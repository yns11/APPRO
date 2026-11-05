import { slotOf, stateOf } from "@/lib/transports";
import type { PlanningResponse, TransportOut } from "@/lib/types";

/**
 * The agenda of one day : a row per slot, a dock = as many lanes as its capacity, a block = a truck.  A closed dock
 * is hatched ; the red line is now ; trucks outside the grid are listed under it.  ``all`` = every planned truck of
 * the week, every supplier (the docks are shared).
 */
export function DocksView({ d, all, day, sent, selected, onSelect, now }: { d: PlanningResponse; all: TransportOut[]; day: string; sent: Set<string>; selected: string | null; onSelect: (id: string) => void; now?: string }) {
  const cap = Math.max(d.capacity, 1);
  const closed = new Set(d.closed_docks[day] ?? []);
  const trucks = all.filter((t) => t.date === day && t.status === "planned");
  const lanes: Record<string, Record<string, TransportOut[]>> = {};
  const off: TransportOut[] = [];
  for (const t of trucks) {
    const slot = slotOf(t.time, d.slots);
    if (!slot) { off.push(t); continue; }
    for (const dock of t.docks) ((lanes[dock] ??= {})[slot] ??= []).push(t);
  }
  const nowSlot = now && now.slice(0, 10) === day ? slotOf(now.slice(11, 16), d.slots) : null;
  const short = (t: TransportOut) => (t.mode === "maf" ? "NAVETTE" : t.supplier_name.split(/\s+/)[0].slice(0, 11));
  return (
    <div className="tp-agenda-wrap">
      <div className="tp-agenda" style={{ gridTemplateColumns: `64px repeat(${d.docks.length * cap}, minmax(0, 1fr))`, gridTemplateRows: `40px repeat(${d.slots.length}, 36px)` }} role="grid" aria-label={`Quais et créneaux du ${day}`}>
        <div className="dh" />
        {d.docks.map((dock) => <div key={dock} className={`dh ${closed.has(dock) ? "closed" : ""}`} style={{ gridColumn: `span ${cap}` }}>{dock}{closed.has(dock) ? " · fermé" : ""}</div>)}
        {d.slots.map((slot, r) => (
          <div key={slot} style={{ display: "contents" }}>
            <div className={`tm ${slot.endsWith(":00") ? "h" : ""}`} style={{ gridRow: r + 2 }}>{slot}</div>
            {d.docks.flatMap((dock) => Array.from({ length: cap }, (_, i) => {
              const xs = lanes[dock]?.[slot] ?? [];
              const t = xs[i];
              const extra = i === cap - 1 && xs.length > cap ? xs.length - cap : 0;
              return (
                <div key={`${dock}-${i}`} className={`lane ${i === cap - 1 ? "b" : ""} ${closed.has(dock) ? "closed" : ""} ${nowSlot === slot ? "now" : ""}`} style={{ gridRow: r + 2 }}>
                  {t && <button type="button" className={`tp-blk ${stateOf(t, sent)} ${selected === t.transport_id ? "sel" : ""}`} onClick={() => onSelect(t.transport_id)} title={`${t.transport_id} · ${t.supplier_name} · ${t.time}${t.check_reason ? ` · à vérifier : ${t.check_reason}` : ""}`}><span className="id">{t.transport_id}{t.changed_since_sent ? " ✱" : ""}</span><span className="s">{short(t)}{extra ? ` +${extra}` : ""}</span></button>}
                </div>
              );
            }))}
          </div>
        ))}
      </div>
      {off.length > 0 && <div className="small subtle" style={{ padding: "6px 12px" }}>Hors grille : {off.map((t) => `${t.transport_id} (${t.time || "sans heure"})`).join(", ")}</div>}
    </div>
  );
}
