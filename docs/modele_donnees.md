# Modèle de données

Le détail colonne par colonne est dans [`dictionnaire_donnees.md`](dictionnaire_donnees.md) ; ce document
donne la vue d'ensemble.

```
                     ┌──────────────── base applicative (Lakebase / SQLite) ────────────────┐
  Référentiel        │ ref_articles  ref_suppliers  ref_article_suppliers                   │
  (géré dans l'App)  │ ref_programs  ref_bom        fct_stock                              │
                     │                                                                       │
  Saisies            │ app_plan_cells (article × fournisseur × jour)                         │
  (deux lignes)      │ app_adjustments (article × jour)                                      │
  Clics             │ app_cell_flags (commande ferme ignorée, proposition CBN refusée)       │
                     │ app_pdp_versions / app_pdp_lines   app_param_overrides   app_audit_log │
                     │                                                                       │
  Faits ERP          │ erp_purchase_orders  erp_receipts  erp_consumption_actual             │
  (miroir, job)      │ erp_production_plan  erp_sync_log                                     │
                     └───────────────────────────────────────────────────────────────────────┘
                                          ▲ job appro_sync_erp (SQL de erp_sql.py)
  Unity Catalog      silver_erp_ye.commandes_edi   silver_erp_ye.recep_edi   (+ production, PDP)
```

## 1. Référentiel

Six tables gérées dans l'application (page *Référentiel* : CRUD ligne par ligne, modèle Excel par table,
import *remplacer* / *fusionner*, export). Schéma canonique `backend/appro/data/schemas.py`
(`REFERENCE_TABLES`) ; les modèles ORM sont générés depuis ce schéma (`data/store.py`, `REF_MODELS`).

| Table | Clé | Rôle |
|---|---|---|
| `ref_articles` | article_id | identité, unité, approvisionneur (périmètre), politique de stock |
| `ref_suppliers` | supplier_id | fournisseurs et jours de livraison |
| `ref_article_suppliers` | article_id, supplier_id | MOQ, PLA, délai, quota, priorité → **voies** du tableau |
| `ref_programs` | program_id | programmes (nom du fichier PDP) |
| `ref_bom` | program_id, article_id | nomenclature 1 niveau |
| `ref_planners` | planner_id | approvisionneurs : nom (= colonne *Approvisionneur* des articles), e-mail Databricks, rôle (appro / manager / admin), actif |
| `ref_delegations` | from_planner, to_planner, date_from | un carnet confié à un collègue entre deux dates |
| `fct_stock` | article_id, snapshot_date | stock de référence (fin de journée) |

## 2. Faits ERP

Quatre tables canoniques (`FACT_TABLES`), produites par le SQL de correspondance de
`backend/appro/data/erp_sql.py` à partir des extractions. Le job `appro_sync_erp` les copie dans les tables
`erp_*` de la base (miroir, `APPRO_DATA_SOURCE=lakebase`) ; l'application peut aussi exécuter le même SQL sur
un SQL warehouse (`uc`).

| Table | Clé | Source | Notes |
|---|---|---|---|
| `fct_purchase_orders` | order_id | `commandes_edi` | créneau `fournisseur|article|date|ferme`, FIRM / FORECAST, restant ERP |
| `fct_receipts` | receipt_id | `recep_edi` | réception par fournisseur / commande / BL / article / jour |
| `fct_consumption_actual` | article_id, date | table UC à configurer (facultatif) | consommation réelle par composant, déjà éclatée |
| `fct_production_plan` | program_id, week_start, version | table à configurer (facultatif) | PDP ERP ; le PDP importé le remplace |

## 3. Saisies

Deux tables seulement, à l'image des deux lignes éditables du tableau :

| Table | Clé | Rôle |
|---|---|---|
| `app_plan_cells` | article_id, supplier_id, date | quantité planifiée d'un fournisseur un jour donné (0 = rien attendu), expression, commentaire, auteur |
| `app_adjustments` | article_id, date | ajustement signé (≤ référence : correction du stock de référence), expression, commentaire, auteur |
| `app_cell_flags` | kind, article_id, supplier_id, date | commande ferme ignorée (hors Scenario ERP et hors plan) ou proposition CBN refusée (quantité affichée, semaine bloquée) |
| `app_meta` | key | `data_version` : compteur partagé entre les workers de l'App, incrémenté à chaque écriture (invalidation des caches) |

Plus les versions de PDP importées, les paramètres (`global`, `article_week`) et le journal.

## 4. Données de démonstration

`data/seed` : un CSV par table canonique (référentiel et faits), extrait du classeur historique. En mode
local, le référentiel est chargé dans la base au premier démarrage et les faits sont lus des CSV ; les tests
utilisent le même jeu.
