"""Excel export / import (openpyxl).

Exports
-------
* ``simulation_workbook`` – a **live** simulation workbook: the ``SIMULATION`` grid is made of
  formulas fed by editable sheets, so the planner can type orders, receipts, adjustments or
  decisions and see stocks, target, shortages and coverage recompute in Excel:

  ==================  =====================================================================
  sheet               role
  ==================  =====================================================================
  PARAMETRES          rules used by the formulas (shortage policy, target policy, tie rule)
  ARTICLES            per-article inputs: initial stocks, coverage target, safety stock, thresholds
  SIMULATION          one block of 15 rows per article; only *Besoin* and *Réceptions & ajustements
                      connus* are values, everything else is a formula
  CARNET_COMMANDES    open order book (firm / forecast): editable date, quantity, received
                      quantity, receipt date, status → feeds the order rows
  SAISIES             free entries (orders, receipts, adjustments, actual production) → feeds
                      the *Saisies* rows; re-importable
  ALERTES             snapshot of the alerts at export time (values)
  ==================  =====================================================================

  The *Commandes simulées* row holds the simulated orders (typed cells and CBN results) as
  editable values: it is re-imported as such.

  Stock recurrence per period (same as the engine, one column per day or ISO week)::

      x           = stock[p-1] + orders[p] + receipts & adjustments[p] - demand[p]
      stock[p]    = IF(shortage policy = "lost", MAX(0, x), x)
      shortage[p] = MAX(0, -x)
      target[p]   = demand of the next N periods (and/or safety stock)
      coverage[p] = number of future periods whose cumulated demand <= stock[p]

* ``alerts_workbook`` / ``orders_workbook`` – single-sheet extracts.

Imports
-------
* ``parse_pdp_workbook`` – weekly production plan.  Accepts the legacy ``SOP - PDP`` layout
  (program names in column A, week labels ``S11-26`` / ``2028W24`` / ``2026-W11`` / dates in row 1)
  or a long layout (``program_id, week_start, qty``).
* ``parse_entries_workbook`` – the ``SAISIES`` sheet (orders, receipts, adjustments, actuals),
  the receipts typed in the ``CARNET_COMMANDES`` sheet and the *Commandes simulées* row of the
  ``SIMULATION`` grid.
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
    ("demand", "Besoin", "input"),
    ("orders_firm", "Commandes fermes (carnet)", "formula"),
    ("orders_forecast", "Commandes prévisionnelles ERP (carnet)", "formula"),
    ("sim_orders", "Commandes simulées", "input"),
    ("entries_orders", "Saisies : commandes (SAISIES)", "formula"),
    ("entries_receipts", "Saisies : réceptions & ajustements (SAISIES, carnet)", "formula"),
    ("known_receipts", "Réceptions & ajustements connus", "input"),
    ("stock_firm", "Stock ferme", "stock"),
    ("stock_forecast", "Stock prévisionnel", "stock"),
    ("stock_sim", "Stock simulé", "stock"),
    ("shortage_sim", "Manque simulé (besoin non servi)", "formula"),
    ("target", "Stock cible", "formula"),
    ("coverage", "Couverture simulée (périodes)", "formula"),
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
    ws_alerts = wb.create_sheet("ALERTES")
    ws_orders = wb.create_sheet("CARNET_COMMANDES")
    ws_entries = wb.create_sheet("SAISIES")

    article_rows = _articles_sheet(ws_art, result, ids, start)
    n_orders = _orders_sheet(ws_orders, result, ids, start, live=True)
    _entries_template(ws_entries)
    _alerts_sheet(ws_alerts, result, ids)
    _simulation_grid(ws_sim, result, ids, granularity, start, article_rows, orders_last_row=n_orders + SPARE_ROWS)
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
        "SIMULATION : les lignes Besoin, Commandes simulées et Réceptions & ajustements connus sont des valeurs modifiables (formules Excel acceptées) ; les autres lignes se recalculent.",
        "Commandes simulées = commandes saisies dans l'application et résultats du Calcul CBN, par jour (quantité signée).",
        "CARNET_COMMANDES : modifier la date attendue / la quantité, saisir une quantité reçue et sa date, ou un statut RECUE / ANNULEE.",
        "SAISIES : nouvelles commandes, réceptions, ajustements (quantité signée) ou production réelle ; date au format date.",
        "Réimport dans l'application (Imports / exports) : SAISIES, réceptions saisies dans le CARNET et ligne Commandes simulées (remplace les commandes simulées de l'article).",
        "Stock net = stock physique − manque (backlog) ; un stock physique n'est jamais négatif.",
    ]
    for i, t in enumerate(notes, start=r + 1):
        ws.cell(i, 1, f"{i - r}.").font = HELPER_FONT
        ws.cell(i, 2, t)
    _widths(ws, (28, 44, 90))


def _layer_start(ar: ArticleResult, key: str, i_start: int) -> float:
    """Net balance of a layer on the day before the grid start (= what the formulas start from)."""
    series = getattr(ar, key)
    return float(series[i_start - 1]) if i_start > 0 else float(ar.kpis["stock_on_hand"])


def _articles_sheet(ws, result: MrpResult, ids: list[str], start: dt.date) -> dict[str, int]:
    headers = ["Article", "Désignation", "Unité", "Fournisseur(s)", "Stock initial ferme", "Stock initial prévisionnel",
               "Stock initial simulé", "Stock au", "Couverture cible (j)", "Stock de sécurité", "Seuil rouge (j)",
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
                  _layer_start(ar, "stock_firm_net", i_start), _layer_start(ar, "stock_forecast_net", i_start),
                  _layer_start(ar, "stock_sim_net", i_start), ar.dates[i_start - 1] if i_start > 0 else None,
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
                     article_rows: dict[str, int], orders_last_row: int) -> None:
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
    o = f"CARNET_COMMANDES!${{}}$2:${{}}${orders_last_row}"
    o_art, o_type, o_date, o_rest = o.format("A", "A"), o.format("C", "C"), o.format("F", "F"), o.format("M", "M")
    o_recv, o_recv_date = o.format("J", "J"), o.format("K", "K")
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
        ws.cell(rows["stock_firm"], 4, f"={art}E${k}").number_format = QTY_FMT
        ws.cell(rows["stock_forecast"], 4, f"={art}F${k}").number_format = QTY_FMT
        ws.cell(rows["stock_sim"], 4, f"={art}G${k}").number_format = QTY_FMT
        ws.cell(rows["target"], 4, f"={art}I${k}").number_format = "0"
        ws.cell(rows["coverage"], 4, "périodes").font = HELPER_FONT

        for c, (_, idxs) in enumerate(pers, start=SIM_FIRST_COL):
            col = get_column_letter(c)
            prev = get_column_letter(c - 1)
            first_period = c == SIM_FIRST_COL
            crit_date = ((o_date, f'">="&{col}$2'), (o_date, f'"<="&{col}$3'))

            def cell(key: str, value):
                x = ws.cell(rows[key], c, value)
                x.number_format = QTY_FMT
                return x

            v = float(sum(ar.demand[i] for i in idxs))
            cell("demand", round(v, 3) if v else 0).font = BLUE_FONT
            ws.cell(rows["demand"], c).fill = INPUT_FILL
            v = float(sum(ar.receipts[i] + ar.adjustments[i] for i in idxs))
            cell("known_receipts", round(v, 3) if v else 0).font = BLUE_FONT
            ws.cell(rows["known_receipts"], c).fill = INPUT_FILL
            v = float(sum(ar.supply_planned[i] for i in idxs))
            cell("sim_orders", round(v, 3) if v else None).font = BLUE_FONT
            ws.cell(rows["sim_orders"], c).fill = INPUT_FILL
            cell("orders_firm", "=" + _sumifs(o_rest, (o_art, f"$A{rows['orders_firm']}"), (o_type, '"FIRM"'), *crit_date))
            cell("orders_forecast", "=" + _sumifs(o_rest, (o_art, f"$A{rows['orders_forecast']}"), (o_type, '"FORECAST"'), *crit_date))
            e_crit = ((e_art, f"$A{rows['entries_orders']}"), (e_date, f'">="&{col}$2'), (e_date, f'"<="&{col}$3'))
            cell("entries_orders", "=" + _sumifs(e_qty, (e_type, '"COMMANDE"'), *e_crit))
            cell("entries_receipts", "=" + _sumifs(e_qty, (e_type, '"RECEPTION"'), *e_crit)
                 + "+" + _sumifs(e_qty, (e_type, '"AJUSTEMENT"'), *e_crit)
                 + "+" + _sumifs(o_recv, (o_art, f"$A{rows['entries_receipts']}"), (o_recv_date, f'">="&{col}$2'), (o_recv_date, f'"<="&{col}$3')))
            # stock layers
            flows = f"{col}{rows['entries_receipts']}+{col}{rows['known_receipts']}-{col}{rows['demand']}"
            firm_in = f"{col}{rows['orders_firm']}"
            fc_in = firm_in + f"+{col}{rows['orders_forecast']}"
            sim_in = fc_in + f"+{col}{rows['sim_orders']}+{col}{rows['entries_orders']}"
            for key, inflow in (("stock_firm", firm_in), ("stock_forecast", fc_in), ("stock_sim", sim_in)):
                prev_ref = f"$D{rows[key]}" if first_period else f"{prev}{rows[key]}"
                x = f"{prev_ref}+{inflow}+{flows}"
                cell(key, f'=IF({P_SHORTAGE}="lost",MAX(0,{x}),{x})').font = BOLD
            prev_ref = f"$D{rows['stock_sim']}" if first_period else f"{prev}{rows['stock_sim']}"
            cell("shortage_sim", f"=MAX(0,-({prev_ref}+{sim_in}+{flows}))")
            # target: demand of the next N periods and/or safety stock
            n_per = f"{art}I${k}" if not week else f"ROUNDUP({art}I${k}/7,0)"
            cov = f"IF({n_per}>0,SUM(OFFSET({col}{rows['demand']},0,1,1,{n_per})),0)"
            cell("target", f'=IF({P_TARGET}="safety_qty",{art}J${k},IF({P_TARGET}="coverage_days",{cov},MAX({cov},{art}J${k})))')
            # coverage: future periods whose cumulated demand is covered by the simulated stock
            if first_period:
                cell("cum_demand", f"={col}{rows['demand']}").font = HELPER_FONT
            else:
                cell("cum_demand", f"={prev}{rows['cum_demand']}+{col}{rows['demand']}").font = HELPER_FONT
            if c < ncols:
                nxt = get_column_letter(c + 1)
                crit = f'IF({P_TIE}="covered","<=","<")&({col}{rows["cum_demand"]}+{col}{rows["stock_sim"]})'
                f = f'=IF({col}{rows["stock_sim"]}<0,0,COUNTIF({nxt}{rows["cum_demand"]}:{last_col}{rows["cum_demand"]},{crit}))'
            else:
                f = 0
            ws.cell(rows["coverage"], c, f).number_format = "0"

        # conditional formats
        fc, lc = get_column_letter(SIM_FIRST_COL), last_col
        stock_rng = f"{fc}{rows['stock_firm']}:{lc}{rows['stock_sim']}"
        ws.conditional_formatting.add(stock_rng, CellIsRule(operator="lessThan", formula=["0"], fill=RED_FILL))
        ws.conditional_formatting.add(stock_rng, FormulaRule(formula=[f"{fc}{rows['stock_firm']}<{fc}${rows['target']}"], fill=YELLOW_FILL))
        ws.conditional_formatting.add(f"{fc}{rows['shortage_sim']}:{lc}{rows['shortage_sim']}",
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


ORDER_HEADERS = ["Article", "Désignation", "Type", "Référence", "Fournisseur", "Date attendue", "Quantité", "Origine",
                 "Retard", "Reçu (saisie)", "Date réception (saisie)", "Statut (saisie : RECUE / ANNULEE)", "Reste à livrer"]


def _orders_sheet(ws, result: MrpResult, ids: list[str], start: dt.date | None = None, live: bool = False) -> int:
    """Open order book (ERP firm / forecast orders; simulated orders live in the grid). Return the row count."""
    for c, h in enumerate(ORDER_HEADERS, start=1):
        ws.cell(1, c, h)
    _style_header(ws, 1, len(ORDER_HEADERS))
    r = 2
    for aid in ids:
        ar = result.articles.get(aid)
        if not ar:
            continue
        for e in ar.events:
            if e.kind != "order" or e.order_type == "PLANNED" or (start and e.date < start):
                continue
            ws.append([aid, ar.article.designation, e.order_type, e.ref, e.supplier_id, e.date, e.qty, e.source,
                       "OUI" if e.late else "", None, None, None, None])
            ws.cell(r, 6).number_format = DATE_FMT
            for c in (6, 7, 10, 11, 12):
                ws.cell(r, c).fill = INPUT_FILL
                ws.cell(r, c).font = BLUE_FONT
            ws.cell(r, 11).number_format = DATE_FMT
            r += 1
    if live:
        for rr in range(2, r + SPARE_ROWS):
            ws.cell(rr, 13, f'=IF(OR(L{rr}="RECUE",L{rr}="ANNULEE"),0,MAX(0,G{rr}-J{rr}))').number_format = QTY_FMT
            ws.cell(rr, 11).number_format = DATE_FMT
    _widths(ws, (13, 28, 11, 18, 12, 13, 12, 10, 8, 12, 14, 18, 12))
    ws.freeze_panes = "A2"
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
    _orders_sheet(wb.active, result, ids or list(result.articles))
    wb.active.title = "CARNET_COMMANDES"
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
    kind: str           # COMMANDE | RECEPTION | AJUSTEMENT | PRODUCTION | COMMANDE_SIMULEE
    key: str            # article_id or program_id
    supplier_id: str | None
    date: dt.date
    qty: float
    comment: str
    order_id: str | None = None      # receipt posted against an order (app id or ERP reference)
    proposal_id: str | None = None   # order created from a proposal decision


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
    * ``CARNET_COMMANDES`` rows with a received quantity → receipts against the order reference;
    * ``SIMULATION`` grid, row *Commandes simulées*: one ``COMMANDE_SIMULEE`` entry per period
      (value or empty → the cell of that day is set / cleared; a week maps to its first day).
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

    if "CARNET_COMMANDES" in wb.sheetnames:
        # columns: A article, D reference, E supplier, F expected date, J received qty, K receipt date
        for n, r in enumerate(wb["CARNET_COMMANDES"].iter_rows(min_row=2, values_only=True), start=2):
            if not r or r[0] in (None, "") or len(r) < 10:
                continue
            qty = _to_float(r[9])
            if not qty or qty <= 0:
                continue
            date = _to_date(r[10] if len(r) > 10 else None) or _to_date(r[5])
            if date is None:
                notes.append(f"CARNET_COMMANDES ligne {n} : date de réception invalide")
                continue
            entries.append(ParsedEntry("RECEPTION", _text(r[0]), _text(r[4]) or None, date, qty,
                                       f"réception saisie dans le carnet ({_text(r[3])})", order_id=_text(r[3]) or None))

    if "SIMULATION" in wb.sheetnames:
        rows = list(wb["SIMULATION"].iter_rows(values_only=True))
        if len(rows) > SIM_HEADER_ROWS:
            starts = [_to_date(v) for v in rows[1][SIM_FIRST_COL - 1:]]
            label = BLOCK[ROW["sim_orders"]][1]
            for r in rows[SIM_HEADER_ROWS:]:
                if not r or len(r) < 3 or _text(r[2]) != label or r[0] in (None, ""):
                    continue
                for j, day in enumerate(starts):
                    if day is None:
                        continue
                    v = r[SIM_FIRST_COL - 1 + j] if len(r) > SIM_FIRST_COL - 1 + j else None
                    qty = _to_float(v)
                    if qty is None and v not in (None, ""):
                        notes.append(f"SIMULATION {r[0]} {day} : valeur non numérique ignorée ({v!r}) – recalculer le classeur")
                        continue
                    entries.append(ParsedEntry("COMMANDE_SIMULEE", _text(r[0]), None, day, qty or 0.0,
                                               "réimport de la ligne Commandes simulées"))
    return entries, notes
