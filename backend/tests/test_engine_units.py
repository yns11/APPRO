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
    PlanLine,
    Program,
    Receipt,
    StockSnapshot,
    Supplier,
    SupplierLink,
)
from appro.engine.projection import coverage_days
from appro.engine.scenario import ScenarioEvent, apply_scenario

MON = dt.date(2026, 9, 21)  # Monday


def make_dataset(**over) -> Dataset:
    base = dict(
        articles=[Article("A1", "Widget", "PCE", coverage_target_days=5, alert_red_days=2, alert_yellow_days=5,
                          overstock_days=30, order_cycle_days=7)],
        suppliers=[Supplier("S1", "Supplier 1"), Supplier("S2", "Supplier 2", delivery_weekdays=frozenset({2, 4}))],
        links=[SupplierLink("A1", "S1", moq=100, pack_qty=50, lead_time_days=5, quota_pct=100, priority=1)],
        programs=[Program("P1", "Line 1")],
        bom=[BomLine("P1", "A1", qty_per=2.0)],
        plan=[PlanLine("P1", MON + dt.timedelta(weeks=k), 500.0) for k in range(8)],
        actuals=[],
        orders=[],
        receipts=[],
        movements=[],
        stock=[StockSnapshot("A1", MON - dt.timedelta(days=1), 1000.0)],
    )
    base.update(over)
    return Dataset(**base)


# ------------------------------------------------------------------ calendar
def test_calendar_working_days_and_holidays():
    cal = WorkCalendar.from_spec("1,2,3,4,5", ["2026-09-23"])
    assert cal.is_working_day(MON)
    assert not cal.is_working_day(MON + dt.timedelta(days=5))  # Saturday
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


# ------------------------------------------------------------------ demand / projection
def test_demand_explosion_and_actuals():
    ds = make_dataset(actuals=[ActualLine("P1", MON, 0.0), ActualLine("P1", MON + dt.timedelta(days=1), 300.0)])
    res = run_mrp(ds, EngineParams(as_of=MON, horizon_days=30, generate_proposals=False))
    r = res.articles["A1"]
    i = r.dates.index(MON)
    assert r.demand[i] == 0.0                # explicit actual 0 overrides the plan
    assert r.demand[i + 1] == 600.0          # 300 × 2
    assert r.demand[i + 2] == 200.0          # plan 500/5 × 2
    assert r.demand_plan[i] == 200.0
    # plan-only mode ignores actuals
    res2 = run_mrp(ds, EngineParams(as_of=MON, horizon_days=30, production_mode="plan_only", generate_proposals=False))
    assert res2.articles["A1"].demand[i] == 200.0


def test_projection_firm_vs_simulated_and_receipts():
    ds = make_dataset(orders=[
        OrderLine("O1", "A1", "S1", MON + dt.timedelta(days=2), 300, order_type=OrderType.FIRM),
        OrderLine("O2", "A1", "S1", MON + dt.timedelta(days=3), 400, order_type=OrderType.FORECAST),
        OrderLine("O3", "A1", "S1", MON + dt.timedelta(days=3), 100, qty_received=100, status=OrderStatus.RECEIVED),
    ], receipts=[Receipt("R1", "A1", MON + dt.timedelta(days=1), 50)],
        movements=[Movement("M1", "A1", MON + dt.timedelta(days=1), -20)])
    res = run_mrp(ds, EngineParams(as_of=MON, horizon_days=10, generate_proposals=False))
    r = res.articles["A1"]
    i = r.dates.index(MON)
    # day0 (Monday): 1000 - 200 = 800 ; day1: +50 -20 -200 = 630 ; day2: +300 -200 = 730 ; day3 firm: 530, sim: 930
    assert r.stock_firm[i] == 800
    assert r.stock_firm[i + 1] == 630
    assert r.stock_firm[i + 2] == 730
    assert r.stock_firm[i + 3] == 530
    assert r.stock_sim[i + 3] == 930
    assert r.kpis["open_firm_qty"] == 300
    assert r.kpis["open_planned_qty"] == 400


