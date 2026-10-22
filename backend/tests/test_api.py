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


def _no_cbn(client) -> None:
    """A 100 % tolerance : no « back to target » proposal, the plan stock follows the ERP and the cells."""
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "target_tolerance_pct", "value": 100}).status_code == 200


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
        assert a["kpis"]["status"] in ("critical", "to_order", "ok") and a["kpis"]["coverage_at_date_days"] >= 0
        assert isinstance(a["kpis"]["data_issues"], list)
        if a["kpis"]["order_due_date"]:
            assert a["kpis"]["order_due_supplier_id"] and a["kpis"]["order_due_qty"] > 0
    k = body["kpis"]
    assert k["critical"] + k["to_order"] + k["ok"] == k["articles"] and k["warning"] == k["to_order"]
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
                         "orders_firm_ordered", "orders_firm_open", "desadv_open"}
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
    _no_cbn(client)
    base = client.get(f"/api/articles/{AID}/projection", params={"granularity": "day"}).json()
    sb = _series(base)
    i0 = base["periods"].index(AS_OF)
    r = client.put("/api/entries/adjustments", json={"article_id": AID, "date": "2026-09-23", "expression": "-(30+20)", "note": "casse"})
    assert r.status_code == 200 and r.json()["qty"] == -50 and r.json()["note"] == "casse"
    assert client.put("/api/entries/adjustments", json={"article_id": AID, "date": "2026-09-10", "expression": "-300"}).status_code == 200
    assert client.put("/api/entries/adjustments", json={"article_id": AID, "date": "2026-09-23", "expression": "abc"}).status_code == 422
    assert client.put("/api/entries/adjustments", json={"article_id": "NOPE", "date": "2026-09-23", "expression": "1"}).status_code == 404
    proj = client.get(f"/api/articles/{AID}/projection", params={"granularity": "day"}).json()
    sa = _series(proj)
    j = proj["periods"].index("2026-09-23")
    # dated before the initialisation day (2026-09-18): no column, it corrects the reference stock
    assert sa["adjustments"][j] == -50 and "2026-09-10" not in proj["periods"] and proj["init_date"] == "2026-09-18"
    assert proj["kpis"]["reference_correction"] == -300 and proj["kpis"]["stock_reference"] == pytest.approx(proj["kpis"]["stock_on_hand"] - 300)
    assert sa["stock_erp"][i0] - sb["stock_erp"][i0] == pytest.approx(-300)
    assert sa["stock_plan"][j] - sb["stock_plan"][j] == pytest.approx(-350)
    # note only, then clear
    assert client.put("/api/entries/adjustments", json={"article_id": AID, "date": "2026-09-23", "note": "casse confirmée"}).json()["qty"] == -50
    assert client.put("/api/entries/adjustments", json={"article_id": AID, "date": "2026-09-23", "expression": "0"}).json() is None
    cells = client.get("/api/entries/adjustments", params={"article_id": AID}).json()
    assert len(cells) == 1 and cells[0]["date"] == "2026-09-10"
    assert client.delete(f"/api/entries/adjustments/{cells[0]['id']}").status_code == 204
    log = client.get("/api/entries/audit").json()
    assert log and log[0]["user"] == "quentin@example.com"


def test_plan_cells_typed_erp_zero_and_backlog(client):
    _no_cbn(client)
    proj = client.get(f"/api/articles/{AID}/projection", params={"granularity": "day"}).json()
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
    proj2 = client.get(f"/api/articles/{AID}/projection", params={"granularity": "day"}).json()
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
    # backlog: one row per late firm order (ERP remaining quantity), no action to take
    bl = client.get("/api/backlog", params={"planner": "QUENTIN"}).json()
    assert all(row["backlog"] > 0 and row["ordered"] >= row["received"] and row["expected_date"] < AS_OF and row["order_id"] for row in bl)
    cockpit = client.get("/api/cockpit", params={"planner": "QUENTIN"}).json()
    assert cockpit["kpis"]["backlog_articles"] == len({row["article_id"] for row in bl})


def test_default_calendar_grid_and_program_impact(client):
    proj = client.get(f"/api/articles/{AID}/projection").json()   # granularity "default"
    days = [p for p in proj["periods"] if "-W" not in p]
    weeks = [p for p in proj["periods"] if "-W" in p]
    # the table starts on the stock initialisation day (2026-09-18, the seed) : nothing exists before it
    assert days[0] == "2026-09-18" and days[-1] == "2026-10-04" and len(days) == 17
    assert weeks and all(w > "2026-W40" for w in weeks) and proj["periods"][0] == "2026-09-18"
    proj1 = client.get(f"/api/articles/{AID}/projection", params={"focus_weeks": 1}).json()
    assert len([p for p in proj1["periods"] if "-W" not in p]) == 10   # 2026-09-18 → Sunday 2026-09-27
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
    _no_cbn(client)
    r = client.get(f"/api/articles/{AID}/weekly-params", params={"weeks": 4}).json()
    # at least 4 weeks, and up to the last PDP week / end of horizon (90 days here = 13 weeks)
    assert r["fields"][0] == "coverage_target_days" and len(r["weeks"]) >= 13 and r["weeks"][0]["week"] == "2026-W38"
    wk = r["weeks"][1]["week"]
    assert client.put("/api/params/overrides", json={"scope": "article_week", "key1": AID, "key2": "bad", "field": "coverage_target_days", "value": 20}).status_code == 422
    ok = client.put("/api/params/overrides", json={"scope": "article_week", "key1": AID, "key2": wk, "field": "coverage_target_days", "value": 20})
    assert ok.status_code == 200, ok.text
    r2 = client.get(f"/api/articles/{AID}/weekly-params", params={"weeks": 4}).json()
    assert r2["weeks"][1]["values"]["coverage_target_days"] == 20 and r2["weeks"][1]["overridden"] == ["coverage_target_days"]
    proj = client.get(f"/api/articles/{AID}/projection", params={"granularity": "day"}).json()
    s = _series(proj)
    i = proj["periods"].index(r2["weeks"][1]["week_start"])
    assert s["target_stock"][i] > s["target_stock"][i - 7]
    client.delete(f"/api/params/overrides/{ok.json()['id']}")


def test_params_overrides(client):
    """Two common rules : the tolerance under the target and the lead-time margin ; the fixed rules are listed, not editable."""
    schema = client.get("/api/params/schema").json()
    assert [p["field"] for p in schema] == ["target_tolerance_pct", "lead_margin_days"] and schema[0]["default"] == 15 and schema[1]["default"] == 1
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "lead_margin_days", "value": 0}).status_code == 200
    assert client.get("/api/params/effective").json()["lead_margin_days"] == 0
    fixed = client.get("/api/params/fixed").json()
    assert any("ouvrés" in f["value"] for f in fixed) and any("perdu" in f["value"] for f in fixed)
    r = client.put("/api/params/overrides", json={"scope": "global", "field": "target_tolerance_pct", "value": 20})
    assert r.status_code == 200, r.text
    assert client.get("/api/params/effective").json()["target_tolerance_pct"] == 20
    assert client.get("/api/params/effective").json()["horizon_days"] == 90   # the test settings ; 364 in production
    for field in ("horizon_days", "shortage_policy", "coverage_unit", "backlog_days", "not_a_field"):
        assert client.put("/api/params/overrides", json={"scope": "global", "field": field, "value": 1}).status_code == 422, field
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "target_tolerance_pct", "value": "abc"}).status_code == 422
    assert client.put("/api/params/overrides", json={"scope": "article", "key1": AID, "field": "coverage_target_days", "value": 20}).status_code == 422
    rows = client.get("/api/params/overrides").json()
    assert {r["field"] for r in rows} == {"target_tolerance_pct", "lead_margin_days"}
    assert all(client.delete(f"/api/params/overrides/{r['id']}").status_code == 204 for r in rows)


