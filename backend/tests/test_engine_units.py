"""Unit tests of the engine building blocks and of the business rules."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pytest

from appro.engine import run_mrp
from appro.engine.calendar import WorkCalendar
from appro.engine.demand import DayIndex, ceil_to_multiple, spread_week
from appro.engine.models import (
    ActualLine,
    AdjustCell,
    AlertType,
    Article,
    BomLine,
    Dataset,
    EngineParams,
    OrderLine,
    OrderType,
    PdpLine,
    PlanCell,
    Program,
    Receipt,
    StockSnapshot,
    Supplier,
    SupplierLink,
)
from appro.engine.projection import coverage_days
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
    assert r.consumed[i] == 500 and r.consumed[i + 1] == 600 and r.required[i] == 0
    assert r.required[i + 2] == 0 and r.required[i + 3] == 0 and r.required[i + 4] == 0
    assert r.required[i + 7] == 200 and r.consumed[i + 7] == 0
    assert r.consumed[i - 3] == 0
    # the single grid row "Besoin" is the sum of both parts
    assert r.demand[i] == 500 and r.demand[i + 7] == 200
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
    assert list(r3.consumed[i3:i3 + 3]) == [2400, 2000, 400] and r3.required[i3 + 3] == 0 and r3.required[i3 + 4] == 0
    r4 = run(ds3, as_of=wed)
    assert r4.consumed[i + 2] == 0 and r4.required[i + 2] == 0
    r5 = run(ds, as_of=wed, production_mode="plan_only")
    assert r5.demand[i] == 200 and r5.demand[i + 2] == 200
    r6 = run(ds, as_of=wed, production_mode="actual_then_plan")
    assert r6.demand[i + 1] == 600 and r6.demand[i + 2] == 200


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
    r = run(ds, horizon_days=10)
    i = r.dates.index(MON)
    # Monday 1000 − 200 = 800 ; Tuesday +50 −20 −200 = 630 ; Wednesday +300 −200 = 730 ; Thursday −200 = 530 ; Friday +600 −200 = 930
    assert list(r.stock_erp[i:i + 5]) == [800, 630, 730, 530, 930]
    assert list(r.stock_plan[i:i + 5]) == [800, 630, 730, 530, 930]          # no cell: plan = ERP
    assert r.orders_forecast[i + 3] == 400 and r.stock_erp[i + 3] == 530
    assert r.kpis["open_firm_qty"] == 900 and r.kpis["open_forecast_qty"] == 400 and r.kpis["plan_qty"] == 900
    assert len(r.lanes) == 1 and r.lanes[0].supplier_id == "S1" and len(r.lanes[0].orders) == 4
    # a typed cell on the forecast day takes it into the plan scenario only
    ds.plan = [PlanCell("A1", "S1", thu, 400)]
    r2 = run(ds, horizon_days=10)
    assert r2.stock_erp[i + 3] == 530 and r2.stock_plan[i + 3] == 930
    assert r2.lanes[0].plan_typed[i + 3] and r2.kpis["plan_cell_count"] == 1


def test_plan_cells_move_split_zero_and_expire():
    wed, fri = MON + D(days=2), MON + D(days=4)
    mon2 = MON + D(days=7)
    ds = make_dataset(orders=[OrderLine("F1", "A1", "S1", wed, 1000, order_type=OrderType.FIRM),
                              OrderLine("F2", "A1", "S1", fri, 800, order_type=OrderType.FIRM)])
    ds.plan = [
        PlanCell("A1", "S1", wed, 0),             # nothing expected Wednesday …
        PlanCell("A1", "S1", fri, 400),           # … 400 Friday (instead of 800) …
        PlanCell("A1", "S1", mon2, 600),          # … 600 next Monday
        PlanCell("A1", "S1", MON + D(days=1), 150),  # stop-gap delivery Tuesday
        PlanCell("A1", "S1", MON - D(days=3), 500),  # expired: ignored
    ]
    r = run(ds, horizon_days=12)
    i = r.dates.index(MON)
    assert r.orders_firm[i + 2] == 1000 and r.orders_firm[i + 4] == 800          # ERP as is
    assert r.plan[i + 1] == 150 and r.plan[i + 2] == 0 and r.plan[i + 4] == 400 and r.plan[i + 7] == 600
    assert sum(r.plan) == 1150 and r.stock_plan[i + 2] == r.stock_erp[i + 2] - 1000 + 150
    assert r.kpis["plan_cell_count"] == 4
    assert r.lanes[0].plan_typed[i + 2] and not r.lanes[0].plan_typed[i + 3]


def test_backlog_is_cumulative_per_supplier_and_ages_out():
    """Past firm orders not covered by receipts over the backlog window: no order matching, no action."""
    ds = make_dataset(
        stock=[StockSnapshot("A1", MON - D(days=10), 1000.0)],   # point zero ten days ago: the past orders exist
        links=[SupplierLink("A1", "S1", quota_pct=50), SupplierLink("A1", "S2", quota_pct=50, priority=2)],
        orders=[OrderLine("L1", "A1", "S1", MON - D(days=5), 200, order_type=OrderType.FIRM),
                OrderLine("L0", "A1", "S1", MON - D(days=60), 300, order_type=OrderType.FIRM),   # too old
                OrderLine("R0", "A1", "S1", MON - D(days=4), 250, qty_open=0),
                OrderLine("R2", "A1", "S2", MON - D(days=3), 100, order_type=OrderType.FIRM)],
        receipts=[Receipt("R1", "A1", MON - D(days=4), 250, supplier_id="S1"),
                  Receipt("R3", "A1", MON - D(days=2), 130, supplier_id="S2")])
    r = run(ds, horizon_days=5)
    i = r.dates.index(MON)
    assert sum(r.orders_firm) == 0 and sum(r.plan) == 0                      # nothing in the future
    assert r.orders_firm_hist[i - 5] == 200 and r.orders_firm_hist[i - 4] == 250 and r.receipts[i - 4] == 250
    lanes = {l.supplier_id: l for l in r.lanes}
    assert lanes["S1"].backlog_ordered == 450 and lanes["S1"].backlog_received == 250 and lanes["S1"].backlog_qty == 200
    assert lanes["S2"].backlog_ordered == 100 and lanes["S2"].backlog_received == 130 and lanes["S2"].backlog_qty == 0
    assert r.kpis["backlog_qty"] == 200
    alerts = [a for a in r.alerts if a.alert_type == AlertType.BACKLOG]
    assert len(alerts) == 1 and "S1 200" in alerts[0].message and alerts[0].scope == "erp"
    # the planner types the quantity where he expects it: plan scenario only
    ds.plan = [PlanCell("A1", "S1", MON + D(days=2), 200)]
    r2 = run(ds, horizon_days=5)
    assert r2.plan[i + 2] == 200 and r2.stock_plan[i + 2] - r2.stock_erp[i + 2] == 200 and r2.kpis["backlog_qty"] == 200
    # shorter window: the backlog ages out
    r3 = run(ds, horizon_days=5, backlog_days=3)
    assert r3.kpis["backlog_qty"] == 0


def test_reference_day_receipts_cover_the_firm_orders_of_the_day():
    ds = make_dataset(orders=[OrderLine("F1", "A1", "S1", MON, 600, order_type=OrderType.FIRM),
                              OrderLine("F2", "A1", "S2", MON, 1000, order_type=OrderType.FIRM)],
                      receipts=[Receipt("R1", "A1", MON, 400, supplier_id="S1"), Receipt("R2", "A1", MON, 150, supplier_id="S2")])
    r = run(ds, horizon_days=3)
    i = r.dates.index(MON)
    lanes = {l.supplier_id: l for l in r.lanes}
    assert lanes["S1"].orders_firm[i] == 200 and lanes["S2"].orders_firm[i] == 850
    assert r.orders_firm[i] == 1050 and r.receipts[i] == 550 and r.plan[i] == 1050
    assert r.stock_erp[i] == 1000 + 550 + 1050 - 200
    # a typed cell that day is taken as is
    ds.plan = [PlanCell("A1", "S1", MON, 600)]
    r2 = run(ds, horizon_days=3)
    assert r2.plan[i] == 600 + 850


def test_lanes_follow_the_active_links_then_the_data():
    ds = make_dataset(links=[SupplierLink("A1", "S2", priority=1), SupplierLink("A1", "S1", priority=2)],
                      orders=[OrderLine("F1", "A1", "S9", MON + D(days=2), 10, order_type=OrderType.FIRM)])
    r = run(ds)
    assert [l.supplier_id for l in r.lanes] == ["S2", "S1", "S9"]
    r1 = run(make_dataset(links=[]))
    assert [l.supplier_id for l in r1.lanes] == [None] and r1.kpis["lanes"] == 1


def test_adjustments_correct_the_reference_stock_and_the_past_is_projected_forward():
    """The initialisation day is the point zero: its stock (corrected by the adjustments dated up to it)
    is projected forward with the receipts, the actual consumption and the later adjustments."""
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=5), 2000.0)],
                      actuals=[ActualLine("P1", MON - D(days=k), 100.0) for k in (1, 2, 3, 4)],   # 200 components / day
                      receipts=[Receipt("R1", "A1", MON - D(days=2), 500)],
                      adjustments=[AdjustCell("A1", MON - D(days=6), -300.0),                     # before the point zero: correction
                                   AdjustCell("A1", MON + D(days=2), 40.0)])                      # known future movement
    r = run(ds, horizon_days=5)
    i = r.dates.index(MON)
    assert r.dates[0] == MON - D(days=5)                                   # nothing before the initialisation day
    assert r.reference_correction == -300 and r.kpis["stock_reference"] == 1700 and r.kpis["stock_on_hand"] == 2000
    assert r.kpis["init_date"] == (MON - D(days=5)).isoformat()
    assert list(r.stock_erp[i - 5:i]) == [1700, 1500, 1300, 1600, 1400]
    assert list(r.stock_plan[i - 5:i]) == list(r.stock_erp[i - 5:i])
    assert r.stock_plan[i] == 1200 and r.stock_plan[i + 2] == 1200 - 200 - 200 + 40
    assert r.adjustments[i + 2] == 40


def test_past_movements_are_projected_forward_from_the_init_day():
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=3), 1000.0)],
                      actuals=[ActualLine("P1", MON - D(days=2), 50.0)],
                      receipts=[Receipt("R1", "A1", MON - D(days=1), 500)],
                      adjustments=[AdjustCell("A1", MON - D(days=2), -30.0)])   # after the point zero: a movement
    r = run(ds, horizon_days=3)
    i = r.dates.index(MON)
    assert r.stock_erp[i - 3] == 1000 and r.stock_erp[i - 2] == 870 and r.stock_erp[i - 1] == 1370
    assert r.stock_plan[i] == 1370 - 200 and r.reference_correction == 0


def test_one_initialisation_date_for_everybody_never_in_the_future():
    from appro.engine.models import DatasetError
    ds = make_dataset(articles=[Article("A1", "W"), Article("A2", "W2")],
                      stock=[StockSnapshot("A1", MON - D(days=1), 10.0), StockSnapshot("A2", MON - D(days=2), 10.0)])
    with pytest.raises(DatasetError, match="plusieurs dates"):
        run(ds)
    ds = make_dataset(stock=[StockSnapshot("A1", MON + D(days=1), 10.0)])
    with pytest.raises(DatasetError, match="postérieure"):
        run(ds)
    # no stock row at all: point zero = today, empty stocks
    r = run(make_dataset(stock=[]))
    assert r.dates[0] == MON and r.kpis["stock_reference"] == 0


def test_display_start_follows_history_weeks_but_never_the_point_zero():
    from appro.api.presenters import display_start
    from appro.engine import run_mrp
    old = make_dataset(stock=[StockSnapshot("A1", MON - D(days=30), 100.0)])
    res = run_mrp(old, EngineParams(as_of=MON + D(days=2), horizon_days=10, history_weeks=2))
    assert display_start(res) == MON - D(weeks=2)                      # Monday of the current week − 2 weeks
    res0 = run_mrp(old, EngineParams(as_of=MON + D(days=2), horizon_days=10, history_weeks=0))
    assert display_start(res0) == MON
    young = make_dataset(stock=[StockSnapshot("A1", MON - D(days=3), 100.0)])
    res2 = run_mrp(young, EngineParams(as_of=MON + D(days=2), horizon_days=10, history_weeks=2))
    assert display_start(res2) == MON - D(days=3)                       # the point zero wins


def test_shortage_policies_and_first_stockout():
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 500.0)],
                      orders=[OrderLine("F1", "A1", "S1", MON + D(days=3), 1000, order_type=OrderType.FIRM)])
    backlog = run(ds, horizon_days=6)
    lost = run(ds, horizon_days=6, shortage_policy="lost")
    i = backlog.dates.index(MON)
    assert list(backlog.stock_erp[i:i + 4]) == [300, 100, 0, 700]
    assert list(backlog.stock_erp_net[i:i + 4]) == [300, 100, -100, 700]
    assert list(backlog.shortage_erp[i:i + 4]) == [0, 0, 100, 0]
    assert list(lost.stock_erp[i:i + 4]) == [300, 100, 0, 800]
    assert list(lost.shortage_erp[i:i + 4]) == [0, 0, 100, 0]
    for r in (backlog, lost):
        assert min(r.stock_erp) >= 0
        assert r.kpis["first_stockout_erp"] == (MON + D(days=2)).isoformat()
        assert r.kpis["max_shortage_erp"] == 100
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
    assert max(r.shortage_plan[r.dates.index(MON):]) == 0
    assert r.kpis["proposal_count"] == len(r.proposals)
    # proposals are placed on the lane of their supplier
    assert sum(r.lanes[0].supply_proposed) == sum(r.supply_proposed) > 0


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
    lanes = {l.supplier_id: l for l in r.lanes}
    assert abs(sum(lanes["S1"].supply_proposed) - by_sup["S1"]) < 1e-6


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


def test_monday_placement_gives_one_proposal_per_monday():
    """With the Monday placement several needs of a week land on the same Monday: they are merged
    into one proposal per supplier and day, everywhere."""
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 100.0)],
                      links=[SupplierLink("A1", "S1", moq=1, pack_qty=1, lead_time_days=2)])
    r = run(ds, horizon_days=60, proposal_placement="monday", generate_proposals=True)
    assert r.proposals and all(p.delivery_date.isoweekday() == 1 for p in r.proposals)
    days = [p.delivery_date for p in r.proposals]
    assert len(days) == len(set(days))
    assert [p.proposal_id for p in r.proposals] == [f"PR-A1-{k:03d}" for k in range(1, len(days) + 1)]
    r0 = run(ds, horizon_days=10, frozen_days=10, generate_proposals=True)
    assert all(p.delivery_date > MON + D(days=10) for p in r0.proposals)


def test_shortfall_tolerance_skips_short_dips():
    thu = MON + D(days=3)
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 1000.0)],
                      orders=[OrderLine("F1", "A1", "S1", thu, 2000, order_type=OrderType.FIRM)])
    base = run(ds, horizon_days=12, generate_proposals=True)
    assert base.proposals and base.proposals[0].delivery_date <= thu
    tol = run(ds, horizon_days=12, generate_proposals=True, shortfall_tolerance_days=1)
    assert not [p for p in tol.proposals if p.delivery_date <= thu]


def test_shortfall_tolerance_never_tolerates_lost_demand():
    """``lost`` policy: the simulated stock is clamped at 0, so a tolerated dip must be judged on the
    unserved demand, not on the sign of the stock (regression: weeks without any proposal)."""
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 1000.0)],
                      pdp=[PdpLine("P1", MON + D(weeks=k), 1000.0) for k in range(-2, 12)])
    base = run(ds, horizon_days=60, generate_proposals=True, shortage_policy="lost", proposal_placement="monday")
    i1 = base.dates.index(MON + D(weeks=1))
    assert float(np.max(base.shortage_plan[i1:])) == 0.0   # first week: no Monday delivery possible yet
    for tol in (5, 30):
        r = run(ds, horizon_days=60, generate_proposals=True, shortage_policy="lost", shortfall_tolerance_days=tol,
                proposal_placement="monday")
        assert list(r.shortage_plan) == list(base.shortage_plan), tol
        assert [(p.delivery_date, p.qty) for p in r.proposals] == [(p.delivery_date, p.qty) for p in base.proposals], tol


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
    ds.plan = [PlanCell("A1", "S1", MON + D(days=2), 2000), PlanCell("A1", "S1", MON + D(days=3), 0)]
    r2 = run(ds, horizon_days=12)
    scopes2 = {a.scope for a in r2.alerts if a.alert_type == AlertType.STOCKOUT}
    assert scopes2 == {"erp"} and r2.kpis["first_stockout_plan"] is None


# ------------------------------------------------------------------ programme impact / expressions
def test_program_impact():
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 700.0)],
                      orders=[OrderLine("F1", "A1", "S1", MON + D(days=4), 5000, order_type=OrderType.FIRM)])
    res = run_mrp(ds, EngineParams(as_of=MON, horizon_days=13, generate_proposals=False))
    imp = res.program_impact
    assert imp["weeks"][0] == "2026-W39"
    p = next(x for x in imp["programs"] if x["program_id"] == "P1")
    assert p["planned"][0] == 500
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


def test_ignored_firm_orders_leave_the_erp_scenario_and_the_plan():
    """A click on a Ferme cell: the firm orders of that supplier and day are ignored (ERP scenario,
    plan prefill) but still displayed with their ordered / remaining quantities."""
    from appro.engine.models import CellFlag
    wed, fri = MON + D(days=2), MON + D(days=4)
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=3), 1000.0)],
                      orders=[OrderLine("F1", "A1", "S1", wed, 1000, qty_open=400, order_type=OrderType.FIRM),
                              OrderLine("F2", "A1", "S1", fri, 800, order_type=OrderType.FIRM),
                              OrderLine("F0", "A1", "S1", MON - D(days=2), 300, qty_open=0, order_type=OrderType.FIRM)])
    r = run(ds)
    i = r.dates.index(MON)
    lane = r.lanes[0]
    assert lane.orders_firm_ordered[i + 2] == 1000 and lane.orders_firm_open[i + 2] == 400     # partial delivery
    assert lane.orders_firm_ordered[i - 2] == 300 and lane.orders_firm_open[i - 2] == 0         # settled, past
    assert r.orders_firm[i + 2] == 400 and r.plan[i + 2] == 400
    ds.flags = [CellFlag("A1", "S1", wed, "order_ignored"), CellFlag("A1", "S1", MON - D(days=2), "order_ignored")]
    r2 = run(ds)
    assert r2.orders_firm[i + 2] == 0 and r2.plan[i + 2] == 0 and r2.orders_firm[i + 4] == 800   # Wednesday ignored
    assert r2.lanes[0].orders_ignored[i + 2] and not r2.lanes[0].orders_ignored[i + 4]
    assert r2.lanes[0].orders_firm_ordered[i + 2] == 1000                                      # still displayed
    assert r2.stock_erp[i + 2] == r.stock_erp[i + 2] - 400 and r2.kpis["ignored_order_days"] == 1
    assert not r2.lanes[0].orders_ignored[i - 2]                                               # the past is never ignored
    typed = make_dataset(stock=list(ds.stock), orders=ds.orders)
    typed.flags, typed.plan = list(ds.flags), [PlanCell("A1", "S1", wed, 250)]
    r3 = run(typed)
    assert r3.orders_firm[i + 2] == 0 and r3.plan[i + 2] == 250                                 # a typed cell still counts


def test_refused_proposal_blocks_the_rest_of_its_week():
    """A click on a Proposition CBN cell refuses it: no proposal from that day to the Sunday of its
    week, the need is served by a later proposal (never an earlier one)."""
    from appro.engine.models import CellFlag
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 1500.0)],
                      links=[SupplierLink("A1", "S1", moq=100, pack_qty=50, lead_time_days=0, quota_pct=100, priority=1)])
    r = run(ds, generate_proposals=True, horizon_days=28)
    first = r.proposals[0]
    assert first.delivery_date > MON
    refused_week_end = first.delivery_date + D(days=6 - first.delivery_date.weekday())
    ds.flags = [CellFlag("A1", None, first.delivery_date, "proposal_refused", qty=first.qty)]
    r2 = run(ds, generate_proposals=True, horizon_days=28)
    assert all(not (first.delivery_date <= p.delivery_date <= refused_week_end) for p in r2.proposals)
    assert r2.proposals and r2.proposals[0].delivery_date > refused_week_end
    assert r2.kpis["refused_proposals"] == 1
    i = r2.dates.index(first.delivery_date)
    assert r2.supply_proposed[i] == 0 and max(r2.supply_proposed) > 0
