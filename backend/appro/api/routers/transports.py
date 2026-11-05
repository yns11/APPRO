"""« Planning transport » : the firm orders of each supplier day by day, the transports (trucks) expected for them
(services/transports.py) and the automatic trucks (services/transports_auto.py).  The application creates, changes
and cancels the trucks ; the transport tool (ATR) notifies the suppliers from the CSV.

Every route is reserved, while the automatic trucks are tried out, to the users of ``APPRO_TRANSPORT_USERS``
(``Access.can_transports``) ; the supplier portal and the grid marks go through the services directly.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...data.assembler import erp_dataset
from ...data.store import AppCellFlag, AppTransport
from ...engine.calendar import iso_week_monday
from ...engine.models import CellFlag
from ...engine.runner import resolve_as_of
from ...services import actions, mrp_service
from ...services import transports as T
from ...services import transports_auto as A
from ...services.access import Access
from ...services.context import AppContext
from .. import schemas as S
from ..deps import access_dep, ctx_dep, current_user, session_dep
from ..fastjson import json_response


def transports_access(access: Access = Depends(access_dep)) -> Access:
    access.require_transports()
    return access


router = APIRouter(prefix="/api/transports", tags=["transports"], dependencies=[Depends(transports_access)])


def _as_of(ctx: AppContext, session: Session, ds) -> dt.date:
    return resolve_as_of(ds, mrp_service.build_params(ctx, session))


def _names(ctx: AppContext) -> dict[str, str]:
    df = ctx.table("ref_suppliers")
    return dict(zip(df["supplier_id"], df["name"]))


def _carriers(ctx: AppContext) -> dict[str, list[str]]:
    return T.carriers_of(ctx.table("ref_carriers").to_dict("records"))


def _dock_closed(ctx: AppContext) -> dict[str, set[dt.date]]:
    return A.dock_closures(ctx.table("ref_calendars").to_dict("records"))


def _dataset(ctx: AppContext, session: Session, planner: str | None = None):
    """The ERP dataset with the « commande ignorée » flags of the planners (an ignored order rides no truck)."""
    ds = erp_dataset(ctx.table, planner=planner)
    ids = {a.article_id for a in ds.articles}
    for f in session.scalars(select(AppCellFlag).where(AppCellFlag.kind == "order_ignored")):
        if f.article_id in ids:
            ds.flags.append(CellFlag(f.article_id, f.supplier_id or None, f.date, f.kind, f.qty))
    return ds


def _sync(ctx: AppContext, session: Session):
    """The whole dataset, the rules and today, after the reconciliation of the automatic trucks for the current data
    version (a no-op when it already ran)."""
    ds = _dataset(ctx, session)
    params = mrp_service.build_params(ctx, session)
    as_of = resolve_as_of(ds, params)
    if A.ensure_reconciled(ctx, session, ds, params, as_of, _carriers(ctx), _dock_closed(ctx)):
        ds = _dataset(ctx, session)
    return ds, params, as_of


def _pack(ctx: AppContext, rows: list[AppTransport]) -> dict:
    names = _names(ctx)
    return {"transports": [T.transport_out(t, names) for t in rows], "csv": T.atr_csv(rows, names), "csv_filename": T.csv_filename(rows)}


def _open_qty(ds) -> dict[str, float]:
    """ERP open quantity per firm order (a cancelled split truck gives its orders back, never beyond it)."""
    return {o.order_id: float(o.qty_open if o.qty_open is not None else o.qty_ordered) for o in ds.orders if o.order_type.value == "FIRM"}


def _rows(session: Session, ids: list[str]) -> list[AppTransport]:
    rows = [session.get(AppTransport, i) for i in dict.fromkeys(ids)]
    missing = [i for i, r in zip(dict.fromkeys(ids), rows) if r is None]
    if missing:
        raise HTTPException(404, f"Transport(s) inconnu(s) : {', '.join(missing)}")
    return [r for r in rows if r is not None]


def _one(session: Session, transport_id: str) -> AppTransport:
    t = session.get(AppTransport, transport_id)
    if t is None:
        raise HTTPException(404, f"Transport inconnu : {transport_id}")
    return t


def _csv_response(content: str, filename: str) -> Response:
    return Response(content.encode("utf-8-sig"), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


def _out(ctx: AppContext, t: AppTransport) -> dict:
    return S.TransportOut(**T.transport_out(t, _names(ctx))).model_dump(mode="json")


# ----------------------------------------------------------------------------- planning
@router.get("/planning", response_model=S.PlanningResponse)
def planning(planner: str | None = None, start: dt.date | None = None, weeks: int = Query(2, ge=1, le=12), scope: str = Query("main", pattern="^(main|maf)$"),
             ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """Suppliers (rows) × working days (columns) of the window (default : from the Monday of the current
    week, two weeks : the planning horizon) : the firm ERP orders of the perimeter, with the transports created (by
    hand or by the application).  ``scope=maf`` is the shuttle planning of the MAF Hambach suppliers : every planner
    sees every supplier there.  The automatic trucks are reconciled first when the data changed."""
    full, params, as_of = _sync(ctx, session)
    ds = full if scope == "maf" or not planner else _dataset(ctx, session, planner)
    first = start or iso_week_monday(as_of)
    out = T.planning(ds, session, first, weeks, ctx.settings.holiday_dates, as_of, scope, _carriers(ctx), A.slot_grid(params))
    closed = _dock_closed(ctx)
    out["capacity"] = max(int(params.dock_slot_capacity or 1), 1)
    out["closed_docks"] = {d.isoformat(): [dock for dock in T.DOCKS if d in closed.get(dock.upper(), set())] for d in out["days"] if any(d in closed.get(dock.upper(), set()) for dock in T.DOCKS)}
    return json_response(S.PlanningResponse(**out).model_dump(mode="json"))


@router.get("/docks", response_model=S.DockDayOut)
def docks(date: dt.date, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """Docks × slots of one day : the planned trucks (shuttles included), the closed docks, the trucks off the grid."""
    ds, params, _as_of = _sync(ctx, session)
    return json_response(S.DockDayOut(**A.dock_day(session, ds, params, date, _dock_closed(ctx))).model_dump(mode="json"))


# ----------------------------------------------------------------------------- CSV and lists
@router.get("/atr.csv")
def atr_csv_upcoming(transport_id: list[str] | None = Query(None), ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """The **global CSV for the transport tool** : every planned transport dated today or later, every supplier
    and every planner (one import into the tool instead of one per transport) ; or, with ``transport_id``
    repeated, the selected transports only."""
    if transport_id:
        rows = _rows(session, transport_id)
        return _csv_response(T.atr_csv(rows, _names(ctx)), T.csv_filename(rows))
    _ds, _params, as_of = _sync(ctx, session)
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
    """Create one transport per slot (date, time, docks) for the selected firm orders of one supplier (or, with
    ``maf``, one shuttle for orders of several MAF Hambach suppliers, open to every planner) : the identifiers
    ``EM…`` are drawn from the shared counter ; the answer carries the CSV for the transport tool.  Journalised, not
    undoable.  A supplier in « Camions automatiques » gets his trucks from the application : the manual creation is
    refused."""
    access.require_write()
    ds = erp_dataset(ctx.table)
    if not body.maf:
        sp = next((s for s in ds.suppliers if s.supplier_id == body.supplier_id), None)
        if sp is not None and sp.transport_auto:
            raise HTTPException(422, f"{sp.supplier_id} : camions automatiques, l'application crée les transports (scindez ou déplacez depuis la cellule)")
        for oid in body.order_ids:
            o = next((x for x in ds.orders if x.order_id == oid), None)
            if o is not None and not (access.is_manager or access.can_edit_article(o.article_id)):
                access.require_article(o.article_id)
    try:
        rows = T.create_transports(session, user, ds, body.supplier_id, body.order_ids, [s.model_dump() for s in body.slots], body.carrier, body.note, maf=body.maf)
    except T.TransportError as exc:
        raise HTTPException(422, str(exc))
    for t in rows:
        t.origin, t.pinned = A.MANUAL, True
    ids = ", ".join(t.transport_id for t in rows)
    who = T.MAF_LABEL if body.maf else _names(ctx).get(body.supplier_id or "", body.supplier_id or "")
    actions.record_fixed(session, user, "transport", f"Transport {ids} : {who}, {len(body.order_ids)} commande(s), {rows[0].date:%d/%m/%Y}", None, response)
    ctx.commit(session)
    return json_response(S.TransportPackOut(**_pack(ctx, rows)).model_dump(mode="json"), status_code=201)


@router.post("/special", response_model=S.TransportPackOut, status_code=201)
def create_special(body: S.TransportSpecialIn, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                   user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """A truck without order : an **EV return** (``kind = ev`` : the supplier announced he comes to take his empty
    packaging back ; identifier ``EM0001EV`` from its own counter) or a **one-off truck** (``kind = ponctuel`` :
    anything else).  Pinned, never touched by the reconciliation.  Journalised, not undoable."""
    access.require_write()
    ds = erp_dataset(ctx.table)
    try:
        t = T.create_special(session, user, ds, body.kind, body.supplier_id, body.date, body.time, body.docks, body.carrier, body.note)
    except T.TransportError as exc:
        raise HTTPException(422, str(exc))
    label = "Retour EV" if body.kind == T.MODE_EV else "Camion ponctuel"
    actions.record_fixed(session, user, "transport", f"{label} {t.transport_id} : {_names(ctx).get(body.supplier_id, body.supplier_id)}, {t.date:%d/%m/%Y} {t.time}", None, response)
    ctx.commit(session)
    return json_response(S.TransportPackOut(**_pack(ctx, [t])).model_dump(mode="json"), status_code=201)


@router.post("/pack", response_model=S.TransportPackOut)
def pack_many(body: S.TransportIdsIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """The CSV of several transports at once."""
    return json_response(S.TransportPackOut(**_pack(ctx, _rows(session, body.transport_ids))).model_dump(mode="json"))


@router.post("/cancel", response_model=list[S.TransportOut])
def cancel_many(body: S.TransportIdsIn, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Cancel the selected transports in one go (kept, status « cancelled ») ; a truck of a split family gives its
    orders back to its sibling of the day ; an automatic truck is not born again on that day unless a new order
    appears."""
    access.require_write()
    rows = _rows(session, body.transport_ids)
    done = [t for t in rows if t.status != "cancelled"]
    cap = _open_qty(erp_dataset(ctx.table)) if done else {}
    for t in done:
        A.cancel_transport(session, user, t, cap)
    if done:
        actions.record_fixed(session, user, "transport", f"Transport(s) annulé(s) : {', '.join(t.transport_id for t in done)}", None, response)
        ctx.commit(session)
    names = _names(ctx)
    return json_response([S.TransportOut(**T.transport_out(t, names)).model_dump(mode="json") for t in rows])


