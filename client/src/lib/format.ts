import { format, parseISO, differenceInCalendarDays } from "date-fns";
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
export function fmtDateShort(iso: string): string { return fmtDate(iso, "EEE dd/MM"); }
export function fmtDateTime(iso: string): string { return fmtDate(iso, "dd/MM/yyyy HH:mm"); }
export function daysFrom(iso: string | null | undefined, from: string): number | null {
  if (!iso) return null;
  return differenceInCalendarDays(parseISO(iso), parseISO(from));
}
export function isWeekend(iso: string): boolean { const d = parseISO(iso).getDay(); return d === 0 || d === 6; }
export function periodLabel(p: string, granularity: "day" | "week"): string {
  if (granularity === "week") return p.replace("-W", " S");
  return fmtDate(p, "EEE dd/MM");
}
export const ALERT_LABELS: Record<string, string> = {
  STOCKOUT: "Rupture", LOW_COVERAGE: "Couverture insuffisante", OVERSTOCK: "Surstock", LATE_ORDER: "Retard fournisseur",
  URGENT_PROPOSAL: "Commande urgente", NO_DEMAND: "Sans besoin", MISSING_DATA: "Données manquantes", NEGATIVE_STOCK: "Stock de départ négatif",
};
export const SEVERITY_LABELS: Record<string, string> = { critical: "Critique", warning: "À surveiller", info: "Info" };
export const KIND_LABELS: Record<string, string> = { order: "Commande", receipt: "Réception", movement: "Ajustement", sim_order: "Commande simulée", proposal: "Proposition" };
export const SCOPE_LABELS: Record<string, string> = { firm: "ferme", forecast: "prévisionnel", simulated: "simulé", data: "données" };
export const ORDER_TYPE_LABELS: Record<string, string> = { FIRM: "Ferme", FORECAST: "Prévisionnelle", PLANNED: "Planifiée", SIMULATED: "Simulée", ADJUSTMENT: "Ajustement", PROPOSAL: "Proposition", RECEIPT: "Réception" };
export const EVENT_KIND_LABELS: Record<string, string> = {
  add_order: "Ajouter une commande", move_order: "Décaler une commande", change_order_qty: "Modifier une quantité", cancel_order: "Annuler une commande",
  plan_factor: "PDP × facteur", set_plan: "Fixer une semaine de PDP", set_actual: "Production réelle", add_movement: "Ajustement de stock",
  set_article_param: "Paramètre article", set_link_param: "Paramètre fournisseur",
};
