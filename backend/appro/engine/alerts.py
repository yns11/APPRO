"""Alert classification for one article (see docs/regles_metier.md § Alertes)."""
from __future__ import annotations

import datetime as dt

import numpy as np

from .demand import DayIndex
from .models import Alert, AlertType, Article, EngineParams, OrderLine, Proposal, Severity, SupplierLink
from .projection import first_negative


def classify_alerts(
    article: Article,
    index: DayIndex,
    as_of: dt.date,
    stock_firm: np.ndarray,
    stock_sim: np.ndarray,
    coverage_firm: np.ndarray,
    coverage_sim: np.ndarray,
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
    if stock_sim[i0] < -1e-6:
        alerts.append(Alert(aid, AlertType.NEGATIVE_STOCK, Severity.CRITICAL,
                            f"Stock négatif à date ({stock_sim[i0]:,.0f}) : vérifier inventaire / saisies",
                            date=as_of, value=float(stock_sim[i0])))

    for scope, stock in (("simulated", stock_sim), ("firm", stock_firm)):
        k = first_negative(stock, i0, last)
        if k is not None:
            days_ahead = k - i0
            worst = float(np.min(stock[k:]))
            if scope == "simulated":
                sev = Severity.CRITICAL
                msg = f"Rupture projetée le {index.dates[k].isoformat()} (J+{days_ahead}), manque max {abs(worst):,.0f}"
            else:
                # inside the lead time nothing can be done any more; inside the firm horizon the
                # planner must confirm forecast / planned orders; beyond it is informational.
                if days_ahead <= lead:
                    sev = Severity.CRITICAL
                elif days_ahead <= params.firm_horizon_days:
                    sev = Severity.WARNING
                else:
                    sev = Severity.INFO
                msg = (f"Rupture sur flux fermes le {index.dates[k].isoformat()} (J+{days_ahead}) : "
                       f"commandes prévisionnelles / planifiées à confirmer")
            alerts.append(Alert(aid, AlertType.STOCKOUT, sev, msg, date=index.dates[k], value=worst,
                                scope=scope, details={"days_ahead": days_ahead, "lead_time_days": lead}))

    # Coverage alerts are based on the *run-out* of the firm flows (on-hand stock + committed
    # supply): the number of days before the firm stock becomes negative.  The pure on-hand
    # coverage (``coverage_sim[i0]``) is a KPI but would flag most JIT articles every week.
    cov = int(coverage_sim[i0])
    k_firm = first_negative(stock_firm, i0, last)
    runout_firm = (k_firm - i0) if k_firm is not None else None
    if stock_sim[i0] >= -1e-6 and demand[i0:].sum() > 0:
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
    if demand[i0:].sum() <= 1e-9 and stock_sim[i0] > 0:
        alerts.append(Alert(aid, AlertType.NO_DEMAND, Severity.INFO,
                            "Aucun besoin sur l'horizon alors que du stock existe (article dormant ?)",
                            date=as_of, value=float(stock_sim[i0])))

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
