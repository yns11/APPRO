import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Download, Plus, Trash2 } from "lucide-react";
import { useCells, useProjection, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { api } from "@/lib/api";
import { Badge, Button, Card, Empty, ErrorBox, Kpi, Segmented, SeverityBadge, Skeleton, SkeletonBlock, Tabs, useToast } from "@/components/ui";
import { StockChart, CoverageChart } from "@/components/charts/StockChart";
import { EntryDrawer, type EntryDraft } from "@/components/EntryDrawer";
import { GridHelp, SimulationGrid } from "@/components/SimulationGrid";
import { OrderActionsDrawer, statusTone, type OrderActionsTarget } from "@/components/OrderActionsDrawer";
import { AlertList } from "./CockpitPage";
import { ACTION_KIND_LABELS, KIND_LABELS, ORDER_STATUS_LABELS, ORDER_TYPE_LABELS, SOURCE_LABELS, fmtDate, fmtDateTime, fmtQty } from "@/lib/format";

export default function ArticlePage() {
  const { articleId } = useParams();
  const { perimeter, set, engineParams } = usePerimeter();
  const [tab, setTab] = useState<"table" | "orders" | "events" | "proposals" | "cells" | "alerts" | "master">("table");
  const [draft, setDraft] = useState<EntryDraft | null>(null);
  const [target, setTarget] = useState<OrderActionsTarget | null>(null);
  const q = useProjection(articleId);
  const cells = useCells(articleId ? { article_id: articleId } : undefined);
  const toast = useToast();
  const delCell = useWrite((id: string) => api.del(`/api/entries/cells/${id}`), () => toast.push("Cellule supprimée"));
  const d = q.data;

  if (!articleId) return <Empty title="Article non précisé" />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const a = d?.article;
  const k = d?.kpis;
  const toQualify = (d?.orders ?? []).filter((o) => o.status === "late" || o.status === "late_sim");
  // keep the drawer in sync with the recomputed states after a save
  const liveTarget = target ? { ...target, orders: target.orders.map((o) => (d?.orders ?? []).find((x) => x.order_id === o.order_id) ?? o) } : null;
  const exportUrl = api.downloadUrl("/api/exports/simulation.xlsx", { article_ids: [articleId], scenario_id: engineParams.scenario_id, granularity: perimeter.granularity === "default" ? "day" : perimeter.granularity, horizon_days: engineParams.horizon_days });

  return (
    <div className="page">
      <div className="page-header">
        <div className="title">
          <div className="row"><Link to="/" className="btn ghost sm"><ArrowLeft />Cockpit</Link>{a && <SeverityBadge severity={k?.severity ?? null} />}</div>
          <h1 style={{ marginTop: 6 }}>{articleId} {a && <span className="muted" style={{ fontWeight: 400 }}>· {a.designation}</span>}</h1>
          <p>{a ? <>Unité {a.unit} · couverture cible {a.coverage_target_days} j · seuils rouge ≤ {a.alert_red_days} j, orange ≤ {a.alert_yellow_days} j, surstock ≥ {a.overstock_days} j · {d?.suppliers.map((s) => `${s.supplier_id} (${s.supplier_name}, délai ${s.lead_time_days} j, MOQ ${fmtQty(s.moq, a.unit)}, PLA ${fmtQty(s.pack_qty, a.unit)}${s.quota_pct < 100 ? `, quota ${s.quota_pct} %` : ""})`).join(" · ")}</> : <Skeleton w={400} />}</p>
        </div>
        <div className="actions">
          <Segmented size="sm" value={perimeter.granularity} onChange={(g) => set({ granularity: g })} options={[{ id: "default", label: "Par défaut" }, { id: "day", label: "Jour" }, { id: "week", label: "Semaine" }]} />
          <Button onClick={() => setDraft({ kind: "order", article_id: articleId, supplier_id: d?.suppliers[0]?.supplier_id ?? null })}><Plus />Saisir</Button>
          <a className="btn" href={exportUrl}><Download />Excel</a>
        </div>
      </div>

      <div className="grid kpis">
        <Kpi label="Stock à date" value={k ? fmtQty(k.stock_as_of_sim, a?.unit) : <Skeleton w={60} h={28} />} unit={a?.unit} meta={k ? `snapshot ${fmtDate(k.snapshot_date)} : ${fmtQty(k.stock_on_hand, a?.unit)}` : ""} />
        <Kpi label="Couverture simulée" value={k ? k.coverage_sim_days : <Skeleton w={40} h={28} />} unit="j" tone={k ? (k.coverage_sim_days <= (a?.alert_red_days ?? 3) ? "critical" : k.coverage_sim_days <= (a?.alert_yellow_days ?? 7) ? "warning" : "ok") : undefined} meta={k ? `ferme : ${k.coverage_firm_days} j · cible ${k.coverage_target_days} j` : ""} />
        <Kpi label="Rupture ferme" value={k ? (k.first_stockout_firm ? fmtDate(k.first_stockout_firm) : "aucune") : <Skeleton w={60} h={28} />} tone={k?.first_stockout_firm ? "critical" : "ok"} meta={k ? (k.first_stockout_firm ? `manque max ${fmtQty(k.max_shortage_firm, a?.unit)}` : `stock mini ${fmtQty(k.min_stock_firm, a?.unit)}`) : ""} />
        <Kpi label="Rupture prévisionnelle" value={k ? (k.first_stockout_forecast ? fmtDate(k.first_stockout_forecast) : "aucune") : <Skeleton w={60} h={28} />} tone={k?.first_stockout_forecast ? "warning" : "ok"} meta={k ? (k.first_stockout_forecast ? `manque max ${fmtQty(k.max_shortage_forecast, a?.unit)}` : `flux ERP fermes + prévisionnels`) : ""} />
        <Kpi label="Rupture simulée" value={k ? (k.first_stockout_sim ? fmtDate(k.first_stockout_sim) : "aucune") : <Skeleton w={60} h={28} />} tone={k?.first_stockout_sim ? "critical" : "ok"} meta={k ? (k.first_stockout_sim ? `manque max ${fmtQty(k.max_shortage_sim, a?.unit)}` : `stock mini ${fmtQty(k.min_stock_sim, a?.unit)}`) : ""} />
        <Kpi label="Besoin 30 j" value={k ? fmtQty(k.demand_next_30d, a?.unit) : <Skeleton w={60} h={28} />} meta={k ? `${fmtQty(k.avg_daily_demand_30d, a?.unit)} / jour · réel ${Math.round(100 * k.actual_share_30d)} % (30 j passés)` : ""} />
        <Kpi label="En-cours ferme" value={k ? fmtQty(k.open_firm_qty, a?.unit) : <Skeleton w={60} h={28} />} meta={k ? `simulé ${fmtQty(k.open_firm_sim_qty, a?.unit)} · + ${fmtQty(k.open_forecast_qty, a?.unit)} prév. ERP · ${k.action_count} action(s)${k.late_order_count ? ` · ${k.late_order_count} à qualifier` : ""}` : ""} tone={k && k.late_order_count ? "warning" : undefined} onClick={() => setTab("orders")} />
        <Kpi label="Réceptions simulées" value={k ? fmtQty(k.sim_receipts_qty, a?.unit) : <Skeleton w={40} h={28} />} tone="brand" meta={k ? `${k.sim_receipt_days} jour(s) saisi(s)` : ""} onClick={() => setTab("cells")} />
        <Kpi label="Complément CBN" value={k ? k.proposal_count : <Skeleton w={40} h={28} />} tone={k && k.urgent_proposal_count ? "critical" : "brand"} meta={k ? `${fmtQty(k.proposed_qty, a?.unit)} · ${k.urgent_proposal_count} urgente(s)` : ""} onClick={() => setTab("proposals")} />
      </div>

      {toQualify.length > 0 && (
        <Card title={`${toQualify.length} commande(s) passée(s) non reçue(s) à qualifier`} hint="exclues de tous les stocks tant qu'elles ne sont pas qualifiées" actions={<Button size="sm" variant="primary" onClick={() => setTarget({ title: `${articleId} · commandes à qualifier`, orders: toQualify })}>Qualifier</Button>}>
          <p className="small subtle" style={{ margin: 0 }}>{toQualify.map((o) => `${o.order_id} : ${fmtQty(o.qty_open, a?.unit)} attendu(s) le ${fmtDate(o.expected_date)} (${o.days_late} j)`).join(" · ")}. Pour chacune : reçue par ailleurs ou morte → clôturer ; arrive encore → « attendue le… » (elle entre alors dans le stock simulé).</p>
        </Card>
      )}

      <Card title="Projection du stock" hint={`Stock ferme = R + F (ERP tel quel) · prévisionnel = R + F + P · simulé = R + F′ (commandes après actions) + réceptions simulées S + complément CBN · ajustements A partout · stocks physiques (jamais négatifs), besoin non servi en « manque » (${k?.shortage_policy === "lost" ? "perdu" : "reporté"}) · barres : besoin (bas), approvisionnements et ajustements (haut)`}>
        {q.isLoading || !d ? <Skeleton h={300} /> : <><StockChart data={d} /><div style={{ marginTop: 8 }}><CoverageChart data={d} /></div></>}
      </Card>

      <Tabs value={tab} onChange={setTab} tabs={[
        { id: "table", label: "Tableau de simulation" },
        { id: "orders", label: "Commandes & actions", count: d?.orders.length },
        { id: "events", label: "Mouvements", count: d?.events.length },
        { id: "proposals", label: "Complément CBN", count: d?.proposals.length },
        { id: "cells", label: "Réceptions simulées & ajustements saisis", count: cells.data?.length },
        { id: "alerts", label: "Alertes", count: d?.alerts.length },
        { id: "master", label: "Données de base" },
      ]} />

      {tab === "table" && (q.isLoading || !d ? <SkeletonBlock rows={10} /> : (
        <Card flush tight>
          <SimulationGrid cols={d} articles={[{ article: d.article, series: d.series, events: d.events, suppliers: d.suppliers, orders: d.orders, kpis: d.kpis }]} cells={cells.data ?? []} onEntry={setDraft} onOrders={setTarget} />
          <GridHelp />
        </Card>
      ))}

      {tab === "orders" && (q.isLoading || !d ? <SkeletonBlock /> : d.orders.length === 0 ? <Empty title="Aucune commande ouverte" hint="Saisir une commande ferme passée hors ERP avec le bouton Saisir." /> : (
        <Card flush title="Commandes ouvertes (créneaux fournisseur · article · date)" hint="cliquer sur une ligne pour agir sur la commande : attendue le… (retard, tranches), annulée, clôturée ; l'action ne touche que le stock simulé">
          <table className="tbl">
            <thead><tr><th>Référence</th><th>Type</th><th>Fournisseur</th><th>Date ERP</th><th className="num">Restant</th><th className="num">Compté (ferme)</th><th>Statut</th><th>Action (simulé)</th><th>Note</th></tr></thead>
            <tbody>
              {d.orders.map((o) => (
                <tr key={o.order_id} className="clickable" onClick={() => setTarget({ title: `${articleId} · ${o.order_id}`, orders: [o] })}>
                  <td className="mono">{o.order_id}<span className="sub">{SOURCE_LABELS[o.source] ?? o.source}</span></td>
                  <td><Badge tone={o.order_type === "FIRM" ? "brand" : "neutral"}>{ORDER_TYPE_LABELS[o.order_type] ?? o.order_type}</Badge></td>
                  <td className="subtle">{o.supplier_id ?? "–"}</td>
                  <td>{fmtDate(o.expected_date)}{o.days_late > 0 && <span className="sub" style={{ color: "var(--critical)" }}>{o.days_late} j de retard</span>}</td>
                  <td className="num">{fmtQty(o.qty_open, d.article.unit)}<span className="sub">/ {fmtQty(o.qty_ordered, d.article.unit)}</span></td>
                  <td className="num">{o.in_firm_layer ? fmtQty(o.qty_expected, d.article.unit) : <span className="subtle">exclue</span>}</td>
                  <td><Badge tone={statusTone(o.status)}>{ORDER_STATUS_LABELS[o.status]}</Badge>{o.review && <span className="sub" style={{ color: "var(--warning-fg)" }}>à revoir</span>}</td>
                  <td className="small">{o.action_kind ? <>{ACTION_KIND_LABELS[o.action_kind]}{o.tranches.length > 0 && <span className="sub">{o.tranches.map((t) => `${fmtQty(t.qty, d.article.unit)} le ${fmtDate(t.date)}${t.late ? " (dépassée)" : ""}`).join(" · ")}</span>}</> : <span className="subtle">–</span>}</td>
                  <td className="small subtle" style={{ whiteSpace: "normal", maxWidth: 260 }}>{o.review || o.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ))}

      {tab === "events" && (q.isLoading || !d ? <SkeletonBlock /> : d.events.length === 0 ? <Empty title="Aucun mouvement sur l'horizon" /> : (
        <Card flush>
          <table className="tbl">
            <thead><tr><th>Date</th><th>Type</th><th>Référence</th><th>Fournisseur</th><th>Nature</th><th>Origine</th><th className="num">Quantité</th><th></th></tr></thead>
            <tbody>
              {d.events.map((e, i) => (
                <tr key={i}>
                  <td>{fmtDate(e.date)}</td>
                  <td>{KIND_LABELS[e.kind] ?? e.kind}</td>
                  <td className="mono">{e.ref}</td>
                  <td className="subtle">{e.supplier_id ?? "–"}</td>
                  <td><Badge tone={e.order_type === "FIRM" ? "brand" : e.order_type === "PROPOSAL" ? "warning" : e.order_type === "RECEIPT" ? "ok" : "neutral"}>{ORDER_TYPE_LABELS[e.order_type] ?? e.order_type}</Badge></td>
                  <td className="subtle">{e.source}</td>
                  <td className="num">{fmtQty(e.qty, d.article.unit)}</td>
                  <td>{e.late && <Badge tone="critical">retard</Badge>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ))}

      {tab === "proposals" && (q.isLoading || !d ? <SkeletonBlock /> : d.proposals.length === 0 ? <Empty title="Aucun complément CBN" hint="Le stock simulé reste au-dessus de la cible sur tout l'horizon." /> : (
        <Card flush>
          <table className="tbl">
            <thead><tr><th>Commander le</th><th>Livraison</th><th>Fournisseur</th><th className="num">Quantité</th><th className="num">Besoin net</th><th className="num">Stock avant → après</th><th>Motif</th></tr></thead>
            <tbody>
              {d.proposals.map((p) => (
                <tr key={p.proposal_id}>
                  <td>{fmtDate(p.order_date)} {p.urgent && <Badge tone="critical">urgent</Badge>}</td>
                  <td>{fmtDate(p.delivery_date)}</td>
                  <td>{p.supplier_id} <span className="sub">{p.supplier_name} · délai {p.lead_time_days} j</span></td>
                  <td className="num"><b>{fmtQty(p.qty, p.unit)}</b><span className="sub">MOQ {fmtQty(p.moq, p.unit)} · PLA {fmtQty(p.pack_qty, p.unit)}</span></td>
                  <td className="num">{fmtQty(p.net_requirement, p.unit)}</td>
                  <td className="num">{fmtQty(p.projected_stock_before, p.unit)} → {fmtQty(p.projected_stock_after, p.unit)}</td>
                  <td className="small" style={{ whiteSpace: "normal", maxWidth: 360 }}>{p.reason}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ))}

      {tab === "cells" && (cells.isLoading ? <SkeletonBlock /> : !cells.data?.length ? <Empty title="Aucune réception simulée ni ajustement saisi" hint="Saisissez directement dans le tableau de simulation." /> : (
        <Card flush>
          <table className="tbl">
            <thead><tr><th>Date</th><th>Nature</th><th className="num">Quantité</th><th>Saisie</th><th>Origine</th><th>Note</th><th>Modifié</th><th></th></tr></thead>
            <tbody>
              {cells.data.map((c) => (
                <tr key={c.id}>
                  <td>{fmtDate(c.date)}</td>
                  <td><Badge tone={c.kind === "sim_receipt" ? "warning" : "neutral"}>{c.kind === "sim_receipt" ? "Réception simulée" : "Ajustement"}</Badge></td>
                  <td className={`num ${c.qty < 0 ? "delta down" : ""}`}><b>{fmtQty(c.qty, a?.unit)}</b></td>
                  <td className="mono small">{c.expression}</td>
                  <td><Badge tone="outline">{c.source}</Badge></td>
                  <td className="small subtle" style={{ whiteSpace: "normal", maxWidth: 420 }}>{c.note}</td>
                  <td className="subtle small">{c.updated_by}<br />{fmtDateTime(c.updated_at)}</td>
                  <td><Button size="sm" variant="ghost" title="Supprimer" onClick={() => delCell.mutate(c.id)}><Trash2 /></Button></td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      ))}

      {tab === "alerts" && <Card>{q.isLoading || !d ? <SkeletonBlock /> : <AlertList alerts={d.alerts} asOf={d.as_of} />}</Card>}

      {tab === "master" && d && (
        <div className="grid cols-2">
          <Card title="Fournisseurs & règles d'approvisionnement" hint="Paramètres modifiables dans Paramètres & règles">
            <table className="tbl compact">
              <thead><tr><th>Fournisseur</th><th className="num">MOQ</th><th className="num">PLA</th><th className="num">Délai (j ouvrés)</th><th className="num">Quota</th><th className="num">Priorité</th></tr></thead>
              <tbody>{d.suppliers.map((s) => <tr key={s.supplier_id}><td>{s.supplier_id}<span className="sub">{s.supplier_name}</span></td><td className="num">{fmtQty(s.moq, d.article.unit)}</td><td className="num">{fmtQty(s.pack_qty, d.article.unit)}</td><td className="num">{s.lead_time_days}</td><td className="num">{s.quota_pct} %</td><td className="num">{s.priority}</td></tr>)}</tbody>
            </table>
          </Card>
          <Card title="Programmes consommateurs (nomenclature 1 niveau)" hint="Besoin = production effective × quantité par unité">
            <table className="tbl compact">
              <thead><tr><th>Programme</th><th className="num">Qté / unité</th><th className="num">Production 30 j</th><th className="num">Besoin induit 30 j</th></tr></thead>
              <tbody>{d.programs.map((p) => <tr key={p.program_id}><td>{p.name}<span className="sub">{p.program_id}</span></td><td className="num">{p.qty_per} {p.unit}</td><td className="num">{fmtQty(p.production_next_30d)}</td><td className="num">{fmtQty(p.production_next_30d * p.qty_per, d.article.unit)}</td></tr>)}</tbody>
            </table>
          </Card>
          <Card title="Politique de stock" className="cols-2">
            <div className="grid cols-4">
              <div><h4>Couverture cible</h4><div>{d.article.coverage_target_days} jours</div></div>
              <div><h4>Seuils d'alerte</h4><div>rouge ≤ {d.article.alert_red_days} j · orange ≤ {d.article.alert_yellow_days} j · surstock ≥ {d.article.overstock_days} j</div></div>
              <div><h4>Stock de sécurité</h4><div>{fmtQty(d.article.safety_stock_qty, d.article.unit)} {d.article.unit}</div></div>
              <div><h4>Lotissement</h4><div>{d.article.lot_policy} · cycle {d.article.order_cycle_days} j</div></div>
            </div>
          </Card>
          {d.diagnostics.length > 0 && <Card title="Diagnostics du calcul" className="cols-2"><ul className="small subtle">{d.diagnostics.map((x, i) => <li key={i}>{x}</li>)}</ul></Card>}
        </div>
      )}

      <EntryDrawer draft={draft} onClose={() => setDraft(null)} articles={a ? [{ article_id: a.article_id, designation: a.designation, unit: a.unit }] : []} />
      <OrderActionsDrawer target={liveTarget} onClose={() => setTarget(null)} />
    </div>
  );
}
