"""Canonical table schemas (columns and types) for the ERP / reference tables.

The same names are used for the seed CSV files, the Unity Catalog tables (see
``scripts/uc/create_tables.sql``) and the pandas frames exchanged inside the app.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class TableSchema:
    name: str
    columns: dict[str, str]  # column -> logical type: str | float | int | bool | date
    key: tuple[str, ...]


TABLES: dict[str, TableSchema] = {
    "ref_articles": TableSchema("ref_articles", {
        "article_id": "str", "designation": "str", "unit": "str", "family": "str", "planner": "str",
        "coverage_target_days": "int", "alert_red_days": "int", "alert_yellow_days": "int",
        "overstock_days": "int", "safety_stock_qty": "float", "service_rate_tracked": "bool",
        "dhrq": "str", "active": "bool"}, ("article_id",)),
    "ref_suppliers": TableSchema("ref_suppliers", {
        "supplier_id": "str", "name": "str", "country": "str", "contact": "str",
        "delivery_weekdays": "str", "calendar_id": "str", "active": "bool"}, ("supplier_id",)),
    "ref_article_suppliers": TableSchema("ref_article_suppliers", {
        "article_id": "str", "supplier_id": "str", "moq": "float", "pack_qty": "float",
        "lead_time_days": "int", "quota_pct": "float", "priority": "int", "active": "bool"},
        ("article_id", "supplier_id")),
    "ref_programs": TableSchema("ref_programs", {
        "program_id": "str", "name": "str", "family": "str", "has_bom": "bool", "active": "bool"}, ("program_id",)),
    "ref_bom": TableSchema("ref_bom", {
        "program_id": "str", "article_id": "str", "qty_per": "float", "unit": "str", "scrap_pct": "float",
        "valid_from": "date", "valid_to": "date"}, ("program_id", "article_id")),
    "fct_production_plan": TableSchema("fct_production_plan", {
        "program_id": "str", "week_start": "date", "iso_week": "str", "qty": "float", "version": "str",
        "published_at": "date"}, ("program_id", "week_start", "version")),
    "fct_production_actual": TableSchema("fct_production_actual", {
        "program_id": "str", "date": "date", "qty": "float"}, ("program_id", "date")),
    "fct_purchase_orders": TableSchema("fct_purchase_orders", {
        "order_id": "str", "line_no": "int", "article_id": "str", "supplier_id": "str", "order_type": "str",
        "message_type": "str", "order_date": "date", "expected_date": "date", "qty_ordered": "float",
        "qty_received": "float", "status": "str", "unit": "str"}, ("order_id", "line_no")),
    "fct_receipts": TableSchema("fct_receipts", {
        "receipt_id": "str", "order_id": "str", "article_id": "str", "supplier_id": "str",
        "receipt_date": "date", "qty": "float", "unit": "str"}, ("receipt_id",)),
    "fct_stock_movements": TableSchema("fct_stock_movements", {
        "movement_id": "str", "article_id": "str", "date": "date", "movement_type": "str", "qty": "float",
        "comment": "str"}, ("movement_id",)),
    "fct_stock": TableSchema("fct_stock", {
        "article_id": "str", "snapshot_date": "date", "qty_on_hand": "float", "qty_blocked": "float",
        "unit": "str", "location": "str"}, ("article_id", "snapshot_date", "location")),
}


def _to_bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return False
    return str(v).strip().lower() in ("true", "1", "yes", "oui", "y", "t")


def coerce(df: pd.DataFrame, schema: TableSchema) -> pd.DataFrame:
    """Coerce a frame to the canonical schema (missing columns added, types normalised)."""
    out = pd.DataFrame(index=df.index)
    for col, typ in schema.columns.items():
        s = df[col] if col in df.columns else pd.Series([None] * len(df), index=df.index, dtype=object)
        if typ == "str":
            out[col] = s.map(lambda v: "" if v is None or (isinstance(v, float) and pd.isna(v)) else str(v).strip())
        elif typ == "float":
            out[col] = pd.to_numeric(s, errors="coerce").fillna(0.0).astype(float)
        elif typ == "int":
            out[col] = pd.to_numeric(s, errors="coerce").fillna(0).astype(int)
        elif typ == "bool":
            out[col] = s.map(_to_bool).astype(bool)
        elif typ == "date":
            parsed = pd.to_datetime(s, errors="coerce")
            out[col] = pd.Series([None if pd.isna(v) else v.date() for v in parsed], index=df.index, dtype=object)
        else:
            raise ValueError(typ)
    return out.reset_index(drop=True)


def parse_weekdays(spec: str | None) -> frozenset[int]:
    if not spec:
        return frozenset({1, 2, 3, 4, 5})
    return frozenset(int(x) for x in str(spec).split(",") if x.strip())


def as_date(v) -> dt.date | None:
    if v is None or v is pd.NaT or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    return dt.date.fromisoformat(str(v)[:10])
