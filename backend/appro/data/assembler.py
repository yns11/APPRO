"""Build an engine :class:`Dataset` from an ERP source plus the app store entries."""
from __future__ import annotations

import datetime as dt
from typing import Iterable

import pandas as pd

from ..engine.models import (ActualLine, Article, BomLine, Dataset, Movement, OrderLine, OrderStatus, OrderType,
                             PlanLine, Program, Receipt, StockSnapshot, Supplier, SupplierLink)
from .schemas import as_date, parse_weekdays


def _records(df: pd.DataFrame) -> Iterable[dict]:
    return df.to_dict("records")


def erp_dataset(source, planner: str | None = None, article_ids: list[str] | None = None,
                holidays: list[dt.date] | None = None) -> Dataset:
    """Convert the canonical ERP frames into engine records (ERP data only)."""
    arts = source.table("ref_articles")
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
        active=bool(r["active"]), service_rate_tracked=bool(r["service_rate_tracked"]), dhrq=r["dhrq"],
    ) for r in _records(arts)]

    sup = source.table("ref_suppliers")
    suppliers = [Supplier(r["supplier_id"], r["name"], r["country"], r["contact"],
                          parse_weekdays(r["delivery_weekdays"]), r["calendar_id"] or "DEFAULT", bool(r["active"]))
                 for r in _records(sup)]

    lk = source.table("ref_article_suppliers")
    links = [SupplierLink(r["article_id"], r["supplier_id"], float(r["moq"]), float(r["pack_qty"]),
                          int(r["lead_time_days"]), float(r["quota_pct"]), int(r["priority"] or 1), bool(r["active"]))
             for r in _records(lk) if r["article_id"] in ids]

    prg = source.table("ref_programs")
    programs = [Program(r["program_id"], r["name"], r["family"], bool(r["active"])) for r in _records(prg)]

    bom_df = source.table("ref_bom")
    bom = [BomLine(r["program_id"], r["article_id"], float(r["qty_per"]), r["unit"], float(r["scrap_pct"] or 0),
                   as_date(r["valid_from"]), as_date(r["valid_to"]))
           for r in _records(bom_df) if r["article_id"] in ids]
    needed_programs = {b.program_id for b in bom}

    plan = [PlanLine(r["program_id"], as_date(r["week_start"]), float(r["qty"]), r["version"])
            for r in _records(source.table("fct_production_plan")) if r["program_id"] in needed_programs]
    actuals = [ActualLine(r["program_id"], as_date(r["date"]), float(r["qty"]))
               for r in _records(source.table("fct_production_actual")) if r["program_id"] in needed_programs]

    orders = []
    for r in _records(source.table("fct_purchase_orders")):
        if r["article_id"] not in ids:
            continue
        oid = r["order_id"] if int(r["line_no"] or 1) == 1 else f"{r['order_id']}/{r['line_no']}"
        orders.append(OrderLine(
            order_id=oid, article_id=r["article_id"], supplier_id=r["supplier_id"] or None,
            expected_date=as_date(r["expected_date"]), qty_ordered=float(r["qty_ordered"]),
            qty_received=float(r["qty_received"] or 0), order_type=OrderType(r["order_type"] or "FIRM"),
            status=OrderStatus(r["status"] or "OPEN"), source="ERP", order_date=as_date(r["order_date"]),
            note=r["message_type"]))
    receipts = [Receipt(r["receipt_id"], r["article_id"], as_date(r["receipt_date"]), float(r["qty"]),
                        r["supplier_id"] or None, r["order_id"] or None, "ERP")
                for r in _records(source.table("fct_receipts")) if r["article_id"] in ids]
    movements = [Movement(r["movement_id"], r["article_id"], as_date(r["date"]), float(r["qty"]),
                          r["movement_type"], "ERP", r["comment"])
                 for r in _records(source.table("fct_stock_movements")) if r["article_id"] in ids]
    stock_df = source.table("fct_stock")
    stock: dict[str, StockSnapshot] = {}
    for r in _records(stock_df):
        if r["article_id"] not in ids:
            continue
        d = as_date(r["snapshot_date"])
        cur = stock.get(r["article_id"])
        if cur is None or d > cur.snapshot_date:
            stock[r["article_id"]] = StockSnapshot(r["article_id"], d, float(r["qty_on_hand"]), float(r["qty_blocked"] or 0))
        elif d == cur.snapshot_date:  # several locations on the same day
            cur.qty_on_hand += float(r["qty_on_hand"])
            cur.qty_blocked += float(r["qty_blocked"] or 0)

    return Dataset(articles=articles, suppliers=suppliers, links=links, programs=programs, bom=bom, plan=plan,
                   actuals=actuals, orders=orders, receipts=receipts, movements=movements,
                   stock=list(stock.values()), holidays=list(holidays or []), meta={"source": source.name})
