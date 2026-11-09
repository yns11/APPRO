"""« Planning transport » : the trucks expected on the docks over the current week and the next (services/transports.py,
services/transports_auto.py).  Every delivering supplier is automatic : the application creates, completes, cancels and
places the trucks ; the planners act on the trucks of **their** suppliers and on the MAF shuttles ; the transport
tool (ATR) notifies the suppliers from the CSV.

Every route is reserved, while the automatic trucks are tried out, to the users of ``APPRO_TRANSPORT_USERS``
(``Access.can_transports``) ; the supplier portal and the grid marks go through the services directly.  An
occupied slot asked by hand answers **409** with the check (``SlotCheckOut`` : who is there, the nearest free slot,
whether forcing is possible) ; the same request with ``force`` takes the slot, never beyond the hard maximum.
"""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...data.assembler import erp_dataset
from ...data.store import AppCellFlag, AppTransport
from ...engine.calendar import iso_week_monday
from ...engine.models import CellFlag, Dataset
from ...engine.runner import resolve_as_of
from ...services import actions, mrp_service, pdf_service
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


def _names(ctx: AppContext) -> dict[str, str]:
    df = ctx.table("ref_suppliers")
    return dict(zip(df["supplier_id"], df["name"]))


def _people(ctx: AppContext) -> dict[str, str]:
    """``e-mail → first name`` of the declared planners (the screens name the author of a shuttle, who cancelled)."""
    df = ctx.table("ref_planners")
    if df.empty or "email" not in df.columns:
        return {}
    return {str(e).strip().lower(): str(n) for e, n in zip(df["email"], df["name"]) if str(e).strip()}


def _carriers(ctx: AppContext) -> dict[str, list[str]]:
    return T.carriers_of(ctx.table("ref_carriers").to_dict("records"))


def _dock_closed(ctx: AppContext) -> dict[str, set[dt.date]]:
    return A.dock_closures(ctx.table("ref_calendars").to_dict("records"))


def _dataset(ctx: AppContext, session: Session, planner: str | None = None) -> Dataset:
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


def _mine(ds: Dataset, access: Access) -> set[str]:
    """The suppliers of the planner's portfolio (an active article ↔ supplier rule on one of his articles) ; a
    manager or an administrator owns every supplier."""
    if access.is_manager:
        return {s.supplier_id for s in ds.suppliers}
    planners = {a.article_id: (a.planner or "").upper() for a in ds.articles}
    return {l.supplier_id for l in ds.links if getattr(l, "active", True) and planners.get(l.article_id, "") in access.portfolio}


def _shown(ds: Dataset, access: Access, planner: str | None) -> set[str]:
    """The suppliers whose trucks are **blue** : those of the portfolio chosen at the top of the screen (``planner``),
    else the signed-in user's own (:func:`_mine`).  The colours follow the portfolio, the rights follow the e-mail."""
    if not planner:
        return _mine(ds, access)
    arts = {a.article_id for a in ds.articles if (a.planner or "").upper() == planner.upper()}
    return {l.supplier_id for l in ds.links if getattr(l, "active", True) and l.article_id in arts}


def _require_own(access: Access, t: AppTransport, mine: set[str]) -> None:
    if access.is_manager or t.mode == "maf" or t.supplier_id == T.MAF_ID or t.supplier_id in mine:
        return
    raise HTTPException(403, f"{t.transport_id} : camion d'un autre approvisionneur ; seuls ses camions et les navettes MAF se modifient")


def _pack(ctx: AppContext, ds: Dataset, rows: list[AppTransport], mine: set[str]) -> dict:
    names = _names(ctx)
    return {"transports": [T.transport_out(t, names, T.article_labels(ds), mine) for t in rows], "csv": T.atr_csv(rows, names), "csv_filename": T.csv_filename(rows)}


def _open_qty(ds: Dataset) -> dict[str, float]:
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


