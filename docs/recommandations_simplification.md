# Recommandations : rendre l'application plus simple, plus réaliste et plus fiable

Liste exhaustive, à enrichir avant implémentation. Décisions acquises au fil des échanges : jours **ouvrés**
partout ; propositions CBN sur **tous les jours de livraison du fournisseur** ; pas de rampe de cible, le
lissage est porté par un **plancher** dans le délai et des **plafonds** par livraison et par semaine ; plus de
stock de sécurité, la cible est la seule couverture cible ; besoin non servi **perdu** par défaut (fait) ;
`RemainInventPhysical` décrémenté dès la validation du journal d'arrivée.

## 1. Règles globales du moteur à supprimer (figées à la valeur en usage, résultats inchangés)

- `proposal_placement` : supprimé, figé à `working_days` ; le regroupement hebdomadaire est porté par
  `delivery_weekdays` du fournisseur et `delivery_shift = earlier`.
- `lot_policy` et `fixed_lot_qty` du moteur : supprimés, jamais alimentés par le référentiel (code mort).
- `target_policy` : supprimé, figé à `max` ; sans stock de sécurité il n'a plus d'objet du tout.
- `missing_actual_policy` : supprimé, figé à `zero` (jour sans consommation déclarée = jour sans mouvement).
- `coverage_tie_rule` : supprimé, figé à `covered`.
- `production_mode` : supprimé, figé à `actual_then_remainder` ; les trois autres modes sont du débogage.
- `spread_rounding` : supprimé, figé à `exact`, seul mode qui respecte la somme du PDP.
- `consumption_offset_days` : supprimé, figé à 0.
- `proposal_lookahead_days` et `stockout_lookahead_days` : supprimés, tout l'horizon est couvert.
- `forecast_date_policy` : supprimé, figé à `actual` (affichage du prévisionnel uniquement).
- `generate_proposals` et `include_proposals_in_plan` : supprimés, toujours actifs.
- `coverage_unit` : supprimé, figé à `working` ; couverture cible, seuils rouge/jaune, surstock et
  couverture affichée sont tous en jours ouvrés.
- `shortfall_tolerance_days` : supprimé, remplacé par le plancher (§ 4).
- `respect_lead_time` : supprimé ; une proposition peut tomber dans le délai, elle est alors marquée
  urgente et limitée au plancher (§ 4).
- `order_cycle_days` (article et hebdomadaire) : supprimé, à 0 partout ; la cadence est portée par les
  jours de livraison du fournisseur et par le MOQ.
- `safety_stock_qty` (article et hebdomadaire) : supprimé ; la couverture cible est le seul niveau visé.
  Avant retrait, vérifier qu'aucune valeur non nulle n'est saisie ; sinon la convertir en jours de
  couverture (sécurité / besoin moyen par jour ouvré) ajoutés à la cible de l'article.
- `backlog_days` : supprimé, le backlog ne dépend plus d'une fenêtre (§ 6).
- `focus_weeks` et `history_weeks` : retirés des règles du moteur, deviennent des réglages d'affichage par
  utilisateur dans Paramètres › Affichage.
- Restent globales : `horizon_days` (longueur de fenêtre, calendaire), `firm_sources`, `shortage_policy`
  (défaut `lost`, fait) jusqu'à son rattachement à l'article.
- Avant toute suppression : lister dans `app_param_overrides` les valeurs non défaut saisies en production
  sur les règles retirées et les reporter là où la règle est rattachée ; les clés orphelines sont ensuite
  purgées par la migration.

## 2. Règles à rattacher au fournisseur, au lien ou à l'article

- Mécanisme : étendre `app_param_overrides` des portées `global` et `article_week` aux portées `article`,
  `supplier` et `link`, précédence global < article < fournisseur < lien ; exposées comme colonnes
  facultatives du Référentiel, vide = héritage ; aucun résultat ne change tant que rien n'est saisi.
- Fournisseur `lead_time_days` : délai commande → livraison en jours ouvrés, délai inclus (lundi + 5 =
  lundi suivant), obligatoire, devient la valeur par défaut des liens.
- Fournisseur `frozen_days` : période gelée en jours ouvrés, aucune livraison proposée avant J + n, défaut 0.
- Fournisseur `delivery_shift` : jour non autorisé → avancer (défaut, regroupe sur le jour de livraison
  précédent) ou reculer.
- Fournisseur `delivery_weekdays` : existant, porte la cadence (1 à 5 jours).
- Pas de plafond au niveau du fournisseur toutes références confondues : il demanderait une unité commune
  (palettes, camions) absente du référentiel ; les plafonds sont sur le lien, dans l'unité de l'article.
