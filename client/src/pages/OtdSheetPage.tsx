import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, ChevronLeft, ChevronRight, FileText } from "lucide-react";
import { api } from "@/lib/api";
import { useOtdSheet } from "@/lib/queries";
import { Badge, Card, Empty, ErrorBox, Skeleton, SkeletonBlock } from "@/components/ui";
import { DataTable, type Column } from "@/components/DataTable";
import { OtdCumulChart } from "@/components/charts/OtdCumulChart";
import { fmtDate, fmtInt, fmtQty, fmtWeekdays } from "@/lib/format";
import type { OtdDayCell, OtdLine, OtdSheet } from "@/lib/types";

const pct = (v: number | null | undefined, digits = 0) => (v === null || v === undefined ? "–" : v.toFixed(digits));
const tone = (v: number | null | undefined): "critical" | "warning" | "ok" | "neutral" => (v === null || v === undefined ? "neutral" : v < 60 ? "critical" : v < 85 ? "warning" : "ok");
const color = (v: number | null | undefined) => (v === null || v === undefined ? "var(--fg-subtle)" : v < 60 ? "var(--critical-fg)" : v < 85 ? "var(--warning-fg)" : "var(--ok-fg)");
const days = (v: number | null | undefined) => (v === null || v === undefined ? "–" : v === 0 ? "le jour" : `${v > 0 ? "+" : "−"}${Math.abs(v)} j`);
/** « sans BL · R123 » stays as is, a real number gets its « BL » prefix */
const blLabel = (bl: string) => (bl.startsWith("sans BL") ? bl : `BL ${bl}`);
const WD = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"];
const MONTHS = ["janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."];
/** "2026-10" → "oct. 2026" */
const shortMonth = (m: string) => `${MONTHS[Number(m.slice(5, 7)) - 1]} ${m.slice(0, 4)}`;
const shiftMonth = (m: string, n: number) => { const k = Number(m.slice(0, 4)) * 12 + Number(m.slice(5, 7)) - 1 + n; return `${Math.floor(k / 12)}-${String((k % 12) + 1).padStart(2, "0")}`; };
const weekNo = (iso: string) => { const d = new Date(`${iso}T12:00:00`); const t = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate())); const day = t.getUTCDay() || 7; t.setUTCDate(t.getUTCDate() + 4 - day); const y0 = new Date(Date.UTC(t.getUTCFullYear(), 0, 1)); return Math.ceil(((t.getTime() - y0.getTime()) / 86400000 + 1) / 7); };

/**
 * The OTD sheet of one supplier over one month (docs § 14) : the formula with its figures, the three indicators
 * decomposed, the whole weeks of the month day by day, every expected day with the reading of its scores, the two
 * cumulative curves.  Reached from the month columns of Performance › OTD and from the supplier's Performance tab.
 */
