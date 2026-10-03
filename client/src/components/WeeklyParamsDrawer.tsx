import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { RotateCcw } from "lucide-react";
import { api } from "@/lib/api";
import { useWeeklyParams, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { Button, Drawer, Empty, ErrorBox, SkeletonBlock, useToast } from "@/components/ui";
import { fmtDate } from "@/lib/format";
import type { WeeklyParamsResponse } from "@/lib/types";

const LABELS: Record<string, string> = {
  coverage_target_days: "Couverture cible (j)", alert_red_days: "Seuil rouge (j)", alert_yellow_days: "Seuil orange (j)",
  overstock_days: "Surstock (j)", safety_stock_qty: "Stock sécurité", order_cycle_days: "Cycle cde (j)",
};

type Pos = { r: number; c: number };
type Item = { key2: string; field: string; value: number | "" };

/** Parse a pasted / typed value: ``""`` = back to the article value ; numbers accept the French comma. */
function parseCell(raw: string): number | "" | null {
  const t = raw.trim().replace(",", ".");
  if (t === "") return "";
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

/**
 * Weekly calendar of the stock-policy parameters of one article, edited like a sheet: click a cell
 * and type (Enter / Tab / arrows move), drag to select a range, Ctrl+C copies it, Ctrl+V pastes a
 * block from Excel (tab-separated), the square of the active cell copies its value by dragging,
 * Delete / an empty value restores the article value.  Every batch is saved in one transaction.
 * Weeks run from the reference week to the last week of the loaded PDP.
 */
export function WeeklyParamsDrawer({ articleId, planner, onClose }: { articleId: string | null; planner?: string | null; onClose: () => void }) {
  const toast = useToast();
  const { rights } = usePerimeter();
  const q = useWeeklyParams(articleId);
  const d = q.data;
  const canEdit = !!articleId && (rights.canManageParams || rights.canEditPlanner(planner));
  const save = useWrite((items: Item[]) => api.put("/api/params/overrides/batch", { scope: "article_week", key1: articleId, items }),
    () => toast.push("Valeurs hebdomadaires enregistrées", "success"));
  const reset = useWrite(() => api.del(`/api/params/overrides?scope=article_week&key1=${encodeURIComponent(articleId ?? "")}`),
    () => toast.push("Valeurs de l'article rétablies pour toutes les semaines"));

  const [anchor, setAnchor] = useState<Pos | null>(null);
  const [focus, setFocus] = useState<Pos | null>(null);
  const [editing, setEditing] = useState<{ p: Pos; value: string } | null>(null);
  const [fill, setFill] = useState<{ from: Pos; to: Pos } | null>(null);
  const dragging = useRef(false);
  const wrap = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => { setAnchor(null); setFocus(null); setEditing(null); setFill(null); }, [articleId]);
  useEffect(() => {
    const up = () => {
      dragging.current = false;
      if (fill) { commitFill(fill); setFill(null); }
    };
    window.addEventListener("mouseup", up);
    return () => window.removeEventListener("mouseup", up);
  });
  useEffect(() => { if (editing) inputRef.current?.focus(); }, [editing]);

  const fields = d?.fields ?? [];
  const rows = d?.weeks ?? [];
  const nR = rows.length, nC = fields.length;
  const range = useMemo(() => {
    if (!anchor || !focus) return null;
    return { r0: Math.min(anchor.r, focus.r), r1: Math.max(anchor.r, focus.r), c0: Math.min(anchor.c, focus.c), c1: Math.max(anchor.c, focus.c) };
  }, [anchor, focus]);
  const inRange = (r: number, c: number) => !!range && r >= range.r0 && r <= range.r1 && c >= range.c0 && c <= range.c1;
  const inFill = (r: number, c: number) => !!fill && c === fill.from.c && r > fill.from.r && r <= fill.to.r;
  const valueAt = (r: number, c: number) => rows[r].values[fields[c]];

  const commit = useCallback((items: Item[]) => {
    const changed = items.filter((it) => { const w = rows.find((x) => x.week === it.key2); return !!w && (it.value === "" ? w.overridden.includes(it.field) : w.values[it.field] !== it.value); });
    if (changed.length) save.mutate(changed);
  }, [rows, save]);
  const commitFill = (f: { from: Pos; to: Pos }) => {
    if (f.to.r <= f.from.r) return;
    const v = valueAt(f.from.r, f.from.c);
    commit(rows.slice(f.from.r + 1, f.to.r + 1).map((w) => ({ key2: w.week, field: fields[f.from.c], value: v })));
  };

  const select = (p: Pos, extend = false) => { setAnchor(extend && anchor ? anchor : p); setFocus(p); };
  const startEdit = (p: Pos, initial?: string) => { if (!canEdit) return; select(p); setEditing({ p, value: initial ?? String(valueAt(p.r, p.c)) }); };
  const endEdit = (saveIt: boolean, move?: Pos) => {
    if (editing && saveIt) {
      const v = parseCell(editing.value);
      if (v === null) toast.push("Valeur numérique attendue", "error");
      else commit([{ key2: rows[editing.p.r].week, field: fields[editing.p.c], value: v }]);
    }
    setEditing(null);
    if (move) { select(move); wrap.current?.focus(); }
  };
  const step = (p: Pos, dr: number, dc: number): Pos => ({ r: Math.min(nR - 1, Math.max(0, p.r + dr)), c: Math.min(nC - 1, Math.max(0, p.c + dc)) });

  const selectionText = () => {
    const rg = range ?? (focus ? { r0: focus.r, r1: focus.r, c0: focus.c, c1: focus.c } : null);
    if (!rg) return "";
    return rows.slice(rg.r0, rg.r1 + 1).map((w) => fields.slice(rg.c0, rg.c1 + 1).map((f) => String(w.values[f])).join("\t")).join("\n");
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
    const items: Item[] = [];
    let bad = 0;
    lines.forEach((line, dr) => line.split("\t").forEach((cell, dc) => {
      const r = origin.r + dr, c = origin.c + dc;
      if (r >= nR || c >= nC) return;
      const v = parseCell(cell);
      if (v === null) { bad++; return; }
      items.push({ key2: rows[r].week, field: fields[c], value: v });
    }));
    // a single value pasted on a range fills the range
    if (lines.length === 1 && !lines[0].includes("\t") && range && (range.r1 > range.r0 || range.c1 > range.c0)) {
      const v = parseCell(lines[0]);
      if (v !== null) { items.length = 0; for (let r = range.r0; r <= range.r1; r++) for (let c = range.c0; c <= range.c1; c++) items.push({ key2: rows[r].week, field: fields[c], value: v }); }
    }
    if (bad) toast.push(`${bad} cellule(s) non numérique(s) ignorée(s)`, "error");
    commit(items);
    if (items.length) { const last = items[items.length - 1]; setFocus({ r: rows.findIndex((w) => w.week === last.key2), c: fields.indexOf(last.field) }); }
  };
  const clearSelection = () => {
    if (!canEdit) return;
    const rg = range ?? (focus ? { r0: focus.r, r1: focus.r, c0: focus.c, c1: focus.c } : null);
    if (!rg) return;
    const items: Item[] = [];
    for (let r = rg.r0; r <= rg.r1; r++) for (let c = rg.c0; c <= rg.c1; c++) if (rows[r].overridden.includes(fields[c])) items.push({ key2: rows[r].week, field: fields[c], value: "" });
    if (items.length) save.mutate(items); else toast.push("Ces cellules portent déjà la valeur de l'article");
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

  const overridden = rows.reduce((n, w) => n + w.overridden.length, 0);
  return (
    <Drawer open={!!articleId} onClose={onClose} wide title={<>Paramètres par semaine · {articleId}</>}
      footer={<>
        <span className="small subtle grow">Cellules préremplies avec les valeurs de l'article (Référentiel) ; une valeur modifiée ne vaut que pour sa semaine (bordure bleue). Saisir, <kbd>Entrée</kbd>, flèches, glisser pour sélectionner, <kbd>Ctrl</kbd>+<kbd>C</kbd> / <kbd>Ctrl</kbd>+<kbd>V</kbd> (bloc Excel), tirer le carré pour recopier vers le bas, <kbd>Suppr</kbd> = valeur de l'article.</span>
        {canEdit && <Button disabled={!overridden || reset.isPending} title="Supprimer toutes les valeurs par semaine de cet article : retour aux valeurs uniques du Référentiel › Articles" onClick={() => { if (window.confirm(`Rétablir les valeurs de l'article pour les ${overridden} cellule(s) personnalisée(s) ?`)) reset.mutate(undefined); }}><RotateCcw />Tout rétablir{overridden ? ` (${overridden})` : ""}</Button>}
        <Button onClick={onClose}>Fermer</Button>
      </>}>
      {!canEdit && articleId && d && <div className="note" style={{ marginBottom: 8 }}>Lecture seule : cet article est hors de votre carnet.</div>}
      {q.isError ? <ErrorBox error={q.error} /> : q.isLoading || !d ? <SkeletonBlock rows={10} /> : rows.length === 0 ? <Empty title="Aucune semaine" /> : (
        <div className={`dgrid-wrap wgrid ${fill ? "filling" : ""}`} ref={wrap} tabIndex={0} onKeyDown={onKeyDown}
          onPaste={(e) => { if (editing) return; e.preventDefault(); paste(e.clipboardData.getData("text/plain")); }}
          onCopy={(e) => { if (editing) return; const t = selectionText(); if (t) { e.clipboardData.setData("text/plain", t); e.preventDefault(); } }}>
          <table className="dgrid" aria-label="Paramètres par semaine">
            <thead>
              <tr>
                <th className="rowno" onClick={() => { setAnchor({ r: 0, c: 0 }); setFocus({ r: nR - 1, c: nC - 1 }); }} title="Tout sélectionner">Semaine</th>
                {fields.map((f, c) => <th key={f} className="num" onClick={() => { setAnchor({ r: 0, c }); setFocus({ r: nR - 1, c }); }} title="Sélectionner la colonne">{LABELS[f] ?? f}<span className="sub">article : {d.defaults[f]}</span></th>)}
              </tr>
            </thead>
            <tbody>
              {rows.map((w, r) => (
                <tr key={w.week}>
                  <td className="rowno" style={{ textAlign: "left", whiteSpace: "nowrap" }} onClick={() => { setAnchor({ r, c: 0 }); setFocus({ r, c: nC - 1 }); }} title="Sélectionner la ligne"><b>{w.week.replace("-W", " S")}</b> <span className="subtle">{fmtDate(w.week_start)}</span></td>
                  {fields.map((f, c) => {
                    const over = w.overridden.includes(f);
                    const isEd = editing && editing.p.r === r && editing.p.c === c;
                    const isFocus = !isEd && focus?.r === r && focus?.c === c;
                    const cls = ["num", over ? "typed over" : "", inRange(r, c) ? "sel" : "", isFocus ? "active" : "", inFill(r, c) ? "fill-range" : "", canEdit ? "" : "ro"].filter(Boolean).join(" ");
                    return (
                      <td key={f} className={cls} title={over ? "Valeur propre à cette semaine (Suppr = valeur de l'article)" : "Valeur de l'article – saisir crée une valeur pour cette semaine"}
                        onMouseDown={(e) => { if (isEd) return; e.preventDefault(); if (editing) endEdit(true); dragging.current = true; select({ r, c }, e.shiftKey); wrap.current?.focus(); }}
                        onMouseEnter={() => { if (fill) setFill({ ...fill, to: { r: Math.max(fill.from.r, r), c: fill.from.c } }); else if (dragging.current) setFocus({ r, c }); }}
                        onDoubleClick={() => startEdit({ r, c })}>
                        {isEd ? <input ref={inputRef} className="cell-input" value={editing!.value} aria-label={`${LABELS[f] ?? f} ${w.week}`}
                          onChange={(e) => setEditing({ p: { r, c }, value: e.target.value })}
                          onBlur={() => endEdit(true)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") { e.preventDefault(); endEdit(true, step({ r, c }, 1, 0)); }
                            else if (e.key === "Tab") { e.preventDefault(); endEdit(true, step({ r, c }, 0, e.shiftKey ? -1 : 1)); }
                            else if (e.key === "Escape") { e.preventDefault(); endEdit(false, { r, c }); }
                          }} />
                          : <>{w.values[f]}{isFocus && canEdit && <span className="fill-handle" title="Tirer vers le bas pour recopier" onMouseDown={(e) => { e.stopPropagation(); e.preventDefault(); setFill({ from: { r, c }, to: { r, c } }); }} />}</>}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <WeekCount d={d} />
    </Drawer>
  );
}

function WeekCount({ d }: { d: WeeklyParamsResponse | undefined }) {
  if (!d?.weeks.length) return null;
  const last = d.weeks[d.weeks.length - 1];
  return <p className="small subtle" style={{ marginTop: 8 }}>{d.weeks.length} semaines, de {d.weeks[0].week.replace("-W", " S")} à {last.week.replace("-W", " S")} (dernière semaine du PDP chargé ou fin d'horizon).</p>;
}
