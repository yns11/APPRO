"""Alert classification for one article (see docs/regles_metier.md § Alertes)."""
from __future__ import annotations

import datetime as dt

import numpy as np

from .calendar import WorkCalendar
from .demand import DayIndex
from .models import (
    FIRM_HORIZON_WD,
    Alert,
    AlertType,
    Article,
    CapShortfall,
    Lane,
    Proposal,
    Severity,
    Supplier,
    SupplierLink,
)
from .projection import Projection, first_shortage


def _fmt(v: float) -> str:
    """Quantity in a message : thousands separated by a space, as in the screens."""
    return f"{v:,.0f}".replace(",", " ")


def _severity_by_horizon(wd_ahead: int, lead: int) -> Severity:
    """Inside the lead time nothing can be done any more; inside the firm horizon (20 working days)
    the planner must act (confirm / order); beyond it the alert is informational.  Same grading for
    the ERP and the plan stockouts : a plan stockout at D+177 is not the emergency of one at D+3."""
    if wd_ahead <= lead:
        return Severity.CRITICAL
    if wd_ahead <= FIRM_HORIZON_WD:
        return Severity.WARNING
    return Severity.INFO


def classify_alerts(
    article: Article,
    index: DayIndex,
    calendar: WorkCalendar,
    as_of: dt.date,
    erp: Projection,
    plan: Projection,
    coverage_plan: np.ndarray,
    stock_start: float,
    demand: np.ndarray,
    lanes: list[Lane],
    proposals: list[Proposal],
    links: list[SupplierLink],
    suppliers: dict[str, Supplier],
    has_bom: bool,
    has_snapshot: bool,
    cap_shortfalls: list[CapShortfall] | None = None,
) -> list[Alert]:
    alerts: list[Alert] = []
    aid = article.article_id
    i0 = index.offset(as_of)
    if i0 is None:
        return alerts
    lead = min((l.lead_time(suppliers) for l in links if l.active), default=5)

    def wd_ahead(k: int) -> int:
        return calendar.working_days_between(as_of, index.dates[k])

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
                            f"Stock de départ négatif dans l'ERP ({_fmt(stock_start)}) : vérifier inventaire / saisies",
                            date=as_of, value=float(stock_start), scope="data"))

    # Stockouts per layer.  A physical stock is never negative: a stockout is the first day with an
    # unserved demand ; the worst value is the cumulated unserved demand (what the cells show).
    k_plan = first_shortage(plan.shortage, i0)
    k_erp = first_shortage(erp.shortage, i0)
    if k_plan is not None:
        worst = float(np.max(plan.shortage_cum[k_plan:]))
        alerts.append(Alert(aid, AlertType.STOCKOUT, _severity_by_horizon(wd_ahead(k_plan), lead),
                            f"Rupture Projeté Appro. le {index.dates[k_plan].isoformat()} (J+{wd_ahead(k_plan)} ouvrés) malgré le plan "
                            f"de livraison et le complément CBN, manque cumulé max {_fmt(worst)}", date=index.dates[k_plan], value=worst,
                            scope="plan", details={"days_ahead": wd_ahead(k_plan), "lead_time_days": lead}))
    if k_erp is not None:
        worst = float(np.max(erp.shortage_cum[k_erp:]))
        alerts.append(Alert(aid, AlertType.STOCKOUT, _severity_by_horizon(wd_ahead(k_erp), lead),
                            f"Rupture sur les commandes ERP le {index.dates[k_erp].isoformat()} (J+{wd_ahead(k_erp)} ouvrés) : "
                            f"livraison à planifier ou commande à passer", date=index.dates[k_erp], value=worst, scope="erp",
                            details={"days_ahead": wd_ahead(k_erp), "lead_time_days": lead}))

    # Coverage alerts are based on the *run-out* of the ERP flows (on-hand stock + firm orders):
    # the number of working days before the ERP stock cannot serve the demand.
    cov = int(coverage_plan[i0])
    runout_firm = wd_ahead(k_erp) if k_erp is not None else None
    red = article.param_at("alert_red_days", as_of)
    yellow = article.param_at("alert_yellow_days", as_of)
    overstock = article.param_at("overstock_days", as_of)
    if plan.shortage[i0] <= 1e-9 and demand[i0:].sum() > 0:
        if runout_firm is not None and runout_firm <= red:
            alerts.append(Alert(aid, AlertType.LOW_COVERAGE, Severity.CRITICAL,
                                f"Commandes ERP épuisées dans {runout_firm} j ouvrés (≤ seuil rouge {red:g} j) ; "
                                f"stock à date : {cov} j de besoin",
                                date=index.dates[k_erp], value=runout_firm, scope="erp"))
        elif runout_firm is not None and runout_firm <= yellow:
            alerts.append(Alert(aid, AlertType.LOW_COVERAGE, Severity.WARNING,
                                f"Commandes ERP épuisées dans {runout_firm} j ouvrés (≤ seuil orange {yellow:g} j) ; "
                                f"stock à date : {cov} j de besoin",
                                date=index.dates[k_erp], value=runout_firm, scope="erp"))
        elif overstock and cov >= overstock:
            alerts.append(Alert(aid, AlertType.OVERSTOCK, Severity.INFO,
                                f"Surstock : le stock à date couvre {cov} j ouvrés de besoin (≥ {overstock:g} j)",
                                date=as_of, value=cov))
    if demand[i0:].sum() <= 1e-9 and plan.stock[i0] > 0:
        alerts.append(Alert(aid, AlertType.NO_DEMAND, Severity.INFO,
                            "Aucun besoin sur l'horizon alors que du stock existe (article dormant ?)",
                            date=as_of, value=float(plan.stock[i0])))

    # --- supply --------------------------------------------------------------------
    urgent = [p for p in proposals if p.urgent]
    if urgent:
        p = urgent[0]
        alerts.append(Alert(aid, AlertType.URGENT_PROPOSAL, Severity.CRITICAL,
                            f"{len(urgent)} proposition(s) hors délai fournisseur – première livraison requise le "
                            f"{p.delivery_date.isoformat()} ({_fmt(p.qty)}, plancher : le minimum qui évite la rupture)",
                            date=p.delivery_date, value=p.qty, details={"count": len(urgent)}))
    if cap_shortfalls:
        c = cap_shortfalls[0]
        alerts.append(Alert(aid, AlertType.CAP_REACHED, Severity.WARNING,
                            f"Plafond fournisseur atteint : {_fmt(c.qty)} non livrables le {c.date.isoformat()} "
                            f"({len(cap_shortfalls)} jour(s)), manque inévitable affiché dans le Projeté Appro.",
                            date=c.date, value=c.qty, details={"count": len(cap_shortfalls), "supplier_id": c.supplier_id}))
    return alerts


SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}


def worst_severity(alerts: list[Alert]) -> Severity | None:
    if not alerts:
        return None
    return min((a.severity for a in alerts), key=lambda s: SEVERITY_ORDER[s])
