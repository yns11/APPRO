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
    coverage_target_days: int = 5          # working days of requirement to hold (target stock)
    alert_red_days: int = 2
    alert_yellow_days: int = 5
    overstock_days: int = 30
    demand_days: int = 5                   # the weekly requirement is spread over the first n open days of the week
    sourcing_policy: Literal["quota", "priority"] = "quota"
    consumption_source: Literal["erp", "plan", "derived"] = "erp"   # past consumption: ERP, exploded PDP, or derived
    derived_terms: list[tuple[str, float]] = field(default_factory=list)   # (source article, coefficient) when derived
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


WEEKLY_FIELDS = ("coverage_target_days", "alert_red_days", "alert_yellow_days", "overstock_days")


@dataclass
class Supplier:
    supplier_id: str
    name: str = ""
    country: str = ""
    contact: str = ""
    delivery_weekdays: frozenset[int] = frozenset({1, 2, 3, 4, 5})
    lead_time_days: int = 5        # default lead time of the supplier, working days, inclusive (Monday + 5 = next Monday)
    active: bool = True
    strict_days: bool = False      # True : deliveries on the standard weekdays only (no « hors jour standard » proposal)


@dataclass
class SupplierLink:
    """Article ↔ supplier sourcing rule (MOQ, packaging, lead time, quota, delivery caps)."""

    article_id: str
    supplier_id: str
    moq: float = 0.0
    pack_qty: float = 0.0          # "UM" – packaging / rounding quantity
    lead_time_days: int | None = None      # working days ; None = the supplier's lead time
    quota_pct: float = 100.0
    priority: int = 1
    active: bool = True
    max_qty_per_delivery: float | None = None   # cap of one delivery (multiple of the UM) ; None = none
    max_qty_per_week: float | None = None       # cap of one ISO week, firm orders and plan included ; None = none

    def lead_time(self, suppliers: dict[str, "Supplier"] | None = None) -> int:
        """Effective lead time: the link's own value, else the supplier's."""
        if self.lead_time_days is not None:
            return int(self.lead_time_days)
        sup = (suppliers or {}).get(self.supplier_id)
        return int(sup.lead_time_days) if sup is not None else 5


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
    scrap_pct: float = 0.0   # programme scrap rate in force that week : the need is qty × (1 + scrap_pct / 100)


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
    first_seen: dt.date | None = None      # day the slot first appeared in the ERP copy (synchronisation job)

    def __post_init__(self) -> None:
        if self.qty_open is None:
            self.qty_open = float(self.qty_ordered)


def order_kind(purch_id: str | None) -> str:
    """``co`` for an open order (``CO-…``), ``po`` for a closed purchase order (six digits), ``other``."""
    ref = (purch_id or "").strip()
    if ref.upper().startswith("CO-"):
        return "co"
    if len(ref) == 6 and ref.isdigit():
        return "po"
    return "other"