# ----------------------------------------------------------------------------- one transport
@router.get("/{transport_id}/atr.csv")
def transport_csv(transport_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    t = _one(session, transport_id)
    return _csv_response(T.atr_csv([t], _names(ctx)), T.csv_filename([t]))


@router.delete("/{transport_id}", response_model=S.TransportOut)
def cancel(transport_id: str, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
           user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Cancel a transport (kept, status « cancelled ») : the planner removes it from the transport tool himself.  A
    truck of a split family (``EM00123B`` while ``EM00123A`` runs the same day) gives its orders back to its sibling."""
    access.require_write()
    t = _one(session, transport_id)
    if t.status != "cancelled":
        into = A.cancel_transport(session, user, t, _open_qty(erp_dataset(ctx.table)))
        actions.record_fixed(session, user, "transport", f"Transport {transport_id} annulé" + (f" : {into.transport_id} reprend ses commandes" if into else ""), None, response)
        ctx.commit(session)
    return json_response(_out(ctx, t))


def _action(fn, session: Session, ctx: AppContext, user: str, response: Response, label: str):
    try:
        out = fn()
    except A.ActionError as exc:
        raise HTTPException(422, str(exc))
    actions.record_fixed(session, user, "transport", label, None, response)
    ctx.commit(session)
    return out


@router.post("/{transport_id}/move", response_model=S.TransportOut)
def move(transport_id: str, body: S.TransportMoveIn, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
         user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Change the day, the hour or the docks of a truck : it is pinned (the scheduler never moves it again) and its
    « à vérifier » is cleared."""
    access.require_write()
    t = _one(session, transport_id)
    _action(lambda: A.move_transport(session, user, t, body.date, body.time, body.docks), session, ctx, user, response, f"Transport {transport_id} déplacé")
    return json_response(_out(ctx, t))


@router.post("/{transport_id}/split", response_model=list[S.TransportOut])
def split(transport_id: str, body: S.TransportSplitIn, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
          user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Some orders of the truck leave for a second truck of the same day (same number, next letter ; slot given or the
    nearest free one)."""
    access.require_write()
    t = _one(session, transport_id)
    params = mrp_service.build_params(ctx, session)
    if not body.items and not body.order_ids:
        raise HTTPException(422, "Indiquez les commandes (et quantités) du second camion")
    new = _action(lambda: A.split_transport(session, user, t, body.moves(), params, body.time, body.docks, _dock_closed(ctx)), session, ctx, user, response,
                  f"Transport {transport_id} scindé")
    return json_response([_out(ctx, t), _out(ctx, new)])


@router.post("/{transport_id}/note", response_model=S.TransportOut)
def note(transport_id: str, body: S.TransportNoteIn, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
         user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """A note of the planner on the truck."""
    access.require_write()
    t = _one(session, transport_id)
    _action(lambda: A.set_note(session, user, t, body.note), session, ctx, user, response, f"Transport {transport_id} : note")
    return json_response(_out(ctx, t))


@router.post("/{transport_id}/seen", response_model=S.TransportOut)
def seen(transport_id: str, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
         user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """« Vu » : the planner checked the truck, the red mark goes."""
    access.require_write()
    t = _one(session, transport_id)
    _action(lambda: A.clear_check(session, user, t), session, ctx, user, response, f"Transport {transport_id} vérifié")
    return json_response(_out(ctx, t))
