import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Plus, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useAdjustments, useAudit, useCockpit, useOrders, usePlanLines, useProduction, useReceipts, useWrite } from "@/lib/queries";
import { Badge, Button, Card, ErrorBox, SkeletonBlock, Tabs, useToast } from "@/components/ui";
import { DataTable, type Column } from "@/components/DataTable";
import { EntryDrawer, type EntryDraft } from "@/components/EntryDrawer";
import { ORDER_TYPE_LABELS, SOURCE_LABELS, fmtDate, fmtDateTime, fmtQty } from "@/lib/format";
import type { AdjustmentOut, AuditOut, OrderOut, PlanLineOut, ProductionOut, ReceiptOut } from "@/lib/types";

type Tab = "plan" | "orders" | "receipts" | "adjustments" | "production" | "audit";
const STATUS_LABEL: Record<string, string> = { OPEN: "Ouverte", SENT: "Envoyée (ferme)", RECEIVED: "Reçue", CANCELLED: "Annulée" };

const article = <T extends { article_id: string }>(): Column<T> => ({ key: "article", label: "Article", get: (r) => r.article_id, render: (r) => <Link to={`/articles/${encodeURIComponent(r.article_id)}`}><b>{r.article_id}</b></Link> });
const who = <T extends { created_by: string; created_at: string }>(): Column<T> => ({ key: "who", label: "Créé", get: (r) => `${r.created_by} ${r.created_at}`, render: (r) => <span className="subtle small">{r.created_by}<br />{fmtDateTime(r.created_at)}</span> });

