import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { CalendarRange, CheckCircle2, Gauge, Layers, PackageCheck, Timer, Truck, Wand2 } from "lucide-react";
import { api } from "@/lib/api";
import { useLeadTimes, useOtd, useSegmentation, useSupplierNames, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { useRemembered } from "@/lib/pageState";
import { ArticleLink, Badge, Button, Card, Empty, ErrorBox, Kpi, Segmented, Skeleton, SkeletonBlock, SupplierLink, Tabs, useToast } from "@/components/ui";
import { DataTable, type Column } from "@/components/DataTable";
import { SearchSelect } from "@/components/SearchSelect";
import { OtdChart } from "@/components/charts/OtdChart";
import { fmtDate, fmtEur, fmtInt, fmtQty } from "@/lib/format";
import type { LeadArticle, LeadSupplier, OtdBucket, OtdLine, OtdSupplier, SegmentRow } from "@/lib/types";

type Tab = "otd" | "lead" | "segments";
const TABS: { id: Tab; label: string }[] = [{ id: "otd", label: "Service fournisseur (OTD)" }, { id: "lead", label: "Délais observés" }, { id: "segments", label: "Segmentation ABC / XYZ" }];
const BUCKETS: { id: OtdBucket; label: string }[] = [{ id: "week", label: "Semaines" }, { id: "month", label: "Mois" }, { id: "quarter", label: "Trimestres" }, { id: "year", label: "Années" }];
const PRESETS: { id: string; label: string; months: number }[] = [{ id: "3m", label: "3 mois", months: 3 }, { id: "6m", label: "6 mois", months: 6 }, { id: "12m", label: "12 mois", months: 12 }, { id: "24m", label: "24 mois", months: 24 }];

const pct = (v: number | null | undefined, digits = 0) => (v === null || v === undefined ? "–" : `${v.toFixed(digits)} %`);
const tone = (v: number | null | undefined): "critical" | "warning" | "ok" | "neutral" => (v === null || v === undefined ? "neutral" : v < 60 ? "critical" : v < 85 ? "warning" : "ok");
/** the same colour for a KPI tile (no tile tone when there is no value) */
const ktone = (v: number | null | undefined): "critical" | "warning" | "ok" | undefined => { const t = tone(v); return t === "neutral" ? undefined : t; };
function ScoreBar({ v }: { v: number | null | undefined }) {
  if (v === null || v === undefined) return <span className="subtle">–</span>;
  return <span className={`score-bar ${tone(v)}`}><span className="track"><span className="fill" style={{ width: `${Math.max(0, Math.min(100, v))}%` }} /></span><b>{v.toFixed(0)}</b></span>;
}
const days = (v: number | null | undefined) => (v === null || v === undefined ? "–" : `${v > 0 ? "+" : ""}${v} j`);
function Profile({ shares, title }: { shares: number[]; title: string }) {
  const max = Math.max(1, ...shares);
  return <span className="profile-bars" title={`${title} : ${["Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"].map((d, i) => `${d} ${shares[i] ?? 0} %`).join(" · ")}`}>{shares.map((s, i) => <span key={i} className={i >= 5 ? "we" : ""} style={{ height: `${Math.max(1, (22 * s) / max)}px` }} />)}</span>;
}

/**
 * Performance fournisseurs : the service (OTD per expected day : quantity, punctuality, DESADV ; weights and bands in
 * Paramètres › Règles communes) per supplier over whole weeks, with the evolution per week / month / quarter / year ;
 * the observed lead times ; the ABC / XYZ segmentation of the articles and the coverage target it suggests.
 */
export default function PerformancePage() {
  const [params, setParams] = useSearchParams();
  const tab: Tab = TABS.find((t) => t.id === params.get("tab"))?.id ?? "otd";
  const setTab = (t: Tab) => { const next = new URLSearchParams(params); if (t === "otd") next.delete("tab"); else next.set("tab", t); setParams(next, { replace: true }); };
  const { perimeter, config } = usePerimeter();
  const names = useSupplierNames();
  const supplierOptions = useMemo(() => Array.from(names.entries()).map(([id, name]) => ({ id, label: name || id, hint: id })).sort((a, b) => a.label.localeCompare(b.label, "fr")), [names]);
  const [bucket, setBucket] = useRemembered<OtdBucket>("perf.bucket", "month");
  const [preset, setPreset] = useRemembered<string>("perf.preset", "12m");
  const [custom, setCustom] = useState<{ from: string; to: string }>({ from: "", to: "" });
  const [supplier, setSupplier] = useState<string>(params.get("supplier_id") ?? "");
  const [article, setArticle] = useState("");
  const asOf = config?.as_of ?? "";
  const range = useMemo(() => {
    if (custom.from || custom.to) return { date_from: custom.from || undefined, date_to: custom.to || undefined };
    const months = PRESETS.find((p) => p.id === preset)?.months ?? 12;
    if (!asOf) return {};
    const to = new Date(`${asOf}T12:00:00`); to.setDate(to.getDate() - 1);
    const from = new Date(`${asOf}T12:00:00`); from.setMonth(from.getMonth() - months);
    const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
    return { date_from: iso(from), date_to: iso(to) };
  }, [custom, preset, asOf]);
  const common = { ...range, planner: perimeter.planner ?? undefined, supplier_id: supplier || undefined, article_id: article.trim() || undefined };
  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Performance fournisseurs</h1><p>{tab === "otd" ? "Par fournisseur, article et jour de livraison attendu : quantité, ponctualité, DESADV. Semaines entières ; un fournisseur = la moyenne de ses jours." : tab === "lead" ? "Délais mesurés sur les réceptions : retard moyen, parts à l'heure et en retard, biais contre le délai paramétré, urgences, profils par jour de la semaine." : "ABC par valeur annuelle, XYZ par régularité de la consommation ; chaque segment suggère une couverture cible."}</p></div>
        <div className="actions"><Link className="btn sm" to="/parametres" title="Poids de l'OTD, tranches de ponctualité, seuils ABC / XYZ">Règles</Link></div>
      </div>
      <Tabs<Tab> tabs={TABS} value={tab} onChange={setTab} />
      {tab !== "segments" && (
        <Card tight>
          <div className="row wrap">
            <Segmented size="sm" value={preset} onChange={(p) => { setPreset(p); setCustom({ from: "", to: "" }); }} options={PRESETS.map((p) => ({ id: p.id, label: p.label }))} />
            <label className="field"><span className="sr-only">Du</span><input className="input sm" type="date" value={custom.from} onChange={(e) => setCustom({ ...custom, from: e.target.value })} aria-label="Du" /></label>
            <label className="field"><span className="sr-only">Au</span><input className="input sm" type="date" value={custom.to} onChange={(e) => setCustom({ ...custom, to: e.target.value })} aria-label="Au" /></label>
            {tab === "otd" && <Segmented size="sm" value={bucket} onChange={setBucket} options={BUCKETS} />}
            <SearchSelect value={supplier} onChange={setSupplier} options={supplierOptions} placeholder="Tous les fournisseurs" ariaLabel="Fournisseur" width={280} />
            <input className="input sm" placeholder="Article (code exact)" value={article} onChange={(e) => setArticle(e.target.value)} style={{ width: 170 }} aria-label="Article" />
            <span className="subtle small" style={{ marginLeft: "auto" }}>{range.date_from ? `${fmtDate(range.date_from)} → ${fmtDate(range.date_to)}` : ""}{perimeter.planner ? ` · approvisionneur ${perimeter.planner}` : ""}</span>
          </div>
        </Card>
      )}
      {tab === "otd" && <OtdTab params={{ ...common, bucket }} />}
      {tab === "lead" && <LeadTab params={common} />}
      {tab === "segments" && <SegmentsTab />}
    </div>
  );
}

