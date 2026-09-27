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
   * `actual_then_plan` [défaut] : le réel déclaré pour (programme, jour) remplace le plan – **un 0 déclaré
     est respecté** ; `missing_actual_policy` [`plan`] décide du sort des jours passés sans déclaration
     (`plan` ou `zero`) ;
   * `plan_only` / `actual_only`.
   Le réel saisi dans l'application prime sur le réel ERP pour le même jour.
3. **Éclatement nomenclature (1 niveau)** : besoin composant = Σ production effective × `qty_per`
   × (1 + `scrap_pct` %) sur les lignes valides (`valid_from` / `valid_to`) ; `consumption_offset_days` [0]
   décale la consommation (ex. −1 : composants consommés la veille).
4. Un PDP importé et **actif** remplace le plan ERP pour les programmes qu'il contient.

## 3. Approvisionnements et couches de stock

Trois stocks **cumulatifs** sont projetés (ferme ⊂ prévisionnel ⊂ simulé) : les deux premiers reposent
sur les données ERP (EDI ferme puis EDI prévisionnel), le troisième ajoute ce que l'approvisionneur simule
dans le tableau (réceptions simulées) et le complément CBN calculé par le moteur.

Notation par jour : **R** = réceptions du jour (postérieures au snapshot), **F** = commandes fermes
restantes du jour, **P** = commandes prévisionnelles du jour, **S** = réception simulée saisie,
**A** = ajustements, **CBN** = complément calculé.

| Jour calculé | Stock ferme | Stock prévisionnel | Stock simulé |
|---|---|---|---|
| Avant la référence | R + A | R + A | R + A |
| À la référence | R + F + A | R + F + P + A | S vide : R + F + P + A ; S saisie : **MAX(R, S)** + A |
| Après la référence | F + A | F + P + A | S vide : F + P + A ; S saisie : **S** + A |

