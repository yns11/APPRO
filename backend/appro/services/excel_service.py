"""Excel export / import (openpyxl).

Exports
-------
* ``simulation_workbook`` – a **live** simulation workbook: the ``SIMULATION`` grid is made of
  formulas fed by editable sheets, so the planner can edit the delivery plan, type receipts or
  adjustments and see the stocks recompute in Excel:

  ==================  =====================================================================
  sheet               role
  ==================  =====================================================================
  PARAMETRES          rules used by the formulas (shortage policy, target policy, tie rule)
  ARTICLES            per-article inputs: initial stocks, coverage target, safety stock, thresholds
  SIMULATION          one block of 15 rows per article; consumption / requirement, receipts,
                      CBN and adjustments are values, everything else is a formula
  PLAN                the delivery plan: one row per ERP order (ERP date / quantity in regard)
                      and per plan line ; editable *Date plan* / *Quantité plan* columns
  SAISIES             free entries (orders, receipts, adjustments, actual production)
  ALERTES             snapshot of the alerts at export time (values)
  ==================  =====================================================================

  Stock recurrence per period (same as the engine, one column per day or ISO week)::

      ERP  : x = stock[p-1] + R + Ferme + saisies + A - besoin
      Plan : x = stock[p-1] + R + Plan + CBN + saisies + A - besoin
      stock[p]    = IF(shortage policy = "lost", MAX(0, x), x)
      shortage[p] = MAX(0, -x)
      target[p]   = demand of the next N periods (and/or safety stock)
      coverage[p] = number of future periods whose cumulated demand <= stock[p]

* ``alerts_workbook`` / ``orders_workbook`` – single-sheet extracts.
* ``pdp_template_workbook`` – the template of the weekly production plan to import.

Imports
-------
* ``parse_pdp_workbook`` – weekly production plan.  Accepts the legacy ``SOP - PDP`` layout
  (program names in column A, week labels ``S11-26`` / ``2028W24`` / ``2026-W11`` / dates in row 1)
  or a long layout (``program_id, week_start, qty``).
* ``parse_entries_workbook`` – the ``SAISIES`` sheet (orders, receipts, adjustments, actuals) and
  the ``PLAN`` sheet (plan lines of the articles present in the workbook).
"""
from __future__ import annotations

import datetime as dt
import io
import re
from dataclasses import dataclass
from typing import Any, Iterable

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import CellIsRule, FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ..engine.calendar import iso_week_label, iso_week_monday
from ..engine.models import ArticleResult, MrpResult

HEADER_FILL = PatternFill("solid", fgColor="1F2A44")
HEADER_FONT = Font(bold=True, color="FFFFFF")
LABEL_FILL = PatternFill("solid", fgColor="EEF1F6")
INPUT_FILL = PatternFill("solid", fgColor="FFFBEA")
HELPER_FONT = Font(color="98A2B3", italic=True)
RED_FILL = PatternFill("solid", fgColor="F8D7DA")
YELLOW_FILL = PatternFill("solid", fgColor="FFF3CD")
GREEN_FILL = PatternFill("solid", fgColor="D1E7DD")
BLUE_FONT = Font(color="255D95", bold=True)    # Excel convention: blue = input, black = formula
BOLD = Font(bold=True)
THIN = Side(style="thin", color="D0D5DD")
DATE_FMT = "yyyy-mm-dd"
QTY_FMT = "#,##0.###"

# Fixed cells of the PARAMETRES sheet referenced by the formulas
P_SHORTAGE = "PARAMETRES!$B$5"
P_TARGET = "PARAMETRES!$B$6"
P_TIE = "PARAMETRES!$B$7"

SPARE_ROWS = 300          # blank rows kept in the SUMIFS ranges for planner additions
ENTRY_ROWS = 2000         # rows scanned in SAISIES
ENTRY_FORMAT_ROWS = 200   # rows of SAISIES pre-formatted as dates (Excel recognises typed dates anyway)

# Row layout of one article block in SIMULATION (offset → key, label, kind)
BLOCK = [
    ("consumed", "Consommé", "input"),
    ("required", "Requis", "input"),
    ("orders_firm", "Ferme (PLAN, date ERP)", "formula"),
    ("orders_forecast", "Prévisionnel (PLAN)", "formula"),
    ("receipts", "Reçu", "input"),
    ("plan", "Plan (PLAN, date plan)", "formula"),
    ("cbn", "Complément CBN", "input"),
    ("adjustments", "Ajustement", "input"),
    ("entries", "Saisies (SAISIES : commandes, réceptions, ajustements)", "formula"),
    ("stock_erp", "Scenario ERP", "stock"),
    ("stock_plan", "Scenario Plan", "stock"),
    ("shortage_plan", "Manque (plan, besoin non servi)", "formula"),
    ("target", "Stock cible", "formula"),
    ("coverage", "Couverture plan (périodes)", "formula"),
    ("cum_demand", "Besoin cumulé (aide au calcul)", "helper"),
]
BLOCK_ROWS = len(BLOCK)
ROW = {key: i for i, (key, _, _) in enumerate(BLOCK)}
SIM_HEADER_ROWS = 3       # label / period start / period end
SIM_FIRST_COL = 5         # E


