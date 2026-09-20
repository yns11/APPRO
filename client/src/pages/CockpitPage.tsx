import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { AlertOctagon, AlertTriangle, ArrowDownToLine, Clock, Download, Layers, PackageSearch, Search, ShoppingCart, Truck } from "lucide-react";
import { useCockpit } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { api } from "@/lib/api";
import { ALERT_LABELS, SCOPE_LABELS, fmtDate, fmtInt, fmtQty, daysFrom } from "@/lib/format";
import { Badge, Card, Empty, ErrorBox, Kpi, SeverityBadge, Skeleton, SkeletonBlock, Sparkline } from "@/components/ui";
import { OutlookChart } from "@/components/charts/OutlookChart";
import type { ArticleSummary, Severity } from "@/lib/types";

type Filter = "all" | "critical" | "warning" | "stockout" | "late" | "proposals" | "overstock" | "ok";
type SortKey = "severity" | "coverage" | "stockout" | "article" | "stock" | "demand";

const SEV_RANK: Record<string, number> = { critical: 0, warning: 1, info: 2 };

export default function CockpitPage() {
  const { perimeter, engineParams } = usePerimeter();
  const q = useCockpit();
  const nav = useNavigate();
  const [filter, setFilter] = useState<Filter>("all");
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<SortKey>("severity");

  const rows = useMemo(() => {
    const arts = q.data?.articles ?? [];
    const s = search.trim().toLowerCase();
    const f = arts.filter((a) => {
      if (s && !`${a.article_id} ${a.designation} ${a.suppliers.join(" ")}`.toLowerCase().includes(s)) return false;
      switch (filter) {
        case "critical": return a.severity === "critical";
        case "warning": return a.severity === "warning";
        case "stockout": return !!a.kpis.first_stockout_firm || !!a.kpis.first_stockout_sim;
        case "late": return a.kpis.late_order_count > 0;
        case "proposals": return Math.abs(a.kpis.open_planned_qty) > 0;
        case "overstock": return a.alert_types.includes("OVERSTOCK");
        case "ok": return !a.severity;
        default: return true;
      }
    });
    const cmp: Record<SortKey, (a: ArticleSummary, b: ArticleSummary) => number> = {
      severity: (a, b) => (SEV_RANK[a.severity ?? ""] ?? 3) - (SEV_RANK[b.severity ?? ""] ?? 3) || a.kpis.coverage_sim_days - b.kpis.coverage_sim_days,
      coverage: (a, b) => a.kpis.coverage_sim_days - b.kpis.coverage_sim_days,
      stockout: (a, b) => (a.kpis.first_stockout_firm ?? "9999").localeCompare(b.kpis.first_stockout_firm ?? "9999"),
      article: (a, b) => a.article_id.localeCompare(b.article_id),
      stock: (a, b) => b.kpis.stock_as_of_sim - a.kpis.stock_as_of_sim,
      demand: (a, b) => b.kpis.demand_next_30d - a.kpis.demand_next_30d,
    };
    return [...f].sort(cmp[sort]);
  }, [q.data, filter, search, sort]);

  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const k = q.data?.kpis;
  const asOf = q.data?.as_of;
  const exportUrl = api.downloadUrl("/api/exports/simulation.xlsx", { planner: engineParams.planner, scenario_id: engineParams.scenario_id, granularity: perimeter.granularity, horizon_days: engineParams.horizon_days });

  return (
    <div className="page">
      <div className="page-header">
        <div className="title">
          <h1>Cockpit du jour</h1>
          <p>{asOf ? <>Situation au <b>{fmtDate(asOf)}</b> · horizon {q.data?.horizon_days} j · {q.data?.kpis.articles} articles{perimeter.planner ? ` · ${perimeter.planner}` : ""}{q.data?.pdp_version ? ` · PDP importé « ${q.data.pdp_version.name} »` : " · PDP ERP"}</> : <Skeleton w={280} />}</p>
        </div>
        <div className="actions">
          <a className="btn" href={api.downloadUrl("/api/exports/alerts.xlsx", { planner: engineParams.planner, scenario_id: engineParams.scenario_id })}><Download />Alertes (xlsx)</a>
          <a className="btn primary" href={exportUrl}><Download />Simulation (xlsx)</a>
        </div>
      </div>

      <div className="grid kpis">
        <Kpi label="Critiques" icon={<AlertOctagon size={14} />} tone="critical" value={k ? fmtInt(k.critical) : <Skeleton w={40} h={28} />} meta="articles en rupture ou couverture rouge" onClick={() => setFilter(filter === "critical" ? "all" : "critical")} active={filter === "critical"} />
        <Kpi label="À surveiller" icon={<AlertTriangle size={14} />} tone="warning" value={k ? fmtInt(k.warning) : <Skeleton w={40} h={28} />} meta="couverture orange, retards" onClick={() => setFilter(filter === "warning" ? "all" : "warning")} active={filter === "warning"} />
        <Kpi label="Ruptures projetées" icon={<PackageSearch size={14} />} tone={k && k.stockouts_7d > 0 ? "critical" : "info"} value={k ? fmtInt(k.stockouts) : <Skeleton w={40} h={28} />} meta={k ? `${k.stockouts_7d} sous 7 jours (flux fermes)` : ""} onClick={() => setFilter(filter === "stockout" ? "all" : "stockout")} active={filter === "stockout"} />
        <Kpi label="Retards fournisseurs" icon={<Clock size={14} />} tone={k && k.late_orders > 0 ? "warning" : "ok"} value={k ? fmtInt(k.late_orders) : <Skeleton w={40} h={28} />} meta="commandes attendues non reçues" onClick={() => setFilter(filter === "late" ? "all" : "late")} active={filter === "late"} />
        <Kpi label="Commandes simulées" icon={<ShoppingCart size={14} />} tone="brand" value={k ? fmtQty(k.sim_orders_qty) : <Skeleton w={40} h={28} />} meta={k ? `${k.sim_order_articles} article(s) · Calcul CBN` : ""} onClick={() => nav("/propositions")} />
        <Kpi label="Couverture moyenne" icon={<Layers size={14} />} value={k ? (k.avg_coverage_days ?? "–") : <Skeleton w={40} h={28} />} unit="jours" meta="stock simulé, articles avec besoin" />
        <Kpi label="En-cours fournisseurs" icon={<Truck size={14} />} value={k ? fmtQty(k.open_firm_qty) : <Skeleton w={40} h={28} />} meta={k ? `+ ${fmtQty(k.open_forecast_qty)} prévisionnelles ERP · + ${fmtQty(k.open_planned_qty)} saisies` : ""} />
        <Kpi label="Surstock" icon={<ArrowDownToLine size={14} />} tone="info" value={k ? fmtInt(k.overstock) : <Skeleton w={40} h={28} />} meta="articles au-dessus du seuil" onClick={() => setFilter(filter === "overstock" ? "all" : "overstock")} active={filter === "overstock"} />
      </div>

      <div className="grid cols-3">
        <Card title="Perspective 12 semaines" hint="Articles en rupture / sous cible en fin de semaine, stock simulé" className="span-2">
          {q.isLoading ? <Skeleton h={220} /> : q.data?.weekly_supply_demand.length ? <OutlookChart data={q.data.weekly_supply_demand} /> : <Empty title="Aucune donnée" />}
        </Card>
        <Card title="Alertes prioritaires" hint={q.data ? `${q.data.alerts.length} alertes` : ""} actions={<Link className="btn sm" to="/propositions">Traiter</Link>}>
          {q.isLoading ? <SkeletonBlock /> : <AlertList alerts={(q.data?.alerts ?? []).filter((a) => a.severity !== "info").slice(0, 8)} asOf={asOf} />}
        </Card>
      </div>

      <Card flush title="Portefeuille" hint="Cliquer sur une ligne pour ouvrir la fiche article"
        actions={<>
          <div className="search"><Search /><input className="input sm" placeholder="Rechercher article, désignation, fournisseur…" value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: 280 }} /></div>
          <select className="select sm" value={filter} onChange={(e) => setFilter(e.target.value as Filter)}>
            <option value="all">Tous</option><option value="critical">Critiques</option><option value="warning">À surveiller</option>
            <option value="stockout">Ruptures</option><option value="late">Retards</option><option value="proposals">Avec commandes simulées</option>
            <option value="overstock">Surstock</option><option value="ok">Sans alerte</option>
          </select>
        </>}>
        {q.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div> : rows.length === 0 ? <Empty title="Aucun article" hint="Modifiez le filtre ou la recherche." /> : (
          <div className="scroll-x">
            <table className="tbl">
              <thead>
                <tr>
                  <Th k="article" sort={sort} setSort={setSort}>Article</Th>
                  <th>Fournisseurs</th>
                  <th>Statut</th>
                  <Th k="stock" sort={sort} setSort={setSort} num>Stock à date</Th>
                  <Th k="coverage" sort={sort} setSort={setSort} num>Couverture</Th>
                  <th className="num">Cible</th>
                  <Th k="stockout" sort={sort} setSort={setSort}>Rupture (ferme)</Th>
                  <Th k="demand" sort={sort} setSort={setSort} num>Besoin 30 j</Th>
                  <th className="num">En-cours</th>
                  <th className="num">Cdes simulées</th>
                  <th>Projection stock</th>
                  <th>Alertes</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((a) => {
                  const d = daysFrom(a.kpis.first_stockout_firm, asOf ?? a.kpis.snapshot_date);
                  return (
                    <tr key={a.article_id} className="clickable" onClick={() => nav(`/articles/${encodeURIComponent(a.article_id)}`)}>
                      <td><b>{a.article_id}</b><span className="sub">{a.designation}</span></td>
                      <td className="subtle">{a.suppliers.join(" / ")}</td>
                      <td><SeverityBadge severity={a.severity} /></td>
                      <td className="num">{fmtQty(a.kpis.stock_as_of_sim, a.unit)} <span className="subtle">{a.unit}</span></td>
                      <td className="num"><CoverageCell days={a.kpis.coverage_sim_days} a={a} /></td>
                      <td className="num subtle">{a.kpis.coverage_target_days} j</td>
                      <td>{a.kpis.first_stockout_firm ? <Badge tone={d !== null && d <= 7 ? "critical" : "warning"}>{fmtDate(a.kpis.first_stockout_firm)} · J+{d}</Badge> : <span className="subtle">–</span>}</td>
                      <td className="num">{fmtQty(a.kpis.demand_next_30d, a.unit)}</td>
                      <td className="num">{fmtQty(a.kpis.open_firm_qty + a.kpis.open_forecast_qty + a.kpis.open_planned_qty, a.unit)}{a.kpis.late_order_count > 0 && <span className="sub" style={{ color: "var(--warning-fg)" }}>{a.kpis.late_order_count} en retard</span>}</td>
                      <td className="num">{Math.abs(a.kpis.open_planned_qty) > 0 ? fmtQty(a.kpis.open_planned_qty, a.unit) : <span className="subtle">–</span>}</td>
                      <td><Sparkline values={a.sparkline} /></td>
                      <td><div className="chip-list">{a.alert_types.filter((t) => t !== "URGENT_PROPOSAL").map((t) => <span key={t} className="chip">{ALERT_LABELS[t] ?? t}</span>)}</div></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {q.data?.diagnostics?.length ? <p className="small subtle">{q.data.diagnostics.slice(-1)[0]}</p> : null}
    </div>
  );
}

function Th({ k, sort, setSort, num, children }: { k: SortKey; sort: SortKey; setSort: (k: SortKey) => void; num?: boolean; children: React.ReactNode }) {
  return <th className={`sortable ${num ? "num" : ""}`} onClick={() => setSort(k)}>{children}{sort === k ? " ▾" : ""}</th>;
}

export function CoverageCell({ days, a }: { days: number; a: { kpis: { coverage_target_days: number }; alert_types: string[] } }) {
  const tone: Severity | "ok" | "info" = a.alert_types.includes("OVERSTOCK") ? "info" : days <= 3 ? "critical" : days < a.kpis.coverage_target_days ? "warning" : "ok";
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
            <div className="who"><span>{ALERT_LABELS[a.alert_type] ?? a.alert_type}</span><span>{a.designation}</span>{a.scope !== "data" && a.scope !== "simulated" && <span>flux {SCOPE_LABELS[a.scope] ?? a.scope}{a.scope === "firm" ? "s" : ""}</span>}{a.date && asOf && <span>J{daysFrom(a.date, asOf)! >= 0 ? "+" : ""}{daysFrom(a.date, asOf)}</span>}</div>
          </div>
        </div>
      ))}
    </div>
  );
}
