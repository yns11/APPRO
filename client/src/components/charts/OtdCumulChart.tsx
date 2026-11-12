import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtDate, fmtQty } from "@/lib/format";
import type { OtdWeek } from "@/lib/types";

const TICK = { fontSize: 11, fill: "var(--fg-subtle)" };

/** Cumulative expected and received quantities over the days of the month, as two step curves : the horizontal gap between the steps is the delay. */
export function OtdCumulChart({ weeks, height = 200 }: { weeks: OtdWeek[]; height?: number }) {
  let ce = 0, cr = 0;
  const rows = weeks.flatMap((w) => w.days).map((d) => { ce += d.expected; cr += d.received; return { date: d.date, label: fmtDate(d.date, "dd/MM"), expected: Math.round(ce * 1000) / 1000, received: Math.round(cr * 1000) / 1000 }; });
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="var(--border)" vertical={false} />
        <XAxis dataKey="label" tick={TICK} minTickGap={24} />
        <YAxis tick={TICK} width={56} tickFormatter={(v: number) => fmtQty(v)} />
        <Tooltip contentStyle={{ fontSize: 12 }} formatter={(v: unknown, name: string) => [fmtQty(Number(v)), name]} labelFormatter={(l) => String(l)} />
        <Line type="stepAfter" dataKey="expected" name="Cumul attendu" stroke="var(--s-demand)" strokeWidth={2} dot={false} isAnimationActive={false} />
        <Line type="stepAfter" dataKey="received" name="Cumul reçu" stroke="var(--brand)" strokeWidth={2} dot={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
