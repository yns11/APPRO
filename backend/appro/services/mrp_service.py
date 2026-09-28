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
    AppPlanLine,
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
    Receipt,
    SimCell,
)
from ..engine.models import PdpLine as EnginePdpLine
from ..engine.scenario import ScenarioEvent, apply_scenario
from .context import AppContext
from .expression import evaluate

log = logging.getLogger(__name__)

ARTICLE_FIELDS = {"coverage_target_days": int, "alert_red_days": int, "alert_yellow_days": int,
                  "overstock_days": int, "safety_stock_qty": float, "lot_policy": str, "order_cycle_days": int,
                  "fixed_lot_qty": float, "active": lambda v: str(v).lower() in ("true", "1", "oui")}
# stock-policy parameters that may be customised per ISO week (scope ``article_week``, key2 = "2026-W40")
ARTICLE_WEEK_FIELDS = {"coverage_target_days": int, "alert_red_days": int, "alert_yellow_days": int,
                       "overstock_days": int, "safety_stock_qty": float, "order_cycle_days": int}
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
            elif o.scope == "article_week" and o.key1 in arts and o.field in ARTICLE_WEEK_FIELDS:
                arts[o.key1].weekly.setdefault(o.key2, {})[o.field] = ARTICLE_WEEK_FIELDS[o.field](o.value)
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


LEGACY_VALUES = {"late_order_policy": {"ignore": "exclude", "keep": "reschedule"}}


def _coerce_param(field: str, value: Any) -> Any:
    default = getattr(EngineParams(), field)
    if field in LEGACY_VALUES and isinstance(value, str):
        value = LEGACY_VALUES[field].get(value, value)
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
        if c.article_id in ids and c.kind == "adjustment":
            ds.cells.append(SimCell(c.article_id, c.date, "adjustment", c.qty, c.source, c.note))
    for l in session.scalars(select(AppPlanLine)):
        if l.article_id in ids:
            ds.plan.append(PlanLine(l.id, l.article_id, l.date, l.qty, l.order_id or None, l.supplier_id, l.source, l.note))
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
        ds.pdp = [p for p in ds.pdp if p.program_id not in by_program]
        for pid, ls in by_program.items():
            ds.pdp.extend(EnginePdpLine(pid, l.week_start, l.qty, version=f"APP:{active.id}") for l in ls)
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
# Grid cells (adjustments) and delivery plan lines
# =============================================================================
def upsert_cell(ctx: AppContext, session: Session, user: str, article_id: str, date: dt.date, kind: str,
                expression: str, source: str = "MANUAL", note: str = "") -> AppCell | None:
    """Create / update / delete the adjustment cell (article, date) from a quantity or an expression.

    A blank expression (or 0) deletes the cell.  Any date is accepted (a past date corrects the
    reference stock).  Returns the row, or None when the cell was deleted.  Does not commit.
    """
    if kind != "adjustment":
        raise ValueError("seuls les ajustements se saisissent en cellule ; le plan se saisit par ligne")
    blank = not expression.strip()
    qty = 0.0 if blank else evaluate(expression)
    rows = session.scalars(select(AppCell).where(AppCell.article_id == article_id, AppCell.date == date,
                                                 AppCell.kind == kind)).all()
    row = rows[0] if rows else None
    for other in rows[1:]:
        session.delete(other)
    if blank or abs(qty) < 1e-9:
        if row is not None:
            audit(session, user, "delete", "cell", row.id, article_id, {"date": date.isoformat(), "kind": kind})
            session.delete(row)
        return None
    if row is None:
        row = AppCell(article_id=article_id, date=date, kind=kind)
        session.add(row)
    row.expression, row.qty, row.source, row.note, row.updated_by = expression.strip(), float(qty), source, note, user
    audit(session, user, "upsert", "cell", row.id, article_id,
          {"date": date.isoformat(), "kind": kind, "expression": row.expression, "qty": row.qty, "source": source})
    return row


def save_plan_line(session: Session, user: str, article_id: str, date: dt.date, qty: float, order_id: str | None = None,
                   supplier_id: str | None = None, note: str = "", line_id: str | None = None, source: str = "MANUAL",
                   erp: dict[str, Any] | None = None) -> AppPlanLine:
    """Create or update one plan line.  Quantity 0 is allowed only for an order override (nothing
    expected from that order) ; a free line with quantity 0 is deleted.  Does not commit."""
    if qty < 0:
        raise ValueError("la quantité d'une ligne du plan ne peut pas être négative")
    row = session.get(AppPlanLine, line_id) if line_id else None
    if line_id and row is None:
        raise KeyError(line_id)
    if row is None:
        if not order_id and abs(qty) < 1e-9:
            raise ValueError("une ligne libre doit avoir une quantité")
        row = AppPlanLine(article_id=article_id, created_by=user)
        session.add(row)
    row.article_id, row.date, row.qty, row.order_id = article_id, date, float(qty), (order_id or None)
    row.supplier_id, row.note, row.updated_by, row.source = supplier_id, note, user, source
    if erp is not None:
        row.erp_json = json.dumps(erp, default=str)
    audit(session, user, "upsert", "plan_line", row.id, article_id,
          {"date": date.isoformat(), "qty": float(qty), "order_id": order_id, "source": source, "note": note})
    if not order_id and abs(qty) < 1e-9:
        session.delete(row)
    return row


def delete_plan_line(session: Session, user: str, row: AppPlanLine) -> None:
    audit(session, user, "delete", "plan_line", row.id, row.article_id, {"date": row.date.isoformat(), "qty": row.qty,
                                                                          "order_id": row.order_id})
    session.delete(row)


def set_plan_cell(ctx: AppContext, session: Session, user: str, article_id: str, date: dt.date, expression: str
                  ) -> AppPlanLine | None:
    """Set the planned quantity of one day from the grid cell.

    * no line that day → a free line is created (blank / 0 → nothing) ;
    * exactly one line (stored, or an ERP order taken as is) → its quantity is set (an ERP order
      gets an override line ; blank restores the ERP quantity, 0 means nothing expected) ;
    * several lines → refused: the list of the day must be used.
    Does not commit.
    """
    result = compute(ctx, session, article_ids=[article_id])
    ar = result.articles.get(article_id)
    if ar is None:
        raise KeyError(article_id)
    lines = [l for l in ar.plan_lines if l.date == date and l.origin in ("erp", "override", "free")]
    blank = not expression.strip()
    qty = None if blank else float(evaluate(expression))
    if len(lines) > 1:
        raise ValueError("plusieurs lignes du plan ce jour-là : modifier la ligne voulue dans la liste")
    if not lines:
        if qty is None or abs(qty) < 1e-9:
            return None
        return save_plan_line(session, user, article_id, date, qty, None,
                              next((l.supplier_id for l in ar.suppliers), None))
    line = lines[0]
    if line.origin == "erp":
        if qty is None:
            return None
        erp = {"expected_date": line.erp_date.isoformat() if line.erp_date else None, "qty_open": line.erp_qty}
        return save_plan_line(session, user, article_id, date, qty, line.order_id, line.supplier_id, erp=erp)
    row = session.get(AppPlanLine, line.line_id)
    if row is None:
        raise KeyError(line.line_id)
    if qty is None:
        delete_plan_line(session, user, row)
        return None
    return save_plan_line(session, user, article_id, date, qty, row.order_id, row.supplier_id, row.note, row.id, row.source)
