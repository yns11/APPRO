"""Application settings (environment variables, ``.env`` locally, ``app.yaml`` on Databricks).

An empty variable is treated as **absent** (Databricks manifests cannot omit a value), never as an
invalid value that would stop the application at start-up.
"""
from __future__ import annotations

import datetime as dt
import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APPRO_", env_file=str(REPO_ROOT / ".env"), extra="ignore")

    # --- ERP facts ---------------------------------------------------------------------
    data_source: str = Field("local", description="'local' (seed CSV), 'uc' (SQL warehouse) or 'lakebase' (mirror tables)")
    seed_dir: Path = Field(REPO_ROOT / "data" / "seed")
    seed_reference: bool | None = Field(None, description="load the seed reference tables when the database is empty (default: local only)")
    uc_catalog: str = Field("emotors_data_champions")
    uc_schema: str = Field("silver_erp_ye")
    erp_orders_table: str = Field("commandes_edi")
    erp_receipts_table: str = Field("recep_edi")
    erp_production_table: str | None = Field(None, description="daily actual production table (program_id, date, qty) ; empty = none")
    erp_pdp_table: str | None = Field(None, description="weekly PDP table (program_id, week_start, qty, version) ; empty = none")
    cache_ttl_seconds: float = Field(300.0)

    # --- application database ----------------------------------------------------------
    db_url: str | None = Field(None, description="SQLAlchemy URL; default SQLite file, or Lakebase when PGHOST is set")
    lakebase_branch: str | None = Field(None, description="projects/<p>/branches/<b> : lets the app find its endpoint when LAKEBASE_ENDPOINT is absent")
    sync_role: str | None = Field(None, description="Postgres role of the synchronisation job, granted write access on the ERP mirror")

    # --- business defaults ---------------------------------------------------------------
    as_of: dt.date | None = Field(None, description="Fixed reference date (demo). Default: today, or snapshot + 1 locally")
    default_planner: str | None = Field(None)
    horizon_days: int = Field(120)
    history_days: int = Field(14)
    working_weekdays: str = Field("1,2,3,4,5")
    holidays: str = Field("", description="Comma separated ISO dates of plant closures")

    # --- web -------------------------------------------------------------------------------
    static_dir: Path = Field(REPO_ROOT / "client" / "dist")
    app_title: str = Field("APPRO – Cockpit approvisionnement")
    log_level: str = Field("INFO")

    @field_validator("seed_reference", "erp_production_table", "erp_pdp_table", "db_url", "lakebase_branch", "sync_role",
                     "as_of", "default_planner", mode="before")
    @classmethod
    def _empty_is_none(cls, v):
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @property
    def warehouse_id(self) -> str | None:
        return os.environ.get("DATABRICKS_WAREHOUSE_ID") or None

    @property
    def lakebase_endpoint(self) -> str | None:
        return os.environ.get("LAKEBASE_ENDPOINT") or None

    @property
    def uses_lakebase(self) -> bool:
        return bool(os.environ.get("PGHOST")) and not self.db_url

    @property
    def resolved_db_url(self) -> str:
        if self.db_url:
            return self.db_url
        if self.uses_lakebase:
            return "lakebase"
        local = REPO_ROOT / "data" / "local"
        local.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{local / 'appro.db'}"

    @property
    def seed_reference_enabled(self) -> bool:
        return self.data_source.lower() == "local" if self.seed_reference is None else self.seed_reference

    @property
    def holiday_dates(self) -> list[dt.date]:
        return [dt.date.fromisoformat(x.strip()) for x in self.holidays.split(",") if x.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