def test_reference_crud_template_and_import(client):
    tables = client.get("/api/reference/tables").json()
    names = {t["name"]: t for t in tables}
    assert set(names) == {"ref_articles", "ref_suppliers", "ref_article_suppliers", "ref_consumption_terms", "ref_closed_orders", "ref_programs",
                          "ref_bom", "fct_stock", "ref_planners", "ref_delegations"}
    assert names["ref_articles"]["rows"] == 16 and names["ref_suppliers"]["rows"] == 11 and names["ref_programs"]["rows"] == 25
    assert names["ref_articles"]["key"] == ["article_id"] and any(c["label"] == "Couverture cible (j ouvrés)" for c in names["ref_articles"]["columns"])
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
    r = client.put("/api/reference/ref_article_suppliers/rows", json={"values": {"article_id": AID, "supplier_id": "S-000599", "moq": 10, "pack_qty": 5, "lead_time_override": 3, "quota_pct": 0, "priority": 2, "active": "oui"}})
    assert r.status_code == 200 and r.json()["active"] is True
    proj = client.get(f"/api/articles/{AID}/projection").json()
    assert [l["supplier_id"] for l in proj["lanes"]] == ["S-000545", "S-000599"]
    links = {l["supplier_id"]: l for l in proj["suppliers"]}
    assert links["S-000599"]["lead_time_days"] == 3 and links["S-000599"]["lead_time_override"] == 3
    assert links["S-000545"]["lead_time_days"] == 5 and links["S-000545"]["lead_time_override"] is None   # inherited from the supplier
    assert client.post("/api/reference/ref_article_suppliers/delete", json={"key": {"article_id": AID, "supplier_id": "S-000599"}}).status_code == 204
    assert client.post("/api/reference/ref_article_suppliers/delete", json={"key": {"article_id": AID, "supplier_id": "S-000599"}}).status_code == 404
    # template: French headers + example row + notice ; filled export ; import replace / merge
    t = client.get("/api/reference/ref_suppliers/template.xlsx")
    assert t.status_code == 200
    wb = load_workbook(io.BytesIO(t.content))
    assert wb.sheetnames == ["Fournisseurs", "NOTICE"]
    ws = wb["Fournisseurs"]
    assert [c.value for c in ws[1]] == ["Fournisseur", "Nom", "Pays", "Contact", "Jours de livraison", "Jours stricts", "Délai (j ouvrés)", "Actif"]
    ws["A2"], ws["B2"], ws["E2"], ws["F2"], ws["G2"], ws["H2"] = "S-NEW", "New supplier", "1,3", "oui", 7, "oui"
    buf = io.BytesIO()
    wb.save(buf)
    rep = client.post("/api/reference/ref_suppliers/import", files={"file": ("f.xlsx", buf.getvalue())}, data={"mode": "merge"})
    assert rep.status_code == 201, rep.text
    assert rep.json()["created"] == 1
    sups = {r["supplier_id"]: r for r in client.get("/api/reference/ref_suppliers/rows").json()}
    assert "S-NEW" in sups and sups["S-NEW"]["delivery_weekdays"] == "1,3" and sups["S-NEW"]["lead_time_days"] == 7 and len(sups) == 12
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
    base = client.get("/api/articles/P-00043859/projection", params={"granularity": "week"}).json()
    r = client.post("/api/pdp/import", files={"file": ("pdp.xlsx", _legacy_pdp_workbook())}, data={"name": "PDP S40"})
    assert r.status_code == 201, r.text
    rep = r.json()
    assert rep["created"] == 7 and rep["version"]["active"] and rep["version"]["programs"] == 2
    assert any("inconnu" in n for n in rep["notes"])
    after = client.get("/api/articles/P-00043859/projection", params={"granularity": "week"}).json()
    d_base = dict(zip(base["periods"], _series(base)["demand"]))
    d_after = dict(zip(after["periods"], _series(after)["demand"]))
    assert d_after["2026-W40"] == pytest.approx(1000) and d_after["2026-W40"] != d_base["2026-W40"]
    v = rep["version"]["id"]
    assert client.post(f"/api/pdp/versions/{v}/deactivate").json()["active"] is False
    back = client.get("/api/articles/P-00043859/projection", params={"granularity": "week"}).json()
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
    assert list(labels[:5]) == ["Consommation et besoin", "Ordre ferme", "Prévisionnel EDI", "Quantité reçue", "Quantité à retenir"] and "Stock projeté" in labels
    labels2 = [ws.cell(r, 3).value for r in range(4, ws.max_row + 1) if ws.cell(r, 1).value == "P-00005775"]
    assert "Quantité à retenir · S-000032" in labels2 and "Quantité à retenir · S-001033" in labels2         # two supplier lanes
    assert ws.cell(1, 5).value == AS_OF
    rows = {ws.cell(r, 3).value: r for r in range(4, ws.max_row + 1) if ws.cell(r, 1).value == AID}
    assert str(ws.cell(rows["Projeté Ferme"], 5).value).startswith("=MAX(0,")
    assert "SUMIFS" in str(ws.cell(rows["Stock cible"], 5).value) and "COUNTIFS" in str(ws.cell(rows["Couverture (périodes ouvrées)"], 5).value)
    assert wb["ARTICLES"]["A2"].value == AID and wb["ARTICLES"]["I2"].value > 0
    assert client.get("/api/exports/alerts.xlsx").status_code == 200
    assert client.get("/api/exports/plan.xlsx", params={"planner": "QUENTIN"}).status_code == 200

    # planner edits in Excel: delay the 30/09 order (1600) to 06/10, an adjustment on 22/09
    cols = {ws.cell(1, c).value: c for c in range(5, ws.max_column + 1)}
    assert ws.cell(rows["Ordre ferme"], cols["2026-09-30"]).value == 1600 and ws.cell(rows["Quantité à retenir"], cols["2026-09-30"]).value == 1600
    ws.cell(rows["Quantité à retenir"], cols["2026-09-30"], 0)
    ws.cell(rows["Quantité à retenir"], cols["2026-10-06"], 1600)
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
    proj = client.get(f"/api/articles/{AID}/projection", params={"granularity": "day"}).json()
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
    # refuse a proposal: nothing proposed on that day, the need moves to the next delivery day
    proj = client.get(f"/api/articles/{AID}/projection", params={"granularity": "day"}).json()
    if proj["proposals"]:
        p = proj["proposals"][0]
        flag = client.post("/api/entries/flags/toggle", json={"article_id": AID, "date": p["delivery_date"], "kind": "proposal_refused", "qty": p["qty"]}).json()
        assert flag["qty"] == p["qty"]
        proj2 = client.get(f"/api/articles/{AID}/projection", params={"granularity": "day"}).json()
        assert all(q["delivery_date"] != p["delivery_date"] for q in proj2["proposals"])
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
             {"key2": "2026-W41", "field": "overstock_days", "value": 50}]
    r = client.put("/api/params/overrides/batch", json={"scope": "article_week", "key1": AID, "items": items})
    assert r.status_code == 200 and len(r.json()) == 3
    weeks = {w["week"]: w for w in client.get(f"/api/articles/{AID}/weekly-params").json()["weeks"]}
    assert weeks["2026-W41"]["values"]["coverage_target_days"] == 14 and weeks["2026-W41"]["overridden"] == ["coverage_target_days", "overstock_days"]
    # an empty value restores the article value for that week
    r = client.put("/api/params/overrides/batch", json={"scope": "article_week", "key1": AID, "items": [{"key2": "2026-W41", "field": "coverage_target_days", "value": ""}]})
    assert r.status_code == 200 and r.json() == []
    weeks = {w["week"]: w for w in client.get(f"/api/articles/{AID}/weekly-params").json()["weeks"]}
    assert weeks["2026-W41"]["overridden"] == ["overstock_days"] and weeks["2026-W40"]["overridden"] == ["coverage_target_days"]
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
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "target_tolerance_pct", "value": 2}).status_code == 403
    assert client.put("/api/params/overrides", json={"scope": "article_week", "key1": other["article_id"], "key2": "2026-W40", "field": "coverage_target_days", "value": 2}).status_code == 403
    assert client.put("/api/params/overrides", json={"scope": "article_week", "key1": AID, "key2": "2026-W40", "field": "coverage_target_days", "value": 2}).status_code == 200
    assert put({"planner_id": "PROC3", "name": "Z", "email": "z@example.com", "role": "appro", "active": True}, {}).status_code == 403
    # the reference tables are open to every declared user for now (OPEN_REFERENCE): his own article row,
    # somebody else's, an import of a whole table ; a reader still writes nothing
    row = next(a for a in arts if a["article_id"] == AID)
    assert client.get("/api/config").json()["access"]["can_edit_reference"] is True
    assert client.put("/api/reference/ref_articles/rows", json={"values": {**row, "coverage_target_days": 9}}).status_code == 200
    assert client.put("/api/reference/ref_articles/rows", json={"values": {**other, "coverage_target_days": 9}}).status_code == 200
    assert client.post("/api/reference/ref_suppliers/import", files={"file": ("f.xlsx", b"x")}).status_code != 403
    assert client.post("/api/reference/ref_suppliers/import", files={"file": ("f.xlsx", b"x")}, headers={"x-forwarded-email": "nobody@example.com"}).status_code == 403
    # a reader (not declared) and an inactive planner write nothing
    reader = {"x-forwarded-email": "nobody@example.com"}
    assert client.get("/api/config", headers=reader).json()["access"]["role"] == "reader"
    assert client.get("/api/config", headers=reader).json()["access"]["can_edit_reference"] is False
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
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "target_tolerance_pct", "value": 2}, headers=boss).status_code == 200
    assert client.put("/api/params/overrides", json={"scope": "article_week", "key1": other["article_id"], "key2": "2026-W40", "field": "coverage_target_days", "value": 2}, headers=boss).status_code == 200
    assert client.put("/api/reference/ref_planners/rows", json={"values": {"planner_id": "PROC3", "name": "Z", "email": "z@example.com", "role": "appro", "active": True}}, headers=boss).status_code == 403
    assert client.put("/api/entries/plan", json={"article_id": other["article_id"], "date": day, "expression": "10"}, headers=boss).status_code == 403
    assert client.get("/api/config", headers=boss).json()["access"]["can_import_pdp"] is True
    assert client.post("/api/pdp/import", files={"file": ("f.xlsx", b"")}, headers=reader).status_code == 403
    # admin: everything
    assert client.put("/api/reference/ref_planners/rows", json={"values": {"planner_id": "PROC3", "name": "Z", "email": "z@example.com", "role": "appro", "active": True}}, headers=root).status_code == 200
    assert client.put("/api/entries/plan", json={"article_id": other["article_id"], "date": day, "expression": "10"}, headers=root).status_code == 200
    assert client.delete("/api/params/overrides", params={"scope": "global"}, headers=root).status_code == 204


def test_zero_article_parameters_are_kept(client):
    """0 typed in the Référentiel (coverage, thresholds) is a value, not a missing cell ; an empty
    « jours de consommation » means the uniform profile."""
    row = next(r for r in client.get("/api/reference/ref_articles/rows").json() if r["article_id"] == AID)
    r = client.put("/api/reference/ref_articles/rows", json={"values": {**row, "coverage_target_days": 0, "alert_red_days": 0, "demand_days": 0}})
    assert r.status_code == 200, r.text
    weeks = client.get(f"/api/articles/{AID}/weekly-params").json()
    assert weeks["defaults"]["coverage_target_days"] == 0 and weeks["defaults"]["alert_red_days"] == 0
    assert all(w["values"]["coverage_target_days"] == 0 for w in weeks["weeks"])
    art = client.get(f"/api/articles/{AID}/projection").json()["article"]
    assert art["coverage_target_days"] == 0 and art["demand_days"] == 5 and art["consumption_source"] == "erp"


def test_stock_initialisation_date_is_unique_and_never_in_the_future(client):
    """``fct_stock`` is the point zero of the application: one date for every article, not after today."""
    rows = client.get("/api/reference/fct_stock/rows").json()
    row = next(r for r in rows if r["article_id"] == AID)
    r = client.put("/api/reference/fct_stock/rows", json={"values": {**row, "snapshot_date": "2026-09-01"}})
    assert r.status_code == 422 and "une seule date" in r.json()["detail"]
    future = (dt.date.today() + dt.timedelta(days=3)).isoformat()
    assert client.put("/api/reference/fct_stock/rows", json={"values": {**row, "snapshot_date": future}}).status_code == 422
    # same date, new quantity: fine
    assert client.put("/api/reference/fct_stock/rows", json={"values": {**row, "qty_on_hand": 123}}).status_code == 200
    cfg = client.get("/api/config").json()
    assert cfg["init_date"] == "2026-09-18" and cfg["as_of"] == AS_OF


