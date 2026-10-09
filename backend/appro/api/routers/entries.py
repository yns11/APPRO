"""The two editable rows of the grid – plan cells and adjustment cells – and the audit journal."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...data.store import AppAdjustment, AppCellFlag, AppPlanCell, AuditLog, audit
from ...services import mrp_service
from ...services.access import Access
from ...services.context import AppContext
from .. import schemas as S
from ..deps import access_dep, ctx_dep, current_user, session_dep

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
                   user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Set the adjustment of one day from a signed quantity or an arithmetic expression (``-50``,
    ``2*600-50``…) ; any date ; blank or 0 clears the cell ; ``expression`` omitted changes the note only."""
    _check_article(ctx, body.article_id)
    access.require_article(body.article_id)
    try:
        row = mrp_service.set_adjustment(session, user, body.article_id, body.date, body.expression, body.note)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    session.commit()
    ctx.bump()
    return row


@router.put("/adjustments/batch", response_model=list[S.AdjustmentOut])
def set_adjustments(body: S.AdjustmentBatchIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                    user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Several adjustment cells in one transaction (fill handle of the grid)."""
    out = []
    for c in body.cells:
        _check_article(ctx, c.article_id)
        access.require_article(c.article_id)
        try:
            row = mrp_service.set_adjustment(session, user, c.article_id, c.date, c.expression, c.note)
        except ValueError as exc:
            session.rollback()
            raise HTTPException(422, f"{c.date} : {exc}")
        if row is not None:
            out.append(row)
    session.commit()
    ctx.bump()
    return out


@router.delete("/adjustments/{cell_id}", status_code=204)
def delete_adjustment(cell_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                      user: str = Depends(current_user), access: Access = Depends(access_dep)):
    row = session.get(AppAdjustment, cell_id)
    if row is None:
        raise HTTPException(404, "Cellule inconnue")
    access.require_article(row.article_id)
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
                  user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Set the planned delivery of one supplier on one day (quantity or expression, 0 = nothing
    expected) ; blank = back to the ERP ; ``expression`` omitted changes the note only.  Dates before
    the reference day are refused (the past is not planned)."""
    _check_article(ctx, body.article_id)
    access.require_article(body.article_id)
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


@router.put("/plan/batch", response_model=list[S.PlanCellOut])
def set_plan_cells(body: S.PlanCellBatchIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                   user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Several plan cells in one transaction (fill handle of the grid) ; past dates are skipped."""
    as_of: dict[str, dt.date] = {}
    out = []
    for c in body.cells:
        _check_article(ctx, c.article_id)
        access.require_article(c.article_id)
        if c.article_id not in as_of:
            as_of[c.article_id] = mrp_service.compute(ctx, session, article_ids=[c.article_id]).as_of
        if c.date < as_of[c.article_id]:
            continue
        try:
            row = mrp_service.set_plan_cell(session, user, c.article_id, c.supplier_id, c.date, c.expression, c.note)
        except ValueError as exc:
            session.rollback()
            raise HTTPException(422, f"{c.date} : {exc}")
        if row is not None:
            out.append(row)
    session.commit()
    ctx.bump()
    return out


@router.delete("/plan/{cell_id}", status_code=204)
def delete_plan_cell(cell_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                     user: str = Depends(current_user), access: Access = Depends(access_dep)):
    row = session.get(AppPlanCell, cell_id)
    if row is None:
        raise HTTPException(404, "Cellule inconnue")
    access.require_article(row.article_id)
    audit(session, user, "delete", "plan_cell", row.id, row.article_id, {"date": str(row.date), "supplier_id": row.supplier_id})
    session.delete(row)
    session.commit()
    ctx.bump()


# ---------------------------------------------------------------- cell flags (clicks on read-only cells)
@router.get("/flags", response_model=list[S.FlagOut])
def list_flags(article_id: str | None = None, kind: str | None = None, session: Session = Depends(session_dep)):
    """Ignored firm-order days and refused CBN proposals."""
    q = select(AppCellFlag).order_by(AppCellFlag.article_id, AppCellFlag.date)
    if article_id:
        q = q.where(AppCellFlag.article_id == article_id)
    if kind:
        q = q.where(AppCellFlag.kind == kind)
    return session.scalars(q).all()


@router.post("/flags/toggle", response_model=S.FlagOut | None)
def toggle_flag(body: S.FlagIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """One click: set the flag when absent (returns it), remove it when present (returns null).
    ``order_ignored`` needs a date on/after the reference day (the past is not planned)."""
    _check_article(ctx, body.article_id)
    access.require_article(body.article_id)
    if body.kind == "order_ignored":
        as_of = mrp_service.compute(ctx, session, article_ids=[body.article_id]).as_of
        if body.date < as_of:
            raise HTTPException(422, f"Seules les commandes fermes à partir de la date de référence ({as_of.isoformat()}) peuvent être ignorées")
    row = mrp_service.toggle_flag(session, user, body.article_id, body.supplier_id, body.date, body.kind, body.qty, body.note)
    session.commit()
    ctx.bump()
    return row


@router.delete("/flags/{flag_id}", status_code=204)
def delete_flag(flag_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                user: str = Depends(current_user), access: Access = Depends(access_dep)):
    row = session.get(AppCellFlag, flag_id)
    if row is None:
        raise HTTPException(404, "Drapeau inconnu")
    access.require_article(row.article_id)
    audit(session, user, "delete", row.kind, row.id, row.article_id, {"date": str(row.date), "supplier_id": row.supplier_id})
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
