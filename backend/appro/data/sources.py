"""ERP / reference data sources.

Two implementations of the same :class:`ErpSource` protocol:

* :class:`LocalCsvSource` – reads the seed CSV files (``data/seed``) – used for local
  development, tests and demos;
* :class:`UnityCatalogSource` – runs SQL on a Databricks SQL warehouse against the
  ``<catalog>.<schema>`` tables created by ``scripts/uc/create_tables.sql``.

Both return pandas frames coerced to the canonical schemas of :mod:`appro.data.schemas`.
Frames are cached in memory for ``cache_ttl_seconds`` (the ERP data changes a few times a day).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Protocol

import pandas as pd

from .schemas import TABLES, coerce

log = logging.getLogger(__name__)


class ErpSource(Protocol):
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
        df = coerce(self._load(name), TABLES[name])
        with self._lock:
            self._cache[name] = (now, df)
        return df

    def refresh(self) -> None:
        with self._lock:
            self._cache.clear()

    def describe(self) -> dict:
        return {"name": self.name, "cached_tables": sorted(self._cache)}


class LocalCsvSource(_CachedSource):
    """Seed CSV files (one file per canonical table)."""

    name = "local-csv"

    def __init__(self, folder: str | os.PathLike, cache_ttl_seconds: float = 300.0) -> None:
        super().__init__(cache_ttl_seconds)
        self.folder = Path(folder)
        if not self.folder.exists():
            raise FileNotFoundError(f"Seed folder not found: {self.folder}")

    def _load(self, name: str) -> pd.DataFrame:
        path = self.folder / f"{name}.csv"
        if not path.exists():
            return pd.DataFrame(columns=list(TABLES[name].columns))
        return pd.read_csv(path, dtype=str, keep_default_na=False)

    def describe(self) -> dict:
        d = super().describe()
        d.update({"folder": str(self.folder)})
        return d


class UnityCatalogSource(_CachedSource):
    """Databricks SQL warehouse source (service-principal auth through the SDK ``Config``)."""

    name = "unity-catalog"

    def __init__(self, catalog: str, schema: str, warehouse_id: str, cache_ttl_seconds: float = 300.0,
                 table_prefix: str = "") -> None:
        super().__init__(cache_ttl_seconds)
        self.catalog, self.schema, self.warehouse_id, self.prefix = catalog, schema, warehouse_id, table_prefix
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

    def fqn(self, name: str) -> str:
        return f"`{self.catalog}`.`{self.schema}`.`{self.prefix}{name}`"

    def _load(self, name: str) -> pd.DataFrame:
        cols = ", ".join(f"`{c}`" for c in TABLES[name].columns)
        query = f"SELECT {cols} FROM {self.fqn(name)}"
        log.info("UC query: %s", query)
        try:
            with self._connection().cursor() as cur:
                cur.execute(query)
                rows = cur.fetchall_arrow().to_pandas()
        except Exception:
            # drop the connection so that the next call reconnects
            with self._conn_lock:
                self._conn = None
            raise
        return rows

    def describe(self) -> dict:
        d = super().describe()
        d.update({"catalog": self.catalog, "schema": self.schema, "warehouse_id": self.warehouse_id})
        return d
