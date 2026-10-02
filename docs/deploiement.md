# Déploiement sur Databricks — guide pas à pas

Ce guide conduit un déploiement **complet et du premier coup** d'APPRO sur Databricks : Lakebase, App,
job de synchronisation, référentiel, contrôles. Chaque étape dit **où** elle se fait (terminal ou
interface Databricks) et **comment vérifier** qu'elle a réussi. Les pièges rencontrés sur les applications
précédentes (Backflush, Campagnes inventaire) sont intégrés dans le code, le bundle et le vérificateur
statique (`.claude/skills/databricks-livraison`).

Architecture déployée :

```
 ERP (bronze) ──job Genie « commandes_edi & recep_edi » (horaire)──▶ Unity Catalog  silver_erp_ye.commandes_edi / recep_edi
                                                                             │
                                                     job « appro_sync_erp » (bundle, horaire, H:20)
                                                                             ▼
                        Lakebase  projet « appro »  ─── tables erp_* (miroir des faits, lues par l'App)
                                                    ─── tables ref_* / fct_stock (référentiel, géré dans l'App)
                                                    ─── tables app_* (plan, ajustements, PDP, paramètres, journal)
                                                                             ▲
                                                     Databricks App « appro-<cible> » (FastAPI + React, main.py)
```

Le référentiel (articles, fournisseurs, règles article ↔ fournisseur, programmes, nomenclatures, stock de
référence) **n'est pas lu de l'ERP** : il se charge dans l'application, par fichier Excel (modèle par
table) ou ligne par ligne. Seules les commandes et les réceptions (et, si vous les configurez, la
production réelle et le PDP) viennent des extractions ERP.

---

## 0. Prérequis

| Élément | Détail | Vérification |
|---|---|---|
| CLI Databricks | ≥ 0.294 (ou 1.15+ ; **pas 1.14.1**, qui ne sait pas déployer une App) | `databricks --version` |
| Python | 3.11 | `python3 --version` |
| Node.js | ≥ 20 (compilation du frontend) | `node --version` |
| Git | le dépôt cloné | `git status` |
| Espace de travail | **Databricks Apps** et **Lakebase** activés ; droit de créer une App et un projet Lakebase | onglets *Compute → Apps* et *Compute → Lakebase* visibles |
| ERP | les tables `emotors_data_champions.silver_erp_ye.commandes_edi` et `recep_edi` existent et sont alimentées par le job horaire (notebook « commandes_edi & recep_edi ») | `SELECT COUNT(*) FROM emotors_data_champions.silver_erp_ye.commandes_edi` dans SQL Editor |
| Droits sur l'ERP | `USE CATALOG`, `USE SCHEMA`, `SELECT` sur ces deux tables **pour l'identité qui exécutera le job de synchronisation** (vous, ou un principal de service) | la requête ci-dessus répond |

Installation de la CLI :

```bash
# macOS / Linux
curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh
# Windows
winget install Databricks.DatabricksCLI
```

Authentification (OAuth, nécessaire pour `databricks apps logs`) :

```bash
databricks auth login --host https://<votre-workspace>.cloud.databricks.com --profile PROD
databricks auth profiles
databricks current-user me --profile PROD
```

