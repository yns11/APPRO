"""Planning transport : firm orders per supplier and day, transports (EM counter, CSV for the transport tool with
header and one line per dock), global CSV, cancellation (a split family gives its orders back), EV returns and
one-off trucks, supplier default docks, grid marks, and the supplier portal (role, gate, overview)."""
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
    assert ids == ["EM00001A", "EM00001B"] and pack["transports"][1]["docks"] == ["B2 NORD", "B6 NORD"] and pack["transports"][1]["carrier"] == "Geodis"
    lines = pack["csv"].split("\r\n")
    d, m, y = day[8:10], day[5:7], day[0:4]
    assert lines[0] == HEADER and lines[-1] == "" and len(lines) == 5          # header + 1 + 2 docks
    assert lines[1] == f"EM00001A;{row['name']};Geodis;{d}/{m}/{y};08:30:00;S-000378;R;;;B15"
    assert lines[2] == f"EM00001B;{row['name']};Geodis;{d}/{m}/{y};14:00:00;S-000378;R;;;B2 NORD"
    assert lines[3] == f"EM00001B;{row['name']};Geodis;{d}/{m}/{y};14:00:00;S-000378;R;;;B6 NORD"
    assert pack["csv_filename"] == "ATR_EM00001A-EM00001B.csv" and "mail" not in pack      # the transport tool notifies the supplier
    qty_a = {o["order_id"]: o["qty"] for o in pack["transports"][0]["orders"]}

    # the planning shows the transports on the orders ; the grid marks the firm order slot ; the counter goes on
    plan = client.get("/api/transports/planning", params={"start": "2026-09-28", "weeks": 2}).json()
    row = next(s for s in plan["suppliers"] if s["supplier_id"] == "S-000378")
    assert next(o for o in row["orders"] if o["order_id"] == same_day[0]["order_id"])["transports"] == ["EM00001A", "EM00001B"]
    aid = same_day[0]["article_id"]
    lane = next(l for l in client.get(f"/api/articles/{aid}/projection").json()["lanes"] if l["supplier_id"] == "S-000378")
    assert next(o for o in lane["orders"] if o["order_id"] == same_day[0]["order_id"])["transports"] == ["EM00001A", "EM00001B"]
    r = client.post("/api/transports", json={**body, "slots": [{"date": day, "time": "16:00", "docks": ["B6 EST"]}]})
    assert r.json()["transports"][0]["transport_id"] == "EM00002A"

    # pack again, per-transport CSV, global CSV (every planned transport from today on), list, cancel, journal
    again = client.post("/api/transports/pack", json={"transport_ids": ["EM00001B"]}).json()
    assert again["csv"].split("\r\n")[1].startswith("EM00001B;") and again["csv_filename"] == "ATR_EM00001B.csv"
    one = client.get("/api/transports/EM00001B/atr.csv")
    assert one.status_code == 200 and one.headers["content-disposition"].endswith('filename="ATR_EM00001B.csv"') and one.text.lstrip("﻿").startswith(HEADER)
    assert client.delete("/api/transports/EM00001B").json()["status"] == "cancelled"
    # EM00001A (same family, same day) takes the orders back ; both trucks carried them whole : no quantity doubles
    kept = next(t for t in client.get("/api/transports", params={"supplier_id": "S-000378"}).json() if t["transport_id"] == "EM00001A")
    assert {o["order_id"]: o["qty"] for o in kept["orders"]} == qty_a and kept["pinned"] is True
    glob = client.get("/api/transports/atr.csv")
    assert glob.status_code == 200 and glob.headers["content-disposition"].endswith('filename="ATR_transports_20260919.csv"')
    glines = glob.text.lstrip("﻿").split("\r\n")
    assert glines[0] == HEADER and [l.split(";")[0] for l in glines[1:] if l] == ["EM00001A", "EM00002A"]
    lst = client.get("/api/transports", params={"supplier_id": "S-000378"}).json()
    assert [t["transport_id"] for t in lst] == ["EM00001A", "EM00002A"]
    assert [t["transport_id"] for t in client.get("/api/transports", params={"supplier_id": "S-000378", "include_cancelled": True}).json()] == ["EM00001A", "EM00001B", "EM00002A"]
    plan = client.get("/api/transports/planning", params={"start": "2026-09-28", "weeks": 2}).json()
    assert [t["transport_id"] for t in plan["transports"]] == ["EM00001A", "EM00002A"]
    r = client.post("/api/transports", json={**body, "slots": [{"date": day, "time": "08:30", "docks": ["B99"]}]})
    assert r.status_code == 422
    labels = [a["label"] for a in client.get("/api/actions").json()]
    assert any("EM00001A, EM00001B" in x for x in labels) and any("EM00001B annulé" in x for x in labels)


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
    assert [t["transport_id"] for t in p["transports"]] == ["EM00001A"]
    assert next(r for r in p["orders"] if r["order_id"] == o["order_id"])["transports"] == ["EM00001A"]
    assert all(r["supplier_id"] == "S-000378" for r in p["desadv"] + p["receipts"])
    k = p["kpis"]
    assert k["open_orders"] == len(p["orders"]) and k["late_orders"] == len(p["backlog"]) and k["transports"] == 1 and k["next_transport"].startswith("EM00001A")
    # a planner is never a supplier, even when listed as a contact ; a reader without a contact is not a supplier
    assert client.get("/api/config").json()["access"]["role"] in ("admin", "appro", "manager")
    assert client.get("/api/portal/overview", headers={"x-forwarded-email": "nobody@example.com"}).status_code == 403
    # a manager / admin previews a supplier's portal
    assert client.get("/api/portal/overview", params={"supplier_id": "S-000378"}).status_code == 200