export default function OtdSheetPage() {
  const { supplierId, month } = useParams();
  const q = useOtdSheet(supplierId, month);
  const d = q.data;
  const [day, setDay] = useState<string | null>(null);
  const cols = useMemo<Column<OtdLine>[]>(() => [
    { key: "date", label: "Jour attendu", get: (o) => o.date, render: (o) => <>{fmtDate(o.date, "EEE dd/MM")}{o.shifted_from && <span className="sub" title="Créneau ERP sur un jour fermé, déplacé au jour ouvré suivant">ERP {fmtDate(o.shifted_from)}</span>}</> },
    { key: "article", label: "Article", get: (o) => `${o.article_id} ${o.designation}`, exportGet: (o) => o.article_id, render: (o) => <Link to={`/articles/${encodeURIComponent(o.article_id)}`}><b>{o.article_id}</b> <span className="subtle">{o.designation}</span></Link> },
    { key: "purch", label: "Commande", get: (o) => o.purch, hidden: true, render: (o) => <span className="mono small">{o.purch}{o.order_count > 1 ? ` (${o.order_count})` : ""}</span> },
    { key: "expected", label: "Attendu", get: (o) => o.expected, num: true, render: (o) => fmtQty(o.expected, o.unit) },
    { key: "received", label: "Reçu le jour", get: (o) => o.received, num: true, render: (o) => <>{fmtQty(o.received, o.unit)}{o.excess > o.tolerance && <span className="sub" style={{ color: "var(--critical-fg)" }}>excédent {fmtQty(o.excess)}</span>}</> },
    { key: "completed", label: "Complet le", get: (o) => o.completed ?? "", render: (o) => o.completed ? fmtDate(o.completed, "EEE dd/MM") : <Badge tone={o.status === "non livrée" ? "critical" : "neutral"}>{o.status}</Badge> },
    { key: "delta", label: "Écart", get: (o) => o.delta, num: true, render: (o) => <b style={{ color: o.delta === null ? "var(--fg-subtle)" : o.delta === 0 ? undefined : color(o.punct_score) }}>{days(o.delta)}</b> },
    { key: "qs", label: "Quantité", get: (o) => o.qty_score, num: true, export: "number", render: (o) => o.qty_score === null ? <span className="subtle">–</span> : <Badge tone={tone(o.qty_score)}>{o.qty_score}</Badge> },
    { key: "ps", label: "Ponctualité", get: (o) => o.punct_score, num: true, export: "number", render: (o) => o.punct_score === null ? <span className="subtle">–</span> : <Badge tone={tone(o.punct_score)}>{o.punct_score}</Badge> },
    { key: "reason", label: "Pourquoi", get: (o) => o.reason, filter: "none", render: (o) => <span className="small" style={{ whiteSpace: "normal", display: "block", minWidth: 260, color: "var(--fg-muted)" }}>{o.reason}</span> },
    { key: "bls", label: "BL", get: (o) => o.bls.join(", "), hidden: true },
    { key: "status", label: "Statut", get: (o) => o.status, filter: "select", hidden: true },
  ], []);
  if (!supplierId || !month) return <Empty title="Fournisseur ou mois non précisé" />;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const rows = d ? (day ? d.lines.filter((o) => o.date === day) : d.lines) : [];
  const sc = d?.scores;
  const pts = sc?.points;
  const prev = d?.history[d.history.length - 2];
  const desadvOk = d ? d.desadv.filter((b) => b.ok).length : 0;
  const pdf = api.downloadUrl("/api/performance/otd/arrete.pdf", { month, supplier_id: supplierId });
  const firstWeek = d?.weeks[0], lastWeek = d?.weeks[d.weeks.length - 1];
  return (
    <div className="page">
      <div className="page-header">
        <div className="title">
          <div className="row"><Link to="/performance" className="btn ghost sm"><ArrowLeft />Performance</Link><Link to={`/fournisseurs/${encodeURIComponent(supplierId)}`} className="btn ghost sm">Fiche fournisseur ↗</Link></div>
          <h1 style={{ marginTop: 6 }}>{supplierId} {d && <span className="muted" style={{ fontWeight: 400 }}>· {d.name}</span>}</h1>
          <p>{d ? <>Livraison le <b>{fmtWeekdays(d.delivery_weekdays)}</b> · excédent toléré <b>{d.tolerance_factor} jour{d.tolerance_factor > 1 ? "s" : ""} de besoin</b>{d.planners.length > 0 && <> · approvisionneur {d.planners.join(", ")}</>}</> : <Skeleton w={360} />}</p>
        </div>
        <div className="actions">
          <MonthNav month={month} supplierId={supplierId} />
          <a className="btn" href={pdf} target="_blank" rel="noopener" title={d?.closing ? "Arrêté du mois, chiffres figés" : "Arrêté provisoire : chiffres du jour, mois non arrêté"}><FileText />Arrêté PDF</a>
        </div>
      </div>
      {d && firstWeek && lastWeek && (
        <p className="small subtle" style={{ marginTop: -6 }}>
          Semaines {weekNo(firstWeek.monday)} à {weekNo(lastWeek.monday)} (du {fmtDate(d.from, "EEEE d MMMM")} au {fmtDate(d.to, "EEEE d MMMM")}) · {fmtInt(sc?.n)} jour(s) attendu(s), {fmtInt(sc?.bls)} BL, {fmtInt(d.articles)} article(s)
          {d.open > 0 && <> · {d.open} jour(s) encore en attente</>}{d.not_evaluable > 0 && <> · {d.not_evaluable} non évaluable(s)</>}
          {d.closing ? <> · <Badge tone="brand" title={`par ${d.closing.closed_by}`}>arrêté le {fmtDate(d.closing.closed_at, "dd/MM/yyyy HH:mm")} · OTD {pct(d.closing.otd)}</Badge></> : <> · <Badge tone="neutral">mois non arrêté</Badge></>}
        </p>
      )}
      {!d ? <SkeletonBlock rows={6} /> : sc && sc.n === 0 ? <Empty title={`Aucun jour évalué en ${d.label}`} hint="Choisir un autre mois, ou attendre la fin du délai d'évaluation." /> : sc && pts && (
        <>
          {/* the formula */}
          <Card>
            <div className="otd-formula">
              <div className="big">
                <div className="label">OTD {d.label}</div>
                <div className="value" style={{ color: color(sc.otd) }}>{pct(sc.otd)}<small> / 100</small></div>
                <div className="meta">cible 90{prev && prev.otd !== null && sc.otd !== null && <> · {shortMonth(prev.period)} {pct(prev.otd)} <span style={{ color: sc.otd >= prev.otd ? "var(--ok-fg)" : "var(--critical-fg)" }}>{sc.otd >= prev.otd ? "▲" : "▼"} {Math.abs(sc.otd - prev.otd).toFixed(0)}</span></>}</div>
              </div>
              <div className="rest">
                <div className="eq">
                  <b>{pct(sc.otd)}</b><span className="op">=</span>
                  <span><b style={{ color: color(sc.qty) }}>{pct(sc.qty)}</b> <span className="op">× {d.params.weights.qty} %</span> Quantité</span><span className="op">+</span>
                  <span><b style={{ color: color(sc.punctuality) }}>{pct(sc.punctuality)}</b> <span className="op">× {d.params.weights.punctuality} %</span> Ponctualité</span><span className="op">+</span>
                  <span><b style={{ color: color(sc.desadv) }}>{pct(sc.desadv)}</b> <span className="op">× {d.params.weights.desadv} %</span> DESADV</span>
                </div>
                <div className="points" role="img" aria-label={`${pct(pts.qty)} points quantité, ${pct(pts.punctuality)} ponctualité, ${pct(pts.desadv)} DESADV, ${pct(pts.lost)} perdus`}>
                  <span className="q" style={{ width: `${pts.qty ?? 0}%` }}>{(pts.qty ?? 0) >= 6 ? pct(pts.qty) : ""}</span>
                  <span className="p" style={{ width: `${pts.punctuality ?? 0}%` }}>{(pts.punctuality ?? 0) >= 6 ? pct(pts.punctuality) : ""}</span>
                  <span className="d" style={{ width: `${pts.desadv ?? 0}%` }}>{(pts.desadv ?? 0) >= 6 ? pct(pts.desadv) : ""}</span>
                  {(pts.lost ?? 0) > 0 && <span className="lost" style={{ width: `${pts.lost ?? 0}%` }}>{(pts.lost ?? 0) >= 6 ? `−${pct(pts.lost)}` : ""}</span>}
                </div>
                <div className="legend">
                  <span><i className="sw q" />Quantité {pct(pts.qty)} / {pct(pts.max.qty)}</span>
                  <span><i className="sw p" />Ponctualité {pct(pts.punctuality)} / {pct(pts.max.punctuality)}</span>
                  <span><i className="sw d" />DESADV {pct(pts.desadv)} / {pct(pts.max.desadv)}</span>
                  {(pts.lost ?? 0) > 0 && <b style={{ color: "var(--critical-fg)" }}>{pct(pts.lost)} point(s) perdu(s){lostOn(pts)}</b>}
                </div>
              </div>
            </div>
            <p className="small" style={{ margin: "12px 0 0", paddingTop: 10, borderTop: "1px solid var(--border)", color: "var(--fg-muted)" }}>{sentence(d)}</p>
          </Card>

          {/* the three indicators */}
          <div className="grid cols-3">
            <Card title={<span>Quantité <span className="subtle">· poids {d.params.weights.qty} %</span></span>} actions={<b className="otd-score" style={{ color: color(sc.qty) }}>{pct(sc.qty)}</b>}>
              <p className="small subtle">{d.quantity.complete} jour(s) sur {d.quantity.total} complet(s) dans les {d.params.band2_days} jours ouvrés{d.quantity.not_delivered ? `, ${d.quantity.not_delivered} non livré(s)` : ""}{d.quantity.excess ? `, ${d.quantity.excess} excédent(s)` : ", aucun excédent"}.</p>
              <div className="otd-list">
                {expectedDays(d).map((c) => <button key={c.date} type="button" className={day === c.date ? "sel" : ""} onClick={() => setDay(day === c.date ? null : c.date)}><span>{fmtDate(c.date, "EEE dd/MM")} · {fmtQty(c.expected)}</span><b style={{ color: color(c.qty_score) }}>{c.status === "non livrée" ? "non livrée" : c.completed.length ? `complet le ${c.completed.map((x) => fmtDate(x, "dd/MM")).join(", ")}` : c.status} · {pct(c.qty_score)}</b></button>)}
              </div>
              <p className="small subtle" style={{ margin: "8px 0 0" }}>Moyenne des {d.quantity.total} jour(s) = <b>{pct(sc.qty)}</b></p>
            </Card>
            <Card title={<span>Ponctualité <span className="subtle">· poids {d.params.weights.punctuality} %</span></span>} actions={<b className="otd-score" style={{ color: color(sc.punctuality) }}>{pct(sc.punctuality)}</b>}>
              <p className="small subtle">Jours ouvrés entre le jour attendu et la quantité complète. Le jour → 100 ; {d.punctuality.bands[1].label} → {d.params.band1_score} ; {d.punctuality.bands[2].label} → {d.params.band2_score} ; ensuite 0.</p>
              <div className="otd-bands">
                {d.punctuality.bands.map((b, i) => { const share = d.quantity.total ? (100 * b.count) / d.quantity.total : 0; return (
                  <div key={b.label}><span>{b.label} → {b.score}</span><div className="track"><div className={`fill b${i}`} style={{ width: `${share}%` }} /></div><b className={b.count ? "" : "subtle"}>{b.count} j</b></div>); })}
              </div>
              <p className="small subtle" style={{ margin: "8px 0 0" }}>Moyenne = <b>{pct(sc.punctuality)}</b>{d.punctuality.delay_mean !== null && <> · écart moyen {days(Math.round(d.punctuality.delay_mean * 10) / 10)}</>}</p>
            </Card>
            <Card title={<span>DESADV <span className="subtle">· poids {d.params.weights.desadv} %</span></span>} actions={<b className="otd-score" style={{ color: color(sc.desadv) }}>{pct(sc.desadv)}</b>}>
              <p className="small subtle">Part des bons de livraison du mois annoncés par un DESADV traité.</p>
              <div className="otd-list">
                {d.desadv.map((b) => <div key={`${b.bl}-${b.date}`}><span>{blLabel(b.bl)} · {fmtDate(b.date, "dd/MM")}</span><b style={{ color: b.ok ? "var(--ok-fg)" : "var(--critical-fg)" }}>{b.ok ? "DESADV ✓" : "sans DESADV"}</b></div>)}
                {d.desadv.length === 0 && <span className="subtle small">Aucun BL reçu sur le mois.</span>}
              </div>
              <p className="small subtle" style={{ margin: "8px 0 0" }}>{desadvOk} BL annoncé(s) ÷ {d.desadv.length} BL = <b>{pct(sc.desadv)}</b></p>
            </Card>
          </div>

          {/* the weeks of the month */}
          <Card title={`Les ${d.weeks_count} semaines du mois, jour par jour`} hint="un clic sur un jour attendu filtre le tableau" actions={<span className="legend"><span><i className="sw cal-ok" />le jour → 100</span><span><i className="sw cal-warn" />{d.punctuality.bands[1].label} → {d.params.band1_score}</span><span><i className="sw cal-bad" />{d.punctuality.bands[2].label} → {d.params.band2_score}, au-delà, non livré</span><span><i className="sw cal-rec" />réception</span></span>}>
            <Calendar sheet={d} selected={day} onPick={(x) => setDay(day === x ? null : x)} />
          </Card>

          {/* the lines */}
          <Card flush title="Le calcul, jour attendu par jour attendu" hint={day ? `jour ${fmtDate(day)} · ${rows.length} ligne(s)` : `${fmtInt(d.lines.length)} ligne(s)`} actions={day ? <button type="button" className="btn sm" onClick={() => setDay(null)}>Tous les jours</button> : undefined}>
            <DataTable rows={rows} columns={cols} rowKey={(o) => o.id} compact maxHeight="60vh" storageKey="perf.otd.sheet" exportTitle={`OTD ${supplierId} ${d.label}`} emptyTitle="Aucune ligne" />
          </Card>

          {/* the cumulative curves */}
          <Card title="Cumul attendu et cumul reçu sur le mois" hint="l'écart horizontal entre les deux marches, c'est le retard" collapsible storageKey="perf.otd.sheet.cumul">
            <OtdCumulChart weeks={d.weeks} height={220} />
          </Card>
        </>
      )}
    </div>
  );
}

