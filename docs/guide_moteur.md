# Guide du moteur et du tableau d'approvisionnement

> Généré par `scripts/guide_moteur.py` : chaque tableau ci-dessous est **calculé par le moteur** de l'application sur un petit jeu de données, les chiffres sont donc exacts par construction. Les règles détaillées sont dans `regles_metier.md` ; ce guide explique **chaque paramètre et son effet**, avec un scénario par paramètre.

Conventions de tous les exemples : aujourd'hui est le **lun 05/10/2026** ; le stock de référence est connu la veille au soir (dim 04/10, le *point zéro*) ; l'article **A1 « Bobine cuivre »** entre à raison de **2 par moteur** dans le programme **Moteur M3**, dont le PDP est de **250 moteurs par semaine** : le besoin est donc de **500 bobines par semaine, 100 par jour ouvré** (semaine ouvrée du lundi au vendredi, pas de fermeture). Fournisseur **S1** : MOQ 200, UM 50, délai 3 jours ouvrés, livraisons tous les jours ouvrés. Article : couverture cible 5 jours, seuil rouge 2 jours, seuil orange 5 jours, surstock 30 jours, cycle de commande 5 jours. Sauf mention contraire, les paramètres globaux sont ceux par défaut.

Les tableaux montrent les jours ouvrés ; **Projeté ERP** et **Projeté Appro.** sont les deux stocks projetés en fin de journée, en gras ; un manque (besoin non servi) est indiqué entre parenthèses ; la **Cible** est le stock cible du jour.

## 1. Lire le tableau : besoin, stock projeté, stock cible

Sans aucune commande, le stock de référence (900) s'épuise de 100 par jour ouvré. Le **Projeté ERP** et le **Projeté Appro.** sont identiques tant qu'il n'y a ni commande ferme, ni saisie, ni proposition. La **Cible** d'un jour est le besoin des 5 jours calendaires **suivants** (`coverage_target_days = 5`, `coverage_unit = calendar`) : un lundi, du mardi au samedi, soit 4 jours ouvrés = 400 ; du mardi au vendredi la fenêtre englobe le week-end sans besoin, soit 300.

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Projeté ERP | **800** | **700** | **600** | **500** | **400** | **300** | **200** | **100** | **·** | **·** (−100) |
| Projeté Appro. | **800** | **700** | **600** | **500** | **400** | **300** | **200** | **100** | **·** | **·** (−100) |
| Cible | 400 | 300 | 300 | 300 | 300 | 400 | 300 | 300 | 300 | 300 |

| KPI | Valeur |
|---|---:|
| Stock à date | 900 |
| Couverture Appro. (j) | 10 |
| Stock cible du jour | 400 |
| Rupture Appro. | 2026-10-16 |
| Besoin 30 j | 1 500 |
| Besoin / jour ouvré | 100 |

**Lecture.** La couverture (en jours) est le nombre de jours dont le besoin cumulé tient dans le stock du jour. La première rupture est le premier jour dont le besoin n'est pas servi : le stock projeté y est nul et le manque est affiché entre parenthèses. Le *Besoin 30 j* reste une somme sur 30 jours calendaires ; sa moyenne se divise par les jours ouvrés de la fenêtre.

- **STOCKOUT** (avertissement, plan) : Rupture Projeté Appro. le 2026-10-16 (J+11) malgré le plan de livraison et le complément CBN, manque max 700
- **STOCKOUT** (avertissement, erp) : Rupture sur les commandes ERP le 2026-10-16 (J+11) : livraison à planifier ou commande à passer

## 2. Commandes ERP, ligne Appro. et commande ignorée

Deux commandes fermes ERP de 400 arrivent le jeudi 08/10 et le jeudi 15/10. Sans saisie, la ligne **Appro.** reprend la commande ferme de chaque jour (cellule vide = ERP) : les deux projetés sont égaux.

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Ferme | · | · | · | 400 | · | · | · | · | 400 | · |
| Appro. | · | · | · | 400 | · | · | · | · | 400 | · |
| Projeté ERP | **800** | **700** | **600** | **900** | **800** | **700** | **600** | **500** | **800** | **700** |
| Projeté Appro. | **800** | **700** | **600** | **900** | **800** | **700** | **600** | **500** | **800** | **700** |

