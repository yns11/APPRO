# -*- coding: utf-8 -*-
"""Génère le pense-bête de l'approvisionneur, en deux formes :

* ``docs/pense_bete_approvisionneur.html`` : une page autonome (règles, marqueurs, couleurs, exemples et
  graphiques SVG) ;
* ``backend/appro/help/pense_bete.json`` : les mêmes sections, une page par section, servies par l'application
  (bouton d'aide de l'en-tête, modifiables sur place par un administrateur).

Les graphiques du CBN sont **calculés par le moteur** sur un petit jeu de données : ce que le dessin montre est
ce que l'application propose.  Lancer depuis la racine du dépôt : ``python scripts/pense_bete.py``.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import pathlib
import sys

RACINE = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RACINE / "backend"))

from appro.engine import run_mrp  # noqa: E402
from appro.engine.calendar import iso_week_label, iso_week_monday  # noqa: E402
from appro.engine.models import (  # noqa: E402
    Article,
    BomLine,
    ConsumptionLine,
    Dataset,
    EngineParams,
    PdpLine,
    Program,
    StockSnapshot,
    Supplier,
    SupplierLink,
)

OUT_HTML = RACINE / "docs" / "pense_bete_approvisionneur.html"
OUT_JSON = RACINE / "backend" / "appro" / "help" / "pense_bete.json"
VERSION = "21 octobre 2026"
D = dt.timedelta

# ----------------------------------------------------------------------------- charts
W, H = 760, 300
PAD_L, PAD_R, PAD_T, PAD_B = 46, 16, 44, 44
DAYS = ["lun", "mar", "mer", "jeu", "ven"]
MONDAY0 = dt.date(2026, 10, 19)     # lundi de la semaine « S43 » des exemples


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def simulate(stock0: float, demand: float, deliveries: dict[int, float], n: int, start: int = 0,
             allow_negative: bool = False) -> tuple[list[float | None], list[float]]:
    """Stock en fin de jour à partir du jour ``start`` (``stock0`` = stock de la veille de ``start``) ; les jours
    avant ``start`` valent None. Le stock n'est jamais négatif (manque cumulé −Σ), sauf ``allow_negative``
    pour la ligne « sans livraison »."""
    stock: list[float | None] = [None] * start
    short: list[float] = [0.0] * start
    s, cum = stock0, 0.0
    for j in range(start, n):
        s = s + deliveries.get(j, 0.0) - demand
        if s < 0 and not allow_negative:
            cum += -s
            s = 0.0
        elif s >= 0:
            cum = 0.0
        stock.append(s)
        short.append(cum)
    return stock, short


def engine_scenario(as_of: dt.date, stock: float, suppliers: list[Supplier], links: list[SupplierLink], weeks: int = 4,
                    margin: int = 1) -> dict:
    """Un article (couverture cible 5 j ouvrés, besoin 100 par jour ouvré), le stock connu la veille d'« aujourd'hui »,
    calculé par le moteur. Renvoie les séries du graphique indexées en jours ouvrés depuis le lundi de la semaine
    d'aujourd'hui : stock projeté, stock « sans livraison » (pointillé), propositions, jour d'aujourd'hui."""
    monday = iso_week_monday(as_of)
    # la semaine en cours : la consommation réelle des jours passés est déclarée (100 / jour), le reliquat du PDP
    # se répartit donc à 100 par jour ouvré restant
    past = [ConsumptionLine("A1", monday + D(days=k), 100.0) for k in range((as_of - monday).days) if (monday + D(days=k)).weekday() < 5]
    ds = Dataset(articles=[Article("A1", "Article", "PCE", coverage_target_days=5)], suppliers=suppliers, links=links,
                 programs=[Program("P1", "Programme")], bom=[BomLine("P1", "A1", 2.0)],
                 pdp=[PdpLine("P1", monday + D(weeks=k), 250.0) for k in range(-2, weeks + 2)],
                 consumption=past, orders=[], receipts=[], stock=[StockSnapshot("A1", as_of - D(days=1), stock)])
    ar = run_mrp(ds, EngineParams(as_of=as_of, horizon_days=7 * weeks + 7, lead_margin_days=margin)).articles["A1"]
    n = 5 * weeks

    def j_of(d: dt.date) -> int | None:
        k = (d - monday).days
        w, wd = divmod(k, 7)
        return w * 5 + wd if 0 <= w < weeks and wd < 5 else None

    j_today = j_of(as_of)
    stock_s: list[float | None] = [None] * n
    ghost: list[float | None] = [None] * n
    s = float(stock)
    for i, d in enumerate(ar.dates):
        j = j_of(d)
        if d < as_of or j is None:
            continue
        stock_s[j] = float(ar.stock_plan[i])
        s -= float(ar.demand[i])
        ghost[j] = s
    bars = []
    for p in ar.proposals:
        j = j_of(p.delivery_date)
        if j is None:
            continue
        mark = " !" if p.urgent else " ◇" if p.off_standard else ""
        bars.append((j, float(p.qty), "b-urgent" if p.urgent else "b-cbn", f"{p.qty:,.0f}".replace(",", " ") + mark))
    weeks_lbl = [iso_week_label(monday + D(weeks=k)).replace("-W", "S").split("S")[-1] for k in range(weeks)]
    return {"weeks": [f"S{w}" for w in weeks_lbl], "stock": stock_s, "ghost": ghost, "bars": bars, "today": j_today, "ar": ar, "j_of": j_of}


def chart(cid: str, title: str, subtitle: str, weeks: list[str], series: list[tuple[str, list[float | None], str]],
          bars: list[tuple[int, float, str, str]], ymax: float, lines: list[tuple[float, str, str]] = (),
          band: tuple[int, int, str] | None = None, marks: list[tuple[int, float, str, str]] = (),
          today: int | None = None, shortage: list[float] | None = None, caption: str = "", ymin: float = 0.0) -> str:
    """Un graphique : n = 5 jours ouvrés × semaines. series = (classe, valeurs, style 'solid'|'dotted').
    bars = (jour, quantité, classe, libellé). lines = (y, classe, libellé). marks = (jour, y, texte, classe)."""
    n = 5 * len(weeks)
    iw = W - PAD_L - PAD_R
    ih = H - PAD_T - PAD_B
    x = lambda j: PAD_L + (j + 0.5) * iw / n  # noqa: E731
    y = lambda v: PAD_T + ih - ((v - ymin) / (ymax - ymin)) * ih  # noqa: E731
    out = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-labelledby="{cid}-t" xmlns="http://www.w3.org/2000/svg">',
           f'<title id="{cid}-t">{esc(title)}</title>']
    out.append(f'<text class="c-title" x="{PAD_L}" y="18">{esc(title)}</text>')
    out.append(f'<text class="c-sub" x="{PAD_L}" y="34">{esc(subtitle)}</text>')
    if band:
        a, b, lbl = band
        out.append(f'<rect class="c-band" x="{x(a) - iw / n / 2:.1f}" y="{PAD_T}" width="{(b - a + 1) * iw / n:.1f}" height="{ih}" />')
        out.append(f'<text class="c-band-l" x="{x(a) - iw / n / 2 + 6:.1f}" y="{PAD_T + 14}">{esc(lbl)}</text>')
    for k in range(0, 5):
        v = ymax * k / 4
        out.append(f'<line class="c-grid" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(v):.1f}" y2="{y(v):.1f}" />')
        out.append(f'<text class="c-tick" x="{PAD_L - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{int(v)}</text>')
    if ymin < 0:
        out.append(f'<line class="c-zero" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(0):.1f}" y2="{y(0):.1f}" />')
        out.append(f'<text class="c-tick" x="{PAD_L - 6}" y="{y(ymin) + 4:.1f}" text-anchor="end">{int(ymin)}</text>')
    for w, wk in enumerate(weeks):
        x0 = PAD_L + w * iw / len(weeks)
        if w:
            out.append(f'<line class="c-sep" x1="{x0:.1f}" x2="{x0:.1f}" y1="{PAD_T}" y2="{PAD_T + ih}" />')
        out.append(f'<text class="c-week" x="{x0 + 4:.1f}" y="{H - 8}">{esc(wk)}</text>')
        for d in range(5):
            out.append(f'<text class="c-day" x="{x(w * 5 + d):.1f}" y="{PAD_T + ih + 14}" text-anchor="middle">{DAYS[d]}</text>')
    out.append(f'<line class="c-sep" x1="{PAD_L}" x2="{W - PAD_R}" y1="{PAD_T + ih}" y2="{PAD_T + ih}" />')
    if today is not None:
        out.append(f'<line class="c-today" x1="{x(today):.1f}" x2="{x(today):.1f}" y1="{PAD_T}" y2="{PAD_T + ih}" />')
        ty = PAD_T + 28 if band else PAD_T + ih - 6
        out.append(f'<text class="c-today-l" x="{x(today) + 4:.1f}" y="{ty}">aujourd\'hui</text>')
    for v, cls, lbl in lines:
        out.append(f'<line class="c-line {cls}" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(v):.1f}" y2="{y(v):.1f}" />')
        out.append(f'<text class="c-line-l {cls}" x="{W - PAD_R - 4}" y="{y(v) - 4:.1f}" text-anchor="end">{esc(lbl)}</text>')
    if shortage:
        for j, c in enumerate(shortage):
            if c > 0:
                out.append(f'<rect class="c-short" x="{x(j) - iw / n / 2 + 1:.1f}" y="{y(0) - 10}" width="{iw / n - 2:.1f}" height="10" />')
                out.append(f'<text class="c-short-l" x="{x(j):.1f}" y="{y(0) + 14:.1f}" text-anchor="middle">−Σ{int(c)}</text>')
    bw = max(6.0, iw / n * 0.42)
    for j, qty, cls, lbl in bars:
        out.append(f'<rect class="c-bar {cls}" x="{x(j) - bw / 2:.1f}" y="{y(qty):.1f}" width="{bw:.1f}" height="{y(0) - y(qty):.1f}" rx="2" />')
        if lbl:
            out.append(f'<text class="c-bar-l {cls}" x="{x(j):.1f}" y="{y(qty) - 5:.1f}" text-anchor="middle">{esc(lbl)}</text>')
    for cls, vals, style in series:
        pts = []
        for j, v in enumerate(vals):
            if v is None:
                continue
            if v < ymin:                      # la ligne « sans livraison » s'arrête au bas du graphique
                pts.append(f"{x(j):.1f},{y(ymin):.1f}")
                break
            pts.append(f"{x(j):.1f},{y(min(v, ymax)):.1f}")
        out.append(f'<polyline class="c-series {cls} {style}" points="{" ".join(pts)}" />')
    for j, v, txt, cls in marks:
        out.append(f'<circle class="c-mark {cls}" cx="{x(j):.1f}" cy="{y(v):.1f}" r="4" />')
        anchor = "end" if j > n * 0.55 else "start"
        dx = -8 if anchor == "end" else 8
        dy = 18 if v < 0 else -10 if v > ymax * 0.12 else -14
        out.append(f'<text class="c-mark-l {cls}" x="{x(j) + dx:.1f}" y="{y(v) + dy:.1f}" text-anchor="{anchor}">{esc(txt)}</text>')
    out.append("</svg>")
    fig = f'<figure class="fig">{"".join(out)}'
    if caption:
        fig += f'<figcaption>{caption}</figcaption>'
    return fig + "</figure>"


LINES = [(500.0, "l-target", "cible 500"), (425.0, "l-trigger", "déclenchement 425")]


def monday_supplier(lead: int, strict: bool = False) -> Supplier:
    return Supplier("A", "Fournisseur A", delivery_weekdays=frozenset({1}), lead_time_days=lead, strict_days=strict)


def charts() -> dict[str, str]:
    c: dict[str, str] = {}
    link = [SupplierLink("A1", "A", moq=0, pack_qty=0)]
    # ---- 1. déclenchement et cible : aujourd'hui lundi S43, 1 000 dimanche soir, fournisseur du lundi, délai 5, marge 1
    s1 = engine_scenario(MONDAY0, 1000.0, [monday_supplier(5)], link)
    j5 = s1["j_of"](MONDAY0 + D(days=7))
    c["trigger"] = chart(
        "c1", "Déclenchement et cible", "aujourd'hui lundi S43 · 1 000 dimanche soir, besoin 100 / jour ouvré · cible 5 j = 500 · déclenchement sous 85 % = 425 · fournisseur du lundi, délai 5, marge 1",
        s1["weeks"], [("s-appro", s1["stock"], "solid"), ("s-ghost", s1["ghost"], "dotted")], s1["bars"], 1000.0, LINES,
        marks=[(j5, 400.0, "sans livraison : 400 < 425 → proposition le lundi", "m-trigger"), (1, 800.0, "date limite = lun. S44 − 4 j.o. = mar. S43", "m-order")],
        today=s1["today"], ymin=-100.0,
        caption="Calculé par le moteur. La proposition se pose sur le jour standard (lundi) avant le besoin et regroupe les besoins jusqu'au lundi suivant : le stock redescend à la cible (500) la veille de la livraison suivante. Pointillé : le stock sans la proposition.")
    # ---- 2. plancher : aujourd'hui mercredi S43, 250 mardi soir, fournisseur du lundi, délai 4, marge 1 → tenable dès lundi S44
    s2 = engine_scenario(MONDAY0 + D(days=2), 250.0, [monday_supplier(4)], link)
    c["floor"] = chart(
        "c2", "Plancher urgent (!) puis cible pleine", "aujourd'hui mercredi S43 · 250 mardi soir, besoin 100 / jour · fournisseur du lundi, délai 4, marge 1 · lundi S44 = première livraison tenable (date limite aujourd'hui)",
        s2["weeks"], [("s-appro", s2["stock"], "solid"), ("s-ghost", s2["ghost"], "dotted")], s2["bars"], 1200.0, LINES,
        band=(2, 4, "non tenable : plancher"), today=s2["today"], ymin=-100.0,
        marks=[(4, -50.0, "sans rien : −50 vendredi", "m-trigger")],
        caption="Calculé par le moteur. Mercredi 150, jeudi 50 : vendredi serait à −50. Aucune livraison tenable avant : plancher urgent de 50 vendredi, le jour du besoin (◇ : pas un lundi). Lundi S44, première livraison tenable : cible pleine, la livraison couvre la semaine (1 000), puis 500 par semaine. Un fournisseur à jours stricts n'aurait rien vendredi : le manque resterait.")
    # ---- 3. hors jour standard : aujourd'hui jeudi S43, 550 mercredi soir, fournisseur du lundi, délai 5, marge 1 → tenable dès mercredi S44
    s3 = engine_scenario(MONDAY0 + D(days=3), 550.0, [monday_supplier(5)], link)
    c["offday"] = chart(
        "c3", "Hors jour standard (◇)", "aujourd'hui jeudi S43 · 550 mercredi soir, besoin 100 / jour · fournisseur du lundi, délai 5, marge 1 · tenable à partir de mercredi S44 (jeudi + 4 j.o.)",
        s3["weeks"], [("s-appro", s3["stock"], "solid"), ("s-ghost", s3["ghost"], "dotted")], s3["bars"], 1000.0, LINES,
        band=(3, 6, "non tenable (lundi S44 compris)"), today=s3["today"], ymin=-100.0,
        marks=[(5, 250.0, "lundi S44 : jour standard, date limite dépassée", "m-trigger"), (8, -50.0, "sans livraison : −50 jeudi S44", "m-trigger")],
        caption="Calculé par le moteur. Jeudi 450, vendredi 350, lundi 250, mardi 150 : jeudi S44 serait à −50. Le lundi S44 n'est plus tenable et une rupture arriverait avant le lundi S45 : le CBN propose la cible pleine au premier jour ouvré tenable, mercredi S44 (◇). Le lundi S45 reprend le rythme standard avec le complément.")
    # ---- 4. multi-sourcing quota : besoin 500 / semaine = 300 A (lundi) + 200 B (jeudi) – schéma de principe
    weeks4 = ["S43", "S44", "S45", "S46"]
    stock, _ = simulate(500.0, 100.0, {0: 300.0, 3: 200.0, 5: 300.0, 8: 200.0, 10: 300.0, 13: 200.0, 15: 300.0, 18: 200.0}, 20)
    c["quota"] = chart(
        "c4", "Multi-sourcing à quotas 60 / 40", "besoin 500 / semaine partagé : A 300 le lundi (60 %), B 200 le jeudi (40 %) · chaque part a son MOQ, son UM, son délai, ses jours, ses plafonds",
        weeks4, [("s-appro", stock, "solid")],
        [(0, 300.0, "b-cbn", "A 300"), (3, 200.0, "b-b", "B 200"), (5, 300.0, "b-cbn", "A 300"), (8, 200.0, "b-b", "B 200"), (10, 300.0, "b-cbn", "A 300"), (13, 200.0, "b-b", "B 200"), (15, 300.0, "b-cbn", "A 300"), (18, 200.0, "b-b", "B 200")],
        1000.0, LINES,
        caption="Schéma de principe. Une ligne Proposition CBN par fournisseur. Le stock oscille entre 500 et 700 : chaque part couvre jusqu'à la livraison suivante de son fournisseur. Part plafonnée ou refusée : elle passe à l'autre fournisseur le même jour.")
    # ---- 5. manque cumulé : 300 lundi matin S43, ordre ferme 1 000 lundi S44, puis 500 par semaine – projection sans plancher
    stock, short = simulate(300.0, 100.0, {5: 1000.0, 10: 500.0, 15: 500.0}, 20)
    c["shortage"] = chart(
        "c5", "Stock projeté et manque cumulé (−Σ)", "300 lundi matin S43, besoin 100 / jour · ordre ferme de 1 000 le lundi S44 · deux jours non servis avant (aucune proposition reprise)",
        weeks4, [("s-appro", stock, "solid")],
        [(5, 1000.0, "b-firm", "1 000 ferme"), (10, 500.0, "b-cbn", "500"), (15, 500.0, "b-cbn", "500")],
        1200.0, LINES, shortage=short, today=0,
        caption="Lundi 200, mardi 100, mercredi 0 ; jeudi et vendredi le besoin n'est pas servi (−Σ100, −Σ200), le stock reste à 0. L'ordre ferme du lundi S44 remet le manque à zéro ; ensuite le CBN maintient la cible. Dans l'application, le CBN proposerait en plus un plancher urgent (◇ !) pour les deux jours manquants.")
    return c


def coverage_scale() -> str:
    """Échelle de la couverture à date : les zones du statut (échelle non linéaire pour laisser lire les petites zones)."""
    w, h = 760, 150
    ticks = [0, 3, 5, 10, 20, 30, 40]
    pos = {0: 0.0, 3: 0.16, 5: 0.3, 10: 0.46, 20: 0.62, 30: 0.78, 40: 1.0}
    sx = lambda d: 40 + pos[d] * (w - 80)  # noqa: E731
    zones = [(0, 3, "z-crit", "Critique"), (3, 5, "z-warn", "À commander"), (5, 30, "z-ok", "OK"), (30, 40, "z-over", "Surstock")]
    out = [f'<svg class="chart scale" viewBox="0 0 {w} {h}" role="img" aria-label="Zones de la couverture à date" xmlns="http://www.w3.org/2000/svg">']
    for a, b, cls, lbl in zones:
        out.append(f'<rect class="{cls}" x="{sx(a):.1f}" y="30" width="{sx(b) - sx(a):.1f}" height="30" />')
        out.append(f'<text class="c-zone" x="{(sx(a) + sx(b)) / 2:.1f}" y="50" text-anchor="middle">{esc(lbl)}</text>')
    for d in ticks[:-1]:
        out.append(f'<text class="c-tick" x="{sx(d):.1f}" y="22" text-anchor="middle">{d} j</text>')
    out.append(f'<text class="c-sub" x="{sx(0):.1f}" y="80">≤ rouge + 1 (ex. 3 j) : Critique, quoi qu\'il arrive</text>')
    out.append(f'<text class="c-sub" x="{sx(0):.1f}" y="97">≤ orange (ex. 5 j) : À commander, sauf si un ordre ferme ou une quantité à retenir arrive avant l\'épuisement</text>')
    out.append(f'<text class="c-sub" x="{sx(0):.1f}" y="114">≥ surstock (ex. 30 j) : filtre Surstock, le statut reste celui des autres règles</text>')
    out.append(f'<text class="c-sub" x="{sx(0):.1f}" y="136">Couverture à date = jours ouvrés de besoin couverts par le stock de la veille seul, sans livraison. Seuils par article (et par semaine ISO).</text>')
    out.append("</svg>")
    return f'<figure class="fig">{"".join(out)}</figure>'


# ----------------------------------------------------------------------------- page
CSS = r"""
<style>
/* Disposition : une colonne de lecture (72ch), navigation collante, tableaux et figures pleine largeur de colonne. */
:root {
  --bg: #f5f7fb; --bg-elev: #ffffff; --bg-subtle: #eef1f7; --fg: #101828; --fg-muted: #475467; --fg-subtle: #667085;
  --border: #e1e6ef; --border-strong: #c9d1de;
  --brand: #2f5bea; --brand-soft: #e4eafc; --brand-fg: #1f45c4;
  --critical: #d92d20; --critical-soft: #fde8e6; --critical-fg: #b42318;
  --warning: #dc6803; --warning-soft: #fdeedb; --warning-fg: #b54708;
  --ok: #079455; --ok-soft: #dcf5e9; --ok-fg: #067647;
  --info: #0086c9; --info-soft: #dff1fb; --info-fg: #026aa2;
  --cell-neg: #7a1f1a; --cell-neg-fg: #fff;
  --font-display: "IBM Plex Sans Condensed", "Arial Narrow", "Segoe UI", system-ui, sans-serif;
  --font-body: "IBM Plex Sans", "Segoe UI", Roboto, system-ui, sans-serif;
  --font-mono: "IBM Plex Mono", ui-monospace, Menlo, Consolas, monospace;
  --measure: 72ch;
}
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg: #0b1220; --bg-elev: #121a2b; --bg-subtle: #182238; --fg: #e8edf6; --fg-muted: #a9b4c8; --fg-subtle: #8290a8;
  --border: #243049; --border-strong: #35435f;
  --brand: #6b8cff; --brand-soft: #1d2a52; --brand-fg: #9fb4ff;
  --critical: #f97066; --critical-soft: #4a1d1a; --critical-fg: #ff9a92;
  --warning: #fdb022; --warning-soft: #4a3310; --warning-fg: #ffcf6b;
  --ok: #32d583; --ok-soft: #113d2b; --ok-fg: #7fe8b3;
  --info: #36bffa; --info-soft: #0f3346; --info-fg: #8ad8ff;
  --cell-neg: #b33a32; --cell-neg-fg: #fff;
  color-scheme: dark;
} }
:root[data-theme="dark"] {
  --bg: #0b1220; --bg-elev: #121a2b; --bg-subtle: #182238; --fg: #e8edf6; --fg-muted: #a9b4c8; --fg-subtle: #8290a8;
  --border: #243049; --border-strong: #35435f;
  --brand: #6b8cff; --brand-soft: #1d2a52; --brand-fg: #9fb4ff;
  --critical: #f97066; --critical-soft: #4a1d1a; --critical-fg: #ff9a92;
  --warning: #fdb022; --warning-soft: #4a3310; --warning-fg: #ffcf6b;
  --ok: #32d583; --ok-soft: #113d2b; --ok-fg: #7fe8b3;
  --info: #36bffa; --info-soft: #0f3346; --info-fg: #8ad8ff;
  --cell-neg: #b33a32; --cell-neg-fg: #fff;
  color-scheme: dark;
}
body { background: var(--bg); color: var(--fg); font-family: var(--font-body); font-size: 15px; line-height: 1.55;
  padding-left: max(16px, env(safe-area-inset-left, 0px)); padding-right: max(16px, env(safe-area-inset-right, 0px)); padding-block: 0 48px; }
