import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useCockpit } from "@/lib/queries";
import { Badge, Card, ErrorBox, SeverityBadge, SkeletonBlock } from "@/components/ui";
import { DataTable, type Column } from "@/components/DataTable";
import { fmtDate, fmtQty } from "@/lib/format";
import type { ArticleSummary } from "@/lib/types";
import { CoverageCell } from "./CockpitPage";

const NEXT_TYPE: Record<string, string> = { ferme: "Ferme ERP", cbn: "CBN", saisie: "Saisie" };

/** Article picker (filterable list) – the detail is ArticlePage. */
export default function ArticlesPage() {
  const q = useCockpit();
  const nav = useNavigate();
  const cols = useMemo<Column<ArticleSummary>[]>(() => [
    { key: "article", label: "Article", get: (a) => `${a.article_id} ${a.designation}`, render: (a) => <><b>{a.article_id}</b><span className="sub">{a.designation}</span></> },
    { key: "unit", label: "Unité", get: (a) => a.unit, filter: "select" },
    { key: "suppliers", label: "Fournisseurs", get: (a) => a.suppliers.join(" / ") },
    { key: "planner", label: "Approvisionneur", get: (a) => a.planner, filter: "select" },
    { key: "severity", label: "Statut", get: (a) => a.severity ?? "ok", filter: "select", render: (a) => <SeverityBadge severity={a.severity} /> },
    { key: "stock", label: "Stock à date", get: (a) => a.kpis.stock_at_date, num: true, title: "Stock projeté à la fin de la veille", render: (a) => fmtQty(a.kpis.stock_at_date, a.unit) },
    { key: "cov", label: "Couverture Appro.", get: (a) => a.kpis.coverage_plan_days, num: true, render: (a) => <CoverageCell days={a.kpis.coverage_plan_days} a={a} /> },
    { key: "backlog", label: "Backlog", get: (a) => a.kpis.backlog_qty, num: true, render: (a) => a.kpis.backlog_qty ? fmtQty(a.kpis.backlog_qty, a.unit) : <span className="subtle">–</span> },
    { key: "next_qty", label: "Prochaine livraison", get: (a) => a.kpis.next_delivery_qty, num: true, title: "Prochaine quantité attendue (ligne Appro. et complément CBN, tous fournisseurs)", render: (a) => a.kpis.next_delivery_qty !== null ? fmtQty(a.kpis.next_delivery_qty, a.unit) : <span className="subtle">–</span> },
    { key: "next_date", label: "Attendue le", get: (a) => a.kpis.next_delivery_date ?? "", render: (a) => a.kpis.next_delivery_date ? fmtDate(a.kpis.next_delivery_date) : <span className="subtle">–</span> },
    { key: "next_type", label: "Nature", get: (a) => a.kpis.next_delivery_type ? NEXT_TYPE[a.kpis.next_delivery_type] : "", filter: "select", render: (a) => a.kpis.next_delivery_type ? <Badge tone={a.kpis.next_delivery_type === "cbn" ? "info" : a.kpis.next_delivery_type === "saisie" ? "brand" : "ok"}>{NEXT_TYPE[a.kpis.next_delivery_type]}</Badge> : <span className="subtle">–</span> },
  ], []);
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  return (
    <div className="page">
      <div className="page-header"><div className="title"><h1>Fiches articles</h1></div></div>
      <Card flush>
        {q.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div> : (
          <DataTable rows={[...(q.data?.articles ?? [])].sort((a, b) => a.article_id.localeCompare(b.article_id))} columns={cols} rowKey={(a) => a.article_id} onRowClick={(a) => nav(`/articles/${encodeURIComponent(a.article_id)}`)} emptyTitle="Aucun article" maxHeight="calc(100vh - 190px)" />
        )}
      </Card>
    </div>
  );
}
