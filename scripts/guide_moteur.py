"""Génère ``docs/guide_moteur.md`` : le guide du moteur et du tableau d'approvisionnement.

Chaque exemple est **calculé par le moteur lui-même** sur un petit jeu de données : les tableaux
du guide sont donc toujours cohérents avec le code.  Lancer depuis la racine du dépôt ::

    python scripts/guide_moteur.py            # réécrit docs/guide_moteur.md
    python scripts/guide_moteur.py --check    # échoue si le fichier committé n'est plus à jour

Le test ``backend/tests/test_guide.py`` exécute la variante ``--check``.
"""
from __future__ import annotations

import datetime as dt
import sys
from pathlib import Path

RACINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE / "backend"))

from appro.engine import run_mrp  # noqa: E402
from appro.engine.calendar import WorkCalendar  # noqa: E402
from appro.engine.models import (  # noqa: E402
    AdjustCell,
    Article,
    BomLine,
    CellFlag,
    ConsumptionLine,
    Dataset,
    EngineParams,
    OrderLine,
    OrderType,
    PdpLine,
    PlanCell,
    Program,
    Receipt,
    StockSnapshot,
    Supplier,
    SupplierLink,
)

D = dt.timedelta
TODAY = dt.date(2026, 10, 5)          # un lundi : « aujourd'hui » de tous les exemples
INIT = TODAY - D(days=1)              # point zéro : stock connu le dimanche soir
JOURS = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]
CAL = WorkCalendar()


# =============================================================================
# Jeu de données de référence
# =============================================================================
def article(**over) -> Article:
    base = dict(article_id="A1", designation="Bobine cuivre", unit="PCE", planner="QUENTIN", coverage_target_days=5,
                alert_red_days=2, alert_yellow_days=5, overstock_days=30, safety_stock_qty=0.0, order_cycle_days=5)
    base.update(over)
    return Article(**base)


def link(**over) -> SupplierLink:
    base = dict(article_id="A1", supplier_id="S1", moq=200, pack_qty=50, lead_time_days=3, quota_pct=100, priority=1)
    base.update(over)
    return SupplierLink(**base)


def dataset(*, stock: float = 900.0, pdp_qty: float = 250.0, weeks: int = 8, **over) -> Dataset:
    """Un article, un fournisseur, un programme : PDP ``pdp_qty`` moteurs par semaine × 2 bobines =
    besoin de 500 bobines par semaine, soit 100 par jour ouvré."""
    base = dict(
        articles=[article()],
        suppliers=[Supplier("S1", "Fournisseur A")],
        links=[link()],
        programs=[Program("P1", "Moteur M3")],
        bom=[BomLine("P1", "A1", qty_per=2.0)],
        pdp=[PdpLine("P1", TODAY + D(weeks=k), pdp_qty) for k in range(0, weeks)],
        consumption=[],
        orders=[],
        receipts=[],
        stock=[StockSnapshot("A1", INIT, stock)],
    )
    base.update(over)
    return Dataset(**base)


def run(ds: Dataset, **params):
    p = dict(as_of=TODAY, horizon_days=21, generate_proposals=False)
    p.update(params)
    return run_mrp(ds, EngineParams(**p))


# =============================================================================
# Rendu Markdown
# =============================================================================
def q(v: float) -> str:
    if abs(v) < 1e-9:
        return "·"
    return f"{v:,.0f}".replace(",", " ")


def jour(d: dt.date) -> str:
    return f"{JOURS[d.weekday()]} {d.day:02d}/{d.month:02d}"


ROWS = [("Besoin", "demand", "flux"), ("Ferme", "orders_firm", "flux"), ("Ferme (passé)", "orders_firm_hist", "flux"), ("Reçu", "receipts", "flux"),
        ("Appro.", "plan", "flux"), ("Prop. CBN", "supply_proposed", "flux"), ("Ajustement", "adjustments", "flux"),
        ("Projeté ERP", "stock_erp", "stock"), ("Projeté Appro.", "stock_plan", "stock"), ("Cible", "target_stock", "stock")]


