import { useCallback, useEffect, useState } from "react";

/**
 * Page state remembered between visits (filters, chosen tab, text searched…), per browser.
 * ``usePageState("tableau", { program: "", suppliers: [] })`` returns the saved value (merged over
 * the defaults, so a new field never breaks an old save) and a patch function.
 */
export function usePageState<T extends object>(key: string, defaults: T): [T, (patch: Partial<T> | ((prev: T) => Partial<T>)) => void, () => void] {
  const storageKey = `appro.page.${key}`;
  const [state, setState] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      return raw ? { ...defaults, ...(JSON.parse(raw) as Partial<T>) } : defaults;
    } catch {
      return defaults;
    }
  });
  useEffect(() => {
    try { localStorage.setItem(storageKey, JSON.stringify(state)); } catch { /* private mode */ }
  }, [state, storageKey]);
  const patch = useCallback((p: Partial<T> | ((prev: T) => Partial<T>)) => setState((prev) => ({ ...prev, ...(typeof p === "function" ? p(prev) : p) })), []);
  const reset = useCallback(() => setState(defaults), [defaults]);   // eslint-disable-line react-hooks/exhaustive-deps
  return [state, patch, reset];
}

/** One remembered value (a tab, a toggle), same storage as :func:`usePageState`. */
export function useRemembered<T>(key: string, initial: T): [T, (v: T) => void] {
  const storageKey = `appro.page.${key}`;
  const [v, setV] = useState<T>(() => { try { const raw = localStorage.getItem(storageKey); return raw ? (JSON.parse(raw) as T) : initial; } catch { return initial; } });
  const set = useCallback((x: T) => { setV(x); try { localStorage.setItem(storageKey, JSON.stringify(x)); } catch { /* private mode */ } }, [storageKey]);
  return [v, set];
}
