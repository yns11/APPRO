import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, CalendarX, Package } from "lucide-react";
import { useProgramImpact } from "@/lib/queries";
import { Badge, Card, Empty, ErrorBox, Kpi, Segmented, SkeletonBlock } from "@/components/ui";
import { fmtInt, fmtPct, fmtQty } from "@/lib/format";
import type { ImpactLayer } from "@/lib/types";

const LAYERS: { id: ImpactLayer; label: string; hint: string }[] = [
  { id: "plan", label: "Stock projeté", hint: "stock + quantités à retenir + complément CBN : ce que le plan de l'approvisionneur permet" },
  { id: "erp", label: "Projeté Ferme", hint: "stock + ordres fermes ERP seuls : ce qui est déjà commandé" },
  { id: "onhand", label: "Stock à date", hint: "stock de référence seul, rien n'arrive" },
];
const HORIZONS = [8, 13, 26, 52];
const wk = (w: string) => w.replace(/^\d{4}-W/, "S");

/**
 * « Impact programmes » : can the plant build what the PDP says, given the component stocks ?  One question, one
 * layer at a time (Stock projeté by default), a short horizon by default : the programmes at risk first (first
 * impacted week, worst week, the component to blame), the week grid only where the plan is not fully feasible, and
 * the limiting components aggregated so the planner knows which article to work on.
 */
