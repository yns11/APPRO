"""Supply flows of one article, per supplier **lane**: the ERP scenario (firm orders as is) and the
plan scenario (the planner's plan cells, else the ERP), the receipts and the backlog.

Rules (docs/regles_metier.md § 3):

* an ERP **order** is a delivery slot ``supplier|article|date|firm`` ; the ERP never moves nor
  cancels a slot and its remaining quantity is unreliable once the date is past ;
* the **ERP scenario** counts the open firm orders dated on or after the reference day ;
* the **plan scenario** counts, for each supplier and day, the typed plan cell when there is one,
  else the firm ERP quantity of the day.  A cell dated before the reference day has expired ;
* on the **reference day** nothing links a receipt to an order: the quantity still expected from
  the firm orders of a supplier is ``max(0, open − receipts of the day)`` (a typed cell is taken as
  is, the planner knows) ;
* the **backlog** of a supplier is ``max(0, Σ ordered firm quantity − Σ received)`` over the last
  ``backlog_days`` before the reference day: no per-order matching, no action to take, it ages out.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .calendar import iso_week_monday
from .demand import DayIndex
from .models import EngineParams, Lane, OrderInfo, OrderLine, PlanCell, Receipt, SupplierLink

EPS = 1e-9


@dataclass
class _LaneAcc:
    supplier_id: str | None
    orders_firm: np.ndarray
    orders_firm_hist: np.ndarray
    orders_forecast: np.ndarray
    receipts: np.ndarray
    plan: np.ndarray
    plan_typed: np.ndarray
    backlog_ordered: float = 0.0
    backlog_received: float = 0.0
    orders: list[OrderInfo] = field(default_factory=list)


def lane_ids(links: list[SupplierLink], orders: list[OrderLine], receipts: list[Receipt], cells: list[PlanCell],
             as_of: dt.date) -> list[str | None]:
    """Suppliers shown as lanes: the active links (priority order), then any other supplier that
    appears in the orders, receipts or plan cells ; a single anonymous lane when there is none."""
    ids: list[str | None] = [l.supplier_id for l in sorted(links, key=lambda l: (l.priority, l.supplier_id)) if l.active]
    extra = {o.supplier_id for o in orders} | {r.supplier_id for r in receipts} | {c.supplier_id for c in cells if c.date >= as_of}
    for sid in sorted((s for s in extra if s and s not in ids)):
        ids.append(sid)
    if None in extra and not ids:
        ids.append(None)
    return ids or [None]


def build_lanes(links: list[SupplierLink], orders: list[OrderLine], receipts: list[Receipt], cells: list[PlanCell],
                supplier_names: dict[str, str], index: DayIndex, as_of: dt.date, params: EngineParams) -> list[Lane]:
    n = index.n
    ids = lane_ids(links, orders, receipts, cells, as_of)
    acc = {sid: _LaneAcc(sid, np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n, dtype=bool))
           for sid in ids}
    fallback = ids[0]

    def lane_of(sid: str | None) -> _LaneAcc:
        return acc.get(sid) or acc[fallback]

    def add(arr: np.ndarray, day: dt.date, qty: float) -> None:
        i = index.offset(day)
        if i is not None:
            arr[i] += qty

    window_start = as_of - dt.timedelta(days=max(int(params.backlog_days), 0))
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
        ln.orders.append(OrderInfo(o.order_id, o.supplier_id, typ, day, float(o.qty_ordered), float(o.qty_open or 0.0), o.ref))
        if firm:
            if day < as_of:
                add(ln.orders_firm_hist, day, float(o.qty_ordered))
                if day >= window_start:
                    ln.backlog_ordered += float(o.qty_ordered)
            elif o.qty_open and o.qty_open > EPS:
                add(ln.orders_firm, day, float(o.qty_open))
        elif o.qty_open and o.qty_open > EPS:
            add(ln.orders_forecast, day, float(o.qty_open))
    # reference day: receipts of the day already cover part of the firm orders of the day
    i0 = index.offset(as_of)
    if i0 is not None:
        for ln in acc.values():
            ln.orders_firm[i0] = max(0.0, ln.orders_firm[i0] - received_today.get(ln.supplier_id, 0.0))
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
                 orders_firm=ln.orders_firm.tolist(), orders_firm_hist=ln.orders_firm_hist.tolist(),
                 orders_forecast=ln.orders_forecast.tolist(), receipts=ln.receipts.tolist(), plan=ln.plan.tolist(),
                 supply_proposed=[0.0] * n, plan_typed=ln.plan_typed.tolist(),
                 backlog_ordered=ln.backlog_ordered, backlog_received=ln.backlog_received,
                 backlog_qty=max(0.0, ln.backlog_ordered - ln.backlog_received), orders=ln.orders)
            for ln in acc.values()]


def lane_totals(lanes: list[Lane], key: str) -> np.ndarray:
    if not lanes:
        return np.zeros(0)
    return np.sum([np.asarray(getattr(l, key), dtype=float) for l in lanes], axis=0)
