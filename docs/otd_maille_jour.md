# OTD à la maille jour : proposition

*Note de conception du 01/11/2026, non implémentée. Elle répond au constat fait sur l'article P-00150525
(EUROCAST DELLE) : des livraisons de 512 pièces reçues chaque jour prévu, notées « complétées en 2 réceptions,
+5 / +6 jours », OTD 50 ou 80 au lieu de 100.*

## 1. Diagnostic : d'où vient la cascade

L'application **n'utilise pas les numéros d'ordre** (les sous-lignes d'une commande ferme, une par unité de
manutention). Le rapprochement actuel (`services/performance.py`, `match_slots`) prend les réceptions validées
d'un même (fournisseur, article, numéro de commande) **dans l'ordre des dates** et les affecte aux créneaux fermes
du plus ancien au plus récent ; un créneau est complet le jour où sa quantité est atteinte.

Cette règle « premier créneau servi d'abord » a exactement le même effet que l'imputation par numéros d'ordre de
l'ERP : **dès qu'une quantité manque une fois, chaque réception sert d'abord ce manque**, et tous les jours suivants
ne sont complets qu'à la livraison d'après. Dans les captures : un reliquat d'environ 800 pièces antérieur au 01/10
(l'ERP montre lui-même 176 / 512 ouverts le 02/10 et 192 / 512 le 14/10) ; la réception du 01/10 sert
septembre, le jour du 01/10 est complété par la réception du 06/10 (+5 j, 2 réceptions), et ainsi de suite. Sur les
onze jours affichés, la moyenne vaut ≈ 61 alors que la quantité du jour a été reçue le jour même onze fois sur onze.

Passer à la **maille jour** ne consiste donc pas à agréger les sous-lignes (l'application le fait déjà par créneau
= jour), mais à **changer la priorité d'affectation** : la réception d'un jour sert d'abord la livraison attendue
*ce jour-là*, et seulement ensuite le reliquat. Le reliquat devient alors un indicateur à part entière, compté une
fois, au lieu de dégrader tous les jours qui le suivent.

## 2. Proposition : « le jour d'abord, le reliquat ensuite »

Pour chaque couple **(fournisseur, article)**, sans tenir compte du numéro de commande ni des sous-lignes :

| Étape | Règle |
|---|---|
| 0. Mailles | **Livraison attendue** du jour J = Σ des créneaux fermes attendus le J (les identifiants de créneaux et de commandes sont gardés pour le détail). **Livraison reçue** du jour R = Σ des réceptions validées du jour R, nettée des retours et corrections négatives du même jour (les BL sont gardés pour l'indicateur DESADV). |
| 1. Jour exact | La quantité reçue le J sert d'abord la livraison attendue le J, à hauteur de la plus petite des deux. |
| 2. Reliquat | Le surplus de chaque jour R (reçu − servi à l'étape 1) sert les jours attendus **antérieurs** encore incomplets, du plus ancien au plus récent, s'ils ont moins de `otd_backlog_max_days` [30] jours. |
| 3. Avance | Le surplus restant sert les jours attendus **suivants** situés à moins de `otd_band2_days` [4] jours (livraison anticipée). |
| 4. Hors commande | Ce qui reste est une réception **hors commande** : comptée et listée, jamais notée. |
| 5. Notation | Un jour attendu est **complet** à la date de la réception qui atteint sa quantité ; `delta` = complet − attendu, en **jours ouvrés** du calendrier usine (aujourd'hui : jours calendaires). QUANTITÉ, PONCTUALITÉ, DESADV et OTD gardent leur définition et leurs paramètres actuels, appliqués au jour. Un jour toujours incomplet `otd_band2_days` jours après sa date est **non livré** (tout à 0) ; il peut être rattrapé ensuite (étape 2) : QUANTITÉ repasse à 100, PONCTUALITÉ reste à 0. |
| 6. Reliquat affiché | **Reliquat** en fin de période = Σ attendu − Σ reçu, cumulés depuis le début de l'historique, en pièces et en **jours de livraison** (÷ quantité moyenne d'un jour livré) ; positif = retard cumulé, négatif = avance. Affiché à côté de l'OTD (tableau, courbe, fiche fournisseur), **sans poids dans l'OTD** par défaut (`otd_weight_backlog` [0] si l'on veut un jour l'y intégrer). |

Agrégations : OTD d'un fournisseur sur une période = moyenne des jours-articles attendus dans la période (chaque
jour-article compte 1, option `otd_weighting = jour | quantité`) ; OTD du portefeuille pour le rapport mensuel =
**moyenne des fournisseurs** (chacun compte une fois, sinon les fournisseurs quotidiens multi-articles écrasent la
moyenne) ; le nombre de jours notés accompagne toujours le chiffre.

Le mode actuel reste disponible (`otd_matching = jour | cumul`, défaut `jour`) le temps de comparer les deux sur
des données réelles.

### Pourquoi « reliquat avant avance »

Un fournisseur qui a du reliquat et livre plus que le jour ne demande rattrape ; c'est aussi ce que fait
l'imputation ERP. L'ordre inverse (avance d'abord) transformerait tout rattrapage en livraison anticipée des jours
suivants, et laisserait le reliquat ouvert indéfiniment.

## 3. Scénarios

Convention : article livré 512 pièces par jour prévu, unité de manutention de 16 pièces, bandes de ponctualité
actuelles (±1–2 j → 60, ±3–4 j → 20), DESADV toujours présent (20 points acquis). « Actuel » = rapprochement
cumulé par commande ; « Jour » = proposition. Les jours sont ouvrés et consécutifs sauf mention.

### S0. Nominal

| Jour | Attendu | Reçu | Actuel | Jour |
|---|---:|---:|---|---|
| J1…J5 | 512 | 512 | 100 | 100 |

Aucune différence tant qu'aucune quantité ne manque.

### S1. Décalage de numéros d'ordre sans écart de quantité

Le fournisseur expédie le J1 496 pièces sur les numéros d'ordre du J1 et 16 sur ceux du J2, et ainsi de suite.
L'ERP montre J1 ouvert à 16, puis chaque jour 16 ouverts et 16 d'avance. Réceptions : 512 chaque jour.

| | Actuel | Jour |
|---|---|---|
| Résultat | 100 partout (les numéros ne sont pas lus) | 100 partout |

C'est le cas que l'on croit voir dans les captures ; il est déjà bien traité. Le problème des captures est le
scénario suivant.

### S2. Reliquat ancien stable (le cas des captures)

Un reliquat de 800 pièces existe avant J1 (septembre) ; ensuite 512 attendues et 512 reçues chaque jour.

| Jour | Attendu | Reçu | Actuel | Jour |
|---|---:|---:|---|---|
| avant J1 | 800 | 0 | non livré (0) | non livré (0), une fois |
| J1 | 512 | 512 | complété J3 (224 de J2 + 288 de J3) : +2 j → 80 | 100 |
| J2 | 512 | 512 | complété J4 : +2 j → 80 | 100 |
| J3…J5 | 512 | 512 | +2 j → 80 (avec un week-end : +4 → 60, +6 → 50) | 100 |

Reliquat affiché : 800 pièces = 1,6 jour de livraison, constant. L'OTD du mois passe de ≈ 60 à ≈ 100 et le manque
de septembre apparaît là où il est : en septembre, une fois.

### S3. Jour manqué puis rattrapé

J2 n'est pas livré ; J6 reçoit 1 024.

| Jour | Attendu | Reçu | Actuel | Jour |
|---|---:|---:|---|---|
| J1 | 512 | 512 | 100 | 100 |
| J2 | 512 | 0 | complété J3 : +1 j → 80 | non livré ; rattrapé J6 (+4 j ouvrés) : Q 100, P 20 → 60 |
| J3 | 512 | 512 | complété J4 : +1 j → 80 | 100 |
| J4, J5 | 512 | 512 | +1 j → 80 | 100 |
| J6 | 512 | 1 024 | complété J6 : 100 | 100 (le surplus de 512 solde J2) |

Actuel : cinq jours dégradés pour un seul manque. Jour : un seul jour dégradé, J2, avec la trace du rattrapage.
Si le rattrapage arrive après `otd_band2_days`, J2 reste à Q 100 / P 0 → 50 ; après `otd_backlog_max_days`, il
reste non livré (0) et le surplus devient avance ou hors commande.

### S4. Retard systématique d'un jour (limite de la méthode)

Chaque jour attendu J est reçu le J+1, tous les jours.

| | Actuel | Jour |
|---|---|---|
| Résultat | +1 j partout → 80 partout : **juste** | J1 non livré (0), puis 100 partout : **faux**, le retard est invisible |

Sans lien fiable entre numéros d'ordre et réceptions, **un décalage stationnaire d'une livraison est
indiscernable d'un reliquat ancien stable** (S2) : dans les deux cas, l'usine a en permanence un jour de livraison
de moins que prévu. La proposition choisit de lire ce cas comme S2 (c'est la lecture des approvisionneurs : « la
bonne quantité le bon jour ») et de rendre le décalage visible par le **reliquat permanent de 1,0 jour de
livraison**. Pour trancher entre S2 et S4, il faudrait une information supplémentaire : la date à laquelle l'ERP
solde chaque créneau (un relevé quotidien de `qty_open` par le job de synchronisation, comme `first_seen`), ou le
DESADV lorsqu'il référence les ordres expédiés. On la garde en option d'analyse, pas dans l'OTD.

### S5. Livraison partielle le jour, solde le lendemain

J1 attend 512 : 400 reçues le J1, 112 le J2 avec les 512 du J2 (624 reçues le J2).

| Jour | Actuel | Jour |
|---|---|---|
| J1 | complété J2 : +1 j → 80 | complété J2 : +1 j → 80 |
| J2 | complété J2 (112 puis 512 de la même réception) : 100 | jour exact 512 → 100 ; le surplus 112 solde J1 |

Identique dans les deux méthodes tant que le solde arrive. Si le solde n'arrive jamais, les 112 manquantes
deviennent un reliquat et l'on retombe dans S2 : cascade en cumul, un seul jour à 50 (Q 0, P 0, D 100) à la
maille jour. Si l'on veut qu'une quantité « presque complète » le jour même compte à l'heure (400 / 512), c'est
une tolérance de quantité (`otd_qty_tolerance_pct`, défaut 0) : la règle métier actuelle est stricte, je ne
propose pas de la changer.

### S6. Livraison anticipée et excédent

J2 attend 512 ; 512 reçues le J1 (où rien n'était attendu) et rien le J2.

| | Actuel | Jour |
|---|---|---|
| J2 | complété J1 : −1 j → 80 | complété J1 : −1 j → 80 (étape 3, avance) |

Variante avec reliquat de 512 avant J1 : la réception du J1 solde le reliquat (étape 2), J2 devient non livré.
C'est la conséquence de « reliquat avant avance » ; elle est cohérente avec l'imputation ERP et avec la réalité
physique (l'usine a reçu un jour de livraison, pas deux). Excédent sans jour attendu dans les 4 jours suivants :
hors commande, listé.

### S7. Rattrapage groupé

J1 à J4 non livrés, J5 reçoit 2 560 (5 jours).

| Jour | Actuel | Jour |
|---|---|---|
| J1 | +4 j → 60 | rattrapé J5 (+4 j) : 60 |
| J2 | +3 j → 60 | +3 j : 60 |
| J3 | +2 j → 80 | +2 j : 80 |
| J4 | +1 j → 80 | +1 j : 80 |
| J5 | 100 | 100 |

Identique dans les deux méthodes : sans aucune livraison intermédiaire, le cumul et le jour coïncident.

### S8. Jour prévu non ouvré, réception validée après le week-end

Attendu vendredi, journal d'arrivée validé lundi : aujourd'hui +3 jours calendaires → 20 ; en jours ouvrés usine
+1 → 60. Attendu un samedi (créneau ERP posé sur un jour fermé) et reçu lundi : +0 jour ouvré → 100 si l'on
compte en ouvrés. Je recommande les **jours ouvrés du calendrier usine** (référentiel calendriers du point 12).
Question de données à vérifier : `receipt_date` est-elle la date d'arrivée physique ou la date de validation du
journal ? Si c'est la validation, une livraison du vendredi validée le lundi serait notée en retard par les deux
méthodes.

### S9. Plusieurs créneaux le même jour, plusieurs réceptions le même jour

J3 porte 512 (ferme planifié) + 192 (ajout en urgence), reçus en deux camions de 352. Aujourd'hui : deux créneaux
notés séparément, le second éventuellement « complété en 2 réceptions » ; demain : un jour attendu de 704, deux
réceptions, 100. La part d'urgence (créneau créé dans le délai) reste mesurée au créneau dans l'onglet Délais.

### S10. Livraisons sans commande

| Cas | Traitement proposé |
|---|---|
| Réception d'une commande fermée (numéro à six chiffres) dont le créneau est dans l'extraction | Sert son jour attendu comme les autres (la clé ignore le numéro de commande). |
| Réception dont la commande est **hors de l'horizon** de l'extraction (plus ancienne que le premier créneau connu) | Exclue, comptée « hors fenêtre », pour ne pas être lue comme une avance au début de l'historique. |
| Réception manuelle sans commande, échantillon, consignation | Étapes 2 → 3 → hors commande ; donc un envoi non planifié peut solder un reliquat ou anticiper un jour proche. Sans numéro d'ordre fiable, on ne peut pas faire mieux ; la liste « hors commande » le rend visible. |
| Retour ou correction négative | Nettée dans la réception du jour ; si le net du jour est négatif, « retour », à part. |
| Réception sur un mauvais article ou fournisseur | Non rapprochable, comptée à part (comme aujourd'hui, 172 lignes sur le jeu de démonstration). |

### S11. Reliquat soldé à la main dans l'ERP

L'approvisionneur annule les 176 restants du 02/10. Si l'ERP réduit `qty_ordered`, le jour attend 336 : les 512
reçues donnent 100 et un surplus de 176 qui sert le reliquat ou l'avance. Si l'ERP met `qty_open` à 0 sans
réduire la quantité commandée, je propose de lire « soldé sans livraison » : le jour sort de l'OTD, compté à part
(aujourd'hui ce cas est « non évaluable » seulement si rien n'a été reçu). À défaut de cette lecture, un reliquat
annulé continuerait de peser comme un jour non livré.

### S12. Effet de la fréquence de livraison

| Fréquence | Ce qui se passe |
|---|---|
| **Quotidienne** | Ambiguïté maximale : un décalage d'une livraison = un jour ; les bandes ±2 / ±4 jours couvrent 2 à 4 livraisons. La cascade du cumul est la plus violente (S2) et l'angle mort de la maille jour (S4) le plus large. Le reliquat en jours de livraison est l'indicateur à lire en second. |
| **Hebdomadaire** | Un décalage d'une livraison = 7 jours > `otd_band2_days` : en cumul, tout passe à 0 après le premier manque ; à la maille jour, le jour manqué vaut 0 et les suivants 100. Une réception à J+1 est sans ambiguïté (le seul jour attendu proche est J) : les bandes fonctionnent bien. |
| **Mensuelle, commandes ponctuelles** | Un seul jour attendu par mois : les deux méthodes coïncident. Le rattrapage (étape 2) peut aller chercher un jour vieux de 30 jours : `otd_backlog_max_days` doit couvrir au moins un intervalle de livraison. |
| **Plusieurs camions par jour** | Agrégés dans le jour ; l'heure n'est pas mesurée (ni l'heure limite de commande). |

Deux conséquences à expliquer aux lecteurs du rapport : un fournisseur quotidien pèse 20 jours notés par mois et
par article contre 4 pour un hebdomadaire (d'où la moyenne par fournisseur pour le portefeuille) ; et une même
avance ou un même retard en jours n'a pas la même gravité selon la fréquence (option ultérieure : bandes par
fournisseur dans `ref_suppliers`, non proposée pour l'instant).

## 3 bis. Variante retenue à l'étude : QUANTITÉ stricte au jour

Proposition du 01/11 (approvisionneur) : **QUANTITÉ = 100 si la quantité du jour J est reçue le jour J, 0 sinon** ;
les règles d'affectation (jour d'abord, reliquat, avance) ne servent plus qu'à la PONCTUALITÉ et au reliquat.

**Ce que cela change.**

* QUANTITÉ ne dépend plus d'aucun rapprochement : seulement de deux totaux par jour, Σ attendu le J et Σ reçu le J
  pour le couple (fournisseur, article). Elle est donc **insensible aux choix d'affectation** (reliquat avant avance,
  âge maximal…) et à l'ambiguïté S2 / S4 sur la lecture du reliquat ; c'est la mesure la plus lisible et la moins
  discutable pour la hiérarchie : la **part de jours livrés complets le bon jour** (« jours parfaits »).
* QUANTITÉ 100 entraîne PONCTUALITÉ 100 (le jour est complet le jour même) et réciproquement. Les deux indicateurs
  ne se distinguent plus que dans l'échec : QUANTITÉ dit « pas complet le jour J », PONCTUALITÉ dit « complet
  combien de jours après ». L'**échelle se durcit** : avec le DESADV acquis, un jour parfait vaut 100, un jour
  complété à ±1–2 j vaut 50 (au lieu de 80), à ±3–4 j 30 (au lieu de 60), au-delà ou jamais livré 20 (au lieu de 50
  ou 0). Un jour livré 5 jours plus tard et un jour jamais livré ont la **même note** ; la différence reste visible
  dans le détail (statut « non livré ») et dans le reliquat. Les poids 30 / 50 / 20 avaient été fixés avec une
  QUANTITÉ « reçue un jour ou l'autre » ; ils peuvent être gardés tels quels (c'est un choix de sévérité) ou
  rééquilibrés, ils sont paramétrables.
* « Quantité exacte » à lire comme **reçu le J ≥ attendu le J** : un excédent le J (rattrapage d'un reliquat,
  S3 J6 : 1 024 reçues pour 512) ne doit pas mettre la QUANTITÉ à 0, sinon on pénalise le fournisseur qui
  rattrape ; l'excédent est traité par le reliquat et l'avance. Si l'on veut aussi sanctionner les livraisons
  excédentaires, c'est une règle à part (seuil `otd_over_delivery_pct`), pas la QUANTITÉ. La tolérance
  `otd_qty_tolerance_pct` reste à 0 par défaut : 500 reçues pour 512 attendues = 0.
* La règle rend la **date de réception** décisive : une livraison du vendredi dont le journal est validé le lundi
  donne QUANTITÉ 0 ; un créneau ERP posé sur un jour fermé (samedi) donne 0 par construction. Deux garde-fous :
  vérifier que `receipt_date` est la date d'arrivée physique (sinon prendre la date de création du journal), et
  **déplacer au jour ouvré suivant** un jour attendu qui tombe sur une fermeture usine avant de noter (décision 7).
* Le mode cumul actuel donnerait la même QUANTITÉ stricte (elle ne dépend pas du rapprochement) ; seule la
  PONCTUALITÉ reste sensible au mode. On pourrait donc livrer la QUANTITÉ stricte en premier, mais le détail par jour
  mélangerait alors une QUANTITÉ au jour et une PONCTUALITÉ en cascade : les deux changements sont à livrer ensemble.

**Scénarios recalculés** (DESADV acquis) :

| Scénario | Jour | Q / P | OTD proposition § 2 | OTD QUANTITÉ stricte |
|---|---|---|---|---|
| S0 nominal | tous | 100 / 100 | 100 | 100 |
| S2 reliquat ancien | J1…J5 | 100 / 100 | 100 | 100 |
| S3 jour manqué, rattrapé à +4 j ouvrés | J2 | 0 / 20 | 60 | 30 |
| S5 400 le J1, solde le J2 | J1 | 0 / 60 | 80 | 50 |
| S6 512 reçues la veille | J2 | 0 / 60 | 80 | 50 |
| S7 rattrapage groupé | J1 … J4 | 0 / 20, 20, 60, 60 | 60, 60, 80, 80 | 30, 30, 50, 50 |
| S4 retard systématique d'un jour | J2…Jn | 100 / 100 | 100 (angle mort inchangé) | 100 (angle mort inchangé) |
| jour jamais livré | | 0 / 0 | 20 | 20 |
| jour livré 6 jours après | | 0 / 0 | 50 | 20 |

| # | Décision supplémentaire | Recommandation |
|---|---|---|
| 7 | QUANTITÉ stricte au jour (reçu le J ≥ attendu le J), jour attendu sur fermeture usine déplacé au jour ouvré suivant | Oui aux deux ; garder 30 / 50 / 20 dans un premier temps et relire l'échelle sur un mois réel. |

## 4. Effets de bord dans l'application

* **API et écrans** : le détail « par commande » devient « par jour attendu » (`/api/performance/otd` : `orders` →
  `deliveries`, avec la liste des créneaux et commandes du jour, les BL, la part servie le jour / en rattrapage /
  en avance). Export Excel, page Performance, onglet Performance de la fiche fournisseur et indicateur « OTD 12
  mois » suivent. Le reliquat s'ajoute au tableau (pièces, jours de livraison, ancienneté du plus vieux jour
  ouvert) et à la courbe (axe secondaire).
* **Délais observés** : le délai « première apparition → complet » et le biais se calculent avec la date de
  complétude de la maille jour ; l'urgence et le profil des jours de commande restent au créneau.
* **File d'exceptions** : un reliquat de plus de `otd_backlog_max_days` jours mérite une exception « reliquat
  ancien à solder ou annuler dans l'ERP » (type nouveau), car il fausse le reliquat affiché et la lecture S2 / S4.
* **Paramètres communs** (nouveaux, tous exposés dans *Paramètres › Règles communes*) : `otd_matching`,
  `otd_backlog_max_days`, `otd_delta_unit` (ouvrés | calendaires), `otd_weighting`, `otd_qty_tolerance_pct`,
  `otd_weight_backlog` ; les six paramètres actuels sont inchangés.
* **Chiffres qui bougent après coup** : un jour de M non livré peut être rattrapé en M+1 (Q repasse à 100). D'où
  l'**arrêté mensuel** ci-dessous.
* **Première semaine de l'historique** : sans jours attendus avant le début de l'extraction, les surplus de
  rattrapage y sont lus comme des avances ; les 12 mois du rapport ne touchent pas cette zone, mais le détail peut
  la montrer : à marquer « amorçage ».
* **Tests** : chaque scénario ci-dessus devient un cas de test paramétré (`tests/test_otd_jour.py`), les deux
  modes étant calculés sur les mêmes fixtures.

## 5. Le rapport mensuel à la hiérarchie

Avant l'application, chaque approvisionneur envoyait un OTD par fournisseur pour le mois écoulé. Pour que le
chiffre envoyé reste celui que l'on retrouve ensuite :

1. **Arrêté** du mois M calculé le `otd_band2_days` + 1 jour ouvré après la fin de M (le temps que le dernier
   jour de M soit évaluable), **enregistré** (table `otd_snapshots` : mois, fournisseur, approvisionneur, OTD,
   QUANTITÉ, PONCTUALITÉ, DESADV, jours notés, reliquat fin de mois, paramètres utilisés, version des données).
2. La page Performance affiche l'arrêté et, s'il diffère, la valeur **révisée** (rattrapages tardifs,
   corrections ERP) avec l'écart ; le rapport envoyé est l'arrêté.
3. Export du rapport mensuel (par approvisionneur, un fournisseur par ligne, courbe 12 mois) dans la mise en page
   de l'ancien envoi : il me faut un exemplaire de cet ancien rapport pour la reproduire.
4. Le mois d'un jour attendu est celui de sa **date attendue** (pas celle de la réception), comme aujourd'hui.

## 6. Décisions attendues

| # | Question | Recommandation |
|---|---|---|
| 1 | Lecture d'un décalage stationnaire (S4) : reliquat ancien, ou retard systématique ? | Reliquat (lecture approvisionneur), rendu visible par le reliquat en jours de livraison. |
| 2 | Écart en jours ouvrés usine ou calendaires ? | Ouvrés. |
| 3 | Âge maximal d'un reliquat rattrapable | 30 jours ; au-delà, non livré définitif et exception « à solder ». |
| 4 | Poids de chaque jour-article : 1, ou proportionnel à la quantité ? | 1 ; quantité en option. |
| 5 | Portefeuille et rapport hiérarchie : moyenne des jours ou des fournisseurs ? | Des fournisseurs. |
| 6 | Arrêté mensuel figé | Oui, enregistré, valeur révisée affichée à côté. |

Mise en œuvre estimée : deux jours (moteur de rapprochement et tests un jour, API, écrans, export, documentation
et pense-bête un jour), plus l'arrêté mensuel et son export (une demi-journée, après réception de l'ancien
modèle). Une extraction réelle (commandes, réceptions, DESADV sur 12 mois de quelques fournisseurs, voir la
demande du 01/11) permettrait de calculer les deux modes côte à côte avant de basculer.