L'approvisionneur **saisit 700** dans la cellule Appro. du 08/10 (il négocie une livraison plus grosse) et **0** le 15/10 (il annule celle-là). Le **Projeté ERP** ne bouge pas : il reflète l'ERP tel quel. Le **Projeté Appro.** suit la décision de l'approvisionneur : c'est le scénario sur lequel le moteur calcule les propositions CBN et les alertes de rupture du plan.

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Ferme | · | · | · | 400 | · | · | · | · | 400 | · |
| Appro. | · | · | · | 700 | · | · | · | · | · | · |
| Projeté ERP | **800** | **700** | **600** | **900** | **800** | **700** | **600** | **500** | **800** | **700** |
| Projeté Appro. | **800** | **700** | **600** | **1 200** | **1 100** | **1 000** | **900** | **800** | **700** | **600** |

**Commande ignorée** : un clic sur la cellule *Ferme* du 15/10 la barre. Elle sort du Projeté ERP **et** du pré-remplissage de la ligne Appro. (l'approvisionneur sait que cette commande ne viendra pas) ; un second clic la rétablit.

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Ferme | · | · | · | 400 | · | · | · | · | · | · |
| Appro. | · | · | · | 400 | · | · | · | · | · | · |
| Projeté ERP | **800** | **700** | **600** | **900** | **800** | **700** | **600** | **500** | **400** | **300** |
| Projeté Appro. | **800** | **700** | **600** | **900** | **800** | **700** | **600** | **500** | **400** | **300** |

> Dans le tableau, la ligne *Ferme* affiche `restant / commandé` après une réception partielle ; une commande ferme **passée** et non reçue ne compte dans aucun stock : elle entre dans le **backlog** du fournisseur (§ 11).

## 3. Couverture cible, stock de sécurité, unité de couverture

Le **stock cible** d'un jour est, selon `target_policy` : le besoin des `coverage_target_days` jours suivants (`coverage_days`), le `safety_stock_qty` fixe (`safety_qty`), ou le **maximum des deux** (`max`, défaut). C'est le niveau sous lequel le moteur déclenche une proposition.

| Jour | Cible, couverture 5 j calendaires | Cible, couverture 10 j | Cible, sécurité 800 (max) | Cible, 5 j **ouvrés** |
|---|---:|---:|---:|---:|
| lun 05/10 | 400 | 800 | 800 | 500 |
| mar 06/10 | 300 | 800 | 800 | 500 |
| mer 07/10 | 300 | 700 | 800 | 500 |
| jeu 08/10 | 300 | 600 | 800 | 500 |
| ven 09/10 | 300 | 600 | 800 | 500 |

**Lecture.** En jours calendaires, la cible compte les 5 jours **suivants** : 400 un lundi (mardi → samedi), 300 du mardi au vendredi (le week-end entre dans la fenêtre) ; en jours **ouvrés** (`coverage_unit = working`) la cible vaut toujours 5 jours de besoin, soit 500. Le stock de sécurité fixe prend le relais dès que la couverture lui est inférieure. `coverage_tie_rule` décide si un jour dont le besoin cumulé **égale** exactement le stock est compté couvert (`covered`, défaut) ou non.

## 4. Propositions CBN : besoin net, MOQ, UM, cycle de commande

Avec `generate_proposals` (défaut : oui), le moteur parcourt le Projeté Appro. à partir de demain et, dès que `stock < cible`, propose une livraison. La quantité vise le **niveau de recomplètement** = cible + besoin du cycle de commande suivant (`order_cycle_days = 5`) : on ne recommande pas tous les jours. Elle est relevée au **MOQ** (200) puis arrondie au multiple supérieur de l'**UM** (50). La date de commande = livraison − délai fournisseur en jours ouvrés (3).

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 | lun 19/10 | mar 20/10 | mer 21/10 | jeu 22/10 | ven 23/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Prop. CBN | · | · | · | · | 400 | · | · | · | · | 300 | · | · | · | · | · |
| Projeté ERP | **800** | **700** | **600** | **500** | **400** | **300** | **200** | **100** | **·** | **·** (−100) | **·** (−200) | **·** (−300) | **·** (−400) | **·** (−500) | **·** (−600) |
| Projeté Appro. | **800** | **700** | **600** | **500** | **800** | **700** | **600** | **500** | **400** | **600** | **500** | **400** | **300** | **200** | **100** |
| Cible | 400 | 300 | 300 | 300 | 300 | 400 | 300 | 300 | 300 | 300 | 400 | 300 | 300 | 200 | 100 |

| Livraison | Fournisseur | Quantité | Besoin net | À commander le | Urgente | Motif |
|---|---|---:|---:|---|---|---|
| ven 09/10 | S1 | 400 | 400 | mar 06/10 | non | Stock projeté 400 < cible 500 le 2026-10-11 |
| ven 16/10 | S1 | 300 | 300 | mar 13/10 | non | Stock projeté 300 < cible 400 le 2026-10-17 |

**Lecture.** Le Projeté Appro. intègre les propositions (`include_proposals_in_plan`, défaut : oui) : il reste au-dessus de la cible, tandis que le Projeté ERP, sans commande, tombe à zéro. Les propositions sont **recalculées à chaque affichage** et n'existent nulle part ailleurs ; pour en reprendre une, l'approvisionneur tape la quantité dans la cellule Appro. (la cellule grisée la prérempli).

Avec un **cycle de commande à 0**, le niveau de recomplètement se réduit à la cible : les propositions sont plus petites et plus fréquentes.

| Livraison | Fournisseur | Quantité | Besoin net | À commander le | Urgente | Motif |
|---|---|---:|---:|---|---|---|
| ven 09/10 | S1 | 200 | 100 | mar 06/10 | non | Stock projeté 400 < cible 500 le 2026-10-11 |
| jeu 15/10 | S1 | 200 | 100 | lun 12/10 | non | Stock projeté 200 < cible 300 le 2026-10-15 |
| ven 16/10 | S1 | 200 | 100 | mar 13/10 | non | Stock projeté 300 < cible 400 le 2026-10-17 |
| mer 21/10 | S1 | 200 | 100 | ven 16/10 | non | Stock projeté 200 < cible 300 le 2026-10-21 |

Avec **MOQ 1 000 et UM 250**, la première proposition saute au MOQ ; la suivante est repoussée d'autant.

| Livraison | Fournisseur | Quantité | Besoin net | À commander le | Urgente | Motif |
|---|---|---:|---:|---|---|---|
| ven 09/10 | S1 | 1 000 | 400 | mar 06/10 | non | Stock projeté 400 < cible 500 le 2026-10-11 |

## 5. Délai fournisseur : propositions urgentes et `respect_lead_time`

Stock de départ 350 et délai de **10 jours ouvrés**. Le creux arrive le mar 06/10 ; la livraison est placée ce jour-là, mais la commande aurait dû partir 10 jours ouvrés plus tôt (mar 22/09) : la proposition est **urgente**, sa date « à commander le » est dans le passé (c'est la date réelle, affichée telle quelle), et une alerte `URGENT_PROPOSAL` est levée.

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Prop. CBN | · | 650 | · | · | · | · | · | 500 | · | · |
| Projeté Appro. | **250** | **800** | **700** | **600** | **500** | **400** | **300** | **700** | **600** | **500** |
| Cible | 400 | 300 | 300 | 300 | 300 | 400 | 300 | 300 | 300 | 300 |

| Livraison | Fournisseur | Quantité | Besoin net | À commander le | Urgente | Motif |
|---|---|---:|---:|---|---|---|
| mar 06/10 | S1 | 650 | 650 | mar 22/09 | **oui** | Stock projeté 150 < cible 300 le 2026-10-06 – délai fournisseur (10 j ouvrés) non tenable, commande à passer immédiatement |
| mer 14/10 | S1 | 500 | 500 | mer 30/09 | **oui** | Stock projeté 200 < cible 300 le 2026-10-14 – délai fournisseur (10 j ouvrés) non tenable, commande à passer immédiatement |
| mer 21/10 | S1 | 200 | 100 | mer 07/10 | non | Stock projeté 200 < cible 300 le 2026-10-21 |

- **STOCKOUT** (critique, erp) : Rupture sur les commandes ERP le 2026-10-08 (J+3) : livraison à planifier ou commande à passer
- **LOW_COVERAGE** (avertissement, erp) : Commandes ERP épuisées dans 3 j (≤ seuil orange 5 j) ; stock à date : 2 j de besoin
- **URGENT_PROPOSAL** (critique, plan) : 2 proposition(s) hors délai fournisseur – première livraison requise le 2026-10-06 (650)

Avec `respect_lead_time = oui`, le moteur **ne propose jamais l'impossible** : la première livraison possible est aujourd'hui + 10 jours ouvrés, le besoin est réévalué ce jour-là, et les jours de manque entre-temps sont une rupture du Projeté Appro. (alerte `STOCKOUT`, scénario plan).

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 | lun 19/10 | mar 20/10 | mer 21/10 | jeu 22/10 | ven 23/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Prop. CBN | · | · | · | · | · | · | · | · | · | · | 1 250 | · | · | · | · |
| Projeté Appro. | **250** | **150** | **50** | **·** (−50) | **·** (−150) | **·** (−250) | **·** (−350) | **·** (−450) | **·** (−550) | **·** (−650) | **500** | **400** | **300** | **200** | **100** |
| Cible | 400 | 300 | 300 | 300 | 300 | 400 | 300 | 300 | 300 | 300 | 400 | 300 | 300 | 200 | 100 |

| Livraison | Fournisseur | Quantité | Besoin net | À commander le | Urgente | Motif |
|---|---|---:|---:|---|---|---|
| lun 19/10 | S1 | 1 250 | 1 250 | lun 05/10 | non | Stock projeté -750 < cible 400 le 2026-10-19 (besoin dès le 2026-10-06, première livraison possible le 2026-10-19) |

- **STOCKOUT** (critique, plan) : Rupture Projeté Appro. le 2026-10-08 (J+3) malgré le plan de livraison et le complément CBN, manque max 650
- **STOCKOUT** (critique, erp) : Rupture sur les commandes ERP le 2026-10-08 (J+3) : livraison à planifier ou commande à passer
- **LOW_COVERAGE** (avertissement, erp) : Commandes ERP épuisées dans 3 j (≤ seuil orange 5 j) ; stock à date : 2 j de besoin

## 6. Jours de livraison du fournisseur et `delivery_shift`

Le fournisseur ne livre que le **mardi et le jeudi** (Référentiel › Fournisseurs, jours de livraison). Quand le creux tombe un autre jour, `delivery_shift = earlier` (défaut) cherche le jour de livraison autorisé le plus proche **avant** (la marchandise est là avant le creux) ; `later` cherche **après** (quitte à accepter un manque, remonté en alerte).

Avec `earlier` :

| Livraison | Fournisseur | Quantité | Besoin net | À commander le | Urgente | Motif |
|---|---|---:|---:|---|---|---|
| jeu 08/10 | S1 | 400 | 400 | lun 05/10 | non | Stock projeté 400 < cible 500 le 2026-10-11 |
| jeu 15/10 | S1 | 300 | 300 | lun 12/10 | non | Stock projeté 300 < cible 400 le 2026-10-17 |

Avec `later` :

| Livraison | Fournisseur | Quantité | Besoin net | À commander le | Urgente | Motif |
|---|---|---:|---:|---|---|---|
| mar 13/10 | S1 | 600 | 600 | jeu 08/10 | non | Stock projeté 200 < cible 300 le 2026-10-13 (besoin dès le 2026-10-11, première livraison possible le 2026-10-13) |
| jeu 22/10 | S1 | 200 | 100 | lun 19/10 | non | Stock projeté 100 < cible 200 le 2026-10-22 (besoin dès le 2026-10-21, première livraison possible le 2026-10-22) |

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Prop. CBN | · | · | · | · | · | · | 600 | · | · | · |
| Projeté Appro. | **800** | **700** | **600** | **500** | **400** | **300** | **800** | **700** | **600** | **500** |
| Cible | 400 | 300 | 300 | 300 | 300 | 400 | 300 | 300 | 300 | 300 |

## 7. Livraisons groupées le lundi, période gelée, limite des propositions

`proposal_placement = monday` ramène toutes les livraisons proposées au **lundi**, quels que soient les jours du fournisseur ; les besoins d'une même semaine sont fusionnés en une proposition par fournisseur et par lundi.

| Livraison | Fournisseur | Quantité | Besoin net | À commander le | Urgente | Motif |
|---|---|---:|---:|---|---|---|
| lun 12/10 | S1 | 700 | 700 | mer 07/10 | non | Stock projeté 300 < cible 400 le 2026-10-12 (besoin dès le 2026-10-11, première livraison possible le 2026-10-12) ; Stock projeté 400 < cible 500 le 2026-10-18 |

`frozen_days = 5` : aucune livraison n'est proposée avant aujourd'hui + 5 jours (période gelée, par exemple parce que les commandes de la semaine sont déjà passées) ; le besoin du creux est réévalué au premier jour proposable.

| Livraison | Fournisseur | Quantité | Besoin net | À commander le | Urgente | Motif |
|---|---|---:|---:|---|---|---|
| lun 12/10 | S1 | 1 050 | 1 050 | mer 07/10 | non | Stock projeté -250 < cible 400 le 2026-10-12 (besoin dès le 2026-10-11, première livraison possible le 2026-10-12) |
| ven 16/10 | S1 | 200 | 200 | mar 13/10 | non | Stock projeté 400 < cible 500 le 2026-10-18 |

`proposal_lookahead_days = 14` : les propositions s'arrêtent à J + 14 même si l'horizon de calcul est plus long (2 proposition(s) sur un horizon de 60 jours) ; `stockout_lookahead_days` joue le même rôle pour la détection des ruptures.

| Livraison | Fournisseur | Quantité | Besoin net | À commander le | Urgente | Motif |
|---|---|---:|---:|---|---|---|
| ven 09/10 | S1 | 400 | 400 | mar 06/10 | non | Stock projeté 400 < cible 500 le 2026-10-11 |
| ven 16/10 | S1 | 400 | 400 | mar 13/10 | non | Stock projeté 300 < cible 400 le 2026-10-17 |

## 8. Besoin non servi : `shortage_policy` et tolérance de manque

Stock 350, une seule commande ferme de 600 le lundi 12/10. Le besoin des jours précédents n'est pas servi. Avec `backlog` (défaut) le besoin non servi est **reporté** : la livraison du 12/10 sert d'abord le retard (stock net négatif, affiché comme manque). Avec `lost` le besoin manqué est **perdu** : le stock physique est borné à 0 et la livraison repart de zéro.

Politique `backlog` :

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Ferme | · | · | · | · | · | 600 | · | · | · | · |
| Projeté Appro. | **250** | **150** | **50** | **·** (−50) | **·** (−150) | **350** | **250** | **150** | **50** | **·** (−50) |

Politique `lost` :

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Ferme | · | · | · | · | · | 600 | · | · | · | · |
| Projeté Appro. | **250** | **150** | **50** | **·** (−50) | **·** (−100) | **500** | **400** | **300** | **200** | **100** |

**Tolérance de manque** (`shortfall_tolerance_days`) : stock 420, livraison ferme de 500 le mercredi. Le stock passe sous la cible mardi (220 < 300), **sans manque**, et repasse au-dessus mercredi grâce à la livraison. Avec la tolérance à 0 (défaut) le moteur propose dès mardi ; avec 2 jours ouvrés, un creux qui se résorbe seul en 2 jours sans besoin non servi est ignoré et la première proposition vient plus tard.

Tolérance 0 :

| Livraison | Fournisseur | Quantité | Besoin net | À commander le | Urgente | Motif |
|---|---|---:|---:|---|---|---|
| mar 06/10 | S1 | 600 | 580 | jeu 01/10 | **oui** | Stock projeté 220 < cible 300 le 2026-10-06 – délai fournisseur (3 j ouvrés) non tenable, commande à passer immédiatement |
| mer 21/10 | S1 | 200 | 80 | ven 16/10 | non | Stock projeté 220 < cible 300 le 2026-10-21 |

Tolérance 2 jours ouvrés :

| Livraison | Fournisseur | Quantité | Besoin net | À commander le | Urgente | Motif |
|---|---|---:|---:|---|---|---|
| ven 09/10 | S1 | 400 | 380 | mar 06/10 | non | Stock projeté 420 < cible 500 le 2026-10-11 |
| ven 16/10 | S1 | 300 | 280 | mar 13/10 | non | Stock projeté 320 < cible 400 le 2026-10-17 |

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 |
|---|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 |
| Ferme | · | · | 500 | · | · | · |
| Projeté Appro. | **320** | **220** | **620** | **520** | **820** | **720** |
| Cible | 400 | 300 | 300 | 300 | 300 | 400 |

## 9. Multi-sourcing : quotas, priorité, refus d'une proposition

Deux fournisseurs : S1 (quota 70 %, priorité 1, délai 3) et S2 (quota 30 %, priorité 2, délai 5). Avec `sourcing_policy = quota` (défaut), chaque proposition va au fournisseur dont la part cumulée est la plus en retard sur son quota ; avec `priority`, tout va au fournisseur de priorité 1. Chaque fournisseur a sa propre voie dans le tableau (lignes Ferme / Reçu / Appro. / Prop. CBN).

Quotas 70 / 30 (six semaines) :

| Livraison | Fournisseur | Quantité | Besoin net | À commander le | Urgente | Motif |
|---|---|---:|---:|---|---|---|
| ven 09/10 | S1 | 400 | 400 | mar 06/10 | non | Stock projeté 400 < cible 500 le 2026-10-11 |
| ven 16/10 | S2 | 400 | 400 | ven 09/10 | non | Stock projeté 300 < cible 400 le 2026-10-17 |
| ven 23/10 | S1 | 400 | 400 | mar 20/10 | non | Stock projeté 200 < cible 300 le 2026-10-23 |
| jeu 29/10 | S1 | 400 | 400 | lun 26/10 | non | Stock projeté 200 < cible 300 le 2026-10-29 |
| mer 04/11 | S2 | 500 | 500 | mer 28/10 | non | Stock projeté 200 < cible 300 le 2026-11-04 |
| mer 11/11 | S1 | 200 | 100 | ven 06/11 | non | Stock projeté 200 < cible 300 le 2026-11-11 |

Parts obtenues sur six semaines : S1 1 400, S2 900 (61 % / 39 %) : la répartition est déterministe et se rapproche du quota proposition après proposition, sans jamais couper une proposition en deux.

Priorité :

| Livraison | Fournisseur | Quantité | Besoin net | À commander le | Urgente | Motif |
|---|---|---:|---:|---|---|---|
| ven 09/10 | S1 | 400 | 400 | mar 06/10 | non | Stock projeté 400 < cible 500 le 2026-10-11 |
| ven 16/10 | S1 | 400 | 400 | mar 13/10 | non | Stock projeté 300 < cible 400 le 2026-10-17 |
| ven 23/10 | S1 | 400 | 400 | mar 20/10 | non | Stock projeté 200 < cible 300 le 2026-10-23 |
| jeu 29/10 | S1 | 400 | 400 | lun 26/10 | non | Stock projeté 200 < cible 300 le 2026-10-29 |
| mer 04/11 | S1 | 500 | 500 | ven 30/10 | non | Stock projeté 200 < cible 300 le 2026-11-04 |
| mer 11/11 | S1 | 200 | 100 | ven 06/11 | non | Stock projeté 200 < cible 300 le 2026-11-11 |

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Prop. CBN · S1 | · | · | · | · | 400 | · | · | · | · | · |
| Prop. CBN · S2 | · | · | · | · | · | · | · | · | · | 400 |
| Projeté Appro. | **800** | **700** | **600** | **500** | **800** | **700** | **600** | **500** | **400** | **700** |

**Refus** : un clic sur la proposition du ven 09/10 (S1) la barre et interdit toute proposition à **ce fournisseur** jusqu'au dimanche de sa semaine ; le besoin est reporté après la fenêtre (ou va à l'autre fournisseur si les quotas le permettent). La proposition refusée reste listée dans le flux *À commander* du cockpit, marquée « ignorée », si elle était urgente.

| Livraison | Fournisseur | Quantité | Besoin net | À commander le | Urgente | Motif |
|---|---|---:|---:|---|---|---|
| lun 12/10 | S1 | 500 | 500 | mer 07/10 | non | Stock projeté 300 < cible 400 le 2026-10-12 (besoin dès le 2026-10-11, première livraison possible le 2026-10-12) |
| ven 16/10 | S2 | 400 | 400 | ven 09/10 | non | Stock projeté 400 < cible 500 le 2026-10-18 |
| ven 23/10 | S1 | 400 | 400 | mar 20/10 | non | Stock projeté 300 < cible 400 le 2026-10-24 |
| ven 30/10 | S1 | 400 | 400 | mar 27/10 | non | Stock projeté 200 < cible 300 le 2026-10-30 |
| jeu 05/11 | S2 | 400 | 400 | jeu 29/10 | non | Stock projeté 200 < cible 300 le 2026-11-05 |
| mer 11/11 | S1 | 200 | 100 | ven 06/11 | non | Stock projeté 200 < cible 300 le 2026-11-11 |

## 10. Alertes : seuils rouge et orange, surstock, gradation des ruptures

Les alertes de **couverture** regardent l'épuisement des flux ERP (stock + commandes fermes) : `LOW_COVERAGE` critique si les commandes ERP sont épuisées sous `alert_red_days`, avertissement sous `alert_yellow_days`, `OVERSTOCK` (info) si le stock à date couvre plus d'`overstock_days`. Les **ruptures** (`STOCKOUT`) sont graduées sur les deux scénarios : critique dans le délai fournisseur, avertissement dans `firm_horizon_days` (28), information au-delà.

**Stock 150 (rupture dès demain)**

- **STOCKOUT** (critique, plan) : Rupture Projeté Appro. le 2026-10-06 (J+1) malgré le plan de livraison et le complément CBN, manque max 3 850
- **STOCKOUT** (critique, erp) : Rupture sur les commandes ERP le 2026-10-06 (J+1) : livraison à planifier ou commande à passer
- **LOW_COVERAGE** (critique, erp) : Commandes ERP épuisées dans 1 j (≤ seuil rouge 2 j) ; stock à date : 0 j de besoin

**Stock 450 (rupture vendredi)**

- **STOCKOUT** (avertissement, plan) : Rupture Projeté Appro. le 2026-10-09 (J+4) malgré le plan de livraison et le complément CBN, manque max 3 550
- **STOCKOUT** (avertissement, erp) : Rupture sur les commandes ERP le 2026-10-09 (J+4) : livraison à planifier ou commande à passer
- **LOW_COVERAGE** (avertissement, erp) : Commandes ERP épuisées dans 4 j (≤ seuil orange 5 j) ; stock à date : 3 j de besoin

**Stock 8 000 (80 jours de besoin)**

- **OVERSTOCK** (info, plan) : Surstock : le stock à date couvre 60 j de besoin (≥ 30 j)

**Stock 3 000, ferme ERP 500 le 12/10 : rupture ERP à J + 32**

- **STOCKOUT** (info, plan) : Rupture Projeté Appro. le 2026-11-23 (J+49) malgré le plan de livraison et le complément CBN, manque max 500
- **STOCKOUT** (info, erp) : Rupture sur les commandes ERP le 2026-11-23 (J+49) : livraison à planifier ou commande à passer
- **OVERSTOCK** (info, plan) : Surstock : le stock à date couvre 41 j de besoin (≥ 30 j)

**Données manquantes** (pas de fournisseur lié) :

- **MISSING_DATA** (avertissement, data) : Aucun fournisseur actif lié à l'article (MOQ / délai inconnus)
- **STOCKOUT** (avertissement, plan) : Rupture Projeté Appro. le 2026-10-16 (J+11) malgré le plan de livraison et le complément CBN, manque max 700
- **STOCKOUT** (avertissement, erp) : Rupture sur les commandes ERP le 2026-10-16 (J+11) : livraison à planifier ou commande à passer

## 11. Consommation réelle, semaine en cours, réceptions et backlog

Ici aujourd'hui est le **mer 07/10** et le stock a été initialisé le dim 27/09. Le passé n'est jamais reconstruit : le besoin des jours passés est la **consommation réelle** déclarée (`conso_composants`, déjà éclatée par composant) ; un jour sans déclaration compte 0 (`missing_actual_policy = zero`). La **semaine en cours** consomme son PDP éclaté (500) moins le réel déjà déclaré (150 + 50 = 200) : le reliquat (300) est réparti sur les jours ouvrés restants, aujourd'hui compris (`production_mode = actual_then_remainder`). La commande ferme du 30/09 (300) n'a été reçue qu'à 200 : les 100 manquants sont le **backlog** du fournisseur, affiché mais compté dans aucun stock.

| | lun 28/09 | mar 29/09 | mer 30/09 | jeu 01/10 | ven 02/10 | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 120 | 80 | 100 | 90 | 110 | 150 | 50 | 100 | 100 | 100 |
| Ferme | · | · | · | · | · | · | · | 300 | · | · |
| Ferme (passé) | · | · | 300 | · | · | · | · | · | · | · |
| Reçu | · | · | 200 | · | · | · | · | · | · | · |
| Appro. | · | · | · | · | · | · | · | 300 | · | · |
| Projeté ERP | **780** | **700** | **800** | **710** | **600** | **450** | **400** | **600** | **500** | **400** |
| Projeté Appro. | **780** | **700** | **800** | **710** | **600** | **450** | **400** | **600** | **500** | **400** |

| KPI | Valeur |
|---|---:|
| Stock de référence | 900 |
| Stock à date | 400 |
| Backlog | 100 |
| Couverture Appro. (j) | 8 |

**Lecture.** *Stock à date* = stock projeté à la fin de la veille (réceptions et consommations réelles comprises). La fenêtre de backlog remonte de `backlog_days` (28) jours, jamais avant le point zéro.

## 12. Ajustements et correction du stock de référence

Une cellule **Ajustement** est une quantité signée. Datée **au plus tard le point zéro**, elle corrige le stock de référence (l'ERP se trompe, l'application n'y écrit jamais) ; datée après, c'est un mouvement connu d'avance (rebut, transfert). Ici −150 le jour de l'initialisation et −200 le jeudi.

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 |
|---|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 |
| Ajustement | · | · | · | -200 | · | · |
| Projeté ERP | **650** | **550** | **450** | **150** | **50** | **·** (−50) |
| Projeté Appro. | **650** | **550** | **450** | **150** | **50** | **·** (−50) |

| KPI | Valeur |
|---|---:|
| Stock de référence | 750 |
| Correction du stock de référence | -150 |
| Stock à date | 750 |

## 13. Paramètres d'article par semaine

Les paramètres de l'article (couverture cible, seuils, surstock, stock de sécurité, cycle) peuvent être surchargés **par semaine ISO** (Référentiel › Articles › Semaines). Ici la couverture cible passe de 5 à **10 jours pour la semaine 42** seulement : la cible double cette semaine-là, puis revient à 5 jours.

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 | lun 19/10 | mar 20/10 | mer 21/10 | jeu 22/10 | ven 23/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Cible | 400 | 300 | 300 | 300 | 300 | 800 | 800 | 700 | 600 | 600 | 400 | 300 | 300 | 200 | 100 |

## 14. Fenêtre de calcul, affichage, cache

- **Point zéro** = date du stock de référence (`fct_stock.snapshot_date`), une seule pour tous les articles, jamais après aujourd'hui. Rien n'existe avant : pas d'historique reconstruit.
- **Fenêtre** = du point zéro à aujourd'hui + `horizon_days` (120), étendue au dimanche de la semaine en cours ; le tableau commence au lundi de la semaine en cours moins `history_weeks` (2) semaines.
- **Calendrier** « Par défaut » : un jour par colonne pour la semaine en cours et `focus_weeks` (2) semaines, une semaine par colonne au-delà ; les cellules des colonnes semaine sont en lecture (cliquer l'en-tête pour le détail).
- Le **sélecteur de période** sous la barre d'outils ne change que les colonnes affichées.
- Tout est recalculé à chaque demande et mis en cache jusqu'à la prochaine écriture (saisie, import, paramètre) ou synchronisation ERP.

## 15. Récapitulatif des paramètres

| Paramètre | Défaut | Portée | Effet |
|---|---|---|---|
| `coverage_target_days` | 7 (article) | article, semaine | jours de besoin à tenir en stock : fixe la cible, donc le déclenchement et la taille des propositions |
| `safety_stock_qty` | 0 (article) | article, semaine | plancher fixe de la cible (`target_policy = max`) |
| `order_cycle_days` | 7 (article) | article, semaine | besoin ajouté au niveau de recomplètement : moins de propositions, plus grosses |
| `alert_red_days / alert_yellow_days` | 3 / 7 (article) | article, semaine | seuils des alertes de couverture (épuisement des commandes ERP) |
| `overstock_days` | 30 (article) | article, semaine | seuil de l'alerte surstock |
| `moq / pack_qty / lead_time_days` | lien article ↔ fournisseur | fournisseur | quantité minimale, arrondi UM, délai ouvré (date de commande, urgence) |
| `quota_pct / priority` | lien | fournisseur | répartition multi-sourcing |
| `delivery_weekdays` | 1-5 (fournisseur) | fournisseur | jours de livraison autorisés |
| `target_policy` | max | global | cible = couverture, sécurité fixe ou max des deux |
| `coverage_unit` | calendar | global | couverture et cible en jours calendaires ou ouvrés |
| `coverage_tie_rule` | covered | global | un jour dont le besoin cumulé égale le stock est-il couvert ? |
| `shortage_policy` | backlog | global | besoin non servi reporté ou perdu |
| `shortfall_tolerance_days` | 0 | global | creux sous cible tolérés s'ils se résorbent seuls |
| `generate_proposals` | oui | global | calcul des propositions CBN |
| `include_proposals_in_plan` | oui | global | les propositions entrent dans le Projeté Appro. |
| `proposal_placement` | working_days | global | livraisons tout jour ouvré autorisé, ou lundis |
| `delivery_shift` | earlier | global | jour non autorisé : avancer ou reculer |
| `respect_lead_time` | non | global | jamais de livraison avant aujourd'hui + délai |
| `frozen_days` | 0 | global | période gelée sans proposition |
| `proposal_lookahead_days / stockout_lookahead_days` | vide | global | limites de calcul des propositions / ruptures |
| `sourcing_policy` | quota | global | quotas ou priorité |
| `firm_horizon_days` | 28 | global | gradation des alertes de rupture |
| `firm_sources` | FIRM | global | types de commandes comptés dans le Projeté ERP |
| `forecast_date_policy` | actual | global | commandes prévisionnelles à leur date ou au lundi |
| `backlog_days` | 28 | global | fenêtre du backlog fournisseur |
| `production_mode / missing_actual_policy` | actual_then_remainder / zero | global | besoin passé et reliquat de la semaine en cours |
| `spread_rounding / consumption_offset_days` | exact / 0 | global | lissage du PDP hebdomadaire sur les jours ouvrés, décalage de consommation |
| `horizon_days / history_weeks / focus_weeks` | 120 / 2 / 2 | global | fenêtre de calcul et d'affichage |

Les paramètres globaux se règlent dans *Paramètres & règles* (managers, administrateurs) ; ceux de l'article dans le *Référentiel*, avec leurs surcharges hebdomadaires ; ceux du fournisseur et du lien article ↔ fournisseur dans le *Référentiel* également.
