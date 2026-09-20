# APPRO — cockpit approvisionnement, MRP et simulation (Databricks App)

Application destinée à remplacer le classeur Excel `SIMULATION_<appro>.xlsx` par un outil quotidien de
reporting, de simulation et d'aide à la décision pour chaque approvisionneur : alertes (rupture,
couverture insuffisante, surstock, retards), projection de stock à partir du plan de production,
propositions de commandes respectant MOQ / conditionnement / délais / jours de livraison / quotas,
scénarios *what-if*, saisies tracées et exports / imports Excel.

| Fonction | Où |
|---|---|
| Cockpit du jour (KPI, alertes, perspective 12 semaines, portefeuille) | `/` |
| Fiche article : courbes stock ferme / prévisionnel / simulé (jamais négatifs) + manque + cible, tableau jour ou semaine, commandes & mouvements, propositions, alertes, données de base | `/articles/<ref>` |
| Plan de commandes : accepter / modifier / ignorer les propositions, export carnet | `/propositions` |
| Scénarios : événements (commande, retard, PDP ×, réel, paramètres…), comparaison base ↔ scénario, activation globale | `/simulation` |
| Saisies : commandes, réceptions, ajustements, production réelle ; journal des actions | `/saisies` |
| Imports (PDP hebdo, réimport du classeur) / exports (simulation Excel **à formules**, alertes, carnet) | `/imports` |
| Référentiel ERP + surcharges (seuils, MOQ, PLA, délais, quotas) | `/referentiel` |
| Règles du moteur (paramétrables, documentées) | `/parametres` |

## Démarrage rapide

```bash
pip install -r requirements-dev.txt
(cd client && npm ci && npm run build)
cd backend && APPRO_AS_OF=2026-09-19 uvicorn appro.api.main:app --port 8000
# → http://localhost:8000  (données de démonstration issues du classeur, approvisionneur QUENTIN)
```

Tests : `cd backend && pytest -q` (moteur, non-régression contre les valeurs du classeur, API).

## Documentation

* [`docs/analyse_excel.md`](docs/analyse_excel.md) — fonctionnement et faiblesses de l'outil actuel
* [`docs/regles_metier.md`](docs/regles_metier.md) — règles de calcul (besoin, stock, couverture, alertes, propositions, scénarios) et leurs variantes
* [`docs/architecture.md`](docs/architecture.md) — architecture logicielle, principes, extensibilité
* [`docs/modele_donnees.md`](docs/modele_donnees.md) — tables Unity Catalog (ERP) et tables applicatives (Lakebase)
* [`docs/deploiement.md`](docs/deploiement.md) — développement local, préparation Databricks, déploiement (CLI / bundle), exploitation

## Pile technique

Python 3.11 · FastAPI · NumPy / pandas · SQLAlchemy (SQLite local, Lakebase PostgreSQL) ·
databricks-sql-connector (Unity Catalog) · openpyxl — React 18 · TypeScript · Vite · TanStack Query ·
Recharts — Databricks Apps (`app.yaml`, `databricks.yml`, `start.sh`).
