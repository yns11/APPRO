"""Transactional app store: entries typed by the planners, scenarios, PDP versions, audit log.

SQLAlchemy 2.0 ORM.  Locally the store is a SQLite file; on Databricks Apps it is a Lakebase
(PostgreSQL) database attached as an app resource.  Schema creation is idempotent
(``Base.metadata.create_all``) so the app can boot on an empty database.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import uuid
from typing import Any

from sqlalchemy import (Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, create_engine,
                        event)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


class Base(DeclarativeBase):
    pass


class AppOrder(Base):
    """Order typed in the app (planned) or accepted from a proposal."""

    __tablename__ = "app_orders"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("AO"))
    article_id: Mapped[str] = mapped_column(String(40), index=True)
    supplier_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    expected_date: Mapped[dt.date] = mapped_column(Date, index=True)
    qty: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(10), default="PCE")
    order_type: Mapped[str] = mapped_column(String(12), default="PLANNED")   # PLANNED | FIRM
    status: Mapped[str] = mapped_column(String(12), default="OPEN")           # OPEN | SENT | RECEIVED | CANCELLED
    source: Mapped[str] = mapped_column(String(12), default="MANUAL")        # MANUAL | PROPOSAL | IMPORT
    erp_order_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    proposal_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class AppReceipt(Base):
    __tablename__ = "app_receipts"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("AR"))
    article_id: Mapped[str] = mapped_column(String(40), index=True)
    supplier_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    order_id: Mapped[str | None] = mapped_column(String(40), nullable=True)   # app or ERP order id
    receipt_date: Mapped[dt.date] = mapped_column(Date, index=True)
    qty: Mapped[float] = mapped_column(Float)
    note: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class AppAdjustment(Base):
    __tablename__ = "app_adjustments"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("AA"))
    article_id: Mapped[str] = mapped_column(String(40), index=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    qty: Mapped[float] = mapped_column(Float)
    movement_type: Mapped[str] = mapped_column(String(30), default="INVENTORY_ADJUSTMENT")
    comment: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class AppProductionActual(Base):
    """Actual produced quantity typed by the planner (overrides the ERP value for that day)."""

    __tablename__ = "app_production_actual"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("AP"))
    program_id: Mapped[str] = mapped_column(String(40), index=True)
    date: Mapped[dt.date] = mapped_column(Date, index=True)
    qty: Mapped[float] = mapped_column(Float)
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    __table_args__ = (Index("ix_app_prod_program_date", "program_id", "date"),)


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


class Scenario(Base):
    __tablename__ = "app_scenarios"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("SC"))
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(12), default="draft")   # draft | archived
    params_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    events: Mapped[list["ScenarioEventRow"]] = relationship(back_populates="scenario", cascade="all, delete-orphan",
                                                            order_by="ScenarioEventRow.seq")

    @property
    def params(self) -> dict[str, Any]:
        return json.loads(self.params_json or "{}")


class ScenarioEventRow(Base):
    __tablename__ = "app_scenario_events"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("EV"))
    scenario_id: Mapped[str] = mapped_column(ForeignKey("app_scenarios.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(30))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    label: Mapped[str] = mapped_column(String(255), default="")
    scenario: Mapped["Scenario"] = relationship(back_populates="events")

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json or "{}")


class ParamOverride(Base):
    """Overrides of reference parameters (article thresholds, link lead times, global rules)."""

    __tablename__ = "app_param_overrides"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("PO"))
    scope: Mapped[str] = mapped_column(String(12))          # global | article | link | planner
    key1: Mapped[str] = mapped_column(String(60), default="")  # article_id / planner
    key2: Mapped[str] = mapped_column(String(60), default="")  # supplier_id for links
    field: Mapped[str] = mapped_column(String(60))
    value: Mapped[str] = mapped_column(String(255))
    updated_by: Mapped[str] = mapped_column(String(120), default="")
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    __table_args__ = (Index("ix_param_scope_keys", "scope", "key1", "key2", "field", unique=True),)


class IgnoredProposal(Base):
    """A proposal the planner chose to ignore (hidden until ``until_date``)."""

    __tablename__ = "app_ignored_proposals"
    id: Mapped[str] = mapped_column(String(40), primary_key=True, default=lambda: new_id("IG"))
    article_id: Mapped[str] = mapped_column(String(40), index=True)
    delivery_date: Mapped[dt.date] = mapped_column(Date)
    supplier_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    until_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    created_by: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow)


class AuditLog(Base):
    __tablename__ = "app_audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(DateTime, default=utcnow, index=True)
    user: Mapped[str] = mapped_column(String(120), default="", index=True)
    action: Mapped[str] = mapped_column(String(40))
    entity_type: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(60), default="")
    article_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")

    @property
    def payload(self) -> dict[str, Any]:
        return json.loads(self.payload_json or "{}")


# =============================================================================
# Engine / session factory
# =============================================================================
def lakebase_url() -> str:
    """Build a PostgreSQL URL from the env vars injected by a Lakebase app resource.

    ``PGPASSWORD`` is injected for provisioned instances; for autoscaling Lakebase projects the
    password is an OAuth token generated through the Databricks SDK.
    """
    host = os.environ["PGHOST"]
    db = os.environ.get("PGDATABASE", "databricks_postgres")
    user = os.environ.get("PGUSER") or os.environ.get("DATABRICKS_CLIENT_ID", "")
    port = os.environ.get("PGPORT", "5432")
    password = os.environ.get("PGPASSWORD")
    if not password:
        from databricks.sdk import WorkspaceClient  # lazy import
        w = WorkspaceClient()
        cred = w.database.generate_database_credential(instance_names=[os.environ.get("PGINSTANCE", "")] if os.environ.get("PGINSTANCE") else None)
        password = cred.token
    from urllib.parse import quote_plus
    return f"postgresql+psycopg://{quote_plus(user)}:{quote_plus(password)}@{host}:{port}/{db}?sslmode=require"


def make_engine(url: str):
    if url == "lakebase":
        url = lakebase_url()
    if url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False}, future=True)

        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_conn, _):  # pragma: no cover - trivial
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    else:
        engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=5, future=True)
    return engine


def init_store(url: str) -> sessionmaker[Session]:
    engine = make_engine(url)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def audit(session: Session, user: str, action: str, entity_type: str, entity_id: str = "",
          article_id: str | None = None, payload: dict | None = None) -> None:
    session.add(AuditLog(user=user, action=action, entity_type=entity_type, entity_id=entity_id,
                         article_id=article_id, payload_json=json.dumps(payload or {}, default=str)))
