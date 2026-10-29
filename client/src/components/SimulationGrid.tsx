import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight, Eye, EyeOff, Lock, Truck } from "lucide-react";
import { api } from "@/lib/api";
import { usePlantCalendar, useWrite } from "@/lib/queries";
import { isFlashed, useFlashedCells } from "@/lib/actionsBus";
import { usePerimeter } from "@/state/PerimeterContext";
import { useToast } from "@/components/ui";
import { useLang, type TKey } from "@/lib/i18n";
import { ORDER_TYPE_LABELS, fmtCellQty, fmtDate, fmtQty, isDecimalArticle, isSaturday, isSunday, isWeekKey, isWeekend, isoWeekKey, isoWeekOf, periodLabel, weekChipLabel } from "@/lib/format";
import { WeekRangeBar, type WeekChip } from "@/components/WeekRangeBar";
import type { DesadvInfo, ReceiptInfo, AdjustmentOut, ArticleKpis, ArticleRef, ErpSnapshot, FlagIn, FlagOut, LaneOut, LinkRef, PlanCellOut, ProposalOut, SeriesOut } from "@/lib/types";
import { fmtSigned, fmtSignedPct } from "@/components/CalibrateDialog";

/** Rows of the grid that can be hidden (for every article) ; lane rows are repeated per supplier.  The firm projection
 *  (``stock_erp``) is no longer a row of the table : it stays a KPI (« Projeté Ferme »). */
export const ROW_KEYS: { key: string; label: TKey; lane?: boolean }[] = [
  { key: "demand", label: "row.demand" },
  { key: "orders_firm", label: "row.orders_firm", lane: true },
  { key: "orders_forecast", label: "row.orders_forecast", lane: true },
  { key: "receipts", label: "row.receipts", lane: true },
  { key: "plan", label: "row.plan", lane: true },
  { key: "supply_proposed", label: "row.supply_proposed", lane: true },
  { key: "adjustments", label: "row.adjustments" },
  { key: "stock_plan", label: "row.stock_plan" },
];

export interface GridColumns { as_of: string; init_date: string; periods: string[]; period_start: string[]; period_end: string[]; }
export interface GridRowArticle { article: ArticleRef; series: SeriesOut[]; lanes: LaneOut[]; kpis?: Partial<ArticleKpis> & { severity?: string | null }; suppliers?: LinkRef[]; proposals?: ProposalOut[]; erp_snapshots?: ErpSnapshot[]; }

/* ---------------------------------------------------------------- geometry (virtualisation) */
const COL_W = 72;          // every data column has the same width: the visible range is arithmetic
const LABEL_W = 285;       // sticky first column (matches the CSS min-width of the first cell)
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

/** ISO date + n days. */
function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}
/** ISO weekday (1 = Monday … 7 = Sunday). */
function isoWeekday(iso: string): number { const d = new Date(`${iso}T00:00:00Z`).getUTCDay(); return d === 0 ? 7 : d; }
/** Every ISO date from ``a`` to ``b`` (inclusive). */
function daysBetween(a: string, b: string): string[] { const out: string[] = []; for (let d = a; d <= b; d = addDays(d, 1)) out.push(d); return out; }

/** ``initial`` is what the cell showed before editing: nothing is saved when the value is unchanged ; ``date`` forces the
 *  day the value is stored on (a CBN proposal taken from a week column keeps its own date). */
type Edit = { aid: string; key: "plan" | "adjustments"; lane: number; i: number; value: string; initial: string; date?: string };
/** Fill handle drag (Excel-like copy along the row). */
type Fill = { aid: string; key: "plan" | "adjustments"; lane: number; from: number; to: number; value: string };
/** The « Placer le jour… » popover of a week column. */
type Picker = { aid: string; key: "plan" | "adjustments"; lane: number; i: number; value: string; x: number; y: number };
/** The right-click menu of a week column cell. */
type Menu = { aid: string; key: "plan" | "adjustments"; lane: number; i: number; value: string; current: string; x: number; y: number };

type RowKind = "article" | "group" | "demand" | "firm" | "fcst" | "rec" | "plan" | "cbn" | "adj" | "stock";
interface RowDesc { kind: RowKind; a: GridRowArticle; li: number; h: number; key: string }

function laneSeries(l: LaneOut, k: string): number[] | undefined { return l.series.find((s) => s.key === k)?.values; }

/**
 * The simulation grid: one column per day or ISO week, one block per article.  Rows and columns are
 * **virtualised**: only the cells in view are in the DOM, whatever the number of articles / days.
 *
 * Two rows are typed, nothing else: **Quantité à retenir** (one row per supplier ; empty = the ERP quantity of
 * the day, a typed value = the planner's decision, 0 included, a blank restores the ERP) and
 * **Ajustement** (signed, any date).  Both rows have a **fill handle** (small square at the corner of
 * the active cell): dragging it along the row copies the value, like Excel.
 *
 * Two rows are clickable: **Ordre ferme** (a click ignores the firm orders of that supplier and day: red,
 * struck, out of the firm projection and of the plan ; click again to restore) and **Proposition CBN**
 * (a click refuses the proposal: struck, no proposal to that supplier that day ; click again).
 * A « ! » marks an urgent proposal (order deadline past : floor = the minimum avoiding the stockout), a « ▲ »
 * a proposal truncated by a supplier cap, a « ◇ » a proposal off the standard delivery day.  Decimal articles
 * (KG, M, decimal bill of material) show the Besoin and stock rows with three decimals.  The stock cells show
 * the **cumulated** unserved demand (reset when the stock is positive again).
 *
 * A CBN proposal is shown greyed in an empty plan cell: one click then Enter takes it, on its own date.
 * Right-click on a typed cell adds a comment.  In a **week column** Enter stores the value on the supplier's
 * first standard delivery day of the week that is not past (the Monday for an adjustment or without supplier) ;
 * Shift+Enter, or right-click › « Placer le jour… », opens the list of the week's days to choose another one ;
 * clearing a week cell sends every typed cell of the week back to the ERP ; clicking a week header switches
 * to the day view.  The labels follow the FR | EN choice of the page.
 */
