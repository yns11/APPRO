"""Supplier performance measured on the ERP facts : service (OTD), observed lead times and delivery profiles,
and the ABC / XYZ segmentation of the articles (docs/regles_metier.md § 14).

Two matchings of the receipts with the firm orders exist (``EngineParams.otd_matching``) :

* **jour** (default, docs/otd_maille_jour.md) : the grain is *one supplier, one article, one day*.  The expected
  quantity of a day is the sum of the firm slots of that day, the received quantity the sum of the validated receipts
  of that day (a day falling on a plant closure is moved to the next working day).  QUANTITÉ is 100 only when the
  receipts of the day cover the expected quantity of the day.  PONCTUALITÉ counts the working days from J until the
  supplier has delivered everything due *since J* (what was missing before J does not count) ; a receipt before J on
  a day with nothing expected is an advance.  Purchase order numbers and sub-lines play no part.
* **cumul** (previous rule, kept for comparison) : the receipts of one (supplier, article, purchase order) are
  allocated to the slots of that purchase order in date order, first slot served first ; a slot is completed on the
  day the ordered quantity is reached, QUANTITÉ is 100 when it is reached one day or another, the delta is in
  calendar days.  Any missing quantity rolls over every later slot (the « 2 réceptions, +6 j » cascade).

Common scoring : PONCTUALITÉ 100 on the day, early or late by 1 … ``otd_band1_days`` days → ``otd_band1_score``, up
to ``otd_band2_days`` → ``otd_band2_score``, beyond 0 ; DESADV 100 when every delivery note received was announced by
a processed DESADV without error ; OTD = Σ weight × indicator.  A day (or slot) is **evaluable** once completed, or
``otd_band2_days`` days after its date without completion (« non livrée », every indicator 0).  A day the ERP shows
received without any receipt line in the extraction is **not evaluable** (data gap, counted apart).  The OTD of a
supplier over a period is the mean of its days (slots) expected in the period.  The **balance** of a period
(Σ expected − Σ received, in pieces and in days of delivery) is reported beside the OTD : it is what the day grain
does not score (an old backlog, a systematic one-day shift).
"""
from __future__ import annotations

import datetime as dt
import math
import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterable

from ..engine.calendar import WorkCalendar, iso_week_label, iso_week_monday
from ..engine.models import Dataset, EngineParams, OrderLine, Receipt

EPS = 1e-6
BUCKETS = ("week", "month", "quarter", "year")


def _po_keys(ref: str | None) -> list[str]:
    return [x.strip() for x in re.split(r"[|;,/\s]+", ref or "") if x.strip()]


def bucket_label(day: dt.date, bucket: str) -> str:
    if bucket == "week":
        return iso_week_label(day)
    if bucket == "month":
        return f"{day.year}-{day.month:02d}"
    if bucket == "quarter":
        return f"{day.year}-Q{(day.month - 1) // 3 + 1}"
    return str(day.year)


def bucket_start(label: str) -> dt.date:
    """First day of a bucket label (sorting, charts)."""
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


@dataclass
class SlotEval:
    """One firm order slot with the receipts allocated to it and its service scores."""

    order_id: str
    supplier_id: str | None
    article_id: str
    purch_id: str
    expected_date: dt.date
    qty_ordered: float
    qty_open: float
    first_seen: dt.date | None = None
    lead_promised: int = 0
    received_qty: float = 0.0
    receipts: list[tuple[dt.date, float, list[str]]] = field(default_factory=list)   # (date, qty, delivery notes)
    completed_date: dt.date | None = None
    status: str = "ouverte"                 # ouverte | reçue | non livrée | non évaluable
    evaluable: bool = False
    qty_score: float | None = None
    punct_score: float | None = None
    desadv_score: float | None = None
    otd: float | None = None
    delta_days: int | None = None           # completion − expected, calendar days (negative = early)
    lead_observed_wd: int | None = None     # first appearance in the ERP → completion, working days
    order_to_ship_days: int | None = None   # first appearance → first DESADV issue, calendar days
    ship_to_receipt_days: float | None = None   # DESADV issue → receipt, calendar days (mean over the BL)
    urgent_order: bool = False              # placed inside the lead time
    bls: list[str] = field(default_factory=list)
    desadv_missing: list[str] = field(default_factory=list)
    method: str = "cumul"                   # cumul (one firm slot) | jour (one expected day)
    slot_count: int = 1                     # jour : firm slots merged in the day
    order_ids: list[str] = field(default_factory=list)
    received_on_day: float | None = None    # jour : quantity received on the expected day itself
    shifted_from: dt.date | None = None     # jour : the ERP date when it fell on a plant closure

    @property
    def bias_wd(self) -> int | None:
        return None if self.lead_observed_wd is None else self.lead_observed_wd - self.lead_promised

    def to_dict(self, names: dict[str, str], arts: dict[str, Any]) -> dict[str, Any]:
        a = arts.get(self.article_id)
        return {"order_id": self.order_id, "supplier_id": self.supplier_id, "supplier_name": names.get(self.supplier_id or "", ""),
                "article_id": self.article_id, "designation": a.designation if a else "", "unit": a.unit if a else "", "planner": a.planner if a else "",
                "purch_id": self.purch_id, "expected_date": self.expected_date.isoformat(), "qty_ordered": self.qty_ordered, "qty_open": self.qty_open,
                "received_qty": round(self.received_qty, 3), "receipt_count": len(self.receipts),
                "first_receipt": self.receipts[0][0].isoformat() if self.receipts else None,
                "completed_date": self.completed_date.isoformat() if self.completed_date else None, "status": self.status,
                "evaluable": self.evaluable, "qty_score": self.qty_score, "punct_score": self.punct_score, "desadv_score": self.desadv_score,
                "otd": None if self.otd is None else round(self.otd, 1), "delta_days": self.delta_days, "first_seen": self.first_seen.isoformat() if self.first_seen else None,
                "lead_promised": self.lead_promised, "lead_observed_wd": self.lead_observed_wd, "bias_wd": self.bias_wd,
                "order_to_ship_days": self.order_to_ship_days, "ship_to_receipt_days": self.ship_to_receipt_days, "urgent_order": self.urgent_order,
                "bls": self.bls, "desadv_missing": self.desadv_missing, "method": self.method, "slot_count": self.slot_count,
                "order_ids": self.order_ids or [self.order_id], "received_on_day": None if self.received_on_day is None else round(self.received_on_day, 3),
                "shifted_from": self.shifted_from.isoformat() if self.shifted_from else None}


