"""Unit tests of the engine building blocks and of the business rules."""
from __future__ import annotations

import datetime as dt

import numpy as np

from appro.engine import run_mrp
from appro.engine.calendar import WorkCalendar
from appro.engine.demand import DayIndex, ceil_to_multiple, spread_week
from appro.engine.models import (
    ActualLine,
    AlertType,
    Article,
    BomLine,
    Dataset,
    EngineParams,
    Movement,
    OrderLine,
    OrderStatus,
    OrderType,
    PdpLine,
    PlanLine,
    Program,
    Receipt,
    SimCell,
    StockSnapshot,
    Supplier,
    SupplierLink,
)
from appro.engine.projection import coverage_days
from appro.engine.scenario import ScenarioEvent, apply_scenario
from appro.services.expression import evaluate

MON = dt.date(2026, 9, 21)  # Monday
D = dt.timedelta


def make_dataset(**over) -> Dataset:
    base = dict(
        articles=[Article("A1", "Widget", "PCE", coverage_target_days=5, alert_red_days=2, alert_yellow_days=5,
                          overstock_days=30, order_cycle_days=7)],
        suppliers=[Supplier("S1", "Supplier 1"), Supplier("S2", "Supplier 2", delivery_weekdays=frozenset({2, 4}))],
        links=[SupplierLink("A1", "S1", moq=100, pack_qty=50, lead_time_days=5, quota_pct=100, priority=1)],
        programs=[Program("P1", "Line 1")],
        bom=[BomLine("P1", "A1", qty_per=2.0)],
        pdp=[PdpLine("P1", MON + D(weeks=k), 500.0) for k in range(-2, 8)],
        actuals=[],
        orders=[],
        receipts=[],
        movements=[],
        stock=[StockSnapshot("A1", MON - D(days=1), 1000.0)],
    )
    base.update(over)
    return Dataset(**base)


def run(ds: Dataset, **params):
    p = dict(as_of=MON, horizon_days=30, generate_proposals=False)
    p.update(params)
    return run_mrp(ds, EngineParams(**p)).articles["A1"]


# ------------------------------------------------------------------ calendar
def test_calendar_working_days_and_holidays():
    cal = WorkCalendar.from_spec("1,2,3,4,5", ["2026-09-23"])
    assert cal.is_working_day(MON)
    assert not cal.is_working_day(MON + D(days=5))  # Saturday
    assert not cal.is_working_day(dt.date(2026, 9, 23))  # holiday
    assert cal.add_working_days(MON, 3) == dt.date(2026, 9, 25)  # skips the holiday
    assert cal.add_working_days(dt.date(2026, 9, 25), -3) == MON
    assert cal.next_working_day(dt.date(2026, 9, 26)) == dt.date(2026, 9, 28)
    assert cal.previous_working_day(dt.date(2026, 9, 27)) == dt.date(2026, 9, 25)
    assert cal.working_days_between(MON, dt.date(2026, 9, 28)) == 4
    assert cal.next_working_day(MON, allowed_weekdays=frozenset({4})) == dt.date(2026, 9, 24)


def test_spread_week_policies():
    assert spread_week(500, 5, "none") == [100.0] * 5
    assert spread_week(503, 5, "exact") == [101, 101, 101, 100, 100]
    assert sum(spread_week(503, 5, "exact")) == 503
    assert spread_week(503, 5, "per_day") == [101.0] * 5  # legacy: 505 in total
    assert spread_week(2519.748, 5, "exact") == [504, 504, 504, 504, 504]
    assert spread_week(10, 0, "exact") == []


def test_ceil_to_multiple():
    assert ceil_to_multiple(101, 50) == 150
    assert ceil_to_multiple(100, 50) == 100
    assert ceil_to_multiple(7, 0) == 7


