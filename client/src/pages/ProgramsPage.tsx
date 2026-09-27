import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useProgramImpact } from "@/lib/queries";
import { Badge, Card, Empty, ErrorBox, Kpi, Segmented, SkeletonBlock } from "@/components/ui";
import { fmtInt, fmtPct, fmtQty } from "@/lib/format";
import type { ImpactLayer } from "@/lib/types";

const LAYERS: { id: ImpactLayer; label: string; hint: string }[] = [
  { id: "onhand", label: "Stock à date", hint: "stock physique seul, rien n'arrive" },
  { id: "firm", label: "Ferme", hint: "stock + commandes fermes" },
  { id: "forecast", label: "Prévisionnel", hint: "+ commandes prévisionnelles ERP" },
  { id: "sim", label: "Simulé", hint: "+ réceptions simulées et complément CBN" },
];

/** Feasible production per programme and week, given the component stocks of each layer. */
export default function ProgramsPage() {
  const q = useProgramImpact();
  const [layer, setLayer] = useState<ImpactLayer>("firm");
  const d = q.data;
  const stats = useMemo(() => {
    if (!d) return null;
    const out = {} as Record<ImpactLayer, { impacted: number; planned: number; feasible: number }>;
    LAYERS.forEach(({ id }) => {
      out[id] = { impacted: d.programs.filter((p) => p.first_impact[id]).length, planned: d.programs.reduce((s, p) => s + p.planned.reduce((a, b) => a + b, 0), 0), feasible: d.programs.reduce((s, p) => s + p.feasible[id].reduce((a, b) => a + b, 0), 0) };
    });
    return out;
  }, [d]);

  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Impact sur les programmes</h1><p>Production réalisable par programme et par semaine compte tenu des stocks de composants : à date (rien n'arrive), ferme, prévisionnel et simulé. Un programme est réalisable au prorata du composant le plus contraint ; les composants limitants sont indiqués.</p></div>
        <div className="actions"><Segmented value={layer} onChange={setLayer} options={LAYERS.map((l) => ({ id: l.id, label: l.label }))} /></div>
      </div>

      <div className="grid kpis">
        {LAYERS.map((l) => (
          <Kpi key={l.id} label={`Programmes impactés · ${l.label}`} value={stats ? fmtInt(stats[l.id].impacted) : "…"} tone={stats && stats[l.id].impacted ? (l.id === "sim" ? "critical" : "warning") : "ok"}
            meta={stats ? `${fmtPct(stats[l.id].planned ? stats[l.id].feasible / stats[l.id].planned : 1)} du plan réalisable · ${l.hint}` : ""} onClick={() => setLayer(l.id)} active={layer === l.id} />
        ))}
      </div>

      <Card flush title={`Réalisable par semaine · ${LAYERS.find((l) => l.id === layer)?.label}`} hint="% du PDP hebdomadaire réalisable ; survoler pour les composants limitants ; vert ≥ 100 %, orange ≥ 80 %, rouge en dessous">
        {q.isLoading || !d ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div> : d.programs.length === 0 ? <Empty title="Aucun programme planifié sur l'horizon" /> : (
          <div className="pivot impact">
            <table>
              <thead><tr><th>Programme</th><th>1er impact</th>{d.weeks.map((w) => <th key={w} className="wk">{w.replace("-W", " S")}</th>)}</tr></thead>
              <tbody>
                {d.programs.map((p) => (
                  <tr key={p.program_id}>
                    <td><b>{p.name}</b><span className="sub mono">{p.program_id} · {p.components} composant(s)</span></td>
                    <td>{p.first_impact[layer] ? <Badge tone={layer === "sim" ? "critical" : "warning"}>{p.first_impact[layer].replace("-W", " S")}</Badge> : <Badge tone="ok">aucun</Badge>}</td>
                    {d.weeks.map((w, i) => {
                      const planned = p.planned[i];
                      const feasible = p.feasible[layer][i];
                      const ratio = planned > 0 ? feasible / planned : 1;
                      const lim = p.limiting[layer][i];
                      const cls = planned === 0 ? "zero" : ratio >= 0.999 ? "ok" : ratio >= 0.8 ? "warn" : "bad";
                      const title = planned === 0 ? "pas de production planifiée" : `PDP ${fmtQty(planned)} · réalisable ${fmtQty(feasible)}${lim.length ? `\nlimitant : ${lim.map((l) => `${l.article_id} (${fmtPct(l.share)})`).join(", ")}` : ""}`;
                      return <td key={w} className={`pct ${cls}`} title={title}>
                        {planned === 0 ? "·" : <>{fmtPct(ratio)}{lim.length > 0 && <span className="sub"><Link to={`/articles/${encodeURIComponent(lim[0].article_id)}`}>{lim[0].article_id}</Link>{lim.length > 1 ? ` +${lim.length - 1}` : ""}</span>}</>}
                      </td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
