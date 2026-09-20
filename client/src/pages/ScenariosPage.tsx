import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { FlaskConical, Play, Plus, Save, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useCompare, useScenarios, useWrite, useCockpit, useOrders, usePrograms } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { Badge, Button, Card, Empty, ErrorBox, Field, Kpi, SkeletonBlock, useToast } from "@/components/ui";
import { EVENT_KIND_LABELS, fmtDate, fmtInt, fmtQty } from "@/lib/format";
import type { CompareResponse, ScenarioEvent, ScenarioOut } from "@/lib/types";

const KINDS = Object.keys(EVENT_KIND_LABELS);

function eventSummary(e: ScenarioEvent): string {
  const p = e.payload as Record<string, string | number>;
  switch (e.kind) {
    case "add_order": return `+ commande ${p.qty} × ${p.article_id} le ${p.date}${p.supplier_id ? ` (${p.supplier_id})` : ""}`;
    case "move_order": return `commande ${p.order_id} décalée de ${p.days} j`;
    case "change_order_qty": return `commande ${p.order_id} → ${p.qty}`;
    case "cancel_order": return `commande ${p.order_id} annulée`;
    case "plan_factor": return `PDP × ${p.factor}${p.program_id ? ` (${p.program_id})` : " (tous programmes)"}${p.from ? ` du ${p.from}` : ""}${p.to ? ` au ${p.to}` : ""}`;
    case "set_plan": return `PDP ${p.program_id} semaine du ${p.week_start} = ${p.qty}`;
    case "set_actual": return `réel ${p.program_id} le ${p.date} = ${p.qty}`;
    case "add_movement": return `ajustement ${p.qty} × ${p.article_id} le ${p.date}`;
    case "set_article_param": return `${p.article_id}.${p.field} = ${p.value}`;
    case "set_link_param": return `${p.article_id}/${p.supplier_id}.${p.field} = ${p.value}`;
    default: return e.kind;
  }
}

