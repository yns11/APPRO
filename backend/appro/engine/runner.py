"""Portfolio-level orchestration of the MRP engine."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

import numpy as np

from .alerts import classify_alerts, worst_severity
from .calendar import WorkCalendar
from .demand import DayIndex, actual_share, build_program_daily, explode_demand
from .models import (Alert, ArticleResult, Dataset, EngineParams, MrpResult, OrderLine, OrderType, SupplyEvent)
from .projection import coverage_days, first_negative, project_stock, target_stock
from .proposals import generate_proposals


def resolve_as_of(dataset: Dataset, params: EngineParams) -> dt.date:
    if params.as_of:
        return params.as_of
    if dataset.stock:
        return max(s.snapshot_date for s in dataset.stock) + dt.timedelta(days=1)
    return dt.date.today()


def _order_is_selected(o: OrderLine, params: EngineParams) -> bool:
    if o.qty_open <= 0:
        return False
    if params.orders_source == "erp" and o.source == "APP":
        return False
    if params.orders_source == "app" and o.source == "ERP":
        return False
    return True


def run_mrp(dataset: Dataset, params: EngineParams | None = None,
            article_ids: list[str] | None = None) -> MrpResult:
    """Run the full computation for the articles of the dataset (or a subset)."""
    params = params or EngineParams()
    calendar = WorkCalendar.from_spec(params.working_weekdays, dataset.holidays)
    as_of = resolve_as_of(dataset, params)
    snapshots = {s.article_id: s for s in dataset.stock}
    earliest_snapshot = min((s.snapshot_date for s in dataset.stock), default=as_of - dt.timedelta(days=1))
    start = min(as_of - dt.timedelta(days=max(params.history_days, 0)), earliest_snapshot)
    end = as_of + dt.timedelta(days=int(params.horizon_days))
    index = DayIndex(start, end)
    diagnostics: list[str] = []

    # ---------------------------------------------------------------- demand
    program_eff, program_plan, actual_mask, diag = build_program_daily(
        dataset.plan, dataset.actuals, calendar, index, params, as_of)
    diagnostics.extend(diag)
    demand_eff = explode_demand(program_eff, dataset.bom, index, params.consumption_offset_days)
    demand_plan = explode_demand(program_plan, dataset.bom, index, params.consumption_offset_days)

    # ---------------------------------------------------------------- lookups
    links_by_article: dict[str, list] = defaultdict(list)
    for l in dataset.links:
        if l.active:
            links_by_article[l.article_id].append(l)
    suppliers = {s.supplier_id: s for s in dataset.suppliers}
    bom_articles = {b.article_id for b in dataset.bom}
    orders_by_article: dict[str, list[OrderLine]] = defaultdict(list)
    for o in dataset.orders:
        orders_by_article[o.article_id].append(o)
    receipts_by_article = defaultdict(list)
    for r in dataset.receipts:
        receipts_by_article[r.article_id].append(r)
    movements_by_article = defaultdict(list)
    for m in dataset.movements:
        movements_by_article[m.article_id].append(m)
    # App receipts posted against an order reduce its open quantity (unless the ERP already did).
    app_received: dict[str, float] = defaultdict(float)
    for r in dataset.receipts:
        if r.source == "APP" and r.order_id:
            app_received[r.order_id] += r.qty

    selected = [a for a in dataset.articles if a.active and (article_ids is None or a.article_id in article_ids)]
    results: dict[str, ArticleResult] = {}
    n = index.n
    i_as_of = index.offset(as_of)
    assert i_as_of is not None

    for article in selected:
        aid = article.article_id
        notes: list[str] = []
        demand = demand_eff.get(aid, np.zeros(n))
        dplan = demand_plan.get(aid, np.zeros(n))
        share = actual_share(program_eff, actual_mask, dataset.bom, aid, index)

        snap = snapshots.get(aid)
        if snap is not None:
            i_snap = index.offset(snap.snapshot_date)
            stock_start = snap.qty_on_hand - (snap.qty_blocked or 0.0)
            if i_snap is None:
                notes.append(f"snapshot du {snap.snapshot_date} hors fenêtre : projection depuis le début de fenêtre")
                i_snap = 0
        else:
            i_snap, stock_start = 0, 0.0
        snap_date = index.dates[i_snap]

        supply_firm = np.zeros(n)
        supply_planned = np.zeros(n)
        receipts = np.zeros(n)
        adjustments = np.zeros(n)
        events: list[SupplyEvent] = []
        late_orders: list[OrderLine] = []
        open_orders: list[OrderLine] = []

        for o in orders_by_article.get(aid, []):
            if not _order_is_selected(o, params):
                continue
            qty = o.qty_open - app_received.get(o.order_id, 0.0)
            if qty <= 1e-9:
                continue
            day = o.expected_date
            late = False
            if day < as_of:
                late = True
                if params.late_order_policy == "ignore":
                    late_orders.append(o)
                    continue
                if params.late_order_policy == "reschedule":
                    day = calendar.next_working_day(as_of, inclusive=True)
                elif day <= snap_date:
                    day = calendar.next_working_day(snap_date, inclusive=False)
                late_orders.append(o)
            i = index.offset(day)
            if i is None:
                continue
            open_orders.append(o)
            typ = o.order_type.value
            if typ in params.firm_sources:
                supply_firm[i] += qty
            elif typ in params.simulated_sources:
                supply_planned[i] += qty
            else:
                continue
            events.append(SupplyEvent(day, "order", o.order_id, qty, o.supplier_id, typ, o.source, late))

        for r in receipts_by_article.get(aid, []):
            if r.receipt_date <= snap_date:
                continue  # already in the on-hand stock
            i = index.offset(r.receipt_date)
            if i is None:
                continue
            receipts[i] += r.qty
            events.append(SupplyEvent(r.receipt_date, "receipt", r.receipt_id, r.qty, r.supplier_id, "RECEIPT", r.source))
        for m in movements_by_article.get(aid, []):
            if m.date <= snap_date:
                continue
            i = index.offset(m.date)
            if i is None:
                continue
            adjustments[i] += m.qty
            events.append(SupplyEvent(m.date, "movement", m.movement_id, m.qty, None, m.movement_type, m.source))

        # zero everything before the snapshot day (unknown history)
        if i_snap > 0:
            for arr in (supply_firm, supply_planned, receipts, adjustments):
                arr[:i_snap] = 0.0
        demand_proj = demand.copy()
        demand_proj[:i_snap + 1] = 0.0  # snapshot day already consumed

        base_supply_firm = supply_firm + receipts
        stock_firm = project_stock(stock_start, base_supply_firm, adjustments, demand_proj)
        stock_sim = project_stock(stock_start, base_supply_firm + supply_planned, adjustments, demand_proj)
        if i_snap > 0:
            stock_firm[:i_snap] = stock_start
            stock_sim[:i_snap] = stock_start
        target = target_stock(demand, article, index, calendar, params)

        proposals, supply_proposed = [], np.zeros(n)
        if params.generate_proposals:
            proposals, supply_proposed, stock_after = generate_proposals(
                article, links_by_article.get(aid, []), suppliers, stock_sim, demand, target,
                index, calendar, as_of, params, supply_planned=supply_planned)
            if params.include_proposals_in_simulation:
                stock_sim = stock_after
                for p in proposals:
                    events.append(SupplyEvent(p.delivery_date, "proposal", p.proposal_id, p.qty, p.supplier_id,
                                              "PROPOSAL", "ENGINE", p.urgent))
        cov_firm = coverage_days(stock_firm, demand, index, calendar, params.coverage_unit, params.coverage_tie_rule)
        cov_sim = coverage_days(stock_sim, demand, index, calendar, params.coverage_unit, params.coverage_tie_rule)

        alerts = classify_alerts(article, index, as_of, stock_firm, stock_sim, cov_firm, cov_sim, demand,
                                 open_orders, late_orders, proposals, links_by_article.get(aid, []),
                                 aid in bom_articles, snap is not None, params)
        k_sim = first_negative(stock_sim, i_as_of)
        k_firm = first_negative(stock_firm, i_as_of)
        horizon_slice = slice(i_as_of, n)
        kpis = {
            "stock_on_hand": float(stock_start),
            "snapshot_date": snap_date.isoformat(),
            "stock_as_of_firm": float(stock_firm[i_as_of]),
            "stock_as_of_sim": float(stock_sim[i_as_of]),
            "coverage_firm_days": int(cov_firm[i_as_of]),
            "coverage_sim_days": int(cov_sim[i_as_of]),
            "coverage_target_days": int(article.coverage_target_days),
            "target_stock": float(target[i_as_of]),
            "first_stockout_sim": index.dates[k_sim].isoformat() if k_sim is not None else None,
            "first_stockout_firm": index.dates[k_firm].isoformat() if k_firm is not None else None,
            "min_stock_sim": float(np.min(stock_sim[horizon_slice])),
            "min_stock_firm": float(np.min(stock_firm[horizon_slice])),
            "demand_next_7d": float(demand[i_as_of + 1:i_as_of + 8].sum()),
            "demand_next_30d": float(demand[i_as_of + 1:i_as_of + 31].sum()),
            "demand_horizon": float(demand[horizon_slice].sum()),
            "avg_daily_demand_30d": float(demand[i_as_of + 1:i_as_of + 31].sum() / max(1, min(30, n - i_as_of - 1))),
            "open_firm_qty": float(supply_firm[horizon_slice].sum()),
            "open_planned_qty": float(supply_planned[horizon_slice].sum()),
            "proposed_qty": float(supply_proposed.sum()),
            "proposal_count": len(proposals),
            "urgent_proposal_count": sum(1 for p in proposals if p.urgent),
            "late_order_count": len(late_orders),
            "late_order_qty": float(sum(o.qty_open for o in late_orders)),
            "alert_count": len(alerts),
            "severity": (worst_severity(alerts).value if alerts else None),
            "actual_share_30d": float(share[i_as_of - 30 if i_as_of >= 30 else 0:i_as_of + 1].mean()) if i_as_of > 0 else 0.0,
        }
        results[aid] = ArticleResult(
            article=article, start_date=start, as_of=as_of, dates=index.dates,
            demand=demand.tolist(), demand_plan=dplan.tolist(), demand_actual_share=share.tolist(),
            supply_firm=supply_firm.tolist(), supply_planned=supply_planned.tolist(),
            supply_proposed=supply_proposed.tolist(), receipts=receipts.tolist(), adjustments=adjustments.tolist(),
            stock_firm=stock_firm.tolist(), stock_sim=stock_sim.tolist(),
            coverage_firm=cov_firm.tolist(), coverage_sim=cov_sim.tolist(), target_stock=target.tolist(),
            events=sorted(events, key=lambda e: (e.date, e.kind, e.ref)), alerts=alerts, proposals=proposals,
            kpis=kpis, suppliers=links_by_article.get(aid, []), diagnostics=notes,
        )

    program_daily = {pid: {index.dates[i]: float(v) for i, v in enumerate(arr) if v}
                     for pid, arr in program_eff.items()}
    return MrpResult(as_of=as_of, start_date=start, end_date=end, params=params, articles=results,
                     program_daily=program_daily, diagnostics=diagnostics)


__all__ = ["run_mrp", "resolve_as_of", "Alert", "OrderType"]
