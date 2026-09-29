"""The two editable rows of the grid – plan cells and adjustment cells – and the audit journal."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...data.store import AppAdjustment, AppPlanCell, AuditLog, audit
from ...services import mrp_service
from ...services.context import AppContext
from .. import schemas as S
from ..deps import ctx_dep, current_user, session_dep

router = APIRouter(prefix="/api/entries", tags=["entries"])


def _check_article(ctx: AppContext, article_id: str) -> None:
    df = ctx.table("ref_articles")
    if df[df["article_id"] == article_id].empty:
        raise HTTPException(404, f"Article inconnu : {article_id}")


# ---------------------------------------------------------------- adjustments
@router.get("/adjustments", response_model=list[S.AdjustmentOut])
def list_adjustments(article_id: str | None = None, session: Session = Depends(session_dep)):
    q = select(AppAdjustment).order_by(AppAdjustment.article_id, AppAdjustment.date)
    if article_id:
        q = q.where(AppAdjustment.article_id == article_id)
    return session.scalars(q).all()


@router.put("/adjustments", response_model=S.AdjustmentOut | None)
def set_adjustment(body: S.AdjustmentIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                   user: str = Depends(current_user)):
    """Set the adjustment of one day from a signed quantity or an arithmetic expression (``-50``,
    ``2*600-50``…) ; any date ; blank or 0 clears the cell ; ``expression`` omitted changes the note only."""
    _check_article(ctx, body.article_id)
    try:
        row = mrp_service.set_adjustment(session, user, body.article_id, body.date, body.expression, body.note)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    session.commit()
    ctx.bump()
    return row


@router.delete("/adjustments/{cell_id}", status_code=204)
def delete_adjustment(cell_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                      user: str = Depends(current_user)):
    row = session.get(AppAdjustment, cell_id)
    if row is None:
        raise HTTPException(404, "Cellule inconnue")
    audit(session, user, "delete", "adjustment", row.id, row.article_id, {"date": str(row.date), "qty": row.qty})
    session.delete(row)
    session.commit()
    ctx.bump()


# ---------------------------------------------------------------- plan cells
@router.get("/plan", response_model=list[S.PlanCellOut])
def list_plan_cells(article_id: str | None = None, session: Session = Depends(session_dep)):
    """Typed plan cells (the ERP quantities are not stored)."""
    q = select(AppPlanCell).order_by(AppPlanCell.article_id, AppPlanCell.date)
    if article_id:
        q = q.where(AppPlanCell.article_id == article_id)
    return session.scalars(q).all()


@router.put("/plan", response_model=S.PlanCellOut | None)
def set_plan_cell(body: S.PlanCellIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                  user: str = Depends(current_user)):
    """Set the planned delivery of one supplier on one day (quantity or expression, 0 = nothing
    expected) ; blank = back to the ERP ; ``expression`` omitted changes the note only.  Dates before
    the reference day are refused (the past is not planned)."""
    _check_article(ctx, body.article_id)
    result = mrp_service.compute(ctx, session, article_ids=[body.article_id])
    if body.date < result.as_of:
        raise HTTPException(422, f"Le plan se saisit à partir de la date de référence ({result.as_of.isoformat()}) ; "
                                 "pour le passé, seuls les ajustements sont possibles")
    try:
        row = mrp_service.set_plan_cell(session, user, body.article_id, body.supplier_id, body.date, body.expression, body.note)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    session.commit()
    ctx.bump()
    return row


@router.delete("/plan/{cell_id}", status_code=204)
def delete_plan_cell(cell_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                     user: str = Depends(current_user)):
    row = session.get(AppPlanCell, cell_id)
    if row is None:
        raise HTTPException(404, "Cellule inconnue")
    audit(session, user, "delete", "plan_cell", row.id, row.article_id, {"date": str(row.date), "supplier_id": row.supplier_id})
    session.delete(row)
    session.commit()
    ctx.bump()


# ---------------------------------------------------------------- audit
@router.get("/audit", response_model=list[S.AuditOut])
def audit_log(article_id: str | None = None, user: str | None = None, limit: int = 200,
              session: Session = Depends(session_dep)):
    q = select(AuditLog).order_by(AuditLog.ts.desc(), AuditLog.id.desc()).limit(min(limit, 1000))
    if article_id:
        q = q.where(AuditLog.article_id == article_id)
    if user:
        q = q.where(AuditLog.user == user)
    return [S.AuditOut(id=r.id, ts=r.ts, user=r.user, action=r.action, entity_type=r.entity_type,
                       entity_id=r.entity_id, article_id=r.article_id, payload=r.payload) for r in session.scalars(q)]
