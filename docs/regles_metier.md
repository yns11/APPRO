# Règles métier du moteur MRP / approvisionnement

Toutes les règles ci-dessous sont implémentées dans `backend/appro/engine`. La plupart sont **figées**
(communes à tous les articles, listées dans *Paramètres › Règles communes*) ; une seule règle commune se
règle (la tolérance sous la cible) ; le reste se paramètre **par article** (fiche article › *Paramétrage*,
Référentiel, et par semaine ISO pour la politique de stock), **par fournisseur** (délai, jours de livraison)
ou **par lien article ↔ fournisseur** (MOQ, UM, délai spécifique, quota, priorité, plafonds). Les valeurs par
défaut sont entre crochets. **Tous les délais, couvertures, seuils et horizons sont en jours ouvrés.**

**Le mode d'emploi tient en une phrase : vide, c'est l'ERP ; un chiffre, c'est votre plan ; le stock se
recalcule.** L'approvisionneur ne saisit que deux lignes du tableau, *Quantité à retenir* et *Ajustement*. Tout le reste
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
  Données › Calendrier usine*. Couvertures, seuils, surstock, délais : tout compte en **jours
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
   Cible et couverture suivent ce profil. Le profil réel observé et une suggestion sont donnés dans *Plan de production ›
   Adhérence PDP / réel* (premier jour où 75 % de la consommation réelle de la semaine est atteinte ; jeudi ou
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
4. Le PDP est une version de l'application, importée ou saisie, **active** ; les semaines passées suivent l'historique des versions (§ 11 bis).

La ligne *Consommation et besoin* affiche le consommé (passé, réel) puis le requis (futur, PDP) ; le classeur exporté les
distingue par la date de référence.

## 3. Approvisionnements : voies fournisseur, plan, commandes en retard

Le tableau montre, **par fournisseur de l'article**, quatre lignes : *Ordre ferme*, *Prévisionnel EDI*, *Quantité reçue* (ERP,
lecture) et *Quantité à retenir* (saisie). Les voies sont **les liens actifs article ↔ fournisseur** du Référentiel (par
priorité), et rien d'autre : les commandes, réceptions et DESADV d'un fournisseur **sans lien actif** restent
hors du tableau (hors des voies, des stocks projetés et du retard de l'article) et ne sont visibles que dans
les vues d'état (cockpit *Flux*, fiche fournisseur, *Recherche*) ; l'alerte `UNLINKED_FLOWS` les signale sur
l'article. Un fournisseur sans lien actif qui porte encore des cellules *Quantité à retenir* saisies garde une voie,
marquée « hors lien », le temps de les effacer ou de réactiver le lien. Un flux sans fournisseur suit la
première voie. Sans aucun lien actif, l'article n'a pas de voie : aucun approvisionnement n'est compté. Les lignes suivantes sont à la maille article : *Proposition CBN*, *Ajustement*, *Projeté
ERP*, *Stock projeté*. Les stocks somment les voies.

### 3.1 La commande = un créneau de livraison

L'ERP ne fournit pas d'identifiant stable de commande. L'extraction `commandes_edi` agrège les lignes d'achat
par **fournisseur | article | date | ferme** (`S-000025|P-00204943|20261006|1`). Une commande de l'application
est donc « ce qu'un fournisseur doit livrer un jour donné pour un article », ferme (`Ordre_ferme = Oui`) ou
prévisionnelle. Le restant ERP (`Quantite_restante`) est la quantité ouverte.

### 3.2 Les deux scenarios

| Jour calculé | Projeté Ferme | Stock projeté |
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
  au **premier jour de livraison standard du fournisseur** de la semaine qui n'est pas passé (`delivery_weekdays`,
  jours ouvrés du calendrier usine ; à défaut le premier jour ouvré, à défaut le lundi ; jamais avant le jour de
  référence), annoncé dans l'infobulle ; la cellule semaine affiche le total de la semaine (jours saisis plus
  jours ERP). **Maj+Entrée**, ou clic droit › *Placer le jour…*, ouvre la liste des jours de la semaine (quantité
  ERP, saisie et proposition CBN de chaque jour, jour standard surligné) pour choisir un autre jour. Reprendre une
  proposition CBN grisée depuis une cellule semaine la garde **à sa propre date** (◇ comprise). **Vider** une
  cellule semaine rend toute la semaine à l'ERP (toutes les cellules saisies de la semaine, une seule action
  annulable). Un ajustement saisi en colonne semaine se place au lundi (au jour de référence dans la semaine en
  cours ; *Placer le jour…* pour un autre jour) ; un clic *Ordre ferme* dans une colonne semaine ignore (ou
  rétablit) les commandes de tous les jours de la semaine, un clic CBN refuse (ou rétablit) toutes les
  propositions de la semaine chez ce fournisseur. L'en-tête d'une colonne semaine bascule sur le détail par jour.
