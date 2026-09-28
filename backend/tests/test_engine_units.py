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
    OrderAction,
    OrderLine,
    OrderStatus,
    OrderType,
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
    # day0 (Monday): 1000 - 200 = 800 ; day1: +50 -20 -200 = 630 ; day2: +300 -200 = 730 ; day3 firm: 530
    assert r.stock_firm[i] == 800
    assert r.stock_firm[i + 1] == 630
    assert r.stock_firm[i + 2] == 730
    assert r.stock_firm[i + 3] == 530
    # the forecast order is in the forecast layer, not in the simulated one (default) unless asked
    assert r.stock_forecast[i + 3] == 930 and r.stock_sim[i + 3] == 530
    r_fc = run_mrp(ds, EngineParams(as_of=MON, horizon_days=10, generate_proposals=False, sim_includes_forecast=True)).articles["A1"]
    assert r_fc.stock_sim[i + 3] == 930
    assert r.kpis["open_firm_qty"] == 300
    assert r.kpis["open_forecast_qty"] == 400


def test_late_order_policies():
    """A past order still open in the ERP is excluded from every layer and listed "à qualifier" by
    default (the ERP remaining quantity is unreliable) ; ``reschedule`` restores the old behaviour,
    optionally limited to recent delays (``late_grace_days``)."""
    late = OrderLine("L1", "A1", "S1", MON - dt.timedelta(days=5), 200, order_type=OrderType.FIRM)
    old = OrderLine("L0", "A1", "S1", MON - dt.timedelta(days=60), 300, order_type=OrderType.FIRM)
    ds = make_dataset(orders=[late, old])
    r = run_mrp(ds, EngineParams(as_of=MON, horizon_days=5, generate_proposals=False)).articles["A1"]
    i = r.dates.index(MON)
    assert sum(r.supply_firm) == 0 and sum(r.supply_firm_sim) == 0
    states = {o.order_id: o for o in r.orders}
    assert states["L1"].status == "late" and states["L1"].days_late == 5 and not states["L1"].in_firm_layer
    assert states["L0"].status == "late" and states["L0"].days_late == 60
    assert r.kpis["late_order_count"] == 2 and r.kpis["late_order_qty"] == 500
    late_alerts = [a for a in r.alerts if a.alert_type == AlertType.LATE_ORDER]
    assert len(late_alerts) == 1 and "2 commande(s)" in late_alerts[0].message and "à qualifier" in late_alerts[0].message
    # reschedule: both land on the reference day (Monday)
    r2 = run_mrp(ds, EngineParams(as_of=MON, horizon_days=5, late_order_policy="reschedule",
                                  generate_proposals=False)).articles["A1"]
    assert r2.supply_firm[i] == 500 and r2.kpis["late_order_count"] == 0
    assert all(o.status == "expected" and o.in_firm_layer for o in r2.orders)
    # reschedule with a grace period: only the recent delay is rescheduled
    r3 = run_mrp(ds, EngineParams(as_of=MON, horizon_days=5, late_order_policy="reschedule", late_grace_days=10,
                                  generate_proposals=False)).articles["A1"]
    assert r3.supply_firm[i] == 200 and r3.kpis["late_order_count"] == 1
    # the planner qualifies the late order: expected on Wednesday → simulated layer only
    ds.actions = [OrderAction("ACT1", "L1", "A1", "reschedule", [(MON + dt.timedelta(days=2), 200.0)]),
                  OrderAction("ACT0", "L0", "A1", "close")]
    r4 = run_mrp(ds, EngineParams(as_of=MON, horizon_days=5, generate_proposals=False)).articles["A1"]
    assert sum(r4.supply_firm) == 0 and r4.supply_firm_sim[i + 2] == 200 and r4.actions[i + 2] == 200
    st = {o.order_id: o for o in r4.orders}
    assert st["L1"].status == "simulated" and st["L0"].status == "closed" and r4.kpis["late_order_count"] == 0
    assert r4.stock_sim[i + 2] - r4.stock_firm[i + 2] == 200


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
    params = EngineParams(as_of=MON, horizon_days=40, generate_proposals=True)
    r = run_mrp(ds, params).articles["A1"]
    assert r.proposals, "a proposal is expected: 2600 pcs cover ~13 days of 200/day"
    p = r.proposals[0]
    assert p.qty >= 100 and p.qty % 50 == 0
    assert p.supplier_id == "S1"
    assert not p.urgent
    assert WorkCalendar().working_days_between(p.order_date, p.delivery_date) == 5
    assert p.delivery_date.isoweekday() <= 5
    # no unserved demand once proposals are included; the physical stock is never negative
    i = r.dates.index(MON)
    assert max(r.shortage_sim[i:]) == 0 and min(r.stock_sim) >= 0
    # firm flows run out -> stockout alert on firm scope (no forecast order: single alert)
    assert any(a.alert_type == AlertType.STOCKOUT and a.scope == "firm" for a in r.alerts)
    assert not any(a.alert_type == AlertType.STOCKOUT and a.scope == "forecast" for a in r.alerts)
    assert r.kpis["max_shortage_firm"] > 0 and r.kpis["max_shortage_sim"] == 0
    assert r.kpis["proposal_count"] == len(r.proposals)