def punctuality_score(delta_days: int, params: EngineParams) -> float:
    d = abs(int(delta_days))
    if d == 0:
        return 100.0
    if d <= max(int(params.otd_band1_days), 0):
        return float(params.otd_band1_score)
    if d <= max(int(params.otd_band2_days), 0):
        return float(params.otd_band2_score)
    return 0.0


def otd_score(q: float, p: float, d: float, params: EngineParams) -> float:
    w = (float(params.otd_weight_qty), float(params.otd_weight_punctuality), float(params.otd_weight_desadv))
    total = sum(w) or 100.0
    return (w[0] * q + w[1] * p + w[2] * d) / total


@dataclass
class DayExpected:
    """The firm slots of one (supplier, article) merged on one working day."""

    qty: float = 0.0
    qty_open: float = 0.0
    order_ids: list[str] = field(default_factory=list)
    purch: list[str] = field(default_factory=list)
    first_seen: dt.date | None = None
    lead_promised: int = 0
    urgent: bool = False
    shifted_from: dt.date | None = None


@dataclass
class DayReceived:
    qty: float = 0.0
    lines: list[tuple[dt.date, float, list[str]]] = field(default_factory=list)   # (date, qty, delivery notes)


DayKey = tuple[str | None, str]


@dataclass
class Matching:
    slots: list[SlotEval]
    unmatched_receipts: int = 0        # receipt lines without any firm slot to compare with (purchase order in cumul mode, article in jour mode)
    method: str = "cumul"
    expected: dict[DayKey, dict[dt.date, DayExpected]] = field(default_factory=dict)
    received: dict[DayKey, dict[dt.date, DayReceived]] = field(default_factory=dict)

    @property
    def first_seen_available(self) -> bool:
        return any(s.first_seen is not None for s in self.slots)


def _working_day(calendar: WorkCalendar, day: dt.date) -> dt.date:
    """The day itself when the plant is open, else the next working day (a slot or a receipt on a closure)."""
    if calendar.is_working_day(day):
        return day
    try:
        return calendar.next_working_day(day)
    except ValueError:
        return day


def aggregate_days(ds: Dataset, calendar: WorkCalendar, orders: Iterable[OrderLine] | None = None,
                   receipts: Iterable[Receipt] | None = None) -> tuple[dict[DayKey, dict[dt.date, DayExpected]], dict[DayKey, dict[dt.date, DayReceived]], int]:
    """Day grain per (supplier, article) : the firm slots merged per working day, the validated receipts merged per
    working day (purchase order numbers ignored).  Receipts of an article the supplier has no firm slot for are
    counted apart (third value)."""
    suppliers = {s.supplier_id: s for s in ds.suppliers}
    links = {(l.article_id, l.supplier_id): l for l in ds.links}
    expected: dict[DayKey, dict[dt.date, DayExpected]] = defaultdict(dict)
    for o in (orders if orders is not None else ds.orders):
        if o.order_type.value != "FIRM":
            continue
        day = _working_day(calendar, o.expected_date)
        e = expected[(o.supplier_id, o.article_id)].setdefault(day, DayExpected())
        if day != o.expected_date:
            e.shifted_from = o.expected_date
        e.qty += float(o.qty_ordered)
        e.qty_open += float(o.qty_open or 0.0)
        e.order_ids.append(o.order_id)
        for po in _po_keys(o.ref):
            if po not in e.purch:
                e.purch.append(po)
        link = links.get((o.article_id, o.supplier_id or ""))
        lead = int(link.lead_time(suppliers) if link else (suppliers[o.supplier_id].lead_time_days if o.supplier_id in suppliers else 5))
        e.lead_promised = max(e.lead_promised, lead)
        if o.first_seen is not None:
            e.first_seen = o.first_seen if e.first_seen is None else min(e.first_seen, o.first_seen)
            if calendar.working_days_between(o.first_seen, o.expected_date) < lead:
                e.urgent = True
    received: dict[DayKey, dict[dt.date, DayReceived]] = defaultdict(dict)
    unmatched = 0
    for r in (receipts if receipts is not None else ds.receipts):
        if r.in_transit or not r.qty:
            continue
        key = (r.supplier_id, r.article_id)
        if key not in expected:
            unmatched += 1
            continue
        day = _working_day(calendar, r.receipt_date)
        d = received[key].setdefault(day, DayReceived())
        d.qty += float(r.qty)
        d.lines.append((r.receipt_date, float(r.qty), r.packing_slips))
    return dict(expected), dict(received), unmatched


