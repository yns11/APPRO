import { useState } from "react";
import { Download, Mail, X } from "lucide-react";
import { api } from "@/lib/api";
import { useWrite } from "@/lib/queries";
import { Badge, Button, useToast } from "@/components/ui";
import { DockPicker } from "@/components/DockPicker";
import { fmtDate, fmtQty } from "@/lib/format";
import { STATE_LABEL, dayLabel, mailtoOf, planOf, stateOf, weekShort, type TruckState } from "@/lib/transports";
import type { PlanningResponse, PlansOut, TransportOut, TransportPackOut } from "@/lib/types";

const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;
const TONE: Record<TruckState, "brand" | "ok" | "warning" | "critical" | "neutral"> = { plan: "brand", sent: "ok", resend: "warning", check: "critical", off: "neutral" };
type Mode = "view" | "move" | "split" | "note";

/**
 * The detail of the selected truck, at the right of the grid : day, slot, dock, plan, the actions (seen, move, split,
 * note, e-mail, CSV, cancel) and the orders on board.  Move, split and note open as small forms inside the panel.
 */
export function TruckPanel({ t, d, plans, sent, onClose }: { t: TransportOut; d: PlanningResponse; plans?: PlansOut; sent: Set<string>; onClose: () => void }) {
  const toast = useToast();
  const [mode, setMode] = useState<Mode>("view");
  const state = stateOf(t, sent);
  const plan = planOf(t, plans);
  const id = encodeURIComponent(t.transport_id);
  const seen = useWrite(() => api.post<TransportOut>(`/api/transports/${id}/seen`), () => toast.push("Camion vérifié", "success"));
  const cancel = useWrite(() => api.post<TransportOut[]>("/api/transports/cancel", { transport_ids: [t.transport_id] }), () => toast.push(`${t.transport_id} annulé${t.changed_since_sent || state === "sent" ? " · la semaine passe « à renvoyer »" : ""} ; retirez-le de l'outil de transport`, "info"));
  const mailOf = async () => {
    try {
      const pack = await api.get<TransportPackOut>(`/api/transports/${id}/pack`);
      if (!pack.mail.to.length) toast.push("E-mail sans destinataire : contact transport à renseigner", "info");
      window.location.href = mailtoOf(pack.mail);
    } catch (e) { toast.push((e as Error).message, "error"); }
  };
  const planText = plan ? (plan.sent_no ? `${weekShort(plan.week)} envoyé le ${plan.sent_at ? fmtDate(plan.sent_at) : ""} (n° ${plan.sent_no})` : `${weekShort(plan.week)} à envoyer`) : "au-delà de la semaine suivante";
  return (
    <aside className="tp-panel" aria-label={`Détail du camion ${t.transport_id}`}>
      <div className="tp-panel-head">
        <div>
          <div className="tp-panel-id">{t.transport_id}{t.pinned && <span className="subtle small" title="Touché à la main : l'ordonnanceur ne le déplace plus"> · épinglé</span>}</div>
          <div className="small subtle">{t.supplier_name} · {t.supplier_id}{t.carrier ? ` · via ${t.carrier}` : ""}</div>
        </div>
        <span className="row" style={{ gap: 6 }}><Badge tone={TONE[state]}>{STATE_LABEL[state]}</Badge><Button size="xs" variant="ghost" icon aria-label="Fermer le détail" onClick={onClose}><X /></Button></span>
      </div>
      <div className="tp-panel-body">
        {state === "check" && <div className="tp-why">À vérifier : {t.check_reason}</div>}
        {t.changed_since_sent && <div className="tp-why warn">✱ {t.change_note}</div>}
        <div className="tp-kv">
          <span>Jour</span><span><b>{dayLabel(t.date)}</b></span>
          <span>Créneau</span><span><b>{t.time || "–"}</b></span>
          <span>Quai</span><span><b>{t.docks.join(" / ")}</b></span>
          <span>Plan</span><span>{planText}</span>
          {t.note && <><span>Note</span><span>{t.note}</span></>}
        </div>
        {t.status !== "cancelled" && mode === "view" && (
          <div className="row" style={{ flexWrap: "wrap", gap: 6 }}>
            {t.check_reason && <Button size="sm" onClick={() => seen.mutate(undefined)}>Vu</Button>}
            <Button size="sm" onClick={() => setMode("move")}>Déplacer</Button>
            <Button size="sm" onClick={() => setMode("split")}>Scinder</Button>
            <Button size="sm" onClick={() => setMode("note")}>Note</Button>
            <Button size="sm" variant="ghost" title="E-mail de ce seul camion" onClick={mailOf}><Mail /></Button>
            <a className="btn ghost sm" href={api.downloadUrl(`/api/transports/${id}/atr.csv`)} download title="CSV ATR de ce camion"><Download /></a>
            <Button size="sm" variant="ghost" className="danger" onClick={() => { if (window.confirm(`Annuler le camion ${t.transport_id} ?${t.origin === "auto" ? "\nIl ne renaîtra que si une nouvelle commande apparaît ce jour-là." : ""}`)) cancel.mutate(undefined); }}>Annuler</Button>
          </div>
        )}
        {mode === "move" && <MoveForm t={t} d={d} onDone={() => setMode("view")} />}
        {mode === "split" && <SplitForm t={t} d={d} onDone={() => setMode("view")} />}
        {mode === "note" && <NoteForm t={t} onDone={() => setMode("view")} />}
        {mode === "view" && (
          <table className="tp-cmd" aria-label="Commandes à bord">
            <tbody>{t.orders.map((o) => <tr key={o.order_id}><td className="mono">{o.ref || o.order_id}</td><td>{o.article_id}</td><td className="num">{fmtQty(o.qty)}</td></tr>)}</tbody>
          </table>
        )}
      </div>
    </aside>
  );
}

