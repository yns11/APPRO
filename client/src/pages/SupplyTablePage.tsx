import { useEffect, useMemo, useState } from "react";
import { ChevronLeft, ChevronRight, Download, Search } from "lucide-react";
import { useAdjustments, useFlags, useGrid, usePlanCells, usePrograms, useRefRows } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { api } from "@/lib/api";
import { Card, Empty, ErrorBox, Segmented, SkeletonBlock } from "@/components/ui";
import { SimulationGrid } from "@/components/SimulationGrid";

const PAGE_SIZES = [10, 20, 50, 100];

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

/**
 * Supply table: the simulation grid of the articles of the perimeter, one below the other, with
 * filters.  The perimeter is computed once on the server and served **page by page** (20 articles
 * by default) ; inside a page the grid only renders the cells in view.
 */
export default function SupplyTablePage() {
  const { perimeter, set, engineParams } = usePerimeter();
  const [program, setProgram] = useState("");
  const [supplier, setSupplier] = useState("");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(() => { try { return Number(localStorage.getItem("appro.grid.pageSize")) || 20; } catch { return 20; } });
  const q = useDebounced(search.trim(), 300);
  useEffect(() => { setPage(1); }, [program, supplier, q, pageSize, perimeter.planner]);
  useEffect(() => { try { localStorage.setItem("appro.grid.pageSize", String(pageSize)); } catch { /* private mode */ } }, [pageSize]);
  const programs = usePrograms();
  const suppliers = useRefRows("ref_suppliers");
  const grid = useGrid({ program_id: program || undefined, supplier_id: supplier || undefined, q: q || undefined, page, page_size: pageSize });
  const planCells = usePlanCells();
  const adjustments = useAdjustments();
  const flags = useFlags();
  const articles = useMemo(() => grid.data?.articles ?? [], [grid.data]);
  const total = grid.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const exportUrl = api.downloadUrl("/api/exports/simulation.xlsx", { planner: engineParams.planner, granularity: perimeter.granularity === "week" ? "week" : "day", horizon_days: engineParams.horizon_days, article_ids: articles.map((a) => a.article.article_id) });

  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Tableau d'approvisionnement</h1></div>
        <div className="actions">
          <Segmented size="sm" value={perimeter.granularity} onChange={(g) => set({ granularity: g })} options={[{ id: "default", label: "Par défaut" }, { id: "day", label: "Jour" }, { id: "week", label: "Semaine" }]} />
          <a className="btn" href={exportUrl} title="Classeur à formules des articles de la page"><Download />Excel (page)</a>
        </div>
      </div>
      <Card tight>
        <div className="row wrap">
          <div className="search" style={{ minWidth: 260 }}><Search /><input className="input sm" placeholder="Article ou désignation…" value={search} onChange={(e) => setSearch(e.target.value)} /></div>
          <select className="select sm" style={{ width: 240 }} value={program} onChange={(e) => setProgram(e.target.value)}>
            <option value="">Tous les programmes</option>
            {(programs.data ?? []).map((p) => <option key={p.program_id} value={p.program_id}>{p.name || p.program_id}</option>)}
          </select>
          <select className="select sm" style={{ width: 240 }} value={supplier} onChange={(e) => setSupplier(e.target.value)}>
            <option value="">Tous les fournisseurs</option>
            {(suppliers.data ?? []).map((s) => <option key={String(s.supplier_id)} value={String(s.supplier_id)}>{String(s.supplier_id)} · {String(s.name ?? "")}</option>)}
          </select>
          <div className="pager" style={{ marginLeft: "auto" }}>
            <span>{total === 0 ? "Aucun article" : `Articles ${(page - 1) * pageSize + 1}–${Math.min(page * pageSize, total)} sur ${total}`}</span>
            <button className="btn xs" disabled={page <= 1} onClick={() => setPage((p) => p - 1)} aria-label="Page précédente"><ChevronLeft /></button>
            <span>{page} / {pages}</span>
            <button className="btn xs" disabled={page >= pages} onClick={() => setPage((p) => p + 1)} aria-label="Page suivante"><ChevronRight /></button>
            <select className="select xs" value={pageSize} onChange={(e) => setPageSize(Number(e.target.value))} aria-label="Articles par page">
              {PAGE_SIZES.map((s) => <option key={s} value={s}>{s} / page</option>)}
            </select>
            {grid.isFetching && <span className="subtle">calcul…</span>}
          </div>
        </div>
      </Card>
      {grid.isError ? <ErrorBox error={grid.error} retry={() => grid.refetch()} /> : grid.isLoading || !grid.data ? <Card><SkeletonBlock rows={10} /></Card>
        : articles.length === 0 ? <Card><Empty title="Aucun article" hint="Modifier les filtres ou le périmètre (approvisionneur) en haut de page." /></Card> : (
          <Card flush tight>
            <SimulationGrid cols={grid.data} articles={articles} planCells={planCells.data ?? []} adjustments={adjustments.data ?? []} flags={flags.data ?? []} showArticleRows
              onSwitchDay={() => set({ granularity: "day" })} />
          </Card>
        )}
    </div>
  );
}
