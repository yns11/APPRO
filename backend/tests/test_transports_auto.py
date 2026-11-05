"""Camions automatiques, every supplier : reserved access, reconciliation over the two-week horizon (one truck per
supplier-day, orders ride it), placement at creation only (spread over the dock, habitual ranges), occupied slots by
hand (409 with the nearest free slot, force up to the hard maximum), ownership (own trucks and shuttles only), planner
actions (move, split, cancel with the sibling taking the orders back, restore, seen), suppressed days, docks × slots,
the board and the printable planning."""
from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient
from test_api import AS_OF, client  # noqa: F401
from tests.conftest import SEED

from appro.api.main import create_app
from appro.config import Settings
from appro.data.store import init_store
from appro.engine.models import EngineParams, Supplier
from appro.services import transports as T
from appro.services import transports_auto as A
from appro.services.context import AppContext, bootstrap_reference, set_context

OTHER = {"x-forwarded-email": "someone.else@example.com"}
MATE = {"x-forwarded-email": "martin@example.com"}
ROOT = {"x-forwarded-email": "root@example.com"}


@pytest.fixture()
def two_planners(seed_source, tmp_path):
    """Two transport users : QUENTIN (every article of the seed) and MARTIN (a declared planner without any article)."""
    from sqlalchemy import create_engine

    engine = create_engine(f"sqlite:///{tmp_path / 'appro.db'}", connect_args={"check_same_thread": False})
    factory = init_store(engine)
    assert bootstrap_reference(factory, SEED) > 0
    settings = Settings(data_source="local", seed_dir=SEED, as_of=dt.date.fromisoformat(AS_OF), horizon_days=90,
                        static_dir=SEED / "no-such-dir", transport_users="quentin@example.com,martin@example.com")
    ctx = AppContext(settings=settings, source=seed_source, session_factory=factory)
    set_context(ctx)
    try:
        c = TestClient(create_app(), headers={"x-forwarded-email": "quentin@example.com"})
        for vals in ({"planner_id": "PROC0", "name": "ROOT", "email": "root@example.com", "role": "admin", "active": True},
                     {"planner_id": "PROC2", "name": "MARTIN", "email": "martin@example.com", "role": "appro", "active": True},
                     {"planner_id": "PROC1", "name": "QUENTIN", "email": "quentin@example.com", "role": "appro", "active": True}):
            assert c.put("/api/reference/ref_planners/rows", json={"values": vals}, headers=ROOT).status_code in (200, 201)
        yield c
    finally:
        set_context(None)


def _row(c, sid: str) -> dict:
    return next(x for x in c.get("/api/reference/ref_suppliers/rows").json() if x["supplier_id"] == sid)


def _set(c, sid: str, **values):
    r = _row(c, sid)
    keep = {k: v for k, v in r.items() if k in ("supplier_id", "name", "country", "contact", "delivery_weekdays", "strict_days", "lead_time_days", "active", "docks",
                                                  "transport_contact", "transport_mode", "cutoff_time", "transport_hours")}
    return c.put("/api/reference/ref_suppliers/rows", json={"values": {**keep, **values}})


