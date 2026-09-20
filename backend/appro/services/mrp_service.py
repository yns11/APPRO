"""Assemble the dataset (ERP + app entries + overrides + active PDP version) and run the engine."""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import logging
import time
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..data.assembler import erp_dataset
from ..data.store import (
    AppAdjustment,
    AppCell,
    AppOrder,
    AppProductionActual,
    AppReceipt,
    ParamOverride,
    PdpLine,
    PdpVersion,
    Scenario,
    audit,
)
from ..engine import run_mrp
from ..engine.models import (
    ActualLine,
    Dataset,
    EngineParams,
    Movement,
    MrpResult,
    OrderLine,
    OrderStatus,
    OrderType,
    PlanLine,
    Proposal,
    Receipt,
    SimCell,
)
from ..engine.scenario import ScenarioEvent, apply_scenario
from .context import AppContext
from .expression import evaluate

log = logging.getLogger(__name__)

ARTICLE_FIELDS = {"coverage_target_days": int, "alert_red_days": int, "alert_yellow_days": int,
                  "overstock_days": int, "safety_stock_qty": float, "lot_policy": str, "order_cycle_days": int,
                  "fixed_lot_qty": float, "active": lambda v: str(v).lower() in ("true", "1", "oui")}
LINK_FIELDS = {"moq": float, "pack_qty": float, "lead_time_days": int, "quota_pct": float, "priority": int,
               "active": lambda v: str(v).lower() in ("true", "1", "oui")}
GLOBAL_FIELDS = {f.name: f.type for f in dataclasses.fields(EngineParams)}


def apply_overrides(ds: Dataset, overrides: list[ParamOverride]) -> list[str]:
    notes = []
    arts = {a.article_id: a for a in ds.articles}
    links = {(l.article_id, l.supplier_id): l for l in ds.links}
    for o in overrides:
        try:
            if o.scope == "article" and o.key1 in arts and o.field in ARTICLE_FIELDS:
                setattr(arts[o.key1], o.field, ARTICLE_FIELDS[o.field](o.value))
            elif o.scope == "link" and (o.key1, o.key2) in links and o.field in LINK_FIELDS:
                setattr(links[(o.key1, o.key2)], o.field, LINK_FIELDS[o.field](o.value))
        except (TypeError, ValueError) as exc:
            notes.append(f"override {o.scope}/{o.key1}/{o.field}={o.value!r} ignoré : {exc}")
    return notes


def global_param_overrides(session: Session) -> dict[str, Any]:
    rows = session.scalars(select(ParamOverride).where(ParamOverride.scope == "global")).all()
    out: dict[str, Any] = {}
    for r in rows:
        if r.field in GLOBAL_FIELDS:
            out[r.field] = _coerce_param(r.field, r.value)
    return out


def _coerce_param(field: str, value: Any) -> Any:
    default = getattr(EngineParams(), field)
    if isinstance(default, bool):
        return str(value).lower() in ("true", "1", "oui", "yes")
    if isinstance(default, int):
        return int(value)
    if isinstance(default, tuple):
        if isinstance(value, str):
            return tuple(x.strip() for x in value.split(",") if x.strip())
        return tuple(value)
    if field == "as_of" and value:
        return dt.date.fromisoformat(str(value)) if not isinstance(value, dt.date) else value
    if default is None and field in ("proposal_lookahead_days", "stockout_lookahead_days"):
        return None if value in (None, "", "null") else int(value)
    return value if default is None else type(default)(value)


def build_params(ctx: AppContext, session: Session, **requested: Any) -> EngineParams:
    """Defaults < settings < global overrides (DB) < request parameters."""
    s = ctx.settings
    base: dict[str, Any] = {
        "horizon_days": s.horizon_days, "history_days": s.history_days,
        "working_weekdays": tuple(int(x) for x in s.working_weekdays.split(",") if x.strip()),
    }
    if s.as_of:
        base["as_of"] = s.as_of
    base.update(global_param_overrides(session))
    for k, v in requested.items():
        if v is None or k not in GLOBAL_FIELDS:
            continue
        base[k] = _coerce_param(k, v)
    return EngineParams(**base)


