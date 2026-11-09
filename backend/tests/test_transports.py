"""Planning transport : the MAF shuttle (orders of several suppliers, typed lines, return leg, PDF), the CSV for the
transport tool (header, one line per dock, no line for a return leg), batches, EV returns and one-off trucks, supplier
transport modes, the supplier portal (role, gate, overview), the start-up migrations."""
from __future__ import annotations

from test_api import client  # noqa: F401

HEADER = "ID TRANSPORT;FOURNISSEUR;TRANSPORTEUR;DATE;HEURE;CODE FOURNISSEUR;FLUX;CHAMP1;CHAMP2;QUAIS"


def _set_contact(c, sid: str, contact: str, docks: str | None = None):
    rows = c.get("/api/reference/ref_suppliers/rows").json()
    r = next(x for x in rows if x["supplier_id"] == sid)
    keep = {k: v for k, v in r.items() if k in ("supplier_id", "name", "address", "contact", "delivery_weekdays", "strict_days", "lead_time_days", "active", "docks")}
    if docks is not None:
        keep["docks"] = docks
    res = c.put("/api/reference/ref_suppliers/rows", json={"values": {**keep, "transport_contact": contact}})
    assert res.status_code in (200, 201), res.text
    return r


def _set_mode(c, sid: str, mode: str, contact: str | None = None):
    rows = c.get("/api/reference/ref_suppliers/rows").json()
    r = next(x for x in rows if x["supplier_id"] == sid)
    keep = {k: v for k, v in r.items() if k not in ("updated_by", "updated_at", "articles", "article_count")}
    if contact is not None:
        keep["transport_contact"] = contact
    return c.put("/api/reference/ref_suppliers/rows", json={"values": {**keep, "transport_mode": mode}})


def _maf_pair(c):
    """Two suppliers of the seed with a firm order on one day of the horizon, switched to MAF Hambach ; that day's orders."""
    plan = c.get("/api/transports/planning", params={"start": "2026-09-21", "weeks": 1}).json()
    pair, day = None, None
    sups = [s for s in plan["suppliers"] if s["orders"]]
    for i, a in enumerate(sups):
        for b in sups[i + 1:]:
            common = {x["expected_date"] for x in a["orders"]} & {x["expected_date"] for x in b["orders"]}
            if common:
                pair, day = (a["supplier_id"], b["supplier_id"]), min(common)
                break
        if pair:
            break
    assert pair, "the seed should hold two suppliers with firm orders on one day"
    for sid, mail in zip(pair, ("maf-a@example.com", "maf-b@example.com")):
        assert _set_mode(c, sid, "MAF Hambach", mail).status_code in (200, 201)
    maf = c.get("/api/transports/planning", params={"start": "2026-09-21", "weeks": 1, "scope": "maf", "planner": "PERSONNE"}).json()   # the planner filter does not apply
    assert maf["scope"] == "maf" and {s["supplier_id"] for s in maf["suppliers"]} == set(pair) and all(s["mode"] == "MAF Hambach" for s in maf["suppliers"])
    ids = [x["order_id"] for s in maf["suppliers"] for x in s["orders"] if x["expected_date"] == day]
    return pair, day, ids, maf


