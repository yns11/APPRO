# OTD : questions à trancher avec les équipes approvisionnement

*Grille d'atelier du 01/11/2026. Elle part d'une feuille blanche : rien de ce qui est implémenté n'est considéré
comme acquis. Chaque ligne est une question à poser, avec les réponses possibles. Une procédure OTD est complète
quand chaque question a une réponse écrite, un exemple chiffré et un propriétaire.*

## 1. À quoi sert l'OTD

| # | Question | Réponses possibles |
|---|---|---|
| 1.1 | Quel est l'usage premier ? | pilotage mensuel des fournisseurs ; rapport à la hiérarchie ; base d'escalade ou de plan d'action ; clause contractuelle ou pénalités ; tout cela à la fois (alors un seul chiffre ne suffira pas). |
| 1.2 | Qui lit le chiffre et avec quel niveau de détail ? | approvisionneur (par jour, par article) ; responsable approvisionnement (par fournisseur, par mois) ; direction (par site, par trimestre) ; le fournisseur lui-même (même chiffre que nous ?). |
| 1.3 | Quelle est la cible et à partir de quand un fournisseur est « en défaut » ? | un seuil unique (90 %) ; par classe de fournisseur ; par classe d'article (A / B / C) ; une tendance plutôt qu'un seuil. |
| 1.4 | Faut-il un chiffre unique ou un chiffre et des composantes ? | OTD seul ; OTD + les trois indicateurs ; un « taux de jours parfaits » en tête et le reste en détail. |
| 1.5 | Le chiffre doit-il être contestable par le fournisseur ? | oui, avec le détail ligne à ligne partagé ; non, usage interne. |

## 2. Périmètre

| # | Question | Réponses possibles |
|---|---|---|
| 2.1 | Quels fournisseurs ? | tous ; actifs seulement ; hors intra-groupe ; hors sous-traitants de façonnage ; hors fournisseurs sans EDI. |
| 2.2 | Quels articles ? | tous les articles achetés ; hors consignation / VMI ; hors outillage et consommables ; hors échantillons et prototypes. |
| 2.3 | Quelles commandes ? | ordres fermes des commandes ouvertes (`CO-`) ; commandes fermées ponctuelles (`PO`) ; commandes de dépannage / urgences ; commandes créées dans le délai fournisseur (notées, exclues, ou notées à part ?). |
| 2.4 | Quels flux physiques ? | livraisons directes ; via plateforme ou transit ; franco de port contre départ usine (le transit est-il de la responsabilité du fournisseur ?). |
| 2.5 | Quels sites et entrepôts ? | un site ; tous ; par entrepôt de réception. |
| 2.6 | Retours, refus qualité, litiges : dans ou hors de l'OTD ? | quantité livrée ; quantité acceptée ; les refus qualité relèvent d'un autre indicateur. |

## 3. La date de référence (« quand devait-il livrer ? »)

