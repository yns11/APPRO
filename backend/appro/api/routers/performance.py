"""Supplier performance (OTD at the day grain, observed lead times), ABC / XYZ segmentation and the exception queue."""
from __future__ import annotations

import datetime as dt
import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...data import reference
from ...data.assembler import calendar_sets, erp_dataset
from ...data.store import OtdClosing
from ...engine.calendar import WorkCalendar
from ...engine.models import EngineParams
from ...engine.runner import resolve_as_of
from ...services import actions, exceptions, mrp_service, otd, pdf_service, performance
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


def _otd_data(ctx: AppContext, session: Session) -> tuple[otd.OtdData, EngineParams]:
    """The evaluated lines of the whole data set, computed once per data version (and per day) and cached."""
    params = mrp_service.build_params(ctx, session)
    as_of = params.as_of or dt.date.today()
    closed, opened, _by_sup = calendar_sets(ctx.table("ref_calendars").to_dict("records"), [])
    holidays = set(ctx.settings.holiday_dates) | closed
    key = otd.cache_key(ctx.sync_version(session), as_of, params, holidays, opened)
    data = ctx.cache_get(key)
    if data is None:
        cal = WorkCalendar.from_spec(params.working_weekdays, holidays, opened)
        data = otd.build(ctx.table("fct_purchase_orders"), ctx.table("fct_receipts"), ctx.table("fct_desadv"), ctx.table("ref_suppliers"),
                         ctx.table("ref_article_suppliers"), ctx.table("ref_articles"), cal, as_of, params)
        ctx.cache_set(key, data, size=data.size())
    return data, params


