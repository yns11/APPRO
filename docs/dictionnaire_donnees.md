# Dictionnaire de données APPRO

Ce document liste **toutes** les données lues et écrites par l'application, pour préparer les tables Unity
Catalog, les tables synchronisées Lakebase, les jobs d'alimentation et la base applicative. Les noms sont
ceux du schéma canonique (`backend/appro/data/schemas.py`), identiques dans le seed CSV, dans Unity
Catalog et dans Lakebase.

Conventions : dates au format `DATE` (jour), quantités en `DOUBLE` dans l'unité de l'article, identifiants
en `STRING`. Les colonnes marquées **clé** forment la clé logique (unicité attendue). Les colonnes absentes
d'une source sont ajoutées vides ; les types sont normalisés à la lecture.

## 1. Données lues (référentiel et faits ERP)

Alimentées par des jobs Databricks (Lakeflow / SQL) à partir des tables bronze / silver de l'ERP. L'application
ne les modifie jamais. Cadence : quotidienne au minimum, plusieurs fois par jour pour commandes, réceptions et
stock.

### 1.1 `ref_articles` – articles approvisionnés

| Colonne | Type | Obligatoire | Description | Source ERP suggérée |
|---|---|---|---|---|
| article_id | STRING **clé** | oui | référence article (`P-00…`) | `InventTable.ItemId` |
| designation | STRING | oui | désignation | `EcoResProduct` / `InventTable.NameAlias` |
| unit | STRING | oui | unité de stock (PCE, KG, M) | unité d'inventaire |
| family | STRING | non | famille / groupe | groupe d'articles |
| planner | STRING | oui | code approvisionneur = **périmètre** de l'application | groupe acheteur / planificateur |
| coverage_target_days | INT | oui | couverture cible (jours calendaires) | paramétrage appro (défaut 7) |
| alert_red_days | INT | oui | seuil rouge (jours) | défaut 3 |
| alert_yellow_days | INT | oui | seuil orange (jours) | défaut = couverture cible |
| overstock_days | INT | oui | seuil de surstock (jours) | défaut max(30, 3 × cible) |
| safety_stock_qty | DOUBLE | non | stock de sécurité fixe | `ReqItemTable.MinInventOnhand` |
| service_rate_tracked | BOOLEAN | non | article suivi en taux de service | |
| dhrq | STRING | non | information libre (DHRQ) | |
| active | BOOLEAN | oui | article actif dans l'application | statut article |

Les seuils peuvent être surchargés dans l'application (par article et par semaine ISO) sans modifier la table.

### 1.2 `ref_suppliers` – fournisseurs

| Colonne | Type | Obligatoire | Description |
|---|---|---|---|
| supplier_id | STRING **clé** | oui | code fournisseur (COFOR, `S-000…`) |
| name | STRING | oui | raison sociale |
| country | STRING | non | pays |
| contact | STRING | non | contact |
| delivery_weekdays | STRING | oui | jours de livraison autorisés, ISO, ex. `1,2,3,4,5` |
| calendar_id | STRING | non | calendrier (réservé, `DEFAULT`) |
| active | BOOLEAN | oui | |

### 1.3 `ref_article_suppliers` – règles d'approvisionnement article ↔ fournisseur

| Colonne | Type | Obligatoire | Description |
|---|---|---|---|
| article_id | STRING **clé** | oui | |
| supplier_id | STRING **clé** | oui | |
| moq | DOUBLE | oui | quantité minimale de commande |
| pack_qty | DOUBLE | oui | conditionnement (PLA) : les propositions sont arrondies au multiple supérieur |
| lead_time_days | INT | oui | délai fournisseur en **jours ouvrés** |
| quota_pct | DOUBLE | oui | quota de répartition (multi-sourcing), 100 si mono-source |
| priority | INT | oui | priorité (1 = principal) |
| active | BOOLEAN | oui | |

### 1.4 `ref_programs` – programmes de production (PF / SF)

| Colonne | Type | Obligatoire | Description |
|---|---|---|---|
| program_id | STRING **clé** | oui | identifiant du programme (`mass-000…`) |
| name | STRING | oui | nom tel qu'utilisé dans le fichier PDP (colonne A du modèle) |
| family | STRING | non | famille |
| has_bom | BOOLEAN | non | possède une nomenclature |
| active | BOOLEAN | oui | |