body::before { content: ""; position: fixed; inset: 0 0 auto; height: env(safe-area-inset-top, 0px); z-index: 10; background: var(--bg); }
* { box-sizing: border-box; }
.wrap { max-width: 980px; margin: 0 auto; min-width: 0; }
header.hero { padding: 36px 0 20px; border-bottom: 1px solid var(--border); display: grid; gap: 8px; }
header.hero .eyebrow { font-family: var(--font-display); text-transform: uppercase; letter-spacing: .08em; font-size: 12px; color: var(--brand-fg); font-weight: 600; }
header.hero h1 { font-family: var(--font-display); font-weight: 700; font-size: clamp(30px, 5vw, 44px); line-height: 1.05; margin: 0; text-wrap: balance; }
header.hero p { margin: 0; color: var(--fg-muted); max-width: var(--measure); }
header.hero .meta { font-size: 13px; color: var(--fg-subtle); display: flex; flex-wrap: wrap; gap: 6px 18px; }
nav.toc { position: sticky; top: env(safe-area-inset-top, 0px); z-index: 5; background: color-mix(in srgb, var(--bg) 88%, transparent); backdrop-filter: blur(8px);
  border-bottom: 1px solid var(--border); margin: 0 -16px; padding: 8px max(16px, env(safe-area-inset-right, 0px)) 8px max(16px, env(safe-area-inset-left, 0px)); display: flex; gap: 4px 14px; flex-wrap: wrap; font-size: 13px; }