- Lien `lead_time_days` : devient facultatif, exception au délai du fournisseur, affiché grisé quand hérité.
- Lien `max_qty_per_delivery` : plafond par livraison, multiple de l'UM et ≥ MOQ, vide = aucun.
- Lien `max_qty_per_week` : plafond par semaine ISO, multiple de l'UM, vide = aucun ; contrôle de saisie :
  ≤ plafond par livraison × nombre de jours de livraison du fournisseur, et avertissement si
  < besoin hebdomadaire moyen de l'article (plafond intenable).
- Lien `moq`, `pack_qty`, `quota_pct`, `priority`, `active` : inchangés.
- Article `sourcing_policy` : quotas ou priorité, défaut quota.
- Article `shortage_policy` : perdu ou reporté, défaut perdu.
- Article `firm_horizon_days` : horizon de gradation des ruptures, en jours ouvrés, défaut 20 ; variante :
  dérivé du plus long délai des fournisseurs actifs de l'article.
- Article `coverage_target_days`, `alert_red_days`, `alert_yellow_days`, `overstock_days` : inchangés,
  hebdomadaires, en jours ouvrés ; la colonne Stock de sécurité et la colonne Cycle de commande sont retirées
  du tableau, des paramètres hebdomadaires, du modèle Excel et de l'export.

## 3. Jour ouvré : une seule unité de temps

- Couverture cible, seuils, surstock et couverture affichée : jours ouvrés, figé (§ 1).
- `frozen_days`, `firm_horizon_days`, délai : lus en jours ouvrés ; les valeurs saisies le sont déjà,
  aucune conversion.
- `horizon_days` reste calendaire (longueur de la fenêtre de calcul, pas une règle métier).
- Indicateur « Besoin 30 j » de la fiche article : devient « Besoin 20 j ouvrés », même base que le besoin
  moyen par jour ouvré.
- Calendrier d'usine (`working_weekdays`, `APPRO_HOLIDAYS`) : devient structurant pour toutes les règles ;
  à afficher dans Paramètres avec les prochains jours fermés.

## 4. Propositions CBN : plancher, plafonds, quotas, refus

- **Plancher dans le délai** : une proposition dont la date de commande (livraison − délai en jours ouvrés)
  est antérieure à aujourd'hui est urgente ; elle porte uniquement la quantité qui évite tout besoin non
  servi jusqu'à la veille de la livraison suivante du fournisseur, lotie au MOQ et à l'UM ; elle ne remonte
  pas à la cible. Marquée urgente, motif « commande à passer immédiatement ».
- **Cible pleine dès la première livraison tenable** : à partir de la première livraison dont la commande
  peut encore être passée aujourd'hui, la proposition vise la couverture cible, sans rampe.
- **Plafond par livraison** : appliqué après lotissement, la quantité est tronquée au multiple d'UM
  inférieur ou égal au plafond ; le jour est alors plein et n'accueille plus de besoin ramené par le
  décalage « avancer » ; l'excédent glisse à la livraison suivante du même fournisseur, par simple
  reprojection.
- **Plafond par semaine** : somme des propositions du lien sur la semaine ISO (fermes et lignes Appro
  comprises dans le cumul, car ce sont des livraisons réelles) ; une fois atteint, les jours restants de la
  semaine sont pleins et l'excédent glisse à la semaine suivante.
- **Plafonds et plancher** : si même le plancher dépasse un plafond, le manque est inévitable : affiché en
  rouge dans le Projeté Appro. et remonté en alerte « plafond fournisseur atteint », comme le délai non
  tenable. Les plafonds s'appliquent aussi aux urgences.
- **Plafonds et quotas** : chaque part est plafonnée par son lien ; l'excédent d'un fournisseur reste chez
  lui à sa livraison suivante, les quotas ne bougent pas. Variante à trancher : un excédent plafonné peut-il
  basculer vers l'autre fournisseur du quota comme le fait un refus ?
- **Quotas** : déjà livré, chaque besoin partagé au prorata dès la première proposition, un fournisseur
  refusé cède sa part.
- **Refus d'une proposition** : bloque le jour et le reste de la semaine ISO pour ce fournisseur ; en
  monosource le besoin glisse après la fenêtre et le manque intermédiaire est affiché et alerté ; le refus
  d'une proposition plancher revient à accepter ce manque, rien n'est reproposé plus tôt.
- **Motif des propositions** : conservé et complété : plancher, plafond atteint (livraison ou semaine),
  part de quota, première livraison possible.
- **Regroupement** : inchangé, `delivery_shift = earlier` ramène chaque besoin sur le jour de livraison
  précédent du fournisseur ; un fournisseur quotidien n'est pas regroupé, un hebdomadaire porte toute la
  semaine, un mardi-jeudi porte deux intervalles.

