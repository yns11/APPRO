import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { periodTick } from "./OtdChart";
import type { OtdPeriod } from "@/lib/types";

const TICK = { fontSize: 10, fill: "var(--fg-subtle)" };

/** One indicator over the period buckets (the small chart under each KPI tile of Performance › OTD). */
export function OtdMiniChart({ series, dataKey, name, color, target, height = 150 }: { series: OtdPeriod[]; dataKey: "otd" | "qty" | "punctuality" | "desadv"; name: string; color: string; target?: number; height?: number }) {
  const rows = series.map((p) => ({ label: periodTick(p.period), n: p.n, v: p[dataKey] }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 10, right: 12, left: -8, bottom: 0 }}>
        <CartesianGrid stroke="var(--border)" vertical={false} />
        <XAxis dataKey="label" tick={TICK} minTickGap={18} />
        <YAxis domain={[0, 100]} tick={TICK} width={40} unit=" %" />
        <Tooltip contentStyle={{ fontSize: 12 }} formatter={(v: unknown) => [v === null || v === undefined || v === "" ? "–" : `${v} %`, name]} labelFormatter={(l, payload) => { const n = (payload?.[0]?.payload as { n?: number } | undefined)?.n; return `${l}${n !== undefined ? ` · ${n} jour(s)` : ""}`; }} />
        {target !== undefined && <ReferenceLine y={target} stroke="var(--fg-muted)" strokeDasharray="2 4" label={{ value: `${target} %`, position: "insideTopRight", fontSize: 10, fill: "var(--fg-muted)" }} />}
        <Line type="monotone" dataKey="v" name={name} stroke={color} strokeWidth={2.2} dot={{ r: 2.5 }} activeDot={{ r: 4.5 }} connectNulls={false} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