def test_proposals_supplier_delivery_weekdays_and_quota():
    links = [SupplierLink("A1", "S1", moq=100, pack_qty=1, lead_time_days=2, quota_pct=50, priority=1),
             SupplierLink("A1", "S2", moq=100, pack_qty=1, lead_time_days=2, quota_pct=50, priority=2)]
    ds = make_dataset(links=links, stock=[StockSnapshot("A1", MON - dt.timedelta(days=1), 300.0)])
    r = run_mrp(ds, EngineParams(as_of=MON, horizon_days=60, generate_proposals=True)).articles["A1"]
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
    r = run_mrp(ds, EngineParams(as_of=MON, horizon_days=40, generate_proposals=True)).articles["A1"]
    assert r.proposals and r.proposals[0].urgent
    assert any(a.alert_type == AlertType.URGENT_PROPOSAL for a in r.alerts)
    r2 = run_mrp(ds, EngineParams(as_of=MON, horizon_days=40, respect_lead_time=True, generate_proposals=True)).articles["A1"]
    first = min(p.delivery_date for p in r2.proposals)
    assert first >= WorkCalendar().add_working_days(MON, 15)
    assert not any(p.urgent for p in r2.proposals)


def test_frozen_period_blocks_early_proposals():
    ds = make_dataset(stock=[StockSnapshot("A1", MON - dt.timedelta(days=1), 100.0)])
    r = run_mrp(ds, EngineParams(as_of=MON, horizon_days=40, frozen_days=10, generate_proposals=True)).articles["A1"]
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


# ------------------------------------------------------------------ stock layers / shortage policy
def test_three_stock_layers_are_cumulative():
    ds = make_dataset(orders=[
        OrderLine("F1", "A1", "S1", MON + dt.timedelta(days=1), 300, order_type=OrderType.FIRM),
        OrderLine("D1", "A1", "S1", MON + dt.timedelta(days=2), 400, order_type=OrderType.FORECAST),
        OrderLine("A2", "A1", "S1", MON + dt.timedelta(days=4), 600, order_type=OrderType.FIRM, source="APP"),
    ], cells=[SimCell("A1", MON + dt.timedelta(days=3), "sim_receipt", 500.0)])
    r = run_mrp(ds, EngineParams(as_of=MON, horizon_days=10, generate_proposals=False)).articles["A1"]
    i = r.dates.index(MON)
    # day0: 1000-200 = 800 ; +300 firm ; +400 forecast (forecast layer only) ; +500 simulated receipt ; +600 app firm
    assert r.stock_firm[i + 1] == 900 and r.stock_forecast[i + 1] == 900 and r.stock_sim[i + 1] == 900
    assert r.stock_firm[i + 2] == 700 and r.stock_forecast[i + 2] == 1100 and r.stock_sim[i + 2] == 700
    assert r.stock_firm[i + 3] == 500 and r.stock_forecast[i + 3] == 900 and r.stock_sim[i + 3] == 1000
    assert r.stock_firm[i + 4] == 900 and r.stock_forecast[i + 4] == 1300 and r.stock_sim[i + 4] == 1400
    assert (r.kpis["open_firm_qty"], r.kpis["open_forecast_qty"], r.kpis["sim_receipts_qty"]) == (900, 400, 500)
    # app orders can be confined to the forecast layer (then out of the simulated stock by default)
    r2 = run_mrp(ds, EngineParams(as_of=MON, horizon_days=10, generate_proposals=False,
                                  app_firm_orders="simulated")).articles["A1"]
    assert r2.stock_firm[i + 4] == 300 and r2.stock_forecast[i + 4] == 1300 and r2.stock_sim[i + 4] == 800
    r3 = run_mrp(ds, EngineParams(as_of=MON, horizon_days=10, generate_proposals=False, sim_includes_forecast=True)).articles["A1"]
    assert r3.stock_sim[i + 4] == 1800