def _desadv_index(ds: Dataset) -> tuple[set[tuple[str | None, str]], dict[tuple[str | None, str], dt.date]]:
    """(supplier, BL) processed without error, and the first issue date of each BL."""
    ok = {(d.supplier_id, d.packing_slip.strip()) for d in ds.desadv if d.processed and d.packing_slip.strip()}
    issue: dict[tuple[str | None, str], dt.date] = {}
    for d in ds.desadv:
        bl = d.packing_slip.strip()
        if bl and d.state.strip().lower() != "annulé":
            cur = issue.get((d.supplier_id, bl))
            issue[(d.supplier_id, bl)] = d.issue_date if cur is None else min(cur, d.issue_date)
    return ok, issue


def _finish(se: SlotEval, calendar: WorkCalendar, desadv_ok: set, desadv_issue: dict, params: EngineParams) -> None:
    """DESADV indicator, OTD, observed lead times and shipping delays of one evaluated slot / day."""
    se.bls = sorted({bl for _d, _q, bls in se.receipts for bl in bls})
    if se.evaluable:
        if se.bls:
            se.desadv_missing = [bl for bl in se.bls if (se.supplier_id, bl) not in desadv_ok]
            se.desadv_score = 100.0 if not se.desadv_missing else 0.0
        else:
            se.desadv_score = 0.0
        se.otd = otd_score(se.qty_score or 0.0, se.punct_score or 0.0, se.desadv_score, params)
    if se.first_seen is not None and se.completed_date is not None:
        se.lead_observed_wd = calendar.working_days_between(se.first_seen, se.completed_date)
    issues = [desadv_issue[(se.supplier_id, bl)] for bl in se.bls if (se.supplier_id, bl) in desadv_issue]
    if issues and se.first_seen is not None:
        se.order_to_ship_days = (min(issues) - se.first_seen).days
    if issues:
        gaps = []
        for day, _q, bls in se.receipts:
            for bl in bls:
                iss = desadv_issue.get((se.supplier_id, bl))
                if iss is not None:
                    gaps.append((day - iss).days)
        if gaps:
            se.ship_to_receipt_days = round(sum(gaps) / len(gaps), 1)


def match_slots(ds: Dataset, as_of: dt.date, calendar: WorkCalendar, params: EngineParams,
                orders: Iterable[OrderLine] | None = None, receipts: Iterable[Receipt] | None = None) -> Matching:
    """**cumul** : allocate the validated receipts to the firm order slots (per supplier, article and purchase order,
    first in first out) and score every slot expected before today.  Receipts of a purchase order without any slot,
    or slots the ERP shows received without a receipt line, are left aside (``status`` tells)."""
    suppliers = {s.supplier_id: s for s in ds.suppliers}
    links = {(l.article_id, l.supplier_id): l for l in ds.links}
    firm = [o for o in (orders if orders is not None else ds.orders) if o.order_type.value == "FIRM"]
    slots: dict[str, SlotEval] = {}
    by_key: dict[tuple[str | None, str, str], list[SlotEval]] = defaultdict(list)
    for o in sorted(firm, key=lambda o: (o.expected_date, o.order_id)):
        link = links.get((o.article_id, o.supplier_id or ""))
        lead = link.lead_time(suppliers) if link else (suppliers[o.supplier_id].lead_time_days if o.supplier_id in suppliers else 5)
        se = SlotEval(o.order_id, o.supplier_id, o.article_id, o.ref or "", o.expected_date, float(o.qty_ordered), float(o.qty_open or 0.0),
                      first_seen=o.first_seen, lead_promised=int(lead))
        slots[o.order_id] = se
        for po in _po_keys(o.ref) or [""]:
            by_key[(o.supplier_id, o.article_id, po)].append(se)
    # receipts : validated arrival journals only, grouped by purchase order
    groups: dict[tuple[str | None, str, str], list[Receipt]] = defaultdict(list)
    for r in (receipts if receipts is not None else ds.receipts):
        if r.in_transit or not r.qty:
            continue
        groups[(r.supplier_id, r.article_id, (r.ref or "").strip())].append(r)
    unmatched_receipts = 0
    for key, rs in groups.items():
        cands = by_key.get(key)
        if not cands:
            unmatched_receipts += len(rs)
            continue
        cands = sorted(cands, key=lambda s: (s.expected_date, s.order_id))
        for r in sorted(rs, key=lambda r: (r.receipt_date, r.receipt_id)):
            left = float(r.qty)
            for se in cands:
                need = se.qty_ordered - se.received_qty
                if need <= EPS:
                    continue
                take = min(left, need)
                se.receipts.append((r.receipt_date, take, r.packing_slips))
                se.received_qty += take
                left -= take
                if se.received_qty >= se.qty_ordered - EPS and se.completed_date is None:
                    se.completed_date = r.receipt_date
                if left <= EPS:
                    break
            if left > EPS:   # over-delivery : the last slot carries it
                cands[-1].receipts.append((r.receipt_date, left, r.packing_slips))
                cands[-1].received_qty += left
    desadv_ok, desadv_issue = _desadv_index(ds)
    grace = dt.timedelta(days=max(int(params.otd_band2_days), 0))
    for se in slots.values():
        if se.completed_date is not None:
            se.status = "reçue"
            se.evaluable = True
        elif se.expected_date + grace < as_of:
            if se.received_qty <= EPS and se.qty_open < se.qty_ordered - EPS:
                se.status = "non évaluable"       # the ERP received it, the extraction has no receipt line
            else:
                se.status = "non livrée"
                se.evaluable = True
        else:
            se.status = "ouverte" if se.expected_date >= as_of or se.qty_open > EPS else "non évaluable"
        if se.evaluable:
            se.qty_score = 100.0 if se.received_qty >= se.qty_ordered - EPS else 0.0
            if se.completed_date is not None:
                se.delta_days = (se.completed_date - se.expected_date).days
                se.punct_score = punctuality_score(se.delta_days, params)
            else:
                se.punct_score = 0.0
        if se.first_seen is not None:
            se.urgent_order = calendar.working_days_between(se.first_seen, se.expected_date) < se.lead_promised
        _finish(se, calendar, desadv_ok, desadv_issue, params)
    expected, received, _unmatched = aggregate_days(ds, calendar, orders, receipts)
    return Matching(sorted(slots.values(), key=lambda s: (s.expected_date, s.supplier_id or "", s.article_id)), unmatched_receipts, "cumul", expected, received)


