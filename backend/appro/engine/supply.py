"""Supply flows of one article, per supplier **lane**: the ERP scenario (firm orders as is), the
plan scenario (the planner's plan cells, else the ERP), the receipts and the backlog.

Rules (docs/regles_metier.md § 3):

* an ERP **order** is a delivery slot ``supplier|article|date|firm`` ; the ERP never moves nor
  cancels a slot and its remaining quantity is unreliable once the date is past ;
* the **ERP scenario** counts the open firm orders dated on or after the reference day, except the
  days the planner **ignored** (a click on the Ferme cell: ``order_ignored`` flag) ;
* the **plan scenario** counts, for each supplier and day, the typed plan cell when there is one,
  else the firm ERP quantity of the day (ignored days excluded).  A cell dated before the
  reference day has expired ;
* on the **reference day** nothing links a receipt to an order: the quantity still expected from
  the firm orders of a supplier is ``max(0, open − receipts of the day)`` (a typed cell is taken as
  is, the planner knows) ;
* the **backlog** of a supplier is ``max(0, Σ ordered firm quantity − Σ received)`` over the last
  ``backlog_days`` before the reference day: no per-order matching, no action to take, it ages out.

For display the Ferme row also carries, per day, the **ordered** and **ERP remaining** quantities
of the firm orders whatever their date (``orders_firm_ordered`` / ``orders_firm_open``): the cell
shows the ordered quantity, or ``remaining / ordered`` when a partial delivery took place.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .calendar import iso_week_monday
from .demand import DayIndex
from .models import (
    CellFlag,
    DesadvInfo,
    DesadvLine,
    EngineParams,
    Lane,
    OrderInfo,
    OrderLine,
    PlanCell,
    Receipt,
    ReceiptInfo,
    SupplierLink,
)

EPS = 1e-9


@dataclass
class _LaneAcc:
    supplier_id: str | None
    n: int
    orders_firm: np.ndarray = field(init=False)
    orders_firm_hist: np.ndarray = field(init=False)
    orders_forecast: np.ndarray = field(init=False)
    receipts: np.ndarray = field(init=False)
    plan: np.ndarray = field(init=False)
    plan_typed: np.ndarray = field(init=False)
    ordered: np.ndarray = field(init=False)
    open: np.ndarray = field(init=False)
    ignored: np.ndarray = field(init=False)
    desadv_open: np.ndarray = field(init=False)
    desadv_ko: np.ndarray = field(init=False)
    receipts_ko: np.ndarray = field(init=False)
    backlog_ordered: float = 0.0
    backlog_received: float = 0.0
    orders: list[OrderInfo] = field(default_factory=list)
    desadv: list[DesadvInfo] = field(default_factory=list)
    receipt_lines: list[ReceiptInfo] = field(default_factory=list)

    def __post_init__(self) -> None:
        n = self.n
        self.orders_firm, self.orders_firm_hist, self.orders_forecast = np.zeros(n), np.zeros(n), np.zeros(n)
        self.receipts, self.plan, self.ordered, self.open = np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n)
        self.plan_typed, self.ignored = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)
        self.desadv_open = np.zeros(n)
        self.desadv_ko, self.receipts_ko = np.zeros(n, dtype=bool), np.zeros(n, dtype=bool)


def lane_ids(links: list[SupplierLink], orders: list[OrderLine], receipts: list[Receipt], cells: list[PlanCell],
             as_of: dt.date, desadv: list[DesadvLine] | None = None) -> list[str | None]:
    """Suppliers shown as lanes: the active links (priority order), then any other supplier that
    appears in the orders, receipts, plan cells or despatch advices ; a single anonymous lane when
    there is none."""
    ids: list[str | None] = [l.supplier_id for l in sorted(links, key=lambda l: (l.priority, l.supplier_id)) if l.active]
    extra = ({o.supplier_id for o in orders} | {r.supplier_id for r in receipts} | {c.supplier_id for c in cells if c.date >= as_of}
             | {d.supplier_id for d in (desadv or ())})
    for sid in sorted((s for s in extra if s and s not in ids)):
        ids.append(sid)
    if None in extra and not ids:
        ids.append(None)
    return ids or [None]


def build_lanes(links: list[SupplierLink], orders: list[OrderLine], receipts: list[Receipt], cells: list[PlanCell],
                supplier_names: dict[str, str], index: DayIndex, as_of: dt.date, params: EngineParams,
                flags: list[CellFlag] | None = None, desadv: list[DesadvLine] | None = None) -> list[Lane]:
    n = index.n
    ids = lane_ids(links, orders, receipts, cells, as_of, desadv)
    acc = {sid: _LaneAcc(sid, n) for sid in ids}
    fallback = ids[0]

    def lane_of(sid: str | None) -> _LaneAcc:
        return acc.get(sid) or acc[fallback]

    def add(arr: np.ndarray, day: dt.date, qty: float) -> None:
        i = index.offset(day)
        if i is not None:
            arr[i] += qty

    # ---- ignored firm days (planner clicks), on/after the reference day only
    for f in flags or ():
        if f.kind == "order_ignored" and f.date >= as_of:
            i = index.offset(f.date)
            if i is not None:
                lane_of(f.supplier_id).ignored[i] = True

    window_start = max(as_of - dt.timedelta(days=max(int(params.backlog_days), 0)), index.start)  # never before the point zero
    # ---- receipts
    for r in receipts:
        if r.qty == 0:
            continue
        ln = lane_of(r.supplier_id)
        add(ln.receipts, r.receipt_date, r.qty)
        if window_start <= r.receipt_date < as_of:
            ln.backlog_received += r.qty
    # ---- ERP orders
    received_today: dict[str | None, float] = defaultdict(float)
    for r in receipts:
        if r.receipt_date == as_of:
            received_today[lane_of(r.supplier_id).supplier_id] += r.qty
    for o in sorted(orders, key=lambda o: (o.expected_date, o.order_id)):
        ln = lane_of(o.supplier_id)
        typ = o.order_type.value
        firm = typ in params.firm_sources
        day = o.expected_date
        if not firm and params.forecast_date_policy == "week_monday":
            day = iso_week_monday(day)
        i = index.offset(day)
        ignored = bool(firm and i is not None and ln.ignored[i])
        ln.orders.append(OrderInfo(o.order_id, o.supplier_id, typ, day, float(o.qty_ordered), float(o.qty_open or 0.0),
                                   o.ref, ignored))
        if firm:
            add(ln.ordered, day, float(o.qty_ordered))
            add(ln.open, day, float(o.qty_open or 0.0))
            if day < as_of:
                add(ln.orders_firm_hist, day, float(o.qty_ordered))
                if day >= window_start:
                    ln.backlog_ordered += float(o.qty_ordered)
            elif o.qty_open and o.qty_open > EPS and not ignored:
                add(ln.orders_firm, day, float(o.qty_open))
        elif o.qty_open and o.qty_open > EPS:
            add(ln.orders_forecast, day, float(o.qty_open))
    # reference day: receipts of the day already cover part of the firm orders of the day
    i0 = index.offset(as_of)
    if i0 is not None:
        for ln in acc.values():
            ln.orders_firm[i0] = max(0.0, ln.orders_firm[i0] - received_today.get(ln.supplier_id, 0.0))
    # ---- despatch advices (DESADV): announced, matched to the receipts by delivery note (BL)
    hidden_days = {(f.supplier_id or None, f.date) for f in (flags or ()) if f.kind == "desadv_hidden"}
    received_bls = {r.packing_slip.strip() for r in receipts if r.packing_slip and r.packing_slip.strip()}
    processed_bls: set[str] = set()
    for d in desadv or ():
        if d.state.strip().lower() == "annulé":
            continue
        bl = d.packing_slip.strip()
        ln = lane_of(d.supplier_id)
        received = bl in received_bls
        hidden = (ln.supplier_id, d.issue_date) in hidden_days
        if d.processed and bl:
            processed_bls.add(bl)
        ln.desadv.append(DesadvInfo(d.desadv_id, bl, d.issue_date, float(d.qty), d.state, d.final_processing,
                                    d.processed, received, hidden, d.purch_id))
        if received or hidden:
            continue
        i = index.offset(d.issue_date)
        if i is not None:
            ln.desadv_open[i] += float(d.qty)
            if not d.processed:
                ln.desadv_ko[i] = True
    for r in sorted(receipts, key=lambda r: (r.receipt_date, r.receipt_id)):
        i = index.offset(r.receipt_date)
        if not r.qty or i is None:                           # nothing exists before the point zero
            continue
        bl = (r.packing_slip or "").strip()
        processed = bool(bl) and bl in processed_bls         # an empty BL (« ACR non validé ») never matches
        ln = lane_of(r.supplier_id)
        ln.receipt_lines.append(ReceiptInfo(r.receipt_id, r.receipt_date, float(r.qty), bl, r.ref or "", processed))
        if not processed:
            ln.receipts_ko[i] = True
    # ---- plan: typed cells, else the ERP
    for ln in acc.values():
        ln.plan[:] = ln.orders_firm
    for c in cells:
        if c.date < as_of:
            continue
        i = index.offset(c.date)
        if i is None:
            continue
        ln = lane_of(c.supplier_id)
        ln.plan[i] = max(0.0, float(c.qty))
        ln.plan_typed[i] = True
    return [Lane(supplier_id=ln.supplier_id, name=supplier_names.get(ln.supplier_id or "", ln.supplier_id or ""),
                 orders_firm=ln.orders_firm, orders_firm_hist=ln.orders_firm_hist, orders_forecast=ln.orders_forecast,
                 receipts=ln.receipts, plan=ln.plan, supply_proposed=np.zeros(n), plan_typed=ln.plan_typed,
                 desadv_open=ln.desadv_open, desadv_ko=ln.desadv_ko, receipts_ko=ln.receipts_ko, desadv=ln.desadv, receipt_lines=ln.receipt_lines,
                 orders_firm_ordered=ln.ordered, orders_firm_open=ln.open, orders_ignored=ln.ignored,
                 backlog_ordered=ln.backlog_ordered, backlog_received=ln.backlog_received,
                 backlog_qty=max(0.0, ln.backlog_ordered - ln.backlog_received), orders=ln.orders)
            for ln in acc.values()]


def lane_totals(lanes: list[Lane], key: str) -> np.ndarray:
    if not lanes:
        return np.zeros(0)
    out = np.zeros_like(getattr(lanes[0], key), dtype=float)
    for l in lanes:
        out += getattr(l, key)
    return out


def blocked_windows(flags: list[CellFlag] | None, article_id: str) -> dict[str | None, list[tuple[dt.date, dt.date]]]:
    """Days where no CBN proposal may be placed, per supplier: from each refused proposal to the
    Sunday of its week.  A refusal without supplier (key ``None``) applies to every supplier."""
    out: dict[str | None, list[tuple[dt.date, dt.date]]] = {}
    for f in flags or ():
        if f.kind == "proposal_refused" and f.article_id == article_id:
            out.setdefault(f.supplier_id or None, []).append((f.date, iso_week_monday(f.date) + dt.timedelta(days=6)))
    return out
