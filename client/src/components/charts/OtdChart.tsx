import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { OtdPeriod } from "@/lib/types";

const TICK = { fontSize: 11, fill: "var(--fg-subtle)" };
const SERIES: { key: keyof OtdPeriod; name: string; color: string; width: number; dash?: string }[] = [
  { key: "otd", name: "OTD", color: "var(--brand)", width: 2.5 },
  { key: "qty", name: "Quantité", color: "var(--s-firm)", width: 1.5, dash: "4 3" },
  { key: "punctuality", name: "Ponctualité", color: "var(--s-stock-sim)", width: 1.5, dash: "4 3" },
  { key: "desadv", name: "DESADV", color: "var(--s-receipt)", width: 1.5, dash: "4 3" },
];

/** Label of a period bucket ("2026-W40" → "S40", "2026-Q4" → "T4 2026", "2026-10" → "oct. 2026"). */
export function periodTick(p: string): string {
  if (p.includes("-W")) return p.replace(/^\d{4}-W/, "S");
  if (p.includes("-Q")) return `T${p.slice(-1)} ${p.slice(0, 4)}`;
  if (/^\d{4}-\d{2}$/.test(p)) return new Date(`${p}-01T12:00:00`).toLocaleDateString("fr-FR", { month: "short", year: "2-digit" });
  return p;
}

/**
 * Evolution of the OTD and its three indicators over the period buckets ; ``compare`` adds one OTD line per supplier
 * (the portfolio stays the thick line).  Buckets without any evaluable order are gaps.
 */
export function OtdChart({ series, compare = [], extra, height = 260, target }: { series: OtdPeriod[]; compare?: { name: string; series: OtdPeriod[] }[]; extra?: { name: string; series: { period: string; otd: number | null }[] }; height?: number; target?: number }) {
  const periods = series.map((p) => p.period);
  const rows = periods.map((period, i) => {
    const row: Record<string, string | number | null> = { period, label: periodTick(period), n: series[i].n, otd: series[i].otd, qty: series[i].qty, punctuality: series[i].punctuality, desadv: series[i].desadv };
    compare.forEach((c) => { row[`c:${c.name}`] = c.series.find((x) => x.period === period)?.otd ?? null; });
    if (extra) row.extra = extra.series.find((x) => x.period === period)?.otd ?? null;
    return row;
  });
  const palette = ["var(--s-proposal)", "var(--s-forecast)", "var(--s-stock-forecast)", "var(--s-demand)", "var(--info)", "var(--warning)", "var(--critical)"];
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="var(--border)" vertical={false} />
        <XAxis dataKey="label" tick={TICK} minTickGap={16} />
        <YAxis domain={[0, 100]} tick={TICK} width={36} unit=" %" />
        <Tooltip contentStyle={{ fontSize: 12 }} formatter={(v: unknown, name: string) => [v === null || v === undefined || v === "" ? "–" : `${v} %`, name]} labelFormatter={(l, payload) => { const n = (payload?.[0]?.payload as { n?: number } | undefined)?.n; return `${l}${n !== undefined ? ` · ${n} commande(s)` : ""}`; }} />
        <Legend wrapperStyle={{ fontSize: 11 }} iconSize={10} />
        {target !== undefined && <ReferenceLine y={target} stroke="var(--fg-muted)" strokeDasharray="2 4" label={{ value: `objectif ${target} %`, position: "insideTopRight", fontSize: 10, fill: "var(--fg-muted)" }} />}
        {SERIES.map((s) => <Line key={s.key} type="monotone" dataKey={s.key} name={compare.length ? `${s.name} (portefeuille)` : s.name} stroke={s.color} strokeWidth={s.width} strokeDasharray={s.dash} dot={{ r: 3 }} activeDot={{ r: 5 }} connectNulls={false} isAnimationActive={false} />)}
        {extra && <Line key="extra" type="monotone" dataKey="extra" name={extra.name} stroke="var(--fg-muted)" strokeWidth={2} strokeDasharray="6 4" dot={{ r: 2 }} connectNulls={false} isAnimationActive={false} />}
        {compare.map((c, i) => <Line key={c.name} type="monotone" dataKey={`c:${c.name}`} name={`OTD ${c.name}`} stroke={palette[i % palette.length]} strokeWidth={2} dot={{ r: 3 }} connectNulls={false} isAnimationActive={false} />)}
      </LineChart>
    </ResponsiveContainer>
  );
}
