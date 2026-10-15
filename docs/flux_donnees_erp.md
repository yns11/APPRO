# Flux de données ERP — des tables Unity Catalog à l'application

Ce document suit chaque colonne lue dans les tables Delta de Unity Catalog jusqu'à son usage à
l'écran : ce que lit le SQL de correspondance, ce que le job de synchronisation écrit dans le miroir
Lakebase, ce que l'application en fait (moteur de calcul, cockpit, tableau d'approvisionnement, fiche
article, recherche). Il est écrit à partir du code, pas d'une intention : les noms de fichiers cités
font foi.

Documents voisins : `dictionnaire_donnees.md` (toutes les tables, référentiel compris, avec leurs
libellés Excel), `regles_metier.md` (les règles du moteur), `guide_moteur.md` (exemples calculés),
`deploiement.md` (mise en place du job et de Lakebase).

---

## 1. Vue d'ensemble

```
Unity Catalog (Delta)                      Job Databricks                    Lakebase (Postgres)                  Application (Databricks App)
emotors_data_champions.silver_erp_ye       appro_sync_erp, toutes les        schéma appro, propriété de l'App     FastAPI + moteur NumPy + React
                                           heures à H:20 (Europe/Paris)
commandes_edi        ──┐                                                      erp_purchase_orders  ──┐
recep_edi            ──┤  SQL de           Spark lit, puis par table :        erp_receipts         ──┤  LakebaseSource      erp_dataset()      run_mrp()
desadv_edi           ──┤  correspondance   DELETE + COPY dans UNE             erp_desadv           ──┤  SELECT colonnes     objets moteur      séries jour
conso_composants     ──┤  erp_sql.py       transaction, puis erp_sync_log     erp_consumption_actual┤  canoniques  ──►     (Dataset)   ──►    par article ──► API ──► écrans
silver_base_article  ──┤                                                      erp_prices           ──┤  cache 5 min                           et par voie
bl_en_attente        ──┤                                                      erp_bl_pending       ──┤
(table PDP, option)  ──┘                                                      erp_production_plan  ──┘
                                                                              erp_sync_log (horodatage, lignes)
```

Trois points fixes structurent la chaîne :

1. **Une seule correspondance** : le SQL Databricks de `backend/appro/data/erp_sql.py` est le seul
   endroit où les colonnes ERP sont nommées. Il est exécuté par le job (vers Lakebase) ou, en mode
   `APPRO_DATA_SOURCE=uc`, par l'application elle-même sur un SQL warehouse. Changer une colonne, c'est
   une seule modification.
2. **Un schéma canonique** : `backend/appro/data/schemas.py` déclare, pour chaque table de faits
   `fct_*`, ses colonnes, leurs types (`str`, `float`, `int`, `date`), leur clé et leurs libellés. Le
   même schéma sert au seed CSV local, au miroir `erp_*` (une colonne Postgres par colonne canonique),
   aux fichiers Excel et à la lecture par l'application.
3. **Un jeu d'objets moteur** : `backend/appro/data/assembler.py` transforme les tables canoniques en
   objets Python typés (`OrderLine`, `Receipt`, `DesadvLine`, `ConsumptionLine`, `PdpLine`, `BlPending`,
   `prices`), déjà restreints aux articles du référentiel. Le moteur (`backend/appro/engine/`) ne connaît
   que ces objets.

### 1.1 Les trois sources possibles (`APPRO_DATA_SOURCE`)

| Valeur | Classe (`backend/appro/data/sources.py`) | Lecture | Usage |
|---|---|---|---|
| `local` | `LocalCsvSource` | un fichier `data/seed/<table>.csv` par table canonique | développement, tests, démo |
| `uc` | `UnityCatalogSource` | exécute le SQL de correspondance sur un SQL warehouse (`DATABRICKS_WAREHOUSE_ID`), authentification par le principal de service de l'App | lecture directe, sans miroir ni job |
| `lakebase` | `LakebaseSource` | `SELECT <colonnes canoniques> FROM erp_<table>` dans la base applicative | **cible retenue** : millisecondes, aucun droit Unity Catalog nécessaire à l'App |

Dans les trois cas, le résultat passe par `coerce()` (`schemas.py`) qui normalise les types : texte
nettoyé (`""` pour une valeur absente), nombres (`0.0` pour une valeur absente ou non numérique), dates
(`None` si non interprétable), booléens. Les tables sont ensuite mises en cache en mémoire pendant
`APPRO_CACHE_TTL_SECONDS` (300 s par défaut) ; `POST /api/reference/refresh` vide ce cache.

### 1.2 Les variables qui désignent les tables source

| Variable d'environnement (App) | Variable du bundle / paramètre du job | Défaut | Vide = |
|---|---|---|---|
| `APPRO_UC_CATALOG` | `uc_catalog` / `--catalog` | `emotors_data_champions` | — |
| `APPRO_UC_SCHEMA` | `uc_schema` / `--schema` | `silver_erp_ye` | — |
| `APPRO_ERP_ORDERS_TABLE` | `erp_orders_table` / `--orders-table` | `commandes_edi` | obligatoire |
| `APPRO_ERP_RECEIPTS_TABLE` | `erp_receipts_table` / `--receipts-table` | `recep_edi` | obligatoire |
| `APPRO_ERP_DESADV_TABLE` | `erp_desadv_table` / `--desadv-table` | `desadv_edi` | aucun DESADV |
| `APPRO_ERP_CONSUMPTION_TABLE` | `erp_consumption_table` / `--consumption-table` | `conso_composants` | aucune consommation réelle |
| `APPRO_ERP_PRICES_TABLE` | `erp_prices_table` / `--prices-table` | `silver_base_article` | aucune valorisation |
| `APPRO_ERP_BL_PENDING_TABLE` | `erp_bl_pending_table` / `--bl-pending-table` | `bl_en_attente` | aucun BL en attente |
| `APPRO_ERP_PDP_TABLE` | `erp_pdp_table` / `--pdp-table` | *(vide)* | PDP uniquement par fichier ou saisie |

