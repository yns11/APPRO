# Analyse de l'outil actuel : classeur `SIMULATION_<appro>.xlsx`

Ce document décrit le fonctionnement du classeur Excel utilisé aujourd'hui par chaque
approvisionneur (exemple analysé : `SIMULATION_QUENTIN.xlsx`), ses entrées / sorties, sa logique de
calcul et ses points faibles. Il justifie les choix de conception de l'application (voir
`docs/regles_metier.md` et `docs/architecture.md`).

## 1. Structure du classeur

| Onglet | Table Excel | Contenu | Rôle |
|---|---|---|---|
| `BASE ARTICLE` | `BASE_ARTICLE` (A1:J21) | REF, NOM, COFOR, SUPPLIER, MOQ, APPRO, Couverture (jour), DHRQ, Taux de service ?, Contact | Référentiel article **et** lien article–fournisseur (une ligne par couple ; 16 articles, 20 couples, 4 articles en double source) |
| `BOM` | `BOM` (A1:AGQ29) | PF/SF, REF PF/SF (`mass-…`), REF, Quantité, Unité + **870 colonnes J400…J1269** | Nomenclature à 1 niveau (28 lignes) et calcul du **besoin journalier** = production effective du programme × quantité |
| `SOP - PDP` | `WEEKLY` (A1:DV26) | PF/SF puis une colonne par semaine (`S11-26` … `2028W30`) | Plan de production hebdomadaire (PDP) par programme, 125 semaines |
| `PREVU - ENGAGÉ` | `DAILY` (D3:AGR32) | Clé, PREVU / ENGAGÉ, PF/SF + 870 colonnes jour | Lissage quotidien du PDP (`1. PREVU`), production réelle saisie (`2. ENGAGÉ`), production effective (`3. EFFECTIF`) |
| `SIMULATION` | `SIMU` (A6:AGU128) | Clé, PERIMETRE, REF, DESIGNATION, COFOR, SUPPLIER, MOQ, DHRQ, VARIABLE + 870 colonnes jour | Simulation par article : 7 variables (besoin, commande, réception, ajustement, stock, stock cible, couverture) |

Grille temporelle : **870 jours calendaires figés** du 14/03/2026 (`J400`) au 30/07/2028 (`J1269`).

## 2. Chaîne de calcul (formules)

1. **PDP → production journalière prévue** (`DAILY`, `1. PREVU`) :
   `ROUND(WEEKLY[semaine] / 5, 0)` posé sur les jours **lundi → vendredi** de la semaine ISO
   (semaine `S12-26` = ISO 2026‑W12, lundi 16/03/2026). Les week-ends restent vides.
2. **Production effective** (`3. EFFECTIF`) : `ENGAGÉ` si la cellule est renseignée (y compris `0`),
   sinon `PREVU`. L'engagé est saisi à la main (parfois sous forme `=25+63`).
3. **Besoin composant** (`BOM[Jxxx]`) : `XLOOKUP(PF & "3. EFFECTIF") × Quantité` par ligne de nomenclature ;
   `SIMU` fait ensuite `SUMIFS(BOM[Jxxx], BOM_REF, REF)` → `1. Besoin`.
4. **Stock projeté** (`5. Stock`) : `Stock(J-1) + SI(Réception vide ; Commande ; Réception) + Ajustement − Besoin`.
   Pour les articles en double source, la formule est dupliquée par fournisseur
   (`2. Commande (S-000032)`, `3. Réception (S-001033)`, …). Le stock initial est une constante en `J400`.
5. **Stock cible** (`6. Target stock`, calculé un jour sur sept) : somme des besoins des
   `Couverture (jour)` prochains jours calendaires.
6. **Couverture** (`7. Couverture`, un jour sur sept) : nombre de jours futurs dont le besoin cumulé reste
   strictement inférieur au stock du jour (`XMATCH(TRUE, Cumul >= stock) − 1`), plafonné par la grille.
