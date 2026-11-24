import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { History, Redo2, Undo2 } from "lucide-react";
import { api, onRecordedAction, type RecordedAction } from "@/lib/api";
import { useActions } from "@/lib/queries";
import { flashCells } from "@/lib/actionsBus";
import { usePerimeter } from "@/state/PerimeterContext";
import { Button, useToast } from "@/components/ui";
import { fmtDateTime } from "@/lib/format";
import type { ActionOut, ActionResultOut } from "@/lib/types";

const UNDO_TOAST = "undo";

/** Whether a keyboard shortcut typed on ``target`` belongs to a field (the browser's own undo applies there). */
function inField(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || !el.tagName) return false;
  const tag = el.tagName.toLowerCase();
  return tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable;
}

/** Topbar ↶ ↷ with the history of the user's own actions : Ctrl+Z / Ctrl+Y (Ctrl+Shift+Z) outside the fields,
 *  a toast « Annuler » after each write, « Annuler jusqu'ici » from the history. */
export function ActionsBar() {
  const { rights } = usePerimeter();
  const qc = useQueryClient();
  const toast = useToast();
  const mine = useActions({ mine: true, limit: 40 });
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);
  const busy = useRef(false);

  const list = mine.data ?? [];
  const undoables = list.filter((a) => a.state === "applied" && a.undoable && a.can_act);
  const redoables = list.filter((a) => a.state === "undone" && a.can_act);
  const next = undoables[0];
  const nextRedo = redoables[redoables.length - 1];   // the oldest undone one is redone first

  const run = useCallback(async (path: string, fallbackLabel?: string) => {
    if (busy.current) return;
    busy.current = true;
    try {
      const out = await api.post<ActionResultOut>(path);
      qc.invalidateQueries();
      flashCells(out.cells);
      const n = out.actions.length;
      const label = out.actions[n - 1]?.label ?? fallbackLabel ?? "";
      if (out.kind === "undo") {
        toast.push(n > 1 ? `${n} actions annulées (jusqu'à « ${label} »)` : `Annulé : ${label}`, "success",
          { key: UNDO_TOAST, action: { label: "Rétablir", onClick: () => { void run(`/api/actions/${out.actions[n - 1].id}/redo`); } } });
      } else {
        toast.push(n > 1 ? `${n} actions rétablies (jusqu'à « ${label} »)` : `Rétabli : ${label}`, "success",
          { key: UNDO_TOAST, action: { label: "Annuler", onClick: () => { void run(`/api/actions/${out.actions[n - 1].id}/undo`); } } });
      }
    } catch (e) {
      toast.push((e as Error).message, "error", { key: UNDO_TOAST });
      qc.invalidateQueries({ queryKey: ["actions"] });
    } finally {
      busy.current = false;
    }
  }, [qc, toast]);

  // the toast « Annuler » after every write that produced an action
  useEffect(() => onRecordedAction((a: RecordedAction) => {
    qc.invalidateQueries({ queryKey: ["actions"] });
    toast.push(a.label, "info", { key: UNDO_TOAST, action: { label: "Annuler", onClick: () => { void run(`/api/actions/${a.id}/undo`); } } });
  }), [qc, toast, run]);

  // Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z outside the fields
  useEffect(() => {
    if (!rights.canWrite) return;
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey || e.metaKey) || e.altKey || inField(e.target)) return;
      const k = e.key.toLowerCase();
      if (k === "z" && !e.shiftKey) { e.preventDefault(); void run("/api/actions/undo-last"); }
      else if (k === "y" || (k === "z" && e.shiftKey)) { e.preventDefault(); void run("/api/actions/redo-last"); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rights.canWrite, run]);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!wrap.current?.contains(e.target as Node)) setOpen(false); };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);

  if (!rights.canWrite) return null;
  const undoTitle = next ? `Annuler : ${next.label}  (Ctrl+Z)` : "Rien à annuler (Ctrl+Z)";
  const redoTitle = nextRedo ? `Rétablir : ${nextRedo.label}  (Ctrl+Y)` : "Rien à rétablir (Ctrl+Y)";
  const stepsTo = (a: ActionOut) => undoables.indexOf(a) + 1;

  return (
    <div className="actions-bar no-print" ref={wrap}>
      <Button variant="ghost" icon title={undoTitle} aria-label="Annuler" disabled={!next} onClick={() => void run("/api/actions/undo-last")}>
        <Undo2 />{undoables.length > 0 && <span className="count">{undoables.length > 99 ? "99+" : undoables.length}</span>}
      </Button>
      <Button variant="ghost" icon title={redoTitle} aria-label="Rétablir" disabled={!nextRedo} onClick={() => void run("/api/actions/redo-last")}><Redo2 /></Button>
      <Button variant="ghost" icon title="Historique de mes actions (7 jours)" aria-label="Historique" aria-expanded={open} onClick={() => setOpen((o) => !o)}><History /></Button>
      {open && (
        <div className="history-menu" role="menu">
          <div className="hm-title"><span>Mes actions</span><span>{list.length ? `${undoables.length} annulable(s)` : ""}</span></div>
          {!list.length && <div className="hm-empty">Aucune action depuis 7 jours.</div>}
          {list.map((a) => {
            const canUndo = a.state === "applied" && a.can_act && !a.conflict;
            const canRedo = a.state === "undone" && a.can_act && !a.conflict;
            const steps = canUndo ? stepsTo(a) : 0;
            const act = a.conflict ? a.conflict : a.state === "discarded" ? "obsolète" : !a.undoable ? "non annulable"
              : canUndo ? (steps > 1 ? `Annuler jusqu'ici (${steps})` : "Annuler") : canRedo ? "Rétablir" : "";
            const onClick = canUndo ? () => { setOpen(false); void run(`/api/actions/${a.id}/undo`, a.label); }
              : canRedo ? () => { setOpen(false); void run(`/api/actions/${a.id}/redo`, a.label); } : undefined;
            return (
              <button key={a.id} type="button" role="menuitem" className={`hm-item ${a.state}`} disabled={!onClick} onClick={onClick} title={a.label}>
                <span className="lbl">{a.label}</span>
                <span className="act">{act}</span>
                <span className="meta">{fmtDateTime(a.ts)}{a.items > 1 ? ` · ${a.items} lignes` : ""}{a.state === "undone" ? " · annulée" : ""}</span>
              </button>
            );
          })}
          <div className="hm-foot">Ctrl+Z annule, Ctrl+Y rétablit (hors champ de saisie). Les actions des autres utilisateurs : page Saisies &amp; journal.</div>
        </div>
      )}
    </div>
  );
}