def test_shuttle_lines_return_leg_pdf_and_csv(client):  # noqa: F811
    pair, day, ids, maf = _maf_pair(client)
    main = client.get("/api/transports/planning", params={"start": "2026-09-21", "weeks": 1}).json()
    assert not any(s["supplier_id"] in pair for s in main["suppliers"])
    # the former automatic trucks of the two suppliers were cancelled by the application (they are MAF now)
    gone = [t for t in client.get("/api/transports", params={"include_cancelled": True}).json() if t["supplier_id"] in pair]
    assert gone and all(t["status"] == "cancelled" and t["cancelled_by"] == "application" for t in gone)
    # a shuttle : two ticked orders, a typed line, a return leg with its own docks and lines
    body = {"date": day, "time": "08:00", "docks": ["B6 EST"], "order_ids": ids, "note": "Navette du matin",
            "lines": [{"article": "Chariots vides", "designation": "retour MAF après déchargement", "qty": "6 U"}, {"article": "", "designation": "", "qty": ""}],
            "return_trip": True, "return_docks": ["B2 NORD"], "return_lines": [{"article": "Emballages vides", "designation": "caisses", "qty": "18 PAL"}]}
    r = client.post("/api/transports", json=body)
    assert r.status_code == 201, r.text
    pack = r.json()
    t = pack["transports"][0]
    assert t["mode"] == "maf" and t["supplier_name"] == "NAVETTE HAMBACH" and {x["supplier_id"] for x in t["orders"]} == set(pair) and t["transport_id"].endswith("A")
    assert t["mine"] is True and t["pinned"] is True and t["return_trip"] is True and t["return_docks"] == ["B2 NORD"]
    assert [(ln["leg"], ln["article"], ln["qty"]) for ln in t["lines"]] == [("aller", "Chariots vides", "6 U"), ("retour", "Emballages vides", "18 PAL")]
    assert all(o["designation"] for o in t["orders"])
    # the CSV : one line per outward dock, NAVETTE HAMBACH as name and code, no line for the return leg
    lines = pack["csv"].split("\r\n")
    d, m, y = day[8:10], day[5:7], day[0:4]
    assert lines[0] == HEADER and lines[1] == f"{t['transport_id']};NAVETTE HAMBACH;;{d}/{m}/{y};08:00:00;NAVETTE HAMBACH;R;;;B6 EST" and lines[2] == "" and len(lines) == 3
    assert pack["csv_filename"] == f"ATR_{t['transport_id']}.csv"
    # the sheet of the shuttle ; a non-shuttle has none
    pdf = client.get(f"/api/transports/{t['transport_id']}/navette.pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    other = next(x for x in main["transports"] if x["mode"] != "maf")
    assert client.get(f"/api/transports/{other['transport_id']}/navette.pdf").status_code == 422
    # the lines change as a whole ; the return leg can be dropped
    r = client.post(f"/api/transports/{t['transport_id']}/lines", json={"lines": [{"article": "Palettes", "designation": "", "qty": "4"}], "return_trip": False})
    assert r.status_code == 200, r.text
    assert [(ln["leg"], ln["article"]) for ln in r.json()["lines"]] == [("aller", "Palettes")] and r.json()["return_trip"] is False and r.json()["return_docks"] == []
    r = client.post(f"/api/transports/{t['transport_id']}/lines", json={"lines": [], "return_trip": True, "return_lines": [{"article": "", "designation": "orphan", "qty": "1"}]})
    assert r.status_code == 422 and "sans article" in r.text
    assert client.post(f"/api/transports/{other['transport_id']}/lines", json={"lines": []}).status_code == 422
    # a shuttle needs an order or a line ; an order outside the MAF is refused ; an occupied slot asks the question
    assert client.post("/api/transports", json={"date": day, "time": "09:00", "docks": ["B6 EST"]}).status_code == 422
    assert client.post("/api/transports", json={"date": day, "time": "09:00", "docks": ["B6 EST"], "order_ids": [other["orders"][0]["order_id"]]}).status_code == 422
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "dock_slot_capacity", "value": 1}).status_code == 200
    r2 = client.post("/api/transports", json={"date": day, "time": "08:00", "docks": ["B6 EST"], "order_ids": ids[:1]})
    assert r2.status_code == 409 and r2.json()["detail"]["others"] == [t["transport_id"]] and r2.json()["detail"]["proposed"] == "08:30"
    r2 = client.post("/api/transports", json={"date": day, "time": "08:00", "docks": ["B6 EST"], "order_ids": ids[:1], "force": True})
    assert r2.status_code == 201 and r2.json()["transports"][0]["transport_id"] == t["transport_id"][:7] + "B"   # the second shuttle of the day shares the number
    # the shuttles are in the MAF planning, never in the main one ; another day draws a new number
    maf = client.get("/api/transports/planning", params={"start": "2026-09-21", "weeks": 1, "scope": "maf"}).json()
    assert sorted(x["transport_id"] for x in maf["transports"]) == sorted([t["transport_id"], r2.json()["transports"][0]["transport_id"]])
    assert all(x["mode"] != "maf" for x in client.get("/api/transports/planning", params={"start": "2026-09-21", "weeks": 1}).json()["transports"])
    other_day = next((x["expected_date"] for s in maf["suppliers"] for x in s["orders"] if x["expected_date"] != day), None)
    if other_day:
        r3 = client.post("/api/transports", json={"date": other_day, "time": "10:00", "docks": ["B6 EST"], "lines": [{"article": "x", "designation": "", "qty": "1"}]})
        assert r3.status_code == 201 and r3.json()["transports"][0]["transport_id"][:7] != t["transport_id"][:7]
    # batches : CSV of a selection, cancel several at once ; the second shuttle of the day takes the orders of the first
    sel = client.get("/api/transports/atr.csv", params={"transport_id": [t["transport_id"], other["transport_id"]]})
    assert sel.status_code == 200 and [l.split(";")[0] for l in sel.text.lstrip("﻿").split("\r\n")[1:] if l] == [t["transport_id"], other["transport_id"]]
    assert client.post("/api/transports/pack", json={"transport_ids": [t["transport_id"], other["transport_id"]]}).json()["csv_filename"] == f"ATR_{t['transport_id']}-{other['transport_id']}.csv"
    out = client.post("/api/transports/cancel", json={"transport_ids": [t["transport_id"]]}).json()
    assert [x["status"] for x in out] == ["cancelled"]
    shuttle_b = next(x for x in client.get("/api/transports", params={"supplier_id": "MAF-HAMBACH"}).json() if x["transport_id"] == t["transport_id"][:7] + "B")
    assert {o["order_id"] for o in shuttle_b["orders"]} == set(ids)
    assert client.post("/api/transports/cancel", json={"transport_ids": ["EM999999"]}).status_code == 404
    labels = [a["label"] for a in client.get("/api/actions").json()]
    assert any("annulé(s)" in x for x in labels) and any(x.startswith(f"Navette MAF {t['transport_id']}") for x in labels)
    # the planner's CSV : his planned transports from today on, shuttles excluded ; the shuttles have their own CSV
    glob = client.get("/api/transports/atr.csv")
    assert glob.status_code == 200 and glob.headers["content-disposition"].endswith('_20260919.csv"')
    glines = glob.text.lstrip("﻿").split("\r\n")
    ids = {l.split(";")[0] for l in glines[1:] if l}
    assert glines[0] == HEADER and ids and shuttle_b["transport_id"] not in ids and not any(l.split(";")[1] == "NAVETTE HAMBACH" for l in glines[1:] if l)
    maf = client.get("/api/transports/atr-maf.csv")
    assert maf.status_code == 200 and maf.headers["content-disposition"].endswith('filename="ATR_navettes_MAF_20260919.csv"')
    mlines = maf.text.lstrip("﻿").split("\r\n")
    assert mlines[0] == HEADER and shuttle_b["transport_id"] in {l.split(";")[0] for l in mlines[1:] if l} and all(l.split(";")[1] == "NAVETTE HAMBACH" for l in mlines[1:] if l)
    by_planner = client.get("/api/transports/atr.csv", params={"planner": "QUENTIN"})
    assert by_planner.status_code == 200 and by_planner.headers["content-disposition"].endswith('filename="ATR_QUENTIN_20260919.csv"')


