import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { OtdPeriod } from "@/lib/types";

const TICK = { fontSize: 11, fill: "var(--fg-subtle)" };
const SERIES: { key: keyof OtdPeriod; name: string; color: string; width: number; dash?: string }[] = [
  { key: "otd", name: "OTD", color: "var(--brand)", width: 2.5 },
  { key: "qty", name: "Quantité", color: "var(--s-firm)", width: 1.2, dash: "4 3" },
  { key: "punctuality", name: "Ponctualité", color: "var(--s-stock-sim)", width: 1.2, dash: "4 3" },
  { key: "desadv", name: "DESADV", color: "var(--s-receipt)", width: 1.2, dash: "4 3" },
];

/** Label of a period bucket ("2026-W40" → "S40", "2026-Q4" → "T4 2026", "2026-10" → "oct. 26"). */
export function periodTick(p: string): string {
  if (p.includes("-W")) return p.replace(/^\d{4}-W/, "S");
  if (p.includes("-Q")) return `T${p.slice(-1)} ${p.slice(0, 4)}`;
  if (/^\d{4}-\d{2}$/.test(p)) return new Date(`${p}-01T12:00:00`).toLocaleDateString("fr-FR", { month: "short", year: "2-digit" });
  return p;
}

/** The OTD (bold) and its three indicators (thin, dashed) over the period buckets ; buckets without a line are gaps. */
export function OtdChart({ series, height = 240, target }: { series: OtdPeriod[]; height?: number; target?: number }) {
  const rows = series.map((p) => ({ label: periodTick(p.period), n: p.n, otd: p.otd, qty: p.qty, punctuality: p.punctuality, desadv: p.desadv }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="var(--border)" vertical={false} />
        <XAxis dataKey="label" tick={TICK} minTickGap={16} />
        <YAxis domain={[0, 100]} tick={TICK} width={36} unit=" %" />
        <Tooltip contentStyle={{ fontSize: 12 }} formatter={(v: unknown, name: string) => [v === null || v === undefined || v === "" ? "–" : `${v} %`, name]} labelFormatter={(l, payload) => { const n = (payload?.[0]?.payload as { n?: number } | undefined)?.n; return `${l}${n !== undefined ? ` · ${n} jour(s)` : ""}`; }} />
        <Legend wrapperStyle={{ fontSize: 11 }} iconSize={10} />
        {target !== undefined && <ReferenceLine y={target} stroke="var(--fg-muted)" strokeDasharray="2 4" label={{ value: `objectif ${target} %`, position: "insideTopRight", fontSize: 10, fill: "var(--fg-muted)" }} />}
        {SERIES.map((s) => <Line key={s.key} type="monotone" dataKey={s.key} name={s.name} stroke={s.color} strokeWidth={s.width} strokeDasharray={s.dash} dot={{ r: s.key === "otd" ? 3 : 0 }} activeDot={{ r: 5 }} connectNulls={false} isAnimationActive={false} />)}
      </LineChart>
    </ResponsiveContainer>
  );
}
