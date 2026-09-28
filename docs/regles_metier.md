# Règles métier du moteur MRP / approvisionnement

Toutes les règles ci-dessous sont implémentées dans `backend/appro/engine` et **paramétrables** :
globalement (`EngineParams`, page *Paramètres & règles*), par article (`ref_articles` + surcharges) ou
par lien article‑fournisseur (`ref_article_suppliers` + surcharges). Les valeurs par défaut sont indiquées
entre crochets. Les variantes possibles sont listées pour chaque règle.

## 1. Temps et calendrier

* **Date de référence (`as_of`)** : « aujourd'hui » du calcul. Par défaut : lendemain du dernier snapshot
  de stock (mode local) ou date du jour (production). Le stock est connu **en fin de journée** du snapshot ;
  la projection commence le lendemain.
* **Horizon glissant** : `history_days` [14] jours avant `as_of` et `horizon_days` [120] jours après
  (7 → 730). Rien n'est figé : chaque calcul reconstruit la fenêtre.
* **Calendrier ouvré** : `working_weekdays` [1‑5] + jours fériés / fermetures (`APPRO_HOLIDAYS`).
  Les délais fournisseurs sont en **jours ouvrés** ; les jours de livraison autorisés sont par fournisseur
  (`delivery_weekdays`).

## 2. Besoins (demande dépendante)

1. **Lissage du PDP hebdomadaire** sur les jours ouverts de la semaine ISO (`spread_rounding`) :
   * `exact` [défaut] : quantités entières dont la somme égale le PDP (méthode du plus fort reste) ;
   * `none` : quotient réel `qté / n jours` ;
   * `per_day` : `ROUND(qté / n)` par jour (comportement du classeur, somme non conservée).
   Une semaine sans jour ouvert (fermeture) est signalée et ignorée.
2. **Production effective** (`production_mode`) :
   * `actual_then_remainder` [défaut] : **jours passés** = production réelle déclarée (un 0 déclaré est
     respecté ; jour passé sans déclaration = 0, `missing_actual_policy` [`zero`], ou `plan`) ; **semaine en
     cours** = reliquat du PDP, `max(PDP de la semaine − réel déjà déclaré, 0)`, réparti sur les jours ouvrés
     restants, jour de référence compris ; **semaines suivantes** = PDP réparti. Exemple : PDP 2 000, mercredi,
     500 lundi et 600 mardi → 300 par jour mercredi, jeudi, vendredi ; PDP déjà dépassé → 0, et une
     consommation déclarée le mercredi est bien décomptée le jeudi puisqu'elle est devenue passé ;
   * `actual_then_plan` : le réel remplace le plan jour par jour ; `plan_only` / `actual_only`.
   Le réel saisi dans l'application prime sur le réel ERP pour le même jour. Le tableau sépare le
   **Consommé** (passé, réel) du **Requis** (futur, PDP).
3. **Éclatement nomenclature (1 niveau)** : besoin composant = Σ production effective × `qty_per`
   × (1 + `scrap_pct` %) sur les lignes valides (`valid_from` / `valid_to`) ; `consumption_offset_days` [0]
   décale la consommation (ex. −1 : composants consommés la veille).
4. Un PDP importé et **actif** remplace le plan ERP pour les programmes qu'il contient.

## 3. Approvisionnements et scenarios de stock

Deux stocks sont projetés : le **Scenario ERP** (les commandes fermes ERP telles quelles) et le **Scenario
Plan** (le plan de livraison de l'approvisionneur). Le second est la vue opérationnelle ; l'écart entre les
deux est l'effet des décisions de l'approvisionneur.

| Jour calculé | Scenario ERP | Scenario Plan |
|---|---|---|
| Avant la référence | historique reconstitué (§ 4) | idem |
| À partir de la référence | R + Ferme + A − besoin | R + Plan + CBN + A − besoin |

R = réceptions du jour (réelles, jour de référence compris), Ferme = commandes fermes ERP ouvertes du jour,
Plan = plan de livraison, CBN = complément calculé, A = ajustements.

### 3.1 La commande = un créneau de livraison

L'ERP ne fournit pas d'identifiant stable de commande. L'extraction (`commandes_edi`, voir
`docs/dictionnaire_donnees.md`) construit un identifiant synthétique **fournisseur | article | date | ferme**
(ex. `S-000025|P-00204943|20261006|1`) : une « commande » de l'application est tout ce qu'un fournisseur doit
livrer un jour donné pour un article, ferme (`Ordre_ferme = Oui`) ou prévisionnelle. L'identifiant est stable
puisque l'ERP ne déplace ni n'annule jamais une commande ; il ne change qu'à l'affermissement. La quantité
ouverte est le restant ERP ; un restant nul vaut réception.