# ------------------------------------------------------------------ demand
def test_demand_actual_then_remainder():
    """Past days = actual production ; current week = remainder of the PDP over the remaining open
    days (reference day included) ; later weeks = PDP.  BOM: 2 components per unit."""
    wed = MON + D(days=2)
    ds = make_dataset(actuals=[ActualLine("P1", MON, 250.0), ActualLine("P1", MON + D(days=1), 300.0)])
    r = run(ds, as_of=wed)
    i = r.dates.index(MON)
    # Monday / Tuesday: actual 250 and 300 units → 500 and 600 components (consumed)
    assert r.consumed[i] == 500 and r.consumed[i + 1] == 600 and r.required[i] == 0
    # Wednesday–Friday: remainder max(500 − 550, 0) = 0 → nothing required this week
    assert r.required[i + 2] == 0 and r.required[i + 3] == 0 and r.required[i + 4] == 0
    # next week: PDP 500 over 5 days = 100 units → 200 components per day
    assert r.required[i + 7] == 200 and r.consumed[i + 7] == 0
    # a past day without report counts 0 (missing_actual_policy = zero)
    assert r.consumed[i - 3] == 0
    # example 1: 500 + 600 done out of a 2 000 PDP on Wednesday → (2000 − 1100) / 3 = 300 units per remaining day
    ds2 = make_dataset(pdp=[PdpLine("P1", MON + D(weeks=k), 2000.0) for k in range(-2, 8)],
                       actuals=[ActualLine("P1", MON, 500.0), ActualLine("P1", MON + D(days=1), 600.0)])
    r2 = run(ds2, as_of=wed)
    assert r2.required[i + 2] == 600 and r2.required[i + 3] == 600 and r2.required[i + 4] == 600   # 300 × 2
    # example 2: PDP already exceeded on Monday–Tuesday, and a consumption reported on Wednesday
    ds3 = make_dataset(pdp=[PdpLine("P1", MON + D(weeks=k), 2000.0) for k in range(-2, 8)],
                       actuals=[ActualLine("P1", MON, 1200.0), ActualLine("P1", MON + D(days=1), 1000.0),
                                ActualLine("P1", wed, 200.0)])
    r3 = run(ds3, as_of=wed + D(days=1))
    i3 = r3.dates.index(MON)
    assert r3.consumed[i3:i3 + 3] == [2400, 2000, 400] and r3.required[i3 + 3] == 0 and r3.required[i3 + 4] == 0
    # an actual reported for the reference day or later is ignored (the day is still to come)
    r4 = run(ds3, as_of=wed)
    assert r4.consumed[i + 2] == 0 and r4.required[i + 2] == 0
    # legacy modes
    r5 = run(ds, as_of=wed, production_mode="plan_only")
    assert r5.demand[i] == 200 and r5.demand[i + 2] == 200
    r6 = run(ds, as_of=wed, production_mode="actual_then_plan")
    assert r6.demand[i + 1] == 600 and r6.demand[i + 2] == 200


# ------------------------------------------------------------------ projection / layers
def test_two_layers_erp_and_plan():
    """ERP = receipts + firm orders as is ; Plan = receipts + delivery plan (ERP as is, overridden by
    plan lines) ; forecast orders are information only ; adjustments count in both."""
    tue, wed, thu, fri = (MON + D(days=k) for k in (1, 2, 3, 4))
    ds = make_dataset(orders=[
        OrderLine("F1", "A1", "S1", wed, 300, order_type=OrderType.FIRM),
        OrderLine("D1", "A1", "S1", thu, 400, order_type=OrderType.FORECAST),
        OrderLine("F2", "A1", "S1", fri, 100, qty_received=100, status=OrderStatus.RECEIVED),
        OrderLine("A2", "A1", "S1", fri, 600, order_type=OrderType.FIRM, source="APP"),
    ], receipts=[Receipt("R1", "A1", tue, 50)], movements=[Movement("M1", "A1", tue, -20)])
    r = run(ds, horizon_days=10)
    i = r.dates.index(MON)
    # Monday 1000 − 200 = 800 ; Tuesday +50 −20 −200 = 630 ; Wednesday +300 −200 = 730 ; Thursday −200 = 530 ; Friday +600 −200 = 930
    assert r.stock_erp[i:i + 5] == [800, 630, 730, 530, 930]
    assert r.stock_plan[i:i + 5] == [800, 630, 730, 530, 930]          # no plan line: plan = ERP
    assert r.orders_forecast[i + 3] == 400 and r.stock_erp[i + 3] == 530
    assert r.kpis["open_firm_qty"] == 900 and r.kpis["open_forecast_qty"] == 400 and r.kpis["plan_qty"] == 900
    st = {o.order_id: o for o in r.orders}
    assert st["F1"].status == "expected" and st["D1"].status == "info" and "F2" not in st
    assert [l.origin for l in r.plan_lines] == ["erp", "erp"]
    # the forecast slot taken into the plan by a line: plan layer only
    ds.plan = [PlanLine("L1", "A1", thu, 400, order_id="D1")]
    r2 = run(ds, horizon_days=10)
    assert r2.stock_erp[i + 3] == 530 and r2.stock_plan[i + 3] == 930
    assert next(l for l in r2.plan_lines if l.order_id == "D1").origin == "override"