Une table facultative laissée vide n'est ni lue par le job ni attendue par l'application : la table
canonique correspondante est simplement vide, et les écrans qui en dépendent l'indiquent (par exemple
« sans prix » sur la carte *Valeur du stock*).

---

## 2. Les sources Unity Catalog, colonne par colonne

Chaque sous-section donne : la table Delta lue, le grain de la lecture, les colonnes exactement lues,
les filtres, puis le SQL exécuté tel quel (`erp_sql.py`). Les colonnes de la table source qui ne
figurent pas ici ne sont **pas lues**.

### 2.1 `commandes_edi` → `fct_purchase_orders` (commandes d'achat)

**Grain lu** : un **créneau de livraison** `ID` = `vendaccount|itemid|yyyyMMdd|silfirmorder`, c'est-à-dire
un fournisseur, un article, une date de livraison et un caractère ferme / prévisionnel. Plusieurs lignes
de `commandes_edi` (plusieurs numéros de commande d'achat) peuvent partager un créneau : elles sont
**sommées**. Une ligne sans `ID` est ignorée.

| Colonne `commandes_edi` | Type attendu | Colonne canonique | Transformation |
|---|---|---|---|
| `ID` | texte | `order_id` (clé) | tel quel ; groupe d'agrégation |
| `Article` | texte | `article_id` | tel quel ; groupe d'agrégation |
| `Code_fournisseur` | texte | `supplier_id` | tel quel ; groupe d'agrégation |
| `Nom_fournisseur` | texte | `supplier_name` | `MAX` sur le groupe (information) |
| `Ordre_ferme` | texte `Oui` / autre | `order_type` | `MAX = 'Oui'` → `FIRM`, sinon `FORECAST` ; **seul critère** ferme / prévisionnel |
| `Date_de_debut` | date | `expected_date` | `MIN` sur le groupe, tronquée au jour |
| `Quantite` | nombre | `qty_ordered` | `SUM`, en double |
| `Quantite_restante` | nombre | `qty_open` | `SUM(GREATEST(x, 0))` : un restant négatif compte 0 |
| `Commande` | texte | `purch_id` | liste triée, dédoublonnée, jointe par `, ` (information) |
| `Niveau_engagement` | texte | `commitment` | `MAX` (information, non utilisé par le moteur) |
| — | — | `first_seen` | **absente du SQL** : posée par le job (§ 3.3) |

```sql
SELECT
  c.ID AS order_id, c.Article AS article_id, c.Code_fournisseur AS supplier_id,
  MAX(c.Nom_fournisseur) AS supplier_name,
  CASE WHEN MAX(c.Ordre_ferme) = 'Oui' THEN 'FIRM' ELSE 'FORECAST' END AS order_type,
  CAST(MIN(c.Date_de_debut) AS DATE) AS expected_date,
  CAST(SUM(c.Quantite) AS DOUBLE) AS qty_ordered,
  CAST(SUM(GREATEST(c.Quantite_restante, 0)) AS DOUBLE) AS qty_open,
  CONCAT_WS(', ', SORT_ARRAY(COLLECT_SET(c.Commande))) AS purch_id,
  MAX(c.Niveau_engagement) AS commitment
FROM `emotors_data_champions`.`silver_erp_ye`.`commandes_edi` c
WHERE c.ID IS NOT NULL
GROUP BY c.ID, c.Article, c.Code_fournisseur
```

Hypothèses sur l'amont (règles du job qui reconstruit `commandes_edi`, hors périmètre de l'application) :
les commandes fermes sont conservées depuis le début de l'année, les prévisionnelles à partir du lundi
suivant ; l'ERP ne déplace ni n'annule un créneau, et `Quantite_restante` n'est plus fiable une fois la
date passée.

### 2.2 `recep_edi` → `fct_receipts` (journaux d'arrivée et réceptions)

**Grain lu** : une ligne par fournisseur, commande, BL, article, jour et statut de réception. Les lignes dont
`Quantite_recue` est nulle ou vide sont ignorées.

| Colonne `recep_edi` | Type attendu | Colonne canonique | Transformation |
|---|---|---|---|
| `Code_fournisseur` | texte | `supplier_id` | tel quel ; entre dans `receipt_id` |
| `Commande` | texte | `purch_id` | tel quel ; entre dans `receipt_id` |
| `BL` | texte | `packing_slip` | tel quel, **entier** ; dans `receipt_id`, remplacé par `LEFT(SHA2(BL, 256), 16)` si plus de 60 caractères (plusieurs BL séparés par ` \| `) |
| `Article` | texte | `article_id` | tel quel ; entre dans `receipt_id` |
| `Date_reception` | date | `receipt_date` | tronquée au jour ; `yyyyMMdd` dans `receipt_id` |
| `Quantite_recue` | nombre | `qty` | en double ; `IS NOT NULL AND <> 0` |
| `Statut_reception` | texte | `status` | `COALESCE(x, 'Reçu')` ; entre dans `receipt_id` ; trois valeurs attendues : **`En transit`**, **`Reçu`**, **`Comptabilisé`** (§ 5.2) |