def test_late_order_policies():
    late = OrderLine("L1", "A1", "S1", MON - dt.timedelta(days=5), 200, order_type=OrderType.FIRM)
    ds = make_dataset(orders=[late])
    r = run_mrp(ds, EngineParams(as_of=MON, horizon_days=5, generate_proposals=False)).articles["A1"]
    i = r.dates.index(MON)
    assert r.supply_firm[i] == 200  # rescheduled on the as-of day (Monday)
    assert any(a.alert_type == AlertType.LATE_ORDER for a in r.alerts)
    r2 = run_mrp(ds, EngineParams(as_of=MON, horizon_days=5, late_order_policy="ignore",
                                  generate_proposals=False)).articles["A1"]
    assert sum(r2.supply_firm) == 0


def test_coverage_days_calendar_and_working():
    cal = WorkCalendar()
    index = DayIndex(MON, MON + dt.timedelta(days=13))
    demand = np.array([100.0 if cal.is_working_day(d) else 0.0 for d in index.dates])
    stock = np.full(index.n, 250.0)
    cov_cal = coverage_days(stock, demand, index, cal, "calendar", "covered")
    cov_wd = coverage_days(stock, demand, index, cal, "working", "covered")
    # Monday: covers Tue + Wed (200) but not Thu -> 2 days
    assert cov_cal[0] == 2 and cov_wd[0] == 2
    # Friday: Sat, Sun (0), Mon, Tue -> 4 calendar days, 2 working days
    assert cov_cal[4] == 4 and cov_wd[4] == 2
    # tie rule
    stock_tie = np.full(index.n, 200.0)
    assert coverage_days(stock_tie, demand, index, cal, "calendar", "covered")[0] == 2
    assert coverage_days(stock_tie, demand, index, cal, "calendar", "not_covered")[0] == 1
    # negative stock -> 0
    assert coverage_days(np.full(index.n, -1.0), demand, index, cal)[0] == 0


# ------------------------------------------------------------------ proposals
def test_proposals_respect_moq_pack_lead_time_and_delivery_days():
    ds = make_dataset(stock=[StockSnapshot("A1", MON - dt.timedelta(days=1), 2600.0)])
    params = EngineParams(as_of=MON, horizon_days=40)
    r = run_mrp(ds, params).articles["A1"]
    assert r.proposals, "a proposal is expected: 2600 pcs cover ~13 days of 200/day"
    p = r.proposals[0]
    assert p.qty >= 100 and p.qty % 50 == 0
    assert p.supplier_id == "S1"
    assert not p.urgent
    assert WorkCalendar().working_days_between(p.order_date, p.delivery_date) == 5
    assert p.delivery_date.isoweekday() <= 5
    # stock never negative once proposals are included
    i = r.dates.index(MON)
    assert min(r.stock_sim[i:]) >= 0
    # firm stock does go negative -> stockout alert on firm scope
    assert any(a.alert_type == AlertType.STOCKOUT and a.scope == "firm" for a in r.alerts)
    assert r.kpis["proposal_count"] == len(r.proposals)


def test_proposals_supplier_delivery_weekdays_and_quota():
    links = [SupplierLink("A1", "S1", moq=100, pack_qty=1, lead_time_days=2, quota_pct=50, priority=1),
             SupplierLink("A1", "S2", moq=100, pack_qty=1, lead_time_days=2, quota_pct=50, priority=2)]
    ds = make_dataset(links=links, stock=[StockSnapshot("A1", MON - dt.timedelta(days=1), 300.0)])
    r = run_mrp(ds, EngineParams(as_of=MON, horizon_days=60)).articles["A1"]
    by_sup = {}
    for p in r.proposals:
        by_sup[p.supplier_id] = by_sup.get(p.supplier_id, 0) + p.qty
        if p.supplier_id == "S2":
            assert p.delivery_date.isoweekday() in (2, 4)
    assert set(by_sup) == {"S1", "S2"}
    share = by_sup["S1"] / sum(by_sup.values())
    assert 0.3 < share < 0.7


