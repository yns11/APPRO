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
    # the CBN complement is part of the plan stock: no article stays in stockout
    assert body["kpis"]["proposals"] > 0 and body["kpis"]["plan_articles"] == 0 and body["kpis"]["late_orders"] == 0
    for a in body["articles"]:
        assert a["kpis"]["first_stockout_plan"] is None and a["kpis"]["min_stock_plan"] >= 0, a["article_id"]


def test_projection_day_and_week(client):
    r = client.get("/api/articles/P-00001046/projection", params={"granularity": "day"})
    assert r.status_code == 200, r.text
    day = r.json()
    keys = {s["key"] for s in day["series"]}
    assert {"consumed", "required", "orders_firm", "plan", "stock_erp", "stock_plan", "coverage_plan", "target_stock"} <= keys
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
    stock_before = next(s for s in before["series"] if s["key"] == "stock_plan")["values"]
    # an order typed in the app is a real (firm) order, counted in both scenarios ; the plan is made of lines
    assert client.post("/api/entries/orders", json={"article_id": "P-00001046", "supplier_id": "S-000545",
                                                    "expected_date": "2026-09-25", "qty": 1600, "order_type": "PLANNED"}).status_code == 422
    r = client.post("/api/entries/orders", json={"article_id": "P-00001046", "supplier_id": "S-000545",
                                                 "expected_date": "2026-09-25", "qty": 1600})
    assert r.status_code == 201, r.text
    order = r.json()
    after = client.get("/api/articles/P-00001046/projection", params={"generate_proposals": "false"}).json()
    stock_after = next(s for s in after["series"] if s["key"] == "stock_plan")["values"]
    i = after["periods"].index("2026-09-25")
    assert stock_after[i] - stock_before[i] == pytest.approx(1600)
    firm_after = next(s for s in after["series"] if s["key"] == "stock_erp")["values"]
    firm_before = next(s for s in before["series"] if s["key"] == "stock_erp")["values"]
    assert firm_after[i] - firm_before[i] == pytest.approx(1600)  # ERP scenario too
    assert client.post("/api/entries/orders", json={"article_id": "P-00001046", "supplier_id": "S-000545",
                                                    "expected_date": "2026-09-10", "qty": 10}).status_code == 422   # past date
    # receipt closes the order ; a receipt is a fact: not in the future, not before the snapshot
    assert client.post("/api/entries/receipts", json={"article_id": "P-00001046", "receipt_date": "2026-09-24", "qty": 1}).status_code == 422
    assert client.post("/api/entries/receipts", json={"article_id": "P-00001046", "receipt_date": "2026-09-18", "qty": 1}).status_code == 422
    r = client.post("/api/entries/receipts", json={"article_id": "P-00001046", "order_id": order["id"],
                                                   "receipt_date": "2026-09-19", "qty": 1600})
    assert r.status_code == 201, r.text
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


