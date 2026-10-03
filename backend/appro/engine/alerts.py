"""Alert classification for one article (see docs/regles_metier.md § Alertes)."""
from __future__ import annotations

import datetime as dt

import numpy as np

from .demand import DayIndex
from .models import Alert, AlertType, Article, EngineParams, Lane, Proposal, Severity, SupplierLink
from .projection import Projection, first_shortage


def _severity_by_horizon(days_ahead: int, lead: int, params: EngineParams) -> Severity:
    """Inside the lead time nothing can be done any more; inside the firm horizon the planner
    must act (confirm / order); beyond it the alert is informational."""
    if days_ahead <= lead:
        return Severity.CRITICAL
    if days_ahead <= params.firm_horizon_days:
        return Severity.WARNING
    return Severity.INFO


def classify_alerts(
    article: Article,
    index: DayIndex,
    as_of: dt.date,
    erp: Projection,
    plan: Projection,
    coverage_plan: np.ndarray,
    stock_start: float,
    demand: np.ndarray,
    lanes: list[Lane],
    proposals: list[Proposal],
    links: list[SupplierLink],
    has_bom: bool,
    has_snapshot: bool,
    params: EngineParams,
) -> list[Alert]:
    alerts: list[Alert] = []
    aid = article.article_id
    i0 = index.offset(as_of)
    if i0 is None:
        return alerts
    last = None if params.stockout_lookahead_days is None else i0 + params.stockout_lookahead_days
    lead = min((l.lead_time_days for l in links if l.active), default=10)

    # --- data quality -------------------------------------------------------------
    if not has_snapshot:
        alerts.append(Alert(aid, AlertType.MISSING_DATA, Severity.WARNING,
                            "Aucun stock de départ (snapshot) pour cet article", scope="data"))
    if not links:
        alerts.append(Alert(aid, AlertType.MISSING_DATA, Severity.WARNING,
                            "Aucun fournisseur actif lié à l'article (MOQ / délai inconnus)", scope="data"))
    if not has_bom:
        alerts.append(Alert(aid, AlertType.MISSING_DATA, Severity.INFO,
                            "Article absent des nomenclatures : aucun besoin calculé", scope="data"))

    # --- stock / coverage ----------------------------------------------------------
    if stock_start < -1e-6:
        alerts.append(Alert(aid, AlertType.NEGATIVE_STOCK, Severity.CRITICAL,
                            f"Stock de départ négatif dans l'ERP ({stock_start:,.0f}) : vérifier inventaire / saisies",
                            date=as_of, value=float(stock_start), scope="data"))

    # Stockouts per layer.  A physical stock is never negative: a stockout is the first day
    # with an unserved demand (backlog or lost quantity, see ``shortage_policy``).
    k_plan = first_shortage(plan.shortage, i0, last)
    k_erp = first_shortage(erp.shortage, i0, last)
    if k_plan is not None:
        worst = float(np.max(plan.shortage[k_plan:]))
        alerts.append(Alert(aid, AlertType.STOCKOUT, Severity.CRITICAL,
                            f"Rupture du plan le {index.dates[k_plan].isoformat()} (J+{k_plan - i0}) malgré le plan de "
                            f"livraison et le complément CBN, manque max {worst:,.0f}", date=index.dates[k_plan], value=worst,
                            scope="plan", details={"days_ahead": k_plan - i0, "lead_time_days": lead}))
    if k_erp is not None:
        worst = float(np.max(erp.shortage[k_erp:]))
        alerts.append(Alert(aid, AlertType.STOCKOUT, _severity_by_horizon(k_erp - i0, lead, params),
                            f"Rupture sur les commandes ERP le {index.dates[k_erp].isoformat()} (J+{k_erp - i0}) : "
                            f"livraison à planifier ou commande à passer", date=index.dates[k_erp], value=worst, scope="erp",
                            details={"days_ahead": k_erp - i0, "lead_time_days": lead}))

    # Coverage alerts are based on the *run-out* of the ERP flows (on-hand stock + firm orders):
    # the number of days before the ERP stock cannot serve the demand.  The pure on-hand
    # coverage (``coverage_plan[i0]``) is a KPI but would flag most JIT articles every week.
    cov = int(coverage_plan[i0])
    runout_firm = (k_erp - i0) if k_erp is not None else None
    red = article.param_at("alert_red_days", as_of)
    yellow = article.param_at("alert_yellow_days", as_of)
    overstock = article.param_at("overstock_days", as_of)
    if plan.shortage[i0] <= 1e-9 and demand[i0:].sum() > 0:
        if runout_firm is not None and runout_firm <= red:
            alerts.append(Alert(aid, AlertType.LOW_COVERAGE, Severity.CRITICAL,
                                f"Commandes ERP épuisées dans {runout_firm} j (≤ seuil rouge {red:g} j) ; "
                                f"stock à date : {cov} j de besoin",
                                date=index.dates[k_erp], value=runout_firm, scope="erp"))
        elif runout_firm is not None and runout_firm <= yellow:
            alerts.append(Alert(aid, AlertType.LOW_COVERAGE, Severity.WARNING,
                                f"Commandes ERP épuisées dans {runout_firm} j (≤ seuil orange {yellow:g} j) ; "
                                f"stock à date : {cov} j de besoin",
                                date=index.dates[k_erp], value=runout_firm, scope="erp"))
        elif overstock and cov >= overstock:
            alerts.append(Alert(aid, AlertType.OVERSTOCK, Severity.INFO,
                                f"Surstock : le stock à date couvre {cov} j de besoin (≥ {overstock:g} j)",
                                date=as_of, value=cov))
    if demand[i0:].sum() <= 1e-9 and plan.stock[i0] > 0:
        alerts.append(Alert(aid, AlertType.NO_DEMAND, Severity.INFO,
                            "Aucun besoin sur l'horizon alors que du stock existe (article dormant ?)",
                            date=as_of, value=float(plan.stock[i0])))

    # --- supply --------------------------------------------------------------------
    # The supplier backlog is shown (lane header, article page), not alerted: nothing to qualify.
    urgent = [p for p in proposals if p.urgent]
    if urgent:
        p = urgent[0]
        alerts.append(Alert(aid, AlertType.URGENT_PROPOSAL, Severity.CRITICAL,
                            f"{len(urgent)} proposition(s) hors délai fournisseur – première livraison requise le "
                            f"{p.delivery_date.isoformat()} ({p.qty:,.0f})", date=p.delivery_date, value=p.qty,
                            details={"count": len(urgent)}))
    return alerts


SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}


def worst_severity(alerts: list[Alert]) -> Severity | None:
    if not alerts:
        return None
    return min((a.severity for a in alerts), key=lambda s: SEVERITY_ORDER[s])
