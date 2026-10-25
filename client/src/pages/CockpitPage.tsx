import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { AlertOctagon, ArrowDownToLine, CheckCircle2, Database, Download, Scale, ShoppingCart } from "lucide-react";
import { useCockpit, useSupplierNames } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { api } from "@/lib/api";
import { ALERT_LABELS, SCOPE_LABELS, STATUS_LABELS, STATUS_TONE, fmtDate, fmtEur, fmtInt, fmtPct, fmtQty, daysFrom } from "@/lib/format";
import { Badge, Card, Empty, ErrorBox, Kpi, Skeleton, SkeletonBlock, SupplierLink, Tabs } from "@/components/ui";
import { useRemembered } from "@/lib/pageState";
import { DataTable, type Column } from "@/components/DataTable";
import { StockValueChart } from "@/components/charts/StockValueChart";
import { FlowsPanel } from "@/components/FlowsPanel";
import { CalibrateDialog, fmtSigned, fmtSignedPct } from "@/components/CalibrateDialog";
import type { ArticleSummary, Severity } from "@/lib/types";

type Filter = "all" | "critical" | "to_order" | "overstock" | "data" | "drift" | "ok";
type CockpitTab = "stocks" | "flux";
const FILTERS: Filter[] = ["all", "critical", "to_order", "overstock", "data", "drift", "ok"];

