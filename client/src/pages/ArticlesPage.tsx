import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useCockpit } from "@/lib/queries";
import { Card, ErrorBox, SeverityBadge, SkeletonBlock, Sparkline } from "@/components/ui";
import { DataTable, type Column } from "@/components/DataTable";
import { fmtDate, fmtQty } from "@/lib/format";
import type { ArticleSummary } from "@/lib/types";
import { CoverageCell } from "./CockpitPage";

/** Article picker (filterable list) – the detail is ArticlePage. */
export default function ArticlesPage() {
  const q = useCockpit();
  const nav = useNavigate();
  const cols = useMemo<Column<ArticleSummary>[]>(() => [
    { key: "article", label: "Article", get: (a) => `${a.article_id} ${a.designation}`, render: (a) => <><b>{a.article_id}</b><span className="sub">{a.designation}</span></> },
    { key: "unit", label: "Unité", get: (a) => a.unit, filter: "select" },
    { key: "planner", label: "Appro", get: (a) => a.planner, filter: "select" },
    { key: "suppliers", label: "Fournisseurs", get: (a) => a.suppliers.join(" / ") },
    { key: "severity", label: "Statut", get: (a) => a.severity ?? "ok", filter: "select", render: (a) => <SeverityBadge severity={a.severity} /> },
    { key: "stock", label: "Stock référence", get: (a) => a.kpis.stock_reference, num: true, render: (a) => fmtQty(a.kpis.stock_reference, a.unit) },
    { key: "cov", label: "Couverture plan", get: (a) => a.kpis.coverage_plan_days, num: true, render: (a) => <CoverageCell days={a.kpis.coverage_plan_days} a={a} /> },
    { key: "backlog", label: "Backlog", get: (a) => a.kpis.backlog_qty, num: true, render: (a) => a.kpis.backlog_qty ? fmtQty(a.kpis.backlog_qty, a.unit) : <span className="subtle">–</span> },
    { key: "snap", label: "Snapshot", get: (a) => a.kpis.snapshot_date, render: (a) => <span className="subtle">{fmtDate(a.kpis.snapshot_date)}</span> },
    { key: "spark", label: "Scenario Plan", get: () => "", filter: "none", sortable: false, render: (a) => <Sparkline values={a.sparkline} /> },
  ], []);
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  return (
    <div className="page">
      <div className="page-header"><div className="title"><h1>Fiches articles</h1></div></div>
      <Card flush>
        {q.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div> : (
          <DataTable rows={[...(q.data?.articles ?? [])].sort((a, b) => a.article_id.localeCompare(b.article_id))} columns={cols} rowKey={(a) => a.article_id} onRowClick={(a) => nav(`/articles/${encodeURIComponent(a.article_id)}`)} emptyTitle="Aucun article" />
        )}
      </Card>
    </div>
  );
}
