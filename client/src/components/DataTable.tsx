import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Columns3, Download, FilterX } from "lucide-react";
import { Empty, useToast } from "@/components/ui";
import { exportTable, looksLikeDate } from "@/lib/exportTable";
import { fmtQty } from "@/lib/format";
import type { ExportType } from "@/lib/types";

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
  /** the footer shows the sum of this column (numeric) over the filtered rows */
  sum?: boolean;
  /** unit of the sum in the footer (``(row) => row.unit`` when every row shares it) */
  unit?: (row: T) => string;
  /** Excel export : cell type (inferred from ``num`` / the value when omitted) ; ``false`` leaves the column out */
  export?: ExportType | false;
  /** Excel export : value written in the cell (default : ``get``) */
  exportGet?: (row: T) => string | number | boolean | null | undefined;
  /** hidden until the reader shows it from the « Colonnes » menu (remembered per ``storageKey``) */
  hidden?: boolean;
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

/** Alternatives of a filter : ``123;456`` keeps the rows matching 123 **or** 456. */
export function filterTerms(expr: string): string[] {
  const terms = expr.split(";").map((t) => t.trim()).filter(Boolean);
  return terms.length ? terms : [expr];
}

export function applyFilters<T>(rows: T[], columns: Column<T>[], filters: Record<string, string>): T[] {
  const active = columns.filter((c) => (filters[c.key] ?? "") !== "");
  if (!active.length) return rows;
  return rows.filter((r) => active.every((c) => {
    const f = filters[c.key];
    const v = c.get(r);
    if (c.filter === "number" || (c.num && c.filter !== "text" && c.filter !== "select")) {
      const n = typeof v === "number" ? v : v == null ? null : Number(v);
      return filterTerms(f).some((t) => matchNumber(n, t));
    }
    if (c.filter === "select") return String(v ?? "") === f;
    const hay = String(v ?? "").toLowerCase();
    return filterTerms(f).some((t) => hay.includes(t.toLowerCase()));
  }));
}

type Sort = { key: string; dir: 1 | -1 } | null;
/** ``cols`` : per-column visibility chosen by the reader (absent = the column's default) */
interface Saved { filters: Record<string, string>; sort: Sort; cols: Record<string, boolean> }
const EMPTY: Saved = { filters: {}, sort: null, cols: {} };

function loadSaved(key?: string): Saved {
  if (!key) return EMPTY;
  try { const raw = localStorage.getItem(`appro.table.${key}`); return raw ? { ...EMPTY, ...(JSON.parse(raw) as Partial<Saved>) } : EMPTY; } catch { return EMPTY; }
}

/**
 * Table with a filter row under the header (one filter per column) and sortable headers.
 * Rows are plain objects; each column declares how to read, render and filter its value.
 *
 * * ``storageKey`` : the filters and the sort are remembered between visits ;
 * * ``exportTitle`` : an « Excel » button exports the visible columns and the filtered rows ;
 * * the footer (``footer`` = "auto", the default) shows the row count and the sums of the ``sum``
 *   columns ; ``footer={null}`` hides it, a node replaces it.
 */
