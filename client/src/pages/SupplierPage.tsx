import { useMemo } from "react";
import { Link, useParams } from "react-router-dom";
import { AlertTriangle, ArrowLeft, CalendarClock, CheckSquare, Clock, Package, ShoppingCart, Truck } from "lucide-react";
import { useSupplier } from "@/lib/queries";
import { useRemembered } from "@/lib/pageState";
import { ArticleLink, Badge, Card, Empty, ErrorBox, Kpi, SeverityBadge, Skeleton, SkeletonBlock, Tabs } from "@/components/ui";
import { DataTable, type Column } from "@/components/DataTable";
import { fmtDate, fmtInt, fmtQty, fmtWeekdays } from "@/lib/format";
import type { BacklogRow, DesadvRow, OrderRow, PendingRow, ProposalOut, SupplierArticleRow } from "@/lib/types";

type Tab = "articles" | "late" | "upcoming" | "in_transit" | "to_process" | "to_validate" | "to_order";
const H = "calc(100vh - 420px)";

const art = <T extends { article_id: string; designation: string }>(): Column<T> => ({ key: "article", label: "Article", get: (r) => `${r.article_id} ${r.designation}`, exportGet: (r) => r.article_id, render: (r) => <ArticleLink id={r.article_id} designation={r.designation} /> });
const planner = <T extends { planner: string }>(): Column<T> => ({ key: "planner", label: "Approvisionneur", get: (r) => r.planner, filter: "select" });

