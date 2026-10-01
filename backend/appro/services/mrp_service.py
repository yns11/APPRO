"""Assemble the dataset (reference + ERP facts + planner cells + parameters + active PDP) and run the engine."""
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
from ..data.store import AppAdjustment, AppCellFlag, AppPlanCell, ParamOverride, PdpLine, PdpVersion, audit
from ..engine import run_mrp
from ..engine.models import AdjustCell, CellFlag, Dataset, EngineParams, MrpResult, PlanCell
from ..engine.models import PdpLine as EnginePdpLine
from .context import AppContext
from .expression import evaluate

log = logging.getLogger(__name__)

# stock-policy parameters that may be customised per ISO week (scope ``article_week``, key2 = "2026-W40")
ARTICLE_WEEK_FIELDS = {"coverage_target_days": int, "alert_red_days": int, "alert_yellow_days": int,
                       "overstock_days": int, "safety_stock_qty": float, "order_cycle_days": int}
GLOBAL_FIELDS = {f.name: f.type for f in dataclasses.fields(EngineParams)}


def apply_weekly_overrides(ds: Dataset, overrides: list[ParamOverride]) -> list[str]:
    notes = []
    arts = {a.article_id: a for a in ds.articles}
    for o in overrides:
        if o.scope != "article_week" or o.key1 not in arts or o.field not in ARTICLE_WEEK_FIELDS:
            continue
        try:
            arts[o.key1].weekly.setdefault(o.key2, {})[o.field] = ARTICLE_WEEK_FIELDS[o.field](o.value)
        except (TypeError, ValueError) as exc:
            notes.append(f"paramètre {o.key1}/{o.key2}/{o.field}={o.value!r} ignoré : {exc}")
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
    """Merge the planner cells and the active PDP version into the dataset."""
    ids = {a.article_id for a in ds.articles}
    for c in session.scalars(select(AppAdjustment)):
        if c.article_id in ids:
            ds.adjustments.append(AdjustCell(c.article_id, c.date, c.qty, c.note))
    for c in session.scalars(select(AppPlanCell)):
        if c.article_id in ids:
            ds.plan.append(PlanCell(c.article_id, c.supplier_id or None, c.date, c.qty, c.note))
    for f in session.scalars(select(AppCellFlag)):
        if f.article_id in ids:
            ds.flags.append(CellFlag(f.article_id, f.supplier_id or None, f.date, f.kind, f.qty))
    programs = {b.program_id for b in ds.bom}
    active = session.scalars(select(PdpVersion).where(PdpVersion.active.is_(True))).first()
    if active:
        lines = session.scalars(select(PdpLine).where(PdpLine.version_id == active.id)).all()
        by_program: dict[str, list] = {}
        for l in lines:
            if l.program_id in programs:
                by_program.setdefault(l.program_id, []).append(l)
        # an imported PDP replaces the ERP plan for the programs it contains
        ds.pdp = [p for p in ds.pdp if p.program_id not in by_program]
        for pid, ls in by_program.items():
            ds.pdp.extend(EnginePdpLine(pid, l.week_start, l.qty, version=f"APP:{active.id}") for l in ls)
        ds.meta["pdp_version"] = {"id": active.id, "name": active.name}


def _cache_key(ctx: AppContext, planner: str | None, article_ids: list[str] | None, params: EngineParams) -> str:
    payload = {"v": ctx.data_version, "planner": planner, "articles": sorted(article_ids or []),
               "params": dataclasses.asdict(params)}
    return hashlib.sha1(json.dumps(payload, default=str, sort_keys=True).encode()).hexdigest()


def compute(ctx: AppContext, session: Session, planner: str | None = None, article_ids: list[str] | None = None,
            **param_overrides: Any) -> MrpResult:
    """Run (or fetch from cache) the MRP for the given perimeter / parameters."""
    params = build_params(ctx, session, **{k: v for k, v in param_overrides.items() if v is not None})
    key = _cache_key(ctx, planner, article_ids, params)
    cached = ctx.cache_get(key)
    if cached is not None:
        return cached
    t0 = time.perf_counter()
    ds = erp_dataset(ctx.table, planner=planner, article_ids=article_ids, holidays=ctx.settings.holiday_dates)
    app_entries_into_dataset(ds, session)
    notes = apply_weekly_overrides(ds, session.scalars(select(ParamOverride).where(ParamOverride.scope == "article_week")).all())
    result = run_mrp(ds, params)
    result.diagnostics.extend(notes)
    result.diagnostics.append(f"calcul {len(result.articles)} articles en {1000 * (time.perf_counter() - t0):.0f} ms")
    ctx.cache_set(key, result)
    return result