def _pdf_response(content: bytes, filename: str) -> Response:
    return Response(content, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="{filename}"'})


def _out(ctx: AppContext, ds: Dataset, t: AppTransport, mine: set[str]) -> dict:
    return S.TransportOut(**T.transport_out(t, _names(ctx), T.article_labels(ds), mine)).model_dump(mode="json")


def _label(access: Access) -> str:
    return access.name or (access.user or "").split("@")[0]


# ----------------------------------------------------------------------------- planning
@router.get("/planning", response_model=S.PlanningResponse)
def planning(planner: str | None = None, start: dt.date | None = None, weeks: int = Query(2, ge=1, le=12), scope: str = Query("main", pattern="^(main|maf)$"),
             ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep), access: Access = Depends(access_dep)):
    """Suppliers (rows) × working days (columns) of the window (default : from the Monday of the current week, two
    weeks : the planning horizon) : the firm ERP orders with the transports created for them.  ``scope=maf`` lists the
    MAF Hambach suppliers and their shuttles (the shuttle dialog) ; every planner sees every supplier there.  The
    automatic trucks are reconciled first when the data changed."""
    full, params, as_of = _sync(ctx, session)
    ds = full if scope == "maf" or not planner else _dataset(ctx, session, planner)
    first = start or iso_week_monday(as_of)
    out = T.planning(ds, session, first, weeks, ctx.settings.holiday_dates, as_of, scope, _carriers(ctx), A.slot_grid(params), _mine(full, access))
    closed = _dock_closed(ctx)
    out["capacity"] = A.capacity_of(params)
    out["closed_docks"] = {d.isoformat(): [dock for dock in T.DOCKS if d in closed.get(dock.upper(), set())] for d in out["days"] if any(d in closed.get(dock.upper(), set()) for dock in T.DOCKS)}
    return json_response(S.PlanningResponse(**out).model_dump(mode="json"))


def _board(ctx: AppContext, session: Session, access: Access, start: dt.date | None, weeks: int, planner: str | None = None) -> dict:
    ds, params, as_of = _sync(ctx, session)
    first = start or iso_week_monday(as_of)
    days = T.working_days(first, weeks, sorted(set(ctx.settings.holiday_dates) | set(ds.holidays)), ds.open_days)
    end = days[-1] if days else first
    mine, editable = _shown(ds, access, planner), _mine(ds, access)
    names, labels = _names(ctx), T.article_labels(ds)
    rows = [t for t in T.list_transports(session, first, end, include_cancelled=True) if t.status == "planned" or (t.cancelled_by or "") not in ("", T.APP_USER)]
    closed = _dock_closed(ctx)
    maf_day, pending = T.maf_pending(ds, session, as_of, ctx.settings.holiday_dates)
    return {"as_of": as_of, "start": first, "end": end, "days": days, "docks": list(T.DOCKS), "slots": A.slot_grid(params), "day_slots": T.day_slots(),
            "capacity": A.capacity_of(params), "hard_max": A.capacity_of(params) * A.FORCE_FACTOR,
            "closed_docks": {d.isoformat(): [dock for dock in T.DOCKS if d in closed.get(dock.upper(), set())] for d in days if any(d in closed.get(dock.upper(), set()) for dock in T.DOCKS)},
            "maf_day": maf_day, "maf_pending": pending, "user_label": _label(access), "planner": (planner or "").upper() or None,
            "transports": [T.transport_out(t, names, labels, mine, editable) for t in rows], "people": _people(ctx)}


@router.get("/board", response_model=S.BoardResponse)
def board(start: dt.date | None = None, weeks: int = Query(2, ge=1, le=4), planner: str | None = None, ctx: AppContext = Depends(ctx_dep),
          session: Session = Depends(session_dep), access: Access = Depends(access_dep)):
    """The single screen : every truck of every supplier over the window (default : the current week and the next),
    flagged **mine** (blue) when it belongs to the portfolio shown (``planner`` : the one chosen at the top of the
    screen, else the signed-in user's own) and **editable** when the signed-in user may act on it ; the trucks a person
    cancelled stay (struck in the agenda) ; the counter of the « Navette MAF » button (MAF orders of the next working
    day without shuttle)."""
    return json_response(S.BoardResponse(**_board(ctx, session, access, start, weeks, planner)).model_dump(mode="json"))


@router.get("/planning.pdf")
def planning_pdf(planner: str | None = None, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep), access: Access = Depends(access_dep)):
    """The printable planning : A4 landscape, the current week (docks × days), its working days (agenda of the
    docks), the next week and its days ; the trucks of the portfolio shown in blue, no quantity nor order number."""
    out = _board(ctx, session, access, None, 2, planner)
    return _pdf_response(pdf_service.planning_pdf(out, _label(access), out["people"]), f"planning_transport_{out['start']:%Y%m%d}.pdf")


