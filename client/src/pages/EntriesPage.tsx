import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useAdjustments, useAudit, useFlags, usePlanCells, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { Badge, Button, Card, ErrorBox, SkeletonBlock, Tabs, useToast } from "@/components/ui";
import { DataTable, type Column } from "@/components/DataTable";
import { fmtDate, fmtDateTime, fmtQty } from "@/lib/format";
import type { AdjustmentOut, AuditOut, FlagOut, PlanCellOut } from "@/lib/types";

type Tab = "plan" | "adjustments" | "flags" | "audit";

const article = <T extends { article_id: string }>(): Column<T> => ({ key: "article", label: "Article", get: (r) => r.article_id, render: (r) => <Link to={`/articles/${encodeURIComponent(r.article_id)}`}><b>{r.article_id}</b></Link> });
const who = <T extends { updated_by: string; updated_at: string }>(): Column<T> => ({ key: "who", label: "Modifié", get: (r) => `${r.updated_by} ${r.updated_at}`, render: (r) => <span className="subtle small">{r.updated_by}<br />{fmtDateTime(r.updated_at)}</span> });

/** Everything the planners typed: plan cells, adjustment cells, and the audit journal. */
export default function EntriesPage() {
  const [tab, setTab] = useState<Tab>("plan");
  const toast = useToast();
  const { config, rights } = usePerimeter();
  const plan = usePlanCells();
  const adjustments = useAdjustments();
  const flags = useFlags();
  const audit = useAudit({ limit: 300 });
  const del = useWrite((path: string) => api.del(path), () => toast.push("Supprimé"));
  const asOf = config?.as_of ?? "";
  const delCol = <T extends { id: string }>(path: string, title: string): Column<T> => ({ key: "del", label: "", get: () => "", filter: "none", sortable: false, render: (r) => rights.canWrite ? <Button size="sm" variant="ghost" title={title} onClick={(e) => { e.stopPropagation(); del.mutate(`${path}/${r.id}`); }}><Trash2 /></Button> : null });

  const planCols = useMemo<Column<PlanCellOut>[]>(() => [
    article<PlanCellOut>(),
    { key: "supplier", label: "Fournisseur", get: (c) => c.supplier_id, filter: "select" },
    { key: "date", label: "Date", get: (c) => c.date, render: (c) => <>{fmtDate(c.date)}{asOf && c.date < asOf && <span className="sub subtle">expirée</span>}</> },
    { key: "qty", label: "Quantité", get: (c) => c.qty, num: true, render: (c) => <b>{fmtQty(c.qty)}</b> },
    { key: "expr", label: "Saisie", get: (c) => c.expression, render: (c) => <span className="mono small">{c.expression}</span> },
    { key: "note", label: "Commentaire", get: (c) => c.note },
    who<PlanCellOut>(), delCol<PlanCellOut>("/api/entries/plan", "Supprimer : retour à l'ERP"),
  ], [asOf]);
  const adjCols = useMemo<Column<AdjustmentOut>[]>(() => [
    article<AdjustmentOut>(),
    { key: "date", label: "Date", get: (c) => c.date, render: (c) => fmtDate(c.date) },
    { key: "qty", label: "Quantité", get: (c) => c.qty, num: true, render: (c) => <span className={c.qty < 0 ? "delta down" : "delta up"}>{c.qty > 0 ? "+" : ""}{fmtQty(c.qty)}</span> },
    { key: "effect", label: "Effet", get: (c) => (asOf && c.date <= asOf ? "stock de référence" : "mouvement prévu"), filter: "select", render: (c) => asOf && c.date <= asOf ? <Badge tone="warning">stock de référence</Badge> : <Badge tone="neutral">mouvement prévu</Badge> },
    { key: "note", label: "Commentaire", get: (c) => c.note },
    who<AdjustmentOut>(), delCol<AdjustmentOut>("/api/entries/adjustments", "Supprimer"),
  ], [asOf]);
  const flagCols = useMemo<Column<FlagOut>[]>(() => [
    article<FlagOut>(),
    { key: "kind", label: "Type", get: (f) => (f.kind === "order_ignored" ? "commande ferme ignorée" : "proposition CBN refusée"), filter: "select", render: (f) => f.kind === "order_ignored" ? <Badge tone="critical">commande ferme ignorée</Badge> : <Badge tone="warning">proposition CBN refusée</Badge> },
    { key: "supplier", label: "Fournisseur", get: (f) => f.supplier_id, filter: "select" },
    { key: "date", label: "Date", get: (f) => f.date, render: (f) => <>{fmtDate(f.date)}{f.kind === "proposal_refused" && <span className="sub subtle">jusqu'à la fin de la semaine</span>}</> },
    { key: "qty", label: "Quantité", get: (f) => f.qty, num: true, render: (f) => f.kind === "proposal_refused" ? fmtQty(f.qty) : <span className="subtle">–</span> },
    who<FlagOut>(), delCol<FlagOut>("/api/entries/flags", "Rétablir"),
  ], []);
  const auditCols = useMemo<Column<AuditOut>[]>(() => [
    { key: "ts", label: "Horodatage", get: (e) => e.ts, render: (e) => <span className="subtle">{fmtDateTime(e.ts)}</span> },
    { key: "user", label: "Utilisateur", get: (e) => e.user, filter: "select" },
    { key: "action", label: "Action", get: (e) => e.action, filter: "select", render: (e) => <Badge tone="outline">{e.action}</Badge> },
    { key: "entity", label: "Objet", get: (e) => `${e.entity_type} ${e.entity_id}`, filter: "text", render: (e) => <>{e.entity_type} <span className="subtle mono">{e.entity_id}</span></> },
    { key: "article", label: "Article", get: (e) => e.article_id ?? "" },
    { key: "payload", label: "Détail", get: (e) => JSON.stringify(e.payload), render: (e) => <span className="small mono" style={{ whiteSpace: "normal", maxWidth: 480, display: "block" }}>{JSON.stringify(e.payload)}</span> },
  ], []);

  const block = <T,>(q: { isLoading: boolean; isError: boolean; error: unknown; data?: T[] }, cols: Column<T>[], key: (r: T) => string, empty: string, hint?: string) => (
    <Card flush>{q.isError ? <ErrorBox error={q.error} /> : q.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : <DataTable rows={q.data ?? []} columns={cols} rowKey={key} compact emptyTitle={empty} emptyHint={hint} />}</Card>
  );

  return (
    <div className="page">
      <div className="page-header"><div className="title"><h1>Saisies & journal</h1><p>Les saisies se font dans le tableau (lignes Plan et Ajustement) ; cette page les liste et les journalise.</p></div></div>
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "plan", label: "Cellules du plan", count: plan.data?.length }, { id: "adjustments", label: "Ajustements", count: adjustments.data?.length },
        { id: "flags", label: "Commandes ignorées & CBN refusées", count: flags.data?.length }, { id: "audit", label: "Journal", count: audit.data?.length },
      ]} />
      {tab === "plan" && block(plan, planCols, (c) => c.id, "Aucune cellule saisie : le plan suit l'ERP partout")}
      {tab === "adjustments" && block(adjustments, adjCols, (c) => c.id, "Aucun ajustement saisi")}
      {tab === "flags" && block(flags, flagCols, (f) => f.id, "Aucune commande ignorée ni proposition refusée", "Cliquer une cellule Ferme ou Proposition CBN du tableau.")}
      {tab === "audit" && block(audit, auditCols, (e) => String(e.id), "Journal vide")}
    </div>
  );
}
