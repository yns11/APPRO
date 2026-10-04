"""Input records, parameters and output records of the MRP engine.

Everything here is a plain dataclass so the engine stays independent from pandas, the
database layer and the API layer.  Quantities are floats (units may be KG or M).

Vocabulary (docs/regles_metier.md):

* the planner edits **two rows** of the grid, *Plan* and *Ajustement*, both made of **cells**
  (article × supplier × day for the plan, article × day for the adjustments) ;
* an empty plan cell means « as the ERP » (the firm ERP quantity of that day), a typed cell is
  the planner's decision ; nothing else is stored about orders.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

import numpy as np


# =============================================================================
# Reference data (managed in the application)
# =============================================================================
@dataclass
class Article:
    article_id: str
    designation: str = ""
    unit: str = "PCE"
    family: str = ""
    planner: str = ""
    coverage_target_days: int = 7
    alert_red_days: int = 3
    alert_yellow_days: int = 7
    overstock_days: int = 30
    safety_stock_qty: float = 0.0
    lot_policy: Literal["coverage", "poq", "fixed_lot"] = "coverage"
    order_cycle_days: int = 7
    fixed_lot_qty: float = 0.0
    active: bool = True
    # per-ISO-week overrides of the stock policy: {"2026-W40": {"coverage_target_days": 10, ...}}
    weekly: dict[str, dict[str, float]] = field(default_factory=dict)

    def param_at(self, name: str, day: dt.date) -> float:
        """Value of a stock-policy parameter on ``day`` (weekly override, else the article value)."""
        if self.weekly:
            y, w, _ = day.isocalendar()
            week = self.weekly.get(f"{y}-W{w:02d}")
            if week and name in week:
                return week[name]
        return getattr(self, name)


WEEKLY_FIELDS = ("coverage_target_days", "alert_red_days", "alert_yellow_days", "overstock_days",
                 "safety_stock_qty", "order_cycle_days")


@dataclass
class Supplier:
    supplier_id: str
    name: str = ""
    country: str = ""
    contact: str = ""
    delivery_weekdays: frozenset[int] = frozenset({1, 2, 3, 4, 5})
    active: bool = True


@dataclass
class SupplierLink:
    """Article ↔ supplier sourcing rule (MOQ, packaging, lead time, quota)."""

    article_id: str
    supplier_id: str
    moq: float = 0.0
    pack_qty: float = 0.0          # "PLA" – packaging / rounding quantity
    lead_time_days: int = 10       # working days
    quota_pct: float = 100.0
    priority: int = 1
    active: bool = True


@dataclass
class Program:
    program_id: str
    name: str = ""
    family: str = ""
    active: bool = True


@dataclass
class BomLine:
    program_id: str
    article_id: str
    qty_per: float
    unit: str = "PCE"
    scrap_pct: float = 0.0
    valid_from: dt.date | None = None
    valid_to: dt.date | None = None


# =============================================================================
# Facts (ERP)
# =============================================================================
@dataclass
class PdpLine:
    """Weekly production plan (PDP) for one program and one ISO week."""

    program_id: str
    week_start: dt.date   # Monday
    qty: float
    version: str = ""


@dataclass
class ConsumptionLine:
    """Actual consumption of one component on one day, **already exploded** through the bill of
    material by the upstream pipeline (the PDP, on the contrary, is exploded by the engine)."""

    article_id: str
    date: dt.date
    qty: float


class OrderType(str, Enum):
    FIRM = "FIRM"          # firm schedule line (Ordre_ferme = Oui)
    FORECAST = "FORECAST"  # forecast schedule line


@dataclass
class OrderLine:
    """One ERP delivery slot: ``supplier|article|date|firm`` (see docs/regles_metier.md § 3.1)."""

    order_id: str
    article_id: str
    supplier_id: str | None
    expected_date: dt.date
    qty_ordered: float
    qty_open: float | None = None          # ERP remaining quantity (None = qty_ordered)
    order_type: OrderType = OrderType.FIRM
    ref: str = ""                          # purchase order numbers, information only

    def __post_init__(self) -> None:
        if self.qty_open is None:
            self.qty_open = float(self.qty_ordered)


@dataclass
class Receipt:
    receipt_id: str
    article_id: str
    receipt_date: dt.date
    qty: float
    supplier_id: str | None = None
    ref: str = ""
    packing_slip: str = ""         # delivery note number (BL) : matched with the despatch advices


@dataclass
class DesadvLine:
    """One article line of an EDI despatch advice (DESADV).  *Not received* while its ``packing_slip``
    is absent from the receipts ; shown in the Reçu row on its issue day, never counted anywhere."""

    desadv_id: str
    article_id: str
    supplier_id: str | None
    packing_slip: str
    issue_date: dt.date
    qty: float
    purch_id: str = ""
    state: str = ""                # Créé, Traité, Erreur, En attente, Annulé
    final_processing: str = ""     # Non traité, OK

    @property
    def processed(self) -> bool:
        return self.state.strip().lower() == "traité" and self.final_processing.strip().upper() == "OK"


@dataclass
class StockSnapshot:
    article_id: str
    snapshot_date: dt.date      # stock known at the END of this day
    qty_on_hand: float
    qty_blocked: float = 0.0


# =============================================================================
# Planner entries (the two editable rows)
# =============================================================================
@dataclass
class AdjustCell:
    """A signed stock **adjustment** typed in the grid, any date: on or before the reference day it
    corrects the reference stock (the ERP stock is often wrong and the app never writes to the ERP),
    later it is a movement known in advance (planned scrap, transfer…)."""

    article_id: str
    date: dt.date
    qty: float
    note: str = ""


@dataclass
class PlanCell:
    """The planned delivery of one supplier on one day.  Typed = the planner's decision, whatever
    the ERP says that day (0 = nothing expected) ; absent = the firm ERP quantity of the day.
    A cell dated before the reference day has expired and is ignored."""

    article_id: str
    supplier_id: str | None
    date: dt.date
    qty: float
    note: str = ""


FlagKind = Literal["order_ignored", "proposal_refused", "desadv_hidden"]


@dataclass
class CellFlag:
    """A click on a read-only cell of the grid.

    * ``order_ignored`` (Ferme row, supplier × day, on/after the reference day): the firm ERP orders
      of that day are **ignored** – out of the ERP scenario and of the plan prefill – until the
      planner clicks again ;
    * ``desadv_hidden`` (Reçu row, supplier × day): the unreceived despatch advices of that day are
      no longer shown (double-click confirmed by the planner) ;
    * ``proposal_refused`` (Proposition CBN row, supplier × day ; article-wide when ``supplier_id`` is
      empty): the proposal of that day is refused
      and **no proposal may be placed from that day to the end of its ISO week** ; ``qty`` keeps
      the refused quantity for display."""

    article_id: str
    supplier_id: str | None
    date: dt.date
    kind: FlagKind
    qty: float = 0.0


class DatasetError(ValueError):
    """The data cannot be computed as is (several stock initialisation dates, a future one…)."""


@dataclass
class Dataset:
    """Everything the engine needs, as plain records."""

    articles: list[Article]
    suppliers: list[Supplier]
    links: list[SupplierLink]
    programs: list[Program]
    bom: list[BomLine]
    pdp: list[PdpLine]
    consumption: list[ConsumptionLine]
    orders: list[OrderLine]
    receipts: list[Receipt]
    stock: list[StockSnapshot]
    holidays: list[dt.date] = field(default_factory=list)
    adjustments: list[AdjustCell] = field(default_factory=list)
    plan: list[PlanCell] = field(default_factory=list)
    flags: list[CellFlag] = field(default_factory=list)
    desadv: list[DesadvLine] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Parameters
# =============================================================================
@dataclass
class EngineParams:
    """All business rules that are configurable (see docs/regles_metier.md)."""

    as_of: dt.date | None = None                  # « today » of the computation ; default: the real date of the day
    horizon_days: int = 120                       # projection horizon after as_of
    history_weeks: int = 2                        # weeks shown before the current week (display start = max(init date, Monday − N weeks))
    working_weekdays: tuple[int, ...] = (1, 2, 3, 4, 5)

    # Demand
    spread_rounding: Literal["none", "exact", "per_day"] = "exact"
    # actual_then_remainder [default]: past days = reported actual production ; current ISO week =
    # remainder of the weekly PDP (PDP − actual consumption already reported) spread over the remaining open
    # days ; later weeks = PDP.  Other modes: actual_then_plan, plan_only, actual_only.
    production_mode: Literal["actual_then_remainder", "actual_then_plan", "plan_only", "actual_only"] = "actual_then_remainder"
    missing_actual_policy: Literal["plan", "zero"] = "zero"   # past day without actual report: plan or 0
    consumption_offset_days: int = 0              # <0: components consumed before the production day

    # Supply – two stock scenarios: ERP (firm orders as is) and Plan (the planner's plan cells)
    firm_sources: tuple[str, ...] = ("FIRM",)                    # order types counted in the ERP stock
    forecast_date_policy: Literal["actual", "week_monday"] = "actual"  # forecast orders on their date or on the Monday
    include_proposals_in_plan: bool = True        # the CBN complement is part of the plan stock
    backlog_days: int = 28                        # age limit of the backlog (past firm orders − receipts)

    # Coverage / stock policy
    shortage_policy: Literal["backlog", "lost"] = "backlog"  # unserved demand: carried forward or lost
    coverage_unit: Literal["calendar", "working"] = "calendar"
    coverage_tie_rule: Literal["covered", "not_covered"] = "covered"
    target_policy: Literal["coverage_days", "safety_qty", "max"] = "max"

    # Proposals (net requirements, "Complément CBN"): computed on every run on the plan stock
    generate_proposals: bool = True
    proposal_placement: Literal["working_days", "monday"] = "working_days"  # delivery on any working day or Mondays only
    shortfall_tolerance_days: int = 0             # ignore dips under target that recover within N working days without shortage
    frozen_days: int = 0
    respect_lead_time: bool = False               # True: never propose a delivery before as_of + lead time
    delivery_shift: Literal["earlier", "later"] = "earlier"
    sourcing_policy: Literal["quota", "priority"] = "quota"
    proposal_lookahead_days: int | None = None   # None: whole horizon

    # Alerts
    stockout_lookahead_days: int | None = None    # None: whole horizon
    firm_horizon_days: int = 28                   # firm-flow stockouts beyond this are informational

    # Display
    focus_weeks: int = 2                          # default calendar: days for the current week + N weeks, then weeks

    def copy_with(self, **changes: Any) -> "EngineParams":
        data = self.__dict__.copy()
        data.update(changes)
        return EngineParams(**data)


# =============================================================================
# Outputs
# =============================================================================
class AlertType(str, Enum):
    STOCKOUT = "STOCKOUT"
    LOW_COVERAGE = "LOW_COVERAGE"
    OVERSTOCK = "OVERSTOCK"
    BACKLOG = "BACKLOG"
    URGENT_PROPOSAL = "URGENT_PROPOSAL"
    NO_DEMAND = "NO_DEMAND"
    MISSING_DATA = "MISSING_DATA"
    NEGATIVE_STOCK = "NEGATIVE_STOCK"


class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


@dataclass
class Alert:
    article_id: str
    alert_type: AlertType
    severity: Severity
    message: str
    date: dt.date | None = None
    value: float | None = None
    scope: Literal["erp", "plan", "data"] = "plan"
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class Proposal:
    proposal_id: str
    article_id: str
    supplier_id: str | None
    delivery_date: dt.date
    order_date: dt.date
    qty: float
    net_requirement: float
    reason: str
    urgent: bool = False
    lead_time_days: int = 0
    moq: float = 0.0
    pack_qty: float = 0.0
    projected_stock_before: float = 0.0
    projected_stock_after: float = 0.0


@dataclass
class Lane:
    """The supply flows of one supplier of the article (one lane of the grid)."""

    supplier_id: str | None
    name: str
    orders_firm: np.ndarray           # open firm ERP orders counted in the ERP stock (reference day matched, ignored excluded)
    orders_firm_hist: np.ndarray      # past firm orders, ordered quantity (display only)
    orders_forecast: np.ndarray       # forecast ERP orders (information)
    receipts: np.ndarray              # receipts (past by construction, reference day included)
    plan: np.ndarray                  # plan counted in the plan stock (typed cells, else ERP), CBN excluded
    supply_proposed: np.ndarray       # CBN complement placed on this supplier
    plan_typed: np.ndarray            # bool: a plan cell is stored that day
    orders_firm_ordered: np.ndarray   # Σ ordered quantity of the firm orders of the day (any date, display)
    orders_firm_open: np.ndarray      # Σ ERP remaining quantity of the same orders (display)
    orders_ignored: np.ndarray        # bool: the firm orders of the day are ignored (planner click)
    backlog_ordered: float            # past firm orders of the backlog window (ordered quantity)
    backlog_received: float           # receipts of the same window
    backlog_qty: float                # max(0, ordered − received)
    orders: list["OrderInfo"] = field(default_factory=list)
    desadv_open: np.ndarray | None = None   # announced, not received, not hidden DESADV quantity (display only)
    desadv_ko: np.ndarray | None = None     # bool: an unreceived DESADV of the day is not « Traité / OK »
    receipts_ko: np.ndarray | None = None   # bool: a receipt of the day has no processed DESADV for its BL
    desadv: list["DesadvInfo"] = field(default_factory=list)
    receipt_lines: list["ReceiptInfo"] = field(default_factory=list)   # the receipts of the lane, one per ERP line (tooltips)


@dataclass
class DesadvInfo:
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


@dataclass
class ReceiptInfo:
    """One ERP receipt line as displayed in the tooltips of the Reçu row.  ``packing_slip`` is empty
    when the ERP has no delivery note yet (shown as « ACR non validé ») ; ``processed`` tells whether
    the delivery note matches a processed DESADV (green dot)."""

    receipt_id: str
    receipt_date: dt.date
    qty: float
    packing_slip: str
    purch_id: str
    processed: bool


@dataclass
class OrderInfo:
    """One ERP delivery slot as displayed in the tooltips / order lists."""

    order_id: str
    supplier_id: str | None
    order_type: str
    expected_date: dt.date
    qty_ordered: float
    qty_open: float
    ref: str = ""
    ignored: bool = False


@dataclass
class ArticleResult:
    article: Article
    start_date: dt.date
    as_of: dt.date
    dates: list[dt.date]
    # daily NumPy series aligned on ``dates`` (kept as arrays: no copy, 3× less memory than lists)
    demand: np.ndarray               # consumption (past, actual-based) + requirement (future)
    consumed: np.ndarray             # past part of ``demand`` (0 from the reference day)
    required: np.ndarray             # future part of ``demand`` (0 before the reference day)
    demand_plan: np.ndarray          # PDP only, for information
    demand_actual_share: np.ndarray
    orders_firm: np.ndarray          # Σ lanes
    orders_firm_hist: np.ndarray
    orders_forecast: np.ndarray
    receipts: np.ndarray
    plan: np.ndarray
    supply_proposed: np.ndarray      # CBN complement
    adjustments: np.ndarray          # planner adjustments (future days ; past ones correct the reference stock)
    reference_correction: float       # sum of the adjustments dated on/before the snapshot day
    # physical stocks (never negative), unserved demand and net balances per scenario ; before the
    # reference day both hold the reconstructed history
    stock_erp: np.ndarray
    stock_plan: np.ndarray
    shortage_onhand: np.ndarray
    shortage_erp: np.ndarray
    shortage_plan: np.ndarray
    stock_erp_net: np.ndarray
    stock_plan_net: np.ndarray
    coverage_erp: np.ndarray
    coverage_plan: np.ndarray
    target_stock: np.ndarray
    lanes: list[Lane]
    alerts: list[Alert]
    proposals: list[Proposal]
    kpis: dict[str, Any]
    suppliers: list[SupplierLink]
    diagnostics: list[str] = field(default_factory=list)


@dataclass
class MrpResult:
    as_of: dt.date            # « today » of the computation
    init_date: dt.date        # stock initialisation day = first day of the window (point zero)
    start_date: dt.date
    end_date: dt.date
    params: EngineParams
    articles: dict[str, ArticleResult]
    program_daily: dict[str, dict[dt.date, float]]
    diagnostics: list[str] = field(default_factory=list)
    program_impact: dict[str, Any] = field(default_factory=dict)   # see engine/programs.py

    @property
    def alerts(self) -> list[Alert]:
        return [a for r in self.articles.values() for a in r.alerts]

    @property
    def proposals(self) -> list[Proposal]:
        return [p for r in self.articles.values() for p in r.proposals]
