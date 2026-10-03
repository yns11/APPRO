# Règles métier du moteur MRP / approvisionnement

Toutes les règles ci-dessous sont implémentées dans `backend/appro/engine` et **paramétrables** :
globalement (`EngineParams`, page *Paramètres & règles*), par article (référentiel, et par semaine ISO)
ou par lien article-fournisseur (référentiel). Les valeurs par défaut sont entre crochets.

**Le mode d'emploi tient en une phrase : vide, c'est l'ERP ; un chiffre, c'est votre plan ; le stock se
recalcule.** L'approvisionneur ne saisit que deux lignes du tableau, *Plan* et *Ajustement*. Tout le reste
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
  réelle, réceptions réelles, commandes fermes non reçues en backlog, cellules du plan expirées. Le **futur**
  = aujourd'hui et après : besoin sur PDP, commandes fermes restantes, plan, propositions.
* **Fenêtre** : du point zéro à aujourd'hui + `horizon_days` [120]. Le **tableau d'approvisionnement**
  commence au plus tard du point zéro et du lundi de la semaine en cours moins `history_weeks` [2]
  semaines (la semaine en cours est toujours affichée en entier depuis son lundi) ; le moteur calcule la
  fenêtre entière, seul l'affichage est tronqué.
* **Calendrier ouvré** : `working_weekdays` [1-5] + fermetures (`APPRO_HOLIDAYS`). Les délais fournisseurs
  sont en **jours ouvrés** ; les jours de livraison autorisés sont par fournisseur (`delivery_weekdays`).

## 2. Besoin (une seule ligne du tableau)

1. **Lissage du PDP hebdomadaire** sur les jours ouverts de la semaine ISO (`spread_rounding`) : `exact`
   [défaut] quantités entières dont la somme égale le PDP ; `none` quotient réel ; `per_day` `ROUND(qté / n)`
   (classeur historique).
2. **Éclatement nomenclature (1 niveau)** du PDP : besoin composant = Σ production × `qty_per` ×
   (1 + `scrap_pct` %) ; `consumption_offset_days` [0] décale la consommation.
3. **Besoin effectif par composant** (`production_mode`, `actual_then_remainder` [défaut]) : **jours passés** =
   consommation réelle déclarée du composant (`fct_consumption_actual`, **déjà éclatée en amont** ; jour sans
   déclaration = 0, `missing_actual_policy` [`zero`]) ; **semaine en cours** = reliquat `max(PDP éclaté de la
   semaine − consommation déjà déclarée cette semaine, 0)` réparti sur les jours ouvrés restants, aujourd'hui
   compris ; **semaines suivantes** = PDP éclaté. Exemple (2 composants par unité) : PDP 2 000 unités, mercredi,
   1 000 et 1 200 composants consommés lundi et mardi → (4 000 − 2 200) / 3 = 600 par jour mercredi, jeudi,
   vendredi ; PDP déjà dépassé → 0. Autres modes : `actual_then_plan` (réel du jour s'il existe, 0 déclaré
   compris, sinon plan), `plan_only`, `actual_only`.
4. Un PDP importé et **actif** remplace le PDP ERP pour les programmes qu'il contient.

La ligne *Besoin* affiche le consommé (passé, réel) puis le requis (futur, PDP) ; le classeur exporté les
distingue par la date de référence.

## 3. Approvisionnements : voies fournisseur, plan, backlog

Le tableau montre, **par fournisseur de l'article** (les liens actifs, par priorité, puis tout fournisseur
présent dans les commandes ou réceptions), quatre lignes : *Ferme*, *Prévisionnel*, *Reçu* (ERP, lecture) et
*Plan* (saisie). Les lignes suivantes sont à la maille article : *Proposition CBN*, *Ajustement*, *Scenario
ERP*, *Scenario Plan*. Les stocks somment les voies.

### 3.1 La commande = un créneau de livraison

L'ERP ne fournit pas d'identifiant stable de commande. L'extraction `commandes_edi` agrège les lignes d'achat
par **fournisseur | article | date | ferme** (`S-000025|P-00204943|20261006|1`). Une commande de l'application
est donc « ce qu'un fournisseur doit livrer un jour donné pour un article », ferme (`Ordre_ferme = Oui`) ou
prévisionnelle. Le restant ERP (`Quantite_restante`) est la quantité ouverte.

