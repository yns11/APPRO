"""Order proposals: net requirement, lot sizing, delivery-day and lead-time rules.

Algorithm (per article, on the *plan* stock)::

    for each day d from the first proposable day to the end of the lookahead:
        if stock[d] < target[d]:                         # reorder point reached
            need     = fill_level(d) - stock[d]          # fill level = target + order-cycle demand
            qty      = round_up(max(need, MOQ), pack_qty)
            delivery = nearest allowed delivery day (working day & supplier delivery weekday)
            order    = delivery - lead_time (working days); urgent when order < as_of
            re-project the simulated stock with the proposal and continue scanning

Supplier choice follows the sourcing policy: ``quota`` (keep the cumulated proposed
quantities close to the quota split, deterministic) or ``priority`` (always the priority-1
supplier).  All rules are parameters of :class:`~appro.engine.models.EngineParams` or of the
article / supplier link records.
"""
from __future__ import annotations

import datetime as dt
from typing import Callable

import numpy as np

from .calendar import WorkCalendar
from .demand import DayIndex, ceil_to_multiple
from .models import Article, EngineParams, Proposal, Supplier, SupplierLink
from .projection import Projection

EPS = 1e-6


def choose_supplier(links: list[SupplierLink], proposed_so_far: dict[str, float],
                    policy: str) -> SupplierLink | None:
    active = [l for l in links if l.active]
    if not active:
        return None
    if policy == "priority":
        return min(active, key=lambda l: (l.priority, l.supplier_id))
    quota_links = [l for l in active if (l.quota_pct or 0) > 0]
    if not quota_links:
        return min(active, key=lambda l: (l.priority, l.supplier_id))
    total = sum(proposed_so_far.get(l.supplier_id, 0.0) for l in quota_links)
    total_quota = sum(l.quota_pct for l in quota_links)

    def deficit(l: SupplierLink) -> float:
        share = proposed_so_far.get(l.supplier_id, 0.0) / total if total > 0 else 0.0
        return (l.quota_pct / total_quota) - share

    return max(quota_links, key=lambda l: (deficit(l), -l.priority, l.supplier_id))


def fill_level(target: np.ndarray, demand_cum: np.ndarray, i: int, article: Article,
               index: DayIndex, calendar: WorkCalendar, params: EngineParams) -> float:
    """Level to reach after the delivery: target + demand of the next order cycle."""
    if article.lot_policy == "fixed_lot":
        return float(target[i])
    cycle = int(article.param_at("order_cycle_days", index.dates[i]) or 0)
    if article.lot_policy == "coverage" and cycle <= 0:
        return float(target[i])
    n = len(target)
    cov = int(article.param_at("coverage_target_days", index.dates[i]) or 0)
    if params.coverage_unit == "working":
        end_cov = min(n - 1, i + (calendar.add_working_days(index.dates[i], cov) - index.dates[i]).days)
        end_cycle = min(n - 1, end_cov + (calendar.add_working_days(index.dates[end_cov], cycle) - index.dates[end_cov]).days)
    else:
        end_cov = min(n - 1, i + cov)
        end_cycle = min(n - 1, end_cov + cycle)
    extra = demand_cum[end_cycle + 1] - demand_cum[end_cov + 1]
    return float(target[i] + max(extra, 0.0))


def _blocked_until(day: dt.date, blocked: list[tuple[dt.date, dt.date]]) -> dt.date | None:
    """End of the refused window containing ``day`` (None when the day is free)."""
    for a, b in blocked:
        if a <= day <= b:
            return b
    return None


def _delivery_day(candidate: dt.date, earliest: dt.date, latest: dt.date, calendar: WorkCalendar,
                  weekdays: frozenset[int] | None, shift: str,
                  blocked: list[tuple[dt.date, dt.date]] | None = None) -> dt.date | None:
    """Nearest allowed delivery day for ``candidate`` inside ``[earliest, latest]`` (None if none).

    A refused proposal blocks its day and the rest of its ISO week: a candidate inside such a window
    is pushed to the first allowed day **after** the window (never earlier – the planner said no
    delivery that week)."""
    blocked = blocked or []

    def allowed(d: dt.date) -> bool:
        return calendar.is_open_weekday(d, weekdays) and _blocked_until(d, blocked) is None

    until = _blocked_until(candidate, blocked)
    if until is not None:
        candidate, shift = until + dt.timedelta(days=1), "later"
    if shift == "earlier":
        d = candidate
        while d >= earliest:
            if allowed(d):
                return d
            d -= dt.timedelta(days=1)
    d = max(candidate, earliest)
    while d <= latest:
        if allowed(d):
            return d
        d += dt.timedelta(days=1)
    return None


