"""Transports (« Planning transport »).

A **transport** is one truck expected from one supplier on one day, at one time, on one or several docks,
carrying some of the supplier's **firm ERP orders** of that day.  Its identifier is ``EM`` + **5 digits + a letter**
(``EM00123A``) : the number comes from a shared counter (never reused) and names a **family** of trucks ; the letter
ranks the truck in its family : ``A`` for a new truck, ``B``, ``C``… for the trucks a split creates from it (the
same number, the next letter) and, for the MAF shuttle, for the second, third… shuttle of the same day.  An **EV
return** (the supplier comes to take his empty packaging back) is ``EM`` + 4 digits + ``EV`` (``EM0001EV``), drawn
from its own counter.

**Every supplier who delivers** (himself or through a carrier) gets his trucks from the application : one per
working day with a firm order, created, completed, cancelled and placed on a dock slot by the reconciliation
(services/transports_auto.py).  Nobody creates a supplier truck by hand any more ; the planners only act on the
trucks of their own suppliers (move, split, note, cancel, restore) and on the MAF shuttles.  This module keeps the
trucks created by hand **without ERP orders of a supplier** : the **MAF shuttle** (orders of several MAF suppliers,
typed lines, an optional return leg), the **EV return** and the **one-off truck** ; the identifiers ; the CSV.

**CSV for the external transport tool (ATR)** : ``;`` separated, header
``ID TRANSPORT;FOURNISSEUR;TRANSPORTEUR;DATE;HEURE;CODE FOURNISSEUR;FLUX;CHAMP1;CHAMP2;QUAIS``, **one line per
transport and per dock** (every other field repeated, the identifier included), flow always ``R`` ; the return leg
of a shuttle has no line (ATR handles it).  The planner downloads it per transport or globally (every planned
transport from today on).  The tool notifies the suppliers and the carriers : the application sends no e-mail.

**Transport modes** of a supplier (``ref_suppliers.transport_mode``) :

* **Fournisseur** (default) : the supplier delivers ; his trucks are automatic ;
* **MAF Hambach** : the parts wait at the advanced supplier warehouse ; one **shuttle** transport may carry the
  orders of several suppliers ; the CSV shows ``NAVETTE HAMBACH`` as supplier name and code ;
* **Messagerie** : parcel flow, no truck : the supplier is out of the planning and his firm orders are marked in
  the supply table as if a transport existed ;
* **a carrier name** (table ``ref_carriers``) : automatic trucks, the carrier appears in the CSV column TRANSPORTEUR.

**Modes of a truck** (``app_transports.mode``) : ``fournisseur``, ``transporteur``, ``maf`` (shuttle), ``ev`` (EV
return, no order) and ``ponctuel`` (one-off truck : anything else, no order).

**Habitual ranges** of a supplier (``ref_suppliers.transport_hours``) : up to two of ``06-10``, ``08-12``,
``10-14``, ``12-16``, ``14-18``, ``16+`` ; the scheduler places his trucks inside.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..data.store import AppCounter, AppTransport, AppTransportLine, AppTransportOrder, audit, utcnow
from ..engine.calendar import WorkCalendar
from ..engine.models import Dataset

COUNTER = "transport"
COUNTER_EV = "transport_ev"
DOCKS = ("B15", "B6 EST", "B2 NORD", "B6 NORD")
MODE_SUPPLIER, MODE_MAF, MODE_PARCEL = "Fournisseur", "MAF Hambach", "Messagerie"
FIXED_MODES = (MODE_SUPPLIER, MODE_MAF, MODE_PARCEL)
MAF_ID = "MAF-HAMBACH"                 # supplier_id of a shuttle transport
MAF_LABEL = "NAVETTE HAMBACH"          # CSV supplier name and code of a shuttle
MAF_SHORT = "HAMBACH"                  # what the screens write on a shuttle
PARCEL_MARK = "Messagerie"             # what the grid shows instead of a transport identifier
MODE_EV, MODE_PONCTUEL = "ev", "ponctuel"     # trucks without orders : EV return (empty packaging), one-off truck
NO_ORDER_MODES = (MODE_EV, MODE_PONCTUEL)
FLOW = "R"
CSV_HEADER = ("ID TRANSPORT", "FOURNISSEUR", "TRANSPORTEUR", "DATE", "HEURE", "CODE FOURNISSEUR", "FLUX", "CHAMP1", "CHAMP2", "QUAIS")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
APP_USER = "application"

#: habitual ranges : token → (first minute, last minute included) ; ``16+`` runs to the last slot of the day
HOUR_RANGES: dict[str, tuple[int, int]] = {"06-10": (6 * 60, 10 * 60), "08-12": (8 * 60, 12 * 60), "10-14": (10 * 60, 14 * 60),
                                           "12-16": (12 * 60, 16 * 60), "14-18": (14 * 60, 18 * 60), "16+": (16 * 60, 21 * 60)}
MAX_HOUR_RANGES = 2
#: the slots a planner may pick by hand : every half hour from 06:00 to 21:00 ; the automatic grid (``dock_slot_*``) is inside
DAY_FIRST, DAY_LAST, DAY_STEP = 6 * 60, 21 * 60, 30


def split_docks(text: str | None) -> list[str]:
    return [x.strip() for x in re.split(r"[;,]+", text or "") if x.strip()]


def split_emails(text: str | None) -> list[str]:
    return [x.strip().lower() for x in re.split(r"[;,\s]+", text or "") if x.strip()]


def mode_kind(mode: str | None) -> str:
    """``fournisseur`` | ``maf`` | ``messagerie`` | ``transporteur`` (any other non-empty value is a carrier name)."""
    m = (mode or "").strip()
    if not m or m.lower() == MODE_SUPPLIER.lower():
        return "fournisseur"
    if m.lower() == MODE_MAF.lower():
        return "maf"
    if m.lower() == MODE_PARCEL.lower():
        return "messagerie"
    return "transporteur"


def carriers_of(rows: list[dict[str, Any]]) -> dict[str, list[str]]:
    """``carrier name → contact e-mails`` of the active carriers (``ref_carriers``)."""
    return {str(r.get("name") or "").strip(): split_emails(str(r.get("contacts") or "")) for r in rows if str(r.get("name") or "").strip() and bool(r.get("active", True))}


def parse_hours(text: str | None) -> list[str]:
    """Canonical tokens of a « Plages habituelles » value (``6-10 ; >16`` → ``["06-10", "16+"]``) ; raises on an
    unknown range or more than two."""
    out: list[str] = []
    for raw in re.split(r"[;,]+", text or ""):
        tok = raw.strip().lower().replace("h", "").replace(" ", "").replace("–", "-").replace(">", "")
        if not tok:
            continue
        if tok in ("16+", "16", "16-21", "16-"):
            tok = "16+"
        else:
            m = re.fullmatch(r"(\d{1,2})-(\d{1,2})", tok)
            if m:
                tok = f"{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        if tok not in HOUR_RANGES:
            raise ValueError(f"plage « {raw.strip()} » inconnue (attendu : {', '.join(HOUR_RANGES)})")
        if tok not in out:
            out.append(tok)
    if len(out) > MAX_HOUR_RANGES:
        raise ValueError(f"{len(out)} plages : {MAX_HOUR_RANGES} au plus")
    return out


def hours_label(tokens: list[str]) -> str:
    return " · ".join("≥ 16h" if t == "16+" else f"{int(t[:2])}h – {int(t[3:])}h" for t in tokens)


def day_slots() -> list[str]:
    """``06:00, 06:30, …, 21:00`` : the slots a planner may pick."""
    return [f"{m // 60:02d}:{m % 60:02d}" for m in range(DAY_FIRST, DAY_LAST + 1, DAY_STEP)]


def minutes(hhmm: str) -> int | None:
    if not hhmm or not TIME_RE.match(hhmm):
        return None
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


# ----------------------------------------------------------------------------- identifiers
ID_RE = re.compile(r"^(EM\d{5})([A-Z]+)$")


def letters(n: int) -> str:
    """1 → A, 26 → Z, 27 → AA (bijective base 26)."""
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


def letter_rank(s: str) -> int:
    n = 0
    for ch in s:
        n = n * 26 + (ord(ch) - 64)
    return n


def family_of(transport_id: str) -> str | None:
    """``EM00123`` of ``EM00123B`` ; ``None`` for an identifier of the former format (``EM000001``)."""
    m = ID_RE.match(transport_id or "")
    return m.group(1) if m else None


def next_transport_id(session: Session) -> str:
    """A **new family** : ``EM00001A``, ``EM00002A``… ; the counter row is locked (PostgreSQL ``FOR UPDATE``) until
    the commit."""
    row = session.execute(select(AppCounter).where(AppCounter.name == COUNTER).with_for_update()).scalar_one_or_none()
    if row is None:
        row = AppCounter(name=COUNTER, value=0)
        session.add(row)
        session.flush()
    row.value = int(row.value or 0) + 1
    session.flush()
    return f"EM{row.value:05d}A"


def next_in_family(session: Session, transport_id: str) -> str:
    """The next letter of the family of ``transport_id`` (``EM00123A`` → ``EM00123B``, then ``C``…) ; a truck of the
    former format has no family : a new one is drawn."""
    fam = family_of(transport_id)
    if fam is None:
        return next_transport_id(session)
    taken = [letter_rank(ID_RE.match(x).group(2)) for x in session.scalars(select(AppTransport.transport_id).where(AppTransport.transport_id.like(f"{fam}%"))).all() if ID_RE.match(x)]
    return f"{fam}{letters(max(taken, default=0) + 1)}"


def next_ev_id(session: Session) -> str:
    """An **EV return** : ``EM0001EV``, ``EM0002EV``… from its own counter."""
    row = session.execute(select(AppCounter).where(AppCounter.name == COUNTER_EV).with_for_update()).scalar_one_or_none()
    if row is None:
        row = AppCounter(name=COUNTER_EV, value=0)
        session.add(row)
        session.flush()
    row.value = int(row.value or 0) + 1
    session.flush()
    return f"EM{row.value:04d}EV"


def shuttle_id(session: Session, day: dt.date) -> str:
    """The MAF shuttles of one day share a number : the first takes ``A``, the second ``B``… (cancelled ones count)."""
    first = next((t for t in session.scalars(select(AppTransport).where(AppTransport.date == day, AppTransport.supplier_id == MAF_ID).order_by(AppTransport.transport_id)).all()
                  if family_of(t.transport_id)), None)
    return next_in_family(session, first.transport_id) if first else next_transport_id(session)


# ----------------------------------------------------------------------------- queries
def working_days(start: dt.date, weeks: int, holidays: list[dt.date], open_days: list[dt.date] | None = None) -> list[dt.date]:
    cal = WorkCalendar.from_spec(holidays=holidays, extra_open=open_days)
    end = start + dt.timedelta(days=7 * weeks - 1)
    return [start + dt.timedelta(days=i) for i in range((end - start).days + 1) if cal.is_working_day(start + dt.timedelta(days=i))]


def list_transports(session: Session, start: dt.date | None = None, end: dt.date | None = None, supplier_ids: list[str] | None = None,
                    include_cancelled: bool = False) -> list[AppTransport]:
    q = select(AppTransport)
    if start is not None:
        q = q.where(AppTransport.date >= start)
    if end is not None:
        q = q.where(AppTransport.date <= end)
    if supplier_ids is not None:
        q = q.where(AppTransport.supplier_id.in_(supplier_ids))
    if not include_cancelled:
        q = q.where(AppTransport.status == "planned")
    return list(session.scalars(q.order_by(AppTransport.date, AppTransport.time, AppTransport.transport_id)).all())


def transports_by_order(session: Session, ds: Dataset | None = None) -> dict[str, list[str]]:
    """``order_id → [transport ids]`` of the planned transports (the grid marks the firm order cells) ; with the
    dataset, the firm orders of the « Messagerie » suppliers carry the mark ``Messagerie`` (no truck to announce)."""
    out: dict[str, list[str]] = {}
    for t in list_transports(session):
        for o in t.orders:
            out.setdefault(o.order_id, []).append(t.transport_id)
    if ds is not None:
        parcel = {sp.supplier_id for sp in ds.suppliers if mode_kind(sp.transport_mode) == "messagerie"}
        if parcel:
            for o in ds.orders:
                if o.order_type.value == "FIRM" and o.supplier_id in parcel:
                    out.setdefault(o.order_id, []).append(PARCEL_MARK)
    return out


def article_labels(ds: Dataset) -> dict[str, tuple[str, str]]:
    """``article_id → (designation, unit)`` of the dataset (the screens show the article, never the order number)."""
    return {a.article_id: (a.designation or "", a.unit or "") for a in ds.articles}


def transport_out(t: AppTransport, names: dict[str, str], arts: dict[str, tuple[str, str]] | None = None, mine: set[str] | None = None,
                  editable: set[str] | None = None) -> dict[str, Any]:
    """The API view of a truck.  ``mine`` = the suppliers of the portfolio **shown** (the one chosen at the top of the
    screen) : a truck of one of them, or a shuttle, is blue ; ``editable`` = the suppliers the signed-in user may act
    on (his own portfolio, a manager : all) ; without them nothing is."""
    arts = arts or {}
    shuttle = t.mode == "maf" or t.supplier_id == MAF_ID
    own = shuttle or (mine is not None and t.supplier_id in mine)
    may = own if editable is None else (shuttle or t.supplier_id in editable)
    return {"transport_id": t.transport_id, "supplier_id": t.supplier_id, "supplier_name": MAF_LABEL if t.supplier_id == MAF_ID else names.get(t.supplier_id, ""),
            "date": t.date, "time": t.time, "docks": split_docks(t.docks), "carrier": t.carrier or "", "note": t.note or "", "status": t.status,
            "mode": t.mode or "fournisseur", "created_by": t.created_by, "created_at": t.created_at,
            "pinned": bool(t.pinned), "check_reason": t.check_reason or "", "mine": own, "editable": may,
            "return_trip": bool(t.return_trip), "return_docks": split_docks(t.return_docks),
            "cancelled_by": t.cancelled_by or "", "cancelled_at": t.cancelled_at,
            "orders": [{"order_id": o.order_id, "article_id": o.article_id, "designation": arts.get(o.article_id, ("", ""))[0], "unit": arts.get(o.article_id, ("", ""))[1],
                        "supplier_id": o.supplier_id or t.supplier_id, "supplier_name": names.get(o.supplier_id or t.supplier_id, ""), "ref": o.ref or "",
                        "expected_date": o.expected_date, "qty": o.qty}
                       for o in t.orders],
            "lines": [{"id": ln.id, "leg": ln.leg or "aller", "article": ln.article or "", "code": ln.code or "", "designation": ln.designation or "", "qty": ln.qty or ""} for ln in t.lines]}


def planning(ds: Dataset, session: Session, start: dt.date, weeks: int, holidays: list[dt.date], as_of: dt.date,
             scope: str = "main", carriers: dict[str, list[str]] | None = None, slots: list[str] | None = None,
             mine: set[str] | None = None) -> dict[str, Any]:
    """Suppliers × working days : the firm ERP orders of the window (active articles of the dataset), with
    the transports already created for them.  The suppliers with an order in the window appear, plus those with a
    truck without order (EV return, one-off truck).  ``scope`` : ``main`` = the suppliers who deliver themselves or
    through a carrier ; ``maf`` = the suppliers whose parts wait at the MAF Hambach (the shuttle dialog).
    « Messagerie » suppliers never appear (except for an EV return or a one-off truck of theirs)."""
    days = working_days(start, weeks, sorted(set(holidays) | set(ds.holidays)), ds.open_days)
    end = days[-1] if days else start
    arts = {a.article_id: a for a in ds.articles if a.active}
    ignored = {(f.article_id, f.supplier_id or None, f.date) for f in ds.flags if f.kind == "order_ignored"}
    sups = {s.supplier_id: s for s in ds.suppliers}
    wanted = {"maf"} if scope == "maf" else {"fournisseur", "transporteur"}
    transports = [t for t in list_transports(session, start, end) if (t.mode == "maf") == (scope == "maf")]
    by_order: dict[str, list[str]] = {}
    for t in transports:
        for o in t.orders:
            by_order.setdefault(o.order_id, []).append(t.transport_id)
    carriers = carriers or {}
    rows: dict[str, dict[str, Any]] = {}

    def row_of(sid: str) -> dict[str, Any]:
        s = sups.get(sid)
        kind = mode_kind(s.transport_mode if s else "")
        carrier = (s.transport_mode.strip() if s and kind == "transporteur" else "")
        return {"supplier_id": sid, "name": s.name if s else "", "transport_contact": s.transport_contact if s else "",
                "emails": carriers.get(carrier, []) if carrier else (s.transport_emails if s else []), "docks": s.dock_list if s else ["B15"],
                "mode": s.transport_mode if s else MODE_SUPPLIER, "carrier": carrier, "hours": s.hour_ranges if s else [], "orders": []}

    for o in ds.orders:
        if o.order_type.value != "FIRM" or not o.supplier_id or o.expected_date < start or o.expected_date > end:
            continue
        a = arts.get(o.article_id)
        if a is None:
            continue
        s = sups.get(o.supplier_id)
        if mode_kind(s.transport_mode if s else "") not in wanted:
            continue
        row = rows.setdefault(o.supplier_id, row_of(o.supplier_id))
        row["orders"].append({"order_id": o.order_id, "article_id": o.article_id, "designation": a.designation, "unit": a.unit, "ref": o.ref or "",
                              "expected_date": o.expected_date, "qty_ordered": float(o.qty_ordered), "qty_open": float(o.qty_open or 0.0),
                              "ignored": (o.article_id, o.supplier_id, o.expected_date) in ignored, "transports": by_order.get(o.order_id, [])})
    if scope != "maf":          # a supplier with an EV return or a one-off truck and no order still has his row
        for t in transports:
            if t.supplier_id not in rows and t.supplier_id != MAF_ID:
                rows[t.supplier_id] = row_of(t.supplier_id)
    for r in rows.values():
        r["orders"].sort(key=lambda x: (x["expected_date"], x["article_id"], x["ref"]))
    names = {sid: s.name for sid, s in sups.items()}
    labels = article_labels(ds)
    return {"as_of": as_of, "start": start, "end": end, "days": days, "docks": list(DOCKS), "scope": scope, "slots": slots or [],
            "suppliers": sorted(rows.values(), key=lambda r: (r["name"].upper(), r["supplier_id"])),
            "transports": [transport_out(t, names, labels, mine) for t in transports]}


def maf_pending(ds: Dataset, session: Session, as_of: dt.date, holidays: list[dt.date]) -> tuple[dt.date | None, int]:
    """The **next working day** and its MAF orders (firm, open, not ignored) that no planned shuttle carries yet :
    the counter of the « Navette MAF » button."""
    cal = WorkCalendar.from_spec(holidays=sorted(set(holidays) | set(ds.holidays)), extra_open=ds.open_days)
    day = cal.next_working_day(as_of, inclusive=False)
    maf = {s.supplier_id for s in ds.suppliers if mode_kind(s.transport_mode) == "maf"}
    if not maf:
        return day, 0
    arts = {a.article_id for a in ds.articles if a.active}
    ignored = {(f.article_id, f.supplier_id or None, f.date) for f in ds.flags if f.kind == "order_ignored"}
    carried = {o.order_id for t in list_transports(session, day, day) for o in t.orders}
    n = sum(1 for o in ds.orders if o.order_type.value == "FIRM" and o.supplier_id in maf and o.expected_date == day and o.article_id in arts
            and float(o.qty_open if o.qty_open is not None else o.qty_ordered) > 0 and (o.article_id, o.supplier_id, o.expected_date) not in ignored
            and o.order_id not in carried)
    return day, n


# ----------------------------------------------------------------------------- creation
class TransportError(ValueError):
    pass


def check_docks(docks: list[str] | None) -> list[str]:
    clean = [d for d in dict.fromkeys(str(x).strip() for x in (docks or [])) if d]
    bad = [d for d in clean if d not in DOCKS]
    if bad or not clean:
        raise TransportError(f"Quai(s) inconnu(s) : « {', '.join(bad) or 'aucun'} » (attendu {', '.join(DOCKS)})")
    return clean


def check_time(time: str | None) -> str:
    time = (time or "").strip()
    if not TIME_RE.match(time):
        raise TransportError(f"Heure invalide : « {time} » (attendu HH:MM)")
    return time


LEG_EV = "ev"


def _clean_lines(lines: list[dict[str, Any]] | None, leg: str) -> list[AppTransportLine]:
    """Typed lines of a shuttle (article required) or packaging lines of an EV return (``leg = ev`` : every packaging
    of the supplier is kept, with or without a planned quantity ; the code is required)."""
    out: list[AppTransportLine] = []
    for ln in lines or []:
        article = str(ln.get("article") or "").strip()[:120]
        code = str(ln.get("code") or "").strip()[:60]
        designation = str(ln.get("designation") or "").strip()[:200]
        qty = str(ln.get("qty") or "").strip()[:40]
        if leg == LEG_EV:
            if not code:
                raise TransportError(f"Emballage sans code : « {designation or article} »")
            out.append(AppTransportLine(leg=leg, article=article, code=code, designation=designation, qty=qty))
            continue
        if not article and not designation:
            continue
        if not article:
            raise TransportError(f"Ligne {leg} sans article : « {designation} »")
        out.append(AppTransportLine(leg=leg, article=article, code=code, designation=designation, qty=qty))
    return out


def packaging_of(ds: Dataset, packaging: list[dict[str, Any]], supplier_id: str) -> list[dict[str, Any]]:
    """The packaging lines a supplier may take back : the ``ref_packaging`` rows of the articles he supplies (active
    article ↔ supplier rules), one line per article and packaging code, with the article's designation."""
    arts = {a.article_id: a for a in ds.articles}
    mine = {l.article_id for l in ds.links if l.supplier_id == supplier_id and getattr(l, "active", True)}
    out = []
    for r in packaging:
        aid = str(r.get("article_id") or "").strip()
        if aid not in mine:
            continue
        w = r.get("unit_weight_kg")
        out.append({"article_id": aid, "designation": arts[aid].designation if aid in arts else "", "packaging_code": str(r.get("packaging_code") or "").strip(),
                    "packaging_name": str(r.get("packaging_name") or "").strip(), "unit_weight_kg": None if w in (None, "") or (isinstance(w, float) and w != w) else float(w)})
    return sorted(out, key=lambda x: (x["article_id"], x["packaging_code"]))