def match_days(ds: Dataset, as_of: dt.date, calendar: WorkCalendar, params: EngineParams,
               orders: Iterable[OrderLine] | None = None, receipts: Iterable[Receipt] | None = None) -> Matching:
    """**jour** : one expected delivery per (supplier, article, working day).  QUANTITÉ = the receipts of the day cover
    the expected quantity of the day.  Completion of a day J = the first day R ≥ J such that the receipts from J to R
    (plus the surplus received in the ``otd_band2_days`` days before J : advance) cover everything expected from J to
    R, searched up to ``otd_backlog_max_days`` after J ; the delta is in working days of the plant calendar."""
    expected, received, unmatched = aggregate_days(ds, calendar, orders, receipts)
    desadv_ok, desadv_issue = _desadv_index(ds)
    grace = dt.timedelta(days=max(int(params.otd_band2_days), 0))
    horizon = dt.timedelta(days=max(int(params.otd_backlog_max_days), int(params.otd_band2_days), 0))
    out: list[SlotEval] = []
    for key, days in expected.items():
        sid, aid = key
        rd = received.get(key, {})
        rdates = sorted(rd)
        timeline = sorted(set(rdates) | set(days))
        for day, e in sorted(days.items()):
            r_day = rd[day].qty if day in rd else 0.0
            se = SlotEval(f"{sid or ''}|{aid}|{day.isoformat()}", sid, aid, " | ".join(e.purch), day, e.qty, e.qty_open,
                          first_seen=e.first_seen, lead_promised=e.lead_promised, method="jour", slot_count=len(e.order_ids),
                          order_ids=list(e.order_ids), received_on_day=r_day, shifted_from=e.shifted_from)
            se.urgent_order = e.urgent
            completed: dt.date | None = None
            carry, carry_days = 0.0, []
            if r_day >= e.qty - EPS:
                completed = day
            else:
                # advance : surplus of the receipt days just before (nothing expected that day, or more than expected)
                for rday in rdates:
                    if rday >= day:
                        break
                    if rday < day - grace:
                        continue
                    surplus = rd[rday].qty - (days[rday].qty if rday in days else 0.0)
                    if surplus > EPS:
                        carry += surplus
                        carry_days.append(rday)
                        if carry >= e.qty - EPS:
                            completed = rday
                            break
                if completed is None and r_day > EPS and carry + r_day >= e.qty - EPS:
                    completed = day
            if completed is None:
                cum_r, cum_e = carry + r_day, e.qty
                for rday in timeline:
                    if rday <= day:
                        continue
                    if rday > day + horizon or rday > as_of:
                        break
                    cum_r += rd[rday].qty if rday in rd else 0.0
                    cum_e += days[rday].qty if rday in days else 0.0
                    if cum_r >= cum_e - EPS:
                        completed = rday
                        break
            lo = min([day] + carry_days) if carry > EPS else day
            hi = max(day, completed) if completed is not None else day
            se.receipts = [line for rday in rdates if lo <= rday <= hi for line in rd[rday].lines]
            se.completed_date = completed
            se.received_qty = e.qty if completed is not None else min(e.qty, r_day + carry)
            if completed is not None:
                se.status = "reçue"
                se.evaluable = True
            elif day + grace < as_of:
                lines_near = any(day - grace <= rday <= day + grace for rday in rdates)
                if r_day <= EPS and not lines_near and e.qty_open < e.qty - EPS:
                    se.status = "non évaluable"       # the ERP received it, the extraction has no receipt line
                else:
                    se.status = "non livrée"
                    se.evaluable = True
            else:
                se.status = "ouverte" if day >= as_of or e.qty_open > EPS else "non évaluable"
            if se.evaluable:
                se.qty_score = 100.0 if r_day >= e.qty - EPS else 0.0
                if completed is not None:
                    se.delta_days = calendar.working_days_between(day, completed)
                    se.punct_score = punctuality_score(se.delta_days, params)
                else:
                    se.punct_score = 0.0
            _finish(se, calendar, desadv_ok, desadv_issue, params)
            out.append(se)
    return Matching(sorted(out, key=lambda s: (s.expected_date, s.supplier_id or "", s.article_id)), unmatched, "jour", expected, received)


