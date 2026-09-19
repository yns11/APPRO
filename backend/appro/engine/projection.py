"""Stock projection, coverage and target stock for one article.

Core recurrence (per day ``d`` after the snapshot day)::

    stock[d] = stock[d-1] + supply[d] + adjustments[d] - demand[d]

Two stocks are projected:

* **firm stock** – only *committed* supply (ERP firm orders / schedule lines, app orders that
  were sent to the supplier, receipts posted after the snapshot);
* **simulated stock** – firm supply + forecast schedule lines + planned (not yet sent) orders
  + accepted proposals + engine proposals (when enabled).

Coverage on day ``d`` = number of *future* days whose cumulated demand is covered by
``stock[d]`` (calendar or working days).  Target stock on day ``d`` = demand of the next
``coverage_target_days`` days (+ optional fixed safety stock).
"""
from __future__ import annotations

import datetime as dt

import numpy as np

from .calendar import WorkCalendar
from .demand import DayIndex
from .models import Article, EngineParams


def cumulative_demand(demand: np.ndarray) -> np.ndarray:
    """``cum[i]`` = Σ demand[0..i] (inclusive)."""
    return np.cumsum(demand)


def project_stock(stock_start: float, supply: np.ndarray, adjustments: np.ndarray,
                  demand: np.ndarray) -> np.ndarray:
    """Vectorised projection; index 0 is the snapshot day (stock known at end of day)."""
    n = len(demand)
    stock = np.empty(n)
    stock[0] = stock_start
    if n > 1:
        delta = supply[1:] + adjustments[1:] - demand[1:]
        stock[1:] = stock_start + np.cumsum(delta)
    return stock


def coverage_days(stock: np.ndarray, demand: np.ndarray, index: DayIndex, calendar: WorkCalendar,
                  unit: str = "calendar", tie_rule: str = "covered") -> np.ndarray:
    """Coverage in days for every day of the window.

    For day ``i`` we look for the largest ``j > i`` with ``Σ demand[i+1..j] <= stock[i]``
    (``<`` when ``tie_rule == "not_covered"``) and count the days in ``(i, j]``
    (all days, or working days only).  Negative stock → 0.  When the stock covers the whole
    remaining window the coverage is the number of remaining days (capped by the horizon).
    """
    n = len(stock)
    cum = cumulative_demand(demand)
    side = "right" if tie_rule == "covered" else "left"
    targets = cum + np.maximum(stock, 0.0)
    # j_max = last index with cum[j] <= cum[i] + stock[i]
    j_max = np.searchsorted(cum, targets, side=side) - 1
    j_max = np.clip(j_max, 0, n - 1)
    idx = np.arange(n)
    j_max = np.maximum(j_max, idx)  # never before i
    if unit == "working":
        is_open = np.array([1 if calendar.is_working_day(d) else 0 for d in index.dates])
        open_cum = np.cumsum(is_open)
        cov = open_cum[j_max] - open_cum[idx]
    else:
        cov = j_max - idx
    cov = np.where(stock < 0, 0, cov)
    return cov.astype(int)


def target_stock(demand: np.ndarray, article: Article, index: DayIndex, calendar: WorkCalendar,
                 params: EngineParams) -> np.ndarray:
    """Target / safety level per day according to the article policy."""
    n = len(demand)
    cum = np.concatenate([[0.0], np.cumsum(demand)])  # cum[k] = Σ demand[0..k-1]
    days = max(int(article.coverage_target_days or 0), 0)
    if params.coverage_unit == "working":
        # translate N working days into a calendar span for each day
        end_idx = np.empty(n, dtype=int)
        for i, d in enumerate(index.dates):
            end_idx[i] = min(n - 1, i + (calendar.add_working_days(d, days) - d).days)
    else:
        end_idx = np.minimum(np.arange(n) + days, n - 1)
    cov_target = cum[end_idx + 1] - cum[np.arange(n) + 1]  # Σ demand[i+1 .. end_idx]
    safety = float(article.safety_stock_qty or 0.0)
    if params.target_policy == "coverage_days":
        return cov_target
    if params.target_policy == "safety_qty":
        return np.full(n, safety)
    return np.maximum(cov_target, safety)


def first_negative(stock: np.ndarray, from_idx: int, to_idx: int | None = None) -> int | None:
    stop = len(stock) if to_idx is None else min(len(stock), to_idx + 1)
    neg = np.where(stock[from_idx:stop] < -1e-9)[0]
    return int(neg[0]) + from_idx if len(neg) else None


def date_of(index: DayIndex, i: int) -> dt.date:
    return index.dates[i]
