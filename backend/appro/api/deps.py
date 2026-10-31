"""FastAPI dependencies: context, DB session, current user."""
from __future__ import annotations

from typing import Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..services.access import Access, resolve_access
from ..services.context import AppContext, get_context


def ctx_dep() -> AppContext:
    return get_context()


def session_dep(ctx: AppContext = Depends(ctx_dep)) -> Iterator[Session]:
    session = ctx.session()
    try:
        yield session
    finally:
        session.close()


def current_user(request: Request) -> str:
    """Databricks Apps forwards the identity of the signed-in user in request headers.  Without them, the
    development fallbacks (``x-appro-user`` header, « local-dev ») only exist on the local seed source :
    on a deployed source an anonymous request is refused."""
    for header in ("x-forwarded-email", "x-forwarded-preferred-username", "x-forwarded-user"):
        v = request.headers.get(header)
        if v:
            return v
    from ..config import get_settings
    if get_settings().data_source.lower() == "local":
        return request.headers.get("x-appro-user", "local-dev")
    raise HTTPException(401, "Identité absente : l'application attend les en-têtes d'identité de Databricks Apps")


def access_dep(ctx: AppContext = Depends(ctx_dep), user: str = Depends(current_user)) -> Access:
    """Rights of the signed-in user (planners, delegations, roles – services/access.py)."""
    return resolve_access(user, ctx.table("ref_planners"), ctx.table("ref_delegations"), ctx.table("ref_articles"),
                          admins=ctx.settings.admin_emails, suppliers=ctx.table("ref_suppliers"))