def test_forecast_layer_stockout_alert():
    # 700 on hand = 3.5 days ; a forecast order on Thursday postpones the ERP run-out, nothing else
    ds = make_dataset(stock=[StockSnapshot("A1", MON - dt.timedelta(days=1), 700.0)],
                      orders=[OrderLine("D1", "A1", "S1", MON + dt.timedelta(days=3), 2000, order_type=OrderType.FORECAST)])
    r = run_mrp(ds, EngineParams(as_of=MON, horizon_days=40, generate_proposals=False, sim_includes_forecast=True)).articles["A1"]
    scopes = {a.scope: a for a in r.alerts if a.alert_type == AlertType.STOCKOUT}
    assert set(scopes) == {"firm", "forecast", "simulated"}
    assert scopes["firm"].date < scopes["forecast"].date == scopes["simulated"].date
    assert "couvrent jusqu'au" in scopes["firm"].message
    assert r.kpis["first_stockout_firm"] < r.kpis["first_stockout_forecast"] == r.kpis["first_stockout_sim"]
    # forecast orders inside the firm horizon may be ignored (they should already be firm)
    r2 = run_mrp(ds, EngineParams(as_of=MON, horizon_days=40, generate_proposals=False, firm_horizon_days=14,
                                  forecast_horizon_policy="beyond_firm_horizon")).articles["A1"]
    assert sum(r2.supply_forecast) == 0 and r2.kpis["first_stockout_forecast"] == r2.kpis["first_stockout_firm"]


def test_shortage_policy_backlog_vs_lost():
    # 500 on hand, demand 200/day (Mon-Fri), receipt of 1000 on Thursday
    ds = make_dataset(stock=[StockSnapshot("A1", MON - dt.timedelta(days=1), 500.0)],
                      orders=[OrderLine("F1", "A1", "S1", MON + dt.timedelta(days=3), 1000, order_type=OrderType.FIRM)])
    backlog = run_mrp(ds, EngineParams(as_of=MON, horizon_days=6, generate_proposals=False)).articles["A1"]
    lost = run_mrp(ds, EngineParams(as_of=MON, horizon_days=6, generate_proposals=False,
                                    shortage_policy="lost")).articles["A1"]
    i = backlog.dates.index(MON)
    # Mon 300, Tue 100, Wed -100 (backlog) / 0 (lost, 100 lost), Thu +1000-200: 700 / 800
    assert backlog.stock_firm[i:i + 4] == [300, 100, 0, 700]
    assert backlog.stock_firm_net[i:i + 4] == [300, 100, -100, 700]
    assert backlog.shortage_firm[i:i + 4] == [0, 0, 100, 0]
    assert lost.stock_firm[i:i + 4] == [300, 100, 0, 800]
    assert lost.stock_firm_net[i:i + 4] == [300, 100, 0, 800]
    assert lost.shortage_firm[i:i + 4] == [0, 0, 100, 0]
    for r in (backlog, lost):
        assert min(r.stock_firm) >= 0
        assert r.kpis["first_stockout_firm"] == (MON + dt.timedelta(days=2)).isoformat()
        assert r.kpis["max_shortage_firm"] == 100
    # with proposals, the lost policy re-projects exactly: no unserved demand after the first delivery
    lost_p = run_mrp(ds, EngineParams(as_of=MON, horizon_days=40, shortage_policy="lost", generate_proposals=True)).articles["A1"]
    first = min(p.delivery_date for p in lost_p.proposals)
    j = lost_p.dates.index(first)
    assert max(lost_p.shortage_sim[j:]) == 0