function lostOn(p: { qty: number | null; punctuality: number | null; desadv: number | null; max: { qty: number; punctuality: number; desadv: number } }): string {
  const parts = [["la quantité", p.max.qty - (p.qty ?? 0)], ["la ponctualité", p.max.punctuality - (p.punctuality ?? 0)], ["le DESADV", p.max.desadv - (p.desadv ?? 0)]].filter(([, v]) => (v as number) > 0.05) as [string, number][];
  if (parts.length === 1) return `, tous sur ${parts[0][0]}`;
  return parts.length ? ` (${parts.map(([k, v]) => `${v.toFixed(0)} sur ${k}`).join(", ")})` : "";
}

function sentence(d: OtdSheet): string {
  const q = d.quantity, p = d.punctuality, ok = d.desadv.filter((b) => b.ok).length;
  const bits = [`${q.complete} jour${q.complete > 1 ? "s" : ""} sur ${q.total} livré${q.complete > 1 ? "s" : ""} en quantité complète`];
  if (q.not_delivered) bits.push(`${q.not_delivered} non livré${q.not_delivered > 1 ? "s" : ""}`);
  if (q.excess) bits.push(`${q.excess} excédent${q.excess > 1 ? "s" : ""}`);
  if (p.late) bits.push(`${p.late} livraison${p.late > 1 ? "s" : ""} en retard`);
  if (p.early) bits.push(`${p.early} en avance`);
  bits.push(d.desadv.length ? `${ok} BL sur ${d.desadv.length} annoncé${ok > 1 ? "s" : ""} par DESADV` : "aucun BL");
  const lost = d.scores.points.lost ?? 0;
  return `En une phrase : ${bits.join(", ")} ; ${lost > 0 ? `${lost.toFixed(0)} point${lost > 1 ? "s" : ""} perdu${lost > 1 ? "s" : ""}.` : "aucun point perdu."}`;
}

