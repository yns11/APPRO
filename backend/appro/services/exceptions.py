"""The **exception queue** : everything that needs a decision today, one line per exception, ranked by a
transparent score and carrying its owner, its deadline and the action to take (docs/regles_metier.md § 9 quater).

Only *actionable* situations enter the queue (an information alert without any action stays in the alerts of the
article) :

* ``order_late`` – a CBN proposal whose order deadline is past ;
* ``order_due`` – a proposal whose deadline falls within the next five working days ;
* ``stockout`` – a projected shortage the CBN could not avoid (supplier caps, refusals) ;
* ``backlog`` – a firm order whose delivery date is past with an ERP remaining quantity ;
* ``desadv_error`` – a despatch advice in error, or processed without any entry journal ;
* ``acr_pending`` – a receipt whose acknowledgement waits for a week or more ;
* ``stock_drift`` – the ERP stock drifts away from the projected stock ;
* ``negative_stock`` – a negative reference stock ;
* ``unlinked_orders`` – firm orders at a supplier without an active link ;
* ``no_supplier`` – an article with a requirement and no active supplier link.

Score (0 – 100) = **urgency** (0 – 50 : overdue 50, today 45, two working days 35, five 25, later 10) + **production at
risk** (0 – 30 : a projected shortage inside the lead time 30, within four weeks 20, a backlog 15, none 0) +
**criticality** (0 – 20 : class A 20, B 12, C 5, unknown 8, raised to 20 for a single-source article used by three
programmes or more).  The ``why`` of every line names the terms."""
from __future__ import annotations

import datetime as dt
from typing import Any

from ..api.presenters import ACR_PENDING_DAYS
from ..engine.alerts import ORDER_DUE_WD
from ..engine.calendar import WorkCalendar
from ..engine.models import Dataset, MrpResult

KINDS: dict[str, str] = {
    "order_late": "Commande en retard de passation", "order_due": "Commande à passer", "stockout": "Rupture projetée non couverte",
    "backlog": "Livraison fournisseur en retard", "desadv_error": "DESADV à corriger", "acr_pending": "Accusé de réception à valider",
    "stock_drift": "Dérive du stock ERP", "negative_stock": "Stock de référence négatif", "unlinked_orders": "Commandes hors lien actif",
    "no_supplier": "Article sans fournisseur actif",
}


def _q(v: float | None) -> str:
    """Quantity in a sentence : thousands separated by a space."""
    return "–" if v is None else f"{float(v):,.0f}".replace(",", " ")


def _urgency(days: int | None) -> tuple[int, str]:
    if days is None:
        return 10, "sans échéance"
    if days < 0:
        return 50, f"échéance dépassée de {-days} j"
    if days == 0:
        return 45, "échéance aujourd'hui"
    if days <= 2:
        return 35, f"échéance dans {days} j"
    if days <= 5:
        return 25, f"échéance dans {days} j"
    return 10, f"échéance dans {days} j"


def _criticality(abc: str | None, programs: int, single_source: bool) -> tuple[int, str]:
    base = {"A": 20, "B": 12, "C": 5}.get(abc or "", 8)
    label = f"classe {abc}" if abc in ("A", "B", "C") else "classe inconnue"
    if single_source and programs >= 3:
        return 20, f"{label}, mono-source, {programs} programmes"
    return base, f"{label}, {programs} programme(s)" + (", mono-source" if single_source else "")


