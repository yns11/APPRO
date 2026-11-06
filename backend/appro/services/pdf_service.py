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
    articles = list(articles)
    with_erp = any(ar.kpis.get("erp_stock_date") for ar in articles)
    erp_dates = sorted({ar.kpis["erp_stock_date"] for ar in articles if ar.kpis.get("erp_stock_date")})
    if with_erp:
        story.insert(2, Paragraph(f"Stock ERP du {dt.date.fromisoformat(erp_dates[-1]):%d/%m/%Y} (snapshot de la veille, entrepôts configurés) ; "
                                  "● = écart déjà signalé entre l'application et l'ERP : à compter en priorité.", meta))
    rows = [[Paragraph("Article", head), Paragraph("Désignation", head), Paragraph("Unité", head), Paragraph("Fournisseur(s)", head),
             Paragraph("Stock théorique", head_r)]
            + ([Paragraph("Stock ERP (veille)", head_r), Paragraph("Écart appli − ERP", head_r)] if with_erp else [])
            + [Paragraph("Quantité comptée", head_r), Paragraph("Écart compté", head_r), Paragraph("Commentaire", head)]]
    for ar in articles:
        a = ar.article
        sups = [f"{l.supplier_id} · {supplier_names.get(l.supplier_id, l.name) or l.name}" for l in ar.lanes if l.supplier_id]
        stock = float(ar.kpis.get("stock_at_date", 0.0))
        row = [Paragraph(f"<b>{a.article_id}</b>" + (" <font color='#b54708'>●</font>" if ar.kpis.get("erp_stock_drift") else ""), cell),
               Paragraph(a.designation or "", cell), Paragraph(a.unit or "", cell), Paragraph("<br/>".join(sups) or "–", cell),
               Paragraph(_fmt_qty(stock, a.unit), cell_r)]
        if with_erp:
            q, gap = ar.kpis.get("erp_stock_qty"), ar.kpis.get("erp_stock_gap")
            row += [Paragraph(_fmt_qty(float(q), a.unit) if q is not None else "–", cell_r),
                    Paragraph(("<b>" if ar.kpis.get("erp_stock_beyond") else "") + (("+" if -gap > 0 else "") + _fmt_qty(-float(gap), a.unit) if gap is not None else "–")
                              + ("</b>" if ar.kpis.get("erp_stock_beyond") else ""), cell_r)]
        rows.append(row + ["", "", ""])
    widths = ([28 * mm, 54 * mm, 12 * mm, 46 * mm, 24 * mm, 24 * mm, 22 * mm, 24 * mm, 20 * mm, 19 * mm] if with_erp
              else [30 * mm, 70 * mm, 14 * mm, 58 * mm, 26 * mm, 28 * mm, 22 * mm, 25 * mm])
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


# =============================================================================
# monthly OTD closing (docs § 14) : one supplier, or the portfolio (summary then one supplier per page)
# =============================================================================
_OK, _WARN, _BAD, _INFO, _MUTED = "#067647", "#b54708", "#b42318", "#026aa2", "#667085"
_OK_BG, _WARN_BG, _BAD_BG = "#dcfae6", "#fef0c7", "#fee4e2"


def _tone(v: float | None) -> str:
    return _MUTED if v is None else _BAD if v < 60 else _WARN if v < 85 else _OK


def _bg(v: float | None) -> colors.Color:
    return colors.HexColor(_BAD_BG if v is not None and v < 60 else _WARN_BG if v is not None and v < 85 else _OK_BG)


def _n(v: float | None, digits: int = 0) -> str:
    return "–" if v is None else f"{v:.{digits}f}"


def _fr(iso: str | None, pattern: str = "%d/%m") -> str:
    return dt.date.fromisoformat(iso).strftime(pattern) if iso else "–"


_WD_FR = ["lun.", "mar.", "mer.", "jeu.", "ven.", "sam.", "dim."]


def _wd(iso: str) -> str:
    d = dt.date.fromisoformat(iso)
    return f"{_WD_FR[d.weekday()]} {d:%d/%m}"


def _weekdays_fr(spec: str) -> str:
    names = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
    days = sorted({int(x) for x in str(spec or "").replace(";", ",").split(",") if x.strip().isdigit()})
    return ", ".join(names[d - 1] for d in days if 1 <= d <= 7) or "non renseignés"


