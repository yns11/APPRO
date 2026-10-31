import { format, parseISO, differenceInCalendarDays, getISOWeek, getISOWeekYear } from "date-fns";
import { enGB, fr } from "date-fns/locale";

const nf0 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1 });
const nf3 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 3 });
const compact = new Intl.NumberFormat("fr-FR", { notation: "compact", maximumFractionDigits: 1 });

const nf3fixed = new Intl.NumberFormat("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 });

/** Piece-like units are displayed as integers ; the others (KG, M, L…) are « decimal » articles. */
export const isPieceUnit = (unit?: string | null): boolean => !unit || ["PCE", "PC", "PCS", "EA", "U", "UN", "PIECE", "PIÈCE"].includes(unit.toUpperCase());

export function fmtQty(v: number | null | undefined, unit?: string): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  const abs = Math.abs(v);
  if (isPieceUnit(unit)) return nf0.format(v);
  return abs >= 1000 ? nf0.format(v) : abs >= 10 ? nf1.format(v) : nf3.format(v);
}
/** Three decimals, always (Besoin and stock rows of a decimal article : KG, M, decimal bill of material). */
export function fmtQty3(v: number | null | undefined): string { return v === null || v === undefined || Number.isNaN(v) ? "–" : nf3fixed.format(v); }
/** Decimal article : non-piece unit, or a requirement that is not a whole number somewhere (decimal bill of material). */
export function isDecimalArticle(unit: string | undefined, demand: number[] | undefined): boolean {
  if (!isPieceUnit(unit)) return true;
  return !!demand && demand.some((x) => Math.abs(x - Math.round(x)) > 1e-6);
}
/** Quantity of a grid cell : three decimals for a decimal article, the usual format otherwise. */
export const fmtCellQty = (v: number, unit: string, decimal: boolean): string => (decimal ? fmtQty3(v) : fmtQty(v, unit));
export function fmtInt(v: number | null | undefined): string { return v === null || v === undefined ? "–" : nf0.format(v); }
export function fmtCompact(v: number | null | undefined): string { return v === null || v === undefined ? "–" : compact.format(v); }
export function fmtPct(v: number | null | undefined, digits = 0): string { return v === null || v === undefined ? "–" : `${(100 * v).toFixed(digits)} %`; }
export function fmtDate(iso: string | null | undefined, pattern = "dd/MM/yyyy", lang: "fr" | "en" = "fr"): string {
  if (!iso) return "–";
  try { return format(parseISO(iso), pattern, { locale: lang === "en" ? enGB : fr }); } catch { return iso; }
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
export function periodLabel(p: string, lang: "fr" | "en" = "fr"): string {
  if (isWeekKey(p)) return p.replace("-W", lang === "en" ? " W" : " S");
  return fmtDate(p, "EEE dd/MM", lang).replace(".", "");
}
/** Week chip label : "S43" in French, "W43" in English. */
export function weekChipLabel(key: string, lang: "fr" | "en" = "fr"): string { return key.replace(/^\d{4}-W/, lang === "en" ? "W" : "S"); }
export const ALERT_LABELS: Record<string, string> = {
  STOCKOUT: "Rupture Appro.", LOW_COVERAGE: "Couverture à date", OVERSTOCK: "Surstock", BACKLOG: "Backlog fournisseur", ORDER_DUE: "Commande à placer",
  URGENT_PROPOSAL: "Commande urgente", NO_DEMAND: "Sans besoin", MISSING_DATA: "Données manquantes", NEGATIVE_STOCK: "Stock de départ négatif",
  CAP_REACHED: "Plafond fournisseur atteint", UNLINKED_FLOWS: "Flux hors lien actif", STOCK_DRIFT: "Dérive du stock ERP",
};
export const PLAN_ORIGIN_LABELS: Record<string, string> = {
  monday: "version connue le lundi", later: "version importée après coup", erp: "plan ERP", latest: "PDP actuel", none: "aucun plan",
};
export const WEEKDAY_SHORT = ["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"];
export const WEEKDAY_LONG = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"];
/** ``"1,2,4"`` → « Lun · Mar · Jeu » */
export function fmtWeekdays(spec: string | null | undefined): string {
  if (!spec) return "Lun → Ven";
  const days = spec.split(",").map((x) => Number(x.trim())).filter((x) => x >= 1 && x <= 7);
  if (days.length === 5 && days.every((d, i) => d === i + 1)) return "Lun → Ven";
  return days.map((d) => WEEKDAY_SHORT[d - 1]).join(" · ");
}
export const CONSUMPTION_SOURCE_LABELS: Record<string, string> = { erp: "Consommation MOM", plan: "PDP éclaté", derived: "Dérivée d'autres articles" };
export const CONSUMPTION_SOURCE_SHORT: Record<string, string> = { erp: "MOM", plan: "PDP", derived: "Dérivée" };
export const SOURCING_LABELS: Record<string, string> = { quota: "Quotas", priority: "Priorité" };
/** ``demand_days`` in words : « Lun → Mer (3 j) », « toute la semaine ». */
export function fmtDemandDays(n: number): string {
  if (n >= 5) return "toute la semaine";
  if (n === 1) return "lundi seulement";
  return `${WEEKDAY_SHORT[0]} → ${WEEKDAY_SHORT[n - 1]} (${n} j)`;
}
export function fmtMinutes(min: number | null | undefined): string {
  if (min === null || min === undefined) return "–";
  if (min < 60) return `${min} min`;
  const h = Math.floor(min / 60), m = min % 60;
  if (h < 48) return m ? `${h} h ${String(m).padStart(2, "0")}` : `${h} h`;
  return `${Math.floor(h / 24)} j`;
}
export const SEVERITY_LABELS: Record<string, string> = { critical: "Critique", warning: "À commander", info: "Info" };
/** Kind of purchase order a receipt belongs to. */
export const ORDER_KIND_LABELS: Record<string, string> = { co: "CO", po: "PO", other: "autre" };
export const STATUS_LABELS: Record<string, string> = { critical: "Critique", to_order: "À commander", ok: "OK" };
export const STATUS_TONE: Record<string, "critical" | "warning" | "ok"> = { critical: "critical", to_order: "warning", ok: "ok" };
export const SCOPE_LABELS: Record<string, string> = { erp: "ERP", plan: "Appro.", data: "données" };
export const ORDER_TYPE_LABELS: Record<string, string> = { FIRM: "Ferme", FORECAST: "Prévisionnelle" };

const eur0 = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", maximumFractionDigits: 0 });
const eurCompact = new Intl.NumberFormat("fr-FR", { style: "currency", currency: "EUR", notation: "compact", maximumFractionDigits: 1 });
/** Euros, full (``12 345 €``) or compact (``1,2 M€``) for the KPI tiles and chart axes. */
export function fmtEur(v: number | null | undefined, compactForm = false): string {
  if (v === null || v === undefined || Number.isNaN(v)) return "–";
  return compactForm ? eurCompact.format(v) : eur0.format(v);
}
