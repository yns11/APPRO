import { useMemo, useState } from "react";
import { Area, Bar, CartesianGrid, ComposedChart, LabelList, Legend, Line, ReferenceArea, ReferenceDot, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Table2 } from "lucide-react";
import type { ProjectionResponse } from "@/lib/types";
import { usePerimeter } from "@/state/PerimeterContext";
import { DataTable, type Column } from "@/components/DataTable";
import { fmtDate, fmtQty, isSaturday, isSunday, isWeekKey, isoWeekKey, periodLabel } from "@/lib/format";

/**
 * Article charts – four panels on one time axis, the **same window as the supply table** (its
 * granularity, its week range, its hidden week-ends) :
 *  1. stock projected, target stock, lead-time window, order to place, stockout, unserved cumul ;
 *  2. flows : firm orders (past = ordered, future = remaining), typed plan, CBN proposals above the axis,
 *     the demand below, the receipts as a line over the past ;
 *  3. exploded PDP (bars) against the reported consumption (line) with the bias of each past period ;
 *  4. coverage in working days with the red / orange / overstock zones of the article.
 * One tooltip (on the first panel) gives the values of the four panels ; a « Valeurs » button opens the table.
 */
interface Row {
  period: string; label: string; past: boolean; current: boolean;
  stock: number; target: number; shortage: number;
  firm: number; plan: number; cbn: number; demand: number; receipts: number | null;
  pdp: number; real: number | null; bias: number | null;
  cov: number;
}

const SYNC = "article-charts";
const LEGEND = { layout: "vertical" as const, align: "right" as const, verticalAlign: "top" as const, wrapperStyle: { width: 170, paddingLeft: 10, fontSize: 11, lineHeight: "18px" }, iconSize: 10 };
const TICK = { fontSize: 11, fill: "var(--fg-subtle)" };
const Y_WIDTH = 66;

