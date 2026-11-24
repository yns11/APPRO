/** Cells an undo / redo just rewrote : the grid highlights them for a moment. */
import { useEffect, useState } from "react";
import type { TouchedCell } from "./types";

const FLASH_MS = 2500;
let current: TouchedCell[] = [];
const listeners = new Set<() => void>();
let timer: ReturnType<typeof setTimeout> | undefined;

export function flashCells(cells: TouchedCell[]) {
  current = cells;
  listeners.forEach((fn) => fn());
  if (timer) clearTimeout(timer);
  timer = setTimeout(() => { current = []; listeners.forEach((fn) => fn()); }, FLASH_MS);
}

export function useFlashedCells(): TouchedCell[] {
  const [cells, setCells] = useState<TouchedCell[]>(current);
  useEffect(() => {
    const fn = () => setCells(current);
    listeners.add(fn);
    return () => { listeners.delete(fn); };
  }, []);
  return cells;
}

/** Whether a grid cell (article, supplier or null, period [start, end]) holds a flashed day. */
export function isFlashed(cells: TouchedCell[], entity: TouchedCell["entity"], articleId: string, supplierId: string | null, start: string, end: string): boolean {
  return cells.some((c) => c.entity === entity && c.article_id === articleId && (entity === "adjustment" || (c.supplier_id ?? null) === (supplierId || null))
    && !!c.date && c.date >= start && c.date <= end);
}
