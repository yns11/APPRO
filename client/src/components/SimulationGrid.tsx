import { Fragment, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight, Eye, EyeOff } from "lucide-react";
import { api } from "@/lib/api";
import { useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { useToast } from "@/components/ui";
import { ORDER_TYPE_LABELS, fmtDate, fmtQty, isWeekKey, isWeekend, periodLabel } from "@/lib/format";
import type { AdjustmentOut, ArticleRef, LaneOut, PlanCellOut, SeriesOut } from "@/lib/types";

/** Rows of the grid that can be hidden (for every article) ; lane rows are repeated per supplier. */
export const ROW_LABELS: { key: string; label: string; lane?: boolean }[] = [
  { key: "demand", label: "Besoin" },
  { key: "orders_firm", label: "Ferme", lane: true },
  { key: "orders_forecast", label: "Prévisionnel", lane: true },
  { key: "receipts", label: "Reçu", lane: true },
  { key: "plan", label: "Plan", lane: true },
  { key: "supply_proposed", label: "Proposition CBN" },
  { key: "adjustments", label: "Ajustement" },
  { key: "stock_erp", label: "Scenario ERP" },
  { key: "stock_plan", label: "Scenario Plan" },
];

export interface GridColumns { as_of: string; periods: string[]; period_start: string[]; period_end: string[]; }
export interface GridRowArticle { article: ArticleRef; series: SeriesOut[]; lanes: LaneOut[]; kpis?: { severity?: string | null }; }

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

/** ``initial`` is what the cell showed before editing: nothing is saved when the value is unchanged. */
type Edit = { aid: string; key: "plan" | "adjustments"; lane: number; i: number; value: string; initial: string };

/**
 * The simulation grid: one column per day or ISO week, one block per article.
 *
 * Two rows are typed, nothing else: **Plan** (one row per supplier ; empty = the ERP quantity of
 * the day, a typed value = the planner's decision, 0 included, a blank restores the ERP) and
 * **Ajustement** (signed, any date).  A CBN proposal is shown greyed in an empty plan cell: one click
 * then Enter takes it.  Right-click on a typed cell adds a comment.  Week columns are read-only:
 * clicking one switches to the day view.  Rows can be hidden for every article (eye menu).
 */
export function SimulationGrid({ cols, articles, planCells, adjustments, showArticleRows, onSwitchDay }: {
  cols: GridColumns; articles: GridRowArticle[]; planCells: PlanCellOut[]; adjustments: AdjustmentOut[];
  showArticleRows?: boolean; onSwitchDay?: (date: string) => void;
}) {
  const toast = useToast();
  const { perimeter, set } = usePerimeter();
  const hidden = new Set(perimeter.hiddenRows);
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [menu, setMenu] = useState(false);
  const [editing, setEditing] = useState<Edit | null>(null);
  const saveAdj = useWrite((c: { article_id: string; date: string; expression?: string; note?: string }) => api.put<AdjustmentOut | null>("/api/entries/adjustments", c),
    (out) => toast.push(out ? `Ajustement ${fmtDate(out.date)} = ${fmtQty(out.qty)}` : "Ajustement effacé", "success"));
  const savePlan = useWrite((c: { article_id: string; supplier_id: string | null; date: string; expression?: string; note?: string }) => api.put<PlanCellOut | null>("/api/entries/plan", c),
    (out) => toast.push(out ? `Plan ${fmtDate(out.date)} = ${fmtQty(out.qty)}` : "Retour à l'ERP", "success"));

  const asOfIdx = useMemo(() => columnOf(cols, cols.as_of), [cols]);
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

  const input = (e: Edit, label: string, initial: (i: number) => string, canTab: (ni: number) => boolean) => (
    <input className="cell-input" autoFocus value={e.value} aria-label={label}
      onChange={(ev) => setEditing({ ...e, value: ev.target.value })} onBlur={() => commit()}
      onKeyDown={(ev) => {
        if (ev.key === "Enter") { ev.preventDefault(); commit(); }
        else if (ev.key === "Escape") { ev.preventDefault(); setEditing(null); }
        else if (ev.key === "Tab") { ev.preventDefault(); const ni = e.i + (ev.shiftKey ? -1 : 1); if (ni >= 0 && ni < cols.periods.length && canTab(ni)) { const nv = initial(ni); commit({ ...e, i: ni, value: nv, initial: nv }); } else commit(); }
      }} />
  );

  return (
    <div className="pivot-wrap">
      <div className="pivot-tools">
        <button className="btn xs ghost" onClick={() => setMenu((m) => !m)} title="Masquer / afficher des lignes (pour tous les articles)"><Eye />Lignes{hidden.size ? ` (${hidden.size} masquée${hidden.size > 1 ? "s" : ""})` : ""}</button>
        {menu && (
          <div className="row-menu" onMouseLeave={() => setMenu(false)}>
            {ROW_LABELS.map((r) => (
              <label key={r.key} className="checkbox"><input type="checkbox" checked={!hidden.has(r.key)} onChange={() => toggleRow(r.key)} />{r.label}{r.lane ? <span className="subtle"> · par fournisseur</span> : ""}</label>
            ))}
          </div>
        )}
      </div>
      <div className="pivot">
        <table>
          <thead>
            <tr>
              <th>Variable</th>
              {cols.periods.map((p, i) => <th key={p} className={`${i === asOfIdx ? "today" : ""} ${isWeekKey(p) ? "wk wkcol" : ""}`} title={isWeekKey(p) ? `${cols.period_start[i]} → ${cols.period_end[i]}${onSwitchDay ? " · cliquer pour le détail par jour" : ""}` : cols.period_start[i]}
                onClick={isWeekKey(p) && onSwitchDay ? () => onSwitchDay(cols.period_start[i]) : undefined} style={isWeekKey(p) && onSwitchDay ? { cursor: "pointer" } : undefined}>{periodLabel(p)}</th>)}
            </tr>
          </thead>
          <tbody>
            {articles.map((a) => {
              const aid = a.article.article_id;
              const unit = a.article.unit;
              const series = Object.fromEntries(a.series.map((s) => [s.key, s.values]));
              const get = (k: string, i: number) => series[k]?.[i] ?? 0;
              const laneGet = (l: LaneOut, k: string, i: number) => l.series.find((s) => s.key === k)?.values[i] ?? 0;
              const several = a.lanes.length > 1;
              const cellClasses = (p: string, i: number) => {
                const past = i < asOfIdx;
                return [past || (!isWeekKey(p) && isWeekend(cols.period_start[i])) ? "past" : "", i === asOfIdx ? "today" : "", isWeekKey(p) ? "wkcol" : ""];
              };
              const readOnlyCol = (i: number) => isWeekKey(cols.periods[i]);
              const groupRow = (id: string, label: string) => (
                <tr className="group-head" key={`${aid}-${id}`} onClick={() => toggleGroup(`${aid}-${id}`)} title={collapsed[`${aid}-${id}`] ? "Afficher le bloc" : "Masquer le bloc"}>
                  <td>{collapsed[`${aid}-${id}`] ? <ChevronRight size={12} /> : <ChevronDown size={12} />}{label}</td>
                  {cols.periods.map((p) => <td key={p} />)}
                </tr>
              );
              const valueRow = (id: string, label: string, val: (i: number) => number, title?: (i: number) => string, extraCls?: (i: number) => string) => (
                <tr key={`${aid}-${id}`}>
                  <td>{label}</td>
                  {cols.periods.map((p, i) => { const v = val(i); return <td key={i} className={[...cellClasses(p, i), extraCls?.(i) ?? ""].filter(Boolean).join(" ")} title={title?.(i)}>{v === 0 ? "·" : fmtQty(v, unit)}</td>; })}
                </tr>
              );
              const ordersTitle = (l: LaneOut, type: "FIRM" | "FORECAST", i: number) => l.orders.filter((o) => (o.order_type === "FIRM") === (type === "FIRM") && columnOf(cols, o.expected_date) === i)
                .map((o) => `${ORDER_TYPE_LABELS[o.order_type]} ${fmtQty(o.qty_open, unit)} restant / ${fmtQty(o.qty_ordered, unit)} commandé${o.ref ? ` · ${o.ref}` : ""}`).join("\n");

              const laneRows = (l: LaneOut, li: number) => {
                const key = `${aid}-lane-${li}`;
                if (collapsed[key]) return null;
                return (
                  <Fragment key={key}>
                    {!hidden.has("orders_firm") && valueRow(`firm-${li}`, "Ferme", (i) => laneGet(l, "orders_firm", i) + laneGet(l, "orders_firm_hist", i), (i) => ordersTitle(l, "FIRM", i), (i) => (ordersTitle(l, "FIRM", i) ? "event" : ""))}
                    {!hidden.has("orders_forecast") && valueRow(`fcst-${li}`, "Prévisionnel", (i) => laneGet(l, "orders_forecast", i), (i) => ordersTitle(l, "FORECAST", i), (i) => (ordersTitle(l, "FORECAST", i) ? "event" : ""))}
                    {!hidden.has("receipts") && valueRow(`rec-${li}`, "Reçu", (i) => laneGet(l, "receipts", i))}
                    {!hidden.has("plan") && (
                      <tr key={`${aid}-plan-${li}`}>
                        <td>Plan</td>
                        {cols.periods.map((p, i) => {
                          const past = i < asOfIdx;
                          const typed = l.plan_typed[i];
                          const cell = planBy.get(`${aid}|${l.supplier_id ?? ""}|${i}`);
                          const v = laneGet(l, "plan", i);
                          const cbn = laneGet(l, "supply_proposed", i);
                          const isEd = editing?.aid === aid && editing.key === "plan" && editing.lane === li && editing.i === i;
                          const ghost = !past && !typed && v === 0 && cbn > 0;
                          const cls = [...cellClasses(p, i), past || readOnlyCol(i) ? "" : "editable", isEd ? "editing" : "", typed ? "typed" : "erp", ghost ? "ghost" : "", cell?.note ? "noted" : ""].filter(Boolean).join(" ");
                          const shown = typed ? (cell?.expression || String(v)) : v ? String(v) : "";
                          const initial = ghost ? String(cbn) : shown;
                          const title = past ? "" : [typed ? `Saisi : ${fmtQty(v, unit)}${cell?.note ? ` – ${cell.note}` : ""} (vider = retour à l'ERP)` : v ? `ERP : ${fmtQty(v, unit)} – taper une quantité pour décider (0 = rien attendu)` : "Vide = rien dans l'ERP – taper une quantité pour planifier une livraison",
                            cbn ? `Proposition CBN : ${fmtQty(cbn, unit)}${ghost ? " (cliquer puis Entrée pour la reprendre)" : ""}` : "", readOnlyCol(i) ? "Semaine agrégée : cliquer l'en-tête pour saisir par jour" : "Clic droit : commentaire"].filter(Boolean).join("\n");
                          const onClick = past || isEd ? undefined : readOnlyCol(i) ? () => onSwitchDay?.(cols.period_start[i]) : () => setEditing({ aid, key: "plan", lane: li, i, value: initial, initial: shown });
                          return <td key={i} className={cls} title={title} onClick={onClick}
                            onContextMenu={past || readOnlyCol(i) ? undefined : (e) => { e.preventDefault(); comment(aid, "plan", l, i, cell?.note ?? "", initial); }}>
                            {isEd ? input(editing!, `Plan ${l.supplier_id ?? ""} ${periodLabel(p)}`, (ni) => { const c = planBy.get(`${aid}|${l.supplier_id ?? ""}|${ni}`); const pv = laneGet(l, "plan", ni); return l.plan_typed[ni] ? (c?.expression || String(pv)) : pv ? String(pv) : ""; }, (ni) => ni >= asOfIdx && !readOnlyCol(ni))
                              : past ? "" : ghost ? <span className="ghostv">{fmtQty(cbn, unit)}</span> : <>{v === 0 && !typed ? "·" : fmtQty(v, unit)}{cbn > 0 && !ghost && <span className="ghostv small"> +{fmtQty(cbn, unit)}</span>}</>}
                          </td>;
                        })}
                      </tr>
                    )}
                  </Fragment>
                );
              };

              return (
                <Fragment key={aid}>
                  {showArticleRows && (
                    <tr className="article-head">
                      <td><Link to={`/articles/${encodeURIComponent(aid)}`}>{aid}</Link> <span className="subtle" style={{ fontWeight: 400 }}>· {a.article.designation}</span></td>
                      {cols.periods.map((p) => <td key={p} />)}
                    </tr>
                  )}
                  {!hidden.has("demand") && valueRow("demand", "Besoin", (i) => get("demand", i), (i) => (i < asOfIdx ? "consommé (réel × nomenclature)" : "requis (PDP × nomenclature)"))}
                  {a.lanes.map((l, li) => (
                    <Fragment key={`${aid}-g-${li}`}>
                      {groupRow(`lane-${li}`, several ? `${l.supplier_id ?? "Sans fournisseur"}${l.name ? ` · ${l.name}` : ""}${l.backlog_qty > 0 ? ` · backlog ${fmtQty(l.backlog_qty, unit)}` : ""}` : `ERP & plan${l.supplier_id ? ` · ${l.supplier_id}` : ""}${l.backlog_qty > 0 ? ` · backlog ${fmtQty(l.backlog_qty, unit)}` : ""}`)}
                      {laneRows(l, li)}
                    </Fragment>
                  ))}
                  {!hidden.has("supply_proposed") && valueRow("cbn", "Proposition CBN", (i) => get("supply_proposed", i), () => "besoin net calculé sur le Scenario Plan ; reprendre en tapant la quantité dans la ligne Plan", () => "cbn")}
                  {!hidden.has("adjustments") && (
                    <tr key={`${aid}-adj`}>
                      <td>Ajustement</td>
                      {cols.periods.map((p, i) => {
                        const cell = adjBy.get(`${aid}|${i}`);
                        const v = get("adjustments", i);
                        const isEd = editing?.aid === aid && editing.key === "adjustments" && editing.i === i;
                        const cls = [...cellClasses(p, i), readOnlyCol(i) ? "" : "editable", isEd ? "editing" : "", cell ? "typed" : "", v < 0 ? "neg-val" : "", cell?.note ? "noted" : ""].filter(Boolean).join(" ");
                        const initial = cell ? (cell.expression || String(cell.qty)) : v ? String(v) : "";
                        const title = [cell ? `${cell.expression || cell.qty} = ${fmtQty(cell.qty, unit)}${cell.note ? ` – ${cell.note}` : ""}` : i <= asOfIdx ? "Quantité signée : corrige le stock de référence" : "Quantité signée ou formule (ex. -(30+20)) : mouvement prévu", readOnlyCol(i) ? "Semaine agrégée : cliquer l'en-tête pour saisir par jour" : "Clic droit : commentaire"].join("\n");
                        return <td key={i} className={cls} title={title} onClick={isEd ? undefined : readOnlyCol(i) ? () => onSwitchDay?.(cols.period_start[i]) : () => setEditing({ aid, key: "adjustments", lane: 0, i, value: initial, initial })}
                          onContextMenu={readOnlyCol(i) ? undefined : (e) => { e.preventDefault(); comment(aid, "adjustments", null, i, cell?.note ?? "", initial); }}>
                          {isEd ? input(editing!, `Ajustement ${periodLabel(p)}`, (ni) => { const c = adjBy.get(`${aid}|${ni}`); const av = get("adjustments", ni); return c ? (c.expression || String(c.qty)) : av ? String(av) : ""; }, (ni) => !readOnlyCol(ni)) : v === 0 ? "·" : `${v > 0 ? "+" : ""}${fmtQty(v, unit)}`}
                        </td>;
                      })}
                    </tr>
                  )}
                  {(["stock_erp", "stock_plan"] as const).filter((k) => !hidden.has(k)).map((k) => (
                    <tr key={`${aid}-${k}`}>
                      <td>{k === "stock_erp" ? "Scenario ERP" : "Scenario Plan"}</td>
                      {cols.periods.map((p, i) => {
                        const past = i < asOfIdx;
                        const v = get(k, i);
                        const cov = get(k === "stock_erp" ? "coverage_erp" : "coverage_plan", i);
                        const short = get(k === "stock_erp" ? "shortage_erp" : "shortage_plan", i);
                        const cls = [...cellClasses(p, i), "stock", past ? "" : coverageClass(cov, a.article), short > 0 ? "short" : ""].filter(Boolean).join(" ");
                        const title = past ? `stock reconstitué ${fmtQty(v, unit)}` : `stock ${fmtQty(v, unit)} · couverture ${cov} j${short > 0 ? ` · manque ${fmtQty(short, unit)} (besoin non servi)` : ""}`;
                        return <td key={i} className={cls} title={title}>
                          {!past && <span className="cov">{cov} j</span>}
                          <span className="val">{fmtQty(v, unit)}</span>
                          {short > 0 && <span className="shortv">−{fmtQty(short, unit)}</span>}
                        </td>;
                      })}
                    </tr>
                  ))}
                </Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      <div className="legend small" style={{ padding: "6px 10px" }}>
        <span><span className="sw bar" style={{ background: "var(--bg-subtle)", border: "1px solid var(--border-strong)" }} />vide = ERP</span>
        <span><span className="sw bar" style={{ background: "var(--brand-soft)", boxShadow: "inset 0 -2px 0 var(--brand)" }} />chiffre = votre plan (0 = rien attendu, vider = retour ERP)</span>
        <span><span className="sw bar" style={{ background: "transparent", border: "1px dashed var(--warning)" }} />grisé = proposition CBN, cliquer puis Entrée pour la reprendre</span>
        <span>clic droit = commentaire · <EyeOff size={11} style={{ verticalAlign: "middle" }} /> masquer des lignes</span>
      </div>
    </div>
  );
}