7. **Mises en forme conditionnelles** (36 règles) : stock < 0 en rouge ; couverture comparée à des seuils
   `AROUGE` / `AJAUNE` / `ASURSTOCK` par `PERIMETRE` … dont les **plages nommées sont cassées (`#REF!`)**.

## 3. Entrées et sorties

Entrées manuelles (copiées depuis l'ERP ou saisies) : référentiel, nomenclature, PDP (collé chaque semaine),
production réelle (`ENGAGÉ`), stock initial, commandes (`2. Commande`, une cellule par date), réceptions
(`3. Réception`), ajustements d'inventaire hebdomadaires (`4. Ajustement`, le dimanche).

Sorties : stock projeté, stock cible, couverture, code couleur. Aucune proposition de commande : l'appro
saisit lui-même ses commandes en essayant de maintenir la couverture.

## 4. Points faibles constatés

| # | Constat | Conséquence |
|---|---|---|
| 1 | Grille de 870 colonnes figées, formules matricielles (`LET`, `FILTER`, `SCAN`) recalculées sur toute la grille | Classeur lent (1,5 Mo), horizon non glissant, à reconstruire chaque année |
| 2 | Règle « la réception remplace la commande **le même jour** » | Une réception livrée à une autre date que prévu est **comptée deux fois** (commande + réception) sauf si l'appro saisit un `0` en réception le jour prévu : source d'erreurs de stock |
| 3 | Commandes passées sans réception comptées comme livrées | Écart silencieux entre stock projeté et stock réel, corrigé par des ajustements hebdomadaires |
| 4 | Seuils d'alerte cassés (`AJAUNE`, `AROUGE`, `ASURSTOCK`, `ALERTEPROG` = `#REF!`) | Les couleurs de couverture ne fonctionnent plus ; seule la rupture (stock < 0) reste visible |
| 5 | Couverture et stock cible calculés un jour sur sept, en jours calendaires | Une rupture entre deux points hebdomadaires n'est signalée que par le stock négatif |
| 6 | MOQ purement informatif, délai fournisseur, conditionnement (PLA) et quotas absents | Aucune proposition de commande, aucune vérification du respect du délai |
| 7 | Double source gérée par duplication de lignes avec le COFOR dans le libellé | Clés texte fragiles (`REF & "2. Commande (S-000032)"`), impossible à généraliser |
| 8 | Étiquettes de semaine hétérogènes (`S11-26` puis `2028W24`), noms de programmes en texte libre | Rapprochements par texte exact ; une faute de frappe casse le besoin |
| 9 | PDP lissé par `ROUND(x/5)` sans conservation du total, week-ends et jours fériés ignorés | Les besoins hebdomadaires diffèrent du PDP, pas de calendrier usine |
| 10 | Saisies libres (`=304+452`, `=-25733+18850`), pas d'auteur, pas d'historique, un fichier par appro | Aucune traçabilité, aucune consolidation, données ERP recopiées à la main |
| 11 | Stock initial figé en `J400`, pas de date de référence explicite | Le « aujourd'hui » n'existe pas dans le modèle ; historique et futur sont confondus |

## 5. Ce que l'application conserve et améliore

* **Conservée** : la logique métier lisible (besoin → commandes / réceptions → stock → cible → couverture),
  les notions de stock ferme / simulé, de couverture cible par article et de production réelle prioritaire
  sur le plan. Le moteur reproduit exactement les valeurs du classeur en mode « legacy »
  (test `backend/tests/test_legacy_regression.py` : besoin, stock, couverture et stock cible identiques
  sur 120 jours pour les 16 articles).
* **Améliorée** : horizon glissant paramétrable, calendrier ouvré, lissage à somme exacte, commandes
  soldées par les réceptions (plus de double compte), retards fournisseurs détectés, seuils d'alerte
  paramétrables et fonctionnels, propositions de commandes (MOQ, PLA, délai, jours de livraison, quotas),
  scénarios de simulation, traçabilité (qui a saisi quoi), données ERP lues dans Unity Catalog,
  export / import Excel compatibles.
