"""OTD at the day grain (docs/otd_maille_jour.md) : the scenarios of the note, each computed with the new rule
(« jour », default) and the previous one (« cumul »), so the difference between the two methods is pinned."""
from __future__ import annotations

import datetime as dt

import pytest
from test_api import client  # noqa: F401
from test_engine_units import MON, make_dataset

from appro.engine.calendar import WorkCalendar
from appro.engine.models import DesadvLine, EngineParams, OrderLine, OrderType, Receipt
from appro.services import performance

D = dt.timedelta
BASE = MON - D(weeks=6)          # a Monday, six weeks before the reference day : every day below is evaluable
CAL = WorkCalendar()             # Monday … Friday


def wd(i: int) -> dt.date:
    """The i-th working day of the scenario (1 = BASE)."""
    return CAL.add_working_days(BASE, i - 1)


def scenario(expected: dict[int, float | list[float]], received: dict[int, float], desadv: bool = True):
    """Firm slots of S1 / A1 on working days ``expected`` (several slots a day when a list is given) and one validated
    receipt per day of ``received`` (BL « BL-i », announced by a processed DESADV unless ``desadv`` is False)."""
    orders, n = [], 0
    for i, q in expected.items():
        for qq in (q if isinstance(q, list) else [q]):
            n += 1
            orders.append(OrderLine(f"O{n}", "A1", "S1", wd(i), float(qq), 0, OrderType.FIRM, "CO-1"))
    receipts = [Receipt(f"R{i}", "A1", wd(i), float(q), "S1", "CO-1", f"BL-{i}", "Comptabilisé") for i, q in received.items()]
    adv = [DesadvLine(f"D{i}", "A1", "S1", f"BL-{i}", wd(i) - D(days=1), float(q), "CO-1", "Traité", "OK", "ITR") for i, q in received.items()] if desadv else []
    return make_dataset(orders=orders, receipts=receipts, desadv=adv)


def by_day(ds, method: str, params: EngineParams | None = None) -> dict[int, performance.SlotEval]:
    """Evaluations keyed by scenario day (cumul : the first slot of the day)."""
    out: dict[int, performance.SlotEval] = {}
    for s in performance.matching(ds, MON, CAL, params or EngineParams(as_of=MON), method).slots:
        i = CAL.working_days_between(BASE, s.expected_date) + 1
        out.setdefault(i, s)
    return out


def otd(ds, method: str, params: EngineParams | None = None) -> dict[int, float | None]:
    return {i: s.otd for i, s in by_day(ds, method, params).items()}


def test_s0_nominal_both_methods_100():
    ds = scenario({i: 512 for i in range(1, 6)}, {i: 512 for i in range(1, 6)})
    assert otd(ds, "jour") == {i: 100 for i in range(1, 6)} and otd(ds, "cumul") == {i: 100 for i in range(1, 6)}
    j = by_day(ds, "jour")[3]
    assert j.method == "jour" and j.qty_score == 100 and j.punct_score == 100 and j.desadv_score == 100 and j.delta_days == 0 and j.received_on_day == 512


def test_s2_old_backlog_no_longer_cascades():
    """800 missing before the week, then 512 expected and received every day : the day grain scores the week 100 and
    the missing day once ; the cumulative rule completes every day two deliveries later (the screenshots)."""
    ds = scenario({1: 800, **{i: 512 for i in range(2, 7)}}, {i: 512 for i in range(2, 7)})
    jour, cumul = by_day(ds, "jour"), by_day(ds, "cumul")
    assert jour[1].status == "non livrée" and jour[1].otd == 0 and jour[1].qty_score == 0 and jour[1].punct_score == 0
    assert all(jour[i].otd == 100 for i in range(2, 7))
    assert all(cumul[i].otd < 100 and cumul[i].delta_days > 0 for i in range(2, 5)) and cumul[1].completed_date == wd(3)
    assert cumul[5].completed_date is None and cumul[6].completed_date is None         # 800 pieces short at the end, carried by the last slots
    rep = performance.otd_report(ds, EngineParams(as_of=MON), MON, CAL, "week", date_from=wd(1), date_to=wd(6), names={"S1": "S1"})
    assert rep["method"] == "jour" and rep["default_method"] == "jour" and rep["compare"]["method"] == "cumul"
    assert rep["totals"]["otd"] == round(500 / 6, 1) and rep["compare"]["totals"]["otd"] < rep["totals"]["otd"]
    assert rep["totals"]["balance_qty"] == 800 and rep["totals"]["balance_days"] == pytest.approx(800 / 512, abs=0.05)   # the backlog, in days of delivery
    assert rep["suppliers"][0]["balance_qty"] == 800 and all("balance_qty" in p for p in rep["suppliers"][0]["series"])


