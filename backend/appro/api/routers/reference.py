"""Reference data (read-only, from the ERP source) and parameters (overrides stored in the app)."""
from __future__ import annotations

import dataclasses
import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import __version__
from ...data.schemas import parse_weekdays
from ...data.store import AuditLog, ParamOverride, audit
from ...engine.models import EngineParams
from ...services import mrp_service
from ...services.context import AppContext
from .. import schemas as S
from ..deps import ctx_dep, current_user, session_dep

router = APIRouter(prefix="/api", tags=["reference"])

PARAM_DOCS: dict[str, tuple[str, list[str] | None]] = {
    "horizon_days": ("Horizon de projection après la date de référence (jours)", None),
    "history_days": ("Jours d'historique affichés avant la date de référence", None),
    "spread_rounding": ("Lissage du PDP hebdomadaire sur les jours ouvrés", ["none", "exact", "per_day"]),
    "production_mode": ("Production effective : réel puis plan / plan seul / réel seul", ["actual_then_plan", "plan_only", "actual_only"]),
    "missing_actual_policy": ("Jour passé sans réel déclaré : utiliser le plan ou 0", ["plan", "zero"]),
    "consumption_offset_days": ("Décalage de consommation des composants vs jour de production", None),
    "late_order_policy": ("Commandes en retard : replanifier à date, ignorer, garder", ["reschedule", "ignore", "keep"]),
    "orders_source": ("Commandes prises en compte : ERP + saisies, ERP seul, saisies seules", ["merged", "erp", "app"]),
    "coverage_unit": ("Unité de couverture : jours calendaires ou ouvrés", ["calendar", "working"]),
    "coverage_tie_rule": ("Un jour dont le besoin cumulé égale le stock est-il couvert ?", ["covered", "not_covered"]),
    "target_policy": ("Stock cible : couverture, stock de sécurité fixe, ou le max des deux", ["coverage_days", "safety_qty", "max"]),
    "generate_proposals": ("Générer des propositions de commande", None),
    "include_proposals_in_simulation": ("Inclure les propositions dans le stock simulé", None),
    "frozen_days": ("Période gelée : aucune proposition livrable avant J + n", None),
    "respect_lead_time": ("Ne jamais proposer une livraison avant J + délai fournisseur", None),
    "delivery_shift": ("Jour de livraison non autorisé : avancer ou reculer", ["earlier", "later"]),
    "sourcing_policy": ("Choix fournisseur : quotas ou priorité", ["quota", "priority"]),
    "proposal_lookahead_days": ("Limiter les propositions à J + n (vide = tout l'horizon)", None),
    "stockout_lookahead_days": ("Limiter la détection de rupture à J + n (vide = tout l'horizon)", None),
    "firm_horizon_days": ("Horizon ferme : une rupture sur flux fermes au-delà est informative", None),
    "shortage_policy": ("Besoin non servi : reporté (backlog, stock net négatif) ou perdu (stock borné à 0)", ["backlog", "lost"]),
    "firm_sources": ("Types de commandes du stock ferme (FIRM = DELJIT / OA / saisie envoyée)", None),
    "forecast_sources": ("Types ajoutés au stock prévisionnel (FORECAST = DELFOR)", None),
    "simulated_sources": ("Types ajoutés au stock simulé (PLANNED = saisies / propositions acceptées)", None),
    "app_firm_orders": ("Commandes saisies marquées envoyées : couche ferme, ou simulation seulement", ["firm", "simulated"]),
}


@router.get("/config", response_model=S.ConfigOut)
def config(ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep), user: str = Depends(current_user)):
    params = mrp_service.build_params(ctx, session)
    from ...data.assembler import erp_dataset
    from ...engine.runner import resolve_as_of
    ds = erp_dataset(ctx.source)
    planners = sorted({a.planner for a in ds.articles if a.planner})
    return S.ConfigOut(title=ctx.settings.app_title, data_source=ctx.source.describe(), as_of=resolve_as_of(ds, params),
                       horizon_days=params.horizon_days, planners=planners,
                       default_planner=ctx.settings.default_planner or (planners[0] if len(planners) == 1 else None),
                       user=user, version=__version__)


