# Recommandations : rendre l'application plus simple, plus réaliste et plus fiable

Synthèse des échanges d'octobre 2026 sur les règles du moteur, le multi-sourcing, le backlog, le
délai, la tolérance et le rattrapage de stock. Chaque recommandation dit ce qu'elle change, pourquoi,
et ce qu'il faut vérifier avant de l'appliquer. Les lots de la fin donnent un ordre de mise en œuvre.

Conventions acquises : **jours ouvrés** partout, propositions CBN sur **tous les jours de livraison du
fournisseur**, cycle de commande à 0 sur tous les articles, valeurs du référentiel déjà saisies en ouvrés.

---

## A. Règles globales du moteur : supprimer ou figer

Objectif : passer de 25 règles à une dizaine, sans changer les résultats actuels (chaque règle retirée est
figée à sa valeur par défaut ou à la valeur en usage).

| Règle | Décision | Valeur figée | Pourquoi |
|---|---|---|---|
| `proposal_placement` | supprimer | `working_days` | doublon des jours de livraison du fournisseur ; `monday` = `delivery_weekdays = 1` |
| `lot_policy`, `fixed_lot_qty` (moteur) | supprimer | `coverage` | jamais alimentés par le référentiel : code mort |
| `target_policy` | supprimer | `max` | couverture à 0 ou sécurité à 0 donnent déjà les deux autres cas |
| `missing_actual_policy` | supprimer | `zero` | un jour sans consommation déclarée est un jour sans mouvement |
| `coverage_tie_rule` | supprimer | `covered` | égalité exacte besoin cumulé = stock, cas quasi inexistant, illisible |
| `production_mode` | supprimer | `actual_then_remainder` | les trois autres modes sont des modes de débogage |
| `spread_rounding` | supprimer | `exact` | seul mode qui respecte la somme du PDP |
| `consumption_offset_days` | supprimer | 0 | jamais renseigné ; propre à une nomenclature s'il existe |
| `proposal_lookahead_days`, `stockout_lookahead_days` | supprimer | tout l'horizon | doublons de l'horizon et de l'horizon ferme |
| `forecast_date_policy` | supprimer | `actual` | n'affecte que l'affichage du prévisionnel |
| `generate_proposals`, `include_proposals_in_plan` | supprimer | oui / oui | interrupteurs de développement ; les désactiver casse le flux À commander |
| `coverage_unit` | supprimer | `working` | une seule unité, les jours ouvrés (§ E) |
| `shortfall_tolerance_days` | supprimer | 0 | remplacée par le plancher et la rampe (§ C) |
| `focus_weeks`, `history_weeks` | déplacer | — | réglages d'affichage, par utilisateur, dans Paramètres › Affichage |
| `backlog_days` | supprimer | — | disparaît avec le backlog sur restant ERP (§ D) |

