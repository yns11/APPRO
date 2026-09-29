"""The **single** mapping between the ERP extractions and the canonical fact tables.

The same Databricks SQL is used by the application when it reads Unity Catalog through a SQL
warehouse (``APPRO_DATA_SOURCE=uc``) and by the synchronisation job that copies the facts into
Lakebase (``jobs/sync_erp_to_lakebase.py``).  Changing a column mapping is therefore one edit.

Sources (schema ``emotors_data_champions.silver_erp_ye`` by default) :

* ``commandes_edi`` – firm and forecast schedule lines aggregated by supplier / purchase order /
  article / delivery date (``ID`` = ``vendaccount|itemid|yyyyMMdd|silfirmorder``) ;
* ``recep_edi`` – physical receipts by supplier / purchase order / packing slip / article / day ;
* an optional daily production table (``APPRO_ERP_PRODUCTION_TABLE``) with the canonical columns
  ``program_id, date, qty`` ; an optional weekly PDP table (``APPRO_ERP_PDP_TABLE``).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ErpTables:
    catalog: str = "emotors_data_champions"
    schema: str = "silver_erp_ye"
    orders: str = "commandes_edi"
    receipts: str = "recep_edi"
    production: str = ""          # empty: no actual production read from the ERP
    pdp: str = ""                 # empty: the PDP comes from the file imported in the application

    def fqn(self, table: str) -> str:
        return f"`{self.catalog}`.`{self.schema}`.`{table}`"


def purchase_orders_sql(t: ErpTables) -> str:
    """One row per delivery slot ``ID`` (several purchase orders may share a slot: summed)."""
    return f"""
SELECT
  c.ID                                                            AS order_id,
  c.Article                                                       AS article_id,
  c.Code_fournisseur                                              AS supplier_id,
  MAX(c.Nom_fournisseur)                                          AS supplier_name,
  CASE WHEN MAX(c.Ordre_ferme) = 'Oui' THEN 'FIRM' ELSE 'FORECAST' END AS order_type,
  CAST(MIN(c.Date_de_debut) AS DATE)                              AS expected_date,
  CAST(SUM(c.Quantite) AS DOUBLE)                                 AS qty_ordered,
  CAST(SUM(GREATEST(c.Quantite_restante, 0)) AS DOUBLE)           AS qty_open,
  CONCAT_WS(', ', SORT_ARRAY(COLLECT_SET(c.Commande)))            AS purch_id,
  MAX(c.Niveau_engagement)                                        AS commitment
FROM {t.fqn(t.orders)} c
WHERE c.ID IS NOT NULL
GROUP BY c.ID, c.Article, c.Code_fournisseur
""".strip()


def receipts_sql(t: ErpTables) -> str:
    return f"""
SELECT
  CONCAT_WS('|', r.Code_fournisseur, r.Commande, COALESCE(r.BL, ''), r.Article,
            DATE_FORMAT(r.Date_reception, 'yyyyMMdd'))            AS receipt_id,
  r.Article                                                       AS article_id,
  r.Code_fournisseur                                              AS supplier_id,
  CAST(r.Date_reception AS DATE)                                  AS receipt_date,
  CAST(r.Quantite_recue AS DOUBLE)                                AS qty,
  r.Commande                                                      AS purch_id,
  r.BL                                                            AS packing_slip
FROM {t.fqn(t.receipts)} r
WHERE r.Quantite_recue IS NOT NULL AND r.Quantite_recue <> 0
""".strip()


def production_actual_sql(t: ErpTables) -> str:
    return f"""
SELECT p.program_id AS program_id, CAST(p.date AS DATE) AS date, CAST(SUM(p.qty) AS DOUBLE) AS qty
FROM {t.fqn(t.production)} p
GROUP BY p.program_id, CAST(p.date AS DATE)
""".strip()


def production_plan_sql(t: ErpTables) -> str:
    return f"""
SELECT p.program_id AS program_id, CAST(p.week_start AS DATE) AS week_start, CAST(p.qty AS DOUBLE) AS qty,
       COALESCE(CAST(p.version AS STRING), '') AS version
FROM {t.fqn(t.pdp)} p
""".strip()


def fact_queries(t: ErpTables) -> dict[str, str]:
    """Canonical fact table → SQL (only the tables that are configured)."""
    out = {"fct_purchase_orders": purchase_orders_sql(t), "fct_receipts": receipts_sql(t)}
    if t.production:
        out["fct_production_actual"] = production_actual_sql(t)
    if t.pdp:
        out["fct_production_plan"] = production_plan_sql(t)
    return out
