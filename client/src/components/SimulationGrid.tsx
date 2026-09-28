import { Fragment, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ChevronDown, ChevronRight } from "lucide-react";
import { api } from "@/lib/api";
import { useWrite } from "@/lib/queries";
import { useToast } from "@/components/ui";
import type { EntryDraft } from "@/components/EntryDrawer";
import type { OrderActionsTarget } from "@/components/OrderActionsDrawer";
import { ACTION_KIND_LABELS, KIND_LABELS, ORDER_STATUS_LABELS, ORDER_TYPE_LABELS, fmtDate, fmtQty, isWeekKey, isWeekend, periodLabel } from "@/lib/format";
import type { ArticleRef, CellKind, CellOut, LinkRef, OrderStateOut, SeriesOut, SupplyEventOut } from "@/lib/types";

/** Rows of the grid whose cells are typed directly (quantity or arithmetic expression). */
export const CELL_ROWS: Record<string, CellKind> = { sim_receipts: "sim_receipt", adjustments: "adjustment" };

export const GRID_ROWS: { key: string; label: string; group: string; cls?: (v: number, a: ArticleRef) => string }[] = [
  { key: "demand", label: "Besoin (composants)", group: "Besoins" },
  { key: "demand_plan", label: "dont plan seul", group: "Besoins" },
  { key: "supply_firm", label: "Commandes fermes (F, ERP)", group: "Approvisionnements" },
  { key: "actions", label: "Actions sur commandes (F′ − F)", group: "Approvisionnements", cls: (v) => (v > 0 ? "action-pos" : v < 0 ? "action-neg" : "") },
  { key: "supply_forecast", label: "Commandes prévisionnelles ERP (P)", group: "Approvisionnements" },
  { key: "receipts", label: "Réceptions (R)", group: "Approvisionnements" },
  { key: "sim_receipts", label: "Réceptions simulées (S) : saisies + complément CBN", group: "Approvisionnements" },
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
const ORDER_ROWS = new Set(["supply_firm", "actions", "supply_forecast"]);

export interface GridColumns { as_of: string; periods: string[]; period_start: string[]; period_end: string[]; }
export interface GridRowArticle { article: ArticleRef; series: SeriesOut[]; events: SupplyEventOut[]; suppliers: LinkRef[]; orders: OrderStateOut[]; kpis?: { severity?: string | null; first_stockout_sim?: string | null }; }

/** index of the column containing a date */
export function columnOf(cols: GridColumns, date: string): number {
  return cols.period_start.findIndex((p, k) => p <= date && date <= (cols.period_end[k] ?? "9999-12-31"));
}

/** Orders shown in a column: ERP date in the column, or a simulated tranche in the column. */
export function ordersOfColumn(cols: GridColumns, orders: OrderStateOut[], i: number, row: "supply_firm" | "actions" | "supply_forecast"): OrderStateOut[] {
  return orders.filter((o) => (row === "supply_firm" ? o.order_type === "FIRM" : row === "supply_forecast" ? o.order_type !== "FIRM" : !!o.action_id)
    && (columnOf(cols, o.expected_date) === i || o.tranches.some((t) => columnOf(cols, t.date) === i)));
}

/**
 * The simulation grid: one column per day or ISO week, one block of rows per article.
 * Simulated receipts and adjustments are typed in the cell (quantity or expression); the order
 * rows open the order actions (delay, partial delivery, cancellation) or the new-order form.
 * Row groups can be collapsed.  Used by the article page and by the supply table.
 */
export function SimulationGrid({ cols, articles, cells, onEntry, onOrders, showArticleRows }: {
  cols: GridColumns; articles: GridRowArticle[]; cells: CellOut[]; onEntry: (draft: EntryDraft) => void;
  onOrders: (target: OrderActionsTarget) => void; showArticleRows?: boolean;
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
  const startEdit = (aid: string, key: string, i: number, current: number, cbn: number) => {
    const mine = own(aid, CELL_ROWS[key], i);
    // one cell: edit its expression ; several days of a week: edit the total ; a CBN value: accept it as the typed value
    const value = mine.length === 1 ? (mine[0].expression || String(mine[0].qty)) : mine.length > 1 ? String(current) : cbn ? String(cbn) : "";
    setEditing({ aid, key, i, value });
  };
  const commitEdit = (next?: { aid: string; key: string; i: number }) => {
    if (!editing) return;
    const kind = CELL_ROWS[editing.key];
    const mine = own(editing.aid, kind, editing.i);
    const previous = mine.length === 1 ? (mine[0].expression || String(mine[0].qty)) : "";
    if (editing.value.trim() !== previous.trim() && !(mine.length === 0 && editing.value.trim() === "")) {
      const date = mine.length === 1 ? mine[0].date : cols.period_start[editing.i];
      saveCell.mutate({ article_id: editing.aid, date, kind, expression: editing.value });
    }
    setEditing(null);
    if (next) startEdit(next.aid, next.key, next.i, 0, 0);
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
            const cbnRow = series["supply_proposed"] ?? [];
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
                        const raw = series[r.key] ?? [];
                        const cellKind = CELL_ROWS[r.key];
                        const isOrderRow = ORDER_ROWS.has(r.key);
                        const editable = !!cellKind || isOrderRow || r.key === "receipts";
                        return (
                          <tr key={`${aid}-${r.key}`}>
                            <td>{r.label}</td>
                            {raw.map((v0, i) => {
                              const cbn = r.key === "sim_receipts" ? (cbnRow[i] ?? 0) : 0;
                              const v = v0 + cbn;
                              const past = i < asOfIdx;
                              const mine = cellKind ? own(aid, cellKind, i) : [];
                              const typed = mine.length > 0;
                              const isEditing = editing?.aid === aid && editing.key === r.key && editing.i === i;
                              const evs = eventsBy.get(`${aid}|${i}`);
                              const colOrders = isOrderRow ? ordersOfColumn(cols, a.orders, i, r.key as "supply_firm" | "actions" | "supply_forecast") : [];
                              const acted = colOrders.some((o) => o.action_id);
                              const cls = [past ? "past" : "", i === asOfIdx ? "today" : "", v === 0 && !typed ? "zero" : "", r.cls ? r.cls(v, a.article) : "",
                                editable && !past ? "editable" : "", isEditing ? "editing" : "", typed ? "typed" : "", !typed && cbn ? "cbn sim" : "",
                                isWeekKey(cols.periods[i]) ? "wkcol" : "",
                                isOrderRow && colOrders.length ? "event" : "", isOrderRow && acted ? "orders" : "",
                                r.key === "supply_firm" && colOrders.some((o) => o.days_late > 0) ? "late" : "",
                                !isWeekKey(cols.periods[i]) && isWeekend(cols.period_start[i]) ? "past" : ""].filter(Boolean).join(" ");
                              const title = cellKind
                                ? [...mine.map((c) => `${fmtDate(c.date)} : ${c.expression || c.qty} = ${fmtQty(c.qty, a.article.unit)} (${c.source})${c.note ? ` – ${c.note}` : ""}`),
                                  ...(cbn ? [`Complément CBN ${fmtQty(cbn, a.article.unit)} (recalculé automatiquement ; saisir une valeur pour le fixer, 0 pour l'interdire ce jour)`] : []),
                                  ...(!mine.length && !cbn && !past ? [cellKind === "sim_receipt" ? "Cliquer pour saisir une réception simulée supplémentaire : quantité ou formule (ex. 2*600-50) ; 0 = aucun complément CBN ce jour ; vide = effacer" : "Cliquer pour saisir un ajustement (quantité signée ou formule)"] : [])].join("\n")
                                : isOrderRow
                                  ? colOrders.map((o) => `${o.order_id} · ${ORDER_TYPE_LABELS[o.order_type] ?? o.order_type} ${fmtQty(o.qty_open, a.article.unit)} le ${fmtDate(o.expected_date)} · ${ORDER_STATUS_LABELS[o.status]}${o.action_kind ? ` (${ACTION_KIND_LABELS[o.action_kind]}${o.tranches.length ? " " + o.tranches.map((t) => `${fmtQty(t.qty)} le ${fmtDate(t.date)}`).join(", ") : ""})` : ""}`).join("\n")
                                    || (past ? "" : "Cliquer pour saisir une commande ferme passée hors ERP")
                                  : evs?.map((e) => `${KIND_LABELS[e.kind] ?? e.kind} ${e.ref} : ${fmtQty(e.qty, a.article.unit)} (${ORDER_TYPE_LABELS[e.order_type] ?? e.order_type}, ${e.source})`).join("\n");
                              const onClick = !editable || past || isEditing ? undefined
                                : cellKind ? () => startEdit(aid, r.key, i, v0, cbn)
                                : isOrderRow && colOrders.length ? () => onOrders({ title: `${aid} · commandes ${periodLabel(cols.periods[i])}`, orders: colOrders })
                                : r.key === "supply_forecast" || r.key === "actions" ? undefined
                                : () => onEntry({ kind: r.key === "receipts" ? "receipt" : "order", article_id: aid, date: cols.period_start[i], supplier_id: a.suppliers[0]?.supplier_id ?? null, order_type: "FIRM" });
                              return <td key={i} className={cls} title={title} onClick={onClick}>
                                {isEditing ? (
                                  <input className="cell-input" autoFocus value={editing.value} aria-label={`${r.label} ${periodLabel(cols.periods[i])}`}
                                    onChange={(e) => setEditing({ ...editing, value: e.target.value })}
                                    onBlur={() => commitEdit()}
                                    onKeyDown={(e) => {
                                      if (e.key === "Enter") { e.preventDefault(); commitEdit(); }
                                      else if (e.key === "Escape") { e.preventDefault(); setEditing(null); }
                                      else if (e.key === "Tab") { e.preventDefault(); const ni = i + (e.shiftKey ? -1 : 1); if (ni >= asOfIdx && ni < raw.length) commitEdit({ aid, key: r.key, i: ni }); else commitEdit(); }
                                    }} />
                                ) : r.key.startsWith("coverage") ? v : typed && v === 0 ? "0" : v === 0 ? "·" : r.key === "actions" && v > 0 ? `+${fmtQty(v, a.article.unit)}` : fmtQty(v, a.article.unit)}
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
      Stock ferme = R + F (ERP tel quel) · prévisionnel = R + F + P · simulé = R + F′ + S, F′ étant les commandes fermes après vos actions (ligne « Actions » = F′ − F) ; une commande passée non reçue ne compte dans aucun stock tant qu'elle n'est pas qualifiée.
      Cliquer sur une cellule de commandes (point bleu) pour agir sur les commandes du jour : attendue le… (retard, tranches), annulée, clôturée ; ces actions ne touchent que le stock simulé.
      Réceptions simulées S : quantités supplémentaires saisies dans la cellule (quantité ou formule + − × ÷) ; les valeurs en italique sont le complément CBN, recalculé automatiquement ; une valeur saisie (0 compris) fixe le jour et y interdit le CBN. Entrée valide, Tab passe à la période suivante, Échap annule ; en vue semaine la saisie se pose sur le premier jour. Cliquer sur un titre de bloc pour le plier.
    </p>
  );
}
