import { useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertOctagon, CalendarClock, ListChecks, Users } from "lucide-react";
import { useExceptions } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { useRemembered } from "@/lib/pageState";
import { ArticleLink, Badge, Card, Empty, ErrorBox, Kpi, Skeleton, SkeletonBlock, SupplierLink } from "@/components/ui";
import { DataTable, type Column } from "@/components/DataTable";
import { STATUS_LABELS, STATUS_TONE, fmtDate, fmtInt, fmtQty } from "@/lib/format";
import type { ExceptionItem, ExceptionKind } from "@/lib/types";

const KIND_TONE: Record<ExceptionKind, "critical" | "warning" | "info" | "neutral" | "brand"> = {
  order_late: "critical", order_due: "warning", stockout: "critical", backlog: "warning", desadv_error: "info", acr_pending: "neutral",
  stock_drift: "info", negative_stock: "critical", unlinked_orders: "neutral", no_supplier: "warning",
};

function Score({ it }: { it: ExceptionItem }) {
  const tone = it.score >= 70 ? "critical" : it.score >= 45 ? "warning" : "ok";
  return <span className={`score-bar ${tone}`} title={`urgence ${it.score_urgency} + production à risque ${it.score_risk} + criticité ${it.score_criticality}`}><span className="track"><span className="fill" style={{ width: `${it.score}%` }} /></span><b>{it.score}</b></span>;
}

/**
 * The exception queue : every actionable situation of the perimeter (order to place, projected shortage, late delivery,
 * DESADV to fix, receipt to book, data issue) ranked by a transparent score — urgency of the deadline, production at
 * risk, criticality of the article — with its owner, its deadline and the action to take.
 */
