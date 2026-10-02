"""Reference tables (managed in the application: CRUD, Excel template / import / export), engine
parameters and configuration."""
from __future__ import annotations

import dataclasses
import datetime as dt
import re
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ... import __version__
from ...data import reference
from ...data.assembler import erp_dataset
from ...data.schemas import REFERENCE_TABLES, TABLES
from ...data.store import REF_MODELS, ParamOverride, audit
from ...engine.models import DatasetError, EngineParams
from ...engine.runner import resolve_as_of, resolve_init_date
from ...services import mrp_service
from ...services.access import ROLES, Access
from ...services.context import AppContext
from .. import schemas as S
from ..deps import access_dep, ctx_dep, current_user, session_dep

router = APIRouter(prefix="/api", tags=["reference"])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

PARAM_DOCS: dict[str, tuple[str, list[str] | None]] = {
    "horizon_days": ("Horizon de projection après la date de référence (jours)", None),
    "history_weeks": ("Semaines affichées avant la semaine en cours dans le tableau (le tableau commence au plus tard de la date d'initialisation du stock et du lundi obtenu)", None),
    "backlog_days": ("Âge maximal du backlog : commandes fermes passées non reçues comptées sur n jours avant la référence", None),
    "spread_rounding": ("Lissage du PDP hebdomadaire sur les jours ouvrés", ["none", "exact", "per_day"]),
    "production_mode": ("Production effective : réel pour le passé, reliquat du PDP de la semaine en cours sur les jours restants, PDP ensuite / réel puis plan / plan seul / réel seul", ["actual_then_remainder", "actual_then_plan", "plan_only", "actual_only"]),
    "missing_actual_policy": ("Jour passé sans réel déclaré : 0 ou plan", ["zero", "plan"]),
    "consumption_offset_days": ("Décalage de consommation des composants vs jour de production", None),
    "shortfall_tolerance_days": ("Tolérance de manque : un passage sous la cible qui se résorbe seul en n jours ouvrés, sans besoin non servi, ne déclenche pas de proposition", None),
    "coverage_unit": ("Unité de couverture : jours calendaires ou ouvrés", ["calendar", "working"]),
    "coverage_tie_rule": ("Un jour dont le besoin cumulé égale le stock est-il couvert ?", ["covered", "not_covered"]),
    "target_policy": ("Stock cible : couverture, stock de sécurité fixe, ou le max des deux", ["coverage_days", "safety_qty", "max"]),
    "generate_proposals": ("Calculer les propositions CBN à chaque calcul", None),
    "include_proposals_in_plan": ("Inclure les propositions CBN dans le Scenario Plan", None),
    "proposal_placement": ("Livraisons proposées : tout jour ouvré (et jour de livraison fournisseur) ou lundis seulement ; une proposition par fournisseur et par jour", ["working_days", "monday"]),
    "forecast_date_policy": ("Commandes prévisionnelles : à leur date réelle ou ramenées au lundi de leur semaine", ["actual", "week_monday"]),
    "focus_weeks": ("Calendrier « Par défaut » : nombre de semaines détaillées jour par jour après la semaine en cours", None),
    "frozen_days": ("Période gelée : aucune proposition livrable avant J + n", None),
    "respect_lead_time": ("Ne jamais proposer une livraison avant J + délai fournisseur", None),
    "delivery_shift": ("Jour de livraison non autorisé : avancer ou reculer", ["earlier", "later"]),
    "sourcing_policy": ("Choix fournisseur : quotas ou priorité", ["quota", "priority"]),
    "proposal_lookahead_days": ("Limiter les propositions à J + n (vide = tout l'horizon)", None),
    "stockout_lookahead_days": ("Limiter la détection de rupture à J + n (vide = tout l'horizon)", None),
    "firm_horizon_days": ("Horizon ferme : une rupture sur les commandes ERP au-delà est informative", None),
    "shortage_policy": ("Besoin non servi : reporté (backlog, stock net négatif) ou perdu (stock borné à 0)", ["backlog", "lost"]),
    "firm_sources": ("Types de commandes comptés dans le Scenario ERP (FIRM = ordre ferme)", None),
}


