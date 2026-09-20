"""Application settings (environment variables, ``.env`` locally, ``app.yaml`` on Databricks)."""
from __future__ import annotations

import datetime as dt
import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="APPRO_", env_file=str(REPO_ROOT / ".env"), extra="ignore")

    # --- ERP / reference data -------------------------------------------------------
    data_source: str = Field("local", description="'local' (seed CSV) or 'uc' (Unity Catalog via SQL warehouse)")
    seed_dir: Path = Field(REPO_ROOT / "data" / "seed")
    uc_catalog: str = Field("main")
    uc_schema: str = Field("appro")
    uc_table_prefix: str = Field("")
    cache_ttl_seconds: float = Field(300.0)

    # --- transactional app store ------------------------------------------------------
    db_url: str | None = Field(None, description="SQLAlchemy URL; default SQLite file, or Lakebase when PGHOST is set")

    # --- business defaults ------------------------------------------------------------
    as_of: dt.date | None = Field(None, description="Fixed as-of date (demo). Default: today, or snapshot+1 locally")
    default_planner: str | None = Field(None)
    horizon_days: int = Field(120)
    history_days: int = Field(14)
    working_weekdays: str = Field("1,2,3,4,5")
    holidays: str = Field("", description="Comma separated ISO dates of plant closures")

    # --- web ------------------------------------------------------------------------
    static_dir: Path = Field(REPO_ROOT / "client" / "dist")
    app_title: str = Field("APPRO – Cockpit approvisionnement")
    log_level: str = Field("INFO")

    @property
    def warehouse_id(self) -> str | None:
        return os.environ.get("DATABRICKS_WAREHOUSE_ID")

    @property
    def resolved_db_url(self) -> str:
        if self.db_url:
            return self.db_url
        if os.environ.get("PGHOST"):
            # Lakebase: password may be injected (PGPASSWORD) or generated at runtime (see store.lakebase_url)
            return "lakebase"
        local = REPO_ROOT / "data" / "local"
        local.mkdir(parents=True, exist_ok=True)
        return f"sqlite:///{local / 'appro.db'}"

    @property
    def holiday_dates(self) -> list[dt.date]:
        return [dt.date.fromisoformat(x.strip()) for x in self.holidays.split(",") if x.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
