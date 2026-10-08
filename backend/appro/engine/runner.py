"""Portfolio-level orchestration of the MRP engine."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

import numpy as np

from .alerts import classify_alerts, worst_severity
from .calendar import WorkCalendar, iso_week_monday
from .demand import DayIndex, actual_share, build_article_demand, build_program_daily, explode_demand
from .models import Alert, ArticleResult, Dataset, DatasetError, EngineParams, MrpResult, OrderType
from .programs import program_impact
from .projection import Projection, coverage_days, first_shortage, project_stock, target_stock
from .proposals import generate_proposals, in_blocked_window
from .supply import blocked_windows, build_lanes, lane_totals


def resolve_as_of(dataset: Dataset, params: EngineParams) -> dt.date:
    """« Today » of the computation: the real date, or the simulated one of the parameters (demo, tests)."""
    return params.as_of or dt.date.today()


def resolve_init_date(dataset: Dataset, as_of: dt.date) -> dt.date:
    """The stock initialisation date: point zero of the application (docs/regles_metier.md § 1).

    One single date for every article (the ``snapshot_date`` of ``fct_stock``), never after today ;
    without any stock row the application starts today with empty stocks.  The stock of that day is
    known at its end ; the projection starts the day after ; nothing before it exists for the engine."""
    dates = {s.snapshot_date for s in dataset.stock}
    if len(dates) > 1:
        raise DatasetError("Le stock de référence porte plusieurs dates d'initialisation (" +
                           ", ".join(d.isoformat() for d in sorted(dates)) +
                           ") : une seule date pour tous les articles est admise.")
    init = next(iter(dates), as_of)
    if init > as_of:
        raise DatasetError(f"La date d'initialisation du stock ({init.isoformat()}) est postérieure à aujourd'hui "
                           f"({as_of.isoformat()}).")
    return init


def run_mrp(dataset: Dataset, params: EngineParams | None = None,
            article_ids: list[str] | None = None) -> MrpResult:
    """Run the full computation for the articles of the dataset (or a subset)."""
    params = params or EngineParams()
    calendar = WorkCalendar.from_spec(params.working_weekdays, dataset.holidays)
    as_of = resolve_as_of(dataset, params)
    init_date = resolve_init_date(dataset, as_of)
    snapshots = {s.article_id: s for s in dataset.stock}
    # the window opens on the initialisation day (stock known at its end) ; the past of the engine is
    # [init, today) ; what is displayed before today is a presentation choice (history_weeks)
    start = init_date
    # the window always covers the whole current ISO week: the remainder of its PDP is spread over it
    end = max(as_of + dt.timedelta(days=int(params.horizon_days)), iso_week_monday(as_of) + dt.timedelta(days=6))
    index = DayIndex(start, end)
    diagnostics: list[str] = []

    # ---------------------------------------------------------------- demand
    # PDP → daily production per programme → exploded component demand ; then the reported actual
    # consumption (already per component) replaces the past days and drives the current-week remainder
    program_plan, diag = build_program_daily(dataset.pdp, calendar, index, params)
    diagnostics.extend(diag)
    demand_plan = explode_demand(program_plan, dataset.bom, index, params.consumption_offset_days)
    demand_eff, actual_mask = build_article_demand(demand_plan, dataset.consumption, calendar, index, params, as_of)

    # ---------------------------------------------------------------- lookups
    links_by_article: dict[str, list] = defaultdict(list)
    for l in dataset.links:
        if l.active:
            links_by_article[l.article_id].append(l)
    suppliers = {s.supplier_id: s for s in dataset.suppliers}
    supplier_names = {s.supplier_id: s.name for s in dataset.suppliers}
    bom_articles = {b.article_id for b in dataset.bom}
    orders_by_article = defaultdict(list)
    for o in dataset.orders:
        orders_by_article[o.article_id].append(o)
    receipts_by_article = defaultdict(list)
    for r in dataset.receipts:
        receipts_by_article[r.article_id].append(r)
    adjust_by_article = defaultdict(list)
    for c in dataset.adjustments:
        adjust_by_article[c.article_id].append(c)
    plan_by_article = defaultdict(list)
    for c in dataset.plan:
        plan_by_article[c.article_id].append(c)
    flags_by_article = defaultdict(list)
    for f in dataset.flags:
        flags_by_article[f.article_id].append(f)
    desadv_by_article = defaultdict(list)
    for d in dataset.desadv:
        desadv_by_article[d.article_id].append(d)

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
        share = actual_share(actual_mask, aid, index)
        consumed = np.where(day_idx < i_as_of, demand, 0.0)
        required = np.where(day_idx >= i_as_of, demand, 0.0)

        snap = snapshots.get(aid)
        i_snap, snap_date = 0, init_date
        stock_snapshot = snap.qty_on_hand - (snap.qty_blocked or 0.0) if snap is not None else 0.0

        lanes = build_lanes(links_by_article.get(aid, []), orders_by_article.get(aid, []), receipts_by_article.get(aid, []),
                            plan_by_article.get(aid, []), supplier_names, index, as_of, params, flags_by_article.get(aid),
                            desadv_by_article.get(aid))
        receipts = lane_totals(lanes, "receipts")
        orders_firm = lane_totals(lanes, "orders_firm")
        plan = lane_totals(lanes, "plan")

        adjustments = np.zeros(n)        # dated after the snapshot: movements of the projection
        past_adjust = np.zeros(n)        # dated on/before the snapshot: corrections of the reference stock
        for c in adjust_by_article.get(aid, []):
            if c.qty == 0:
                continue
            i = index.offset(c.date)
            if i is None:
                if c.date <= snap_date:
                    past_adjust[0] += c.qty   # older than the window: still corrects the reference stock
                continue
            (adjustments if c.date > snap_date else past_adjust)[i] += c.qty

        # Reference stock = initial ERP stock + planner corrections dated on/before the initialisation day
        reference_correction = float(past_adjust.sum())
        stock_start = stock_snapshot + reference_correction
        history = np.array([stock_start])   # end of the initialisation day ; nothing earlier exists
        demand_proj = demand.copy()
        demand_proj[:i_snap + 1] = 0.0  # snapshot day already consumed
        adjust_proj = adjustments.copy()
        adjust_proj[:i_snap + 1] = 0.0
        receipts_proj = receipts.copy()
        receipts_proj[:i_snap + 1] = 0.0

        # Scenario inflows (docs/regles_metier.md § 3): ERP = R + firm orders ; Plan = R + plan cells / ERP
        inflow_erp = receipts_proj + orders_firm
        inflow_plan = receipts_proj + plan

        def project(supply: np.ndarray) -> Projection:
            proj = project_stock(stock_start, supply, adjust_proj, demand_proj, params.shortage_policy, i_snap)
            proj.net[:i_snap + 1] = history
            proj.stock[:i_snap + 1] = np.maximum(history, 0.0)
            return proj

        onhand = project(np.zeros(n))
        erp = project(inflow_erp)
        plan_proj = project(inflow_plan)
        target = target_stock(demand, article, index, calendar, params)

        proposals, supply_proposed = [], np.zeros(n)
        proposals_ignored: list = []
        if params.generate_proposals:
            # the re-projection is only needed by the ``lost`` policy (a receipt after a lost day does
            # not serve that day) ; with ``backlog`` adding the proposal from its day on is exact
            blocked = blocked_windows(flags_by_article.get(aid), aid)
            reproject = (lambda extra: project(inflow_plan + extra)) if params.shortage_policy == "lost" else None
            proposals, supply_proposed, _ = generate_proposals(
                article, links_by_article.get(aid, []), suppliers, plan_proj.net, demand, target,
                index, calendar, as_of, params, supply_planned=lane_totals(lanes, "orders_forecast"),
                reproject=reproject, blocked=blocked)
            if blocked:
                # what the engine would have proposed without the refusals : the refused ones stay listed
                # (« ignorée ») in the ordering flow, outside every calculation
                free, _, _ = generate_proposals(article, links_by_article.get(aid, []), suppliers, plan_proj.net, demand,
                                                target, index, calendar, as_of, params, reproject=reproject)
                for p in free:
                    if in_blocked_window(blocked, p.supplier_id, p.delivery_date):
                        p.ignored = True
                        proposals_ignored.append(p)
            if params.include_proposals_in_plan:
                plan_proj = project(inflow_plan + supply_proposed)
            by_lane = {l.supplier_id: l for l in lanes}
            for p in proposals:
                lane = by_lane.get(p.supplier_id) or lanes[0]
                j = index.offset(p.delivery_date)
                if j is not None:
                    lane.supply_proposed[j] += p.qty
        # next expected delivery (Plan row + CBN complement, every supplier) : its day, quantity and nature
        next_delivery = None
        for i in range(i_as_of, n):
            qty = sum(float(l.plan[i]) + float(l.supply_proposed[i]) for l in lanes)
            if qty > 1e-9:
                typed = any(bool(l.plan_typed[i]) and l.plan[i] > 0 for l in lanes)
                firm = any(not bool(l.plan_typed[i]) and l.plan[i] > 0 for l in lanes)
                next_delivery = (index.dates[i], qty, "saisie" if typed else "ferme" if firm else "cbn")
                break
        cov = {name: coverage_days(layer.net, demand, index, calendar, params.coverage_unit, params.coverage_tie_rule)
               for name, layer in (("erp", erp), ("plan", plan_proj))}

        alerts = classify_alerts(article, index, as_of, erp, plan_proj, cov["plan"], stock_start, demand,
                                 lanes, proposals, links_by_article.get(aid, []), aid in bom_articles,
                                 snap is not None, params)
        last = None if params.stockout_lookahead_days is None else i_as_of + params.stockout_lookahead_days
        k = {name: first_shortage(layer.shortage, i_as_of, last) for name, layer in (("erp", erp), ("plan", plan_proj))}
        horizon_slice = slice(i_as_of, n)
        typed = int(sum(int(l.plan_typed[i_as_of:].sum()) for l in lanes))
        ignored_days = int(sum(int(l.orders_ignored[i_as_of:].sum()) for l in lanes))
        refused = sum(1 for f in flags_by_article.get(aid, []) if f.kind == "proposal_refused")
        price = dataset.prices.get(aid)
        # stock « à date » = the stock at the end of yesterday (the reference day itself is not over) ;
        # identical in both scenarios before today, so read on the ERP one
        stock_at_date = float(erp.stock[i_as_of - 1]) if i_as_of > 0 else float(max(stock_start, 0.0))
        plan_stockout = next((a for a in alerts if a.alert_type.value == "STOCKOUT" and a.scope == "plan"), None)
        kpis = {
            "stock_on_hand": float(stock_snapshot),
            "reference_correction": reference_correction,
            "stock_reference": float(stock_start),
            "stock_at_date": stock_at_date,
            "price": price,
            "stock_value": (stock_at_date * price) if price is not None else None,
            "target_value": (float(target[i_as_of]) * price) if price is not None else None,
            "stockout_plan_severity": plan_stockout.severity.value if plan_stockout else None,
            "init_date": snap_date.isoformat(),
            "shortage_policy": params.shortage_policy,
            "stock_as_of_erp": float(erp.stock[i_as_of]),
            "stock_as_of_plan": float(plan_proj.stock[i_as_of]),
            "coverage_erp_days": int(cov["erp"][i_as_of]),
            "coverage_plan_days": int(cov["plan"][i_as_of]),
            "coverage_target_days": int(article.param_at("coverage_target_days", as_of)),
            "target_stock": float(target[i_as_of]),
            "first_stockout_erp": index.dates[k["erp"]].isoformat() if k["erp"] is not None else None,
            "first_stockout_plan": index.dates[k["plan"]].isoformat() if k["plan"] is not None else None,
            "min_stock_erp": float(np.min(erp.stock[horizon_slice])),
            "min_stock_plan": float(np.min(plan_proj.stock[horizon_slice])),
            "max_shortage_erp": float(np.max(erp.shortage[horizon_slice])),
            "max_shortage_plan": float(np.max(plan_proj.shortage[horizon_slice])),
            "demand_next_7d": float(demand[i_as_of + 1:i_as_of + 8].sum()),
            "demand_next_30d": float(demand[i_as_of + 1:i_as_of + 31].sum()),
            "demand_horizon": float(demand[horizon_slice].sum()),
            "avg_daily_demand_30d": float(demand[i_as_of + 1:i_as_of + 31].sum() / max(1, min(30, n - i_as_of - 1))),
            "open_firm_qty": float(orders_firm[horizon_slice].sum()),
            "open_forecast_qty": float(lane_totals(lanes, "orders_forecast")[horizon_slice].sum()),
            "plan_qty": float(plan[horizon_slice].sum()),
            "plan_cell_count": typed,
            "ignored_order_days": ignored_days,
            "refused_proposals": refused,
            "backlog_qty": float(sum(l.backlog_qty for l in lanes)),
            "backlog_ordered": float(sum(l.backlog_ordered for l in lanes)),
            "backlog_received": float(sum(l.backlog_received for l in lanes)),
            "proposed_qty": float(supply_proposed.sum()),
            "proposal_count": len(proposals),
            "urgent_proposal_count": sum(1 for p in proposals if p.urgent),
            "alert_count": len(alerts),
            "severity": (worst_severity(alerts).value if alerts else None),
            "actual_share_30d": float(share[i_as_of - 30 if i_as_of >= 30 else 0:i_as_of + 1].mean()) if i_as_of > 0 else 0.0,
            "lanes": len(lanes),
            "next_delivery_date": next_delivery[0].isoformat() if next_delivery else None,
            "next_delivery_qty": next_delivery[1] if next_delivery else None,
            "next_delivery_type": next_delivery[2] if next_delivery else None,
        }
        results[aid] = ArticleResult(
            article=article, start_date=start, as_of=as_of, dates=index.dates,
            demand=demand, consumed=consumed, required=required, demand_plan=dplan, demand_actual_share=share,
            orders_firm=orders_firm, orders_firm_hist=lane_totals(lanes, "orders_firm_hist"),
            orders_forecast=lane_totals(lanes, "orders_forecast"), receipts=receipts,
            plan=plan, supply_proposed=supply_proposed,
            adjustments=adjustments + past_adjust, reference_correction=reference_correction,
            stock_erp=erp.stock, stock_plan=plan_proj.stock,
            shortage_onhand=onhand.shortage, shortage_erp=erp.shortage, shortage_plan=plan_proj.shortage,
            stock_erp_net=erp.net, stock_plan_net=plan_proj.net,
            coverage_erp=cov["erp"], coverage_plan=cov["plan"], target_stock=target,
            lanes=lanes, alerts=alerts, proposals=proposals, kpis=kpis, suppliers=links_by_article.get(aid, []),
            diagnostics=notes, proposals_ignored=proposals_ignored, price=price,
        )

    program_daily = {pid: {index.dates[i]: float(v) for i, v in enumerate(arr) if v}
                     for pid, arr in program_plan.items()}
    names = {p.program_id: p.name for p in dataset.programs}
    impact = program_impact(results, dataset.bom, program_plan, names, index, as_of, params.shortage_policy)
    return MrpResult(as_of=as_of, init_date=init_date, start_date=start, end_date=end, params=params, articles=results,
                     program_daily=program_daily, diagnostics=diagnostics, program_impact=impact)


__all__ = ["run_mrp", "resolve_as_of", "resolve_init_date", "Alert", "OrderType"]