def test_adjustment_cells_any_date(client):
    aid = "P-00001046"
    base = client.get(f"/api/articles/{aid}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    sb = {x["key"]: x["values"] for x in base["series"]}
    i0 = base["periods"].index(AS_OF)
    # a future adjustment applies at its date ; a past one corrects the reference stock (and the history)
    assert client.put("/api/entries/cells", json={"article_id": aid, "date": "2026-09-23", "kind": "adjustment", "expression": "-(30+20)"}).json()["qty"] == -50
    r = client.put("/api/entries/cells", json={"article_id": aid, "date": "2026-09-10", "kind": "adjustment", "expression": "-300"})
    assert r.status_code == 200, r.text
    assert client.put("/api/entries/cells", json={"article_id": aid, "date": "2026-09-23", "kind": "sim_receipt", "expression": "1"}).status_code == 422
    assert client.put("/api/entries/cells", json={"article_id": aid, "date": "2026-09-23", "expression": "abc"}).status_code == 422
    proj = client.get(f"/api/articles/{aid}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    sa = {x["key"]: x["values"] for x in proj["series"]}
    j = proj["periods"].index("2026-09-23")
    assert sa["adjustments"][j] == -50 and sa["adjustments"][proj["periods"].index("2026-09-10")] == -300
    assert proj["kpis"]["reference_correction"] == -300 and proj["kpis"]["stock_reference"] == pytest.approx(proj["kpis"]["stock_on_hand"] - 300)
    assert sa["stock_erp"][i0] - sb["stock_erp"][i0] == pytest.approx(-300)
    assert sa["stock_plan"][j] - sb["stock_plan"][j] == pytest.approx(-350)
    # the past stock is reconstructed backwards: corrected from the day of the correction, untouched before
    k = proj["periods"].index("2026-09-10")
    assert sa["stock_erp"][k] - sb["stock_erp"][k] == pytest.approx(-300) and sa["stock_erp"][k - 1] == pytest.approx(sb["stock_erp"][k - 1])
    # blank or 0 clears the cell
    assert client.put("/api/entries/cells", json={"article_id": aid, "date": "2026-09-23", "expression": "0"}).json() is None
    cells = client.get("/api/entries/cells", params={"article_id": aid}).json()
    assert len(cells) == 1 and cells[0]["date"] == "2026-09-10"
    assert client.delete(f"/api/entries/cells/{cells[0]['id']}").status_code == 204
    props = client.get("/api/proposals", params={"planner": "QUENTIN"}).json()
    assert props and props[0]["proposal_id"].startswith("PR-")


def test_plan_lines_overrides_free_lines_and_not_received(client):
    aid = "P-00001046"
    proj = client.get(f"/api/articles/{aid}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    firm = next(o for o in proj["orders"] if o["order_id"] == "PO-000016")
    assert firm["status"] == "expected" and firm["expected_date"] == "2026-09-30" and firm["qty_open"] == 1600
    erp_line = next(l for l in proj["plan_lines"] if l["order_id"] == "PO-000016")
    assert erp_line["origin"] == "erp" and erp_line["counted"] and erp_line["line_id"] is None
    assert any(l["origin"] == "cbn" for l in client.get(f"/api/articles/{aid}/projection", params={"granularity": "day"}).json()["plan_lines"])
    # delay PO-000016: 600 on 02/10 and 1000 on 05/10 (two lines, same order) → ERP scenario unchanged
    r = client.put("/api/entries/plan", json={"article_id": aid, "order_id": "PO-000016", "date": "2026-10-02", "qty": 600, "note": "retard annoncé"})
    assert r.status_code == 200, r.text
    l1 = r.json()
    assert l1["order_id"] == "PO-000016" and l1["erp"]["qty_open"] == 1600 and l1["source"] == "MANUAL"
    l2 = client.put("/api/entries/plan", json={"article_id": aid, "order_id": "PO-000016", "date": "2026-10-05", "qty": 1000}).json()
    assert client.put("/api/entries/plan", json={"article_id": aid, "order_id": "NOPE", "date": "2026-10-05", "qty": 1}).status_code == 404
    assert client.put("/api/entries/plan", json={"article_id": aid, "date": "2026-09-01", "qty": 5}).status_code == 422   # past date
    proj2 = client.get(f"/api/articles/{aid}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    s2 = {x["key"]: x["values"] for x in proj2["series"]}
    i, j, k = (proj2["periods"].index(d) for d in ("2026-09-30", "2026-10-02", "2026-10-05"))
    assert s2["orders_firm"][i] == 1600 and s2["plan"][i] == 0 and s2["plan"][j] == 600 and s2["plan"][k] == 1000
    assert s2["stock_plan"][i] - s2["stock_erp"][i] == pytest.approx(-1600) and s2["stock_plan"][k] == pytest.approx(s2["stock_erp"][k])
    st = next(o for o in proj2["orders"] if o["order_id"] == "PO-000016")
    assert st["status"] == "planned" and st["plan_qty"] == 1600 and st["plan_dates"] == ["2026-10-02", "2026-10-05"]
    assert [l["origin"] for l in proj2["plan_lines"] if l["order_id"] == "PO-000016"] == ["override", "override"]
    # update a line, then a free line typed in the grid cell, then a cell on a day with one ERP order
    assert client.put("/api/entries/plan", json={"article_id": aid, "line_id": l2["id"], "order_id": "PO-000016", "date": "2026-10-06", "qty": 1000}).json()["date"] == "2026-10-06"
    free = client.put("/api/entries/plan/cell", json={"article_id": aid, "date": "2026-09-24", "expression": "2*150"})
    assert free.status_code == 200 and free.json()["qty"] == 300 and free.json()["order_id"] is None
    over = client.put("/api/entries/plan/cell", json={"article_id": aid, "date": "2026-10-07", "expression": "0"})   # PO-000017 → nothing expected
    assert over.status_code == 200 and over.json()["order_id"] == "PO-000017" and over.json()["qty"] == 0
    assert client.put("/api/entries/plan/cell", json={"article_id": aid, "date": "2026-09-24", "expression": ""}).json() is None   # free line removed
    assert client.put("/api/entries/plan/cell", json={"article_id": aid, "date": "2026-10-07", "expression": ""}).json() is None   # back to ERP
    lines = client.get("/api/entries/plan", params={"article_id": aid}).json()
    assert {(l["date"], l["qty"]) for l in lines} == {("2026-10-02", 600.0), ("2026-10-06", 1000.0)}
    assert client.delete(f"/api/entries/plan/{l1['id']}").status_code == 204
    assert client.delete(f"/api/entries/plan/{l1['id']}").status_code == 404
    assert client.get("/api/cockpit", params={"planner": "QUENTIN"}).json()["kpis"]["plan_articles"] == 1
    # a past ERP order still open (here an app order dated before the reference) is excluded and "non reçue"
    r = client.post("/api/entries/orders", json={"article_id": aid, "supplier_id": "S-000545", "expected_date": "2026-09-10", "qty": 700, "force": True})
    assert r.status_code == 201, r.text
    late = client.get("/api/orders", params={"planner": "QUENTIN", "not_received": "true"}).json()
    assert len(late) == 1 and late[0]["order_id"] == r.json()["id"] and late[0]["status"] == "not_received" and late[0]["days_late"] == 9
    proj4 = client.get(f"/api/articles/{aid}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    assert proj4["kpis"]["backlog_count"] == 1 and proj4["kpis"]["backlog_qty"] == 700
    assert any(a["alert_type"] == "LATE_ORDER" for a in proj4["alerts"])
    assert client.get("/api/cockpit", params={"planner": "QUENTIN"}).json()["kpis"]["late_orders"] == 1
    # dated in the plan → plan scenario only, no longer "non reçue"
    r2 = client.put("/api/entries/plan", json={"article_id": aid, "order_id": r.json()["id"], "date": "2026-09-22", "qty": 700})
    assert r2.status_code == 200, r2.text
    proj5 = client.get(f"/api/articles/{aid}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    s5 = {x["key"]: x["values"] for x in proj5["series"]}
    m = proj5["periods"].index("2026-09-22")
    assert s5["plan"][m] == 700 and s5["orders_firm"][m] == 0 and proj5["kpis"]["backlog_count"] == 0
    assert client.get("/api/orders", params={"planner": "QUENTIN", "not_received": "true"}).json() == []
    assert client.get("/api/orders", params={"planner": "QUENTIN", "status": ["planned"]}).json()[0]["order_id"] == r.json()["id"]
    # past orders are displayed in the history row
    assert sum(s5["orders_firm_hist"]) > 0


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
    assert {x["key"] for x in grid["articles"][0]["series"]} >= {"consumed", "required", "plan", "stock_plan"}
    assert "plan_lines" in grid["articles"][0] and "orders" in grid["articles"][0]
    prog = client.get("/api/grid", params={"planner": "QUENTIN", "program_id": "mass-00040633"}).json()
    assert 0 < len(prog["articles"]) < 16 and all("mass-00040633" in a["programs"] for a in prog["articles"])
    sup = client.get("/api/grid", params={"planner": "QUENTIN", "supplier_id": "S-000545"}).json()
    assert 0 < len(sup["articles"]) < 16
    assert client.get("/api/grid", params={"planner": "QUENTIN", "supplier_id": "S-NOPE"}).json()["articles"] == []
    # programme impact
    imp = client.get("/api/programs/impact", params={"planner": "QUENTIN"}).json()
    assert imp["weeks"] and imp["programs"]
    p0 = imp["programs"][0]
    assert len(p0["planned"]) == len(imp["weeks"]) and set(p0["feasible"]) == {"onhand", "erp", "plan"}
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
    assert set(wb.sheetnames) == {"PARAMETRES", "ARTICLES", "SIMULATION", "ALERTES", "PLAN", "SAISIES"}
    ws = wb["SIMULATION"]
    assert ws["A4"].value == "P-00001046" and ws["C4"].value == "Consommé" and ws["C5"].value == "Requis" and str(ws["C9"].value).startswith("Plan")
    assert ws.cell(1, 5).value.startswith("2026-W")
    # the grid is made of formulas fed by the other sheets
    assert str(ws["E13"].value).startswith("=IF(PARAMETRES!$B$5")          # Scenario ERP
    assert str(ws["E14"].value).startswith("=IF(PARAMETRES!$B$5") and "+E9+E10+" in str(ws["E14"].value)   # Scenario Plan = … + plan + CBN
    assert "PLAN!$G" in str(ws["E6"].value) and "PLAN!$I" in str(ws["E9"].value) and "SAISIES" in str(ws["E12"].value)
    assert "OFFSET" in str(ws["E16"].value) and "COUNTIF" in str(ws["E17"].value)
    assert wb["ARTICLES"]["A2"].value == "P-00001046" and wb["ARTICLES"]["I2"].value > 0
    assert client.get("/api/exports/alerts.xlsx").status_code == 200
    assert client.get("/api/exports/orders.xlsx", params={"planner": "QUENTIN"}).status_code == 200
    # PLAN sheet: one row per ERP order with the ERP date / quantity and the plan columns prefilled
    wp = wb["PLAN"]
    rows = {wp.cell(r, 4).value: r for r in range(2, wp.max_row + 1) if wp.cell(r, 1).value == "P-00001046"}
    r16 = rows["PO-000016"]
    assert str(wp.cell(r16, 6).value)[:10] == "2026-09-30" and wp.cell(r16, 7).value == 1600 and wp.cell(r16, 9).value == 1600
    # planner edits: delay PO-000016 in two tranches (extra row, same reference), nothing from PO-000017, a free line
    wp.cell(r16, 8, "2026-10-12")
    wp.cell(r16, 9, 1000)
    wp.append(["P-00001046", "RESIN", "FIRM", "PO-000016", "S-000545", None, None, "2026-10-19", 600, "modifiée", None, "tranche 2"])
    r17 = rows["PO-000017"]
    wp.cell(r17, 9, 0)
    wp.append(["P-00001046", "RESIN", "LIBRE", None, "S-000545", None, None, "2026-09-24", 250, "LIBRE", None, "dépannage"])
    ws = wb["SAISIES"]
    ws.append(["COMMANDE", "P-00001046", "S-000545", "2026-10-20", 1600, "réimport", ""])
    ws.append(["RECEPTION", "P-00001046", "S-000545", "2026-09-22", 400, "", ""])
    ws.append(["AJUSTEMENT", "P-00003751", "", "2026-09-21", -30, "casse", ""])
    ws.append(["PRODUCTION", "mass-00040633", "", "2026-09-21", 250, "", ""])
    ws.append(["COMMANDE", "UNKNOWN", "", "2026-10-20", 5, "", ""])
    ws.append(["FOO", "P-00001046", "", "2026-10-20", 5, "", ""])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/api/imports/entries", files={"file": ("simu.xlsx", buf.getvalue())})
    assert r.status_code == 201, r.text
    assert r.json()["ignored"] == 2, r.json()
    orders = client.get("/api/entries/orders").json()
    assert len(orders) == 1 and orders[0]["source"] == "IMPORT"
    lines = client.get("/api/entries/plan", params={"article_id": "P-00001046"}).json()
    assert {(l["order_id"], l["date"], l["qty"], l["source"]) for l in lines} == {
        ("PO-000016", "2026-10-12", 1000.0, "IMPORT"), ("PO-000016", "2026-10-19", 600.0, "IMPORT"),
        ("PO-000017", "2026-10-07", 0.0, "IMPORT"), (None, "2026-09-24", 250.0, "IMPORT")}
    assert client.get("/api/entries/plan", params={"article_id": "P-00005775"}).json() == []
    assert len(client.get("/api/entries/receipts").json()) == 1
    assert len(client.get("/api/entries/adjustments").json()) == 1
    assert len(client.get("/api/entries/production").json()) == 1
    # re-importing the same workbook replaces the plan of the articles it contains (no duplicates)
    r = client.post("/api/imports/entries", files={"file": ("simu.xlsx", buf.getvalue())})
    assert r.status_code == 201
    assert len(client.get("/api/entries/plan", params={"article_id": "P-00001046"}).json()) == 4


def test_pdp_template(client):
    r = client.get("/api/pdp/template.xlsx", params={"weeks": 6})
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["SOP - PDP", "NOTICE"]
    ws = wb["SOP - PDP"]
    assert ws["C1"].value == "2026-W38" and ws["H1"].value == "2026-W43" and ws.max_row > 2 and ws["B2"].value
    # the template, once filled, imports
    ws["C2"] = 1234
    buf = io.BytesIO()
    wb.save(buf)
    rep = client.post("/api/pdp/import", files={"file": ("pdp.xlsx", buf.getvalue())}, data={"name": "modèle"}).json()
    assert rep["created"] >= 1 and rep["version"]["active"]
