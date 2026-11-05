import { useEffect, useState } from "react";
import { Copy, Download, Eye, MoveRight, Scissors, StickyNote, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useWrite } from "@/lib/queries";
import { Button, Drawer, useToast } from "@/components/ui";
import { DockPicker } from "@/components/DockPicker";
import { fmtDate, fmtQty } from "@/lib/format";
import { copyText, dayLabel, kindOf, stateOf } from "@/lib/transports";
import type { PlanningResponse, TransportOut } from "@/lib/types";

const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;
export type Action = "move" | "split" | "note";
export interface MenuAt { t: TransportOut; x: number; y: number }

/**
 * The menu a click on a truck opens, where the click happened : the truck in two lines (day, slot, dock, orders,
 * why to check, note), then the actions : copy the identifier (to paste in an e-mail), seen, move, split, note,
 * CSV, cancel.  Move, split and note open a small drawer.
 */
export function TruckMenu({ menu, d, onClose }: { menu: MenuAt | null; d: PlanningResponse; onClose: () => void }) {
  const toast = useToast();
  const [action, setAction] = useState<{ kind: Action; t: TransportOut } | null>(null);
  useEffect(() => {
    if (!menu) return;
    const down = (e: MouseEvent) => { if (!(e.target as HTMLElement).closest(".tp-menu")) onClose(); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("mousedown", down); document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", down); document.removeEventListener("keydown", esc); };
  }, [menu, onClose]);
  const seen = useWrite((id: string) => api.post<TransportOut>(`/api/transports/${encodeURIComponent(id)}/seen`), () => toast.push("Camion vérifié", "success"));
  const cancel = useWrite((id: string) => api.del<TransportOut>(`/api/transports/${encodeURIComponent(id)}`), (out) => toast.push(`${out.transport_id} annulé ; retirez-le de l'outil de transport`, "info"));
  const copy = async (t: TransportOut) => { toast.push((await copyText(t.transport_id)) ? `${t.transport_id} copié` : `Copie impossible : ${t.transport_id}`, "success"); onClose(); };
  const t = menu?.t;
  const state = t ? stateOf(t) : "plan";
  return (
    <>
      {menu && t && (
        <div className="tp-menu" style={{ left: Math.min(menu.x, window.innerWidth - 300), top: Math.min(menu.y, window.innerHeight - 360) }} role="menu" aria-label={`Actions sur ${t.transport_id}`}>
          <div className="hd">
            <div className="row" style={{ justifyContent: "space-between" }}><b className="mono">{t.transport_id}</b><span className={`tp-dot ${state}`}>{state === "check" ? "à vérifier" : t.status === "cancelled" ? "annulé" : t.origin === "auto" ? "auto" : kindOf(t) ?? "manuel"}{t.pinned && t.origin === "auto" ? " · épinglé" : ""}</span></div>
            <div className="small">{t.supplier_name}{t.carrier ? ` · via ${t.carrier}` : ""}</div>
            <div className="small"><b>{dayLabel(t.date)}</b> · {t.time || "–"} · quai {t.docks.join(" / ")}</div>
            {t.orders.length > 0 && <div className="small subtle ell">{t.orders.length} commande(s) : {t.orders.map((o) => `${o.ref || o.order_id} ${o.article_id} ${fmtQty(o.qty)}`).join(" ; ")}</div>}
            {t.check_reason && <div className="small" style={{ color: "var(--critical-fg)" }}>À vérifier : {t.check_reason}</div>}
            {t.note && <div className="small">Note : {t.note}</div>}
          </div>
          <button type="button" role="menuitem" onClick={() => copy(t)}><Copy />Copier l'identifiant</button>
          {t.status !== "cancelled" && <>
            {t.check_reason && <button type="button" role="menuitem" onClick={() => { seen.mutate(t.transport_id); onClose(); }}><Eye />Vu, rien à changer</button>}
            <button type="button" role="menuitem" onClick={() => { setAction({ kind: "move", t }); onClose(); }}><MoveRight />Déplacer (jour, créneau, quais)</button>
            {t.orders.length > 0 && <button type="button" role="menuitem" onClick={() => { setAction({ kind: "split", t }); onClose(); }}><Scissors />Scinder en deux camions</button>}
            <button type="button" role="menuitem" onClick={() => { setAction({ kind: "note", t }); onClose(); }}><StickyNote />Note</button>
          </>}
          <button type="button" role="menuitem" onClick={() => { window.location.href = api.downloadUrl(`/api/transports/${encodeURIComponent(t.transport_id)}/atr.csv`); onClose(); }}><Download />CSV ATR de ce camion</button>
          {t.status !== "cancelled" && <button type="button" role="menuitem" className="danger" onClick={() => { onClose(); if (window.confirm(`Annuler le camion ${t.transport_id} ?${t.origin === "auto" ? "\nUn camion de la même famille ce jour-là reprend ses commandes ; sinon le jour reste sans camion tant qu'aucune nouvelle commande n'apparaît." : ""}`)) cancel.mutate(t.transport_id); }}><Trash2 />Annuler le camion</button>}
        </div>
      )}
      {action?.kind === "move" && <MoveDrawer t={action.t} d={d} onClose={() => setAction(null)} />}
      {action?.kind === "split" && <SplitDrawer t={action.t} d={d} onClose={() => setAction(null)} />}
      {action?.kind === "note" && <NoteDrawer t={action.t} onClose={() => setAction(null)} />}
    </>
  );
}

function MoveDrawer({ t, d, onClose }: { t: TransportOut; d: PlanningResponse; onClose: () => void }) {
  const toast = useToast();
  const [date, setDate] = useState(t.date);
  const [time, setTime] = useState(t.time || d.slots[0] || "08:00");
  const [docks, setDocks] = useState<string[]>(t.docks);
  const run = useWrite((body: { date: string; time: string; docks: string[] }) => api.post<TransportOut>(`/api/transports/${encodeURIComponent(t.transport_id)}/move`, body),
    (out) => { toast.push(`${out.transport_id} déplacé au ${fmtDate(out.date)} ${out.time}, quai ${out.docks.join(" / ")}`, "success"); onClose(); });
  const valid = !!date && TIME_RE.test(time) && docks.length > 0;
  return (
    <Drawer open onClose={onClose} title={<>Déplacer · <span className="mono">{t.transport_id}</span></>}
      footer={<><Button onClick={onClose}>Annuler</Button><Button variant="primary" disabled={!valid || run.isPending} onClick={() => run.mutate({ date, time, docks })}>{run.isPending ? "…" : "Déplacer"}</Button></>}>
      <div className="tp-form">
        <label className="field"><span>Jour</span><input className="input sm" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
        <label className="field"><span>Créneau</span>
          <span className="row" style={{ gap: 6 }}>
            <select className="select sm" value={d.slots.includes(time) ? time : ""} onChange={(e) => { if (e.target.value) setTime(e.target.value); }} aria-label="Créneau"><option value="">autre…</option>{d.slots.map((s) => <option key={s} value={s}>{s}</option>)}</select>
            <input className="input sm" type="time" value={time} step={300} onChange={(e) => setTime(e.target.value)} aria-label="Heure" style={{ width: 100 }} />
          </span></label>
        <div className="field"><span>Quais</span><DockPicker all={d.docks} value={docks} onChange={setDocks} label="Quais" size="sm" /></div>
        <p className="small subtle">Le camion est épinglé : l'ordonnanceur ne le déplace plus.</p>
        {run.error && <div className="error-box">{(run.error as Error).message}</div>}
      </div>
    </Drawer>
  );
}

/** Every truck is splittable : whole orders, or a quantity of an order (a single-order truck splits by quantity). */
function SplitDrawer({ t, d, onClose }: { t: TransportOut; d: PlanningResponse; onClose: () => void }) {
  const toast = useToast();
  const [qty, setQty] = useState<Record<string, string>>(() => Object.fromEntries(t.orders.map((o) => [o.order_id, t.orders.length === 1 ? String(Math.round(o.qty / 2)) : ""])));
  const [time, setTime] = useState("");
  const [docks, setDocks] = useState<string[]>(t.docks);
  const run = useWrite((body: { items: { order_id: string; qty: number | null }[]; time?: string; docks: string[] }) => api.post<TransportOut[]>(`/api/transports/${encodeURIComponent(t.transport_id)}/split`, body),
    (out) => { toast.push(`${out[1].transport_id} créé à ${out[1].time} ; ${out[0].transport_id} garde ${out[0].orders.length} commande(s)`, "success"); onClose(); });
  const items = t.orders.flatMap((o) => { const v = qty[o.order_id]?.trim(); if (!v) return []; const n = Number(v.replace(",", ".")); return Number.isFinite(n) && n > 0 && n <= o.qty ? [{ order_id: o.order_id, qty: n >= o.qty ? null : n }] : []; });
  const bad = t.orders.some((o) => { const v = qty[o.order_id]?.trim(); if (!v) return false; const n = Number(v.replace(",", ".")); return !(Number.isFinite(n) && n > 0 && n <= o.qty); });
  const all = items.length === t.orders.length && items.every((i) => i.qty === null);
  const valid = items.length > 0 && !bad && !all && docks.length > 0;
  return (
    <Drawer open onClose={onClose} title={<>Scinder · <span className="mono">{t.transport_id}</span></>}
      footer={<><Button onClick={onClose}>Annuler</Button><Button variant="primary" disabled={!valid || run.isPending} onClick={() => run.mutate({ items, time: time || undefined, docks })}>{run.isPending ? "…" : "Créer le second camion"}</Button></>}>
      <div className="tp-form">
        <div className="tp-form-title">Quantité qui part dans le second camion</div>
        <table className="tp-cmd">
          <tbody>{t.orders.map((o) => <tr key={o.order_id}><td className="mono">{o.ref || o.order_id}</td><td>{o.article_id}</td><td className="num subtle">{fmtQty(o.qty)}</td><td style={{ width: 90 }}><input className="input sm num" inputMode="decimal" value={qty[o.order_id] ?? ""} placeholder="0" aria-label={`Quantité de ${o.ref || o.order_id}`} onChange={(e) => setQty((q) => ({ ...q, [o.order_id]: e.target.value }))} /></td></tr>)}</tbody>
        </table>
        <label className="field"><span>Créneau du second camion</span><select className="select sm" value={time} onChange={(e) => setTime(e.target.value)} aria-label="Créneau du second camion"><option value="">le plus proche libre</option>{d.slots.map((s) => <option key={s} value={s}>{s}</option>)}</select></label>
        <div className="field"><span>Quais du second camion</span><DockPicker all={d.docks} value={docks} onChange={setDocks} label="Quais du second camion" size="sm" /></div>
        {all && <div className="small" style={{ color: "var(--warning-fg)" }}>Tout partirait : il resterait un camion vide, déplacez-le plutôt.</div>}
        <p className="small subtle">Même numéro, lettre suivante ({t.transport_id.slice(0, 7)}B, C…) ; annuler l'un rend ses commandes à l'autre.</p>
        {run.error && <div className="error-box">{(run.error as Error).message}</div>}
      </div>
    </Drawer>
  );
}

function NoteDrawer({ t, onClose }: { t: TransportOut; onClose: () => void }) {
  const toast = useToast();
  const [note, setNote] = useState(t.note);
  const run = useWrite((body: { note: string }) => api.post<TransportOut>(`/api/transports/${encodeURIComponent(t.transport_id)}/note`, body), () => { toast.push("Note enregistrée", "success"); onClose(); });
  return (
    <Drawer open onClose={onClose} title={<>Note · <span className="mono">{t.transport_id}</span></>}
      footer={<><Button onClick={onClose}>Annuler</Button><Button variant="primary" disabled={run.isPending} onClick={() => run.mutate({ note })}>Enregistrer</Button></>}>
      <div className="tp-form">
        <input className="input sm" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="Ex. : prévenir le quai 30 min avant l'arrivée" aria-label="Note" autoFocus />
      </div>
    </Drawer>
  );
}