@router.get("/api/performance/otd")
def otd_endpoint(bucket: Literal["week", "month", "quarter", "year"] = "month", date_from: dt.date | None = None, date_to: dt.date | None = None,
                 supplier_id: list[str] | None = Query(None), article_id: str | None = None, planner: str | None = None,
                 ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """Service of the suppliers per expected day (docs § 14) over whole weeks, per supplier and per period bucket."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from doit précéder date_to")
    data, params = _otd_data(ctx, session)
    return json_response(otd.otd_report(data, params, bucket, date_from, date_to, supplier_id or None, [article_id] if article_id else None, planner or None))


MONTH = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="mois au format AAAA-MM")


def _closings(session: Session, month: str) -> dict[str, OtdClosing]:
    return {c.supplier_id: c for c in session.scalars(select(OtdClosing).where(OtdClosing.month == month)).all()}


def _closing_dict(c: OtdClosing) -> dict:
    return {"closed_at": c.closed_at.isoformat(timespec="minutes"), "closed_by": c.closed_by, "otd": c.otd}


@router.get("/api/performance/otd/months")
def otd_months(ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """The months that hold evaluated days, the last closed month (default of the supplier sheet) and the months
    already frozen by a closing."""
    data, _params = _otd_data(ctx, session)
    months = sorted({otd.bucket_label(x.day, "month") for x in data.lines if x.evaluable})
    closings: dict[str, dict] = {}
    for c in session.scalars(select(OtdClosing)).all():
        d = closings.setdefault(c.month, {"closed_at": c.closed_at.isoformat(timespec="minutes"), "closed_by": c.closed_by, "suppliers": 0})
        d["suppliers"] += 1
        if c.closed_at.isoformat(timespec="minutes") > d["closed_at"]:
            d["closed_at"], d["closed_by"] = c.closed_at.isoformat(timespec="minutes"), c.closed_by
    return {"as_of": data.as_of.isoformat(), "last_closed": otd.last_closed_month(data.as_of), "months": months, "closings": closings}


@router.get("/api/performance/otd/sheet")
def otd_sheet(supplier_id: str, month: str = MONTH, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """The **supplier × month sheet** : the formula with its figures, the three indicators decomposed, the weeks of the
    month day by day, every evaluated line with the reading of its scores (live figures ; the closing, when the month
    is frozen, is recalled beside them)."""
    data, params = _otd_data(ctx, session)
    if supplier_id not in data.names:
        raise HTTPException(404, f"fournisseur inconnu : {supplier_id}")
    sh = otd.sheet(data, params, supplier_id, month)
    c = session.get(OtdClosing, (month, supplier_id))
    sh["closing"] = _closing_dict(c) if c else None
    return json_response(sh)


class CloseIn(BaseModel):
    month: str = Field(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    force: bool = False


@router.post("/api/performance/otd/close")
def otd_close(body: CloseIn, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep), user: str = Depends(current_user),
              access: Access = Depends(access_dep)):
    """**Close a month** : freeze the sheet of every supplier evaluated over it (managers and administrators ; a month
    already closed is redone by an administrator only, with ``force``)."""
    if not access.is_manager:
        raise HTTPException(403, "l'arrêté mensuel est réservé aux managers et administrateurs")
    data, params = _otd_data(ctx, session)
    if otd.month_bounds(body.month)[1] >= data.as_of:
        raise HTTPException(422, f"le mois {otd.month_label(body.month)} n'est pas terminé : dernier mois clos {otd.last_closed_month(data.as_of)}")
    existing = _closings(session, body.month)
    if existing and not body.force:
        raise HTTPException(409, f"{otd.month_label(body.month)} est déjà arrêté ({len(existing)} fournisseur(s)) ; un administrateur peut le refaire")
    if existing:
        access.require_admin("refaire un arrêté est réservé aux administrateurs")
        for c in existing.values():
            session.delete(c)
        session.flush()
    rows = otd.sheet_summary(data, params, body.month)
    now = dt.datetime.utcnow().replace(microsecond=0)
    for r in rows:
        sh = otd.sheet(data, params, r["supplier_id"], body.month)
        session.add(OtdClosing(month=body.month, supplier_id=r["supplier_id"], otd=sh["scores"]["otd"], payload=json.dumps(sh, ensure_ascii=False), closed_by=user, closed_at=now))
    ctx.commit(session)
    return {"month": body.month, "suppliers": len(rows), "closed_at": now.isoformat(timespec="minutes"), "closed_by": user}


@router.get("/api/performance/otd/arrete.pdf")
def otd_closing_pdf(month: str = MONTH, supplier_id: list[str] | None = Query(None), planner: str | None = None,
                    ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """The **monthly report** (PDF, A4) : the frozen sheets when the month is closed, the live figures otherwise
    (marked « provisoire ») ; one supplier, or the portfolio : a summary table of every supplier then one supplier
    per page."""
    data, params = _otd_data(ctx, session)
    closings = _closings(session, month)
    wanted = set(supplier_id or [])
    frozen = None
    if closings:
        sheets = [json.loads(c.payload) for c in closings.values()]
        c0 = max(closings.values(), key=lambda c: c.closed_at)
        frozen = {"closed_at": c0.closed_at, "closed_by": c0.closed_by}
    else:
        ids = sorted(wanted) if wanted else [r["supplier_id"] for r in otd.sheet_summary(data, params, month, None, planner or None)]
        sheets = [otd.sheet(data, params, sid, month) for sid in ids if sid in data.names]
    if wanted:
        sheets = [sh for sh in sheets if sh["supplier_id"] in wanted]
    if planner:
        sheets = [sh for sh in sheets if planner in sh["planners"]]
    sheets = [sh for sh in sheets if sh["scores"]["n"]]
    sheets.sort(key=lambda sh: (sh["scores"]["otd"] if sh["scores"]["otd"] is not None else 101, sh["name"]))
    if not sheets:
        raise HTTPException(404, f"aucun fournisseur évalué sur {otd.month_label(month)}")
    content = pdf_service.otd_closing_pdf(month, sheets, frozen, data.as_of, planner or None)
    suffix = f"_{sheets[0]['supplier_id']}" if len(sheets) == 1 else ""
    return Response(content, media_type="application/pdf", headers={"Content-Disposition": f'inline; filename="otd_{month}{suffix}.pdf"'})


@router.get("/api/performance/lead-times")
def lead_times(date_from: dt.date | None = None, date_to: dt.date | None = None, supplier_id: list[str] | None = Query(None),
               article_id: str | None = None, planner: str | None = None, ctx: AppContext = Depends(ctx_dep), session: Session = Depends(session_dep)):
    """Observed delays and delivery profiles per supplier (per article when one supplier or one article is chosen)."""
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from doit précéder date_to")
    data, params = _otd_data(ctx, session)
    return json_response(otd.lead_time_report(data, params, date_from, date_to, supplier_id or None, [article_id] if article_id else None, planner or None))


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