/* ---------------------------------------------------------------- OTD */
function OtdTab({ params }: { params: Record<string, string | undefined> }) {
  const q = useOtd(params);
  const d = q.data;
  const [showLines, setShowLines] = useRemembered("perf.otd.lines", false);
  const supCols = useMemo<Column<OtdSupplier>[]>(() => [
    { key: "supplier", label: "Fournisseur", get: (s) => `${s.supplier_id ?? ""} ${s.name}`, render: (s) => <SupplierLink id={s.supplier_id} name={s.name} /> },
    { key: "n", label: "Jours", get: (s) => s.n, num: true, title: "Jours de livraison attendus et évalués sur la période" },
    { key: "otd", label: "OTD", get: (s) => s.otd, num: true, export: "number", render: (s) => <ScoreBar v={s.otd} /> },
    { key: "qty", label: "Quantité", get: (s) => s.qty, num: true, export: "number", render: (s) => <Badge tone={tone(s.qty)}>{pct(s.qty)}</Badge> },
    { key: "punct", label: "Ponctualité", get: (s) => s.punctuality, num: true, export: "number", render: (s) => <Badge tone={tone(s.punctuality)}>{pct(s.punctuality)}</Badge> },
    { key: "desadv", label: "DESADV", get: (s) => s.desadv, num: true, export: "number", title: "Part des BL de la période annoncés par un DESADV traité", render: (s) => <Badge tone={tone(s.desadv)}>{pct(s.desadv)}</Badge> },
    { key: "nd", label: "Non livrés", get: (s) => s.not_delivered, num: true, render: (s) => s.not_delivered ? <b style={{ color: "var(--critical-fg)" }}>{s.not_delivered}</b> : <span className="subtle">0</span> },
    { key: "window", label: "Fenêtre", get: (s) => s.window, num: true, hidden: true, title: "Jours ouvrés tolérés de part et d'autre du jour attendu", render: (s) => `±${s.window} j` },
    { key: "articles", label: "Articles", get: (s) => s.articles, num: true, hidden: true },
  ], []);
  const lineCols = useMemo<Column<OtdLine>[]>(() => [
    { key: "date", label: "Jour", get: (o) => o.date, render: (o) => <>{fmtDate(o.date)}{o.shifted_from && <span className="sub" title="Créneau ERP sur un jour fermé, déplacé au jour ouvré suivant">ERP {fmtDate(o.shifted_from)}</span>}</> },
    { key: "supplier", label: "Fournisseur", get: (o) => `${o.supplier_id ?? ""} ${o.supplier_name}`, render: (o) => <SupplierLink id={o.supplier_id} name={o.supplier_name} inline /> },
    { key: "article", label: "Article", get: (o) => `${o.article_id} ${o.designation}`, exportGet: (o) => o.article_id, render: (o) => <ArticleLink id={o.article_id} designation={o.designation} /> },
    { key: "expected", label: "Attendu", get: (o) => o.expected, num: true, render: (o) => fmtQty(o.expected, o.unit) },
    { key: "received", label: "Reçu le jour", get: (o) => o.received, num: true, render: (o) => <>{fmtQty(o.received, o.unit)}{o.excess > o.tolerance && <span className="sub" style={{ color: "var(--critical-fg)" }}>excédent {fmtQty(o.excess)}</span>}</> },
    { key: "delta", label: "Écart", get: (o) => o.delta, num: true, title: "Jours ouvrés entre le jour attendu et la quantité complète (négatif = avance)", render: (o) => o.delta === null ? <span className="subtle">–</span> : <span style={{ color: o.delta > o.window ? "var(--critical-fg)" : o.delta < -o.window ? "var(--info-fg)" : undefined }}>{o.delta === 0 ? "le jour" : days(o.delta)}</span> },
    { key: "qs", label: "Quantité", get: (o) => o.qty_score, num: true, export: "number", render: (o) => o.qty_score === null ? <span className="subtle">–</span> : <Badge tone={tone(o.qty_score)}>{o.qty_score}</Badge> },
    { key: "ps", label: "Ponctualité", get: (o) => o.punct_score, num: true, export: "number", render: (o) => o.punct_score === null ? <span className="subtle">–</span> : <Badge tone={tone(o.punct_score)}>{o.punct_score}</Badge> },
    { key: "status", label: "Statut", get: (o) => o.status, filter: "select", render: (o) => <Badge tone={o.status === "reçue" ? "ok" : o.status === "non livrée" ? "critical" : "neutral"}>{o.status}</Badge> },
    { key: "purch", label: "Commandes", get: (o) => o.purch, hidden: true, render: (o) => <span className="mono small">{o.purch}{o.order_count > 1 ? ` (${o.order_count})` : ""}</span> },
    { key: "completed", label: "Complet le", get: (o) => o.completed ?? "", hidden: true, render: (o) => o.completed ? fmtDate(o.completed) : <span className="subtle">–</span> },
    { key: "bls", label: "BL", get: (o) => o.bls.join(", "), hidden: true },
    { key: "planner", label: "Approvisionneur", get: (o) => o.planner, filter: "select", hidden: true },
  ], []);
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const t = d?.totals;
  return (
    <>
      <div className="grid kpis">
        <Kpi label="OTD" icon={<Gauge size={14} />} value={t ? pct(t.otd) : <Skeleton w={60} h={28} />} meta={t ? `${fmtInt(t.suppliers)} fournisseur(s) · ${fmtInt(t.n)} jour(s)` : ""} tone={ktone(t?.otd)} />
        <Kpi label="Quantité" icon={<PackageCheck size={14} />} value={t ? pct(t.qty) : <Skeleton w={60} h={28} />} meta={d ? `poids ${d.params.weights.qty} %` : ""} tone={ktone(t?.qty)} />
        <Kpi label="Ponctualité" icon={<Timer size={14} />} value={t ? pct(t.punctuality) : <Skeleton w={60} h={28} />} meta={d ? `poids ${d.params.weights.punctuality} %` : ""} tone={ktone(t?.punctuality)} />
        <Kpi label="DESADV" icon={<Truck size={14} />} value={t ? pct(t.desadv) : <Skeleton w={60} h={28} />} meta={d ? `poids ${d.params.weights.desadv} %` : ""} tone={ktone(t?.desadv)} />
      </div>
      {d && (d.not_evaluable > 0 || !d.first_seen_available) && (
        <p className="small subtle">{d.not_evaluable > 0 ? `${d.not_evaluable} jour(s) reçu(s) d'après l'ERP sans réception dans l'extraction : non notés. ` : ""}{!d.first_seen_available ? "La date de création des commandes viendra avec le job de synchronisation (délai observé, urgences)." : ""}</p>
      )}
      <Card title="Évolution" hint={`par ${BUCKETS.find((b) => b.id === d?.bucket)?.label.toLowerCase() ?? "période"}`} collapsible storageKey="perf.otd.chart">
        {q.isLoading || !d ? <Skeleton h={240} /> : d.totals.series.length === 0 ? <Empty title="Aucun jour évalué sur la période" hint="Élargir la période ou retirer les filtres." /> : <OtdChart series={d.totals.series} target={90} height={240} />}
      </Card>
      <Card flush title="Par fournisseur" actions={<Button size="sm" variant={showLines ? "primary" : "default"} onClick={() => setShowLines(!showLines)}>Détail par jour</Button>}>
        {q.isLoading || !d ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : <DataTable rows={d.suppliers} columns={supCols} rowKey={(s) => s.supplier_id ?? "none"} compact maxHeight="50vh" storageKey="perf.otd.suppliers2" exportTitle="OTD par fournisseur" emptyTitle="Aucun jour évalué" />}
      </Card>
      {showLines && d && (
        <Card flush title="Détail par jour" hint={`${fmtInt(d.lines.length)} ligne(s)${d.truncated ? ", liste tronquée à 5 000" : ""}`}>
          <DataTable rows={d.lines} columns={lineCols} rowKey={(o) => o.id} compact maxHeight="60vh" storageKey="perf.otd.lines2" exportTitle="OTD par jour" emptyTitle="Aucune ligne" />
        </Card>
      )}
    </>
  );
}