Contrôle statique du bundle, à lancer avant **chaque** déploiement (lecture seule, moins d'une seconde) :

```bash
python3 .claude/skills/databricks-livraison/verifier_bundle.py .
```

Il est aussi exécuté par la suite de tests (`backend/tests/test_bundle.py`) et par `scripts/deploy.sh`.

---

## 1. Créer le projet Lakebase

**Terminal**

```bash
databricks postgres list-projects --profile PROD                      # réutiliser un projet si possible
databricks postgres create-project appro \
    --json '{"spec": {"display_name": "APPRO"}}' --profile PROD
```

**Interface** : *Compute → Lakebase → Create project*, nom `appro`. Un projet neuf provisionne
automatiquement la branche `production`, l'endpoint `primary` et la base `databricks_postgres`.

Relever les **quatre identifiants** attendus par `databricks.yml` (chemins de ressource complets, jamais
des noms courts) :

```bash
databricks postgres list-branches  projects/appro --profile PROD
databricks postgres list-databases projects/appro/branches/production --profile PROD
databricks postgres list-endpoints projects/appro/branches/production --profile PROD
```

| Variable du bundle | Valeur (projet créé avec les défauts) | D'où elle vient |
|---|---|---|
| `lakebase_project` | `appro` | 2e élément du fil d'Ariane de la console |
| `lakebase_branch` | `production` | 3e élément |
| `lakebase_database` | `databricks-postgres` | dernier segment du `name` de `list-databases` (RFC 1123 : **tiret**, pas souligné) |
| `lakebase_endpoint` | `primary` | dernier segment du `name` de `list-endpoints` |

> Le nom PostgreSQL de la base (`databricks_postgres`) n'a pas à être déclaré : la plateforme l'injecte
> dans l'App (`PGDATABASE`). Ne recopiez pas le libellé du sélecteur de la console pour l'identifiant.

Si les valeurs diffèrent des défauts, les écrire dans un fichier d'override par cible (lu par toutes les
commandes du bundle, jamais committé) :

```bash
mkdir -p .databricks/bundle/dev
cat > .databricks/bundle/dev/variable-overrides.json <<'JSON'
{ "lakebase_project": "appro", "lakebase_branch": "production", "lakebase_database": "databricks-postgres", "lakebase_endpoint": "primary" }
JSON
```

Dimensionnement : APPRO écrit peu (quelques cellules par clic) et lit des tables de quelques milliers de
lignes. Un endpoint avec un plancher bas et une suspension courte suffit ; le réveil après mise à l'échelle
zéro est absorbé par le pré-ping du pool de connexions.

---

## 2. Compiler le frontend et déployer l'App (cible `dev`)

**Terminal**, depuis la racine du dépôt :

```bash
scripts/deploy.sh dev PROD --var="sync_role=prenom.nom@exemple.com"
```

Le script enchaîne, dans cet ordre : vérificateur statique → `scripts/build.sh` si `client/dist` est absent
ou périmé → `databricks bundle validate` → `databricks bundle deploy` → `databricks bundle run appro`
(démarrage de l'App) → affichage de l'état et de l'URL.

`sync_role` est l'identité qui exécutera le job de synchronisation (votre e-mail Databricks pour commencer ;
un principal de service plus tard). L'App lui accorde à son démarrage le droit d'écrire les tables `erp_*`.
Mieux vaut l'inscrire dans le bloc `targets.dev.variables` de `databricks.yml` que de le répéter à chaque
commande.

Sous Windows : `scripts\deploy.cmd dev PROD --var="sync_role=..."` (ou `deploy.ps1`).

Sans script (procédure manuelle, même ordre) :

```bash
python3 .claude/skills/databricks-livraison/verifier_bundle.py .
scripts/build.sh                                   # ou : cd client && npm ci && npm run build && cd ..
databricks bundle validate -t dev --profile PROD --var="sync_role=prenom.nom@exemple.com"
databricks bundle deploy   -t dev --profile PROD --var="sync_role=prenom.nom@exemple.com"
databricks bundle run appro -t dev --profile PROD --var="sync_role=prenom.nom@exemple.com"
```

