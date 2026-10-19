"""Production plan (PDP) versions imported from Excel."""
from __future__ import annotations

import datetime as dt
import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...data.store import AppMeta, PdpLine, PdpVersion, ProgramScrap, audit
from ...services import excel_service, mrp_service
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


@router.get("/template.xlsx")
def template(weeks: int = Query(26, ge=4, le=104), ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """Template of the weekly PDP to import: one row per programme with a bill of material, one
    column per ISO week from the current week."""
    from ...data.assembler import erp_dataset
    from ...engine.calendar import iso_week_monday
    from ...engine.runner import resolve_as_of
    from ...services import mrp_service
    ds = erp_dataset(ctx.table)
    with_bom = {b.program_id for b in ds.bom}
    programs = [(p.program_id, p.name) for p in ds.programs if p.program_id in with_bom and p.active]
    monday = iso_week_monday(resolve_as_of(ds, mrp_service.build_params(ctx, session)))
    content = excel_service.pdp_template_workbook(programs, monday, weeks)
    return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="modele_pdp_{dt.date.today().isoformat()}.xlsx"'})


def _sheet_frame(ctx: AppContext, session: Session):
    """Effective PDP (ERP + active version), the ERP-only plan, the reference day and the current Monday."""
    from ...data.assembler import erp_dataset
    from ...engine.calendar import iso_week_monday
    from ...engine.runner import resolve_as_of
    from ...services import mrp_service
    ds = erp_dataset(ctx.table)
    erp_lines = list(ds.pdp)
    mrp_service.app_entries_into_dataset(ds, session)
    as_of = resolve_as_of(ds, mrp_service.build_params(ctx, session))
    return ds, erp_lines, as_of, iso_week_monday(as_of)


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
          session: Session = Depends(session_dep)):
    """The effective weekly PDP as a sheet: every active programme (plus any programme planned), one
    column per ISO week from the current week to ``weeks`` weeks ahead or to the last planned week.
    The current week and the weeks after it are editable (managers, admins).  ``planner`` keeps the
    programmes whose bill of material reaches that planner's articles.  Rows follow the order the
    managers stored (drag and drop), then the alphabetical order."""
    from ...engine.calendar import iso_week_label
    ds, erp_lines, as_of, monday = _sheet_frame(ctx, session)
    last = max([p.week_start for p in ds.pdp] + [monday])
    n = min(max(weeks, (last - monday).days // 7 + 1), 160)
    week_starts = [monday + dt.timedelta(days=7 * k) for k in range(n)]
    with_bom = {b.program_id for b in ds.bom}
    by_program: dict[str, dict[dt.date, float]] = {}
    src: dict[str, str] = {}
    for p in ds.pdp:
        weeks_of = by_program.setdefault(p.program_id, {})
        weeks_of[p.week_start] = weeks_of.get(p.week_start, 0.0) + p.qty
        src[p.program_id] = "app" if p.version.startswith("APP:") else "erp"
    names = {p.program_id: p for p in ds.programs}
    # every active programme (a programme just added in the Référentiel appears at once, even before its
    # bill of material) plus any programme planned
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
    return S.PdpSheetOut(
        as_of=as_of, current_week=iso_week_label(monday), active_version=_out(session, active) if active else None,
        erp_available=bool(erp_lines),
        weeks=[S.PdpSheetWeek(week=iso_week_label(d), week_start=d, editable=d >= monday) for d in week_starts],
        programs=[S.PdpSheetProgram(program_id=pid, name=names[pid].name if pid in names else pid,
                                    active=names[pid].active if pid in names else True, source=src.get(pid, "none"),
                                    has_bom=pid in with_bom, scrap_pct=mrp_service.scrap_rate_at(rates, pid, monday),
                                    values=[float(by_program.get(pid, {}).get(d, 0.0)) for d in week_starts])
                  for pid in listed])


@router.put("/sheet", response_model=S.PdpVersionOut, status_code=201)
def save_sheet(body: S.PdpSheetIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
               user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Direct entry of the PDP.  The changed cells (current and future weeks) are applied on the effective
    plan and saved as a **new active version** that holds the programmes of the previous active
    version plus the programmes touched (copied from the ERP plan, then edited) ; the other programmes
    keep following the ERP plan.  Deactivating the version restores the previous state."""
    access.require_pdp()
    ds, erp_lines, as_of, monday = _sheet_frame(ctx, session)
    known = {p.program_id for p in ds.programs}
    for c in body.cells:
        if c.program_id not in known:
            raise HTTPException(422, f"Programme inconnu : {c.program_id}")
        if c.week_start.isoweekday() != 1:
            raise HTTPException(422, f"{c.week_start.isoformat()} n'est pas un lundi")
        if c.week_start < monday:
            raise HTTPException(422, f"Semaine {c.week_start.isoformat()} : seules la semaine en cours et les semaines à venir sont modifiables")
    active = session.scalars(select(PdpVersion).where(PdpVersion.active.is_(True))).first()
    lines: dict[str, dict[dt.date, float]] = {}
    if active:
        for l in active.lines:
            lines.setdefault(l.program_id, {})[l.week_start] = l.qty
    touched = {c.program_id for c in body.cells}
    for pid in touched - set(lines):
        lines[pid] = {p.week_start: p.qty for p in erp_lines if p.program_id == pid}
    for c in body.cells:
        lines[c.program_id][c.week_start] = float(c.qty)
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
          {"name": version.name, "cells": len(body.cells), "programs": sorted(touched), "from_version": active.id if active else None})
    session.commit()
    ctx.bump()
    return _out(session, version)


@router.put("/scrap", response_model=S.PdpSheetProgram | None)
def save_scrap(body: S.PdpScrapIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
               user: str = Depends(current_user), access: Access = Depends(access_dep)):
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
    row = session.get(ProgramScrap, (body.program_id, monday))
    if row is None:
        row = ProgramScrap(program_id=body.program_id, week_start=monday, rate_pct=0.0)
        session.add(row)
    previous = row.rate_pct
    row.rate_pct, row.updated_by = float(body.rate_pct), user
    audit(session, user, "edit", "program_scrap", f"{body.program_id}|{monday.isoformat()}", None,
          {"program_id": body.program_id, "week_start": monday.isoformat(), "rate_pct": float(body.rate_pct), "previous": previous})
    session.commit()
    ctx.bump()
    return None


@router.put("/order", response_model=list[str])
def save_order(body: S.PdpOrderIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
               user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Row order of the PDP sheet (drag and drop), shared by every user ; programmes absent from the
    list follow, in alphabetical order."""
    access.require_pdp()
    seen: list[str] = []
    for pid in body.program_ids:
        if pid not in seen:
            seen.append(pid)
    row = session.get(AppMeta, PROGRAM_ORDER_KEY)
    if row is None:
        row = AppMeta(key=PROGRAM_ORDER_KEY, value="[]")
        session.add(row)
    row.value = json.dumps(seen)
    audit(session, user, "reorder", "pdp_sheet", PROGRAM_ORDER_KEY, None, {"programs": len(seen)})
    session.commit()
    ctx.bump()
    return seen


@router.get("/versions", response_model=list[S.PdpVersionOut])
def versions(session: Session = Depends(session_dep)):
    return [_out(session, v) for v in session.scalars(select(PdpVersion).order_by(PdpVersion.imported_at.desc())).all()]


@router.post("/import", response_model=S.ImportReport, status_code=201)
async def import_pdp(file: UploadFile = File(...), name: str = Form(""), note: str = Form(""),
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
    version = PdpVersion(name=name or file.filename or "PDP", source_file=file.filename or "", note=note,
                         imported_by=user, active=False)
    for l in lines:
        version.lines.append(PdpLine(program_id=l.program_id, week_start=l.week_start, qty=l.qty))
    session.add(version)
    # a text field on purpose : since FastAPI 0.149 a missing boolean form field reads as False, which would
    # silently stop activating the imported version
    if activate.strip().lower() not in ("false", "0", "no", "non", "off"):
        for v in session.scalars(select(PdpVersion).where(PdpVersion.active.is_(True))):
            v.active = False
        version.active = True
    audit(session, user, "import", "pdp_version", version.id, None, {"name": version.name, "lines": len(lines)})
    session.commit()
    ctx.bump()
    return S.ImportReport(created=len(lines), ignored=len(notes), notes=notes, version=_out(session, version))


@router.post("/versions/{version_id}/activate", response_model=S.PdpVersionOut)
def activate(version_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
             user: str = Depends(current_user), access: Access = Depends(access_dep)):
    access.require_pdp()
    v = session.get(PdpVersion, version_id)
    if v is None:
        raise HTTPException(404, "Version inconnue")
    for other in session.scalars(select(PdpVersion).where(PdpVersion.active.is_(True))):
        other.active = False
    v.active = True
    audit(session, user, "activate", "pdp_version", v.id, None, {"name": v.name})
    session.commit()
    ctx.bump()
    return _out(session, v)


@router.post("/versions/{version_id}/deactivate", response_model=S.PdpVersionOut)
def deactivate(version_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
               user: str = Depends(current_user), access: Access = Depends(access_dep)):
    access.require_pdp()
    v = session.get(PdpVersion, version_id)
    if v is None:
        raise HTTPException(404, "Version inconnue")
    v.active = False
    audit(session, user, "deactivate", "pdp_version", v.id, None, {"name": v.name})
    session.commit()
    ctx.bump()
    return _out(session, v)


@router.delete("/versions/{version_id}", status_code=204)
def delete_version(version_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                   user: str = Depends(current_user), access: Access = Depends(access_dep)):
    access.require_pdp()
    v = session.get(PdpVersion, version_id)
    if v is None:
        raise HTTPException(404, "Version inconnue")
    audit(session, user, "delete", "pdp_version", v.id, None, {"name": v.name})
    session.delete(v)
    session.commit()
    ctx.bump()


@router.get("/versions/{version_id}/lines")
def version_lines(version_id: str, session: Session = Depends(session_dep)):
    v = session.get(PdpVersion, version_id)
    if v is None:
        raise HTTPException(404, "Version inconnue")
    return [{"program_id": l.program_id, "week_start": l.week_start.isoformat(), "qty": l.qty} for l in v.lines]