def _planning(c, start="2026-09-21", weeks=2, **kw):
    r = c.get("/api/transports/planning", params={"start": start, "weeks": weeks, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def _board(c, headers=None):
    r = c.get("/api/transports/board", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def test_slot_grid_hours_and_spread_placement():
    p = EngineParams()
    grid = A.slot_grid(p)
    assert grid[0] == "07:00" and grid[-1] == "15:30" and len(grid) == 18
    assert A.slot_of("07:10", grid) == "07:00" and A.slot_of("15:45", grid) == "15:30" and A.slot_of("16:00", grid) is None and A.slot_of("", grid) is None
    day = T.day_slots()
    assert day[0] == "06:00" and day[-1] == "21:00" and len(day) == 31 and A.slot_of("16:00", day) == "16:00"
    # habitual ranges : canonical tokens, two at most, unknown refused
    assert T.parse_hours("6-10 ; >16") == ["06-10", "16+"] and T.parse_hours("") == [] and T.parse_hours("12-16;12-16") == ["12-16"]
    with pytest.raises(ValueError):
        T.parse_hours("06-10;08-12;10-14")
    with pytest.raises(ValueError):
        T.parse_hours("07-11")
    assert T.hours_label(["06-10", "16+"]) == "6h – 10h · ≥ 16h"
    sp = Supplier(supplier_id="S", transport_hours="16+")
    assert A.candidates_for(sp, grid) == ["16:00", "16:30", "17:00", "17:30", "18:00", "18:30", "19:00", "19:30", "20:00", "20:30", "21:00"]
    assert A.candidates_for(Supplier(supplier_id="S", transport_hours="06-10"), grid)[0] == "06:00" and A.candidates_for(None, grid) == grid
    # spread : the first truck takes the first slot, the next ones the slot farthest from the trucks already on the dock
    occ = A.Occupancy()
    got = []
    for _ in range(4):
        s, why = A.place_spread(occ, grid, ["B15"], 2)
        assert why == ""
        occ.add(["B15"], s)
        got.append(s)
    assert got == ["07:00", "15:30", "11:00", "09:00"]
    # a range : inside it ; the range full → the nearest free slot of the day ; the whole day full → the last slot and a reason
    occ = A.Occupancy()
    s, _ = A.place_spread(occ, A.candidates_for(sp, grid), ["B6 EST"], 1)
    assert s == "16:00"
    for x in A.candidates_for(sp, grid):
        occ.add(["B6 EST"], x)
    s, why = A.place_spread(occ, A.candidates_for(sp, grid), ["B6 EST"], 1)
    assert s == "15:30" and why == ""
    for x in day:
        occ.add(["B6 EST"], x)
    s, why = A.place_spread(occ, grid, ["B6 EST"], 1)
    assert s == "15:30" and "complet" in why
    # a two-dock truck needs room on both docks
    assert A.place_spread(occ, grid, ["B15", "B6 EST"], 1)[1]
    assert A.slot_grid(p.copy_with(dock_slot_start="08:00", dock_slot_end="10:00", dock_slot_minutes=60)) == ["08:00", "09:00", "10:00"]


def test_access_reserved_to_transport_users(client):  # noqa: F811
    assert client.get("/api/config").json()["access"]["can_transports"] is True
    assert client.get("/api/config", headers=OTHER).json()["access"]["can_transports"] is False
    assert client.get("/api/transports/planning", headers=OTHER).status_code == 403
    assert client.get("/api/transports/board", headers=OTHER).status_code == 403
    assert client.get("/api/transports", headers=OTHER).status_code == 403
    assert client.get("/api/transports/planning").status_code == 200


def test_every_supplier_is_automatic_and_actions(client):  # noqa: F811
    # capacity 1 per dock and slot ; S-000517 delivers after 16h as a habit
    assert client.put("/api/params/overrides", json={"scope": "global", "field": "dock_slot_capacity", "value": 1}).status_code == 200
    assert _set(client, "S-000517", transport_hours="16+").status_code in (200, 201)
    assert _set(client, "S-000517", transport_hours="06-10;08-12;10-14").status_code == 422
    assert _set(client, "S-000517", transport_hours="07-11").status_code == 422
    assert _row(client, "S-000517")["transport_hours"] == "16+"
    plan = _planning(client)
    assert plan["slots"][0] == "07:00" and len(plan["slots"]) == 18 and "auto" not in plan["suppliers"][0]
    row = next(s for s in plan["suppliers"] if s["supplier_id"] == "S-000032")
    assert row["hours"] == [] and next(s for s in plan["suppliers"] if s["supplier_id"] == "S-000517")["hours"] == ["16+"]
    trucks = [t for t in plan["transports"] if t["mode"] in ("fournisseur", "transporteur")]
    by = {(t["supplier_id"], t["date"]): t for t in trucks}
    # one truck per supplier and working day with a firm order, every supplier, from today to the end of next week
    assert sorted(d for s, d in by if s == "S-000032") == ["2026-09-21", "2026-09-22", "2026-09-23", "2026-09-24", "2026-09-25"]
    assert sorted(d for s, d in by if s == "S-000517") == ["2026-09-24"]
    with_orders = {s["supplier_id"] for s in plan["suppliers"] if any(o["expected_date"] <= "2026-09-27" and not o["ignored"] and o["qty_open"] > 0 for o in s["orders"])}
    assert with_orders <= {s for s, _d in by} and len(with_orders) >= 3
    assert all(t["date"] <= "2026-09-27" for t in trucks)
    assert [t for t in _planning(client, "2026-09-28", 1)["transports"] if t["mode"] in ("fournisseur", "transporteur")] == []
    assert len(by[("S-000032", "2026-09-25")]["orders"]) == 2 and by[("S-000032", "2026-09-25")]["docks"] == ["B15"]
    assert all(t["created_by"] == "application" and t["pinned"] is False and "origin" not in t for t in trucks)
    o = by[("S-000032", "2026-09-25")]["orders"][0]
    assert o["designation"] and o["unit"]      # the screens show the article, never the order number
    # placed once, spread over the dock : the first truck of a day at 07:00 ; S-000517 inside its range ; everybody is « mine » for an admin
    assert by[("S-000032", "2026-09-21")]["time"] == "07:00" and by[("S-000517", "2026-09-24")]["time"] >= "16:00"
    assert all(t["mine"] for t in trucks)
    # today is Saturday 19/09 : a truck for the next working day is « à vérifier »
    assert by[("S-000032", "2026-09-21")]["check_reason"].startswith("commande apparue pour demain")
    assert by[("S-000032", "2026-09-25")]["check_reason"] == ""
    ids = sorted(t["transport_id"] for t in trucks)
    assert all(i.startswith("EM") and i.endswith("A") and len(i) == 8 for i in ids)
    # a second read changes nothing (the reconciliation ran once for this data version) ; a habit set afterwards moves nothing
    assert _set(client, "S-000032", transport_hours="12-16").status_code in (200, 201)
    again = _planning(client)
    assert sorted(t["transport_id"] for t in again["transports"] if t["mode"] in ("fournisseur", "transporteur")) == ids
    assert all(t["time"] == by[(t["supplier_id"], t["date"])]["time"] for t in again["transports"] if t["mode"] in ("fournisseur", "transporteur"))
    # nobody creates a supplier truck by hand any more : the creation route is the MAF shuttle only
    r = client.post("/api/transports", json={"supplier_id": "S-000032", "order_ids": [o["order_id"]], "slots": [{"date": o["expected_date"], "time": "08:00", "docks": ["B15"]}]})
    assert r.status_code == 422

    # ---- occupied slot by hand : refused with the nearest free slot, forced within the hard maximum, never beyond
    em = by[("S-000032", "2026-09-24")]["transport_id"]          # B15 07:00
    em2 = by[("S-000517", "2026-09-24")]["transport_id"]         # B15 after 16h
    chk = client.get("/api/transports/slot", params={"date": "2026-09-24", "time": "07:00", "dock": ["B15"]}).json()
    assert chk["free"] is False and chk["count"] == 1 and chk["capacity"] == 1 and chk["hard_max"] == 2 and chk["proposed"] == "07:30" and chk["others"] == [em] and chk["forceable"]
    r = client.post(f"/api/transports/{em2}/move", json={"time": "07:00"})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["proposed"] == "07:30" and r.json()["detail"]["others"] == [em]
    moved = client.post(f"/api/transports/{em2}/move", json={"time": "07:00", "force": True}).json()
    assert moved["time"] == "07:00" and moved["pinned"] is True and moved["check_reason"] == ""
    r = client.post("/api/transports/special", json={"kind": "ev", "supplier_id": "S-000378", "date": "2026-09-24", "time": "07:15", "docks": ["B15"], "force": True})
    assert r.status_code == 422 and "plein" in r.text
    r = client.post("/api/transports/special", json={"kind": "ev", "supplier_id": "S-000378", "date": "2026-09-24", "time": "07:30", "docks": ["B15"]})
    assert r.status_code == 201, r.text
    # a free slot : no question asked ; an unknown dock refused ; the moved truck keeps its orders when the day changes
    moved = client.post(f"/api/transports/{em2}/move", json={"docks": ["B6 EST"], "date": "2026-09-25", "time": "09:00"}).json()
    assert moved["docks"] == ["B6 EST"] and moved["date"] == "2026-09-25" and moved["time"] == "09:00" and moved["pinned"] is True
    assert client.post(f"/api/transports/{em2}/move", json={"docks": ["B99"]}).status_code == 422

    # ---- split : one order of the two leaves for a second truck of the same day (same number, letter B), both pinned
    em3 = by[("S-000032", "2026-09-25")]["transport_id"]
    first_order = by[("S-000032", "2026-09-25")]["orders"][0]["order_id"]
    r = client.post(f"/api/transports/{em3}/split", json={"order_ids": [first_order]})
    assert r.status_code == 200, r.text
    old, new = r.json()
    assert len(old["orders"]) == 1 and len(new["orders"]) == 1 and new["orders"][0]["order_id"] == first_order
    assert old["pinned"] and new["pinned"] and new["date"] == "2026-09-25" and new["time"] != old["time"]
    assert new["transport_id"] == em3[:7] + "B" and em3.endswith("A")
    assert client.post(f"/api/transports/{em3}/split", json={"order_ids": [o["order_id"] for o in old["orders"]]}).status_code == 422
    # a split on an occupied slot goes through the same check
    r = client.post(f"/api/transports/{new['transport_id']}/split", json={"items": [{"order_id": first_order, "qty": 1}], "time": old["time"]})
    assert r.status_code == 409 and r.json()["detail"]["others"] == [em3]
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
    # B → A takes the whole order) ; the day keeps a truck, nothing is suppressed ; a cancelled truck stays on the board
    gone = client.delete(f"/api/transports/{c['transport_id']}").json()
    assert gone["status"] == "cancelled" and gone["cancelled_by"] == "quentin@example.com" and gone["cancelled_at"]
    b_now = next(t for t in _planning(client)["transports"] if t["transport_id"] == b["transport_id"])
    assert len(b_now["orders"]) == 1 and abs(b_now["orders"][0]["qty"] - qty) < 1e-6
    board = _board(client)
    assert next(t for t in board["transports"] if t["transport_id"] == c["transport_id"])["status"] == "cancelled"
    # C cannot come back : its quantity already rides B
    assert client.post(f"/api/transports/{c['transport_id']}/restore", json={}).status_code == 422
    assert client.delete(f"/api/transports/{b['transport_id']}").json()["status"] == "cancelled"
    a_now = next(t for t in _planning(client)["transports"] if t["transport_id"] == em3)
    assert sorted(o["order_id"] for o in a_now["orders"]) == sorted(o["order_id"] for o in by[("S-000032", "2026-09-25")]["orders"])
    assert a_now["pinned"] is True
    labels = [a["label"] for a in client.get("/api/actions").json()]
    assert any(x == f"Transport {b['transport_id']} annulé : {em3} reprend ses commandes" for x in labels)
    # ---- cancel the last truck of the day : the day is suppressed, the truck is not born again ; restore brings it back
    assert client.delete(f"/api/transports/{em3}").json()["status"] == "cancelled"
    after = _planning(client)
    assert [t for t in after["transports"] if t["supplier_id"] == "S-000032" and t["date"] == "2026-09-25"] == []
    sep25 = next(s for s in after["suppliers"] if s["supplier_id"] == "S-000032")
    assert all(o["transports"] == [] for o in sep25["orders"] if o["expected_date"] == "2026-09-25")
    back = client.post(f"/api/transports/{em3}/restore", json={})
    assert back.status_code == 200, back.text
    assert back.json()["status"] == "planned" and back.json()["cancelled_by"] == "" and len(back.json()["orders"]) == 2
    assert client.post(f"/api/transports/{em3}/restore", json={}).status_code == 422       # not cancelled
    # a restore onto an occupied slot asks the question too
    assert client.post(f"/api/transports/{em3}/move", json={"time": "09:00", "docks": ["B6 EST"], "force": True}).status_code == 200    # same slot as em2 : forced
    assert client.delete(f"/api/transports/{em3}").status_code == 200
    r = client.post(f"/api/transports/{em3}/restore", json={})
    assert r.status_code == 409 and r.json()["detail"]["others"] == [em2]
    assert client.post(f"/api/transports/{em3}/restore", json={"force": True}).status_code == 200

    # ---- docks × slots of a day : half hours from 06:00, the automatic grid apart
    day = client.get("/api/transports/docks", params={"date": "2026-09-24"}).json()
    assert day["capacity"] == 1 and day["hard_max"] == 2 and day["slots"][0] == "06:00" and day["auto_slots"] == plan["slots"]
    b15 = next(d for d in day["docks"] if d["dock"] == "B15")
    assert b15["closed"] is False and [x["transport_id"] for x in b15["slots"]["07:00"]] == [em]
    assert [x["mode"] for x in b15["slots"]["07:30"]] == ["ev"]
    day = client.get("/api/transports/docks", params={"date": "2026-09-25"}).json()
    assert em2 in [x["transport_id"] for x in next(d for d in day["docks"] if d["dock"] == "B6 EST")["slots"]["09:00"]]

    # ---- an order ignored in the supply table leaves its truck ; an empty truck is cancelled by the application (not on the board)
    t2 = next(t for t in _planning(client)["transports"] if t["transport_id"] == em2)
    assert [o["expected_date"] for o in t2["orders"]] == ["2026-09-24"] * len(t2["orders"]) and t2["date"] == "2026-09-25"
    for o in t2["orders"]:
        assert client.post("/api/entries/flags/toggle", json={"article_id": o["article_id"], "supplier_id": "S-000517", "date": "2026-09-24", "kind": "order_ignored"}).status_code == 200
    after = _planning(client)
    assert not [t for t in after["transports"] if t["supplier_id"] == "S-000517" and t["mode"] != "ev"]       # ignored : no truck is born either
    gone = next(t for t in client.get("/api/transports", params={"supplier_id": "S-000517", "include_cancelled": True}).json() if t["transport_id"] == em2)
    assert gone["status"] == "cancelled" and gone["cancelled_by"] == "application"
    assert em2 not in {t["transport_id"] for t in _board(client)["transports"]}

    # ---- the printable planning
    pdf = client.get("/api/transports/planning.pdf")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf" and pdf.content.startswith(b"%PDF")


def test_ownership_mine_and_shuttles(two_planners):
    """A planner acts on the trucks of his suppliers and on the shuttles only ; a manager on everything."""
    c = two_planners
    board = _board(c)
    assert board["user_label"] == "QUENTIN" and board["people"]["martin@example.com"] == "MARTIN"
    trucks = [t for t in board["transports"] if t["mode"] in ("fournisseur", "transporteur")]
    assert trucks and all(t["mine"] for t in trucks)
    for_martin = _board(c, MATE)
    assert for_martin["user_label"] == "MARTIN" and not any(t["mine"] for t in for_martin["transports"])
    em = trucks[0]["transport_id"]
    assert c.post(f"/api/transports/{em}/note", json={"note": "x"}, headers=MATE).status_code == 403
    assert c.post(f"/api/transports/{em}/move", json={"time": "10:00"}, headers=MATE).status_code == 403
    assert c.delete(f"/api/transports/{em}", headers=MATE).status_code == 403
    assert c.post("/api/transports/cancel", json={"transport_ids": [em]}, headers=MATE).status_code == 403
    assert c.post("/api/transports/special", json={"kind": "ev", "supplier_id": trucks[0]["supplier_id"], "date": "2026-09-22", "time": "10:00", "docks": ["B15"]}, headers=MATE).status_code == 403
    # a shuttle belongs to everybody : MARTIN creates one, QUENTIN moves it
    r = c.post("/api/transports", json={"date": "2026-09-22", "time": "08:00", "docks": ["B6 EST"], "lines": [{"article": "Chariots vides", "designation": "retour MAF", "qty": "6 U"}]}, headers=MATE)
    assert r.status_code == 201, r.text
    sh = r.json()["transports"][0]
    assert sh["mine"] is True and sh["created_by"] == "martin@example.com" and sh["mode"] == "maf"
    assert c.post(f"/api/transports/{sh['transport_id']}/move", json={"time": "08:30"}).status_code == 200
    assert next(t for t in _board(c, MATE)["transports"] if t["transport_id"] == sh["transport_id"])["mine"] is True
    # a manager owns everything
    assert c.put("/api/reference/ref_planners/rows", json={"values": {"planner_id": "PROC2", "name": "MARTIN", "email": "martin@example.com", "role": "manager", "active": True}}, headers=ROOT).status_code == 200
    assert c.post(f"/api/transports/{em}/note", json={"note": "x"}, headers=MATE).status_code == 200


def test_reconcile_runs_once_per_version_and_board_counters(client):  # noqa: F811
    first = _planning(client)
    n = len([t for t in first["transports"] if t["supplier_id"] == "S-000032"])
    assert n == 5
    # the global CSV lists the automatic trucks (today and later)
    csv = client.get("/api/transports/atr.csv").text
    assert csv.count("S-000032") == 5
    # the reconciliation is journalised once
    log = [a for a in client.get("/api/entries/audit", params={"limit": 500}).json() if a["entity_type"] == "transports"]
    assert len(log) == 1 and log[0]["payload"]["created"] >= 5 and log[0]["user"] == "application" and "moved" not in log[0]["payload"]
    assert dt.date.fromisoformat(AS_OF).isoweekday() == 6
    # the board : two weeks from the Monday of the current week, the MAF counter on the next working day
    board = _board(client)
    assert board["start"] == "2026-09-14" and board["end"] == "2026-09-25" and board["day_slots"][0] == "06:00" and board["hard_max"] == 4
    assert board["maf_day"] == "2026-09-21" and board["maf_pending"] == 0
    # S-000032 leaves for the MAF : its trucks stay (never touched by hand… the reconciliation drops them : no more a delivering supplier), its orders of Monday wait for a shuttle
    assert _set(client, "S-000032", transport_mode="MAF Hambach").status_code in (200, 201)
    board = _board(client)
    monday = [o for s in _planning(client, "2026-09-21", 1, scope="maf")["suppliers"] for o in s["orders"] if o["expected_date"] == "2026-09-21" and not o["ignored"]]
    assert board["maf_pending"] == len(monday) > 0
    r = client.post("/api/transports", json={"date": "2026-09-21", "time": "08:00", "docks": ["B6 EST"], "order_ids": [o["order_id"] for o in monday]})
    assert r.status_code == 201, r.text
    assert _board(client)["maf_pending"] == 0
