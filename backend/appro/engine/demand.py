"""Demand computation: weekly plan → daily production → component demand.

Business rules (docs/regles_metier.md) :

* **Weekly spreading** – a weekly PDP quantity is spread over the *open* days of its ISO week
  (working weekdays minus holidays) with integer quantities whose sum equals ``round(qty)``
  (largest-remainder method).

* **Programme scrap** – the weekly PDP quantity is raised by the programme scrap rate in force that
  week (``PdpLine.scrap_pct``, typed in the PDP sheet, dated) : need = PDP × (1 + scrap% / 100),
  before the BOM explosion.

* **BOM explosion** – component demand = Σ production × qty_per × (1 + scrap%).

* **Consumption profile** (``Article.demand_days``, 1..5) – the weekly requirement of a component is
  consumed on the **first n open days** of its week (n = 5 : every open day).  The profile is applied
  after the explosion, per component : a campaign article consumed on Monday–Wednesday carries its
  whole week on those three days.

* **Effective demand** (per component) – past days use the reported actual **consumption** of the
  component (a past day without report is a day without movement) ; the current ISO week spreads the
  *remainder* of its exploded PDP (demand of the week − consumption already reported this week,
  floored at 0) over the remaining profile days from today, or over the remaining open days of the
  week when the profile days are over ; later weeks use the profile.  The consumption comes **already
  exploded** per component (upstream pipeline) ; only the PDP goes through the bill of material.

* **Consumption source** (``Article.consumption_source``) – ``erp`` : the reported consumption ;
  ``plan`` : the article has no reliable consumption data, its past is the exploded PDP ; ``derived`` :
  its consumption is Σ coefficient × consumption of other (ERP-sourced) articles.
"""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict

import numpy as np

from .calendar import WorkCalendar, iso_week_label, iso_week_monday
from .models import Article, BomLine, ConsumptionLine, Dataset, PdpLine


def spread_week(qty: float, n_days: int, rounding: str = "exact") -> list[float]:
    """Spread a weekly quantity over ``n_days`` open days.  ``exact`` (the rule) gives integer
    quantities whose sum equals ``round(qty)`` ; ``none`` keeps floats (decimal units : KG, M)."""
    if n_days <= 0:
        return []
    if rounding == "none":
        return [qty / n_days] * n_days
    total = int(round(qty))
    base, remainder = divmod(abs(total), n_days)
    sign = 1 if total >= 0 else -1
    return [sign * float(base + (1 if i < remainder else 0)) for i in range(n_days)]


class DayIndex:
    """Maps dates of the projection window to integer offsets."""

    def __init__(self, start: dt.date, end: dt.date) -> None:
        if end < start:
            raise ValueError("end must be >= start")
        self.start = start
        self.end = end
        self.n = (end - start).days + 1
        self.dates = [start + dt.timedelta(days=i) for i in range(self.n)]
        self._weeks: list[str] | None = None

    @property
    def week_labels(self) -> list[str]:
        """ISO week label of every day (computed once, shared by every article)."""
        if self._weeks is None:
            self._weeks = [iso_week_label(d) for d in self.dates]
        return self._weeks

    def offset(self, day: dt.date) -> int | None:
        i = (day - self.start).days
        return i if 0 <= i < self.n else None


def build_program_daily(plan: list[PdpLine], calendar: WorkCalendar, index: DayIndex) -> tuple[dict[str, np.ndarray], list[str]]:
    """Daily planned production per programme (weekly PDP spread over the open days) plus diagnostics."""
    diagnostics: list[str] = []
    planned: dict[str, np.ndarray] = defaultdict(lambda: np.zeros(index.n))
    # Newest version wins when several versions of the same week coexist.
    best: dict[tuple[str, dt.date], PdpLine] = {}
    for line in plan:
        key = (line.program_id, line.week_start)
        if key not in best or line.version >= best[key].version:
            best[key] = line
    for (program_id, monday), line in best.items():
        # ignore weeks fully outside the window
        if monday + dt.timedelta(days=6) < index.start or monday > index.end:
            continue
        open_days = calendar.open_days_in_week(monday)
        qty = float(line.qty) * (1.0 + max(float(line.scrap_pct or 0.0), 0.0) / 100.0)
        if not open_days:
            if line.qty:
                diagnostics.append(f"{program_id}: week of {monday} has no open day – {line.qty} dropped")
            continue
        for day, q in zip(open_days, spread_week(qty, len(open_days))):
            i = index.offset(day)
            if i is not None:
                planned[program_id][i] += q
    return dict(planned), diagnostics