```sql
SELECT
  CONCAT_WS('|', r.Code_fournisseur, r.Commande,
            CASE WHEN LENGTH(COALESCE(r.BL, '')) > 60 THEN LEFT(SHA2(r.BL, 256), 16) ELSE COALESCE(r.BL, '') END,
            r.Article, DATE_FORMAT(r.Date_reception, 'yyyyMMdd'), COALESCE(r.Statut_reception, '')) AS receipt_id,
  r.Article AS article_id, r.Code_fournisseur AS supplier_id,
  CAST(r.Date_reception AS DATE) AS receipt_date,
  CAST(r.Quantite_recue AS DOUBLE) AS qty,
  r.Commande AS purch_id, r.BL AS packing_slip,
  COALESCE(r.Statut_reception, 'Reçu') AS status
FROM `emotors_data_champions`.`silver_erp_ye`.`recep_edi` r
WHERE r.Quantite_recue IS NOT NULL AND r.Quantite_recue <> 0
```

**Contrat du statut** (job amont, table `wmsjournaltable` jointe à `wmsjournaltrans` et à l'ACR de
`vend_packing_slip_trans`) :

| `Statut_reception` | État ERP | `Date_reception` |
|---|---|---|
| `En transit` | journal d'arrivée créé, non validé (`posted = 0`) | date de la ligne du journal (`transDate`) |
| `Reçu` | journal validé (`posted = 1`), aucun accusé de réception pour ce BL et cet article | date de validation (`postedDateTime`) |
| `Comptabilisé` | journal validé, accusé de réception présent dans `vend_packing_slip_trans` | date de validation (`postedDateTime`) |

Historique souhaité : au moins la fenêtre de backlog (`backlog_days`, 28 jours par défaut) avant aujourd'hui, et
tout ce qui suit la date d'initialisation du stock.

### 2.3 `desadv_edi` → `fct_desadv` (avis d'expédition EDI)

Table préparée en amont depuis les messages `DespatchAdvice-Purchase` (`bronze_erp.siledimessage`,
`siledi_item_line`, `purch_table`). **Grain lu** : une ligne par ligne d'article d'un message. Les lignes
sans article ou sans date d'émission sont ignorées ; un BL vide est **conservé** (le DESADV est affiché
mais ne peut être rapproché).

| Colonne `desadv_edi` | Type attendu | Colonne canonique | Transformation |
|---|---|---|---|
| `Document_ID` | texte / nombre | `desadv_id` (clé) | `Document_ID\|ID_Ligne` |
| `ID_Ligne` | texte / nombre | `desadv_id` (clé) | idem |
| `Code_article` | texte | `article_id` | tel quel ; `IS NOT NULL` |
| `Code_fournisseur` | texte | `supplier_id` | tel quel |
| `Nom_fournisseur` | texte | `supplier_name` | tel quel (information) |
| `BL` | texte | `packing_slip` | en texte ; **clé de rapprochement** avec `recep_edi.BL` |
| `Commande_ouverte` | texte | `purch_id` | en texte (information) |
| `Date_emission` | date | `issue_date` | tronquée au jour ; `IS NOT NULL` |
| `Quantite_achat` | nombre | `qty` | en double |
| `Etat_message` | texte | `state` | `Créé`, `Traité`, `Erreur`, `En attente`, `Annulé` |
| `Traitement_final` | texte | `final_processing` | `Non traité`, `OK` |
| `ID_transaction_stock` | texte / nombre | `stock_trans_id` | en texte ; vide sur un message traité = **aucun journal de saisie créé** |

```sql
SELECT
  CONCAT_WS('|', CAST(d.Document_ID AS STRING), CAST(d.ID_Ligne AS STRING)) AS desadv_id,
  d.Code_article AS article_id, d.Code_fournisseur AS supplier_id, d.Nom_fournisseur AS supplier_name,
  CAST(d.BL AS STRING) AS packing_slip, CAST(d.Commande_ouverte AS STRING) AS purch_id,
  CAST(d.Date_emission AS DATE) AS issue_date, CAST(d.Quantite_achat AS DOUBLE) AS qty,
  d.Etat_message AS state, d.Traitement_final AS final_processing,
  CAST(d.ID_transaction_stock AS STRING) AS stock_trans_id
FROM `emotors_data_champions`.`silver_erp_ye`.`desadv_edi` d
WHERE d.Code_article IS NOT NULL AND d.Date_emission IS NOT NULL
```

### 2.4 `conso_composants` → `fct_consumption_actual` (consommation réelle par composant)

Table **à préparer en amont** avec un contrat réduit à trois colonnes canoniques ; le nom de la table
est libre (`APPRO_ERP_CONSUMPTION_TABLE`). Contrairement au PDP, éclaté par le moteur à travers la
nomenclature, la consommation arrive **déjà éclatée** : une quantité de composant consommée, par jour,
dans l'unité de stock de l'article. **Grain lu** : `(article_id, jour)`, les lignes d'un même jour
(programmes, mouvements) sont sommées.

| Colonne source | Type attendu | Colonne canonique | Transformation |
|---|---|---|---|
| `article_id` | texte | `article_id` (clé) | tel quel ; `IS NOT NULL` ; référence composant telle que dans `ref_articles` |
| `date` | date ou horodatage | `date` (clé) | tronquée au jour ; `IS NOT NULL` ; jour du **mouvement de consommation** |
| `qty` | nombre | `qty` | `SUM` par (article, jour) ; `IS NOT NULL` ; un 0 déclaré est respecté, un négatif se soustrait |