### 1.5 `ref_bom` – nomenclature (1 niveau)

| Colonne | Type | Obligatoire | Description |
|---|---|---|---|
| program_id | STRING **clé** | oui | |
| article_id | STRING **clé** | oui | composant approvisionné |
| qty_per | DOUBLE | oui | quantité de composant par unité produite |
| unit | STRING | oui | unité du composant |
| scrap_pct | DOUBLE | non | rebut en % (majore le besoin) |
| valid_from / valid_to | DATE | non | validité de la ligne |

### 1.6 `fct_production_plan` – PDP hebdomadaire (ERP)

Peut rester vide : le PDP est le plus souvent **importé depuis le fichier Excel** (modèle téléchargeable dans
l'application, page *Imports / exports*, généralement le jeudi ou le vendredi). Un PDP importé et actif
remplace cette table pour les programmes qu'il contient.

| Colonne | Type | Obligatoire | Description |
|---|---|---|---|
| program_id | STRING **clé** | oui | |
| week_start | DATE **clé** | oui | lundi de la semaine ISO |
| iso_week | STRING | non | libellé `2026-W40` |
| qty | DOUBLE | oui | quantité à produire dans la semaine |
| version | STRING **clé** | oui | version (la plus récente gagne) |
| published_at | DATE | non | |

### 1.7 `fct_production_actual` – production réelle journalière

Utilisée pour le **consommé** (passé) et pour le reliquat de la semaine en cours. Un jour déclaré à 0 est
respecté ; un jour passé sans déclaration vaut 0.

| Colonne | Type | Obligatoire | Description |
|---|---|---|---|
| program_id | STRING **clé** | oui | |
| date | DATE **clé** | oui | jour de production |
| qty | DOUBLE | oui | quantité produite |

### 1.8 `fct_purchase_orders` – commandes (créneaux de livraison)

Issue de l'extraction `commandes_edi` (voir la vue `v_fct_purchase_orders_from_commandes_edi` dans
`scripts/uc/create_tables.sql`). Une ligne = un **créneau** fournisseur | article | date | ferme.

| Colonne | Type | Obligatoire | Description | `commandes_edi` |
|---|---|---|---|---|
| order_id | STRING **clé** | oui | identifiant synthétique `vendaccount|itemid|yyyyMMdd|silfirmorder` | `ID` |
| line_no | INT **clé** | oui | toujours 1 | — |
| article_id | STRING | oui | | `Article` |
| supplier_id | STRING | oui | | `Code_fournisseur` |
| order_type | STRING | oui | `FIRM` (Ordre_ferme = Oui) ou `FORECAST` | `Ordre_ferme` |
| message_type | STRING | non | information : niveau d'engagement, numéros de commande | `Niveau_engagement`, `Commande` |
| order_date | DATE | non | non fourni | — |
| expected_date | DATE | oui | date de livraison | `Date_de_debut` |
| qty_ordered | DOUBLE | oui | quantité commandée | `Quantite` |
| qty_received | DOUBLE | oui | commandée − restante | `Quantite − Quantite_restante` |
| status | STRING | oui | `OPEN` (le restant fait foi ; restant 0 = reçue) | — |
| unit | STRING | non | | — |

Règles d'exploitation : les lignes fermes sont conservées depuis le début de l'année (commandes passées non
reçues = **backlog**, hors stocks, à qualifier) ; les prévisionnelles à partir du lundi suivant.

### 1.9 `fct_receipts` – réceptions

| Colonne | Type | Obligatoire | Description |
|---|---|---|---|
| receipt_id | STRING **clé** | oui | identifiant du mouvement de réception |
| order_id | STRING | non | référence de commande si connue (souvent absente) |
| article_id | STRING | oui | |
| supplier_id | STRING | non | fournisseur (améliore le lettrage du jour de référence) |
| receipt_date | DATE | oui | jour de réception |
| qty | DOUBLE | oui | quantité reçue |
| unit | STRING | non | |

Historique souhaité : au moins la fenêtre d'historique affichée (14 jours par défaut, `history_days`).

### 1.10 `fct_stock` – stock de référence