# ------------------------------------------------------------------ simulation cells / expressions
def test_simulated_receipts_are_additive_and_block_cbn():
    """S is an additional receipt of the simulated layer (0 included) and a typed day never gets a
    CBN proposal ; on the reference day the orders are matched against the receipts of the day."""
    sat = MON - dt.timedelta(days=2)   # reference day = Saturday, snapshot Friday
    ds = make_dataset(
        stock=[StockSnapshot("A1", sat - dt.timedelta(days=1), 1000.0)],
        orders=[OrderLine("F0", "A1", "S1", sat, 300, order_type=OrderType.FIRM),                 # on the reference day
                OrderLine("F1", "A1", "S1", MON, 400, order_type=OrderType.FIRM),
                OrderLine("D1", "A1", "S1", MON, 100, order_type=OrderType.FORECAST),
                OrderLine("F2", "A1", "S1", MON + dt.timedelta(days=1), 500, order_type=OrderType.FIRM)],
        receipts=[Receipt("R0", "A1", sat, 120, supplier_id="S1")],
        cells=[SimCell("A1", sat, "sim_receipt", 200.0),
               SimCell("A1", MON, "sim_receipt", 0.0),
               SimCell("A1", MON + dt.timedelta(days=1), "sim_receipt", 900.0),
               SimCell("A1", MON + dt.timedelta(days=1), "adjustment", -10.0)])
    r = run_mrp(ds, EngineParams(as_of=sat, horizon_days=6, generate_proposals=False)).articles["A1"]
    i = r.dates.index(sat)
    # Saturday: receipt 120 matched against F0 (300 ordered) → 180 still expected ; S 200 adds up in the simulated layer
    assert r.supply_firm[i] == 180 and r.stock_firm[i] == 1300 and r.stock_forecast[i] == 1300 and r.stock_sim[i] == 1500
    assert next(o for o in r.orders if o.order_id == "F0").qty_expected == 180
    # Monday: firm +400 -200 = 1500 ; forecast +100 → 1600 ; sim: S = 0 adds nothing, F kept → 1700
    assert r.stock_firm[i + 2] == 1500 and r.stock_forecast[i + 2] == 1600 and r.stock_sim[i + 2] == 1700
    # Tuesday: firm +500 -10 -200 = 1790 ; sim +500 +900 -10 -200 = 2890
    assert r.stock_firm[i + 3] == 1790 and r.stock_sim[i + 3] == 2890
    assert r.sim_receipt_mask[i + 2] is True and r.sim_receipts[i + 2] == 0 and r.sim_receipt_mask[i + 4] is False
    assert r.kpis["sim_receipt_days"] == 3 and r.kpis["sim_receipts_qty"] == 1100
    # CBN constraint: a typed 0 blocks the proposal on that day, the delivery moves to the nearest allowed day
    ds2 = make_dataset(stock=[StockSnapshot("A1", MON - dt.timedelta(days=1), 900.0)])
    base = run_mrp(ds2, EngineParams(as_of=MON, horizon_days=20)).articles["A1"]
    first = base.proposals[0].delivery_date
    assert first == MON + dt.timedelta(days=1)   # the earliest proposable day
    ds2.cells = [SimCell("A1", first, "sim_receipt", 0.0)]
    blocked = run_mrp(ds2, EngineParams(as_of=MON, horizon_days=20)).articles["A1"]
    assert blocked.proposals and all(p.delivery_date != first for p in blocked.proposals)
    assert blocked.proposals[0].delivery_date == first + dt.timedelta(days=1)
    # a typed quantity is a decision too: no proposal that day, the shortfall is filled elsewhere
    ds2.cells = [SimCell("A1", first + dt.timedelta(days=1), "sim_receipt", 100.0)]
    typed = run_mrp(ds2, EngineParams(as_of=MON, horizon_days=20)).articles["A1"]
    assert all(p.delivery_date != first + dt.timedelta(days=1) for p in typed.proposals)


