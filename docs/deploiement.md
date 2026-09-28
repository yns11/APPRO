# Déploiement sur Databricks Apps – guide complet

Ce guide décrit, dans l'ordre, tout ce qu'il faut préparer pour faire tourner APPRO sur Databricks Apps avec
des données réelles : tables Unity Catalog, base Lakebase, ressources de l'application, déploiement, contrôles
et exploitation. Les commandes utilisent la CLI Databricks (≥ 0.230). Le dictionnaire des données à fournir est
dans [`dictionnaire_donnees.md`](dictionnaire_donnees.md).

## 0. Vue d'ensemble

```
ERP (bronze / silver)  ──jobs SQL──▶  Unity Catalog  <catalog>.<schema>.ref_* / fct_*
                                            │  tables synchronisées (reverse ETL)
                                            ▼
                         Lakebase (PostgreSQL) ── schéma appro_erp (lecture) + tables app_* (écriture)
                                            ▲
                                            │  ressource « database »
                              Databricks App « appro » (FastAPI + React)
```

Choix retenu (voir dictionnaire § 3) : **Lakebase** porte la base applicative et, via des tables synchronisées,
la copie de lecture des tables ERP. Variante : lecture directe dans Unity Catalog par un SQL warehouse
(`APPRO_DATA_SOURCE=uc`), sans tables synchronisées.

## 1. Prérequis

* Espace de travail avec **Databricks Apps** et **Lakebase** activés ; droits : créer une app, créer un projet
  Lakebase, créer des tables synchronisées, `USE CATALOG` / `USE SCHEMA` / `SELECT` sur le schéma ERP.
* CLI Databricks configurée (`databricks auth login --host https://<workspace>`), Python 3.11 et Node 20 en local
  pour construire le frontend.