nav.toc a { color: var(--fg-muted); text-decoration: none; font-family: var(--font-display); font-weight: 600; letter-spacing: .02em; padding: 2px 0; border-bottom: 2px solid transparent; }
nav.toc a:hover, nav.toc a:focus-visible { color: var(--brand-fg); border-bottom-color: var(--brand); outline: none; }
nav.toc .part { color: var(--fg-subtle); font-weight: 400; font-family: var(--font-body); }
main { display: grid; gap: 40px; padding-top: 28px; }
main > *, section.part > *, .tile, .legend > div > span:last-child { min-width: 0; }
h2 { font-family: var(--font-display); font-weight: 700; font-size: 28px; margin: 0 0 4px; line-height: 1.1; text-wrap: balance; }
h2 .part { display: block; font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--brand-fg); margin-bottom: 6px; }
h3 { font-family: var(--font-display); font-weight: 700; font-size: 21px; margin: 28px 0 8px; line-height: 1.15; }
h4 { font-family: var(--font-display); font-weight: 600; font-size: 16px; margin: 18px 0 6px; color: var(--fg); }
section.part { border-top: 3px solid var(--brand); padding-top: 18px; }
section.part > p.lead, .help-doc p.lead { color: var(--fg-muted); max-width: var(--measure); margin: 0 0 10px; font-size: 16px; }
p, li { max-width: var(--measure); }
p { margin: 8px 0; }
ul { margin: 6px 0; padding-left: 20px; }
li { margin: 3px 0; }
b { font-weight: 600; }
.rule { display: grid; grid-template-columns: minmax(120px, 170px) 1fr; gap: 6px 14px; align-items: baseline; margin: 10px 0; }
.rule dt { font-family: var(--font-display); font-weight: 600; color: var(--fg); }
.rule dd { margin: 0; max-width: var(--measure); }
@media (max-width: 560px) { .rule { grid-template-columns: 1fr; } .rule dt { margin-top: 6px; } }
.tbl { overflow-x: auto; max-width: 100%; margin: 10px 0 4px; border: 1px solid var(--border); border-radius: 6px; background: var(--bg-elev); }
table { border-collapse: collapse; width: 100%; font-size: 13.5px; }
th, td { padding: 7px 10px; border-bottom: 1px solid var(--border); text-align: left; vertical-align: top; }
th { font-family: var(--font-display); font-weight: 600; font-size: 12px; letter-spacing: .04em; text-transform: uppercase; color: var(--fg-subtle); background: var(--bg-subtle); white-space: nowrap; }
tr:last-child td { border-bottom: 0; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; font-family: var(--font-mono); font-size: 12.5px; }
td.note { color: var(--fg-muted); }
.cmt { color: var(--fg-muted); font-size: 13px; margin: 6px 0 0; max-width: var(--measure); }
.chip { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 12px; font-weight: 600; border: 1px solid transparent; white-space: nowrap; }
.chip.critical { background: var(--critical-soft); color: var(--critical-fg); }
.chip.warning { background: var(--warning-soft); color: var(--warning-fg); }
.chip.ok { background: var(--ok-soft); color: var(--ok-fg); }
.chip.info { background: var(--info-soft); color: var(--info-fg); }
.chip.neutral { background: var(--bg-subtle); color: var(--fg-muted); }
.chip.brand { background: var(--brand-soft); color: var(--brand-fg); }
.chip.outline { border-color: var(--border-strong); color: var(--fg-muted); }
.cell { display: inline-block; min-width: 64px; padding: 2px 8px; border: 1px solid var(--border-strong); border-radius: 3px; font-family: var(--font-mono); font-size: 12.5px; text-align: right; background: var(--bg-elev); color: var(--fg); font-variant-numeric: tabular-nums; }
.cell.erp { color: var(--fg-subtle); }
.cell.typed { background: var(--brand-soft); color: var(--brand-fg); box-shadow: inset 0 -2px 0 var(--brand); font-weight: 600; }
.cell.cbn { border-style: dashed; border-color: var(--warning); color: var(--fg-subtle); }
.cell.firm-open { background: var(--warning-soft); color: var(--warning-fg); }
.cell.firm-done { background: var(--ok-soft); color: var(--ok-fg); }
.cell.firm-ignored { background: var(--critical-soft); color: var(--critical-fg); text-decoration: line-through; }
.cell.transit { background: var(--warning-soft); color: var(--warning-fg); font-style: italic; }
.cell.received { background: var(--bg-elev); }
.cell.booked { background: var(--ok-soft); color: var(--ok-fg); }
.cell.neg { background: var(--cell-neg); color: var(--cell-neg-fg); font-weight: 600; }
.cell.refused { text-decoration: line-through; color: var(--fg-subtle); }
.cell .cov { font-size: 10px; color: var(--fg-subtle); margin-right: 5px; font-family: var(--font-body); }
.cell.cov-red .cov { color: var(--critical-fg); } .cell.cov-orange .cov { color: var(--warning-fg); } .cell.cov-green .cov { color: var(--ok-fg); }
.dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; vertical-align: middle; margin-left: 4px; }
.dot.ok { background: var(--ok); } .dot.ko { background: var(--critical); }
.mk { font-weight: 700; } .mk.urgent { color: var(--critical-fg); } .mk.cap { color: var(--warning-fg); } .mk.off { color: var(--brand-fg); }
.legend { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 8px 16px; margin: 10px 0; }
.legend > div { display: flex; gap: 10px; align-items: baseline; font-size: 13.5px; }
.legend .k { flex: none; }
.callout { border-left: 3px solid var(--brand); background: var(--brand-soft); padding: 10px 14px; border-radius: 0 6px 6px 0; max-width: var(--measure); margin: 12px 0; font-size: 14px; }
.callout.warn { border-left-color: var(--warning); background: var(--warning-soft); }
.fig { margin: 14px 0 4px; background: var(--bg-elev); border: 1px solid var(--border); border-radius: 6px; padding: 8px 8px 4px; overflow-x: auto; }
.fig figcaption { font-size: 13px; color: var(--fg-muted); padding: 6px 6px 8px; max-width: none; }
svg.chart { width: 100%; height: auto; display: block; font-family: var(--font-body); }
.c-title { font-family: var(--font-display); font-weight: 700; font-size: 15px; fill: var(--fg); }
.c-sub { font-size: 11px; fill: var(--fg-subtle); }
.c-grid { stroke: var(--border); stroke-width: 1; }
.c-sep { stroke: var(--border-strong); stroke-width: 1; stroke-dasharray: 2 3; }
.c-zero { stroke: var(--fg-muted); stroke-width: 1.2; }
.c-tick, .c-day { font-size: 10px; fill: var(--fg-subtle); }
.c-week { font-size: 11px; font-weight: 600; fill: var(--fg-muted); font-family: var(--font-display); }
.c-band { fill: var(--bg-subtle); }
.c-band-l { font-size: 11px; fill: var(--fg-muted); font-style: italic; }
.c-today { stroke: var(--fg-muted); stroke-width: 1.5; stroke-dasharray: 4 3; }
.c-today-l { font-size: 10px; fill: var(--fg-muted); }
.c-line { stroke-width: 1.5; stroke-dasharray: 6 4; fill: none; }
.c-line.l-target { stroke: var(--fg-muted); } .c-line.l-trigger { stroke: var(--warning); }
.c-line-l { font-size: 11px; } .c-line-l.l-target { fill: var(--fg-muted); } .c-line-l.l-trigger { fill: var(--warning-fg); }
.c-series { fill: none; stroke-width: 2.5; stroke-linejoin: round; }
.c-series.s-appro { stroke: var(--brand); } .c-series.s-ghost { stroke: var(--fg-subtle); stroke-width: 1.5; }
.c-series.dotted { stroke-dasharray: 3 4; }
.c-bar.b-cbn { fill: var(--ok); } .c-bar.b-urgent { fill: var(--critical); } .c-bar.b-firm { fill: var(--warning); } .c-bar.b-b { fill: var(--info); }
.c-bar-l { font-size: 11px; font-weight: 700; } .c-bar-l.b-cbn { fill: var(--ok-fg); } .c-bar-l.b-urgent { fill: var(--critical-fg); } .c-bar-l.b-firm { fill: var(--warning-fg); } .c-bar-l.b-b { fill: var(--info-fg); }
.c-short { fill: var(--cell-neg); } .c-short-l { font-size: 9px; font-weight: 700; fill: var(--critical-fg); }
.c-mark { stroke-width: 2; fill: var(--bg-elev); } .c-mark.m-trigger { stroke: var(--critical); } .c-mark.m-order { stroke: var(--warning); }
.c-mark-l { font-size: 11px; } .c-mark-l.m-trigger { fill: var(--critical-fg); } .c-mark-l.m-order { fill: var(--warning-fg); }
.z-crit { fill: var(--critical-soft); } .z-warn { fill: var(--warning-soft); } .z-ok { fill: var(--ok-soft); } .z-over { fill: var(--bg-subtle); }
.c-zone { font-family: var(--font-display); font-weight: 700; font-size: 12px; fill: var(--fg); }
footer { margin-top: 36px; padding-top: 16px; border-top: 1px solid var(--border); font-size: 13px; color: var(--fg-subtle); }
@media (prefers-reduced-motion: reduce) { * { scroll-behavior: auto !important; } }
html { scroll-behavior: smooth; scroll-padding-top: 56px; }
a { color: var(--brand-fg); }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap: 10px; margin: 12px 0; }
.tile { border: 1px solid var(--border); border-left-width: 4px; border-radius: 6px; padding: 10px 12px; background: var(--bg-elev); min-width: 0; }
.tile h4 { margin: 0 0 4px; font-size: 15px; }
.tile p { margin: 0; font-size: 13px; color: var(--fg-muted); }
.tile.critical { border-left-color: var(--critical); } .tile.warning { border-left-color: var(--warning); } .tile.info { border-left-color: var(--info); } .tile.brand { border-left-color: var(--brand); } .tile.ok { border-left-color: var(--ok); }
</style>
"""


def cell(txt: str, cls: str = "", cov: str | None = None) -> str:
    c = f'<span class="cov">{cov}</span>' if cov is not None else ""
    return f'<span class="cell {cls}">{c}{txt}</span>'


def chip(t: str, cls: str) -> str:
    return f'<span class="chip {cls}">{t}</span>'


def table(headers: list[str], rows: list[list[str]], num: set[int] = frozenset()) -> str:
    th = "".join(f'<th class="{"num" if i in num else ""}">{h}</th>' for i, h in enumerate(headers))
    body = []
    for r in rows:
        body.append("<tr>" + "".join(f'<td class="{"num" if i in num else ("note" if i == len(r) - 1 and r[i].startswith("→") else "")}">{v}</td>' for i, v in enumerate(r)) + "</tr>")
    return f'<div class="tbl"><table><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


PART_A = "Tableau d'approvisionnement"
PART_B = "Cockpit du jour"


def sections() -> list[dict[str, str]]:
    """Les pages du pense-bête : une par section (clé, partie, titre, corps HTML sans le titre)."""
    c = charts()
    S: list[dict[str, str]] = []

    def page(slug: str, part: str, part_title: str, title: str, *body: str) -> None:
        S.append({"slug": slug, "part": part, "part_title": part_title, "title": title, "html": "\n".join(body)})

    # ============================================================== accueil
    page("accueil", "", "", "Mode d'emploi en une page",
         """<p class="lead">Ce que l'application décide, affiche et colore. Une règle par ligne, les exemples dessous. Délais, couvertures et seuils se comptent en <b>jours ouvrés</b> du calendrier usine.</p>
