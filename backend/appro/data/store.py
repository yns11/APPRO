"""Application database (SQLAlchemy 2.0): reference tables, planner cells, PDP versions,
parameters, ERP mirror, audit log.

Locally the store is a SQLite file ; on Databricks Apps it is the **Lakebase** (PostgreSQL) project
attached to the app as a ``postgres`` resource.  Schema creation is idempotent
(``Base.metadata.create_all``) so the app boots on an empty database.

Lakebase specifics (see docs/deploiement.md § 7) : the password is an OAuth token that lives one
hour.  It is therefore generated **at each physical connection** (``do_connect`` event) from a
cached credential refreshed before it expires, never frozen in the engine URL.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
import time
import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    create_engine,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .schemas import FACT_TABLES, REFERENCE_TABLES, TABLES, TableSchema

log = logging.getLogger(__name__)


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


# =============================================================================
# Reference tables and ERP mirror: one ORM class per canonical schema
# =============================================================================
_SQL_TYPES = {"str": String(200), "float": Float, "int": Integer, "bool": Boolean, "date": Date}


def _model(schema: TableSchema, table_name: str, audited: bool) -> type[Base]:
    attrs: dict[str, Any] = {"__tablename__": table_name}
    ann: dict[str, Any] = {}
    for c in schema.columns:
        pk = c.name in schema.key
        col = mapped_column(_SQL_TYPES[c.type], primary_key=pk, nullable=not pk)
        attrs[c.name] = col
        ann[c.name] = Mapped[Any]
    if audited:
        attrs["updated_by"] = mapped_column(String(120), default="")
        attrs["updated_at"] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
        ann["updated_by"] = Mapped[str]
        ann["updated_at"] = Mapped[dt.datetime]
    attrs["__annotations__"] = ann
    return type("".join(p.title() for p in table_name.split("_")), (Base,), attrs)


REF_MODELS: dict[str, type[Base]] = {name: _model(TABLES[name], name, True) for name in REFERENCE_TABLES}
ERP_MODELS: dict[str, type[Base]] = {name: _model(TABLES[name], f"erp_{name[4:]}", False) for name in FACT_TABLES}


class ErpSyncLog(Base):
    """One row per mirrored fact table: when and how many rows the synchronisation job wrote."""

    __tablename__ = "erp_sync_log"
    table_name: Mapped[str] = mapped_column(String(60), primary_key=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    synced_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(255), default="")
    run_id: Mapped[str] = mapped_column(String(80), default="")


# =============================================================================
# Planner entries: the two editable rows of the grid
# =============================================================================
class AppAdjustment(Base):
    """One adjustment cell (article × day): signed quantity or arithmetic expression, note."""

    __tablename__ = "app_adjustments"
    __table_args__ = (Index("ix_app_adjustments_key", "article_id", "date", unique=True),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("AJ"))
    article_id: Mapped[str] = mapped_column(String(40), index=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    expression: Mapped[str] = mapped_column(String(200), default="")
    qty: Mapped[float] = mapped_column(Float)
    note: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(120), default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AppPlanCell(Base):
    """One plan cell (article × supplier × day): the planned delivery quantity, note.
    ``supplier_id`` is ``""`` for an article without supplier."""

    __tablename__ = "app_plan_cells"
    __table_args__ = (Index("ix_app_plan_cells_key", "article_id", "supplier_id", "date", unique=True),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("PL"))
    article_id: Mapped[str] = mapped_column(String(40), index=True)
    supplier_id: Mapped[str] = mapped_column(String(40), default="")
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    expression: Mapped[str] = mapped_column(String(200), default="")
    qty: Mapped[float] = mapped_column(Float)
    note: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(120), default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AppCellFlag(Base):
    """A click on a read-only cell of the grid: ``order_ignored`` (article × supplier × day) or
    ``proposal_refused`` (article × day, ``supplier_id`` empty) ; ``qty`` = refused quantity shown."""

    __tablename__ = "app_cell_flags"
    __table_args__ = (Index("ix_app_cell_flags_key", "kind", "article_id", "supplier_id", "date", unique=True),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("FL"))
    kind: Mapped[str] = mapped_column(String(24))
    article_id: Mapped[str] = mapped_column(String(40), index=True)
    supplier_id: Mapped[str] = mapped_column(String(40), default="")
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    qty: Mapped[float] = mapped_column(Float, default=0.0)
    note: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(120), default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


# =============================================================================
# PDP versions, parameters, audit
# =============================================================================
class PdpVersion(Base):
    """A production plan imported from Excel (one active version at most)."""

    __tablename__ = "app_pdp_versions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("PDP"))
    name: Mapped[str] = mapped_column(String(120))
    source_file: Mapped[str] = mapped_column(String(255), default="")
    note: Mapped[str] = mapped_column(Text, default="")
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    imported_by: Mapped[str] = mapped_column(String(120), default="")
    imported_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    lines: Mapped[list["PdpLine"]] = relationship(back_populates="version", cascade="all, delete-orphan")


class PdpLine(Base):
    __tablename__ = "app_pdp_lines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    version_id: Mapped[str] = mapped_column(ForeignKey("app_pdp_versions.id"), index=True)
    program_id: Mapped[str] = mapped_column(String(40), index=True)
    week_start: Mapped[dt.date] = mapped_column(Date)
    qty: Mapped[float] = mapped_column(Float)
    version: Mapped["PdpVersion"] = relationship(back_populates="lines")


class ParamOverride(Base):
    """Engine rules (``global``) and per-ISO-week article parameters (``article_week``)."""

    __tablename__ = "app_param_overrides"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("PO"))
    scope: Mapped[str] = mapped_column(String(12))          # global | article_week
    key1: Mapped[str] = mapped_column(String(60), default="")  # article_id
    key2: Mapped[str] = mapped_column(String(60), default="")  # ISO week, e.g. 2026-W40
    field: Mapped[str] = mapped_column(String(60))
    value: Mapped[str] = mapped_column(String(255))
    updated_by: Mapped[str] = mapped_column(String(120), default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (Index("ix_param_scope_keys", "scope", "key1", "key2", "field", unique=True),)


class AuditLog(Base):
    __tablename__ = "app_audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    user: Mapped[str] = mapped_column(String(120), default="", index=True)
    action: Mapped[str] = mapped_column(String(40))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(120), default="")
    article_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json or "{}")


def audit(session: Session, user: str, action: str, entity_type: str, entity_id: str = "",
          article_id: str | None = None, payload: dict | None = None) -> None:
    session.add(AuditLog(user=user, action=action, entity_type=entity_type, entity_id=entity_id,
                         article_id=article_id, payload_json=json.dumps(payload or {}, default=str)))


# =============================================================================
# Lakebase credentials (token generated at each physical connection)
# =============================================================================
#: Variables the platform injects when a ``postgres`` resource is attached, plus the ones the
#: manifest must request (``LAKEBASE_ENDPOINT``) – names only, exposed by ``/api/health``.
LAKEBASE_ENV = ("PGHOST", "PGPORT", "PGDATABASE", "PGUSER", "PGSSLMODE", "PGPASSWORD", "LAKEBASE_ENDPOINT",
                "APPRO_LAKEBASE_BRANCH")
REST_CREDENTIALS = "/api/2.0/postgres/credentials"
REST_ENDPOINTS = "/api/2.0/postgres/{branch}/endpoints"


def lakebase_env_status() -> dict[str, list[str]]:
    present = [k for k in LAKEBASE_ENV if os.environ.get(k)]
    return {"present": present, "absent": [k for k in LAKEBASE_ENV if k not in present]}


class LakebaseCredentials:
    """Cached OAuth token for Lakebase, regenerated before it expires (1 h)."""

    def __init__(self, endpoint: str | None, branch: str | None, refresh_seconds: int = 1800) -> None:
        self.endpoint, self.branch, self.refresh = endpoint, branch, refresh_seconds
        self._lock = threading.Lock()
        self._token: str | None = None
        self._obtained = 0.0
        self._client: Any = None

    def _workspace(self):
        if self._client is None:
            from databricks.sdk import WorkspaceClient
            self._client = WorkspaceClient()
        return self._client

    def _resolve_endpoint(self) -> str:
        if self.endpoint:
            return self.endpoint
        if not self.branch:
            raise RuntimeError("Lakebase : ni LAKEBASE_ENDPOINT ni APPRO_LAKEBASE_BRANCH ne sont renseignés ; "
                               "impossible de générer un identifiant")
        w = self._workspace()
        host = (os.environ.get("PGHOST") or "").lower()
        api = getattr(w, "postgres", None)
        endpoints: list[Any] = []
        if api is not None and hasattr(api, "list_endpoints"):
            endpoints = list(api.list_endpoints(self.branch))
        else:
            body = w.api_client.do("GET", REST_ENDPOINTS.format(branch=self.branch)) or {}
            endpoints = body.get("endpoints", [])
        fallback = None
        for ep in endpoints:
            name = getattr(ep, "name", None) or (ep.get("name") if isinstance(ep, dict) else None)
            status = getattr(ep, "status", None) or (ep.get("status", {}) if isinstance(ep, dict) else {})
            hosts = getattr(status, "hosts", None) or (status.get("hosts", {}) if isinstance(status, dict) else {})
            values = [str(getattr(hosts, a, "") or (hosts.get(a, "") if isinstance(hosts, dict) else "")).lower()
                      for a in ("host", "read_write_pooled_host", "read_only_host", "read_only_pooled_host")]
            if host and host in values:
                self.endpoint = name
                return name
            if fallback is None and "READ_WRITE" in str(getattr(status, "endpoint_type", "") or
                                                           (status.get("endpoint_type") if isinstance(status, dict) else "")):
                fallback = name
        if fallback:
            log.warning("PGHOST %s absent des endpoints de %s : repli sur %s", host, self.branch, fallback)
            self.endpoint = fallback
            return fallback
        raise RuntimeError(f"Aucun endpoint de {self.branch} ne porte l'hôte {host}")

    def _generate(self) -> str:
        endpoint = self._resolve_endpoint()
        w = self._workspace()
        api = getattr(w, "postgres", None)
        if api is not None and hasattr(api, "generate_database_credential"):
            try:
                return api.generate_database_credential(endpoint=endpoint).token
            except TypeError:
                return api.generate_database_credential(endpoint).token
        body = w.api_client.do("POST", REST_CREDENTIALS, body={"endpoint": endpoint}) or {}
        token = body.get("token")
        if not token:
            raise RuntimeError(f"Réponse sans jeton de {REST_CREDENTIALS} : {sorted(body)}")
        return token

    def token(self) -> str:
        with self._lock:
            if self._token is None or time.monotonic() - self._obtained > self.refresh:
                self._token = self._generate()
                self._obtained = time.monotonic()
                log.info("Jeton Lakebase généré (validité ~1 h, renouvelé après %d s)", self.refresh)
            return self._token

    def invalidate(self) -> None:
        with self._lock:
            self._token = None


def _search_path(schema: str | None) -> dict[str, Any]:
    """psycopg ``connect_args`` placing the app schema first (``public`` stays visible)."""
    if not schema or schema == "public":
        return {}
    return {"options": f"-c search_path={schema},public"}


def make_engine(url: str, credentials: LakebaseCredentials | None = None, schema: str | None = None):
    """SQLAlchemy engine. ``url == "lakebase"`` builds the PostgreSQL engine from the injected
    ``PG*`` variables ; the password is provided per connection by ``credentials`` (or ``PGPASSWORD``).
    On PostgreSQL every connection works in ``schema`` (see ``init_store``)."""
    if url == "lakebase":
        host = os.environ["PGHOST"]
        db = os.environ.get("PGDATABASE", "databricks_postgres")
        user = os.environ.get("PGUSER") or os.environ.get("DATABRICKS_CLIENT_ID", "")
        port = os.environ.get("PGPORT", "5432")
        sslmode = os.environ.get("PGSSLMODE", "require")
        from urllib.parse import quote_plus
        engine = create_engine(f"postgresql+psycopg://{quote_plus(user)}@{host}:{port}/{db}?sslmode={sslmode}",
                               pool_pre_ping=True, pool_size=4, max_overflow=4, pool_recycle=1500, future=True,
                               connect_args=_search_path(schema))
        static = os.environ.get("PGPASSWORD")

        @event.listens_for(engine, "do_connect")
        def _password(dialect, conn_rec, cargs, cparams):  # pragma: no cover - needs Databricks
            cparams["password"] = credentials.token() if credentials is not None and not static else static

        if credentials is not None and not static:
            @event.listens_for(engine, "handle_error")
            def _on_error(ctx):  # pragma: no cover - needs Databricks
                if "not authorized" in str(ctx.original_exception).lower():
                    credentials.invalidate()
        return engine
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False}, future=True)

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
        return engine
    extra = _search_path(schema) if url.startswith("postgresql") else {}
    return create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5, future=True, connect_args=extra)


SCHEMA_HELP = (
    "Le rôle de l'application ({user}) ne peut pas créer le schéma « {schema} » dans la base {db}. Avec un rôle "
    "propriétaire du projet Lakebase (éditeur SQL), exécuter une fois : "
    'GRANT CREATE ON DATABASE "{db}" TO "{user}"; puis redémarrer l\'application. '
    "Cause d'origine : {exc}"
)


def ensure_schema(engine, schema: str | None) -> None:
    """PostgreSQL : the app owns its schema (a Databricks App's role has no CREATE on ``public`` ;
    ``CAN_CONNECT_AND_CREATE`` lets it create schemas in the database). Idempotent."""
    if engine.dialect.name != "postgresql" or not schema or schema == "public":
        return
    try:
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    except Exception as exc:  # pragma: no cover - needs PostgreSQL
        raise RuntimeError(SCHEMA_HELP.format(user=engine.url.username or "?", schema=schema,
                                              db=engine.url.database or "?", exc=exc)) from exc


def init_store(engine, schema: str | None = None) -> sessionmaker[Session]:
    ensure_schema(engine, schema)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def grant_sync_role(engine, role: str, schema: str | None = None) -> None:
    """Let the synchronisation identity (a user or a service principal) write the ERP mirror tables
    the app owns (and use the app schema).  PostgreSQL only ; a failure is logged, never fatal."""
    if not role or engine.dialect.name != "postgresql":
        return
    tables = [m.__tablename__ for m in ERP_MODELS.values()] + [ErpSyncLog.__tablename__]
    try:
        with engine.begin() as conn:
            if schema and schema != "public":
                conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO "{role}"'))
            for t in tables:
                conn.execute(text(f'GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLE "{t}" TO "{role}"'))
        log.info("Droits d'écriture du miroir ERP accordés à %s", role)
    except Exception as exc:  # pragma: no cover - needs PostgreSQL
        log.warning("GRANT au rôle de synchronisation %s impossible : %s", role, exc)
