"""Order proposals: net requirement, lot sizing, delivery-day and lead-time rules.

Algorithm (per article, on the *simulated* stock)::

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
    cycle = int(article.order_cycle_days or 0)
    if article.lot_policy == "coverage" and cycle <= 0:
        return float(target[i])
    n = len(target)
    cov = int(article.coverage_target_days or 0)
    if params.coverage_unit == "working":
        end_cov = min(n - 1, i + (calendar.add_working_days(index.dates[i], cov) - index.dates[i]).days)
        end_cycle = min(n - 1, end_cov + (calendar.add_working_days(index.dates[end_cov], cycle) - index.dates[end_cov]).days)
    else:
        end_cov = min(n - 1, i + cov)
        end_cycle = min(n - 1, end_cov + cycle)
    extra = demand_cum[end_cycle + 1] - demand_cum[end_cov + 1]
    return float(target[i] + max(extra, 0.0))


def _delivery_day(candidate: dt.date, earliest: dt.date, latest: dt.date, calendar: WorkCalendar,
                  weekdays: frozenset[int] | None, shift: str) -> dt.date | None:
    """Nearest allowed delivery day for ``candidate`` inside ``[earliest, latest]`` (None if none)."""
    if shift == "earlier":
        try:
            d = calendar.previous_working_day(candidate, inclusive=True, allowed_weekdays=weekdays)
            if d >= earliest:
                return d
        except ValueError:
            pass
    try:
        d = calendar.next_working_day(max(candidate, earliest), inclusive=True, allowed_weekdays=weekdays)
    except ValueError:
        return None
    return d if d <= latest else None


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
) -> tuple[list[Proposal], np.ndarray, np.ndarray]:
    """Return proposals, the proposed-supply series and the resulting simulated net stock.

    ``stock_sim`` is the *net* simulated balance before proposals.  ``reproject(proposed)``
    recomputes the projection with the proposed supply added: it keeps the ``lost`` shortage
    policy exact (a receipt that arrives after a lost day does not serve that day).  Without it
    the proposal is simply added to the balance from its delivery day on (``backlog`` policy).

    ``supply_planned`` (forecast / planned, non-firm supply per day) is only used to enrich the
    reason of urgent proposals: when a later non-firm order exists, advancing it is usually the
    preferred action (MRP "expedite" exception message).
    """
    n = index.n
    stock = stock_sim.copy()
    proposed = np.zeros(n)
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
    seq = seq_start
    i = earliest_idx
    while i <= last_idx:
        if stock[i] < target[i] - EPS:
            link = choose_supplier(links, proposed_by_supplier, params.sourcing_policy)
            supplier = suppliers.get(link.supplier_id) if link else None
            lead = int(link.lead_time_days) if link else 0
            weekdays = supplier.delivery_weekdays if supplier else None
            earliest_date = index.dates[earliest_idx]
            if params.respect_lead_time and link:
                earliest_date = max(earliest_date, calendar.add_working_days(as_of, lead))
            delivery = _delivery_day(index.dates[i], earliest_date, index.dates[last_idx], calendar,
                                     weekdays, params.delivery_shift)
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
                stock = reproject(proposed).net
            else:
                stock[j:] += qty
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
    return proposals, proposed, stock
