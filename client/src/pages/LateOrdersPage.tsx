import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, CalendarClock, Search } from "lucide-react";
import { api } from "@/lib/api";
import { useOrderStates, useWrite } from "@/lib/queries";
import { Badge, Button, Card, Empty, ErrorBox, Kpi, SkeletonBlock, Tabs, useToast } from "@/components/ui";
import { OrderActionsDrawer, statusTone, type OrderActionsTarget } from "@/components/OrderActionsDrawer";
import { ACTION_KIND_LABELS, ORDER_STATUS_LABELS, ORDER_TYPE_LABELS, SOURCE_LABELS, fmtDate, fmtInt, fmtQty } from "@/lib/format";
import type { OrderStateOut } from "@/lib/types";

type Tab = "qualify" | "actions" | "review";

/** Past orders still open in the ERP (excluded from every stock) to qualify, and the planner actions in place. */
export default function LateOrdersPage() {
  const q = useOrderStates();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("qualify");
  const [search, setSearch] = useState("");
  const [target, setTarget] = useState<OrderActionsTarget | null>(null);
  const [quick, setQuick] = useState<Record<string, string>>({});
  const close = useWrite((o: OrderStateOut) => api.put("/api/entries/actions", { order_id: o.order_id, article_id: o.article_id, kind: "close", tranches: [], note: "clôturée depuis la liste des retards", supplier_id: o.supplier_id }), () => toast.push("Commande clôturée", "success"));
  const expect = useWrite((p: { o: OrderStateOut; date: string }) => api.put("/api/entries/actions", { order_id: p.o.order_id, article_id: p.o.article_id, kind: "reschedule", tranches: [{ date: p.date, qty: p.o.qty_open }], note: "date attendue saisie depuis la liste des retards", supplier_id: p.o.supplier_id }), () => toast.push("Date attendue enregistrée : la commande entre dans le stock simulé", "success"));

  const all = q.data ?? [];
  const s = search.trim().toLowerCase();
  const match = (o: OrderStateOut) => !s || `${o.article_id} ${o.designation} ${o.order_id} ${o.supplier_id ?? ""}`.toLowerCase().includes(s);
  const toQualify = useMemo(() => all.filter((o) => (o.status === "late" || o.status === "late_sim") && match(o)), [all, s]);
  const withAction = useMemo(() => all.filter((o) => o.action_id && match(o)), [all, s]);
  const review = useMemo(() => all.filter((o) => o.review && match(o)), [all, s]);
  const bySupplier = useMemo(() => {
    const m = new Map<string, { n: number; qty: number }>();
    all.filter((o) => o.status === "late" || o.status === "late_sim").forEach((o) => { const k = o.supplier_id ?? "?"; const c = m.get(k) ?? { n: 0, qty: 0 }; c.n++; c.qty += o.qty_open; m.set(k, c); });
    return Array.from(m.entries()).sort((a, b) => b[1].n - a[1].n);
  }, [all]);
  const liveTarget = target ? { ...target, orders: target.orders.map((o) => all.find((x) => x.order_id === o.order_id && x.article_id === o.article_id) ?? o) } : null;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;

  const rows = tab === "qualify" ? toQualify : tab === "actions" ? withAction : review;
  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Retards à qualifier & actions sur commandes</h1><p>Une commande dont la date ERP est passée et qui reste ouverte ne compte dans aucun stock : l'ERP ne déplace ni n'annule jamais une commande et son restant n'est pas fiable (réceptions saisies à la main). À vous de la qualifier : reçue par ailleurs ou morte → <b>clôturer</b> ; elle arrive encore → <b>attendue le…</b> (elle entre alors dans le stock simulé). Les actions sur les commandes à venir (retard, tranches, annulation) sont listées ici aussi.</p></div>
        <div className="actions"><div className="search"><Search /><input className="input sm" placeholder="Article, commande, fournisseur…" value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: 260 }} aria-label="Recherche" /></div></div>
      </div>

      <div className="grid kpis">
        <Kpi label="À qualifier" value={q.data ? fmtInt(all.filter((o) => o.status === "late" || o.status === "late_sim").length) : "…"} tone={toQualify.length ? "warning" : "ok"} meta={q.data ? `${fmtQty(all.filter((o) => o.status === "late" || o.status === "late_sim").reduce((x, o) => x + o.qty_open, 0))} unités hors stocks` : ""} onClick={() => setTab("qualify")} active={tab === "qualify"} />
        <Kpi label="Actions en place" value={q.data ? fmtInt(all.filter((o) => o.action_id).length) : "…"} tone="brand" meta={q.data ? `${all.filter((o) => o.action_kind === "reschedule").length} replanifiées · ${all.filter((o) => o.action_kind === "cancel").length} annulées · ${all.filter((o) => o.action_kind === "close").length} clôturées` : ""} onClick={() => setTab("actions")} active={tab === "actions"} />
        <Kpi label="À revoir" value={q.data ? fmtInt(all.filter((o) => o.review).length) : "…"} tone={review.length ? "warning" : "ok"} meta="l'ERP a changé depuis l'action (quantité réduite, tranche non couverte)" onClick={() => setTab("review")} active={tab === "review"} />
        <Kpi label="Fournisseurs concernés" value={bySupplier.length} meta={bySupplier.slice(0, 3).map(([id, c]) => `${id}: ${c.n}`).join(" · ")} />
      </div>

      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "qualify", label: "À qualifier", count: toQualify.length },
        { id: "actions", label: "Actions en place", count: withAction.length },
        { id: "review", label: "À revoir", count: review.length },
      ]} />

      <Card flush>
        {q.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div> : rows.length === 0 ? <Empty title={tab === "qualify" ? "Aucune commande passée à qualifier" : tab === "actions" ? "Aucune action en place" : "Rien à revoir"} hint={tab === "qualify" ? "Toutes les commandes ouvertes sont à venir ou qualifiées." : undefined} /> : (
          <table className="tbl">
            <thead><tr><th>Article</th><th>Commande</th><th>Fournisseur</th><th>Date ERP</th><th className="num">Restant</th><th>Statut</th><th>Action (simulé)</th>{tab === "qualify" && <th>Qualifier</th>}<th /></tr></thead>
            <tbody>
              {rows.map((o) => {
                const key = `${o.article_id}|${o.order_id}`;
                return (
                  <tr key={key}>
                    <td><Link to={`/articles/${encodeURIComponent(o.article_id)}`}><b>{o.article_id}</b></Link><span className="sub">{o.designation}</span></td>
                    <td className="mono">{o.order_id}<span className="sub">{ORDER_TYPE_LABELS[o.order_type] ?? o.order_type} · {SOURCE_LABELS[o.source] ?? o.source}</span></td>
                    <td>{o.supplier_id ?? "–"}</td>
                    <td>{fmtDate(o.expected_date)}{o.days_late > 0 && <span className="sub" style={{ color: "var(--critical)" }}>{o.days_late} j de retard</span>}</td>
                    <td className="num">{fmtQty(o.qty_open, o.unit)} <span className="subtle">{o.unit}</span><span className="sub">/ {fmtQty(o.qty_ordered, o.unit)}</span></td>
                    <td><Badge tone={statusTone(o.status)}>{ORDER_STATUS_LABELS[o.status]}</Badge></td>
                    <td className="small">{o.action_kind ? <>{ACTION_KIND_LABELS[o.action_kind]}{o.tranches.length > 0 && <span className="sub">{o.tranches.map((t) => `${fmtQty(t.qty, o.unit)} le ${fmtDate(t.date)}${t.late ? " (dépassée)" : ""}`).join(" · ")}</span>}{o.review && <span className="sub" style={{ color: "var(--warning-fg)" }}>{o.review}</span>}</> : <span className="subtle">–</span>}</td>
                    {tab === "qualify" && (
                      <td style={{ whiteSpace: "nowrap" }}>
                        <div className="row" style={{ gap: 6 }}>
                          <input className="input sm" type="date" value={quick[key] ?? ""} onChange={(e) => setQuick({ ...quick, [key]: e.target.value })} aria-label={`Date attendue ${o.order_id}`} style={{ width: 150 }} />
                          <Button size="sm" variant="primary" disabled={!quick[key] || expect.isPending} title="Attendue à cette date pour tout le restant (stock simulé)" onClick={() => expect.mutate({ o, date: quick[key] })}><CalendarClock />Attendue</Button>
                          <Button size="sm" disabled={close.isPending} title="Reçue par ailleurs ou ne viendra plus : sortir de la liste" onClick={() => close.mutate(o)}><CheckCircle2 />Clôturer</Button>
                        </div>
                      </td>
                    )}
                    <td><Button size="sm" variant="ghost" onClick={() => setTarget({ title: `${o.article_id} · ${o.order_id}`, orders: [o] })}>Détail…</Button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>
      <OrderActionsDrawer target={liveTarget} onClose={() => setTarget(null)} />
    </div>
  );
}