### 3.2 Les deux scenarios

| Jour calculé | Scenario ERP | Scenario Plan |
|---|---|---|
| Avant la référence | historique reconstitué (§ 4) | idem |
| À partir de la référence | R + Ferme + A − besoin | R + Plan + CBN + A − besoin |

R = réceptions du jour, Ferme = commandes fermes ERP ouvertes du jour (toutes voies), Plan = cellules du plan
(§ 3.3), CBN = propositions (si `include_proposals_in_plan` [oui]), A = ajustements.

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
* Le plan se saisit en granularité jour ; une colonne semaine est en lecture et bascule sur le jour au clic.
* Un **commentaire** facultatif se pose par clic droit sur une cellule (Plan ou Ajustement).
* **Recopie** : la cellule active (Plan ou Ajustement) porte un petit carré dans son coin ; le tirer le long de la
  ligne recopie la valeur sur les cellules traversées, comme Excel (copie, pas de série). Les colonnes semaine
  et le passé sont sautés ; une seule transaction, un seul recalcul.

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

L'affichage ne change pas le calcul : le Scenario ERP et le pré-remplissage du plan comptent le **restant ERP**
des commandes à partir de la référence, et rien pour le passé (§ 3.2).

Un **clic** sur une cellule Ferme à partir de la date de référence **ignore** les commandes fermes de ce
fournisseur et de ce jour : elles sortent du Scenario ERP et ne pré-remplissent plus le Plan (cellule vide =
0 ; une cellule saisie garde sa valeur). Un nouveau clic les rétablit. Le passé ne s'ignore pas (il ne compte
dans aucun stock). Ces jours ignorés sont listés dans *Saisies & journal* et comptés dans le KPI *Plan*.

### 3.3 ter La ligne Reçu : réceptions et avis d'expédition (DESADV)

La ligne *Reçu* de chaque fournisseur montre les réceptions ERP et, **en italique sur fond orange, les DESADV
non reçus** : un avis d'expédition dont le BL n'apparaît pas encore dans les réceptions, affiché au jour
d'émission avec sa quantité annoncée. Cette quantité **n'entre dans aucun calcul** (ni stock, ni backlog, ni
proposition) : elle prévient que la marchandise est en route. Point **vert** si le message est *Traité* et son
traitement final *OK*, **rouge** sinon (erreur, en attente, créé). Le DESADV disparaît du tableau dès que son BL
est réceptionné, ou si l'approvisionneur **double-clique** la cellule et confirme (drapeau `desadv_hidden`,
listé et rétablissable dans *Saisies & journal*). Les messages annulés ne sont jamais affichés. Les réceptions
portent elles aussi un point : vert si leur BL correspond à un DESADV traité, rouge sinon.

### 3.4 Backlog fournisseur

Les commandes fermes **passées** ne comptent dans aucun stock (leur restant ERP n'est pas fiable). Par
fournisseur, sur les `backlog_days` [28] derniers jours avant la référence :

`backlog = max(0, Σ quantité commandée des fermes passées − Σ réceptions)`

Cumul, sans lettrage : une réception en avance ou en retard de quelques jours ne crée pas de fausse alerte.
Le backlog est affiché (KPI de la fiche, liste du cockpit, en-tête de la voie, alerte `BACKLOG`) et **sort
tout seul** : réception dans l'ERP, ou commande plus ancienne que la fenêtre. Si l'approvisionneur l'attend
encore, il tape la quantité dans le plan au jour prévu ; sinon il ne fait rien.


Le backlog est **affiché** (en-tête de voie, fiche article), jamais une alerte ni un KPI du cockpit.

### 3.5 Ajustements et stock de référence

Seules les commandes et les réceptions sont lues de l'ERP : les ajustements sont des **saisies** (ligne
*Ajustement*, toute date, quantité signée ou expression). Daté du jour d'initialisation du stock ou avant, un
ajustement **corrige le stock initial** (le stock ERP est souvent faux, l'application ne l'écrit jamais) et
persiste jusqu'à sa suppression ; rappelé dans le KPI *Stock de référence*. Daté après, c'est un mouvement
(casse, transfert, inventaire) compté à sa date, passé ou futur. Les ajustements comptent dans les deux
scenarios.