def test_transport_modes_carrier_and_parcel(client):  # noqa: F811
    """Carriers table ; supplier transport mode (Fournisseur by default, a carrier, Messagerie) ; the carrier fills the CSV."""
    carriers = client.get("/api/reference/ref_carriers/rows").json()
    assert {c["name"] for c in carriers} >= {"Geodis", "DB Schenker"}
    assert client.put("/api/reference/ref_carriers/rows", json={"values": {"name": "Dachser", "contacts": "pas-un-mail", "active": True}}).status_code == 422
    assert client.put("/api/reference/ref_carriers/rows", json={"values": {"name": "Dachser", "contacts": "Booking@dachser.example; Quai@dachser.example", "active": True}}).status_code in (200, 201)
    sups = client.get("/api/reference/ref_suppliers/rows").json()
    assert all(s["transport_mode"] == "Fournisseur" and s["transport_hours"] == "" for s in sups)
    assert _set_mode(client, "S-000378", "Inconnu").status_code == 422
    assert _set_mode(client, "S-000378", "dachser").status_code in (200, 201)         # case-insensitive, canonical name kept
    assert next(s for s in client.get("/api/reference/ref_suppliers/rows").json() if s["supplier_id"] == "S-000378")["transport_mode"] == "Dachser"
    plan = client.get("/api/transports/planning", params={"start": "2026-09-21", "weeks": 1}).json()
    row = next(s for s in plan["suppliers"] if s["supplier_id"] == "S-000378")
    assert row["mode"] == "Dachser" and row["carrier"] == "Dachser" and row["emails"] == ["booking@dachser.example", "quai@dachser.example"]
    mine = [t for t in plan["transports"] if t["supplier_id"] == "S-000378"]
    assert mine and all(t["mode"] == "transporteur" and t["carrier"] == "Dachser" for t in mine)
    csv = client.get(f"/api/transports/{mine[0]['transport_id']}/atr.csv").text.lstrip("﻿").split("\r\n")
    assert csv[1].split(";")[2] == "Dachser" and csv[1].split(";")[5] == "S-000378"
    # Messagerie : out of the planning, firm orders marked in the grid, the trucks to come cancelled
    aid = row["orders"][0]["article_id"]
    assert _set_mode(client, "S-000378", "Messagerie").status_code in (200, 201)
    assert not any(s["supplier_id"] == "S-000378" for s in client.get("/api/transports/planning", params={"start": "2026-09-21", "weeks": 1}).json()["suppliers"])
    lane = next(l for l in client.get(f"/api/articles/{aid}/projection").json()["lanes"] if l["supplier_id"] == "S-000378")
    assert all(o["transports"] == ["Messagerie"] for o in lane["orders"] if o["order_type"] == "FIRM")
    assert all(t["status"] == "cancelled" for t in client.get("/api/transports", params={"supplier_id": "S-000378", "include_cancelled": True}).json())


