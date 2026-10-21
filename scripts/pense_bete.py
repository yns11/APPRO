# -*- coding: utf-8 -*-
"""Génère docs/pense_bete_approvisionneur.html : règles, marqueurs, couleurs, exemples et graphiques SVG."""
from __future__ import annotations

import html
import pathlib

OUT = pathlib.Path(__file__).resolve().parents[1] / "docs" / "pense_bete_approvisionneur.html"

# ----------------------------------------------------------------------------- charts
W, H = 760, 300
PAD_L, PAD_R, PAD_T, PAD_B = 46, 16, 44, 44
DAYS = ["lun", "mar", "mer", "jeu", "ven"]


def esc(s: str) -> str:
    return html.escape(s, quote=True)


def simulate(stock0: float, demand: float, deliveries: dict[int, float], n: int) -> tuple[list[float], list[float]]:
    """Stock en fin de jour (jamais négatif) et manque cumulé (−Σ) depuis le dernier jour positif."""
    stock, short, s, cum = [], [], stock0, 0.0
    for j in range(n):
        s = s + deliveries.get(j, 0.0) - demand
        if s < 0:
            cum += -s
            s = 0.0
        else:
            cum = 0.0
        stock.append(s)
        short.append(cum)
    return stock, short


def chart(cid: str, title: str, subtitle: str, weeks: list[str], series: list[tuple[str, list[float], str]],
          bars: list[tuple[int, float, str, str]], ymax: float, lines: list[tuple[float, str, str]] = (),
          band: tuple[int, int, str] | None = None, marks: list[tuple[int, float, str, str]] = (),
          today: int | None = None, shortage: list[float] | None = None, caption: str = "") -> str:
    """Un graphique : n = 5 jours ouvrés × semaines. series = (classe, valeurs, style 'solid'|'dotted').
    bars = (jour, quantité, classe, libellé). lines = (y, classe, libellé). marks = (jour, y, texte, classe)."""
    n = 5 * len(weeks)
    iw = W - PAD_L - PAD_R
    ih = H - PAD_T - PAD_B
    x = lambda j: PAD_L + (j + 0.5) * iw / n  # noqa: E731
    y = lambda v: PAD_T + ih - (v / ymax) * ih  # noqa: E731
    out = [f'<svg class="chart" viewBox="0 0 {W} {H}" role="img" aria-labelledby="{cid}-t" xmlns="http://www.w3.org/2000/svg">',
           f'<title id="{cid}-t">{esc(title)}</title>']
    out.append(f'<text class="c-title" x="{PAD_L}" y="18">{esc(title)}</text>')
    out.append(f'<text class="c-sub" x="{PAD_L}" y="34">{esc(subtitle)}</text>')
    # band (délai / non tenable)
    if band:
        a, b, lbl = band
        out.append(f'<rect class="c-band" x="{x(a) - iw / n / 2:.1f}" y="{PAD_T}" width="{(b - a + 1) * iw / n:.1f}" height="{ih}" />')
        out.append(f'<text class="c-band-l" x="{x(a) - iw / n / 2 + 6:.1f}" y="{PAD_T + 14}">{esc(lbl)}</text>')
    # grid + week separators + labels
    for k in range(0, 5):
        v = ymax * k / 4
        out.append(f'<line class="c-grid" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(v):.1f}" y2="{y(v):.1f}" />')
        out.append(f'<text class="c-tick" x="{PAD_L - 6}" y="{y(v) + 4:.1f}" text-anchor="end">{int(v)}</text>')
    for w, wk in enumerate(weeks):
        x0 = PAD_L + w * iw / len(weeks)
        if w:
            out.append(f'<line class="c-sep" x1="{x0:.1f}" x2="{x0:.1f}" y1="{PAD_T}" y2="{PAD_T + ih}" />')
        out.append(f'<text class="c-week" x="{x0 + 4:.1f}" y="{H - 8}">{esc(wk)}</text>')
        for d in range(5):
            out.append(f'<text class="c-day" x="{x(w * 5 + d):.1f}" y="{PAD_T + ih + 14}" text-anchor="middle">{DAYS[d]}</text>')
    # today
    if today is not None:
        out.append(f'<line class="c-today" x1="{x(today):.1f}" x2="{x(today):.1f}" y1="{PAD_T}" y2="{PAD_T + ih}" />')
        out.append(f'<text class="c-today-l" x="{x(today) + 4:.1f}" y="{PAD_T + 28}">aujourd\'hui</text>')
    # horizontal lines
    for v, cls, lbl in lines:
        out.append(f'<line class="c-line {cls}" x1="{PAD_L}" x2="{W - PAD_R}" y1="{y(v):.1f}" y2="{y(v):.1f}" />')
        out.append(f'<text class="c-line-l {cls}" x="{W - PAD_R - 4}" y="{y(v) - 4:.1f}" text-anchor="end">{esc(lbl)}</text>')
    # shortage (−Σ)
    if shortage:
        for j, c in enumerate(shortage):
            if c > 0:
                out.append(f'<rect class="c-short" x="{x(j) - iw / n / 2 + 1:.1f}" y="{y(0) - 10}" width="{iw / n - 2:.1f}" height="10" />')
                out.append(f'<text class="c-short-l" x="{x(j):.1f}" y="{y(0) + 14:.1f}" text-anchor="middle">−Σ{int(c)}</text>')
    # bars
    bw = max(6.0, iw / n * 0.42)
    for j, qty, cls, lbl in bars:
        out.append(f'<rect class="c-bar {cls}" x="{x(j) - bw / 2:.1f}" y="{y(qty):.1f}" width="{bw:.1f}" height="{y(0) - y(qty):.1f}" rx="2" />')
        if lbl:
            out.append(f'<text class="c-bar-l {cls}" x="{x(j):.1f}" y="{y(qty) - 5:.1f}" text-anchor="middle">{esc(lbl)}</text>')
    # series
    for cls, vals, style in series:
        pts = " ".join(f"{x(j):.1f},{y(v):.1f}" for j, v in enumerate(vals) if v is not None)
        out.append(f'<polyline class="c-series {cls} {style}" points="{pts}" />')
    # marks
    for j, v, txt, cls in marks:
        out.append(f'<circle class="c-mark {cls}" cx="{x(j):.1f}" cy="{y(v):.1f}" r="4" />')
        anchor = "end" if j > n * 0.55 else "start"
        dx = -8 if anchor == "end" else 8
        dy = -10 if v > ymax * 0.12 else -14
        out.append(f'<text class="c-mark-l {cls}" x="{x(j) + dx:.1f}" y="{y(v) + dy:.1f}" text-anchor="{anchor}">{esc(txt)}</text>')
    out.append("</svg>")
    fig = f'<figure class="fig">{"".join(out)}'
    if caption:
        fig += f'<figcaption>{caption}</figcaption>'
    return fig + "</figure>"


