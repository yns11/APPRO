import { Fragment, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { useWrite } from "@/lib/queries";
import { useToast } from "@/components/ui";
import type { EntryDraft } from "@/components/EntryDrawer";
import { KIND_LABELS, ORDER_TYPE_LABELS, fmtDate, fmtQty, isWeekKey, isWeekend, periodLabel } from "@/lib/format";
import type { ArticleRef, CellKind, CellOut, LinkRef, SeriesOut, SupplyEventOut } from "@/lib/types";

/** Rows of the grid whose cells are typed directly (quantity or arithmetic expression). */
export const CELL_ROWS: Record<string, CellKind> = { sim_receipts: "sim_receipt", adjustments: "adjustment" };

export const GRID_ROWS: { key: string; label: string; group: string; cls?: (v: number, a: ArticleRef) => string }[] = [
  { key: "demand", label: "Besoin (composants)", group: "Besoins" },
  { key: "demand_plan", label: "dont plan seul", group: "Besoins" },
  { key: "supply_firm", label: "Commandes fermes (F)", group: "Approvisionnements" },
  { key: "supply_forecast", label: "Commandes prévisionnelles ERP (P)", group: "Approvisionnements" },
  { key: "receipts", label: "Réceptions (R)", group: "Approvisionnements" },
  { key: "sim_receipts", label: "Réceptions simulées (S)", group: "Approvisionnements", cls: (v) => (v !== 0 ? "sim" : "") },
  { key: "supply_proposed", label: "Complément CBN", group: "Approvisionnements", cls: (v) => (v !== 0 ? "sim" : "") },
  { key: "adjustments", label: "Ajustements (A)", group: "Approvisionnements" },
  { key: "stock_firm", label: "Stock ferme", group: "Stocks", cls: () => "stock" },
  { key: "stock_forecast", label: "Stock prévisionnel", group: "Stocks", cls: () => "stock" },
  { key: "stock_sim", label: "Stock simulé", group: "Stocks", cls: () => "stock" },
  { key: "shortage_firm", label: "Manque ferme (besoin non servi)", group: "Manques", cls: (v) => (v > 0 ? "neg" : "") },
  { key: "shortage_forecast", label: "Manque prévisionnel", group: "Manques", cls: (v) => (v > 0 ? "neg" : "") },
  { key: "shortage_sim", label: "Manque simulé", group: "Manques", cls: (v) => (v > 0 ? "neg" : "") },
  { key: "coverage_firm", label: "Couverture ferme (j)", group: "Couverture", cls: (v, a) => (v <= a.alert_red_days ? "red" : v <= a.alert_yellow_days ? "yellow" : v >= a.overstock_days ? "green" : "") },
  { key: "coverage_forecast", label: "Couverture prévisionnelle (j)", group: "Couverture", cls: (v, a) => (v <= a.alert_red_days ? "red" : v <= a.alert_yellow_days ? "yellow" : v >= a.overstock_days ? "green" : "") },
  { key: "coverage_sim", label: "Couverture simulée (j)", group: "Couverture", cls: (v, a) => (v <= a.alert_red_days ? "red" : v <= a.alert_yellow_days ? "yellow" : v >= a.overstock_days ? "green" : "") },
];
const GROUPS = Array.from(new Set(GRID_ROWS.map((r) => r.group)));

export interface GridColumns { as_of: string; periods: string[]; period_start: string[]; period_end: string[]; }
export interface GridRowArticle { article: ArticleRef; series: SeriesOut[]; events: SupplyEventOut[]; suppliers: LinkRef[]; kpis?: { severity?: string | null; first_stockout_sim?: string | null }; }

/** index of the column containing a date */
export function columnOf(cols: GridColumns, date: string): number {
  return cols.period_start.findIndex((p, k) => p <= date && date <= (cols.period_end[k] ?? "9999-12-31"));
}

/**
 * The simulation grid: one column per day or ISO week, one block of rows per article.
 * Simulated receipts and adjustments are typed in the cell (quantity or expression); firm orders
 * and receipts open the entry form.  Row groups can be collapsed.  Used by the article page (one
 * article) and by the supply table (many articles).
 */
export function SimulationGrid({ cols, articles, cells, onEntry, showArticleRows }: {
  cols: GridColumns; articles: GridRowArticle[]; cells: CellOut[]; onEntry: (draft: EntryDraft) => void; showArticleRows?: boolean;
}) {
  const toast = useToast();
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [editing, setEditing] = useState<{ aid: string; key: string; i: number; value: string } | null>(null);
  const saveCell = useWrite((c: { article_id: string; date: string; kind: CellKind; expression: string }) => api.put<CellOut | null>("/api/entries/cells", c),
    (out) => toast.push(out ? `${out.kind === "sim_receipt" ? "Réception simulée" : "Ajustement"} ${fmtDate(out.date)} = ${fmtQty(out.qty)}` : "Cellule effacée", "success"));

  const asOfIdx = useMemo(() => columnOf(cols, cols.as_of), [cols]);
  const cellsBy = useMemo(() => {
    const m = new Map<string, CellOut[]>();
    cells.forEach((c) => {
      const i = columnOf(cols, c.date);
      if (i >= 0) m.set(`${c.article_id}|${c.kind}|${i}`, [...(m.get(`${c.article_id}|${c.kind}|${i}`) ?? []), c]);
    });
    return m;
  }, [cells, cols]);
  const eventsBy = useMemo(() => {
    const m = new Map<string, SupplyEventOut[]>();
    articles.forEach((a) => a.events.forEach((e) => {
      const i = columnOf(cols, e.date);
      if (i >= 0) m.set(`${a.article.article_id}|${i}`, [...(m.get(`${a.article.article_id}|${i}`) ?? []), e]);
    }));
    return m;
  }, [articles, cols]);

  const own = (aid: string, kind: CellKind, i: number) => cellsBy.get(`${aid}|${kind}|${i}`) ?? [];
  const startEdit = (aid: string, key: string, i: number, current: number) => {
    const mine = own(aid, CELL_ROWS[key], i);
    // one cell: edit its expression ; several days of a week: edit the total
    const value = mine.length === 1 ? (mine[0].expression || String(mine[0].qty)) : mine.length > 1 ? String(current) : "";
    setEditing({ aid, key, i, value });
  };
  const commitEdit = (next?: { aid: string; key: string; i: number }) => {
    if (!editing) return;
    const kind = CELL_ROWS[editing.key];
    const mine = own(editing.aid, kind, editing.i);
    const previous = mine.length === 1 ? (mine[0].expression || String(mine[0].qty)) : "";
    if (editing.value.trim() !== previous.trim()) {
      const date = mine.length === 1 ? mine[0].date : cols.period_start[editing.i];
      saveCell.mutate({ article_id: editing.aid, date, kind, expression: editing.value });
    }
    setEditing(null);
    if (next) startEdit(next.aid, next.key, next.i, 0);
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
                        <td>{isCollapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}{g}{isCollapsed && <span className="subtle" style={{ textTransform: "none", letterSpacing: 0 }}> ({rows.length} lignes)</span>}</td>
                        {cols.periods.map((p) => <td key={p} />)}
                      </tr>
                      {!isCollapsed && rows.map((r) => {
                        const vals = series[r.key] ?? [];
                        const cellKind = CELL_ROWS[r.key];
                        const editable = !!cellKind || r.key === "supply_firm" || r.key === "receipts";
                        const isEvent = r.key === "supply_firm" || r.key === "supply_forecast";
                        return (
                          <tr key={`${aid}-${r.key}`}>
                            <td>{r.label}</td>
                            {vals.map((v, i) => {
                              const past = i < asOfIdx;
                              const mine = cellKind ? own(aid, cellKind, i) : [];
                              const typed = mine.length > 0;
                              const isEditing = editing?.aid === aid && editing.key === r.key && editing.i === i;
                              const evs = eventsBy.get(`${aid}|${i}`);
                              const cls = [past ? "past" : "", i === asOfIdx ? "today" : "", v === 0 && !typed ? "zero" : "", r.cls ? r.cls(v, a.article) : "",
                                editable && !past ? "editable" : "", isEditing ? "editing" : "", typed ? "typed" : "", isWeekKey(cols.periods[i]) ? "wkcol" : "",
                                isEvent && evs?.some((e) => e.kind === "order") ? "event" : "",
                                evs?.some((e) => e.late && e.kind === "order") && r.key === "supply_firm" ? "late" : "",
                                !isWeekKey(cols.periods[i]) && isWeekend(cols.period_start[i]) ? "past" : ""].filter(Boolean).join(" ");
                              const title = cellKind
                                ? (mine.map((c) => `${fmtDate(c.date)} : ${c.expression || c.qty} = ${fmtQty(c.qty, a.article.unit)} (${c.source})${c.note ? ` – ${c.note}` : ""}`).join("\n")
                                  || (past ? "" : cellKind === "sim_receipt" ? "Cliquer pour saisir une réception simulée : quantité ou formule (ex. 2*600-50) ; 0 = rien n'arrive ; vide = commandes conservées" : "Cliquer pour saisir un ajustement (quantité signée ou formule)"))
                                : evs?.map((e) => `${KIND_LABELS[e.kind] ?? e.kind} ${e.ref} : ${fmtQty(e.qty, a.article.unit)} (${ORDER_TYPE_LABELS[e.order_type] ?? e.order_type}, ${e.source})`).join("\n");
                              const onClick = !editable || past || isEditing ? undefined
                                : cellKind ? () => startEdit(aid, r.key, i, v)
                                : () => onEntry({ kind: r.key === "receipts" ? "receipt" : "order", article_id: aid, date: cols.period_start[i], supplier_id: a.suppliers[0]?.supplier_id ?? null, order_type: "FIRM" });
                              return <td key={i} className={cls} title={title} onClick={onClick}>
                                {isEditing ? (
                                  <input className="cell-input" autoFocus value={editing.value} aria-label={`${r.label} ${periodLabel(cols.periods[i])}`}
                                    onChange={(e) => setEditing({ ...editing, value: e.target.value })}
                                    onBlur={() => commitEdit()}
                                    onKeyDown={(e) => {
                                      if (e.key === "Enter") { e.preventDefault(); commitEdit(); }
                                      else if (e.key === "Escape") { e.preventDefault(); setEditing(null); }
                                      else if (e.key === "Tab") { e.preventDefault(); const ni = i + (e.shiftKey ? -1 : 1); if (ni >= asOfIdx && ni < vals.length) commitEdit({ aid, key: r.key, i: ni }); else commitEdit(); }
                                    }} />
                                ) : r.key.startsWith("coverage") ? v : typed && v === 0 ? "0" : v === 0 ? "·" : fmtQty(v, a.article.unit)}
                              </td>;
                            })}
                          </tr>
                        );
                      })}
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

export function GridHelp() {
  return (
    <p className="small subtle" style={{ padding: "8px 12px" }}>
      Réceptions simulées et ajustements : cliquer sur la cellule et taper une quantité ou une formule (+ − × ÷, parenthèses), Entrée pour valider, Tab pour la période suivante, Échap pour annuler.
      Une réception simulée remplace les commandes attendues (F + P) du jour dans le stock simulé : 0 = rien n'arrive, vide = commandes conservées ; le jour de référence, MAX(R, S) est retenu.
      Le Complément CBN est recalculé automatiquement sur le stock simulé. En vue semaine la saisie se pose sur le premier jour de la semaine. Cliquer sur un titre de bloc pour le plier. Commandes fermes et réceptions ouvrent un formulaire ; un point bleu signale une commande ERP (rouge : en retard).
    </p>
  );
}