def matching(ds: Dataset, as_of: dt.date, calendar: WorkCalendar, params: EngineParams, method: str | None = None) -> Matching:
    """The matching of the chosen method (``params.otd_matching`` when none is given)."""
    m = (method or params.otd_matching or "jour").strip().lower()
    return match_slots(ds, as_of, calendar, params) if m == "cumul" else match_days(ds, as_of, calendar, params)


def _mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def _pct(xs: list[float], q: float) -> float | None:
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 1)


def _scores(rows: list[SlotEval]) -> dict[str, Any]:
    return {"n": len(rows), "otd": _mean([s.otd for s in rows]), "qty": _mean([s.qty_score for s in rows]),
            "punctuality": _mean([s.punct_score for s in rows]), "desadv": _mean([s.desadv_score for s in rows]),
            "on_time": sum(1 for s in rows if s.delta_days == 0), "early": sum(1 for s in rows if s.delta_days is not None and s.delta_days < 0),
            "late": sum(1 for s in rows if s.delta_days is not None and s.delta_days > 0), "not_delivered": sum(1 for s in rows if s.status == "non livrée")}


def _window(slots: list[SlotEval], date_from: dt.date, date_to: dt.date, supplier_ids: list[str] | None, article_ids: list[str] | None) -> list[SlotEval]:
    return [s for s in slots if date_from <= s.expected_date <= date_to
            and (not supplier_ids or (s.supplier_id or "") in supplier_ids) and (not article_ids or s.article_id in article_ids)]


class _Balance:
    """Σ expected − Σ received per day, from the day aggregates (method independent) : the backlog (positive) or
    advance (negative) built over a window, in pieces and in days of delivery (÷ mean quantity of a receipt day)."""

    def __init__(self, m: Matching, supplier_ids: list[str] | None, article_ids: list[str] | None):
        self.rows: list[tuple[str | None, dt.date, float, float]] = []    # supplier, day, expected, received
        for key, days in m.expected.items():
            sid, aid = key
            if (supplier_ids and (sid or "") not in supplier_ids) or (article_ids and aid not in article_ids):
                continue
            rec = m.received.get(key, {})
            first = min(days)          # receipts before the first firm slot known for the pair are outside the extraction's horizon
            for day in set(days) | set(rec):
                if day >= first:
                    self.rows.append((sid, day, days[day].qty if day in days else 0.0, rec[day].qty if day in rec else 0.0))

    def of(self, date_from: dt.date, date_to: dt.date, supplier_id: str | None = "*", bucket: str | None = None, label: str | None = None) -> dict[str, Any]:
        rows = [r for r in self.rows if date_from <= r[1] <= date_to and (supplier_id == "*" or r[0] == supplier_id)
                and (label is None or bucket_label(r[1], bucket or "month") == label)]
        exp = sum(r[2] for r in rows)
        rec = sum(r[3] for r in rows)
        rec_days = len({(r[0], r[1]) for r in rows if r[3] > EPS})
        per_day = rec / rec_days if rec_days else 0.0
        return {"balance_qty": round(exp - rec, 1), "balance_days": round((exp - rec) / per_day, 1) if per_day > EPS else None,
                "expected_qty": round(exp, 1), "received_qty": round(rec, 1)}


