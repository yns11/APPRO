"""Production plan (PDP) versions imported from Excel."""
from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...data.store import AppMeta, PdpLine, PdpVersion, ProgramScrap, audit
from ...services import actions, excel_service, mrp_service
from ...services.access import Access
from ...services.context import AppContext
from .. import schemas as S
from ..deps import access_dep, ctx_dep, current_user, session_dep

router = APIRouter(prefix="/api/pdp", tags=["pdp"])


def _out(session: Session, v: PdpVersion) -> S.PdpVersionOut:
    stats = session.execute(select(func.count(PdpLine.id), func.count(func.distinct(PdpLine.program_id)),
                                   func.min(PdpLine.week_start), func.max(PdpLine.week_start))
                            .where(PdpLine.version_id == v.id)).one()
    return S.PdpVersionOut(id=v.id, name=v.name, source_file=v.source_file, note=v.note, active=v.active,
                           imported_by=v.imported_by, imported_at=v.imported_at, line_count=stats[0], programs=stats[1],
                           first_week=stats[2], last_week=stats[3])


def _frame(ctx: AppContext, session: Session):
    """Dataset with the effective PDP, the reference day, the current Monday and the point zero."""
    from ...data.assembler import erp_dataset
    from ...engine.calendar import iso_week_monday
    from ...engine.runner import resolve_as_of, resolve_init_date
    ds = erp_dataset(ctx.table)
    params = mrp_service.build_params(ctx, session)
    mrp_service.app_entries_into_dataset(ds, session, params)
    as_of = resolve_as_of(ds, params)
    return ds, as_of, iso_week_monday(as_of), resolve_init_date(ds, as_of)


def _sheet_frame(ctx: AppContext, session: Session):
    """Effective PDP, the reference day and the current Monday (used by the Référentiel too)."""
    ds, as_of, monday, _init = _frame(ctx, session)
    return ds, as_of, monday


def _sheet_programs(ds) -> list[tuple[str, str]]:
    with_bom = {b.program_id for b in ds.bom}
    return [(p.program_id, p.name) for p in ds.programs if p.program_id in with_bom and p.active]