def test_plan_lines_override_split_zero_free_and_expire():
    wed, fri = MON + D(days=2), MON + D(days=4)
    mon2 = MON + D(days=7)
    ds = make_dataset(orders=[OrderLine("F1", "A1", "S1", wed, 1000, order_type=OrderType.FIRM),
                              OrderLine("F2", "A1", "S1", fri, 800, order_type=OrderType.FIRM)])
    ds.plan = [
        PlanLine("L1", "A1", fri, 400, order_id="F1"),          # F1 delayed and split: 400 Friday …
        PlanLine("L2", "A1", mon2, 600, order_id="F1"),         # … 600 next Monday
        PlanLine("L3", "A1", fri, 0, order_id="F2"),            # nothing expected from F2
        PlanLine("L4", "A1", MON + D(days=1), 150),             # free line (stop-gap) on Tuesday
        PlanLine("L5", "A1", MON - D(days=3), 500),             # expired free line: display only
    ]
    r = run(ds, horizon_days=12)
    i = r.dates.index(MON)
    assert r.orders_firm[i + 2] == 1000 and r.orders_firm[i + 4] == 800          # ERP as is
    assert r.plan[i + 1] == 150 and r.plan[i + 2] == 0 and r.plan[i + 4] == 400 and r.plan[i + 7] == 600
    assert r.plan_hist[i - 3] == 500 and sum(r.plan) == 1150
    assert r.stock_plan[i + 2] == r.stock_erp[i + 2] - 1000 + 150
    st = {o.order_id: o for o in r.orders}
    assert st["F1"].status == "planned" and st["F1"].plan_qty == 1000 and st["F1"].plan_dates == [fri, mon2]
    assert st["F2"].status == "planned" and st["F2"].plan_qty == 0
    origins = {(l.line_id, l.origin, l.counted) for l in r.plan_lines}
    assert {("L1", "override", True), ("L4", "free", True), ("L5", "expired", False)} <= origins
    assert r.kpis["plan_line_count"] == 4


def test_not_received_orders_are_excluded_and_dated_by_a_plan_line():
    """A past ERP order still open counts in no stock and is 'non reçue' ; a plan line with a date
    puts it in the plan stock.  A past received order is history only."""
    late = OrderLine("L1", "A1", "S1", MON - D(days=5), 200, order_type=OrderType.FIRM)
    old = OrderLine("L0", "A1", "S1", MON - D(days=60), 300, order_type=OrderType.FIRM)
    done = OrderLine("R0", "A1", "S1", MON - D(days=4), 250, qty_received=250, status=OrderStatus.RECEIVED)
    ds = make_dataset(orders=[late, old, done], receipts=[Receipt("R1", "A1", MON - D(days=4), 250)])
    r = run(ds, horizon_days=5)
    i = r.dates.index(MON)
    assert sum(r.orders_firm) == 0 and sum(r.plan) == 0
    assert r.orders_firm_hist[i - 5] == 200 and r.orders_firm_hist[i - 4] == 250 and r.receipts[i - 4] == 250
    st = {o.order_id: o for o in r.orders}
    assert st["L1"].status == "not_received" and st["L1"].days_late == 5 and "R0" not in st
    assert r.kpis["backlog_count"] == 2 and r.kpis["backlog_qty"] == 500     # L0 is outside the window but still due
    late_alerts = [a for a in r.alerts if a.alert_type == AlertType.LATE_ORDER]
    assert len(late_alerts) == 1 and "non reçue" in late_alerts[0].message
    ds.plan = [PlanLine("L1", "A1", MON + D(days=2), 200, order_id="L1")]
    r2 = run(ds, horizon_days=5)
    assert sum(r2.orders_firm) == 0 and r2.plan[i + 2] == 200 and r2.kpis["backlog_count"] == 1
    assert r2.stock_plan[i + 2] - r2.stock_erp[i + 2] == 200
    assert next(o for o in r2.orders if o.order_id == "L1").status == "planned"