/* ---------------------------------------------------------------- observed lead times */
function LeadTab({ params }: { params: Record<string, string | undefined> }) {
  const q = useLeadTimes(params);
  const d = q.data;
  const base = <T extends LeadSupplier | LeadArticle>(): Column<T>[] => [
    { key: "n", label: "Jours", get: (r) => r.n, num: true, title: "Jours de livraison attendus sur la période" },
    { key: "received", label: "Reçus", get: (r) => r.received, num: true },
    { key: "nd", label: "Non livrés", get: (r) => r.not_delivered, num: true, render: (r) => r.not_delivered ? <b style={{ color: "var(--critical-fg)" }}>{r.not_delivered}</b> : <span className="subtle">0</span> },
    { key: "delay", label: "Retard moyen", get: (r) => r.delay_mean, num: true, export: "number", title: "Jours ouvrés entre le jour attendu et la quantité complète (négatif = avance)", render: (r) => r.delay_mean === null ? <span className="subtle">–</span> : <b style={{ color: r.delay_mean > 0 ? "var(--critical-fg)" : undefined }}>{days(r.delay_mean)}</b> },
    { key: "on_time", label: "À l'heure", get: (r) => r.on_time_pct, num: true, export: "number", render: (r) => <Badge tone={tone(r.on_time_pct)}>{pct(r.on_time_pct)}</Badge> },
    { key: "late", label: "En retard", get: (r) => r.late_pct, num: true, export: "number", render: (r) => <>{pct(r.late_pct)}{r.recurring_late && <Badge tone="critical" title="Au moins la moitié des livraisons en retard">récurrent</Badge>}</> },
    { key: "beyond", label: `> ${d?.band2_days ?? 4} j`, get: (r) => r.late_beyond_pct, num: true, export: "number", title: "Part des livraisons au-delà de la seconde tranche", render: (r) => pct(r.late_beyond_pct) },
    { key: "promised", label: "Délai paramétré", get: (r) => String(r.lead_promised ?? ""), num: true, render: (r) => <span className="subtle">{r.lead_promised ?? "–"} j</span> },
    { key: "observed", label: "Délai observé", get: (r) => r.lead_observed_mean, num: true, export: "number", title: "Création de la commande → quantité complète, jours ouvrés", render: (r) => r.lead_observed_mean === null ? <span className="subtle">–</span> : `${r.lead_observed_mean} j` },
    { key: "bias", label: "Biais", get: (r) => r.bias_mean, num: true, export: "number", title: "Observé − paramétré : positif = délai du référentiel optimiste", render: (r) => r.bias_mean === null ? <span className="subtle">–</span> : <b style={{ color: r.bias_mean > 0 ? "var(--critical-fg)" : "var(--ok-fg)" }}>{days(r.bias_mean)}</b> },
    { key: "ship", label: "Expédition → réception", get: (r) => r.ship_to_receipt_mean, num: true, export: "number", title: "DESADV émis → réception, jours calendaires", render: (r) => r.ship_to_receipt_mean === null ? <span className="subtle">–</span> : `${r.ship_to_receipt_mean} j` },
    { key: "urgent", label: "Urgences", get: (r) => r.urgent_pct, num: true, export: "number", title: "Commandes créées à l'intérieur du délai", render: (r) => pct(r.urgent_pct) },
    { key: "partial", label: "Partielles", get: (r) => r.partial_pct, num: true, export: "number", title: "Jours servis en plusieurs réceptions", render: (r) => pct(r.partial_pct) },
    { key: "qty", label: "Qté / réception", get: (r) => r.qty_per_receipt_mean, num: true, export: "number", hidden: true, render: (r) => r.qty_per_receipt_mean === null ? <span className="subtle">–</span> : fmtQty(r.qty_per_receipt_mean) },
    { key: "pe", label: "Profil prévu", get: () => "", filter: "none", sortable: false, export: false, title: "Livraisons prévues par jour de la semaine (Lun → Dim)", render: (r) => <Profile shares={r.profile_expected} title="Livraisons prévues" /> },
    { key: "pr", label: "Profil reçu", get: () => "", filter: "none", sortable: false, export: false, title: "Réceptions par jour de la semaine (Lun → Dim)", render: (r) => <Profile shares={r.profile_receipts} title="Réceptions" /> },
  ];
  const supCols = useMemo<Column<LeadSupplier>[]>(() => [{ key: "supplier", label: "Fournisseur", get: (s) => `${s.supplier_id ?? ""} ${s.name}`, render: (s) => <SupplierLink id={s.supplier_id} name={s.name} /> }, ...base<LeadSupplier>()], [d?.band2_days]);   // eslint-disable-line react-hooks/exhaustive-deps
  const artCols = useMemo<Column<LeadArticle>[]>(() => [
    { key: "article", label: "Article", get: (a) => `${a.article_id} ${a.designation}`, exportGet: (a) => a.article_id, render: (a) => <ArticleLink id={a.article_id} designation={a.designation} /> },
    { key: "supplier", label: "Fournisseur", get: (a) => `${a.supplier_id ?? ""} ${a.supplier_name}`, render: (a) => <SupplierLink id={a.supplier_id} name={a.supplier_name} inline /> },
    ...base<LeadArticle>()], [d?.band2_days]);   // eslint-disable-line react-hooks/exhaustive-deps
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const t = d?.totals;
  return (
    <>
      <div className="grid kpis">
        <Kpi label="Retard moyen" icon={<Timer size={14} />} value={t ? days(t.delay_mean) : <Skeleton w={60} h={28} />} meta={t ? `${fmtInt(t.received)} jour(s) reçu(s)` : ""} tone={t && t.delay_mean !== null ? (t.delay_mean > 1 ? "critical" : t.delay_mean > 0 ? "warning" : "ok") : undefined} />
        <Kpi label="À l'heure" icon={<CheckCircle2 size={14} />} value={t ? pct(t.on_time_pct) : <Skeleton w={60} h={28} />} meta={t ? `${pct(t.early_pct)} en avance · ${pct(t.late_pct)} en retard` : ""} tone={ktone(t?.on_time_pct)} />
        <Kpi label="Biais du délai paramétré" icon={<CalendarRange size={14} />} value={t && t.bias_mean !== null ? days(t.bias_mean) : "–"} meta={t && t.lead_observed_mean !== null ? `délai observé ${t.lead_observed_mean} j ouvrés` : "date de création des commandes à venir"} tone={t && t.bias_mean !== null ? (t.bias_mean > 1 ? "critical" : t.bias_mean > 0 ? "warning" : "ok") : undefined} />
        <Kpi label="Urgences" icon={<Gauge size={14} />} value={t ? pct(t.urgent_pct) : <Skeleton w={60} h={28} />} meta="commandes créées dans le délai" />
        <Kpi label="Partielles" icon={<Layers size={14} />} value={t ? pct(t.partial_pct) : <Skeleton w={60} h={28} />} meta="jours servis en plusieurs réceptions" />
      </div>
      <Card flush title="Par fournisseur" hint="un fournisseur ou un article choisi ouvre le détail par article">
        {q.isLoading || !d ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : <div className="scroll-x"><DataTable rows={d.suppliers} columns={supCols} rowKey={(s) => s.supplier_id ?? "none"} compact maxHeight="50vh" storageKey="perf.lead.suppliers2" exportTitle="Délais observés par fournisseur" emptyTitle="Aucune livraison sur la période" /></div>}
      </Card>
      {d && d.articles.length > 0 && (
        <Card flush title="Par article">
          <div className="scroll-x"><DataTable rows={d.articles} columns={artCols} rowKey={(a) => `${a.supplier_id}|${a.article_id}`} compact maxHeight="50vh" storageKey="perf.lead.articles2" exportTitle="Délais observés par article" /></div>
        </Card>
      )}
    </>
  );
}