export default function CockpitPage() {
  const { perimeter, engineParams, config, rights } = usePerimeter();
  const q = useCockpit();
  const [calibrate, setCalibrate] = useState(false);
  const nav = useNavigate();
  const [params, setParams] = useSearchParams();
  const tab: CockpitTab = params.get("tab") === "flux" ? "flux" : "stocks";
  const setTab = (t: CockpitTab) => setParams(t === "stocks" ? {} : { tab: t }, { replace: true });
  const [savedFilter, setFilter] = useRemembered<Filter>("cockpit.filter2", "all");
  const filter: Filter = FILTERS.includes(savedFilter) ? savedFilter : "all";
  const toggle = (f: Filter) => setFilter(filter === f ? "all" : f);
  const asOf = q.data?.as_of;
  const names = useSupplierNames();
  const erpOn = !!q.data?.erp_stock_enabled;
  const erpDate = q.data?.erp_stock_date ?? null;

  const rows = useMemo(() => (q.data?.articles ?? []).filter((a) => {
    switch (filter) {
      case "critical": return a.kpis.status === "critical";
      case "to_order": return a.kpis.status === "to_order";
      case "overstock": return a.kpis.overstock;
      case "data": return a.kpis.data_issues.length > 0;
      case "drift": return a.kpis.erp_stock_drift;
      case "ok": return a.kpis.status === "ok";
      default: return true;
    }
  }).sort((a, b) => (filter === "drift" ? (a.kpis.erp_stock_gap ?? 0) - (b.kpis.erp_stock_gap ?? 0) : 0)), [q.data, filter]);

  const cols = useMemo<Column<ArticleSummary>[]>(() => [
    { key: "article", label: "Article", get: (a) => `${a.article_id} ${a.designation}`, render: (a) => <><b>{a.article_id}</b><span className="sub" title={a.designation}>{a.designation.length > 30 ? `${a.designation.slice(0, 30)}…` : a.designation}</span></> },
    { key: "suppliers", label: "Fournisseurs", get: (a) => a.suppliers.map((sid) => `${sid} ${names.get(sid) ?? ""}`).join(" / "), title: "Liens actifs, code et nom (le filtre porte sur les deux) ; cliquer pour ouvrir la fiche fournisseur", render: (a) => <span className="subtle">{a.suppliers.map((sid) => <span key={sid} style={{ display: "block" }}><SupplierLink id={sid} name={names.get(sid)} inline className="subtle" /></span>)}</span> },
    { key: "planner", label: "Approvisionneur", get: (a) => a.planner, filter: "select" },
    { key: "status", label: "Statut", get: (a) => STATUS_LABELS[a.kpis.status], filter: "select", exportGet: (a) => `${STATUS_LABELS[a.kpis.status]}${a.kpis.data_issues.length ? " · données" : ""}`, render: (a) => <StatusCell a={a} /> },
    { key: "stock", label: "Stock à date", get: (a) => a.kpis.stock_at_date, num: true, title: "Stock à la fin de la veille ; en dessous, le stock cible du jour", render: (a) => <span className="cell-2l"><span>{fmtQty(a.kpis.stock_at_date, a.unit)} <span className="subtle">{a.unit}</span></span><span className="sub">cible {fmtQty(a.kpis.target_stock, a.unit)}</span></span> },
    { key: "erp_stock", label: "Stock ERP (veille)", hidden: !erpOn, get: (a) => a.kpis.erp_stock_qty ?? "", num: true, title: "Stock physique de l'ERP à la fin du dernier snapshot (entrepôts configurés) ; en dessous, la date et les entrepôts", render: (a) => a.kpis.erp_stock_qty === null ? <span className="subtle">–</span> : <span className="cell-2l"><span>{fmtQty(a.kpis.erp_stock_qty, a.unit)}</span><span className="sub">{fmtDate(a.kpis.erp_stock_date ?? "")}{a.kpis.erp_stock_stale ? " · ancien" : ""}{a.kpis.erp_stock_warehouses ? ` · ${Object.keys(a.kpis.erp_stock_warehouses).join(" + ")}` : ""}</span></span> },
    { key: "erp_gap", label: "Écart ERP − projeté", hidden: !erpOn, get: (a) => a.kpis.erp_stock_gap ?? "", num: true, title: "Stock ERP du snapshot − stock projeté du même jour ; négatif = nous surestimons (risque de rupture), positif = nous sous-estimons ; « dérive » = au-delà du seuil avec le même signe sur les derniers snapshots", render: (a) => a.kpis.erp_stock_gap === null ? <span className="subtle">–</span> : <GapCell a={a} /> },
    { key: "cov_date", label: "Couverture à date", get: (a) => a.kpis.coverage_at_date_days, num: true, title: "Jours ouvrés de besoin couverts par le stock de la veille seul, sans aucune livraison ; en dessous, la couverture cible", render: (a) => <span className="cell-2l"><CoverageAtDate a={a} /><span className="sub">cible {a.kpis.coverage_target_days} j</span></span> },
    { key: "next_firm", label: "Commande en cours", get: (a) => a.kpis.next_firm_date ?? "", title: "Prochaine commande ferme attendue (ERP) : date, fournisseur, restant à livrer", render: (a) => a.kpis.next_firm_date ? <span className="cell-2l"><span>{fmtDate(a.kpis.next_firm_date)} <span className="subtle">· J+{daysFrom(a.kpis.next_firm_date, asOf ?? a.kpis.init_date)}</span></span><span className="sub"><SupplierLink id={a.kpis.next_firm_supplier_id ?? ""} name={names.get(a.kpis.next_firm_supplier_id ?? "")} inline className="subtle" /> · {fmtQty(a.kpis.next_firm_qty ?? 0, a.unit)}</span></span> : <span className="subtle">–</span>, exportGet: (a) => a.kpis.next_firm_date ? `${a.kpis.next_firm_date} ${a.kpis.next_firm_supplier_id ?? ""} ${a.kpis.next_firm_qty ?? ""}` : "" },
    { key: "order_due", label: "À commander au plus tard le", get: (a) => a.kpis.order_due_date ?? "", title: "Première proposition CBN : date limite de commande (livraison − délai + marge), fournisseur retenu, quantité ; ! = date limite dépassée, ◇ = livraison hors jour standard", render: (a) => a.kpis.order_due_date ? <span className="cell-2l"><span style={a.kpis.order_due_late ? { color: "var(--critical-fg)", fontWeight: 600 } : undefined}>{fmtDate(a.kpis.order_due_date)} <span className={a.kpis.order_due_late ? "" : "subtle"}>· {a.kpis.order_due_late ? (a.kpis.order_due_date < (asOf ?? a.kpis.init_date) ? "dépassée !" : "aujourd'hui !") : `J+${daysFrom(a.kpis.order_due_date, asOf ?? a.kpis.init_date)}`}</span></span><span className="sub"><SupplierLink id={a.kpis.order_due_supplier_id ?? ""} name={names.get(a.kpis.order_due_supplier_id ?? "")} inline className="subtle" /> · {fmtQty(a.kpis.order_due_qty ?? 0, a.unit)} · livraison {fmtDate(a.kpis.order_due_delivery_date ?? "")}{a.kpis.order_due_off_standard ? <span title="Hors jour standard" style={{ color: "var(--brand-fg)" }}> ◇</span> : null}</span></span> : <span className="subtle">–</span>, exportGet: (a) => a.kpis.order_due_date ? `${a.kpis.order_due_date} ${a.kpis.order_due_supplier_id ?? ""} ${a.kpis.order_due_qty ?? ""}` : "" },
    { key: "cov", label: "Couverture projetée", hidden: true, get: (a) => a.kpis.coverage_plan_days, num: true, title: "Jours ouvrés couverts par le stock projeté (quantités à retenir et CBN compris)", render: (a) => <span>{a.kpis.coverage_plan_days} j</span> },
    { key: "stockout_plan", label: "Rupture projetée", hidden: true, get: (a) => a.kpis.first_stockout_plan ?? "", render: (a) => a.kpis.first_stockout_plan ? <Badge tone="warning">{fmtDate(a.kpis.first_stockout_plan)} · J+{daysFrom(a.kpis.first_stockout_plan, asOf ?? a.kpis.init_date)}</Badge> : <span className="subtle">–</span> },
    { key: "stockout", label: "Rupture Ferme", hidden: true, get: (a) => a.kpis.first_stockout_erp ?? "", title: "Projeté Ferme : premier jour non servi par le stock et les ordres fermes seuls", render: (a) => a.kpis.first_stockout_erp ? <span>{fmtDate(a.kpis.first_stockout_erp)} <span className="subtle">· J+{daysFrom(a.kpis.first_stockout_erp, asOf ?? a.kpis.init_date)}</span></span> : <span className="subtle">–</span> },
    { key: "open", label: "En-cours ERP", hidden: true, get: (a) => a.kpis.open_firm_qty, num: true, render: (a) => <>{fmtQty(a.kpis.open_firm_qty, a.unit)}{a.kpis.open_forecast_qty > 0 && <span className="sub">+ {fmtQty(a.kpis.open_forecast_qty, a.unit)} prév.</span>}</> },
    { key: "plan", label: "Quantité à retenir", hidden: true, get: (a) => a.kpis.plan_qty, num: true, render: (a) => <>{fmtQty(a.kpis.plan_qty, a.unit)}{a.kpis.plan_cell_count > 0 && <span className="sub">{a.kpis.plan_cell_count} cellule(s)</span>}</> },
    { key: "cbn", label: "CBN", hidden: true, get: (a) => a.kpis.proposal_count, num: true, render: (a) => a.kpis.proposal_count > 0 ? <>{a.kpis.proposal_count} <span className="subtle">({fmtQty(a.kpis.proposed_qty, a.unit)})</span>{a.kpis.capped_proposal_count > 0 && <span className="sub" style={{ color: "var(--warning-fg)" }}>{a.kpis.capped_proposal_count} plafonnée(s)</span>}</> : <span className="subtle">–</span> },
    { key: "value", label: "Valeur", hidden: true, get: (a) => a.kpis.stock_value, num: true, export: "number", render: (a) => a.kpis.stock_value === null ? <span className="subtle">–</span> : <span>{fmtEur(a.kpis.stock_value)}</span> },
    { key: "alerts", label: "Alertes", hidden: true, get: (a) => a.alert_types.map((t) => ALERT_LABELS[t] ?? t).join(" "), render: (a) => <div className="chip-list">{a.alert_types.map((t) => <span key={t} className="chip">{ALERT_LABELS[t] ?? t}</span>)}</div> },
    { key: "backlog", label: "En retard", title: "Restant ERP des commandes fermes dont la date de livraison est passée", get: (a) => a.kpis.backlog_qty, num: true, render: (a) => a.kpis.backlog_qty > 0 ? <span style={{ color: "var(--warning-fg)" }}>{fmtQty(a.kpis.backlog_qty, a.unit)}</span> : <span className="subtle">–</span> },
  ], [asOf, names, erpOn]);

  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const k = q.data?.kpis;
  const exportUrl = api.downloadUrl("/api/exports/simulation.xlsx", { planner: engineParams.planner, granularity: perimeter.granularity === "week" ? "week" : "day" });
  const gap = k && k.stock_value !== null && k.target_value ? (k.stock_value - k.target_value) / k.target_value : null;

  return (
    <div className="page">
      <div className="page-header">
        <div className="title">
          <h1>Cockpit du jour</h1>
          <p>{asOf ? <>Aujourd'hui <b>{fmtDate(asOf)}</b> · stock initialisé le {fmtDate(q.data!.init_date)} · {q.data?.kpis.articles} articles{perimeter.planner ? ` · ${perimeter.planner}` : ""}{erpOn && erpDate ? <> · <span title="Snapshot de stock physique de l'ERP, comparé au stock projeté du même jour">stock ERP du {fmtDate(erpDate)}</span></> : null}</> : <Skeleton w={280} />}</p>
        </div>
        <div className="actions">
          <a className="btn" href={api.downloadUrl("/api/exports/alerts.xlsx", { planner: engineParams.planner })}><Download />Alertes (xlsx)</a>
          <a className="btn primary" href={exportUrl}><Download />Simulation (xlsx)</a>
        </div>
      </div>
      <Tabs<CockpitTab> tabs={[{ id: "stocks", label: "Couvertures et stocks" }, { id: "flux", label: "Flux d'approvisionnement" }]} value={tab} onChange={setTab} />
      {tab === "flux" && <FlowsPanel />}
      {tab === "stocks" && <>
      {config?.reference_empty && <div className="note">Le référentiel est vide : chargez d'abord les articles, fournisseurs, règles article ↔ fournisseur, programmes, nomenclatures et le stock de référence dans la page <Link to="/referentiel">Référentiel</Link> (modèles Excel à télécharger).</div>}

      <div className="grid kpis sticky-kpis">
        <Kpi label="Critiques" icon={<AlertOctagon size={14} />} tone="critical" value={k ? fmtInt(k.critical) : <Skeleton w={40} h={28} />} meta="couverture rouge ou date limite dépassée" onClick={() => toggle("critical")} active={filter === "critical"} />
        <Kpi label="À commander" icon={<ShoppingCart size={14} />} tone="warning" value={k ? fmtInt(k.to_order) : <Skeleton w={40} h={28} />} meta="date limite de commande sous 5 jours ouvrés" onClick={() => toggle("to_order")} active={filter === "to_order"} />
        <Kpi label="Surstock" icon={<ArrowDownToLine size={14} />} tone="info" value={k ? fmtInt(k.overstock) : <Skeleton w={40} h={28} />} meta="au-dessus du seuil" onClick={() => toggle("overstock")} active={filter === "overstock"} />
        <Kpi label="Données" icon={<Database size={14} />} tone="brand" value={k ? fmtInt(k.data_issues) : <Skeleton w={40} h={28} />} meta={erpOn ? "stock, lien, commande ou dérive ERP à corriger" : "stock, lien ou commande à corriger"} onClick={() => toggle("data")} active={filter === "data"} />
        {erpOn && <Kpi label="Écart de stock ERP" icon={<Scale size={14} />} tone={k && k.stock_drift > 0 ? "warning" : "ok"} value={k ? fmtInt(k.stock_drift) : <Skeleton w={40} h={28} />} meta={k ? `${k.stock_drift_low} en moins (−), ${k.stock_drift_high} en plus (+)${erpDate ? ` · stock ERP du ${fmtDate(erpDate)}` : " · aucun snapshot"}` : ""} onClick={() => toggle("drift")} active={filter === "drift"} />}
        <Kpi label="OK" icon={<CheckCircle2 size={14} />} tone="ok" value={k ? fmtInt(k.ok) : <Skeleton w={40} h={28} />} meta="rien à faire aujourd'hui" onClick={() => toggle("ok")} active={filter === "ok"} />
      </div>
      {k && (
        <div className="cockpit-context">
          <span>Couverture médiane <b>{k.median_coverage_days ?? "–"} j</b></span>
          <span>Valeur du stock <b>{fmtEur(k.stock_value, true)}</b>{gap !== null && <span style={gap > 0 ? { color: "var(--critical-fg)" } : undefined}> ({gap > 0 ? "+" : "−"}{fmtPct(Math.abs(gap))} de la cible {fmtEur(k.target_value, true)})</span>}{k.unpriced_articles > 0 && <> · {k.unpriced_articles} sans prix</>}</span>
          <span>En-cours ERP <b>{fmtQty(k.open_firm_qty)}</b></span>
          <span>En retard <b>{k.backlog_articles}</b> article(s)</span>
        </div>
      )}

      <Card flush title="Portefeuille" hint="cliquer sur une tuile ou filtrer chaque colonne ; une ligne ouvre la fiche article ; « Colonnes » affiche les colonnes secondaires" collapsible storageKey="cockpit.portfolio.card"
        actions={<>
          {erpOn && filter === "drift" && rights.canManageParams && rows.length > 0 && <button className="btn sm" onClick={() => setCalibrate(true)} title="Un ajustement signé par article filtré, daté du snapshot ; une seule action annulable"><Scale />Caler les {rows.length} article(s) filtrés sur l'ERP…</button>}
          <select className="select sm" value={filter} onChange={(e) => setFilter(e.target.value as Filter)} aria-label="Filtre du portefeuille">
          <option value="all">Tous</option><option value="critical">Critiques</option><option value="to_order">À commander</option>
          <option value="overstock">Surstock</option><option value="data">Données à corriger</option>{erpOn && <option value="drift">Écart de stock ERP</option>}<option value="ok">OK</option>
        </select></>}>
        {q.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div> : (
          <DataTable rows={rows} columns={cols} rowKey={(a) => a.article_id} onRowClick={(a) => nav(`/articles/${encodeURIComponent(a.article_id)}`)} emptyTitle="Aucun article" maxHeight="calc(100vh - 260px)" storageKey="cockpit.portfolio2" exportTitle="Portefeuille" />
        )}
      </Card>

      <div className="grid cols-3">
        <Card title="Projection de la valeur du stock" hint="Σ prix × stock projeté en fin de semaine, articles valorisés ; pointillé : valeur du stock cible" className="span-2" collapsible storageKey="cockpit.value">
          {q.isLoading ? <Skeleton h={220} /> : q.data?.weekly_stock_value.length ? <StockValueChart data={q.data.weekly_stock_value} /> : <Empty title="Aucun article valorisé" hint="Les prix viennent de la table des prix synchronisée depuis l'ERP (std_cost_price)." />}
        </Card>
        <Card title="Alertes prioritaires" hint={q.data ? `${q.data.alerts.filter((a) => a.severity !== "info").length} alertes` : ""} actions={<button className="btn sm" onClick={() => setTab("flux")}>À commander</button>} collapsible storageKey="cockpit.alerts">
          {q.isLoading ? <SkeletonBlock /> : <AlertList alerts={(q.data?.alerts ?? []).filter((a) => a.severity !== "info").slice(0, 8)} asOf={asOf} />}
        </Card>
      </div>
      {q.data?.diagnostics?.length ? <p className="small subtle">{q.data.diagnostics.slice(-1)[0]}</p> : null}
      {calibrate && <CalibrateDialog title={`Caler ${rows.length} article(s) sur l'ERP`} onClose={() => setCalibrate(false)}
        rows={rows.filter((a) => a.kpis.erp_stock_date).map((a) => ({ article_id: a.article_id, designation: a.designation, unit: a.unit, erp_qty: a.kpis.erp_stock_qty ?? 0, erp_date: a.kpis.erp_stock_date ?? "", projected: (a.kpis.erp_stock_qty ?? 0) - (a.kpis.erp_stock_gap ?? 0), gap: a.kpis.erp_stock_gap ?? 0, locked: !rights.canEditPlanner(a.planner) }))} />}
      </>}
    </div>
  );
}

/** Signed gap ERP − projected, in quantity and in % of the target, with the drift mention (orange = ERP below, blue = ERP above). */
function GapCell({ a }: { a: ArticleSummary }) {
  const gap = a.kpis.erp_stock_gap ?? 0, pct = a.kpis.erp_stock_gap_pct;
  const color = !a.kpis.erp_stock_beyond ? "var(--fg-muted)" : gap < 0 ? "var(--warning-fg)" : "var(--info-fg)";
  return <span className="cell-2l"><span style={{ color, fontWeight: a.kpis.erp_stock_beyond ? 600 : 400 }}>{fmtSigned(gap, a.unit)}{pct !== null ? <span className="subtle"> · {fmtSignedPct(pct)}</span> : null}</span>
    <span className="sub" style={a.kpis.erp_stock_drift ? { color } : undefined}>{a.kpis.erp_stock_drift ? `dérive · ${a.kpis.erp_stock_drift_runs} snapshots de suite` : a.kpis.erp_stock_beyond ? "au-delà du seuil" : "sous le seuil"}</span></span>;
}

/** Status badge plus a blue dot when the article carries a data issue (never part of the status). */
function StatusCell({ a }: { a: ArticleSummary }) {
  const issues = a.kpis.data_issues.map((t) => ALERT_LABELS[t] ?? t).join(", ");
  return (
    <span className="status-cell">
      <Badge tone={STATUS_TONE[a.kpis.status]}><span className={`dot ${STATUS_TONE[a.kpis.status]}`} />{STATUS_LABELS[a.kpis.status]}</Badge>
      {issues && <span className="data-dot" title={`Données à corriger : ${issues}`} aria-label={`Données à corriger : ${issues}`} />}
    </span>
  );
}

/** Coverage « à date » coloured by the article thresholds : red ≤ red + 1, orange ≤ orange, grey when overstock. */
function CoverageAtDate({ a }: { a: ArticleSummary }) {
  const d = a.kpis.coverage_at_date_days;
  const tone: Severity | "ok" | "neutral" = a.kpis.overstock ? "neutral" : d <= a.kpis.alert_red_days + 1 ? "critical" : d <= a.kpis.alert_yellow_days ? "warning" : "ok";
  return <Badge tone={tone}>{d} j</Badge>;
}

export function CoverageCell({ days, a }: { days: number; a: { kpis: { coverage_target_days: number }; alert_types: string[] } }) {
  const tone: Severity | "ok" | "info" | "neutral" = a.alert_types.includes("OVERSTOCK") ? "neutral" : days <= 3 ? "critical" : days < a.kpis.coverage_target_days ? "warning" : "ok";
  return <Badge tone={tone}>{days} j</Badge>;
}

export function AlertList({ alerts, asOf }: { alerts: { article_id: string; designation: string; severity: Severity; alert_type: string; message: string; date: string | null; scope: string }[]; asOf?: string }) {
  if (!alerts.length) return <Empty title="Aucune alerte" hint="Le portefeuille est couvert sur l'horizon." />;
  return (
    <div>
      {alerts.map((a, i) => (
        <div className="alert-item" key={i}>
          <div className={`bar ${a.severity}`} />
          <div>
            <div className="msg"><Link to={`/articles/${encodeURIComponent(a.article_id)}`}><b>{a.article_id}</b></Link> · {a.message}</div>
            <div className="who"><span>{ALERT_LABELS[a.alert_type] ?? a.alert_type}</span><span>{a.designation}</span>{a.scope !== "data" && <span>scenario {SCOPE_LABELS[a.scope] ?? a.scope}</span>}{a.date && asOf && <span>J{daysFrom(a.date, asOf)! >= 0 ? "+" : ""}{daysFrom(a.date, asOf)}</span>}</div>
          </div>
        </div>
      ))}
    </div>
  );
}
