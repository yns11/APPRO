"""Pydantic models exchanged with the frontend."""
from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ reference
class ArticleRef(ORM):
    article_id: str
    designation: str
    unit: str
    family: str = ""
    planner: str = ""
    coverage_target_days: int
    alert_red_days: int
    alert_yellow_days: int
    overstock_days: int
    safety_stock_qty: float = 0
    lot_policy: str = "coverage"
    order_cycle_days: int = 7
    active: bool = True


class SupplierRef(ORM):
    supplier_id: str
    name: str
    country: str = ""
    contact: str = ""
    delivery_weekdays: list[int]
    active: bool = True


class LinkRef(ORM):
    article_id: str
    supplier_id: str
    supplier_name: str = ""
    moq: float
    pack_qty: float
    lead_time_days: int
    quota_pct: float
    priority: int
    active: bool = True


class ProgramRef(ORM):
    program_id: str
    name: str
    family: str = ""
    active: bool = True
    components: int = 0


class BomRef(ORM):
    program_id: str
    program_name: str = ""
    article_id: str
    qty_per: float
    unit: str
    scrap_pct: float = 0


# ------------------------------------------------------------------ engine outputs
class AlertOut(BaseModel):
    article_id: str
    designation: str = ""
    alert_type: str
    severity: Literal["critical", "warning", "info"]
    scope: str
    message: str
    date: dt.date | None = None
    value: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ProposalOut(BaseModel):
    proposal_id: str
    article_id: str
    designation: str = ""
    unit: str = ""
    supplier_id: str | None
    supplier_name: str = ""
    delivery_date: dt.date
    order_date: dt.date
    qty: float
    net_requirement: float
    reason: str
    urgent: bool
    ignored: bool = False
    lead_time_days: int
    moq: float
    pack_qty: float
    projected_stock_before: float
    projected_stock_after: float


class SupplyEventOut(BaseModel):
    date: dt.date
    kind: str
    ref: str
    qty: float
    supplier_id: str | None
    order_type: str
    source: str
    late: bool = False


class ArticleSummary(BaseModel):
    article_id: str
    designation: str
    unit: str
    planner: str
    suppliers: list[str]
    severity: str | None
    kpis: dict[str, Any]
    alert_types: list[str]
    sparkline: list[float]  # simulated stock, weekly samples over the horizon


class CockpitKpis(BaseModel):
    articles: int
    critical: int
    warning: int
    stockouts: int
    stockouts_7d: int
    low_coverage: int
    overstock: int
    late_orders: int
    proposals: int
    urgent_proposals: int
    proposals_qty: float
    open_firm_qty: float
    open_forecast_qty: float
    open_planned_qty: float
    avg_coverage_days: float | None
    demand_next_30d: float


class CockpitResponse(BaseModel):
    as_of: dt.date
    horizon_days: int
    planner: str | None
    scenario_id: str | None
    data_source: str
    pdp_version: dict[str, Any] | None
    kpis: CockpitKpis
    articles: list[ArticleSummary]
    alerts: list[AlertOut]
    proposals: list[ProposalOut]
    diagnostics: list[str]
    weekly_supply_demand: list[dict[str, Any]]


class SeriesOut(BaseModel):
    key: str
    label: str
    values: list[float]


class ProjectionResponse(BaseModel):
    article: ArticleRef
    as_of: dt.date
    granularity: Literal["day", "week"]
    periods: list[str]
    period_start: list[dt.date]
    series: list[SeriesOut]
    events: list[SupplyEventOut]
    proposals: list[ProposalOut]
    alerts: list[AlertOut]
    kpis: dict[str, Any]
    suppliers: list[LinkRef]
    programs: list[dict[str, Any]]
    diagnostics: list[str]


# ------------------------------------------------------------------ entries
class OrderIn(BaseModel):
    article_id: str
    supplier_id: str | None = None
    expected_date: dt.date
    qty: float = Field(gt=0)
    order_type: Literal["PLANNED", "FIRM"] = "PLANNED"
    note: str = ""


class OrderUpdate(BaseModel):
    supplier_id: str | None = None
    expected_date: dt.date | None = None
    qty: float | None = Field(None, gt=0)
    order_type: Literal["PLANNED", "FIRM"] | None = None
    status: Literal["OPEN", "SENT", "RECEIVED", "CANCELLED"] | None = None
    note: str | None = None


class OrderOut(ORM):
    id: str
    article_id: str
    supplier_id: str | None
    expected_date: dt.date
    qty: float
    unit: str
    order_type: str
    status: str
    source: str
    erp_order_id: str | None
    proposal_id: str | None
    note: str
    created_by: str
    created_at: dt.datetime
    updated_at: dt.datetime


