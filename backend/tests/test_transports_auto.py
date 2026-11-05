"""Camions automatiques : reserved access, reconciliation over the two-week horizon (one truck per supplier-day,
orders ride it, slots), planner actions (move, split, cancel with the sibling taking the orders back, seen),
suppressed days, docks × slots, manual creation refused for an automatic supplier."""
from __future__ import annotations

import datetime as dt

from test_api import AS_OF, client  # noqa: F401

from appro.engine.models import EngineParams
from appro.services import transports_auto as A

OTHER = {"x-forwarded-email": "someone.else@example.com"}


def _row(c, sid: str) -> dict:
    return next(x for x in c.get("/api/reference/ref_suppliers/rows").json() if x["supplier_id"] == sid)


def _set(c, sid: str, **values):
    r = _row(c, sid)
    keep = {k: v for k, v in r.items() if k in ("supplier_id", "name", "country", "contact", "delivery_weekdays", "strict_days", "lead_time_days", "active", "docks",
                                                  "transport_contact", "transport_mode", "cutoff_time", "transport_auto")}
    res = c.put("/api/reference/ref_suppliers/rows", json={"values": {**keep, **values}})
    assert res.status_code in (200, 201), res.text
    return res.json()


def _planning(c, start="2026-09-21", weeks=2):
    r = c.get("/api/transports/planning", params={"start": start, "weeks": weeks})
    assert r.status_code == 200, r.text
    return r.json()


def test_slot_grid_and_placement():
    p = EngineParams()
    grid = A.slot_grid(p)
    assert grid[0] == "07:00" and grid[-1] == "15:30" and len(grid) == 18
    assert A.slot_of("07:10", grid) == "07:00" and A.slot_of("15:45", grid) == "15:30" and A.slot_of("16:00", grid) is None and A.slot_of("", grid) is None
    occ = A.Occupancy()
    # habitual hour kept while the dock has room ; then the nearest free slot ; then the last slot and a reason
    assert A.place(occ, grid, ["B15"], "09:00", None, 2) == ("09:00", "")
    occ.add(["B15"], "09:00", grid)
    occ.add(["B15"], "09:00", grid)
    assert A.place(occ, grid, ["B15"], "09:00", None, 2) == ("09:30", "")
    occ.add(["B15"], "09:30", grid)
    occ.add(["B15"], "09:30", grid)
    assert A.place(occ, grid, ["B15"], "09:00", None, 2) == ("08:30", "")
    assert A.place(occ, grid, ["B15"], None, "09:30", 2) == ("10:00", "")          # current slot full, no habit : nearest to the current one
    for s in grid:
        occ.add(["B6 EST"], s, grid)
    time, why = A.place(occ, grid, ["B6 EST"], "08:00", None, 1)
    assert time == "15:30" and "complet" in why
    # a two-dock truck needs room on both docks
    assert A.place(occ, grid, ["B15", "B6 EST"], "08:00", None, 1)[1]
    assert A.slot_grid(p.copy_with(dock_slot_start="08:00", dock_slot_end="10:00", dock_slot_minutes=60)) == ["08:00", "09:00", "10:00"]


def test_access_reserved_to_transport_users(client):  # noqa: F811
    assert client.get("/api/config").json()["access"]["can_transports"] is True
    assert client.get("/api/config", headers=OTHER).json()["access"]["can_transports"] is False
    assert client.get("/api/transports/planning", headers=OTHER).status_code == 403
    assert client.get("/api/transports/docks", params={"date": "2026-09-21"}, headers=OTHER).status_code == 403
    assert client.get("/api/transports", headers=OTHER).status_code == 403
    assert client.get("/api/transports/planning").status_code == 200


