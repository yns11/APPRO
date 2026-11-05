/** Helpers of the « Planning transport » : civil dates, ISO weeks, the state of a truck, slots, mail links. */
import type { PlanMailPart, PlanOut, PlansOut, TransportOut } from "@/lib/types";
import { WEEKDAY_LONG, WEEKDAY_SHORT, fmtDate } from "@/lib/format";

/** The five states a truck can show ; one colour each, the same in every view. */
export type TruckState = "plan" | "sent" | "resend" | "check" | "off";
export const STATE_LABEL: Record<TruckState, string> = { plan: "à envoyer", sent: "envoyé", resend: "✱ changé depuis l'envoi", check: "à vérifier", off: "annulé" };

/** ``YYYY-MM-DD`` of a local date (never ``toISOString`` : it converts to UTC and shifts the day in Europe/Paris). */
export function localIso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
/** Monday of the ISO week of ``iso`` (civil date arithmetic, same result in every time zone). */
export function mondayOf(iso: string): string {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return localIso(d);
}
export function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + n);
  return localIso(d);
}
function isoWeek(monday: string): { year: number; week: number } {
  const d = new Date(`${monday}T12:00:00`);
  const thursday = new Date(d); thursday.setDate(d.getDate() + 3);
  const jan4 = new Date(thursday.getFullYear(), 0, 4);
  const week = 1 + Math.round(((thursday.getTime() - jan4.getTime()) / 86400000 - 3 + ((jan4.getDay() + 6) % 7)) / 7);
  return { year: thursday.getFullYear(), week };
}
/** ``2026-W46`` of the ISO week of any day (the key of the server's weekly plans). */
export function isoWeekKey(iso: string): string { const w = isoWeek(mondayOf(iso)); return `${w.year}-W${String(w.week).padStart(2, "0")}`; }
export function weekShort(key: string): string { return `S${key.split("-W")[1]}`; }
export const dayLabel = (iso: string) => `${WEEKDAY_SHORT[(new Date(`${iso}T12:00:00`).getDay() + 6) % 7]} ${fmtDate(iso, "dd/MM")}`;
export const dayLong = (iso: string) => { const d = new Date(`${iso}T12:00:00`); const n = WEEKDAY_LONG[(d.getDay() + 6) % 7]; return `${n.charAt(0).toUpperCase()}${n.slice(1)} ${fmtDate(iso, "dd/MM")}`; };

/** Key of the weekly plan recipient of a truck (``carrier:<name>`` or ``sup:<id>``), as the server computes it. */
export function recipientOf(t: TransportOut): string {
  return t.mode === "transporteur" && t.carrier.trim() ? `carrier:${t.carrier.trim()}` : `sup:${t.supplier_id}`;
}
/** ``recipient|week`` of every plan already sent at least once. */
export function sentKeysOf(plans?: PlansOut): Set<string> {
  return new Set((plans?.plans ?? []).filter((p) => p.sent_no > 0).map((p) => `${p.recipient}|${p.week}`));
}
export function planOf(t: TransportOut, plans?: PlansOut): PlanOut | undefined {
  const r = recipientOf(t), w = isoWeekKey(t.date);
  return plans?.plans.find((p) => p.recipient === r && p.week === w);
}
export function stateOf(t: TransportOut, sent: Set<string>): TruckState {
  if (t.status === "cancelled") return "off";
  if (t.check_reason) return "check";
  if (t.changed_since_sent) return "resend";
  return sent.has(`${recipientOf(t)}|${isoWeekKey(t.date)}`) ? "sent" : "plan";
}

/** The slot a time falls in (floor), ``null`` outside the grid. */
export function slotOf(time: string, slots: string[]): string | null {
  if (!time || !slots.length) return null;
  const m = (s: string) => Number(s.slice(0, 2)) * 60 + Number(s.slice(3, 5));
  const step = slots.length > 1 ? m(slots[1]) - m(slots[0]) : 30;
  const t = m(time);
  if (t < m(slots[0]) || t >= m(slots[slots.length - 1]) + step) return null;
  return slots[Math.min(Math.floor((t - m(slots[0])) / step), slots.length - 1)];
}
/** Outlook opens the pre-filled message ; the planner reads it and sends it (``;`` between recipients, as Outlook expects). */
export function mailtoOf(mail: Pick<PlanMailPart, "to" | "subject" | "body">): string {
  return `mailto:${mail.to.join(";")}?subject=${encodeURIComponent(mail.subject)}&body=${encodeURIComponent(mail.body)}`;
}
export const csvUrl = (ids: string[]) => `/api/transports/atr.csv?${ids.map((id) => `transport_id=${encodeURIComponent(id)}`).join("&")}`;
