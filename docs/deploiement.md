# Déploiement et exploitation

## 1. Développement local

```bash
# backend
pip install -r requirements-dev.txt
cp .env.example .env                       # source locale (seed CSV), date de référence figée
cd backend && pytest -q                    # 27 tests (moteur, non-régression Excel, API)
uvicorn appro.api.main:app --reload --port 8000

# frontend (dans un autre terminal)
cd client && npm ci && npm run dev         # http://localhost:5173 (proxy /api → 8000)
npm run build                              # produit client/dist servi par FastAPI sur http://localhost:8000
```

Variables utiles (`backend/appro/config.py`, préfixe `APPRO_`) : `DATA_SOURCE` (`local` | `uc`),
`SEED_DIR`, `AS_OF`, `HORIZON_DAYS`, `HISTORY_DAYS`, `WORKING_WEEKDAYS`, `HOLIDAYS`, `DEFAULT_PLANNER`,
`DB_URL`, `UC_CATALOG`, `UC_SCHEMA`, `CACHE_TTL_SECONDS`, `STATIC_DIR`.

## 2. Préparer Databricks

1. **Unity Catalog** : exécuter `scripts/uc/create_tables.sql` (remplacer `${catalog}` / `${schema}`),
   puis alimenter les tables via les pipelines ERP – ou, pour une démo, `scripts/uc/load_seed.py`
   depuis un notebook / job (`--catalog main --schema appro`).
2. **SQL warehouse** : noter son id (`databricks warehouses list`).
3. **Lakebase** : créer (ou réutiliser) un projet / une branche et une base (`databricks postgres …`) ;
   l'app y crée ses tables `app_*` au premier démarrage. Ajouter `psycopg` est déjà fait dans
   `requirements.txt`. Sans Lakebase, l'app démarre sur SQLite éphémère (perdu au redéploiement) –
   acceptable uniquement pour un test.

## 3. Déployer l'application

### Option A – CLI (rapide)

```bash
scripts/deploy.sh appro /Workspace/Users/<vous>/apps/appro <profil>
```
Le script construit le frontend, téléverse le code (sans `node_modules`) et lance
`databricks apps deploy`. Attacher ensuite les ressources dans l'UI de l'app (SQL warehouse → clé
`sql-warehouse`, Lakebase → clé `database`) si elles ne le sont pas déjà, puis redéployer.

### Option B – Asset Bundle (multi-environnements)

```bash
databricks bundle validate -t dev --var warehouse_id=<id> --var lakebase_branch=<branche>
databricks apps deploy -t dev --var warehouse_id=<id> --var lakebase_branch=<branche>
```
`databricks.yml` déclare les ressources et leurs permissions (`CAN_USE`, `CAN_CONNECT_AND_CREATE`) qui
sont accordées automatiquement au service principal de l'app.

### Vérification

```bash
databricks apps get appro -o json        # app_status.state = RUNNING, url
databricks apps logs appro --follow      # logs [SYSTEM] / [APP]
```
`GET <url>/api/health` doit répondre `{"status":"ok"}` ; `GET <url>/api/config` montre la source
(`unity-catalog`, catalogue, schéma) et la date de référence.

## 4. Points d'attention plateforme

* Port : `start.sh` écoute `0.0.0.0:${DATABRICKS_APP_PORT}` (obligatoire).
* Le frontend est construit avant l'upload (`scripts/build.sh`) ; à défaut `start.sh` le construit au
  démarrage (Node disponible dans le runtime, < 10 min).
* Timeout proxy 120 s par requête : les calculs restent < 1 s pour un portefeuille de quelques centaines
  d'articles ; pour des milliers d'articles, restreindre le périmètre par approvisionneur (déjà le cas) ou
  augmenter `cache_ttl_seconds`.
* Identité : en-têtes `x-forwarded-email` → journal des actions. En local, `x-appro-user` ou `local-dev`.
* Mémoire : l'app charge les tables du périmètre en mémoire (pandas) avec un cache de 5 min ; pour de très
  gros historiques de réceptions / mouvements, filtrer les tables UC par date dans `UnityCatalogSource._load`
  (ex. `receipt_date >= as_of − 90 j`).

## 5. Intégration continue

`.github/workflows/ci.yml` exécute `ruff`, `pytest` (backend) et `npm run build` (frontend) sur chaque push.
