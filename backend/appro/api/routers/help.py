"""The in-app « pense-bête » (services/help_pages.py) : read by everyone, edited by administrators."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from ...services import actions, help_pages
from ...services.access import Access
from .. import schemas as S
from ..deps import access_dep, current_user, session_dep

router = APIRouter(prefix="/api/help", tags=["help"])


@router.get("/pages", response_model=list[S.HelpPageSummary])
def pages(session: Session = Depends(session_dep)):
    """The pages of the pense-bête, in reading order, with their part and whether an administrator edited them."""
    return help_pages.list_pages(session)


@router.get("/pages/{slug}", response_model=S.HelpPageOut)
def page(slug: str, session: Session = Depends(session_dep)):
    """One page : the text saved by an administrator when there is one, else the text shipped with the application."""
    out = help_pages.get_page(session, slug)
    if out is None:
        raise HTTPException(404, f"Page inconnue : {slug}")
    return out


@router.put("/pages/{slug}", response_model=S.HelpPageOut)
def save(slug: str, body: S.HelpPageIn, response: Response, session: Session = Depends(session_dep),
         user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Replace the text of a page for every user (administrators only).  Journalised, not undoable : the
    previous text is not kept ; « Version d'origine » brings the shipped text back."""
    access.require_admin("la modification du pense-bête est réservée aux administrateurs")
    try:
        out = help_pages.save_page(session, user, slug, body.html, body.title)
    except KeyError:
        raise HTTPException(404, f"Page inconnue : {slug}")
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    actions.record_fixed(session, user, "help", f"Pense-bête : page « {out['title']} » modifiée", response=response)
    session.commit()
    return out


@router.delete("/pages/{slug}", response_model=S.HelpPageOut)
def reset(slug: str, response: Response, session: Session = Depends(session_dep), user: str = Depends(current_user),
          access: Access = Depends(access_dep)):
    """Back to the text shipped with the application (administrators only)."""
    access.require_admin("la modification du pense-bête est réservée aux administrateurs")
    try:
        out = help_pages.reset_page(session, user, slug)
    except KeyError:
        raise HTTPException(404, f"Page inconnue : {slug}")
    actions.record_fixed(session, user, "help", f"Pense-bête : page « {out['title']} » rétablie", response=response)
    session.commit()
    return out