def generate_proposals(
    article: Article,
    links: list[SupplierLink],
    suppliers: dict[str, Supplier],
    stock_sim: np.ndarray,
    demand: np.ndarray,
    target: np.ndarray,
    index: DayIndex,
    calendar: WorkCalendar,
    as_of: dt.date,
    params: EngineParams,
    seq_start: int = 1,
    supply_planned: np.ndarray | None = None,
    reproject: Callable[[np.ndarray], Projection] | None = None,
    blocked: list[tuple[dt.date, dt.date]] | None = None,
) -> tuple[list[Proposal], np.ndarray, np.ndarray]:
    """Return proposals, the proposed-supply series and the resulting simulated net stock.

    ``stock_sim`` is the *net* simulated balance before proposals.  ``reproject(proposed)``
    recomputes the projection with the proposed supply added: it keeps the ``lost`` shortage
    policy exact (a receipt that arrives after a lost day does not serve that day).  Without it
    the proposal is simply added to the balance from its delivery day on (``backlog`` policy).

    ``shortfall_tolerance_days`` (parameter): a dip under the target that recovers by itself within
    that many working days, without any unserved demand, is ignored (no proposal for a one-day
    gap the planner accepts).

    ``supply_planned`` (forecast / planned, non-firm supply per day) is only used to enrich the
    reason of urgent proposals: when a later non-firm order exists, advancing it is usually the
    preferred action (MRP "expedite" exception message).

    ``blocked`` lists the (first day, last day) windows where the planner refused a proposal: no
    delivery is proposed inside them (the need moves after the window).
    """
    n = index.n
    stock = stock_sim.copy()
    proposed = np.zeros(n)
    # unserved demand per day, kept in step with ``stock``: the ``lost`` policy clamps the simulated
    # stock at 0, so the sign of the stock does not tell whether a day was served or not
    shortage = reproject(proposed).shortage if reproject is not None else np.maximum(-stock, 0.0)
    proposals: list[Proposal] = []
    as_of_idx = index.offset(as_of)
    if as_of_idx is None:
        return proposals, proposed, stock
    demand_cum = np.concatenate([[0.0], np.cumsum(demand)])
    earliest_idx = as_of_idx + 1 + max(int(params.frozen_days), 0)
    if earliest_idx >= n:
        return proposals, proposed, stock
    last_idx = n - 1
    if params.proposal_lookahead_days is not None:
        last_idx = min(last_idx, as_of_idx + int(params.proposal_lookahead_days))
    proposed_by_supplier: dict[str, float] = {}
    tolerance = max(int(params.shortfall_tolerance_days), 0)
    seq = seq_start
    i = earliest_idx
    while i <= last_idx:
        if stock[i] < target[i] - EPS:
            if tolerance:
                # tolerated dip: back above target within N working days and never below zero
                k, days = i, 0
                while k + 1 < n and stock[k] < target[k] - EPS and shortage[k] <= EPS and days <= tolerance:
                    k += 1
                    if calendar.is_working_day(index.dates[k]):
                        days += 1
                if stock[k] >= target[k] - EPS and days <= tolerance and shortage[i:k + 1].max() <= EPS:
                    i = k + 1
                    continue
            link = choose_supplier(links, proposed_by_supplier, params.sourcing_policy)
            supplier = suppliers.get(link.supplier_id) if link else None
            lead = int(link.lead_time_days) if link else 0
            weekdays = supplier.delivery_weekdays if supplier else None
            if params.proposal_placement == "monday":
                weekdays = frozenset({1})  # deliveries grouped on Mondays whatever the supplier days
            earliest_date = index.dates[earliest_idx]
            if params.respect_lead_time and link:
                earliest_date = max(earliest_date, calendar.add_working_days(as_of, lead))
            delivery = _delivery_day(index.dates[i], earliest_date, index.dates[last_idx], calendar,
                                     weekdays, params.delivery_shift, blocked)
            if delivery is None:
                break  # no feasible delivery day inside the horizon
            j = index.offset(delivery)
            # The need is evaluated on the delivery day when it had to be pushed later than the
            # trigger day (the days in between are an unavoidable shortage, reported as an alert).
            k = max(i, j)
            if stock[k] >= target[k] - EPS:
                i = k + 1  # the delivery day is already covered: the shortage before it cannot be fixed
                continue
            level = fill_level(target, demand_cum, k, article, index, calendar, params)
            need = level - stock[k]
            if article.lot_policy == "fixed_lot" and article.fixed_lot_qty > 0:
                qty = ceil_to_multiple(max(need, EPS), article.fixed_lot_qty)
            else:
                qty = max(need, link.moq if link else 0.0)
                qty = ceil_to_multiple(qty, link.pack_qty if link else 0.0)
            if qty <= EPS:
                i = k + 1
                continue
            order_date = calendar.add_working_days(delivery, -lead)
            urgent = order_date < as_of
            before = float(stock[k])
            proposed[j] += qty
            if reproject is not None:
                proj = reproject(proposed)
                stock, shortage = proj.net, proj.shortage
            else:
                stock[j:] += qty
                shortage = np.maximum(-stock, 0.0)
            if link:
                proposed_by_supplier[link.supplier_id] = proposed_by_supplier.get(link.supplier_id, 0.0) + qty
            reason = f"Stock projeté {before:,.0f} < cible {target[k]:,.0f} le {index.dates[k].isoformat()}"
            if j > i:
                reason += f" (besoin dès le {index.dates[i].isoformat()}, première livraison possible le {delivery.isoformat()})"
            if urgent:
                reason += f" – délai fournisseur ({lead} j ouvrés) non tenable, commande à passer immédiatement"
                if supply_planned is not None:
                    later = np.where(supply_planned[j + 1:min(n, j + 60)] > 0)[0]
                    if len(later):
                        d_later = index.dates[j + 1 + int(later[0])]
                        reason += (f" ; alternative : avancer la commande prévisionnelle / planifiée du "
                                   f"{d_later.isoformat()} ({supply_planned[j + 1 + int(later[0])]:,.0f})")
            proposals.append(Proposal(
                proposal_id=f"PR-{article.article_id}-{seq:03d}",
                article_id=article.article_id,
                supplier_id=link.supplier_id if link else None,
                delivery_date=delivery,
                order_date=max(order_date, as_of) if urgent else order_date,
                qty=float(qty),
                net_requirement=float(max(need, 0.0)),
                reason=reason,
                urgent=urgent,
                lead_time_days=lead,
                moq=float(link.moq) if link else 0.0,
                pack_qty=float(link.pack_qty) if link else 0.0,
                projected_stock_before=before,
                projected_stock_after=float(stock[k]),
            ))
            seq += 1
            i = k + 1
            continue
        i += 1
    return merge_same_day(proposals), proposed, stock


def merge_same_day(proposals: list[Proposal]) -> list[Proposal]:
    """One proposal per (supplier, delivery day): several needs of the same week land on the same
    delivery day (Monday placement, supplier delivery days) and would otherwise be listed as many
    small lines everywhere (grid, lists, tooltips, exports)."""
    merged: dict[tuple[str | None, dt.date], Proposal] = {}
    for p in proposals:
        key = (p.supplier_id, p.delivery_date)
        cur = merged.get(key)
        if cur is None:
            merged[key] = Proposal(**p.__dict__)
            continue
        cur.qty += p.qty
        cur.net_requirement += p.net_requirement
        cur.order_date = min(cur.order_date, p.order_date)
        cur.urgent = cur.urgent or p.urgent
        cur.projected_stock_after = p.projected_stock_after
        if cur.reason.count(" ; ") < 2 and p.reason not in cur.reason:
            cur.reason = f"{cur.reason} ; {p.reason}"
    out = sorted(merged.values(), key=lambda p: (p.delivery_date, p.supplier_id or ""))
    for k, p in enumerate(out, start=1):
        p.proposal_id = f"PR-{p.article_id}-{k:03d}"
    return out
