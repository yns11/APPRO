import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight, Eye, EyeOff, Lock } from "lucide-react";
import { api } from "@/lib/api";
import { useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { useToast } from "@/components/ui";
import { ORDER_TYPE_LABELS, fmtDate, fmtQty, isSaturday, isSunday, isWeekKey, isWeekend, isoWeekOf, periodLabel } from "@/lib/format";
import type { DesadvInfo, AdjustmentOut, ArticleRef, FlagIn, FlagOut, LaneOut, PlanCellOut, SeriesOut } from "@/lib/types";

/** Rows of the grid that can be hidden (for every article) ; lane rows are repeated per supplier. */
export const ROW_LABELS: { key: string; label: string; lane?: boolean }[] = [
  { key: "demand", label: "Besoin" },
  { key: "orders_firm", label: "Ferme", lane: true },
  { key: "orders_forecast", label: "Prévisionnel", lane: true },
  { key: "receipts", label: "Reçu", lane: true },
  { key: "plan", label: "Plan", lane: true },
  { key: "supply_proposed", label: "Proposition CBN", lane: true },
  { key: "adjustments", label: "Ajustement" },
  { key: "stock_erp", label: "Scenario ERP" },
  { key: "stock_plan", label: "Scenario Plan" },
];

export interface GridColumns { as_of: string; init_date: string; periods: string[]; period_start: string[]; period_end: string[]; }
export interface GridRowArticle { article: ArticleRef; series: SeriesOut[]; lanes: LaneOut[]; kpis?: { severity?: string | null }; }

/* ---------------------------------------------------------------- geometry (virtualisation) */
const COL_W = 72;          // every data column has the same width: the visible range is arithmetic
const LABEL_W = 150;       // sticky first column
const ROW_H = 26;
const STOCK_H = 36;        // stock cells carry the coverage above the value
const HEAD_H = 30;
const OVERSCAN_COLS = 4;
const OVERSCAN_PX = 120;

/** index of the column containing a date (columns are sorted, day or ISO-week ranges) */
export function columnOf(cols: GridColumns, date: string): number {
  const s = cols.period_start, e = cols.period_end;
  let lo = 0, hi = s.length - 1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (date < s[mid]) hi = mid - 1;
    else if (date > (e[mid] ?? "9999-12-31")) lo = mid + 1;
    else return mid;
  }
  return -1;
}

/** Coverage colour of a stock cell: red / yellow thresholds, green = normal, no colour = overstock. */
export function coverageClass(days: number, a: ArticleRef): string {
  if (days <= a.alert_red_days) return "cov-red";
  if (days <= a.alert_yellow_days) return "cov-yellow";
  if (days >= a.overstock_days) return "cov-over";
  return "cov-ok";
}

/** ``initial`` is what the cell showed before editing: nothing is saved when the value is unchanged. */
type Edit = { aid: string; key: "plan" | "adjustments"; lane: number; i: number; value: string; initial: string };
/** Fill handle drag (Excel-like copy along the row). */
type Fill = { aid: string; key: "plan" | "adjustments"; lane: number; from: number; to: number; value: string };

type RowKind = "article" | "group" | "demand" | "firm" | "fcst" | "rec" | "plan" | "cbn" | "adj" | "stock";
interface RowDesc { kind: RowKind; a: GridRowArticle; li: number; h: number; sk?: "stock_erp" | "stock_plan"; key: string }

function laneSeries(l: LaneOut, k: string): number[] | undefined { return l.series.find((s) => s.key === k)?.values; }

/**
 * The simulation grid: one column per day or ISO week, one block per article.  Rows and columns are
 * **virtualised**: only the cells in view are in the DOM, whatever the number of articles / days.
 *
 * Two rows are typed, nothing else: **Plan** (one row per supplier ; empty = the ERP quantity of
 * the day, a typed value = the planner's decision, 0 included, a blank restores the ERP) and
 * **Ajustement** (signed, any date).  Both rows have a **fill handle** (small square at the corner of
 * the active cell): dragging it along the row copies the value, like Excel.
 *
 * Two rows are clickable: **Ferme** (a click ignores the firm orders of that supplier and day: red,
 * struck, out of the ERP scenario and of the plan ; click again to restore) and **Proposition CBN**
 * (a click refuses the proposal: struck, no proposal until the end of its week ; click again).
 * A CBN proposal is shown greyed in an empty plan cell: one click then Enter takes it.  Right-click
 * on a typed cell adds a comment.  Week columns are read-only: clicking one switches to the day view.
 */
