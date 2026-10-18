import { useMemo } from "react";
import { AlertTriangle, CheckSquare, ClipboardList, Clock, Inbox, PackageCheck, ShoppingCart, Truck } from "lucide-react";
import { useFlows } from "@/lib/queries";
import { ArticleLink, Badge, Card, Empty, ErrorBox, Kpi, Skeleton, SkeletonBlock, SupplierLink } from "@/components/ui";
import { useRemembered } from "@/lib/pageState";
import { DataTable, type Column } from "@/components/DataTable";
import { fmtDate, fmtInt, fmtQty, ORDER_TYPE_LABELS } from "@/lib/format";
import type { BacklogRow, DesadvRow, FlowKey, OrderRow, PendingRow, ProposalOut, ReceiptRow } from "@/lib/types";

const FLOW_H = "calc(100vh - 330px)";   // the table scrolls under its sticky header, the tiles stay in view

const FLOWS: { key: FlowKey; label: string; hint: string; icon: typeof Truck; tone?: "critical" | "warning" | "ok" | "info" | "brand" }[] = [
  { key: "to_order", label: "À commander", hint: "propositions CBN urgentes (refusées comprises, marquées ignorées)", icon: ShoppingCart, tone: "critical" },
  { key: "ordered", label: "Commandé", hint: "commandes fermes apparues aujourd'hui dans l'ERP", icon: ClipboardList, tone: "brand" },
  { key: "in_transit", label: "En transit", hint: "journaux d'arrivée non validés et DESADV annoncés", icon: Truck, tone: "info" },
  { key: "to_process", label: "À traiter", hint: "DESADV en erreur, ou traités sans journal de saisie", icon: AlertTriangle, tone: "warning" },
  { key: "to_receive", label: "À réceptionner", hint: "commandes fermes attendues aujourd'hui", icon: Inbox, tone: "brand" },
  { key: "received", label: "Reçu", hint: "réceptions apparues aujourd'hui", icon: PackageCheck, tone: "ok" },
  { key: "late", label: "En retard", hint: "commandes fermes passées avec un restant ERP", icon: Clock, tone: "warning" },
  { key: "to_validate", label: "À valider", hint: "réceptions non comptabilisées depuis 7 jours (ACR à valider)", icon: CheckSquare, tone: "warning" },
];

const art = <T extends { article_id: string; designation: string }>(): Column<T> => ({ key: "article", label: "Article", get: (r) => `${r.article_id} ${r.designation}`, exportGet: (r) => r.article_id, render: (r) => <ArticleLink id={r.article_id} designation={r.designation} /> });
const sup = <T extends { supplier_id: string | null; supplier_name: string }>(): Column<T> => ({ key: "supplier", label: "Fournisseur", get: (r) => `${r.supplier_id ?? ""} ${r.supplier_name}`, title: "Code ou nom : filtre sur les deux ; cliquer pour ouvrir la fiche fournisseur", render: (r) => <SupplierLink id={r.supplier_id} name={r.supplier_name} /> });
const planner = <T extends { planner: string }>(): Column<T> => ({ key: "planner", label: "Approvisionneur", get: (r) => r.planner, filter: "select" });