def test_same_day_matching_of_receipts_and_orders():
    """Reference day: nothing links a receipt to an order, so the quantity still expected from an
    order dated that day is min(open, max(0, ordered − receipts of the day)), same supplier first."""
    ds = make_dataset(orders=[OrderLine("F1", "A1", "S1", MON, 600, order_type=OrderType.FIRM),
                              OrderLine("F2", "A1", "S2", MON, 1000, order_type=OrderType.FIRM),
                              OrderLine("F3", "A1", "S1", MON, 500, 200, order_type=OrderType.FIRM)],   # 200 booked in the ERP
                      receipts=[Receipt("R1", "A1", MON, 400, supplier_id="S1"), Receipt("R2", "A1", MON, 150)])
    r = run(ds, horizon_days=3)
    i = r.dates.index(MON)
    st = {o.order_id: o for o in r.orders}
    # F1 (S1, ordered 600): takes 400 of S1 then 150 of the pool → 50 expected ; F2 (S2): nothing to match → 1000
    # F3 (S1, ordered 500, open 300): pools empty → min(300, 500) = 300
    assert st["F1"].qty_expected == 50 and st["F2"].qty_expected == 1000 and st["F3"].qty_expected == 300
    assert r.orders_firm[i] == 1350 and r.receipts[i] == 550 and r.plan[i] == 1350
    assert r.stock_erp[i] == 1000 + 550 + 1350 - 200


def test_adjustments_correct_the_reference_stock_and_history_is_reconstructed():
    """A planner adjustment dated on/before the reference day corrects the reference stock (the ERP
    stock is unreliable) ; the past stock is reconstructed backwards from receipts, adjustments
    and the actual consumption."""
    ds = make_dataset(actuals=[ActualLine("P1", MON - D(days=k), 100.0) for k in (1, 2, 3, 4)],   # 200 components / day
                      receipts=[Receipt("R1", "A1", MON - D(days=2), 500)],
                      cells=[SimCell("A1", MON - D(days=3), "adjustment", -300.0),                 # count found 300 short
                             SimCell("A1", MON + D(days=2), "adjustment", 40.0)])                  # known future movement
    r = run(ds, horizon_days=5)
    i = r.dates.index(MON)
    assert r.reference_correction == -300 and r.kpis["stock_reference"] == 700 and r.kpis["stock_on_hand"] == 1000
    # Monday: 700 − 200 = 500 ; Wednesday: +40
    assert r.stock_plan[i] == 500 and r.stock_plan[i + 2] == 500 - 200 + 40 - 200
    # history backwards from the corrected snapshot (Sunday 700), 200 consumed on Thu..Sun, receipt 500 on
    # Saturday, adjustment −300 on Friday: Sat 900 → Fri 600 → Thu 1100 → Wed 1300
    assert r.stock_erp[i - 5:i] == [1300, 1100, 600, 900, 700]
    assert r.stock_plan[i - 5:i] == r.stock_erp[i - 5:i]
    assert r.adjustments[i - 3] == -300 and r.adjustments[i + 2] == 40


def test_adjustment_history_chain():
    """Explicit backward chain: stock[d-1] = stock[d] − receipts[d] − adjustments[d] + consumed[d]."""
    ds = make_dataset(actuals=[ActualLine("P1", MON - D(days=2), 50.0)],       # Saturday: 100 components consumed
                      receipts=[Receipt("R1", "A1", MON - D(days=1), 500)],     # Sunday (snapshot day): already in the snapshot
                      cells=[SimCell("A1", MON - D(days=2), "adjustment", -30.0)])
    r = run(ds, horizon_days=3)
    i = r.dates.index(MON)
    # snapshot Sunday = 1000 − 30 (correction) = 970 ; Saturday = Sunday − 500 (receipt Sunday) = 470 ;
    # Friday = Saturday − (−30) + 100 = 600
    assert r.stock_erp[i - 1] == 970 and r.stock_erp[i - 2] == 470 and r.stock_erp[i - 3] == 600
    assert r.stock_plan[i] == 970 - 200