def test_configured_admins_can_never_be_locked_out(client, seed_source, tmp_path):
    """APPRO_ADMINS (the bundle deployer) stays administrator whatever the planners table says."""
    import pandas as pd

    from appro.services.access import resolve_access
    planners = pd.DataFrame([{"planner_id": "PROC1", "name": "QUENTIN", "email": "quentin@example.com", "role": "appro", "active": True}])
    empty = pd.DataFrame(columns=["from_planner", "to_planner", "date_from", "date_to", "active"])
    arts = pd.DataFrame([{"article_id": "A", "planner": "QUENTIN"}])
    assert resolve_access("quentin@example.com", planners, empty, arts).role == "appro"
    assert resolve_access("quentin@example.com", planners, empty, arts, admins={"quentin@example.com"}).role == "admin"
    assert resolve_access("Boss@Example.com", planners, empty, arts).role == "reader"
    assert resolve_access("Boss@Example.com", planners, empty, arts, admins={"boss@example.com"}).role == "admin"
    assert resolve_access("quentin@example.com", planners, empty, arts, admins={"quentin@example.com"}).name == "QUENTIN"


def test_grid_lanes_carry_the_same_details_as_the_article_page(client):
    """The multi-article table shows the Ferme / Reçu tooltips, dots and DESADV cells of the article
    page: the lanes carry the order, receipt and DESADV lines of the displayed window."""
    grid = client.get("/api/grid", params={"planner": "QUENTIN", "page_size": 50}).json()
    art = next(a for a in grid["articles"] if a["article"]["article_id"] == AID)
    lane = next(l for l in art["lanes"] if l["supplier_id"] == "S-000545")
    assert lane["orders"] and all(o["expected_date"] >= grid["period_start"][0] for o in lane["orders"])
    assert {d["packing_slip"] for d in lane["desadv"]} == {"BL-990000", "BL-990001", "BL-990002"}
    assert [(r["packing_slip"], r["processed"]) for r in lane["receipt_lines"]] == [("BL-990000", True)]
    proj = client.get(f"/api/articles/{AID}/projection").json()
    plane = next(l for l in proj["lanes"] if l["supplier_id"] == "S-000545")
    assert plane["receipt_lines"] == lane["receipt_lines"] and plane["desadv"] == lane["desadv"]


def test_perimeter_lists_the_programs_and_suppliers_of_the_planner(client):
    per = client.get("/api/reference/perimeter", params={"planner": "QUENTIN"}).json()
    assert per["articles"] == 16 and "mass-00040633" in {p["id"] for p in per["programs"]}
    assert "S-000545" in {s["id"] for s in per["suppliers"]} and all(s["name"] for s in per["suppliers"])
    assert client.get("/api/reference/perimeter", params={"planner": "NOBODY"}).json() == {"planner": "NOBODY", "articles": 0, "programs": [], "suppliers": []}
    assert client.get("/api/reference/perimeter").json()["programs"] == per["programs"]


def test_pdp_sheet_direct_entry(client):
    """The PDP sheet: programmes × ISO weeks from the first complete week after the point zero (never after
    the current week) ; a manager types the current and future weeks, saved as a new active version holding
    every line of the previous one plus the cells typed."""
    sh = client.get("/api/pdp/sheet", params={"weeks": 8}).json()
    assert sh["current_week"] == "2026-W38" and sh["weeks"][0]["week_start"] == "2026-09-14" and sh["first_week"] == "2026-W38"
    assert sh["active_version"]["name"] == "PDP du jeu de démonstration" and sh["init_date"] == "2026-09-18"
    assert all(w["editable"] and not w["past"] for w in sh["weeks"])      # the current week is editable too
    prog = next(p for p in sh["programs"] if p["program_id"] == "mass-00037796")
    assert prog["source"] == "app" and len(prog["values"]) == len(sh["weeks"])
    before = prog["values"]
    other = next(p for p in sh["programs"] if p["program_id"] != "mass-00037796" and p["source"] == "app")
    n_programs = len({l["program_id"] for l in client.get(f"/api/pdp/versions/{sh['active_version']['id']}/lines").json()})
    r = client.put("/api/pdp/sheet", json={"name": "Saisie test", "cells": [{"program_id": "mass-00037796", "week_start": "2026-09-28", "qty": 123}]})
    assert r.status_code == 201, r.text
    v = r.json()
    assert v["active"] is True and v["programs"] == n_programs and v["source_file"] == "saisie directe"
    sh2 = client.get("/api/pdp/sheet", params={"weeks": 8}).json()
    prog2 = next(p for p in sh2["programs"] if p["program_id"] == "mass-00037796")
    assert sh2["active_version"]["id"] == v["id"] and prog2["source"] == "app"
    assert prog2["values"][2] == 123 and prog2["values"][:2] == before[:2] and prog2["values"][3:] == before[3:]
    # a second entry stacks on the active version (its lines are kept), the name is generated
    r2 = client.put("/api/pdp/sheet", json={"cells": [{"program_id": other["program_id"], "week_start": "2026-10-05", "qty": 0}]})
    assert r2.status_code == 201 and r2.json()["programs"] == n_programs and r2.json()["name"].startswith("Saisie du ")
    sh3 = client.get("/api/pdp/sheet", params={"weeks": 8}).json()
    assert next(p for p in sh3["programs"] if p["program_id"] == "mass-00037796")["values"][2] == 123
    assert next(p for p in sh3["programs"] if p["program_id"] == other["program_id"])["values"][3] == 0
    assert len(client.get("/api/pdp/versions").json()) == 3
    # the engine follows: the demand of the touched week changes with the plan
    assert client.get(f"/api/articles/{AID}/projection").json()["as_of"] == AS_OF
    # the current week is accepted ; a week before the point zero is not ; not a Monday, unknown programme, negative
    bad = [{"program_id": "mass-00037796", "week_start": "2026-09-07", "qty": 1}]
    assert "point zéro" in client.put("/api/pdp/sheet", json={"cells": bad}).json()["detail"]
    assert client.put("/api/pdp/sheet", json={"cells": [{**bad[0], "week_start": "2026-09-14", "qty": 9}]}).status_code == 201
    assert next(p for p in client.get("/api/pdp/sheet", params={"weeks": 8}).json()["programs"] if p["program_id"] == "mass-00037796")["values"][0] == 9
    assert client.put("/api/pdp/sheet", json={"cells": [{**bad[0], "week_start": "2026-09-29"}]}).status_code == 422
    assert client.put("/api/pdp/sheet", json={"cells": [{**bad[0], "program_id": "nope", "week_start": "2026-09-28"}]}).status_code == 422
    assert client.put("/api/pdp/sheet", json={"cells": [{**bad[0], "week_start": "2026-09-28", "qty": -1}]}).status_code == 422
    assert client.put("/api/pdp/sheet", json={"cells": []}).status_code == 422
    # deactivating the active version hands over to the most recent other one (there is no ERP plan)
    vs = client.get("/api/pdp/versions").json()
    active = next(x for x in vs if x["active"])
    client.post(f"/api/pdp/versions/{active['id']}/deactivate")
    vs2 = client.get("/api/pdp/versions").json()
    assert sum(1 for x in vs2 if x["active"]) == 1 and next(x for x in vs2 if x["active"])["id"] != active["id"]


def test_pdp_export_is_reimportable_as_is(client):
    """« Exporter (Excel) » writes the effective PDP in the import format (every programme, every week from
    the first complete week after the point zero) : imported back, it gives the same sheet."""
    sh = client.get("/api/pdp/sheet", params={"weeks": 8}).json()
    r = client.get("/api/pdp/export.xlsx", params={"weeks": 8})
    assert r.status_code == 200 and r.headers["content-disposition"].startswith('attachment; filename="pdp_')
    wb = load_workbook(io.BytesIO(r.content))
    ws = wb["SOP - PDP"]
    assert ws["C1"].value == sh["weeks"][0]["week"] and ws["A1"].value.startswith("PF/SF")
    rows = {ws.cell(r, 2).value: [ws.cell(r, c).value or 0 for c in range(3, 3 + len(sh["weeks"]))] for r in range(2, ws.max_row + 1)}
    for p in sh["programs"]:
        if p["program_id"] in rows:
            assert rows[p["program_id"]] == [v or 0 for v in p["values"]], p["program_id"]
    rep = client.post("/api/pdp/import", files={"file": ("pdp_export.xlsx", r.content)}, data={"name": "réimport"}).json()
    assert rep["version"]["active"] and rep["created"] > 0
    sh2 = client.get("/api/pdp/sheet", params={"weeks": 8}).json()
    assert {p["program_id"]: p["values"] for p in sh2["programs"]} == {p["program_id"]: p["values"] for p in sh["programs"]}


def test_effective_pdp_past_weeks_follow_the_history(client):
    """Past weeks (from the first complete week after the point zero) come from the active version when it
    holds them, else from the version known at their Monday, else from the first version imported later."""
    import datetime as dt

    from appro.data.store import PdpLine, PdpVersion
    from appro.services.context import get_context
    from appro.services.mrp_service import effective_pdp, first_full_monday
    assert first_full_monday(dt.date(2026, 9, 18), dt.date(2026, 10, 19)) == dt.date(2026, 9, 21)
    assert first_full_monday(dt.date(2026, 9, 14), dt.date(2026, 10, 19)) == dt.date(2026, 9, 14)
    assert first_full_monday(dt.date(2026, 9, 18), dt.date(2026, 9, 14)) == dt.date(2026, 9, 14)
    ctx = get_context()
    with ctx.session() as session:
        for v in session.query(PdpVersion).all():
            session.delete(v)
        session.flush()
        old = PdpVersion(name="ancienne", imported_at=dt.datetime(2026, 9, 20, 8), active=False)
        old.lines = [PdpLine(program_id="P", week_start=dt.date(2026, 9, 21), qty=10), PdpLine(program_id="P", week_start=dt.date(2026, 9, 28), qty=11)]
        mid = PdpVersion(name="milieu", imported_at=dt.datetime(2026, 9, 27, 8), active=False)
        mid.lines = [PdpLine(program_id="P", week_start=dt.date(2026, 9, 28), qty=20), PdpLine(program_id="P", week_start=dt.date(2026, 10, 5), qty=21)]
        act = PdpVersion(name="active", imported_at=dt.datetime(2026, 10, 15, 8), active=True)
        act.lines = [PdpLine(program_id="P", week_start=dt.date(2026, 10, 12), qty=30), PdpLine(program_id="P", week_start=dt.date(2026, 10, 19), qty=31)]
        session.add_all([old, mid, act])
        session.commit()
        lines, meta = effective_pdp(session, {"P"}, dt.date(2026, 10, 21), dt.date(2026, 9, 18))
        got = {l.week_start.isoformat(): l.qty for l in lines}
        # S39 : only « ancienne » holds it (imported before its Monday) ; S40 : « milieu » known on the Monday wins over
        # « ancienne » ; S41 : « milieu » (known) ; S42 : the active version ; S43 : current week, active version
        assert got == {"2026-09-21": 10, "2026-09-28": 20, "2026-10-05": 21, "2026-10-12": 30, "2026-10-19": 31}
        origins = {k: (v["origin"], v["version_name"]) for k, v in meta["pdp_week_origin"].items()}
        assert origins == {"2026-09-21": ("monday", "ancienne"), "2026-09-28": ("monday", "milieu"), "2026-10-05": ("monday", "milieu"),
                           "2026-10-12": ("active", "active")}
        assert meta["pdp_first_week"] == "2026-09-21"
        # a week nobody held before its Monday : the first version imported afterwards
        act.lines.append(PdpLine(program_id="P", week_start=dt.date(2026, 11, 2), qty=40))
        session.commit()
        lines2, meta2 = effective_pdp(session, {"P"}, dt.date(2026, 11, 18), dt.date(2026, 9, 18))
        assert {l.week_start.isoformat(): l.qty for l in lines2}["2026-11-02"] == 40
        assert meta2["pdp_week_origin"]["2026-10-26"]["origin"] == "none" and meta2["pdp_week_origin"]["2026-11-02"]["origin"] == "active"
        assert meta2["pdp_week_origin"]["2026-10-19"]["origin"] == "active"          # held by the active version


