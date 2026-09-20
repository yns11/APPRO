"""What-if scenarios: typed events applied on a *copy* of the dataset before the run.

Supported event kinds (``ScenarioEvent.kind`` → payload keys):

* ``add_order``        article_id, supplier_id?, date, qty, order_type? (PLANNED)
* ``move_order``       order_id, days? (±) or new_date
* ``change_order_qty`` order_id, qty
* ``cancel_order``     order_id
* ``plan_factor``      factor (e.g. 1.2), program_id?, from?, to?   – scale the weekly plan
* ``set_plan``         program_id, week_start, qty
* ``set_actual``       program_id, date, qty
* ``add_movement``     article_id, date, qty, comment?
* ``set_article_param`` article_id, field, value  (e.g. coverage_target_days)
* ``set_link_param``   article_id, supplier_id, field, value  (e.g. lead_time_days, moq)
"""
from __future__ import annotations

import copy
import datetime as dt
from dataclasses import dataclass, field
from typing import Any

from .models import ActualLine, Dataset, Movement, OrderLine, OrderStatus, OrderType, PlanLine


@dataclass
class ScenarioEvent:
    kind: str
    payload: dict[str, Any] = field(default_factory=dict)


def _date(v: Any) -> dt.date:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v))


def apply_scenario(dataset: Dataset, events: list[ScenarioEvent]) -> tuple[Dataset, list[str]]:
    """Return a new dataset with the events applied, plus human-readable diagnostics."""
    ds = copy.deepcopy(dataset)
    notes: list[str] = []
    orders_by_id = {o.order_id: o for o in ds.orders}
    for n, ev in enumerate(events, start=1):
        p = ev.payload
        try:
            if ev.kind == "add_order":
                oid = p.get("order_id") or f"SC-{n:03d}"
                ds.orders.append(OrderLine(
                    order_id=oid, article_id=p["article_id"], supplier_id=p.get("supplier_id"),
                    expected_date=_date(p["date"]), qty_ordered=float(p["qty"]),
                    order_type=OrderType(p.get("order_type", "PLANNED")), status=OrderStatus.OPEN,
                    source="SCENARIO", note=p.get("note", "scénario"),
                ))
                orders_by_id[oid] = ds.orders[-1]
            elif ev.kind == "move_order":
                o = orders_by_id[p["order_id"]]
                if "new_date" in p and p["new_date"]:
                    o.expected_date = _date(p["new_date"])
                else:
                    o.expected_date = o.expected_date + dt.timedelta(days=int(p.get("days", 0)))
                o.source = "SCENARIO"
            elif ev.kind == "change_order_qty":
                o = orders_by_id[p["order_id"]]
                o.qty_ordered = float(p["qty"])
                o.source = "SCENARIO"
            elif ev.kind == "cancel_order":
                o = orders_by_id[p["order_id"]]
                o.status = OrderStatus.CANCELLED
                o.source = "SCENARIO"
            elif ev.kind == "plan_factor":
                factor = float(p["factor"])
                pid = p.get("program_id")
                d_from = _date(p["from"]) if p.get("from") else None
                d_to = _date(p["to"]) if p.get("to") else None
                for line in ds.plan:
                    if pid and line.program_id != pid:
                        continue
                    if d_from and line.week_start + dt.timedelta(days=6) < d_from:
                        continue
                    if d_to and line.week_start > d_to:
                        continue
                    line.qty = line.qty * factor
            elif ev.kind == "set_plan":
                ws = _date(p["week_start"])
                found = False
                for line in ds.plan:
                    if line.program_id == p["program_id"] and line.week_start == ws:
                        line.qty = float(p["qty"])
                        found = True
                if not found:
                    ds.plan.append(PlanLine(p["program_id"], ws, float(p["qty"]), version="SCENARIO"))
            elif ev.kind == "set_actual":
                d = _date(p["date"])
                ds.actuals = [a for a in ds.actuals if not (a.program_id == p["program_id"] and a.date == d)]
                ds.actuals.append(ActualLine(p["program_id"], d, float(p["qty"])))
            elif ev.kind == "add_movement":
                ds.movements.append(Movement(
                    movement_id=f"SC-MV-{n:03d}", article_id=p["article_id"], date=_date(p["date"]),
                    qty=float(p["qty"]), movement_type=p.get("movement_type", "SCENARIO"), source="SCENARIO",
                    comment=p.get("comment", "scénario")))
            elif ev.kind == "set_article_param":
                for a in ds.articles:
                    if a.article_id == p["article_id"]:
                        setattr(a, p["field"], type(getattr(a, p["field"]))(p["value"]))
            elif ev.kind == "set_link_param":
                for l in ds.links:
                    if l.article_id == p["article_id"] and l.supplier_id == p.get("supplier_id", l.supplier_id):
                        setattr(l, p["field"], type(getattr(l, p["field"]))(p["value"]))
            else:
                notes.append(f"événement #{n} ignoré : type inconnu {ev.kind!r}")
        except KeyError as exc:
            notes.append(f"événement #{n} ({ev.kind}) ignoré : clé manquante {exc}")
        except (TypeError, ValueError) as exc:
            notes.append(f"événement #{n} ({ev.kind}) ignoré : {exc}")
    return ds, notes
