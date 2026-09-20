import { Bar, CartesianGrid, ComposedChart, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { WeeklyOutlook } from "@/lib/types";

/** Portfolio outlook: articles in stockout / below target per week + simulated orders to receive. */
export function OutlookChart({ data, height = 220 }: { data: WeeklyOutlook[]; height?: number }) {
  const rows = data.map((w) => ({ ...w, label: w.week.replace("-W", " S") }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={rows} margin={{ top: 8, right: 8, left: -12, bottom: 0 }}>
        <CartesianGrid stroke="var(--border)" vertical={false} />
        <XAxis dataKey="label" tick={{ fontSize: 11, fill: "var(--fg-subtle)" }} />
        <YAxis allowDecimals={false} tick={{ fontSize: 11, fill: "var(--fg-subtle)" }} />
        <Tooltip contentStyle={{ fontSize: 11 }} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        <Bar dataKey="stockout_articles" name="Articles en rupture" stackId="a" fill="var(--critical)" />
        <Bar dataKey="below_target_articles" name="Articles sous cible" stackId="a" fill="var(--warning)" />
        <Bar dataKey="proposals" name="Commandes simulées à livrer" fill="var(--s-proposal)" fillOpacity={0.5} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
