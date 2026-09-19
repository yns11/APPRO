#!/usr/bin/env python3
"""Load the CSV seed (data/seed) into the Unity Catalog tables – demo / bootstrap only.

Run from a Databricks notebook / job (Spark available) or locally with Databricks Connect::

    python scripts/uc/load_seed.py --catalog main --schema appro [--seed data/seed]

The script creates the tables when needed (scripts/uc/create_tables.sql) and overwrites their
content with the seed.  In production the tables are fed by the ERP integration pipelines.
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--catalog", default="main")
    ap.add_argument("--schema", default="appro")
    ap.add_argument("--seed", default=str(ROOT / "data" / "seed"))
    args = ap.parse_args()
    try:
        from pyspark.sql import SparkSession
        spark = SparkSession.builder.getOrCreate()
    except Exception:  # pragma: no cover
        from databricks.connect import DatabricksSession
        spark = DatabricksSession.builder.getOrCreate()

    ddl = (ROOT / "scripts" / "uc" / "create_tables.sql").read_text()
    ddl = ddl.replace("${catalog}", args.catalog).replace("${schema}", args.schema)
    for stmt in [s.strip() for s in ddl.split(";") if s.strip() and not s.strip().startswith("--")]:
        cleaned = "\n".join(l for l in stmt.splitlines() if not l.strip().startswith("--"))
        if cleaned.strip():
            spark.sql(cleaned)

    import pandas as pd
    for csv in sorted(Path(args.seed).glob("*.csv")):
        name = csv.stem
        if not re.fullmatch(r"(ref|fct)_[a-z_]+", name):
            continue
        pdf = pd.read_csv(csv, dtype=str, keep_default_na=False)
        target = f"{args.catalog}.{args.schema}.{name}"
        schema = spark.table(target).schema
        sdf = spark.createDataFrame(pdf)
        for f in schema.fields:
            if f.name in sdf.columns:
                sdf = sdf.withColumn(f.name, sdf[f.name].cast(f.dataType))
        sdf.select([f.name for f in schema.fields if f.name in sdf.columns]).write.mode("overwrite").saveAsTable(target)
        print(f"loaded {len(pdf):5d} rows -> {target}")


if __name__ == "__main__":
    main()
