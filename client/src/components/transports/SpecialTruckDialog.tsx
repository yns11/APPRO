import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { usePerimeterLists, useSuppliers } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { Button, Drawer, useToast } from "@/components/ui";
import { SearchSelect } from "@/components/SearchSelect";
import { PalletIcon } from "@/components/transports/icons";
import { ConflictBox, SlotField, busyMap, useSlotWrite } from "@/components/transports/SlotField";
import { copyText } from "@/lib/transports";
import type { BoardResponse, SpecialKind, TransportPackOut, TransportSpecialIn } from "@/lib/types";
import { Truck } from "lucide-react";

const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;
export const SPECIAL: Record<SpecialKind, { title: string; help: string; cta: string }> = {
  ev: { title: "Retour EV (emballages vides)", help: "Le fournisseur a prévenu qu'il vient enlever ses emballages vides ce jour-là. Identifiant EM + 4 chiffres + EV, compteur à part.", cta: "Créer le retour EV" },
  ponctuel: { title: "Camion ponctuel", help: "Tout autre camion hors commandes fermes : échantillons, retour de pièces, dépannage… Identifiant EM + 5 chiffres + A (nouveau numéro).", cta: "Créer le camion" },
};

/** A truck without order : one of your suppliers, day, slot (half hours, occupied slot checked), docks, carrier, note. */
export function SpecialTruckDialog({ kind, d, date, onClose }: { kind: SpecialKind; d: BoardResponse; date: string; onClose: () => void }) {
  const toast = useToast();
  const { config } = usePerimeter();
  const sups = useSuppliers();
  const manager = !!config?.access && (config.access.is_admin || config.access.role === "manager");
  const mine = usePerimeterLists(manager ? null : config?.access?.name ?? null);
  const options = useMemo(() => {
    const own = manager ? null : new Set((mine.data?.suppliers ?? []).map((s) => s.id));
    return (sups.data ?? []).filter((s) => s.active && (!own || own.has(s.supplier_id))).map((s) => ({ id: s.supplier_id, label: s.name || s.supplier_id, hint: s.supplier_id })).sort((a, b) => a.label.localeCompare(b.label, "fr"));
  }, [sups.data, mine.data, manager]);
  const [supplier, setSupplier] = useState("");
  const [day, setDay] = useState(date);
  const [time, setTime] = useState("08:00");
  const [picked, setPicked] = useState<string[]>([d.docks[0]]);
  const [carrier, setCarrier] = useState("");
  const [note, setNote] = useState("");
  const run = useSlotWrite((body: TransportSpecialIn) => api.post<TransportPackOut>("/api/transports/special", body),
    async (pack) => { const id = pack.transports[0].transport_id; const ok = await copyText(id); toast.push(`${id} créé${ok ? " · identifiant copié" : ""}`, "success"); onClose(); });
  const body = (force?: boolean): TransportSpecialIn => ({ kind, supplier_id: supplier, date: day, time, docks: picked, carrier, note, force });
  const valid = !!supplier && !!day && TIME_RE.test(time) && picked.length > 0;
  const meta = SPECIAL[kind];
  return (
    <Drawer open onClose={onClose} title={<>{kind === "ev" ? <PalletIcon width={16} height={16} /> : <Truck size={16} />} {meta.title}</>}
      footer={<><Button onClick={onClose}>Annuler</Button><Button variant="primary" disabled={!valid || run.isPending || !!run.conflict} onClick={() => run.mutate(body())}>{run.isPending ? "…" : meta.cta}</Button></>}>
      <p className="small subtle" style={{ marginTop: 0 }}>{meta.help}</p>
      <div className="tp-form">
        <div className="field"><span>Fournisseur{manager ? "" : " (vos fournisseurs)"}</span><SearchSelect value={supplier} onChange={setSupplier} options={options} placeholder="Code ou nom" ariaLabel="Fournisseur" width="100%" loading={sups.isLoading || mine.isLoading} /></div>
        <SlotField date={day} time={time} docks={picked} onDate={(v) => { setDay(v); run.clearConflict(); }} onTime={(v) => { setTime(v); run.clearConflict(); }} onDocks={(v) => { setPicked(v); run.clearConflict(); }}
          daySlots={d.day_slots} allDocks={d.docks} busy={busyMap(d.transports, day, picked, d.day_slots)} />
        {run.conflict && <ConflictBox check={run.conflict} onTake={(x) => { setTime(x); run.mutate({ ...body(), time: x }); }} onForce={() => run.mutate(body(true))} pending={run.isPending} />}
        <div className="grid cols-2">
          <label className="field"><span>Transporteur</span><input className="input sm" value={carrier} onChange={(e) => setCarrier(e.target.value)} maxLength={120} /></label>
          <label className="field"><span>Note</span><input className="input sm" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder={kind === "ev" ? "nombre de palettes, contact…" : "objet du camion"} /></label>
        </div>
      </div>
    </Drawer>
  );
}
