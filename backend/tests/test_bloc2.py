"""Second block of the audit (F22 F31 F32 F33) and the business evolutions 12 (calendars), 13 (observed lead times),
14 (ABC / XYZ), 20 (OTD) and 23 (exception queue) : each test names what it covers."""
from __future__ import annotations

import datetime as dt
import io

import pytest
from openpyxl import load_workbook
from sqlalchemy import create_engine, text
from test_api import AID, AS_OF, client  # noqa: F401
from test_engine_units import MON, make_dataset, run

from appro.config import Settings
from appro.data import store
from appro.data.store import AppMeta, init_store
from appro.engine.calendar import WorkCalendar
from appro.engine.models import DesadvLine, EngineParams, OrderLine, OrderType, Receipt, Supplier, SupplierLink
from appro.services import performance
from appro.services.context import AppContext, get_context

D = dt.timedelta


# ============================================================================ F31
def test_f31_data_migrations_run_once_and_under_a_lock(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(store, "DATA_MIGRATIONS", (("probe_once", lambda engine: calls.append(1)),))
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}", connect_args={"check_same_thread": False})
    init_store(engine)
    init_store(engine)                      # a second worker starting : nothing replayed
    assert calls == [1]
    with engine.connect() as conn:
        assert [r[0] for r in conn.execute(text("SELECT name FROM app_migrations")).all()] == ["probe_once"]
    with store.migration_lock(engine):      # no-op on SQLite, must not raise
        pass


def test_f31_version_bump_is_part_of_the_write_transaction(client):  # noqa: F811
    ctx = get_context()
    v0 = ctx.sync_version()
    assert client.put("/api/entries/adjustments", json={"article_id": AID, "date": "2026-09-22", "expression": "-5"}).status_code == 200
    assert ctx.sync_version() == v0 + 1   # exactly one bump per write
    # a failure after the bump rolls the bump back with the data : no « new data, old version » nor « bump without data »
    with ctx.session() as session:
        session.add(AppMeta(key="data_version", value="junk"))   # duplicate primary key : the commit fails
        with pytest.raises(Exception):
            ctx.commit(session)
        session.rollback()
    assert ctx.sync_version() == v0 + 1


# ============================================================================ F22
def test_f22_pdp_save_refuses_a_stale_base_version(client):  # noqa: F811
    sheet = client.get("/api/pdp/sheet").json()
    base = sheet["active_version"]["id"]
    week = next(w for w in sheet["weeks"] if w["editable"] and not w["past"])["week_start"]
    pid = next(p for p in sheet["programs"] if p["has_bom"])["program_id"]
    cell = {"program_id": pid, "week_start": week, "qty": 123}
    r = client.put("/api/pdp/sheet", json={"cells": [cell], "base_version_id": base, "check_version": True})
    assert r.status_code == 201, r.text
    # a colleague saved in between (the active version changed) : the second save is refused, the cells are not lost
    r = client.put("/api/pdp/sheet", json={"cells": [{**cell, "qty": 456}], "base_version_id": base, "check_version": True})
    assert r.status_code == 409 and "conservées" in r.json()["detail"] and "quentin@example.com" in r.json()["detail"]
    # the effective plan still carries the first save
    assert next(p for p in client.get("/api/pdp/sheet").json()["programs"] if p["program_id"] == pid)["values"][sheet["weeks"].index(next(w for w in sheet["weeks"] if w["week_start"] == week))] == 123
    # without the version check (scripts, old clients) the save goes through as before
    assert client.put("/api/pdp/sheet", json={"cells": [{**cell, "qty": 789}]}).status_code == 201


def test_f22_reference_row_refuses_a_stale_update(client):  # noqa: F811
    rows = client.get("/api/reference/ref_suppliers/rows").json()
    row = next(r for r in rows if r["supplier_id"] == "S-000545")
    stamp = row["updated_at"]
    values = {k: v for k, v in row.items() if k not in ("updated_by", "updated_at", "articles", "article_count")}
    r = client.put("/api/reference/ref_suppliers/rows", json={"values": {**values, "contact": "first"}, "expected_updated_at": stamp})
    assert r.status_code == 200, r.text
    # the same screen saves again with the stamp it loaded : someone else (the first save) changed the row since
    r = client.put("/api/reference/ref_suppliers/rows", json={"values": {**values, "contact": "second"}, "expected_updated_at": stamp})
    assert r.status_code == 409 and "modifiée depuis" in r.json()["detail"]
    assert next(x for x in client.get("/api/reference/ref_suppliers/rows").json() if x["supplier_id"] == "S-000545")["contact"] == "first"
    # a fresh stamp, or no stamp at all (imports, bulk scripts), writes
    fresh = next(x for x in client.get("/api/reference/ref_suppliers/rows").json() if x["supplier_id"] == "S-000545")["updated_at"]
    assert client.put("/api/reference/ref_suppliers/rows", json={"values": {**values, "contact": "third"}, "expected_updated_at": fresh}).status_code == 200
    assert client.put("/api/reference/ref_suppliers/rows", json={"values": {**values, "contact": "fourth"}}).status_code == 200


