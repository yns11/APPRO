"""Génère ``docs/guide_moteur.md`` : le guide du moteur et du tableau d'approvisionnement.

Chaque exemple est **calculé par le moteur lui-même** sur un petit jeu de données : les tableaux
du guide sont donc toujours cohérents avec le code.  Lancer depuis la racine du dépôt ::

    python scripts/guide_moteur.py            # réécrit docs/guide_moteur.md
    python scripts/guide_moteur.py --check    # échoue si le fichier committé n'est plus à jour

Le test ``backend/tests/test_guide.py`` exécute la variante ``--check`` ; les scénarios du guide
servent aussi de tests de non-régression (``backend/tests/test_engine_units.py``).
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
                alert_red_days=2, alert_yellow_days=5, overstock_days=30)
    base.update(over)
    return Article(**base)


def link(**over) -> SupplierLink:
    base = dict(article_id="A1", supplier_id="S1", moq=200, pack_qty=50, quota_pct=100, priority=1)
    base.update(over)
    return SupplierLink(**base)


def supplier(sid: str = "S1", name: str = "Fournisseur A", lead: int = 3, days: frozenset[int] | None = None) -> Supplier:
    return Supplier(sid, name, lead_time_days=lead, delivery_weekdays=days or frozenset({1, 2, 3, 4, 5}))


def dataset(*, stock: float = 900.0, pdp_qty: float = 250.0, weeks: int = 8, **over) -> Dataset:
    """Un article, un fournisseur, un programme : PDP ``pdp_qty`` moteurs par semaine × 2 bobines =
    besoin de 500 bobines par semaine, soit 100 par jour ouvré."""
    base = dict(
        articles=[article()],
        suppliers=[supplier()],
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


def run(ds: Dataset, cbn: bool = True, **params):
    """``cbn=False`` : tolérance à 100 % = aucune proposition de retour à la cible (le plancher reste)."""
    p = dict(as_of=TODAY, horizon_days=21)
    if not cbn:
        p["target_tolerance_pct"] = 100.0
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
    d'``aujourd'hui`` : une ligne par série, une colonne par jour.  Les stocks projetés portent le
    **manque cumulé** entre parenthèses (ce que montre l'écran)."""
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
                short = float(ar.shortage_plan_cum[i]) if key == "stock_plan" else float(ar.shortage_erp_cum[i]) if key == "stock_erp" else 0.0
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
    out = ["| Livraison | Fournisseur | Quantité | Besoin net | À commander le | Urgente | Plancher | Plafonnée | Motif |",
           "|---|---|---:|---:|---|---|---|---|---|"]
    for p in ar.proposals:
        out.append(f"| {jour(p.delivery_date)} | {p.supplier_id} | {q(p.qty)} | {q(p.net_requirement)} | {jour(p.order_date)} | "
                   f"{'**oui**' if p.urgent else 'non'} | {'oui' if p.floor else 'non'} | {'oui' if p.capped else 'non'} | {p.reason} |")
    return "\n".join(out)


def alertes(ar) -> str:
    if not ar.alerts:
        return "_Aucune alerte._"
    sev = {"critical": "critique", "warning": "avertissement", "info": "info"}
    return "\n".join(f"- **{a.alert_type.value}** ({sev[a.severity.value]}, {a.scope}) : {a.message}" for a in ar.alerts)


