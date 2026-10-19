"""Portfolio-level orchestration of the MRP engine."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

import numpy as np

from .alerts import classify_alerts, status_of, worst_severity
from .calendar import WorkCalendar, iso_week_monday
from .demand import DayIndex, actual_share, build_article_demand, build_program_daily, explode_demand
from .models import Alert, ArticleResult, Dataset, DatasetError, EngineParams, MrpResult, OrderType
from .programs import program_impact
from .projection import Projection, coverage_days, coverage_from, first_shortage, project_stock, target_stock
from .proposals import generate_proposals, is_refused
from .supply import blocked_days, build_lanes, lane_totals, unlinked_flows


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
    articles_by_id = {a.article_id: a for a in dataset.articles}

    # ---------------------------------------------------------------- demand
    # PDP → daily production per programme → exploded component demand, profiled per article
    # (demand_days) ; then the reported actual consumption (already per component) replaces the past
    # days and drives the current-week remainder, according to the consumption source of the article
    program_plan, diag = build_program_daily(dataset.pdp, calendar, index)
    diagnostics.extend(diag)
    demand_plan = explode_demand(program_plan, dataset.bom, index)
    demand_eff, actual_mask = build_article_demand(demand_plan, dataset.consumption, articles_by_id, calendar, index, as_of)

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
        unlinked = unlinked_flows([l.supplier_id for l in lanes], orders_by_article.get(aid, []), receipts_by_article.get(aid, []),
                                  desadv_by_article.get(aid), supplier_names, as_of)
        receipts = lane_totals(lanes, "receipts", n)
        orders_firm = lane_totals(lanes, "orders_firm", n)
        plan = lane_totals(lanes, "plan", n)

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
            proj = project_stock(stock_start, supply, adjust_proj, demand_proj, i_snap)
            proj.net[:i_snap + 1] = history
            proj.stock[:i_snap + 1] = np.maximum(history, 0.0)
            return proj

        onhand = project(np.zeros(n))
        erp = project(inflow_erp)
        plan_proj = project(inflow_plan)
        target = target_stock(demand, article, index, calendar)

        # ---- proposals (always computed, always part of the plan stock)
        blocked = blocked_days(flags_by_article.get(aid), aid)
        lane_plan = {l.supplier_id: l.plan for l in lanes}
        proposals, supply_proposed, _, cap_shortfalls = generate_proposals(
            article, links_by_article.get(aid, []), suppliers, plan_proj.net, demand, target,
            index, calendar, as_of, params, supply_planned=lane_totals(lanes, "orders_forecast", n),
            reproject=lambda extra: project(inflow_plan + extra), blocked=blocked, lane_plan=lane_plan)
        proposals_ignored: list = []
        if blocked:
            # what the engine would have proposed without the refusals : the refused ones stay listed
            # (« ignorée ») in the ordering flow, outside every calculation
            free, _, _, _ = generate_proposals(article, links_by_article.get(aid, []), suppliers, plan_proj.net, demand,
                                               target, index, calendar, as_of, params,
                                               reproject=lambda extra: project(inflow_plan + extra), lane_plan=lane_plan)
            for p in free:
                if is_refused(blocked, p.supplier_id, p.delivery_date):
                    p.ignored = True
                    proposals_ignored.append(p)
        plan_proj = project(inflow_plan + supply_proposed)
        by_lane = {l.supplier_id: l for l in lanes}
        for p in proposals:
            lane = by_lane.get(p.supplier_id) or (lanes[0] if lanes else None)
            j = index.offset(p.delivery_date)
            if j is not None and lane is not None:
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
        cov = {name: coverage_days(layer.net, demand, index, calendar) for name, layer in (("erp", erp), ("plan", plan_proj))}

        # stock « à date » = the stock at the end of yesterday (the reference day itself is not over) ;
        # identical in both scenarios before today, so read on the ERP one.  Its coverage counts no inflow.
        stock_at_date = float(erp.stock[i_as_of - 1]) if i_as_of > 0 else float(max(stock_start, 0.0))
        cov_at_date, runout = coverage_from(stock_at_date, demand, i_as_of, index, calendar)
        end_check = runout if runout is not None else n - 1
        inflow_before_runout = bool(float(orders_firm[i_as_of:end_check + 1].sum() + plan[i_as_of:end_check + 1].sum()) > 1e-9)

        alerts = classify_alerts(article, index, calendar, as_of, plan_proj, stock_start, stock_at_date, cov_at_date, runout,
                                 inflow_before_runout, demand, proposals, links_by_article.get(aid, []), supplier_names,
                                 aid in bom_articles, snap is not None, cap_shortfalls, unlinked)
        k = {name: first_shortage(layer.shortage, i_as_of) for name, layer in (("erp", erp), ("plan", plan_proj))}
        horizon_slice = slice(i_as_of, n)
        typed = int(sum(int(l.plan_typed[i_as_of:].sum()) for l in lanes))
        ignored_days = int(sum(int(l.orders_ignored[i_as_of:].sum()) for l in lanes))
        refused = sum(1 for f in flags_by_article.get(aid, []) if f.kind == "proposal_refused")
        price = dataset.prices.get(aid)
        # next firm delivery expected (ERP, not ignored) and first order to place (CBN), with their supplier
        firm_next = sorted((o for l in lanes for o in l.orders
                            if o.order_type == "FIRM" and o.expected_date >= as_of and o.qty_open > 1e-9 and not o.ignored),
                           key=lambda o: (o.expected_date, o.supplier_id or ""))
        next_firm = firm_next[0] if firm_next else None
        due = sorted(proposals, key=lambda p: (p.order_date, p.delivery_date, p.supplier_id or ""))
        order_due = due[0] if due else None
        data_issues = sorted({a.alert_type.value for a in alerts if a.scope == "data"})
        # requirement of the next 20 working days (four weeks) and its average per working day
        wd_mask = calendar.working_mask(index.start, n)
        future_wd = [i for i in range(i_as_of + 1, n) if wd_mask[i]][:20]
        demand_20wd = float(demand[future_wd].sum()) if future_wd else 0.0
        lead = min((l.lead_time(suppliers) for l in links_by_article.get(aid, []) if l.active), default=5)
        kpis = {
            "stock_on_hand": float(stock_snapshot),
            "reference_correction": reference_correction,
            "stock_reference": float(stock_start),
            "stock_at_date": stock_at_date,
            "price": price,
            "stock_value": (stock_at_date * price) if price is not None else None,
            "target_value": (float(target[i_as_of]) * price) if price is not None else None,
            "status": status_of(alerts),
            "coverage_at_date_days": int(cov_at_date),
            "runout_date": index.dates[runout].isoformat() if runout is not None else None,
            "overstock": any(a.alert_type.value == "OVERSTOCK" for a in alerts),
            "data_issues": data_issues,
            "next_firm_date": next_firm.expected_date.isoformat() if next_firm else None,
            "next_firm_supplier_id": next_firm.supplier_id if next_firm else None,
            "next_firm_supplier_name": supplier_names.get(next_firm.supplier_id or "", "") if next_firm else None,
            "next_firm_qty": float(next_firm.qty_open) if next_firm else None,
            "order_due_date": order_due.order_date.isoformat() if order_due else None,
            "order_due_delivery_date": order_due.delivery_date.isoformat() if order_due else None,
            "order_due_supplier_id": order_due.supplier_id if order_due else None,
            "order_due_supplier_name": supplier_names.get(order_due.supplier_id or "", "") if order_due else None,
            "order_due_qty": float(order_due.qty) if order_due else None,
            "order_due_late": bool(order_due and (order_due.urgent or order_due.order_date < as_of)),
            "init_date": snap_date.isoformat(),
            "stock_as_of_erp": float(erp.stock[i_as_of]),
            "stock_as_of_plan": float(plan_proj.stock[i_as_of]),
            "coverage_erp_days": int(cov["erp"][i_as_of]),
            "coverage_plan_days": int(cov["plan"][i_as_of]),
            "coverage_target_days": int(article.param_at("coverage_target_days", as_of)),
            "alert_red_days": float(article.param_at("alert_red_days", as_of)),
            "alert_yellow_days": float(article.param_at("alert_yellow_days", as_of)),
            "overstock_days": float(article.param_at("overstock_days", as_of) or 0),
            "target_stock": float(target[i_as_of]),
            "lead_time_days": lead,
            "demand_days": int(article.demand_days),
            "consumption_source": article.consumption_source,
            "first_stockout_erp": index.dates[k["erp"]].isoformat() if k["erp"] is not None else None,
            "first_stockout_plan": index.dates[k["plan"]].isoformat() if k["plan"] is not None else None,
            "min_stock_erp": float(np.min(erp.stock[horizon_slice])),
            "min_stock_plan": float(np.min(plan_proj.stock[horizon_slice])),
            "max_shortage_erp": float(np.max(erp.shortage_cum[horizon_slice])),
            "max_shortage_plan": float(np.max(plan_proj.shortage_cum[horizon_slice])),
            "demand_next_7d": float(demand[i_as_of + 1:i_as_of + 8].sum()),
            "demand_next_20wd": demand_20wd,
            "demand_horizon": float(demand[horizon_slice].sum()),
            "avg_daily_demand_wd": demand_20wd / max(1, len(future_wd)),
            "working_days_counted": len(future_wd),
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
            "capped_proposal_count": sum(1 for p in proposals if p.capped),
            "alert_count": len(alerts),
            "severity": (worst_severity(alerts).value if alerts else None),
            "actual_share_30d": float(share[i_as_of - 30 if i_as_of >= 30 else 0:i_as_of + 1].mean()) if i_as_of > 0 else 0.0,
            "lanes": len(lanes),
            "unlinked_flows": sum(u.orders_open + u.orders_late + u.receipts + u.desadv for u in unlinked),
            "next_delivery_date": next_delivery[0].isoformat() if next_delivery else None,
            "next_delivery_qty": next_delivery[1] if next_delivery else None,
            "next_delivery_type": next_delivery[2] if next_delivery else None,
        }
        results[aid] = ArticleResult(
            article=article, start_date=start, as_of=as_of, dates=index.dates,
            demand=demand, consumed=consumed, required=required, demand_plan=dplan, demand_actual_share=share,
            orders_firm=orders_firm, orders_firm_hist=lane_totals(lanes, "orders_firm_hist", n),
            orders_forecast=lane_totals(lanes, "orders_forecast", n), receipts=receipts,
            plan=plan, supply_proposed=supply_proposed,
            adjustments=adjustments + past_adjust, reference_correction=reference_correction,
            stock_erp=erp.stock, stock_plan=plan_proj.stock,
            shortage_onhand=onhand.shortage, shortage_erp=erp.shortage, shortage_plan=plan_proj.shortage,
            shortage_erp_cum=erp.shortage_cum, shortage_plan_cum=plan_proj.shortage_cum,
            stock_erp_net=erp.net, stock_plan_net=plan_proj.net,
            coverage_erp=cov["erp"], coverage_plan=cov["plan"], target_stock=target,
            lanes=lanes, alerts=alerts, proposals=proposals, kpis=kpis, suppliers=links_by_article.get(aid, []),
            diagnostics=notes, proposals_ignored=proposals_ignored, price=price, cap_shortfalls=cap_shortfalls,
            unlinked=unlinked,
        )

    program_daily = {pid: {index.dates[i]: float(v) for i, v in enumerate(arr) if v}
                     for pid, arr in program_plan.items()}
    names = {p.program_id: p.name for p in dataset.programs}
    impact = program_impact(results, dataset.bom, program_plan, names, index, as_of)
    return MrpResult(as_of=as_of, init_date=init_date, start_date=start, end_date=end, params=params, articles=results,
                     program_daily=program_daily, diagnostics=diagnostics, program_impact=impact, holidays=list(dataset.holidays))


__all__ = ["run_mrp", "resolve_as_of", "resolve_init_date", "Alert", "OrderType"]