> `databricks bundle deploy` seul téléverse le code et met à jour les définitions mais **laisse l'App
> arrêtée** : `bundle run appro` (ou *Start* dans l'interface) est indispensable. Un `--var` passé à
> `validate` doit l'être à l'identique à `deploy` et à `run`.

**Vérifier** :

```bash
databricks apps get appro-dev --profile PROD -o json | jq '{state: .app_status.state, url: .url, sp: .service_principal_client_id}'
databricks apps logs appro-dev --follow --profile PROD          # OAuth requis
URL=$(databricks apps get appro-dev --profile PROD -o json | jq -r .url)
curl -s "$URL/api/health" | jq
```

Attendu dans `/api/health` : `"status": "ok"`, `"database_status": "ok"`, `"database": "lakebase"`,
`lakebase_env.present` contenant `PGHOST`, `PGDATABASE`, `PGUSER` **et** `LAKEBASE_ENDPOINT`,
`"frontend_built": true`, `"reference_rows": 0` (référentiel encore vide).

Le premier démarrage crée le schéma **`appro`** (variable `app_schema`) puis toutes les tables dedans
(référentiel, `app_*`, `erp_*`, `erp_sync_log`) et exécute le `GRANT` d'écriture sur `erp_*` au profit de
`sync_role`. Le rôle d'une App n'a **pas** le droit `CREATE` sur le schéma `public` d'un projet Lakebase
(`permission denied for schema public`) ; `CAN_CONNECT_AND_CREATE` lui permet en revanche de créer son
propre schéma. Dans l'éditeur SQL Lakebase, préfixer les tables (`appro.ref_articles`) ou exécuter
`SET search_path TO appro;` en tête de requête.

Si `/api/health` répond `ne peut pas créer le schéma « appro »`, le rôle de l'App n'a même pas `CREATE` sur
la base : avec votre rôle (propriétaire du projet), dans l'éditeur SQL, `GRANT CREATE ON DATABASE
databricks_postgres TO "<client_id de l'App>";` (le `client_id` figure dans le message et dans
*Compute → Apps → appro-dev → Authorization*), puis redémarrer l'App.

**Interface** : *Compute → Apps → appro-dev* montre l'état, l'URL, les journaux (*Logs*), les ressources
(*Resources* : `postgres` attaché) et l'environnement (*Environment* : les variables ci-dessus).

---

## 3. Donner l'accès à Lakebase à l'identité de synchronisation

Le job (ou le notebook) tourne sous une identité qui n'est pas celle de l'App. Elle doit avoir un **rôle
Postgres** dans le projet Lakebase, sinon la connexion échoue avec `FATAL: role "…" does not exist`.

**Interface** : *Compute → Lakebase → appro → branche production → Roles → Add role* : sélectionner
l'utilisateur (ou le principal de service) `sync_role`. Aucun droit particulier n'est à cocher : l'App lui
a déjà accordé l'écriture sur ses tables `erp_*` (et rien d'autre).

**Terminal** (contrôle, avec un rôle propriétaire) :

```sql
SELECT table_name, string_agg(privilege_type, ', ' ORDER BY privilege_type) AS droits
FROM information_schema.role_table_grants
WHERE grantee = 'prenom.nom@exemple.com' AND table_schema = 'appro' GROUP BY table_name ORDER BY table_name;
-- attendu : erp_production_actual, erp_production_plan, erp_purchase_orders, erp_receipts, erp_sync_log : DELETE, INSERT, SELECT, TRUNCATE, UPDATE
-- vide ? vérifier d'abord que les tables existent : SELECT tablename, tableowner FROM pg_tables WHERE schemaname = 'appro';
-- puis que le rôle existe :                     SELECT rolname FROM pg_roles WHERE rolname = 'prenom.nom@exemple.com';
```

Si le `GRANT` a été oublié (App démarrée avant que `sync_role` ne soit renseigné) : renseigner la variable,
redéployer (`scripts/deploy.sh dev PROD --var=...`) ; l'App rejoue le `GRANT` à chaque démarrage.

---

## 4. Charger le référentiel dans l'application

**Interface de l'App** (`<URL>/referentiel`), pour chaque onglet, dans cet ordre :

1. **Fournisseurs** — bouton *Modèle* : télécharge un classeur minimaliste (en-têtes en français, une
   ligne d'exemple, onglet NOTICE décrivant chaque colonne). Le remplir, puis *Importer un fichier* en mode
   *remplacer la table*.
2. **Articles** — colonnes : Article, Désignation, Unité, Famille, Approvisionneur (= périmètre),
   Couverture cible, Seuil rouge, Seuil orange, Surstock, Stock de sécurité, Cycle de commande, Actif.
3. **Article ↔ fournisseur** — MOQ, PLA (conditionnement), délai en jours ouvrés, quota %, priorité.
4. **Programmes** — le nom doit être celui utilisé dans le fichier PDP.
5. **Nomenclatures** — programme, composant, quantité par unité, unité, rebut %.
6. **Approvisionneurs** — ID, prénom (= colonne *Approvisionneur* des articles), e-mail Databricks, rôle
   (`appro`, `manager`, `admin`), actif. **Commencer par soi-même avec le rôle `admin`** : tant que la table
   est vide tout le monde est administrateur, dès la première ligne les autres utilisateurs passent en lecture
   seule (règles métier § 12). Les **Délégations** (délégant, destinataire, dates) se saisissent ensuite.
6. **Stock de référence** — article, date du stock (veille au soir), stock physique, stock bloqué.

Chaque ligne est aussi modifiable à la souris (clic sur la ligne) ou ajoutable (*Ajouter*). *Exporter*
donne le contenu courant dans le même format que le modèle : c'est la sauvegarde du référentiel.

**Vérifier** : `curl -s "$URL/api/config" | jq .planners` liste les approvisionneurs ; le cockpit affiche
les articles du périmètre (sans commandes tant que le job n'a pas tourné).

---

## 5. Première synchronisation des faits ERP

Deux voies équivalentes ; la première est celle du bundle.

### 5.1 Par le job du bundle

```bash
databricks bundle run appro_sync_erp -t dev --profile PROD
```

Le job lit `commandes_edi` et `recep_edi` avec le SQL de correspondance de
`backend/appro/data/erp_sql.py`, remplace `erp_purchase_orders` et `erp_receipts` dans une transaction par
table, puis écrit `erp_sync_log`. Environnement serverless, dépendance `psycopg` ; l'hôte et le jeton
Lakebase sont déduits de `--endpoint` / `--branch` (appels REST directs si le SDK du runtime est ancien).

**Vérifier** : la tâche est verte, et

```bash
curl -s "$URL/api/health" | jq '.source.sync'
# [{"table_name":"erp_purchase_orders","row_count":…,"synced_at":"…"}, {"table_name":"erp_receipts",…}]
```

### 5.2 Par le notebook (sans bundle, depuis l'interface)

*Workspace → Import → File* : `jobs/sync_erp_to_lakebase_notebook.py`. Renseigner les widgets
(`lakebase_endpoint` = `projects/appro/branches/production/endpoints/primary`, catalogue / schéma ERP),
*Run all*. Puis *Schedule* : toutes les heures, de 5 h à 20 h, du lundi au vendredi, à H:20 (après le
job horaire de l'ERP). Le notebook obtient son jeton du contexte de session, indépendamment de la version
du SDK.

### 5.3 Cadence

Le job du bundle est planifié `0 20 5-20 ? * MON-FRI` (Europe/Paris). En cible `dev` la planification est
**suspendue** (`schedule_pause_status: PAUSED`) ; en `preprod` / `prod` elle est active. Vérifier avant
de conclure qu'un job « ne tourne pas » :

```bash
databricks bundle validate -t prod -o json --profile PROD | jq '.resources.jobs.appro_sync_erp.schedule'
```

---

## 6. Recette

| Contrôle | Où | Attendu |
|---|---|---|
| `/api/health` | terminal | `status ok`, `database_status ok`, `reference_rows` > 0, `source.sync` renseigné |
| Cockpit | App | date de référence = aujourd'hui ; KPI et portefeuille du périmètre |
| Fiche article | App | ligne Ferme avec les commandes ERP, Reçu avec les réceptions, Scenario ERP = Scenario Plan |
| Saisie du plan | App | taper une quantité dans une cellule Plan → cellule bleue, Scenario Plan recalculé ; recharger la page : la valeur est conservée (persistance Lakebase) |
| Journal | App, *Saisies & journal* | l'utilisateur est votre e-mail Databricks |
| Export / réimport | App, *Imports / exports* | le classeur exporté se rouvre ; ses lignes Plan modifiées reviennent dans l'App |
| Job | *Workflows* | `[dev] Ma Routine Appro — synchronisation ERP → Lakebase` vert ; `erp_sync_log` à jour |

Ouvrir l'App aux approvisionneurs : *Compute → Apps → appro-dev → Permissions → Can use* (utilisateurs ou
groupe). Leur identité arrive à l'application par l'en-tête `x-forwarded-email`.

---

## 7. Ce que le code fait pour éviter les pannes connues

| Piège (observé sur Backflush / Campagnes inventaire) | Parade dans APPRO |
|---|---|
| `ModuleNotFoundError` en boucle : le conteneur reçoit le *contenu* du dossier source, le paquet racine n'existe plus | `main.py` à la racine met `backend/` sur `sys.path` ; `app.yaml` lance `python main.py` |
| 503 sur toutes les routes : `LAKEBASE_ENDPOINT` n'est pas injecté par la ressource `postgres` | réclamé par `valueFrom: postgres` dans `app.yaml` et le bundle ; repli par `APPRO_LAKEBASE_BRANCH` (recherche de l'endpoint par `PGHOST`) ; `/api/health` liste les variables présentes / absentes |
| `Database instance … does not exist` (404) | clé `postgres` avec `branch` / `database` en chemins complets, jamais la clé `database` |
| `OAuth: User is not authorized` après une heure | jeton généré **à chaque connexion physique** (événement `do_connect` de SQLAlchemy), régénéré après 30 min, invalidé sur refus ; `pool_recycle` 25 min |
| Interface absente, API fonctionnelle | `client/dist` ré-inclus par `sync.include` ; `scripts/deploy.sh` compile avant de déployer |
| Un réglage figé dans `app.yaml` écrase le défaut du code | `test_bundle.py` compare les deux ; `app.yaml` et le `config` du bundle sont comparés entre eux |
| Variable vide refusée au démarrage | toute variable vide vaut « absente » (`config.py`) |
| Job : `SystemExit` fait échouer une tâche réussie ; `sys.path` sans la racine | `main()` retourne ; amorce de `sys.path` en tête du script |
| Job : le SDK du runtime serverless ne connaît pas `w.postgres` | appels REST directs (`/api/2.0/postgres/…`) via `api_client.do` |
| Job : `permission denied` sur les tables de l'App | l'App accorde elle-même l'écriture sur `erp_*` (et `USAGE` sur son schéma) au `sync_role` ; le job vérifie la présence des tables et nomme la cause |
| `permission denied for schema public` au premier `CREATE TABLE` (rôle de l'App sans `CREATE` sur `public`) | l'App crée et possède son propre schéma `appro` (`APPRO_DB_SCHEMA`), placé en tête du `search_path` de chaque connexion ; le job écrit dans ce schéma (`--pg-schema`) |
| `Scheduled — Paused` sans qu'aucune commande n'échoue | `pause_status` posé sur la ressource, déclaré par chaque cible, avec `timezone_id` |
| `permission denied for schema public` **en prod alors que dev fonctionne** : les deux cibles partagent le projet Lakebase ; le schéma `appro` a été créé par l'App de dev (son principal de service), l'App de prod ne peut pas y créer de tables et PostgreSQL retombe sur `public` | un schéma par cible (`app_schema` : `appro` en dev, `appro_preprod`, `appro_prod`) ; au démarrage l'App vérifie `has_schema_privilege(schema, 'CREATE')` et nomme la cause (schéma possédé par un autre rôle) au lieu de laisser PostgreSQL retomber sur `public` |
| Le tableau ne se rafraîchit pas après une saisie (il faut Ctrl+Maj+R) : l'App tourne avec **plusieurs workers** uvicorn (`APPRO_WORKERS`, 2 par défaut), chacun avec son cache mémoire ; l'écriture servie par l'un n'invalidait pas le cache de l'autre | compteur `data_version` dans la table `app_meta`, incrémenté à chaque écriture et relu à chaque calcul : tout worker jette son cache dès qu'un autre a écrit ; réponses `/api/*` en `Cache-Control: no-store` |

---

## 8. Passage en préproduction / production

```bash
scripts/deploy.sh preprod PROD --var="sync_role=<client_id du principal de service>"
databricks bundle run appro_sync_erp -t preprod --profile PROD
```

Avant `prod`, dans `databricks.yml` :

- [ ] `targets.prod.variables.sync_role` = identité de production (principal de service recommandé, rôle ajouté au projet Lakebase, `SELECT` sur les tables ERP) ;
- [ ] `notification_email` renseigné : un job qui échoue en silence n'est vu de personne ;
- [ ] `schedule_pause_status: UNPAUSED` (déjà posé) ; `app_schema` propre à la cible (déjà posé : `appro_prod`) et, de préférence, `lakebase_branch` distinct de `dev` ;
- [ ] référentiel chargé (l'export de `dev` se réimporte tel quel en `prod` : les tables, dont *Approvisionneurs* et *Délégations*, sont dans le schéma de la cible, pas partagées) ;
- [ ] premier utilisateur déclaré en `admin` dans *Approvisionneurs* (tant que la table est vide, tout le monde est administrateur) ;
- [ ] `/api/health` vert, une saisie test persistante, journal nominatif ;
- [ ] permissions *Can use* accordées au groupe des approvisionneurs.

Mises à jour ultérieures : `scripts/deploy.sh <cible> PROD` (recompile si le frontend a changé, redéploie,
redémarre). Le schéma applicatif évolue par `create_all` (tables et colonnes ajoutées) ; une migration
destructive se fait par un script SQL sur Lakebase. Lakebase est sauvegardé par la plateforme ; exporter en
plus le référentiel (page Référentiel, *Exporter*) après chaque modification importante.

---

## 9. Variante : lecture directe par SQL warehouse (sans miroir)

Si le job de synchronisation n'est pas souhaité, l'App peut lire les extractions en direct :

1. attacher une ressource `sql-warehouse` (permission *Can use*) à l'App — dans `resources/appro_app.yml` :
   ```yaml
        - name: sql-warehouse
          sql_warehouse:
            id: ${var.warehouse_id}
            permission: CAN_USE
   ```
   et dans les deux blocs `env` : `APPRO_DATA_SOURCE=uc` et `DATABRICKS_WAREHOUSE_ID` (`value_from: sql-warehouse` /
   `valueFrom: sql-warehouse`) ;
2. donner au **principal de service de l'App** (`service_principal_client_id` de `apps get`) les droits
   `USE CATALOG`, `USE SCHEMA` et `SELECT` sur `commandes_edi` et `recep_edi` :
   ```sql
   GRANT USE CATALOG ON CATALOG emotors_data_champions TO `<client_id>`;
   GRANT USE SCHEMA ON SCHEMA emotors_data_champions.silver_erp_ye TO `<client_id>`;
   GRANT SELECT ON TABLE emotors_data_champions.silver_erp_ye.commandes_edi TO `<client_id>`;
   GRANT SELECT ON TABLE emotors_data_champions.silver_erp_ye.recep_edi TO `<client_id>`;
   ```
   Seul un propriétaire du catalogue peut les passer : c'est la raison d'être du miroir Lakebase, qui n'exige
   aucun droit pour l'App.

Le même SQL de correspondance est exécuté sur le warehouse ; la base Lakebase reste nécessaire pour le
référentiel et les saisies.

---

## 10. Diagnostic

| Symptôme | Cause probable | Correction |
|---|---|---|
| `App Not Available`, `ModuleNotFoundError: No module named 'appro'` | `main.py` absent de la racine déployée, ou commande différente de `python main.py` | vérifier `app.yaml` ; `bundle sync --dry-run` |
| `/api/health` : `database_status` en erreur, `lakebase_env.absent` contient `LAKEBASE_ENDPOINT` et `PGHOST` | ressource `postgres` non attachée, ou déploiement antérieur à l'attachement | `apps get … -o json \| jq .resources` ; redéployer (`bundle run appro`) : les variables sont injectées à la création du déploiement |
| `absent` contient seulement `LAKEBASE_ENDPOINT` | `valueFrom: postgres` manquant dans le manifeste | rétablir (le test `test_bundle.py` l'impose), redéployer |
| `/api/health` : `permission denied for schema public` | version antérieure au schéma `appro`, ou `APPRO_DB_SCHEMA` forcé à `public` | laisser `app_schema` = `appro` ; redéployer |
| `/api/health` : `ne peut pas créer le schéma « appro »` | le rôle de l'App n'a pas `CREATE` sur la base | `GRANT CREATE ON DATABASE databricks_postgres TO "<client_id>"` avec un rôle propriétaire, redémarrer (§ 2) |
| `permission denied for table erp_purchase_orders` dans le job | `sync_role` non renseigné au démarrage de l'App, ou rôle absent du projet | § 3, redéployer l'App, relancer le job |
| `FATAL: role "…" does not exist` dans le job | l'identité du job n'a pas de rôle dans le projet Lakebase | § 3 |
| `Tables cibles absentes de Lakebase` dans le job | l'App n'a jamais démarré sur cette base | § 2 |
| `Tables source introuvables` dans le job | catalogue / schéma faux, ou pas de `SELECT` pour l'identité du job | corriger `uc_catalog` / `uc_schema`, droits UC |
| Le cockpit est vide | référentiel non chargé | § 4 (`reference_rows` dans `/api/health`) |
| Ferme / Reçu vides alors que le job est vert | `planner` des articles ne correspond à aucun périmètre, ou identifiants article différents entre référentiel et ERP | comparer `article_id` du référentiel et `Article` de `commandes_edi` |
| Interface absente, `/api/docs` répond | frontend non compilé avant le déploiement | `scripts/deploy.sh` (recompile) |
| Job « Scheduled — Paused » | cible en mode `development` ou variable `PAUSED` | § 5.3 |
| `Invalid update mask … forward_user_access_token` au `apps deploy` | CLI 1.14.1 | passer en 1.15+ |
| `unknown field: postgres` au `bundle validate` | CLI antérieure à 0.294 | mettre la CLI à jour |

---

## 11. Développement local

```bash
pip install -r requirements-dev.txt
cp .env.example .env                      # source locale (seed), date de référence figée, référentiel chargé du seed
cd backend && pytest -q                   # moteur, non-régression, API, classeur (LibreOffice), bundle
uvicorn appro.api.main:app --reload --port 8000
cd client && npm ci && npm run dev        # http://localhost:5173 (proxy /api → 8000)
```

Contre Lakebase depuis un poste (jeton d'une heure) :

```bash
export APPRO_DB_URL="postgresql+psycopg://<user>:$(databricks postgres generate-database-credential --json '{"endpoint":"projects/appro/branches/production/endpoints/primary"}' -o json | jq -r .token)@<host>:5432/databricks_postgres?sslmode=require"
export APPRO_DATA_SOURCE=lakebase APPRO_SEED_REFERENCE=false
```
