"""Demand computation: weekly plan → daily production → component demand.

Business rules (all configurable through :class:`~appro.engine.models.EngineParams`):

* **Weekly spreading** – a weekly PDP quantity is spread over the *open* days of its ISO week
  (working weekdays minus holidays).  Rounding policies:

  - ``none``    : ``qty / n_open_days`` on each open day (floats);
  - ``exact``   : integer quantities whose sum equals ``round(qty)`` (largest-remainder method);
  - ``per_day`` : ``round(qty / n_open_days)`` on each day – the legacy Excel behaviour, whose
    weekly sum may differ from the plan.

* **Effective production** – ``actual_then_plan`` uses the reported actual quantity for a
  (program, day) when one exists – *including an explicit 0* – and the plan otherwise.
  ``plan_only`` ignores actuals, ``actual_only`` ignores the plan.
  ``missing_actual_policy`` decides what to do with past days that have no actual report.

* **BOM explosion** – component demand = Σ effective production × qty_per × (1 + scrap%).
  ``consumption_offset_days`` shifts the consumption relative to the production day
  (negative = components consumed before the production day).
"""
from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict

import numpy as np

from .calendar import WorkCalendar
from .models import ActualLine, BomLine, Dataset, EngineParams, PlanLine


def spread_week(qty: float, n_days: int, rounding: str) -> list[float]:
    """Spread a weekly quantity over ``n_days`` open days according to ``rounding``."""
    if n_days <= 0:
        return []
    if rounding == "none":
        return [qty / n_days] * n_days
    if rounding == "per_day":
        return [float(round(qty / n_days))] * n_days
    if rounding == "exact":
        total = int(round(qty))
        base, remainder = divmod(abs(total), n_days)
        sign = 1 if total >= 0 else -1
        return [sign * float(base + (1 if i < remainder else 0)) for i in range(n_days)]
    raise ValueError(f"Unknown spread rounding policy: {rounding!r}")


class DayIndex:
    """Maps dates of the projection window to integer offsets."""

    def __init__(self, start: dt.date, end: dt.date) -> None:
        if end < start:
            raise ValueError("end must be >= start")
        self.start = start
        self.end = end
        self.n = (end - start).days + 1
        self.dates = [start + dt.timedelta(days=i) for i in range(self.n)]

    def offset(self, day: dt.date) -> int | None:
        i = (day - self.start).days
        return i if 0 <= i < self.n else None


def build_program_daily(
    plan: list[PlanLine],
    actuals: list[ActualLine],
    calendar: WorkCalendar,
    index: DayIndex,
    params: EngineParams,
    as_of: dt.date,
) -> tuple[dict[str, np.ndarray], dict[str, np.ndarray], dict[str, np.ndarray], list[str]]:
    """Return (effective, plan_only, actual_mask) daily arrays per program plus diagnostics."""
    diagnostics: list[str] = []
    planned: dict[str, np.ndarray] = defaultdict(lambda: np.zeros(index.n))
    # Newest version wins when several versions of the same week coexist.
    best: dict[tuple[str, dt.date], PlanLine] = {}
    for line in plan:
        key = (line.program_id, line.week_start)
        if key not in best or line.version >= best[key].version:
            best[key] = line
    for (program_id, monday), line in best.items():
        # ignore weeks fully outside the window
        if monday + dt.timedelta(days=6) < index.start or monday > index.end:
            continue
        open_days = calendar.open_days_in_week(monday)
        if not open_days:
            if line.qty:
                diagnostics.append(f"{program_id}: week of {monday} has no open day – {line.qty} dropped")
            continue
        for day, q in zip(open_days, spread_week(line.qty, len(open_days), params.spread_rounding)):
            i = index.offset(day)
            if i is not None:
                planned[program_id][i] += q

    effective: dict[str, np.ndarray] = {}
    actual_mask: dict[str, np.ndarray] = {}
    reported: dict[str, dict[int, float]] = defaultdict(dict)
    for a in actuals:
        i = index.offset(a.date)
        if i is not None:
            reported[a.program_id][i] = a.qty
    as_of_idx = index.offset(as_of)
    programs = set(planned) | set(reported)
    for pid in programs:
        p = planned[pid] if pid in planned else np.zeros(index.n)
        mask = np.zeros(index.n, dtype=bool)
        if params.production_mode == "plan_only":
            eff = p.copy()
        elif params.production_mode == "actual_only":
            eff = np.zeros(index.n)
            for i, q in reported.get(pid, {}).items():
                eff[i] = q
                mask[i] = True
        else:  # actual_then_plan
            eff = p.copy()
            for i, q in reported.get(pid, {}).items():
                eff[i] = q
                mask[i] = True
            if params.missing_actual_policy == "zero" and as_of_idx is not None:
                past = np.arange(index.n) < as_of_idx
                eff[past & ~mask] = 0.0
        effective[pid] = eff
        actual_mask[pid] = mask
        planned.setdefault(pid, p)
    return effective, dict(planned), actual_mask, diagnostics


def explode_demand(
    program_daily: dict[str, np.ndarray],
    bom: list[BomLine],
    index: DayIndex,
    offset_days: int = 0,
) -> dict[str, np.ndarray]:
    """One-level BOM explosion with scrap and optional consumption offset."""
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
        if offset_days:
            shifted = np.zeros(index.n)
            if offset_days < 0:
                k = -offset_days
                shifted[: index.n - k] = contrib[k:]
                # consumption that falls before the window start is lost (already consumed)
            else:
                k = offset_days
                shifted[k:] = contrib[: index.n - k]
            contrib = shifted
        demand[line.article_id] = demand[line.article_id] + contrib
    return dict(demand)


def actual_share(
    program_daily_eff: dict[str, np.ndarray],
    actual_mask: dict[str, np.ndarray],
    bom: list[BomLine],
    article_id: str,
    index: DayIndex,
) -> np.ndarray:
    """Share (0..1) of an article's daily demand that comes from *reported actual* production."""
    total = np.zeros(index.n)
    from_actual = np.zeros(index.n)
    for line in bom:
        if line.article_id != article_id or line.program_id not in program_daily_eff:
            continue
        contrib = np.abs(program_daily_eff[line.program_id] * line.qty_per)
        total += contrib
        from_actual += contrib * actual_mask[line.program_id]
    with np.errstate(divide="ignore", invalid="ignore"):
        share = np.where(total > 0, from_actual / total, 0.0)
    return share


def round_qty(qty: float, unit: str) -> float:
    """Display rounding: integer for pieces, 3 decimals otherwise."""
    if unit.upper() in ("PCE", "PC", "PCS", "EA", "UN"):
        return float(round(qty))
    return float(round(qty, 3))


def ceil_to_multiple(qty: float, multiple: float) -> float:
    if multiple and multiple > 0:
        return math.ceil(qty / multiple - 1e-9) * multiple
    return qty


__all__ = ["DayIndex", "Dataset", "spread_week", "build_program_daily", "explode_demand",
           "actual_share", "round_qty", "ceil_to_multiple"]
