import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { usePerimeterLists, useSuppliers, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { Button, Drawer, useToast } from "@/components/ui";
import { SearchSelect } from "@/components/SearchSelect";
import { PalletIcon } from "@/components/transports/icons";
import { ConflictBox, SlotField, busyMap, useSlotWrite } from "@/components/transports/SlotField";
import type { BoardResponse, PackagingLine, SpecialKind, TransportLineIn, TransportOut, TransportPackOut, TransportSpecialIn } from "@/lib/types";
import { Truck } from "lucide-react";

const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;
export const SPECIAL: Record<SpecialKind, { title: string; help: string; cta: string }> = {
  ev: { title: "Retour EV (emballages vides)", help: "Le fournisseur a prévenu qu'il vient enlever ses emballages vides ce jour-là. Identifiant EM + 4 chiffres + EV, compteur à part ; les emballages et quantités prévues s'impriment sur le BL de retour.", cta: "Créer le retour EV" },
  ponctuel: { title: "Camion ponctuel", help: "Tout autre camion hors commandes fermes : échantillons, retour de pièces, dépannage… Identifiant EM + 5 chiffres + A (nouveau numéro).", cta: "Créer le camion" },
};

/** The packaging a supplier may take back, with a planned quantity to type on each line. */
export function usePackaging(supplierId: string, enabled = true) {
  return useQuery({ queryKey: ["transports-packaging", supplierId], enabled: enabled && !!supplierId, staleTime: 60_000,
    queryFn: () => api.get<PackagingLine[]>("/api/transports/packaging", { supplier_id: supplierId }) });
}

/** Lines of the EV return : one row per packaging of the supplier's articles, the planned quantity typed on the right. */
export function PackagingTable({ lines, qty, onQty, loading }: { lines: PackagingLine[]; qty: Record<string, string>; onQty: (key: string, v: string) => void; loading?: boolean }) {
  if (loading) return <div className="small subtle">chargement des emballages…</div>;
  if (!lines.length) return <div className="small subtle">Aucun emballage connu pour les articles de ce fournisseur (Référentiel › Emballages : article, code, nom).</div>;
  return (
    <table className="tp-cmd" aria-label="Emballages à retourner">
      <thead><tr><th>Article</th><th>Code emballage</th><th>Nom</th><th className="num">Quantité prévue</th></tr></thead>
      <tbody>
        {lines.map((l) => { const k = `${l.article_id}|${l.packaging_code}`; return (
          <tr key={k}><td><b>{l.article_id}</b><div className="small subtle">{l.designation}</div></td><td className="mono">{l.packaging_code}</td><td className="small">{l.packaging_name}{l.unit_weight_kg != null ? <span className="subtle"> · {l.unit_weight_kg} kg</span> : null}</td>
            <td style={{ width: 96 }}><input className="input sm num" inputMode="decimal" value={qty[k] ?? ""} placeholder="0" aria-label={`Quantité prévue ${l.packaging_code}`} onChange={(e) => onQty(k, e.target.value)} /></td></tr>); })}
      </tbody>
    </table>
  );
}

/** The typed quantities as the lines of the truck (only the lines with a quantity). */
export function packagingLines(lines: PackagingLine[], qty: Record<string, string>): TransportLineIn[] {
  return lines.flatMap((l) => { const v = (qty[`${l.article_id}|${l.packaging_code}`] ?? "").trim(); return v && Number(v.replace(",", ".")) > 0 ? [{ article: l.article_id, code: l.packaging_code, designation: l.packaging_name, qty: v }] : []; });
}

/** A truck without order : one of your suppliers, day, slot (half hours, occupied slot checked), docks, carrier, note ; an EV return adds the packaging to take back. */
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
  const [qty, setQty] = useState<Record<string, string>>({});
  const packaging = usePackaging(supplier, kind === "ev");
  useEffect(() => { setQty({}); }, [supplier]);
  const run = useSlotWrite((body: TransportSpecialIn) => api.post<TransportPackOut>("/api/transports/special", body),
    (pack) => { toast.push(`${pack.transports[0].transport_id} créé`, "success"); onClose(); });
  const body = (force?: boolean): TransportSpecialIn => ({ kind, supplier_id: supplier, date: day, time, docks: picked, carrier, note, lines: kind === "ev" ? packagingLines(packaging.data ?? [], qty) : [], force });
  const valid = !!supplier && !!day && TIME_RE.test(time) && picked.length > 0;
  const meta = SPECIAL[kind];
  return (
    <Drawer open onClose={onClose} wide={kind === "ev"} title={<>{kind === "ev" ? <PalletIcon width={16} height={16} /> : <Truck size={16} />} {meta.title}</>}
      footer={<><Button onClick={onClose}>Annuler</Button><Button variant="primary" disabled={!valid || run.isPending || !!run.conflict} onClick={() => run.mutate(body())}>{run.isPending ? "…" : meta.cta}</Button></>}>
      <p className="small subtle" style={{ marginTop: 0 }}>{meta.help}</p>
      <div className="tp-form">
        <div className="field"><span>Fournisseur{manager ? "" : " (vos fournisseurs)"}</span><SearchSelect value={supplier} onChange={setSupplier} options={options} placeholder="Code ou nom" ariaLabel="Fournisseur" width="100%" loading={sups.isLoading || mine.isLoading} /></div>
        <SlotField date={day} time={time} docks={picked} onDate={(v) => { setDay(v); run.clearConflict(); }} onTime={(v) => { setTime(v); run.clearConflict(); }} onDocks={(v) => { setPicked(v); run.clearConflict(); }}
          daySlots={d.day_slots} allDocks={d.docks} busy={busyMap(d.transports, day, picked, d.day_slots)} />
        {run.conflict && <ConflictBox check={run.conflict} onTake={(x) => { setTime(x); run.mutate({ ...body(), time: x }); }} onForce={() => run.mutate(body(true))} pending={run.isPending} />}
        {kind === "ev" && supplier && <div className="field"><span>Emballages à retourner</span><PackagingTable lines={packaging.data ?? []} qty={qty} onQty={(k, v) => setQty((q) => ({ ...q, [k]: v }))} loading={packaging.isLoading} /></div>}
        <div className="grid cols-2">
          <label className="field"><span>Transporteur</span><input className="input sm" value={carrier} onChange={(e) => setCarrier(e.target.value)} maxLength={120} /></label>
          <label className="field"><span>Note</span><input className="input sm" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder={kind === "ev" ? "nombre de palettes, contact…" : "objet du camion"} /></label>
        </div>
      </div>
    </Drawer>
  );
}


