import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { AlertTriangle, CalendarCheck, CalendarClock, ClipboardList, PackageCheck, Truck } from "lucide-react";
import { api } from "@/lib/api";
import { usePerimeter } from "@/state/PerimeterContext";
import { useRemembered } from "@/lib/pageState";
import { Badge, Card, Empty, ErrorBox, Kpi, Skeleton, SkeletonBlock, Tabs } from "@/components/ui";
import { DataTable, type Column } from "@/components/DataTable";
import { fmtDate, fmtInt, fmtQty } from "@/lib/format";
import type { PortalDesadvRow, PortalOrderRow, PortalReceiptRow, PortalResponse, TransportOut } from "@/lib/types";

type Tab = "orders" | "backlog" | "transports" | "forecasts" | "desadv" | "receipts";
const H = "calc(100vh - 470px)";

/**
 * « Espace fournisseur » : the only page a supplier's transport contact sees once signed in — his firm
 * orders in progress and their transports, his backlog, the transports announced to him, the EDI forecasts
 * (DELFOR), his despatch advices (DESADV) and his receipts, with their KPI ; filters by article, date range
 * and status.  A manager or an administrator previews it with ``?supplier_id=``.
 */
export default function SupplierPortalPage() {
  const { config } = usePerimeter();
  const [params] = useSearchParams();
  const preview = params.getAll("supplier_id");
  const q = useQuery({ queryKey: ["portal", preview], staleTime: 30_000, queryFn: () => api.get<PortalResponse>("/api/portal/overview", { supplier_id: preview }) });
  const [tab, setTab] = useRemembered<Tab>("portal.tab", "orders");
  const [from, setFrom] = useState(""); const [to, setTo] = useState("");
  const d = q.data;
  const inRange = (date: string | null | undefined) => !!date && (!from || date >= from) && (!to || date <= to);

  const art = <T extends { article_id: string; designation: string }>(): Column<T> => ({ key: "article", label: "Article", get: (r) => `${r.article_id} ${r.designation}`, exportGet: (r) => r.article_id, render: (r) => <><b>{r.article_id}</b> <span className="subtle">{r.designation}</span></> });
  const orderCols = useMemo<Column<PortalOrderRow>[]>(() => [
    { key: "purch", label: "Commande", get: (r) => r.purch_id || r.order_id, render: (r) => <span className="mono small">{r.purch_id || r.order_id}</span> },
    art<PortalOrderRow>(),
    { key: "date", label: "Livraison prévue", get: (r) => r.date, render: (r) => fmtDate(r.date) },
    { key: "ordered", label: "Commandé", get: (r) => r.qty_ordered, num: true, render: (r) => <>{fmtQty(r.qty_ordered, r.unit)} <span className="subtle">{r.unit}</span></> },
    { key: "open", label: "Restant à livrer", get: (r) => r.qty_open, num: true, sum: true, unit: (r) => r.unit, render: (r) => <b>{fmtQty(r.qty_open, r.unit)}</b> },
    { key: "status", label: "Statut", get: (r) => r.status, filter: "select", render: (r) => <Badge tone={r.status === "en retard" ? "critical" : r.status === "aujourd'hui" ? "warning" : r.status === "soldée" ? "ok" : "brand"}>{r.status}</Badge> },
    { key: "transports", label: "Transports", get: (r) => r.transports.join(" "), render: (r) => r.transports.length ? r.transports.map((t) => <span key={t} className="badge brand" style={{ marginRight: 4 }}><Truck size={10} /> {t}</span>) : <span className="subtle">–</span> },
  ], []);
  const forecastCols = useMemo<Column<PortalOrderRow>[]>(() => [
    art<PortalOrderRow>(),
    { key: "date", label: "Date de besoin", get: (r) => r.date, render: (r) => fmtDate(r.date) },
    { key: "qty", label: "Quantité prévue", get: (r) => r.qty_ordered, num: true, sum: true, unit: (r) => r.unit, render: (r) => <>{fmtQty(r.qty_ordered, r.unit)} <span className="subtle">{r.unit}</span></> },
    { key: "type", label: "Type", get: (r) => r.order_type, filter: "select" },
    { key: "ref", label: "Référence", get: (r) => r.purch_id, render: (r) => <span className="mono small">{r.purch_id}</span> },
  ], []);
  const transportCols = useMemo<Column<TransportOut>[]>(() => [
    { key: "id", label: "Transport", get: (t) => t.transport_id, render: (t) => <b className="mono">{t.transport_id}</b> },
    { key: "date", label: "Date", get: (t) => t.date, render: (t) => fmtDate(t.date) },
    { key: "time", label: "Heure", get: (t) => t.time },
    { key: "docks", label: "Quais", get: (t) => t.docks.join(" / "), filter: "select" },
    { key: "carrier", label: "Transporteur", get: (t) => t.carrier, filter: "select" },
    { key: "orders", label: "Commandes", get: (t) => t.orders.map((o) => `${o.ref || o.order_id} ${o.article_id}`).join(" ; "), render: (t) => <span className="small">{t.orders.map((o) => <span key={o.order_id} style={{ display: "block" }}><span className="mono">{o.ref || o.order_id}</span> · <b>{o.article_id}</b> · {fmtQty(o.qty)}</span>)}</span> },
    { key: "status", label: "Statut", get: (t) => (t.date < (d?.as_of ?? "") ? "passé" : "prévu"), filter: "select", render: (t) => <Badge tone={t.date < (d?.as_of ?? "") ? "ok" : "brand"}>{t.date < (d?.as_of ?? "") ? "passé" : "prévu"}</Badge> },
    { key: "note", label: "Commentaire", get: (t) => t.note, render: (t) => <span className="small">{t.note}</span> },
  ], [d?.as_of]);
  const desadvCols = useMemo<Column<PortalDesadvRow>[]>(() => [
    { key: "bl", label: "BL (DESADV)", get: (r) => r.packing_slip, render: (r) => <span className="mono small">{r.packing_slip}</span> },
    art<PortalDesadvRow>(),
    { key: "date", label: "Émis le", get: (r) => r.date, render: (r) => fmtDate(r.date) },
    { key: "qty", label: "Quantité annoncée", get: (r) => r.qty, num: true, sum: true, unit: (r) => r.unit, render: (r) => <>{fmtQty(r.qty, r.unit)} <span className="subtle">{r.unit}</span></> },
    { key: "purch", label: "Commande", get: (r) => r.purch_id, render: (r) => <span className="mono small">{r.purch_id}</span> },
    { key: "state", label: "État EDI", get: (r) => r.state, filter: "select" },
    { key: "status", label: "Statut", get: (r) => r.status, filter: "select", render: (r) => <Badge tone={r.status === "reçu" ? "ok" : r.status === "erreur" ? "critical" : "info"}>{r.status}</Badge> },
  ], []);
  const receiptCols = useMemo<Column<PortalReceiptRow>[]>(() => [
    { key: "date", label: "Reçu le", get: (r) => r.date, render: (r) => fmtDate(r.date) },
    art<PortalReceiptRow>(),
    { key: "qty", label: "Quantité reçue", get: (r) => r.qty, num: true, sum: true, unit: (r) => r.unit, render: (r) => <>{fmtQty(r.qty, r.unit)} <span className="subtle">{r.unit}</span></> },
    { key: "purch", label: "Commande", get: (r) => r.purch_id, render: (r) => <span className="mono small">{r.purch_id}</span> },
    { key: "bl", label: "BL", get: (r) => r.packing_slip, render: (r) => <span className="mono small">{r.packing_slip}</span> },
    { key: "status", label: "Statut ERP", get: (r) => r.status, filter: "select", render: (r) => <Badge tone={r.status.toLowerCase().includes("transit") ? "info" : "ok"}>{r.status}</Badge> },
  ], []);

  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const k = d?.kpis;
  const tabs: { id: Tab; label: string; count?: number }[] = [
    { id: "orders", label: "Commandes fermes en cours", count: d?.orders.length },
    { id: "backlog", label: "En retard", count: d?.backlog.length },
    { id: "transports", label: "Transports", count: d?.transports.length },
    { id: "forecasts", label: "Prévisionnel (DELFOR)", count: d?.forecasts.length },
    { id: "desadv", label: "DESADV", count: d?.desadv.length },
    { id: "receipts", label: "Réceptions", count: d?.receipts.length },
  ];
  const who = d?.suppliers.map((s) => `${s.supplier_id} · ${s.name}`).join(", ");
  const title = `Espace fournisseur - ${d?.suppliers.map((s) => s.supplier_id).join(" ") ?? ""}`;
  const dateFilter = <div className="row small" style={{ gap: 6 }}><span className="subtle">Du</span><input className="input sm" type="date" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="Du" style={{ width: 150 }} /><span className="subtle">au</span><input className="input sm" type="date" value={to} onChange={(e) => setTo(e.target.value)} aria-label="Au" style={{ width: 150 }} />{(from || to) && <button className="btn ghost sm" onClick={() => { setFrom(""); setTo(""); }}>Effacer</button>}</div>;
  return (
    <div className="page">
      <div className="page-header">
        <div className="title">
          <h1>Espace fournisseur {d && <span className="muted" style={{ fontWeight: 400 }}>· {who}</span>}</h1>
          <p>Vos commandes fermes en cours et les transports annoncés, votre retard, le prévisionnel qui vous est attribué, vos avis d'expédition (DESADV) et vos réceptions, au {d ? fmtDate(d.as_of) : "…"}. Connecté : <b>{config?.user}</b>.</p>
        </div>
      </div>
      <div className="grid kpis">
        <Kpi label="Commandes fermes en cours" icon={<ClipboardList size={14} />} value={k ? fmtInt(k.open_orders) : <Skeleton w={40} h={28} />} meta={k?.next_delivery ? `prochaine livraison le ${fmtDate(k.next_delivery)}` : "lignes avec un restant à livrer"} tone="brand" onClick={() => setTab("orders")} active={tab === "orders"} />
        <Kpi label="En retard" icon={<AlertTriangle size={14} />} value={k ? fmtInt(k.late_orders) : <Skeleton w={40} h={28} />} meta="livraison prévue passée, restant à livrer" tone={k && k.late_orders > 0 ? "critical" : "ok"} onClick={() => setTab("backlog")} active={tab === "backlog"} />
        <Kpi label="Aujourd'hui" icon={<CalendarClock size={14} />} value={k ? fmtInt(k.today_orders) : <Skeleton w={40} h={28} />} meta="commandes attendues ce jour" tone={k && k.today_orders > 0 ? "warning" : undefined} onClick={() => setTab("orders")} active={false} />
        <Kpi label="Transports prévus" icon={<CalendarCheck size={14} />} value={k ? fmtInt(k.transports) : <Skeleton w={40} h={28} />} meta={k?.next_transport ? `prochain ${k.next_transport}` : "identifiants EM à rappeler au chauffeur"} tone={k && k.transports > 0 ? "brand" : undefined} onClick={() => setTab("transports")} active={tab === "transports"} />
        <Kpi label="Prévisionnel" icon={<CalendarClock size={14} />} value={k ? fmtInt(k.forecast_lines) : <Skeleton w={40} h={28} />} meta={k ? `${k.forecast_weeks} semaine(s) couverte(s)` : ""} onClick={() => setTab("forecasts")} active={tab === "forecasts"} />
        <Kpi label="DESADV en transit" icon={<Truck size={14} />} value={k ? fmtInt(k.desadv_open) : <Skeleton w={40} h={28} />} meta={k && k.desadv_ko ? `${k.desadv_ko} en erreur` : "annoncés, pas encore reçus"} tone={k && k.desadv_ko ? "critical" : k && k.desadv_open ? "info" : undefined} onClick={() => setTab("desadv")} active={tab === "desadv"} />
        <Kpi label="Réceptions 30 j" icon={<PackageCheck size={14} />} value={k ? fmtInt(k.receipts_30d) : <Skeleton w={40} h={28} />} meta="lignes reçues sur 30 jours" tone="ok" onClick={() => setTab("receipts")} active={tab === "receipts"} />
      </div>
      <Tabs value={tab} onChange={setTab} tabs={tabs} />
      <Card flush>
        {!d ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div>
          : d.suppliers.length === 0 ? <Empty title="Aucun fournisseur associé" hint="Votre adresse n'est le contact transport d'aucun fournisseur actif." />
          : tab === "orders" ? <DataTable rows={d.orders.filter((r) => inRange(r.date))} columns={orderCols} rowKey={(r) => r.order_id} maxHeight={H} storageKey="portal.orders" exportTitle={`${title} - commandes`} emptyTitle="Aucune commande ferme en cours" toolbar={dateFilter} />
          : tab === "backlog" ? <DataTable rows={d.backlog.filter((r) => inRange(r.date))} columns={orderCols} rowKey={(r) => r.order_id} maxHeight={H} storageKey="portal.backlog" exportTitle={`${title} - retard`} emptyTitle="Aucun retard" toolbar={dateFilter} />
          : tab === "transports" ? <DataTable rows={d.transports.filter((t) => inRange(t.date))} columns={transportCols} rowKey={(t) => t.transport_id} maxHeight={H} storageKey="portal.transports" exportTitle={`${title} - transports`} emptyTitle="Aucun transport annoncé" toolbar={dateFilter} />
          : tab === "forecasts" ? <DataTable rows={d.forecasts.filter((r) => inRange(r.date))} columns={forecastCols} rowKey={(r) => r.order_id} maxHeight={H} storageKey="portal.forecasts" exportTitle={`${title} - previsionnel`} emptyTitle="Aucun prévisionnel" toolbar={dateFilter} />
          : tab === "desadv" ? <DataTable rows={d.desadv.filter((r) => inRange(r.date))} columns={desadvCols} rowKey={(r) => r.desadv_id} maxHeight={H} storageKey="portal.desadv" exportTitle={`${title} - desadv`} emptyTitle="Aucun DESADV" toolbar={dateFilter} />
          : <DataTable rows={d.receipts.filter((r) => inRange(r.date))} columns={receiptCols} rowKey={(r) => r.receipt_id} maxHeight={H} storageKey="portal.receipts" exportTitle={`${title} - receptions`} emptyTitle="Aucune réception" toolbar={dateFilter} />}
      </Card>
    </div>
  );
}