function MoveForm({ t, d, onDone }: { t: TransportOut; d: PlanningResponse; onDone: () => void }) {
  const toast = useToast();
  const [date, setDate] = useState(t.date);
  const [time, setTime] = useState(t.time || d.slots[0] || "08:00");
  const [docks, setDocks] = useState<string[]>(t.docks);
  const run = useWrite((body: { date: string; time: string; docks: string[] }) => api.post<TransportOut>(`/api/transports/${encodeURIComponent(t.transport_id)}/move`, body),
    (out) => { toast.push(`${out.transport_id} déplacé au ${fmtDate(out.date)} ${out.time}, quai ${out.docks.join(" / ")}${out.changed_since_sent ? " · semaine à renvoyer" : ""}`, "success"); onDone(); });
  const valid = !!date && TIME_RE.test(time) && docks.length > 0;
  return (
    <form className="tp-form" onSubmit={(e) => { e.preventDefault(); if (valid) run.mutate({ date, time, docks }); }}>
      <div className="tp-form-title">Déplacer</div>
      <label className="field"><span>Jour</span><input className="input sm" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
      <label className="field"><span>Créneau</span>
        <span className="row" style={{ gap: 6 }}>
          <select className="select sm" value={d.slots.includes(time) ? time : ""} onChange={(e) => { if (e.target.value) setTime(e.target.value); }} aria-label="Créneau"><option value="">autre…</option>{d.slots.map((s) => <option key={s} value={s}>{s}</option>)}</select>
          <input className="input sm" type="time" value={time} step={300} onChange={(e) => setTime(e.target.value)} aria-label="Heure" style={{ width: 100 }} />
        </span></label>
      <div className="field"><span>Quais</span><DockPicker all={d.docks} value={docks} onChange={setDocks} label="Quais" size="sm" /></div>
      <div className="row" style={{ gap: 6 }}><Button size="sm" variant="primary" disabled={!valid || run.isPending}>{run.isPending ? "…" : "Déplacer"}</Button><Button size="sm" variant="ghost" onClick={onDone}>Annuler</Button></div>
      {run.error && <div className="error-box">{(run.error as Error).message}</div>}
    </form>
  );
}

