"""Automatic trucks (« Camions automatiques », docs/transports_automatiques.md, docs/regles_metier.md § 9 ter bis).

For the suppliers whose reference row says **Camions automatiques** (``ref_suppliers.transport_auto``, modes
*Fournisseur* and *carrier*), the planner creates nothing any more : at every change of the data version the
**reconciliation** (:func:`reconcile`) compares, for every working day of the two planning weeks (the current one
and the next), the firm ERP orders expected from the supplier with the trucks that exist :

* a day with at least one firm order and no truck gets **one truck** (``origin = auto``) carrying every order of
  the day ; a new order of a day that already has its truck **rides it** ; an order that leaves a day (date changed
  in the ERP, closed, deleted) leaves the truck, and an empty truck is **cancelled** (the EM number is never reused) ;
* the **scheduler** gives each truck a dock slot (every ``dock_slot_minutes`` from ``dock_slot_start`` to
  ``dock_slot_end``, ``dock_slot_capacity`` trucks per dock and slot) : the trucks **touched by a planner**
  (``pinned``) and the manual trucks (MAF shuttles, EV returns, one-off trucks) keep their slot ; the others are
  placed by supplier code, at the supplier's **habitual hour** (the time of his latest truck) when every dock of the
  truck has room, else on the nearest free slot ; no room at all → last slot and **à vérifier** ; a closed dock, an
  order appearing for today or tomorrow → **à vérifier** too.

The application only creates, changes and cancels the trucks : the notices to the suppliers and carriers are sent
by the transport tool (ATR) from the CSV, never by the application.

Today's trucks are on the road : the reconciliation adds a truck or an order for today (à vérifier) but never removes
anything from today ; past days are frozen.  The planner keeps a one-click menu on each truck : copy the identifier,
split, move (day, hour, docks), note, cancel, « vu » ; a cancelled automatic truck leaves a **suppressed day**
(``app_transport_days_suppressed``) : a truck is born there again only when an order that was not in the cancelled
truck appears.  Cancelling one truck of a **split family** (``EM00123B`` while ``EM00123A`` runs the same day) gives
its orders back to the other truck.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import threading
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
from ..engine.models import Dataset, EngineParams
from . import transports as T

log = logging.getLogger(__name__)

PLANNING_WEEKS = 2                      # the current week and the next : nothing is planned beyond
RECONCILED_KEY = "transports_reconciled_version"   # app_meta : the data version the last reconciliation processed
RECONCILE_LOCK_KEY = 7_419_302          # pg_advisory_xact_lock : one reconciliation at a time across the workers
AUTO, MANUAL = "auto", "manuel"
APP_USER = "application"
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
    """``07:00, 07:30, …, 15:30`` : the delivery slots of a day (``dock_slot_*`` rules)."""
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


class Occupancy:
    """Trucks per (dock, slot) of one day."""

    def __init__(self) -> None:
        self.count: dict[tuple[str, str], int] = {}

    def add(self, docks: list[str], time: str, grid: list[str]) -> None:
        slot = slot_of(time, grid)
        if slot is None:
            return
        for d in docks:
            self.count[(d.upper(), slot)] = self.count.get((d.upper(), slot), 0) + 1

    def free(self, docks: list[str], slot: str, capacity: int) -> bool:
        return all(self.count.get((d.upper(), slot), 0) < capacity for d in docks)


def place(occ: Occupancy, grid: list[str], docks: list[str], preferred: str | None, current: str | None, capacity: int) -> tuple[str, str]:
    """The slot of a truck : its current one when it is still free, else the habitual hour of the supplier, else
    the nearest free slot (before or after) ; no room anywhere → the last slot and the reason « à vérifier »."""
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


# ----------------------------------------------------------------------------- helpers
def _not_shuttle(t: AppTransport) -> bool:
    return (t.mode or "") != "maf" and t.supplier_id != T.MAF_ID


def _carries_orders(t: AppTransport) -> bool:
    """A truck the reconciliation reasons about : a supplier truck (not an EV return nor a one-off truck)."""
    return _not_shuttle(t) and (t.mode or "") not in T.NO_ORDER_MODES


def _qty_of(o: Any) -> float:
    return float(o.qty_open if o.qty_open is not None else o.qty_ordered)


# ----------------------------------------------------------------------------- reconciliation
def reconcile(session: Session, ds: Dataset, params: EngineParams, as_of: dt.date, carriers: dict[str, list[str]] | None = None,
              dock_closed: dict[str, set[dt.date]] | None = None, user: str = APP_USER) -> dict[str, Any]:
    """Expected state ↔ existing trucks of the automatic suppliers over the two planning weeks, then the dock
    slots of every movable truck (module docstring).  Does not commit.  Returns the counts (``created``,
    ``completed``, ``removed``, ``cancelled``, ``moved``) and ``changed``."""
    dock_closed = dock_closed or {}
    cal = WorkCalendar.from_spec(params.working_weekdays, holidays=ds.holidays, extra_open=ds.open_days)
    start = iso_week_monday(as_of)
    end = start + dt.timedelta(days=7 * PLANNING_WEEKS - 1)
    days = [start + dt.timedelta(days=i) for i in range((end - start).days + 1) if cal.is_working_day(start + dt.timedelta(days=i))]
    grid = slot_grid(params)
    capacity = max(int(params.dock_slot_capacity or 1), 1)
    sups = {s.supplier_id: s for s in ds.suppliers}
    auto = {sid: s for sid, s in sups.items() if s.active and s.transport_auto and T.mode_kind(s.transport_mode) in ("fournisseur", "transporteur")}
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
    others = [t for t in rows if not _carries_orders(t)]                        # shuttles, EV returns, one-off trucks : fixed
    suppressed = {(s.supplier_id, s.date): s for s in session.scalars(select(AppTransportSuppressed).where(AppTransportSuppressed.date >= start)).all()}
    by_day: dict[tuple[str, dt.date], list[AppTransport]] = {}
    for t in trucks_all:
        if t.date >= start:
            by_day.setdefault((t.supplier_id, t.date), []).append(t)
    habit: dict[str, str] = {}      # supplier → time of his most recent truck that already has one
    for t in sorted(trucks_all, key=lambda x: (x.date, x.created_at or dt.datetime.min), reverse=True):
        if t.time and t.supplier_id not in habit:
            habit[t.supplier_id] = t.time
    counts = {"created": 0, "completed": 0, "removed": 0, "cancelled": 0, "moved": 0}
    tomorrow = cal.next_working_day(as_of, inclusive=False)

    for sid in sorted(auto):
        sp = auto[sid]
        wanted = {oid: o for (s_id, _d), orders in expected.items() if s_id == sid for oid, o in orders.items()}
        mine = [t for (s_id, d), ts in by_day.items() if s_id == sid for t in ts if t.status == "planned"]
        rides: dict[str, int] = {}
        for t in mine:
            for o in t.orders:
                rides[o.order_id] = rides.get(o.order_id, 0) + 1
        # an order that left the firm orders leaves its automatic truck ; an order whose ERP date moved leaves a truck
        # the planner did not pin (it rides its new day's truck below) ; today's trucks are on the road : untouched
        for t in mine:
            if not t.is_auto or t.date <= as_of:
                continue
            for o in list(t.orders):
                want = wanted.get(o.order_id)
                if want is None or (not t.pinned and want.expected_date != t.date):
                    t.orders.remove(o)
                    counts["removed"] += 1
                elif rides.get(o.order_id, 1) == 1 and abs(_qty_of(want) - float(o.qty or 0.0)) > 1e-9:
                    o.qty = _qty_of(want)      # an order split over several trucks keeps the planner's quantities
            if not t.orders:
                t.status = "cancelled"
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
            target = next((t for t in trucks if t.is_auto and not t.pinned), None) or next((t for t in trucks if t.is_auto), None)
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
                                      origin=AUTO, pinned=False, check_reason="")
                session.add(target)
                by_day.setdefault((sid, day), []).append(target)
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

    # ---- dock slots : fixed trucks first (manual, pinned, shuttles, EV returns, one-off trucks), then the movable ones by supplier code
    for day in days:
        if day < as_of:
            continue
        day_trucks = [t for (sid, d), ts in by_day.items() if d == day for t in ts if t.status == "planned"]
        movable = [t for t in day_trucks if t.is_auto and not t.pinned]
        occ = Occupancy()
        for t in day_trucks:
            if t not in movable:
                occ.add(T.split_docks(t.docks), t.time, grid)
        for t in others:
            if t.date == day:
                occ.add(T.split_docks(t.docks), t.time, grid)
        for t in sorted(movable, key=lambda x: (x.supplier_id, x.transport_id)):
            docks = T.split_docks(t.docks) or ["B15"]
            closed = [d for d in docks if day in dock_closed.get(d.upper(), set())]
            time, full = place(occ, grid, docks, habit.get(t.supplier_id), t.time, capacity)
            if time != t.time:
                if t.time:
                    counts["moved"] += 1
                t.time = time
            habit.setdefault(t.supplier_id, time)
            occ.add(docks, time, grid)
            reason = full or (f"quai {', '.join(closed)} fermé ce jour-là" if closed else "")
            if reason:
                t.check_reason = reason
            elif t.check_reason and ("complet" in t.check_reason or t.check_reason.startswith("quai ")):
                t.check_reason = ""
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
class ActionError(ValueError):
    pass


def move_transport(session: Session, user: str, t: AppTransport, date: dt.date | None = None, time: str | None = None, docks: list[str] | None = None) -> None:
    """Day, hour or docks changed by the planner : the truck is **pinned** (the scheduler never moves it again) and
    its « à vérifier » is cleared."""
    if t.status != "planned":
        raise ActionError(f"{t.transport_id} est annulé")
    before = {"date": t.date.isoformat(), "time": t.time, "docks": T.split_docks(t.docks)}
    notes = []
    if date is not None and date != t.date:
        t.date = date
        notes.append(f"déplacé au {WEEKDAYS_FR[date.weekday()]} {date:%d/%m}")
    if time is not None and time != t.time:
        if not T.TIME_RE.match(time):
            raise ActionError(f"Heure invalide : « {time} »")
        t.time = time
        notes.append(f"heure {time}")
    if docks is not None:
        clean = [d for d in dict.fromkeys(str(x).strip() for x in docks) if d]
        bad = [d for d in clean if d not in T.DOCKS]
        if bad or not clean:
            raise ActionError(f"Quai(s) inconnu(s) : « {', '.join(bad) or 'aucun'} »")
        if ";".join(clean) != (t.docks or ""):
            t.docks = ";".join(clean)
            notes.append(f"quai {' / '.join(clean)}")
    if not notes:
        return
    t.pinned = True
    t.check_reason = ""
    audit(session, user, "move", "transport", t.transport_id, None, {"before": before, "after": {"date": t.date.isoformat(), "time": t.time, "docks": T.split_docks(t.docks)}, "notes": notes})
    session.flush()


def split_transport(session: Session, user: str, t: AppTransport, items: list[dict[str, Any]], params: EngineParams, time: str | None = None,
                    docks: list[str] | None = None, dock_closed: dict[str, set[dt.date]] | None = None) -> AppTransport:
    """Part of a truck leaves for a **second truck** of the same day (same number, next letter : ``EM00123A`` →
    ``EM00123B`` ; slot given or the nearest free one) ;
    both are pinned.  ``items`` = ``[{order_id, qty?}]`` : an order without ``qty`` (or with its whole quantity)
    moves entirely ; a smaller ``qty`` splits the order, the rest stays on the first truck.  Every truck is
    splittable, a single-order one by quantity."""
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
        if qty <= 0 or qty > total + 1e-9:
            raise ActionError(f"Quantité invalide pour {o.ref or o.order_id} : {qty:g} (à bord : {total:g})")
        moves.append((o, min(qty, total)))
    if not moves:
        raise ActionError("Aucune commande à scinder")
    if len(moves) == len(t.orders) and all(abs(q - float(o.qty or 0.0)) < 1e-9 for o, q in moves):
        raise ActionError("Tout partirait dans le second camion : il resterait un camion vide, déplacez-le plutôt")
    new_docks = [d for d in dict.fromkeys(str(x).strip() for x in (docks or T.split_docks(t.docks))) if d] or ["B15"]
    bad = [d for d in new_docks if d not in T.DOCKS]
    if bad:
        raise ActionError(f"Quai(s) inconnu(s) : « {', '.join(bad)} »")
    if time is None:
        grid = slot_grid(params)
        occ = Occupancy()
        for other in T.list_transports(session, t.date, t.date):
            occ.add(T.split_docks(other.docks), other.time, grid)
        time, _full = place(occ, grid, new_docks, t.time, None, max(int(params.dock_slot_capacity or 1), 1))
    elif not T.TIME_RE.match(time):
        raise ActionError(f"Heure invalide : « {time} »")
    new = AppTransport(transport_id=T.next_in_family(session, t.transport_id), supplier_id=t.supplier_id, date=t.date, time=time, docks=";".join(new_docks),
                       carrier=t.carrier or "", note="", mode=t.mode, status="planned", created_by=user, origin=t.origin or MANUAL, pinned=True, check_reason="")
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
    to the sibling (returned) ; otherwise an automatic truck leaves a suppressed day (no rebirth without a new
    order).  ``cap`` = ERP open quantity per order (a quantity never grows beyond it when joining back)."""
    if t.status == "cancelled":
        return None
    into = sibling_of(session, t) if t.orders else None
    if into is not None:
        _take_back(session, user, t, into, cap)
    elif t.is_auto:
        row = session.get(AppTransportSuppressed, (t.supplier_id, t.date))
        ids = sorted({o.order_id for o in t.orders} | set(json.loads(row.orders_json or "[]") if row else []))
        if row is None:
            row = AppTransportSuppressed(supplier_id=t.supplier_id, date=t.date)
            session.add(row)
        row.orders_json, row.cancelled_by, row.cancelled_at = json.dumps(ids), user, utcnow()
    T.cancel_transport(session, user, t)
    return into


