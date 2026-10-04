"""Production plan (PDP) versions imported from Excel."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...data.store import PdpLine, PdpVersion, audit
from ...services import excel_service
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


@router.get("/sheet", response_model=S.PdpSheetOut)
def sheet(weeks: int = Query(26, ge=4, le=160), ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """The effective weekly PDP as a sheet: every active programme with a bill of material (plus any
    programme planned), one column per ISO week from the current week to ``weeks`` weeks ahead or to
    the last planned week.  Weeks strictly after the current one are editable (managers, admins)."""
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
    listed = sorted({p.program_id for p in ds.programs if p.active and p.program_id in with_bom} | set(by_program),
                    key=lambda pid: (names[pid].name if pid in names else pid).lower())
    active = session.scalars(select(PdpVersion).where(PdpVersion.active.is_(True))).first()
    return S.PdpSheetOut(
        as_of=as_of, current_week=iso_week_label(monday), active_version=_out(session, active) if active else None,
        erp_available=bool(erp_lines),
        weeks=[S.PdpSheetWeek(week=iso_week_label(d), week_start=d, editable=d > monday) for d in week_starts],
        programs=[S.PdpSheetProgram(program_id=pid, name=names[pid].name if pid in names else pid,
                                    active=names[pid].active if pid in names else True, source=src.get(pid, "none"),
                                    values=[float(by_program.get(pid, {}).get(d, 0.0)) for d in week_starts])
                  for pid in listed])


@router.put("/sheet", response_model=S.PdpVersionOut, status_code=201)
def save_sheet(body: S.PdpSheetIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
               user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Direct entry of the PDP.  The changed cells (future weeks only) are applied on the effective
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
        if c.week_start <= monday:
            raise HTTPException(422, f"Semaine {c.week_start.isoformat()} : seules les semaines à venir sont modifiables")
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


@router.get("/versions", response_model=list[S.PdpVersionOut])
def versions(session: Session = Depends(session_dep)):
    return [_out(session, v) for v in session.scalars(select(PdpVersion).order_by(PdpVersion.imported_at.desc())).all()]


@router.post("/import", response_model=S.ImportReport, status_code=201)
async def import_pdp(file: UploadFile = File(...), name: str = Form(""), note: str = Form(""),
                     activate: bool = Form(True), sheet: str | None = Form(None),
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
    if activate:
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
