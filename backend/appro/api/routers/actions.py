"""Annuler / rétablir : the journal of user gestures (services/actions.py)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...services import actions
from ...services.access import Access
from ...services.context import AppContext
from .. import schemas as S
from ..deps import access_dep, ctx_dep, current_user, session_dep

router = APIRouter(prefix="/api/actions", tags=["actions"])


@router.get("", response_model=list[S.ActionOut])
def list_actions(user: str | None = None, article_id: str | None = None, mine: bool = False, limit: int = 50,
                 session: Session = Depends(session_dep), actor: str = Depends(current_user),
                 access: Access = Depends(access_dep)):
    """The actions of the last 7 days, newest first, with their state and – when a touched row has been
    changed since – « modifié depuis par X »."""
    out = actions.list_actions(session, actor, access.is_admin, user=user, article_id=article_id, mine=mine, limit=limit)
    session.commit()     # the purge
    return out


def _run(fn, ctx: AppContext, session: Session, actor: str, access: Access, kind: str) -> S.ActionResultOut:
    try:
        done = fn()
    except actions.ActionError as exc:
        session.rollback()
        raise HTTPException(exc.status, str(exc))
    session.commit()
    ctx.bump()
    return S.ActionResultOut(kind=kind, actions=[S.ActionOut(**actions.to_out(session, a, actor, access.is_admin))
                                                 for a in done], cells=actions.touched_cells(done))


@router.post("/undo-last", response_model=S.ActionResultOut)
def undo_last(ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
              actor: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Ctrl+Z : undo the caller's most recent action."""
    return _run(lambda: actions.undo_last(session, actor), ctx, session, actor, access, "undo")


@router.post("/redo-last", response_model=S.ActionResultOut)
def redo_last(ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
              actor: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Ctrl+Y : redo the action the caller undid last."""
    return _run(lambda: actions.redo_last(session, actor), ctx, session, actor, access, "redo")


@router.post("/{action_id}/undo", response_model=S.ActionResultOut)
def undo(action_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
         actor: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Undo this action and, first, every newer applied action of the same user (« annuler jusqu'ici ») ;
    all or nothing.  Own actions, or anyone's for an administrator."""
    return _run(lambda: actions.undo(session, actor, action_id, access.is_admin), ctx, session, actor, access, "undo")


@router.post("/{action_id}/redo", response_model=S.ActionResultOut)
def redo(action_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
         actor: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Redo this action and, first, every older undone action of the same user."""
    return _run(lambda: actions.redo(session, actor, action_id, access.is_admin), ctx, session, actor, access, "redo")
