# Dictionnaire de données — Ma Routine Appro

Toutes les données lues et écrites par l'application. Les noms sont ceux du schéma canonique
(`backend/appro/data/schemas.py`), identiques dans le seed CSV, les modèles Excel, la base applicative et
le SQL de correspondance ERP. Dates au format jour, quantités en nombre décimal dans l'unité de l'article,
identifiants en texte. Les colonnes **clé** identifient une ligne.

Deux familles :

| Famille | Tables | Source | Où elles vivent |
|---|---|---|---|
| **Référentiel** | `ref_articles`, `ref_suppliers`, `ref_article_suppliers`, `ref_consumption_terms`, `ref_closed_orders`, `ref_programs`, `ref_bom`, `fct_stock`, `ref_planners`, `ref_delegations` | **gérées dans l'application** (page Référentiel : ligne par ligne, modèle Excel par table, modification en lot ; fiche article › Paramétrage) | base applicative (Lakebase ; SQLite en local) |
| **Faits ERP** | `fct_purchase_orders`, `fct_receipts`, `fct_consumption_actual` (facultatif), `fct_desadv`, `fct_prices`, `fct_stock_snapshot` (facultatif) | extractions ERP (`commandes_edi`, `recep_edi`…) | miroir `erp_*` de la base applicative, alimenté par le job (`APPRO_DATA_SOURCE=lakebase`), ou lecture directe par SQL warehouse (`uc`) |

## 1. Référentiel (géré dans l'application)

Le modèle Excel de chaque table (`GET /api/reference/<table>/template.xlsx`, bouton *Modèle*) porte les
libellés ci-dessous en en-tête, une ligne d'exemple et un onglet NOTICE. L'import accepte les libellés ou les
noms techniques, en mode *remplacer* ou *fusionner* (par clé).

La page Référentiel ajoute à trois tables des **colonnes requête**, calculées à la lecture à partir des autres
tables (`GET /api/reference/<table>/rows`) ; elles ne sont ni stockées, ni exportées, ni importées :

