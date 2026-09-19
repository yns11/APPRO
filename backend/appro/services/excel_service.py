"""Excel export / import (openpyxl).

Exports
-------
* ``simulation_workbook`` – rolling-horizon simulation (day or week granularity) for a set of
  articles, plus alerts, proposals, open order book and an empty ``SAISIES`` sheet that can be
  filled and re-imported.
* ``alerts_workbook`` / ``orders_workbook`` – single-sheet extracts.

Imports
-------
* ``parse_pdp_workbook`` – weekly production plan.  Accepts the legacy ``SOP - PDP`` layout
  (program names in column A, week labels ``S11-26`` / ``2028W24`` / ``2026-W11`` / dates in row 1)
  or a long layout (``program_id, week_start, qty``).
* ``parse_entries_workbook`` – the ``SAISIES`` sheet (orders, receipts, adjustments, actuals).
"""
from __future__ import annotations

import datetime as dt
import io
import re
from dataclasses import dataclass
from typing import Any, Iterable

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.rule import CellIsRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from ..engine.calendar import iso_week_label, iso_week_monday
from ..engine.models import MrpResult

HEADER_FILL = PatternFill("solid", fgColor="1F2A44")
HEADER_FONT = Font(bold=True, color="FFFFFF")
LABEL_FILL = PatternFill("solid", fgColor="EEF1F6")
RED_FILL = PatternFill("solid", fgColor="F8D7DA")
YELLOW_FILL = PatternFill("solid", fgColor="FFF3CD")
GREEN_FILL = PatternFill("solid", fgColor="D1E7DD")
BLUE_FONT = Font(color="255D95", bold=True)
THIN = Side(style="thin", color="D0D5DD")

SERIES = [
    ("demand", "Besoin"),
    ("supply_firm", "Commandes fermes"),
    ("supply_planned", "Commandes planifiées / prévisionnelles"),
    ("supply_proposed", "Propositions"),
    ("receipts", "Réceptions"),
    ("adjustments", "Ajustements"),
    ("stock_firm", "Stock ferme"),
    ("stock_sim", "Stock simulé"),
    ("target_stock", "Stock cible"),
    ("coverage_sim", "Couverture (jours)"),
]
FLOW_SERIES = {"demand", "supply_firm", "supply_planned", "supply_proposed", "receipts", "adjustments"}


def _style_header(ws, row: int, ncols: int) -> None:
    for c in range(1, ncols + 1):
        cell = ws.cell(row, c)
        cell.fill = HEADER_FILL
        cell.font = HEADER_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)


def periods(dates: list[dt.date], granularity: str, start: dt.date) -> list[tuple[str, list[int]]]:
    """Group the day indexes of ``dates`` (from ``start``) into labelled periods."""
    out: dict[str, list[int]] = {}
    for i, d in enumerate(dates):
        if d < start:
            continue
        label = d.isoformat() if granularity == "day" else iso_week_label(d)
        out.setdefault(label, []).append(i)
    return list(out.items())


def aggregate(series: list[float], idxs: list[int], kind: str) -> float:
    if kind in FLOW_SERIES:
        return float(sum(series[i] for i in idxs))
    return float(series[idxs[-1]])  # stock-like: end of period


