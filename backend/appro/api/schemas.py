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
    ignored: bool = False          # refused by a click : listed in the ordering flow, out of the calculations


class OrderInfoOut(BaseModel):
    order_id: str
    supplier_id: str | None
    order_type: str
    expected_date: dt.date
    qty_ordered: float
    qty_open: float
    ref: str = ""


class DesadvInfoOut(BaseModel):
    """One despatch advice line of the supplier: shown in the Reçu row while not received."""

    desadv_id: str
    packing_slip: str
    issue_date: dt.date
    qty: float
    state: str
    final_processing: str
    processed: bool
    received: bool
    hidden: bool
    purch_id: str = ""


class SeriesOut(BaseModel):
    key: str
    label: str
    values: list[float]


class ReceiptInfoOut(BaseModel):
    """One ERP receipt line: delivery note (BL) for the tooltips of the Reçu row ; empty = « ACR non validé »."""

    receipt_id: str
    receipt_date: dt.date
    qty: float
    packing_slip: str
    purch_id: str
    processed: bool


class LaneOut(BaseModel):
    """One supplier of the article: its own Ferme / Prévisionnel / Reçu / Plan rows."""

    supplier_id: str | None
    name: str
    series: list[SeriesOut]
    plan_typed: list[bool]
    orders_ignored: list[bool] = Field(default_factory=list)
    desadv_ko: list[bool] = Field(default_factory=list)
    receipts_ko: list[bool] = Field(default_factory=list)
    backlog_ordered: float
    backlog_received: float
    backlog_qty: float
    orders: list[OrderInfoOut] = Field(default_factory=list)
    desadv: list[DesadvInfoOut] = Field(default_factory=list)
    receipt_lines: list[ReceiptInfoOut] = Field(default_factory=list)


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
    median_coverage_days: float | None = None     # the median reads better : the mean is pulled by a few extreme articles
    demand_next_30d: float
    stock_value: float | None = None              # Σ price × stock at date (yesterday's closing stock), euros
    target_value: float | None = None             # Σ price × target stock of the day
    priced_articles: int = 0
    unpriced_articles: int = 0


class WeeklyStockValue(BaseModel):
    week: str
    week_start: dt.date
    value_plan: float          # Σ price × Projeté Appro. stock at the end of the week
    value_target: float        # Σ price × target stock at the end of the week


class CockpitResponse(BaseModel):
    as_of: dt.date
    init_date: dt.date
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
    weekly_stock_value: list[WeeklyStockValue] = Field(default_factory=list)


# ------------------------------------------------------------------ supply flows (cockpit tab 2) and search
class OrderRow(BaseModel):
    order_id: str
    article_id: str
    designation: str = ""
    unit: str = ""
    planner: str = ""
    supplier_id: str | None
    supplier_name: str = ""
    order_type: str
    expected_date: dt.date
    qty_ordered: float
    qty_open: float
    purch_id: str = ""
    first_seen: dt.date | None = None


class ReceiptRow(BaseModel):
    receipt_id: str
    article_id: str
    designation: str = ""
    unit: str = ""
    planner: str = ""
    supplier_id: str | None
    supplier_name: str = ""
    receipt_date: dt.date
    qty: float
    purch_id: str = ""
    packing_slip: str = ""
    status: str = ""


class DesadvRow(BaseModel):
    desadv_id: str
    article_id: str
    designation: str = ""
    unit: str = ""
    planner: str = ""
    supplier_id: str | None
    supplier_name: str = ""
    packing_slip: str = ""
    purch_id: str = ""
    issue_date: dt.date
    qty: float
    state: str = ""
    final_processing: str = ""
    received: bool = False
    journal: bool = True        # False : processed message without any stock transaction (entry journal missing)
    issue: str = ""             # why it is listed in « à traiter »


class PendingRow(BaseModel):
    pending_id: str
    article_id: str
    designation: str = ""
    unit: str = ""
    planner: str = ""
    supplier_id: str | None
    supplier_name: str = ""
    purch_id: str = ""
    packing_slip: str = ""
    qty: float
    registered_date: dt.date
    days_pending: int = 0


class FlowsKpis(BaseModel):
    to_order: int
    ordered: int
    in_transit: int
    to_process: int
    to_receive: int
    received: int
    late: int
    to_validate: int


class FlowsResponse(BaseModel):
    """The « Flux d'approvisionnement » tab of the cockpit : one counter and one table per flow."""

    as_of: dt.date
    planner: str | None
    first_seen_available: bool          # False until the synchronisation job has stamped the orders
    kpis: FlowsKpis
    to_order: list[ProposalOut]
    ordered: list[OrderRow]
    in_transit: list[DesadvRow]
    to_process: list[DesadvRow]
    to_receive: list[OrderRow]
    received: list[ReceiptRow]
    late: list[BacklogRow]
    to_validate: list[PendingRow]


class SearchResponse(BaseModel):
    kind: Literal["receipts", "orders", "desadv", "pending"]
    total: int
    truncated: bool
    rows: list[dict[str, Any]]


class ProjectionResponse(BaseModel):
    article: ArticleRef
    as_of: dt.date
    init_date: dt.date
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
    init_date: dt.date
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
    """One ISO week of the Plan row for one supplier: ``date`` = Monday, ``end_date`` = Sunday."""

    supplier_id: str | None
    supplier_name: str
    week: str
    date: dt.date
    end_date: dt.date
    qty: float
    typed_qty: float
    erp_qty: float
    cbn_qty: float
    typed: bool


class DeliveryPlanResponse(BaseModel):
    """The *Plan* row as an ERP delivery schedule (one line per supplier and ISO week with a quantity)."""

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
    kind: Literal["order_ignored", "proposal_refused", "desadv_hidden"]
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


class ParamOverrideItemIn(BaseModel):
    key2: str = ""
    field: str
    value: str | int | float | bool | None


class ParamOverrideBatchIn(BaseModel):
    scope: Literal["global", "article_week"]
    key1: str = ""
    items: list[ParamOverrideItemIn]


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


class PerimeterItem(BaseModel):
    id: str
    name: str = ""


class PerimeterOut(BaseModel):
    """Programmes and suppliers reachable from the articles of a planner (filters of the supply table)."""

    planner: str | None = None
    articles: int = 0
    programs: list[PerimeterItem] = Field(default_factory=list)
    suppliers: list[PerimeterItem] = Field(default_factory=list)


class PdpSheetWeek(BaseModel):
    week: str
    week_start: dt.date
    editable: bool          # strictly after the current week, for managers and administrators


class PdpSheetProgram(BaseModel):
    program_id: str
    name: str
    active: bool = True
    source: Literal["app", "erp", "none"]   # where the displayed quantities come from
    has_bom: bool = True                    # without a bill of material the plan explodes into nothing
    values: list[float]


class PdpSheetOut(BaseModel):
    """The effective weekly PDP as one sheet: programmes in rows, ISO weeks in columns."""

    as_of: dt.date
    current_week: str
    active_version: PdpVersionOut | None = None
    erp_available: bool = False
    weeks: list[PdpSheetWeek]
    programs: list[PdpSheetProgram]


class PdpCellIn(BaseModel):
    program_id: str
    week_start: dt.date
    qty: float = Field(ge=0)


class PdpSheetIn(BaseModel):
    """Direct entry of the PDP: the changed cells only ; saved as a new active version."""

    name: str = ""
    note: str = ""
    cells: list[PdpCellIn] = Field(min_length=1)


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
    init_date: dt.date
    horizon_days: int
    planners: list[str]
    default_planner: str | None
    user: str
    version: str
    reference_empty: bool
    access: dict[str, Any]
