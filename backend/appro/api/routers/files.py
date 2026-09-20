"""Excel exports (simulation, alerts, order book) and re-import of entries."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...data.store import AppAdjustment, AppOrder, AppProductionActual, AppReceipt, audit
from ...services import excel_service, mrp_service
from ...services.context import AppContext
from .. import schemas as S
from ..deps import ctx_dep, current_user, session_dep

router = APIRouter(prefix="/api", tags=["files"])
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx(content: bytes, filename: str) -> Response:
    return Response(content, media_type=XLSX, headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/exports/simulation.xlsx")
def export_simulation(planner: str | None = None, article_ids: list[str] | None = Query(None),
                      scenario_id: str | None = None, granularity: Literal["day", "week"] = "day",
                      horizon_days: int | None = Query(None, ge=7, le=730), from_date: dt.date | None = None,
                      ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    try:
        result = mrp_service.compute(ctx, session, planner=planner, article_ids=article_ids, scenario_id=scenario_id,
                                     **({"horizon_days": horizon_days} if horizon_days else {}))
    except KeyError as exc:
        raise HTTPException(404, f"Scénario inconnu : {exc}")
    meta = {"Périmètre": planner or "tous", "Scénario": scenario_id or "base", "Source": ctx.source.name}
    content = excel_service.simulation_workbook(result, article_ids, granularity, from_date, meta)
    return _xlsx(content, f"simulation_{result.as_of.isoformat()}_{granularity}.xlsx")


@router.get("/exports/alerts.xlsx")
def export_alerts(planner: str | None = None, scenario_id: str | None = None,
                  ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    result = mrp_service.compute(ctx, session, planner=planner, scenario_id=scenario_id)
    return _xlsx(excel_service.alerts_workbook(result), f"alertes_{result.as_of.isoformat()}.xlsx")


@router.get("/exports/orders.xlsx")
def export_orders(planner: str | None = None, scenario_id: str | None = None,
                  ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    result = mrp_service.compute(ctx, session, planner=planner, scenario_id=scenario_id)
    return _xlsx(excel_service.orders_workbook(result), f"carnet_commandes_{result.as_of.isoformat()}.xlsx")


@router.post("/imports/entries", response_model=S.ImportReport, status_code=201)
async def import_entries(file: UploadFile = File(...), ctx: AppContext = Depends(ctx_dep),
                         session: Session = Depends(session_dep), user: str = Depends(current_user)):
    """Import the planner inputs of an exported workbook: ``SAISIES`` sheet, decisions of the
    ``PROPOSITIONS`` sheet (A / M) and receipts typed in the ``CARNET_COMMANDES`` sheet."""
    content = await file.read()
    try:
        entries, notes = excel_service.parse_entries_workbook(content)
    except Exception as exc:
        raise HTTPException(422, f"Classeur illisible : {exc}")
    articles = ctx.source.table("ref_articles")
    units = dict(zip(articles["article_id"], articles["unit"]))
    programs = set(ctx.source.table("ref_programs")["program_id"])
    created = 0
    for e in entries:
        if e.kind == "PRODUCTION":
            if e.key not in programs:
                notes.append(f"programme inconnu : {e.key}")
                continue
            session.add(AppProductionActual(program_id=e.key, date=e.date, qty=e.qty, created_by=user))
        else:
            if e.key not in units:
                notes.append(f"article inconnu : {e.key}")
                continue
            if e.kind == "COMMANDE":
                session.add(AppOrder(article_id=e.key, supplier_id=e.supplier_id, expected_date=e.date, qty=e.qty,
                                     unit=units[e.key] or "PCE", note=e.comment, created_by=user,
                                     source="PROPOSAL" if e.proposal_id else "IMPORT", proposal_id=e.proposal_id))
            elif e.kind == "RECEPTION":
                session.add(AppReceipt(article_id=e.key, supplier_id=e.supplier_id, order_id=e.order_id,
                                       receipt_date=e.date, qty=e.qty, note=e.comment, created_by=user))
                app_order = session.get(AppOrder, e.order_id) if e.order_id else None
                if app_order is not None:
                    received = sum(r.qty for r in session.scalars(select(AppReceipt).where(AppReceipt.order_id == app_order.id))) + e.qty
                    if received >= app_order.qty - 1e-9:
                        app_order.status = "RECEIVED"
            else:
                session.add(AppAdjustment(article_id=e.key, date=e.date, qty=e.qty, comment=e.comment, created_by=user))
        created += 1
    audit(session, user, "import", "entries", file.filename or "", None, {"created": created, "ignored": len(notes)})
    session.commit()
    ctx.bump()
    return S.ImportReport(created=created, ignored=len(notes), notes=notes)