def create_shuttle(session: Session, user: str, ds: Dataset, day: dt.date, time: str, docks: list[str], order_ids: list[str],
                   lines: list[dict[str, Any]] | None = None, return_trip: bool = False, return_docks: list[str] | None = None,
                   return_lines: list[dict[str, Any]] | None = None, carrier: str = "", note: str = "") -> AppTransport:
    """One **MAF shuttle** : the firm orders of any « MAF Hambach » suppliers (ticked), plus **typed lines** (article,
    description, quantity) ; with ``return_trip`` the shuttle goes back loaded to the MAF on ``return_docks`` with
    its own typed lines (no slot, no time : the same as the outward leg, ATR refines).  Does not commit."""
    sups = {s.supplier_id: s for s in ds.suppliers}
    orders = {o.order_id: o for o in ds.orders if o.order_type.value == "FIRM" and o.supplier_id in sups and mode_kind(sups[o.supplier_id].transport_mode) == "maf"}
    missing = [oid for oid in order_ids if oid not in orders]
    if missing:
        raise TransportError(f"Commande(s) inconnue(s) ou hors MAF Hambach : {', '.join(missing)}")
    time = check_time(time)
    docks = check_docks(docks)
    out_lines = _clean_lines(lines, "aller")
    back_lines = _clean_lines(return_lines, "retour") if return_trip else []
    back_docks = check_docks(return_docks or docks) if return_trip else []
    if not order_ids and not out_lines and not back_lines:
        raise TransportError("Une navette emporte au moins une commande MAF ou une ligne saisie")
    t = AppTransport(transport_id=shuttle_id(session, day), supplier_id=MAF_ID, date=day, time=time, docks=";".join(docks),
                     carrier=(carrier or "").strip()[:120], note=(note or "").strip()[:500], mode="maf", status="planned", created_by=user,
                     pinned=True, check_reason="", return_trip=bool(return_trip), return_docks=";".join(back_docks))
    for oid in dict.fromkeys(order_ids):
        o = orders[oid]
        t.orders.append(AppTransportOrder(order_id=oid, article_id=o.article_id, supplier_id=o.supplier_id or "", ref=o.ref or "", expected_date=o.expected_date,
                                          qty=float(o.qty_open if o.qty_open is not None else o.qty_ordered)))
    t.lines.extend(out_lines + back_lines)
    session.add(t)
    session.flush()
    audit(session, user, "create", "transport", t.transport_id, None,
          {"supplier_id": MAF_ID, "mode": "maf", "date": day.isoformat(), "time": time, "docks": docks, "orders": list(dict.fromkeys(order_ids)),
           "lines": len(out_lines), "return_trip": bool(return_trip), "return_docks": back_docks, "return_lines": len(back_lines)})
    return t


