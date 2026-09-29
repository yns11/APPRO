import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AlertOctagon, AlertTriangle, ArrowDownToLine, Clock, Download, Layers, PackageSearch, ShoppingCart, Truck } from "lucide-react";
import { useCockpit } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { api } from "@/lib/api";
import { ALERT_LABELS, SCOPE_LABELS, fmtDate, fmtInt, fmtQty, daysFrom } from "@/lib/format";
import { Badge, Card, Empty, ErrorBox, Kpi, SeverityBadge, Skeleton, SkeletonBlock, Sparkline } from "@/components/ui";
import { DataTable, type Column } from "@/components/DataTable";
import { OutlookChart } from "@/components/charts/OutlookChart";
import type { ArticleSummary, BacklogRow, Severity } from "@/lib/types";

type Filter = "all" | "critical" | "warning" | "stockout" | "backlog" | "proposals" | "overstock" | "ok";

export default function CockpitPage() {
  const { perimeter, engineParams, config } = usePerimeter();
  const q = useCockpit();
  const nav = useNavigate();
  const [filter, setFilter] = useState<Filter>("all");
  const [showBacklog, setShowBacklog] = useState(false);
  const asOf = q.data?.as_of;

  const rows = useMemo(() => (q.data?.articles ?? []).filter((a) => {
    switch (filter) {
      case "critical": return a.severity === "critical";
      case "warning": return a.severity === "warning";
      case "stockout": return !!a.kpis.first_stockout_erp || !!a.kpis.first_stockout_plan;
      case "backlog": return a.kpis.backlog_qty > 0;
      case "proposals": return a.kpis.proposal_count > 0;
      case "overstock": return a.alert_types.includes("OVERSTOCK");
      case "ok": return !a.severity;
      default: return true;
    }
  }), [q.data, filter]);

  const cols = useMemo<Column<ArticleSummary>[]>(() => [
    { key: "article", label: "Article", get: (a) => `${a.article_id} ${a.designation}`, render: (a) => <><b>{a.article_id}</b><span className="sub">{a.designation}</span></> },
    { key: "suppliers", label: "Fournisseurs", get: (a) => a.suppliers.join(" / "), render: (a) => <span className="subtle">{a.suppliers.join(" / ")}</span> },
    { key: "severity", label: "Statut", get: (a) => a.severity ?? "ok", filter: "select", render: (a) => <SeverityBadge severity={a.severity} /> },
    { key: "stock", label: "Stock référence", get: (a) => a.kpis.stock_reference, num: true, render: (a) => <>{fmtQty(a.kpis.stock_reference, a.unit)} <span className="subtle">{a.unit}</span></> },
    { key: "cov", label: "Couverture plan", get: (a) => a.kpis.coverage_plan_days, num: true, render: (a) => <CoverageCell days={a.kpis.coverage_plan_days} a={a} /> },
    { key: "target", label: "Cible", get: (a) => a.kpis.coverage_target_days, num: true, render: (a) => <span className="subtle">{a.kpis.coverage_target_days} j</span> },
    { key: "stockout", label: "Rupture ERP", get: (a) => a.kpis.first_stockout_erp ?? "", render: (a) => { const d = daysFrom(a.kpis.first_stockout_erp, asOf ?? a.kpis.snapshot_date); return a.kpis.first_stockout_erp ? <Badge tone={d !== null && d <= 7 ? "critical" : "warning"}>{fmtDate(a.kpis.first_stockout_erp)} · J+{d}</Badge> : <span className="subtle">–</span>; } },
    { key: "stockout_plan", label: "Rupture plan", get: (a) => a.kpis.first_stockout_plan ?? "", render: (a) => a.kpis.first_stockout_plan ? <Badge tone="critical">{fmtDate(a.kpis.first_stockout_plan)}</Badge> : <span className="subtle">–</span> },
    { key: "demand", label: "Besoin 30 j", get: (a) => a.kpis.demand_next_30d, num: true, render: (a) => fmtQty(a.kpis.demand_next_30d, a.unit) },
    { key: "open", label: "En-cours ERP", get: (a) => a.kpis.open_firm_qty, num: true, render: (a) => <>{fmtQty(a.kpis.open_firm_qty, a.unit)}{a.kpis.open_forecast_qty > 0 && <span className="sub">+ {fmtQty(a.kpis.open_forecast_qty, a.unit)} prév.</span>}</> },
    { key: "backlog", label: "Backlog", get: (a) => a.kpis.backlog_qty, num: true, render: (a) => a.kpis.backlog_qty > 0 ? <span style={{ color: "var(--warning-fg)" }}>{fmtQty(a.kpis.backlog_qty, a.unit)}</span> : <span className="subtle">–</span> },
    { key: "plan", label: "Plan", get: (a) => a.kpis.plan_qty, num: true, render: (a) => <>{fmtQty(a.kpis.plan_qty, a.unit)}{a.kpis.plan_cell_count > 0 && <span className="sub">{a.kpis.plan_cell_count} cellule(s)</span>}</> },
    { key: "cbn", label: "CBN", get: (a) => a.kpis.proposal_count, num: true, render: (a) => a.kpis.proposal_count > 0 ? <>{a.kpis.proposal_count} <span className="subtle">({fmtQty(a.kpis.proposed_qty, a.unit)})</span>{a.kpis.urgent_proposal_count > 0 && <span className="sub" style={{ color: "var(--critical-fg)" }}>{a.kpis.urgent_proposal_count} urgent</span>}</> : <span className="subtle">–</span> },
    { key: "spark", label: "Scenario Plan", get: () => "", filter: "none", sortable: false, render: (a) => <Sparkline values={a.sparkline} /> },
    { key: "alerts", label: "Alertes", get: (a) => a.alert_types.map((t) => ALERT_LABELS[t] ?? t).join(" "), render: (a) => <div className="chip-list">{a.alert_types.filter((t) => t !== "URGENT_PROPOSAL").map((t) => <span key={t} className="chip">{ALERT_LABELS[t] ?? t}</span>)}</div> },
  ], [asOf]);
  const backlogCols = useMemo<Column<BacklogRow>[]>(() => [
    { key: "article", label: "Article", get: (r) => `${r.article_id} ${r.designation}`, render: (r) => <><Link to={`/articles/${encodeURIComponent(r.article_id)}`}><b>{r.article_id}</b></Link><span className="sub">{r.designation}</span></> },
    { key: "supplier", label: "Fournisseur", get: (r) => `${r.supplier_id ?? ""} ${r.supplier_name}`, filter: "text", render: (r) => <>{r.supplier_id}<span className="sub">{r.supplier_name}</span></> },
    { key: "ordered", label: "Commandé (fermes passées)", get: (r) => r.ordered, num: true, render: (r) => fmtQty(r.ordered, r.unit) },
    { key: "received", label: "Reçu", get: (r) => r.received, num: true, render: (r) => fmtQty(r.received, r.unit) },
    { key: "backlog", label: "Backlog", get: (r) => r.backlog, num: true, render: (r) => <b style={{ color: "var(--warning-fg)" }}>{fmtQty(r.backlog, r.unit)}</b> },
  ], []);

  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const k = q.data?.kpis;
  const exportUrl = api.downloadUrl("/api/exports/simulation.xlsx", { planner: engineParams.planner, granularity: perimeter.granularity === "week" ? "week" : "day", horizon_days: engineParams.horizon_days });

  return (
    <div className="page">
      <div className="page-header">
        <div className="title">
          <h1>Cockpit du jour</h1>
          <p>{asOf ? <>Situation au <b>{fmtDate(asOf)}</b> · horizon {q.data?.horizon_days} j · {q.data?.kpis.articles} articles{perimeter.planner ? ` · ${perimeter.planner}` : ""}</> : <Skeleton w={280} />}</p>
        </div>
        <div className="actions">
          <a className="btn" href={api.downloadUrl("/api/exports/alerts.xlsx", { planner: engineParams.planner })}><Download />Alertes (xlsx)</a>
          <a className="btn primary" href={exportUrl}><Download />Simulation (xlsx)</a>
        </div>
      </div>
      {config?.reference_empty && <div className="note">Le référentiel est vide : chargez d'abord les articles, fournisseurs, règles article ↔ fournisseur, programmes, nomenclatures et le stock de référence dans la page <Link to="/referentiel">Référentiel</Link> (modèles Excel à télécharger).</div>}

      <div className="grid kpis">
        <Kpi label="Critiques" icon={<AlertOctagon size={14} />} tone="critical" value={k ? fmtInt(k.critical) : <Skeleton w={40} h={28} />} meta="rupture ou couverture rouge" onClick={() => setFilter(filter === "critical" ? "all" : "critical")} active={filter === "critical"} />
        <Kpi label="À surveiller" icon={<AlertTriangle size={14} />} tone="warning" value={k ? fmtInt(k.warning) : <Skeleton w={40} h={28} />} meta="couverture orange, backlog" onClick={() => setFilter(filter === "warning" ? "all" : "warning")} active={filter === "warning"} />
        <Kpi label="Ruptures plan" icon={<PackageSearch size={14} />} tone={k && k.stockouts_7d > 0 ? "critical" : "info"} value={k ? fmtInt(k.stockouts) : <Skeleton w={40} h={28} />} meta={k ? `${k.stockouts_7d} sous 7 jours` : ""} onClick={() => setFilter(filter === "stockout" ? "all" : "stockout")} active={filter === "stockout"} />
        <Kpi label="Backlog fournisseur" icon={<Clock size={14} />} tone={k && k.backlog_articles > 0 ? "warning" : "ok"} value={k ? fmtInt(k.backlog_articles) : <Skeleton w={40} h={28} />} meta={k ? `articles · ${fmtQty(k.backlog_qty)} non reçus, hors stocks` : ""} onClick={() => setShowBacklog((s) => !s)} active={showBacklog} />
        <Kpi label="Propositions CBN" icon={<ShoppingCart size={14} />} tone="brand" value={k ? fmtInt(k.proposals) : <Skeleton w={40} h={28} />} meta={k ? `${k.urgent_proposals} urgentes · ${fmtQty(k.proposals_qty)} unités` : ""} onClick={() => nav("/propositions")} />
        <Kpi label="Couverture moyenne" icon={<Layers size={14} />} value={k ? (k.avg_coverage_days ?? "–") : <Skeleton w={40} h={28} />} unit="jours" meta="Scenario Plan, articles avec besoin" />
        <Kpi label="En-cours ERP" icon={<Truck size={14} />} value={k ? fmtQty(k.open_firm_qty) : <Skeleton w={40} h={28} />} meta={k ? `+ ${fmtQty(k.open_forecast_qty)} prévisionnel · plan ${fmtQty(k.plan_qty)} (${k.plan_articles} article(s) avec saisie)` : ""} />
        <Kpi label="Surstock" icon={<ArrowDownToLine size={14} />} tone="info" value={k ? fmtInt(k.overstock) : <Skeleton w={40} h={28} />} meta="articles au-dessus du seuil" onClick={() => setFilter(filter === "overstock" ? "all" : "overstock")} active={filter === "overstock"} />
      </div>

      {showBacklog && (
        <Card flush title="Backlog fournisseur" hint="commandes fermes passées non couvertes par des réceptions sur la fenêtre de backlog ; rien à qualifier : taper la quantité dans le plan si elle arrive encore">
          <DataTable rows={q.data?.backlog ?? []} columns={backlogCols} rowKey={(r) => `${r.article_id}|${r.supplier_id}`} compact emptyTitle="Aucun backlog" />
        </Card>
      )}

      <div className="grid cols-3">
        <Card title="Perspective 12 semaines" hint="articles en rupture / sous cible en fin de semaine, Scenario Plan" className="span-2">
          {q.isLoading ? <Skeleton h={220} /> : q.data?.weekly_supply_demand.length ? <OutlookChart data={q.data.weekly_supply_demand} /> : <Empty title="Aucune donnée" />}
        </Card>
        <Card title="Alertes prioritaires" hint={q.data ? `${q.data.alerts.length} alertes` : ""} actions={<Link className="btn sm" to="/propositions">Propositions</Link>}>
          {q.isLoading ? <SkeletonBlock /> : <AlertList alerts={(q.data?.alerts ?? []).filter((a) => a.severity !== "info").slice(0, 8)} asOf={asOf} />}
        </Card>
      </div>

      <Card flush title="Portefeuille" hint="filtrer chaque colonne ; cliquer sur une ligne pour ouvrir la fiche"
        actions={<select className="select sm" value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
          <option value="all">Tous</option><option value="critical">Critiques</option><option value="warning">À surveiller</option>
          <option value="stockout">Ruptures</option><option value="backlog">Avec backlog</option><option value="proposals">Avec proposition CBN</option>
          <option value="overstock">Surstock</option><option value="ok">Sans alerte</option>
        </select>}>
        {q.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div> : (
          <div className="scroll-x"><DataTable rows={rows} columns={cols} rowKey={(a) => a.article_id} onRowClick={(a) => nav(`/articles/${encodeURIComponent(a.article_id)}`)} emptyTitle="Aucun article" /></div>
        )}
      </Card>
      {q.data?.diagnostics?.length ? <p className="small subtle">{q.data.diagnostics.slice(-1)[0]}</p> : null}
    </div>
  );
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