def explode_demand(program_daily: dict[str, np.ndarray], bom: list[BomLine], index: DayIndex) -> dict[str, np.ndarray]:
    """One-level BOM explosion with scrap (daily, uniform over the open days of the week)."""
    demand: dict[str, np.ndarray] = defaultdict(lambda: np.zeros(index.n))
    for line in bom:
        prod = program_daily.get(line.program_id)
        if prod is None:
            continue
        factor = line.qty_per * (1.0 + (line.scrap_pct or 0.0) / 100.0)
        contrib = prod * factor
        if line.valid_from or line.valid_to:
            valid = np.ones(index.n, dtype=bool)
            for i, day in enumerate(index.dates):
                if (line.valid_from and day < line.valid_from) or (line.valid_to and day > line.valid_to):
                    valid[i] = False
            contrib = contrib * valid
        demand[line.article_id] = demand[line.article_id] + contrib
    return dict(demand)


def profile_days(monday: dt.date, demand_days: int, calendar: WorkCalendar) -> list[dt.date]:
    """The consumption days of one week : its first ``demand_days`` open days (all of them when 5+)."""
    open_days = calendar.open_days_in_week(monday)
    n = max(1, min(int(demand_days or 5), 5))
    return open_days[:n] if n < len(open_days) else open_days


def _is_decimal(values: np.ndarray) -> bool:
    return bool(np.any(np.abs(values - np.round(values)) > 1e-6))


def apply_profile(daily: np.ndarray, demand_days: int, calendar: WorkCalendar, index: DayIndex) -> np.ndarray:
    """Re-spread the exploded daily demand of each ISO week over the first ``demand_days`` open days
    of that week (the weekly total is kept ; 5 = unchanged).  Decimal articles (KG, M) keep decimal
    quantities, integer ones stay integer."""
    n = max(1, min(int(demand_days or 5), 5))
    if n >= 5:
        return daily
    out = np.zeros(index.n)
    decimal = _is_decimal(daily)
    i = 0
    while i < index.n:
        monday = iso_week_monday(index.dates[i])
        j_end = min(index.n, index.offset(monday) + 7 if index.offset(monday) is not None else i + (7 - index.dates[i].weekday()))
        total = float(daily[i:j_end].sum())
        if total > 0:
            days = [d for d in profile_days(monday, n, calendar) if index.offset(d) is not None and i <= index.offset(d) < j_end]
            if not days:   # the profile days of this week are outside the window : keep the uniform spread
                out[i:j_end] = daily[i:j_end]
            else:
                parts = [total / len(days)] * len(days) if decimal else spread_week(total, len(days))
                for d, q in zip(days, parts):
                    out[index.offset(d)] += q
        i = j_end
    return out