def set_lines(session: Session, user: str, t: AppTransport, lines: list[dict[str, Any]] | None, return_trip: bool | None = None,
              return_docks: list[str] | None = None, return_lines: list[dict[str, Any]] | None = None) -> None:
    """The typed lines of a shuttle (both legs) and its return leg, or the packaging lines of an EV return, replaced
    as a whole.  Does not commit."""
    if t.status != "planned":
        raise TransportError(f"{t.transport_id} est annulé")
    if t.mode == MODE_EV:
        new_lines = _clean_lines(lines, LEG_EV)
        t.lines.clear()
        t.lines.extend(new_lines)
        session.flush()
        audit(session, user, "lines", "transport", t.transport_id, None, {"lines": len(new_lines), "leg": LEG_EV})
        return
    if t.mode != "maf":
        raise TransportError(f"{t.transport_id} n'est ni une navette ni un retour EV")
    back = bool(t.return_trip) if return_trip is None else bool(return_trip)
    new_lines = _clean_lines(lines, "aller") + (_clean_lines(return_lines, "retour") if back else [])
    if back:
        t.return_docks = ";".join(check_docks(return_docks or split_docks(t.return_docks) or split_docks(t.docks)))
    else:
        t.return_docks = ""
    t.return_trip = back
    t.lines.clear()
    t.lines.extend(new_lines)
    session.flush()
    audit(session, user, "lines", "transport", t.transport_id, None, {"lines": len(new_lines), "return_trip": back, "return_docks": split_docks(t.return_docks)})


