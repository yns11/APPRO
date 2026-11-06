import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeftRight, ArrowRight, Plus, X } from "lucide-react";
import { api } from "@/lib/api";
import { useWrite } from "@/lib/queries";
import { Button, Drawer, useToast } from "@/components/ui";
import { DockPicker } from "@/components/DockPicker";
import { ConflictBox, SlotField, busyMap, useSlotWrite } from "@/components/transports/SlotField";
import { fmtDate, fmtQty } from "@/lib/format";
import { cleanLines, copyText, emptyLine, mondayOf } from "@/lib/transports";
import type { BoardResponse, PlanningResponse, ShuttleIn, TransportLineIn, TransportLinesIn, TransportOut, TransportPackOut } from "@/lib/types";

/**
 * The MAF shuttle : the day (next working day first), the slot and the docks of the outward leg, the MAF orders of that
 * day to tick (supplier, article, designation, quantity — never an order number), typed lines (article, designation,
 * quantity), an optional return leg (its docks, its lines ; no slot of its own), carrier and note.  With ``edit``, only
 * the lines and the return leg of an existing shuttle change.
 */
export function ShuttleDialog({ d, edit, onClose }: { d: BoardResponse; edit?: TransportOut; onClose: () => void }) {
  const toast = useToast();
  const today = d.as_of;
  const days = d.days.filter((x) => x >= today);
  const [day, setDay] = useState(edit?.date ?? d.maf_day ?? days[0] ?? today);
  const [time, setTime] = useState(edit?.time ?? "08:00");
  const [docks, setDocks] = useState<string[]>(edit?.docks ?? [d.docks[1] ?? d.docks[0]]);
  const [picked, setPicked] = useState<Set<string>>(new Set(edit?.orders.map((o) => o.order_id) ?? []));
  const [lines, setLines] = useState<TransportLineIn[]>(edit ? edit.lines.filter((l) => l.leg === "aller").map(({ article, designation, qty }) => ({ article, designation, qty })) : [emptyLine()]);
  const [back, setBack] = useState(edit?.return_trip ?? false);
  const [backDocks, setBackDocks] = useState<string[]>(edit?.return_docks.length ? edit.return_docks : edit?.docks ?? [d.docks[1] ?? d.docks[0]]);
  const [backLines, setBackLines] = useState<TransportLineIn[]>(edit ? edit.lines.filter((l) => l.leg === "retour").map(({ article, designation, qty }) => ({ article, designation, qty })) : [emptyLine()]);
  const [carrier, setCarrier] = useState(edit?.carrier ?? "");
  const [note, setNote] = useState(edit?.note ?? "");
  const maf = useQuery({ queryKey: ["transports-planning", "maf", mondayOf(today)], enabled: !edit,
    queryFn: () => api.get<PlanningResponse>("/api/transports/planning", { start: mondayOf(today), weeks: 2, scope: "maf" }) });
  const rows = useMemo(() => (maf.data?.suppliers ?? []).flatMap((s) => s.orders.filter((o) => o.expected_date === day && !o.ignored && o.qty_open > 0).map((o) => ({ s, o }))), [maf.data, day]);
  useEffect(() => { if (!edit && maf.data) setPicked(new Set(rows.filter(({ o }) => !o.transports.length).map(({ o }) => o.order_id))); }, [rows, maf.data, edit]);
  const create = useSlotWrite((body: ShuttleIn) => api.post<TransportPackOut>("/api/transports", body),
    async (pack) => { const id = pack.transports[0].transport_id; const ok = await copyText(id); toast.push(`Navette ${id} créée${ok ? " · identifiant copié" : ""}`, "success"); onClose(); });
  const save = useWrite((body: TransportLinesIn) => api.post<TransportOut>(`/api/transports/${encodeURIComponent(edit!.transport_id)}/lines`, body), () => { toast.push("Lignes enregistrées", "success"); onClose(); });
  const body = (force?: boolean): ShuttleIn => ({ date: day, time, docks, order_ids: [...picked], lines: cleanLines(lines), return_trip: back, return_docks: back ? backDocks : [], return_lines: back ? cleanLines(backLines) : [], carrier, note, force });
  const hasLoad = picked.size > 0 || cleanLines(lines).length > 0 || (back && cleanLines(backLines).length > 0);
  const valid = !!day && /^([01]\d|2[0-3]):[0-5]\d$/.test(time) && docks.length > 0 && hasLoad && (!back || backDocks.length > 0);
  const pending = create.isPending || save.isPending;
  return (
    <Drawer open onClose={onClose} wide title={<><ArrowLeftRight size={16} /> {edit ? <>Lignes de la navette · <span className="mono">{edit.transport_id}</span></> : "Navette MAF"}</>}
      footer={<><Button onClick={onClose}>Annuler</Button>
        {edit ? <Button variant="primary" disabled={!hasLoad || pending || (back && !backDocks.length)} onClick={() => save.mutate({ lines: cleanLines(lines), return_trip: back, return_docks: back ? backDocks : [], return_lines: back ? cleanLines(backLines) : [] })}>Enregistrer</Button>
          : <Button variant="primary" disabled={!valid || pending || !!create.conflict} onClick={() => create.mutate(body())}>{pending ? "…" : "Créer la navette"}</Button>}</>}>
      <div className="tp-form">
        {!edit && <>
          <div className="grid cols-2">
            <label className="field"><span>Jour</span><select className="select sm" value={day} onChange={(e) => { setDay(e.target.value); create.clearConflict(); }} aria-label="Jour">{days.map((x) => <option key={x} value={x}>{fmtDate(x)}{x === d.maf_day ? " · prochain jour ouvré" : ""}</option>)}</select></label>
            <label className="field"><span>Transporteur</span><input className="input sm" value={carrier} onChange={(e) => setCarrier(e.target.value)} maxLength={120} placeholder="navette interne" /></label>
          </div>
          <section className="tp-sec">
            <h4><ArrowRight size={14} />Aller · du MAF vers l'usine</h4>
            <SlotField date={day} time={time} docks={docks} onTime={(v) => { setTime(v); create.clearConflict(); }} onDocks={(v) => { setDocks(v); create.clearConflict(); }} daySlots={d.day_slots} allDocks={d.docks}
              busy={busyMap(d.transports, day, docks, d.day_slots)} />
            {create.conflict && <ConflictBox check={create.conflict} onTake={(x) => { setTime(x); create.mutate({ ...body(), time: x }); }} onForce={() => create.mutate(body(true))} pending={pending} />}
            <table className="tp-cmd" aria-label="Commandes MAF du jour">
              <thead><tr><th /><th>Fournisseur</th><th>Article</th><th>Désignation</th><th className="num">Quantité</th></tr></thead>
              <tbody>
                {rows.map(({ s, o }) => <tr key={o.order_id} className={o.transports.length ? "muted" : ""}><td><input type="checkbox" checked={picked.has(o.order_id)} onChange={() => setPicked((p) => { const n = new Set(p); if (n.has(o.order_id)) n.delete(o.order_id); else n.add(o.order_id); return n; })} aria-label={`${o.article_id} ${s.name}`} /></td><td>{s.name || s.supplier_id}</td><td><b>{o.article_id}</b></td><td className="small">{o.designation}{o.transports.length ? <span className="subtle"> · déjà sur {o.transports.join(", ")}</span> : null}</td><td className="num">{fmtQty(o.qty_open, o.unit)} {o.unit}</td></tr>)}
                {rows.length === 0 && <tr><td colSpan={5} className="subtle small">{maf.isLoading ? "chargement…" : "Aucune commande MAF ce jour-là : la navette emporte des lignes saisies."}</td></tr>}
              </tbody>
            </table>
            <LinesEditor lines={lines} onChange={setLines} label="Ligne saisie (article, désignation, quantité)" />
          </section>
        </>}
        {edit && <section className="tp-sec"><h4><ArrowRight size={14} />Aller · lignes saisies (les commandes MAF cochées à la création ne changent pas ici)</h4><LinesEditor lines={lines} onChange={setLines} label="Ligne saisie" /></section>}
        <section className={`tp-sec ${back ? "on" : ""}`}>
          <h4><label className="row" style={{ gap: 8, cursor: "pointer" }}><input type="checkbox" checked={back} onChange={(e) => setBack(e.target.checked)} /> Aller / Retour</label><span className="small subtle">la navette repart chargée vers le MAF, même horaire, pas de créneau en plus (ATR affine)</span></h4>
          {back && <>
            <div className="field"><span>Quais retour</span><DockPicker all={d.docks} value={backDocks} onChange={setBackDocks} label="Quais retour" size="sm" /></div>
            <LinesEditor lines={backLines} onChange={setBackLines} label="Ligne retour" />
          </>}
        </section>
        {!edit && <label className="field"><span>Note</span><input className="input sm" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="facultatif" /></label>}
        {save.error && <div className="error-box">{(save.error as Error).message}</div>}
      </div>
    </Drawer>
  );
}

