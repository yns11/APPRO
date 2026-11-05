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
from contextlib import contextmanager
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
# text columns are unbounded : a registered receipt lists every BL of its DESADV, an order slot every
# purchase order number ; a VARCHAR(200) once broke the COPY of the synchronisation job
_SQL_TYPES = {"str": Text, "float": Float, "int": Integer, "bool": Boolean, "date": Date}


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


class AppMeta(Base):
    """Key / value rows shared by every worker process: ``data_version`` is incremented on each write
    so that the in-memory caches of all the processes are invalidated together."""

    __tablename__ = "app_meta"
    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


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


class ProgramScrap(Base):
    """Programme scrap rate (%) typed in the PDP sheet, **dated** : the rate applies from its Monday
    on, until a later row ; the weeks before keep the rate they had (a change never rewrites the past)."""

    __tablename__ = "app_program_scrap"
    program_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    week_start: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    rate_pct: Mapped[float] = mapped_column(Float, default=0.0)
    updated_by: Mapped[str] = mapped_column(String(120), default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


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


class OtdClosing(Base):
    """**Monthly closing** of the supplier service : the OTD sheet of one supplier over one month, frozen the day the
    month is closed (the figures of the hierarchy report never move afterwards, whatever is received later)."""

    __tablename__ = "app_otd_closings"
    month: Mapped[str] = mapped_column(String(7), primary_key=True)          # 2026-10
    supplier_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    otd: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")                # the sheet (services/otd.py sheet()) as JSON
    closed_by: Mapped[str] = mapped_column(String(120), default="")
    closed_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


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


class AppAction(Base):
    """One **gesture** of a user (a cell typed, a fill or a paste of the grid, a reference row, a weekly
    parameter, a PDP save…) with the state of every touched row **before and after** : the unit of
    « annuler / rétablir » (services/actions.py).  Kept :data:`appro.services.actions.RETENTION_DAYS` days."""

    __tablename__ = "app_actions"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("AC"))
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    user: Mapped[str] = mapped_column(String(120), default="", index=True)
    kind: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(255), default="")
    article_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    group_id: Mapped[str] = mapped_column(String(60), default="", index=True)   # one gesture spread over several requests
    items_json: Mapped[str] = mapped_column(Text, default="[]")                 # [{entity, key, before, after}]
    undoable: Mapped[bool] = mapped_column(Boolean, default=True)
    state: Mapped[str] = mapped_column(String(12), default="applied")           # applied | undone | discarded
    changed_at: Mapped[dt.datetime | None] = mapped_column(DateTime, nullable=True)
    changed_by: Mapped[str] = mapped_column(String(120), default="")

    @property
    def items(self) -> list[dict[str, Any]]:
        return json.loads(self.items_json or "[]")