def test_supplier_portal_role_and_gate(client):  # noqa: F811
    _set_contact(client, "S-000378", "Quai@Fournisseur.example")
    plan = client.get("/api/transports/planning", params={"start": "2026-09-21", "weeks": 1}).json()
    mine = [t for t in plan["transports"] if t["supplier_id"] == "S-000378"]
    assert mine, "the seed holds firm orders of S-000378 in the horizon : automatic trucks"
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
    assert {t["transport_id"] for t in p["transports"]} == {t["transport_id"] for t in mine}
    o = mine[0]["orders"][0]
    assert mine[0]["transport_id"] in next(r for r in p["orders"] if r["order_id"] == o["order_id"])["transports"]
    assert all(r["supplier_id"] == "S-000378" for r in p["desadv"] + p["receipts"])
    k = p["kpis"]
    assert k["open_orders"] == len(p["orders"]) and k["late_orders"] == len(p["backlog"]) and k["transports"] == len(mine) and k["next_transport"].startswith(mine[0]["transport_id"])
    # a planner is never a supplier, even when listed as a contact ; a reader without a contact is not a supplier
    assert client.get("/api/config").json()["access"]["role"] in ("admin", "appro", "manager")
    assert client.get("/api/portal/overview", headers={"x-forwarded-email": "nobody@example.com"}).status_code == 403
    # a manager / admin previews a supplier's portal
    assert client.get("/api/portal/overview", params={"supplier_id": "S-000378"}).status_code == 200