export function ArticleCharts({ data }: { data: ProjectionResponse }) {
  const { perimeter } = usePerimeter();
  const [showTable, setShowTable] = useState(false);
  const unit = data.article.unit;
  const a = data.article;
  const rows = useMemo<Row[]>(() => {
    const s = (k: string) => data.series.find((x) => x.key === k)?.values ?? [];
    const weekOf = (i: number) => (isWeekKey(data.periods[i]) ? data.periods[i] : isoWeekKey(data.periods[i]));
    const keys = Array.from(new Set(data.periods.map((_p, i) => weekOf(i))));
    const r = perimeter.weekRange;
    let lo = 0, hi = keys.length - 1;
    if (r) {
      const ia = keys.indexOf(r.from), ib = keys.indexOf(r.to);
      if (ia >= 0 || ib >= 0) { lo = ia < 0 ? 0 : ia; hi = ib < 0 ? keys.length - 1 : ib; }
    }
    const out: Row[] = [];
    data.periods.forEach((p, i) => {
      const wk = keys.indexOf(weekOf(i));
      if (wk < lo || wk > hi) return;
      if (!isWeekKey(p) && ((!perimeter.showSaturday && isSaturday(p)) || (!perimeter.showSunday && isSunday(p)))) return;
      const start = data.period_start[i], end = data.period_end[i] ?? start;
      const current = start <= data.as_of && data.as_of <= end;
      const past = end < data.as_of || current;
      const pdp = s("demand_plan")[i] ?? 0, real = s("consumed")[i] ?? 0;
      out.push({
        period: p, label: periodLabel(p), past, current,
        stock: s("stock_plan")[i] ?? 0, target: s("target_stock")[i] ?? 0, shortage: -(s("shortage_plan_cum")[i] ?? 0),
        firm: (s("orders_firm")[i] ?? 0) + (s("orders_firm_hist")[i] ?? 0), plan: Math.max((s("plan")[i] ?? 0) - (s("orders_firm")[i] ?? 0), 0),
        cbn: s("supply_proposed")[i] ?? 0, demand: -(s("demand")[i] ?? 0), receipts: past ? (s("receipts")[i] ?? 0) : null,
        pdp, real: past && !current ? real : null, bias: past && !current && pdp > 0 && real > 0 ? Math.round(100 * (real - pdp) / pdp) : null,
        cov: s("coverage_plan")[i] ?? 0,
      });
    });
    return out;
  }, [data, perimeter.weekRange, perimeter.showSaturday, perimeter.showSunday]);

  const today = rows.find((r) => r.current)?.period;
  const todayIdx = rows.findIndex((r) => r.current);
  const periodOf = (iso: string | null | undefined) => {
    if (!iso) return undefined;
    const i = data.periods.findIndex((_p, j) => data.period_start[j] <= iso && iso <= (data.period_end[j] ?? data.period_start[j]));
    return i >= 0 && rows.some((r) => r.period === data.periods[i]) ? data.periods[i] : undefined;
  };
  const leadEnd = useMemo(() => {
    if (todayIdx < 0) return undefined;
    const days = Math.round(data.kpis.lead_time_days * 7 / 5);
    const d = new Date(data.as_of); d.setDate(d.getDate() + days);
    const iso = d.toISOString().slice(0, 10);
    const hit = rows.find((r, i) => i >= todayIdx && data.period_end[data.periods.indexOf(r.period)] >= iso);
    return (hit ?? rows[rows.length - 1]).period;
  }, [rows, todayIdx, data]);
  const orderDue = periodOf(data.kpis.order_due_date);
  const stockout = periodOf(data.kpis.first_stockout_plan);
  const hasShortage = rows.some((r) => r.shortage < 0);
  // the coverage axis stops a little above the overstock threshold : beyond it the line is clipped at the
  // top (a coverage of 140 days tells nothing more than « far above the overstock »)
  const covMax = Math.max(a.overstock_days + 4, a.alert_yellow_days * 2, 10);
  const yFmt = (v: number) => fmtQty(Math.abs(v), unit);
  const hasReal = rows.some((r) => (r.real ?? 0) > 0);
  const planSource = a.consumption_source === "plan";

  const tableCols = useMemo<Column<Row>[]>(() => [
    { key: "period", label: "Période", get: (r) => r.label },
    { key: "stock", label: "Stock projeté", get: (r) => r.stock, num: true, render: (r) => fmtQty(r.stock, unit) },
    { key: "target", label: "Stock cible", get: (r) => r.target, num: true, render: (r) => fmtQty(r.target, unit) },
    { key: "demand", label: "Besoin", get: (r) => -r.demand, num: true, render: (r) => fmtQty(-r.demand, unit) },
    { key: "firm", label: "Ordre ferme", get: (r) => r.firm, num: true, render: (r) => fmtQty(r.firm, unit) },
    { key: "plan", label: "Quantité à retenir (saisie)", get: (r) => r.plan, num: true, render: (r) => fmtQty(r.plan, unit) },
    { key: "cbn", label: "Proposition CBN", get: (r) => r.cbn, num: true, render: (r) => fmtQty(r.cbn, unit) },
    { key: "receipts", label: "Quantité reçue", get: (r) => r.receipts, num: true, render: (r) => (r.receipts === null ? "" : fmtQty(r.receipts, unit)) },
    { key: "pdp", label: "PDP éclaté", get: (r) => r.pdp, num: true, render: (r) => fmtQty(r.pdp, unit) },
    { key: "real", label: "Conso réelle", get: (r) => r.real, num: true, render: (r) => (r.real === null ? "" : fmtQty(r.real, unit)) },
    { key: "bias", label: "Écart %", get: (r) => r.bias, num: true, export: "number", render: (r) => (r.bias === null ? "" : `${r.bias > 0 ? "+" : ""}${r.bias} %`) },
    { key: "cov", label: "Couverture (j)", get: (r) => r.cov, num: true },
  ], [unit]);

  const common = { data: rows, syncId: SYNC, margin: { top: 8, right: 8, left: 0, bottom: 0 } };
  const xHidden = <XAxis dataKey="period" hide />;
  const todayLine = today ? <ReferenceLine x={today} stroke="var(--brand)" strokeWidth={1.5} /> : null;
  const pastArea = todayIdx > 0 ? <ReferenceArea x1={rows[0].period} x2={rows[todayIdx].period} fill="var(--bg)" fillOpacity={0.45} /> : null;

  return (
    <div className="charts">
      <div className="row" style={{ justifyContent: "space-between", padding: "4px 8px 0" }}>
        <span className="subtle small">{rows.length} période(s) · fenêtre du tableau d'approvisionnement{perimeter.weekRange ? "" : " (tout)"} · délai {data.kpis.lead_time_days} j ouvrés · cible {a.coverage_target_days} j</span>
        <button type="button" className={`btn xs ${showTable ? "primary" : "ghost"}`} onClick={() => setShowTable((v) => !v)} title="Afficher les valeurs des quatre bandeaux en tableau (exportable)"><Table2 />Valeurs</button>
      </div>

      <PanelTitle>Stock projeté</PanelTitle>
      <ResponsiveContainer width="100%" height={hasShortage ? 270 : 230}>
        <ComposedChart {...common} stackOffset="sign">
          <CartesianGrid stroke="var(--border)" vertical={false} />
          {xHidden}
          <YAxis tick={TICK} tickFormatter={yFmt} width={Y_WIDTH} />
          <Tooltip content={<AllTooltip rows={rows} unit={unit} />} cursor={{ stroke: "var(--border-strong)" }} />
          <Legend {...LEGEND} />
          {pastArea}
          {today && leadEnd && <ReferenceArea x1={today} x2={leadEnd} fill="var(--brand)" fillOpacity={0.08} label={<ViewBoxLabel text="délai fournisseur" dy={28} muted />} />}
          <Area type="monotone" dataKey="stock" name="Stock projeté" stroke="var(--s-stock-sim)" strokeWidth={2.2} fill="var(--s-stock-sim)" fillOpacity={0.1} dot={false} activeDot={{ r: 4 }} />
          <Line type="monotone" dataKey="target" name="Stock cible" stroke="var(--s-target)" strokeWidth={1.5} strokeDasharray="2 4" dot={false} activeDot={false} />
          {hasShortage && <Bar dataKey="shortage" name="Manque cumulé" fill="var(--s-shortage)" fillOpacity={0.8} maxBarSize={22} radius={[0, 0, 3, 3]} />}
          <ReferenceLine y={0} stroke="var(--border-strong)" />
          {todayLine}
          {today && <ReferenceLine x={today} stroke="none" label={<ViewBoxLabel text="Aujourd'hui" dy={14} />} />}
          {orderDue && <ReferenceDot x={orderDue} y={0} r={0} shape={<OrderDueMarker text={`À commander au plus tard le ${fmtDate(data.kpis.order_due_date ?? "")}${data.kpis.order_due_late ? " !" : ""}`} sub={`${data.kpis.order_due_supplier_id ?? ""}${(() => { const n = data.suppliers.find((s) => s.supplier_id === data.kpis.order_due_supplier_id)?.supplier_name; return n ? ` · ${n}` : ""; })()} · ${fmtQty(data.kpis.order_due_qty ?? 0, unit)} · livraison ${fmtDate(data.kpis.order_due_delivery_date ?? "")}${data.kpis.order_due_off_standard ? " ◇" : ""}`} late={data.kpis.order_due_late} />} />}
          {stockout && <ReferenceDot x={stockout} y={0} r={5} fill="var(--critical)" stroke="var(--bg-elev)" strokeWidth={2} label={{ value: `rupture ${fmtDate(data.kpis.first_stockout_plan ?? "")}`, position: "top", fontSize: 11, fill: "var(--critical-fg)", fontWeight: 600 }} />}
        </ComposedChart>
      </ResponsiveContainer>

      <PanelTitle>Flux : commandes fermes (passé = commandé, futur = restant), saisies, CBN · réceptions en courbe · besoin en dessous</PanelTitle>
      <ResponsiveContainer width="100%" height={190}>
        <ComposedChart {...common} stackOffset="sign">
          <defs>
            <pattern id="chart-hatch" patternUnits="userSpaceOnUse" width="6" height="6" patternTransform="rotate(45)">
              <rect width="6" height="6" fill="var(--s-planned)" /><line x1="0" y1="0" x2="0" y2="6" stroke="var(--s-stock-sim)" strokeWidth="2.5" />
            </pattern>
          </defs>
          <CartesianGrid stroke="var(--border)" vertical={false} />
          {xHidden}
          <YAxis tick={TICK} tickFormatter={yFmt} width={Y_WIDTH} />
          <Tooltip content={() => null} cursor={{ stroke: "var(--border-strong)" }} />
          <Legend {...LEGEND} />
          {pastArea}
          <ReferenceLine y={0} stroke="var(--border-strong)" />
          <Bar dataKey="firm" name="Ordre ferme" stackId="in" fill="var(--s-firm)" maxBarSize={22} />
          <Bar dataKey="plan" name="Quantité à retenir (saisie)" stackId="in" fill="url(#chart-hatch)" maxBarSize={22} />
          <Bar dataKey="cbn" name="Proposition CBN" stackId="in" fill="var(--s-proposal)" maxBarSize={22} radius={[3, 3, 0, 0]} />
          <Bar dataKey="demand" name="Besoin" stackId="out" fill="var(--s-demand)" fillOpacity={0.55} maxBarSize={22} radius={[0, 0, 3, 3]} />
          <Line type="monotone" dataKey="receipts" name="Quantité reçue (passé)" stroke="var(--s-receipt)" strokeWidth={2} dot={{ r: 3.5, fill: "var(--s-receipt)", stroke: "var(--bg-elev)", strokeWidth: 1.5 }} activeDot={{ r: 5 }} connectNulls={false} />
          {todayLine}
        </ComposedChart>
      </ResponsiveContainer>

      <PanelTitle>PDP éclaté et consommation réelle · écart par période (réel − plan) / plan{planSource ? " · article en source de consommation PDP : le réel est le plan" : !hasReal ? " · aucune consommation déclarée sur la fenêtre" : ""}</PanelTitle>
      <ResponsiveContainer width="100%" height={170}>
        <ComposedChart {...common} margin={{ top: 18, right: 8, left: 0, bottom: 0 }}>
          <CartesianGrid stroke="var(--border)" vertical={false} />
          {xHidden}
          <YAxis tick={TICK} tickFormatter={yFmt} width={Y_WIDTH} />
          <Tooltip content={() => null} cursor={{ stroke: "var(--border-strong)" }} />
          <Legend {...LEGEND} />
          {pastArea}
          <Bar dataKey="pdp" name="PDP éclaté (besoin)" fill="var(--s-forecast)" fillOpacity={0.9} maxBarSize={22} radius={[3, 3, 0, 0]}>
            <LabelList dataKey="bias" position="top" fontSize={10} formatter={(v: number | null) => (v === null || v === undefined ? "" : `${v > 0 ? "+" : ""}${v} %`)} fill="var(--fg-muted)" />
          </Bar>
          {!planSource && <Line type="monotone" dataKey="real" name="Consommation réelle" stroke="var(--s-stock-forecast)" strokeWidth={2} dot={{ r: 3.5, fill: "var(--s-stock-forecast)", stroke: "var(--bg-elev)", strokeWidth: 1.5 }} activeDot={{ r: 5 }} connectNulls={false} />}
          {todayLine}
        </ComposedChart>
      </ResponsiveContainer>

      <PanelTitle>Couverture (jours ouvrés de besoin couverts par le stock projeté) <span className="subtle" style={{ fontWeight: 400 }}>· zones : <span style={{ color: "var(--critical-fg)" }}>rouge ≤ {a.alert_red_days} j + 1</span> · <span style={{ color: "var(--warning-fg)" }}>orange ≤ {a.alert_yellow_days} j</span> · cible {a.coverage_target_days} j · surstock ≥ {a.overstock_days} j (axe coupé au-delà)</span></PanelTitle>
      <ResponsiveContainer width="100%" height={160}>
        <ComposedChart {...common}>
          <CartesianGrid stroke="var(--border)" vertical={false} />
          <XAxis dataKey="period" tickFormatter={(v: string) => periodLabel(v)} tick={TICK} minTickGap={18} />
          <YAxis tick={TICK} width={Y_WIDTH} unit=" j" domain={[0, covMax]} allowDataOverflow ticks={[0, a.alert_yellow_days, a.overstock_days]} />
          <Tooltip content={() => null} cursor={{ stroke: "var(--border-strong)" }} />
          <Legend {...LEGEND} />
          <ReferenceArea y1={0} y2={a.alert_red_days + 1} fill="var(--critical)" fillOpacity={0.12} />
          <ReferenceArea y1={a.alert_red_days + 1} y2={a.alert_yellow_days} fill="var(--warning)" fillOpacity={0.1} />
          <ReferenceArea y1={a.overstock_days} y2={covMax} fill="var(--fg-muted)" fillOpacity={0.1} />
          {pastArea}
          <ReferenceLine y={a.alert_red_days + 1} stroke="var(--critical)" strokeOpacity={0.5} />
          <ReferenceLine y={a.alert_yellow_days} stroke="var(--warning)" strokeOpacity={0.5} />
          <ReferenceLine y={a.coverage_target_days} stroke="var(--fg-muted)" strokeDasharray="2 4" />
          <ReferenceLine y={a.overstock_days} stroke="var(--fg-muted)" strokeOpacity={0.5} />
          <Line type="stepAfter" dataKey="cov" name="Couverture projetée" stroke="var(--s-stock-sim)" strokeWidth={2.2} dot={false} activeDot={{ r: 4 }} />
          {todayLine}
        </ComposedChart>
      </ResponsiveContainer>

      {showTable && (
        <div style={{ marginTop: 10 }}>
          <DataTable rows={rows} columns={tableCols} rowKey={(r) => r.period} compact maxHeight={360} storageKey="article.chart.values" exportTitle={`Courbes ${data.article.article_id}`} footer={null} />
        </div>
      )}
    </div>
  );
}