def create_special(session: Session, user: str, ds: Dataset, kind: str, supplier_id: str, day: dt.date, time: str, docks: list[str],
                   carrier: str = "", note: str = "", lines: list[dict[str, Any]] | None = None) -> AppTransport:
    """A truck **without order** : ``ev`` = an EV return (the supplier comes to take his empty packaging back ;
    identifier ``EM0001EV`` from its own counter ; ``lines`` = the packaging and planned quantities, printed on the
    return note) or ``ponctuel`` = a one-off truck (anything else : samples, a return of parts, a truck outside the
    orders ; a new family ``EM00124A``).  Does not commit."""
    if kind not in NO_ORDER_MODES:
        raise TransportError(f"Type de camion inconnu : « {kind} »")
    sp = next((s for s in ds.suppliers if s.supplier_id == supplier_id), None)
    if sp is None:
        raise TransportError(f"Fournisseur inconnu : {supplier_id or '(vide)'}")
    time = check_time(time)
    docks = check_docks(docks)
    tid = next_ev_id(session) if kind == MODE_EV else next_transport_id(session)
    t = AppTransport(transport_id=tid, supplier_id=supplier_id, date=day, time=time, docks=";".join(docks), carrier=(carrier or "").strip()[:120],
                     note=(note or "").strip()[:500], mode=kind, status="planned", created_by=user, pinned=True, check_reason="")
    if kind == MODE_EV:
        t.lines.extend(_clean_lines(lines, LEG_EV))
    session.add(t)
    session.flush()
    audit(session, user, "create", "transport", t.transport_id, None,
          {"supplier_id": supplier_id, "mode": kind, "date": day.isoformat(), "time": time, "docks": docks, "carrier": t.carrier, "orders": [], "lines": len(t.lines)})
    return t