Restent globales : `horizon_days`, `firm_sources`, `shortage_policy` (en attendant son rattachement à l'article).

Précaution unique avant de supprimer : vérifier dans `app_param_overrides` qu'aucune valeur non défaut n'a été
saisie en production sur les règles retirées ; sinon la reporter là où la règle est rattachée.

## B. Rattacher les règles au fournisseur, au lien ou à l'article

Mécanisme : étendre `app_param_overrides` (portées `global` et `article_week` aujourd'hui) aux portées
`supplier`, `link` et `article`, avec la précédence global < article < fournisseur < lien, et exposer ces
valeurs comme colonnes **facultatives** du Référentiel : vide = héritage. Aucun résultat ne change tant que
rien n'est saisi.

### Sur le fournisseur (`ref_suppliers`)

| Champ | Rôle | Défaut |
|---|---|---|
| `lead_time_days` | délai commande → livraison par défaut, jours ouvrés | obligatoire |
| `frozen_days` | période gelée : aucune livraison proposée avant J + n ouvrés | 0 |
| `respect_lead_time` | ne jamais proposer avant J + délai (sinon la proposition est simplement marquée urgente) | non |
| `delivery_shift` | jour non autorisé : avancer (regroupe sur le jour de livraison précédent) ou reculer | avancer |
| `delivery_weekdays` | existant ; porte désormais aussi la cadence hebdomadaire | 1,2,3,4,5 |

### Sur le lien article ↔ fournisseur (`ref_article_suppliers`)

| Champ | Rôle | Défaut |
|---|---|---|
| `lead_time_days` | devient **facultatif** : exception au délai du fournisseur | vide = fournisseur |
| `max_qty_per_delivery` | plafond par livraison (camion, emballage, capacité) ; l'excédent glisse à la livraison suivante | vide = aucun |
| `moq`, `pack_qty`, `quota_pct`, `priority`, `active` | inchangés | |

### Sur l'article (`ref_articles`, et par semaine)

| Champ | Rôle | Défaut |
|---|---|---|
| `sourcing_policy` | quotas ou priorité | quota |
| `shortage_policy` | besoin non servi perdu ou reporté | perdu |
| `firm_horizon_days` | horizon de gradation des ruptures, jours ouvrés ; alternative : dériver du plus long délai des fournisseurs de l'article | 20 |
| `target_ramp_days` | durée de rattrapage de la cible, jours ouvrés (§ C) ; 0 = en une fois | = couverture cible |
| `order_cycle_days` | **supprimer** : à 0 partout, la cadence est portée par les jours de livraison du fournisseur et par le MOQ | — |

## C. Propositions CBN : plus réalistes

1. **Plancher dans le délai.** Avant J + délai du fournisseur, une proposition ne porte que la quantité qui
   évite tout besoin non servi (plus le stock de sécurité). Elle reste marquée urgente, mais ne demande plus de
   remonter à la cible. C'est la mesure qui change le plus vos urgences, sur tous les articles, sans paramètre.
2. **Rampe de cible** (`target_ramp_days`). À partir de J + délai, la cible visée part du stock projeté et
   rejoint la vraie cible en n jours ouvrés ; les propositions suivent cette pente, aux jours de livraison du
   fournisseur, chaque livraison visant la cible effective de la veille de la livraison suivante. Rampe plus
   courte que l'intervalle de livraison : étirée à la deuxième livraison. La rampe s'applique aussi en
   anticipation d'une hausse de cible (PDP qui monte). Sans effet quand l'écart tient en quelques jours de
   besoin ou quand le MOQ dépasse l'écart ; utile sur les couvertures longues. Défaut = couverture cible.
3. **Plafond par livraison** (`max_qty_per_delivery`, lien). Complément physique de la rampe ; sur un
   fournisseur hebdomadaire, un plafond trop bas crée un manque visible plutôt qu'une livraison impossible.
4. **Quotas** : déjà livré, chaque besoin est partagé au prorata, un fournisseur refusé cède sa part.
5. **Motif des propositions** : conserver la phrase explicative par proposition (plancher, part de quota,
   rampe), c'est ce qui rend ces règles acceptables à l'écran.

## D. Backlog : exact et sans réglage

Remplacer `max(0, Σ commandé des fermes passées − Σ réceptions de la fenêtre)` par
**Σ restant ERP des commandes fermes dont la date de livraison est passée**, par fournisseur. Plus de fenêtre
de 28 jours, plus d'effet d'une réception qui solde une autre commande, cohérence avec la ligne Ferme qui
affiche déjà `restant / commandé`.

Vérification préalable : à quel moment l'ERP décrémente `RemainInventPhysical`, à la validation du journal
d'arrivée ou à l'accusé de réception. Si c'est à l'ACR, déduire du restant les réceptions au statut *Reçu* de
la même commande (même `purch_id`), que `recep_edi` porte.

## E. Une seule unité de temps : le jour ouvré

| Paramètre | Aujourd'hui | Cible |
|---|---|---|
| couverture cible, seuils, surstock, couverture affichée | calendaires sauf règle basculée | ouvrés, figé |
| `frozen_days` | calendaires | ouvrés |
| `firm_horizon_days` | calendaires | ouvrés |
| délai, tolérance | ouvrés | — |
| `horizon_days` | calendaires | reste calendaire (longueur de fenêtre, pas une règle métier) |
| Besoin 30 j (fiche article) | 30 jours calendaires, moyenne par jour ouvré | 20 jours ouvrés |

Les valeurs du référentiel ayant été saisies en ouvrés, aucune conversion. Le calendrier d'usine
(`APPRO_HOLIDAYS`, `working_weekdays`) devient structurant : à tenir à jour.

## F. Référentiel et écrans

- Retirer la colonne *Cycle de commande* des articles, des paramètres hebdomadaires, du modèle Excel et de
  l'export ; ajouter *Rattrapage (j ouvrés)* avec la même mécanique hebdomadaire.
- Fournisseurs : ajouter *Délai (j ouvrés)*, *Période gelée*, *Respecter le délai*, *Décalage de livraison* ;
  rendre le délai du lien facultatif avec l'héritage affiché en grisé.
- Lien : ajouter *Max par livraison*.
- Page Paramètres : ne garder que les règles réellement communes ; déplacer `focus_weeks` et `history_weeks`
  dans l'onglet Affichage ; afficher le journal de synchronisation (`erp_sync_log`) avec l'âge de chaque table
  miroir et une alerte si une table a plus d'une heure de retard.
- Guide du moteur : régénérer avec des scénarios plancher / rampe (quotidien, hebdomadaire, mardi-jeudi) et
  retirer les sections des règles supprimées.

## G. Fiabilité des données

- `recep_edi` : contrat à trois statuts (*En transit*, *Reçu*, *Comptabilisé*) à déployer dans le job amont
  tel que fourni ; sans lui, les journaux non validés apparaissent comme reçus.
- `desadv_edi` : garder tous les messages, états *Traité* compris, depuis la date d'initialisation du stock ;
  c'est l'absence d'un DESADV traité qui met un point rouge sur une réception correcte.
- Articles orphelins : un contrôle dans Paramètres listant les articles présents dans `erp_receipts`,
  `erp_purchase_orders` ou `erp_desadv` et absents de `ref_articles` (aujourd'hui ignorés en silence).
- `first_seen` : vérifier après le premier passage du job que la colonne se remplit, sinon le flux Commandé
  reste vide.
- `bl_en_attente` : table devenue redondante avec le statut *Reçu* ; peut être retirée du job et de la
  configuration.

## H. Ordre de mise en œuvre proposé

**Lot 1, sans changement de résultat** : suppression et figeage des règles du § A, jours ouvrés (§ E),
retrait du cycle de commande, contrôle des articles orphelins, journal de synchronisation dans Paramètres.

**Lot 2, propositions réalistes** : plancher dans le délai, rampe par article avec défaut = couverture,
plafond par livraison, guide régénéré. Résultats modifiés uniquement sur les articles en rattrapage.

**Lot 3, rattachements** : portées `supplier`, `link`, `article` des règles, délai par défaut sur le
fournisseur, colonnes facultatives du Référentiel, migration des éventuelles valeurs globales non défaut.

**Lot 4, backlog** : restant ERP après vérification du comportement de `RemainInventPhysical`, retrait de
`backlog_days` et de `bl_en_attente`.

Chaque lot est livrable seul, testé par les scénarios du guide du moteur, et rétro-compatible avec les
saisies existantes.
