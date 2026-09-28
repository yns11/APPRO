import { useEffect, useState } from "react";
import { Check, Plus, Scissors, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useWrite } from "@/lib/queries";
import { Badge, Button, Drawer, useToast } from "@/components/ui";
import { ORDER_STATUS_LABELS, ORDER_TYPE_LABELS, ORIGIN_LABELS, fmtDate, fmtQty } from "@/lib/format";
import type { OrderStateOut, PlanLineIn, PlanLineState } from "@/lib/types";

export interface PlanTarget { article_id: string; unit: string; title: string; date?: string; lines: PlanLineState[]; orders: OrderStateOut[]; asOf: string; }

const ORIGIN_TONE: Record<string, "ok" | "warning" | "neutral" | "brand" | "outline"> = { erp: "outline", override: "warning", free: "brand", cbn: "neutral", expired: "neutral" };

/** One line of the plan with inline editing of its date / quantity ; ERP lines become overrides when edited. */
function LineRow({ l, unit, asOf, onSaved }: { l: PlanLineState; unit: string; asOf: string; onSaved: () => void }) {
  const toast = useToast();
  const [date, setDate] = useState(l.date);
  const [qty, setQty] = useState(String(l.qty));
  useEffect(() => { setDate(l.date); setQty(String(l.qty)); }, [l]);
  const save = useWrite((body: PlanLineIn) => api.put("/api/entries/plan", body), () => { toast.push("Plan enregistré", "success"); onSaved(); });
  const del = useWrite((id: string) => api.del(`/api/entries/plan/${id}`), () => { toast.push(l.order_id ? "Retour à l'ERP" : "Ligne supprimée"); onSaved(); });
  const dirty = date !== l.date || Number(qty) !== l.qty;
  const readOnly = l.origin === "expired";
  const body = (d: string, q: number, note?: string): PlanLineIn => ({ article_id: l.article_id, date: d, qty: q, order_id: l.order_id, supplier_id: l.supplier_id, note: note ?? l.note, line_id: l.origin === "override" || l.origin === "free" ? l.line_id : null });
  return (
    <tr className={l.origin === "expired" ? "past" : ""}>
      <td><Badge tone={ORIGIN_TONE[l.origin]}>{ORIGIN_LABELS[l.origin]}</Badge></td>
      <td className="mono small">{l.order_id ?? <span className="subtle">—</span>}{l.erp_date && <span className="sub">ERP {fmtDate(l.erp_date)} · {fmtQty(l.erp_qty ?? 0, unit)}</span>}</td>
      <td>{readOnly || l.origin === "cbn" ? fmtDate(l.date) : <input className="input sm" type="date" min={asOf} value={date} onChange={(e) => setDate(e.target.value)} aria-label="Date plan" />}</td>
      <td className="num">{readOnly || l.origin === "cbn" ? fmtQty(l.qty, unit) : <input className="input sm" type="number" step="any" min={0} value={qty} onChange={(e) => setQty(e.target.value)} style={{ width: 100, textAlign: "right" }} aria-label="Quantité plan" />}</td>
      <td className="small subtle" style={{ whiteSpace: "normal", maxWidth: 220 }}>{l.note}</td>
      <td style={{ whiteSpace: "nowrap" }}>
        {l.origin === "cbn" ? (
          <Button size="sm" variant="primary" title="Accepter la proposition : elle devient une ligne du plan" onClick={() => save.mutate({ article_id: l.article_id, date: l.date, qty: l.qty, supplier_id: l.supplier_id, note: "complément CBN accepté" })}><Check />Accepter</Button>
        ) : readOnly ? null : (
          <div className="row" style={{ gap: 4 }}>
            <Button size="sm" variant={dirty ? "primary" : "default"} disabled={!dirty || !date || Number(qty) < 0 || save.isPending} onClick={() => save.mutate(body(date, Number(qty)))}><Check />{l.origin === "erp" ? "Modifier" : "Enregistrer"}</Button>
            <Button size="sm" title="Couper en deux tranches" onClick={() => { const half = Math.floor(l.qty / 2); save.mutate(body(l.date, l.qty - half)); save.mutate({ ...body(l.date, half), line_id: null }); }}><Scissors /></Button>
            {(l.origin === "override" || l.origin === "free") && l.line_id && <Button size="sm" variant="ghost" title={l.order_id ? "Supprimer la ligne : la commande suit l'ERP" : "Supprimer la ligne libre"} onClick={() => del.mutate(l.line_id!)}><Trash2 /></Button>}
            {l.origin === "erp" && <Button size="sm" variant="ghost" title="Rien n'est attendu de cette commande" onClick={() => save.mutate(body(l.date, 0))}>0</Button>}
          </div>
        )}
        {(save.error || del.error) && <div className="error-box small">{((save.error ?? del.error) as Error).message}</div>}
      </td>
    </tr>
  );
}

