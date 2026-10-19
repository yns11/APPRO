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
from ...services import mrp_service, validation
from ...services.access import ROLES, Access
from ...services.context import AppContext
from .. import schemas as S
from ..deps import access_dep, ctx_dep, current_user, session_dep

router = APIRouter(prefix="/api", tags=["reference"])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

PARAM_DOCS: dict[str, tuple[str, list[str] | None]] = {
    "target_tolerance_pct": ("Tolérance sous la cible (%) : le CBN ne remonte à la cible que si le stock projeté passe sous (100 − n) % de la cible ; la quantité proposée vise 100 %", None),
    "horizon_days": ("Horizon de calcul après la date de référence (jours) : 52 semaines, fixé dans l'application", None),
    "history_weeks": ("Affichage : semaines montrées avant la semaine en cours (réglage par utilisateur)", None),
    "focus_weeks": ("Affichage : semaines détaillées jour par jour après la semaine en cours (réglage par utilisateur)", None),
}
#: rules fixed in the application (documented for the screens : the guide of the engine explains each one)
FIXED_RULES: list[tuple[str, str]] = [
    ("Unité de temps", "jours ouvrés pour la couverture, les seuils et le délai"),
    ("Besoin non servi", "perdu : le stock s'arrête à 0, le manque affiché est le cumul des jours sans stock"),
    ("Stock cible", "besoin des n prochains jours ouvrés de couverture ; pas de stock de sécurité ni de cycle de commande"),
    ("Propositions", "sur les jours de livraison du fournisseur, regroupées sur la livraison précédant le besoin ; plancher dans le délai, cible pleine ensuite ; plafonds du lien"),
    ("Horizon", "52 semaines, tableau et PDP compris"),
    ("Commandes comptées", "ordres fermes seulement (les prévisionnels sont affichés, jamais comptés)"),
]


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