def test_s3_missed_day_caught_up_four_days_later():
    ds = scenario({i: 512 for i in range(1, 7)}, {1: 512, 3: 512, 4: 512, 5: 512, 6: 1024})
    jour, cumul = by_day(ds, "jour"), by_day(ds, "cumul")
    assert jour[2].completed_date == wd(6) and jour[2].delta_days == 4 and (jour[2].qty_score, jour[2].punct_score, jour[2].otd) == (0, 20, 30)
    assert all(jour[i].otd == 100 for i in (1, 3, 4, 5, 6)) and jour[6].received_on_day == 1024
    assert [cumul[i].delta_days for i in range(1, 7)] == [0, 1, 1, 1, 3, 0] and cumul[2].otd == 80       # calendar days : Friday → Monday = 3
    # beyond otd_backlog_max_days the day stays « non livré » ; a wider horizon lets the catch-up count
    late = scenario({1: 512, 2: 512}, {1: 512, 28: 1024})          # the balance arrives 26 working days (≈ 36 days) later
    assert by_day(late, "jour")[2].status == "non livrée"
    assert by_day(late, "jour", EngineParams(as_of=MON, otd_backlog_max_days=60))[2].status == "reçue"


def test_s4_systematic_one_day_shift_is_the_known_blind_spot():
    ds = scenario({i: 512 for i in range(1, 7)}, {i: 512 for i in range(2, 8)})
    jour, cumul = by_day(ds, "jour"), by_day(ds, "cumul")
    assert all(jour[i].otd == 100 for i in range(2, 7))
    # day 1 is complete only on day 7, when everything due since day 1 has arrived : +6 working days, 20 points
    assert jour[1].completed_date == wd(7) and jour[1].delta_days == 6 and jour[1].otd == 20
    assert [cumul[i].delta_days for i in range(1, 7)] == [1, 1, 1, 1, 3, 1] and [cumul[i].otd for i in range(1, 7)] == [80, 80, 80, 80, 60, 80]   # calendar days
    rep = performance.otd_report(ds, EngineParams(as_of=MON), MON, CAL, "month", date_from=wd(1), date_to=wd(7), names={"S1": "S1"})
    assert rep["totals"]["balance_qty"] == 512 - 512 and rep["suppliers"][0]["expected_qty"] == 6 * 512     # the extra day 7 receipt balances the window


def test_s5_partial_then_balance_next_day():
    ds = scenario({1: 512, 2: 512}, {1: 400, 2: 624})
    jour, cumul = by_day(ds, "jour"), by_day(ds, "cumul")
    assert jour[1].completed_date == wd(2) and jour[1].delta_days == 1 and (jour[1].qty_score, jour[1].punct_score, jour[1].otd) == (0, 60, 50)
    assert jour[2].otd == 100 and jour[2].received_on_day == 624
    assert cumul[1].otd == 80 and cumul[2].otd == 100


def test_s6_early_delivery_on_a_day_with_nothing_expected():
    ds = scenario({2: 512}, {1: 512})
    j, c = by_day(ds, "jour")[2], by_day(ds, "cumul")[2]
    assert j.completed_date == wd(1) and j.delta_days == -1 and (j.qty_score, j.punct_score, j.otd) == (0, 60, 50)
    assert c.delta_days == -1 and c.otd == 80


def test_s7_grouped_catch_up():
    ds = scenario({i: 512 for i in range(1, 6)}, {5: 2560})
    jour, cumul = by_day(ds, "jour"), by_day(ds, "cumul")
    assert [jour[i].delta_days for i in range(1, 6)] == [4, 3, 2, 1, 0] and [jour[i].otd for i in range(1, 6)] == [30, 30, 50, 50, 100]
    assert [cumul[i].otd for i in range(1, 6)] == [60, 60, 80, 80, 100]