/** Text anchored at the top-left corner of a reference line / area (recharts passes the ``viewBox``). */
function ViewBoxLabel({ viewBox, text, dy, muted }: { viewBox?: { x: number; y: number }; text: string; dy: number; muted?: boolean }) {
  if (!viewBox) return null;
  return <text x={viewBox.x + 6} y={viewBox.y + dy} fontSize={muted ? 10 : 11} fontWeight={muted ? 400 : 600} fill={muted ? "var(--fg-muted)" : "var(--brand)"}>{text}</text>;
}

function PanelTitle({ children }: { children: React.ReactNode }) {
  return <div className="small" style={{ fontWeight: 600, padding: "10px 8px 2px" }}>{children}</div>;
}

/** Marker of the first proposal to order : a triangle on the axis and a callout with the supplier and the quantity. */
function OrderDueMarker({ cx, cy, text, sub, late }: { cx?: number; cy?: number; text: string; sub: string; late?: boolean }) {
  if (cx === undefined || cy === undefined) return null;
  const w = Math.max(text.length, sub.length) * 6.4 + 20;
  return (
    <g>
      <polygon points={`${cx},${cy - 2} ${cx - 7},${cy + 10} ${cx + 7},${cy + 10}`} fill={late ? "var(--critical)" : "var(--s-proposal)"} />
      <rect x={cx + 10} y={cy - 36} width={w} height={38} rx={6} fill="var(--bg)" stroke="var(--border-strong)" />
      <text x={cx + 20} y={cy - 21} fontSize={11} fontWeight={600} fill="var(--fg)">{text}</text>
      <text x={cx + 20} y={cy - 7} fontSize={11} fill="var(--fg-muted)">{sub}</text>
    </g>
  );
}

