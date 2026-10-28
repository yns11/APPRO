"""Planning de livraisons : firm orders per supplier and day, transports (EM counter, CSV for the transport
tool, e-mail to the transport contacts), cancellation."""
from __future__ import annotations

from test_api import client  # noqa: F401


def test_transports_planning_create_pack_cancel(client):  # noqa: F811
    # the supplier's transport contacts are a column of the reference table
    sup = client.get("/api/reference/ref_suppliers/rows").json()
    rows = sup["rows"] if isinstance(sup, dict) else sup
    s378 = next(r for r in rows if r["supplier_id"] == "S-000378")
    assert "transport_contact" in s378
    keep = {k: v for k, v in s378.items() if k in ("supplier_id", "name", "country", "contact", "delivery_weekdays", "strict_days", "lead_time_days", "active")}
    r = client.put("/api/reference/ref_suppliers/rows", json={"values": {**keep, "transport_contact": "transport@fournisseur.example; quai@fournisseur.example"}})
    assert r.status_code in (200, 201), r.text

    plan = client.get("/api/transports/planning", params={"start": "2026-09-28", "weeks": 2}).json()
    assert plan["start"] == "2026-09-28" and plan["end"] == "2026-10-09" and len(plan["days"]) == 10
    assert plan["docks"] == ["B15", "B06", "B2NORD"]
    row = next(s for s in plan["suppliers"] if s["supplier_id"] == "S-000378")
    assert row["emails"] == ["transport@fournisseur.example", "quai@fournisseur.example"]
    orders = row["orders"]
    assert orders and all(o["expected_date"] >= "2026-09-28" for o in orders)
    day = orders[0]["expected_date"]
    same_day = [o for o in orders if o["expected_date"] == day]

    # two trucks for the orders of that day
    body = {"supplier_id": "S-000378", "order_ids": [o["order_id"] for o in same_day],
            "slots": [{"date": day, "time": "08:30", "dock": "B15"}, {"date": day, "time": "14:00", "dock": "B2NORD"}], "note": "Deux camions"}
    r = client.post("/api/transports", json=body)
    assert r.status_code == 201, r.text
    pack = r.json()
    ids = [t["transport_id"] for t in pack["transports"]]
    assert ids == ["EM000001", "EM000002"]
    assert all(len(t["orders"]) == len(same_day) for t in pack["transports"])
    lines = pack["csv"].split("\r\n")
    assert lines[-1] == "" and len(lines) == 3
    d, m, y = day[8:10], day[5:7], day[0:4]
    assert lines[0] == f"EM000001;{row['name']};{' '.join(dict.fromkeys(o['ref'] for o in same_day))};{d}/{m}/{y};08:30:00;;R;;;B15"
    assert lines[1].startswith("EM000002;") and lines[1].endswith(";14:00:00;;R;;;B2NORD")
    assert pack["csv_filename"] == "ATR_EM000001-EM000002.csv"
    mail = pack["mail"]
    assert mail["to"] == ["transport@fournisseur.example", "quai@fournisseur.example"]
    assert "EM000001, EM000002" in mail["subject"] and f"{d}/{m}/{y}" in mail["subject"] and "quai B15 / B2NORD" in mail["subject"]
    assert "EM000002 – " in mail["body"] and "14:00" in mail["body"] and "Deux camions" in mail["body"] and same_day[0]["article_id"] in mail["body"]

    # the planning now shows the transports on the orders ; a third one continues the counter
    plan = client.get("/api/transports/planning", params={"start": "2026-09-28", "weeks": 2}).json()
    row = next(s for s in plan["suppliers"] if s["supplier_id"] == "S-000378")
    assert next(o for o in row["orders"] if o["order_id"] == same_day[0]["order_id"])["transports"] == ["EM000001", "EM000002"]
    assert [t["transport_id"] for t in plan["transports"]] == ["EM000001", "EM000002"]
    r = client.post("/api/transports", json={**body, "slots": [{"date": day, "time": "16:00", "dock": "B06"}]})
    assert r.json()["transports"][0]["transport_id"] == "EM000003"

    # pack again, cancel, journal
    again = client.get("/api/transports/EM000002/pack").json()
    assert again["csv"].startswith("EM000002;") and again["csv_filename"] == "ATR_EM000002.csv"
    assert client.delete("/api/transports/EM000002").json()["status"] == "cancelled"
    plan = client.get("/api/transports/planning", params={"start": "2026-09-28", "weeks": 2}).json()
    assert [t["transport_id"] for t in plan["transports"]] == ["EM000001", "EM000003"]
    # invalid slot
    r = client.post("/api/transports", json={**body, "slots": [{"date": day, "time": "08:30", "dock": "B99"}]})
    assert r.status_code == 422
    acts = client.get("/api/actions").json()
    labels = [a["label"] for a in (acts["actions"] if isinstance(acts, dict) else acts)]
    assert any("EM000001, EM000002" in x for x in labels) and any("EM000002 annulé" in x for x in labels)
