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
* **Calendrier ouvré** : `working_weekdays` [1-5] + fermetures (`APPRO_HOLIDAYS`) **+ la table Calendriers du
  Référentiel** (`ref_calendars`), visibles dans *Paramètres › Données › Calendrier usine*. Couvertures, seuils,
  surstock, délais : tout compte en **jours ouvrés**. Le **délai fournisseur** (`ref_suppliers.lead_time_days` [5],
  remplaçable par lien : `lead_time_override`) est **inclusif** : commandé lundi avec un délai de 5, livrable le lundi
  suivant. Les jours de livraison autorisés sont par fournisseur (`delivery_weekdays`).
* **Calendriers métier** (table *Calendriers*, une ligne = un calendrier, une clé, une période, une nature) :
  * *usine* : une **fermeture** (ni besoin, ni livraison, ni jour ouvré compté) ou un jour **ouvert**
    exceptionnellement (un samedi travaillé : il compte comme jour ouvré, reçoit du besoin et des livraisons) ;
    une fermeture l'emporte sur un jour ouvert ;
  * *fournisseur* (clé : code fournisseur) : le fournisseur n'expédie ni ne livre ; *transit* (clé : code fournisseur
    ou **nom du transporteur** : tous les fournisseurs dont c'est le mode de transport) : transporteur à l'arrêt,
    aucune livraison ; *quai* (clé :
    nom du quai) : quai fermé, les fournisseurs dont **tous** les quais par défaut sont fermés ne livrent pas ce jour-là.
    Ces jours sont retirés des **jours de livraison possibles** du fournisseur : la proposition CBN glisse au jour
    standard précédent tenable, ou ◇ sur un autre jour ouvré, exactement comme un jour refusé ; le besoin de l'usine
    et le calendrier ouvré ne changent pas. Une « fermeture d'expédition » se déclare comme une fermeture fournisseur
    sur les jours de livraison qu'elle empêche.
* **Heure limite de commande** (`ref_suppliers.cutoff_time`, HH:MM, heure de Paris : `APPRO_TIMEZONE`) : une
  commande passée **après** cette heure part le **jour ouvré suivant** ; le délai du fournisseur compte à partir de
  là, la première livraison tenable recule donc d'un jour ouvré dès que l'heure est passée (le tableau de bord et la
  fiche fournisseur le signalent : « heure limite dépassée aujourd'hui »). Vide = aucune heure limite.

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
     dans aucun programme où il est absent ; sans terme, la consommation MOM (déclarée) de l'article est utilisée.
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
l'état *Traité* et au traitement final *OK* porte ce BL **chez le même fournisseur** (un numéro de BL n'est unique
que chez son émetteur : jamais de rapprochement croisé entre fournisseurs), **rouge** sinon (réception manuelle, DESADV absent, en
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

## 4 bis. Stock ERP de la veille : écart, dérive, calage

L'ERP photographie chaque jour le stock physique par article et entrepôt (`fct_stock_snapshot` ←
`stock_snapshot` : `item_id, snapshot_date, entrepot, stock_physique` ; entrepôts `B06` et `B06VRAC` par défaut,
400 jours conservés (`stock_history_days`, variable du bundle, `--stock-history-days` du job, widget du notebook, `APPRO_STOCK_HISTORY_DAYS`) ; `APPRO_ERP_STOCK_TABLE` vide = fonction absente, aucune colonne, tuile ni onglet). Le
snapshot d'un jour est connu à sa fin : celui d'aujourd'hui n'est jamais comparé. Ce stock ne remplace **jamais**
le stock de référence (point zéro) : il sert à mesurer l'écart et à le corriger d'un geste.

* **Écart** = stock ERP du jour − stock projeté du même jour (solde net : stock − manque cumulé quand le stock
  est à 0). **Négatif** : l'application surestime le stock, une rupture peut se cacher (ton avertissement, en
  tête du filtre, couverture « selon l'ERP » dans la fiche). **Positif** : elle sous-estime, risque de commander
  trop ou trop tôt (ton information).
* **Dérive** : les `stock_drift_persist` [3] derniers snapshots dépassent tous le seuil, `stock_drift_pct` [10] %
  du stock cible du jour **et** `stock_drift_min_packs` [1] UM du fournisseur principal, avec le **même signe**
  (règles communes réglables). Un signe qui change d'un jour à l'autre est un décalage de déclaration
  (consommation déclarée le lendemain, réception comptabilisée après coup), pas une dérive. Une dérive est une
  **donnée à corriger** (`STOCK_DRIFT`, pastille bleue, compteur *Données*), jamais un statut.
* **Où** : cockpit (tuile *Écart de stock ERP* qui filtre, dérives négatives d'abord ; colonnes *Stock ERP
  (veille)* et *Écart ERP − projeté* ; date du snapshot dans l'en-tête, « ancien » au-delà de deux jours
  ouvrés) ; tableau d'approvisionnement (badge *dérive ERP* sur l'en-tête de l'article, « ERP n » sous le stock
  projeté des jours passés, orange / bleu au-delà du seuil, infobulle avec les entrepôts) ; fiche article (tuile
  *Stock ERP (veille)*, couverture « selon l'ERP » quand le snapshot est celui de la veille, onglet *Stock ERP* :
  snapshots en courbe contre le stock projeté, lecture de l'écart par entrepôt, calages passés) ; courbes
  (points ERP sur le passé) ; feuille d'inventaire (colonnes *Stock ERP* et *Écart appli − ERP*, ● sur les
  dérives) ; journal de synchronisation (`erp_stock_snapshot`).