def test_cockpit_value_median_and_weekly_value(client):
    """Stock value (price × stock at date), target value, median coverage, weekly projected value."""
    body = client.get("/api/cockpit", params={"planner": "QUENTIN"}).json()
    k = body["kpis"]
    assert k["priced_articles"] == 16 and k["unpriced_articles"] == 0
    assert k["stock_value"] > 0 and k["target_value"] > 0 and k["median_coverage_days"] is not None
    arts = body["articles"]
    assert all(a["kpis"]["stock_at_date"] >= 0 and a["kpis"]["price"] > 0 for a in arts)
    assert k["stock_value"] == pytest.approx(sum(a["kpis"]["stock_value"] for a in arts))
    wk = body["weekly_stock_value"]
    assert wk and wk[0]["week"] == "2026-W38" and wk[0]["week_start"] == "2026-09-14" and wk[0]["value_plan"] >= 0 and wk[0]["value_target"] >= 0
    assert len(wk) >= 12


def test_flows_of_the_day(client):
    f = client.get("/api/flows", params={"planner": "QUENTIN"}).json()
    k = f["kpis"]
    assert set(k) == {"to_order", "ordered", "in_transit", "to_process", "to_receive", "received", "late", "to_validate"}
    assert f["first_seen_available"] is False and k["ordered"] == 0        # the seed carries no first_seen stamp
    # à valider : validated receipts not booked for a week or more (RC-900003 : 9 days, RC-900004 : 14 days) ;
    # RC-900001 (yesterday) is not there yet
    assert k["to_validate"] == 2 and {r["packing_slip"]: r["days_pending"] for r in f["to_validate"]} == {"BL-980001": 9, "BL-980002": 14}
    transit = {d["packing_slip"]: d for d in f["in_transit"]}
    assert {"BL-990001", "BL-990002", "BL-990006", "BL-990005"} <= set(transit) and "BL-990000" not in transit
    # BL-990005 is carried by an arrival journal not validated yet : listed once, through that journal
    assert transit["BL-990005"]["state"] == "Journal d'arrivée non validé" and transit["BL-990005"]["desadv_id"] == "RC-900002"
    assert transit["BL-990001"]["state"] == "Traité"
    issues = {d["packing_slip"]: d["issue"] for d in f["to_process"]}
    assert issues["BL-990002"] == "message en erreur" and issues["BL-990005"] == "traité sans journal de saisie"
    # à commander : every proposal whose date limite falls within the next five working days, the urgent ones first
    due_limit = "2026-09-25"   # Saturday 19/09 + 5 working days
    assert f["to_order"] and all(p["urgent"] or p["order_date"] <= due_limit for p in f["to_order"])
    flags = [p["urgent"] for p in f["to_order"]]
    assert flags == sorted(flags, reverse=True) and k["to_order"] == sum(1 for p in f["to_order"] if not p["ignored"])
    assert all(not p["ignored"] for p in f["to_order"]) and all("off_standard" in p for p in f["to_order"])
    # en transit : the last five working days only ; à traiter and en retard are complete, newest first, with the
    # default window of the screen
    assert f["recent_since"] == "2026-09-14"
    assert all(d["issue_date"] >= f["recent_since"] for d in f["in_transit"])
    dates = [d["issue_date"] for d in f["to_process"]]
    assert dates == sorted(dates, reverse=True) and k["to_process"] == sum(1 for d in dates if d >= f["recent_since"])
    late = f["late"]
    assert len(late) == len(client.get("/api/backlog", params={"planner": "QUENTIN"}).json())
    exp = [b["expected_date"] for b in late]
    assert exp == sorted(exp, reverse=True) and k["late"] == sum(1 for d in exp if d >= f["recent_since"])
    # a refused proposal stays listed, flagged « ignorée » (hidden by default on screen), and leaves the calculations
    p = f["to_order"][0]
    assert client.post("/api/entries/flags/toggle", json={"article_id": p["article_id"], "supplier_id": p["supplier_id"], "date": p["delivery_date"], "kind": "proposal_refused", "qty": p["qty"]}).status_code == 200
    f2 = client.get("/api/flows", params={"planner": "QUENTIN"}).json()
    ign = [x for x in f2["to_order"] if x["ignored"] and x["article_id"] == p["article_id"]]
    assert ign and ign[0]["order_date"] == p["order_date"] and f2["kpis"]["to_order"] == sum(1 for x in f2["to_order"] if not x["ignored"])


def test_search_page_queries(client):
    r = client.get("/api/search", params={"kind": "receipts", "q": "BL-990000;BL-990005", "planner": "QUENTIN"}).json()
    assert r["total"] == 2 and {x["packing_slip"] for x in r["rows"]} == {"BL-990000", "BL-990005"} and r["truncated"] is False
    assert {x["status"] for x in r["rows"]} == {"Reçu", "En transit"}
    r = client.get("/api/search", params={"kind": "receipts", "date_from": "2026-09-01", "date_to": "2026-09-30"}).json()
    assert r["total"] >= 2 and all("2026-09-01" <= x["date"] <= "2026-09-30" for x in r["rows"])
    assert client.get("/api/search", params={"kind": "pending"}).status_code == 422
    d = client.get("/api/search", params={"kind": "desadv", "q": "ennovi"}).json()
    assert d["total"] == 2 and all(x["journal"] is False for x in d["rows"])
    o = client.get("/api/search", params={"kind": "orders", "supplier_id": "S-000545", "limit": 3}).json()
    assert o["truncated"] is True and len(o["rows"]) == 3
    assert client.get("/api/search", params={"kind": "nope"}).status_code == 422
    # « flux actif » = an active article ↔ supplier link exists for the line ; « flux inactif » otherwise
    links = {(lk["article_id"], lk["supplier_id"]) for lk in client.get("/api/reference/ref_article_suppliers/rows").json() if lk["active"]}
    every = client.get("/api/search", params={"kind": "orders", "planner": "QUENTIN"}).json()
    assert all(x["linked"] == ((x["article_id"], x["supplier_id"]) in links) for x in every["rows"])
    on = client.get("/api/search", params={"kind": "orders", "planner": "QUENTIN", "link": "active"}).json()
    off = client.get("/api/search", params={"kind": "orders", "planner": "QUENTIN", "link": "inactive"}).json()
    assert on["total"] + off["total"] == every["total"] and all(x["linked"] for x in on["rows"]) and not any(x["linked"] for x in off["rows"])
    assert client.get("/api/search", params={"kind": "orders", "link": "nope"}).status_code == 422
    # « ; » also in the supply table free text : 123;456 = one or the other
    g = client.get("/api/grid", params={"planner": "QUENTIN", "q": "P-00001046 ; P-00003751"}).json()
    assert {a["article"]["article_id"] for a in g["articles"]} == {"P-00001046", "P-00003751"}


def test_existing_tables_receive_the_new_columns(tmp_path):
    """A table created by an older version (no ``status`` on erp_receipts) gets the column at start-up."""
    from sqlalchemy import create_engine, inspect, text
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE erp_receipts (receipt_id VARCHAR(200) PRIMARY KEY, article_id VARCHAR(200), supplier_id VARCHAR(200), "
                          "receipt_date DATE, qty FLOAT, purch_id VARCHAR(200), packing_slip VARCHAR(200))"))
        conn.execute(text("INSERT INTO erp_receipts (receipt_id, article_id, qty) VALUES ('R1', 'A1', 5)"))
    init_store(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("erp_receipts")}
    assert "status" in cols
    with engine.connect() as conn:
        assert conn.execute(text("SELECT receipt_id, status FROM erp_receipts")).all() == [("R1", None)]


def test_first_seen_is_carried_over_between_two_copies():
    from appro.data.erp_sql import carry_first_seen
    today = dt.date(2026, 10, 5)
    previous = {"A": dt.date(2026, 9, 1), "B": None}
    assert carry_first_seen(previous, "A", today) == dt.date(2026, 9, 1)
    assert carry_first_seen(previous, "B", today) == today and carry_first_seen(previous, "NEW", today) == today


def test_next_expected_delivery_and_new_programme_in_the_sheet(client):
    """Fiches articles: the next expected delivery (day, quantity, nature) ; a programme added in the
    Référentiel is listed in the PDP sheet at once, without any bill of material."""
    arts = client.get("/api/cockpit", params={"planner": "QUENTIN"}).json()["articles"]
    a = next(x for x in arts if x["article_id"] == AID)
    k = a["kpis"]
    assert k["next_delivery_date"] and k["next_delivery_date"] >= AS_OF and k["next_delivery_qty"] > 0
    assert k["next_delivery_type"] in ("ferme", "cbn", "saisie")
    day = (dt.date.fromisoformat(AS_OF) + dt.timedelta(days=1)).isoformat()
    assert client.put("/api/entries/plan", json={"article_id": AID, "date": day, "expression": "77"}).status_code == 200
    k2 = next(x for x in client.get("/api/cockpit", params={"planner": "QUENTIN"}).json()["articles"] if x["article_id"] == AID)["kpis"]
    assert k2["next_delivery_date"] == day and k2["next_delivery_qty"] == 77 and k2["next_delivery_type"] == "saisie"
    r = client.put("/api/reference/ref_programs/rows", json={"values": {"program_id": "mass-NEW", "name": "Nouveau programme", "family": "", "active": True}})
    assert r.status_code == 200, r.text
    sh = client.get("/api/pdp/sheet", params={"weeks": 6}).json()
    new = next(p for p in sh["programs"] if p["program_id"] == "mass-NEW")
    assert new["has_bom"] is False and new["source"] == "none" and all(v == 0 for v in new["values"])


