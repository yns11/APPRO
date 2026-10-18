# Règles métier du moteur MRP / approvisionnement

Toutes les règles ci-dessous sont implémentées dans `backend/appro/engine`. La plupart sont **figées**
(communes à tous les articles, listées dans *Paramètres › Règles communes*) ; une seule règle commune se
règle (la tolérance sous la cible) ; le reste se paramètre **par article** (fiche article › *Paramétrage*,
Référentiel, et par semaine ISO pour la politique de stock), **par fournisseur** (délai, jours de livraison)
ou **par lien article ↔ fournisseur** (MOQ, UM, délai spécifique, quota, priorité, plafonds). Les valeurs par
défaut sont entre crochets. **Tous les délais, couvertures, seuils et horizons sont en jours ouvrés.**

**Le mode d'emploi tient en une phrase : vide, c'est l'ERP ; un chiffre, c'est votre plan ; le stock se
recalcule.** L'approvisionneur ne saisit que deux lignes du tableau, *Appro.* et *Ajustement*. Tout le reste
se lit.

## 1. Temps et calendrier

* **Date d'initialisation du stock (point zéro)** : la `snapshot_date` du stock de référence (`fct_stock`),
  **une seule pour tous les articles**, jamais postérieure à aujourd'hui (refusée à la saisie, à l'import et
  au calcul). Le stock de ce jour est connu **en fin de journée** ; la projection commence le lendemain ;
  **rien n'existe avant** pour le moteur (aucun historique reconstitué, aucun mouvement antérieur, un
  ajustement daté avant corrige simplement le stock initial). Sans aucune ligne de stock, le point zéro est
  aujourd'hui avec des stocks nuls.
* **Aujourd'hui (`as_of`)** : la date du jour du serveur ; `APPRO_AS_OF` ou la règle globale `as_of` ne
  servent qu'à simuler un autre jour (démonstration, tests) et ne peuvent pas précéder le point zéro.
  Le **passé** = les jours du point zéro (exclu) à aujourd'hui (exclu) : besoin consommé sur la production
  réelle, réceptions réelles, commandes fermes passées non soldées en retard, cellules du plan expirées. Le **futur**
  = aujourd'hui et après : besoin sur PDP, commandes fermes restantes, plan, propositions.
* **Fenêtre** : du point zéro à aujourd'hui + **52 semaines** (horizon fixe, tableau et PDP compris). Le
  **tableau d'approvisionnement** commence au plus tard du point zéro et du lundi de la semaine en cours
  moins les *semaines passées* choisies par l'utilisateur [2] (la semaine en cours est toujours affichée en
  entier depuis son lundi) ; le moteur calcule la fenêtre entière, seul l'affichage est tronqué.
* **Calendrier ouvré** : `working_weekdays` [1-5] + fermetures (`APPRO_HOLIDAYS`), visibles dans *Paramètres ›
  Données › Calendrier usine*. Couvertures, seuils, surstock, délais, horizon ferme : tout compte en **jours
  ouvrés**. Le **délai fournisseur** (`ref_suppliers.lead_time_days` [5], remplaçable par lien :
  `lead_time_override`) est **inclusif** : commandé lundi avec un délai de 5, livrable le lundi suivant. Les
  jours de livraison autorisés sont par fournisseur (`delivery_weekdays`).

## 2. Besoin (une seule ligne du tableau)

1. **Lissage du PDP hebdomadaire** sur les jours ouverts de la semaine ISO : quantités entières dont la somme
   égale le PDP (un article décimal garde des quantités décimales).