<div class="callout"><b>Vide, c'est l'ERP ; un chiffre, c'est votre plan ; le stock se recalcule.</b></div>
<p><b>Partie A · Tableau d'approvisionnement</b> : une ligne <b>Besoin</b> ; par fournisseur actif : <b>Ordre ferme</b>, <b>Prévisionnel EDI</b>, <b>Quantité reçue</b>, <b>Quantité à retenir</b>, <b>Proposition CBN</b> ; puis <b>Ajustement</b> et <b>Stock projeté</b>, seule projection de stock du tableau. Deux lignes se saisissent (Quantité à retenir, Ajustement), trois se cliquent (Ordre ferme, Proposition CBN, Quantité reçue), le reste se lit.</p>
<p><b>Partie B · Cockpit du jour</b> : l'onglet <b>Couvertures et stocks</b> répond à « que dois-je faire aujourd'hui ? » article par article ; l'onglet <b>Flux d'approvisionnement</b> suit commandes, transports et réceptions du jour.</p>
<p>Les boutons <b>FR | EN</b> du tableau d'approvisionnement et de la fiche article passent les intitulés en anglais, pour un partage d'écran ou une capture envoyée à un fournisseur.</p>
<p class="cmt">Règles détaillées : <i>docs/regles_metier.md</i> ; calculs pas à pas : <i>docs/guide_moteur.md</i>.</p>""")

    # ============================================================== A.1 CBN
    page("cbn", "A", PART_A, "1. Proposition CBN",
         """<p>Recalculée à chaque affichage sur le stock projeté, <b>après</b> le plan : elle ne propose que ce que ni l'ERP ni vos cellules ne couvrent. Une proposition par fournisseur et par jour de livraison.</p>
