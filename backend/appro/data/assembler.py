"""Build an engine :class:`Dataset` from the canonical frames (reference + ERP facts)."""
from __future__ import annotations

import datetime as dt
from typing import Callable, Iterable

import pandas as pd

from ..engine.models import (
    ActualLine,
    Article,
    BomLine,
    Dataset,
    OrderLine,
    OrderType,
    PdpLine,
    Program,
    Receipt,
    StockSnapshot,
    Supplier,
    SupplierLink,
)
from .schemas import as_date, parse_weekdays

Tables = Callable[[str], pd.DataFrame]


def _records(df: pd.DataFrame) -> Iterable[dict]:
    return df.to_dict("records")


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

    articles = [Article(
        article_id=r["article_id"], designation=r["designation"], unit=r["unit"] or "PCE", family=r["family"],
        planner=r["planner"], coverage_target_days=int(r["coverage_target_days"] or 7),
        alert_red_days=int(r["alert_red_days"] or 3), alert_yellow_days=int(r["alert_yellow_days"] or 7),
        overstock_days=int(r["overstock_days"] or 30), safety_stock_qty=float(r["safety_stock_qty"] or 0.0),
        order_cycle_days=int(r["order_cycle_days"] or 7), active=bool(r["active"]),
    ) for r in _records(arts)]

    suppliers = [Supplier(r["supplier_id"], r["name"], r["country"], r["contact"], parse_weekdays(r["delivery_weekdays"]),
                          bool(r["active"])) for r in _records(t("ref_suppliers"))]
    links = [SupplierLink(r["article_id"], r["supplier_id"], float(r["moq"]), float(r["pack_qty"]),
                          int(r["lead_time_days"]), float(r["quota_pct"]), int(r["priority"] or 1), bool(r["active"]))
             for r in _records(t("ref_article_suppliers")) if r["article_id"] in ids]
    programs = [Program(r["program_id"], r["name"], r["family"], bool(r["active"])) for r in _records(t("ref_programs"))]
    bom = [BomLine(r["program_id"], r["article_id"], float(r["qty_per"]), r["unit"], float(r["scrap_pct"] or 0),
                   as_date(r["valid_from"]), as_date(r["valid_to"]))
           for r in _records(t("ref_bom")) if r["article_id"] in ids]
    needed_programs = {b.program_id for b in bom}

    pdp = [PdpLine(r["program_id"], as_date(r["week_start"]), float(r["qty"]), r["version"])
           for r in _records(t("fct_production_plan")) if r["program_id"] in needed_programs and r["week_start"]]
    actuals = [ActualLine(r["program_id"], as_date(r["date"]), float(r["qty"]))
               for r in _records(t("fct_production_actual")) if r["program_id"] in needed_programs and r["date"]]

    orders = [OrderLine(order_id=r["order_id"], article_id=r["article_id"], supplier_id=r["supplier_id"] or None,
                        expected_date=as_date(r["expected_date"]), qty_ordered=float(r["qty_ordered"]),
                        qty_open=float(r["qty_open"]), order_type=OrderType(r["order_type"] or "FIRM"), ref=r["purch_id"])
              for r in _records(t("fct_purchase_orders")) if r["article_id"] in ids and r["expected_date"]]
    receipts = [Receipt(r["receipt_id"], r["article_id"], as_date(r["receipt_date"]), float(r["qty"]),
                        r["supplier_id"] or None, r["purch_id"])
                for r in _records(t("fct_receipts")) if r["article_id"] in ids and r["receipt_date"]]
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
                   actuals=actuals, orders=orders, receipts=receipts, stock=list(stock.values()),
                   holidays=list(holidays or []))