@router.get("/config", response_model=S.ConfigOut)
def config(ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep), user: str = Depends(current_user),
           access: Access = Depends(access_dep)):
    params = mrp_service.build_params(ctx, session)
    ds = erp_dataset(ctx.table)
    planners = sorted({a.planner for a in ds.articles if a.planner})
    mine = next((p for p in planners if access.name and p.upper() == access.name.upper()), None)
    as_of = resolve_as_of(ds, params)
    try:
        init_date = resolve_init_date(ds, as_of)
    except DatasetError as exc:   # the cockpit shows the data problem instead of a blank page
        raise HTTPException(422, str(exc))
    return S.ConfigOut(title=ctx.settings.app_title, data_source=ctx.source.describe(), as_of=as_of, init_date=init_date,
                       horizon_days=params.horizon_days, planners=planners,
                       default_planner=ctx.settings.default_planner or mine or (planners[0] if len(planners) == 1 else None),
                       user=user, version=__version__, reference_empty=not ds.articles, access=access.to_dict())


# ---------------------------------------------------------------- reference tables (CRUD)
def _check_stock_dates(session: Session, rows: list[dict], replace: bool) -> None:
    """``fct_stock`` carries the stock initialisation date, the point zero of the application: one single
    date for every article, never in the future (docs/regles_metier.md § 1)."""
    dates = {pd.Timestamp(r["snapshot_date"]).date() for r in rows if r.get("snapshot_date") not in (None, "")}
    if not replace:
        dates |= {d for d in session.scalars(select(REF_MODELS["fct_stock"].snapshot_date).distinct()).all() if d}
    if len(dates) > 1:
        raise HTTPException(422, "Le stock de référence doit porter une seule date d'initialisation pour tous les articles "
                            f"(trouvé : {', '.join(d.isoformat() for d in sorted(dates))}). Importer la table en mode "
                            "« remplacer » avec une date unique.")
    today = dt.date.today()
    if any(d > today for d in dates):
        raise HTTPException(422, f"La date d'initialisation du stock ne peut pas être postérieure à aujourd'hui ({today.isoformat()}).")


def _schema(name: str):
    if name not in REFERENCE_TABLES:
        raise HTTPException(404, f"Table inconnue : {name}. Tables : {', '.join(REFERENCE_TABLES)}")
    return TABLES[name]


@router.get("/reference/tables", response_model=list[S.RefTableInfo])
def tables(session: Session = Depends(session_dep)):
    out = []
    for name in REFERENCE_TABLES:
        t = TABLES[name]
        n = len(session.scalars(select(REF_MODELS[name])).all())
        out.append(S.RefTableInfo(name=name, label=t.label, description=t.description, key=list(t.key), rows=n,
                                  columns=[S.RefColumn(name=c.name, label=c.label, type=c.type, key=c.name in t.key,
                                                       required=c.required or c.name in t.key, description=c.description)
                                           for c in t.columns]))
    return out


@router.get("/reference/{name}/rows")
def rows(name: str, session: Session = Depends(session_dep)):
    _schema(name)
    return reference.rows_out(session, name)


@router.put("/reference/{name}/rows")
def upsert_row(name: str, body: S.RefRowIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
               user: str = Depends(current_user), access: Access = Depends(access_dep)):
    _schema(name)
    access.require_row(name, body.values)
    if name == "ref_planners" and str(body.values.get("role", "")).lower() not in ROLES:
        raise HTTPException(422, f"Rôle inconnu : {body.values.get('role')} (attendu : {', '.join(ROLES)})")
    if name == "fct_stock":
        _check_stock_dates(session, [body.values], replace=False)
    try:
        out = reference.upsert_row(session, name, body.values, user)
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, f"Ligne invalide : {exc}")
    session.commit()
    ctx.bump()
    return out


@router.post("/reference/{name}/delete", status_code=204)
def delete_row(name: str, body: S.RefKeyIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
               user: str = Depends(current_user), access: Access = Depends(access_dep)):
    _schema(name)
    access.require_row(name, body.key)
    try:
        found = reference.delete_row(session, name, body.key, user)
    except (ValueError, KeyError) as exc:
        raise HTTPException(422, f"Clé invalide : {exc}")
    if not found:
        raise HTTPException(404, "Ligne inconnue")
    session.commit()
    ctx.bump()