def test_legacy_dock_column_is_retired_and_transports_start_fresh(tmp_path):
    """A database created by the first version holds ``app_transports.dock NOT NULL`` : the start-up migration
    copies it into ``docks`` and drops it, so the new version's INSERT no longer fails (NotNullViolation).  The
    fresh start of November 2026 then deletes every transport, the counters, the suppressed days, the former
    weekly plans table and the ✱ columns : the next reconciliation starts from nothing."""
    import datetime as dt

    from sqlalchemy import create_engine, inspect, text

    from appro.data.store import AppCounter, AppMeta, AppTransport, apply_transports_fresh_start, init_store

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE app_transports (transport_id VARCHAR(20) PRIMARY KEY, supplier_id VARCHAR(40) NOT NULL, date DATE NOT NULL, "
                          "time VARCHAR(8) NOT NULL, dock VARCHAR(20) NOT NULL, note TEXT NOT NULL, status VARCHAR(12) NOT NULL, "
                          "created_by VARCHAR(120) NOT NULL, created_at DATETIME NOT NULL, changed_since_sent BOOLEAN, change_note TEXT)"))
        conn.execute(text("INSERT INTO app_transports VALUES ('EM000001', 'S-1', '2026-10-30', '08:00', 'B15', '', 'planned', 'x', '2026-10-29 06:00:00', 0, '')"))
        conn.execute(text("CREATE TABLE app_transport_plans (recipient VARCHAR(160), week VARCHAR(8), sent_no INTEGER)"))
    factory = init_store(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("app_transports")}
    assert "dock" not in cols and {"docks", "carrier"} <= cols and not {"changed_since_sent", "change_note"} & cols
    assert not inspect(engine).has_table("app_transport_plans")
    with engine.connect() as conn:
        assert "transports_fresh_start_2026_11" in {r[0] for r in conn.execute(text("SELECT name FROM app_migrations")).all()}
    with factory() as session:
        assert session.get(AppTransport, "EM000001") is None          # purged by the fresh start
        session.add(AppTransport(transport_id="EM00002A", supplier_id="S-1", date=dt.date(2026, 10, 30), time="09:00", docks="B15;B6 EST", created_by="y"))
        session.add(AppCounter(name="transport", value=2))
        session.add(AppMeta(key="transports_reconciled_version", value="7"))
        session.commit()
        assert session.get(AppTransport, "EM00002A").docks == "B15;B6 EST"
    # the fresh start itself : transports, counters and the reconciled version go ; the next start reconciles again
    assert apply_transports_fresh_start(engine) == 1
    with factory() as session:
        assert session.get(AppTransport, "EM00002A") is None and session.get(AppCounter, "transport") is None
        assert session.get(AppMeta, "transports_reconciled_version") is None


