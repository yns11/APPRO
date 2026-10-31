"""Process-wide application context: settings, fact source, session factory, reference frames, cache."""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from collections import OrderedDict
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
    PdpLine,
    PdpVersion,
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
                     desadv=settings.erp_desadv_table or "",
                     prices=settings.erp_prices_table or "", stock=settings.erp_stock_table or "",
                     stock_warehouses=settings.stock_warehouse_list, stock_history_days=settings.stock_history_days)


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
    _cache: "OrderedDict[str, tuple[Any, int]]" = field(default_factory=OrderedDict)   # key → (value, bytes), least recently used first
    _cache_bytes: int = 0
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
                self._cache_bytes = 0
                self._ref_cache.clear()
        return v

    @property
    def data_version(self) -> int:
        return self.sync_version()

    def bump(self) -> None:
        """Invalidate every cache after a change made outside a request transaction (ERP refresh)."""
        with self.session() as session:
            v = bump_data_version(session)
        self._set_version(v)

    def commit(self, session: Session) -> int:
        """Validate the writes of a request **and** the version bump in the same transaction : a failure
        between the two can no longer leave new data with an old version (the other processes would keep
        serving their stale cache).  Returns the new version."""
        v = bump_data_version(session, commit=False)
        session.commit()
        self._set_version(v)
        return v

    def _set_version(self, v: int) -> None:
        with self._lock:
            self._version, self._checked_at = v, time.monotonic()
            self._cache.clear()
            self._cache_bytes = 0
            self._ref_cache.clear()

    def cache_get(self, key: str):
        with self._lock:
            hit = self._cache.get(key)
            if hit is None:
                return None
            self._cache.move_to_end(key)
            return hit[0]

    @property
    def cache_budget(self) -> int:
        return max(int(getattr(self.settings, "cache_budget_mb", 256)), 1) * 1024 * 1024

    def cache_set(self, key: str, value: Any, size: int | None = None) -> None:
        """Keep the result under a **memory budget** (``APPRO_CACHE_BUDGET_MB``) : the least recently used results
        leave first ; a single result larger than the budget is served but not kept."""
        size = int(size if size is not None else estimate_size(value))
        with self._lock:
            old = self._cache.pop(key, None)
            if old is not None:
                self._cache_bytes -= old[1]
            if size > self.cache_budget:
                return
            while self._cache and self._cache_bytes + size > self.cache_budget:
                _k, (_v, b) = self._cache.popitem(last=False)
                self._cache_bytes -= b
            self._cache[key] = (value, size)
            self._cache_bytes += size

    def cache_stats(self) -> dict[str, int]:
        with self._lock:
            return {"entries": len(self._cache), "bytes": self._cache_bytes, "budget": self.cache_budget}

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


def estimate_size(value: Any) -> int:
    """Rough memory footprint of a cached engine result : the NumPy arrays of every article (the bulk), plus a fixed
    allowance per article for the Python objects (lanes, orders, alerts, proposals)."""
    import numpy as np
    total = 4096
    arts = getattr(value, "articles", None)
    if isinstance(arts, dict):
        for ar in arts.values():
            total += 16_384
            for v in ar.__dict__.values():
                if isinstance(v, np.ndarray):
                    total += int(v.nbytes)
                elif isinstance(v, list):
                    for x in v:
                        for w in getattr(x, "__dict__", {}).values():
                            if isinstance(w, np.ndarray):
                                total += int(w.nbytes)
                        total += 256
    return total


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
        total += seed_pdp_version(session, seed_dir)
        session.commit()
    return total


def seed_pdp_version(session: Session, seed_dir: Path) -> int:
    """The production plan of the demonstration set is an imported version like any other (there is no
    ERP plan) : ``pdp_seed.csv`` (program_id, week_start, qty) becomes the active version when the
    application holds none yet."""
    path = seed_dir / "pdp_seed.csv"
    if not path.exists() or session.scalars(select(PdpVersion).limit(1)).first() is not None:
        return 0
    df = pd.read_csv(path, dtype=str, keep_default_na=False)
    version = PdpVersion(name="PDP du jeu de démonstration", source_file=path.name, note="chargé au premier démarrage",
                         imported_by="seed", active=True)
    n = 0
    for r in df.to_dict("records"):
        if not r.get("program_id") or not r.get("week_start"):
            continue
        version.lines.append(PdpLine(program_id=str(r["program_id"]), week_start=dt.date.fromisoformat(str(r["week_start"])[:10]),
                                     qty=float(r["qty"] or 0)))
        n += 1
    session.add(version)
    return n


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
