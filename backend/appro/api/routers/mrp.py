"""Cockpit, article projection, alerts, proposals and ad-hoc simulation endpoints."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...engine.scenario import ScenarioEvent
from ...services import mrp_service
from ...services.context import AppContext
from .. import presenters as P
from .. import schemas as S
from ..deps import ctx_dep, session_dep

router = APIRouter(prefix="/api", tags=["mrp"])

ENGINE_QUERY = dict(
    horizon_days=Query(None, ge=7, le=730), as_of=Query(None), production_mode=Query(None),
    orders_source=Query(None), coverage_unit=Query(None), generate_proposals=Query(None),
    include_proposals_in_simulation=Query(None), respect_lead_time=Query(None), frozen_days=Query(None, ge=0),
    late_order_policy=Query(None), sourcing_policy=Query(None), spread_rounding=Query(None),
)


def _param_kwargs(**kw):
    return {k: v for k, v in kw.items() if v is not None}


def _supplier_names(ctx: AppContext) -> dict[str, str]:
    df = ctx.source.table("ref_suppliers")
    return dict(zip(df["supplier_id"], df["name"]))


def _programs_for(ctx: AppContext, article_id: str, result) -> list[dict]:
    bom = ctx.source.table("ref_bom")
    prg = ctx.source.table("ref_programs")
    names = dict(zip(prg["program_id"], prg["name"]))
    rows = bom[bom["article_id"] == article_id]
    out = []
    for r in rows.to_dict("records"):
        daily = result.program_daily.get(r["program_id"], {})
        nxt = sum(q for d, q in daily.items() if result.as_of < d <= result.as_of + dt.timedelta(days=30))
        out.append({"program_id": r["program_id"], "name": names.get(r["program_id"], r["program_id"]),
                    "qty_per": float(r["qty_per"]), "unit": r["unit"], "production_next_30d": float(nxt)})
    return out


@router.get("/cockpit", response_model=S.CockpitResponse)
def cockpit(planner: str | None = None, scenario_id: str | None = None, article_ids: list[str] | None = Query(None),
            horizon_days: int | None = ENGINE_QUERY["horizon_days"], as_of: dt.date | None = None,
            production_mode: str | None = None, orders_source: str | None = None, coverage_unit: str | None = None,
            generate_proposals: bool | None = None, respect_lead_time: bool | None = None,
            frozen_days: int | None = None, late_order_policy: str | None = None,
            ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    try:
        result = mrp_service.compute(ctx, session, planner=planner, article_ids=article_ids, scenario_id=scenario_id,
                                     **_param_kwargs(horizon_days=horizon_days, as_of=as_of,
                                                     production_mode=production_mode, orders_source=orders_source,
                                                     coverage_unit=coverage_unit, generate_proposals=generate_proposals,
                                                     respect_lead_time=respect_lead_time, frozen_days=frozen_days,
                                                     late_order_policy=late_order_policy))
    except KeyError as exc:
        raise HTTPException(404, f"Scénario inconnu : {exc}")
    names = _supplier_names(ctx)
    arts = sorted(result.articles.values(), key=lambda r: ({"critical": 0, "warning": 1, "info": 2}.get(r.kpis.get("severity") or "", 3),
                                                            r.kpis["coverage_sim_days"], r.article.article_id))
    meta = result.articles and next(iter(result.articles.values()))
    return S.CockpitResponse(
        as_of=result.as_of, horizon_days=result.params.horizon_days, planner=planner, scenario_id=scenario_id,
        data_source=ctx.source.name, pdp_version=None if not meta else getattr(meta, "pdp_version", None),
        kpis=P.cockpit_kpis(result), articles=[P.article_summary(r) for r in arts],
        alerts=[P.alert_out(a, r.article.designation) for r in arts for a in r.alerts],
        proposals=[P.proposal_out(p, r, names) for r in arts for p in r.proposals],
        diagnostics=result.diagnostics, weekly_supply_demand=P.weekly_supply_demand(result))


@router.get("/articles/{article_id}/projection", response_model=S.ProjectionResponse)
def projection(article_id: str, granularity: Literal["day", "week"] = "day", scenario_id: str | None = None,
               horizon_days: int | None = ENGINE_QUERY["horizon_days"], as_of: dt.date | None = None,
               production_mode: str | None = None, orders_source: str | None = None, coverage_unit: str | None = None,
               generate_proposals: bool | None = None, include_proposals_in_simulation: bool | None = None,
               respect_lead_time: bool | None = None, frozen_days: int | None = None,
               late_order_policy: str | None = None, history_days: int | None = Query(None, ge=0, le=365),
               ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    try:
        result = mrp_service.compute(ctx, session, article_ids=[article_id], scenario_id=scenario_id,
                                     **_param_kwargs(horizon_days=horizon_days, as_of=as_of, production_mode=production_mode,
                                                     orders_source=orders_source, coverage_unit=coverage_unit,
                                                     generate_proposals=generate_proposals,
                                                     include_proposals_in_simulation=include_proposals_in_simulation,
                                                     respect_lead_time=respect_lead_time, frozen_days=frozen_days,
                                                     late_order_policy=late_order_policy, history_days=history_days))
    except KeyError as exc:
        raise HTTPException(404, f"Scénario inconnu : {exc}")
    ar = result.articles.get(article_id)
    if ar is None:
        raise HTTPException(404, f"Article inconnu : {article_id}")
    return P.projection_out(ar, result, granularity, _supplier_names(ctx), _programs_for(ctx, article_id, result))


@router.get("/alerts", response_model=list[S.AlertOut])
def alerts(planner: str | None = None, scenario_id: str | None = None, severity: str | None = None,
           alert_type: str | None = None, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    result = mrp_service.compute(ctx, session, planner=planner, scenario_id=scenario_id)
    out = [P.alert_out(a, r.article.designation) for r in result.articles.values() for a in r.alerts]
    if severity:
        out = [a for a in out if a.severity == severity]
    if alert_type:
        out = [a for a in out if a.alert_type == alert_type]
    return out


@router.get("/proposals", response_model=list[S.ProposalOut])
def proposals(planner: str | None = None, scenario_id: str | None = None, include_ignored: bool = False,
              ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    result = mrp_service.compute(ctx, session, planner=planner, scenario_id=scenario_id)
    names = _supplier_names(ctx)
    out = [P.proposal_out(p, r, names) for r in result.articles.values() for p in r.proposals]
    if not include_ignored:
        out = [p for p in out if not p.ignored]
    return sorted(out, key=lambda p: (not p.urgent, p.order_date, p.article_id))


@router.post("/simulate", response_model=S.CompareResponse)
def simulate(req: S.SimulateRequest, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """Compare the baseline with an ad-hoc scenario (saved scenario events + extra events + params)."""
    events = [ScenarioEvent(e.kind, e.payload) for e in req.events]
    try:
        base = mrp_service.compute(ctx, session, planner=req.planner, article_ids=req.article_ids)
        scen = mrp_service.compute(ctx, session, planner=req.planner, article_ids=req.article_ids,
                                   scenario_id=req.scenario_id, extra_events=events, **req.params)
    except KeyError as exc:
        raise HTTPException(404, f"Scénario inconnu : {exc}")
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, f"Paramètres invalides : {exc}")
    return S.CompareResponse(as_of=base.as_of, base_kpis=P.cockpit_kpis(base), scenario_kpis=P.cockpit_kpis(scen),
                             articles=P.compare_articles(base, scen), diagnostics=scen.diagnostics)