function expectedDays(d: OtdSheet): OtdDayCell[] {
  return d.weeks.flatMap((w) => w.days).filter((c) => c.lines > 0);
}

function MonthNav({ month, supplierId }: { month: string; supplierId: string }) {
  const to = (m: string) => `/performance/otd/${encodeURIComponent(supplierId)}/${m}`;
  return (
    <div className="month-nav" role="group" aria-label="Mois">
      <Link to={to(shiftMonth(month, -1))} className="arrow" aria-label="Mois précédent" title="Mois précédent"><ChevronLeft size={16} /></Link>
      <Link to={to(shiftMonth(month, -1))}>{shortMonth(shiftMonth(month, -1))}</Link>
      <span className="current">{shortMonth(month)}</span>
      <Link to={to(shiftMonth(month, 1))}>{shortMonth(shiftMonth(month, 1))}</Link>
      <Link to={to(shiftMonth(month, 1))} className="arrow" aria-label="Mois suivant" title="Mois suivant"><ChevronRight size={16} /></Link>
    </div>
  );
}

function Calendar({ sheet, selected, onPick }: { sheet: OtdSheet; selected: string | null; onPick: (date: string) => void }) {
  const weekdays = useMemo(() => { const s = new Set<number>([1, 2, 3, 4, 5]); sheet.weeks.forEach((w) => w.days.forEach((c) => s.add(new Date(`${c.date}T12:00:00`).getDay() || 7))); return Array.from(s).sort(); }, [sheet]);
  const cellTone = (c: OtdDayCell) => (c.lines === 0 ? "" : c.status === "non livrée" ? "bad" : c.status === "ouverte" || c.status === "non évaluable" ? "wait" : c.punct_score === null ? "" : c.punct_score >= 100 ? "ok" : c.punct_score >= sheet.params.band1_score ? "warn" : "bad");
  return (
    <div className="scroll-x">
      <div className="otd-cal" style={{ gridTemplateColumns: `88px repeat(${weekdays.length}, minmax(0, 1fr))` }}>
        <div />
        {weekdays.map((wd) => <div key={wd} className="head">{WD[wd - 1]}</div>)}
        {sheet.weeks.map((w) => (
          <WeekRow key={w.label} week={w} weekdays={weekdays} selected={selected} onPick={onPick} cellTone={cellTone} />
        ))}
      </div>
    </div>
  );
}

