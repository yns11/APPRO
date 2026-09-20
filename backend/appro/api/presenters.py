"""Conversion of engine results into API schemas (kept out of the routers for testability)."""
from __future__ import annotations

import datetime as dt
from typing import Any

from ..engine.calendar import iso_week_label
from ..engine.models import Alert, ArticleResult, MrpResult, Proposal, SupplierLink
from . import schemas as S

SERIES_LABELS = [
    ("demand", "Besoin"),
    ("demand_plan", "Besoin (plan seul)"),
    ("supply_firm", "Commandes fermes"),
    ("supply_forecast", "Commandes prévisionnelles (ERP)"),
    ("supply_planned", "Commandes simulées"),
    ("receipts", "Réceptions"),
    ("adjustments", "Ajustements"),
    ("stock_firm", "Stock ferme"),
    ("stock_forecast", "Stock prévisionnel"),
    ("stock_sim", "Stock simulé"),
    ("shortage_firm", "Manque ferme"),
    ("shortage_forecast", "Manque prévisionnel"),
    ("shortage_sim", "Manque simulé"),
    ("target_stock", "Stock cible"),
    ("coverage_firm", "Couverture ferme (j)"),
    ("coverage_forecast", "Couverture prévisionnelle (j)"),
    ("coverage_sim", "Couverture simulée (j)"),
    ("demand_actual_share", "Part du réel dans le besoin"),
]
FLOWS = {"demand", "demand_plan", "supply_firm", "supply_forecast", "supply_planned", "receipts", "adjustments"}
SHORTAGES = {"shortage_firm", "shortage_forecast", "shortage_sim"}


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
                        safety_stock_qty=a.safety_stock_qty, lot_policy=a.lot_policy,
                        order_cycle_days=a.order_cycle_days, active=a.active)


