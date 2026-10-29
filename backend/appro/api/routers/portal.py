"""« Espace fournisseur » : what a supplier's transport contact sees once signed in with one of the
e-mails of ``ref_suppliers.transport_contact`` — his firm orders in progress and their transports, the
EDI forecasts (DELFOR) attributed to him, his backlog, his despatch advices (DESADV) and his receipts,
with their KPI.  Nothing else of the application is reachable (``supplier_gate`` in ``main.py``).
A manager or an administrator may preview the portal of a supplier (``supplier_id``)."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ...data.assembler import erp_dataset
from ...engine.runner import resolve_as_of
from ...services import mrp_service
from ...services import transports as T
from ...services.access import Access
from ...services.context import AppContext
from .. import presenters as P
from .. import schemas as S
from ..deps import access_dep, ctx_dep, session_dep
from ..fastjson import json_response

router = APIRouter(prefix="/api/portal", tags=["portal"])


@router.get("/overview", response_model=S.PortalResponse)
def overview(supplier_id: list[str] | None = Query(None), ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep),
             access: Access = Depends(access_dep)):
    if access.is_supplier:
        sids = list(access.supplier_ids)
    elif access.is_manager and supplier_id:
        sids = list(supplier_id)
    else:
        raise HTTPException(403, "Espace fournisseur : réservé aux contacts transport des fournisseurs (aperçu : manager ou administrateur avec supplier_id)")
    ds = erp_dataset(ctx.table)
    as_of = resolve_as_of(ds, mrp_service.build_params(ctx, session))
    sups = [s for s in ds.suppliers if s.supplier_id in sids]
    names = {s.supplier_id: s.name for s in ds.suppliers}
    by_order = T.transports_by_order(session)
    transports = [T.transport_out(t, names) for t in T.list_transports(session, supplier_ids=sids)]
    mine = lambda rows: [r for r in rows if (r.get("supplier_id") or "") in sids]  # noqa: E731

    orders_all = mine(P.search_rows(ds, names, "orders"))
    for r in orders_all:
        r["transports"] = by_order.get(r["order_id"], [])
        r["status"] = ("en retard" if r["date"] < as_of else "aujourd'hui" if r["date"] == as_of else "à venir") if r["qty_open"] > 1e-9 else "soldée"
    firm = [r for r in orders_all if r["order_type"] == "FIRM"]
    open_orders = sorted([r for r in firm if r["qty_open"] > 1e-9], key=lambda r: (r["date"], r["article_id"]))
    backlog = [r for r in open_orders if r["date"] < as_of]
    forecasts = sorted([r for r in orders_all if r["order_type"] != "FIRM" and r["date"] >= as_of], key=lambda r: (r["date"], r["article_id"]))
    desadv = sorted(mine(P.search_rows(ds, names, "desadv")), key=lambda r: (r["date"], r["article_id"]), reverse=True)
    receipts = sorted(mine(P.search_rows(ds, names, "receipts")), key=lambda r: (r["date"], r["article_id"]), reverse=True)
    for r in desadv:
        r["status"] = "reçu" if r["received"] else "erreur" if (r["state"] or "").lower().startswith("err") else "en transit"

    upcoming = [r for r in open_orders if r["date"] >= as_of]
    planned = [t for t in transports if t["date"] >= as_of]
    weeks = {r["date"].isocalendar()[:2] for r in forecasts}
    kpis = S.PortalKpis(
        open_orders=len(open_orders), open_qty_lines=len(upcoming), late_orders=len(backlog), today_orders=sum(1 for r in open_orders if r["date"] == as_of),
        next_delivery=upcoming[0]["date"] if upcoming else None, transports=len(planned),
        next_transport=f"{planned[0]['transport_id']} · {planned[0]['date']:%d/%m} {planned[0]['time']}" if planned else None,
        forecast_lines=len(forecasts), forecast_weeks=len(weeks),
        desadv_open=sum(1 for r in desadv if r["status"] == "en transit"), desadv_ko=sum(1 for r in desadv if r["status"] == "erreur"),
        receipts_30d=sum(1 for r in receipts if r["date"] and r["date"] >= as_of - dt.timedelta(days=30)))
    refs = [S.SupplierRef(supplier_id=s.supplier_id, name=s.name, country=s.country, contact=s.contact, delivery_weekdays=",".join(str(d) for d in sorted(s.delivery_weekdays)),
                          lead_time_days=s.lead_time_days, active=s.active, strict_days=s.strict_days, transport_contact=s.transport_contact, docks=s.docks) for s in sups]
    out = S.PortalResponse(suppliers=refs, as_of=as_of, kpis=kpis, orders=open_orders, backlog=backlog,
                           forecasts=forecasts, transports=[S.TransportOut(**t) for t in transports], desadv=desadv, receipts=receipts)
    return json_response(out.model_dump(mode="json"))
