"""« Planning de livraisons » : the firm orders of each supplier day by day, and the transports (trucks)
announced for them (services/transports.py)."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from ...data.assembler import erp_dataset
from ...data.store import AppTransport
from ...engine.calendar import iso_week_monday
from ...engine.runner import resolve_as_of
from ...services import actions, mrp_service
from ...services import transports as T
from ...services.access import Access
from ...services.context import AppContext
from .. import schemas as S
from ..deps import access_dep, ctx_dep, current_user, session_dep
from ..fastjson import json_response

router = APIRouter(prefix="/api/transports", tags=["transports"])


def _as_of(ctx: AppContext, session: Session, ds) -> dt.date:
    return resolve_as_of(ds, mrp_service.build_params(ctx, session))


def _names(ctx: AppContext) -> dict[str, str]:
    df = ctx.table("ref_suppliers")
    return dict(zip(df["supplier_id"], df["name"]))


def _pack(ctx: AppContext, ds, rows: list[AppTransport], user: str) -> dict:
    names = _names(ctx)
    sup = next((s for s in ds.suppliers if s.supplier_id == rows[0].supplier_id), None)
    arts = {a.article_id: a for a in ds.articles}
    return {"transports": [T.transport_out(t, names) for t in rows], "csv": T.atr_csv(rows, names), "csv_filename": T.csv_filename(rows),
            "mail": T.mail_parts(rows, sup, arts, user)}


def _csv_response(content: str, filename: str) -> Response:
    return Response(content.encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/planning", response_model=S.PlanningResponse)
def planning(planner: str | None = None, start: dt.date | None = None, weeks: int = Query(2, ge=1, le=8),
             ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """Suppliers (rows) × working days (columns) of the window (default : from the Monday of the current
    week, two weeks) : the firm ERP orders of the perimeter, with the transports already created."""
    ds = erp_dataset(ctx.table, planner=planner)
    as_of = _as_of(ctx, session, ds)
    first = start or iso_week_monday(as_of)
    return json_response(S.PlanningResponse(**T.planning(ds, session, first, weeks, ctx.settings.holiday_dates, as_of)).model_dump(mode="json"))


@router.get("/atr.csv")
def atr_csv_upcoming(ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """The **global CSV for the transport tool** : every planned transport dated today or later, every supplier
    and every planner (one import into the tool instead of one per transport)."""
    ds = erp_dataset(ctx.table)
    as_of = _as_of(ctx, session, ds)
    rows = T.list_transports(session, start=as_of)
    return _csv_response(T.atr_csv(rows, _names(ctx)), f"ATR_transports_{as_of:%Y%m%d}.csv")


@router.get("", response_model=list[S.TransportOut])
def list_transports(supplier_id: str | None = None, date_from: dt.date | None = None, date_to: dt.date | None = None,
                    include_cancelled: bool = False, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """Transports of one supplier (supplier page) or of a period."""
    names = _names(ctx)
    rows = T.list_transports(session, date_from, date_to, [supplier_id] if supplier_id else None, include_cancelled)
    return json_response([S.TransportOut(**T.transport_out(t, names)).model_dump(mode="json") for t in rows])


@router.post("", response_model=S.TransportPackOut, status_code=201)
def create(body: S.TransportCreateIn, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
           user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Create one transport per slot (date, time, docks) for the selected firm orders of one supplier : the
    identifiers ``EM…`` are drawn from the shared counter ; the answer carries the CSV for the transport
    tool and the e-mail to the supplier's transport contacts.  Journalised, not undoable."""
    access.require_write()
    ds = erp_dataset(ctx.table)
    for oid in body.order_ids:
        o = next((x for x in ds.orders if x.order_id == oid), None)
        if o is not None and not (access.is_manager or access.can_edit_article(o.article_id)):
            access.require_article(o.article_id)
    try:
        rows = T.create_transports(session, user, ds, body.supplier_id, body.order_ids, [s.model_dump() for s in body.slots], body.carrier, body.note)
    except T.TransportError as exc:
        raise HTTPException(422, str(exc))
    ids = ", ".join(t.transport_id for t in rows)
    actions.record_fixed(session, user, "transport", f"Transport {ids} : {_names(ctx).get(body.supplier_id, body.supplier_id)}, "
                         f"{len(body.order_ids)} commande(s), {rows[0].date:%d/%m/%Y}", None, response)
    session.commit()
    ctx.bump()
    return json_response(S.TransportPackOut(**_pack(ctx, ds, rows, user)).model_dump(mode="json"), status_code=201)


@router.get("/{transport_id}/pack", response_model=S.TransportPackOut)
def pack(transport_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep), user: str = Depends(current_user)):
    """The CSV and the e-mail of one transport (downloaded or opened from the list, as often as needed)."""
    t = session.get(AppTransport, transport_id)
    if t is None:
        raise HTTPException(404, f"Transport inconnu : {transport_id}")
    return json_response(S.TransportPackOut(**_pack(ctx, erp_dataset(ctx.table), [t], user)).model_dump(mode="json"))


@router.get("/{transport_id}/atr.csv")
def transport_csv(transport_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    t = session.get(AppTransport, transport_id)
    if t is None:
        raise HTTPException(404, f"Transport inconnu : {transport_id}")
    return _csv_response(T.atr_csv([t], _names(ctx)), T.csv_filename([t]))


@router.delete("/{transport_id}", response_model=S.TransportOut)
def cancel(transport_id: str, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
           user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Cancel a transport (kept, status « cancelled ») : the planner tells the supplier and the transport tool himself."""
    access.require_write()
    t = session.get(AppTransport, transport_id)
    if t is None:
        raise HTTPException(404, f"Transport inconnu : {transport_id}")
    if t.status != "cancelled":
        T.cancel_transport(session, user, t)
        actions.record_fixed(session, user, "transport", f"Transport {transport_id} annulé", None, response)
        session.commit()
        ctx.bump()
    return json_response(S.TransportOut(**T.transport_out(t, _names(ctx))).model_dump(mode="json"))