class ReceiptIn(BaseModel):
    article_id: str
    supplier_id: str | None = None
    order_id: str | None = None
    receipt_date: dt.date
    qty: float = Field(gt=0)
    note: str = ""


class ReceiptOut(ORM):
    id: str
    article_id: str
    supplier_id: str | None
    order_id: str | None
    receipt_date: dt.date
    qty: float
    note: str
    created_by: str
    created_at: dt.datetime


class AdjustmentIn(BaseModel):
    article_id: str
    date: dt.date
    qty: float
    movement_type: str = "INVENTORY_ADJUSTMENT"
    comment: str = ""


class AdjustmentOut(ORM):
    id: str
    article_id: str
    date: dt.date
    qty: float
    movement_type: str
    comment: str
    created_by: str
    created_at: dt.datetime


class ProductionActualIn(BaseModel):
    program_id: str
    date: dt.date
    qty: float = Field(ge=0)


class ProductionActualOut(ORM):
    id: str
    program_id: str
    date: dt.date
    qty: float
    created_by: str
    created_at: dt.datetime


# ------------------------------------------------------------------ proposals
class ProposalDecision(BaseModel):
    article_id: str
    supplier_id: str | None = None
    delivery_date: dt.date
    qty: float = Field(gt=0)
    order_type: Literal["PLANNED", "FIRM"] = "PLANNED"
    proposal_id: str | None = None
    note: str = ""


class ProposalIgnore(BaseModel):
    article_id: str
    supplier_id: str | None = None
    delivery_date: dt.date
    reason: str = ""
    until_date: dt.date | None = None


# ------------------------------------------------------------------ scenarios
EVENT_KINDS = ("add_order", "move_order", "change_order_qty", "cancel_order", "plan_factor", "set_plan",
               "set_actual", "add_movement", "set_article_param", "set_link_param")


class ScenarioEventIn(BaseModel):
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)
    label: str = ""


class ScenarioEventOut(ScenarioEventIn):
    id: str
    seq: int


class ScenarioIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    events: list[ScenarioEventIn] = Field(default_factory=list)


class ScenarioOut(BaseModel):
    id: str
    name: str
    description: str
    status: str
    params: dict[str, Any]
    events: list[ScenarioEventOut]
    created_by: str
    created_at: dt.datetime
    updated_at: dt.datetime


class SimulateRequest(BaseModel):
    """Ad-hoc simulation (not saved)."""

    article_ids: list[str] | None = None
    planner: str | None = None
    scenario_id: str | None = None
    events: list[ScenarioEventIn] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


class CompareArticle(BaseModel):
    article_id: str
    designation: str
    unit: str
    base: dict[str, Any]
    scenario: dict[str, Any]
    delta_min_stock: float
    delta_max_shortage: float = 0.0
    delta_coverage: int
    stockout_changed: bool


class CompareResponse(BaseModel):
    as_of: dt.date
    base_kpis: CockpitKpis
    scenario_kpis: CockpitKpis
    articles: list[CompareArticle]
    diagnostics: list[str]


# ------------------------------------------------------------------ params / pdp / audit
class ParamOverrideIn(BaseModel):
    scope: Literal["global", "article", "link"]
    key1: str = ""
    key2: str = ""
    field: str
    value: str | int | float | bool | None


class ParamOverrideOut(ORM):
    id: str
    scope: str
    key1: str
    key2: str
    field: str
    value: str
    updated_by: str
    updated_at: dt.datetime


class ParamDoc(BaseModel):
    field: str
    default: Any
    type: str
    description: str
    options: list[str] | None = None


class PdpVersionOut(ORM):
    id: str
    name: str
    source_file: str
    note: str
    active: bool
    imported_by: str
    imported_at: dt.datetime
    line_count: int = 0
    programs: int = 0
    first_week: dt.date | None = None
    last_week: dt.date | None = None


class ImportReport(BaseModel):
    created: int
    ignored: int
    notes: list[str]
    version: PdpVersionOut | None = None


class AuditOut(BaseModel):
    id: int
    ts: dt.datetime
    user: str
    action: str
    entity_type: str
    entity_id: str
    article_id: str | None
    payload: dict[str, Any]


class ConfigOut(BaseModel):
    title: str
    data_source: dict[str, Any]
    as_of: dt.date
    horizon_days: int
    planners: list[str]
    default_planner: str | None
    user: str
    version: str