class AppHelpPage(Base):
    """One page of the in-app « pense-bête » **edited by an administrator** : the saved version replaces
    the default text shipped with the application (``appro/help/pense_bete.json``) for everybody ; deleting
    the row brings the default back.  One row per page at most : no history is kept."""

    __tablename__ = "app_help_pages"
    slug: Mapped[str] = mapped_column(String(60), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[str] = mapped_column(String(120), default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AppCounter(Base):
    """Named incremental counters (transport families « EM00001 »…) ; read with ``FOR UPDATE`` on
    PostgreSQL so two planners never draw the same number."""

    __tablename__ = "app_counters"
    name: Mapped[str] = mapped_column(String(40), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


class AppMigration(Base):
    """One-shot data migrations already applied on this database (``DATA_MIGRATIONS``) : each runs once,
    whatever the number of worker processes or restarts."""

    __tablename__ = "app_migrations"
    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    applied_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class AppTransport(Base):
    """One **transport** (one truck) expected from a supplier : identifier ``EM`` + 5 digits + letter (family
    number, rank in the family ; ``EM`` + 4 digits + ``EV`` for an EV return), delivery date, time and docks ;
    exported as one CSV line per dock for the external transport tool (ATR), which notifies the supplier."""

    __tablename__ = "app_transports"
    transport_id: Mapped[str] = mapped_column(String(20), primary_key=True)
    supplier_id: Mapped[str] = mapped_column(String(40), index=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    time: Mapped[str] = mapped_column(String(8), default="")          # HH:MM
    docks: Mapped[str] = mapped_column(Text, default="")              # delivery docks, « ; » separated (one CSV line per dock)
    carrier: Mapped[str] = mapped_column(Text, default="")
    note: Mapped[str] = mapped_column(Text, default="")
    mode: Mapped[str] = mapped_column(String(20), default="fournisseur")   # fournisseur | maf | transporteur | ev | ponctuel (services/transports.py)
    status: Mapped[str] = mapped_column(String(12), default="planned")  # planned | cancelled
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    # --- automatic trucks (services/transports_auto.py)
    origin: Mapped[str | None] = mapped_column(String(12), default="manuel", nullable=True)     # manuel | auto (created by the reconciliation)
    pinned: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)         # touched by a planner : the scheduler never moves it again
    check_reason: Mapped[str | None] = mapped_column(Text, default="", nullable=True)          # « à vérifier » : why (empty = nothing to check)
    orders: Mapped[list["AppTransportOrder"]] = relationship(back_populates="transport", cascade="all, delete-orphan", lazy="selectin")

    @property
    def is_auto(self) -> bool:
        return (self.origin or "manuel") == "auto"


class AppTransportOrder(Base):
    """A firm ERP order slot carried by a transport (the same slot may ride several trucks)."""

    __tablename__ = "app_transport_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transport_id: Mapped[str] = mapped_column(String(20), ForeignKey("app_transports.transport_id", ondelete="CASCADE"), index=True)
    order_id: Mapped[str] = mapped_column(String(120), index=True)
    article_id: Mapped[str] = mapped_column(String(40), index=True)
    supplier_id: Mapped[str] = mapped_column(String(40), default="")   # the order's supplier (a MAF shuttle carries several suppliers)
    ref: Mapped[str] = mapped_column(Text, default="")                 # purchase order numbers
    expected_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    qty: Mapped[float] = mapped_column(Float, default=0.0)
    transport: Mapped["AppTransport"] = relationship(back_populates="orders")


class AppTransportSuppressed(Base):
    """A supplier-day whose automatic truck a planner **cancelled** : the reconciliation creates a truck there again
    only when an order that was not in the cancelled truck appears."""

    __tablename__ = "app_transport_days_suppressed"
    supplier_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    orders_json: Mapped[str] = mapped_column(Text, default="[]")          # order ids the cancelled truck carried
    cancelled_by: Mapped[str] = mapped_column(String(120), default="")
    cancelled_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


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


SCHEMA_OWNED_HELP = (
    "Le schéma « {schema} » de la base {db} existe déjà mais le rôle de l'application ({user}) n'a pas le droit "
    "d'y créer des tables : il appartient à un autre rôle, en général l'application d'une AUTRE cible du bundle "
    "(dev / preprod / prod partageant le même projet Lakebase ; PostgreSQL retombe alors sur « public », d'où "
    "« permission denied for schema public »). Deux solutions : donner à chaque cible son propre schéma "
    "(variable app_schema du bundle / APPRO_DB_SCHEMA, ex. appro_prod), ou faire exécuter par le propriétaire "
    'du schéma : GRANT ALL ON SCHEMA "{schema}" TO "{user}"; puis redémarrer l\'application.'
)


def ensure_schema(engine, schema: str | None) -> None:
    """PostgreSQL : the app owns its schema (a Databricks App's role has no CREATE on ``public`` ;
    ``CAN_CONNECT_AND_CREATE`` lets it create schemas in the database). Idempotent.  When the schema
    already exists but belongs to another role (another target's app on the same Lakebase branch)
    the role cannot create tables in it – PostgreSQL would silently fall back to ``public`` – so the
    privilege is checked here and the failure names the cause."""
    if engine.dialect.name != "postgresql" or not schema or schema == "public":
        return
    try:
        with engine.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema}"'))
    except Exception as exc:  # pragma: no cover - needs PostgreSQL
        raise RuntimeError(SCHEMA_HELP.format(user=engine.url.username or "?", schema=schema,
                                              db=engine.url.database or "?", exc=exc)) from exc
    with engine.connect() as conn:  # pragma: no cover - needs PostgreSQL
        ok = conn.execute(text("SELECT has_schema_privilege(:s, 'CREATE')"), {"s": schema}).scalar()
    if not ok:  # pragma: no cover - needs PostgreSQL
        raise RuntimeError(SCHEMA_OWNED_HELP.format(user=engine.url.username or "?", schema=schema,
                                                    db=engine.url.database or "?"))


def ensure_columns(engine) -> list[str]:
    """Add the columns a newer version of the application declares on tables that already exist
    (``create_all`` never alters a table) and widen the bounded text columns of an earlier version :
    the ERP mirror and the reference tables keep their rows, the job and the screens find the new
    columns.  Returns the ``table.column`` added or widened."""
    from sqlalchemy import inspect
    inspector = inspect(engine)
    added: list[str] = []
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not inspector.has_table(table.name):
                continue
            present = {c["name"]: c for c in inspector.get_columns(table.name)}
            for col in table.columns:
                if col.name not in present:
                    typ = col.type.compile(engine.dialect)
                    conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {typ}'))
                    added.append(f"{table.name}.{col.name}")
                elif (engine.dialect.name == "postgresql" and isinstance(col.type, Text)
                      and isinstance(present[col.name]["type"], String) and present[col.name]["type"].length):
                    # a bounded VARCHAR of an earlier version : widen it (SQLite never enforces the length)
                    conn.execute(text(f'ALTER TABLE "{table.name}" ALTER COLUMN "{col.name}" TYPE TEXT'))
                    added.append(f"{table.name}.{col.name} (TEXT)")
    if added:
        log.info("Colonnes ajoutées aux tables existantes : %s", ", ".join(added))
    return added


#: columns an earlier version wrote and the current one no longer maps : ``(table, old column, new column)`` ;
#: the values are copied into the new column (when it is empty) and the old column is dropped, otherwise an
#: INSERT of the new version fails on its NOT NULL constraint
RENAMED_COLUMNS = (("app_transports", "dock", "docks"),)


def retire_columns(engine) -> list[str]:
    """Legacy columns of the application tables : copy them into their successor and drop them
    (``RENAMED_COLUMNS``) ; any other column the current version does not map loses its NOT NULL so the
    new version's INSERT no longer fails.  Returns what was changed."""
    from sqlalchemy import inspect
    inspector = inspect(engine)
    done: list[str] = []
    with engine.begin() as conn:
        for table_name, old, new in RENAMED_COLUMNS:
            if not inspector.has_table(table_name):
                continue
            present = {c["name"] for c in inspector.get_columns(table_name)}
            if old in present and new in present:
                conn.execute(text(f'UPDATE "{table_name}" SET "{new}" = "{old}" WHERE ("{new}" IS NULL OR "{new}" = \'\') AND "{old}" IS NOT NULL'))
                conn.execute(text(f'ALTER TABLE "{table_name}" DROP COLUMN "{old}"'))
                done.append(f"{table_name}.{old} → {new}")
        if engine.dialect.name == "postgresql":
            for table in Base.metadata.sorted_tables:
                if not table.name.startswith("app_") or not inspector.has_table(table.name):
                    continue
                mapped = {c.name for c in table.columns}
                for col in inspector.get_columns(table.name):
                    if col["name"] not in mapped and not col.get("nullable", True) and (table.name, col["name"]) not in {(t, o) for t, o, _n in RENAMED_COLUMNS}:
                        conn.execute(text(f'ALTER TABLE "{table.name}" ALTER COLUMN "{col["name"]}" DROP NOT NULL'))
                        done.append(f"{table.name}.{col['name']} (NULL autorisé)")
    if done:
        log.info("Colonnes d'une version antérieure retirées : %s", ", ".join(done))
    return done


def normalise_nullable_numerics(engine) -> int:
    """Optional numeric columns (link caps, lead time override) : a 0 written by an earlier version
    (missing cells were coerced to 0) means « no value » and becomes NULL.  Returns the rows fixed."""
    n = 0
    with engine.begin() as conn:
        for name, schema in TABLES.items():
            if not schema.reference:
                continue
            for col in schema.nullable_numeric:
                try:
                    res = conn.execute(text(f"UPDATE {name} SET {col} = NULL WHERE {col} IS NOT NULL AND {col} <= 0"))
                    n += res.rowcount or 0
                except Exception as exc:  # table or column not there yet (fresh database before create_all)
                    log.debug("normalise %s.%s skipped: %s", name, col, exc)
    return n


def apply_text_defaults(engine) -> int:
    """Text columns added by a newer version with a business default (supplier docks = B15) : rows of an
    earlier version are empty, they receive the default once.  Returns the rows fixed."""
    n = 0
    with engine.begin() as conn:
        try:
            res = conn.execute(text("UPDATE ref_suppliers SET docks = 'B15' WHERE docks IS NULL OR docks = ''"))
            n += res.rowcount or 0
        except Exception as exc:  # pragma: no cover - table not there yet
            log.debug("default docks skipped: %s", exc)
    return n


def apply_transport_mode_default(engine) -> int:
    """``ref_suppliers.transport_mode`` added later : every supplier without a mode delivers himself (« Fournisseur »)."""
    n = 0
    with engine.begin() as conn:
        try:
            res = conn.execute(text("UPDATE ref_suppliers SET transport_mode = 'Fournisseur' WHERE transport_mode IS NULL OR transport_mode = ''"))
            n += res.rowcount or 0
        except Exception as exc:  # pragma: no cover - table not there yet
            log.debug("default transport mode skipped: %s", exc)
    return n


def apply_otd_band2_five_days(engine) -> int:
    """The second punctuality band grew from 4 to 5 working days (a whole week of delay stays evaluated) : a stored
    override still at the former default follows the new one."""
    n = 0
    with engine.begin() as conn:
        try:
            res = conn.execute(text("UPDATE app_param_overrides SET value = '5' WHERE scope = 'global' AND field = 'otd_band2_days' AND value = '4'"))
            n += res.rowcount or 0
        except Exception as exc:  # pragma: no cover - table not there yet
            log.debug("otd band2 migration skipped: %s", exc)
    return n


def apply_transport_origin_manual(engine) -> int:
    """Columns of the automatic trucks added later : every existing transport was created by hand."""
    n = 0
    with engine.begin() as conn:
        try:
            res = conn.execute(text("UPDATE app_transports SET origin = 'manuel' WHERE origin IS NULL OR origin = ''"))
            n += res.rowcount or 0
            conn.execute(text("UPDATE app_transports SET pinned = false WHERE pinned IS NULL"))
            conn.execute(text("UPDATE app_transports SET check_reason = '' WHERE check_reason IS NULL"))
        except Exception as exc:  # pragma: no cover - table not there yet
            log.debug("transport origin migration skipped: %s", exc)
    return n


def apply_transports_fresh_start(engine) -> int:
    """Fresh start of the transports (November 2026, with the two-week horizon and the identifiers ``EM`` + 5 digits +
    letter) : every transport, every order on board, the suppressed days and the former weekly plans are deleted,
    the counters go back to zero and the next reconciliation creates the trucks of the current and next weeks again.
    The table and columns of the former plan e-mails are dropped.  Returns the transports deleted."""
    n = 0
    statements = (
        "DELETE FROM app_transport_orders",
        "DELETE FROM app_transports",
        "DELETE FROM app_transport_days_suppressed",
        "DELETE FROM app_counters",
        "DELETE FROM app_meta WHERE key = 'transports_reconciled_version'",
        "DROP TABLE IF EXISTS app_transport_plans",
        "ALTER TABLE app_transports DROP COLUMN changed_since_sent",
        "ALTER TABLE app_transports DROP COLUMN change_note",
    )
    for i, stmt in enumerate(statements):
        try:
            with engine.begin() as conn:
                res = conn.execute(text(stmt))
                if i == 1:
                    n = res.rowcount or 0
        except Exception as exc:  # pragma: no cover - column or table already gone
            log.debug("transports fresh start, step skipped (%s): %s", stmt, exc)
    return n


MIGRATION_LOCK_KEY = 7_419_301          # pg_advisory_lock key shared by every worker of the application
#: one-shot data fixes, run once per database (journalised in ``app_migrations``) ; the schema steps
#: (``ensure_columns``, ``retire_columns``, ``create_all``) stay idempotent and run at every start-up, under the lock
DATA_MIGRATIONS: tuple[tuple[str, Any], ...] = (
    ("nullable_numerics_zero_to_null", normalise_nullable_numerics),
    ("supplier_docks_default_b15", apply_text_defaults),
    ("supplier_transport_mode_default", apply_transport_mode_default),
    ("otd_band2_five_days", apply_otd_band2_five_days),
    ("transport_origin_manual", apply_transport_origin_manual),
    ("transports_fresh_start_2026_11", apply_transports_fresh_start),
)


@contextmanager
def migration_lock(engine):
    """Serialise the start-up migrations of the worker processes (PostgreSQL advisory lock held on a dedicated
    connection for the whole DDL sequence) : two workers starting together never race on the same ALTER.
    Other dialects (SQLite, one process) need no lock."""
    if engine.dialect.name != "postgresql":
        yield
        return
    conn = engine.connect()
    try:
        conn.execute(text("SELECT pg_advisory_lock(:k)"), {"k": MIGRATION_LOCK_KEY})
        conn.commit()
        yield
    finally:
        try:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": MIGRATION_LOCK_KEY})
            conn.commit()
        finally:
            conn.close()


def run_data_migrations(engine) -> list[str]:
    """Apply the ``DATA_MIGRATIONS`` not yet journalised in ``app_migrations`` (the table exists : ``create_all``
    ran before).  Returns the names applied."""
    done: list[str] = []
    with engine.connect() as conn:
        applied = {r[0] for r in conn.execute(text("SELECT name FROM app_migrations")).all()}
    for name, fn in DATA_MIGRATIONS:
        if name in applied:
            continue
        fn(engine)
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO app_migrations (name, applied_at) VALUES (:n, :t)"), {"n": name, "t": utcnow()})
        done.append(name)
    if done:
        log.info("Migrations de données appliquées : %s", ", ".join(done))
    return done


def init_store(engine, schema: str | None = None) -> sessionmaker[Session]:
    """Create / upgrade the application schema, **once at a time** : the whole sequence runs under a
    database lock so that several worker processes starting together do not run the same DDL, and the
    one-shot data fixes are journalised and never replayed."""
    ensure_schema(engine, schema)
    with migration_lock(engine):
        ensure_columns(engine)
        retire_columns(engine)
        Base.metadata.create_all(engine)
        run_data_migrations(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    with factory() as session:
        if session.get(AppMeta, DATA_VERSION_KEY) is None:
            session.add(AppMeta(key=DATA_VERSION_KEY, value="0"))
            session.commit()
    return factory


DATA_VERSION_KEY = "data_version"


def read_data_version(session: Session) -> int:
    row = session.get(AppMeta, DATA_VERSION_KEY)
    return int(row.value or 0) if row else 0


def bump_data_version(session: Session, commit: bool = True) -> int:
    """Increment the shared counter (row lock on PostgreSQL: concurrent writers serialise).  ``commit=False``
    leaves the increment in the caller's transaction : the data and the version are then validated together
    (:meth:`appro.services.context.AppContext.commit`)."""
    row = session.get(AppMeta, DATA_VERSION_KEY, with_for_update=session.bind.dialect.name == "postgresql")
    if row is None:
        row = AppMeta(key=DATA_VERSION_KEY, value="0")
        session.add(row)
    row.value = str(int(row.value or 0) + 1)
    if commit:
        session.commit()
    else:
        session.flush()
    return int(row.value)


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
