# Databricks notebook source
# MAGIC %md
# MAGIC # APPRO — synchronisation des faits ERP vers Lakebase
# MAGIC
# MAGIC Copie `commandes_edi`, `recep_edi`, `desadv_edi`, les prix (`silver_base_article`), `bl_en_attente` (et, si configurées, la consommation réelle par composant et le PDP ERP)
# MAGIC dans les tables `erp_*` de la base Lakebase de l'application APPRO, avec le même SQL de
# MAGIC correspondance que l'application (`backend/appro/data/erp_sql.py`).
# MAGIC
# MAGIC **Quand l'utiliser.** C'est la variante « interface » du job `appro_sync_erp` du bundle :
# MAGIC importer ce fichier dans l'espace de travail (*Workspace → Import → File*), renseigner les
# MAGIC widgets, « Exécuter tout », puis *Schedule* (toutes les heures, après le job qui reconstruit
# MAGIC `commandes_edi`). Le notebook tourne sous **votre** identité, qui a déjà accès à l'ERP.
# MAGIC
# MAGIC **Prérequis.** L'application APPRO a été déployée et démarrée au moins une fois : c'est elle qui
# MAGIC crée les tables `erp_*` et qui accorde l'écriture au rôle `APPRO_SYNC_ROLE` (votre e-mail).
# MAGIC
# MAGIC | Widget | Où le trouver |
# MAGIC |---|---|
# MAGIC | `lakebase_endpoint` | `databricks postgres list-endpoints projects/<projet>/branches/<branche>` → `name` |
# MAGIC | `pg_host` | facultatif : `PGHOST` de l'App (onglet *Environment*), si la découverte échoue |
# MAGIC | `erp_catalog`, `erp_schema` | catalogue / schéma des extractions ERP |

# COMMAND ----------

# MAGIC %pip install psycopg[binary]==3.2.3
# MAGIC %restart_python

# COMMAND ----------

dbutils.widgets.text("lakebase_endpoint", "projects/appro/branches/production/endpoints/primary", "1. Endpoint Lakebase")
dbutils.widgets.text("pg_host", "", "2. Hôte Lakebase (vide = découverte)")
dbutils.widgets.text("pg_database", "databricks_postgres", "3. Base Postgres")
dbutils.widgets.text("pg_schema", "appro", "4. Schéma de l'application")
dbutils.widgets.text("erp_catalog", "emotors_data_champions", "5. Catalogue ERP")
dbutils.widgets.text("erp_schema", "silver_erp_ye", "6. Schéma ERP")
dbutils.widgets.text("orders_table", "commandes_edi", "7. Table commandes")
dbutils.widgets.text("receipts_table", "recep_edi", "8. Table réceptions")
dbutils.widgets.text("consumption_table", "conso_composants", "9. Table consommation réelle par composant (vide = aucune)")
dbutils.widgets.text("desadv_table", "desadv_edi", "9b. Table des avis d'expédition DESADV (vide = aucune)")
dbutils.widgets.text("pdp_table", "", "10. Table PDP ERP (vide = aucune)")
dbutils.widgets.text("prices_table", "silver_base_article", "11. Table des articles avec std_cost_price (vide = aucune)")
dbutils.widgets.text("bl_pending_table", "bl_en_attente", "12. Table des BL enregistrés en attente de validation (vide = aucune)")

# COMMAND ----------

import datetime as dt
import json
import logging

import psycopg
from psycopg import sql

logging.basicConfig(level=logging.INFO)
LOG = logging.getLogger("appro.sync")
W = {k: dbutils.widgets.get(k).strip() for k in ("lakebase_endpoint", "pg_host", "pg_database", "pg_schema", "erp_catalog",
                                                  "erp_schema", "orders_table", "receipts_table", "consumption_table", "pdp_table", "desadv_table",
                                                  "prices_table", "bl_pending_table")}

# --- SQL de correspondance (copie de backend/appro/data/erp_sql.py : à tenir identique) ----------
def fqn(t):
    return f"`{W['erp_catalog']}`.`{W['erp_schema']}`.`{t}`"