<dl class="rule">
<dt>Déclenchement</dt><dd>un besoin non servi, ou un stock projeté <b>sous 85 % de la cible</b> (tolérance 15 %, règle commune réglable).</dd>
<dt>Cible</dt><dd>le besoin des <b>n prochains jours ouvrés</b> (couverture cible de l'article). Ni stock de sécurité, ni cycle de commande.</dd>
<dt>Quantité</dt><dd>cible − stock projeté + manque, relevée au <b>MOQ</b>, arrondie au multiple d'<b>UM</b>, tronquée par les plafonds.</dd>
<dt>Placement</dt><dd>le dernier <b>jour de livraison standard</b> du fournisseur avant le besoin ; les besoins jusqu'à la livraison suivante sont regroupés dessus.</dd>
<dt>Date limite</dt><dd>livraison − (délai − marge) jours ouvrés : le dernier jour où l'ordre peut partir, affiché « à commander au plus tard le ». <b>Marge</b> : 1 jour ouvré par défaut (règle commune), 0 = délai strict.</dd>
<dt>Tenable</dt><dd>date limite ≥ aujourd'hui. Le CBN vise alors la <b>cible pleine</b>.</dd>
</dl>""",
         c["trigger"],
         """<dl class="rule">
<dt>Hors jour standard ◇</dt><dd>le jour standard n'est pas tenable et une rupture arriverait avant la livraison standard suivante : la proposition se pose sur le <b>premier jour ouvré tenable</b> avant le besoin, marquée ◇. Sauf fournisseur à <b>jours stricts</b> (case du fournisseur, décochée par défaut).</dd>
<dt>Sous la cible sans rupture</dt><dd>rien ne manque avant la livraison standard suivante tenable : le besoin <b>l'attend</b>, pas de ◇ pour un simple passage sous la cible.</dd>
<dt>Plancher !</dt><dd>aucun jour ouvré tenable avant le besoin : proposition <b>urgente</b>, limitée au <b>minimum qui évite la rupture</b>, lotie MOQ / UM, posée sur le jour du besoin (sur le jour standard pour un fournisseur à jours stricts). Pas de tolérance sur le plancher.</dd>
</dl>""",
         c["floor"], c["offday"],
         """<dl class="rule">
<dt>Plafonds ▲</dt><dd>maximum par livraison et par semaine ISO (ordres fermes et quantités à retenir compris) : quantité tronquée au multiple d'UM, l'excédent glisse à la livraison suivante. Un manque qu'aucun plafond n'absorbe est signalé (« plafond fournisseur atteint »).</dd>
<dt>Refuser</dt><dd>un clic barre la cellule : plus de proposition pour <b>ce fournisseur ce jour-là</b>. Le besoin passe aux autres fournisseurs à quota, sinon glisse au jour suivant. Second clic : rétablie. Un refus daté dans le passé disparaît du tableau.</dd>
<dt>Reprendre</dt><dd>cliquer la cellule grisée puis Entrée : elle devient une quantité à retenir, à la date de la proposition (◇ comprise), et le CBN se recalcule sans elle.</dd>
</dl>
<h4>Marqueurs dans la cellule</h4>
<div class="legend">
<div><span class="k">""" + cell("1 440", "cbn") + """</span><span>grisé, pointillé orange : proposition (clic puis Entrée pour la reprendre)</span></div>
<div><span class="k">""" + cell('1 440 <span class="mk urgent">!</span>', "cbn") + """</span><span>urgente : plancher, date limite dépassée</span></div>
<div><span class="k">""" + cell('1 440 <span class="mk cap">▲</span>', "cbn") + """</span><span>plafonnée par le fournisseur</span></div>
<div><span class="k">""" + cell('1 440 <span class="mk off">◇</span>', "cbn") + """</span><span>hors jour standard</span></div>
<div><span class="k">""" + cell("1 440", "cbn refused") + """</span><span>refusée d'un clic (ce jour, ce fournisseur)</span></div>
</div>
<h4>Exemple : fournisseur du lundi, délai 5, marge 1, rupture mercredi 4/11, cible 1 440</h4>""",
         table(["L'appro regarde le", "Livraison proposée", "Date limite", "Quantité", "Cockpit"], [
             ["lun. 26/10", "lun. 2/11", "mar. 27/10", cell("1 440", "cbn"), chip("À commander", "warning")],
             ["mar. 27/10", "lun. 2/11", "aujourd'hui", cell("1 440", "cbn"), chip("À commander", "warning")],
             ["mer. 28/10", "mar. 3/11 ◇ (lundi non tenable)", "aujourd'hui", cell('1 440 <span class="mk off">◇</span>', "cbn"), chip("À commander", "warning")],
             ["jeu. 29/10", "mer. 4/11 ◇ (le jour du besoin)", "aujourd'hui", cell('1 440 <span class="mk off">◇</span>', "cbn"), chip("À commander", "warning")],
             ["ven. 30/10", "mer. 4/11 ◇, plancher", "jeu. 29/10, dépassée", cell('240 <span class="mk urgent">!</span>', "cbn"), chip("Critique", "critical")],
         ]),
         '<p class="cmt">Du mardi au jeudi, l\'article reste « à commander » à la cible pleine, sur un jour de livraison réaliste. Le plancher n\'apparaît que lorsque plus aucune livraison tenable ne précède le besoin.</p>')

    # ============================================================== A.2 Ordre ferme
    page("ferme", "A", PART_A, "2. Ordre ferme",
         """<p>La cellule affiche la <b>quantité commandée</b> du jour ; le calcul compte le <b>restant ERP</b> à partir d'aujourd'hui, rien pour le passé. Un clic ignore les commandes du jour, un second les rétablit.</p>
<div class="legend">
<div><span class="k">""" + cell("1 600", "firm-open") + """</span><span>orange : en cours (restant &gt; 0)</span></div>
<div><span class="k">""" + cell("400 / 1 600", "firm-open") + """</span><span>livraison partielle : restant / commandé</span></div>
<div><span class="k">""" + cell("1 600", "firm-done") + """</span><span>vert : soldée</span></div>
<div><span class="k">""" + cell("1 600", "firm-ignored") + """</span><span>rouge barré : ignorée d'un clic (hors projection, hors quantité à retenir)</span></div>
</div>
<p><b>En retard</b> = restant ERP des ordres fermes dont la livraison est passée. Ni alerte ni statut : une colonne du cockpit et une carte du flux, une ligne par commande, qui sort dès que l'ERP solde. Si vous l'attendez encore, tapez la quantité dans Quantité à retenir au jour prévu.</p>
<p>La ligne <b>Prévisionnel EDI</b> montre les quantités prévisionnelles de l'EDI : information, jamais comptée.</p>""")

    # ============================================================== A.3 Quantité reçue
    page("recu", "A", PART_A, "3. Quantité reçue",
         """<p>Une réception ERP est un <b>journal d'arrivée</b>. Comptent dans la ligne Quantité reçue et le stock : les réceptions d'une <b>commande ouverte</b> (CO-…) ou d'une <b>PO déclarée</b> (six chiffres, Référentiel › Commandes fermées). Les autres restent visibles dans Recherche, les flux et la fiche fournisseur.</p>""",
         table(["Statut ERP", "Cellule", "Compté ?", "Cockpit"], [
             ["En transit (journal non validé)", cell("🚚 <i>800</i>", "transit"), "jamais", "carte En transit (5 derniers jours ouvrés)"],
             ["Reçu (journal validé, ACR à valider)", cell("1 600", "received"), "oui", "carte Reçu le jour même ; À valider après 7 jours sans ACR"],
             ["Comptabilisé (ACR validé)", cell("1 600", "booked"), "oui", "carte Reçu le jour même"],
             ["DESADV annoncé, sans journal", cell("🚚 <i>800</i>", "transit"), "non", "En transit ; en erreur ou sans journal de saisie : À traiter"],
         ]),
         """<div class="legend">
<div><span class="k"><span class="dot ok"></span></span><span>point vert : BL annoncé par un DESADV traité / OK</span></div>
<div><span class="k"><span class="dot ko"></span></span><span>point rouge : aucun DESADV traité (réception manuelle, absent, en erreur, annulé). Jamais rouge pour une PO déclarée.</span></div>
</div>
<p>Fond et point sont indépendants. Un en transit se <b>masque</b> par double-clic confirmé (rétablissable dans Saisies &amp; journal). L'info-bulle détaille chaque ligne du jour : BL, quantité, état d'ACR, origine.</p>""")

    # ============================================================== A.4 Quantité à retenir
    page("appro", "A", PART_A, "4. Quantité à retenir",
         """<div class="legend">
<div><span class="k">""" + cell("1 600", "erp") + """</span><span>vide : l'ordre ferme ERP du jour (gris). Rien n'est stocké.</span></div>
<div><span class="k">""" + cell("1 200", "typed") + """</span><span>chiffre : votre décision pour ce fournisseur ce jour (décaler, fractionner, livraison hors commande).</span></div>
<div><span class="k">""" + cell("0", "typed") + """</span><span>0 : rien attendu ce jour. Vider = retour à l'ERP.</span></div>
<div><span class="k">""" + cell("1 440", "cbn") + """</span><span>grisé : proposition CBN dans une cellule vide sans ERP.</span></div>
</div>
<ul>
<li>Une cellule datée avant aujourd'hui a <b>expiré</b> (listée « expirée » dans Saisies &amp; journal).</li>
<li><b>Colonne semaine</b> : Entrée place la saisie sur le <b>jour de livraison standard</b> du fournisseur dans la semaine (le premier non passé ; le lundi sans fournisseur, ou pour un ajustement). L'info-bulle annonce la date avant la frappe.</li>
<li>Reprendre une proposition CBN depuis la cellule semaine garde <b>sa date</b>, ◇ comprise.</li>
<li><b>Maj+Entrée</b> après la frappe, ou clic droit › <b>Placer le jour…</b>, ouvre la liste des jours de la semaine (ERP, saisie, CBN de chaque jour, jour standard surligné) : la saisie se pose sur le jour choisi, la colonne reste en semaine.</li>
<li>Vider une cellule semaine remet à l'ERP toutes les cellules saisies de la semaine pour ce fournisseur (annulable). Le clic Ordre ferme sur une colonne semaine ignore toute la semaine. L'en-tête de la colonne bascule sur le détail par jour.</li>
<li>Recopie en tirant le carré. Clic droit : commentaire. Quantité ou expression (<span class="cell">2*600-50</span>). Chaque geste s'annule : toast « Annuler », Ctrl+Z / Ctrl+Y, ↶ ↷ (7 jours).</li>
</ul>""")

    # ============================================================== A.5 Stock projeté
    page("stock", "A", PART_A, "5. Stock projeté",
         """<p>Une seule projection dans le tableau, le <b>Stock projeté</b> : stock de référence + quantités reçues + ordres fermes restants (sauf cellule saisie) + quantités à retenir + propositions CBN + ajustements − besoin. Le <b>Projeté Ferme</b> (ordres fermes seuls, sans plan ni CBN) reste un indicateur : rupture ferme de la fiche, colonne du cockpit, impact programmes.</p>
<dl class="rule">
<dt>Formule</dt><dd>stock du jour = stock de la veille + entrées + ajustements − besoin, depuis le stock de référence (point zéro).</dd>
<dt>Jamais négatif</dt><dd>le besoin non servi est <b>perdu</b>. La cellule affiche le stock physique et, en fond rouge foncé, le <b>manque cumulé −Σ</b> depuis le dernier jour positif, remis à zéro dès que le stock redevient positif.</dd>
<dt>Couverture</dt><dd>en petit dans chaque cellule : jours ouvrés futurs dont le besoin cumulé est couvert par le stock du jour.</dd>
<dt>Passé</dt><dd>du point zéro à hier, reconstitué : gris, sans couverture.</dd>
</dl>
<div class="legend">
<div><span class="k">""" + cell("3 022", "cov-green", "14 j") + """</span><span>vert : au-dessus du seuil orange</span></div>
<div><span class="k">""" + cell("1 210", "cov-orange", "5 j") + """</span><span>orange : ≤ seuil orange (ex. 5 j)</span></div>
<div><span class="k">""" + cell("260", "cov-red", "1 j") + """</span><span>rouge : ≤ seuil rouge (ex. 2 j)</span></div>
<div><span class="k">""" + cell("0 · −Σ 300", "neg") + """</span><span>rupture : stock 0 et manque cumulé</span></div>
<div><span class="k">""" + cell("9 800", "", "42 j") + """</span><span>neutre : ≥ surstock (ex. 30 j)</span></div>
</div>""",
         table(["Jour", "Besoin", "Entrées", "Ajustement", "Stock projeté", "Commentaire"], [
             ["lun.", "100", "", "", cell("200", "cov-red", "2 j"), "→ veille 300 − 100"],
             ["mar.", "100", "", "", cell("100", "cov-red", "1 j"), "→ dernier jour couvert"],
             ["mer.", "100", "", "", cell("0", "cov-red", "0 j"), "→ exactement servi"],
             ["jeu.", "100", "", "", cell("0 · −Σ 100", "neg"), "→ non servi, perdu"],
             ["ven.", "100", "", "−50", cell("0 · −Σ 250", "neg"), "→ la casse s'ajoute au manque"],
             ["lun.", "100", "1 000 (ferme)", "", cell("900", "cov-green", "9 j"), "→ manque remis à zéro"],
         ], num={1, 2, 3}),
         c["shortage"])

    # ============================================================== A.6 Multi-sourcing
    page("multi", "A", PART_A, "6. Multi-sourcing",
         """<p>Les voies du tableau sont les <b>liens actifs</b>, par priorité. Les flux d'un fournisseur sans lien actif restent hors du tableau (Flux, Recherche, fiche fournisseur) ; un ordre ferme à venir chez lui remonte dans « Données ». Le fournisseur est toujours affiché par son code <b>et</b> son nom, filtrables l'un comme l'autre.</p>
<dl class="rule">
<dt>Quotas (défaut)</dt><dd>chaque besoin est <b>partagé</b> au prorata ; chaque part a son MOQ, son UM, son délai, ses jours, sa marge, ses plafonds. Une proposition par fournisseur.</dd>
<dt>Priorité</dt><dd>tout au fournisseur n° 1.</dd>
<dt>Plafond atteint</dt><dd>la part non livrable passe à l'autre fournisseur à quota <b>le même jour</b>, avant de glisser.</dd>
<dt>Refus</dt><dd>une part refusée passe aux autres fournisseurs à quota ; en mono-source, le besoin glisse au jour suivant.</dd>
<dt>Statut</dt><dd>suit la <b>date limite la plus proche</b> ; « Commande à placer » nomme ce fournisseur.</dd>
</dl>""",
         table(["Besoin net", "A · 60 % · lundi · MOQ 200 · UM 100", "B · 40 % · jeudi · MOQ 300 · UM 50", "Commentaire"], [
             ["1 000", cell("600", "cbn"), cell("400", "cbn"), "→ partage 60 / 40, chaque part lotie"],
             ["500", cell("300", "cbn"), cell("300", "cbn"), "→ 200 relevés au MOQ 300 de B"],
             ["1 000, A plafonné à 400", cell('400 <span class="mk cap">▲</span>', "cbn"), cell("600", "cbn"), "→ l'excédent de A passe à B"],
             ["1 000, B refusé jeudi", cell("1 000", "cbn"), cell("400", "cbn refused"), "→ tout à A ; B possible le jeudi suivant"],
         ]),
         c["quota"])

    # ============================================================== B.7 Couvertures et stocks
    page("stocks", "B", PART_B, "7. Onglet Couvertures et stocks",
         """<p>Cinq tuiles qui filtrent le portefeuille. Elles se lisent sur le <b>stock à date</b> (fin de la veille) et la <b>couverture à date</b> (jours ouvrés couverts par ce stock seul, sans livraison), comparés aux seuils de l'article.</p>""",
         coverage_scale(),
         """<div class="tiles">
<div class="tile critical"><h4>Critiques</h4><p>couverture à date ≤ rouge + 1 ; <b>ou</b> proposition urgente (date limite dépassée) ; <b>ou</b> stock de départ négatif.</p></div>
<div class="tile warning"><h4>À commander</h4><p>une date limite dans les <b>5 prochains jours ouvrés</b> ; <b>ou</b> couverture ≤ orange sans ordre ferme ni quantité à retenir avant l'épuisement.</p></div>
<div class="tile info"><h4>Surstock</h4><p>couverture à date ≥ seuil de surstock. Un <b>filtre</b>, pas un statut.</p></div>
<div class="tile brand"><h4>Données</h4><p>pas de stock de départ ; pas de lien actif ; absent des nomenclatures ; ordre ferme à venir chez un fournisseur sans lien. <b>Pastille bleue</b>, jamais dans le statut.</p></div>
<div class="tile ok"><h4>OK</h4><p>tout le reste : rien à faire aujourd'hui.</p></div>
</div>
<h4>Le portefeuille</h4>
<p>Article, fournisseurs (code et nom), approvisionneur, statut, stock à date (cible dessous), couverture à date (cible dessous), <b>commande en cours</b> (prochain ordre ferme : date, fournisseur, restant), <b>commande à placer</b> (première proposition : date limite « au plus tard le », fournisseur, quantité, livraison, ◇ si hors jour standard), en retard. Colonnes secondaires (dont <i>Rupture Ferme</i>) dans le menu Colonnes.</p>
<h4>Exemple : seuils rouge 2 j, orange 5 j, surstock 30 j, cible 10 j</h4>""",
         table(["Article", "Couverture à date", "Livraison avant l'épuisement ?", "Commande à placer", "Statut", "Commentaire"], [
             ["P-001", chip("1 j", "critical"), "oui, ordre ferme demain", "–", chip("Critique", "critical"), "→ la couverture rouge prime"],
             ["P-002", chip("4 j", "warning"), "non", "–", chip("À commander", "warning"), "→ orange, rien n'arrive"],
             ["P-003", chip("4 j", "warning"), "oui, quantité à retenir jeudi", "–", chip("OK", "ok"), "→ orange mais couvert"],
             ["P-004", chip("12 j", "ok"), "–", "au plus tard mer. 28/10 · A · 1 440 · liv. 3/11 ◇", chip("À commander", "warning"), "→ date limite sous 5 j ouvrés"],
             ["P-005", chip("12 j", "ok"), "–", "27/10 dépassée · plancher !", chip("Critique", "critical"), "→ une urgence CBN rend critique"],
             ["P-006", chip("42 j", "neutral"), "–", "–", chip("OK", "ok") + " + Surstock", "→ sans lien actif : pastille Données"],
         ]),
         '<p class="cmt">Sous les tuiles : contexte (couverture médiane, valeur du stock contre la cible, en-cours, en retard), projection de la valeur du stock, alertes prioritaires.</p>')

    # ============================================================== B.8 Flux
    page("flux", "B", PART_B, "8. Onglet Flux d'approvisionnement",
         """<p>Huit cartes ; un clic affiche les lignes dessous, avec export Excel. Calculées sur la copie ERP du jour (bandeau orange si la synchronisation est en retard).</p>
<div class="tiles">
<div class="tile critical"><h4>À commander</h4><p>propositions dont la <b>date limite tombe dans les 5 prochains jours ouvrés</b> ; les <b>urgentes</b> en tête, marquées <span class="mk urgent">!</span> ; colonne « au plus tard le », ◇ si hors jour standard. Les propositions <i>refusées</i> sont masquées ; le bouton « Afficher les ignorées » les montre.</p></div>
<div class="tile brand"><h4>Commandé</h4><p>ordres fermes <b>apparus aujourd'hui</b> dans la copie ERP, quelle que soit la livraison.</p></div>
<div class="tile info"><h4>En transit</h4><p>journaux d'arrivée <b>non validés</b> et DESADV qu'aucun journal ne porte, des <b>5 derniers jours ouvrés</b> seulement.</p></div>
<div class="tile warning"><h4>À traiter</h4><p>DESADV en <b>erreur</b>, ou traités <b>sans journal de saisie</b> ; les 5 derniers jours ouvrés, du plus récent au plus ancien (bouton « Tout afficher »).</p></div>
<div class="tile brand"><h4>À réceptionner</h4><p>ordres fermes attendus <b>aujourd'hui</b>.</p></div>
<div class="tile ok"><h4>Reçu</h4><p>journaux validés datés d'aujourd'hui.</p></div>
<div class="tile warning"><h4>En retard</h4><p>ordres fermes passés avec un restant ERP, <b>une ligne par commande</b> ; les 5 derniers jours ouvrés, du plus récent au plus ancien (bouton « Tout afficher »).</p></div>
<div class="tile warning"><h4>À valider</h4><p>réceptions au statut Reçu sans ACR depuis <b>7 jours</b> ou plus.</p></div>
</div>
<h4>Exemple : une journée, mercredi 28/10</h4>""",
         table(["Ligne du jour", "Carte", "Commentaire"], [
             ["Proposition A · 1 440 · au plus tard aujourd'hui · liv. mar. 3/11 ◇", chip("À commander", "critical"), "→ date limite sous 5 j ; ◇ mardi au lieu du lundi"],
             ["Proposition B · 240 ! · au plus tard hier · liv. 4/11", chip("À commander", "critical") + " en tête", "→ urgente : plancher"],
             ["Proposition C refusée lundi", chip("À commander", "critical") + " <i>masquée</i>", "→ visible par « Afficher les ignorées »"],
             ["CO-10234 créée ce matin, livraison 16/11", chip("Commandé", "brand"), "→ apparue aujourd'hui"],
             ["Journal BL-990002 non validé, émis lundi", chip("En transit", "info"), "→ jamais compté ; sort de la carte après 5 j ouvrés"],
             ["DESADV BL-990003 en erreur", chip("À traiter", "warning"), "→ à corriger dans l'ERP"],
             ["CO-10001 attendue aujourd'hui, restant 800", chip("À réceptionner", "brand"), "→ sans journal ce soir, en retard demain"],
             ["BL-990000 validé ce matin, ACR à valider", chip("Reçu", "ok"), "→ compté ; sans ACR dans 7 jours : À valider"],
             ["CO-09980 prévue le 20/10, restant 400", chip("En retard", "warning"), "→ sort dès que l'ERP solde"],
         ]))

    # ============================================================== Mémo
    page("memo", "", "", "Mémo : marqueurs, couleurs et gestes",
         table(["Où", "Signe", "Sens"], [
             ["Proposition CBN", '<span class="mk urgent">!</span>', "urgente : plancher, date limite dépassée → Critique, en tête de la carte À commander"],
             ["Proposition CBN", '<span class="mk cap">▲</span>', "plafonnée (max par livraison ou par semaine)"],
             ["Proposition CBN, cockpit", '<span class="mk off">◇</span>', "hors jour standard : jour ouvré tenable, pas le jour habituel"],
             ["Proposition CBN", "barré", "refusée d'un clic, ce fournisseur ce jour-là"],
             ["Ordre ferme", "orange / vert / rouge barré", "en cours / soldée / ignorée"],
             ["Quantité reçue", "italique orange 🚚 / blanc / vert", "en transit (jamais compté) / reçu / comptabilisé"],
             ["Quantité reçue", '<span class="dot ok"></span> / <span class="dot ko"></span>', "DESADV traité / aucun DESADV traité"],
             ["Quantité à retenir", "gris / bleu", "ERP (vide) / votre saisie (0 = rien attendu)"],
             ["Stock projeté", "petit chiffre vert · orange · rouge · neutre", "couverture : normale · ≤ orange · ≤ rouge · ≥ surstock"],
             ["Stock projeté", "fond rouge foncé −Σ", "manque cumulé du besoin non servi"],
             ["Statut", chip("Critique", "critical") + " " + chip("À commander", "warning") + " " + chip("OK", "ok"), "couverture rouge ou urgence / date limite sous 5 j ou orange non couvert / rien à faire"],
             ["Statut", "pastille bleue", "données à corriger"],
             ["Partout", "Ctrl+Z · Ctrl+Y · ↶ ↷", "annuler / rétablir chaque geste, 7 jours, dans l'ordre"],
             ["En-tête", "FR | EN", "intitulés du tableau et des tuiles en anglais (partage d'écran, capture)"],
             ["En-tête", "?", "ce pense-bête dans l'application (administrateurs pour l'instant) ; un administrateur peut le modifier sur place"],
         ]),
         """<div class="callout warn"><b>Réglable en commun</b> : tolérance (85 % de la cible), marge de délai (1 j ouvré). <b>Par article</b> (et par semaine ISO) : couverture cible, seuils rouge et orange, surstock, jours de consommation, source de consommation, politique de multi-sourcing. <b>Par fournisseur</b> : délai, jours de livraison standard, jours stricts. <b>Par lien</b> : MOQ, UM, quota, priorité, délai spécifique, plafonds.</div>""")
    return S


def page() -> str:
    """La page autonome (docs/pense_bete_approvisionneur.html)."""
    secs = sections()
    by = {s["slug"]: s for s in secs}
    parts: list[str] = []
    parts.append('<title>Pense-bête de l\'approvisionneur</title>\n<meta name="claude:viewport-fit-sides" content="cover">\n'
                 '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
                 '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Condensed:wght@600;700&family=IBM+Plex+Sans:ital,wght@0,400;0,600;1,400&family=IBM+Plex+Mono:wght@400;600&display=swap">')
    parts.append(CSS)
    parts.append('<div class="wrap">')
    parts.append(f"""<header class="hero">
<div class="eyebrow">Ma Routine Appro · règles, statuts, marqueurs, couleurs</div>
<h1>Pense-bête de l'approvisionneur</h1>
<p>Ce que l'application décide, affiche et colore, en une page. Une règle par ligne, les exemples dessous. Délais, couvertures et seuils se comptent en <b>jours ouvrés</b> du calendrier usine.</p>
<div class="meta"><span>Version du {VERSION}</span><span>Marge de délai, hors jour standard ◇, plancher au jour du besoin, une seule projection de stock dans le tableau, colonne semaine sur le jour standard</span></div>
</header>
<nav class="toc" aria-label="Sommaire">
<span class="part">Tableau d'appro</span><a href="#cbn">CBN</a><a href="#ferme">Ordre ferme</a><a href="#recu">Quantité reçue</a><a href="#appro">Quantité à retenir</a><a href="#stock">Stock projeté</a><a href="#multi">Multi-sourcing</a>
<span class="part">Cockpit</span><a href="#stocks">Couvertures et stocks</a><a href="#flux">Flux d'approvisionnement</a><a href="#memo">Mémo</a>
</nav>
<main>""")
    parts.append(f'<section class="part" id="tableau">\n<h2><span class="part">Partie A</span>{PART_A}</h2>\n{by["accueil"]["html"]}')
    for slug in ("cbn", "ferme", "recu", "appro", "stock", "multi"):
        parts.append(f'<h3 id="{slug}">{by[slug]["title"]}</h3>\n{by[slug]["html"]}')
    parts.append("</section>")
    parts.append(f'<section class="part" id="cockpit">\n<h2><span class="part">Partie B</span>Tableau de bord : {PART_B}</h2>')
    for slug in ("stocks", "flux"):
        parts.append(f'<h3 id="{slug}">{by[slug]["title"]}</h3>\n{by[slug]["html"]}')
    parts.append("</section>")
    parts.append(f'<section class="part" id="memo">\n<h2><span class="part">Mémo</span>Marqueurs, couleurs et gestes</h2>\n{by["memo"]["html"]}\n</section>')
    parts.append('<footer>Ma Routine Appro · pense-bête de l\'approvisionneur · règles détaillées : <i>docs/regles_metier.md</i>, calculs : <i>docs/guide_moteur.md</i>.</footer>')
    parts.append("</main></div>")
    return "\n".join(parts)


def write_json() -> None:
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps({"version": VERSION, "pages": sections()}, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    OUT_HTML.write_text(page(), encoding="utf-8")
    write_json()
    print(OUT_HTML, OUT_HTML.stat().st_size)
    print(OUT_JSON, OUT_JSON.stat().st_size)