def otd_report(ds: Dataset, params: EngineParams, as_of: dt.date, calendar: WorkCalendar, bucket: str = "month",
               date_from: dt.date | None = None, date_to: dt.date | None = None, supplier_ids: list[str] | None = None,
               article_ids: list[str] | None = None, names: dict[str, str] | None = None, max_orders: int = 5000,
               method: str | None = None, compare: bool = True) -> dict[str, Any]:
    """OTD and its three indicators per supplier and per period bucket, over the days (slots) expected in the window ;
    ``compare`` adds the scores of the other matching method, computed on the same window."""
    bucket = bucket if bucket in BUCKETS else "month"
    date_to = date_to or (as_of - dt.timedelta(days=1))
    date_from = date_from or (date_to - dt.timedelta(days=365))
    names = names or {s.supplier_id: s.name for s in ds.suppliers}
    arts = {a.article_id: a for a in ds.articles}
    m = matching(ds, as_of, calendar, params, method)
    win = _window(m.slots, date_from, date_to, supplier_ids, article_ids)
    rows = [s for s in win if s.evaluable]
    labels = sorted({bucket_label(s.expected_date, bucket) for s in rows}, key=bucket_start)
    bal = _Balance(m, supplier_ids, article_ids)
    by_sup: dict[str | None, list[SlotEval]] = defaultdict(list)
    for s in rows:
        by_sup[s.supplier_id].append(s)
    suppliers = []
    for sid, ss in sorted(by_sup.items(), key=lambda kv: (kv[0] or "")):
        series = []
        for lab in labels:
            part = [s for s in ss if bucket_label(s.expected_date, bucket) == lab]
            series.append({"period": lab, "start": bucket_start(lab).isoformat(), **_scores(part), **bal.of(date_from, date_to, sid, bucket, lab)})
        suppliers.append({"supplier_id": sid, "name": names.get(sid or "", ""), **_scores(ss), **bal.of(date_from, date_to, sid), "series": series,
                          "articles": len({s.article_id for s in ss})})
    suppliers.sort(key=lambda r: ((r["otd"] if r["otd"] is not None else 101), -(r["n"])))
    totals = {**_scores(rows), **bal.of(date_from, date_to),
              "series": [{"period": lab, "start": bucket_start(lab).isoformat(), **_scores([s for s in rows if bucket_label(s.expected_date, bucket) == lab]),
                          **bal.of(date_from, date_to, "*", bucket, lab)} for lab in labels]}
    other = None
    if compare:
        other_method = "cumul" if m.method == "jour" else "jour"
        m2 = matching(ds, as_of, calendar, params, other_method)
        rows2 = [s for s in _window(m2.slots, date_from, date_to, supplier_ids, article_ids) if s.evaluable]
        by2: dict[str | None, list[SlotEval]] = defaultdict(list)
        for s in rows2:
            by2[s.supplier_id].append(s)
        other = {"method": other_method, "totals": {**_scores(rows2), "series": [{"period": lab, **_scores([s for s in rows2 if bucket_label(s.expected_date, bucket) == lab])} for lab in labels]},
                 "suppliers": {sid or "": _scores(ss) for sid, ss in by2.items()}}
    detail = sorted(win, key=lambda s: (s.expected_date, s.supplier_id or ""), reverse=True)
    return {"as_of": as_of.isoformat(), "from": date_from.isoformat(), "to": date_to.isoformat(), "bucket": bucket, "periods": labels,
            "method": m.method, "default_method": (params.otd_matching or "jour"),
            "params": {"weights": {"qty": params.otd_weight_qty, "punctuality": params.otd_weight_punctuality, "desadv": params.otd_weight_desadv},
                       "band1_days": params.otd_band1_days, "band1_score": params.otd_band1_score, "band2_days": params.otd_band2_days,
                       "band2_score": params.otd_band2_score, "backlog_max_days": params.otd_backlog_max_days},
            "totals": totals, "suppliers": suppliers, "compare": other,
            "not_evaluable": sum(1 for s in win if s.status == "non évaluable"), "open": sum(1 for s in win if s.status == "ouverte"),
            "unmatched_receipts": m.unmatched_receipts, "first_seen_available": m.first_seen_available,
            "orders": [s.to_dict(names, arts) for s in detail[:max_orders]], "truncated": len(detail) > max_orders}


def _dist(values: list[float]) -> dict[str, Any]:
    xs = [x for x in values if x is not None]
    return {"n": len(xs), "mean": _mean(xs), "p50": _pct(xs, 0.5), "p90": _pct(xs, 0.9),
            "std": round(statistics.pstdev(xs), 1) if len(xs) > 1 else (0.0 if xs else None), "min": min(xs) if xs else None, "max": max(xs) if xs else None}


def _weekday_profile(days: list[dt.date]) -> list[float]:
    """Share (%) of Monday … Sunday."""
    n = len(days)
    counts = [0] * 7
    for d in days:
        counts[d.isoweekday() - 1] += 1
    return [round(100.0 * c / n, 1) if n else 0.0 for c in counts]


def _lead_row(ss: list[SlotEval], params: EngineParams) -> dict[str, Any]:
    done = [s for s in ss if s.completed_date is not None]
    deltas = [s.delta_days for s in done]
    late_beyond = sum(1 for s in done if s.delta_days is not None and s.delta_days > params.otd_band2_days)
    obs = [s.lead_observed_wd for s in done if s.lead_observed_wd is not None]
    bias = [s.bias_wd for s in done if s.bias_wd is not None]
    promised = sorted({s.lead_promised for s in ss})
    n_eval = sum(1 for s in ss if s.evaluable)
    return {"n": len(ss), "evaluated": n_eval, "received": len(done), "not_delivered": sum(1 for s in ss if s.status == "non livrée"),
            "delay": _dist(deltas),                     # completion − expected, calendar days (mean = « retard moyen », std = dispersion)
            "on_time_pct": round(100.0 * sum(1 for d in deltas if d == 0) / len(deltas), 1) if deltas else None,
            "early_pct": round(100.0 * sum(1 for d in deltas if d < 0) / len(deltas), 1) if deltas else None,
            "late_pct": round(100.0 * sum(1 for d in deltas if d > 0) / len(deltas), 1) if deltas else None,
            "late_beyond_pct": round(100.0 * late_beyond / len(deltas), 1) if deltas else None,
            "recurring_late": bool(deltas) and len(deltas) >= 3 and sum(1 for d in deltas if d > 0) / len(deltas) >= 0.5,
            "lead_promised": promised[0] if len(promised) == 1 else (f"{promised[0]}–{promised[-1]}" if promised else None),
            "lead_observed": _dist(obs),                 # first appearance → completion, working days
            "bias": _dist(bias),                         # observed − promised, working days
            "order_to_ship": _dist([s.order_to_ship_days for s in ss if s.order_to_ship_days is not None]),
            "ship_to_receipt": _dist([s.ship_to_receipt_days for s in ss if s.ship_to_receipt_days is not None]),
            "urgent_pct": round(100.0 * sum(1 for s in ss if s.urgent_order) / len(ss), 1) if ss and any(s.first_seen for s in ss) else None,
            "partial_pct": round(100.0 * sum(1 for s in done if len(s.receipts) > 1) / len(done), 1) if done else None,
            "qty_per_receipt": _dist([q for s in done for _d, q, _b in s.receipts]),
            "profile_expected": _weekday_profile([s.expected_date for s in ss]),
            "profile_receipts": _weekday_profile([d for s in done for d, _q, _b in s.receipts]),
            "profile_orders": _weekday_profile([s.first_seen for s in ss if s.first_seen is not None])}