def test_grid_sorted_by_supplier_name(client):
    g = client.get("/api/grid", params={"planner": "QUENTIN", "sort": "supplier", "page_size": 50}).json()
    names = [min((l["name"] or l["supplier_id"] or "").lower() for l in a["lanes"] if l["supplier_id"]) for a in g["articles"]]
    assert names == sorted(names) and names[0] < names[-1]
    assert client.get("/api/grid", params={"planner": "QUENTIN", "sort": "nope"}).status_code == 422


def test_planner_filter_on_reference_tables_and_pdp_sheet_and_row_order(client):
    """The global planner filter reaches the Référentiel (articles, links, BOM, stock, suppliers,
    programmes), the PDP sheet (programmes reaching the planner's articles) ; the sheet keeps the
    row order stored by the managers."""
    arts = client.get("/api/reference/ref_articles/rows").json()
    other = next(a for a in arts if a["article_id"] != AID)
    assert client.put("/api/reference/ref_articles/rows", json={"values": {**other, "planner": "ALICE"}}).status_code == 200
    mine = client.get("/api/reference/ref_articles/rows", params={"planner": "quentin"}).json()
    assert len(mine) == 15 and all(a["planner"] == "QUENTIN" for a in mine)
    alice = client.get("/api/reference/ref_articles/rows", params={"planner": "ALICE"}).json()
    assert [a["article_id"] for a in alice] == [other["article_id"]]
    for table in ("ref_article_suppliers", "ref_bom", "fct_stock"):
        rows = client.get(f"/api/reference/{table}/rows", params={"planner": "ALICE"}).json()
        assert rows and all(r["article_id"] == other["article_id"] for r in rows), table
    sups = client.get("/api/reference/ref_suppliers/rows", params={"planner": "ALICE"}).json()
    links = client.get("/api/reference/ref_article_suppliers/rows", params={"planner": "ALICE"}).json()
    assert {s["supplier_id"] for s in sups} == {l["supplier_id"] for l in links}
    progs = client.get("/api/reference/ref_programs/rows", params={"planner": "ALICE"}).json()
    bom = client.get("/api/reference/ref_bom/rows", params={"planner": "ALICE"}).json()
    assert {p["program_id"] for p in progs} == {b["program_id"] for b in bom}
    assert len(client.get("/api/reference/ref_planners/rows", params={"planner": "ALICE"}).json()) == len(client.get("/api/reference/ref_planners/rows").json())
    sheet = client.get("/api/pdp/sheet", params={"weeks": 6, "planner": "ALICE"}).json()
    assert {p["program_id"] for p in sheet["programs"]} == {b["program_id"] for b in bom}
    full = [p["program_id"] for p in client.get("/api/pdp/sheet", params={"weeks": 6}).json()["programs"]]
    assert len(full) > len(sheet["programs"])
    # row order : the last two first, the rest alphabetical as before
    wanted = full[-2:]
    assert client.put("/api/pdp/order", json={"program_ids": wanted + [wanted[0]]}).json() == wanted
    after = [p["program_id"] for p in client.get("/api/pdp/sheet", params={"weeks": 6}).json()["programs"]]
    assert after[:2] == wanted and after[2:] == full[:-2]


def test_reference_query_columns(client):
    """Articles, suppliers and programmes carry columns computed from the other tables."""
    arts = {r["article_id"]: r for r in client.get("/api/reference/ref_articles/rows").json()}
    bom = client.get("/api/reference/ref_bom/rows").json()
    links = [r for r in client.get("/api/reference/ref_article_suppliers/rows").json() if r["active"]]
    names = {p["program_id"]: p["name"] for p in client.get("/api/reference/ref_programs/rows").json()}
    for aid, r in arts.items():
        want = sorted({names[b["program_id"]] for b in bom if b["article_id"] == aid}, key=str.lower)
        assert r["programs"] == ", ".join(want) and r["program_count"] == len(want)
        assert r["supplier_count"] == sum(1 for lk in links if lk["article_id"] == aid)
    assert any(r["supplier_count"] >= 2 for r in arts.values())   # the seed holds a multi-sourced article
    sups = client.get("/api/reference/ref_suppliers/rows").json()
    for s in sups:
        want = sorted({lk["article_id"] for lk in links if lk["supplier_id"] == s["supplier_id"]})
        assert s["articles"] == ", ".join(want) and s["article_count"] == len(want)
    progs = {p["program_id"]: p for p in client.get("/api/reference/ref_programs/rows").json()}
    assert all(p["article_count"] == sum(1 for b in bom if b["program_id"] == pid) for pid, p in progs.items())
    assert {p["has_plan"] for p in progs.values()} <= {True, False} and any(p["has_plan"] for p in progs.values())
    # a new programme : no components, no plan
    assert client.put("/api/reference/ref_programs/rows", json={"values": {"program_id": "mass-NEW", "name": "Nouveau", "active": True}}).status_code == 200
    new = next(p for p in client.get("/api/reference/ref_programs/rows").json() if p["program_id"] == "mass-NEW")
    assert new["article_count"] == 0 and new["has_plan"] is False
    # the query columns are not stored : saving a row carrying them is fine
    row = arts[AID]
    assert client.put("/api/reference/ref_articles/rows", json={"values": row}).status_code == 200


def test_grid_week_columns_keep_firm_orders_and_filter_several_suppliers(client):
    """Week columns aggregate the firm display series (ordered / open) like the day columns ; the
    supplier filter accepts several suppliers (any of them)."""
    day = client.get("/api/grid", params={"granularity": "day", "page_size": 200}).json()
    week = client.get("/api/grid", params={"granularity": "week", "page_size": 200}).json()
    assert week["periods"][1].startswith("20") and "-W" in week["periods"][1]
    for ad, aw in zip(day["articles"], week["articles"]):
        for ld, lw in zip(ad["lanes"], aw["lanes"]):
            sd = {x["key"]: sum(x["values"]) for x in ld["series"]}
            sw = {x["key"]: sum(x["values"]) for x in lw["series"]}
            for key in ("orders_firm_ordered", "orders_firm_open", "orders_firm", "receipts"):
                assert abs(sd[key] - sw[key]) < 1e-6, (ad["article"]["article_id"], key)
    assert any(sum(x["values"]) > 0 for a in week["articles"] for l in a["lanes"] for x in l["series"] if x["key"] == "orders_firm_ordered")
    links = client.get("/api/reference/ref_article_suppliers/rows").json()
    sids = sorted({lk["supplier_id"] for lk in links})[:2]
    one = {a["article"]["article_id"] for a in client.get("/api/grid", params={"supplier_id": sids[0], "page_size": 200}).json()["articles"]}
    two = {a["article"]["article_id"] for a in client.get("/api/grid", params=[("supplier_id", sids[0]), ("supplier_id", sids[1]), ("page_size", 200)]).json()["articles"]}
    want = {lk["article_id"] for lk in links if lk["supplier_id"] in sids}
    assert one <= two == want and len(two) > len(one)


def test_programme_scrap_rate_in_the_pdp_sheet(client):
    """Column « Taux rebut (%) » of the PDP sheet : 0 by default ; a rate typed by a manager applies from
    the current week on (dated row), the need of the components follows, the past is untouched."""
    sh = client.get("/api/pdp/sheet", params={"weeks": 8}).json()
    assert all(p["scrap_pct"] == 0 for p in sh["programs"])
    pid = "mass-00037796"
    bom = client.get("/api/reference/ref_bom/rows").json()
    aid = next(b["article_id"] for b in bom if b["program_id"] == pid)
    before = client.get(f"/api/articles/{aid}/projection", params={"granularity": "day"}).json()
    req = lambda body: {k: v for k, v in zip(body["periods"], next(s for s in body["series"] if s["key"] == "required")["values"])}  # noqa: E731
    r = client.put("/api/pdp/scrap", json={"program_id": pid, "rate_pct": 10})
    assert r.status_code == 200, r.text
    sh2 = client.get("/api/pdp/sheet", params={"weeks": 8}).json()
    assert next(p for p in sh2["programs"] if p["program_id"] == pid)["scrap_pct"] == 10
    after = client.get(f"/api/articles/{aid}/projection", params={"granularity": "day"}).json()
    b, a = req(before), req(after)
    future = [d for d in a if d >= "2026-09-14" and b[d] > 0]
    assert future and all(a[d] == pytest.approx(b[d] * 1.1, rel=1e-6) for d in future)
    assert all(a[d] == b[d] for d in a if d < "2026-09-14")
    # a second rate replaces the current week's row (same Monday) ; validation
    assert client.put("/api/pdp/scrap", json={"program_id": pid, "rate_pct": 0}).status_code == 200
    assert next(p for p in client.get("/api/pdp/sheet", params={"weeks": 8}).json()["programs"] if p["program_id"] == pid)["scrap_pct"] == 0
    assert client.put("/api/pdp/scrap", json={"program_id": "nope", "rate_pct": 1}).status_code == 422
    assert client.put("/api/pdp/scrap", json={"program_id": pid, "rate_pct": -1}).status_code == 422


# ---------------------------------------------------------------- Paramètres › Données, supplier page, exports
def test_data_sync_orphans_calendar(client):
    sync = client.get("/api/data/sync").json()
    assert sync["status"] == "unknown" and len(sync["tables"]) == 5 and sync["late_after_minutes"] == 120
    assert {t["table"] for t in sync["tables"]} >= {"erp_purchase_orders", "erp_receipts", "erp_consumption_actual"}
    orphans = client.get("/api/data/orphans").json()
    assert isinstance(orphans, list) and all(set(o) >= {"article_id", "orders", "receipts", "consumption", "last_date"} for o in orphans)
    cal = client.get("/api/data/calendar").json()
    assert cal["working_weekdays"] == [1, 2, 3, 4, 5] and "holidays" in cal