/** Planner entries (delivery plan lines, orders, receipts, adjustments, actual production) + audit journal. */
export default function EntriesPage() {
  const [tab, setTab] = useState<Tab>("plan");
  const [draft, setDraft] = useState<EntryDraft | null>(null);
  const toast = useToast();
  const cockpit = useCockpit();
  const plan = usePlanLines();
  const orders = useOrders();
  const receipts = useReceipts();
  const adjustments = useAdjustments();
  const production = useProduction();
  const audit = useAudit({ limit: 300 });
  const del = useWrite((path: string) => api.del(path), () => toast.push("Supprimé"));
  const patchOrder = useWrite(({ id, status }: { id: string; status: string }) => api.patch(`/api/entries/orders/${id}`, { status }), () => toast.push("Commande mise à jour", "success"));
  const articles = (cockpit.data?.articles ?? []).map((a) => ({ article_id: a.article_id, designation: a.designation, unit: a.unit }));
  const confirmDel = (path: string) => { if (window.confirm("Supprimer cette saisie ? L'action est journalisée.")) del.mutate(path); };
  const delCol = <T extends { id: string }>(path: string): Column<T> => ({ key: "del", label: "", get: () => "", filter: "none", sortable: false, render: (r) => <Button size="sm" variant="ghost" onClick={(e) => { e.stopPropagation(); confirmDel(`${path}/${r.id}`); }}><Trash2 /></Button> });

  const planCols = useMemo<Column<PlanLineOut>[]>(() => [
    article<PlanLineOut>(),
    { key: "order", label: "Commande", get: (l) => l.order_id ?? "", render: (l) => l.order_id ? <span className="mono">{l.order_id}<span className="sub">ERP {String(l.erp.expected_date ?? "")} · {fmtQty(Number(l.erp.qty_open ?? 0))}</span></span> : <Badge tone="brand">libre</Badge> },
    { key: "supplier", label: "Fournisseur", get: (l) => l.supplier_id ?? "", filter: "select" },
    { key: "date", label: "Date plan", get: (l) => l.date, render: (l) => fmtDate(l.date) },
    { key: "qty", label: "Quantité", get: (l) => l.qty, num: true, render: (l) => fmtQty(l.qty) },
    { key: "source", label: "Origine", get: (l) => SOURCE_LABELS[l.source] ?? l.source, filter: "select" },
    { key: "note", label: "Commentaire", get: (l) => l.note },
    { key: "who", label: "Modifié", get: (l) => `${l.updated_by} ${l.updated_at}`, render: (l) => <span className="subtle small">{l.updated_by}<br />{fmtDateTime(l.updated_at)}</span> },
    delCol<PlanLineOut>("/api/entries/plan"),
  ], []);
  const orderCols = useMemo<Column<OrderOut>[]>(() => [
    { key: "article", label: "Article", get: (o) => o.article_id, render: (o) => <><Link to={`/articles/${encodeURIComponent(o.article_id)}`}><b>{o.article_id}</b></Link><span className="sub mono">{o.id}</span></> },
    { key: "supplier", label: "Fournisseur", get: (o) => o.supplier_id ?? "", filter: "select" },
    { key: "date", label: "Livraison", get: (o) => o.expected_date, render: (o) => fmtDate(o.expected_date) },
    { key: "qty", label: "Quantité", get: (o) => o.qty, num: true, render: (o) => <>{fmtQty(o.qty, o.unit)} <span className="subtle">{o.unit}</span></> },
    { key: "type", label: "Nature", get: (o) => ORDER_TYPE_LABELS[o.order_type] ?? o.order_type, filter: "select", render: (o) => <Badge tone={o.order_type === "FIRM" ? "brand" : "neutral"}>{ORDER_TYPE_LABELS[o.order_type] ?? o.order_type}</Badge> },
    { key: "status", label: "Statut", get: (o) => STATUS_LABEL[o.status] ?? o.status, filter: "select", render: (o) => <select className="select sm" value={o.status} onClick={(e) => e.stopPropagation()} onChange={(e) => patchOrder.mutate({ id: o.id, status: e.target.value })}>{Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select> },
    { key: "source", label: "Origine", get: (o) => SOURCE_LABELS[o.source] ?? o.source, filter: "select" },
    { key: "note", label: "Note", get: (o) => o.note },
    who<OrderOut>(), delCol<OrderOut>("/api/entries/orders"),
  ], [patchOrder]);
  const receiptCols = useMemo<Column<ReceiptOut>[]>(() => [
    article<ReceiptOut>(),
    { key: "supplier", label: "Fournisseur", get: (r) => r.supplier_id ?? "", filter: "select" },
    { key: "date", label: "Date", get: (r) => r.receipt_date, render: (r) => fmtDate(r.receipt_date) },
    { key: "qty", label: "Quantité", get: (r) => r.qty, num: true, render: (r) => fmtQty(r.qty) },
    { key: "order", label: "Commande soldée", get: (r) => r.order_id ?? "", render: (r) => <span className="mono">{r.order_id ?? "–"}</span> },
    { key: "note", label: "Note", get: (r) => r.note },
    who<ReceiptOut>(), delCol<ReceiptOut>("/api/entries/receipts"),
  ], []);
  const adjCols = useMemo<Column<AdjustmentOut>[]>(() => [
    article<AdjustmentOut>(),
    { key: "date", label: "Date", get: (r) => r.date, render: (r) => fmtDate(r.date) },
    { key: "qty", label: "Quantité", get: (r) => r.qty, num: true, render: (r) => <span className={r.qty < 0 ? "delta down" : "delta up"}>{r.qty > 0 ? "+" : ""}{fmtQty(r.qty)}</span> },
    { key: "type", label: "Type", get: (r) => r.movement_type, filter: "select" },
    { key: "comment", label: "Commentaire", get: (r) => r.comment },
    who<AdjustmentOut>(), delCol<AdjustmentOut>("/api/entries/adjustments"),
  ], []);
  const prodCols = useMemo<Column<ProductionOut>[]>(() => [
    { key: "program", label: "Programme", get: (r) => r.program_id, filter: "select", render: (r) => <span className="mono">{r.program_id}</span> },
    { key: "date", label: "Date", get: (r) => r.date, render: (r) => fmtDate(r.date) },
    { key: "qty", label: "Quantité réelle", get: (r) => r.qty, num: true, render: (r) => fmtQty(r.qty) },
    who<ProductionOut>(), delCol<ProductionOut>("/api/entries/production"),
  ], []);
  const auditCols = useMemo<Column<AuditOut>[]>(() => [
    { key: "ts", label: "Horodatage", get: (e) => e.ts, render: (e) => <span className="subtle">{fmtDateTime(e.ts)}</span> },
    { key: "user", label: "Utilisateur", get: (e) => e.user, filter: "select" },
    { key: "action", label: "Action", get: (e) => e.action, filter: "select", render: (e) => <Badge tone="outline">{e.action}</Badge> },
    { key: "entity", label: "Objet", get: (e) => `${e.entity_type} ${e.entity_id}`, render: (e) => <>{e.entity_type} <span className="subtle mono">{e.entity_id}</span></> },
    { key: "article", label: "Article", get: (e) => e.article_id ?? "" },
    { key: "payload", label: "Détail", get: (e) => JSON.stringify(e.payload), render: (e) => <span className="small mono" style={{ whiteSpace: "normal", maxWidth: 480, display: "block" }}>{JSON.stringify(e.payload)}</span> },
  ], []);

  const block = <T,>(q: { isLoading: boolean; isError: boolean; error: unknown; data?: T[] }, cols: Column<T>[], key: (r: T) => string, empty: string, hint?: string) => (
    <Card flush>{q.isError ? <ErrorBox error={q.error} /> : q.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : <DataTable rows={q.data ?? []} columns={cols} rowKey={key} compact emptyTitle={empty} emptyHint={hint} />}</Card>
  );

  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Saisies & journal</h1></div>
        <div className="actions"><Button variant="primary" onClick={() => setDraft({ kind: tab === "receipts" ? "receipt" : tab === "adjustments" ? "adjustment" : tab === "production" ? "production" : "order" })}><Plus />Nouvelle saisie</Button></div>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "plan", label: "Plan de livraison", count: plan.data?.length }, { id: "orders", label: "Commandes hors ERP", count: orders.data?.length },
        { id: "receipts", label: "Réceptions", count: receipts.data?.length }, { id: "adjustments", label: "Ajustements", count: adjustments.data?.length },
        { id: "production", label: "Production réelle", count: production.data?.length }, { id: "audit", label: "Journal", count: audit.data?.length },
      ]} />
      {tab === "plan" && block(plan, planCols, (l) => l.id, "Aucune ligne de plan saisie", "Le plan se saisit dans le tableau de simulation ou la fiche article.")}
      {tab === "orders" && block(orders, orderCols, (o) => o.id, "Aucune commande ferme saisie hors ERP")}
      {tab === "receipts" && block(receipts, receiptCols, (r) => r.id, "Aucune réception saisie")}
      {tab === "adjustments" && block(adjustments, adjCols, (r) => r.id, "Aucun ajustement saisi ici", "Les ajustements se saisissent aussi dans la ligne Ajustement du tableau.")}
      {tab === "production" && block(production, prodCols, (r) => r.id, "Aucune production réelle saisie", "La production réelle d'un jour remplace le plan pour ce jour (0 = pas de production).")}
      {tab === "audit" && block(audit, auditCols, (e) => String(e.id), "Journal vide")}
      <EntryDrawer draft={draft} onClose={() => setDraft(null)} articles={articles} />
    </div>
  );
}
