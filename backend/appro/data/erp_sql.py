"""The **single** mapping between the ERP extractions and the canonical fact tables.

The same Databricks SQL is used by the application when it reads Unity Catalog through a SQL
warehouse (``APPRO_DATA_SOURCE=uc``) and by the synchronisation job that copies the facts into
Lakebase (``jobs/sync_erp_to_lakebase.py``).  Changing a column mapping is therefore one edit.

Sources (schema ``emotors_data_champions.silver_erp_ye`` by default) :

* ``commandes_edi`` – firm and forecast schedule lines aggregated by supplier / purchase order /
  article / delivery date (``ID`` = ``vendaccount|itemid|yyyyMMdd|silfirmorder``) ;
* ``recep_edi`` – physical receipts by supplier / purchase order / packing slip / article / day ;
* an optional daily **actual consumption** table (``APPRO_ERP_CONSUMPTION_TABLE``) with the canonical
  columns ``article_id, date, qty`` – the consumption is given **per component, already exploded**
  through the bill of material (unlike the PDP, exploded by the engine) ; an optional weekly PDP
  table (``APPRO_ERP_PDP_TABLE``).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class ErpTables:
    """Keyword-only on purpose: a positional construction once sent the DESADV table to the PDP query."""

    catalog: str = "emotors_data_champions"
    schema: str = "silver_erp_ye"
    orders: str = "commandes_edi"
    receipts: str = "recep_edi"
    consumption: str = ""         # empty: no actual consumption read from the ERP
    desadv: str = ""              # empty: no despatch advices (DESADV) read from the ERP
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


def consumption_actual_sql(t: ErpTables) -> str:
    """Actual consumption per component and day.  The source table carries the canonical columns
    ``article_id`` (the component, as in ``ref_articles``), ``date`` and ``qty`` (stock unit of the
    article) ; several rows of a day (several programmes, several movements) are summed."""
    return f"""
SELECT c.article_id AS article_id, CAST(c.date AS DATE) AS date, CAST(SUM(c.qty) AS DOUBLE) AS qty
FROM {t.fqn(t.consumption)} c
WHERE c.article_id IS NOT NULL AND c.date IS NOT NULL AND c.qty IS NOT NULL
GROUP BY c.article_id, CAST(c.date AS DATE)
""".strip()


def desadv_sql(t: ErpTables) -> str:
    """Despatch advices, one row per article line of a DespatchAdvice-Purchase message (``desadv_edi``)."""
    return f"""
SELECT
  CONCAT_WS('|', CAST(d.Document_ID AS STRING), CAST(d.ID_Ligne AS STRING)) AS desadv_id,
  d.Code_article                                                  AS article_id,
  d.Code_fournisseur                                              AS supplier_id,
  d.Nom_fournisseur                                               AS supplier_name,
  CAST(d.BL AS STRING)                                            AS packing_slip,
  CAST(d.Commande_ouverte AS STRING)                              AS purch_id,
  CAST(d.Date_emission AS DATE)                                   AS issue_date,
  CAST(d.Quantite_achat AS DOUBLE)                                AS qty,
  d.Etat_message                                                  AS state,
  d.Traitement_final                                              AS final_processing
FROM {t.fqn(t.desadv)} d
WHERE d.Code_article IS NOT NULL AND d.Date_emission IS NOT NULL
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
    if t.consumption:
        out["fct_consumption_actual"] = consumption_actual_sql(t)
    if t.desadv:
        out["fct_desadv"] = desadv_sql(t)
    if t.pdp:
        out["fct_production_plan"] = production_plan_sql(t)
    return out