def charts() -> dict[str, str]:
    c: dict[str, str] = {}
    weeks4 = ["S43", "S44", "S45", "S46"]
    # ---- 1. déclenchement et cible : stock en fin de jour ; chaque lundi la livraison couvre jusqu'au lundi suivant
    stock, _ = simulate(1000.0, 100.0, {5: 500.0, 10: 500.0, 15: 500.0}, 20)
    without, _ = simulate(1000.0, 100.0, {}, 10)
    c["trigger"] = chart(
        "c1", "Déclenchement et cible", "besoin 100 / jour · cible 5 jours ouvrés = 500 · déclenchement sous 85 % = 425 · fournisseur du lundi, délai 5, marge 1",
        weeks4, [("s-appro", stock, "solid"), ("s-ghost", [None] * 4 + without[4:], "dotted")],
        [(5, 500.0, "b-cbn", "500"), (10, 500.0, "b-cbn", "500"), (15, 500.0, "b-cbn", "500")],
        1000.0, [(500.0, "l-target", "cible 500"), (425.0, "l-trigger", "déclenchement 425")],
        marks=[(5, 400.0, "sans livraison : 400 < 425 → proposition le lundi", "m-trigger"), (1, 800.0, "date limite = lun. S44 − 4 j.o. = mar. S43", "m-order")],
        today=0, caption="La proposition se pose sur le jour standard (lundi) avant le besoin et regroupe les besoins jusqu'au lundi suivant : le stock redescend à la cible (500) la veille de la livraison suivante. Ligne pointillée : ce que ferait le stock sans la proposition.")
    # ---- 2. plancher : aujourd'hui mer. S43, stock 250 mardi soir ; vendredi non tenable ; lundi S44 tenable
    stock, _ = simulate(250.0, 100.0, {4: 50.0, 5: 1000.0, 10: 500.0, 15: 500.0}, 20)
    stock[:2] = [None, None]  # type: ignore[list-item]
    c["floor"] = chart(
        "c2", "Plancher urgent (!) puis cible pleine", "aujourd'hui mercredi S43 · stock 250 mardi soir, besoin 100 / jour · lundi S44 est la première livraison tenable (date limite = aujourd'hui)",
        weeks4, [("s-appro", stock, "solid")],
        [(4, 50.0, "b-urgent", "50 !"), (5, 1000.0, "b-cbn", "1 000"), (10, 500.0, "b-cbn", "500"), (15, 500.0, "b-cbn", "500")],
        1000.0, [(500.0, "l-target", "cible 500"), (425.0, "l-trigger", "déclenchement 425")],
        band=(2, 4, "aucune livraison tenable : plancher"), today=2,
        caption="Jusqu'à vendredi, aucune livraison n'est tenable : le CBN propose, urgente, la quantité minimale qui évite la rupture (50 vendredi, le jour du besoin, ◇). Lundi S44, première livraison tenable : cible pleine, et la livraison couvre la semaine (1 000), puis 500 par semaine.")
    # ---- 3. hors jour standard : aujourd'hui jeu. S43, stock 550 mercredi soir ; tenable à partir de mer. S44
    stock, _ = simulate(550.0, 100.0, {7: 850.0, 10: 300.0, 15: 500.0}, 20)
    stock[:3] = [None, None, None]  # type: ignore[list-item]
    before, _ = simulate(550.0, 100.0, {}, 20)
    c["offday"] = chart(
        "c3", "Hors jour standard (◇)", "aujourd'hui jeudi S43 · fournisseur du lundi, délai 5, marge 1 · tenable à partir de mercredi S44 (jeudi + 4 j.o.)",
        weeks4, [("s-appro", stock, "solid"), ("s-ghost", [None] * 6 + before[6:9] + [None] * 11, "dotted")],
        [(7, 850.0, "b-cbn", "850 ◇"), (10, 300.0, "b-cbn", "300"), (15, 500.0, "b-cbn", "500")],
        1000.0, [(500.0, "l-target", "cible 500"), (425.0, "l-trigger", "déclenchement 425")],
        band=(3, 6, "non tenable (lundi S44 compris)"),
        marks=[(5, 250.0, "lundi S44 : jour standard, date limite dépassée", "m-trigger"), (8, 0.0, "sans livraison : rupture jeudi", "m-trigger")], today=3,
        caption="Le jour standard (lundi S44) n'est pas tenable ; plutôt que d'attendre le lundi S45 (rupture jeudi) ou de tomber au plancher, le CBN propose la cible pleine au premier jour ouvré tenable avant le besoin : mercredi S44, marqué ◇. Le lundi S45 ne reçoit que le complément. Sauf fournisseur à jours stricts.")
    # ---- 4. multi-sourcing quota
    stock, _ = simulate(700.0, 100.0, {3: 400.0, 5: 600.0, 8: 400.0, 10: 600.0, 13: 400.0, 15: 600.0}, 20)
    c["quota"] = chart(
        "c4", "Multi-sourcing à quotas 60 / 40", "A livre le lundi (60 %), B le jeudi (40 %) · chaque besoin est partagé, chaque part a son MOQ, son UM, son délai et ses plafonds",
        weeks4, [("s-appro", stock, "solid")],
        [(3, 400.0, "b-b", "B 400"), (5, 600.0, "b-cbn", "A 600"), (8, 400.0, "b-b", "B 400"), (10, 600.0, "b-cbn", "A 600"), (13, 400.0, "b-b", "B 400"), (15, 600.0, "b-cbn", "A 600")],
        1200.0, [(500.0, "l-target", "cible 500")], today=0,
        caption="Une ligne Proposition CBN par fournisseur. La part qu'un fournisseur ne peut pas livrer (plafond) passe à l'autre le même jour ; une part refusée d'un clic aussi. Avec la politique « priorité », tout va au fournisseur n° 1.")
    # ---- 5. manque cumulé
    stock, short = simulate(300.0, 100.0, {5: 500.0, 10: 500.0, 15: 500.0}, 20)
    c["shortage"] = chart(
        "c5", "Stock projeté et manque cumulé (−Σ)", "stock 300 lundi soir S43, besoin 100 / jour, première livraison lundi S44 : deux jours non servis",
        weeks4, [("s-appro", stock, "solid")],
        [(5, 500.0, "b-firm", "500"), (10, 500.0, "b-cbn", "500"), (15, 500.0, "b-cbn", "500")],
        1000.0, [(500.0, "l-target", "cible 500")], shortage=short, today=0,
        marks=[(2, 0.0, "stock à 0 : le besoin non servi est perdu", "m-trigger")],
        caption="Le stock n'est jamais négatif ; la cellule affiche le stock physique et, en fond rouge foncé, le cumul du besoin non servi depuis le dernier jour positif (−Σ100 jeudi, −Σ200 vendredi), remis à zéro dès la livraison du lundi (commande ferme, en orange).")
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
    out.append(f'<text class="c-sub" x="{sx(0):.1f}" y="97">≤ orange (ex. 5 j) : À commander, sauf si une livraison ferme ou une cellule Appro. arrive avant l\'épuisement</text>')
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
.wrap { max-width: 980px; margin: 0 auto; }
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
.wrap { min-width: 0; }
h2 { font-family: var(--font-display); font-weight: 700; font-size: 28px; margin: 0 0 4px; line-height: 1.1; text-wrap: balance; }
h2 .part { display: block; font-size: 12px; letter-spacing: .08em; text-transform: uppercase; color: var(--brand-fg); margin-bottom: 6px; }
h3 { font-family: var(--font-display); font-weight: 700; font-size: 21px; margin: 28px 0 8px; line-height: 1.15; }
h4 { font-family: var(--font-display); font-weight: 600; font-size: 16px; margin: 18px 0 6px; color: var(--fg); }
section.part { border-top: 3px solid var(--brand); padding-top: 18px; }
section.part > p.lead { color: var(--fg-muted); max-width: var(--measure); margin: 0 0 10px; font-size: 16px; }
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
/* chips et cellules d'exemple */
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
/* figures */
.fig { margin: 14px 0 4px; background: var(--bg-elev); border: 1px solid var(--border); border-radius: 6px; padding: 8px 8px 4px; overflow-x: auto; }
.fig figcaption { font-size: 13px; color: var(--fg-muted); padding: 6px 6px 8px; max-width: none; }
svg.chart { width: 100%; height: auto; display: block; font-family: var(--font-body); }
.c-title { font-family: var(--font-display); font-weight: 700; font-size: 15px; fill: var(--fg); }
.c-sub { font-size: 11px; fill: var(--fg-subtle); }
.c-grid { stroke: var(--border); stroke-width: 1; }
.c-sep { stroke: var(--border-strong); stroke-width: 1; stroke-dasharray: 2 3; }
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
.c-short { fill: var(--cell-neg); } .c-short-l { font-size: 10px; font-weight: 700; fill: var(--critical-fg); }
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


