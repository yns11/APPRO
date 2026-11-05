/** Helpers of the « Planning transport » : civil dates, ISO weeks, the state and kind of a truck, slots, clipboard. */
import type { TransportOut } from "@/lib/types";
import { WEEKDAY_LONG, WEEKDAY_SHORT, fmtDate } from "@/lib/format";

/** The three states a truck can show ; one colour each, the same in every view. */
export type TruckState = "plan" | "check" | "off";
export const STATE_LABEL: Record<TruckState, string> = { plan: "prévu", check: "à vérifier", off: "annulé" };
/** What a truck without order is called. */
export const KIND_LABEL: Record<string, string> = { ev: "retour EV", ponctuel: "ponctuel", maf: "navette" };

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
/** ``2026-W46`` of the ISO week of any day. */
export function isoWeekKey(iso: string): string { const w = isoWeek(mondayOf(iso)); return `${w.year}-W${String(w.week).padStart(2, "0")}`; }
export function weekShort(key: string): string { return `S${key.split("-W")[1]}`; }
export const dayLabel = (iso: string) => `${WEEKDAY_SHORT[(new Date(`${iso}T12:00:00`).getDay() + 6) % 7]} ${fmtDate(iso, "dd/MM")}`;
export const dayLong = (iso: string) => { const d = new Date(`${iso}T12:00:00`); const n = WEEKDAY_LONG[(d.getDay() + 6) % 7]; return `${n.charAt(0).toUpperCase()}${n.slice(1)} ${fmtDate(iso, "dd/MM")}`; };

export function stateOf(t: TransportOut): TruckState {
  if (t.status === "cancelled") return "off";
  if (t.check_reason) return "check";
  return "plan";
}
/** A truck without order (EV return, one-off truck) : the kind replaces the order count in the cell. */
export const kindOf = (t: TransportOut): string | null => (t.mode === "ev" || t.mode === "ponctuel" ? KIND_LABEL[t.mode] : null);

/** The slot a time falls in (floor), ``null`` outside the grid. */
export function slotOf(time: string, slots: string[]): string | null {
  if (!time || !slots.length) return null;
  const m = (s: string) => Number(s.slice(0, 2)) * 60 + Number(s.slice(3, 5));
  const step = slots.length > 1 ? m(slots[1]) - m(slots[0]) : 30;
  const t = m(time);
  if (t < m(slots[0]) || t >= m(slots[slots.length - 1]) + step) return null;
  return slots[Math.min(Math.floor((t - m(slots[0])) / step), slots.length - 1)];
}
export const csvUrl = (ids: string[]) => `/api/transports/atr.csv?${ids.map((id) => `transport_id=${encodeURIComponent(id)}`).join("&")}`;

/** Copy a text (the identifier of a truck, to paste in an e-mail) ; the hidden textarea is the fallback without HTTPS. */
export async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(text); return true; }
  } catch { /* fall through */ }
  try {
    const ta = document.createElement("textarea");
    ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    const ok = document.execCommand("copy");
    document.body.removeChild(ta);
    return ok;
  } catch { return false; }
}
