"""Unit tests of the engine building blocks and of the business rules (docs/regles_metier.md)."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from appro.engine import run_mrp
from appro.engine.calendar import WorkCalendar
from appro.engine.demand import DayIndex, ceil_to_multiple, floor_to_multiple, spread_week
from appro.engine.models import (
    AdjustCell,
    AlertType,
    Article,
    BomLine,
    CellFlag,
    ConsumptionLine,
    Dataset,
    DesadvLine,
    EngineParams,
    OrderLine,
    OrderType,
    PdpLine,
    PlanCell,
    Program,
    Receipt,
    Severity,
    StockSnapshot,
    Supplier,
    SupplierLink,
)
from appro.engine.projection import coverage_days
from appro.services.expression import evaluate

MON = dt.date(2026, 9, 21)  # Monday
D = dt.timedelta
CAL = WorkCalendar()


def make_dataset(**over) -> Dataset:
    base = dict(
        articles=[Article("A1", "Widget", "PCE", coverage_target_days=5, alert_red_days=2, alert_yellow_days=5, overstock_days=30)],
        suppliers=[Supplier("S1", "Supplier 1"), Supplier("S2", "Supplier 2", delivery_weekdays=frozenset({2, 4}))],
        links=[SupplierLink("A1", "S1", moq=100, pack_qty=50, quota_pct=100, priority=1)],
        programs=[Program("P1", "Line 1")],
        bom=[BomLine("P1", "A1", qty_per=2.0)],
        pdp=[PdpLine("P1", MON + D(weeks=k), 500.0) for k in range(-2, 8)],
        consumption=[],
        orders=[],
        receipts=[],
        stock=[StockSnapshot("A1", MON - D(days=1), 1000.0)],
    )
    base.update(over)
    return Dataset(**base)


def run(ds: Dataset, no_cbn: bool = False, **params):
    """``no_cbn`` : a 100 % tolerance never triggers a « back to target » proposal ; only the floor
    (a shortage inside the lead time) still does – handy to check the raw stock arithmetic."""
    p = dict(as_of=MON, horizon_days=30)
    if no_cbn:
        p["target_tolerance_pct"] = 100.0
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
    assert CAL.add_working_days(MON, 5) == MON + D(days=7)        # « lundi + 5 = lundi suivant »


def test_spread_week_and_multiples():
    assert spread_week(500, 5, "none") == [100.0] * 5
    assert spread_week(503, 5) == [101, 101, 101, 100, 100] and sum(spread_week(503, 5)) == 503
    assert spread_week(2519.748, 5) == [504, 504, 504, 504, 504]
    assert spread_week(10, 0) == []
    assert ceil_to_multiple(101, 50) == 150 and ceil_to_multiple(100, 50) == 100 and ceil_to_multiple(7, 0) == 7
    assert floor_to_multiple(149, 50) == 100 and floor_to_multiple(7, 0) == 7


# ------------------------------------------------------------------ demand
def test_demand_actual_then_remainder():
    """Past days = reported component consumption ; current week = remainder of the exploded PDP over
    the remaining open days (today included) ; later weeks = PDP.  BOM: 2 components per unit, the
    consumption arrives already exploded (500 = 250 units × 2)."""
    wed = MON + D(days=2)
    ds = make_dataset(consumption=[ConsumptionLine("A1", MON, 500.0), ConsumptionLine("A1", MON + D(days=1), 600.0)])
    r = run(ds, as_of=wed)
    i = r.dates.index(MON)
    assert r.consumed[i] == 500 and r.consumed[i + 1] == 600 and r.required[i] == 0
    assert r.required[i + 2] == 0 and r.required[i + 3] == 0 and r.required[i + 4] == 0
    assert r.required[i + 7] == 200 and r.consumed[i + 7] == 0
    assert r.consumed[i - 3] == 0
    assert r.demand[i] == 500 and r.demand[i + 7] == 200
    # 500 + 600 done out of a 2 000 PDP on Wednesday → (2000 − 1100) / 3 = 300 units per remaining day
    ds2 = make_dataset(pdp=[PdpLine("P1", MON + D(weeks=k), 2000.0) for k in range(-2, 8)],
                       consumption=[ConsumptionLine("A1", MON, 1000.0), ConsumptionLine("A1", MON + D(days=1), 1200.0)])
    r2 = run(ds2, as_of=wed)
    assert r2.required[i + 2] == 600 and r2.required[i + 3] == 600 and r2.required[i + 4] == 600   # 300 × 2
    # PDP already exceeded on Monday–Tuesday, and a consumption reported on Wednesday
    ds3 = make_dataset(pdp=[PdpLine("P1", MON + D(weeks=k), 2000.0) for k in range(-2, 8)],
                       consumption=[ConsumptionLine("A1", MON, 2400.0), ConsumptionLine("A1", MON + D(days=1), 2000.0),
                                    ConsumptionLine("A1", wed, 400.0)])
    r3 = run(ds3, as_of=wed + D(days=1))
    i3 = r3.dates.index(MON)
    assert list(r3.consumed[i3:i3 + 3]) == [2400, 2000, 400] and r3.required[i3 + 3] == 0 and r3.required[i3 + 4] == 0
    r4 = run(ds3, as_of=wed)
    assert r4.consumed[i + 2] == 0 and r4.required[i + 2] == 0


def test_demand_days_profile_and_current_week_remainder():
    """``demand_days = 3`` : the weekly requirement is consumed on the first three open days ; the
    current week spreads its remainder on the remaining profile days, then on the remaining open days."""
    art = Article("A1", "Widget", "PCE", coverage_target_days=5, demand_days=3)
    ds = make_dataset(articles=[art])
    r = run(ds, no_cbn=True)
    i = r.dates.index(MON)
    assert list(r.demand[i + 7:i + 14]) == [334, 333, 333, 0, 0, 0, 0]          # 1 000 over Monday–Wednesday
    assert list(r.demand[i:i + 7]) == [334, 333, 333, 0, 0, 0, 0]
    # Tuesday : 200 consumed Monday → 800 left on Tuesday and Wednesday
    ds.consumption = [ConsumptionLine("A1", MON, 200.0)]
    r2 = run(make_dataset(articles=[art], consumption=ds.consumption), no_cbn=True, as_of=MON + D(days=1))
    assert list(r2.demand[i:i + 5]) == [200, 400, 400, 0, 0]
    # Thursday : 850 consumed, the campaign days are over → the remaining 150 go on Thursday and Friday
    cons = [ConsumptionLine("A1", MON, 300.0), ConsumptionLine("A1", MON + D(days=1), 300.0), ConsumptionLine("A1", MON + D(days=2), 250.0)]
    r3 = run(make_dataset(articles=[art], consumption=cons), no_cbn=True, as_of=MON + D(days=3))
    assert list(r3.demand[i:i + 5]) == [300, 300, 250, 75, 75]
    # a holiday on Monday : the three profile days become Tuesday–Thursday
    r4 = run(make_dataset(articles=[art], holidays=[MON + D(days=7)]), no_cbn=True)
    assert list(r4.demand[i + 7:i + 12]) == [0, 334, 333, 333, 0]
    # target in quantity is unchanged : five working days of requirement is still a week
    assert r.target_stock[i + 10] == 1000 and r.kpis["demand_days"] == 3


def test_consumption_source_plan_and_derived():
    """``plan`` : the past is the exploded PDP, the reports are ignored ; ``derived`` : the consumption is
    Σ coefficient × consumption of the source articles."""
    a1 = Article("A1", "Widget", "PCE", coverage_target_days=5)
    a2 = Article("A2", "Derived", "PCE", coverage_target_days=5, consumption_source="derived", derived_terms=[("A1", 2.0)])
    a3 = Article("A3", "Planned", "PCE", coverage_target_days=5, consumption_source="plan")
    ds = make_dataset(articles=[a1, a2, a3], bom=[BomLine("P1", "A1", 2.0), BomLine("P1", "A2", 4.0), BomLine("P1", "A3", 2.0)],
                      stock=[StockSnapshot(x, MON - D(days=1), 5000.0) for x in ("A1", "A2", "A3")],
                      consumption=[ConsumptionLine("A1", MON, 150.0), ConsumptionLine("A3", MON, 999.0)])
    res = run_mrp(ds, EngineParams(as_of=MON + D(days=2), horizon_days=14, target_tolerance_pct=100))
    i = res.articles["A1"].dates.index(MON)
    assert res.articles["A1"].consumed[i] == 150
    assert res.articles["A2"].consumed[i] == 300 and res.articles["A2"].demand_actual_share[i] == 1.0   # 2 × 150
    assert res.articles["A2"].consumed[i + 1] == 0                                                      # no report Tuesday
    assert res.articles["A3"].consumed[i] == 200 and res.articles["A3"].consumed[i + 1] == 200          # the PDP, not 999
    assert res.articles["A3"].demand_actual_share[i] == 0.0 and res.articles["A3"].kpis["consumption_source"] == "plan"


# ------------------------------------------------------------------ scenarios
def test_two_scenarios_erp_and_plan():
    """ERP = receipts + firm orders as is ; Plan = receipts + plan cells (else ERP) ; forecast orders
    are information only ; adjustments count in both."""
    tue, wed, thu, fri = (MON + D(days=k) for k in (1, 2, 3, 4))
    ds = make_dataset(orders=[
        OrderLine("F1", "A1", "S1", wed, 300, order_type=OrderType.FIRM),
        OrderLine("D1", "A1", "S1", thu, 400, order_type=OrderType.FORECAST),
        OrderLine("F2", "A1", "S1", fri, 100, qty_open=0),
        OrderLine("F3", "A1", "S1", fri, 600, order_type=OrderType.FIRM),
    ], receipts=[Receipt("R1", "A1", tue, 50)], adjustments=[AdjustCell("A1", tue, -20)])
    r = run(ds, no_cbn=True, horizon_days=10)
    i = r.dates.index(MON)
    # Monday 1000 − 200 = 800 ; Tuesday +50 −20 −200 = 630 ; Wednesday +300 −200 = 730 ; Thursday −200 = 530 ; Friday +600 −200 = 930
    assert list(r.stock_erp[i:i + 5]) == [800, 630, 730, 530, 930]
    assert list(r.stock_plan[i:i + 5]) == [800, 630, 730, 530, 930]          # no cell: plan = ERP
    assert r.orders_forecast[i + 3] == 400 and r.stock_erp[i + 3] == 530
    assert r.kpis["open_firm_qty"] == 900 and r.kpis["open_forecast_qty"] == 400 and r.kpis["plan_qty"] == 900
    assert len(r.lanes) == 1 and r.lanes[0].supplier_id == "S1" and len(r.lanes[0].orders) == 4
    ds.plan = [PlanCell("A1", "S1", thu, 400)]
    r2 = run(ds, no_cbn=True, horizon_days=10)
    assert r2.stock_erp[i + 3] == 530 and r2.stock_plan[i + 3] == 930
    assert r2.lanes[0].plan_typed[i + 3] and r2.kpis["plan_cell_count"] == 1


def test_plan_cells_move_split_zero_and_expire():
    wed, fri = MON + D(days=2), MON + D(days=4)
    mon2 = MON + D(days=7)
    ds = make_dataset(orders=[OrderLine("F1", "A1", "S1", wed, 1000, order_type=OrderType.FIRM),
                              OrderLine("F2", "A1", "S1", fri, 800, order_type=OrderType.FIRM)])
    ds.plan = [PlanCell("A1", "S1", wed, 0), PlanCell("A1", "S1", fri, 400), PlanCell("A1", "S1", mon2, 600),
               PlanCell("A1", "S1", MON + D(days=1), 150), PlanCell("A1", "S1", MON - D(days=3), 500)]
    r = run(ds, no_cbn=True, horizon_days=12)
    i = r.dates.index(MON)
    assert r.orders_firm[i + 2] == 1000 and r.orders_firm[i + 4] == 800          # ERP as is
    assert r.plan[i + 1] == 150 and r.plan[i + 2] == 0 and r.plan[i + 4] == 400 and r.plan[i + 7] == 600
    assert sum(r.plan) == 1150 and r.stock_plan[i + 2] == r.stock_erp[i + 2] - 1000 + 150
    assert r.kpis["plan_cell_count"] == 4
    assert r.lanes[0].plan_typed[i + 2] and not r.lanes[0].plan_typed[i + 3]


def test_backlog_is_the_erp_remaining_quantity_of_the_late_firm_orders():
    """Backlog of a supplier = Σ ERP remaining of its firm orders dated before today, no window, no
    receipt matching ; an ignored late order stays in it."""
    ds = make_dataset(
        stock=[StockSnapshot("A1", MON - D(days=70), 1000.0)],
        links=[SupplierLink("A1", "S1", quota_pct=50), SupplierLink("A1", "S2", quota_pct=50, priority=2)],
        orders=[OrderLine("L1", "A1", "S1", MON - D(days=5), 200, qty_open=200, order_type=OrderType.FIRM),
                OrderLine("L0", "A1", "S1", MON - D(days=60), 300, qty_open=120, order_type=OrderType.FIRM),   # old, still open
                OrderLine("R0", "A1", "S1", MON - D(days=4), 250, qty_open=0),                                  # settled
                OrderLine("R2", "A1", "S2", MON - D(days=3), 100, qty_open=30, order_type=OrderType.FIRM)],
        receipts=[Receipt("R1", "A1", MON - D(days=4), 250, supplier_id="S1")])
    r = run(ds, no_cbn=True, horizon_days=5)
    lanes = {l.supplier_id: l for l in r.lanes}
    assert lanes["S1"].backlog_qty == 320 and lanes["S1"].backlog_ordered == 500 and lanes["S1"].backlog_received == 180
    assert lanes["S2"].backlog_qty == 30 and r.kpis["backlog_qty"] == 350
    assert sum(r.orders_firm) == 0 and sum(r.plan) == 0                      # nothing counted in the future
    assert not [a for a in r.alerts if a.alert_type == AlertType.BACKLOG]   # shown on the lane, never alerted
    ds.flags = [CellFlag("A1", "S1", MON - D(days=5), "order_ignored")]      # the past is never ignored
    assert run(ds, no_cbn=True, horizon_days=5).kpis["backlog_qty"] == 350


def test_reference_day_receipts_cover_the_firm_orders_of_the_day():
    ds = make_dataset(links=[SupplierLink("A1", "S1", moq=100, pack_qty=50, priority=1), SupplierLink("A1", "S2", priority=2)],
                      orders=[OrderLine("F1", "A1", "S1", MON, 600, order_type=OrderType.FIRM),
                              OrderLine("F2", "A1", "S2", MON, 1000, order_type=OrderType.FIRM)],
                      receipts=[Receipt("R1", "A1", MON, 400, supplier_id="S1"), Receipt("R2", "A1", MON, 150, supplier_id="S2")])
    r = run(ds, no_cbn=True, horizon_days=3)
    i = r.dates.index(MON)
    lanes = {l.supplier_id: l for l in r.lanes}
    assert lanes["S1"].orders_firm[i] == 200 and lanes["S2"].orders_firm[i] == 850
    assert r.orders_firm[i] == 1050 and r.receipts[i] == 550 and r.plan[i] == 1050
    assert r.stock_erp[i] == 1000 + 550 + 1050 - 200
    ds.plan = [PlanCell("A1", "S1", MON, 600)]
    assert run(ds, no_cbn=True, horizon_days=3).plan[i] == 600 + 850


def test_lanes_are_the_active_links_only_and_other_flows_are_reported():
    """The supply table shows the active links (priority order) and nothing else : the ERP flows of
    another supplier stay out of the lanes, of the stocks and of the backlog, and are listed in an
    UNLINKED_FLOWS alert (the status views and the search still show them).  A supplier holding typed
    plan cells keeps a lane « hors lien » ; a flow without supplier rides the first lane."""
    wed = MON + D(days=2)
    ds = make_dataset(links=[SupplierLink("A1", "S2", priority=1), SupplierLink("A1", "S1", priority=2),
                             SupplierLink("A1", "S9", priority=3, active=False)],
                      suppliers=[Supplier("S1", "Supplier 1"), Supplier("S2", "Supplier 2"), Supplier("S9", "Other")],
                      orders=[OrderLine("F1", "A1", "S9", wed, 10, order_type=OrderType.FIRM),
                              OrderLine("F0", "A1", "S9", MON - D(days=3), 30, order_type=OrderType.FIRM),
                              OrderLine("F2", "A1", None, wed, 7, order_type=OrderType.FIRM)],
                      receipts=[Receipt("R1", "A1", MON - D(days=1), 40, supplier_id="S9")])
    r = run(ds, no_cbn=True, horizon_days=5)
    i = r.dates.index(MON)
    assert [l.supplier_id for l in r.lanes] == ["S2", "S1"] and all(l.linked for l in r.lanes)
    assert r.orders_firm[i + 2] == 7 and r.lanes[0].orders_firm[i + 2] == 7      # anonymous order on the first lane
    assert sum(r.receipts) == 0 and r.kpis["backlog_qty"] == 0
    u = r.unlinked
    assert [x.supplier_id for x in u] == ["S9"] and u[0].orders_open == 1 and u[0].open_qty == 10
    assert u[0].orders_late == 1 and u[0].late_qty == 30 and u[0].receipts == 1
    alert = next(a for a in r.alerts if a.alert_type.value == "UNLINKED_FLOWS")
    assert "S9 Other" in alert.message and alert.severity.value == "info" and r.kpis["unlinked_flows"] == 3
    assert r.kpis["data_issues"] == ["UNLINKED_FLOWS"] and r.kpis["status"] != "critical"
    # typed cells of a supplier without active link : his lane stays, flagged
    ds.plan = [PlanCell("A1", "S9", wed, 25)]
    r2 = run(ds, no_cbn=True, horizon_days=5)
    assert [(l.supplier_id, l.linked) for l in r2.lanes] == [("S2", True), ("S1", True), ("S9", False)]
    assert r2.plan[i + 2] == 7 + 25 and r2.lanes[2].orders_firm[i + 2] == 10   # the lane exists : its ERP flows show again
    # no active link at all : no lane, the ERP flows are reported, the stock has no supply
    r1 = run(make_dataset(links=[], orders=[OrderLine("F1", "A1", "S9", wed, 10, order_type=OrderType.FIRM)]), no_cbn=True, horizon_days=5)
    assert r1.lanes == [] and r1.kpis["lanes"] == 0 and r1.orders_firm[i + 2] == 0 and r1.unlinked[0].supplier_id == "S9"
    assert {a.alert_type.value for a in r1.alerts} >= {"MISSING_DATA", "UNLINKED_FLOWS"}


def test_adjustments_correct_the_reference_stock_and_the_past_is_projected_forward():
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=5), 2000.0)],
                      consumption=[ConsumptionLine("A1", MON - D(days=k), 200.0) for k in (1, 2, 3, 4)],
                      receipts=[Receipt("R1", "A1", MON - D(days=2), 500)],
                      adjustments=[AdjustCell("A1", MON - D(days=6), -300.0), AdjustCell("A1", MON + D(days=2), 40.0)])
    r = run(ds, no_cbn=True, horizon_days=5)
    i = r.dates.index(MON)
    assert r.dates[0] == MON - D(days=5)
    assert r.reference_correction == -300 and r.kpis["stock_reference"] == 1700 and r.kpis["stock_on_hand"] == 2000
    assert r.kpis["init_date"] == (MON - D(days=5)).isoformat()
    assert list(r.stock_erp[i - 5:i]) == [1700, 1500, 1300, 1600, 1400]
    assert list(r.stock_plan[i - 5:i]) == list(r.stock_erp[i - 5:i])
    assert r.stock_plan[i] == 1200 and r.stock_plan[i + 2] == 1200 - 200 - 200 + 40
    assert r.adjustments[i + 2] == 40


def test_one_initialisation_date_for_everybody_never_in_the_future():
    from appro.engine.models import DatasetError
    ds = make_dataset(articles=[Article("A1", "W"), Article("A2", "W2")],
                      stock=[StockSnapshot("A1", MON - D(days=1), 10.0), StockSnapshot("A2", MON - D(days=2), 10.0)])
    with pytest.raises(DatasetError, match="plusieurs dates"):
        run(ds)
    with pytest.raises(DatasetError, match="postérieure"):
        run(make_dataset(stock=[StockSnapshot("A1", MON + D(days=1), 10.0)]))
    r = run(make_dataset(stock=[]))
    assert r.dates[0] == MON and r.kpis["stock_reference"] == 0


def test_display_start_follows_history_weeks_but_never_the_point_zero():
    from appro.api.presenters import display_start
    old = make_dataset(stock=[StockSnapshot("A1", MON - D(days=30), 100.0)])
    assert display_start(run_mrp(old, EngineParams(as_of=MON + D(days=2), horizon_days=10, history_weeks=2))) == MON - D(weeks=2)
    assert display_start(run_mrp(old, EngineParams(as_of=MON + D(days=2), horizon_days=10, history_weeks=0))) == MON
    young = make_dataset(stock=[StockSnapshot("A1", MON - D(days=3), 100.0)])
    assert display_start(run_mrp(young, EngineParams(as_of=MON + D(days=2), horizon_days=10, history_weeks=2))) == MON - D(days=3)


def test_lost_demand_cumulated_shortage_and_first_stockout():
    """The unserved demand is lost ; the cells show its running sum, reset once the stock is positive."""
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 500.0)],
                      orders=[OrderLine("F1", "A1", "S1", MON + D(days=4), 1000, order_type=OrderType.FIRM)])
    r = run(ds, no_cbn=True, horizon_days=6, as_of=MON)
    i = r.dates.index(MON)
    # the floor cannot act : the first deliverable day is tomorrow, the shortage starts Wednesday
    assert list(r.stock_erp[i:i + 5]) == [300, 100, 0, 0, 800]
    assert list(r.shortage_erp[i:i + 5]) == [0, 0, 100, 200, 0]
    assert list(r.shortage_erp_cum[i:i + 5]) == [0, 0, 100, 300, 0]
    assert r.kpis["first_stockout_erp"] == (MON + D(days=2)).isoformat() and r.kpis["max_shortage_erp"] == 300
    assert min(r.stock_erp) >= 0 and r.kpis["lead_time_days"] == 5


def test_coverage_days_in_working_days():
    index = DayIndex(MON, MON + D(days=13))
    demand = np.array([100 if CAL.is_working_day(d) else 0 for d in index.dates], dtype=float)
    cov = coverage_days(np.full(index.n, 250.0), demand, index, CAL)
    assert cov[0] == 2 and cov[4] == 2                      # Friday : Monday and Tuesday of the next week
    assert coverage_days(np.full(index.n, 200.0), demand, index, CAL)[0] == 2   # an exact match is covered
    assert coverage_days(np.full(index.n, -5.0), demand, index, CAL)[0] == 0


# ------------------------------------------------------------------ proposals
def test_proposals_respect_moq_pack_lead_time_and_delivery_days():
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 2600.0)])
    r = run(ds, horizon_days=40)
    assert r.proposals
    p = r.proposals[0]
    assert p.qty >= 100 and p.qty % 50 == 0
    assert p.delivery_date.isoweekday() in (1, 2, 3, 4, 5)
    assert not p.urgent and CAL.working_days_between(p.order_date, p.delivery_date) == 5 and p.lead_time_days == 5
    assert p.delivery_date > MON
    assert max(r.shortage_plan[r.dates.index(MON):]) == 0
    assert r.kpis["proposal_count"] == len(r.proposals)
    assert sum(r.lanes[0].supply_proposed) == sum(r.supply_proposed) > 0
    # the link's own lead time wins over the supplier's
    r2 = run(make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 2600.0)],
                          links=[SupplierLink("A1", "S1", moq=100, pack_qty=50, lead_time_days=8)]), horizon_days=40)
    assert r2.proposals[0].lead_time_days == 8 and r2.kpis["lead_time_days"] == 8


def test_proposals_supplier_delivery_weekdays_and_quota():
    links = [SupplierLink("A1", "S1", moq=100, pack_qty=1, lead_time_days=2, quota_pct=50, priority=1),
             SupplierLink("A1", "S2", moq=100, pack_qty=1, lead_time_days=2, quota_pct=50, priority=2)]
    ds = make_dataset(links=links, stock=[StockSnapshot("A1", MON - D(days=1), 300.0)])
    r = run(ds, horizon_days=60)
    by_sup = {}
    for p in r.proposals:
        by_sup[p.supplier_id] = by_sup.get(p.supplier_id, 0) + p.qty
        if p.supplier_id == "S2":
            assert p.delivery_date.isoweekday() in (2, 4)
    assert set(by_sup) == {"S1", "S2"}
    assert 0.3 < by_sup["S1"] / sum(by_sup.values()) < 0.7
    lanes = {l.supplier_id: l for l in r.lanes}
    assert abs(sum(lanes["S1"].supply_proposed) - by_sup["S1"]) < 1e-6


HV_SAT = dt.date(2026, 10, 17)


def hv_dataset(stock: float = 3182.0, cap_day: float | None = None, cap_week: float | None = None, lead: int = 10):
    """The worked example of the design review : 420 / working day, Thursday supplier, MOQ = UM = 1 440,
    coverage 5 working days (target 2 100), lead time 10 working days, stock 3 182 on Saturday evening."""
    monday = dt.date(2026, 10, 19)
    return make_dataset(
        articles=[Article("A1", "HV", "PCE", coverage_target_days=5, alert_red_days=2, alert_yellow_days=5, overstock_days=30)],
        suppliers=[Supplier("S1", "Thursday", delivery_weekdays=frozenset({4}), lead_time_days=lead)],
        links=[SupplierLink("A1", "S1", moq=1440, pack_qty=1440, max_qty_per_delivery=cap_day, max_qty_per_week=cap_week)],
        bom=[BomLine("P1", "A1", 1.0)], pdp=[PdpLine("P1", monday + D(weeks=k), 2100.0) for k in range(-2, 12)],
        stock=[StockSnapshot("A1", HV_SAT, stock)])


def hv(ds: Dataset, **params):
    p = dict(as_of=dt.date(2026, 10, 18), horizon_days=60)
    p.update(params)
    return run_mrp(ds, EngineParams(**p)).articles["A1"]


def test_floor_inside_the_lead_time_then_full_target():
    """Inside the lead time an urgent proposal only avoids the shortage (one lot), the first tenable
    delivery brings the stock back to the target (three lots) ; no shortage anywhere."""
    r = hv(hv_dataset())
    got = [(p.delivery_date.isoformat(), p.qty, p.urgent, p.floor) for p in r.proposals[:4]]
    assert got == [("2026-10-22", 1440, True, True), ("2026-10-29", 1440, True, True),
                   ("2026-11-05", 4320, False, False), ("2026-11-12", 1440, False, False)]
    assert max(r.shortage_plan[r.dates.index(dt.date(2026, 10, 19)):]) == 0
    assert "Plancher" in r.proposals[0].reason and "retour à la cible" in r.proposals[2].reason
    assert r.proposals[0].order_date == dt.date(2026, 10, 8)
    # the urgent alert names the floor
    assert any(a.alert_type == AlertType.URGENT_PROPOSAL and "plancher" in a.message for a in r.alerts)


def test_tolerance_under_the_target():
    """A deficit smaller than 15 % of the target waits for the next delivery ; 0 % is the strict rule."""
    r = hv(hv_dataset(stock=9655.0))                       # 2 095 projected on 11/11 for a target of 2 100
    assert r.proposals[0].delivery_date == dt.date(2026, 11, 12) and r.proposals[0].qty == 2880
    strict = hv(hv_dataset(stock=9655.0), target_tolerance_pct=0)
    assert strict.proposals[0].delivery_date == dt.date(2026, 11, 5) and strict.proposals[0].qty == 1440
    assert "85 %" in r.proposals[0].reason


def test_cap_per_delivery_slides_the_excess_to_the_next_delivery():
    r = hv(hv_dataset(cap_day=2880.0))
    got = [(p.delivery_date.isoformat(), p.qty, p.capped) for p in r.proposals[:4]]
    assert got == [("2026-10-22", 1440, False), ("2026-10-29", 1440, False), ("2026-11-05", 2880, False), ("2026-11-12", 2880, False)]
    assert "première livraison possible le 2026-11-12" in r.proposals[3].reason   # the 05/11 is full : the rest slides
    assert max(r.shortage_plan[r.dates.index(dt.date(2026, 10, 19)):]) == 0 and not r.cap_shortfalls
    # a cap below the lot : the proposal itself is truncated and flagged
    r2 = hv(hv_dataset(cap_day=1440.0))
    p = next(x for x in r2.proposals if x.delivery_date == dt.date(2026, 11, 5))
    assert p.qty == 1440 and p.capped and "plafond par livraison" in p.reason and r2.kpis["capped_proposal_count"] >= 1


def test_weekly_cap_below_the_need_leaves_a_shortage_and_an_alert():
    """A weekly cap under the weekly requirement : the days are full, the shortage stays and is alerted."""
    r = hv(hv_dataset(cap_week=1440.0))
    assert all(p.qty <= 1440 for p in r.proposals)
    i = r.dates.index(dt.date(2026, 11, 5))
    assert max(r.shortage_plan[i:]) > 0 and r.cap_shortfalls
    assert any(a.alert_type == AlertType.CAP_REACHED for a in r.alerts)
    # firm orders count in the weekly cap : a week already served by a firm order gets no proposal
    ds = hv_dataset(cap_week=2880.0)
    ds.orders = [OrderLine("F1", "A1", "S1", dt.date(2026, 11, 5), 2880, order_type=OrderType.FIRM)]
    r2 = hv(ds)
    assert not [p for p in r2.proposals if p.delivery_date == dt.date(2026, 11, 5)]


def test_refusal_blocks_the_day_only_and_the_need_slides_to_the_next_delivery_day():
    ds = hv_dataset()
    ds.flags = [CellFlag("A1", "S1", dt.date(2026, 10, 22), "proposal_refused", qty=1440)]
    r = hv(ds)
    assert r.proposals[0].delivery_date == dt.date(2026, 10, 29) and r.proposals[0].qty == 2880
    i = r.dates.index(dt.date(2026, 10, 28))
    assert r.shortage_plan[i] == 178 and r.shortage_plan_cum[i] == 178        # the accepted shortage is shown
    assert r.proposals_ignored and r.proposals_ignored[0].delivery_date == dt.date(2026, 10, 22) and r.proposals_ignored[0].ignored
    assert r.kpis["refused_proposals"] == 1
    # a Tuesday / Thursday supplier : refusing Tuesday leaves Thursday proposable
    ds2 = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 600.0)],
                       links=[SupplierLink("A1", "S2", moq=100, pack_qty=50, lead_time_days=1)])
    base = run(ds2, horizon_days=21)
    tue = next(p for p in base.proposals if p.delivery_date.isoweekday() == 2)
    ds2.flags = [CellFlag("A1", "S2", tue.delivery_date, "proposal_refused", qty=tue.qty)]
    r2 = run(ds2, horizon_days=21)
    days = {p.delivery_date for p in r2.proposals}
    assert tue.delivery_date not in days and tue.delivery_date + D(days=2) in days


def test_quota_split_every_need_and_a_refused_or_capped_supplier_hands_its_share_over():
    links = [SupplierLink("A1", "S1", moq=1, pack_qty=10, lead_time_days=2, quota_pct=70, priority=1),
             SupplierLink("A1", "S2", moq=1, pack_qty=10, lead_time_days=2, quota_pct=30, priority=2)]
    ds = make_dataset(links=links, suppliers=[Supplier("S1", "Supplier 1"), Supplier("S2", "Supplier 2")],
                      stock=[StockSnapshot("A1", MON - D(days=1), 300.0)])
    r = run(ds, horizon_days=42)
    first_day = min(p.delivery_date for p in r.proposals)
    first = {p.supplier_id: p for p in r.proposals if p.delivery_date == first_day}
    assert set(first) == {"S1", "S2"}
    s1, s2 = first["S1"], first["S2"]
    assert s1.qty / (s1.qty + s2.qty) == pytest.approx(0.7, abs=0.03) and "part 70 %" in s1.reason and "part 30 %" in s2.reason
    tot = {sid: sum(p.qty for p in r.proposals if p.supplier_id == sid) for sid in ("S1", "S2")}
    assert tot["S1"] / (tot["S1"] + tot["S2"]) == pytest.approx(0.7, abs=0.03)
    # refuse S1 on the first day : S2 takes the whole need of that day
    ds.flags = [CellFlag("A1", "S1", first_day, "proposal_refused", qty=s1.qty)]
    r2 = run(ds, horizon_days=42)
    that_day = [p for p in r2.proposals if p.delivery_date == first_day]
    assert that_day and all(p.supplier_id == "S2" for p in that_day)
    assert sum(p.qty for p in that_day) >= s1.qty + s2.qty - 10
    # cap S1 to 10 per delivery : the truncated share goes to S2
    links[0].max_qty_per_delivery = 10.0
    r3 = run(make_dataset(links=links, suppliers=ds.suppliers, stock=[StockSnapshot("A1", MON - D(days=1), 300.0)]), horizon_days=42)
    day3 = {p.supplier_id: p for p in r3.proposals if p.delivery_date == first_day}
    assert day3["S1"].qty == 10 and day3["S1"].capped and day3["S2"].qty >= s1.qty + s2.qty - 10 - 10
    # priority policy : everything to the priority-1 supplier
    art = Article("A1", "Widget", "PCE", coverage_target_days=5, sourcing_policy="priority")
    r4 = run(make_dataset(articles=[art], links=links, suppliers=ds.suppliers, stock=[StockSnapshot("A1", MON - D(days=1), 300.0)]), horizon_days=42)
    assert {p.supplier_id for p in r4.proposals} == {"S1"}


# ------------------------------------------------------------------ alerts
def test_alert_levels():
    r = run(make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 100000.0)]), horizon_days=60)
    assert any(a.alert_type == AlertType.OVERSTOCK for a in r.alerts)
    r = run(make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 350.0)]), horizon_days=60)
    low = [a for a in r.alerts if a.alert_type == AlertType.LOW_COVERAGE]
    assert low and low[0].severity.value == "critical"  # ERP flows run out within 2 working days
    r = run(make_dataset(stock=[]), horizon_days=10)
    assert any(a.alert_type == AlertType.MISSING_DATA for a in r.alerts)


def test_plan_stockout_is_information_and_erp_stockout_is_a_kpi_only():
    """A shortage the plan cannot fix is reported on the plan scenario only, as information ; the ERP
    scenario stockout is a KPI (column), never an alert."""
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 100.0)],
                      orders=[OrderLine("F1", "A1", "S1", MON + D(days=3), 2000, order_type=OrderType.FIRM)])
    r = run(ds, horizon_days=12)
    stockouts = [a for a in r.alerts if a.alert_type == AlertType.STOCKOUT]
    assert [(a.scope, a.severity.value) for a in stockouts] == [("plan", "info")]
    assert r.kpis["first_stockout_erp"] == r.kpis["first_stockout_plan"] == MON.isoformat()
    ds2 = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 500.0)],
                       orders=[OrderLine("F1", "A1", "S1", MON + D(days=3), 2000, order_type=OrderType.FIRM)],
                       links=[SupplierLink("A1", "S1", moq=100, pack_qty=50, lead_time_days=30)])
    ds2.plan = [PlanCell("A1", "S1", MON + D(days=2), 2000), PlanCell("A1", "S1", MON + D(days=3), 0)]
    r2 = run(ds2, horizon_days=12)
    assert not [a for a in r2.alerts if a.alert_type == AlertType.STOCKOUT] and r2.kpis["first_stockout_plan"] is None
    assert r2.kpis["first_stockout_erp"] == (MON + D(days=2)).isoformat()


def test_program_impact():
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 700.0)],
                      orders=[OrderLine("F1", "A1", "S1", MON + D(days=4), 5000, order_type=OrderType.FIRM)],
                      links=[SupplierLink("A1", "S1", moq=100, pack_qty=50, lead_time_days=30)])
    res = run_mrp(ds, EngineParams(as_of=MON, horizon_days=13, target_tolerance_pct=100))
    imp = res.program_impact
    assert imp["weeks"][0] == "2026-W39"
    p = next(x for x in imp["programs"] if x["program_id"] == "P1")
    assert p["planned"][0] == 500
    assert p["feasible"]["onhand"][0] == 350 and p["feasible"]["erp"][0] == 450
    assert p["limiting"]["onhand"][0][0]["article_id"] == "A1"
    assert p["first_impact"]["onhand"] == "2026-W39" and p["first_impact"]["erp"] == "2026-W39"


def test_weekly_article_parameters():
    week2 = MON + D(days=7)
    art = Article("A1", "Widget", "PCE", coverage_target_days=5, alert_red_days=2, alert_yellow_days=5, overstock_days=30)
    art.weekly = {"2026-W40": {"coverage_target_days": 10}}
    assert art.param_at("coverage_target_days", week2) == 10 and art.param_at("coverage_target_days", MON) == 5
    r = run(make_dataset(articles=[art], stock=[StockSnapshot("A1", MON - D(days=1), 100000.0)]))
    i = r.dates.index(MON)
    assert r.target_stock[i] == 1000                      # 5 working days of 200
    assert r.target_stock[r.dates.index(week2)] == 2000  # 10 working days


def test_expression_evaluator():
    assert evaluate("1200") == 1200
    assert evaluate(" 1 200,5 ") == 1200.5
    assert evaluate("(100+50)*3-20/4") == 445
    assert evaluate("-500") == -500
    assert evaluate("=2*(3+4)") == 14
    for bad in ("abc", "2**8", "__import__('os')", "1/0", ""):
        with pytest.raises(ValueError):
            evaluate(bad)


def test_ignored_firm_orders_leave_the_erp_scenario_and_the_plan():
    wed, fri = MON + D(days=2), MON + D(days=4)
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=3), 1000.0)],
                      orders=[OrderLine("F1", "A1", "S1", wed, 1000, qty_open=400, order_type=OrderType.FIRM),
                              OrderLine("F2", "A1", "S1", fri, 800, order_type=OrderType.FIRM),
                              OrderLine("F0", "A1", "S1", MON - D(days=2), 300, qty_open=0, order_type=OrderType.FIRM)])
    r = run(ds, no_cbn=True)
    i = r.dates.index(MON)
    lane = r.lanes[0]
    assert lane.orders_firm_ordered[i + 2] == 1000 and lane.orders_firm_open[i + 2] == 400
    assert lane.orders_firm_ordered[i - 2] == 300 and lane.orders_firm_open[i - 2] == 0
    assert r.orders_firm[i + 2] == 400 and r.plan[i + 2] == 400
    ds.flags = [CellFlag("A1", "S1", wed, "order_ignored"), CellFlag("A1", "S1", MON - D(days=2), "order_ignored")]
    r2 = run(ds, no_cbn=True)
    assert r2.orders_firm[i + 2] == 0 and r2.plan[i + 2] == 0 and r2.orders_firm[i + 4] == 800
    assert r2.lanes[0].orders_ignored[i + 2] and not r2.lanes[0].orders_ignored[i + 4]
    assert r2.lanes[0].orders_firm_ordered[i + 2] == 1000
    assert r2.stock_erp[i + 2] == r.stock_erp[i + 2] - 400 and r2.kpis["ignored_order_days"] == 1
    assert not r2.lanes[0].orders_ignored[i - 2]
    typed = make_dataset(stock=list(ds.stock), orders=ds.orders)
    typed.flags, typed.plan = list(ds.flags), [PlanCell("A1", "S1", wed, 250)]
    r3 = run(typed, no_cbn=True)
    assert r3.orders_firm[i + 2] == 0 and r3.plan[i + 2] == 250


def test_desadv_shown_in_the_receipt_row_until_received_or_hidden():
    mon1, tue1, wed1 = MON - D(days=7), MON - D(days=6), MON - D(days=5)
    ds = make_dataset(links=[SupplierLink("A1", "S1", moq=100, pack_qty=50, priority=1), SupplierLink("A1", "S2", priority=2)],
                      stock=[StockSnapshot("A1", MON - D(days=10), 1000.0)],
                      receipts=[Receipt("R1", "A1", tue1, 300, supplier_id="S1", packing_slip="BL-1"),
                                Receipt("R2", "A1", wed1, 100, supplier_id="S1", packing_slip="BL-9")])
    ds.desadv = [DesadvLine("D1|1", "A1", "S1", "BL-1", mon1, 300, state="Traité", final_processing="OK"),
                 DesadvLine("D2|1", "A1", "S1", "BL-2", wed1, 500, state="Traité", final_processing="OK"),
                 DesadvLine("D3|1", "A1", "S1", "BL-3", wed1, 50, state="Erreur", final_processing="Non traité"),
                 DesadvLine("D4|1", "A1", "S1", "BL-4", MON, 70, state="Annulé"),
                 DesadvLine("D5|1", "A1", "S2", "BL-5", MON, 20, state="Traité", final_processing="OK")]
    r = run(ds, no_cbn=True, horizon_days=5)
    i = r.dates.index(MON)
    lanes = {l.supplier_id: l for l in r.lanes}
    s1 = lanes["S1"]
    assert s1.desadv_open[i - 7] == 0 and s1.desadv_open[i - 5] == 550 and s1.desadv_open[i] == 0
    assert bool(s1.desadv_ko[i - 5]) and not bool(s1.desadv_ko[i - 6])
    assert not bool(s1.receipts_ko[i - 6]) and bool(s1.receipts_ko[i - 5])
    assert lanes["S2"].desadv_open[i] == 20 and not bool(lanes["S2"].desadv_ko[i])
    assert [d.received for d in s1.desadv] == [True, False, False]
    assert sum(r.receipts) == 400 and r.stock_erp[i - 5] == r.stock_erp[i - 6] + 100 - r.demand[i - 5]
    ds.flags = [CellFlag("A1", "S1", wed1, "desadv_hidden", qty=550)]
    s1b = {l.supplier_id: l for l in run(ds, no_cbn=True, horizon_days=5).lanes}["S1"]
    assert s1b.desadv_open[i - 5] == 0 and [d.hidden for d in s1b.desadv][1:3] == [True, True]


def test_receipt_lines_carry_the_delivery_note_and_a_missing_bl_is_never_matched():
    tue1, wed1 = MON - D(days=6), MON - D(days=5)
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=10), 1000.0)],
                      receipts=[Receipt("R1", "A1", tue1, 300, supplier_id="S1", ref="PO-1", packing_slip="BL-1"),
                                Receipt("R2", "A1", tue1, 50, supplier_id="S1", ref="PO-2", packing_slip=""),
                                Receipt("R3", "A1", wed1, 10, supplier_id="S1", packing_slip="   ")])
    ds.desadv = [DesadvLine("D1|1", "A1", "S1", "BL-1", tue1, 300, state="Traité", final_processing="OK"),
                 DesadvLine("D2|1", "A1", "S1", "", wed1, 40, state="Traité", final_processing="OK")]
    r = run(ds, no_cbn=True, horizon_days=5)
    i = r.dates.index(MON)
    s1 = {l.supplier_id: l for l in r.lanes}["S1"]
    assert [(x.receipt_id, x.packing_slip, x.purch_id, x.processed) for x in s1.receipt_lines] == [
        ("R1", "BL-1", "PO-1", True), ("R2", "", "PO-2", False), ("R3", "", "", False)]
    assert bool(s1.receipts_ko[i - 6]) and bool(s1.receipts_ko[i - 5])
    assert [(d.packing_slip, d.received) for d in s1.desadv] == [("BL-1", True), ("", False)]
    assert s1.desadv_open[i - 5] == 40 and not bool(s1.desadv_ko[i - 5])


def test_receipt_statuses_transit_received_booked():
    mon1, tue1, wed1, thu1 = MON - D(days=7), MON - D(days=6), MON - D(days=5), MON - D(days=4)
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=10), 1000.0)],
                      receipts=[Receipt("R1", "A1", tue1, 300, supplier_id="S1", packing_slip="BL-1 | BL-2", status="Reçu"),
                                Receipt("R2", "A1", tue1, 50, supplier_id="S1", packing_slip="BL-3", status="Comptabilisé"),
                                Receipt("J1", "A1", wed1, 500, supplier_id="S1", packing_slip="BL-4", status="En transit"),
                                Receipt("J2", "A1", wed1, 60, supplier_id="S1", packing_slip="BL-9", status="En transit"),
                                Receipt("R3", "A1", thu1, 10, supplier_id="S1", packing_slip="", status="Comptabilisé")])
    ds.desadv = [DesadvLine("D1|1", "A1", "S1", "BL-1", mon1, 150, state="Traité", final_processing="OK"),
                 DesadvLine("D2|1", "A1", "S1", "BL-2", mon1, 150, state="Traité", final_processing="OK"),
                 DesadvLine("D3|1", "A1", "S1", "BL-3", mon1, 50, state="Traité", final_processing="OK"),
                 DesadvLine("D4|1", "A1", "S1", "BL-4", mon1, 500, state="Traité", final_processing="OK"),
                 DesadvLine("D5|1", "A1", "S1", "BL-5", thu1, 40, state="Créé", final_processing="Non traité")]
    r = run(ds, no_cbn=True, horizon_days=5)
    i = r.dates.index(MON)
    s1 = {l.supplier_id: l for l in r.lanes}["S1"]
    assert r.receipts[i - 6] == 350 and r.receipts[i - 5] == 0 and r.receipts[i - 4] == 10 and sum(r.receipts) == 360
    assert s1.desadv_open[i - 5] == 560 and bool(s1.desadv_ko[i - 5]) and s1.desadv_open[i - 4] == 40 and bool(s1.desadv_ko[i - 4])
    journals = [d for d in s1.desadv if d.source == "journal"]
    assert [(d.desadv_id, d.packing_slip, d.processed, d.state) for d in journals] == [("J1", "BL-4", True, "Journal d'arrivée non validé"), ("J2", "BL-9", False, "Journal d'arrivée non validé")]
    by_bl = {d.packing_slip: d for d in s1.desadv if d.source == "desadv"}
    assert [(bl, by_bl[bl].received, by_bl[bl].journaled) for bl in ("BL-1", "BL-2", "BL-3", "BL-4", "BL-5")] == [
        ("BL-1", True, False), ("BL-2", True, False), ("BL-3", True, False), ("BL-4", False, True), ("BL-5", False, False)]
    assert [(x.receipt_id, x.processed, x.booked, x.status) for x in s1.receipt_lines] == [
        ("R1", True, False, "Reçu"), ("R2", True, True, "Comptabilisé"), ("R3", False, True, "Comptabilisé")]
    assert not bool(s1.receipts_ko[i - 6]) and bool(s1.receipts_ko[i - 4])
    assert not bool(s1.receipts_booked[i - 6]) and bool(s1.receipts_booked[i - 4])
    assert not Receipt("R9", "A1", tue1, 1, status="Enregistré").in_transit and not Receipt("R9", "A1", tue1, 1, status="Enregistré").booked


def test_three_statuses_from_the_stock_at_date_and_the_order_dates():
    """critical : coverage « à date » ≤ red + 1 working day, or an order date already past ; to order : an
    order date within five working days, or coverage ≤ orange with nothing landing before the run-out ;
    ok otherwise.  No common horizon is involved ; the data issues never change the status."""
    # 1. coverage at date (stock of yesterday, no inflow) : 350 serves one working day of 200 → ≤ red (2) + 1
    r = run(make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 350.0)]), horizon_days=30)
    assert r.kpis["status"] == "critical" and r.kpis["coverage_at_date_days"] == 1 and r.kpis["runout_date"] == (MON + D(days=1)).isoformat()
    low = next(a for a in r.alerts if a.alert_type == AlertType.LOW_COVERAGE)
    assert low.severity == Severity.CRITICAL and "seuil rouge" in low.message
    # 2. order date already past : 1 500 covers 7 working days but the lead time is 10 → floor proposal, urgent
    late = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 1500.0)],
                        links=[SupplierLink("A1", "S1", moq=100, pack_qty=50, lead_time_days=10)])
    r2 = run(late, horizon_days=40)
    assert r2.kpis["status"] == "critical" and r2.kpis["order_due_late"] and r2.kpis["order_due_supplier_id"] == "S1"
    assert any(a.alert_type == AlertType.URGENT_PROPOSAL and "date de commande est passée" in a.message for a in r2.alerts)
    assert not any(a.alert_type == AlertType.LOW_COVERAGE for a in r2.alerts)
    # 3. order to place within five working days : 2 400 covers 12 days, the CBN orders this week (lead 5)
    soon = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 2400.0)])
    r3 = run(soon, horizon_days=40)
    assert r3.kpis["status"] == "to_order" and r3.kpis["coverage_at_date_days"] == 12
    due = next(a for a in r3.alerts if a.alert_type == AlertType.ORDER_DUE)
    assert due.severity == Severity.WARNING and "S1" in due.message
    assert MON <= dt.date.fromisoformat(r3.kpis["order_due_date"]) <= MON + D(days=7) and r3.kpis["order_due_qty"] > 0
    assert not r3.kpis["order_due_late"]
    # 4. coverage ≤ orange (5) with nothing landing before the run-out → to order ; a firm order before it → ok
    orange = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 900.0)],
                          links=[SupplierLink("A1", "S1", moq=100, pack_qty=50, lead_time_days=3)])   # lead 5 : the order date would be past
    r4 = run(orange, no_cbn=True, horizon_days=30)
    assert r4.kpis["coverage_at_date_days"] == 4 and r4.kpis["status"] == "to_order"
    assert any(a.alert_type == AlertType.LOW_COVERAGE and a.severity == Severity.WARNING for a in r4.alerts)
    orange.orders = [OrderLine("F1", "A1", "S1", MON + D(days=2), 5000, order_type=OrderType.FIRM)]
    r5 = run(orange, no_cbn=True, horizon_days=30)
    assert not any(a.alert_type == AlertType.LOW_COVERAGE for a in r5.alerts) and r5.kpis["status"] == "ok"
    assert r5.kpis["next_firm_date"] == (MON + D(days=2)).isoformat() and r5.kpis["next_firm_qty"] == 5000 and r5.kpis["next_firm_supplier_id"] == "S1"
    # 5. data issues are information only
    r6 = run(make_dataset(stock=[]), no_cbn=True, horizon_days=10)
    assert "MISSING_DATA" in r6.kpis["data_issues"] and all(a.severity == Severity.INFO for a in r6.alerts if a.scope == "data")
    # 6. no horizon in the gradation : a far plan stockout stays information whatever its distance
    far = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 10000.0)],
                       links=[SupplierLink("A1", "S1", moq=1, pack_qty=1, lead_time_days=3, max_qty_per_week=1.0)],
                       pdp=[PdpLine("P1", MON + D(weeks=k), 500.0) for k in range(0, 30)])
    r7 = run(far, horizon_days=200)
    plan = [a for a in r7.alerts if a.alert_type == AlertType.STOCKOUT and a.scope == "plan"]
    assert plan and plan[0].severity == Severity.INFO and r7.kpis["overstock"]


def test_refused_proposals_stay_listed_as_ignored_with_their_real_order_date():
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 100.0)],
                      links=[SupplierLink("A1", "S1", moq=1, pack_qty=1, lead_time_days=15)])
    base = run(ds, horizon_days=40)
    first = base.proposals[0]
    assert first.urgent and first.floor and first.order_date < MON and not base.proposals_ignored
    ds.flags = [CellFlag("A1", "S1", first.delivery_date, "proposal_refused", qty=first.qty)]
    r = run(ds, horizon_days=40)
    assert all(p.delivery_date != first.delivery_date for p in r.proposals)
    ign = r.proposals_ignored
    assert ign and all(p.ignored and p.delivery_date == first.delivery_date for p in ign)
    assert any(p.urgent and p.order_date == first.order_date for p in ign)


def test_stock_at_date_and_value_kpis():
    tue = MON - D(days=6)
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=8), 1000.0)], receipts=[Receipt("R1", "A1", tue, 200)])
    ds.prices = {"A1": 2.5}
    r = run(ds, horizon_days=10)
    i = r.dates.index(MON)
    assert r.kpis["stock_at_date"] == pytest.approx(float(r.stock_erp[i - 1])) and r.price == 2.5
    assert r.kpis["stock_value"] == pytest.approx(2.5 * r.stock_erp[i - 1]) and r.kpis["target_value"] == pytest.approx(2.5 * r.target_stock[i])
    r2 = run(make_dataset(stock=[StockSnapshot("A1", MON - D(days=8), 1000.0)]), horizon_days=10)
    assert r2.kpis["price"] is None and r2.kpis["stock_value"] is None


def test_requirement_of_the_next_20_working_days():
    r = run(make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 5000.0)]), horizon_days=60)
    assert r.kpis["demand_next_20wd"] == 4000 and r.kpis["working_days_counted"] == 20
    assert r.kpis["avg_daily_demand_wd"] == 200


def test_programme_scrap_rate_raises_the_weekly_need_before_the_bom():
    base = run(make_dataset(), horizon_days=21)
    lines = [PdpLine("P1", MON + D(weeks=k), 500.0, scrap_pct=10.0 if k >= 1 else 0.0) for k in range(-2, 8)]
    r = run(make_dataset(pdp=lines), horizon_days=21)
    i0, i1 = r.dates.index(MON), r.dates.index(MON + D(weeks=1))
    week = lambda res, i: sum(res.demand[i:i + 7])  # noqa: E731
    assert week(r, i0) == week(base, i0)
    assert week(r, i1) == pytest.approx(week(base, i1) * 1.1) == pytest.approx(1100.0)
