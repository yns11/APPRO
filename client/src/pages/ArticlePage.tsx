import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Download, Plus, Trash2 } from "lucide-react";
import { useCells, useProjection, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { api } from "@/lib/api";
import { Badge, Button, Card, Empty, ErrorBox, Kpi, Segmented, SeverityBadge, Skeleton, SkeletonBlock, Tabs, useToast } from "@/components/ui";
import { StockChart, CoverageChart } from "@/components/charts/StockChart";
import { EntryDrawer, type EntryDraft } from "@/components/EntryDrawer";
import { SimulationGrid } from "@/components/SimulationGrid";
import { PlanDrawer, type PlanTarget } from "@/components/PlanDrawer";
import { DataTable, type Column } from "@/components/DataTable";
import { AlertList } from "./CockpitPage";
import { KIND_LABELS, ORDER_STATUS_LABELS, ORDER_TYPE_LABELS, ORIGIN_LABELS, SOURCE_LABELS, fmtDate, fmtDateTime, fmtQty } from "@/lib/format";
import type { OrderStateOut, PlanLineState, SupplyEventOut } from "@/lib/types";

type Tab = "table" | "plan" | "orders" | "events" | "proposals" | "cells" | "alerts" | "master";

export default function ArticlePage() {
  const { articleId } = useParams();
  const { perimeter, set, engineParams } = usePerimeter();
  const [tab, setTab] = useState<Tab>("table");
  const [draft, setDraft] = useState<EntryDraft | null>(null);
  const [target, setTarget] = useState<PlanTarget | null>(null);
  const q = useProjection(articleId);
  const cells = useCells(articleId ? { article_id: articleId } : undefined);
  const toast = useToast();
  const delCell = useWrite((id: string) => api.del(`/api/entries/cells/${id}`), () => toast.push("Ajustement supprimé"));
  const d = q.data;
  const a = d?.article;
  const k = d?.kpis;
  const liveTarget = target && d ? { ...target, lines: d.plan_lines, orders: d.orders } : target;
  const exportUrl = api.downloadUrl("/api/exports/simulation.xlsx", { article_ids: [articleId ?? ""], scenario_id: engineParams.scenario_id, granularity: perimeter.granularity === "default" ? "day" : perimeter.granularity, horizon_days: engineParams.horizon_days });
  const unit = a?.unit ?? "";

  const orderCols = useMemo<Column<OrderStateOut>[]>(() => [
    { key: "order_id", label: "Commande", get: (o) => o.order_id, render: (o) => <span className="mono">{o.order_id}<span className="sub">{SOURCE_LABELS[o.source] ?? o.source}</span></span> },
    { key: "type", label: "Type", get: (o) => ORDER_TYPE_LABELS[o.order_type] ?? o.order_type, filter: "select", render: (o) => <Badge tone={o.order_type === "FIRM" ? "brand" : "neutral"}>{ORDER_TYPE_LABELS[o.order_type] ?? o.order_type}</Badge> },
    { key: "supplier", label: "Fournisseur", get: (o) => o.supplier_id ?? "", filter: "select" },
    { key: "date", label: "Date ERP", get: (o) => o.expected_date, render: (o) => <>{fmtDate(o.expected_date)}{o.days_late > 0 && <span className="sub" style={{ color: "var(--critical)" }}>{o.days_late} j de retard</span>}</> },
    { key: "open", label: "Restant", get: (o) => o.qty_open, num: true, render: (o) => <>{fmtQty(o.qty_open, unit)}<span className="sub">/ {fmtQty(o.qty_ordered, unit)}</span></> },
    { key: "erp", label: "Scenario ERP", get: (o) => o.status === "not_received" ? 0 : o.qty_expected, num: true, render: (o) => o.status === "not_received" ? <span className="subtle">exclue</span> : o.status === "info" ? <span className="subtle">info</span> : fmtQty(o.qty_expected, unit) },
    { key: "plan", label: "Scenario Plan", get: (o) => o.plan_qty, num: true, render: (o) => o.plan_qty > 0 || o.status === "planned" ? <>{fmtQty(o.plan_qty, unit)}<span className="sub">{o.plan_dates.map((x) => fmtDate(x)).join(", ")}</span></> : <span className="subtle">–</span> },
    { key: "status", label: "Statut", get: (o) => ORDER_STATUS_LABELS[o.status], filter: "select", render: (o) => <Badge tone={o.status === "not_received" ? "critical" : o.status === "planned" ? "warning" : o.status === "info" ? "neutral" : "ok"}>{ORDER_STATUS_LABELS[o.status]}</Badge> },
    { key: "note", label: "Note", get: (o) => o.note },
  ], [unit]);
  const lineCols = useMemo<Column<PlanLineState>[]>(() => [
    { key: "origin", label: "Origine", get: (l) => ORIGIN_LABELS[l.origin], filter: "select", render: (l) => <Badge tone={l.origin === "override" ? "warning" : l.origin === "free" ? "brand" : l.origin === "erp" ? "outline" : "neutral"}>{ORIGIN_LABELS[l.origin]}</Badge> },
    { key: "order", label: "Commande", get: (l) => l.order_id ?? "", render: (l) => <span className="mono">{l.order_id ?? "—"}</span> },
    { key: "erp_date", label: "Date ERP", get: (l) => l.erp_date ?? "", render: (l) => l.erp_date ? fmtDate(l.erp_date) : "" },
    { key: "erp_qty", label: "Qté ERP", get: (l) => l.erp_qty, num: true, render: (l) => l.erp_qty != null ? fmtQty(l.erp_qty, unit) : "" },
    { key: "date", label: "Date plan", get: (l) => l.date, render: (l) => <b>{fmtDate(l.date)}</b> },
    { key: "qty", label: "Qté plan", get: (l) => l.qty, num: true, render: (l) => <b>{fmtQty(l.qty, unit)}</b> },
    { key: "counted", label: "Comptée", get: (l) => l.counted ? "oui" : "non", filter: "select" },
    { key: "note", label: "Commentaire", get: (l) => l.note },
  ], [unit]);
  const eventCols = useMemo<Column<SupplyEventOut>[]>(() => [
    { key: "date", label: "Date", get: (e) => e.date, render: (e) => fmtDate(e.date) },
    { key: "kind", label: "Type", get: (e) => KIND_LABELS[e.kind] ?? e.kind, filter: "select" },
    { key: "ref", label: "Référence", get: (e) => e.ref, render: (e) => <span className="mono">{e.ref}</span> },
    { key: "supplier", label: "Fournisseur", get: (e) => e.supplier_id ?? "", filter: "select" },
    { key: "nature", label: "Nature", get: (e) => ORDER_TYPE_LABELS[e.order_type] ?? e.order_type, filter: "select" },
    { key: "source", label: "Origine", get: (e) => SOURCE_LABELS[e.source] ?? e.source, filter: "select" },
    { key: "qty", label: "Quantité", get: (e) => e.qty, num: true, render: (e) => fmtQty(e.qty, unit) },
  ], [unit]);

  if (!articleId) return <Empty title="Article non précisé" />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;

  return (
    <div className="page">
      <div className="page-header">
        <div className="title">
          <div className="row"><Link to="/" className="btn ghost sm"><ArrowLeft />Cockpit</Link>{a && <SeverityBadge severity={k?.severity ?? null} />}</div>
          <h1 style={{ marginTop: 6 }}>{articleId} {a && <span className="muted" style={{ fontWeight: 400 }}>· {a.designation}</span>}</h1>
          <p>{a ? <>{a.unit} · cible {a.coverage_target_days} j · rouge ≤ {a.alert_red_days} j · orange ≤ {a.alert_yellow_days} j · surstock ≥ {a.overstock_days} j · {d?.suppliers.map((s) => `${s.supplier_id} (délai ${s.lead_time_days} j, MOQ ${fmtQty(s.moq, a.unit)}, PLA ${fmtQty(s.pack_qty, a.unit)})`).join(" · ")}</> : <Skeleton w={400} />}</p>
        </div>
        <div className="actions">
          <Segmented size="sm" value={perimeter.granularity} onChange={(g) => set({ granularity: g })} options={[{ id: "default", label: "Par défaut" }, { id: "day", label: "Jour" }, { id: "week", label: "Semaine" }]} />
          <Button onClick={() => d && setTarget({ article_id: articleId, unit, title: `${articleId} · plan de livraison`, lines: d.plan_lines, orders: d.orders, asOf: d.as_of })}>Plan</Button>
          <Button onClick={() => setDraft({ kind: "order", article_id: articleId, supplier_id: d?.suppliers[0]?.supplier_id ?? null })}><Plus />Saisir</Button>
          <a className="btn" href={exportUrl}><Download />Excel</a>
        </div>
      </div>

      <div className="grid kpis">
        <Kpi label="Stock de référence" value={k ? fmtQty(k.stock_reference, unit) : <Skeleton w={60} h={28} />} unit={unit} meta={k ? `ERP ${fmtQty(k.stock_on_hand, unit)} au ${fmtDate(k.snapshot_date)}${k.reference_correction ? ` · corrigé de ${k.reference_correction > 0 ? "+" : ""}${fmtQty(k.reference_correction, unit)}` : ""}` : ""} tone={k?.reference_correction ? "warning" : undefined} onClick={() => setTab("cells")} />
        <Kpi label="Couverture plan" value={k ? k.coverage_plan_days : <Skeleton w={40} h={28} />} unit="j" tone={k ? (k.coverage_plan_days <= (a?.alert_red_days ?? 3) ? "critical" : k.coverage_plan_days <= (a?.alert_yellow_days ?? 7) ? "warning" : "ok") : undefined} meta={k ? `ERP : ${k.coverage_erp_days} j · cible ${k.coverage_target_days} j` : ""} />
        <Kpi label="Rupture ERP" value={k ? (k.first_stockout_erp ? fmtDate(k.first_stockout_erp) : "aucune") : <Skeleton w={60} h={28} />} tone={k?.first_stockout_erp ? "critical" : "ok"} meta={k ? (k.first_stockout_erp ? `manque max ${fmtQty(k.max_shortage_erp, unit)}` : `stock mini ${fmtQty(k.min_stock_erp, unit)}`) : ""} />
        <Kpi label="Rupture plan" value={k ? (k.first_stockout_plan ? fmtDate(k.first_stockout_plan) : "aucune") : <Skeleton w={60} h={28} />} tone={k?.first_stockout_plan ? "critical" : "ok"} meta={k ? (k.first_stockout_plan ? `manque max ${fmtQty(k.max_shortage_plan, unit)}` : `stock mini ${fmtQty(k.min_stock_plan, unit)}`) : ""} />
        <Kpi label="Backlog" value={k ? fmtQty(k.backlog_qty, unit) : <Skeleton w={60} h={28} />} unit={unit} tone={k && k.backlog_count ? "warning" : "ok"} meta={k ? `${k.backlog_count} commande(s) ERP passée(s) non reçue(s), hors stocks` : ""} onClick={() => setTab("orders")} />
        <Kpi label="En-cours ERP" value={k ? fmtQty(k.open_firm_qty, unit) : <Skeleton w={60} h={28} />} meta={k ? `ferme · + ${fmtQty(k.open_forecast_qty, unit)} prévisionnel` : ""} onClick={() => setTab("orders")} />
        <Kpi label="Plan de livraison" value={k ? fmtQty(k.plan_qty, unit) : <Skeleton w={60} h={28} />} tone="brand" meta={k ? `${k.plan_line_count} ligne(s) saisie(s) · CBN ${fmtQty(k.proposed_qty, unit)}${k.urgent_proposal_count ? ` (${k.urgent_proposal_count} urgent)` : ""}` : ""} onClick={() => setTab("plan")} />
        <Kpi label="Besoin 30 j" value={k ? fmtQty(k.demand_next_30d, unit) : <Skeleton w={60} h={28} />} meta={k ? `${fmtQty(k.avg_daily_demand_30d, unit)} / jour` : ""} />
      </div>

      {q.isLoading || !d ? <SkeletonBlock rows={10} /> : (
        <Card flush tight>
          <SimulationGrid cols={d} articles={[{ article: d.article, series: d.series, events: d.events, suppliers: d.suppliers, orders: d.orders, plan_lines: d.plan_lines, kpis: d.kpis }]} cells={cells.data ?? []} onEntry={setDraft} onPlan={setTarget} />
        </Card>
      )}

      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "table", label: "Courbes" },
        { id: "plan", label: "Plan de livraison", count: d?.plan_lines.length },
        { id: "orders", label: "Commandes ERP", count: d?.orders.length },
        { id: "events", label: "Mouvements", count: d?.events.length },
        { id: "proposals", label: "Complément CBN", count: d?.proposals.length },
        { id: "cells", label: "Ajustements", count: cells.data?.length },
        { id: "alerts", label: "Alertes", count: d?.alerts.length },
        { id: "master", label: "Données de base" },
      ]} />

      {tab === "table" && (q.isLoading || !d ? <Skeleton h={300} /> : (
        <Card><StockChart data={d} /><div style={{ marginTop: 8 }}><CoverageChart data={d} /></div></Card>
      ))}

      {tab === "plan" && d && (
        <Card flush title="Plan de livraison" hint="ERP repris tel quel, lignes modifiées / libres, complément CBN et lignes expirées"
          actions={<Button size="sm" variant="primary" onClick={() => setTarget({ article_id: articleId, unit, title: `${articleId} · plan de livraison`, lines: d.plan_lines, orders: d.orders, asOf: d.as_of })}>Modifier</Button>}>
          <DataTable rows={d.plan_lines} columns={lineCols} rowKey={(l) => `${l.line_id ?? "x"}|${l.order_id ?? ""}|${l.date}|${l.origin}`} compact
            onRowClick={(l) => setTarget({ article_id: articleId, unit, title: `${articleId} · plan ${fmtDate(l.date)}`, date: l.date, lines: d.plan_lines, orders: d.orders, asOf: d.as_of })} emptyTitle="Aucune ligne" />
        </Card>
      )}

      {tab === "orders" && d && (
        <Card flush title="Commandes ERP (créneaux fournisseur · article · date)" hint="une commande passée non reçue ne compte dans aucun scenario : la dater dans le plan si elle arrive encore">
          <DataTable rows={d.orders} columns={orderCols} rowKey={(o) => o.order_id} compact emptyTitle="Aucune commande ouverte"
            onRowClick={(o) => setTarget({ article_id: articleId, unit, title: `${articleId} · ${o.order_id}`, date: o.status === "not_received" ? undefined : o.expected_date, lines: d.plan_lines, orders: d.orders, asOf: d.as_of })} />
        </Card>
      )}

      {tab === "events" && d && (
        <Card flush><DataTable rows={d.events} columns={eventCols} rowKey={(e) => `${e.date}|${e.kind}|${e.ref}|${e.qty}`} compact emptyTitle="Aucun mouvement sur l'horizon" /></Card>
      )}

      {tab === "proposals" && (q.isLoading || !d ? <SkeletonBlock /> : d.proposals.length === 0 ? <Empty title="Aucun complément CBN" hint="Le Scenario Plan reste au-dessus de la cible sur tout l'horizon." /> : (
        <Card flush>
          <table className="tbl compact">
            <thead><tr><th>Commander le</th><th>Livraison</th><th>Fournisseur</th><th className="num">Quantité</th><th className="num">Besoin net</th><th className="num">Stock avant → après</th><th>Motif</th></tr></thead>
            <tbody>
              {d.proposals.map((p) => (
                <tr key={p.proposal_id}>
                  <td>{fmtDate(p.order_date)} {p.urgent && <Badge tone="critical">urgent</Badge>}</td>
                  <td>{fmtDate(p.delivery_date)}</td>
                  <td>{p.supplier_id} <span className="sub">délai {p.lead_time_days} j</span></td>
                  <td className="num"><b>{fmtQty(p.qty, p.unit)}</b><span className="sub">MOQ {fmtQty(p.moq, p.unit)} · PLA {fmtQty(p.pack_qty, p.unit)}</span></td>
                  <td className="num">{fmtQty(p.net_requirement, p.unit)}</td>
                  <td className="num">{fmtQty(p.projected_stock_before, p.unit)} → {fmtQty(p.projected_stock_after, p.unit)}</td>
                  <td className="small" style={{ whiteSpace: "normal", maxWidth: 360 }}>{p.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ))}

      {tab === "cells" && (cells.isLoading ? <SkeletonBlock /> : !cells.data?.length ? <Empty title="Aucun ajustement saisi" hint="Saisir directement dans la ligne Ajustement du tableau ; une date passée corrige le stock de référence." /> : (
        <Card flush title="Ajustements" hint="datés jusqu'à la référence : correction du stock de référence, persistante jusqu'à suppression ; datés après : mouvement prévu">
          <table className="tbl compact">
            <thead><tr><th>Date</th><th className="num">Quantité</th><th>Saisie</th><th>Effet</th><th>Note</th><th>Modifié</th><th></th></tr></thead>
            <tbody>
              {cells.data.map((c) => (
                <tr key={c.id}>
                  <td>{fmtDate(c.date)}</td>
                  <td className={`num ${c.qty < 0 ? "delta down" : "delta up"}`}><b>{c.qty > 0 ? "+" : ""}{fmtQty(c.qty, unit)}</b></td>
                  <td className="mono small">{c.expression}</td>
                  <td className="small">{d && c.date <= d.as_of ? <Badge tone="warning">stock de référence</Badge> : <Badge tone="neutral">mouvement prévu</Badge>}</td>
                  <td className="small subtle">{c.note}</td>
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
          <Card title="Fournisseurs & règles d'approvisionnement" hint="modifiables dans le Référentiel">
            <table className="tbl compact">
              <thead><tr><th>Fournisseur</th><th className="num">MOQ</th><th className="num">PLA</th><th className="num">Délai (j ouvrés)</th><th className="num">Quota</th><th className="num">Priorité</th></tr></thead>
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

      <EntryDrawer draft={draft} onClose={() => setDraft(null)} articles={a ? [{ article_id: a.article_id, designation: a.designation, unit: a.unit }] : []} />
      <PlanDrawer target={liveTarget} onClose={() => setTarget(null)} />
    </div>
  );
}
