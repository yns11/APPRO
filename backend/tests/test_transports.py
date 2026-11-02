"""Planning de livraisons : firm orders per supplier and day, transports (EM counter, CSV for the transport
tool with header and one line per dock, e-mail to the transport contacts), global CSV, cancellation, supplier
default docks, grid marks, and the supplier portal (role, gate, overview)."""
from __future__ import annotations

from test_api import client  # noqa: F401

HEADER = "ID TRANSPORT;FOURNISSEUR;TRANSPORTEUR;DATE;HEURE;CODE FOURNISSEUR;FLUX;CHAMP1;CHAMP2;QUAIS"


def _set_contact(c, sid: str, contact: str, docks: str | None = None):
    rows = c.get("/api/reference/ref_suppliers/rows").json()
    r = next(x for x in rows if x["supplier_id"] == sid)
    keep = {k: v for k, v in r.items() if k in ("supplier_id", "name", "country", "contact", "delivery_weekdays", "strict_days", "lead_time_days", "active", "docks")}
    if docks is not None:
        keep["docks"] = docks
    res = c.put("/api/reference/ref_suppliers/rows", json={"values": {**keep, "transport_contact": contact}})
    assert res.status_code in (200, 201), res.text
    return r


def test_transports_planning_create_pack_cancel(client):  # noqa: F811
    # the supplier's transport contacts and default docks are columns of the reference table (docks = B15 by default)
    before = _set_contact(client, "S-000378", "transport@fournisseur.example; quai@fournisseur.example", "B15;B6 EST")
    assert before["docks"] == "B15"

    plan = client.get("/api/transports/planning", params={"start": "2026-09-28", "weeks": 2}).json()
    assert plan["start"] == "2026-09-28" and plan["end"] == "2026-10-09" and len(plan["days"]) == 10
    assert plan["docks"] == ["B15", "B6 EST", "B2 NORD", "B6 NORD"]
    assert all(s["orders"] for s in plan["suppliers"])      # only the suppliers with an order in the window
    row = next(s for s in plan["suppliers"] if s["supplier_id"] == "S-000378")
    assert row["emails"] == ["transport@fournisseur.example", "quai@fournisseur.example"] and row["docks"] == ["B15", "B6 EST"]
    orders = row["orders"]
    day = orders[0]["expected_date"]
    same_day = [o for o in orders if o["expected_date"] == day]

    # two trucks for the orders of that day, the second one on two docks
    body = {"supplier_id": "S-000378", "order_ids": [o["order_id"] for o in same_day], "carrier": "Geodis", "note": "Deux camions",
            "slots": [{"date": day, "time": "08:30", "docks": ["B15"]}, {"date": day, "time": "14:00", "docks": ["B2 NORD", "B6 NORD"]}]}
    r = client.post("/api/transports", json=body)
    assert r.status_code == 201, r.text
    pack = r.json()
    ids = [t["transport_id"] for t in pack["transports"]]
    assert ids == ["EM000001", "EM000002"] and pack["transports"][1]["docks"] == ["B2 NORD", "B6 NORD"] and pack["transports"][1]["carrier"] == "Geodis"
    lines = pack["csv"].split("\r\n")
    d, m, y = day[8:10], day[5:7], day[0:4]
    assert lines[0] == HEADER and lines[-1] == "" and len(lines) == 5          # header + 1 + 2 docks
    assert lines[1] == f"EM000001;{row['name']};Geodis;{d}/{m}/{y};08:30:00;S-000378;R;;;B15"
    assert lines[2] == f"EM000002;{row['name']};Geodis;{d}/{m}/{y};14:00:00;S-000378;R;;;B2 NORD"
    assert lines[3] == f"EM000002;{row['name']};Geodis;{d}/{m}/{y};14:00:00;S-000378;R;;;B6 NORD"
    assert pack["csv_filename"] == "ATR_EM000001-EM000002.csv"
    mail = pack["mail"]
    assert mail["to"] == ["transport@fournisseur.example", "quai@fournisseur.example"]
    assert "EM000001, EM000002" in mail["subject"] and f"{d}/{m}/{y}" in mail["subject"] and "quai B15 / B2 NORD / B6 NORD" in mail["subject"]
    assert "EM000002 – " in mail["body"] and "14:00" in mail["body"] and "Deux camions" in mail["body"] and "transporteur Geodis" in mail["body"]

    # the planning shows the transports on the orders ; the grid marks the firm order slot ; the counter goes on
    plan = client.get("/api/transports/planning", params={"start": "2026-09-28", "weeks": 2}).json()
    row = next(s for s in plan["suppliers"] if s["supplier_id"] == "S-000378")
    assert next(o for o in row["orders"] if o["order_id"] == same_day[0]["order_id"])["transports"] == ["EM000001", "EM000002"]
    aid = same_day[0]["article_id"]
    lane = next(l for l in client.get(f"/api/articles/{aid}/projection").json()["lanes"] if l["supplier_id"] == "S-000378")
    assert next(o for o in lane["orders"] if o["order_id"] == same_day[0]["order_id"])["transports"] == ["EM000001", "EM000002"]
    r = client.post("/api/transports", json={**body, "slots": [{"date": day, "time": "16:00", "docks": ["B6 EST"]}]})
    assert r.json()["transports"][0]["transport_id"] == "EM000003"

    # pack again, per-transport CSV, global CSV (every planned transport from today on), list, cancel, journal
    again = client.get("/api/transports/EM000002/pack").json()
    assert again["csv"].split("\r\n")[1].startswith("EM000002;") and again["csv_filename"] == "ATR_EM000002.csv"
    one = client.get("/api/transports/EM000002/atr.csv")
    assert one.status_code == 200 and one.headers["content-disposition"].endswith('filename="ATR_EM000002.csv"') and one.text.lstrip("﻿").startswith(HEADER)
    assert client.delete("/api/transports/EM000002").json()["status"] == "cancelled"
    glob = client.get("/api/transports/atr.csv")
    assert glob.status_code == 200 and glob.headers["content-disposition"].endswith('filename="ATR_transports_20260919.csv"')
    glines = glob.text.lstrip("﻿").split("\r\n")
    assert glines[0] == HEADER and [l.split(";")[0] for l in glines[1:] if l] == ["EM000001", "EM000003"]
    lst = client.get("/api/transports", params={"supplier_id": "S-000378"}).json()
    assert [t["transport_id"] for t in lst] == ["EM000001", "EM000003"]
    assert [t["transport_id"] for t in client.get("/api/transports", params={"supplier_id": "S-000378", "include_cancelled": True}).json()] == ["EM000001", "EM000002", "EM000003"]
    plan = client.get("/api/transports/planning", params={"start": "2026-09-28", "weeks": 2}).json()
    assert [t["transport_id"] for t in plan["transports"]] == ["EM000001", "EM000003"]
    r = client.post("/api/transports", json={**body, "slots": [{"date": day, "time": "08:30", "docks": ["B99"]}]})
    assert r.status_code == 422
    labels = [a["label"] for a in client.get("/api/actions").json()]
    assert any("EM000001, EM000002" in x for x in labels) and any("EM000002 annulé" in x for x in labels)


