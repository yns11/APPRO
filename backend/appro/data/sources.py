"""ERP fact sources.

Three implementations of the same :class:`FactSource` protocol return the canonical fact frames
(:data:`appro.data.schemas.FACT_TABLES`):

* :class:`LocalCsvSource` – the seed CSV files (``data/seed``), for development, tests and demos ;
* :class:`UnityCatalogSource` – runs the mapping SQL of :mod:`appro.data.erp_sql` on a Databricks
  SQL warehouse against the ERP extractions (``commandes_edi``, ``recep_edi``…) ;
* :class:`LakebaseSource` – reads the ``erp_*`` mirror tables of the application database, filled
  by the synchronisation job (``jobs/sync_erp_to_lakebase.py``).

Frames are cached in memory for ``cache_ttl_seconds`` (the ERP data changes a few times a day).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Protocol

import pandas as pd

from .erp_sql import ErpTables, fact_queries
from .schemas import FACT_TABLES, TABLES, coerce

log = logging.getLogger(__name__)


class FactSource(Protocol):
    name: str

    def table(self, name: str) -> pd.DataFrame: ...
    def refresh(self) -> None: ...
    def describe(self) -> dict: ...


class _CachedSource:
    """Shared caching behaviour."""

    name = "base"

    def __init__(self, cache_ttl_seconds: float = 300.0) -> None:
        self._ttl = cache_ttl_seconds
        self._cache: dict[str, tuple[float, pd.DataFrame]] = {}
        self._lock = threading.Lock()
        self.last_error: str | None = None

    def _load(self, name: str) -> pd.DataFrame:  # pragma: no cover - abstract
        raise NotImplementedError

    def table(self, name: str) -> pd.DataFrame:
        if name not in TABLES:
            raise KeyError(f"Unknown table {name!r}")
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(name)
            if hit and now - hit[0] < self._ttl:
                return hit[1]
        try:
            df = coerce(self._load(name), TABLES[name])
            self.last_error = None
        except Exception as exc:
            self.last_error = f"{name}: {type(exc).__name__}: {exc}"
            raise
        with self._lock:
            self._cache[name] = (now, df)
        return df

    def refresh(self) -> None:
        with self._lock:
            self._cache.clear()

    def describe(self) -> dict:
        return {"name": self.name, "cached_tables": sorted(self._cache), "last_error": self.last_error}


class LocalCsvSource(_CachedSource):
    """Seed CSV files (one file per canonical table, facts and reference alike)."""

    name = "local-csv"

    def __init__(self, folder: str | os.PathLike, cache_ttl_seconds: float = 300.0) -> None:
        super().__init__(cache_ttl_seconds)
        self.folder = Path(folder)
        if not self.folder.exists():
            raise FileNotFoundError(f"Seed folder not found: {self.folder}")

    def _load(self, name: str) -> pd.DataFrame:
        path = self.folder / f"{name}.csv"
        if not path.exists():
            return pd.DataFrame(columns=list(TABLES[name].column_names))
        return pd.read_csv(path, dtype=str, keep_default_na=False)

    def describe(self) -> dict:
        d = super().describe()
        d.update({"folder": str(self.folder)})
        return d


class UnityCatalogSource(_CachedSource):
    """Databricks SQL warehouse source (service-principal auth through the SDK ``Config``)."""

    name = "unity-catalog"

    def __init__(self, tables: ErpTables, warehouse_id: str, cache_ttl_seconds: float = 300.0) -> None:
        super().__init__(cache_ttl_seconds)
        self.tables, self.warehouse_id = tables, warehouse_id
        self.queries = fact_queries(tables)
        self._conn = None
        self._conn_lock = threading.Lock()

    def _connection(self):
        if self._conn is None:
            with self._conn_lock:
                if self._conn is None:
                    from databricks import sql  # imported lazily: not needed locally
                    from databricks.sdk.core import Config
                    cfg = Config()
                    self._conn = sql.connect(
                        server_hostname=cfg.host,
                        http_path=f"/sql/1.0/warehouses/{self.warehouse_id}",
                        credentials_provider=lambda: cfg.authenticate,
                    )
        return self._conn

    def _load(self, name: str) -> pd.DataFrame:
        query = self.queries.get(name)
        if query is None:
            return pd.DataFrame(columns=list(TABLES[name].column_names))
        log.info("UC query for %s", name)
        try:
            with self._connection().cursor() as cur:
                cur.execute(query)
                return cur.fetchall_arrow().to_pandas()
        except Exception:
            with self._conn_lock:
                self._conn = None   # reconnect on the next call
            raise

    def describe(self) -> dict:
        d = super().describe()
        d.update({"catalog": self.tables.catalog, "schema": self.tables.schema, "warehouse_id": self.warehouse_id,
                  "tables": {k: v for k, v in self.tables.__dict__.items() if k not in ("catalog", "schema")}})
        return d


class LakebaseSource(_CachedSource):
    """The ``erp_*`` mirror tables of the application database (same engine as the store)."""

    name = "lakebase"

    def __init__(self, engine: Any, cache_ttl_seconds: float = 300.0) -> None:
        super().__init__(cache_ttl_seconds)
        self.engine = engine

    def _load(self, name: str) -> pd.DataFrame:
        if name not in FACT_TABLES:
            return pd.DataFrame(columns=list(TABLES[name].column_names))
        from sqlalchemy import text
        cols = ", ".join(TABLES[name].column_names)
        with self.engine.connect() as conn:
            return pd.read_sql(text(f"SELECT {cols} FROM erp_{name[4:]}"), conn)

    def sync_status(self) -> list[dict]:
        from sqlalchemy import text
        try:
            with self.engine.connect() as conn:
                rows = conn.execute(text("SELECT table_name, row_count, synced_at, source, run_id FROM erp_sync_log "
                                         "ORDER BY table_name")).mappings().all()
            return [dict(r) for r in rows]
        except Exception as exc:  # table missing before the first sync
            return [{"error": str(exc)}]

    def describe(self) -> dict:
        d = super().describe()
        d.update({"sync": self.sync_status()})
        return d