def lead_time_report(ds: Dataset, params: EngineParams, as_of: dt.date, calendar: WorkCalendar, date_from: dt.date | None = None,
                     date_to: dt.date | None = None, supplier_ids: list[str] | None = None, article_ids: list[str] | None = None,
                     names: dict[str, str] | None = None, method: str | None = None) -> dict[str, Any]:
    """Observed delays per supplier (and per article when one supplier is chosen) : mean, dispersion (p50 / p90 / std),
    bias against the parameterised lead time, share of urgent orders, recurrence of lateness, weekday profiles."""
    date_to = date_to or (as_of - dt.timedelta(days=1))
    date_from = date_from or (date_to - dt.timedelta(days=365))
    names = names or {s.supplier_id: s.name for s in ds.suppliers}
    arts = {a.article_id: a for a in ds.articles}
    m = matching(ds, as_of, calendar, params, method)
    slots = _window(m.slots, date_from, date_to, supplier_ids, article_ids)
    by_sup: dict[str | None, list[SlotEval]] = defaultdict(list)
    for s in slots:
        by_sup[s.supplier_id].append(s)
    suppliers = [{"supplier_id": sid, "name": names.get(sid or "", ""), **_lead_row(ss, params)} for sid, ss in sorted(by_sup.items(), key=lambda kv: kv[0] or "")]
    suppliers.sort(key=lambda r: (-(r["delay"]["mean"] or 0), -r["n"]))
    articles: list[dict[str, Any]] = []
    if supplier_ids and len(supplier_ids) == 1 or article_ids:
        by_art: dict[tuple[str | None, str], list[SlotEval]] = defaultdict(list)
        for s in slots:
            by_art[(s.supplier_id, s.article_id)].append(s)
        for (sid, aid), ss in sorted(by_art.items(), key=lambda kv: (kv[0][0] or "", kv[0][1])):
            a = arts.get(aid)
            articles.append({"supplier_id": sid, "supplier_name": names.get(sid or "", ""), "article_id": aid, "designation": a.designation if a else "",
                             "unit": a.unit if a else "", **_lead_row(ss, params)})
    return {"as_of": as_of.isoformat(), "from": date_from.isoformat(), "to": date_to.isoformat(), "totals": _lead_row(slots, params),
            "suppliers": suppliers, "articles": articles, "first_seen_available": any(s.first_seen is not None for s in slots),
            "band2_days": params.otd_band2_days, "method": m.method}


# =============================================================================
# ABC / XYZ segmentation
# =============================================================================
def parse_matrix(spec: str, default: float | None = None) -> dict[str, float]:
    out: dict[str, float] = {}
    for part in (spec or "").replace(";", ",").split(","):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        try:
            out[k.strip().upper()] = float(v.strip().replace(",", "."))
        except ValueError:
            continue
    return out


