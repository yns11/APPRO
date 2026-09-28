"""Order flows of one article: ERP placement (firm / forecast layers), planner actions (simulated
layer), late-order policy and same-day matching of receipts against orders.

Vocabulary (docs/regles_metier.md § 3):

* an **order** is a delivery slot of the ERP extract – ``supplier|article|date|firm`` – or an
  order typed in the app; the ERP never moves nor cancels a slot and its remaining quantity is
  unreliable once the date is past (receipts booked by hand are not matched);
* the **firm** and **forecast** layers count the ERP as is: an order still open on a past date
  is *excluded* and listed "à qualifier" (``late_order_policy = exclude``) or rescheduled to the
  next working day (``reschedule``, optionally limited by ``late_grace_days``);
* the **simulated** layer counts F′: the same orders after the planner **actions**
  (``reschedule`` tranches, ``cancel``, ``close``); the part of the open quantity not covered by
  the tranches stays at the ERP date; tranches dated in the past follow the late policy;
* on the **reference day** nothing links a receipt to an order, so the quantity still expected
  from an order dated that day is ``min(open, max(0, ordered − receipts of the day))``, the
  receipts being allocated to the orders of the same supplier (then to those without a
  supplier), firm orders first.  Applied independently to the ERP placement and to the
  simulated tranches.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from dataclasses import dataclass, field

import numpy as np

from .calendar import WorkCalendar, iso_week_monday
from .demand import DayIndex
from .models import EngineParams, OrderAction, OrderLine, OrderState, Receipt, SupplyEvent

EPS = 1e-9


@dataclass
class OrderFlows:
    supply_firm: np.ndarray            # F  – firm orders, ERP as is (late policy applied)
    supply_forecast: np.ndarray        # P  – forecast / planned orders, ERP as is
    supply_firm_sim: np.ndarray        # F′ – firm orders after the planner actions
    supply_forecast_sim: np.ndarray    # P′ – forecast orders after the planner actions
    states: list[OrderState] = field(default_factory=list)
    events: list[SupplyEvent] = field(default_factory=list)

    @property
    def to_qualify(self) -> list[OrderState]:
        return [s for s in self.states if s.status in ("late", "late_sim")]


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


def _layer_type(o: OrderLine, params: EngineParams) -> str | None:
    typ = o.order_type.value
    if o.source != "ERP" and params.app_firm_orders == "simulated":
        typ = "PLANNED"  # planner entries never feed the firm layer
    if typ in params.firm_sources:
        return "firm"
    if typ in params.forecast_sources:
        return "forecast"
    return None


def build_order_flows(orders: list[OrderLine], actions: dict[str, OrderAction], receipts: list[Receipt],
                      app_received: dict[str, float], index: DayIndex, calendar: WorkCalendar, as_of: dt.date,
                      params: EngineParams) -> OrderFlows:
    n = index.n
    flows = OrderFlows(np.zeros(n), np.zeros(n), np.zeros(n), np.zeros(n))
    pool_erp = _ReceiptPool(receipts, as_of)
    pool_sim = pool_erp.copy()
    horizon_cut = as_of + dt.timedelta(days=int(params.firm_horizon_days))

    def selected(o: OrderLine) -> bool:
        if params.orders_source == "erp" and o.source == "APP":
            return False
        if params.orders_source == "app" and o.source == "ERP":
            return False
        return True

    def place(day: dt.date, qty: float, pool: _ReceiptPool, o: OrderLine, qty_ordered: float
              ) -> tuple[dt.date | None, float, bool]:
        """Resolve a delivery (ERP date or tranche) against the late policy and the same-day matching.

        Returns (day counted or None when excluded, quantity counted, late flag)."""
        late = day < as_of
        if late:
            if params.late_order_policy != "reschedule":
                return None, 0.0, True
            if params.late_grace_days > 0 and (as_of - day).days > params.late_grace_days:
                return None, 0.0, True
            day = calendar.next_working_day(as_of, inclusive=True)
        if day == as_of:
            qty = pool.expected(o.supplier_id, qty_ordered, qty)
        return day, qty, late

    def add(arr: np.ndarray, day: dt.date | None, qty: float) -> None:
        if day is None or qty <= EPS:
            return
        i = index.offset(day)
        if i is not None:
            arr[i] += qty

    # firm orders first (they take the receipts of the day), then forecast ; deterministic order
    ranked = sorted((o for o in orders if selected(o)), key=lambda o: (_layer_type(o, params) != "firm", o.expected_date, o.order_id))
    for o in ranked:
        layer = _layer_type(o, params)
        if layer is None:
            continue
        qty_open = o.qty_open - app_received.get(o.order_id, 0.0)
        if qty_open <= EPS:
            continue
        typ = o.order_type.value
        day = o.expected_date
        if typ == "FORECAST" and params.forecast_date_policy == "week_monday":
            day = iso_week_monday(day)
        if layer == "forecast" and params.forecast_horizon_policy == "beyond_firm_horizon" and day < horizon_cut:
            continue  # inside the firm horizon the forecast should already be firm: not counted
        days_late = max(0, (as_of - day).days)
        arr_erp = flows.supply_firm if layer == "firm" else flows.supply_forecast
        arr_sim = flows.supply_firm_sim if layer == "firm" else flows.supply_forecast_sim

        # --- ERP placement (firm / forecast layers)
        erp_day, erp_qty, late = place(day, qty_open, pool_erp, o, o.qty_ordered)
        add(arr_erp, erp_day, erp_qty)
        if erp_day is not None:
            flows.events.append(SupplyEvent(erp_day, "order", o.order_id, erp_qty, o.supplier_id, typ, o.source, late))

        # --- simulated placement (actions)
        action = actions.get(o.order_id)
        state = OrderState(order_id=o.order_id, article_id=o.article_id, supplier_id=o.supplier_id, order_type=typ,
                           source=o.source, expected_date=day, qty_ordered=float(o.qty_ordered), qty_open=float(qty_open),
                           qty_expected=float(erp_qty), days_late=days_late,
                           status="late" if (late and erp_day is None) else "expected", in_firm_layer=erp_day is not None,
                           note=o.note)
        if action is None:
            sim_day, sim_qty, _ = place(day, qty_open, pool_sim, o, o.qty_ordered)
            add(arr_sim, sim_day, sim_qty)
            flows.states.append(state)
            continue
        state.action_id, state.action_kind, state.note = action.action_id, action.kind, action.note or o.note
        if action.kind in ("cancel", "close"):
            state.status = "cancelled" if action.kind == "cancel" else "closed"
            flows.states.append(state)
            continue
        # reschedule: tranches, truncated to the open quantity ; the remainder stays at the ERP date
        tranches: list[tuple[dt.date, float]] = []
        remaining = qty_open
        review = ""
        for t_day, t_qty in sorted(action.tranches, key=lambda t: t[0]):
            q = float(t_qty)
            if q <= EPS:
                continue
            if q > remaining + EPS:
                review = (f"quantité ERP réduite à {qty_open:,.0f} : tranches ramenées à la quantité restante")
                q = remaining
            if q <= EPS:
                break
            tranches.append((t_day, q))
            remaining -= q
        if remaining > EPS:
            tranches.append((day, remaining))
            if len(action.tranches) > 0 and remaining < qty_open - EPS:
                review = review or f"{remaining:,.0f} non couvert(s) par les tranches : attendu(s) à la date ERP"
        placed_any = False
        for t_day, q in tranches:
            # same-day matching basis: what the ERP already received + this tranche
            sim_day, sim_qty, t_late = place(t_day, q, pool_sim, o, o.qty_ordered - qty_open + q)
            add(arr_sim, sim_day, sim_qty)
            counted = sim_day is not None
            placed_any = placed_any or counted
            state.tranches.append({"date": t_day, "qty": float(q), "late": t_late, "expected": float(sim_qty) if counted else 0.0})
            if counted:
                flows.events.append(SupplyEvent(sim_day, "order_sim", o.order_id, sim_qty, o.supplier_id, typ, "ACTION", t_late))
        state.status = "simulated" if placed_any else "late_sim"
        state.review = review
        flows.states.append(state)

    flows.states.sort(key=lambda s: (s.expected_date, s.order_id))
    return flows
