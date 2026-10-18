"""Stock projection, coverage and target stock for one article.

Core recurrence (per day ``d`` after the snapshot day)::

    stock[d] = max(stock[d-1] + supply[d] + adjustments[d] - demand[d], 0)

Two stock scenarios are projected (see ``runner.py``), from the reference stock (ERP snapshot
corrected by the planner adjustments dated on or before it) :

* **ERP scenario** – receipts + firm ERP orders as they are ;
* **plan scenario** – receipts + plan cells (else the firm ERP orders) + CBN proposals.

A physical stock is never negative and the demand that cannot be served is **lost** (production
is not caught up) : ``shortage[d]`` is the quantity that could not be served on day ``d`` ;
``shortage_cum`` is the running sum of those quantities, reset as soon as the stock is positive
again (what the screens show in the stock cells).

Coverage on day ``d`` = number of *future working days* whose cumulated demand is covered by
``stock[d]`` (a day whose cumulated demand equals the stock is covered).  Target stock on day
``d`` = demand of the next ``coverage_target_days`` working days.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np

from .calendar import WorkCalendar
from .demand import DayIndex
from .models import Article


def cumulative_demand(demand: np.ndarray) -> np.ndarray:
    """``cum[i]`` = Σ demand[0..i] (inclusive)."""
    return np.cumsum(demand)


@dataclass
class Projection:
    """Daily series of one stock layer (all aligned on the day index)."""

    net: np.ndarray          # same as ``stock`` (kept for the callers that read the balance)
    stock: np.ndarray        # physical stock, never negative
    shortage: np.ndarray     # demand lost that day
    shortage_cum: np.ndarray # running sum of the lost demand, reset when the stock is positive again

    def __len__(self) -> int:
        return len(self.net)


def shortage_running_sum(stock: np.ndarray, shortage: np.ndarray) -> np.ndarray:
    """Cumulated unserved demand shown in the stock cells : it grows while the stock stays at zero
    and resets as soon as a day ends with a positive stock."""
    out = np.zeros(len(stock))
    acc = 0.0
    for i in range(len(stock)):
        if shortage[i] > 1e-9:
            acc += float(shortage[i])
        elif stock[i] > 1e-9:
            acc = 0.0
        out[i] = acc
    return out


def project_stock(stock_start: float, supply: np.ndarray, adjustments: np.ndarray, demand: np.ndarray,
                  i_snap: int = 0) -> Projection:
    """Vectorised projection from the snapshot day ``i_snap`` (stock known at the end of that day).

    Days up to and including ``i_snap`` keep the snapshot stock.  The lost-demand rule is the
    Skorokhod reflection of the cumulated balance at 0: ``stock = c - min(0, running_min(c))``.
    """
    delta = np.asarray(supply, dtype=float) + np.asarray(adjustments, dtype=float) - np.asarray(demand, dtype=float)
    delta = delta.copy()
    delta[:i_snap + 1] = 0.0
    net = float(stock_start) + np.cumsum(delta)
    floor = np.minimum(0.0, np.minimum.accumulate(net))
    stock = net - floor
    prev = np.concatenate([[float(stock_start)], stock[:-1]])
    shortage = np.maximum(0.0, -(prev + delta))
    return Projection(net=stock.copy(), stock=stock, shortage=shortage, shortage_cum=shortage_running_sum(stock, shortage))


def coverage_days(stock: np.ndarray, demand: np.ndarray, index: DayIndex, calendar: WorkCalendar) -> np.ndarray:
    """Coverage in working days for every day of the window.

    For day ``i`` we look for the largest ``j > i`` with ``Σ demand[i+1..j] <= stock[i]`` and count
    the working days in ``(i, j]``.  When the stock covers the whole remaining window the coverage
    is the number of remaining working days (capped by the horizon)."""
    n = len(stock)
    cum = cumulative_demand(demand)
    targets = cum + np.maximum(stock, 0.0)
    j_max = np.searchsorted(cum, targets, side="right") - 1
    j_max = np.clip(j_max, 0, n - 1)
    idx = np.arange(n)
    j_max = np.maximum(j_max, idx)  # never before i
    open_cum = np.cumsum(calendar.working_mask(index.start, n).astype(int))
    cov = open_cum[j_max] - open_cum[idx]
    cov = np.where(stock < 0, 0, cov)
    return cov.astype(int)


def target_stock(demand: np.ndarray, article: Article, index: DayIndex, calendar: WorkCalendar) -> np.ndarray:
    """Target stock per day : the demand of the next ``coverage_target_days`` working days."""
    n = len(demand)
    cum = np.concatenate([[0.0], np.cumsum(demand)])  # cum[k] = Σ demand[0..k-1]
    # the coverage target may change from one ISO week to another: resolve it once per ISO week
    if article.weekly:
        weeks = index.week_labels
        uniq = {w: max(int(article.param_at("coverage_target_days", d) or 0), 0) for w, d in zip(weeks, index.dates)}
        days = np.fromiter((uniq[w] for w in weeks), dtype=int, count=n)
    else:
        days = np.full(n, max(int(article.coverage_target_days or 0), 0), dtype=int)
    # translate N working days into a calendar span for each day (working-day cumsum)
    mask = calendar.working_mask(index.start, n + 400)
    open_cum = np.concatenate([[0], np.cumsum(mask)])
    idx = np.arange(n)
    wanted = open_cum[idx + 1] + days           # cumulated open days to reach
    end_idx = np.searchsorted(open_cum, wanted, side="left") - 1
    end_idx = np.clip(np.where(days > 0, end_idx, idx), 0, n - 1)
    return cum[end_idx + 1] - cum[np.arange(n) + 1]  # Σ demand[i+1 .. end_idx]


def first_shortage(shortage: np.ndarray, from_idx: int, to_idx: int | None = None) -> int | None:
    """First day index in ``[from_idx, to_idx]`` with an unserved demand (None if none)."""
    stop = len(shortage) if to_idx is None else min(len(shortage), to_idx + 1)
    hit = np.where(shortage[from_idx:stop] > 1e-9)[0]
    return int(hit[0]) + from_idx if len(hit) else None


def date_of(index: DayIndex, i: int) -> dt.date:
    return index.dates[i]