def _cumul_drawing(sheet: dict, width: float, height: float):
    """Step curves of the cumulative expected and received quantities over the weeks of the month."""
    from reportlab.graphics.shapes import Drawing, Line, PolyLine, Rect, String
    days = [c for w in sheet["weeks"] for c in w["days"]]
    if not days:
        return None
    ce = cr = 0.0
    pts: list[tuple[str, float, float]] = []
    for c in days:
        ce += float(c["expected"] or 0.0)
        cr += float(c["received"] or 0.0)
        pts.append((c["date"], ce, cr))
    top = max(1.0, max(max(e, r) for _d, e, r in pts))
    left, bottom, right, up = 38, 16, width - 6, height - 6
    sx = (right - left) / max(1, len(pts))
    sy = (up - bottom) / top
    d = Drawing(width, height)
    d.add(Rect(0, 0, width, height, fillColor=colors.white, strokeColor=None))
    for k in range(5):
        y = bottom + k * (up - bottom) / 4
        d.add(Line(left, y, right, y, strokeColor=colors.HexColor("#eef1f6"), strokeWidth=0.6))
        d.add(String(left - 4, y - 2.5, _fmt_qty(top * k / 4, ""), fontSize=6, textAnchor="end", fillColor=colors.HexColor(_MUTED)))

    def step(idx: int) -> list[float]:
        out = [left, bottom]
        prev = 0.0
        for i, p in enumerate(pts):
            x0, x1 = left + i * sx, left + (i + 1) * sx
            out += [x0, bottom + prev * sy, x0, bottom + p[idx] * sy, x1, bottom + p[idx] * sy]
            prev = p[idx]
        return out
    d.add(PolyLine(step(1), strokeColor=colors.HexColor("#667085"), strokeWidth=1.4))
    d.add(PolyLine(step(2), strokeColor=colors.HexColor("#1f44c4"), strokeWidth=1.4))
    mondays = {w["monday"] for w in sheet["weeks"]}
    for i, p in enumerate(pts):
        if p[0] in mondays:
            d.add(String(left + i * sx + 2, 4, _fr(p[0]), fontSize=6, fillColor=colors.HexColor(_MUTED)))
    d.add(Line(left, bottom, right, bottom, strokeColor=colors.HexColor("#dfe4ec"), strokeWidth=0.8))
    d.add(String(right, up - 2, "attendu (gris) · reçu (bleu)", fontSize=6, textAnchor="end", fillColor=colors.HexColor(_MUTED)))
    return d


def _points_drawing(sheet: dict, width: float, height: float = 12):
    from reportlab.graphics.shapes import Drawing, Rect, String
    pts = sheet["scores"]["points"]
    d = Drawing(width, height)
    x = 0.0
    for key, col in (("qty", "#079455"), ("punctuality", "#dc6803"), ("desadv", "#0086c9")):
        v = pts.get(key) or 0.0
        w = width * v / 100.0
        if w > 0:
            d.add(Rect(x, 0, w, height, fillColor=colors.HexColor(col), strokeColor=None))
            if w > 14:
                d.add(String(x + w / 2, 3, _n(v), fontSize=7, textAnchor="middle", fillColor=colors.white, fontName="Helvetica-Bold"))
        x += w
    if x < width - 0.5:
        d.add(Rect(x, 0, width - x, height, fillColor=colors.HexColor(_BAD_BG), strokeColor=None))
        if width - x > 18:
            d.add(String(x + (width - x) / 2, 3, f"−{_n(pts.get('lost'))}", fontSize=7, textAnchor="middle", fillColor=colors.HexColor(_BAD), fontName="Helvetica-Bold"))
    return d


