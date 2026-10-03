# Ma Routine Appro — cockpit approvisionnement (Databricks App)

> Nom de l'application : **Ma Routine Appro**. Les identifiants techniques (bundle `appro`, App `appro-<cible>`, paquet Python `appro`, variables `APPRO_*`) ne changent pas : les renommer recréerait les Apps Databricks, leurs principaux de service et leurs schémas.

Application qui remplace le classeur Excel `SIMULATION_<appro>.xlsx` : projection de stock à partir du PDP
et des commandes ERP, plan de livraison saisi directement dans le tableau, propositions CBN (MOQ,
conditionnement, délais, jours de livraison, quotas), alertes, exports / imports Excel.

**Mode d'emploi : vide, c'est l'ERP ; un chiffre, c'est votre plan ; le stock se recalcule.** Deux lignes
du tableau se saisissent, *Plan* (une par fournisseur) et *Ajustement* ; deux lignes se cliquent, *Ferme*
(ignorer une commande) et *Proposition CBN* (refuser une proposition) ; tout le reste se lit.

| Page | Contenu |
|---|---|
| Cockpit du jour | KPI, backlog fournisseur, alertes, perspective 12 semaines, portefeuille filtrable |
| Tableau d'approvisionnement | le tableau des articles du périmètre, **par pages** (filtres programme / fournisseur / recherche), grille virtualisée : 500 articles × 1 000 jours restent fluides |
| Fiche article | tableau (Besoin ; par fournisseur : Ferme **cliquable** (commandé / restant, en cours / soldée / ignorée), Prévisionnel, Reçu, **Plan** ; Proposition CBN **cliquable** (refus, semaine bloquée) ; **Ajustement** ; Scenario ERP ; Scenario Plan avec couverture et manque dans les cellules ; poignée de **recopie** sur Plan et Ajustement), KPI, courbes, **planning de livraison** (lignes ERP copiables, FR / EN), commandes ERP, propositions, alertes |
| Propositions CBN | une proposition par fournisseur et par jour de livraison |
| Saisies & journal | cellules du plan, ajustements, journal nominatif |
| Référentiel | articles, fournisseurs, règles article ↔ fournisseur, programmes, nomenclatures, stock de référence, **approvisionneurs (rôles) et délégations** : CRUD et **modèle Excel par table** ; paramètres par semaine édités comme une feuille |
| Impact programmes | production réalisable par programme et semaine |
| Imports / exports | PDP hebdomadaire (modèle), classeur de simulation à formules (export, réimport), alertes, plan |
| Paramètres | règles d'approvisionnement du moteur, affichage (polices, tailles), administration (droits) |

## Démarrage rapide (local)

```bash
pip install -r requirements-dev.txt
(cd client && npm ci && npm run build)
cp .env.example .env
python main.py                             # http://localhost:8000 — données de démonstration, approvisionneur QUENTIN
```

Tests : `cd backend && pytest -q` (moteur, non-régression contre le classeur, API, formules du classeur via
LibreOffice, bundle). Lint : `ruff check backend scripts jobs main.py` ; frontend : `cd client && npm run build`.
La CI GitHub Actions est en lancement manuel.

## Déploiement Databricks

Guide complet pas à pas : [`docs/deploiement.md`](docs/deploiement.md). En résumé :

```bash
python3 .claude/skills/databricks-livraison/verifier_bundle.py .     # contrôles statiques
scripts/deploy.sh dev PROD --var="sync_role=prenom.nom@exemple.com"  # compile, valide, déploie, démarre
databricks bundle run appro_sync_erp -t dev --profile PROD           # première synchronisation ERP → Lakebase
```

## Documentation

* [`docs/regles_metier.md`](docs/regles_metier.md) — règles de calcul (besoin, voies fournisseur, plan par cellules, backlog, ajustements, projection, couverture, alertes, propositions, classeur)
* [`docs/dictionnaire_donnees.md`](docs/dictionnaire_donnees.md) — référentiel géré dans l'application, faits ERP et leur correspondance avec `commandes_edi` / `recep_edi`, base applicative
* [`docs/modele_donnees.md`](docs/modele_donnees.md) — vue d'ensemble des tables
* [`docs/architecture.md`](docs/architecture.md) — architecture et principes
* [`docs/deploiement.md`](docs/deploiement.md) — déploiement Databricks pas à pas (terminal et interface), diagnostic
* [`docs/analyse_excel.md`](docs/analyse_excel.md) — fonctionnement et limites du classeur historique

## Pile technique

Python 3.11 · FastAPI · NumPy / pandas · SQLAlchemy (SQLite en local, Lakebase PostgreSQL) · openpyxl —
React 18 · TypeScript · Vite · TanStack Query · Recharts — Databricks Apps (bundle `databricks.yml`,
`app.yaml`, `main.py`), job de synchronisation serverless (`jobs/`).