* Le dépôt cloné ; `scripts/build.sh` construit `client/dist` (obligatoire avant l'envoi du code).

## 2. Préparer les tables Unity Catalog

1. Choisir le catalogue et le schéma cibles, par exemple `emotors_data_champions.appro`.
2. Créer les tables canoniques :
   ```sql
   -- scripts/uc/create_tables.sql, en remplaçant ${catalog} / ${schema} (et ${silver_schema} pour la vue)
   ```
   Le script crée `ref_articles`, `ref_suppliers`, `ref_article_suppliers`, `ref_programs`, `ref_bom`,
   `fct_production_plan`, `fct_production_actual`, `fct_purchase_orders`, `fct_receipts`, `fct_stock_movements`,
   `fct_stock`, et la vue `v_fct_purchase_orders_from_commandes_edi` qui traduit l'extraction `commandes_edi`.
3. Écrire les **jobs d'alimentation** (Lakeflow Jobs, SQL) : un job par table ou un job multi-tâches, avec
   `CREATE OR REPLACE TABLE … AS SELECT …` ou `MERGE`. Cadence conseillée : toutes les 2 h pour
   `fct_purchase_orders`, `fct_receipts`, `fct_stock`, `fct_production_actual` ; quotidienne pour les `ref_*`.
   Pour les commandes, le job peut être `CREATE OR REPLACE TABLE fct_purchase_orders AS SELECT * FROM
   v_fct_purchase_orders_from_commandes_edi` (les tables synchronisées ne lisent pas les vues).
4. Contrôles de qualité à mettre dans le job : unicité de `order_id`, `article_id` des faits présents dans
   `ref_articles`, `planner` renseigné, un seul snapshot par article et par jour, `delivery_weekdays` non vide.
5. Pour une démonstration sans ERP : `python scripts/uc/load_seed.py --catalog <c> --schema <s>` depuis un
   notebook charge le jeu de démonstration.

## 3. Créer la base Lakebase

1. Créer un projet Lakebase (autoscaling) et une branche `main` ; noter le nom d'instance / branche et la base
   (`databricks_postgres` par défaut) :
   ```bash
   databricks postgres create-project appro-db   # ou depuis l'UI Compute > Lakebase
   ```
2. Créer le schéma de lecture et les **tables synchronisées** depuis Unity Catalog, une par table canonique,
   dans le schéma `appro_erp` (mode *Snapshot* ou *Triggered* selon la cadence des jobs, *Continuous* inutile) :
   ```bash
   databricks database create-synced-table \
     --name <catalog>.<schema>.ref_articles_synced \
     --spec '{"source_table_full_name": "<catalog>.<schema>.ref_articles", "primary_key_columns": ["article_id"],
              "scheduling_policy": "TRIGGERED", "new_pipeline_spec": {"storage_catalog": "<catalog>", "storage_schema": "<schema>"}}' \
     --database-instance-name <instance> --logical-database-name databricks_postgres
   ```
   Répéter pour chaque table avec ses clés (voir dictionnaire : `supplier_id` ; `article_id, supplier_id` ;
   `program_id` ; `program_id, article_id` ; `program_id, week_start, version` ; `program_id, date` ;
   `order_id, line_no` ; `receipt_id` ; `movement_id` ; `article_id, snapshot_date, location`). Dans Postgres,
   les tables synchronisées portent le nom de la table UC dans un schéma du même nom que le schéma UC ; créer
   ensuite des vues dans `appro_erp` si les noms diffèrent, ou renseigner `APPRO_LAKEBASE_SCHEMA` avec le
   schéma réel. L'application ne lit que les colonnes canoniques.
3. Les tables `app_*` sont créées par l'application au premier démarrage dans le schéma `public` ; aucun script.
4. Sans Lakebase : passer `APPRO_DATA_SOURCE=uc`, attacher un SQL warehouse (§ 4) ; les saisies iraient alors
   dans un SQLite éphémère, perdu à chaque redéploiement, ce qui n'est acceptable que pour un test.

## 4. Créer l'application et ses ressources

1. Construire le frontend et envoyer le code :
   ```bash
   scripts/build.sh
   databricks apps create appro
   databricks workspace mkdirs /Workspace/Users/<vous>/apps/appro
   databricks workspace import-dir . /Workspace/Users/<vous>/apps/appro --overwrite   # exclure node_modules, .git, data/local
   ```
   (ou `scripts/deploy.sh appro /Workspace/Users/<vous>/apps/appro <profil>` qui enchaîne ces étapes).
2. Dans l'UI de l'app (Compute > Apps > appro > Edit), attacher les **ressources** :
   * `database` → la base Lakebase (permission *Can connect and create*) : injecte `PGHOST`, `PGDATABASE`,
     `PGUSER` (le principal de service de l'app) et le jeton ;
   * facultatif `sql-warehouse` → un SQL warehouse (*Can use*) si `APPRO_DATA_SOURCE=uc`.
3. Vérifier `app.yaml` (variables d'environnement) :

   | Variable | Valeur | Rôle |
   |---|---|---|
   | `APPRO_DATA_SOURCE` | `lakebase` (ou `uc`) | source des tables ERP |
   | `APPRO_LAKEBASE_SCHEMA` | `appro_erp` | schéma Postgres des tables synchronisées |
   | `APPRO_UC_CATALOG` / `APPRO_UC_SCHEMA` | catalogue / schéma UC | mode `uc` |
   | `DATABRICKS_WAREHOUSE_ID` | `valueFrom: sql-warehouse` | mode `uc` |
   | `PGHOST` | `valueFrom: database` | base applicative (et lecture en mode `lakebase`) |
   | `APPRO_HORIZON_DAYS` / `APPRO_HISTORY_DAYS` | 120 / 14 | fenêtre de projection |
   | `APPRO_HOLIDAYS` | `2026-12-25,2027-01-01` | fermetures |
   | `APPRO_DEFAULT_PLANNER` | code appro | périmètre par défaut (facultatif) |

4. Déployer et démarrer :
   ```bash
   databricks apps deploy appro --source-code-path /Workspace/Users/<vous>/apps/appro
   databricks apps get appro -o json | jq '.app_status, .url'
   databricks apps logs appro --follow
   ```
5. Droits du **principal de service** de l'app (affiché dans la page de l'app) : `USE CATALOG`, `USE SCHEMA`,
   `SELECT` sur le schéma UC en mode `uc` ; rôle Postgres avec `CREATE` sur `public` et `SELECT` sur `appro_erp`
   en mode `lakebase` (la ressource *Can connect and create* le fait).
6. Donner accès aux approvisionneurs : permissions *Can use* sur l'app (utilisateurs ou groupe). L'identité arrive
   à l'application par l'en-tête `x-forwarded-email` et alimente le journal.

Variante Asset Bundle : `databricks.yml` déclare l'app et ses ressources ; `databricks bundle validate -t dev`
puis `databricks apps deploy -t dev --var warehouse_id=… --var lakebase_branch=…`.

## 5. Vérifications après déploiement

| Contrôle | Attendu |
|---|---|
| `GET <url>/api/health` | `{"status": "ok"}` |
| `GET <url>/api/config` | `data_source.name` = `lakebase` ou `unity-catalog`, `as_of` = date du jour, `planners` non vide |
| Cockpit | nombre d'articles du périmètre, date de référence = aujourd'hui, stock de référence = stock ERP de la veille |
| Fiche article | ligne Ferme avec les commandes ERP, Consommé alimenté par la production réelle |
| Saisie d'une ligne du plan | présente après rechargement de la page (persistance Lakebase) |
| Journal | l'utilisateur est l'e-mail Databricks |

## 6. Exploitation

* **Cadence** : la date de référence est le jour courant ; le stock lu est celui de la veille au soir. Programmer
  les jobs ERP avant l'arrivée des approvisionneurs (ex. 6 h) puis toutes les 2 h ; le cache de l'application
  (`APPRO_CACHE_TTL_SECONDS`, 300 s) et le bouton *Rafraîchir* de l'en-tête rechargent les tables.
* **PDP** : import par fichier (modèle téléchargeable dans *Imports / exports*), le jeudi ou le vendredi ; la
  version activée remplace le PDP ERP pour ses programmes.
* **Sauvegarde** : Lakebase est sauvegardé par la plateforme (point-in-time restore) ; exporter en plus le plan
  et les saisies par l'export Excel si besoin d'archive.
* **Mises à jour** : `scripts/build.sh` puis `databricks apps deploy` ; le schéma applicatif évolue par
  `create_all` (ajout de tables / colonnes nullables) ; pour une migration destructive, prévoir un script SQL.
* **Diagnostic** : `databricks apps logs appro`, page *Données de base* de la fiche article (diagnostics du
  calcul), `GET /api/params/effective`.
* **Limites** : timeout du proxy 120 s par requête ; le calcul reste inférieur à la seconde pour quelques
  centaines d'articles par périmètre. Pour des milliers d'articles, restreindre le périmètre par approvisionneur
  ou limiter l'historique des réceptions lues (filtre de date dans le job d'alimentation).

## 7. Développement local

```bash
pip install -r requirements-dev.txt
cp .env.example .env                      # source locale (seed), date de référence figée
cd backend && pytest -q                   # moteur, non-régression, API, formules Excel (LibreOffice)
uvicorn appro.api.main:app --reload --port 8000
cd client && npm ci && npm run dev        # http://localhost:5173 (proxy /api → 8000)
```

Pour tester contre Lakebase depuis un poste : `APPRO_DB_URL=postgresql+psycopg://<user>:<token>@<host>:5432/databricks_postgres?sslmode=require`
et `APPRO_DATA_SOURCE=lakebase`.