def simulation_workbook(result: MrpResult, article_ids: Iterable[str] | None = None, granularity: str = "day",
                        start: dt.date | None = None, meta: dict[str, Any] | None = None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "PARAMETRES"
    rows = [("Généré le", dt.datetime.now().strftime("%Y-%m-%d %H:%M")), ("Date de référence", result.as_of.isoformat()),
            ("Horizon (jours)", result.params.horizon_days), ("Granularité", granularity)]
    for k, v in (meta or {}).items():
        rows.append((k, str(v)))
    for r, (k, v) in enumerate(rows, start=1):
        ws.cell(r, 1, k).font = Font(bold=True)
        ws.cell(r, 2, v)
    ws.column_dimensions["A"].width = 24
    ws.column_dimensions["B"].width = 40

    ids = list(article_ids) if article_ids else list(result.articles)
    start = start or result.as_of
    # ------------------------------------------------------------------ SIMULATION
    ws = wb.create_sheet("SIMULATION")
    first = next((result.articles[a] for a in ids if a in result.articles), None)
    if first is None:
        ws.cell(1, 1, "Aucun article")
    else:
        pers = periods(first.dates, granularity, start)
        fixed = ["Article", "Désignation", "Fournisseur(s)", "Unité", "Variable"]
        for c, h in enumerate(fixed, start=1):
            ws.cell(1, c, h)
        for c, (label, _) in enumerate(pers, start=len(fixed) + 1):
            ws.cell(1, c, label)
        _style_header(ws, 1, len(fixed) + len(pers))
        r = 2
        for aid in ids:
            ar = result.articles.get(aid)
            if ar is None:
                continue
            sup = " / ".join(sorted({l.supplier_id for l in ar.suppliers}))
            block_start = r
            for key, label in SERIES:
                ws.cell(r, 1, aid)
                ws.cell(r, 2, ar.article.designation)
                ws.cell(r, 3, sup)
                ws.cell(r, 4, ar.article.unit)
                ws.cell(r, 5, label).fill = LABEL_FILL
                series = getattr(ar, key)
                for c, (_, idxs) in enumerate(pers, start=len(fixed) + 1):
                    v = aggregate(series, idxs, key)
                    cell = ws.cell(r, c, round(v, 3) if v else 0)
                    cell.number_format = "#,##0.###"
                    if key in ("stock_firm", "stock_sim"):
                        cell.font = BLUE_FONT
                r += 1
            for c in range(1, len(fixed) + len(pers) + 1):
                ws.cell(r - 1, c).border = Border(bottom=THIN)
            # conditional formats on the block
            last_col = get_column_letter(len(fixed) + len(pers))
            first_col = get_column_letter(len(fixed) + 1)
            stock_rows = [block_start + i for i, (k, _) in enumerate(SERIES) if k in ("stock_firm", "stock_sim")]
            for sr in stock_rows:
                ws.conditional_formatting.add(f"{first_col}{sr}:{last_col}{sr}",
                                              CellIsRule(operator="lessThan", formula=["0"], fill=RED_FILL))
            cov_row = block_start + [k for k, _ in SERIES].index("coverage_sim")
            rng = f"{first_col}{cov_row}:{last_col}{cov_row}"
            ws.conditional_formatting.add(rng, CellIsRule(operator="lessThanOrEqual", formula=[str(ar.article.alert_red_days)], fill=RED_FILL))
            ws.conditional_formatting.add(rng, CellIsRule(operator="lessThanOrEqual", formula=[str(ar.article.alert_yellow_days)], fill=YELLOW_FILL))
            ws.conditional_formatting.add(rng, CellIsRule(operator="greaterThanOrEqual", formula=[str(ar.article.overstock_days)], fill=GREEN_FILL))
        ws.freeze_panes = "F2"
        ws.column_dimensions["A"].width = 13
        ws.column_dimensions["B"].width = 28
        ws.column_dimensions["C"].width = 18
        ws.column_dimensions["E"].width = 34
        for c in range(len(fixed) + 1, len(fixed) + len(pers) + 1):
            ws.column_dimensions[get_column_letter(c)].width = 11

    _alerts_sheet(wb.create_sheet("ALERTES"), result, ids)
    _proposals_sheet(wb.create_sheet("PROPOSITIONS"), result, ids)
    _orders_sheet(wb.create_sheet("CARNET_COMMANDES"), result, ids)
    _entries_template(wb.create_sheet("SAISIES"))
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


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
    for col, w in zip("ABCDEFGH", (13, 28, 18, 10, 10, 12, 12, 90)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"


def _proposals_sheet(ws, result: MrpResult, ids: list[str]) -> None:
    headers = ["Article", "Désignation", "Fournisseur", "Date commande", "Date livraison", "Quantité", "Unité",
               "Besoin net", "MOQ", "PLA", "Délai (j ouvrés)", "Urgent", "Ignorée", "Motif", "Décision (A/M/I)",
               "Qté modifiée", "Date modifiée"]
    for c, h in enumerate(headers, start=1):
        ws.cell(1, c, h)
    _style_header(ws, 1, len(headers))
    for aid in ids:
        ar = result.articles.get(aid)
        if not ar:
            continue
        for p in ar.proposals:
            ws.append([aid, ar.article.designation, p.supplier_id, p.order_date, p.delivery_date, p.qty, ar.article.unit,
                       round(p.net_requirement, 3), p.moq, p.pack_qty, p.lead_time_days, "OUI" if p.urgent else "",
                       "OUI" if p.ignored else "", p.reason, "", "", ""])
    for col, w in zip("ABCDEFGHIJKLMNOPQ", (13, 28, 12, 13, 13, 11, 7, 11, 9, 9, 9, 8, 8, 70, 14, 12, 13)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"


def _orders_sheet(ws, result: MrpResult, ids: list[str]) -> None:
    headers = ["Article", "Désignation", "Type", "Référence", "Fournisseur", "Date attendue", "Quantité", "Origine", "Retard"]
    for c, h in enumerate(headers, start=1):
        ws.cell(1, c, h)
    _style_header(ws, 1, len(headers))
    for aid in ids:
        ar = result.articles.get(aid)
        if not ar:
            continue
        for e in ar.events:
            if e.kind in ("order", "proposal"):
                ws.append([aid, ar.article.designation, e.order_type, e.ref, e.supplier_id, e.date, e.qty, e.source,
                           "OUI" if e.late else ""])
    for col, w in zip("ABCDEFGHI", (13, 28, 11, 18, 12, 13, 12, 10, 8)):
        ws.column_dimensions[col].width = w
    ws.freeze_panes = "A2"


ENTRY_HEADERS = ["Type (COMMANDE/RECEPTION/AJUSTEMENT/PRODUCTION)", "Article ou Programme", "Fournisseur", "Date",
                 "Quantité", "Commentaire"]


def _entries_template(ws) -> None:
    for c, h in enumerate(ENTRY_HEADERS, start=1):
        ws.cell(1, c, h)
    _style_header(ws, 1, len(ENTRY_HEADERS))
    ws.cell(2, 1, "COMMANDE").font = Font(italic=True, color="888888")
    ws.cell(2, 2, "P-00001046").font = Font(italic=True, color="888888")
    ws.cell(2, 3, "S-000545").font = Font(italic=True, color="888888")
    ws.cell(2, 4, "2026-10-15").font = Font(italic=True, color="888888")
    ws.cell(2, 5, 1600).font = Font(italic=True, color="888888")
    ws.cell(2, 6, "exemple – ligne ignorée si type vide").font = Font(italic=True, color="888888")
    for col, w in zip("ABCDEF", (44, 22, 14, 14, 12, 50)):
        ws.column_dimensions[col].width = w


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
    _proposals_sheet(wb.create_sheet("PROPOSITIONS"), result, ids or list(result.articles))
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
    kind: str           # COMMANDE | RECEPTION | AJUSTEMENT | PRODUCTION
    key: str            # article_id or program_id
    supplier_id: str | None
    date: dt.date
    qty: float
    comment: str


def parse_entries_workbook(content: bytes) -> tuple[list[ParsedEntry], list[str]]:
    wb = load_workbook(io.BytesIO(content), data_only=True, read_only=True)
    ws = wb["SAISIES"] if "SAISIES" in wb.sheetnames else wb[wb.sheetnames[0]]
    entries: list[ParsedEntry] = []
    notes: list[str] = []
    for n, r in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not r or r[0] in (None, ""):
            continue
        kind = str(r[0]).strip().upper()
        if kind not in ("COMMANDE", "RECEPTION", "AJUSTEMENT", "PRODUCTION"):
            notes.append(f"ligne {n} : type inconnu {r[0]!r}")
            continue
        try:
            date = r[3].date() if isinstance(r[3], dt.datetime) else dt.date.fromisoformat(str(r[3])[:10])
            qty = float(r[4])
        except (TypeError, ValueError, AttributeError):
            notes.append(f"ligne {n} : date ou quantité invalide")
            continue
        key = str(r[1]).strip() if r[1] is not None else ""
        if not key:
            notes.append(f"ligne {n} : article / programme manquant")
            continue
        entries.append(ParsedEntry(kind, key, (str(r[2]).strip() or None) if r[2] else None, date, qty,
                                   str(r[5]) if len(r) > 5 and r[5] is not None else ""))
    return entries, notes
