-- Unity Catalog tables read by the APPRO application (canonical schema – see docs/modele_donnees.md).
-- Run once per environment: replace ${catalog} / ${schema} (or use the parameters of a SQL task).
-- These tables are normally fed by the ERP integration pipelines (Lakeflow); the seed loader
-- (scripts/uc/load_seed.py) fills them from the CSV seed for a demo environment.
CREATE SCHEMA IF NOT EXISTS ${catalog}.${schema};

CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.ref_articles (
  article_id STRING NOT NULL, designation STRING, unit STRING, family STRING, planner STRING,
  coverage_target_days INT, alert_red_days INT, alert_yellow_days INT, overstock_days INT,
  safety_stock_qty DOUBLE, service_rate_tracked BOOLEAN, dhrq STRING, active BOOLEAN
) COMMENT 'Article master data (référence, désignation, unité, couverture cible, seuils d alerte)';

CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.ref_suppliers (
  supplier_id STRING NOT NULL, name STRING, country STRING, contact STRING,
  delivery_weekdays STRING COMMENT 'ISO weekdays allowed for deliveries, e.g. 1,2,3,4,5',
  calendar_id STRING, active BOOLEAN
) COMMENT 'Supplier master data (COFOR)';

CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.ref_article_suppliers (
  article_id STRING NOT NULL, supplier_id STRING NOT NULL, moq DOUBLE, pack_qty DOUBLE COMMENT 'PLA – quantité de conditionnement / arrondi',
  lead_time_days INT COMMENT 'délai fournisseur en jours ouvrés', quota_pct DOUBLE, priority INT, active BOOLEAN
) COMMENT 'Article <-> supplier sourcing rules';

CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.ref_programs (
  program_id STRING NOT NULL, name STRING, family STRING, has_bom BOOLEAN, active BOOLEAN
) COMMENT 'Production programs (PF/SF)';

CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.ref_bom (
  program_id STRING NOT NULL, article_id STRING NOT NULL, qty_per DOUBLE, unit STRING, scrap_pct DOUBLE,
  valid_from DATE, valid_to DATE
) COMMENT 'One-level bill of material';

CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.fct_production_plan (
  program_id STRING NOT NULL, week_start DATE NOT NULL COMMENT 'Monday of the ISO week', iso_week STRING, qty DOUBLE,
  version STRING, published_at DATE
) COMMENT 'Weekly production plan (PDP)';

CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.fct_production_actual (
  program_id STRING NOT NULL, date DATE NOT NULL, qty DOUBLE
) COMMENT 'Actual daily production per program';

CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.fct_purchase_orders (
  order_id STRING NOT NULL, line_no INT, article_id STRING NOT NULL, supplier_id STRING,
  order_type STRING COMMENT 'FIRM (DELJIT / PO) or FORECAST (DELFOR)', message_type STRING,
  order_date DATE, expected_date DATE NOT NULL, qty_ordered DOUBLE, qty_received DOUBLE,
  status STRING COMMENT 'OPEN | PARTIAL | RECEIVED | CLOSED | CANCELLED', unit STRING
) COMMENT 'Purchase orders / schedule lines';

CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.fct_receipts (
  receipt_id STRING NOT NULL, order_id STRING, article_id STRING NOT NULL, supplier_id STRING,
  receipt_date DATE NOT NULL, qty DOUBLE, unit STRING
) COMMENT 'Goods receipts';

CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.fct_stock_movements (
  movement_id STRING NOT NULL, article_id STRING NOT NULL, date DATE NOT NULL, movement_type STRING, qty DOUBLE, comment STRING
) COMMENT 'Stock movements other than receipts / consumption (inventory adjustments, scrap…)';

CREATE TABLE IF NOT EXISTS ${catalog}.${schema}.fct_stock (
  article_id STRING NOT NULL, snapshot_date DATE NOT NULL COMMENT 'stock known at end of day', qty_on_hand DOUBLE,
  qty_blocked DOUBLE, unit STRING, location STRING
) COMMENT 'Daily stock snapshots';
