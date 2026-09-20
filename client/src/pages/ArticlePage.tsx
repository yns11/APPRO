import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Calculator, Download, Plus, Trash2 } from "lucide-react";
import { useCells, useProjection, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { api } from "@/lib/api";
import { Badge, Button, Card, Empty, ErrorBox, Kpi, Segmented, SeverityBadge, Skeleton, SkeletonBlock, Tabs, useToast } from "@/components/ui";
import { StockChart, CoverageChart } from "@/components/charts/StockChart";
import { EntryDrawer, type EntryDraft } from "@/components/EntryDrawer";
import { AlertList } from "./CockpitPage";
import { KIND_LABELS, ORDER_TYPE_LABELS, fmtDate, fmtDateTime, fmtQty, isWeekend, periodLabel } from "@/lib/format";
import type { CbnReport, CellKind, CellOut, ProjectionResponse } from "@/lib/types";

/** Rows of the grid whose cells are typed directly (quantity or arithmetic expression). */
const CELL_ROWS: Record<string, CellKind> = { supply_planned: "sim_order", adjustments: "adjustment" };

const PIVOT_ROWS: { key: string; label: string; group?: string; cls?: (v: number, a: ProjectionResponse["article"]) => string }[] = [
  { key: "demand", label: "Besoin (composants)", group: "Besoins" },
  { key: "demand_plan", label: "dont plan seul", group: "Besoins" },
  { key: "supply_firm", label: "Commandes fermes", group: "Approvisionnements" },
  { key: "supply_forecast", label: "Commandes prévisionnelles (ERP)", group: "Approvisionnements" },
  { key: "supply_planned", label: "Commandes simulées", group: "Approvisionnements", cls: (v) => (v !== 0 ? "sim" : "") },
  { key: "receipts", label: "Réceptions (après snapshot)", group: "Approvisionnements" },
  { key: "adjustments", label: "Ajustements", group: "Approvisionnements" },
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

export default function ArticlePage() {
  const { articleId } = useParams();
  const { perimeter, set, engineParams } = usePerimeter();
  const [tab, setTab] = useState<"table" | "events" | "cells" | "alerts" | "master">("table");
  const [draft, setDraft] = useState<EntryDraft | null>(null);
  const [editing, setEditing] = useState<{ key: string; i: number; value: string } | null>(null);
  const q = useProjection(articleId);
  const cells = useCells(articleId ? { article_id: articleId } : undefined);
  const toast = useToast();
  const saveCell = useWrite((c: { date: string; kind: CellKind; expression: string }) => api.put<CellOut | null>("/api/entries/cells", { article_id: articleId, ...c }),
    (out) => toast.push(out ? `${out.kind === "sim_order" ? "Commande simulée" : "Ajustement"} ${fmtDate(out.date)} = ${fmtQty(out.qty)}` : "Cellule effacée", "success"));
  const delCell = useWrite((id: string) => api.del(`/api/entries/cells/${id}`), () => toast.push("Cellule supprimée"));
  const cbn = useWrite(() => api.post<CbnReport>("/api/cbn/run", { article_ids: [articleId], scenario_id: engineParams.scenario_id }),
    (r) => toast.push(r.proposals ? `Calcul CBN : ${r.proposals} commande(s) simulée(s) ajoutée(s) (${fmtQty(r.qty)}), ${r.urgent} urgente(s)` : "Calcul CBN : aucun besoin net, rien à ajouter", r.urgent ? "info" : "success"));

  const d = q.data;
  const asOfIdx = useMemo(() => d ? d.period_start.findIndex((p, i) => p <= d.as_of && (d.period_start[i + 1] ?? "9999-12-31") > d.as_of) : -1, [d]);
  const series = useMemo(() => Object.fromEntries((d?.series ?? []).map((s) => [s.key, s.values])), [d]);
  const eventsByPeriod = useMemo(() => {
    const m = new Map<number, ProjectionResponse["events"]>();
    if (!d) return m;
    d.events.forEach((e) => {
      const i = d.period_start.findIndex((p, k) => p <= e.date && (d.period_start[k + 1] ?? "9999-12-31") > e.date);
      if (i >= 0) m.set(i, [...(m.get(i) ?? []), e]);
    });
    return m;
  }, [d]);
  /** cells of the grid grouped by (kind, period index) */
  const cellsByPeriod = useMemo(() => {
    const m = new Map<string, CellOut[]>();
    if (!d || !cells.data) return m;
    cells.data.forEach((c) => {
      const i = d.period_start.findIndex((p, k) => p <= c.date && (d.period_start[k + 1] ?? "9999-12-31") > c.date);
      if (i >= 0) m.set(`${c.kind}:${i}`, [...(m.get(`${c.kind}:${i}`) ?? []), c]);
    });
    return m;
  }, [d, cells.data]);

  const startEdit = (key: string, i: number, current: number) => {
    if (!d) return;
    const kind = CELL_ROWS[key];
    const own = cellsByPeriod.get(`${kind}:${i}`) ?? [];
    // one cell: edit its expression ; several (typed + CBN, or several days of a week): edit the total
    const value = own.length === 1 ? (own[0].expression || String(own[0].qty)) : current ? String(current) : "";
    setEditing({ key, i, value });
  };
  const commitEdit = (next?: { key: string; i: number }) => {
    if (!editing || !d) return;
    const kind = CELL_ROWS[editing.key];
    const own = cellsByPeriod.get(`${kind}:${editing.i}`) ?? [];
    const previous = own.length === 1 ? (own[0].expression || String(own[0].qty)) : "";
    if (editing.value.trim() !== previous.trim()) {
      const date = own.length >= 1 && own.every((c) => c.date === own[0].date) ? own[0].date : d.period_start[editing.i];
      saveCell.mutate({ date, kind, expression: editing.value });
    }
    setEditing(next ? { ...next, value: "" } : null);
    if (next) startEdit(next.key, next.i, 0);
  };

  if (!articleId) return <Empty title="Article non précisé" />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const a = d?.article;
  const k = d?.kpis;
  const exportUrl = api.downloadUrl("/api/exports/simulation.xlsx", { article_ids: [articleId], scenario_id: engineParams.scenario_id, granularity: perimeter.granularity, horizon_days: engineParams.horizon_days });

  return (
    <div className="page">
      <div className="page-header">
        <div className="title">
          <div className="row"><Link to="/" className="btn ghost sm"><ArrowLeft />Cockpit</Link>{a && <SeverityBadge severity={k?.severity ?? null} />}</div>
          <h1 style={{ marginTop: 6 }}>{articleId} {a && <span className="muted" style={{ fontWeight: 400 }}>· {a.designation}</span>}</h1>
          <p>{a ? <>Unité {a.unit} · couverture cible {a.coverage_target_days} j · seuils rouge ≤ {a.alert_red_days} j, orange ≤ {a.alert_yellow_days} j, surstock ≥ {a.overstock_days} j · {d?.suppliers.map((s) => `${s.supplier_id} (${s.supplier_name}, délai ${s.lead_time_days} j, MOQ ${fmtQty(s.moq, a.unit)}, PLA ${fmtQty(s.pack_qty, a.unit)}${s.quota_pct < 100 ? `, quota ${s.quota_pct} %` : ""})`).join(" · ")}</> : <Skeleton w={400} />}</p>
        </div>
        <div className="actions">
          <Segmented size="sm" value={perimeter.granularity} onChange={(g) => set({ granularity: g })} options={[{ id: "day", label: "Jour" }, { id: "week", label: "Semaine" }]} />
          <Button variant="primary" disabled={cbn.isPending} onClick={() => cbn.mutate(undefined)} title="Recalcule les besoins nets et les écrit dans la ligne Commandes simulées (les cellules saisies à la main sont conservées)"><Calculator />{cbn.isPending ? "Calcul…" : "Calcul CBN"}</Button>
          <Button onClick={() => setDraft({ kind: "order", article_id: articleId, supplier_id: d?.suppliers[0]?.supplier_id ?? null })}><Plus />Saisir</Button>
          <a className="btn" href={exportUrl}><Download />Excel</a>
        </div>
      </div>

      <div className="grid kpis">
        <Kpi label="Stock à date" value={k ? fmtQty(k.stock_as_of_sim, a?.unit) : <Skeleton w={60} h={28} />} unit={a?.unit} meta={k ? `snapshot ${fmtDate(k.snapshot_date)} : ${fmtQty(k.stock_on_hand, a?.unit)}` : ""} />
        <Kpi label="Couverture simulée" value={k ? k.coverage_sim_days : <Skeleton w={40} h={28} />} unit="j" tone={k ? (k.coverage_sim_days <= (a?.alert_red_days ?? 3) ? "critical" : k.coverage_sim_days <= (a?.alert_yellow_days ?? 7) ? "warning" : "ok") : undefined} meta={k ? `ferme : ${k.coverage_firm_days} j · cible ${k.coverage_target_days} j` : ""} />
        <Kpi label="Rupture ferme" value={k ? (k.first_stockout_firm ? fmtDate(k.first_stockout_firm) : "aucune") : <Skeleton w={60} h={28} />} tone={k?.first_stockout_firm ? "critical" : "ok"} meta={k ? (k.first_stockout_firm ? `manque max ${fmtQty(k.max_shortage_firm, a?.unit)}` : `stock mini ${fmtQty(k.min_stock_firm, a?.unit)}`) : ""} />
        <Kpi label="Rupture prévisionnelle" value={k ? (k.first_stockout_forecast ? fmtDate(k.first_stockout_forecast) : "aucune") : <Skeleton w={60} h={28} />} tone={k?.first_stockout_forecast ? "warning" : "ok"} meta={k ? (k.first_stockout_forecast ? `manque max ${fmtQty(k.max_shortage_forecast, a?.unit)}` : `flux ERP fermes + prévisionnels`) : ""} />
        <Kpi label="Rupture simulée" value={k ? (k.first_stockout_sim ? fmtDate(k.first_stockout_sim) : "aucune") : <Skeleton w={60} h={28} />} tone={k?.first_stockout_sim ? "critical" : "ok"} meta={k ? (k.first_stockout_sim ? `manque max ${fmtQty(k.max_shortage_sim, a?.unit)}` : `stock mini ${fmtQty(k.min_stock_sim, a?.unit)}`) : ""} />
        <Kpi label="Besoin 30 j" value={k ? fmtQty(k.demand_next_30d, a?.unit) : <Skeleton w={60} h={28} />} meta={k ? `${fmtQty(k.avg_daily_demand_30d, a?.unit)} / jour · réel ${Math.round(100 * k.actual_share_30d)} % (30 j passés)` : ""} />
        <Kpi label="En-cours" value={k ? fmtQty(k.open_firm_qty, a?.unit) : <Skeleton w={60} h={28} />} meta={k ? `ferme · + ${fmtQty(k.open_forecast_qty, a?.unit)} prév. ERP · + ${fmtQty(k.open_planned_qty, a?.unit)} saisies${k.late_order_count ? ` · ${k.late_order_count} en retard` : ""}` : ""} tone={k && k.late_order_count ? "warning" : undefined} />
        <Kpi label="Commandes simulées" value={k ? fmtQty(k.open_planned_qty, a?.unit) : <Skeleton w={40} h={28} />} tone="brand" meta={cells.data ? `${cells.data.filter((c) => c.kind === "sim_order").length} cellule(s) · ${cells.data.filter((c) => c.source === "CBN").length} issue(s) du CBN` : ""} onClick={() => setTab("cells")} />
      </div>

      <Card title="Projection du stock" hint={`Stock ferme = stock + commandes fermes · prévisionnel = + prévisionnel ERP · simulé = + commandes simulées (saisies ou Calcul CBN) · les stocks sont physiques (jamais négatifs), le besoin non servi apparaît en « manque » (${k?.shortage_policy === "lost" ? "perdu" : "reporté"}) · barres : besoin (bas), approvisionnements et ajustements (haut)`}>
        {q.isLoading || !d ? <Skeleton h={300} /> : <><StockChart data={d} /><div style={{ marginTop: 8 }}><CoverageChart data={d} /></div></>}
      </Card>

      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "table", label: "Tableau de simulation" },
        { id: "events", label: "Commandes & mouvements", count: d?.events.length },
        { id: "cells", label: "Commandes simulées & ajustements saisis", count: cells.data?.length },
        { id: "alerts", label: "Alertes", count: d?.alerts.length },
        { id: "master", label: "Données de base" },
      ]} />

      {tab === "table" && (q.isLoading || !d ? <SkeletonBlock rows={10} /> : (
        <Card flush tight>
          <div className="pivot">
            <table>
              <thead>
                <tr>
                  <th>Variable</th>
                  {d.periods.map((p, i) => <th key={p} className={`${i === asOfIdx ? "today" : ""} ${d.granularity === "week" ? "wk" : ""}`} title={d.period_start[i]}>{periodLabel(p, d.granularity)}</th>)}
                </tr>
              </thead>
              <tbody>
                {PIVOT_ROWS.map((r, ri) => {
                  const vals = series[r.key] ?? [];
                  const head = ri === 0 || PIVOT_ROWS[ri - 1].group !== r.group;
                  const cellKind = CELL_ROWS[r.key];
                  const editable = !!cellKind || r.key === "supply_firm" || r.key === "receipts";
                  const isEvent = r.key === "supply_firm" || r.key === "supply_forecast";
                  return (
                    <>
                      {head && <tr className="group-head" key={`g-${r.group}`}><td>{r.group}</td>{d.periods.map((p) => <td key={p} />)}</tr>}
                      <tr key={r.key}>
                        <td>{r.label}</td>
                        {vals.map((v, i) => {
                          const past = i < asOfIdx;
                          const own = cellKind ? cellsByPeriod.get(`${cellKind}:${i}`) : undefined;
                          const isEditing = editing?.key === r.key && editing.i === i;
                          const cls = [past ? "past" : "", i === asOfIdx ? "today" : "", v === 0 ? "zero" : "", r.cls ? r.cls(v, d.article) : "",
                            editable && !past ? "editable" : "", isEditing ? "editing" : "", own?.some((c) => c.source === "CBN") ? "cbn" : "",
                            isEvent && eventsByPeriod.get(i)?.some((e) => e.kind === "order") ? "event" : "",
                            eventsByPeriod.get(i)?.some((e) => e.late && e.kind === "order") && r.key === "supply_firm" ? "late" : "",
                            d.granularity === "day" && isWeekend(d.period_start[i]) ? "past" : ""].filter(Boolean).join(" ");
                          const title = cellKind
                            ? (own?.map((c) => `${fmtDate(c.date)} : ${c.expression || c.qty} = ${fmtQty(c.qty, d.article.unit)} (${c.source})${c.note ? ` – ${c.note}` : ""}`).join("\n") || (past ? "" : "Cliquer pour saisir une quantité ou une formule (ex. 2*600-50)"))
                            : eventsByPeriod.get(i)?.map((e) => `${KIND_LABELS[e.kind] ?? e.kind} ${e.ref} : ${fmtQty(e.qty, d.article.unit)} (${ORDER_TYPE_LABELS[e.order_type] ?? e.order_type}, ${e.source})`).join("\n");
                          const onClick = !editable || past || isEditing ? undefined
                            : cellKind ? () => startEdit(r.key, i, v)
                            : () => setDraft({ kind: r.key === "receipts" ? "receipt" : "order", article_id: articleId, date: d.period_start[i], supplier_id: d.suppliers[0]?.supplier_id ?? null, order_type: "FIRM" });
                          return <td key={i} className={cls} title={title} onClick={onClick}>
                            {isEditing ? (
                              <input className="cell-input" autoFocus value={editing.value} aria-label={`${r.label} ${periodLabel(d.periods[i], d.granularity)}`}
                                onChange={(e) => setEditing({ ...editing, value: e.target.value })}
                                onBlur={() => commitEdit()}
                                onKeyDown={(e) => {
                                  if (e.key === "Enter") { e.preventDefault(); commitEdit(); }
                                  else if (e.key === "Escape") { e.preventDefault(); setEditing(null); }
                                  else if (e.key === "Tab") { e.preventDefault(); const ni = i + (e.shiftKey ? -1 : 1); if (ni >= asOfIdx && ni < vals.length) commitEdit({ key: r.key, i: ni }); else commitEdit(); }
                                }} />
                            ) : r.key.startsWith("coverage") ? v : v === 0 ? "·" : fmtQty(v, d.article.unit)}
                          </td>;
                        })}
                      </tr>
                    </>
                  );
                })}
              </tbody>
            </table>
          </div>
          <p className="small subtle" style={{ padding: "8px 12px" }}>Commandes simulées et ajustements : cliquer sur la cellule et taper une quantité (négative possible) ou une formule (+ − × ÷, parenthèses), Entrée pour valider, Tab pour passer à la période suivante, Échap pour annuler ; vide efface. Les cellules hachurées viennent du Calcul CBN et restent modifiables. En vue semaine la saisie se pose sur le premier jour de la semaine. Commandes fermes et réceptions ouvrent un formulaire. Un point bleu signale une commande ERP (rouge : en retard).</p>
        </Card>
      ))}

      {tab === "events" && (q.isLoading || !d ? <SkeletonBlock /> : d.events.length === 0 ? <Empty title="Aucun mouvement sur l'horizon" /> : (
        <Card flush>
          <table className="tbl">
            <thead><tr><th>Date</th><th>Type</th><th>Référence</th><th>Fournisseur</th><th>Nature</th><th>Origine</th><th className="num">Quantité</th><th></th></tr></thead>
            <tbody>
              {d.events.map((e, i) => (
                <tr key={i}>
                  <td>{fmtDate(e.date)}</td>
                  <td>{KIND_LABELS[e.kind] ?? e.kind}</td>
                  <td className="mono">{e.ref}</td>
                  <td className="subtle">{e.supplier_id ?? "–"}</td>
                  <td><Badge tone={e.order_type === "FIRM" ? "brand" : e.order_type === "PROPOSAL" ? "warning" : e.order_type === "RECEIPT" ? "ok" : "neutral"}>{ORDER_TYPE_LABELS[e.order_type] ?? e.order_type}</Badge></td>
                  <td className="subtle">{e.source}</td>
                  <td className="num">{fmtQty(e.qty, d.article.unit)}</td>
                  <td>{e.late && <Badge tone="critical">retard</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ))}

      {tab === "cells" && (cells.isLoading ? <SkeletonBlock /> : !cells.data?.length ? <Empty title="Aucune commande simulée ni ajustement saisi" hint="Saisissez directement dans le tableau ou lancez le Calcul CBN." /> : (
        <Card flush>
          <table className="tbl">
            <thead><tr><th>Date</th><th>Nature</th><th className="num">Quantité</th><th>Saisie</th><th>Origine</th><th>Note</th><th>Modifié</th><th></th></tr></thead>
            <tbody>
              {cells.data.map((c) => (
                <tr key={c.id}>
                  <td>{fmtDate(c.date)}</td>
                  <td><Badge tone={c.kind === "sim_order" ? "warning" : "neutral"}>{c.kind === "sim_order" ? "Commande simulée" : "Ajustement"}</Badge></td>
                  <td className={`num ${c.qty < 0 ? "delta down" : ""}`}><b>{fmtQty(c.qty, a?.unit)}</b></td>
                  <td className="mono small">{c.expression}</td>
                  <td><Badge tone={c.source === "CBN" ? "brand" : "outline"}>{c.source}</Badge></td>
                  <td className="small subtle" style={{ whiteSpace: "normal", maxWidth: 420 }}>{c.note}</td>
                  <td className="subtle small">{c.updated_by}<br />{fmtDateTime(c.updated_at)}</td>
                  <td><Button size="sm" variant="ghost" title="Supprimer" onClick={() => delCell.mutate(c.id)}><Trash2 /></Button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ))}

      {tab === "alerts" && <Card>{q.isLoading || !d ? <SkeletonBlock /> : <AlertList alerts={d.alerts} asOf={d.as_of} />}</Card>}

      {tab === "master" && d && (
        <div className="grid cols-2">
          <Card title="Fournisseurs & règles d'approvisionnement" hint="Paramètres modifiables dans Paramètres & règles">
            <table className="tbl compact">
              <thead><tr><th>Fournisseur</th><th className="num">MOQ</th><th className="num">PLA</th><th className="num">Délai (j ouvrés)</th><th className="num">Quota</th><th className="num">Priorité</th></tr></thead>
              <tbody>{d.suppliers.map((s) => <tr key={s.supplier_id}><td>{s.supplier_id}<span className="sub">{s.supplier_name}</span></td><td className="num">{fmtQty(s.moq, d.article.unit)}</td><td className="num">{fmtQty(s.pack_qty, d.article.unit)}</td><td className="num">{s.lead_time_days}</td><td className="num">{s.quota_pct} %</td><td className="num">{s.priority}</td></tr>)}</tbody>
            </table>
          </Card>
          <Card title="Programmes consommateurs (nomenclature 1 niveau)" hint="Besoin = production effective × quantité par unité">
            <table className="tbl compact">
              <thead><tr><th>Programme</th><th className="num">Qté / unité</th><th className="num">Production 30 j</th><th className="num">Besoin induit 30 j</th></tr></thead>
              <tbody>{d.programs.map((p) => <tr key={p.program_id}><td>{p.name}<span className="sub">{p.program_id}</span></td><td className="num">{p.qty_per} {p.unit}</td><td className="num">{fmtQty(p.production_next_30d)}</td><td className="num">{fmtQty(p.production_next_30d * p.qty_per, d.article.unit)}</td></tr>)}</tbody>
            </table>
          </Card>
          <Card title="Politique de stock" className="cols-2">
            <div className="grid cols-4">
              <div><h4>Couverture cible</h4><div>{d.article.coverage_target_days} jours</div></div>
              <div><h4>Seuils d'alerte</h4><div>rouge ≤ {d.article.alert_red_days} j · orange ≤ {d.article.alert_yellow_days} j · surstock ≥ {d.article.overstock_days} j</div></div>
              <div><h4>Stock de sécurité</h4><div>{fmtQty(d.article.safety_stock_qty, d.article.unit)} {d.article.unit}</div></div>
              <div><h4>Lotissement</h4><div>{d.article.lot_policy} · cycle {d.article.order_cycle_days} j</div></div>
            </div>
          </Card>
          {d.diagnostics.length > 0 && <Card title="Diagnostics du calcul" className="cols-2"><ul className="small subtle">{d.diagnostics.map((x, i) => <li key={i}>{x}</li>)}</ul></Card>}
        </div>
      )}

      <EntryDrawer draft={draft} onClose={() => setDraft(null)} articles={a ? [{ article_id: a.article_id, designation: a.designation, unit: a.unit }] : []} />
    </div>
  );
}
