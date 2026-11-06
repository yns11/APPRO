import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { api } from "@/lib/api";
import { useInvalidateAll } from "@/lib/queries";
import { Button, useToast } from "@/components/ui";
import { DockPicker } from "@/components/DockPicker";
import { conflictOf, slotOf } from "@/lib/transports";
import type { SlotCheck, TransportOut } from "@/lib/types";

const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;

/** The live check of a slot : who is already on the docks at that time, the nearest free slot. */
export function useSlotCheck(date: string, time: string, docks: string[], exclude?: string, enabled = true) {
  return useQuery({
    queryKey: ["transport-slot", date, time, docks.join(";"), exclude ?? null],
    enabled: enabled && !!date && TIME_RE.test(time) && docks.length > 0,
    queryFn: () => api.get<SlotCheck>("/api/transports/slot", { date, time, dock: docks, exclude }),
    staleTime: 5_000,
  });
}

/**
 * Day, slot (every half hour from 06:00 to 21:00, the trucks already there next to each) and docks of a truck.  The
 * server answers an occupied slot with a 409 : the ``ConflictBox`` under the field tells who is there and what to do.
 */
export function SlotField({ date, time, docks, onDate, onTime, onDocks, daySlots, allDocks, label = "Créneau", dayLabel = "Jour", busy }:
  { date: string; time: string; docks: string[]; onDate?: (v: string) => void; onTime: (v: string) => void; onDocks?: (v: string[]) => void; daySlots: string[]; allDocks: string[];
    label?: string; dayLabel?: string; busy?: Record<string, number> }) {
  return (
    <div className="tp-form">
      <div className="grid cols-2">
        {onDate && <label className="field"><span>{dayLabel}</span><input className="input sm" type="date" value={date} onChange={(e) => onDate(e.target.value)} /></label>}
        <label className="field"><span>{label}</span>
          <select className="select sm" value={daySlots.includes(time) ? time : ""} onChange={(e) => { if (e.target.value) onTime(e.target.value); }} aria-label={label}>
            {!daySlots.includes(time) && <option value="">{time || "choisir…"}</option>}
            {daySlots.map((s) => <option key={s} value={s}>{s}{busy && busy[s] ? ` · ${busy[s]} camion${busy[s] > 1 ? "s" : ""}` : ""}</option>)}
          </select></label>
      </div>
      {onDocks && <div className="field"><span>Quais</span><DockPicker all={allDocks} value={docks} onChange={onDocks} label="Quais" size="sm" /></div>}
    </div>
  );
}

/** The warning of an occupied slot and its two ways out : take the nearest free slot, or force (within the hard maximum). */
export function ConflictBox({ check, onTake, onForce, pending }: { check: SlotCheck; onTake: (time: string) => void; onForce: () => void; pending?: boolean }) {
  if (check.free) return null;
  return (
    <div className="tp-conflict" role="alert">
      <AlertTriangle size={16} />
      <div>
        <b>{check.time} est complet sur {check.docks.join(" / ")}</b> : {check.others.length ? `${check.others.join(", ")} ${check.others.length > 1 ? "y sont" : "y est"} déjà` : `${check.count} camion(s) déjà`}
        {check.proposed ? <> · le créneau libre le plus proche est <b>{check.proposed}</b></> : <> · plus aucun créneau libre ce jour-là sur ces quais</>}.
        <div className="small">{check.forceable ? `Forcer pose un ${check.count + 1}ᵉ camion sur le même créneau (${check.hard_max} au plus) ; il apparaît en orange dans l'agenda.` : `${check.hard_max} camions déjà : impossible de forcer.`}</div>
        <div className="row" style={{ gap: 6, marginTop: 8 }}>
          {check.proposed && <Button size="sm" variant="primary" disabled={pending} onClick={() => onTake(check.proposed!)}>Prendre {check.proposed}</Button>}
          {check.forceable && <Button size="sm" className="warn" disabled={pending} onClick={onForce}>Forcer {check.time} ({check.count + 1} / {check.hard_max})</Button>}
        </div>
      </div>
    </div>
  );
}

/**
 * A write that may hit an occupied slot : a 409 keeps the dialog open with the check (``conflict``) instead of a toast ;
 * any other error is toasted ; a success invalidates the queries and calls ``onDone``.
 */
export function useSlotWrite<TIn, TOut>(fn: (input: TIn) => Promise<TOut>, onDone?: (out: TOut) => void) {
  const invalidate = useInvalidateAll();
  const toast = useToast();
  const [conflict, setConflict] = useState<SlotCheck | null>(null);
  const m = useMutation({
    mutationFn: fn,
    onSuccess: (out) => { setConflict(null); invalidate(); onDone?.(out); },
    onError: (e) => { const c = conflictOf(e); if (c) setConflict(c); else toast.push((e as Error).message, "error"); },
  });
  return { ...m, conflict, clearConflict: () => setConflict(null) };
}

/** Trucks per half hour on the chosen docks that day (the figures next to the slots of the picker). */
export function busyMap(transports: TransportOut[], date: string, docks: string[], daySlots: string[], exclude?: string): Record<string, number> {
  const per: Record<string, Record<string, number>> = {};
  for (const t of transports) {
    if (t.date !== date || t.status !== "planned" || t.transport_id === exclude) continue;
    const s = slotOf(t.time, daySlots);
    if (!s) continue;
    for (const dk of t.docks) if (docks.includes(dk)) (per[s] ??= {})[dk] = ((per[s] ??= {})[dk] ?? 0) + 1;
  }
  return Object.fromEntries(Object.entries(per).map(([s, m]) => [s, Math.max(...Object.values(m))]));
}