```sql
SELECT c.article_id AS article_id, CAST(c.date AS DATE) AS date, CAST(SUM(c.qty) AS DOUBLE) AS qty
FROM `emotors_data_champions`.`silver_erp_ye`.`conso_composants` c
WHERE c.article_id IS NOT NULL AND c.date IS NOT NULL AND c.qty IS NOT NULL
GROUP BY c.article_id, CAST(c.date AS DATE)
```

Toute autre colonne (programme, ordre de fabrication, atelier) est tolérée et ignorée. Règles
d'alimentation : couvrir tous les jours depuis la date d'initialisation du stock jusqu'à hier (un jour
absent suit `missing_actual_policy`, mieux vaut un 0 explicite) ; ne jamais déclarer le jour courant ni
le futur.

### 2.5 `silver_base_article` → `fct_prices` (prix de revient standard)

**Grain lu** : un prix par `item_id`, restreint aux références `P-00%` (la base article contient bien plus
d'items que le référentiel). En cas de doublon, `MAX`.

| Colonne `silver_base_article` | Type attendu | Colonne canonique | Transformation |
|---|---|---|---|
| `item_id` | texte | `article_id` (clé) | `IS NOT NULL AND LIKE 'P-00%'` |
| `std_cost_price` | nombre | `price` | `MAX`, en double, euros par unité de stock ; `IS NOT NULL` |

```sql
SELECT a.item_id AS article_id, CAST(MAX(a.std_cost_price) AS DOUBLE) AS price
FROM `emotors_data_champions`.`silver_erp_ye`.`silver_base_article` a
WHERE a.item_id IS NOT NULL AND a.std_cost_price IS NOT NULL AND a.item_id LIKE 'P-00%'
GROUP BY a.item_id
```

Un article présent ici et absent du référentiel n'est **jamais ajouté** au référentiel ; seuls les
articles du référentiel sont valorisés.

### 2.6 `bl_en_attente` → `fct_bl_pending` (accusés de réception non validés, facultatif)

Réceptions validées depuis au moins 7 jours sans accusé de réception. Depuis le contrat à trois statuts de
`recep_edi`, le flux *À valider* est calculé par l'application depuis les réceptions (§ 5.2) ; cette table ne sert
plus qu'à la page *Recherche* et peut rester vide. **Grain lu** : une ligne par fournisseur, commande, BL,
article, jour d'enregistrement.

| Colonne `bl_en_attente` | Type attendu | Colonne canonique | Transformation |
|---|---|---|---|
| `Fournisseur` | texte | `supplier_id` | tel quel ; entre dans `pending_id` |
| `Commande` | texte | `purch_id` | tel quel ; entre dans `pending_id` |
| `BL_DESADV` | texte | `packing_slip` | entier ; haché sur 16 caractères dans `pending_id` au-delà de 60 caractères |
| `Article` | texte | `article_id` | `IS NOT NULL` ; entre dans `pending_id` |
| `Quantite` | nombre | `qty` | en double |
| `Date_enregistrement` | date | `registered_date` | tronquée au jour ; `IS NOT NULL` ; `yyyyMMdd` dans `pending_id` |
| `Jours_en_attente` | nombre | `days_pending` | en entier |

### 2.7 Table de PDP hebdomadaire → `fct_production_plan` (facultative, non configurée aujourd'hui)

Si `APPRO_ERP_PDP_TABLE` est renseignée, la table doit porter les colonnes canoniques `program_id`,
`week_start` (lundi ISO), `qty`, `version` (texte, vide accepté). Le PDP vient aujourd'hui de la page
*Plan de production* (saisie directe ou import d'un classeur), dont la version active **remplace** cette
table pour ses programmes.

```sql
SELECT p.program_id AS program_id, CAST(p.week_start AS DATE) AS week_start, CAST(p.qty AS DOUBLE) AS qty,
       COALESCE(CAST(p.version AS STRING), '') AS version
FROM <catalogue>.<schéma>.<table> p
```

### 2.8 Ce qui n'est pas lu

* Aucune table de stock ERP : le **stock de référence** (`fct_stock`) est une table du référentiel, saisie
  ou importée dans l'application (une date d'initialisation unique pour tous les articles).
* Aucune table de nomenclature, de fournisseur ni d'article ERP : le référentiel (`ref_*`) est géré dans
  l'application. Les prix sont la seule information d'article lue de l'ERP.
* Les colonnes de `commandes_edi`, `recep_edi` et `desadv_edi` non citées ci-dessus.

---

## 3. Le miroir Lakebase

### 3.1 Les tables `erp_*`

Le miroir est **créé par l'application** à son démarrage (`init_store`, `backend/appro/data/store.py`),
dans le schéma Postgres `appro` (`APPRO_DB_SCHEMA`) qu'elle possède. Une table `erp_<nom>` par table
canonique `fct_<nom>`, construite automatiquement depuis `schemas.py` :

| Type canonique | Type Postgres | Remarque |
|---|---|---|
| `str` | `TEXT` | non borné : une réception enregistrée liste des dizaines de BL, un créneau tous ses numéros de commande ; un `VARCHAR(200)` d'une version antérieure a cassé le `COPY` du job et est **élargi automatiquement** au démarrage (`ensure_columns`) |
| `float` | `DOUBLE PRECISION` | |
| `int` | `INTEGER` | |
| `date` | `DATE` | |
| `bool` | `BOOLEAN` | (aucune colonne de faits) |

Les colonnes de la **clé** canonique forment la clé primaire (`NOT NULL`) ; les autres sont nullables. Une
colonne ajoutée par une nouvelle version de l'application est créée sur la table existante par
`ALTER TABLE … ADD COLUMN` au démarrage (les lignes sont conservées). Les tables `erp_*` ne portent ni
`updated_by` ni `updated_at` : elles sont remplacées en bloc, pas éditées.

| Table Lakebase | Clé primaire | Colonnes |
|---|---|---|
| `erp_purchase_orders` | `order_id` | `article_id`, `supplier_id`, `supplier_name`, `order_type`, `expected_date`, `qty_ordered`, `qty_open`, `purch_id`, `commitment`, `first_seen` |
| `erp_receipts` | `receipt_id` | `article_id`, `supplier_id`, `receipt_date`, `qty`, `purch_id`, `packing_slip`, `status` |
| `erp_desadv` | `desadv_id` | `article_id`, `supplier_id`, `supplier_name`, `packing_slip`, `purch_id`, `issue_date`, `qty`, `state`, `final_processing`, `stock_trans_id` |
| `erp_consumption_actual` | `article_id`, `date` | `qty` |
| `erp_prices` | `article_id` | `price` |
| `erp_bl_pending` | `pending_id` | `supplier_id`, `purch_id`, `packing_slip`, `article_id`, `qty`, `registered_date`, `days_pending` |
| `erp_production_plan` | `program_id`, `week_start`, `version` | `qty` |
| `erp_sync_log` | `table_name` | `row_count`, `synced_at`, `source`, `run_id` |

### 3.2 Droits

L'App possède les tables. Le job tourne sous une autre identité (`APPRO_SYNC_ROLE`, variable `sync_role`
du bundle : un utilisateur ou le `client_id` d'un principal de service). Au démarrage, l'application lui
accorde `USAGE` sur le schéma et `SELECT, INSERT, UPDATE, DELETE, TRUNCATE` sur chaque table `erp_*` et
sur `erp_sync_log` (`grant_sync_role`). D'où l'ordre impératif : **déployer et démarrer l'application
avant la première exécution du job** ; le job vérifie la présence des tables cibles et échoue avec un
message explicite sinon.

### 3.3 Le job `appro_sync_erp` (`jobs/sync_erp_to_lakebase.py`)

Planifié **toutes les heures à H:20, Europe/Paris**, tous les jours, actif (`UNPAUSED`) en dev, préprod et
prod (`resources/appro_sync.job.yml`), décalé après le job amont qui reconstruit `commandes_edi` et
`recep_edi` à H:00. Une exécution à la fois, délai maximal 30 min, une relance automatique.

Pour chaque table configurée, dans l'ordre `fct_purchase_orders`, `fct_receipts`, puis les tables
facultatives présentes :

1. **Contrôles** : existence de toutes les tables source (une seule erreur listant les manquantes),
   existence des tables cibles dans Lakebase.
2. **Lecture Spark** du SQL de correspondance (§ 2). Les colonnes canoniques absentes du résultat sont
   celles que le job remplit lui-même ; aujourd'hui une seule : `first_seen`.
3. **Transaction unique** par table : `SELECT order_id, first_seen` de la copie en place (commandes
   uniquement), puis `DELETE FROM erp_<nom>`, puis `COPY … FROM STDIN` ligne à ligne depuis Spark
   (`toLocalIterator`), puis `INSERT … ON CONFLICT` dans `erp_sync_log`. Les lecteurs voient l'ancienne copie
   jusqu'au commit ; une exécution interrompue laisse la copie précédente intacte.
4. **`first_seen`** : pour chaque créneau de commande, la date `first_seen` de la copie précédente est
   reportée ; un créneau inconnu reçoit la date du jour de l'exécution. C'est la seule information que
   le miroir **ajoute** à la source : le jour où un créneau est apparu dans l'ERP, que l'ERP lui-même ne
   conserve pas. Tant que le job n'a jamais tourné, la colonne est vide et le cockpit le signale.

Le journal `erp_sync_log` (nombre de lignes, horodatage UTC, table source, identifiant d'exécution) est
exposé par `GET /api/health` et `GET /api/config` (clé `data_source.sync`) ; la page *Paramètres* n'affiche
aujourd'hui que le nom de la source et le catalogue.

Exécution manuelle : `databricks bundle run appro_sync_erp -t dev --profile <PROFIL>` ; un sous-ensemble
de tables avec `--tables fct_receipts,fct_desadv`. Le mode notebook (`jobs/sync_erp_to_lakebase_notebook.py`)
exécute le même SQL.

### 3.4 Lecture par l'application

`LakebaseSource.table("fct_receipts")` exécute `SELECT <colonnes canoniques> FROM erp_receipts` (toutes les
lignes : quelques milliers à quelques dizaines de milliers), applique `coerce()`, puis met le résultat
en cache pendant 5 min. L'application ne lit donc le miroir qu'une fois par table et par tranche de
5 min et par processus ; une écriture de l'utilisateur (cellule, référentiel) incrémente `data_version`
dans `app_meta`, ce qui invalide les résultats de calcul en cache de tous les processus, sans relire les
faits ERP. Une nouvelle copie du job est vue au plus tard 5 min après son commit.

---

## 4. Le modèle de données final : les objets du moteur

`erp_dataset()` (`backend/appro/data/assembler.py`) construit un `Dataset` (`backend/appro/engine/models.py`)
à partir des tables canoniques. Première règle, valable pour tous les faits : **seuls les articles du
référentiel (`ref_articles`) sont retenus**, restreints au périmètre demandé (approvisionneur, liste
d'articles). Une ligne ERP d'un article inconnu du référentiel est silencieusement ignorée.

| Objet | Source | Construction | Lignes ignorées |
|---|---|---|---|
| `OrderLine` | `fct_purchase_orders` | `order_id`, `article_id`, `supplier_id` (`None` si vide), `expected_date`, `qty_ordered`, `qty_open`, `order_type` (`FIRM` par défaut), `ref` = `purch_id`, `first_seen` | sans date de livraison |
| `Receipt` | `fct_receipts` | `receipt_id`, `article_id`, `receipt_date`, `qty`, `supplier_id`, `ref` = `purch_id`, `packing_slip`, `status` ; propriétés `registered` (statut commençant par « enregistr ») et `packing_slips` (liste des BL séparés par ` \| `) | sans date |
| `DesadvLine` | `fct_desadv` | toutes les colonnes ; propriété `processed` = état *Traité* et traitement final *OK* | sans date d'émission (un BL vide est gardé) |
| `ConsumptionLine` | `fct_consumption_actual` | `article_id`, `date`, `qty` | sans date |
| `PdpLine` | `fct_production_plan` puis versions de l'application | `program_id`, `week_start`, `qty`, `version` ; seuls les programmes dont la nomenclature touche les articles retenus | sans lundi |
| `prices` | `fct_prices` | dictionnaire `article_id → prix` | prix vide |
| `BlPending` | `fct_bl_pending` | toutes les colonnes | sans date d'enregistrement |
| `StockSnapshot` | `fct_stock` (référentiel) | la ligne la plus récente par article ; plusieurs lignes à la même date sont sommées | — |

Les tables de référentiel (`Article`, `Supplier`, `SupplierLink`, `Program`, `BomLine`) complètent le
`Dataset`, puis `app_entries_into_dataset` (`services/mrp_service.py`) y ajoute les saisies de
l'application : cellules Appro. (`app_plan_cells`), ajustements (`app_adjustments`), drapeaux
(`app_cell_flags` : commande ignorée, proposition refusée, DESADV masqué), version de PDP active
(`app_pdp_lines`), paramètres par semaine (`app_param_overrides`), taux de rebut datés par programme
(`app_program_scrap`, appliqués à chaque ligne de PDP : besoin = PDP × (1 + taux / 100)).

Repères de temps (`regles_metier.md` § 1) : le **point zéro** est la date d'initialisation du stock
(`fct_stock.snapshot_date`, unique) ; **aujourd'hui** est la date réelle ou `APPRO_AS_OF` ; la fenêtre
de calcul va du point zéro à aujourd'hui + `horizon_days`, étendue au dimanche de la semaine en cours.
Rien n'existe avant le point zéro : un fait daté avant lui n'entre dans aucun calcul.

---

## 5. Exploitation métier, colonne par colonne

Les rubriques renvoient aux écrans : **TA** = tableau d'approvisionnement (vue globale et fiche article,
même grille), **FA** = fiche article (indicateurs), **CK** = cockpit (onglets *Couvertures et stocks* et
*Flux d'approvisionnement*), **RE** = page Recherche, **AL** = alertes.

### 5.1 Commandes (`erp_purchase_orders`)

Chaque commande est affectée à la **voie** (ligne de fournisseur) du tableau d'approvisionnement désignée
par `supplier_id` ; un fournisseur absent des règles article ↔ fournisseur crée sa propre voie, un
fournisseur vide rejoint la première voie.

| Colonne | Usage |
|---|---|
| `order_type` | `FIRM` : la commande est **ferme**, elle entre dans le Projeté ERP (paramètre `firm_sources`) et dans la ligne *Ferme* (TA) ; `FORECAST` : ligne *Prévisionnel* (TA), information seulement, jamais comptée dans un stock, datée à sa date réelle ou au lundi de sa semaine (`forecast_date_policy`) |
| `expected_date` | jour de la commande dans le TA. Ferme **à partir d'aujourd'hui** : comptée dans le Projeté ERP et, par défaut, dans la ligne *Appro.* (sauf cellule saisie ou jour ignoré par clic). Ferme **avant aujourd'hui** : affichée dans *Ferme (passé)*, ne compte dans aucun stock, entre dans le **backlog** du fournisseur si elle date de moins de `backlog_days`. Le flux **À réceptionner** (CK) liste les fermes dont la date est aujourd'hui |
| `qty_open` | quantité **restant à livrer** : c'est elle qui alimente le Projeté ERP et l'*Appro.* par défaut ; une ferme dont le restant est nul est « soldée » (fond vert, TA) ; le jour même, le restant est diminué des réceptions du jour du fournisseur |
| `qty_ordered` | quantité **commandée** : affichée dans la cellule *Ferme* (TA, `restant / commandé` en cas de livraison partielle), comptée dans le backlog (Σ commandé − Σ reçu sur la fenêtre), affichée dans les flux et la recherche |
| `purch_id` | numéros de commande d'achat en info-bulle de la cellule *Ferme* et *Prévisionnel*, colonnes des flux (CK) et de la recherche (RE) |
| `supplier_name` | non utilisé par les écrans (le nom vient de `ref_suppliers`) ; conservé pour le diagnostic |
| `commitment` | non utilisé |
| `first_seen` | flux **Commandé** (CK) : créneaux fermes apparus **aujourd'hui** dans l'ERP, quelle que soit leur date de livraison ; colonne *Première apparition* de la recherche (RE). Vide tant que le job n'a pas tourné : le cockpit affiche alors que l'information n'est pas disponible |
| `order_id` | identifiant technique ; porté par les lignes des flux et de la recherche |

Clic sur une cellule *Ferme* (TA) : la commande ferme du jour et du fournisseur est **ignorée**
(`app_cell_flags`, `order_ignored`) : hors Projeté ERP et hors *Appro.*, barrée en rouge ; second clic pour
rétablir. Dans une colonne semaine, le clic porte sur tous les jours de la semaine.

### 5.2 Journaux d'arrivée et réceptions (`erp_receipts`)

| Colonne | Usage |
|---|---|
| `status` | **`En transit`** : la ligne n'est pas une réception. Affichée dans la ligne *Reçu* (TA) à sa date, en italique sur fond orange avec une icône camion, jamais comptée ; flux **En transit** (CK). Son BL « porte » le DESADV de même BL, qui n'est plus affiché séparément. **`Reçu`** : réception comptée, accusé de réception à valider ; après 7 jours, flux **À valider** (CK). **`Comptabilisé`** : réception comptée, **fond vert** (TA). Colonne *Statut* des flux et de la recherche |
| `receipt_date`, `qty` | pour `Reçu` / `Comptabilisé` : ligne *Reçu* (TA) au jour de réception, sur la voie de `supplier_id` ; **entrée de stock** dans la reconstitution du passé (Projeté ERP et Projeté Appro., entre le point zéro et hier) ; réceptions de la fenêtre de backlog (`backlog_days` avant aujourd'hui) soustraites des commandes fermes passées ; réceptions du jour soustraites du restant ferme du jour ; flux **Reçu** (CK) : réceptions datées d'aujourd'hui ; les lignes antérieures au point zéro sont ignorées. Pour `En transit` : jour et quantité affichés, rien de compté |
| `packing_slip` | numéro(s) de BL dans l'info-bulle de la cellule *Reçu* (TA), colonnes des flux et de la recherche ; **rapprochement avec les DESADV** : un BL porté par une réception marque le DESADV comme reçu, un BL porté par un journal en transit fait afficher le journal à la place du DESADV ; le **point** de la ligne est vert si un DESADV traité porte ce BL, rouge sinon ; un BL vide = « sans BL », réception manuelle, point rouge |
| `purch_id` | info-bulle, flux, recherche |
| `receipt_id` | identifiant technique ; identifiant des lignes *En transit* (journaux) et *À valider* |

### 5.3 Avis d'expédition (`erp_desadv`)

Un DESADV n'est **jamais compté** dans un stock : il annonce. Un message `Annulé` est ignoré partout.

| Colonne | Usage |
|---|---|
| `packing_slip` | **clé de rapprochement** avec `erp_receipts.packing_slip` : BL porté par une réception validée → DESADV *reçu* (disparaît de la ligne *Reçu* et du flux *En transit*) ; BL porté par un journal en transit → le journal est affiché à sa place ; sinon DESADV **annoncé** : affiché en italique sur fond orange avec l'icône camion dans la ligne *Reçu* (TA) au jour d'émission, listé dans **En transit** (CK). Un BL vide ne peut être rapproché : affiché, jamais reçu, sauf masquage |
| `issue_date` | jour d'affichage dans la ligne *Reçu* (TA) ; tri des flux |
| `qty` | quantité annoncée, affichée dans la cellule (TA), les flux et la recherche |
| `state`, `final_processing` | `processed` = *Traité* et *OK* : point **vert** sur l'en transit (DESADV ou journal portant ce BL), rouge sinon (TA) ; une réception dont le BL correspond à un DESADV traité porte un point vert, rouge sinon (réception manuelle) ; `Erreur` → flux **À traiter** (CK, motif « message en erreur ») ; `Annulé` → ignoré |
| `stock_trans_id` | vide sur un message **traité** = aucun journal de saisie créé dans l'ERP → flux **À traiter** (CK, motif « traité sans journal de saisie ») ; colonne *Journal* de la recherche (oui / non) |
| `supplier_id` | voie du TA ; un fournisseur inconnu des règles crée sa voie |
| `purch_id`, `supplier_name` | information (info-bulle, flux, recherche) |

Double-clic sur un en transit (TA, vue jour) : il est **masqué** (`desadv_hidden`, BL conservé en note),
rétablissable depuis *Saisies & journal*.

### 5.4 Consommation réelle (`erp_consumption_actual`)

| Colonne | Usage |
|---|---|
| `article_id`, `date`, `qty` | ligne *Besoin* (TA) des **jours passés** (du point zéro à hier) : la consommation déclarée remplace le PDP éclaté (`production_mode = actual_then_remainder`, défaut) ; un jour passé sans déclaration suit `missing_actual_policy` (0 par défaut, ou PDP). **Semaine en cours** : le reliquat du PDP éclaté de la semaine (PDP − consommation déjà déclarée cette semaine, plancher 0) est réparti sur les jours ouvrés restants, aujourd'hui compris. Les semaines suivantes suivent le PDP. Modes `actual_then_plan`, `plan_only`, `actual_only` (paramètres globaux). L'API expose la part de réel sur 30 jours (`actual_share_30d`), non affichée à l'écran |

Cette ligne *Besoin* est l'entrée de tout le reste : projection des stocks, couvertures, stock cible,
alertes, propositions CBN.

### 5.5 Prix (`erp_prices`)

| Colonne | Usage |
|---|---|
| `price` | **Valeur du stock** (CK, onglet *Couvertures et stocks*) = Σ prix × stock à date (stock projeté ERP à la fin d'hier), avec l'écart en % à la **valeur cible** = Σ prix × stock cible du jour ; **projection de la valeur du stock** (CK) = Σ prix × Projeté Appro. à la fin de chaque semaine ISO ; indicateurs *Prix*, *Valeur du stock*, *Valeur cible* (FA). Un article sans prix n'entre dans aucune valeur et est compté « sans prix » sur la carte |

### 5.6 BL en attente (`erp_bl_pending`)

| Colonne | Usage |
|---|---|
| toutes | recherche (RE), type *BL en attente*, uniquement : le flux **À valider** (CK) est calculé depuis `erp_receipts` (statut `Reçu` depuis 7 jours ou plus). Aucun effet sur les calculs |

### 5.7 PDP ERP (`erp_production_plan`)

| Colonne | Usage |
|---|---|
| `program_id`, `week_start`, `qty`, `version` | PDP par défaut d'un programme, éclaté par la nomenclature en besoin journalier (lissage `spread_rounding`) ; remplacé, programme par programme, par la version active saisie ou importée dans la page *Plan de production* ; la version la plus récente gagne |

### 5.8 Synthèse par écran

| Écran | Faits ERP mobilisés |
|---|---|
| Tableau d'approvisionnement (TA) | *Besoin* ← consommation + PDP éclaté ; *Ferme*, *Ferme (passé)*, *Prévisionnel* ← commandes ; *Reçu* ← réceptions validées (statut, BL, ACR) et en transit (journaux non validés, DESADV annoncés) ; *Appro.* ← fermes restantes par défaut ; *Projeté ERP / Appro.* ← stock de référence + réceptions passées + fermes futures (+ saisies) − besoin ; en-tête de voie : backlog ← fermes passées − réceptions |
| Fiche article (FA) | les mêmes, plus : stock à date, prix et valeurs, prochaine livraison (ferme, saisie ou CBN), backlog depuis la date de fenêtre |
| Cockpit, *Couvertures et stocks* (CK) | couvertures et stocks projetés (tous faits), valeur du stock et projection ← prix |
| Cockpit, *Flux d'approvisionnement* (CK) | À commander ← propositions CBN urgentes ; Commandé ← `first_seen` ; En transit ← journaux non validés + DESADV annoncés sans journal ; À traiter ← DESADV en erreur ou sans journal de saisie ; À réceptionner ← fermes du jour ; Reçu ← réceptions du jour ; En retard ← backlog ; À valider ← réceptions `Reçu` depuis 7 jours |
| Recherche (RE) | réceptions, commandes, DESADV, BL en attente : toutes les lignes du périmètre, filtres texte (`;` = ou), dates, fournisseur |
| Alertes (AL) | calculées sur les stocks projetés ; le backlog est affiché, pas alerté |
| `GET /api/health`, `GET /api/config` | `erp_sync_log` : fraîcheur et volume de chaque table miroir |

---

## 6. Règles de fraîcheur et limites à connaître

* **Latence** : job toutes les heures à H:20, cache applicatif de 5 min. Une réception saisie dans l'ERP à
  H:05 est visible dans l'application entre H:20 et H:25 environ.
* **Pas de différentiel** : chaque exécution remplace la table entière. Une ligne disparue de la source
  disparaît du miroir ; seule `first_seen` survit, et seulement pour les créneaux encore présents.
* **`qty_open` d'une commande passée** n'est pas fiable (l'ERP ne la solde pas) : l'application n'y lit que
  l'affichage `restant / commandé` ; le backlog se calcule sur `qty_ordered` − réceptions.
* **Un DESADV sans BL** et une réception sans BL ne peuvent pas être rapprochés : la réception est une
  réception manuelle (point rouge), le DESADV reste en transit tant qu'il n'est pas masqué.
* **Un journal d'arrivée non validé** n'est visible que si `recep_edi` le conserve avec le statut `En transit` ;
  l'accusé de réception vient de `vend_packing_slip_trans` (ancien schéma, un jour de décalage) : une réception
  reste `Reçu` un jour après la validation de l'ACR.
* **Point zéro** : une réception, une commande ou une consommation datée avant la date d'initialisation
  du stock n'entre dans aucun calcul ; une table de faits qui ne couvre pas cette date fausse la
  reconstitution du passé.
* **Articles inconnus** : toute ligne ERP dont l'article n'est pas dans `ref_articles` est ignorée sans
  message. Le Référentiel (bouton *Sans fournisseur*, colonne *Programmes*) aide à repérer les articles
  incomplets, pas les lignes ERP orphelines : une requête SQL sur Lakebase (`SELECT DISTINCT article_id
  FROM erp_receipts EXCEPT SELECT article_id FROM ref_articles`) les liste.
* **Prix** : `MAX(std_cost_price)` par article ; une devise autre que l'euro ou un prix par unité
  différente de l'unité de stock fausserait la valeur affichée.
* **Première exécution** : `first_seen` est posée à la date de cette exécution pour tous les créneaux
  existants ; le flux *Commandé* de ce jour-là liste donc tout l'en-cours ferme. Il redevient pertinent
  dès le lendemain.