1 bis. **Taux de rebut du programme** (colonne *Taux rebut (%)* de la page *Plan de production*, 0 par défaut) :
   besoin de la semaine = PDP de la semaine × (1 + taux / 100), avant l'éclatement. Le taux est **daté** : une
   modification vaut pour la semaine en cours et les suivantes, les semaines passées gardent le taux qu'elles
   avaient (table `app_program_scrap`, une ligne par programme et lundi d'entrée en vigueur).
2. **Éclatement nomenclature (1 niveau)** du PDP : besoin composant = Σ production × `qty_per` ×
   (1 + `scrap_pct` %).
2 bis. **Jours de consommation de l'article** (`demand_days` [5], 1 à 5) : le besoin hebdomadaire est porté par
   les `demand_days` premiers jours ouvrés de la semaine (3 = lundi, mardi, mercredi ; 5 = toute la semaine).
   Cible et couverture suivent ce profil. Le profil réel observé et une suggestion sont donnés dans *Paramètres ›
   Données › Adhérence* (premier jour où 75 % de la consommation réelle de la semaine est atteinte ; jeudi ou
   vendredi → 5).
3. **Besoin effectif par composant** selon sa **source de consommation** (`consumption_source` [`erp`]) :
   * `erp` : **jours passés** = consommation réelle déclarée du composant (`fct_consumption_actual`, **déjà
     éclatée en amont** ; jour sans déclaration = 0) ; **semaine en cours** = reliquat `max(PDP éclaté de la
     semaine − consommation déjà déclarée cette semaine, 0)` réparti sur les jours du profil encore à venir,
     aujourd'hui compris, sinon sur les jours ouvrés restants ; **semaines suivantes** = PDP éclaté profilé.
     Exemple (2 composants par unité, 5 jours) : PDP 2 000 unités, mercredi, 1 000 et 1 200 composants consommés
     lundi et mardi → (4 000 − 2 200) / 3 = 600 par jour mercredi, jeudi, vendredi ; PDP déjà dépassé → 0.
   * `plan` : aucune consommation fiable ; le passé est le PDP éclaté, comme le futur.
   * `derived` : consommation = Σ coefficient × consommation déclarée des **articles sources** (table
     `ref_consumption_terms`) ; sources éligibles : en source `erp`, présentes dans un programme de l'article et
     dans aucun programme où il est absent ; sans terme, la consommation ERP de l'article est utilisée.
4. Un PDP importé ou saisi et **actif** remplace le PDP ERP pour les programmes qu'il contient (§ 11 bis).

La ligne *Besoin* affiche le consommé (passé, réel) puis le requis (futur, PDP) ; le classeur exporté les
distingue par la date de référence.

## 3. Approvisionnements : voies fournisseur, plan, commandes en retard

Le tableau montre, **par fournisseur de l'article** (les liens actifs, par priorité, puis tout fournisseur
présent dans les commandes ou réceptions), quatre lignes : *Ferme*, *Prévisionnel*, *Reçu* (ERP, lecture) et
*Appro.* (saisie). Les lignes suivantes sont à la maille article : *Proposition CBN*, *Ajustement*, *Projeté
ERP*, *Projeté Appro.*. Les stocks somment les voies.

### 3.1 La commande = un créneau de livraison

L'ERP ne fournit pas d'identifiant stable de commande. L'extraction `commandes_edi` agrège les lignes d'achat
par **fournisseur | article | date | ferme** (`S-000025|P-00204943|20261006|1`). Une commande de l'application
est donc « ce qu'un fournisseur doit livrer un jour donné pour un article », ferme (`Ordre_ferme = Oui`) ou
prévisionnelle. Le restant ERP (`Quantite_restante`) est la quantité ouverte.

### 3.2 Les deux scenarios

| Jour calculé | Projeté ERP | Projeté Appro. |
|---|---|---|
| Avant la référence | historique reconstitué (§ 4) | idem |
| À partir de la référence | R + Ferme + A − besoin | R + Plan + CBN + A − besoin |

R = réceptions du jour, Ferme = commandes fermes ERP ouvertes du jour (toutes voies ; les prévisionnelles sont
affichées, jamais comptées), Plan = cellules du plan (§ 3.3), CBN = propositions, A = ajustements.

**Jour de référence** : rien ne relie une réception à une commande ; la quantité encore attendue des commandes
fermes d'un fournisseur ce jour-là est `max(0, ouvert − réceptions du jour du même fournisseur)`. Une cellule
du plan saisie ce jour-là est prise telle quelle.

### 3.3 Le plan : une cellule par fournisseur et par jour

* **Cellule vide** = la quantité ferme ERP du jour (affichée en gris). Rien n'est stocké.
* **Cellule saisie** (bleue) = la décision de l'approvisionneur pour ce fournisseur et ce jour, quelle que soit
  la valeur ERP : décaler = 0 ici et la quantité là-bas ; fractionner = deux jours ; annuler = 0 ; livraison hors
  commande = une quantité sur un jour vide. Vider la cellule = retour à l'ERP.
* **Proposition CBN** (§ 7) : affichée en grisé dans une cellule vide sans ERP ; un clic puis Entrée la reprend
  (elle devient une cellule saisie ordinaire).
* Une cellule datée avant la référence a **expiré** : ignorée, listée « expirée » dans *Saisies & journal*.
* Le plan se saisit aussi bien dans une colonne jour que dans une **colonne semaine** : la valeur saisie se place
  alors au **lundi** de la semaine (au jour de référence dans la semaine en cours, le plan ne datant jamais avant) ;
  la cellule semaine affiche le total de la semaine (le lundi saisi plus les autres jours, ERP ou saisis). Même
  règle pour un ajustement (lundi, sans limite au passé) ; un clic Ferme dans une colonne semaine ignore (ou
  rétablit) les commandes de tous les jours de la semaine, un clic CBN refuse la proposition datée du lundi
  (le refus ne vaut que pour ce jour). L'en-tête d'une colonne semaine bascule sur le détail par jour.
