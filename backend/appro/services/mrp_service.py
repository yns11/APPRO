"""Assemble the dataset (reference + ERP facts + planner cells + parameters + active PDP) and run the engine."""
from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import logging
import time
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..data.assembler import erp_dataset
from ..data.store import (
    AppAdjustment,
    AppCellFlag,
    AppPlanCell,
    ParamOverride,
    PdpLine,
    PdpVersion,
    ProgramScrap,
    audit,
)
from ..engine import run_mrp
from ..engine.calendar import iso_week_label, iso_week_monday
from ..engine.models import AdjustCell, CellFlag, Dataset, EngineParams, MrpResult, PlanCell
from ..engine.models import PdpLine as EnginePdpLine
from ..engine.runner import resolve_as_of, resolve_init_date
from .context import AppContext
from .expression import evaluate

log = logging.getLogger(__name__)

# stock-policy parameters that may be customised per ISO week (scope ``article_week``, key2 = "2026-W40")
ARTICLE_WEEK_FIELDS = {"coverage_target_days": int, "alert_red_days": int, "alert_yellow_days": int, "overstock_days": int}
GLOBAL_FIELDS = {f.name: f.type for f in dataclasses.fields(EngineParams)}
#: the rules a user may change from the Paramètres screen (the others are display or technical)
EDITABLE_GLOBAL_FIELDS = ("target_tolerance_pct", "lead_margin_days")


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
    return value if default is None else type(default)(value)


def purge_orphan_overrides(session: Session) -> int:
    """Delete the overrides of rules that no longer exist (an earlier version of the application) ;
    returns the number of rows removed.  Called at start-up."""
    rows = session.scalars(select(ParamOverride)).all()
    n = 0
    for r in rows:
        allowed = GLOBAL_FIELDS if r.scope == "global" else ARTICLE_WEEK_FIELDS if r.scope == "article_week" else {}
        if r.field not in allowed:
            session.delete(r)
            n += 1
    if n:
        session.commit()
    return n