* **Caler sur l'ERP** (fiche article ; en lot sur les articles filtrés du cockpit pour les managers et
  administrateurs) : un **ajustement signé daté du snapshot**, égal à l'écart (ajouté à un ajustement déjà posé ce
  jour-là), commenté « Calage sur le stock ERP du … », une seule action annulable et journalisée
  (`POST /api/entries/adjustments/calibrate`). Le stock projeté rejoint le stock ERP ce jour-là, le CBN et les
  alertes se recalculent ; le point zéro et l'ERP ne changent pas. Droits des saisies (approvisionneur du carnet,
  manager, administrateur) ; les articles hors carnet, sans snapshot ou déjà alignés sont ignorés et nommés. Un
  article calé tous les mois a un problème de déclaration, pas de stock.

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

## 9 bis. Tableau de bord (ex-cockpit) : deux onglets, et la page Recherche

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

**Pense-bête intégré** : le bouton **?** de l'en-tête (tous les utilisateurs) ouvre le
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

## 9 ter. Planning transport et transports (identifiants EM)

**Page « Planning transport »** (règle du 05/11/2026, troisième version) : **un seul écran, les quais**. En haut,
**douze boutons de période** en deux groupes : *Cette semaine* et ses cinq jours, *Semaine prochaine* et ses cinq
jours (l'**horizon** : rien au-delà). Une semaine montre, par quai (lignes, `B15` prend 55 % de la hauteur) et par
jour ouvré (colonnes), **le nombre de camions en gros chiffres** et « n à vous », puis une tuile par camion avec le
seul nom du fournisseur ; totaux par jour (en bas) et par quai (à droite). Un jour montre l'**agenda des quais** : une
ligne par créneau de la grille automatique (07:00 → 15:30), une ligne « < 7h » au-dessus et « ≥ 16h » en dessous pour
les camions placés à la main en dehors, une colonne par quai sans séparation de voies, un bloc = un camion (deux
côte à côte = la capacité), un créneau forcé en orange, un quai fermé hachuré, le trait rouge « maintenant ». À droite
de l'en-tête : **Navette MAF** (avec le nombre de commandes MAF du **prochain jour ouvré** encore sans navette),
**Retour EV**, **Camion ponctuel**, puis l'imprimante (**PDF du planning**), le **CSV du carnet** et le **CSV des
navettes MAF**.
Ni écran Fournisseurs, ni Planning MAF, ni liste déroulante de périmètre : tous les camions de tous les fournisseurs
sont là, les quais étant partagés.

**Cinq tons, partout** (semaine, agenda, menu, PDF) : **bleu** = les camions automatiques du **carnet sélectionné en
haut à gauche** (les fournisseurs liés aux articles de ce carnet ; sans carnet choisi, le vôtre) ; **rouge** = ceux de
ce carnet **à vérifier** ; **jaune** = ce qui est posé à la main : toute navette MAF, les retours EV et les camions
ponctuels de ce carnet ; **gris** = les camions des autres carnets (un point rouge s'ils ont quelque chose à
vérifier) ; **gris barré** = annulé (il reste dans l'agenda jusqu'à la fin de son jour). Un **petit point à droite**
marque un camion automatique **touché à la main** (déplacé, scindé, rétabli ou annoté), pour le distinguer d'un camion
jamais modifié. La couleur suit
le carnet affiché ; **les droits suivent l'e-mail connecté** : un approvisionneur **ne modifie que les camions de son
propre carnet et les navettes**, quel que soit le carnet affiché ; un manager ou un administrateur, tous. Un camion qu'on
ne peut pas modifier n'offre que *CSV ATR* (et le *BL de retour EV* d'un retour EV).

**Aucun numéro de commande n'est affiché** : ni dans les tuiles, ni dans le menu, ni dans les infobulles, ni dans les
PDF. Un camion se lit en **articles** (article, désignation, quantité restante ; deux commandes d'un même article sont
fusionnées) ; une navette en article, fournisseur et quantité. Les identifiants ERP restent dans la base et dans le
journal d'audit, pas à l'écran.

L'application **crée, modifie et annule** les transports ; elle n'envoie **aucun e-mail** : les fournisseurs et les
transporteurs sont prévenus par le système de transport (ATR) à partir du fichier CSV.

- **Identifiant** : `EM` + **5 chiffres + une lettre** (`EM00123A`). Le numéro, **incrémental et jamais réutilisé**,
  vient d'un compteur partagé (`app_counters`, verrouillé en PostgreSQL) et désigne une **famille** de camions ; la
  lettre classe le camion dans sa famille : `A` pour un camion nouveau, `B` puis `C`… pour les camions issus d'une
  **scission** (même numéro, lettre suivante) ; pour la **navette MAF**, le premier camion de la journée prend `A`, le
  deuxième `B`, et ainsi de suite. Un **retour EV** porte `EM` + **4 chiffres + `EV`** (`EM0001EV`), d'un compteur à
  part. Le menu ne propose pas de copie de l'identifiant : il se lit sur la tuile et dans le menu.
