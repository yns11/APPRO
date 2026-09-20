"""Production plan (PDP) versions imported from Excel."""
from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ...data.store import PdpLine, PdpVersion, audit
from ...services import excel_service
from ...services.context import AppContext
from .. import schemas as S
from ..deps import ctx_dep, current_user, session_dep

router = APIRouter(prefix="/api/pdp", tags=["pdp"])


def _out(session: Session, v: PdpVersion) -> S.PdpVersionOut:
    stats = session.execute(select(func.count(PdpLine.id), func.count(func.distinct(PdpLine.program_id)),
                                   func.min(PdpLine.week_start), func.max(PdpLine.week_start))
                            .where(PdpLine.version_id == v.id)).one()
    return S.PdpVersionOut(id=v.id, name=v.name, source_file=v.source_file, note=v.note, active=v.active,
                           imported_by=v.imported_by, imported_at=v.imported_at, line_count=stats[0], programs=stats[1],
                           first_week=stats[2], last_week=stats[3])


@router.get("/versions", response_model=list[S.PdpVersionOut])
def versions(session: Session = Depends(session_dep)):
    return [_out(session, v) for v in session.scalars(select(PdpVersion).order_by(PdpVersion.imported_at.desc())).all()]


@router.post("/import", response_model=S.ImportReport, status_code=201)
async def import_pdp(file: UploadFile = File(...), name: str = Form(""), note: str = Form(""),
                     activate: bool = Form(True), sheet: str | None = Form(None),
                     ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                     user: str = Depends(current_user)):
    content = await file.read()
    if not content:
        raise HTTPException(422, "Fichier vide")
    prg = ctx.source.table("ref_programs")
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
             user: str = Depends(current_user)):
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
               user: str = Depends(current_user)):
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
                   user: str = Depends(current_user)):
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