def _set_mode(c, sid: str, mode: str, contact: str | None = None):
    rows = c.get("/api/reference/ref_suppliers/rows").json()
    r = next(x for x in rows if x["supplier_id"] == sid)
    keep = {k: v for k, v in r.items() if k not in ("updated_by", "updated_at", "articles", "article_count")}
    if contact is not None:
        keep["transport_contact"] = contact
    return c.put("/api/reference/ref_suppliers/rows", json={"values": {**keep, "transport_mode": mode}})


def test_transport_modes_carrier_maf_parcel_and_batches(client):  # noqa: F811
    """Carriers table ; supplier transport mode (Fournisseur by default, a carrier, MAF Hambach, Messagerie) ;
    the main planning against the MAF planning ; shuttle transports across suppliers ; CSV per mode ; batch cancel,
    CSV of a selection."""
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
    # ---- a carrier transport : the carrier fills the CSV column
    r = client.post("/api/transports", json={"supplier_id": "S-000378", "order_ids": [o["order_id"]], "slots": [{"date": o["expected_date"], "time": "09:00", "docks": ["B15"]}]})
    assert r.status_code == 201, r.text
    pack = r.json()
    t1 = pack["transports"][0]
    assert t1["mode"] == "transporteur" and t1["carrier"] == "Dachser" and t1["orders"][0]["supplier_id"] == "S-000378"
    assert pack["csv"].split("\r\n")[1].split(";")[2] == "Dachser"

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
    maf = client.get("/api/transports/planning", params={"start": "2026-09-28", "weeks": 2, "scope": "maf"}).json()
    assert [t["transport_id"] for t in maf["transports"]] == [t2["transport_id"]]
    # a second shuttle the same day shares the number : A then B ; another day draws a new number
    assert t2["transport_id"].endswith("A")
    r2 = client.post("/api/transports", json={"maf": True, "order_ids": ids[:1], "slots": [{"date": day, "time": "15:00", "docks": ["B6 EST"]}]})
    assert r2.status_code == 201 and r2.json()["transports"][0]["transport_id"] == t2["transport_id"][:7] + "B"
    other = next(x["expected_date"] for s in maf["suppliers"] for x in s["orders"] if x["expected_date"] != day)
    other_ids = [x["order_id"] for s in maf["suppliers"] for x in s["orders"] if x["expected_date"] == other]
    r3 = client.post("/api/transports", json={"maf": True, "order_ids": other_ids, "slots": [{"date": other, "time": "10:00", "docks": ["B6 EST"]}]})
    assert r3.status_code == 201 and r3.json()["transports"][0]["transport_id"].endswith("A") and r3.json()["transports"][0]["transport_id"][:7] != t2["transport_id"][:7]
    assert all(t["mode"] != "maf" for t in client.get("/api/transports/planning", params={"start": "2026-09-28", "weeks": 2}).json()["transports"])

    # ---- Messagerie : out of the planning, firm orders marked in the grid, no transport
    parcel = next(s["supplier_id"] for s in main["suppliers"] if s["supplier_id"] not in pair and s["supplier_id"] != "S-000378")
    aid = next(s for s in main["suppliers"] if s["supplier_id"] == parcel)["orders"][0]["article_id"]
    assert _set_mode(client, parcel, "Messagerie").status_code in (200, 201)
    assert not any(s["supplier_id"] == parcel for s in client.get("/api/transports/planning", params={"start": "2026-09-28", "weeks": 2}).json()["suppliers"])
    lane = next(l for l in client.get(f"/api/articles/{aid}/projection").json()["lanes"] if l["supplier_id"] == parcel)
    assert all(o["transports"] == ["Messagerie"] for o in lane["orders"] if o["order_type"] == "FIRM")
    assert client.post("/api/transports", json={"supplier_id": parcel, "order_ids": [lane["orders"][0]["order_id"]], "slots": [{"date": day, "time": "10:00", "docks": ["B15"]}]}).status_code == 422

    # ---- batches : CSV of a selection, cancel several at once
    sel = client.get("/api/transports/atr.csv", params={"transport_id": [t1["transport_id"], t2["transport_id"]]})
    assert sel.status_code == 200 and [l.split(";")[0] for l in sel.text.lstrip("﻿").split("\r\n")[1:] if l] == [t1["transport_id"], t2["transport_id"]]
    assert client.post("/api/transports/pack", json={"transport_ids": [t1["transport_id"], t2["transport_id"]]}).json()["csv_filename"] == f"ATR_{t1['transport_id']}-{t2['transport_id']}.csv"
    out = client.post("/api/transports/cancel", json={"transport_ids": [t1["transport_id"], t2["transport_id"]]}).json()
    assert [t["status"] for t in out] == ["cancelled", "cancelled"]
    # the second shuttle of the day (same number, letter B) took the orders of the cancelled first one
    shuttle_b = next(t for t in client.get("/api/transports", params={"supplier_id": "MAF-HAMBACH"}).json() if t["transport_id"] == t2["transport_id"][:7] + "B")
    assert {o["order_id"] for o in shuttle_b["orders"]} == set(ids)
    assert client.post("/api/transports/cancel", json={"transport_ids": ["EM999999"]}).status_code == 404
    labels = [a["label"] for a in client.get("/api/actions").json()]
    assert any("annulé(s)" in x for x in labels)