def exception_queue(result: MrpResult, ds: Dataset, names: dict[str, str], abc: dict[str, str] | None = None) -> dict[str, Any]:
    as_of = result.as_of
    calendar: WorkCalendar = result.calendar
    due_limit = calendar.add_working_days(as_of, ORDER_DUE_WD)
    progs: dict[str, set[str]] = {}
    for b in ds.bom:
        progs.setdefault(b.article_id, set()).add(b.program_id)
    prog_names = {p.program_id: p.name or p.program_id for p in ds.programs}
    links: dict[str, list] = {}
    for l in ds.links:
        if l.active:
            links.setdefault(l.article_id, []).append(l)
    items: list[dict[str, Any]] = []

    def who(sid: str | None) -> str:
        return f"{sid} {names.get(sid or '', '')}".strip() if sid else "sans fournisseur"

    def push(kind: str, ar, deadline: dt.date | None, risk: tuple[int, str], action: str, detail: str, supplier_id: str | None = None,
             qty: float | None = None, resolution_days: int | None = None, ref: str = "") -> None:
        aid = ar.article.article_id
        days = calendar.working_days_between(as_of, deadline) if deadline is not None else None
        if deadline is not None and deadline < as_of:
            days = -calendar.working_days_between(deadline, as_of) or -1
        u, u_why = _urgency(days)
        c, c_why = _criticality((abc or {}).get(aid), len(progs.get(aid, ())), len(links.get(aid, [])) == 1)
        k = ar.kpis
        first_short = k.get("first_stockout_plan")
        prog_list = sorted(prog_names.get(p, p) for p in progs.get(aid, ()))
        items.append({
            "id": f"{kind}|{aid}|{supplier_id or ''}|{ref or (deadline.isoformat() if deadline else '')}",
            "kind": kind, "kind_label": KINDS[kind], "article_id": aid, "designation": ar.article.designation, "unit": ar.article.unit,
            "owner": ar.article.planner or "", "supplier_id": supplier_id, "supplier_name": names.get(supplier_id or "", ""),
            "deadline": deadline.isoformat() if deadline else None, "days_to_deadline": days, "overdue": bool(deadline and deadline < as_of),
            "qty": qty, "ref": ref, "action": action, "detail": detail,
            "production_at_risk": {"first_stockout": first_short, "shortage_20wd": round(float(k.get("max_shortage_plan", 0.0)), 1),
                                   "programs": prog_list[:6], "program_count": len(prog_list)},
            "resolution_days": resolution_days if resolution_days is not None else int(k.get("lead_time_days", 5)),
            "score": u + risk[0] + c, "score_urgency": u, "score_risk": risk[0], "score_criticality": c,
            "why": [u_why, risk[1], c_why], "status": k.get("status"), "abc": (abc or {}).get(aid),
        })

    for ar in result.articles.values():
        aid = ar.article.article_id
        k = ar.kpis
        lead = int(k.get("lead_time_days", 5))
        first_short = k.get("first_stockout_plan")
        short_date = dt.date.fromisoformat(first_short) if first_short else None
        if short_date is not None and short_date <= calendar.add_working_days(as_of, lead):
            risk = (30, f"rupture projetée le {first_short} (dans le délai)")
        elif short_date is not None and short_date <= calendar.add_working_days(as_of, 20):
            risk = (20, f"rupture projetée le {first_short}")
        else:
            risk = (0, "pas de rupture projetée sous quatre semaines")
        # --- orders to place
        for p in sorted(ar.proposals, key=lambda p: (p.order_date, p.delivery_date)):
            if p.urgent or p.order_date < as_of:
                push("order_late", ar, p.order_date, (30, f"plancher ou date limite dépassée, livraison requise le {p.delivery_date.isoformat()}") if p.urgent else risk,
                     f"Commander {_q(p.qty)} {ar.article.unit} chez {who(p.supplier_id)} immédiatement, livraison le {p.delivery_date:%d/%m}",
                     p.reason, p.supplier_id, p.qty, lead, p.proposal_id)
            elif p.order_date <= due_limit:
                push("order_due", ar, p.order_date, risk,
                     f"Commander {_q(p.qty)} {ar.article.unit} chez {who(p.supplier_id)} au plus tard le {p.order_date:%d/%m}, livraison le {p.delivery_date:%d/%m}",
                     p.reason, p.supplier_id, p.qty, lead, p.proposal_id)
        # --- shortage the CBN could not avoid
        if ar.cap_shortfalls:
            c = ar.cap_shortfalls[0]
            push("stockout", ar, c.date, (30, f"manque inévitable de {_q(c.qty)} le {c.date.isoformat()} (plafond fournisseur)"),
                 f"Négocier une livraison supplémentaire chez {who(c.supplier_id)} ou un autre fournisseur avant le {c.date:%d/%m}",
                 f"{len(ar.cap_shortfalls)} jour(s) de manque que les plafonds du lien ne peuvent pas absorber", c.supplier_id, c.qty, lead)
        elif short_date is not None and not ar.proposals and short_date <= calendar.add_working_days(as_of, 20):
            push("stockout", ar, short_date, risk, "Vérifier la demande et les livraisons : rupture projetée sans proposition CBN possible",
                 f"première rupture le {first_short}, manque cumulé max {_q(k.get('max_shortage_plan', 0))}", None, None, lead)
        # --- supplier backlog
        for l in ar.lanes:
            for o in l.orders:
                if o.order_type == "FIRM" and o.expected_date < as_of and o.qty_open > 1e-6:
                    push("backlog", ar, o.expected_date, (15, "livraison attendue non soldée dans l'ERP") if risk[0] < 15 else risk,
                         f"Relancer {who(l.supplier_id)} : {_q(o.qty_open)} {ar.article.unit} restant sur {o.ref or o.order_id}, prévu le {o.expected_date:%d/%m}",
                         "ignorée dans le tableau" if o.ignored else "comptée dans la projection ferme", l.supplier_id, o.qty_open, 2, o.order_id)
            # --- despatch advices to fix, receipts to book
            for d in l.desadv:
                if d.source != "desadv" or d.received or d.hidden:
                    continue
                if d.state.strip().lower() == "erreur":
                    push("desadv_error", ar, d.issue_date, (0, "message EDI en erreur : la réception ne sera pas automatique"),
                         f"Corriger le DESADV {d.packing_slip or d.desadv_id} de {who(l.supplier_id)} dans l'ERP", f"état {d.state}", l.supplier_id, d.qty, 1, d.desadv_id)
            for r in l.receipt_lines:
                if not r.booked and (as_of - r.receipt_date).days >= ACR_PENDING_DAYS:
                    push("acr_pending", ar, r.receipt_date + dt.timedelta(days=ACR_PENDING_DAYS), (0, "réception comptée, accusé de réception non validé"),
                         f"Valider l'accusé de réception du BL {r.packing_slip or r.receipt_id} ({_q(r.qty)} {ar.article.unit}, reçu le {r.receipt_date:%d/%m})",
                         f"en attente depuis {(as_of - r.receipt_date).days} jours", l.supplier_id, r.qty, 1, r.receipt_id)
        # --- data issues with an action
        if k.get("erp_stock_drift"):
            gap = float(k.get("erp_stock_gap") or 0.0)
            push("stock_drift", ar, as_of, (20, "le stock réel est plus bas que la projection") if gap < 0 else (5, "le stock réel est plus haut que la projection"),
                 "Vérifier les déclarations de consommation et de réception, ou caler le stock projeté sur l'ERP",
                 f"écart ERP − projeté {gap:+,.0f} sur {k.get('erp_stock_drift_runs', 0)} snapshots".replace(",", " "), None, gap, 1)
        if float(k.get("stock_reference", 0.0)) < -1e-6:
            push("negative_stock", ar, as_of, (20, "stock de référence négatif"), "Corriger le stock de référence (inventaire ou ajustement)",
                 f"stock de référence {_q(k.get('stock_reference'))}", None, k.get("stock_reference"), 1)
        for u in ar.unlinked:
            if u.orders_open or u.orders_late:
                push("unlinked_orders", ar, as_of, (5, "commandes fermes hors du tableau"),
                     f"Créer le lien article ↔ fournisseur {who(u.supplier_id)} ou solder ses commandes",
                     f"{u.orders_open} commande(s) à venir ({_q(u.open_qty)}), {u.orders_late} en retard", u.supplier_id, u.open_qty + u.late_qty, 1)
        if not links.get(aid) and float(k.get("demand_horizon", 0.0)) > 1e-6:
            push("no_supplier", ar, as_of, risk, "Déclarer un fournisseur actif dans le Référentiel (aucune proposition possible)",
                 f"besoin de l'horizon {_q(k.get('demand_horizon', 0))} {ar.article.unit}", None, k.get("demand_horizon"), 1)
    items.sort(key=lambda x: (-x["score"], x["deadline"] or "9999", x["article_id"]))
    by_kind: dict[str, int] = {}
    by_owner: dict[str, int] = {}
    for it in items:
        by_kind[it["kind"]] = by_kind.get(it["kind"], 0) + 1
        by_owner[it["owner"] or "–"] = by_owner.get(it["owner"] or "–", 0) + 1
    return {"as_of": as_of.isoformat(), "total": len(items), "overdue": sum(1 for it in items if it["overdue"]),
            "today": sum(1 for it in items if it["days_to_deadline"] == 0),
            "by_kind": [{"kind": k, "label": KINDS[k], "count": by_kind.get(k, 0)} for k in KINDS if by_kind.get(k)],
            "by_owner": sorted(({"owner": o, "count": n} for o, n in by_owner.items()), key=lambda x: -x["count"]),
            "items": items}
