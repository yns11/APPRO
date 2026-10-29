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