def test_start_up_migrations_of_the_transports(tmp_path):
    """A database created by the first version holds ``app_transports.dock NOT NULL`` and, later, ``origin`` : the
    start-up migrations copy ``dock`` into ``docks`` and drop it, purge the transports (fresh start of November 2026),
    drop the former plan table and columns, then drop ``origin`` and the suppliers' « Camions automatiques » column :
    the next reconciliation starts from nothing and every supplier is automatic."""
    import datetime as dt

    from sqlalchemy import create_engine, inspect, select, text

    from appro.data.store import (
        AppCounter,
        AppMeta,
        AppTransport,
        AppTransportLine,
        apply_transports_all_automatic,
        apply_transports_fresh_start,
        init_store,
    )

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE app_transports (transport_id VARCHAR(20) PRIMARY KEY, supplier_id VARCHAR(40) NOT NULL, date DATE NOT NULL, "
                          "time VARCHAR(8) NOT NULL, dock VARCHAR(20) NOT NULL, note TEXT NOT NULL, status VARCHAR(12) NOT NULL, "
                          "created_by VARCHAR(120) NOT NULL, created_at DATETIME NOT NULL, changed_since_sent BOOLEAN, change_note TEXT, origin VARCHAR(12))"))
        conn.execute(text("INSERT INTO app_transports VALUES ('EM000001', 'S-1', '2026-10-30', '08:00', 'B15', '', 'planned', 'x', '2026-10-29 06:00:00', 0, '', 'auto')"))
        conn.execute(text("CREATE TABLE app_transport_plans (recipient VARCHAR(160), week VARCHAR(8), sent_no INTEGER)"))
        conn.execute(text("CREATE TABLE ref_suppliers (supplier_id VARCHAR(40) PRIMARY KEY, name TEXT, transport_auto BOOLEAN, updated_by VARCHAR(120), updated_at DATETIME)"))
        conn.execute(text("INSERT INTO ref_suppliers (supplier_id, name, transport_auto) VALUES ('S-1', 'One', 1)"))
    factory = init_store(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("app_transports")}
    assert "dock" not in cols and {"docks", "carrier", "return_trip", "return_docks", "cancelled_by", "cancelled_at"} <= cols
    assert not {"changed_since_sent", "change_note", "origin"} & cols
    assert not inspect(engine).has_table("app_transport_plans") and inspect(engine).has_table("app_transport_lines")
    assert "transport_auto" not in {c["name"] for c in inspect(engine).get_columns("ref_suppliers")} and "transport_hours" in {c["name"] for c in inspect(engine).get_columns("ref_suppliers")}
    with engine.connect() as conn:
        assert {"transports_fresh_start_2026_11", "transports_all_automatic_2026_11", "transports_fresh_start_2026_11_b"} <= {r[0] for r in conn.execute(text("SELECT name FROM app_migrations")).all()}
    with factory() as session:
        assert session.get(AppTransport, "EM000001") is None          # purged by the fresh start
        session.add(AppTransport(transport_id="EM00002A", supplier_id="S-1", date=dt.date(2026, 10, 30), time="09:00", docks="B15;B6 EST", created_by="y"))
        session.add(AppCounter(name="transport", value=2))
        session.add(AppMeta(key="transports_reconciled_version", value="7"))
        session.commit()
        assert session.get(AppTransport, "EM00002A").docks == "B15;B6 EST"
    # the fresh start itself : transports, their lines, counters and the reconciled version go ; the next start reconciles again
    with factory() as session:
        session.add(AppTransportLine(transport_id="EM00002A", leg="aller", article="x", designation="", qty="1"))
        session.commit()
    assert apply_transports_fresh_start(engine) == 1
    with factory() as session:
        assert session.get(AppTransport, "EM00002A") is None and session.get(AppCounter, "transport") is None
        assert session.get(AppMeta, "transports_reconciled_version") is None and session.scalars(select(AppTransportLine)).first() is None
    assert apply_transports_all_automatic(engine) >= 3        # the UPDATEs apply, the DROPs are already done