export function ProgramsImpact() {
  const q = useProgramImpact();
  const [layer, setLayer] = useState<ImpactLayer>("plan");
  const [horizon, setHorizon] = useState(13);
  const [onlyImpacted, setOnlyImpacted] = useState(true);
  const d = q.data;
  const weeks = useMemo(() => (d?.weeks ?? []).slice(0, horizon), [d, horizon]);

  const rows = useMemo(() => {
    if (!d) return [];
    return d.programs.map((p) => {
      let worst = 1, worstWeek: string | null = null, first: string | null = null, planned = 0, feasible = 0;
      weeks.forEach((w, i) => {
        const pl = p.planned[i] ?? 0, fe = p.feasible[layer][i] ?? 0;
        planned += pl; feasible += fe;
        if (pl <= 0) return;
        const r = fe / pl;
        if (r < 0.999 && first === null) first = w;
        if (r < worst) { worst = r; worstWeek = w; }
      });
      const blame = new Map<string, number>();
      weeks.forEach((_w, i) => (p.limiting[layer][i] ?? []).forEach((l) => blame.set(l.article_id, Math.min(blame.get(l.article_id) ?? 1, l.share))));
      const limiting = [...blame.entries()].sort((a, b) => a[1] - b[1]).map(([article_id, share]) => ({ article_id, share }));
      return { p, first, worst, worstWeek, planned, feasible, limiting };
    }).sort((a, b) => (a.first ? 0 : 1) - (b.first ? 0 : 1) || (a.first ?? "").localeCompare(b.first ?? "") || a.worst - b.worst || a.p.name.localeCompare(b.p.name));
  }, [d, weeks, layer]);
  const impacted = rows.filter((r) => r.first);
  const shown = onlyImpacted ? impacted : rows;
  const components = useMemo(() => {
    const m = new Map<string, { programs: Set<string>; first: string; worst: number }>();
    impacted.forEach((r) => { const first = r.first ?? ""; r.limiting.forEach((l) => {
      const cur = m.get(l.article_id) ?? { programs: new Set<string>(), first, worst: 1 };
      cur.programs.add(r.p.name); if (first < cur.first) cur.first = first; cur.worst = Math.min(cur.worst, l.share);
      m.set(l.article_id, cur);
    }); });
    return [...m.entries()].map(([article_id, v]) => ({ article_id, ...v })).sort((a, b) => a.first.localeCompare(b.first) || a.worst - b.worst);
  }, [impacted]);
  const plannedAll = rows.reduce((s, r) => s + r.planned, 0), feasibleAll = rows.reduce((s, r) => s + r.feasible, 0);
  const firstWeek = impacted[0]?.first ?? null;
  const current = LAYERS.find((l) => l.id === layer)!;

  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  return (
    <div className="stack">
      <div className="row wrap" style={{ justifyContent: "space-between", gap: 12 }}>
        <p className="subtle small" style={{ margin: 0, maxWidth: 720 }}>Ce que le PDP permet de produire avec les stocks de composants, semaine par semaine, au prorata du composant le plus contraint. Couche <b>{current.label}</b> : {current.hint}.</p>
        <div className="row wrap" style={{ gap: 8 }}>
          <Segmented value={layer} onChange={setLayer} options={LAYERS.map((l) => ({ id: l.id, label: l.label }))} />
          <select className="select sm" value={horizon} onChange={(e) => setHorizon(Number(e.target.value))} aria-label="Horizon" style={{ width: "auto" }}>{HORIZONS.map((h) => <option key={h} value={h}>{h} semaines</option>)}</select>
        </div>
      </div>

      <div className="grid kpis">
        <Kpi label="Programmes à risque" icon={<AlertTriangle size={14} />} value={d ? fmtInt(impacted.length) : "…"} meta={d ? `sur ${d.programs.length} planifié(s), ${horizon} semaines` : ""} tone={impacted.length ? (layer === "plan" ? "critical" : "warning") : "ok"} />
        <Kpi label="Premier impact" icon={<CalendarX size={14} />} value={d ? (firstWeek ? wk(firstWeek) : "aucun") : "…"} meta={firstWeek ? impacted.filter((r) => r.first === firstWeek).map((r) => r.p.name).slice(0, 3).join(", ") : "tout le plan est réalisable"} tone={firstWeek ? "warning" : "ok"} />
        <Kpi label="Plan réalisable" value={d ? fmtPct(plannedAll ? feasibleAll / plannedAll : 1) : "…"} meta="production réalisable / planifiée sur l'horizon" tone={plannedAll && feasibleAll / plannedAll < 0.999 ? "warning" : "ok"} />
        <Kpi label="Composants limitants" icon={<Package size={14} />} value={d ? fmtInt(components.length) : "…"} meta={components[0] ? `d'abord ${components[0].article_id}` : "aucun"} tone={components.length ? "brand" : "ok"} />
      </div>

      <Card flush title={`Programmes · ${current.label}`} hint="% du PDP hebdomadaire réalisable, affiché seulement quand il manque quelque chose (· = rien de planifié, ✓ = 100 %) ; survoler une cellule pour le détail"
        actions={<label className="row small" style={{ gap: 6 }}><input type="checkbox" checked={onlyImpacted} onChange={(e) => setOnlyImpacted(e.target.checked)} />programmes à risque seulement</label>}>
        {q.isLoading || !d ? <div style={{ padding: 20 }}><SkeletonBlock rows={6} /></div> : d.programs.length === 0 ? <Empty title="Aucun programme planifié sur l'horizon" />
          : shown.length === 0 ? <Empty title="Aucun programme à risque" hint={`Les stocks de composants (${current.label.toLowerCase()}) couvrent le PDP des ${horizon} prochaines semaines.`} /> : (
          <div className="pivot impact">
            <table>
              <thead><tr><th>Programme</th><th>1er impact</th><th>Pire semaine</th><th>Composant à traiter</th>{weeks.map((w) => <th key={w} className="wk" title={w}>{wk(w)}</th>)}</tr></thead>
              <tbody>
                {shown.map(({ p, first, worst, worstWeek, limiting }) => (
                  <tr key={p.program_id} className={first ? "" : "muted"}>
                    <td><b>{p.name}</b><span className="sub mono">{p.program_id} · {p.components} composant(s)</span></td>
                    <td>{first ? <Badge tone={layer === "plan" ? "critical" : "warning"}>{wk(first)}</Badge> : <Badge tone="ok">aucun</Badge>}</td>
                    <td>{worstWeek ? <span className="small"><b>{fmtPct(worst)}</b> <span className="subtle">{wk(worstWeek)}</span></span> : <span className="subtle">–</span>}</td>
                    <td className="small">{limiting.length ? <><Link to={`/articles/${encodeURIComponent(limiting[0].article_id)}`}>{limiting[0].article_id}</Link>{limiting.length > 1 && <span className="subtle" title={limiting.slice(1).map((l) => l.article_id).join(", ")}> +{limiting.length - 1}</span>}</> : <span className="subtle">–</span>}</td>
                    {weeks.map((w, i) => {
                      const planned = p.planned[i] ?? 0, feasible = p.feasible[layer][i] ?? 0;
                      const ratio = planned > 0 ? feasible / planned : 1;
                      const lim = p.limiting[layer][i] ?? [];
                      const cls = planned === 0 ? "zero" : ratio >= 0.999 ? "full" : ratio >= 0.8 ? "warn" : "bad";
                      const title = planned === 0 ? "pas de production planifiée" : `${wk(w)} · PDP ${fmtQty(planned)} · réalisable ${fmtQty(feasible)} (${fmtPct(ratio)})${lim.length ? `\nlimitant : ${lim.map((l) => `${l.article_id} (${fmtPct(l.share)})`).join(", ")}` : ""}`;
                      return <td key={w} className={`pct ${cls}`} title={title}>{planned === 0 ? "·" : ratio >= 0.999 ? "✓" : fmtPct(ratio)}</td>;
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {components.length > 0 && (
        <Card title="Composants limitants" hint="les articles qui bloquent un programme sur l'horizon : à traiter dans l'ordre du premier impact" tight>
          <table className="tbl compact">
            <thead><tr><th>Article</th><th>1er impact</th><th className="num">Part servie au pire</th><th>Programmes touchés</th></tr></thead>
            <tbody>{components.map((c) => <tr key={c.article_id}><td><Link to={`/articles/${encodeURIComponent(c.article_id)}`}><b>{c.article_id}</b></Link></td><td><Badge tone={layer === "plan" ? "critical" : "warning"}>{wk(c.first)}</Badge></td><td className="num">{fmtPct(c.worst)}</td><td className="small">{[...c.programs].join(", ")}</td></tr>)}</tbody>
          </table>
        </Card>
      )}
    </div>
  );
}
