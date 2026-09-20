"""Net requirement run ("Calcul CBN"): proposals are written into the simulated-order cells."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ...services import mrp_service
from ...services.context import AppContext
from .. import presenters as P
from .. import schemas as S
from ..deps import ctx_dep, current_user, session_dep

router = APIRouter(prefix="/api/cbn", tags=["cbn"])


@router.post("/run", response_model=S.CbnReport)
def run(body: S.CbnRequest, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
        user: str = Depends(current_user)):
    """Recompute the net requirements of the perimeter and store them as simulated orders.

    Simulated orders typed by the planner are kept and taken into account; the cells written by
    the previous run are replaced (``reset``).  Every written cell stays editable in the grid.
    """
    try:
        result, written, removed = mrp_service.run_cbn(ctx, session, user, planner=body.planner,
                                                       article_ids=body.article_ids, scenario_id=body.scenario_id,
                                                       reset=body.reset, **body.params)
    except KeyError as exc:
        raise HTTPException(404, f"Scénario inconnu : {exc}")
    except (TypeError, ValueError) as exc:
        raise HTTPException(422, f"Paramètres invalides : {exc}")
    df = ctx.source.table("ref_suppliers")
    names = dict(zip(df["supplier_id"], df["name"]))
    items = [P.proposal_out(p, result.articles[p.article_id], names) for p in written]
    items.sort(key=lambda p: (not p.urgent, p.order_date, p.article_id))
    return S.CbnReport(as_of=result.as_of, articles=len(result.articles), proposals=len(written),
                       urgent=sum(1 for p in written if p.urgent), qty=float(sum(p.qty for p in written)),
                       removed=removed, items=items, diagnostics=result.diagnostics)
