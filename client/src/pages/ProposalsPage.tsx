import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Calculator, Download, Trash2 } from "lucide-react";
import { useCells, useCockpit, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { api } from "@/lib/api";
import { Badge, Button, Card, Empty, ErrorBox, Kpi, SkeletonBlock, useToast } from "@/components/ui";
import { fmtDate, fmtDateTime, fmtInt, fmtQty } from "@/lib/format";
import type { CbnReport } from "@/lib/types";

/**
 * Net requirement run ("Calcul CBN") for the perimeter and the resulting simulated orders.
 * A proposal and a typed simulated order are the same thing: one editable cell of the grid.
 */
export default function ProposalsPage() {
  const { engineParams, perimeter } = usePerimeter();
  const cockpit = useCockpit();
  const cells = useCells({ kind: "sim_order" });
  const toast = useToast();
  const [reset, setReset] = useState(true);
  const [report, setReport] = useState<CbnReport | null>(null);
  const [onlyCbn, setOnlyCbn] = useState(false);
  const cbn = useWrite(() => api.post<CbnReport>("/api/cbn/run", { planner: engineParams.planner, scenario_id: engineParams.scenario_id, reset }),
    (r) => { setReport(r); toast.push(`Calcul CBN terminé : ${r.proposals} commande(s) simulée(s) (${fmtQty(r.qty)}), ${r.urgent} urgente(s), ${r.removed} cellule(s) précédente(s) remplacée(s)`, r.urgent ? "info" : "success"); });
  const delCell = useWrite((id: string) => api.del(`/api/entries/cells/${id}`), () => toast.push("Commande simulée supprimée"));
  const clearCbn = useWrite(async (ids: string[]) => { await Promise.all(ids.map((id) => api.del(`/api/entries/cells/${id}`))); }, () => { setReport(null); toast.push("Résultats du CBN effacés"); });

  const perimeterIds = useMemo(() => new Set((cockpit.data?.articles ?? []).map((a) => a.article_id)), [cockpit.data]);
  const info = useMemo(() => Object.fromEntries((cockpit.data?.articles ?? []).map((a) => [a.article_id, a])), [cockpit.data]);
  const rows = useMemo(() => (cells.data ?? []).filter((c) => (perimeterIds.size === 0 || perimeterIds.has(c.article_id)) && (!onlyCbn || c.source === "CBN")), [cells.data, perimeterIds, onlyCbn]);
  const totals = useMemo(() => ({ qty: rows.reduce((s, c) => s + c.qty, 0), cbn: rows.filter((c) => c.source === "CBN").length, urgent: rows.filter((c) => c.note.includes("URGENT")).length, articles: new Set(rows.map((c) => c.article_id)).size }), [rows]);

  if (cells.isError) return <ErrorBox error={cells.error} retry={() => cells.refetch()} />;
  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Calcul CBN & commandes simulées</h1><p>Le Calcul CBN (calcul des besoins nets) recalcule les propositions du périmètre — MOQ, conditionnement, délai, jours de livraison, quotas — et les écrit dans la ligne « Commandes simulées » du tableau de chaque article. Une proposition et une commande simulée saisie à la main sont la même chose : une cellule modifiable, sans décision à prendre. Les cellules saisies à la main sont conservées et prises en compte par le calcul.</p></div>
        <div className="actions">
          <label className="checkbox" title="Remplacer les cellules écrites par le précédent Calcul CBN (les saisies manuelles sont toujours conservées)"><input type="checkbox" checked={reset} onChange={(e) => setReset(e.target.checked)} />remplacer le CBN précédent</label>
          <a className="btn" href={api.downloadUrl("/api/exports/orders.xlsx", { planner: engineParams.planner, scenario_id: engineParams.scenario_id })}><Download />Carnet (xlsx)</a>
          <Button variant="primary" disabled={cbn.isPending} onClick={() => cbn.mutate(undefined)}><Calculator />{cbn.isPending ? "Calcul en cours…" : `Calcul CBN${perimeter.planner ? ` · ${perimeter.planner}` : ""}`}</Button>
        </div>
      </div>

      <div className="grid kpis">
        <Kpi label="Commandes simulées" value={cells.data ? fmtInt(rows.length) : "…"} tone="brand" meta={`${totals.articles} article(s) · ${fmtQty(totals.qty)} unités`} />
        <Kpi label="Issues du CBN" value={cells.data ? fmtInt(totals.cbn) : "…"} meta={<label className="checkbox"><input type="checkbox" checked={onlyCbn} onChange={(e) => setOnlyCbn(e.target.checked)} />n'afficher que celles-ci</label>} />
        <Kpi label="Urgentes" value={cells.data ? fmtInt(totals.urgent) : "…"} tone={totals.urgent ? "critical" : "ok"} meta="délai fournisseur non tenable (dernier calcul)" />
        <Kpi label="Dernier calcul" value={report ? fmtInt(report.proposals) : "–"} meta={report ? `${report.articles} articles · ${fmtQty(report.qty)} · ${report.removed} remplacée(s)` : "aucun calcul dans cette session"} />
      </div>

      {report && report.items.length > 0 && (
        <Card title="Résultat du dernier Calcul CBN" hint="détail des besoins nets écrits en commandes simulées (urgentes en premier)" actions={<Button size="sm" variant="ghost" onClick={() => setReport(null)}>Masquer</Button>}>
          <div className="scroll-x" style={{ maxHeight: 320 }}>
            <table className="tbl compact">
              <thead><tr><th>Article</th><th>Fournisseur</th><th>Commander le</th><th>Livraison</th><th className="num">Quantité</th><th className="num">Besoin net</th><th className="num">Stock avant → après</th><th>Motif</th></tr></thead>
              <tbody>{report.items.map((p) => (
                <tr key={p.proposal_id}>
                  <td><Link to={`/articles/${encodeURIComponent(p.article_id)}`}><b>{p.article_id}</b></Link><span className="sub">{p.designation}</span></td>
                  <td>{p.supplier_id}<span className="sub">{p.supplier_name} · délai {p.lead_time_days} j</span></td>
                  <td>{fmtDate(p.order_date)}{p.urgent && <span className="sub"><Badge tone="critical">urgent</Badge></span>}</td>
                  <td>{fmtDate(p.delivery_date)}</td>
                  <td className="num"><b>{fmtQty(p.qty, p.unit)}</b><span className="sub">MOQ {fmtQty(p.moq, p.unit)} · PLA {fmtQty(p.pack_qty, p.unit)}</span></td>
                  <td className="num">{fmtQty(p.net_requirement, p.unit)}</td>
                  <td className="num">{fmtQty(p.projected_stock_before, p.unit)} → {fmtQty(p.projected_stock_after, p.unit)}</td>
                  <td className="small subtle" style={{ whiteSpace: "normal", minWidth: 220, maxWidth: 360 }}>{p.reason}</td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        </Card>
      )}

      <Card flush title="Commandes simulées du périmètre" hint="modifiables dans le tableau de chaque fiche article ; supprimer une ligne efface la cellule"
        actions={totals.cbn > 0 ? <Button size="sm" onClick={() => { if (window.confirm("Effacer toutes les commandes simulées issues du CBN (les saisies manuelles sont conservées) ?")) clearCbn.mutate(rows.filter((c) => c.source === "CBN").map((c) => c.id)); }}><Trash2 />Effacer le CBN</Button> : undefined}>
        {cells.isLoading || cockpit.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div> : rows.length === 0 ? <Empty title="Aucune commande simulée" hint="Lancez le Calcul CBN ou saisissez des quantités dans le tableau de simulation d'un article." /> : (
          <div className="scroll-x">
            <table className="tbl">
              <thead><tr><th>Article</th><th>Date de livraison</th><th className="num">Quantité</th><th>Saisie</th><th>Origine</th><th>Note</th><th>Modifié</th><th></th></tr></thead>
              <tbody>{rows.map((c) => (
                <tr key={c.id}>
                  <td><Link to={`/articles/${encodeURIComponent(c.article_id)}`}><b>{c.article_id}</b></Link><span className="sub">{info[c.article_id]?.designation ?? ""}</span></td>
                  <td>{fmtDate(c.date)}</td>
                  <td className={`num ${c.qty < 0 ? "delta down" : ""}`}><b>{fmtQty(c.qty, info[c.article_id]?.unit)}</b> <span className="subtle">{info[c.article_id]?.unit ?? ""}</span></td>
                  <td className="mono small">{c.expression}</td>
                  <td><Badge tone={c.source === "CBN" ? "brand" : "outline"}>{c.source}</Badge>{c.note.includes("URGENT") && <span className="sub"><Badge tone="critical">urgent</Badge></span>}</td>
                  <td className="small subtle" style={{ whiteSpace: "normal", minWidth: 220, maxWidth: 420 }} title={c.note}>{c.note.length > 140 ? `${c.note.slice(0, 140)}…` : c.note}</td>
                  <td className="subtle small">{c.updated_by}<br />{fmtDateTime(c.updated_at)}</td>
                  <td><Button size="sm" variant="ghost" title="Supprimer" onClick={() => delCell.mutate(c.id)}><Trash2 /></Button></td>
                </tr>
              ))}</tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
