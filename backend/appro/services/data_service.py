"""Data quality and analysis services of the Paramètres › Données screen : synchronisation status,
orphan articles, plant calendar, adherence of the plan to the actual consumption."""
from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..data.assembler import erp_dataset
from ..data.schemas import FACT_TABLES
from ..data.store import ErpSyncLog, PdpLine, PdpVersion
from ..engine.calendar import WorkCalendar, iso_week_label, iso_week_monday
from ..engine.demand import DayIndex, apply_profile, build_program_daily, explode_demand
from ..engine.models import PdpLine as EnginePdpLine
from . import mrp_service
from .context import AppContext

SYNC_LATE_AFTER = dt.timedelta(hours=2)   # the job runs every hour : two hours without a copy is a failure
ADHERENCE_SHARE = 0.75                    # suggested demand_days : first weekday where the cumulated real share reaches 75 %


# =============================================================================
# Synchronisation
# =============================================================================
def sync_status(ctx: AppContext, session: Session) -> dict[str, Any]:
    """Age of every mirrored fact table and the overall state (ok / late / unknown)."""
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    rows = {r.table_name: r for r in session.scalars(select(ErpSyncLog)).all()}
    tables = []
    mirror = ctx.settings.data_source.lower() == "lakebase"
    for name in FACT_TABLES:
        target = f"erp_{name[4:]}"
        r = rows.get(target) or rows.get(name)
        if r is None:
            tables.append({"table": target, "synced_at": None, "age_minutes": None, "rows": None, "source": "", "status": "unknown"})
            continue
        age = now - r.synced_at
        tables.append({"table": target, "synced_at": r.synced_at.isoformat(), "age_minutes": int(age.total_seconds() // 60),
                       "rows": r.row_count, "source": r.source, "run_id": r.run_id,
                       "status": "late" if age > SYNC_LATE_AFTER else "ok"})
    known = [t for t in tables if t["status"] != "unknown"]
    overall = "unknown" if not known else "late" if any(t["status"] == "late" for t in known) else "ok"
    last = max((t["synced_at"] for t in known), default=None)
    return {"data_source": ctx.source.name, "mirror": mirror, "status": overall, "last_sync_at": last,
            "late_after_minutes": int(SYNC_LATE_AFTER.total_seconds() // 60), "tables": tables}


# =============================================================================
# Orphan articles
# =============================================================================
def orphan_articles(ctx: AppContext) -> list[dict[str, Any]]:
    """Articles present in the ERP facts (orders, receipts, despatch advices, consumption) but absent
    from the reference table : the application ignores them silently."""
    known = set(ctx.table("ref_articles")["article_id"])
    found: dict[str, dict[str, Any]] = {}

    def note(aid: str, kind: str, day, qty: float, supplier: str = "") -> None:
        if not aid or aid in known:
            return
        row = found.setdefault(aid, {"article_id": aid, "orders": 0, "receipts": 0, "desadv": 0, "consumption": 0.0,
                                     "last_date": None, "suppliers": set()})
        if kind == "consumption":
            row["consumption"] += float(qty or 0)
        else:
            row[kind] += 1
        if day is not None and (row["last_date"] is None or str(day) > str(row["last_date"])):
            row["last_date"] = day
        if supplier:
            row["suppliers"].add(str(supplier))

    for r in ctx.table("fct_purchase_orders").to_dict("records"):
        note(r["article_id"], "orders", r["expected_date"], r["qty_ordered"], r["supplier_id"])
    for r in ctx.table("fct_receipts").to_dict("records"):
        note(r["article_id"], "receipts", r["receipt_date"], r["qty"], r["supplier_id"])
    for r in ctx.table("fct_desadv").to_dict("records"):
        note(r["article_id"], "desadv", r["issue_date"], r["qty"], r["supplier_id"])
    for r in ctx.table("fct_consumption_actual").to_dict("records"):
        note(r["article_id"], "consumption", r["date"], r["qty"])
    out = []
    for row in found.values():
        row["suppliers"] = sorted(row["suppliers"])
        row["last_date"] = row["last_date"].isoformat() if isinstance(row["last_date"], dt.date) else row["last_date"]
        out.append(row)
    return sorted(out, key=lambda r: (-(r["orders"] + r["receipts"] + r["desadv"]), r["article_id"]))


# =============================================================================
# Plant calendar
# =============================================================================
def plant_calendar(ctx: AppContext, session: Session, weeks: int = 26) -> dict[str, Any]:
    params = mrp_service.build_params(ctx, session)
    today = params.as_of or dt.date.today()
    closures = sorted(d for d in ctx.settings.holiday_dates if d >= today - dt.timedelta(days=7))
    return {"working_weekdays": list(params.working_weekdays), "holidays": [d.isoformat() for d in closures],
            "next_closures": [d.isoformat() for d in closures if d >= today][:weeks],
            "source": "APPRO_HOLIDAYS / APPRO_WORKING_WEEKDAYS"}


# =============================================================================
# Adherence of the plan to the actual consumption
# =============================================================================
def _pdp_version_at(session: Session, monday: dt.date) -> PdpVersion | None:
    """The latest version imported before the Monday of a week (what was known at the start of the week)."""
    cutoff = dt.datetime.combine(monday, dt.time.min)
    return session.scalars(select(PdpVersion).where(PdpVersion.imported_at < cutoff)
                           .order_by(PdpVersion.imported_at.desc())).first()


def adherence(ctx: AppContext, session: Session, weeks: int = 12, bucket: str = "week", plan_mode: str = "monday",
              planner: str | None = None, program_id: str | None = None, article_id: str | None = None) -> dict[str, Any]:
    """Planned requirement (exploded PDP, profiled) against the reported consumption over the last
    ``weeks`` complete ISO weeks, per article.

    * net adherence = 1 − |Σ real − Σ plan| / Σ plan (volume) and the signed bias (real − plan) / plan ;
    * gross adherence = 1 − Σ per bucket |real − plan| / Σ plan (timing : ``week`` or ``day`` buckets) ;
    * ``plan_mode`` : ``monday`` takes, for each week, the PDP version known at its Monday (the one the
      CBN used), ``latest`` the current effective plan ;
    * suggested ``demand_days`` : the first weekday (Mon = 1 … Fri = 5) where the cumulated real share
      of the week reaches 75 %, 5 when it is Thursday or Friday.
    Articles in ``plan`` consumption source are excluded (their past *is* the plan) ; derived ones are flagged."""
    ds = erp_dataset(ctx.table, planner=planner, article_ids=[article_id] if article_id else None, holidays=ctx.settings.holiday_dates)
    erp_pdp = list(ds.pdp)
    mrp_service.app_entries_into_dataset(ds, session)
    params = mrp_service.build_params(ctx, session)
    as_of = params.as_of or dt.date.today()
    calendar = WorkCalendar.from_spec(params.working_weekdays, ds.holidays)
    this_monday = iso_week_monday(as_of)
    first_monday = this_monday - dt.timedelta(days=7 * max(int(weeks), 1))
    index = DayIndex(first_monday, this_monday - dt.timedelta(days=1))
    if program_id:
        keep = {b.article_id for b in ds.bom if b.program_id == program_id}
        ds.articles = [a for a in ds.articles if a.article_id in keep]
    articles = {a.article_id: a for a in ds.articles if a.consumption_source != "plan"}
    excluded = [a.article_id for a in ds.articles if a.consumption_source == "plan"]
    rates = mrp_service.scrap_rates(session)

    # ---- planned requirement per article and day, according to the plan mode
    def exploded(lines: list[EnginePdpLine]) -> dict[str, np.ndarray]:
        for p in lines:
            p.scrap_pct = mrp_service.scrap_rate_at(rates, p.program_id, p.week_start)
        prog, _ = build_program_daily(lines, calendar, index)
        return explode_demand(prog, ds.bom, index)

    if plan_mode == "latest":
        plan_daily = exploded(list(ds.pdp))
    else:
        plan_daily = defaultdict(lambda: np.zeros(index.n))
        programs = {b.program_id for b in ds.bom}
        for k in range(max(int(weeks), 1)):
            monday = first_monday + dt.timedelta(days=7 * k)
            version = _pdp_version_at(session, monday)
            lines = [p for p in erp_pdp if p.week_start == monday]
            if version is not None:
                rows = session.scalars(select(PdpLine).where(PdpLine.version_id == version.id, PdpLine.week_start == monday)).all()
                covered = {r.program_id for r in rows}
                lines = [p for p in lines if p.program_id not in covered]
                lines += [EnginePdpLine(r.program_id, r.week_start, r.qty, version=f"APP:{version.id}") for r in rows if r.program_id in programs]
            for aid, arr in exploded(lines).items():
                plan_daily[aid] = plan_daily[aid] + arr
    for aid, art in articles.items():
        if aid in plan_daily:
            plan_daily[aid] = apply_profile(plan_daily[aid], art.demand_days, calendar, index)

    # ---- actual consumption per article and day (derived articles : Σ coef × sources)
    actual: dict[str, np.ndarray] = defaultdict(lambda: np.zeros(index.n))
    for c in ds.consumption:
        i = index.offset(c.date)
        if i is not None:
            actual[c.article_id][i] += float(c.qty)
    for aid, art in articles.items():
        if art.consumption_source == "derived" and art.derived_terms:
            acc = np.zeros(index.n)
            for src, coef in art.derived_terms:
                acc += float(coef) * actual.get(src, np.zeros(index.n))
            actual[aid] = acc

    # ---- buckets
    labels = [iso_week_label(d) if bucket == "week" else d.isoformat() for d in index.dates]
    keys = list(dict.fromkeys(labels))
    pos = {k: i for i, k in enumerate(keys)}
    bucket_of = np.array([pos[lab] for lab in labels])
    weekday = np.array([d.isoweekday() for d in index.dates])
    open_mask = calendar.working_mask(index.start, index.n)

    def agg(arr: np.ndarray) -> np.ndarray:
        out = np.zeros(len(keys))
        np.add.at(out, bucket_of, arr)
        return out

    rows = []
    tot_plan = tot_real = tot_gross = 0.0
    for aid, art in sorted(articles.items()):
        plan = plan_daily.get(aid, np.zeros(index.n))
        real = actual.get(aid, np.zeros(index.n))
        p_sum, r_sum = float(plan.sum()), float(real.sum())
        p_b, r_b = agg(plan), agg(real)
        gross_abs = float(np.abs(r_b - p_b).sum())
        # profile of the real consumption by weekday (open days only)
        by_wd = [float(real[(weekday == w) & open_mask].sum()) for w in range(1, 6)]
        share = [x / r_sum for x in by_wd] if r_sum > 0 else [0.0] * 5
        cum = np.cumsum(share)
        suggested = next((w for w in range(1, 6) if cum[w - 1] >= ADHERENCE_SHARE), 5) if r_sum > 0 else None
        if suggested in (4, 5):
            suggested = 5
        row = {"article_id": aid, "designation": art.designation, "unit": art.unit, "planner": art.planner,
               "consumption_source": art.consumption_source, "demand_days": art.demand_days,
               "plan": round(p_sum, 3), "real": round(r_sum, 3),
               "bias_pct": round(100.0 * (r_sum - p_sum) / p_sum, 1) if p_sum > 0 else None,
               "net_pct": round(100.0 * (1.0 - abs(r_sum - p_sum) / p_sum), 1) if p_sum > 0 else None,
               "gross_pct": round(100.0 * (1.0 - gross_abs / p_sum), 1) if p_sum > 0 else None,
               "weekday_share": [round(100.0 * x, 1) for x in share], "suggested_demand_days": suggested,
               "status": "plan sans conso" if p_sum > 0 and r_sum <= 0 else "conso sans plan" if r_sum > 0 and p_sum <= 0 else
                         "sans donnée" if p_sum <= 0 and r_sum <= 0 else "ok"}
        rows.append(row)
        if p_sum > 0:
            tot_plan += p_sum
            tot_real += r_sum
            tot_gross += gross_abs
    totals = {"articles": len(rows), "plan": round(tot_plan, 3), "real": round(tot_real, 3),
              "bias_pct": round(100.0 * (tot_real - tot_plan) / tot_plan, 1) if tot_plan > 0 else None,
              "net_pct": round(100.0 * (1.0 - abs(tot_real - tot_plan) / tot_plan), 1) if tot_plan > 0 else None,
              "gross_pct": round(100.0 * (1.0 - tot_gross / tot_plan), 1) if tot_plan > 0 else None,
              "plan_without_consumption": sum(1 for r in rows if r["status"] == "plan sans conso"),
              "suggestion_changes": sum(1 for r in rows if r["suggested_demand_days"] and r["suggested_demand_days"] != r["demand_days"])}
    return {"as_of": as_of.isoformat(), "from": first_monday.isoformat(), "to": index.end.isoformat(), "weeks": int(weeks),
            "bucket": bucket, "plan_mode": plan_mode, "buckets": keys, "totals": totals, "rows": rows, "excluded_plan_source": excluded}