/** The packaging of an existing EV return, changed as a whole (menu › Emballages à retourner). */
export function EvLinesDrawer({ t, onClose }: { t: TransportOut; onClose: () => void }) {
  const toast = useToast();
  const packaging = usePackaging(t.supplier_id);
  const [qty, setQty] = useState<Record<string, string>>(() => Object.fromEntries(t.lines.filter((l) => l.leg === "ev").map((l) => [`${l.article}|${l.code}`, l.qty])));
  const save = useWrite((lines: TransportLineIn[]) => api.post<TransportOut>(`/api/transports/${encodeURIComponent(t.transport_id)}/lines`, { lines }), () => { toast.push("Emballages enregistrés", "success"); onClose(); });
  return (
    <Drawer open wide onClose={onClose} title={<><PalletIcon width={16} height={16} /> Emballages à retourner · <span className="mono">{t.transport_id}</span></>}
      footer={<><Button onClick={onClose}>Annuler</Button><Button variant="primary" disabled={save.isPending || packaging.isLoading} onClick={() => save.mutate(packagingLines(packaging.data ?? [], qty))}>Enregistrer</Button></>}>
      <p className="small subtle" style={{ marginTop: 0 }}>{t.supplier_name || t.supplier_id} · les quantités prévues s'impriment sur le BL de retour ; la quantité chargée se remplit à la main au chargement.</p>
      <PackagingTable lines={packaging.data ?? []} qty={qty} onQty={(k, v) => setQty((q) => ({ ...q, [k]: v }))} loading={packaging.isLoading} />
    </Drawer>
  );
}
