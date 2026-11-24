import type { MouseEvent } from "react";
import { KindIcon } from "@/components/transports/icons";
import { agendaRow, hourLabel, shortName, toneOf, touchedBy } from "@/lib/transports";
import type { BoardResponse, TransportOut } from "@/lib/types";

/**
 * The agenda of one day : a row per slot of the automatic grid, « < 7h » above and « ≥ 16h » below for the trucks placed
 * by hand outside it, a column per dock, a block = a truck (half the width : two trucks side by side, the capacity).  A
 * slot holding more trucks than the capacity (forced) is orange ; a closed dock is hatched ; the red line is now ; a
 * cancelled truck stays grey and struck until the end of its day.
 */
export function DayAgenda({ d, day, selected, onSelect, now }: { d: BoardResponse; day: string; selected: string | null; onSelect: (t: TransportOut, e: MouseEvent<HTMLButtonElement>) => void; now?: string }) {
  const closed = new Set(d.closed_docks[day] ?? []);
  const trucks = d.transports.filter((t) => t.date === day);
  const rows = ["before", ...d.slots, "after"];
  const label = (r: string) => (r === "before" ? `< ${hourLabel(d.slots[0])}` : r === "after" ? `≥ ${hourLabel(endOf(d.slots))}` : r);
  const nowRow = now && now.slice(0, 10) === day ? agendaRow(now.slice(11, 16), d.slots) : null;
  const inRow = (r: string) => trucks.filter((t) => agendaRow(t.time, d.slots) === r).sort((a, b) => a.time.localeCompare(b.time) || a.transport_id.localeCompare(b.transport_id));
  const off = trucks.filter((t) => agendaRow(t.time, d.slots) === null);
  return (
    <div className="tp-agenda-wrap">
      <div className="tp-agenda" style={{ gridTemplateColumns: `64px repeat(${d.docks.length}, minmax(0, 1fr))` }} role="grid" aria-label={`Quais et créneaux du ${day}`}>
        <div className="dh" />
        {d.docks.map((dock) => {
          const n = trucks.filter((t) => t.status === "planned" && t.docks.includes(dock)).length;
          return <div key={dock} className={`dh ${closed.has(dock) ? "closed" : ""}`}>{dock}<small>{closed.has(dock) ? "fermé" : `${n} camion${n > 1 ? "s" : ""}`}</small></div>;
        })}
        {rows.map((r) => {
          const xs = inRow(r);
          const edge = r === "before" || r === "after";
          return (
            <div key={r} style={{ display: "contents" }}>
              <div className={`tm ${r.endsWith(":00") || edge ? "h" : ""} ${edge ? "edge" : ""}`}>{label(r)}</div>
              {d.docks.map((dock) => {
                const here = xs.filter((t) => t.docks.includes(dock));
                const live = here.filter((t) => t.status === "planned").length;
                return (
                  <div key={dock} className={`sl ${r.endsWith(":00") || edge ? "hr" : ""} ${closed.has(dock) ? "closed" : ""} ${nowRow === r ? "now" : ""} ${live > d.capacity ? "forced" : ""} ${edge ? "edge" : ""}`}>
                    {here.map((t) => (
                      <button key={t.transport_id} type="button" className={`tp-blk ${toneOf(t)} ${t.check_reason && !t.mine ? "dot" : ""} ${touchedBy(t) ? "touched" : ""} ${here.length === 1 ? "one" : ""} ${selected === t.transport_id ? "sel" : ""}`} aria-haspopup="menu"
                        onClick={(e) => onSelect(t, e)} title={`${t.transport_id} · ${t.supplier_name || t.supplier_id} · ${t.time}${t.status === "cancelled" ? " · annulé" : ""}${t.check_reason ? ` · à vérifier : ${t.check_reason}` : ""}${touchedBy(t) ? " · touché à la main" : ""}`}>
                        <KindIcon t={t} size={12} /><span className="id">{t.transport_id}</span><span className="n">{shortName(t)}{edge ? ` · ${t.time}` : ""}{t.status === "cancelled" ? " · annulé" : ""}</span>
                      </button>
                    ))}
                  </div>
                );
              })}
            </div>
          );
        })}
      </div>
      {off.length > 0 && <div className="small subtle" style={{ padding: "6px 12px" }}>Sans heure : {off.map((t) => t.transport_id).join(", ")}</div>}
    </div>
  );
}

/** The first hour after the last slot of the grid (15:30 → 16:00). */
function endOf(slots: string[]): string {
  const last = slots[slots.length - 1];
  const step = slots.length > 1 ? (Number(slots[1].slice(0, 2)) * 60 + Number(slots[1].slice(3))) - (Number(slots[0].slice(0, 2)) * 60 + Number(slots[0].slice(3))) : 30;
  const m = Number(last.slice(0, 2)) * 60 + Number(last.slice(3)) + step;
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}