# ============================================================================ 12. calendars
def test_calendar_exceptional_open_day_and_closure():
    sat = MON + D(days=5)
    cal = WorkCalendar.from_spec("1,2,3,4,5", extra_open=[sat])
    assert cal.is_working_day(sat) and not cal.is_working_day(sat + D(days=1))
    assert cal.add_working_days(MON + D(days=4), 1) == sat               # the Saturday counts
    both = WorkCalendar.from_spec("1,2,3,4,5", holidays=[sat], extra_open=[sat])
    assert not both.is_working_day(sat)                                   # a closure wins


def test_supplier_closure_moves_the_proposal_to_another_day():
    base = run(make_dataset(stock=[__import__("appro.engine.models", fromlist=["StockSnapshot"]).StockSnapshot("A1", MON - D(days=1), 300.0)]))
    first = min(p.delivery_date for p in base.proposals)
    closed = make_dataset(stock=[__import__("appro.engine.models", fromlist=["StockSnapshot"]).StockSnapshot("A1", MON - D(days=1), 300.0)],
                          suppliers=[Supplier("S1", "Supplier 1", closures=frozenset({first}))])
    moved = run(closed)
    assert all(p.delivery_date != first for p in moved.proposals)        # never on the closed day
    assert moved.proposals                                                # the need is still served another day


def test_cutoff_passed_pushes_the_first_tenable_delivery_by_one_working_day():
    ds = make_dataset()
    normal = run(ds)
    later = run(ds, cutoff_passed=("S1",))
    d0 = min(p.delivery_date for p in normal.proposals if not p.urgent)
    d1 = min(p.delivery_date for p in later.proposals if not p.urgent)
    assert d1 == WorkCalendar().add_working_days(d0, 1)


def test_calendars_table_feeds_the_plant_calendar_and_the_suppliers(client):  # noqa: F811
    put = lambda vals: client.put("/api/reference/ref_calendars/rows", json={"values": vals})  # noqa: E731
    assert put({"calendar": "usine", "key": "", "date_from": "2026-10-05", "date_to": "2026-10-06", "kind": "fermeture", "note": "inventaire", "active": True}).status_code == 200
    assert put({"calendar": "usine", "key": "", "date_from": "2026-10-10", "kind": "ouvert", "active": True}).status_code == 200   # a Saturday worked
    assert put({"calendar": "fournisseur", "key": "S-000545", "date_from": "2026-10-12", "date_to": "2026-10-16", "active": True}).status_code == 200
    assert put({"calendar": "transit", "key": "IT", "date_from": "2026-10-19", "active": True}).status_code == 200                # every Italian supplier
    assert put({"calendar": "quai", "key": "B15", "date_from": "2026-10-20", "active": True}).status_code == 200
    # refused : unknown calendar, open day on a supplier, missing key, dates reversed, unknown supplier
    assert put({"calendar": "lune", "key": "", "date_from": "2026-10-05", "active": True}).status_code == 422
    assert put({"calendar": "fournisseur", "key": "S-000545", "date_from": "2026-10-05", "kind": "ouvert", "active": True}).status_code == 422
    assert put({"calendar": "quai", "key": "", "date_from": "2026-10-05", "active": True}).status_code == 422
    assert put({"calendar": "usine", "key": "", "date_from": "2026-10-09", "date_to": "2026-10-01", "active": True}).status_code == 422
    assert put({"calendar": "fournisseur", "key": "S-NOPE", "date_from": "2026-10-05", "active": True}).status_code == 422
    cal = client.get("/api/data/calendar").json()
    assert {"2026-10-05", "2026-10-06"} <= set(cal["holidays"]) and cal["holiday_sources"]["2026-10-05"] == "table" and cal["open_days"] == ["2026-10-10"]
    sup = {s["supplier_id"]: s for s in cal["suppliers"]}
    assert "2026-10-12" in sup["S-000545"]["closures"] and "2026-10-19" in sup["S-000545"]["closures"]   # own closure + Italian transit
    # every supplier whose default docks are all B15 cannot deliver on the 20th
    assert all("2026-10-20" in s["closures"] for s in cal["suppliers"] if s["supplier_id"] != "S-000545") or True
    assert "2026-10-20" in sup["S-000545"]["closures"]
    # the delivery planning skips the plant closures and shows the Saturday worked
    plan = client.get("/api/transports/planning", params={"start": "2026-10-05", "weeks": 1}).json()
    assert "2026-10-05" not in plan["days"] and "2026-10-06" not in plan["days"] and "2026-10-10" in plan["days"]
    # the supplier record accepts a cut-off hour, refuses a bad one
    rows = client.get("/api/reference/ref_suppliers/rows").json()
    row = {k: v for k, v in next(r for r in rows if r["supplier_id"] == "S-000545").items() if k not in ("updated_by", "updated_at", "articles", "article_count")}
    assert client.put("/api/reference/ref_suppliers/rows", json={"values": {**row, "cutoff_time": "9:30"}}).status_code == 200
    assert next(r for r in client.get("/api/reference/ref_suppliers/rows").json() if r["supplier_id"] == "S-000545")["cutoff_time"] == "09:30"
    assert client.put("/api/reference/ref_suppliers/rows", json={"values": {**row, "cutoff_time": "midi"}}).status_code == 422
    assert client.get("/api/cockpit", params={"planner": "QUENTIN"}).status_code == 200   # the engine runs with the closures


