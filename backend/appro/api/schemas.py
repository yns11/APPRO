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
    order_cycle_days: int = 7
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


class RefColumn(BaseModel):
    name: str
    label: str
    type: str
    key: bool
    required: bool
    description: str = ""


class RefTableInfo(BaseModel):
    name: str
    label: str
    description: str
    key: list[str]
    columns: list[RefColumn]
    rows: int


class RefRowIn(BaseModel):
    values: dict[str, Any]


class RefKeyIn(BaseModel):
    key: dict[str, Any]


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
    lead_time_days: int
    moq: float
    pack_qty: float
    projected_stock_before: float
    projected_stock_after: float


class OrderInfoOut(BaseModel):
    order_id: str
    supplier_id: str | None
    order_type: str
    expected_date: dt.date
    qty_ordered: float
    qty_open: float
    ref: str = ""


class SeriesOut(BaseModel):
    key: str
    label: str
    values: list[float]


class LaneOut(BaseModel):
    """One supplier of the article: its own Ferme / Prévisionnel / Reçu / Plan rows."""

    supplier_id: str | None
    name: str
    series: list[SeriesOut]
    plan_typed: list[bool]
    orders_ignored: list[bool] = Field(default_factory=list)
    backlog_ordered: float
    backlog_received: float
    backlog_qty: float
    orders: list[OrderInfoOut] = Field(default_factory=list)


class ArticleSummary(BaseModel):
    article_id: str
    designation: str
    unit: str
    planner: str
    suppliers: list[str]
    severity: str | None
    kpis: dict[str, Any]
    alert_types: list[str]
    sparkline: list[float]  # plan stock, weekly samples over the horizon


class BacklogRow(BaseModel):
    article_id: str
    designation: str
    unit: str
    supplier_id: str | None
    supplier_name: str
    ordered: float
    received: float
    backlog: float


class CockpitKpis(BaseModel):
    articles: int
    critical: int
    warning: int
    stockouts: int
    stockouts_7d: int
    low_coverage: int
    overstock: int
    backlog_articles: int        # articles with a supplier backlog
    backlog_qty: float
    proposals: int
    urgent_proposals: int
    proposals_qty: float
    plan_articles: int           # articles with at least one typed plan cell
    plan_qty: float
    open_firm_qty: float
    open_forecast_qty: float
    avg_coverage_days: float | None
    demand_next_30d: float


class CockpitResponse(BaseModel):
    as_of: dt.date
    horizon_days: int
    planner: str | None
    data_source: str
    pdp_version: dict[str, Any] | None
    kpis: CockpitKpis
    articles: list[ArticleSummary]
    alerts: list[AlertOut]
    proposals: list[ProposalOut]
    backlog: list[BacklogRow]
    diagnostics: list[str]
    weekly_supply_demand: list[dict[str, Any]]


class ProjectionResponse(BaseModel):
    article: ArticleRef
    as_of: dt.date
    granularity: Literal["default", "day", "week"]
    periods: list[str]              # ISO date (day column) or ISO week label (week column)
    period_start: list[dt.date]
    period_end: list[dt.date]
    series: list[SeriesOut]
    lanes: list[LaneOut]
    proposals: list[ProposalOut]
    alerts: list[AlertOut]
    kpis: dict[str, Any]
    suppliers: list[LinkRef]
    programs: list[dict[str, Any]]
    diagnostics: list[str]


class GridArticle(BaseModel):
    """One article of the multi-article supply table (same series as the article projection)."""

    article: ArticleRef
    series: list[SeriesOut]
    lanes: list[LaneOut]
    kpis: dict[str, Any]
    suppliers: list[LinkRef]
    programs: list[str]


class GridResponse(BaseModel):
    """One page of the supply table (``total`` articles in the perimeter after filters)."""

    as_of: dt.date
    granularity: Literal["default", "day", "week"]
    periods: list[str]
    period_start: list[dt.date]
    period_end: list[dt.date]
    articles: list[GridArticle]
    diagnostics: list[str]
    total: int = 0
    page: int = 1
    page_size: int = 0


class DeliveryRow(BaseModel):
    supplier_id: str | None
    supplier_name: str
    date: dt.date
    end_date: dt.date
    qty: float
    typed: bool


class DeliveryPlanResponse(BaseModel):
    """The *Plan* row as an ERP delivery schedule (one line per supplier and day with a quantity)."""

    article_id: str
    designation: str
    unit: str
    as_of: dt.date
    suppliers: list[dict[str, Any]]
    rows: list[DeliveryRow]


class ProgramImpactResponse(BaseModel):
    as_of: dt.date
    weeks: list[str]
    programs: list[dict[str, Any]]
    diagnostics: list[str]


# ------------------------------------------------------------------ the two editable rows
class AdjustmentIn(BaseModel):
    """An adjustment cell: signed quantity or arithmetic expression (blank or 0 = clear) ; any date
    (on/before the reference day it corrects the reference stock).  ``expression`` omitted = note only."""

    article_id: str
    date: dt.date
    expression: str | None = Field(None, max_length=200)
    note: str | None = Field(None, max_length=500)


class AdjustmentOut(ORM):
    id: str
    article_id: str
    date: dt.date
    expression: str
    qty: float
    note: str
    updated_by: str
    updated_at: dt.datetime


class PlanCellIn(BaseModel):
    """A plan cell: the quantity expected from ``supplier_id`` that day (0 = nothing) ; blank = back
    to the ERP.  ``expression`` omitted = note only."""

    article_id: str
    supplier_id: str | None = None
    date: dt.date
    expression: str | None = Field(None, max_length=200)
    note: str | None = Field(None, max_length=500)


class PlanCellOut(ORM):
    id: str
    article_id: str
    supplier_id: str
    date: dt.date
    expression: str
    qty: float
    note: str
    updated_by: str
    updated_at: dt.datetime


class PlanCellBatchIn(BaseModel):
    """Several plan cells at once (fill handle of the grid): one transaction, one recalculation."""

    cells: list[PlanCellIn] = Field(max_length=2000)


class AdjustmentBatchIn(BaseModel):
    cells: list[AdjustmentIn] = Field(max_length=2000)


class FlagIn(BaseModel):
    """A click on a read-only cell: ``order_ignored`` (Ferme row: supplier × day, the firm orders of
    the day leave the ERP scenario and the plan) or ``proposal_refused`` (Proposition CBN row: no
    proposal from that day to the end of its ISO week).  ``qty`` keeps the refused quantity shown."""

    article_id: str
    supplier_id: str | None = None
    date: dt.date
    kind: Literal["order_ignored", "proposal_refused"]
    qty: float = 0.0
    note: str | None = Field(None, max_length=500)


class FlagOut(ORM):
    id: str
    article_id: str
    supplier_id: str
    date: dt.date
    kind: str
    qty: float
    note: str
    updated_by: str
    updated_at: dt.datetime


class WeeklyParamRow(BaseModel):
    week: str
    week_start: dt.date
    values: dict[str, float]
    overridden: list[str]


class WeeklyParamsResponse(BaseModel):
    article_id: str
    fields: list[str]
    defaults: dict[str, float]
    weeks: list[WeeklyParamRow]


# ------------------------------------------------------------------ params / pdp / audit
class ParamOverrideIn(BaseModel):
    scope: Literal["global", "article_week"]
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
    reference_empty: bool