def test_adherence_net_gross_and_demand_days_suggestion(client):
    r = client.get("/api/data/adherence", params={"weeks": 4, "planner": "QUENTIN"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["weeks"] == 4 and body["bucket"] == "week" and body["plan_mode"] == "monday" and len(body["buckets"]) == 4
    assert body["totals"]["articles"] == len(body["rows"]) > 0
    for row in body["rows"]:
        assert row["consumption_source"] != "plan"
        assert row["suggested_demand_days"] in (None, 1, 2, 3, 5)
        if row["plan"] > 0:
            assert row["net_pct"] is not None and row["gross_pct"] is not None and row["gross_pct"] <= row["net_pct"] + 1e-6
        assert len(row["weekday_share"]) == 5
    daily = client.get("/api/data/adherence", params={"weeks": 2, "bucket": "day", "plan_mode": "latest", "article_id": AID}).json()
    assert len(daily["buckets"]) == 14 and [r["article_id"] for r in daily["rows"]] == [AID]
    assert [v["origin"] for v in daily["plan_versions"]] == ["latest", "latest"]


def test_adherence_plan_of_each_week_falls_back_on_a_later_version_and_takes_week_bounds(client):
    """« PDP du lundi » : with no version imported before the Monday of a week, the first version imported
    afterwards that holds the week is the plan of that week (flagged ``later``) instead of an empty plan ;
    the window is given by Mondays (``week_from`` / ``week_to``), always before the current week."""
    before = client.get("/api/data/adherence", params={"weeks": 4, "article_id": AID}).json()
    assert before["from"] == "2026-08-17" and before["to"] == "2026-09-13" and len(before["plan_versions"]) == 4
    assert {v["origin"] for v in before["plan_versions"]} <= {"later", "none"}     # the seed plan is a version loaded at start-up
    # a version imported today (after every Monday of the window) holding four past weeks
    wb = load_workbook(io.BytesIO(client.get("/api/pdp/template.xlsx", params={"weeks": 4}).content))
    ws = wb["SOP - PDP"]
    for k, col in enumerate("CDEF"):
        ws[f"{col}1"] = f"2026-W{34 + k}"
        for row in range(2, ws.max_row + 1):
            ws[f"{col}{row}"] = 1000
    buf = io.BytesIO()
    wb.save(buf)
    rep = client.post("/api/pdp/import", files={"file": ("past.xlsx", buf.getvalue())}, data={"name": "PDP passé", "activate": "false"}).json()
    assert rep["created"] >= 4
    after = client.get("/api/data/adherence", params={"weeks": 4, "article_id": AID}).json()
    assert [v["origin"] for v in after["plan_versions"]] == ["later"] * 4
    # the demonstration version (loaded at start-up, imported first) already held those weeks : it stays the
    # first version imported after their Mondays ; the new one would only serve weeks it does not cover
    assert all(v["version_name"] == "PDP du jeu de démonstration" for v in after["plan_versions"])
    assert after["plan_coverage"] == {"monday": 0, "later": 4, "latest": 0, "none": 0}
    assert after["rows"][0]["plan"] > 0 and after["rows"][0]["plan"] == before["rows"][0]["plan"]
    far = client.get("/api/data/adherence", params={"week_from": "2026-06-01", "week_to": "2026-06-22", "article_id": AID}).json()
    assert {v["origin"] for v in far["plan_versions"]} <= {"none", "later"}
    # week bounds : two weeks, the current week is never part of the window
    two = client.get("/api/data/adherence", params={"week_from": "2026-08-24", "week_to": "2026-09-07", "article_id": AID}).json()
    assert two["from"] == "2026-08-24" and two["to"] == "2026-09-13" and two["weeks"] == 3 and len(two["buckets"]) == 3
    cur = client.get("/api/data/adherence", params={"week_from": "2026-09-07", "week_to": "2026-09-16", "article_id": AID}).json()
    assert cur["buckets"] == ["2026-W37"]
    assert client.get("/api/data/adherence", params={"week_from": "2026-09-07", "week_to": "2026-08-01"}).status_code == 422


def test_supplier_page(client):
    links = client.get("/api/reference/links", params={"article_id": AID}).json()
    sid = links[0]["supplier_id"]
    r = client.get(f"/api/suppliers/{sid}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["supplier"]["supplier_id"] == sid and body["supplier"]["lead_time_days"] == 5
    assert any(a["article_id"] == AID for a in body["articles"])
    row = next(a for a in body["articles"] if a["article_id"] == AID)
    assert row["lead_time_days"] == 5 and row["lead_time_override"] is None and row["supplier_count"] >= 1
    assert body["kpis"]["articles"] == len(body["articles"])
    assert all(o["supplier_id"] == sid for o in body["late"] + body["in_transit"] + body["upcoming"])
    assert client.get("/api/suppliers/NOPE").status_code == 404


def test_generic_table_export(client):
    body = {"title": "Articles en retard", "sheet": "Retards",
            "columns": [{"key": "a", "label": "Article"}, {"key": "q", "label": "Quantité", "type": "number"},
                        {"key": "d", "label": "Date", "type": "date"}, {"key": "ok", "label": "Actif", "type": "bool"}],
            "rows": [{"a": "P-1", "q": 1440, "d": "2026-09-19", "ok": True}, {"a": "P-2", "q": "12.5", "d": None, "ok": False}],
            "footer": {"a": "Total", "q": 1452.5}}
    r = client.post("/api/exports/table.xlsx", json=body)
    assert r.status_code == 200 and "articles_en_retard_" in r.headers["content-disposition"]
    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(r.content))
    ws = wb["Retards"]
    assert [c.value for c in ws[1]] == ["Article", "Quantité", "Date", "Actif"]
    assert ws["B2"].value == 1440 and ws["C2"].value.date() == dt.date(2026, 9, 19) and ws["D2"].value == "oui"
    assert ws["B3"].value == 12.5 and ws["D3"].value == "non" and ws["A4"].value == "Total" and ws["B4"].value == 1452.5


def test_link_caps_and_lead_override_are_validated(client):
    links = client.get("/api/reference/links", params={"article_id": AID}).json()
    base = {k: links[0][k] for k in ("article_id", "supplier_id", "moq", "pack_qty", "quota_pct", "priority", "active")}
    pack = base["pack_qty"]
    put = lambda **kw: client.put("/api/reference/ref_article_suppliers/rows", json={"values": {**base, **kw}})  # noqa: E731
    r = put(max_qty_per_delivery=pack * 2 + 1)
    assert r.status_code == 422 and "multiple de l'UM" in r.json()["detail"]
    r = put(max_qty_per_delivery=pack, moq=pack * 3)
    assert r.status_code == 422 and "≥ MOQ" in r.json()["detail"]
    r = put(max_qty_per_delivery=pack * 2, max_qty_per_week=pack * 20)
    assert r.status_code == 422 and "jours de livraison" in r.json()["detail"]
    r = put(lead_time_override=0)
    assert r.status_code == 422 and "délai spécifique" in r.json()["detail"]
    r = put(max_qty_per_delivery=pack * 2, max_qty_per_week=pack * 4, lead_time_override=7)
    assert r.status_code == 200, r.text
    link = next(l for l in client.get("/api/reference/links", params={"article_id": AID}).json() if l["supplier_id"] == base["supplier_id"])
    assert link["lead_time_days"] == 7 and link["lead_time_override"] == 7 and link["max_qty_per_week"] == pack * 4
    # the stored rows keep « empty » for the optional columns (never 0) : the screens show « hérité » / « – »
    rows = client.get("/api/reference/ref_article_suppliers/rows").json()
    untouched = [r for r in rows if r["supplier_id"] != base["supplier_id"] or r["article_id"] != AID]
    assert untouched and all(r["lead_time_override"] is None and r["max_qty_per_delivery"] is None and r["max_qty_per_week"] is None for r in untouched)
    mine = next(r for r in rows if r["supplier_id"] == base["supplier_id"] and r["article_id"] == AID)
    assert mine["lead_time_override"] == 7 and mine["max_qty_per_delivery"] == pack * 2
    # clearing the override (empty string) goes back to the supplier's lead time
    assert put(lead_time_override="", max_qty_per_delivery="", max_qty_per_week="").status_code == 200
    link = next(l for l in client.get("/api/reference/links", params={"article_id": AID}).json() if l["supplier_id"] == base["supplier_id"])
    assert link["lead_time_override"] is None and link["lead_time_days"] == 5 and link["max_qty_per_week"] is None


def test_consumption_terms_are_validated(client):
    sources = client.get("/api/reference/consumption-sources", params={"article_id": AID}).json()
    assert all(s["article_id"] != AID and s["shared_programs"] for s in sources)
    put = lambda src, coef=1: client.put("/api/reference/ref_consumption_terms/rows",  # noqa: E731
                                        json={"values": {"article_id": AID, "source_article_id": src, "coef": coef}})
    assert put(AID).status_code == 422
    r = put("P-NOPE")
    assert r.status_code == 422 and "consommation ERP" in r.json()["detail"]
    if sources:
        src = sources[0]["article_id"]
        assert put(src, 0).status_code == 422
        assert put(src, 2).status_code == 200
        # the reverse term would loop (and the source is not eligible from the other side once derived)
        rev = client.put("/api/reference/ref_consumption_terms/rows", json={"values": {"article_id": src, "source_article_id": AID, "coef": 1}})
        assert rev.status_code == 422


def test_closed_orders_declared_receipts_count_in_the_stock(client):
    """Only the receipts of an open order (CO-…) or of a closed order (PO, six digits) declared in the
    Référentiel enter the Reçu row and the stock ; the others stay in the search and the flows."""
    art, day = "P-00005775", "2026-09-19"
    rows = client.get("/api/reference/ref_closed_orders/rows").json()
    assert [r["purch_id"] for r in rows] == ["024596"] and rows[0]["receipt_count"] == 1 and rows[0]["receipt_qty"] == 1000
    assert rows[0]["last_receipt"] == day and rows[0]["receipt_articles"] == art and rows[0]["receipt_suppliers"] == "S-000032"
    # the search shows every receipt, with its kind and whether it counts
    rec = client.get("/api/search", params={"kind": "receipts", "article_id": art, "date_from": day}).json()["rows"]
    kinds = {r["purch_id"]: (r["order_kind"], r["counted"]) for r in rec}
    assert kinds["024596"] == ("po", True) and kinds["024597"] == ("po", False)
    assert all(v[1] for k, v in kinds.items() if k.startswith("CO-"))
    po = client.get("/api/search", params={"kind": "receipts", "article_id": art, "order": "po"}).json()["rows"]
    other = client.get("/api/search", params={"kind": "receipts", "article_id": art, "order": "other"}).json()["rows"]
    assert {r["purch_id"] for r in po} == {"024596"} and {r["purch_id"] for r in other} == {"024597"}
    # the undeclared PO does not reach the Reçu row ; declaring it adds its 500
    def receipts_on(d: str) -> float:
        proj = client.get(f"/api/articles/{art}/projection", params={"granularity": "day"}).json()
        i = proj["periods"].index(d)
        return sum(next(x["values"][i] for x in lane["series"] if x["key"] == "receipts") for lane in proj["lanes"])
    before = receipts_on(day)
    lanes = client.get(f"/api/articles/{art}/projection", params={"granularity": "day"}).json()["lanes"]
    lines = {x["purch_id"]: x for lane in lanes for x in lane["receipt_lines"] if x["receipt_date"] == day}
    assert "024596" in lines and lines["024596"]["order_kind"] == "po" and "024597" not in lines
    assert client.put("/api/reference/ref_closed_orders/rows", json={"values": {"purch_id": "024597", "active": True}}).status_code == 200
    assert receipts_on(day) == before + 500
    # validation : six digits, never an open order
    bad = client.put("/api/reference/ref_closed_orders/rows", json={"values": {"purch_id": "CO-000001", "active": True}})
    assert bad.status_code == 422 and "commande ouverte" in bad.json()["detail"]
    assert client.put("/api/reference/ref_closed_orders/rows", json={"values": {"purch_id": "24596", "active": True}}).status_code == 422
    # deactivating the declaration takes the receipt out again
    assert client.put("/api/reference/ref_closed_orders/rows", json={"values": {"purch_id": "024597", "active": False}}).status_code == 200
    assert receipts_on(day) == before


# ---------------------------------------------------------------- annuler / rétablir
def _acts(client, **params):
    return client.get("/api/actions", params=params).json()


def test_undo_redo_plan_cell_and_shortcuts(client):
    """A typed cell is one action : the response names it, Ctrl+Z puts the cell back, Ctrl+Y re-applies it."""
    _no_cbn(client)
    day = "2026-09-23"
    r = client.put("/api/entries/plan", json={"article_id": AID, "supplier_id": "S-000545", "date": day, "expression": "600"})
    assert r.status_code == 200 and r.headers["X-Action-Id"].startswith("AC-")
    assert "retenir%20P-00001046" in r.headers["X-Action-Label"]
    aid = r.headers["X-Action-Id"]
    acts = _acts(client, mine=True)
    assert acts[0]["id"] == aid and acts[0]["state"] == "applied" and acts[0]["items"] == 1 and acts[0]["conflict"] is None
    assert acts[0]["mine"] and acts[0]["can_act"] and acts[0]["article_id"] == AID
    # a second write of the same cell is another action (no group)
    r2 = client.put("/api/entries/plan", json={"article_id": AID, "supplier_id": "S-000545", "date": day, "expression": "650"})
    assert r2.headers["X-Action-Id"] != aid
    # the older action of the chain is not « modifié depuis » : undoing « jusqu'ici » restores the cell first
    assert [(a["id"], a["conflict"]) for a in _acts(client, mine=True)[:2]] == [(r2.headers["X-Action-Id"], None), (aid, None)]
    # Ctrl+Z : back to 600 ; Ctrl+Z again : the cell disappears
    out = client.post("/api/actions/undo-last").json()
    assert out["kind"] == "undo" and [a["id"] for a in out["actions"]] == [r2.headers["X-Action-Id"]]
    cells = client.get("/api/entries/plan", params={"article_id": AID}).json()
    assert [c["qty"] for c in cells if c["date"] == day] == [600.0]
    assert client.post("/api/actions/undo-last").status_code == 200
    assert [c for c in client.get("/api/entries/plan", params={"article_id": AID}).json() if c["date"] == day] == []
    assert client.post("/api/actions/undo-last").json()["actions"][0]["kind"] == "param"     # the tolerance rule of _no_cbn
    assert client.post("/api/actions/undo-last").status_code == 409      # nothing left
    # Ctrl+Y : the rule, then 600, then 650 ; a fourth one has nothing to redo
    assert client.post("/api/actions/redo-last").json()["actions"][0]["kind"] == "param"
    assert [a["id"] for a in client.post("/api/actions/redo-last").json()["actions"]] == [aid]
    assert [c["qty"] for c in client.get("/api/entries/plan", params={"article_id": AID}).json() if c["date"] == day] == [600.0]
    client.post("/api/actions/redo-last")
    assert [c["qty"] for c in client.get("/api/entries/plan", params={"article_id": AID}).json() if c["date"] == day] == [650.0]
    assert client.post("/api/actions/redo-last").status_code == 409
    # the journal keeps both, applied again ; the audit log holds the undo / redo lines
    states = {a["id"]: a["state"] for a in _acts(client, mine=True)}
    assert states[aid] == "applied" and states[r2.headers["X-Action-Id"]] == "applied"
    assert {e["action"] for e in client.get("/api/entries/audit").json()} >= {"undo", "redo"}
    # a write without change records nothing
    n = len(_acts(client))
    r3 = client.put("/api/entries/plan", json={"article_id": AID, "supplier_id": "S-000545", "date": day, "expression": "650"})
    assert "X-Action-Id" not in r3.headers and len(_acts(client)) == n


def test_undo_until_here_strict_order_and_new_action_clears_redo(client):
    """Undoing an older action undoes the newer ones first (« annuler jusqu'ici ») ; a new action discards
    the undone ones."""
    _no_cbn(client)
    days = ["2026-09-23", "2026-09-24", "2026-09-25"]
    ids = []
    for i, day in enumerate(days):
        r = client.put("/api/entries/adjustments", json={"article_id": AID, "date": day, "expression": str(-10 * (i + 1))})
        ids.append(r.headers["X-Action-Id"])
    out = client.post(f"/api/actions/{ids[0]}/undo").json()
    assert [a["id"] for a in out["actions"]] == [ids[2], ids[1], ids[0]]
    assert client.get("/api/entries/adjustments", params={"article_id": AID}).json() == []
    # redo of the middle one redoes the first one too
    out = client.post(f"/api/actions/{ids[1]}/redo").json()
    assert [a["id"] for a in out["actions"]] == [ids[0], ids[1]]
    assert sorted(c["qty"] for c in client.get("/api/entries/adjustments", params={"article_id": AID}).json()) == [-20.0, -10.0]
    # a new action : the third one can no longer be redone
    client.put("/api/entries/adjustments", json={"article_id": AID, "date": "2026-09-26", "expression": "-5"})
    states = {a["id"]: a["state"] for a in _acts(client)}
    assert states[ids[2]] == "discarded"
    r = client.post(f"/api/actions/{ids[2]}/redo")
    assert r.status_code == 409 and "ne peut plus être rétablie" in r.json()["detail"]
    assert client.post("/api/actions/redo-last").status_code == 409


def test_undo_batch_is_one_action_and_detects_conflicts(client):
    """A fill of the grid is one action ; a cell changed since by someone else blocks the undo without
    writing anything (« modifié depuis par X »)."""
    _no_cbn(client)
    cells = [{"article_id": AID, "supplier_id": "S-000545", "date": d, "expression": "100"} for d in ("2026-09-23", "2026-09-24", "2026-09-25")]
    r = client.put("/api/entries/plan/batch", json={"cells": cells})
    aid = r.headers["X-Action-Id"]
    act = next(a for a in _acts(client) if a["id"] == aid)
    assert act["items"] == 3 and "3 cellules" in act["label"]
    other = {"x-forwarded-email": "alice@example.com"}
    client.put("/api/entries/plan", json={"article_id": AID, "supplier_id": "S-000545", "date": "2026-09-24", "expression": "999"}, headers=other)
    act = next(a for a in _acts(client) if a["id"] == aid)
    assert act["conflict"] == "modifié depuis par alice@example.com"
    r = client.post(f"/api/actions/{aid}/undo")
    assert r.status_code == 409 and "alice@example.com" in r.json()["detail"]
    qty = {c["date"]: c["qty"] for c in client.get("/api/entries/plan", params={"article_id": AID}).json()}
    assert qty["2026-09-23"] == 100.0 and qty["2026-09-24"] == 999.0      # nothing was written
    # alice undoes her own cell : the fill is consistent again and can be undone
    assert client.post("/api/actions/undo-last", headers=other).status_code == 200
    assert client.post(f"/api/actions/{aid}/undo").status_code == 200
    assert client.get("/api/entries/plan", params={"article_id": AID}).json() == []


def test_undo_group_header_merges_a_bulk_edit(client):
    """The reference screen edits rows one request at a time : the ``X-Action-Group`` header makes them
    one action, undone as a whole."""
    rows = client.get("/api/reference/ref_suppliers/rows").json()
    two = rows[:2]
    hdr = {"X-Action-Group": "bulk-1"}
    for r in two:
        assert client.put("/api/reference/ref_suppliers/rows", json={"values": {**r, "country": "ZZ"}}, headers=hdr).status_code == 200
    acts = _acts(client)
    assert acts[0]["items"] == 2 and acts[0]["label"] == "Fournisseurs : 2 lignes" and acts[0]["group_id"] == "bulk-1"
    assert client.post(f"/api/actions/{acts[0]['id']}/undo").status_code == 200
    after = {r["supplier_id"]: r["country"] for r in client.get("/api/reference/ref_suppliers/rows").json()}
    assert [after[r["supplier_id"]] for r in two] == [r["country"] for r in two]
    # a deleted row comes back on undo, with every column
    victim = {k: v for k, v in rows[-1].items() if k not in ("updated_by", "updated_at")}
    assert client.post("/api/reference/ref_suppliers/delete", json={"key": {"supplier_id": victim["supplier_id"]}}).status_code == 204
    assert victim["supplier_id"] not in {r["supplier_id"] for r in client.get("/api/reference/ref_suppliers/rows").json()}
    assert client.post("/api/actions/undo-last").status_code == 200
    back = next(r for r in client.get("/api/reference/ref_suppliers/rows").json() if r["supplier_id"] == victim["supplier_id"])
    assert {k: back[k] for k in victim} == victim


def test_undo_params_scrap_order_and_pdp_versions(client):
    """Weekly parameters, scrap rates, the programme order and the active PDP version are undoable ; a
    version deleted is journalised but not undoable."""
    # weekly parameter (batch) then reset
    body = {"scope": "article_week", "key1": AID, "items": [{"key2": "2026-W40", "field": "coverage_target_days", "value": 12}]}
    assert client.put("/api/params/overrides/batch", json=body).status_code == 200
    assert client.delete("/api/params/overrides", params={"scope": "article_week", "key1": AID}).status_code == 204
    assert client.get("/api/params/overrides", params={"scope": "article_week", "key1": AID}).json() == []
    assert client.post("/api/actions/undo-last").status_code == 200      # the reset
    assert [o["value"] for o in client.get("/api/params/overrides", params={"scope": "article_week", "key1": AID}).json()] == ["12"]
    assert client.post("/api/actions/undo-last").status_code == 200      # the batch
    assert client.get("/api/params/overrides", params={"scope": "article_week", "key1": AID}).json() == []
    # scrap rate
    prog = client.get("/api/pdp/sheet").json()["programs"][0]["program_id"]
    assert client.put("/api/pdp/scrap", json={"program_id": prog, "rate_pct": 7.5}).status_code == 200
    assert client.post("/api/actions/undo-last").status_code == 200
    assert next(p for p in client.get("/api/pdp/sheet").json()["programs"] if p["program_id"] == prog)["scrap_pct"] == 0
    # programme order
    order = [p["program_id"] for p in client.get("/api/pdp/sheet").json()["programs"]]
    assert client.put("/api/pdp/order", json={"program_ids": order[::-1]}).status_code == 200
    assert [p["program_id"] for p in client.get("/api/pdp/sheet").json()["programs"]] == order[::-1]
    assert client.post("/api/actions/undo-last").status_code == 200
    assert [p["program_id"] for p in client.get("/api/pdp/sheet").json()["programs"]] == order
    # a PDP sheet save : undo = the previous version is active again, the new one stays (inactive)
    before = next(v for v in client.get("/api/pdp/versions").json() if v["active"])
    r = client.put("/api/pdp/sheet", json={"name": "Essai", "cells": [{"program_id": prog, "week_start": "2026-09-28", "qty": 42}]})
    assert r.status_code == 201 and "Saisie%20PDP" in r.headers["X-Action-Label"]
    new = r.json()["id"]
    assert client.post("/api/actions/undo-last").status_code == 200
    versions = {v["id"]: v["active"] for v in client.get("/api/pdp/versions").json()}
    assert versions[before["id"]] is True and versions[new] is False
    assert client.post("/api/actions/redo-last").status_code == 200
    assert {v["id"]: v["active"] for v in client.get("/api/pdp/versions").json()}[new] is True
    # the version deleted : journalised, not undoable ; its undo now conflicts (the version is gone)
    r = client.delete(f"/api/pdp/versions/{new}")
    assert r.status_code == 204
    acts = _acts(client)
    assert acts[0]["undoable"] is False and acts[0]["can_act"] is False and "Suppression" in acts[0]["label"]
    assert client.post(f"/api/actions/{acts[0]['id']}/undo").status_code == 409
    sheet_action = next(a for a in acts if a["kind"] == "pdp_sheet")
    assert sheet_action["conflict"] is not None
    r = client.post(f"/api/actions/{sheet_action['id']}/undo")
    assert r.status_code == 409


def test_undo_ownership_and_retention(client, monkeypatch):
    """A planner undoes their own actions only ; an administrator anyone's ; actions older than 7 days
    are purged."""
    import appro.services.actions as act
    from appro.data.store import AppAction, utcnow
    from appro.services.context import get_context
    _no_cbn(client)
    # declare the planners : root admin, quentin appro (bootstrap over)
    assert client.put("/api/reference/ref_planners/rows", json={"values": {"planner_id": "PROC0", "name": "ROOT", "email": "root@example.com", "role": "admin", "active": True}}).status_code == 200
    root = {"x-forwarded-email": "root@example.com"}
    assert client.put("/api/reference/ref_planners/rows", json={"values": {"planner_id": "PROC1", "name": "QUENTIN", "email": "quentin@example.com", "role": "appro", "active": True}}, headers=root).status_code == 200
    r = client.put("/api/entries/adjustments", json={"article_id": AID, "date": "2026-09-23", "expression": "-3"})
    aid = r.headers["X-Action-Id"]
    alice = {"x-forwarded-email": "alice@example.com"}
    acts = client.get("/api/actions", headers=alice).json()
    mine = next(a for a in acts if a["id"] == aid)
    assert mine["mine"] is False and mine["can_act"] is False
    assert client.post(f"/api/actions/{aid}/undo", headers=alice).status_code == 403
    assert client.post(f"/api/actions/{aid}/undo", headers=root).status_code == 200       # admin
    assert client.get("/api/entries/adjustments", params={"article_id": AID}).json() == []
    # retention : an action dated 8 days ago vanishes from the list and cannot be undone
    with get_context().session() as s:
        a = s.get(AppAction, aid)
        a.ts = utcnow() - dt.timedelta(days=act.RETENTION_DAYS + 1)
        s.commit()
    assert aid not in {a["id"] for a in client.get("/api/actions").json()}
    assert client.post(f"/api/actions/{aid}/redo").status_code == 404


def test_a_planner_declares_closed_orders(client):
    """Declaring a PO is the planner's job, not only the managers' : an ``appro`` adds, edits and
    deletes rows of ref_closed_orders (a reader cannot)."""
    assert client.put("/api/reference/ref_planners/rows", json={"values": {"planner_id": "PROC0", "name": "ROOT", "email": "root@example.com", "role": "admin", "active": True}}).status_code == 200
    root = {"x-forwarded-email": "root@example.com"}
    assert client.put("/api/reference/ref_planners/rows", json={"values": {"planner_id": "PROC1", "name": "QUENTIN", "email": "quentin@example.com", "role": "appro", "active": True}}, headers=root).status_code == 200
    cfg = client.get("/api/config").json()["access"]
    assert cfg["role"] == "appro" and cfg["can_edit_reference"] is True
    row = {"purch_id": "031234", "supplier_id": "S-000545", "article_id": AID, "note": "déclarée par l'appro", "active": True}
    assert client.put("/api/reference/ref_closed_orders/rows", json={"values": row}).status_code == 200
    assert client.put("/api/reference/ref_closed_orders/rows", json={"values": {**row, "active": False}}).status_code == 200
    assert client.post("/api/reference/ref_closed_orders/delete", json={"key": {"purch_id": "031234"}}).status_code == 204
    reader = {"x-forwarded-email": "nobody@example.com"}     # not declared = reader
    assert client.put("/api/reference/ref_closed_orders/rows", json={"values": row}, headers=reader).status_code == 403


def test_help_pages_default_edited_and_reset(client):
    """The in-app pense-bête : pages shipped with the application, read by everyone ; an administrator saves
    another text (sanitised), everybody then reads that version ; the reset brings the shipped text back."""
    pages = client.get("/api/help/pages").json()
    slugs = [p["slug"] for p in pages]
    assert slugs[:2] == ["accueil", "cbn"] and "flux" in slugs and "memo" in slugs and all(not p["edited"] for p in pages)
    cbn = client.get("/api/help/pages/cbn").json()
    assert cbn["title"].endswith("Proposition CBN") and "<svg" in cbn["html"] and cbn["part"] == "A" and not cbn["edited"]
    assert client.get("/api/help/pages/nope").status_code == 404
    # the bootstrap admin (planners table empty) edits a page ; scripts and handlers are stripped
    body = {"html": "<p onclick=\"x()\">Texte <b>modifié</b></p><script>alert(1)</script><a href=\"javascript:x\">l</a>", "title": "1. CBN (maison)"}
    r = client.put("/api/help/pages/cbn", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["edited"] and out["title"] == "1. CBN (maison)" and "<script" not in out["html"] and "onclick" not in out["html"] and "javascript:" not in out["html"]
    assert "Texte <b>modifié</b>" in out["html"] and out["updated_by"] == "quentin@example.com"
    assert client.get("/api/help/pages/cbn").json()["html"] == out["html"]
    assert next(p for p in client.get("/api/help/pages").json() if p["slug"] == "cbn")["edited"] is True
    # journalised, not undoable
    acts = client.get("/api/actions", params={"mine": True}).json()
    assert acts and acts[0]["kind"] == "help" and acts[0]["undoable"] is False
    # a declared non-admin user reads the saved version but cannot write ; a reader reads it too
    assert client.put("/api/reference/ref_planners/rows", json={"values": {"planner_id": "PROC0", "name": "ROOT", "email": "root@example.com", "role": "admin", "active": True}}).status_code == 200
    assert client.put("/api/reference/ref_planners/rows", json={"values": {"planner_id": "PROC1", "name": "QUENTIN", "email": "quentin@example.com", "role": "appro", "active": True}}, headers={"x-forwarded-email": "root@example.com"}).status_code == 200
    assert client.get("/api/help/pages/cbn").json()["html"] == out["html"]
    assert client.put("/api/help/pages/cbn", json={"html": "<p>non</p>"}).status_code == 403
    assert client.delete("/api/help/pages/cbn").status_code == 403
    assert client.get("/api/help/pages/cbn", headers={"x-forwarded-email": "nobody@example.com"}).json()["html"] == out["html"]
    # the administrator brings the shipped text back
    back = client.delete("/api/help/pages/cbn", headers={"x-forwarded-email": "root@example.com"})
    assert back.status_code == 200 and not back.json()["edited"] and back.json()["html"] == cbn["html"]


def test_inventory_pdf(client):
    """The printable inventory sheet follows the filters of the supply table and is a real PDF."""
    r = client.get("/api/exports/inventaire.pdf", params={"planner": "QUENTIN"})
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content.startswith(b"%PDF")
    assert "inventaire_" in r.headers["content-disposition"]
    full = len(r.content)
    r2 = client.get("/api/exports/inventaire.pdf", params={"planner": "QUENTIN", "q": "P-00001046", "sort": "supplier"})
    assert r2.status_code == 200 and r2.content.startswith(b"%PDF") and len(r2.content) < full
    assert client.get("/api/exports/inventaire.pdf", params={"supplier_id": ["S-000545"]}).status_code == 200
