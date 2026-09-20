"""Stock projection, coverage and target stock for one article.

Core recurrence (per day ``d`` after the snapshot day)::

    stock[d] = stock[d-1] + supply[d] + adjustments[d] - demand[d]

Three cumulative stock layers are projected (see ``runner.py``):

* **firm stock** – on-hand stock + *committed* supply (ERP firm orders / schedule lines, app
  orders sent to the supplier, receipts and adjustments posted after the snapshot);
* **forecast stock** – firm + ERP forecast schedule lines (DELFOR);
* **simulated stock** – forecast + planned (app) orders + scenario orders + engine proposals.

A physical stock can never be negative.  ``shortage_policy`` decides what happens to the
demand that cannot be served:

* ``backlog`` (default, MRP "projected available balance") – the unserved demand is carried
  forward: the *net* balance goes negative and the next receipts serve the backlog first.
  ``stock = max(net, 0)`` and ``shortage = max(-net, 0)`` (cumulated backlog);
* ``lost`` – the unserved demand is lost (production is not caught up):
  ``stock[d] = max(stock[d-1] + supply[d] + adjustments[d] - demand[d], 0)`` and ``shortage[d]``
  is the quantity that could not be served on day ``d``.

Coverage on day ``d`` = number of *future* days whose cumulated demand is covered by
``stock[d]`` (calendar or working days).  Target stock on day ``d`` = demand of the next
``coverage_target_days`` days (+ optional fixed safety stock).
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

from .calendar import WorkCalendar
from .demand import DayIndex
from .models import Article, EngineParams


def cumulative_demand(demand: np.ndarray) -> np.ndarray:
    """``cum[i]`` = Σ demand[0..i] (inclusive)."""
    return np.cumsum(demand)


@dataclass
class Projection:
    """Daily series of one stock layer (all aligned on the day index)."""

    net: np.ndarray        # projected available balance (< 0 = backlog, ``backlog`` policy only)
    stock: np.ndarray      # physical stock, never negative
    shortage: np.ndarray   # backlog policy: cumulated backlog ; lost policy: demand lost that day

    def __len__(self) -> int:
        return len(self.net)


def project_stock(stock_start: float, supply: np.ndarray, adjustments: np.ndarray, demand: np.ndarray,
                  policy: str = "backlog", i_snap: int = 0) -> Projection:
    """Vectorised projection from the snapshot day ``i_snap`` (stock known at the end of that day).

    Days up to and including ``i_snap`` keep the snapshot stock.  The ``lost`` policy is the
    Skorokhod reflection of the cumulated balance at 0: ``stock = c - min(0, running_min(c))``.
    """
    delta = np.asarray(supply, dtype=float) + np.asarray(adjustments, dtype=float) - np.asarray(demand, dtype=float)
    delta = delta.copy()
    delta[:i_snap + 1] = 0.0
    net = float(stock_start) + np.cumsum(delta)
    if policy == "lost":
        floor = np.minimum(0.0, np.minimum.accumulate(net))
        stock = net - floor
        prev = np.concatenate([[float(stock_start)], stock[:-1]])
        shortage = np.maximum(0.0, -(prev + delta))
        return Projection(net=stock.copy(), stock=stock, shortage=shortage)
    return Projection(net=net, stock=np.maximum(net, 0.0), shortage=np.maximum(-net, 0.0))


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


def first_shortage(shortage: np.ndarray, from_idx: int, to_idx: int | None = None) -> int | None:
    """First day index in ``[from_idx, to_idx]`` with an unserved demand (None if none)."""
    stop = len(shortage) if to_idx is None else min(len(shortage), to_idx + 1)
    hit = np.where(shortage[from_idx:stop] > 1e-9)[0]
    return int(hit[0]) + from_idx if len(hit) else None


def date_of(index: DayIndex, i: int) -> dt.date:
    return index.dates[i]