### 3.2 Le plan de livraison

Le plan est **la seule chose que l'approvisionneur saisit** pour les livraisons :

* au départ, chaque commande ferme ERP ouverte et non passée est une ligne du plan à sa date et pour son
  restant (reprise automatique, rien n'est stocké) ;
* l'application n'enregistre une **ligne** (`app_plan_lines`) que lorsqu'il s'écarte de l'ERP : date ou
  quantité modifiée, commande coupée en plusieurs lignes (même référence), quantité 0 (rien attendu de cette
  commande), ou ligne **libre** sans commande (dépannage, livraison hors commande, complément CBN accepté) ;
* une commande prévisionnelle ERP n'est qu'une information, sauf si une ligne la reprend dans le plan ;
* une ligne dont la date est passée **expire** : elle ne compte plus (sa réception, si elle a eu lieu, est
  dans R) et reste lisible en grisé ;
* quand l'ERP passe le restant d'une commande à 0, ses lignes sont sans effet.

Saisie : dans la cellule de la ligne *Plan* du tableau (quantité ou expression : une cellule vide crée une ligne
libre, une cellule avec une ligne modifie sa quantité, 0 = rien attendu, vider = retour à l'ERP), par la liste
du jour (clic) ou par le tiroir *Plan* de la fiche (dates, tranches, lignes libres, prévisionnel repris, CBN
accepté). Le complément CBN apparaît en italique dans la ligne *Plan* tant qu'il n'est pas accepté.

### 3.3 Commandes non reçues (backlog)

La source garde les lignes fermes depuis le début de l'année et le restant d'une ligne passée n'est pas
fiable (réceptions manuelles non lettrées). Une commande ERP dont la date est passée et qui reste ouverte
**ne compte dans aucun scenario** : elle est listée « non reçue » (page dédiée, KPI *Backlog* de la fiche,
alerte `LATE_ORDER` agrégée). L'approvisionneur la **date** dans le plan si elle arrive encore (Scenario
Plan) ou la **clôture** (ligne à 0). Les lignes libres passées n'entrent pas dans cette liste : rien ne
permettrait de dire si elles ont été reçues.

### 3.4 Jour de référence : lettrage des réceptions

Le stock de référence est celui de la **veille au soir** ; les réceptions du jour sont comptées à part. Rien
ne relie une réception à une commande : la quantité encore attendue d'une commande datée du jour est
`min(restant, max(0, commandé − réceptions du jour))`, les réceptions étant affectées aux commandes du même
fournisseur (puis à celles sans fournisseur), fermes d'abord. Appliqué séparément au Scenario ERP et aux
lignes du plan du jour.

### 3.5 Ajustements et correction du stock de référence

Seules les commandes et les réceptions sont lues de l'ERP : les ajustements sont des **saisies** (cellule
de la ligne *Ajustement*, toute colonne, ou formulaire), signées, à toute date. Daté du jour de référence ou
avant, un ajustement **corrige le stock de référence** (le stock ERP est souvent faux et l'application ne
l'écrit jamais) et persiste jusqu'à sa suppression ; il est rappelé dans le KPI *Stock de référence*. Daté
après, c'est un mouvement prévu (casse, transfert) compté à sa date. Les ajustements comptent dans les deux
scenarios.

| Élément | Scenario ERP | Scenario Plan | Règle |
|---|---|---|---|
| Commande ERP ferme | ✔ | ✔ sauf ligne de plan | restant ERP ; passée non reçue : hors stocks, backlog |
| Commande ERP prévisionnelle | information | ✔ si reprise par une ligne | `forecast_date_policy` : date réelle [défaut] ou lundi |
| Commande saisie hors ERP | ✔ | ✔ | commande réelle ; date ≥ référence sauf « ancienne encore due » |
| **Ligne du plan** | – | ✔ | remplace la commande référencée ou s'ajoute (libre) |
| Complément CBN | – | ✔ (option) | `include_proposals_in_plan` [oui] |
| Réception | ✔ | ✔ | fait constaté, date ≤ référence ; lettrée le jour de référence |
| Ajustement | ✔ | ✔ | saisie signée, toute date |

**Affichage du passé** : les colonnes antérieures à la référence montrent les commandes fermes passées
(quantité commandée, rouge si non reçue), les lignes de plan expirées en grisé, les réceptions, les
ajustements et le consommé ; rien de tout cela n'entre dans un calcul, le stock passé étant reconstitué (§ 4).

**Calendrier d'affichage** (`focus_weeks` [2]) : le mode *Par défaut* détaille jour par jour la semaine en
cours et les `focus_weeks` semaines suivantes, et agrège en semaines ISO le passé et le futur au-delà ; les
modes *Jour* et *Semaine* sont uniformes. Une semaine agrégée affiche la somme des flux et la valeur de fin de
semaine des stocks.

## 4. Projection de stock et besoin non servi

`x[j] = stock[j−1] + approvisionnements[j] + ajustements[j] − besoin[j]`, à partir du stock de référence
(`qty_on_hand − qty_blocked` du snapshot, corrigé des ajustements passés), pour chacun des deux scenarios.

**Historique reconstitué** : avant le snapshot, le stock est remonté à rebours à partir du stock de référence
avec les seuls mouvements connus, `stock[j−1] = stock[j] − réceptions[j] − ajustements[j] + consommé[j]`.
C'est une reconstitution, pas le stock ERP de ces jours-là : la consommation est calculée (réel × nomenclature)
et les mouvements ERP autres que les réceptions ne sont pas lus. Les colonnes passées l'affichent en gris,
sans couverture.

Un stock physique n'est **jamais négatif** : l'application affiche le stock physique et, séparément, le
**manque** (besoin non servi). Le sort de ce besoin non servi est un paramètre, `shortage_policy` :

* `backlog` [défaut, logique MRP « projected available balance »] : le besoin non servi est **reporté** ;
  le solde net `x` devient négatif, les réceptions suivantes servent d'abord ce retard. Stock affiché
  `= max(x, 0)`, manque `= max(−x, 0)` (retard cumulé). C'est le comportement à retenir quand la
  production non faite est **décalée** (le composant sera consommé plus tard) ;
* `lost` : le besoin non servi est **perdu** ; `stock[j] = max(x, 0)` et le manque du jour est la
  quantité non servie ce jour-là. À retenir quand la production non faite est **abandonnée** (le besoin ne
  se reporte pas). Le classeur exporté applique la même règle (cellule *Politique de manque*).

Les propositions, couvertures et alertes utilisent le solde net de la couche (`backlog`) ou le stock borné
(`lost`) ; dans les deux cas la **date de rupture** est le premier jour où un manque apparaît et la
quantité affichée est le manque maximal.

## 5. Couverture et stock cible

* **Couverture (jours)** sur chaque jour : nombre de jours futurs dont le besoin cumulé est couvert par le
  stock du jour. `coverage_unit` : `calendar` [défaut, comme le classeur] ou `working` ;
  `coverage_tie_rule` : `covered` [défaut] ou `not_covered` (classeur). Manque (solde négatif) → 0.
* **Stock cible** (`target_policy`) : `coverage_days` = besoin des `coverage_target_days` prochains jours ;
  `safety_qty` = stock de sécurité fixe ; `max` [défaut] = le plus grand des deux.

## 6. Alertes

| Type | Sévérité | Règle |
|---|---|---|
| `STOCKOUT` (plan) | critique | premier manque sur le Scenario Plan malgré le plan de livraison et le complément CBN (`stockout_lookahead_days`) |
| `STOCKOUT` (ERP) | critique si ≤ délai fournisseur, avertissement si ≤ `firm_horizon_days` [28], info au‑delà | premier manque sur le Scenario ERP : livraison à planifier ou commande à passer |
| `LOW_COVERAGE` | critique si épuisement des commandes ERP ≤ `alert_red_days` [3], avertissement si ≤ `alert_yellow_days` [= couverture cible] | basé sur l'épuisement du Scenario ERP, pas seulement sur le stock de référence |
| `OVERSTOCK` | info | couverture du stock à date ≥ `overstock_days` [max(30 ; 3 × cible)] |
| `NEGATIVE_STOCK` | critique | stock de départ négatif dans l'ERP (inventaire / saisies à vérifier) |
| `LATE_ORDER` | avertissement (une par article) | commandes ERP passées **non reçues** (backlog, hors stocks) : à dater dans le plan ou à clôturer |
| `URGENT_PROPOSAL` | critique | complément CBN dont la date de commande théorique est déjà passée |
| `NO_DEMAND` | info | aucun besoin sur l'horizon alors que du stock existe |
| `MISSING_DATA` | avertissement / info | pas de snapshot, pas de fournisseur, pas de nomenclature |

La sévérité d'un article est la pire de ses alertes.

## 7. Complément CBN (calcul des besoins nets)

Le complément CBN est recalculé **automatiquement** à chaque calcul (`generate_proposals` [oui]) sur le
Scenario Plan, c'est-à-dire après le plan de livraison : il ne propose que ce que ni l'ERP ni le plan ne
couvrent. Il apparaît en italique dans la ligne *Plan* du tableau, dans le tiroir du plan (bouton *Accepter*,
qui le transforme en ligne libre) et dans la page *Complément CBN*. `shortfall_tolerance_days` [0] : un
passage sous la cible qui se résorbe seul en n jours ouvrés, sans besoin non servi, ne déclenche pas de
proposition (creux d'un jour accepté). `proposal_placement` [`working_days`] : livraison proposée n'importe
quel jour ouvré autorisé par le fournisseur, ou `monday` : livraisons regroupées le lundi.

Algorithme, par article, sur le Scenario Plan (solde net avant propositions) :

1. À partir de `as_of + 1 + frozen_days` [0] : dès que `stock[d] < cible[d]` → **point de commande atteint** (sauf creux toléré).
2. **Niveau de recomplètement** = cible + besoin du prochain cycle de commande (`lot_policy` :
   `coverage` [défaut] avec `order_cycle_days` [7], `poq`, `fixed_lot` = multiple de `fixed_lot_qty`).
3. **Quantité** = max(besoin net, `moq`) arrondie au multiple supérieur de `pack_qty` (PLA).
4. **Fournisseur** (`sourcing_policy`) : `quota` [défaut] = répartition déterministe au plus près des quotas
   (`quota_pct`), sinon `priority`.
5. **Date de livraison** : jour ouvré **et** jour de livraison autorisé du fournisseur, décalé
   (`delivery_shift` : `earlier` [défaut] ou `later`) ; jamais avant la période gelée. Si la livraison
   doit être repoussée après le jour du besoin, le besoin est réévalué au jour de livraison et la pénurie
   intermédiaire reste visible (alerte rupture).
6. **Date de commande** = livraison − `lead_time_days` ouvrés. Si elle est antérieure à `as_of`, la
   proposition est **urgente** (`respect_lead_time` [non] : `oui` interdit toute livraison avant
   `as_of + délai` et laisse apparaître la rupture).
7. Le Scenario Plan est **re-projeté** avec la proposition (exact aussi en politique `lost`) et l'itération
   continue (`proposal_lookahead_days`).
8. Pour une proposition **urgente**, le motif indique la première commande prévisionnelle / planifiée
   ultérieure qui pourrait être **avancée** à la place (message d'exception MRP « avancer »). Évolution
   prévue : générer directement des actions « avancer / reculer / annuler » sur les commandes existantes,
   en plus des nouvelles commandes.

Pour transformer une proposition en décision, l'approvisionneur l'accepte (ligne libre du plan) ou saisit
lui-même la ligne qu'il veut : le complément CBN se recalcule sans elle.

## 8. Paramètres d'article par semaine

Les paramètres de politique de stock (couverture cible, seuils rouge / orange, surstock, stock de sécurité,
cycle de commande) sont fixes par article dans le référentiel ; le bouton « Semaines » ouvre un calendrier
hebdomadaire prérempli avec ces valeurs, où chaque valeur peut être personnalisée pour une semaine ISO
(surcharge `article_week`, réversible). Le moteur applique la valeur de la semaine du jour calculé : cible
et niveau de recomplètement jour par jour, seuils d'alerte de la semaine de référence.

## 9. Impact sur les programmes

Pour chaque programme et chaque semaine, la production **réalisable** est le PDP pondéré par la part
servable de son composant le plus contraint : `part_c(j) = 1 − besoin non servi_c(j) / besoin_c(j)`,
`réalisable(j) = PDP(j) × min_c part_c(j)`, évaluée pour trois stocks : **à date** (rien n'arrive),
Scenario ERP et Scenario Plan. Le besoin non servi du jour est la quantité perdue (`lost`) ou
l'augmentation du retard (`backlog`). La page *Impact programmes* affiche le % réalisable par semaine, le
premier impact et les composants limitants (`engine/programs.py`).

## 10. Scénarios

Un scénario est une liste d'événements appliqués sur une copie des données avant calcul :
ajout / décalage / modification / annulation de commande, facteur ou valeur de PDP, production réelle,
ajustement de stock, paramètre article ou fournisseur, et des paramètres moteur (horizon…). Comparaison
base ↔ scénario par KPI et par article ; un scénario « actif » s'applique à toutes les pages et exports.

## 11. Traçabilité, priorité des données et règles de date

Ordre de priorité : **saisie applicative > ERP** pour un même objet (réel de production d'un jour,
réception rattachée à une commande, PDP actif). Chaque écriture (saisie, décision, import, paramètre) est
journalisée avec l'utilisateur (`x-forwarded-email` sur Databricks Apps), l'action et le contenu.

| Objet | Nature | Date admise |
|---|---|---|
| Ligne du plan | prévision | à partir de la date de référence |
| Commande saisie hors ERP | prévision | à partir de la référence ; « commande ancienne encore due » pour un retard |
| Réception | fait constaté | après le snapshot, au plus tard la date de référence |
| Ajustement | fait constaté ou prévu | toute date ; ≤ référence = correction du stock de référence |

## 12. Classeur Excel « vivant »

L'export *Simulation* n'est pas un état figé : l'onglet `SIMULATION` est constitué de **formules** qui
reproduisent les règles ci-dessus, alimentées par des onglets de saisie. L'approvisionneur peut donc
travailler hors ligne et voir les stocks se recalculer, puis réimporter son plan.

| Onglet | Rôle |
|---|---|
| `PARAMETRES` | politique de manque, politique de cible, règle d'égalité de couverture (cellules modifiables lues par les formules) |
| `ARTICLES` | stocks initiaux des deux scenarios, correction de référence, couverture cible, stock de sécurité, seuils, MOQ / PLA / délai |
| `SIMULATION` | par article, 15 lignes : Consommé et Requis (valeurs), Ferme et Prévisionnel (`SUMIFS` sur `PLAN`, dates ERP), Reçu (valeurs), **Plan** (`SUMIFS` sur `PLAN`, dates plan), Complément CBN (valeurs), Ajustement (valeurs), saisies (`SUMIFS` sur `SAISIES`), Scenario ERP, Scenario Plan, manque, cible (`OFFSET`), couverture (`COUNTIF`), besoin cumulé |
| `PLAN` | le plan de livraison : une ligne par commande ERP (Date / Qté ERP en regard, non modifiables) et par ligne du plan ; colonnes de saisie **Date plan / Qté plan** ; commandes non reçues listées avec leur retard (une date plan les fait entrer dans le Scenario Plan) |
| `SAISIES` | commandes fermes hors ERP (les deux scenarios), réceptions, ajustements, production réelle |
| `ALERTES` | photo des alertes à l'export |

Récurrence par colonne (jour ou semaine ISO) : `x = stock précédent + flux + réceptions & ajustements −
besoin`, `stock = SI(politique = "lost" ; MAX(0 ; x) ; x)`, `manque = MAX(0 ; −x)`. En granularité semaine,
la cible porte sur `ARRONDI.SUP(couverture / 7)` semaines et la couverture est comptée en semaines
(approximation assumée ; le jour reste la granularité de référence). Le lettrage du jour de référence est
appliqué à l'export (colonne *Qté ERP*) mais pas recalculé par Excel. La conformité des formules avec le
moteur est vérifiée par un test automatisé (recalcul LibreOffice, `tests/test_excel_formulas.py`).

Réimport (page *Imports / exports*) : lignes `SAISIES` et onglet `PLAN` : le plan des articles présents dans
le classeur **remplace** celui de l'application (lignes qui diffèrent de l'ERP et lignes `LIBRE`).

Le **modèle du fichier PDP** à importer (onglet `SOP - PDP`, une ligne par programme, une colonne par semaine
ISO, notice) se télécharge dans la même page (`GET /api/pdp/template.xlsx`).

## 13. Hypothèses sur les données de démonstration

Le classeur ne contient ni délais, ni conditionnements, ni quotas : le jeu de démonstration
(`data/seed`, généré par `scripts/extract_seed_from_excel.py`) utilise `pack_qty = MOQ`, des délais
indicatifs par fournisseur (5 à 20 jours ouvrés), des quotas 100/0 ou 50/50 pour les doubles sources,
des seuils rouge 3 j / orange = couverture cible / surstock max(30, 3 × cible). Ces valeurs doivent être
remplacées par celles de l'ERP dans Unity Catalog.
