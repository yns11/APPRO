"""Cockpit, article projection, multi-article grid, backlog, programme impact, alerts, proposals."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...data.assembler import erp_dataset
from ...data.store import ParamOverride
from ...engine.calendar import iso_week_label, iso_week_monday
from ...engine.models import WEEKLY_FIELDS
from ...engine.runner import resolve_as_of
from ...services import mrp_service, pdf_service
from ...services.context import AppContext
from ...services.transports import transports_by_order
from .. import presenters as P
from .. import schemas as S
from ..deps import ctx_dep, session_dep
from ..fastjson import json_response

router = APIRouter(prefix="/api", tags=["mrp"])

HORIZON = Query(None, ge=7, le=400)
HISTORY = Query(None, ge=0, le=52)


def _kw(**kw):
    return {k: v for k, v in kw.items() if v is not None}


def _supplier_names(ctx: AppContext) -> dict[str, str]:
    df = ctx.table("ref_suppliers")
    return dict(zip(df["supplier_id"], df["name"]))


def _suppliers(ctx: AppContext):
    """Supplier records (lead time, delivery days) for the effective lead time of the links."""
    return {s.supplier_id: s for s in erp_dataset(ctx.table).suppliers}


def _programs_for(ctx: AppContext, article_id: str, result) -> list[dict]:
    bom = ctx.table("ref_bom")
    prg = ctx.table("ref_programs")
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
def cockpit(planner: str | None = None, article_ids: list[str] | None = Query(None), horizon_days: int | None = HORIZON,
            ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    result = mrp_service.compute(ctx, session, planner=planner, article_ids=article_ids, **_kw(horizon_days=horizon_days))
    names = _supplier_names(ctx)
    arts = sorted(result.articles.values(), key=lambda r: ({"critical": 0, "warning": 1, "info": 2}.get(r.kpis.get("severity") or "", 3),
                                                            r.kpis["coverage_plan_days"], r.article.article_id))
    # the lists are capped: the cockpit shows the first alerts and KPIs, the dedicated pages page through the rest
    alerts = sorted((P.alert_out(a, r.article.designation) for r in arts for a in r.alerts),
                    key=lambda a: ({"critical": 0, "warning": 1}.get(a.severity, 2), a.article_id))[:2000]
    props = sorted((P.proposal_out(p, r, names) for r in arts for p in r.proposals),
                   key=lambda p: (not p.urgent, p.order_date, p.article_id))[:500]
    return S.CockpitResponse(
        as_of=result.as_of, init_date=result.init_date, horizon_days=result.params.horizon_days, planner=planner, data_source=ctx.source.name,
        pdp_version=None, kpis=P.cockpit_kpis(result), articles=[P.article_summary(r) for r in arts],
        erp_stock_enabled=bool(ctx.settings.erp_stock_table),
        erp_stock_date=max((dt.date.fromisoformat(r.kpis["erp_stock_date"]) for r in arts if r.kpis.get("erp_stock_date")), default=None),
        alerts=alerts, proposals=props, backlog=P.backlog_rows(result),
        diagnostics=result.diagnostics, weekly_supply_demand=P.weekly_supply_demand(result),
        weekly_stock_value=P.weekly_stock_value(result))


@router.get("/articles/{article_id}/projection", response_model=S.ProjectionResponse)
def projection(article_id: str, granularity: Literal["default", "day", "week"] = "default",
               horizon_days: int | None = HORIZON, history_weeks: int | None = HISTORY, focus_weeks: int | None = Query(None, ge=0, le=52),
               ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    result = mrp_service.compute(ctx, session, article_ids=[article_id],
                                 **_kw(horizon_days=horizon_days, history_weeks=history_weeks, focus_weeks=focus_weeks))
    ar = result.articles.get(article_id)
    if ar is None:
        raise HTTPException(404, f"Article inconnu : {article_id}")
    return json_response(P.projection_out(ar, result, granularity, _supplier_names(ctx), _programs_for(ctx, article_id, result),
                                          suppliers=_suppliers(ctx), transports=transports_by_order(session)))


@router.get("/articles/{article_id}/delivery-plan", response_model=S.DeliveryPlanResponse)
def delivery_plan(article_id: str, horizon_days: int | None = HORIZON, ctx: AppContext = Depends(ctx_dep),
                  session: Session = Depends(session_dep)):
    """The Plan row as an ERP delivery schedule: one line per supplier and ISO week with a quantity."""
    result = mrp_service.compute(ctx, session, article_ids=[article_id], **_kw(horizon_days=horizon_days))
    ar = result.articles.get(article_id)
    if ar is None:
        raise HTTPException(404, f"Article inconnu : {article_id}")
    return json_response(P.delivery_plan(ar, result.as_of, _supplier_names(ctx)))


def _programs_of(ctx: AppContext) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for r in ctx.table("ref_bom").to_dict("records"):
        out.setdefault(r["article_id"], []).append(r["program_id"])
    return out


@router.get("/grid", response_model=S.GridResponse)
def grid(planner: str | None = None, article_ids: list[str] | None = Query(None), program_id: str | None = None,
         supplier_id: list[str] | None = Query(None), q: str | None = None, granularity: Literal["default", "day", "week"] = "default",
         horizon_days: int | None = HORIZON, history_weeks: int | None = HISTORY, focus_weeks: int | None = Query(None, ge=0, le=52),
         page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=200), sort: Literal["article", "supplier"] = "article",
         ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """One page of the supply table (same columns for every article).  Filters: articles, programme,
    suppliers (several ``supplier_id`` = any of them), free text ``q`` on the identifier / designation ; ``sort`` by article identifier or by
    the name of the (first) supplier.  The whole perimeter is computed once (cached) ; only the
    requested page is serialised."""
    result, arts, names = _select_articles(ctx, session, planner, article_ids, program_id, supplier_id, q, sort,
                                           **_kw(horizon_days=horizon_days, history_weeks=history_weeks, focus_weeks=focus_weeks))
    total = len(arts)
    page_arts = arts[(page - 1) * page_size: page * page_size]
    return json_response(P.grid_out(result, granularity, names, _programs_of(ctx), articles=page_arts,
                                    total=total, page=page, page_size=page_size, suppliers=_suppliers(ctx), transports=transports_by_order(session)))


def _select_articles(ctx: AppContext, session: Session, planner: str | None, article_ids: list[str] | None, program_id: str | None,
                     supplier_id: list[str] | None, q: str | None, sort: str, **kw):
    """The articles of the supply table : perimeter (planner, articles, programme, suppliers), free text on
    the identifier / designation, sorted by article identifier or by the name of the first supplier."""
    ids = set(article_ids or [])
    if program_id:
        bom = ctx.table("ref_bom")
        prog_ids = set(bom[bom["program_id"] == program_id]["article_id"])
        ids = (ids & prog_ids if ids else prog_ids) or {"__none__"}
    if supplier_id:
        lk = ctx.table("ref_article_suppliers")
        sup_ids = set(lk[lk["supplier_id"].isin(supplier_id)]["article_id"])
        ids = (ids & sup_ids if ids else sup_ids) or {"__none__"}
    result = mrp_service.compute(ctx, session, planner=planner, article_ids=sorted(ids) if ids else None, **kw)
    names = _supplier_names(ctx)
    if sort == "supplier":
        def first_supplier(r) -> str:
            sids = [l.supplier_id for l in r.lanes if l.supplier_id]
            return min((names.get(sid, sid) or sid).lower() for sid in sids) if sids else "~"
        arts = sorted(result.articles.values(), key=lambda r: (first_supplier(r), r.article.article_id))
    else:
        arts = sorted(result.articles.values(), key=lambda r: r.article.article_id)
    if q and q.strip():   # « ; » separates alternatives : 123;456 = contains 123 or 456
        arts = [r for r in arts if P.match_any(f"{r.article.article_id} {r.article.designation}", q)]
    return result, arts, names


@router.get("/exports/inventaire.pdf")
def export_inventory(planner: str | None = None, article_ids: list[str] | None = Query(None), program_id: str | None = None,
                     supplier_id: list[str] | None = Query(None), q: str | None = None, sort: Literal["article", "supplier"] = "article",
                     ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """Printable **inventory sheet** (PDF, A4 landscape) of the articles of the supply table, same filters
    as ``/grid`` : one line per article with the theoretical stock at date and blank columns for the count."""
    result, arts, names = _select_articles(ctx, session, planner, article_ids, program_id, supplier_id, q, sort)
    prg = ctx.table("ref_programs")
    prog_name = next(iter(prg[prg["program_id"] == program_id]["name"]), program_id) if program_id else ""
    parts = [f"programme {prog_name}" if program_id else "", f"fournisseur(s) {', '.join(names.get(s, s) or s for s in supplier_id)}" if supplier_id else "",
             f"recherche « {q.strip()} »" if q and q.strip() else "", "trié par fournisseur" if sort == "supplier" else ""]
    content = pdf_service.inventory_pdf(result.as_of, arts, names, planner or "tous les approvisionneurs", " · ".join(x for x in parts if x))
    return Response(content, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="inventaire_{result.as_of.isoformat()}.pdf"'})


@router.get("/backlog", response_model=list[S.BacklogRow])
def backlog(planner: str | None = None, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """Supplier backlog: the late firm ERP orders (delivery date passed, ERP remaining quantity open)."""
    return P.backlog_rows(mrp_service.compute(ctx, session, planner=planner))


@router.get("/programs/impact", response_model=S.ProgramImpactResponse)
def programs_impact(planner: str | None = None, horizon_days: int | None = HORIZON,
                    ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """Feasible production per programme and week, for the on-hand / ERP / plan stocks."""
    result = mrp_service.compute(ctx, session, planner=planner, **_kw(horizon_days=horizon_days))
    return S.ProgramImpactResponse(as_of=result.as_of, weeks=result.program_impact.get("weeks", []),
                                   programs=result.program_impact.get("programs", []), diagnostics=result.diagnostics)


@router.get("/articles/{article_id}/weekly-params", response_model=S.WeeklyParamsResponse)
def weekly_params(article_id: str, weeks: int = Query(26, ge=1, le=104), ctx: AppContext = Depends(ctx_dep),
                  session: Session = Depends(session_dep)):
    """Stock-policy parameters per ISO week from the reference week (article values, weekly overrides).
    The calendar runs at least ``weeks`` weeks and up to the last week of the effective PDP and to the end
    of the horizon, whichever is later."""
    ds = erp_dataset(ctx.table, article_ids=[article_id])
    if not ds.articles:
        raise HTTPException(404, f"Article inconnu : {article_id}")
    params = mrp_service.build_params(ctx, session)
    mrp_service.app_entries_into_dataset(ds, session, params)
    mrp_service.apply_weekly_overrides(ds, session.scalars(select(ParamOverride).where(ParamOverride.scope == "article_week")).all())
    a = ds.articles[0]
    as_of = resolve_as_of(ds, params)
    monday = iso_week_monday(as_of)
    last = max([p.week_start for p in ds.pdp] + [as_of + dt.timedelta(days=params.horizon_days)])
    weeks = min(max(weeks, (last - monday).days // 7 + 1), 160)
    rows = []
    for k in range(weeks):
        d = monday + dt.timedelta(days=7 * k)
        wk = iso_week_label(d)
        rows.append(S.WeeklyParamRow(week=wk, week_start=d, values={f: float(a.param_at(f, d)) for f in WEEKLY_FIELDS},
                                     overridden=sorted(a.weekly.get(wk, {}).keys())))
    return S.WeeklyParamsResponse(article_id=article_id, fields=list(WEEKLY_FIELDS),
                                  defaults={f: float(getattr(a, f)) for f in WEEKLY_FIELDS}, weeks=rows)


@router.get("/alerts", response_model=list[S.AlertOut])
def alerts(planner: str | None = None, severity: str | None = None, alert_type: str | None = None,
           ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    result = mrp_service.compute(ctx, session, planner=planner)
    out = [P.alert_out(a, r.article.designation) for r in result.articles.values() for a in r.alerts]
    if severity:
        out = [a for a in out if a.severity == severity]
    if alert_type:
        out = [a for a in out if a.alert_type == alert_type]
    return out


@router.get("/proposals", response_model=list[S.ProposalOut])
def proposals(planner: str | None = None, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """Net requirements ("Proposition CBN") computed on the plan stock of the perimeter, one per
    supplier and delivery day."""
    result = mrp_service.compute(ctx, session, planner=planner)
    names = _supplier_names(ctx)
    out = [P.proposal_out(p, r, names) for r in result.articles.values() for p in r.proposals]
    return sorted(out, key=lambda p: (not p.urgent, p.order_date, p.article_id))
