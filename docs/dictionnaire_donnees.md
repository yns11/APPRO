# Dictionnaire de données APPRO

Toutes les données lues et écrites par l'application. Les noms sont ceux du schéma canonique
(`backend/appro/data/schemas.py`), identiques dans le seed CSV, les modèles Excel, la base applicative et
le SQL de correspondance ERP. Dates au format jour, quantités en nombre décimal dans l'unité de l'article,
identifiants en texte. Les colonnes **clé** identifient une ligne.

Deux familles :

| Famille | Tables | Source | Où elles vivent |
|---|---|---|---|
| **Référentiel** | `ref_articles`, `ref_suppliers`, `ref_article_suppliers`, `ref_programs`, `ref_bom`, `fct_stock` | **gérées dans l'application** (page Référentiel : ligne par ligne, ou modèle Excel par table) | base applicative (Lakebase ; SQLite en local) |
| **Faits ERP** | `fct_purchase_orders`, `fct_receipts`, `fct_production_actual` (facultatif), `fct_production_plan` (facultatif) | extractions ERP (`commandes_edi`, `recep_edi`…) | miroir `erp_*` de la base applicative, alimenté par le job (`APPRO_DATA_SOURCE=lakebase`), ou lecture directe par SQL warehouse (`uc`) |

## 1. Référentiel (géré dans l'application)

Le modèle Excel de chaque table (`GET /api/reference/<table>/template.xlsx`, bouton *Modèle*) porte les
libellés ci-dessous en en-tête, une ligne d'exemple et un onglet NOTICE. L'import accepte les libellés ou les
noms techniques, en mode *remplacer* ou *fusionner* (par clé).

### 1.1 `ref_articles` — Articles

| Colonne | Libellé | Type | Obligatoire | Description |
|---|---|---|---|---|
| article_id | Article | texte **clé** | oui | référence (`P-00…`), identique à `Article` de `commandes_edi` |
| designation | Désignation | texte | oui | |
| unit | Unité | texte | oui | PCE, KG, M |
| family | Famille | texte | non | |
| planner | Approvisionneur | texte | oui | code approvisionneur = **périmètre** de l'application |
| coverage_target_days | Couverture cible (j) | entier | oui | jours calendaires de besoin à couvrir |
| alert_red_days | Seuil rouge (j) | entier | oui | couverture ≤ seuil : critique |
| alert_yellow_days | Seuil orange (j) | entier | oui | couverture ≤ seuil : à surveiller |
| overstock_days | Surstock (j) | entier | oui | couverture ≥ seuil : surstock |
| safety_stock_qty | Stock de sécurité | nombre | non | quantité fixe |
| order_cycle_days | Cycle de commande (j) | entier | non | besoin ajouté au niveau de recomplètement (défaut 7) |
| active | Actif | oui / non | oui | |

Les paramètres de politique de stock peuvent être personnalisés par semaine ISO (bouton *Semaines*),
sans modifier la table.

### 1.2 `ref_suppliers` — Fournisseurs

| Colonne | Libellé | Type | Obligatoire | Description |
|---|---|---|---|---|
| supplier_id | Fournisseur | texte **clé** | oui | code (`S-000…`), identique à `Code_fournisseur` |
| name | Nom | texte | oui | |
| country | Pays | texte | non | |
| contact | Contact | texte | non | |
| delivery_weekdays | Jours de livraison | texte | oui | jours ISO autorisés, `1,2,3,4,5` (1 = lundi) |
| active | Actif | oui / non | oui | |

### 1.3 `ref_article_suppliers` — Article ↔ fournisseur

| Colonne | Libellé | Type | Obligatoire | Description |
|---|---|---|---|---|
| article_id | Article | texte **clé** | oui | |
| supplier_id | Fournisseur | texte **clé** | oui | |
| moq | MOQ | nombre | oui | quantité minimale de commande |
| pack_qty | PLA | nombre | oui | conditionnement : arrondi au multiple supérieur |
| lead_time_days | Délai (j ouvrés) | entier | oui | |
| quota_pct | Quota % | nombre | oui | répartition multi-sourcing (100 si mono-source) |
| priority | Priorité | entier | oui | 1 = principal |
| active | Actif | oui / non | oui | |

Un article avec plusieurs liens actifs a **une voie par fournisseur** dans le tableau (Ferme, Prévisionnel,
Reçu, Plan) ; les stocks sont sommés.

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