* Un **commentaire** facultatif se pose par clic droit sur une cellule jour (Quantité à retenir ou Ajustement) ;
  dans une colonne semaine, clic droit › *Commentaire…*.
* **FR | EN** : deux boutons dans l'en-tête du tableau d'approvisionnement et de la fiche article passent en
  anglais les intitulés des lignes, les en-têtes de colonnes (S → W), les infobulles, la légende et les tuiles
  de la fiche article (partage d'écran ou capture pour un fournisseur anglophone) ; le reste de l'application
  reste en français. Le choix est mémorisé dans le navigateur.
* **Recopie** : la cellule active (Plan ou Ajustement) porte un petit carré dans son coin ; le tirer le long de la
  ligne recopie la valeur sur les cellules traversées, comme Excel (copie, pas de série) ; une colonne semaine
  traversée reçoit la valeur à son jour standard (même règle que la saisie). Le passé est sauté ; une seule transaction, un seul recalcul.

Il n'existe **aucun autre objet** : ni ligne de plan, ni statut de commande, ni action « dater / clôturer ».
Deux lignes en lecture sont néanmoins **cliquables** (§ 3.3 bis et § 7).

### 3.3 bis La ligne Ordre ferme : affichage et commandes ignorées

La cellule *Ordre ferme* d'un fournisseur et d'un jour affiche toujours la **quantité commandée** des commandes
fermes de ce jour, quelle que soit la quantité restant à livrer :

| Situation | Texte | Couleur |
|---|---|---|
| rien reçu, ou tout reçu | quantité commandée | vert (**soldée**) quand le restant ERP est nul, orange (**en cours**) sinon |
| livraisons partielles, restant > 0 | `restant / commandé` | orange (en cours) |
| commande **ignorée** (clic) | quantité commandée barrée | rouge |

L'affichage ne change pas le calcul : le Projeté Ferme et le pré-remplissage du plan comptent le **restant ERP**
des commandes à partir de la référence, et rien pour le passé (§ 3.2).

Un **clic** sur une cellule Ordre ferme à partir de la date de référence **ignore** les commandes fermes de ce
fournisseur et de ce jour : elles sortent du Projeté Ferme et ne pré-remplissent plus le Plan (cellule vide =
0 ; une cellule saisie garde sa valeur). Un nouveau clic les rétablit. Le passé ne s'ignore pas (il ne compte
dans aucun stock). Ces jours ignorés sont listés dans *Saisies & journal* et comptés dans le KPI *Quantité à retenir*.

### 3.3 ter La ligne Quantité reçue : journaux d'arrivée, réceptions et avis d'expédition (DESADV)

**Quelles réceptions comptent.** L'ERP fournit toutes les réceptions, quelle que soit la commande. Seules
entrent dans la ligne *Quantité reçue* et le stock celles d'une **commande ouverte** (`CO-…`) et celles d'une **commande
fermée (PO, six chiffres) déclarée** dans *Référentiel › Commandes fermées (PO)* et active : c'est le flux
dégradé (demande d'achat puis PO ponctuelle) en attendant une commande ouverte. Toute autre réception reste
visible dans *Recherche* (filtres CO / PO déclarée / autres, bouton « Déclarer la PO »), les flux et la fiche
fournisseur, sans entrer dans le stock. Une PO n'ayant pas de DESADV, ses réceptions ne sont jamais marquées
rouge pour BL non annoncé, et l'info-bulle les signale « PO … (commande fermée déclarée) ».

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

L'**info-bulle** de chaque cellule *Quantité reçue* détaille les lignes du jour (ou de la semaine) : pour chaque réception
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
*Ordre ferme*, `restant / commandé`). Une commande **ignorée** d'un clic sort de la projection mais reste en retard.
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