QUERIES = {
    "erp_purchase_orders": (["order_id", "article_id", "supplier_id", "supplier_name", "order_type", "expected_date",
                             "qty_ordered", "qty_open", "purch_id", "commitment", "first_seen"], f"""
SELECT c.ID AS order_id, c.Article AS article_id, c.Code_fournisseur AS supplier_id, MAX(c.Nom_fournisseur) AS supplier_name,
  CASE WHEN MAX(c.Ordre_ferme) = 'Oui' THEN 'FIRM' ELSE 'FORECAST' END AS order_type,
  CAST(MIN(c.Date_de_debut) AS DATE) AS expected_date, CAST(SUM(c.Quantite) AS DOUBLE) AS qty_ordered,
  CAST(SUM(GREATEST(c.Quantite_restante, 0)) AS DOUBLE) AS qty_open,
  CONCAT_WS(', ', SORT_ARRAY(COLLECT_SET(c.Commande))) AS purch_id, MAX(c.Niveau_engagement) AS commitment
FROM {fqn(W['orders_table'])} c WHERE c.ID IS NOT NULL GROUP BY c.ID, c.Article, c.Code_fournisseur"""),
    "erp_receipts": (["receipt_id", "article_id", "supplier_id", "receipt_date", "qty", "purch_id", "packing_slip", "status"], f"""
SELECT CONCAT_WS('|', r.Code_fournisseur, r.Commande,
                 CASE WHEN LENGTH(COALESCE(r.BL, '')) > 60 THEN LEFT(SHA2(r.BL, 256), 16) ELSE COALESCE(r.BL, '') END,
                 r.Article, DATE_FORMAT(r.Date_reception, 'yyyyMMdd'), COALESCE(r.Statut_reception, '')) AS receipt_id,
  r.Article AS article_id, r.Code_fournisseur AS supplier_id, CAST(r.Date_reception AS DATE) AS receipt_date,
  CAST(r.Quantite_recue AS DOUBLE) AS qty, r.Commande AS purch_id, r.BL AS packing_slip, COALESCE(r.Statut_reception, 'Reçu') AS status
FROM {fqn(W['receipts_table'])} r WHERE r.Quantite_recue IS NOT NULL AND r.Quantite_recue <> 0"""),
}
if W["consumption_table"]:
    QUERIES["erp_consumption_actual"] = (["article_id", "date", "qty"], f"""
SELECT c.article_id AS article_id, CAST(c.date AS DATE) AS date, CAST(SUM(c.qty) AS DOUBLE) AS qty
FROM {fqn(W['consumption_table'])} c WHERE c.article_id IS NOT NULL AND c.date IS NOT NULL AND c.qty IS NOT NULL
GROUP BY c.article_id, CAST(c.date AS DATE)""")
if W["desadv_table"]:
    QUERIES["erp_desadv"] = (["desadv_id", "article_id", "supplier_id", "supplier_name", "packing_slip", "purch_id", "issue_date", "qty", "state", "final_processing", "stock_trans_id"], f"""
SELECT CONCAT_WS('|', CAST(d.Document_ID AS STRING), CAST(d.ID_Ligne AS STRING)) AS desadv_id, d.Code_article AS article_id,
       d.Code_fournisseur AS supplier_id, d.Nom_fournisseur AS supplier_name, CAST(d.BL AS STRING) AS packing_slip,
       CAST(d.Commande_ouverte AS STRING) AS purch_id, CAST(d.Date_emission AS DATE) AS issue_date,
       CAST(d.Quantite_achat AS DOUBLE) AS qty, d.Etat_message AS state, d.Traitement_final AS final_processing,
       CAST(d.ID_transaction_stock AS STRING) AS stock_trans_id
FROM {fqn(W['desadv_table'])} d WHERE d.Code_article IS NOT NULL AND d.Date_emission IS NOT NULL""")
if W["prices_table"]:
    QUERIES["erp_prices"] = (["article_id", "price"], f"""
SELECT a.item_id AS article_id, CAST(MAX(a.std_cost_price) AS DOUBLE) AS price FROM {fqn(W['prices_table'])} a
WHERE a.item_id IS NOT NULL AND a.std_cost_price IS NOT NULL AND a.item_id LIKE 'P-00%' GROUP BY a.item_id""")
if W["bl_pending_table"]:
    QUERIES["erp_bl_pending"] = (["pending_id", "supplier_id", "purch_id", "packing_slip", "article_id", "qty", "registered_date", "days_pending"], f"""
SELECT CONCAT_WS('|', b.Fournisseur, b.Commande, CASE WHEN LENGTH(COALESCE(b.BL_DESADV, '')) > 60 THEN LEFT(SHA2(b.BL_DESADV, 256), 16) ELSE COALESCE(b.BL_DESADV, '') END,
                 b.Article, DATE_FORMAT(b.Date_enregistrement, 'yyyyMMdd')) AS pending_id,
       b.Fournisseur AS supplier_id, b.Commande AS purch_id, b.BL_DESADV AS packing_slip, b.Article AS article_id,
       CAST(b.Quantite AS DOUBLE) AS qty, CAST(b.Date_enregistrement AS DATE) AS registered_date, CAST(b.Jours_en_attente AS INT) AS days_pending
FROM {fqn(W['bl_pending_table'])} b WHERE b.Article IS NOT NULL AND b.Date_enregistrement IS NOT NULL""")
if W["pdp_table"]:
    QUERIES["erp_production_plan"] = (["program_id", "week_start", "qty", "version"], f"""
SELECT p.program_id AS program_id, CAST(p.week_start AS DATE) AS week_start, CAST(p.qty AS DOUBLE) AS qty,
  COALESCE(CAST(p.version AS STRING), '') AS version FROM {fqn(W['pdp_table'])} p""")

