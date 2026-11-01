"""Supplier service (OTD) at the day grain (docs/regles_metier.md § 14).

One **line** = one supplier, one article, one expected working day.  *Expected* = the firm order slots of that day
(a slot on a plant closure counts on the next working day), *received* = the validated receipts of that day
(same rule).  Purchase order numbers play no part.

* **QUANTITÉ** : 100 when the expected quantity of the day is received in full within the evaluation horizon (the
  window plus ``otd_band2_days`` working days) and the day does not create an advance beyond the tolerance (one day of
  need from two delivery days a week, two days of need otherwise) ; 0 otherwise (« non livrée », or excess).
  The advance is read on the running balance received − expected, restarted on the first Monday of every month.
* **PONCTUALITÉ** : working days from the expected day until everything due *since that day* has been received
  (what was missing before does not count) ; a receipt before the day, on a day with nothing expected, is an
  advance.  Inside the supplier's window (±1 working day up to two delivery days a week, 0 beyond) : 100 ; then
  the common bands ``otd_band1_days`` / ``otd_band2_days`` and their scores ; beyond : 0 ; never received : 0.
* **DESADV** : share of the delivery notes received over the period announced by a processed DESADV.
* **OTD** of a supplier over a period = ``otd_weight_qty`` × mean QUANTITÉ + ``otd_weight_punctuality`` × mean
  PONCTUALITÉ + ``otd_weight_desadv`` × DESADV, over its lines of the period.  Periods are whole ISO weeks ; a week
  belongs to the month of its Wednesday.  The portfolio figure is the mean of the suppliers.

Everything is computed from the three fact frames on a working-day grid, once per data version, and cached.
"""
from __future__ import annotations

import datetime as dt
import hashlib
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ..engine.calendar import WorkCalendar, iso_week_label
from ..engine.models import EngineParams

EPS = 1e-6
BUCKETS = ("week", "month", "quarter", "year")
STATUSES = ("reçue", "non livrée", "non évaluable", "ouverte")