| # | Question | Réponses possibles |
|---|---|---|
| 3.1 | Quelle date fait foi ? | date demandée à la création ; date confirmée par le fournisseur (accusé de réception) ; dernière date replanifiée par l'approvisionneur ; date du dernier DELFOR envoyé. |
| 3.2 | Si l'approvisionneur déplace la date, le fournisseur est-il jugé sur la nouvelle ? | toujours ; seulement si le déplacement respecte le délai ; jamais (la demande initiale compte). |
| 3.3 | Une demande orale ou par mail (« livrez plutôt mardi ») compte-t-elle ? | non, seul l'ERP compte ; oui si tracée dans l'ERP avant la livraison. |
| 3.4 | Quelle précision ? | le jour ; la semaine ; un créneau horaire ; le jour avec heure limite (après l'heure limite = lendemain ?). |
| 3.5 | Un créneau posé sur un jour fermé (samedi, férié, fermeture usine) ? | déplacé au jour ouvré précédent ; au suivant ; erreur de données à corriger ; noté tel quel. |
| 3.6 | Une livraison pendant une fermeture usine imprévue ou un quai saturé ? | neutralisée ; notée à l'heure si présentée ; au fournisseur de prouver la présentation. |
| 3.7 | Les commandes passées dans le délai (urgences) ? | hors OTD ; dans l'OTD avec la date demandée ; dans l'OTD avec date = création + délai contractuel. |

## 4. La maille de mesure (« qu'est-ce qu'une livraison ? »)

| # | Question | Réponses possibles |
|---|---|---|
| 4.1 | L'unité notée ? | la sous-ligne / numéro d'ordre (UM) ; la ligne de commande ; le jour par article et fournisseur ; la semaine ; le bon de livraison ; la commande entière. |
| 4.2 | Plusieurs créneaux le même jour pour le même article ? | fusionnés ; notés séparément. |
| 4.3 | Plusieurs articles dans un même camion ? | chaque article compte ; le camion compte une fois. |
| 4.4 | Plusieurs camions le même jour ? | agrégés par jour ; chacun jugé. |
| 4.5 | Un article livré par deux fournisseurs ? | chaque couple article × fournisseur à part. |
| 4.6 | Poids d'une ligne dans la moyenne ? | chaque ligne compte 1 ; au prorata de la quantité ; au prorata de la valeur ; les articles critiques pèsent plus. |

## 5. Quantité (« a-t-il livré ce qu'il fallait ? »)

| # | Question | Réponses possibles |
|---|---|---|
| 5.1 | Tolérance en moins ? | aucune ; ± 1 UM ; ± x % ; selon l'article. |
| 5.2 | Livraison excédentaire ? | acceptée sans pénalité ; pénalisée au-delà d'un seuil ; refusée à quai (donc jamais vue). |
| 5.3 | Livraison partielle le jour, solde plus tard ? | quantité 0 ; quantité au prorata ; quantité 100 si le solde arrive dans les n jours. |
| 5.4 | Quantité livrée ou quantité acceptée (après contrôle qualité) ? | livrée ; acceptée ; les deux séparément. |
| 5.5 | Unité de mesure : pièces, UM, kg ? | l'unité de l'ERP ; conversion en UM. |

## 6. Ponctualité (« quand a-t-il livré ? »)

| # | Question | Réponses possibles |
|---|---|---|
| 6.1 | L'avance est-elle une faute ? | oui, même barème que le retard ; tolérée jusqu'à n jours ; jamais (stock accepté) ; refusée à quai. |
| 6.2 | Tranches et scores ? | 100 / 60 / 20 / 0 à 0, ±1-2, ±3-4, au-delà ; tout ou rien ; linéaire ; différentes pour avance et retard. |
| 6.3 | Jours ouvrés ou calendaires, et selon quel calendrier ? | jours ouvrés usine ; jours ouvrés du fournisseur ; calendaires ; jours de livraison standard du fournisseur. |
| 6.4 | Les tranches dépendent-elles de la fréquence de livraison ? | les mêmes pour tous ; plus larges pour un fournisseur mensuel ; exprimées en nombre de livraisons. |
| 6.5 | Quelle date de réception ? | arrivée physique au quai ; création du journal d'arrivée ; validation du journal ; comptabilisation (ACR). Vérifier ce que porte la donnée. |
| 6.6 | Un manque d'un jour comble-t-il automatiquement le jour suivant (cascade) ou chaque jour est-il jugé seul ? | chaque jour pour lui-même ; cumul depuis le début du mois ; cumul depuis toujours (lecture ERP). |
| 6.7 | Un jour manqué puis rattrapé : rattrapé dans combien de temps pour compter ? | jamais (0 définitif) ; dans la tranche 2 ; dans le mois ; sans limite. |
| 6.8 | Un fournisseur toujours en retard d'un jour et un fournisseur parfait avec un vieux reliquat se ressemblent : comment les distinguer ? | par les numéros d'ordre (si fiables) ; par le reliquat affiché à part ; par la date de solde ERP du créneau ; on accepte de ne pas distinguer. |

## 7. Reliquat et arriéré

| # | Question | Réponses possibles |
|---|---|---|
| 7.1 | Un reliquat pèse-t-il une fois ou tous les jours tant qu'il dure ? | une fois (le jour manqué) ; chaque jour ; un indicateur à part. |
| 7.2 | Un créneau ancien jamais livré : retard ou commande à annuler ? | retard tant qu'il est ouvert dans l'ERP ; à annuler au-delà de n jours (qui le fait ?) ; exclu au-delà de n jours. |
| 7.3 | Le reliquat existant au démarrage de la mesure ? | remis à zéro ; porté tel quel ; traité fournisseur par fournisseur. |
| 7.4 | Qui nettoie l'ERP (créneaux soldés à la main, quantités réduites) et avec quel effet sur l'OTD ? | l'approvisionneur, sous n jours ; le fournisseur demande ; aucune règle. |
| 7.5 | Faut-il un indicateur « solde de la période » (attendu − reçu) à côté de l'OTD ? | oui, en pièces ; oui, en jours de livraison ; non. |

## 8. DESADV et EDI

| # | Question | Réponses possibles |
|---|---|---|
| 8.1 | Un fournisseur sans EDI : DESADV à 0, neutralisé, ou poids reporté sur les deux autres ? | 0 (incitation) ; neutralisé ; hors indicateur. |
| 8.2 | Qu'est-ce qu'un DESADV « sans erreur » ? | traité par l'ERP ; cohérent avec le BL ; cohérent avec la quantité reçue ; émis avant l'arrivée. |
| 8.3 | Une erreur due à notre ERP ou à un référentiel incomplet : imputée au fournisseur ? | non, neutralisée (qui décide ?) ; oui. |
| 8.4 | DESADV reçu après la livraison, BL sans DESADV, DESADV sans réception ? | chacun 0 ; seulement le BL sans DESADV ; seuil de délai. |
| 8.5 | Poids du DESADV dans l'OTD ? | 20 % ; moins ; indicateur séparé hors OTD. |

## 9. Exclusions et causes externes

| # | Question | Réponses possibles |
|---|---|---|
| 9.1 | Quelles causes neutralisent une ligne ? | force majeure ; grève ou défaillance d'un transporteur que nous mandatons ; fermeture usine imprévue ; erreur ERP ; demande tardive de l'approvisionneur. |
| 9.2 | Qui prononce l'exclusion et comment est-elle tracée ? | l'approvisionneur avec motif ; le responsable ; jamais (le chiffre brut fait foi, l'analyse est à côté). |
| 9.3 | Une exclusion est-elle visible dans le chiffre (brut et net) ? | deux chiffres ; un seul net ; un seul brut. |
| 9.4 | Délai pour demander une exclusion ? | avant l'arrêté du mois ; n jours ; sans limite. |

## 10. Agrégation et pondération

| # | Question | Réponses possibles |
|---|---|---|
| 10.1 | OTD d'un fournisseur sur un mois ? | moyenne des lignes ; moyenne pondérée par quantité ou valeur ; % de lignes parfaites. |
| 10.2 | OTD du portefeuille ou du site ? | moyenne des lignes ; moyenne des fournisseurs (chacun compte 1) ; pondérée par le chiffre d'achats. |
| 10.3 | Nombre minimal de lignes pour afficher un chiffre ? | aucun ; 3 ; 10 ; afficher avec avertissement. |
| 10.4 | Un fournisseur livré tous les jours pèse-t-il plus qu'un fournisseur mensuel ? | oui (plus de lignes) ; non (moyenne des fournisseurs). |
| 10.5 | Le mois d'une ligne ? | mois de la date attendue ; mois de la réception ; mois de l'arrêté. |

## 11. Période, arrêté et révisions

| # | Question | Réponses possibles |
|---|---|---|
| 11.1 | Quand le chiffre du mois est-il figé ? | le dernier jour du mois ; n jours ouvrés après (le temps que le dernier jour soit jugeable) ; jamais. |
| 11.2 | Une correction après l'arrêté (rattrapage, correction ERP, exclusion) change-t-elle le chiffre envoyé ? | non, on garde l'arrêté et on montre la révision ; oui, on renvoie ; on révise le mois suivant. |
| 11.3 | Si la règle change, recalcule-t-on l'historique ? | oui, tout ; non, à partir de la date du changement ; les deux en parallèle pendant n mois. |
| 11.4 | Profondeur d'historique conservée ? | 12 mois ; 24 ; illimitée. |

## 12. Données et qualité

| # | Question | Réponses possibles |
|---|---|---|
| 12.1 | Quelle est la source de chaque donnée (attendu, reçu, BL, DESADV) et sa fraîcheur ? | tables ERP copiées chaque jour ; saisie manuelle ; mixte. |
| 12.2 | Que faire d'une ligne que l'ERP montre reçue sans réception dans l'extraction ? | non notée, comptée à part ; notée 0 ; notée 100. |
| 12.3 | Réceptions manuelles sans commande, réceptions sur mauvais article ou mauvais fournisseur ? | listées, non notées ; corrigées dans l'ERP ; ignorées. |
| 12.4 | La date de création des commandes est-elle disponible (pour les urgences et le délai observé) ? | oui dans l'ERP ; posée par la synchronisation ; non. |
| 12.5 | Comment valider la nouvelle mesure contre l'ancien OTD Excel ? | double calcul sur deux ou trois mois, fournisseur par fournisseur, écarts expliqués. |
| 12.6 | Quels fournisseurs pilotes ? | un quotidien, un hebdomadaire, un mensuel, un sans EDI. |

## 13. Gouvernance

| # | Question | Réponses possibles |
|---|---|---|
| 13.1 | Qui peut modifier les règles (poids, tranches, maille) et avec quelle traçabilité ? | administrateur ; responsable approvisionnement ; comité. |
| 13.2 | La règle est-elle communiquée aux fournisseurs, et sous quelle forme ? | une page de règles ; le détail de leurs lignes ; rien. |
| 13.3 | Comment un litige fournisseur est-il instruit ? | détail ligne à ligne partagé ; réunion mensuelle ; pas de procédure. |
| 13.4 | À quelle cadence relit-on la règle ? | annuelle ; après le premier semestre ; jamais. |

## Le livrable de l'atelier

Une page « Procédure OTD » qui tient en une colonne : la maille (4.1), la date de référence (3.1), les trois
questions par ligne (5, 6, 8) avec leurs barèmes, les exclusions (9), l'agrégation (10), l'arrêté (11), et dix
exemples chiffrés couvrant : jour parfait, partiel, avance, un jour de retard, quatre jours, jamais livré, rattrapage
groupé, créneau sur jour fermé, urgence, fournisseur sans EDI. Chaque exemple devient un test de l'application.
