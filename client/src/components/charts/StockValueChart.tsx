import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { WeeklyStockValue } from "@/lib/types";
import { fmtEur } from "@/lib/format";

/**
 * Projected value of the portfolio stock (Scenario Plan) at the end of each ISO week, against the
 * value of the target stock : one axis (euros), two thin lines, the target dashed and grey.
 */
export function StockValueChart({ data, height = 220 }: { data: WeeklyStockValue[]; height?: number }) {
  const rows = data.map((w) => ({ ...w, label: w.week.replace("-W", " S") }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={rows} margin={{ top: 8, right: 12, left: 8, bottom: 0 }}>
        <CartesianGrid stroke="var(--border)" vertical={false} />
        <XAxis dataKey="label" tick={{ fontSize: 11, fill: "var(--fg-subtle)" }} minTickGap={18} />
        <YAxis tick={{ fontSize: 11, fill: "var(--fg-subtle)" }} tickFormatter={(v: number) => fmtEur(v, true)} width={64} />
        <Tooltip contentStyle={{ fontSize: 11 }} formatter={(v: number) => fmtEur(v)} labelFormatter={(l: string, payload) => { const p = payload?.[0]?.payload as WeeklyStockValue | undefined; return p ? `${l} · fin de semaine` : l; }} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        <Line type="monotone" dataKey="value_plan" name="Valeur du stock · Scenario Plan" stroke="var(--brand)" strokeWidth={2} dot={false} activeDot={{ r: 4 }} isAnimationActive={false} />
        <Line type="monotone" dataKey="value_target" name="Valeur du stock cible" stroke="var(--fg-subtle)" strokeWidth={1.5} strokeDasharray="4 3" dot={false} activeDot={{ r: 3 }} isAnimationActive={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}