def set_note(session: Session, user: str, t: AppTransport, note: str) -> None:
    t.note = (note or "").strip()[:500]
    t.pinned = bool(t.pinned) or False
    audit(session, user, "note", "transport", t.transport_id, None, {"note": t.note})
    session.flush()


def clear_check(session: Session, user: str, t: AppTransport) -> None:
    """« Vu » : the planner looked at the truck to check."""
    t.check_reason = ""
    audit(session, user, "seen", "transport", t.transport_id, None, {})
    session.flush()


# ----------------------------------------------------------------------------- docks × slots
def dock_day(session: Session, ds: Dataset, params: EngineParams, day: dt.date, dock_closed: dict[str, set[dt.date]] | None = None) -> dict[str, Any]:
    """Who comes when : every planned truck of the day (shuttles included) on the docks × slots grid."""
    dock_closed = dock_closed or {}
    grid = slot_grid(params)
    names = {s.supplier_id: s.name for s in ds.suppliers}
    docks = {d: {"dock": d, "closed": day in dock_closed.get(d.upper(), set()), "slots": {s: [] for s in grid}} for d in T.DOCKS}
    off: list[dict[str, Any]] = []
    for t in T.list_transports(session, day, day):
        item = {"transport_id": t.transport_id, "supplier_id": t.supplier_id, "supplier_name": T.MAF_LABEL if t.supplier_id == T.MAF_ID else names.get(t.supplier_id, ""),
                "origin": t.origin or MANUAL, "mode": t.mode or "fournisseur", "check_reason": t.check_reason or "", "docks": T.split_docks(t.docks)}
        slot = slot_of(t.time, grid)
        if slot is None:
            off.append(item)
            continue
        for d in T.split_docks(t.docks):
            if d in docks:
                docks[d]["slots"][slot].append(item)
    return {"date": day, "slots": grid, "capacity": max(int(params.dock_slot_capacity or 1), 1), "docks": list(docks.values()), "off_grid": off}