- **Camions des fournisseurs** : tous automatiques (§ 9 ter bis). Personne ne crée un camion de fournisseur à la main.
- **Navette MAF** (bouton) : le jour (le prochain jour ouvré d'abord), le créneau et les quais de l'**aller**, les
  commandes MAF de ce jour à cocher (fournisseur, article, désignation, quantité ; celles qui n'ont pas encore de
  navette sont précochées), des **lignes saisies** libres (Article, Désignation, Quantité telle que tapée : « 6 U »,
  « 18 PAL »), puis l'option **Aller / Retour** : la navette repart chargée vers le MAF sur les **quais retour**
  choisis, avec ses propres lignes retour ; le retour n'a **ni heure ni créneau** (même horaire que l'aller, ATR
  affine) et **n'occupe pas de place** dans l'agenda ni de ligne dans le fichier ATR. Une navette emporte au moins une
  commande ou une ligne. Son menu ajoute **PDF de la navette** (fiche A4 : les deux trajets, leurs lignes, cases de
  visa) et **Modifier les lignes** ; l'auteur reste visible (« créée par QUENTIN »).
- **Camions sans commande** (boutons de droite, pour l'un de **vos** fournisseurs ; jour, créneau, quais, transporteur
  et note) : le **retour EV** quand le fournisseur prévient qu'il vient enlever ses emballages vides ; le **camion
  ponctuel** pour tout autre camion hors ordres fermes (échantillons, retour de pièces, dépannage…). Jamais touchés
  par la réconciliation, comptés dans les quais et dans le CSV ATR.