Une cellule S **vide** conserve les commandes attendues (F + P) ; **0** est une saisie explicite qui les
remplace (rien n'arrive) ; effacer la saisie rétablit F + P. La réception simulée prend donc le pas sur
les commandes attendues du jour, ce qui permet de simuler un retard (0 ce jour-là, la quantité un autre
jour), une livraison partielle ou excédentaire. Le complément CBN (§ 7) s'ajoute ensuite au stock simulé
seulement. Les réceptions et ajustements sont des faits physiques comptés dans les trois couches (R est
nul après la référence en pratique).

| Élément | Ferme | Prévisionnel | Simulé | Règle |
|---|---|---|---|---|
| Commande ERP `FIRM` (DELJIT / OA) | ✔ | ✔ | ✔ sauf S saisie | quantité ouverte = commandée − reçue (`firm_sources`) |
| Commande ERP `FORECAST` (DELFOR) | – | ✔ | ✔ sauf S saisie | `forecast_sources` ; `forecast_date_policy` : date réelle [défaut] ou lundi de la semaine |
| Commande app `FIRM` (saisie) | ✔ | ✔ | ✔ sauf S saisie | commande réelle passée hors ERP ; `app_firm_orders = simulated` la confine à la couche prévisionnelle |
| Commande de scénario | – | ✔ | ✔ sauf S saisie | idem prévisionnel |
| **Réception simulée S** (cellule) | – | – | ✔ | remplace F + P du jour ; MAX(R, S) le jour de référence |
| Complément CBN | – | – | ✔ (option) | `include_proposals_in_simulation` [oui] |
| Réception postérieure au snapshot | ✔ | ✔ | ✔ | fait physique ; **solde la commande** liée (plus de double compte) |
| Ajustement (inventaire, casse, cellule du tableau) | ✔ | ✔ | ✔ | fait physique, quantité signée |

Lecture : le stock **ferme** répond à « que se passe-t-il si rien d'autre n'arrive que le ferme ? », le
stock **prévisionnel** à « l'ERP suffit-il ? », le stock **simulé** à « mes hypothèses de livraison
suffisent-elles, et que faut-il en plus (complément CBN) ? ».

**Saisie dans le tableau** : les lignes *Réceptions simulées* et *Ajustements* se saisissent directement
dans la cellule (fiche article ou tableau d'approvisionnement), avec une quantité (négative possible pour
un ajustement) ou une expression arithmétique (`+ − × ÷`, parenthèses, ex. `2*600-50`) évaluée côté serveur
(`services/expression.py`, aucun autre opérateur ni fonction). Une cellule vidée est effacée ; `0` est une
valeur. En vue semaine la saisie se pose sur le premier jour de la colonne. Chaque cellule conserve son
expression, son origine (`MANUAL`, `IMPORT`) et une note.

**Calendrier d'affichage** (`focus_weeks` [2]) : le mode *Par défaut* détaille jour par jour la semaine en
cours et les `focus_weeks` semaines suivantes, et agrège en semaines ISO le passé et le futur au-delà ;
les modes *Jour* et *Semaine* sont uniformes. Une semaine agrégée affiche la somme des flux et la valeur
de fin de semaine des stocks.

## 4. Projection de stock et besoin non servi

`x[j] = stock[j−1] + approvisionnements[j] + ajustements[j] − besoin[j]`, à partir du stock du snapshot
(`qty_on_hand − qty_blocked`), pour chacune des trois couches.

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
| `STOCKOUT` (simulé) | critique | premier manque sur le stock simulé malgré les réceptions simulées et le complément CBN (`stockout_lookahead_days`) |
| `STOCKOUT` (prévisionnel) | critique si ≤ délai fournisseur, avertissement si ≤ `firm_horizon_days` [28], info au‑delà | premier manque sur les flux ERP fermes + prévisionnels : commande à passer / proposition à valider (émise seulement si sa date diffère de la rupture ferme) |
| `STOCKOUT` (ferme) | idem | premier manque sur les flux fermes ; le message indique jusqu'où les commandes prévisionnelles couvrent (à confirmer) ou qu'aucune ne couvre la date |
| `LOW_COVERAGE` | critique si épuisement des flux fermes ≤ `alert_red_days` [3], avertissement si ≤ `alert_yellow_days` [= couverture cible] | basé sur l'épuisement du stock ferme (stock + commandes fermes), pas seulement sur le stock à date |
| `OVERSTOCK` | info | couverture du stock à date ≥ `overstock_days` [max(30 ; 3 × cible)] |
| `NEGATIVE_STOCK` | critique | stock de départ négatif dans l'ERP (inventaire / saisies à vérifier) |
| `LATE_ORDER` | avertissement | commande attendue avant `as_of` et non reçue |
| `URGENT_PROPOSAL` | critique | complément CBN dont la date de commande théorique est déjà passée |
| `NO_DEMAND` | info | aucun besoin sur l'horizon alors que du stock existe |
| `MISSING_DATA` | avertissement / info | pas de snapshot, pas de fournisseur, pas de nomenclature |

La sévérité d'un article est la pire de ses alertes.

## 7. Complément CBN (calcul des besoins nets)

Le complément CBN est recalculé **automatiquement** à chaque calcul (`generate_proposals` [oui]) sur le
stock simulé, c'est-à-dire après les réceptions simulées saisies : il ne propose que ce que ni l'ERP ni
les hypothèses de l'approvisionneur ne couvrent. Il apparaît dans la ligne *Complément CBN* du tableau et
dans la page du même nom (liste par fournisseur, urgences, export du carnet). `proposal_placement`
[`working_days`] : livraison proposée n'importe quel jour ouvré autorisé par le fournisseur, ou `monday` :
livraisons regroupées le lundi (quel que soit le calendrier du fournisseur).

Algorithme, par article, sur le stock simulé (solde net avant propositions) :

1. À partir de `as_of + 1 + frozen_days` [0] : dès que `stock[d] < cible[d]` → **point de commande atteint**.
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
7. Le stock simulé est **re-projeté** avec la proposition (exact aussi en politique `lost`) et l'itération
   continue (`proposal_lookahead_days`).
8. Pour une proposition **urgente**, le motif indique la première commande prévisionnelle / planifiée
   ultérieure qui pourrait être **avancée** à la place (message d'exception MRP « avancer »). Évolution
   prévue : générer directement des actions « avancer / reculer / annuler » sur les commandes existantes,
   en plus des nouvelles commandes.

Pour transformer une proposition en hypothèse ferme, l'approvisionneur saisit la quantité en réception
simulée à la date voulue : le complément CBN se recalcule aussitôt sans elle.

## 8. Paramètres d'article par semaine

Les paramètres de politique de stock (couverture cible, seuils rouge / orange, surstock, stock de sécurité,
cycle de commande) sont fixes par article dans le référentiel ; le bouton « Semaines » ouvre un calendrier
hebdomadaire prérempli avec ces valeurs, où chaque valeur peut être personnalisée pour une semaine ISO
(surcharge `article_week`, réversible). Le moteur applique la valeur de la semaine du jour calculé : cible
et niveau de recomplètement jour par jour, seuils d'alerte de la semaine de référence.

## 9. Impact sur les programmes

Pour chaque programme et chaque semaine, la production **réalisable** est le PDP pondéré par la part
servable de son composant le plus contraint : `part_c(j) = 1 − besoin non servi_c(j) / besoin_c(j)`,
`réalisable(j) = PDP(j) × min_c part_c(j)`, évaluée pour quatre stocks : **à date** (rien n'arrive),
ferme, prévisionnel et simulé. Le besoin non servi du jour est la quantité perdue (`lost`) ou
l'augmentation du retard (`backlog`). La page *Impact programmes* affiche le % réalisable par semaine, le
premier impact et les composants limitants (`engine/programs.py`).

## 10. Scénarios

Un scénario est une liste d'événements appliqués sur une copie des données avant calcul :
ajout / décalage / modification / annulation de commande, facteur ou valeur de PDP, production réelle,
ajustement de stock, paramètre article ou fournisseur, et des paramètres moteur (horizon…). Comparaison
base ↔ scénario par KPI et par article ; un scénario « actif » s'applique à toutes les pages et exports.

## 11. Traçabilité et priorité des données

Ordre de priorité : **saisie applicative > ERP** pour un même objet (réel de production d'un jour,
réception rattachée à une commande, PDP actif). Chaque écriture (saisie, décision, import, paramètre) est
journalisée avec l'utilisateur (`x-forwarded-email` sur Databricks Apps), l'action et le contenu.

## 12. Classeur Excel « vivant »

L'export *Simulation* n'est pas un état figé : l'onglet `SIMULATION` est constitué de **formules** qui
reproduisent les règles ci-dessus, alimentées par des onglets de saisie. L'approvisionneur peut donc
travailler hors ligne et voir les stocks se recalculer, puis réimporter ses décisions.

| Onglet | Rôle |
|---|---|
| `PARAMETRES` | politique de manque, politique de cible, règle d'égalité de couverture (cellules modifiables lues par les formules) |
| `ARTICLES` | stocks initiaux des trois couches, couverture cible, stock de sécurité, seuils, MOQ / PLA / délai (modifiables) |
| `SIMULATION` | par article, 17 lignes : besoin (valeurs), commandes fermes F / prévisionnelles P (`SUMIFS` sur le carnet), réceptions connues R, **réceptions simulées S** (vide / 0 / valeur, formules Excel acceptées), complément CBN, saisies (commandes, réceptions, ajustements), ajustements connus A, stocks ferme / prévisionnel / simulé (règles du § 3, `IF(S="", R+F+P, S)`, `MAX(R,S)` le jour de référence), manque simulé, cible (`OFFSET` sur le besoin), couverture (`COUNTIF` sur le besoin cumulé) |
| `CARNET_COMMANDES` | carnet ouvert : date, quantité, quantité reçue, date de réception, statut (`RECUE` / `ANNULEE`) modifiables ; *Reste à livrer* calculé |
| `SAISIES` | commandes, réceptions, ajustements, production réelle (lignes libres) |
| `ALERTES` | photo des alertes à l'export |

Récurrence par colonne (jour ou semaine ISO) : `x = stock précédent + commandes + réceptions & ajustements
− besoin`, `stock = SI(politique = "lost" ; MAX(0 ; x) ; x)`, `manque = MAX(0 ; −x)`. En granularité
semaine, la cible porte sur `ARRONDI.SUP(couverture / 7)` semaines et la couverture est comptée en semaines
(approximation assumée ; le jour reste la granularité de référence). Une réception saisie dans le carnet
s'ajoute au stock à sa date et réduit le reste à livrer de la commande. La conformité des formules avec le
moteur est vérifiée par un test automatisé (recalcul LibreOffice, `tests/test_excel_formulas.py`).

Réimport (page *Imports / exports*) : lignes `SAISIES`, réceptions saisies dans le `CARNET_COMMANDES`
(→ réceptions rattachées à la commande) et ligne *Réceptions simulées* de `SIMULATION` (→ cellules, origine
`IMPORT` : valeur, 0 explicite ou vide ; elle **remplace** les réceptions simulées de l'article sur les
dates du classeur).

## 13. Hypothèses sur les données de démonstration

Le classeur ne contient ni délais, ni conditionnements, ni quotas : le jeu de démonstration
(`data/seed`, généré par `scripts/extract_seed_from_excel.py`) utilise `pack_qty = MOQ`, des délais
indicatifs par fournisseur (5 à 20 jours ouvrés), des quotas 100/0 ou 50/50 pour les doubles sources,
des seuils rouge 3 j / orange = couverture cible / surstock max(30, 3 × cible). Ces valeurs doivent être
remplacées par celles de l'ERP dans Unity Catalog.
