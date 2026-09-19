#!/usr/bin/env python3
"""Extract the legacy Excel simulation workbook into the canonical seed datasets.

The legacy tool (``SIMULATION_<planner>.xlsx``) is a five-sheet workbook:

* ``BASE ARTICLE``   – article / supplier master data (one row per article-supplier link)
* ``BOM``            – one-level bill of material (program -> component, qty per unit)
* ``SOP - PDP``      – weekly production plan per program (``WEEKLY`` table)
* ``PREVU - ENGAGÉ`` – daily production: planned (``1. PREVU``), actual (``2. ENGAGÉ``)
* ``SIMULATION``     – per article: demand, orders, receipts, adjustments, stock, coverage

This script reads the *cached* values of the workbook (``data_only=True``) and writes the
CSV files consumed by :class:`appro.data.local_source.LocalErpSource` and by the Unity
Catalog bootstrap job (``scripts/uc/load_seed.py``).  The output schema is documented in
``docs/modele_donnees.md``.

Usage::

    python scripts/extract_seed_from_excel.py <workbook.xlsx> [--out data/seed] [--as-of 2026-09-18]

Assumptions made while converting the legacy semantics (see docs/analyse_excel.md):

* ``2. Commande`` cells with a quantity > 0 become purchase-order lines.  Cells equal to 0 are
  placeholders in the legacy file and are ignored.
* ``3. Réception`` cells with a quantity > 0 become receipts.  A receipt on the *same day* as an
  order closes that order (this is the legacy "receipt replaces order" rule).  A receipt of 0 on
  an order day means the order was moved/cancelled in the legacy file.
* Past orders (expected before the as-of date) without any receipt row are considered received
  in full, because the legacy stock formula counted them as consumed supply.  A past order with a
  smaller same-day receipt is ``CLOSED`` (closed short: the legacy file never expected the remainder).
* Future orders are ``OPEN``; they are typed ``FIRM`` within the firm horizon and ``FORECAST``
  beyond it (DELJIT / DELFOR semantics).
* Lead times, packaging quantities (PLA) and quotas do not exist in the workbook: demo values
  are derived (see ``SUPPLIER_LEAD_TIMES``) and must be replaced by the ERP values in Unity Catalog.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import unicodedata
from collections import defaultdict
from pathlib import Path

import openpyxl

FIRM_HORIZON_DAYS = 28

# Demo lead times in working days (not present in the legacy workbook).
SUPPLIER_LEAD_TIMES = {
    "S-000032": 5,   # R.BOURGEOIS S.A. (FR)
    "S-001033": 10,  # ERNESTO MALVESTITI (IT)
    "S-000378": 10,  # EUROTRANCIATURA (IT)
    "S-001562": 15,  # EUROMISI (IT)
    "S-000599": 10,  # ENNOVI Czech Republic
    "S-000517": 10,  # ENNOVI HUNGARY
    "S-000186": 15,  # SCHWERING HASSE (DE)
    "S-000286": 15,  # COVEME SPA (IT)
    "S-000106": 20,  # ISOVOLTA (AT)
    "S-000545": 15,  # ELANTAS EUROPE (IT)
    "S-000904": 20,  # ESSEX Furukawa (FR/…)
}
SUPPLIER_COUNTRIES = {
    "S-000032": "FR", "S-001033": "IT", "S-000378": "IT", "S-001562": "IT", "S-000599": "CZ",
    "S-000517": "HU", "S-000186": "DE", "S-000286": "IT", "S-000106": "AT", "S-000545": "IT",
    "S-000904": "FR",
}
# Supplier quotas for dual-sourced articles (share of the demand in %, demo assumption).
DUAL_SOURCE_QUOTAS = {
    ("P-00005775", "S-000032"): 100, ("P-00005775", "S-001033"): 0,
    ("P-00043859", "S-000032"): 100, ("P-00043859", "S-001033"): 0,
    ("P-00162871", "S-000378"): 50, ("P-00162871", "S-001562"): 50,
    ("P-00197812", "S-000378"): 50, ("P-00197812", "S-001562"): 50,
}


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text


def week_label_to_monday(label: str) -> dt.date:
    """Convert the legacy week labels (``S11-26`` or ``2028W24``) to the ISO week Monday."""
    m = re.fullmatch(r"S(\d{1,2})-(\d{2})", label)
    if m:
        week, year = int(m.group(1)), 2000 + int(m.group(2))
    else:
        m = re.fullmatch(r"(\d{4})W(\d{1,2})", label)
        if not m:
            raise ValueError(f"Unknown week label: {label!r}")
        year, week = int(m.group(1)), int(m.group(2))
    return dt.date.fromisocalendar(year, week, 1)


def iso_week_label(day: dt.date) -> str:
    y, w, _ = day.isocalendar()
    return f"{y}-W{w:02d}"


def write_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: ("" if row.get(k) is None else row.get(k)) for k in fieldnames})
    print(f"  wrote {path} ({len(rows)} rows)")


def fmt(x) -> str:
    if isinstance(x, float):
        return f"{x:.6f}".rstrip("0").rstrip(".")
    return str(x)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("workbook")
    ap.add_argument("--out", default="data/seed")
    ap.add_argument("--as-of", default="2026-09-18", help="Stock snapshot date (end of day)")
    args = ap.parse_args()
    out = Path(args.out)
    as_of = dt.date.fromisoformat(args.as_of)

    wb = openpyxl.load_workbook(args.workbook, data_only=True)

    # ------------------------------------------------------------------ BASE ARTICLE
    ws = wb["BASE ARTICLE"]
    header = [c.value for c in ws[1]]
    base_rows = [dict(zip(header, [c.value for c in row])) for row in ws.iter_rows(min_row=2) if row[0].value]

    # ------------------------------------------------------------------ BOM
    ws = wb["BOM"]
    bom_rows = []
    for row in ws.iter_rows(min_row=2, max_col=5, values_only=True):
        if not row[0]:
            continue
        pf_name, pf_id, ref, qty, unit = row
        bom_rows.append({"pf_name": pf_name.strip(), "pf_id": pf_id.strip(), "ref": ref.strip(),
                         "qty": float(qty), "unit": (unit or "").strip().upper()})

    # ------------------------------------------------------------------ WEEKLY plan
    ws = wb["SOP - PDP"]
    week_labels = [c.value for c in ws[1]][1:]
    weekly = []
    program_names: list[str] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row[0]:
            continue
        name = row[0].strip()
        program_names.append(name)
        for label, qty in zip(week_labels, row[1:]):
            if label is None:
                continue
            if qty is None or qty == "":
                continue
            weekly.append((name, label, float(qty)))

    # ------------------------------------------------------------------ DAILY actuals
    ws = wb["PREVU - ENGAGÉ"]
    dates = {}
    for c in range(7, ws.max_column + 1):
        v = ws.cell(4, c).value
        if isinstance(v, dt.datetime):
            dates[c] = v.date()
    actuals = []
    for r in range(4, ws.max_row + 1):
        kind = ws.cell(r, 5).value
        name = ws.cell(r, 6).value
        if kind != "2. ENGAGÉ" or not name:
            continue
        for c, day in dates.items():
            v = ws.cell(r, c).value
            if v is None or v == "":
                continue
            actuals.append({"program_name": name.strip(), "date": day, "qty": float(v)})

    # ------------------------------------------------------------------ SIMULATION entries
    ws = wb["SIMULATION"]
    sim_dates = {}
    for c in range(10, ws.max_column + 1):
        v = ws.cell(7, c).value
        if isinstance(v, dt.datetime):
            sim_dates[c] = v.date()
    orders_raw = defaultdict(dict)      # (ref, supplier) -> {date: qty}
    receipts_raw = defaultdict(dict)
    adjustments_raw = defaultdict(dict)  # ref -> {date: qty}
    stock_rows = {}                      # ref -> {date: stock}
    for r in range(9, ws.max_row + 1):
        ref = ws.cell(r, 3).value
        var = ws.cell(r, 9).value
        if not ref or not var:
            continue
        m = re.fullmatch(r"(\d)\. ([^()]+?)(?: \((S-\d+)\))?", var)
        if not m:
            continue
        kind, supplier = m.group(2).strip(), m.group(3)
        series = {sim_dates[c]: ws.cell(r, c).value for c in sim_dates if ws.cell(r, c).value not in (None, "")}
        if kind == "Commande":
            orders_raw[(ref, supplier)].update(series)
        elif kind == "Réception":
            receipts_raw[(ref, supplier)].update(series)
        elif kind == "Ajustement":
            adjustments_raw[ref].update(series)
        elif kind == "Stock":
            stock_rows[ref] = series

    # ------------------------------------------------------------------ build reference tables
    unit_by_ref = {}
    for b in bom_rows:
        unit_by_ref.setdefault(b["ref"], b["unit"])

    articles, suppliers, links = {}, {}, []
    for b in base_rows:
        ref = b["REF"].strip()
        cov = int(b["Couverture (jour)"] or 7)
        articles.setdefault(ref, {
            "article_id": ref,
            "designation": (b["NOM"] or "").strip(),
            "unit": unit_by_ref.get(ref, "PCE"),
            "family": "",
            "planner": (b["APPRO"] or "").strip().upper(),
            "coverage_target_days": cov,
            "alert_red_days": 3,
            "alert_yellow_days": cov,
            "overstock_days": max(30, 3 * cov),
            "safety_stock_qty": 0,
            "service_rate_tracked": "true" if str(b.get("Taux de service ?") or "").strip().upper() == "OUI" else "false",
            "dhrq": (b.get("DHRQ") or ""),
            "active": "true",
        })
        sid = b["COFOR"].strip()
        suppliers.setdefault(sid, {
            "supplier_id": sid,
            "name": (b["SUPPLIER"] or "").strip(),
            "country": SUPPLIER_COUNTRIES.get(sid, ""),
            "contact": (b.get("Contact") or ""),
            "delivery_weekdays": "1,2,3,4,5",
            "calendar_id": "DEFAULT",
            "active": "true",
        })
        moq = float(b["MOQ"] or 0)
        n_sources = sum(1 for x in base_rows if x["REF"].strip() == ref)
        quota = DUAL_SOURCE_QUOTAS.get((ref, sid), 100 if n_sources == 1 else 0)
        links.append({
            "article_id": ref,
            "supplier_id": sid,
            "moq": fmt(moq),
            "pack_qty": fmt(moq),
            "lead_time_days": SUPPLIER_LEAD_TIMES.get(sid, 10),
            "quota_pct": quota,
            "priority": 1 if quota >= 50 else 2,
            "active": "true",
        })

    # Programs: ids from the BOM when available, otherwise a stable slug.
    program_id_by_name = {}
    for b in bom_rows:
        program_id_by_name.setdefault(b["pf_name"], b["pf_id"])
    programs = []
    for name in program_names:
        pid = program_id_by_name.get(name) or f"prog-{slugify(name)}"
        program_id_by_name[name] = pid
        fam = re.search(r"(M2 BEV|M2 ERAD|M2 PHEV|M2 SCHMID|M3/M4|M2|M3|M4)", name)
        programs.append({"program_id": pid, "name": name, "family": fam.group(1) if fam else "",
                         "has_bom": "true" if any(b["pf_name"] == name for b in bom_rows) else "false",
                         "active": "true"})

    bom = [{"program_id": b["pf_id"], "article_id": b["ref"], "qty_per": fmt(b["qty"]), "unit": b["unit"],
            "scrap_pct": 0, "valid_from": "", "valid_to": ""} for b in bom_rows]

    plan = []
    for name, label, qty in weekly:
        monday = week_label_to_monday(label)
        plan.append({"program_id": program_id_by_name[name], "week_start": monday.isoformat(),
                     "iso_week": iso_week_label(monday), "qty": fmt(qty), "version": "PDP-LEGACY",
                     "published_at": as_of.isoformat()})

    prod_actual = [{"program_id": program_id_by_name[a["program_name"]], "date": a["date"].isoformat(),
                    "qty": fmt(a["qty"])} for a in actuals if a["program_name"] in program_id_by_name]

    # ------------------------------------------------------------------ orders / receipts
    orders, receipts = [], []
    po_seq = 0
    rc_seq = 0
    primary_supplier = {}
    for l in links:
        if l["priority"] == 1:
            primary_supplier.setdefault(l["article_id"], l["supplier_id"])
    keys = sorted(set(orders_raw) | set(receipts_raw))
    for (ref, supplier) in keys:
        sid = supplier or primary_supplier.get(ref)
        o_series = orders_raw.get((ref, supplier), {})
        r_series = receipts_raw.get((ref, supplier), {})
        for day in sorted(set(o_series) | set(r_series)):
            oq = o_series.get(day)
            rq = r_series.get(day)
            order_id = None
            if oq is not None and float(oq) > 0:
                po_seq += 1
                order_id = f"PO-{po_seq:06d}"
                oq = float(oq)
                if day <= as_of:
                    if rq is None:
                        status, received = "RECEIVED", oq
                    elif float(rq) <= 0:
                        status, received = "CANCELLED", 0.0
                    else:
                        received = float(rq)
                        # legacy rule: the receipt replaces the order, the remainder is never delivered
                        status = "RECEIVED" if received >= oq else "CLOSED"
                    otype = "FIRM"
                else:
                    status, received = "OPEN", 0.0
                    otype = "FIRM" if (day - as_of).days <= FIRM_HORIZON_DAYS else "FORECAST"
                orders.append({
                    "order_id": order_id, "line_no": 1, "article_id": ref, "supplier_id": sid,
                    "order_type": otype, "message_type": "DELJIT" if otype == "FIRM" else "DELFOR",
                    "order_date": (day - dt.timedelta(days=SUPPLIER_LEAD_TIMES.get(sid, 10))).isoformat(),
                    "expected_date": day.isoformat(), "qty_ordered": fmt(oq), "qty_received": fmt(received),
                    "status": status, "unit": articles[ref]["unit"],
                })
            if rq is not None and float(rq) > 0:
                rc_seq += 1
                receipts.append({
                    "receipt_id": f"RC-{rc_seq:06d}", "order_id": order_id or "", "article_id": ref,
                    "supplier_id": sid, "receipt_date": day.isoformat(), "qty": fmt(float(rq)),
                    "unit": articles[ref]["unit"],
                })

    movements = []
    mv_seq = 0
    for ref, series in adjustments_raw.items():
        for day, qty in sorted(series.items()):
            if qty in (None, ""):
                continue
            mv_seq += 1
            movements.append({"movement_id": f"MV-{mv_seq:06d}", "article_id": ref, "date": day.isoformat(),
                              "movement_type": "INVENTORY_ADJUSTMENT", "qty": fmt(float(qty)),
                              "comment": "Ajustement hebdomadaire (fichier legacy)"})

    stock = []
    for ref, series in stock_rows.items():
        # Excel cached projected stock at the as-of date = best available on-hand estimate.
        candidates = [d for d in series if d <= as_of and isinstance(series[d], (int, float))]
        day = max(candidates)
        stock.append({"article_id": ref, "snapshot_date": as_of.isoformat(), "qty_on_hand": fmt(round(float(series[day]), 3)),
                      "qty_blocked": 0, "unit": articles[ref]["unit"], "location": "MAIN"})

    print(f"Writing seed to {out}")
    write_csv(out / "ref_articles.csv", list(articles.values()), list(next(iter(articles.values())).keys()))
    write_csv(out / "ref_suppliers.csv", list(suppliers.values()), list(next(iter(suppliers.values())).keys()))
    write_csv(out / "ref_article_suppliers.csv", links, list(links[0].keys()))
    write_csv(out / "ref_programs.csv", programs, list(programs[0].keys()))
    write_csv(out / "ref_bom.csv", bom, list(bom[0].keys()))
    write_csv(out / "fct_production_plan.csv", plan, list(plan[0].keys()))
    write_csv(out / "fct_production_actual.csv", prod_actual, list(prod_actual[0].keys()))
    write_csv(out / "fct_purchase_orders.csv", orders, list(orders[0].keys()))
    write_csv(out / "fct_receipts.csv", receipts, list(receipts[0].keys()))
    write_csv(out / "fct_stock_movements.csv", movements, list(movements[0].keys()))
    write_csv(out / "fct_stock.csv", stock, list(stock[0].keys()))


if __name__ == "__main__":
    main()
