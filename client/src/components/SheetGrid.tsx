import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useToast } from "@/components/ui";

export interface SheetCell {
  value: number;
  /** displayed text (defaults to the value) */
  text?: string;
  /** the cell holds its own value (blue border) */
  over?: boolean;
  /** never editable (greyed) */
  ro?: boolean;
  title?: string;
  cls?: string;
}
export type Pos = { r: number; c: number };
/** one requested change ; ``""`` asks the owner to clear the cell (back to its default) */
export interface SheetChange { r: number; c: number; value: number | "" }

/** Parse a pasted / typed value: ``""`` = cleared ; numbers accept the French comma and spaces. */
export function parseCell(raw: string): number | "" | null {
  const t = raw.trim().replace(/\s/g, "").replace(",", ".");
  if (t === "") return "";
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

/**
 * A numeric sheet edited like Excel: click a cell and type (Enter / Tab / arrows move), drag to
 * select a range, Ctrl+C copies it, Ctrl+V pastes a block from Excel (tab-separated ; a single value
 * pasted on a range fills it), the square of the active cell copies its value by dragging (``fillAxis``:
 * down, right or both), Delete clears the selection.  The owner receives the changes and decides.
 */
export function SheetGrid({ rowHeaders, colHeaders, cells, canEdit, onCommit, onClear, corner, ariaLabel, className = "", fillAxis = "col", rowClass, colClass, colWidth }:
  {
    rowHeaders: ReactNode[]; colHeaders: ReactNode[]; cells: SheetCell[][]; canEdit: boolean;
    onCommit: (changes: SheetChange[]) => void; onClear?: (cells: Pos[]) => void;
    corner?: ReactNode; ariaLabel: string; className?: string; fillAxis?: "col" | "row" | "both";
    rowClass?: (r: number) => string; colClass?: (c: number) => string; colWidth?: number;
  }) {
  const toast = useToast();
  const nR = cells.length, nC = colHeaders.length;
  const [anchor, setAnchor] = useState<Pos | null>(null);
  const [focus, setFocus] = useState<Pos | null>(null);
  const [editing, setEditing] = useState<{ p: Pos; value: string } | null>(null);
  const [fill, setFill] = useState<{ from: Pos; to: Pos } | null>(null);
  const dragging = useRef(false);
  const wrap = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const editRef = useRef(editing);          // the edit being closed: Enter then the blur of the input must commit once
  editRef.current = editing;
  useEffect(() => { if (focus && (focus.r >= nR || focus.c >= nC)) { setFocus(null); setAnchor(null); } }, [nR, nC, focus]);
  useEffect(() => {
    const up = () => { dragging.current = false; if (fill) { commitFill(fill); setFill(null); } };
    window.addEventListener("mouseup", up);
    return () => window.removeEventListener("mouseup", up);
  });
  useEffect(() => { if (editing) inputRef.current?.focus(); }, [editing]);

  const range = useMemo(() => {
    if (!anchor || !focus) return null;
    return { r0: Math.min(anchor.r, focus.r), r1: Math.max(anchor.r, focus.r), c0: Math.min(anchor.c, focus.c), c1: Math.max(anchor.c, focus.c) };
  }, [anchor, focus]);
  const inRange = (r: number, c: number) => !!range && r >= range.r0 && r <= range.r1 && c >= range.c0 && c <= range.c1;
  const inFill = (r: number, c: number) => !!fill && r >= Math.min(fill.from.r, fill.to.r) && r <= Math.max(fill.from.r, fill.to.r)
    && c >= Math.min(fill.from.c, fill.to.c) && c <= Math.max(fill.from.c, fill.to.c) && !(r === fill.from.r && c === fill.from.c);
  const editable = (r: number, c: number) => canEdit && !cells[r][c].ro;
  const cellsOf = (rg: { r0: number; r1: number; c0: number; c1: number }) => {
    const out: Pos[] = [];
    for (let r = rg.r0; r <= rg.r1; r++) for (let c = rg.c0; c <= rg.c1; c++) out.push({ r, c });
    return out;
  };
  const selection = () => range ?? (focus ? { r0: focus.r, r1: focus.r, c0: focus.c, c1: focus.c } : null);

  const commit = useCallback((changes: SheetChange[]) => {
    const ok = changes.filter((x) => editable(x.r, x.c));
    if (ok.length) onCommit(ok);
  }, [onCommit, cells, canEdit]);   // eslint-disable-line react-hooks/exhaustive-deps
  const commitFill = (f: { from: Pos; to: Pos }) => {
    const v = cells[f.from.r][f.from.c].value;
    commit(cellsOf({ r0: Math.min(f.from.r, f.to.r), r1: Math.max(f.from.r, f.to.r), c0: Math.min(f.from.c, f.to.c), c1: Math.max(f.from.c, f.to.c) })
      .filter((p) => !(p.r === f.from.r && p.c === f.from.c)).map((p) => ({ ...p, value: v })));
  };
  const fillTarget = (from: Pos, r: number, c: number): Pos => {
    if (fillAxis === "col") return { r, c: from.c };
    if (fillAxis === "row") return { r: from.r, c };
    return Math.abs(r - from.r) >= Math.abs(c - from.c) ? { r, c: from.c } : { r: from.r, c };
  };

  const select = (p: Pos, extend = false) => { setAnchor(extend && anchor ? anchor : p); setFocus(p); };
  const startEdit = (p: Pos, initial?: string) => { if (!editable(p.r, p.c)) return; select(p); setEditing({ p, value: initial ?? String(cells[p.r][p.c].value) }); };
  const endEdit = (saveIt: boolean, move?: Pos) => {
    const ed = editRef.current;
    editRef.current = null;
    if (ed && saveIt) {
      const v = parseCell(ed.value);
      if (v === null) toast.push("Valeur numérique attendue", "error");
      else commit([{ ...ed.p, value: v }]);
    }
    setEditing(null);
    if (move) { select(move); wrap.current?.focus(); }
  };
  const step = (p: Pos, dr: number, dc: number): Pos => ({ r: Math.min(nR - 1, Math.max(0, p.r + dr)), c: Math.min(nC - 1, Math.max(0, p.c + dc)) });

  const selectionText = () => {
    const rg = selection();
    if (!rg) return "";
    return cells.slice(rg.r0, rg.r1 + 1).map((row) => row.slice(rg.c0, rg.c1 + 1).map((x) => String(x.value)).join("\t")).join("\n");
  };
  const copy = async () => {
    const t = selectionText();
    if (!t) return;
    try { await navigator.clipboard.writeText(t); } catch { /* clipboard unavailable */ }
    toast.push("Sélection copiée", "success");
  };
  const paste = (text: string) => {
    if (!canEdit || !focus) return;
    const origin = range ? { r: range.r0, c: range.c0 } : focus;
    const lines = text.replace(/\r/g, "").split("\n").filter((l, i, arr) => !(i === arr.length - 1 && l === ""));
    let changes: SheetChange[] = [];
    let bad = 0;
    lines.forEach((line, dr) => line.split("\t").forEach((cell, dc) => {
      const r = origin.r + dr, c = origin.c + dc;
      if (r >= nR || c >= nC) return;
      const v = parseCell(cell);
      if (v === null) { bad++; return; }
      changes.push({ r, c, value: v });
    }));
    if (lines.length === 1 && !lines[0].includes("\t") && range && (range.r1 > range.r0 || range.c1 > range.c0)) {
      const v = parseCell(lines[0]);
      if (v !== null) changes = cellsOf(range).map((p) => ({ ...p, value: v }));
    }
    if (bad) toast.push(`${bad} cellule(s) non numérique(s) ignorée(s)`, "error");
    commit(changes);
    if (changes.length) setFocus({ r: changes[changes.length - 1].r, c: changes[changes.length - 1].c });
  };
  const clearSelection = () => {
    if (!canEdit) return;
    const rg = selection();
    if (!rg) return;
    const ps = cellsOf(rg).filter((p) => editable(p.r, p.c));
    if (onClear) onClear(ps);
    else {
      const over = ps.filter((p) => cells[p.r][p.c].over);
      if (over.length) onCommit(over.map((p) => ({ ...p, value: "" as const }))); else toast.push("Ces cellules portent déjà la valeur par défaut");
    }
  };
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (editing) return;
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c") { e.preventDefault(); copy(); return; }
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") { e.preventDefault(); setAnchor({ r: 0, c: 0 }); setFocus({ r: nR - 1, c: nC - 1 }); return; }
    if (!focus) return;
    const moves: Record<string, [number, number]> = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] };
    if (moves[e.key]) { e.preventDefault(); const [dr, dc] = moves[e.key]; select(step(focus, dr, dc), e.shiftKey); return; }
    if (e.key === "Enter" || e.key === "F2") { e.preventDefault(); startEdit(focus); return; }
    if (e.key === "Tab") { e.preventDefault(); select(step(focus, 0, e.shiftKey ? -1 : 1)); return; }
    if (e.key === "Delete" || e.key === "Backspace") { e.preventDefault(); clearSelection(); return; }
    if (e.key.length === 1 && !e.ctrlKey && !e.metaKey && /[\d\-.,]/.test(e.key)) { e.preventDefault(); startEdit(focus, e.key); }
  };

  return (
    <div className={`dgrid-wrap wgrid ${fill ? "filling" : ""} ${className}`} ref={wrap} tabIndex={0} onKeyDown={onKeyDown}
      onPaste={(e) => { if (editing) return; e.preventDefault(); paste(e.clipboardData.getData("text/plain")); }}
      onCopy={(e) => { if (editing) return; const t = selectionText(); if (t) { e.clipboardData.setData("text/plain", t); e.preventDefault(); } }}>
      <table className="dgrid" aria-label={ariaLabel}>
        <thead>
          <tr>
            <th className="rowno corner" onClick={() => { setAnchor({ r: 0, c: 0 }); setFocus({ r: nR - 1, c: nC - 1 }); }} title="Tout sélectionner">{corner}</th>
            {colHeaders.map((h, c) => <th key={c} className={`num ${colClass?.(c) ?? ""}`} style={colWidth ? { minWidth: colWidth } : undefined} onClick={() => { setAnchor({ r: 0, c }); setFocus({ r: nR - 1, c }); }} title="Sélectionner la colonne">{h}</th>)}
          </tr>
        </thead>
        <tbody>
          {cells.map((row, r) => (
            <tr key={r} className={rowClass?.(r)}>
              <td className="rowno rowhead" onClick={() => { setAnchor({ r, c: 0 }); setFocus({ r, c: nC - 1 }); }} title="Sélectionner la ligne">{rowHeaders[r]}</td>
              {row.map((x, c) => {
                const isEd = editing && editing.p.r === r && editing.p.c === c;
                const isFocus = !isEd && focus?.r === r && focus?.c === c;
                const cls = ["num", x.over ? "typed over" : "", inRange(r, c) ? "sel" : "", isFocus ? "active" : "", inFill(r, c) ? "fill-range" : "", editable(r, c) ? "" : "ro", x.cls ?? ""].filter(Boolean).join(" ");
                return (
                  <td key={c} className={cls} title={x.title}
                    onMouseDown={(e) => { if (isEd) return; e.preventDefault(); if (editing) endEdit(true); dragging.current = true; select({ r, c }, e.shiftKey); wrap.current?.focus(); }}
                    onMouseEnter={() => { if (fill) setFill({ ...fill, to: fillTarget(fill.from, r, c) }); else if (dragging.current) setFocus({ r, c }); }}
                    onDoubleClick={() => startEdit({ r, c })}>
                    {isEd ? <input ref={inputRef} className="cell-input" value={editing!.value} aria-label={`cellule ${r + 1}-${c + 1}`}
                      onChange={(e) => setEditing({ p: { r, c }, value: e.target.value })}
                      onBlur={() => endEdit(true)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") { e.preventDefault(); endEdit(true, step({ r, c }, 1, 0)); }
                        else if (e.key === "Tab") { e.preventDefault(); endEdit(true, step({ r, c }, 0, e.shiftKey ? -1 : 1)); }
                        else if (e.key === "Escape") { e.preventDefault(); endEdit(false, { r, c }); }
                      }} />
                      : <>{x.text ?? x.value}{isFocus && editable(r, c) && <span className="fill-handle" title="Tirer pour recopier" onMouseDown={(e) => { e.stopPropagation(); e.preventDefault(); setFill({ from: { r, c }, to: { r, c } }); }} />}</>}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