export function ExceptionsPanel() {
  const q = useExceptions();
  const { config } = usePerimeter();
  const nav = useNavigate();
  const [kind, setKind] = useRemembered<string>("exceptions.kind", "");
  const [owner, setOwner] = useState("");
  const [overdueOnly, setOverdueOnly] = useRemembered("exceptions.overdue", false);
  const d = q.data;
  const asOf = d?.as_of ?? config?.as_of ?? "";
  const rows = useMemo(() => (d?.items ?? []).filter((it) => (!kind || it.kind === kind) && (!owner || it.owner === owner) && (!overdueOnly || it.overdue)), [d, kind, owner, overdueOnly]);
  const cols = useMemo<Column<ExceptionItem>[]>(() => [
    { key: "score", label: "Priorité", get: (it) => it.score, num: true, export: "number", title: "Score 0 – 100 = urgence (0 – 50) + production à risque (0 – 30) + criticité (0 – 20)", render: (it) => <Score it={it} /> },
    { key: "kind", label: "Exception", get: (it) => it.kind_label, filter: "select", render: (it) => <Badge tone={KIND_TONE[it.kind]}>{it.kind_label}</Badge> },
    { key: "article", label: "Article", get: (it) => `${it.article_id} ${it.designation}`, exportGet: (it) => it.article_id, render: (it) => <ArticleLink id={it.article_id} designation={it.designation} /> },
    { key: "supplier", label: "Fournisseur", get: (it) => `${it.supplier_id ?? ""} ${it.supplier_name}`, render: (it) => <SupplierLink id={it.supplier_id} name={it.supplier_name} /> },
    { key: "deadline", label: "Échéance", get: (it) => it.deadline ?? "", title: "Date limite de l'action ; J± en jours ouvrés", render: (it) => it.deadline ? <span className="cell-2l"><span style={it.overdue ? { color: "var(--critical-fg)", fontWeight: 600 } : undefined}>{fmtDate(it.deadline)}</span><span className="sub">{it.days_to_deadline === null ? "" : it.days_to_deadline < 0 ? `dépassée de ${-it.days_to_deadline} j` : it.days_to_deadline === 0 ? "aujourd'hui" : `J+${it.days_to_deadline}`}</span></span> : <span className="subtle">–</span> },
    { key: "action", label: "Action", get: (it) => it.action, render: (it) => <span className="cell-2l" style={{ whiteSpace: "normal", maxWidth: 420, display: "inline-block" }}><span>{it.action}</span><span className="sub exc-why">{it.detail}</span></span> },
    { key: "risk", label: "Production à risque", get: (it) => it.production_at_risk.first_stockout ?? "", title: "Première rupture projetée de l'article et programmes qui l'utilisent", render: (it) => <span className="cell-2l"><span>{it.production_at_risk.first_stockout ? <Badge tone="critical">rupture {fmtDate(it.production_at_risk.first_stockout)}</Badge> : <span className="subtle">aucune rupture projetée</span>}</span><span className="sub" title={it.production_at_risk.programs.join(", ")}>{it.production_at_risk.program_count} programme(s){it.production_at_risk.shortage_20wd > 0 ? ` · manque max ${fmtQty(it.production_at_risk.shortage_20wd, it.unit)}` : ""}</span></span> },
    { key: "owner", label: "Propriétaire", get: (it) => it.owner, filter: "select" },
    { key: "resolution", label: "Délai de résolution", get: (it) => it.resolution_days, num: true, title: "Jours ouvrés estimés pour clore l'exception (délai fournisseur pour une commande, un jour pour une correction de données)", render: (it) => `${it.resolution_days} j` },
    { key: "why", label: "Pourquoi ce rang", get: (it) => it.why.join(" ; "), hidden: true, render: (it) => <span className="exc-why">{it.why.join(" · ")}</span> },
    { key: "status", label: "Statut article", get: (it) => (it.status ? STATUS_LABELS[it.status] : ""), filter: "select", hidden: true, render: (it) => it.status ? <Badge tone={STATUS_TONE[it.status]}>{STATUS_LABELS[it.status]}</Badge> : null },
    { key: "abc", label: "Classe", get: (it) => it.abc ?? "", filter: "select", hidden: true },
    { key: "qty", label: "Quantité", get: (it) => it.qty, num: true, hidden: true, render: (it) => it.qty === null ? "–" : fmtQty(it.qty, it.unit) },
    { key: "ref", label: "Référence", get: (it) => it.ref, hidden: true, render: (it) => <span className="mono small">{it.ref}</span> },
  ], []);
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  return (
    <>
      <div className="grid kpis">
        <Kpi label="Exceptions" icon={<ListChecks size={14} />} value={d ? fmtInt(d.total) : <Skeleton w={40} h={28} />} meta="situations qui appellent une décision" tone={d && d.total ? "brand" : "ok"} onClick={() => { setKind(""); setOverdueOnly(false); }} active={!kind && !overdueOnly} />
        <Kpi label="Échéance dépassée" icon={<AlertOctagon size={14} />} value={d ? fmtInt(d.overdue) : <Skeleton w={40} h={28} />} meta="à traiter en premier" tone={d && d.overdue ? "critical" : "ok"} onClick={() => setOverdueOnly(!overdueOnly)} active={overdueOnly} />
        <Kpi label="Aujourd'hui" icon={<CalendarClock size={14} />} value={d ? fmtInt(d.today) : <Skeleton w={40} h={28} />} meta="échéance ce jour" tone={d && d.today ? "warning" : undefined} />
        <Kpi label="Propriétaires" icon={<Users size={14} />} value={d ? fmtInt(d.by_owner.length) : <Skeleton w={40} h={28} />} meta={d?.by_owner.slice(0, 3).map((o) => `${o.owner} ${o.count}`).join(" · ") ?? ""} />
      </div>
      <Card flush title="File d'exceptions" hint={`classée par score décroissant : urgence de l'échéance (0 – 50), production à risque (0 – 30), criticité de l'article (0 – 20) ; une ligne ouvre la fiche article · aujourd'hui ${fmtDate(asOf)}`}
        actions={<div className="row wrap">
          <select className="select sm" value={owner} onChange={(e) => setOwner(e.target.value)} aria-label="Propriétaire"><option value="">Tous les propriétaires</option>{(d?.by_owner ?? []).map((o) => <option key={o.owner} value={o.owner}>{o.owner} ({o.count})</option>)}</select>
        </div>}>
        {d && d.by_kind.length > 0 && (
          <div className="kind-chips" style={{ padding: "8px 12px" }}>
            {d.by_kind.map((k) => <button key={k.kind} type="button" className={`btn xs ${kind === k.kind ? "primary" : ""}`} onClick={() => setKind(kind === k.kind ? "" : k.kind)} title={`Ne montrer que : ${k.label}`}>{k.label} <b>{k.count}</b></button>)}
          </div>
        )}
        {q.isLoading || !d ? <div style={{ padding: 20 }}><SkeletonBlock rows={6} /></div> : d.total === 0 ? <Empty title="Aucune exception" hint="Rien n'appelle de décision aujourd'hui sur ce périmètre." /> : (
          <DataTable rows={rows} columns={cols} rowKey={(it) => it.id} onRowClick={(it) => nav(`/articles/${encodeURIComponent(it.article_id)}`)} rowClass={(it) => (it.overdue ? "exc-overdue" : "")} compact maxHeight="calc(100vh - 380px)" storageKey="exceptions.table" exportTitle="File d'exceptions" emptyTitle="Aucune exception ne correspond aux filtres" />
        )}
      </Card>
    </>
  );
}
