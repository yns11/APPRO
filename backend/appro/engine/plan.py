"""Supply flows of one article: the ERP layer (orders as is) and the plan layer (the planner's
delivery plan), the late-order rule and the same-day matching of receipts against orders.

Vocabulary (docs/regles_metier.md § 3):

* an **order** is a delivery slot of the ERP extract – ``supplier|article|date|firm`` – or an
  order typed in the app; the ERP never moves nor cancels a slot and its remaining quantity is
  unreliable once the date is past;
* the **ERP stock** counts the firm orders as is; an order still open on a past date is excluded
  and listed *non reçue* (to qualify);
* the **plan stock** counts the delivery plan: every firm order as is, unless plan lines
  override it (``order_id`` set: new date / quantity, split, or 0), plus the free lines and the
  forecast slots taken over by a line.  A plan line whose date is past has expired: it is not
  counted (its receipt, if any, is in the receipts);
* on the **reference day** nothing links a receipt to an order: the quantity still expected from
  an order dated that day is ``min(open, max(0, ordered − receipts of the day))``, the receipts
  being allocated to the orders of the same supplier (then to those without a supplier), firm
  orders first.  Applied separately to the ERP placement and to the plan lines of the day.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .calendar import iso_week_monday
from .demand import DayIndex
from .models import EngineParams, OrderLine, OrderState, PlanLine, PlanLineState, Receipt, SupplyEvent

EPS = 1e-9


@dataclass
class SupplyFlows:
    orders_firm: np.ndarray            # ERP layer: open firm orders (matched on the reference day)
    orders_firm_hist: np.ndarray       # past firm orders, ordered quantity (display only)
    orders_forecast: np.ndarray        # forecast orders (information)
    plan: np.ndarray                   # plan layer (ERP as is + plan lines), CBN excluded
    plan_hist: np.ndarray              # expired plan lines (display only)
    orders: list[OrderState] = field(default_factory=list)
    lines: list[PlanLineState] = field(default_factory=list)
    events: list[SupplyEvent] = field(default_factory=list)

    @property
    def not_received(self) -> list[OrderState]:
        return [o for o in self.orders if o.status == "not_received"]


class _ReceiptPool:
    """Receipts of the reference day, allocated to the orders of that day (same-day matching)."""

    def __init__(self, receipts: list[Receipt], day: dt.date) -> None:
        self.pool: dict[str | None, float] = defaultdict(float)
        for r in receipts:
            if r.receipt_date == day and r.qty > 0:
                self.pool[r.supplier_id or None] += r.qty

    def copy(self) -> "_ReceiptPool":
        c = _ReceiptPool([], dt.date.min)
        c.pool = defaultdict(float, self.pool)
        return c

    def expected(self, supplier_id: str | None, qty_ordered: float, qty_open: float) -> float:
        """Quantity still expected today from an order, consuming the matching receipts."""
        used = 0.0
        for key in ((supplier_id, None) if supplier_id else (None,)):
            take = min(self.pool.get(key, 0.0), qty_ordered - used)
            if take > EPS:
                self.pool[key] -= take
                used += take
            if qty_ordered - used <= EPS:
                break
        return max(0.0, min(qty_open, qty_ordered - used))


def build_supply_flows(orders: list[OrderLine], plan_lines: list[PlanLine], receipts: list[Receipt],
                       app_received: dict[str, float], index: DayIndex, as_of: dt.date,
                       params: EngineParams) -> SupplyFlows:
    n = index.n
    flows = SupplyFlows(np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n))
    pool_erp = _ReceiptPool(receipts, as_of)
    pool_plan = pool_erp.copy()
    overrides: dict[str, list[PlanLine]] = defaultdict(list)
    for line in plan_lines:
        if line.order_id:
            overrides[line.order_id].append(line)

    def selected(o: OrderLine) -> bool:
        if params.orders_source == "erp" and o.source == "APP":
            return False
        if params.orders_source == "app" and o.source == "ERP":
            return False
        return True

    def add(arr: np.ndarray, day: dt.date, qty: float) -> None:
        i = index.offset(day)
        if i is not None and qty > EPS:
            arr[i] += qty

    def place_line(line: PlanLine, basis_ordered: float, supplier_id: str | None) -> PlanLineState:
        """Count one plan line in the plan layer (expired when past, matched on the reference day)."""
        qty = float(line.qty)
        if line.date < as_of:
            add(flows.plan_hist, line.date, qty)
            return PlanLineState(line.line_id, line.order_id, line.article_id, line.date, qty, supplier_id,
                                 "expired", False, note=line.note)
        counted = pool_plan.expected(supplier_id, basis_ordered + qty, qty) if line.date == as_of else qty
        add(flows.plan, line.date, counted)
        flows.events.append(SupplyEvent(line.date, "plan", line.order_id or line.line_id, counted, supplier_id,
                                        "PLAN", line.source))
        return PlanLineState(line.line_id, line.order_id, line.article_id, line.date, qty, supplier_id,
                             "override" if line.order_id else "free", True, note=line.note)

    # firm orders first (they take the receipts of the day), then forecast ; deterministic order
    ranked = sorted((o for o in orders if selected(o)),
                    key=lambda o: (o.order_type.value not in params.firm_sources, o.expected_date, o.order_id))
    seen: set[str] = set()
    for o in ranked:
        seen.add(o.order_id)
        typ = o.order_type.value
        firm = typ in params.firm_sources
        qty_open = o.qty_open - app_received.get(o.order_id, 0.0)
        day = o.expected_date
        if typ == "FORECAST" and params.forecast_date_policy == "week_monday":
            day = iso_week_monday(day)
        days_late = max(0, (as_of - day).days)
        received_qty = o.qty_ordered - qty_open
        lines = overrides.get(o.order_id, [])
        state = OrderState(order_id=o.order_id, article_id=o.article_id, supplier_id=o.supplier_id, order_type=typ,
                           source=o.source, expected_date=day, qty_ordered=float(o.qty_ordered),
                           qty_open=float(max(qty_open, 0.0)), qty_expected=0.0, days_late=days_late,
                           status="info" if not firm else "expected", note=o.note)
        # ---- ERP layer and display of the past
        if qty_open <= EPS:
            if firm:
                add(flows.orders_firm_hist, day, float(o.qty_ordered))     # received: history only
            continue
        if firm and day < as_of:
            add(flows.orders_firm_hist, day, float(o.qty_ordered))
            state.status = "not_received"
        elif firm:
            expected = pool_erp.expected(o.supplier_id, o.qty_ordered, qty_open) if day == as_of else qty_open
            state.qty_expected = float(expected)
            add(flows.orders_firm, day, expected)
            flows.events.append(SupplyEvent(day, "order", o.order_id, expected, o.supplier_id, typ, o.source, False))
        else:
            add(flows.orders_forecast, day, qty_open)
            flows.events.append(SupplyEvent(day, "order", o.order_id, qty_open, o.supplier_id, typ, o.source, False))
        # ---- plan layer
        if lines:
            state.status = "planned" if firm else "info"
            for line in sorted(lines, key=lambda l: (l.date, l.line_id)):
                st = place_line(line, received_qty, o.supplier_id)
                st.erp_date, st.erp_qty = day, float(qty_open)
                flows.lines.append(st)
                if st.counted:
                    state.plan_qty += st.qty
                    state.plan_dates.append(st.date)
        elif firm and day >= as_of:
            counted = pool_plan.expected(o.supplier_id, o.qty_ordered, qty_open) if day == as_of else qty_open
            add(flows.plan, day, counted)
            state.plan_qty, state.plan_dates = float(counted), [day]
            flows.lines.append(PlanLineState(None, o.order_id, o.article_id, day, float(qty_open), o.supplier_id,
                                             "erp", True, erp_date=day, erp_qty=float(qty_open)))
        flows.orders.append(state)

    # plan lines that reference an unknown / vanished order, and free lines
    for line in sorted(plan_lines, key=lambda l: (l.date, l.line_id)):
        if line.order_id and line.order_id in seen:
            continue
        st = place_line(line, 0.0, line.supplier_id)
        if line.order_id:
            st.note = (st.note + " · " if st.note else "") + "commande absente de l'ERP"
        flows.lines.append(st)

    flows.orders.sort(key=lambda s: (s.expected_date, s.order_id))
    flows.lines.sort(key=lambda s: (s.date, s.order_id or "", s.line_id or ""))
    return flows
