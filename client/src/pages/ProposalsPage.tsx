import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Download } from "lucide-react";
import { useProposals } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { api } from "@/lib/api";
import { Badge, Card, Empty, ErrorBox, Kpi, SkeletonBlock } from "@/components/ui";
import { fmtDate, fmtInt, fmtQty } from "@/lib/format";

/** Net requirements ("Complément CBN") computed automatically on the simulated stock of the perimeter. */
export default function ProposalsPage() {
  const { engineParams } = usePerimeter();
  const q = useProposals();
  const [supplierFilter, setSupplierFilter] = useState("");
  const [onlyUrgent, setOnlyUrgent] = useState(false);
  const rows = useMemo(() => (q.data ?? []).filter((p) => (!supplierFilter || p.supplier_id === supplierFilter) && (!onlyUrgent || p.urgent)), [q.data, supplierFilter, onlyUrgent]);
  const bySupplier = useMemo(() => {
    const m = new Map<string, { name: string; count: number; qty: number; urgent: number }>();
    (q.data ?? []).forEach((p) => { const k = p.supplier_id ?? "?"; const cur = m.get(k) ?? { name: p.supplier_name, count: 0, qty: 0, urgent: 0 }; cur.count++; cur.qty += p.qty; if (p.urgent) cur.urgent++; m.set(k, cur); });
    return Array.from(m.entries()).sort((a, b) => b[1].count - a[1].count);
  }, [q.data]);
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const urgent = (q.data ?? []).filter((p) => p.urgent).length;
  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Complément CBN</h1><p>Besoins nets calculés automatiquement sur le stock simulé (réceptions simulées incluses) : MOQ, conditionnement, délai, jours de livraison, quotas. Ils complètent les flux ERP et vos réceptions simulées ; ils apparaissent dans la ligne « Complément CBN » du tableau de chaque article.</p></div>
        <div className="actions">
          <a className="btn" href={api.downloadUrl("/api/exports/orders.xlsx", { planner: engineParams.planner, scenario_id: engineParams.scenario_id })}><Download />Carnet (xlsx)</a>
        </div>
      </div>
      <div className="grid kpis">
        <Kpi label="Propositions" value={q.data ? fmtInt(q.data.length) : "…"} tone="brand" meta={q.data ? `${fmtQty(q.data.reduce((s, p) => s + p.qty, 0))} unités` : ""} />
        <Kpi label="Urgentes" value={q.data ? fmtInt(urgent) : "…"} tone={urgent ? "critical" : "ok"} meta="délai fournisseur non tenable" onClick={() => setOnlyUrgent((v) => !v)} active={onlyUrgent} />
        <Kpi label="Fournisseurs concernés" value={bySupplier.length} meta={bySupplier.slice(0, 3).map(([id, s]) => `${id}: ${s.count}`).join(" · ")} />
      </div>
      <div className="grid cols-4">
        <Card title="Par fournisseur" tight>
          {bySupplier.length === 0 ? <Empty title="Aucune proposition" /> : (
            <table className="tbl compact">
              <thead><tr><th>Fournisseur</th><th className="num">Nb</th><th className="num" title="urgentes">Urg.</th></tr></thead>
              <tbody>{bySupplier.map(([id, s]) => <tr key={id} className={`clickable ${supplierFilter === id ? "selected" : ""}`} onClick={() => setSupplierFilter(supplierFilter === id ? "" : id)}><td>{id}<span className="sub">{s.name}</span></td><td className="num">{s.count}</td><td className="num">{s.urgent ? <Badge tone="critical">{s.urgent}</Badge> : "–"}</td></tr>)}</tbody>
            </table>
          )}
        </Card>
        <Card className="span-3" flush title="Plan de commandes" hint={supplierFilter ? `filtre : ${supplierFilter}` : "urgentes en premier"}>
          {q.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div> : rows.length === 0 ? <Empty title="Aucune proposition" hint="Le stock simulé couvre les besoins sur l'horizon." /> : (
            <div className="scroll-x">
              <table className="tbl">
                <thead><tr><th>Article</th><th>Fournisseur</th><th>Commander le</th><th>Livraison</th><th className="num">Quantité</th><th className="num">Besoin net</th><th className="num">Stock avant → après</th><th>Motif</th></tr></thead>
                <tbody>
                  {rows.map((p) => (
                    <tr key={p.proposal_id}>
                      <td><Link to={`/articles/${encodeURIComponent(p.article_id)}`}><b>{p.article_id}</b></Link><span className="sub">{p.designation}</span></td>
                      <td>{p.supplier_id}<span className="sub">{p.supplier_name} · délai {p.lead_time_days} j</span></td>
                      <td>{fmtDate(p.order_date)}{p.urgent && <span className="sub"><Badge tone="critical">urgent</Badge></span>}</td>
                      <td>{fmtDate(p.delivery_date)}</td>
                      <td className="num"><b>{fmtQty(p.qty, p.unit)}</b> <span className="subtle">{p.unit}</span><span className="sub">MOQ {fmtQty(p.moq, p.unit)} · PLA {fmtQty(p.pack_qty, p.unit)}</span></td>
                      <td className="num">{fmtQty(p.net_requirement, p.unit)}</td>
                      <td className="num">{fmtQty(p.projected_stock_before, p.unit)} → {fmtQty(p.projected_stock_after, p.unit)}</td>
                      <td className="small subtle" style={{ whiteSpace: "normal", minWidth: 220, maxWidth: 320 }} title={p.reason}>{p.reason.length > 110 ? `${p.reason.slice(0, 110)}…` : p.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
    </div>
  );
}
