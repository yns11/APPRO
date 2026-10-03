"""Process-wide application context: settings, fact source, session factory, reference frames, cache."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings, get_settings
from ..data import reference
from ..data.erp_sql import ErpTables
from ..data.schemas import REFERENCE_TABLES, TABLES, coerce
from ..data.sources import FactSource, LakebaseSource, LocalCsvSource, UnityCatalogSource
from ..data.store import (
    REF_MODELS,
    LakebaseCredentials,
    bump_data_version,
    grant_sync_role,
    init_store,
    make_engine,
    read_data_version,
)

log = logging.getLogger(__name__)


def erp_tables(settings: Settings) -> ErpTables:
    return ErpTables(catalog=settings.uc_catalog, schema=settings.uc_schema, orders=settings.erp_orders_table,
                     receipts=settings.erp_receipts_table, consumption=settings.erp_consumption_table or "",
                     desadv=settings.erp_desadv_table or "", pdp=settings.erp_pdp_table or "")


def build_source(settings: Settings, engine) -> FactSource:
    kind = settings.data_source.lower()
    if kind == "uc":
        if not settings.warehouse_id:
            raise RuntimeError("DATABRICKS_WAREHOUSE_ID is required when APPRO_DATA_SOURCE=uc")
        return UnityCatalogSource(erp_tables(settings), settings.warehouse_id, settings.cache_ttl_seconds)
    if kind == "lakebase":
        return LakebaseSource(engine, settings.cache_ttl_seconds)
    return LocalCsvSource(settings.seed_dir, settings.cache_ttl_seconds)


@dataclass
class AppContext:
    settings: Settings
    source: FactSource
    session_factory: sessionmaker[Session]
    _version: int = 0
    _cache: dict[str, Any] = field(default_factory=dict)
    _ref_cache: dict[str, pd.DataFrame] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _checked_at: float = 0.0

    # ---- data version: bumped on every write so cached MRP results are invalidated.  The counter
    # lives in the database (``app_meta``): the app runs several worker processes, each with its own
    # memory cache, and a write served by one process must invalidate the caches of the others.
    def sync_version(self, session: Session | None = None) -> int:
        """Read the shared counter (with the request session when there is one) and drop the local
        caches when another process bumped it.  Without a session the check is throttled."""
        if session is None:
            if time.monotonic() - self._checked_at < 1.0:
                return self._version
            with self.session() as own:
                v = read_data_version(own)
        else:
            v = read_data_version(session)
        with self._lock:
            self._checked_at = time.monotonic()
            if v != self._version:
                self._version = v
                self._cache.clear()
                self._ref_cache.clear()
        return v

    @property
    def data_version(self) -> int:
        return self.sync_version()

    def bump(self) -> None:
        with self.session() as session:
            v = bump_data_version(session)
        with self._lock:
            self._version, self._checked_at = v, time.monotonic()
            self._cache.clear()
            self._ref_cache.clear()

    def cache_get(self, key: str):
        with self._lock:
            return self._cache.get(key)

    def cache_set(self, key: str, value: Any) -> None:
        with self._lock:
            if len(self._cache) > 64:
                self._cache.clear()
            self._cache[key] = value

    def session(self) -> Session:
        return self.session_factory()

    # ---- canonical frames: reference tables from the store, facts from the source
    def table(self, name: str) -> pd.DataFrame:
        if name not in REFERENCE_TABLES:
            return self.source.table(name)
        self.sync_version()  # other processes may have written the reference tables
        with self._lock:
            hit = self._ref_cache.get(name)
        if hit is not None:
            return hit
        with self.session() as session:
            df = reference.frame(session, name)
        with self._lock:
            self._ref_cache[name] = df
        return df


_context: AppContext | None = None
_context_lock = threading.Lock()


def bootstrap_reference(session_factory: sessionmaker[Session], seed_dir: Path) -> int:
    """Load the seed CSV of every reference table that is still empty. Returns the row count loaded."""
    total = 0
    with session_factory() as session:
        for name in REFERENCE_TABLES:
            if session.scalars(select(REF_MODELS[name]).limit(1)).first() is not None:
                continue
            path = seed_dir / f"{name}.csv"
            if not path.exists():
                continue
            df = coerce(pd.read_csv(path, dtype=str, keep_default_na=False), TABLES[name])
            total += reference.replace_all(session, name, df, user="seed")
        session.commit()
    return total


def get_context() -> AppContext:
    global _context
    if _context is None:
        with _context_lock:
            if _context is None:
                settings = get_settings()
                creds = None
                if settings.resolved_db_url == "lakebase":
                    creds = LakebaseCredentials(settings.lakebase_endpoint, settings.lakebase_branch)
                engine = make_engine(settings.resolved_db_url, creds, settings.db_schema)
                factory = init_store(engine, settings.db_schema)
                grant_sync_role(engine, settings.sync_role or "", settings.db_schema)
                source = build_source(settings, engine)
                if settings.seed_reference_enabled:
                    n = bootstrap_reference(factory, settings.seed_dir)
                    if n:
                        log.info("Référentiel initialisé depuis le seed : %d lignes", n)
                log.info("APPRO context ready: source=%s db=%s", source.name, settings.resolved_db_url.split("@")[-1])
                _context = AppContext(settings=settings, source=source, session_factory=factory)
    return _context


def set_context(ctx: AppContext | None) -> None:
    """Used by tests to inject an isolated context."""
    global _context
    _context = ctx