def tableau(ar, days: int = 10, start: dt.date | None = None, rows: list[str] | None = None, weekends: bool = False,
            lanes: bool = False) -> str:
    """Le tableau d'approvisionnement de l'article sur ``days`` jours (ouvrés par défaut) à partir
    d'``aujourd'hui`` : une ligne par série, une colonne par jour."""
    start = start or ar.as_of
    idx = [i for i, d in enumerate(ar.dates) if d >= start and (weekends or d.weekday() < 5)][:days]
    wanted = rows or [r[0] for r in ROWS]
    head = "| | " + " | ".join(jour(ar.dates[i]) for i in idx) + " |"
    sep = "|---|" + "|".join("---:" for _ in idx) + "|"
    lines = [head, sep]
    for label, key, kind in ROWS:
        if label not in wanted:
            continue
        if lanes and key in ("orders_firm", "orders_firm_hist", "receipts", "plan", "supply_proposed") and len(ar.lanes) > 1:
            for l in ar.lanes:
                arr = getattr(l, key)
                lines.append(f"| {label} · {l.supplier_id} | " + " | ".join(q(float(arr[i])) for i in idx) + " |")
            continue
        arr = getattr(ar, key)
        cells = []
        for i in idx:
            v = float(arr[i])
            if kind == "stock":
                short = float(ar.shortage_plan[i]) if key == "stock_plan" else float(ar.shortage_erp[i]) if key == "stock_erp" else 0.0
                cells.append(f"**{q(v)}**" if key != "target_stock" else q(v))
                if short > 1e-9:
                    cells[-1] += f" (−{q(short)})"
            else:
                cells.append(q(v))
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def propositions(ar) -> str:
    if not ar.proposals:
        return "_Aucune proposition._"
    out = ["| Livraison | Fournisseur | Quantité | Besoin net | À commander le | Urgente | Motif |", "|---|---|---:|---:|---|---|---|"]
    for p in ar.proposals:
        out.append(f"| {jour(p.delivery_date)} | {p.supplier_id} | {q(p.qty)} | {q(p.net_requirement)} | {jour(p.order_date)} | "
                   f"{'**oui**' if p.urgent else 'non'} | {p.reason} |")
    return "\n".join(out)


def alertes(ar) -> str:
    if not ar.alerts:
        return "_Aucune alerte._"
    sev = {"critical": "critique", "warning": "avertissement", "info": "info"}
    return "\n".join(f"- **{a.alert_type.value}** ({sev[a.severity.value]}, {a.scope}) : {a.message}" for a in ar.alerts)


def kpis(ar, keys: list[str]) -> str:
    labels = {"stock_at_date": "Stock à date", "coverage_erp_days": "Couverture ERP (j)", "coverage_plan_days": "Couverture Appro. (j)",
              "target_stock": "Stock cible du jour", "first_stockout_erp": "Rupture ERP", "first_stockout_plan": "Rupture Appro.",
              "demand_next_30d": "Besoin 30 j", "avg_daily_demand_30d": "Besoin / jour ouvré", "backlog_qty": "Backlog",
              "stock_reference": "Stock de référence", "reference_correction": "Correction du stock de référence"}
    out = ["| KPI | Valeur |", "|---|---:|"]
    for k in keys:
        v = ar.kpis[k]
        out.append(f"| {labels.get(k, k)} | {q(v) if isinstance(v, (int, float)) else (v or '–')} |")
    return "\n".join(out)


