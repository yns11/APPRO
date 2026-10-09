import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";

export interface WeekChip { key: string; label: string; start: string; current?: boolean }
export type WeekRange = { from: string; to: string } | null;

/**
 * Week-grained range picker for the supply table : one chip per ISO week of the loaded window ; click
 * a week, or drag from a week to another, to show only that period (columns displayed, never the
 * engine dates) ; Shift+click extends ; the cross shows the whole window again.
 */
export function WeekRangeBar({ weeks, range, onChange }: { weeks: WeekChip[]; range: WeekRange; onChange: (r: WeekRange) => void }) {
  const [anchor, setAnchor] = useState<string | null>(null);
  const [drag, setDrag] = useState<WeekRange>(null);
  const dragging = useRef(false);
  const idx = (k: string) => weeks.findIndex((w) => w.key === k);
  const ordered = (a: string, b: string): WeekRange => (idx(a) <= idx(b) ? { from: a, to: b } : { from: b, to: a });
  useEffect(() => {
    const up = () => { if (dragging.current) { dragging.current = false; if (drag) onChange(drag); setDrag(null); } };
    window.addEventListener("mouseup", up);
    return () => window.removeEventListener("mouseup", up);
  });
  const shown = drag ?? range;
  const inRange = (k: string) => !!shown && idx(k) >= idx(shown.from) && idx(k) <= idx(shown.to);
  const count = shown ? idx(shown.to) - idx(shown.from) + 1 : weeks.length;
  const label = (k: string) => weeks.find((w) => w.key === k)?.label ?? k;
  return (
    <div className="weekbar" role="group" aria-label="Période affichée (semaines)">
      <div className="weekbar-chips">
        {weeks.map((w) => {
          const sel = inRange(w.key);
          const first = sel && shown?.from === w.key, last = sel && shown?.to === w.key;
          return (
            <button key={w.key} type="button" className={["chip-week", sel ? "sel" : "", first ? "first" : "", last ? "last" : "", w.current ? "current" : ""].filter(Boolean).join(" ")}
              title={`${w.label} · du ${w.start}${w.current ? " · semaine en cours" : ""} — cliquer ou glisser pour choisir la période`}
              onMouseDown={(e) => { e.preventDefault(); if (e.shiftKey && anchor) { onChange(ordered(anchor, w.key)); return; } dragging.current = true; setAnchor(w.key); setDrag({ from: w.key, to: w.key }); }}
              onMouseEnter={() => { if (dragging.current && anchor) setDrag(ordered(anchor, w.key)); }}>
              {w.label}
            </button>
          );
        })}
      </div>
      <span className="weekbar-info subtle small">
        {shown ? <>{label(shown.from)}{shown.from !== shown.to ? <> → {label(shown.to)}</> : null} · {count} semaine{count > 1 ? "s" : ""}</> : <>toute la fenêtre · {weeks.length} semaines</>}
        {range && <button type="button" className="btn xs ghost" title="Afficher toute la fenêtre" onClick={() => onChange(null)}><X />Tout</button>}
      </span>
    </div>
  );
}