## 5. Stock cible et alertes

- Cible du jour = besoin cumulé des `coverage_target_days` prochains jours ouvrés, rien d'autre.
- Alertes rouge/jaune et surstock : seuils en jours ouvrés, inchangés par ailleurs.
- Rupture gradée : `firm_horizon_days` en jours ouvrés.
- Le KPI « valeur cible » utilise la même cible.

## 6. Backlog fournisseur sur le restant ERP

- Backlog d'un fournisseur = Σ `qty_open` (`RemainInventPhysical`) des commandes fermes dont la date de
  livraison est passée, sans fenêtre ; `RemainInventPhysical` étant décrémenté dès la validation du journal
  d'arrivée, aucune correction par les réceptions *Reçu* n'est nécessaire.
- La ligne Ferme affiche déjà `restant / commandé` : cohérence entre ligne et KPI.
- Les fermes ignorées par clic restent hors projection mais restent dans le backlog : c'est précisément
  du retard fournisseur. À confirmer.
- `backlog_days` et la fenêtre de 28 jours disparaissent ; le flux « En retard » du cockpit liste les
  commandes concernées avec leur restant.

## 7. Données ERP et fiabilité

- `recep_edi` : contrat à trois statuts *En transit* / *Reçu* / *Comptabilisé*, rapprochement ACR par
  `inventTransId` (fait dans le job) ; mettre à jour `docs/flux_donnees_erp.md` et le dictionnaire qui
  décrivent encore le rapprochement par numéro de BL.
- `bl_en_attente` : table redondante avec le statut *Reçu*, retirée du job, de la configuration
  (`APPRO_ERP_BL_PENDING_TABLE`), du miroir `erp_bl_pending` et du type de recherche *BL en attente*.
- `desadv_edi` : conserver tous les messages, états *Traité* compris, depuis la date d'initialisation du
  stock ; c'est l'absence d'un DESADV traité qui met un point rouge sur une réception correcte.
- Articles orphelins : contrôle dans Paramètres listant les articles présents dans `erp_receipts`,
  `erp_purchase_orders` ou `erp_desadv` et absents de `ref_articles`, avec quantités et dernière date.
- `first_seen` des commandes : vérifier son remplissage après le premier passage du job ; sans lui le
  flux Commandé reste vide.
- Journal de synchronisation : afficher `erp_sync_log` dans Paramètres avec l'âge de chaque table miroir et
  une alerte si une table a plus d'une heure de retard ; bandeau discret dans l'application quand la
  dernière synchronisation a échoué.
- Fraîcheur : rappeler que le bouton de recharge relit le miroir, et que le miroir est rafraîchi par le
  job horaire à H:20.

## 8. Référentiel et écrans

- Articles : retirer *Stock de sécurité* et *Cycle de commande* ; conserver couverture et seuils ; colonnes
  facultatives des règles rattachées (§ 2) ; boutons rapides existants conservés.
- Fournisseurs : ajouter *Délai (j ouvrés)*, *Période gelée*, *Décalage de livraison* ; *Jours de livraison*
  existant.
- Liens article-fournisseur : *Délai* facultatif avec héritage grisé, *Max par livraison*, *Max par semaine*
  avec contrôles de cohérence et avertissement de plafond intenable.
- Paramètres hebdomadaires : couverture et seuils seulement ; plus de sécurité ni de cycle.
- Modèles Excel d'import et exports : colonnes alignées ; les anciens fichiers contenant les colonnes
  retirées sont acceptés, colonnes ignorées avec avertissement.
- Page Paramètres : règles communes restantes seulement ; onglet Affichage avec `focus_weeks` et
  `history_weeks` par utilisateur ; onglet Données avec journal de synchronisation, articles orphelins et
  calendrier d'usine.
- Tableau d'approvisionnement : motif des propositions enrichi ; légende complétée (plafond atteint).
- Guide du moteur : régénéré avec les scénarios plancher, plafond par livraison, plafond par semaine,
  quotidien / hebdomadaire / mardi-jeudi, multi-sourcing plafonné ; sections des règles supprimées retirées.
- Documentation : règles métier, dictionnaire de données, flux ERP et README alignés.

## 9. Migration et précautions

- Migration des `app_param_overrides` : report des valeurs non défaut, purge des clés orphelines.
- Colonnes de référentiel retirées : conservées en base une version, ignorées par le moteur, supprimées
  ensuite.
- Jeu de tests : scénarios du guide du moteur comme tests de non-régression (plancher, plafonds, quotas,
  refus, backlog sur restant).
- Validation sur la base réelle : comparer les propositions avant / après sur l'ensemble du carnet et
  lister les articles dont la première proposition change de plus d'un lot.