export function SimulationGrid({ cols, articles, planCells, adjustments, flags = [], showArticleRows, onSwitchDay }: {
  cols: GridColumns; articles: GridRowArticle[]; planCells: PlanCellOut[]; adjustments: AdjustmentOut[]; flags?: FlagOut[];
  showArticleRows?: boolean; onSwitchDay?: (date: string) => void;
}) {
  const toast = useToast();
  const { perimeter, set, rights } = usePerimeter();
  const hidden = useMemo(() => new Set(perimeter.hiddenRows), [perimeter.hiddenRows]);
  /** read-only article: outside the user's portfolio (the server refuses the writes too) */
  const lockedOf = useCallback((a: GridRowArticle) => !rights.canEditPlanner(a.article.planner), [rights]);
  const LOCK = "Lecture seule : article hors de votre carnet";
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [menu, setMenu] = useState(false);
  const [editing, setEditing] = useState<Edit | null>(null);
  const [active, setActive] = useState<Omit<Edit, "value" | "initial"> | null>(null);
  const [fill, setFill] = useState<Fill | null>(null);
  const fillRef = useRef<Fill | null>(null);

  const saveAdj = useWrite((c: { article_id: string; date: string; expression?: string; note?: string }) => api.put<AdjustmentOut | null>("/api/entries/adjustments", c),
    (out) => toast.push(out ? `Ajustement ${fmtDate(out.date)} = ${fmtQty(out.qty)}` : "Ajustement effacé", "success"));
  const savePlan = useWrite((c: { article_id: string; supplier_id: string | null; date: string; expression?: string; note?: string }) => api.put<PlanCellOut | null>("/api/entries/plan", c),
    (out) => toast.push(out ? `Plan ${fmtDate(out.date)} = ${fmtQty(out.qty)}` : "Retour à l'ERP", "success"));
  const savePlanBatch = useWrite((cells: { article_id: string; supplier_id: string | null; date: string; expression: string }[]) => api.put<PlanCellOut[]>("/api/entries/plan/batch", { cells }),
    (out) => toast.push(`${out.length} cellule(s) du plan recopiée(s)`, "success"));
  const saveAdjBatch = useWrite((cells: { article_id: string; date: string; expression: string }[]) => api.put<AdjustmentOut[]>("/api/entries/adjustments/batch", { cells }),
    (out) => toast.push(`${out.length} ajustement(s) recopié(s)`, "success"));
  const toggleFlag = useWrite((f: FlagIn) => api.post<FlagOut | null>("/api/entries/flags/toggle", f),
    (out) => toast.push(out ? (out.kind === "order_ignored" ? `Commande ferme du ${fmtDate(out.date)} ignorée` : `Proposition du ${fmtDate(out.date)} refusée jusqu'à la fin de sa semaine`) : "Rétabli", "success"));

  /* ---------------- lookups */
  const n = cols.periods.length;
  const asOfIdx = useMemo(() => columnOf(cols, cols.as_of), [cols]);
  const initIdx = useMemo(() => columnOf(cols, cols.init_date), [cols]);
  const planBy = useMemo(() => {
    const m = new Map<string, PlanCellOut>();
    planCells.forEach((c) => { const i = columnOf(cols, c.date); if (i >= 0) m.set(`${c.article_id}|${c.supplier_id}|${i}`, c); });
    return m;
  }, [planCells, cols]);
  const adjBy = useMemo(() => {
    const m = new Map<string, AdjustmentOut>();
    adjustments.forEach((c) => { const i = columnOf(cols, c.date); if (i >= 0) m.set(`${c.article_id}|${i}`, c); });
    return m;
  }, [adjustments, cols]);
  const flagBy = useMemo(() => {
    const m = new Map<string, FlagOut>();
    flags.forEach((f) => { const i = columnOf(cols, f.date); if (i >= 0) m.set(`${f.kind}|${f.article_id}|${f.supplier_id}|${i}`, f); });
    return m;
  }, [flags, cols]);
  const readOnlyCol = useCallback((i: number) => isWeekKey(cols.periods[i]), [cols]);
  /** displayed columns → original column indexes (Saturdays / Sundays can be hidden in day mode) */
  const shown = useMemo(() => {
    const out: number[] = [];
    cols.periods.forEach((p, i) => {
      if (!isWeekKey(p)) {
        if (!perimeter.showSaturday && isSaturday(p)) return;
        if (!perimeter.showSunday && isSunday(p)) return;
      }
      out.push(i);
    });
    return out;
  }, [cols, perimeter.showSaturday, perimeter.showSunday]);
  const m = shown.length;
  const hasDayCols = useMemo(() => cols.periods.some((p) => !isWeekKey(p)), [cols]);
  /** next displayed column from ``i`` in direction ``dir`` (Tab navigation skips hidden days) */
  const nextShown = useCallback((i: number, dir: 1 | -1): number => { const k = shown.indexOf(i); return k < 0 ? -1 : (shown[k + dir] ?? -1); }, [shown]);

  /* ---------------- rows (flattened, with heights) */
  const rows = useMemo<RowDesc[]>(() => {
    const out: RowDesc[] = [];
    for (const a of articles) {
      const aid = a.article.article_id;
      if (showArticleRows) out.push({ kind: "article", a, li: 0, h: ROW_H + 2, key: `${aid}-head` });
      if (!hidden.has("demand")) out.push({ kind: "demand", a, li: 0, h: ROW_H, key: `${aid}-demand` });
      a.lanes.forEach((_, li) => {
        out.push({ kind: "group", a, li, h: ROW_H - 4, key: `${aid}-lane-${li}` });
        if (collapsed[`${aid}-lane-${li}`]) return;
        if (!hidden.has("orders_firm")) out.push({ kind: "firm", a, li, h: ROW_H, key: `${aid}-firm-${li}` });
        if (!hidden.has("orders_forecast")) out.push({ kind: "fcst", a, li, h: ROW_H, key: `${aid}-fcst-${li}` });
        if (!hidden.has("receipts")) out.push({ kind: "rec", a, li, h: ROW_H, key: `${aid}-rec-${li}` });
        if (!hidden.has("plan")) out.push({ kind: "plan", a, li, h: ROW_H, key: `${aid}-plan-${li}` });
        if (!hidden.has("supply_proposed")) out.push({ kind: "cbn", a, li, h: ROW_H, key: `${aid}-cbn-${li}` });
      });
      if (!hidden.has("adjustments")) out.push({ kind: "adj", a, li: 0, h: ROW_H, key: `${aid}-adj` });
      if (!hidden.has("stock_erp")) out.push({ kind: "stock", a, li: 0, h: STOCK_H, sk: "stock_erp", key: `${aid}-stock_erp` });
      if (!hidden.has("stock_plan")) out.push({ kind: "stock", a, li: 0, h: STOCK_H, sk: "stock_plan", key: `${aid}-stock_plan` });
    }
    return out;
  }, [articles, hidden, collapsed, showArticleRows]);
  const offsets = useMemo(() => { const o = new Array<number>(rows.length + 1); o[0] = 0; rows.forEach((r, k) => { o[k + 1] = o[k] + r.h; }); return o; }, [rows]);

  /* ---------------- viewport */
  const wrap = useRef<HTMLDivElement>(null);
  const [view, setView] = useState({ sl: 0, st: 0, cw: 1200, ch: 600 });
  const raf = useRef(0);
  const onScroll = useCallback(() => {
    const el = wrap.current;
    if (!el) return;
    cancelAnimationFrame(raf.current);
    raf.current = requestAnimationFrame(() => setView({ sl: el.scrollLeft, st: el.scrollTop, cw: el.clientWidth, ch: el.clientHeight }));
  }, []);
  useLayoutEffect(() => {
    const el = wrap.current;
    if (!el) return;
    const ro = new ResizeObserver(onScroll);
    ro.observe(el);
    onScroll();
    return () => { ro.disconnect(); cancelAnimationFrame(raf.current); };
  }, [onScroll]);
  const c0 = Math.max(0, Math.floor(view.sl / COL_W) - OVERSCAN_COLS);
  const c1 = Math.min(m, Math.ceil((view.sl + view.cw - LABEL_W) / COL_W) + OVERSCAN_COLS);
  const totalH = offsets[rows.length];
  let r0 = 0, r1 = rows.length;
  { // binary searches on the row offsets
    const top = view.st - OVERSCAN_PX, bottom = view.st + view.ch + OVERSCAN_PX;
    let lo = 0, hi = rows.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (offsets[m + 1] <= top) lo = m + 1; else hi = m; }
    r0 = lo; lo = r0; hi = rows.length;
    while (lo < hi) { const m = (lo + hi) >> 1; if (offsets[m] < bottom) lo = m + 1; else hi = m; }
    r1 = lo;
  }
  const visibleCols = useMemo(() => shown.slice(c0, Math.max(c0, c1)), [shown, c0, c1]);
  const leftW = c0 * COL_W, rightW = (m - c1) * COL_W;
  const colCount = 1 + (leftW > 0 ? 1 : 0) + visibleCols.length + (rightW > 0 ? 1 : 0);

  /* ---------------- editing */
  const toggleRow = (key: string) => set({ hiddenRows: hidden.has(key) ? perimeter.hiddenRows.filter((k) => k !== key) : [...perimeter.hiddenRows, key] });
  const toggleGroup = (g: string) => setCollapsed((c) => ({ ...c, [g]: !c[g] }));
  const commit = (next?: Edit) => {
    if (editing && editing.value.trim() !== editing.initial.trim()) {
      const a = articles.find((x) => x.article.article_id === editing.aid);
      const date = cols.period_start[editing.i];
      if (editing.key === "adjustments") saveAdj.mutate({ article_id: editing.aid, date, expression: editing.value });
      else if (a) savePlan.mutate({ article_id: editing.aid, supplier_id: a.lanes[editing.lane].supplier_id, date, expression: editing.value });
    }
    setEditing(next ?? null);
  };
  const comment = (aid: string, key: "plan" | "adjustments", lane: LaneOut | null, i: number, current: string, currentValue: string) => {
    if (key === "adjustments" && !adjBy.get(`${aid}|${i}`)) { toast.push("Saisir d'abord une quantité d'ajustement dans la cellule", "info"); return; }
    const note = window.prompt("Commentaire de la cellule (vide pour effacer)", current);
    if (note === null) return;
    const date = cols.period_start[i];
    if (key === "adjustments") saveAdj.mutate({ article_id: aid, date, note });
    // a comment on an ERP cell turns it into a typed cell with the same quantity (0 when the ERP has nothing)
    else savePlan.mutate({ article_id: aid, supplier_id: lane?.supplier_id ?? null, date, note, ...(planBy.get(`${aid}|${lane?.supplier_id ?? ""}|${i}`) ? {} : { expression: currentValue || "0" }) });
  };

  /* ---------------- fill handle (copy along the row) */
  const startFill = (e: React.MouseEvent, aid: string, key: "plan" | "adjustments", lane: number, i: number, value: string) => {
    e.preventDefault(); e.stopPropagation();
    if (editing) { value = editing.value; commit(); }
    const f: Fill = { aid, key, lane, from: i, to: i, value };
    fillRef.current = f; setFill(f);
  };
  const extendFill = (i: number) => {
    const f = fillRef.current;
    if (f && f.to !== i) { fillRef.current = { ...f, to: i }; setFill(fillRef.current); }
  };
  useEffect(() => {
    if (!fill) return;
    const up = () => {
      const f = fillRef.current;
      fillRef.current = null; setFill(null);
      if (!f || f.to === f.from) return;
      const lo = Math.min(f.from, f.to), hi = Math.max(f.from, f.to);
      const targets = [] as number[];
      for (let i = lo; i <= hi; i++) if (i !== f.from && !readOnlyCol(i) && (f.key === "adjustments" || i >= asOfIdx)) targets.push(i);
      if (!targets.length) return;
      const a = articles.find((x) => x.article.article_id === f.aid);
      if (f.key === "plan" && a) savePlanBatch.mutate(targets.map((i) => ({ article_id: f.aid, supplier_id: a.lanes[f.lane].supplier_id, date: cols.period_start[i], expression: f.value })));
      else if (f.key === "adjustments") saveAdjBatch.mutate(targets.map((i) => ({ article_id: f.aid, date: cols.period_start[i], expression: f.value })));
    };
    window.addEventListener("mouseup", up);
    return () => window.removeEventListener("mouseup", up);
  }, [fill, articles, cols, asOfIdx, readOnlyCol, savePlanBatch, saveAdjBatch]);
  const inFill = (aid: string, key: string, lane: number, i: number) => !!fill && fill.aid === aid && fill.key === key && fill.lane === lane && i >= Math.min(fill.from, fill.to) && i <= Math.max(fill.from, fill.to);

  const input = (e: Edit, label: string, initial: (i: number) => string, canTab: (ni: number) => boolean) => (
    <input className="cell-input" autoFocus value={e.value} aria-label={label}
      onChange={(ev) => setEditing({ ...e, value: ev.target.value })} onBlur={() => { if (!fillRef.current) commit(); }}
      onKeyDown={(ev) => {
        if (ev.key === "Enter") { ev.preventDefault(); commit(); }
        else if (ev.key === "Escape") { ev.preventDefault(); setEditing(null); }
        else if (ev.key === "Tab") { ev.preventDefault(); const ni = nextShown(e.i, ev.shiftKey ? -1 : 1); if (ni >= 0 && ni < n && canTab(ni)) { const nv = initial(ni); setActive({ aid: e.aid, key: e.key, lane: e.lane, i: ni }); commit({ ...e, i: ni, value: nv, initial: nv }); } else commit(); }
      }} />
  );
  const handle = (aid: string, key: "plan" | "adjustments", lane: number, i: number, value: string) => (
    <span className="fill-handle" title="Tirer pour recopier la valeur sur les cellules voisines" onMouseDown={(e) => startFill(e, aid, key, lane, i, value)} />
  );

  /* ---------------- cells */
  const cellBase = (i: number): string[] => {
    const p = cols.periods[i];
    const past = i < asOfIdx;
    return [past || (!isWeekKey(p) && isWeekend(cols.period_start[i])) ? "past" : "", i === asOfIdx ? "today" : "", isWeekKey(p) ? "wkcol" : ""];
  };
  const spacerL = leftW > 0 ? <td key="sl" className="spacer" style={{ width: leftW, minWidth: leftW, maxWidth: leftW }} /> : null;
  const spacerR = rightW > 0 ? <td key="sr" className="spacer" style={{ width: rightW, minWidth: rightW, maxWidth: rightW }} /> : null;
  const plainRow = (r: RowDesc, label: ReactNode, cls: string, val: (i: number) => number, title?: (i: number) => string, extra?: (i: number) => string) => (
    <tr key={r.key} className={cls} style={{ height: r.h }}>
      <td>{label}</td>
      {spacerL}
      {visibleCols.map((i) => { const v = val(i); return <td key={i} className={[...cellBase(i), extra?.(i) ?? ""].filter(Boolean).join(" ")} title={title?.(i)}>{v === 0 ? "·" : fmtQty(v, r.a.article.unit)}</td>; })}
      {spacerR}
    </tr>
  );

  const renderRow = (r: RowDesc): ReactNode => {
    const a = r.a, aid = a.article.article_id, unit = a.article.unit, locked = lockedOf(a);
    const series = (k: string, i: number) => a.series.find((s) => s.key === k)?.values[i] ?? 0;
    switch (r.kind) {
      case "article":
        return (
          <tr key={r.key} className="article-head" style={{ height: r.h }}>
            <td><Link to={`/articles/${encodeURIComponent(aid)}`}>{aid}</Link> <span className="subtle" style={{ fontWeight: 400 }}>· {a.article.designation}</span>{locked && <span className="subtle" style={{ fontWeight: 400 }} title={LOCK}> · <Lock size={11} style={{ verticalAlign: "-1px" }} /> {a.article.planner}</span>}</td>
            <td colSpan={colCount - 1} />
          </tr>
        );
      case "group": {
        const l = a.lanes[r.li], several = a.lanes.length > 1, g = `${aid}-lane-${r.li}`;
        const label = several ? `${l.supplier_id ?? "Sans fournisseur"}${l.name ? ` · ${l.name}` : ""}` : `ERP & plan${l.supplier_id ? ` · ${l.supplier_id}` : ""}`;
        return (
          <tr key={r.key} className="group-head" style={{ height: r.h }} onClick={() => toggleGroup(g)} title={collapsed[g] ? "Afficher le bloc" : "Masquer le bloc"}>
            <td>{collapsed[g] ? <ChevronRight size={12} /> : <ChevronDown size={12} />}{label}{l.backlog_qty > 0 ? ` · backlog ${fmtQty(l.backlog_qty, unit)}` : ""}</td>
            <td colSpan={colCount - 1} />
          </tr>
        );
      }
      case "demand":
        return plainRow(r, "Besoin", "", (i) => series("demand", i), (i) => (i < asOfIdx ? "consommé (réel × nomenclature)" : "requis (PDP × nomenclature)"));
      case "firm": {
        const l = a.lanes[r.li];
        const ordered = laneSeries(l, "orders_firm_ordered"), open = laneSeries(l, "orders_firm_open");
        const ordersTitle = (i: number) => l.orders.filter((o) => o.order_type === "FIRM" && columnOf(cols, o.expected_date) === i)
          .map((o) => `${fmtQty(o.qty_open, unit)} restant / ${fmtQty(o.qty_ordered, unit)} commandé${o.ref ? ` · ${o.ref}` : ""}${o.ignored ? " · ignorée" : ""}`).join("\n");
        return (
          <tr key={r.key} style={{ height: r.h }}>
            <td>Ferme</td>
            {spacerL}
            {visibleCols.map((i) => {
              const qo = ordered?.[i] ?? 0, qr = open?.[i] ?? 0;
              const ignored = !!l.orders_ignored[i];
              const partial = qr > 0 && qr < qo;
              const past = i < asOfIdx, ro = readOnlyCol(i);
              const clickable = qo > 0 && !past && !locked;
              const cls = [...cellBase(i), qo > 0 ? (ignored ? "ignored" : qr > 0 ? "firm-open" : "firm-settled") : "", clickable ? "clickable" : "", l.orders.length && ordersTitle(i) ? "event" : ""].filter(Boolean).join(" ");
              const text = qo === 0 ? "·" : partial ? <span className="partial">{fmtQty(qr, unit)} / {fmtQty(qo, unit)}</span> : fmtQty(qo, unit);
              const state = qo === 0 ? "" : ignored ? "Commande ignorée : hors Scenario ERP et hors Plan (cliquer pour la rétablir)" : qr > 0 ? `En cours : ${fmtQty(qr, unit)} restant à livrer sur ${fmtQty(qo, unit)} commandé${clickable ? (ro ? " (semaine agrégée : cliquer l'en-tête pour le détail)" : " – cliquer pour l'ignorer") : ""}` : `Soldée : ${fmtQty(qo, unit)} commandé, tout reçu`;
              const title = [state, ordersTitle(i)].filter(Boolean).join("\n");
              const onClick = !clickable ? undefined : ro ? () => onSwitchDay?.(cols.period_start[i]) : () => toggleFlag.mutate({ article_id: aid, supplier_id: l.supplier_id, date: cols.period_start[i], kind: "order_ignored" });
              return <td key={i} className={cls} title={title} onClick={onClick}>{ignored ? <s>{text}</s> : text}</td>;
            })}
            {spacerR}
          </tr>
        );
      }
      case "fcst": {
        const l = a.lanes[r.li], fc = laneSeries(l, "orders_forecast");
        const t = (i: number) => l.orders.filter((o) => o.order_type !== "FIRM" && columnOf(cols, o.expected_date) === i).map((o) => `${ORDER_TYPE_LABELS[o.order_type]} ${fmtQty(o.qty_open, unit)} restant / ${fmtQty(o.qty_ordered, unit)} commandé${o.ref ? ` · ${o.ref}` : ""}`).join("\n");
        return plainRow(r, "Prévisionnel", "", (i) => fc?.[i] ?? 0, t, (i) => (t(i) ? "event" : ""));
      }
      case "rec": {
        const l = a.lanes[r.li], rc = laneSeries(l, "receipts"), dv = laneSeries(l, "desadv_open");
        const sid = l.supplier_id ?? "";
        const inCol = (d: DesadvInfo, i: number) => columnOf(cols, d.issue_date) === i;
        const openOf = (i: number) => l.desadv.filter((d) => inCol(d, i) && !d.received && !d.hidden);
        return (
          <tr key={r.key} style={{ height: r.h }}>
            <td>Reçu</td>
            {spacerL}
            {visibleCols.map((i) => {
              const v = rc?.[i] ?? 0, open = dv?.[i] ?? 0;
              const ro = readOnlyCol(i);
              const hasDesadvData = l.desadv.length > 0;
              const recKo = !!l.receipts_ko?.[i], dvKo = !!l.desadv_ko?.[i];
              const openList = open > 0 ? openOf(i) : [];
              const title = [
                v > 0 ? `Reçu ${fmtQty(v, unit)}${hasDesadvData ? (recKo ? " – point rouge : un BL reçu sans DESADV traité" : " – point vert : BL annoncé par un DESADV traité") : ""}` : "",
                open > 0 ? `DESADV non reçu ${fmtQty(open, unit)} (annoncé, hors calculs) : ${openList.map((d) => `BL ${d.packing_slip} ${fmtQty(d.qty, unit)} · ${d.state}${d.final_processing ? ` / ${d.final_processing}` : ""}${d.purch_id ? ` · ${d.purch_id}` : ""}`).join(" ; ")}${ro ? " – semaine agrégée : cliquer l'en-tête pour le détail" : locked ? "" : " – double-clic pour le masquer"}` : "",
              ].filter(Boolean).join("\n");
              const cls = [...cellBase(i), open > 0 ? "desadv" : "", open > 0 && !ro && !locked ? "clickable" : ""].filter(Boolean).join(" ");
              const onDouble = open <= 0 || locked ? undefined : ro ? () => onSwitchDay?.(cols.period_start[i])
                : () => { if (window.confirm(`Masquer le DESADV non reçu du ${fmtDate(cols.period_start[i])} (${fmtQty(open, unit)}, BL ${openList.map((d) => d.packing_slip).join(", ")}) ?\nIl ne sera plus affiché dans le tableau ; rétablissable depuis Saisies & journal.`)) toggleFlag.mutate({ article_id: aid, supplier_id: sid || null, date: cols.period_start[i], kind: "desadv_hidden", qty: open, note: openList.map((d) => d.packing_slip).join(", ") }); };
              return (
                <td key={i} className={cls} title={title} onDoubleClick={onDouble}>
                  {v > 0 && <>{fmtQty(v, unit)}{hasDesadvData && <span className={`dot ${recKo ? "ko" : "ok"}`} />}</>}
                  {v > 0 && open > 0 && <br />}
                  {open > 0 && <span className="desadv-qty">{fmtQty(open, unit)}<span className={`dot ${dvKo ? "ko" : "ok"}`} /></span>}
                  {v === 0 && open === 0 && "·"}
                </td>
              );
            })}
            {spacerR}
          </tr>
        );
      }
      case "plan": {
        const l = a.lanes[r.li], li = r.li;
        const pl = laneSeries(l, "plan"), cb = laneSeries(l, "supply_proposed");
        const shownOf = (ni: number) => { const c = planBy.get(`${aid}|${l.supplier_id ?? ""}|${ni}`); const pv = pl?.[ni] ?? 0; return l.plan_typed[ni] ? (c?.expression || String(pv)) : pv ? String(pv) : ""; };
        return (
          <tr key={r.key} style={{ height: r.h }}>
            <td>Plan</td>
            {spacerL}
            {visibleCols.map((i) => {
              const past = i < asOfIdx, ro = readOnlyCol(i);
              const typed = !!l.plan_typed[i];
              const cell = planBy.get(`${aid}|${l.supplier_id ?? ""}|${i}`);
              const v = pl?.[i] ?? 0, cbn = cb?.[i] ?? 0;
              const isEd = editing?.aid === aid && editing.key === "plan" && editing.lane === li && editing.i === i;
              const isActive = !isEd && active?.aid === aid && active.key === "plan" && active.lane === li && active.i === i;
              const ghost = !past && !typed && v === 0 && cbn > 0;
              const cls = [...cellBase(i), past || ro || locked ? "" : "editable", isEd ? "editing" : "", isActive ? "active" : "", typed ? "typed" : "erp", ghost ? "ghost" : "", cell?.note ? "noted" : "", inFill(aid, "plan", li, i) ? "fill-range" : ""].filter(Boolean).join(" ");
              const shown = shownOf(i);
              const initial = ghost ? String(cbn) : shown;
              const title = past ? "" : [typed ? `Saisi : ${fmtQty(v, unit)}${cell?.note ? ` – ${cell.note}` : ""} (vider = retour à l'ERP)` : v ? `ERP : ${fmtQty(v, unit)} – taper une quantité pour décider (0 = rien attendu)` : "Vide = rien dans l'ERP – taper une quantité pour planifier une livraison",
                cbn ? `Proposition CBN : ${fmtQty(cbn, unit)}${ghost ? " (cliquer puis Entrée pour la reprendre)" : ""}` : "", ro ? "Semaine agrégée : cliquer l'en-tête pour saisir par jour" : "Clic droit : commentaire · tirer le carré pour recopier"].filter(Boolean).join("\n");
              const onClick = past || isEd || locked ? undefined : ro ? () => onSwitchDay?.(cols.period_start[i]) : () => { setActive({ aid, key: "plan", lane: li, i }); setEditing({ aid, key: "plan", lane: li, i, value: initial, initial: shown }); };
              return <td key={i} className={cls} title={title} onClick={onClick} onMouseEnter={fill ? () => extendFill(i) : undefined}
                onContextMenu={past || ro || locked ? undefined : (e) => { e.preventDefault(); comment(aid, "plan", l, i, cell?.note ?? "", initial); }}>
                {isEd ? input(editing!, `Plan ${l.supplier_id ?? ""} ${periodLabel(cols.periods[i])}`, shownOf, (ni) => ni >= asOfIdx && !readOnlyCol(ni))
                  : past ? "" : ghost ? <span className="ghostv">{fmtQty(cbn, unit)}</span> : <>{v === 0 && !typed ? "·" : fmtQty(v, unit)}{cbn > 0 && !ghost && <span className="ghostv small"> +{fmtQty(cbn, unit)}</span>}</>}
                {(isEd || isActive) && !past && !ro && handle(aid, "plan", li, i, isEd ? editing!.value : shown)}
              </td>;
            })}
            {spacerR}
          </tr>
        );
      }
      case "cbn": {
        const l = a.lanes[r.li], cbs = laneSeries(l, "supply_proposed"), sid = l.supplier_id ?? "";
        return (
          <tr key={r.key} style={{ height: r.h }}>
            <td>Proposition CBN</td>
            {spacerL}
            {visibleCols.map((i) => {
              const v = cbs?.[i] ?? 0;
              const flag = flagBy.get(`proposal_refused|${aid}|${sid}|${i}`) ?? flagBy.get(`proposal_refused|${aid}||${i}`);
              const ro = readOnlyCol(i), past = i < asOfIdx;
              const clickable = !past && !locked && (v > 0 || !!flag);
              const cls = [...cellBase(i), "cbn", flag ? "refused" : "", clickable ? "clickable" : ""].filter(Boolean).join(" ");
              const title = flag ? `Proposition refusée (${fmtQty(flag.qty, unit)}) : aucune proposition à ce fournisseur jusqu'à la fin de sa semaine – cliquer pour la rétablir`
                : v > 0 ? `Besoin net calculé sur le Scenario Plan : ${fmtQty(v, unit)}${ro ? " (semaine agrégée : cliquer l'en-tête pour le détail)" : " – reprise dans le Plan (cellule grisée) ; cliquer pour la refuser"}` : "";
              const onClick = !clickable ? undefined : ro && !flag ? () => onSwitchDay?.(cols.period_start[i]) : () => toggleFlag.mutate({ article_id: aid, supplier_id: flag ? (flag.supplier_id || null) : (sid || null), date: flag ? flag.date : cols.period_start[i], kind: "proposal_refused", qty: v });
              return <td key={i} className={cls} title={title} onClick={onClick}>{flag ? <s>{fmtQty(flag.qty, unit)}</s> : v === 0 ? "·" : fmtQty(v, unit)}</td>;
            })}
            {spacerR}
          </tr>
        );
      }
      case "adj": {
        const initialOf = (ni: number) => { const c = adjBy.get(`${aid}|${ni}`); const av = series("adjustments", ni); return c ? (c.expression || String(c.qty)) : av ? String(av) : ""; };
        return (
          <tr key={r.key} style={{ height: r.h }}>
            <td>Ajustement</td>
            {spacerL}
            {visibleCols.map((i) => {
              const cell = adjBy.get(`${aid}|${i}`);
              const v = series("adjustments", i);
              const ro = readOnlyCol(i);
              const isEd = editing?.aid === aid && editing.key === "adjustments" && editing.i === i;
              const isActive = !isEd && active?.aid === aid && active.key === "adjustments" && active.i === i;
              const cls = [...cellBase(i), ro || locked ? "" : "editable", isEd ? "editing" : "", isActive ? "active" : "", cell ? "typed" : "", v < 0 ? "neg-val" : "", cell?.note ? "noted" : "", inFill(aid, "adjustments", 0, i) ? "fill-range" : ""].filter(Boolean).join(" ");
              const initial = initialOf(i);
              const title = [cell ? `${cell.expression || cell.qty} = ${fmtQty(cell.qty, unit)}${cell.note ? ` – ${cell.note}` : ""}` : i <= initIdx ? "Quantité signée : corrige le stock initial (point zéro)" : "Quantité signée ou formule (ex. -(30+20)) : mouvement, passé ou prévu", ro ? "Semaine agrégée : cliquer l'en-tête pour saisir par jour" : "Clic droit : commentaire · tirer le carré pour recopier"].join("\n");
              const onClick = isEd || locked ? undefined : ro ? () => onSwitchDay?.(cols.period_start[i]) : () => { setActive({ aid, key: "adjustments", lane: 0, i }); setEditing({ aid, key: "adjustments", lane: 0, i, value: initial, initial }); };
              return <td key={i} className={cls} title={title} onClick={onClick} onMouseEnter={fill ? () => extendFill(i) : undefined}
                onContextMenu={ro || locked ? undefined : (e) => { e.preventDefault(); comment(aid, "adjustments", null, i, cell?.note ?? "", initial); }}>
                {isEd ? input(editing!, `Ajustement ${periodLabel(cols.periods[i])}`, initialOf, (ni) => !readOnlyCol(ni)) : v === 0 ? "·" : `${v > 0 ? "+" : ""}${fmtQty(v, unit)}`}
                {(isEd || isActive) && !ro && handle(aid, "adjustments", 0, i, isEd ? editing!.value : initial)}
              </td>;
            })}
            {spacerR}
          </tr>
        );
      }
      case "stock": {
        const k = r.sk!;
        return (
          <tr key={r.key} style={{ height: r.h }}>
            <td>{k === "stock_erp" ? "Scenario ERP" : "Scenario Plan"}</td>
            {spacerL}
            {visibleCols.map((i) => {
              const past = i < asOfIdx;
              const v = series(k, i);
              const cov = series(k === "stock_erp" ? "coverage_erp" : "coverage_plan", i);
              const short = series(k === "stock_erp" ? "shortage_erp" : "shortage_plan", i);
              const cls = [...cellBase(i), "stock", past ? "" : coverageClass(cov, a.article), short > 0 ? "short" : ""].filter(Boolean).join(" ");
              const title = past ? `stock reconstitué ${fmtQty(v, unit)}` : `stock ${fmtQty(v, unit)} · couverture ${cov} j${short > 0 ? ` · manque ${fmtQty(short, unit)} (besoin non servi)` : ""}`;
              return <td key={i} className={cls} title={title}>
                {!past && <span className="cov">{cov} j</span>}
                <span className="val">{fmtQty(v, unit)}</span>
                {short > 0 && <span className="shortv">−{fmtQty(short, unit)}</span>}
              </td>;
            })}
            {spacerR}
          </tr>
        );
      }
    }
  };

  const topH = offsets[r0], bottomH = totalH - offsets[r1];
  return (
    <div className={`pivot-wrap ${fill ? "filling" : ""}`}>
      <div className="pivot-tools">
        <button className="btn xs ghost" onClick={() => setMenu((m) => !m)} title="Masquer / afficher des lignes (pour tous les articles)"><Eye />Lignes{hidden.size ? ` (${hidden.size} masquée${hidden.size > 1 ? "s" : ""})` : ""}</button>
        {menu && (
          <div className="row-menu" onMouseLeave={() => setMenu(false)}>
            {ROW_LABELS.map((r) => (
              <label key={r.key} className="checkbox"><input type="checkbox" checked={!hidden.has(r.key)} onChange={() => toggleRow(r.key)} />{r.label}{r.lane ? <span className="subtle"> · par fournisseur</span> : ""}</label>
            ))}
          </div>
        )}
        {hasDayCols && <>
          <label className="checkbox" title="Afficher les colonnes des samedis (mode jour)"><input type="checkbox" checked={perimeter.showSaturday} onChange={(e) => set({ showSaturday: e.target.checked })} />Samedi</label>
          <label className="checkbox" title="Afficher les colonnes des dimanches (mode jour)"><input type="checkbox" checked={perimeter.showSunday} onChange={(e) => set({ showSunday: e.target.checked })} />Dimanche</label>
        </>}
        <span className="subtle small" style={{ marginLeft: "auto" }}>{articles.length} article{articles.length > 1 ? "s" : ""} · {m} colonnes</span>
      </div>
      <div className="pivot virtual" ref={wrap} onScroll={onScroll}>
        <table style={{ width: LABEL_W + m * COL_W }}>
          <thead>
            <tr style={{ height: HEAD_H }}>
              <th style={{ width: LABEL_W, minWidth: LABEL_W, maxWidth: LABEL_W }}>Variable</th>
              {leftW > 0 && <th className="spacer" style={{ width: leftW, minWidth: leftW, maxWidth: leftW }} />}
              {visibleCols.map((i) => { const p = cols.periods[i]; return <th key={p} style={{ width: COL_W, minWidth: COL_W, maxWidth: COL_W }} className={`${i === asOfIdx ? "today" : ""} ${isWeekKey(p) ? "wk wkcol" : ""}`} title={isWeekKey(p) ? `${cols.period_start[i]} → ${cols.period_end[i]}${onSwitchDay ? " · cliquer pour le détail par jour" : ""}` : `${fmtDate(cols.period_start[i], "EEEE dd/MM/yyyy")} · semaine ${isoWeekOf(cols.period_start[i])}`}
                onClick={isWeekKey(p) && onSwitchDay ? () => onSwitchDay(cols.period_start[i]) : undefined}>{periodLabel(p)}</th>; })}
              {rightW > 0 && <th className="spacer" style={{ width: rightW, minWidth: rightW, maxWidth: rightW }} />}
            </tr>
          </thead>
          <tbody>
            {topH > 0 && <tr className="vspacer" style={{ height: topH }}><td colSpan={colCount} /></tr>}
            {rows.slice(r0, r1).map(renderRow)}
            {bottomH > 0 && <tr className="vspacer" style={{ height: bottomH }}><td colSpan={colCount} /></tr>}
          </tbody>
        </table>
      </div>
      <div className="legend small" style={{ padding: "6px 10px" }}>
        <span><span className="sw bar" style={{ background: "var(--bg-subtle)", border: "1px solid var(--border-strong)" }} />vide = ERP</span>
        <span><span className="sw bar" style={{ background: "var(--cell-yellow)" }} /><i>DESADV non reçu</i> (annoncé, hors calculs ; double-clic pour masquer) · <span className="dot ok" style={{ verticalAlign: "middle" }} /> traité OK · <span className="dot ko" style={{ verticalAlign: "middle" }} /> non traité / BL sans DESADV</span>
        <span><span className="sw bar" style={{ background: "var(--brand-soft)", boxShadow: "inset 0 -2px 0 var(--brand)" }} />chiffre = votre plan (0 = rien attendu, vider = retour ERP) · carré = recopier en tirant</span>
        <span><span className="sw bar" style={{ background: "transparent", border: "1px dashed var(--warning)" }} />grisé = proposition CBN, cliquer puis Entrée pour la reprendre · cliquer la ligne CBN pour la refuser</span>
        <span><span className="sw bar" style={{ background: "var(--cell-yellow)" }} />ferme en cours · <span className="sw bar" style={{ background: "var(--cell-green)" }} />ferme soldée · <span className="sw bar" style={{ background: "var(--cell-red)" }} />ferme ignorée (clic)</span>
        <span>clic droit = commentaire · <EyeOff size={11} style={{ verticalAlign: "middle" }} /> masquer des lignes</span>
      </div>
    </div>
  );
}