/** The « Flux d'approvisionnement » tab : eight counters, a click opens the lines of that flow below. */
export function FlowsPanel() {
  const q = useFlows();
  const [flow, setFlow] = useRemembered<FlowKey>("flows.flow", "to_order");
  const d = q.data;

  const proposalCols = useMemo<Column<ProposalOut>[]>(() => [
    art<ProposalOut>(), { ...sup<ProposalOut>(), render: (p) => <>{p.supplier_id}<span className="sub">{p.supplier_name} · délai {p.lead_time_days} j</span></> },
    { key: "order", label: "À commander le", get: (p) => p.order_date, render: (p) => <span style={{ color: "var(--critical-fg)" }}>{fmtDate(p.order_date)}</span>, title: "Date réelle à laquelle la commande devait partir pour tenir le délai" },
    { key: "delivery", label: "Livraison", get: (p) => p.delivery_date, render: (p) => fmtDate(p.delivery_date) },
    { key: "qty", label: "Quantité", get: (p) => p.qty, num: true, sum: true, unit: (p) => p.unit, render: (p) => <><b>{fmtQty(p.qty, p.unit)}</b> <span className="subtle">{p.unit}</span><span className="sub">{p.floor ? "plancher · " : ""}{p.capped ? "plafonnée · " : ""}MOQ {fmtQty(p.moq, p.unit)} · UM {fmtQty(p.pack_qty, p.unit)}</span></> },
    { key: "net", label: "Besoin net", get: (p) => p.net_requirement, num: true, render: (p) => fmtQty(p.net_requirement, p.unit) },
    { key: "stock", label: "Stock avant → après", get: (p) => p.projected_stock_before, num: true, render: (p) => <>{fmtQty(p.projected_stock_before, p.unit)} → {fmtQty(p.projected_stock_after, p.unit)}</> },
    { key: "ignored", label: "Ignorée", get: (p) => p.ignored ? "oui" : "non", filter: "select", render: (p) => p.ignored ? <Badge tone="neutral" title="Refusée d'un clic dans le tableau : hors calculs, listée pour mémoire">ignorée</Badge> : <span className="subtle">–</span> },
    { key: "reason", label: "Motif", get: (p) => p.reason, render: (p) => <span className="small subtle" style={{ whiteSpace: "normal", minWidth: 220, maxWidth: 320, display: "block" }} title={p.reason}>{p.reason.length > 110 ? `${p.reason.slice(0, 110)}…` : p.reason}</span> },
  ], []);
  const orderCols = useMemo<Column<OrderRow>[]>(() => [
    art<OrderRow>(), planner<OrderRow>(), sup<OrderRow>(),
    { key: "purch", label: "Commande", get: (o) => o.purch_id },
    { key: "type", label: "Type", get: (o) => ORDER_TYPE_LABELS[o.order_type] ?? o.order_type, filter: "select" },
    { key: "date", label: "Livraison prévue", get: (o) => o.expected_date, render: (o) => fmtDate(o.expected_date) },
    { key: "qty", label: "Commandé", get: (o) => o.qty_ordered, num: true, render: (o) => fmtQty(o.qty_ordered, o.unit) },
    { key: "open", label: "Restant", get: (o) => o.qty_open, num: true, sum: true, unit: (o) => o.unit, render: (o) => fmtQty(o.qty_open, o.unit) },
    { key: "seen", label: "Apparue le", get: (o) => o.first_seen ?? "", render: (o) => o.first_seen ? fmtDate(o.first_seen) : <span className="subtle">–</span> },
  ], []);
  const desadvCols = useMemo<Column<DesadvRow>[]>(() => [
    art<DesadvRow>(), planner<DesadvRow>(), sup<DesadvRow>(),
    { key: "bl", label: "BL", get: (x) => x.packing_slip, render: (x) => x.packing_slip || <span className="subtle">sans BL</span> },
    { key: "purch", label: "Commande", get: (x) => x.purch_id },
    { key: "date", label: "Date", get: (x) => x.issue_date, render: (x) => fmtDate(x.issue_date), title: "Date d'émission du DESADV, ou date de la ligne du journal d'arrivée" },
    { key: "qty", label: "Annoncé", get: (x) => x.qty, num: true, sum: true, unit: (x) => x.unit, render: (x) => fmtQty(x.qty, x.unit) },
    { key: "state", label: "État", get: (x) => `${x.state}${x.final_processing ? ` / ${x.final_processing}` : ""}`, filter: "select", render: (x) => <Badge tone={x.state.toLowerCase() === "erreur" ? "critical" : x.state.toLowerCase().startsWith("journal") ? "info" : x.state.toLowerCase() === "traité" && x.final_processing.toUpperCase() === "OK" ? "ok" : "warning"}>{x.state}{x.final_processing ? ` / ${x.final_processing}` : ""}</Badge> },
    { key: "issue", label: "Point", get: (x) => x.issue || (x.received ? "reçu" : "en transit"), filter: "select" },
  ], []);
  const receiptCols = useMemo<Column<ReceiptRow>[]>(() => [
    art<ReceiptRow>(), planner<ReceiptRow>(), sup<ReceiptRow>(),
    { key: "bl", label: "BL", get: (r) => r.packing_slip, render: (r) => r.packing_slip || <span className="subtle">sans BL</span> },
    { key: "purch", label: "Commande", get: (r) => r.purch_id },
    { key: "date", label: "Reçu le", get: (r) => r.receipt_date, render: (r) => fmtDate(r.receipt_date) },
    { key: "qty", label: "Quantité", get: (r) => r.qty, num: true, sum: true, unit: (r) => r.unit, render: (r) => fmtQty(r.qty, r.unit) },
    { key: "status", label: "Statut", get: (r) => r.status, filter: "select", render: (r) => <Badge tone={r.status.toLowerCase().startsWith("comptabilis") ? "ok" : r.status.toLowerCase().includes("transit") ? "info" : "warning"}>{r.status || "Reçu"}</Badge> },
  ], []);
  const backlogCols = useMemo<Column<BacklogRow>[]>(() => [
    art<BacklogRow>(), planner<BacklogRow>(), sup<BacklogRow>(),
    { key: "purch", label: "Commande", get: (b) => b.purch_id, render: (b) => <span className="mono small">{b.purch_id}</span> },
    { key: "date", label: "Livraison prévue", get: (b) => b.expected_date ?? "", render: (b) => fmtDate(b.expected_date) },
    { key: "ordered", label: "Commandé", get: (b) => b.ordered, num: true, render: (b) => fmtQty(b.ordered, b.unit) },
    { key: "received", label: "Reçu (ERP)", get: (b) => b.received, num: true, render: (b) => fmtQty(b.received, b.unit) },
    { key: "backlog", label: "Restant en retard", get: (b) => b.backlog, num: true, sum: true, unit: (b) => b.unit, render: (b) => <b style={{ color: "var(--warning-fg)" }}>{fmtQty(b.backlog, b.unit)}</b> },
    { key: "ignored", label: "Ignorée", get: (b) => (b.ignored ? "oui" : "non"), filter: "select", render: (b) => b.ignored ? <Badge tone="neutral" title="Ignorée d'un clic dans le tableau : hors projection, toujours en retard">ignorée</Badge> : <span className="subtle">–</span> },
  ], []);
  const pendingCols = useMemo<Column<PendingRow>[]>(() => [
    art<PendingRow>(), planner<PendingRow>(), sup<PendingRow>(),
    { key: "bl", label: "BL", get: (b) => b.packing_slip, render: (b) => b.packing_slip || <span className="subtle">sans BL</span> },
    { key: "purch", label: "Commande", get: (b) => b.purch_id },
    { key: "date", label: "Reçu le", get: (b) => b.registered_date, render: (b) => fmtDate(b.registered_date) },
    { key: "qty", label: "Quantité", get: (b) => b.qty, num: true, sum: true, unit: (b) => b.unit, render: (b) => fmtQty(b.qty, b.unit) },
    { key: "days", label: "Jours en attente", get: (b) => b.days_pending, num: true, render: (b) => <Badge tone={b.days_pending >= 14 ? "critical" : "warning"}>{fmtInt(b.days_pending)} j</Badge> },
  ], []);

  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const meta = FLOWS.find((f) => f.key === flow)!;
  const table = () => {
    if (!d) return <div style={{ padding: 20 }}><SkeletonBlock rows={6} /></div>;
    switch (flow) {
      case "to_order": return <div className="scroll-x"><DataTable rows={d.to_order} columns={proposalCols} rowKey={(p) => `${p.proposal_id}-${p.ignored}`} rowClass={(p) => (p.ignored ? "muted" : "")} emptyTitle="Aucune proposition urgente" emptyHint="Toutes les propositions CBN respectent le délai fournisseur." maxHeight={FLOW_H} storageKey="flows.to_order" exportTitle="À commander" /></div>;
      case "ordered": return <DataTable rows={d.ordered} columns={orderCols} rowKey={(o) => o.order_id} emptyTitle="Aucune nouvelle commande ferme aujourd'hui" emptyHint={d.first_seen_available ? undefined : "La date d'apparition des commandes est posée par le job de synchronisation : elle sera disponible après sa prochaine exécution."} maxHeight={FLOW_H} storageKey="flows.ordered" exportTitle="Commandé" />;
      case "in_transit": return <DataTable rows={d.in_transit} columns={desadvCols} rowKey={(x) => x.desadv_id} emptyTitle="Rien en transit" maxHeight={FLOW_H} storageKey="flows.in_transit" exportTitle="En transit" />;
      case "to_process": return <DataTable rows={d.to_process} columns={desadvCols} rowKey={(x) => x.desadv_id} emptyTitle="Aucun DESADV à traiter" maxHeight={FLOW_H} storageKey="flows.to_process" exportTitle="À traiter" />;
      case "to_receive": return <DataTable rows={d.to_receive} columns={orderCols} rowKey={(o) => o.order_id} emptyTitle="Aucune commande ferme attendue aujourd'hui" maxHeight={FLOW_H} storageKey="flows.to_receive" exportTitle="À réceptionner" />;
      case "received": return <DataTable rows={d.received} columns={receiptCols} rowKey={(r) => r.receipt_id} emptyTitle="Aucune réception aujourd'hui" maxHeight={FLOW_H} storageKey="flows.received" exportTitle="Reçu" />;
      case "late": return <DataTable rows={d.late} columns={backlogCols} rowKey={(b) => b.order_id} emptyTitle="Aucune commande en retard" maxHeight={FLOW_H} storageKey="flows.late" exportTitle="En retard" />;
      case "to_validate": return <DataTable rows={d.to_validate} columns={pendingCols} rowKey={(b) => b.pending_id} emptyTitle="Aucune réception en attente de comptabilisation" maxHeight={FLOW_H} storageKey="flows.to_validate" exportTitle="À valider" />;
    }
  };
  return (
    <>
      <div className="grid kpis flows sticky-kpis">
        {FLOWS.map((f) => (
          <Kpi key={f.key} label={f.label} icon={<f.icon size={14} />} tone={d && d.kpis[f.key] > 0 ? f.tone : undefined} value={d ? fmtInt(d.kpis[f.key]) : <Skeleton w={40} h={28} />} meta={f.hint} onClick={() => setFlow(f.key)} active={flow === f.key} />
        ))}
      </div>
      <Card flush title={meta.label} hint={meta.hint}>
        {d && d.kpis[flow] === 0 && flow !== "ordered" ? <Empty title={`Rien dans « ${meta.label} »`} /> : table()}
      </Card>
    </>
  );
}