/** Typed lines : article, designation, quantity as typed (« 6 U », « 18 PAL ») ; a line needs an article. */
function LinesEditor({ lines, onChange, label }: { lines: TransportLineIn[]; onChange: (v: TransportLineIn[]) => void; label: string }) {
  const set = (i: number, patch: Partial<TransportLineIn>) => onChange(lines.map((l, j) => (j === i ? { ...l, ...patch } : l)));
  return (
    <div className="tp-free">
      {lines.map((l, i) => (
        <div key={i} className="row" style={{ gap: 6 }}>
          <input className="input sm" value={l.article} placeholder="Article" aria-label={`Article ${i + 1}`} maxLength={120} onChange={(e) => set(i, { article: e.target.value })} style={{ flex: 1.1 }} />
          <input className="input sm" value={l.designation} placeholder="Désignation" aria-label={`Désignation ${i + 1}`} maxLength={200} onChange={(e) => set(i, { designation: e.target.value })} style={{ flex: 1.6 }} />
          <input className="input sm num" value={l.qty} placeholder="Quantité" aria-label={`Quantité ${i + 1}`} maxLength={40} onChange={(e) => set(i, { qty: e.target.value })} style={{ width: 96 }} />
          <Button size="xs" variant="ghost" icon title="Retirer la ligne" onClick={() => onChange(lines.length > 1 ? lines.filter((_, j) => j !== i) : [emptyLine()])}><X /></Button>
        </div>
      ))}
      <Button size="sm" onClick={() => onChange([...lines, emptyLine()])} style={{ alignSelf: "flex-start" }}><Plus />{label}</Button>
    </div>
  );
}
