"""The two editable rows of the grid – plan cells and adjustment cells – and the audit journal."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...data.store import AppAdjustment, AppCellFlag, AppPlanCell, AuditLog, audit
from ...services import actions, mrp_service
from ...services.access import Access
from ...services.context import AppContext
from .. import schemas as S
from ..deps import access_dep, ctx_dep, current_user, session_dep

router = APIRouter(prefix="/api/entries", tags=["entries"])


def _check_article(ctx: AppContext, article_id: str) -> None:
    df = ctx.table("ref_articles")
    if df[df["article_id"] == article_id].empty:
        raise HTTPException(404, f"Article inconnu : {article_id}")


def _d(day: dt.date) -> str:
    return day.strftime("%d/%m")


def _cell_label(what: str, article_id: str, day: dt.date, expression: str | None, supplier_id: str | None = None) -> str:
    where = f"{article_id}" + (f" · {supplier_id}" if supplier_id else "") + f" · {_d(day)}"
    if expression is None:
        return f"{what} {where} : commentaire"
    if not expression.strip():
        return f"{what} {where} : effacé"
    return f"{what} {where} : {expression.strip()}"


FLAG_LABELS = {"order_ignored": "Commande ignorée", "proposal_refused": "Proposition refusée", "desadv_hidden": "DESADV masqué"}


def _one_article(cells) -> str | None:
    ids = {c.article_id for c in cells}
    return ids.pop() if len(ids) == 1 else None


# ---------------------------------------------------------------- adjustments
@router.get("/adjustments", response_model=list[S.AdjustmentOut])
def list_adjustments(article_id: str | None = None, session: Session = Depends(session_dep)):
    q = select(AppAdjustment).order_by(AppAdjustment.article_id, AppAdjustment.date)
    if article_id:
        q = q.where(AppAdjustment.article_id == article_id)
    return session.scalars(q).all()


@router.put("/adjustments", response_model=S.AdjustmentOut | None)
def set_adjustment(body: S.AdjustmentIn, request: Request, response: Response, ctx: AppContext = Depends(ctx_dep),
                   session: Session = Depends(session_dep), user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Set the adjustment of one day from a signed quantity or an arithmetic expression (``-50``,
    ``2*600-50``…) ; any date ; blank or 0 clears the cell ; ``expression`` omitted changes the note only."""
    _check_article(ctx, body.article_id)
    access.require_article(body.article_id)
    rec = actions.Recorder(session, user, actions.group_of(request))
    rec.before("adjustment", {"article_id": body.article_id, "date": body.date})
    try:
        row = mrp_service.set_adjustment(session, user, body.article_id, body.date, body.expression, body.note)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    rec.done("adjustment", _cell_label("Ajustement", body.article_id, body.date, body.expression), body.article_id, response,
             group_label="Ajustements : {n} cellules")
    session.commit()
    ctx.bump()
    return row