# COMMAND ----------

# --- Connexion : hôte et jeton par l'API Lakebase, avec le jeton de la session (indépendant du SDK) ---
ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()
HOST_WS = ctx.apiUrl().get()
TOKEN_WS = ctx.apiToken().get()
USER = ctx.userName().get()
import requests

H = {"Authorization": f"Bearer {TOKEN_WS}"}
ep = W["lakebase_endpoint"]
host = W["pg_host"]
if not host:
    r = requests.get(f"{HOST_WS}/api/2.0/postgres/{ep}", headers=H, timeout=30)
    r.raise_for_status()
    host = r.json().get("status", {}).get("hosts", {}).get("host")
    if not host:
        raise RuntimeError(f"L'endpoint {ep} n'expose pas d'hôte : est-il démarré ? {r.json()}")
r = requests.post(f"{HOST_WS}/api/2.0/postgres/credentials", headers=H, json={"endpoint": ep}, timeout=30)
r.raise_for_status()
token = r.json()["token"]
print(f"Lakebase : hôte {host}, identité {USER}, base {W['pg_database']}")
conn = psycopg.connect(host=host, port=5432, dbname=W["pg_database"], user=USER, password=token, sslmode="require", autocommit=False)

# COMMAND ----------

# --- Contrôles : tables cibles créées par l'application, tables source lisibles -------------------
with conn.cursor() as cur:
    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (W["pg_schema"],))
    present = {x[0] for x in cur.fetchall()}
missing = [t for t in list(QUERIES) + ["erp_sync_log"] if t not in present]
if missing:
    raise RuntimeError(f"Tables absentes de Lakebase : {missing}. Déployer et démarrer l'application APPRO d'abord.")
for t in (W["orders_table"], W["receipts_table"], W["consumption_table"], W["pdp_table"], W["desadv_table"], W["prices_table"], W["bl_pending_table"]):
    if t and not spark.catalog.tableExists(f"{W['erp_catalog']}.{W['erp_schema']}.{t}"):
        raise RuntimeError(f"Table source introuvable : {W['erp_catalog']}.{W['erp_schema']}.{t}")
print("Contrôles OK")

# COMMAND ----------

# --- Publication : DELETE + COPY dans une transaction par table, puis journal ---------------------
results = {}
run_id = json.loads(ctx.toJson()).get("tags", {}).get("jobRunId", "notebook")
today = dt.date.today()
for target, (columns, query) in QUERIES.items():
    df = spark.sql(query)
    missing = [c for c in columns if c not in df.columns]          # first_seen des commandes : posé par le job
    df = df.select(*[c for c in columns if c not in missing])
    n = 0
    with conn.transaction(), conn.cursor() as cur:
        previous = {}
        if target == "erp_purchase_orders" and "first_seen" in missing:
            cur.execute(sql.SQL("SELECT order_id, first_seen FROM {}.{} WHERE first_seen IS NOT NULL").format(sql.Identifier(W["pg_schema"]), sql.Identifier(target)))
            previous = dict(cur.fetchall())
        cur.execute(sql.SQL("DELETE FROM {}.{}").format(sql.Identifier(W["pg_schema"]), sql.Identifier(target)))
        stmt = sql.SQL("COPY {}.{} ({}) FROM STDIN").format(sql.Identifier(W["pg_schema"]), sql.Identifier(target),
                                                            sql.SQL(", ").join(sql.Identifier(c) for c in columns))
        with cur.copy(stmt) as copy:
            for row in df.toLocalIterator():
                d = row.asDict()
                if "first_seen" in missing:
                    d["first_seen"] = previous.get(d.get("order_id")) or today    # conservé d'une copie à l'autre, sinon apparu aujourd'hui
                copy.write_row(tuple(d.get(c) for c in columns))
                n += 1
        cur.execute(sql.SQL("INSERT INTO {}.erp_sync_log (table_name, row_count, synced_at, source, run_id) VALUES (%s, %s, %s, %s, %s) "
                            "ON CONFLICT (table_name) DO UPDATE SET row_count = EXCLUDED.row_count, synced_at = EXCLUDED.synced_at, "
                            "source = EXCLUDED.source, run_id = EXCLUDED.run_id").format(sql.Identifier(W["pg_schema"])),
                    (target, n, dt.datetime.utcnow(), f"{W['erp_catalog']}.{W['erp_schema']}", str(run_id)))
    results[target] = n
    print(f"{target}: {n} ligne(s)")
conn.close()
display(spark.createDataFrame([(k, v) for k, v in results.items()], ["table", "lignes"]))