@router.get("/reference/{name}/template.xlsx")
def template(name: str, filled: bool = False, session: Session = Depends(session_dep)):
    """Minimal Excel template of the table (``filled=true``: with the current content, for an export)."""
    t = _schema(name)
    content = reference.template_workbook(name, reference.rows_out(session, name) if filled else None)
    fn = f"{'export' if filled else 'modele'}_{name}_{dt.date.today().isoformat()}.xlsx"
    return Response(content, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{fn}"',
                                                       "X-Table-Label": t.label})


@router.post("/reference/{name}/import", response_model=S.ImportReport, status_code=201)
async def import_table(name: str, file: UploadFile = File(...), mode: str = Form("replace"),
                       ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                       user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Load an Excel file built on the template.  ``mode`` = ``replace`` (the table is replaced) or
    ``merge`` (rows are created / updated by key)."""
    _schema(name)
    access.require_table(name)
    content = await file.read()
    if not content:
        raise HTTPException(422, "Fichier vide")
    try:
        df, notes = reference.parse_workbook(name, content)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(422, f"Classeur illisible : {exc}")
    if mode not in ("replace", "merge"):
        raise HTTPException(422, "mode : replace ou merge")
    if df.empty and mode == "replace":
        raise HTTPException(422, "Aucune ligne dans le fichier : la table n'a pas été vidée")
    if name == "fct_stock":
        _check_stock_dates(session, df.to_dict("records"), replace=(mode == "replace"))
    try:
        n = reference.replace_all(session, name, df, user) if mode == "replace" else reference.merge_all(session, name, df, user)
    except ValueError as exc:
        session.rollback()
        raise HTTPException(422, f"Ligne invalide : {exc}")
    audit(session, user, "import", name, file.filename or "", None, {"rows": n, "mode": mode})
    session.commit()
    ctx.bump()
    return S.ImportReport(created=n, ignored=len(notes), notes=notes)


# --- typed views used by the screens
@router.get("/reference/articles", response_model=list[S.ArticleRef])
def articles(planner: str | None = None, ctx: AppContext = Depends(ctx_dep)):
    ds = erp_dataset(ctx.table, planner=planner)
    return [S.ArticleRef(**{k: getattr(a, k) for k in S.ArticleRef.model_fields}) for a in ds.articles]


@router.get("/reference/links", response_model=list[S.LinkRef])
def links(article_id: str | None = None, ctx: AppContext = Depends(ctx_dep)):
    ds = erp_dataset(ctx.table, article_ids=[article_id] if article_id else None)
    names = {s.supplier_id: s.name for s in ds.suppliers}
    return [S.LinkRef(article_id=l.article_id, supplier_id=l.supplier_id, supplier_name=names.get(l.supplier_id, ""),
                      moq=l.moq, pack_qty=l.pack_qty, lead_time_days=l.lead_time_days, quota_pct=l.quota_pct,
                      priority=l.priority, active=l.active) for l in ds.links]


@router.get("/reference/programs", response_model=list[S.ProgramRef])
def programs(ctx: AppContext = Depends(ctx_dep)):
    df = ctx.table("ref_programs")
    counts = ctx.table("ref_bom").groupby("program_id").size().to_dict()
    return [S.ProgramRef(program_id=r["program_id"], name=r["name"], family=r["family"], active=bool(r["active"]),
                         components=int(counts.get(r["program_id"], 0))) for r in df.to_dict("records")]


@router.get("/reference/pdp")
def pdp(program_id: str | None = None, ctx: AppContext = Depends(ctx_dep)):
    df = ctx.table("fct_production_plan")
    if program_id:
        df = df[df["program_id"] == program_id]
    return [{"program_id": r["program_id"], "week_start": r["week_start"].isoformat() if r["week_start"] else None,
             "qty": float(r["qty"]), "version": r["version"]} for r in df.to_dict("records")]


@router.post("/reference/refresh")
def refresh(ctx: AppContext = Depends(ctx_dep), access: Access = Depends(access_dep)):
    access.require_write()
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


def _check_override(scope: str, key2: str, field: str, value: Any) -> None:
    allowed = {"global": set(mrp_service.GLOBAL_FIELDS), "article_week": set(mrp_service.ARTICLE_WEEK_FIELDS)}[scope]
    if scope == "article_week" and not re.fullmatch(r"\d{4}-W\d{2}", key2 or ""):
        raise HTTPException(422, "key2 doit être une semaine ISO, ex. 2026-W40")
    if field not in allowed:
        raise HTTPException(422, f"Champ non paramétrable pour {scope} : {field}. Autorisés : {sorted(allowed)}")
    if scope == "global":
        try:
            mrp_service._coerce_param(field, value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, f"Valeur invalide : {exc}")
    elif value is not None and value != "":
        try:
            mrp_service.ARTICLE_WEEK_FIELDS[field](value)
        except (TypeError, ValueError) as exc:
            raise HTTPException(422, f"Valeur invalide pour {field} : {exc}")


def _write_override(session: Session, user: str, scope: str, key1: str, key2: str, field: str,
                    value: Any) -> ParamOverride | None:
    """Upsert one override ; ``None`` / empty value for an article week deletes it (back to the article value)."""
    row = session.scalars(select(ParamOverride).where(ParamOverride.scope == scope, ParamOverride.key1 == key1,
                                                      ParamOverride.key2 == key2, ParamOverride.field == field)).first()
    if scope == "article_week" and (value is None or value == ""):
        if row is not None:
            audit(session, user, "delete_param", "param_override", row.id, key1, {"field": field, "week": key2})
            session.delete(row)
        return None
    text = "" if value is None else str(value)
    if row is None:
        row = ParamOverride(scope=scope, key1=key1, key2=key2, field=field, value=text, updated_by=user)
        session.add(row)
    else:
        row.value, row.updated_by = text, user
    audit(session, user, "set_param", "param_override", f"{scope}/{key1}/{key2}/{field}",
          key1 if scope == "article_week" else None, {"value": text})
    return row


def _authorize_override(access: Access, scope: str, key1: str) -> None:
    if scope == "global":
        access.require_params()
    else:
        access.require_article_params(key1)


@router.put("/params/overrides", response_model=S.ParamOverrideOut | None)
def upsert_override(body: S.ParamOverrideIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                    user: str = Depends(current_user), access: Access = Depends(access_dep)):
    _authorize_override(access, body.scope, body.key1)
    _check_override(body.scope, body.key2, body.field, body.value)
    row = _write_override(session, user, body.scope, body.key1, body.key2, body.field, body.value)
    session.commit()
    ctx.bump()
    return row


@router.put("/params/overrides/batch", response_model=list[S.ParamOverrideOut])
def upsert_overrides(body: S.ParamOverrideBatchIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                     user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Several weekly values of one article in one transaction (paste / fill of the weekly grid) ;
    an empty value restores the article value for that week."""
    _authorize_override(access, body.scope, body.key1)
    for it in body.items:
        _check_override(body.scope, it.key2, it.field, it.value)
    out = []
    for it in body.items:
        row = _write_override(session, user, body.scope, body.key1, it.key2, it.field, it.value)
        if row is not None:
            out.append(row)
    session.commit()
    ctx.bump()
    return out


@router.delete("/params/overrides", status_code=204)
def reset_overrides(scope: str, key1: str = "", ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                    user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Remove every override of a scope / key (all the weekly values of an article, or all the global rules)."""
    if scope not in ("global", "article_week"):
        raise HTTPException(422, "scope : global ou article_week")
    _authorize_override(access, scope, key1)
    rows = session.scalars(select(ParamOverride).where(ParamOverride.scope == scope, ParamOverride.key1 == key1)).all()
    for row in rows:
        session.delete(row)
    audit(session, user, "reset_params", "param_override", f"{scope}/{key1}", key1 or None, {"rows": len(rows)})
    session.commit()
    ctx.bump()


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