def table(headers: list[str], rows: list[list[str]], num: set[int] = frozenset()) -> str:
    th = "".join(f'<th class="{"num" if i in num else ""}">{h}</th>' for i, h in enumerate(headers))
    body = []
    for r in rows:
        body.append("<tr>" + "".join(f'<td class="{"num" if i in num else ("note" if i == len(r) - 1 and r[i].startswith("→") else "")}">{v}</td>' for i, v in enumerate(r)) + "</tr>")
    return f'<div class="tbl"><table><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def page() -> str:
    c = charts()
    chip = lambda t, cls: f'<span class="chip {cls}">{t}</span>'  # noqa: E731
    parts: list[str] = []
    parts.append('<title>Pense-bête de l\'approvisionneur</title>\n<meta name="claude:viewport-fit-sides" content="cover">\n'
                 '<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
                 '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Condensed:wght@600;700&family=IBM+Plex+Sans:ital,wght@0,400;0,600;1,400&family=IBM+Plex+Mono:wght@400;600&display=swap">')
    parts.append(CSS)
    parts.append('<div class="wrap">')
    parts.append('''<header class="hero">
<div class="eyebrow">Ma Routine Appro · règles, statuts, marqueurs, couleurs</div>
<h1>Pense-bête de l'approvisionneur</h1>
<p>Tout ce que l'application décide, affiche ou colore, en une page. Une règle par ligne, les exemples en dessous. Tous les délais, couvertures et seuils se comptent en <b>jours ouvrés</b> du calendrier usine.</p>
<div class="meta"><span>Version du 21 octobre 2026</span><span>Intègre les règles validées : marge de délai, livraison hors jour standard ◇, une seule projection de stock dans le tableau</span></div>
</header>
<nav class="toc" aria-label="Sommaire">
<span class="part">Tableau d'appro</span><a href="#cbn">CBN</a><a href="#ferme">Ferme</a><a href="#recu">Reçu</a><a href="#appro">Appro.</a><a href="#stock">Stock projeté</a><a href="#multi">Multi-sourcing</a>
<span class="part">Cockpit</span><a href="#stocks">Couvertures et stocks</a><a href="#flux">Flux d'approvisionnement</a><a href="#memo">Mémo</a>
</nav>
<main>''')

    # ============================================================== PARTIE A
    parts.append('''<section class="part" id="tableau">
<h2><span class="part">Partie A</span>Tableau d'approvisionnement</h2>
<p class="lead">Une ligne <b>Besoin</b> ; par fournisseur actif, les lignes <b>Ferme</b>, <b>Prévisionnel</b>, <b>Reçu</b>, <b>Appro.</b> et <b>Proposition CBN</b> ; puis <b>Ajustement</b> et <b>Projeté Appro.</b>, la seule projection de stock du tableau. Deux lignes se saisissent (Appro., Ajustement), trois se cliquent (Ferme, Proposition CBN, Reçu), le reste se lit.</p>
<div class="callout"><b>Vide, c'est l'ERP ; un chiffre, c'est votre plan ; le stock se recalcule.</b> Une colonne semaine se saisit comme une colonne jour : la valeur se place au lundi et la cellule affiche le total de la semaine.</div>
''')

    # ---------------------------------------------------------------- CBN
    parts.append('''<h3 id="cbn">1. Proposition CBN</h3>
<p>Recalculée à chaque affichage sur le Projeté Appro., <b>après</b> le plan : elle ne propose que ce que ni l'ERP ni vos cellules ne couvrent. Une proposition par fournisseur et par jour de livraison.</p>
<dl class="rule">
<dt>Déclenchement</dt><dd>un besoin non servi, ou un stock projeté <b>sous 85 % de la cible</b> (tolérance 15 %, seule règle commune réglable avec la marge).</dd>
<dt>Cible</dt><dd>le besoin des <b>n prochains jours ouvrés</b> (couverture cible de l'article, par semaine si besoin). Ni stock de sécurité, ni cycle de commande.</dd>
<dt>Quantité</dt><dd>cible − stock projeté + manque, relevée au <b>MOQ</b>, arrondie au multiple supérieur de l'<b>UM</b> du lien, puis tronquée par les plafonds.</dd>
<dt>Placement</dt><dd>sur le <b>jour de livraison standard</b> du fournisseur, le dernier avant le besoin ; les besoins jusqu'à la livraison suivante sont regroupés dessus.</dd>
<dt>Date limite</dt><dd>livraison − (délai − marge) jours ouvrés : le dernier jour où l'ordre peut partir. Affichée « à commander au plus tard le ». <b>Marge</b> = 1 jour ouvré par défaut (0 = délai strict).</dd>
<dt>Tenable</dt><dd>date limite ≥ aujourd'hui. Le CBN vise alors la <b>cible pleine</b>.</dd>
<dt>Hors jour standard ◇</dt><dd>le jour standard n'est pas tenable mais un autre jour ouvré avant le besoin l'est : la proposition se pose sur le <b>premier jour ouvré tenable</b>, marquée ◇. Sauf fournisseur à <b>jours stricts</b> (case du fournisseur, décochée par défaut).</dd>
<dt>Plancher !</dt><dd>aucun jour ouvré tenable avant le besoin : proposition <b>urgente</b>, limitée au <b>minimum qui évite la rupture</b> jusqu'à la veille de la livraison suivante, lotie MOQ / UM. La tolérance ne s'applique pas au plancher.</dd>
<dt>Plafonds ▲</dt><dd>maximum par livraison et par semaine ISO (commandes fermes et cellules Appro. comprises) : la quantité est tronquée au multiple d'UM, l'excédent glisse à la livraison suivante ; un manque qu'aucun plafond ne peut absorber est signalé (alerte « plafond fournisseur atteint »).</dd>
<dt>Refuser</dt><dd>un clic sur la cellule la barre : plus de proposition pour <b>ce fournisseur ce jour-là</b> ; le besoin passe aux autres fournisseurs à quota, ou glisse au jour de livraison suivant. Un second clic rétablit.</dd>
<dt>Reprendre</dt><dd>cliquer la cellule grisée puis Entrée : elle devient une cellule Appro. ordinaire et le CBN se recalcule sans elle.</dd>
</dl>
<h4>Marqueurs dans la cellule</h4>
<div class="legend">
<div><span class="k">''' + cell("1 440", "cbn") + '''</span><span>grisé, pointillé orange = proposition (cliquer puis Entrée pour la reprendre)</span></div>
<div><span class="k">''' + cell('1 440 <span class="mk urgent">!</span>', "cbn") + '''</span><span>urgente = plancher, date limite dépassée</span></div>
<div><span class="k">''' + cell('1 440 <span class="mk cap">▲</span>', "cbn") + '''</span><span>plafonnée par le fournisseur</span></div>
<div><span class="k">''' + cell('1 440 <span class="mk off">◇</span>', "cbn") + '''</span><span>hors jour standard (jour tenable avant le besoin)</span></div>
<div><span class="k">''' + cell("1 440", "cbn refused") + '''</span><span>refusée d'un clic (ce jour, ce fournisseur)</span></div>
</div>
<h4>Exemple : fournisseur du lundi, délai 5, marge 1, besoin mercredi 4/11 (cible 1 440)</h4>''')
    parts.append(table(["L'appro regarde le", "Livraison proposée", "Date limite", "Quantité", "Cockpit"], [
        ["lun. 26/10", "lun. 2/11", "mar. 27/10", cell("1 440", "cbn"), chip("À commander", "warning")],
        ["mar. 27/10", "lun. 2/11", "aujourd'hui", cell("1 440", "cbn"), chip("À commander", "warning")],
        ["mer. 28/10", "mar. 3/11 ◇ (lundi non tenable)", "aujourd'hui", cell('1 440 <span class="mk off">◇</span>', "cbn"), chip("À commander", "warning")],
        ["jeu. 29/10", "mer. 4/11 ◇ (le jour du besoin)", "aujourd'hui", cell('1 440 <span class="mk off">◇</span>', "cbn"), chip("À commander", "warning")],
        ["ven. 30/10", "mer. 4/11 ◇, plancher", "jeu. 29/10, dépassée", cell('240 <span class="mk urgent">!</span>', "cbn"), chip("Critique", "critical")],
    ]))
    parts.append('<p class="cmt">Du mardi au jeudi, l\'article reste « à commander » à la cible pleine, avec un jour de livraison réaliste. Le plancher n\'apparaît que lorsque plus aucune livraison tenable ne précède le besoin.</p>')
    parts.append(c["trigger"])
    parts.append(c["floor"])
    parts.append(c["offday"])

    # ---------------------------------------------------------------- Ferme
    parts.append('''<h3 id="ferme">2. Ferme</h3>
<p>La cellule affiche la <b>quantité commandée</b> des commandes fermes ERP du jour ; le calcul, lui, compte le <b>restant ERP</b> à partir d'aujourd'hui, et rien pour le passé. Un clic (à partir d'aujourd'hui) ignore les commandes du jour ; un second les rétablit.</p>
<div class="legend">
<div><span class="k">''' + cell("1 600", "firm-open") + '''</span><span>orange = en cours (restant ERP &gt; 0)</span></div>
<div><span class="k">''' + cell("400 / 1 600", "firm-open") + '''</span><span>livraison partielle : restant / commandé</span></div>
<div><span class="k">''' + cell("1 600", "firm-done") + '''</span><span>vert = soldée (restant nul)</span></div>
<div><span class="k">''' + cell("1 600", "firm-ignored") + '''</span><span>rouge barré = ignorée d'un clic (hors projection, hors plan)</span></div>
</div>
<p><b>En retard</b> = somme du restant ERP des commandes fermes dont la livraison est passée. Ce n'est ni une alerte ni un statut : c'est une colonne du cockpit et une carte du flux, une ligne par commande, qui disparaît dès que l'ERP solde la commande. Si vous l'attendez encore, tapez la quantité dans Appro. au jour prévu.</p>''')

    # ---------------------------------------------------------------- Reçu
    parts.append('''<h3 id="recu">3. Reçu</h3>
<p>Une ligne de réception ERP est un <b>journal d'arrivée</b>. Seules entrent dans la ligne Reçu et dans le stock les réceptions d'une <b>commande ouverte</b> (CO-…) ou d'une <b>commande fermée (PO, six chiffres) déclarée</b> dans Référentiel › Commandes fermées (PO). Les autres restent visibles dans Recherche, les flux et la fiche fournisseur.</p>''')
    parts.append(table(["Statut ERP", "Cellule", "Compté ?", "Dans le cockpit"], [
        ["En transit (journal non validé)", cell("🚚 <i>800</i>", "transit"), "non, jamais", "carte En transit"],
        ["Reçu (journal validé, ACR à valider)", cell("1 600", "received"), "oui", "carte Reçu le jour même ; carte À valider après 7 jours sans ACR"],
        ["Comptabilisé (ACR validé)", cell("1 600", "booked"), "oui", "carte Reçu le jour même"],
        ["DESADV annoncé sans journal", cell("🚚 <i>800</i>", "transit"), "non", "carte En transit ; En erreur ou sans journal de saisie : carte À traiter"],
    ]))
    parts.append('''<div class="legend">
<div><span class="k"><span class="dot ok"></span></span><span>point vert = le BL est annoncé par un DESADV traité / OK</span></div>
<div><span class="k"><span class="dot ko"></span></span><span>point rouge = aucun DESADV traité (réception manuelle, DESADV absent, en erreur, annulé) ; jamais rouge pour une PO déclarée</span></div>
</div>
<p>Le fond (transit, reçu, comptabilisé) et le point (DESADV) sont indépendants. Un en transit se <b>masque</b> par double-clic confirmé (rétablissable dans Saisies &amp; journal). L'info-bulle détaille chaque ligne du jour : BL, quantité, état d'ACR, origine.</p>''')

    # ---------------------------------------------------------------- Appro
    parts.append('''<h3 id="appro">4. Appro.</h3>
<div class="legend">
<div><span class="k">''' + cell("1 600", "erp") + '''</span><span>vide = la commande ferme ERP du jour (gris). Rien n'est stocké.</span></div>
<div><span class="k">''' + cell("1 200", "typed") + '''</span><span>chiffre = votre décision pour ce fournisseur ce jour : décaler, fractionner, annuler (0), livraison hors commande.</span></div>
<div><span class="k">''' + cell("0", "typed") + '''</span><span>0 = rien attendu ce jour. Vider la cellule = retour à l'ERP.</span></div>
<div><span class="k">''' + cell("1 440", "cbn") + '''</span><span>grisé = proposition CBN dans une cellule vide sans ERP.</span></div>
</div>
<ul>
<li>Une cellule datée avant aujourd'hui a <b>expiré</b> : ignorée, listée « expirée » dans Saisies &amp; journal.</li>
<li>Colonne semaine : la saisie se place au lundi (ou aujourd'hui dans la semaine en cours). Recopie : tirer le carré de la cellule active, comme Excel. Clic droit : commentaire.</li>
<li>Quantité ou expression (<span class="cell">2*600-50</span>). Chaque geste s'annule : toast « Annuler », Ctrl+Z / Ctrl+Y, historique ↶ ↷ (7 jours).</li>
</ul>''')

    # ---------------------------------------------------------------- Stock projeté
    parts.append('''<h3 id="stock">5. Stock projeté</h3>
<p>Une seule projection dans le tableau, le <b>Projeté Appro.</b> : stock de référence + réceptions + commandes fermes restantes (sauf cellule saisie) + cellules Appro. + propositions CBN + ajustements − besoin. Le <b>Projeté Ferme</b> (stock, réceptions et commandes fermes seuls, sans plan ni CBN) reste un indicateur : rupture ferme de la fiche article, colonne du cockpit, impact programmes.</p>
<dl class="rule">
<dt>Formule</dt><dd>stock du jour = stock de la veille + entrées + ajustements − besoin, à partir du stock de référence (point zéro) corrigé des ajustements.</dd>
<dt>Jamais négatif</dt><dd>le besoin non servi est <b>perdu</b>, jamais reporté. La cellule affiche le stock physique et, en fond rouge foncé, le <b>manque cumulé −Σ</b> depuis le dernier jour positif, remis à zéro dès que le stock redevient positif.</dd>
<dt>Couverture</dt><dd>en petit dans chaque cellule : jours ouvrés futurs dont le besoin cumulé est couvert par le stock du jour.</dd>
<dt>Passé</dt><dd>du point zéro à hier, reconstitué (réceptions, ajustements, consommation réelle) : gris, sans couverture.</dd>
</dl>
<div class="legend">
<div><span class="k">''' + cell("3 022", "cov-green", "14 j") + '''</span><span>vert : au-dessus du seuil orange</span></div>
<div><span class="k">''' + cell("1 210", "cov-orange", "5 j") + '''</span><span>orange : couverture ≤ seuil orange (ex. 5 j)</span></div>
<div><span class="k">''' + cell("260", "cov-red", "1 j") + '''</span><span>rouge : couverture ≤ seuil rouge (ex. 2 j)</span></div>
<div><span class="k">''' + cell("0 · −Σ 300", "neg") + '''</span><span>rupture : stock 0 et manque cumulé</span></div>
<div><span class="k">''' + cell("9 800", "", "42 j") + '''</span><span>neutre : ≥ seuil de surstock (ex. 30 j)</span></div>
</div>''')
    parts.append(table(["Jour", "Besoin", "Entrées", "Ajustement", "Projeté Appro.", "Commentaire"], [
        ["lun.", "100", "", "", cell("200", "cov-red", "2 j"), "→ stock de la veille 300 − 100"],
        ["mar.", "100", "", "", cell("100", "cov-red", "1 j"), "→ dernier jour couvert"],
        ["mer.", "100", "", "", cell("0 · −Σ 0", "cov-red", "0 j"), "→ exactement servi, stock nul"],
        ["jeu.", "100", "", "", cell("0 · −Σ 100", "neg"), "→ besoin non servi, perdu"],
        ["ven.", "100", "", "−50", cell("0 · −Σ 250", "neg"), "→ l'ajustement (casse) s'ajoute au manque"],
        ["lun.", "100", "500 (CBN)", "", cell("400", "cov-green", "4 j"), "→ le manque est remis à zéro"],
    ], num={1, 2, 3}))
    parts.append(c["shortage"])

    # ---------------------------------------------------------------- Multi-sourcing
    parts.append('''<h3 id="multi">6. Multi-sourcing</h3>
<p>Les voies du tableau sont les <b>liens actifs</b> article ↔ fournisseur, par priorité. Les flux d'un fournisseur sans lien actif restent hors du tableau (visibles dans Flux, Recherche et la fiche fournisseur) ; une commande ferme à venir chez lui remonte dans « Données à corriger ».</p>
<dl class="rule">
<dt>Quotas (défaut)</dt><dd>chaque besoin est <b>partagé</b> au prorata des quotas ; chaque part a son MOQ, son UM, son délai, ses jours de livraison, sa marge et ses plafonds. Les parts d'un même besoin forment une proposition par fournisseur.</dd>
<dt>Priorité</dt><dd>tout va au fournisseur de priorité 1 ; les autres ne reçoivent rien.</dd>
<dt>Plafond atteint</dt><dd>la part qu'un fournisseur ne peut pas livrer passe à l'autre fournisseur à quota <b>le même jour</b>, avant de glisser.</dd>
<dt>Refus</dt><dd>une part refusée d'un clic passe aux autres fournisseurs à quota ; en mono-source, le besoin glisse au jour de livraison suivant.</dd>
<dt>Statut</dt><dd>suit la <b>date limite la plus proche</b> parmi les parts ; la colonne « Commande à placer » nomme ce fournisseur.</dd>
</dl>''')
    parts.append(table(["Besoin net", "Fournisseur A · 60 % · lundi · MOQ 200 · UM 100", "Fournisseur B · 40 % · jeudi · MOQ 300 · UM 50", "Commentaire"], [
        ["1 000", cell("600", "cbn"), cell("400", "cbn"), "→ partage 60 / 40, chaque part lotie"],
        ["500", cell("300", "cbn"), cell("300", "cbn"), "→ 200 relevés au MOQ 300 de B"],
        ["1 000, A plafonné à 400 / livraison", cell('400 <span class="mk cap">▲</span>', "cbn"), cell("600", "cbn"), "→ l'excédent de A passe à B le même jour"],
        ["1 000, B refusé jeudi (clic)", cell("1 000", "cbn"), cell("400", "cbn refused"), "→ tout à A ; B redevient possible le jeudi suivant"],
    ]))
    parts.append(c["quota"])
    parts.append("</section>")

    # ============================================================== PARTIE B
    parts.append('''<section class="part" id="cockpit">
<h2><span class="part">Partie B</span>Tableau de bord : Cockpit du jour</h2>
<p class="lead">Deux onglets. <b>Couvertures et stocks</b> répond à « que dois-je faire aujourd'hui ? » article par article ; <b>Flux d'approvisionnement</b> suit les commandes, les transports et les réceptions du jour.</p>
<h3 id="stocks">7. Onglet Couvertures et stocks</h3>
<p>Cinq tuiles qui filtrent le portefeuille d'un clic. Les tuiles se lisent sur deux indicateurs : le <b>stock à date</b> (fin de la veille) et la <b>couverture à date</b> (jours ouvrés de besoin couverts par ce stock seul, sans aucune livraison), comparés aux seuils de l'article.</p>
''')
    parts.append(coverage_scale())
    parts.append('''<div class="tiles">
<div class="tile critical"><h4>Critiques</h4><p>couverture à date ≤ seuil rouge + 1 ; <b>ou</b> une proposition CBN urgente (date limite dépassée) ; <b>ou</b> stock de départ négatif.</p></div>
<div class="tile warning"><h4>À commander</h4><p>une date limite de commande tombe dans les <b>5 prochains jours ouvrés</b> ; <b>ou</b> couverture à date ≤ seuil orange et aucune livraison ferme ni cellule Appro. n'arrive avant l'épuisement.</p></div>
<div class="tile info"><h4>Surstock</h4><p>couverture à date ≥ seuil de surstock. Un <b>filtre</b>, pas un statut : l'article reste OK, À commander ou Critique par ailleurs.</p></div>
<div class="tile brand"><h4>Données</h4><p>pas de stock de départ ; pas de lien fournisseur actif ; absent des nomenclatures ; commande ferme à venir chez un fournisseur sans lien. <b>Pastille bleue</b> sur le statut, jamais dans le statut.</p></div>
<div class="tile ok"><h4>OK</h4><p>tout le reste : rien à faire aujourd'hui.</p></div>
</div>
<h4>Le portefeuille</h4>
<p>Colonnes principales : article, fournisseurs (liens actifs), approvisionneur, statut, stock à date (cible en dessous), couverture à date (cible en dessous), <b>commande en cours</b> (prochaine ferme : date, fournisseur, restant), <b>commande à placer</b> (première proposition : date limite, fournisseur retenu, quantité, livraison, ◇ si hors jour standard), en retard. Les colonnes secondaires (couverture Appro., rupture Appro., rupture ferme, en-cours, valeur, alertes) sont dans le menu Colonnes.</p>
<h4>Exemple : six articles, seuils rouge 2 j, orange 5 j, surstock 30 j, cible 10 j</h4>''')
    parts.append(table(["Article", "Couverture à date", "Livraison avant l'épuisement ?", "Commande à placer", "Statut", "Commentaire"], [
        ["P-001", chip("1 j", "critical"), "oui, ferme demain", "–", chip("Critique", "critical"), "→ la couverture rouge prime, même si une livraison arrive"],
        ["P-002", chip("4 j", "warning"), "non", "–", chip("À commander", "warning"), "→ orange et rien n'arrive avant l'épuisement"],
        ["P-003", chip("4 j", "warning"), "oui, Appro. saisie jeudi", "–", chip("OK", "ok"), "→ orange mais couvert : rien à faire"],
        ["P-004", chip("12 j", "ok"), "–", "au plus tard mer. 28/10 · A · 1 440 · liv. 3/11 ◇", chip("À commander", "warning"), "→ date limite sous 5 j ouvrés"],
        ["P-005", chip("12 j", "ok"), "–", "au plus tard 27/10, dépassée · plancher !", chip("Critique", "critical"), "→ une urgence CBN rend l'article critique"],
        ["P-006", chip("42 j", "neutral"), "–", "–", chip("OK", "ok") + " + Surstock", "→ filtré par la tuile Surstock, sans lien actif : pastille Données"],
    ]))
    parts.append('<p class="cmt">Sous les tuiles : une ligne de contexte (couverture médiane, valeur du stock contre la valeur cible, en-cours ERP, articles en retard), puis la projection de la valeur du stock et les alertes prioritaires.</p>')

    # ---------------------------------------------------------------- Flux
    parts.append('''<h3 id="flux">8. Onglet Flux d'approvisionnement</h3>
<p>Huit cartes ; un clic affiche les lignes correspondantes en dessous, avec un bouton Excel. Les cartes se calculent sur la copie ERP du jour (synchronisation toutes les heures, bandeau orange si elle est en retard).</p>
<div class="tiles">
<div class="tile critical"><h4>À commander</h4><p>propositions CBN dont la <b>date limite tombe dans les 5 prochains jours ouvrés</b>, les <b>urgentes</b> (date limite dépassée) en tête et marquées <span class="mk urgent">!</span> ; colonne « au plus tard le », livraison avec ◇ si hors jour standard. Une proposition refusée reste listée, marquée <i>ignorée</i>, hors calculs.</p></div>
<div class="tile brand"><h4>Commandé</h4><p>commandes fermes <b>apparues aujourd'hui</b> dans la copie ERP, quelle que soit leur date de livraison.</p></div>
<div class="tile info"><h4>En transit</h4><p>journaux d'arrivée <b>non validés</b>, plus les DESADV non annulés qu'aucun journal ne porte encore.</p></div>
<div class="tile warning"><h4>À traiter</h4><p>DESADV en <b>erreur</b>, ou traités <b>sans journal de saisie</b>.</p></div>
<div class="tile brand"><h4>À réceptionner</h4><p>commandes fermes dont la livraison est attendue <b>aujourd'hui</b>.</p></div>
<div class="tile ok"><h4>Reçu</h4><p>réceptions (journaux validés) datées d'aujourd'hui.</p></div>
<div class="tile warning"><h4>En retard</h4><p>commandes fermes passées avec un restant ERP, <b>une ligne par commande</b> : numéro, date prévue, commandé, reçu, restant.</p></div>
<div class="tile warning"><h4>À valider</h4><p>réceptions au statut Reçu (journal validé, ACR non validé) depuis <b>7 jours</b> ou plus.</p></div>
</div>
<h4>Exemple : une journée, mercredi 28/10</h4>''')
    parts.append(table(["Ligne du jour", "Carte", "Commentaire"], [
        ["Proposition A · 1 440 · au plus tard aujourd'hui · liv. mar. 3/11 ◇", chip("À commander", "critical"), "→ date limite sous 5 j ouvrés ; ◇ = mardi au lieu du lundi"],
        ["Proposition B · 240 ! · au plus tard hier · liv. 4/11", chip("À commander", "critical") + " en tête", "→ urgente : plancher, date limite dépassée"],
        ["Proposition C refusée d'un clic lundi", chip("À commander", "critical") + " <i>ignorée</i>", "→ listée pour mémoire, hors calculs"],
        ["CO-10234 créée ce matin, livraison le 16/11", chip("Commandé", "brand"), "→ apparue aujourd'hui dans l'ERP"],
        ["Journal d'arrivée BL-990002 non validé", chip("En transit", "info"), "→ jamais compté dans le stock"],
        ["DESADV BL-990003 en erreur", chip("À traiter", "warning"), "→ à corriger dans l'ERP"],
        ["CO-10001 attendue aujourd'hui, restant 800", chip("À réceptionner", "brand"), "→ ce soir sans journal, elle sera en retard demain"],
        ["BL-990000 validé ce matin (ACR à valider)", chip("Reçu", "ok"), "→ compté dans le stock ; sans ACR dans 7 jours : À valider"],
        ["CO-09980 prévue le 20/10, restant 400", chip("En retard", "warning"), "→ une ligne par commande, sort dès que l'ERP solde"],
    ]))
    parts.append("</section>")

    # ============================================================== MÉMO
    parts.append('''<section class="part" id="memo">
<h2><span class="part">Mémo</span>Marqueurs, couleurs et gestes en un coup d'œil</h2>''')
    parts.append(table(["Où", "Signe", "Sens"], [
        ["Proposition CBN", '<span class="mk urgent">!</span>', "urgente : plancher, date limite dépassée → article Critique, carte À commander en tête"],
        ["Proposition CBN", '<span class="mk cap">▲</span>', "plafonnée par le fournisseur (max par livraison ou par semaine)"],
        ["Proposition CBN, cockpit", '<span class="mk off">◇</span>', "hors jour standard : livraison sur un jour ouvré tenable, pas sur le jour de livraison habituel"],
        ["Proposition CBN", "barré", "refusée d'un clic, ce fournisseur ce jour-là"],
        ["Ferme", "orange / vert / rouge barré", "en cours / soldée / ignorée d'un clic"],
        ["Reçu", "italique orange 🚚 / blanc / vert", "en transit (jamais compté) / reçu (compté, ACR à valider) / comptabilisé"],
        ["Reçu", '<span class="dot ok"></span> / <span class="dot ko"></span>', "BL annoncé par un DESADV traité / aucun DESADV traité"],
        ["Appro.", "gris / bleu", "ERP (vide) / votre saisie (0 = rien attendu)"],
        ["Projeté Appro.", "petit chiffre vert · orange · rouge · neutre", "couverture en jours ouvrés : normale · ≤ orange · ≤ rouge · ≥ surstock"],
        ["Projeté Appro.", "fond rouge foncé −Σ", "manque cumulé du besoin non servi"],
        ["Statut", chip("Critique", "critical") + " " + chip("À commander", "warning") + " " + chip("OK", "ok"), "couverture rouge ou urgence / date limite sous 5 j ouvrés ou orange non couvert / rien à faire"],
        ["Statut", "pastille bleue", "données à corriger (stock de départ, lien, nomenclature, commande hors lien)"],
        ["Partout", "Ctrl+Z · Ctrl+Y · ↶ ↷", "annuler / rétablir chaque geste, 7 jours, dans l'ordre"],
    ]))
    parts.append('''<div class="callout warn"><b>Règles communes réglables</b> (Paramètres) : tolérance de déclenchement (15 % → 85 % de la cible) et marge de délai (1 jour ouvré). <b>Par article</b> (fiche › Paramétrage, et par semaine ISO) : couverture cible, seuils rouge et orange, surstock, jours de consommation, source de consommation, politique de multi-sourcing. <b>Par fournisseur</b> : délai, jours de livraison standard, jours stricts. <b>Par lien</b> : MOQ, UM, quota, priorité, délai spécifique, plafonds.</div>
</section>''')
    parts.append('<footer>Ma Routine Appro · pense-bête de l\'approvisionneur · les règles détaillées avec leurs calculs sont dans <i>docs/regles_metier.md</i> et <i>docs/guide_moteur.md</i>.</footer>')
    parts.append("</main></div>")
    return "\n".join(parts)


if __name__ == "__main__":
    OUT.write_text(page(), encoding="utf-8")
    print(OUT, OUT.stat().st_size)