# =============================================================================
# periods : whole ISO weeks, a week belongs to the month of its Wednesday
# =============================================================================
def week_monday(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def week_anchor(day: dt.date) -> dt.date:
    """The Wednesday of the week : its month is the month of the week."""
    return week_monday(day) + dt.timedelta(days=2)


def bucket_label(day: dt.date, bucket: str) -> str:
    a = week_anchor(day)
    if bucket == "week":
        return iso_week_label(a)
    if bucket == "month":
        return f"{a.year}-{a.month:02d}"
    if bucket == "quarter":
        return f"{a.year}-Q{(a.month - 1) // 3 + 1}"
    return str(a.year)


def bucket_start(label: str) -> dt.date:
    if "-W" in label:
        y, w = label.split("-W")
        return dt.date.fromisocalendar(int(y), int(w), 1)
    if "-Q" in label:
        y, q = label.split("-Q")
        return dt.date(int(y), 3 * (int(q) - 1) + 1, 1)
    if "-" in label:
        y, m = label.split("-")
        return dt.date(int(y), int(m), 1)
    return dt.date(int(label), 1, 1)


def snap_window(date_from: dt.date, date_to: dt.date) -> tuple[dt.date, dt.date]:
    """Whole weeks : Monday of the first week, Sunday of the last."""
    return week_monday(date_from), week_monday(date_to) + dt.timedelta(days=6)


# =============================================================================
# scoring helpers
# =============================================================================
def punctuality_score(delta_days: int, window: int, params: EngineParams) -> float:
    """``delta_days`` in working days (negative = early), ``window`` = tolerated days either side."""
    eff = max(0, abs(int(delta_days)) - max(int(window), 0))
    if eff == 0:
        return 100.0
    if eff <= max(int(params.otd_band1_days), 0):
        return float(params.otd_band1_score)
    if eff <= max(int(params.otd_band2_days), 0):
        return float(params.otd_band2_score)
    return 0.0


def otd_score(q: float | None, p: float | None, d: float | None, params: EngineParams) -> float | None:
    if q is None or p is None:
        return None
    w = (float(params.otd_weight_qty), float(params.otd_weight_punctuality), float(params.otd_weight_desadv))
    total = sum(w) or 100.0
    return round((w[0] * q + w[1] * p + w[2] * (d or 0.0)) / total, 1)


def supplier_window(delivery_weekdays: Iterable[int] | str | None) -> int:
    """±1 working day up to two delivery days a week, 0 beyond."""
    return 1 if _weekday_count(delivery_weekdays) <= 2 else 0


def tolerance_factor(delivery_weekdays: Iterable[int] | str | None) -> int:
    """Days of need tolerated in excess : one from two delivery days a week, two otherwise."""
    return 1 if _weekday_count(delivery_weekdays) >= 2 else 2


def _weekday_count(spec: Iterable[int] | str | None) -> int:
    if spec is None or (isinstance(spec, float) and np.isnan(spec)):
        return 0
    if isinstance(spec, str):
        return len({int(x) for x in spec.replace(";", ",").split(",") if x.strip().isdigit()})
    return len(set(spec))


# =============================================================================
# data
# =============================================================================
@dataclass
class OtdLine:
    supplier_id: str
    article_id: str
    day: dt.date
    expected: float
    received: float                  # received on the day itself
    qty_open: float
    order_count: int
    purch: str
    shifted_from: dt.date | None
    window: int
    tolerance: float
    excess: float
    completed: dt.date | None = None
    delta: int | None = None         # working days, negative = early
    status: str = "ouverte"
    evaluable: bool = False
    qty_score: float | None = None
    punct_score: float | None = None
    receipt_days: int = 0
    bls: list[str] = field(default_factory=list)
    first_seen: dt.date | None = None
    lead_promised: int = 0
    lead_observed: int | None = None
    urgent: bool = False
    order_to_ship: int | None = None
    ship_to_receipt: float | None = None

    @property
    def key(self) -> str:
        return f"{self.supplier_id}|{self.article_id}|{self.day.isoformat()}"

    @property
    def bias(self) -> int | None:
        return None if self.lead_observed is None else self.lead_observed - self.lead_promised


@dataclass
class BlRow:
    supplier_id: str
    bl: str
    day: dt.date
    ok: bool


@dataclass
class OtdData:
    as_of: dt.date
    lines: list[OtdLine]
    bls: list[BlRow]
    unmatched_receipts: int
    first_seen_available: bool
    articles: dict[str, tuple[str, str, str]] = field(default_factory=dict)     # article_id → (designation, unit, planner)
    names: dict[str, str] = field(default_factory=dict)
    windows: dict[str, int] = field(default_factory=dict)

    def size(self) -> int:
        return 4096 + 400 * len(self.lines) + 80 * len(self.bls)


def cache_key(version: int, as_of: dt.date, params: EngineParams, holidays: Iterable[dt.date], open_days: Iterable[dt.date]) -> str:
    payload = (version, as_of.isoformat(), params.working_weekdays, params.otd_weight_qty, params.otd_weight_punctuality, params.otd_weight_desadv,
               params.otd_band1_days, params.otd_band1_score, params.otd_band2_days, params.otd_band2_score,
               sorted(d.isoformat() for d in holidays), sorted(d.isoformat() for d in open_days))
    return "otd:" + hashlib.sha1(repr(payload).encode()).hexdigest()


def _bls(value: Any, receipt_id: str) -> list[str]:
    s = "" if value is None or (isinstance(value, float) and np.isnan(value)) else str(value)
    out = [x.strip() for x in s.split("|") if x.strip()]
    return out or [f"sans BL · {receipt_id}"]


def _s(v: Any) -> str:
    return "" if v is None or (isinstance(v, float) and np.isnan(v)) else str(v)


def _date(v: Any) -> dt.date | None:
    if v is None or (isinstance(v, float) and np.isnan(v)) or v is pd.NaT:
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        return pd.Timestamp(v).date()
    except (ValueError, TypeError):
        return None


def build(orders: pd.DataFrame, receipts: pd.DataFrame, desadv: pd.DataFrame, suppliers: pd.DataFrame, links: pd.DataFrame,
          articles: pd.DataFrame, calendar: WorkCalendar, as_of: dt.date, params: EngineParams) -> OtdData:
    """Evaluate every expected day of every (supplier, article) from the fact frames."""
    # ---- reference : windows, tolerances, lead times, names
    windows: dict[str, int] = {}
    factors: dict[str, int] = {}
    leads: dict[str, int] = {}
    names: dict[str, str] = {}
    for r in suppliers.itertuples(index=False):
        sid = _s(r.supplier_id)
        windows[sid] = supplier_window(_s(getattr(r, "delivery_weekdays", "")))
        factors[sid] = tolerance_factor(_s(getattr(r, "delivery_weekdays", "")))
        leads[sid] = int(getattr(r, "lead_time_days", 5) or 5)
        names[sid] = _s(getattr(r, "name", ""))
    link_lead: dict[tuple[str, str], int] = {}
    if len(links):
        for r in links.itertuples(index=False):
            ov = getattr(r, "lead_time_override", None)
            if ov is not None and not (isinstance(ov, float) and np.isnan(ov)) and str(ov).strip() not in ("", "None"):
                link_lead[(_s(r.article_id), _s(r.supplier_id))] = int(float(ov))
    arts = {_s(r.article_id): (_s(getattr(r, "designation", "")), _s(getattr(r, "unit", "")), _s(getattr(r, "planner", ""))) for r in articles.itertuples(index=False)}

    # ---- working-day grid
    firm = orders[orders["order_type"].astype(str).str.upper() == "FIRM"] if len(orders) else orders
    o_dates = [d for d in (_date(x) for x in firm["expected_date"]) if d is not None] if len(firm) else []
    r_dates = [d for d in (_date(x) for x in receipts["receipt_date"]) if d is not None] if len(receipts) else []
    if not o_dates:
        return OtdData(as_of, [], [], 0, False, arts, names, windows)
    lo = min(o_dates + r_dates) - dt.timedelta(days=14)
    hi = max(o_dates + r_dates + [as_of]) + dt.timedelta(days=21)
    grid: list[dt.date] = []
    d = lo
    while d <= hi:
        if calendar.is_working_day(d):
            grid.append(d)
        d += dt.timedelta(days=1)
    if not grid:
        return OtdData(as_of, [], [], 0, False, arts, names, windows)

    def idx(day: dt.date) -> int:
        return min(bisect_left(grid, day), len(grid) - 1)

    i_as_of = idx(as_of)
    band2 = max(int(params.otd_band2_days), 0)

    # ---- expected per (supplier, article, grid index)
    exp: dict[tuple[str, str], dict[int, list]] = defaultdict(dict)       # [qty, qty_open, n, purch set, first_seen, urgent, shifted_from]
    first_seen_available = False
    for r in firm.itertuples(index=False):
        day = _date(r.expected_date)
        if day is None:
            continue
        sid, aid = _s(r.supplier_id), _s(r.article_id)
        i = idx(day)
        e = exp[(sid, aid)].setdefault(i, [0.0, 0.0, 0, set(), None, False, None])
        e[0] += float(r.qty_ordered or 0.0)
        e[1] += float(r.qty_open if r.qty_open is not None and not (isinstance(r.qty_open, float) and np.isnan(r.qty_open)) else r.qty_ordered or 0.0)
        e[2] += 1
        po = _s(getattr(r, "purch_id", ""))
        if po:
            e[3].add(po)
        fs = _date(getattr(r, "first_seen", None))
        if fs is not None:
            first_seen_available = True
            e[4] = fs if e[4] is None else min(e[4], fs)
            if i - idx(fs) < link_lead.get((aid, sid), leads.get(sid, 5)):
                e[5] = True
        if grid[i] != day:
            e[6] = day

    # ---- receipts per (supplier, article, grid index) ; delivery notes per supplier
    processed = set()
    issue: dict[tuple[str, str], dt.date] = {}
    if len(desadv):
        for r in desadv.itertuples(index=False):
            bl = _s(r.packing_slip).strip()
            if not bl:
                continue
            key = (_s(r.supplier_id), bl)
            if _s(r.state).strip().lower() == "traité" and _s(r.final_processing).strip().upper() == "OK":
                processed.add(key)
            if _s(r.state).strip().lower() != "annulé":
                d0 = _date(r.issue_date)
                if d0 is not None:
                    issue[key] = d0 if key not in issue else min(issue[key], d0)
    rec: dict[tuple[str, str], dict[int, list]] = defaultdict(dict)       # [qty, bls list, n]
    bl_rows: list[BlRow] = []
    seen_bl: set[tuple[str, str]] = set()
    unmatched = 0
    if len(receipts):
        for r in receipts.itertuples(index=False):
            status = _s(r.status).strip().lower()
            qty = float(r.qty or 0.0)
            day = _date(r.receipt_date)
            if "transit" in status or qty == 0.0 or day is None:
                continue
            sid, aid = _s(r.supplier_id), _s(r.article_id)
            bls = _bls(getattr(r, "packing_slip", ""), _s(r.receipt_id))
            for bl in bls:
                if (sid, bl) not in seen_bl:
                    seen_bl.add((sid, bl))
                    bl_rows.append(BlRow(sid, bl, day, (sid, bl) in processed))
            if (sid, aid) not in exp:
                unmatched += 1
                continue
            i = idx(day)
            cell = rec[(sid, aid)].setdefault(i, [0.0, [], 0])
            cell[0] += qty
            cell[1].extend(bls)
            cell[2] += 1

    # ---- evaluation per pair
    lines: list[OtdLine] = []
    for (sid, aid), days in exp.items():
        rd = rec.get((sid, aid), {})
        window = windows.get(sid, 1)
        factor = factors.get(sid, 2)
        h = window + band2
        lo_i = min(min(days), min(rd) if rd else min(days))
        hi_i = max(max(days), max(rd) if rd else max(days))
        n = hi_i - lo_i + 1
        E = np.zeros(n)
        R = np.zeros(n)
        for i, e in days.items():
            E[i - lo_i] = e[0]
        for i, c in rd.items():
            R[i - lo_i] = c[0]
        D = np.cumsum(R - E)
        avail = np.maximum(R - E, 0.0)          # surplus of each day still available as an advance for a later line
        # advance created per day, balance restarted on the first Monday of each month
        excess = np.zeros(n)
        adv = prev_pos = 0.0
        cur_month = None
        for k in range(n):
            a = week_anchor(grid[lo_i + k])
            m = (a.year, a.month)
            if m != cur_month:
                cur_month, adv, prev_pos = m, 0.0, 0.0
            adv += R[k] - E[k]
            pos = max(adv, 0.0)
            excess[k] = max(0.0, pos - prev_pos)
            prev_pos = pos
        # need per working day : expected so far ÷ working days spanned
        past = [i for i in days if i <= i_as_of]
        need = sum(days[i][0] for i in past) / max(1, (max(past) - min(past) + 1)) if past else 0.0
        tol = factor * need
        for i in sorted(days):
            e = days[i]
            k = i - lo_i
            r_day, e_qty = float(R[k]), float(E[k])
            line = OtdLine(sid, aid, grid[i], e_qty, r_day, float(e[1]), int(e[2]), " | ".join(sorted(e[3])), e[6], window, round(tol, 3), round(float(excess[k]), 3),
                           first_seen=e[4], lead_promised=link_lead.get((aid, sid), leads.get(sid, 5)), urgent=bool(e[5]))
            j: int | None = None
            carry_first: int | None = None
            if r_day >= e_qty - EPS:
                j = k
            else:
                carry = 0.0
                used: list[tuple[int, float]] = []
                for kk in range(max(0, k - h), k):
                    s = float(avail[kk])
                    if s > EPS:
                        take = min(s, e_qty - carry)
                        carry += take
                        used.append((kk, take))
                        carry_first = kk if carry_first is None else carry_first
                        if carry >= e_qty - EPS:
                            j = kk
                            break
                if j is None and r_day > EPS and carry + r_day >= e_qty - EPS:
                    j = k
                for kk, take in used:
                    avail[kk] -= take
                if j is None:
                    target = (D[k - 1] if k > 0 else 0.0) - carry
                    for jj in range(k + 1, min(n, k + 1 + h)):
                        if lo_i + jj > i_as_of:
                            break
                        if D[jj] >= target - EPS:
                            j = jj
                            break
            if j is not None:
                line.completed = grid[lo_i + j]
                line.delta = j - k
                line.status, line.evaluable = "reçue", True
            elif i + h < i_as_of:
                near = any(R[kk] > EPS for kk in range(max(0, k - h), min(n, k + h + 1)))
                if r_day <= EPS and not near and float(e[1]) < e_qty - EPS:
                    line.status = "non évaluable"
                else:
                    line.status, line.evaluable = "non livrée", True
            else:
                line.status = "ouverte"
            if line.evaluable:
                line.qty_score = 100.0 if j is not None and excess[k] <= tol + EPS else 0.0     # in full within the evaluation horizon
                line.punct_score = punctuality_score(line.delta, window, params) if line.delta is not None else 0.0
            lo_k = min(k, carry_first) if carry_first is not None else k
            hi_k = max(k, j) if j is not None else k
            bls: list[str] = []
            for kk in range(lo_k, hi_k + 1):
                c = rd.get(lo_i + kk)
                if c:
                    line.receipt_days += 1
                    bls.extend(c[1])
            line.bls = sorted(set(bls))
            if line.first_seen is not None and line.completed is not None:
                line.lead_observed = i_of(grid, line.completed) - idx(line.first_seen)
            issues = [issue[(sid, bl)] for bl in line.bls if (sid, bl) in issue]
            if issues:
                if line.first_seen is not None:
                    line.order_to_ship = (min(issues) - line.first_seen).days
                gaps = [(grid[lo_i + kk] - issue[(sid, bl)]).days for kk in range(lo_k, hi_k + 1) for bl in (rd.get(lo_i + kk) or [0, []])[1] if (sid, bl) in issue]
                if gaps:
                    line.ship_to_receipt = round(sum(gaps) / len(gaps), 1)
            lines.append(line)
    lines.sort(key=lambda x: (x.day, x.supplier_id, x.article_id))
    return OtdData(as_of, lines, bl_rows, unmatched, first_seen_available, arts, names, windows)


def i_of(grid: list[dt.date], day: dt.date) -> int:
    return min(bisect_left(grid, day), len(grid) - 1)


# =============================================================================
# reports
# =============================================================================
def _mean(xs: list[float | None]) -> float | None:
    v = [x for x in xs if x is not None]
    return round(sum(v) / len(v), 1) if v else None


def _group(rows: list[OtdLine], bls: list[BlRow], params: EngineParams) -> dict[str, Any]:
    """Scores of one group of lines (one supplier over one period) : the DESADV share is read on the delivery notes
    received over the same period."""
    q = _mean([x.qty_score for x in rows])
    p = _mean([x.punct_score for x in rows])
    d = round(100.0 * sum(1 for b in bls if b.ok) / len(bls), 1) if bls else (0.0 if rows else None)
    return {"n": len(rows), "otd": otd_score(q, p, d, params), "qty": q, "punctuality": p, "desadv": d,
            "not_delivered": sum(1 for x in rows if x.status == "non livrée"), "bls": len(bls)}


def _portfolio(groups: list[dict[str, Any]]) -> dict[str, Any]:
    """Mean of the suppliers (each counts once)."""
    gs = [g for g in groups if g["n"]]
    return {"n": sum(g["n"] for g in gs), "otd": _mean([g["otd"] for g in gs]), "qty": _mean([g["qty"] for g in gs]),
            "punctuality": _mean([g["punctuality"] for g in gs]), "desadv": _mean([g["desadv"] for g in gs]),
            "not_delivered": sum(g["not_delivered"] for g in gs), "bls": sum(g["bls"] for g in gs), "suppliers": len(gs)}


def _select(data: OtdData, date_from: dt.date, date_to: dt.date, supplier_ids: list[str] | None, article_ids: list[str] | None,
            planner: str | None) -> tuple[list[OtdLine], list[BlRow]]:
    sup = set(supplier_ids or [])
    art = set(article_ids or [])
    lines = [x for x in data.lines if date_from <= x.day <= date_to and (not sup or x.supplier_id in sup) and (not art or x.article_id in art)
             and (not planner or data.articles.get(x.article_id, ("", "", ""))[2] == planner)]
    if art or planner:        # the DESADV share stays a supplier-level reading : keep the notes of the suppliers present
        present = {x.supplier_id for x in lines}
        bls = [b for b in data.bls if date_from <= b.day <= date_to and b.supplier_id in present]
    else:
        bls = [b for b in data.bls if date_from <= b.day <= date_to and (not sup or b.supplier_id in sup)]
    return lines, bls


def line_dict(x: OtdLine, data: OtdData) -> dict[str, Any]:
    a = data.articles.get(x.article_id, ("", "", ""))
    return {"id": x.key, "date": x.day.isoformat(), "shifted_from": x.shifted_from.isoformat() if x.shifted_from else None,
            "supplier_id": x.supplier_id, "supplier_name": data.names.get(x.supplier_id, ""), "article_id": x.article_id, "designation": a[0], "unit": a[1], "planner": a[2],
            "purch": x.purch, "order_count": x.order_count, "expected": x.expected, "received": round(x.received, 3), "excess": x.excess, "tolerance": x.tolerance,
            "completed": x.completed.isoformat() if x.completed else None, "delta": x.delta, "window": x.window, "status": x.status,
            "qty_score": x.qty_score, "punct_score": x.punct_score, "receipt_days": x.receipt_days, "bls": x.bls,
            "first_seen": x.first_seen.isoformat() if x.first_seen else None, "lead_promised": x.lead_promised, "lead_observed": x.lead_observed,
            "bias": x.bias, "urgent": x.urgent}


def otd_report(data: OtdData, params: EngineParams, bucket: str = "month", date_from: dt.date | None = None, date_to: dt.date | None = None,
               supplier_ids: list[str] | None = None, article_ids: list[str] | None = None, planner: str | None = None, max_lines: int = 5000) -> dict[str, Any]:
    bucket = bucket if bucket in BUCKETS else "month"
    date_to = date_to or (data.as_of - dt.timedelta(days=1))
    date_from = date_from or (date_to - dt.timedelta(days=365))
    date_from, date_to = snap_window(date_from, date_to)
    win, bls = _select(data, date_from, date_to, supplier_ids, article_ids, planner)
    rows = [x for x in win if x.evaluable]
    labels = sorted({bucket_label(x.day, bucket) for x in rows}, key=bucket_start)
    by_sup: dict[str, list[OtdLine]] = defaultdict(list)
    for x in rows:
        by_sup[x.supplier_id].append(x)
    bl_sup: dict[str, list[BlRow]] = defaultdict(list)
    for b in bls:
        bl_sup[b.supplier_id].append(b)
    suppliers = []
    for sid, ss in by_sup.items():
        sb = bl_sup.get(sid, [])
        series = [{"period": lab, "start": bucket_start(lab).isoformat(),
                   **_group([x for x in ss if bucket_label(x.day, bucket) == lab], [b for b in sb if bucket_label(b.day, bucket) == lab], params)} for lab in labels]
        suppliers.append({"supplier_id": sid, "name": data.names.get(sid, ""), **_group(ss, sb, params), "window": data.windows.get(sid, 1),
                          "articles": len({x.article_id for x in ss}), "series": series})
    suppliers.sort(key=lambda r: ((r["otd"] if r["otd"] is not None else 101), -r["n"]))
    totals = {**_portfolio(suppliers),
              "series": [{"period": lab, "start": bucket_start(lab).isoformat(), **_portfolio([s["series"][i] for s in suppliers])} for i, lab in enumerate(labels)]}
    detail = sorted(win, key=lambda x: (x.day, x.supplier_id, x.article_id), reverse=True)
    return {"as_of": data.as_of.isoformat(), "from": date_from.isoformat(), "to": date_to.isoformat(), "bucket": bucket, "periods": labels,
            "params": {"weights": {"qty": params.otd_weight_qty, "punctuality": params.otd_weight_punctuality, "desadv": params.otd_weight_desadv},
                       "band1_days": params.otd_band1_days, "band1_score": params.otd_band1_score, "band2_days": params.otd_band2_days, "band2_score": params.otd_band2_score},
            "totals": totals, "suppliers": suppliers,
            "not_evaluable": sum(1 for x in win if x.status == "non évaluable"), "open": sum(1 for x in win if x.status == "ouverte"),
            "unmatched_receipts": data.unmatched_receipts, "first_seen_available": data.first_seen_available,
            "lines": [line_dict(x, data) for x in detail[:max_lines]], "truncated": len(detail) > max_lines}


def _weekday_profile(days: list[dt.date]) -> list[float]:
    n = len(days)
    counts = [0] * 7
    for d in days:
        counts[d.isoweekday() - 1] += 1
    return [round(100.0 * c / n, 1) if n else 0.0 for c in counts]


def _lead_row(ss: list[OtdLine], params: EngineParams) -> dict[str, Any]:
    done = [x for x in ss if x.completed is not None]
    deltas = [x.delta for x in done if x.delta is not None]
    nd = len(deltas)
    promised = sorted({x.lead_promised for x in ss})
    return {"n": len(ss), "evaluated": sum(1 for x in ss if x.evaluable), "received": len(done), "not_delivered": sum(1 for x in ss if x.status == "non livrée"),
            "delay_mean": _mean(deltas),
            "on_time_pct": round(100.0 * sum(1 for d in deltas if d == 0) / nd, 1) if nd else None,
            "early_pct": round(100.0 * sum(1 for d in deltas if d < 0) / nd, 1) if nd else None,
            "late_pct": round(100.0 * sum(1 for d in deltas if d > 0) / nd, 1) if nd else None,
            "late_beyond_pct": round(100.0 * sum(1 for x in done if x.delta is not None and x.delta - x.window > params.otd_band2_days) / nd, 1) if nd else None,
            "recurring_late": nd >= 3 and sum(1 for d in deltas if d > 0) / nd >= 0.5,
            "lead_promised": promised[0] if len(promised) == 1 else (f"{promised[0]}–{promised[-1]}" if promised else None),
            "lead_observed_mean": _mean([x.lead_observed for x in done]),
            "bias_mean": _mean([x.bias for x in done]),
            "order_to_ship_mean": _mean([x.order_to_ship for x in ss]),
            "ship_to_receipt_mean": _mean([x.ship_to_receipt for x in ss]),
            "urgent_pct": round(100.0 * sum(1 for x in ss if x.urgent) / len(ss), 1) if ss and any(x.first_seen for x in ss) else None,
            "partial_pct": round(100.0 * sum(1 for x in done if x.receipt_days > 1) / len(done), 1) if done else None,
            "qty_per_receipt_mean": round(sum(x.received for x in ss) / max(1, sum(1 for x in ss if x.received > EPS)), 1) if any(x.received > EPS for x in ss) else None,
            "profile_expected": _weekday_profile([x.day for x in ss]),
            "profile_receipts": _weekday_profile([x.completed for x in done if x.completed is not None]),
            "profile_orders": _weekday_profile([x.first_seen for x in ss if x.first_seen is not None])}


def lead_time_report(data: OtdData, params: EngineParams, date_from: dt.date | None = None, date_to: dt.date | None = None,
                     supplier_ids: list[str] | None = None, article_ids: list[str] | None = None, planner: str | None = None) -> dict[str, Any]:
    """Observed delays per supplier (per article when one supplier or one article is chosen) : mean delay in working
    days, shares on time / early / late, recurrence, observed lead time and bias, urgency, weekday profiles."""
    date_to = date_to or (data.as_of - dt.timedelta(days=1))
    date_from = date_from or (date_to - dt.timedelta(days=365))
    date_from, date_to = snap_window(date_from, date_to)
    lines, _bls = _select(data, date_from, date_to, supplier_ids, article_ids, planner)
    by_sup: dict[str, list[OtdLine]] = defaultdict(list)
    for x in lines:
        by_sup[x.supplier_id].append(x)
    suppliers = [{"supplier_id": sid, "name": data.names.get(sid, ""), **_lead_row(ss, params)} for sid, ss in sorted(by_sup.items())]
    suppliers.sort(key=lambda r: (-(r["delay_mean"] or 0), -r["n"]))
    articles: list[dict[str, Any]] = []
    if (supplier_ids and len(supplier_ids) == 1) or article_ids:
        by_art: dict[tuple[str, str], list[OtdLine]] = defaultdict(list)
        for x in lines:
            by_art[(x.supplier_id, x.article_id)].append(x)
        for (sid, aid), ss in sorted(by_art.items()):
            a = data.articles.get(aid, ("", "", ""))
            articles.append({"supplier_id": sid, "supplier_name": data.names.get(sid, ""), "article_id": aid, "designation": a[0], "unit": a[1], **_lead_row(ss, params)})
    return {"as_of": data.as_of.isoformat(), "from": date_from.isoformat(), "to": date_to.isoformat(), "totals": _lead_row(lines, params),
            "suppliers": suppliers, "articles": articles, "first_seen_available": data.first_seen_available, "band2_days": params.otd_band2_days}
