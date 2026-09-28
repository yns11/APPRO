"""End-to-end API tests on an isolated context (seed CSV source + in-memory SQLite store)."""
from __future__ import annotations

import datetime as dt
import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy.pool import StaticPool
from tests.conftest import SEED

from appro.api.main import create_app
from appro.config import Settings
from appro.data.store import Base
from appro.services.context import AppContext, set_context

AS_OF = "2026-09-19"


@pytest.fixture()
def client(seed_source):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    settings = Settings(data_source="local", seed_dir=SEED, as_of=dt.date.fromisoformat(AS_OF), horizon_days=90,
                        static_dir=SEED / "no-such-dir")
    ctx = AppContext(settings=settings, source=seed_source, session_factory=sessionmaker(bind=engine, expire_on_commit=False))
    set_context(ctx)
    try:
        yield TestClient(create_app(), headers={"x-forwarded-email": "quentin@example.com"})
    finally:
        set_context(None)


def test_health_and_config(client):
    assert client.get("/api/health").json()["status"] == "ok"
    cfg = client.get("/api/config").json()
    assert cfg["as_of"] == AS_OF and cfg["planners"] == ["QUENTIN"] and cfg["user"] == "quentin@example.com"


def test_cockpit(client):
    r = client.get("/api/cockpit", params={"planner": "QUENTIN"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kpis"]["articles"] == 16
    assert len(body["articles"]) == 16
    assert body["as_of"] == AS_OF
    first = body["articles"][0]
    assert {"article_id", "kpis", "sparkline", "severity"} <= set(first)
    assert body["weekly_supply_demand"] and body["weekly_supply_demand"][0]["week"].startswith("2026-W")
    # the CBN complement is part of the simulated stock: no article stays in stockout
    assert body["kpis"]["proposals"] > 0 and body["kpis"]["sim_receipt_articles"] == 0
    for a in body["articles"]:
        assert a["kpis"]["first_stockout_sim"] is None and a["kpis"]["min_stock_sim"] >= 0, a["article_id"]


def test_projection_day_and_week(client):
    r = client.get("/api/articles/P-00001046/projection", params={"granularity": "day"})
    assert r.status_code == 200, r.text
    day = r.json()
    keys = {s["key"] for s in day["series"]}
    assert {"demand", "stock_firm", "stock_sim", "coverage_sim", "target_stock"} <= keys
    assert len(day["periods"]) == len(day["series"][0]["values"])
    assert day["programs"] and day["suppliers"][0]["supplier_id"] == "S-000545"
    week = client.get("/api/articles/P-00001046/projection", params={"granularity": "week"}).json()
    assert len(week["periods"]) < len(day["periods"])
    demand_day = sum(next(s for s in day["series"] if s["key"] == "demand")["values"])
    demand_week = sum(next(s for s in week["series"] if s["key"] == "demand")["values"])
    assert abs(demand_day - demand_week) < 0.01
    assert client.get("/api/articles/NOPE/projection").status_code == 404


def test_entries_change_projection_and_audit(client):
    before = client.get("/api/articles/P-00001046/projection", params={"generate_proposals": "false"}).json()
    stock_before = next(s for s in before["series"] if s["key"] == "stock_sim")["values"]
    # an order typed in the app is a real (firm) order: simulated orders are grid cells
    assert client.post("/api/entries/orders", json={"article_id": "P-00001046", "supplier_id": "S-000545",
                                                    "expected_date": "2026-09-25", "qty": 1600, "order_type": "PLANNED"}).status_code == 422
    r = client.post("/api/entries/orders", json={"article_id": "P-00001046", "supplier_id": "S-000545",
                                                 "expected_date": "2026-09-25", "qty": 1600})
    assert r.status_code == 201, r.text
    order = r.json()
    after = client.get("/api/articles/P-00001046/projection", params={"generate_proposals": "false"}).json()
    stock_after = next(s for s in after["series"] if s["key"] == "stock_sim")["values"]
    i = after["periods"].index("2026-09-25")
    assert stock_after[i] - stock_before[i] == pytest.approx(1600)
    firm_after = next(s for s in after["series"] if s["key"] == "stock_firm")["values"]
    firm_before = next(s for s in before["series"] if s["key"] == "stock_firm")["values"]
    assert firm_after[i] - firm_before[i] == pytest.approx(1600)  # firm layer too
    # receipt closes the order
    r = client.post("/api/entries/receipts", json={"article_id": "P-00001046", "order_id": order["id"],
                                                   "receipt_date": "2026-09-24", "qty": 1600})
    assert r.status_code == 201
    assert client.get("/api/entries/orders", params={"article_id": "P-00001046"}).json()[0]["status"] == "RECEIVED"
    # adjustment + production + deletes
    assert client.post("/api/entries/adjustments", json={"article_id": "P-00001046", "date": "2026-09-22", "qty": -50}).status_code == 201
    assert client.post("/api/entries/adjustments", json={"article_id": "P-00001046", "date": "2026-09-22", "qty": 0}).status_code == 422
    assert client.post("/api/entries/production", json={"program_id": "mass-00040633", "date": "2026-09-21", "qty": 0}).status_code == 201
    assert client.post("/api/entries/production", json={"program_id": "nope", "date": "2026-09-21", "qty": 1}).status_code == 404
    log = client.get("/api/audit").json()
    assert log and log[0]["user"] == "quentin@example.com"
    actions = {e["action"] for e in log}
    assert {"create", "upsert"} <= actions


def test_simulated_receipts_cells(client):
    aid = "P-00001046"
    base = client.get(f"/api/articles/{aid}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    sb = {x["key"]: x["values"] for x in base["series"]}
    i = base["periods"].index("2026-09-30")   # a firm order (1600) is expected on that day
    assert sb["supply_firm"][i] == 1600
    # a simulated receipt typed with an expression adds up to the expected order that day (simulated layer only)
    r = client.put("/api/entries/cells", json={"article_id": aid, "date": "2026-09-30", "kind": "sim_receipt", "expression": "1000 + 2*100"})
    assert r.status_code == 200, r.text
    assert r.json()["qty"] == 1200 and r.json()["expression"] == "1000 + 2*100" and r.json()["source"] == "MANUAL"
    assert client.put("/api/entries/cells", json={"article_id": aid, "date": "2026-09-30", "expression": "abc"}).status_code == 422
    assert client.put("/api/entries/cells", json={"article_id": "NOPE", "date": "2026-09-30", "expression": "1"}).status_code == 404
    assert client.put("/api/entries/cells", json={"article_id": aid, "date": "2026-09-23", "kind": "adjustment", "expression": "-(30+20)"}).json()["qty"] == -50
    proj = client.get(f"/api/articles/{aid}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    sa = {x["key"]: x["values"] for x in proj["series"]}
    assert sa["sim_receipts"][i] == 1200 and sa["adjustments"][proj["periods"].index("2026-09-23")] == -50
    assert sa["stock_sim"][i] - sa["stock_firm"][i] == pytest.approx(1200)
    assert sa["supply_firm_sim"][i] == 1600 and sa["actions"][i] == 0
    assert sa["stock_firm"][i] - sb["stock_firm"][i] == pytest.approx(-50)   # adjustments apply to every layer
    assert "target_stock" in sa and "supply_proposed" in sa and "sim_receipts" in sa
    # an explicit 0 keeps the cell (no CBN that day) ; a blank clears it
    assert client.put("/api/entries/cells", json={"article_id": aid, "date": "2026-09-30", "expression": "0"}).json()["qty"] == 0
    zero = client.get(f"/api/articles/{aid}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    sz = {x["key"]: x["values"] for x in zero["series"]}
    assert sz["stock_sim"][i] - sz["stock_firm"][i] == pytest.approx(0)
    assert client.put("/api/entries/cells", json={"article_id": aid, "date": "2026-09-30", "expression": ""}).json() is None
    back = client.get(f"/api/articles/{aid}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    assert {x["key"]: x["values"] for x in back["series"]}["stock_sim"][i] == pytest.approx(sb["stock_sim"][i] - 50)
    cells = client.get("/api/entries/cells", params={"article_id": aid}).json()
    assert len(cells) == 1 and cells[0]["kind"] == "adjustment"
    body = client.get("/api/cockpit", params={"planner": "QUENTIN"}).json()
    assert body["kpis"]["sim_receipt_articles"] == 0
    assert client.delete(f"/api/entries/cells/{cells[0]['id']}").status_code == 204
    assert client.delete(f"/api/entries/cells/{cells[0]['id']}").status_code == 404
    # the CBN complement is listed
    props = client.get("/api/proposals", params={"planner": "QUENTIN"}).json()
    assert props and props[0]["proposal_id"].startswith("PR-")


def test_order_actions_and_late_orders(client):
    aid = "P-00001046"
    proj = client.get(f"/api/articles/{aid}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    orders = proj["orders"]
    firm = next(o for o in orders if o["order_id"] == "PO-000016")
    assert firm["status"] == "expected" and firm["expected_date"] == "2026-09-30" and firm["qty_open"] == 1600 and firm["in_firm_layer"]
    assert all(o["status"] == "expected" for o in orders)
    # delay PO-000016 to 05/10 in two tranches: firm layer unchanged, simulated layer moved
    r = client.put("/api/entries/actions", json={"article_id": aid, "order_id": "PO-000016", "kind": "reschedule",
                                                 "tranches": [{"date": "2026-10-02", "qty": 600}, {"date": "2026-10-05", "qty": 1000}], "note": "retard annoncé"})
    assert r.status_code == 200, r.text
    act = r.json()
    assert act["kind"] == "reschedule" and len(act["tranches"]) == 2 and act["erp"]["qty_open"] == 1600
    assert client.put("/api/entries/actions", json={"article_id": aid, "order_id": "NOPE", "kind": "cancel"}).status_code == 404
    assert client.put("/api/entries/actions", json={"article_id": aid, "order_id": "PO-000017", "kind": "reschedule", "tranches": []}).status_code == 422
    proj2 = client.get(f"/api/articles/{aid}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    s2 = {x["key"]: x["values"] for x in proj2["series"]}
    i, j, k = (proj2["periods"].index(d) for d in ("2026-09-30", "2026-10-02", "2026-10-05"))
    assert s2["supply_firm"][i] == 1600 and s2["supply_firm_sim"][i] == 0 and s2["actions"][i] == -1600
    assert s2["supply_firm_sim"][j] == 600 and s2["supply_firm_sim"][k] == 1000 and s2["actions"][k] == 1000
    assert s2["stock_sim"][i] - s2["stock_firm"][i] == pytest.approx(-1600) and s2["stock_sim"][k] == pytest.approx(s2["stock_firm"][k])
    st = next(o for o in proj2["orders"] if o["order_id"] == "PO-000016")
    assert st["status"] == "simulated" and st["action_id"] == act["id"] and [t["qty"] for t in st["tranches"]] == [600, 1000]
    # replacing the action (one per order) ; cancel ; list ; delete
    r = client.put("/api/entries/actions", json={"article_id": aid, "order_id": "PO-000016", "kind": "cancel"})
    assert r.json()["id"] == act["id"] and r.json()["kind"] == "cancel"
    proj3 = client.get(f"/api/articles/{aid}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    assert next(o for o in proj3["orders"] if o["order_id"] == "PO-000016")["status"] == "cancelled"
    assert len(client.get("/api/entries/actions", params={"article_id": aid}).json()) == 1
    assert client.delete(f"/api/entries/actions/{act['id']}").status_code == 204
    assert client.delete(f"/api/entries/actions/{act['id']}").status_code == 404
    # a past order still open (here an app order dated before the reference) is excluded and "à qualifier"
    r = client.post("/api/entries/orders", json={"article_id": aid, "supplier_id": "S-000545", "expected_date": "2026-09-10", "qty": 700})
    assert r.status_code == 201
    late = client.get("/api/orders", params={"planner": "QUENTIN", "to_qualify": "true"}).json()
    assert len(late) == 1 and late[0]["order_id"] == r.json()["id"] and late[0]["status"] == "late" and late[0]["days_late"] == 9
    proj4 = client.get(f"/api/articles/{aid}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    s4 = {x["key"]: x["values"] for x in proj4["series"]}
    assert s4["stock_firm"] == {x["key"]: x["values"] for x in proj["series"]}["stock_firm"]   # excluded from every layer
    assert proj4["kpis"]["late_order_count"] == 1 and any(a["alert_type"] == "LATE_ORDER" for a in proj4["alerts"])
    assert client.get("/api/cockpit", params={"planner": "QUENTIN"}).json()["kpis"]["late_orders"] == 1
    # qualified: expected on 22/09 → simulated layer only
    r2 = client.put("/api/entries/actions", json={"article_id": aid, "order_id": r.json()["id"], "kind": "reschedule", "tranches": [{"date": "2026-09-22", "qty": 700}]})
    assert r2.status_code == 200, r2.text
    proj5 = client.get(f"/api/articles/{aid}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    s5 = {x["key"]: x["values"] for x in proj5["series"]}
    m = proj5["periods"].index("2026-09-22")
    assert s5["supply_firm_sim"][m] == 700 and s5["supply_firm"][m] == 0 and proj5["kpis"]["late_order_count"] == 0
    assert client.get("/api/orders", params={"planner": "QUENTIN", "to_qualify": "true"}).json() == []
    assert client.get("/api/orders", params={"planner": "QUENTIN", "status": ["simulated"]}).json()[0]["order_id"] == r.json()["id"]
    # rescheduling policy restores the old behaviour as a parameter
    proj6 = client.get(f"/api/articles/{aid}/projection", params={"granularity": "day", "generate_proposals": "false", "late_order_policy": "reschedule"}).json()
    assert proj6["kpis"]["late_order_count"] == 0 and {x["key"]: x["values"] for x in proj6["series"]}["supply_firm"][proj6["periods"].index("2026-09-21")] == 700


def test_default_calendar_grid_and_program_impact(client):
    proj = client.get("/api/articles/P-00001046/projection").json()   # granularity "default"
    assert proj["granularity"] == "default"
    days = [p for p in proj["periods"] if "-W" not in p]
    weeks = [p for p in proj["periods"] if "-W" in p]
    # current ISO week (whole week, as-of is Saturday 19/09) + 2 focus weeks in days: 14/09 → 04/10
    assert days[0] == "2026-09-14" and days[-1] == "2026-10-04" and len(days) == 21
    assert weeks and weeks[0] < "2026-W38" and weeks[-1] > "2026-W40"
    assert proj["periods"][0].startswith("2026-W")   # past weeks first
    assert len(proj["period_end"]) == len(proj["periods"])
    # focus_weeks is a global parameter
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "focus_weeks", "value": 1}).status_code == 200
    proj1 = client.get("/api/articles/P-00001046/projection").json()
    assert len([p for p in proj1["periods"] if "-W" not in p]) == 14
    ov = [o for o in client.get("/api/params/overrides").json() if o["field"] == "focus_weeks"]
    client.delete(f"/api/params/overrides/{ov[0]['id']}")
    # multi-article grid with filters
    grid = client.get("/api/grid", params={"planner": "QUENTIN", "granularity": "week"}).json()
    assert len(grid["articles"]) == 16 and grid["periods"] == proj["periods"][:0] + grid["periods"]
    assert grid["articles"][0]["article"]["article_id"] < grid["articles"][1]["article"]["article_id"]
    assert {x["key"] for x in grid["articles"][0]["series"]} >= {"demand", "sim_receipts", "stock_sim"}
    prog = client.get("/api/grid", params={"planner": "QUENTIN", "program_id": "mass-00040633"}).json()
    assert 0 < len(prog["articles"]) < 16 and all("mass-00040633" in a["programs"] for a in prog["articles"])
    sup = client.get("/api/grid", params={"planner": "QUENTIN", "supplier_id": "S-000545"}).json()
    assert 0 < len(sup["articles"]) < 16
    assert client.get("/api/grid", params={"planner": "QUENTIN", "supplier_id": "S-NOPE"}).json()["articles"] == []
    # programme impact
    imp = client.get("/api/programs/impact", params={"planner": "QUENTIN"}).json()
    assert imp["weeks"] and imp["programs"]
    p0 = imp["programs"][0]
    assert len(p0["planned"]) == len(imp["weeks"]) and set(p0["feasible"]) == {"onhand", "firm", "forecast", "sim"}
    assert all(f <= pl + 1e-6 for f, pl in zip(p0["feasible"]["onhand"], p0["planned"]))
    assert any(p["first_impact"]["onhand"] for p in imp["programs"])


def test_weekly_article_parameters_api(client):
    r = client.get("/api/articles/P-00001046/weekly-params", params={"weeks": 4}).json()
    assert r["fields"][0] == "coverage_target_days" and len(r["weeks"]) == 4 and r["weeks"][0]["week"] == "2026-W38"
    assert r["weeks"][1]["values"]["coverage_target_days"] == r["defaults"]["coverage_target_days"]
    wk = r["weeks"][1]["week"]
    assert client.put("/api/params/overrides", json={"scope": "article_week", "key1": "P-00001046", "key2": "bad", "field": "coverage_target_days", "value": 20}).status_code == 422
    ok = client.put("/api/params/overrides", json={"scope": "article_week", "key1": "P-00001046", "key2": wk, "field": "coverage_target_days", "value": 20})
    assert ok.status_code == 200, ok.text
    r2 = client.get("/api/articles/P-00001046/weekly-params", params={"weeks": 4}).json()
    assert r2["weeks"][1]["values"]["coverage_target_days"] == 20 and r2["weeks"][1]["overridden"] == ["coverage_target_days"]
    assert r2["weeks"][2]["values"]["coverage_target_days"] == r["defaults"]["coverage_target_days"]
    proj = client.get("/api/articles/P-00001046/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    s = {x["key"]: x["values"] for x in proj["series"]}
    i = proj["periods"].index(r2["weeks"][1]["week_start"])
    assert s["target_stock"][i] > s["target_stock"][i - 7]   # 20 days of demand vs the default coverage
    client.delete(f"/api/params/overrides/{ok.json()['id']}")


def test_scenarios_and_simulate(client):
    body = {"name": "PDP +20 %", "description": "hausse", "params": {"horizon_days": 60},
            "events": [{"kind": "plan_factor", "payload": {"factor": 1.2}, "label": "+20 % partout"},
                       {"kind": "add_order", "payload": {"article_id": "P-00003751", "supplier_id": "S-000599",
                                                         "date": "2026-10-05", "qty": 2700}}]}
    r = client.post("/api/scenarios", json=body)
    assert r.status_code == 201, r.text
    sc = r.json()
    assert len(sc["events"]) == 2 and sc["params"]["horizon_days"] == 60
    cmp_ = client.get(f"/api/scenarios/{sc['id']}/compare", params={"planner": "QUENTIN"}).json()
    assert cmp_["scenario_kpis"]["demand_next_30d"] > cmp_["base_kpis"]["demand_next_30d"]
    assert len(cmp_["articles"]) == 16
    cockpit = client.get("/api/cockpit", params={"planner": "QUENTIN", "scenario_id": sc["id"]}).json()
    assert cockpit["horizon_days"] == 60
    assert client.get("/api/cockpit", params={"scenario_id": "nope"}).status_code == 404
    assert client.post("/api/scenarios", json={"name": "x", "events": [{"kind": "bogus"}]}).status_code == 422
    sim = client.post("/api/simulate", json={"planner": "QUENTIN", "article_ids": ["P-00001046"],
                                             "events": [{"kind": "move_order", "payload": {"order_id": "PO-000032", "days": 10}}],
                                             "params": {"generate_proposals": False}})
    assert sim.status_code == 200, sim.text
    assert len(sim.json()["articles"]) == 1
    r = client.put(f"/api/scenarios/{sc['id']}", json={**body, "name": "renommé", "events": body["events"][:1]})
    assert r.json()["name"] == "renommé" and len(r.json()["events"]) == 1
    assert client.delete(f"/api/scenarios/{sc['id']}").status_code == 204
    assert client.get(f"/api/scenarios/{sc['id']}").status_code == 404


def test_params_overrides(client):
    schema = client.get("/api/params/schema").json()
    assert any(p["field"] == "coverage_unit" and p["options"] for p in schema)
    r = client.put("/api/params/overrides", json={"scope": "global", "field": "horizon_days", "value": 45})
    assert r.status_code == 200, r.text
    assert client.get("/api/params/effective").json()["horizon_days"] == 45
    assert client.get("/api/cockpit").json()["horizon_days"] == 45
    r = client.put("/api/params/overrides", json={"scope": "article", "key1": "P-00001046", "field": "coverage_target_days", "value": 20})
    assert r.status_code == 200
    arts = client.get("/api/reference/articles").json()
    assert next(a for a in arts if a["article_id"] == "P-00001046")["coverage_target_days"] == 20
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "not_a_field", "value": 1}).status_code == 422
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "horizon_days", "value": "abc"}).status_code == 422
    rows = client.get("/api/params/overrides").json()
    assert len(rows) == 2
    assert client.delete(f"/api/params/overrides/{rows[0]['id']}").status_code == 204


def test_reference_endpoints(client):
    assert len(client.get("/api/reference/suppliers").json()) == 11
    assert len(client.get("/api/reference/programs").json()) == 25
    assert client.get("/api/reference/bom", params={"article_id": "P-00001046"}).json()
    assert client.get("/api/reference/links", params={"article_id": "P-00005775"}).json()[1]["supplier_id"] == "S-001033"
    assert client.get("/api/reference/plan", params={"program_id": "mass-00040633"}).json()
    assert client.post("/api/reference/refresh").json()["status"] == "ok"


def _legacy_pdp_workbook() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "SOP - PDP"
    ws.append(["PF/SF", "S40-26", "S41-26", "2026-W42", "2027W01", "note"])
    ws.append(["Stators M3", 1000, 2000, 3000, 4000, "x"])
    ws.append(["Programme inconnu", 1, 2, 3, 4, ""])
    ws.append(["mass-00040922", 500, "", 700, 800, ""])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_pdp_import_and_activation(client):
    base = client.get("/api/articles/P-00043859/projection", params={"granularity": "week", "generate_proposals": "false"}).json()
    r = client.post("/api/pdp/import", files={"file": ("pdp.xlsx", _legacy_pdp_workbook())}, data={"name": "PDP S40"})
    assert r.status_code == 201, r.text
    rep = r.json()
    assert rep["created"] == 7 and rep["version"]["active"] and rep["version"]["programs"] == 2
    assert any("inconnu" in n for n in rep["notes"])
    after = client.get("/api/articles/P-00043859/projection", params={"granularity": "week", "generate_proposals": "false"}).json()
    d_base = dict(zip(base["periods"], next(s for s in base["series"] if s["key"] == "demand")["values"]))
    d_after = dict(zip(after["periods"], next(s for s in after["series"] if s["key"] == "demand")["values"]))
    assert d_after["2026-W40"] == pytest.approx(1000)          # qty_per = 1 for the stator stack
    assert d_after["2026-W40"] != d_base["2026-W40"]
    v = rep["version"]["id"]
    assert client.post(f"/api/pdp/versions/{v}/deactivate").json()["active"] is False
    back = client.get("/api/articles/P-00043859/projection", params={"granularity": "week", "generate_proposals": "false"}).json()
    assert next(s for s in back["series"] if s["key"] == "demand")["values"] == next(s for s in base["series"] if s["key"] == "demand")["values"]
    assert len(client.get(f"/api/pdp/versions/{v}/lines").json()) == 7
    assert client.delete(f"/api/pdp/versions/{v}").status_code == 204
    assert client.post("/api/pdp/import", files={"file": ("x.xlsx", b"not an excel")}).status_code == 422


def test_exports_and_reimport(client):
    r = client.get("/api/exports/simulation.xlsx", params={"planner": "QUENTIN", "granularity": "week",
                                                          "article_ids": ["P-00001046", "P-00005775"]})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    wb = load_workbook(io.BytesIO(r.content))
    assert set(wb.sheetnames) == {"PARAMETRES", "ARTICLES", "SIMULATION", "ALERTES", "CARNET_COMMANDES", "SAISIES"}
    ws = wb["SIMULATION"]
    assert ws["A4"].value == "P-00001046" and ws["C4"].value == "Besoin" and str(ws["C9"].value).startswith("Réceptions simulées")
    assert ws.cell(1, 5).value.startswith("2026-W")
    # the grid is made of formulas fed by the other sheets
    assert str(ws["E15"].value).startswith("=IF(PARAMETRES!$B$5")          # stock ferme
    assert str(ws["E17"].value).startswith("=IF(PARAMETRES!$B$5") and "PARAMETRES!$B$8" in str(ws["E17"].value) and "+E9+" in str(ws["E17"].value)   # stock simulé
    assert "CARNET_COMMANDES" in str(ws["E5"].value) and "CARNET_COMMANDES!$O" in str(ws["E6"].value) and "SAISIES" in str(ws["E11"].value)
    assert "OFFSET" in str(ws["E19"].value) and "COUNTIF" in str(ws["E20"].value)
    assert wb["ARTICLES"]["A2"].value == "P-00001046" and wb["ARTICLES"]["I2"].value > 0
    assert client.get("/api/exports/alerts.xlsx").status_code == 200
    assert client.get("/api/exports/orders.xlsx", params={"planner": "QUENTIN"}).status_code == 200
    # fill the SAISIES sheet, type simulated receipts in the grid, post a receipt in the order book and re-import
    ws["F9"] = 1500          # P-00001046, second week → simulated receipt on the first day of that week
    ws["G9"] = 0             # explicit 0: no CBN that week
    ws = wb["SAISIES"]
    ws.append(["COMMANDE", "P-00001046", "S-000545", "2026-10-20", 1600, "réimport", ""])
    ws.append(["RECEPTION", "P-00001046", "S-000545", "2026-09-22", 400, "", ""])
    ws.append(["AJUSTEMENT", "P-00003751", "", "2026-09-21", -30, "casse", ""])
    ws.append(["PRODUCTION", "mass-00040633", "", "2026-09-21", 250, "", ""])
    ws.append(["COMMANDE", "UNKNOWN", "", "2026-10-20", 5, "", ""])
    ws.append(["FOO", "P-00001046", "", "2026-10-20", 5, "", ""])
    wo = wb["CARNET_COMMANDES"]
    assert wo["A2"].value in ("P-00001046", "P-00005775") and wo["D2"].value and str(wo["O2"].value).startswith("=IF(J2")
    wo["M2"] = 250
    wo["N2"] = "2026-09-23"
    # planner actions typed in the order book: a delay in two tranches (extra row with the same reference), a cancellation
    wo["J2"] = "2026-10-12"
    wo["K2"] = 1000
    n_orders = wo.max_row
    wo.append([wo["A2"].value, wo["B2"].value, wo["C2"].value, wo["D2"].value, wo["E2"].value, wo["F2"].value, None, wo["H2"].value,
               None, "2026-10-19", 600, None, None, None])
    wo["L3"] = "ANNULEE"
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/api/imports/entries", files={"file": ("simu.xlsx", buf.getvalue())})
    assert r.status_code == 201, r.text
    assert r.json()["created"] == 9 and r.json()["ignored"] == 2, r.json()
    actions = client.get("/api/entries/actions").json()
    by_order = {a["order_id"]: a for a in actions}
    assert by_order[wo["D2"].value]["kind"] == "reschedule" and by_order[wo["D2"].value]["source"] == "IMPORT"
    assert [(t["date"], t["qty"]) for t in by_order[wo["D2"].value]["tranches"]] == [("2026-10-12", 1000.0), ("2026-10-19", 600.0)]
    assert by_order[wo["D3"].value]["kind"] == "cancel"
    assert n_orders >= 3
    orders = client.get("/api/entries/orders").json()
    assert len(orders) == 1 and orders[0]["source"] == "IMPORT"
    cells = client.get("/api/entries/cells", params={"article_id": "P-00001046"}).json()
    assert {(c["date"], c["qty"], c["source"], c["kind"]) for c in cells} == {("2026-09-21", 1500.0, "IMPORT", "sim_receipt"), ("2026-09-28", 0.0, "IMPORT", "sim_receipt")}
    receipts = client.get("/api/entries/receipts").json()
    assert len(receipts) == 2 and any(r["order_id"] == wo["D2"].value and r["qty"] == 250 for r in receipts)
    assert len(client.get("/api/entries/adjustments").json()) == 1
    assert len(client.get("/api/entries/production").json()) == 1
