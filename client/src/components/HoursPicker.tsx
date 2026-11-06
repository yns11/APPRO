import { HOUR_RANGES, MAX_HOUR_RANGES } from "@/lib/transports";

/** The habitual delivery ranges of a supplier : six pills (6h–10h … ≥ 16h), two at most ; none = any time. */
export function HoursPicker({ value, onChange, disabled, label }: { value: string[]; onChange: (v: string[]) => void; disabled?: boolean; label?: string }) {
  const toggle = (v: string) => {
    if (value.includes(v)) onChange(value.filter((x) => x !== v));
    else if (value.length < MAX_HOUR_RANGES) onChange([...value, v]);
  };
  return (
    <span className="dock-picker sm hours-picker" role="group" aria-label={label ?? "Plages habituelles"}>
      {HOUR_RANGES.map((r) => {
        const on = value.includes(r.value);
        const full = !on && value.length >= MAX_HOUR_RANGES;
        return <label key={r.value} className={`${on ? "on" : ""} ${disabled || full ? "off" : ""}`} title={full ? `${MAX_HOUR_RANGES} plages au plus` : undefined}><input type="checkbox" checked={on} disabled={disabled || full} onChange={() => toggle(r.value)} /> {r.label}</label>;
      })}
    </span>
  );
}
