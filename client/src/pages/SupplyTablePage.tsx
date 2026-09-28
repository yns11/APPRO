import { useMemo, useState } from "react";
import { Download, Search } from "lucide-react";
import { useCells, useCockpit, useGrid, usePrograms, useSuppliers } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { api } from "@/lib/api";
import { Card, Empty, ErrorBox, Segmented, SkeletonBlock } from "@/components/ui";
import { EntryDrawer, type EntryDraft } from "@/components/EntryDrawer";
import { SimulationGrid } from "@/components/SimulationGrid";
import { PlanDrawer, type PlanTarget } from "@/components/PlanDrawer";

/** Supply table: the simulation grid of every article of the perimeter, one below the other, with filters. */
export default function SupplyTablePage() {
  const { perimeter, set, engineParams } = usePerimeter();
  const [program, setProgram] = useState("");
  const [supplier, setSupplier] = useState("");
  const [search, setSearch] = useState("");
  const [draft, setDraft] = useState<EntryDraft | null>(null);
  const [target, setTarget] = useState<PlanTarget | null>(null);
  const programs = usePrograms();
  const suppliers = useSuppliers();
  const cockpit = useCockpit();
  const grid = useGrid({ program_id: program || undefined, supplier_id: supplier || undefined });
  const cells = useCells();
  const s = search.trim().toLowerCase();
  const articles = useMemo(() => (grid.data?.articles ?? []).filter((a) => !s || `${a.article.article_id} ${a.article.designation}`.toLowerCase().includes(s)), [grid.data, s]);
  const liveTarget = useMemo(() => {
    if (!target) return null;
    const a = grid.data?.articles.find((x) => x.article.article_id === target.article_id);
    return a ? { ...target, lines: a.plan_lines, orders: a.orders } : target;
  }, [target, grid.data]);
  const exportUrl = api.downloadUrl("/api/exports/simulation.xlsx", { planner: engineParams.planner, scenario_id: engineParams.scenario_id, granularity: perimeter.granularity === "default" ? "day" : perimeter.granularity, horizon_days: engineParams.horizon_days, article_ids: articles.map((a) => a.article.article_id) });

  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Tableau d'approvisionnement</h1></div>
        <div className="actions">
          <Segmented size="sm" value={perimeter.granularity} onChange={(g) => set({ granularity: g })} options={[{ id: "default", label: "Par défaut" }, { id: "day", label: "Jour" }, { id: "week", label: "Semaine" }]} />
          <a className="btn" href={exportUrl}><Download />Excel</a>
        </div>
      </div>

      <Card tight>
        <div className="row" style={{ gap: 12, flexWrap: "wrap" }}>
          <select className="select sm" value={program} onChange={(e) => setProgram(e.target.value)} style={{ width: 260 }} aria-label="Programme">
            <option value="">Tous les programmes</option>
            {(programs.data ?? []).filter((p) => p.components > 0).map((p) => <option key={p.program_id} value={p.program_id}>{p.name} ({p.program_id})</option>)}
          </select>
          <select className="select sm" value={supplier} onChange={(e) => setSupplier(e.target.value)} style={{ width: 240 }} aria-label="Fournisseur">
            <option value="">Tous les fournisseurs</option>
            {(suppliers.data ?? []).map((x) => <option key={x.supplier_id} value={x.supplier_id}>{x.supplier_id} · {x.name}</option>)}
          </select>
          <div className="search"><Search /><input className="input sm" placeholder="Article, désignation…" value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: 240 }} aria-label="Article" /></div>
          <span className="small subtle">{grid.data ? `${articles.length} article(s) · ${grid.data.periods.length} colonnes` : ""}</span>
        </div>
      </Card>

      {grid.isError ? <ErrorBox error={grid.error} retry={() => grid.refetch()} /> : grid.isLoading || !grid.data ? <Card><SkeletonBlock rows={12} /></Card> : articles.length === 0 ? <Empty title="Aucun article" hint="Modifiez les filtres." /> : (
        <Card flush tight>
          <SimulationGrid cols={grid.data} articles={articles} cells={cells.data ?? []} onEntry={setDraft} onPlan={setTarget} showArticleRows />
        </Card>
      )}
      <EntryDrawer draft={draft} onClose={() => setDraft(null)} articles={(cockpit.data?.articles ?? []).map((a) => ({ article_id: a.article_id, designation: a.designation, unit: a.unit }))} />
      <PlanDrawer target={liveTarget} onClose={() => setTarget(null)} />
    </div>
  );
}
