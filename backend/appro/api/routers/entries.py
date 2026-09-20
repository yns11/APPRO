"""Planner entries: orders, receipts, stock adjustments, actual production (with audit log)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...data.store import AppAdjustment, AppCell, AppOrder, AppProductionActual, AppReceipt, audit
from ...services import mrp_service
from ...services.context import AppContext
from .. import schemas as S
from ..deps import ctx_dep, current_user, session_dep

router = APIRouter(prefix="/api/entries", tags=["entries"])


def _unit_of(ctx: AppContext, article_id: str) -> str:
    df = ctx.source.table("ref_articles")
    row = df[df["article_id"] == article_id]
    if row.empty:
        raise HTTPException(404, f"Article inconnu : {article_id}")
    return row.iloc[0]["unit"] or "PCE"


# ---------------------------------------------------------------- orders
@router.get("/orders", response_model=list[S.OrderOut])
def list_orders(article_id: str | None = None, status: str | None = Query(None), session: Session = Depends(session_dep)):
    q = select(AppOrder).order_by(AppOrder.expected_date)
    if article_id:
        q = q.where(AppOrder.article_id == article_id)
    if status:
        q = q.where(AppOrder.status == status)
    return session.scalars(q).all()


@router.post("/orders", response_model=S.OrderOut, status_code=201)
def create_order(body: S.OrderIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                 user: str = Depends(current_user)):
    row = AppOrder(**body.model_dump(), unit=_unit_of(ctx, body.article_id), created_by=user, source="MANUAL")
    session.add(row)
    audit(session, user, "create", "order", row.id, body.article_id, body.model_dump(mode="json"))
    session.commit()
    ctx.bump()
    return row


@router.patch("/orders/{order_id}", response_model=S.OrderOut)
def update_order(order_id: str, body: S.OrderUpdate, ctx: AppContext = Depends(ctx_dep),
                 session: Session = Depends(session_dep), user: str = Depends(current_user)):
    row = session.get(AppOrder, order_id)
    if row is None:
        raise HTTPException(404, "Commande inconnue")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(row, k, v)
    audit(session, user, "update", "order", row.id, row.article_id, {k: str(v) for k, v in changes.items()})
    session.commit()
    ctx.bump()
    return row


@router.delete("/orders/{order_id}", status_code=204)
def delete_order(order_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                 user: str = Depends(current_user)):
    row = session.get(AppOrder, order_id)
    if row is None:
        raise HTTPException(404, "Commande inconnue")
    audit(session, user, "delete", "order", row.id, row.article_id, {"qty": row.qty, "expected_date": str(row.expected_date)})
    session.delete(row)
    session.commit()
    ctx.bump()


# ---------------------------------------------------------------- receipts
@router.get("/receipts", response_model=list[S.ReceiptOut])
def list_receipts(article_id: str | None = None, session: Session = Depends(session_dep)):
    q = select(AppReceipt).order_by(AppReceipt.receipt_date.desc())
    if article_id:
        q = q.where(AppReceipt.article_id == article_id)
    return session.scalars(q).all()


@router.post("/receipts", response_model=S.ReceiptOut, status_code=201)
def create_receipt(body: S.ReceiptIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                   user: str = Depends(current_user)):
    _unit_of(ctx, body.article_id)
    row = AppReceipt(**body.model_dump(), created_by=user)
    session.add(row)
    if body.order_id:
        app_order = session.get(AppOrder, body.order_id)
        if app_order is not None:
            received = sum(r.qty for r in session.scalars(select(AppReceipt).where(AppReceipt.order_id == app_order.id))) + body.qty
            if received >= app_order.qty - 1e-9:
                app_order.status = "RECEIVED"
    audit(session, user, "create", "receipt", row.id, body.article_id, body.model_dump(mode="json"))
    session.commit()
    ctx.bump()
    return row


@router.delete("/receipts/{receipt_id}", status_code=204)
def delete_receipt(receipt_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                   user: str = Depends(current_user)):
    row = session.get(AppReceipt, receipt_id)
    if row is None:
        raise HTTPException(404, "Réception inconnue")
    audit(session, user, "delete", "receipt", row.id, row.article_id, {"qty": row.qty, "receipt_date": str(row.receipt_date)})
    session.delete(row)
    session.commit()
    ctx.bump()


# ---------------------------------------------------------------- adjustments
@router.get("/adjustments", response_model=list[S.AdjustmentOut])
def list_adjustments(article_id: str | None = None, session: Session = Depends(session_dep)):
    q = select(AppAdjustment).order_by(AppAdjustment.date.desc())
    if article_id:
        q = q.where(AppAdjustment.article_id == article_id)
    return session.scalars(q).all()


@router.post("/adjustments", response_model=S.AdjustmentOut, status_code=201)
def create_adjustment(body: S.AdjustmentIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                      user: str = Depends(current_user)):
    _unit_of(ctx, body.article_id)
    if body.qty == 0:
        raise HTTPException(422, "La quantité d'ajustement ne peut pas être nulle")
    row = AppAdjustment(**body.model_dump(), created_by=user)
    session.add(row)
    audit(session, user, "create", "adjustment", row.id, body.article_id, body.model_dump(mode="json"))
    session.commit()
    ctx.bump()
    return row


@router.delete("/adjustments/{adjustment_id}", status_code=204)
def delete_adjustment(adjustment_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                      user: str = Depends(current_user)):
    row = session.get(AppAdjustment, adjustment_id)
    if row is None:
        raise HTTPException(404, "Ajustement inconnu")
    audit(session, user, "delete", "adjustment", row.id, row.article_id, {"qty": row.qty, "date": str(row.date)})
    session.delete(row)
    session.commit()
    ctx.bump()


# ---------------------------------------------------------------- actual production
@router.get("/production", response_model=list[S.ProductionActualOut])
def list_production(program_id: str | None = None, session: Session = Depends(session_dep)):
    q = select(AppProductionActual).order_by(AppProductionActual.date.desc())
    if program_id:
        q = q.where(AppProductionActual.program_id == program_id)
    return session.scalars(q).all()


@router.post("/production", response_model=S.ProductionActualOut, status_code=201)
def upsert_production(body: S.ProductionActualIn, ctx: AppContext = Depends(ctx_dep),
                      session: Session = Depends(session_dep), user: str = Depends(current_user)):
    programs = set(ctx.source.table("ref_programs")["program_id"])
    if body.program_id not in programs:
        raise HTTPException(404, f"Programme inconnu : {body.program_id}")
    row = session.scalars(select(AppProductionActual).where(AppProductionActual.program_id == body.program_id,
                                                            AppProductionActual.date == body.date)).first()
    if row is None:
        row = AppProductionActual(**body.model_dump(), created_by=user)
        session.add(row)
    else:
        row.qty = body.qty
        row.created_by = user
    audit(session, user, "upsert", "production", row.id, None, body.model_dump(mode="json"))
    session.commit()
    ctx.bump()
    return row


@router.delete("/production/{row_id}", status_code=204)
def delete_production(row_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                      user: str = Depends(current_user)):
    row = session.get(AppProductionActual, row_id)
    if row is None:
        raise HTTPException(404, "Saisie inconnue")
    audit(session, user, "delete", "production", row.id, None, {"program_id": row.program_id, "date": str(row.date)})
    session.delete(row)
    session.commit()
    ctx.bump()


# ---------------------------------------------------------------- simulation grid cells
@router.get("/cells", response_model=list[S.CellOut])
def list_cells(article_id: str | None = None, kind: str | None = None, session: Session = Depends(session_dep)):
    q = select(AppCell).order_by(AppCell.article_id, AppCell.date)
    if article_id:
        q = q.where(AppCell.article_id == article_id)
    if kind:
        q = q.where(AppCell.kind == kind)
    return session.scalars(q).all()


@router.put("/cells", response_model=S.CellOut | None)
def upsert_cell(body: S.CellIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                user: str = Depends(current_user)):
    """Set a simulated order or an adjustment for one day from a quantity or an arithmetic
    expression (``1200``, ``2*600-50``, ``(800+400)/2``…).  Empty or zero removes the cell."""
    _unit_of(ctx, body.article_id)
    try:
        row = mrp_service.upsert_cell(ctx, session, user, body.article_id, body.date, body.kind, body.expression)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    session.commit()
    ctx.bump()
    return row


@router.delete("/cells/{cell_id}", status_code=204)
def delete_cell(cell_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                user: str = Depends(current_user)):
    row = session.get(AppCell, cell_id)
    if row is None:
        raise HTTPException(404, "Cellule inconnue")
    audit(session, user, "delete", "cell", row.id, row.article_id, {"date": str(row.date), "kind": row.kind, "qty": row.qty})
    session.delete(row)
    session.commit()
    ctx.bump()