| Élément | Projeté Ferme | Stock projeté | Règle |
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

## 6. Statut et alertes

Le **statut** d'un article répond à une seule question : que doit faire l'approvisionneur aujourd'hui ? Il se lit
sur les données de l'article, de ses fournisseurs, de ses commandes et de son stock, sans horizon commun.

* **Critique** : la **couverture à date** (stock de la veille seul, sans aucune livraison comptée) tient au plus
  `alert_red_days` + 1 jours ouvrés ; ou une proposition CBN a sa **date limite de commande dépassée** (§ 7 :
  plancher ! ou livraison hors jour standard ◇) ; ou le stock de départ est négatif.
* **À commander** : une proposition CBN a sa **date limite de commande** dans les **5 prochains jours ouvrés** ;
  ou la couverture à date tient au plus `alert_yellow_days` jours ouvrés et aucune livraison ferme ni cellule
  *Quantité à retenir* saisie n'arrive avant l'épuisement.
* **OK** : tout le reste.

Le **surstock** (couverture à date ≥ `overstock_days`) est un filtre, pas un statut. Les **données à corriger**
(pas de stock de départ, pas de lien actif, commande ferme à venir chez un fournisseur sans lien) sont une
pastille à part, jamais dans le statut. En **multi-sourcing**, chaque part d'une proposition porte le délai du
fournisseur que le CBN a retenu (§ 7) : le statut suit la date limite la plus proche, et la colonne
*À commander au plus tard le* nomme ce fournisseur (◇ quand la livraison est hors jour standard).

| Type | Sévérité | Règle |
|---|---|---|
| `LOW_COVERAGE` | critique | couverture à date ≤ seuil rouge + 1 jour ouvré |
| `LOW_COVERAGE` | avertissement | couverture à date ≤ seuil orange, rien de ferme ni de saisi avant l'épuisement |
| `URGENT_PROPOSAL` | critique | proposition dont la date limite de commande est passée (plancher !, ou hors jour standard ◇, § 7) |
| `ORDER_DUE` | avertissement | première proposition à commander au plus tard sous 5 jours ouvrés (date limite, fournisseur, quantité, livraison, ◇) |
| `NEGATIVE_STOCK` | critique | stock de référence négatif |
| `STOCKOUT` (plan) | info | premier manque sur le stock projeté malgré le plan et les propositions (plafonds, refus) ; manque cumulé maximal |
| `OVERSTOCK` | info | couverture à date ≥ `overstock_days` |
| `CAP_REACHED` | info | manque inévitable sous les plafonds du fournisseur (§ 7) |
| `NO_DEMAND` | info | aucun besoin alors que du stock existe |
| `MISSING_DATA` | info (données) | pas de stock de référence, pas de fournisseur actif, pas de nomenclature |
| `UNLINKED_FLOWS` | info (données) | commande ferme **à venir** chez un fournisseur sans lien actif, hors tableau (§ 3) ; les réceptions et commandes passées d'un ancien fournisseur ne déclenchent rien |

La rupture sur les commandes ERP seules n'est plus une alerte : c'est une colonne (*Rupture Ferme*, calculée sur
le **Projeté Ferme** : stock + ordres fermes seuls, qui n'est plus une ligne du tableau mais reste un KPI de la
fiche article, une colonne du cockpit et une couche de l'impact programmes).

## 7. Propositions CBN

Recalculées à chaque calcul sur le stock projeté, **après** le plan : elles ne proposent que ce que ni l'ERP
ni le plan ne couvrent. Elles n'existent nulle part ailleurs ; pour en reprendre une, taper la quantité dans la
cellule *Quantité à retenir* (la cellule grisée la prérempli), le CBN se recalcule sans elle.

**Déclenchement** : un besoin non servi, ou un stock projeté **sous (100 − tolérance) % de la cible**
(`target_tolerance_pct` [15], règle commune réglable : déclenchement sous 85 % de la cible). La
quantité ramène le stock à **100 % de la cible** (besoin net = cible − stock projeté + manque), relevée au
**MOQ** puis arrondie au multiple supérieur de l'**UM** du lien.

**Placement** : sur un jour de livraison **standard** du fournisseur (`delivery_weekdays`), le dernier **avant**
le besoin ; les besoins jusqu'à la livraison suivante sont regroupés sur cette livraison (**une proposition par
fournisseur et par jour de livraison**, partout).