def build_params(ctx: AppContext, session: Session, **requested: Any) -> EngineParams:
    """Defaults < settings < global overrides (DB) < request parameters."""
    s = ctx.settings
    base: dict[str, Any] = {
        "horizon_days": s.horizon_days, "history_weeks": s.history_weeks,
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


def first_full_monday(init_date: dt.date, this_monday: dt.date | None = None) -> dt.date:
    """First complete ISO week after the point zero : the week of ``init_date`` when it is a Monday, else
    the next one ; never after the current week (a point zero set this week starts the sheet on the
    current week).  The PDP sheet and the past weeks of the effective plan start there."""
    monday = iso_week_monday(init_date)
    first = monday if monday == init_date else monday + dt.timedelta(days=7)
    return min(first, this_monday) if this_monday else first


def plan_versions_by_week(session: Session, mondays: list[dt.date]) -> list[dict[str, Any]]:
    """For each past week, the imported PDP version that stands for « the plan of that week » :

    * ``monday`` : the **latest version imported before the Monday** of the week **that holds a line
      for that week** (what the CBN saw on that Monday) ;
    * ``later`` : no version imported before the Monday holds the week (the application was not yet in
      use, or every earlier version stopped before it) → the **first version imported afterwards** that
      holds the week is taken instead (flagged : it was not known on the Monday) ;
    * ``none`` : no imported version holds the week.
    The versions are matched on their content, never on their active flag : an old version stays
    the plan of its weeks after a newer import."""
    if not mondays:
        return []
    weeks = sorted(set(mondays))
    rows = session.execute(
        select(PdpLine.week_start, PdpVersion.id, PdpVersion.name, PdpVersion.imported_at)
        .join(PdpVersion, PdpVersion.id == PdpLine.version_id)
        .where(PdpLine.week_start >= weeks[0], PdpLine.week_start <= weeks[-1])
        .group_by(PdpLine.week_start, PdpVersion.id, PdpVersion.name, PdpVersion.imported_at)).all()
    by_week: dict[dt.date, list[tuple[dt.datetime, str, str]]] = defaultdict(list)
    for week_start, vid, name, imported_at in rows:
        by_week[week_start].append((imported_at, vid, name))
    out = []
    for monday in weeks:
        cutoff = dt.datetime.combine(monday, dt.time.min)
        cands = sorted(by_week.get(monday, []))
        before = [c for c in cands if c[0] < cutoff]
        after = [c for c in cands if c[0] >= cutoff]
        if before:
            imported_at, vid, name = before[-1]
            origin = "monday"
        elif after:
            imported_at, vid, name = after[0]
            origin = "later"
        else:
            imported_at, vid, name, origin = None, None, None, "none"
        out.append({"week": iso_week_label(monday), "week_start": monday.isoformat(), "version_id": vid, "version_name": name,
                    "imported_at": imported_at.isoformat(timespec="minutes") if imported_at else None, "origin": origin})
    return out


def effective_pdp(session: Session, programs: set[str], as_of: dt.date, init_date: dt.date) -> tuple[list[EnginePdpLine], dict[str, Any]]:
    """The one production plan every screen and the engine use (docs/regles_metier.md § 11 bis) :

    * current week and the weeks after it : the **active version** ;
    * past weeks, from the first complete week after the point zero : the active version when it holds
      the week ; otherwise the version **known at the Monday** of the week (:func:`plan_versions_by_week`),
      else the first version imported afterwards that holds it ; else nothing.
    Returns the lines (programmes with a bill of material only) and a meta dict : the active version and,
    per past week, the version the plan comes from."""
    this_monday = iso_week_monday(as_of)
    active = session.scalars(select(PdpVersion).where(PdpVersion.active.is_(True))).first()
    lines: list[EnginePdpLine] = []
    active_weeks: set[dt.date] = set()
    if active:
        for l in session.scalars(select(PdpLine).where(PdpLine.version_id == active.id)).all():
            if l.program_id in programs:
                lines.append(EnginePdpLine(l.program_id, l.week_start, l.qty, version=f"APP:{active.id}"))
            active_weeks.add(l.week_start)
    first = first_full_monday(init_date, this_monday)
    past = [first + dt.timedelta(days=7 * k) for k in range((this_monday - first).days // 7)] if this_monday > first else []
    week_origin: dict[str, dict[str, Any]] = {}
    missing = [m for m in past if m not in active_weeks]
    fallback = {dt.date.fromisoformat(v["week_start"]): v for v in plan_versions_by_week(session, missing)}
    for m in past:
        if m in active_weeks:
            week_origin[m.isoformat()] = {"origin": "active", "version_id": active.id if active else None, "version_name": active.name if active else None}
            continue
        v = fallback.get(m)
        week_origin[m.isoformat()] = {"origin": v["origin"] if v else "none", "version_id": v["version_id"] if v else None,
                                      "version_name": v["version_name"] if v else None}
        if v and v["version_id"]:
            for l in session.scalars(select(PdpLine).where(PdpLine.version_id == v["version_id"], PdpLine.week_start == m)).all():
                if l.program_id in programs:
                    lines.append(EnginePdpLine(l.program_id, l.week_start, l.qty, version=f"APP:{v['version_id']}"))
    meta = {"pdp_version": {"id": active.id, "name": active.name} if active else None, "pdp_first_week": first.isoformat(),
            "pdp_week_origin": week_origin}
    return lines, meta


def app_entries_into_dataset(ds: Dataset, session: Session, params: EngineParams | None = None) -> None:
    """Merge the planner cells and the effective production plan into the dataset (``params`` gives the
    simulated « today » ; the real date otherwise)."""
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
    as_of = resolve_as_of(ds, params) if params is not None else dt.date.today()
    init_date = resolve_init_date(ds, as_of)
    ds.pdp, meta = effective_pdp(session, programs, as_of, init_date)
    ds.meta.update(meta)
    rates = scrap_rates(session)
    for p in ds.pdp:
        p.scrap_pct = scrap_rate_at(rates, p.program_id, p.week_start)


def scrap_rates(session: Session) -> dict[str, list[tuple[dt.date, float]]]:
    """Dated programme scrap rates, per programme, in week order."""
    out: dict[str, list[tuple[dt.date, float]]] = {}
    for r in session.scalars(select(ProgramScrap).order_by(ProgramScrap.program_id, ProgramScrap.week_start)):
        out.setdefault(r.program_id, []).append((r.week_start, float(r.rate_pct or 0.0)))
    return out


def scrap_rate_at(rates: dict[str, list[tuple[dt.date, float]]], program_id: str, week_start: dt.date) -> float:
    """Rate in force for one week : the latest row dated on or before its Monday ; 0 before the first row."""
    rate = 0.0
    for start, value in rates.get(program_id, ()):
        if start <= week_start:
            rate = value
        else:
            break
    return rate


def _cache_key(version: int, planner: str | None, article_ids: list[str] | None, params: EngineParams) -> str:
    # « today » is part of the result: a result computed yesterday must not survive midnight
    payload = {"v": version, "today": (params.as_of or dt.date.today()).isoformat(), "planner": planner, "articles": sorted(article_ids or []),
               "params": dataclasses.asdict(params)}
    return hashlib.sha1(json.dumps(payload, default=str, sort_keys=True).encode()).hexdigest()


def compute(ctx: AppContext, session: Session, planner: str | None = None, article_ids: list[str] | None = None,
            **param_overrides: Any) -> MrpResult:
    """Run (or fetch from cache) the MRP for the given perimeter / parameters."""
    params = build_params(ctx, session, **{k: v for k, v in param_overrides.items() if v is not None})
    key = _cache_key(ctx.sync_version(session), planner, article_ids, params)
    cached = ctx.cache_get(key)
    if cached is not None:
        return cached
    t0 = time.perf_counter()
    ds = erp_dataset(ctx.table, planner=planner, article_ids=article_ids, holidays=ctx.settings.holiday_dates)
    app_entries_into_dataset(ds, session, params)
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