def test_same_day_matching_of_receipts_and_orders():
    """Reference day: nothing links a receipt to an order, so the quantity still expected from an
    order dated that day is min(open, max(0, ordered − receipts of the day)), firm orders first,
    same supplier first."""
    ds = make_dataset(orders=[OrderLine("F1", "A1", "S1", MON, 600, order_type=OrderType.FIRM),
                              OrderLine("F2", "A1", "S2", MON, 1000, order_type=OrderType.FIRM),
                              OrderLine("F3", "A1", "S1", MON, 500, 200, order_type=OrderType.FIRM)],   # 200 booked in the ERP
                      receipts=[Receipt("R1", "A1", MON, 400, supplier_id="S1"), Receipt("R2", "A1", MON, 150)])
    r = run_mrp(ds, EngineParams(as_of=MON, horizon_days=3, generate_proposals=False)).articles["A1"]
    i = r.dates.index(MON)
    st = {o.order_id: o for o in r.orders}
    # F1 (S1, ordered 600): takes 400 of S1 then 150 of the pool → 50 expected ; F2 (S2): nothing to match → 1000
    # F3 (S1, ordered 500, open 300): pools empty → min(300, 500) = 300
    assert st["F1"].qty_expected == 50 and st["F2"].qty_expected == 1000 and st["F3"].qty_expected == 300
    assert r.supply_firm[i] == 1350 and r.receipts[i] == 550
    assert r.stock_firm[i] == 1000 + 550 + 1350 - 200


def test_order_actions_reschedule_partial_cancel_and_review():
    thu = MON + dt.timedelta(days=3)
    ds = make_dataset(orders=[OrderLine("F1", "A1", "S1", thu, 1000, order_type=OrderType.FIRM),
                              OrderLine("F2", "A1", "S1", thu + dt.timedelta(days=7), 1000, order_type=OrderType.FIRM),
                              OrderLine("F3", "A1", "S1", thu + dt.timedelta(days=14), 800, order_type=OrderType.FIRM)])
    mon2 = MON + dt.timedelta(days=7)
    ds.actions = [
        OrderAction("A1", "F1", "A1", "reschedule", [(thu, 500.0), (mon2 + dt.timedelta(days=1), 500.0)]),   # partial
        OrderAction("A2", "F2", "A1", "cancel"),
        OrderAction("A3", "F3", "A1", "reschedule", [(thu + dt.timedelta(days=15), 1200.0)]),               # more than open
    ]
    r = run_mrp(ds, EngineParams(as_of=MON, horizon_days=25, generate_proposals=False)).articles["A1"]
    i = r.dates.index(MON)
    # firm layer = ERP as is
    assert r.supply_firm[i + 3] == 1000 and r.supply_firm[i + 10] == 1000 and r.supply_firm[i + 17] == 800
    # simulated layer = after actions
    assert r.supply_firm_sim[i + 3] == 500 and r.supply_firm_sim[i + 8] == 500
    assert r.supply_firm_sim[i + 10] == 0 and r.supply_firm_sim[i + 17] == 0 and r.supply_firm_sim[i + 18] == 800
    assert r.actions[i + 3] == -500 and r.actions[i + 8] == 500 and r.actions[i + 10] == -1000 and r.actions[i + 18] == 800
    st = {o.order_id: o for o in r.orders}
    assert st["F1"].status == "simulated" and [t["qty"] for t in st["F1"].tranches] == [500, 500]
    assert st["F2"].status == "cancelled" and st["F3"].status == "simulated"
    assert "ramenées" in st["F3"].review and st["F3"].tranches[0]["qty"] == 800
    assert r.kpis["action_count"] == 3 and r.kpis["review_count"] == 1
    # an uncovered remainder stays at the ERP date ; a simulated date already past goes back "à qualifier"
    ds.actions = [OrderAction("A1", "F1", "A1", "reschedule", [(mon2, 300.0)]),
                  OrderAction("A2", "F2", "A1", "reschedule", [(MON - dt.timedelta(days=2), 1000.0)])]
    r2 = run_mrp(ds, EngineParams(as_of=MON, horizon_days=25, generate_proposals=False)).articles["A1"]
    assert r2.supply_firm_sim[i + 7] == 300 and r2.supply_firm_sim[i + 3] == 700
    st2 = {o.order_id: o for o in r2.orders}
    assert st2["F2"].status == "late_sim" and r2.kpis["late_order_count"] == 1 and sum(r2.actions) == -1000
    assert "non couvert" in st2["F1"].review