* Un **commentaire** facultatif se pose par clic droit sur une cellule (Plan ou Ajustement).
* **Recopie** : la cellule active (Plan ou Ajustement) porte un petit carré dans son coin ; le tirer le long de la
  ligne recopie la valeur sur les cellules traversées, comme Excel (copie, pas de série) ; une colonne semaine
  traversée reçoit la valeur à son lundi. Le passé est sauté ; une seule transaction, un seul recalcul.

Il n'existe **aucun autre objet** : ni ligne de plan, ni statut de commande, ni action « dater / clôturer ».
Deux lignes en lecture sont néanmoins **cliquables** (§ 3.3 bis et § 7).

### 3.3 bis La ligne Ferme : affichage et commandes ignorées

La cellule *Ferme* d'un fournisseur et d'un jour affiche toujours la **quantité commandée** des commandes
fermes de ce jour, quelle que soit la quantité restant à livrer :

| Situation | Texte | Couleur |
|---|---|---|
| rien reçu, ou tout reçu | quantité commandée | vert (**soldée**) quand le restant ERP est nul, orange (**en cours**) sinon |
| livraisons partielles, restant > 0 | `restant / commandé` | orange (en cours) |
| commande **ignorée** (clic) | quantité commandée barrée | rouge |

L'affichage ne change pas le calcul : le Projeté ERP et le pré-remplissage du plan comptent le **restant ERP**
des commandes à partir de la référence, et rien pour le passé (§ 3.2).

Un **clic** sur une cellule Ferme à partir de la date de référence **ignore** les commandes fermes de ce
fournisseur et de ce jour : elles sortent du Projeté ERP et ne pré-remplissent plus le Plan (cellule vide =
0 ; une cellule saisie garde sa valeur). Un nouveau clic les rétablit. Le passé ne s'ignore pas (il ne compte
dans aucun stock). Ces jours ignorés sont listés dans *Saisies & journal* et comptés dans le KPI *Appro.*.

### 3.3 ter La ligne Reçu : journaux d'arrivée, réceptions et avis d'expédition (DESADV)

Une ligne de `recep_edi` est un **journal d'arrivée** de l'ERP, porteur d'un statut :

| Statut | État ERP | Dans l'application |
|---|---|---|
| **En transit** | journal créé, non validé (`posted = 0`) | affiché en italique sur fond orange avec une icône camion, **jamais compté** (ni stock, ni retard) |
| **Reçu** | journal validé, accusé de réception (ACR) non validé | réception : comptée dans le stock ; après 7 jours sans ACR, flux *À valider* |
| **Comptabilisé** | journal validé, ACR validé | réception, **fond vert** |

Chaque ligne porte un **point** qui dit si son BL est annoncé par un DESADV traité : **vert** si un DESADV à
l'état *Traité* et au traitement final *OK* porte ce BL, **rouge** sinon (réception manuelle, DESADV absent, en
erreur, créé, en attente ou annulé). Le point et le fond sont indépendants : un reçu comptabilisé sans DESADV
traité a un fond vert et un point rouge.

Les **DESADV** non annulés sont affichés en transit, au jour d'émission et pour leur quantité annoncée, tant que
leur BL n'est porté ni par une réception (journal validé) ni par un journal en transit ; dès qu'un journal porte
le BL, c'est le journal qui est affiché. Point vert si le message est *Traité / OK*, rouge sinon. Un en transit
(journal ou DESADV) peut être **masqué** par un double-clic confirmé (drapeau `desadv_hidden`, listé et
rétablissable dans *Saisies & journal*).

L'**info-bulle** de chaque cellule *Reçu* détaille les lignes du jour (ou de la semaine) : pour chaque réception
son BL, sa quantité et son état d'ACR (*comptabilisé* ou *ACR à valider depuis n j*) ; pour chaque en transit son BL,
sa quantité et son origine (journal d'arrivée non validé, ou DESADV avec son état). Une ligne sans BL s'affiche
« sans BL » : c'est une réception manuelle, qui ne peut être rapprochée d'aucun DESADV (point rouge). Ces règles
valent à l'identique dans la fiche article et dans le tableau d'approvisionnement global.

### 3.4 Commandes en retard (ex-backlog)

Les commandes fermes **passées** ne comptent dans aucun stock. Une commande ferme dont la date de livraison est
passée et dont l'ERP garde un **restant** (`qty_open`, mis à jour à la validation du journal de réception) est
**en retard** :

`en retard = Σ restant ERP des commandes fermes dont la date de livraison est antérieure à aujourd'hui`

Sans fenêtre et sans correction par les réceptions : c'est le restant ERP qui fait foi (cohérent avec la ligne
*Ferme*, `restant / commandé`). Une commande **ignorée** d'un clic sort de la projection mais reste en retard.
Le retard est affiché (KPI *En retard* de la fiche, en-tête de la voie, carte *En retard* du cockpit et de la
fiche fournisseur, **une ligne par commande** : numéro, date prévue, commandé, reçu ERP, restant) ; il **sort
tout seul** dès que l'ERP solde la commande. Si l'approvisionneur l'attend encore, il tape la quantité dans le
plan au jour prévu ; sinon il ne fait rien. Ce n'est ni une alerte ni un KPI de criticité.