/** Drawer editing the delivery plan of one article (all lines, or the lines of one day). */
export function PlanDrawer({ target, onClose }: { target: PlanTarget | null; onClose: () => void }) {
  const toast = useToast();
  const [free, setFree] = useState<{ date: string; qty: string; note: string } | null>(null);
  const add = useWrite((body: PlanLineIn) => api.put("/api/entries/plan", body), () => { toast.push("Ligne ajoutée", "success"); setFree(null); });
  const takeForecast = useWrite((o: OrderStateOut) => api.put("/api/entries/plan", { article_id: o.article_id, date: o.expected_date, qty: o.qty_open, order_id: o.order_id, supplier_id: o.supplier_id, note: "prévisionnel repris dans le plan" }), () => toast.push("Prévisionnel repris dans le plan", "success"));
  const dateLine = useWrite((p: { o: OrderStateOut; date: string }) => api.put("/api/entries/plan", { article_id: p.o.article_id, date: p.date, qty: p.o.qty_open, order_id: p.o.order_id, supplier_id: p.o.supplier_id, note: "commande non reçue datée dans le plan" }), () => toast.push("Commande datée dans le plan", "success"));
  const [dates, setDates] = useState<Record<string, string>>({});
  if (!target) return null;
  const { article_id, unit, asOf } = target;
  const lines = target.date ? target.lines.filter((l) => l.date === target.date) : target.lines;
  const notReceived = target.orders.filter((o) => o.status === "not_received");
  const forecasts = target.orders.filter((o) => o.status === "info" && (!target.date || o.expected_date === target.date));
  const supplier = target.orders[0]?.supplier_id ?? null;
  return (
    <Drawer open onClose={onClose} wide title={target.title}
      footer={<><span className="small subtle grow">Le plan ne modifie que le Scenario Plan ; le Scenario ERP suit l'ERP tel quel. Une ligne dont la date passe expire ; une commande ERP reçue ferme ses lignes.</span><Button onClick={onClose}>Fermer</Button></>}>
      <table className="tbl compact">
        <thead><tr><th>Origine</th><th>Commande</th><th>Date plan</th><th className="num">Quantité ({unit})</th><th>Commentaire</th><th /></tr></thead>
        <tbody>
          {lines.length === 0 && !free && <tr><td colSpan={6} className="subtle">Aucune ligne{target.date ? " ce jour-là" : ""}.</td></tr>}
          {lines.map((l) => <LineRow key={`${l.line_id ?? "x"}|${l.order_id ?? ""}|${l.date}|${l.origin}|${l.qty}`} l={l} unit={unit} asOf={asOf} onSaved={() => undefined} />)}
          {free && (
            <tr>
              <td><Badge tone="brand">libre</Badge></td><td className="subtle">—</td>
              <td><input className="input sm" type="date" min={asOf} value={free.date} onChange={(e) => setFree({ ...free, date: e.target.value })} aria-label="Date de la ligne libre" /></td>
              <td className="num"><input className="input sm" type="number" step="any" min={0} value={free.qty} style={{ width: 100, textAlign: "right" }} onChange={(e) => setFree({ ...free, qty: e.target.value })} aria-label="Quantité de la ligne libre" /></td>
              <td><input className="input sm" value={free.note} onChange={(e) => setFree({ ...free, note: e.target.value })} placeholder="dépannage, hors commande…" /></td>
              <td><Button size="sm" variant="primary" disabled={!free.date || !(Number(free.qty) > 0)} onClick={() => add.mutate({ article_id, date: free.date, qty: Number(free.qty), supplier_id: supplier, note: free.note })}><Check /></Button> <Button size="sm" variant="ghost" onClick={() => setFree(null)}>Annuler</Button></td>
            </tr>
          )}
        </tbody>
      </table>
      <div className="row" style={{ gap: 8 }}>
        <Button size="sm" onClick={() => setFree({ date: target.date && target.date >= asOf ? target.date : asOf, qty: "", note: "" })}><Plus />Ligne libre</Button>
      </div>
      {forecasts.length > 0 && (
        <div>
          <h4>Prévisionnel ERP{target.date ? " du jour" : ""}</h4>
          <table className="tbl compact">
            <thead><tr><th>Commande</th><th>Date ERP</th><th className="num">Quantité</th><th /></tr></thead>
            <tbody>{forecasts.map((o) => (
              <tr key={o.order_id}><td className="mono small">{o.order_id}</td><td>{fmtDate(o.expected_date)}</td><td className="num">{fmtQty(o.qty_open, unit)}</td>
                <td><Button size="sm" onClick={() => takeForecast.mutate(o)}>Reprendre dans le plan</Button></td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
      {!target.date && notReceived.length > 0 && (
        <div>
          <h4>Commandes ERP non reçues (hors stocks)</h4>
          <table className="tbl compact">
            <thead><tr><th>Commande</th><th>Date ERP</th><th className="num">Restant</th><th>Statut</th><th>Arrive encore le…</th></tr></thead>
            <tbody>{notReceived.map((o) => (
              <tr key={o.order_id}><td className="mono small">{o.order_id}<span className="sub">{ORDER_TYPE_LABELS[o.order_type]}</span></td><td>{fmtDate(o.expected_date)}<span className="sub" style={{ color: "var(--critical)" }}>{o.days_late} j</span></td>
                <td className="num">{fmtQty(o.qty_open, unit)}</td><td><Badge tone="critical">{ORDER_STATUS_LABELS[o.status]}</Badge></td>
                <td><div className="row" style={{ gap: 6 }}><input className="input sm" type="date" min={asOf} value={dates[o.order_id] ?? ""} onChange={(e) => setDates({ ...dates, [o.order_id]: e.target.value })} aria-label={`Date plan ${o.order_id}`} style={{ width: 150 }} />
                  <Button size="sm" variant="primary" disabled={!dates[o.order_id]} onClick={() => dateLine.mutate({ o, date: dates[o.order_id] })}>Dater</Button></div></td></tr>
            ))}</tbody>
          </table>
        </div>
      )}
    </Drawer>
  );
}
