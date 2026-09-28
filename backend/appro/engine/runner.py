"""Portfolio-level orchestration of the MRP engine."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

import numpy as np

from .alerts import classify_alerts, worst_severity
from .calendar import WorkCalendar
from .demand import DayIndex, actual_share, build_program_daily, explode_demand
from .models import Alert, ArticleResult, Dataset, EngineParams, MrpResult, OrderLine, OrderType, PlanLine, SupplyEvent
from .plan import build_supply_flows
from .programs import program_impact
from .projection import Projection, coverage_days, first_shortage, project_stock, reconstruct_history, target_stock
from .proposals import generate_proposals


def resolve_as_of(dataset: Dataset, params: EngineParams) -> dt.date:
    if params.as_of:
        return params.as_of
    if dataset.stock:
        return max(s.snapshot_date for s in dataset.stock) + dt.timedelta(days=1)
    return dt.date.today()


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
        dataset.pdp, dataset.actuals, calendar, index, params, as_of)
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
    cells_by_article = defaultdict(list)
    for c in dataset.cells:
        cells_by_article[c.article_id].append(c)
    plan_by_article: dict[str, list[PlanLine]] = defaultdict(list)
    for line in dataset.plan:
        plan_by_article[line.article_id].append(line)
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
    day_idx = np.arange(n)

    for article in selected:
        aid = article.article_id
        notes: list[str] = []
        demand = demand_eff.get(aid, np.zeros(n))
        dplan = demand_plan.get(aid, np.zeros(n))
        share = actual_share(program_eff, actual_mask, dataset.bom, aid, index)
        consumed = np.where(day_idx < i_as_of, demand, 0.0)
        required = np.where(day_idx >= i_as_of, demand, 0.0)

        snap = snapshots.get(aid)
        if snap is not None:
            i_snap = index.offset(snap.snapshot_date)
            stock_snapshot = snap.qty_on_hand - (snap.qty_blocked or 0.0)
            if i_snap is None:
                notes.append(f"snapshot du {snap.snapshot_date} hors fenêtre : projection depuis le début de fenêtre")
                i_snap = 0
        else:
            i_snap, stock_snapshot = 0, 0.0
        snap_date = index.dates[i_snap]

        receipts = np.zeros(n)           # R
        adjustments = np.zeros(n)        # A (dated after the snapshot)
        past_adjust = np.zeros(n)        # planner adjustments dated on/before the snapshot (reference corrections)
        erp_past = np.zeros(n)           # ERP movements dated on/before the snapshot: already in the ERP stock
        events: list[SupplyEvent] = []

        # Orders and delivery plan: ERP layer, plan layer, past history, not-received orders
        flows = build_supply_flows(orders_by_article.get(aid, []), plan_by_article.get(aid, []),
                                   receipts_by_article.get(aid, []), app_received, index, as_of, params)
        events.extend(flows.events)

        for r in receipts_by_article.get(aid, []):
            i = index.offset(r.receipt_date)
            if i is None:
                continue
            receipts[i] += r.qty
            events.append(SupplyEvent(r.receipt_date, "receipt", r.receipt_id, r.qty, r.supplier_id, "RECEIPT", r.source))
        for m in movements_by_article.get(aid, []):
            i = index.offset(m.date)
            if i is None:
                continue
            if m.date > snap_date:
                adjustments[i] += m.qty
            elif m.source == "ERP":
                erp_past[i] += m.qty      # history only: the ERP snapshot already contains it
            else:
                past_adjust[i] += m.qty
            events.append(SupplyEvent(m.date, "movement", m.movement_id, m.qty, None, m.movement_type, m.source))
        for c in cells_by_article.get(aid, []):
            if c.qty == 0:
                continue
            i = index.offset(c.date)
            if i is None:
                if c.date <= snap_date:
                    past_adjust[0] += c.qty   # older than the window: still corrects the reference stock
                continue
            (adjustments if c.date > snap_date else past_adjust)[i] += c.qty
            events.append(SupplyEvent(c.date, "movement", f"ADJ-{c.date.isoformat()}", c.qty, None, "ADJUSTMENT", c.source))

        # Reference stock = ERP snapshot + planner corrections dated on/before the snapshot
        reference_correction = float(past_adjust.sum())
        stock_start = stock_snapshot + reference_correction
        history = reconstruct_history(stock_start, receipts, past_adjust + erp_past, consumed, i_snap)
        demand_proj = demand.copy()
        demand_proj[:i_snap + 1] = 0.0  # snapshot day already consumed
        adjust_proj = adjustments.copy()
        adjust_proj[:i_snap + 1] = 0.0
        receipts_proj = receipts.copy()
        receipts_proj[:i_snap + 1] = 0.0

        # Layer inflows (docs/regles_metier.md § 3): ERP = R + firm orders as is ; Plan = R + delivery plan
        inflow_erp = receipts_proj + flows.orders_firm
        inflow_plan = receipts_proj + flows.plan

        def project(supply: np.ndarray) -> Projection:
            proj = project_stock(stock_start, supply, adjust_proj, demand_proj, params.shortage_policy, i_snap)
            proj.net[:i_snap + 1] = history
            proj.stock[:i_snap + 1] = np.maximum(history, 0.0)
            return proj

        onhand = project(np.zeros(n))
        erp = project(inflow_erp)
        plan = project(inflow_plan)
        target = target_stock(demand, article, index, calendar, params)

        proposals, supply_proposed = [], np.zeros(n)
        if params.generate_proposals:
            proposals, supply_proposed, _ = generate_proposals(
                article, links_by_article.get(aid, []), suppliers, plan.net, demand, target,
                index, calendar, as_of, params, supply_planned=flows.orders_forecast,
                reproject=lambda extra: project(inflow_plan + extra))
            if params.include_proposals_in_plan:
                plan = project(inflow_plan + supply_proposed)
                for p in proposals:
                    events.append(SupplyEvent(p.delivery_date, "proposal", p.proposal_id, p.qty, p.supplier_id,
                                              "PROPOSAL", "ENGINE", p.urgent))
        cov = {name: coverage_days(layer.net, demand, index, calendar, params.coverage_unit, params.coverage_tie_rule)
               for name, layer in (("erp", erp), ("plan", plan))}

        alerts = classify_alerts(article, index, as_of, erp, plan, cov["plan"], stock_start, demand,
                                 flows.orders, proposals, links_by_article.get(aid, []),
                                 aid in bom_articles, snap is not None, params)
        last = None if params.stockout_lookahead_days is None else i_as_of + params.stockout_lookahead_days
        k = {name: first_shortage(layer.shortage, i_as_of, last) for name, layer in (("erp", erp), ("plan", plan))}
        horizon_slice = slice(i_as_of, n)
        not_received = flows.not_received
        kpis = {
            "stock_on_hand": float(stock_snapshot),
            "reference_correction": reference_correction,
            "stock_reference": float(stock_start),
            "snapshot_date": snap_date.isoformat(),
            "shortage_policy": params.shortage_policy,
            "stock_as_of_erp": float(erp.stock[i_as_of]),
            "stock_as_of_plan": float(plan.stock[i_as_of]),
            "coverage_erp_days": int(cov["erp"][i_as_of]),
            "coverage_plan_days": int(cov["plan"][i_as_of]),
            "coverage_target_days": int(article.param_at("coverage_target_days", as_of)),
            "target_stock": float(target[i_as_of]),
            "first_stockout_erp": index.dates[k["erp"]].isoformat() if k["erp"] is not None else None,
            "first_stockout_plan": index.dates[k["plan"]].isoformat() if k["plan"] is not None else None,
            "min_stock_erp": float(np.min(erp.stock[horizon_slice])),
            "min_stock_plan": float(np.min(plan.stock[horizon_slice])),
            "max_shortage_erp": float(np.max(erp.shortage[horizon_slice])),
            "max_shortage_plan": float(np.max(plan.shortage[horizon_slice])),
            "demand_next_7d": float(demand[i_as_of + 1:i_as_of + 8].sum()),
            "demand_next_30d": float(demand[i_as_of + 1:i_as_of + 31].sum()),
            "demand_horizon": float(demand[horizon_slice].sum()),
            "avg_daily_demand_30d": float(demand[i_as_of + 1:i_as_of + 31].sum() / max(1, min(30, n - i_as_of - 1))),
            "open_firm_qty": float(flows.orders_firm[horizon_slice].sum()),
            "open_forecast_qty": float(flows.orders_forecast[horizon_slice].sum()),
            "plan_qty": float(flows.plan[horizon_slice].sum()),
            "plan_line_count": sum(1 for l in flows.lines if l.origin in ("override", "free")),
            "backlog_qty": float(sum(o.qty_open for o in not_received)),
            "backlog_count": len(not_received),
            "proposed_qty": float(supply_proposed.sum()),
            "proposal_count": len(proposals),
            "urgent_proposal_count": sum(1 for p in proposals if p.urgent),
            "alert_count": len(alerts),
            "severity": (worst_severity(alerts).value if alerts else None),
            "actual_share_30d": float(share[i_as_of - 30 if i_as_of >= 30 else 0:i_as_of + 1].mean()) if i_as_of > 0 else 0.0,
        }
        results[aid] = ArticleResult(
            article=article, start_date=start, as_of=as_of, dates=index.dates,
            demand=demand.tolist(), consumed=consumed.tolist(), required=required.tolist(), demand_plan=dplan.tolist(),
            demand_actual_share=share.tolist(),
            orders_firm=flows.orders_firm.tolist(), orders_firm_hist=flows.orders_firm_hist.tolist(),
            orders_forecast=flows.orders_forecast.tolist(), receipts=receipts.tolist(),
            plan=flows.plan.tolist(), plan_hist=flows.plan_hist.tolist(), supply_proposed=supply_proposed.tolist(),
            adjustments=(adjustments + past_adjust + erp_past).tolist(), reference_correction=reference_correction,
            stock_erp=erp.stock.tolist(), stock_plan=plan.stock.tolist(),
            shortage_onhand=onhand.shortage.tolist(), shortage_erp=erp.shortage.tolist(), shortage_plan=plan.shortage.tolist(),
            stock_erp_net=erp.net.tolist(), stock_plan_net=plan.net.tolist(),
            coverage_erp=cov["erp"].tolist(), coverage_plan=cov["plan"].tolist(), target_stock=target.tolist(),
            events=sorted(events, key=lambda e: (e.date, e.kind, e.ref)), alerts=alerts, proposals=proposals,
            kpis=kpis, suppliers=links_by_article.get(aid, []), orders=flows.orders, plan_lines=flows.lines,
            diagnostics=notes,
        )

    program_daily = {pid: {index.dates[i]: float(v) for i, v in enumerate(arr) if v}
                     for pid, arr in program_eff.items()}
    names = {p.program_id: p.name for p in dataset.programs}
    impact = program_impact(results, dataset.bom, program_eff, names, index, as_of, params.shortage_policy)
    return MrpResult(as_of=as_of, start_date=start, end_date=end, params=params, articles=results,
                     program_daily=program_daily, diagnostics=diagnostics, program_impact=impact)


__all__ = ["run_mrp", "resolve_as_of", "Alert", "OrderType"]
