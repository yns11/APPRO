import type { MouseEvent } from "react";
import { KindIcon } from "@/components/transports/icons";
import { dayLabel, shortName, toneOf, touchedBy } from "@/lib/transports";
import type { BoardResponse, TransportOut } from "@/lib/types";

/**
 * The week : docks (rows, B15 takes 55 % of the height) × working days (columns).  Each cell says first how many trucks
 * come (big figure) and how many are yours, then one chip per truck with the supplier only (yours in blue, to check in
 * red, the others grey with a red dot when they have something to check).  Totals per day (bottom) and per dock
 * (right).  A click on a day header opens its agenda ; a click on a chip opens the truck's menu.
 */
export function WeekBoard({ d, days, selected, onSelect, onDay, whose = "à vous" }: { d: BoardResponse; days: string[]; selected: string | null; onSelect: (t: TransportOut, e: MouseEvent<HTMLButtonElement>) => void; onDay: (day: string) => void; whose?: string }) {
  const planned = d.transports.filter((t) => t.status === "planned" && days.includes(t.date));
  const cell = (dock: string, day: string) => planned.filter((t) => t.date === day && t.docks.includes(dock)).sort((a, b) => a.time.localeCompare(b.time) || a.transport_id.localeCompare(b.transport_id));
  const dayTotal = (day: string) => planned.filter((t) => t.date === day).length;
  const dockTotal = (dock: string) => planned.filter((t) => t.docks.includes(dock)).length;
  const rows = d.docks.map((_dock, i) => (i === 0 ? "55fr" : `${Math.round(45 / Math.max(d.docks.length - 1, 1))}fr`)).join(" ");
  return (
    <div className="tp-grid tp-board" style={{ gridTemplateColumns: `76px repeat(${days.length}, minmax(0, 1fr)) 84px`, gridTemplateRows: `44px ${rows} 40px` }} role="grid" aria-label="Camions par quai et par jour">
      <div className="tp-hd">Quai</div>
      {days.map((day) => (
        <button key={day} type="button" className={`tp-hd day ${day === d.as_of ? "today" : ""}`} onClick={() => onDay(day)} title={`Ouvrir l'agenda du ${dayLabel(day)}`}>
          <span>{dayLabel(day)}</span><small>{dayTotal(day)} camion{dayTotal(day) > 1 ? "s" : ""}</small>
        </button>
      ))}
      <div className="tp-hd">Semaine</div>
      {d.docks.map((dock) => (
        <div key={dock} className="tp-row">
          <div className="tp-rh">{dock}</div>
          {days.map((day) => {
            const xs = cell(dock, day);
            const mine = xs.filter((t) => t.mine).length;
            const closed = (d.closed_docks[day] ?? []).includes(dock);
            return (
              <div key={day} className={`tp-cell ${day === d.as_of ? "today" : ""} ${day < d.as_of ? "past" : ""} ${closed ? "closed" : ""}`}>
                <div className="cnt">
                  {closed && !xs.length ? <b className="closed-lbl">fermé</b> : <><b className={`big ${xs.length ? "" : "zero"}`}>{xs.length}</b><span>camion{xs.length > 1 ? "s" : ""}</span>{mine > 0 && <span className="me">{mine} {whose}</span>}</>}
                </div>
                <div className="chips">
                  {xs.map((t) => (
                    <button key={t.transport_id} type="button" className={`tp-chip ${toneOf(t)} ${t.check_reason && !t.mine ? "dot" : ""} ${touchedBy(t) ? "touched" : ""} ${selected === t.transport_id ? "sel" : ""}`} aria-haspopup="menu" aria-label={`Camion ${t.transport_id}`}
                      title={`${t.transport_id} · ${t.time} · ${t.supplier_name || t.supplier_id}${t.check_reason ? ` · à vérifier : ${t.check_reason}` : ""}${touchedBy(t) ? " · touché à la main" : ""}`} onClick={(e) => onSelect(t, e)}>
                      <KindIcon t={t} size={11} /><span className="n">{shortName(t)}</span>
                    </button>
                  ))}
                </div>
              </div>
            );
          })}
          <div className="tp-tot">{dockTotal(dock)}</div>
        </div>
      ))}
      <div className="tp-rh total">Total</div>
      {days.map((day) => <div key={day} className="tp-tot">{dayTotal(day)}</div>)}
      <div className="tp-tot grand">{planned.length}</div>
    </div>
  );
}