/** One tooltip for the four panels : every value of the hovered period. */
function AllTooltip({ active, label, rows, unit }: { active?: boolean; label?: string; rows: Row[]; unit: string }) {
  if (!active || !label) return null;
  const r = rows.find((x) => x.period === label);
  if (!r) return null;
  const line = (name: string, v: number | null | undefined, suffix = "") => (v === null || v === undefined ? null : <div className="r" key={name}><span>{name}</span><b>{typeof v === "number" ? fmtQty(Math.abs(v), unit) : v}{suffix}</b></div>);
  return (
    <div className="tooltip-box" style={{ minWidth: 230 }}>
      <div className="t">{r.label}{r.current ? " · aujourd'hui" : r.past ? " · passé" : ""}</div>
      {line("Stock projeté", r.stock)}{line("Stock cible", r.target)}{r.shortage < 0 && line("Manque cumulé", r.shortage)}
      <div className="sep" />
      {line("Besoin", r.demand)}{r.firm > 0 && line(r.past ? "Ordre ferme (commandé)" : "Ordre ferme (restant)", r.firm)}{r.plan > 0 && line("Quantité à retenir (saisie)", r.plan)}{r.cbn > 0 && line("Proposition CBN", r.cbn)}{r.receipts !== null && r.receipts > 0 && line("Quantité reçue", r.receipts)}
      <div className="sep" />
      {line("PDP éclaté", r.pdp)}{r.real !== null && line("Conso réelle", r.real)}{r.bias !== null && <div className="r"><span>Écart</span><b style={{ color: Math.abs(r.bias) > 15 ? "var(--critical-fg)" : undefined }}>{r.bias > 0 ? "+" : ""}{r.bias} %</b></div>}
      <div className="sep" />
      <div className="r"><span>Couverture</span><b>{r.cov} j</b></div>
    </div>
  );
}
