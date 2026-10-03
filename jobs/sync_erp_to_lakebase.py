"""Job Databricks : copie des faits ERP (Unity Catalog) vers les tables ``erp_*`` de Lakebase.

Pour chaque table de faits configurée (commandes, réceptions, consommation réelle, PDP) :

1. le SQL de correspondance de ``backend/appro/data/erp_sql.py`` est exécuté par Spark sur les
   extractions (``commandes_edi``, ``recep_edi``…) ;
2. les lignes sont écrites dans la table ``erp_<nom>`` de la base applicative, **dans une seule
   transaction** : ``DELETE`` puis ``COPY``. Les lecteurs (l'application) voient l'ancienne copie
   jusqu'au commit ; une exécution interrompue laisse la copie précédente intacte ;
3. ``erp_sync_log`` reçoit l'horodatage et le nombre de lignes (affiché dans /api/health).

Les tables ``erp_*`` sont **créées par l'application** à son démarrage (elle en est propriétaire)
et le droit d'y écrire est accordé au rôle ``APPRO_SYNC_ROLE`` par l'application elle-même :
déployer et démarrer l'application **avant** la première exécution du job.

Exécution ::

    databricks bundle run appro_sync_erp -t dev --profile <PROFIL>
    # ou, en local contre un Postgres (PGHOST / PGUSER / PGPASSWORD) :
    python jobs/sync_erp_to_lakebase.py --catalog … --schema … --branch … --endpoint …
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path


def _amorcer_chemin_projet() -> Path:
    """Place la racine du dépôt (et backend/) dans ``sys.path``.

    Une tâche ``spark_python_task`` évalue le fichier sans la racine du bundle dans ``sys.path`` ;
    sur calcul serverless ``__file__`` peut manquer.  Volontairement dupliqué, non importé : une
    fonction partagée devrait elle-même être importée, ce qui est précisément ce qui échoue avant.
    """
    candidats: list[Path] = []
    fichier = globals().get("__file__")
    if fichier:
        candidats.append(Path(fichier).resolve())
    try:
        import inspect
        candidats.append(Path(inspect.currentframe().f_code.co_filename).resolve())  # type: ignore[union-attr]
    except Exception:
        pass
    if sys.argv and sys.argv[0]:
        candidats.append(Path(sys.argv[0]).resolve())
    candidats.append(Path.cwd().resolve())
    for candidat in candidats:
        for base in (candidat, *candidat.parents):
            if (base / "backend" / "appro" / "data" / "erp_sql.py").is_file():
                for p in (str(base), str(base / "backend"), str(base / "jobs")):
                    if p not in sys.path:
                        sys.path.insert(0, p)
                return base
    raise RuntimeError("Racine du projet introuvable : le bundle a-t-il été synchronisé en entier ? "
                       f"(pistes : {[str(c) for c in candidats]})")


RACINE = _amorcer_chemin_projet()

from lakebase import conninfo  # noqa: E402

from appro.data.erp_sql import ErpTables, fact_queries  # noqa: E402
from appro.data.schemas import TABLES  # noqa: E402

LOGGER = logging.getLogger("appro.sync")
LOG_EVERY = 50_000


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalog", default="emotors_data_champions")
    p.add_argument("--schema", default="silver_erp_ye")
    p.add_argument("--orders-table", default="commandes_edi")
    p.add_argument("--receipts-table", default="recep_edi")
    p.add_argument("--consumption-table", default="", help="table UC de consommation réelle par composant (article_id, date, qty) ; vide = aucune")
    p.add_argument("--pdp-table", default="")
    p.add_argument("--desadv-table", default="desadv_edi", help="table UC des avis d'expédition (DESADV) ; vide = aucune")
    p.add_argument("--branch", default="", help="projects/<projet>/branches/<branche>")
    p.add_argument("--endpoint", default="", help="projects/<projet>/branches/<branche>/endpoints/<endpoint>")
    p.add_argument("--pg-host", default="", help="hôte Lakebase si la découverte est impossible")
    p.add_argument("--pg-user", default="")
    p.add_argument("--pg-database", default="databricks_postgres")
    p.add_argument("--pg-schema", default="appro", help="schéma Postgres de l'application (APPRO_DB_SCHEMA)")
    p.add_argument("--tables", default="", help="sous-ensemble (fct_purchase_orders,fct_receipts…) ; vide = toutes")
    p.add_argument("--run-id", default="")
    return p


def _spark():
    try:
        from pyspark.sql import SparkSession
        return SparkSession.builder.getOrCreate()
    except Exception:  # pragma: no cover - local run with Databricks Connect
        from databricks.connect import DatabricksSession
        return DatabricksSession.builder.getOrCreate()


def _table_exists(spark, fqn: str) -> bool:
    try:
        return spark.catalog.tableExists(fqn.replace("`", ""))
    except Exception:
        return False


def _check_sources(spark, tables: ErpTables) -> None:
    """Fail once, naming every missing source, rather than one table per run."""
    missing = [fqn for fqn in (tables.fqn(tables.orders), tables.fqn(tables.receipts)) if not _table_exists(spark, fqn)]
    for opt in (tables.consumption, tables.pdp, tables.desadv):
        if opt and not _table_exists(spark, tables.fqn(opt)):
            missing.append(tables.fqn(opt))
    if missing:
        raise RuntimeError("Tables source introuvables (catalogue / schéma / droits SELECT) : " + ", ".join(missing))


def _check_targets(conn, pg_schema: str, names: list[str]) -> None:
    from psycopg import sql
    with conn.cursor() as cur:
        cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = %s", (pg_schema,))
        present = {r[0] for r in cur.fetchall()}
    missing = [n for n in names + ["erp_sync_log"] if n not in present]
    if missing:
        raise RuntimeError("Tables cibles absentes de Lakebase : " + ", ".join(missing) +
                           ". C'est l'application qui crée les tables erp_* à son démarrage et accorde l'écriture au rôle "
                           "APPRO_SYNC_ROLE : déployer et démarrer l'application d'abord (une version qui connaît ces tables), "
                           "vérifier /api/health, puis relancer le job.")
    _ = sql  # imported for symmetry with the writers


def publish(conn, spark, query: str, name: str, pg_schema: str, run_id: str, source: str) -> int:
    """One fact table: DELETE + COPY in a single transaction, then the sync log."""
    from psycopg import sql
    columns = list(TABLES[name].column_names)
    target = f"erp_{name[4:]}"
    LOGGER.info("[%s] lecture (%s)", target, source)
    df = spark.sql(query).select(*columns)
    started = dt.datetime.now(dt.timezone.utc)
    written = 0
    with conn.transaction(), conn.cursor() as cur:
        cur.execute(sql.SQL("DELETE FROM {}.{}").format(sql.Identifier(pg_schema), sql.Identifier(target)))
        stmt = sql.SQL("COPY {}.{} ({}) FROM STDIN").format(sql.Identifier(pg_schema), sql.Identifier(target),
                                                            sql.SQL(", ").join(sql.Identifier(c) for c in columns))
        with cur.copy(stmt) as copy:
            for row in df.toLocalIterator():
                copy.write_row(tuple(row))
                written += 1
                if written % LOG_EVERY == 0:
                    LOGGER.info("[%s] %d lignes…", target, written)
        cur.execute(sql.SQL(
            "INSERT INTO {}.erp_sync_log (table_name, row_count, synced_at, source, run_id) VALUES (%s, %s, %s, %s, %s) "
            "ON CONFLICT (table_name) DO UPDATE SET row_count = EXCLUDED.row_count, synced_at = EXCLUDED.synced_at, "
            "source = EXCLUDED.source, run_id = EXCLUDED.run_id").format(sql.Identifier(pg_schema)),
            (target, written, dt.datetime.now(dt.timezone.utc).replace(tzinfo=None), source, run_id))
    LOGGER.info("[%s] %d ligne(s) publiées en %.1f s", target, written, (dt.datetime.now(dt.timezone.utc) - started).total_seconds())
    return written


def run(args: argparse.Namespace) -> dict[str, int]:
    import psycopg
    tables = ErpTables(catalog=args.catalog, schema=args.schema, orders=args.orders_table, receipts=args.receipts_table,
                       consumption=args.consumption_table or "", desadv=args.desadv_table or "", pdp=args.pdp_table or "")
    queries = fact_queries(tables)
    wanted = {t.strip() for t in args.tables.split(",") if t.strip()}
    if wanted:
        unknown = wanted - set(queries)
        if unknown:
            raise RuntimeError(f"Tables inconnues : {sorted(unknown)} ; connues : {sorted(queries)}")
        queries = {k: v for k, v in queries.items() if k in wanted}
    spark = _spark()
    _check_sources(spark, tables)
    info = conninfo(args.endpoint or None, args.branch or None, args.pg_database, args.pg_host or None, args.pg_user or None)
    results: dict[str, int] = {}
    with psycopg.connect(info, autocommit=False) as conn:
        _check_targets(conn, args.pg_schema, [f"erp_{n[4:]}" for n in queries])
        for name, query in queries.items():
            src = {"fct_purchase_orders": tables.fqn(tables.orders), "fct_receipts": tables.fqn(tables.receipts),
                   "fct_consumption_actual": tables.fqn(tables.consumption), "fct_production_plan": tables.fqn(tables.pdp),
                   "fct_desadv": tables.fqn(tables.desadv)}[name]
            results[name] = publish(conn, spark, query, name, args.pg_schema, args.run_id, src.replace("`", ""))
    return results


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_arg_parser().parse_args()
    results = run(args)
    LOGGER.info("Synchronisation terminée : %s", results)
    # pas de sortie forcée ici : le noyau de la tâche traite une sortie explicite comme un échec, même avec le code 0


if __name__ == "__main__":
    main()
