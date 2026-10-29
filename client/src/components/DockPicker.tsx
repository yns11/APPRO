import type { RefValue } from "@/lib/types";

/** Delivery docks of the plant (the same list as ``services/transports.py``). */
export const DOCKS = ["B15", "B6 EST", "B2 NORD", "B6 NORD"];

export function splitDocks(v: RefValue | string | null | undefined): string[] {
  return String(v ?? "").split(/[;,]+/).map((x) => x.trim()).filter(Boolean);
}

/** Docks as one-click checkboxes (several allowed : one CSV line per dock for the transport tool). */
export function DockPicker({ all, value, onChange, label, size, disabled }: { all: string[]; value: string[]; onChange: (v: string[]) => void; label?: string; size?: "sm"; disabled?: boolean }) {
  return (
    <span className={`dock-picker ${size ?? ""}`} role="group" aria-label={label ?? "Quais"}>
      {all.map((d) => <label key={d} className={`${value.includes(d) ? "on" : ""} ${disabled ? "off" : ""}`}><input type="checkbox" checked={value.includes(d)} disabled={disabled} onChange={() => onChange(value.includes(d) ? value.filter((x) => x !== d) : [...value, d])} /> {d}</label>)}
    </span>
  );
}
