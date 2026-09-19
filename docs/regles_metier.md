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

## 3. Approvisionnements

| Élément | Ferme | Simulé | Règle |
|---|---|---|---|
| Commande ERP `FIRM` (DELJIT / OA) | ✔ | ✔ | quantité ouverte = commandée − reçue |
| Commande ERP `FORECAST` (DELFOR) | – | ✔ | `forecast` compté dans le stock simulé seulement (`simulated_sources`) |
| Commande app `PLANNED` | – | ✔ | saisie ou proposition acceptée, non envoyée |
| Commande app `FIRM` / statut `SENT` | ✔ | ✔ | envoyée au fournisseur |
| Proposition moteur | – | ✔ (option) | `include_proposals_in_simulation` [oui] |
| Réception postérieure au snapshot | ✔ | ✔ | comptée à sa date ; **solde la commande** liée (plus de double compte) |
| Ajustement (inventaire, casse…) | ✔ | ✔ | compté à sa date s'il est postérieur au snapshot |

* **Commande en retard** (date attendue < `as_of`, non reçue) → alerte `LATE_ORDER` et
  `late_order_policy` : `reschedule` [défaut, replanifiée au prochain jour ouvré], `ignore`, `keep`.
* **Source des commandes** (`orders_source`) : `merged` [défaut], `erp`, `app` — bascule ERP / saisies.
* Une réception app rattachée à une commande ERP réduit la quantité ouverte de celle-ci tant que l'ERP
  ne l'a pas intégrée (à supprimer ensuite ; l'écran *Saisies* le rappelle).

## 4. Projection de stock

`stock[j] = stock[j−1] + approvisionnements[j] + ajustements[j] − besoin[j]`, à partir du stock du snapshot
(`qty_on_hand − qty_blocked`). Deux séries : **stock ferme** et **stock simulé** (cf. tableau ci-dessus).

## 5. Couverture et stock cible

* **Couverture (jours)** sur chaque jour : nombre de jours futurs dont le besoin cumulé est couvert par le
  stock du jour. `coverage_unit` : `calendar` [défaut, comme le classeur] ou `working` ;
  `coverage_tie_rule` : `covered` [défaut] ou `not_covered` (classeur). Stock négatif → 0.
* **Stock cible** (`target_policy`) : `coverage_days` = besoin des `coverage_target_days` prochains jours ;
  `safety_qty` = stock de sécurité fixe ; `max` [défaut] = le plus grand des deux.

## 6. Alertes

| Type | Sévérité | Règle |
|---|---|---|
| `STOCKOUT` (simulé) | critique | premier jour où le stock simulé < 0 (`stockout_lookahead_days`) |
| `STOCKOUT` (ferme) | critique si ≤ délai fournisseur, avertissement si ≤ `firm_horizon_days` [28], info au‑delà | premier jour où le stock ferme < 0 : commandes prévisionnelles / planifiées à confirmer |
| `LOW_COVERAGE` | critique si épuisement des flux fermes ≤ `alert_red_days` [3], avertissement si ≤ `alert_yellow_days` [= couverture cible] | basé sur l'épuisement du stock ferme (stock + commandes fermes), pas seulement sur le stock à date |
| `OVERSTOCK` | info | couverture du stock à date ≥ `overstock_days` [max(30 ; 3 × cible)] |
| `NEGATIVE_STOCK` | critique | stock négatif à la date de référence (inventaire / saisies à vérifier) |
| `LATE_ORDER` | avertissement | commande attendue avant `as_of` et non reçue |
| `URGENT_PROPOSAL` | critique | proposition dont la date de commande théorique est déjà passée |
| `NO_DEMAND` | info | aucun besoin sur l'horizon alors que du stock existe |
| `MISSING_DATA` | avertissement / info | pas de snapshot, pas de fournisseur, pas de nomenclature |

La sévérité d'un article est la pire de ses alertes.

## 7. Propositions de commandes (calcul des besoins nets)

Algorithme, par article, sur le stock simulé :

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
7. La proposition est injectée dans le stock simulé et l'itération continue (`proposal_lookahead_days`).
8. Pour une proposition **urgente**, le motif indique la première commande prévisionnelle / planifiée
   ultérieure qui pourrait être **avancée** à la place (message d'exception MRP « avancer »). Évolution
   prévue : générer directement des actions « avancer / reculer / annuler » sur les commandes existantes,
   en plus des nouvelles commandes.

Décisions possibles : **accepter** (→ commande planifiée, source `PROPOSAL`), **modifier** (saisie
pré‑remplie), **ignorer** (masquée jusqu'à une date ; réactivable). Une proposition acceptée disparaît au
calcul suivant puisque la commande planifiée couvre le besoin.

## 8. Scénarios

Un scénario est une liste d'événements appliqués sur une copie des données avant calcul :
ajout / décalage / modification / annulation de commande, facteur ou valeur de PDP, production réelle,
ajustement de stock, paramètre article ou fournisseur, et des paramètres moteur (horizon…). Comparaison
base ↔ scénario par KPI et par article ; un scénario « actif » s'applique à toutes les pages et exports.

## 9. Traçabilité et priorité des données

Ordre de priorité : **saisie applicative > ERP** pour un même objet (réel de production d'un jour,
réception rattachée à une commande, PDP actif). Chaque écriture (saisie, décision, import, paramètre) est
journalisée avec l'utilisateur (`x-forwarded-email` sur Databricks Apps), l'action et le contenu.

## 10. Hypothèses sur les données de démonstration

Le classeur ne contient ni délais, ni conditionnements, ni quotas : le jeu de démonstration
(`data/seed`, généré par `scripts/extract_seed_from_excel.py`) utilise `pack_qty = MOQ`, des délais
indicatifs par fournisseur (5 à 20 jours ouvrés), des quotas 100/0 ou 50/50 pour les doubles sources,
des seuils rouge 3 j / orange = couverture cible / surstock max(30, 3 × cible). Ces valeurs doivent être
remplacées par celles de l'ERP dans Unity Catalog.