def otd_closing_pdf(month: str, sheets: list[dict], frozen: dict | None, as_of: dt.date, planner: str | None = None) -> bytes:
    """A4 portrait.  Several sheets : a first page with the portfolio summary (one line per supplier), then one supplier
    per page ; one sheet : that supplier's page alone.  Each supplier page : the formula with its figures and the points
    bar, the three indicators decomposed, the evaluated days with their scores, the cumulative curves, the rule."""
    from reportlab.platypus import KeepTogether, PageBreak, Table

    from .otd import month_bounds, month_label
    buf = BytesIO()
    label = month_label(month)
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=13 * mm, bottomMargin=16 * mm,
                            title=f"Arrêté OTD {label}", author="Ma Routine Appro")
    W = A4[0] - 28 * mm
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=16, leading=19, alignment=0, spaceAfter=1)
    h2 = ParagraphStyle("h2", parent=styles["Heading3"], fontSize=10.5, leading=13, spaceBefore=6, spaceAfter=3)
    meta = ParagraphStyle("meta", parent=styles["Normal"], fontSize=8.5, leading=11, textColor=colors.HexColor("#475467"))
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=9, leading=12)
    big = ParagraphStyle("big", parent=styles["Normal"], fontSize=11, leading=14)
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=7.8, leading=9.6)
    cell_r = ParagraphStyle("cell_r", parent=cell, alignment=TA_RIGHT)
    head = ParagraphStyle("head", parent=cell, fontName="Helvetica-Bold", textColor=colors.HexColor("#475467"), fontSize=7)
    head_r = ParagraphStyle("head_r", parent=head, alignment=TA_RIGHT)
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=7.2, leading=9.2, textColor=colors.HexColor(_MUTED))
    mon0, sun1 = month_bounds(month)
    weeks_txt = f"semaines {mon0.isocalendar()[1]} à {(sun1 - dt.timedelta(days=6)).isocalendar()[1]} · {mon0:%d/%m} → {sun1:%d/%m/%Y}"
    who = (frozen or {}).get("closed_by") or "l'application"
    stamp = (f"Arrêté le {frozen['closed_at']:%d/%m/%Y à %H:%M} par {who} · chiffres figés" if frozen
             else f"<b>Provisoire</b> : chiffres au {as_of:%d/%m/%Y}, mois non arrêté")
    p0 = sheets[0]["params"]
    rule = (f"Ligne = fournisseur × article × jour ouvré attendu ; mois = semaines ISO entières, rattachées au mois de leur mercredi. "
            f"Quantité acquise si complète dans les {p0['band2_days']} jours ouvrés, excédent toléré un ou deux jours de besoin selon la fréquence de livraison. "
            f"Ponctualité : 100 le jour exact, {p0['band1_score']} de 1 à {p0['band1_days']} j ouvrés d'écart, {p0['band2_score']} de {p0['band1_days'] + 1} à {p0['band2_days']} j, 0 ensuite. "
            f"Pondération {p0['weights']['qty']} / {p0['weights']['punctuality']} / {p0['weights']['desadv']}. DESADV : part des BL du mois annoncés par un DESADV traité.")
    story: list = []

    def grid_style(extra: list | None = None) -> TableStyle:
        return TableStyle([("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f7fb")), ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#dfe4ec")),
                           ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#eef1f6")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                           ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5), ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)]
                          + (extra or []))

    def score_cell(v: float | None, bold: bool = True) -> Paragraph:
        s = _n(v)
        return Paragraph(f"<font color='{_tone(v)}'>{'<b>' if bold else ''}{s}{'</b>' if bold else ''}</font>", cell_r)

    # ---- portfolio summary
    if len(sheets) > 1:
        valid = [sh["scores"] for sh in sheets if sh["scores"]["otd"] is not None]
        mean = lambda k: (sum(float(s[k]) for s in valid if s[k] is not None) / len([s for s in valid if s[k] is not None])) if valid else None   # noqa: E731
        story += [Paragraph(f"Arrêté OTD · {label} · portefeuille", h1),
                  Paragraph(f"Ma Routine Appro · {len(sheets)} fournisseur(s) évalué(s){' · approvisionneur ' + planner if planner else ''} · {weeks_txt}", meta),
                  Paragraph(stamp, meta), Spacer(1, 4 * mm)]
        kp = [[Paragraph("OTD portefeuille", head), Paragraph("Quantité", head), Paragraph("Ponctualité", head), Paragraph("DESADV", head), Paragraph("Jours évalués", head), Paragraph("Non livrés", head)],
              [Paragraph(f"<font size='15' color='{_tone(mean('otd'))}'><b>{_n(mean('otd'))}</b></font>", cell), Paragraph(f"<font size='12'>{_n(mean('qty'))}</font>", cell),
               Paragraph(f"<font size='12'>{_n(mean('punctuality'))}</font>", cell), Paragraph(f"<font size='12'>{_n(mean('desadv'))}</font>", cell),
               Paragraph(f"<font size='12'>{sum(sh['scores']['n'] for sh in sheets)}</font>", cell), Paragraph(f"<font size='12'>{sum(sh['scores']['not_delivered'] for sh in sheets)}</font>", cell)]]
        t = Table(kp, colWidths=[W / 6] * 6)
        t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#dfe4ec")), ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#eef1f6")),
                               ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
        story += [t, Spacer(1, 4 * mm), Paragraph("Moyenne des fournisseurs (chacun compte une fois). Une page par fournisseur suit, du plus faible au meilleur.", small), Spacer(1, 2 * mm)]
        rows = [[Paragraph("Fournisseur", head), Paragraph("Approvisionneur", head), Paragraph("Jours", head_r), Paragraph("OTD", head_r), Paragraph("Quantité", head_r),
                 Paragraph("Ponctualité", head_r), Paragraph("DESADV", head_r), Paragraph("Non livrés", head_r), Paragraph("Articles", head_r)]]
        for sh in sheets:
            sc = sh["scores"]
            rows.append([Paragraph(f"<b>{sh['supplier_id']}</b> {sh['name']}", cell), Paragraph(", ".join(sh["planners"]) or "–", cell), Paragraph(str(sc["n"]), cell_r),
                         score_cell(sc["otd"]), score_cell(sc["qty"], False), score_cell(sc["punctuality"], False), score_cell(sc["desadv"], False),
                         Paragraph(f"<font color='{_BAD if sc['not_delivered'] else _MUTED}'>{sc['not_delivered']}</font>", cell_r), Paragraph(str(sh["articles"]), cell_r)])
        t = LongTable(rows, colWidths=[W * 0.29, W * 0.14, W * 0.07, W * 0.09, W * 0.09, W * 0.10, W * 0.08, W * 0.07, W * 0.07], repeatRows=1)
        t.setStyle(grid_style())
        story += [t, Spacer(1, 4 * mm), Paragraph(rule, small), PageBreak()]

    # ---- one page per supplier
    for n, sh in enumerate(sheets):
        sc, pts = sh["scores"], sh["scores"]["points"]
        w = sh["params"]["weights"]
        story += [Paragraph(f"OTD · {label} · {sh['supplier_id']} {sh['name']}", h1),
                  Paragraph(f"Approvisionneur(s) : {', '.join(sh['planners']) or '–'} · livraison le {_weekdays_fr(sh['delivery_weekdays'])} · excédent toléré "
                            f"{sh['tolerance_factor']} jour(s) de besoin · {weeks_txt} · {sc['n']} jour(s) attendu(s), {sc['bls']} BL", meta),
                  Paragraph(stamp, meta), Spacer(1, 3 * mm)]
        formula = (f"<font size='20' color='{_tone(sc['otd'])}'><b>{_n(sc['otd'])}</b></font> &nbsp;=&nbsp; <b>{_n(sc['qty'])}</b> × {w['qty']} % Quantité "
                   f"&nbsp;+&nbsp; <b>{_n(sc['punctuality'])}</b> × {w['punctuality']} % Ponctualité &nbsp;+&nbsp; <b>{_n(sc['desadv'])}</b> × {w['desadv']} % DESADV")
        legend = (f"Quantité {_n(pts['qty'])} / {_n(pts['max']['qty'])} · Ponctualité {_n(pts['punctuality'])} / {_n(pts['max']['punctuality'])} · "
                  f"DESADV {_n(pts['desadv'])} / {_n(pts['max']['desadv'])}" + (f" · <font color='{_BAD}'><b>{_n(pts['lost'])} point(s) perdu(s)</b></font>" if pts["lost"] else ""))
        box = Table([[Paragraph(formula, big)], [_points_drawing(sh, W - 12)], [Paragraph(legend, small)]], colWidths=[W])
        box.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#dfe4ec")), ("LEFTPADDING", (0, 0), (-1, -1), 6), ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                                 ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        story.append(box)
        story.append(Spacer(1, 3 * mm))
        q, pu = sh["quantity"], sh["punctuality"]
        bands = " · ".join(f"{b['label']} ({b['score']:.0f}) : <b>{b['count']}</b>" for b in pu["bands"])
        desadv_ok = sum(1 for b in sh["desadv"] if b["ok"])
        three = Table([[Paragraph(f"QUANTITÉ · <font color='{_tone(sc['qty'])}'><b>{_n(sc['qty'])}</b></font>", body),
                        Paragraph(f"PONCTUALITÉ · <font color='{_tone(sc['punctuality'])}'><b>{_n(sc['punctuality'])}</b></font>", body),
                        Paragraph(f"DESADV · <font color='{_tone(sc['desadv'])}'><b>{_n(sc['desadv'])}</b></font>", body)],
                       [Paragraph(f"{q['complete']} jour(s) sur {q['total']} complet(s) dans les {sh['params']['band2_days']} j ouvrés"
                                  f"{' · ' + str(q['not_delivered']) + ' non livré(s)' if q['not_delivered'] else ''}{' · ' + str(q['excess']) + ' excédent(s)' if q['excess'] else ''}.", cell),
                        Paragraph(f"{bands}. Retard moyen {'+' if (pu['delay_mean'] or 0) > 0 else ''}{_n(pu['delay_mean'], 1)} j.", cell),
                        Paragraph(f"{desadv_ok} BL sur {len(sh['desadv'])} annoncé(s) par un DESADV traité.", cell)]], colWidths=[W / 3] * 3)
        three.setStyle(TableStyle([("BOX", (0, 0), (0, -1), 0.6, colors.HexColor("#dfe4ec")), ("BOX", (1, 0), (1, -1), 0.6, colors.HexColor("#dfe4ec")), ("BOX", (2, 0), (2, -1), 0.6, colors.HexColor("#dfe4ec")),
                                   ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
        story += [three, Spacer(1, 3 * mm)]
        # the evaluated days
        rows = [[Paragraph("Jour attendu", head), Paragraph("Article", head), Paragraph("Attendu", head_r), Paragraph("Complet le", head), Paragraph("Écart", head_r),
                 Paragraph("Quantité", head_r), Paragraph("Ponctualité", head_r), Paragraph("Pourquoi", head)]]
        tints = []
        for i, x in enumerate(sh["lines"], start=1):
            delta = "–" if x["delta"] is None else "le jour" if x["delta"] == 0 else f"{'+' if x['delta'] > 0 else '−'}{abs(x['delta'])} j"
            dcol = _MUTED if x["delta"] is None else "#101828" if x["delta"] == 0 else _tone(x["punct_score"])
            rows.append([Paragraph(_wd(x["date"]), cell), Paragraph(f"<b>{x['article_id']}</b> {x['designation'][:34]}", cell), Paragraph(_fmt_qty(float(x["expected"]), x["unit"]), cell_r),
                         Paragraph(_fr(x["completed"]) if x["completed"] else f"<font color='{_MUTED}'>{x['status']}</font>", cell),
                         Paragraph(f"<font color='{dcol}'><b>{delta}</b></font>", cell_r), score_cell(x["qty_score"]), score_cell(x["punct_score"]), Paragraph(x["reason"], cell)])
            if x["punct_score"] is not None and x["punct_score"] < 100:
                tints.append(("BACKGROUND", (6, i), (6, i), _bg(x["punct_score"])))
            if x["qty_score"] is not None and x["qty_score"] < 100:
                tints.append(("BACKGROUND", (5, i), (5, i), _bg(x["qty_score"])))
        t = LongTable(rows, colWidths=[W * 0.10, W * 0.20, W * 0.08, W * 0.09, W * 0.07, W * 0.08, W * 0.09, W * 0.29], repeatRows=1)
        t.setStyle(grid_style(tints))
        story += [Paragraph("Le calcul, jour attendu par jour attendu", h2), t]
        drawing = _cumul_drawing(sh, W, 42 * mm)
        if drawing is not None:
            story += [KeepTogether([Paragraph("Cumul attendu et cumul reçu sur le mois", h2), drawing])]
        story += [Spacer(1, 2 * mm), Paragraph(rule, small)]
        if n < len(sheets) - 1:
            story.append(PageBreak())

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor(_MUTED))
        canvas.drawString(14 * mm, 8 * mm, f"Arrêté OTD · {label} · {'figé' if frozen else 'provisoire'} · Ma Routine Appro")
        canvas.drawRightString(A4[0] - 14 * mm, 8 * mm, f"page {d.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


# =============================================================================
# Planning transport (docs § 9 ter) : the printable planning and the sheet of a MAF shuttle
# =============================================================================
_BLUE, _BLUE_BG, _GREY_BG = "#1d3fb5", "#e8eefc", "#f3f4f6"
_WD = ("Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche")
_WD_SHORT = ("Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim")


def _short_name(t: dict, maf_short: str = "HAMBACH") -> str:
    if t["mode"] == "maf":
        return maf_short + (" A/R" if t.get("return_trip") else "")
    first = (t.get("supplier_name") or t["supplier_id"]).split()[0] if (t.get("supplier_name") or t["supplier_id"]).split() else t["supplier_id"]
    suffix = {"ev": " · EV", "ponctuel": " · ponctuel"}.get(t["mode"], "")
    return first[:12] + suffix


def _slot_floor(time: str, slots: list[str]) -> str | None:
    if not time or len(time) < 5:
        return None
    m = int(time[:2]) * 60 + int(time[3:5])
    first = int(slots[0][:2]) * 60 + int(slots[0][3:5])
    last = int(slots[-1][:2]) * 60 + int(slots[-1][3:5])
    step = (int(slots[1][:2]) * 60 + int(slots[1][3:5]) - first) if len(slots) > 1 else 30
    if m < first:
        return "before"
    if m >= last + step:
        return "after"
    return slots[min((m - first) // step, len(slots) - 1)]


def planning_pdf(board: dict, user_label: str, people: dict[str, str] | None = None) -> bytes:
    """A4 landscape : for each of the two weeks, one page docks × days (trucks per cell in big figures, « n à vous »,
    the suppliers at their hour, totals per dock and per day) then one page per working day (the agenda of the docks :
    one row per slot, « < 7h » and « ≥ 16h » rows, the identifier and the supplier of every truck).  The planner's
    trucks and the shuttles are blue, a truck to check red, a cancelled truck struck ; no quantity, no order number."""
    from reportlab.platypus import PageBreak, Table

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=10 * mm, rightMargin=10 * mm, topMargin=10 * mm, bottomMargin=12 * mm,
                            title=f"Planning transport {board['start']:%d/%m/%Y}", author="Ma Routine Appro")
    W = landscape(A4)[0] - 20 * mm
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Title"], fontSize=14, leading=17, alignment=0, spaceAfter=1)
    meta = ParagraphStyle("meta", parent=styles["Normal"], fontSize=8, leading=10, textColor=colors.HexColor("#475467"))
    head = ParagraphStyle("head", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=8, leading=10)
    head_c = ParagraphStyle("head_c", parent=head, alignment=1)
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=6.8, leading=8.4)
    tot = ParagraphStyle("tot", parent=styles["Normal"], fontSize=11, leading=13, fontName="Helvetica-Bold", alignment=1)
    blk = ParagraphStyle("blk", parent=styles["Normal"], fontSize=6.6, leading=8)
    stamp = f"Ma Routine Appro · édité le {dt.datetime.now():%d/%m/%Y %H:%M} par {user_label}"
    blue = (f"bleu = camions du carnet {board['planner']}" if board.get("planner") else "bleu = vos camions") + " · jaune = navettes, retours EV et camions ponctuels posés à la main · • = touché à la main"
    docks: list[str] = board["docks"]
    slots: list[str] = board["slots"]
    trucks = [t for t in board["transports"]]
    planned = [t for t in trucks if t["status"] == "planned"]
    weeks: list[list[dt.date]] = []
    for d in board["days"]:
        if not weeks or (d - weeks[-1][0]).days >= 7 or d.isocalendar()[1] != weeks[-1][0].isocalendar()[1]:
            weeks.append([d])
        else:
            weeks[-1].append(d)
    story: list = []

    def colour(t: dict) -> str:
        if t["status"] != "planned":
            return "#9ca3af"
        if t.get("mine"):
            if t.get("check_reason"):
                return "#b42318"
            return "#b54708" if t["mode"] in ("maf", "ev", "ponctuel") else _BLUE
        return "#374151"

    def touched(t: dict) -> str:
        return " •" if t["status"] == "planned" and t["mode"] not in ("maf", "ev", "ponctuel") and (t.get("pinned") or t.get("note")) else ""

    def week_page(days: list[dt.date]) -> None:
        wk = days[0].isocalendar()[1]
        n_week = sum(1 for t in planned if t["date"] in days)
        story.append(Paragraph(f"Planning transport · S{wk:02d} · {days[0]:%d/%m} → {days[-1]:%d/%m/%Y}", h1))
        story.append(Paragraph(f"{n_week} camion(s) · {board['capacity']} par quai et par créneau · {blue} · rouge = à vérifier", meta))
        story.append(Spacer(1, 3 * mm))
        rows = [[Paragraph("Quai", head)] + [Paragraph(f"{_WD_SHORT[d.weekday()]} {d:%d/%m}<br/><font size='7' color='#475467'>{sum(1 for t in planned if t['date'] == d)} camion(s)</font>", head_c) for d in days]
                + [Paragraph("Semaine", head_c)]]
        heights = [11 * mm]
        avail = (landscape(A4)[1] - 22 * mm - 30 * mm) - 11 * mm - 9 * mm
        for dock in docks:
            line = [Paragraph(f"<b>{dock}</b>", head)]
            for d in days:
                closed = dock in board["closed_docks"].get(d.isoformat(), [])
                here = sorted((t for t in planned if t["date"] == d and dock in t["docks"]), key=lambda t: (t["time"], t["transport_id"]))
                mine_n = sum(1 for t in here if t.get("mine"))
                if closed and not here:
                    line.append(Paragraph("<font color='#475467'>fermé</font>", cell))
                    continue
                items = " · ".join(f"<font color='{colour(t)}'>{t['time']} {_short_name(t)}{touched(t)}</font>" for t in here)
                line.append(Paragraph(f"<font size='15'><b>{len(here)}</b></font> <font size='6.5' color='#475467'>camion(s)</font>"
                                      + (f" <font size='7' color='{_BLUE}'><b>{mine_n} {'du carnet' if board.get('planner') else 'à vous'}</b></font>" if mine_n else "") + f"<br/>{items}", cell))
            line.append(Paragraph(str(sum(1 for t in planned if t["date"] in days and dock in t["docks"])), tot))
            rows.append(line)
            heights.append(avail * (0.55 if dock == docks[0] else 0.45 / max(len(docks) - 1, 1)))
        rows.append([Paragraph("Total", head)] + [Paragraph(str(sum(1 for t in planned if t["date"] == d)), tot) for d in days] + [Paragraph(str(n_week), tot)])
        heights.append(9 * mm)
        widths = [16 * mm] + [(W - 16 * mm - 18 * mm) / len(days)] * len(days) + [18 * mm]
        tbl = Table(rows, colWidths=widths, rowHeights=heights)
        style = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c9d1de")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f5fa")),
                 ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#f3f5fa")), ("BACKGROUND", (-1, 1), (-1, -1), colors.HexColor("#f3f5fa")), ("BACKGROUND", (0, -1), (-1, -1), colors.HexColor("#f3f5fa")),
                 ("VALIGN", (-1, 1), (-1, -1), "MIDDLE"), ("VALIGN", (0, -1), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
        for j, d in enumerate(days, start=1):
            if d == board["as_of"]:
                style.append(("BACKGROUND", (j, 0), (j, 0), colors.HexColor(_BLUE_BG)))
            for i, dock in enumerate(docks, start=1):
                if dock in board["closed_docks"].get(d.isoformat(), []):
                    style.append(("BACKGROUND", (j, i), (j, i), colors.HexColor("#ececef")))
        tbl.setStyle(TableStyle(style))
        story.append(tbl)

    def day_page(d: dt.date) -> None:
        here = [t for t in trucks if t["date"] == d]
        n_day = sum(1 for t in here if t["status"] == "planned")
        story.append(Paragraph(f"{_WD[d.weekday()]} {d:%d/%m/%Y} · agenda des quais", h1))
        story.append(Paragraph(f"{n_day} camion(s) · {blue} · rouge = à vérifier · barré = annulé", meta))
        story.append(Spacer(1, 2 * mm))
        keys = ["before"] + slots + ["after"]
        labels = {"before": f"< {int(slots[0][:2])}h", "after": f"≥ {int(slots[-1][:2]) + (1 if slots[-1][3:] != '00' else 0)}h" if slots[-1][3:] == "30" else f"≥ {int(slots[-1][:2]) + 1}h"}
        rows = [[Paragraph("", head)] + [Paragraph(f"{dock}<br/><font size='7' color='#475467'>{sum(1 for t in here if t['status'] == 'planned' and dock in t['docks'])} camion(s)</font>", head_c) for dock in docks]]
        for k in keys:
            line = [Paragraph(labels.get(k, k), ParagraphStyle("t", parent=cell, textColor=colors.HexColor("#475467"), fontName="Helvetica-Bold" if k.endswith(":00") or k in labels else "Helvetica"))]
            for dock in docks:
                xs = sorted((t for t in here if dock in t["docks"] and _slot_floor(t["time"], slots) == k), key=lambda t: (t["time"], t["transport_id"]))
                parts = []
                for t in xs:
                    txt = f"<b>{t['transport_id']}</b> {_short_name(t)}{touched(t)}" + (f" {t['time']}" if k in labels else "")
                    if t["status"] != "planned":
                        txt = f"<strike>{txt}</strike> annulé"
                    parts.append(f"<font color='{colour(t)}'>{txt}</font>")
                line.append(Paragraph(" &nbsp;|&nbsp; ".join(parts), blk))
            rows.append(line)
        widths = [14 * mm] + [(W - 14 * mm) / len(docks)] * len(docks)
        avail = landscape(A4)[1] - 22 * mm - 24 * mm
        tbl = Table(rows, colWidths=widths, rowHeights=[9 * mm] + [(avail - 9 * mm) / len(keys)] * len(keys))
        style = [("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d5dae3")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f5fa")),
                 ("BACKGROUND", (0, 1), (0, -1), colors.HexColor("#f8f9fb")), ("LEFTPADDING", (0, 0), (-1, -1), 2), ("RIGHTPADDING", (0, 0), (-1, -1), 2), ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]
        for j, dock in enumerate(docks, start=1):
            if dock in board["closed_docks"].get(d.isoformat(), []):
                style.append(("BACKGROUND", (j, 1), (j, -1), colors.HexColor("#ececef")))
            for i, k in enumerate(keys, start=1):
                n = len([t for t in here if t["status"] == "planned" and dock in t["docks"] and _slot_floor(t["time"], slots) == k])
                if n > board["capacity"]:
                    style.append(("BACKGROUND", (j, i), (j, i), colors.HexColor("#fff4e5")))
        tbl.setStyle(TableStyle(style))
        story.append(tbl)

    pages = 0
    for days in weeks:
        if pages:
            story.append(PageBreak())
        week_page(days)
        pages += 1
        for d in days:
            story.append(PageBreak())
            day_page(d)
            pages += 1

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#667085"))
        canvas.drawString(10 * mm, 6.5 * mm, stamp)
        canvas.drawRightString(landscape(A4)[0] - 10 * mm, 6.5 * mm, f"page {d.page} / {pages}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()


def shuttle_pdf(t: dict, people: dict[str, str] | None = None) -> bytes:
    """A4 portrait, one page : the shuttle (identifier, day, hour, docks of both legs, carrier, note, author), the
    outward lines (ERP orders with their supplier, typed lines) and the return lines, signature boxes."""
    from reportlab.platypus import Table

    people = people or {}
    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=16 * mm,
                            title=f"Navette MAF {t['transport_id']}", author="Ma Routine Appro")
    W = A4[0] - 32 * mm
    styles = getSampleStyleSheet()
    small = ParagraphStyle("small", parent=styles["Normal"], fontSize=8, leading=10, textColor=colors.HexColor("#475467"))
    big = ParagraphStyle("big", parent=styles["Normal"], fontSize=24, leading=28, fontName="Courier-Bold")
    body = ParagraphStyle("body", parent=styles["Normal"], fontSize=9.5, leading=12.5)
    body_r = ParagraphStyle("body_r", parent=body, alignment=TA_RIGHT)
    sec = ParagraphStyle("sec", parent=styles["Normal"], fontSize=10.5, leading=13, fontName="Helvetica-Bold", spaceBefore=6, spaceAfter=3)
    head = ParagraphStyle("head", parent=styles["Normal"], fontSize=7.5, leading=9.5, fontName="Helvetica-Bold", textColor=colors.HexColor("#344054"))
    head_r = ParagraphStyle("head_r", parent=head, alignment=TA_RIGHT)
    day: dt.date = t["date"]
    who = people.get((t.get("created_by") or "").lower(), (t.get("created_by") or "").split("@")[0])
    created = t.get("created_at")
    when = f" · {created:%d/%m/%Y %H:%M}" if isinstance(created, dt.datetime) else ""
    out_docks, back_docks = " / ".join(t["docks"]), " / ".join(t.get("return_docks") or t["docks"])
    header = Table([[Paragraph("NAVETTE MAF HAMBACH · FICHE DE TRANSPORT", small), Paragraph(f"<b>{_WD[day.weekday()]} {day:%d/%m/%Y} · {t['time']}</b>", body_r)],
                    [Paragraph(t["transport_id"], big), Paragraph(f"Aller · quai {out_docks}" + (f"<br/>Retour · quai {back_docks}" if t.get("return_trip") else "<br/>Sans retour"), body_r)]],
                   colWidths=[W * 0.6, W * 0.4])
    header.setStyle(TableStyle([("LINEBELOW", (0, -1), (-1, -1), 1.2, colors.black), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, -1), (-1, -1), 6)]))
    story = [header, Spacer(1, 3 * mm),
             Paragraph(f"Transporteur : <b>{t.get('carrier') or 'navette interne'}</b>" + (f" · Note : {t['note']}" if t.get("note") else "") + f" · Créée par <b>{who}</b>{when}", body),
             Spacer(1, 2 * mm)]
    grid = TableStyle([("LINEBELOW", (0, 0), (-1, 0), 1, colors.black), ("LINEBELOW", (0, 1), (-1, -1), 0.3, colors.HexColor("#d0d5dd")), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                       ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4), ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4)])
    story.append(Paragraph(f"→ ALLER · MAF Hambach → usine · {t['time']} · quai {out_docks}", sec))
    rows = [[Paragraph("Fournisseur", head), Paragraph("Article", head), Paragraph("Désignation", head), Paragraph("Quantité", head_r)]]
    for o in t["orders"]:
        rows.append([Paragraph(o.get("supplier_name") or o.get("supplier_id") or "", body), Paragraph(f"<b>{o['article_id']}</b>", body), Paragraph(o.get("designation") or "", body),
                     Paragraph(f"{_fmt_qty(float(o['qty']), o.get('unit') or '')} {o.get('unit') or ''}".strip(), body_r)])
    for ln in t["lines"]:
        if ln["leg"] == "aller":
            rows.append([Paragraph("<font color='#667085'>saisi</font>", body), Paragraph(f"<b>{ln['article']}</b>", body), Paragraph(ln.get("designation") or "", body), Paragraph(ln.get("qty") or "", body_r)])
    if len(rows) == 1:
        rows.append([Paragraph("<font color='#667085'>aucune ligne</font>", body), "", "", ""])
    tbl = Table(rows, colWidths=[W * 0.26, W * 0.22, W * 0.36, W * 0.16])
    tbl.setStyle(grid)
    story += [tbl, Paragraph(f"Lignes aller : <b>{len(rows) - 1 if rows[1][1] else 0}</b>", body_r)]
    if t.get("return_trip"):
        story.append(Paragraph(f"← RETOUR · usine → MAF Hambach · quai {back_docks} · après déchargement", sec))
        rows = [[Paragraph("Article", head), Paragraph("Désignation", head), Paragraph("Quantité", head_r)]]
        for ln in t["lines"]:
            if ln["leg"] == "retour":
                rows.append([Paragraph(f"<b>{ln['article']}</b>", body), Paragraph(ln.get("designation") or "", body), Paragraph(ln.get("qty") or "", body_r)])
        if len(rows) == 1:
            rows.append([Paragraph("<font color='#667085'>aucune ligne</font>", body), "", ""])
        tbl = Table(rows, colWidths=[W * 0.3, W * 0.5, W * 0.2])
        tbl.setStyle(grid)
        story += [tbl, Paragraph(f"Lignes retour : <b>{len(rows) - 1 if rows[1][1] else 0}</b>", body_r)]
    story.append(Spacer(1, 8 * mm))
    boxes = Table([[Paragraph("Chauffeur · nom et signature", small), Paragraph("Quai usine · heure réelle, visa", small), Paragraph("MAF Hambach · visa", small)]],
                  colWidths=[W / 3] * 3, rowHeights=[22 * mm])
    boxes.setStyle(TableStyle([("BOX", (0, 0), (0, 0), 0.6, colors.HexColor("#98a2b3")), ("BOX", (1, 0), (1, 0), 0.6, colors.HexColor("#98a2b3")), ("BOX", (2, 0), (2, 0), 0.6, colors.HexColor("#98a2b3")),
                               ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 4)]))
    story.append(boxes)

    def footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(colors.HexColor("#667085"))
        canvas.drawString(16 * mm, 8 * mm, f"L'identifiant {t['transport_id']} est demandé à l'entrée · à présenter à l'aller" + (" et au retour" if t.get("return_trip") else ""))
        canvas.drawRightString(A4[0] - 16 * mm, 8 * mm, f"Ma Routine Appro · généré le {dt.datetime.now():%d/%m/%Y} · page {d.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)
    return buf.getvalue()