| Table | Colonnes requête | Boutons de filtre rapide (cumulables) |
|---|---|---|
| `ref_articles` | *Programmes* (noms des programmes dont la nomenclature utilise l'article, `programs`, `program_count`) ; *Fourn.* (`supplier_count`, règles article ↔ fournisseur actives). La colonne *Famille* n'est pas affichée. | *Sans fournisseur*, *Multi-sourcing* (≥ 2 fournisseurs actifs), *Sans programme*, *Conso dérivée / PDP*, *Jours conso ≠ 5* |
| `ref_suppliers` | *Articles* (nombre et références des articles reliés par une règle active, `articles`, `article_count`) | *Sans article*, *Délai ≠ 5* ; modification en lot : délai, jours de livraison, jours stricts, actif |
| `ref_article_suppliers` | — | *Plafonnés*, *Délai spécifique* |
| `ref_programs` | *Articles* (nombre de composants de la nomenclature, `article_count`) ; `has_plan` (une quantité planifiée à partir de la semaine en cours dans le PDP effectif) | *Sans nomenclature*, *Sans plan* |

### 1.1 `ref_articles` — Articles

| Colonne | Libellé | Type | Obligatoire | Description |
|---|---|---|---|---|
| article_id | Article | texte **clé** | oui | référence (`P-00…`), identique à `Article` de `commandes_edi` |
| designation | Désignation | texte | oui | |
| unit | Unité | texte | oui | PCE, KG, M |
| family | Famille | texte | non | |
| planner | Approvisionneur | texte | oui | code approvisionneur = **périmètre** de l'application |
| coverage_target_days | Couverture cible (j ouvrés) | entier | oui | jours **ouvrés** de besoin à couvrir (défaut 5) |
| alert_red_days | Seuil rouge (j ouvrés) | entier | oui | couverture à date (stock de la veille seul) ≤ n + 1 jours ouvrés : critique (défaut 2) |
| alert_yellow_days | Seuil orange (j ouvrés) | entier | oui | couverture à date ≤ n jours ouvrés sans livraison avant l'épuisement : à commander (défaut 5) |
| overstock_days | Surstock (j ouvrés) | entier | oui | stock à date couvrant ≥ n jours ouvrés : surstock (défaut 30) |
| demand_days | Jours de consommation | entier | non | 1 à 5 : jours ouvrés de la semaine portant le besoin hebdomadaire (vide ou 0 = 5, toute la semaine) |
| sourcing_policy | Multi-sourcing | texte | non | `quota` (défaut : chaque besoin partagé selon les quotas) ou `priority` |
| consumption_source | Source conso | texte | non | `erp` (défaut : consommation déclarée), `plan` (PDP éclaté) ou `derived` (dérivée, table `ref_consumption_terms`) |
| active | Actif | oui / non | oui | |

Couverture cible, seuils et surstock peuvent être personnalisés par semaine ISO (fiche article › Paramétrage ›
*Par semaine*, ou Référentiel), sans modifier la table. Le stock de sécurité et le cycle de commande n'existent
plus ; les anciennes colonnes sont ignorées à l'import.

### 1.2 `ref_suppliers` — Fournisseurs

| Colonne | Libellé | Type | Obligatoire | Description |
|---|---|---|---|---|
| supplier_id | Fournisseur | texte **clé** | oui | code (`S-000…`), identique à `Code_fournisseur` |
| name | Nom | texte | oui | |
| country | Pays | texte | non | |
| contact | Contact | texte | non | |
| transport_contact | Contact transport | texte | non | adresses e-mail des avis de transport, séparées par `;` ou `,` ; destinataires des e-mails du Planning de livraisons ; une personne connectée avec l'une de ces adresses n'accède qu'à l'**espace fournisseur** |
| docks | Quais | texte | non (`B15`) | quais de livraison par défaut, séparés par `;` (`B15`, `B6 EST`, `B2 NORD`, `B6 NORD`), présélectionnés à la création d'un transport ; cases à cocher à un clic dans le Référentiel ; les lignes vides reçoivent `B15` au démarrage |
| delivery_weekdays | Jours de livraison | texte | oui | jours de livraison **standard**, ISO, `1,2,3,4,5` (1 = lundi) ; saisie en colonne semaine posée sur le premier de ces jours |
| strict_days | Jours stricts | oui / non | non (non) | oui : livraisons uniquement les jours standard, jamais de proposition hors jour standard (◇) ; non : le CBN peut poser une proposition ◇ sur un autre jour ouvré quand le jour standard n'est plus tenable |
| lead_time_days | Délai (j ouvrés) | entier | oui | délai du fournisseur en jours **ouvrés**, inclusif (lundi + 5 = lundi suivant) ; initialisé à 5 pour tous |
| active | Actif | oui / non | oui | |

### 1.3 `ref_article_suppliers` — Article ↔ fournisseur

| Colonne | Libellé | Type | Obligatoire | Description |
|---|---|---|---|---|
| article_id | Article | texte **clé** | oui | |
| supplier_id | Fournisseur | texte **clé** | oui | |
| moq | MOQ | nombre | oui | quantité minimale de commande |
| pack_qty | UM | nombre | oui | unité de manutention (conditionnement) : arrondi au multiple supérieur |
| lead_time_override | Délai spécifique (j ouvrés) | entier | non | exception au délai du fournisseur ; **vide** = délai du fournisseur (grisé « hérité » dans les écrans ; jamais 0) |
| quota_pct | Quota % | nombre | oui | répartition multi-sourcing (100 si mono-source) |
| priority | Priorité | entier | oui | 1 = principal |
| max_qty_per_delivery | Max / livraison | nombre | non | plafond d'une livraison : multiple de l'UM, ≥ MOQ ; vide = aucun |
| max_qty_per_week | Max / semaine | nombre | non | plafond d'une semaine ISO, commandes fermes et Appro. compris : multiple de l'UM, ≤ max / livraison × jours de livraison ; vide = aucun |
| active | Actif | oui / non | oui | |

Un article avec plusieurs liens actifs a **une voie par fournisseur** dans le tableau (Ferme, Prévisionnel,
Reçu, Plan) ; les stocks sont sommés. Les plafonds et le délai spécifique sont validés à la saisie et à l'import
(HTTP 422 sinon). L'ancienne colonne *Délai* du lien est ignorée à l'import : le délai est celui du fournisseur.

### 1.3 bis `ref_consumption_terms` — Termes de consommation

| Colonne | Libellé | Type | Obligatoire | Description |
|---|---|---|---|---|
| article_id | Article | texte **clé** | oui | article en source de consommation `derived` |
| source_article_id | Article source | texte **clé** | oui | article dont la consommation déclarée sert de base ; éligible s'il est en source `erp`, présent dans un programme de l'article et dans aucun programme où l'article est absent (`GET /api/reference/consumption-sources?article_id=`) |
| coef | Coefficient | nombre | oui | > 0 : quantité d'article consommée par unité d'article source |

Consommation dérivée = Σ coef × consommation déclarée des sources ; sans terme, la consommation MOM (déclarée) de
l'article est utilisée. Les boucles sont refusées.

### 1.3 ter `ref_closed_orders` — Commandes fermées (PO)

| Colonne | Libellé | Type | Obligatoire | Description |
|---|---|---|---|---|
| purch_id | N° de PO | texte **clé** | oui | numéro de la commande fermée ponctuelle : **six chiffres** (`024596`) ; une commande ouverte (`CO-…`) est refusée, elle compte déjà |
| supplier_id | Fournisseur | texte | non | information |
| article_id | Article | texte | non | information (article principal ; vide = tous) |
| note | Commentaire | texte | non | |
| active | Actif | booléen | oui | inactif : la PO ne compte plus |

Règle : une réception ERP n'entre dans la ligne *Reçu* et le stock que si sa commande (`fct_receipts.purch_id`)
est une commande ouverte (`CO-…`) ou une PO déclarée ici et active. Toute autre réception (PO non déclarée,
demande d'achat, régularisation) reste visible dans *Recherche*, les flux et la fiche fournisseur. Colonnes
calculées dans le Référentiel : réceptions ERP portant le numéro (nombre, quantité, dernière date, articles,
fournisseurs). Depuis *Recherche*, le bouton « Déclarer la PO » d'une réception non comptée crée la ligne.

### 1.4 `ref_programs` — Programmes

| Colonne | Libellé | Type | Obligatoire | Description |
|---|---|---|---|---|
| program_id | Programme | texte **clé** | oui | `mass-000…` |
| name | Nom | texte | oui | nom utilisé dans le fichier PDP |
| family | Famille | texte | non | |
| active | Actif | oui / non | oui | |

### 1.5 `ref_bom` — Nomenclatures (1 niveau)

| Colonne | Libellé | Type | Obligatoire | Description |
|---|---|---|---|---|
| program_id | Programme | texte **clé** | oui | |
| article_id | Composant | texte **clé** | oui | |
| qty_per | Qté / unité | nombre | oui | composant par unité produite |
| unit | Unité | texte | oui | |
| scrap_pct | Rebut % | nombre | non | majore le besoin |
| valid_from / valid_to | Valide du / au | date | non | |

### 1.6 `fct_stock` — Stock de référence

| Colonne | Libellé | Type | Obligatoire | Description |
|---|---|---|---|---|
| article_id | Article | texte **clé** | oui | |
| snapshot_date | Date du stock | date **clé** | oui | stock connu **en fin de journée** (veille au soir de la date de référence) |
| qty_on_hand | Stock physique | nombre | oui | |
| qty_blocked | Stock bloqué | nombre | non | déduit |
| unit | Unité | texte | non | |

Seule la ligne la plus récente de chaque article est utilisée. Le stock ERP étant souvent faux, les
**ajustements** saisis dans le tableau (ligne *Ajustement*, toute date) corrigent le stock de référence
sans toucher à cette table.

`snapshot_date` est la **date d'initialisation du stock** : une seule date pour tous les articles, jamais postérieure à aujourd'hui (refusée sinon). C'est le point zéro de l'application : rien n'est calculé avant.

### 1.7 `ref_planners` — Approvisionneurs

| Colonne | Type | Description |
|---|---|---|
| `planner_id` | str, clé | identifiant (PROC1, PROC2…) |
| `name` | str | prénom / code tel qu'il figure dans la colonne *Approvisionneur* des articles (= son carnet) |
| `email` | str | identifiant de connexion Databricks (`x-forwarded-email`), comparé sans la casse |
| `role` | str | `appro`, `manager` ou `admin` (règles métier § 12) |
| `active` | bool | inactif = lecture seule |

Table vide = tout utilisateur est administrateur (première installation).

### 1.8 `ref_delegations` — Délégations

| Colonne | Type | Description |
|---|---|---|
| `from_planner` | str, clé | approvisionneur qui délègue son carnet (`planner_id`) |
| `to_planner` | str, clé | approvisionneur qui reçoit l'accès |
| `date_from` | date, clé | premier jour |
| `date_to` | date | dernier jour (vide = sans fin) |
| `note` | str | motif |
| `active` | bool | |

## 2. Faits ERP

Lecture de bout en bout (colonnes UC lues, miroir Lakebase, usage écran par écran) : `flux_donnees_erp.md`.

Correspondance appliquée par `backend/appro/data/erp_sql.py` (Databricks SQL), exécutée soit par le job
`appro_sync_erp` (vers `erp_*`), soit par l'application sur un SQL warehouse. Une seule source de vérité
pour la correspondance.

### 2.1 `fct_purchase_orders` ← `silver_erp_ye.commandes_edi`

Une ligne par **créneau de livraison** `ID` = `vendaccount|itemid|yyyyMMdd|silfirmorder` (plusieurs numéros
de commande d'achat peuvent partager un créneau : sommés).

| Colonne | Type | `commandes_edi` | Règle |
|---|---|---|---|
| order_id | texte **clé** | `ID` | |
| article_id | texte | `Article` | |
| supplier_id | texte | `Code_fournisseur` | |
| supplier_name | texte | `Nom_fournisseur` | |
| order_type | texte | `Ordre_ferme` | `Oui` → `FIRM`, sinon `FORECAST` (seul critère de classement ; `Niveau_engagement` reste informatif) |
| expected_date | date | `Date_de_debut` | |
| qty_ordered | nombre | Σ `Quantite` | |
| qty_open | nombre | Σ `Quantite_restante` (≥ 0) | restant ERP ; non fiable une fois la date passée |
| purch_id | texte | `Commande` (liste) | information |
| commitment | texte | `Niveau_engagement` | information |
| first_seen | date | — (posée par le job) | jour où le créneau est apparu dans la copie Lakebase : conservé d'une exécution à l'autre, un créneau inconnu reçoit la date du jour ; alimente le flux **Commandé** du cockpit ; vide tant que le job n'a pas tourné |

Exploitation : les commandes fermes sont conservées depuis le début de l'année, les prévisionnelles à
partir du lundi suivant (règles du job amont). Une commande ferme **passée** ne compte dans aucun stock :
elle entre dans le **backlog** du fournisseur (§ 3.3 des règles métier).

### 2.2 `fct_receipts` ← `silver_erp_ye.recep_edi`

Une ligne par **journal d'arrivée** (fournisseur / commande / BL / article / jour / statut).

| Colonne | Type | `recep_edi` | Règle |
|---|---|---|---|
| receipt_id | texte **clé** | `Code_fournisseur|Commande|BL|Article|yyyyMMdd|Statut_reception` | construit (BL haché au-delà de 60 caractères) |
| article_id | texte | `Article` | |
| supplier_id | texte | `Code_fournisseur` | affecte la ligne à la voie du fournisseur |
| receipt_date | date | `Date_reception` | date de validation du journal (`posted = 1`) ou date de la ligne du journal (`posted = 0`) |
| qty | nombre | `Quantite_recue` | lignes à quantité nulle ignorées |
| purch_id | texte | `Commande` | information |
| packing_slip | texte | `BL` | numéro(s) de BL (plusieurs séparés par « \| ») : clé de rapprochement avec les DESADV ; vide = réception manuelle |
| status | texte | `Statut_reception` | **`En transit`** (journal créé, non validé : affiché avec une icône camion, jamais compté), **`Reçu`** (journal validé, accusé de réception non validé : compté, flux *À valider* après 7 jours), **`Comptabilisé`** (accusé de réception validé : compté, fond vert) |

Contrat attendu de `recep_edi` (job amont) : `Statut_reception` dérivé de `wmsjournaltable.posted` et de la
présence de l'accusé de réception (ACR) dans `vend_packing_slip_trans`, rapproché **par `inventTransId`** (la
transaction de stock du journal, et non plus par numéro de BL : un BL partagé par plusieurs lignes ne fausse
plus le statut) ; les journaux non validés **sont conservés** avec le statut `En transit`. Historique souhaité :
tout ce qui suit la date d'initialisation du stock.

### 2.3 `fct_consumption_actual` ← table UC de consommation réelle par composant (facultatif)

Table **à préparer dans Unity Catalog** (`APPRO_ERP_CONSUMPTION_TABLE`, variable `erp_consumption_table` du bundle,
ex. `emotors_data_champions.silver_erp_ye.conso_composants`). Contrairement au PDP, que le moteur éclate par la
nomenclature, la consommation réelle arrive **déjà éclatée** : une quantité de composant consommée, par jour.
Vide (aucune table configurée) : le besoin des jours passés suit `missing_actual_policy` (0 ou PDP) et le reliquat
de la semaine en cours est le PDP entier.

**Contrat de la table source** (une seule exigence de nom : les trois colonnes canoniques ; le reste est libre) :

| Colonne | Type UC | Obligatoire | Règle |
|---|---|---|---|
| `article_id` | STRING | oui | référence du **composant** telle qu'elle figure dans `ref_articles` (ex. `P-00001046`) ; les lignes d'articles inconnus de l'application sont ignorées |
| `date` | DATE (ou TIMESTAMP, tronqué au jour) | oui | **jour de consommation** (date du mouvement de sortie de stock / de l'ordre de fabrication), pas la date de déclaration |
| `qty` | DOUBLE | oui | quantité consommée dans l'**unité de stock de l'article** (PCE, KG, M…), positive ; plusieurs lignes d'un même jour (plusieurs programmes, plusieurs mouvements) sont **sommées** par le job ; un **0 déclaré** est respecté (jour sans consommation) ; un retour en stock s'exprime en négatif et se soustrait |

Grain attendu : une ligne par composant × jour × (programme, mouvement…) au choix ; le job agrège en
`(article_id, date)`. Colonnes supplémentaires (programme, ordre de fabrication, atelier) tolérées et ignorées.

Règles d'alimentation :

* couvrir **tous les jours depuis la date d'initialisation du stock** (point zéro) jusqu'à hier ; un jour
  absent est traité selon `missing_actual_policy` [`zero`] : mieux vaut déclarer un 0 explicite qu'omettre
  le jour ;
* ne jamais déclarer le jour courant ni le futur (ignorés par le mode par défaut, et sources de confusion) ;
* la rupture avec l'ancien schéma : plus de `program_id` ni de production en unités de produit fini ;
  l'éclatement (quantité par unité, rebut) est fait **en amont**, avec la nomenclature réellement
  consommée (ordre de fabrication), ce qui absorbe les écarts de rebut et de substitution.

SQL exécuté par le job (identique en lecture directe par SQL warehouse) :

```sql
SELECT c.article_id, CAST(c.date AS DATE) AS date, CAST(SUM(c.qty) AS DOUBLE) AS qty
FROM <catalogue>.<schéma>.<table> c
WHERE c.article_id IS NOT NULL AND c.date IS NOT NULL AND c.qty IS NOT NULL
GROUP BY c.article_id, CAST(c.date AS DATE)
```

Contrôle de cohérence avant mise en service (à exécuter dans l'éditeur SQL) : la somme hebdomadaire par
composant doit être de l'ordre du PDP éclaté de la semaine :

```sql
SELECT article_id, DATE_TRUNC('week', date) AS semaine, SUM(qty) AS conso
FROM <catalogue>.<schéma>.<table>
WHERE date >= DATE'2026-09-18'          -- date d'initialisation du stock
GROUP BY ALL ORDER BY 1, 2
```

Colonnes canoniques côté application (miroir `erp_consumption_actual`) :

| Colonne | Type | Description |
|---|---|---|
| article_id | texte **clé** | composant |
| date | date **clé** | jour de consommation |
| qty | nombre | quantité consommée (unité de stock) |

### 2.4 `fct_desadv` ← `silver_erp_ye.desadv_edi` — avis d'expédition EDI (DESADV)

Table préparée en amont depuis `bronze_erp.siledimessage` (messages `DespatchAdvice-Purchase`),
`siledi_item_line` et `purch_table` (`APPRO_ERP_DESADV_TABLE`, variable `erp_desadv_table`, défaut `desadv_edi`).
Une ligne par ligne d'article d'un message.

| Colonne | Type | Source | Règle |
|---|---|---|---|
| desadv_id | texte **clé** | `Document_ID` \| `ID_Ligne` | |
| article_id | texte | `Code_article` | |
| supplier_id | texte | `Code_fournisseur` | voie du tableau |
| supplier_name | texte | `Nom_fournisseur` | |
| packing_slip | texte | `BL` | **clé de rapprochement** avec `fct_receipts.packing_slip` (`recep_edi.BL`, un ou plusieurs BL) ; vide (`BL` nul) : le DESADV est affiché mais ne peut être rapproché |
| purch_id | texte | `Commande_ouverte` | information |
| issue_date | date | `Date_emission` | jour affiché dans la ligne *Reçu* |
| qty | nombre | `Quantite_achat` | quantité annoncée, **jamais comptée** dans un stock |
| state | texte | `Etat_message` | Créé, Traité, Erreur, En attente, Annulé (un message annulé est ignoré) |
| final_processing | texte | `Traitement_final` | Non traité, OK |
| stock_trans_id | texte | `ID_transaction_stock` | vide sur un message *Traité* = aucun **journal de saisie** créé : listé dans le flux **À traiter** du cockpit |

Un DESADV est **non reçu** tant que son BL n'apparaît pas dans `recep_edi` ; il est alors affiché, en italique
sur fond orange, dans la ligne *Reçu* du fournisseur au jour d'émission, avec un point vert s'il est *Traité* et
*OK*, rouge sinon. Il disparaît dès que le BL est réceptionné, ou si l'approvisionneur le masque (double-clic
confirmé, drapeau `desadv_hidden`). Les réceptions portent un point vert si leur BL correspond à un DESADV
traité, rouge sinon.

### 2.5 PDP hebdomadaire

Pas de table ERP : le PDP est une **version de l'application** (`app_pdp_versions`, `app_pdp_lines`), importée
(classeur au format du modèle / de l'export) ou saisie dans la feuille PDP. Le jeu de démonstration charge
`data/seed/pdp_seed.csv` comme première version active.

### 2.6 `fct_prices` ← `silver_erp_ye.silver_base_article` — prix des articles

Prix de revient standard, en euros, par article (`APPRO_ERP_PRICES_TABLE`, variable `erp_prices_table`,
défaut `silver_base_article`). La table source contient bien plus d'articles que le référentiel : le job copie
les `item_id` en `P-00…`, et **seuls les articles du référentiel sont valorisés** ; un article présent dans
les prix et absent du référentiel n'y est **jamais ajouté**.

| Colonne | Type | Source | Règle |
|---|---|---|---|
| article_id | texte **clé** | `item_id` | |
| price | nombre | `std_cost_price` | euros par unité de stock ; `MAX` si plusieurs lignes |

Usage : **valeur du stock** = Σ prix × stock à date (stock de la veille), **valeur cible** = Σ prix × stock
cible du jour (KPI *Valeur du stock*, sous-ligne +x % / −x % de la cible, rouge au-dessus) ; **projection de la
valeur du stock** (Projeté Appro., fin de chaque semaine ISO) dans le cockpit. Un article sans prix n'entre dans
aucune valeur (compté « sans prix » sur la carte).

### 2.7 `fct_stock_snapshot` ← `silver_erp_ye.stock_snapshot` — stock ERP de la veille

Snapshot quotidien du stock physique par article et entrepôt (`APPRO_ERP_STOCK_TABLE`, variable
`erp_stock_table`, défaut `stock_snapshot` ; vide = aucune comparaison). Le job ne copie que les entrepôts
configurés (`APPRO_STOCK_WAREHOUSES`, défaut `B06,B06VRAC`) et les 30 derniers jours.

| Colonne | Type | Source | Règle |
|---|---|---|---|
| article_id | texte **clé** | `item_id` | |
| snapshot_date | date **clé** | `snapshot_date` | stock connu en fin de journée |
| warehouse | texte **clé** | `entrepot` | entrepôts configurés seulement |
| qty | nombre | `SUM(stock_physique)` | unité de stock de l'article |

Usage (`docs/regles_metier.md` § 4 bis) : comparé au **stock projeté du même jour** (écart ERP − projeté, en %
du stock cible), **dérive** quand les derniers snapshots dépassent le seuil avec le même signe (règles communes
`stock_drift_pct`, `stock_drift_min_packs`, `stock_drift_persist`), **calage** = ajustement signé daté du
snapshot. Jamais un stock de départ : le point zéro reste `fct_stock`.

### 2.8 Accusés de réception non validés

La table `bl_en_attente` n'est plus lue : le flux **À valider** du cockpit et de la fiche fournisseur est calculé
depuis les réceptions elles-mêmes (statut *Reçu* depuis 7 jours ou plus), la page *Recherche* ne propose plus
de type « BL en attente ». Le job, la configuration (`APPRO_ERP_BL_PENDING_TABLE`) et le miroir `erp_bl_pending`
sont retirés ; une table `erp_bl_pending` résiduelle peut être supprimée.

## 3. Base applicative (écrite par l'application)

Tables créées automatiquement au premier démarrage (`create_all`) dans Lakebase, dans le schéma `appro`
que l'application crée et possède (`APPRO_DB_SCHEMA` ; son rôle n'a pas `CREATE` sur `public`) ; SQLite
en local. Chaque écriture est journalisée avec l'utilisateur.

| Table | Contenu | Clé |
|---|---|---|
| `ref_articles`, `ref_suppliers`, `ref_article_suppliers`, `ref_consumption_terms`, `ref_closed_orders`, `ref_programs`, `ref_bom`, `fct_stock`, `ref_planners`, `ref_delegations` | le référentiel (§ 1) + `updated_by`, `updated_at` ; les colonnes ajoutées sont créées au démarrage (`ensure_columns`), les anciennes (stock de sécurité, cycle de commande, délai du lien) ignorées puis supprimables ; un 0 hérité dans un délai spécifique ou un plafond est remis à vide au démarrage | clé de la table |
| `app_meta` | clé / valeur partagées par tous les processus de l'App : `data_version`, incrémentée à chaque écriture pour invalider les caches mémoire de chaque worker | key |
| `app_plan_cells` | **cellules du plan** : article, fournisseur (`""` si aucun), date, expression saisie, quantité, commentaire, auteur | (article, fournisseur, date) |
| `app_adjustments` | **cellules d'ajustement** : article, date (toute date), expression, quantité signée, commentaire, auteur | (article, date) |
| `app_cell_flags` | **clics sur les cellules en lecture** : `kind` = `order_ignored` (commande ferme ignorée : article, fournisseur, jour), `proposal_refused` (proposition CBN refusée : article, fournisseur, jour, quantité refusée affichée ; bloque les propositions à ce fournisseur **ce jour-là seulement**) ou `desadv_hidden` (DESADV non reçu masqué : article, fournisseur, jour, BL en note), auteur | (kind, article, fournisseur, date) |
| `app_pdp_versions`, `app_pdp_lines` | versions de PDP importées (classeur) ou saisies dans la page *Plan de production* (`source_file` = « saisie directe ») ; une active au plus | id |
| `app_program_scrap` | **taux de rebut par programme**, daté : `rate_pct` s'applique de `week_start` (lundi) jusqu'à la ligne suivante ; besoin = PDP × (1 + taux / 100) ; auteur | (program_id, week_start) |
| `app_help_pages` | **pense-bête intégré** : texte enregistré par un administrateur pour une page (`slug`, titre, `body_html` nettoyé, auteur, horodatage) ; absent = texte livré (`backend/appro/help/pense_bete.json`) ; `DELETE` revient au texte livré | slug |
| `app_param_overrides` | règles communes du moteur (`global`, champs `target_tolerance_pct`, `lead_margin_days`, `stock_drift_pct`, `stock_drift_min_packs`, `stock_drift_persist`) et politique de stock d'article par semaine ISO (`article_week` : couverture cible, seuils, surstock) ; les surcharges des règles supprimées sont purgées au démarrage | (scope, key1, key2, field) |
| `app_audit_log` | journal : horodatage, utilisateur (`x-forwarded-email`), action, objet, article, détail JSON | id |
| `app_transports` | **transports** du Planning de livraisons : identifiant `EM` + 6 chiffres, fournisseur, date, heure, quais (`;`), transporteur, commentaire, statut `planned` / `cancelled`, auteur, horodatage | transport_id |
| `app_transport_orders` | commandes fermes portées par un transport (`order_id` du créneau ERP, article, numéros de commande, date prévue, quantité restante au moment de la création) | id |
| `app_counters` | compteurs incrémentaux nommés (`transport` → dernier numéro EM attribué), lus avec `FOR UPDATE` | name |
| `app_actions` | annuler / rétablir : un geste utilisateur (horodatage, utilisateur, type, libellé, article, groupe), les lignes touchées avec leur état avant / après (JSON), annulable ou non, état `applied` / `undone` / `discarded`, qui l'a annulée ou rétablie et quand ; purge après 7 jours | id |
| `erp_purchase_orders`, `erp_receipts`, `erp_consumption_actual`, `erp_desadv`, `erp_prices`, `erp_stock_snapshot` | **miroir des faits ERP** (§ 2), écrit par le job de synchronisation (rôle `APPRO_SYNC_ROLE`) | clé de la table |
| `erp_sync_log` | par table miroir : nombre de lignes, horodatage, source, identifiant d'exécution (`/api/health`, `GET /api/data/sync` : âge de chaque table, état *en retard* au-delà de deux heures, bandeau dans l'application) | table_name |

Volumétrie : quelques milliers de lignes par table ; le miroir des commandes suit la source (dizaines de
milliers de lignes au plus), remplacé à chaque exécution.

## 4. Lakebase ou Unity Catalog seul ?

* **Écrire** les saisies (une cellule par clic, concurrence, unicité, journal) exige une base
  transactionnelle : Lakebase. Un SQL warehouse n'est pas fait pour cela.
* **Lire** les faits ERP peut se faire depuis Lakebase (miroir, millisecondes, aucun droit Unity Catalog
  pour l'App) ou depuis un SQL warehouse (secondes, warehouse démarré, `USE CATALOG` à accorder au principal
  de service de l'App par un propriétaire du catalogue).

Retenu : **Lakebase pour tout**, avec un job de synchronisation qui tourne sous une identité ayant déjà accès
à l'ERP, comme sur Campagnes inventaire (miroir) et Backflush (publication dans Lakebase). La lecture directe
par warehouse reste disponible (`APPRO_DATA_SOURCE=uc`, guide § 9).
