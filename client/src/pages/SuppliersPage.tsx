import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useCockpit, useRefRows } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { Badge, Card, ErrorBox, SkeletonBlock } from "@/components/ui";
import { DataTable, type Column } from "@/components/DataTable";
import { fmtInt, fmtWeekdays } from "@/lib/format";
import type { RefRow } from "@/lib/types";

interface Row { supplier_id: string; name: string; country: string; delivery_weekdays: string; lead_time_days: number; active: boolean; articles: number; critical: number; warning: number; backlog_articles: number; urgent: number; }

/** Supplier picker : one line per supplier of the perimeter, with the state of its articles ; the detail is SupplierPage. */
export default function SuppliersPage() {
  const { perimeter } = usePerimeter();
  const suppliers = useRefRows("ref_suppliers", perimeter.planner);
  const cockpit = useCockpit();
  const nav = useNavigate();
  const rows = useMemo<Row[]>(() => {
    const stats = new Map<string, { critical: number; warning: number; backlog: number; urgent: number; n: number }>();
    for (const a of cockpit.data?.articles ?? []) {
      for (const sid of a.suppliers) {
        const s = stats.get(sid) ?? { critical: 0, warning: 0, backlog: 0, urgent: 0, n: 0 };
        s.n += 1;
        if (a.severity === "critical") s.critical += 1;
        if (a.severity === "warning") s.warning += 1;
        if (a.kpis.backlog_qty > 0) s.backlog += 1;
        if (a.kpis.urgent_proposal_count > 0) s.urgent += 1;
        stats.set(sid, s);
      }
    }
    return (suppliers.data ?? []).map((r: RefRow) => {
      const s = stats.get(String(r.supplier_id));
      return { supplier_id: String(r.supplier_id), name: String(r.name ?? ""), country: String(r.country ?? ""), delivery_weekdays: String(r.delivery_weekdays ?? "1,2,3,4,5"),
        lead_time_days: Number(r.lead_time_days ?? 5), active: r.active !== false, articles: Number(r.article_count ?? s?.n ?? 0),
        critical: s?.critical ?? 0, warning: s?.warning ?? 0, backlog_articles: s?.backlog ?? 0, urgent: s?.urgent ?? 0 };
    });
  }, [suppliers.data, cockpit.data]);
  const cols = useMemo<Column<Row>[]>(() => [
    { key: "supplier_id", label: "Code", get: (r) => r.supplier_id, render: (r) => <b>{r.supplier_id}</b> },
    { key: "name", label: "Fournisseur", get: (r) => r.name },
    { key: "country", label: "Pays", get: (r) => r.country, filter: "select" },
    { key: "days", label: "Jours de livraison", get: (r) => fmtWeekdays(r.delivery_weekdays), filter: "select" },
    { key: "lead", label: "Délai (j ouvrés)", get: (r) => r.lead_time_days, num: true },
    { key: "articles", label: "Articles", get: (r) => r.articles, num: true, sum: true },
    { key: "critical", label: "Critiques", get: (r) => r.critical, num: true, sum: true, render: (r) => r.critical ? <Badge tone="critical">{fmtInt(r.critical)}</Badge> : <span className="subtle">–</span> },
    { key: "warning", label: "À surveiller", get: (r) => r.warning, num: true, sum: true, render: (r) => r.warning ? <Badge tone="warning">{fmtInt(r.warning)}</Badge> : <span className="subtle">–</span> },
    { key: "urgent", label: "Urgences CBN", get: (r) => r.urgent, num: true, sum: true, render: (r) => r.urgent ? <Badge tone="critical">{fmtInt(r.urgent)}</Badge> : <span className="subtle">–</span> },
    { key: "backlog", label: "Articles en retard", get: (r) => r.backlog_articles, num: true, sum: true, render: (r) => r.backlog_articles ? <span style={{ color: "var(--warning-fg)" }}>{fmtInt(r.backlog_articles)}</span> : <span className="subtle">–</span> },
    { key: "active", label: "Actif", get: (r) => (r.active ? "oui" : "non"), filter: "select", render: (r) => <Badge tone={r.active ? "ok" : "neutral"}>{r.active ? "oui" : "non"}</Badge> },
  ], []);
  if (suppliers.isError) return <ErrorBox error={suppliers.error} retry={() => suppliers.refetch()} />;
  return (
    <div className="page">
      <div className="page-header"><div className="title"><h1>Fournisseurs</h1><p>Un fournisseur par ligne, avec l'état de ses articles dans le périmètre ; cliquer pour ouvrir la fiche (articles, règles, retards, en transit, commandes à venir).</p></div></div>
      <Card flush>
        {suppliers.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div> : (
          <DataTable rows={rows} columns={cols} rowKey={(r) => r.supplier_id} onRowClick={(r) => nav(`/fournisseurs/${encodeURIComponent(r.supplier_id)}`)} emptyTitle="Aucun fournisseur" maxHeight="calc(100vh - 230px)" storageKey="suppliers" exportTitle="Fournisseurs" />
        )}
      </Card>
    </div>
  );
}
