# Ma Routine Appro — cockpit approvisionnement (Databricks App)

> Nom de l'application : **Ma Routine Appro**. Les identifiants techniques (bundle `appro`, App `appro-<cible>`, paquet Python `appro`, variables `APPRO_*`) ne changent pas : les renommer recréerait les Apps Databricks, leurs principaux de service et leurs schémas.

Application qui remplace le classeur Excel `SIMULATION_<appro>.xlsx` : projection de stock à partir du PDP
et des commandes ERP, plan de livraison saisi directement dans le tableau, propositions CBN (MOQ,
conditionnement, délais, jours de livraison, quotas), alertes, exports / imports Excel.

**Mode d'emploi : vide, c'est l'ERP ; un chiffre, c'est votre plan ; le stock se recalcule.** Deux lignes
du tableau se saisissent, *Appro.* (une par fournisseur) et *Ajustement* ; deux lignes se cliquent, *Ferme*
(ignorer une commande) et *Proposition CBN* (refuser une proposition) ; tout le reste se lit.

| Page | Contenu |
|---|---|
| Cockpit du jour | onglet *Couvertures et stocks* (tuiles Critiques / À commander / Surstock / Données / OK, portefeuille avec couverture à date, commande en cours et commande à placer, projection de la valeur du stock, alertes) et onglet *Flux d'approvisionnement* (à commander, commandé, en transit, à traiter, à réceptionner, reçu, en retard, à valider) |
| Tableau d'approvisionnement | le tableau des articles du périmètre, **par pages** (filtres programme / fournisseur / recherche, **période affichée** par semaines), grille virtualisée : 500 articles × 1 000 jours restent fluides |
| Fiche article | tableau (Besoin ; par fournisseur : Ferme **cliquable** (commandé / restant, en cours / soldée / ignorée), Prévisionnel, Reçu, **Appro.** ; Proposition CBN **cliquable** (refus du jour ; **!** urgente = plancher, **▲** plafonnée) ; **Ajustement** ; Projeté ERP ; Projeté Appro. avec couverture en jours ouvrés et manque cumulé dans les cellules ; poignée de **recopie** sur Plan et Ajustement), KPI, courbes, **planning de livraison** (lignes ERP copiables, FR / EN), onglet **Paramétrage** (politique de stock et par semaine, jours de consommation avec profil réel et suggestion, source de consommation ERP / PDP / dérivée, fournisseurs et règles : délai hérité ou spécifique, plafonds), commandes ERP, propositions, alertes |
| Fournisseurs | liste des fournisseurs (état de leurs articles) et **fiche fournisseur** (articles et règles, en retard par commande, commandes à venir, en transit, à traiter, à valider, urgences CBN) ; code et nom cliquables partout |
| Saisies & journal | cellules du plan, ajustements, commandes ignorées, **actions (annuler / rétablir, 7 jours)**, journal nominatif ; dans l'en-tête : ↶ ↷, historique, Ctrl+Z / Ctrl+Y, toast « Annuler » après chaque écriture |
| Référentiel | articles, fournisseurs, règles article ↔ fournisseur, termes de consommation, commandes fermées (PO), programmes, nomenclatures, stock de référence, **approvisionneurs (rôles) et délégations** : CRUD, **modèle Excel par table**, **modification en lot** ; colonnes calculées et filtres rapides (sans fournisseur, multi-sourcing, sans programme, conso dérivée / PDP, jours conso ≠ 5, plafonnés, délai spécifique…) ; paramètres par semaine édités comme une feuille |
| Plan de production (PDP) | trois onglets : **Plan de production** (PDP effectif en feuille (programmes × semaines, du point zéro à la dernière semaine planifiée ; semaines passées grisées, masquées par défaut, modifiables par un administrateur seulement) : saisie directe par les managers et administrateurs, import d'un classeur, **export Excel réimportable tel quel**, versions ; aucune table ERP de PDP), **Adhérence PDP / réel** (nette, brute, biais, profil réel, jours de consommation suggérés, version du PDP retenue par semaine), **Impact programmes** (production réalisable par programme et semaine) |
| Imports / exports | classeur de simulation à formules (export, réimport), alertes, plan |
| Paramètres | **Règles communes** (tolérance sous la cible ; règles figées listées), **Affichage** (semaines passées / détaillées, thème, polices), **Données** (synchronisation ERP et alerte de retard, articles hors référentiel, calendrier usine), **Utilisateurs** |

Tout tableau porte un bouton **Excel** (colonnes affichées, lignes filtrées) et un pied de totaux ; les filtres
de chaque page sont mémorisés entre deux visites. Le moteur compte en **jours ouvrés** (délai fournisseur
inclusif, couvertures, seuils), l'horizon est fixe (52 semaines), le besoin
non servi est perdu et affiché en cumul, les propositions suivent un **plancher dans le délai** puis la cible
pleine (déclenchement sous 85 % de la cible), avec **plafonds** par livraison et par semaine et quotas ;
détail dans `docs/guide_moteur.md`.

## Démarrage rapide (local)

```bash
pip install -r requirements-dev.txt
(cd client && npm ci && npm run build)
cp .env.example .env
python main.py                             # http://localhost:8000 — données de démonstration, approvisionneur QUENTIN
```

Tests : `cd backend && pytest -q` (moteur et scénarios du guide, API, formules du classeur via LibreOffice,
bundle, guide à jour). Lint : `ruff check backend scripts jobs main.py` ; frontend : `cd client && npm run build`.
La CI GitHub Actions est en lancement manuel.

## Déploiement Databricks

Guide complet pas à pas : [`docs/deploiement.md`](docs/deploiement.md). En résumé :

```bash
python3 .claude/skills/databricks-livraison/verifier_bundle.py .     # contrôles statiques
scripts/deploy.sh dev PROD --var="sync_role=prenom.nom@exemple.com"  # compile, valide, déploie, démarre
databricks bundle run appro_sync_erp -t dev --profile PROD           # première synchronisation ERP → Lakebase
```

## Documentation

* [`docs/guide_moteur.md`](docs/guide_moteur.md) — **guide du moteur et du tableau d'approvisionnement** : chaque paramètre et son effet, avec des scénarios calculés par le moteur (tableaux générés par `scripts/guide_moteur.py`)
* [`docs/regles_metier.md`](docs/regles_metier.md) — règles de calcul (besoin et jours de consommation, voies fournisseur, plan par cellules, commandes en retard, ajustements, projection, couverture, alertes, propositions : plancher, tolérance, plafonds, quotas, classeur)
* [`docs/recommandations_simplification.md`](docs/recommandations_simplification.md) — liste des simplifications et règles décidées (toutes implémentées)
* [`docs/dictionnaire_donnees.md`](docs/dictionnaire_donnees.md) — référentiel géré dans l'application, faits ERP et leur correspondance avec `commandes_edi` / `recep_edi`, base applicative
* [`docs/flux_donnees_erp.md`](docs/flux_donnees_erp.md) — **flux de données ERP de bout en bout** : colonnes lues dans chaque table Unity Catalog (SQL exact), miroir Lakebase écrit par le job, objets du moteur, exploitation de chaque colonne dans les écrans
* [`docs/modele_donnees.md`](docs/modele_donnees.md) — vue d'ensemble des tables
* [`docs/architecture.md`](docs/architecture.md) — architecture et principes
* [`docs/deploiement.md`](docs/deploiement.md) — déploiement Databricks pas à pas (terminal et interface), diagnostic
* [`docs/analyse_excel.md`](docs/analyse_excel.md) — fonctionnement et limites du classeur historique

## Pile technique

Python 3.11 · FastAPI · NumPy / pandas · SQLAlchemy (SQLite en local, Lakebase PostgreSQL) · openpyxl —
React 18 · TypeScript · Vite · TanStack Query · Recharts — Databricks Apps (bundle `databricks.yml`,
`app.yaml`, `main.py`), job de synchronisation serverless (`jobs/`).
