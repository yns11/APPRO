"""ERP stock snapshots against the projected stock : gap, drift (threshold, minimum packs, persistence,
same sign), coverage from the ERP stock, the STOCK_DRIFT data issue, and « caler sur l'ERP »."""
from __future__ import annotations

from test_api import client  # noqa: F401
from test_engine_units import MON, D, make_dataset

from appro.engine import run_mrp
from appro.engine.models import ConsumptionLine, EngineParams, StockObservation, StockSnapshot


def _run(ds, **params):
    return run_mrp(ds, EngineParams(as_of=params.pop("as_of", MON), horizon_days=30, **params)).articles["A1"]


def test_gap_threshold_and_coverage_from_the_erp_stock():
    """Snapshot of the initialisation day (Sunday 20/9, stock 1 000) : ERP says 850 → gap −150 ; target 1 000
    (five working days of 200 : PDP 500 × 2 per unit) → beyond 10 % (100) and one pack (50) ; one snapshot only :
    no drift unless the persistence is 1.  The coverage from the ERP stock is read because the snapshot is yesterday's."""
    ds = make_dataset(erp_stock=[StockObservation("A1", MON - D(days=1), 800.0, "B06"), StockObservation("A1", MON - D(days=1), 50.0, "B06VRAC")])
    ar = _run(ds)
    k = ar.kpis
    assert k["erp_stock_qty"] == 850 and k["erp_stock_date"] == (MON - D(days=1)).isoformat() and k["erp_stock_gap"] == -150
    assert round(k["erp_stock_gap_pct"], 1) == -15.0 and k["erp_stock_beyond"] and not k["erp_stock_drift"] and k["erp_stock_drift_runs"] == 0
    assert k["erp_stock_warehouses"] == {"B06": 800.0, "B06VRAC": 50.0} and not k["erp_stock_stale"]
    assert k["coverage_erp_stock_days"] == 4 and k["coverage_at_date_days"] == 5      # 850 / 200 per working day vs 1 000
    assert ar.erp_snapshots == [{"date": (MON - D(days=1)).isoformat(), "qty": 850.0, "projected": 1000.0, "gap": -150.0, "gap_pct": -15.0,
                                 "beyond": True, "drift": False, "warehouses": {"B06": 800.0, "B06VRAC": 50.0}}]
    assert "STOCK_DRIFT" not in k["data_issues"]
    # persistence 1 : the single snapshot is a drift, reported as a data issue (never the status)
    ar1 = _run(ds, stock_drift_persist=1)
    assert ar1.kpis["erp_stock_drift"] and ar1.kpis["erp_stock_drift_runs"] == 1 and ar1.kpis["data_issues"] == ["STOCK_DRIFT"]
    assert ar1.kpis["status"] == ar.kpis["status"] and any(a.alert_type.value == "STOCK_DRIFT" and a.scope == "data" and "en moins" in a.message for a in ar1.alerts)
    # under the threshold : 10 % of 1 000 is 100, a gap of 40 is nothing ; a minimum of 4 packs (200) hides 150 too
    assert not _run(make_dataset(erp_stock=[StockObservation("A1", MON - D(days=1), 960.0)]), stock_drift_persist=1).kpis["erp_stock_beyond"]
    assert not _run(ds, stock_drift_persist=1, stock_drift_min_packs=4).kpis["erp_stock_drift"]
    assert _run(ds, stock_drift_persist=1, stock_drift_pct=40, stock_drift_min_packs=0).kpis["erp_stock_beyond"] is False


def test_drift_needs_the_same_sign_on_the_last_snapshots():
    """Thursday 24/9 : snapshots Sunday → Wednesday, consumption 100 a day declared.  A constant −150 on
    four snapshots is a drift (persistence 3) ; a sign that alternates is a declaration lag, not a drift ;
    today's snapshot is ignored (the day is not over) ; an old snapshot is flagged stale."""
    thu = MON + D(days=3)
    cons = [ConsumptionLine("A1", MON + D(days=k), 100.0) for k in range(3)]
    days = [MON - D(days=1), MON, MON + D(days=1), MON + D(days=2)]
    projected = [1000.0, 900.0, 800.0, 700.0]
    ds = make_dataset(consumption=cons, erp_stock=[StockObservation("A1", d, p - 150) for d, p in zip(days, projected)] + [StockObservation("A1", thu, 1.0)])
    ar = _run(ds, as_of=thu)
    assert [s["gap"] for s in ar.erp_snapshots] == [-150.0] * 4 and ar.kpis["erp_stock_drift"] and ar.kpis["erp_stock_drift_runs"] == 4
    assert all(s["drift"] for s in ar.erp_snapshots) and ar.kpis["erp_stock_date"] == (MON + D(days=2)).isoformat()
    alt = make_dataset(consumption=cons, erp_stock=[StockObservation("A1", d, p + (150 if k % 2 else -150)) for k, (d, p) in enumerate(zip(days, projected))])
    ar2 = _run(alt, as_of=thu)
    assert not ar2.kpis["erp_stock_drift"] and ar2.kpis["erp_stock_drift_runs"] == 0 and all(s["beyond"] for s in ar2.erp_snapshots)
    # only the last two beyond : run of 2 < 3 ; persistence 2 makes it a drift
    part = make_dataset(consumption=cons, erp_stock=[StockObservation("A1", d, p - (150 if k >= 2 else 10)) for k, (d, p) in enumerate(zip(days, projected))])
    assert not _run(part, as_of=thu).kpis["erp_stock_drift"] and _run(part, as_of=thu, stock_drift_persist=2).kpis["erp_stock_drift"]
    # the last snapshot is Sunday's : stale (more than two working days), no coverage from the ERP stock
    old = make_dataset(consumption=cons, erp_stock=[StockObservation("A1", MON - D(days=1), 850.0)])
    k = _run(old, as_of=thu).kpis
    assert k["erp_stock_stale"] and k["coverage_erp_stock_days"] is None and k["erp_stock_gap"] == -150