@dataclass
class Receipt:
    receipt_id: str
    article_id: str
    receipt_date: dt.date
    qty: float
    supplier_id: str | None = None
    ref: str = ""
    packing_slip: str = ""         # delivery note number(s) (BL, several joined by " | ") : matched with the despatch advices
    status: str = ""               # En transit (arrival journal not validated) / Reçu (validated, not booked) / Comptabilisé (ACR validated)
    order_kind: str = "co"         # co (open order CO-…) / po (closed order, six digits) / other
    counted: bool = True           # counted in the Reçu row and the stock : open orders and the declared closed orders

    @property
    def in_transit(self) -> bool:
        """Arrival journal created but not validated (``posted = 0``) : the goods are on their way, nothing
        is in stock ; shown as « En transit » in the Reçu row, never counted."""
        return "transit" in self.status.strip().lower()

    @property
    def booked(self) -> bool:
        """Acknowledgement of receipt validated (« Comptabilisé ») : green background in the Reçu row."""
        return self.status.strip().lower().startswith("comptabilis")

    @property
    def packing_slips(self) -> list[str]:
        return [x.strip() for x in (self.packing_slip or "").split("|") if x.strip()]


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
    stock_trans_id: str = ""       # ID_transaction_stock : empty on a processed message = no entry journal created

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
      empty): the proposal of that day is refused : **no proposal may be placed on that day** for
      that supplier (the need moves to its next delivery day) ; ``qty`` keeps the refused quantity
      for display."""

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
    prices: dict[str, float] = field(default_factory=dict)        # article → standard cost price (euros)
    meta: dict[str, Any] = field(default_factory=dict)


# =============================================================================
# Parameters
# =============================================================================
@dataclass
class EngineParams:
    """The few rules that stay configurable (docs/regles_metier.md).  Everything else is fixed :
    working days everywhere, unserved demand lost, coverage target as the only stock target,
    proposals on the delivery days of the supplier, firm horizon of 20 working days."""

    as_of: dt.date | None = None                  # « today » of the computation ; default: the real date of the day
    horizon_days: int = 364                       # projection horizon after as_of : 52 weeks, fixed in the application
    history_weeks: int = 2                        # display : weeks shown before the current week
    focus_weeks: int = 2                          # display : weeks detailed day by day after the current week
    working_weekdays: tuple[int, ...] = (1, 2, 3, 4, 5)
    target_tolerance_pct: float = 15.0            # a proposal is triggered under (100 − n) % of the target, and fills it to 100 %
    lead_margin_days: int = 1                     # order deadline = delivery − (lead time − margin) working days ; 0 = strict lead time

    def copy_with(self, **changes: Any) -> "EngineParams":
        data = self.__dict__.copy()
        data.update(changes)
        return EngineParams(**data)


#: horizon (working days) under which an ERP stockout must be acted upon ; beyond it is informational


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
    CAP_REACHED = "CAP_REACHED"
    UNLINKED_FLOWS = "UNLINKED_FLOWS"
    ORDER_DUE = "ORDER_DUE"          # a CBN proposal to order within the next five working days


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
    ignored: bool = False          # refused by the planner (click) : listed for information, out of every calculation
    floor: bool = False            # urgent proposal limited to what avoids a shortage (inside the lead time)
    capped: bool = False           # truncated by a delivery / weekly cap of the supplier link
    off_standard: bool = False     # « hors jour standard » (◇) : placed on a tenable working day that is not a standard delivery day


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
    backlog_ordered: float            # Σ ordered quantity of the firm orders dated before the reference day still open in the ERP
    backlog_received: float           # ordered − remaining of the same orders (what the ERP already received)
    backlog_qty: float                # Σ ERP remaining quantity of those orders (ignored ones included : they are late)
    orders: list["OrderInfo"] = field(default_factory=list)
    desadv_open: np.ndarray | None = None   # announced, not received, not hidden DESADV quantity (display only)
    desadv_ko: np.ndarray | None = None     # bool: an unreceived DESADV of the day is not « Traité / OK »
    receipts_ko: np.ndarray | None = None   # bool: a receipt of the day has no processed DESADV for its BL
    receipts_booked: np.ndarray | None = None   # bool: every receipt of the day is booked (ACR validated) : green background
    desadv: list["DesadvInfo"] = field(default_factory=list)
    receipt_lines: list["ReceiptInfo"] = field(default_factory=list)   # the receipts of the lane, one per ERP line (tooltips)
    linked: bool = True               # False : no active link, the lane only exists for typed plan cells


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
    stock_trans_id: str = ""
    journaled: bool = False        # its BL is carried by an arrival journal not validated yet (shown through that journal)
    source: str = "desadv"         # desadv (announced by EDI) or journal (arrival journal not validated : « En transit »)


@dataclass
class ReceiptInfo:
    """One ERP receipt line as displayed in the tooltips of the Reçu row.  ``packing_slip`` is empty
    when the ERP has no delivery note (manual receipt) ; ``processed`` tells whether the delivery note
    matches a processed DESADV (green dot) ; ``booked`` whether the acknowledgement of receipt is
    validated (green background)."""

    receipt_id: str
    receipt_date: dt.date
    qty: float
    packing_slip: str
    purch_id: str
    processed: bool
    status: str = ""               # Reçu / Comptabilisé
    booked: bool = False
    order_kind: str = "co"         # co / po : a closed order has no DESADV, never flagged for it


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
    shortage_erp: np.ndarray         # unserved demand of the day (lost)
    shortage_plan: np.ndarray
    shortage_erp_cum: np.ndarray     # running sum of the unserved demand, reset when the stock is positive again (display)
    shortage_plan_cum: np.ndarray
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
    proposals_ignored: list[Proposal] = field(default_factory=list)   # refused urgent / normal proposals, information only
    price: float | None = None                                         # standard cost price (euros), None = unknown
    cap_shortfalls: list["CapShortfall"] = field(default_factory=list) # needs that no delivery cap could absorb
    unlinked: list[Any] = field(default_factory=list)                  # ERP flows of suppliers without an active link (out of the table)


@dataclass
class CapShortfall:
    """A need the CBN could not place because every delivery day of the supplier(s) was full
    (delivery or weekly cap) : the shortage stays, the alert names the cap."""

    date: dt.date
    supplier_id: str | None
    qty: float


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
    holidays: list[dt.date] = field(default_factory=list)          # plant closures of the dataset (Excel export)

    @property
    def alerts(self) -> list[Alert]:
        return [a for r in self.articles.values() for a in r.alerts]

    @property
    def proposals(self) -> list[Proposal]:
        return [p for r in self.articles.values() for p in r.proposals]
