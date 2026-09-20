import { useState } from "react";
import { Link } from "react-router-dom";
import { Plus, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useAdjustments, useAudit, useCockpit, useOrders, useProduction, useReceipts, useWrite } from "@/lib/queries";
import { Badge, Button, Card, Empty, ErrorBox, SkeletonBlock, Tabs, useToast } from "@/components/ui";
import { EntryDrawer, type EntryDraft } from "@/components/EntryDrawer";
import { ORDER_TYPE_LABELS, fmtDate, fmtDateTime, fmtQty } from "@/lib/format";

type Tab = "orders" | "receipts" | "adjustments" | "production" | "audit";
const STATUS_LABEL: Record<string, string> = { OPEN: "Ouverte", SENT: "Envoyée (ferme)", RECEIVED: "Reçue", CANCELLED: "Annulée" };

/** Planner entries (orders, receipts, adjustments, actual production) + audit journal. */
export default function EntriesPage() {
  const [tab, setTab] = useState<Tab>("orders");
  const [draft, setDraft] = useState<EntryDraft | null>(null);
  const toast = useToast();
  const cockpit = useCockpit();
  const orders = useOrders();
  const receipts = useReceipts();
  const adjustments = useAdjustments();
  const production = useProduction();
  const audit = useAudit({ limit: 300 });
  const del = useWrite((path: string) => api.del(path), () => toast.push("Supprimé"));
  const patchOrder = useWrite(({ id, status }: { id: string; status: string }) => api.patch(`/api/entries/orders/${id}`, { status }), () => toast.push("Commande mise à jour", "success"));
  const articles = (cockpit.data?.articles ?? []).map((a) => ({ article_id: a.article_id, designation: a.designation, unit: a.unit }));
  const confirmDel = (path: string) => { if (window.confirm("Supprimer cette saisie ? L'action est journalisée.")) del.mutate(path); };

  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Saisies & journal</h1><p>Commandes fermes passées hors ERP, réceptions, ajustements de stock et production réelle saisis dans l'application. Les commandes simulées se saisissent directement dans le tableau de simulation de la fiche article (ou par le Calcul CBN). Ces données complètent l'ERP et sont tracées (qui, quoi, quand).</p></div>
        <div className="actions"><Button variant="primary" onClick={() => setDraft({ kind: tab === "receipts" ? "receipt" : tab === "adjustments" ? "adjustment" : tab === "production" ? "production" : "order" })}><Plus />Nouvelle saisie</Button></div>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "orders", label: "Commandes", count: orders.data?.length }, { id: "receipts", label: "Réceptions", count: receipts.data?.length },
        { id: "adjustments", label: "Ajustements", count: adjustments.data?.length }, { id: "production", label: "Production réelle", count: production.data?.length },
        { id: "audit", label: "Journal des actions", count: audit.data?.length },
      ]} />

      {tab === "orders" && <Card flush>{orders.isError ? <ErrorBox error={orders.error} /> : orders.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : !orders.data?.length ? <Empty title="Aucune commande ferme saisie" hint="Les commandes simulées se saisissent dans le tableau de la fiche article." /> : (
        <table className="tbl">
          <thead><tr><th>Article</th><th>Fournisseur</th><th>Livraison</th><th className="num">Quantité</th><th>Nature</th><th>Statut</th><th>Origine</th><th>Note</th><th>Créée</th><th></th></tr></thead>
          <tbody>{orders.data.map((o) => (
            <tr key={o.id}>
              <td><Link to={`/articles/${encodeURIComponent(o.article_id)}`}><b>{o.article_id}</b></Link><span className="sub mono">{o.id}</span></td>
              <td>{o.supplier_id ?? "–"}</td><td>{fmtDate(o.expected_date)}</td>
              <td className="num">{fmtQty(o.qty, o.unit)} <span className="subtle">{o.unit}</span></td>
              <td><Badge tone={o.order_type === "FIRM" ? "brand" : "neutral"}>{ORDER_TYPE_LABELS[o.order_type] ?? o.order_type}</Badge></td>
              <td><select className="select sm" value={o.status} onChange={(e) => patchOrder.mutate({ id: o.id, status: e.target.value })}>{Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></td>
              <td className="subtle">{o.source}{o.proposal_id ? ` · ${o.proposal_id}` : ""}</td>
              <td className="small" style={{ whiteSpace: "normal", maxWidth: 240 }}>{o.note}</td>
              <td className="subtle small">{o.created_by}<br />{fmtDateTime(o.created_at)}</td>
              <td><Button size="sm" variant="ghost" onClick={() => confirmDel(`/api/entries/orders/${o.id}`)}><Trash2 /></Button></td>
            </tr>
          ))}</tbody>
        </table>
      )}</Card>}

      {tab === "receipts" && <Card flush>{receipts.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : !receipts.data?.length ? <Empty title="Aucune réception saisie" /> : (
        <table className="tbl">
          <thead><tr><th>Article</th><th>Fournisseur</th><th>Date</th><th className="num">Quantité</th><th>Commande soldée</th><th>Note</th><th>Créée</th><th></th></tr></thead>
          <tbody>{receipts.data.map((r) => (
            <tr key={r.id}><td><Link to={`/articles/${encodeURIComponent(r.article_id)}`}><b>{r.article_id}</b></Link></td><td>{r.supplier_id ?? "–"}</td><td>{fmtDate(r.receipt_date)}</td><td className="num">{fmtQty(r.qty)}</td><td className="mono">{r.order_id ?? "–"}</td><td className="small">{r.note}</td><td className="subtle small">{r.created_by}<br />{fmtDateTime(r.created_at)}</td><td><Button size="sm" variant="ghost" onClick={() => confirmDel(`/api/entries/receipts/${r.id}`)}><Trash2 /></Button></td></tr>
          ))}</tbody>
        </table>
      )}</Card>}

      {tab === "adjustments" && <Card flush>{adjustments.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : !adjustments.data?.length ? <Empty title="Aucun ajustement saisi" /> : (
        <table className="tbl">
          <thead><tr><th>Article</th><th>Date</th><th className="num">Quantité</th><th>Type</th><th>Commentaire</th><th>Créé</th><th></th></tr></thead>
          <tbody>{adjustments.data.map((r) => (
            <tr key={r.id}><td><Link to={`/articles/${encodeURIComponent(r.article_id)}`}><b>{r.article_id}</b></Link></td><td>{fmtDate(r.date)}</td><td className={`num ${r.qty < 0 ? "delta down" : "delta up"}`}>{r.qty > 0 ? "+" : ""}{fmtQty(r.qty)}</td><td className="subtle">{r.movement_type}</td><td className="small">{r.comment}</td><td className="subtle small">{r.created_by}<br />{fmtDateTime(r.created_at)}</td><td><Button size="sm" variant="ghost" onClick={() => confirmDel(`/api/entries/adjustments/${r.id}`)}><Trash2 /></Button></td></tr>
          ))}</tbody>
        </table>
      )}</Card>}

      {tab === "production" && <Card flush>{production.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : !production.data?.length ? <Empty title="Aucune production réelle saisie" hint="La production réelle d'un jour remplace le plan pour ce jour (0 = pas de production)." /> : (
        <table className="tbl">
          <thead><tr><th>Programme</th><th>Date</th><th className="num">Quantité réelle</th><th>Saisi</th><th></th></tr></thead>
          <tbody>{production.data.map((r) => (
            <tr key={r.id}><td className="mono">{r.program_id}</td><td>{fmtDate(r.date)}</td><td className="num">{fmtQty(r.qty)}</td><td className="subtle small">{r.created_by}<br />{fmtDateTime(r.created_at)}</td><td><Button size="sm" variant="ghost" onClick={() => confirmDel(`/api/entries/production/${r.id}`)}><Trash2 /></Button></td></tr>
          ))}</tbody>
        </table>
      )}</Card>}

      {tab === "audit" && <Card flush>{audit.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : !audit.data?.length ? <Empty title="Journal vide" /> : (
        <table className="tbl compact">
          <thead><tr><th>Horodatage</th><th>Utilisateur</th><th>Action</th><th>Objet</th><th>Article</th><th>Détail</th></tr></thead>
          <tbody>{audit.data.map((e) => (
            <tr key={e.id}><td className="subtle">{fmtDateTime(e.ts)}</td><td>{e.user}</td><td><Badge tone="outline">{e.action}</Badge></td><td>{e.entity_type} <span className="subtle mono">{e.entity_id}</span></td><td>{e.article_id ?? ""}</td><td className="small mono" style={{ whiteSpace: "normal", maxWidth: 480 }}>{JSON.stringify(e.payload)}</td></tr>
          ))}</tbody>
        </table>
      )}</Card>}

      <EntryDrawer draft={draft} onClose={() => setDraft(null)} articles={articles} />
    </div>
  );
}