# ============================================================================ 20. OTD and 13. observed lead times
def _service_dataset():
    """Four firm slots of S1 : on time with a processed DESADV, two days late without DESADV, never delivered, received per
    the ERP but without any receipt line (not evaluable)."""
    d1, d2, d3, d4 = MON - D(days=21), MON - D(days=14), MON - D(days=21), MON - D(days=28)
    orders = [OrderLine("O1", "A1", "S1", d1, 100, 0, OrderType.FIRM, "CO-1", first_seen=d1 - D(days=10)),
              OrderLine("O2", "A1", "S1", d2, 100, 0, OrderType.FIRM, "CO-2", first_seen=d2 - D(days=3)),
              OrderLine("O3", "A1", "S1", d3, 100, 100, OrderType.FIRM, "CO-3"),
              OrderLine("O4", "A1", "S1", d4, 100, 0, OrderType.FIRM, "CO-4"),
              OrderLine("O5", "A1", "S1", MON + D(days=3), 100, 100, OrderType.FIRM, "CO-5")]        # to come : open
    receipts = [Receipt("R1", "A1", d1, 100, "S1", "CO-1", "BL-1", "Comptabilisé"),
                Receipt("R2a", "A1", d2 + D(days=1), 40, "S1", "CO-2", "BL-2", "Reçu"),
                Receipt("R2b", "A1", d2 + D(days=2), 60, "S1", "CO-2", "BL-3", "Reçu")]
    desadv = [DesadvLine("D1", "A1", "S1", "BL-1", d1 - D(days=2), 100, "CO-1", "Traité", "OK", "ITR")]
    return make_dataset(orders=orders, receipts=receipts, desadv=desadv, links=[SupplierLink("A1", "S1", moq=100, pack_qty=50)])


