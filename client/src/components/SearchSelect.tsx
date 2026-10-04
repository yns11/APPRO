import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, X } from "lucide-react";

export interface SearchOption { id: string; label: string; hint?: string }

/**
 * Searchable single-choice list (combobox): typing filters the options on their identifier, label
 * and hint ; arrows + Enter choose, Escape closes, the cross clears.  ``value`` is the chosen id
 * ("" = none, shown as ``placeholder``).
 */
export function SearchSelect({ value, onChange, options, placeholder, width = 240, ariaLabel, loading }:
  { value: string; onChange: (id: string) => void; options: SearchOption[]; placeholder: string; width?: number | string; ariaLabel?: string; loading?: boolean }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);
  const wrap = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const chosen = options.find((o) => o.id === value);
  const filtered = useMemo(() => {
    const needle = q.trim().toLowerCase();
    const xs = needle ? options.filter((o) => `${o.id} ${o.label} ${o.hint ?? ""}`.toLowerCase().includes(needle)) : options;
    return xs.slice(0, 200);
  }, [options, q]);
  useEffect(() => { setIdx(0); }, [q, open]);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => { if (!wrap.current?.contains(e.target as Node)) { setOpen(false); setQ(""); } };
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);
  const pick = (id: string) => { onChange(id); setOpen(false); setQ(""); };
  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); if (!open) setOpen(true); else setIdx((i) => Math.min(filtered.length - 1, i + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); if (open && filtered[idx]) pick(filtered[idx].id); else setOpen(true); }
    else if (e.key === "Escape") { setOpen(false); setQ(""); }
    else if (e.key === "Backspace" && !q && value) { onChange(""); }
  };
  return (
    <div className={`combo ${open ? "open" : ""}`} ref={wrap} style={{ width }}>
      <input ref={input} className="input sm" role="combobox" aria-expanded={open} aria-label={ariaLabel ?? placeholder} aria-autocomplete="list"
        value={open ? q : chosen ? chosen.label : ""} placeholder={chosen && !open ? chosen.label : placeholder} title={chosen ? `${chosen.id} · ${chosen.label}` : undefined}
        onChange={(e) => { setQ(e.target.value); if (!open) setOpen(true); }} onFocus={() => setOpen(true)} onKeyDown={onKey} />
      {value ? <button type="button" className="combo-x" aria-label="Effacer" onMouseDown={(e) => e.preventDefault()} onClick={() => { onChange(""); setQ(""); input.current?.focus(); }}><X /></button>
        : <span className="combo-chev"><ChevronDown /></span>}
      {open && (
        <ul className="combo-list" role="listbox">
          {loading ? <li className="subtle">chargement…</li>
            : filtered.length === 0 ? <li className="subtle">aucun résultat</li>
              : filtered.map((o, i) => (
                <li key={o.id} role="option" aria-selected={o.id === value} className={`${i === idx ? "hover" : ""} ${o.id === value ? "chosen" : ""}`}
                  onMouseEnter={() => setIdx(i)} onMouseDown={(e) => e.preventDefault()} onClick={() => pick(o.id)}>
                  <span className="lbl">{o.label}</span>{o.hint && <span className="hint">{o.hint}</span>}
                </li>
              ))}
          {options.length > filtered.length && filtered.length >= 200 && <li className="subtle">… affiner la recherche</li>}
        </ul>
      )}
    </div>
  );
}
