import { dayLabel, slotOf } from "@/lib/transports";
import type { PlanningResponse, TransportOut } from "@/lib/types";

const NOON = "12:00";

/**
 * Trucks per dock and half-day over the week, five shades of blue up to the capacity (capacity × slots of the
 * half-day) ; a red dot marks a truck to check ; a click opens the docks view of that day.
 */
export function LoadView({ d, all, onPick }: { d: PlanningResponse; all: TransportOut[]; onPick: (day: string) => void }) {
  const cap = Math.max(d.capacity, 1);
  const halves: ["am" | "pm", string][] = [["am", "matin"], ["pm", "a.-midi"]];
  const slotsOf = (h: "am" | "pm") => d.slots.filter((s) => (h === "am" ? s < NOON : s >= NOON)).length;
  const count: Record<string, { n: number; check: boolean }> = {};
  for (const t of all) {
    if (t.status !== "planned") continue;
    const slot = slotOf(t.time, d.slots) ?? t.time;
    const h = slot < NOON ? "am" : "pm";
    for (const dock of t.docks) {
      const k = `${dock}|${t.date}|${h}`;
      const c = (count[k] ??= { n: 0, check: false });
      c.n += 1; c.check = c.check || !!t.check_reason;
    }
  }
  const level = (n: number, max: number) => (n <= 0 ? 0 : n >= max ? 4 : Math.min(3, Math.max(1, Math.ceil((3 * n) / max))));
  const dayTotal = (day: string, h: "am" | "pm") => d.docks.reduce((s, dock) => s + (count[`${dock}|${day}|${h}`]?.n ?? 0), 0);
  const dockTotal = (dock: string) => d.days.reduce((s, day) => s + (count[`${dock}|${day}|am`]?.n ?? 0) + (count[`${dock}|${day}|pm`]?.n ?? 0), 0);
  const cols = d.days.length * 2;
  return (
    <div className="tp-heat" style={{ gridTemplateColumns: `110px repeat(${cols}, minmax(0, 1fr)) 90px` }} role="grid" aria-label="Charge des quais de la semaine">
      <div />
      {d.days.map((day) => <div key={day} className={`dh ${day === d.as_of ? "today" : ""}`} style={{ gridColumn: "span 2" }}>{dayLabel(day)}</div>)}
      <div className="dh">Semaine</div>
      <div />
      {d.days.flatMap((day) => halves.map(([h, l]) => <div key={`${day}-${h}`} className="sh">{l}</div>))}
      <div />
      {d.docks.map((dock) => (
        <div key={dock} style={{ display: "contents" }}>
          <div className="rh">{dock}</div>
          {d.days.flatMap((day) => halves.map(([h]) => {
            const closed = (d.closed_docks[day] ?? []).includes(dock);
            const c = count[`${dock}|${day}|${h}`];
            const max = cap * Math.max(slotsOf(h), 1);
            return closed
              ? <div key={`${day}-${h}`} className="c closed" title={`${dock} fermé le ${dayLabel(day)}`} />
              : <button key={`${day}-${h}`} type="button" className={`c l${level(c?.n ?? 0, max)}`} onClick={() => onPick(day)} title={`${dock} · ${dayLabel(day)} ${h === "am" ? "matin" : "après-midi"} · ${c?.n ?? 0} camion(s) sur ${max} places`}>{c?.n ?? 0}{c?.check && <span className="flag" aria-label="un camion à vérifier" />}</button>;
          }))}
          <div className="tot">{dockTotal(dock)}</div>
        </div>
      ))}
      <div className="rh subtle" style={{ fontWeight: 400 }}>Total</div>
      {d.days.flatMap((day) => halves.map(([h]) => <div key={`${day}-${h}`} className="tot">{dayTotal(day, h)}</div>))}
      <div className="tot">{d.docks.reduce((s, dock) => s + dockTotal(dock), 0)}</div>
    </div>
  );
}