### 3.5 Ajustements et stock de référence

Seules les commandes et les réceptions sont lues de l'ERP : les ajustements sont des **saisies** (ligne
*Ajustement*, toute date, quantité signée ou expression). Daté du jour d'initialisation du stock ou avant, un
ajustement **corrige le stock initial** (le stock ERP est souvent faux, l'application ne l'écrit jamais) et
persiste jusqu'à sa suppression ; rappelé dans le KPI *Stock de référence*. Daté après, c'est un mouvement
(casse, transfert, inventaire) compté à sa date, passé ou futur. Les ajustements comptent dans les deux
scenarios.

| Élément | Projeté ERP | Projeté Appro. | Règle |
|---|---|---|---|
| Commande ERP ferme future | ✔ | ✔ sauf cellule saisie | restant ERP |
| Commande ERP ferme passée | – | – | en retard si l'ERP garde un restant |
| Commande ERP prévisionnelle | information | information | à sa date réelle |
| **Cellule du plan** | – | ✔ | remplace la valeur ERP du jour pour ce fournisseur |
| Proposition CBN | – | ✔ | recalculée à chaque affichage |
| Réception | ✔ | ✔ | fait ERP, à sa date |
| Ajustement | ✔ | ✔ | saisie signée, toute date |

**Affichage du passé** (du point zéro à hier) : commandes fermes passées (quantité commandée), réceptions,
ajustements, besoin consommé ; les cellules du plan passées ne s'affichent pas. Le stock passé est le stock
initial projeté en avant avec ces mouvements (§ 4).

**Calendrier d'affichage** (*semaines détaillées*, réglage par utilisateur [2], *Paramètres › Affichage*) :
*Par défaut* détaille jour par jour la semaine en cours et les semaines détaillées suivantes, et agrège en
semaines ISO le passé et le futur lointain ; *Jour* et *Semaine* sont uniformes. Les lignes du tableau peuvent
être **masquées pour tous les articles** (menu *Lignes*). Les articles **décimaux** (unité KG, M… ou
nomenclature décimale) affichent le besoin et les stocks avec trois décimales (affichage seulement).

## 4. Projection de stock et besoin non servi

`x[j] = stock[j−1] + approvisionnements[j] + ajustements[j] − besoin[j]`, à partir du stock de référence
(`qty_on_hand − qty_blocked`, corrigé des ajustements passés), pour chaque scenario.

**Historique reconstitué** : avant le stock de référence, `stock[j−1] = stock[j] − réceptions[j] −
ajustements[j] + consommé[j]`, avec la consommation calculée (réel × nomenclature). C'est une
reconstitution, pas le stock ERP de ces jours-là ; affichée en gris, sans couverture.

Un stock physique n'est **jamais négatif** : le besoin non servi est **perdu** (jamais reporté). Le tableau
affiche le stock physique et, dans la même cellule, le **manque cumulé** (−Σ) : la somme du besoin non servi
depuis le dernier jour où le stock était positif, remise à zéro dès que le stock redevient positif ; fond
rouge foncé. Les alertes de rupture donnent le manque cumulé maximal.

## 5. Couverture et stock cible

* **Couverture (jours ouvrés)**, affichée en entier en petit dans chaque cellule de stock : nombre de jours
  ouvrés futurs dont le besoin cumulé est couvert par le stock du jour (un jour dont le besoin cumulé égale
  exactement le stock est couvert). Couleurs : rouge ≤ `alert_red_days` [2], orange ≤ `alert_yellow_days` [5],
  vert = normal, neutre ≥ `overstock_days` [30].
* **Stock cible** : besoin des `coverage_target_days` [5] prochains jours ouvrés (profil `demand_days`
  compris). Ni stock de sécurité ni cycle de commande.

## 6. Alertes

| Type | Sévérité | Règle |
|---|---|---|
| `STOCKOUT` (plan) | critique si ≤ délai fournisseur, avertissement si ≤ horizon ferme (**20 jours ouvrés**, commun), info au-delà | premier manque sur le Projeté Appro. malgré le plan et les propositions (même gradation que l'ERP : une rupture à J+177 n'est pas l'urgence d'une rupture à J+3) ; manque cumulé maximal |
| `STOCKOUT` (ERP) | critique si ≤ délai fournisseur, avertissement si ≤ 20 jours ouvrés, info au-delà | premier manque sur le Projeté ERP |
| `LOW_COVERAGE` | critique / avertissement | épuisement des commandes ERP ≤ seuil rouge / orange (jours ouvrés) |
| `OVERSTOCK` | info | couverture ≥ `overstock_days` |
| `NEGATIVE_STOCK` | critique | stock de référence négatif |
| `URGENT_PROPOSAL` | critique | proposition dont la date de commande est déjà passée (plancher dans le délai, § 7) |
| `CAP_REACHED` | avertissement | manque inévitable sous les plafonds du fournisseur (§ 7) |
| `NO_DEMAND` | info | aucun besoin alors que du stock existe |
| `MISSING_DATA` | avertissement / info | pas de stock de référence, pas de fournisseur, pas de nomenclature |

