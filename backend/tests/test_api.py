"""End-to-end API tests on an isolated context (seed CSV facts + in-memory SQLite store holding the
seed reference tables)."""
from __future__ import annotations

import datetime as dt
import io

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
from sqlalchemy import create_engine
from tests.conftest import SEED

from appro.api.main import create_app
from appro.config import Settings
from appro.data.store import init_store
from appro.services import mrp_service
from appro.services.context import AppContext, bootstrap_reference, set_context

AS_OF = "2026-09-19"
AID = "P-00001046"


@pytest.fixture()
def client(seed_source, tmp_path):
    # a file database: several connections, like the PostgreSQL pool of the deployed application
    engine = create_engine(f"sqlite:///{tmp_path / 'appro.db'}", connect_args={"check_same_thread": False})
    factory = init_store(engine)
    assert bootstrap_reference(factory, SEED) > 0
    settings = Settings(data_source="local", seed_dir=SEED, as_of=dt.date.fromisoformat(AS_OF), horizon_days=90,
                        static_dir=SEED / "no-such-dir")
    ctx = AppContext(settings=settings, source=seed_source, session_factory=factory)
    set_context(ctx)
    try:
        yield TestClient(create_app(), headers={"x-forwarded-email": "quentin@example.com"})
    finally:
        set_context(None)


def _series(proj: dict) -> dict[str, list[float]]:
    return {x["key"]: x["values"] for x in proj["series"]}