def build_article_demand(
    demand_plan: dict[str, np.ndarray],
    consumption: list[ConsumptionLine],
    articles: dict[str, Article],
    calendar: WorkCalendar,
    index: DayIndex,
    as_of: dt.date,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray]]:
    """Effective daily demand per component from the exploded PDP (profiled) and the reported
    consumption, according to the consumption source of each article.

    Returns ``(effective, actual_mask)`` ; ``actual_mask[aid][i]`` is True when the day carries a
    reported (or derived) consumption."""
    reported: dict[str, dict[int, float]] = defaultdict(dict)
    for c in consumption:
        i = index.offset(c.date)
        if i is not None:
            reported[c.article_id][i] = reported[c.article_id].get(i, 0.0) + float(c.qty)
    # derived articles : Σ coefficient × consumption of their (ERP) sources
    for aid, art in articles.items():
        if art.consumption_source == "derived" and art.derived_terms:
            acc: dict[int, float] = {}
            for src, coef in art.derived_terms:
                for i, q in reported.get(src, {}).items():
                    acc[i] = acc.get(i, 0.0) + float(coef) * q
            reported[aid] = acc
    as_of_idx = index.offset(as_of)
    effective: dict[str, np.ndarray] = {}
    actual_mask: dict[str, np.ndarray] = {}
    monday = iso_week_monday(as_of)
    week_idx = [index.offset(monday + dt.timedelta(days=k)) for k in range(7)]
    week_idx = [i for i in week_idx if i is not None]
    for aid in set(demand_plan) | set(reported):
        art = articles.get(aid)
        n_days = int(art.demand_days) if art else 5
        plan = demand_plan.get(aid)
        plan = apply_profile(plan, n_days, calendar, index) if plan is not None else np.zeros(index.n)
        eff = plan.copy()
        mask = np.zeros(index.n, dtype=bool)
        if as_of_idx is None:
            effective[aid], actual_mask[aid] = eff, mask
            continue
        rep = reported.get(aid, {}) if (art is None or art.consumption_source != "plan") else {}
        past = np.arange(index.n) < as_of_idx
        if art is not None and art.consumption_source == "plan":
            pass   # the past is the exploded, profiled PDP
        else:
            for i, q in rep.items():
                if i < as_of_idx:      # today and later are always future
                    eff[i] = q
                    mask[i] = True
            eff[past & ~mask] = 0.0    # a past day without report is a day without movement
            # current ISO week: remainder of the exploded PDP over the remaining profile days
            week_plan = float(sum(plan[i] for i in week_idx))
            done = float(sum(rep.get(i, 0.0) for i in week_idx if i < as_of_idx))
            remaining = [d for d in profile_days(monday, n_days, calendar) if d >= as_of]
            if not remaining:          # the campaign days are over : what is left goes on the remaining open days
                remaining = [d for d in calendar.open_days_in_week(monday) if d >= as_of]
            for i in week_idx:
                if i >= as_of_idx:
                    eff[i] = 0.0
            if remaining:
                rest = max(week_plan - done, 0.0)
                parts = [rest / len(remaining)] * len(remaining) if _is_decimal(plan) else spread_week(rest, len(remaining))
                for day, q in zip(remaining, parts):
                    i = index.offset(day)
                    if i is not None:
                        eff[i] = q
        effective[aid] = eff
        actual_mask[aid] = mask
    return effective, actual_mask


def actual_share(actual_mask: dict[str, np.ndarray], article_id: str, index: DayIndex) -> np.ndarray:
    """Share (0..1) of an article's daily demand that comes from *reported actual* consumption."""
    mask = actual_mask.get(article_id)
    return mask.astype(float) if mask is not None else np.zeros(index.n)


def round_qty(qty: float, unit: str) -> float:
    """Display rounding: integer for pieces, 3 decimals otherwise."""
    if unit.upper() in ("PCE", "PC", "PCS", "EA", "UN"):
        return float(round(qty))
    return float(round(qty, 3))


def ceil_to_multiple(qty: float, multiple: float) -> float:
    if multiple and multiple > 0:
        return math.ceil(qty / multiple - 1e-9) * multiple
    return qty


def floor_to_multiple(qty: float, multiple: float) -> float:
    if multiple and multiple > 0:
        return math.floor(qty / multiple + 1e-9) * multiple
    return qty


__all__ = ["DayIndex", "Dataset", "spread_week", "build_program_daily", "build_article_demand", "explode_demand",
           "apply_profile", "profile_days", "actual_share", "round_qty", "ceil_to_multiple", "floor_to_multiple"]