export function DataTable<T>({ rows, columns: allColumns, rowKey, onRowClick, rowClass, emptyTitle = "Aucune ligne", emptyHint, compact, maxHeight, footer = "auto", storageKey, exportTitle, toolbar }: {
  rows: T[]; columns: Column<T>[]; rowKey: (row: T) => string; onRowClick?: (row: T) => void; rowClass?: (row: T) => string;
  emptyTitle?: string; emptyHint?: ReactNode; compact?: boolean; maxHeight?: number | string; footer?: ReactNode | "auto" | null;
  storageKey?: string; exportTitle?: string; toolbar?: ReactNode;
}) {
  const [{ filters, sort, cols: colPrefs }, setSaved] = useState<Saved>(() => loadSaved(storageKey));
  const setFilters = (f: Record<string, string>) => setSaved((s) => ({ ...s, filters: f }));
  const setSort = (fn: (s: Sort) => Sort) => setSaved((s) => ({ ...s, sort: fn(s.sort) }));
  const setColPref = (key: string, on: boolean) => setSaved((s) => ({ ...s, cols: { ...s.cols, [key]: on } }));
  useEffect(() => { if (storageKey) { try { localStorage.setItem(`appro.table.${storageKey}`, JSON.stringify({ filters, sort, cols: colPrefs })); } catch { /* private mode */ } } }, [filters, sort, colPrefs, storageKey]);
  const [colsOpen, setColsOpen] = useState(false);
  const hasHidden = allColumns.some((c) => c.hidden);
  const columns = useMemo(() => allColumns.filter((c) => colPrefs[c.key] ?? !c.hidden), [allColumns, colPrefs]);
  const toast = useToast();
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
  const sums = useMemo(() => {
    const out: Record<string, { total: number; unit: string | null }> = {};
    columns.filter((c) => c.sum).forEach((c) => {
      let total = 0, unit: string | null | undefined;
      sorted.forEach((r) => { const v = c.get(r); if (typeof v === "number" && !Number.isNaN(v)) total += v; const u = c.unit?.(r) ?? ""; unit = unit === undefined ? u : unit === u ? u : null; });
      out[c.key] = { total, unit: unit ?? "" };
    });
    return out;
  }, [sorted, columns]);
  const hasSums = Object.keys(sums).length > 0;

  const doExport = async () => {
    const exported = columns.filter((c) => c.export !== false && c.key !== "del" && !(c.filter === "none" && !c.export));
    const colType = (c: Column<T>): ExportType => c.export || (c.num ? "number" : sorted.some((r) => looksLikeDate(c.get(r))) ? "date" : "text");
    const label = (c: Column<T>) => (typeof c.label === "string" ? c.label : c.title ?? c.key);
    const cols = exported.map((c) => ({ key: c.key, label: label(c), type: colType(c) }));
    const body = sorted.map((r) => Object.fromEntries(exported.map((c) => [c.key, (c.exportGet ?? c.get)(r) ?? null])));
    const foot = hasSums ? Object.fromEntries(exported.map((c, i) => [c.key, i === 0 ? `${sorted.length} ligne(s)` : sums[c.key] ? sums[c.key].total : null])) : null;
    try { await exportTable(exportTitle ?? "Export", cols, body, foot); } catch (e) { toast.push((e as Error).message, "error"); }
  };
  const showTools = !!exportTitle || !!toolbar || anyFilter || hasHidden;

  return (
    <div className="dt-wrap">
      {showTools && (
        <div className="dt-tools">
          {toolbar}
          <span className="subtle small">{anyFilter ? `${sorted.length} / ${rows.length} ligne(s)` : `${rows.length} ligne(s)`}</span>
          {anyFilter && <button type="button" className="btn xs ghost" onClick={() => setFilters({})} title="Effacer tous les filtres de colonnes"><FilterX />Effacer les filtres</button>}
          {hasHidden && (
            <span className="dt-cols-wrap">
              <button type="button" className="btn xs ghost" onClick={() => setColsOpen((v) => !v)} aria-expanded={colsOpen} title="Choisir les colonnes affichées"><Columns3 />Colonnes</button>
              {colsOpen && (
                <div className="dt-cols" role="menu">
                  {allColumns.map((c) => (
                    <label key={c.key} className="checkbox small">
                      <input type="checkbox" checked={colPrefs[c.key] ?? !c.hidden} onChange={(e) => setColPref(c.key, e.target.checked)} />
                      {typeof c.label === "string" ? c.label : c.title ?? c.key}
                    </label>
                  ))}
                  <button type="button" className="btn xs ghost" onClick={() => { setSaved((s) => ({ ...s, cols: {} })); setColsOpen(false); }}>Par défaut</button>
                </div>
              )}
            </span>
          )}
          {exportTitle && <button type="button" className="btn xs" onClick={doExport} title="Exporter les lignes filtrées et les colonnes affichées dans un classeur Excel" disabled={!sorted.length}><Download />Excel</button>}
        </div>
      )}
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
                    <input className="input xs" value={filters[c.key] ?? ""} placeholder={c.filter === "number" || c.num ? "= > < a-b ; ou" : "filtrer ; ou"} title="Plusieurs valeurs séparées par « ; » : l'une ou l'autre" aria-label={`Filtre ${typeof c.label === "string" ? c.label : c.key}`}
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
          {footer === "auto" ? (hasSums && sorted.length > 0 && (
            <tfoot className="dt-sums"><tr>
              {columns.map((c, i) => <td key={c.key} className={c.num ? "num" : ""}>{i === 0 ? <span className="subtle">{sorted.length} ligne(s)</span> : sums[c.key] ? <b title="Total des lignes filtrées">{fmtQty(sums[c.key].total, sums[c.key].unit ?? undefined)}{sums[c.key].unit ? <span className="subtle"> {sums[c.key].unit}</span> : null}</b> : null}</td>)}
            </tr></tfoot>
          )) : footer ? <tfoot><tr><td colSpan={columns.length}>{footer}</td></tr></tfoot> : null}
        </table>
      </div>
    </div>
  );
}