def test_late_order_and_coverage_projection_shape():
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 500.0)],
                      orders=[OrderLine("F1", "A1", "S1", MON + D(days=3), 1000, order_type=OrderType.FIRM)])
    backlog = run(ds, horizon_days=6)
    lost = run(ds, horizon_days=6, shortage_policy="lost")
    i = backlog.dates.index(MON)
    # Mon 300, Tue 100, Wed -100 (backlog) / 0 (lost, 100 lost), Thu +1000-200: 700 / 800
    assert backlog.stock_erp[i:i + 4] == [300, 100, 0, 700]
    assert backlog.stock_erp_net[i:i + 4] == [300, 100, -100, 700]
    assert backlog.shortage_erp[i:i + 4] == [0, 0, 100, 0]
    assert lost.stock_erp[i:i + 4] == [300, 100, 0, 800]
    assert lost.shortage_erp[i:i + 4] == [0, 0, 100, 0]
    for r in (backlog, lost):
        assert min(r.stock_erp) >= 0
        assert r.kpis["first_stockout_erp"] == (MON + D(days=2)).isoformat()
        assert r.kpis["max_shortage_erp"] == 100
    # with proposals, the lost policy re-projects exactly: no unserved demand after the first delivery
    lost_p = run(ds, horizon_days=40, shortage_policy="lost", generate_proposals=True)
    first = min(p.delivery_date for p in lost_p.proposals)
    j = lost_p.dates.index(first)
    assert max(lost_p.shortage_plan[j:]) == 0


def test_coverage_days_calendar_and_working():
    cal = WorkCalendar()
    index = DayIndex(MON, MON + D(days=13))
    demand = np.array([100 if cal.is_working_day(d) else 0 for d in index.dates], dtype=float)
    stock = np.full(index.n, 250.0)
    cov_cal = coverage_days(stock, demand, index, cal, "calendar", "covered")
    cov_wd = coverage_days(stock, demand, index, cal, "working", "covered")
    assert cov_cal[0] == 2 and cov_wd[0] == 2
    # Friday: 250 covers Sat, Sun (0) and Mon, Tue (200) → 4 calendar days, 2 working days
    assert cov_cal[4] == 4 and cov_wd[4] == 2
    stock2 = np.full(index.n, 200.0)
    assert coverage_days(stock2, demand, index, cal, "calendar", "covered")[0] == 2
    assert coverage_days(stock2, demand, index, cal, "calendar", "not_covered")[0] == 1
    neg = np.full(index.n, -5.0)
    assert coverage_days(neg, demand, index, cal)[0] == 0


# ------------------------------------------------------------------ proposals
def test_proposals_respect_moq_pack_lead_time_and_delivery_days():
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 2600.0)])
    r = run(ds, horizon_days=40, generate_proposals=True)
    assert r.proposals
    p = r.proposals[0]
    assert p.qty >= 100 and p.qty % 50 == 0
    assert p.delivery_date.isoweekday() in (1, 2, 3, 4, 5)
    assert not p.urgent and WorkCalendar().working_days_between(p.order_date, p.delivery_date) == 5
    assert p.delivery_date > MON
    # proposals are part of the plan stock: no shortage left
    assert max(r.shortage_plan[r.dates.index(MON):]) == 0
    assert r.kpis["proposal_count"] == len(r.proposals)
    # a proposal is a CBN line of the plan
    cbn = [l for l in r.plan_lines if l.origin == "cbn"]
    assert len(cbn) == 0   # engine result carries proposals separately ; presenters merge them


def test_proposals_supplier_delivery_weekdays_and_quota():
    links = [SupplierLink("A1", "S1", moq=100, pack_qty=1, lead_time_days=2, quota_pct=50, priority=1),
             SupplierLink("A1", "S2", moq=100, pack_qty=1, lead_time_days=2, quota_pct=50, priority=2)]
    ds = make_dataset(links=links, stock=[StockSnapshot("A1", MON - D(days=1), 300.0)])
    r = run(ds, horizon_days=60, generate_proposals=True)
    by_sup = {}
    for p in r.proposals:
        by_sup[p.supplier_id] = by_sup.get(p.supplier_id, 0) + p.qty
        if p.supplier_id == "S2":
            assert p.delivery_date.isoweekday() in (2, 4)
    assert set(by_sup) == {"S1", "S2"}
    share = by_sup["S1"] / sum(by_sup.values())
    assert 0.3 < share < 0.7


