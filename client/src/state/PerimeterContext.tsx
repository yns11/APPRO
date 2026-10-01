import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import type { ConfigOut } from "@/lib/types";

/** Global "perimeter" of the analysis: planner, horizon, granularity, theme, hidden grid rows. */
export interface Perimeter {
  planner: string | null;
  horizonDays: number;
  granularity: "default" | "day" | "week";
  theme: "light" | "dark" | "system";
  /** keys of the simulation grid rows hidden for every article (e.g. "orders_forecast", "stock_erp") */
  hiddenRows: string[];
  /** day columns of the grid: show Saturdays / Sundays */
  showSaturday: boolean;
  showSunday: boolean;
  /** navigation panel reduced to its icons */
  sidebarCollapsed: boolean;
}

interface Ctx {
  perimeter: Perimeter;
  set: (patch: Partial<Perimeter>) => void;
  config: ConfigOut | undefined;
  configError: Error | null;
  /** query params shared by every engine call */
  engineParams: { planner: string | null; horizon_days: number };
}

const KEY = "appro.perimeter.v2";
const defaults: Perimeter = { planner: null, horizonDays: 120, granularity: "default", theme: "system", hiddenRows: [], showSaturday: true, showSunday: true, sidebarCollapsed: false };

function load(): Perimeter {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? { ...defaults, ...(JSON.parse(raw) as Partial<Perimeter>) } : defaults;
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
    if (config && perimeter.planner === null && config.default_planner) setPerimeter((p) => ({ ...p, planner: config.default_planner }));
  }, [config, perimeter.planner]);

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

  const set = useCallback((patch: Partial<Perimeter>) => setPerimeter((p) => ({ ...p, ...patch })), []);
  const engineParams = useMemo(() => ({ planner: perimeter.planner, horizon_days: perimeter.horizonDays }), [perimeter.planner, perimeter.horizonDays]);
  const value = useMemo(() => ({ perimeter, set, config, configError: error as Error | null, engineParams }), [perimeter, set, config, error, engineParams]);
  return <PerimeterCtx.Provider value={value}>{children}</PerimeterCtx.Provider>;
}

export function usePerimeter(): Ctx {
  const ctx = useContext(PerimeterCtx);
  if (!ctx) throw new Error("usePerimeter outside provider");
  return ctx;
}