- **Créneau choisi à la main** (navette, retour EV, camion ponctuel, déplacement, scission, rétablissement) : toute
  **demi-heure de 06:00 à 21:00** (le nombre de camions déjà là s'affiche à côté de chaque créneau). Un créneau **occupé**
  (la capacité du quai est atteinte, 2 camions par quai et par créneau) est refusé avec un avertissement qui nomme les
  camions présents et propose **le créneau libre le plus proche** (*Prendre 10:30*) ; l'approvisionneur peut **forcer**
  (*Forcer 10:00 (3 / 4)*) jusqu'à **deux fois la capacité** (4 par quai et par créneau), jamais au-delà ; le créneau
  forcé apparaît en orange dans l'agenda.
- **Fichier pour l'outil de transport (ATR)** : CSV `;`, en-tête `ID TRANSPORT;FOURNISSEUR;TRANSPORTEUR;DATE;HEURE;
  CODE FOURNISSEUR;FLUX;CHAMP1;CHAMP2;QUAIS`, **une ligne par transport et par quai de l'aller** (tous les autres champs
  répétés), date `jj/mm/aaaa`, heure `hh:mm:ss`, flux toujours `R`, **aucune ligne pour le retour d'une navette**.
  ATR affiche la colonne FOURNISSEUR comme « Route » dans le mail qu'il envoie au contact transport, et retrouve ce
  contact par CODE FOURNISSEUR ; les deux colonnes FOURNISSEUR / TRANSPORTEUR portent donc, selon le camion :
  **navette MAF** `NAVETTE HAMBACH` / `TRANSCO` ; **retour EV** `PACKAGING` / le fournisseur (son transporteur si son
  mode est un transporteur) ; **camion ponctuel** le fournisseur / le fournisseur (ou son transporteur) ; **camion
  d'un fournisseur livré par un transporteur** le fournisseur / le transporteur ; **camion d'un fournisseur qui
  livre lui-même** ses numéros de commande puis ses quais, `CO-000001 | B15 | B6 EST` (plusieurs commandes séparées
  par une virgule) / le fournisseur, qui lit ainsi ses commandes et ses quais dans le mail au lieu de son propre nom ;
  un transporteur saisi à la main sur un retour EV ou un camion ponctuel l'emporte. Un fournisseur en **Messagerie**
  n'a jamais de ligne. CODE FOURNISSEUR reste le code du fournisseur (`NAVETTE HAMBACH` pour une navette).
  *CSV ATR de ce camion* dans le menu ; dans l'en-tête, **deux fichiers** : le **CSV du carnet** (les transports
  prévus datés d'aujourd'hui ou plus tard des fournisseurs du carnet affiché, **sans les navettes MAF** :
  `GET /api/transports/atr.csv?planner=`, chaque approvisionneur charge son fichier dans ATR quand ses camions sont
  prêts, sans emporter la semaine inachevée d'un collègue ; sans carnet choisi, le sien ; un manager, tous les
  fournisseurs) et le **CSV des navettes MAF** (toutes les navettes prévues, quel que soit leur auteur :
  `GET /api/transports/atr-maf.csv`), que tout le monde peut télécharger.
- **PDF du planning** (icône imprimante) : A4 paysage, **douze pages** : la semaine en cours (quais × jours, nombres
  en gros, « n à vous », les fournisseurs à leur heure, totaux), ses cinq jours (agenda des quais, identifiant et
  fournisseur de chaque camion), la semaine prochaine, ses cinq jours ; les camions de l'approvisionneur connecté en
  bleu, à vérifier en rouge, annulés barrés ; ni quantité ni numéro de commande ; « édité le … par … » en pied.
- **Marques** : petite icône camion en haut à gauche de la cellule *Ordre ferme* du tableau d'approvisionnement
  (infobulle : identifiants), onglet **Transports** de la fiche fournisseur (articles, jamais de numéro de commande).
- **Annulation** (menu) : le transport reste avec le statut *annulé* (hors CSV global, gris barré dans l'agenda jusqu'à
  la fin de son jour, qui et quand dans son menu), l'approvisionneur le retire de l'outil de transport. **Un camion
  d'une famille scindée** (`EM00123B` pendant que `EM00123A` roule le même jour) **rend ses commandes à l'autre camion**
  de la famille (la lettre juste avant, sinon la suivante) : une quantité scindée se recompose, jamais au-delà du
  restant ERP ; le camion qui reprend est épinglé. **Rétablir** (menu d'un camion annulé) le remet tel qu'il était
  (identifiant, jour, créneau, quais) en passant par l'avertissement de créneau occupé ; un camion dont les commandes
  roulent déjà sur un autre camion du jour ne se rétablit pas. Création, annulation et rétablissement sont
  journalisés ; ils ne sont **pas annulables par Ctrl+Z** (le fichier a pu quitter l'application).
- **Droits** : écrire demande d'être approvisionneur déclaré, manager ou administrateur ; sur un camion, d'en être
  l'approvisionneur (fournisseur de son carnet) ou qu'il s'agisse d'une navette ; les deux CSV et le PDF se
  téléchargent par tout utilisateur qui voit le planning.

**Modes de transport** (colonne *Mode transport* du fournisseur, liste déroulante, « Fournisseur » partout à
l'initialisation) :

* **Fournisseur** : le fournisseur livre ; ses camions sont automatiques ;
* **MAF Hambach** : les pièces sont au magasin avancé fournisseurs ; pas de camion automatique : une **navette** peut
  emporter les commandes d'un même jour de plusieurs fournisseurs ; dans le fichier ATR, FOURNISSEUR et CODE
  FOURNISSEUR valent `NAVETTE HAMBACH` ; les écrans écrivent `HAMBACH` ;
* **Messagerie** : flux colis, pas de camion ; le fournisseur sort du planning et ses ordres fermes portent d'office
  la marque « transport créé » (icône camion, mention *Messagerie*) dans le tableau d'approvisionnement ;
* **un transporteur** (table **Transporteurs** du Référentiel : nom, contacts) : camions automatiques, la colonne
  TRANSPORTEUR du fichier ATR porte son nom.

Un fournisseur qui passe en *MAF Hambach* ou *Messagerie* (ou devient inactif) voit ses camions **à venir annulés**
par l'application à la synchronisation suivante.

## 9 ter bis. Camions automatiques

Règle arrêtée le 04/11/2026, resserrée le 05/11/2026 en deux temps (genèse : docs/transports_automatiques.md ;
code : `backend/appro/services/transports_auto.py`). **Accès** : le Planning transport, l'onglet *Transports* de la
fiche fournisseur et l'API `/api/transports` sont ouverts à **tous les utilisateurs** depuis le 09/11/2026 (fin de
l'essai) ; `APPRO_TRANSPORT_USERS` (vide par défaut) permet encore de les réserver à quelques e-mails ; l'espace
fournisseur et les marques du tableau d'approvisionnement ne changent pas.

**Qui est automatique : tout fournisseur qui livre**, lui-même (mode *Fournisseur*) ou par un transporteur. Il n'y a
plus de case « Camions automatiques » ni de notion de camion manuel ou automatique : les seuls camions créés à la main
sont la navette MAF, le retour EV et le camion ponctuel. Une colonne facultative **Plages habituelles**
(`ref_suppliers.transport_hours`, Référentiel ou fiche fournisseur › Paramétrage) : **jusqu'à deux plages** parmi
`06-10`, `08-12`, `10-14`, `12-16`, `14-18` et `16+` (≥ 16h) ; vide = n'importe quand dans la grille automatique.
Règles communes (Paramètres › Planning transport) : grille automatique de 07:00 à 15:30 toutes les 30 minutes
(`dock_slot_start`, `dock_slot_end`, `dock_slot_minutes`), **2 camions par quai et par créneau** (`dock_slot_capacity`),
le double en forçant à la main.

**Création : une réconciliation par version des données.** À chaque changement des données (synchronisation ERP,
saisie, référentiel), à la première lecture du planning, l'application compare, pour chaque fournisseur livrant et
chaque jour ouvré de l'**horizon** (semaine en cours et semaine suivante, rien au-delà), les ordres fermes ERP
attendus (articles actifs, restant > 0, hors commandes *ignorées* dans le tableau d'approvisionnement) et les camions
existants :

- un jour avec au moins un ordre ferme et sans camion reçoit **un camion** (créé par `application`, identifiant EM
  suivant, quais par défaut du fournisseur, transporteur du mode) qui emporte tous les ordres du jour ; un ordre qui
  apparaît sur un jour qui a déjà son camion **y monte** ;
- un ordre qui quitte les ordres fermes (date changée, soldé, supprimé, ignoré) **sort** de son camion ; un camion vide
  est **annulé** par l'application (l'EM n'est jamais réutilisé, le camion n'apparaît pas barré dans l'agenda) ; une
  quantité qui change est mise à jour (sauf si la commande roule sur deux camions scindés) ;
- **aujourd'hui est figé pour les retraits** (le camion du jour est en route) : un ordre apparu pour aujourd'hui ou
  demain monte dans un camion marqué **à vérifier** ; les jours passés ne bougent plus ;
- un camion **déplacé à la main** garde ses commandes même si l'ERP change leur date ; les navettes, les retours EV
  et les camions ponctuels ne sont jamais touchés ; un camion **annulé à la main** sans camion frère laisse un *jour
  supprimé* : un camion n'y renaît que si une commande absente du camion annulé apparaît (le rétablir efface le jour
  supprimé) ;
- la réconciliation tourne **une fois par version** (`app_meta.transports_reconciled_version`, verrou), est
  journalisée (créés, complétés, retirés, annulés) et n'est pas annulable par Ctrl+Z.

**Placement, une fois pour toutes.** Un camion est posé sur un créneau **à sa création** et **ne bouge plus jamais de
lui-même** : seul un approvisionneur le replace (menu › Déplacer). Les camions nouveaux d'une journée sont posés du
fournisseur le plus contraint (des plages habituelles) au plus libre, chacun : 1) parmi les demi-heures de ses
**plages habituelles** s'il en a, sinon parmi les créneaux de la grille automatique ; 2) **jamais au-delà de la
capacité** du quai (un camion multi-quais prend une place sur chacun) ; 3) sur le créneau **le plus éloigné des
camions déjà posés** sur ses quais ce jour-là (le premier camion d'un quai à 07:00, le deuxième à 15:30, le
troisième vers 11:00…), au plus tôt à égalité ; 4) plus de place dans ses créneaux → le créneau libre le plus proche de
la journée ; aucune place nulle part → dernier créneau et **à vérifier** (« quai B15 complet toute la journée :
déplacer le camion ») ; un quai fermé ce jour-là (table Calendriers, calendrier *quai*) → à vérifier aussi.

**Actions de l'approvisionneur** sur ses camions et sur les navettes : un clic sur un camion (semaine ou agenda) ouvre
son **menu** à l'endroit du clic : le camion (identifiant, à qui il est, jour, créneau, quais, ses articles, raison du
« à vérifier », note) puis *Vu* (efface « à vérifier »), **PDF de la navette** et **Modifier
les lignes** (navette), **BL de retour EV** et **Emballages à retourner** (retour EV, ci-dessous), **Déplacer** (jour, créneau parmi les demi-heures 06:00 → 21:00, quais ; contrôle du créneau
occupé), **Scinder** (une quantité par **article** part dans un second camion du même jour, même numéro et lettre
suivante, créneau choisi et contrôlé ou le plus proche libre ; les commandes d'un article donnent dans l'ordre ; **tout
camion est scindable**, un camion à un seul article par quantité), **Note**, *CSV ATR de ce camion*, **Annuler** ;
sur un camion annulé : **Rétablir** seulement ; sur le camion d'un collègue : *CSV ATR* (et le *BL de retour EV*)
seulement.

**Retour EV et BL de retour des emballages vides.** La table **Emballages** du Référentiel (`ref_packaging` :
article, code emballage, nom de l'emballage, poids unitaire facultatif ; l'article doit exister) est la nomenclature
d'emballages. Dans la fenêtre *Retour EV*, dès que le fournisseur est choisi, l'application liste **tous les emballages
des articles de ce fournisseur** (article et désignation, code emballage, nom, poids) avec un champ **Quantité
prévue** ; **toutes les lignes sont enregistrées**, avec ou sans quantité (lignes `app_transport_lines`, trajet `ev`,
le code emballage dans `code`), et toutes s'impriment sur le BL. Le menu du retour EV offre **Emballages à retourner** (même tableau, prérempli, remplacement
en bloc) et **Créer le BL de retour EV** : un PDF A4 portrait « EMPTY PACKAGING RETURN » : en-tête de la société
(`APPRO_COMPANY_HEADER`), numéro du BL = identifiant du transport, date du camion, identifiant client = code
fournisseur, immatriculation à remplir, **SHIP FROM** (`APPRO_SHIP_FROM`) et **SHIP TO** (nom et **adresse** du
fournisseur, colonne *Adresse* du Référentiel, qui remplace l'ancienne colonne *Pays*), puis une ligne par emballage :
code, nom, poids unitaire, **Planned Qty** (la quantité prévue saisie, vide sinon), **Quantity** (vide, manuscrite au
chargement), poids ; ligne TOTAL : la somme des quantités prévues, les totaux Quantity et Weight vides (manuscrits) ; note du camion en *Notes / Instructions* ; cases de visa expéditeur et
transporteur ; contacts (`APPRO_EV_RETURN_CONTACTS`) en pied. Les lignes EV comme le BL sont refusés (422) sur un
camion qui n'est pas un retour EV.

**Remises à zéro du 05/11/2026** : la migration `transports_fresh_start_2026_11` a supprimé tous les transports et
remis les compteurs à zéro ; la migration `transports_all_automatic_2026_11` retire la colonne « Camions
automatiques » des fournisseurs et l'origine `manuel` / `auto` des camions ; la migration
`transports_fresh_start_2026_11_b`, une fois les plages habituelles renseignées, purge de nouveau tous les transports
(lignes de navette comprises), les jours supprimés et les compteurs : la réconciliation suivante repose chaque camion
dans les plages de son fournisseur, à partir de `EM00001A`.

## 9 quater. File d'exceptions

L'onglet **File d'exceptions** du tableau de bord ne liste que des situations qui **appellent une décision** (une
alerte d'information – surstock, article dormant – n'y entre jamais), une ligne par exception, avec son
**propriétaire** (l'approvisionneur de l'article), son **échéance**, l'**action** à faire et un **délai de résolution**
estimé (délai du fournisseur pour une commande, un jour pour une correction de données) :

| Exception | Déclencheur | Échéance | Action proposée |
|---|---|---|---|
| commande en retard de passation | proposition CBN urgente (date limite dépassée, plancher) | la date limite | commander immédiatement |
| commande à passer | proposition dont la date limite tombe sous 5 jours ouvrés | la date limite | commander au plus tard le… |
| rupture projetée non couverte | manque qu'aucun plafond ne peut absorber, ou rupture sous 4 semaines sans proposition possible | le jour de la rupture | livraison supplémentaire, autre fournisseur |
| livraison fournisseur en retard | ordre ferme passé avec un restant ERP | la date prévue | relancer le fournisseur |
| DESADV à corriger | message EDI en erreur | son émission | corriger dans l'ERP |
| accusé de réception à valider | réception non comptabilisée depuis 7 jours ou plus | réception + 7 j | valider l'ACR |
| dérive du stock ERP, stock de référence négatif, commandes hors lien actif, article sans fournisseur actif | données (§ 4 bis, § 3, § 6) | aujourd'hui | vérifier, caler, créer le lien, déclarer un fournisseur |

**Score** (0 – 100, affiché avec sa décomposition) = **urgence** (0 – 50 : échéance dépassée 50, aujourd'hui 45,
sous 2 jours ouvrés 35, sous 5 jours 25, au-delà 10) + **production à risque** (0 – 30 : rupture projetée dans le
délai 30, sous quatre semaines 20, livraison en retard 15, rien 0) + **criticité de l'article** (0 – 20 : classe
ABC A 20, B 12, C 5, inconnue 8 ; 20 pour un article mono-source utilisé par trois programmes ou plus). La file
se filtre par exception, par propriétaire et sur les échéances dépassées ; une ligne ouvre la fiche article.

## 10. Traçabilité et règles de date

Chaque écriture (cellule, référentiel, import, paramètre) est journalisée avec l'utilisateur
(`x-forwarded-email` sur Databricks Apps), l'action et le contenu. **Annuler ou rétablir** une action revérifie
les **droits du moment** : un utilisateur redevenu lecteur, ou dont le carnet ne contient plus l'article touché,
ne peut plus revenir sur ses anciennes écritures ; un administrateur le peut toujours. Supprimer un paramètre
(règle commune, valeur hebdomadaire) demande le même droit que le poser.

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

**Imports** (référentiel, PDP, classeur de simulation) : une cellule non vide qui n'est pas un nombre valide est
**refusée avec son libellé** (jamais transformée en 0 ni en « sans valeur ») ; la virgule décimale française est
acceptée ; la ligne d'exemple du modèle n'est ignorée que si **toutes** ses cellules sont encore celles de
l'exemple ; un PDP qui répète un programme sur une même semaine, ou porte une quantité négative, est refusé en
bloc ; dans le modèle large, la colonne *program_id* l'emporte sur le nom (deux programmes peuvent porter le même
nom). Un **fournisseur inactif** garde sa voie, ses commandes et ses réceptions, mais ne reçoit plus aucune
proposition CBN. Un plafond hebdomadaire inférieur au MOQ est refusé.

### 10 ter. Écritures concurrentes

Plusieurs approvisionneurs travaillent en même temps ; aucune écriture n'écrase silencieusement celle d'un autre :

* **Référentiel** : chaque ligne porte `updated_at` ; l'écran renvoie ce tampon avec la ligne complète qu'il
  enregistre (`expected_updated_at`). Si quelqu'un a enregistré la ligne entre-temps, le serveur refuse (HTTP 409
  « modifiée depuis par X le … ») sans rien écrire ; l'écran garde les valeurs saisies et propose de recharger.
  Les modifications en lot s'arrêtent à la première ligne en conflit (les lignes déjà écrites restent une seule
  action annulable). Un import ou un script sans tampon écrit comme avant.
* **PDP** : la feuille mémorise la version active sur laquelle les cellules ont été saisies (`base_version_id`).
  Si une autre version est devenue active avant l'enregistrement (saisie ou import d'un collègue), le serveur
  refuse (409) et l'écran affiche le conflit : les cellules saisies **restent affichées en bleu**, « Recharger en
  conservant mes saisies » recharge la base, retire les cellules devenues identiques et laisse enregistrer à
  nouveau après vérification. Les cellules non enregistrées sont aussi gardées en **brouillon** dans le navigateur,
  par utilisateur : un rechargement ou un onglet fermé ne les perd pas (notice « brouillon restauré »). Quitter
  la page avec des saisies en attente demande une confirmation.
* **Cellules du tableau** : chaque cellule est une écriture unitaire datée et signée ; deux planificateurs ne
  travaillent jamais le même article sans délégation, le journal « annuler » signale « modifié depuis par X ».
* **Version des données** : l'incrément de `data_version` est écrit **dans la même transaction** que les données
  et le journal (`AppContext.commit`) ; une panne entre les deux ne peut plus laisser un cache ancien servir de
  nouvelles données. Les migrations de schéma du démarrage s'exécutent **sous un verrou** de base
  (`pg_advisory_lock`) et les corrections de données ponctuelles sont journalisées (`app_migrations`) : plusieurs
  workers qui démarrent ensemble ne rejouent ni la même modification ni les mêmes corrections.

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
| **appro** | les cellules Plan / Ajustement / Ferme / CBN et les paramètres hebdomadaires **des articles de son carnet** = ceux dont la colonne *Approvisionneur* porte son nom ; ses propres délégations ; **les données fournisseur et article de tout le référentiel**, depuis la page Référentiel, l'onglet *Paramétrage* de la fiche article (fiche, politique de stock, jours et source de consommation, fournisseurs et règles) et l'onglet *Paramétrage* de la fiche fournisseur (fiche, jours de livraison, quais, contacts, règles article ↔ fournisseur) ; techniquement, **toutes les tables du référentiel** (articles, fournisseurs, article ↔ fournisseur, termes de consommation, **commandes fermées (PO)** : déclaration depuis Recherche ou le Référentiel, programmes, nomenclatures, stock), ligne par ligne ou par import, carnet ou non (`OPEN_REFERENCE` dans `services/access.py` ; remettre à `False` pour revenir au carnet) |
| **manager** | comme un appro, **plus** la règle commune du moteur (tolérance), les paramètres hebdomadaires de tout article, la saisie, l'import et l'activation des PDP |
| **admin** | tout, y compris la table des approvisionneurs |
| tout utilisateur déclaré (ou de `APPRO_TRANSPORT_USERS` si la variable est renseignée) | voit le **Planning transport** (camions, API `/api/transports`) ; un approvisionneur n'y modifie que les camions de ses fournisseurs et les navettes MAF (un manager : tous) |
| **fournisseur** (e-mail présent dans la colonne *Contact transport* d'un fournisseur actif, et absent de la table des approvisionneurs et de `APPRO_ADMINS`) | rien ; **ne voit que l'espace fournisseur** (`/portail`) : ses commandes fermes en cours et leurs transports, son retard, les transports annoncés, le prévisionnel EDI (DELFOR) qui lui est attribué, ses DESADV, ses réceptions, avec leurs KPI et des filtres (article, dates, statut) ; toute autre route de l'API répond 403 (`supplier_gate`). Un manager ou un administrateur prévisualise cet espace avec `?supplier_id=` |

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

## 14. Performance fournisseurs : service (OTD), délais observés, segmentation

Page **Performance fournisseurs** (et onglet *Performance* de la fiche fournisseur). Règle arrêtée le 01/11/2026,
simplifiée le 02/11/2026 (fenêtre unique, le jour exact ; tranche 2 portée à cinq jours) (genèse et scénarios
chiffrés : `docs/otd_maille_jour.md`, `docs/otd_atelier_questions.md`) ; calcul dans `services/otd.py`, scénarios
rejoués par `tests/test_otd.py`.

**La maille** : une ligne = un fournisseur, un article, un jour ouvré attendu. *Attendu du jour* = somme des ordres
fermes du jour ; *reçu du jour* = somme des réceptions validées du jour. Un créneau ou une réception sur une
fermeture usine compte au jour ouvré suivant. Les numéros de commande et d'ordre ne jouent pas. Une réception d'un
article sans ordre ferme chez ce fournisseur est comptée à part, jamais notée.

**Déduite du référentiel fournisseur** (jours de livraison) : la **tolérance d'excédent**, un jour de besoin à
partir de deux jours de livraison par semaine, deux jours sinon (jour de besoin = attendu moyen par jour ouvré du
couple). La ponctualité, elle, se lit **au jour exact** pour tous les fournisseurs, quelle que soit leur fréquence
de livraison : une seule règle à expliquer, et un pluri-hebdo n'est pas noté plus sévèrement qu'un hebdo.

**Les trois indicateurs d'une ligne** (règles communes, *Paramètres › Règles communes*) :

* **QUANTITÉ** : 100 si l'attendu du jour est reçu **en entier dans le délai d'évaluation** (`otd_band2_days` [5]
  jours ouvrés) et si le jour ne crée pas d'avance au-delà de la tolérance ; 0 sinon (« non
  livrée », ou excédent). La quantité dit « c'est arrivé », la ponctualité dit « avec quel écart ». L'avance se lit
  sur le solde reçu − attendu, remis à zéro au premier lundi de chaque mois ;
* **PONCTUALITÉ** : jours ouvrés, à partir du jour attendu, jusqu'à ce que tout ce qui était dû **depuis ce jour**
  soit livré (ce qui manquait avant ne compte pas) ; une réception avant le jour, un jour sans attendu, est une
  avance. Le jour exact : 100 ; 1 à `otd_band1_days` [2] jours d'écart : `otd_band1_score` [60] ; jusqu'à
  `otd_band2_days` [5] : `otd_band2_score` [20] ; au-delà, ou jamais livré : 0. Les écarts se comptent en valeur
  absolue : une avance est notée comme un retard ;
* **DESADV** : part des BL reçus sur la période annoncés par un DESADV traité (lu par fournisseur et période, pas
  par ligne).

**OTD** d'un fournisseur sur une période = `otd_weight_qty` [30] % × QUANTITÉ moyenne + `otd_weight_punctuality`
[50] % × PONCTUALITÉ moyenne + `otd_weight_desadv` [20] % × DESADV, sur ses lignes de la période. Une ligne est
évaluée une fois complète, ou `otd_band2_days` jours ouvrés après le jour attendu si elle ne l'est pas (« non livrée »,
tout à 0). Une ligne que l'ERP montre reçue sans aucune réception dans l'extraction est **non évaluable** (comptée à
part). Les périodes sont des **semaines entières** ; une semaine appartient au mois de son mercredi. Le chiffre du
portefeuille est la **moyenne des fournisseurs** (chacun compte une fois). Un regroupement de livraisons au-delà de
la tolérance pénalise le jour du regroupement (QUANTITÉ) et les jours regroupés (avance) : double effet accepté pour
garder la règle en quatre lignes. Échelle d'une ligne, DESADV acquis : complète le jour 100 ; un ou deux jours
d'écart 80 ; trois à cinq jours 60 ; au-delà, ou jamais, 20 ; excédent 70. Exemple d'un hebdo livré le lundi,
servi +3 j, +5 j, le jour, le jour : ponctualité (20 + 20 + 100 + 100) ÷ 4 = 60, quantité 100, DESADV 100, OTD du
mois 30 + 30 + 20 = **80**. Le calcul se fait une fois par version des données et reste en cache ; chaque ligne porte
sa lecture en une phrase (colonne *Pourquoi* : « +3 j de retard → tranche 3 à 5 j → 20 ; complet dans les 5 j,
quantité acquise »).

**Fiche OTD fournisseur × mois** (`/performance/otd/<fournisseur>/<AAAA-MM>`, `GET /api/performance/otd/sheet`) :
un écran par fournisseur et par mois, du chiffre au jour : la formule avec ses chiffres et la barre des points
(acquis par indicateur, perdus) ; les trois indicateurs décomposés (jours complets, répartition des écarts par
tranche, liste des BL et de leur DESADV) ; les semaines entières du mois jour par jour (jours attendus colorés par
tranche, réceptions) ; le tableau du calcul, un jour attendu par ligne avec sa colonne *Pourquoi* ; les cumuls
attendu et reçu. On y entre depuis les **colonnes par mois** du tableau *Par fournisseur* (vue par mois, douze
derniers mois, chaque score est un lien), depuis l'icône de fiche en bout de ligne (dernier mois clos) et depuis les
barres mensuelles de l'onglet *Performance* de la fiche fournisseur. Le **dernier mois clos** (toutes ses semaines
terminées avant aujourd'hui) est le mois par défaut.

**Page Performance › OTD** : un **sélecteur de mois** (une pastille par mois, du premier mois évalué au mois en
cours, 24 au plus ; clic ou glisser, les douze derniers mois par défaut ; la période est faite des semaines entières
de ces mois), un filtre **fournisseurs multiple** et un filtre article ; quatre tuiles (OTD, quantité, ponctualité,
DESADV) portant chacune **sa courbe mensuelle** ; la carte d'arrêté ; le tableau par fournisseur avec une colonne par
mois. Le détail jour par jour n'y figure plus : il est dans la fiche du mois.

**Arrêté mensuel** (`POST /api/performance/otd/close`, managers et administrateurs ; refaire un arrêté est réservé
aux administrateurs) : fige la fiche de chaque fournisseur évalué sur le mois dans `app_otd_closings` (chiffres,
lignes, auteur, horodatage) ; une réception saisie après ne les modifie plus. Le **PDF d'arrêté**
(`GET /api/performance/otd/arrete.pdf?month=`) lit les fiches figées quand le mois est arrêté, les chiffres du jour
sinon (marqués *provisoire*) ; version **portefeuille** : un tableau récapitulatif de tous les fournisseurs (OTD,
quantité, ponctualité, DESADV, non livrés) puis **une page par fournisseur**, du plus faible au meilleur ; version
**fournisseur** : sa page seule. Carte *Arrêté mensuel* de la page Performance (choix du mois, état, PDF, bouton
d'arrêt) ; bouton *Arrêté PDF* de la fiche. La fiche à l'écran reste vivante et rappelle l'arrêté quand il existe.

**Délais observés** : par fournisseur (et par article dès qu'un fournisseur ou un article est choisi) : retard moyen
en jours ouvrés entre le jour attendu et la quantité complète ; parts à l'heure, en avance, en retard, au-delà de la
seconde tranche ; **retard récurrent** (au moins la moitié des livraisons en retard sur trois lignes ou plus) ;
**délai observé** création de la commande → quantité complète (jours ouvrés) et **biais** = observé − paramétré (dès
que le job renseigne `first_seen`) ; expédition (DESADV) → réception ; part d'**urgences** (commandes créées dans le
délai) ; jours servis en plusieurs réceptions ; **profils** par jour de la semaine. Des moyennes seulement, pas
de dispersion.

**Segmentation ABC / XYZ** (onglet *Segmentation*) : **ABC** par valeur annuelle = consommation des 52 dernières
semaines × prix standard (besoin de l'horizon sans consommation) : A = les premiers `abc_a_pct` [80] % de la
valeur cumulée, B jusqu'à `abc_b_pct` [95] %, C au-delà, « – » sans prix ; **XYZ** par coefficient de variation
(écart-type / moyenne) de la consommation hebdomadaire sur les 26 dernières semaines complètes depuis la première
semaine consommée : X < `xyz_x_cv` [0,5], Y < `xyz_y_cv` [1,0], Z au-delà ou sans consommation (quatre semaines au
moins). Criticité : programmes utilisateurs, mono-source. Chaque segment porte une **couverture cible suggérée**
(`segment_coverage_days`, « AX:3,AY:5,AZ:8,BX:5,BY:8,BZ:10,CX:8,CY:10,CZ:15 » jours ouvrés) et un **objectif de
service** par classe (`segment_service_pct`, « A:98,B:95,C:90 », information). « Appliquer la suggestion » écrit la
couverture cible suggérée dans les articles choisis (une action annulable, droits du référentiel) ; rien n'est
jamais appliqué automatiquement.