def test_s8_slot_on_a_closure_moves_to_the_next_working_day_and_deltas_are_working_days():
    sat = wd(5) + D(days=1)
    orders = [OrderLine("O1", "A1", "S1", sat, 512, 0, OrderType.FIRM, "CO-1"), OrderLine("O2", "A1", "S1", wd(5), 512, 0, OrderType.FIRM, "CO-1")]
    receipts = [Receipt("R1", "A1", wd(6), 512, "S1", "CO-1", "BL-1", "Reçu"), Receipt("R2", "A1", wd(6), 512, "S1", "CO-1", "BL-2", "Reçu")]
    ds = make_dataset(orders=orders, receipts=receipts)
    jour = {s.expected_date: s for s in performance.match_days(ds, MON, CAL, EngineParams(as_of=MON)).slots}
    monday = jour[wd(6)]
    assert monday.shifted_from == sat and monday.qty_score == 100 and monday.punct_score == 100 and monday.slot_count == 1
    friday = jour[wd(5)]
    assert friday.completed_date == wd(6) and friday.delta_days == 1 and friday.punct_score == 60      # Friday → Monday : one working day
    assert {s.order_id: s.delta_days for s in performance.match_slots(ds, MON, CAL, EngineParams(as_of=MON)).slots}["O2"] == 3   # cumul : calendar days


def test_s9_two_slots_and_two_trucks_the_same_day_merge():
    orders = [OrderLine("O1", "A1", "S1", wd(3), 512, 0, OrderType.FIRM, "CO-1"), OrderLine("O2", "A1", "S1", wd(3), 192, 0, OrderType.FIRM, "CO-9")]
    receipts = [Receipt("R1", "A1", wd(3), 352, "S1", "CO-1", "BL-1", "Reçu"), Receipt("R2", "A1", wd(3), 352, "S1", "CO-9", "BL-2", "Reçu")]
    ds = make_dataset(orders=orders, receipts=receipts)
    m = performance.match_days(ds, MON, CAL, EngineParams(as_of=MON))
    assert len(m.slots) == 1 and m.slots[0].slot_count == 2 and m.slots[0].qty_ordered == 704 and m.slots[0].qty_score == 100
    assert m.slots[0].order_ids == ["O1", "O2"] and m.slots[0].purch_id == "CO-1 | CO-9" and m.slots[0].bls == ["BL-1", "BL-2"]
    assert m.slots[0].desadv_score == 0 and m.slots[0].otd == 80          # no DESADV in this scenario : 30 + 50 + 0


def test_not_evaluable_and_unmatched_in_day_mode():
    """A day the ERP shows received without any receipt line is not scored ; receipts of an article without firm
    slot are counted apart ; a day still inside the evaluation delay is open."""
    orders = [OrderLine("O1", "A1", "S1", wd(2), 100, 0, OrderType.FIRM, "CO-1"), OrderLine("O2", "A1", "S1", MON - D(days=1), 100, 100, OrderType.FIRM, "CO-2")]
    receipts = [Receipt("R9", "A9", wd(2), 50, "S1", "CO-7", "BL-9", "Reçu")]
    ds = make_dataset(orders=orders, receipts=receipts)
    m = performance.match_days(ds, MON, CAL, EngineParams(as_of=MON))
    st = {s.order_ids[0]: s.status for s in m.slots}
    assert st == {"O1": "non évaluable", "O2": "ouverte"} and m.unmatched_receipts == 1


def test_otd_matching_rule_and_api(client):  # noqa: F811
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "otd_matching", "value": "ailleurs"}).status_code == 422
    body = client.get("/api/performance/otd", params={"planner": "QUENTIN", "bucket": "quarter"}).json()
    assert body["method"] == "jour" and body["compare"]["method"] == "cumul" and "balance_qty" in body["totals"] and body["params"]["backlog_max_days"] == 30
    assert all(o["method"] == "jour" and o["order_ids"] for o in body["orders"][:20])
    other = client.get("/api/performance/otd", params={"planner": "QUENTIN", "bucket": "quarter", "method": "cumul", "compare": "false"}).json()
    assert other["method"] == "cumul" and other["compare"] is None and other["totals"]["n"] > 0 and body["totals"]["n"] > 0
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "otd_matching", "value": "cumul"}).status_code == 200
    assert client.get("/api/performance/otd", params={"planner": "QUENTIN"}).json()["method"] == "cumul"
    assert client.get("/api/performance/lead-times", params={"planner": "QUENTIN", "method": "jour"}).json()["method"] == "jour"
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "otd_matching", "value": "jour"}).status_code == 200
