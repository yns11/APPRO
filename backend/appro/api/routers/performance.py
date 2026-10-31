"""Supplier performance (OTD, observed lead times), ABC / XYZ segmentation and the exception queue."""
from __future__ import annotations

import datetime as dt
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...data import reference
from ...data.assembler import erp_dataset
from ...engine.calendar import WorkCalendar
from ...engine.runner import resolve_as_of
from ...services import actions, exceptions, mrp_service, performance
from ...services.access import Access
from ...services.context import AppContext
from ..deps import access_dep, ctx_dep, current_user, session_dep
from ..fastjson import json_response

router = APIRouter(tags=["performance"])


def _frame(ctx: AppContext, session: Session, planner: str | None, article_id: str | None = None):
    ds = erp_dataset(ctx.table, planner=planner, article_ids=[article_id] if article_id else None, holidays=ctx.settings.holiday_dates)
    params = mrp_service.build_params(ctx, session)
    as_of = resolve_as_of(ds, params)
    calendar = WorkCalendar.from_spec(params.working_weekdays, ds.holidays, ds.open_days)
    names = dict(zip(ctx.table("ref_suppliers")["supplier_id"], ctx.table("ref_suppliers")["name"]))
    return ds, params, as_of, calendar, names


@router.get("/api/performance/otd")
def otd(bucket: Literal["week", "month", "quarter", "year"] = "month", date_from: dt.date | None = None, date_to: dt.date | None = None,
        supplier_id: list[str] | None = Query(None), article_id: str | None = None, planner: str | None = None,
        ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """Service of the suppliers : OTD = 30 % quantity + 50 % punctuality + 20 % DESADV per firm order slot, averaged per
    supplier and per period bucket (week, month, quarter, year) ; the weights and bands are common rules."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from doit précéder date_to")
    ds, params, as_of, calendar, names = _frame(ctx, session, planner, article_id)
    return json_response(performance.otd_report(ds, params, as_of, calendar, bucket, date_from, date_to, supplier_id or None,
                                                [article_id] if article_id else None, names))


@router.get("/api/performance/lead-times")
def lead_times(date_from: dt.date | None = None, date_to: dt.date | None = None, supplier_id: list[str] | None = Query(None),
               article_id: str | None = None, planner: str | None = None, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """Observed delays and delivery profiles per supplier (per article when one supplier or one article is chosen)."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from doit précéder date_to")
    ds, params, as_of, calendar, names = _frame(ctx, session, planner, article_id)
    return json_response(performance.lead_time_report(ds, params, as_of, calendar, date_from, date_to, supplier_id or None,
                                                      [article_id] if article_id else None, names))


@router.get("/api/performance/segmentation")
def segmentation(planner: str | None = None, weeks: int = Query(26, ge=8, le=104), ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """ABC (yearly value) × XYZ (variability of the weekly consumption) of the articles, with the suggested coverage
    target of each segment (common rule ``segment_coverage_days``)."""
    ds, params, as_of, _calendar, _names = _frame(ctx, session, planner)
    result = mrp_service.compute(ctx, session, planner=planner)
    fallback = {aid: float(ar.kpis.get("demand_horizon", 0.0)) for aid, ar in result.articles.items()}
    return json_response(performance.segmentation(ds, params, as_of, weeks, fallback))


class ApplySegmentationIn(BaseModel):
    article_ids: list[str] = Field(..., min_length=1, max_length=2000)
    planner: str | None = None


@router.post("/api/performance/segmentation/apply")
def apply_segmentation(body: ApplySegmentationIn, request: Request, response: Response, ctx: AppContext = Depends(ctx_dep),
                       session: Session = Depends(session_dep), user: str = Depends(current_user), access: Access = Depends(access_dep)):
    """Write the suggested coverage target of the segment into the articles listed (one undoable action) ; an article
    without suggestion or already at the value is skipped, an article outside the caller's rights refuses the whole call."""
    ds, params, as_of, _calendar, _names = _frame(ctx, session, body.planner)
    result = mrp_service.compute(ctx, session, planner=body.planner)
    fallback = {aid: float(ar.kpis.get("demand_horizon", 0.0)) for aid, ar in result.articles.items()}
    seg = {r["article_id"]: r for r in performance.segmentation(ds, params, as_of, 26, fallback)["rows"]}
    rows = {r["article_id"]: r for r in reference.rows_out(session, "ref_articles")}
    rec = actions.Recorder(session, user, actions.group_of(request))
    changed, skipped = [], []
    for aid in dict.fromkeys(body.article_ids):
        r, row = seg.get(aid), rows.get(aid)
        if r is None or row is None or r["suggested_coverage_days"] is None:
            skipped.append({"article_id": aid, "reason": "pas de suggestion"})
            continue
        if int(row.get("coverage_target_days") or 0) == int(r["suggested_coverage_days"]):
            skipped.append({"article_id": aid, "reason": "déjà à la valeur suggérée"})
            continue
        access.require_row("ref_articles", row)
        values = {k: v for k, v in row.items() if k not in ("updated_by", "updated_at")}
        values["coverage_target_days"] = int(r["suggested_coverage_days"])
        reference.upsert_row(session, "ref_articles", values, user, rec)
        changed.append({"article_id": aid, "from": row.get("coverage_target_days"), "to": int(r["suggested_coverage_days"]), "segment": r["segment"]})
    if changed:
        rec.done("reference", f"Segmentation : couverture cible de {len(changed)} article(s) alignée sur le segment", None, response,
                 group_label="Segmentation : {n} articles")
        ctx.commit(session)
    return {"changed": changed, "skipped": skipped}


@router.get("/api/exceptions")
def exception_queue(planner: str | None = None, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """The exception queue of the perimeter : actionable situations ranked by urgency, production at risk and criticality."""
    result = mrp_service.compute(ctx, session, planner=planner)
    ds, params, as_of, _calendar, names = _frame(ctx, session, planner)
    seg = performance.segmentation(ds, params, as_of, 26, {aid: float(ar.kpis.get("demand_horizon", 0.0)) for aid, ar in result.articles.items()})
    abc = {r["article_id"]: r["abc"] for r in seg["rows"] if r["abc"] != "–"}
    return json_response(exceptions.exception_queue(result, ds, names, abc))