def cancel_transport(session: Session, user: str, t: AppTransport) -> None:
    t.status = "cancelled"
    t.cancelled_by, t.cancelled_at = user, utcnow()
    audit(session, user, "cancel", "transport", t.transport_id, None, {"supplier_id": t.supplier_id, "date": t.date.isoformat()})
    session.flush()


# ----------------------------------------------------------------------------- CSV (ATR)
SHUTTLE_CARRIER = "TRANSCO"            # TRANSPORTEUR of a MAF shuttle
PACKAGING_LABEL = "PACKAGING"          # FOURNISSEUR of an EV return


def csv_cells(t: AppTransport, names: dict[str, str], modes: dict[str, str] | None = None) -> tuple[str, str] | None:
    """The FOURNISSEUR and TRANSPORTEUR cells of a transport in the ATR file, or ``None`` when the transport has no
    line (supplier in Messagerie mode).  The tool shows FOURNISSEUR as the « Route » of the e-mail it sends to the
    transport contact, so the cell carries what that contact needs :

    * MAF shuttle : ``NAVETTE HAMBACH`` / ``TRANSCO`` ;
    * EV return : ``PACKAGING`` / the supplier (or his carrier) ;
    * one-off truck : the supplier / the supplier (or his carrier) ;
    * supplier truck by a carrier : the supplier / the carrier (unchanged) ;
    * supplier truck delivered by the supplier himself : ``CO-000001 | B15 | B6 EST`` (its purchase orders then its
      docks) / the supplier, who reads his own orders and docks in the e-mail instead of his own name."""
    if t.mode == "maf" or t.supplier_id == MAF_ID:
        return MAF_LABEL, SHUTTLE_CARRIER
    name = names.get(t.supplier_id, t.supplier_id)
    mode = (modes or {}).get(t.supplier_id, MODE_SUPPLIER)
    kind = mode_kind(mode)
    if kind == "messagerie":
        return None
    by_carrier = (t.carrier or "").strip() or (mode.strip() if kind == "transporteur" else "")
    if t.mode == MODE_EV:
        return PACKAGING_LABEL, by_carrier or name
    if t.mode == MODE_PONCTUEL:
        return name, by_carrier or name
    if t.mode == "transporteur" or by_carrier:
        return name, by_carrier
    refs = list(dict.fromkeys(x.strip() for o in t.orders for x in (o.ref or "").replace(";", ",").split(",") if x.strip()))
    return " | ".join(([", ".join(refs)] if refs else []) + split_docks(t.docks)), name


