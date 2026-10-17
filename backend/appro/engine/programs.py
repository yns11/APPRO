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
    starts = np.array([b[0] - i0 for b in bucket])      # week boundaries, relative to the reference day
    programs = []
    for pid, daily in sorted(program_daily.items()):
        planned = np.asarray(daily)[i0:]
        if planned.sum() <= 1e-9:
            continue
        articles = comps.get(pid, [])
        planned_w = np.add.reduceat(planned, starts)
        row: dict[str, Any] = {"program_id": pid, "name": program_names.get(pid, pid), "components": len(articles),
                               "planned": np.round(planned_w, 3).tolist(), "feasible": {}, "limiting": {}, "first_impact": {}}
        for L in LAYERS:
            if articles:
                mat = np.stack([shares[a][L][i0:] for a in articles])          # components × days
                share = mat.min(axis=0)
                feasible_w = np.add.reduceat(planned * share, starts)
                # limiting components of the week: lowest weighted share, ties on quantity
                weighted = np.add.reduceat(mat * planned, starts, axis=1) / np.maximum(planned_w, 1e-9)
                limiting = []
                for w in range(len(weeks)):
                    ks = np.where(weighted[:, w] < 0.999)[0]
                    lim = sorted(((float(weighted[k, w]), articles[k]) for k in ks))[:3]
                    limiting.append([{"article_id": a, "share": round(s, 3)} for s, a in lim])
            else:
                feasible_w, limiting = planned_w.copy(), [[] for _ in weeks]
            row["feasible"][L] = np.round(feasible_w, 3).tolist()
            row["limiting"][L] = limiting
            hit = np.where(feasible_w < planned_w - 1e-6)[0]
            row["first_impact"][L] = weeks[int(hit[0])] if len(hit) else None
        programs.append(row)
    return {"weeks": weeks, "programs": programs}
