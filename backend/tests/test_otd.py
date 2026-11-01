"""Supplier service at the day grain (docs/regles_metier.md § 14) : the rule, the two quantified tables of the
specification (daily supplier, Monday / Thursday supplier) and the periods in whole weeks."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest
from test_api import client  # noqa: F401
from test_engine_units import MON

from appro.engine.calendar import WorkCalendar
from appro.engine.models import EngineParams
from appro.services import otd

D = dt.timedelta
BASE = MON - D(weeks=8)          # a Monday, eight weeks before the reference day
CAL = WorkCalendar()
PARAMS = EngineParams(as_of=MON)


def wd(i: int) -> dt.date:
    """The i-th working day of the scenario (1 = BASE, a Monday ; 4 = Thursday ; 6 = the next Monday)."""
    return CAL.add_working_days(BASE, i - 1)


def frames(expected: dict[int, float], received: dict[int, float | tuple[float, bool]], weekdays: str = "1,2,3,4,5",
           first_seen: dict[int, dt.date] | None = None, extra_orders: list[dict] | None = None):
    """One supplier S1, one article A1 : firm slots on the working days of ``expected`` and one validated receipt per
    day of ``received`` (BL « BL-i », announced by a processed DESADV unless the tuple says False)."""
    orders = [{"order_id": f"O{i}", "article_id": "A1", "supplier_id": "S1", "order_type": "FIRM", "expected_date": wd(i), "qty_ordered": float(q),
               "qty_open": 0.0, "purch_id": "CO-1", "first_seen": (first_seen or {}).get(i)} for i, q in expected.items()] + (extra_orders or [])
    receipts, desadv = [], []
    for i, v in received.items():
        q, ok = v if isinstance(v, tuple) else (v, True)
        receipts.append({"receipt_id": f"R{i}", "article_id": "A1", "supplier_id": "S1", "receipt_date": wd(i), "qty": float(q), "purch_id": "CO-1", "packing_slip": f"BL-{i}", "status": "Comptabilisé"})
        if ok:
            desadv.append({"desadv_id": f"D{i}", "article_id": "A1", "supplier_id": "S1", "packing_slip": f"BL-{i}", "purch_id": "CO-1", "issue_date": wd(i) - D(days=1), "qty": float(q), "state": "Traité", "final_processing": "OK"})
    cols = ["desadv_id", "article_id", "supplier_id", "packing_slip", "purch_id", "issue_date", "qty", "state", "final_processing"]
    return (pd.DataFrame(orders), pd.DataFrame(receipts), pd.DataFrame(desadv, columns=cols),
            pd.DataFrame([{"supplier_id": "S1", "name": "Supplier 1", "delivery_weekdays": weekdays, "lead_time_days": 5}]),
            pd.DataFrame(columns=["article_id", "supplier_id", "lead_time_override"]),
            pd.DataFrame([{"article_id": "A1", "designation": "Widget", "unit": "PCE", "planner": "QUENTIN"}]))


def lines(expected, received, **kw) -> dict[int, otd.OtdLine]:
    data = otd.build(*frames(expected, received, **kw), CAL, MON, PARAMS)
    return {CAL.working_days_between(BASE, x.day) + 1: x for x in data.lines}


def score(x: otd.OtdLine, desadv: float = 100.0) -> float | None:
    return otd.otd_score(x.qty_score, x.punct_score, desadv, PARAMS)


DAILY = {i: 512.0 for i in range(1, 11)}       # ten working days of 512, need 512 a day, window 0, tolerance 512


# ---------------------------------------------------------------------------------------------------- daily supplier
def test_daily_nominal_and_the_rule_derivation():
    L = lines(DAILY, dict(DAILY))
    assert all(x.qty_score == 100 and x.punct_score == 100 and x.delta == 0 and x.status == "reçue" for x in L.values())
    x = L[3]
    assert x.window == 0 and x.tolerance == pytest.approx(512) and x.excess == 0 and x.order_count == 1 and x.purch == "CO-1"
    assert otd.supplier_window("1,4") == 1 and otd.supplier_window("1,2,3") == 0 and otd.supplier_window("") == 1
    assert otd.tolerance_factor("1,4") == 1 and otd.tolerance_factor("2") == 2 and otd.tolerance_factor("") == 2


def test_daily_one_day_late_costs_the_day_only():
    r = dict(DAILY)
    r[4] = 0.0
    r[5] = 1024.0
    L = lines(DAILY, r)
    assert (L[4].qty_score, L[4].punct_score, L[4].delta, score(L[4])) == (100, 60, 1, 80)     # in full, one day late
    assert all(L[i].qty_score == 100 and L[i].punct_score == 100 for i in (3, 5, 6))      # the catch-up day is perfect, no cascade


def test_daily_three_days_late_and_never_delivered():
    r = dict(DAILY)
    r[4] = 0.0
    r[7] = 1024.0
    L = lines(DAILY, r)
    assert (L[4].qty_score, L[4].delta, L[4].punct_score, score(L[4])) == (100, 3, 20, 60) and L[5].punct_score == 100 and L[6].punct_score == 100
    r = dict(DAILY)
    r[4] = 0.0
    L = lines(DAILY, r)
    assert L[4].status == "non livrée" and L[4].evaluable and (L[4].qty_score, L[4].punct_score) == (0, 0) and score(L[4]) == 20
    assert all(L[i].qty_score == 100 for i in range(5, 11))                                 # what was missing before does not count


def test_daily_partial_then_balance():
    r = dict(DAILY)
    r[4], r[5] = 400.0, 624.0
    L = lines(DAILY, r)
    assert (L[4].qty_score, L[4].punct_score, L[4].delta, score(L[4])) == (100, 60, 1, 80)
    assert (L[5].qty_score, L[5].punct_score, L[5].excess) == (100, 100, 0)


def test_daily_grouped_deliveries_tolerance_and_advance():
    # two days in one : the advance of one day of need is tolerated, the next day is early by one day
    r = dict(DAILY)
    r[4], r[5] = 1024.0, 0.0
    L = lines(DAILY, r)
    assert (L[4].qty_score, L[4].punct_score, L[4].excess) == (100, 100, 512) and score(L[4]) == 100
    assert (L[5].qty_score, L[5].punct_score, L[5].delta, L[5].completed) == (100, 60, -1, wd(4)) and score(L[5]) == 80
    # three days in one : the day loses its QUANTITÉ, the two next days are early (the double penalty, accepted)
    r = dict(DAILY)
    r[4], r[5], r[6] = 1536.0, 0.0, 0.0
    L = lines(DAILY, r)
    assert (L[4].qty_score, L[4].punct_score, L[4].excess) == (0, 100, 1024) and score(L[4]) == 70
    assert (L[5].qty_score, L[5].punct_score, L[5].delta) == (100, 60, -1) and (L[6].qty_score, L[6].punct_score, L[6].delta) == (100, 60, -2)
    assert L[7].qty_score == 100 and L[7].punct_score == 100
    # two days in one and a third day missing : the surplus serves one line only, the third day is not delivered
    r = dict(DAILY)
    r[4], r[5], r[6] = 1024.0, 0.0, 0.0
    L = lines(DAILY, r)
    assert L[5].completed == wd(4) and L[6].status == "non livrée"


def test_quantity_is_in_full_within_the_horizon_only():
    r = dict(BIWEEKLY)
    r.pop(9)
    r[14] = 800.0                                  # Thursday delivered the next Thursday : +5 working days, inside window 1 + band 4
    L = lines(BIWEEKLY, r, weekdays="1,4")
    assert (L[9].qty_score, L[9].punct_score, L[9].delta) == (100, 20, 5) and score(L[9]) == 60
    r = dict(BIWEEKLY)
    r.pop(9)
    r[16] = 1000.0                                 # the Monday after : +7, beyond the horizon → non livrée, frozen
    L = lines(BIWEEKLY, r, weekdays="1,4")
    assert L[9].status == "non livrée" and (L[9].qty_score, L[9].punct_score) == (0, 0) and score(L[9]) == 20


def test_daily_over_delivery_penalised_once():
    r = dict(DAILY)
    r[4] = 1536.0
    L = lines(DAILY, r)
    assert (L[4].qty_score, L[4].punct_score) == (0, 100) and score(L[4]) == 70
    assert all(L[i].qty_score == 100 and L[i].punct_score == 100 and L[i].excess == 0 for i in range(5, 11))


def test_desadv_is_read_per_delivery_note_of_the_period():
    r = dict(DAILY)
    r[4] = (512.0, False)
    data = otd.build(*frames(DAILY, r), CAL, MON, PARAMS)
    rep = otd.otd_report(data, PARAMS, "month", date_from=wd(1), date_to=wd(10))
    t = rep["totals"]
    assert t["n"] == 10 and t["qty"] == 100 and t["punctuality"] == 100 and t["desadv"] == 90 and t["otd"] == 98 and t["bls"] == 10
    assert rep["suppliers"][0]["window"] == 0 and rep["lines"][0]["bls"] == ["BL-10"]


# ---------------------------------------------------------------------------------------------------- Monday / Thursday supplier
BIWEEKLY = {1: 600.0, 4: 400.0, 6: 600.0, 9: 400.0, 11: 600.0, 14: 400.0, 16: 600.0, 19: 400.0}     # four weeks, 200 a day of need


def test_biweekly_window_and_tolerance_are_derived():
    L = lines(BIWEEKLY, dict(BIWEEKLY), weekdays="1,4")
    x = L[6]
    assert x.window == 1 and 200 <= x.tolerance <= 215 and all(y.qty_score == 100 and y.punct_score == 100 for y in L.values())


def test_biweekly_monday_delivered_tuesday_is_inside_the_window():
    r = dict(BIWEEKLY)
    r.pop(6)
    r[7] = 600.0
    L = lines(BIWEEKLY, r, weekdays="1,4")
    assert (L[6].qty_score, L[6].punct_score, L[6].delta) == (100, 100, 1) and score(L[6]) == 100     # inside the ±1 window : perfect


def test_biweekly_thursday_delivered_next_monday():
    r = dict(BIWEEKLY)
    r.pop(9)
    r[11] = 1000.0
    L = lines(BIWEEKLY, r, weekdays="1,4")
    assert (L[9].qty_score, L[9].punct_score, L[9].delta, L[9].completed) == (100, 60, 2, wd(11)) and score(L[9]) == 80
    assert (L[11].qty_score, L[11].punct_score, L[11].excess) == (100, 100, 0)        # the catch-up Monday is perfect


def test_biweekly_grouping_and_over_delivery():
    # 1 000 on Monday, nothing on Thursday : Monday loses its QUANTITÉ, Thursday is early by three days
    r = dict(BIWEEKLY)
    r[6], r[9] = 1000.0, 0.0
    L = lines(BIWEEKLY, r, weekdays="1,4")
    assert (L[6].qty_score, L[6].punct_score) == (0, 100) and score(L[6]) == 70
    assert (L[9].qty_score, L[9].punct_score, L[9].delta) == (100, 60, -3) and score(L[9]) == 80
    # 800 on Monday : the advance of one day of need is tolerated
    r = dict(BIWEEKLY)
    r[6] = 800.0
    L = lines(BIWEEKLY, r, weekdays="1,4")
    assert (L[6].qty_score, L[9].qty_score, L[9].punct_score) == (100, 100, 100)
    # 1 000 on Monday and 400 on Thursday anyway : only Monday is penalised
    r = dict(BIWEEKLY)
    r[6] = 1000.0
    L = lines(BIWEEKLY, r, weekdays="1,4")
    assert score(L[6]) == 70 and (L[9].qty_score, L[9].punct_score) == (100, 100)


# ---------------------------------------------------------------------------------------------------- calendar, periods, statuses
def test_slot_on_a_closure_moves_to_the_next_working_day():
    sat = wd(5) + D(days=1)
    extra = [{"order_id": "OS", "article_id": "A1", "supplier_id": "S1", "order_type": "FIRM", "expected_date": sat, "qty_ordered": 512.0, "qty_open": 0.0, "purch_id": "CO-1", "first_seen": None}]
    data = otd.build(*frames({i: 512.0 for i in range(1, 6)}, {**{i: 512.0 for i in range(1, 6)}, 6: 512.0}, extra_orders=extra), CAL, MON, PARAMS)
    monday = next(x for x in data.lines if x.day == wd(6))
    assert monday.shifted_from == sat and monday.qty_score == 100 and monday.punct_score == 100


def test_periods_are_whole_weeks_attached_to_the_month_of_their_wednesday():
    assert otd.bucket_label(dt.date(2026, 10, 29), "month") == "2026-10"      # Thursday 29/10 : its week's Wednesday is 28/10
    assert otd.bucket_label(dt.date(2026, 11, 2), "month") == "2026-11"       # Monday 02/11 : Wednesday 04/11
    assert otd.bucket_label(dt.date(2026, 3, 31), "month") == "2026-04"       # Tuesday 31/03 : Wednesday 01/04 → April
    assert otd.bucket_label(dt.date(2026, 3, 31), "quarter") == "2026-Q2" and otd.bucket_label(dt.date(2026, 3, 31), "week") == "2026-W14"
    assert otd.snap_window(dt.date(2026, 10, 7), dt.date(2026, 10, 21)) == (dt.date(2026, 10, 5), dt.date(2026, 10, 25))


def test_statuses_open_not_evaluable_unmatched_and_portfolio_mean_of_suppliers():
    o, r, d, s, l, a = frames({1: 100.0}, {})
    o = pd.concat([o, pd.DataFrame([{"order_id": "O9", "article_id": "A1", "supplier_id": "S1", "order_type": "FIRM", "expected_date": MON - D(days=3), "qty_ordered": 100.0, "qty_open": 100.0, "purch_id": "CO-1", "first_seen": None},
                                    {"order_id": "O8", "article_id": "A1", "supplier_id": "S2", "order_type": "FIRM", "expected_date": wd(2), "qty_ordered": 100.0, "qty_open": 0.0, "purch_id": "CO-2", "first_seen": None}])])
    r = pd.DataFrame([{"receipt_id": "R8", "article_id": "A1", "supplier_id": "S2", "receipt_date": wd(2), "qty": 100.0, "purch_id": "CO-2", "packing_slip": "BL-8", "status": "Reçu"},
                      {"receipt_id": "R7", "article_id": "A9", "supplier_id": "S2", "receipt_date": wd(2), "qty": 5.0, "purch_id": "CO-7", "packing_slip": "BL-7", "status": "Reçu"}])
    s = pd.concat([s, pd.DataFrame([{"supplier_id": "S2", "name": "Supplier 2", "delivery_weekdays": "1,2,3,4,5", "lead_time_days": 5}])])
    data = otd.build(o, r, d, s, l, a, CAL, MON, PARAMS)
    st = {(x.supplier_id, x.day): x.status for x in data.lines}
    assert st[("S1", wd(1))] == "non évaluable" and st[("S1", MON - D(days=3))] == "ouverte" and st[("S2", wd(2))] == "reçue" and data.unmatched_receipts == 1
    rep = otd.otd_report(data, PARAMS, "week", date_from=wd(1), date_to=MON)
    assert rep["not_evaluable"] == 1 and rep["open"] == 1 and rep["totals"]["suppliers"] == 1 and rep["totals"]["otd"] == 80   # S2 alone : no DESADV → 30 + 50
    assert rep["suppliers"][0]["supplier_id"] == "S2" and rep["suppliers"][0]["series"][0]["desadv"] == 0


def test_lead_times_without_dispersion():
    fs = {i: wd(i) - D(days=10) for i in DAILY}
    r = dict(DAILY)
    r[4], r[5] = 0.0, 1024.0
    data = otd.build(*frames(DAILY, r, first_seen=fs), CAL, MON, PARAMS)
    lt = otd.lead_time_report(data, PARAMS, date_from=wd(1), date_to=wd(10))
    t = lt["totals"]
    assert t["received"] == 10 and t["delay_mean"] == 0.1 and t["late_pct"] == 10.0 and t["on_time_pct"] == 90.0 and t["lead_observed_mean"] is not None
    assert t["bias_mean"] == round(t["lead_observed_mean"] - 5, 1) and t["urgent_pct"] == 0.0 and t["partial_pct"] == 0.0
    assert not any("p50" in k or "p90" in k or "std" in k for k in t)


def test_otd_endpoint_shape_and_cache(client):  # noqa: F811
    body = client.get("/api/performance/otd", params={"planner": "QUENTIN", "bucket": "month"}).json()
    assert {"totals", "suppliers", "lines", "periods", "params"} <= set(body) and "compare" not in body and "balance_qty" not in body["totals"]
    assert all({"qty_score", "punct_score", "status", "received", "expected", "window"} <= set(x) for x in body["lines"][:10])
    health = client.get("/api/health").json()
    assert health["cache"]["entries"] >= 1
    schema = {p["field"] for p in client.get("/api/params/schema").json()}
    assert "otd_matching" not in schema and "otd_backlog_max_days" not in schema and "otd_band1_days" in schema
