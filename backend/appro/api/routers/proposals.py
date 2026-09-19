"""Decisions on engine proposals: accept (→ planned order), modify (accept with changes), ignore."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...data.store import AppOrder, IgnoredProposal, audit
from ...services.context import AppContext
from .. import schemas as S
from ..deps import ctx_dep, current_user, session_dep

router = APIRouter(prefix="/api/proposals", tags=["proposals"])


@router.post("/accept", response_model=S.OrderOut, status_code=201)
def accept(body: S.ProposalDecision, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
           user: str = Depends(current_user)):
    df = ctx.source.table("ref_articles")
    row = df[df["article_id"] == body.article_id]
    if row.empty:
        raise HTTPException(404, f"Article inconnu : {body.article_id}")
    order = AppOrder(article_id=body.article_id, supplier_id=body.supplier_id, expected_date=body.delivery_date,
                     qty=body.qty, unit=row.iloc[0]["unit"] or "PCE", order_type=body.order_type, status="OPEN",
                     source="PROPOSAL", proposal_id=body.proposal_id, note=body.note, created_by=user)
    session.add(order)
    # an accepted proposal is no longer ignored
    for ig in session.scalars(select(IgnoredProposal).where(IgnoredProposal.article_id == body.article_id,
                                                            IgnoredProposal.delivery_date == body.delivery_date)):
        session.delete(ig)
    audit(session, user, "accept_proposal", "order", order.id, body.article_id, body.model_dump(mode="json"))
    session.commit()
    ctx.bump()
    return order


@router.post("/accept-batch", response_model=list[S.OrderOut], status_code=201)
def accept_batch(body: list[S.ProposalDecision], ctx: AppContext = Depends(ctx_dep),
                 session: Session = Depends(session_dep), user: str = Depends(current_user)):
    return [accept(b, ctx, session, user) for b in body]


@router.post("/ignore", response_model=dict, status_code=201)
def ignore(body: S.ProposalIgnore, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
           user: str = Depends(current_user)):
    row = IgnoredProposal(article_id=body.article_id, supplier_id=body.supplier_id, delivery_date=body.delivery_date,
                          reason=body.reason, until_date=body.until_date, created_by=user)
    session.add(row)
    audit(session, user, "ignore_proposal", "proposal", row.id, body.article_id, body.model_dump(mode="json"))
    session.commit()
    ctx.bump()
    return {"id": row.id}


@router.get("/ignored", response_model=list[dict])
def list_ignored(session: Session = Depends(session_dep)):
    return [{"id": r.id, "article_id": r.article_id, "supplier_id": r.supplier_id,
             "delivery_date": r.delivery_date.isoformat(), "reason": r.reason,
             "until_date": r.until_date.isoformat() if r.until_date else None, "created_by": r.created_by}
            for r in session.scalars(select(IgnoredProposal)).all()]


@router.delete("/ignored/{row_id}", status_code=204)
def unignore(row_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
             user: str = Depends(current_user)):
    row = session.get(IgnoredProposal, row_id)
    if row is None:
        raise HTTPException(404, "Inconnu")
    audit(session, user, "unignore_proposal", "proposal", row.id, row.article_id)
    session.delete(row)
    session.commit()
    ctx.bump()
