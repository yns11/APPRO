import { format, parseISO, differenceInCalendarDays, getISOWeek, getISOWeekYear } from "date-fns";
import { fr } from "date-fns/locale";

const nf0 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1 });
const nf3 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 3 });
const compact = new Intl.NumberFormat("fr-FR", { notation: "compact", maximumFractionDigits: 1 });

export function fmtQty(v: number | null | undefined, unit?: string): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  const isPiece = !unit || ["PCE", "PC", "PCS", "EA"].includes(unit.toUpperCase());
  const abs = Math.abs(v);
  if (isPiece) return nf0.format(v);
  return abs >= 1000 ? nf0.format(v) : abs >= 10 ? nf1.format(v) : nf3.format(v);
}
export function fmtInt(v: number | null | undefined): string { return v === null || v === undefined ? "–" : nf0.format(v); }
export function fmtCompact(v: number | null | undefined): string { return v === null || v === undefined ? "–" : compact.format(v); }
export function fmtPct(v: number | null | undefined, digits = 0): string { return v === null || v === undefined ? "–" : `${(100 * v).toFixed(digits)} %`; }
export function fmtDate(iso: string | null | undefined, pattern = "dd/MM/yyyy"): string {
  if (!iso) return "–";
  try { return format(parseISO(iso), pattern, { locale: fr }); } catch { return iso; }
}
export function fmtDateTime(iso: string): string { return fmtDate(iso, "dd/MM/yyyy HH:mm"); }
export function daysFrom(iso: string | null | undefined, from: string): number | null {
  if (!iso) return null;
  return differenceInCalendarDays(parseISO(iso), parseISO(from));
}
export function isWeekend(iso: string): boolean { const d = parseISO(iso).getDay(); return d === 0 || d === 6; }
export function isSaturday(iso: string): boolean { return parseISO(iso).getDay() === 6; }
export function isSunday(iso: string): boolean { return parseISO(iso).getDay() === 0; }
/** ISO week of a date, as the week columns are labelled ("2026 S40"). */
export function isoWeekOf(iso: string): string { const d = parseISO(iso); return `${getISOWeekYear(d)} S${String(getISOWeek(d)).padStart(2, "0")}`; }
/** ISO week key of a date, as the week columns are keyed ("2026-W40"). */
export function isoWeekKey(iso: string): string { const d = parseISO(iso); return `${getISOWeekYear(d)}-W${String(getISOWeek(d)).padStart(2, "0")}`; }
/** A column key is either an ISO week label ("2026-W38") or an ISO date. */
export const isWeekKey = (p: string) => p.includes("-W");
export function periodLabel(p: string): string {
  if (isWeekKey(p)) return p.replace("-W", " S");
  return fmtDate(p, "EEE dd/MM").replace(".", "");
}
export const ALERT_LABELS: Record<string, string> = {
  STOCKOUT: "Rupture", LOW_COVERAGE: "Couverture insuffisante", OVERSTOCK: "Surstock", BACKLOG: "Backlog fournisseur",
  URGENT_PROPOSAL: "Commande urgente", NO_DEMAND: "Sans besoin", MISSING_DATA: "Données manquantes", NEGATIVE_STOCK: "Stock de départ négatif",
};
export const SEVERITY_LABELS: Record<string, string> = { critical: "Critique", warning: "À surveiller", info: "Info" };
export const SCOPE_LABELS: Record<string, string> = { erp: "ERP", plan: "plan", data: "données" };
export const ORDER_TYPE_LABELS: Record<string, string> = { FIRM: "Ferme", FORECAST: "Prévisionnelle" };

const eur0 = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const eurCompact = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", notation: "compact", maximumFractionDigits: 1 });
/** Euros, full (``12 345 €``) or compact (``1,2 M€``) for the KPI tiles and chart axes. */
export function fmtEur(v: number | null | undefined, compactForm = false): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  return compactForm ? eurCompact.format(v) : eur0.format(v);
}
