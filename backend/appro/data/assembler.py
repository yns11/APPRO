"""Build an engine :class:`Dataset` from the canonical frames (reference + ERP facts)."""
from __future__ import annotations

import datetime as dt
from typing import Callable, Iterable

import pandas as pd

from ..engine.models import (
    Article,
    BlPending,
    BomLine,
    ConsumptionLine,
    Dataset,
    DesadvLine,
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


def _missing(v) -> bool:
    """Empty cell (None, "", NaN) – **0 is a value**, never a missing one."""
    return v is None or v == "" or (isinstance(v, float) and v != v)


def _int(v, default: int) -> int:
    return default if _missing(v) else int(float(v))


def _float(v, default: float) -> float:
    return default if _missing(v) else float(v)


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
        planner=r["planner"], coverage_target_days=_int(r["coverage_target_days"], 7),
        alert_red_days=_int(r["alert_red_days"], 3), alert_yellow_days=_int(r["alert_yellow_days"], 7),
        overstock_days=_int(r["overstock_days"], 30), safety_stock_qty=_float(r["safety_stock_qty"], 0.0),
        order_cycle_days=_int(r["order_cycle_days"], 7), active=bool(r["active"]),
    ) for r in _records(arts)]

    suppliers = [Supplier(r["supplier_id"], r["name"], r["country"], r["contact"], parse_weekdays(r["delivery_weekdays"]),
                          bool(r["active"])) for r in _records(t("ref_suppliers"))]
    links = [SupplierLink(r["article_id"], r["supplier_id"], float(r["moq"]), float(r["pack_qty"]),
                          int(r["lead_time_days"]), float(r["quota_pct"]), _int(r["priority"], 1), bool(r["active"]))
             for r in _records(t("ref_article_suppliers")) if r["article_id"] in ids]
    programs = [Program(r["program_id"], r["name"], r["family"], bool(r["active"])) for r in _records(t("ref_programs"))]
    bom = [BomLine(r["program_id"], r["article_id"], float(r["qty_per"]), r["unit"], _float(r["scrap_pct"], 0.0),
                   as_date(r["valid_from"]), as_date(r["valid_to"]))
           for r in _records(t("ref_bom")) if r["article_id"] in ids]
    needed_programs = {b.program_id for b in bom}

    pdp = [PdpLine(r["program_id"], as_date(r["week_start"]), float(r["qty"]), r["version"])
           for r in _records(t("fct_production_plan")) if r["program_id"] in needed_programs and r["week_start"]]
    consumption = [ConsumptionLine(r["article_id"], as_date(r["date"]), float(r["qty"]))
                   for r in _records(t("fct_consumption_actual")) if r["article_id"] in ids and r["date"]]

    orders = [OrderLine(order_id=r["order_id"], article_id=r["article_id"], supplier_id=r["supplier_id"] or None,
                        expected_date=as_date(r["expected_date"]), qty_ordered=float(r["qty_ordered"]),
                        qty_open=float(r["qty_open"]), order_type=OrderType(r["order_type"] or "FIRM"), ref=r["purch_id"],
                        first_seen=as_date(r.get("first_seen")))
              for r in _records(t("fct_purchase_orders")) if r["article_id"] in ids and r["expected_date"]]
    receipts = [Receipt(r["receipt_id"], r["article_id"], as_date(r["receipt_date"]), float(r["qty"]),
                        r["supplier_id"] or None, r["purch_id"], str(r.get("packing_slip") or ""), str(r.get("status") or ""))
                for r in _records(t("fct_receipts")) if r["article_id"] in ids and r["receipt_date"]]
    desadv = [DesadvLine(r["desadv_id"], r["article_id"], r["supplier_id"] or None, str(r["packing_slip"] or ""),
                         as_date(r["issue_date"]), float(r["qty"] or 0.0), str(r.get("purch_id") or ""),
                         str(r.get("state") or ""), str(r.get("final_processing") or ""), str(r.get("stock_trans_id") or ""))
              for r in _records(t("fct_desadv")) if r["article_id"] in ids and r["issue_date"]]   # no BL yet : still shown
    # prices : the item master holds far more items than the reference ; only the reference articles are valued
    prices = {r["article_id"]: float(r["price"]) for r in _records(t("fct_prices")) if r["article_id"] in ids and not _missing(r["price"])}
    bl_pending = [BlPending(r["pending_id"], r["supplier_id"] or None, str(r.get("purch_id") or ""), str(r.get("packing_slip") or ""),
                            r["article_id"], float(r["qty"] or 0.0), as_date(r["registered_date"]), _int(r.get("days_pending"), 0))
                  for r in _records(t("fct_bl_pending")) if r["article_id"] in ids and r["registered_date"]]
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
                   holidays=list(holidays or []), prices=prices, bl_pending=bl_pending)