def _check_rows(session: Session, name: str, rows: list[dict], replace: bool) -> None:
    """Business validations beyond the column types (services/validation.py)."""
    try:
        if name == "ref_article_suppliers":
            sups = {r["supplier_id"]: r for r in reference.rows_out(session, "ref_suppliers")}
            validation.check_links(rows, sups)
        elif name == "ref_consumption_terms":
            validation.check_terms(session, rows, replace=replace)
        elif name == "ref_suppliers":
            for r in rows:
                lead = r.get("lead_time_days")
                if lead not in (None, "") and float(lead) < 1:
                    raise ValueError(f"{r.get('supplier_id')} : le délai doit être d'au moins 1 jour ouvré")
    except ValueError as exc:
        raise HTTPException(422, str(exc))


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
def rows(name: str, planner: str | None = None, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """Rows of one reference table ; ``planner`` keeps what concerns that planner's articles (the
    articles themselves, their supplier links, bills of material and stock, the suppliers and the
    programmes they reach) ; planners and delegations are never filtered.  Articles, suppliers and
    programmes carry **query columns** computed from the other tables (never stored, never
    imported) : see :func:`query_columns`."""
    _schema(name)
    out = reference.rows_out(session, name)
    if planner and name not in ("ref_planners", "ref_delegations"):
        want = planner.upper()
        ids = {r["article_id"] for r in reference.rows_out(session, "ref_articles") if str(r.get("planner") or "").upper() == want}
        if name == "ref_articles":
            out = [r for r in out if str(r.get("planner") or "").upper() == want]
        elif name in ("ref_article_suppliers", "ref_bom", "fct_stock"):
            out = [r for r in out if r.get("article_id") in ids]
        elif name == "ref_suppliers":
            sids = {r["supplier_id"] for r in reference.rows_out(session, "ref_article_suppliers") if r.get("article_id") in ids}
            out = [r for r in out if r.get("supplier_id") in sids]
        elif name == "ref_programs":
            pids = {r["program_id"] for r in reference.rows_out(session, "ref_bom") if r.get("article_id") in ids}
            out = [r for r in out if r.get("program_id") in pids]
    query_columns(ctx, session, name, out)
    return out


def query_columns(ctx: AppContext, session: Session, name: str, out: list[dict]) -> None:
    """Add the computed columns of the Référentiel tables (in place) :

    * articles : ``programs`` (names of the programmes whose bill of material uses the article),
      ``program_count``, ``supplier_count`` (active article ↔ supplier rules) ;
    * suppliers : ``articles`` (ids of the articles with an active rule for the supplier), ``article_count`` ;
    * programmes : ``article_count`` (components of the bill of material), ``has_plan`` (a quantity
      planned from the current week on, PDP ERP or active version)."""
    if name == "ref_articles":
        names = {p["program_id"]: str(p.get("name") or p["program_id"]) for p in reference.rows_out(session, "ref_programs")}
        progs: dict[str, set[str]] = {}
        for b in reference.rows_out(session, "ref_bom"):
            progs.setdefault(b["article_id"], set()).add(names.get(b["program_id"], str(b["program_id"])))
        sups: dict[str, int] = {}
        for lk in reference.rows_out(session, "ref_article_suppliers"):
            if lk.get("active", True):
                sups[lk["article_id"]] = sups.get(lk["article_id"], 0) + 1
        for r in out:
            found = sorted(progs.get(r["article_id"], ()), key=str.lower)
            r["programs"], r["program_count"], r["supplier_count"] = ", ".join(found), len(found), sups.get(r["article_id"], 0)
    elif name == "ref_suppliers":
        arts: dict[str, set[str]] = {}
        for lk in reference.rows_out(session, "ref_article_suppliers"):
            if lk.get("active", True):
                arts.setdefault(lk["supplier_id"], set()).add(str(lk["article_id"]))
        for r in out:
            found = sorted(arts.get(r["supplier_id"], ()))
            r["articles"], r["article_count"] = ", ".join(found), len(found)
    elif name == "ref_programs":
        from .pdp import _sheet_frame
        counts: dict[str, int] = {}
        for b in reference.rows_out(session, "ref_bom"):
            counts[b["program_id"]] = counts.get(b["program_id"], 0) + 1
        ds, _erp, _as_of, monday = _sheet_frame(ctx, session)
        planned = {p.program_id for p in ds.pdp if p.qty > 0 and p.week_start >= monday}
        for r in out:
            r["article_count"], r["has_plan"] = counts.get(r["program_id"], 0), r["program_id"] in planned


@router.put("/reference/{name}/rows")
def upsert_row(name: str, body: S.RefRowIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
               user: str = Depends(current_user), access: Access = Depends(access_dep)):
    _schema(name)
    access.require_row(name, body.values)
    if name == "ref_planners" and str(body.values.get("role", "")).lower() not in ROLES:
        raise HTTPException(422, f"Rôle inconnu : {body.values.get('role')} (attendu : {', '.join(ROLES)})")
    if name == "fct_stock":
        _check_stock_dates(session, [body.values], replace=False)
    _check_rows(session, name, [body.values], replace=False)
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
    _check_rows(session, name, df.to_dict("records"), replace=(mode == "replace"))
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
def links(article_id: str | None = None, supplier_id: str | None = None, ctx: AppContext = Depends(ctx_dep)):
    ds = erp_dataset(ctx.table, article_ids=[article_id] if article_id else None)
    names = {s.supplier_id: s.name for s in ds.suppliers}
    sups = {s.supplier_id: s for s in ds.suppliers}
    out = [S.LinkRef(article_id=l.article_id, supplier_id=l.supplier_id, supplier_name=names.get(l.supplier_id, ""),
                     moq=l.moq, pack_qty=l.pack_qty, lead_time_days=l.lead_time(sups), lead_time_override=l.lead_time_days,
                     quota_pct=l.quota_pct, priority=l.priority, active=l.active, max_qty_per_delivery=l.max_qty_per_delivery,
                     max_qty_per_week=l.max_qty_per_week) for l in ds.links]
    return [l for l in out if not supplier_id or l.supplier_id == supplier_id]


@router.get("/reference/consumption-sources")
def consumption_sources(article_id: str, session: Session = Depends(session_dep)):
    """Articles eligible as consumption sources of ``article_id`` (derived consumption)."""
    return validation.eligible_sources(session, article_id)


@router.get("/reference/suppliers", response_model=list[S.SupplierRef])
def suppliers_list(ctx: AppContext = Depends(ctx_dep)):
    df = ctx.table("ref_suppliers")
    return [S.SupplierRef(supplier_id=r["supplier_id"], name=r["name"], country=r["country"], contact=r["contact"],
                          delivery_weekdays=r["delivery_weekdays"] or "1,2,3,4,5",
                          lead_time_days=int(r["lead_time_days"]) if int(r["lead_time_days"] or 0) > 0 else 5,
                          active=bool(r["active"])) for r in df.to_dict("records")]


@router.get("/reference/programs", response_model=list[S.ProgramRef])
def programs(ctx: AppContext = Depends(ctx_dep)):
    df = ctx.table("ref_programs")
    counts = ctx.table("ref_bom").groupby("program_id").size().to_dict()
    return [S.ProgramRef(program_id=r["program_id"], name=r["name"], family=r["family"], active=bool(r["active"]),
                         components=int(counts.get(r["program_id"], 0))) for r in df.to_dict("records")]


@router.get("/reference/perimeter", response_model=S.PerimeterOut)
def perimeter(planner: str | None = None, ctx: AppContext = Depends(ctx_dep)):
    """The programmes and suppliers of the articles of one planner (all articles without planner):
    the choices offered by the filters of the supply table."""
    arts = ctx.table("ref_articles")
    if planner:
        arts = arts[arts["planner"].astype(str).str.upper() == planner.upper()]
    ids = set(arts["article_id"])
    bom = ctx.table("ref_bom")
    prog_ids = set(bom[bom["article_id"].isin(ids)]["program_id"])
    programs = ctx.table("ref_programs")
    programs = programs[programs["program_id"].isin(prog_ids)].sort_values("name")
    links = ctx.table("ref_article_suppliers")
    sup_ids = set(links[links["article_id"].isin(ids)]["supplier_id"])
    suppliers = ctx.table("ref_suppliers")
    suppliers = suppliers[suppliers["supplier_id"].isin(sup_ids)].sort_values("name")
    return S.PerimeterOut(
        planner=planner, articles=len(ids),
        programs=[S.PerimeterItem(id=str(r["program_id"]), name=str(r["name"] or "")) for r in programs.to_dict("records")],
        suppliers=[S.PerimeterItem(id=str(r["supplier_id"]), name=str(r["name"] or "")) for r in suppliers.to_dict("records")])


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
@router.get("/params/fixed")
def params_fixed():
    """The rules fixed in the application, for the Paramètres screen."""
    return [{"label": k, "value": v} for k, v in FIXED_RULES]


@router.get("/params/schema", response_model=list[S.ParamDoc])
def params_schema():
    defaults = EngineParams()
    out = []
    for f in dataclasses.fields(EngineParams):
        if f.name not in mrp_service.EDITABLE_GLOBAL_FIELDS:
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
    allowed = {"global": set(mrp_service.EDITABLE_GLOBAL_FIELDS), "article_week": set(mrp_service.ARTICLE_WEEK_FIELDS)}[scope]
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
    from sqlalchemy.exc import IntegrityError
    for attempt in range(2):     # two clients writing the same week at once: the second pass finds the rows
        out = []
        try:
            for it in body.items:
                row = _write_override(session, user, body.scope, body.key1, it.key2, it.field, it.value)
                if row is not None:
                    out.append(row)
            session.commit()
            break
        except IntegrityError:
            session.rollback()
            if attempt:
                raise
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