@router.put("/adjustments/batch", response_model=list[S.AdjustmentOut])
def set_adjustments(body: S.AdjustmentBatchIn, request: Request, response: Response, ctx: AppContext = Depends(ctx_dep),
                    session: Session = Depends(session_dep), user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Several adjustment cells in one transaction (fill handle of the grid)."""
    out = []
    rec = actions.Recorder(session, user, actions.group_of(request))
    for c in body.cells:
        _check_article(ctx, c.article_id)
        access.require_article(c.article_id)
        rec.before("adjustment", {"article_id": c.article_id, "date": c.date})
        try:
            row = mrp_service.set_adjustment(session, user, c.article_id, c.date, c.expression, c.note)
        except ValueError as exc:
            session.rollback()
            raise HTTPException(422, f"{c.date} : {exc}")
        if row is not None:
            out.append(row)
    aid = _one_article(body.cells)
    rec.done("adjustment", f"Ajustements {aid or ''} : {len(body.cells)} cellules".replace("  ", " "), aid, response,
             group_label="Ajustements : {n} cellules")
    session.commit()
    ctx.bump()
    return out


@router.delete("/adjustments/{cell_id}", status_code=204)
def delete_adjustment(cell_id: str, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                      user: str = Depends(current_user), access: Access = Depends(access_dep)):
    row = session.get(AppAdjustment, cell_id)
    if row is None:
        raise HTTPException(404, "Cellule inconnue")
    access.require_article(row.article_id)
    rec = actions.Recorder(session, user)
    rec.before("adjustment", {"article_id": row.article_id, "date": row.date})
    audit(session, user, "delete", "adjustment", row.id, row.article_id, {"date": str(row.date), "qty": row.qty})
    session.delete(row)
    rec.done("adjustment", _cell_label("Ajustement", row.article_id, row.date, ""), row.article_id, response)
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
def set_plan_cell(body: S.PlanCellIn, request: Request, response: Response, ctx: AppContext = Depends(ctx_dep),
                  session: Session = Depends(session_dep), user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Set the planned delivery of one supplier on one day (quantity or expression, 0 = nothing
    expected) ; blank = back to the ERP ; ``expression`` omitted changes the note only.  Dates before
    the reference day are refused (the past is not planned)."""
    _check_article(ctx, body.article_id)
    access.require_article(body.article_id)
    result = mrp_service.compute(ctx, session, article_ids=[body.article_id])
    if body.date < result.as_of:
        raise HTTPException(422, f"Le plan se saisit à partir de la date de référence ({result.as_of.isoformat()}) ; "
                                 "pour le passé, seuls les ajustements sont possibles")
    rec = actions.Recorder(session, user, actions.group_of(request))
    rec.before("plan_cell", {"article_id": body.article_id, "supplier_id": body.supplier_id or "", "date": body.date})
    try:
        row = mrp_service.set_plan_cell(session, user, body.article_id, body.supplier_id, body.date, body.expression, body.note)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    rec.done("plan_cell", _cell_label("Quantité à retenir", body.article_id, body.date, body.expression, body.supplier_id), body.article_id,
             response, group_label="Quantité à retenir : {n} cellules")
    session.commit()
    ctx.bump()
    return row


@router.put("/plan/batch", response_model=list[S.PlanCellOut])
def set_plan_cells(body: S.PlanCellBatchIn, request: Request, response: Response, ctx: AppContext = Depends(ctx_dep),
                   session: Session = Depends(session_dep), user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Several plan cells in one transaction (fill handle of the grid) ; past dates are skipped."""
    as_of: dict[str, dt.date] = {}
    out = []
    rec = actions.Recorder(session, user, actions.group_of(request))
    for c in body.cells:
        _check_article(ctx, c.article_id)
        access.require_article(c.article_id)
        if c.article_id not in as_of:
            as_of[c.article_id] = mrp_service.compute(ctx, session, article_ids=[c.article_id]).as_of
        if c.date < as_of[c.article_id]:
            continue
        rec.before("plan_cell", {"article_id": c.article_id, "supplier_id": c.supplier_id or "", "date": c.date})
        try:
            row = mrp_service.set_plan_cell(session, user, c.article_id, c.supplier_id, c.date, c.expression, c.note)
        except ValueError as exc:
            session.rollback()
            raise HTTPException(422, f"{c.date} : {exc}")
        if row is not None:
            out.append(row)
    aid = _one_article(body.cells)
    rec.done("plan_cell", f"Quantité à retenir {aid or ''} : {len(body.cells)} cellules".replace("  ", " "), aid, response,
             group_label="Quantité à retenir : {n} cellules")
    session.commit()
    ctx.bump()
    return out


@router.delete("/plan/{cell_id}", status_code=204)
def delete_plan_cell(cell_id: str, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                     user: str = Depends(current_user), access: Access = Depends(access_dep)):
    row = session.get(AppPlanCell, cell_id)
    if row is None:
        raise HTTPException(404, "Cellule inconnue")
    access.require_article(row.article_id)
    rec = actions.Recorder(session, user)
    rec.before("plan_cell", {"article_id": row.article_id, "supplier_id": row.supplier_id, "date": row.date})
    audit(session, user, "delete", "plan_cell", row.id, row.article_id, {"date": str(row.date), "supplier_id": row.supplier_id})
    session.delete(row)
    rec.done("plan_cell", _cell_label("Quantité à retenir", row.article_id, row.date, "", row.supplier_id), row.article_id, response)
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
def toggle_flag(body: S.FlagIn, request: Request, response: Response, ctx: AppContext = Depends(ctx_dep),
                session: Session = Depends(session_dep), user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """One click: set the flag when absent (returns it), remove it when present (returns null).
    ``order_ignored`` needs a date on/after the reference day (the past is not planned)."""
    _check_article(ctx, body.article_id)
    access.require_article(body.article_id)
    if body.kind == "order_ignored":
        as_of = mrp_service.compute(ctx, session, article_ids=[body.article_id]).as_of
        if body.date < as_of:
            raise HTTPException(422, f"Seules les commandes fermes à partir de la date de référence ({as_of.isoformat()}) peuvent être ignorées")
    rec = actions.Recorder(session, user, actions.group_of(request))
    rec.before("flag", {"kind": body.kind, "article_id": body.article_id, "supplier_id": body.supplier_id or "", "date": body.date})
    row = mrp_service.toggle_flag(session, user, body.article_id, body.supplier_id, body.date, body.kind, body.qty, body.note)
    rec.done("flag", f"{FLAG_LABELS.get(body.kind, body.kind)} {body.article_id}" + (f" · {body.supplier_id}" if body.supplier_id else "")
             + f" · {_d(body.date)}" + (" : retiré" if row is None else ""), body.article_id, response)
    session.commit()
    ctx.bump()
    return row


@router.delete("/flags/{flag_id}", status_code=204)
def delete_flag(flag_id: str, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                user: str = Depends(current_user), access: Access = Depends(access_dep)):
    row = session.get(AppCellFlag, flag_id)
    if row is None:
        raise HTTPException(404, "Drapeau inconnu")
    access.require_article(row.article_id)
    rec = actions.Recorder(session, user)
    rec.before("flag", {"kind": row.kind, "article_id": row.article_id, "supplier_id": row.supplier_id, "date": row.date})
    audit(session, user, "delete", row.kind, row.id, row.article_id, {"date": str(row.date), "supplier_id": row.supplier_id})
    session.delete(row)
    rec.done("flag", f"{FLAG_LABELS.get(row.kind, row.kind)} {row.article_id}" + (f" · {row.supplier_id}" if row.supplier_id else "")
             + f" · {_d(row.date)} : retiré", row.article_id, response)
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