**Date limite de commande** = livraison − (délai − marge) jours ouvrés : le dernier jour où l'ordre peut partir.
La **marge de délai** (`lead_margin_days` [1], règle commune réglable, 0 = délai strict) reconnaît qu'un
fournisseur livre en général un peu avant son délai contractuel. Une proposition est **tenable** tant que sa
date limite n'est pas passée (≥ aujourd'hui) : le CBN vise alors la cible pleine. La date limite est affichée
« au plus tard le » (flux *À commander*, fiche article, cockpit, courbes).

**Hors jour standard ◇** : quand le jour standard n'est plus tenable mais qu'une rupture arriverait avant la
livraison standard suivante tenable, la proposition se pose sur le **premier jour ouvré tenable** avant le besoin,
marquée **◇** (« hors jour standard ») ; si rien ne manque avant la livraison standard suivante, le besoin
**attend** celle-ci (simple passage sous la cible, pas de ◇). Un fournisseur à **jours stricts**
(`strict_days`, Référentiel) ne reçoit jamais de ◇ : le besoin non tenable devient un plancher au jour standard.

**Plancher !** : quand aucun jour ouvré tenable ne précède le besoin, la proposition est **urgente** (date limite
dépassée, marquée **!**) : posée au **jour du besoin** (au jour standard pour un fournisseur à jours stricts),
limitée à la **quantité minimale qui évite la rupture** jusqu'à la livraison suivante, lotie MOQ / UM. La
tolérance ne s'applique pas au plancher. Dès la première livraison tenable, la proposition vise la cible pleine,
sans rampe. Une proposition refusée au jour du besoin glisse au jour suivant (jamais avant).

