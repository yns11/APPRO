import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, CalendarDays, Download, Lightbulb, Pencil, Plus, Trash2 } from "lucide-react";
import { useAdherence, useAdjustments, useConsumptionSources, useFlags, usePlanCells, useProjection, useRefRows, useRefTables, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { useRemembered } from "@/lib/pageState";
import { api } from "@/lib/api";
import { ArticleLink, Badge, Button, Card, Empty, ErrorBox, Kpi, Segmented, SeverityBadge, Skeleton, SkeletonBlock, SupplierLink, Tabs, useToast } from "@/components/ui";
import { StockChart, CoverageChart } from "@/components/charts/StockChart";
import { SimulationGrid } from "@/components/SimulationGrid";
import { DeliveryPlan } from "@/components/DeliveryPlan";
import { DataTable, type Column } from "@/components/DataTable";
import { RefRowEditor } from "@/components/RefRowEditor";
import { WeeklyParamsDrawer } from "@/components/WeeklyParamsDrawer";
import { AlertList } from "./CockpitPage";
import { CONSUMPTION_SOURCE_LABELS, CONSUMPTION_SOURCE_SHORT, ORDER_TYPE_LABELS, SOURCING_LABELS, WEEKDAY_SHORT, fmtDate, fmtDateTime, fmtDemandDays, fmtQty, fmtWeekdays } from "@/lib/format";
import type { ArticleRef, ConsumptionSource, LinkRef, OrderInfo, PlanCellOut, AdjustmentOut, RefRow } from "@/lib/types";

type Tab = "chart" | "delivery" | "params" | "plan" | "orders" | "proposals" | "adjustments" | "alerts";

export default function ArticlePage() {
  const { articleId } = useParams();
  const { perimeter, set, rights } = usePerimeter();
  const [tab, setTab] = useRemembered<Tab>("article.tab", "chart");
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
  const exportUrl = api.downloadUrl("/api/exports/simulation.xlsx", { article_ids: [articleId ?? ""], granularity: perimeter.granularity === "week" ? "week" : "day" });
  const canEdit = !!a && (rights.canEditPlanner(a.planner) || rights.canManageParams);

  const orders = useMemo(() => (d?.lanes ?? []).flatMap((l) => l.orders), [d]);
  const orderCols = useMemo<Column<OrderInfo>[]>(() => [
    { key: "supplier", label: "Fournisseur", get: (o) => o.supplier_id ?? "", filter: "select", render: (o) => <SupplierLink id={o.supplier_id} /> },
    { key: "type", label: "Type", get: (o) => ORDER_TYPE_LABELS[o.order_type] ?? o.order_type, filter: "select", render: (o) => <Badge tone={o.order_type === "FIRM" ? "brand" : "neutral"}>{ORDER_TYPE_LABELS[o.order_type] ?? o.order_type}</Badge> },
    { key: "date", label: "Date de livraison", get: (o) => o.expected_date, render: (o) => <>{fmtDate(o.expected_date)}{d && o.expected_date < d.as_of && <span className="sub subtle">passée</span>}</> },
    { key: "ordered", label: "Commandé", get: (o) => o.qty_ordered, num: true, sum: true, render: (o) => fmtQty(o.qty_ordered, unit) },
    { key: "open", label: "Restant ERP", get: (o) => o.qty_open, num: true, sum: true, render: (o) => fmtQty(o.qty_open, unit) },
    { key: "state", label: "État", get: (o) => (o.ignored ? "ignorée" : d && o.expected_date < d.as_of && o.qty_open > 0 ? "en retard" : o.qty_open > 0 ? "en cours" : "soldée"), filter: "select", render: (o) => o.ignored ? <Badge tone="critical">ignorée</Badge> : d && o.expected_date < d.as_of && o.qty_open > 0 ? <Badge tone="warning">en retard</Badge> : o.qty_open > 0 ? <Badge tone="brand">en cours</Badge> : <Badge tone="ok">soldée</Badge> },
    { key: "ref", label: "N° commande", get: (o) => o.ref, render: (o) => <span className="mono small">{o.ref}</span> },
  ], [unit, d]);
  const planCols = useMemo<Column<PlanCellOut>[]>(() => [
    { key: "supplier", label: "Fournisseur", get: (c) => c.supplier_id, filter: "select", render: (c) => <SupplierLink id={c.supplier_id} /> },
    { key: "date", label: "Date", get: (c) => c.date, render: (c) => fmtDate(c.date) },
    { key: "qty", label: "Quantité", get: (c) => c.qty, num: true, sum: true, render: (c) => <b>{fmtQty(c.qty, unit)}</b> },
    { key: "expr", label: "Saisie", get: (c) => c.expression, render: (c) => <span className="mono small">{c.expression}</span> },
    { key: "note", label: "Commentaire", get: (c) => c.note },
    { key: "who", label: "Modifié", get: (c) => `${c.updated_by} ${c.updated_at}`, render: (c) => <span className="subtle small">{c.updated_by}<br />{fmtDateTime(c.updated_at)}</span> },
    ...(rights.canWrite ? [{ key: "del", label: "", get: () => "", filter: "none" as const, sortable: false, export: false as const, render: (c: PlanCellOut) => <Button size="sm" variant="ghost" title="Supprimer : la cellule revient à l'ERP" onClick={() => delPlan.mutate(c.id)}><Trash2 /></Button> }] : []),
  ], [unit, delPlan, rights.canWrite]);
  const adjCols = useMemo<Column<AdjustmentOut>[]>(() => [
    { key: "date", label: "Date", get: (c) => c.date, render: (c) => fmtDate(c.date) },
    { key: "qty", label: "Quantité", get: (c) => c.qty, num: true, sum: true, render: (c) => <b className={c.qty < 0 ? "delta down" : "delta up"}>{c.qty > 0 ? "+" : ""}{fmtQty(c.qty, unit)}</b> },
    { key: "effect", label: "Effet", get: (c) => (d && c.date <= d.init_date ? "stock initial" : c.date <= (d?.as_of ?? "") ? "mouvement passé" : "mouvement prévu"), filter: "select", render: (c) => d && c.date <= d.init_date ? <Badge tone="warning">stock initial</Badge> : <Badge tone="neutral">{d && c.date <= d.as_of ? "mouvement passé" : "mouvement prévu"}</Badge> },
    { key: "expr", label: "Saisie", get: (c) => c.expression, render: (c) => <span className="mono small">{c.expression}</span> },
    { key: "note", label: "Commentaire", get: (c) => c.note },
    { key: "who", label: "Modifié", get: (c) => `${c.updated_by} ${c.updated_at}`, render: (c) => <span className="subtle small">{c.updated_by}<br />{fmtDateTime(c.updated_at)}</span> },
    { key: "del", label: "", get: () => "", filter: "none", sortable: false, export: false, render: (c) => <Button size="sm" variant="ghost" title="Supprimer" onClick={() => delAdj.mutate(c.id)}><Trash2 /></Button> },
  ], [unit, d, delAdj]);

  if (!articleId) return <Empty title="Article non précisé" />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;

  return (
    <div className="page">
      <div className="page-header">
        <div className="title">
          <div className="row"><Link to="/" className="btn ghost sm"><ArrowLeft />Cockpit</Link>{a && <SeverityBadge severity={k?.severity ?? null} />}{a && a.consumption_source !== "erp" && <Badge tone="info" title={CONSUMPTION_SOURCE_LABELS[a.consumption_source]}>conso {CONSUMPTION_SOURCE_SHORT[a.consumption_source]}</Badge>}{a && a.demand_days < 5 && <Badge tone="brand" title="Besoin hebdomadaire réparti sur les premiers jours ouvrés de la semaine">besoin {fmtDemandDays(a.demand_days)}</Badge>}</div>
          <h1 style={{ marginTop: 6 }}>{articleId} {a && <span className="muted" style={{ fontWeight: 400 }}>· {a.designation}</span>}</h1>
          <p>{a ? <>{a.unit} · cible {a.coverage_target_days} j ouvrés · rouge ≤ {a.alert_red_days} j · orange ≤ {a.alert_yellow_days} j · surstock ≥ {a.overstock_days} j · {d?.suppliers.map((s, i) => <span key={s.supplier_id}>{i > 0 && " · "}<SupplierLink id={s.supplier_id} name={s.supplier_name} inline /> <span className="subtle">(délai {s.lead_time_days} j, MOQ {fmtQty(s.moq, a.unit)}, UM {fmtQty(s.pack_qty, a.unit)}{s.max_qty_per_delivery ? `, max ${fmtQty(s.max_qty_per_delivery, a.unit)}/liv.` : ""}{s.max_qty_per_week ? `, max ${fmtQty(s.max_qty_per_week, a.unit)}/sem.` : ""})</span></span>)}</> : <Skeleton w={400} />}</p>
        </div>
        <div className="actions">
          <Segmented size="sm" value={perimeter.granularity} onChange={(g) => set({ granularity: g })} options={[{ id: "default", label: "Par défaut" }, { id: "day", label: "Jour" }, { id: "week", label: "Semaine" }]} />
          <Button variant={tab === "params" ? "primary" : "default"} onClick={() => setTab("params")}><Pencil />Paramétrage</Button>
          <a className="btn" href={exportUrl}><Download />Excel</a>
        </div>
      </div>

      <div className="grid kpis">
        <Kpi label="Stock à date" value={k ? fmtQty(k.stock_at_date, unit) : <Skeleton w={60} h={28} />} unit={unit} meta={k ? `fin de la veille · référence ${fmtQty(k.stock_reference, unit)} initialisée le ${fmtDate(k.init_date)}${k.reference_correction ? ` (corrigée de ${k.reference_correction > 0 ? "+" : ""}${fmtQty(k.reference_correction, unit)})` : ""}` : ""} tone={k?.reference_correction ? "warning" : undefined} onClick={() => setTab("adjustments")} />
        <Kpi label="Couverture Appro." value={k ? k.coverage_plan_days : <Skeleton w={40} h={28} />} unit="j ouvrés" tone={k ? (k.coverage_plan_days <= (a?.alert_red_days ?? 2) ? "critical" : k.coverage_plan_days <= (a?.alert_yellow_days ?? 5) ? "warning" : "ok") : undefined} meta={k ? `ERP : ${k.coverage_erp_days} j · cible ${k.coverage_target_days} j = ${fmtQty(k.target_stock, unit)}` : ""} />
        <Kpi label="Rupture ERP" value={k ? (k.first_stockout_erp ? fmtDate(k.first_stockout_erp) : "aucune") : <Skeleton w={60} h={28} />} tone={k?.first_stockout_erp ? "critical" : "ok"} meta={k ? (k.first_stockout_erp ? `manque cumulé max ${fmtQty(k.max_shortage_erp, unit)}` : `stock mini ${fmtQty(k.min_stock_erp, unit)}`) : ""} />
        <Kpi label="Rupture Appro." value={k ? (k.first_stockout_plan ? fmtDate(k.first_stockout_plan) : "aucune") : <Skeleton w={60} h={28} />} tone={k?.first_stockout_plan ? "critical" : "ok"} meta={k ? (k.first_stockout_plan ? `manque cumulé max ${fmtQty(k.max_shortage_plan, unit)}` : `stock mini ${fmtQty(k.min_stock_plan, unit)}`) : ""} />
        <Kpi label="En retard" value={k ? fmtQty(k.backlog_qty, unit) : <Skeleton w={60} h={28} />} unit={unit} tone={k && k.backlog_qty > 0 ? "warning" : "ok"} meta={k ? (k.backlog_qty > 0 ? `restant ERP des commandes fermes passées · commandé ${fmtQty(k.backlog_ordered, unit)}, reçu ${fmtQty(k.backlog_received, unit)}` : "aucune commande ferme passée avec un restant") : ""} onClick={() => setTab("orders")} />
        <Kpi label="En-cours ERP" value={k ? fmtQty(k.open_firm_qty, unit) : <Skeleton w={60} h={28} />} meta={k ? `ferme · + ${fmtQty(k.open_forecast_qty, unit)} prévisionnel` : ""} onClick={() => setTab("orders")} />
        <Kpi label="Appro." value={k ? fmtQty(k.plan_qty, unit) : <Skeleton w={60} h={28} />} tone="brand" meta={k ? `${k.plan_cell_count} cellule(s) saisie(s) · CBN ${fmtQty(k.proposed_qty, unit)}${k.urgent_proposal_count ? ` (${k.urgent_proposal_count} urgent)` : ""}${k.capped_proposal_count ? ` (${k.capped_proposal_count} plafonnée)` : ""}${k.ignored_order_days ? ` · ${k.ignored_order_days} j ferme ignoré(s)` : ""}${k.refused_proposals ? ` · ${k.refused_proposals} CBN refusée(s)` : ""}` : ""} onClick={() => setTab("delivery")} />
        <Kpi label="Besoin 20 j ouvrés" value={k ? fmtQty(k.demand_next_20wd, unit) : <Skeleton w={60} h={28} />} meta={k ? `${fmtQty(k.avg_daily_demand_wd, unit)} / jour ouvré · délai ${k.lead_time_days} j ouvrés` : ""} />
      </div>

      {q.isLoading || !d ? <SkeletonBlock rows={10} /> : (
        <Card flush tight>
          <SimulationGrid cols={d} articles={[{ article: d.article, series: d.series, lanes: d.lanes, kpis: d.kpis, suppliers: d.suppliers, proposals: d.proposals }]} planCells={planCells.data ?? []} adjustments={adjustments.data ?? []} flags={flags.data ?? []}
            onSwitchDay={() => set({ granularity: "day" })} />
        </Card>
      )}

      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "chart", label: "Courbes" },
        { id: "delivery", label: "Planning de livraison" },
        { id: "params", label: "Paramétrage" },
        { id: "plan", label: "Cellules du plan", count: planCells.data?.length },
        { id: "orders", label: "Commandes ERP", count: orders.length },
        { id: "proposals", label: "Propositions CBN", count: d?.proposals.length },
        { id: "adjustments", label: "Ajustements", count: adjustments.data?.length },
        { id: "alerts", label: "Alertes", count: d?.alerts.length },
      ]} />

      {tab === "chart" && (q.isLoading || !d ? <Skeleton h={300} /> : <Card><StockChart data={d} /><div style={{ marginTop: 8 }}><CoverageChart data={d} /></div></Card>)}

      {tab === "delivery" && <DeliveryPlan articleId={articleId} />}

      {tab === "params" && a && d && <ParamsTab article={a} links={d.suppliers} programs={d.programs} canEdit={canEdit} diagnostics={d.diagnostics} />}

      {tab === "plan" && (
        <Card flush title="Cellules saisies dans la ligne Appro." hint="tout ce qui n'est pas ici vient de l'ERP ; supprimer une cellule rend la journée à l'ERP">
          <DataTable rows={planCells.data ?? []} columns={planCols} rowKey={(c) => c.id} compact emptyTitle="Aucune cellule saisie : le plan suit l'ERP" emptyHint="Taper une quantité dans la ligne Appro. du tableau." storageKey="article.plan" exportTitle={`${articleId} - cellules Appro`} />
        </Card>
      )}

      {tab === "orders" && d && (
        <Card flush title="Commandes ERP (créneaux fournisseur · date)" hint="lecture seule ; en retard = date passée avec un restant ERP (le restant se met à jour à la validation du journal de réception)">
          <DataTable rows={orders} columns={orderCols} rowKey={(o) => o.order_id} compact emptyTitle="Aucune commande" storageKey="article.orders" exportTitle={`${articleId} - commandes ERP`} />
        </Card>
      )}

      {tab === "proposals" && (q.isLoading || !d ? <SkeletonBlock /> : d.proposals.length === 0 ? <Empty title="Aucune proposition CBN" hint="Le Projeté Appro. reste au-dessus de 85 % de la cible sur tout l'horizon, sans rupture." /> : (
        <Card flush title="Propositions CBN" hint="une par fournisseur et par jour de livraison ; pour la reprendre, taper la quantité dans la ligne Appro. (la cellule grisée la prérempli) ; ! = urgente (plancher dans le délai), ▲ = plafonnée">
          <table className="tbl compact">
            <thead><tr><th>À commander le</th><th>Livraison</th><th>Fournisseur</th><th className="num">Quantité</th><th className="num">Besoin net</th><th className="num">Stock avant → après</th><th>Motif</th></tr></thead>
            <tbody>
              {d.proposals.map((p) => (
                <tr key={p.proposal_id}>
                  <td>{fmtDate(p.order_date)} {p.urgent && <Badge tone="critical">urgent</Badge>}</td>
                  <td>{fmtDate(p.delivery_date)}</td>
                  <td><SupplierLink id={p.supplier_id} name={p.supplier_name} /><span className="sub">délai {p.lead_time_days} j ouvrés</span></td>
                  <td className="num"><b>{fmtQty(p.qty, p.unit)}</b>{p.floor && <Badge tone="critical" title="Plancher : le minimum qui évite la rupture jusqu'à la veille de la livraison suivante">plancher</Badge>}{p.capped && <Badge tone="warning" title="Tronquée au plafond du fournisseur (max / livraison ou / semaine)">plafonnée</Badge>}<span className="sub">MOQ {fmtQty(p.moq, p.unit)} · UM {fmtQty(p.pack_qty, p.unit)}</span></td>
                  <td className="num">{fmtQty(p.net_requirement, p.unit)}</td>
                  <td className="num">{fmtQty(p.projected_stock_before, p.unit)} → {fmtQty(p.projected_stock_after, p.unit)}</td>
                  <td className="small" style={{ whiteSpace: "normal", maxWidth: 380 }}>{p.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ))}

      {tab === "adjustments" && (
        <Card flush title="Ajustements" hint="datés jusqu'au jour d'initialisation du stock : correction du stock initial, persistante jusqu'à suppression ; datés après : mouvement compté à sa date">
          <DataTable rows={adjustments.data ?? []} columns={adjCols} rowKey={(c) => c.id} compact emptyTitle="Aucun ajustement" emptyHint="Saisir directement dans la ligne Ajustement du tableau." storageKey="article.adj" exportTitle={`${articleId} - ajustements`} />
        </Card>
      )}

      {tab === "alerts" && <Card>{q.isLoading || !d ? <SkeletonBlock /> : <AlertList alerts={d.alerts} asOf={d.as_of} />}</Card>}
    </div>
  );
}

/* ====================================================================== Paramétrage tab */
const POLICY_FIELDS: { key: keyof ArticleRef; label: string; help: string }[] = [
  { key: "coverage_target_days", label: "Couverture cible", help: "stock cible = besoin des n prochains jours ouvrés" },
  { key: "alert_red_days", label: "Seuil rouge", help: "commandes ERP épuisées dans ≤ n jours ouvrés" },
  { key: "alert_yellow_days", label: "Seuil orange", help: "commandes ERP épuisées dans ≤ n jours ouvrés" },
  { key: "overstock_days", label: "Surstock", help: "stock à date couvrant ≥ n jours ouvrés" },
];

function ParamsTab({ article, links, programs, canEdit, diagnostics }: { article: ArticleRef; links: LinkRef[]; programs: { program_id: string; name: string; qty_per: number; unit: string; production_next_30d: number }[]; canEdit: boolean; diagnostics: string[] }) {
  const toast = useToast();
  const tables = useRefTables();
  const linkTable = tables.data?.find((t) => t.name === "ref_article_suppliers");
  const termTable = tables.data?.find((t) => t.name === "ref_consumption_terms");
  const [editLink, setEditLink] = useState<RefRow | null>(null);
  const [editTerm, setEditTerm] = useState<RefRow | null>(null);
  const [weekly, setWeekly] = useState(false);
  const adh = useAdherence({ weeks: 8, article_id: article.article_id }, article.consumption_source !== "plan");
  const row = adh.data?.rows[0];
  const sources = useConsumptionSources(article.article_id, article.consumption_source === "derived");
  const base = { article_id: article.article_id, designation: article.designation, unit: article.unit, family: article.family, planner: article.planner, active: article.active };
  const saveArticle = useWrite((patch: Record<string, unknown>) => api.put("/api/reference/ref_articles/rows", { values: { ...base, coverage_target_days: article.coverage_target_days, alert_red_days: article.alert_red_days, alert_yellow_days: article.alert_yellow_days, overstock_days: article.overstock_days, demand_days: article.demand_days, sourcing_policy: article.sourcing_policy, consumption_source: article.consumption_source, ...patch } }),
    () => toast.push("Paramètre enregistré", "success"));
  const delTerm = useWrite((src: string) => api.post("/api/reference/ref_consumption_terms/delete", { key: { article_id: article.article_id, source_article_id: src } }), () => toast.push("Terme supprimé"));
  const delLink = useWrite((sid: string) => api.post("/api/reference/ref_article_suppliers/delete", { key: { article_id: article.article_id, supplier_id: sid } }), () => toast.push("Lien supprimé"));
  const numInput = (key: keyof ArticleRef) => (
    <input className="input sm" type="number" min={0} key={`${key}-${article[key]}`} defaultValue={String(article[key])} disabled={!canEdit} style={{ width: 70 }} aria-label={POLICY_FIELDS.find((f) => f.key === key)?.label}
      onBlur={(e) => { const v = Number(e.target.value); if (!Number.isNaN(v) && v !== article[key]) saveArticle.mutate({ [key]: v }); }} />
  );
  const share = row?.weekday_share ?? [];
  const maxShare = Math.max(1, ...share);
  const setSource = (v: ConsumptionSource) => { if (v !== article.consumption_source) saveArticle.mutate({ consumption_source: v }); };
  const linkRow = (l: LinkRef): RefRow => ({ article_id: l.article_id, supplier_id: l.supplier_id, moq: l.moq, pack_qty: l.pack_qty, lead_time_override: l.lead_time_override, quota_pct: l.quota_pct, priority: l.priority, max_qty_per_delivery: l.max_qty_per_delivery, max_qty_per_week: l.max_qty_per_week, active: l.active });

  return (
    <div className="stack">
      {!canEdit && <div className="note">Lecture seule : cet article est hors de votre carnet. Les paramètres sont modifiables par son approvisionneur, un manager ou un administrateur.</div>}
      <div className="grid cols-2">
        <Card title="Politique de stock" hint="en jours ouvrés ; valeurs de l'article, modifiables semaine par semaine" actions={<Button size="sm" onClick={() => setWeekly(true)} title="Valeurs propres à certaines semaines (saisonnalité, arrêt d'usine…)"><CalendarDays />Par semaine</Button>}>
          <div className="param-grid">
            {POLICY_FIELDS.map((f) => (
              <div key={f.key} className={`param ${canEdit ? "editable" : ""}`} title={f.help}>
                <span className="label">{f.label}</span>
                <span className="value">{numInput(f.key)}<small>j ouvrés</small></span>
                <span className="small subtle">{f.help}</span>
              </div>
            ))}
            <div className="param" title="Répartition du besoin entre plusieurs fournisseurs">
              <span className="label">Multi-sourcing</span>
              <span className="value"><select className="select sm" value={article.sourcing_policy} disabled={!canEdit || links.length < 2} onChange={(e) => saveArticle.mutate({ sourcing_policy: e.target.value })}>{Object.entries(SOURCING_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></span>
              <span className="small subtle">{links.length < 2 ? "mono-source : sans objet" : article.sourcing_policy === "quota" ? "chaque besoin réparti selon les quotas ; un excédent plafonné passe à l'autre fournisseur" : "le fournisseur prioritaire d'abord"}</span>
            </div>
          </div>
          <p className="small subtle" style={{ marginTop: 10 }}>Règles communes à tous les articles : délai inclusif en jours ouvrés, plancher dans le délai, retour à la cible sous 85 % de la cible, horizon ferme 20 j ouvrés, besoin non servi perdu. Voir <Link to="/parametres">Paramètres › Règles communes</Link>.</p>
        </Card>

        <Card title="Jours de consommation" hint="jours ouvrés de la semaine qui portent le besoin hebdomadaire (le PDP est hebdomadaire)">
          <div className="row wrap" style={{ alignItems: "flex-start", gap: 20 }}>
            <div>
              <div className="day-picker" role="radiogroup" aria-label="Jours de consommation">
                {[1, 2, 3, 4, 5].map((n) => <button key={n} type="button" className={n <= article.demand_days ? "on" : ""} disabled={!canEdit} title={`Besoin réparti du lundi au ${WEEKDAY_SHORT[n - 1]} (${n} j)`} onClick={() => n !== article.demand_days && saveArticle.mutate({ demand_days: n })}>{WEEKDAY_SHORT[n - 1]}</button>)}
              </div>
              <div className="small subtle" style={{ marginTop: 6 }}>Besoin : <b>{fmtDemandDays(article.demand_days)}</b>. Semaine en cours : réel déclaré, reliquat sur les jours du profil restants, sinon sur les jours ouvrés restants.</div>
              {row?.suggested_demand_days && row.suggested_demand_days !== article.demand_days && (
                <div className="banner" style={{ marginTop: 10 }}><Lightbulb size={16} /><span className="grow small">Sur 8 semaines, 75 % de la consommation réelle est atteinte le <b>{WEEKDAY_SHORT[row.suggested_demand_days - 1]}</b> : suggestion <b>{row.suggested_demand_days} j</b>.</span>{canEdit && <Button size="xs" onClick={() => saveArticle.mutate({ demand_days: row.suggested_demand_days })}>Appliquer</Button>}</div>
              )}
            </div>
            <div style={{ minWidth: 220, flex: 1 }}>
              <div className="small subtle" style={{ marginBottom: 4 }}>Profil observé (consommation réelle, 8 semaines)</div>
              {article.consumption_source === "plan" ? <div className="small subtle">source PDP : pas de consommation réelle à observer</div>
                : adh.isLoading ? <Skeleton h={64} /> : !row || row.real <= 0 ? <div className="small subtle">aucune consommation déclarée sur la période</div> : (
                  <div className="profile">{share.map((s, i) => <div key={i} className={`bar ${i < article.demand_days ? "on" : ""}`}><b>{Math.round(s)} %</b><i style={{ height: `${Math.max(2, (s / maxShare) * 40)}px` }} /><span>{WEEKDAY_SHORT[i]}</span></div>)}</div>
                )}
              {row && row.plan > 0 && <div className="small subtle" style={{ marginTop: 6 }}>Adhérence nette {row.net_pct ?? "–"} % · brute {row.gross_pct ?? "–"} % · biais {row.bias_pct !== null ? `${row.bias_pct > 0 ? "+" : ""}${row.bias_pct} %` : "–"}</div>}
            </div>
          </div>
        </Card>

        <Card title="Source de la consommation" hint="ce qui remplit le besoin des jours passés et le reliquat de la semaine en cours">
          <div className="stack">
            <select className="select" value={article.consumption_source} disabled={!canEdit} onChange={(e) => setSource(e.target.value as ConsumptionSource)} aria-label="Source de consommation">
              {Object.entries(CONSUMPTION_SOURCE_LABELS).map(([v, l]) => <option key={v} value={v}>{l}</option>)}
            </select>
            <p className="small subtle">
              {article.consumption_source === "erp" && "La consommation réelle déclarée dans l'ERP (fct_consumption_actual) remplit le passé ; le reliquat de la semaine en cours est repris sur les jours restants."}
              {article.consumption_source === "plan" && "Aucune consommation ERP : le besoin passé est le PDP éclaté (nomenclature) comme le futur. À réserver aux articles dont la consommation n'est pas déclarée."}
              {article.consumption_source === "derived" && "Consommation = Σ coefficient × consommation déclarée des articles sources. Sources éligibles : en mode ERP, présentes dans un programme de l'article et dans aucun programme où il est absent."}
            </p>
            {article.consumption_source === "derived" && (
              <>
                <table className="tbl compact terms-table">
                  <thead><tr><th>Article source</th><th className="num">Coefficient</th><th>Programmes communs</th><th /></tr></thead>
                  <tbody>
                    {article.derived_terms.length === 0 && <tr><td colSpan={4} className="subtle">Aucun terme : sans terme, la consommation ERP de l'article est utilisée.</td></tr>}
                    {article.derived_terms.map((t) => { const src = sources.data?.find((s) => s.article_id === t.source_article_id); return (
                      <tr key={t.source_article_id}>
                        <td><ArticleLink id={t.source_article_id} designation={src?.designation} /></td>
                        <td className="num"><b>× {t.coef}</b></td>
                        <td className="small subtle">{src ? src.shared_programs.join(", ") : <Badge tone="warning" title="L'article source n'est plus éligible (programmes ou source de consommation)">non éligible</Badge>}</td>
                        <td>{canEdit && <><Button size="xs" variant="ghost" title="Modifier le coefficient" onClick={() => setEditTerm({ article_id: article.article_id, source_article_id: t.source_article_id, coef: t.coef })}><Pencil /></Button><Button size="xs" variant="ghost" title="Supprimer" onClick={() => delTerm.mutate(t.source_article_id)}><Trash2 /></Button></>}</td>
                      </tr>); })}
                  </tbody>
                </table>
                {canEdit && <div><Button size="sm" onClick={() => setEditTerm({})} disabled={!termTable}><Plus />Ajouter un article source{sources.data ? ` (${sources.data.length} éligible${sources.data.length > 1 ? "s" : ""})` : ""}</Button></div>}
              </>
            )}
          </div>
        </Card>

        <Card title="Programmes consommateurs" hint="besoin = production × quantité par unité (nomenclature)">
          <table className="tbl compact">
            <thead><tr><th>Programme</th><th className="num">Qté / unité</th><th className="num">Production 30 j</th><th className="num">Besoin induit 30 j</th></tr></thead>
            <tbody>{programs.length === 0 && <tr><td colSpan={4} className="subtle">Article absent des nomenclatures : aucun besoin calculé.</td></tr>}{programs.map((p) => <tr key={p.program_id}><td>{p.name}<span className="sub">{p.program_id}</span></td><td className="num">{p.qty_per} {p.unit}</td><td className="num">{fmtQty(p.production_next_30d)}</td><td className="num">{fmtQty(p.production_next_30d * p.qty_per, article.unit)}</td></tr>)}</tbody>
          </table>
        </Card>
      </div>

      <Card flush title="Fournisseurs & règles d'approvisionnement" hint="délai en jours ouvrés, inclusif (lundi + 5 = lundi suivant) ; grisé = délai du fournisseur hérité ; plafonds en multiples de l'UM"
        actions={canEdit && linkTable ? <Button size="sm" variant="primary" onClick={() => setEditLink({})}><Plus />Ajouter un fournisseur</Button> : undefined}>
        <table className="tbl compact">
          <thead><tr><th>Fournisseur</th><th>Livraisons</th><th className="num">MOQ</th><th className="num">UM</th><th className="num">Délai (j ouvrés)</th><th className="num">Quota</th><th className="num">Priorité</th><th className="num">Max / livraison</th><th className="num">Max / semaine</th><th>Actif</th><th /></tr></thead>
          <tbody>
            {links.length === 0 && <tr><td colSpan={11} className="subtle">Aucun fournisseur lié : MOQ, UM et délai inconnus, aucune proposition CBN.</td></tr>}
            {links.map((s) => (
              <tr key={s.supplier_id} className={s.active ? "" : "muted"}>
                <td><SupplierLink id={s.supplier_id} name={s.supplier_name} /></td>
                <td className="small subtle"><SupplierDays supplierId={s.supplier_id} /></td>
                <td className="num">{fmtQty(s.moq, article.unit)}</td>
                <td className="num">{fmtQty(s.pack_qty, article.unit)}</td>
                <td className="num">{s.lead_time_override === null ? <span className="inherited" title="Délai du fournisseur (hérité) – saisir un délai spécifique dans le lien pour le remplacer">{s.lead_time_days}</span> : <b title="Délai spécifique au lien">{s.lead_time_days}</b>}</td>
                <td className="num">{s.quota_pct} %</td>
                <td className="num">{s.priority}</td>
                <td className="num">{s.max_qty_per_delivery === null ? <span className="subtle">–</span> : fmtQty(s.max_qty_per_delivery, article.unit)}</td>
                <td className="num">{s.max_qty_per_week === null ? <span className="subtle">–</span> : fmtQty(s.max_qty_per_week, article.unit)}</td>
                <td><Badge tone={s.active ? "ok" : "neutral"}>{s.active ? "oui" : "non"}</Badge></td>
                <td>{canEdit && linkTable && <><Button size="xs" variant="ghost" title="Modifier la règle" onClick={() => setEditLink(linkRow(s))}><Pencil /></Button><Button size="xs" variant="ghost" title="Supprimer le lien" onClick={() => { if (window.confirm(`Supprimer le lien avec ${s.supplier_id} ?`)) delLink.mutate(s.supplier_id); }}><Trash2 /></Button></>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {diagnostics.length > 0 && <details className="small subtle"><summary>Diagnostics du calcul</summary><ul>{diagnostics.map((x, i) => <li key={i}>{x}</li>)}</ul></details>}

      {linkTable && <RefRowEditor table={linkTable} row={editLink} onClose={() => setEditLink(null)} fixed={{ article_id: article.article_id }} />}
      {termTable && <RefRowEditor table={termTable} row={editTerm} onClose={() => setEditTerm(null)} fixed={{ article_id: article.article_id }} />}
      <WeeklyParamsDrawer articleId={weekly ? article.article_id : null} planner={article.planner} onClose={() => setWeekly(false)} />
    </div>
  );
}

/** Delivery weekdays of a supplier (from the suppliers table). */
function SupplierDays({ supplierId }: { supplierId: string }) {
  const rows = useRefRows("ref_suppliers");
  const s = (rows.data ?? []).find((r) => String(r.supplier_id) === supplierId);
  if (!s) return <span>…</span>;
  return <span title="Jours de livraison du fournisseur">{fmtWeekdays(String(s.delivery_weekdays ?? ""))}</span>;
}
