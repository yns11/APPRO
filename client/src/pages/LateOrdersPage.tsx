import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { CalendarClock, CheckCircle2 } from "lucide-react";
import { api } from "@/lib/api";
import { useOrderStates, useWrite } from "@/lib/queries";
import { Badge, Button, Card, ErrorBox, Kpi, SkeletonBlock, Tabs, useToast } from "@/components/ui";
import { DataTable, type Column } from "@/components/DataTable";
import { ORDER_STATUS_LABELS, ORDER_TYPE_LABELS, SOURCE_LABELS, fmtDate, fmtInt, fmtQty } from "@/lib/format";
import type { OrderStateOut } from "@/lib/types";

type Tab = "not_received" | "planned";

/** Past ERP orders still open (excluded from every stock) to date in the plan or close, and the orders overridden by the plan. */
export default function LateOrdersPage() {
  const q = useOrderStates();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("not_received");
  const [quick, setQuick] = useState<Record<string, string>>({});
  const date = useWrite((p: { o: OrderStateOut; date: string }) => api.put("/api/entries/plan", { order_id: p.o.order_id, article_id: p.o.article_id, date: p.date, qty: p.o.qty_open, supplier_id: p.o.supplier_id, note: "commande non reçue datée dans le plan" }), () => toast.push("Commande datée : elle entre dans le Scenario Plan", "success"));
  const close = useWrite((o: OrderStateOut) => api.put("/api/entries/plan", { order_id: o.order_id, article_id: o.article_id, date: new Date().toISOString().slice(0, 10), qty: 0, supplier_id: o.supplier_id, note: "clôturée : reçue par ailleurs ou ne viendra plus" }), () => toast.push("Commande clôturée dans le plan", "success"));
  const all = q.data ?? [];
  const notReceived = useMemo(() => all.filter((o) => o.status === "not_received"), [all]);
  const planned = useMemo(() => all.filter((o) => o.status === "planned"), [all]);
  const asOf = new Date().toISOString().slice(0, 10);

  const cols = useMemo<Column<OrderStateOut>[]>(() => [
    { key: "article", label: "Article", get: (o) => `${o.article_id} ${o.designation}`, render: (o) => <><Link to={`/articles/${encodeURIComponent(o.article_id)}`}><b>{o.article_id}</b></Link><span className="sub">{o.designation}</span></> },
    { key: "order", label: "Commande", get: (o) => o.order_id, render: (o) => <span className="mono">{o.order_id}<span className="sub">{ORDER_TYPE_LABELS[o.order_type]} · {SOURCE_LABELS[o.source] ?? o.source}</span></span> },
    { key: "supplier", label: "Fournisseur", get: (o) => o.supplier_id ?? "", filter: "select" },
    { key: "date", label: "Date ERP", get: (o) => o.expected_date, render: (o) => <>{fmtDate(o.expected_date)}{o.days_late > 0 && <span className="sub" style={{ color: "var(--critical)" }}>{o.days_late} j</span>}</> },
    { key: "late", label: "Retard (j)", get: (o) => o.days_late, num: true },
    { key: "open", label: "Restant", get: (o) => o.qty_open, num: true, render: (o) => <>{fmtQty(o.qty_open, o.unit)} <span className="subtle">{o.unit}</span></> },
    { key: "status", label: "Statut", get: (o) => ORDER_STATUS_LABELS[o.status], filter: "select", render: (o) => <Badge tone={o.status === "not_received" ? "critical" : "warning"}>{ORDER_STATUS_LABELS[o.status]}</Badge> },
    { key: "plan", label: "Plan", get: (o) => o.plan_qty, num: true, render: (o) => o.plan_dates.length ? <>{fmtQty(o.plan_qty, o.unit)}<span className="sub">{o.plan_dates.map((x) => fmtDate(x)).join(", ")}</span></> : <span className="subtle">–</span> },
    ...(tab === "not_received" ? [{
      key: "act", label: "Qualifier", get: () => "", filter: "none" as const, sortable: false,
      render: (o: OrderStateOut) => {
        const key = `${o.article_id}|${o.order_id}`;
        return <div className="row" style={{ gap: 6 }} onClick={(e) => e.stopPropagation()}>
          <input className="input sm" type="date" min={asOf} value={quick[key] ?? ""} onChange={(e) => setQuick({ ...quick, [key]: e.target.value })} aria-label={`Date plan ${o.order_id}`} style={{ width: 150 }} />
          <Button size="sm" variant="primary" disabled={!quick[key] || date.isPending} title="Arrive encore à cette date : entre dans le Scenario Plan" onClick={() => date.mutate({ o, date: quick[key] })}><CalendarClock />Dater</Button>
          <Button size="sm" disabled={close.isPending} title="Reçue par ailleurs ou ne viendra plus : sort de la liste (0 attendu)" onClick={() => close.mutate(o)}><CheckCircle2 />Clôturer</Button>
        </div>;
      },
    }] : []),
  ], [tab, quick, date.isPending, close.isPending, asOf]);

  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const rows = tab === "not_received" ? notReceived : planned;
  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Commandes non reçues</h1><p>Une commande ERP dont la date est passée et qui reste ouverte ne compte dans aucun scenario : l'ERP ne bouge jamais une commande et son restant n'est pas fiable. Elle arrive encore → la <b>dater</b> (Scenario Plan) ; reçue par ailleurs ou morte → la <b>clôturer</b>.</p></div>
      </div>
      <div className="grid kpis">
        <Kpi label="Non reçues" value={q.data ? fmtInt(notReceived.length) : "…"} tone={notReceived.length ? "warning" : "ok"} meta={q.data ? `backlog ${fmtQty(notReceived.reduce((s, o) => s + o.qty_open, 0))} unités hors stocks` : ""} onClick={() => setTab("not_received")} active={tab === "not_received"} />
        <Kpi label="Commandes planifiées" value={q.data ? fmtInt(planned.length) : "…"} tone="brand" meta="commandes ERP dont le plan diffère de l'ERP" onClick={() => setTab("planned")} active={tab === "planned"} />
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "not_received", label: "Non reçues", count: notReceived.length }, { id: "planned", label: "Planifiées", count: planned.length }]} />
      <Card flush>
        {q.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div> : (
          <DataTable rows={rows} columns={cols} rowKey={(o) => `${o.article_id}|${o.order_id}`} compact emptyTitle={tab === "not_received" ? "Aucune commande passée non reçue" : "Aucune commande planifiée"} />
        )}
      </Card>
    </div>
  );
}
