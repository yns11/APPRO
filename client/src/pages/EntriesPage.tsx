import { useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Redo2, Trash2, Undo2 } from "lucide-react";
import { api } from "@/lib/api";
import { useActions, useAdjustments, useAudit, useFlags, usePlanCells, useWrite } from "@/lib/queries";
import { flashCells } from "@/lib/actionsBus";
import { usePerimeter } from "@/state/PerimeterContext";
import { Badge, Button, Card, ErrorBox, SkeletonBlock, Tabs, useToast } from "@/components/ui";
import { DataTable, type Column } from "@/components/DataTable";
import { fmtDate, fmtDateTime, fmtQty } from "@/lib/format";
import type { ActionOut, ActionResultOut, AdjustmentOut, AuditOut, FlagOut, PlanCellOut } from "@/lib/types";

type Tab = "plan" | "adjustments" | "flags" | "actions" | "audit";
const FLAG_LABELS: Record<string, string> = { order_ignored: "commande ferme ignorée", proposal_refused: "proposition CBN refusée (ce jour)", desadv_hidden: "en transit masqué (DESADV / journal d'arrivée)" };

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
  const actions = useActions({ limit: 300 });
  const qc = useQueryClient();
  const del = useWrite((path: string) => api.del(path), () => toast.push("Supprimé"));
  const act = useWrite((path: string) => api.post<ActionResultOut>(path), (out) => {
    flashCells(out.cells);
    const n = out.actions.length, label = out.actions[n - 1]?.label ?? "";
    toast.push(out.kind === "undo" ? (n > 1 ? `${n} actions annulées (jusqu'à « ${label} »)` : `Annulé : ${label}`) : (n > 1 ? `${n} actions rétablies` : `Rétabli : ${label}`), "success");
    qc.invalidateQueries({ queryKey: ["actions"] });
  });
  const asOf = config?.as_of ?? "";
  const initDate = config?.init_date ?? "";
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
    { key: "effect", label: "Effet", get: (c) => (initDate && c.date <= initDate ? "stock initial" : asOf && c.date <= asOf ? "mouvement passé" : "mouvement prévu"), filter: "select", render: (c) => initDate && c.date <= initDate ? <Badge tone="warning">stock initial</Badge> : <Badge tone="neutral">{asOf && c.date <= asOf ? "mouvement passé" : "mouvement prévu"}</Badge> },
    { key: "note", label: "Commentaire", get: (c) => c.note },
    who<AdjustmentOut>(), delCol<AdjustmentOut>("/api/entries/adjustments", "Supprimer"),
  ], [asOf, initDate]);
  const flagCols = useMemo<Column<FlagOut>[]>(() => [
    article<FlagOut>(),
    { key: "kind", label: "Type", get: (f) => FLAG_LABELS[f.kind], filter: "select", render: (f) => <Badge tone={f.kind === "order_ignored" ? "critical" : f.kind === "proposal_refused" ? "warning" : "neutral"}>{FLAG_LABELS[f.kind]}</Badge> },
    { key: "supplier", label: "Fournisseur", get: (f) => f.supplier_id, filter: "select" },
    { key: "date", label: "Date", get: (f) => f.date, render: (f) => <>{fmtDate(f.date)}{f.kind === "proposal_refused" && <span className="sub subtle">ce jour seulement</span>}</> },
    { key: "qty", label: "Quantité", get: (f) => f.qty, num: true, render: (f) => f.kind !== "order_ignored" ? fmtQty(f.qty) : <span className="subtle">–</span> },
    { key: "note", label: "Détail", get: (f) => f.note, render: (f) => <span className="subtle small">{f.note}</span> },
    who<FlagOut>(), delCol<FlagOut>("/api/entries/flags", "Rétablir"),
  ], []);
  const actionCols = useMemo<Column<ActionOut>[]>(() => {
    const rows = actions.data ?? [];
    // « annuler jusqu'ici » : the newer applied actions of the same user go first
    const stepsUndo = (a: ActionOut) => rows.filter((x) => x.user === a.user && x.state === "applied" && x.undoable && x.ts >= a.ts).length;
    const stepsRedo = (a: ActionOut) => rows.filter((x) => x.user === a.user && x.state === "undone" && x.ts <= a.ts).length;
    const stateOf = (a: ActionOut) => a.conflict ? a.conflict : !a.undoable ? "non annulable" : a.state === "discarded" ? "obsolète" : a.state === "undone" ? "annulée" : "appliquée";
    return [
      { key: "ts", label: "Horodatage", get: (a) => a.ts, render: (a) => <span className="subtle">{fmtDateTime(a.ts)}</span> },
      { key: "user", label: "Utilisateur", get: (a) => a.user, filter: "select", render: (a) => <>{a.user}{a.mine && <span className="sub subtle">vous</span>}</> },
      { key: "label", label: "Action", get: (a) => a.label, filter: "text", render: (a) => <span className={a.state === "undone" ? "subtle" : ""} style={a.state === "undone" ? { textDecoration: "line-through" } : undefined}>{a.label}</span> },
      { key: "article", label: "Article", get: (a) => a.article_id ?? "", render: (a) => a.article_id ? <Link to={`/articles/${encodeURIComponent(a.article_id)}`}>{a.article_id}</Link> : <span className="subtle">–</span> },
      { key: "items", label: "Lignes", get: (a) => a.items, num: true },
      { key: "state", label: "État", get: (a) => stateOf(a), filter: "select", render: (a) => {
        const st = stateOf(a);
        const tone = a.conflict ? "warning" : st === "appliquée" ? "ok" : st === "annulée" ? "neutral" : "outline";
        return <><Badge tone={tone}>{st}</Badge>{!a.mine && !a.can_act && a.undoable && <span className="sub subtle">pas la vôtre</span>}{a.changed_by && a.state !== "applied" && <span className="sub subtle">{a.state === "undone" ? "annulée" : ""} par {a.changed_by}</span>}</>;
      } },
      { key: "act", label: "", get: () => "", filter: "none", sortable: false, export: false, render: (a) => {
        if (!a.can_act || a.conflict || a.state === "discarded") return null;
        if (a.state === "applied") { const n = stepsUndo(a); return <Button size="sm" variant="ghost" title={n > 1 ? `Annule aussi les ${n - 1} action(s) plus récente(s) de ${a.mine ? "vos saisies" : a.user}` : "Annuler cette action"} onClick={(e) => { e.stopPropagation(); act.mutate(`/api/actions/${a.id}/undo`); }}><Undo2 />{n > 1 ? `Annuler jusqu'ici (${n})` : "Annuler"}</Button>; }
        const n = stepsRedo(a);
        return <Button size="sm" variant="ghost" title={n > 1 ? `Rétablit aussi les ${n - 1} action(s) annulée(s) avant` : "Rétablir cette action"} onClick={(e) => { e.stopPropagation(); act.mutate(`/api/actions/${a.id}/redo`); }}><Redo2 />{n > 1 ? `Rétablir jusqu'ici (${n})` : "Rétablir"}</Button>;
      } },
    ];
  }, [actions.data, act]);
  const auditCols = useMemo<Column<AuditOut>[]>(() => [
    { key: "ts", label: "Horodatage", get: (e) => e.ts, render: (e) => <span className="subtle">{fmtDateTime(e.ts)}</span> },
    { key: "user", label: "Utilisateur", get: (e) => e.user, filter: "select" },
    { key: "action", label: "Action", get: (e) => e.action, filter: "select", render: (e) => <Badge tone="outline">{e.action}</Badge> },
    { key: "entity", label: "Objet", get: (e) => `${e.entity_type} ${e.entity_id}`, filter: "text", render: (e) => <>{e.entity_type} <span className="subtle mono">{e.entity_id}</span></> },
    { key: "article", label: "Article", get: (e) => e.article_id ?? "" },
    { key: "payload", label: "Détail", get: (e) => JSON.stringify(e.payload), render: (e) => <span className="small mono" style={{ whiteSpace: "normal", maxWidth: 480, display: "block" }}>{JSON.stringify(e.payload)}</span> },
  ], []);

  const block = <T,>(q: { isLoading: boolean; isError: boolean; error: unknown; data?: T[] }, cols: Column<T>[], key: (r: T) => string, empty: string, hint?: string) => (
    <Card flush>{q.isError ? <ErrorBox error={q.error} /> : q.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : <DataTable rows={q.data ?? []} columns={cols} rowKey={key} compact emptyTitle={empty} emptyHint={hint} storageKey={`saisies.${tab}`} exportTitle={`Saisies - ${tab}`} />}</Card>
  );

  return (
    <div className="page">
      <div className="page-header"><div className="title"><h1>Saisies & journal</h1><p>Les saisies se font dans le tableau (lignes Appro. et Ajustement) ; cette page les liste et les journalise.</p></div></div>
      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "plan", label: "Cellules du plan", count: plan.data?.length }, { id: "adjustments", label: "Ajustements", count: adjustments.data?.length },
        { id: "flags", label: "Commandes ignorées & CBN refusées", count: flags.data?.length },
        { id: "actions", label: "Actions (annuler / rétablir)", count: actions.data?.length }, { id: "audit", label: "Journal", count: audit.data?.length },
      ]} />
      {tab === "actions" && <p className="small subtle" style={{ margin: "0 0 10px" }}>Chaque geste (cellule, recopie, ligne du référentiel, paramètre, PDP…) est gardé <b>7 jours</b> avec l'état avant / après. Annuler une action ancienne annule d'abord les plus récentes du même utilisateur (« jusqu'ici ») ; une nouvelle action rend obsolètes celles qui étaient annulées ; une ligne modifiée depuis par quelqu'un d'autre bloque l'annulation. Vos actions : aussi via ↶ ↷ de l'en-tête et Ctrl+Z / Ctrl+Y.</p>}
      {tab === "actions" && block(actions, actionCols, (a) => a.id, "Aucune action depuis 7 jours")}
      {tab === "plan" && block(plan, planCols, (c) => c.id, "Aucune cellule saisie : le plan suit l'ERP partout")}
      {tab === "adjustments" && block(adjustments, adjCols, (c) => c.id, "Aucun ajustement saisi")}
      {tab === "flags" && block(flags, flagCols, (f) => f.id, "Aucune commande ignorée ni proposition refusée", "Cliquer une cellule Ferme ou Proposition CBN du tableau.")}
      {tab === "audit" && block(audit, auditCols, (e) => String(e.id), "Journal vide")}
    </div>
  );
}
