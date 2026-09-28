import { Area, Bar, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import type { ProjectionResponse } from "@/lib/types";
import { fmtQty, periodLabel } from "@/lib/format";

/**
 * Article projection chart – IBCS-inspired notation:
 *  - Scenario ERP: solid dark line (ERP orders as is)
 *  - Scenario Plan: dotted line (delivery plan + CBN complement)
 *  - unserved demand (shortage) of the plan: red bars below the axis
 *  - target stock: thin grey dashed reference
 *  - supply bars: ERP flows (firm / forecast / receipts) in one stack, plan (hatched) + CBN in another,
 *    adjustments in grey (signed), consumption / requirement bars below the axis
 * Stocks are physical: never negative ; before the reference day the history is reconstructed.
 */
export function StockChart({ data, height = 300 }: { data: ProjectionResponse; height?: number }) {
  const s = (k: string) => data.series.find((x) => x.key === k)?.values ?? [];
  const rows = data.periods.map((p, i) => ({
    period: p, label: periodLabel(p, data.granularity),
    demand: -((s("consumed")[i] ?? 0) + (s("required")[i] ?? 0)),
    firm: (s("orders_firm")[i] ?? 0) + (s("orders_firm_hist")[i] ?? 0), forecast: s("orders_forecast")[i] ?? 0,
    plan: (s("plan")[i] ?? 0) + (s("plan_hist")[i] ?? 0), proposed: s("supply_proposed")[i] ?? 0,
    adjustments: s("adjustments")[i] ?? 0, receipts: s("receipts")[i] ?? 0, shortage: -(s("shortage_plan")[i] ?? 0),
    stock_erp: s("stock_erp")[i] ?? 0, stock_plan: s("stock_plan")[i] ?? 0, target: s("target_stock")[i] ?? 0,
  }));
  const asOfLabel = data.periods.find((_p, i) => data.period_start[i] <= data.as_of && data.as_of <= (data.period_end[i] ?? "9999"));
  const unit = data.article.unit;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }} stackOffset="sign">
        <defs>
          <pattern id="hatch" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
            <rect width="6" height="6" fill="var(--bg-elev)" /><line x1="0" y1="0" x2="0" y2="6" stroke="var(--s-stock-sim)" strokeWidth="3" />
          </pattern>
        </defs>
        <CartesianGrid stroke="var(--border)" vertical={false} />
        <XAxis dataKey="period" tickFormatter={(v: string) => periodLabel(v, data.granularity)} tick={{ fontSize: 11, fill: "var(--fg-subtle)" }} minTickGap={24} />
        <YAxis yAxisId="qty" tick={{ fontSize: 11, fill: "var(--fg-subtle)" }} tickFormatter={(v: number) => fmtQty(v, unit)} width={64} />
        <Tooltip content={<ChartTooltip unit={unit} granularity={data.granularity} />} />
        <Legend wrapperStyle={{ fontSize: 11 }} />
        {asOfLabel && <ReferenceLine yAxisId="qty" x={asOfLabel} stroke="var(--brand)" strokeDasharray="4 3" label={{ value: "Aujourd'hui", fontSize: 10, fill: "var(--brand)", position: "insideTopLeft" }} />}
        <ReferenceLine yAxisId="qty" y={0} stroke="var(--border-strong)" />
        <Bar yAxisId="qty" dataKey="demand" name="Consommé / requis" stackId="flow" fill="var(--s-demand)" fillOpacity={0.55} />
        <Bar yAxisId="qty" dataKey="receipts" name="Reçu" stackId="flow" fill="var(--s-receipt)" />
        <Bar yAxisId="qty" dataKey="firm" name="Ferme (ERP)" stackId="flow" fill="var(--s-firm)" />
        <Bar yAxisId="qty" dataKey="forecast" name="Prévisionnel (ERP)" stackId="flow" fill="var(--s-forecast)" fillOpacity={0.5} />
        <Bar yAxisId="qty" dataKey="plan" name="Plan" stackId="plan" fill="url(#hatch)" stroke="var(--s-stock-sim)" />
        <Bar yAxisId="qty" dataKey="proposed" name="Complément CBN" stackId="plan" fill="var(--s-proposal)" fillOpacity={0.45} />
        <Bar yAxisId="qty" dataKey="adjustments" name="Ajustement" stackId="flow" fill="var(--s-adjust)" fillOpacity={0.8} />
        <Bar yAxisId="qty" dataKey="shortage" name="Manque plan" stackId="short" fill="var(--s-shortage)" fillOpacity={0.7} />
        <Area yAxisId="qty" type="monotone" dataKey="target" name="Stock cible" stroke="var(--s-target)" strokeDasharray="2 3" fill="var(--s-target)" fillOpacity={0.06} dot={false} />
        <Line yAxisId="qty" type="monotone" dataKey="stock_erp" name="Scenario ERP" stroke="var(--s-stock-firm)" strokeWidth={2.2} dot={false} />
        <Line yAxisId="qty" type="monotone" dataKey="stock_plan" name="Scenario Plan" stroke="var(--s-stock-sim)" strokeWidth={2} strokeDasharray="2 3" dot={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

function ChartTooltip({ active, payload, label, unit, granularity }: { active?: boolean; payload?: { name: string; value: number; color?: string }[]; label?: string; unit: string; granularity: string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="tooltip-box">
      <div className="t">{periodLabel(String(label), granularity)}</div>
      {payload.filter((p) => p.value !== 0 || p.name.startsWith("Scenario")).map((p) => (
        <div key={p.name} className="r"><span style={{ color: p.color }}>{p.name}</span><b>{p.name === "Ajustement" ? (p.value < 0 ? "−" : "") + fmtQty(Math.abs(p.value), unit) : fmtQty(Math.abs(p.value), unit)}</b></div>
      ))}
    </div>
  );
}

export function CoverageChart({ data, height = 120 }: { data: ProjectionResponse; height?: number }) {
  const cov = data.series.find((x) => x.key === "coverage_plan")?.values ?? [];
  const covFirm = data.series.find((x) => x.key === "coverage_erp")?.values ?? [];
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
        <Line type="stepAfter" dataKey="firm" name="Couverture ERP" stroke="var(--s-stock-firm)" strokeWidth={1.6} dot={false} />
        <Line type="stepAfter" dataKey="sim" name="Couverture plan" stroke="var(--s-stock-sim)" strokeWidth={1.6} strokeDasharray="2 3" dot={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
