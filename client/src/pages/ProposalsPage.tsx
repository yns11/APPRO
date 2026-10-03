import { useMemo } from "react";
import { Link } from "react-router-dom";
import { Download } from "lucide-react";
import { useProposals } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { api } from "@/lib/api";
import { Badge, Card, ErrorBox, Kpi, SkeletonBlock } from "@/components/ui";
import { DataTable, type Column } from "@/components/DataTable";
import { fmtDate, fmtInt, fmtQty } from "@/lib/format";
import type { ProposalOut } from "@/lib/types";

/** Net requirements ("Complément CBN") computed automatically on the plan stock of the perimeter. */
export default function ProposalsPage() {
  const { engineParams } = usePerimeter();
  const q = useProposals();
  const cols = useMemo<Column<ProposalOut>[]>(() => [
    { key: "article", label: "Article", get: (p) => `${p.article_id} ${p.designation}`, render: (p) => <><Link to={`/articles/${encodeURIComponent(p.article_id)}`}><b>{p.article_id}</b></Link><span className="sub">{p.designation}</span></> },
    { key: "supplier", label: "Fournisseur", get: (p) => p.supplier_id ?? "", filter: "select", render: (p) => <>{p.supplier_id}<span className="sub">{p.supplier_name} · délai {p.lead_time_days} j</span></> },
    { key: "urgent", label: "Urgence", get: (p) => p.urgent ? "urgent" : "normal", filter: "select", render: (p) => p.urgent ? <Badge tone="critical">urgent</Badge> : <span className="subtle">–</span> },
    { key: "order", label: "Commander le", get: (p) => p.order_date, render: (p) => fmtDate(p.order_date) },
    { key: "delivery", label: "Livraison", get: (p) => p.delivery_date, render: (p) => fmtDate(p.delivery_date) },
    { key: "qty", label: "Quantité", get: (p) => p.qty, num: true, render: (p) => <><b>{fmtQty(p.qty, p.unit)}</b> <span className="subtle">{p.unit}</span><span className="sub">MOQ {fmtQty(p.moq, p.unit)} · UM {fmtQty(p.pack_qty, p.unit)}</span></> },
    { key: "net", label: "Besoin net", get: (p) => p.net_requirement, num: true, render: (p) => fmtQty(p.net_requirement, p.unit) },
    { key: "stock", label: "Stock avant → après", get: (p) => p.projected_stock_before, num: true, render: (p) => <>{fmtQty(p.projected_stock_before, p.unit)} → {fmtQty(p.projected_stock_after, p.unit)}</> },
    { key: "reason", label: "Motif", get: (p) => p.reason, render: (p) => <span className="small subtle" style={{ whiteSpace: "normal", minWidth: 220, maxWidth: 320, display: "block" }} title={p.reason}>{p.reason.length > 110 ? `${p.reason.slice(0, 110)}…` : p.reason}</span> },
  ], []);
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const urgent = (q.data ?? []).filter((p) => p.urgent).length;
  const suppliers = new Set((q.data ?? []).map((p) => p.supplier_id));
  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Propositions CBN</h1><p>Besoins nets calculés sur le Scenario Plan (MOQ, conditionnement, délai, jours de livraison, quotas), une proposition par fournisseur et par jour de livraison. Pour en reprendre une : taper la quantité dans la ligne Plan du tableau, la cellule grisée la prérempli.</p></div>
        <div className="actions"><a className="btn" href={api.downloadUrl("/api/exports/plan.xlsx", { planner: engineParams.planner })}><Download />Plan (xlsx)</a></div>
      </div>
      <div className="grid kpis">
        <Kpi label="Propositions" value={q.data ? fmtInt(q.data.length) : "…"} tone="brand" meta={q.data ? `${fmtQty(q.data.reduce((s, p) => s + p.qty, 0))} unités` : ""} />
        <Kpi label="Urgentes" value={q.data ? fmtInt(urgent) : "…"} tone={urgent ? "critical" : "ok"} meta="délai fournisseur non tenable" />
        <Kpi label="Fournisseurs concernés" value={suppliers.size} />
      </div>
      <Card flush>
        {q.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div> : (
          <div className="scroll-x"><DataTable rows={q.data ?? []} columns={cols} rowKey={(p) => p.proposal_id} emptyTitle="Aucune proposition" emptyHint="Le Scenario Plan couvre les besoins sur l'horizon." /></div>
        )}
      </Card>
    </div>
  );
}
