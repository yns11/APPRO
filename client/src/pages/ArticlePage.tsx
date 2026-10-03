import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Download, Trash2 } from "lucide-react";
import { useAdjustments, useFlags, usePlanCells, useProjection, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { api } from "@/lib/api";
import { Badge, Button, Card, Empty, ErrorBox, Kpi, Segmented, SeverityBadge, Skeleton, SkeletonBlock, Tabs, useToast } from "@/components/ui";
import { StockChart, CoverageChart } from "@/components/charts/StockChart";
import { SimulationGrid } from "@/components/SimulationGrid";
import { DeliveryPlan } from "@/components/DeliveryPlan";
import { DataTable, type Column } from "@/components/DataTable";
import { AlertList } from "./CockpitPage";
import { ORDER_TYPE_LABELS, fmtDate, fmtDateTime, fmtQty } from "@/lib/format";
import type { OrderInfo, PlanCellOut, AdjustmentOut } from "@/lib/types";

type Tab = "chart" | "delivery" | "plan" | "orders" | "proposals" | "adjustments" | "alerts" | "master";

export default function ArticlePage() {
  const { articleId } = useParams();
  const { perimeter, set, engineParams, rights } = usePerimeter();
  const [tab, setTab] = useState<Tab>("chart");
  const q = useProjection(articleId);
  const planCells = usePlanCells(articleId ? { article_id: articleId } : undefined);
  const adjustments = useAdjustments(articleId ? { article_id: articleId } : undefined);
  const flags = useFlags(articleId ? { article_id: articleId } : undefined);
  const toast = useToast();
  const delPlan = useWrite((id: string) => api.del(`/api/entries/plan/${id}`), () => toast.push("Retour à l'ERP"));
  const delAdj = useWrite((id: string) => api.del(`/api/entries/adjustments/${id}`), () => toast.push("Ajustement supprimé"));
  const d = q.data;
  const a = d?.article;
  const k = d?.kpis;
  const unit = a?.unit ?? "";
  const exportUrl = api.downloadUrl("/api/exports/simulation.xlsx", { article_ids: [articleId ?? ""], granularity: perimeter.granularity === "week" ? "week" : "day", horizon_days: engineParams.horizon_days });

  const orders = useMemo(() => (d?.lanes ?? []).flatMap((l) => l.orders), [d]);
  const orderCols = useMemo<Column<OrderInfo>[]>(() => [
    { key: "supplier", label: "Fournisseur", get: (o) => o.supplier_id ?? "", filter: "select" },
    { key: "type", label: "Type", get: (o) => ORDER_TYPE_LABELS[o.order_type] ?? o.order_type, filter: "select", render: (o) => <Badge tone={o.order_type === "FIRM" ? "brand" : "neutral"}>{ORDER_TYPE_LABELS[o.order_type] ?? o.order_type}</Badge> },
    { key: "date", label: "Date de livraison", get: (o) => o.expected_date, render: (o) => <>{fmtDate(o.expected_date)}{d && o.expected_date < d.as_of && <span className="sub subtle">passée</span>}</> },
    { key: "ordered", label: "Commandé", get: (o) => o.qty_ordered, num: true, render: (o) => fmtQty(o.qty_ordered, unit) },
    { key: "open", label: "Restant ERP", get: (o) => o.qty_open, num: true, render: (o) => fmtQty(o.qty_open, unit) },
    { key: "state", label: "État", get: (o) => (o.ignored ? "ignorée" : o.qty_open > 0 ? "en cours" : "soldée"), filter: "select", render: (o) => o.ignored ? <Badge tone="critical">ignorée</Badge> : o.qty_open > 0 ? <Badge tone="warning">en cours</Badge> : <Badge tone="ok">soldée</Badge> },
    { key: "ref", label: "N° commande", get: (o) => o.ref, render: (o) => <span className="mono small">{o.ref}</span> },
  ], [unit, d]);
  const planCols = useMemo<Column<PlanCellOut>[]>(() => [
    { key: "supplier", label: "Fournisseur", get: (c) => c.supplier_id, filter: "select" },
    { key: "date", label: "Date", get: (c) => c.date, render: (c) => fmtDate(c.date) },
    { key: "qty", label: "Quantité", get: (c) => c.qty, num: true, render: (c) => <b>{fmtQty(c.qty, unit)}</b> },
    { key: "expr", label: "Saisie", get: (c) => c.expression, render: (c) => <span className="mono small">{c.expression}</span> },
    { key: "note", label: "Commentaire", get: (c) => c.note },
    { key: "who", label: "Modifié", get: (c) => `${c.updated_by} ${c.updated_at}`, render: (c) => <span className="subtle small">{c.updated_by}<br />{fmtDateTime(c.updated_at)}</span> },
    ...(rights.canWrite ? [{ key: "del", label: "", get: () => "", filter: "none" as const, sortable: false, render: (c: PlanCellOut) => <Button size="sm" variant="ghost" title="Supprimer : la cellule revient à l'ERP" onClick={() => delPlan.mutate(c.id)}><Trash2 /></Button> }] : []),
  ], [unit, delPlan, rights.canWrite]);
  const adjCols = useMemo<Column<AdjustmentOut>[]>(() => [
    { key: "date", label: "Date", get: (c) => c.date, render: (c) => fmtDate(c.date) },
    { key: "qty", label: "Quantité", get: (c) => c.qty, num: true, render: (c) => <b className={c.qty < 0 ? "delta down" : "delta up"}>{c.qty > 0 ? "+" : ""}{fmtQty(c.qty, unit)}</b> },
    { key: "effect", label: "Effet", get: (c) => (d && c.date <= d.init_date ? "stock initial" : c.date <= (d?.as_of ?? "") ? "mouvement passé" : "mouvement prévu"), filter: "select", render: (c) => d && c.date <= d.init_date ? <Badge tone="warning">stock initial</Badge> : <Badge tone="neutral">{d && c.date <= d.as_of ? "mouvement passé" : "mouvement prévu"}</Badge> },
    { key: "expr", label: "Saisie", get: (c) => c.expression, render: (c) => <span className="mono small">{c.expression}</span> },
    { key: "note", label: "Commentaire", get: (c) => c.note },
    { key: "who", label: "Modifié", get: (c) => `${c.updated_by} ${c.updated_at}`, render: (c) => <span className="subtle small">{c.updated_by}<br />{fmtDateTime(c.updated_at)}</span> },
    { key: "del", label: "", get: () => "", filter: "none", sortable: false, render: (c) => <Button size="sm" variant="ghost" title="Supprimer" onClick={() => delAdj.mutate(c.id)}><Trash2 /></Button> },
  ], [unit, d, delAdj]);

  if (!articleId) return <Empty title="Article non précisé" />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;

  return (
    <div className="page">
      <div className="page-header">
        <div className="title">
          <div className="row"><Link to="/" className="btn ghost sm"><ArrowLeft />Cockpit</Link>{a && <SeverityBadge severity={k?.severity ?? null} />}</div>
          <h1 style={{ marginTop: 6 }}>{articleId} {a && <span className="muted" style={{ fontWeight: 400 }}>· {a.designation}</span>}</h1>
          <p>{a ? <>{a.unit} · cible {a.coverage_target_days} j · rouge ≤ {a.alert_red_days} j · orange ≤ {a.alert_yellow_days} j · surstock ≥ {a.overstock_days} j · {d?.suppliers.map((s) => `${s.supplier_id} (délai ${s.lead_time_days} j, MOQ ${fmtQty(s.moq, a.unit)}, UM ${fmtQty(s.pack_qty, a.unit)})`).join(" · ")}</> : <Skeleton w={400} />}</p>
        </div>
        <div className="actions">
          <Segmented size="sm" value={perimeter.granularity} onChange={(g) => set({ granularity: g })} options={[{ id: "default", label: "Par défaut" }, { id: "day", label: "Jour" }, { id: "week", label: "Semaine" }]} />
          <a className="btn" href={exportUrl}><Download />Excel</a>
        </div>
      </div>

      <div className="grid kpis">
        <Kpi label="Stock de référence" value={k ? fmtQty(k.stock_reference, unit) : <Skeleton w={60} h={28} />} unit={unit} meta={k ? `ERP ${fmtQty(k.stock_on_hand, unit)} initialisé le ${fmtDate(k.init_date)}${k.reference_correction ? ` · corrigé de ${k.reference_correction > 0 ? "+" : ""}${fmtQty(k.reference_correction, unit)}` : ""}` : ""} tone={k?.reference_correction ? "warning" : undefined} onClick={() => setTab("adjustments")} />
        <Kpi label="Couverture plan" value={k ? k.coverage_plan_days : <Skeleton w={40} h={28} />} unit="j" tone={k ? (k.coverage_plan_days <= (a?.alert_red_days ?? 3) ? "critical" : k.coverage_plan_days <= (a?.alert_yellow_days ?? 7) ? "warning" : "ok") : undefined} meta={k ? `ERP : ${k.coverage_erp_days} j · cible ${k.coverage_target_days} j` : ""} />
        <Kpi label="Rupture ERP" value={k ? (k.first_stockout_erp ? fmtDate(k.first_stockout_erp) : "aucune") : <Skeleton w={60} h={28} />} tone={k?.first_stockout_erp ? "critical" : "ok"} meta={k ? (k.first_stockout_erp ? `manque max ${fmtQty(k.max_shortage_erp, unit)}` : `stock mini ${fmtQty(k.min_stock_erp, unit)}`) : ""} />
        <Kpi label="Rupture plan" value={k ? (k.first_stockout_plan ? fmtDate(k.first_stockout_plan) : "aucune") : <Skeleton w={60} h={28} />} tone={k?.first_stockout_plan ? "critical" : "ok"} meta={k ? (k.first_stockout_plan ? `manque max ${fmtQty(k.max_shortage_plan, unit)}` : `stock mini ${fmtQty(k.min_stock_plan, unit)}`) : ""} />
        <Kpi label="Backlog" value={k ? fmtQty(k.backlog_qty, unit) : <Skeleton w={60} h={28} />} unit={unit} tone={k && k.backlog_qty > 0 ? "warning" : "ok"} meta={k ? `commandé ${fmtQty(k.backlog_ordered, unit)} − reçu ${fmtQty(k.backlog_received, unit)} (fermes passées, hors stocks)` : ""} onClick={() => setTab("orders")} />
        <Kpi label="En-cours ERP" value={k ? fmtQty(k.open_firm_qty, unit) : <Skeleton w={60} h={28} />} meta={k ? `ferme · + ${fmtQty(k.open_forecast_qty, unit)} prévisionnel` : ""} onClick={() => setTab("orders")} />
        <Kpi label="Plan" value={k ? fmtQty(k.plan_qty, unit) : <Skeleton w={60} h={28} />} tone="brand" meta={k ? `${k.plan_cell_count} cellule(s) saisie(s) · CBN ${fmtQty(k.proposed_qty, unit)}${k.urgent_proposal_count ? ` (${k.urgent_proposal_count} urgent)` : ""}${k.ignored_order_days ? ` · ${k.ignored_order_days} j ferme ignoré(s)` : ""}${k.refused_proposals ? ` · ${k.refused_proposals} CBN refusée(s)` : ""}` : ""} onClick={() => setTab("delivery")} />
        <Kpi label="Besoin 30 j" value={k ? fmtQty(k.demand_next_30d, unit) : <Skeleton w={60} h={28} />} meta={k ? `${fmtQty(k.avg_daily_demand_30d, unit)} / jour` : ""} />
      </div>

      {q.isLoading || !d ? <SkeletonBlock rows={10} /> : (
        <Card flush tight>
          <SimulationGrid cols={d} articles={[{ article: d.article, series: d.series, lanes: d.lanes, kpis: d.kpis }]} planCells={planCells.data ?? []} adjustments={adjustments.data ?? []} flags={flags.data ?? []}
            onSwitchDay={() => set({ granularity: "day" })} />
        </Card>
      )}

      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "chart", label: "Courbes" },
        { id: "delivery", label: "Planning de livraison" },
        { id: "plan", label: "Cellules du plan", count: planCells.data?.length },
        { id: "orders", label: "Commandes ERP", count: orders.length },
        { id: "proposals", label: "Propositions CBN", count: d?.proposals.length },
        { id: "adjustments", label: "Ajustements", count: adjustments.data?.length },
        { id: "alerts", label: "Alertes", count: d?.alerts.length },
        { id: "master", label: "Données de base" },
      ]} />

      {tab === "chart" && (q.isLoading || !d ? <Skeleton h={300} /> : <Card><StockChart data={d} /><div style={{ marginTop: 8 }}><CoverageChart data={d} /></div></Card>)}

      {tab === "delivery" && <DeliveryPlan articleId={articleId} />}

      {tab === "plan" && (
        <Card flush title="Cellules saisies dans la ligne Plan" hint="tout ce qui n'est pas ici vient de l'ERP ; supprimer une cellule rend la journée à l'ERP">
          <DataTable rows={planCells.data ?? []} columns={planCols} rowKey={(c) => c.id} compact emptyTitle="Aucune cellule saisie : le plan suit l'ERP" emptyHint="Taper une quantité dans la ligne Plan du tableau." />
        </Card>
      )}

      {tab === "orders" && d && (
        <Card flush title="Commandes ERP (créneaux fournisseur · date)" hint={`lecture seule ; backlog = commandes fermes passées − réceptions sur la fenêtre (${d.lanes.map((l) => `${l.supplier_id ?? "–"} : ${fmtQty(l.backlog_qty, unit)}`).join(", ")})`}>
          <DataTable rows={orders} columns={orderCols} rowKey={(o) => o.order_id} compact emptyTitle="Aucune commande" />
        </Card>
      )}

      {tab === "proposals" && (q.isLoading || !d ? <SkeletonBlock /> : d.proposals.length === 0 ? <Empty title="Aucune proposition CBN" hint="Le Scenario Plan reste au-dessus de la cible sur tout l'horizon." /> : (
        <Card flush title="Propositions CBN" hint="une par fournisseur et par jour de livraison ; pour la reprendre, taper la quantité dans la ligne Plan (la cellule grisée la prérempli)">
          <table className="tbl compact">
            <thead><tr><th>Commander le</th><th>Livraison</th><th>Fournisseur</th><th className="num">Quantité</th><th className="num">Besoin net</th><th className="num">Stock avant → après</th><th>Motif</th></tr></thead>
            <tbody>
              {d.proposals.map((p) => (
                <tr key={p.proposal_id}>
                  <td>{fmtDate(p.order_date)} {p.urgent && <Badge tone="critical">urgent</Badge>}</td>
                  <td>{fmtDate(p.delivery_date)}</td>
                  <td>{p.supplier_id} <span className="sub">délai {p.lead_time_days} j</span></td>
                  <td className="num"><b>{fmtQty(p.qty, p.unit)}</b><span className="sub">MOQ {fmtQty(p.moq, p.unit)} · UM {fmtQty(p.pack_qty, p.unit)}</span></td>
                  <td className="num">{fmtQty(p.net_requirement, p.unit)}</td>
                  <td className="num">{fmtQty(p.projected_stock_before, p.unit)} → {fmtQty(p.projected_stock_after, p.unit)}</td>
                  <td className="small" style={{ whiteSpace: "normal", maxWidth: 360 }}>{p.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ))}

      {tab === "adjustments" && (
        <Card flush title="Ajustements" hint="datés jusqu'au jour d'initialisation du stock : correction du stock initial, persistante jusqu'à suppression ; datés après : mouvement compté à sa date">
          <DataTable rows={adjustments.data ?? []} columns={adjCols} rowKey={(c) => c.id} compact emptyTitle="Aucun ajustement" emptyHint="Saisir directement dans la ligne Ajustement du tableau." />
        </Card>
      )}

      {tab === "alerts" && <Card>{q.isLoading || !d ? <SkeletonBlock /> : <AlertList alerts={d.alerts} asOf={d.as_of} />}</Card>}

      {tab === "master" && d && (
        <div className="grid cols-2">
          <Card title="Fournisseurs & règles d'approvisionnement" hint="modifiables dans le Référentiel">
            <table className="tbl compact">
              <thead><tr><th>Fournisseur</th><th className="num">MOQ</th><th className="num">UM</th><th className="num">Délai (j ouvrés)</th><th className="num">Quota</th><th className="num">Priorité</th></tr></thead>
              <tbody>{d.suppliers.map((s) => <tr key={s.supplier_id}><td>{s.supplier_id}<span className="sub">{s.supplier_name}</span></td><td className="num">{fmtQty(s.moq, unit)}</td><td className="num">{fmtQty(s.pack_qty, unit)}</td><td className="num">{s.lead_time_days}</td><td className="num">{s.quota_pct} %</td><td className="num">{s.priority}</td></tr>)}</tbody>
            </table>
          </Card>
          <Card title="Programmes consommateurs" hint="besoin = production × quantité par unité">
            <table className="tbl compact">
              <thead><tr><th>Programme</th><th className="num">Qté / unité</th><th className="num">Production 30 j</th><th className="num">Besoin induit 30 j</th></tr></thead>
              <tbody>{d.programs.map((p) => <tr key={p.program_id}><td>{p.name}<span className="sub">{p.program_id}</span></td><td className="num">{p.qty_per} {p.unit}</td><td className="num">{fmtQty(p.production_next_30d)}</td><td className="num">{fmtQty(p.production_next_30d * p.qty_per, unit)}</td></tr>)}</tbody>
            </table>
          </Card>
          {d.diagnostics.length > 0 && <Card title="Diagnostics du calcul" className="cols-2"><ul className="small subtle">{d.diagnostics.map((x, i) => <li key={i}>{x}</li>)}</ul></Card>}
        </div>
      )}
    </div>
  );
}