function WeekRow({ week, weekdays, selected, onPick, cellTone }: { week: OtdSheet["weeks"][number]; weekdays: number[]; selected: string | null; onPick: (d: string) => void; cellTone: (c: OtdDayCell) => string }) {
  const byWd = new Map(week.days.map((c) => [new Date(`${c.date}T12:00:00`).getDay() || 7, c]));
  return (
    <>
      <div className="wk"><b>S{String(weekNo(week.monday)).padStart(2, "0")}</b><span className="subtle">{fmtDate(week.monday, "d")} → {fmtDate(week.sunday, "d MMM")}</span></div>
      {weekdays.map((wd) => {
        const c = byWd.get(wd);
        if (!c) return <div key={wd} className="cell off" />;
        const t = cellTone(c);
        const content = (
          <>
            <div className="top"><b>{fmtDate(c.date, "d")}</b>{c.lines > 0 && c.punct_score !== null && <span className={`pill ${t}`}>P {pct(c.punct_score)}</span>}{c.lines > 0 && c.punct_score === null && <span className="pill wait">{c.status}</span>}</div>
            {c.lines > 0 && <div>Attendu <b>{fmtQty(c.expected)}</b>{c.lines > 1 && <span className="subtle"> · {c.lines} art.</span>}{c.delta === 0 && <span className="subtle"> · le jour</span>}</div>}
            {c.lines > 0 && c.delta !== null && c.delta !== 0 && <div style={{ color: color(c.punct_score) }}>complet le {c.completed.map((x) => fmtDate(x, "d/MM")).join(", ")} · {days(Math.round(c.delta))}</div>}
            {c.received > 0 && <div className="rec">Reçu <b>{fmtQty(c.received)}</b>{c.bls.length > 0 && <span className="subtle"> · {c.bls.slice(0, 3).map(blLabel).join(", ")}{c.bls.length > 3 ? "…" : ""}</span>}</div>}
          </>
        );
        return c.lines > 0
          ? <button key={wd} type="button" className={`cell ${t} ${c.received > 0 ? "rec" : ""} ${selected === c.date ? "sel" : ""}`} onClick={() => onPick(c.date)} title="Filtrer le tableau sur ce jour">{content}</button>
          : <div key={wd} className={`cell ${c.received > 0 ? "rec" : ""}`}>{content}</div>;
      })}
    </>
  );
}