def atr_csv(transports: list[AppTransport], names: dict[str, str], modes: dict[str, str] | None = None) -> str:
    """Header then **one line per transport and per dock** (identifier and every other field repeated), ``;``
    separated, CRLF (the tool runs on Windows) ; the return leg of a shuttle has no line ; FOURNISSEUR and
    TRANSPORTEUR as :func:`csv_cells` (a Messagerie supplier never appears) :
    ``EM00001A;CO-000001 | B15;ENNOVI HUNGARY;03/11/2026;14:00:00;S-000517;R;;;B15``."""
    lines = [";".join(CSV_HEADER)]
    for t in transports:
        cells2 = csv_cells(t, names, modes)
        if cells2 is None:
            continue
        shuttle = t.mode == "maf" or t.supplier_id == MAF_ID
        for dock in split_docks(t.docks) or [""]:
            cells = [t.transport_id, cells2[0], cells2[1], f"{t.date:%d/%m/%Y}", f"{t.time}:00", MAF_LABEL if shuttle else t.supplier_id, FLOW, "", "", dock]
            lines.append(";".join(c.replace(";", " ") for c in cells))
    return "\r\n".join(lines) + "\r\n"


def csv_filename(transports: list[AppTransport]) -> str:
    ids = [t.transport_id for t in transports]
    return f"ATR_{ids[0]}.csv" if len(ids) == 1 else f"ATR_{ids[0]}-{ids[-1]}.csv"