def test_ev_return_and_one_off_truck(client):  # noqa: F811
    """A truck without order : the EV return has its own counter (``EM0001EV``), the one-off truck a new family ;
    both are pinned, listed in the planning (the supplier gets a row even without order), in the CSV and the docks
    view ; the reconciliation never touches them ; an occupied slot asks the question."""
    r = client.post("/api/transports/special", json={"kind": "ev", "supplier_id": "S-000599", "date": "2026-09-29", "time": "10:00", "docks": ["B6 EST"], "note": "enlèvement emballages"})
    assert r.status_code == 201, r.text
    ev = r.json()["transports"][0]
    assert ev["transport_id"] == "EM0001EV" and ev["mode"] == "ev" and ev["orders"] == [] and ev["pinned"] is True and ev["mine"] is True
    assert r.json()["csv"].split("\r\n")[1] == f"EM0001EV;{ev['supplier_name']};;29/09/2026;10:00:00;S-000599;R;;;B6 EST"
    r = client.post("/api/transports/special", json={"kind": "ev", "supplier_id": "S-000517", "date": "2026-09-29", "time": "10:00", "docks": ["B6 EST"]})
    assert r.json()["transports"][0]["transport_id"] == "EM0002EV"
    # capacity 2 : a third truck at 10:00 on B6 EST is refused with 10:30 proposed, forced it passes, a fifth never
    r = client.post("/api/transports/special", json={"kind": "ponctuel", "supplier_id": "S-000599", "date": "2026-09-29", "time": "10:00", "docks": ["B6 EST"], "carrier": "Geodis", "note": "échantillons"})
    assert r.status_code == 409 and r.json()["detail"]["proposed"] == "10:30" and r.json()["detail"]["count"] == 2 and r.json()["detail"]["hard_max"] == 4
    r = client.post("/api/transports/special", json={"kind": "ponctuel", "supplier_id": "S-000599", "date": "2026-09-29", "time": "10:00", "docks": ["B6 EST"], "carrier": "Geodis", "note": "échantillons", "force": True})
    assert r.status_code == 201, r.text
    one = r.json()["transports"][0]
    assert one["mode"] == "ponctuel" and one["carrier"] == "Geodis" and one["transport_id"].endswith("A") and len(one["transport_id"]) == 8
    assert client.post("/api/transports/special", json={"kind": "ev", "supplier_id": "S-000599", "date": "2026-09-29", "time": "10:10", "docks": ["B6 EST"], "force": True}).status_code == 201
    r = client.post("/api/transports/special", json={"kind": "ev", "supplier_id": "S-000599", "date": "2026-09-29", "time": "10:20", "docks": ["B6 EST"], "force": True})
    assert r.status_code == 422 and "plein" in r.text
    # the EV counter and the family counter are independent ; the suppliers keep a row without order
    plan = client.get("/api/transports/planning", params={"start": "2026-09-28", "weeks": 1}).json()
    assert {t["transport_id"] for t in plan["transports"]} >= {"EM0001EV", "EM0002EV", "EM0003EV", one["transport_id"]}
    assert any(s["supplier_id"] == "S-000599" for s in plan["suppliers"])
    assert client.post("/api/transports/special", json={"kind": "ev", "supplier_id": "S-NOPE", "date": "2026-09-29", "time": "10:00", "docks": ["B15"]}).status_code == 422
    assert client.post("/api/transports/special", json={"kind": "autre", "supplier_id": "S-000599", "date": "2026-09-29", "time": "10:00", "docks": ["B15"]}).status_code == 422
    assert client.post("/api/transports/special", json={"kind": "ev", "supplier_id": "S-000599", "date": "2026-09-29", "time": "10:00", "docks": ["B99"]}).status_code == 422
    # no order on board : nothing to split ; cancel works and gives nothing back ; the cancelled truck is on the board, struck
    assert client.post("/api/transports/EM0001EV/split", json={"order_ids": ["x"]}).status_code == 422
    assert client.delete("/api/transports/EM0001EV").json()["status"] == "cancelled"
    day = client.get("/api/transports/docks", params={"date": "2026-09-29"}).json()
    b6 = next(d for d in day["docks"] if d["dock"] == "B6 EST")
    assert sorted(x["transport_id"] for x in b6["slots"]["10:00"]) == sorted(["EM0002EV", "EM0003EV", one["transport_id"]]) and b6["slots"]["10:00"][0]["mode"] in ("ev", "ponctuel")
    board = client.get("/api/transports/board", params={"start": "2026-09-28", "weeks": 1}).json()
    assert next(t for t in board["transports"] if t["transport_id"] == "EM0001EV")["status"] == "cancelled"
    labels = [a["label"] for a in client.get("/api/actions").json()]
    assert any(x.startswith("Retour EV EM0001EV") for x in labels) and any(x.startswith(f"Camion ponctuel {one['transport_id']}") for x in labels)


