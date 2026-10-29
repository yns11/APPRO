"""Build an engine :class:`Dataset` from the canonical frames (reference + ERP facts)."""
from __future__ import annotations

import datetime as dt
from typing import Callable, Iterable

import pandas as pd

from ..engine.models import (
    Article,
    BomLine,
    ConsumptionLine,
    Dataset,
    DesadvLine,
    OrderLine,
    OrderType,
    PdpLine,
    Program,
    Receipt,
    StockObservation,
    StockSnapshot,
    Supplier,
    SupplierLink,
    order_kind,
)
from .schemas import as_date, parse_weekdays

Tables = Callable[[str], pd.DataFrame]


def _records(df: pd.DataFrame) -> Iterable[dict]:
    return df.to_dict("records")


def _missing(v) -> bool:
    """Empty cell (None, "", NaN) – **0 is a value**, never a missing one."""
    return v is None or v == "" or (isinstance(v, float) and v != v)


def _int(v, default: int) -> int:
    return default if _missing(v) else int(float(v))


def _float(v, default: float) -> float:
    return default if _missing(v) else float(v)


def _demand_days(v) -> int:
    """1..5 ; an empty or zero cell means the uniform profile (5)."""
    n = _int(v, 5)
    return 5 if n <= 0 else min(n, 5)


def _lead(v) -> int:
    """Supplier lead time in working days ; an empty or zero cell means the default (5)."""
    n = _int(v, 5)
    return 5 if n <= 0 else n


def _optional_int(v) -> int | None:
    """Empty or zero = no value (the link inherits the supplier's lead time)."""
    n = _int(v, 0)
    return n if n > 0 else None


def _optional_float(v) -> float | None:
    x = _float(v, 0.0)
    return x if x > 0 else None


def _choice(v, allowed: tuple[str, ...], default: str) -> str:
    x = str(v or "").strip().lower()
    return x if x in allowed else default


def _tables(source) -> Tables:
    return source if callable(source) else source.table