def test_health_and_config(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["database_status"] == "ok" and h["reference_rows"] == 16 and "lakebase_env" in h
    cfg = client.get("/api/config").json()
    assert cfg["as_of"] == AS_OF and cfg["planners"] == ["QUENTIN"] and cfg["user"] == "quentin@example.com"
    assert cfg["reference_empty"] is False


def test_cockpit(client):
    r = client.get("/api/cockpit", params={"planner": "QUENTIN"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["kpis"]["articles"] == 16 and len(body["articles"]) == 16 and body["as_of"] == AS_OF
    first = body["articles"][0]
    assert {"article_id", "kpis", "sparkline", "severity"} <= set(first)
    assert body["weekly_supply_demand"] and body["weekly_supply_demand"][0]["week"].startswith("2026-W")
    assert body["kpis"]["proposals"] > 0 and body["kpis"]["plan_articles"] == 0
    for a in body["articles"]:
        assert a["kpis"]["first_stockout_plan"] is None and a["kpis"]["min_stock_plan"] >= 0, a["article_id"]
    assert isinstance(body["backlog"], list)


def test_projection_day_week_and_lanes(client):
    r = client.get(f"/api/articles/{AID}/projection", params={"granularity": "day"})
    assert r.status_code == 200, r.text
    day = r.json()
    keys = {s["key"] for s in day["series"]}
    assert {"demand", "orders_firm", "plan", "stock_erp", "stock_plan", "coverage_plan", "target_stock", "supply_proposed"} <= keys
    assert len(day["periods"]) == len(day["series"][0]["values"])
    assert day["programs"] and day["suppliers"][0]["supplier_id"] == "S-000545"
    assert len(day["lanes"]) == 1 and day["lanes"][0]["supplier_id"] == "S-000545"
    lane_keys = {s["key"] for s in day["lanes"][0]["series"]}
    assert lane_keys == {"orders_firm", "orders_firm_hist", "orders_forecast", "receipts", "plan", "supply_proposed",
                         "orders_firm_ordered", "orders_firm_open"}
    assert len(day["lanes"][0]["orders_ignored"]) == len(day["periods"])
    assert len(day["lanes"][0]["plan_typed"]) == len(day["periods"])
    week = client.get(f"/api/articles/{AID}/projection", params={"granularity": "week"}).json()
    assert len(week["periods"]) < len(day["periods"])
    assert abs(sum(_series(day)["demand"]) - sum(_series(week)["demand"])) < 0.01
    assert client.get("/api/articles/NOPE/projection").status_code == 404
    # a two-supplier article has two lanes
    two = client.get("/api/articles/P-00005775/projection", params={"granularity": "day"}).json()
    assert [l["supplier_id"] for l in two["lanes"]] == ["S-000032", "S-001033"]


def test_adjustment_cells_any_date_with_note(client):
    base = client.get(f"/api/articles/{AID}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    sb = _series(base)
    i0 = base["periods"].index(AS_OF)
    r = client.put("/api/entries/adjustments", json={"article_id": AID, "date": "2026-09-23", "expression": "-(30+20)", "note": "casse"})
    assert r.status_code == 200 and r.json()["qty"] == -50 and r.json()["note"] == "casse"
    assert client.put("/api/entries/adjustments", json={"article_id": AID, "date": "2026-09-10", "expression": "-300"}).status_code == 200
    assert client.put("/api/entries/adjustments", json={"article_id": AID, "date": "2026-09-23", "expression": "abc"}).status_code == 422
    assert client.put("/api/entries/adjustments", json={"article_id": "NOPE", "date": "2026-09-23", "expression": "1"}).status_code == 404
    proj = client.get(f"/api/articles/{AID}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    sa = _series(proj)
    j = proj["periods"].index("2026-09-23")
    assert sa["adjustments"][j] == -50 and sa["adjustments"][proj["periods"].index("2026-09-10")] == -300
    assert proj["kpis"]["reference_correction"] == -300 and proj["kpis"]["stock_reference"] == pytest.approx(proj["kpis"]["stock_on_hand"] - 300)
    assert sa["stock_erp"][i0] - sb["stock_erp"][i0] == pytest.approx(-300)
    assert sa["stock_plan"][j] - sb["stock_plan"][j] == pytest.approx(-350)
    k = proj["periods"].index("2026-09-10")
    assert sa["stock_erp"][k] - sb["stock_erp"][k] == pytest.approx(-300) and sa["stock_erp"][k - 1] == pytest.approx(sb["stock_erp"][k - 1])
    # note only, then clear
    assert client.put("/api/entries/adjustments", json={"article_id": AID, "date": "2026-09-23", "note": "casse confirmée"}).json()["qty"] == -50
    assert client.put("/api/entries/adjustments", json={"article_id": AID, "date": "2026-09-23", "expression": "0"}).json() is None
    cells = client.get("/api/entries/adjustments", params={"article_id": AID}).json()
    assert len(cells) == 1 and cells[0]["date"] == "2026-09-10"
    assert client.delete(f"/api/entries/adjustments/{cells[0]['id']}").status_code == 204
    log = client.get("/api/entries/audit").json()
    assert log and log[0]["user"] == "quentin@example.com"


def test_plan_cells_typed_erp_zero_and_backlog(client):
    proj = client.get(f"/api/articles/{AID}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    s0 = _series(proj)
    i = proj["periods"].index("2026-09-30")
    assert s0["orders_firm"][i] == 1600 and s0["plan"][i] == 1600           # no cell: plan = ERP
    lane = proj["lanes"][0]
    assert not lane["plan_typed"][i]
    orders = {o["order_id"]: o for o in lane["orders"]}
    assert orders["PO-000016"]["qty_open"] == 1600 and orders["PO-000016"]["order_type"] == "FIRM"
    # the planner types: nothing on 30/09, 600 on 02/10, 1000 on 05/10 (ERP scenario unchanged)
    r = client.put("/api/entries/plan", json={"article_id": AID, "supplier_id": "S-000545", "date": "2026-09-30", "expression": "0", "note": "retard annoncé"})
    assert r.status_code == 200 and r.json()["qty"] == 0 and r.json()["note"] == "retard annoncé"
    assert client.put("/api/entries/plan", json={"article_id": AID, "supplier_id": "S-000545", "date": "2026-10-02", "expression": "600"}).status_code == 200
    assert client.put("/api/entries/plan", json={"article_id": AID, "supplier_id": "S-000545", "date": "2026-10-05", "expression": "2*500"}).json()["qty"] == 1000
    assert client.put("/api/entries/plan", json={"article_id": AID, "date": "2026-09-01", "expression": "5"}).status_code == 422   # past
    assert client.put("/api/entries/plan", json={"article_id": AID, "date": "2026-10-01", "expression": "-5"}).status_code == 422  # negative
    proj2 = client.get(f"/api/articles/{AID}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    s2 = _series(proj2)
    i, j, k = (proj2["periods"].index(d) for d in ("2026-09-30", "2026-10-02", "2026-10-05"))
    assert s2["orders_firm"][i] == 1600 and s2["plan"][i] == 0 and s2["plan"][j] == 600 and s2["plan"][k] == 1000
    assert s2["stock_plan"][i] - s2["stock_erp"][i] == pytest.approx(-1600) and s2["stock_plan"][k] == pytest.approx(s2["stock_erp"][k])
    assert proj2["lanes"][0]["plan_typed"][i] and proj2["lanes"][0]["plan_typed"][j] and proj2["kpis"]["plan_cell_count"] == 3
    # blank = back to the ERP ; note only keeps the quantity
    assert client.put("/api/entries/plan", json={"article_id": AID, "supplier_id": "S-000545", "date": "2026-09-30", "expression": ""}).json() is None
    assert client.put("/api/entries/plan", json={"article_id": AID, "supplier_id": "S-000545", "date": "2026-10-02", "note": "tranche 1"}).json()["qty"] == 600
    cells = client.get("/api/entries/plan", params={"article_id": AID}).json()
    assert {(c["date"], c["qty"]) for c in cells} == {("2026-10-02", 600.0), ("2026-10-05", 1000.0)}
    assert client.get("/api/cockpit", params={"planner": "QUENTIN"}).json()["kpis"]["plan_articles"] == 1
    assert client.delete(f"/api/entries/plan/{cells[0]['id']}").status_code == 204
    assert client.delete(f"/api/entries/plan/{cells[0]['id']}").status_code == 404
    # backlog: cumulative per supplier over the window, listed without any action to take
    bl = client.get("/api/backlog", params={"planner": "QUENTIN"}).json()
    assert all(row["backlog"] > 0 and row["ordered"] >= row["received"] for row in bl)
    cockpit = client.get("/api/cockpit", params={"planner": "QUENTIN"}).json()
    assert cockpit["kpis"]["backlog_articles"] == len({row["article_id"] for row in bl})


def test_default_calendar_grid_and_program_impact(client):
    proj = client.get(f"/api/articles/{AID}/projection").json()   # granularity "default"
    days = [p for p in proj["periods"] if "-W" not in p]
    weeks = [p for p in proj["periods"] if "-W" in p]
    assert days[0] == "2026-09-14" and days[-1] == "2026-10-04" and len(days) == 21
    assert weeks and weeks[0] < "2026-W38" and weeks[-1] > "2026-W40" and proj["periods"][0].startswith("2026-W")
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "focus_weeks", "value": 1}).status_code == 200
    proj1 = client.get(f"/api/articles/{AID}/projection").json()
    assert len([p for p in proj1["periods"] if "-W" not in p]) == 14
    ov = [o for o in client.get("/api/params/overrides").json() if o["field"] == "focus_weeks"]
    client.delete(f"/api/params/overrides/{ov[0]['id']}")
    grid = client.get("/api/grid", params={"planner": "QUENTIN", "granularity": "week"}).json()
    assert len(grid["articles"]) == 16 and grid["articles"][0]["article"]["article_id"] < grid["articles"][1]["article"]["article_id"]
    assert {x["key"] for x in grid["articles"][0]["series"]} >= {"demand", "stock_plan", "supply_proposed"} and grid["articles"][0]["lanes"]
    assert grid["total"] == 16 and grid["page"] == 1 and len(grid["articles"]) <= grid["page_size"]
    prog = client.get("/api/grid", params={"planner": "QUENTIN", "program_id": "mass-00040633"}).json()
    assert 0 < len(prog["articles"]) < 16 and all("mass-00040633" in a["programs"] for a in prog["articles"])
    sup = client.get("/api/grid", params={"planner": "QUENTIN", "supplier_id": "S-000545"}).json()
    assert 0 < len(sup["articles"]) < 16
    assert client.get("/api/grid", params={"planner": "QUENTIN", "supplier_id": "S-NOPE"}).json()["articles"] == []
    imp = client.get("/api/programs/impact", params={"planner": "QUENTIN"}).json()
    assert imp["weeks"] and imp["programs"]
    p0 = imp["programs"][0]
    assert len(p0["planned"]) == len(imp["weeks"]) and set(p0["feasible"]) == {"onhand", "erp", "plan"}


def test_weekly_article_parameters_api(client):
    r = client.get(f"/api/articles/{AID}/weekly-params", params={"weeks": 4}).json()
    # at least 4 weeks, and up to the last PDP week / end of horizon (90 days here = 13 weeks)
    assert r["fields"][0] == "coverage_target_days" and len(r["weeks"]) >= 13 and r["weeks"][0]["week"] == "2026-W38"
    wk = r["weeks"][1]["week"]
    assert client.put("/api/params/overrides", json={"scope": "article_week", "key1": AID, "key2": "bad", "field": "coverage_target_days", "value": 20}).status_code == 422
    ok = client.put("/api/params/overrides", json={"scope": "article_week", "key1": AID, "key2": wk, "field": "coverage_target_days", "value": 20})
    assert ok.status_code == 200, ok.text
    r2 = client.get(f"/api/articles/{AID}/weekly-params", params={"weeks": 4}).json()
    assert r2["weeks"][1]["values"]["coverage_target_days"] == 20 and r2["weeks"][1]["overridden"] == ["coverage_target_days"]
    proj = client.get(f"/api/articles/{AID}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    s = _series(proj)
    i = proj["periods"].index(r2["weeks"][1]["week_start"])
    assert s["target_stock"][i] > s["target_stock"][i - 7]
    client.delete(f"/api/params/overrides/{ok.json()['id']}")


def test_params_overrides(client):
    schema = client.get("/api/params/schema").json()
    assert any(p["field"] == "coverage_unit" and p["options"] for p in schema)
    assert any(p["field"] == "backlog_days" for p in schema)
    r = client.put("/api/params/overrides", json={"scope": "global", "field": "horizon_days", "value": 45})
    assert r.status_code == 200, r.text
    assert client.get("/api/params/effective").json()["horizon_days"] == 45
    assert client.get("/api/cockpit").json()["horizon_days"] == 45
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "not_a_field", "value": 1}).status_code == 422
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "horizon_days", "value": "abc"}).status_code == 422
    assert client.put("/api/params/overrides", json={"scope": "article", "key1": AID, "field": "coverage_target_days", "value": 20}).status_code == 422
    rows = client.get("/api/params/overrides").json()
    assert len(rows) == 1 and client.delete(f"/api/params/overrides/{rows[0]['id']}").status_code == 204


def test_reference_crud_template_and_import(client):
    tables = client.get("/api/reference/tables").json()
    names = {t["name"]: t for t in tables}
    assert set(names) == {"ref_articles", "ref_suppliers", "ref_article_suppliers", "ref_programs", "ref_bom", "fct_stock",
                          "ref_planners", "ref_delegations"}
    assert names["ref_articles"]["rows"] == 16 and names["ref_suppliers"]["rows"] == 11 and names["ref_programs"]["rows"] == 25
    assert names["ref_articles"]["key"] == ["article_id"] and any(c["label"] == "Couverture cible (j)" for c in names["ref_articles"]["columns"])
    # edit a threshold directly: the engine sees it
    row = next(r for r in client.get("/api/reference/ref_articles/rows").json() if r["article_id"] == AID)
    assert row["coverage_target_days"] == 10
    r = client.put("/api/reference/ref_articles/rows", json={"values": {**row, "coverage_target_days": 20}})
    assert r.status_code == 200, r.text
    assert next(a for a in client.get("/api/reference/articles").json() if a["article_id"] == AID)["coverage_target_days"] == 20
    assert client.get(f"/api/articles/{AID}/projection").json()["kpis"]["coverage_target_days"] == 20
    assert client.put("/api/reference/ref_articles/rows", json={"values": {"designation": "x"}}).status_code == 422
    assert client.get("/api/reference/nope/rows").status_code == 404
    # create a supplier link, then delete it
    r = client.put("/api/reference/ref_article_suppliers/rows", json={"values": {"article_id": AID, "supplier_id": "S-000599", "moq": 10, "pack_qty": 5, "lead_time_days": 3, "quota_pct": 0, "priority": 2, "active": "oui"}})
    assert r.status_code == 200 and r.json()["active"] is True
    assert [l["supplier_id"] for l in client.get(f"/api/articles/{AID}/projection").json()["lanes"]] == ["S-000545", "S-000599"]
    assert client.post("/api/reference/ref_article_suppliers/delete", json={"key": {"article_id": AID, "supplier_id": "S-000599"}}).status_code == 204
    assert client.post("/api/reference/ref_article_suppliers/delete", json={"key": {"article_id": AID, "supplier_id": "S-000599"}}).status_code == 404
    # template: French headers + example row + notice ; filled export ; import replace / merge
    t = client.get("/api/reference/ref_suppliers/template.xlsx")
    assert t.status_code == 200
    wb = load_workbook(io.BytesIO(t.content))
    assert wb.sheetnames == ["Fournisseurs", "NOTICE"]
    ws = wb["Fournisseurs"]
    assert [c.value for c in ws[1]] == ["Fournisseur", "Nom", "Pays", "Contact", "Jours de livraison", "Actif"]
    ws["A2"], ws["B2"], ws["E2"], ws["F2"] = "S-NEW", "New supplier", "1,3", "oui"
    buf = io.BytesIO()
    wb.save(buf)
    rep = client.post("/api/reference/ref_suppliers/import", files={"file": ("f.xlsx", buf.getvalue())}, data={"mode": "merge"})
    assert rep.status_code == 201, rep.text
    assert rep.json()["created"] == 1
    sups = {r["supplier_id"]: r for r in client.get("/api/reference/ref_suppliers/rows").json()}
    assert "S-NEW" in sups and sups["S-NEW"]["delivery_weekdays"] == "1,3" and len(sups) == 12
    filled = load_workbook(io.BytesIO(client.get("/api/reference/ref_suppliers/template.xlsx", params={"filled": "true"}).content))
    assert filled["Fournisseurs"].max_row == 13
    rep2 = client.post("/api/reference/ref_suppliers/import", files={"file": ("f.xlsx", buf.getvalue())}, data={"mode": "replace"})
    assert rep2.status_code == 201 and len(client.get("/api/reference/ref_suppliers/rows").json()) == 1
    # missing mandatory column
    wb2 = Workbook()
    wb2.active.append(["Fournisseur", "Pays"])
    wb2.active.append(["S-X", "FR"])
    b2 = io.BytesIO()
    wb2.save(b2)
    assert client.post("/api/reference/ref_suppliers/import", files={"file": ("f.xlsx", b2.getvalue())}).status_code == 422
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
    d_base = dict(zip(base["periods"], _series(base)["demand"]))
    d_after = dict(zip(after["periods"], _series(after)["demand"]))
    assert d_after["2026-W40"] == pytest.approx(1000) and d_after["2026-W40"] != d_base["2026-W40"]
    v = rep["version"]["id"]
    assert client.post(f"/api/pdp/versions/{v}/deactivate").json()["active"] is False
    back = client.get("/api/articles/P-00043859/projection", params={"granularity": "week", "generate_proposals": "false"}).json()
    assert _series(back)["demand"] == _series(base)["demand"]
    assert len(client.get(f"/api/pdp/versions/{v}/lines").json()) == 7
    assert client.delete(f"/api/pdp/versions/{v}").status_code == 204
    assert client.post("/api/pdp/import", files={"file": ("x.xlsx", b"not an excel")}).status_code == 422


def test_exports_and_reimport(client):
    r = client.get("/api/exports/simulation.xlsx", params={"planner": "QUENTIN", "granularity": "day", "article_ids": [AID, "P-00005775"]})
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/vnd.openxmlformats")
    wb = load_workbook(io.BytesIO(r.content))
    assert set(wb.sheetnames) == {"PARAMETRES", "ARTICLES", "SIMULATION", "ALERTES"}
    ws = wb["SIMULATION"]
    labels = [ws.cell(r, 3).value for r in range(4, ws.max_row + 1) if ws.cell(r, 1).value == AID]
    assert list(labels[:5]) == ["Besoin", "Ferme", "Prévisionnel", "Reçu", "Plan"] and "Scenario Plan" in labels
    labels2 = [ws.cell(r, 3).value for r in range(4, ws.max_row + 1) if ws.cell(r, 1).value == "P-00005775"]
    assert "Plan · S-000032" in labels2 and "Plan · S-001033" in labels2         # two supplier lanes
    assert ws.cell(1, 5).value == AS_OF
    rows = {ws.cell(r, 3).value: r for r in range(4, ws.max_row + 1) if ws.cell(r, 1).value == AID}
    assert str(ws.cell(rows["Scenario ERP"], 5).value).startswith("=IF(PARAMETRES!$B$5")
    assert "OFFSET" in str(ws.cell(rows["Stock cible"], 5).value) and "COUNTIF" in str(ws.cell(rows["Couverture plan (périodes)"], 5).value)
    assert wb["ARTICLES"]["A2"].value == AID and wb["ARTICLES"]["I2"].value > 0
    assert client.get("/api/exports/alerts.xlsx").status_code == 200
    assert client.get("/api/exports/plan.xlsx", params={"planner": "QUENTIN"}).status_code == 200
    # planner edits in Excel: delay the 30/09 order (1600) to 06/10, an adjustment on 22/09
    cols = {ws.cell(1, c).value: c for c in range(5, ws.max_column + 1)}
    assert ws.cell(rows["Ferme"], cols["2026-09-30"]).value == 1600 and ws.cell(rows["Plan"], cols["2026-09-30"]).value == 1600
    ws.cell(rows["Plan"], cols["2026-09-30"], 0)
    ws.cell(rows["Plan"], cols["2026-10-06"], 1600)
    ws.cell(rows["Ajustement"], cols["2026-09-22"], -50)
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post("/api/imports/simulation", files={"file": ("simu.xlsx", buf.getvalue())})
    assert r.status_code == 201, r.text
    plan = client.get("/api/entries/plan", params={"article_id": AID}).json()
    assert {(c["date"], c["qty"]) for c in plan} == {("2026-09-30", 0.0), ("2026-10-06", 1600.0)}
    adj = client.get("/api/entries/adjustments", params={"article_id": AID}).json()
    assert [(c["date"], c["qty"]) for c in adj] == [("2026-09-22", -50.0)]
    assert client.get("/api/entries/plan", params={"article_id": "P-00005775"}).json() == []
    # re-importing the same workbook is idempotent
    assert client.post("/api/imports/simulation", files={"file": ("simu.xlsx", buf.getvalue())}).status_code == 201
    assert len(client.get("/api/entries/plan", params={"article_id": AID}).json()) == 2
    # week granularity cannot be re-imported
    wk = client.get("/api/exports/simulation.xlsx", params={"granularity": "week", "article_ids": [AID]}).content
    assert client.post("/api/imports/simulation", files={"file": ("w.xlsx", wk)}).status_code == 422


def test_pdp_template(client):
    r = client.get("/api/pdp/template.xlsx", params={"weeks": 6})
    assert r.status_code == 200
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["SOP - PDP", "NOTICE"]
    ws = wb["SOP - PDP"]
    assert ws["C1"].value == "2026-W38" and ws["H1"].value == "2026-W43" and ws.max_row > 2 and ws["B2"].value
    ws["C2"] = 1234
    buf = io.BytesIO()
    wb.save(buf)
    rep = client.post("/api/pdp/import", files={"file": ("pdp.xlsx", buf.getvalue())}, data={"name": "modèle"}).json()
    assert rep["created"] >= 1 and rep["version"]["active"]


def test_flags_toggle_batches_and_delivery_plan(client):
    # ignore the firm order of 30/09: out of the ERP scenario and of the plan, still listed
    body = {"article_id": AID, "supplier_id": "S-000545", "date": "2026-09-30", "kind": "order_ignored"}
    r = client.post("/api/entries/flags/toggle", json=body)
    assert r.status_code == 200 and r.json()["kind"] == "order_ignored"
    proj = client.get(f"/api/articles/{AID}/projection", params={"granularity": "day", "generate_proposals": "false"}).json()
    s = _series(proj)
    i = proj["periods"].index("2026-09-30")
    lane = proj["lanes"][0]
    assert s["orders_firm"][i] == 0 and s["plan"][i] == 0 and lane["orders_ignored"][i]
    assert {x["key"]: x["values"][i] for x in lane["series"]}["orders_firm_ordered"] == 1600
    assert any(o["ignored"] for o in lane["orders"] if o["expected_date"] == "2026-09-30")
    assert proj["kpis"]["ignored_order_days"] == 1
    assert client.get("/api/entries/flags", params={"article_id": AID}).json()[0]["date"] == "2026-09-30"
    assert client.post("/api/entries/flags/toggle", json={**body, "date": "2026-09-01"}).status_code == 422   # past
    assert client.post("/api/entries/flags/toggle", json=body).json() is None                                  # toggle back
    assert client.get("/api/entries/flags", params={"article_id": AID}).json() == []
    # refuse a proposal: nothing proposed until the end of its week
    proj = client.get(f"/api/articles/{AID}/projection", params={"granularity": "day"}).json()
    if proj["proposals"]:
        p = proj["proposals"][0]
        flag = client.post("/api/entries/flags/toggle", json={"article_id": AID, "date": p["delivery_date"], "kind": "proposal_refused", "qty": p["qty"]}).json()
        assert flag["qty"] == p["qty"]
        proj2 = client.get(f"/api/articles/{AID}/projection", params={"granularity": "day"}).json()
        d = dt.date.fromisoformat(p["delivery_date"])
        end = (d + dt.timedelta(days=6 - d.weekday())).isoformat()
        assert all(not (p["delivery_date"] <= q["delivery_date"] <= end) for q in proj2["proposals"])
        assert client.delete(f"/api/entries/flags/{flag['id']}").status_code == 204
    # batches (fill handle): several cells, one transaction ; past dates skipped
    cells = [{"article_id": AID, "supplier_id": "S-000545", "date": d, "expression": "250"} for d in ("2026-09-01", "2026-10-06", "2026-10-07", "2026-10-08")]
    out = client.put("/api/entries/plan/batch", json={"cells": cells}).json()
    assert [c["date"] for c in out] == ["2026-10-06", "2026-10-07", "2026-10-08"] and all(c["qty"] == 250 for c in out)
    adj = client.put("/api/entries/adjustments/batch", json={"cells": [{"article_id": AID, "date": d, "expression": "-10"} for d in ("2026-10-06", "2026-10-07")]}).json()
    assert len(adj) == 2 and all(c["qty"] == -10 for c in adj)
    assert client.put("/api/entries/plan/batch", json={"cells": [{"article_id": AID, "date": "2026-10-06", "expression": "-1"}]}).status_code == 422
    # delivery plan: the Plan row (typed cells, ERP firm, CBN proposals) per supplier and ISO week (Monday → Sunday)
    dp = client.get(f"/api/articles/{AID}/delivery-plan").json()
    rows = {r["date"]: r for r in dp["rows"]}
    wk = rows["2026-10-05"]
    assert wk["week"] == "2026-W41" and wk["end_date"] == "2026-10-11" and wk["typed"] and wk["typed_qty"] == 750
    assert abs(wk["qty"] - (wk["typed_qty"] + wk["erp_qty"] + wk["cbn_qty"])) < 1e-6
    assert all(r["qty"] > 0 and r["date"] >= "2026-09-14" and dt.date.fromisoformat(r["date"]).weekday() == 0 for r in dp["rows"])
    assert dp["unit"] == "KG"
    proj = client.get(f"/api/articles/{AID}/projection", params={"granularity": "week"}).json()
    assert any(p["qty"] > 0 for p in proj["proposals"])   # proposals exist, so the schedule carries CBN quantities
    assert sum(r["cbn_qty"] for r in dp["rows"]) > 0
    # paging and search of the supply table
    g = client.get("/api/grid", params={"planner": "QUENTIN", "page_size": 5, "page": 2}).json()
    assert g["total"] == 16 and len(g["articles"]) == 5 and g["page"] == 2
    g2 = client.get("/api/grid", params={"planner": "QUENTIN", "q": "resin"}).json()
    assert g2["total"] >= 1 and all("RESIN" in a["article"]["designation"].upper() for a in g2["articles"])


def test_data_version_is_shared_between_worker_processes(seed_source, tmp_path):
    """Two application contexts over the same database (= two uvicorn workers): a write served by one
    must invalidate the computed cache of the other."""
    engine = create_engine(f"sqlite:///{tmp_path / 'appro.db'}", connect_args={"check_same_thread": False})
    factory = init_store(engine)
    assert bootstrap_reference(factory, SEED) > 0
    settings = Settings(data_source="local", seed_dir=SEED, as_of=dt.date.fromisoformat(AS_OF), horizon_days=60,
                        static_dir=SEED / "no-such-dir")
    a = AppContext(settings=settings, source=seed_source, session_factory=factory)
    b = AppContext(settings=settings, source=seed_source, session_factory=factory)
    with a.session() as s:
        before = mrp_service.compute(a, s, article_ids=[AID]).articles[AID].kpis["plan_cell_count"]
    with b.session() as s:
        assert mrp_service.compute(b, s, article_ids=[AID]).articles[AID].kpis["plan_cell_count"] == before
    # worker A writes a plan cell (route code path: service + commit + bump)
    with a.session() as s:
        d = dt.date.fromisoformat(AS_OF) + dt.timedelta(days=3)
        mrp_service.set_plan_cell(s, "quentin@example.com", AID, None, d, "123")
        s.commit()
    a.bump()
    with b.session() as s:
        after = mrp_service.compute(b, s, article_ids=[AID]).articles[AID].kpis["plan_cell_count"]
    assert after == before + 1
    assert b.data_version == a.data_version


def test_weekly_overrides_batch_and_reset(client):
    items = [{"key2": "2026-W40", "field": "coverage_target_days", "value": 12}, {"key2": "2026-W41", "field": "coverage_target_days", "value": 14},
             {"key2": "2026-W41", "field": "safety_stock_qty", "value": 50}]
    r = client.put("/api/params/overrides/batch", json={"scope": "article_week", "key1": AID, "items": items})
    assert r.status_code == 200 and len(r.json()) == 3
    weeks = {w["week"]: w for w in client.get(f"/api/articles/{AID}/weekly-params").json()["weeks"]}
    assert weeks["2026-W41"]["values"]["coverage_target_days"] == 14 and weeks["2026-W41"]["overridden"] == ["coverage_target_days", "safety_stock_qty"]
    # an empty value restores the article value for that week
    r = client.put("/api/params/overrides/batch", json={"scope": "article_week", "key1": AID, "items": [{"key2": "2026-W41", "field": "coverage_target_days", "value": ""}]})
    assert r.status_code == 200 and r.json() == []
    weeks = {w["week"]: w for w in client.get(f"/api/articles/{AID}/weekly-params").json()["weeks"]}
    assert weeks["2026-W41"]["overridden"] == ["safety_stock_qty"] and weeks["2026-W40"]["overridden"] == ["coverage_target_days"]
    assert client.put("/api/params/overrides/batch", json={"scope": "article_week", "key1": AID, "items": [{"key2": "2026-W41", "field": "coverage_target_days", "value": "abc"}]}).status_code == 422
    # reset everything
    assert client.delete("/api/params/overrides", params={"scope": "article_week", "key1": AID}).status_code == 204
    assert all(not w["overridden"] for w in client.get(f"/api/articles/{AID}/weekly-params").json()["weeks"])
    assert client.get("/api/params/overrides", params={"scope": "article_week", "key1": AID}).json() == []


def test_planners_delegations_and_roles(client):
    """Empty planner table = everybody admin ; then each role sees its rights enforced."""
    cfg = client.get("/api/config").json()
    assert cfg["access"]["role"] == "admin" and cfg["access"]["bootstrap"] is True
    arts = client.get("/api/reference/ref_articles/rows").json()
    mine = next(a for a in arts if a["article_id"] == AID)["planner"]
    # the seed has a single planner: hand one article over to a colleague (as the bootstrap admin)
    other = next(a for a in arts if a["article_id"] != AID)
    other["planner"] = "ALICE"
    assert client.put("/api/reference/ref_articles/rows", json={"values": other}).status_code == 200
    root = {"x-forwarded-email": "root@example.com"}
    put = lambda vals, h=root: client.put("/api/reference/ref_planners/rows", json={"values": vals}, headers=h)  # noqa: E731
    # first row = the administrator (declared while everybody is still admin) ; the others by the admin
    assert put({"planner_id": "PROC0", "name": "ROOT", "email": "root@example.com", "role": "admin", "active": True}, {}).status_code == 200
    assert put({"planner_id": "PROC1", "name": mine, "email": "Quentin@Example.com", "role": "appro", "active": True}, {}).status_code == 403  # quentin is now a reader
    assert put({"planner_id": "PROC1", "name": mine, "email": "Quentin@Example.com", "role": "appro", "active": True}).status_code == 200
    assert put({"planner_id": "PROC2", "name": other["planner"], "email": "other@example.com", "role": "appro", "active": True}).status_code == 200
    assert put({"planner_id": "PROC9", "name": "BOSS", "email": "boss@example.com", "role": "manager", "active": True}).status_code == 200
    assert put({"planner_id": "PROCX", "name": "X", "email": "x@example.com", "role": "king", "active": True}).status_code == 422
    # the test client is quentin: an appro (e-mail matched case-insensitively), default perimeter = his portfolio
    cfg = client.get("/api/config").json()
    acc = cfg["access"]
    assert acc["role"] == "appro" and acc["planner_id"] == "PROC1" and acc["portfolio"] == [mine.upper()] and not acc["can_manage_params"]
    assert cfg["default_planner"] == mine
    day = (dt.date.fromisoformat(AS_OF) + dt.timedelta(days=5)).isoformat()
    assert client.put("/api/entries/plan", json={"article_id": AID, "date": day, "expression": "10"}).status_code == 200
    r = client.put("/api/entries/plan", json={"article_id": other["article_id"], "date": day, "expression": "10"})
    assert r.status_code == 403 and "hors de votre carnet" in r.json()["detail"]
    assert client.put("/api/entries/adjustments", json={"article_id": other["article_id"], "date": day, "expression": "-1"}).status_code == 403
    assert client.post("/api/entries/flags/toggle", json={"article_id": other["article_id"], "date": day, "kind": "proposal_refused", "qty": 1}).status_code == 403
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "frozen_days", "value": 2}).status_code == 403
    assert client.put("/api/params/overrides", json={"scope": "article_week", "key1": other["article_id"], "key2": "2026-W40", "field": "coverage_target_days", "value": 2}).status_code == 403
    assert client.put("/api/params/overrides", json={"scope": "article_week", "key1": AID, "key2": "2026-W40", "field": "coverage_target_days", "value": 2}).status_code == 200
    assert put({"planner_id": "PROC3", "name": "Z", "email": "z@example.com", "role": "appro", "active": True}, {}).status_code == 403
    assert client.post("/api/reference/ref_suppliers/import", files={"file": ("f.xlsx", b"x")}).status_code == 403
    # his own article row: yes ; somebody else's: no ; giving his article away: no
    row = next(a for a in arts if a["article_id"] == AID)
    assert client.put("/api/reference/ref_articles/rows", json={"values": {**row, "coverage_target_days": 9}}).status_code == 200
    assert client.put("/api/reference/ref_articles/rows", json={"values": {**row, "planner": other["planner"]}}).status_code == 403
    assert client.put("/api/reference/ref_articles/rows", json={"values": {**other, "coverage_target_days": 9}}).status_code == 403
    # a reader (not declared) and an inactive planner write nothing
    reader = {"x-forwarded-email": "nobody@example.com"}
    assert client.get("/api/config", headers=reader).json()["access"]["role"] == "reader"
    assert client.put("/api/entries/plan", json={"article_id": AID, "date": day, "expression": "10"}, headers=reader).status_code == 403
    assert client.post("/api/reference/refresh", headers=reader).status_code == 403
    # delegation PROC2 → PROC1 (other's portfolio) between two dates covering today
    today = dt.date.today()
    deleg = {"from_planner": "PROC2", "to_planner": "PROC1", "date_from": (today - dt.timedelta(days=1)).isoformat(),
             "date_to": (today + dt.timedelta(days=7)).isoformat(), "note": "congés", "active": True}
    # an appro may only delegate his own portfolio: quentin cannot create a delegation from PROC2
    assert client.put("/api/reference/ref_delegations/rows", json={"values": deleg}).status_code == 403
    other_hdr = {"x-forwarded-email": "other@example.com"}
    assert client.put("/api/reference/ref_delegations/rows", json={"values": deleg}, headers=other_hdr).status_code == 200
    acc = client.get("/api/config").json()["access"]
    assert set(acc["portfolio"]) == {mine.upper(), other["planner"].upper()} and acc["delegated_from"] == ["PROC2"]
    assert client.put("/api/entries/plan", json={"article_id": other["article_id"], "date": day, "expression": "10"}).status_code == 200
    # expired delegation does not count
    assert client.put("/api/reference/ref_delegations/rows", json={"values": {**deleg, "date_to": (today - dt.timedelta(days=1)).isoformat()}}, headers=other_hdr).status_code == 200
    assert client.get("/api/config").json()["access"]["portfolio"] == [mine.upper()]
    # manager: global rules, any article's weeks, PDP, reference tables – but not the planners
    boss = {"x-forwarded-email": "boss@example.com"}
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "frozen_days", "value": 2}, headers=boss).status_code == 200
    assert client.put("/api/params/overrides", json={"scope": "article_week", "key1": other["article_id"], "key2": "2026-W40", "field": "coverage_target_days", "value": 2}, headers=boss).status_code == 200
    assert client.put("/api/reference/ref_planners/rows", json={"values": {"planner_id": "PROC3", "name": "Z", "email": "z@example.com", "role": "appro", "active": True}}, headers=boss).status_code == 403
    assert client.put("/api/entries/plan", json={"article_id": other["article_id"], "date": day, "expression": "10"}, headers=boss).status_code == 403
    assert client.get("/api/config", headers=boss).json()["access"]["can_import_pdp"] is True
    assert client.post("/api/pdp/import", files={"file": ("f.xlsx", b"")}, headers=reader).status_code == 403
    # admin: everything
    assert client.put("/api/reference/ref_planners/rows", json={"values": {"planner_id": "PROC3", "name": "Z", "email": "z@example.com", "role": "appro", "active": True}}, headers=root).status_code == 200
    assert client.put("/api/entries/plan", json={"article_id": other["article_id"], "date": day, "expression": "10"}, headers=root).status_code == 200
    assert client.delete("/api/params/overrides", params={"scope": "global"}, headers=root).status_code == 204