def test_packaging_table_ev_return_lines_and_return_note(client):  # noqa: F811
    """Référentiel › Emballages (article, code, name, unit weight) ; the EV return dialog lists the packaging of the
    supplier's articles ; the planned quantities ride the truck (lines, leg « ev ») and print on the return note with the
    supplier's address ; the lines change as a whole."""
    put = lambda vals: client.put("/api/reference/ref_packaging/rows", json={"values": vals})  # noqa: E731
    art = next(r for r in client.get("/api/reference/ref_article_suppliers/rows").json() if r["supplier_id"] == "S-000599")["article_id"]
    assert put({"article_id": art, "packaging_code": "R200044", "packaging_name": "REGR EMC BUSBAR ASSY BEV", "unit_weight_kg": 80}).status_code in (200, 201)
    assert put({"article_id": art, "packaging_code": "R200045", "packaging_name": "REGR AC BUSBAR ASSY BEV"}).status_code in (200, 201)
    assert put({"article_id": "P-NOPE", "packaging_code": "X", "packaging_name": "x"}).status_code == 422
    assert put({"article_id": art, "packaging_code": "R200046", "packaging_name": "x", "unit_weight_kg": -1}).status_code == 422
    rows = client.get("/api/reference/ref_packaging/rows").json()
    assert [(r["packaging_code"], r["unit_weight_kg"]) for r in rows] == [("R200044", 80.0), ("R200045", None)]
    assert client.get("/api/reference/ref_packaging/rows", params={"planner": "QUENTIN"}).json() == rows
    # the supplier's packaging lines ; another supplier has none
    lines = client.get("/api/transports/packaging", params={"supplier_id": "S-000599"}).json()
    assert [(x["article_id"], x["packaging_code"], x["packaging_name"], x["unit_weight_kg"]) for x in lines] == [(art, "R200044", "REGR EMC BUSBAR ASSY BEV", 80.0), (art, "R200045", "REGR AC BUSBAR ASSY BEV", None)]
    assert lines[0]["designation"]
    assert client.get("/api/transports/packaging", params={"supplier_id": "S-000378"}).json() == []
    # the address of the supplier (former country column) prints on the note
    _set_contact(client, "S-000599", "", None)
    me = next(r for r in client.get("/api/reference/ref_suppliers/rows").json() if r["supplier_id"] == "S-000599")
    assert "country" not in me and "address" in me
    keep = {k: v for k, v in me.items() if k not in ("updated_by", "updated_at", "articles", "article_count")}
    assert client.put("/api/reference/ref_suppliers/rows", json={"values": {**keep, "address": "SOLUBAC, 5 rue Charles de Coulomb, 14120 MONDEVILLE"}}).status_code == 200
    # the EV return with planned quantities : every packaging line is kept, with or without quantity ; no code = refused
    body = {"kind": "ev", "supplier_id": "S-000599", "date": "2026-09-29", "time": "10:00", "docks": ["B6 EST"], "note": "palettes à sangler",
            "lines": [{"article": art, "code": "R200044", "designation": "REGR EMC BUSBAR ASSY BEV", "qty": "12"}, {"article": art, "code": "R200045", "designation": "REGR AC BUSBAR ASSY BEV", "qty": ""}]}
    r = client.post("/api/transports/special", json=body)
    assert r.status_code == 201, r.text
    ev = r.json()["transports"][0]
    assert [(ln["leg"], ln["code"], ln["qty"]) for ln in ev["lines"]] == [("ev", "R200044", "12"), ("ev", "R200045", "")]
    assert client.post("/api/transports/special", json={**body, "lines": [{"article": art, "code": "", "designation": "x", "qty": "3"}]}).status_code == 422
    pdf = client.get(f"/api/transports/{ev['transport_id']}/retour-ev.pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")
    # the lines change as a whole ; a one-off truck has no return note
    r = client.post(f"/api/transports/{ev['transport_id']}/lines", json={"lines": [{"article": art, "code": "R200044", "designation": "x", "qty": "10"}, {"article": art, "code": "R200045", "designation": "y", "qty": "2"}]})
    assert r.status_code == 200 and [(ln["code"], ln["qty"]) for ln in r.json()["lines"]] == [("R200044", "10"), ("R200045", "2")]
    one = client.post("/api/transports/special", json={"kind": "ponctuel", "supplier_id": "S-000599", "date": "2026-09-30", "time": "10:00", "docks": ["B15"]}).json()["transports"][0]
    assert client.get(f"/api/transports/{one['transport_id']}/retour-ev.pdf").status_code == 422
    assert client.post(f"/api/transports/{one['transport_id']}/lines", json={"lines": []}).status_code == 422
