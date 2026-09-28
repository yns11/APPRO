# Modèle de données

## 1. Tables ERP / référentiel (Unity Catalog, lecture seule)

Schéma canonique partagé par le seed CSV (`data/seed`), les tables UC (`scripts/uc/create_tables.sql`)
et les frames internes (`appro.data.schemas.TABLES`). Clé primaire logique en gras.

| Table | Colonnes | Notes |
|---|---|---|
| `ref_articles` | **article_id**, designation, unit, family, planner, coverage_target_days, alert_red_days, alert_yellow_days, overstock_days, safety_stock_qty, service_rate_tracked, dhrq, active | `planner` = approvisionneur (périmètre) |
| `ref_suppliers` | **supplier_id** (COFOR), name, country, contact, delivery_weekdays (`1,2,3,4,5`), calendar_id, active | jours de livraison ISO |
| `ref_article_suppliers` | **article_id, supplier_id**, moq, pack_qty (PLA), lead_time_days (ouvrés), quota_pct, priority, active | règles de sourcing |
| `ref_programs` | **program_id**, name, family, has_bom, active | PF/SF |
| `ref_bom` | **program_id, article_id**, qty_per, unit, scrap_pct, valid_from, valid_to | nomenclature 1 niveau |
| `fct_production_plan` | **program_id, week_start, version**, iso_week, qty, published_at | PDP hebdo ; la version la plus récente gagne |
| `fct_production_actual` | **program_id, date**, qty | production réelle |
| `fct_purchase_orders` | **order_id, line_no**, article_id, supplier_id, order_type (FIRM/FORECAST), message_type (DELJIT/DELFOR), order_date, expected_date, qty_ordered, qty_received, status (OPEN/PARTIAL/RECEIVED/CLOSED/CANCELLED), unit | quantité ouverte = commandée − reçue ; `order_id` = identifiant **synthétique** du créneau `fournisseur|article|date|ferme` (voir § 1.1) |
| `fct_receipts` | **receipt_id**, order_id, article_id, supplier_id, receipt_date, qty, unit | |
| `fct_stock_movements` | **movement_id**, article_id, date, movement_type, qty, comment | inventaires, casse… |
| `fct_stock` | **article_id, snapshot_date, location**, qty_on_hand, qty_blocked, unit | stock fin de journée ; plusieurs emplacements sommés |

Sources ERP typiques : tables de commandes (OA, DELFOR/DELJIT EDI), réceptions (MIGO / entrées
marchandises), stocks (MARD…), mouvements, plan de production (S&OP / PDP), déclarations de production.
Les jobs Lakeflow alimentent ces tables ; l'application ne les modifie jamais.

### 1.1 Commandes : de l'extraction `commandes_edi` au canonique

L'ERP (D365, lignes d'achat `purch_line`) n'offre pas d'identifiant stable et unique de commande. La table
nettoyée `commandes_edi` (requête en amont, catalogue silver) agrège les lignes d'achat par
**fournisseur | article | date de livraison | drapeau ferme** et produit :

| `commandes_edi` | `fct_purchase_orders` | Remarque |
|---|---|---|
| `ID` = `vendaccount|itemid|yyyyMMdd|silfirmorder` | `order_id` | identifiant synthétique du **créneau de livraison**, stable car l'ERP ne déplace ni n'annule jamais une ligne ; unique après retrait de `purchid` du `GROUP BY` |
| `Commande` (collect des `purchid`) | `message_type` (information) | numéro(s) de commande d'achat, à titre indicatif |
| `Code_fournisseur` | `supplier_id` | |
| `Article` | `article_id` | |
| `Date_de_debut` | `expected_date` | |
| `Ordre_ferme` = Oui / Non | `order_type` = FIRM / FORECAST | seul critère de classement F / P ; `Niveau_engagement` reste informatif |
| `Quantite` | `qty_ordered` | |
| `Quantite` − `Quantite_restante` | `qty_received` | restant ERP non fiable pour les lignes passées : voir la politique des retards (`docs/regles_metier.md` § 3.2) |
| — | `line_no` = 1, `status` = OPEN, `order_date` = null | |

Les lignes prévisionnelles ne sont extraites qu'à partir du lundi suivant ; les lignes fermes sont
conservées depuis le début de l'année (commandes non reçues = backlog). Le dictionnaire complet est dans
`docs/dictionnaire_donnees.md`. La vue de correspondance est fournie dans
`scripts/uc/create_tables.sql` (`v_fct_purchase_orders_from_commandes_edi`).

## 2. Tables applicatives (Lakebase PostgreSQL / SQLite en local)

Créées automatiquement au démarrage (`Base.metadata.create_all`).

| Table | Rôle |
|---|---|
| `app_orders` | commandes fermes saisies hors ERP (comptées dans les deux scenarios) : article, fournisseur, date attendue, qté, `status` (OPEN/SENT/RECEIVED/CANCELLED), `source` (MANUAL/IMPORT), note, auteur, dates |
| `app_plan_lines` | lignes du **plan de livraison** : article, `order_id` (créneau ERP surchargé ; vide = ligne libre), fournisseur, date, quantité (0 = rien attendu de la commande), `source` (MANUAL / IMPORT / CBN), commentaire, `erp_json` (état ERP vu à la saisie), auteur, dates |
| `app_cells` | ajustements saisis dans la grille : article, date (toute date ; ≤ référence = correction du stock de référence), expression, quantité, `source` (MANUAL/IMPORT), note, auteur ; unique par (article, date, kind) |
| `app_receipts` | réceptions saisies (optionnellement rattachées à une commande app ou ERP) |
| `app_adjustments` | ajustements de stock (±) |
| `app_production_actual` | production réelle saisie par (programme, jour) – prime sur l'ERP |
| `app_pdp_versions` / `app_pdp_lines` | versions de PDP importées ; une version active au plus |
| `app_scenarios` / `app_scenario_events` | scénarios (paramètres JSON) et événements ordonnés (type + payload JSON) |
| `app_param_overrides` | surcharges : `global` (règles moteur), `article` (seuils, cible…), `article_week` (mêmes paramètres pour une semaine ISO, key2 = `2026-W40`), `link` (MOQ, PLA, délai, quota…) |
| `app_audit_log` | journal : horodatage, utilisateur, action, objet, article, payload JSON |

## 3. Données de démonstration

`scripts/extract_seed_from_excel.py` convertit le classeur legacy en seed canonique (16 articles,
11 fournisseurs, 20 liens, 25 programmes, 28 lignes de nomenclature, 534 semaines de PDP, 1 121 jours de
production réelle, 919 lignes de commandes, 477 réceptions, 227 ajustements, 16 snapshots de stock au
18/09/2026). Les conventions de conversion sont décrites en tête du script et dans
`docs/analyse_excel.md`. `scripts/uc/load_seed.py` charge ce seed dans Unity Catalog pour un
environnement de démonstration.