/** Every truck is splittable : whole orders, or a quantity of an order (a single-order truck splits by quantity). */
function SplitForm({ t, d, onDone }: { t: TransportOut; d: PlanningResponse; onDone: () => void }) {
  const toast = useToast();
  const [qty, setQty] = useState<Record<string, string>>(() => Object.fromEntries(t.orders.map((o) => [o.order_id, t.orders.length === 1 ? String(Math.round(o.qty / 2)) : ""])));
  const [time, setTime] = useState("");
  const [docks, setDocks] = useState<string[]>(t.docks);
  const run = useWrite((body: { items: { order_id: string; qty: number | null }[]; time?: string; docks: string[] }) => api.post<TransportOut[]>(`/api/transports/${encodeURIComponent(t.transport_id)}/split`, body),
    (out) => { toast.push(`${out[1].transport_id} créé à ${out[1].time} ; ${out[0].transport_id} garde ${out[0].orders.length} commande(s)`, "success"); onDone(); });
  const items = t.orders.flatMap((o) => { const v = qty[o.order_id]?.trim(); if (!v) return []; const n = Number(v.replace(",", ".")); return Number.isFinite(n) && n > 0 && n <= o.qty ? [{ order_id: o.order_id, qty: n >= o.qty ? null : n }] : []; });
  const bad = t.orders.some((o) => { const v = qty[o.order_id]?.trim(); if (!v) return false; const n = Number(v.replace(",", ".")); return !(Number.isFinite(n) && n > 0 && n <= o.qty); });
  const all = items.length === t.orders.length && items.every((i) => i.qty === null);
  const valid = items.length > 0 && !bad && !all && docks.length > 0;
  return (
    <form className="tp-form" onSubmit={(e) => { e.preventDefault(); if (valid) run.mutate({ items, time: time || undefined, docks }); }}>
      <div className="tp-form-title">Scinder · quantité qui part dans le second camion</div>
      <table className="tp-cmd">
        <tbody>{t.orders.map((o) => <tr key={o.order_id}><td className="mono">{o.ref || o.order_id}</td><td className="num subtle">{fmtQty(o.qty)}</td><td style={{ width: 90 }}><input className="input sm num" inputMode="decimal" value={qty[o.order_id] ?? ""} placeholder="0" aria-label={`Quantité de ${o.ref || o.order_id}`} onChange={(e) => setQty((q) => ({ ...q, [o.order_id]: e.target.value }))} /></td></tr>)}</tbody>
      </table>
      <label className="field"><span>Créneau</span><select className="select sm" value={time} onChange={(e) => setTime(e.target.value)} aria-label="Créneau du second camion"><option value="">le plus proche libre</option>{d.slots.map((s) => <option key={s} value={s}>{s}</option>)}</select></label>
      <div className="field"><span>Quais</span><DockPicker all={d.docks} value={docks} onChange={setDocks} label="Quais du second camion" size="sm" /></div>
      {all && <div className="small" style={{ color: "var(--warning-fg)" }}>Tout partirait : il resterait un camion vide, déplacez-le plutôt.</div>}
      <div className="row" style={{ gap: 6 }}><Button size="sm" variant="primary" disabled={!valid || run.isPending}>{run.isPending ? "…" : "Créer le second camion"}</Button><Button size="sm" variant="ghost" onClick={onDone}>Annuler</Button></div>
      {run.error && <div className="error-box">{(run.error as Error).message}</div>}
    </form>
  );
}

function NoteForm({ t, onDone }: { t: TransportOut; onDone: () => void }) {
  const toast = useToast();
  const [note, setNote] = useState(t.note);
  const run = useWrite((body: { note: string }) => api.post<TransportOut>(`/api/transports/${encodeURIComponent(t.transport_id)}/note`, body), () => { toast.push("Note enregistrée", "success"); onDone(); });
  return (
    <form className="tp-form" onSubmit={(e) => { e.preventDefault(); run.mutate({ note }); }}>
      <div className="tp-form-title">Note pour le fournisseur</div>
      <input className="input sm" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="Ex. : prévenir le quai 30 min avant l'arrivée" aria-label="Note" />
      <div className="row" style={{ gap: 6 }}><Button size="sm" variant="primary" disabled={run.isPending}>Enregistrer</Button><Button size="sm" variant="ghost" onClick={onDone}>Annuler</Button></div>
    </form>
  );
}
