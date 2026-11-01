"""ABC / XYZ segmentation of the articles (docs/regles_metier.md § 14).  The supplier service (OTD) and the observed
lead times live in :mod:`appro.services.otd`."""
from __future__ import annotations

import datetime as dt
import statistics
from collections import defaultdict
from typing import Any

from ..engine.calendar import iso_week_monday
from ..engine.models import Dataset, EngineParams

EPS = 1e-6
BUCKETS = ("week", "month", "quarter", "year")


# =============================================================================
# ABC / XYZ segmentation
# =============================================================================
def parse_matrix(spec: str, default: float | None = None) -> dict[str, float]:
    out: dict[str, float] = {}
    for part in (spec or "").replace(";", ",").split(","):
        if ":" not in part:
            continue
        k, v = part.split(":", 1)
        try:
            out[k.strip().upper()] = float(v.strip().replace(",", "."))
        except ValueError:
            continue
    return out


def segmentation(ds: Dataset, params: EngineParams, as_of: dt.date, weeks: int = 26, demand_fallback: dict[str, float] | None = None,
                 current_targets: dict[str, int] | None = None) -> dict[str, Any]:
    """ABC by yearly value (consumption of the last 52 weeks × standard price ; the yearly requirement of the engine when
    no consumption is reported), XYZ by the coefficient of variation of the weekly consumption over the last ``weeks``
    complete ISO weeks (X < ``xyz_x_cv``, Y < ``xyz_y_cv``, Z beyond or without consumption) ; criticality (programmes
    using the article, single source) and the suggested coverage target of the segment."""
    this_monday = iso_week_monday(as_of)
    first_monday = this_monday - dt.timedelta(days=7 * weeks)
    year_ago = as_of - dt.timedelta(days=364)
    weekly: dict[str, dict[dt.date, float]] = defaultdict(lambda: defaultdict(float))
    yearly: dict[str, float] = defaultdict(float)
    for c in ds.consumption:
        if c.date >= as_of:
            continue
        if c.date >= year_ago:
            yearly[c.article_id] += float(c.qty)
        if first_monday <= c.date < this_monday:
            weekly[c.article_id][iso_week_monday(c.date)] += float(c.qty)
    # derived articles : Σ coef × sources
    for a in ds.articles:
        if a.consumption_source == "derived" and a.derived_terms:
            yearly[a.article_id] = sum(coef * yearly.get(src, 0.0) for src, coef in a.derived_terms)
            acc: dict[dt.date, float] = defaultdict(float)
            for src, coef in a.derived_terms:
                for wk, q in weekly.get(src, {}).items():
                    acc[wk] += coef * q
            weekly[a.article_id] = acc
    progs: dict[str, set[str]] = defaultdict(set)
    for b in ds.bom:
        progs[b.article_id].add(b.program_id)
    active_links: dict[str, list] = defaultdict(list)
    for l in ds.links:
        if l.active:
            active_links[l.article_id].append(l)
    suppliers = {s.supplier_id: s for s in ds.suppliers}
    mondays = [first_monday + dt.timedelta(days=7 * k) for k in range(weeks)]
    rows = []
    for a in ds.articles:
        if not a.active:
            continue
        price = ds.prices.get(a.article_id)
        qty_year = yearly.get(a.article_id, 0.0)
        basis = "consommation 52 semaines"
        if qty_year <= EPS and demand_fallback and demand_fallback.get(a.article_id, 0.0) > EPS:
            qty_year, basis = float(demand_fallback[a.article_id]), "besoin de l'horizon (PDP)"
        value = qty_year * price if price is not None else None
        series = [weekly.get(a.article_id, {}).get(m, 0.0) for m in mondays]
        # the series starts at the first week with a consumption : an article launched mid-window is not « irregular »
        first = next((i for i, v in enumerate(series) if v > EPS), None)
        used = series[first:] if first is not None else []
        if len(used) >= 4 and sum(used) > EPS:
            mean = sum(used) / len(used)
            cv = round(statistics.pstdev(used) / mean, 2) if mean > EPS else None
        else:
            cv = None
        xyz = "Z" if cv is None else "X" if cv < float(params.xyz_x_cv) else "Y" if cv < float(params.xyz_y_cv) else "Z"
        links = active_links.get(a.article_id, [])
        lead = max((l.lead_time(suppliers) for l in links), default=None)
        rows.append({"article_id": a.article_id, "designation": a.designation, "unit": a.unit, "planner": a.planner, "family": a.family,
                     "price": price, "qty_year": round(qty_year, 3), "value_year": round(value, 2) if value is not None else None, "value_basis": basis,
                     "cv": cv, "weeks_used": len(used), "xyz": xyz, "programs": len(progs.get(a.article_id, ())), "suppliers": len(links),
                     "single_source": len(links) == 1, "lead_time_days": lead,
                     "coverage_target_days": (current_targets or {}).get(a.article_id, a.coverage_target_days)})
    # ABC on the valued articles
    valued = sorted((r for r in rows if r["value_year"] is not None and r["value_year"] > EPS), key=lambda r: -r["value_year"])
    total = sum(r["value_year"] for r in valued)
    cum = 0.0
    a_pct, b_pct = float(params.abc_a_pct), float(params.abc_b_pct)
    for r in valued:
        cum += r["value_year"]
        share = 100.0 * cum / total if total else 0.0
        r["abc"] = "A" if share <= a_pct + 1e-9 else "B" if share <= b_pct + 1e-9 else "C"
        r["value_share_pct"] = round(100.0 * r["value_year"] / total, 2) if total else 0.0
        r["cum_share_pct"] = round(share, 1)
    for r in rows:
        if "abc" not in r:
            r["abc"] = "–"
            r["value_share_pct"], r["cum_share_pct"] = None, None
        r["segment"] = f"{r['abc']}{r['xyz']}" if r["abc"] != "–" else f"–{r['xyz']}"
        r["criticality"] = ("haute" if r["programs"] >= 3 or (r["single_source"] and r["abc"] == "A") else "moyenne" if r["programs"] >= 1 else "basse")
    coverage = parse_matrix(params.segment_coverage_days)
    service = parse_matrix(params.segment_service_pct)
    for r in rows:
        sug = coverage.get(r["segment"]) if r["abc"] != "–" else None
        r["suggested_coverage_days"] = int(sug) if sug is not None else None
        r["coverage_delta"] = (int(sug) - int(r["coverage_target_days"])) if sug is not None else None
        r["service_target_pct"] = service.get(r["abc"]) if r["abc"] != "–" else None
    rows.sort(key=lambda r: (-(r["value_year"] or -1), r["article_id"]))
    segments: dict[str, dict[str, Any]] = {}
    for r in rows:
        seg = segments.setdefault(r["segment"], {"segment": r["segment"], "abc": r["abc"], "xyz": r["xyz"], "articles": 0, "value_year": 0.0,
                                                 "suggested_coverage_days": r["suggested_coverage_days"], "service_target_pct": r["service_target_pct"],
                                                 "changes": 0})
        seg["articles"] += 1
        seg["value_year"] += r["value_year"] or 0.0
        if r["coverage_delta"]:
            seg["changes"] += 1
    for seg in segments.values():
        seg["value_share_pct"] = round(100.0 * seg["value_year"] / total, 1) if total else None
        seg["value_year"] = round(seg["value_year"], 2)
    return {"as_of": as_of.isoformat(), "weeks": weeks, "from": first_monday.isoformat(), "to": (this_monday - dt.timedelta(days=1)).isoformat(),
            "params": {"abc_a_pct": params.abc_a_pct, "abc_b_pct": params.abc_b_pct, "xyz_x_cv": params.xyz_x_cv, "xyz_y_cv": params.xyz_y_cv,
                       "coverage": coverage, "service": service},
            "total_value_year": round(total, 2), "valued": len(valued), "unvalued": len(rows) - len(valued),
            "segments": sorted(segments.values(), key=lambda s: (s["abc"], s["xyz"])), "rows": rows}
