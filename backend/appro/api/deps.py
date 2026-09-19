"""FastAPI dependencies: context, DB session, current user."""
from __future__ import annotations

from typing import Iterator

from fastapi import Depends, Request
from sqlalchemy.orm import Session

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
    """Databricks Apps forwards the identity of the signed-in user in request headers."""
    for header in ("x-forwarded-email", "x-forwarded-preferred-username", "x-forwarded-user"):
        v = request.headers.get(header)
        if v:
            return v
    return request.headers.get("x-appro-user", "local-dev")
