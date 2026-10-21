# Guide du moteur et du tableau d'approvisionnement

> Généré par `scripts/guide_moteur.py` : chaque tableau ci-dessous est **calculé par le moteur** de l'application sur un petit jeu de données, les chiffres sont donc exacts par construction. Les règles détaillées sont dans `regles_metier.md` ; ce guide explique **chaque règle et chaque paramètre**, avec un scénario par règle. Les mêmes scénarios sont rejoués dans `backend/tests/test_engine_units.py`.

Conventions de tous les exemples : aujourd'hui est le **lun 05/10/2026** ; le stock de référence est connu la veille au soir (dim 04/10, le *point zéro*) ; l'article **A1 « Bobine cuivre »** entre à raison de **2 par moteur** dans le programme **Moteur M3**, dont le PDP est de **250 moteurs par semaine** : le besoin est donc de **500 bobines par semaine, 100 par jour ouvré** (semaine ouvrée du lundi au vendredi, pas de fermeture, jours de consommation = 5). Fournisseur **S1** : délai **3 jours ouvrés** (inclusif : commandé lundi, livrable jeudi), livraisons tous les jours ouvrés ; lien A1 ↔ S1 : MOQ 200, UM 50, sans plafond. Article : couverture cible 5 jours ouvrés, seuil rouge 2, seuil orange 5, surstock 30. Règle communes : tolérance sous la cible 15 %, marge de délai 1 jour ouvré.