| Colonne | Type | Obligatoire | Description |
|---|---|---|---|
| article_id | STRING **clé** | oui | |
| snapshot_date | DATE **clé** | oui | jour du stock, connu **en fin de journée** (veille au soir de la date de référence) |
| qty_on_hand | DOUBLE | oui | stock physique |
| qty_blocked | DOUBLE | non | stock bloqué (déduit) |
| unit | STRING | non | |
| location | STRING **clé** | non | emplacement ; plusieurs emplacements sont sommés |

Seul le dernier snapshot par article est utilisé. Un historique journalier permettrait, plus tard, de comparer le
stock reconstitué au stock ERP.

### 1.11 `fct_stock_movements` – mouvements de stock ERP (facultatif)

Non alimentée dans le périmètre actuel (les ajustements sont des saisies de l'application). Si elle l'est, les
mouvements antérieurs au snapshot ne servent qu'à l'historique reconstitué.

| Colonne | Type | Description |
|---|---|---|
| movement_id | STRING **clé** | |
| article_id | STRING | |
| date | DATE | |
| movement_type | STRING | inventaire, casse, transfert… |
| qty | DOUBLE | signée |
| comment | STRING | |

## 2. Données écrites (base applicative)

Tables créées automatiquement par l'application (`Base.metadata.create_all`) dans **Lakebase** (PostgreSQL) ;
SQLite en local. Chaque écriture est journalisée.

| Table | Contenu | Clé |
|---|---|---|
| `app_plan_lines` | lignes du **plan de livraison** : article, `order_id` (créneau ERP surchargé, ou vide = ligne libre), fournisseur, date, quantité, origine (MANUAL / IMPORT / CBN), commentaire, état ERP vu à la saisie (`erp_json`), auteur, dates | id |
| `app_cells` | **ajustements** saisis dans la grille : article, date (toute date ; ≤ référence = correction du stock de référence), expression, quantité, origine, note | (article, date, kind) |
| `app_adjustments` | ajustements saisis par formulaire (même sémantique) | id |
| `app_orders` | commandes fermes passées hors ERP (comptées dans les deux scenarios) | id |
| `app_receipts` | réceptions saisies (fait constaté, ≤ date de référence) | id |
| `app_production_actual` | production réelle saisie par (programme, jour), prioritaire sur l'ERP | (programme, date) |
| `app_pdp_versions`, `app_pdp_lines` | versions de PDP importées (une active au plus) | id |
| `app_scenarios`, `app_scenario_events` | scénarios what-if | id |
| `app_param_overrides` | surcharges de paramètres : `global`, `article`, `article_week`, `link` | (scope, key1, key2, field) |
| `app_audit_log` | journal : horodatage, utilisateur (`x-forwarded-email`), action, objet, article, détail JSON | id |

Volumétrie : quelques milliers de lignes par table, croissance lente (journal : une ligne par écriture).

## 3. Lakebase ou Unity Catalog seul ?

Deux besoins distincts :

1. **Lire** le référentiel et les faits ERP : lecture de tables entières (quelques milliers de lignes) toutes
   les 5 minutes au plus (cache). Possible depuis un SQL warehouse (latence de 1 à 5 s par table, warehouse à
   maintenir démarré) ou depuis Lakebase (millisecondes) via des **tables synchronisées** depuis Unity Catalog.
2. **Écrire** les saisies des approvisionneurs : transactions courtes, concurrentes, à chaque clic, avec
   unicité et journal. Un SQL warehouse n'est pas fait pour cela (pas de transaction fine, latence, coût par
   écriture, pas de clé unique) ; Unity Catalog seul imposerait de réécrire des fichiers Delta à chaque saisie.

Recommandation : **Lakebase** pour la base applicative, et de préférence **aussi pour la lecture** via des
tables synchronisées (`APPRO_DATA_SOURCE=lakebase`) : une seule ressource attachée à l'app, aucune dépendance au
warehouse à l'exécution, un seul mécanisme d'authentification. La lecture directe Unity Catalog par SQL
warehouse (`APPRO_DATA_SOURCE=uc`) reste disponible si les tables synchronisées ne sont pas possibles. Une
variante sans Lakebase (saisies dans des tables Delta via le warehouse) n'est pas fournie : elle serait lente
et fragile pour un usage quotidien multi-utilisateurs. Les contraintes rencontrées sur les applications
Backflush et Campagnes inventaire n'étant pas accessibles depuis ce dépôt, elles sont à confronter à ce
choix (en particulier : disponibilité de Lakebase dans l'espace de travail, droits du principal de service).
