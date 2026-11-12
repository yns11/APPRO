"""Automatic trucks (docs/regles_metier.md § 9 ter bis, docs/transports_automatiques.md).

**Every supplier who delivers** (modes *Fournisseur* and carrier) is automatic : at every change of the data version
the **reconciliation** (:func:`reconcile`) compares, for every working day of the two planning weeks (the current
one and the next), the firm ERP orders expected from the supplier with the trucks that exist :

* a day with at least one firm order and no truck gets **one truck** carrying every order of the day ; a new order of
  a day that already has its truck **rides it** ; an order that leaves a day (date changed in the ERP, closed,
  deleted, ignored) leaves the truck, and an empty truck is **cancelled** (the EM number is never reused) ;
* a **new truck is placed once**, at its creation, and never moves by itself afterwards : only a planner replaces
  it.  The placement (:func:`place_spread`) never exceeds ``dock_slot_capacity`` trucks per dock and slot, stays in
  the supplier's **habitual ranges** (``ref_suppliers.transport_hours``, up to two of 06-10 … 16+) when he has some,
  else in the automatic grid (``dock_slot_start`` → ``dock_slot_end`` every ``dock_slot_minutes``), and takes, among
  the free slots, the one **farthest from the trucks already on the dock** (earliest on a tie) ; the trucks of a day
  are placed from the most constrained supplier to the least.  No room at all → the last slot and **à vérifier** ;
  a closed dock, an order appearing for today or tomorrow → **à vérifier** too.

**Slots by hand** (move, split, shuttle, EV return, one-off truck, restore) : every half hour from 06:00 to 21:00.
An occupied slot (``capacity`` trucks already on one of the docks) is refused with the nearest free slot proposed
(:class:`SlotConflict`) ; the planner may **force** up to ``2 × capacity`` trucks per dock and slot, never beyond.

The application only creates, changes and cancels the trucks : the notices to the suppliers and carriers are sent
by the transport tool (ATR) from the CSV, never by the application.

Today's trucks are on the road : the reconciliation adds a truck or an order for today (à vérifier) but never removes
anything from today ; past days are frozen.  The planner acts on **his** trucks (the suppliers of his portfolio) and
on the MAF shuttles : copy the identifier, split, move (day, hour, docks), note, cancel, restore, « vu » ; a cancelled
supplier truck leaves a **suppressed day** (``app_transport_days_suppressed``) : a truck is born there again only
when an order that was not in the cancelled truck appears.  Cancelling one truck of a **split family** (``EM00123B``
while ``EM00123A`` runs the same day) gives its orders back to the other truck.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from ..data.store import (
    AppMeta,
    AppTransport,
    AppTransportOrder,
    AppTransportSuppressed,
    audit,
    read_data_version,
    utcnow,
)
from ..engine.calendar import WorkCalendar, iso_week_monday
from ..engine.models import Dataset, EngineParams, Supplier
from . import transports as T

log = logging.getLogger(__name__)

PLANNING_WEEKS = 2                      # the current week and the next : nothing is planned beyond
RECONCILED_KEY = "transports_reconciled_version"   # app_meta : the data version the last reconciliation processed
RECONCILE_LOCK_KEY = 7_419_302          # pg_advisory_xact_lock : one reconciliation at a time across the workers
APP_USER = T.APP_USER
FORCE_FACTOR = 2                        # a planner may force up to FORCE_FACTOR × capacity trucks per dock and slot
WEEKDAYS_FR = ("lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim.")
_RECONCILE_LOCK = threading.Lock()


# ----------------------------------------------------------------------------- slots
def _minutes(hhmm: str, default: str) -> int:
    s = (hhmm or default).strip()
    if not T.TIME_RE.match(s):
        s = default
    h, m = s.split(":")
    return int(h) * 60 + int(m)


def _hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def slot_grid(params: EngineParams) -> list[str]:
    """``07:00, 07:30, …, 15:30`` : the slots of the automatic placement (``dock_slot_*`` rules)."""
    start = _minutes(params.dock_slot_start, "07:00")
    end = _minutes(params.dock_slot_end, "15:30")
    step = max(int(params.dock_slot_minutes or 30), 5)
    if end < start:
        start, end = end, start
    return [_hhmm(m) for m in range(start, end + 1, step)]


def slot_of(time: str, grid: list[str]) -> str | None:
    """The slot a time falls in (floor), ``None`` outside the grid."""
    if not time or not T.TIME_RE.match(time) or not grid:
        return None
    m = _minutes(time, grid[0])
    first, last = _minutes(grid[0], "07:00"), _minutes(grid[-1], "15:30")
    step = (_minutes(grid[1], "07:30") - first) if len(grid) > 1 else 30
    if m < first or m >= last + step:
        return None
    return grid[min((m - first) // step, len(grid) - 1)]


def capacity_of(params: EngineParams) -> int:
    return max(int(params.dock_slot_capacity or 1), 1)


class Occupancy:
    """Trucks per (dock, slot) of one day, on the half-hour slots of the day (``T.day_slots()``)."""

    def __init__(self, grid: list[str] | None = None) -> None:
        self.grid = grid or T.day_slots()
        self.count: dict[tuple[str, str], int] = {}

    def add(self, docks: list[str], time: str, grid: list[str] | None = None) -> None:
        slot = slot_of(time, grid or self.grid)
        if slot is None:
            return
        for d in docks:
            self.count[(d.upper(), slot)] = self.count.get((d.upper(), slot), 0) + 1

    def load(self, docks: list[str], slot: str) -> int:
        return max((self.count.get((d.upper(), slot), 0) for d in docks), default=0)

    def free(self, docks: list[str], slot: str, capacity: int) -> bool:
        return all(self.count.get((d.upper(), slot), 0) < capacity for d in docks)

    def occupied(self, docks: list[str]) -> list[int]:
        """Minutes of the slots where at least one truck stands on one of ``docks``."""
        return sorted({_minutes(s, "07:00") for (d, s), n in self.count.items() if n > 0 and d in {x.upper() for x in docks}})


def candidates_for(sp: Supplier | None, grid: list[str]) -> list[str]:
    """The slots a new truck of the supplier may take : the half hours of his habitual ranges when he has some, else
    the automatic grid."""
    tokens = sp.hour_ranges if sp is not None else []
    if not tokens:
        return list(grid)
    out: list[str] = []
    for s in T.day_slots():
        m = _minutes(s, "06:00")
        if any(lo <= m <= hi for lo, hi in (T.HOUR_RANGES[t] for t in tokens if t in T.HOUR_RANGES)) and s not in out:
            out.append(s)
    return out or list(grid)


def place_spread(occ: Occupancy, candidates: list[str], docks: list[str], capacity: int) -> tuple[str, str]:
    """The slot of a **new** truck : among the free candidates, the one farthest from the trucks already standing on
    its docks (earliest on a tie) ; no free candidate → the nearest free slot of the whole day ; no room at all → the
    last candidate and the reason « à vérifier »."""
    if not candidates:
        candidates = T.day_slots()
    busy = occ.occupied(docks)
    free = [s for s in candidates if occ.free(docks, s, capacity)]
    if not free:
        anchor = _minutes(candidates[len(candidates) // 2], "07:00")
        rest = sorted((s for s in T.day_slots() if occ.free(docks, s, capacity)), key=lambda s: (abs(_minutes(s, "07:00") - anchor), _minutes(s, "07:00")))
        if rest:
            return rest[0], ""
        return candidates[-1], f"quai {', '.join(docks)} complet toute la journée : déplacer le camion"
    if not busy:
        return free[0], ""

    def distance(s: str) -> int:
        m = _minutes(s, "07:00")
        return min(abs(m - b) for b in busy)

    return max(free, key=lambda s: (distance(s), -_minutes(s, "07:00"))), ""


def place(occ: Occupancy, grid: list[str], docks: list[str], preferred: str | None, current: str | None, capacity: int) -> tuple[str, str]:
    """The nearest free slot to ``current`` or ``preferred`` (the second truck of a split) ; no room anywhere → the
    last slot and the reason « à vérifier »."""
    if not grid:
        return current or preferred or "08:00", ""
    cur = slot_of(current or "", grid)
    pref = slot_of(preferred or "", grid)
    for cand in (cur, pref):
        if cand is not None and occ.free(docks, cand, capacity):
            return cand, ""
    anchor = pref if pref is not None else cur if cur is not None else grid[0]
    i = grid.index(anchor)
    if occ.free(docks, anchor, capacity):
        return anchor, ""
    for k in range(1, len(grid)):
        for j in (i + k, i - k):
            if 0 <= j < len(grid) and occ.free(docks, grid[j], capacity):
                return grid[j], ""
    full = ", ".join(docks)
    return grid[-1], f"quai {full} complet toute la journée : déplacer le camion"


def dock_closures(rows: list[dict[str, Any]]) -> dict[str, set[dt.date]]:
    """``dock (upper) → closed days`` from the ``ref_calendars`` rows (calendar « quai »)."""
    out: dict[str, set[dt.date]] = {}
    for r in rows:
        if not bool(r.get("active", True)) or str(r.get("calendar") or "").strip().lower() != "quai":
            continue
        if str(r.get("kind") or "fermeture").strip().lower() == "ouvert":
            continue
        a, b = r.get("date_from"), r.get("date_to")
        if a is None or (isinstance(a, float)):
            continue
        a = a if isinstance(a, dt.date) else dt.date.fromisoformat(str(a)[:10])
        b = a if b is None or (isinstance(b, float)) or str(b) in ("", "NaT") else (b if isinstance(b, dt.date) else dt.date.fromisoformat(str(b)[:10]))
        key = str(r.get("key") or "").strip().upper()
        days = out.setdefault(key, set())
        for i in range(min((b - a).days, 400) + 1):
            days.add(a + dt.timedelta(days=i))
    return out


# ----------------------------------------------------------------------------- occupied slot (by hand)
@dataclass
class SlotCheck:
    """What the planner is told about the slot he asks for."""

    day: dt.date
    time: str
    slot: str | None            # the half hour the time falls in (None outside 06:00 → 21:00 : no check)
    docks: list[str]
    count: int                  # trucks already on the fullest of the docks at that slot
    capacity: int
    hard_max: int               # FORCE_FACTOR × capacity : never beyond, even forced
    proposed: str | None        # the nearest free slot of the day (None when the day is full)
    others: list[str]           # identifiers of the trucks already there

    @property
    def free(self) -> bool:
        return self.slot is None or self.count < self.capacity

    @property
    def forceable(self) -> bool:
        return self.slot is not None and self.count < self.hard_max

    def to_dict(self) -> dict[str, Any]:
        return {"date": self.day.isoformat(), "time": self.time, "slot": self.slot, "docks": self.docks, "count": self.count, "capacity": self.capacity,
                "hard_max": self.hard_max, "proposed": self.proposed, "others": self.others, "free": self.free, "forceable": self.forceable}


class ActionError(ValueError):
    pass


class SlotConflict(Exception):
    """The slot asked for is occupied (``capacity`` reached) and the planner did not force : the check carries the
    nearest free slot ; forcing is possible while the hard maximum is not reached."""

    def __init__(self, check: SlotCheck) -> None:
        super().__init__(f"créneau {check.time} occupé sur {', '.join(check.docks)} ({check.count} camions)")
        self.check = check


def slot_check(session: Session, params: EngineParams, day: dt.date, time: str, docks: list[str], exclude: str | None = None) -> SlotCheck:
    """Who is already on the docks at that time, the nearest free slot."""
    grid = T.day_slots()
    capacity = capacity_of(params)
    occ = Occupancy(grid)
    slot = slot_of(time, grid)
    others: list[str] = []
    for t in T.list_transports(session, day, day):
        if t.transport_id == exclude:
            continue
        occ.add(T.split_docks(t.docks), t.time)
        if slot is not None and slot_of(t.time, grid) == slot and {d.upper() for d in T.split_docks(t.docks)} & {d.upper() for d in docks}:
            others.append(t.transport_id)
    count = occ.load(docks, slot) if slot is not None else 0
    proposed = None
    if slot is not None and count >= capacity:
        anchor = _minutes(slot, "07:00")
        free = sorted((s for s in grid if occ.free(docks, s, capacity)), key=lambda s: (abs(_minutes(s, "07:00") - anchor), -_minutes(s, "07:00")))
        proposed = free[0] if free else None
    return SlotCheck(day, time, slot, docks, count, capacity, capacity * FORCE_FACTOR, proposed, others)


def ensure_slot(session: Session, params: EngineParams, day: dt.date, time: str, docks: list[str], force: bool, exclude: str | None = None) -> SlotCheck:
    """Refuse an occupied slot unless forced ; refuse a full slot (hard maximum) whatever happens."""
    chk = slot_check(session, params, day, time, docks, exclude)
    if chk.free:
        return chk
    if not chk.forceable:
        raise ActionError(f"Créneau {time} plein sur {', '.join(docks)} : {chk.count} camions, {chk.hard_max} au plus même en forçant"
                          + (f" ; le plus proche libre : {chk.proposed}" if chk.proposed else ""))
    if not force:
        raise SlotConflict(chk)
    return chk


# ----------------------------------------------------------------------------- helpers
def _not_shuttle(t: AppTransport) -> bool:
    return (t.mode or "") != "maf" and t.supplier_id != T.MAF_ID


def _carries_orders(t: AppTransport) -> bool:
    """A truck the reconciliation reasons about : a supplier truck (not a shuttle, an EV return nor a one-off truck)."""
    return _not_shuttle(t) and (t.mode or "") not in T.NO_ORDER_MODES


def _qty_of(o: Any) -> float:
    return float(o.qty_open if o.qty_open is not None else o.qty_ordered)


# ----------------------------------------------------------------------------- reconciliation
def reconcile(session: Session, ds: Dataset, params: EngineParams, as_of: dt.date, carriers: dict[str, list[str]] | None = None,
              dock_closed: dict[str, set[dt.date]] | None = None, user: str = APP_USER) -> dict[str, Any]:
    """Expected state ↔ existing trucks of every delivering supplier over the two planning weeks, then the slot of
    every **new** truck (module docstring).  Does not commit.  Returns the counts (``created``, ``completed``,
    ``removed``, ``cancelled``) and ``changed``."""
    dock_closed = dock_closed or {}
    cal = WorkCalendar.from_spec(params.working_weekdays, holidays=ds.holidays, extra_open=ds.open_days)
    start = iso_week_monday(as_of)
    end = start + dt.timedelta(days=7 * PLANNING_WEEKS - 1)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1) if cal.is_working_day(start + dt.timedelta(days=i))]
    grid = slot_grid(params)
    capacity = capacity_of(params)
    sups = {s.supplier_id: s for s in ds.suppliers}
    auto = {sid: s for sid, s in sups.items() if s.active and T.mode_kind(s.transport_mode) in ("fournisseur", "transporteur")}
    arts = {a.article_id for a in ds.articles if a.active}
    ignored = {(f.article_id, f.supplier_id or None, f.date) for f in ds.flags if f.kind == "order_ignored"}
    expected: dict[tuple[str, dt.date], dict[str, Any]] = {}
    for o in ds.orders:
        if o.order_type.value != "FIRM" or o.supplier_id not in auto or o.article_id not in arts:
            continue
        if o.expected_date is None or o.expected_date < as_of or o.expected_date > end:
            continue
        if (o.article_id, o.supplier_id, o.expected_date) in ignored or _qty_of(o) <= 0:
            continue
        expected.setdefault((o.supplier_id, o.expected_date), {})[o.order_id] = o
    rows = T.list_transports(session, start - dt.timedelta(days=56), end)       # planned, every supplier
    trucks_all = [t for t in rows if _carries_orders(t)]
    suppressed = {(s.supplier_id, s.date): s for s in session.scalars(select(AppTransportSuppressed).where(AppTransportSuppressed.date >= start)).all()}
    by_day: dict[tuple[str, dt.date], list[AppTransport]] = {}
    for t in trucks_all:
        if t.date >= start:
            by_day.setdefault((t.supplier_id, t.date), []).append(t)
    counts = {"created": 0, "completed": 0, "removed": 0, "cancelled": 0}
    tomorrow = cal.next_working_day(as_of, inclusive=False)
    born: list[AppTransport] = []
    # a supplier who left the delivering modes (MAF Hambach, Messagerie, inactive) : his trucks to come are cancelled
    for t in trucks_all:
        if t.status == "planned" and t.date > as_of and t.supplier_id not in auto:
            t.status, t.cancelled_by, t.cancelled_at = "cancelled", user, utcnow()
            counts["cancelled"] += 1
            audit(session, user, "cancel", "transport", t.transport_id, None, {"supplier_id": t.supplier_id, "date": t.date.isoformat(), "reason": "fournisseur plus livrant"})

    for sid in sorted(auto):
        sp = auto[sid]
        wanted = {oid: o for (s_id, _d), orders in expected.items() if s_id == sid for oid, o in orders.items()}
        mine = [t for (s_id, d), ts in by_day.items() if s_id == sid for t in ts if t.status == "planned"]
        rides: dict[str, int] = {}
        for t in mine:
            for o in t.orders:
                rides[o.order_id] = rides.get(o.order_id, 0) + 1
        # an order that left the firm orders leaves its truck ; an order whose ERP date moved leaves a truck the planner
        # did not place by hand (it rides its new day's truck below) ; today's trucks are on the road : untouched
        for t in mine:
            if t.date <= as_of:
                continue
            for o in list(t.orders):
                want = wanted.get(o.order_id)
                if want is None or (not t.pinned and want.expected_date != t.date):
                    t.orders.remove(o)
                    counts["removed"] += 1
                elif rides.get(o.order_id, 1) == 1 and abs(_qty_of(want) - float(o.qty or 0.0)) > 1e-9:
                    o.qty = _qty_of(want)      # an order split over several trucks keeps the planner's quantities
            if not t.orders and t.mode in ("fournisseur", "transporteur"):
                t.status, t.cancelled_by, t.cancelled_at = "cancelled", user, utcnow()
                counts["cancelled"] += 1
                audit(session, user, "cancel", "transport", t.transport_id, None, {"supplier_id": sid, "date": t.date.isoformat(), "reason": "plus aucune commande"})
        carried = {o.order_id for t in mine if t.status == "planned" for o in t.orders}
        for day in days:
            if day < as_of:
                continue
            exp = expected.get((sid, day), {})
            missing = [oid for oid in exp if oid not in carried]
            if not missing:
                continue
            trucks = [t for t in by_day.get((sid, day), []) if t.status == "planned"]
            target = next((t for t in trucks if not t.pinned), None) or (trucks[0] if trucks else None)
            new = target is None
            if new:
                row = suppressed.get((sid, day))
                if row is not None:
                    known = set(json.loads(row.orders_json or "[]"))
                    if set(missing) <= known:
                        continue        # the planner cancelled this day's truck and nothing new came since
                    session.delete(row)
                    suppressed.pop((sid, day), None)
                carrier = sp.transport_mode.strip() if T.mode_kind(sp.transport_mode) == "transporteur" else ""
                target = AppTransport(transport_id=T.next_transport_id(session), supplier_id=sid, date=day, time="", docks=";".join(sp.dock_list),
                                      carrier=carrier[:120], note="", mode="transporteur" if carrier else "fournisseur", status="planned", created_by=user,
                                      pinned=False, check_reason="")
                session.add(target)
                by_day.setdefault((sid, day), []).append(target)
                born.append(target)
                counts["created"] += 1
                if day <= tomorrow:
                    target.check_reason = f"commande apparue pour {'aujourd’hui' if day == as_of else 'demain'}"
            for oid in missing:
                o = exp[oid]
                target.orders.append(AppTransportOrder(order_id=oid, article_id=o.article_id, supplier_id=o.supplier_id or "", ref=o.ref or "",
                                                       expected_date=o.expected_date, qty=_qty_of(o)))
                carried.add(oid)
                if not new:
                    counts["completed"] += 1
            session.flush()
            audit(session, user, "create" if new else "complete", "transport", target.transport_id, None,
                  {"supplier_id": sid, "date": day.isoformat(), "orders": list(missing), "auto": True})

    # ---- slots of the new trucks only : the trucks already placed never move ; a day at a time, every truck of the
    # day counts (shuttles, EV returns, one-off trucks included), the most constrained supplier first
    for day in sorted({t.date for t in born}):
        occ = Occupancy()
        for t in rows:
            if t.date == day and t.status == "planned" and t.time:
                occ.add(T.split_docks(t.docks), t.time)
        news = [t for t in born if t.date == day]
        news.sort(key=lambda t: (len(candidates_for(sups.get(t.supplier_id), grid)), t.supplier_id, t.transport_id))
        for t in news:
            docks = T.split_docks(t.docks) or ["B15"]
            closed = [d for d in docks if day in dock_closed.get(d.upper(), set())]
            time, full = place_spread(occ, candidates_for(sups.get(t.supplier_id), grid), docks, capacity)
            t.time = time
            occ.add(docks, time)
            reason = full or (f"quai {', '.join(closed)} fermé ce jour-là" if closed else "")
            if reason:
                t.check_reason = reason
    session.flush()
    changed = any(counts.values())
    if changed:
        audit(session, user, "reconcile", "transports", "", None, {**counts, "as_of": as_of.isoformat(), "start": start.isoformat(), "end": end.isoformat()})
        log.info("Camions automatiques : %s", ", ".join(f"{k} {v}" for k, v in counts.items() if v))
    return {**counts, "changed": changed}


def ensure_reconciled(ctx: Any, session: Session, ds: Dataset, params: EngineParams, as_of: dt.date, carriers: dict[str, list[str]],
                      dock_closed: dict[str, set[dt.date]]) -> bool:
    """Run the reconciliation **once per data version**, whatever the number of requests or worker processes : the
    version processed is kept in ``app_meta`` ; a run that changed something bumps the version and records the new
    number in the same transaction.  Returns whether anything changed."""
    v = ctx.sync_version(session)
    row = session.get(AppMeta, RECONCILED_KEY)
    if row is not None and row.value == str(v):
        return False
    with _RECONCILE_LOCK:
        if session.bind is not None and session.bind.dialect.name == "postgresql":   # pragma: no cover - needs PostgreSQL
            session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": RECONCILE_LOCK_KEY})
        session.expire_all()
        v = read_data_version(session)
        row = session.get(AppMeta, RECONCILED_KEY)
        if row is not None and row.value == str(v):
            return False
        res = reconcile(session, ds, params, as_of, carriers, dock_closed)
        if row is None:
            row = AppMeta(key=RECONCILED_KEY, value=str(v))
            session.add(row)
        if res["changed"]:
            ctx.commit(session, on_version=lambda nv: setattr(row, "value", str(nv)))
        else:
            row.value = str(v)
            session.commit()
        return bool(res["changed"])


# ----------------------------------------------------------------------------- planner actions
def move_transport(session: Session, user: str, t: AppTransport, params: EngineParams, date: dt.date | None = None, time: str | None = None,
                   docks: list[str] | None = None, force: bool = False) -> SlotCheck | None:
    """Day, hour or docks changed by the planner : the slot is checked (:func:`ensure_slot`), the truck is **pinned**
    (it keeps its orders when the ERP moves their date) and its « à vérifier » is cleared.  Returns the check."""
    if t.status != "planned":
        raise ActionError(f"{t.transport_id} est annulé")
    before = {"date": t.date.isoformat(), "time": t.time, "docks": T.split_docks(t.docks)}
    new_date = date if date is not None else t.date
    new_time = t.time
    if time is not None and time != t.time:
        if not T.TIME_RE.match(time):
            raise ActionError(f"Heure invalide : « {time} »")
        new_time = time
    new_docks = T.split_docks(t.docks)
    if docks is not None:
        clean = [d for d in dict.fromkeys(str(x).strip() for x in docks) if d]
        bad = [d for d in clean if d not in T.DOCKS]
        if bad or not clean:
            raise ActionError(f"Quai(s) inconnu(s) : « {', '.join(bad) or 'aucun'} »")
        new_docks = clean
    if new_date == t.date and new_time == t.time and new_docks == T.split_docks(t.docks):
        return None
    chk = ensure_slot(session, params, new_date, new_time, new_docks, force, exclude=t.transport_id)
    notes = []
    if new_date != t.date:
        notes.append(f"déplacé au {WEEKDAYS_FR[new_date.weekday()]} {new_date:%d/%m}")
    if new_time != t.time:
        notes.append(f"heure {new_time}")
    if new_docks != T.split_docks(t.docks):
        notes.append(f"quai {' / '.join(new_docks)}")
    t.date, t.time, t.docks = new_date, new_time, ";".join(new_docks)
    t.pinned = True
    t.check_reason = ""
    audit(session, user, "move", "transport", t.transport_id, None, {"before": before, "after": {"date": t.date.isoformat(), "time": t.time, "docks": new_docks},
                                                                      "notes": notes, "forced": bool(force and not chk.free)})
    session.flush()
    return chk


def split_transport(session: Session, user: str, t: AppTransport, items: list[dict[str, Any]], params: EngineParams, time: str | None = None,
                    docks: list[str] | None = None, force: bool = False) -> AppTransport:
    """Part of a truck leaves for a **second truck** of the same day (same number, next letter : ``EM00123A`` →
    ``EM00123B`` ; slot given, checked like any slot chosen by hand, or the nearest free one) ; both are pinned.
    ``items`` = ``[{order_id, qty?}]`` : an order without ``qty`` (or with its whole quantity) moves entirely ; a
    smaller ``qty`` splits the order, the rest stays on the first truck.  Every truck is splittable, a single-order one
    by quantity."""
    if t.status != "planned":
        raise ActionError(f"{t.transport_id} est annulé")
    by_id = {o.order_id: o for o in t.orders}
    moves: list[tuple[AppTransportOrder, float]] = []
    for it in items:
        o = by_id.get(str(it.get("order_id") or ""))
        if o is None:
            raise ActionError(f"Commande absente de ce camion : {it.get('order_id')}")
        total = float(o.qty or 0.0)
        qty = it.get("qty")
        qty = total if qty is None else float(qty)
        if qty < 0 or qty > total + 1e-9:
            raise ActionError(f"Quantité invalide pour {o.article_id} : {qty:g} (à bord : {total:g})")
        if qty > 0:            # a zero stays on the first truck : fine as long as another line moves
            moves.append((o, min(qty, total)))
    if not moves:
        raise ActionError("Aucune commande à scinder : au moins un article doit avoir une quantité")
    if len(moves) == len(t.orders) and all(abs(q - float(o.qty or 0.0)) < 1e-9 for o, q in moves):
        raise ActionError("Tout partirait dans le second camion : il resterait un camion vide, déplacez-le plutôt")
    new_docks = [d for d in dict.fromkeys(str(x).strip() for x in (docks or T.split_docks(t.docks))) if d] or ["B15"]
    bad = [d for d in new_docks if d not in T.DOCKS]
    if bad:
        raise ActionError(f"Quai(s) inconnu(s) : « {', '.join(bad)} »")
    if time is None:
        occ = Occupancy()
        for other in T.list_transports(session, t.date, t.date):
            occ.add(T.split_docks(other.docks), other.time)
        time, _full = place(occ, T.day_slots(), new_docks, t.time, None, capacity_of(params))
    else:
        if not T.TIME_RE.match(time):
            raise ActionError(f"Heure invalide : « {time} »")
        ensure_slot(session, params, t.date, time, new_docks, force)
    new = AppTransport(transport_id=T.next_in_family(session, t.transport_id), supplier_id=t.supplier_id, date=t.date, time=time, docks=";".join(new_docks),
                       carrier=t.carrier or "", note="", mode=t.mode, status="planned", created_by=user, pinned=True, check_reason="")
    moved: list[dict[str, Any]] = []
    for o, qty in moves:
        whole = abs(qty - float(o.qty or 0.0)) < 1e-9
        if whole:
            t.orders.remove(o)
        else:
            o.qty = float(o.qty or 0.0) - qty
        new.orders.append(AppTransportOrder(order_id=o.order_id, article_id=o.article_id, supplier_id=o.supplier_id or "", ref=o.ref or "",
                                            expected_date=o.expected_date, qty=qty))
        moved.append({"order_id": o.order_id, "qty": qty, "whole": whole})
    t.pinned = True
    session.add(new)
    session.flush()
    audit(session, user, "split", "transport", t.transport_id, None, {"new": new.transport_id, "orders": moved, "time": time, "docks": new_docks})
    return new


def sibling_of(session: Session, t: AppTransport) -> AppTransport | None:
    """The other planned truck of the same family (same number) **the same day** : the letter just before, else the
    letter just after.  ``None`` for a truck alone, of the former identifier format, or whose family rides other
    days."""
    fam = T.family_of(t.transport_id)
    if fam is None:
        return None
    rows = [x for x in session.scalars(select(AppTransport).where(AppTransport.transport_id.like(f"{fam}%"), AppTransport.date == t.date,
                                                                   AppTransport.status == "planned")).all()
            if x.transport_id != t.transport_id and T.family_of(x.transport_id) == fam and x.supplier_id == t.supplier_id]
    if not rows:
        return None
    me = T.letter_rank(T.ID_RE.match(t.transport_id).group(2))
    rank = {x.transport_id: T.letter_rank(T.ID_RE.match(x.transport_id).group(2)) for x in rows}
    before = [x for x in rows if rank[x.transport_id] < me]
    after = [x for x in rows if rank[x.transport_id] > me]
    return max(before, key=lambda x: rank[x.transport_id]) if before else min(after, key=lambda x: rank[x.transport_id])


def _take_back(session: Session, user: str, t: AppTransport, into: AppTransport, cap: dict[str, float] | None) -> list[dict[str, Any]]:
    """The orders of ``t`` ride ``into`` : a quantity split joins back (``70 + 30``), never beyond the ERP open
    quantity (``cap``) when the same order already rides the other truck entirely."""
    mine = {o.order_id: o for o in into.orders}
    moved: list[dict[str, Any]] = []
    for o in list(t.orders):
        qty = float(o.qty or 0.0)
        have = mine.get(o.order_id)
        if have is None:
            into.orders.append(AppTransportOrder(order_id=o.order_id, article_id=o.article_id, supplier_id=o.supplier_id or "", ref=o.ref or "",
                                                 expected_date=o.expected_date, qty=qty))
        else:
            total = float(have.qty or 0.0) + qty
            limit = (cap or {}).get(o.order_id)
            have.qty = min(total, limit) if limit is not None and limit > 0 else total
        moved.append({"order_id": o.order_id, "qty": qty})
    into.pinned = True
    session.flush()
    audit(session, user, "take_back", "transport", into.transport_id, None, {"from": t.transport_id, "orders": moved})
    return moved


def cancel_transport(session: Session, user: str, t: AppTransport, cap: dict[str, float] | None = None) -> AppTransport | None:
    """Cancel by the planner.  A truck of a split family whose sibling runs the same day **gives its orders back**
    to the sibling (returned) ; otherwise a supplier truck leaves a suppressed day (no rebirth without a new
    order).  ``cap`` = ERP open quantity per order (a quantity never grows beyond it when joining back)."""
    if t.status == "cancelled":
        return None
    into = sibling_of(session, t) if t.orders else None
    if into is not None:
        _take_back(session, user, t, into, cap)
    elif _carries_orders(t):
        row = session.get(AppTransportSuppressed, (t.supplier_id, t.date))
        ids = sorted({o.order_id for o in t.orders} | set(json.loads(row.orders_json or "[]") if row else []))
        if row is None:
            row = AppTransportSuppressed(supplier_id=t.supplier_id, date=t.date)
            session.add(row)
        row.orders_json, row.cancelled_by, row.cancelled_at = json.dumps(ids), user, utcnow()
    T.cancel_transport(session, user, t)
    return into


def restore_transport(session: Session, user: str, t: AppTransport, params: EngineParams, force: bool = False) -> SlotCheck:
    """A cancelled truck comes back as it was (identifier, day, slot, docks), through the occupied-slot check.  An
    order that already rides another planned truck of the supplier that day (given back at the cancellation) stays
    there : a truck whose every order left is refused.  The suppressed day goes (the reconciliation may complete the
    truck again)."""
    if t.status != "cancelled":
        raise ActionError(f"{t.transport_id} n'est pas annulé")
    elsewhere = {o.order_id for x in T.list_transports(session, t.date, t.date, [t.supplier_id]) for o in x.orders}
    kept = [o for o in t.orders if o.order_id not in elsewhere]
    if t.orders and not kept:
        raise ActionError(f"{t.transport_id} : ses commandes roulent déjà sur un autre camion du jour")
    chk = ensure_slot(session, params, t.date, t.time, T.split_docks(t.docks), force)
    for o in list(t.orders):
        if o.order_id in elsewhere:
            t.orders.remove(o)
    t.status, t.cancelled_by, t.cancelled_at = "planned", "", None
    t.pinned = True
    row = session.get(AppTransportSuppressed, (t.supplier_id, t.date))
    if row is not None:
        session.delete(row)
    session.flush()
    audit(session, user, "restore", "transport", t.transport_id, None, {"supplier_id": t.supplier_id, "date": t.date.isoformat(), "forced": bool(force and not chk.free)})
    return chk


def set_note(session: Session, user: str, t: AppTransport, note: str) -> None:
    t.note = (note or "").strip()[:500]
    audit(session, user, "note", "transport", t.transport_id, None, {"note": t.note})
    session.flush()


def clear_check(session: Session, user: str, t: AppTransport) -> None:
    """« Vu » : the planner looked at the truck to check."""
    t.check_reason = ""
    audit(session, user, "seen", "transport", t.transport_id, None, {})
    session.flush()


# ----------------------------------------------------------------------------- docks × slots
def dock_day(session: Session, ds: Dataset, params: EngineParams, day: dt.date, dock_closed: dict[str, set[dt.date]] | None = None) -> dict[str, Any]:
    """Who comes when : every planned truck of the day (shuttles included) on the docks × half-hour slots of the day
    (06:00 → 21:00) ; the automatic grid is given apart."""
    dock_closed = dock_closed or {}
    grid = T.day_slots()
    names = {s.supplier_id: s.name for s in ds.suppliers}
    docks = {d: {"dock": d, "closed": day in dock_closed.get(d.upper(), set()), "slots": {s: [] for s in grid}} for d in T.DOCKS}
    off: list[dict[str, Any]] = []
    for t in T.list_transports(session, day, day):
        item = {"transport_id": t.transport_id, "supplier_id": t.supplier_id, "supplier_name": T.MAF_LABEL if t.supplier_id == T.MAF_ID else names.get(t.supplier_id, ""),
                "mode": t.mode or "fournisseur", "check_reason": t.check_reason or "", "docks": T.split_docks(t.docks), "time": t.time}
        slot = slot_of(t.time, grid)
        if slot is None:
            off.append(item)
            continue
        for d in T.split_docks(t.docks):
            if d in docks:
                docks[d]["slots"][slot].append(item)
    return {"date": day, "slots": grid, "auto_slots": slot_grid(params), "capacity": capacity_of(params), "hard_max": capacity_of(params) * FORCE_FACTOR,
            "docks": list(docks.values()), "off_grid": off}
