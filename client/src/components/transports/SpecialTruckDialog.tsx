import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useSuppliers, useWrite } from "@/lib/queries";
import { Button, Drawer, useToast } from "@/components/ui";
import { DockPicker } from "@/components/DockPicker";
import { SearchSelect } from "@/components/SearchSelect";
import { copyText } from "@/lib/transports";
import type { SpecialKind, TransportPackOut, TransportSpecialIn } from "@/lib/types";

const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;
export const SPECIAL: Record<SpecialKind, { title: string; help: string; cta: string }> = {
  ev: { title: "Retour EV (emballages vides)", help: "Le fournisseur a prévenu qu'il vient enlever ses emballages vides ce jour-là. Identifiant EM + 4 chiffres + EV, compteur à part.", cta: "Créer le retour EV" },
  ponctuel: { title: "Camion ponctuel", help: "Tout autre camion hors commandes fermes : échantillons, retour de pièces, dépannage… Identifiant EM + 5 chiffres + A (nouveau numéro).", cta: "Créer le camion" },
};

/** A truck without order : supplier, day, slot, docks, carrier, note. */
export function SpecialTruckDialog({ kind, date, docks, slots, onClose }: { kind: SpecialKind; date: string; docks: string[]; slots: string[]; onClose: () => void }) {
  const toast = useToast();
  const sups = useSuppliers();
  const options = useMemo(() => (sups.data ?? []).filter((s) => s.active).map((s) => ({ id: s.supplier_id, label: s.name || s.supplier_id, hint: s.supplier_id })).sort((a, b) => a.label.localeCompare(b.label, "fr")), [sups.data]);
  const [supplier, setSupplier] = useState("");
  const [day, setDay] = useState(date);
  const [time, setTime] = useState(slots[2] ?? "08:00");
  const [picked, setPicked] = useState<string[]>([docks[0]]);
  const [carrier, setCarrier] = useState("");
  const [note, setNote] = useState("");
  const run = useWrite((body: TransportSpecialIn) => api.post<TransportPackOut>("/api/transports/special", body),
    async (pack) => { const id = pack.transports[0].transport_id; const ok = await copyText(id); toast.push(`${id} créé${ok ? " · identifiant copié" : ""}`, "success"); onClose(); });
  const valid = !!supplier && !!day && TIME_RE.test(time) && picked.length > 0;
  const meta = SPECIAL[kind];
  return (
    <Drawer open onClose={onClose} title={meta.title}
      footer={<><Button onClick={onClose}>Annuler</Button><Button variant="primary" disabled={!valid || run.isPending} onClick={() => run.mutate({ kind, supplier_id: supplier, date: day, time, docks: picked, carrier, note })}>{run.isPending ? "…" : meta.cta}</Button></>}>
      <p className="small subtle" style={{ marginTop: 0 }}>{meta.help}</p>
      <div className="tp-form">
        <div className="field"><span>Fournisseur</span><SearchSelect value={supplier} onChange={setSupplier} options={options} placeholder="Code ou nom" ariaLabel="Fournisseur" width="100%" loading={sups.isLoading} /></div>
        <div className="grid cols-2">
          <label className="field"><span>Jour</span><input className="input sm" type="date" value={day} onChange={(e) => setDay(e.target.value)} /></label>
          <label className="field"><span>Créneau</span>
            <span className="row" style={{ gap: 6 }}>
              <select className="select sm" value={slots.includes(time) ? time : ""} onChange={(e) => { if (e.target.value) setTime(e.target.value); }} aria-label="Créneau"><option value="">autre…</option>{slots.map((s) => <option key={s} value={s}>{s}</option>)}</select>
              <input className="input sm" type="time" value={time} step={300} onChange={(e) => setTime(e.target.value)} aria-label="Heure" style={{ width: 100 }} />
            </span></label>
        </div>
        <div className="field"><span>Quais</span><DockPicker all={docks} value={picked} onChange={setPicked} label="Quais" size="sm" /></div>
        <div className="grid cols-2">
          <label className="field"><span>Transporteur</span><input className="input sm" value={carrier} onChange={(e) => setCarrier(e.target.value)} maxLength={120} /></label>
          <label className="field"><span>Note</span><input className="input sm" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder={kind === "ev" ? "nombre de palettes, contact…" : "objet du camion"} /></label>
        </div>
        {run.error && <div className="error-box">{(run.error as Error).message}</div>}
      </div>
    </Drawer>
  );
}