def app_entries_into_dataset(ds: Dataset, session: Session) -> None:
    """Merge the planner entries (orders, receipts, adjustments, actuals, active PDP) into the dataset."""
    ids = {a.article_id for a in ds.articles}
    for o in session.scalars(select(AppOrder).where(AppOrder.status.in_(["OPEN", "SENT"]))):
        if o.article_id not in ids:
            continue
        ds.orders.append(OrderLine(
            order_id=o.id, article_id=o.article_id, supplier_id=o.supplier_id, expected_date=o.expected_date,
            qty_ordered=o.qty, qty_received=0.0,
            order_type=OrderType.FIRM if (o.order_type == "FIRM" or o.status == "SENT") else OrderType.PLANNED,
            status=OrderStatus.OPEN, source="APP", note=o.note))
    for r in session.scalars(select(AppReceipt)):
        if r.article_id in ids:
            ds.receipts.append(Receipt(r.id, r.article_id, r.receipt_date, r.qty, r.supplier_id, r.order_id, "APP"))
    for a in session.scalars(select(AppAdjustment)):
        if a.article_id in ids:
            ds.movements.append(Movement(a.id, a.article_id, a.date, a.qty, a.movement_type, "APP", a.comment))
    for c in session.scalars(select(AppCell)):
        if c.article_id in ids:
            ds.cells.append(SimCell(c.article_id, c.date, c.kind, c.qty, c.source, c.note))
    programs = {b.program_id for b in ds.bom}
    app_actuals = {(a.program_id, a.date): a.qty for a in session.scalars(select(AppProductionActual))
                   if a.program_id in programs}
    if app_actuals:
        ds.actuals = [a for a in ds.actuals if (a.program_id, a.date) not in app_actuals]
        ds.actuals.extend(ActualLine(p, d, q) for (p, d), q in app_actuals.items())
    active = session.scalars(select(PdpVersion).where(PdpVersion.active.is_(True))).first()
    if active:
        lines = session.scalars(select(PdpLine).where(PdpLine.version_id == active.id)).all()
        by_program = {}
        for l in lines:
            if l.program_id in programs:
                by_program.setdefault(l.program_id, []).append(l)
        # an imported PDP replaces the ERP plan for the programs it contains
        ds.plan = [p for p in ds.plan if p.program_id not in by_program]
        for pid, ls in by_program.items():
            ds.plan.extend(PlanLine(pid, l.week_start, l.qty, version=f"APP:{active.id}") for l in ls)
        ds.meta["pdp_version"] = {"id": active.id, "name": active.name}


def scenario_events(session: Session, scenario_id: str | None) -> tuple[list[ScenarioEvent], dict[str, Any]]:
    if not scenario_id:
        return [], {}
    sc = session.get(Scenario, scenario_id)
    if sc is None:
        raise KeyError(scenario_id)
    return [ScenarioEvent(e.kind, e.payload) for e in sc.events], sc.params


def _cache_key(ctx: AppContext, planner: str | None, article_ids: list[str] | None, params: EngineParams,
               scenario_id: str | None, extra_events: list[ScenarioEvent] | None) -> str:
    payload = {"v": ctx.data_version, "planner": planner, "articles": sorted(article_ids or []),
               "params": dataclasses.asdict(params), "scenario": scenario_id,
               "events": [dataclasses.asdict(e) for e in extra_events or []]}
    return hashlib.sha1(json.dumps(payload, default=str, sort_keys=True).encode()).hexdigest()


def compute(ctx: AppContext, session: Session, planner: str | None = None, article_ids: list[str] | None = None,
            scenario_id: str | None = None, extra_events: list[ScenarioEvent] | None = None,
            **param_overrides: Any) -> MrpResult:
    """Run (or fetch from cache) the MRP for the given perimeter / scenario / parameters."""
    events, sc_params = scenario_events(session, scenario_id)
    merged = dict(sc_params)
    merged.update({k: v for k, v in param_overrides.items() if v is not None})
    params = build_params(ctx, session, **merged)
    key = _cache_key(ctx, planner, article_ids, params, scenario_id, extra_events)
    cached = ctx.cache_get(key)
    if cached is not None:
        return cached
    t0 = time.perf_counter()
    ds = erp_dataset(ctx.source, planner=planner, article_ids=article_ids, holidays=ctx.settings.holiday_dates)
    app_entries_into_dataset(ds, session)
    notes = apply_overrides(ds, session.scalars(select(ParamOverride).where(ParamOverride.scope != "global")).all())
    all_events = events + list(extra_events or [])
    if all_events:
        ds, sc_notes = apply_scenario(ds, all_events)
        notes.extend(sc_notes)
    result = run_mrp(ds, params)
    result.diagnostics.extend(notes)
    result.diagnostics.append(f"calcul {len(result.articles)} articles en {1000 * (time.perf_counter() - t0):.0f} ms")
    ctx.cache_set(key, result)
    return result


