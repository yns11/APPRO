# Règles métier du moteur MRP / approvisionnement

Toutes les règles ci-dessous sont implémentées dans `backend/appro/engine` et **paramétrables** :
globalement (`EngineParams`, page *Paramètres & règles*), par article (référentiel, et par semaine ISO)
ou par lien article-fournisseur (référentiel). Les valeurs par défaut sont entre crochets.

**Le mode d'emploi tient en une phrase : vide, c'est l'ERP ; un chiffre, c'est votre plan ; le stock se
recalcule.** L'approvisionneur ne saisit que deux lignes du tableau, *Plan* et *Ajustement*. Tout le reste
se lit.

## 1. Temps et calendrier

* **Date de référence (`as_of`)** : « aujourd'hui » du calcul. Par défaut la date du jour (production) ou le
  lendemain du dernier stock de référence (mode local). Le stock est connu **en fin de journée** de la veille ;
  la projection commence le jour de référence.
* **Fenêtre glissante** : `history_days` [14] jours affichés avant la référence, `horizon_days` [120] après.
* **Calendrier ouvré** : `working_weekdays` [1-5] + fermetures (`APPRO_HOLIDAYS`). Les délais fournisseurs
  sont en **jours ouvrés** ; les jours de livraison autorisés sont par fournisseur (`delivery_weekdays`).

## 2. Besoin (une seule ligne du tableau)

1. **Lissage du PDP hebdomadaire** sur les jours ouverts de la semaine ISO (`spread_rounding`) : `exact`
   [défaut] quantités entières dont la somme égale le PDP ; `none` quotient réel ; `per_day` `ROUND(qté / n)`
   (classeur historique).
2. **Production effective** (`production_mode`, `actual_then_remainder` [défaut]) : **jours passés** =
   production réelle déclarée (jour sans déclaration = 0, `missing_actual_policy` [`zero`]) ; **semaine en
   cours** = reliquat `max(PDP − réel déjà déclaré, 0)` réparti sur les jours ouvrés restants, jour de référence
   compris ; **semaines suivantes** = PDP. Exemple : PDP 2 000, mercredi, 500 lundi et 600 mardi → 300 par jour
   mercredi, jeudi, vendredi ; PDP déjà dépassé → 0. Autres modes : `actual_then_plan`, `plan_only`,
   `actual_only`.
3. **Éclatement nomenclature (1 niveau)** : besoin composant = Σ production × `qty_per` × (1 + `scrap_pct` %) ;
   `consumption_offset_days` [0] décale la consommation.
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

Il n'existe **aucun autre objet** : ni ligne de plan, ni statut de commande, ni action « dater / clôturer ».

### 3.4 Backlog fournisseur

Les commandes fermes **passées** ne comptent dans aucun stock (leur restant ERP n'est pas fiable). Par
fournisseur, sur les `backlog_days` [28] derniers jours avant la référence :

`backlog = max(0, Σ quantité commandée des fermes passées − Σ réceptions)`

Cumul, sans lettrage : une réception en avance ou en retard de quelques jours ne crée pas de fausse alerte.
Le backlog est affiché (KPI de la fiche, liste du cockpit, en-tête de la voie, alerte `BACKLOG`) et **sort
tout seul** : réception dans l'ERP, ou commande plus ancienne que la fenêtre. Si l'approvisionneur l'attend
encore, il tape la quantité dans le plan au jour prévu ; sinon il ne fait rien.

### 3.5 Ajustements et stock de référence

Seules les commandes et les réceptions sont lues de l'ERP : les ajustements sont des **saisies** (ligne
*Ajustement*, toute date, quantité signée ou expression). Daté du jour de référence ou avant, un ajustement
**corrige le stock de référence** (le stock ERP est souvent faux, l'application ne l'écrit jamais) et persiste
jusqu'à sa suppression ; rappelé dans le KPI *Stock de référence*. Daté après, c'est un mouvement prévu
(casse, transfert) compté à sa date. Les ajustements comptent dans les deux scenarios.

| Élément | Scenario ERP | Scenario Plan | Règle |
|---|---|---|---|
| Commande ERP ferme future | ✔ | ✔ sauf cellule saisie | restant ERP |
| Commande ERP ferme passée | – | – | backlog fournisseur |
| Commande ERP prévisionnelle | information | information | `forecast_date_policy` : date réelle [défaut] ou lundi |
| **Cellule du plan** | – | ✔ | remplace la valeur ERP du jour pour ce fournisseur |
| Proposition CBN | – | ✔ (option) | `include_proposals_in_plan` [oui] |
| Réception | ✔ | ✔ | fait ERP, à sa date |
| Ajustement | ✔ | ✔ | saisie signée, toute date |

**Affichage du passé** : commandes fermes passées (quantité commandée), réceptions, ajustements, besoin
consommé ; les cellules du plan passées ne s'affichent pas. Le stock passé est reconstitué (§ 4).

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
toléré, `shortfall_tolerance_days` [0]), quantité = max(besoin net jusqu'au niveau de recomplètement, MOQ)
arrondie au PLA ; fournisseur par quota [défaut] ou priorité ; livraison le premier jour ouvré autorisé du
fournisseur (`delivery_shift` [`earlier`]), ou le **lundi** (`proposal_placement = monday`) ; date de
commande = livraison − délai ouvré, **urgente** si déjà passée ; re-projection puis itération.

**Une proposition par fournisseur et par jour de livraison**, partout (tableau, listes, infobulles,
exports) : les besoins d'une même semaine ramenés au même lundi sont fusionnés.

Pour la reprendre : taper la quantité dans la cellule *Plan* (la cellule grisée la prérempli). Le CBN se
recalcule sans elle.

## 8. Paramètres d'article par semaine

Couverture cible, seuils, surstock, stock de sécurité et cycle de commande sont fixes par article dans le
référentiel ; le bouton *Semaines* ouvre un calendrier hebdomadaire où chaque valeur peut être personnalisée
pour une semaine ISO (réversible). Le moteur applique la valeur de la semaine du jour calculé.

## 9. Impact sur les programmes

Production **réalisable** par programme et semaine = PDP × part servable du composant le plus contraint,
pour trois stocks : à date, Scenario ERP, Scenario Plan (page *Impact programmes*).

## 10. Traçabilité et règles de date

Chaque écriture (cellule, référentiel, import, paramètre) est journalisée avec l'utilisateur
(`x-forwarded-email` sur Databricks Apps), l'action et le contenu.

| Objet | Date admise |
|---|---|
| Cellule du plan | à partir de la date de référence (le passé n'est pas planifié) |
| Ajustement | toute date ; ≤ référence = correction du stock de référence |
| Référentiel | sans date, sauf le stock de référence (date du stock) |

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

## 12. Données de démonstration

`data/seed` (16 articles, 11 fournisseurs, 20 liens, 25 programmes, 28 lignes de nomenclature, PDP et
production réelle, 772 créneaux de commande, 477 réceptions, 16 stocks de référence au 18/09/2026), extrait
du classeur historique par `scripts/extract_seed_from_excel.py`. Le référentiel du seed est chargé dans la
base locale au premier démarrage (`APPRO_SEED_REFERENCE`, oui en local seulement). Délais, conditionnements
et quotas n'existent pas dans le classeur : valeurs indicatives, à remplacer dans le référentiel.
