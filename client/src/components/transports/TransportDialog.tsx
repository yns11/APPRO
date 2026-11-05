import { useState } from "react";
import { Plus, X } from "lucide-react";
import { api } from "@/lib/api";
import { useWrite } from "@/lib/queries";
import { Button, Drawer, useToast } from "@/components/ui";
import { DockPicker } from "@/components/DockPicker";
import { fmtDate, fmtQty } from "@/lib/format";
import type { PlanningSupplier, TransportPackOut, TransportSlotIn } from "@/lib/types";

const DEFAULT_TIME = "08:00";

/**
 * Manual creation (suppliers in manual, MAF shuttle) : the orders of the day with a tick each (the clicked
 * supplier's ones ticked), one or several trucks (date, slot, docks), carrier and note.
 */
export function TransportDialog({ maf, suppliers, date, initial, docks, slots, onClose, onCreated }: { maf: boolean; suppliers: PlanningSupplier[]; date: string; initial: Set<string>; docks: string[]; slots: string[]; onClose: () => void; onCreated: () => void }) {
  const toast = useToast();
  const first = suppliers.find((s) => s.orders.some((o) => initial.has(o.order_id))) ?? suppliers[0];
  const preset = (first?.docks ?? []).filter((x) => docks.includes(x));
  const [picked, setPicked] = useState<Set<string>>(initial);
  const [trucks, setTrucks] = useState<TransportSlotIn[]>([{ date, time: slots[2] ?? DEFAULT_TIME, docks: preset.length ? preset : [docks[0]] }]);
  const [carrier, setCarrier] = useState(maf ? "" : first?.carrier ?? "");
  const [note, setNote] = useState("");
  const rows = suppliers.flatMap((s) => s.orders.filter((o) => o.expected_date === date && !o.ignored).map((o) => ({ s, o })));
  const run = useWrite((body: { supplier_id?: string; maf?: boolean; order_ids: string[]; slots: TransportSlotIn[]; carrier: string; note: string }) => api.post<TransportPackOut>("/api/transports", body),
    (pack) => { toast.push(`${pack.transports.map((t) => t.transport_id).join(", ")} créé${pack.mail.to.length ? "" : " · aucun destinataire e-mail (Référentiel)"}`, "success"); onCreated(); });
  const update = (i: number, patch: Partial<TransportSlotIn>) => setTrucks((s) => s.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const ids = [...picked];
  const valid = ids.length > 0 && trucks.every((s) => s.date && /^([01]\d|2[0-3]):[0-5]\d$/.test(s.time) && s.docks.length > 0);
  return (
    <Drawer open onClose={onClose} title={maf ? <>Navette MAF · {fmtDate(date)}</> : <>Camion · {first?.supplier_id} <span className="muted">{first?.name}</span> · {fmtDate(date)}</>}
      footer={<><Button onClick={onClose}>Annuler</Button><Button variant="primary" disabled={!valid || run.isPending} onClick={() => run.mutate(maf ? { maf: true, order_ids: ids, slots: trucks, carrier, note } : { supplier_id: first.supplier_id, order_ids: ids, slots: trucks, carrier, note })}>{run.isPending ? "…" : `Créer ${trucks.length > 1 ? `${trucks.length} camions` : maf ? "la navette" : "le camion"}`}</Button></>}>
      <table className="tbl compact">
        <thead><tr><th /><th>Commande</th>{maf && <th>Fournisseur</th>}<th>Article</th><th className="num">Quantité</th></tr></thead>
        <tbody>{rows.map(({ s, o }) => <tr key={o.order_id}><td><input type="checkbox" checked={picked.has(o.order_id)} onChange={() => setPicked((p) => { const n = new Set(p); if (n.has(o.order_id)) n.delete(o.order_id); else n.add(o.order_id); return n; })} aria-label={o.ref || o.order_id} /></td><td className="mono small">{o.ref || o.order_id}</td>{maf && <td className="small">{s.supplier_id}</td>}<td><b>{o.article_id}</b></td><td className="num">{fmtQty(o.qty_open, o.unit)} {o.unit}</td></tr>)}</tbody>
      </table>
      <table className="tbl compact" style={{ marginTop: 12 }}>
        <thead><tr><th>Date</th><th>Créneau</th><th>Quais</th><th /></tr></thead>
        <tbody>{trucks.map((s, i) => (
          <tr key={i}>
            <td><input className="input sm" type="date" value={s.date} onChange={(e) => update(i, { date: e.target.value })} aria-label={`Date du camion ${i + 1}`} /></td>
            <td><input className="input sm" type="time" value={s.time} step={300} list="tp-slots" onChange={(e) => update(i, { time: e.target.value })} aria-label={`Heure du camion ${i + 1}`} /></td>
            <td><DockPicker all={docks} value={s.docks} onChange={(v) => update(i, { docks: v })} label={`Quais du camion ${i + 1}`} size="sm" /></td>
            <td>{trucks.length > 1 && <Button size="sm" variant="ghost" icon title="Retirer" onClick={() => setTrucks((x) => x.filter((_, j) => j !== i))}><X /></Button>}</td>
          </tr>))}</tbody>
      </table>
      <datalist id="tp-slots">{slots.map((s) => <option key={s} value={s} />)}</datalist>
      <Button size="sm" style={{ marginTop: 8 }} onClick={() => setTrucks((x) => [...x, { ...x[x.length - 1], docks: [...x[x.length - 1].docks] }])}><Plus />Un camion de plus</Button>
      <div className="grid cols-2" style={{ marginTop: 12 }}>
        <label className="field"><span>Transporteur</span><input className="input sm" value={carrier} onChange={(e) => setCarrier(e.target.value)} maxLength={120} /></label>
        <label className="field"><span>Note</span><input className="input sm" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} /></label>
      </div>
      {run.error && <div className="error-box">{(run.error as Error).message}</div>}
    </Drawer>
  );
}