def test_urgent_proposal_when_lead_time_cannot_be_met():
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 100.0)],
                      links=[SupplierLink("A1", "S1", moq=1, pack_qty=1, lead_time_days=15)])
    r = run(ds, horizon_days=40, generate_proposals=True)
    assert r.proposals and r.proposals[0].urgent
    assert any(a.alert_type == AlertType.URGENT_PROPOSAL for a in r.alerts)
    r2 = run(ds, horizon_days=40, respect_lead_time=True, generate_proposals=True)
    first = min(p.delivery_date for p in r2.proposals)
    assert first >= WorkCalendar().add_working_days(MON, 15)
    assert not any(p.urgent for p in r2.proposals)


def test_frozen_period_and_monday_placement():
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 100.0)])
    r = run(ds, horizon_days=40, frozen_days=10, generate_proposals=True)
    assert all(p.delivery_date > MON + D(days=10) for p in r.proposals)
    r3 = run(ds, horizon_days=60, proposal_placement="monday", generate_proposals=True)
    assert r3.proposals and all(p.delivery_date.isoweekday() == 1 for p in r3.proposals)


def test_shortfall_tolerance_skips_short_dips():
    """A one-day dip under the target that recovers by itself (a firm order the next day) is
    ignored when the tolerance allows it."""
    thu = MON + D(days=3)
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 1000.0)],
                      orders=[OrderLine("F1", "A1", "S1", thu, 2000, order_type=OrderType.FIRM)])
    # Mon 800 (target 800), Tue 600 (target 600), Wed 400 < 600 (dip), Thu +2000 → 2200
    base = run(ds, horizon_days=12, generate_proposals=True)
    assert base.proposals and base.proposals[0].delivery_date <= thu
    tol = run(ds, horizon_days=12, generate_proposals=True, shortfall_tolerance_days=1)
    assert not [p for p in tol.proposals if p.delivery_date <= thu]


def test_forecast_monday_policy():
    thu = MON + D(days=3)
    ds = make_dataset(orders=[OrderLine("D1", "A1", "S1", thu, 400, order_type=OrderType.FORECAST)])
    r = run(ds, forecast_date_policy="week_monday")
    i = r.dates.index(MON)
    assert r.orders_forecast[i] == 400 and r.orders_forecast[i + 3] == 0
    r2 = run(ds)
    assert r2.orders_forecast[i + 3] == 400


# ------------------------------------------------------------------ alerts
def test_alert_levels():
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 100000.0)])
    r = run(ds, horizon_days=60, generate_proposals=True)
    assert any(a.alert_type == AlertType.OVERSTOCK for a in r.alerts)
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 350.0)])
    r = run(ds, horizon_days=60, generate_proposals=True)
    low = [a for a in r.alerts if a.alert_type == AlertType.LOW_COVERAGE]
    assert low and low[0].severity.value == "critical"  # 350 -> 1 day of coverage <= red (2)
    ds = make_dataset(stock=[])
    r = run(ds, horizon_days=10, generate_proposals=True)
    assert any(a.alert_type == AlertType.MISSING_DATA for a in r.alerts)


def test_stockout_alert_scopes():
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 500.0)],
                      orders=[OrderLine("F1", "A1", "S1", MON + D(days=3), 2000, order_type=OrderType.FIRM)])
    r = run(ds, horizon_days=12)
    scopes = {a.scope: a for a in r.alerts if a.alert_type == AlertType.STOCKOUT}
    assert set(scopes) == {"erp", "plan"}
    assert r.kpis["first_stockout_erp"] == r.kpis["first_stockout_plan"] == (MON + D(days=2)).isoformat()
    # the planner brings the order forward in the plan: the plan stockout disappears, the ERP one stays
    ds.plan = [PlanLine("L1", "A1", MON + D(days=2), 2000, order_id="F1")]
    r2 = run(ds, horizon_days=12)
    scopes2 = {a.scope for a in r2.alerts if a.alert_type == AlertType.STOCKOUT}
    assert scopes2 == {"erp"} and r2.kpis["first_stockout_plan"] is None