> Version réduite et lisible de ces règles (un fournisseur, pas d'ordre ferme, de refus ni de plafond) :
> [`docs/mini_cbn.ipynb`](mini_cbn.ipynb), une fonction Python dont `backend/tests/test_mini_cbn.py` vérifie qu'elle
> reproduit exactement le moteur.

**Plafonds du lien** (`max_qty_per_delivery`, `max_qty_per_week`, multiples de l'UM ; le plafond hebdomadaire
compte les commandes fermes et les cellules Quantité à retenir de la semaine ISO) : appliqués **après** le lotissement, en
tronquant au multiple d'UM ; un jour plein ne tire plus de besoin, l'excédent glisse à la livraison suivante,
une semaine pleine à la semaine suivante. Les plafonds s'appliquent aussi aux propositions urgentes. Quand même
le plancher dépasse les plafonds, le manque est **inévitable** : affiché en rouge dans le stock projeté et
remonté par l'alerte `CAP_REACHED` (« plafond fournisseur atteint »).

**Multi-sourcing** (`sourcing_policy` de l'article, `quota` [défaut]) : **chaque besoin est partagé** entre les
fournisseurs à quota au prorata des quotas (chaque part a son MOQ, son UM, son délai, ses jours de livraison
et ses plafonds ; les parts d'un même besoin forment une proposition par fournisseur) ; la part qu'un
fournisseur ne peut pas livrer (plafond) est **reportée sur l'autre fournisseur à quota** le même jour avant de
glisser ; avec `priority`, tout va au fournisseur de priorité 1. Dans le tableau, la ligne *Proposition CBN*
est **à la maille fournisseur** (une par voie, sous la ligne *Quantité à retenir*), comme dans l'export Excel.
Marques dans la cellule : **!** urgente (date limite dépassée, plancher), **▲** plafonnée, **◇** hors jour
standard ; l'infobulle donne la date limite (« à commander au plus tard le »).

**Refuser une proposition** : un clic sur une cellule *Proposition CBN* la barre, la retire du plan et interdit
toute proposition à **ce fournisseur ce jour-là seulement** (un fournisseur mardi / jeudi refusé le mardi reste
possible le jeudi) : en multi-sourcing, sa part passe aux autres fournisseurs à quota ; en mono-source, le
besoin glisse au jour de livraison ouvert suivant, au prix d'un manque éventuel entre-temps, affiché dans le
stock projeté et alerté. Un refus enregistré sans fournisseur (anciennes données) vaut pour tous. La quantité
refusée reste affichée barrée ; un nouveau clic rétablit la proposition. Les refus sont listés dans *Saisies &
journal* ; un refus **daté avant aujourd'hui a expiré** : il n'apparaît plus dans le tableau (listé « expirée »
dans *Saisies & journal*). Une proposition refusée dont la date limite est passée ou tombe sous 5 jours ouvrés
reste dans le flux *À commander* du cockpit, marquée « ignorée », hors de tout calcul, **masquée par défaut**
(bouton *Afficher les ignorées*).

**Planning de livraison** (onglet de la fiche article) : la ligne *Quantité à retenir* est présentée comme des lignes de
planning ERP, **une par fournisseur et par semaine ISO** à quantité non nulle, à partir de la date de
référence. La quantité de la semaine additionne **tout ce que porte la ligne Quantité à retenir** : cellules saisies,
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
pour trois stocks : à date, Projeté Ferme, Stock projeté (page *Plan de production*, onglet *Impact programmes*).

## 9 bis. Cockpit du jour : deux onglets, et la page Recherche

**Couvertures et stocks** : cinq tuiles qui filtrent le portefeuille d'un clic, *Critiques*, *À commander*,
*Surstock*, *Données*, *OK* (§ 6), puis une ligne de contexte (couverture médiane, valeur du stock contre la
valeur cible, en-cours ERP, articles en retard). Le **portefeuille** vient en premier (pliable) : article,
fournisseurs (liens actifs), approvisionneur, statut (pastille bleue = données à corriger), stock à date avec le
stock cible en dessous, couverture à date avec la couverture cible en dessous, *commande en cours* (prochaine
commande ferme attendue : date, fournisseur, restant), *à commander au plus tard le* (première proposition CBN :
date limite de commande, ! si dépassée, fournisseur retenu, quantité, livraison, ◇ hors jour standard), puis en
retard. Les fournisseurs sont partout affichés **code et nom** (le filtre porte sur les deux). Les colonnes
secondaires (couverture projetée, rupture projetée, rupture Ferme, en-cours ERP, quantité à retenir, CBN, valeur,
alertes) s'affichent par le menu *Colonnes* du tableau, mémorisé. Suivent la projection de la valeur du stock et les alertes prioritaires.

**Flux d'approvisionnement** : huit cartes, un clic affiche les lignes correspondantes en dessous ; les cartes et
l'en-tête de la table restent visibles pendant le défilement.

| Flux | Contenu |
|---|---|
| À commander | propositions CBN dont la **date limite de commande** est dépassée (**!**, en tête) ou tombe sous **5 jours ouvrés** (colonne *Au plus tard le*, ◇ hors jour standard) ; la tuile compte les propositions **non ignorées** ; une proposition refusée d'un clic est masquée par défaut, le bouton *Afficher les ignorées* la liste, marquée *ignorée*, hors calculs |
| Commandé | commandes fermes **apparues aujourd'hui** dans la copie ERP (`first_seen` posé par le job de synchronisation), quelle que soit la date de livraison |
| En transit | journaux d'arrivée **non validés** (statut *En transit*), plus les DESADV non annulés qu'aucun journal ne porte encore, datés des **5 derniers jours ouvrés** seulement (pas de bouton pour le reste) |
| À traiter | DESADV en **erreur**, ou **traités sans journal de saisie** (`ID_transaction_stock` vide) ; par défaut les **5 derniers jours ouvrés**, tri par date décroissante, bouton *Tout afficher* |
| À réceptionner | commandes fermes dont la livraison est attendue aujourd'hui |
| Reçu | réceptions (journaux validés) datées d'aujourd'hui |
| En retard | commandes fermes passées avec un restant ERP, **une ligne par commande** (§ 3.4) ; par défaut les **5 derniers jours ouvrés**, tri par date décroissante, bouton *Tout afficher* |
| À valider | réceptions au statut *Reçu* (journal validé, ACR non validé) depuis **7 jours** ou plus, calculées depuis les réceptions |

L'ancienne page *Propositions CBN* est remplacée par le flux *À commander* (`/propositions` y redirige).

**Recherche** : réceptions (par défaut), commandes ou DESADV du périmètre ; boutons *Flux actif* / *Flux inactif* (un lien article ↔ fournisseur actif existe, ou non, pour la ligne ; colonne *Flux*) ; texte libre sur tous les champs
(« ; » = ou), période, fournisseur ; résultat en tableau filtrable par colonne (5 000 lignes au plus, affiner
au-delà). Les filtres des pages (recherche, tableau, référentiel, flux) sont **mémorisés** entre deux visites.

**Fournisseurs** : une liste (état des articles de chaque fournisseur dans le périmètre) et une **fiche
fournisseur** (code et nom cliquables partout) : articles et règles (MOQ, UM, délai hérité ou spécifique,
quotas, plafonds, situation), commandes en retard, commandes à venir, en transit, à traiter, à valider,
à commander (date limite dépassée ou sous 5 jours ouvrés). Les filtres de colonne *Fournisseur* portent sur le
code **et** le nom, partout (cockpit, fiche article, saisies, référentiel, courbes).

**Exports** : tout tableau de l'application porte un bouton *Excel* (colonnes affichées, lignes filtrées) et un
pied de tableau (nombre de lignes, totaux) ; le tableau d'approvisionnement n'a pas de pied, son export est le
classeur de simulation (§ 11).

**Feuille d'inventaire (PDF)** : le bouton *Inventaire (PDF)* du tableau d'approvisionnement (articles filtrés :
programme, fournisseurs, recherche, tri) et de la liste *Fiches articles* (périmètre) ouvre un PDF imprimable
(`GET /api/exports/inventaire.pdf`, A4 paysage) : une ligne par article avec code, désignation, unité,
fournisseurs (code et nom), **stock théorique** (stock à date, fin de la veille) et des colonnes vides *Quantité
comptée*, *Écart*, *Commentaire*, puis une ligne de signature ; l'écart constaté se saisit ensuite comme
*Ajustement* du jour.

**Paramètres › Données** : journal de synchronisation ERP (âge de chaque table miroir, alerte au-delà de deux
heures, bandeau en haut de page si la dernière copie est en retard), articles présents dans l'ERP et absents
du référentiel, calendrier usine.

**Plan de production › Adhérence PDP / réel** : **adhérence PDP / réel** (nette = 1 − |Σ réel − Σ plan| / Σ plan ; brute =
1 − Σ par semaine ou par jour |réel − plan| / Σ plan ; biais ; profil réel par jour de semaine et suggestion de
jours de consommation, applicable d'un clic). La période se choisit avec le **sélecteur de semaines** du
tableau d'approvisionnement (les 26 semaines complètes avant la semaine en cours, mémorisé). Le **plan de
référence** est, au choix, le *PDP actuel* ou le ***PDP du lundi*** : pour chaque semaine, la dernière version
importée **avant son lundi qui contient cette semaine** (ce que le CBN a vu) ; quand aucune version
antérieure ne la contient (application pas encore en service, versions trop courtes), la **première version
importée ensuite** qui la contient sert de plan, et la semaine est signalée « importée après coup » ; sans
aucune version, « aucun plan ». Une bande sous le sélecteur montre,
semaine par semaine, la version retenue et sa date d'import.

**Fiches articles** (liste) : en-tête fixe ; colonnes *Prochaine livraison* (quantité), *Attendue le* et *Nature*
(**Ferme ERP**, **CBN** ou **Saisie**) = première quantité attendue à partir d'aujourd'hui sur la ligne *Quantité à
retenir* et le complément CBN, tous fournisseurs confondus.

**Pense-bête intégré** : le bouton **?** de l'en-tête (réservé aux administrateurs pour le moment) ouvre le
pense-bête de l'approvisionneur dans l'application, **une page par section** (navigation à gauche, pages
précédente / suivante). Un administrateur passe en **mode édition** (*Modifier*) : le texte se corrige sur place
(gras, listes, tableaux et figures conservés), *Enregistrer* publie la page pour tous les lecteurs (table
`app_help_pages`, HTML nettoyé des scripts et liens actifs, action journalisée), *Version d'origine* revient au
texte livré avec l'application (`backend/appro/help/pense_bete.json`, généré par `scripts/pense_bete.py`).
Les lecteurs voient la dernière version enregistrée, sans mention des versions précédentes ni bouton d'édition.

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

### 10 bis. Annuler / rétablir

Chaque **geste** d'un utilisateur est une *action* gardée **7 jours** avec, pour chaque ligne touchée,
son état **avant** et **après** : une cellule saisie, une recopie ou un collage (une seule action pour
toutes les cellules), un lot de cellules, une ligne du référentiel (création, modification, suppression,
modification en lot = une action), un import par fusion, une déclaration de PO, un paramètre par semaine
(valeur, lot, réinitialisation), une règle commune, un taux de rebut, l'ordre des programmes, une
sauvegarde ou un import de la feuille PDP, l'activation ou la désactivation d'une version.

| Règle | Détail |
|---|---|
| Annuler | réécrit l'état *avant* de chaque ligne touchée ; rétablir réécrit l'état *après* |
| Ordre strict | les actions d'un utilisateur s'annulent de la plus récente à la plus ancienne : annuler une action ancienne annule d'abord les plus récentes (« annuler jusqu'ici ») ; rétablir va dans l'autre sens |
| Nouvelle action | efface la pile des actions annulées de l'utilisateur (pas de branches) |
| Conflit | une ligne modifiée depuis par quelqu'un d'autre bloque l'annulation (« modifié depuis par X ») : rien n'est écrit, tout ou rien sur la chaîne |
| Qui | chacun annule ses propres actions ; un **administrateur** peut annuler celles de n'importe qui (page Saisies & journal, onglet Actions) |
| PDP | annuler une saisie ou un import de feuille **réactive la version précédente** ; la nouvelle version reste dans la liste, inactive |
| Non annulable | une table du référentiel **remplacée** par un fichier et la **suppression** d'une version de PDP : journalisées, mais l'écran demande une confirmation à la place |
| Rétention | 7 jours ; au-delà l'action disparaît du journal et ne peut plus être annulée ni rétablie |

Dans l'application : boutons ↶ / ↷ de l'en-tête (le badge compte les actions annulables), historique
« Mes actions » avec « Annuler jusqu'ici », raccourcis **Ctrl+Z** / **Ctrl+Y** (ou Ctrl+Shift+Z) hors d'un
champ de saisie, toast « Annuler » (8 s) après chaque écriture, cellules réécrites surlignées un instant.

## 11. Classeur Excel « vivant »

L'export *Simulation* reproduit le tableau : par article, *Consommation et besoin*, puis *Ordre ferme / Prévisionnel EDI /
Quantité reçue / Quantité à retenir* par fournisseur, *Proposition CBN*, *Ajustement*, *Projeté Ferme*, *Stock
projeté*, manque, cible, couverture. Les lignes *Quantité à retenir* et *Ajustement* sont des saisies (fond jaune, bleu = déjà saisi dans
l'application) ; les stocks sont des **formules** : `x = stock précédent + Σ Reçu + Σ Plan (ou Σ Ferme) +
CBN + Ajustement − Besoin`, `stock = MAX(0 ; x)` ; la cible et la couverture sont des formules sur les jours
ouvrés (lignes d'aide `wd`, `wd_cum`). Un test automatisé recalcule le classeur avec LibreOffice et le compare
au moteur.

**Réimport** (granularité jour) : dans chaque ligne *Quantité à retenir*, une valeur différente de la ligne *Ordre ferme* du même
fournisseur devient une cellule saisie, une valeur égale vaut « ERP » ; chaque valeur d'*Ajustement* devient
une cellule. Les cellules des articles présents dans le classeur remplacent celles de l'application.

### 11 bis. Plan de production : versions, historique et saisie directe

Il n'existe **aucune table ERP de PDP** : le plan de production est une **version de l'application**, importée
(classeur) ou saisie dans la feuille, dont une seule est **active**. Le **PDP effectif** suit une règle unique,
pour la feuille, le moteur et l'adhérence en mode « PDP actuel » :

* **semaine en cours et suivantes** : la version active ;
* **semaines passées**, depuis la **première semaine complète après le point zéro** du stock (jamais après la
  semaine en cours) : la version active si elle contient la semaine ; sinon la version **connue au lundi** de
  la semaine (dernière importée avant ce lundi qui la contient, ce que le CBN a vu) ; sinon la **première
  version importée ensuite** qui la contient ; sinon rien. L'en-tête de la feuille signale « hist. » et nomme
  la version au survol.

La page *Plan de production (PDP)* montre ce PDP effectif en une feuille : tous les programmes actifs du
Référentiel (un programme ajouté y figure aussitôt, badge « sans nomenclature » tant qu'il n'en a pas, plus tout
programme planifié) en lignes, semaines ISO en colonnes, du point zéro à 26 semaines (choix 26 à 104) ou à la
dernière semaine planifiée. Les semaines passées sont **grisées et masquées par défaut** (bouton « Depuis le
point zéro », période mémorisée). Les **managers et administrateurs** saisissent la semaine en cours et les
semaines à venir comme dans Excel (saisie, flèches, Ctrl+C / Ctrl+V d'un bloc, recopie par le carré, Suppr = 0),
puis **enregistrent** : les cellules modifiées sont appliquées au plan effectif et forment une **nouvelle
version active** (`source_file` = « saisie directe », nom libre ou « Saisie du jj/mm/aaaa hh:mm ») qui reprend
toutes les lignes de la version active précédente. Une **semaine passée** n'est modifiable que par un
**administrateur** : la nouvelle version l'enregistre alors entière (valeurs effectives de l'historique, puis
les cellules saisies), pour que la semaine reste complète ; les autres semaines passées continuent de suivre
l'historique. Désactiver la version active rend la main à la version la plus récente restante. L'import d'un
classeur et la liste des versions (activer, désactiver, supprimer) sont sur la même page ; les semaines passées
d'un classeur importé par un manager sont ignorées (signalées dans le rapport), conservées pour un
administrateur.

**Exporter (Excel)** écrit le PDP effectif complet (tous les programmes, du point zéro à la dernière semaine
planifiée) dans le format d'import, onglet `SOP - PDP` : le classeur se corrige et se **réimporte tel quel**.
Le **modèle vide** (semaine en cours et suivantes) reste disponible ; les **modèles du référentiel** (un par
table) sont dans *Référentiel*.

La colonne **Taux rebut (%)**, juste après le programme, porte le taux de rebut du programme (0 par défaut) :
le besoin hebdomadaire éclaté par la nomenclature est PDP × (1 + taux / 100) (§ 2). Saisi par un manager ou un
administrateur (Entrée ou sortie de la cellule), il est enregistré aussitôt, hors des versions de PDP, et
**daté du lundi de la semaine en cours** : il s'applique à la semaine en cours et aux suivantes, les semaines
passées conservent le taux en vigueur à l'époque. Les quantités affichées dans la feuille restent le PDP brut.

## 12. Approvisionneurs, délégations et droits

Les droits se lisent dans deux tables du référentiel (`ref_planners`, `ref_delegations`) et dans la colonne
*Approvisionneur* des articles ; le serveur les applique sur chaque écriture (HTTP 403 sinon), l'interface
verrouille ce qui n'est pas permis (cellules non éditables, cadenas sur l'article, boutons absents).

| Qui | Peut écrire |
|---|---|
| utilisateur non déclaré, ou inactif (**lecteur**) | rien : lecture seule partout |
| **appro** | les cellules Plan / Ajustement / Ferme / CBN et les paramètres hebdomadaires **des articles de son carnet** = ceux dont la colonne *Approvisionneur* porte son nom ; ses propres délégations ; **pour le moment, toutes les tables du référentiel** (articles, fournisseurs, article ↔ fournisseur, termes de consommation, **commandes fermées (PO)** : déclaration depuis Recherche ou le Référentiel, programmes, nomenclatures, stock), ligne par ligne ou par import, carnet ou non (`OPEN_REFERENCE` dans `services/access.py` ; remettre à `False` pour revenir au carnet) |
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