| Élément | Scenario ERP | Scenario Plan | Règle |
|---|---|---|---|
| Commande ERP ferme future | ✔ | ✔ sauf cellule saisie | restant ERP |
| Commande ERP ferme passée | – | – | backlog fournisseur |
| Commande ERP prévisionnelle | information | information | `forecast_date_policy` : date réelle [défaut] ou lundi |
| **Cellule du plan** | – | ✔ | remplace la valeur ERP du jour pour ce fournisseur |
| Proposition CBN | – | ✔ (option) | `include_proposals_in_plan` [oui] |
| Réception | ✔ | ✔ | fait ERP, à sa date |
| Ajustement | ✔ | ✔ | saisie signée, toute date |

**Affichage du passé** (du point zéro à hier) : commandes fermes passées (quantité commandée), réceptions,
ajustements, besoin consommé ; les cellules du plan passées ne s'affichent pas. Le stock passé est le stock
initial projeté en avant avec ces mouvements (§ 4) ; le backlog ne regarde jamais avant le point zéro.

**Calendrier d'affichage** (`focus_weeks` [2]) : *Par défaut* détaille jour par jour la semaine en cours et
les `focus_weeks` suivantes, et agrège en semaines ISO le passé et le futur lointain ; *Jour* et *Semaine*
sont uniformes. Les lignes du tableau peuvent être **masquées pour tous les articles** (menu *Lignes*).

## 4. Projection de stock et besoin non servi

`x[j] = stock[j−1] + approvisionnements[j] + ajustements[j] − besoin[j]`, à partir du stock de référence
(`qty_on_hand − qty_blocked`, corrigé des ajustements passés), pour chaque scenario.

**Historique reconstitué** : avant le stock de référence, `stock[j−1] = stock[j] − réceptions[j] −
ajustements[j] + consommé[j]`, avec la consommation calculée (réel × nomenclature). C'est une
reconstitution, pas le stock ERP de ces jours-là ; affichée en gris, sans couverture.