# ------------------------------------------------------------------ scenarios
def test_scenario_events():
    ds = make_dataset(orders=[OrderLine("O1", "A1", "S1", MON + D(days=2), 300, order_type=OrderType.FIRM)])
    events = [
        ScenarioEvent("move_order", {"order_id": "O1", "days": 2}),
        ScenarioEvent("change_order_qty", {"order_id": "O1", "qty": 450}),
        ScenarioEvent("plan_factor", {"factor": 2.0}),
        ScenarioEvent("set_actual", {"program_id": "P1", "date": MON.isoformat(), "qty": 10}),
        ScenarioEvent("add_movement", {"article_id": "A1", "date": (MON + D(days=1)).isoformat(), "qty": -100}),
        ScenarioEvent("set_article_param", {"article_id": "A1", "field": "coverage_target_days", "value": 10}),
        ScenarioEvent("set_link_param", {"article_id": "A1", "supplier_id": "S1", "field": "lead_time_days", "value": 3}),
        ScenarioEvent("bogus", {}),
        ScenarioEvent("cancel_order", {"order_id": "nope"}),
    ]
    ds2, notes = apply_scenario(ds, events)
    assert ds.orders[0].expected_date == MON + D(days=2)  # original untouched
    assert ds2.orders[0].expected_date == MON + D(days=4) and ds2.orders[0].qty_ordered == 450
    assert all(l.qty == 1000 for l in ds2.pdp)
    assert ds2.actuals[0].qty == 10 and ds2.movements[0].qty == -100
    assert ds2.articles[0].coverage_target_days == 10 and ds2.links[0].lead_time_days == 3
    assert len(notes) == 2


# ------------------------------------------------------------------ programme impact / expressions
def test_program_impact():
    # 700 on hand, 200/day: Monday to Wednesday served, Thursday partially, Friday not at all (on-hand layer)
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 700.0)],
                      orders=[OrderLine("F1", "A1", "S1", MON + D(days=4), 5000, order_type=OrderType.FIRM)])
    res = run_mrp(ds, EngineParams(as_of=MON, horizon_days=13, generate_proposals=False))
    imp = res.program_impact
    assert imp["weeks"][0] == "2026-W39"
    p = next(x for x in imp["programs"] if x["program_id"] == "P1")
    assert p["planned"][0] == 500
    # on-hand: Mon 100 + Tue 100 + Wed 100 + Thu 50 = 350 ; ERP: Friday order arrives → 450 ; plan identical
    assert p["feasible"]["onhand"][0] == 350 and p["feasible"]["erp"][0] == 450 and p["feasible"]["plan"][0] == 450
    assert p["limiting"]["onhand"][0][0]["article_id"] == "A1"
    assert p["first_impact"]["onhand"] == "2026-W39" and p["first_impact"]["erp"] == "2026-W39"


def test_weekly_article_parameters():
    week2 = MON + D(days=7)
    art = Article("A1", "Widget", "PCE", coverage_target_days=5, alert_red_days=2, alert_yellow_days=5,
                  overstock_days=30, order_cycle_days=7)
    art.weekly = {"2026-W40": {"coverage_target_days": 10, "safety_stock_qty": 50}}
    assert art.param_at("coverage_target_days", week2) == 10 and art.param_at("coverage_target_days", MON) == 5
    ds = make_dataset(articles=[art], stock=[StockSnapshot("A1", MON - D(days=1), 100000.0)])
    r = run(ds)
    i = r.dates.index(MON)
    # demand 200 on working days: 5 calendar days from Monday = 800 ; 10 calendar days from week-2 Monday = 1600
    assert r.target_stock[i] == 800
    assert r.target_stock[r.dates.index(week2)] == 1600


def test_expression_evaluator():
    assert evaluate("1200") == 1200
    assert evaluate(" 1 200,5 ") == 1200.5
    assert evaluate("(100+50)*3-20/4") == 445
    assert evaluate("-500") == -500
    assert evaluate("=2*(3+4)") == 14
    for bad in ("abc", "2**8", "__import__('os')", "1/0", ""):
        try:
            evaluate(bad)
        except ValueError:
            continue
        raise AssertionError(f"{bad!r} should be rejected")
