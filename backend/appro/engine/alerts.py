"""Alert classification for one article (docs/regles_metier.md § 6).

Three statuses only, read on the article data and the CBN, never on a common horizon :

* **critical** : the stock « à date » (end of yesterday, no inflow counted) covers at most the red
  threshold + 1 working day ; or a CBN proposal whose order deadline (« au plus tard le », margin
  included) is already past ; or a negative reference stock ;
* **to order** : a CBN proposal whose order deadline falls within the next five working days ; or the
  stock « à date » covers at most the orange threshold and no firm order nor typed plan cell lands
  before it runs out ;
* **ok** otherwise.

Everything else is information : the plan stockout that the CBN could not avoid (caps, refusals), the
overstock, the dormant article, the data issues (no reference stock, no active link, firm orders of a
supplier without an active link).  In multi-sourcing the proposals already carry the supplier retained
for each share, with its own lead time : the status follows the earliest order date."""
from __future__ import annotations

import datetime as dt
from typing import Any

import numpy as np

from .calendar import WorkCalendar
from .demand import DayIndex
from .models import (
    Alert,
    AlertType,
    Article,
    CapShortfall,
    Proposal,
    Severity,
    SupplierLink,
)
from .projection import Projection, first_shortage


def _fmt(v: float) -> str:
    """Quantity in a message : thousands separated by a space, as in the screens."""
    return f"{v:,.0f}".replace(",", " ")


ORDER_DUE_WD = 5      # « à commander » : order date within the next five working days


