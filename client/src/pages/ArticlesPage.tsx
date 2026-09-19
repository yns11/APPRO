import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Search } from "lucide-react";
import { useCockpit } from "@/lib/queries";
import { Card, Empty, ErrorBox, SeverityBadge, SkeletonBlock, Sparkline } from "@/components/ui";
import { fmtDate, fmtQty } from "@/lib/format";
import { CoverageCell } from "./CockpitPage";

/** Article picker (list) – the detail is ArticlePage. */
export default function ArticlesPage() {
  const q = useCockpit();
  const nav = useNavigate();
  const [search, setSearch] = useState("");
  const rows = useMemo(() => {
    const s = search.trim().toLowerCase();
    return (q.data?.articles ?? []).filter((a) => !s || `${a.article_id} ${a.designation} ${a.suppliers.join(" ")}`.toLowerCase().includes(s))
      .sort((a, b) => a.article_id.localeCompare(b.article_id));
  }, [q.data, search]);
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Fiches articles</h1><p>Choisissez un article pour ouvrir sa projection détaillée (tableau, courbes, saisies, propositions).</p></div>
        <div className="actions"><div className="search"><Search /><input className="input" placeholder="Rechercher…" value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: 300 }} autoFocus /></div></div>
      </div>
      <Card flush>
        {q.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div> : rows.length === 0 ? <Empty title="Aucun article" /> : (
          <table className="tbl">
            <thead><tr><th>Article</th><th>Unité</th><th>Fournisseurs</th><th>Statut</th><th className="num">Stock à date</th><th className="num">Couverture</th><th>Snapshot stock</th><th>Projection</th></tr></thead>
            <tbody>
              {rows.map((a) => (
                <tr key={a.article_id} className="clickable" onClick={() => nav(`/articles/${encodeURIComponent(a.article_id)}`)}>
                  <td><b>{a.article_id}</b><span className="sub">{a.designation}</span></td>
                  <td className="subtle">{a.unit}</td>
                  <td className="subtle">{a.suppliers.join(" / ")}</td>
                  <td><SeverityBadge severity={a.severity} /></td>
                  <td className="num">{fmtQty(a.kpis.stock_as_of_sim, a.unit)}</td>
                  <td className="num"><CoverageCell days={a.kpis.coverage_sim_days} a={a} /></td>
                  <td className="subtle">{fmtDate(a.kpis.snapshot_date)}</td>
                  <td><Sparkline values={a.sparkline} /></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