/* ---------------------------------------------------------------- ABC / XYZ */
function SegmentsTab() {
  const { perimeter, rights } = usePerimeter();
  const toast = useToast();
  const q = useSegmentation({ planner: perimeter.planner ?? undefined });
  const d = q.data;
  const [filter, setFilter] = useState<string>("");
  const rows = useMemo(() => (d?.rows ?? []).filter((r) => !filter || r.segment === filter), [d, filter]);
  const changes = rows.filter((r) => r.coverage_delta);
  const apply = useWrite((ids: string[]) => api.post<{ changed: unknown[]; skipped: unknown[] }>("/api/performance/segmentation/apply", { article_ids: ids, planner: perimeter.planner ?? null }),
    (r) => toast.push(`${r.changed.length} couverture(s) cible alignée(s) sur le segment${r.skipped.length ? ` · ${r.skipped.length} inchangée(s)` : ""}`, "success"));
  const cols = useMemo<Column<SegmentRow>[]>(() => [
    { key: "article", label: "Article", get: (r) => `${r.article_id} ${r.designation}`, exportGet: (r) => r.article_id, render: (r) => <ArticleLink id={r.article_id} designation={r.designation} /> },
    { key: "planner", label: "Approvisionneur", get: (r) => r.planner, filter: "select" },
    { key: "segment", label: "Segment", get: (r) => r.segment, filter: "select", render: (r) => <Badge tone={r.abc === "A" ? "critical" : r.abc === "B" ? "warning" : "neutral"}>{r.segment}</Badge> },
    { key: "value", label: "Valeur annuelle", get: (r) => r.value_year, num: true, export: "number", title: "Consommation des 52 dernières semaines × prix standard (ou besoin de l'horizon sans consommation)", render: (r) => r.value_year === null ? <span className="subtle" title="Article sans prix">–</span> : <span className="cell-2l"><span>{fmtEur(r.value_year)}</span><span className="sub">{r.value_share_pct} % · cumul {r.cum_share_pct} %{r.value_basis !== "consommation 52 semaines" ? " · sur besoin" : ""}</span></span> },
    { key: "qty", label: "Quantité / an", get: (r) => r.qty_year, num: true, render: (r) => fmtQty(r.qty_year, r.unit) },
    { key: "cv", label: "Variabilité (CV)", get: (r) => r.cv, num: true, export: "number", title: "Écart-type / moyenne de la consommation hebdomadaire depuis la première semaine consommée", render: (r) => r.cv === null ? <span className="subtle">–</span> : <>{r.cv}<span className="sub">{r.weeks_used} semaines</span></> },
    { key: "crit", label: "Criticité", get: (r) => r.criticality, filter: "select", render: (r) => <>{r.criticality}<span className="sub">{r.programs} programme(s){r.single_source ? " · mono-source" : ""}</span></> },
    { key: "cov", label: "Couverture cible", get: (r) => r.coverage_target_days, num: true, render: (r) => `${r.coverage_target_days} j` },
    { key: "sug", label: "Suggérée", get: (r) => r.suggested_coverage_days, num: true, export: "number", render: (r) => r.suggested_coverage_days === null ? <span className="subtle">–</span> : <b style={r.coverage_delta ? { color: r.coverage_delta > 0 ? "var(--warning-fg)" : "var(--ok-fg)" } : undefined}>{r.suggested_coverage_days} j{r.coverage_delta ? ` (${r.coverage_delta > 0 ? "+" : ""}${r.coverage_delta})` : ""}</b> },
    { key: "service", label: "Objectif de service", get: (r) => r.service_target_pct, num: true, export: "number", render: (r) => pct(r.service_target_pct) },
    { key: "lead", label: "Délai", get: (r) => r.lead_time_days, num: true, hidden: true, render: (r) => r.lead_time_days === null ? "–" : `${r.lead_time_days} j` },
  ], []);
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const grid = (abc: string, xyz: string) => d?.segments.find((s) => s.abc === abc && s.xyz === xyz);
  return (
    <>
      <div className="grid cols-3">
        <Card title="Matrice ABC × XYZ" hint={d ? `${d.valued} article(s) valorisé(s)${d.unvalued ? `, ${d.unvalued} sans prix` : ""} · A ≤ ${d.params.abc_a_pct} % de la valeur, B ≤ ${d.params.abc_b_pct} % · X : CV < ${d.params.xyz_x_cv}, Y < ${d.params.xyz_y_cv} · semaines ${fmtDate(d.from)} → ${fmtDate(d.to)}` : ""} className="span-2">
          {!d ? <SkeletonBlock rows={4} /> : (
            <div className="seg-grid">
              <div className="cell head" />{["X · régulier", "Y · variable", "Z · irrégulier"].map((h) => <div key={h} className="cell head">{h}</div>)}
              {["A", "B", "C"].map((abc) => (<>
                <div key={abc} className="cell head" title={`objectif de service ${d.params.service[abc] ?? "–"} %`}>{abc}<span className="sub">{d.params.service[abc] !== undefined ? `service ${d.params.service[abc]} %` : ""}</span></div>
                {["X", "Y", "Z"].map((xyz) => { const s = grid(abc, xyz); const seg = `${abc}${xyz}`; return (
                  <button key={seg} type="button" className={`cell ${filter === seg ? "active" : ""}`} style={filter === seg ? { outline: "2px solid var(--brand)" } : undefined} onClick={() => setFilter(filter === seg ? "" : seg)} title={`Filtrer le tableau sur ${seg}`}>
                    <b>{s ? fmtInt(s.articles) : 0} article(s)</b>
                    <span className="subtle">{s ? `${s.value_share_pct ?? 0} % de la valeur` : "–"}</span><br />
                    <span>cible suggérée <b style={{ display: "inline" }}>{d.params.coverage[seg] ?? "–"} j</b>{s?.changes ? <span style={{ color: "var(--warning-fg)" }}> · {s.changes} à ajuster</span> : null}</span>
                  </button>); })}
              </>))}
            </div>
          )}
        </Card>
        <Card title="Politique de stock par segment" hint="couvertures suggérées et objectifs de service : règles communes">
          <p className="small">La matrice suggère une <b>couverture cible</b> (jours ouvrés de besoin) par segment : courte pour un article cher et régulier (AX), longue pour un article peu coûteux et irrégulier (CZ). Les seuils A / B, X / Y et les neuf valeurs se règlent dans <Link to="/parametres">Paramètres › Règles communes</Link>. L'objectif de service par classe est une information pour les revues.</p>
          {d && <p className="small subtle">Valeur annuelle totale {fmtEur(d.total_value_year, true)}{d.unvalued ? ` · ${d.unvalued} article(s) sans prix classé(s) « – »` : ""}.</p>}
          {rights.canWrite && <Button size="sm" variant="primary" disabled={!changes.length || apply.isPending} onClick={() => { if (window.confirm(`Aligner la couverture cible de ${changes.length} article(s)${filter ? ` du segment ${filter}` : ""} sur la valeur suggérée ? (une seule action, annulable)`)) apply.mutate(changes.map((r) => r.article_id)); }}><Wand2 />Appliquer la suggestion ({changes.length})</Button>}
        </Card>
      </div>
      <Card flush title={`Articles${filter ? ` · segment ${filter}` : ""}`} hint="valeur annuelle, variabilité, criticité, couverture cible actuelle et suggérée ; une ligne ouvre la fiche article" actions={filter ? <Button size="sm" onClick={() => setFilter("")}>Tous les segments</Button> : undefined}>
        {!d ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : <DataTable rows={rows} columns={cols} rowKey={(r) => r.article_id} compact maxHeight="55vh" storageKey="perf.segments" exportTitle="Segmentation ABC XYZ" emptyTitle="Aucun article" />}
      </Card>
    </>
  );
}