Un stock physique n'est **jamais négatif** : le tableau affiche le stock physique et, dans la même cellule,
le **manque** (besoin non servi) sur fond rouge foncé. `shortage_policy` : `backlog` [défaut] le besoin non
servi est reporté (solde net négatif, les réceptions suivantes le servent d'abord) ; `lost` il est perdu.

## 5. Couverture et stock cible

* **Couverture (jours)**, affichée en petit dans chaque cellule de stock : nombre de jours futurs dont le
  besoin cumulé est couvert par le stock du jour (`coverage_unit` [`calendar`] ou `working` ;
  `coverage_tie_rule` [`covered`]). Couleurs : rouge ≤ `alert_red_days`, orange ≤ `alert_yellow_days`, vert =
  normal, neutre ≥ `overstock_days`.
* **Stock cible** (`target_policy` [`max`]) : besoin des `coverage_target_days` prochains jours et / ou stock
  de sécurité.

## 6. Alertes

| Type | Sévérité | Règle |
|---|---|---|
| `STOCKOUT` (plan) | critique | premier manque sur le Scenario Plan malgré le plan et les propositions |
| `STOCKOUT` (ERP) | critique si ≤ délai fournisseur, avertissement si ≤ `firm_horizon_days` [28], info au-delà | premier manque sur le Scenario ERP |
| `LOW_COVERAGE` | critique / avertissement | épuisement des commandes ERP ≤ seuil rouge / orange |
| `OVERSTOCK` | info | couverture ≥ `overstock_days` |
| `NEGATIVE_STOCK` | critique | stock de référence négatif |
| `BACKLOG` | avertissement (une par article) | backlog fournisseur > 0 (§ 3.4) |
| `URGENT_PROPOSAL` | critique | proposition dont la date de commande est déjà passée |
| `NO_DEMAND` | info | aucun besoin alors que du stock existe |
| `MISSING_DATA` | avertissement / info | pas de stock de référence, pas de fournisseur, pas de nomenclature |

## 7. Propositions CBN

Recalculées à chaque calcul (`generate_proposals` [oui]) sur le Scenario Plan, **après** le plan : elles ne
proposent que ce que ni l'ERP ni le plan ne couvrent. Algorithme : dès que `stock[d] < cible[d]` (sauf creux
toléré, `shortfall_tolerance_days` [0] : retour au-dessus de la cible en n jours ouvrés **sans aucun besoin non
servi**, jugé sur la série des manques quelle que soit la politique de manque), quantité = max(besoin net jusqu'au niveau de recomplètement, MOQ)
arrondie à l'UM (unité de manutention) ; fournisseur par quota [défaut] ou priorité ; livraison le premier jour ouvré autorisé du
fournisseur (`delivery_shift` [`earlier`]), ou le **lundi** (`proposal_placement = monday`) ; date de
commande = livraison − délai ouvré, **urgente** si déjà passée ; re-projection puis itération.

**Une proposition par fournisseur et par jour de livraison**, partout (tableau, listes, infobulles,
exports) : les besoins d'une même semaine ramenés au même lundi sont fusionnés. Dans le tableau, la ligne
*Proposition CBN* est **à la maille fournisseur** (une par voie, sous la ligne *Plan*), comme dans l'export Excel.

Pour la reprendre : taper la quantité dans la cellule *Plan* (la cellule grisée la prérempli). Le CBN se
recalcule sans elle.

**Refuser une proposition** : un clic sur une cellule *Proposition CBN* la barre, la retire du plan et
**interdit toute proposition à ce fournisseur entre sa date et le dimanche de sa semaine** : le besoin est servi
par une proposition placée après cette fenêtre (jamais avant : l'approvisionneur a dit « pas de livraison de ce
fournisseur cette semaine »), au prix d'un manque éventuel entre-temps, affiché dans le Scenario Plan. Un refus
enregistré sans fournisseur (anciennes données) vaut pour tous. La quantité refusée reste
affichée barrée ; un nouveau clic rétablit la proposition. Les refus sont listés dans *Saisies & journal*.

**Planning de livraison** (onglet de la fiche article) : la ligne *Plan* est présentée comme des lignes de
planning ERP, **une par fournisseur et par semaine ISO** à quantité non nulle, à partir de la date de
référence. La quantité de la semaine additionne **tout ce que porte la ligne Plan** : cellules saisies,
commandes fermes ERP reprises (cellules vides) et propositions CBN (cellules grisées) ; l'infobulle de la
quantité en donne la décomposition. Colonnes : *Quantité livrée*, *Unité*, *Date de début de livraison* (le
lundi), *Heure de début* (11:59:00 PM), *Date de fin* (le dimanche), *Heure de fin* (11:59:00 PM). Le bouton
calendrier ne garde que les semaines commençant **après** la date de référence. Une plage se sélectionne à la
souris comme dans Excel (ou par en-tête de colonne / numéro de ligne) et se copie par Ctrl+C ou le bouton
*Copier*, en texte tabulé prêt à coller dans l'ERP ou une feuille. Le bouton *FR / EN* choisit le format des
dates (`jj/mm/aaaa` ou `m/j/aaaa`) et le séparateur décimal.

## 8. Paramètres d'article par semaine

Couverture cible, seuils, surstock, stock de sécurité et cycle de commande sont fixes par article dans le
référentiel ; le bouton *Semaines* ouvre un calendrier hebdomadaire où chaque valeur peut être personnalisée
pour une semaine ISO (réversible). Le moteur applique la valeur de la semaine du jour calculé.

Le calendrier court de la semaine de référence à la **dernière semaine du PDP chargé** (table ERP ou version
importée active) ou à la fin de l'horizon, la plus lointaine des deux (26 semaines au minimum). Il s'édite
comme une feuille : cliquer une cellule et taper (Entrée, Tab, flèches), glisser pour sélectionner une plage,
Ctrl+C / Ctrl+V (bloc tabulé venant d'Excel, ou une valeur recopiée sur toute la plage), tirer le carré de la
cellule active pour recopier vers le bas, Suppr ou une cellule vide = valeur de l'article. Chaque bloc est
enregistré en une transaction (`PUT /api/params/overrides/batch`) ; *Tout rétablir* supprime toutes les valeurs
par semaine de l'article (`DELETE /api/params/overrides?scope=article_week&key1=…`).

## 9. Impact sur les programmes

Production **réalisable** par programme et semaine = PDP × part servable du composant le plus contraint,
pour trois stocks : à date, Scenario ERP, Scenario Plan (page *Impact programmes*).

## 10. Traçabilité et règles de date

Chaque écriture (cellule, référentiel, import, paramètre) est journalisée avec l'utilisateur
(`x-forwarded-email` sur Databricks Apps), l'action et le contenu.

| Objet | Date admise |
|---|---|
| Cellule du plan | à partir d'aujourd'hui (le passé n'est pas planifié) |
| Ajustement | toute date ; ≤ point zéro = correction du stock initial ; après = mouvement à sa date |
| Commande ferme ignorée | à partir d'aujourd'hui |
| Proposition CBN refusée | à partir d'aujourd'hui ; bloque ce fournisseur jusqu'au dimanche de sa semaine |
| DESADV non reçu masqué | toute date (double-clic confirmé) |
| Stock de référence | une seule date pour tous les articles, jamais après aujourd'hui (point zéro) |
| Autres tables du référentiel | sans date |

## 11. Classeur Excel « vivant »

L'export *Simulation* reproduit le tableau : par article, *Besoin*, puis *Ferme / Prévisionnel / Reçu /
Plan* par fournisseur, *Proposition CBN*, *Ajustement*, *Scenario ERP*, *Scenario Plan*, manque, cible,
couverture. Les lignes *Plan* et *Ajustement* sont des saisies (fond jaune, bleu = déjà saisi dans
l'application) ; les stocks sont des **formules** : `x = stock précédent + Σ Reçu + Σ Plan (ou Σ Ferme) +
CBN + Ajustement − Besoin`, `stock = SI(politique = "lost" ; MAX(0 ; x) ; x)`. Un test automatisé recalcule le
classeur avec LibreOffice et le compare au moteur.

**Réimport** (granularité jour) : dans chaque ligne *Plan*, une valeur différente de la ligne *Ferme* du même
fournisseur devient une cellule saisie, une valeur égale vaut « ERP » ; chaque valeur d'*Ajustement* devient
une cellule. Les cellules des articles présents dans le classeur remplacent celles de l'application.

Le **modèle du fichier PDP** (onglet `SOP - PDP`, une ligne par programme, une colonne par semaine ISO)
se télécharge dans *Imports / exports* ; les **modèles du référentiel** (un par table) dans *Référentiel*.

## 12. Approvisionneurs, délégations et droits

Les droits se lisent dans deux tables du référentiel (`ref_planners`, `ref_delegations`) et dans la colonne
*Approvisionneur* des articles ; le serveur les applique sur chaque écriture (HTTP 403 sinon), l'interface
verrouille ce qui n'est pas permis (cellules non éditables, cadenas sur l'article, boutons absents).

| Qui | Peut écrire |
|---|---|
| utilisateur non déclaré, ou inactif (**lecteur**) | rien : lecture seule partout |
| **appro** | les cellules Plan / Ajustement / Ferme / CBN, les paramètres hebdomadaires et les lignes du référentiel (articles, article ↔ fournisseur, nomenclatures, stock) **des articles de son carnet** = ceux dont la colonne *Approvisionneur* porte son nom ; ses propres délégations |
| **manager** | comme un appro sur son carnet, **plus** les règles globales du moteur, les paramètres hebdomadaires de tout article, l'import et l'activation des PDP, l'import des tables du référentiel (sauf celle des approvisionneurs) |
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
base locale au premier démarrage (`APPRO_SEED_REFERENCE`, oui en local seulement). Délais, conditionnements
et quotas n'existent pas dans le classeur : valeurs indicatives, à remplacer dans le référentiel.