## 7. Propositions CBN

Recalculées à chaque calcul sur le Projeté Appro., **après** le plan : elles ne proposent que ce que ni l'ERP
ni le plan ne couvrent. Elles n'existent nulle part ailleurs ; pour en reprendre une, taper la quantité dans la
cellule *Appro.* (la cellule grisée la prérempli), le CBN se recalcule sans elle.

**Déclenchement** : un besoin non servi, ou un stock projeté **sous (100 − tolérance) % de la cible**
(`target_tolerance_pct` [15], seule règle commune réglable : déclenchement sous 85 % de la cible). La
quantité ramène le stock à **100 % de la cible** (besoin net = cible − stock projeté + manque), relevée au
**MOQ** puis arrondie au multiple supérieur de l'**UM** du lien.

**Placement** : sur un jour de livraison du fournisseur (`delivery_weekdays`), le dernier **avant** le besoin ;
les besoins jusqu'à la livraison suivante sont regroupés sur cette livraison (**une proposition par
fournisseur et par jour de livraison**, partout). Date de commande = livraison − délai en jours ouvrés
(inclusif) ; une date de commande déjà passée rend la proposition **urgente** (date réelle affichée telle
quelle, « à commander le »).

**Plancher dans le délai** : tant que la livraison tombe **avant la première livraison tenable** (aujourd'hui +
délai), le moteur ne remonte pas à la cible : il propose, urgente, la **quantité minimale qui évite la rupture
jusqu'à la veille de la livraison suivante**, lotie MOQ / UM. La tolérance ne s'applique pas au plancher. À
partir de la première livraison tenable, la proposition vise la cible pleine, sans rampe.

**Plafonds du lien** (`max_qty_per_delivery`, `max_qty_per_week`, multiples de l'UM ; le plafond hebdomadaire
compte les commandes fermes et les cellules Appro. de la semaine ISO) : appliqués **après** le lotissement, en
tronquant au multiple d'UM ; un jour plein ne tire plus de besoin, l'excédent glisse à la livraison suivante,
une semaine pleine à la semaine suivante. Les plafonds s'appliquent aussi aux propositions urgentes. Quand même
le plancher dépasse les plafonds, le manque est **inévitable** : affiché en rouge dans le Projeté Appro. et
remonté par l'alerte `CAP_REACHED` (« plafond fournisseur atteint »).

