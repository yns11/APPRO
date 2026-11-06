/** Helpers of the « Planning transport » : civil dates, ISO weeks, the tone and kind of a truck, slots, lines, clipboard. */
import type { ApiError } from "@/lib/api";
import type { SlotCheck, TransportLineIn, TransportOrderOut, TransportOut } from "@/lib/types";
import { WEEKDAY_LONG, WEEKDAY_SHORT, fmtDate, fmtQty } from "@/lib/format";

/** The tones a truck can show, the same in every view : of the portfolio shown (blue), to check (red), by hand (yellow : every
 * shuttle, the EV returns and one-off trucks of the portfolio shown), another portfolio's (grey), cancelled (struck). */
export type TruckTone = "mine" | "check" | "manual" | "other" | "off";
export const TONE_LABEL: Record<TruckTone, string> = { mine: "camions du carnet", check: "à vérifier", manual: "navettes, retours EV et camions ponctuels du carnet", other: "autres carnets", off: "annulé" };
/** What a truck without supplier orders is called. */
export const KIND_LABEL: Record<string, string> = { ev: "retour EV", ponctuel: "ponctuel", maf: "navette MAF" };
/** The screens write HAMBACH on a shuttle (the CSV keeps NAVETTE HAMBACH). */
export const MAF_SHORT = "HAMBACH";
/** The habitual ranges a supplier may have (Référentiel) : token → label. */
export const HOUR_RANGES: { value: string; label: string }[] = [
  { value: "06-10", label: "6h – 10h" }, { value: "08-12", label: "8h – 12h" }, { value: "10-14", label: "10h – 14h" },
  { value: "12-16", label: "12h – 16h" }, { value: "14-18", label: "14h – 18h" }, { value: "16+", label: "≥ 16h" },
];
export const MAX_HOUR_RANGES = 2;
export const splitHours = (v: unknown): string[] => String(v ?? "").split(/[;,]+/).map((x) => x.trim()).filter(Boolean);

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
export const dayShort = (iso: string) => `${WEEKDAY_SHORT[(new Date(`${iso}T12:00:00`).getDay() + 6) % 7]} ${fmtDate(iso, "dd")}`;
export const dayLong = (iso: string) => { const d = new Date(`${iso}T12:00:00`); const n = WEEKDAY_LONG[(d.getDay() + 6) % 7]; return `${n.charAt(0).toUpperCase()}${n.slice(1)} ${fmtDate(iso, "dd/MM")}`; };

export function toneOf(t: TransportOut): TruckTone {
  if (t.status === "cancelled") return "off";
  if (!t.mine) return "other";
  if (t.check_reason) return "check";
  return t.mode === "maf" || t.mode === "ev" || t.mode === "ponctuel" ? "manual" : "mine";
}
/** An automatic truck a planner touched (moved, split, restored, annotated) : a small dot on its right tells it from one never modified. */
export const touchedBy = (t: TransportOut) => t.status === "planned" && t.mode !== "maf" && t.mode !== "ev" && t.mode !== "ponctuel" && (t.pinned || !!t.note);
export const isShuttle = (t: TransportOut) => t.mode === "maf";
/** The name written on a chip or a block : HAMBACH for a shuttle (· A/R), the first word of the supplier otherwise. */
export function shortName(t: TransportOut, max = 12): string {
  if (isShuttle(t)) return `${MAF_SHORT}${t.return_trip ? " · A/R" : ""}`;
  const first = (t.supplier_name || t.supplier_id).split(/\s+/)[0] ?? t.supplier_id;
  return first.length > max ? `${first.slice(0, max - 1)}.` : first;
}
/** The planner behind an e-mail : the first name of the declared planner, else the part before @. */
export function who(email: string | null | undefined, people: Record<string, string> | undefined): string {
  if (!email) return "";
  if (email === "application") return "l'application";
  return people?.[email.toLowerCase()] ?? email.split("@")[0].toUpperCase();
}

/** The minutes of ``HH:MM``. */
export const minutes = (s: string) => Number(s.slice(0, 2)) * 60 + Number(s.slice(3, 5));
/** The slot a time falls in (floor), ``null`` outside the grid. */
export function slotOf(time: string, slots: string[]): string | null {
  if (!time || !slots.length) return null;
  const step = slots.length > 1 ? minutes(slots[1]) - minutes(slots[0]) : 30;
  const t = minutes(time);
  if (t < minutes(slots[0]) || t >= minutes(slots[slots.length - 1]) + step) return null;
  return slots[Math.min(Math.floor((t - minutes(slots[0])) / step), slots.length - 1)];
}
/** Where a truck sits in the agenda : a slot of the automatic grid, « before » it or « after » it. */
export function agendaRow(time: string, slots: string[]): string | null {
  if (!time || !slots.length) return null;
  const step = slots.length > 1 ? minutes(slots[1]) - minutes(slots[0]) : 30;
  const t = minutes(time);
  if (t < minutes(slots[0])) return "before";
  if (t >= minutes(slots[slots.length - 1]) + step) return "after";
  return slotOf(time, slots);
}
export const hourLabel = (hhmm: string) => `${Number(hhmm.slice(0, 2))}h${hhmm.slice(3, 5) === "00" ? "" : hhmm.slice(3, 5)}`;

/** One line of a truck for the screens : the article (never the order number), merged across the orders of the same article. */
export interface ArticleLine { article_id: string; designation: string; unit: string; qty: number; supplier_id: string; supplier_name: string; orders: TransportOrderOut[]; }
export function articleLines(t: TransportOut): ArticleLine[] {
  const out = new Map<string, ArticleLine>();
  for (const o of [...t.orders].sort((a, b) => (a.expected_date ?? "").localeCompare(b.expected_date ?? "") || a.order_id.localeCompare(b.order_id))) {
    const k = `${o.supplier_id}|${o.article_id}`;
    const l = out.get(k);
    if (l) { l.qty += o.qty; l.orders.push(o); } else out.set(k, { article_id: o.article_id, designation: o.designation, unit: o.unit, qty: o.qty, supplier_id: o.supplier_id, supplier_name: o.supplier_name, orders: [o] });
  }
  return [...out.values()];
}
/** A quantity of an article line leaves for the second truck : the orders of the line give, first in first out. */
export function splitItems(line: ArticleLine, qty: number): { order_id: string; qty: number | null }[] {
  const items: { order_id: string; qty: number | null }[] = [];
  let left = qty;
  for (const o of line.orders) {
    if (left <= 1e-9) break;
    const take = Math.min(left, o.qty);
    items.push({ order_id: o.order_id, qty: take >= o.qty - 1e-9 ? null : take });
    left -= take;
  }
  return items;
}
export const qtyLabel = (qty: number, unit: string) => `${fmtQty(qty, unit)}${unit ? ` ${unit}` : ""}`;
export const emptyLine = (): TransportLineIn => ({ article: "", designation: "", qty: "" });
export const cleanLines = (lines: TransportLineIn[]) => lines.filter((l) => l.article.trim() || l.designation.trim() || l.qty.trim());

/** The slot check carried by a 409 (occupied slot), ``null`` for any other error. */
export function conflictOf(err: unknown): SlotCheck | null {
  const e = err as ApiError | undefined;
  if (!e || e.status !== 409 || !e.detail || typeof e.detail !== "object") return null;
  return e.detail as SlotCheck;
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