def test_urgent_proposal_when_lead_time_cannot_be_met():
    ds = make_dataset(stock=[StockSnapshot("A1", MON - dt.timedelta(days=1), 100.0)],
                      links=[SupplierLink("A1", "S1", moq=1, pack_qty=1, lead_time_days=15)])
    r = run_mrp(ds, EngineParams(as_of=MON, horizon_days=40)).articles["A1"]
    assert r.proposals and r.proposals[0].urgent
    assert any(a.alert_type == AlertType.URGENT_PROPOSAL for a in r.alerts)
    r2 = run_mrp(ds, EngineParams(as_of=MON, horizon_days=40, respect_lead_time=True)).articles["A1"]
    first = min(p.delivery_date for p in r2.proposals)
    assert first >= WorkCalendar().add_working_days(MON, 15)
    assert not any(p.urgent for p in r2.proposals)


def test_frozen_period_blocks_early_proposals():
    ds = make_dataset(stock=[StockSnapshot("A1", MON - dt.timedelta(days=1), 100.0)])
    r = run_mrp(ds, EngineParams(as_of=MON, horizon_days=40, frozen_days=10)).articles["A1"]
    assert all(p.delivery_date > MON + dt.timedelta(days=10) for p in r.proposals)


# ------------------------------------------------------------------ alerts
def test_alert_levels():
    ds = make_dataset(stock=[StockSnapshot("A1", MON - dt.timedelta(days=1), 100000.0)])
    r = run_mrp(ds, EngineParams(as_of=MON, horizon_days=60)).articles["A1"]
    assert any(a.alert_type == AlertType.OVERSTOCK for a in r.alerts)
    ds = make_dataset(stock=[StockSnapshot("A1", MON - dt.timedelta(days=1), 350.0)])
    r = run_mrp(ds, EngineParams(as_of=MON, horizon_days=60)).articles["A1"]
    low = [a for a in r.alerts if a.alert_type == AlertType.LOW_COVERAGE]
    assert low and low[0].severity.value == "critical"  # 350 -> 1 day of coverage <= red (2)
    ds = make_dataset(stock=[])
    r = run_mrp(ds, EngineParams(as_of=MON, horizon_days=10)).articles["A1"]
    assert any(a.alert_type == AlertType.MISSING_DATA for a in r.alerts)


# ------------------------------------------------------------------ scenarios
def test_scenario_events():
    ds = make_dataset(orders=[OrderLine("O1", "A1", "S1", MON + dt.timedelta(days=2), 300, order_type=OrderType.FIRM)])
    events = [
        ScenarioEvent("move_order", {"order_id": "O1", "days": 3}),
        ScenarioEvent("plan_factor", {"factor": 1.5, "program_id": "P1"}),
        ScenarioEvent("add_order", {"article_id": "A1", "supplier_id": "S1", "date": (MON + dt.timedelta(days=4)).isoformat(), "qty": 250}),
        ScenarioEvent("set_article_param", {"article_id": "A1", "field": "coverage_target_days", "value": 10}),
        ScenarioEvent("bogus", {}),
    ]
    sc, notes = apply_scenario(ds, events)
    assert ds.orders[0].expected_date == MON + dt.timedelta(days=2)  # original untouched
    assert sc.orders[0].expected_date == MON + dt.timedelta(days=5)
    assert sc.plan[0].qty == 750
    assert len(sc.orders) == 2 and sc.orders[1].source == "SCENARIO"
    assert sc.articles[0].coverage_target_days == 10
    assert notes and "bogus" in notes[0]
    base = run_mrp(ds, EngineParams(as_of=MON, horizon_days=10, generate_proposals=False)).articles["A1"]
    what_if = run_mrp(sc, EngineParams(as_of=MON, horizon_days=10, generate_proposals=False)).articles["A1"]
    i = base.dates.index(MON)
    assert what_if.demand[i] == 300.0 and base.demand[i] == 200.0