Exploitation : les commandes fermes sont conservées depuis le début de l'année, les prévisionnelles à
partir du lundi suivant (règles du job amont). Une commande ferme **passée** ne compte dans aucun stock :
elle entre dans le **backlog** du fournisseur (§ 3.3 des règles métier).

### 2.2 `fct_receipts` ← `silver_erp_ye.recep_edi`

| Colonne | Type | `recep_edi` | Règle |
|---|---|---|---|
| receipt_id | texte **clé** | `Code_fournisseur|Commande|BL|Article|yyyyMMdd` | construit |
| article_id | texte | `Article` | |
| supplier_id | texte | `Code_fournisseur` | affecte la réception à la voie du fournisseur |
| receipt_date | date | `Date_reception` | |
| qty | nombre | `Quantite_recue` | lignes à quantité nulle ignorées |
| purch_id | texte | `Commande` | information |
| packing_slip | texte | `BL` | information |

Historique souhaité : au moins la fenêtre de backlog (28 jours par défaut, `backlog_days`).

### 2.3 `fct_production_actual` — production réelle journalière (facultatif)

Table à fournir avec les colonnes canoniques (`APPRO_ERP_PRODUCTION_TABLE`, variable `erp_production_table`).
Vide = aucune production réelle : le besoin passé est nul et le reliquat de la semaine en cours est le PDP
entier.

| Colonne | Type | Description |
|---|---|---|
| program_id | texte **clé** | |
| date | date **clé** | jour de production |
| qty | nombre | quantité produite (un 0 déclaré est respecté) |

### 2.4 `fct_production_plan` — PDP hebdomadaire ERP (facultatif)

Le PDP est le plus souvent **importé par fichier** (modèle dans *Imports / exports*, le jeudi ou le
vendredi) ; une version importée et active remplace cette table pour ses programmes.

| Colonne | Type | Description |
|---|---|---|
| program_id | texte **clé** | |
| week_start | date **clé** | lundi de la semaine ISO |
| qty | nombre | |
| version | texte **clé** | la plus récente gagne |

## 3. Base applicative (écrite par l'application)

Tables créées automatiquement au premier démarrage (`create_all`) dans Lakebase, dans le schéma `appro`
que l'application crée et possède (`APPRO_DB_SCHEMA` ; son rôle n'a pas `CREATE` sur `public`) ; SQLite
en local. Chaque écriture est journalisée avec l'utilisateur.

| Table | Contenu | Clé |
|---|---|---|
| `ref_articles`, `ref_suppliers`, `ref_article_suppliers`, `ref_programs`, `ref_bom`, `fct_stock`, `ref_planners`, `ref_delegations` | le référentiel (§ 1) + `updated_by`, `updated_at` | clé de la table |
| `app_meta` | clé / valeur partagées par tous les processus de l'App : `data_version`, incrémentée à chaque écriture pour invalider les caches mémoire de chaque worker | key |
| `app_plan_cells` | **cellules du plan** : article, fournisseur (`""` si aucun), date, expression saisie, quantité, commentaire, auteur | (article, fournisseur, date) |
| `app_adjustments` | **cellules d'ajustement** : article, date (toute date), expression, quantité signée, commentaire, auteur | (article, date) |
| `app_cell_flags` | **clics sur les cellules en lecture** : `kind` = `order_ignored` (commande ferme ignorée : article, fournisseur, jour) ou `proposal_refused` (proposition CBN refusée : article, jour, quantité refusée affichée ; bloque les propositions jusqu'au dimanche), auteur | (kind, article, fournisseur, date) |
| `app_pdp_versions`, `app_pdp_lines` | versions de PDP importées (une active au plus) | id |
| `app_param_overrides` | règles globales du moteur (`global`) et paramètres d'article par semaine ISO (`article_week`) | (scope, key1, key2, field) |
| `app_audit_log` | journal : horodatage, utilisateur (`x-forwarded-email`), action, objet, article, détail JSON | id |
| `erp_purchase_orders`, `erp_receipts`, `erp_production_actual`, `erp_production_plan` | **miroir des faits ERP** (§ 2), écrit par le job de synchronisation (rôle `APPRO_SYNC_ROLE`) | clé de la table |
| `erp_sync_log` | par table miroir : nombre de lignes, horodatage, source, identifiant d'exécution (affiché dans `/api/health`) | table_name |

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
