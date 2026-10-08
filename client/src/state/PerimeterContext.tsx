import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { DISPLAY_DEFAULTS, applyDisplay, type Display } from "@/lib/display";
import type { AccessOut, ConfigOut } from "@/lib/types";

/** Global "perimeter" of the analysis: planner, horizon, granularity, theme, hidden grid rows. */
export interface Perimeter {
  planner: string | null;
  /** the user picked a perimeter in the top bar (null = everybody) ; until then the default applies */
  plannerChosen?: boolean;
  horizonDays: number;
  granularity: "default" | "day" | "week";
  theme: "light" | "dark" | "system";
  /** keys of the simulation grid rows hidden for every article (e.g. "orders_forecast", "stock_erp") */
  hiddenRows: string[];
  /** day columns of the grid: show Saturdays / Sundays */
  showSaturday: boolean;
  showSunday: boolean;
  /** ISO weeks displayed by the supply tables ("2026-W40" → "2026-W45") ; null = the whole window */
  weekRange: { from: string; to: string } | null;
  /** navigation panel reduced to its icons */
  sidebarCollapsed: boolean;
  /** fonts and sizes (Paramètres › Affichage) */
  display: Display;
}

/** What the signed-in user may write (mirror of the server rules ; the server enforces them). */
export interface Rights {
  access: AccessOut | undefined;
  /** the Plan / Ajustement / Ferme / CBN cells of this article's planner are editable */
  canEditPlanner: (planner: string | null | undefined) => boolean;
  canWrite: boolean;
  canManageParams: boolean;
  canImportPdp: boolean;
  isAdmin: boolean;
  /** add / edit rows of a reference table (row-level rules still apply on the server) */
  canEditTable: (name: string) => boolean;
  /** replace / import a whole reference table */
  canImportTable: (name: string) => boolean;
}

interface Ctx {
  perimeter: Perimeter;
  set: (patch: Partial<Perimeter>) => void;
  config: ConfigOut | undefined;
  configError: Error | null;
  /** query params shared by every engine call */
  engineParams: { planner: string | null; horizon_days: number };
  rights: Rights;
}

const KEY = "appro.perimeter.v2";
const defaults: Perimeter = { planner: null, horizonDays: 120, granularity: "default", theme: "system", hiddenRows: [], showSaturday: true, showSunday: true, weekRange: null, sidebarCollapsed: false, display: DISPLAY_DEFAULTS };

function load(): Perimeter {
  try {
    const raw = localStorage.getItem(KEY);
    const saved = raw ? (JSON.parse(raw) as Partial<Perimeter>) : {};
    return { ...defaults, ...saved, display: { ...DISPLAY_DEFAULTS, ...(saved.display ?? {}) } };
  } catch {
    return defaults;
  }
}

const PerimeterCtx = createContext<Ctx | null>(null);

export function PerimeterProvider({ children }: { children: ReactNode }) {
  const [perimeter, setPerimeter] = useState<Perimeter>(load);
  const { data: config, error } = useQuery({ queryKey: ["config"], queryFn: () => api.get<ConfigOut>("/api/config"), staleTime: 60_000 });

  useEffect(() => {
    try { localStorage.setItem(KEY, JSON.stringify(perimeter)); } catch { /* private mode */ }
  }, [perimeter]);

  useEffect(() => {
    if (config && perimeter.planner === null && !perimeter.plannerChosen && config.default_planner) setPerimeter((p) => ({ ...p, planner: config.default_planner }));
  }, [config, perimeter.planner, perimeter.plannerChosen]);

  useEffect(() => {
    const root = document.documentElement;
    const apply = () => {
      const dark = perimeter.theme === "dark" || (perimeter.theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
      root.setAttribute("data-theme", dark ? "dark" : "light");
    };
    apply();
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, [perimeter.theme]);

  useEffect(() => { applyDisplay(perimeter.display); }, [perimeter.display]);

  const set = useCallback((patch: Partial<Perimeter>) => setPerimeter((p) => ({ ...p, ...patch })), []);
  const engineParams = useMemo(() => ({ planner: perimeter.planner, horizon_days: perimeter.horizonDays }), [perimeter.planner, perimeter.horizonDays]);
  const rights = useMemo<Rights>(() => {
    const a = config?.access;
    const portfolio = new Set((a?.portfolio ?? []).map((p) => p.toUpperCase()));
    const ARTICLE_TABLES = ["ref_articles", "ref_article_suppliers", "ref_bom", "fct_stock", "ref_delegations"];
    return {
      access: a,
      canEditPlanner: (planner) => !!a && (a.is_admin || (!!planner && portfolio.has(planner.toUpperCase()))),
      canWrite: !!a?.can_write, canManageParams: !!a?.can_manage_params, canImportPdp: !!a?.can_import_pdp, isAdmin: !!a?.is_admin,
      canEditTable: (name) => !!a && (a.is_admin || (a.can_manage_params && name !== "ref_planners") || (a.can_write && ARTICLE_TABLES.includes(name))),
      canImportTable: (name) => !!a && (a.is_admin || (a.can_manage_params && name !== "ref_planners")),
    };
  }, [config]);
  const value = useMemo(() => ({ perimeter, set, config, configError: error as Error | null, engineParams, rights }), [perimeter, set, config, error, engineParams, rights]);
  return <PerimeterCtx.Provider value={value}>{children}</PerimeterCtx.Provider>;
}

export function usePerimeter(): Ctx {
  const ctx = useContext(PerimeterCtx);
  if (!ctx) throw new Error("usePerimeter outside provider");
  return ctx;
}