def test_forecast_monday_policy_and_proposal_placement():
    thu = MON + dt.timedelta(days=3)
    ds = make_dataset(orders=[OrderLine("D1", "A1", "S1", thu, 400, order_type=OrderType.FORECAST)],
                      stock=[StockSnapshot("A1", MON - dt.timedelta(days=1), 2600.0)])
    r = run_mrp(ds, EngineParams(as_of=MON, horizon_days=40, forecast_date_policy="week_monday")).articles["A1"]
    i = r.dates.index(MON)
    assert r.supply_forecast[i] == 400 and r.supply_forecast[i + 3] == 0
    r2 = run_mrp(ds, EngineParams(as_of=MON, horizon_days=40)).articles["A1"]
    assert r2.supply_forecast[i + 3] == 400
    # proposals on Mondays only
    r3 = run_mrp(ds, EngineParams(as_of=MON, horizon_days=60, proposal_placement="monday")).articles["A1"]
    assert r3.proposals and all(p.delivery_date.isoweekday() == 1 for p in r3.proposals)


def test_weekly_article_parameters():
    week2 = MON + dt.timedelta(days=7)
    art = Article("A1", "Widget", "PCE", coverage_target_days=5, alert_red_days=2, alert_yellow_days=5,
                  overstock_days=30, order_cycle_days=7)
    art.weekly = {"2026-W40": {"coverage_target_days": 10, "safety_stock_qty": 50}}
    assert art.param_at("coverage_target_days", week2) == 10 and art.param_at("coverage_target_days", MON) == 5
    ds = make_dataset(articles=[art], stock=[StockSnapshot("A1", MON - dt.timedelta(days=1), 100000.0)])
    r = run_mrp(ds, EngineParams(as_of=MON, horizon_days=30, generate_proposals=False)).articles["A1"]
    i = r.dates.index(MON)
    # demand 200 on working days: 5 calendar days from Monday = 800 ; 10 calendar days from week-2 Monday = 1600
    assert r.target_stock[i] == 800
    assert r.target_stock[r.dates.index(week2)] == 1600


def test_program_impact():
    # 700 on hand, 200/day: Monday to Wednesday served, Thursday partially, Friday not at all (on-hand layer)
    ds = make_dataset(stock=[StockSnapshot("A1", MON - dt.timedelta(days=1), 700.0)],
                      orders=[OrderLine("F1", "A1", "S1", MON + dt.timedelta(days=4), 5000, order_type=OrderType.FIRM)])
    res = run_mrp(ds, EngineParams(as_of=MON, horizon_days=13, generate_proposals=False))
    imp = res.program_impact
    assert imp["weeks"][0] == "2026-W39"
    p = next(x for x in imp["programs"] if x["program_id"] == "P1")
    assert p["planned"][0] == 500
    # on-hand: Mon 100 + Tue 100 + Wed 100 + Thu 50 = 350 ; firm: Friday order arrives → 450 ; sim identical
    assert p["feasible"]["onhand"][0] == 350 and p["feasible"]["firm"][0] == 450 and p["feasible"]["sim"][0] == 450
    assert p["limiting"]["onhand"][0][0]["article_id"] == "A1"
    assert p["first_impact"]["onhand"] == "2026-W39" and p["first_impact"]["firm"] == "2026-W39"


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
