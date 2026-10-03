import { useMemo, useState, type ReactNode } from "react";
import { Empty } from "@/components/ui";

/** Column of a filterable / sortable table.  ``get`` returns the raw value used by the filter and the sort. */
export interface Column<T> {
  key: string;
  label: ReactNode;
  get: (row: T) => string | number | null | undefined;
  render?: (row: T) => ReactNode;
  num?: boolean;
  /** text (contains), select (one of the distinct values), number (=, >, <, >=, <=, a-b), none */
  filter?: "text" | "select" | "number" | "none";
  sortable?: boolean;
  width?: number | string;
  title?: string;
}

/** Number filter: ``>100``, ``<=5``, ``100-200``, ``=0`` or a plain number (exact). */
export function matchNumber(value: number | null | undefined, expr: string): boolean {
  const e = expr.replace(/\s+/g, "").replace(",", ".");
  if (!e) return true;
  if (value === null || value === undefined || Number.isNaN(value)) return false;
  const range = e.match(/^(-?\d+(?:\.\d+)?)-(-?\d+(?:\.\d+)?)$/);
  if (range) return value >= Number(range[1]) && value <= Number(range[2]);
  const m = e.match(/^(>=|<=|>|<|=|!=)?(-?\d+(?:\.\d+)?)$/);
  if (!m) return true;
  const n = Number(m[2]);
  switch (m[1]) {
    case ">": return value > n;
    case "<": return value < n;
    case ">=": return value >= n;
    case "<=": return value <= n;
    case "!=": return value !== n;
    default: return value === n;
  }
}

export function applyFilters<T>(rows: T[], columns: Column<T>[], filters: Record<string, string>): T[] {
  const active = columns.filter((c) => (filters[c.key] ?? "") !== "");
  if (!active.length) return rows;
  return rows.filter((r) => active.every((c) => {
    const f = filters[c.key];
    const v = c.get(r);
    if (c.filter === "number" || (c.num && c.filter !== "text" && c.filter !== "select")) return matchNumber(typeof v === "number" ? v : v == null ? null : Number(v), f);
    if (c.filter === "select") return String(v ?? "") === f;
    return String(v ?? "").toLowerCase().includes(f.toLowerCase());
  }));
}

/**
 * Table with a filter row under the header (one filter per column) and sortable headers.
 * Rows are plain objects; each column declares how to read, render and filter its value.
 */
export function DataTable<T>({ rows, columns, rowKey, onRowClick, rowClass, emptyTitle = "Aucune ligne", emptyHint, compact, maxHeight, footer }: {
  rows: T[]; columns: Column<T>[]; rowKey: (row: T) => string; onRowClick?: (row: T) => void; rowClass?: (row: T) => string;
  emptyTitle?: string; emptyHint?: ReactNode; compact?: boolean; maxHeight?: number | string; footer?: ReactNode;
}) {
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<{ key: string; dir: 1 | -1 } | null>(null);
  const filtered = useMemo(() => applyFilters(rows, columns, filters), [rows, columns, filters]);
  const sorted = useMemo(() => {
    if (!sort) return filtered;
    const col = columns.find((c) => c.key === sort.key);
    if (!col) return filtered;
    return [...filtered].sort((a, b) => {
      const va = col.get(a), vb = col.get(b);
      if (va == null && vb == null) return 0;
      if (va == null) return 1;
      if (vb == null) return -1;
      const cmp = typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb), "fr", { numeric: true });
      return cmp * sort.dir;
    });
  }, [filtered, sort, columns]);
  const options = useMemo(() => {
    const m: Record<string, string[]> = {};
    columns.filter((c) => c.filter === "select").forEach((c) => {
      m[c.key] = Array.from(new Set(rows.map((r) => String(c.get(r) ?? "")))).filter((x) => x !== "").sort((a, b) => a.localeCompare(b, "fr", { numeric: true }));
    });
    return m;
  }, [rows, columns]);
  const anyFilter = Object.values(filters).some((v) => v !== "");
  const toggleSort = (key: string) => setSort((s) => (s?.key === key ? (s.dir === 1 ? { key, dir: -1 } : null) : { key, dir: 1 }));

  return (
    <div className="dt" style={maxHeight ? { maxHeight, overflow: "auto" } : undefined}>
      <table className={`tbl ${compact ? "compact" : ""}`}>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} className={`${c.num ? "num" : ""} ${c.sortable !== false ? "sortable" : ""}`} style={c.width ? { width: c.width } : undefined} title={c.title}
                onClick={c.sortable !== false ? () => toggleSort(c.key) : undefined}>
                {c.label}{sort?.key === c.key ? (sort.dir === 1 ? " ▴" : " ▾") : ""}
              </th>
            ))}
          </tr>
          <tr className="dt-filters">
            {columns.map((c) => (
              <th key={c.key} className={c.num ? "num" : ""}>
                {c.filter === "none" ? null : c.filter === "select" ? (
                  <select className="select xs" value={filters[c.key] ?? ""} onChange={(e) => setFilters({ ...filters, [c.key]: e.target.value })} aria-label={`Filtre ${typeof c.label === "string" ? c.label : c.key}`}>
                    <option value="">tous</option>
                    {(options[c.key] ?? []).map((o) => <option key={o} value={o}>{o}</option>)}
                  </select>
                ) : (
                  <input className="input xs" value={filters[c.key] ?? ""} placeholder={c.filter === "number" || c.num ? "= > < a-b" : "filtrer"} aria-label={`Filtre ${typeof c.label === "string" ? c.label : c.key}`}
                    onChange={(e) => setFilters({ ...filters, [c.key]: e.target.value })} />
                )}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {sorted.length === 0 ? (
            <tr><td colSpan={columns.length}><Empty title={anyFilter ? "Aucune ligne ne correspond aux filtres" : emptyTitle} hint={anyFilter ? <button className="btn xs" onClick={() => setFilters({})}>Effacer les filtres</button> : emptyHint} /></td></tr>
          ) : sorted.map((r) => (
            <tr key={rowKey(r)} className={`${onRowClick ? "clickable" : ""} ${rowClass?.(r) ?? ""}`} onClick={onRowClick ? () => onRowClick(r) : undefined}>
              {columns.map((c) => <td key={c.key} className={c.num ? "num" : ""}>{c.render ? c.render(r) : (c.get(r) ?? "")}</td>)}
            </tr>
          ))}
        </tbody>
        {footer && <tfoot><tr><td colSpan={columns.length}>{footer}</td></tr></tfoot>}
      </table>
      {anyFilter && <div className="small subtle" style={{ padding: "4px 12px" }}>{sorted.length} / {rows.length} lignes · <button className="btn xs ghost" onClick={() => setFilters({})}>effacer les filtres</button></div>}
    </div>
  );
}