/** Scenario builder + comparison (baseline vs scenario) */
export default function ScenariosPage() {
  const { perimeter, set } = usePerimeter();
  const list = useScenarios();
  const toast = useToast();
  const [editing, setEditing] = useState<Partial<ScenarioOut> | null>(null);
  const [adhoc, setAdhoc] = useState<CompareResponse | null>(null);
  const [adhocLoading, setAdhocLoading] = useState(false);
  const cockpit = useCockpit();
  const orders = useOrders();
  const programs = usePrograms();
  const selectedId = perimeter.scenarioId;
  const compare = useCompare(selectedId, perimeter.planner);

  const save = useWrite(async (s: Partial<ScenarioOut>) => {
    const body = { name: s.name ?? "Scénario", description: s.description ?? "", params: s.params ?? {}, events: (s.events ?? []).map((e) => ({ kind: e.kind, payload: e.payload, label: e.label })) };
    return s.id ? api.put<ScenarioOut>(`/api/scenarios/${s.id}`, body) : api.post<ScenarioOut>("/api/scenarios", body);
  }, (out) => { toast.push("Scénario enregistré", "success"); setEditing(null); set({ scenarioId: (out as ScenarioOut).id }); });
  const remove = useWrite((id: string) => api.del(`/api/scenarios/${id}`), () => { toast.push("Scénario supprimé"); if (selectedId) set({ scenarioId: null }); });

  const runAdhoc = async () => {
    if (!editing) return;
    setAdhocLoading(true);
    try {
      setAdhoc(await api.post<CompareResponse>("/api/simulate", { planner: perimeter.planner, scenario_id: null, events: (editing.events ?? []).map((e) => ({ kind: e.kind, payload: e.payload, label: e.label })), params: editing.params ?? {} }));
    } catch (e) { toast.push((e as Error).message, "error"); } finally { setAdhocLoading(false); }
  };

  const articles = useMemo(() => (cockpit.data?.articles ?? []).map((a) => ({ id: a.article_id, label: `${a.article_id} · ${a.designation}` })), [cockpit.data]);
  const erpOrders = useMemo(() => {
    const out: { id: string; label: string }[] = [];
    (orders.data ?? []).forEach((o) => out.push({ id: o.id, label: `${o.id} · ${o.article_id} · ${o.expected_date} · ${o.qty}` }));
    return out;
  }, [orders.data]);

  if (list.isError) return <ErrorBox error={list.error} retry={() => list.refetch()} />;
  const shown = adhoc ?? compare.data ?? null;

  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Scénarios & simulation</h1><p>Simulez l'impact d'une commande, d'un retard, d'un changement de plan ou d'un paramètre. Un scénario sélectionné dans la barre supérieure s'applique à toutes les pages (cockpit, fiches, propositions, exports).</p></div>
        <div className="actions"><Button variant="primary" onClick={() => { setAdhoc(null); setEditing({ name: "", description: "", params: {}, events: [] }); }}><Plus />Nouveau scénario</Button></div>
      </div>

      <div className="grid cols-3">
        <Card title="Scénarios enregistrés" tight>
          {list.isLoading ? <SkeletonBlock /> : (list.data ?? []).length === 0 ? <Empty title="Aucun scénario" hint="Créez un scénario pour comparer avec la situation de base." icon={<FlaskConical />} /> : (
            <div className="stack">
              {(list.data ?? []).map((s) => (
                <div key={s.id} className={`card tight ${selectedId === s.id ? "" : ""}`} style={{ borderColor: selectedId === s.id ? "var(--brand)" : undefined }}>
                  <div className="row"><b className="grow truncate">{s.name}</b>{selectedId === s.id && <Badge tone="brand">actif</Badge>}</div>
                  <div className="small subtle">{s.events.length} événement(s) · {s.created_by} · {fmtDate(s.updated_at)}</div>
                  <div className="row" style={{ marginTop: 8 }}>
                    <Button size="sm" variant={selectedId === s.id ? "default" : "primary"} onClick={() => set({ scenarioId: selectedId === s.id ? null : s.id })}>{selectedId === s.id ? "Désactiver" : "Activer"}</Button>
                    <Button size="sm" onClick={() => { setAdhoc(null); setEditing(s); }}>Modifier</Button>
                    <Button size="sm" variant="ghost" onClick={() => { if (window.confirm(`Supprimer « ${s.name} » ?`)) remove.mutate(s.id); }}><Trash2 /></Button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>

        <div className="cols-2 stack" style={{ gridColumn: "span 2" }}>
          {editing && (
            <Card title={editing.id ? `Modifier « ${editing.name} »` : "Nouveau scénario"} actions={<><Button onClick={() => setEditing(null)}>Fermer</Button><Button onClick={runAdhoc} disabled={adhocLoading}><Play />{adhocLoading ? "Calcul…" : "Tester sans enregistrer"}</Button><Button variant="primary" disabled={!editing.name || save.isPending} onClick={() => save.mutate(editing)}><Save />Enregistrer</Button></>}>
              <div className="form-grid">
                <Field label="Nom"><input className="input" value={editing.name ?? ""} onChange={(e) => setEditing({ ...editing, name: e.target.value })} placeholder="ex. PDP +20 % en novembre" /></Field>
                <Field label="Horizon (jours, optionnel)"><input className="input" type="number" value={(editing.params?.horizon_days as number) ?? ""} onChange={(e) => setEditing({ ...editing, params: { ...editing.params, horizon_days: e.target.value ? Number(e.target.value) : undefined } })} /></Field>
                <Field label="Description" span2><input className="input" value={editing.description ?? ""} onChange={(e) => setEditing({ ...editing, description: e.target.value })} /></Field>
              </div>
              <div className="divider" />
              <EventEditor events={editing.events ?? []} onChange={(events) => setEditing({ ...editing, events })} articles={articles} orders={erpOrders} programs={(programs.data ?? []).filter((p) => p.components > 0).map((p) => ({ id: p.program_id, label: `${p.name} (${p.program_id})` }))} />
            </Card>
          )}

          <Card title={adhoc ? "Résultat du test (non enregistré)" : selectedId ? "Comparaison base ↔ scénario actif" : "Comparaison"} hint={shown ? `au ${fmtDate(shown.as_of)}` : "activez ou testez un scénario"}>
            {!shown ? (compare.isLoading ? <SkeletonBlock /> : <Empty title="Aucune comparaison" hint="Activez un scénario dans la liste ou testez un scénario en cours d'édition." />) : (
              <>
                <div className="grid kpis">
                  <Delta label="Critiques" a={shown.base_kpis.critical} b={shown.scenario_kpis.critical} lowerIsBetter />
                  <Delta label="Ruptures projetées" a={shown.base_kpis.stockouts} b={shown.scenario_kpis.stockouts} lowerIsBetter />
                  <Delta label="Propositions" a={shown.base_kpis.proposals} b={shown.scenario_kpis.proposals} />
                  <Delta label="Qté proposée" a={shown.base_kpis.proposals_qty} b={shown.scenario_kpis.proposals_qty} qty />
                  <Delta label="Couverture moy. (j)" a={shown.base_kpis.avg_coverage_days ?? 0} b={shown.scenario_kpis.avg_coverage_days ?? 0} />
                  <Delta label="Besoin 30 j" a={shown.base_kpis.demand_next_30d} b={shown.scenario_kpis.demand_next_30d} qty />
                </div>
                <div className="table-wrap" style={{ marginTop: 16, maxHeight: 420 }}>
                  <table className="tbl compact">
                    <thead><tr><th>Article</th><th className="num">Couverture base → scén.</th><th className="num">Manque max base → scén.</th><th>Rupture base → scén.</th><th className="num">Propositions</th></tr></thead>
                    <tbody>
                      {shown.articles.filter((r) => r.delta_min_stock !== 0 || r.delta_max_shortage !== 0 || r.delta_coverage !== 0 || r.stockout_changed || r.base.proposal_count !== r.scenario.proposal_count).map((r) => (
                        <tr key={r.article_id}>
                          <td><Link to={`/articles/${encodeURIComponent(r.article_id)}`}><b>{r.article_id}</b></Link><span className="sub">{r.designation}</span></td>
                          <td className="num">{String(r.base.coverage_sim_days)} → <b className={`delta ${r.delta_coverage > 0 ? "up" : r.delta_coverage < 0 ? "down" : ""}`}>{String(r.scenario.coverage_sim_days)}</b></td>
                          <td className="num">{fmtQty(r.base.max_shortage_sim as number, r.unit)} → <b className={`delta ${r.delta_max_shortage < 0 ? "up" : r.delta_max_shortage > 0 ? "down" : ""}`}>{fmtQty(r.scenario.max_shortage_sim as number, r.unit)}</b></td>
                          <td>{fmtDate(r.base.first_stockout_sim as string | null)} → {fmtDate(r.scenario.first_stockout_sim as string | null)}</td>
                          <td className="num">{String(r.base.proposal_count)} → {String(r.scenario.proposal_count)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {shown.diagnostics.length > 0 && <p className="small subtle" style={{ marginTop: 8 }}>{shown.diagnostics.filter((d) => !d.startsWith("calcul")).join(" · ")}</p>}
              </>
            )}
          </Card>
        </div>
      </div>
    </div>
  );
}

function Delta({ label, a, b, lowerIsBetter, qty }: { label: string; a: number; b: number; lowerIsBetter?: boolean; qty?: boolean }) {
  const d = b - a;
  const good = d === 0 ? undefined : lowerIsBetter ? d < 0 : d > 0;
  const f = (v: number) => (qty ? fmtQty(v) : Number.isInteger(v) ? fmtInt(v) : v.toFixed(1));
  return <Kpi label={label} value={<>{f(b)} <small className={`delta ${good === undefined ? "" : good ? "up" : "down"}`}>{d > 0 ? "+" : ""}{f(d)}</small></>} meta={`base : ${f(a)}`} tone={good === undefined ? undefined : good ? "ok" : "critical"} />;
}

function EventEditor({ events, onChange, articles, orders, programs }: { events: ScenarioEvent[]; onChange: (e: ScenarioEvent[]) => void; articles: { id: string; label: string }[]; orders: { id: string; label: string }[]; programs: { id: string; label: string }[] }) {
  const [kind, setKind] = useState("plan_factor");
  const [p, setP] = useState<Record<string, string>>({});
  const setF = (k: string, v: string) => setP((x) => ({ ...x, [k]: v }));
  const add = () => {
    const payload: Record<string, unknown> = {};
    Object.entries(p).forEach(([k, v]) => { if (v !== "") payload[k] = ["qty", "factor", "days", "value"].includes(k) && !Number.isNaN(Number(v)) ? Number(v) : v; });
    onChange([...events, { kind, payload, label: "" }]);
    setP({});
  };
  const F = (k: string, label: string, type: "text" | "number" | "date" = "text", options?: { id: string; label: string }[]) => (
    <Field label={label} key={k}>
      {options ? <select className="select sm" value={p[k] ?? ""} onChange={(e) => setF(k, e.target.value)}><option value="">—</option>{options.map((o) => <option key={o.id} value={o.id}>{o.label}</option>)}</select>
        : <input className="input sm" type={type} step="any" value={p[k] ?? ""} onChange={(e) => setF(k, e.target.value)} />}
    </Field>
  );
  const fields: Record<string, JSX.Element[]> = {
    add_order: [F("article_id", "Article", "text", articles), F("supplier_id", "Fournisseur (id)"), F("date", "Date de livraison", "date"), F("qty", "Quantité", "number")],
    move_order: [F("order_id", "Commande", "text", orders), F("days", "Décalage (jours, ± )", "number")],
    change_order_qty: [F("order_id", "Commande", "text", orders), F("qty", "Nouvelle quantité", "number")],
    cancel_order: [F("order_id", "Commande", "text", orders)],
    plan_factor: [F("factor", "Facteur (1.2 = +20 %)", "number"), F("program_id", "Programme (optionnel)", "text", programs), F("from", "Du (optionnel)", "date"), F("to", "Au (optionnel)", "date")],
    set_plan: [F("program_id", "Programme", "text", programs), F("week_start", "Lundi de la semaine", "date"), F("qty", "Quantité", "number")],
    set_actual: [F("program_id", "Programme", "text", programs), F("date", "Date", "date"), F("qty", "Quantité réelle", "number")],
    add_movement: [F("article_id", "Article", "text", articles), F("date", "Date", "date"), F("qty", "Quantité (±)", "number")],
    set_article_param: [F("article_id", "Article", "text", articles), F("field", "Champ", "text", ["coverage_target_days", "alert_red_days", "alert_yellow_days", "overstock_days", "safety_stock_qty", "order_cycle_days"].map((x) => ({ id: x, label: x }))), F("value", "Valeur", "number")],
    set_link_param: [F("article_id", "Article", "text", articles), F("supplier_id", "Fournisseur (id)"), F("field", "Champ", "text", ["lead_time_days", "moq", "pack_qty", "quota_pct", "priority"].map((x) => ({ id: x, label: x }))), F("value", "Valeur", "number")],
  };
  return (
    <div className="stack">
      <h4>Événements du scénario ({events.length})</h4>
      {events.length > 0 && (
        <div className="timeline">{events.map((e, i) => <div className="ev" key={i}><span className="d">{EVENT_KIND_LABELS[e.kind]}</span><span className="row">{eventSummary(e)}<button className="btn ghost xs right" onClick={() => onChange(events.filter((_, k) => k !== i))}><Trash2 size={12} /></button></span></div>)}</div>
      )}
      <div className="card tight" style={{ background: "var(--bg-subtle)" }}>
        <div className="form-grid" style={{ gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))" }}>
          <Field label="Type d'événement"><select className="select sm" value={kind} onChange={(e) => { setKind(e.target.value); setP({}); }}>{KINDS.map((k) => <option key={k} value={k}>{EVENT_KIND_LABELS[k]}</option>)}</select></Field>
          {fields[kind]}
          <div className="field"><label>&nbsp;</label><Button size="sm" onClick={add}><Plus />Ajouter</Button></div>
        </div>
      </div>
    </div>
  );
}
