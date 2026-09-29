import { useMemo, useState } from "react";
import { Download, Search } from "lucide-react";
import { useAdjustments, useGrid, usePlanCells, usePrograms, useRefRows } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { api } from "@/lib/api";
import { Card, Empty, ErrorBox, Segmented, SkeletonBlock } from "@/components/ui";
import { SimulationGrid } from "@/components/SimulationGrid";

/** Supply table: the simulation grid of every article of the perimeter, one below the other, with filters. */
export default function SupplyTablePage() {
  const { perimeter, set, engineParams } = usePerimeter();
  const [program, setProgram] = useState("");
  const [supplier, setSupplier] = useState("");
  const [search, setSearch] = useState("");
  const programs = usePrograms();
  const suppliers = useRefRows("ref_suppliers");
  const grid = useGrid({ program_id: program || undefined, supplier_id: supplier || undefined });
  const planCells = usePlanCells();
  const adjustments = useAdjustments();
  const s = search.trim().toLowerCase();
  const articles = useMemo(() => (grid.data?.articles ?? []).filter((a) => !s || `${a.article.article_id} ${a.article.designation}`.toLowerCase().includes(s)), [grid.data, s]);
  const exportUrl = api.downloadUrl("/api/exports/simulation.xlsx", { planner: engineParams.planner, granularity: perimeter.granularity === "week" ? "week" : "day", horizon_days: engineParams.horizon_days, article_ids: articles.map((a) => a.article.article_id) });

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
            {(suppliers.data ?? []).map((x) => <option key={String(x.supplier_id)} value={String(x.supplier_id)}>{String(x.supplier_id)} · {String(x.name)}</option>)}
          </select>
          <div className="search"><Search /><input className="input sm" placeholder="Article, désignation…" value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: 240 }} aria-label="Article" /></div>
          <span className="small subtle">{grid.data ? `${articles.length} article(s) · ${grid.data.periods.length} colonnes` : ""}</span>
        </div>
      </Card>

      {grid.isError ? <ErrorBox error={grid.error} retry={() => grid.refetch()} /> : grid.isLoading || !grid.data ? <Card><SkeletonBlock rows={12} /></Card> : articles.length === 0 ? <Empty title="Aucun article" hint="Modifiez les filtres, ou chargez le référentiel (page Référentiel)." /> : (
        <Card flush tight>
          <SimulationGrid cols={grid.data} articles={articles} planCells={planCells.data ?? []} adjustments={adjustments.data ?? []} showArticleRows onSwitchDay={() => set({ granularity: "day" })} />
        </Card>
      )}
    </div>
  );
}
