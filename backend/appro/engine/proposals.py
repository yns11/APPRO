"""Order proposals (« Proposition CBN ») : floor inside the lead time, full target after it,
tolerance, lot sizing, delivery caps, quotas and refusals.

Algorithm (per article, on the *plan* stock, scanned day by day from tomorrow)::

    for each day d:
        delivery day of each eligible supplier = its previous allowed day (« earlier » shift), or the
            next one when that day is refused or full ; urgent when the order should already be placed
            (delivery before as_of + lead time, working days, inclusive)
        inside the lead time (every supplier urgent) : only a **shortage** triggers a proposal, sized to
            what avoids it (floor) – the stock is not brought back to the target ;
        after it : a stock under (100 − tolerance) % of the target triggers a proposal that fills the
            stock back to 100 % of the target ;
        qty = round_up(max(need, MOQ), pack) then truncated by the delivery / weekly caps of the link ;
            a full day never receives more, the rest slides to the next delivery day ; with quotas the
            truncated share goes to the other supplier(s) of the quota ;
        re-project the simulated stock with the proposal and continue scanning.

Supplier choice follows the article's sourcing policy: ``quota`` splits every need between the quota
suppliers in proportion to their quotas (each part lot-sized with its own MOQ / pack quantity,
delivered on its own allowed day with its own lead time), ``priority`` gives everything to the
priority-1 supplier.  A supplier the planner refused for a day hands its share to the others ; when
every supplier is refused the need moves to the next delivery day.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Callable

import numpy as np

from .calendar import WorkCalendar, iso_week_label
from .demand import DayIndex, ceil_to_multiple, floor_to_multiple
from .models import Article, CapShortfall, EngineParams, Proposal, Supplier, SupplierLink
from .projection import Projection

EPS = 1e-6
INF = float("inf")


def _fmt(v: float) -> str:
    """Quantity in a message : thousands separated by a space, as in the screens."""
    return f"{v:,.0f}".replace(",", " ") if abs(v - round(v)) < 1e-6 else f"{v:,.3f}".replace(",", " ")


def eligible_suppliers(links: list[SupplierLink], policy: str) -> list[tuple[SupplierLink | None, float]]:
    """The suppliers a need is split between, with their share (Σ = 1).  ``priority`` : the priority-1
    supplier alone ; ``quota`` : every supplier with a quota, in proportion (the priority-1 supplier
    alone when no quota is set) ; ``[(None, 1.0)]`` without any active supplier."""
    active = [l for l in links if l.active]
    if not active:
        return [(None, 1.0)]
    first = min(active, key=lambda l: (l.priority, l.supplier_id))
    quota_links = [l for l in active if (l.quota_pct or 0) > 0] if policy != "priority" else []
    if not quota_links:
        return [(first, 1.0)]
    total = sum(l.quota_pct for l in quota_links)
    return [(l, l.quota_pct / total) for l in sorted(quota_links, key=lambda l: (l.priority, l.supplier_id))]


def is_refused(blocked: dict[str | None, set[dt.date]] | None, supplier_id: str | None, day: dt.date) -> bool:
    """Did the planner refuse a proposal to ``supplier_id`` on ``day`` ?"""
    b = blocked or {}
    return day in b.get(None, set()) or (supplier_id is not None and day in b.get(supplier_id, set()))


@dataclass
class _Link:
    """Per-supplier state of one run : lead time, delivery days, caps, quantities already placed."""

    link: SupplierLink | None
    supplier_id: str | None
    lead: int
    weekdays: frozenset[int] | None
    tenable: dt.date                 # first delivery day whose order can still be placed today
    cap_day: float | None
    cap_week: float | None
    pack: float
    moq: float
    plan: np.ndarray                 # firm orders and plan cells of the lane (count in the caps)
    proposed: np.ndarray
    refused: set[dt.date]
    week_of: list[str]
    _plan_week: dict[str, float] | None = None

    def plan_week(self, wk: str) -> float:
        if self._plan_week is None:
            acc: dict[str, float] = {}
            for i, w in enumerate(self.week_of):
                if self.plan[i]:
                    acc[w] = acc.get(w, 0.0) + float(self.plan[i])
            self._plan_week = acc
        return self._plan_week.get(wk, 0.0)

    def proposed_week(self, wk: str) -> float:
        return float(sum(float(self.proposed[i]) for i, w in enumerate(self.week_of) if w == wk and self.proposed[i]))

    def room(self, j: int) -> tuple[float, str]:
        """Quantity the day ``j`` can still receive (multiple of the pack) and the binding cap."""
        r, which = INF, ""
        if self.cap_day is not None:
            r, which = max(0.0, self.cap_day - float(self.plan[j]) - float(self.proposed[j])), "livraison"
        if self.cap_week is not None:
            wk = self.week_of[j]
            rw = max(0.0, self.cap_week - self.plan_week(wk) - self.proposed_week(wk))
            if rw < r:
                r, which = rw, "semaine"
        if r == INF:
            return r, ""
        return floor_to_multiple(r, self.pack) if self.pack else r, which

    def full(self, j: int) -> bool:
        r, _ = self.room(j)
        return r < (self.pack if self.pack else EPS)


def _delivery_day(st: _Link, candidate: dt.date, earliest: dt.date, latest: dt.date, calendar: WorkCalendar,
                  index: DayIndex, with_constraints: bool = True) -> dt.date | None:
    """Delivery day for a need on ``candidate`` : the previous allowed day of the supplier (needs are
    grouped on the delivery day before them), unless that day is refused or full, in which case the
    first allowed, free day **after** the candidate (the need slides, never earlier)."""
    def base(d: dt.date) -> bool:
        return calendar.is_open_weekday(d, st.weekdays)

    def ok(d: dt.date) -> bool:
        if not base(d):
            return False
        if not with_constraints:
            return True
        j = index.offset(d)
        return d not in st.refused and j is not None and not st.full(j)

    d = candidate
    while d >= earliest:
        if base(d):
            if ok(d):
                return d
            break
        d -= dt.timedelta(days=1)
    d = max(candidate, earliest)
    while d <= latest:
        if ok(d):
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
    blocked: dict[str | None, set[dt.date]] | None = None,
    lane_plan: dict[str | None, np.ndarray] | None = None,
) -> tuple[list[Proposal], np.ndarray, np.ndarray, list[CapShortfall]]:
    """Return proposals, the proposed-supply series, the resulting simulated stock and the needs no
    cap could absorb.

    ``reproject(proposed)`` recomputes the projection with the proposed supply added (a receipt that
    arrives after a lost day does not serve that day).  ``blocked`` maps a supplier (``None`` = every
    supplier) to the days the planner refused.  ``lane_plan`` gives, per supplier, the firm orders and
    plan cells already expected (they count in the delivery caps)."""
    n = index.n
    proposed = np.zeros(n)
    proj = reproject(proposed) if reproject is not None else None
    stock = proj.net.copy() if proj is not None else stock_sim.copy()
    shortage = proj.shortage.copy() if proj is not None else np.maximum(-stock, 0.0)
    proposals: list[Proposal] = []
    shortfalls: list[CapShortfall] = []
    as_of_idx = index.offset(as_of)
    if as_of_idx is None:
        return proposals, proposed, stock, shortfalls
    earliest_idx = as_of_idx + 1
    if earliest_idx >= n:
        return proposals, proposed, stock, shortfalls
    last_idx = n - 1
    earliest_date, latest_date = index.dates[earliest_idx], index.dates[last_idx]
    tol = max(float(params.target_tolerance_pct), 0.0) / 100.0
    trigger = target * (1.0 - tol)
    today = as_of if calendar.is_working_day(as_of) else calendar.next_working_day(as_of)
    week_of = index.week_labels
    states: dict[str | None, _Link] = {}
    for link, _share in eligible_suppliers(links, article.sourcing_policy):
        sid = link.supplier_id if link else None
        sup = suppliers.get(sid) if sid else None
        lead = link.lead_time(suppliers) if link else 0
        states[sid] = _Link(
            link=link, supplier_id=sid, lead=lead, weekdays=sup.delivery_weekdays if sup else None,
            tenable=calendar.add_working_days(today, lead), cap_day=link.max_qty_per_delivery if link else None,
            cap_week=link.max_qty_per_week if link else None, pack=float(link.pack_qty) if link else 0.0,
            moq=float(link.moq) if link else 0.0,
            plan=(lane_plan or {}).get(sid, np.zeros(n)), proposed=np.zeros(n),
            refused=(blocked or {}).get(None, set()) | ((blocked or {}).get(sid, set()) if sid else set()),
            week_of=week_of)
    seq = seq_start
    i = earliest_idx
    while i <= last_idx:
        short = shortage[i] > EPS
        under = stock[i] < trigger[i] - EPS
        if not (short or under):
            i += 1
            continue
        # ---- one delivery day per eligible supplier (own lead time, delivery days, refusals, caps)
        parts = []   # (state, share, delivery day, free of any refusal / cap, urgent, pushed by a full day)
        for link, share in eligible_suppliers(links, article.sourcing_policy):
            st = states[link.supplier_id if link else None]
            delivery = _delivery_day(st, index.dates[i], earliest_date, latest_date, calendar, index)
            if delivery is None:
                continue
            free = _delivery_day(st, index.dates[i], earliest_date, latest_date, calendar, index, with_constraints=False)
            j_free = index.offset(free) if free is not None else None
            by_cap = free is not None and delivery != free and free not in st.refused and j_free is not None and st.full(j_free)
            parts.append((st, share, delivery, delivery == free, delivery < st.tenable, by_cap))
        if not parts:
            break  # no feasible delivery day inside the horizon
        chosen = [x for x in parts if x[3]] or parts
        floor = all(x[4] for x in chosen)
        if floor and not short:
            i += 1          # inside the lead time, only a shortage is worth an urgent order
            continue
        total_share = sum(x[1] for x in chosen)
        # The need is evaluated on the first delivery day when it had to be pushed later than the
        # trigger day (the days in between are an unavoidable shortage, reported as an alert).
        k = max(i, min(index.offset(x[2]) for x in chosen))
        if k > i and any(x[5] for x in chosen) and shortage[i:k].sum() > EPS:
            # the previous delivery day is full : the need slides to the next one and the days in
            # between are a shortage no cap could absorb
            shortfalls.append(CapShortfall(index.dates[i], chosen[0][0].supplier_id, float(shortage[i:k].sum())))
        if floor:
            if shortage[k] <= EPS:
                i = k + 1
                continue
            need = float(shortage[k])
        else:
            if stock[k] >= trigger[k] - EPS and shortage[k] <= EPS:
                i = k + 1
                continue
            need = float(target[k] - stock[k] + shortage[k])
        before = float(stock[k])
        # ---- split, lot-size, cap ; a truncated share goes to the other suppliers of the quota
        wanted = {id(x[0]): need * x[1] / total_share for x in chosen}
        lines: list[tuple[_Link, float, dt.date, int, float, float, str, bool]] = []
        for _pass in range(2):
            lines = []
            leftover = 0.0
            open_states = []
            for st, share, delivery, _free, urgent, _pushed in chosen:
                part = wanted[id(st)]
                if part <= EPS:
                    continue
                qty = ceil_to_multiple(max(part, st.moq), st.pack)
                j = index.offset(delivery)
                room, which = st.room(j)
                capped = room < qty - EPS
                placed = min(qty, room) if capped else qty
                if placed <= EPS:
                    leftover += part
                    continue
                if capped:
                    leftover += max(0.0, part - placed)
                else:
                    open_states.append(st)
                lines.append((st, share / total_share, delivery, j, placed, part, which if capped else "", urgent))
            if leftover <= EPS or not open_states or _pass:
                break
            extra_share = sum(x[1] for x in chosen if x[0] in open_states)
            for st, share, _d, _f, _u, _p in chosen:
                if st in open_states:
                    wanted[id(st)] += leftover * share / extra_share
        if not lines:
            shortfalls.append(CapShortfall(index.dates[k], chosen[0][0].supplier_id, need))
            i = k + 1
            continue
        for st, _s, _d, j, placed, _p, _w, _u in lines:
            proposed[j] += placed
            st.proposed[j] += placed
        if reproject is not None:
            proj = reproject(proposed)
            stock, shortage = proj.net, proj.shortage
        else:
            for _st, _s, _d, j, placed, _p, _w, _u in lines:
                stock[j:] += placed
            shortage = np.maximum(-stock, 0.0)
        if shortage[k] > EPS and any(x[6] for x in lines):
            shortfalls.append(CapShortfall(index.dates[k], lines[0][0].supplier_id, float(shortage[k])))
        split = len(lines) > 1
        for st, share, delivery, j, placed, part, cap_kind, urgent in lines:
            order_date = calendar.add_working_days(delivery, -st.lead)
            if floor:
                reason = (f"Plancher : besoin non servi de {_fmt(need)} le {index.dates[k].isoformat()}, lot minimal pour l'éviter ; "
                          f"délai fournisseur ({st.lead} j ouvrés) non tenable, commande à passer immédiatement")
            else:
                reason = (f"Stock projeté {_fmt(before)} < {100 - tol * 100:.0f} % de la cible ({_fmt(trigger[k])}) le "
                          f"{index.dates[k].isoformat()} : retour à la cible {_fmt(target[k])}")
                if urgent:
                    reason += f" ; délai fournisseur ({st.lead} j ouvrés) non tenable, commande à passer immédiatement"
            if split:
                reason += f" ; part {share * 100:.0f} % du quota ({_fmt(part)} sur {_fmt(need)})"
            if j > i:
                reason += f" (besoin dès le {index.dates[i].isoformat()}, première livraison possible le {delivery.isoformat()})"
            if cap_kind:
                cap = st.cap_day if cap_kind == "livraison" else st.cap_week
                reason += (f" ; plafond par {cap_kind} ({_fmt(cap or 0)}) atteint : {_fmt(placed)} livrés, "
                           f"le reste glisse à la livraison suivante")
            if urgent and supply_planned is not None:
                later = np.where(supply_planned[j + 1:min(n, j + 60)] > 0)[0]
                if len(later):
                    d_later = index.dates[j + 1 + int(later[0])]
                    reason += (f" ; alternative : avancer la commande prévisionnelle du "
                               f"{d_later.isoformat()} ({_fmt(supply_planned[j + 1 + int(later[0])])})")
            proposals.append(Proposal(
                proposal_id=f"PR-{article.article_id}-{seq:03d}", article_id=article.article_id,
                supplier_id=st.supplier_id, delivery_date=delivery, order_date=order_date, qty=float(placed),
                net_requirement=float(max(part, 0.0)), reason=reason, urgent=urgent, lead_time_days=st.lead,
                moq=st.moq, pack_qty=st.pack, projected_stock_before=before, projected_stock_after=float(stock[k]),
                floor=floor, capped=bool(cap_kind)))
            seq += 1
        i = k + 1
    return merge_same_day(proposals), proposed, stock, shortfalls


def merge_same_day(proposals: list[Proposal]) -> list[Proposal]:
    """One proposal per (supplier, delivery day): several needs of the same interval land on the same
    delivery day and would otherwise be listed as many small lines everywhere (grid, lists, tooltips)."""
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
        cur.floor = cur.floor and p.floor
        cur.capped = cur.capped or p.capped
        cur.projected_stock_after = p.projected_stock_after
        if cur.reason.count(" ; ") < 2 and p.reason not in cur.reason:
            cur.reason = f"{cur.reason} ; {p.reason}"
    out = sorted(merged.values(), key=lambda p: (p.delivery_date, p.supplier_id or ""))
    for k, p in enumerate(out, start=1):
        p.proposal_id = f"PR-{p.article_id}-{k:03d}"
    return out


def week_key(day: dt.date) -> str:
    return iso_week_label(day)