/** One supplier : record, linked articles and their rule, supply flows of the day restricted to the supplier, upcoming firm orders. */
export default function SupplierPage() {
  const { supplierId } = useParams();
  const q = useSupplier(supplierId);
  const [tab, setTab] = useRemembered<Tab>("supplier.tab", "articles");
  const d = q.data;
  const s = d?.supplier;
  const k = d?.kpis;

  const articleCols = useMemo<Column<SupplierArticleRow>[]>(() => [
    art<SupplierArticleRow>(), planner<SupplierArticleRow>(),
    { key: "severity", label: "Statut", get: (a) => a.severity ?? "ok", filter: "select", render: (a) => <SeverityBadge severity={a.severity} /> },
    { key: "stock", label: "Stock à date", get: (a) => a.stock_at_date, num: true, render: (a) => <>{fmtQty(a.stock_at_date, a.unit)} <span className="subtle">{a.unit}</span></> },
    { key: "cov", label: "Couverture Appro.", get: (a) => a.coverage_plan_days, num: true, render: (a) => a.coverage_plan_days === null ? <span className="subtle">–</span> : <Badge tone={a.coverage_plan_days <= 2 ? "critical" : a.coverage_plan_days <= 5 ? "warning" : "ok"}>{a.coverage_plan_days} j</Badge> },
    { key: "stockout", label: "Rupture Appro.", get: (a) => a.first_stockout_plan ?? "", render: (a) => a.first_stockout_plan ? <Badge tone="critical">{fmtDate(a.first_stockout_plan)}</Badge> : <span className="subtle">–</span> },
    { key: "moq", label: "MOQ", get: (a) => a.moq, num: true, render: (a) => fmtQty(a.moq, a.unit) },
    { key: "pack", label: "UM", get: (a) => a.pack_qty, num: true, render: (a) => fmtQty(a.pack_qty, a.unit) },
    { key: "lead", label: "Délai (j ouvrés)", get: (a) => a.lead_time_days, num: true, title: "Délai effectif : valeur propre au lien, sinon celui du fournisseur (grisé)", render: (a) => a.lead_time_override === null ? <span className="inherited" title="Délai du fournisseur (hérité)">{a.lead_time_days}</span> : <b title="Délai spécifique au lien">{a.lead_time_days}</b> },
    { key: "quota", label: "Quota", get: (a) => a.quota_pct, num: true, render: (a) => <>{a.quota_pct} %{a.supplier_count > 1 && <span className="sub">{a.supplier_count} fournisseurs</span>}</> },
    { key: "cap_d", label: "Max / livraison", get: (a) => a.max_qty_per_delivery, num: true, render: (a) => a.max_qty_per_delivery === null ? <span className="subtle">–</span> : fmtQty(a.max_qty_per_delivery, a.unit) },
    { key: "cap_w", label: "Max / semaine", get: (a) => a.max_qty_per_week, num: true, render: (a) => a.max_qty_per_week === null ? <span className="subtle">–</span> : fmtQty(a.max_qty_per_week, a.unit) },
    { key: "open", label: "En-cours ferme", get: (a) => a.open_firm_qty, num: true, render: (a) => fmtQty(a.open_firm_qty, a.unit) },
    { key: "backlog", label: "En retard", get: (a) => a.backlog_qty, num: true, render: (a) => a.backlog_qty > 0 ? <span style={{ color: "var(--warning-fg)" }}>{fmtQty(a.backlog_qty, a.unit)}</span> : <span className="subtle">–</span> },
    { key: "plan", label: "Appro. + CBN", get: (a) => a.planned_qty, num: true, render: (a) => <>{fmtQty(a.planned_qty, a.unit)}{a.urgent_count > 0 && <span className="sub" style={{ color: "var(--critical-fg)" }}>{a.urgent_count} urgent(e)</span>}</> },
    { key: "active", label: "Lien actif", get: (a) => (a.active ? "oui" : "non"), filter: "select", render: (a) => <Badge tone={a.active ? "ok" : "neutral"}>{a.active ? "oui" : "non"}</Badge> },
  ], []);
  const backlogCols = useMemo<Column<BacklogRow>[]>(() => [
    art<BacklogRow>(), planner<BacklogRow>(),
    { key: "purch", label: "Commande", get: (b) => b.purch_id, render: (b) => <span className="mono small">{b.purch_id}</span> },
    { key: "date", label: "Livraison prévue", get: (b) => b.expected_date ?? "", render: (b) => fmtDate(b.expected_date) },
    { key: "ordered", label: "Commandé", get: (b) => b.ordered, num: true, render: (b) => fmtQty(b.ordered, b.unit) },
    { key: "received", label: "Reçu (ERP)", get: (b) => b.received, num: true, render: (b) => fmtQty(b.received, b.unit) },
    { key: "backlog", label: "Restant en retard", get: (b) => b.backlog, num: true, sum: true, unit: (b) => b.unit, render: (b) => <b style={{ color: "var(--warning-fg)" }}>{fmtQty(b.backlog, b.unit)}</b> },
    { key: "ignored", label: "Ignorée", get: (b) => (b.ignored ? "oui" : "non"), filter: "select", render: (b) => b.ignored ? <Badge tone="neutral" title="Ignorée d'un clic dans le tableau : hors projection, toujours en retard">ignorée</Badge> : <span className="subtle">–</span> },
  ], []);
  const orderCols = useMemo<Column<OrderRow>[]>(() => [
    art<OrderRow>(), planner<OrderRow>(),
    { key: "purch", label: "Commande", get: (o) => o.purch_id, render: (o) => <span className="mono small">{o.purch_id}</span> },
    { key: "date", label: "Livraison prévue", get: (o) => o.expected_date, render: (o) => fmtDate(o.expected_date) },
    { key: "qty", label: "Commandé", get: (o) => o.qty_ordered, num: true, render: (o) => fmtQty(o.qty_ordered, o.unit) },
    { key: "open", label: "Restant", get: (o) => o.qty_open, num: true, sum: true, unit: (o) => o.unit, render: (o) => fmtQty(o.qty_open, o.unit) },
    { key: "state", label: "Dans le tableau", get: (o) => (o.ignored ? "ignorée" : "comptée"), filter: "select", render: (o) => (o.ignored ? <Badge tone="neutral" title="Ignorée par l'approvisionneur (clic sur la ligne Ferme) : hors projection, toujours attendue">ignorée</Badge> : <span className="subtle">comptée</span>) },
  ], []);
  const desadvCols = useMemo<Column<DesadvRow>[]>(() => [
    art<DesadvRow>(), planner<DesadvRow>(),
    { key: "bl", label: "BL", get: (x) => x.packing_slip, render: (x) => x.packing_slip || <span className="subtle">sans BL</span> },
    { key: "purch", label: "Commande", get: (x) => x.purch_id },
    { key: "date", label: "Date", get: (x) => x.issue_date, render: (x) => fmtDate(x.issue_date) },
    { key: "qty", label: "Annoncé", get: (x) => x.qty, num: true, sum: true, unit: (x) => x.unit, render: (x) => fmtQty(x.qty, x.unit) },
    { key: "state", label: "État", get: (x) => `${x.state}${x.final_processing ? ` / ${x.final_processing}` : ""}`, filter: "select" },
    { key: "issue", label: "Point", get: (x) => x.issue || (x.received ? "reçu" : "en transit"), filter: "select" },
  ], []);
  const pendingCols = useMemo<Column<PendingRow>[]>(() => [
    art<PendingRow>(), planner<PendingRow>(),
    { key: "bl", label: "BL", get: (b) => b.packing_slip, render: (b) => b.packing_slip || <span className="subtle">sans BL</span> },
    { key: "purch", label: "Commande", get: (b) => b.purch_id },
    { key: "date", label: "Reçu le", get: (b) => b.registered_date, render: (b) => fmtDate(b.registered_date) },
    { key: "qty", label: "Quantité", get: (b) => b.qty, num: true, sum: true, unit: (b) => b.unit, render: (b) => fmtQty(b.qty, b.unit) },
    { key: "days", label: "Jours en attente", get: (b) => b.days_pending, num: true, render: (b) => <Badge tone={b.days_pending >= 14 ? "critical" : "warning"}>{fmtInt(b.days_pending)} j</Badge> },
  ], []);
  const proposalCols = useMemo<Column<ProposalOut>[]>(() => [
    art<ProposalOut>(),
    { key: "order", label: "À commander le", get: (p) => p.order_date, render: (p) => <span style={{ color: "var(--critical-fg)" }}>{fmtDate(p.order_date)}</span> },
    { key: "delivery", label: "Livraison", get: (p) => p.delivery_date, render: (p) => fmtDate(p.delivery_date) },
    { key: "qty", label: "Quantité", get: (p) => p.qty, num: true, sum: true, unit: (p) => p.unit, render: (p) => <><b>{fmtQty(p.qty, p.unit)}</b><span className="sub">{p.floor ? "plancher" : ""}{p.capped ? " · plafonnée" : ""}</span></> },
    { key: "reason", label: "Motif", get: (p) => p.reason, render: (p) => <span className="small subtle" style={{ whiteSpace: "normal", maxWidth: 360, display: "block" }}>{p.reason}</span> },
  ], []);

  if (!supplierId) return <Empty title="Fournisseur non précisé" />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: "articles", label: "Articles & règles", count: d?.articles.length },
    { id: "late", label: "En retard", count: d?.late.length },
    { id: "upcoming", label: "Commandes à venir", count: d?.upcoming.length },
    { id: "in_transit", label: "En transit", count: d?.in_transit.length },
    { id: "to_process", label: "À traiter", count: d?.to_process.length },
    { id: "to_validate", label: "À valider", count: d?.to_validate.length },
    { id: "to_order", label: "À commander (urgent)", count: d?.to_order.length },
  ];
  const title = `Fournisseur ${supplierId}`;
  return (
    <div className="page">
      <div className="page-header">
        <div className="title">
          <div className="row"><Link to="/fournisseurs" className="btn ghost sm"><ArrowLeft />Fournisseurs</Link>{s && !s.active && <Badge tone="neutral">inactif</Badge>}</div>
          <h1 style={{ marginTop: 6 }}>{supplierId} {s && <span className="muted" style={{ fontWeight: 400 }}>· {s.name}</span>}</h1>
          <p>{s ? <>{s.country && <>{s.country} · </>}{s.contact && <>{s.contact} · </>}livraisons <b>{fmtWeekdays(s.delivery_weekdays)}</b> · délai <b>{s.lead_time_days} j ouvrés</b> (inclusif : lundi + 5 = lundi suivant){k && k.active_links !== k.articles ? ` · ${k.active_links} lien(s) actif(s) sur ${k.articles}` : ""}</> : <Skeleton w={400} />}</p>
        </div>
        <div className="actions"><Link className="btn" to="/referentiel?table=ref_suppliers">Modifier dans le Référentiel</Link></div>
      </div>

      <div className="grid kpis">
        <Kpi label="Articles" icon={<Package size={14} />} value={k ? fmtInt(k.articles) : <Skeleton w={40} h={28} />} meta={k ? `${k.critical} critique(s)` : ""} tone={k && k.critical > 0 ? "critical" : undefined} onClick={() => setTab("articles")} active={tab === "articles"} />
        <Kpi label="En retard" icon={<Clock size={14} />} value={k ? fmtInt(k.late_orders) : <Skeleton w={40} h={28} />} meta={k ? `${fmtQty(k.late_qty)} restant sur commandes passées` : ""} tone={k && k.late_orders > 0 ? "warning" : "ok"} onClick={() => setTab("late")} active={tab === "late"} />
        <Kpi label="Commandes à venir" icon={<CalendarClock size={14} />} value={k ? fmtInt(k.upcoming_orders) : <Skeleton w={40} h={28} />} meta={k ? `${fmtQty(k.upcoming_qty)} restant à livrer` : ""} tone="brand" onClick={() => setTab("upcoming")} active={tab === "upcoming"} />
        <Kpi label="En transit" icon={<Truck size={14} />} value={k ? fmtInt(k.in_transit) : <Skeleton w={40} h={28} />} meta="journaux non validés et DESADV annoncés" tone={k && k.in_transit > 0 ? "info" : undefined} onClick={() => setTab("in_transit")} active={tab === "in_transit"} />
        <Kpi label="À traiter" icon={<AlertTriangle size={14} />} value={k ? fmtInt(k.to_process) : <Skeleton w={40} h={28} />} meta="DESADV en erreur ou sans journal" tone={k && k.to_process > 0 ? "warning" : undefined} onClick={() => setTab("to_process")} active={tab === "to_process"} />
        <Kpi label="À valider" icon={<CheckSquare size={14} />} value={k ? fmtInt(k.to_validate) : <Skeleton w={40} h={28} />} meta="réceptions sans ACR depuis 7 j" tone={k && k.to_validate > 0 ? "warning" : undefined} onClick={() => setTab("to_validate")} active={tab === "to_validate"} />
        <Kpi label="Urgences CBN" icon={<ShoppingCart size={14} />} value={k ? fmtInt(k.urgent_proposals) : <Skeleton w={40} h={28} />} meta="propositions hors délai (plancher)" tone={k && k.urgent_proposals > 0 ? "critical" : "ok"} onClick={() => setTab("to_order")} active={tab === "to_order"} />
      </div>

      <Tabs value={tab} onChange={setTab} tabs={tabs} />
      <Card flush>
        {!d ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div>
          : tab === "articles" ? <div className="scroll-x"><DataTable rows={d.articles} columns={articleCols} rowKey={(a) => a.article_id} maxHeight={H} storageKey="supplier.articles" exportTitle={`${title} - articles`} emptyTitle="Aucun article lié à ce fournisseur" /></div>
          : tab === "late" ? <DataTable rows={d.late} columns={backlogCols} rowKey={(b) => b.order_id} maxHeight={H} storageKey="supplier.late" exportTitle={`${title} - en retard`} emptyTitle="Aucune commande en retard" />
          : tab === "upcoming" ? <DataTable rows={d.upcoming} columns={orderCols} rowKey={(o) => o.order_id} maxHeight={H} storageKey="supplier.upcoming" exportTitle={`${title} - commandes à venir`} emptyTitle="Aucune commande ferme à venir" />
          : tab === "in_transit" ? <DataTable rows={d.in_transit} columns={desadvCols} rowKey={(x) => x.desadv_id} maxHeight={H} storageKey="supplier.transit" exportTitle={`${title} - en transit`} emptyTitle="Rien en transit" />
          : tab === "to_process" ? <DataTable rows={d.to_process} columns={desadvCols} rowKey={(x) => x.desadv_id} maxHeight={H} storageKey="supplier.process" exportTitle={`${title} - à traiter`} emptyTitle="Aucun DESADV à traiter" />
          : tab === "to_validate" ? <DataTable rows={d.to_validate} columns={pendingCols} rowKey={(b) => b.pending_id} maxHeight={H} storageKey="supplier.validate" exportTitle={`${title} - à valider`} emptyTitle="Aucune réception en attente de comptabilisation" />
          : <DataTable rows={d.to_order} columns={proposalCols} rowKey={(p) => `${p.proposal_id}-${p.ignored}`} rowClass={(p) => (p.ignored ? "muted" : "")} maxHeight={H} storageKey="supplier.order" exportTitle={`${title} - à commander`} emptyTitle="Aucune proposition urgente" />}
      </Card>
    </div>
  );
}