def test_automatic_trucks_reconciliation_and_actions(client):  # noqa: F811
    # capacity 1 per dock and slot, so two automatic suppliers on B15 get different slots
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "dock_slot_capacity", "value": 1}).status_code == 200
    _set(client, "S-000032", transport_auto=True)
    _set(client, "S-000517", transport_auto=True)
    plan = _planning(client)
    assert plan["slots"][0] == "07:00" and len(plan["slots"]) == 18 and "plan_weekday" not in plan
    row = next(s for s in plan["suppliers"] if s["supplier_id"] == "S-000032")
    assert row["auto"] is True
    auto = [t for t in plan["transports"] if t["origin"] == "auto"]
    by = {(t["supplier_id"], t["date"]): t for t in auto}
    # one truck per supplier and working day with a firm order, from today to the end of next week (nothing beyond)
    assert sorted(d for s, d in by if s == "S-000032") == ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"]
    assert sorted(d for s, d in by if s == "S-000517") == ["2026-09-24"]
    assert all(t["date"] <= "2026-09-27" for t in auto)
    assert [t for t in _planning(client, "2026-09-28", 1)["transports"] if t["origin"] == "auto"] == []
    assert len(by[("S-000032", "2026-09-25")]["orders"]) == 2 and by[("S-000032", "2026-09-25")]["docks"] == ["B15"]
    assert all(t["created_by"] == "application" and t["pinned"] is False and t["time"] in plan["slots"] for t in auto)
    # the first free slot when the supplier has no habit, then the habit ; B15 full at 07:00 (capacity 1) : next slot
    assert by[("S-000032", "2026-09-21")]["time"] == "07:00" and by[("S-000032", "2026-09-24")]["time"] == "07:00" and by[("S-000517", "2026-09-24")]["time"] == "07:30"
    # today is Saturday 19/09 : a truck for the next working day is « à vérifier »
    assert by[("S-000032", "2026-09-21")]["check_reason"].startswith("commande apparue pour demain")
    assert by[("S-000032", "2026-09-25")]["check_reason"] == ""
    ids = sorted(t["transport_id"] for t in auto)
    assert all(i.startswith("EM") and i.endswith("A") and len(i) == 8 for i in ids)
    # a second read changes nothing (the reconciliation ran once for this data version)
    again = _planning(client)
    assert sorted(t["transport_id"] for t in again["transports"] if t["origin"] == "auto") == ids
    assert all(t["time"] == by[(t["supplier_id"], t["date"])]["time"] for t in again["transports"] if t["origin"] == "auto")
    # manual creation is refused for an automatic supplier
    o = row["orders"][0]
    r = client.post("/api/transports", json={"supplier_id": "S-000032", "order_ids": [o["order_id"]], "slots": [{"date": o["expected_date"], "time": "08:00", "docks": ["B15"]}]})
    assert r.status_code == 422 and "automatiques" in r.text

    # ---- a planner moves a truck : pinned, « à vérifier » cleared, never moved again by the scheduler
    em = by[("S-000032", "2026-09-21")]["transport_id"]
    moved = client.post(f"/api/transports/{em}/move", json={"time": "09:00"}).json()
    assert moved["pinned"] is True and moved["time"] == "09:00" and moved["check_reason"] == ""
    again = _planning(client)
    assert next(t for t in again["transports"] if t["transport_id"] == em)["time"] == "09:00"
    em2 = by[("S-000517", "2026-09-24")]["transport_id"]
    moved = client.post(f"/api/transports/{em2}/move", json={"docks": ["B6 EST"], "date": "2026-09-25"}).json()
    assert moved["docks"] == ["B6 EST"] and moved["date"] == "2026-09-25" and moved["pinned"] is True and moved["time"] == "07:30"
    assert client.post(f"/api/transports/{em2}/move", json={"docks": ["B99"]}).status_code == 422

    # ---- split : one order of the two leaves for a second truck of the same day (same number, letter B), both pinned
    em3 = by[("S-000032", "2026-09-25")]["transport_id"]
    first_order = by[("S-000032", "2026-09-25")]["orders"][0]["order_id"]
    r = client.post(f"/api/transports/{em3}/split", json={"order_ids": [first_order]})
    assert r.status_code == 200, r.text
    old, new = r.json()
    assert len(old["orders"]) == 1 and len(new["orders"]) == 1 and new["orders"][0]["order_id"] == first_order
    assert old["pinned"] and new["pinned"] and new["date"] == "2026-09-25" and new["time"] in plan["slots"] and new["time"] != old["time"]
    assert new["transport_id"] == em3[:7] + "B" and em3.endswith("A") and new["origin"] == "auto"
    assert client.post(f"/api/transports/{em3}/split", json={"order_ids": [o["order_id"] for o in old["orders"]]}).status_code == 422
    # every truck is splittable : the single-order one by quantity (the rest stays on the first truck)
    single = new
    oid, qty = single["orders"][0]["order_id"], single["orders"][0]["qty"]
    assert client.post(f"/api/transports/{single['transport_id']}/split", json={"items": [{"order_id": oid, "qty": qty + 1}]}).status_code == 422
    assert client.post(f"/api/transports/{single['transport_id']}/split", json={"items": [{"order_id": oid}]}).status_code == 422
    r = client.post(f"/api/transports/{single['transport_id']}/split", json={"items": [{"order_id": oid, "qty": round(qty / 3, 3)}], "time": "11:00"})
    assert r.status_code == 200, r.text
    b, c = r.json()
    assert c["transport_id"] == em3[:7] + "C"      # a split of the B truck : C, still the family of EM…A
    assert b["orders"][0]["order_id"] == oid == c["orders"][0]["order_id"] and abs(b["orders"][0]["qty"] + c["orders"][0]["qty"] - qty) < 1e-6 and c["time"] == "11:00"
    # the reconciliation keeps the split quantities (the order rides two trucks)
    again = _planning(client)
    kept = {t["transport_id"]: t for t in again["transports"]}
    assert abs(kept[b["transport_id"]]["orders"][0]["qty"] - b["orders"][0]["qty"]) < 1e-6 and abs(kept[c["transport_id"]]["orders"][0]["qty"] - c["orders"][0]["qty"]) < 1e-6
    assert again["capacity"] == 1 and isinstance(again["closed_docks"], dict)
    # note and « vu »
    assert client.post(f"/api/transports/{em3}/note", json={"note": "prévenir le quai"}).json()["note"] == "prévenir le quai"
    assert client.post(f"/api/transports/{em}/seen").json()["check_reason"] == ""

    # ---- cancel a truck of a split family : the sibling of the day takes its orders back (C → B joins the quantity ;
    # B → A takes the whole order) ; the day keeps a truck, nothing is suppressed
    assert client.delete(f"/api/transports/{c['transport_id']}").json()["status"] == "cancelled"
    b_now = next(t for t in _planning(client)["transports"] if t["transport_id"] == b["transport_id"])
    assert len(b_now["orders"]) == 1 and abs(b_now["orders"][0]["qty"] - qty) < 1e-6
    assert client.delete(f"/api/transports/{b['transport_id']}").json()["status"] == "cancelled"
    a_now = next(t for t in _planning(client)["transports"] if t["transport_id"] == em3)
    assert sorted(o["order_id"] for o in a_now["orders"]) == sorted(o["order_id"] for o in by[("S-000032", "2026-09-25")]["orders"])
    assert a_now["pinned"] is True
    labels = [a["label"] for a in client.get("/api/actions").json()]
    assert any(x == f"Transport {b['transport_id']} annulé : {em3} reprend ses commandes" for x in labels)
    # ---- cancel the last truck of the day : the day is suppressed, the truck is not born again
    assert client.delete(f"/api/transports/{em3}").json()["status"] == "cancelled"
    after = _planning(client)
    assert [t for t in after["transports"] if t["supplier_id"] == "S-000032" and t["date"] == "2026-09-25"] == []
    assert [t["status"] for t in client.get("/api/transports", params={"supplier_id": "S-000032", "include_cancelled": True}).json() if t["transport_id"] == em3] == ["cancelled"]
    sep25 = next(s for s in after["suppliers"] if s["supplier_id"] == "S-000032")
    assert all(o["transports"] == [] for o in sep25["orders"] if o["expected_date"] == "2026-09-25")

    # ---- docks × slots of a day
    day = client.get("/api/transports/docks", params={"date": "2026-09-24"}).json()
    assert day["capacity"] == 1 and day["slots"] == plan["slots"]
    b15 = next(d for d in day["docks"] if d["dock"] == "B15")
    assert b15["closed"] is False and [x["transport_id"] for x in b15["slots"]["07:00"]] == [by[("S-000032", "2026-09-24")]["transport_id"]]
    assert b15["slots"]["07:30"] == []      # the moved truck left its slot
    day = client.get("/api/transports/docks", params={"date": "2026-09-25"}).json()
    assert [x["transport_id"] for x in next(d for d in day["docks"] if d["dock"] == "B6 EST")["slots"]["07:30"]] == [em2]

    # ---- an order ignored in the supply table leaves its truck ; an empty truck is cancelled
    # (em2 was moved to 25/09 and pinned : it kept its orders of 24/09 ; ignoring them empties it)
    t2 = next(t for t in after["transports"] if t["transport_id"] == em2)
    assert [o["expected_date"] for o in t2["orders"]] == ["2026-09-24"] * len(t2["orders"]) and t2["date"] == "2026-09-25"
    for o in t2["orders"]:
        assert client.post("/api/entries/flags/toggle", json={"article_id": o["article_id"], "supplier_id": "S-000517", "date": "2026-09-24", "kind": "order_ignored"}).status_code == 200
    after = _planning(client)
    assert not [t for t in after["transports"] if t["supplier_id"] == "S-000517"]       # ignored : no truck is born either
    assert [t["status"] for t in client.get("/api/transports", params={"supplier_id": "S-000517", "include_cancelled": True}).json() if t["transport_id"] == em2] == ["cancelled"]

    # ---- switching a supplier back to manual creates no new truck ; the manual dialog works again
    _set(client, "S-000517", transport_auto=False)
    after = _planning(client)
    r = client.post("/api/transports", json={"supplier_id": "S-000517", "order_ids": [next(s for s in after["suppliers"] if s["supplier_id"] == "S-000517")["orders"][0]["order_id"]],
                                              "slots": [{"date": "2026-09-24", "time": "08:00", "docks": ["B15"]}]})
    assert r.status_code == 201 and r.json()["transports"][0]["origin"] == "manuel" and r.json()["transports"][0]["pinned"] is True


def test_reconcile_runs_once_per_version_and_respects_capacity(client):  # noqa: F811
    _set(client, "S-000032", transport_auto=True)
    first = _planning(client)
    n = len([t for t in first["transports"] if t["origin"] == "auto"])
    assert n == 5
    # the global CSV lists the automatic trucks (today and later)
    csv = client.get("/api/transports/atr.csv").text
    assert csv.count("S-000032") == 5
    # the reconciliation is journalised once
    log = [a for a in client.get("/api/entries/audit", params={"limit": 500}).json() if a["entity_type"] == "transports"]
    assert len(log) == 1 and log[0]["payload"]["created"] == 5 and log[0]["user"] == "application"
    assert dt.date.fromisoformat(AS_OF).isoweekday() == 6