def classify_alerts(
    article: Article,
    index: DayIndex,
    calendar: WorkCalendar,
    as_of: dt.date,
    plan: Projection,
    stock_start: float,
    stock_at_date: float,
    coverage_at_date: int,
    runout: int | None,
    inflow_before_runout: bool,
    demand: np.ndarray,
    proposals: list[Proposal],
    links: list[SupplierLink],
    supplier_names: dict[str, str],
    has_bom: bool,
    has_snapshot: bool,
    cap_shortfalls: list[CapShortfall] | None = None,
    unlinked: list[Any] | None = None,
) -> list[Alert]:
    alerts: list[Alert] = []
    aid = article.article_id
    i0 = index.offset(as_of)
    if i0 is None:
        return alerts

    def wd_ahead(k: int) -> int:
        return calendar.working_days_between(as_of, index.dates[k])

    def who(sid: str | None) -> str:
        return f"{sid} {supplier_names.get(sid or '', '')}".strip() if sid else "sans fournisseur"

    # --- data quality (information : never drives the status) -----------------------
    if not has_snapshot:
        alerts.append(Alert(aid, AlertType.MISSING_DATA, Severity.INFO,
                            "Aucun stock de départ (snapshot) pour cet article", scope="data"))
    if not links:
        alerts.append(Alert(aid, AlertType.MISSING_DATA, Severity.INFO,
                            "Aucun fournisseur actif lié à l'article (MOQ / délai inconnus)", scope="data"))
    if not has_bom:
        alerts.append(Alert(aid, AlertType.MISSING_DATA, Severity.INFO,
                            "Article absent des nomenclatures : aucun besoin calculé", scope="data"))
    coming = [u for u in (unlinked or ()) if u.orders_open]
    if coming:
        parts = [f"{who(u.supplier_id)} : {u.orders_open} commande(s) ferme(s) à venir ({_fmt(u.open_qty)})" for u in coming]
        alerts.append(Alert(aid, AlertType.UNLINKED_FLOWS, Severity.INFO,
                            "Commandes fermes à venir chez un fournisseur sans lien actif, hors tableau (visibles dans Flux et sur "
                            "la fiche fournisseur) : créer le lien ou solder la commande – " + " ; ".join(parts),
                            scope="data", details={"suppliers": [u.supplier_id for u in coming]}))

    # --- stock « à date » ----------------------------------------------------------------
    if stock_start < -1e-6:
        alerts.append(Alert(aid, AlertType.NEGATIVE_STOCK, Severity.CRITICAL,
                            f"Stock de départ négatif dans l'ERP ({_fmt(stock_start)}) : vérifier inventaire / saisies",
                            date=as_of, value=float(stock_start), scope="data"))
    red = article.param_at("alert_red_days", as_of)
    yellow = article.param_at("alert_yellow_days", as_of)
    overstock = article.param_at("overstock_days", as_of)
    runout_date = index.dates[runout] if runout is not None else None
    if demand[i0:].sum() > 1e-9:
        if coverage_at_date <= red + 1:
            alerts.append(Alert(aid, AlertType.LOW_COVERAGE, Severity.CRITICAL,
                                f"Le stock à date ({_fmt(stock_at_date)}) couvre {coverage_at_date} j ouvrés de besoin, seuil rouge "
                                f"{red:g} j + 1" + (f" ; épuisé le {runout_date.isoformat()}" if runout_date else ""),
                                date=runout_date or as_of, value=coverage_at_date, scope="erp",
                                details={"coverage_days": coverage_at_date, "red_days": red}))
        elif coverage_at_date <= yellow and not inflow_before_runout:
            alerts.append(Alert(aid, AlertType.LOW_COVERAGE, Severity.WARNING,
                                f"Le stock à date couvre {coverage_at_date} j ouvrés de besoin (seuil orange {yellow:g} j) et aucune "
                                f"livraison ferme ni Appro. saisie n'arrive avant"
                                + (f" le {runout_date.isoformat()}" if runout_date else " la fin de la fenêtre"),
                                date=runout_date or as_of, value=coverage_at_date, scope="erp",
                                details={"coverage_days": coverage_at_date, "yellow_days": yellow}))
        elif overstock and coverage_at_date >= overstock:
            alerts.append(Alert(aid, AlertType.OVERSTOCK, Severity.INFO,
                                f"Surstock : le stock à date couvre {coverage_at_date} j ouvrés de besoin (≥ {overstock:g} j)",
                                date=as_of, value=coverage_at_date))
    elif plan.stock[i0] > 0:
        alerts.append(Alert(aid, AlertType.NO_DEMAND, Severity.INFO,
                            "Aucun besoin sur l'horizon alors que du stock existe (article dormant ?)",
                            date=as_of, value=float(plan.stock[i0])))

    # --- orders to place (the CBN already chose the supplier of each share) ---------------
    ordered = sorted(proposals, key=lambda p: (p.order_date, p.delivery_date, p.supplier_id or ""))
    late = [p for p in ordered if p.urgent or p.order_date < as_of]
    if late:
        p = late[0]
        alerts.append(Alert(aid, AlertType.URGENT_PROPOSAL, Severity.CRITICAL,
                            f"{len(late)} proposition(s) dont la date limite de commande est passée – {who(p.supplier_id)}, livraison "
                            f"requise le {p.delivery_date.isoformat()} ({_fmt(p.qty)}"
                            + (", plancher : le minimum qui évite la rupture" if p.floor else "") + (", hors jour standard ◇" if p.off_standard else "") + ")",
                            date=p.delivery_date, value=p.qty, details={"count": len(late), "supplier_id": p.supplier_id}))
    due_limit = calendar.add_working_days(as_of, ORDER_DUE_WD)
    due = [p for p in ordered if as_of <= p.order_date <= due_limit and not (p.urgent or p.order_date < as_of)]
    if due:
        p = due[0]
        alerts.append(Alert(aid, AlertType.ORDER_DUE, Severity.WARNING,
                            f"Commande à placer au plus tard le {p.order_date.isoformat()} chez {who(p.supplier_id)} : {_fmt(p.qty)} "
                            f"à livrer le {p.delivery_date.isoformat()}" + (" (hors jour standard ◇)" if p.off_standard else "")
                            + (f" ({len(due)} propositions sous {ORDER_DUE_WD} j ouvrés)" if len(due) > 1 else ""),
                            date=p.order_date, value=p.qty, details={"count": len(due), "supplier_id": p.supplier_id}))

    # --- information on the plan ---------------------------------------------------------
    k_plan = first_shortage(plan.shortage, i0)
    if k_plan is not None:
        worst = float(np.max(plan.shortage_cum[k_plan:]))
        alerts.append(Alert(aid, AlertType.STOCKOUT, Severity.INFO,
                            f"Rupture du stock projeté le {index.dates[k_plan].isoformat()} (J+{wd_ahead(k_plan)} ouvrés) malgré le plan "
                            f"de livraison et le complément CBN, manque cumulé max {_fmt(worst)}", date=index.dates[k_plan], value=worst,
                            scope="plan", details={"days_ahead": wd_ahead(k_plan)}))
    if cap_shortfalls:
        c = cap_shortfalls[0]
        alerts.append(Alert(aid, AlertType.CAP_REACHED, Severity.INFO,
                            f"Plafond fournisseur atteint : {_fmt(c.qty)} non livrables le {c.date.isoformat()} "
                            f"({len(cap_shortfalls)} jour(s)), manque inévitable affiché dans le stock projeté",
                            date=c.date, value=c.qty, details={"count": len(cap_shortfalls), "supplier_id": c.supplier_id}))
    return alerts


SEVERITY_ORDER = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}


def worst_severity(alerts: list[Alert]) -> Severity | None:
    if not alerts:
        return None
    return min((a.severity for a in alerts), key=lambda s: SEVERITY_ORDER[s])


def status_of(alerts: list[Alert]) -> str:
    """``critical`` / ``to_order`` / ``ok`` : the information alerts never change it."""
    worst = worst_severity(alerts)
    return "critical" if worst == Severity.CRITICAL else "to_order" if worst == Severity.WARNING else "ok"