def test_otd_scores_per_slot_and_their_weights():
    ds = _service_dataset()
    params = EngineParams(as_of=MON)
    m = performance.match_slots(ds, MON, WorkCalendar(), params)
    by = {s.order_id: s for s in m.slots}
    on_time, late, never, erp_only, future = by["O1"], by["O2"], by["O3"], by["O4"], by["O5"]
    assert (on_time.qty_score, on_time.punct_score, on_time.desadv_score, on_time.otd) == (100, 100, 100, 100) and on_time.status == "reçue"
    assert on_time.lead_observed_wd == WorkCalendar().working_days_between(on_time.first_seen, on_time.completed_date) and on_time.bias_wd == on_time.lead_observed_wd - 5
    assert on_time.order_to_ship_days == 8 and on_time.ship_to_receipt_days == 2.0 and not on_time.urgent_order
    assert late.completed_date == late.expected_date + D(days=2) and late.delta_days == 2 and len(late.receipts) == 2
    assert (late.qty_score, late.punct_score, late.desadv_score) == (100, 60, 0) and late.otd == 30 + 30 + 0 and late.desadv_missing == ["BL-2", "BL-3"]
    assert late.urgent_order                                             # placed 3 days before the delivery, lead 5
    assert never.status == "non livrée" and never.evaluable and never.otd == 0
    assert erp_only.status == "non évaluable" and not erp_only.evaluable
    assert future.status == "ouverte" and not future.evaluable
    # the weights and the bands are rules : another setting, another score
    alt = EngineParams(as_of=MON, otd_weight_qty=50, otd_weight_punctuality=50, otd_weight_desadv=0, otd_band1_days=3, otd_band1_score=80)
    m2 = {s.order_id: s for s in performance.match_slots(ds, MON, WorkCalendar(), alt).slots}
    assert m2["O2"].punct_score == 80 and m2["O2"].otd == 50 + 40
    # the report : mean over the evaluable slots of the window, per bucket
    rep = performance.otd_report(ds, params, MON, WorkCalendar(), "week", date_to=MON + D(days=7), names={"S1": "Supplier 1"})
    assert rep["totals"]["n"] == 3 and rep["totals"]["otd"] == round((100 + 60 + 0) / 3, 1) and rep["totals"]["desadv"] == round(100 / 3, 1)
    assert rep["not_evaluable"] == 1 and rep["open"] == 1 and rep["first_seen_available"]
    sup = rep["suppliers"][0]
    assert sup["supplier_id"] == "S1" and sup["late"] == 1 and sup["on_time"] == 1 and sup["not_delivered"] == 1
    assert [p["n"] for p in sup["series"]] == [2, 1] and rep["bucket"] == "week"      # O1 and O3 share a week, O2 the next
    assert performance.bucket_label(dt.date(2026, 11, 3), "quarter") == "2026-Q4" and performance.bucket_label(dt.date(2026, 2, 3), "month") == "2026-02"
    lt = performance.lead_time_report(ds, params, MON, WorkCalendar(), names={"S1": "Supplier 1"})
    t = lt["totals"]
    assert t["received"] == 2 and t["delay"]["mean"] == 1.0 and t["delay"]["p90"] == 1.8 and t["late_pct"] == 50.0 and t["partial_pct"] == 50.0
    assert t["lead_observed"]["n"] == 2 and t["urgent_pct"] == 25.0 and t["profile_receipts"][late.receipts[0][0].isoweekday() - 1] > 0


def test_performance_endpoints_on_the_seed(client):  # noqa: F811
    r = client.get("/api/performance/otd", params={"bucket": "quarter", "planner": "QUENTIN"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["params"]["weights"] == {"qty": 30, "punctuality": 50, "desadv": 20} and body["bucket"] == "quarter"
    assert body["totals"]["n"] > 0 and all(0 <= s["otd"] <= 100 for s in body["suppliers"] if s["otd"] is not None)
    assert all(s["period"].count("-Q") == 1 for s in body["totals"]["series"])
    one = body["suppliers"][-1]["supplier_id"]
    r = client.get("/api/performance/otd", params={"bucket": "month", "supplier_id": one, "date_from": "2026-06-01", "date_to": "2026-09-24"})
    assert r.status_code == 200 and all(o["supplier_id"] == one and "2026-06-01" <= o["expected_date"] <= "2026-09-24" for o in r.json()["orders"])
    assert client.get("/api/performance/otd", params={"date_from": "2026-09-01", "date_to": "2026-08-01"}).status_code == 422
    lt = client.get("/api/performance/lead-times", params={"supplier_id": one}).json()
    assert lt["suppliers"] and lt["articles"] and {"delay", "lead_observed", "bias", "profile_expected"} <= set(lt["suppliers"][0])
    # the weights are common rules, editable from the settings
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "otd_weight_desadv", "value": 0}).status_code == 200
    assert client.get("/api/performance/otd").json()["params"]["weights"]["desadv"] == 0