@router.get("/template.xlsx")
def template(weeks: int = Query(26, ge=4, le=104), ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """Empty template of the weekly PDP to import: one row per active programme with a bill of material,
    one column per ISO week from the current week."""
    ds, _as_of, monday, _init = _frame(ctx, session)
    content = excel_service.pdp_template_workbook(_sheet_programs(ds), monday, weeks)
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="modele_pdp_{dt.date.today().isoformat()}.xlsx"'})


@router.get("/export.xlsx")
def export(weeks: int = Query(26, ge=4, le=160), ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """The effective PDP as a workbook in the import format : every programme with a bill of material
    (active, or planned), every week from the first complete week after the point zero to the last
    planned week (at least ``weeks`` weeks ahead).  Re-importable as is."""
    ds, _as_of, monday, init = _frame(ctx, session)
    first = mrp_service.first_full_monday(init, monday)
    last = max([p.week_start for p in ds.pdp] + [monday + dt.timedelta(days=7 * (weeks - 1))])
    n = min((last - first).days // 7 + 1, 260)
    names = {p.program_id: p.name for p in ds.programs}
    programs = _sheet_programs(ds)
    planned = {p.program_id for p in ds.pdp if p.qty}
    programs += [(pid, names.get(pid, pid)) for pid in sorted(planned - {pid for pid, _ in programs})]
    values: dict[tuple[str, dt.date], float] = {}
    for p in ds.pdp:
        values[(p.program_id, p.week_start)] = values.get((p.program_id, p.week_start), 0.0) + p.qty
    content = excel_service.pdp_template_workbook(programs, first, n, values)
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="pdp_{dt.date.today().isoformat()}.xlsx"'})


PROGRAM_ORDER_KEY = "pdp_program_order"


def read_program_order(session: Session) -> list[str]:
    row = session.get(AppMeta, PROGRAM_ORDER_KEY)
    try:
        order = json.loads(row.value) if row and row.value else []
    except ValueError:
        order = []
    return [str(x) for x in order] if isinstance(order, list) else []


@router.get("/sheet", response_model=S.PdpSheetOut)
def sheet(weeks: int = Query(26, ge=4, le=160), planner: str | None = None, ctx: AppContext = Depends(ctx_dep),
          session: Session = Depends(session_dep), access: Access = Depends(access_dep)):
    """The effective weekly PDP as a sheet: every active programme (plus any programme planned), one
    column per ISO week **from the first complete week after the point zero** to ``weeks`` weeks ahead or
    to the last planned week.  The current week and the weeks after it are editable (managers, admins) ;
    the past weeks are read-only, except for an administrator.  ``planner`` keeps the programmes whose
    bill of material reaches that planner's articles.  Rows follow the order the managers stored."""
    from ...engine.calendar import iso_week_label
    ds, as_of, monday, init = _frame(ctx, session)
    first = mrp_service.first_full_monday(init, monday)
    last = max([p.week_start for p in ds.pdp] + [monday + dt.timedelta(days=7 * (weeks - 1))])
    week_starts = [first + dt.timedelta(days=7 * k) for k in range(min((last - first).days // 7 + 1, 260))]
    with_bom = {b.program_id for b in ds.bom}
    by_program: dict[str, dict[dt.date, float]] = {}
    for p in ds.pdp:
        weeks_of = by_program.setdefault(p.program_id, {})
        weeks_of[p.week_start] = weeks_of.get(p.week_start, 0.0) + p.qty
    names = {p.program_id: p for p in ds.programs}
    listed = sorted({p.program_id for p in ds.programs if p.active} | set(by_program),
                    key=lambda pid: (names[pid].name if pid in names else pid).lower())
    if planner:
        mine = {a.article_id for a in ds.articles if (a.planner or "").upper() == planner.upper()}
        reach = {b.program_id for b in ds.bom if b.article_id in mine}
        listed = [pid for pid in listed if pid in reach]
    order = {pid: i for i, pid in enumerate(read_program_order(session))}
    listed.sort(key=lambda pid: (order.get(pid, len(order)), 0))   # stable : the alphabetical order remains inside each group
    rates = mrp_service.scrap_rates(session)
    active = session.scalars(select(PdpVersion).where(PdpVersion.active.is_(True))).first()
    origins = ds.meta.get("pdp_week_origin", {})
    weeks_out = []
    for d in week_starts:
        past = d < monday
        o = origins.get(d.isoformat()) if past else None
        weeks_out.append(S.PdpSheetWeek(week=iso_week_label(d), week_start=d, editable=(not past) or access.is_admin, past=past,
                                        origin=o["origin"] if o else None, origin_version=o["version_name"] if o else None))
    return S.PdpSheetOut(
        as_of=as_of, current_week=iso_week_label(monday), active_version=_out(session, active) if active else None,
        first_week=iso_week_label(first), init_date=init,
        weeks=weeks_out,
        programs=[S.PdpSheetProgram(program_id=pid, name=names[pid].name if pid in names else pid,
                                    active=names[pid].active if pid in names else True,
                                    source="app" if any(by_program.get(pid, {}).values()) else "none",
                                    has_bom=pid in with_bom, scrap_pct=mrp_service.scrap_rate_at(rates, pid, monday),
                                    values=[float(by_program.get(pid, {}).get(d, 0.0)) for d in week_starts])
                  for pid in listed])


@router.put("/sheet", response_model=S.PdpVersionOut, status_code=201)
def save_sheet(body: S.PdpSheetIn, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
               user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Direct entry of the PDP.  The changed cells are applied on the effective plan and saved as a **new
    active version** holding every line of the previous active version plus the cells typed.  The current
    week and the weeks after it are open to managers and administrators ; a **past week** (from the first
    complete week after the point zero) only to an administrator : the week is then materialised in the
    new version with its effective values (history) before the edits, so that it stays complete."""
    access.require_pdp()
    ds, as_of, monday, init = _frame(ctx, session)
    first = mrp_service.first_full_monday(init, monday)
    known = {p.program_id for p in ds.programs}
    past_weeks: set[dt.date] = set()
    for c in body.cells:
        if c.program_id not in known:
            raise HTTPException(422, f"Programme inconnu : {c.program_id}")
        if c.week_start.isoweekday() != 1:
            raise HTTPException(422, f"{c.week_start.isoformat()} n'est pas un lundi")
        if c.week_start < first:
            raise HTTPException(422, f"Semaine {c.week_start.isoformat()} : antérieure au point zéro du stock ({first.isoformat()})")
        if c.week_start < monday:
            if not access.is_admin:
                raise HTTPException(422, f"Semaine {c.week_start.isoformat()} : une semaine passée n'est modifiable que par un administrateur")
            past_weeks.add(c.week_start)
    active = session.scalars(select(PdpVersion).where(PdpVersion.active.is_(True))).first()
    rec = actions.Recorder(session, user)
    rec.before(actions.PDP_ACTIVE, {})
    lines: dict[str, dict[dt.date, float]] = {}
    if active:
        for l in active.lines:
            lines.setdefault(l.program_id, {})[l.week_start] = l.qty
    # a past week edited : its effective values (history) come along so the week is complete in the new version
    for p in ds.pdp:
        if p.week_start in past_weeks and p.week_start not in lines.get(p.program_id, {}):
            lines.setdefault(p.program_id, {})[p.week_start] = p.qty
    for c in body.cells:
        lines.setdefault(c.program_id, {})[c.week_start] = float(c.qty)
    stamp = dt.datetime.now().strftime("%d/%m/%Y %H:%M")
    version = PdpVersion(name=body.name.strip() or f"Saisie du {stamp}", source_file="saisie directe", note=body.note,
                         imported_by=user, active=False)
    for pid, by_week in lines.items():
        for week_start, qty in sorted(by_week.items()):
            version.lines.append(PdpLine(program_id=pid, week_start=week_start, qty=qty))
    session.add(version)
    for v in session.scalars(select(PdpVersion).where(PdpVersion.active.is_(True))):
        v.active = False
    version.active = True
    audit(session, user, "edit", "pdp_version", version.id, None,
          {"name": version.name, "cells": len(body.cells), "programs": sorted({c.program_id for c in body.cells}),
           "past_weeks": sorted(w.isoformat() for w in past_weeks), "from_version": active.id if active else None})
    # undo = the previous version becomes active again (the new one stays in the list, inactive)
    rec.done("pdp_sheet", f"Saisie PDP : {len(body.cells)} cellule(s) → version « {version.name} »", None, response)
    session.commit()
    ctx.bump()
    return _out(session, version)


@router.put("/scrap", response_model=S.PdpSheetProgram | None)
def save_scrap(body: S.PdpScrapIn, request: Request, response: Response, ctx: AppContext = Depends(ctx_dep),
               session: Session = Depends(session_dep), user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Programme scrap rate (%) : the weekly need becomes PDP × (1 + rate / 100) before the bill of
    material.  The rate is **dated on the current week's Monday** : it applies to the current and the
    following weeks, the weeks before keep the rate they had."""
    access.require_pdp()
    from ...data.assembler import erp_dataset
    from ...engine.calendar import iso_week_monday
    from ...engine.runner import resolve_as_of
    ds = erp_dataset(ctx.table)
    if body.program_id not in {p.program_id for p in ds.programs}:
        raise HTTPException(422, f"Programme inconnu : {body.program_id}")
    monday = iso_week_monday(resolve_as_of(ds, mrp_service.build_params(ctx, session)))
    rec = actions.Recorder(session, user, actions.group_of(request))
    rec.before("program_scrap", {"program_id": body.program_id, "week_start": monday})
    row = session.get(ProgramScrap, (body.program_id, monday))
    if row is None:
        row = ProgramScrap(program_id=body.program_id, week_start=monday, rate_pct=0.0)
        session.add(row)
    previous = row.rate_pct
    row.rate_pct, row.updated_by = float(body.rate_pct), user
    audit(session, user, "edit", "program_scrap", f"{body.program_id}|{monday.isoformat()}", None,
          {"program_id": body.program_id, "week_start": monday.isoformat(), "rate_pct": float(body.rate_pct), "previous": previous})
    rec.done("pdp_scrap", f"Rebut {body.program_id} : {float(body.rate_pct):g} %", None, response, group_label="Rebuts : {n} programmes")
    session.commit()
    ctx.bump()
    return None


@router.put("/order", response_model=list[str])
def save_order(body: S.PdpOrderIn, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
               user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Row order of the PDP sheet (drag and drop), shared by every user ; programmes absent from the
    list follow, in alphabetical order."""
    access.require_pdp()
    seen: list[str] = []
    for pid in body.program_ids:
        if pid not in seen:
            seen.append(pid)
    rec = actions.Recorder(session, user)
    rec.before("meta", {"key": PROGRAM_ORDER_KEY})
    row = session.get(AppMeta, PROGRAM_ORDER_KEY)
    if row is None:
        row = AppMeta(key=PROGRAM_ORDER_KEY, value="[]")
        session.add(row)
    row.value = json.dumps(seen)
    audit(session, user, "reorder", "pdp_sheet", PROGRAM_ORDER_KEY, None, {"programs": len(seen)})
    rec.done("pdp_order", "Ordre des programmes du PDP", None, response)
    session.commit()
    ctx.bump()
    return seen


@router.get("/versions", response_model=list[S.PdpVersionOut])
def versions(session: Session = Depends(session_dep)):
    return [_out(session, v) for v in session.scalars(select(PdpVersion).order_by(PdpVersion.imported_at.desc())).all()]


@router.post("/import", response_model=S.ImportReport, status_code=201)
async def import_pdp(response: Response, file: UploadFile = File(...), name: str = Form(""), note: str = Form(""),
                     activate: str = Form("true"), sheet: str | None = Form(None),
                     ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                     user: str = Depends(current_user), access: Access = Depends(access_dep)):
    access.require_pdp()
    content = await file.read()
    if not content:
        raise HTTPException(422, "Fichier vide")
    prg = ctx.table("ref_programs")
    names = {}
    for r in prg.to_dict("records"):
        names[str(r["program_id"]).upper()] = r["program_id"]
        names[str(r["name"]).upper()] = r["program_id"]
    try:
        lines, notes = excel_service.parse_pdp_workbook(content, names, sheet)
    except Exception as exc:  # openpyxl errors on non-xlsx files
        raise HTTPException(422, f"Classeur illisible : {exc}")
    if not lines:
        raise HTTPException(422, "Aucune ligne de PDP reconnue : " + "; ".join(notes[:5]))
    # the past weeks of the file (before the current week) are kept for an administrator only
    _ds, _as_of, monday, _init = _frame(ctx, session)
    past = sorted({l.week_start for l in lines if l.week_start < monday})
    if past and not access.is_admin:
        lines = [l for l in lines if l.week_start >= monday]
        notes.append(f"{len(past)} semaine(s) passée(s) ignorée(s) (réservées à un administrateur) : "
                     + ", ".join(w.isoformat() for w in past[:6]) + (" …" if len(past) > 6 else ""))
        if not lines:
            raise HTTPException(422, "Le classeur ne contient que des semaines passées : " + "; ".join(notes[:5]))
    version = PdpVersion(name=name or file.filename or "PDP", source_file=file.filename or "", note=note,
                         imported_by=user, active=False)
    for l in lines:
        version.lines.append(PdpLine(program_id=l.program_id, week_start=l.week_start, qty=l.qty))
    rec = actions.Recorder(session, user)
    rec.before(actions.PDP_ACTIVE, {})
    session.add(version)
    # a text field on purpose : since FastAPI 0.149 a missing boolean form field reads as False, which would
    # silently stop activating the imported version
    if activate.strip().lower() not in ("false", "0", "no", "non", "off"):
        for v in session.scalars(select(PdpVersion).where(PdpVersion.active.is_(True))):
            v.active = False
        version.active = True
    audit(session, user, "import", "pdp_version", version.id, None, {"name": version.name, "lines": len(lines)})
    # undo = the previous version becomes active again (the imported one stays in the list, inactive)
    rec.done("pdp_import", f"Import PDP « {version.name} » ({len(lines)} lignes)", None, response)
    session.commit()
    ctx.bump()
    return S.ImportReport(created=len(lines), ignored=len(notes), notes=notes, version=_out(session, version))


@router.post("/versions/{version_id}/activate", response_model=S.PdpVersionOut)
def activate(version_id: str, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
             user: str = Depends(current_user), access: Access = Depends(access_dep)):
    access.require_pdp()
    v = session.get(PdpVersion, version_id)
    if v is None:
        raise HTTPException(404, "Version inconnue")
    rec = actions.Recorder(session, user)
    rec.before(actions.PDP_ACTIVE, {})
    for other in session.scalars(select(PdpVersion).where(PdpVersion.active.is_(True))):
        other.active = False
    v.active = True
    audit(session, user, "activate", "pdp_version", v.id, None, {"name": v.name})
    rec.done("pdp_version", f"Activation du PDP « {v.name} »", None, response)
    session.commit()
    ctx.bump()
    return _out(session, v)


@router.post("/versions/{version_id}/deactivate", response_model=S.PdpVersionOut)
def deactivate(version_id: str, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
               user: str = Depends(current_user), access: Access = Depends(access_dep)):
    access.require_pdp()
    v = session.get(PdpVersion, version_id)
    if v is None:
        raise HTTPException(404, "Version inconnue")
    rec = actions.Recorder(session, user)
    rec.before(actions.PDP_ACTIVE, {})
    was_active = v.active
    v.active = False
    # « back to the previous state » : the most recent other version takes over (there is no ERP plan)
    fallback = None
    if was_active:
        fallback = session.scalars(select(PdpVersion).where(PdpVersion.id != v.id).order_by(PdpVersion.imported_at.desc())).first()
        if fallback is not None:
            fallback.active = True
    audit(session, user, "deactivate", "pdp_version", v.id, None, {"name": v.name, "now_active": fallback.id if fallback else None})
    rec.done("pdp_version", f"Désactivation du PDP « {v.name} »", None, response)
    session.commit()
    ctx.bump()
    return _out(session, v)


@router.delete("/versions/{version_id}", status_code=204)
def delete_version(version_id: str, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                   user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Deletes the version and its lines – **not undoable** (the screen asks for a confirmation)."""
    access.require_pdp()
    v = session.get(PdpVersion, version_id)
    if v is None:
        raise HTTPException(404, "Version inconnue")
    audit(session, user, "delete", "pdp_version", v.id, None, {"name": v.name})
    session.delete(v)
    actions.record_fixed(session, user, "pdp_version", f"Suppression du PDP « {v.name} »", response=response)
    session.commit()
    ctx.bump()


@router.get("/versions/{version_id}/lines")
def version_lines(version_id: str, session: Session = Depends(session_dep)):
    v = session.get(PdpVersion, version_id)
    if v is None:
        raise HTTPException(404, "Version inconnue")
    return [{"program_id": l.program_id, "week_start": l.week_start.isoformat(), "qty": l.qty} for l in v.lines]
