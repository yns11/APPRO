import { useEffect, useState } from "react";
import { Copy, Download, Eye, FileText, ListChecks, Lock, MoveRight, RotateCcw, Scissors, StickyNote, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { Button, Drawer, useToast } from "@/components/ui";
import { DockPicker } from "@/components/DockPicker";
import { KindIcon } from "@/components/transports/icons";
import { ConflictBox, SlotField, busyMap, useSlotWrite } from "@/components/transports/SlotField";
import { ShuttleDialog } from "@/components/transports/ShuttleDialog";
import { fmtDate, fmtDateTime } from "@/lib/format";
import { articleLines, copyText, dayLabel, isShuttle, qtyLabel, splitItems, toneOf, who, type ArticleLine } from "@/lib/transports";
import type { BoardResponse, TransportOut } from "@/lib/types";

export type Action = "move" | "split" | "note" | "lines";
export interface MenuAt { t: TransportOut; x: number; y: number }

/**
 * The menu a click on a truck opens, where the click happened : the truck (identifier, who it belongs to, day, slot,
 * docks, its lines as article + designation + quantity — never an order number —, why to check, note), then the
 * actions.  Your trucks and the shuttles : copy the identifier, seen, move, split, note, lines and PDF of a shuttle,
 * CSV, cancel ; a colleague's truck : copy and CSV only ; a cancelled truck : restore and copy.
 */
export function TruckMenu({ menu, d, onClose }: { menu: MenuAt | null; d: BoardResponse; onClose: () => void }) {
  const toast = useToast();
  const { config } = usePerimeter();
  const canWrite = !!config?.access?.can_write;
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
  const tone = t ? toneOf(t) : "mine";
  const own = !!t && t.editable && canWrite;      // the rights follow the e-mail ; the colour (tone) follows the portfolio shown
  return (
    <>
      {menu && t && (
        <div className="tp-menu" style={{ left: Math.min(menu.x, window.innerWidth - 310), top: Math.min(menu.y, window.innerHeight - 380) }} role="menu" aria-label={`Actions sur ${t.transport_id}`}>
          <div className="hd">
            <div className="row" style={{ justifyContent: "space-between", alignItems: "baseline" }}>
              <b className={`mono ${tone === "off" ? "off" : ""}`}><KindIcon t={t} size={13} /> {t.transport_id}</b>
              <span className={`tp-tag ${tone}`}>{tagOf(t, d)}</span>
            </div>
            {!isShuttle(t) && <div className="small">{t.supplier_name || t.supplier_id} · <span className="mono">{t.supplier_id}</span>{t.carrier ? ` · via ${t.carrier}` : ""}</div>}
            <div className="small"><b>{dayLabel(t.date)}</b> · {t.time || "–"} · {isShuttle(t) ? <>aller quai {t.docks.join(" / ")}{t.return_trip ? ` · retour quai ${(t.return_docks.length ? t.return_docks : t.docks).join(" / ")}` : ""}</> : <>quai {t.docks.join(" / ")}</>}</div>
            <Lines t={t} />
            {t.check_reason && <div className="small" style={{ color: "var(--critical-fg)" }}>{t.editable ? "À vérifier" : "À vérifier chez son approvisionneur"} : {t.check_reason}</div>}
            {t.note && <div className="small">Note : {t.note}</div>}
            {tone === "off" && !isShuttle(t) && t.mode !== "ev" && t.mode !== "ponctuel" && <div className="small subtle">Ses commandes attendent : aucun camion ne renaît tant qu'une nouvelle commande n'apparaît pas ce jour-là.</div>}
          </div>
          {tone === "off" && own && <RestoreItem t={t} onClose={onClose} />}
          <button type="button" role="menuitem" onClick={() => copy(t)}><Copy />Copier l'identifiant</button>
          {tone !== "off" && own && <>
            {t.check_reason && <button type="button" role="menuitem" onClick={() => { seen.mutate(t.transport_id); onClose(); }}><Eye />Vu, rien à changer</button>}
            {isShuttle(t) && <button type="button" role="menuitem" onClick={() => { window.open(api.downloadUrl(`/api/transports/${encodeURIComponent(t.transport_id)}/navette.pdf`), "_blank"); onClose(); }}><FileText />PDF de la navette</button>}
            <button type="button" role="menuitem" onClick={() => { setAction({ kind: "move", t }); onClose(); }}><MoveRight />Déplacer (jour, créneau, quais)</button>
            {t.orders.length > 0 && !isShuttle(t) && <button type="button" role="menuitem" onClick={() => { setAction({ kind: "split", t }); onClose(); }}><Scissors />Scinder en deux camions</button>}
            {isShuttle(t) && <button type="button" role="menuitem" onClick={() => { setAction({ kind: "lines", t }); onClose(); }}><ListChecks />Modifier les lignes</button>}
            <button type="button" role="menuitem" onClick={() => { setAction({ kind: "note", t }); onClose(); }}><StickyNote />Note</button>
          </>}
          {tone !== "off" && <button type="button" role="menuitem" onClick={() => { window.location.href = api.downloadUrl(`/api/transports/${encodeURIComponent(t.transport_id)}/atr.csv`); onClose(); }}><Download />CSV ATR de ce camion</button>}
          {tone !== "off" && own && <button type="button" role="menuitem" className="danger" onClick={() => { onClose(); if (window.confirm(`Annuler ${isShuttle(t) ? "la navette" : "le camion"} ${t.transport_id} ?${t.orders.length && !isShuttle(t) ? "\nUn camion de la même famille ce jour-là reprend ses commandes ; sinon le jour reste sans camion tant qu'aucune nouvelle commande n'apparaît." : ""}`)) cancel.mutate(t.transport_id); }}><Trash2 />Annuler {isShuttle(t) ? "la navette" : "le camion"}</button>}
          {tone !== "off" && !own && canWrite && <div className="off-hint"><Lock />Déplacer, scinder, annuler : réservés à son approvisionneur (ou à un manager)</div>}
        </div>
      )}
      {action?.kind === "move" && <MoveDrawer t={action.t} d={d} onClose={() => setAction(null)} />}
      {action?.kind === "split" && <SplitDrawer t={action.t} d={d} onClose={() => setAction(null)} />}
      {action?.kind === "note" && <NoteDrawer t={action.t} onClose={() => setAction(null)} />}
      {action?.kind === "lines" && <ShuttleDialog d={d} edit={action.t} onClose={() => setAction(null)} />}
    </>
  );
}

function tagOf(t: TransportOut, d: BoardResponse): string {
  if (t.status === "cancelled") return `annulé par ${who(t.cancelled_by, d.people)}${t.cancelled_at ? ` · ${fmtDateTime(t.cancelled_at).slice(11)}` : ""}`;
  if (isShuttle(t)) return `navette${t.return_trip ? " A/R" : ""} · créée par ${who(t.created_by, d.people)}`;
  if (!t.mine) return t.editable ? "autre carnet" : "autre approvisionneur";
  if (t.check_reason) return "à vérifier";
  return t.mode === "ev" ? `retour EV · ${who(t.created_by, d.people)}` : t.mode === "ponctuel" ? `ponctuel · ${who(t.created_by, d.people)}` : t.pinned ? "automatique · touché à la main" : t.note ? "automatique · annoté" : "automatique";
}

/** The lines of a truck : article, designation and quantity ; the supplier instead of the designation on a shuttle ; the typed lines after. */
function Lines({ t }: { t: TransportOut }) {
  const lines = articleLines(t);
  if (!lines.length && !t.lines.length) return null;
  return (
    <div className="tp-lines">
      {lines.map((l) => <div key={`${l.supplier_id}|${l.article_id}`} className="ln"><span className="a"><b>{l.article_id}</b> <span className="s">{isShuttle(t) ? l.supplier_name || l.supplier_id : l.designation}</span></span><span className="q">{qtyLabel(l.qty, l.unit)}</span></div>)}
      {t.lines.map((l) => <div key={l.id ?? `${l.leg}-${l.article}`} className="ln"><span className="a"><b>{l.article}</b> <span className="s">{l.leg === "retour" ? "retour · " : ""}{l.designation || "saisi"}</span></span><span className="q">{l.qty}</span></div>)}
    </div>
  );
}

/** Restore a cancelled truck as it was ; its slot may be occupied since : the same question as everywhere (take the free slot = restore forced then move, or force). */
function RestoreItem({ t, onClose }: { t: TransportOut; onClose: () => void }) {
  const toast = useToast();
  const run = useSlotWrite((force: boolean) => api.post<TransportOut>(`/api/transports/${encodeURIComponent(t.transport_id)}/restore`, { force }), (out) => { toast.push(`${out.transport_id} rétabli à ${out.time}`, "success"); onClose(); });
  return (
    <>
      <button type="button" role="menuitem" onClick={() => run.mutate(false)}><RotateCcw />Rétablir {isShuttle(t) ? "la navette" : "le camion"}</button>
      {run.conflict && <Drawer open onClose={() => { run.clearConflict(); onClose(); }} title={<>Rétablir · <span className="mono">{t.transport_id}</span></>}>
        <p className="small">Le créneau du camion annulé ({t.time}, quai {t.docks.join(" / ")}) est occupé depuis. Forcez, puis déplacez-le depuis son menu ; ou laissez-le annulé.</p>
        <ConflictBox check={{ ...run.conflict, proposed: null }} onTake={() => undefined} onForce={() => run.mutate(true)} pending={run.isPending} />
      </Drawer>}
    </>
  );
}

function MoveDrawer({ t, d, onClose }: { t: TransportOut; d: BoardResponse; onClose: () => void }) {
  const toast = useToast();
  const [date, setDate] = useState(t.date);
  const [time, setTime] = useState(t.time || d.day_slots[2] || "07:00");
  const [docks, setDocks] = useState<string[]>(t.docks);
  const run = useSlotWrite((body: { date: string; time: string; docks: string[]; force?: boolean }) => api.post<TransportOut>(`/api/transports/${encodeURIComponent(t.transport_id)}/move`, body),
    (out) => { toast.push(`${out.transport_id} déplacé au ${fmtDate(out.date)} ${out.time}, quai ${out.docks.join(" / ")}`, "success"); onClose(); });
  const valid = !!date && /^([01]\d|2[0-3]):[0-5]\d$/.test(time) && docks.length > 0;
  const changed = date !== t.date || time !== t.time || docks.join(";") !== t.docks.join(";");
  return (
    <Drawer open onClose={onClose} title={<>Déplacer · <span className="mono">{t.transport_id}</span></>}
      footer={<><Button onClick={onClose}>Annuler</Button><Button variant="primary" disabled={!valid || !changed || run.isPending || !!run.conflict} onClick={() => run.mutate({ date, time, docks })}>{run.isPending ? "…" : "Déplacer"}</Button></>}>
      <p className="small subtle" style={{ marginTop: 0 }}>{t.supplier_name || t.supplier_id} · {dayLabel(t.date)} {t.time} · quai {t.docks.join(" / ")}</p>
      <SlotField date={date} time={time} docks={docks} onDate={(v) => { setDate(v); run.clearConflict(); }} onTime={(v) => { setTime(v); run.clearConflict(); }} onDocks={(v) => { setDocks(v); run.clearConflict(); }}
        daySlots={d.day_slots} allDocks={d.docks} busy={busyMap(d.transports, date, docks, d.day_slots, t.transport_id)} />
      {run.conflict && <ConflictBox check={run.conflict} onTake={(x) => { setTime(x); run.mutate({ date, time: x, docks }); }} onForce={() => run.mutate({ date, time, docks, force: true })} pending={run.isPending} />}
      <p className="small subtle">Un camion déplacé garde ses commandes, même si l'ERP change leur date. {d.capacity} camion{d.capacity > 1 ? "s" : ""} par quai et par créneau, {d.hard_max} au plus en forçant.</p>
    </Drawer>
  );
}

/** Every truck is splittable : whole article lines, or a quantity of a line (the orders of the line give first in first out). */
function SplitDrawer({ t, d, onClose }: { t: TransportOut; d: BoardResponse; onClose: () => void }) {
  const toast = useToast();
  const lines = articleLines(t);
  const [qty, setQty] = useState<Record<string, string>>(() => Object.fromEntries(lines.map((l) => [key(l), lines.length === 1 ? String(Math.round(l.qty / 2)) : ""])));
  const [time, setTime] = useState("");
  const [docks, setDocks] = useState<string[]>(t.docks);
  const run = useSlotWrite((body: { items: { order_id: string; qty: number | null }[]; time?: string; docks: string[]; force?: boolean }) => api.post<TransportOut[]>(`/api/transports/${encodeURIComponent(t.transport_id)}/split`, body),
    (out) => { toast.push(`${out[1].transport_id} créé à ${out[1].time} ; ${out[0].transport_id} garde ${articleLines(out[0]).length} article(s)`, "success"); onClose(); });
  const parsed = lines.map((l) => { const v = qty[key(l)]?.trim(); if (!v) return { l, n: 0, bad: false }; const n = Number(v.replace(",", ".")); return { l, n, bad: !(Number.isFinite(n) && n > 0 && n <= l.qty + 1e-9) }; });
  const items = parsed.flatMap(({ l, n, bad }) => (n > 0 && !bad ? splitItems(l, n) : []));
  const bad = parsed.some((x) => x.bad);
  const all = parsed.every(({ l, n }) => n >= l.qty - 1e-9) && parsed.length > 0;
  const valid = items.length > 0 && !bad && !all && docks.length > 0;
  const body = (force?: boolean) => ({ items, time: time || undefined, docks, force });
  return (
    <Drawer open onClose={onClose} title={<>Scinder · <span className="mono">{t.transport_id}</span></>}
      footer={<><Button onClick={onClose}>Annuler</Button><Button variant="primary" disabled={!valid || run.isPending || !!run.conflict} onClick={() => run.mutate(body())}>{run.isPending ? "…" : "Créer le second camion"}</Button></>}>
      <div className="tp-form">
        <div className="tp-form-title">Quantité qui part dans le second camion</div>
        <table className="tp-cmd">
          <tbody>{lines.map((l) => <tr key={key(l)}><td><b>{l.article_id}</b><div className="small subtle">{l.designation}</div></td><td className="num subtle">{qtyLabel(l.qty, l.unit)}</td><td style={{ width: 90 }}><input className="input sm num" inputMode="decimal" value={qty[key(l)] ?? ""} placeholder="0" aria-label={`Quantité de ${l.article_id}`} onChange={(e) => { setQty((q) => ({ ...q, [key(l)]: e.target.value })); run.clearConflict(); }} /></td></tr>)}</tbody>
        </table>
        <label className="field"><span>Créneau du second camion</span><select className="select sm" value={time} onChange={(e) => { setTime(e.target.value); run.clearConflict(); }} aria-label="Créneau du second camion"><option value="">le plus proche libre</option>{d.day_slots.map((s) => <option key={s} value={s}>{s}</option>)}</select></label>
        <div className="field"><span>Quais du second camion</span><DockPicker all={d.docks} value={docks} onChange={(v) => { setDocks(v); run.clearConflict(); }} label="Quais du second camion" size="sm" /></div>
        {run.conflict && <ConflictBox check={run.conflict} onTake={(x) => { setTime(x); run.mutate({ ...body(), time: x }); }} onForce={() => run.mutate(body(true))} pending={run.isPending} />}
        {all && <div className="small" style={{ color: "var(--warning-fg)" }}>Tout partirait : il resterait un camion vide, déplacez-le plutôt.</div>}
        <p className="small subtle">Même numéro, lettre suivante ({t.transport_id.slice(0, 7)}B, C…) ; annuler l'un rend ses commandes à l'autre.</p>
      </div>
    </Drawer>
  );
}
const key = (l: ArticleLine) => `${l.supplier_id}|${l.article_id}`;

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