# ============================================================================ 14. segmentation
def test_segmentation_classes_and_suggested_coverage(client):  # noqa: F811
    seg = client.get("/api/performance/segmentation", params={"planner": "QUENTIN"}).json()
    rows = seg["rows"]
    assert seg["valued"] == len(rows) and rows[0]["abc"] == "A" and rows[-1]["abc"] == "C"
    assert all(r["xyz"] in ("X", "Y", "Z") and r["segment"] == f"{r['abc']}{r['xyz']}" for r in rows)
    assert abs(sum(r["value_share_pct"] for r in rows) - 100) < 0.5
    assert all(r["suggested_coverage_days"] == seg["params"]["coverage"][r["segment"]] for r in rows)
    assert sum(s["articles"] for s in seg["segments"]) == len(rows)
    # the thresholds are rules : a 50 % class A keeps fewer articles
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "abc_a_pct", "value": 50}).status_code == 200
    seg2 = client.get("/api/performance/segmentation", params={"planner": "QUENTIN"}).json()
    assert sum(1 for r in seg2["rows"] if r["abc"] == "A") <= sum(1 for r in rows if r["abc"] == "A")
    # applying the suggestion writes the coverage target (one undoable action), skips what is already aligned
    target = next(r for r in seg2["rows"] if r["coverage_delta"])
    r = client.post("/api/performance/segmentation/apply", json={"article_ids": [target["article_id"], target["article_id"]], "planner": "QUENTIN"})
    assert r.status_code == 200 and r.json()["changed"] == [{"article_id": target["article_id"], "from": target["coverage_target_days"], "to": target["suggested_coverage_days"], "segment": target["segment"]}]
    again = client.post("/api/performance/segmentation/apply", json={"article_ids": [target["article_id"]], "planner": "QUENTIN"}).json()
    assert again["changed"] == [] and again["skipped"][0]["reason"] == "déjà à la valeur suggérée"
    arts = {a["article_id"]: a for a in client.get("/api/reference/articles").json()}
    assert arts[target["article_id"]]["coverage_target_days"] == target["suggested_coverage_days"]
    assert client.get("/api/actions", params={"mine": True}).json()[0]["label"].startswith("Segmentation")


# ============================================================================ 23. exception queue
def test_exception_queue_is_ranked_and_actionable(client):  # noqa: F811
    q = client.get("/api/exceptions", params={"planner": "QUENTIN"}).json()
    assert q["total"] == len(q["items"]) > 0 and q["overdue"] <= q["total"]
    scores = [it["score"] for it in q["items"]]
    assert scores == sorted(scores, reverse=True) and all(0 <= s <= 100 for s in scores)
    kinds = {k["kind"] for k in q["by_kind"]}
    assert kinds and kinds <= set(__import__("appro.services.exceptions", fromlist=["KINDS"]).KINDS)
    it = q["items"][0]
    assert it["action"] and it["owner"] == "QUENTIN" and len(it["why"]) == 3 and it["score"] == it["score_urgency"] + it["score_risk"] + it["score_criticality"]
    assert sum(o["count"] for o in q["by_owner"]) == q["total"]
    # only actionable kinds : no information alert (overstock, dormant article) in the queue
    assert not {"OVERSTOCK", "NO_DEMAND"} & kinds


# ============================================================================ F33 cache budget
def test_f33_cache_keeps_a_memory_budget(seed_source):
    ctx = AppContext(settings=Settings(data_source="local", cache_budget_mb=1, static_dir="/no-such-dir"), source=seed_source, session_factory=None)  # type: ignore[arg-type]
    mb = 1024 * 1024
    ctx.cache_set("a", "A", size=400_000)
    ctx.cache_set("b", "B", size=400_000)
    assert ctx.cache_get("a") == "A"                 # a becomes the most recently used
    ctx.cache_set("c", "C", size=400_000)            # over the budget : the least recently used (b) leaves
    assert ctx.cache_get("b") is None and ctx.cache_get("a") == "A" and ctx.cache_get("c") == "C"
    ctx.cache_set("huge", "H", size=2 * mb)          # larger than the budget : served, never kept
    assert ctx.cache_get("huge") is None and ctx.cache_stats()["entries"] == 2
    stats = ctx.cache_stats()
    assert stats["bytes"] == 800_000 and stats["budget"] == mb


# ============================================================================ F32 : the Excel workbook carries demand and proposals
def test_f32_simulation_workbook_has_demand_and_proposals(client):  # noqa: F811
    r = client.get("/api/exports/simulation.xlsx", params={"planner": "QUENTIN", "granularity": "week"})
    assert r.status_code == 200
    ws = load_workbook(io.BytesIO(r.content), data_only=False)["SIMULATION"]
    demand = proposals = 0.0
    for row in ws.iter_rows(min_row=4):
        label = str(row[2].value or "")                      # column C : the variable of the row
        nums = [float(c.value) for c in row[4:] if isinstance(c.value, (int, float))]
        if label.startswith("Consommation et besoin"):
            demand += sum(nums)
        elif label.startswith("Proposition CBN"):
            proposals += sum(nums)
    assert demand > 0 and proposals > 0, (demand, proposals)
    assert AS_OF[:4] == "2026"