def test_ev_return_and_one_off_truck(client):  # noqa: F811
    """A truck without order : the EV return has its own counter (``EM0001EV``), the one-off truck a new family ;
    both are pinned, listed in the planning (the supplier gets a row even without order), in the CSV and the docks
    view ; the reconciliation never touches them."""
    r = client.post("/api/transports/special", json={"kind": "ev", "supplier_id": "S-000378", "date": "2026-09-29", "time": "10:00", "docks": ["B6 EST"], "note": "enlèvement emballages"})
    assert r.status_code == 201, r.text
    ev = r.json()["transports"][0]
    assert ev["transport_id"] == "EM0001EV" and ev["mode"] == "ev" and ev["orders"] == [] and ev["pinned"] is True and ev["origin"] == "manuel"
    assert r.json()["csv"].split("\r\n")[1] == f"EM0001EV;{ev['supplier_name']};;29/09/2026;10:00:00;S-000378;R;;;B6 EST"
    r = client.post("/api/transports/special", json={"kind": "ev", "supplier_id": "S-000517", "date": "2026-09-29", "time": "10:00", "docks": ["B6 EST"]})
    assert r.json()["transports"][0]["transport_id"] == "EM0002EV"
    r = client.post("/api/transports/special", json={"kind": "ponctuel", "supplier_id": "S-000378", "date": "2026-09-30", "time": "14:00", "docks": ["B15"], "carrier": "Geodis", "note": "échantillons"})
    assert r.status_code == 201, r.text
    one = r.json()["transports"][0]
    assert one["transport_id"] == "EM00001A" and one["mode"] == "ponctuel" and one["carrier"] == "Geodis"
    # the EV counter and the family counter are independent ; a manual truck with orders follows the family counter
    plan = client.get("/api/transports/planning", params={"start": "2026-09-28", "weeks": 1}).json()
    assert {t["transport_id"] for t in plan["transports"]} >= {"EM0001EV", "EM0002EV", "EM00001A"}
    assert any(s["supplier_id"] == "S-000378" for s in plan["suppliers"])
    assert client.post("/api/transports/special", json={"kind": "ev", "supplier_id": "S-NOPE", "date": "2026-09-29", "time": "10:00", "docks": ["B15"]}).status_code == 422
    assert client.post("/api/transports/special", json={"kind": "autre", "supplier_id": "S-000378", "date": "2026-09-29", "time": "10:00", "docks": ["B15"]}).status_code == 422
    assert client.post("/api/transports/special", json={"kind": "ev", "supplier_id": "S-000378", "date": "2026-09-29", "time": "10:00", "docks": ["B99"]}).status_code == 422
    # no order on board : nothing to split ; cancel works and gives nothing back
    assert client.post("/api/transports/EM0001EV/split", json={"order_ids": ["x"]}).status_code == 422
    assert client.delete("/api/transports/EM0001EV").json()["status"] == "cancelled"
    day = client.get("/api/transports/docks", params={"date": "2026-09-29"}).json()
    b6 = next(d for d in day["docks"] if d["dock"] == "B6 EST")
    assert [x["transport_id"] for x in b6["slots"]["10:00"]] == ["EM0002EV"] and b6["slots"]["10:00"][0]["mode"] == "ev"
    labels = [a["label"] for a in client.get("/api/actions").json()]
    assert any(x.startswith("Retour EV EM0001EV") for x in labels) and any(x.startswith("Camion ponctuel EM00001A") for x in labels)
