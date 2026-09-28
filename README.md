# APPRO — cockpit approvisionnement, MRP et simulation (Databricks App)

Application destinée à remplacer le classeur Excel `SIMULATION_<appro>.xlsx` par un outil quotidien de
reporting, de simulation et d'aide à la décision pour chaque approvisionneur : alertes (rupture,
couverture insuffisante, surstock, retards), projection de stock à partir du plan de production,
propositions de commandes respectant MOQ / conditionnement / délais / jours de livraison / quotas,
scénarios *what-if*, saisies tracées et exports / imports Excel.

| Fonction | Où |
|---|---|
| Cockpit du jour (KPI, alertes, perspective 12 semaines, portefeuille filtrable) | `/` |
| Fiche article : tableau de simulation (Conso et besoins, Données ERP, Approvisionnement, Projection de stock : Scenario ERP / Scenario Plan avec couverture et manque dans les cellules), **plan de livraison** saisi dans la cellule ou par tiroir (dates, tranches, lignes libres, CBN accepté, prévisionnel repris), ajustements à toute date (correction du stock de référence), KPI Backlog, courbes, commandes ERP, mouvements, alertes | `/articles/<ref>` |
| Tableau d'approvisionnement : le même tableau pour tous les articles du périmètre (filtres programme / fournisseur / article) | `/tableau` |
| Complément CBN du périmètre (filtrable), export du plan | `/propositions` |
| Commandes non reçues : commandes ERP passées encore ouvertes (hors stocks), à dater dans le plan ou à clôturer ; commandes planifiées | `/retards` |
| Impact programmes : production réalisable par programme et semaine selon le stock à date / Scenario ERP / Scenario Plan | `/programmes` |
| Scénarios : événements (commande, retard, PDP ×, réel, paramètres…), comparaison base ↔ scénario | `/simulation` |
| Saisies : plan de livraison, commandes hors ERP, réceptions, ajustements, production réelle ; journal (tables filtrables) | `/saisies` |
| Imports (PDP hebdo avec **modèle téléchargeable**, réimport du classeur) / exports (simulation Excel à formules, alertes, plan) | `/imports` |
| Référentiel ERP + surcharges (seuils, MOQ, PLA, délais, quotas) et paramètres d'article par semaine | `/referentiel` |
| Règles du moteur (paramétrables, documentées) | `/parametres` |

## Démarrage rapide

```bash
pip install -r requirements-dev.txt
(cd client && npm ci && npm run build)
cd backend && APPRO_AS_OF=2026-09-19 uvicorn appro.api.main:app --port 8000
# → http://localhost:8000  (données de démonstration issues du classeur, approvisionneur QUENTIN)
```

Tests : `cd backend && pytest -q` (moteur, non-régression contre les valeurs du classeur, API, formules du classeur
exporté via LibreOffice). La CI GitHub Actions est en lancement manuel (`workflow_dispatch`).

## Documentation

* [`docs/analyse_excel.md`](docs/analyse_excel.md) — fonctionnement et faiblesses de l'outil actuel
* [`docs/regles_metier.md`](docs/regles_metier.md) — règles de calcul (besoins réel / reliquat PDP, Scenario ERP et Scenario Plan, commandes = créneaux de livraison, plan de livraison, commandes non reçues, lettrage du jour de référence, correction du stock de référence, historique reconstitué, couverture, alertes, complément CBN, paramètres hebdomadaires, impact programmes, scénarios)
* [`docs/dictionnaire_donnees.md`](docs/dictionnaire_donnees.md) — dictionnaire de toutes les données lues (tables ERP à préparer) et écrites (base applicative), et le choix Lakebase / Unity Catalog
* [`docs/architecture.md`](docs/architecture.md) — architecture logicielle, principes, extensibilité
* [`docs/modele_donnees.md`](docs/modele_donnees.md) — tables Unity Catalog (ERP) et tables applicatives (Lakebase)
* [`docs/deploiement.md`](docs/deploiement.md) — guide complet : tables Unity Catalog, Lakebase et tables synchronisées, ressources de l'app, déploiement, contrôles, exploitation

## Pile technique

Python 3.11 · FastAPI · NumPy / pandas · SQLAlchemy (SQLite local, Lakebase PostgreSQL) ·
databricks-sql-connector (Unity Catalog) · openpyxl — React 18 · TypeScript · Vite · TanStack Query ·
Recharts — Databricks Apps (`app.yaml`, `databricks.yml`, `start.sh`).