**Multi-sourcing** (`sourcing_policy` de l'article, `quota` [défaut]) : **chaque besoin est partagé** entre les
fournisseurs à quota au prorata des quotas (chaque part a son MOQ, son UM, son délai, ses jours de livraison
et ses plafonds ; les parts d'un même besoin forment une proposition par fournisseur) ; la part qu'un
fournisseur ne peut pas livrer (plafond) est **reportée sur l'autre fournisseur à quota** le même jour avant de
glisser ; avec `priority`, tout va au fournisseur de priorité 1. Dans le tableau, la ligne *Proposition CBN*
est **à la maille fournisseur** (une par voie, sous la ligne *Appro.*), comme dans l'export Excel. Marques dans
la cellule : **!** urgente (plancher), **▲** plafonnée.

**Refuser une proposition** : un clic sur une cellule *Proposition CBN* la barre, la retire du plan et interdit
toute proposition à **ce fournisseur ce jour-là seulement** (un fournisseur mardi / jeudi refusé le mardi reste
possible le jeudi) : en multi-sourcing, sa part passe aux autres fournisseurs à quota ; en mono-source, le
besoin glisse au jour de livraison ouvert suivant, au prix d'un manque éventuel entre-temps, affiché dans le
Projeté Appro. et alerté. Un refus enregistré sans fournisseur (anciennes données) vaut pour tous. La quantité
refusée reste affichée barrée ; un nouveau clic rétablit la proposition. Les refus sont listés dans *Saisies &
journal*. Une proposition refusée **urgente** reste listée dans le flux *À commander* du cockpit, marquée
« ignorée », hors de tout calcul.

**Planning de livraison** (onglet de la fiche article) : la ligne *Appro.* est présentée comme des lignes de
planning ERP, **une par fournisseur et par semaine ISO** à quantité non nulle, à partir de la date de
référence. La quantité de la semaine additionne **tout ce que porte la ligne Appro.** : cellules saisies,
commandes fermes ERP reprises (cellules vides) et propositions CBN (cellules grisées) ; l'infobulle de la
quantité en donne la décomposition. Colonnes : *Quantité livrée*, *Unité*, *Date de début de livraison* (le
lundi), *Heure de début* (11:59:00 PM), *Date de fin* (le dimanche), *Heure de fin* (11:59:00 PM). Le bouton
calendrier ne garde que les semaines commençant **après** la date de référence. Une plage se sélectionne à la
souris comme dans Excel (ou par en-tête de colonne / numéro de ligne) et se copie par Ctrl+C ou le bouton
*Copier*, en texte tabulé prêt à coller dans l'ERP ou une feuille. Le bouton *FR / EN* choisit le format des
dates (`jj/mm/aaaa` ou `m/j/aaaa`) et le séparateur décimal.

## 8. Paramètres d'article par semaine

Couverture cible, seuils rouge et orange et surstock sont fixes par article (fiche article › *Paramétrage*,
ou Référentiel) ; le bouton *Par semaine* ouvre un calendrier hebdomadaire où chaque valeur peut être
personnalisée pour une semaine ISO (réversible). Le moteur applique la valeur de la semaine du jour calculé.
Les jours de consommation, la politique de multi-sourcing et la source de consommation ne varient pas par
semaine.

Le calendrier court de la semaine de référence à la **dernière semaine du PDP chargé** (table ERP ou version
importée active) ou à la fin de l'horizon, la plus lointaine des deux (26 semaines au minimum). Il s'édite
comme une feuille : cliquer une cellule et taper (Entrée, Tab, flèches), glisser pour sélectionner une plage,
Ctrl+C / Ctrl+V (bloc tabulé venant d'Excel, ou une valeur recopiée sur toute la plage), tirer le carré de la
cellule active pour recopier vers le bas, Suppr ou une cellule vide = valeur de l'article. Chaque bloc est
enregistré en une transaction (`PUT /api/params/overrides/batch`) ; *Tout rétablir* supprime toutes les valeurs
par semaine de l'article (`DELETE /api/params/overrides?scope=article_week&key1=…`).

## 9. Impact sur les programmes

Production **réalisable** par programme et semaine = PDP × part servable du composant le plus contraint,
pour trois stocks : à date, Projeté ERP, Projeté Appro. (page *Impact programmes*).

## 9 bis. Cockpit du jour : deux onglets, et la page Recherche

**Couvertures et stocks** : cartes *Critiques*, *À surveiller*, *Ruptures plan*, *Couverture médiane* (médiane
des couvertures Projeté Appro. des articles avec besoin ; la moyenne, tirée par les extrêmes, est rappelée en
sous-ligne), **Valeur du stock** (Σ prix × stock à date ; sous-ligne « +x % de la valeur cible » en rouge au-dessus
de la cible, « −x % » neutre en dessous ; cible = Σ prix × stock cible du jour), *En-cours ERP*, *Surstock* ;
graphique **projection de la valeur du stock** (Projeté Appro., fin de chaque semaine ISO, pointillé = valeur du
stock cible) ; alertes prioritaires ; portefeuille (colonnes *Fournisseurs*, **Approvisionneur**, *Statut*,
**Stock à date** = stock projeté à la fin de la veille, réceptions et consommations réelles comprises, et non le
stock d'initialisation). Les champs de filtre acceptent des alternatives séparées par « ; » (`123;456` = contient
123 ou 456), partout (cockpit, fiches, tableau, recherche). Les blocs *Projection de la valeur du stock* et
*Alertes prioritaires* se plient d'un clic sur leur titre (état mémorisé dans le navigateur).

**Flux d'approvisionnement** : huit cartes, un clic affiche les lignes correspondantes en dessous ; les cartes et
l'en-tête de la table restent visibles pendant le défilement.

| Flux | Contenu |
|---|---|
| À commander | propositions CBN **urgentes** (date réelle « à commander le » dans le passé) ; une proposition refusée d'un clic reste listée, marquée *ignorée*, hors calculs |
| Commandé | commandes fermes **apparues aujourd'hui** dans la copie ERP (`first_seen` posé par le job de synchronisation), quelle que soit la date de livraison |
| En transit | journaux d'arrivée **non validés** (statut *En transit*), plus les DESADV non annulés qu'aucun journal ne porte encore |
| À traiter | DESADV en **erreur**, ou **traités sans journal de saisie** (`ID_transaction_stock` vide) |
| À réceptionner | commandes fermes dont la livraison est attendue aujourd'hui |
| Reçu | réceptions (journaux validés) datées d'aujourd'hui |
| En retard | commandes fermes passées avec un restant ERP, **une ligne par commande** (§ 3.4) |
| À valider | réceptions au statut *Reçu* (journal validé, ACR non validé) depuis **7 jours** ou plus, calculées depuis les réceptions |

L'ancienne page *Propositions CBN* est remplacée par le flux *À commander* (`/propositions` y redirige).

**Recherche** : réceptions (par défaut), commandes ou DESADV du périmètre ; texte libre sur tous les champs
(« ; » = ou), période, fournisseur ; résultat en tableau filtrable par colonne (5 000 lignes au plus, affiner
au-delà). Les filtres des pages (recherche, tableau, référentiel, flux) sont **mémorisés** entre deux visites.

**Fournisseurs** : une liste (état des articles de chaque fournisseur dans le périmètre) et une **fiche
fournisseur** (code et nom cliquables partout) : articles et règles (MOQ, UM, délai hérité ou spécifique,
quotas, plafonds, situation), commandes en retard, commandes à venir, en transit, à traiter, à valider,
urgences CBN. Les filtres de colonne *Fournisseur* portent sur le code **et** le nom.

**Exports** : tout tableau de l'application porte un bouton *Excel* (colonnes affichées, lignes filtrées) et un
pied de tableau (nombre de lignes, totaux) ; le tableau d'approvisionnement n'a pas de pied, son export est le
classeur de simulation (§ 11).

**Paramètres › Données** : journal de synchronisation ERP (âge de chaque table miroir, alerte au-delà de deux
heures, bandeau en haut de page si la dernière copie est en retard), articles présents dans l'ERP et absents
du référentiel, calendrier usine, **adhérence PDP / réel** (nette = 1 − |Σ réel − Σ plan| / Σ plan ; brute =
1 − Σ par semaine ou par jour |réel − plan| / Σ plan ; biais ; plan de référence = version du PDP connue au
lundi de chaque semaine ou plan actuel ; profil réel par jour de semaine et suggestion de jours de
consommation, applicable d'un clic).

**Fiches articles** (liste) : en-tête fixe ; colonnes *Prochaine livraison* (quantité), *Attendue le* et *Nature*
(**Ferme ERP**, **CBN** ou **Saisie**) = première quantité attendue à partir d'aujourd'hui sur la ligne *Appro.* et le
complément CBN, tous fournisseurs confondus.

**Période affichée** : sous la barre d'outils des tableaux d'approvisionnement (global et fiche article), une
rangée de semaines ISO ; cliquer une semaine, ou glisser d'une semaine à une autre, n'affiche que ces colonnes
(Maj+clic étend, « Tout » rétablit la fenêtre entière). Le choix est commun aux deux tableaux et mémorisé ; il ne
change **ni la fenêtre ni les dates du calcul**, seulement les colonnes montrées.

## 10. Traçabilité et règles de date

Chaque écriture (cellule, référentiel, import, paramètre) est journalisée avec l'utilisateur
(`x-forwarded-email` sur Databricks Apps), l'action et le contenu.

| Objet | Date admise |
|---|---|
| Cellule du plan | à partir d'aujourd'hui (le passé n'est pas planifié) |
| Ajustement | toute date ; ≤ point zéro = correction du stock initial ; après = mouvement à sa date |
| Commande ferme ignorée | à partir d'aujourd'hui |
| Proposition CBN refusée | à partir d'aujourd'hui ; bloque ce fournisseur ce jour-là seulement |
| DESADV non reçu masqué | toute date (double-clic confirmé) |
| Stock de référence | une seule date pour tous les articles, jamais après aujourd'hui (point zéro) |
| Autres tables du référentiel | sans date |

## 11. Classeur Excel « vivant »

L'export *Simulation* reproduit le tableau : par article, *Besoin*, puis *Ferme / Prévisionnel / Reçu /
Plan* par fournisseur, *Proposition CBN*, *Ajustement*, *Projeté ERP*, *Projeté Appro.*, manque, cible,
couverture. Les lignes *Appro.* et *Ajustement* sont des saisies (fond jaune, bleu = déjà saisi dans
l'application) ; les stocks sont des **formules** : `x = stock précédent + Σ Reçu + Σ Plan (ou Σ Ferme) +
CBN + Ajustement − Besoin`, `stock = MAX(0 ; x)` ; la cible et la couverture sont des formules sur les jours
ouvrés (lignes d'aide `wd`, `wd_cum`). Un test automatisé recalcule le classeur avec LibreOffice et le compare
au moteur.

**Réimport** (granularité jour) : dans chaque ligne *Appro.*, une valeur différente de la ligne *Ferme* du même
fournisseur devient une cellule saisie, une valeur égale vaut « ERP » ; chaque valeur d'*Ajustement* devient
une cellule. Les cellules des articles présents dans le classeur remplacent celles de l'application.

### 11 bis. Plan de production : saisie directe

La page *Plan de production (PDP)* montre le **PDP effectif** (ERP, remplacé programme par programme par la
version active) en une feuille : tous les programmes actifs du Référentiel (un programme ajouté y figure aussitôt,
badge « sans nomenclature » tant qu'il n'en a pas, plus tout programme planifié) en lignes, semaines ISO en colonnes, de la semaine en cours à 26 semaines (choix 26 à 104) ou à la dernière semaine
planifiée. Les **managers et administrateurs** saisissent les **semaines à venir** (la semaine en cours est
grisée, non modifiable) comme dans Excel (saisie, flèches, Ctrl+C / Ctrl+V d'un bloc, recopie par le carré,
Suppr = 0), puis **enregistrent** : les cellules modifiées sont appliquées au plan effectif et forment une
**nouvelle version active** (`source_file` = « saisie directe », nom libre ou « Saisie du jj/mm/aaaa hh:mm »).
Cette version reprend les programmes de la version active précédente et y ajoute les programmes modifiés
(copiés depuis l'ERP puis corrigés) ; les autres programmes continuent de suivre l'ERP. Désactiver la version
revient à l'état précédent. L'import d'un classeur et la liste des versions (activer, désactiver, supprimer)
sont sur la même page.

La colonne **Taux rebut (%)**, juste après le programme, porte le taux de rebut du programme (0 par défaut) :
le besoin hebdomadaire éclaté par la nomenclature est PDP × (1 + taux / 100) (§ 2). Saisi par un manager ou un
administrateur (Entrée ou sortie de la cellule), il est enregistré aussitôt, hors des versions de PDP, et
**daté du lundi de la semaine en cours** : il s'applique à la semaine en cours et aux suivantes, les semaines
passées conservent le taux en vigueur à l'époque. Les quantités affichées dans la feuille restent le PDP brut.

Le **modèle du fichier PDP** (onglet `SOP - PDP`, une ligne par programme, une colonne par semaine ISO)
se télécharge dans *Plan de production (PDP)* ; les **modèles du référentiel** (un par table) dans *Référentiel*.

## 12. Approvisionneurs, délégations et droits

Les droits se lisent dans deux tables du référentiel (`ref_planners`, `ref_delegations`) et dans la colonne
*Approvisionneur* des articles ; le serveur les applique sur chaque écriture (HTTP 403 sinon), l'interface
verrouille ce qui n'est pas permis (cellules non éditables, cadenas sur l'article, boutons absents).

| Qui | Peut écrire |
|---|---|
| utilisateur non déclaré, ou inactif (**lecteur**) | rien : lecture seule partout |
| **appro** | les cellules Plan / Ajustement / Ferme / CBN et les paramètres hebdomadaires **des articles de son carnet** = ceux dont la colonne *Approvisionneur* porte son nom ; ses propres délégations ; **pour le moment, toutes les tables du référentiel** (articles, fournisseurs, article ↔ fournisseur, programmes, nomenclatures, stock), ligne par ligne ou par import, carnet ou non (`OPEN_REFERENCE` dans `services/access.py` ; remettre à `False` pour revenir au carnet) |
| **manager** | comme un appro, **plus** la règle commune du moteur (tolérance), les paramètres hebdomadaires de tout article, la saisie, l'import et l'activation des PDP |
| **admin** | tout, y compris la table des approvisionneurs |

L'identité est l'e-mail transmis par Databricks Apps (`x-forwarded-email`), comparé sans tenir compte de la
casse à la colonne *Email*. Une **délégation** (délégant, destinataire, du, au, active) ajoute le carnet du
délégant à celui du destinataire entre les deux dates incluses (*au* vide = sans fin) ; un appro ne peut créer
que des délégations de son propre carnet. L'import d'un classeur de simulation ignore les articles hors
carnet (ligne notée dans le rapport). **Tant que la table des approvisionneurs est vide, tout utilisateur est
administrateur** : la première ligne à créer est donc son propre compte avec le rôle `admin`. Filet de sécurité :
les e-mails de `APPRO_ADMINS` (variable `admin_emails` du bundle, par défaut l'identité qui déploie) sont
administrateurs quoi que dise la table, pour qu'aucune erreur de saisie ne puisse enfermer l'installateur en
lecture seule. Le périmètre
proposé par défaut en haut de page est le carnet de l'utilisateur connecté.

## 13. Données de démonstration

`data/seed` (16 articles, 11 fournisseurs, 20 liens, 25 programmes, 28 lignes de nomenclature, PDP et
consommation réelle par composant (éclatée depuis la production réelle du classeur), 772 créneaux de commande, 477 réceptions, 16 stocks de référence au 18/09/2026), extrait
du classeur historique par `scripts/extract_seed_from_excel.py`. Le référentiel du seed est chargé dans la
base locale au premier démarrage (`APPRO_SEED_REFERENCE`, oui en local seulement). Conditionnements et quotas
n'existent pas dans le classeur : valeurs indicatives, à remplacer dans le référentiel ; le délai de tous les
fournisseurs est initialisé à 5 jours ouvrés.
