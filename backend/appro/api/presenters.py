"""Conversion of engine results into API schemas (kept out of the routers for testability)."""
from __future__ import annotations

import datetime as dt
from typing import Any

from ..engine.calendar import iso_week_label, iso_week_monday
from ..engine.models import Alert, ArticleResult, Lane, MrpResult, Proposal, SupplierLink
from . import schemas as S

SERIES_LABELS = [
    ("demand", "Besoin"),
    ("consumed", "Consommé"),
    ("required", "Requis"),
    ("demand_plan", "Besoin (PDP seul)"),
    ("orders_firm", "Ferme"),
    ("orders_firm_hist", "Ferme (passé)"),
    ("orders_forecast", "Prévisionnel"),
    ("receipts", "Reçu"),
    ("plan", "Plan"),
    ("supply_proposed", "Proposition CBN"),
    ("adjustments", "Ajustement"),
    ("stock_erp", "Scenario ERP"),
    ("stock_plan", "Scenario Plan"),
    ("shortage_erp", "Manque ERP"),
    ("shortage_plan", "Manque Plan"),
    ("target_stock", "Stock cible"),
    ("coverage_erp", "Couverture ERP (j)"),
    ("coverage_plan", "Couverture Plan (j)"),
]
LANE_SERIES = [("orders_firm", "Ferme"), ("orders_firm_hist", "Ferme (passé)"), ("orders_forecast", "Prévisionnel"),
               ("receipts", "Reçu"), ("plan", "Plan"), ("supply_proposed", "Proposition CBN")]
FLOWS = {"demand", "consumed", "required", "demand_plan", "orders_firm", "orders_firm_hist", "orders_forecast",
         "receipts", "plan", "supply_proposed", "adjustments"}
SHORTAGES = {"shortage_erp", "shortage_plan"}


def alert_out(a: Alert, designation: str = "") -> S.AlertOut:
    return S.AlertOut(article_id=a.article_id, designation=designation, alert_type=a.alert_type.value,
                      severity=a.severity.value, scope=a.scope, message=a.message, date=a.date, value=a.value,
                      details=a.details)


def proposal_out(p: Proposal, ar: ArticleResult, supplier_names: dict[str, str]) -> S.ProposalOut:
    return S.ProposalOut(
        proposal_id=p.proposal_id, article_id=p.article_id, designation=ar.article.designation, unit=ar.article.unit,
        supplier_id=p.supplier_id, supplier_name=supplier_names.get(p.supplier_id or "", ""),
        delivery_date=p.delivery_date, order_date=p.order_date, qty=p.qty, net_requirement=p.net_requirement,
        reason=p.reason, urgent=p.urgent, lead_time_days=p.lead_time_days, moq=p.moq,
        pack_qty=p.pack_qty, projected_stock_before=p.projected_stock_before,
        projected_stock_after=p.projected_stock_after)


def link_out(l: SupplierLink, supplier_names: dict[str, str]) -> S.LinkRef:
    return S.LinkRef(article_id=l.article_id, supplier_id=l.supplier_id, supplier_name=supplier_names.get(l.supplier_id, ""),
                     moq=l.moq, pack_qty=l.pack_qty, lead_time_days=l.lead_time_days, quota_pct=l.quota_pct,
                     priority=l.priority, active=l.active)


def article_ref(ar: ArticleResult) -> S.ArticleRef:
    a = ar.article
    return S.ArticleRef(article_id=a.article_id, designation=a.designation, unit=a.unit, family=a.family,
                        planner=a.planner, coverage_target_days=a.coverage_target_days, alert_red_days=a.alert_red_days,
                        alert_yellow_days=a.alert_yellow_days, overstock_days=a.overstock_days,
                        safety_stock_qty=a.safety_stock_qty, order_cycle_days=a.order_cycle_days, active=a.active)


