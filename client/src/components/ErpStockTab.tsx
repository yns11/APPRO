import { useMemo, useState } from "react";
import { Bar, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Scale } from "lucide-react";
import { Badge, Button, Card, Empty } from "@/components/ui";
import { CalibrateDialog, fmtSigned, fmtSignedPct } from "@/components/CalibrateDialog";
import { fmtDate, fmtQty } from "@/lib/format";
import type { AdjustmentOut, ProjectionResponse } from "@/lib/types";

/** Adjustments created by « Caler sur l'ERP » (their note starts with the calage wording). */
export const isCalibration = (a: AdjustmentOut) => /^Calage sur le stock ERP/i.test(a.note ?? "");

/**
 * Fiche article › Stock ERP : the ERP stock snapshots against the projected stock of the same day (the copied history, 400 days by default),
 * the reading of the last gap (warehouses, sign, drift), the « Caler sur l'ERP » button and the past calibrations.
 */
export function ErpStockTab({ d, adjustments, canEdit }: { d: ProjectionResponse; adjustments: AdjustmentOut[]; canEdit: boolean }) {
  const [dialog, setDialog] = useState(false);
  const unit = d.article.unit;
  const k = d.kpis;
  const snaps = d.erp_snapshots;
  const last = snaps[snaps.length - 1];
  const rows = useMemo(() => snaps.map((s) => ({ ...s, label: fmtDate(s.date, "dd/MM"), gapBar: s.gap })), [snaps]);
  const calibrations = useMemo(() => adjustments.filter(isCalibration).sort((a, b) => b.date.localeCompare(a.date)), [adjustments]);
  if (!last) return <Empty title="Aucun snapshot ERP" hint="La table de stock ERP n'est pas configurée, ou aucun snapshot ne couvre cet article." />;
  const low = last.gap < 0;
  const tone = k.erp_stock_drift ? (low ? "warning" : "info") : "neutral";
  return (
    <div className="grid cols-3">
      <Card className="span-2" title="Stock ERP et dérive · snapshots copiés" hint="points = stock ERP en fin de journée (entrepôts configurés) · ligne = stock projeté par l'application le même jour · barres = écart signé ERP − projeté"
        actions={canEdit && Math.abs(last.gap) > 1e-9 ? <Button size="sm" variant={k.erp_stock_drift ? "primary" : "default"} onClick={() => setDialog(true)}><Scale />Caler sur l'ERP (ajustement {fmtSigned(last.gap, unit)} au {fmtDate(last.date)})…</Button> : undefined}>
        <ResponsiveContainer width="100%" height={260}>
          <ComposedChart data={rows} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
            <CartesianGrid stroke="var(--border)" vertical={false} />
            <XAxis dataKey="label" tick={{ fontSize: 11, fill: "var(--fg-subtle)" }} minTickGap={14} />
            <YAxis yAxisId="stock" tick={{ fontSize: 11, fill: "var(--fg-subtle)" }} width={64} tickFormatter={(v: number) => fmtQty(v, unit)} />
            <YAxis yAxisId="gap" orientation="right" tick={{ fontSize: 11, fill: "var(--fg-subtle)" }} width={56} tickFormatter={(v: number) => fmtSigned(v, unit)} />
            <Tooltip contentStyle={{ fontSize: 11 }} formatter={(v: number, name: string) => [name === "Écart ERP − projeté" ? fmtSigned(v, unit) : fmtQty(v, unit), name]} />
            <Legend wrapperStyle={{ fontSize: 11 }} />
            <ReferenceLine yAxisId="gap" y={0} stroke="var(--border-strong)" />
            <Bar yAxisId="gap" dataKey="gapBar" name="Écart ERP − projeté" fill="var(--fg-subtle)" fillOpacity={0.35} maxBarSize={14} isAnimationActive={false} />
            <Line yAxisId="stock" type="monotone" dataKey="projected" name="Stock projeté (application)" stroke="var(--brand)" strokeWidth={2} dot={false} isAnimationActive={false} />
            <Line yAxisId="stock" type="monotone" dataKey="qty" name="Stock ERP (snapshot)" stroke="var(--fg)" strokeWidth={1} strokeDasharray="2 3" isAnimationActive={false}
              dot={(p: { cx?: number; cy?: number; payload?: { drift: boolean; beyond: boolean; gap: number } }) => <circle key={`${p.cx}-${p.cy}`} cx={p.cx} cy={p.cy} r={p.payload?.drift ? 4.5 : 3.5} fill={p.payload?.drift ? (p.payload.gap < 0 ? "var(--warning)" : "var(--info)") : p.payload?.beyond ? "var(--fg-muted)" : "var(--fg)"} stroke="var(--bg-elev)" strokeWidth={1.5} />} />
          </ComposedChart>
        </ResponsiveContainer>
        <div className="legend small" style={{ padding: "6px 0 0" }}>
          <span><span className="dot" style={{ background: "var(--fg)", verticalAlign: "middle" }} /> snapshot ERP · <span className="dot" style={{ background: "var(--warning)", verticalAlign: "middle" }} /> dérive, ERP plus bas · <span className="dot" style={{ background: "var(--info)", verticalAlign: "middle" }} /> dérive, ERP plus haut</span>
          <span>un signe qui change d'un jour à l'autre est un décalage de déclaration, pas une dérive</span>
        </div>
      </Card>
      <div className="stack">
        <Card title="Lecture de l'écart" tight>
          <table className="tbl compact">
            <tbody>
              <tr><td className="subtle">Stock ERP du {fmtDate(last.date)}</td><td className="num">{fmtQty(last.qty, unit)}</td></tr>
              {Object.entries(last.warehouses).map(([w, q]) => <tr key={w}><td className="subtle" style={{ paddingLeft: 18 }}>{w}</td><td className="num subtle">{fmtQty(q, unit)}</td></tr>)}
              <tr><td className="subtle">Stock projeté le {fmtDate(last.date)}</td><td className="num">{fmtQty(last.projected, unit)}</td></tr>
              <tr style={{ borderTop: "1px solid var(--border)" }}><td><b>Écart ERP − projeté</b></td><td className="num"><b style={{ color: low ? "var(--warning-fg)" : "var(--info-fg)" }}>{fmtSigned(last.gap, unit)}{last.gap_pct !== null ? ` · ${fmtSignedPct(last.gap_pct)}` : ""}</b></td></tr>
            </tbody>
          </table>
          <p className="small" style={{ margin: "10px 0 0" }}>
            <Badge tone={tone}>{k.erp_stock_drift ? `dérive · ${k.erp_stock_drift_runs} snapshots de suite` : k.erp_stock_beyond ? "au-delà du seuil, pas encore une dérive" : "sous le seuil"}</Badge>
            {k.erp_stock_stale && <Badge tone="warning">snapshot ancien</Badge>}
          </p>
          <p className="small subtle" style={{ margin: "8px 0 0" }}>
            {low ? <><b style={{ color: "var(--warning-fg)" }}>Écart négatif</b> : l'application surestime le stock, une rupture peut se cacher{k.coverage_erp_stock_days !== null ? <> ({k.coverage_erp_stock_days} j de couverture selon l'ERP contre {k.coverage_at_date_days} j)</> : null}.</>
              : <><b style={{ color: "var(--info-fg)" }}>Écart positif</b> : l'application sous-estime le stock, risque de commander trop ou trop tôt.</>}
            {" "}Dérive = écart au-delà du seuil (Paramètres › Règles communes) du même signe sur les derniers snapshots ; un signe qui change est un décalage de déclaration.
          </p>
        </Card>
        <Card title="Calages passés" hint="ajustements créés par « Caler sur l'ERP » (annulables, journalisés)" tight>
          {calibrations.length === 0 ? <p className="small subtle" style={{ margin: 0 }}>Aucun calage.</p> : (
            <table className="tbl compact">
              <thead><tr><th>Date</th><th className="num">Ajustement</th><th>Par</th></tr></thead>
              <tbody>{calibrations.map((c) => <tr key={c.id}><td>{fmtDate(c.date)}</td><td className="num" style={{ color: c.qty < 0 ? "var(--warning-fg)" : "var(--info-fg)" }}>{fmtSigned(c.qty, unit)}</td><td className="subtle small">{c.updated_by}</td></tr>)}</tbody>
            </table>
          )}
          {calibrations.length >= 3 && <p className="small subtle" style={{ margin: "8px 0 0" }}>Plusieurs calages du même signe : un problème de déclaration plutôt que de stock (consommation ou rebut non déclarés, réception comptée deux fois, PO non déclarée…).</p>}
        </Card>
      </div>
      {dialog && <CalibrateDialog rows={[{ article_id: d.article.article_id, designation: d.article.designation, unit, erp_qty: last.qty, erp_date: last.date, projected: last.projected, gap: last.gap }]} onClose={() => setDialog(false)} />}
    </div>
  );
}
