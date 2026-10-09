"""Conversion of engine results into API schemas (kept out of the routers for testability)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any

import numpy as np

from ..engine.calendar import iso_week_label, iso_week_monday
from ..engine.models import Alert, ArticleResult, Dataset, Lane, MrpResult, Proposal, SupplierLink
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
    ("plan", "Appro."),
    ("supply_proposed", "Proposition CBN"),
    ("adjustments", "Ajustement"),
    ("stock_erp", "Projeté ERP"),
    ("stock_plan", "Projeté Appro."),
    ("shortage_erp", "Manque ERP"),
    ("shortage_plan", "Manque Plan"),
    ("target_stock", "Stock cible"),
    ("coverage_erp", "Couverture ERP (j)"),
    ("coverage_plan", "Couverture Appro. (j)"),
]
LANE_SERIES = [("orders_firm", "Ferme"), ("orders_firm_hist", "Ferme (passé)"), ("orders_forecast", "Prévisionnel"),
               ("receipts", "Reçu"), ("plan", "Appro."), ("supply_proposed", "Proposition CBN"),
               ("orders_firm_ordered", "Ferme commandé"), ("orders_firm_open", "Ferme restant ERP"),
               ("desadv_open", "DESADV non reçu")]
#: series of the multi-article table (the article page gets every series)
GRID_SERIES = {"demand", "supply_proposed", "adjustments", "stock_erp", "stock_plan", "shortage_erp", "shortage_plan",
               "coverage_erp", "coverage_plan", "target_stock"}
FLOWS = {"demand", "consumed", "required", "demand_plan", "orders_firm", "orders_firm_hist", "orders_forecast",
         "receipts", "plan", "supply_proposed", "adjustments", "desadv_open"}
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
        projected_stock_after=p.projected_stock_after, ignored=p.ignored)


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
    values = [r.kpis["stock_value"] for r in arts if r.kpis.get("stock_value") is not None]
    targets = [r.kpis["target_value"] for r in arts if r.kpis.get("target_value") is not None]
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
        median_coverage_days=(float(np.median(cov)) if cov else None),
        demand_next_30d=float(sum(r.kpis["demand_next_30d"] for r in arts)),
        stock_value=(sum(v for v in values) if values else None),
        target_value=(sum(t for t in targets) if values else None),
        priced_articles=len(values), unpriced_articles=len(arts) - len(values),
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


def weekly_stock_value(result: MrpResult, weeks: int = 52) -> list[S.WeeklyStockValue]:
    """Projected value of the portfolio stock (Projeté Appro.) and of its target, at the end of each
    ISO week from the current one : Σ price × stock over the articles that have a price."""
    arts = [r for r in result.articles.values() if r.price is not None]
    if not arts:
        return []
    ref = arts[0]
    i0 = ref.dates.index(result.as_of)
    last_of_week: dict[str, int] = {}
    for i in range(i0, len(ref.dates)):
        wk = iso_week_label(ref.dates[i])
        if wk not in last_of_week and len(last_of_week) >= weeks:
            break
        last_of_week[wk] = i
    out = []
    for wk, i in last_of_week.items():
        out.append(S.WeeklyStockValue(week=wk, week_start=iso_week_monday(ref.dates[i]),
                                      value_plan=float(sum(r.price * r.stock_plan[i] for r in arts)),
                                      value_target=float(sum(r.price * r.target_stock[i] for r in arts))))
    return out


def _desadv_received(ds: Dataset) -> set[str]:
    return {bl for r in ds.receipts for bl in r.packing_slips}


def flows_out(result: MrpResult, ds: Dataset, supplier_names: dict[str, str], planner: str | None) -> S.FlowsResponse:
    """The supply flows of the day (cockpit, tab « Flux d'approvisionnement »)."""
    as_of = result.as_of
    arts = {a.article_id: a for a in ds.articles}
    res = result.articles

    def art(aid: str) -> dict[str, str]:
        a = arts.get(aid)
        return {"designation": a.designation if a else "", "unit": a.unit if a else "", "planner": a.planner if a else ""}

    def order_row(o) -> S.OrderRow:
        return S.OrderRow(order_id=o.order_id, article_id=o.article_id, **art(o.article_id), supplier_id=o.supplier_id,
                          supplier_name=supplier_names.get(o.supplier_id or "", ""), order_type=o.order_type.value,
                          expected_date=o.expected_date, qty_ordered=o.qty_ordered, qty_open=float(o.qty_open or 0.0),
                          purch_id=o.ref, first_seen=o.first_seen)

    def receipt_row(r) -> S.ReceiptRow:
        return S.ReceiptRow(receipt_id=r.receipt_id, article_id=r.article_id, **art(r.article_id), supplier_id=r.supplier_id,
                            supplier_name=supplier_names.get(r.supplier_id or "", ""), receipt_date=r.receipt_date, qty=r.qty,
                            purch_id=r.ref, packing_slip=r.packing_slip, status=r.status)

    received_bls = _desadv_received(ds)

    def desadv_row(d, issue: str = "") -> S.DesadvRow:
        return S.DesadvRow(desadv_id=d.desadv_id, article_id=d.article_id, **art(d.article_id), supplier_id=d.supplier_id,
                           supplier_name=supplier_names.get(d.supplier_id or "", ""), packing_slip=d.packing_slip, purch_id=d.purch_id,
                           issue_date=d.issue_date, qty=d.qty, state=d.state, final_processing=d.final_processing,
                           received=d.packing_slip.strip() in received_bls, journal=bool(d.stock_trans_id.strip()), issue=issue)

    def pending_row(b) -> S.PendingRow:
        return S.PendingRow(pending_id=b.pending_id, article_id=b.article_id, **art(b.article_id), supplier_id=b.supplier_id,
                            supplier_name=supplier_names.get(b.supplier_id or "", ""), purch_id=b.purch_id, packing_slip=b.packing_slip,
                            qty=b.qty, registered_date=b.registered_date, days_pending=b.days_pending)

    # 1. à commander : urgent proposals, the refused ones included (flagged « ignorée »)
    to_order = [proposal_out(p, r, supplier_names) for r in res.values() for p in (*r.proposals, *r.proposals_ignored) if p.urgent]
    to_order.sort(key=lambda p: (p.order_date, p.article_id, p.supplier_id or ""))
    firm = [o for o in ds.orders if o.order_type.value == "FIRM" and o.article_id in res]
    # 2. commandé : firm slots that appeared in the ERP today, whatever their delivery date
    ordered = sorted((order_row(o) for o in firm if o.first_seen == as_of), key=lambda o: (o.expected_date, o.article_id))
    live = [d for d in ds.desadv if d.article_id in res and d.state.strip().lower() != "annulé"]
    # 3. en transit : announced, not received yet
    in_transit = sorted((desadv_row(d) for d in live if d.packing_slip.strip() not in received_bls), key=lambda d: (d.issue_date, d.article_id))
    # 4. à traiter : in error, or processed without any stock transaction (no entry journal)
    to_process = []
    for d in live:
        if d.state.strip().lower() == "erreur":
            to_process.append(desadv_row(d, "message en erreur"))
        elif d.processed and not d.stock_trans_id.strip():
            to_process.append(desadv_row(d, "traité sans journal de saisie"))
    to_process.sort(key=lambda d: (d.issue_date, d.article_id))
    # 5. à réceptionner : firm slots due today
    to_receive = sorted((order_row(o) for o in firm if o.expected_date == as_of), key=lambda o: (o.supplier_id or "", o.article_id))
    # 6. reçu : receipts dated today
    received = sorted((receipt_row(r) for r in ds.receipts if r.article_id in res and r.receipt_date == as_of), key=lambda r: (r.supplier_id or "", r.article_id))
    # 7. en retard : supplier backlog
    late = backlog_rows(result)
    # 8. à valider : registered receipts waiting for their acknowledgement
    to_validate = sorted((pending_row(b) for b in ds.bl_pending if b.article_id in res), key=lambda b: (-b.days_pending, b.article_id))
    return S.FlowsResponse(
        as_of=as_of, planner=planner, first_seen_available=any(o.first_seen is not None for o in ds.orders),
        kpis=S.FlowsKpis(to_order=len(to_order), ordered=len(ordered), in_transit=len(in_transit), to_process=len(to_process),
                         to_receive=len(to_receive), received=len(received), late=len(late), to_validate=len(to_validate)),
        to_order=to_order, ordered=ordered, in_transit=in_transit, to_process=to_process, to_receive=to_receive,
        received=received, late=late, to_validate=to_validate)


def search_rows(ds: Dataset, supplier_names: dict[str, str], kind: str) -> list[dict[str, Any]]:
    """Every line of one kind (receipts, orders, DESADV, pending BL) of the dataset, as flat rows."""
    arts = {a.article_id: a for a in ds.articles}

    def art(aid: str) -> dict[str, str]:
        a = arts.get(aid)
        return {"designation": a.designation if a else "", "unit": a.unit if a else "", "planner": a.planner if a else ""}

    def sup(sid: str | None) -> dict[str, Any]:
        return {"supplier_id": sid, "supplier_name": supplier_names.get(sid or "", "")}

    if kind == "receipts":
        return [{"receipt_id": r.receipt_id, "article_id": r.article_id, **art(r.article_id), **sup(r.supplier_id), "date": r.receipt_date,
                 "qty": r.qty, "purch_id": r.ref, "packing_slip": r.packing_slip, "status": r.status} for r in ds.receipts]
    if kind == "orders":
        return [{"order_id": o.order_id, "article_id": o.article_id, **art(o.article_id), **sup(o.supplier_id), "date": o.expected_date,
                 "order_type": o.order_type.value, "qty_ordered": o.qty_ordered, "qty_open": float(o.qty_open or 0.0), "purch_id": o.ref,
                 "first_seen": o.first_seen} for o in ds.orders]
    if kind == "desadv":
        received = _desadv_received(ds)
        return [{"desadv_id": d.desadv_id, "article_id": d.article_id, **art(d.article_id), **sup(d.supplier_id), "date": d.issue_date,
                 "qty": d.qty, "purch_id": d.purch_id, "packing_slip": d.packing_slip, "state": d.state, "final_processing": d.final_processing,
                 "received": d.packing_slip.strip() in received, "journal": bool(d.stock_trans_id.strip())} for d in ds.desadv]
    if kind == "pending":
        return [{"pending_id": b.pending_id, "article_id": b.article_id, **art(b.article_id), **sup(b.supplier_id), "date": b.registered_date,
                 "qty": b.qty, "purch_id": b.purch_id, "packing_slip": b.packing_slip, "days_pending": b.days_pending} for b in ds.bl_pending]
    raise ValueError(kind)


def match_any(text: str, q: str | None) -> bool:
    """Free-text filter : terms separated by « ; » are alternatives (``123;456`` = contains 123 or 456)."""
    terms = [t.strip().lower() for t in (q or "").split(";") if t.strip()]
    if not terms:
        return True
    hay = text.lower()
    return any(t in hay for t in terms)


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


@dataclass
class Columns:
    """The columns of a grid: contiguous groups of day indexes (vectorised aggregation)."""

    keys: list[str]
    starts: np.ndarray      # first day index of each column
    lasts: np.ndarray       # last day index of each column
    first: int              # first day index shown
    day_only: bool          # every column is a single day

    @classmethod
    def build(cls, dates: list[dt.date], as_of: dt.date, granularity: str, focus_weeks: int,
              start: dt.date | None = None) -> "Columns":
        groups = period_groups(dates, as_of, granularity, focus_weeks, start)
        starts = np.array([g[0] for g in groups.values()], dtype=int)
        lasts = np.array([g[-1] for g in groups.values()], dtype=int)
        return cls(list(groups), starts, lasts, int(starts[0]) if len(starts) else 0, bool(np.all(starts == lasts)))

    def flow(self, raw) -> list[float]:
        """Σ over the days of each column."""
        a = np.asarray(raw, dtype=float)
        v = a[self.starts] if self.day_only else np.add.reduceat(a[self.first:], self.starts - self.first)
        return np.round(v, 3).tolist()

    def level(self, raw) -> list[float]:
        """Value at the last day of each column (stocks, coverage)."""
        return np.round(np.asarray(raw, dtype=float)[self.lasts], 3).tolist()

    def any(self, raw) -> list[bool]:
        a = np.asarray(raw, dtype=bool)
        v = a[self.starts] if self.day_only else np.logical_or.reduceat(a[self.first:], self.starts - self.first)
        return v.tolist()

    def meta(self, dates: list[dt.date]) -> dict[str, Any]:
        return {"periods": self.keys, "period_start": [dates[i].isoformat() for i in self.starts],
                "period_end": [dates[i].isoformat() for i in self.lasts]}


def _agg(key: str, raw, cols: Columns, lost: bool) -> list[float]:
    return cols.flow(raw) if key in FLOWS or (lost and key in SHORTAGES) else cols.level(raw)


def series_out(ar: ArticleResult, cols: Columns, lost: bool, keys: set[str] | None = None) -> list[dict[str, Any]]:
    return [{"key": key, "label": label, "values": _agg(key, getattr(ar, key), cols, lost)}
            for key, label in SERIES_LABELS if keys is None or key in keys]


def lane_out(l: Lane, cols: Columns, with_orders: bool = True, since: dt.date | None = None) -> dict[str, Any]:
    """One lane.  ``since`` keeps only the order / receipt / DESADV lines dated inside the displayed
    window (the multi-article table, page after page, must stay light) ; ``None`` keeps them all."""
    def shown(day: dt.date) -> bool:
        return since is None or day >= since
    return {
        "supplier_id": l.supplier_id, "name": l.name,
        "series": [{"key": key, "label": label, "values": _agg(key, getattr(l, key), cols, False)} for key, label in LANE_SERIES],
        "plan_typed": cols.any(l.plan_typed), "orders_ignored": cols.any(l.orders_ignored),
        "desadv_ko": cols.any(l.desadv_ko) if l.desadv_ko is not None else cols.any(np.zeros(len(l.plan), dtype=bool)),
        "receipts_ko": cols.any(l.receipts_ko) if l.receipts_ko is not None else cols.any(np.zeros(len(l.plan), dtype=bool)),
        "backlog_ordered": l.backlog_ordered, "backlog_received": l.backlog_received, "backlog_qty": l.backlog_qty,
        "orders": [o.__dict__ for o in l.orders if shown(o.expected_date)] if with_orders else [],
        "desadv": [d.__dict__ for d in l.desadv if shown(d.issue_date)] if with_orders else [],
        "receipt_lines": [r.__dict__ for r in l.receipt_lines if shown(r.receipt_date)] if with_orders else [],
    }


def display_start(result: MrpResult) -> dt.date:
    """First day shown in the supply table: the Monday ``history_weeks`` weeks before the current week,
    never before the stock initialisation day (the point zero : nothing exists before it)."""
    monday = iso_week_monday(result.as_of) - dt.timedelta(days=7 * max(int(result.params.history_weeks), 0))
    return max(monday, result.init_date)


def projection_out(ar: ArticleResult, result: MrpResult, granularity: str, supplier_names: dict[str, str],
                   programs: list[dict[str, Any]], from_date: dt.date | None = None) -> dict[str, Any]:
    start = from_date or display_start(result)
    cols = Columns.build(ar.dates, result.as_of, granularity, result.params.focus_weeks, start)
    lost = result.params.shortage_policy == "lost"
    return {
        "article": article_ref(ar).model_dump(mode="json"), "as_of": result.as_of.isoformat(), "init_date": result.init_date.isoformat(), "granularity": granularity,
        **cols.meta(ar.dates),
        "series": series_out(ar, cols, lost), "lanes": [lane_out(l, cols, since=start) for l in ar.lanes],
        "proposals": [proposal_out(p, ar, supplier_names).model_dump(mode="json") for p in ar.proposals],
        "alerts": [alert_out(a, ar.article.designation).model_dump(mode="json") for a in ar.alerts],
        "kpis": ar.kpis, "suppliers": [link_out(l, supplier_names).model_dump(mode="json") for l in ar.suppliers],
        "programs": programs, "diagnostics": ar.diagnostics + result.diagnostics,
    }


def grid_out(result: MrpResult, granularity: str, supplier_names: dict[str, str],
             programs_of: dict[str, list[str]], from_date: dt.date | None = None,
             articles: list[ArticleResult] | None = None, total: int | None = None, page: int = 1,
             page_size: int | None = None) -> dict[str, Any]:
    """Multi-article supply table: every article on the same columns (one page of articles)."""
    start = from_date or display_start(result)
    arts = list(result.articles.values()) if articles is None else articles
    base = {"as_of": result.as_of.isoformat(), "init_date": result.init_date.isoformat(), "granularity": granularity, "diagnostics": result.diagnostics,
            "total": len(result.articles) if total is None else total, "page": page, "page_size": page_size or len(arts)}
    if not result.articles:
        return {**base, "periods": [], "period_start": [], "period_end": [], "articles": []}
    dates = next(iter(result.articles.values())).dates
    cols = Columns.build(dates, result.as_of, granularity, result.params.focus_weeks, start)
    lost = result.params.shortage_policy == "lost"
    return {
        **base, **cols.meta(dates),
        "articles": [{"article": article_ref(ar).model_dump(mode="json"), "series": series_out(ar, cols, lost, GRID_SERIES),
                      "lanes": [lane_out(l, cols, since=start) for l in ar.lanes], "kpis": ar.kpis,
                      "suppliers": [link_out(l, supplier_names).model_dump(mode="json") for l in ar.suppliers],
                      "programs": programs_of.get(ar.article.article_id, [])}
                     for ar in arts],
    }


def delivery_plan(ar: ArticleResult, as_of: dt.date, supplier_names: dict[str, str]) -> dict[str, Any]:
    """The *Plan* row as an ERP delivery schedule, **one line per supplier and ISO week** from the
    reference day: everything the Plan row shows (typed cells, firm ERP orders taken over, CBN
    proposals) added up over the week ; delivery window = Monday → Sunday of the week."""
    i0 = ar.dates.index(as_of)
    rows: dict[tuple[str | None, dt.date], dict[str, Any]] = {}
    for l in ar.lanes:
        plan, cbn = np.asarray(l.plan), np.asarray(l.supply_proposed)
        typed = np.asarray(l.plan_typed, dtype=bool)
        total = plan + cbn
        for i in np.where(total[i0:] > 1e-9)[0] + i0:
            d = ar.dates[int(i)]
            monday = d - dt.timedelta(days=d.weekday())
            r = rows.get((l.supplier_id, monday))
            if r is None:
                y, w, _ = monday.isocalendar()
                r = rows[(l.supplier_id, monday)] = {
                    "supplier_id": l.supplier_id, "supplier_name": l.name, "week": f"{y}-W{w:02d}",
                    "date": monday.isoformat(), "end_date": (monday + dt.timedelta(days=6)).isoformat(),
                    "qty": 0.0, "typed_qty": 0.0, "erp_qty": 0.0, "cbn_qty": 0.0, "typed": False}
            r["qty"] += float(total[i])
            r["cbn_qty"] += float(cbn[i])
            if typed[i]:
                r["typed_qty"] += float(plan[i])
                r["typed"] = True
            else:
                r["erp_qty"] += float(plan[i])
    out = sorted(rows.values(), key=lambda r: (r["date"], r["supplier_id"] or ""))
    return {"article_id": ar.article.article_id, "designation": ar.article.designation, "unit": ar.article.unit,
            "as_of": as_of.isoformat(), "suppliers": [{"supplier_id": l.supplier_id, "name": l.name} for l in ar.lanes],
            "rows": out}