def test_supplier_portal_role_and_gate(client):  # noqa: F811
    _set_contact(client, "S-000378", "Quai@Fournisseur.example")
    day = client.get("/api/transports/planning", params={"start": "2026-09-28", "weeks": 2}).json()
    row = next(s for s in day["suppliers"] if s["supplier_id"] == "S-000378")
    o = row["orders"][0]
    assert client.post("/api/transports", json={"supplier_id": "S-000378", "order_ids": [o["order_id"]], "slots": [{"date": o["expected_date"], "time": "10:00", "docks": ["B15"]}]}).status_code == 201

    h = {"x-forwarded-email": "quai@fournisseur.example"}
    cfg = client.get("/api/config", headers=h).json()
    assert cfg["access"]["role"] == "supplier" and cfg["access"]["supplier_ids"] == ["S-000378"] and cfg["access"]["can_write"] is False
    # the gate : nothing but the portal, the config and the help
    assert client.get("/api/cockpit", headers=h).status_code == 403
    assert client.get("/api/transports/planning", headers=h).status_code == 403
    assert client.get("/api/reference/ref_suppliers/rows", headers=h).status_code == 403
    assert client.get("/api/help/pages", headers=h).status_code == 200
    p = client.get("/api/portal/overview", headers=h).json()
    assert [s["supplier_id"] for s in p["suppliers"]] == ["S-000378"]
    assert p["orders"] and all(r["supplier_id"] == "S-000378" and r["order_type"] == "FIRM" and r["qty_open"] > 0 for r in p["orders"])
    assert all(r["status"] == "en retard" and r["date"] < p["as_of"] for r in p["backlog"])
    assert all(r["order_type"] != "FIRM" and r["date"] >= p["as_of"] for r in p["forecasts"])
    assert [t["transport_id"] for t in p["transports"]] == ["EM000001"]
    assert next(r for r in p["orders"] if r["order_id"] == o["order_id"])["transports"] == ["EM000001"]
    assert all(r["supplier_id"] == "S-000378" for r in p["desadv"] + p["receipts"])
    k = p["kpis"]
    assert k["open_orders"] == len(p["orders"]) and k["late_orders"] == len(p["backlog"]) and k["transports"] == 1 and k["next_transport"].startswith("EM000001")
    # a planner is never a supplier, even when listed as a contact ; a reader without a contact is not a supplier
    assert client.get("/api/config").json()["access"]["role"] in ("admin", "appro", "manager")
    assert client.get("/api/portal/overview", headers={"x-forwarded-email": "nobody@example.com"}).status_code == 403
    # a manager / admin previews a supplier's portal
    assert client.get("/api/portal/overview", params={"supplier_id": "S-000378"}).status_code == 200