def sparkline(ar: ArticleResult, points: int = 18) -> list[float]:
    i0 = ar.dates.index(ar.as_of)
    n = len(ar.dates) - i0
    step = max(1, n // points)
    return [round(ar.stock_sim[i], 1) for i in range(i0, len(ar.dates), step)][:points]


def article_summary(ar: ArticleResult) -> S.ArticleSummary:
    return S.ArticleSummary(
        article_id=ar.article.article_id, designation=ar.article.designation, unit=ar.article.unit,
        planner=ar.article.planner, suppliers=sorted({l.supplier_id for l in ar.suppliers}),
        severity=ar.kpis.get("severity"), kpis=ar.kpis, alert_types=sorted({a.alert_type.value for a in ar.alerts}),
        sparkline=sparkline(ar))


def cockpit_kpis(result: MrpResult) -> S.CockpitKpis:
    arts = list(result.articles.values())
    alerts = [a for r in arts for a in r.alerts]
    cov = [r.kpis["coverage_sim_days"] for r in arts if r.kpis["demand_horizon"] > 0]
    stockouts_7d = 0
    for r in arts:
        d = r.kpis.get("first_stockout_sim")
        if d and (dt.date.fromisoformat(d) - result.as_of).days <= 7:
            stockouts_7d += 1
    return S.CockpitKpis(
        articles=len(arts),
        critical=sum(1 for r in arts if r.kpis.get("severity") == "critical"),
        warning=sum(1 for r in arts if r.kpis.get("severity") == "warning"),
        stockouts=sum(1 for r in arts if r.kpis.get("first_stockout_sim")),
        stockouts_7d=stockouts_7d,
        low_coverage=sum(1 for a in alerts if a.alert_type.value == "LOW_COVERAGE"),
        overstock=sum(1 for a in alerts if a.alert_type.value == "OVERSTOCK"),
        late_orders=sum(r.kpis["late_order_count"] for r in arts),
        sim_order_articles=sum(1 for r in arts if abs(r.kpis["open_planned_qty"]) > 1e-9),
        sim_orders_qty=float(sum(r.kpis["open_planned_qty"] for r in arts)),
        open_firm_qty=float(sum(r.kpis["open_firm_qty"] for r in arts)),
        open_forecast_qty=float(sum(r.kpis["open_forecast_qty"] for r in arts)),
        open_planned_qty=float(sum(r.kpis["open_planned_qty"] for r in arts)),
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
            if r.shortage_sim[i] > 0:
                b["stockout_articles"] += 1
            elif r.stock_sim[i] < r.target_stock[i]:
                b["below_target_articles"] += 1
    for r in arts:
        for e in r.events:
            if e.kind == "sim_order":
                wk = iso_week_label(e.date)
                if wk in buckets:
                    buckets[wk]["proposals"] += 1
                    buckets[wk]["proposed_qty"] += e.qty
    return [{k: v for k, v in b.items() if not k.startswith("_")} for b in buckets.values()]


def projection_out(ar: ArticleResult, result: MrpResult, granularity: str, supplier_names: dict[str, str],
                   programs: list[dict[str, Any]], from_date: dt.date | None = None) -> S.ProjectionResponse:
    start = from_date or (result.as_of - dt.timedelta(days=result.params.history_days))
    idxs = [i for i, d in enumerate(ar.dates) if d >= start]
    groups: dict[str, list[int]] = {}
    if granularity == "week":
        for i in idxs:
            groups.setdefault(iso_week_label(ar.dates[i]), []).append(i)
    else:
        for i in idxs:
            groups[ar.dates[i].isoformat()] = [i]
    labels = list(groups)
    starts = [ar.dates[g[0]] for g in groups.values()]
    series = []
    # a lost quantity is a flow (summed over the period); a backlog is a level (end of period)
    lost = result.params.shortage_policy == "lost"
    for key, label in SERIES_LABELS:
        raw = getattr(ar, key)
        if key in FLOWS or (lost and key in SHORTAGES):
            vals = [round(float(sum(raw[i] for i in g)), 3) for g in groups.values()]
        elif key == "demand_actual_share":
            vals = [round(float(sum(raw[i] for i in g) / len(g)), 3) for g in groups.values()]
        else:
            vals = [round(float(raw[g[-1]]), 3) for g in groups.values()]
        series.append(S.SeriesOut(key=key, label=label, values=vals))
    return S.ProjectionResponse(
        article=article_ref(ar), as_of=result.as_of, granularity=granularity, periods=labels, period_start=starts,
        series=series,
        events=[S.SupplyEventOut(**e.__dict__) for e in ar.events if e.date >= start],
        proposals=[proposal_out(p, ar, supplier_names) for p in ar.proposals],
        alerts=[alert_out(a, ar.article.designation) for a in ar.alerts],
        kpis=ar.kpis, suppliers=[link_out(l, supplier_names) for l in ar.suppliers], programs=programs,
        diagnostics=ar.diagnostics + result.diagnostics)


def compare_articles(base: MrpResult, scen: MrpResult) -> list[S.CompareArticle]:
    out = []
    for aid, b in base.articles.items():
        s = scen.articles.get(aid)
        if s is None:
            continue
        keys = ("stock_as_of_sim", "coverage_sim_days", "first_stockout_sim", "min_stock_sim", "max_shortage_sim",
                "open_planned_qty", "demand_next_30d", "severity")
        out.append(S.CompareArticle(
            article_id=aid, designation=b.article.designation, unit=b.article.unit,
            base={k: b.kpis.get(k) for k in keys}, scenario={k: s.kpis.get(k) for k in keys},
            delta_min_stock=float(s.kpis["min_stock_sim"] - b.kpis["min_stock_sim"]),
            delta_max_shortage=float(s.kpis["max_shortage_sim"] - b.kpis["max_shortage_sim"]),
            delta_coverage=int(s.kpis["coverage_sim_days"] - b.kpis["coverage_sim_days"]),
            stockout_changed=(b.kpis.get("first_stockout_sim") != s.kpis.get("first_stockout_sim"))))
    return out
