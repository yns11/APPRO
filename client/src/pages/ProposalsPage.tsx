import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Check, CheckCheck, Download, EyeOff, RotateCcw } from "lucide-react";
import { useProposals, useWrite, useCockpit } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { api } from "@/lib/api";
import { Badge, Button, Card, Empty, ErrorBox, Kpi, SkeletonBlock, useToast } from "@/components/ui";
import { EntryDrawer, type EntryDraft } from "@/components/EntryDrawer";
import { fmtDate, fmtInt, fmtQty } from "@/lib/format";
import type { ProposalOut } from "@/lib/types";

/** Order workbench: accept / modify / ignore engine proposals, export the order book. */
export default function ProposalsPage() {
  const { engineParams } = usePerimeter();
  const [showIgnored, setShowIgnored] = useState(false);
  const q = useProposals(showIgnored);
  const cockpit = useCockpit();
  const toast = useToast();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [draft, setDraft] = useState<EntryDraft | null>(null);
  const [supplierFilter, setSupplierFilter] = useState("");
  const [onlyUrgent, setOnlyUrgent] = useState(false);

  const accept = useWrite((ps: ProposalOut[]) => api.post("/api/proposals/accept-batch", ps.map((p) => ({ article_id: p.article_id, supplier_id: p.supplier_id, delivery_date: p.delivery_date, qty: p.qty, proposal_id: p.proposal_id }))),
    () => { toast.push("Propositions acceptées → commandes planifiées", "success"); setSelected(new Set()); });
  const ignore = useWrite((p: ProposalOut) => api.post("/api/proposals/ignore", { article_id: p.article_id, supplier_id: p.supplier_id, delivery_date: p.delivery_date, reason: "ignorée depuis le plan de commandes" }), () => toast.push("Proposition ignorée"));
  const unignoreAll = useWrite(async () => { const ig = await api.get<{ id: string }[]>("/api/proposals/ignored"); await Promise.all(ig.map((r) => api.del(`/api/proposals/ignored/${r.id}`))); }, () => toast.push("Propositions ignorées réactivées"));

  const rows = useMemo(() => (q.data ?? []).filter((p) => (!supplierFilter || p.supplier_id === supplierFilter) && (!onlyUrgent || p.urgent)), [q.data, supplierFilter, onlyUrgent]);
  const bySupplier = useMemo(() => {
    const m = new Map<string, { name: string; count: number; qty: number; urgent: number }>();
    rows.forEach((p) => { const k = p.supplier_id ?? "?"; const cur = m.get(k) ?? { name: p.supplier_name, count: 0, qty: 0, urgent: 0 }; cur.count++; cur.qty += p.qty; if (p.urgent) cur.urgent++; m.set(k, cur); });
    return Array.from(m.entries()).sort((a, b) => b[1].count - a[1].count);
  }, [rows]);
  const unitOf = useMemo(() => Object.fromEntries((cockpit.data?.articles ?? []).map((a) => [a.article_id, { article_id: a.article_id, designation: a.designation, unit: a.unit }])), [cockpit.data]);

  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const toggle = (id: string) => setSelected((s) => { const n = new Set(s); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const visibleIds = rows.filter((p) => !p.ignored).map((p) => p.proposal_id);
  const allSelected = visibleIds.length > 0 && visibleIds.every((id) => selected.has(id));
  const selectedRows = rows.filter((p) => selected.has(p.proposal_id) && !p.ignored);
  const urgent = (q.data ?? []).filter((p) => p.urgent && !p.ignored).length;

  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Propositions de commandes</h1><p>Besoins nets calculés par le moteur (MOQ, conditionnement, délai, jours de livraison, quotas). Acceptez, modifiez ou ignorez chaque proposition ; une proposition acceptée devient une commande planifiée, à transmettre à l'ERP.</p></div>
        <div className="actions">
          <a className="btn" href={api.downloadUrl("/api/exports/orders.xlsx", { planner: engineParams.planner, scenario_id: engineParams.scenario_id })}><Download />Carnet + propositions (xlsx)</a>
          <Button variant="primary" disabled={selectedRows.length === 0 || accept.isPending} onClick={() => accept.mutate(selectedRows)}><CheckCheck />Accepter la sélection ({selectedRows.length})</Button>
        </div>
      </div>

      <div className="grid kpis">
        <Kpi label="Propositions" value={q.data ? fmtInt(q.data.filter((p) => !p.ignored).length) : "…"} tone="brand" meta="à traiter" />
        <Kpi label="Urgentes" value={q.data ? fmtInt(urgent) : "…"} tone={urgent ? "critical" : "ok"} meta="délai fournisseur non tenable" onClick={() => setOnlyUrgent((v) => !v)} active={onlyUrgent} />
        <Kpi label="Fournisseurs concernés" value={bySupplier.length} meta={bySupplier.slice(0, 3).map(([id, s]) => `${id}: ${s.count}`).join(" · ")} />
        <Kpi label="Ignorées" value={showIgnored ? (q.data ?? []).filter((p) => p.ignored).length : "masquées"} meta={<label className="checkbox"><input type="checkbox" checked={showIgnored} onChange={(e) => setShowIgnored(e.target.checked)} />afficher</label>} />
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
        <Card className="span-3" flush title="Plan de commandes" hint={supplierFilter ? `filtre : ${supplierFilter}` : "toutes les propositions, urgentes en premier"}
          actions={<>{supplierFilter && <Button size="sm" onClick={() => setSupplierFilter("")}>Tous les fournisseurs</Button>}{showIgnored && <Button size="sm" onClick={() => unignoreAll.mutate(undefined)}><RotateCcw />Réactiver les ignorées</Button>}</>}>
          {q.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div> : rows.length === 0 ? <Empty title="Aucune proposition" hint="Le stock simulé couvre les besoins sur l'horizon, ou les propositions sont désactivées dans les paramètres." /> : (
            <div className="scroll-x">
              <table className="tbl">
                <thead><tr>
                  <th><input type="checkbox" checked={allSelected} onChange={() => setSelected(allSelected ? new Set() : new Set(visibleIds))} aria-label="Tout sélectionner" /></th>
                  <th>Article</th><th>Fournisseur</th><th>Commander le</th><th>Livraison</th><th className="num">Quantité</th><th className="num">Besoin net</th><th className="num">Stock avant → après</th><th>Motif</th><th></th>
                </tr></thead>
                <tbody>
                  {rows.map((p) => (
                    <tr key={p.proposal_id} className={selected.has(p.proposal_id) ? "selected" : ""} style={p.ignored ? { opacity: 0.55 } : undefined}>
                      <td>{!p.ignored && <input type="checkbox" checked={selected.has(p.proposal_id)} onChange={() => toggle(p.proposal_id)} />}</td>
                      <td><Link to={`/articles/${encodeURIComponent(p.article_id)}`}><b>{p.article_id}</b></Link><span className="sub">{p.designation}</span></td>
                      <td>{p.supplier_id}<span className="sub">{p.supplier_name} · délai {p.lead_time_days} j</span></td>
                      <td>{fmtDate(p.order_date)}{p.urgent && <span className="sub"><Badge tone="critical">urgent</Badge></span>}</td>
                      <td>{fmtDate(p.delivery_date)}</td>
                      <td className="num"><b>{fmtQty(p.qty, p.unit)}</b> <span className="subtle">{p.unit}</span><span className="sub">MOQ {fmtQty(p.moq, p.unit)} · PLA {fmtQty(p.pack_qty, p.unit)}</span></td>
                      <td className="num">{fmtQty(p.net_requirement, p.unit)}</td>
                      <td className="num">{fmtQty(p.projected_stock_before, p.unit)} → {fmtQty(p.projected_stock_after, p.unit)}</td>
                      <td className="small subtle" style={{ whiteSpace: "normal", minWidth: 220, maxWidth: 320 }} title={p.reason}>{p.reason.length > 110 ? `${p.reason.slice(0, 110)}…` : p.reason}</td>
                      <td>{p.ignored ? <Badge tone="neutral">ignorée</Badge> : (
                        <div className="row">
                          <Button size="sm" variant="primary" title="Accepter" onClick={() => accept.mutate([p])}><Check /></Button>
                          <Button size="sm" onClick={() => setDraft({ kind: "order", article_id: p.article_id, supplier_id: p.supplier_id, date: p.delivery_date, qty: p.qty, note: `d'après ${p.proposal_id}` })}>Modifier</Button>
                          <Button size="sm" variant="ghost" title="Ignorer" onClick={() => ignore.mutate(p)}><EyeOff /></Button>
                        </div>
                      )}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
      </div>
      <EntryDrawer draft={draft} onClose={() => setDraft(null)} articles={Object.values(unitOf)} />
    </div>
  );
}