Tous les délais, couvertures, seuils et horizons sont en **jours ouvrés**. Les tableaux montrent les jours ouvrés ; **Projeté Ferme** (stock + ordres fermes seuls, un KPI dans l'application) et **Stock projeté** (la ligne du tableau : fermes, quantités à retenir, propositions) sont les deux stocks projetés en fin de journée, en gras ; le **manque cumulé** (besoin non servi depuis le dernier jour avec stock) est indiqué entre parenthèses ; la **Cible** est le stock cible du jour.

## 1. Lire le tableau : besoin, stock projeté, stock cible, manque cumulé

Sans aucune commande (et sans retour à la cible : tolérance mise à 100 % pour cet exemple), le stock de référence (900) s'épuise de 100 par jour ouvré. Le **Projeté Ferme** et le **Stock projeté** sont identiques tant qu'il n'y a ni commande ferme, ni saisie, ni proposition. La **Cible** d'un jour est le besoin des **5 jours ouvrés suivants** (`coverage_target_days = 5`) : 500 tous les jours, le week-end ne compte pas. Dès que le stock ne suffit plus, il reste à **0** (le besoin non servi est **perdu**, jamais reporté) et le manque s'accumule jour après jour (Projeté Ferme) ; il repart de zéro au premier jour où le stock redevient positif. Un besoin non servi déclenche toujours le CBN, même à 100 % de tolérance : le Stock projeté remonte à la cible dès le premier manque (§ 4).

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 | lun 19/10 | mar 20/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Projeté Ferme | **800** | **700** | **600** | **500** | **400** | **300** | **200** | **100** | **·** | **·** (−100) | **·** (−200) | **·** (−300) |
| Stock projeté | **800** | **700** | **600** | **500** | **400** | **300** | **200** | **100** | **·** | **500** | **400** | **300** |
| Cible | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 400 |

| KPI | Valeur |
|---|---:|
| Stock à date | 900 |
| Couverture projetée (j ouvrés) | 8 |
| Stock cible du jour | 500 |
| Rupture projetée | – |
| Manque cumulé max (stock projeté) | · |
| Besoin 20 j ouvrés | 1 500 |
| Besoin / jour ouvré | 100 |

**Lecture.** La couverture (en jours ouvrés) est le nombre de jours ouvrés dont le besoin cumulé tient dans le stock du jour. La première rupture est le premier jour dont le besoin n'est pas servi. Le *Besoin 20 j ouvrés* est la somme du besoin des 20 prochains jours ouvrés (quatre semaines) ; sa moyenne est par jour ouvré compté.

_Aucune alerte._

## 2. Commandes ERP, ligne Quantité à retenir et commande ignorée

Deux commandes fermes ERP de 400 arrivent le jeudi 08/10 et le jeudi 15/10. Sans saisie, la ligne **Quantité à retenir** reprend la commande ferme de chaque jour (cellule vide = ERP) : les deux projetés sont égaux. Seules les commandes **fermes** comptent ; les prévisionnelles sont affichées, jamais comptées.

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Ordre ferme | · | · | · | 400 | · | · | · | · | 400 | · |
| Quantité à retenir | · | · | · | 400 | · | · | · | · | 400 | · |
| Projeté Ferme | **800** | **700** | **600** | **900** | **800** | **700** | **600** | **500** | **800** | **700** |
| Stock projeté | **800** | **700** | **600** | **900** | **800** | **700** | **600** | **500** | **800** | **700** |

L'approvisionneur **saisit 700** dans la cellule Quantité à retenir du 08/10 (il négocie une livraison plus grosse) et **0** le 15/10 (il annule celle-là). Le **Projeté Ferme** ne bouge pas : il reflète l'ERP tel quel. Le **Stock projeté** suit la décision de l'approvisionneur : c'est le scénario sur lequel le moteur calcule les propositions CBN et les alertes de rupture du plan.

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Ordre ferme | · | · | · | 400 | · | · | · | · | 400 | · |
| Quantité à retenir | · | · | · | 700 | · | · | · | · | · | · |
| Projeté Ferme | **800** | **700** | **600** | **900** | **800** | **700** | **600** | **500** | **800** | **700** |
| Stock projeté | **800** | **700** | **600** | **1 200** | **1 100** | **1 000** | **900** | **800** | **700** | **600** |

**Commande ignorée** : un clic sur la cellule *Ordre ferme* du 15/10 la barre. Elle sort du Projeté Ferme **et** du pré-remplissage de la ligne Quantité à retenir (l'approvisionneur sait que cette commande ne viendra pas) ; un second clic la rétablit.

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Ordre ferme | · | · | · | 400 | · | · | · | · | · | · |
| Quantité à retenir | · | · | · | 400 | · | · | · | · | · | · |
| Projeté Ferme | **800** | **700** | **600** | **900** | **800** | **700** | **600** | **500** | **400** | **300** |
| Stock projeté | **800** | **700** | **600** | **900** | **800** | **700** | **600** | **500** | **400** | **300** |

> Dans le tableau, la ligne *Ordre ferme* affiche `restant / commandé` après une réception partielle ; une commande ferme dont la **date est passée** et dont l'ERP garde un restant ne compte dans aucun stock : elle est **en retard** (§ 11).

## 3. Couverture cible et jours ouvrés

Le **stock cible** d'un jour est le besoin des `coverage_target_days` jours ouvrés suivants : c'est le niveau que visent les propositions. Il n'y a ni stock de sécurité ni cycle de commande : la cadence des livraisons vient des jours de livraison du fournisseur et de ses plafonds.

| Jour | Cible, couverture 3 j ouvrés | Cible, couverture 5 j ouvrés | Cible, couverture 10 j ouvrés |
|---|---:|---:|---:|
| lun 05/10 | 300 | 500 | 1 000 |
| mar 06/10 | 300 | 500 | 1 000 |
| mer 07/10 | 300 | 500 | 1 000 |
| jeu 08/10 | 300 | 500 | 1 000 |
| ven 09/10 | 300 | 500 | 1 000 |

**Lecture.** Le vendredi, la fenêtre de 5 jours ouvrés enjambe le week-end (lundi → vendredi suivants) : la cible reste 500. Un jour dont le besoin cumulé **égale** exactement le stock est compté couvert.

## 4. Propositions CBN : tolérance, retour à la cible, MOQ, UM

Le moteur parcourt le Stock projeté à partir de demain. Une proposition se déclenche quand le stock projeté passe **sous (100 − tolérance) % de la cible**, soit **85 %** avec la tolérance commune de 15 % (ou dès qu'un besoin n'est pas servi). La quantité ramène le stock à **100 % de la cible** : besoin net = cible − stock projeté (+ manque), relevé au **MOQ** (200) puis arrondi au multiple supérieur de l'**UM** (50). La livraison est placée sur un jour de livraison du fournisseur (le dernier avant le besoin), la date limite de commande = livraison − (délai − marge) jours ouvrés (3 − 1 = 2) : le dernier jour où l'ordre peut partir, affichée « au plus tard le ».

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 | lun 19/10 | mar 20/10 | mer 21/10 | jeu 22/10 | ven 23/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Prop. CBN | · | · | · | · | 200 | · | 200 | · | 200 | · | 200 | · | · | · | · |
| Projeté Ferme | **800** | **700** | **600** | **500** | **400** | **300** | **200** | **100** | **·** | **·** (−100) | **·** (−200) | **·** (−300) | **·** (−400) | **·** (−500) | **·** (−600) |
| Stock projeté | **800** | **700** | **600** | **500** | **600** | **500** | **600** | **500** | **600** | **500** | **600** | **500** | **400** | **300** | **200** |
| Cible | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 400 | 300 | 200 | 100 |

| Livraison | Fournisseur | Quantité | Besoin net | Au plus tard le | Urgente | Plancher | Plafonnée | Motif |
|---|---|---:|---:|---|---|---|---|---|
| ven 09/10 | S1 | 200 | 100 | mer 07/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-09 : retour à la cible 500 |
| mar 13/10 | S1 | 200 | 100 | ven 09/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-13 : retour à la cible 500 |
| jeu 15/10 | S1 | 200 | 100 | mar 13/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-15 : retour à la cible 500 |
| lun 19/10 | S1 | 200 | 100 | jeu 15/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-19 : retour à la cible 500 |

**Lecture.** Le Stock projeté intègre les propositions : il reste au-dessus de 85 % de la cible, tandis que le Projeté Ferme, sans commande, tombe à zéro. Les propositions sont **recalculées à chaque affichage** et n'existent nulle part ailleurs ; pour en reprendre une, l'approvisionneur tape la quantité dans la cellule Quantité à retenir (la cellule grisée la prérempli).

Avec une **tolérance à 0 %**, la moindre descente sous la cible déclenche : les propositions sont plus fréquentes et plus petites (à l'UM près).

| Livraison | Fournisseur | Quantité | Besoin net | Au plus tard le | Urgente | Plancher | Plafonnée | Motif |
|---|---|---:|---:|---|---|---|---|---|
| ven 09/10 | S1 | 200 | 100 | mer 07/10 | non | non | non | Stock projeté 400 < 100 % de la cible (500) le 2026-10-09 : retour à la cible 500 |
| mar 13/10 | S1 | 200 | 100 | ven 09/10 | non | non | non | Stock projeté 400 < 100 % de la cible (500) le 2026-10-13 : retour à la cible 500 |
| jeu 15/10 | S1 | 200 | 100 | mar 13/10 | non | non | non | Stock projeté 400 < 100 % de la cible (500) le 2026-10-15 : retour à la cible 500 |
| lun 19/10 | S1 | 200 | 100 | jeu 15/10 | non | non | non | Stock projeté 400 < 100 % de la cible (500) le 2026-10-19 : retour à la cible 500 |

Avec **MOQ 1 000 et UM 250**, la première proposition saute au MOQ ; la suivante est repoussée d'autant.

| Livraison | Fournisseur | Quantité | Besoin net | Au plus tard le | Urgente | Plancher | Plafonnée | Motif |
|---|---|---:|---:|---|---|---|---|---|
| ven 09/10 | S1 | 1 000 | 100 | mer 07/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-09 : retour à la cible 500 |

## 5. Délai fournisseur, marge, date limite et plancher

Stock de départ 350 et délai de **10 jours ouvrés**, marge de délai 1 : une livraison est **tenable** à partir du ven 16/10 (aujourd'hui + délai − marge). Le creux arrive le jeu 08/10, **avant** : sa date limite de commande (ven 25/09) est dépassée. Aucun jour ouvré tenable ne précède le besoin : le moteur applique le **plancher** : il propose, **urgente** (**!**), au jour du besoin, la quantité **minimale qui évite la rupture** (lotie MOQ / UM), répétée à chaque nouveau creux jusqu'à la première livraison tenable, sans remonter à la cible. La date limite, dans le passé, est affichée telle quelle (« au plus tard le »), et une alerte `URGENT_PROPOSAL` est levée. À partir de la **première livraison tenable**, la proposition vise la **cible pleine**, sans rampe. (Ici le fournisseur livre tous les jours ouvrés : aucune proposition **hors jour standard ◇** ; avec un fournisseur du lundi, le besoin non tenable serait posé ◇ sur le premier jour ouvré tenable avant le besoin, sauf jours stricts.)

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 | lun 19/10 | mar 20/10 | mer 21/10 | jeu 22/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Prop. CBN | · | · | · | 200 | · | 200 | · | 200 | · | 550 | 200 | · | · | · |
| Stock projeté | **250** | **150** | **50** | **150** | **50** | **150** | **50** | **150** | **50** | **500** | **600** | **500** | **400** | **300** |
| Cible | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 400 | 300 | 200 |

| Livraison | Fournisseur | Quantité | Besoin net | Au plus tard le | Urgente | Plancher | Plafonnée | Motif |
|---|---|---:|---:|---|---|---|---|---|
| jeu 08/10 | S1 | 200 | 50 | ven 25/09 | **oui** | oui | non | Plancher : besoin non servi de 50 le 2026-10-08, lot minimal pour l'éviter ; aucune livraison tenable avant le besoin, date limite 2026-09-25 dépassée (délai 10 j ouvrés, marge 1 j), commande à passer immédiatement |
| lun 12/10 | S1 | 200 | 50 | mar 29/09 | **oui** | oui | non | Plancher : besoin non servi de 50 le 2026-10-12, lot minimal pour l'éviter ; aucune livraison tenable avant le besoin, date limite 2026-09-29 dépassée (délai 10 j ouvrés, marge 1 j), commande à passer immédiatement |
| mer 14/10 | S1 | 200 | 50 | jeu 01/10 | **oui** | oui | non | Plancher : besoin non servi de 50 le 2026-10-14, lot minimal pour l'éviter ; aucune livraison tenable avant le besoin, date limite 2026-10-01 dépassée (délai 10 j ouvrés, marge 1 j), commande à passer immédiatement |
| ven 16/10 | S1 | 550 | 550 | lun 05/10 | non | non | non | Stock projeté 0 < 85 % de la cible (425) le 2026-10-16 : retour à la cible 500 (sous la cible dès le 2026-10-15, livraison standard suivante le 2026-10-16) |
| lun 19/10 | S1 | 200 | 100 | mar 06/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-19 : retour à la cible 500 |

- **LOW_COVERAGE** (critique, erp) : Le stock à date (350) couvre 3 j ouvrés de besoin, seuil rouge 2 j + 1 ; épuisé le 2026-10-08
- **URGENT_PROPOSAL** (critique, plan) : 3 proposition(s) dont la date limite de commande est passée – S1 Fournisseur A, livraison requise le 2026-10-08 (200, plancher : le minimum qui évite la rupture)
- **ORDER_DUE** (avertissement, plan) : Commande à placer au plus tard le 2026-10-05 chez S1 Fournisseur A : 550 à livrer le 2026-10-16 (2 propositions sous 5 j ouvrés)

**Lecture.** Le plancher est strict : la tolérance de 15 % ne s'applique qu'au déclenchement du retour à la cible, jamais au plancher. Si l'approvisionneur **refuse** une proposition plancher (clic), le besoin non servi devient une rupture affichée et alertée, et le CBN reporte le besoin à la livraison suivante.

**Délai spécifique du lien** : le délai est porté par le fournisseur (ici 10) ; un lien peut le remplacer (ici **4 jours ouvrés** pour A1 ↔ S1, première livraison tenable ven 09/10). Le plancher ne s'applique plus qu'au creux d'avant (date limite dépassée).

| Livraison | Fournisseur | Quantité | Besoin net | Au plus tard le | Urgente | Plancher | Plafonnée | Motif |
|---|---|---:|---:|---|---|---|---|---|
| jeu 08/10 | S1 | 550 | 550 | lun 05/10 | non | non | non | Stock projeté 0 < 85 % de la cible (425) le 2026-10-08 : retour à la cible 500 (sous la cible dès le 2026-10-06, livraison standard suivante le 2026-10-08) |
| ven 09/10 | S1 | 200 | 100 | mar 06/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-09 : retour à la cible 500 |
| mar 13/10 | S1 | 200 | 100 | jeu 08/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-13 : retour à la cible 500 |
| jeu 15/10 | S1 | 200 | 100 | lun 12/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-15 : retour à la cible 500 |
| lun 19/10 | S1 | 200 | 100 | mer 14/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-19 : retour à la cible 500 |

## 6. Jours de livraison du fournisseur : mardi / jeudi

Le fournisseur ne livre que le **mardi et le jeudi** (Référentiel › Fournisseurs, jours de livraison). Quand le creux tombe un autre jour, la livraison est placée sur le jour de livraison autorisé le plus proche **avant** le creux (la marchandise est là avant le besoin) ; les besoins d'ici la livraison suivante sont regroupés sur cette livraison.

| Livraison | Fournisseur | Quantité | Besoin net | Au plus tard le | Urgente | Plancher | Plafonnée | Motif |
|---|---|---:|---:|---|---|---|---|---|
| jeu 08/10 | S1 | 200 | 100 | mar 06/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-09 : retour à la cible 500 |
| mar 13/10 | S1 | 200 | 100 | ven 09/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-13 : retour à la cible 500 |
| jeu 15/10 | S1 | 400 | 200 | mar 13/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-15 : retour à la cible 500 ; Stock projeté 400 < 85 % de la cible (425) le 2026-10-19 : retour à la cible 500 |

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Prop. CBN | · | · | · | 200 | · | · | 200 | · | 400 | · |
| Stock projeté | **800** | **700** | **600** | **700** | **600** | **500** | **600** | **500** | **800** | **700** |
| Cible | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 |

## 7. Jours de consommation de l'article (`demand_days`)

Le PDP est hebdomadaire ; par défaut son besoin est réparti sur les **5 jours ouvrés** de la semaine. Un article consommé en début de semaine se paramètre avec **`demand_days = 3`** : le besoin de la semaine (500) tombe sur **lundi, mardi, mercredi** (167 / 167 / 166), rien jeudi et vendredi. La cible (besoin des 5 prochains jours ouvrés) et la couverture suivent ce profil : un jeudi, la cible englobe déjà les trois jours de consommation de la semaine suivante.

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 167 | 167 | 166 | · | · | 167 | 167 | 166 | · | · |
| Stock projeté | **733** | **566** | **400** | **400** | **400** | **233** | **66** | **500** | **500** | **500** |
| Cible | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 |

Pour la **semaine en cours**, le reliquat (PDP éclaté − réel déjà déclaré) est réparti sur les jours du profil encore à venir ; s'ils sont passés, sur les jours ouvrés restants (§ 11). Le profil observé et une suggestion de `demand_days` sont proposés dans *Paramètres › Données › Adhérence* et dans l'onglet *Paramétrage* de la fiche article.

## 8. Plafonds du fournisseur : par livraison et par semaine

Un lien peut porter un **plafond par livraison** (multiple de l'UM, ≥ MOQ) et un **plafond par semaine** (≤ plafond par livraison × jours de livraison), comptés **commandes fermes et quantités à retenir comprises**. Le plafond s'applique **après** le lotissement MOQ / UM, en tronquant au multiple d'UM ; l'excédent glisse à la livraison suivante (ou à la semaine suivante). Ici, stock 350 et **400 maximum par livraison** : la première proposition ne peut pas remonter à la cible d'un coup, elle est **plafonnée** et le reste suit.

| Livraison | Fournisseur | Quantité | Besoin net | Au plus tard le | Urgente | Plancher | Plafonnée | Motif |
|---|---|---:|---:|---|---|---|---|---|
| mer 07/10 | S1 | 400 | 450 | lun 05/10 | non | non | oui | Stock projeté 50 < 85 % de la cible (425) le 2026-10-07 : retour à la cible 500 (sous la cible dès le 2026-10-06, livraison standard suivante le 2026-10-07) ; plafond par livraison (400) atteint : 400 livrés, le reste glisse à la livraison suivante |
| jeu 08/10 | S1 | 200 | 150 | mar 06/10 | non | non | non | Stock projeté 350 < 85 % de la cible (425) le 2026-10-08 : retour à la cible 500 |
| lun 12/10 | S1 | 200 | 150 | jeu 08/10 | non | non | non | Stock projeté 350 < 85 % de la cible (425) le 2026-10-12 : retour à la cible 500 |
| mer 14/10 | S1 | 200 | 150 | lun 12/10 | non | non | non | Stock projeté 350 < 85 % de la cible (425) le 2026-10-14 : retour à la cible 500 |
| ven 16/10 | S1 | 200 | 150 | mer 14/10 | non | non | non | Stock projeté 350 < 85 % de la cible (425) le 2026-10-16 : retour à la cible 500 |
| mer 21/10 | S1 | 200 | 50 | lun 19/10 | non | non | non | Stock projeté 250 < 85 % de la cible (255) le 2026-10-21 : retour à la cible 300 |

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Prop. CBN | · | · | 400 | 200 | · | 200 | · | 200 | · | 200 |
| Stock projeté | **250** | **150** | **450** | **550** | **450** | **550** | **450** | **550** | **450** | **550** |
| Cible | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 |

**Plafond insuffisant** : fournisseur mardi / jeudi, 200 maximum par livraison et **400 par semaine**, pour un besoin de 500 par semaine. Même le plancher dépasse ce que le fournisseur peut livrer : le manque est **inévitable**, affiché en rouge dans le Stock projeté et remonté par l'alerte « plafond fournisseur atteint ». Les plafonds s'appliquent aussi aux propositions urgentes.

| Livraison | Fournisseur | Quantité | Besoin net | Au plus tard le | Urgente | Plancher | Plafonnée | Motif |
|---|---|---:|---:|---|---|---|---|---|
| jeu 08/10 | S1 | 200 | 550 | mar 06/10 | non | non | oui | Stock projeté 0 < 85 % de la cible (425) le 2026-10-08 : retour à la cible 500 (sous la cible dès le 2026-10-06, livraison standard suivante le 2026-10-08) ; plafond par livraison (200) atteint : 200 livrés, le reste glisse à la livraison suivante |
| mar 13/10 | S1 | 200 | 600 | ven 09/10 | non | non | oui | Stock projeté 0 < 85 % de la cible (425) le 2026-10-13 : retour à la cible 500 (sous la cible dès le 2026-10-09, livraison standard suivante le 2026-10-13) ; plafond par livraison (200) atteint : 200 livrés, le reste glisse à la livraison suivante |
| jeu 15/10 | S1 | 200 | 600 | mar 13/10 | non | non | oui | Stock projeté 0 < 85 % de la cible (425) le 2026-10-15 : retour à la cible 500 (sous la cible dès le 2026-10-14, livraison standard suivante le 2026-10-15) ; plafond par livraison (200) atteint : 200 livrés, le reste glisse à la livraison suivante |
| mar 20/10 | S1 | 200 | 500 | ven 16/10 | non | non | oui | Stock projeté 0 < 85 % de la cible (340) le 2026-10-20 : retour à la cible 400 (sous la cible dès le 2026-10-16, livraison standard suivante le 2026-10-20) ; plafond par livraison (200) atteint : 200 livrés, le reste glisse à la livraison suivante |
| jeu 22/10 | S1 | 200 | 300 | mar 20/10 | non | non | oui | Stock projeté 0 < 85 % de la cible (170) le 2026-10-22 : retour à la cible 200 (sous la cible dès le 2026-10-21, livraison standard suivante le 2026-10-22) ; plafond par livraison (200) atteint : 200 livrés, le reste glisse à la livraison suivante |

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Prop. CBN | · | · | · | 200 | · | · | 200 | · | 200 | · |
| Stock projeté | **250** | **150** | **50** | **150** | **50** | **·** (−50) | **100** | **·** | **100** | **·** |
| Cible | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 | 500 |

- **LOW_COVERAGE** (critique, erp) : Le stock à date (350) couvre 3 j ouvrés de besoin, seuil rouge 2 j + 1 ; épuisé le 2026-10-08
- **ORDER_DUE** (avertissement, plan) : Commande à placer au plus tard le 2026-10-06 chez S1 Fournisseur A : 200 à livrer le 2026-10-08 (2 propositions sous 5 j ouvrés)
- **STOCKOUT** (info, plan) : Rupture du stock projeté le 2026-10-12 (J+5 ouvrés) malgré le plan de livraison et le complément CBN, manque cumulé max 100
- **CAP_REACHED** (info, plan) : Plafond fournisseur atteint : 50 non livrables le 2026-10-09 (2 jour(s)), manque inévitable affiché dans le stock projeté

## 9. Multi-sourcing : quotas, priorité, plafond reporté, refus d'un jour

Deux fournisseurs : S1 (quota 70 %, priorité 1, délai 3) et S2 (quota 30 %, priorité 2, délai 5). Avec la politique **quotas** (défaut, par article), **chaque besoin est partagé** entre les fournisseurs au prorata des quotas : chaque part est arrondie au MOQ et à l'UM de son fournisseur et livrée à son propre jour (délai, jours de livraison) ; avec **priorité**, tout va au fournisseur de priorité 1. Chaque fournisseur a sa propre voie dans le tableau (lignes Ordre ferme / Quantité reçue / Quantité à retenir / Prop. CBN).

Quotas 70 / 30 (six semaines) :

| Livraison | Fournisseur | Quantité | Besoin net | Au plus tard le | Urgente | Plancher | Plafonnée | Motif |
|---|---|---:|---:|---|---|---|---|---|
| ven 09/10 | S1 | 200 | 70 | mer 07/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-09 : retour à la cible 500 ; part 70 % du quota (70 sur 100) |
| ven 09/10 | S2 | 200 | 30 | lun 05/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-09 : retour à la cible 500 ; part 30 % du quota (30 sur 100) |
| jeu 15/10 | S1 | 200 | 70 | mar 13/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-15 : retour à la cible 500 ; part 70 % du quota (70 sur 100) |
| jeu 15/10 | S2 | 200 | 30 | ven 09/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-15 : retour à la cible 500 ; part 30 % du quota (30 sur 100) |
| mer 21/10 | S1 | 200 | 70 | lun 19/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-21 : retour à la cible 500 ; part 70 % du quota (70 sur 100) |
| mer 21/10 | S2 | 200 | 30 | jeu 15/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-21 : retour à la cible 500 ; part 30 % du quota (30 sur 100) |
| mar 27/10 | S1 | 200 | 70 | ven 23/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-27 : retour à la cible 500 ; part 70 % du quota (70 sur 100) |
| mar 27/10 | S2 | 200 | 30 | mer 21/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-27 : retour à la cible 500 ; part 30 % du quota (30 sur 100) |
| lun 02/11 | S1 | 200 | 70 | jeu 29/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-11-02 : retour à la cible 500 ; part 70 % du quota (70 sur 100) |
| lun 02/11 | S2 | 200 | 30 | mar 27/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-11-02 : retour à la cible 500 ; part 30 % du quota (30 sur 100) |
| ven 06/11 | S1 | 200 | 70 | mer 04/11 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-11-06 : retour à la cible 500 ; part 70 % du quota (70 sur 100) |
| ven 06/11 | S2 | 200 | 30 | lun 02/11 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-11-06 : retour à la cible 500 ; part 30 % du quota (30 sur 100) |

Parts obtenues sur six semaines : S1 1 200, S2 1 200 (50 % / 50 %) : l'écart avec 70 / 30 vient des MOQ et des UM de chaque fournisseur.

Priorité :

| Livraison | Fournisseur | Quantité | Besoin net | Au plus tard le | Urgente | Plancher | Plafonnée | Motif |
|---|---|---:|---:|---|---|---|---|---|
| ven 09/10 | S1 | 200 | 100 | mer 07/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-09 : retour à la cible 500 |
| mar 13/10 | S1 | 200 | 100 | ven 09/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-13 : retour à la cible 500 |
| jeu 15/10 | S1 | 200 | 100 | mar 13/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-15 : retour à la cible 500 |
| lun 19/10 | S1 | 200 | 100 | jeu 15/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-19 : retour à la cible 500 |
| mer 21/10 | S1 | 200 | 100 | lun 19/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-21 : retour à la cible 500 |
| ven 23/10 | S1 | 200 | 100 | mer 21/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-23 : retour à la cible 500 |
| mar 27/10 | S1 | 200 | 100 | ven 23/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-27 : retour à la cible 500 |
| jeu 29/10 | S1 | 200 | 100 | mar 27/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-29 : retour à la cible 500 |
| lun 02/11 | S1 | 200 | 100 | jeu 29/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-11-02 : retour à la cible 500 |
| mer 04/11 | S1 | 200 | 100 | lun 02/11 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-11-04 : retour à la cible 500 |
| ven 06/11 | S1 | 200 | 100 | mer 04/11 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-11-06 : retour à la cible 500 |

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Prop. CBN · S1 | · | · | · | · | 200 | · | · | · | 200 | · |
| Prop. CBN · S2 | · | · | · | · | 200 | · | · | · | 200 | · |
| Stock projeté | **800** | **700** | **600** | **500** | **800** | **700** | **600** | **500** | **800** | **700** |

**Plafond et quotas** : S1 est plafonné à 200 par livraison. La part de S1 qui dépasse son plafond est **reportée sur S2** (l'autre fournisseur à quota) le même jour, avant de glisser à la livraison suivante.

| Livraison | Fournisseur | Quantité | Besoin net | Au plus tard le | Urgente | Plancher | Plafonnée | Motif |
|---|---|---:|---:|---|---|---|---|---|
| mer 07/10 | S1 | 200 | 315 | lun 05/10 | non | non | oui | Stock projeté 50 < 85 % de la cible (425) le 2026-10-07 : retour à la cible 500 ; part 70 % du quota (315 sur 450) (sous la cible dès le 2026-10-06, livraison standard suivante le 2026-10-07) ; plafond par livraison (200) atteint : 200 livrés, le reste glisse à la livraison suivante |
| mer 07/10 | S2 | 250 | 250 | lun 05/10 | non | non | non | Stock projeté 50 < 85 % de la cible (425) le 2026-10-07 : retour à la cible 500 ; part 30 % du quota (250 sur 450) (sous la cible dès le 2026-10-06, livraison standard suivante le 2026-10-07) |
| jeu 08/10 | S1 | 200 | 70 | mar 06/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-08 : retour à la cible 500 ; part 70 % du quota (70 sur 100) |
| jeu 08/10 | S2 | 200 | 30 | mar 06/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-08 : retour à la cible 500 ; part 30 % du quota (30 sur 100) |
| mer 14/10 | S1 | 200 | 70 | lun 12/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-14 : retour à la cible 500 ; part 70 % du quota (70 sur 100) |
| mer 14/10 | S2 | 200 | 30 | lun 12/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-14 : retour à la cible 500 ; part 30 % du quota (30 sur 100) |

**Refus** : un clic sur la proposition du ven 09/10 (S1) la barre et interdit toute proposition à **ce fournisseur ce jour-là seulement** (un fournisseur mardi / jeudi refusé le mardi reste possible le jeudi) ; sa part passe à l'autre fournisseur à quota. Un mono-source refusé glisse au jour de livraison ouvert suivant, le manque intermédiaire est affiché et alerté. La proposition refusée reste dans le flux *À commander* du cockpit (si sa date limite est dépassée ou sous 5 jours ouvrés), marquée « ignorée » et masquée par défaut ; un refus daté avant aujourd'hui a expiré et n'est plus affiché.

| Livraison | Fournisseur | Quantité | Besoin net | Au plus tard le | Urgente | Plancher | Plafonnée | Motif |
|---|---|---:|---:|---|---|---|---|---|
| ven 09/10 | S2 | 200 | 100 | lun 05/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-09 : retour à la cible 500 |
| mar 13/10 | S1 | 200 | 70 | ven 09/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-13 : retour à la cible 500 ; part 70 % du quota (70 sur 100) |
| mar 13/10 | S2 | 200 | 30 | mer 07/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-13 : retour à la cible 500 ; part 30 % du quota (30 sur 100) |
| lun 19/10 | S1 | 200 | 70 | jeu 15/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-19 : retour à la cible 500 ; part 70 % du quota (70 sur 100) |
| lun 19/10 | S2 | 200 | 30 | mar 13/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-19 : retour à la cible 500 ; part 30 % du quota (30 sur 100) |
| ven 23/10 | S1 | 200 | 70 | mer 21/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-23 : retour à la cible 500 ; part 70 % du quota (70 sur 100) |
| ven 23/10 | S2 | 200 | 30 | lun 19/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-23 : retour à la cible 500 ; part 30 % du quota (30 sur 100) |
| jeu 29/10 | S1 | 200 | 70 | mar 27/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-29 : retour à la cible 500 ; part 70 % du quota (70 sur 100) |
| jeu 29/10 | S2 | 200 | 30 | ven 23/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-10-29 : retour à la cible 500 ; part 30 % du quota (30 sur 100) |
| mer 04/11 | S1 | 200 | 70 | lun 02/11 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-11-04 : retour à la cible 500 ; part 70 % du quota (70 sur 100) |
| mer 04/11 | S2 | 200 | 30 | jeu 29/10 | non | non | non | Stock projeté 400 < 85 % de la cible (425) le 2026-11-04 : retour à la cible 500 ; part 30 % du quota (30 sur 100) |

## 10. Statut et alertes : couverture à date, date limite de commande, surstock

Trois statuts, sans horizon commun. **Critique** : la **couverture à date** (le stock de la veille seul, sans aucune livraison) tient au plus `alert_red_days` + 1 jours ouvrés (`LOW_COVERAGE` critique), ou une proposition CBN a sa date limite de commande dépassée (`URGENT_PROPOSAL`), ou le stock de départ est négatif (`NEGATIVE_STOCK`). **À commander** : une proposition a sa date limite sous cinq jours ouvrés (`ORDER_DUE`), ou la couverture à date tient au plus `alert_yellow_days` jours ouvrés sans livraison ferme ni quantité à retenir saisie avant l'épuisement (`LOW_COVERAGE` avertissement). **OK** sinon. Le reste est de l'information : `OVERSTOCK` (couverture à date ≥ `overstock_days`), `STOCKOUT` (rupture du Stock projeté que le CBN n'a pas pu éviter), `CAP_REACHED` (manque inévitable sous les plafonds, § 8), et les données (`MISSING_DATA`, `UNLINKED_FLOWS`). En multi-sourcing chaque part de proposition porte le délai de son fournisseur : le statut suit la date limite la plus proche.

**Stock 150 (rupture dès demain)**

- **LOW_COVERAGE** (critique, erp) : Le stock à date (150) couvre 1 j ouvrés de besoin, seuil rouge 2 j + 1 ; épuisé le 2026-10-06
- **URGENT_PROPOSAL** (critique, plan) : 1 proposition(s) dont la date limite de commande est passée – S1 Fournisseur A, livraison requise le 2026-10-06 (200, plancher : le minimum qui évite la rupture)
- **ORDER_DUE** (avertissement, plan) : Commande à placer au plus tard le 2026-10-06 chez S1 Fournisseur A : 550 à livrer le 2026-10-08

**Stock 450 (rupture vendredi)**

- **LOW_COVERAGE** (avertissement, erp) : Le stock à date couvre 4 j ouvrés de besoin (seuil orange 5 j) et aucune livraison ferme ni Appro. saisie n'arrive avant le 2026-10-09
- **ORDER_DUE** (avertissement, plan) : Commande à placer au plus tard le 2026-10-07 chez S1 Fournisseur A : 550 à livrer le 2026-10-09

**Stock 8 000 (80 jours ouvrés de besoin)**

- **OVERSTOCK** (info, plan) : Surstock : le stock à date couvre 45 j ouvrés de besoin (≥ 30 j)

**Stock 3 000, ferme ERP 500 le 12/10 : rien à faire, surstock (30 jours ouvrés de besoin)**

- **OVERSTOCK** (info, plan) : Surstock : le stock à date couvre 30 j ouvrés de besoin (≥ 30 j)

**Données manquantes** (pas de fournisseur lié) :

- **MISSING_DATA** (info, data) : Aucun fournisseur actif lié à l'article (MOQ / délai inconnus)

## 11. Consommation réelle, semaine en cours, réceptions et commandes en retard

Ici aujourd'hui est le **mer 07/10** et le stock a été initialisé le dim 27/09. Le passé n'est jamais reconstruit : le besoin des jours passés est la **consommation réelle** déclarée (`conso_composants`, déjà éclatée par composant ; source de consommation « ERP », défaut) ; un jour sans déclaration compte 0. La **semaine en cours** consomme son PDP éclaté (500) moins le réel déjà déclaré (150 + 50 = 200) : le reliquat (300) est réparti sur les jours ouvrés restants, aujourd'hui compris. La commande ferme du 30/09 (300) n'a été reçue qu'à 200 et l'ERP garde un **restant de 100** : c'est une commande **en retard**, affichée mais comptée dans aucun stock (le restant ERP se met à jour à la validation du journal de réception).

| | lun 28/09 | mar 29/09 | mer 30/09 | jeu 01/10 | ven 02/10 | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 120 | 80 | 100 | 90 | 110 | 150 | 50 | 100 | 100 | 100 |
| Ordre ferme | · | · | · | · | · | · | · | 300 | · | · |
| Ordre ferme (passé) | · | · | 300 | · | · | · | · | · | · | · |
| Quantité reçue | · | · | 200 | · | · | · | · | · | · | · |
| Quantité à retenir | · | · | · | · | · | · | · | 300 | · | · |
| Projeté Ferme | **780** | **700** | **800** | **710** | **600** | **450** | **400** | **600** | **500** | **400** |
| Stock projeté | **780** | **700** | **800** | **710** | **600** | **450** | **400** | **600** | **500** | **400** |

| KPI | Valeur |
|---|---:|
| Stock de référence | 900 |
| Stock à date | 400 |
| En retard (restant ERP) | 100 |
| Couverture projetée (j ouvrés) | 6 |

**Lecture.** *Stock à date* = stock projeté à la fin de la veille (réceptions et consommations réelles comprises). *En retard* = Σ du restant ERP des commandes fermes dont la date de livraison est passée, sans fenêtre ; une commande ignorée sort de la projection mais reste en retard.

**Sources de consommation.** Un article sans consommation déclarée fiable se met en source **PDP** : son passé est le PDP éclaté, comme le futur. Un article dont la consommation se déduit d'autres articles se met en source **dérivée** : conso = Σ coefficient × consommation déclarée des articles sources (sources en mode ERP, présentes dans un programme de l'article et dans aucun programme où il est absent ; table *Termes de consommation*).

## 12. Ajustements et correction du stock de référence

Une cellule **Ajustement** est une quantité signée. Datée **au plus tard le point zéro**, elle corrige le stock de référence (l'ERP se trompe, l'application n'y écrit jamais) ; datée après, c'est un mouvement connu d'avance (rebut, transfert). Ici −150 le jour de l'initialisation et −200 le jeudi.

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 |
|---|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 |
| Ajustement | · | · | · | -200 | · | · |
| Projeté Ferme | **650** | **550** | **450** | **150** | **50** | **·** (−50) |
| Stock projeté | **650** | **550** | **450** | **150** | **50** | **500** |

| KPI | Valeur |
|---|---:|
| Stock de référence | 750 |
| Correction du stock de référence | -150 |
| Stock à date | 750 |

## 13. Paramètres d'article par semaine

Les paramètres de l'article (couverture cible, seuils rouge et orange, surstock) peuvent être surchargés **par semaine ISO** (fiche article › Paramétrage › Par semaine, ou Référentiel › Articles). Ici la couverture cible passe de 5 à **10 jours ouvrés pour la semaine 42** seulement : la cible double cette semaine-là, puis revient à 5 jours.

| | lun 05/10 | mar 06/10 | mer 07/10 | jeu 08/10 | ven 09/10 | lun 12/10 | mar 13/10 | mer 14/10 | jeu 15/10 | ven 16/10 | lun 19/10 | mar 20/10 | mer 21/10 | jeu 22/10 | ven 23/10 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Besoin | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 | 100 |
| Cible | 500 | 500 | 500 | 500 | 500 | 1 000 | 900 | 800 | 700 | 600 | 500 | 400 | 300 | 200 | 100 |

## 14. Fenêtre de calcul, affichage, cache

- **Point zéro** = date du stock de référence (`fct_stock.snapshot_date`), une seule pour tous les articles, jamais après aujourd'hui. Rien n'existe avant : pas d'historique reconstruit.
- **Fenêtre** = du point zéro à aujourd'hui + **52 semaines** (horizon fixe, tableau et PDP compris), étendue au dimanche de la semaine en cours ; le tableau commence au lundi de la semaine en cours moins les *semaines passées* choisies par l'utilisateur (2 par défaut).
- **Calendrier** « Par défaut » : un jour par colonne pour la semaine en cours et les *semaines détaillées* choisies par l'utilisateur (2), une semaine par colonne au-delà ; une colonne semaine se saisit comme une colonne jour, la valeur se place au lundi de la semaine (au jour de référence dans la semaine en cours) ; cliquer l'en-tête pour le détail par jour.
- Le **sélecteur de période** sous la barre d'outils ne change que les colonnes affichées.
- **Calendrier usine** : jours ouvrés et fermetures (configuration de l'application, visibles dans *Paramètres › Données*) ; aucun besoin, aucune livraison et aucun jour compté sur une fermeture.
- Le **taux de rebut d'un programme** (colonne *Taux rebut (%)* du Plan de production) majore le PDP de la semaine avant l'éclatement : besoin = PDP × (1 + taux / 100) ; daté, il ne touche que la semaine en cours et les suivantes.
- Les articles **décimaux** (unité KG, M… ou nomenclature décimale) affichent le besoin et les stocks avec trois décimales ; les autres en entiers (affichage seulement, les calculs sont exacts).
- Tout est recalculé à chaque demande et mis en cache jusqu'à la prochaine écriture (saisie, import, paramètre) ou synchronisation ERP.

## 15. Récapitulatif des paramètres

| Paramètre | Défaut | Portée | Effet |
|---|---|---|---|
| `coverage_target_days` | 5 (article) | article, semaine | jours ouvrés de besoin à tenir en stock : fixe la cible, donc le déclenchement et la taille des propositions |
| `alert_red_days / alert_yellow_days` | 2 / 5 (article) | article, semaine | seuils des alertes de couverture (épuisement des commandes ERP), jours ouvrés |
| `overstock_days` | 30 (article) | article, semaine | seuil de l'alerte surstock, jours ouvrés |
| `demand_days` | 5 (article) | article | jours ouvrés de la semaine portant le besoin hebdomadaire (1 = lundi seul … 5 = toute la semaine) |
| `sourcing_policy` | quota (article) | article | quotas (chaque besoin partagé) ou priorité |
| `consumption_source` | erp (article) | article | besoin passé : consommation ERP, PDP éclaté, ou dérivée d'autres articles (termes × coefficient) |
| `lead_time_days` | 5 (fournisseur) | fournisseur | délai en jours ouvrés, inclusif (lundi + 5 = lundi suivant) ; date limite de commande, urgence, plancher |
| `delivery_weekdays` | 1-5 (fournisseur) | fournisseur | jours de livraison standard ; saisie en colonne semaine posée sur le premier |
| `strict_days` | non (fournisseur) | fournisseur | oui : jamais de proposition hors jour standard (◇), le besoin non tenable devient un plancher au jour standard |
| `moq / pack_qty` | lien article ↔ fournisseur | lien | quantité minimale, arrondi UM |
| `lead_time_override` | vide (lien) | lien | délai spécifique au lien, remplace celui du fournisseur |
| `quota_pct / priority` | lien | lien | répartition multi-sourcing |
| `max_qty_per_delivery / max_qty_per_week` | vide (lien) | lien | plafonds, multiples de l'UM ; fermes et quantités à retenir comptés ; excédent reporté, manque inévitable alerté |
| `target_tolerance_pct` | 15 | commune | retour à la cible dès que le stock projeté passe sous (100 − n) % de la cible ; la quantité vise 100 % |
| `lead_margin_days` | 1 | commune | marge de délai : date limite de commande = livraison − (délai − marge) j ouvrés ; 0 = délai strict ; tenable ⇔ date limite ≥ aujourd'hui |
| `Règles figées` | — | application | jours ouvrés partout ; besoin non servi perdu ; date limite dépassée = plancher ! au jour du besoin, ou livraison hors jour standard ◇ au premier jour ouvré tenable (sauf jours stricts) ; cible pleine dès que tenable ; livraison standard avant le besoin, regroupée ; horizon 52 semaines ; à commander au plus tard sous 5 j ouvrés ; fermes seules comptées ; refus = jour seul, expiré dans le passé |
| `history_weeks / focus_weeks` | 2 / 2 (utilisateur) | affichage | semaines passées montrées, semaines détaillées jour par jour |

La règle commune se règle dans *Paramètres › Règles communes* (managers, administrateurs) ; les paramètres de l'article dans sa fiche (onglet *Paramétrage*, avec les surcharges hebdomadaires et les liens fournisseurs) ou dans le *Référentiel* ; ceux du fournisseur dans le *Référentiel* et sa fiche.
