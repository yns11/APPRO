import { Area, Bar, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ProjectionResponse } from "@/lib/types";
import { fmtQty, periodLabel } from "@/lib/format";

/**
 * Article projection chart – IBCS-inspired notation:
 *  - firm stock: solid dark line (actual / committed)
 *  - simulated stock: dashed line (plan / what-if)
 *  - target stock: thin grey dashed reference
 *  - supply bars stacked by commitment (firm / planned / proposals / receipts), demand bars in grey
 */
export function StockChart({ data, height = 300 }: { data: ProjectionResponse; height?: number }) {
  const s = (k: string) => data.series.find((x) => x.key === k)?.values ?? [];
  const rows = data.periods.map((p, i) => ({
    period: p, label: periodLabel(p, data.granularity),
    demand: -(s("demand")[i] ?? 0), firm: s("supply_firm")[i] ?? 0, planned: s("supply_planned")[i] ?? 0,
    proposed: s("supply_proposed")[i] ?? 0, receipts: s("receipts")[i] ?? 0,
    stock_firm: s("stock_firm")[i] ?? 0, stock_sim: s("stock_sim")[i] ?? 0, target: s("target_stock")[i] ?? 0,
  }));
  const asOfLabel = data.granularity === "week" ? data.periods.find((_p, i) => data.period_start[i] <= data.as_of && (data.period_start[i + 1] ?? "9999") > data.as_of) : data.as_of;
  const unit = data.article.unit;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }} stackOffset="sign">
        <defs>
          <pattern id="hatch" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
            <rect width="6" height="6" fill="var(--bg-elev)" /><line x1="0" y1="0" x2="0" y2="6" stroke="var(--s-proposal)" strokeWidth="3" />
          </pattern>
        </defs>
        <CartesianGrid stroke="var(--border)" vertical={false} />
        <XAxis dataKey="period" tickFormatter={(v: string) => periodLabel(v, data.granularity)} tick={{ fontSize: 11, fill: "var(--fg-subtle)" }} minTickGap={24} />
        <YAxis yAxisId="qty" tick={{ fontSize: 11, fill: "var(--fg-subtle)" }} tickFormatter={(v: number) => fmtQty(v, unit)} width={64} />
        <Tooltip content={<ChartTooltip unit={unit} granularity={data.granularity} />} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        {asOfLabel && <ReferenceLine yAxisId="qty" x={asOfLabel} stroke="var(--brand)" strokeDasharray="4 3" label={{ value: "Aujourd'hui", fontSize: 10, fill: "var(--brand)", position: "insideTopLeft" }} />}
        <ReferenceLine yAxisId="qty" y={0} stroke="var(--border-strong)" />
        <Bar yAxisId="qty" dataKey="demand" name="Besoin" stackId="flow" fill="var(--s-demand)" fillOpacity={0.55} />
        <Bar yAxisId="qty" dataKey="receipts" name="Réceptions" stackId="flow" fill="var(--s-receipt)" />
        <Bar yAxisId="qty" dataKey="firm" name="Cdes fermes" stackId="flow" fill="var(--s-firm)" />
        <Bar yAxisId="qty" dataKey="planned" name="Cdes planifiées / prév." stackId="flow" fill="var(--s-planned)" />
        <Bar yAxisId="qty" dataKey="proposed" name="Propositions" stackId="flow" fill="url(#hatch)" stroke="var(--s-proposal)" />
        <Area yAxisId="qty" type="monotone" dataKey="target" name="Stock cible" stroke="var(--s-target)" strokeDasharray="2 3" fill="var(--s-target)" fillOpacity={0.06} dot={false} />
        <Line yAxisId="qty" type="monotone" dataKey="stock_firm" name="Stock ferme" stroke="var(--s-stock-firm)" strokeWidth={2.2} dot={false} />
        <Line yAxisId="qty" type="monotone" dataKey="stock_sim" name="Stock simulé" stroke="var(--s-stock-sim)" strokeWidth={2} strokeDasharray="6 3" dot={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

function ChartTooltip({ active, payload, label, unit, granularity }: { active?: boolean; payload?: { name: string; value: number; color?: string }[]; label?: string; unit: string; granularity: "day" | "week" }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="tooltip-box">
      <div className="t">{periodLabel(String(label), granularity)}</div>
      {payload.filter((p) => p.value !== 0 || p.name.startsWith("Stock")).map((p) => (
        <div key={p.name} className="r"><span style={{ color: p.color }}>{p.name}</span><b>{fmtQty(Math.abs(p.value), unit)}</b></div>
      ))}
    </div>
  );
}

export function CoverageChart({ data, height = 120 }: { data: ProjectionResponse; height?: number }) {
  const cov = data.series.find((x) => x.key === "coverage_sim")?.values ?? [];
  const covFirm = data.series.find((x) => x.key === "coverage_firm")?.values ?? [];
  const rows = data.periods.map((p, i) => ({ period: p, sim: cov[i] ?? 0, firm: covFirm[i] ?? 0 }));
  const a = data.article;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={rows} margin={{ top: 4, right: 12, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="var(--border)" vertical={false} />
        <XAxis dataKey="period" hide />
        <YAxis tick={{ fontSize: 10, fill: "var(--fg-subtle)" }} width={64} unit=" j" />
        <Tooltip formatter={(v: number) => [`${v} j`, ""]} labelFormatter={(l) => periodLabel(String(l), data.granularity)} contentStyle={{ fontSize: 11 }} />
        <ReferenceLine y={a.alert_red_days} stroke="var(--critical)" strokeDasharray="3 3" label={{ value: "rouge", fontSize: 9, fill: "var(--critical)", position: "insideTopLeft" }} />
        <ReferenceLine y={a.alert_yellow_days} stroke="var(--warning)" strokeDasharray="3 3" label={{ value: "orange", fontSize: 9, fill: "var(--warning)", position: "insideTopLeft" }} />
        <ReferenceLine y={a.overstock_days} stroke="var(--ok)" strokeDasharray="3 3" label={{ value: "surstock", fontSize: 9, fill: "var(--ok)", position: "insideTopLeft" }} />
        <Line type="stepAfter" dataKey="firm" name="Couverture ferme" stroke="var(--s-stock-firm)" strokeWidth={1.6} dot={false} />
        <Line type="stepAfter" dataKey="sim" name="Couverture simulée" stroke="var(--s-stock-sim)" strokeWidth={1.6} strokeDasharray="5 3" dot={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
