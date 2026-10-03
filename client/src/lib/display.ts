/** Personalisation of the display (fonts, sizes): stored per browser, applied as CSS variables on <html>. */
import "@fontsource-variable/inter/index.css";
import "@fontsource/ibm-plex-sans/latin-400.css";
import "@fontsource/ibm-plex-sans/latin-500.css";
import "@fontsource/ibm-plex-sans/latin-600.css";
import "@fontsource-variable/source-sans-3/index.css";
import "@fontsource/roboto-condensed/latin-400.css";
import "@fontsource/roboto-condensed/latin-500.css";
import "@fontsource/roboto-condensed/latin-600.css";

export type FontId = "system" | "inter" | "plex" | "source" | "roboto-condensed";
export type GridFontId = "same" | "inter" | "plex" | "source" | "roboto-condensed";

export interface FontOption { id: FontId; label: string; stack: string; note: string }
export const FONTS: FontOption[] = [
  { id: "system", label: "Système (actuelle)", stack: 'ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif', note: "police du poste de travail (Segoe UI sous Windows)" },
  { id: "inter", label: "Inter", stack: '"Inter Variable", "Inter", ui-sans-serif, system-ui, sans-serif', note: "chiffres tabulaires, très lisible à petite taille" },
  { id: "plex", label: "IBM Plex Sans", stack: '"IBM Plex Sans", ui-sans-serif, system-ui, sans-serif', note: "chiffres bien différenciés (0 / O, 1 / l), rendu plus aéré" },
  { id: "source", label: "Source Sans 3", stack: '"Source Sans 3 Variable", "Source Sans 3", ui-sans-serif, system-ui, sans-serif', note: "plus étroite : plus de colonnes à l'écran, lisibilité conservée" },
  { id: "roboto-condensed", label: "Roboto Condensed", stack: '"Roboto Condensed", ui-sans-serif, system-ui, sans-serif', note: "condensée : tableau dense, grands nombres sans débordement" },
];
export const BASE_SIZES = [{ id: 14, label: "Compacte (14 px)" }, { id: 15, label: "Réduite (15 px)" }, { id: 16, label: "Normale (16 px)" }, { id: 17, label: "Grande (17 px)" }] as const;
export const GRID_SIZES = [{ id: 10, label: "10 px" }, { id: 11, label: "11 px (actuelle)" }, { id: 12, label: "12 px" }, { id: 13, label: "13 px" }] as const;

export interface Display { font: FontId; gridFont: GridFontId; baseSize: number; gridSize: number }
export const DISPLAY_DEFAULTS: Display = { font: "system", gridFont: "same", baseSize: 16, gridSize: 11 };

export function fontStack(id: FontId | GridFontId, fallback: FontId): string {
  const f = FONTS.find((x) => x.id === (id === "same" ? fallback : id)) ?? FONTS[0];
  return f.stack;
}

/** Apply the display settings as CSS variables (see styles/tokens.css). */
export function applyDisplay(d: Display): void {
  const root = document.documentElement;
  root.style.setProperty("--font-sans", fontStack(d.font, "system"));
  root.style.setProperty("--font-grid", fontStack(d.gridFont, d.font));
  root.style.setProperty("--base-fs", `${d.baseSize}px`);
  root.style.setProperty("--grid-fs", `${d.gridSize}px`);
}