@router.get("/packaging", response_model=list[S.PackagingLineOut])
def packaging(supplier_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """The packaging a supplier may take back (Référentiel › Emballages of the articles he supplies) : the lines of the
    EV return dialog, each with a planned quantity to type."""
    ds = _dataset(ctx, session)
    return json_response([S.PackagingLineOut(**x).model_dump(mode="json") for x in T.packaging_of(ds, ctx.table("ref_packaging").to_dict("records"), supplier_id)])


@router.get("/docks", response_model=S.DockDayOut)
def docks(date: dt.date, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """Docks × half-hour slots of one day : the planned trucks (shuttles included), the closed docks, the trucks off the grid."""
    ds, params, _as_of = _sync(ctx, session)
    return json_response(S.DockDayOut(**A.dock_day(session, ds, params, date, _dock_closed(ctx))).model_dump(mode="json"))


@router.get("/slot", response_model=S.SlotCheckOut)
def slot(date: dt.date, time: str = Query(..., pattern=S.TIME_PATTERN), dock: list[str] = Query(...), exclude: str | None = None,
         ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """Is that slot free on those docks ?  Who is there, the nearest free slot, whether forcing is possible."""
    _ds, params, _as_of = _sync(ctx, session)
    return json_response(S.SlotCheckOut(**A.slot_check(session, params, date, time, dock, exclude).to_dict()).model_dump(mode="json"))


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
                    include_cancelled: bool = False, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep), access: Access = Depends(access_dep)):
    """Transports of one supplier (supplier page) or of a period."""
    ds = _dataset(ctx, session)
    names, labels, mine = _names(ctx), T.article_labels(ds), _mine(ds, access)
    rows = T.list_transports(session, date_from, date_to, [supplier_id] if supplier_id else None, include_cancelled)
    return json_response([S.TransportOut(**T.transport_out(t, names, labels, mine)).model_dump(mode="json") for t in rows])


def _slot_errors(fn):
    """Run a planner action : an occupied slot answers 409 with the check, any other refusal 422."""
    try:
        return fn()
    except A.SlotConflict as exc:
        raise HTTPException(409, detail=exc.check.to_dict())
    except (A.ActionError, T.TransportError) as exc:
        raise HTTPException(422, str(exc))


@router.post("", response_model=S.TransportPackOut, status_code=201)
def create_shuttle(body: S.ShuttleIn, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                   user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """A **MAF shuttle** : the MAF orders ticked (any supplier, any planner) and the typed lines of the outward leg ;
    with ``return_trip`` the shuttle goes back loaded on ``return_docks`` with its own lines (no slot, no time of its
    own).  The slot is checked (409 when occupied, ``force`` to take it anyway).  Journalised, not undoable."""
    access.require_write()
    ds, params, _as_of = _sync(ctx, session)
    _slot_errors(lambda: A.ensure_slot(session, params, body.date, body.time, T.check_docks(body.docks), body.force))
    t = _slot_errors(lambda: T.create_shuttle(session, user, ds, body.date, body.time, body.docks, body.order_ids, [ln.model_dump() for ln in body.lines],
                                              body.return_trip, body.return_docks, [ln.model_dump() for ln in body.return_lines], body.carrier, body.note))
    actions.record_fixed(session, user, "transport", f"Navette MAF {t.transport_id} : {len(t.orders)} commande(s), {len(t.lines)} ligne(s), {t.date:%d/%m/%Y} {t.time}", None, response)
    ctx.commit(session)
    return json_response(S.TransportPackOut(**_pack(ctx, ds, [t], _mine(ds, access))).model_dump(mode="json"), status_code=201)


@router.post("/special", response_model=S.TransportPackOut, status_code=201)
def create_special(body: S.TransportSpecialIn, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                   user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """A truck without order : an **EV return** (``kind = ev`` : the supplier announced he comes to take his empty
    packaging back ; identifier ``EM0001EV`` from its own counter) or a **one-off truck** (``kind = ponctuel`` :
    anything else).  Never touched by the reconciliation.  Slot checked.  Journalised, not undoable."""
    access.require_write()
    ds, params, _as_of = _sync(ctx, session)
    mine = _mine(ds, access)
    if not access.is_manager and body.supplier_id not in mine:
        raise HTTPException(403, f"{body.supplier_id} : fournisseur d'un autre approvisionneur")
    _slot_errors(lambda: A.ensure_slot(session, params, body.date, body.time, T.check_docks(body.docks), body.force))
    t = _slot_errors(lambda: T.create_special(session, user, ds, body.kind, body.supplier_id, body.date, body.time, body.docks, body.carrier, body.note,
                                              [ln.model_dump() for ln in body.lines]))
    label = "Retour EV" if body.kind == T.MODE_EV else "Camion ponctuel"
    actions.record_fixed(session, user, "transport", f"{label} {t.transport_id} : {_names(ctx).get(body.supplier_id, body.supplier_id)}, {t.date:%d/%m/%Y} {t.time}", None, response)
    ctx.commit(session)
    return json_response(S.TransportPackOut(**_pack(ctx, ds, [t], mine)).model_dump(mode="json"), status_code=201)


@router.post("/pack", response_model=S.TransportPackOut)
def pack_many(body: S.TransportIdsIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep), access: Access = Depends(access_dep)):
    """The CSV of several transports at once."""
    ds = _dataset(ctx, session)
    return json_response(S.TransportPackOut(**_pack(ctx, ds, _rows(session, body.transport_ids), _mine(ds, access))).model_dump(mode="json"))


@router.post("/cancel", response_model=list[S.TransportOut])
def cancel_many(body: S.TransportIdsIn, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
                user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Cancel the selected transports in one go (kept, status « cancelled ») ; a truck of a split family gives its
    orders back to its sibling of the day ; a supplier truck is not born again on that day unless a new order
    appears.  Only the planner's own trucks and the shuttles."""
    access.require_write()
    ds = _dataset(ctx, session)
    mine = _mine(ds, access)
    rows = _rows(session, body.transport_ids)
    for t in rows:
        _require_own(access, t, mine)
    done = [t for t in rows if t.status != "cancelled"]
    cap = _open_qty(ds) if done else {}
    for t in done:
        A.cancel_transport(session, user, t, cap)
    if done:
        actions.record_fixed(session, user, "transport", f"Transport(s) annulé(s) : {', '.join(t.transport_id for t in done)}", None, response)
        ctx.commit(session)
    return json_response([_out(ctx, ds, t, mine) for t in rows])


# ----------------------------------------------------------------------------- one transport
@router.get("/{transport_id}/atr.csv")
def transport_csv(transport_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    t = _one(session, transport_id)
    return _csv_response(T.atr_csv([t], _names(ctx)), T.csv_filename([t]))


@router.get("/{transport_id}/navette.pdf")
def shuttle_pdf(transport_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep), access: Access = Depends(access_dep)):
    """The sheet of a MAF shuttle : both legs, their lines (article, description, quantity ; supplier for an order),
    signature boxes ; no order number."""
    t = _one(session, transport_id)
    if t.mode != "maf":
        raise HTTPException(422, f"{transport_id} n'est pas une navette MAF")
    ds = _dataset(ctx, session)
    out = T.transport_out(t, _names(ctx), T.article_labels(ds), _mine(ds, access))
    return _pdf_response(pdf_service.shuttle_pdf(out, _people(ctx)), f"navette_{transport_id}.pdf")


@router.get("/{transport_id}/retour-ev.pdf")
def ev_return_pdf(transport_id: str, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep), access: Access = Depends(access_dep)):
    """The **empty packaging return note** of an EV return : letterhead and SHIP FROM (settings), SHIP TO (the
    supplier and his address), one line per packaging with its planned quantity, blank quantity and weight columns to
    fill by hand at loading, notes, sender and carrier boxes."""
    t = _one(session, transport_id)
    if t.mode != T.MODE_EV:
        raise HTTPException(422, f"{transport_id} n'est pas un retour EV")
    ds = _dataset(ctx, session)
    sup = ctx.table("ref_suppliers")
    row = sup[sup["supplier_id"] == t.supplier_id]
    supplier = {"supplier_id": t.supplier_id, "name": str(row["name"].iloc[0]) if not row.empty else t.supplier_id, "address": str(row["address"].iloc[0] or "") if not row.empty and "address" in row.columns else ""}
    out = T.transport_out(t, _names(ctx), T.article_labels(ds), _mine(ds, access))
    weights = {(str(r.get("article_id") or "").strip(), str(r.get("packaging_code") or "").strip()): r.get("unit_weight_kg") for r in ctx.table("ref_packaging").to_dict("records")}
    for ln in out["lines"]:
        w = weights.get((ln["article"], ln["code"]))
        ln["unit_weight"] = "" if w in (None, "") or (isinstance(w, float) and w != w) else f"{float(w):g}"
    st = ctx.settings
    return _pdf_response(pdf_service.ev_return_pdf(out, supplier, st.company_header, st.ship_from, st.ev_return_contacts), f"retour_EV_{transport_id}.pdf")


@router.delete("/{transport_id}", response_model=S.TransportOut)
def cancel(transport_id: str, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
           user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Cancel a transport (kept, status « cancelled », struck in the agenda until the end of its day) : the planner
    removes it from the transport tool himself.  A truck of a split family (``EM00123B`` while ``EM00123A`` runs the
    same day) gives its orders back to its sibling.  Only the planner's own trucks and the shuttles."""
    access.require_write()
    t = _one(session, transport_id)
    ds = _dataset(ctx, session)
    mine = _mine(ds, access)
    _require_own(access, t, mine)
    if t.status != "cancelled":
        into = A.cancel_transport(session, user, t, _open_qty(ds))
        actions.record_fixed(session, user, "transport", f"Transport {transport_id} annulé" + (f" : {into.transport_id} reprend ses commandes" if into else ""), None, response)
        ctx.commit(session)
    return json_response(_out(ctx, ds, t, mine))


def _action(fn, session: Session, ctx: AppContext, user: str, response: Response, label: str):
    out = _slot_errors(fn)
    actions.record_fixed(session, user, "transport", label, None, response)
    ctx.commit(session)
    return out


def _owned(ctx: AppContext, session: Session, transport_id: str, access: Access) -> tuple[AppTransport, Dataset, set[str]]:
    access.require_write()
    t = _one(session, transport_id)
    ds = _dataset(ctx, session)
    mine = _mine(ds, access)
    _require_own(access, t, mine)
    return t, ds, mine


@router.post("/{transport_id}/move", response_model=S.TransportOut)
def move(transport_id: str, body: S.TransportMoveIn, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
         user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Change the day, the hour or the docks of a truck : the slot is checked (409 when occupied, ``force`` to take it
    anyway, never beyond the hard maximum) ; the truck is pinned and its « à vérifier » cleared."""
    t, ds, mine = _owned(ctx, session, transport_id, access)
    params = mrp_service.build_params(ctx, session)
    _action(lambda: A.move_transport(session, user, t, params, body.date, body.time, body.docks, body.force), session, ctx, user, response, f"Transport {transport_id} déplacé")
    return json_response(_out(ctx, ds, t, mine))


@router.post("/{transport_id}/split", response_model=list[S.TransportOut])
def split(transport_id: str, body: S.TransportSplitIn, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
          user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Some orders of the truck leave for a second truck of the same day (same number, next letter ; slot given and
    checked, or the nearest free one)."""
    t, ds, mine = _owned(ctx, session, transport_id, access)
    params = mrp_service.build_params(ctx, session)
    if not body.items and not body.order_ids:
        raise HTTPException(422, "Indiquez les articles (et quantités) du second camion")
    new = _action(lambda: A.split_transport(session, user, t, body.moves(), params, body.time, body.docks, body.force), session, ctx, user, response,
                  f"Transport {transport_id} scindé")
    return json_response([_out(ctx, ds, t, mine), _out(ctx, ds, new, mine)])


@router.post("/{transport_id}/note", response_model=S.TransportOut)
def note(transport_id: str, body: S.TransportNoteIn, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
         user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """A note of the planner on the truck."""
    t, ds, mine = _owned(ctx, session, transport_id, access)
    _action(lambda: A.set_note(session, user, t, body.note), session, ctx, user, response, f"Transport {transport_id} : note")
    return json_response(_out(ctx, ds, t, mine))


@router.post("/{transport_id}/seen", response_model=S.TransportOut)
def seen(transport_id: str, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
         user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """« Vu » : the planner checked the truck, the red mark goes."""
    t, ds, mine = _owned(ctx, session, transport_id, access)
    _action(lambda: A.clear_check(session, user, t), session, ctx, user, response, f"Transport {transport_id} vérifié")
    return json_response(_out(ctx, ds, t, mine))


@router.post("/{transport_id}/restore", response_model=S.TransportOut)
def restore(transport_id: str, body: S.ForceIn, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
            user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """A cancelled truck comes back as it was, through the occupied-slot check (409, ``force``)."""
    t, ds, mine = _owned(ctx, session, transport_id, access)
    params = mrp_service.build_params(ctx, session)
    _action(lambda: A.restore_transport(session, user, t, params, body.force), session, ctx, user, response, f"Transport {transport_id} rétabli")
    return json_response(_out(ctx, ds, t, mine))


@router.post("/{transport_id}/lines", response_model=S.TransportOut)
def lines(transport_id: str, body: S.TransportLinesIn, response: Response, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
          user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """The typed lines of a shuttle (both legs) and its return leg, or the packaging lines of an EV return, replaced as a whole."""
    t, ds, mine = _owned(ctx, session, transport_id, access)
    _action(lambda: T.set_lines(session, user, t, [ln.model_dump() for ln in body.lines], body.return_trip, body.return_docks, [ln.model_dump() for ln in body.return_lines]),
            session, ctx, user, response, f"Navette {transport_id} : lignes")
    return json_response(_out(ctx, ds, t, mine))
