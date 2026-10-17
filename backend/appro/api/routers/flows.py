"""Supply flows of the day (cockpit tab « Flux d'approvisionnement ») and the search page."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ...data.assembler import erp_dataset
from ...services import mrp_service
from ...services.context import AppContext
from .. import presenters as P
from .. import schemas as S
from ..deps import ctx_dep, session_dep
from ..fastjson import json_response

router = APIRouter(prefix="/api", tags=["flows"])

SEARCH_LIMIT = 5000


def _names(ctx: AppContext) -> dict[str, str]:
    df = ctx.table("ref_suppliers")
    return dict(zip(df["supplier_id"], df["name"]))


@router.get("/flows", response_model=S.FlowsResponse)
def flows(planner: str | None = None, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """Eight flows, each a counter and its lines : à commander (urgent proposals, refused ones flagged),
    commandé (firm slots appeared today), en transit (DESADV not received), à traiter (DESADV in error
    or processed without entry journal), à réceptionner (firm slots due today), reçu (receipts of the
    day), en retard (supplier backlog), à valider (registered receipts waiting for validation)."""
    result = mrp_service.compute(ctx, session, planner=planner)
    ds = erp_dataset(ctx.table, planner=planner)
    return json_response(P.flows_out(result, ds, _names(ctx), planner).model_dump(mode="json"))


@router.get("/search", response_model=S.SearchResponse)
def search(kind: Literal["receipts", "orders", "desadv", "pending"] = "receipts", q: str | None = None,
           date_from: dt.date | None = None, date_to: dt.date | None = None, planner: str | None = None,
           supplier_id: str | None = None, article_id: str | None = None, limit: int = Query(SEARCH_LIMIT, ge=1, le=SEARCH_LIMIT),
           ctx: AppContext = Depends(ctx_dep)):
    """Search page : one kind of line at a time, free text (« ; » = or) over every text field, date range."""
    ds = erp_dataset(ctx.table, planner=planner, article_ids=[article_id] if article_id else None)
    rows = P.search_rows(ds, _names(ctx), kind)
    if supplier_id:
        rows = [r for r in rows if (r.get("supplier_id") or "") == supplier_id]
    if date_from:
        rows = [r for r in rows if r["date"] and r["date"] >= date_from]
    if date_to:
        rows = [r for r in rows if r["date"] and r["date"] <= date_to]
    if q and q.strip():
        rows = [r for r in rows if P.match_any(" ".join(str(v) for v in r.values() if v is not None), q)]
    rows.sort(key=lambda r: (r["date"] or dt.date.min, r["article_id"]), reverse=True)
    total = len(rows)
    return json_response(S.SearchResponse(kind=kind, total=total, truncated=total > limit, rows=rows[:limit]).model_dump(mode="json"))
