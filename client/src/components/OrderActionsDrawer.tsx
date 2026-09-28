import { useEffect, useState } from "react";
import { Plus, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useWrite } from "@/lib/queries";
import { Badge, Button, Drawer, Field, useToast } from "@/components/ui";
import { ACTION_KIND_LABELS, ORDER_STATUS_LABELS, ORDER_TYPE_LABELS, SOURCE_LABELS, fmtDate, fmtQty } from "@/lib/format";
import type { ActionIn, ActionKind, OrderStateOut } from "@/lib/types";

export interface OrderActionsTarget { title: string; orders: OrderStateOut[]; }

type Choice = "erp" | ActionKind;
interface Draft { choice: Choice; tranches: { date: string; qty: string }[]; note: string; }

function initial(o: OrderStateOut): Draft {
  if (o.action_kind === "reschedule") return { choice: "reschedule", tranches: o.tranches.map((t) => ({ date: t.date, qty: String(t.qty) })), note: o.note };
  if (o.action_kind === "cancel" || o.action_kind === "close") return { choice: o.action_kind, tranches: [{ date: o.expected_date, qty: String(o.qty_open) }], note: o.note };
  return { choice: "erp", tranches: [{ date: o.expected_date, qty: String(o.qty_open) }], note: "" };
}

export function statusTone(s: OrderStateOut["status"]): "ok" | "warning" | "critical" | "neutral" | "brand" {
  switch (s) {
    case "late": case "late_sim": return "critical";
    case "simulated": return "warning";
    case "cancelled": case "closed": return "neutral";
    default: return "ok";
  }
}

