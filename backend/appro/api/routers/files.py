"""Excel exports (simulation, alerts, plan) and re-import of the simulation workbook."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from ...data.store import audit
from ...services import excel_service, mrp_service
from ...services.access import Access
from ...services.context import AppContext
from .. import schemas as S
from ..deps import access_dep, ctx_dep, current_user, session_dep

router = APIRouter(prefix="/api", tags=["files"])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx(content: bytes, filename: str) -> Response:
    return Response(content, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/exports/simulation.xlsx")
def export_simulation(planner: str | None = None, article_ids: list[str] | None = Query(None),
                      granularity: Literal["day", "week"] = "day", horizon_days: int | None = Query(None, ge=7, le=730),
                      from_date: dt.date | None = None, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    result = mrp_service.compute(ctx, session, planner=planner, article_ids=article_ids,
                                 **({"horizon_days": horizon_days} if horizon_days else {}))
    meta = {"Périmètre": planner or "tous", "Source": ctx.source.name}
    content = excel_service.simulation_workbook(result, article_ids, granularity, from_date, meta)
    return _xlsx(content, f"simulation_{result.as_of.isoformat()}_{granularity}.xlsx")


@router.get("/exports/alerts.xlsx")
def export_alerts(planner: str | None = None, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    result = mrp_service.compute(ctx, session, planner=planner)
    return _xlsx(excel_service.alerts_workbook(result), f"alertes_{result.as_of.isoformat()}.xlsx")


@router.get("/exports/plan.xlsx")
def export_plan(planner: str | None = None, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    result = mrp_service.compute(ctx, session, planner=planner)
    return _xlsx(excel_service.plan_workbook(result), f"plan_{result.as_of.isoformat()}.xlsx")


@router.post("/imports/simulation", response_model=S.ImportReport, status_code=201)
async def import_simulation(file: UploadFile = File(...), ctx: AppContext = Depends(ctx_dep),
                            session: Session = Depends(session_dep), user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Re-import the *Plan* and *Ajustement* rows of an exported ``SIMULATION`` sheet (day
    granularity): the plan cells and adjustments of every article present in the workbook replace
    the stored ones for the days of the sheet."""
    content = await file.read()
    try:
        cells, notes = excel_service.parse_simulation_workbook(content)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except Exception as exc:
        raise HTTPException(422, f"Classeur illisible : {exc}")
    known = set(ctx.table("ref_articles")["article_id"])
    as_of = mrp_service.compute(ctx, session).as_of
    created = 0
    for aid, items in cells.items():
        if aid not in known:
            notes.append(f"article inconnu : {aid}")
            continue
        if not access.can_edit_article(aid):
            notes.append(f"article hors de votre carnet, ignoré : {aid}")
            continue
        for c in items:
            if c.kind == "PLAN":
                if c.date < as_of:
                    continue
                row = mrp_service.set_plan_cell(session, user, aid, c.supplier_id, c.date,
                                                "" if c.qty is None else str(c.qty))
            else:
                row = mrp_service.set_adjustment(session, user, aid, c.date, "" if c.qty is None else str(c.qty))
            if row is not None:
                created += 1
    audit(session, user, "import", "simulation", file.filename or "", None, {"cells": created, "articles": len(cells)})
    session.commit()
    ctx.bump()
    return S.ImportReport(created=created, ignored=len(notes), notes=notes)

