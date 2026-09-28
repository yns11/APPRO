"""Impact of the component stocks on the production programmes.

For every programme and day, the *feasible* production is the planned production scaled by the
share of its components that can be served on that day, in a given stock layer:

    share_c(d)   = 1 - unserved_c(d) / demand_c(d)        (1 when the component has no demand)
    share_p(d)   = min over the components c of p of share_c(d)
    feasible(d)  = planned(d) × share_p(d)

``unserved_c(d)`` is the demand of day ``d`` that the layer cannot serve: the daily lost quantity
(``lost`` policy) or the increase of the backlog on that day (``backlog`` policy).  Three layers are
evaluated: on-hand stock only (nothing else arrives), ERP and plan.  Results are
aggregated per ISO week from the reference date, with the limiting components of each week.
"""
from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np

from .calendar import iso_week_label
from .demand import DayIndex
from .models import ArticleResult, BomLine

LAYERS = ("onhand", "erp", "plan")


def _unserved(shortage: np.ndarray, policy: str) -> np.ndarray:
    if policy == "lost":
        return shortage
    prev = np.concatenate([[0.0], shortage[:-1]])
    return np.maximum(0.0, shortage - prev)


def program_impact(results: dict[str, ArticleResult], bom: list[BomLine], program_daily: dict[str, np.ndarray],
                   program_names: dict[str, str], index: DayIndex, as_of: dt.date, policy: str) -> dict[str, Any]:
    i0 = index.offset(as_of)
    if i0 is None or not program_daily:
        return {"weeks": [], "programs": []}
    # component shares per layer, computed once per article
    shares: dict[str, dict[str, np.ndarray]] = {}
    for aid, ar in results.items():
        demand = np.asarray(ar.demand)
        with np.errstate(divide="ignore", invalid="ignore"):
            shares[aid] = {}
            for layer, short in (("onhand", ar.shortage_onhand), ("erp", ar.shortage_erp), ("plan", ar.shortage_plan)):
                un = _unserved(np.asarray(short), policy)
                sh = np.where(demand > 1e-9, 1.0 - un / np.where(demand > 1e-9, demand, 1.0), 1.0)
                shares[aid][layer] = np.clip(sh, 0.0, 1.0)
    comps: dict[str, list[str]] = {}
    for b in bom:
        if b.article_id in results:
            comps.setdefault(b.program_id, []).append(b.article_id)
    # weekly buckets from the reference date
    weeks: list[str] = []
    bucket: list[list[int]] = []
    for i in range(i0, index.n):
        wk = iso_week_label(index.dates[i])
        if not weeks or weeks[-1] != wk:
            weeks.append(wk)
            bucket.append([])
        bucket[-1].append(i)
    programs = []
    for pid, daily in sorted(program_daily.items()):
        planned = np.asarray(daily)
        if planned[i0:].sum() <= 1e-9:
            continue
        articles = comps.get(pid, [])
        row: dict[str, Any] = {"program_id": pid, "name": program_names.get(pid, pid), "components": len(articles),
                               "planned": [], "feasible": {L: [] for L in LAYERS}, "limiting": {L: [] for L in LAYERS},
                               "first_impact": {L: None for L in LAYERS}}
        for wk, idxs in zip(weeks, bucket):
            row["planned"].append(round(float(planned[idxs].sum()), 3))
            for L in LAYERS:
                if articles:
                    mat = np.array([shares[a][L][idxs] for a in articles])   # components × days
                    share = mat.min(axis=0)
                    # limiting components of the week: lowest weighted share, ties on quantity
                    weekly = (mat * planned[idxs]).sum(axis=1) / max(planned[idxs].sum(), 1e-9)
                    lim = sorted(((float(weekly[k]), a) for k, a in enumerate(articles) if weekly[k] < 0.999))[:3]
                else:
                    share, lim = np.ones(len(idxs)), []
                feasible = float((planned[idxs] * share).sum())
                row["feasible"][L].append(round(feasible, 3))
                row["limiting"][L].append([{"article_id": a, "share": round(s, 3)} for s, a in lim])
                if row["first_impact"][L] is None and feasible < planned[idxs].sum() - 1e-6:
                    row["first_impact"][L] = wk
        programs.append(row)
    return {"weeks": weeks, "programs": programs}