def test_projected_stock_at_zero_compares_on_the_net_balance():
    """Stock 0 with a cumulated shortage of 100 on Monday (stock 100 at the snapshot, 200 consumed) : the ERP
    shows 50 → the gap is 50 − (0 − 100) = +150, not +50."""
    ds = make_dataset(stock=[StockSnapshot("A1", MON - D(days=1), 100.0)], consumption=[ConsumptionLine("A1", MON, 200.0)],
                      erp_stock=[StockObservation("A1", MON, 50.0)])
    ar = _run(ds, as_of=MON + D(days=1))
    s = ar.erp_snapshots[-1]
    assert s["projected"] == -100.0 and s["gap"] == 150.0


def test_api_cockpit_grid_calibrate_and_undo(client):  # noqa: F811
    """Seed (snapshots 18/09 → 24/09, as_of 19/09 : only the 18/09 snapshot counts).  The cockpit exposes the
    source and the counts ; lowering the thresholds turns the −200 of P-00003751 into a drift ; « caler sur
    l'ERP » creates one signed adjustment dated the snapshot, undoable."""
    d = client.get("/api/cockpit").json()
    assert d["erp_stock_enabled"] is True and d["erp_stock_date"] == "2026-09-18" and d["kpis"]["stock_drift"] == 0
    a = next(x for x in d["articles"] if x["article_id"] == "P-00003751")
    assert a["kpis"]["erp_stock_gap"] == -200 and a["kpis"]["erp_stock_date"] == "2026-09-18" and not a["kpis"]["erp_stock_drift"]
    g = client.get("/api/grid", params={"planner": "QUENTIN", "q": "P-00003751"}).json()["articles"][0]
    assert [s["date"] for s in g["erp_snapshots"]] == ["2026-09-18"] and g["erp_snapshots"][0]["gap"] == -200
    for field, value in (("stock_drift_pct", 1), ("stock_drift_min_packs", 0), ("stock_drift_persist", 1)):
        assert client.put("/api/params/overrides", json={"scope": "global", "key1": "", "key2": "", "field": field, "value": value}).status_code == 200
    d = client.get("/api/cockpit").json()
    a = next(x for x in d["articles"] if x["article_id"] == "P-00003751")
    assert d["kpis"]["stock_drift"] >= 1 and d["kpis"]["stock_drift_low"] >= 1 and a["kpis"]["erp_stock_drift"] and "STOCK_DRIFT" in a["kpis"]["data_issues"]
    r = client.post("/api/entries/adjustments/calibrate", json={"article_ids": ["P-00003751", "P-99999999"]})
    assert r.status_code == 200
    out = r.json()
    assert [(c["article_id"], c["date"], c["expression"]) for c in out["created"]] == [("P-00003751", "2026-09-18", "-200")]
    assert out["created"][0]["note"].startswith("Calage sur le stock ERP du 18/09/2026") and out["skipped"] == [{"article_id": "P-99999999", "reason": "article inconnu"}]
    assert "Calage" in r.headers["X-Action-Label"]
    a = next(x for x in client.get("/api/cockpit").json()["articles"] if x["article_id"] == "P-00003751")
    assert a["kpis"]["erp_stock_gap"] == 0 and not a["kpis"]["erp_stock_drift"]
    # a second call adds nothing (already aligned) ; the undo restores the gap
    assert client.post("/api/entries/adjustments/calibrate", json={"article_ids": ["P-00003751"]}).json()["skipped"] == [{"article_id": "P-00003751", "reason": "déjà aligné"}]
    assert client.post("/api/actions/undo-last").status_code == 200
    a = next(x for x in client.get("/api/cockpit").json()["articles"] if x["article_id"] == "P-00003751")
    assert a["kpis"]["erp_stock_gap"] == -200
    pdf = client.get("/api/exports/inventaire.pdf", params={"planner": "QUENTIN"})
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