def _style_header(ws, row: int, ncols: int) -> None:
    for c in range(1, ncols + 1):
        cell = ws.cell(row, c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def _widths(ws, widths: Iterable[float]) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def periods(dates: list[dt.date], granularity: str, start: dt.date) -> list[tuple[str, list[int]]]:
    """Group the day indexes of ``dates`` (from ``start``) into labelled periods."""
    out: dict[str, list[int]] = {}
    for i, d in enumerate(dates):
        if d < start:
            continue
        label = d.isoformat() if granularity == "day" else iso_week_label(d)
        out.setdefault(label, []).append(i)
    return list(out.items())


def _date_cell(ws, row: int, col: int, value: dt.date | None):
    cell = ws.cell(row, col, value)
    cell.number_format = DATE_FMT
    return cell


# =============================================================================
# Simulation workbook
# =============================================================================
def simulation_workbook(result: MrpResult, article_ids: Iterable[str] | None = None, granularity: str = "day",
                        start: dt.date | None = None, meta: dict[str, Any] | None = None) -> bytes:
    ids = [a for a in (list(article_ids) if article_ids else list(result.articles)) if a in result.articles]
    start = start or result.as_of
    wb = Workbook()
    _parameters_sheet(wb.active, result, granularity, meta)
    ws_art = wb.create_sheet("ARTICLES")
    ws_sim = wb.create_sheet("SIMULATION")
    ws_plan = wb.create_sheet("PLAN")
    ws_entries = wb.create_sheet("SAISIES")
    ws_alerts = wb.create_sheet("ALERTES")

    article_rows = _articles_sheet(ws_art, result, ids, start)
    n_plan = _plan_sheet(ws_plan, result, ids)
    _entries_template(ws_entries)
    _alerts_sheet(ws_alerts, result, ids)
    _simulation_grid(ws_sim, result, ids, granularity, start, article_rows, plan_last_row=n_plan + SPARE_ROWS)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _parameters_sheet(ws, result: MrpResult, granularity: str, meta: dict[str, Any] | None) -> None:
    ws.title = "PARAMETRES"
    p = result.params
    rows: list[tuple[str, Any, str]] = [
        ("Généré le", dt.datetime.now().strftime("%Y-%m-%d %H:%M"), ""),
        ("Date de référence", result.as_of.isoformat(), "stock connu la veille au soir"),
        ("Horizon (jours)", p.horizon_days, ""),
        ("Granularité", granularity, "une colonne par jour ou par semaine ISO"),
        ("Politique de manque", p.shortage_policy, "backlog : besoin non servi reporté (stock net négatif) ; lost : stock borné à 0, besoin perdu"),
        ("Politique de cible", p.target_policy, "max : max(couverture, sécurité) ; coverage_days ; safety_qty"),
        ("Règle d'égalité couverture", p.coverage_tie_rule, "covered : un besoin cumulé égal au stock est couvert ; not_covered (classeur historique)"),
        ("Unité de couverture", p.coverage_unit, "le classeur compte des périodes (colonnes) ; l'application peut compter en jours ouvrés"),
    ]
    for k, v in (meta or {}).items():
        rows.append((k, str(v), ""))
    for r, (k, v, note) in enumerate(rows, start=1):
        ws.cell(r, 1, k).font = BOLD
        cell = ws.cell(r, 2, v)
        if 5 <= r <= 7:
            cell.fill = INPUT_FILL
            cell.font = BLUE_FONT
        ws.cell(r, 3, note).font = HELPER_FONT
    r = len(rows) + 2
    ws.cell(r, 1, "Mode d'emploi").font = BOLD
    notes = [
        "Cellules bleues sur fond jaune = saisies ; cellules noires = formules (ne pas écraser).",
        "SIMULATION : Consommé (passé, réel), Requis (futur, PDP), Reçu, Complément CBN et Ajustement sont des valeurs modifiables ; les autres lignes se recalculent.",
        "PLAN : une ligne par commande ERP (Date ERP / Qté ERP en regard, non modifiables) et par ligne du plan ; modifier Date plan / Qté plan = décaler, réduire (0 = rien attendu) ; ajouter une ligne avec la même référence = tranche ; ligne de type LIBRE = livraison hors commande.",
        "Une commande ERP passée non reçue est listée dans PLAN (Retard) mais ne compte dans aucun stock tant que Date plan est vide ; lui donner une Date plan la fait entrer dans le Scenario Plan.",
        "SAISIES : nouvelles commandes fermes (dans les deux scenarios), réceptions, ajustements (quantité signée) ou production réelle ; date au format date.",
        "Réimport dans l'application (Imports / exports) : SAISIES et PLAN ; le plan des articles présents dans le classeur est remplacé par celui du classeur.",
        "Stock net = stock physique − manque (backlog) ; un stock physique n'est jamais négatif.",
    ]
    for i, t in enumerate(notes, start=r + 1):
        ws.cell(i, 1, f"{i - r}.").font = HELPER_FONT
        ws.cell(i, 2, t)
    _widths(ws, (28, 44, 90))


def _layer_start(ar: ArticleResult, key: str, i_start: int) -> float:
    """Net balance of a layer on the day before the grid start (= what the formulas start from)."""
    series = getattr(ar, key)
    return float(series[i_start - 1]) if i_start > 0 else float(ar.kpis["stock_reference"])


def _articles_sheet(ws, result: MrpResult, ids: list[str], start: dt.date) -> dict[str, int]:
    headers = ["Article", "Désignation", "Unité", "Fournisseur(s)", "Stock initial ERP", "Stock initial plan",
               "Correction de référence", "Stock au", "Couverture cible (j)", "Stock de sécurité", "Seuil rouge (j)",
               "Seuil orange (j)", "Surstock (j)", "MOQ", "PLA", "Délai (j ouvrés)", "Approvisionneur"]
    for c, h in enumerate(headers, start=1):
        ws.cell(1, c, h)
    _style_header(ws, 1, len(headers))
    rows: dict[str, int] = {}
    for r, aid in enumerate(ids, start=2):
        ar = result.articles[aid]
        a = ar.article
        i_start = next((i for i, d in enumerate(ar.dates) if d >= start), len(ar.dates))
        link = min(ar.suppliers, key=lambda l: (l.priority, l.supplier_id)) if ar.suppliers else None
        values = [aid, a.designation, a.unit, " / ".join(sorted({l.supplier_id for l in ar.suppliers})),
                  _layer_start(ar, "stock_erp_net", i_start), _layer_start(ar, "stock_plan_net", i_start),
                  ar.reference_correction, ar.dates[i_start - 1] if i_start > 0 else None,
                  a.coverage_target_days, a.safety_stock_qty, a.alert_red_days, a.alert_yellow_days, a.overstock_days,
                  link.moq if link else 0, link.pack_qty if link else 0, link.lead_time_days if link else 0, a.planner]
        for c, v in enumerate(values, start=1):
            cell = ws.cell(r, c, v)
            if c == 8:
                cell.number_format = DATE_FMT
            elif 5 <= c <= 16:
                cell.number_format = QTY_FMT
                cell.font = BLUE_FONT
                cell.fill = INPUT_FILL
        rows[aid] = r
    ws.freeze_panes = "C2"
    _widths(ws, (13, 28, 7, 18, 14, 14, 14, 11, 11, 11, 10, 10, 10, 9, 9, 10, 14))
    return rows


def _sumifs(sum_rng: str, *criteria: tuple[str, str]) -> str:
    return "SUMIFS(" + sum_rng + "".join(f",{rng},{crit}" for rng, crit in criteria) + ")"


def _simulation_grid(ws, result: MrpResult, ids: list[str], granularity: str, start: dt.date,
                     article_rows: dict[str, int], plan_last_row: int) -> None:
    if not ids:
        ws.cell(1, 1, "Aucun article")
        return
    first = result.articles[ids[0]]
    pers = periods(first.dates, granularity, start)
    if not pers:
        ws.cell(1, 1, "Aucune période après la date de début")
        return
    ncols = SIM_FIRST_COL - 1 + len(pers)
    last_col = get_column_letter(ncols)
    fixed = ["Article", "Désignation", "Variable", "Réf."]
    for c, h in enumerate(fixed, start=1):
        ws.cell(1, c, h)
    ws.cell(2, 1, "Début de période").font = HELPER_FONT
    ws.cell(3, 1, "Fin de période").font = HELPER_FONT
    for c, (label, idxs) in enumerate(pers, start=SIM_FIRST_COL):
        ws.cell(1, c, label)
        _date_cell(ws, 2, c, first.dates[idxs[0]]).font = HELPER_FONT
        _date_cell(ws, 3, c, first.dates[idxs[-1]]).font = HELPER_FONT
    _style_header(ws, 1, ncols)

    # ranges of the feeding sheets (bounded for performance, with spare rows for additions)
    o = f"PLAN!${{}}$2:${{}}${plan_last_row}"
    o_art, o_type, o_date, o_qty = o.format("A", "A"), o.format("C", "C"), o.format("F", "F"), o.format("G", "G")
    o_pdate, o_pqty = o.format("H", "H"), o.format("I", "I")
    e = f"SAISIES!${{}}$2:${{}}${ENTRY_ROWS}"
    e_type, e_art, e_date, e_qty = e.format("A", "A"), e.format("B", "B"), e.format("D", "D"), e.format("E", "E")
    week = granularity == "week"

    r = SIM_HEADER_ROWS + 1
    for aid in ids:
        ar = result.articles[aid]
        a = ar.article
        k = article_rows[aid]
        art = "ARTICLES!$"
        top = r
        rows = {key: top + off for key, off in ROW.items()}
        for off, (key, label, kind) in enumerate(BLOCK):
            rr = top + off
            ws.cell(rr, 1, aid)
            ws.cell(rr, 2, a.designation)
            lab = ws.cell(rr, 3, label)
            lab.fill = LABEL_FILL
            if kind == "helper":
                lab.font = HELPER_FONT
            elif kind == "stock":
                lab.font = BOLD
        # reference column: initial stocks and target parameters
        ws.cell(rows["stock_erp"], 4, f"={art}E${k}").number_format = QTY_FMT
        ws.cell(rows["stock_plan"], 4, f"={art}F${k}").number_format = QTY_FMT
        ws.cell(rows["target"], 4, f"={art}I${k}").number_format = "0"
        ws.cell(rows["coverage"], 4, "périodes").font = HELPER_FONT

        for c, (_, idxs) in enumerate(pers, start=SIM_FIRST_COL):
            col = get_column_letter(c)
            prev = get_column_letter(c - 1)
            first_period = c == SIM_FIRST_COL
            crit_erp = ((o_date, f'">="&{col}$2'), (o_date, f'"<="&{col}$3'))
            crit_plan = ((o_pdate, f'">="&{col}$2'), (o_pdate, f'"<="&{col}$3'))

            def cell(key: str, value):
                x = ws.cell(rows[key], c, value)
                x.number_format = QTY_FMT
                return x

            for key, series in (("consumed", ar.consumed), ("required", ar.required), ("receipts", ar.receipts),
                                ("adjustments", ar.adjustments), ("cbn", ar.supply_proposed)):
                v = float(sum(series[i] for i in idxs))
                cell(key, round(v, 3) if v else 0).font = BLUE_FONT
                ws.cell(rows[key], c).fill = INPUT_FILL
            a_ref = f"$A{rows['orders_firm']}"
            e_crit = ((e_art, a_ref), (e_date, f'">="&{col}$2'), (e_date, f'"<="&{col}$3'))
            cmd = _sumifs(e_qty, (e_type, '"COMMANDE"'), *e_crit)
            cell("orders_firm", "=" + _sumifs(o_qty, (o_art, a_ref), (o_type, '"FIRM"'), *crit_erp) + "+" + cmd)
            cell("orders_forecast", "=" + _sumifs(o_qty, (o_art, a_ref), (o_type, '"FORECAST"'), *crit_erp))
            cell("plan", "=" + _sumifs(o_pqty, (o_art, a_ref), *crit_plan) + "+" + cmd)
            cell("entries", "=" + _sumifs(e_qty, (e_type, '"RECEPTION"'), *e_crit) + "+" + _sumifs(e_qty, (e_type, '"AJUSTEMENT"'), *e_crit))
            demand_ref = f"({col}{rows['consumed']}+{col}{rows['required']})"
            common = f"{col}{rows['receipts']}+{col}{rows['entries']}+{col}{rows['adjustments']}-{demand_ref}"
            erp_in = f"{col}{rows['orders_firm']}+{common}"
            plan_in = f"{col}{rows['plan']}+{col}{rows['cbn']}+{common}"
            for key, inflow in (("stock_erp", erp_in), ("stock_plan", plan_in)):
                prev_ref = f"$D{rows[key]}" if first_period else f"{prev}{rows[key]}"
                x = f"{prev_ref}+{inflow}"
                cell(key, f'=IF({P_SHORTAGE}="lost",MAX(0,{x}),{x})').font = BOLD
            prev_ref = f"$D{rows['stock_plan']}" if first_period else f"{prev}{rows['stock_plan']}"
            cell("shortage_plan", f"=MAX(0,-({prev_ref}+{plan_in}))")
            # target: demand of the next N periods and/or safety stock
            n_per = f"{art}I${k}" if not week else f"ROUNDUP({art}I${k}/7,0)"
            cov = (f"IF({n_per}>0,SUM(OFFSET({col}{rows['required']},0,1,1,{n_per}))"
                   f"+SUM(OFFSET({col}{rows['consumed']},0,1,1,{n_per})),0)")
            cell("target", f'=IF({P_TARGET}="safety_qty",{art}J${k},IF({P_TARGET}="coverage_days",{cov},MAX({cov},{art}J${k})))')
            # coverage: future periods whose cumulated demand is covered by the plan stock
            if first_period:
                cell("cum_demand", f"={demand_ref}").font = HELPER_FONT
            else:
                cell("cum_demand", f"={prev}{rows['cum_demand']}+{demand_ref}").font = HELPER_FONT
            if c < ncols:
                nxt = get_column_letter(c + 1)
                crit = f'IF({P_TIE}="covered","<=","<")&({col}{rows["cum_demand"]}+{col}{rows["stock_plan"]})'
                f = f'=IF({col}{rows["stock_plan"]}<0,0,COUNTIF({nxt}{rows["cum_demand"]}:{last_col}{rows["cum_demand"]},{crit}))'
            else:
                f = 0
            ws.cell(rows["coverage"], c, f).number_format = "0"

        # conditional formats
        fc, lc = get_column_letter(SIM_FIRST_COL), last_col
        stock_rng = f"{fc}{rows['stock_erp']}:{lc}{rows['stock_plan']}"
        ws.conditional_formatting.add(stock_rng, CellIsRule(operator="lessThan", formula=["0"], fill=RED_FILL))
        ws.conditional_formatting.add(stock_rng, FormulaRule(formula=[f"{fc}{rows['stock_erp']}<{fc}${rows['target']}"], fill=YELLOW_FILL))
        ws.conditional_formatting.add(f"{fc}{rows['shortage_plan']}:{lc}{rows['shortage_plan']}",
                                      CellIsRule(operator="greaterThan", formula=["0"], fill=RED_FILL))
        cov_rng = f"{fc}{rows['coverage']}:{lc}{rows['coverage']}"
        ws.conditional_formatting.add(cov_rng, CellIsRule(operator="lessThanOrEqual", formula=[f"{art}K${k}"], fill=RED_FILL))
        ws.conditional_formatting.add(cov_rng, CellIsRule(operator="lessThanOrEqual", formula=[f"{art}L${k}"], fill=YELLOW_FILL))
        ws.conditional_formatting.add(cov_rng, CellIsRule(operator="greaterThanOrEqual", formula=[f"{art}M${k}"], fill=GREEN_FILL))
        for c in range(1, ncols + 1):
            ws.cell(top + BLOCK_ROWS - 1, c).border = Border(bottom=THIN)
        r = top + BLOCK_ROWS

    ws.freeze_panes = f"{get_column_letter(SIM_FIRST_COL)}{SIM_HEADER_ROWS + 1}"
    _widths(ws, (13, 26, 44, 11))
    for c in range(SIM_FIRST_COL, ncols + 1):
        ws.column_dimensions[get_column_letter(c)].width = 11


def _alerts_sheet(ws, result: MrpResult, ids: list[str]) -> None:
    headers = ["Article", "Désignation", "Type", "Sévérité", "Périmètre", "Date", "Valeur", "Message"]
    for c, h in enumerate(headers, start=1):
        ws.cell(1, c, h)
    _style_header(ws, 1, len(headers))
    r = 2
    for aid in ids:
        ar = result.articles.get(aid)
        if not ar:
            continue
        for a in ar.alerts:
            ws.append([aid, ar.article.designation, a.alert_type.value, a.severity.value, a.scope,
                       a.date.isoformat() if a.date else "", a.value, a.message])
            fill = RED_FILL if a.severity.value == "critical" else YELLOW_FILL if a.severity.value == "warning" else None
            if fill:
                ws.cell(r, 4).fill = fill
            r += 1
    _widths(ws, (13, 28, 18, 10, 10, 12, 12, 90))
    ws.freeze_panes = "A2"


PLAN_HEADERS = ["Article", "Désignation", "Type", "Référence", "Fournisseur", "Date ERP", "Qté ERP (restante)",
                "Date plan (saisie)", "Qté plan (saisie)", "Origine", "Retard (j)", "Commentaire"]
ORIGIN_LABELS = {"erp": "ERP", "override": "modifiée", "free": "LIBRE", "expired": "expirée", "cbn": "CBN"}


def _plan_sheet(ws, result: MrpResult, ids: list[str]) -> int:
    """Delivery plan: one row per ERP order (ERP date / quantity in regard) and per plan line ;
    columns H–I are the planner's inputs (date / quantity of the plan).  Return the row count."""
    for c, h in enumerate(PLAN_HEADERS, start=1):
        ws.cell(1, c, h)
    _style_header(ws, 1, len(PLAN_HEADERS))
    r = 2
    for aid in ids:
        ar = result.articles.get(aid)
        if not ar:
            continue
        seen_orders: set[str] = set()
        rows: list[list] = []
        for l in ar.plan_lines:
            typ = "LIBRE" if l.order_id is None else next((o.order_type for o in ar.orders if o.order_id == l.order_id), "FIRM")
            first = l.order_id is not None and l.order_id not in seen_orders
            if l.order_id:
                seen_orders.add(l.order_id)
            rows.append([aid, ar.article.designation, typ, l.order_id, l.supplier_id, l.erp_date if first else None,
                         l.erp_qty if first else None, l.date, l.qty, ORIGIN_LABELS.get(l.origin, l.origin), None, l.note])
        # not-received orders and forecast orders: ERP columns only
        for o in ar.orders:
            if o.order_id in seen_orders:
                continue
            rows.append([aid, ar.article.designation, o.order_type, o.order_id, o.supplier_id, o.expected_date,
                         o.qty_open, None, None, "ERP", o.days_late or None,
                         "non reçue : donner une date plan si elle arrive encore" if o.status == "not_received" else o.note])
        for values in sorted(rows, key=lambda v: (v[7] or v[5] or dt.date.max, str(v[3]))):
            ws.append(values)
            for c in (6, 8):
                ws.cell(r, c).number_format = DATE_FMT
            for c in (7, 9):
                ws.cell(r, c).number_format = QTY_FMT
            for c in (8, 9, 12):
                ws.cell(r, c).fill = INPUT_FILL
                ws.cell(r, c).font = BLUE_FONT
            if values[10]:
                ws.cell(r, 11).fill = RED_FILL
            r += 1
    for rr in range(r, r + SPARE_ROWS):
        ws.cell(rr, 8).number_format = DATE_FMT
    _widths(ws, (13, 26, 10, 34, 12, 12, 13, 13, 13, 10, 9, 40))
    ws.freeze_panes = "E2"
    return r - 2


ENTRY_HEADERS = ["Type (COMMANDE/RECEPTION/AJUSTEMENT/PRODUCTION)", "Article ou Programme", "Fournisseur", "Date",
                 "Quantité", "Commentaire", "Référence commande (réception)"]


def _entries_template(ws) -> None:
    for c, h in enumerate(ENTRY_HEADERS, start=1):
        ws.cell(1, c, h)
    _style_header(ws, 1, len(ENTRY_HEADERS))
    example = ["EXEMPLE", "P-00001046", "S-000545", dt.date(2026, 10, 15), 1600,
               "exemple (type EXEMPLE = ligne ignorée) : remplacer par COMMANDE / RECEPTION / AJUSTEMENT / PRODUCTION", ""]
    for c, v in enumerate(example, start=1):
        ws.cell(2, c, v).font = Font(italic=True, color="888888")
    for rr in range(3, ENTRY_FORMAT_ROWS + 1):
        ws.cell(rr, 4).number_format = DATE_FMT
    _widths(ws, (44, 22, 14, 14, 12, 50, 26))


def alerts_workbook(result: MrpResult, ids: list[str] | None = None) -> bytes:
    wb = Workbook()
    _alerts_sheet(wb.active, result, ids or list(result.articles))
    wb.active.title = "ALERTES"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def orders_workbook(result: MrpResult, ids: list[str] | None = None) -> bytes:
    wb = Workbook()
    _plan_sheet(wb.active, result, ids or list(result.articles))
    wb.active.title = "PLAN"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# =============================================================================
# Imports
# =============================================================================
@dataclass
class ParsedPdpLine:
    program_id: str
    week_start: dt.date
    qty: float


def parse_week_label(value: Any) -> dt.date | None:
    """``S11-26`` / ``2028W24`` / ``2026-W11`` / ``W11-2026`` / a date → Monday of the ISO week."""
    if value is None or value == "":
        return None
    if isinstance(value, dt.datetime):
        return iso_week_monday(value.date())
    if isinstance(value, dt.date):
        return iso_week_monday(value)
    s = str(value).strip().upper()
    m = re.fullmatch(r"S(\d{1,2})-(\d{2,4})", s)
    if m:
        w, y = int(m.group(1)), int(m.group(2))
        return dt.date.fromisocalendar(y if y > 100 else 2000 + y, w, 1)
    m = re.fullmatch(r"(\d{4})-?W(\d{1,2})", s)
    if m:
        return dt.date.fromisocalendar(int(m.group(1)), int(m.group(2)), 1)
    m = re.fullmatch(r"W(\d{1,2})-(\d{4})", s)
    if m:
        return dt.date.fromisocalendar(int(m.group(2)), int(m.group(1)), 1)
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return iso_week_monday(dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
    return None


def parse_pdp_workbook(content: bytes, program_names: dict[str, str], sheet: str | None = None
                       ) -> tuple[list[ParsedPdpLine], list[str]]:
    """Parse a PDP workbook. ``program_names`` maps program name **and** id (upper-cased) → program_id."""
    wb = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    ws = wb[sheet] if sheet and sheet in wb.sheetnames else (
        wb["SOP - PDP"] if "SOP - PDP" in wb.sheetnames else wb[wb.sheetnames[0]])
    rows = list(ws.iter_rows(values_only=True))
    notes: list[str] = []
    lines: list[ParsedPdpLine] = []
    if not rows:
        return lines, ["classeur vide"]
    header = [str(h).strip().lower() if h is not None else "" for h in rows[0]]
    # long layout
    if {"program_id", "qty"} <= set(header) and ("week_start" in header or "iso_week" in header):
        ip, iq = header.index("program_id"), header.index("qty")
        iw = header.index("week_start") if "week_start" in header else header.index("iso_week")
        for r in rows[1:]:
            if not r or r[ip] is None:
                continue
            pid = program_names.get(str(r[ip]).strip().upper())
            monday = parse_week_label(r[iw])
            if not pid or not monday:
                notes.append(f"ligne ignorée : {r[ip]!r} / {r[iw]!r}")
                continue
            lines.append(ParsedPdpLine(pid, monday, float(r[iq] or 0)))
        return lines, notes
    # wide (legacy) layout
    week_cols = {}
    for c, h in enumerate(rows[0]):
        if c == 0:
            continue
        monday = parse_week_label(h)
        if monday:
            week_cols[c] = monday
        elif h not in (None, ""):
            notes.append(f"colonne {c + 1} ignorée : {h!r} n'est pas une semaine")
    if not week_cols:
        return lines, ["aucune colonne semaine reconnue (attendu : S11-26, 2026-W11, 2028W24 ou une date)"]
    for r in rows[1:]:
        if not r or r[0] in (None, ""):
            continue
        pid = program_names.get(str(r[0]).strip().upper())
        if not pid:
            notes.append(f"programme inconnu ignoré : {r[0]!r}")
            continue
        for c, monday in week_cols.items():
            v = r[c] if c < len(r) else None
            if v in (None, ""):
                continue
            try:
                lines.append(ParsedPdpLine(pid, monday, float(v)))
            except (TypeError, ValueError):
                notes.append(f"valeur non numérique ignorée : {r[0]} / {monday} = {v!r}")
    return lines, notes


@dataclass
class ParsedEntry:
    kind: str           # COMMANDE | RECEPTION | AJUSTEMENT | PRODUCTION | PLAN_ARTICLE | PLAN_LINE
    key: str            # article_id or program_id
    supplier_id: str | None
    date: dt.date
    qty: float
    comment: str
    order_id: str | None = None      # receipt posted against an order, or order overridden by a plan line


def _to_date(v: Any) -> dt.date | None:
    if v in (None, ""):
        return None
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    try:
        return dt.date.fromisoformat(str(v).strip()[:10])
    except ValueError:
        return None


def _to_float(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _text(v: Any) -> str:
    return str(v).strip() if v is not None else ""


def parse_entries_workbook(content: bytes) -> tuple[list[ParsedEntry], list[str]]:
    """Read every planner input of an exported workbook (or of a bare ``SAISIES`` sheet).

    * ``SAISIES`` rows: type, article / program, supplier, date, qty, comment, order reference;
    * ``PLAN``: the plan of every article present in the sheet (``PLAN_ARTICLE`` = reset, then one
      ``PLAN_LINE`` per row that differs from the ERP placement or is a free line).
    """
    wb = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    entries: list[ParsedEntry] = []
    notes: list[str] = []

    ws = wb["SAISIES"] if "SAISIES" in wb.sheetnames else wb[wb.sheetnames[0]]
    for n, r in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not r or r[0] in (None, ""):
            continue
        kind = _text(r[0]).upper()
        if kind == "EXEMPLE":
            continue
        if kind not in ("COMMANDE", "RECEPTION", "AJUSTEMENT", "PRODUCTION"):
            notes.append(f"SAISIES ligne {n} : type inconnu {r[0]!r}")
            continue
        date, qty = _to_date(r[3] if len(r) > 3 else None), _to_float(r[4] if len(r) > 4 else None)
        if date is None or qty is None:
            notes.append(f"SAISIES ligne {n} : date ou quantité invalide")
            continue
        key = _text(r[1] if len(r) > 1 else None)
        if not key:
            notes.append(f"SAISIES ligne {n} : article / programme manquant")
            continue
        entries.append(ParsedEntry(kind, key, _text(r[2] if len(r) > 2 else None) or None, date, qty,
                                   _text(r[5] if len(r) > 5 else None), order_id=_text(r[6] if len(r) > 6 else None) or None))

    if "PLAN" in wb.sheetnames:
        # columns: A article, C type, D reference, E supplier, F ERP date, G ERP qty, H plan date, I plan qty.
        # The plan of every article present in the sheet is replaced by the sheet: one PLAN_ARTICLE entry
        # per article, then one PLAN_LINE per row that differs from the ERP (or free line).
        by_article: dict[str, list[tuple[int, tuple]]] = {}
        for n, r in enumerate(wb["PLAN"].iter_rows(min_row=2, values_only=True), start=2):
            if not r or r[0] in (None, "") or len(r) < 9:
                continue
            by_article.setdefault(_text(r[0]), []).append((n, r))
        for aid, rows_ in by_article.items():
            entries.append(ParsedEntry("PLAN_ARTICLE", aid, None, dt.date.today(), 0.0, "plan du classeur"))
            groups: dict[str, list[tuple[int, tuple]]] = {}
            for n, r in rows_:
                groups.setdefault(_text(r[3]) or f"__free_{n}", []).append((n, r))
            for ref, grp in groups.items():
                free = ref.startswith("__free_")
                lines: list[tuple[int, dt.date, float]] = []
                for n, r in grp:
                    d_plan, q_plan = _to_date(r[7]), _to_float(r[8])
                    if d_plan is None and q_plan is None:
                        continue
                    if d_plan is None:
                        d_plan = _to_date(r[5])
                    if d_plan is None:
                        notes.append(f"PLAN ligne {n} : quantité sans date, ignorée")
                        continue
                    if q_plan is None:
                        q_plan = _to_float(r[6]) or 0.0
                    if q_plan < 0:
                        notes.append(f"PLAN ligne {n} : quantité négative, ignorée")
                        continue
                    lines.append((n, d_plan, float(q_plan)))
                if not lines:
                    continue
                erp_date = _to_date(grp[0][1][5])
                erp_qty = _to_float(grp[0][1][6])
                if (not free and len(lines) == 1 and erp_date == lines[0][1] and erp_qty is not None
                        and abs(erp_qty - lines[0][2]) < 1e-9):
                    continue  # ERP taken as is
                supplier = _text(grp[0][1][4]) or None
                comment = _text(grp[0][1][11]) if len(grp[0][1]) > 11 else ""
                for n, d_plan, q_plan in lines:
                    if free and q_plan <= 0:
                        continue
                    entries.append(ParsedEntry("PLAN_LINE", aid, supplier, d_plan, q_plan, comment,
                                               order_id=None if free else ref))

    return entries, notes


# =============================================================================
# PDP template
# =============================================================================
def pdp_template_workbook(programs: list[tuple[str, str]], first_monday: dt.date, weeks: int = 26) -> bytes:
    """Template of the weekly production plan to import: sheet ``SOP - PDP`` with one row per
    programme (name in column A, id in column B) and one column per ISO week (``2026-W40``), plus a
    ``NOTICE`` sheet.  ``programs`` = [(program_id, name), …]."""
    wb = Workbook()
    ws = wb.active
    ws.title = "SOP - PDP"
    ws.cell(1, 1, "PF/SF (nom du programme)")
    ws.cell(1, 2, "program_id (facultatif)")
    for k in range(weeks):
        d = first_monday + dt.timedelta(days=7 * k)
        ws.cell(1, 3 + k, iso_week_label(d))
    _style_header(ws, 1, 2 + weeks)
    for r, (pid, name) in enumerate(sorted(programs, key=lambda x: x[1] or x[0]), start=2):
        ws.cell(r, 1, name or pid)
        ws.cell(r, 2, pid).font = HELPER_FONT
        for k in range(weeks):
            c = ws.cell(r, 3 + k)
            c.fill = INPUT_FILL
            c.font = BLUE_FONT
            c.number_format = QTY_FMT
    ws.freeze_panes = "C2"
    _widths(ws, [34, 18] + [11] * weeks)
    notice = wb.create_sheet("NOTICE")
    lines = [
        "Modèle du plan de production hebdomadaire (PDP) à importer dans APPRO (page Imports / exports).",
        "Onglet SOP - PDP : une ligne par programme, une colonne par semaine ISO.",
        "Colonne A : nom du programme tel que connu dans le référentiel (ou son identifiant) ; colonne B : identifiant, facultatif, prioritaire s'il est renseigné.",
        "En-têtes de semaine acceptés : 2026-W40, S40-26, 2026W40, W40-2026 ou une date (n'importe quel jour de la semaine).",
        "Quantités : nombre de produits finis / semi-finis à produire dans la semaine ; vide = 0 ; les colonnes non reconnues sont ignorées.",
        "Le plan importé, s'il est activé, remplace le PDP ERP pour les programmes qu'il contient ; les autres programmes gardent le PDP ERP.",
        "Format long également accepté : colonnes program_id, week_start (lundi) ou iso_week, qty.",
    ]
    for i, t in enumerate(lines, start=1):
        notice.cell(i, 1, t)
    _widths(notice, (140,))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
