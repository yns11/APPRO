"""Camions automatiques : reserved access, reconciliation (one truck per supplier-day, orders ride it, slots), weekly
plans (à envoyer / envoyée / à renvoyer, complete e-mail, re-sending), planner actions (move, split, cancel, seen),
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
    assert client.get("/api/transports/plans", headers=OTHER).status_code == 403
    assert client.get("/api/transports", headers=OTHER).status_code == 403
    assert client.get("/api/transports/planning").status_code == 200


def test_automatic_trucks_reconciliation_plans_and_actions(client):  # noqa: F811
    # capacity 1 per dock and slot, so two automatic suppliers on B15 get different slots
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "dock_slot_capacity", "value": 1}).status_code == 200
    _set(client, "S-000378", transport_auto=True, transport_contact="transport@fournisseur.example")
    _set(client, "S-000517", transport_auto=True)
    plan = _planning(client)
    assert plan["plan_weekday"] == 4 and plan["slots"][0] == "07:00" and len(plan["slots"]) == 18
    row = next(s for s in plan["suppliers"] if s["supplier_id"] == "S-000378")
    assert row["auto"] is True and row["recipient"] == "sup:S-000378"
    auto = [t for t in plan["transports"] if t["origin"] == "auto"]
    by = {(t["supplier_id"], t["date"]): t for t in auto}
    # one truck per supplier and working day with a firm order (Sunday 27/09 has none) ; it carries every order of the day
    assert sorted(d for s, d in by if s == "S-000378") == ["2026-09-21", "2026-09-28", "2026-10-01"]
    assert sorted(d for s, d in by if s == "S-000517") == ["2026-09-24", "2026-10-01"]
    assert len(by[("S-000378", "2026-09-28")]["orders"]) == 3 and by[("S-000378", "2026-09-28")]["docks"] == ["B15"]
    assert all(t["created_by"] == "application" and t["pinned"] is False and t["time"] in plan["slots"] for t in auto)
    # the planning shows them on the orders ; the first free slot when the supplier has no habit, then the habit
    assert by[("S-000378", "2026-09-21")]["time"] == "07:00" and by[("S-000517", "2026-09-24")]["time"] == "07:00"
    assert by[("S-000378", "2026-10-01")]["time"] == "07:00" and by[("S-000517", "2026-10-01")]["time"] == "07:30"   # B15 full at 07:00 (capacity 1)
    # today is Saturday 19/09 : a truck for the next working day is « à vérifier »
    assert by[("S-000378", "2026-09-21")]["check_reason"].startswith("commande apparue pour demain")
    assert by[("S-000378", "2026-09-28")]["check_reason"] == ""
    ids = sorted(t["transport_id"] for t in auto)
    # a second read changes nothing (the reconciliation ran once for this data version)
    again = _planning(client)
    assert sorted(t["transport_id"] for t in again["transports"] if t["origin"] == "auto") == ids
    assert all(t["time"] == by[(t["supplier_id"], t["date"])]["time"] for t in again["transports"] if t["origin"] == "auto")
    # manual creation is refused for an automatic supplier
    o = row["orders"][0]
    r = client.post("/api/transports", json={"supplier_id": "S-000378", "order_ids": [o["order_id"]], "slots": [{"date": o["expected_date"], "time": "08:00", "docks": ["B15"]}]})
    assert r.status_code == 422 and "automatiques" in r.text

    # ---- weekly plans : next week (S39) to send ; nothing left in the current week after Saturday
    plans = client.get("/api/transports/plans").json()
    assert plans["current_week"] == "2026-W38" and plans["next_week"] == "2026-W39" and plans["plan_weekday"] == 4
    p378 = next(p for p in plans["plans"] if p["recipient"] == "sup:S-000378" and p["week"] == "2026-W39")
    assert p378["state"] == "a_envoyer" and p378["trucks"] == 1 and p378["label"] == "S39" and p378["monday"] == "2026-09-21" and p378["emails"] == ["transport@fournisseur.example"]
    assert p378["checks"] == 1
    mail = client.get("/api/transports/plans/2026-W39/sup:S-000378/mail").json()
    assert mail["mail"]["to"] == ["transport@fournisseur.example"] and "semaine 39" in mail["mail"]["subject"] and "remplace" not in mail["mail"]["subject"]
    em = by[("S-000378", "2026-09-21")]["transport_id"]
    assert em in mail["mail"]["body"] and "Bonjour" in mail["mail"]["body"] and "envoi n° 1" in mail["mail"]["body"] and mail["split"] is False
    assert client.get("/api/transports/plans/2026-W39/sup:S-000599/mail").status_code == 404
    assert client.get("/api/transports/plans/bad/sup:S-000378/mail").status_code == 422
    sent = client.post("/api/transports/plans/2026-W39/sup:S-000378/sent").json()
    assert sent["sent_no"] == 1 and sent["state"] == "envoyee" and sent["sent_by"] == "quentin@example.com"
    plans = client.get("/api/transports/plans").json()
    assert next(p for p in plans["plans"] if p["recipient"] == "sup:S-000378")["state"] == "envoyee"

    # ---- a planner moves the truck of a sent week : pinned, ✱, the week is « à renvoyer » with the reason
    moved = client.post(f"/api/transports/{em}/move", json={"time": "09:00"}).json()
    assert moved["pinned"] is True and moved["time"] == "09:00" and moved["changed_since_sent"] is True and moved["change_note"] == "heure 09:00" and moved["check_reason"] == ""
    plans = client.get("/api/transports/plans").json()
    p378 = next(p for p in plans["plans"] if p["recipient"] == "sup:S-000378")
    assert p378["state"] == "a_renvoyer" and p378["changed"] == 1 and p378["reasons"] == [f"{em} : heure 09:00 (lun. 21/09)"]
    mail = client.get("/api/transports/plans/2026-W39/sup:S-000378/mail").json()
    assert "mise à jour du 19/09 (remplace l'envoi du" in mail["mail"]["subject"] and f"✱ {em}" in mail["mail"]["body"] and "envoi n° 2" in mail["mail"]["body"]
    sent = client.post("/api/transports/plans/2026-W39/sup:S-000378/sent").json()
    assert sent["sent_no"] == 2 and sent["state"] == "envoyee" and sent["changed"] == 0
    # the reconciliation never moves a pinned truck, nor a truck of a sent week
    again = _planning(client)
    assert next(t for t in again["transports"] if t["transport_id"] == em)["time"] == "09:00"
    # the same moved truck of the week not sent : nothing to re-send
    em2 = by[("S-000517", "2026-09-24")]["transport_id"]
    moved = client.post(f"/api/transports/{em2}/move", json={"docks": ["B6 EST"], "date": "2026-09-25"}).json()
    assert moved["changed_since_sent"] is False and moved["docks"] == ["B6 EST"] and moved["date"] == "2026-09-25" and moved["pinned"] is True
    assert client.post(f"/api/transports/{em2}/move", json={"docks": ["B99"]}).status_code == 422

    # ---- split : one order of the three leaves for a second truck of the same day, both pinned
    em3 = by[("S-000378", "2026-09-28")]["transport_id"]
    first_order = by[("S-000378", "2026-09-28")]["orders"][0]["order_id"]
    r = client.post(f"/api/transports/{em3}/split", json={"order_ids": [first_order]})
    assert r.status_code == 200, r.text
    old, new = r.json()
    assert len(old["orders"]) == 2 and len(new["orders"]) == 1 and new["orders"][0]["order_id"] == first_order
    assert old["pinned"] and new["pinned"] and new["date"] == "2026-09-28" and new["time"] in plan["slots"] and new["time"] != old["time"]
    assert new["transport_id"] == em3[:7] + "B" and em3.endswith("A") and new["origin"] == "auto"   # same family, next letter
    assert client.post(f"/api/transports/{em3}/split", json={"order_ids": [o["order_id"] for o in old["orders"]]}).status_code == 422
    # every truck is splittable : the single-order one by quantity (the rest stays on the first truck, both pinned)
    single = new
    oid, qty = single["orders"][0]["order_id"], single["orders"][0]["qty"]
    assert client.post(f"/api/transports/{single['transport_id']}/split", json={"items": [{"order_id": oid, "qty": qty + 1}]}).status_code == 422
    assert client.post(f"/api/transports/{single['transport_id']}/split", json={"items": [{"order_id": oid}]}).status_code == 422
    r = client.post(f"/api/transports/{single['transport_id']}/split", json={"items": [{"order_id": oid, "qty": round(qty / 3, 3)}], "time": "11:00"})
    assert r.status_code == 200, r.text
    a, b = r.json()
    assert b["transport_id"] == em3[:7] + "C"      # a split of the B truck : C, still the family of EM…A
    assert a["orders"][0]["order_id"] == oid == b["orders"][0]["order_id"] and abs(a["orders"][0]["qty"] + b["orders"][0]["qty"] - qty) < 1e-6 and b["time"] == "11:00"
    # the reconciliation keeps the split quantities (the order rides two trucks)
    again = _planning(client)
    kept = {t["transport_id"]: t for t in again["transports"]}
    assert abs(kept[a["transport_id"]]["orders"][0]["qty"] - a["orders"][0]["qty"]) < 1e-6 and abs(kept[b["transport_id"]]["orders"][0]["qty"] - b["orders"][0]["qty"]) < 1e-6
    assert again["capacity"] == 1 and isinstance(again["closed_docks"], dict)
    # note and « vu »
    assert client.post(f"/api/transports/{em3}/note", json={"note": "prévenir le quai"}).json()["note"] == "prévenir le quai"
    assert client.post(f"/api/transports/{em}/seen").json()["check_reason"] == ""

    # ---- cancel an automatic truck : the day is suppressed, the truck is not born again
    em4 = by[("S-000378", "2026-10-01")]["transport_id"]
    assert client.delete(f"/api/transports/{em4}").json()["status"] == "cancelled"
    after = _planning(client)
    oct1 = [t for t in after["transports"] if t["supplier_id"] == "S-000378" and t["date"] == "2026-10-01"]
    assert oct1 == []                       # cancelled, not ✱ (the week S40 was never sent) : gone from the planning
    assert [t["status"] for t in client.get("/api/transports", params={"supplier_id": "S-000378", "include_cancelled": True}).json() if t["transport_id"] == em4] == ["cancelled"]

    # ---- docks × slots of a day
    day = client.get("/api/transports/docks", params={"date": "2026-10-01"}).json()
    assert day["capacity"] == 1 and day["slots"] == plan["slots"]
    b15 = next(d for d in day["docks"] if d["dock"] == "B15")
    assert b15["closed"] is False and [x["transport_id"] for x in b15["slots"]["07:30"]] == [by[("S-000517", "2026-10-01")]["transport_id"]]
    assert b15["slots"]["07:00"] == []      # the cancelled truck left its slot

    # ---- an order ignored in the supply table leaves its truck ; an empty truck of a sent week is cancelled and ✱
    # (em2 was moved to 25/09 and pinned : it kept its orders of 24/09 ; ignoring them empties it)
    t2 = next(t for t in after["transports"] if t["transport_id"] == em2)
    assert [o["expected_date"] for o in t2["orders"]] == ["2026-09-24"] * len(t2["orders"]) and t2["date"] == "2026-09-25"
    client.post("/api/transports/plans/2026-W39/sup:S-000517/sent")
    for o in t2["orders"]:
        assert client.post("/api/entries/flags/toggle", json={"article_id": o["article_id"], "supplier_id": "S-000517", "date": "2026-09-24", "kind": "order_ignored"}).status_code == 200
    after = _planning(client)
    t2 = next(t for t in after["transports"] if t["transport_id"] == em2)
    assert t2["status"] == "cancelled" and t2["changed_since_sent"] is True and "annulé" in t2["change_note"]
    assert not [t for t in after["transports"] if t["supplier_id"] == "S-000517" and t["date"] == "2026-09-24"]   # ignored : no truck is born
    p517 = next(p for p in client.get("/api/transports/plans").json()["plans"] if p["recipient"] == "sup:S-000517" and p["week"] == "2026-W39")
    assert p517["state"] == "a_renvoyer" and p517["trucks"] == 0 and p517["changed"] == 1
    body = client.get("/api/transports/plans/2026-W39/sup:S-000517/mail").json()["mail"]["body"]
    assert f"✱ {em2}" in body and "ANNULÉ" in body
    # re-sending clears the ✱ : the cancelled truck leaves the plan and the band
    client.post("/api/transports/plans/2026-W39/sup:S-000517/sent")
    assert all(p["recipient"] != "sup:S-000517" or p["week"] != "2026-W39" for p in client.get("/api/transports/plans").json()["plans"])
    # the band never goes beyond the next week
    assert {p["week"] for p in client.get("/api/transports/plans").json()["plans"]} <= {"2026-W38", "2026-W39"}

    # ---- switching a supplier back to manual keeps his trucks and creates no new one ; the manual dialog works again
    _set(client, "S-000517", transport_auto=False)
    after = _planning(client)
    em5 = by[("S-000517", "2026-10-01")]["transport_id"]
    assert [t["status"] for t in after["transports"] if t["transport_id"] == em5] == ["planned"]
    r = client.post("/api/transports", json={"supplier_id": "S-000517", "order_ids": [next(s for s in after["suppliers"] if s["supplier_id"] == "S-000517")["orders"][0]["order_id"]],
                                              "slots": [{"date": "2026-10-01", "time": "08:00", "docks": ["B15"]}]})
    assert r.status_code == 201 and r.json()["transports"][0]["origin"] == "manuel" and r.json()["transports"][0]["pinned"] is True


def test_reconcile_runs_once_per_version_and_respects_capacity(client):  # noqa: F811
    _set(client, "S-000378", transport_auto=True)
    first = _planning(client)
    n = len([t for t in first["transports"] if t["origin"] == "auto"])
    assert n == 3
    # the global CSV lists the automatic trucks (today and later) ; a cancelled one leaves it
    csv = client.get("/api/transports/atr.csv").text
    assert csv.count("S-000378") == 3
    # the reconciliation is journalised once
    log = [a for a in client.get("/api/entries/audit", params={"limit": 500}).json() if a["entity_type"] == "transports"]
    assert len(log) == 1 and log[0]["payload"]["created"] == 3 and log[0]["user"] == "application"
    assert dt.date.fromisoformat(AS_OF).isoweekday() == 6