# =============================================================================
# Le guide
# =============================================================================
def guide() -> str:
    S: list[str] = []
    w = S.append

    w("# Guide du moteur et du tableau d'approvisionnement\n")
    w("> Généré par `scripts/guide_moteur.py` : chaque tableau ci-dessous est **calculé par le moteur** de "
      "l'application sur un petit jeu de données, les chiffres sont donc exacts par construction. Les règles "
      "détaillées sont dans `regles_metier.md` ; ce guide explique **chaque paramètre et son effet**, avec un "
      "scénario par paramètre.\n")
    w(f"Conventions de tous les exemples : aujourd'hui est le **{jour(TODAY)}/2026** ; le stock de référence est "
      f"connu la veille au soir ({jour(INIT)}, le *point zéro*) ; l'article **A1 « Bobine cuivre »** entre à raison "
      "de **2 par moteur** dans le programme **Moteur M3**, dont le PDP est de **250 moteurs par semaine** : le "
      "besoin est donc de **500 bobines par semaine, 100 par jour ouvré** (semaine ouvrée du lundi au vendredi, "
      "pas de fermeture). Fournisseur **S1** : MOQ 200, UM 50, délai 3 jours ouvrés, livraisons tous les jours "
      "ouvrés. Article : couverture cible 5 jours, seuil rouge 2 jours, seuil orange 5 jours, surstock 30 jours, "
      "cycle de commande 5 jours. Sauf mention contraire, les paramètres globaux sont ceux par défaut.\n")
    w("Les tableaux montrent les jours ouvrés ; **Projeté ERP** et **Projeté Appro.** sont les deux stocks projetés "
      "en fin de journée, en gras ; un manque (besoin non servi) est indiqué entre parenthèses ; la **Cible** est "
      "le stock cible du jour.\n")

    # ------------------------------------------------------------------ 1. lecture
    w("## 1. Lire le tableau : besoin, stock projeté, stock cible\n")
    r = run(dataset())
    ar = r.articles["A1"]
    w("Sans aucune commande, le stock de référence (900) s'épuise de 100 par jour ouvré. Le **Projeté ERP** et le "
      "**Projeté Appro.** sont identiques tant qu'il n'y a ni commande ferme, ni saisie, ni proposition. La "
      "**Cible** d'un jour est le besoin des 5 jours calendaires **suivants** (`coverage_target_days = 5`, "
      "`coverage_unit = calendar`) : un lundi, du mardi au samedi, soit 4 jours ouvrés = 400 ; du mardi au "
      "vendredi la fenêtre englobe le week-end sans besoin, soit 300.\n")
    w(tableau(ar, days=10, rows=["Besoin", "Projeté ERP", "Projeté Appro.", "Cible"]))
    w("")
    w(kpis(ar, ["stock_at_date", "coverage_plan_days", "target_stock", "first_stockout_plan", "demand_next_30d", "avg_daily_demand_30d"]))
    w("")
    w("**Lecture.** La couverture (en jours) est le nombre de jours dont le besoin cumulé tient dans le stock du "
      "jour. La première rupture est le premier jour dont le besoin n'est pas servi : le stock projeté y est "
      "nul et le manque est affiché entre parenthèses. Le *Besoin 30 j* reste une somme sur 30 jours calendaires ; "
      "sa moyenne se divise par les jours ouvrés de la fenêtre.\n")
    w(alertes(ar))
    w("")

    # ------------------------------------------------------------------ 2. ERP et Appro.
    w("## 2. Commandes ERP, ligne Appro. et commande ignorée\n")
    ds = dataset(orders=[OrderLine("CO-1", "A1", "S1", TODAY + D(days=3), 400, order_type=OrderType.FIRM),
                         OrderLine("CO-2", "A1", "S1", TODAY + D(days=10), 400, order_type=OrderType.FIRM)])
    ar = run(ds).articles["A1"]
    w("Deux commandes fermes ERP de 400 arrivent le jeudi 08/10 et le jeudi 15/10. Sans saisie, la ligne "
      "**Appro.** reprend la commande ferme de chaque jour (cellule vide = ERP) : les deux projetés sont égaux.\n")
    w(tableau(ar, days=10, rows=["Besoin", "Ferme", "Appro.", "Projeté ERP", "Projeté Appro."]))
    w("")
    ds.plan = [PlanCell("A1", "S1", TODAY + D(days=3), 700.0), PlanCell("A1", "S1", TODAY + D(days=10), 0.0)]
    ar2 = run(ds).articles["A1"]
    w("L'approvisionneur **saisit 700** dans la cellule Appro. du 08/10 (il négocie une livraison plus grosse) et "
      "**0** le 15/10 (il annule celle-là). Le **Projeté ERP** ne bouge pas : il reflète l'ERP tel quel. Le "
      "**Projeté Appro.** suit la décision de l'approvisionneur : c'est le scénario sur lequel le moteur calcule "
      "les propositions CBN et les alertes de rupture du plan.\n")
    w(tableau(ar2, days=10, rows=["Besoin", "Ferme", "Appro.", "Projeté ERP", "Projeté Appro."]))
    w("")
    ds.plan = []
    ds.flags = [CellFlag("A1", "S1", TODAY + D(days=10), "order_ignored")]
    ar3 = run(ds).articles["A1"]
    w("**Commande ignorée** : un clic sur la cellule *Ferme* du 15/10 la barre. Elle sort du Projeté ERP **et** "
      "du pré-remplissage de la ligne Appro. (l'approvisionneur sait que cette commande ne viendra pas) ; un second "
      "clic la rétablit.\n")
    w(tableau(ar3, days=10, rows=["Besoin", "Ferme", "Appro.", "Projeté ERP", "Projeté Appro."]))
    w("")
    w("> Dans le tableau, la ligne *Ferme* affiche `restant / commandé` après une réception partielle ; une "
      "commande ferme **passée** et non reçue ne compte dans aucun stock : elle entre dans le **backlog** du "
      "fournisseur (§ 11).\n")

    # ------------------------------------------------------------------ 3. cible
    w("## 3. Couverture cible, stock de sécurité, unité de couverture\n")
    w("Le **stock cible** d'un jour est, selon `target_policy` : le besoin des `coverage_target_days` jours "
      "suivants (`coverage_days`), le `safety_stock_qty` fixe (`safety_qty`), ou le **maximum des deux** "
      "(`max`, défaut). C'est le niveau sous lequel le moteur déclenche une proposition.\n")
    ar5 = run(dataset()).articles["A1"]
    ar10 = run(dataset(articles=[article(coverage_target_days=10)])).articles["A1"]
    ar_s = run(dataset(articles=[article(safety_stock_qty=800)])).articles["A1"]
    ar_w = run(dataset(), coverage_unit="working").articles["A1"]
    w("| Jour | Cible, couverture 5 j calendaires | Cible, couverture 10 j | Cible, sécurité 800 (max) | Cible, 5 j **ouvrés** |")
    w("|---|---:|---:|---:|---:|")
    i0 = ar5.dates.index(TODAY)
    for i in range(i0, i0 + 7):
        if ar5.dates[i].weekday() >= 5:
            continue
        w(f"| {jour(ar5.dates[i])} | {q(ar5.target_stock[i])} | {q(ar10.target_stock[i])} | {q(ar_s.target_stock[i])} | {q(ar_w.target_stock[i])} |")
    w("")
    w("**Lecture.** En jours calendaires, la cible compte les 5 jours **suivants** : 400 un lundi (mardi → "
      "samedi), 300 du mardi au vendredi (le week-end entre dans la fenêtre) ; en jours **ouvrés** "
      "(`coverage_unit = working`) la cible vaut toujours 5 jours de besoin, soit 500. Le stock de sécurité fixe prend le relais dès que la couverture lui est "
      "inférieure. `coverage_tie_rule` décide si un jour dont le besoin cumulé **égale** exactement le stock est "
      "compté couvert (`covered`, défaut) ou non.\n")

    # ------------------------------------------------------------------ 4. CBN
    w("## 4. Propositions CBN : besoin net, MOQ, UM, cycle de commande\n")
    ds = dataset()
    ar = run(ds, generate_proposals=True).articles["A1"]
    w("Avec `generate_proposals` (défaut : oui), le moteur parcourt le Projeté Appro. à partir de demain et, dès "
      "que `stock < cible`, propose une livraison. La quantité vise le **niveau de recomplètement** = cible + "
      "besoin du cycle de commande suivant (`order_cycle_days = 5`) : on ne recommande pas tous les jours. Elle "
      "est relevée au **MOQ** (200) puis arrondie au multiple supérieur de l'**UM** (50). La date de commande = "
      "livraison − délai fournisseur en jours ouvrés (3).\n")
    w(tableau(ar, days=15, rows=["Besoin", "Prop. CBN", "Projeté ERP", "Projeté Appro.", "Cible"]))
    w("")
    w(propositions(ar))
    w("")
    w("**Lecture.** Le Projeté Appro. intègre les propositions (`include_proposals_in_plan`, défaut : oui) : "
      "il reste au-dessus de la cible, tandis que le Projeté ERP, sans commande, tombe à zéro. Les propositions "
      "sont **recalculées à chaque affichage** et n'existent nulle part ailleurs ; pour en reprendre une, "
      "l'approvisionneur tape la quantité dans la cellule Appro. (la cellule grisée la prérempli).\n")
    ar_c0 = run(dataset(articles=[article(order_cycle_days=0)]), generate_proposals=True).articles["A1"]
    w("Avec un **cycle de commande à 0**, le niveau de recomplètement se réduit à la cible : les propositions "
      "sont plus petites et plus fréquentes.\n")
    w(propositions(ar_c0))
    w("")
    ar_lot = run(dataset(links=[link(moq=1000, pack_qty=250)]), generate_proposals=True).articles["A1"]
    w("Avec **MOQ 1 000 et UM 250**, la première proposition saute au MOQ ; la suivante est repoussée d'autant.\n")
    w(propositions(ar_lot))
    w("")

    # ------------------------------------------------------------------ 5. délai
    w("## 5. Délai fournisseur : propositions urgentes et `respect_lead_time`\n")
    ds = dataset(stock=350.0, links=[link(lead_time_days=10)])
    ar = run(ds, generate_proposals=True).articles["A1"]
    first = ar.proposals[0]
    w(f"Stock de départ 350 et délai de **10 jours ouvrés**. Le creux arrive le {jour(first.delivery_date)} ; la livraison "
      f"est placée ce jour-là, mais la commande aurait dû partir 10 jours ouvrés plus tôt ({jour(first.order_date)}) : la proposition est "
      "**urgente**, sa date « à commander le » est dans le passé (c'est la date réelle, affichée telle quelle), et "
      "une alerte `URGENT_PROPOSAL` est levée.\n")
    w(tableau(ar, days=10, rows=["Besoin", "Prop. CBN", "Projeté Appro.", "Cible"]))
    w("")
    w(propositions(ar))
    w("")
    w(alertes(ar))
    w("")
    ar_r = run(ds, generate_proposals=True, respect_lead_time=True).articles["A1"]
    w("Avec `respect_lead_time = oui`, le moteur **ne propose jamais l'impossible** : la première livraison "
      "possible est aujourd'hui + 10 jours ouvrés, le besoin est réévalué ce jour-là, et les jours de manque "
      "entre-temps sont une rupture du Projeté Appro. (alerte `STOCKOUT`, scénario plan).\n")
    w(tableau(ar_r, days=15, rows=["Besoin", "Prop. CBN", "Projeté Appro.", "Cible"]))
    w("")
    w(propositions(ar_r))
    w("")
    w(alertes(ar_r))
    w("")

    # ------------------------------------------------------------------ 6. jours de livraison
    w("## 6. Jours de livraison du fournisseur et `delivery_shift`\n")
    ds = dataset(suppliers=[Supplier("S1", "Fournisseur A", delivery_weekdays=frozenset({2, 4}))])
    ar_e = run(ds, generate_proposals=True).articles["A1"]
    ar_l = run(ds, generate_proposals=True, delivery_shift="later").articles["A1"]
    w("Le fournisseur ne livre que le **mardi et le jeudi** (Référentiel › Fournisseurs, jours de livraison). "
      "Quand le creux tombe un autre jour, `delivery_shift = earlier` (défaut) cherche le jour de livraison "
      "autorisé le plus proche **avant** (la marchandise est là avant le creux) ; `later` cherche **après** "
      "(quitte à accepter un manque, remonté en alerte).\n")
    w("Avec `earlier` :\n")
    w(propositions(ar_e))
    w("")
    w("Avec `later` :\n")
    w(propositions(ar_l))
    w("")
    w(tableau(ar_l, days=10, rows=["Besoin", "Prop. CBN", "Projeté Appro.", "Cible"]))
    w("")

    # ------------------------------------------------------------------ 7. lundi, gel, horizon
    w("## 7. Livraisons groupées le lundi, période gelée, limite des propositions\n")
    ar_m = run(dataset(), generate_proposals=True, proposal_placement="monday").articles["A1"]
    w("`proposal_placement = monday` ramène toutes les livraisons proposées au **lundi**, quels que soient les "
      "jours du fournisseur ; les besoins d'une même semaine sont fusionnés en une proposition par fournisseur "
      "et par lundi.\n")
    w(propositions(ar_m))
    w("")
    ar_f = run(dataset(stock=350.0), generate_proposals=True, frozen_days=5).articles["A1"]
    w("`frozen_days = 5` : aucune livraison n'est proposée avant aujourd'hui + 5 jours (période gelée, par "
      "exemple parce que les commandes de la semaine sont déjà passées) ; le besoin du creux est réévalué au "
      "premier jour proposable.\n")
    w(propositions(ar_f))
    w("")
    ar_la = run(dataset(), horizon_days=60, generate_proposals=True, proposal_lookahead_days=14).articles["A1"]
    w("`proposal_lookahead_days = 14` : les propositions s'arrêtent à J + 14 même si l'horizon de calcul est "
      f"plus long ({len(ar_la.proposals)} proposition(s) sur un horizon de 60 jours) ; `stockout_lookahead_days` "
      "joue le même rôle pour la détection des ruptures.\n")
    w(propositions(ar_la))
    w("")

    # ------------------------------------------------------------------ 8. politique de manque
    w("## 8. Besoin non servi : `shortage_policy` et tolérance de manque\n")
    ds = dataset(stock=350.0, orders=[OrderLine("CO-1", "A1", "S1", TODAY + D(days=7), 600, order_type=OrderType.FIRM)])
    ar_b = run(ds).articles["A1"]
    ar_lo = run(ds, shortage_policy="lost").articles["A1"]
    w("Stock 350, une seule commande ferme de 600 le lundi 12/10. Le besoin des jours précédents n'est pas servi. "
      "Avec `backlog` (défaut) le besoin non servi est **reporté** : la livraison du 12/10 sert d'abord le "
      "retard (stock net négatif, affiché comme manque). Avec `lost` le besoin manqué est **perdu** : le stock "
      "physique est borné à 0 et la livraison repart de zéro.\n")
    w("Politique `backlog` :\n")
    w(tableau(ar_b, days=10, rows=["Besoin", "Ferme", "Projeté Appro."]))
    w("")
    w("Politique `lost` :\n")
    w(tableau(ar_lo, days=10, rows=["Besoin", "Ferme", "Projeté Appro."]))
    w("")
    ds_t = dataset(stock=420.0, orders=[OrderLine("CO-1", "A1", "S1", TODAY + D(days=2), 500, order_type=OrderType.FIRM)])
    ar_t0 = run(ds_t, generate_proposals=True).articles["A1"]
    ar_t2 = run(ds_t, generate_proposals=True, shortfall_tolerance_days=2).articles["A1"]
    w("**Tolérance de manque** (`shortfall_tolerance_days`) : stock 420, livraison ferme de 500 le mercredi. Le "
      "stock passe sous la cible mardi (220 < 300), **sans manque**, et repasse au-dessus mercredi grâce à la "
      "livraison. Avec la tolérance à 0 (défaut) le moteur propose dès mardi ; avec 2 jours ouvrés, un creux qui "
      "se résorbe seul en 2 jours sans besoin non servi est ignoré et la première proposition vient plus tard.\n")
    w("Tolérance 0 :\n")
    w(propositions(ar_t0))
    w("")
    w("Tolérance 2 jours ouvrés :\n")
    w(propositions(ar_t2))
    w("")
    w(tableau(ar_t2, days=6, rows=["Besoin", "Ferme", "Projeté Appro.", "Cible"]))
    w("")

    # ------------------------------------------------------------------ 9. multi-sourcing
    w("## 9. Multi-sourcing : quotas, priorité, refus d'une proposition\n")
    ds = dataset(suppliers=[Supplier("S1", "Fournisseur A"), Supplier("S2", "Fournisseur B")],
                 links=[link(quota_pct=70, priority=1), link(supplier_id="S2", quota_pct=30, priority=2, lead_time_days=5)])
    ar_q = run(ds, horizon_days=42, generate_proposals=True).articles["A1"]
    ar_p = run(ds, horizon_days=42, generate_proposals=True, sourcing_policy="priority").articles["A1"]
    w("Deux fournisseurs : S1 (quota 70 %, priorité 1, délai 3) et S2 (quota 30 %, priorité 2, délai 5). Avec "
      "`sourcing_policy = quota` (défaut), chaque proposition va au fournisseur dont la part cumulée est la plus "
      "en retard sur son quota ; avec `priority`, tout va au fournisseur de priorité 1. Chaque fournisseur a sa "
      "propre voie dans le tableau (lignes Ferme / Reçu / Appro. / Prop. CBN).\n")
    w("Quotas 70 / 30 (six semaines) :\n")
    w(propositions(ar_q))
    tot = {sid: sum(p.qty for p in ar_q.proposals if p.supplier_id == sid) for sid in ("S1", "S2")}
    w("")
    w(f"Parts obtenues sur six semaines : S1 {q(tot['S1'])}, S2 {q(tot['S2'])} ({100 * tot['S1'] / (tot['S1'] + tot['S2']):.0f} % / "
      f"{100 * tot['S2'] / (tot['S1'] + tot['S2']):.0f} %) : la répartition est déterministe et se rapproche du quota "
      "proposition après proposition, sans jamais couper une proposition en deux.\n")
    w("Priorité :\n")
    w(propositions(ar_p))
    w("")
    w(tableau(ar_q, days=10, rows=["Besoin", "Prop. CBN", "Projeté Appro."], lanes=True))
    w("")
    first = ar_q.proposals[0]
    ds.flags = [CellFlag("A1", first.supplier_id, first.delivery_date, "proposal_refused", qty=first.qty)]
    ar_ref = run(ds, horizon_days=42, generate_proposals=True).articles["A1"]
    w(f"**Refus** : un clic sur la proposition du {jour(first.delivery_date)} ({first.supplier_id}) la barre et "
      f"interdit toute proposition à **ce fournisseur** jusqu'au dimanche de sa semaine ; le besoin est reporté "
      "après la fenêtre (ou va à l'autre fournisseur si les quotas le permettent). La proposition refusée reste "
      "listée dans le flux *À commander* du cockpit, marquée « ignorée », si elle était urgente.\n")
    w(propositions(ar_ref))
    w("")

    # ------------------------------------------------------------------ 10. alertes
    w("## 10. Alertes : seuils rouge et orange, surstock, gradation des ruptures\n")
    w("Les alertes de **couverture** regardent l'épuisement des flux ERP (stock + commandes fermes) : "
      "`LOW_COVERAGE` critique si les commandes ERP sont épuisées sous `alert_red_days`, avertissement sous "
      "`alert_yellow_days`, `OVERSTOCK` (info) si le stock à date couvre plus d'`overstock_days`. Les **ruptures** "
      "(`STOCKOUT`) sont graduées sur les deux scénarios : critique dans le délai fournisseur, avertissement dans "
      "`firm_horizon_days` (28), information au-delà.\n")
    cases = [("Stock 150 (rupture dès demain)", dataset(stock=150.0)),
             ("Stock 450 (rupture vendredi)", dataset(stock=450.0)),
             ("Stock 8 000 (80 jours de besoin)", dataset(stock=8000.0)),
             ("Stock 3 000, ferme ERP 500 le 12/10 : rupture ERP à J + 32", dataset(stock=3000.0, orders=[OrderLine("CO-1", "A1", "S1", TODAY + D(days=7), 500, order_type=OrderType.FIRM)]))]
    for title, d in cases:
        ar = run(d, horizon_days=60).articles["A1"]
        w(f"**{title}**\n")
        w(alertes(ar))
        w("")
    ds_md = dataset(stock=900.0, links=[])
    w("**Données manquantes** (pas de fournisseur lié) :\n")
    w(alertes(run(ds_md).articles["A1"]))
    w("")

    # ------------------------------------------------------------------ 11. réel, semaine en cours, backlog
    w("## 11. Consommation réelle, semaine en cours, réceptions et backlog\n")
    ds = dataset(stock=900.0,
                 consumption=[ConsumptionLine("A1", TODAY - D(days=7), 120), ConsumptionLine("A1", TODAY - D(days=6), 80),
                              ConsumptionLine("A1", TODAY - D(days=5), 100), ConsumptionLine("A1", TODAY - D(days=4), 90),
                              ConsumptionLine("A1", TODAY - D(days=3), 110)],
                 orders=[OrderLine("CO-0", "A1", "S1", TODAY - D(days=5), 300, order_type=OrderType.FIRM),
                         OrderLine("CO-1", "A1", "S1", TODAY + D(days=2), 300, order_type=OrderType.FIRM)],
                 receipts=[Receipt("R-1", "A1", TODAY - D(days=5), 200, supplier_id="S1", packing_slip="BL-1")],
                 pdp=[PdpLine("P1", TODAY + D(weeks=k), 250.0) for k in range(-1, 8)])
    ds.stock = [StockSnapshot("A1", TODAY - D(days=8), 900.0)]
    as_of_wed = TODAY + D(days=2)
    ds.consumption += [ConsumptionLine("A1", TODAY, 150), ConsumptionLine("A1", TODAY + D(days=1), 50)]
    ar = run(ds, as_of=as_of_wed, history_weeks=1).articles["A1"]
    w(f"Ici aujourd'hui est le **{jour(as_of_wed)}** et le stock a été initialisé le {jour(TODAY - D(days=8))}. "
      "Le passé n'est jamais reconstruit : le besoin des jours passés est la **consommation réelle** déclarée "
      "(`conso_composants`, déjà éclatée par composant) ; un jour sans déclaration compte 0 "
      "(`missing_actual_policy = zero`). La **semaine en cours** consomme son PDP éclaté (500) moins le réel déjà "
      "déclaré (150 + 50 = 200) : le reliquat (300) est réparti sur les jours ouvrés restants, aujourd'hui compris "
      "(`production_mode = actual_then_remainder`). La commande ferme du 30/09 (300) n'a été reçue qu'à 200 : les "
      "100 manquants sont le **backlog** du fournisseur, affiché mais compté dans aucun stock.\n")
    w(tableau(ar, days=10, start=TODAY - D(days=7), rows=["Besoin", "Ferme", "Ferme (passé)", "Reçu", "Appro.", "Projeté ERP", "Projeté Appro."]))
    w("")
    w(kpis(ar, ["stock_reference", "stock_at_date", "backlog_qty", "coverage_plan_days"]))
    w("")
    w("**Lecture.** *Stock à date* = stock projeté à la fin de la veille (réceptions et consommations réelles "
      "comprises). La fenêtre de backlog remonte de `backlog_days` (28) jours, jamais avant le point zéro.\n")

    # ------------------------------------------------------------------ 12. ajustements
    w("## 12. Ajustements et correction du stock de référence\n")
    ds = dataset(adjustments=[AdjustCell("A1", INIT, -150, "inventaire : écart constaté"), AdjustCell("A1", TODAY + D(days=3), -200, "rebut prévu")])
    ar = run(ds).articles["A1"]
    w("Une cellule **Ajustement** est une quantité signée. Datée **au plus tard le point zéro**, elle corrige le "
      "stock de référence (l'ERP se trompe, l'application n'y écrit jamais) ; datée après, c'est un mouvement "
      "connu d'avance (rebut, transfert). Ici −150 le jour de l'initialisation et −200 le jeudi.\n")
    w(tableau(ar, days=6, rows=["Besoin", "Ajustement", "Projeté ERP", "Projeté Appro."]))
    w("")
    w(kpis(ar, ["stock_reference", "reference_correction", "stock_at_date"]))
    w("")

    # ------------------------------------------------------------------ 13. hebdo
    w("## 13. Paramètres d'article par semaine\n")
    a_w = article()
    a_w.weekly = {"2026-W42": {"coverage_target_days": 10, "safety_stock_qty": 0}}
    ar = run(dataset(articles=[a_w]), horizon_days=21).articles["A1"]
    w("Les paramètres de l'article (couverture cible, seuils, surstock, stock de sécurité, cycle) peuvent être "
      "surchargés **par semaine ISO** (Référentiel › Articles › Semaines). Ici la couverture cible passe de 5 à "
      "**10 jours pour la semaine 42** seulement : la cible double cette semaine-là, puis revient à 5 jours.\n")
    w(tableau(ar, days=15, rows=["Besoin", "Cible"]))
    w("")

    # ------------------------------------------------------------------ 14. fenêtre
    w("## 14. Fenêtre de calcul, affichage, cache\n")
    w("- **Point zéro** = date du stock de référence (`fct_stock.snapshot_date`), une seule pour tous les "
      "articles, jamais après aujourd'hui. Rien n'existe avant : pas d'historique reconstruit.\n"
      "- **Fenêtre** = du point zéro à aujourd'hui + `horizon_days` (120), étendue au dimanche de la semaine en "
      "cours ; le tableau commence au lundi de la semaine en cours moins `history_weeks` (2) semaines.\n"
      "- **Calendrier** « Par défaut » : un jour par colonne pour la semaine en cours et `focus_weeks` (2) "
      "semaines, une semaine par colonne au-delà ; une colonne semaine se saisit comme une colonne jour, la "
      "valeur se place au lundi de la semaine (au jour de référence dans la semaine en cours) ; cliquer "
      "l'en-tête pour le détail par jour.\n"
      "- Le **sélecteur de période** sous la barre d'outils ne change que les colonnes affichées.\n"
      "- Tout est recalculé à chaque demande et mis en cache jusqu'à la prochaine écriture (saisie, import, "
      "paramètre) ou synchronisation ERP.\n")

    # ------------------------------------------------------------------ 15. récapitulatif
    w("## 15. Récapitulatif des paramètres\n")
    w("| Paramètre | Défaut | Portée | Effet |")
    w("|---|---|---|---|")
    recap = [
        ("coverage_target_days", "7 (article)", "article, semaine", "jours de besoin à tenir en stock : fixe la cible, donc le déclenchement et la taille des propositions"),
        ("safety_stock_qty", "0 (article)", "article, semaine", "plancher fixe de la cible (`target_policy = max`)"),
        ("order_cycle_days", "7 (article)", "article, semaine", "besoin ajouté au niveau de recomplètement : moins de propositions, plus grosses"),
        ("alert_red_days / alert_yellow_days", "3 / 7 (article)", "article, semaine", "seuils des alertes de couverture (épuisement des commandes ERP)"),
        ("overstock_days", "30 (article)", "article, semaine", "seuil de l'alerte surstock"),
        ("moq / pack_qty / lead_time_days", "lien article ↔ fournisseur", "fournisseur", "quantité minimale, arrondi UM, délai ouvré (date de commande, urgence)"),
        ("quota_pct / priority", "lien", "fournisseur", "répartition multi-sourcing"),
        ("delivery_weekdays", "1-5 (fournisseur)", "fournisseur", "jours de livraison autorisés"),
        ("target_policy", "max", "global", "cible = couverture, sécurité fixe ou max des deux"),
        ("coverage_unit", "calendar", "global", "couverture et cible en jours calendaires ou ouvrés"),
        ("coverage_tie_rule", "covered", "global", "un jour dont le besoin cumulé égale le stock est-il couvert ?"),
        ("shortage_policy", "backlog", "global", "besoin non servi reporté ou perdu"),
        ("shortfall_tolerance_days", "0", "global", "creux sous cible tolérés s'ils se résorbent seuls"),
        ("generate_proposals", "oui", "global", "calcul des propositions CBN"),
        ("include_proposals_in_plan", "oui", "global", "les propositions entrent dans le Projeté Appro."),
        ("proposal_placement", "working_days", "global", "livraisons tout jour ouvré autorisé, ou lundis"),
        ("delivery_shift", "earlier", "global", "jour non autorisé : avancer ou reculer"),
        ("respect_lead_time", "non", "global", "jamais de livraison avant aujourd'hui + délai"),
        ("frozen_days", "0", "global", "période gelée sans proposition"),
        ("proposal_lookahead_days / stockout_lookahead_days", "vide", "global", "limites de calcul des propositions / ruptures"),
        ("sourcing_policy", "quota", "global", "quotas ou priorité"),
        ("firm_horizon_days", "28", "global", "gradation des alertes de rupture"),
        ("firm_sources", "FIRM", "global", "types de commandes comptés dans le Projeté ERP"),
        ("forecast_date_policy", "actual", "global", "commandes prévisionnelles à leur date ou au lundi"),
        ("backlog_days", "28", "global", "fenêtre du backlog fournisseur"),
        ("production_mode / missing_actual_policy", "actual_then_remainder / zero", "global", "besoin passé et reliquat de la semaine en cours"),
        ("spread_rounding / consumption_offset_days", "exact / 0", "global", "lissage du PDP hebdomadaire sur les jours ouvrés, décalage de consommation"),
        ("horizon_days / history_weeks / focus_weeks", "120 / 2 / 2", "global", "fenêtre de calcul et d'affichage"),
    ]
    for name, default, scope, effet in recap:
        w(f"| `{name}` | {default} | {scope} | {effet} |")
    w("")
    w("Les paramètres globaux se règlent dans *Paramètres & règles* (managers, administrateurs) ; ceux de "
      "l'article dans le *Référentiel*, avec leurs surcharges hebdomadaires ; ceux du fournisseur et du lien "
      "article ↔ fournisseur dans le *Référentiel* également.\n")
    return "\n".join(S)


def main() -> int:
    target = RACINE / "docs" / "guide_moteur.md"
    text = guide()
    if "--check" in sys.argv:
        current = target.read_text(encoding="utf-8") if target.exists() else ""
        if current != text:
            print(f"{target} n'est plus à jour : relancer python scripts/guide_moteur.py", file=sys.stderr)
            return 1
        print("guide_moteur.md à jour")
        return 0
    target.write_text(text, encoding="utf-8")
    print(f"{target} ({len(text.splitlines())} lignes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