# =============================================================================
# The two editable rows: adjustment cells and plan cells
# =============================================================================
def set_adjustment(session: Session, user: str, article_id: str, date: dt.date, expression: str | None,
                   note: str | None = None) -> AppAdjustment | None:
    """Create / update / delete the adjustment cell (article, day).

    ``expression`` None keeps the quantity (note-only change) ; blank or 0 deletes the cell.  Any
    date is accepted (a past date corrects the reference stock).  Does not commit."""
    row = session.scalars(select(AppAdjustment).where(AppAdjustment.article_id == article_id,
                                                      AppAdjustment.date == date)).first()
    if expression is None:
        if row is None:
            return None
        row.note, row.updated_by = note or "", user
        audit(session, user, "note", "adjustment", row.id, article_id, {"date": date.isoformat(), "note": row.note})
        return row
    blank = not expression.strip()
    qty = 0.0 if blank else evaluate(expression)
    if blank or abs(qty) < 1e-9:
        if row is not None:
            audit(session, user, "delete", "adjustment", row.id, article_id, {"date": date.isoformat()})
            session.delete(row)
        return None
    if row is None:
        row = AppAdjustment(article_id=article_id, date=date, qty=0.0)
        session.add(row)
    row.expression, row.qty, row.updated_by = expression.strip(), float(qty), user
    if note is not None:
        row.note = note
    audit(session, user, "upsert", "adjustment", row.id, article_id,
          {"date": date.isoformat(), "expression": row.expression, "qty": row.qty, "note": row.note})
    return row


def set_plan_cell(session: Session, user: str, article_id: str, supplier_id: str | None, date: dt.date,
                  expression: str | None, note: str | None = None) -> AppPlanCell | None:
    """Create / update / delete the plan cell (article, supplier, day).

    A typed quantity (0 included) is the planner's decision for that day ; blank removes the cell
    (back to the ERP) ; ``expression`` None keeps the quantity (note-only change).  Does not commit."""
    sid = supplier_id or ""
    row = session.scalars(select(AppPlanCell).where(AppPlanCell.article_id == article_id, AppPlanCell.supplier_id == sid,
                                                    AppPlanCell.date == date)).first()
    if expression is None:
        if row is None:
            return None
        row.note, row.updated_by = note or "", user
        audit(session, user, "note", "plan_cell", row.id, article_id, {"date": date.isoformat(), "supplier_id": sid, "note": row.note})
        return row
    if not expression.strip():
        if row is not None:
            audit(session, user, "delete", "plan_cell", row.id, article_id, {"date": date.isoformat(), "supplier_id": sid})
            session.delete(row)
        return None
    qty = float(evaluate(expression))
    if qty < 0:
        raise ValueError("une quantité planifiée ne peut pas être négative")
    if row is None:
        row = AppPlanCell(article_id=article_id, supplier_id=sid, date=date, qty=0.0)
        session.add(row)
    row.expression, row.qty, row.updated_by = expression.strip(), qty, user
    if note is not None:
        row.note = note
    audit(session, user, "upsert", "plan_cell", row.id, article_id,
          {"date": date.isoformat(), "supplier_id": sid, "expression": row.expression, "qty": qty, "note": row.note})
    return row


def toggle_flag(session: Session, user: str, article_id: str, supplier_id: str | None, date: dt.date, kind: str,
                qty: float = 0.0, note: str | None = None) -> AppCellFlag | None:
    """Set the flag when absent, remove it when present (one click = toggle).  Does not commit."""
    sid = supplier_id or ""
    row = session.scalars(select(AppCellFlag).where(AppCellFlag.kind == kind, AppCellFlag.article_id == article_id,
                                                    AppCellFlag.supplier_id == sid, AppCellFlag.date == date)).first()
    if row is not None:
        audit(session, user, "delete", kind, row.id, article_id, {"date": date.isoformat(), "supplier_id": sid})
        session.delete(row)
        return None
    row = AppCellFlag(kind=kind, article_id=article_id, supplier_id=sid, date=date, qty=float(qty), note=note or "",
                      updated_by=user)
    session.add(row)
    audit(session, user, "upsert", kind, row.id, article_id, {"date": date.isoformat(), "supplier_id": sid, "qty": qty})
    return row