export function SimulationGrid({ cols, articles, planCells, adjustments, flags = [], showArticleRows, onSwitchDay }: {
  cols: GridColumns; articles: GridRowArticle[]; planCells: PlanCellOut[]; adjustments: AdjustmentOut[]; flags?: FlagOut[];
  showArticleRows?: boolean; onSwitchDay?: (date: string) => void;
}) {
  const toast = useToast();
  const { lang, t } = useLang();
  const { perimeter, set, rights } = usePerimeter();
  const calendar = usePlantCalendar();
  const hidden = useMemo(() => new Set(perimeter.hiddenRows), [perimeter.hiddenRows]);
  /** read-only article: outside the user's portfolio (the server refuses the writes too) */
  const lockedOf = useCallback((a: GridRowArticle) => !rights.canEditPlanner(a.article.planner), [rights]);
  const LOCK = t("grid.readonly");
  const NO_BL = t("rec.no_bl");   // réception manuelle ou DESADV sans BL dans l'ERP
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [menu, setMenu] = useState(false);
  const [editing, setEditing] = useState<Edit | null>(null);
  const [active, setActive] = useState<Omit<Edit, "value" | "initial" | "date"> | null>(null);
  const [fill, setFill] = useState<Fill | null>(null);
  const [picker, setPicker] = useState<Picker | null>(null);
  const [ctx, setCtx] = useState<Menu | null>(null);
  const fillRef = useRef<Fill | null>(null);
  const PLAN = t("row.plan");

  const saveAdj = useWrite((c: { article_id: string; date: string; expression?: string; note?: string }) => api.put<AdjustmentOut | null>("/api/entries/adjustments", c),
    (out) => toast.push(out ? `${t("row.adjustments")} ${fmtDate(out.date)} = ${fmtQty(out.qty)}` : "Ajustement effacé", "success"));
  const savePlan = useWrite((c: { article_id: string; supplier_id: string | null; date: string; expression?: string; note?: string }) => api.put<PlanCellOut | null>("/api/entries/plan", c),
    (out) => toast.push(out ? `${PLAN} ${fmtDate(out.date)} = ${fmtQty(out.qty)}` : "Retour à l'ERP", "success"));
  const savePlanBatch = useWrite((cells: { article_id: string; supplier_id: string | null; date: string; expression: string }[]) => api.put<PlanCellOut[]>("/api/entries/plan/batch", { cells }),
    (out) => toast.push(out.length ? `${out.length} cellule(s) « ${PLAN} » écrite(s)` : "Semaine rendue à l'ERP", "success"));
  const saveAdjBatch = useWrite((cells: { article_id: string; date: string; expression: string }[]) => api.put<AdjustmentOut[]>("/api/entries/adjustments/batch", { cells }),
    (out) => toast.push(`${out.length} ajustement(s) recopié(s)`, "success"));
  const toggleFlag = useWrite((f: FlagIn) => api.post<FlagOut | null>("/api/entries/flags/toggle", f),
    (out) => toast.push(out ? (out.kind === "order_ignored" ? `Ordre ferme du ${fmtDate(out.date)} ignoré` : `Proposition du ${fmtDate(out.date)} refusée (ce jour seulement)`) : "Rétabli", "success"));
  /** several days at once (the firm orders or the proposals of a week column) */
  const toggleFlags = useWrite((fs: FlagIn[]) => Promise.all(fs.map((f) => api.post<FlagOut | null>("/api/entries/flags/toggle", f))),
    (outs) => { const on = outs.filter(Boolean).length; const kind = outs.find(Boolean)?.kind; toast.push(on ? (kind === "proposal_refused" ? `${on} proposition(s) refusée(s)` : `${on} jour(s) d'ordres fermes ignoré(s)`) : `${outs.length} jour(s) rétabli(s)`, "success"); });

  /* ---------------- lookups */
  const n = cols.periods.length;
  const asOfIdx = useMemo(() => columnOf(cols, cols.as_of), [cols]);
  const initIdx = useMemo(() => columnOf(cols, cols.init_date), [cols]);
  const planBy = useMemo(() => {
    const m = new Map<string, PlanCellOut>();
    planCells.forEach((c) => m.set(`${c.article_id}|${c.supplier_id ?? ""}|${c.date}`, c));
    return m;
  }, [planCells]);
  const adjBy = useMemo(() => {
    const m = new Map<string, AdjustmentOut>();
    adjustments.forEach((c) => m.set(`${c.article_id}|${c.date}`, c));
    return m;
  }, [adjustments]);
  /** refused proposals and ignored days by column ; a refusal dated before today has expired and is not shown */
  const flagBy = useMemo(() => {
    const m = new Map<string, FlagOut>();
    flags.forEach((f) => { if (f.kind === "proposal_refused" && f.date < cols.as_of) return; const i = columnOf(cols, f.date); if (i >= 0) m.set(`${f.kind}|${f.article_id}|${f.supplier_id}|${i}`, f); });
    return m;
  }, [flags, cols]);
  const weekCol = useCallback((i: number) => isWeekKey(cols.periods[i]), [cols]);
  const holidays = useMemo(() => new Set(calendar.data?.holidays ?? []), [calendar.data]);
  const workingDays = useMemo(() => new Set(calendar.data?.working_weekdays ?? [1, 2, 3, 4, 5]), [calendar.data]);
  const isWorking = useCallback((iso: string) => workingDays.has(isoWeekday(iso)) && !holidays.has(iso), [workingDays, holidays]);
  const linkOf = useCallback((a: GridRowArticle, sid: string | null) => (sid ? a.suppliers?.find((s) => s.supplier_id === sid) : undefined), []);
  const weekdaysOf = useCallback((link: LinkRef | undefined) => (link ? new Set(link.delivery_weekdays.split(",").map((x) => Number(x.trim())).filter((x) => x >= 1 && x <= 7)) : null), []);
  /** day a value typed in column ``i`` is stored on : the day itself, or – in a week column – the supplier's first
   *  standard delivery day of the week that is not past (plan), else the first working day, else the Monday ;
   *  an adjustment goes to the Monday (today in the current week) */
  const editDate = useCallback((i: number, key: "plan" | "adjustments", a?: GridRowArticle, sid?: string | null): string => {
    const start = cols.period_start[i];
    const lo = key === "plan" && start < cols.as_of ? cols.as_of : start;
    if (!isWeekKey(cols.periods[i])) return lo;
    const end = cols.period_end[i] ?? start;
    const allowed = key === "plan" && a ? weekdaysOf(linkOf(a, sid ?? null)) : null;
    const days = daysBetween(lo, end);
    const std = allowed ? days.find((d) => isWorking(d) && allowed.has(isoWeekday(d))) : undefined;
    return std ?? (key === "plan" ? days.find(isWorking) ?? lo : lo);
  }, [cols, isWorking, linkOf, weekdaysOf]);
  /** whether the day a week column stores on is a standard delivery day of the supplier (tooltip wording) */
  const isStandardDay = useCallback((a: GridRowArticle, sid: string | null, iso: string) => { const w = weekdaysOf(linkOf(a, sid)); return !!w && w.has(isoWeekday(iso)); }, [linkOf, weekdaysOf]);
  const planCell = useCallback((aid: string, sid: string | null, date: string) => planBy.get(`${aid}|${sid ?? ""}|${date}`), [planBy]);
  const adjCell = useCallback((aid: string, date: string) => adjBy.get(`${aid}|${date}`), [adjBy]);
  /** ISO week of every column ("2026-W40") : the key of the week range picker */
  const weekOf = useCallback((i: number) => (isWeekKey(cols.periods[i]) ? cols.periods[i] : isoWeekKey(cols.period_start[i])), [cols]);
  const weeks = useMemo<WeekChip[]>(() => {
    const out: WeekChip[] = [];
    const todayWeek = isoWeekKey(cols.as_of);
    cols.periods.forEach((_p, i) => { const k = weekOf(i); if (!out.length || out[out.length - 1].key !== k) out.push({ key: k, label: weekChipLabel(k, lang), start: fmtDate(cols.period_start[i]), current: k === todayWeek }); });
    return out;
  }, [cols, weekOf, lang]);
  /** the stored range applies only when it overlaps the loaded window (otherwise the whole window) */
  const range = useMemo(() => {
    const r = perimeter.weekRange;
    if (!r) return null;
    const keys = weeks.map((w) => w.key);
    const a = keys.indexOf(r.from), b = keys.indexOf(r.to);
    if (a < 0 && b < 0) return null;
    return { from: a < 0 ? keys[0] : r.from, to: b < 0 ? keys[keys.length - 1] : r.to };
  }, [perimeter.weekRange, weeks]);
  /** displayed columns → original column indexes (Saturdays / Sundays can be hidden in day mode ; weeks outside the chosen range are hidden) */
  const shown = useMemo(() => {
    const out: number[] = [];
    const keys = weeks.map((w) => w.key);
    const lo = range ? keys.indexOf(range.from) : -1, hi = range ? keys.indexOf(range.to) : Infinity;
    cols.periods.forEach((p, i) => {
      if (!isWeekKey(p)) {
        if (!perimeter.showSaturday && isSaturday(p)) return;
        if (!perimeter.showSunday && isSunday(p)) return;
      }
      if (range) { const w = keys.indexOf(weekOf(i)); if (w < lo || w > hi) return; }
      out.push(i);
    });
    return out;
  }, [cols, perimeter.showSaturday, perimeter.showSunday, range, weeks, weekOf]);
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
      if (!hidden.has("stock_plan")) out.push({ kind: "stock", a, li: 0, h: STOCK_H, key: `${aid}-stock_plan` });
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
  const articleOf = (aid: string) => articles.find((x) => x.article.article_id === aid);
  /** typed plan cells of one supplier inside the week of column ``i`` (from today on) */
  const typedInWeek = (aid: string, sid: string | null, i: number) => planCells.filter((c) => c.article_id === aid && (c.supplier_id ?? "") === (sid ?? "") && c.date >= cols.as_of && c.date >= cols.period_start[i] && c.date <= (cols.period_end[i] ?? cols.period_start[i]));
  /** write one value of a row on ``date`` */
  const write = (aid: string, key: "plan" | "adjustments", lane: number, date: string, value: string) => {
    const a = articleOf(aid);
    if (key === "adjustments") saveAdj.mutate({ article_id: aid, date, expression: value });
    else if (a) savePlan.mutate({ article_id: aid, supplier_id: a.lanes[lane].supplier_id, date, expression: value });
  };
  const commit = (next?: Edit) => {
    if (editing && editing.value.trim() !== editing.initial.trim()) {
      const a = articleOf(editing.aid);
      const sid = a?.lanes[editing.lane].supplier_id ?? null;
      if (editing.key === "plan" && weekCol(editing.i) && !editing.value.trim() && !editing.date) {
        // clearing a week cell : every typed cell of the week goes back to the ERP (one undoable action)
        const cells = typedInWeek(editing.aid, sid, editing.i);
        if (cells.length) savePlanBatch.mutate(cells.map((c) => ({ article_id: editing.aid, supplier_id: sid, date: c.date, expression: "" })));
      } else {
        write(editing.aid, editing.key, editing.lane, editing.date ?? editDate(editing.i, editing.key, a, sid), editing.value);
      }
    }
    setEditing(next ?? null);
  };
  const comment = (aid: string, key: "plan" | "adjustments", lane: LaneOut | null, i: number, current: string, currentValue: string) => {
    const a = articleOf(aid);
    const date = editDate(i, key, a, lane?.supplier_id ?? null);
    if (key === "adjustments" && !adjCell(aid, date)) { toast.push("Saisir d'abord une quantité d'ajustement dans la cellule", "info"); return; }
    const note = window.prompt("Commentaire de la cellule (vide pour effacer)", current);
    if (note === null) return;
    if (key === "adjustments") saveAdj.mutate({ article_id: aid, date, note });
    // a comment on an ERP cell turns it into a typed cell with the same quantity (0 when the ERP has nothing)
    else savePlan.mutate({ article_id: aid, supplier_id: lane?.supplier_id ?? null, date, note, ...(planCell(aid, lane?.supplier_id ?? null, date) ? {} : { expression: currentValue || "0" }) });
  };
  const openPicker = (aid: string, key: "plan" | "adjustments", lane: number, i: number, value: string, at: { x: number; y: number }) => {
    setPicker({ aid, key, lane, i, value, x: Math.min(at.x, window.innerWidth - 340), y: Math.min(at.y, window.innerHeight - 320) });
  };
  useEffect(() => {
    if (!picker && !ctx) return;
    const close = (e: MouseEvent) => { const el = e.target as HTMLElement; if (!el.closest?.(".day-picker, .ctx-menu")) { setPicker(null); setCtx(null); } };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") { setPicker(null); setCtx(null); } };
    window.addEventListener("mousedown", close);
    window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", esc); };
  }, [picker, ctx]);

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
      for (let i = lo; i <= hi; i++) if (i !== f.from && (f.key === "adjustments" || i >= asOfIdx)) targets.push(i);
      if (!targets.length) return;
      const a = articles.find((x) => x.article.article_id === f.aid);
      const sid = a?.lanes[f.lane].supplier_id ?? null;
      if (f.key === "plan" && a) savePlanBatch.mutate(targets.map((i) => ({ article_id: f.aid, supplier_id: sid, date: editDate(i, "plan", a, sid), expression: f.value })));
      else if (f.key === "adjustments") saveAdjBatch.mutate(targets.map((i) => ({ article_id: f.aid, date: editDate(i, "adjustments"), expression: f.value })));
    };
    window.addEventListener("mouseup", up);
    return () => window.removeEventListener("mouseup", up);
  }, [fill, articles, asOfIdx, editDate, savePlanBatch, saveAdjBatch]);
  const inFill = (aid: string, key: string, lane: number, i: number) => !!fill && fill.aid === aid && fill.key === key && fill.lane === lane && i >= Math.min(fill.from, fill.to) && i <= Math.max(fill.from, fill.to);

  const input = (e: Edit, label: string, initial: (i: number) => string, canTab: (ni: number) => boolean) => (
    <input className="cell-input" autoFocus value={e.value} aria-label={label}
      onChange={(ev) => setEditing({ ...e, value: ev.target.value })} onBlur={() => { if (!fillRef.current && !picker) commit(); }}
      onKeyDown={(ev) => {
        if (ev.key === "Enter" && ev.shiftKey && weekCol(e.i)) { ev.preventDefault(); const r = (ev.target as HTMLElement).getBoundingClientRect(); openPicker(e.aid, e.key, e.lane, e.i, e.value, { x: r.left, y: r.bottom + 4 }); setEditing(null); }
        else if (ev.key === "Enter") { ev.preventDefault(); commit(); }
        else if (ev.key === "Escape") { ev.preventDefault(); setEditing(null); }
        else if (ev.key === "Tab") { ev.preventDefault(); const ni = nextShown(e.i, ev.shiftKey ? -1 : 1); if (ni >= 0 && ni < n && canTab(ni)) { const nv = initial(ni); setActive({ aid: e.aid, key: e.key, lane: e.lane, i: ni }); commit({ ...e, i: ni, value: nv, initial: nv, date: undefined }); } else commit(); }
      }} />
  );
  const handle = (aid: string, key: "plan" | "adjustments", lane: number, i: number, value: string) => (
    <span className="fill-handle" title="Tirer pour recopier la valeur sur les cellules voisines" onMouseDown={(e) => startFill(e, aid, key, lane, i, value)} />
  );

  /* ---------------- cells */
  const flashed = useFlashedCells();
  const flash = (entity: "plan_cell" | "adjustment", aid: string, sid: string | null, i: number) =>
    flashed.length && isFlashed(flashed, entity, aid, sid, cols.period_start[i], cols.period_end[i]) ? "flash" : "";
  const cellBase = (i: number): string[] => {
    const p = cols.periods[i];
    const past = i < asOfIdx;
    return [past || (!isWeekKey(p) && isWeekend(cols.period_start[i])) ? "past" : "", i === asOfIdx ? "today" : "", isWeekKey(p) ? "wkcol" : ""];
  };
  const spacerL = leftW > 0 ? <td key="sl" className="spacer" style={{ width: leftW, minWidth: leftW, maxWidth: leftW }} /> : null;
  const spacerR = rightW > 0 ? <td key="sr" className="spacer" style={{ width: rightW, minWidth: rightW, maxWidth: rightW }} /> : null;
  /** decimal article (non-piece unit or decimal requirement) : Besoin and stock rows with three decimals */
  const decimalOf = useCallback((a: GridRowArticle) => isDecimalArticle(a.article.unit, a.series.find((s) => s.key === "demand")?.values), []);
  const dayLabel = (iso: string) => fmtDate(iso, "EEEE dd/MM", lang);
  /** ERP stock snapshot shown under the projected stock of a past column : the day's snapshot, or the last one of the week */
  const erpOf = (a: GridRowArticle, i: number): ErpSnapshot | undefined => {
    const snaps = a.erp_snapshots;
    if (!snaps?.length) return undefined;
    const s = cols.period_start[i], e = cols.period_end[i] ?? s;
    let hit: ErpSnapshot | undefined;
    for (const x of snaps) if (x.date >= s && x.date <= e) hit = x;
    return hit;
  };
  const pctOf = (s: { gap_pct: number | null }) => fmtSignedPct(s.gap_pct);
  const hasErp = articles.some((a) => a.erp_snapshots?.length);
  const plainRow = (r: RowDesc, label: ReactNode, cls: string, val: (i: number) => number, title?: (i: number) => string, extra?: (i: number) => string, decimal = false) => (
    <tr key={r.key} className={cls} style={{ height: r.h }}>
      <td>{label}</td>
      {spacerL}
      {visibleCols.map((i) => { const v = val(i); return <td key={i} className={[...cellBase(i), extra?.(i) ?? "", decimal ? "decimal" : ""].filter(Boolean).join(" ")} title={title?.(i)}>{v === 0 ? "·" : fmtCellQty(v, r.a.article.unit, decimal)}</td>; })}
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
            <td colSpan={colCount - 1} style={{ textAlign: "left", paddingLeft: 10 }}>
              {a.kpis?.erp_stock_drift && a.kpis.erp_stock_date && <Link to={`/articles/${encodeURIComponent(aid)}`} className={`erp-badge ${(a.kpis.erp_stock_gap ?? 0) < 0 ? "low" : "high"}`}
                title={t("grid.erp_badge_title", { d: fmtDate(a.kpis.erp_stock_date, undefined, lang), q: fmtQty(a.kpis.erp_stock_qty ?? 0, unit), s: fmtQty((a.kpis.erp_stock_qty ?? 0) - (a.kpis.erp_stock_gap ?? 0), unit), v: fmtSigned(a.kpis.erp_stock_gap ?? 0, unit), p: pctOf({ gap_pct: a.kpis.erp_stock_gap_pct ?? null }), n: a.kpis.erp_stock_drift_runs ?? 0 })}>
                <span className="dot" />{t("grid.erp_badge", { v: fmtSigned(a.kpis.erp_stock_gap ?? 0, unit), p: pctOf({ gap_pct: a.kpis.erp_stock_gap_pct ?? null }) })}</Link>}
            </td>
          </tr>
        );
      case "group": {
        const l = a.lanes[r.li], g = `${aid}-lane-${r.li}`;
        const link = a.suppliers?.find((x) => x.supplier_id === l.supplier_id);
        const rules = link ? `${t("page.moq")} ${fmtQty(link.moq, unit)} · ${t("page.pack")} ${fmtQty(link.pack_qty, unit)} · ${t("grid.lead")} ${link.lead_time_days} ${t("grid.wd")}${link.lead_time_override === null ? ` ${t("grid.of_supplier")}` : ` ${t("grid.of_link")}`}${link.max_qty_per_delivery ? ` · max ${fmtQty(link.max_qty_per_delivery, unit)} ${t("grid.max_delivery")}` : ""}${link.max_qty_per_week ? ` · max ${fmtQty(link.max_qty_per_week, unit)} ${t("grid.max_week")}` : ""}${link.quota_pct < 100 ? ` · ${t("grid.quota")} ${link.quota_pct} %` : ""}` : "";
        return (
          <tr key={r.key} className="group-head" style={{ height: r.h }} onClick={() => toggleGroup(g)} title={[rules, collapsed[g] ? t("grid.show_block") : t("grid.hide_block")].filter(Boolean).join("\n")}>
            <td>{collapsed[g] ? <ChevronRight size={12} /> : <ChevronDown size={12} />}<span className="lane-name">{l.supplier_id ? <><Link to={`/fournisseurs/${encodeURIComponent(l.supplier_id)}`} onClick={(e) => e.stopPropagation()} title={t("grid.open_supplier")}>{l.supplier_id}</Link>{l.name ? <> · <Link to={`/fournisseurs/${encodeURIComponent(l.supplier_id)}`} onClick={(e) => e.stopPropagation()} title={t("grid.open_supplier")}>{l.name}</Link></> : null}</> : t("grid.no_supplier")}</span>{link && (link.max_qty_per_delivery || link.max_qty_per_week) ? <span className="subtle small" style={{ marginLeft: 6 }} title={rules}>{t("grid.capped")}</span> : null}{l.linked === false && <span className="lane-unlinked" title={t("grid.unlinked_title")}>{t("grid.unlinked")}</span>}</td>
            <td colSpan={colCount - 1} />
          </tr>
        );
      }
      case "demand": {
        const src = a.article.consumption_source;
        const pastTitle = src === "plan" ? t("demand.past_plan") : src === "derived" ? t("demand.past_derived") : t("demand.past_erp");
        const nd = a.article.demand_days ?? 5;
        return plainRow(r, <>{t("row.demand")}{nd < 5 && <span className="subtle small" title={t("grid.demand_days", { n: nd })}> · {nd} j</span>}</>, "", (i) => series("demand", i), (i) => (i < asOfIdx ? pastTitle : t("demand.future")), undefined, decimalOf(a));
      }
      case "firm": {
        const l = a.lanes[r.li];
        const ordered = laneSeries(l, "orders_firm_ordered"), open = laneSeries(l, "orders_firm_open");
        const ordersTitle = (i: number) => l.orders.filter((o) => o.order_type === "FIRM" && columnOf(cols, o.expected_date) === i)
          .map((o) => `${fmtQty(o.qty_open, unit)} ${t("firm.remaining")} / ${fmtQty(o.qty_ordered, unit)} ${t("firm.ordered")}${o.ref ? ` · ${o.ref}` : ""}${o.ignored ? ` · ${t("firm.ignored_short")}` : ""}`).join("\n");
        return (
          <tr key={r.key} style={{ height: r.h }}>
            <td>{t("row.orders_firm")}</td>
            {spacerL}
            {visibleCols.map((i) => {
              const qo = ordered?.[i] ?? 0, qr = open?.[i] ?? 0;
              const ignored = !!l.orders_ignored[i];
              const partial = qr > 0 && qr < qo;
              const past = i < asOfIdx, wk = weekCol(i);
              // days of the firm orders of the column on / after the reference day (a week column holds several)
              const days = wk ? [...new Map(l.orders.filter((o) => o.order_type === "FIRM" && o.expected_date >= cols.as_of && columnOf(cols, o.expected_date) === i).map((o) => [o.expected_date, o.ignored] as const)).entries()] : [];
              const clickable = qo > 0 && !past && !locked && (!wk || days.length > 0);
              const ems = [...new Set(l.orders.filter((o) => o.order_type === "FIRM" && columnOf(cols, o.expected_date) === i).flatMap((o) => o.transports ?? []))];
              const cls = [...cellBase(i), qo > 0 ? (ignored ? "ignored" : qr > 0 ? "firm-open" : "firm-settled") : "", clickable ? "clickable" : "", l.orders.length && ordersTitle(i) ? "event" : "", ems.length ? "firm-tr" : ""].filter(Boolean).join(" ");
              const text = qo === 0 ? "·" : partial ? <span className="partial">{fmtQty(qr, unit)} / {fmtQty(qo, unit)}</span> : fmtQty(qo, unit);
              const state = qo === 0 ? "" : ignored ? t("firm.ignored") : qr > 0 ? `${t("firm.open", { open: fmtQty(qr, unit), ordered: fmtQty(qo, unit) })}${clickable ? (wk ? t("firm.click_week") : t("firm.click_day")) : ""}` : t("firm.settled", { ordered: fmtQty(qo, unit) });
              const title = [state, ordersTitle(i), ems.length ? `${t("firm.transport")} ${ems.join(", ")}` : ""].filter(Boolean).join("\n");
              // a week column toggles every day of the week : ignore the days not yet ignored, or restore them all
              const onClick = !clickable ? undefined : !wk ? () => toggleFlag.mutate({ article_id: aid, supplier_id: l.supplier_id, date: cols.period_start[i], kind: "order_ignored" })
                : () => { const all = days.every(([, ig]) => ig); toggleFlags.mutate(days.filter(([, ig]) => all || !ig).map(([d]) => ({ article_id: aid, supplier_id: l.supplier_id, date: d, kind: "order_ignored" as const }))); };
              return <td key={i} className={cls} title={title} onClick={onClick}>{ems.length > 0 && <Truck className="tr-ico" size={9} aria-label={ems.join(", ")} />}{ignored ? <s>{text}</s> : text}</td>;
            })}
            {spacerR}
          </tr>
        );
      }
      case "fcst": {
        const l = a.lanes[r.li], fc = laneSeries(l, "orders_forecast");
        const tt = (i: number) => l.orders.filter((o) => o.order_type !== "FIRM" && columnOf(cols, o.expected_date) === i).map((o) => `${ORDER_TYPE_LABELS[o.order_type]} ${fmtQty(o.qty_open, unit)} ${t("firm.remaining")} / ${fmtQty(o.qty_ordered, unit)} ${t("firm.ordered")}${o.ref ? ` · ${o.ref}` : ""}`).join("\n");
        return plainRow(r, t("row.orders_forecast"), "", (i) => fc?.[i] ?? 0, tt, (i) => (tt(i) ? "event" : ""));
      }
      case "rec": {
        const l = a.lanes[r.li], rc = laneSeries(l, "receipts"), dv = laneSeries(l, "desadv_open");
        const sid = l.supplier_id ?? "";
        const inCol = (d: DesadvInfo, i: number) => columnOf(cols, d.issue_date) === i;
        const openOf = (i: number) => l.desadv.filter((d) => inCol(d, i) && !d.received && !d.hidden && !d.journaled);
        const receiptsOf = (i: number) => (l.receipt_lines ?? []).filter((x: ReceiptInfo) => columnOf(cols, x.receipt_date) === i);
        const bl = (x: { packing_slip: string; order_kind?: string; purch_id?: string }) => `${x.packing_slip ? `BL ${x.packing_slip}` : NO_BL}${x.order_kind === "po" ? ` · PO ${x.purch_id ?? ""} (commande fermée déclarée)` : ""}`;
        const acr = (x: ReceiptInfo) => (x.booked ? "comptabilisé" : `ACR à valider${(() => { const d = Math.round((new Date(cols.as_of).getTime() - new Date(x.receipt_date).getTime()) / 86400000); return d >= 7 ? ` depuis ${d} j` : ""; })()}`);
        // why a receipt carries a red dot: manual receipt (no BL) or a BL unknown to the processed DESADV
        const recKoReason = (xs: ReceiptInfo[]) => {
          const noBl = xs.some((x) => !x.packing_slip), unknown = xs.some((x) => x.packing_slip && !x.processed);
          return [noBl ? "réception manuelle (sans BL)" : "", unknown ? "aucun DESADV traité pour ce BL" : ""].filter(Boolean).join(" ; ") || "aucun DESADV traité pour ce BL";
        };
        const transitLine = (d: DesadvInfo) => (d.source === "journal" ? `${bl(d)} ${fmtQty(d.qty, unit)} · journal d'arrivée non validé${d.processed ? " · DESADV traité" : " · aucun DESADV traité"}` : `${bl(d)} ${fmtQty(d.qty, unit)} · DESADV ${d.state}${d.final_processing ? ` / ${d.final_processing}` : ""}`);
        return (
          <tr key={r.key} style={{ height: r.h }}>
            <td>{t("row.receipts")}</td>
            {spacerL}
            {visibleCols.map((i) => {
              const v = rc?.[i] ?? 0, open = dv?.[i] ?? 0;
              const ro = weekCol(i);
              const hasDesadvData = l.desadv.length > 0;
              const recKo = !!l.receipts_ko?.[i], dvKo = !!l.desadv_ko?.[i], booked = v > 0 && !!l.receipts_booked?.[i];
              const openList = open > 0 ? openOf(i) : [];
              const recList = v > 0 ? receiptsOf(i) : [];
              const title = [
                v > 0 ? `${t("rec.received")} ${fmtQty(v, unit)}${recList.length ? ` : ${recList.map((x) => `${bl(x)} ${fmtQty(x.qty, unit)} · ${acr(x)}`).join(" ; ")}` : ""}${hasDesadvData ? (recKo ? ` – point rouge : ${recKoReason(recList)}` : " – point vert : BL annoncé par un DESADV traité") : ""}${booked ? " – fond vert : accusé de réception validé" : ""}` : "",
                open > 0 ? `${t("rec.transit")} ${fmtQty(open, unit)} ${t("rec.transit_hint")} : ${openList.map(transitLine).join(" ; ")}${dvKo ? " – point rouge : aucun DESADV traité pour ce BL" : " – point vert : DESADV traité"}${ro ? " – semaine agrégée : cliquer l'en-tête pour le détail" : locked ? "" : " – double-clic pour le masquer"}` : "",
              ].filter(Boolean).join("\n");
              const cls = [...cellBase(i), open > 0 ? "desadv" : "", booked ? "rec-booked" : "", open > 0 && !ro && !locked ? "clickable" : ""].filter(Boolean).join(" ");
              const onDouble = open <= 0 || locked ? undefined : ro ? () => onSwitchDay?.(cols.period_start[i])
                : () => { if (window.confirm(`Masquer l'en transit du ${fmtDate(cols.period_start[i])} (${fmtQty(open, unit)}, ${openList.map(bl).join(", ")}) ?\nIl ne sera plus affiché dans le tableau ; rétablissable depuis Saisies & journal.`)) toggleFlag.mutate({ article_id: aid, supplier_id: sid || null, date: cols.period_start[i], kind: "desadv_hidden", qty: open, note: openList.map((d) => d.packing_slip || NO_BL).join(", ") }); };
              return (
                <td key={i} className={cls} title={title} onDoubleClick={onDouble}>
                  {v > 0 && <>{fmtQty(v, unit)}{hasDesadvData && <span className={`dot ${recKo ? "ko" : "ok"}`} />}</>}
                  {v > 0 && open > 0 && <br />}
                  {open > 0 && <span className="desadv-qty"><Truck size={10} className="truck" aria-label={t("rec.transit")} />{fmtQty(open, unit)}<span className={`dot ${dvKo ? "ko" : "ok"}`} /></span>}
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
        const propsIn = (i: number) => (a.proposals ?? []).filter((p) => (p.supplier_id ?? "") === (l.supplier_id ?? "") && columnOf(cols, p.delivery_date) === i);
        // a week column shows the week total (several typed days may add up) ; a day column its expression
        const shownOf = (ni: number) => { const c = weekCol(ni) ? undefined : planCell(aid, l.supplier_id, editDate(ni, "plan", a, l.supplier_id)); const pv = pl?.[ni] ?? 0; return l.plan_typed[ni] ? (c?.expression || String(pv)) : pv ? String(pv) : ""; };
        return (
          <tr key={r.key} style={{ height: r.h }}>
            <td>{t("row.plan")}</td>
            {spacerL}
            {visibleCols.map((i) => {
              const past = i < asOfIdx, wk = weekCol(i);
              const typed = !!l.plan_typed[i];
              const target = past ? "" : editDate(i, "plan", a, l.supplier_id);
              const cell = wk ? undefined : planCell(aid, l.supplier_id, target);
              const v = pl?.[i] ?? 0, cbn = cb?.[i] ?? 0;
              const isEd = editing?.aid === aid && editing.key === "plan" && editing.lane === li && editing.i === i;
              const isActive = !isEd && active?.aid === aid && active.key === "plan" && active.lane === li && active.i === i;
              const ghost = !past && !typed && v === 0 && cbn > 0;
              const cls = [...cellBase(i), past || locked ? "" : "editable", isEd ? "editing" : "", isActive ? "active" : "", typed ? "typed" : "erp", ghost ? "ghost" : "", cell?.note ? "noted" : "", inFill(aid, "plan", li, i) ? "fill-range" : "", flash("plan_cell", aid, l.supplier_id, i)].filter(Boolean).join(" ");
              const shownV = shownOf(i);
              const initial = ghost ? String(cbn) : shownV;
              const ps = cbn > 0 ? propsIn(i) : [];
              const title = past ? "" : [typed ? `${t("plan.typed", { v: fmtQty(v, unit) })}${cell?.note ? ` – ${cell.note}` : ""} ${t("plan.typed_hint")}` : v ? t("plan.erp", { v: fmtQty(v, unit) }) : t("plan.empty"),
                cbn ? `${t("plan.cbn", { v: fmtQty(cbn, unit) })}${ps.some((p) => p.off_standard) ? " ◇" : ""}${ps.some((p) => p.urgent) ? " !" : ""}${ghost ? t("plan.cbn_take") : ""}` : "",
                wk ? `${t("plan.week_target", { d: dayLabel(target) })} ${isStandardDay(a, l.supplier_id, target) ? t("plan.week_standard") : t("plan.week_monday")} · ${t("plan.week_more")}` : "", t("plan.gestures")].filter(Boolean).join("\n");
              // a CBN proposal taken from a week column keeps its own delivery date (◇ included)
              const ghostDate = ghost && wk && ps.length ? ps.map((p) => p.delivery_date).sort()[0] : undefined;
              const onClick = past || isEd || locked ? undefined : () => { setActive({ aid, key: "plan", lane: li, i }); setEditing({ aid, key: "plan", lane: li, i, value: initial, initial: shownV, date: ghostDate }); };
              const onCtx = past || locked ? undefined : (e: React.MouseEvent) => {
                e.preventDefault();
                if (!wk) { comment(aid, "plan", l, i, cell?.note ?? "", initial); return; }
                setCtx({ aid, key: "plan", lane: li, i, value: isEd ? editing!.value : initial, current: "", x: e.clientX, y: e.clientY });
              };
              return <td key={i} className={cls} title={title} onClick={onClick} onMouseEnter={fill ? () => extendFill(i) : undefined} onContextMenu={onCtx}>
                {isEd ? input(editing!, `${t("row.plan")} ${l.supplier_id ?? ""} ${periodLabel(cols.periods[i], lang)}`, shownOf, (ni) => ni >= asOfIdx)
                  : past ? "" : ghost ? <span className="ghostv">{fmtQty(cbn, unit)}</span> : <>{v === 0 && !typed ? "·" : fmtQty(v, unit)}{cbn > 0 && !ghost && <span className="ghostv small"> +{fmtQty(cbn, unit)}</span>}</>}
                {(isEd || isActive) && !past && handle(aid, "plan", li, i, isEd ? editing!.value : shownV)}
              </td>;
            })}
            {spacerR}
          </tr>
        );
      }
      case "cbn": {
        const l = a.lanes[r.li], cbs = laneSeries(l, "supply_proposed"), sid = l.supplier_id ?? "";
        const propsOf = (i: number) => (a.proposals ?? []).filter((p) => (p.supplier_id ?? "") === sid && columnOf(cols, p.delivery_date) === i);
        const flagsOf = (i: number) => flags.filter((f) => f.kind === "proposal_refused" && f.article_id === aid && (f.supplier_id === sid || f.supplier_id === "") && f.date >= cols.as_of && columnOf(cols, f.date) === i);
        return (
          <tr key={r.key} style={{ height: r.h }}>
            <td>{t("row.supply_proposed")}</td>
            {spacerL}
            {visibleCols.map((i) => {
              const v = cbs?.[i] ?? 0;
              const wk = weekCol(i), past = i < asOfIdx;
              const flag = past ? undefined : flagBy.get(`proposal_refused|${aid}|${sid}|${i}`) ?? flagBy.get(`proposal_refused|${aid}||${i}`);
              const ps = v > 0 ? propsOf(i) : [];
              const urgent = ps.some((p) => p.urgent), floor = ps.some((p) => p.floor), capped = ps.some((p) => p.capped), off = ps.some((p) => p.off_standard);
              const clickable = !past && !locked && (v > 0 || !!flag);
              const cls = [...cellBase(i), "cbn", flag ? "refused" : "", clickable ? "clickable" : "", floor ? "floor" : ""].filter(Boolean).join(" ");
              const reasons = ps.map((p) => p.reason).filter(Boolean).join("\n");
              const deadline = ps.length ? t("cbn.deadline", { d: fmtDate(ps.map((p) => p.order_date).sort()[0], "EEE dd/MM", lang) }) : "";
              const title = flag ? t("cbn.refused", { v: fmtQty(flag.qty, unit) })
                : v > 0 ? [`${t("cbn.title", { v: fmtQty(v, unit) })}${urgent ? t("cbn.urgent") : ""}${floor ? t("cbn.floor") : ""}${capped ? t("cbn.capped") : ""}${off ? t("cbn.off") : ""}`, deadline, reasons, `${t("cbn.take")}${wk ? t("cbn.take_week") : t("cbn.take_day")}`].filter(Boolean).join("\n") : "";
              // a refusal blocks the supplier that day only ; a week column refuses (or restores) every proposal day of the week
              const onClick = !clickable ? undefined : !wk ? () => toggleFlag.mutate({ article_id: aid, supplier_id: flag ? (flag.supplier_id || null) : (sid || null), date: flag ? flag.date : cols.period_start[i], kind: "proposal_refused", qty: v })
                : () => { const existing = flagsOf(i); if (existing.length) toggleFlags.mutate(existing.map((f) => ({ article_id: aid, supplier_id: f.supplier_id || null, date: f.date, kind: "proposal_refused" as const }))); else toggleFlags.mutate(ps.map((p) => ({ article_id: aid, supplier_id: sid || null, date: p.delivery_date, kind: "proposal_refused" as const, qty: p.qty }))); };
              return <td key={i} className={cls} title={title} onClick={onClick}>{flag ? <s>{fmtQty(flag.qty, unit)}</s> : v === 0 ? "·" : <>{fmtQty(v, unit)}{urgent && <span className="mark urgent" aria-label="urgent">!</span>}{capped && <span className="mark capped" aria-label="plafonnée">▲</span>}{off && <span className="mark off" aria-label="hors jour standard">◇</span>}</>}</td>;
            })}
            {spacerR}
          </tr>
        );
      }
      case "adj": {
        const initialOf = (ni: number) => { const c = weekCol(ni) ? undefined : adjCell(aid, editDate(ni, "adjustments")); const av = series("adjustments", ni); return c ? (c.expression || String(c.qty)) : av ? String(av) : ""; };
        return (
          <tr key={r.key} style={{ height: r.h }}>
            <td>{t("row.adjustments")}</td>
            {spacerL}
            {visibleCols.map((i) => {
              const target = editDate(i, "adjustments");
              const cell = adjCell(aid, target);
              const v = series("adjustments", i);
              const wk = weekCol(i);
              const isEd = editing?.aid === aid && editing.key === "adjustments" && editing.i === i;
              const isActive = !isEd && active?.aid === aid && active.key === "adjustments" && active.i === i;
              const cls = [...cellBase(i), locked ? "" : "editable", isEd ? "editing" : "", isActive ? "active" : "", cell || (wk && v !== 0) ? "typed" : "", v < 0 ? "neg-val" : "", cell?.note ? "noted" : "", inFill(aid, "adjustments", 0, i) ? "fill-range" : "", flash("adjustment", aid, null, i)].filter(Boolean).join(" ");
              const initial = initialOf(i);
              const title = [cell && !wk ? `${cell.expression || cell.qty} = ${fmtQty(cell.qty, unit)}${cell.note ? ` – ${cell.note}` : ""}` : i <= initIdx ? t("adj.initial") : t("adj.any"),
                wk ? `${t("adj.week", { d: dayLabel(target) })}${cell ? ` – ${t("adj.this_day")} : ${cell.expression || cell.qty}${cell.note ? ` – ${cell.note}` : ""}` : ""} · ${t("plan.week_more")}` : "", t("plan.gestures")].filter(Boolean).join("\n");
              const onClick = isEd || locked ? undefined : () => { setActive({ aid, key: "adjustments", lane: 0, i }); setEditing({ aid, key: "adjustments", lane: 0, i, value: initial, initial }); };
              const onCtx = locked ? undefined : (e: React.MouseEvent) => {
                e.preventDefault();
                if (!wk) { comment(aid, "adjustments", null, i, cell?.note ?? "", initial); return; }
                setCtx({ aid, key: "adjustments", lane: 0, i, value: isEd ? editing!.value : initial, current: cell?.note ?? "", x: e.clientX, y: e.clientY });
              };
              return <td key={i} className={cls} title={title} onClick={onClick} onMouseEnter={fill ? () => extendFill(i) : undefined} onContextMenu={onCtx}>
                {isEd ? input(editing!, `${t("row.adjustments")} ${periodLabel(cols.periods[i], lang)}`, initialOf, () => true) : v === 0 ? "·" : `${v > 0 ? "+" : ""}${fmtQty(v, unit)}`}
                {(isEd || isActive) && handle(aid, "adjustments", 0, i, isEd ? editing!.value : initial)}
              </td>;
            })}
            {spacerR}
          </tr>
        );
      }
      case "stock": {
        const decimal = decimalOf(a);
        const f = (x: number) => fmtCellQty(x, unit, decimal);
        return (
          <tr key={r.key} style={{ height: r.h }}>
            <td>{t("row.stock_plan")}</td>
            {spacerL}
            {visibleCols.map((i) => {
              const past = i < asOfIdx;
              const v = series("stock_plan", i);
              const cov = series("coverage_plan", i);
              const shortCum = series("shortage_plan_cum", i);
              const shortDay = series("shortage_plan", i);
              const cls = [...cellBase(i), "stock", past ? "" : coverageClass(cov, a.article), shortCum > 0 ? "short" : "", decimal ? "decimal" : ""].filter(Boolean).join(" ");
              const target = series("target_stock", i);
              const erp = past ? erpOf(a, i) : undefined;
              const erpLine = erp ? `\n${t("grid.erp_cell", { d: fmtDate(erp.date, undefined, lang), q: fmtQty(erp.qty, unit), w: Object.keys(erp.warehouses).length > 1 ? ` (${Object.entries(erp.warehouses).map(([w, q]) => `${w} ${fmtQty(q, unit)}`).join(" + ")})` : "", v: fmtSigned(erp.gap, unit), p: pctOf(erp), x: erp.drift ? t("grid.erp_drift", { n: a.kpis?.erp_stock_drift_runs ?? 0 }) : erp.beyond ? "" : t("grid.erp_below") })}` : "";
              const title = (past ? `${t("stock.past", { v: f(v) })}${shortCum > 0 ? ` · ${t("stock.cum", { v: f(shortCum) })}` : ""}` : `${t("stock.line", { v: f(v), c: cov, t: f(target) })}${shortCum > 0 ? t("stock.cum_detail", { v: f(shortCum), d: shortDay > 0 ? t("stock.this_day", { v: f(shortDay) }) : "" }) : ""}`) + erpLine;
              return <td key={i} className={`${cls}${erp?.drift ? (erp.gap < 0 ? " erp-low" : " erp-high") : ""}`} title={title}>
                {!past && <span className="cov">{cov} {t("stock.cov_unit")}</span>}
                <span className="val">{f(v)}</span>
                {shortCum > 0 && <span className="shortv">−{f(shortCum)}<small> Σ</small></span>}
                {erp && <span className={`erpv ${erp.beyond ? (erp.gap < 0 ? "low" : "high") : ""}`}>ERP {fmtCellQty(erp.qty, unit, false)}</span>}
              </td>;
            })}
            {spacerR}
          </tr>
        );
      }
    }
  };

  /* ---------------- « Placer le jour… » popover of a week column */
  const dayPicker = () => {
    if (!picker) return null;
    const a = articleOf(picker.aid);
    if (!a) return null;
    const lane = picker.key === "plan" ? a.lanes[picker.lane] : null;
    const sid = lane?.supplier_id ?? null;
    const start = cols.period_start[picker.i], end = cols.period_end[picker.i] ?? start;
    const allowed = picker.key === "plan" ? weekdaysOf(linkOf(a, sid)) : null;
    const days = daysBetween(start, end).filter((d) => (picker.key === "adjustments" || d >= cols.as_of) && (isWorking(d) || (perimeter.showSaturday && isoWeekday(d) === 6) || (perimeter.showSunday && isoWeekday(d) === 7)));
    const unit = a.article.unit;
    const erpOf = (d: string) => (lane?.orders ?? []).filter((o) => o.order_type === "FIRM" && o.expected_date === d && !o.ignored).reduce((s, o) => s + o.qty_open, 0);
    const cbnOf = (d: string) => (a.proposals ?? []).filter((p) => (p.supplier_id ?? "") === (sid ?? "") && p.delivery_date === d);
    const choose = (d: string) => { write(picker.aid, picker.key, picker.lane, d, picker.value); setPicker(null); setActive({ aid: picker.aid, key: picker.key, lane: picker.lane, i: picker.i }); };
    return (
      <div className="day-picker" role="dialog" style={{ left: picker.x, top: picker.y }} onMouseDown={(e) => e.stopPropagation()}>
        <div className="dp-title">{t("picker.title", { v: picker.value.trim() ? fmtQty(Number(picker.value.replace(",", ".")) || 0, unit) || picker.value : "∅" })}<span className="subtle"> · {weekChipLabel(weekOf(picker.i), lang)}</span></div>
        {days.map((d) => {
          const std = !!allowed && allowed.has(isoWeekday(d));
          const erp = picker.key === "plan" ? erpOf(d) : 0;
          const typed = picker.key === "plan" ? planCell(picker.aid, sid, d) : adjCell(picker.aid, d);
          const cbn = picker.key === "plan" ? cbnOf(d) : [];
          const cbnQty = cbn.reduce((s, p) => s + p.qty, 0);
          return (
            <button key={d} type="button" className={`dp-day ${std ? "std" : ""} ${isWorking(d) ? "" : "off"}`} onClick={() => choose(d)}>
              <span className="d">{fmtDate(d, "EEE dd/MM", lang)}{std && <span className="dp-std">{t("picker.standard")}</span>}</span>
              <span className="meta">{erp > 0 && <span>{t("picker.erp")} {fmtQty(erp, unit)}</span>}{typed && <span className="typed">{t("picker.typed")} {fmtQty(typed.qty, unit)}</span>}{cbnQty > 0 && <span className="cbn">{t("picker.cbn")} {fmtQty(cbnQty, unit)}{cbn.some((p) => p.off_standard) ? " ◇" : ""}{cbn.some((p) => p.urgent) ? " !" : ""}</span>}</span>
            </button>
          );
        })}
        <button type="button" className="btn xs ghost" onClick={() => setPicker(null)}>{t("picker.cancel")}</button>
      </div>
    );
  };
  const contextMenu = () => {
    if (!ctx) return null;
    const a = articleOf(ctx.aid);
    const lane = a && ctx.key === "plan" ? a.lanes[ctx.lane] : null;
    return (
      <div className="ctx-menu" role="menu" style={{ left: Math.min(ctx.x, window.innerWidth - 220), top: Math.min(ctx.y, window.innerHeight - 90) }} onMouseDown={(e) => e.stopPropagation()}>
        <button type="button" role="menuitem" onClick={() => { setCtx(null); if (editing) commit(); comment(ctx.aid, ctx.key, lane, ctx.i, ctx.current, ctx.value); }}>{t("menu.comment")}</button>
        <button type="button" role="menuitem" onClick={() => { const v = editing && editing.aid === ctx.aid && editing.key === ctx.key && editing.i === ctx.i ? editing.value : ctx.value; setEditing(null); setCtx(null); openPicker(ctx.aid, ctx.key, ctx.lane, ctx.i, v, { x: ctx.x, y: ctx.y }); }}>{t("menu.place")}</button>
      </div>
    );
  };

  const topH = offsets[r0], bottomH = totalH - offsets[r1];
  return (
    <div className={`pivot-wrap ${fill ? "filling" : ""}`}>
      <div className="pivot-tools">
        <button className="btn xs ghost" onClick={() => setMenu((m) => !m)} title={t("grid.rows_title")}><Eye />{t("grid.rows")}{hidden.size ? ` (${hidden.size} ${t("grid.rows_hidden")})` : ""}</button>
        {menu && (
          <div className="row-menu" onMouseLeave={() => setMenu(false)}>
            {ROW_KEYS.map((r) => (
              <label key={r.key} className="checkbox"><input type="checkbox" checked={!hidden.has(r.key)} onChange={() => toggleRow(r.key)} />{t(r.label)}{r.lane ? <span className="subtle"> · {t("grid.per_supplier")}</span> : ""}</label>
            ))}
          </div>
        )}
        {hasDayCols && <>
          <label className="checkbox" title={t("grid.saturday")}><input type="checkbox" checked={perimeter.showSaturday} onChange={(e) => set({ showSaturday: e.target.checked })} />{t("grid.saturday")}</label>
          <label className="checkbox" title={t("grid.sunday")}><input type="checkbox" checked={perimeter.showSunday} onChange={(e) => set({ showSunday: e.target.checked })} />{t("grid.sunday")}</label>
        </>}
        <span className="subtle small" style={{ marginLeft: "auto" }}>{articles.length} {t("grid.articles")} · {m} {t("grid.columns")}</span>
      </div>
      {weeks.length > 1 && <WeekRangeBar weeks={weeks} range={range} onChange={(r) => set({ weekRange: r })} lang={lang} />}
      <div className="pivot virtual" ref={wrap} onScroll={onScroll}>
        <table style={{ width: LABEL_W + m * COL_W }}>
          <thead>
            <tr style={{ height: HEAD_H }}>
              <th style={{ width: LABEL_W, minWidth: LABEL_W, maxWidth: LABEL_W }}>{t("grid.variable")}</th>
              {leftW > 0 && <th className="spacer" style={{ width: leftW, minWidth: leftW, maxWidth: leftW }} />}
              {visibleCols.map((i) => { const p = cols.periods[i]; return <th key={p} style={{ width: COL_W, minWidth: COL_W, maxWidth: COL_W }} className={`${i === asOfIdx ? "today" : ""} ${isWeekKey(p) ? "wk wkcol" : ""}`} title={isWeekKey(p) ? `${cols.period_start[i]} → ${cols.period_end[i]}${onSwitchDay ? ` · ${t("grid.week_detail")}` : ""}` : `${fmtDate(cols.period_start[i], "EEEE dd/MM/yyyy", lang)} · ${t("grid.week")} ${isoWeekOf(cols.period_start[i])}`}
                onClick={isWeekKey(p) && onSwitchDay ? () => onSwitchDay(cols.period_start[i]) : undefined}>{periodLabel(p, lang)}</th>; })}
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
      {dayPicker()}
      {contextMenu()}
      <div className="legend small" style={{ padding: "6px 10px" }}>
        <span><span className="sw bar" style={{ background: "var(--bg-subtle)", border: "1px solid var(--border-strong)" }} />{t("legend.erp")}</span>
        <span><span className="sw bar" style={{ background: "var(--cell-yellow)" }} /><Truck size={11} style={{ verticalAlign: "middle" }} /> <i>{t("rec.transit").toLowerCase()}</i> {t("legend.transit").replace(/^[^(]*/, "")} · <span className="dot ok" style={{ verticalAlign: "middle" }} /> {t("legend.desadv_ok")} · <span className="dot ko" style={{ verticalAlign: "middle" }} /> {t("legend.desadv_ko")} · <span className="sw bar" style={{ background: "var(--cell-green)" }} />{t("legend.booked")}</span>
        <span><span className="sw bar" style={{ background: "var(--brand-soft)", boxShadow: "inset 0 -2px 0 var(--brand)" }} />{t("legend.typed")}</span>
        <span><span className="sw bar" style={{ background: "transparent", border: "1px dashed var(--warning)" }} />{t("legend.cbn")} · <b style={{ color: "var(--critical-fg)" }}>!</b> {t("legend.marks")} · <b style={{ color: "var(--warning-fg)" }}>▲</b> {t("legend.capped")} · <b style={{ color: "var(--brand-fg)" }}>◇</b> {t("legend.off")}</span>
        <span><span className="sw bar" style={{ background: "var(--cell-neg-strong)" }} />{t("legend.short")}</span>
        <span><span className="sw bar" style={{ background: "var(--cell-yellow)" }} />{t("legend.firm")} · <span className="sw bar" style={{ background: "var(--cell-green)" }} />{t("legend.firm_settled")} · <span className="sw bar" style={{ background: "var(--cell-red)" }} />{t("legend.firm_ignored")} · <Truck size={10} style={{ verticalAlign: -1, color: "var(--brand)" }} /> {t("legend.firm_transport")}</span>
        {hasErp && <span>{t("legend.erp_stock")}</span>}
        <span>{t("legend.week")} · <EyeOff size={11} style={{ verticalAlign: "middle" }} /> {t("legend.hide")}</span>
      </div>
    </div>
  );
}
