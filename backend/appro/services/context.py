"""Process-wide application context: settings, ERP source, session factory, result cache."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from ..config import Settings, get_settings
from ..data.sources import ErpSource, LocalCsvSource, UnityCatalogSource
from ..data.store import init_store

log = logging.getLogger(__name__)


def build_source(settings: Settings) -> ErpSource:
    if settings.data_source.lower() == "uc":
        if not settings.warehouse_id:
            raise RuntimeError("DATABRICKS_WAREHOUSE_ID is required when APPRO_DATA_SOURCE=uc")
        return UnityCatalogSource(settings.uc_catalog, settings.uc_schema, settings.warehouse_id,
                                  settings.cache_ttl_seconds, settings.uc_table_prefix)
    return LocalCsvSource(settings.seed_dir, settings.cache_ttl_seconds)


@dataclass
class AppContext:
    settings: Settings
    source: ErpSource
    session_factory: sessionmaker[Session]
    _version: int = 0
    _cache: dict[str, Any] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    # ---- data version: bumped on every write so cached MRP results are invalidated
    @property
    def data_version(self) -> int:
        return self._version

    def bump(self) -> None:
        with self._lock:
            self._version += 1
            self._cache.clear()

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


_context: AppContext | None = None
_context_lock = threading.Lock()


def get_context() -> AppContext:
    global _context
    if _context is None:
        with _context_lock:
            if _context is None:
                settings = get_settings()
                source = build_source(settings)
                factory = init_store(settings.resolved_db_url)
                log.info("APPRO context ready: source=%s db=%s", source.name, settings.resolved_db_url.split("@")[-1])
                _context = AppContext(settings=settings, source=source, session_factory=factory)
    return _context


def set_context(ctx: AppContext | None) -> None:
    """Used by tests to inject an isolated context."""
    global _context
    _context = ctx