def sparkline(ar: ArticleResult, points: int = 18) -> list[float]:
    i0 = ar.dates.index(ar.as_of)
    n = len(ar.dates) - i0
    step = max(1, n // points)
    return [round(ar.stock_plan[i], 1) for i in range(i0, len(ar.dates), step)][:points]


def article_summary(ar: ArticleResult) -> S.ArticleSummary:
    return S.ArticleSummary(
        article_id=ar.article.article_id, designation=ar.article.designation, unit=ar.article.unit,
        planner=ar.article.planner, suppliers=[l.supplier_id or "" for l in ar.lanes if l.supplier_id],
        severity=ar.kpis.get("severity"), kpis=ar.kpis, alert_types=sorted({a.alert_type.value for a in ar.alerts}),
        sparkline=sparkline(ar))


def backlog_rows(result: MrpResult) -> list[S.BacklogRow]:
    out = []
    for ar in result.articles.values():
        for l in ar.lanes:
            if l.backlog_qty > 1e-6:
                out.append(S.BacklogRow(article_id=ar.article.article_id, designation=ar.article.designation,
                                        unit=ar.article.unit, supplier_id=l.supplier_id, supplier_name=l.name,
                                        ordered=l.backlog_ordered, received=l.backlog_received, backlog=l.backlog_qty))
    return sorted(out, key=lambda r: (-r.backlog, r.article_id))


def cockpit_kpis(result: MrpResult) -> S.CockpitKpis:
    arts = list(result.articles.values())
    alerts = [a for r in arts for a in r.alerts]
    props = [p for r in arts for p in r.proposals]
    cov = [r.kpis["coverage_plan_days"] for r in arts if r.kpis["demand_horizon"] > 0]
    stockouts_7d = 0
    for r in arts:
        d = r.kpis.get("first_stockout_plan")
        if d and (dt.date.fromisoformat(d) - result.as_of).days <= 7:
            stockouts_7d += 1
    return S.CockpitKpis(
        articles=len(arts),
        critical=sum(1 for r in arts if r.kpis.get("severity") == "critical"),
        warning=sum(1 for r in arts if r.kpis.get("severity") == "warning"),
        stockouts=sum(1 for r in arts if r.kpis.get("first_stockout_plan")),
        stockouts_7d=stockouts_7d,
        low_coverage=sum(1 for a in alerts if a.alert_type.value == "LOW_COVERAGE"),
        overstock=sum(1 for a in alerts if a.alert_type.value == "OVERSTOCK"),
        backlog_articles=sum(1 for r in arts if r.kpis["backlog_qty"] > 1e-6),
        backlog_qty=float(sum(r.kpis["backlog_qty"] for r in arts)),
        proposals=len(props),
        urgent_proposals=sum(1 for p in props if p.urgent),
        proposals_qty=float(sum(p.qty for p in props)),
        plan_articles=sum(1 for r in arts if r.kpis["plan_cell_count"] > 0),
        plan_qty=float(sum(r.kpis["plan_qty"] for r in arts)),
        open_firm_qty=float(sum(r.kpis["open_firm_qty"] for r in arts)),
        open_forecast_qty=float(sum(r.kpis["open_forecast_qty"] for r in arts)),
        avg_coverage_days=(round(sum(cov) / len(cov), 1) if cov else None),
        demand_next_30d=float(sum(r.kpis["demand_next_30d"] for r in arts)),
    )


def weekly_supply_demand(result: MrpResult, weeks: int = 12) -> list[dict[str, Any]]:
    """Portfolio-level weekly view: number of articles below target / in stockout per week."""
    arts = list(result.articles.values())
    if not arts:
        return []
    ref = arts[0]
    i0 = ref.dates.index(result.as_of)
    buckets: dict[str, dict[str, Any]] = {}
    for i in range(i0, len(ref.dates)):
        wk = iso_week_label(ref.dates[i])
        if wk not in buckets:
            if len(buckets) >= weeks:
                break
            buckets[wk] = {"week": wk, "week_start": ref.dates[i].isoformat(), "stockout_articles": 0,
                           "below_target_articles": 0, "proposals": 0, "proposed_qty": 0.0, "_last": i}
        buckets[wk]["_last"] = i
    for b in buckets.values():
        i = b["_last"]
        for r in arts:
            if r.shortage_plan[i] > 0:
                b["stockout_articles"] += 1
            elif r.stock_plan[i] < r.target_stock[i]:
                b["below_target_articles"] += 1
    for r in arts:
        for p in r.proposals:
            wk = iso_week_label(p.delivery_date)
            if wk in buckets:
                buckets[wk]["proposals"] += 1
                buckets[wk]["proposed_qty"] += p.qty
    return [{k: v for k, v in b.items() if not k.startswith("_")} for b in buckets.values()]


def period_groups(dates: list[dt.date], as_of: dt.date, granularity: str, focus_weeks: int,
                  start: dt.date | None = None) -> dict[str, list[int]]:
    """Group the day indexes into columns.

    * ``day``: one column per day ; ``week``: one column per ISO week ;
    * ``default``: one column per day for the current ISO week and the next ``focus_weeks`` weeks,
      one column per ISO week before (past) and after (far future).
    Column keys are ISO dates for day columns and ISO week labels for week columns.
    """
    groups: dict[str, list[int]] = {}
    focus_start = iso_week_monday(as_of)
    focus_end = focus_start + dt.timedelta(days=7 * (max(int(focus_weeks), 0) + 1))  # exclusive
    for i, d in enumerate(dates):
        if start and d < start:
            continue
        if granularity == "week" or (granularity == "default" and not (focus_start <= d < focus_end)):
            key = iso_week_label(d)
        else:
            key = d.isoformat()
        groups.setdefault(key, []).append(i)
    return groups


def _agg(key: str, raw: list, groups: dict[str, list[int]], lost: bool) -> list[float]:
    if key in FLOWS or (lost and key in SHORTAGES):
        return [round(float(sum(raw[i] for i in g)), 3) for g in groups.values()]
    return [round(float(raw[g[-1]]), 3) for g in groups.values()]


def series_out(ar: ArticleResult, groups: dict[str, list[int]], lost: bool) -> list[S.SeriesOut]:
    return [S.SeriesOut(key=key, label=label, values=_agg(key, getattr(ar, key), groups, lost)) for key, label in SERIES_LABELS]


def lane_out(l: Lane, groups: dict[str, list[int]]) -> S.LaneOut:
    return S.LaneOut(
        supplier_id=l.supplier_id, name=l.name,
        series=[S.SeriesOut(key=key, label=label, values=_agg(key, getattr(l, key), groups, False)) for key, label in LANE_SERIES],
        plan_typed=[any(l.plan_typed[i] for i in g) for g in groups.values()],
        backlog_ordered=l.backlog_ordered, backlog_received=l.backlog_received, backlog_qty=l.backlog_qty,
        orders=[S.OrderInfoOut(**o.__dict__) for o in l.orders])


def projection_out(ar: ArticleResult, result: MrpResult, granularity: str, supplier_names: dict[str, str],
                   programs: list[dict[str, Any]], from_date: dt.date | None = None) -> S.ProjectionResponse:
    start = from_date or (result.as_of - dt.timedelta(days=result.params.history_days))
    groups = period_groups(ar.dates, result.as_of, granularity, result.params.focus_weeks, start)
    lost = result.params.shortage_policy == "lost"
    return S.ProjectionResponse(
        article=article_ref(ar), as_of=result.as_of, granularity=granularity, periods=list(groups),
        period_start=[ar.dates[g[0]] for g in groups.values()], period_end=[ar.dates[g[-1]] for g in groups.values()],
        series=series_out(ar, groups, lost), lanes=[lane_out(l, groups) for l in ar.lanes],
        proposals=[proposal_out(p, ar, supplier_names) for p in ar.proposals],
        alerts=[alert_out(a, ar.article.designation) for a in ar.alerts],
        kpis=ar.kpis, suppliers=[link_out(l, supplier_names) for l in ar.suppliers], programs=programs,
        diagnostics=ar.diagnostics + result.diagnostics)


def grid_out(result: MrpResult, granularity: str, supplier_names: dict[str, str],
             programs_of: dict[str, list[str]], from_date: dt.date | None = None) -> S.GridResponse:
    """Multi-article supply table: every article on the same columns."""
    start = from_date or (result.as_of - dt.timedelta(days=result.params.history_days))
    arts = list(result.articles.values())
    if not arts:
        return S.GridResponse(as_of=result.as_of, granularity=granularity, periods=[], period_start=[], period_end=[],
                              articles=[], diagnostics=result.diagnostics)
    dates = arts[0].dates
    groups = period_groups(dates, result.as_of, granularity, result.params.focus_weeks, start)
    lost = result.params.shortage_policy == "lost"
    return S.GridResponse(
        as_of=result.as_of, granularity=granularity, periods=list(groups),
        period_start=[dates[g[0]] for g in groups.values()], period_end=[dates[g[-1]] for g in groups.values()],
        articles=[S.GridArticle(article=article_ref(ar), series=series_out(ar, groups, lost),
                                lanes=[lane_out(l, groups) for l in ar.lanes], kpis=ar.kpis,
                                suppliers=[link_out(l, supplier_names) for l in ar.suppliers],
                                programs=programs_of.get(ar.article.article_id, []))
                  for ar in sorted(arts, key=lambda r: r.article.article_id)],
        diagnostics=result.diagnostics)