def test_legacy_dock_column_is_retired(tmp_path):
    """A database created by the first version holds ``app_transports.dock NOT NULL`` : the start-up migration
    copies it into ``docks`` and drops it, so the new version's INSERT no longer fails (NotNullViolation)."""
    import datetime as dt

    from sqlalchemy import create_engine, inspect, text

    from appro.data.store import AppTransport, init_store

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE app_transports (transport_id VARCHAR(20) PRIMARY KEY, supplier_id VARCHAR(40) NOT NULL, date DATE NOT NULL, "
                          "time VARCHAR(8) NOT NULL, dock VARCHAR(20) NOT NULL, note TEXT NOT NULL, status VARCHAR(12) NOT NULL, "
                          "created_by VARCHAR(120) NOT NULL, created_at DATETIME NOT NULL)"))
        conn.execute(text("INSERT INTO app_transports VALUES ('EM000001', 'S-1', '2026-10-30', '08:00', 'B15', '', 'planned', 'x', '2026-10-29 06:00:00')"))
    factory = init_store(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("app_transports")}
    assert "dock" not in cols and {"docks", "carrier"} <= cols
    with factory() as session:
        assert session.get(AppTransport, "EM000001").docks == "B15"
        session.add(AppTransport(transport_id="EM000002", supplier_id="S-1", date=dt.date(2026, 10, 30), time="09:00", docks="B15;B6 EST", created_by="y"))
        session.commit()
        assert session.get(AppTransport, "EM000002").docks == "B15;B6 EST"


def _set_mode(c, sid: str, mode: str, contact: str | None = None):
    rows = c.get("/api/reference/ref_suppliers/rows").json()
    r = next(x for x in rows if x["supplier_id"] == sid)
    keep = {k: v for k, v in r.items() if k not in ("updated_by", "updated_at", "articles", "article_count")}
    if contact is not None:
        keep["transport_contact"] = contact
    return c.put("/api/reference/ref_suppliers/rows", json={"values": {**keep, "transport_mode": mode}})