@router.get("/reference/articles", response_model=list[S.ArticleRef])
def articles(planner: str | None = None, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    from ...data.assembler import erp_dataset
    ds = erp_dataset(ctx.source, planner=planner)
    mrp_service.apply_overrides(ds, session.scalars(select(ParamOverride).where(ParamOverride.scope != "global")).all())
    return [S.ArticleRef(**{k: getattr(a, k) for k in S.ArticleRef.model_fields}) for a in ds.articles]


@router.get("/reference/suppliers", response_model=list[S.SupplierRef])
def suppliers(ctx: AppContext = Depends(ctx_dep)):
    df = ctx.source.table("ref_suppliers")
    return [S.SupplierRef(supplier_id=r["supplier_id"], name=r["name"], country=r["country"], contact=r["contact"],
                          delivery_weekdays=sorted(parse_weekdays(r["delivery_weekdays"])), active=bool(r["active"]))
            for r in df.to_dict("records")]


@router.get("/reference/links", response_model=list[S.LinkRef])
def links(article_id: str | None = None, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    from ...data.assembler import erp_dataset
    ds = erp_dataset(ctx.source, article_ids=[article_id] if article_id else None)
    mrp_service.apply_overrides(ds, session.scalars(select(ParamOverride).where(ParamOverride.scope == "link")).all())
    names = {s.supplier_id: s.name for s in ds.suppliers}
    return [S.LinkRef(article_id=l.article_id, supplier_id=l.supplier_id, supplier_name=names.get(l.supplier_id, ""),
                      moq=l.moq, pack_qty=l.pack_qty, lead_time_days=l.lead_time_days, quota_pct=l.quota_pct,
                      priority=l.priority, active=l.active) for l in ds.links]


@router.get("/reference/programs", response_model=list[S.ProgramRef])
def programs(ctx: AppContext = Depends(ctx_dep)):
    df = ctx.source.table("ref_programs")
    bom = ctx.source.table("ref_bom")
    counts = bom.groupby("program_id").size().to_dict()
    return [S.ProgramRef(program_id=r["program_id"], name=r["name"], family=r["family"], active=bool(r["active"]),
                         components=int(counts.get(r["program_id"], 0))) for r in df.to_dict("records")]


@router.get("/reference/bom", response_model=list[S.BomRef])
def bom(article_id: str | None = None, program_id: str | None = None, ctx: AppContext = Depends(ctx_dep)):
    df = ctx.source.table("ref_bom")
    names = dict(zip(ctx.source.table("ref_programs")["program_id"], ctx.source.table("ref_programs")["name"]))
    if article_id:
        df = df[df["article_id"] == article_id]
    if program_id:
        df = df[df["program_id"] == program_id]
    return [S.BomRef(program_id=r["program_id"], program_name=names.get(r["program_id"], ""), article_id=r["article_id"],
                     qty_per=float(r["qty_per"]), unit=r["unit"], scrap_pct=float(r["scrap_pct"] or 0))
            for r in df.to_dict("records")]


@router.get("/reference/plan")
def plan(program_id: str | None = None, ctx: AppContext = Depends(ctx_dep)):
    df = ctx.source.table("fct_production_plan")
    if program_id:
        df = df[df["program_id"] == program_id]
    return [{"program_id": r["program_id"], "week_start": r["week_start"].isoformat() if r["week_start"] else None,
             "iso_week": r["iso_week"], "qty": float(r["qty"]), "version": r["version"]} for r in df.to_dict("records")]


@router.post("/reference/refresh")
def refresh(ctx: AppContext = Depends(ctx_dep), user: str = Depends(current_user)):
    ctx.source.refresh()
    ctx.bump()
    return {"status": "ok", "source": ctx.source.describe()}


# ---------------------------------------------------------------- parameters
@router.get("/params/schema", response_model=list[S.ParamDoc])
def params_schema():
    defaults = EngineParams()
    out = []
    for f in dataclasses.fields(EngineParams):
        if f.name in ("as_of", "working_weekdays"):
            continue
        doc, options = PARAM_DOCS.get(f.name, (f.name, None))
        val = getattr(defaults, f.name)
        typ = "bool" if isinstance(val, bool) else "int" if isinstance(val, int) else "list" if isinstance(val, tuple) else "str"
        if val is None:
            typ = "int?"
        out.append(S.ParamDoc(field=f.name, default=list(val) if isinstance(val, tuple) else val, type=typ,
                              description=doc, options=options))
    return out


@router.get("/params/effective")
def params_effective(ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    p = mrp_service.build_params(ctx, session)
    return {k: (v.isoformat() if isinstance(v, dt.date) else list(v) if isinstance(v, tuple) else v)
            for k, v in dataclasses.asdict(p).items()}


@router.get("/params/overrides", response_model=list[S.ParamOverrideOut])
def list_overrides(scope: str | None = None, key1: str | None = None, session: Session = Depends(session_dep)):
    q = select(ParamOverride).order_by(ParamOverride.scope, ParamOverride.key1, ParamOverride.field)
    if scope:
        q = q.where(ParamOverride.scope == scope)
    if key1:
        q = q.where(ParamOverride.key1 == key1)
    return session.scalars(q).all()


@router.put("/params/overrides", response_model=S.ParamOverrideOut)
def upsert_override(body: S.ParamOverrideIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                    user: str = Depends(current_user)):
    allowed = {"global": set(mrp_service.GLOBAL_FIELDS), "article": set(mrp_service.ARTICLE_FIELDS),
               "link": set(mrp_service.LINK_FIELDS)}[body.scope]
    if body.field not in allowed:
        raise HTTPException(422, f"Champ non paramétrable pour {body.scope} : {body.field}. Autorisés : {sorted(allowed)}")
    if body.scope == "global":
        try:
            mrp_service._coerce_param(body.field, body.value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, f"Valeur invalide : {exc}")
    row = session.scalars(select(ParamOverride).where(ParamOverride.scope == body.scope, ParamOverride.key1 == body.key1,
                                                      ParamOverride.key2 == body.key2, ParamOverride.field == body.field)).first()
    value = "" if body.value is None else str(body.value)
    if row is None:
        row = ParamOverride(scope=body.scope, key1=body.key1, key2=body.key2, field=body.field, value=value, updated_by=user)
        session.add(row)
    else:
        row.value, row.updated_by = value, user
    audit(session, user, "set_param", "param_override", f"{body.scope}/{body.key1}/{body.key2}/{body.field}",
          body.key1 if body.scope in ("article", "link") else None, {"value": value})
    session.commit()
    ctx.bump()
    return row


@router.delete("/params/overrides/{override_id}", status_code=204)
def delete_override(override_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                    user: str = Depends(current_user)):
    row = session.get(ParamOverride, override_id)
    if row is None:
        raise HTTPException(404, "Paramètre inconnu")
    audit(session, user, "delete_param", "param_override", row.id, None, {"field": row.field})
    session.delete(row)
    session.commit()
    ctx.bump()


# ---------------------------------------------------------------- audit
@router.get("/audit", response_model=list[S.AuditOut])
def audit_log(article_id: str | None = None, user: str | None = None, limit: int = 200,
              session: Session = Depends(session_dep)):
    q = select(AuditLog).order_by(AuditLog.ts.desc()).limit(min(limit, 1000))
    if article_id:
        q = q.where(AuditLog.article_id == article_id)
    if user:
        q = q.where(AuditLog.user == user)
    return [S.AuditOut(id=r.id, ts=r.ts, user=r.user, action=r.action, entity_type=r.entity_type,
                       entity_id=r.entity_id, article_id=r.article_id, payload=r.payload) for r in session.scalars(q)]