def kpis(ar, keys: list[str]) -> str:
    labels = {"stock_at_date": "Stock à date", "coverage_erp_days": "Couverture ERP (j ouvrés)", "coverage_plan_days": "Couverture Appro. (j ouvrés)",
              "target_stock": "Stock cible du jour", "first_stockout_erp": "Rupture ERP", "first_stockout_plan": "Rupture Appro.",
              "demand_next_20wd": "Besoin 20 j ouvrés", "avg_daily_demand_wd": "Besoin / jour ouvré", "backlog_qty": "En retard (restant ERP)",
              "stock_reference": "Stock de référence", "reference_correction": "Correction du stock de référence", "lead_time_days": "Délai (j ouvrés)",
              "max_shortage_plan": "Manque cumulé max (Appro.)"}
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
      "détaillées sont dans `regles_metier.md` ; ce guide explique **chaque règle et chaque paramètre**, avec un "
      "scénario par règle. Les mêmes scénarios sont rejoués dans `backend/tests/test_engine_units.py`.\n")
    w(f"Conventions de tous les exemples : aujourd'hui est le **{jour(TODAY)}/2026** ; le stock de référence est "
      f"connu la veille au soir ({jour(INIT)}, le *point zéro*) ; l'article **A1 « Bobine cuivre »** entre à raison "
      "de **2 par moteur** dans le programme **Moteur M3**, dont le PDP est de **250 moteurs par semaine** : le "
      "besoin est donc de **500 bobines par semaine, 100 par jour ouvré** (semaine ouvrée du lundi au vendredi, "
      "pas de fermeture, jours de consommation = 5). Fournisseur **S1** : délai **3 jours ouvrés** (inclusif : "
      "commandé lundi, livrable jeudi), livraisons tous les jours ouvrés ; lien A1 ↔ S1 : MOQ 200, UM 50, sans "
      "plafond. Article : couverture cible 5 jours ouvrés, seuil rouge 2, seuil orange 5, surstock 30. Règle "
      "commune : tolérance sous la cible 15 %.\n")
    w("Tous les délais, couvertures, seuils et horizons sont en **jours ouvrés**. Les tableaux montrent les jours "
      "ouvrés ; **Projeté ERP** et **Projeté Appro.** sont les deux stocks projetés en fin de journée, en gras ; le "
      "**manque cumulé** (besoin non servi depuis le dernier jour avec stock) est indiqué entre parenthèses ; la "
      "**Cible** est le stock cible du jour.\n")

    # ------------------------------------------------------------------ 1. lecture
    w("## 1. Lire le tableau : besoin, stock projeté, stock cible, manque cumulé\n")
    r = run(dataset(), cbn=False)
    ar = r.articles["A1"]
    w("Sans aucune commande (et sans retour à la cible : tolérance mise à 100 % pour cet exemple), le stock de "
      "référence (900) s'épuise de 100 par jour ouvré. Le **Projeté ERP** et le **Projeté Appro.** sont identiques "
      "tant qu'il n'y a ni commande ferme, ni saisie, ni proposition. La **Cible** d'un jour est le besoin des "
      "**5 jours ouvrés suivants** (`coverage_target_days = 5`) : 500 tous les jours, le week-end ne compte pas. "
      "Dès que le stock ne suffit plus, il reste à **0** (le besoin non servi est **perdu**, jamais reporté) et le "
      "manque s'accumule jour après jour (Projeté ERP) ; il repart de zéro au premier jour où le stock redevient "
      "positif. Un besoin non servi déclenche toujours le CBN, même à 100 % de tolérance : le Projeté Appro. "
      "remonte à la cible dès le premier manque (§ 4).\n")
    w(tableau(ar, days=12, rows=["Besoin", "Projeté ERP", "Projeté Appro.", "Cible"]))
    w("")
    w(kpis(ar, ["stock_at_date", "coverage_plan_days", "target_stock", "first_stockout_plan", "max_shortage_plan", "demand_next_20wd", "avg_daily_demand_wd"]))
    w("")
    w("**Lecture.** La couverture (en jours ouvrés) est le nombre de jours ouvrés dont le besoin cumulé tient dans le "
      "stock du jour. La première rupture est le premier jour dont le besoin n'est pas servi. Le *Besoin 20 j "
      "ouvrés* est la somme du besoin des 20 prochains jours ouvrés (l'horizon ferme) ; sa moyenne est par jour "
      "ouvré compté.\n")
    w(alertes(ar))
    w("")

    # ------------------------------------------------------------------ 2. ERP et Appro.
    w("## 2. Commandes ERP, ligne Appro. et commande ignorée\n")
    ds = dataset(orders=[OrderLine("CO-1", "A1", "S1", TODAY + D(days=3), 400, order_type=OrderType.FIRM),
                         OrderLine("CO-2", "A1", "S1", TODAY + D(days=10), 400, order_type=OrderType.FIRM)])
    ar = run(ds, cbn=False).articles["A1"]
    w("Deux commandes fermes ERP de 400 arrivent le jeudi 08/10 et le jeudi 15/10. Sans saisie, la ligne "
      "**Appro.** reprend la commande ferme de chaque jour (cellule vide = ERP) : les deux projetés sont égaux. "
      "Seules les commandes **fermes** comptent ; les prévisionnelles sont affichées, jamais comptées.\n")
    w(tableau(ar, days=10, rows=["Besoin", "Ferme", "Appro.", "Projeté ERP", "Projeté Appro."]))
    w("")
    ds.plan = [PlanCell("A1", "S1", TODAY + D(days=3), 700.0), PlanCell("A1", "S1", TODAY + D(days=10), 0.0)]
    ar2 = run(ds, cbn=False).articles["A1"]
    w("L'approvisionneur **saisit 700** dans la cellule Appro. du 08/10 (il négocie une livraison plus grosse) et "
      "**0** le 15/10 (il annule celle-là). Le **Projeté ERP** ne bouge pas : il reflète l'ERP tel quel. Le "
      "**Projeté Appro.** suit la décision de l'approvisionneur : c'est le scénario sur lequel le moteur calcule "
      "les propositions CBN et les alertes de rupture du plan.\n")
    w(tableau(ar2, days=10, rows=["Besoin", "Ferme", "Appro.", "Projeté ERP", "Projeté Appro."]))
    w("")
    ds.plan = []
    ds.flags = [CellFlag("A1", "S1", TODAY + D(days=10), "order_ignored")]
    ar3 = run(ds, cbn=False).articles["A1"]
    w("**Commande ignorée** : un clic sur la cellule *Ferme* du 15/10 la barre. Elle sort du Projeté ERP **et** "
      "du pré-remplissage de la ligne Appro. (l'approvisionneur sait que cette commande ne viendra pas) ; un second "
      "clic la rétablit.\n")
    w(tableau(ar3, days=10, rows=["Besoin", "Ferme", "Appro.", "Projeté ERP", "Projeté Appro."]))
    w("")
    w("> Dans le tableau, la ligne *Ferme* affiche `restant / commandé` après une réception partielle ; une "
      "commande ferme dont la **date est passée** et dont l'ERP garde un restant ne compte dans aucun stock : "
      "elle est **en retard** (§ 11).\n")

    # ------------------------------------------------------------------ 3. cible
    w("## 3. Couverture cible et jours ouvrés\n")
    w("Le **stock cible** d'un jour est le besoin des `coverage_target_days` jours ouvrés suivants : c'est le "
      "niveau que visent les propositions. Il n'y a ni stock de sécurité ni cycle de commande : la cadence des "
      "livraisons vient des jours de livraison du fournisseur et de ses plafonds.\n")
    ar5 = run(dataset(), cbn=False).articles["A1"]
    ar10 = run(dataset(articles=[article(coverage_target_days=10)]), cbn=False).articles["A1"]
    ar3d = run(dataset(articles=[article(coverage_target_days=3)]), cbn=False).articles["A1"]
    w("| Jour | Cible, couverture 3 j ouvrés | Cible, couverture 5 j ouvrés | Cible, couverture 10 j ouvrés |")
    w("|---|---:|---:|---:|")
    i0 = ar5.dates.index(TODAY)
    for i in range(i0, i0 + 7):
        if ar5.dates[i].weekday() >= 5:
            continue
        w(f"| {jour(ar5.dates[i])} | {q(ar3d.target_stock[i])} | {q(ar5.target_stock[i])} | {q(ar10.target_stock[i])} |")
    w("")
    w("**Lecture.** Le vendredi, la fenêtre de 5 jours ouvrés enjambe le week-end (lundi → vendredi suivants) : la "
      "cible reste 500. Un jour dont le besoin cumulé **égale** exactement le stock est compté couvert.\n")

    # ------------------------------------------------------------------ 4. CBN
    w("## 4. Propositions CBN : tolérance, retour à la cible, MOQ, UM\n")
    ds = dataset()
    ar = run(ds).articles["A1"]
    w("Le moteur parcourt le Projeté Appro. à partir de demain. Une proposition se déclenche quand le stock "
      "projeté passe **sous (100 − tolérance) % de la cible**, soit **85 %** avec la tolérance commune de 15 % "
      "(ou dès qu'un besoin n'est pas servi). La quantité ramène le stock à **100 % de la cible** : "
      "besoin net = cible − stock projeté (+ manque), relevé au **MOQ** (200) puis arrondi au multiple supérieur "
      "de l'**UM** (50). La livraison est placée sur un jour de livraison du fournisseur (le dernier avant le "
      "besoin), la date de commande = livraison − délai en jours ouvrés (3).\n")
    w(tableau(ar, days=15, rows=["Besoin", "Prop. CBN", "Projeté ERP", "Projeté Appro.", "Cible"]))
    w("")
    w(propositions(ar))
    w("")
    w("**Lecture.** Le Projeté Appro. intègre les propositions : il reste au-dessus de 85 % de la cible, tandis que "
      "le Projeté ERP, sans commande, tombe à zéro. Les propositions sont **recalculées à chaque affichage** et "
      "n'existent nulle part ailleurs ; pour en reprendre une, l'approvisionneur tape la quantité dans la cellule "
      "Appro. (la cellule grisée la prérempli).\n")
    ar_t0 = run(ds, target_tolerance_pct=0.0).articles["A1"]
    w("Avec une **tolérance à 0 %**, la moindre descente sous la cible déclenche : les propositions sont plus "
      "fréquentes et plus petites (à l'UM près).\n")
    w(propositions(ar_t0))
    w("")
    ar_lot = run(dataset(links=[link(moq=1000, pack_qty=250)])).articles["A1"]
    w("Avec **MOQ 1 000 et UM 250**, la première proposition saute au MOQ ; la suivante est repoussée d'autant.\n")
    w(propositions(ar_lot))
    w("")

    # ------------------------------------------------------------------ 5. délai, plancher
    w("## 5. Délai fournisseur et plancher dans le délai\n")
    ds = dataset(stock=350.0, suppliers=[supplier(lead=10)])
    ar = run(ds).articles["A1"]
    first = ar.proposals[0]
    w(f"Stock de départ 350 et délai de **10 jours ouvrés** (première livraison tenable : {jour(CAL.add_working_days(TODAY, 10))}). "
      f"Le creux arrive le {jour(first.delivery_date)}, **dans le délai** : la commande aurait dû partir le "
      f"{jour(first.order_date)}. Le moteur applique alors le **plancher** : il propose, **urgente**, la quantité "
      "**minimale qui évite la rupture jusqu'à la veille de la livraison suivante** (lotie MOQ / UM), sans "
      "remonter à la cible. La date « à commander le » est dans le passé, affichée telle quelle, et une alerte "
      "`URGENT_PROPOSAL` est levée. À partir de la **première livraison tenable**, la proposition vise la "
      "**cible pleine**, sans rampe.\n")
    w(tableau(ar, days=14, rows=["Besoin", "Prop. CBN", "Projeté Appro.", "Cible"]))
    w("")
    w(propositions(ar))
    w("")
    w(alertes(ar))
    w("")
    w("**Lecture.** Le plancher est strict : la tolérance de 15 % ne s'applique qu'au déclenchement du retour à "
      "la cible, jamais au plancher. Si l'approvisionneur **refuse** une proposition plancher (clic), le besoin "
      "non servi devient une rupture affichée et alertée, et le CBN reporte le besoin à la livraison suivante.\n")
    ds2 = dataset(stock=350.0, suppliers=[supplier(lead=10)], links=[link(lead_time_days=4)])
    ar_o = run(ds2).articles["A1"]
    w("**Délai spécifique du lien** : le délai est porté par le fournisseur (ici 10) ; un lien peut le remplacer "
      f"(ici **4 jours ouvrés** pour A1 ↔ S1, première livraison tenable {jour(CAL.add_working_days(TODAY, 4))}). "
      "Le plancher ne s'applique plus qu'au creux d'avant.\n")
    w(propositions(ar_o))
    w("")

    # ------------------------------------------------------------------ 6. jours de livraison
    w("## 6. Jours de livraison du fournisseur : mardi / jeudi\n")
    ds = dataset(suppliers=[supplier(days=frozenset({2, 4}))])
    ar_e = run(ds).articles["A1"]
    w("Le fournisseur ne livre que le **mardi et le jeudi** (Référentiel › Fournisseurs, jours de livraison). "
      "Quand le creux tombe un autre jour, la livraison est placée sur le jour de livraison autorisé le plus "
      "proche **avant** le creux (la marchandise est là avant le besoin) ; les besoins d'ici la livraison "
      "suivante sont regroupés sur cette livraison.\n")
    w(propositions(ar_e))
    w("")
    w(tableau(ar_e, days=10, rows=["Besoin", "Prop. CBN", "Projeté Appro.", "Cible"]))
    w("")

    # ------------------------------------------------------------------ 7. jours de consommation
    w("## 7. Jours de consommation de l'article (`demand_days`)\n")
    ar_d3 = run(dataset(articles=[article(demand_days=3)]), cbn=False).articles["A1"]
    w("Le PDP est hebdomadaire ; par défaut son besoin est réparti sur les **5 jours ouvrés** de la semaine. "
      "Un article consommé en début de semaine se paramètre avec **`demand_days = 3`** : le besoin de la semaine "
      "(500) tombe sur **lundi, mardi, mercredi** (167 / 167 / 166), rien jeudi et vendredi. La cible (besoin des 5 "
      "prochains jours ouvrés) et la couverture suivent ce profil : un jeudi, la cible englobe déjà les trois "
      "jours de consommation de la semaine suivante.\n")
    w(tableau(ar_d3, days=10, rows=["Besoin", "Projeté Appro.", "Cible"]))
    w("")
    w("Pour la **semaine en cours**, le reliquat (PDP éclaté − réel déjà déclaré) est réparti sur les jours du "
      "profil encore à venir ; s'ils sont passés, sur les jours ouvrés restants (§ 11). Le profil observé et une "
      "suggestion de `demand_days` sont proposés dans *Paramètres › Données › Adhérence* et dans l'onglet "
      "*Paramétrage* de la fiche article.\n")

    # ------------------------------------------------------------------ 8. plafonds
    w("## 8. Plafonds du fournisseur : par livraison et par semaine\n")
    ds = dataset(stock=350.0, links=[link(max_qty_per_delivery=400)])
    ar_c = run(ds).articles["A1"]
    w("Un lien peut porter un **plafond par livraison** (multiple de l'UM, ≥ MOQ) et un **plafond par semaine** "
      "(≤ plafond par livraison × jours de livraison), comptés **commandes fermes et Appro. compris**. Le plafond "
      "s'applique **après** le lotissement MOQ / UM, en tronquant au multiple d'UM ; l'excédent glisse à la "
      "livraison suivante (ou à la semaine suivante). Ici, stock 350 et **400 maximum par livraison** : la "
      "première proposition ne peut pas remonter à la cible d'un coup, elle est **plafonnée** et le reste suit.\n")
    w(propositions(ar_c))
    w("")
    w(tableau(ar_c, days=10, rows=["Besoin", "Prop. CBN", "Projeté Appro.", "Cible"]))
    w("")
    ds_w = dataset(stock=350.0, suppliers=[supplier(days=frozenset({2, 4}))], links=[link(max_qty_per_delivery=200, max_qty_per_week=400)])
    ar_w = run(ds_w).articles["A1"]
    w("**Plafond insuffisant** : fournisseur mardi / jeudi, 200 maximum par livraison et **400 par semaine**, pour "
      "un besoin de 500 par semaine. Même le plancher dépasse ce que le fournisseur peut livrer : le manque est "
      "**inévitable**, affiché en rouge dans le Projeté Appro. et remonté par l'alerte « plafond fournisseur "
      "atteint ». Les plafonds s'appliquent aussi aux propositions urgentes.\n")
    w(propositions(ar_w))
    w("")
    w(tableau(ar_w, days=10, rows=["Besoin", "Prop. CBN", "Projeté Appro.", "Cible"]))
    w("")
    w(alertes(ar_w))
    w("")

    # ------------------------------------------------------------------ 9. multi-sourcing
    w("## 9. Multi-sourcing : quotas, priorité, plafond reporté, refus d'un jour\n")
    ds = dataset(suppliers=[supplier(), supplier("S2", "Fournisseur B", lead=5)],
                 links=[link(quota_pct=70, priority=1), link(supplier_id="S2", quota_pct=30, priority=2)])
    ar_q = run(ds, horizon_days=42).articles["A1"]
    ds_p = dataset(articles=[article(sourcing_policy="priority")], suppliers=[supplier(), supplier("S2", "Fournisseur B", lead=5)],
                   links=[link(quota_pct=70, priority=1), link(supplier_id="S2", quota_pct=30, priority=2)])
    ar_p = run(ds_p, horizon_days=42).articles["A1"]
    w("Deux fournisseurs : S1 (quota 70 %, priorité 1, délai 3) et S2 (quota 30 %, priorité 2, délai 5). Avec la "
      "politique **quotas** (défaut, par article), **chaque besoin est partagé** entre les fournisseurs au prorata "
      "des quotas : chaque part est arrondie au MOQ et à l'UM de son fournisseur et livrée à son propre jour "
      "(délai, jours de livraison) ; avec **priorité**, tout va au fournisseur de priorité 1. Chaque fournisseur a "
      "sa propre voie dans le tableau (lignes Ferme / Reçu / Appro. / Prop. CBN).\n")
    w("Quotas 70 / 30 (six semaines) :\n")
    w(propositions(ar_q))
    tot = {sid: sum(p.qty for p in ar_q.proposals if p.supplier_id == sid) for sid in ("S1", "S2")}
    w("")
    w(f"Parts obtenues sur six semaines : S1 {q(tot['S1'])}, S2 {q(tot['S2'])} ({100 * tot['S1'] / (tot['S1'] + tot['S2']):.0f} % / "
      f"{100 * tot['S2'] / (tot['S1'] + tot['S2']):.0f} %) : l'écart avec 70 / 30 vient des MOQ et des UM de chaque "
      "fournisseur.\n")
    w("Priorité :\n")
    w(propositions(ar_p))
    w("")
    w(tableau(ar_q, days=10, rows=["Besoin", "Prop. CBN", "Projeté Appro."], lanes=True))
    w("")
    ds_cap = dataset(stock=350.0, suppliers=[supplier(), supplier("S2", "Fournisseur B", lead=3)],
                     links=[link(quota_pct=70, priority=1, max_qty_per_delivery=200), link(supplier_id="S2", quota_pct=30, priority=2)])
    ar_cap = run(ds_cap).articles["A1"]
    w("**Plafond et quotas** : S1 est plafonné à 200 par livraison. La part de S1 qui dépasse son plafond est "
      "**reportée sur S2** (l'autre fournisseur à quota) le même jour, avant de glisser à la livraison suivante.\n")
    w(propositions(ar_cap))
    w("")
    first = ar_q.proposals[0]
    ds.flags = [CellFlag("A1", first.supplier_id, first.delivery_date, "proposal_refused", qty=first.qty)]
    ar_ref = run(ds, horizon_days=42).articles["A1"]
    w(f"**Refus** : un clic sur la proposition du {jour(first.delivery_date)} ({first.supplier_id}) la barre et "
      f"interdit toute proposition à **ce fournisseur ce jour-là seulement** (un fournisseur mardi / jeudi refusé "
      "le mardi reste possible le jeudi) ; sa part passe à l'autre fournisseur à quota. Un mono-source refusé "
      "glisse au jour de livraison ouvert suivant, le manque intermédiaire est affiché et alerté. La proposition "
      "refusée reste listée dans le flux *À commander* du cockpit, marquée « ignorée », si elle était urgente.\n")
    w(propositions(ar_ref))
    w("")

    # ------------------------------------------------------------------ 10. alertes
    w("## 10. Alertes : seuils rouge et orange, surstock, gradation des ruptures\n")
    w("Les alertes de **couverture** regardent l'épuisement des flux ERP (stock + commandes fermes) : "
      "`LOW_COVERAGE` critique si les commandes ERP sont épuisées sous `alert_red_days` jours ouvrés, avertissement "
      "sous `alert_yellow_days`, `OVERSTOCK` (info) si le stock à date couvre `overstock_days` jours ouvrés ou plus. "
      "Les **ruptures** (`STOCKOUT`) sont graduées sur les deux scénarios : critique dans le délai fournisseur, "
      "avertissement dans l'**horizon ferme (20 jours ouvrés, commun à tous les articles)**, information au-delà. "
      "`CAP_REACHED` signale un manque inévitable sous les plafonds du fournisseur (§ 8).\n")
    cases = [("Stock 150 (rupture dès demain)", dataset(stock=150.0)),
             ("Stock 450 (rupture vendredi)", dataset(stock=450.0)),
             ("Stock 8 000 (80 jours ouvrés de besoin)", dataset(stock=8000.0)),
             ("Stock 3 000, ferme ERP 500 le 12/10 : rupture ERP à J + 32 jours ouvrés", dataset(stock=3000.0, orders=[OrderLine("CO-1", "A1", "S1", TODAY + D(days=7), 500, order_type=OrderType.FIRM)]))]
    for title, d in cases:
        ar = run(d, cbn=False, horizon_days=60).articles["A1"]
        w(f"**{title}**\n")
        w(alertes(ar))
        w("")
    ds_md = dataset(stock=900.0, links=[])
    w("**Données manquantes** (pas de fournisseur lié) :\n")
    w(alertes(run(ds_md, cbn=False).articles["A1"]))
    w("")

    # ------------------------------------------------------------------ 11. réel, semaine en cours, retard
    w("## 11. Consommation réelle, semaine en cours, réceptions et commandes en retard\n")
    ds = dataset(stock=900.0,
                 consumption=[ConsumptionLine("A1", TODAY - D(days=7), 120), ConsumptionLine("A1", TODAY - D(days=6), 80),
                              ConsumptionLine("A1", TODAY - D(days=5), 100), ConsumptionLine("A1", TODAY - D(days=4), 90),
                              ConsumptionLine("A1", TODAY - D(days=3), 110)],
                 orders=[OrderLine("CO-0", "A1", "S1", TODAY - D(days=5), 300, qty_open=100, order_type=OrderType.FIRM),
                         OrderLine("CO-1", "A1", "S1", TODAY + D(days=2), 300, order_type=OrderType.FIRM)],
                 receipts=[Receipt("R-1", "A1", TODAY - D(days=5), 200, supplier_id="S1", packing_slip="BL-1")],
                 pdp=[PdpLine("P1", TODAY + D(weeks=k), 250.0) for k in range(-1, 8)])
    ds.stock = [StockSnapshot("A1", TODAY - D(days=8), 900.0)]
    as_of_wed = TODAY + D(days=2)
    ds.consumption += [ConsumptionLine("A1", TODAY, 150), ConsumptionLine("A1", TODAY + D(days=1), 50)]
    ar = run(ds, cbn=False, as_of=as_of_wed, history_weeks=1).articles["A1"]
    w(f"Ici aujourd'hui est le **{jour(as_of_wed)}** et le stock a été initialisé le {jour(TODAY - D(days=8))}. "
      "Le passé n'est jamais reconstruit : le besoin des jours passés est la **consommation réelle** déclarée "
      "(`conso_composants`, déjà éclatée par composant ; source de consommation « ERP », défaut) ; un jour sans "
      "déclaration compte 0. La **semaine en cours** consomme son PDP éclaté (500) moins le réel déjà déclaré "
      "(150 + 50 = 200) : le reliquat (300) est réparti sur les jours ouvrés restants, aujourd'hui compris. La "
      "commande ferme du 30/09 (300) n'a été reçue qu'à 200 et l'ERP garde un **restant de 100** : c'est une "
      "commande **en retard**, affichée mais comptée dans aucun stock (le restant ERP se met à jour à la "
      "validation du journal de réception).\n")
    w(tableau(ar, days=10, start=TODAY - D(days=7), rows=["Besoin", "Ferme", "Ferme (passé)", "Reçu", "Appro.", "Projeté ERP", "Projeté Appro."]))
    w("")
    w(kpis(ar, ["stock_reference", "stock_at_date", "backlog_qty", "coverage_plan_days"]))
    w("")
    w("**Lecture.** *Stock à date* = stock projeté à la fin de la veille (réceptions et consommations réelles "
      "comprises). *En retard* = Σ du restant ERP des commandes fermes dont la date de livraison est passée, sans "
      "fenêtre ; une commande ignorée sort de la projection mais reste en retard.\n")
    w("**Sources de consommation.** Un article sans consommation déclarée fiable se met en source **PDP** : son "
      "passé est le PDP éclaté, comme le futur. Un article dont la consommation se déduit d'autres articles se met "
      "en source **dérivée** : conso = Σ coefficient × consommation déclarée des articles sources (sources en mode "
      "ERP, présentes dans un programme de l'article et dans aucun programme où il est absent ; table *Termes de "
      "consommation*).\n")

    # ------------------------------------------------------------------ 12. ajustements
    w("## 12. Ajustements et correction du stock de référence\n")
    ds = dataset(adjustments=[AdjustCell("A1", INIT, -150, "inventaire : écart constaté"), AdjustCell("A1", TODAY + D(days=3), -200, "rebut prévu")])
    ar = run(ds, cbn=False).articles["A1"]
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
    a_w.weekly = {"2026-W42": {"coverage_target_days": 10}}
    ar = run(dataset(articles=[a_w]), cbn=False, horizon_days=21).articles["A1"]
    w("Les paramètres de l'article (couverture cible, seuils rouge et orange, surstock) peuvent être surchargés "
      "**par semaine ISO** (fiche article › Paramétrage › Par semaine, ou Référentiel › Articles). Ici la "
      "couverture cible passe de 5 à **10 jours ouvrés pour la semaine 42** seulement : la cible double cette "
      "semaine-là, puis revient à 5 jours.\n")
    w(tableau(ar, days=15, rows=["Besoin", "Cible"]))
    w("")

    # ------------------------------------------------------------------ 14. fenêtre
    w("## 14. Fenêtre de calcul, affichage, cache\n")
    w("- **Point zéro** = date du stock de référence (`fct_stock.snapshot_date`), une seule pour tous les "
      "articles, jamais après aujourd'hui. Rien n'existe avant : pas d'historique reconstruit.\n"
      "- **Fenêtre** = du point zéro à aujourd'hui + **52 semaines** (horizon fixe, tableau et PDP compris), "
      "étendue au dimanche de la semaine en cours ; le tableau commence au lundi de la semaine en cours moins "
      "les *semaines passées* choisies par l'utilisateur (2 par défaut).\n"
      "- **Calendrier** « Par défaut » : un jour par colonne pour la semaine en cours et les *semaines "
      "détaillées* choisies par l'utilisateur (2), une semaine par colonne au-delà ; une colonne semaine se "
      "saisit comme une colonne jour, la valeur se place au lundi de la semaine (au jour de référence dans la "
      "semaine en cours) ; cliquer l'en-tête pour le détail par jour.\n"
      "- Le **sélecteur de période** sous la barre d'outils ne change que les colonnes affichées.\n"
      "- **Calendrier usine** : jours ouvrés et fermetures (configuration de l'application, visibles dans "
      "*Paramètres › Données*) ; aucun besoin, aucune livraison et aucun jour compté sur une fermeture.\n"
      "- Le **taux de rebut d'un programme** (colonne *Taux rebut (%)* du Plan de production) majore le PDP de la "
      "semaine avant l'éclatement : besoin = PDP × (1 + taux / 100) ; daté, il ne touche que la semaine en cours "
      "et les suivantes.\n"
      "- Les articles **décimaux** (unité KG, M… ou nomenclature décimale) affichent le besoin et les stocks avec "
      "trois décimales ; les autres en entiers (affichage seulement, les calculs sont exacts).\n"
      "- Tout est recalculé à chaque demande et mis en cache jusqu'à la prochaine écriture (saisie, import, "
      "paramètre) ou synchronisation ERP.\n")

    # ------------------------------------------------------------------ 15. récapitulatif
    w("## 15. Récapitulatif des paramètres\n")
    w("| Paramètre | Défaut | Portée | Effet |")
    w("|---|---|---|---|")
    recap = [
        ("coverage_target_days", "5 (article)", "article, semaine", "jours ouvrés de besoin à tenir en stock : fixe la cible, donc le déclenchement et la taille des propositions"),
        ("alert_red_days / alert_yellow_days", "2 / 5 (article)", "article, semaine", "seuils des alertes de couverture (épuisement des commandes ERP), jours ouvrés"),
        ("overstock_days", "30 (article)", "article, semaine", "seuil de l'alerte surstock, jours ouvrés"),
        ("demand_days", "5 (article)", "article", "jours ouvrés de la semaine portant le besoin hebdomadaire (1 = lundi seul … 5 = toute la semaine)"),
        ("sourcing_policy", "quota (article)", "article", "quotas (chaque besoin partagé) ou priorité"),
        ("consumption_source", "erp (article)", "article", "besoin passé : consommation ERP, PDP éclaté, ou dérivée d'autres articles (termes × coefficient)"),
        ("lead_time_days", "5 (fournisseur)", "fournisseur", "délai en jours ouvrés, inclusif (lundi + 5 = lundi suivant) ; date de commande, urgence, plancher"),
        ("delivery_weekdays", "1-5 (fournisseur)", "fournisseur", "jours de livraison autorisés"),
        ("moq / pack_qty", "lien article ↔ fournisseur", "lien", "quantité minimale, arrondi UM"),
        ("lead_time_override", "vide (lien)", "lien", "délai spécifique au lien, remplace celui du fournisseur"),
        ("quota_pct / priority", "lien", "lien", "répartition multi-sourcing"),
        ("max_qty_per_delivery / max_qty_per_week", "vide (lien)", "lien", "plafonds, multiples de l'UM ; fermes et Appro. comptés ; excédent reporté, manque inévitable alerté"),
        ("target_tolerance_pct", "15", "commune", "retour à la cible dès que le stock projeté passe sous (100 − n) % de la cible ; la quantité vise 100 %"),
        ("Règles figées", "—", "application", "jours ouvrés partout ; besoin non servi perdu ; plancher dans le délai ; cible pleine ensuite ; livraison avant le besoin, regroupée ; horizon ferme 20 j ouvrés ; horizon 52 semaines ; fermes seules comptées ; refus = jour seul"),
        ("history_weeks / focus_weeks", "2 / 2 (utilisateur)", "affichage", "semaines passées montrées, semaines détaillées jour par jour"),
    ]
    for name, default, scope, effet in recap:
        w(f"| `{name}` | {default} | {scope} | {effet} |")
    w("")
    w("La règle commune se règle dans *Paramètres › Règles communes* (managers, administrateurs) ; les "
      "paramètres de l'article dans sa fiche (onglet *Paramétrage*, avec les surcharges hebdomadaires et les "
      "liens fournisseurs) ou dans le *Référentiel* ; ceux du fournisseur dans le *Référentiel* et sa fiche.\n")
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