/** One order (delivery slot): ERP state, current action, editor of the action (simulated stock only). */
function OrderEditor({ o, onSaved }: { o: OrderStateOut; onSaved: () => void }) {
  const toast = useToast();
  const [d, setD] = useState<Draft>(() => initial(o));
  useEffect(() => setD(initial(o)), [o]);
  const save = useWrite((body: ActionIn) => api.put("/api/entries/actions", body), () => { toast.push("Action enregistrée", "success"); onSaved(); });
  const remove = useWrite((id: string) => api.del(`/api/entries/actions/${id}`), () => { toast.push("Action supprimée : la commande suit l'ERP"); onSaved(); });
  const covered = d.tranches.reduce((s, t) => s + (Number(t.qty) || 0), 0);
  const rest = o.qty_open - covered;
  const valid = d.choice !== "reschedule" || (d.tranches.length > 0 && d.tranches.every((t) => t.date && Number(t.qty) > 0));
  const submit = () => {
    if (d.choice === "erp") { if (o.action_id) remove.mutate(o.action_id); return; }
    save.mutate({ order_id: o.order_id, article_id: o.article_id, kind: d.choice, note: d.note, supplier_id: o.supplier_id,
      tranches: d.choice === "reschedule" ? d.tranches.map((t) => ({ date: t.date, qty: Number(t.qty) })) : [] });
  };
  return (
    <div className="order-editor">
      <div className="row" style={{ justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
        <div>
          <div className="mono" style={{ fontWeight: 600 }}>{o.order_id}</div>
          <div className="small subtle">{ORDER_TYPE_LABELS[o.order_type] ?? o.order_type} · {o.supplier_id ?? "fournisseur non précisé"} · {SOURCE_LABELS[o.source] ?? o.source}{o.note && !o.action_id ? ` · ${o.note}` : ""}</div>
          <div className="small">Date ERP <b>{fmtDate(o.expected_date)}</b> · restant <b>{fmtQty(o.qty_open, o.unit)}</b> / {fmtQty(o.qty_ordered, o.unit)} commandés
            {o.days_late > 0 && <span style={{ color: "var(--critical)" }}> · {o.days_late} j de retard</span>}
            {o.in_firm_layer && o.qty_expected !== o.qty_open && <span className="subtle"> · attendu aujourd'hui {fmtQty(o.qty_expected, o.unit)} après lettrage des réceptions du jour</span>}</div>
        </div>
        <Badge tone={statusTone(o.status)}>{ORDER_STATUS_LABELS[o.status]}</Badge>
      </div>
      {o.review && <div className="error-box" style={{ marginTop: 6 }}>À revoir : {o.review}</div>}
      {!o.in_firm_layer && (o.status === "late" || o.status === "late_sim") && (
        <p className="small subtle" style={{ margin: "6px 0 0" }}>Commande passée non reçue dans l'ERP : elle ne compte dans aucun stock. Saisir la date attendue si elle arrive encore, ou la clôturer si elle a été reçue par ailleurs ou ne viendra plus.</p>
      )}
      <div className="form-grid" style={{ marginTop: 10 }}>
        <Field label="Action (stock simulé seulement)" span2>
          <select className="select" value={d.choice} onChange={(e) => setD({ ...d, choice: e.target.value as Choice })}>
            <option value="erp">Aucune : suivre l'ERP{o.days_late > 0 && !o.in_firm_layer ? " (exclue, à qualifier)" : ""}</option>
            <option value="reschedule">{ACTION_KIND_LABELS.reschedule} (retard, livraison partielle, tranches)</option>
            <option value="cancel">{ACTION_KIND_LABELS.cancel} : le fournisseur ne livrera pas</option>
            <option value="close">{ACTION_KIND_LABELS.close} : reçue par ailleurs ou à ignorer</option>
          </select>
        </Field>
        {d.choice === "reschedule" && (
          <div className="span-2">
            <table className="tbl compact">
              <thead><tr><th>Livraison attendue le</th><th className="num">Quantité ({o.unit})</th><th /></tr></thead>
              <tbody>
                {d.tranches.map((t, i) => (
                  <tr key={i}>
                    <td><input className="input sm" type="date" value={t.date} onChange={(e) => setD({ ...d, tranches: d.tranches.map((x, k) => (k === i ? { ...x, date: e.target.value } : x)) })} /></td>
                    <td className="num"><input className="input sm" type="number" step="any" min={0} value={t.qty} style={{ width: 110, textAlign: "right" }} onChange={(e) => setD({ ...d, tranches: d.tranches.map((x, k) => (k === i ? { ...x, qty: e.target.value } : x)) })} /></td>
                    <td><Button size="xs" variant="ghost" title="Retirer la tranche" disabled={d.tranches.length === 1} onClick={() => setD({ ...d, tranches: d.tranches.filter((_, k) => k !== i) })}><Trash2 /></Button></td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="row" style={{ justifyContent: "space-between", marginTop: 6 }}>
              <Button size="sm" onClick={() => setD({ ...d, tranches: [...d.tranches, { date: d.tranches[d.tranches.length - 1]?.date ?? o.expected_date, qty: String(Math.max(rest, 0)) }] })}><Plus />Tranche</Button>
              <span className="small subtle">{Math.abs(rest) < 1e-6 ? "Quantité restante entièrement affectée" : rest > 0 ? `${fmtQty(rest, o.unit)} non affecté(s) : attendu(s) à la date ERP` : `${fmtQty(-rest, o.unit)} au-delà du restant : les tranches seront ramenées au restant`}</span>
            </div>
          </div>
        )}
        {d.choice !== "erp" && <Field label="Commentaire" span2><input className="input" value={d.note} onChange={(e) => setD({ ...d, note: e.target.value })} placeholder="Info fournisseur, référence du mail…" /></Field>}
      </div>
      <div className="row" style={{ justifyContent: "flex-end", gap: 8, marginTop: 8 }}>
        {o.action_id && d.choice !== "erp" && <Button variant="ghost" onClick={() => remove.mutate(o.action_id!)}>Supprimer l'action</Button>}
        <Button variant="primary" disabled={!valid || save.isPending || remove.isPending || (d.choice === "erp" && !o.action_id)} onClick={submit}>{save.isPending || remove.isPending ? "Enregistrement…" : "Enregistrer"}</Button>
      </div>
      {(save.error || remove.error) && <div className="error-box">{((save.error ?? remove.error) as Error).message}</div>}
    </div>
  );
}

/** Drawer listing the orders of one grid cell (or of one article) and editing their actions. */
export function OrderActionsDrawer({ target, onClose }: { target: OrderActionsTarget | null; onClose: () => void }) {
  return (
    <Drawer open={!!target} onClose={onClose} wide title={target?.title ?? ""}
      footer={<><span className="small subtle grow">Une action ne modifie que le stock simulé ; les stocks ferme et prévisionnel suivent l'ERP tel quel. Une commande garde son action jusqu'à sa réception, sa disparition de l'ERP ou sa clôture.</span><Button onClick={onClose}>Fermer</Button></>}>
      {target && target.orders.length === 0 && <p className="subtle">Aucune commande sur cette période.</p>}
      {target?.orders.map((o) => <OrderEditor key={o.order_id} o={o} onSaved={() => undefined} />)}
    </Drawer>
  );
}
