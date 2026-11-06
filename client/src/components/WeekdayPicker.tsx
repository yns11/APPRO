import { useEffect, useState } from "react";
import { WEEKDAY_LONG, WEEKDAY_SHORT } from "@/lib/format";
import type { RefValue } from "@/lib/types";

/** ``"1,3,5"`` → [1, 3, 5] (ISO weekdays, Monday = 1). */
export function splitWeekdays(v: RefValue | string | null | undefined): number[] {
  return String(v ?? "").split(/[;,\s]+/).map((x) => Number(x)).filter((x) => x >= 1 && x <= 7);
}

/**
 * Standard delivery weekdays as **five checkboxes on one line, without text** (Monday to Friday, the weekday in the
 * tooltip) : a supplier delivering on Tuesday and Thursday shows the second and the fourth boxes ticked.
 */
export function WeekdayPicker({ value, onChange, disabled, label }: { value: number[]; onChange?: (v: number[]) => void; disabled?: boolean; label?: string }) {
  // the box reacts at once ; the parent's value (saved, refetched) takes over when it changes
  const [local, setLocal] = useState(value);
  useEffect(() => { setLocal(value); }, [value.join(",")]);  // eslint-disable-line react-hooks/exhaustive-deps
  const toggle = (n: number) => { const next = local.includes(n) ? local.filter((x) => x !== n) : [...local, n].sort((a, b) => a - b); setLocal(next); onChange?.(next); };
  return (
    <span className="weekday-picker" role="group" aria-label={label ?? "Jours de livraison"}>
      {[1, 2, 3, 4, 5].map((n) => (
        <input key={n} type="checkbox" checked={local.includes(n)} disabled={disabled || !onChange} title={WEEKDAY_LONG[n - 1]} aria-label={WEEKDAY_SHORT[n - 1]} onChange={() => toggle(n)} />
      ))}
    </span>
  );
}
