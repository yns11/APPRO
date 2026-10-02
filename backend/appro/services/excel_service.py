"""Excel export / import (openpyxl).

Exports
-------
* ``simulation_workbook`` – a **live** simulation workbook: the ``SIMULATION`` grid reproduces the
  application grid (one block per article, one Ferme / Prévisionnel / Reçu / Plan row per supplier)
  and its stock rows are formulas, so the planner can edit the *Plan* and *Ajustement* rows offline,
  see the stocks recompute in Excel, and re-import the workbook.

  ==================  =====================================================================
  sheet               role
  ==================  =====================================================================
  PARAMETRES          rules used by the formulas (shortage policy, target policy, tie rule)
  ARTICLES            per-article inputs: initial stocks, coverage target, safety stock, thresholds
  SIMULATION          one block per article ; Plan and Ajustement rows are inputs (yellow), the
                      stocks, shortage, target and coverage are formulas
  ALERTES             snapshot of the alerts at export time (values)
  ==================  =====================================================================

  Stock recurrence per period (same as the engine, one column per day or ISO week)::

      ERP  : x = stock[p-1] + Σ Reçu + Σ Ferme + A - besoin
      Plan : x = stock[p-1] + Σ Reçu + Σ Plan + CBN + A - besoin
      stock[p]    = IF(shortage policy = "lost", MAX(0, x), x)
      shortage[p] = MAX(0, -x)
      target[p]   = demand of the next N periods (and/or safety stock)
      coverage[p] = number of future periods whose cumulated demand <= stock[p]

* ``alerts_workbook`` / ``plan_workbook`` – single-sheet extracts.
* ``pdp_template_workbook`` – the template of the weekly production plan to import.

Imports
-------
* ``parse_pdp_workbook`` – PDP (legacy wide layout ``SOP - PDP`` or long layout) ;
* ``parse_simulation_workbook`` – the *Plan* and *Ajustement* rows of a ``SIMULATION`` sheet
  exported in **day** granularity (a Plan value that differs from the Ferme value of the same
  column becomes a typed cell ; an Ajustement value becomes an adjustment cell).
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
TYPED_FILL = PatternFill("solid", fgColor="DCE6FF")
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

SIM_HEADER_ROWS = 3       # label / period start / period end
SIM_FIRST_COL = 5         # E
COL_ARTICLE, COL_DESIGNATION, COL_VARIABLE, COL_REF = 1, 2, 3, 4
LANE_ROWS = [("orders_firm", "Ferme", "value"), ("orders_forecast", "Prévisionnel", "value"),
             ("receipts", "Reçu", "value"), ("plan", "Plan", "input")]
TAIL_ROWS = [("cbn", "Proposition CBN", "value"), ("adjustments", "Ajustement", "input"),
             ("stock_erp", "Scenario ERP", "stock"), ("stock_plan", "Scenario Plan", "stock"),
             ("shortage_plan", "Manque (plan, besoin non servi)", "formula"), ("target", "Stock cible", "formula"),
             ("coverage", "Couverture plan (périodes)", "formula"), ("cum_demand", "Besoin cumulé (aide au calcul)", "helper")]


@dataclass(frozen=True)
class BlockRow:
    key: str
    label: str
    kind: str
    lane: int | None = None        # index of the supplier lane, None for article rows


def block_layout(ar: ArticleResult) -> list[BlockRow]:
    """Rows of one article block: Besoin, then Ferme / Prévisionnel / Reçu / Plan per supplier
    (labels suffixed by the supplier when there are several), then the article rows."""
    rows = [BlockRow("demand", "Besoin", "value")]
    several = len(ar.lanes) > 1
    for k, lane in enumerate(ar.lanes):
        for key, label, kind in LANE_ROWS:
            rows.append(BlockRow(key, f"{label} · {lane.supplier_id}" if several and lane.supplier_id else label, kind, k))
    rows.extend(BlockRow(key, label, kind) for key, label, kind in TAIL_ROWS)
    return rows


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
    article_rows = _articles_sheet(ws_art, result, ids, start)
    _alerts_sheet(ws_alerts, result, ids)
    _simulation_grid(ws_sim, result, ids, granularity, start, article_rows)
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
        ("Granularité", granularity, "une colonne par jour ou par semaine ISO ; seul le jour se réimporte"),
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
        "Vide, c'est l'ERP. Un chiffre, c'est votre plan. Le stock se recalcule.",
        "SIMULATION : les lignes Plan (une par fournisseur) et Ajustement sont les saisies (fond jaune ; bleu = cellule déjà saisie dans l'application) ; les autres lignes se recalculent.",
        "Plan : la cellule est préremplie avec la valeur ERP (ligne Ferme) ; modifier la valeur = décaler, réduire, fractionner (taper sur plusieurs jours), 0 = rien attendu ; une valeur égale au Ferme = pas de saisie.",
        "Ajustement : quantité signée, toute date (une date passée corrige le stock de référence).",
        "Réimport dans l'application (Imports / exports) : le plan et les ajustements des articles présents dans le classeur remplacent ceux de l'application (granularité jour uniquement).",
        "Stock net = stock physique − manque (backlog) ; un stock physique n'est jamais négatif.",
    ]
    for i, t in enumerate(notes, start=r + 1):
        ws.cell(i, 1, f"{i - r}.").font = HELPER_FONT
        ws.cell(i, 2, t)
    _widths(ws, (28, 44, 90))


def _layer_start(ar: ArticleResult, key: str, i_start: int) -> float:
    """Net balance of a scenario on the day before the grid start (= what the formulas start from)."""
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
        values = [aid, a.designation, a.unit, " / ".join(l.supplier_id or "" for l in ar.lanes),
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


def _simulation_grid(ws, result: MrpResult, ids: list[str], granularity: str, start: dt.date,
                     article_rows: dict[str, int]) -> None:
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
    for c, h in enumerate(["Article", "Désignation", "Variable", "Fournisseur"], start=1):
        ws.cell(1, c, h)
    ws.cell(2, 1, "Début de période").font = HELPER_FONT
    ws.cell(3, 1, "Fin de période").font = HELPER_FONT
    for c, (label, idxs) in enumerate(pers, start=SIM_FIRST_COL):
        ws.cell(1, c, label)
        _date_cell(ws, 2, c, first.dates[idxs[0]]).font = HELPER_FONT
        _date_cell(ws, 3, c, first.dates[idxs[-1]]).font = HELPER_FONT
    _style_header(ws, 1, ncols)
    week = granularity == "week"
    i_as_of = first.dates.index(result.as_of)

    r = SIM_HEADER_ROWS + 1
    for aid in ids:
        ar = result.articles[aid]
        a = ar.article
        k = article_rows[aid]
        art = "ARTICLES!$"
        layout = block_layout(ar)
        top = r
        rows = {(b.key, b.lane): top + off for off, b in enumerate(layout)}
        row = lambda key, lane=None: rows[(key, lane)]  # noqa: E731
        for off, b in enumerate(layout):
            rr = top + off
            ws.cell(rr, COL_ARTICLE, aid)
            ws.cell(rr, COL_DESIGNATION, a.designation)
            lab = ws.cell(rr, COL_VARIABLE, b.label)
            lab.fill = LABEL_FILL
            if b.kind == "helper":
                lab.font = HELPER_FONT
            elif b.kind == "stock":
                lab.font = BOLD
            if b.lane is not None:
                ws.cell(rr, COL_REF, ar.lanes[b.lane].supplier_id or "")
        ws.cell(row("stock_erp"), COL_REF, f"={art}E${k}").number_format = QTY_FMT
        ws.cell(row("stock_plan"), COL_REF, f"={art}F${k}").number_format = QTY_FMT
        ws.cell(row("target"), COL_REF, f"={art}I${k}").number_format = "0"
        ws.cell(row("coverage"), COL_REF, "périodes").font = HELPER_FONT
        lane_idx = list(range(len(ar.lanes)))

        for c, (_, idxs) in enumerate(pers, start=SIM_FIRST_COL):
            col = get_column_letter(c)
            prev = get_column_letter(c - 1)
            first_period = c == SIM_FIRST_COL
            past = idxs[-1] < i_as_of

            def cell(key: str, value, lane=None):
                x = ws.cell(rows[(key, lane)], c, value)
                x.number_format = QTY_FMT
                return x

            cell("demand", round(float(sum(ar.demand[i] for i in idxs)), 3))
            for li in lane_idx:
                lane = ar.lanes[li]
                firm = float(sum(lane.orders_firm[i] + lane.orders_firm_hist[i] for i in idxs))
                cell("orders_firm", round(firm, 3), li)
                cell("orders_forecast", round(float(sum(lane.orders_forecast[i] for i in idxs)), 3), li)
                cell("receipts", round(float(sum(lane.receipts[i] for i in idxs)), 3), li)
                plan_cell = cell("plan", round(float(sum(lane.plan[i] for i in idxs)), 3), li)
                plan_cell.font = BLUE_FONT
                if not past:
                    plan_cell.fill = TYPED_FILL if any(lane.plan_typed[i] for i in idxs) else INPUT_FILL
            cell("cbn", round(float(sum(ar.supply_proposed[i] for i in idxs)), 3))
            adj = cell("adjustments", round(float(sum(ar.adjustments[i] for i in idxs)), 3))
            adj.font = BLUE_FONT
            adj.fill = INPUT_FILL
            demand_ref = f"{col}{row('demand')}"
            receipts_sum = "+".join(f"{col}{row('receipts', li)}" for li in lane_idx)
            firm_sum = "+".join(f"{col}{row('orders_firm', li)}" for li in lane_idx)
            plan_sum = "+".join(f"{col}{row('plan', li)}" for li in lane_idx)
            common = f"{receipts_sum}+{col}{row('adjustments')}-{demand_ref}"
            erp_in = f"{firm_sum}+{common}"
            plan_in = f"{plan_sum}+{col}{row('cbn')}+{common}"
            for key, inflow in (("stock_erp", erp_in), ("stock_plan", plan_in)):
                prev_ref = f"$D{row(key)}" if first_period else f"{prev}{row(key)}"
                x = f"{prev_ref}+{inflow}"
                cell(key, f'=IF({P_SHORTAGE}="lost",MAX(0,{x}),{x})').font = BOLD
            prev_ref = f"$D{row('stock_plan')}" if first_period else f"{prev}{row('stock_plan')}"
            cell("shortage_plan", f"=MAX(0,-({prev_ref}+{plan_in}))")
            n_per = f"{art}I${k}" if not week else f"ROUNDUP({art}I${k}/7,0)"
            cov = f"IF({n_per}>0,SUM(OFFSET({col}{row('demand')},0,1,1,{n_per})),0)"
            cell("target", f'=IF({P_TARGET}="safety_qty",{art}J${k},IF({P_TARGET}="coverage_days",{cov},MAX({cov},{art}J${k})))')
            if first_period:
                cell("cum_demand", f"={demand_ref}").font = HELPER_FONT
            else:
                cell("cum_demand", f"={prev}{row('cum_demand')}+{demand_ref}").font = HELPER_FONT
            if c < ncols:
                nxt = get_column_letter(c + 1)
                crit = f'IF({P_TIE}="covered","<=","<")&({col}{row("cum_demand")}+{col}{row("stock_plan")})'
                f = f'=IF({col}{row("stock_plan")}<0,0,COUNTIF({nxt}{row("cum_demand")}:{last_col}{row("cum_demand")},{crit}))'
            else:
                f = 0
            ws.cell(row("coverage"), c, f).number_format = "0"

        fc, lc = get_column_letter(SIM_FIRST_COL), last_col
        stock_rng = f"{fc}{row('stock_erp')}:{lc}{row('stock_plan')}"
        ws.conditional_formatting.add(stock_rng, CellIsRule(operator="lessThan", formula=["0"], fill=RED_FILL))
        ws.conditional_formatting.add(stock_rng, FormulaRule(formula=[f"{fc}{row('stock_erp')}<{fc}${row('target')}"], fill=YELLOW_FILL))
        ws.conditional_formatting.add(f"{fc}{row('shortage_plan')}:{lc}{row('shortage_plan')}",
                                      CellIsRule(operator="greaterThan", formula=["0"], fill=RED_FILL))
        cov_rng = f"{fc}{row('coverage')}:{lc}{row('coverage')}"
        ws.conditional_formatting.add(cov_rng, CellIsRule(operator="lessThanOrEqual", formula=[f"{art}K${k}"], fill=RED_FILL))
        ws.conditional_formatting.add(cov_rng, CellIsRule(operator="lessThanOrEqual", formula=[f"{art}L${k}"], fill=YELLOW_FILL))
        ws.conditional_formatting.add(cov_rng, CellIsRule(operator="greaterThanOrEqual", formula=[f"{art}M${k}"], fill=GREEN_FILL))
        for c in range(1, ncols + 1):
            ws.cell(top + len(layout) - 1, c).border = Border(bottom=THIN)
        r = top + len(layout)

    ws.freeze_panes = f"{get_column_letter(SIM_FIRST_COL)}{SIM_HEADER_ROWS + 1}"
    _widths(ws, (13, 26, 34, 12))
    for c in range(SIM_FIRST_COL, ncols + 1):
        ws.column_dimensions[get_column_letter(c)].width = 11


def _alerts_sheet(ws, result: MrpResult, ids: list[str]) -> None:
    headers = ["Article", "Désignation", "Type", "Sévérité", "Scenario", "Date", "Valeur", "Message"]
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


def alerts_workbook(result: MrpResult, ids: list[str] | None = None) -> bytes:
    wb = Workbook()
    _alerts_sheet(wb.active, result, ids or list(result.articles))
    wb.active.title = "ALERTES"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


PLAN_HEADERS = ["Article", "Désignation", "Fournisseur", "Date", "Ferme ERP", "Plan", "Écart", "Proposition CBN", "Saisie"]


def plan_workbook(result: MrpResult, ids: list[str] | None = None) -> bytes:
    """The delivery plan as a list: one row per article / supplier / day with a firm ERP quantity, a
    plan quantity or a CBN proposal, from the reference day."""
    wb = Workbook()
    ws = wb.active
    ws.title = "PLAN"
    for c, h in enumerate(PLAN_HEADERS, start=1):
        ws.cell(1, c, h)
    _style_header(ws, 1, len(PLAN_HEADERS))
    r = 2
    for aid in ids or list(result.articles):
        ar = result.articles.get(aid)
        if not ar:
            continue
        i0 = ar.dates.index(result.as_of)
        for lane in ar.lanes:
            for i in range(i0, len(ar.dates)):
                firm, plan, cbn = lane.orders_firm[i], lane.plan[i], lane.supply_proposed[i]
                if firm or plan or cbn or lane.plan_typed[i]:
                    ws.append([aid, ar.article.designation, lane.supplier_id or "", ar.dates[i], firm, plan, plan - firm, cbn,
                               "oui" if lane.plan_typed[i] else ""])
                    ws.cell(r, 4).number_format = DATE_FMT
                    for c in (5, 6, 7, 8):
                        ws.cell(r, c).number_format = QTY_FMT
                    r += 1
    _widths(ws, (13, 26, 12, 12, 12, 12, 12, 14, 8))
    ws.freeze_panes = "A2"
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
class ParsedCell:
    kind: str                 # PLAN | ADJUSTMENT
    article_id: str
    date: dt.date
    qty: float | None         # None: cleared
    supplier_id: str | None = None


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


def _num(v: Any) -> float | None:
    if v in (None, ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_simulation_workbook(content: bytes) -> tuple[dict[str, list[ParsedCell]], list[str]]:
    """Read the *Plan* and *Ajustement* rows of a ``SIMULATION`` sheet (day granularity).

    Returns, per article present in the sheet, the cells of its plan (one per supplier row and day
    where the Plan value differs from the Ferme value of the same supplier ; ``qty`` None where they
    are equal, i.e. back to the ERP) and its adjustments (``qty`` None for an empty / 0 cell).
    """
    wb = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    if "SIMULATION" not in wb.sheetnames:
        raise ValueError("onglet SIMULATION absent")
    rows = list(wb["SIMULATION"].iter_rows(values_only=True))
    if len(rows) <= SIM_HEADER_ROWS:
        raise ValueError("onglet SIMULATION vide")
    notes: list[str] = []
    header = rows[0]
    day_cols: dict[int, dt.date] = {}
    for c in range(SIM_FIRST_COL - 1, len(header)):
        label = header[c]
        if label is None:
            continue
        if "-W" in str(label):
            raise ValueError("classeur en granularité semaine : seul un export « Jour » se réimporte")
        d = _to_date(rows[1][c]) or _to_date(label)
        if d is not None:
            day_cols[c] = d
    if not day_cols:
        raise ValueError("aucune colonne jour reconnue dans SIMULATION")
    out: dict[str, list[ParsedCell]] = {}
    firm_by_lane: dict[tuple[str, str], list] = {}
    for r in rows[SIM_HEADER_ROWS:]:
        if not r or r[COL_ARTICLE - 1] in (None, ""):
            continue
        aid = str(r[COL_ARTICLE - 1]).strip()
        label = str(r[COL_VARIABLE - 1] or "").strip()
        base = label.split(" · ")[0]
        supplier = str(r[COL_REF - 1] or "").strip() or None
        out.setdefault(aid, [])
        if base == "Ferme":
            firm_by_lane[(aid, supplier or "")] = r
        elif base == "Plan":
            firm = firm_by_lane.get((aid, supplier or ""))
            for c, d in day_cols.items():
                v = _num(r[c] if c < len(r) else None)
                f = _num(firm[c] if firm is not None and c < len(firm) else None) or 0.0
                if v is None:
                    out[aid].append(ParsedCell("PLAN", aid, d, None, supplier))
                elif abs(v - f) < 1e-9:
                    out[aid].append(ParsedCell("PLAN", aid, d, None, supplier))
                else:
                    out[aid].append(ParsedCell("PLAN", aid, d, max(v, 0.0), supplier))
        elif base == "Ajustement":
            for c, d in day_cols.items():
                v = _num(r[c] if c < len(r) else None)
                out[aid].append(ParsedCell("ADJUSTMENT", aid, d, None if not v else v))
    return out, notes


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
    for r, (pid, name) in enumerate(sorted(programs, key=lambda p: p[1]), start=2):
        ws.cell(r, 1, name)
        ws.cell(r, 2, pid).font = HELPER_FONT
        for k in range(weeks):
            ws.cell(r, 3 + k).fill = INPUT_FILL
    ws.freeze_panes = "C2"
    _widths(ws, [30, 18] + [11] * weeks)
    notice = wb.create_sheet("NOTICE")
    lines = [
        "Modèle du PDP hebdomadaire à importer dans Ma Routine Appro (page Imports / exports).",
        "Onglet « SOP - PDP » : une ligne par programme (colonne A = nom du programme tel que connu dans le référentiel ; colonne B = identifiant, facultatif), une colonne par semaine ISO.",
        "En-têtes de semaine acceptés : 2026-W40, S40-26, 2026W40, W40-2026 ou une date de la semaine.",
        "Cellules : quantité à produire dans la semaine (vide = 0 / inchangé). Les programmes inconnus sont ignorés et signalés dans le rapport d'import.",
        "Une version importée et activée remplace le PDP ERP pour les programmes qu'elle contient ; les versions précédentes restent consultables.",
        "Le PDP de la semaine en cours n'est utilisé que pour son reliquat : PDP − production réelle déjà déclarée, réparti sur les jours ouvrés restants.",
    ]
    for i, t in enumerate(lines, start=1):
        notice.cell(i, 1, t)
    notice.column_dimensions["A"].width = 140
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