def erp_dataset(source, planner: str | None = None, article_ids: list[str] | None = None,
                holidays: list[dt.date] | None = None) -> Dataset:
    """Convert the canonical frames into engine records.  ``source`` is a callable ``name → frame``
    or an object with a ``table(name)`` method."""
    t = _tables(source)
    arts = t("ref_articles")
    if planner:
        arts = arts[arts["planner"].str.upper() == planner.upper()]
    if article_ids:
        arts = arts[arts["article_id"].isin(article_ids)]
    ids = set(arts["article_id"])

    terms: dict[str, list[tuple[str, float]]] = {}
    for r in _records(t("ref_consumption_terms")):
        if r["article_id"] in ids and r["source_article_id"] and float(r["coef"] or 0) > 0:
            terms.setdefault(r["article_id"], []).append((str(r["source_article_id"]), float(r["coef"])))
    articles = [Article(
        article_id=r["article_id"], designation=r["designation"], unit=r["unit"] or "PCE", family=r["family"],
        planner=r["planner"], coverage_target_days=_int(r["coverage_target_days"], 5),
        alert_red_days=_int(r["alert_red_days"], 2), alert_yellow_days=_int(r["alert_yellow_days"], 5),
        overstock_days=_int(r["overstock_days"], 30), demand_days=_demand_days(r.get("demand_days")),
        sourcing_policy=_choice(r.get("sourcing_policy"), ("quota", "priority"), "quota"),
        consumption_source=_choice(r.get("consumption_source"), ("erp", "plan", "derived"), "erp"),
        derived_terms=terms.get(r["article_id"], []), active=bool(r["active"]),
    ) for r in _records(arts)]
    # a derived article without any term keeps its choice (the screens show it) : the demand builder
    # uses the article's own ERP consumption until terms are declared
    source_ids = {src for a in articles for src, _c in a.derived_terms}

    suppliers = [Supplier(r["supplier_id"], r["name"], r["country"], r["contact"], parse_weekdays(r["delivery_weekdays"]),
                          _lead(r.get("lead_time_days")), bool(r["active"]), strict_days=bool(r.get("strict_days") or False),
                          transport_contact=str(r.get("transport_contact") or ""), docks=str(r.get("docks") or "") or "B15")
                 for r in _records(t("ref_suppliers"))]
    links = [SupplierLink(r["article_id"], r["supplier_id"], float(r["moq"]), float(r["pack_qty"]),
                          _optional_int(r.get("lead_time_override")), float(r["quota_pct"]), _int(r["priority"], 1), bool(r["active"]),
                          _optional_float(r.get("max_qty_per_delivery")), _optional_float(r.get("max_qty_per_week")))
             for r in _records(t("ref_article_suppliers")) if r["article_id"] in ids]
    programs = [Program(r["program_id"], r["name"], r["family"], bool(r["active"])) for r in _records(t("ref_programs"))]
    bom = [BomLine(r["program_id"], r["article_id"], float(r["qty_per"]), r["unit"], _float(r["scrap_pct"], 0.0),
                   as_date(r["valid_from"]), as_date(r["valid_to"]))
           for r in _records(t("ref_bom")) if r["article_id"] in ids]

    pdp: list[PdpLine] = []        # the production plan comes from the application's versions (mrp_service.app_entries_into_dataset)
    consumption = [ConsumptionLine(r["article_id"], as_date(r["date"]), float(r["qty"]))
                   for r in _records(t("fct_consumption_actual")) if (r["article_id"] in ids or r["article_id"] in source_ids) and r["date"]]

    orders = [OrderLine(order_id=r["order_id"], article_id=r["article_id"], supplier_id=r["supplier_id"] or None,
                        expected_date=as_date(r["expected_date"]), qty_ordered=float(r["qty_ordered"]),
                        qty_open=float(r["qty_open"]), order_type=OrderType(r["order_type"] or "FIRM"), ref=r["purch_id"],
                        first_seen=as_date(r.get("first_seen")))
              for r in _records(t("fct_purchase_orders")) if r["article_id"] in ids and r["expected_date"]]
    # receipts counted in the stock : those of an open order (CO-…) and of the closed orders (PO) declared in
    # the Référentiel ; every other receipt stays visible (search, flows, supplier page) without counting
    declared = {str(r["purch_id"]).strip() for r in _records(t("ref_closed_orders")) if bool(r.get("active", True))}
    receipts = []
    for r in _records(t("fct_receipts")):
        if r["article_id"] not in ids or not r["receipt_date"]:
            continue
        kind = order_kind(r["purch_id"])
        receipts.append(Receipt(r["receipt_id"], r["article_id"], as_date(r["receipt_date"]), float(r["qty"]),
                                r["supplier_id"] or None, r["purch_id"], str(r.get("packing_slip") or ""), str(r.get("status") or ""),
                                order_kind=kind, counted=kind == "co" or (kind == "po" and str(r["purch_id"]).strip() in declared)))
    desadv = [DesadvLine(r["desadv_id"], r["article_id"], r["supplier_id"] or None, str(r["packing_slip"] or ""),
                         as_date(r["issue_date"]), float(r["qty"] or 0.0), str(r.get("purch_id") or ""),
                         str(r.get("state") or ""), str(r.get("final_processing") or ""), str(r.get("stock_trans_id") or ""))
              for r in _records(t("fct_desadv")) if r["article_id"] in ids and r["issue_date"]]   # no BL yet : still shown
    # prices : the item master holds far more items than the reference ; only the reference articles are valued
    prices = {r["article_id"]: float(r["price"]) for r in _records(t("fct_prices")) if r["article_id"] in ids and not _missing(r["price"])}
    # ERP stock snapshots (comparison with the projected stock only ; the reference stock stays fct_stock)
    erp_stock = [StockObservation(r["article_id"], as_date(r["snapshot_date"]), float(r["qty"]), str(r.get("warehouse") or ""))
                 for r in _records(t("fct_stock_snapshot")) if r["article_id"] in ids and r["snapshot_date"] and not _missing(r["qty"])]
    stock: dict[str, StockSnapshot] = {}
    for r in _records(t("fct_stock")):
        if r["article_id"] not in ids or not r["snapshot_date"]:
            continue
        d = as_date(r["snapshot_date"])
        cur = stock.get(r["article_id"])
        if cur is None or d > cur.snapshot_date:
            stock[r["article_id"]] = StockSnapshot(r["article_id"], d, float(r["qty_on_hand"]), float(r["qty_blocked"] or 0))
        elif d == cur.snapshot_date:
            cur.qty_on_hand += float(r["qty_on_hand"])
            cur.qty_blocked += float(r["qty_blocked"] or 0)

    return Dataset(articles=articles, suppliers=suppliers, links=links, programs=programs, bom=bom, pdp=pdp,
                   consumption=consumption, desadv=desadv, orders=orders, receipts=receipts, stock=list(stock.values()),
                   holidays=list(holidays or []), prices=prices, erp_stock=erp_stock)
