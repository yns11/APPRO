"""Alert classification for one article (see docs/regles_metier.md § Alertes)."""
from __future__ import annotations

import datetime as dt

import numpy as np

from .demand import DayIndex
from .models import Alert, AlertType, Article, EngineParams, OrderLine, Proposal, Severity, SupplierLink
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
    firm: Projection,
    forecast: Projection,
    sim: Projection,
    coverage_sim: np.ndarray,
    stock_start: float,
    demand: np.ndarray,
    open_orders: list[OrderLine],
    late_orders: list[OrderLine],
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
    k_sim = first_shortage(sim.shortage, i0, last)
    k_fc = first_shortage(forecast.shortage, i0, last)
    k_firm = first_shortage(firm.shortage, i0, last)
    if k_sim is not None:
        worst = float(np.max(sim.shortage[k_sim:]))
        alerts.append(Alert(aid, AlertType.STOCKOUT, Severity.CRITICAL,
                            f"Rupture simulée le {index.dates[k_sim].isoformat()} (J+{k_sim - i0}) malgré les saisies "
                            f"et propositions, manque max {worst:,.0f}", date=index.dates[k_sim], value=worst,
                            scope="simulated", details={"days_ahead": k_sim - i0, "lead_time_days": lead}))
    if k_fc is not None and k_fc != k_firm:
        worst = float(np.max(forecast.shortage[k_fc:]))
        alerts.append(Alert(aid, AlertType.STOCKOUT, _severity_by_horizon(k_fc - i0, lead, params),
                            f"Rupture sur flux ERP (fermes + prévisionnels) le {index.dates[k_fc].isoformat()} "
                            f"(J+{k_fc - i0}) : commande à passer / proposition à valider", date=index.dates[k_fc],
                            value=worst, scope="forecast", details={"days_ahead": k_fc - i0, "lead_time_days": lead}))
    if k_firm is not None:
        worst = float(np.max(firm.shortage[k_firm:]))
        if k_fc is not None and k_fc > k_firm:
            hint = (f"commandes prévisionnelles à confirmer (elles couvrent jusqu'au "
                    f"{index.dates[k_fc - 1].isoformat()})")
        else:
            hint = "aucune commande prévisionnelle ne couvre cette date : commande à passer"
        alerts.append(Alert(aid, AlertType.STOCKOUT, _severity_by_horizon(k_firm - i0, lead, params),
                            f"Rupture sur flux fermes le {index.dates[k_firm].isoformat()} (J+{k_firm - i0}) : {hint}",
                            date=index.dates[k_firm], value=worst, scope="firm",
                            details={"days_ahead": k_firm - i0, "lead_time_days": lead}))

    # Coverage alerts are based on the *run-out* of the firm flows (on-hand stock + committed
    # supply): the number of days before the firm stock cannot serve the demand.  The pure
    # on-hand coverage (``coverage_sim[i0]``) is a KPI but would flag most JIT articles every week.
    cov = int(coverage_sim[i0])
    runout_firm = (k_firm - i0) if k_firm is not None else None
    if sim.shortage[i0] <= 1e-9 and demand[i0:].sum() > 0:
        if runout_firm is not None and runout_firm <= article.alert_red_days:
            alerts.append(Alert(aid, AlertType.LOW_COVERAGE, Severity.CRITICAL,
                                f"Flux fermes épuisés dans {runout_firm} j (≤ seuil rouge {article.alert_red_days} j) ; "
                                f"stock à date : {cov} j de besoin",
                                date=index.dates[k_firm], value=runout_firm, scope="firm"))
        elif runout_firm is not None and runout_firm <= article.alert_yellow_days:
            alerts.append(Alert(aid, AlertType.LOW_COVERAGE, Severity.WARNING,
                                f"Flux fermes épuisés dans {runout_firm} j (≤ seuil orange {article.alert_yellow_days} j) ; "
                                f"stock à date : {cov} j de besoin",
                                date=index.dates[k_firm], value=runout_firm, scope="firm"))
        elif article.overstock_days and cov >= article.overstock_days:
            alerts.append(Alert(aid, AlertType.OVERSTOCK, Severity.INFO,
                                f"Surstock : le stock à date couvre {cov} j de besoin (≥ {article.overstock_days} j)",
                                date=as_of, value=cov))
    if demand[i0:].sum() <= 1e-9 and sim.stock[i0] > 0:
        alerts.append(Alert(aid, AlertType.NO_DEMAND, Severity.INFO,
                            "Aucun besoin sur l'horizon alors que du stock existe (article dormant ?)",
                            date=as_of, value=float(sim.stock[i0])))

    # --- supply --------------------------------------------------------------------
    for o in late_orders:
        days = (as_of - o.expected_date).days
        alerts.append(Alert(aid, AlertType.LATE_ORDER, Severity.WARNING,
                            f"Commande {o.order_id} attendue le {o.expected_date.isoformat()} ({days} j de retard), "
                            f"reste {o.qty_open:,.0f}", date=o.expected_date, value=o.qty_open,
                            details={"order_id": o.order_id, "supplier_id": o.supplier_id, "days_late": days}))
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