def test_transport_modes_carrier_maf_parcel_and_batches(client):  # noqa: F811
    """Carriers table ; supplier transport mode (Fournisseur by default, a carrier, MAF Hambach, Messagerie) ;
    the main planning against the MAF planning ; shuttle transports across suppliers ; CSV and e-mail per mode ;
    batch cancel, CSV and mail of a selection."""
    # ---- carriers : seeded, validated
    carriers = client.get("/api/reference/ref_carriers/rows").json()
    assert {c["name"] for c in carriers} >= {"Geodis", "DB Schenker"}
    assert client.put("/api/reference/ref_carriers/rows", json={"values": {"name": "Dachser", "contacts": "pas-un-mail", "active": True}}).status_code == 422
    assert client.put("/api/reference/ref_carriers/rows", json={"values": {"name": "Dachser", "contacts": "Booking@dachser.example; Quai@dachser.example", "active": True}}).status_code in (200, 201)
    # ---- every supplier delivers himself unless told otherwise ; an unknown mode is refused
    sups = client.get("/api/reference/ref_suppliers/rows").json()
    assert all(s["transport_mode"] == "Fournisseur" for s in sups)
    assert _set_mode(client, "S-000378", "Inconnu").status_code == 422
    assert _set_mode(client, "S-000378", "dachser").status_code in (200, 201)         # case-insensitive, canonical name kept
    assert next(s for s in client.get("/api/reference/ref_suppliers/rows").json() if s["supplier_id"] == "S-000378")["transport_mode"] == "Dachser"

    plan = client.get("/api/transports/planning", params={"start": "2026-09-28", "weeks": 2}).json()
    assert plan["scope"] == "main"
    row = next(s for s in plan["suppliers"] if s["supplier_id"] == "S-000378")
    assert row["mode"] == "Dachser" and row["carrier"] == "Dachser" and row["emails"] == ["booking@dachser.example", "quai@dachser.example"]
    o = row["orders"][0]
    # ---- a carrier transport : the carrier fills the CSV column, the mail goes to the carrier's contacts
    r = client.post("/api/transports", json={"supplier_id": "S-000378", "order_ids": [o["order_id"]], "slots": [{"date": o["expected_date"], "time": "09:00", "docks": ["B15"]}]})
    assert r.status_code == 201, r.text
    pack = r.json()
    t1 = pack["transports"][0]
    assert t1["mode"] == "transporteur" and t1["carrier"] == "Dachser" and t1["orders"][0]["supplier_id"] == "S-000378"
    assert pack["csv"].split("\r\n")[1].split(";")[2] == "Dachser" and pack["mail"]["to"] == ["booking@dachser.example", "quai@dachser.example"]

    # ---- MAF Hambach : two suppliers with an order on the same day leave the main planning for the shuttle planning
    others = [s for s in plan["suppliers"] if s["supplier_id"] != "S-000378"]
    pair, day = None, None
    for i, a in enumerate(others):
        for b in others[i + 1:]:
            common = {x["expected_date"] for x in a["orders"]} & {x["expected_date"] for x in b["orders"]}
            if common:
                pair, day = (a["supplier_id"], b["supplier_id"]), min(common)
                break
        if pair:
            break
    assert pair, "the seed should hold two suppliers with firm orders on one day"
    for sid, mail in zip(pair, ("maf-a@example.com", "maf-b@example.com")):
        assert _set_mode(client, sid, "MAF Hambach", mail).status_code in (200, 201)
    main = client.get("/api/transports/planning", params={"start": "2026-09-28", "weeks": 2}).json()
    assert not any(s["supplier_id"] in pair for s in main["suppliers"])
    maf = client.get("/api/transports/planning", params={"start": "2026-09-28", "weeks": 2, "scope": "maf", "planner": "PERSONNE"}).json()   # the planner filter does not apply
    assert maf["scope"] == "maf" and {s["supplier_id"] for s in maf["suppliers"]} == set(pair) and all(s["mode"] == "MAF Hambach" for s in maf["suppliers"])
    ids = [x["order_id"] for s in maf["suppliers"] for x in s["orders"] if x["expected_date"] == day]
    # a MAF supplier cannot get a transport through the main route
    assert client.post("/api/transports", json={"supplier_id": pair[0], "order_ids": ids[:1], "slots": [{"date": day, "time": "10:00", "docks": ["B15"]}]}).status_code == 422
    r = client.post("/api/transports", json={"maf": True, "order_ids": ids, "slots": [{"date": day, "time": "10:00", "docks": ["B6 EST"]}], "note": "Navette du matin"})
    assert r.status_code == 201, r.text
    pack = r.json()
    t2 = pack["transports"][0]
    assert t2["mode"] == "maf" and t2["supplier_name"] == "NAVETTE HAMBACH" and {x["supplier_id"] for x in t2["orders"]} == set(pair)
    line = pack["csv"].split("\r\n")[1].split(";")
    assert line[1] == "NAVETTE HAMBACH" and line[5] == "NAVETTE HAMBACH" and line[9] == "B6 EST"
    assert sorted(pack["mail"]["to"]) == ["maf-a@example.com", "maf-b@example.com"] and "NAVETTE HAMBACH" in pack["mail"]["subject"]
    maf = client.get("/api/transports/planning", params={"start": "2026-09-28", "weeks": 2, "scope": "maf"}).json()
    assert [t["transport_id"] for t in maf["transports"]] == [t2["transport_id"]]
    assert all(t["mode"] != "maf" for t in client.get("/api/transports/planning", params={"start": "2026-09-28", "weeks": 2}).json()["transports"])

    # ---- Messagerie : out of the planning, firm orders marked in the grid, no transport
    parcel = next(s["supplier_id"] for s in main["suppliers"] if s["supplier_id"] not in pair and s["supplier_id"] != "S-000378")
    aid = next(s for s in main["suppliers"] if s["supplier_id"] == parcel)["orders"][0]["article_id"]
    assert _set_mode(client, parcel, "Messagerie").status_code in (200, 201)
    assert not any(s["supplier_id"] == parcel for s in client.get("/api/transports/planning", params={"start": "2026-09-28", "weeks": 2}).json()["suppliers"])
    lane = next(l for l in client.get(f"/api/articles/{aid}/projection").json()["lanes"] if l["supplier_id"] == parcel)
    assert all(o["transports"] == ["Messagerie"] for o in lane["orders"] if o["order_type"] == "FIRM")
    assert client.post("/api/transports", json={"supplier_id": parcel, "order_ids": [lane["orders"][0]["order_id"]], "slots": [{"date": day, "time": "10:00", "docks": ["B15"]}]}).status_code == 422

    # ---- batches : CSV of a selection, one mail per recipient group, cancel several at once
    sel = client.get("/api/transports/atr.csv", params={"transport_id": [t1["transport_id"], t2["transport_id"]]})
    assert sel.status_code == 200 and [l.split(";")[0] for l in sel.text.lstrip("﻿").split("\r\n")[1:] if l] == [t1["transport_id"], t2["transport_id"]]
    assert client.post("/api/transports/pack", json={"transport_ids": [t1["transport_id"], t2["transport_id"]]}).status_code == 422
    assert client.post("/api/transports/pack", json={"transport_ids": [t2["transport_id"]]}).json()["mail"]["to"] == sorted(["maf-a@example.com", "maf-b@example.com"])
    out = client.post("/api/transports/cancel", json={"transport_ids": [t1["transport_id"], t2["transport_id"]]}).json()
    assert [t["status"] for t in out] == ["cancelled", "cancelled"]
    assert client.post("/api/transports/cancel", json={"transport_ids": ["EM999999"]}).status_code == 404
    labels = [a["label"] for a in client.get("/api/actions").json()]
    assert any("annulé(s)" in x for x in labels)