def segmentation(ds: Dataset, params: EngineParams, as_of: dt.date, weeks: int = 26, demand_fallback: dict[str, float] | None = None,
                 current_targets: dict[str, int] | None = None) -> dict[str, Any]:
    """ABC by yearly value (consumption of the last 52 weeks × standard price ; the yearly requirement of the engine when
    no consumption is reported), XYZ by the coefficient of variation of the weekly consumption over the last ``weeks``
    complete ISO weeks (X < ``xyz_x_cv``, Y < ``xyz_y_cv``, Z beyond or without consumption) ; criticality (programmes
    using the article, single source) and the suggested coverage target of the segment."""
    this_monday = iso_week_monday(as_of)
    first_monday = this_monday - dt.timedelta(days=7 * weeks)
    year_ago = as_of - dt.timedelta(days=364)
    weekly: dict[str, dict[dt.date, float]] = defaultdict(lambda: defaultdict(float))
    yearly: dict[str, float] = defaultdict(float)
    for c in ds.consumption:
        if c.date >= as_of:
            continue
        if c.date >= year_ago:
            yearly[c.article_id] += float(c.qty)
        if first_monday <= c.date < this_monday:
            weekly[c.article_id][iso_week_monday(c.date)] += float(c.qty)
    # derived articles : Σ coef × sources
    for a in ds.articles:
        if a.consumption_source == "derived" and a.derived_terms:
            yearly[a.article_id] = sum(coef * yearly.get(src, 0.0) for src, coef in a.derived_terms)
            acc: dict[dt.date, float] = defaultdict(float)
            for src, coef in a.derived_terms:
                for wk, q in weekly.get(src, {}).items():
                    acc[wk] += coef * q
            weekly[a.article_id] = acc
    progs: dict[str, set[str]] = defaultdict(set)
    for b in ds.bom:
        progs[b.article_id].add(b.program_id)
    active_links: dict[str, list] = defaultdict(list)
    for l in ds.links:
        if l.active:
            active_links[l.article_id].append(l)
    suppliers = {s.supplier_id: s for s in ds.suppliers}
    mondays = [first_monday + dt.timedelta(days=7 * k) for k in range(weeks)]
    rows = []
    for a in ds.articles:
        if not a.active:
            continue
        price = ds.prices.get(a.article_id)
        qty_year = yearly.get(a.article_id, 0.0)
        basis = "consommation 52 semaines"
        if qty_year <= EPS and demand_fallback and demand_fallback.get(a.article_id, 0.0) > EPS:
            qty_year, basis = float(demand_fallback[a.article_id]), "besoin de l'horizon (PDP)"
        value = qty_year * price if price is not None else None
        series = [weekly.get(a.article_id, {}).get(m, 0.0) for m in mondays]
        # the series starts at the first week with a consumption : an article launched mid-window is not « irregular »
        first = next((i for i, v in enumerate(series) if v > EPS), None)
        used = series[first:] if first is not None else []
        if len(used) >= 4 and sum(used) > EPS:
            mean = sum(used) / len(used)
            cv = round(statistics.pstdev(used) / mean, 2) if mean > EPS else None
        else:
            cv = None
        xyz = "Z" if cv is None else "X" if cv < float(params.xyz_x_cv) else "Y" if cv < float(params.xyz_y_cv) else "Z"
        links = active_links.get(a.article_id, [])
        lead = max((l.lead_time(suppliers) for l in links), default=None)
        rows.append({"article_id": a.article_id, "designation": a.designation, "unit": a.unit, "planner": a.planner, "family": a.family,
                     "price": price, "qty_year": round(qty_year, 3), "value_year": round(value, 2) if value is not None else None, "value_basis": basis,
                     "cv": cv, "weeks_used": len(used), "xyz": xyz, "programs": len(progs.get(a.article_id, ())), "suppliers": len(links),
                     "single_source": len(links) == 1, "lead_time_days": lead,
                     "coverage_target_days": (current_targets or {}).get(a.article_id, a.coverage_target_days)})
    # ABC on the valued articles
    valued = sorted((r for r in rows if r["value_year"] is not None and r["value_year"] > EPS), key=lambda r: -r["value_year"])
    total = sum(r["value_year"] for r in valued)
    cum = 0.0
    a_pct, b_pct = float(params.abc_a_pct), float(params.abc_b_pct)
    for r in valued:
        cum += r["value_year"]
        share = 100.0 * cum / total if total else 0.0
        r["abc"] = "A" if share <= a_pct + 1e-9 else "B" if share <= b_pct + 1e-9 else "C"
        r["value_share_pct"] = round(100.0 * r["value_year"] / total, 2) if total else 0.0
        r["cum_share_pct"] = round(share, 1)
    for r in rows:
        if "abc" not in r:
            r["abc"] = "–"
            r["value_share_pct"], r["cum_share_pct"] = None, None
        r["segment"] = f"{r['abc']}{r['xyz']}" if r["abc"] != "–" else f"–{r['xyz']}"
        r["criticality"] = ("haute" if r["programs"] >= 3 or (r["single_source"] and r["abc"] == "A") else "moyenne" if r["programs"] >= 1 else "basse")
    coverage = parse_matrix(params.segment_coverage_days)
    service = parse_matrix(params.segment_service_pct)
    for r in rows:
        sug = coverage.get(r["segment"]) if r["abc"] != "–" else None
        r["suggested_coverage_days"] = int(sug) if sug is not None else None
        r["coverage_delta"] = (int(sug) - int(r["coverage_target_days"])) if sug is not None else None
        r["service_target_pct"] = service.get(r["abc"]) if r["abc"] != "–" else None
    rows.sort(key=lambda r: (-(r["value_year"] or -1), r["article_id"]))
    segments: dict[str, dict[str, Any]] = {}
    for r in rows:
        seg = segments.setdefault(r["segment"], {"segment": r["segment"], "abc": r["abc"], "xyz": r["xyz"], "articles": 0, "value_year": 0.0,
                                                 "suggested_coverage_days": r["suggested_coverage_days"], "service_target_pct": r["service_target_pct"],
                                                 "changes": 0})
        seg["articles"] += 1
        seg["value_year"] += r["value_year"] or 0.0
        if r["coverage_delta"]:
            seg["changes"] += 1
    for seg in segments.values():
        seg["value_share_pct"] = round(100.0 * seg["value_year"] / total, 1) if total else None
        seg["value_year"] = round(seg["value_year"], 2)
    return {"as_of": as_of.isoformat(), "weeks": weeks, "from": first_monday.isoformat(), "to": (this_monday - dt.timedelta(days=1)).isoformat(),
            "params": {"abc_a_pct": params.abc_a_pct, "abc_b_pct": params.abc_b_pct, "xyz_x_cv": params.xyz_x_cv, "xyz_y_cv": params.xyz_y_cv,
                       "coverage": coverage, "service": service},
            "total_value_year": round(total, 2), "valued": len(valued), "unvalued": len(rows) - len(valued),
            "segments": sorted(segments.values(), key=lambda s: (s["abc"], s["xyz"])), "rows": rows}