# =============================================================================
# Simulation cells and net requirement run (CBN)
# =============================================================================
USER_SOURCES = ("MANUAL", "IMPORT")


def upsert_cell(ctx: AppContext, session: Session, user: str, article_id: str, date: dt.date, kind: str,
                expression: str, source: str = "MANUAL", note: str = "") -> AppCell | None:
    """Create / update / delete the cell (article, date, kind, source) from a quantity or an expression.

    * a *user* source (``MANUAL`` / ``IMPORT``) replaces every other cell of that day and kind: what
      the planner types is the whole simulated quantity of the day;
    * a ``CBN`` cell is kept apart from the typed one (both are counted); a second CBN quantity on
      the same day is added to the existing CBN cell.
    An empty expression (or a zero result) deletes the cell.  Returns the row, or None when the
    cell was deleted.  Does not commit.
    """
    qty = evaluate(expression) if expression.strip() else 0.0
    rows = session.scalars(select(AppCell).where(AppCell.article_id == article_id, AppCell.date == date,
                                                 AppCell.kind == kind)).all()
    row = next((r for r in rows if r.source == source), None)
    if source in USER_SOURCES:
        for other in rows:
            if other is not row:
                audit(session, user, "delete", "cell", other.id, article_id,
                      {"date": date.isoformat(), "kind": kind, "replaced_by": source})
                session.delete(other)
    elif row is not None:  # CBN on a day that already has a CBN cell: add up
        qty = row.qty + qty
        prev = row.expression.strip() if row.expression else f"{row.qty:g}"
        if any(op in prev for op in "+-*/"):
            prev = f"({prev})"
        expression = f"{prev}+{expression}"
        note = f"{row.note} ; {note}".strip(" ;") if row.note else note
    if abs(qty) < 1e-9:
        if row is not None:
            audit(session, user, "delete", "cell", row.id, article_id, {"date": date.isoformat(), "kind": kind})
            session.delete(row)
        return None
    if row is None:
        row = AppCell(article_id=article_id, date=date, kind=kind, source=source)
        session.add(row)
    row.expression, row.qty, row.note, row.updated_by = expression.strip(), float(qty), note, user
    audit(session, user, "upsert", "cell", row.id, article_id,
          {"date": date.isoformat(), "kind": kind, "expression": row.expression, "qty": row.qty, "source": source})
    return row


def run_cbn(ctx: AppContext, session: Session, user: str, planner: str | None = None,
            article_ids: list[str] | None = None, scenario_id: str | None = None, reset: bool = True,
            **param_overrides: Any) -> tuple[MrpResult, list[Proposal], int]:
    """Net requirement run: compute the proposals on the *current* simulated stock (which already
    contains the typed simulated orders) and write them into the simulated-order cells.

    With ``reset`` the cells previously written by a CBN run are removed first, so the run is
    reproducible; typed cells are always kept and respected (a typed cell and a CBN result may
    coexist on the same day).
    Returns the result, the proposals written and the number of cells removed.
    """
    perimeter = erp_dataset(ctx.source, planner=planner, article_ids=article_ids)
    ids = {a.article_id for a in perimeter.articles}
    removed = 0
    if reset:
        for row in session.scalars(select(AppCell).where(AppCell.kind == "sim_order", AppCell.source == "CBN")):
            if row.article_id in ids:
                session.delete(row)
                removed += 1
        session.flush()
        ctx.bump()
    result = compute(ctx, session, planner=planner, article_ids=article_ids, scenario_id=scenario_id,
                     generate_proposals=True, include_proposals_in_simulation=True, **param_overrides)
    written: list[Proposal] = []
    for r in result.articles.values():
        for p in r.proposals:
            note = f"{p.proposal_id} · {p.supplier_id or '?'} · commander le {p.order_date.isoformat()}"
            if p.urgent:
                note += " · URGENT"
            upsert_cell(ctx, session, user, p.article_id, p.delivery_date, "sim_order", f"{p.qty:g}", source="CBN",
                        note=f"{note} · {p.reason}")
            written.append(p)
    audit(session, user, "cbn_run", "cbn", planner or "all", None,
          {"articles": len(result.articles), "proposals": len(written), "removed": removed, "scenario": scenario_id})
    session.commit()
    ctx.bump()
    return result, written, removed
