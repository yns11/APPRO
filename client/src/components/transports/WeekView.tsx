import type { MouseEvent } from "react";
import { Badge, SupplierLink } from "@/components/ui";
import { dayLabel, kindOf, stateOf, type TruckState } from "@/lib/transports";
import type { PlanningOrder, PlanningResponse, PlanningSupplier, TransportOut } from "@/lib/types";

/** One truck in a cell : identifier and time, dock and number of orders (or its kind) ; the colour is its state. */
export function TruckChip({ t, state, selected, onClick }: { t: TransportOut; state: TruckState; selected: boolean; onClick: (e: MouseEvent<HTMLButtonElement>) => void }) {
  const kind = kindOf(t);
  return (
    <button type="button" className={`tp-chip ${state} ${selected ? "sel" : ""}`} onClick={onClick} aria-pressed={selected} aria-haspopup="menu" aria-label={`Camion ${t.transport_id}`}
      title={`${t.transport_id} · ${t.time} · quai ${t.docks.join(" / ")} · ${kind ?? `${t.orders.length} commande(s)`}${t.note ? `\n${t.note}` : ""}${t.check_reason ? `\nà vérifier : ${t.check_reason}` : ""}`}>
      <span className="l1"><span className="id">{t.transport_id}</span><span>{t.status === "cancelled" ? "annulé" : t.time}</span></span>
      <span className="l2">{state === "check" ? <span className="ell">à vérifier · {t.check_reason}</span> : <><span>{t.docks.join("/")}</span><span>{kind ?? `${t.orders.length} cmd`}</span></>}</span>
    </button>
  );
}

/**
 * Suppliers × working days of the week, one truck per cell.  A supplier in manual with orders and no truck shows a
 * dashed « + camion » ; an automatic supplier whose day has no truck (suppressed day) shows « sans camion ».  A
 * supplier with an EV return or a one-off truck and no order keeps his row.
 */
export function WeekView({ d, selected, onSelect, onCreate }: { d: PlanningResponse; selected: string | null; onSelect: (t: TransportOut, e: MouseEvent<HTMLButtonElement>) => void; onCreate: (s: PlanningSupplier, date: string, orders: PlanningOrder[]) => void }) {
  const byId = new Map(d.transports.map((t) => [t.transport_id, t]));
  const days = d.days;
  const trucksOf = (s: PlanningSupplier, day: string, orders: PlanningOrder[]) => {
    if (d.scope === "maf") return [...new Set(orders.flatMap((o) => o.transports))].map((id) => byId.get(id)).filter((t): t is TransportOut => !!t);
    return d.transports.filter((t) => t.supplier_id === s.supplier_id && t.date === day);
  };
  const rows = d.suppliers.filter((s) => s.orders.some((o) => days.includes(o.expected_date)) || d.transports.some((t) => t.supplier_id === s.supplier_id && days.includes(t.date)));
  return (
    <div className="tp-grid" style={{ gridTemplateColumns: `200px repeat(${days.length}, minmax(0, 1fr))` }} role="grid" aria-label="Planning de la semaine">
      <div className="tp-hd">Fournisseur</div>
      {days.map((day) => <div key={day} className={`tp-hd ${day === d.as_of ? "today" : ""}`}>{dayLabel(day)}</div>)}
      {rows.map((s) => (
        <div key={s.supplier_id} className="tp-row">
          <div className="tp-sup">
            <span className="n"><SupplierLink id={s.supplier_id} name={s.name} /></span>
            <span className="m"><Badge tone={s.auto ? "brand" : "neutral"}>{s.auto ? "auto" : "manuel"}</Badge>{s.carrier ? <span className="subtle"> via {s.carrier}</span> : null}</span>
          </div>
          {days.map((day) => {
            const orders = s.orders.filter((o) => o.expected_date === day && !o.ignored);
            const trucks = trucksOf(s, day, orders);
            const uncovered = orders.filter((o) => !o.transports.length);
            return (
              <div key={day} className={`tp-cell ${day === d.as_of ? "today" : ""} ${day < d.as_of ? "past" : ""}`}>
                {trucks.map((t) => <TruckChip key={t.transport_id} t={t} state={stateOf(t)} selected={selected === t.transport_id} onClick={(e) => onSelect(t, e)} />)}
                {uncovered.length > 0 && day >= d.as_of && (s.auto
                  ? <span className="tp-chip none" title={uncovered.map((o) => `${o.ref || o.order_id} · ${o.article_id}`).join("\n")}>sans camion · {uncovered.length} cmd</span>
                  : <button type="button" className="tp-chip ghost" onClick={() => onCreate(s, day, uncovered)} title={uncovered.map((o) => `${o.ref || o.order_id} · ${o.article_id}`).join("\n")}>+ {d.scope === "maf" ? "navette" : "camion"} · {uncovered.length} cmd</button>)}
              </div>
            );
          })}
        </div>
      ))}
      {rows.length === 0 && <div className="tp-empty" style={{ gridColumn: `1 / span ${days.length + 1}` }}>Aucun ordre ferme ni camion cette semaine sur ce périmètre.</div>}
    </div>
  );
}
