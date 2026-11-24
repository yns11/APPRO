import type { ExportColumn } from "./types";

/**
 * Excel export of any screen table : the visible columns and the filtered rows are sent to
 * ``POST /api/exports/table.xlsx`` (openpyxl types the cells) and the workbook is downloaded.
 */
export async function exportTable(title: string, columns: ExportColumn[], rows: Record<string, unknown>[], footer?: Record<string, unknown> | null, sheet?: string): Promise<void> {
  const res = await fetch("/api/exports/table.xlsx", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title, sheet: (sheet ?? title).slice(0, 31), columns, rows, footer: footer ?? null }),
  });
  if (!res.ok) throw new Error(`Export impossible (HTTP ${res.status})`);
  const blob = await res.blob();
  const disposition = res.headers.get("Content-Disposition") ?? "";
  const m = disposition.match(/filename="([^"]+)"/);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = m?.[1] ?? "export.xlsx";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

/** ``"2026-09-19"`` → true : an ISO date the server may type as a date cell. */
export const looksLikeDate = (v: unknown): boolean => typeof v === "string" && /^\d{4}-\d{2}-\d{2}/.test(v);
