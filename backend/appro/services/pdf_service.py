"""Printable PDF documents (reportlab).  Today : the **inventory sheet** of a list of articles – what a
planner takes on the shop floor to count a stock : one line per article with the theoretical stock
(« stock à date », the projected stock at the end of yesterday) and blank columns to fill by hand."""
from __future__ import annotations

import datetime as dt
from io import BytesIO
from typing import Iterable

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle

from ..engine.models import ArticleResult


def _fmt_qty(v: float, unit: str) -> str:
    """French number : thousands separated by a space, three decimals for non-piece units when needed."""
    decimal = unit.upper() not in ("PCE", "PC", "PCS", "U", "UN", "") or abs(v - round(v)) > 1e-9
    s = f"{v:,.3f}" if decimal else f"{v:,.0f}"
    s = s.replace(",", " ").replace(".", ",")
    return s.rstrip("0").rstrip(",") if decimal and "," in s else s


def inventory_pdf(as_of: dt.date, articles: Iterable[ArticleResult], supplier_names: dict[str, str],
                  perimeter: str, filters: str = "") -> bytes:
    """A4 landscape inventory sheet : header (date, perimeter, filters), one line per article (code,
    designation, unit, suppliers, theoretical stock, blank *Quantité comptée* / *Écart* / *Commentaire*),
    footer with the page number and a signature line.  The theoretical stock is the stock at date of the
    application (end of the day before ``as_of``) ; the count is meant to be typed back as an
    *Ajustement* of the day."""
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=12 * mm, rightMargin=12 * mm, topMargin=12 * mm, bottomMargin=14 * mm,
                            title=f"Feuille d'inventaire {as_of.isoformat()}", author="Ma Routine Appro")
    styles = getSampleStyleSheet()
    h = ParagraphStyle("h", parent=styles["Title"], fontSize=15, leading=18, alignment=0, spaceAfter=2)
    meta = ParagraphStyle("meta", parent=styles["Normal"], fontSize=9, leading=12, textColor=colors.HexColor("#475467"))
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=8.5, leading=10.5)
    cell_r = ParagraphStyle("cell_r", parent=cell, alignment=TA_RIGHT)
    head = ParagraphStyle("head", parent=cell, fontName="Helvetica-Bold", textColor=colors.white)
    head_r = ParagraphStyle("head_r", parent=head, alignment=TA_RIGHT)
    date_fr = as_of.strftime("%d/%m/%Y")
    story = [Paragraph("Feuille d'inventaire", h),
             Paragraph(f"Ma Routine Appro · périmètre : <b>{perimeter}</b> · stock théorique à la fin de la veille du <b>{date_fr}</b>"
                       f"{(' · ' + filters) if filters else ''}", meta),
             Paragraph("Compter chaque article, reporter la quantité comptée et l'écart (compté − théorique) ; saisir ensuite l'écart "
                       "comme <b>Ajustement</b> du jour dans le tableau d'approvisionnement.", meta),
             Spacer(1, 5 * mm)]
    rows = [[Paragraph("Article", head), Paragraph("Désignation", head), Paragraph("Unité", head), Paragraph("Fournisseur(s)", head),
             Paragraph("Stock théorique", head_r), Paragraph("Quantité comptée", head_r), Paragraph("Écart", head_r), Paragraph("Commentaire", head)]]
    for ar in articles:
        a = ar.article
        sups = [f"{l.supplier_id} · {supplier_names.get(l.supplier_id, l.name) or l.name}" for l in ar.lanes if l.supplier_id]
        stock = float(ar.kpis.get("stock_at_date", 0.0))
        rows.append([Paragraph(f"<b>{a.article_id}</b>", cell), Paragraph(a.designation or "", cell), Paragraph(a.unit or "", cell),
                     Paragraph("<br/>".join(sups) or "–", cell), Paragraph(_fmt_qty(stock, a.unit), cell_r), "", "", ""])
    widths = [30 * mm, 70 * mm, 14 * mm, 58 * mm, 26 * mm, 28 * mm, 22 * mm, 25 * mm]
    table = LongTable(rows, colWidths=widths, repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2f5bea")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f3f5fa")]),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d1de")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 1), (-1, -1), 7), ("BOTTOMPADDING", (0, 1), (-1, -1), 7),
    ]))
    story.append(table)
    story.append(Spacer(1, 8 * mm))
    story.append(Paragraph("Compté par : ______________________________ &nbsp;&nbsp;&nbsp; Date : ____ / ____ / ________ "
                           "&nbsp;&nbsp;&nbsp; Visa : ______________", meta))

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#667085"))
        canvas.drawString(12 * mm, 7 * mm, f"Feuille d'inventaire · {perimeter} · {date_fr}")
        canvas.drawRightString(landscape(A4)[0] - 12 * mm, 7 * mm, f"page {d.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
