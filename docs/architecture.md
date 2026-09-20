# Architecture

```
┌────────────────────────────── Databricks App « appro » ──────────────────────────────┐
│  client/  (React 18 + TypeScript + Vite)      ──── build ───►  client/dist (statique)  │
│     pages : cockpit, fiche article, calcul CBN, scénarios, saisies, imports, référentiel, paramètres │
│     state : PerimeterContext (appro, scénario, horizon, granularité, thème)             │
│     lib   : api.ts (fetch typé), queries.ts (TanStack Query), format.ts                 │
│                                       │ /api (JSON, xlsx)                               │
│  backend/appro/api  (FastAPI, uvicorn) ▼                                                │
│     routers : mrp, entries, proposals, scenarios, pdp, files, reference(+params, audit)  │
│     presenters : résultats moteur → schémas Pydantic                                    │
│  backend/appro/services                                                                 │
│     mrp_service : assemblage (ERP + saisies + surcharges + PDP actif + scénario) → moteur, cache │
│     excel_service : export xlsx à formules / import (openpyxl)                            │
│  backend/appro/engine  (pur Python + NumPy, sans I/O)                                   │
│     calendar · demand · projection · proposals · alerts · scenario · runner             │
│  backend/appro/data                                                                     │
│     sources  : LocalCsvSource (data/seed) │ UnityCatalogSource (SQL warehouse)          │
│     store    : SQLAlchemy (SQLite en local │ Lakebase PostgreSQL sur Databricks)         │
│     assembler: frames canoniques → records moteur                                        │
└─────────────────────────────────────────────────────────────────────────────────────────┘
          ▲ lecture (service principal, CAN_USE)               ▲ lecture / écriture
   Unity Catalog  <catalog>.<schema>.ref_* / fct_*      Lakebase  app_* (saisies, scénarios, PDP, audit)
```

## Principes

* **Séparation stricte logique / UI** : le moteur (`appro.engine`) ne connaît ni pandas, ni la base,
  ni HTTP. Il reçoit un `Dataset` (records) + `EngineParams` et renvoie un `MrpResult`. Il est testé
  unitairement et contre les valeurs du classeur legacy.
* **Déterminisme et performance** : calculs vectorisés NumPy sur une fenêtre glissante (index jour) ;
  16 articles × 135 jours ≈ 20 ms ; complexité linéaire en articles × jours. Les résultats sont mis en cache
  par (version des données, périmètre, paramètres, scénario) et invalidés à chaque écriture.
* **Deux sources de vérité** : les données ERP (lecture seule, Unity Catalog) et les saisies applicatives
  (Lakebase). La fusion est explicite (`app_entries_into_dataset`) et paramétrable (`orders_source`).
* **Extensibilité** : nouvelles règles = nouveau champ d'`EngineParams` (documenté automatiquement dans
  l'écran Paramètres via `/api/params/schema`) ; nouvelle source = implémentation de `ErpSource` ;
  nouveaux événements de scénario = une branche dans `scenario.apply_scenario`.
* **Conception UI** (voir `client/src/styles/tokens.css`) : jetons de design (couleurs sémantiques, échelle
  typographique ratio 1,25, espacements 4 px, rayons, ombres), mode clair / sombre, notation inspirée
  IBCS : ferme = trait plein foncé, prévisionnel = tirets, simulé = pointillé, cible = référence grise, commandes simulées = hachures ;
  états chargement / vide / erreur / partiel sur chaque vue ; chaque KPI porte unité, période et fraîcheur.

## Flux de calcul d'une page

1. Le client appelle `/api/cockpit` ou `/api/articles/{id}/projection` avec le périmètre courant.
2. `mrp_service.compute` construit les paramètres (défauts ← configuration ← surcharges globales ←
   requête), assemble le `Dataset` (dont les cellules du tableau), applique le scénario, exécute `run_mrp` ; `run_cbn` écrit les propositions en cellules.
3. Les présentateurs agrègent (jour / semaine) et sérialisent ; le client affiche, avec états.
4. Toute écriture (saisie, décision, import, paramètre) journalise dans `app_audit_log` et incrémente la
   version des données → invalidation du cache serveur et des requêtes client.

## Arborescence

```
backend/appro/engine/      moteur MRP (voir docs/regles_metier.md)
backend/appro/data/        schémas canoniques, sources ERP, store SQLAlchemy, assembleur
backend/appro/services/    contexte applicatif, service MRP, service Excel
backend/appro/api/         FastAPI : routers, schémas, présentateurs, dépendances
backend/tests/             pytest : unités moteur, non-régression Excel, API bout en bout
client/src/                React : pages, composants UI, graphiques, état, styles (tokens)
data/seed/                 jeu de données canonique extrait du classeur (démo / tests)
scripts/                   extraction Excel, DDL Unity Catalog, chargeur de seed, build / deploy
docs/                      analyse, règles métier, architecture, modèle de données, déploiement
app.yaml · databricks.yml · start.sh · requirements.txt   packaging Databricks Apps
```

## Sécurité et identité

* Sur Databricks Apps, l'accès aux données ERP se fait avec le **service principal** de l'app
  (ressource SQL warehouse `CAN_USE`, Lakebase `CAN_CONNECT_AND_CREATE`). L'identité de l'utilisateur est
  lue dans les en-têtes `x-forwarded-email` / `x-forwarded-preferred-username` pour la traçabilité.
* Aucun identifiant n'est codé en dur : `DATABRICKS_WAREHOUSE_ID` et `PGHOST` sont injectés (`valueFrom`).
* Le passage en OBO (token utilisateur, `sql` scope) est possible en remplaçant le `credentials_provider`
  de `UnityCatalogSource` par le token de la requête si un contrôle d'accès par ligne est requis.
