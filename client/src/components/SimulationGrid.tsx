import { Fragment, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { useWrite } from "@/lib/queries";
import { useToast } from "@/components/ui";
import type { EntryDraft } from "@/components/EntryDrawer";
import type { PlanTarget } from "@/components/PlanDrawer";
import { ORDER_STATUS_LABELS, ORDER_TYPE_LABELS, ORIGIN_LABELS, fmtDate, fmtQty, isWeekKey, isWeekend, periodLabel } from "@/lib/format";
import type { ArticleRef, CellOut, LinkRef, OrderStateOut, PlanLineState, SeriesOut, SupplyEventOut } from "@/lib/types";

/** Rows of the simulation grid, in the blocks and order of the specification. */
export const GRID_ROWS: { key: string; label: string; group: string }[] = [
  { key: "consumed", label: "Consommé", group: "Conso et besoins" },
  { key: "required", label: "Requis", group: "Conso et besoins" },
  { key: "orders_firm", label: "Ferme", group: "Données ERP" },
  { key: "orders_forecast", label: "Prévisionnel", group: "Données ERP" },
  { key: "receipts", label: "Reçu", group: "Données ERP" },
  { key: "plan", label: "Plan", group: "Approvisionnement" },
  { key: "adjustments", label: "Ajustement", group: "Approvisionnement" },
  { key: "stock_erp", label: "Scenario ERP", group: "Projection de stock" },
  { key: "stock_plan", label: "Scenario Plan", group: "Projection de stock" },
];
const GROUPS = Array.from(new Set(GRID_ROWS.map((r) => r.group)));

export interface GridColumns { as_of: string; periods: string[]; period_start: string[]; period_end: string[]; }
export interface GridRowArticle { article: ArticleRef; series: SeriesOut[]; events: SupplyEventOut[]; suppliers: LinkRef[]; orders: OrderStateOut[]; plan_lines: PlanLineState[]; kpis?: { severity?: string | null; first_stockout_plan?: string | null }; }

/** index of the column containing a date */
export function columnOf(cols: GridColumns, date: string): number {
  return cols.period_start.findIndex((p, k) => p <= date && date <= (cols.period_end[k] ?? "9999-12-31"));
}

/** Coverage colour of a stock cell: red / yellow thresholds, green = normal, no colour = overstock. */
export function coverageClass(days: number, a: ArticleRef): string {
  if (days <= a.alert_red_days) return "cov-red";
  if (days <= a.alert_yellow_days) return "cov-yellow";
  if (days >= a.overstock_days) return "cov-over";
  return "cov-ok";
}

/**
 * The simulation grid: one column per day or ISO week, one block of rows per article.
 * Plan and adjustment cells are typed directly (quantity or expression) ; the order rows and the
 * plan row open the plan drawer ; stock cells carry the coverage (small, top-left) and the unserved
 * demand (shortage) when any.  Row groups can be collapsed.  Used by the article page and by the
 * supply table.
 */
export function SimulationGrid({ cols, articles, cells, onEntry, onPlan, showArticleRows }: {
  cols: GridColumns; articles: GridRowArticle[]; cells: CellOut[]; onEntry: (draft: EntryDraft) => void;
  onPlan: (target: PlanTarget) => void; showArticleRows?: boolean;
}) {
  const toast = useToast();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [editing, setEditing] = useState<{ aid: string; key: string; i: number; value: string } | null>(null);
  const saveCell = useWrite((c: { article_id: string; date: string; expression: string }) => api.put<CellOut | null>("/api/entries/cells", { ...c, kind: "adjustment" }),
    (out) => toast.push(out ? `Ajustement ${fmtDate(out.date)} = ${fmtQty(out.qty)}` : "Ajustement effacé", "success"));
  const savePlan = useWrite((c: { article_id: string; date: string; expression: string }) => api.put("/api/entries/plan/cell", c),
    () => toast.push("Plan mis à jour", "success"));

  const asOfIdx = useMemo(() => columnOf(cols, cols.as_of), [cols]);
  const cellsBy = useMemo(() => {
    const m = new Map<string, CellOut[]>();
    cells.forEach((c) => {
      const i = columnOf(cols, c.date);
      if (i >= 0) m.set(`${c.article_id}|${i}`, [...(m.get(`${c.article_id}|${i}`) ?? []), c]);
    });
    return m;
  }, [cells, cols]);

  const linesOf = (a: GridRowArticle, i: number) => a.plan_lines.filter((l) => columnOf(cols, l.date) === i);
  const ordersOf = (a: GridRowArticle, i: number, type: "FIRM" | "FORECAST") => a.orders.filter((o) => (type === "FIRM" ? o.order_type === "FIRM" : o.order_type !== "FIRM") && columnOf(cols, o.expected_date) === i);
  const openPlan = (a: GridRowArticle, i: number) => onPlan({ article_id: a.article.article_id, unit: a.article.unit, title: `${a.article.article_id} · plan ${periodLabel(cols.periods[i])}`, date: cols.period_start[i], lines: a.plan_lines, orders: a.orders, asOf: cols.as_of });
  const startEdit = (aid: string, key: string, i: number, current: string) => setEditing({ aid, key, i, value: current });
  const commitEdit = (next?: { aid: string; key: string; i: number; value: string }) => {
    if (!editing) return;
    const date = cols.period_start[editing.i];
    if (editing.key === "adjustments") {
      const mine = cellsBy.get(`${editing.aid}|${editing.i}`) ?? [];
      const previous = mine.length === 1 ? (mine[0].expression || String(mine[0].qty)) : "";
      if (editing.value.trim() !== previous.trim()) saveCell.mutate({ article_id: editing.aid, date: mine.length === 1 ? mine[0].date : date, expression: editing.value });
    } else if (editing.key === "plan") {
      savePlan.mutate({ article_id: editing.aid, date, expression: editing.value });
    }
    setEditing(null);
    if (next) setEditing(next);
  };
  const toggle = (g: string) => setCollapsed((c) => ({ ...c, [g]: !c[g] }));

  return (
    <div className="pivot">
      <table>
        <thead>
          <tr>
            <th>Variable</th>
            {cols.periods.map((p, i) => <th key={p} className={`${i === asOfIdx ? "today" : ""} ${isWeekKey(p) ? "wk wkcol" : ""}`} title={isWeekKey(p) ? `${cols.period_start[i]} → ${cols.period_end[i]}` : cols.period_start[i]}>{periodLabel(p)}</th>)}
          </tr>
        </thead>
        <tbody>
          {articles.map((a) => {
            const aid = a.article.article_id;
            const series = Object.fromEntries(a.series.map((s) => [s.key, s.values]));
            const get = (k: string, i: number) => series[k]?.[i] ?? 0;
            return (
              <Fragment key={aid}>
                {showArticleRows && (
                  <tr className="article-head">
                    <td><Link to={`/articles/${encodeURIComponent(aid)}`}>{aid}</Link> <span className="subtle" style={{ fontWeight: 400 }}>· {a.article.designation}</span></td>
                    {cols.periods.map((p) => <td key={p} />)}
                  </tr>
                )}
                {GROUPS.map((g) => {
                  const rows = GRID_ROWS.filter((r) => r.group === g);
                  const isCollapsed = !!collapsed[g];
                  return (
                    <Fragment key={`${aid}-${g}`}>
                      <tr className="group-head" onClick={() => toggle(g)} title={isCollapsed ? "Afficher le bloc" : "Masquer le bloc"}>
                        <td>{isCollapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}{g}</td>
                        {cols.periods.map((p) => <td key={p} />)}
                      </tr>
                      {!isCollapsed && rows.map((r) => (
                        <tr key={`${aid}-${r.key}`}>
                          <td>{r.label}</td>
                          {cols.periods.map((p, i) => {
                            const past = i < asOfIdx;
                            const weekend = !isWeekKey(p) && isWeekend(cols.period_start[i]);
                            const base = [past || weekend ? "past" : "", i === asOfIdx ? "today" : "", isWeekKey(p) ? "wkcol" : ""];
                            const isEditing = editing?.aid === aid && editing.key === r.key && editing.i === i;
                            const input = (initial: string) => (
                              <input className="cell-input" autoFocus value={editing!.value} aria-label={`${r.label} ${periodLabel(p)}`}
                                onChange={(e) => setEditing({ ...editing!, value: e.target.value })} onBlur={() => commitEdit()}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter") { e.preventDefault(); commitEdit(); }
                                  else if (e.key === "Escape") { e.preventDefault(); setEditing(null); }
                                  else if (e.key === "Tab") { e.preventDefault(); const ni = i + (e.shiftKey ? -1 : 1); if (ni >= 0 && ni < cols.periods.length && (r.key === "adjustments" || ni >= asOfIdx)) commitEdit({ aid, key: r.key, i: ni, value: initial }); else commitEdit(); }
                                }} />
                            );
                            // ---- stock cells: value + coverage + shortage
                            if (r.key === "stock_erp" || r.key === "stock_plan") {
                              const v = get(r.key, i);
                              const cov = get(r.key === "stock_erp" ? "coverage_erp" : "coverage_plan", i);
                              const short = get(r.key === "stock_erp" ? "shortage_erp" : "shortage_plan", i);
                              const cls = [...base, "stock", past ? "" : coverageClass(cov, a.article), short > 0 ? "short" : ""].filter(Boolean).join(" ");
                              const title = past ? `stock reconstitué ${fmtQty(v, a.article.unit)}` : `stock ${fmtQty(v, a.article.unit)} · couverture ${cov} j${short > 0 ? ` · manque ${fmtQty(short, a.article.unit)} (besoin non servi)` : ""}`;
                              return <td key={i} className={cls} title={title}>
                                {!past && <span className="cov">{cov} j</span>}
                                <span className="val">{fmtQty(v, a.article.unit)}</span>
                                {short > 0 && <span className="shortv">−{fmtQty(short, a.article.unit)}</span>}
                              </td>;
                            }
                            // ---- plan: ERP as is + lines (typed) + CBN (italic) ; past: expired lines in grey
                            if (r.key === "plan") {
                              const lines = linesOf(a, i);
                              const cbn = get("supply_proposed", i);
                              const v = past ? get("plan_hist", i) : get("plan", i) + cbn;
                              const stored = lines.some((l) => l.origin === "override" || l.origin === "free");
                              const cls = [...base, "editable", isEditing ? "editing" : "", stored ? "typed" : "", !stored && cbn ? "cbn" : "", lines.length ? "event" : ""].filter(Boolean).join(" ");
                              const title = lines.map((l) => `${ORIGIN_LABELS[l.origin]} ${l.order_id ?? ""} ${fmtQty(l.qty, a.article.unit)} le ${fmtDate(l.date)}${l.erp_date && (l.erp_date !== l.date || l.erp_qty !== l.qty) ? ` (ERP ${fmtDate(l.erp_date)} · ${fmtQty(l.erp_qty ?? 0, a.article.unit)})` : ""}`).join("\n")
                                || (past ? "" : "Taper une quantité (ligne libre ou quantité de la ligne du jour) ; cliquer pour la liste du jour");
                              const single = lines.filter((l) => l.origin === "erp" || l.origin === "override" || l.origin === "free");
                              const initial = single.length === 1 ? String(single[0].qty) : single.length === 0 && cbn ? String(cbn) : "";
                              return <td key={i} className={cls} title={title} onClick={past || isEditing ? undefined : (e) => {
                                if ((e.target as HTMLElement).closest(".more") || lines.length > 1 || (lines.length === 1 && single.length === 0 && !cbn)) openPlan(a, i);
                                else if (lines.length === 0 && !cbn) startEdit(aid, r.key, i, "");
                                else if (single.length <= 1) startEdit(aid, r.key, i, initial);
                                else openPlan(a, i);
                              }} onDoubleClick={past ? undefined : () => openPlan(a, i)}>
                                {isEditing ? input(initial) : v === 0 && !stored ? "·" : fmtQty(v, a.article.unit)}
                                {!past && !isEditing && (lines.length > 0 || cbn > 0) && <span className="more" title="Liste du jour" onClick={(e) => { e.stopPropagation(); openPlan(a, i); }}>…</span>}
                              </td>;
                            }
                            // ---- adjustments: typed, any column
                            if (r.key === "adjustments") {
                              const mine = cellsBy.get(`${aid}|${i}`) ?? [];
                              const v = get("adjustments", i);
                              const cls = [...base, "editable", isEditing ? "editing" : "", mine.length ? "typed" : "", v < 0 ? "neg-val" : ""].filter(Boolean).join(" ");
                              const initial = mine.length === 1 ? (mine[0].expression || String(mine[0].qty)) : mine.length ? String(v) : "";
                              const title = mine.map((c) => `${fmtDate(c.date)} : ${c.expression || c.qty} = ${fmtQty(c.qty, a.article.unit)}${c.note ? ` – ${c.note}` : ""}`).join("\n") || (past ? "Ajustement passé : corrige le stock de référence" : "Quantité signée ou formule (ex. -(30+20))");
                              return <td key={i} className={cls} title={title} onClick={isEditing ? undefined : () => startEdit(aid, r.key, i, initial)}>
                                {isEditing ? input(initial) : v === 0 ? "·" : `${v > 0 ? "+" : ""}${fmtQty(v, a.article.unit)}`}
                              </td>;
                            }
                            // ---- ERP orders: firm (past = ordered qty, future = open) / forecast ; receipts
                            if (r.key === "orders_firm" || r.key === "orders_forecast" || r.key === "receipts") {
                              const v = r.key === "orders_firm" ? get("orders_firm", i) + get("orders_firm_hist", i) : get(r.key, i);
                              const ords = r.key === "receipts" ? [] : ordersOf(a, i, r.key === "orders_firm" ? "FIRM" : "FORECAST");
                              const late = ords.some((o) => o.status === "not_received");
                              const cls = [...base, ords.length ? "event" : "", late ? "late" : "", r.key !== "receipts" || !past ? "editable" : ""].filter(Boolean).join(" ");
                              const title = ords.map((o) => `${o.order_id} · ${ORDER_TYPE_LABELS[o.order_type]} ${fmtQty(o.qty_open, a.article.unit)} restant / ${fmtQty(o.qty_ordered, a.article.unit)} commandé · ${ORDER_STATUS_LABELS[o.status]}${o.plan_dates.length ? ` · plan : ${o.plan_dates.map((x) => fmtDate(x)).join(", ")}` : ""}`).join("\n")
                                || (r.key === "receipts" ? (past || i === asOfIdx ? "Cliquer pour saisir une réception" : "") : past ? "" : r.key === "orders_firm" ? "Cliquer pour saisir une commande ferme hors ERP" : "");
                              const onClick = ords.length ? () => onPlan({ article_id: aid, unit: a.article.unit, title: `${aid} · commandes ${periodLabel(p)}`, date: past ? undefined : cols.period_start[i], lines: a.plan_lines, orders: a.orders, asOf: cols.as_of })
                                : r.key === "receipts" && (past || i === asOfIdx) ? () => onEntry({ kind: "receipt", article_id: aid, date: cols.period_start[i], supplier_id: a.suppliers[0]?.supplier_id ?? null })
                                : r.key === "orders_firm" && !past ? () => onEntry({ kind: "order", article_id: aid, date: cols.period_start[i], supplier_id: a.suppliers[0]?.supplier_id ?? null, order_type: "FIRM" })
                                : undefined;
                              return <td key={i} className={cls} title={title} onClick={onClick}>{v === 0 ? "·" : fmtQty(v, a.article.unit)}</td>;
                            }
                            // ---- consumption / requirement
                            const v = get(r.key, i);
                            return <td key={i} className={base.filter(Boolean).join(" ")}>{v === 0 ? "·" : fmtQty(v, a.article.unit)}</td>;
                          })}
                        </tr>
                      ))}
                    </Fragment>
                  );
                })}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
