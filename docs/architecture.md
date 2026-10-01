# Architecture

```
┌────────────────────────────── Databricks App « appro-<cible> » ──────────────────────────────┐
│  main.py (racine) : backend/ sur sys.path, uvicorn sur DATABRICKS_APP_PORT                    │
│  client/  (React 18 + TypeScript + Vite)      ──── build ───►  client/dist (statique)          │
│     pages : cockpit, tableau d'appro, fiche article, propositions, saisies & journal,          │
│             référentiel (CRUD + Excel), impact programmes, imports / exports, paramètres        │
│     SimulationGrid : virtualisée (lignes et colonnes), une voie par fournisseur, deux lignes    │
│                      saisies (Plan, Ajustement), deux lignes cliquables (Ferme, CBN), recopie   │
│                                       │ /api (JSON, xlsx)                                       │
│  backend/appro/api  (FastAPI)          ▼                                                        │
│     routers : mrp (cockpit, projection, grille, backlog, propositions), entries (cellules,       │
│               journal), reference (CRUD, modèles, paramètres, config), pdp, files (exports)      │
│  backend/appro/services : context (sources + cache), mrp_service (assemblage → moteur,          │
│               cellules), excel_service (classeur à formules, modèles, réimport)                  │
│  backend/appro/engine  (pur Python + NumPy, sans I/O)                                           │
│     calendar · demand · supply (voies, backlog) · projection · proposals · alerts · programs     │
│  backend/appro/data                                                                             │
│     schemas (canonique + libellés) · erp_sql (correspondance ERP) · sources (csv | uc | lakebase)│
│     store (référentiel, cellules, miroir erp_*, jeton Lakebase) · reference (CRUD, Excel)        │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
          ▲ lecture / écriture (ressource postgres, CAN_CONNECT_AND_CREATE)
   Lakebase  ref_* / fct_stock · app_* · erp_*  ◄──── job appro_sync_erp ◄──── Unity Catalog (commandes_edi, recep_edi)
```

## Principes

* **Deux lignes saisies, rien d'autre.** Le modèle applicatif se réduit à deux tables de cellules (plan par
  fournisseur et jour, ajustement par jour). Aucun objet « commande », « ligne », « statut » n'est stocké :
  l'ERP reste la référence par défaut de chaque cellule vide.
* **Séparation stricte logique / UI** : le moteur (`appro.engine`) ne connaît ni pandas, ni la base, ni
  HTTP. Il reçoit un `Dataset` (records) + `EngineParams` et renvoie un `MrpResult` ; testé unitairement et
  contre les valeurs du classeur historique.
* **Un seul schéma canonique** (`data/schemas.py`) pilote le seed CSV, les modèles ORM du référentiel et du
  miroir, les modèles Excel, les imports et le dictionnaire. **Une seule correspondance ERP**
  (`data/erp_sql.py`) sert au job et à la lecture directe.
* **Le référentiel vit dans l'application** : les tables `ref_*` et le stock de référence sont dans la base
  applicative, éditées à l'écran ou par fichier ; aucune dépendance à une table Unity Catalog de référentiel.
* **Déterminisme et performance** : NumPy de bout en bout (les séries restent des tableaux, jamais des listes),
  calendrier mémoïsé, re-projection seulement pour la politique `lost` ; 500 articles × 1 000 jours ≈ 3 s de
  calcul, résultats en cache par (version des données, périmètre, paramètres), invalidés à chaque écriture.
  Le tableau est servi **par pages** d'articles, agrégé par opérations vectorisées et sérialisé sans
  validation Pydantic (`api/fastjson.py`, `orjson`) : une page de 20 articles sur 1 000 jours ≈ 0,1 s et 2 Mo.
  Côté client la grille ne rend que les cellules visibles (virtualisation lignes + colonnes).
* **Livraison sans aller-retour** : `main.py` racine, `app.yaml` = `config` du bundle (testé), jeton Lakebase
  par connexion, `sync.include` du frontend, vérificateur statique dans la suite de tests.

## Flux d'une page

1. Le client appelle `/api/cockpit`, `/api/grid` ou `/api/articles/{id}/projection` avec le périmètre.
2. `mrp_service.compute` construit les paramètres (défauts ← configuration ← règles globales ← requête),
   assemble le `Dataset` (référentiel depuis la base, faits depuis la source, cellules, PDP actif), exécute
   `run_mrp`.
3. Les présentateurs agrègent (jour / semaine), par voie fournisseur, et sérialisent.
4. Toute écriture journalise dans `app_audit_log` et incrémente la version des données → invalidation du
   cache serveur et des requêtes client.

## Arborescence

```
main.py                    point d'entrée Databricks Apps (et python main.py)
app.yaml · databricks.yml · resources/   manifeste et bundle (App + job)
jobs/                      synchronisation ERP → Lakebase (spark_python_task et notebook), connexion Lakebase
backend/appro/engine/      moteur MRP (docs/regles_metier.md)
backend/appro/data/        schémas canoniques, correspondance ERP, sources, store, référentiel
backend/appro/services/    contexte, service MRP, service Excel
backend/appro/api/         FastAPI : routers, schémas, présentateurs
backend/tests/             pytest : moteur, non-régression classeur, API, formules (LibreOffice), bundle
client/src/                React : pages, composants, graphiques, état, styles (tokens)
data/seed/                 jeu de démonstration (référentiel + faits)
scripts/                   build.sh, deploy.sh / .ps1 / .cmd, extraction du classeur historique
.claude/skills/databricks-livraison/   vérificateur statique du bundle et catalogue des pannes
docs/                      règles métier, dictionnaire, modèle, architecture, déploiement, analyse du classeur
```

## Sécurité et identité

* L'App accède à Lakebase avec son **principal de service** (ressource `postgres`) ; il est propriétaire des
  tables et n'accorde au rôle de synchronisation que l'écriture des tables `erp_*`.
* L'identité de l'utilisateur est lue dans `x-forwarded-email` pour la traçabilité.
* Aucun identifiant n'est codé en dur : `PGHOST`… et `LAKEBASE_ENDPOINT` sont injectés par la plateforme ; le
  jeton est régénéré par l'application.
