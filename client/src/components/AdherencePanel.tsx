import { useEffect, useMemo } from "react";
import { AlertTriangle, Lightbulb } from "lucide-react";
import { addDays, format as fmtRaw, parseISO, startOfISOWeek } from "date-fns";
import { api } from "@/lib/api";
import { useAdherence, usePerimeterLists, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { usePageState } from "@/lib/pageState";
import { ArticleLink, Badge, Button, Card, ErrorBox, Kpi, Skeleton, SkeletonBlock, useToast } from "@/components/ui";
import { DataTable, type Column } from "@/components/DataTable";
import { SearchSelect } from "@/components/SearchSelect";
import { WeekRangeBar, type WeekChip, type WeekRange } from "@/components/WeekRangeBar";
import { CONSUMPTION_SOURCE_SHORT, PLAN_ORIGIN_LABELS, WEEKDAY_SHORT, fmtDate, fmtDateTime, fmtInt, fmtQty, isoWeekKey } from "@/lib/format";
import type { AdherenceRow, PlanVersionWeek } from "@/lib/types";

/** Adherence of the reported consumption to the exploded PDP (tab of the Plan de production page). */
/** The past ISO weeks the adherence can look at : the 26 complete weeks before the current one. */
const ADHERENCE_WEEKS = 26;
function pastWeekChips(asOf: string | undefined): (WeekChip & { monday: string })[] {
  const thisMonday = startOfISOWeek(asOf ? parseISO(asOf) : new Date());
  return Array.from({ length: ADHERENCE_WEEKS }, (_, k) => {
    const monday = addDays(thisMonday, -7 * (ADHERENCE_WEEKS - k));
    const iso = fmtRaw(monday, "yyyy-MM-dd");
    return { key: isoWeekKey(iso), label: isoWeekKey(iso).replace(/^\d{4}-W/, "S"), start: fmtRaw(monday, "dd/MM"), monday: iso };
  });
}

export function AdherenceCard() {
  const { perimeter, config } = usePerimeter();
  const chips = useMemo(() => pastWeekChips(config?.as_of), [config?.as_of]);
  const [f, patch] = usePageState("parametres.adherence", {
    weekRange: { from: chips[ADHERENCE_WEEKS - 12].key, to: chips[ADHERENCE_WEEKS - 1].key } as WeekRange,
    bucket: "week" as "week" | "day", plan_mode: "monday" as "monday" | "latest", program: "",
  });
  // a remembered period that slid out of the window (weeks went by) falls back to the whole window
  const known = (k: string) => chips.some((c) => c.key === k);
  const range: WeekRange = f.weekRange && known(f.weekRange.from) && known(f.weekRange.to) ? f.weekRange : null;
  useEffect(() => { if (f.weekRange && !range) patch({ weekRange: null }); }, [f.weekRange, range, patch]);
  const first = chips.find((c) => c.key === (range?.from ?? chips[0].key)) ?? chips[0];
  const last = chips.find((c) => c.key === (range?.to ?? chips[ADHERENCE_WEEKS - 1].key)) ?? chips[ADHERENCE_WEEKS - 1];
  const lists = usePerimeterLists(perimeter.planner);
  const programOptions = useMemo(() => (lists.data?.programs ?? []).map((p) => ({ id: p.id, label: p.name || p.id, hint: p.name ? p.id : undefined })), [lists.data]);
  const q = useAdherence({ week_from: first.monday, week_to: last.monday, bucket: f.bucket, plan_mode: f.plan_mode, planner: perimeter.planner ?? undefined, program_id: f.program || undefined });
  const toast = useToast();
  const applySuggestion = useWrite(async (r: AdherenceRow) => {
    const rows = await api.get<Record<string, unknown>[]>("/api/reference/ref_articles/rows");
    const row = rows.find((x) => x.article_id === r.article_id);
    if (!row) throw new Error(`Article ${r.article_id} introuvable`);
    const { updated_by: _u, updated_at: _a, programs: _p, program_count: _c, supplier_count: _s, ...values } = row as Record<string, unknown>;
    return api.put("/api/reference/ref_articles/rows", { values: { ...values, demand_days: r.suggested_demand_days } });
  }, () => toast.push("Jours de consommation mis à jour", "success"));
  const d = q.data;
  const pct = (v: number | null) => v === null ? <span className="subtle">–</span> : <span className="row" style={{ gap: 6 }}><span className={`pct-bar ${v >= 85 ? "" : v >= 60 ? "warn" : "bad"}`} style={{ width: 70 }}><i style={{ width: `${Math.max(0, Math.min(100, v))}%` }} /></span><b>{v} %</b></span>;
  const cols = useMemo<Column<AdherenceRow>[]>(() => [
    { key: "article", label: "Article", get: (r) => `${r.article_id} ${r.designation}`, exportGet: (r) => r.article_id, render: (r) => <ArticleLink id={r.article_id} designation={r.designation} /> },
    { key: "planner", label: "Approvisionneur", get: (r) => r.planner, filter: "select" },
    { key: "source", label: "Conso", get: (r) => CONSUMPTION_SOURCE_SHORT[r.consumption_source], filter: "select", render: (r) => <Badge tone={r.consumption_source === "erp" ? "neutral" : "info"} title={r.consumption_source === "derived" ? "Consommation dérivée : le réel est Σ coef × sources" : undefined}>{CONSUMPTION_SOURCE_SHORT[r.consumption_source]}</Badge> },
    { key: "plan", label: "Plan (PDP éclaté)", get: (r) => r.plan, num: true, render: (r) => fmtQty(r.plan, r.unit) },
    { key: "real", label: "Réel", get: (r) => r.real, num: true, render: (r) => fmtQty(r.real, r.unit) },
    { key: "bias", label: "Biais", get: (r) => r.bias_pct, num: true, export: "percent", title: "(réel − plan) / plan : positif = on consomme plus que le plan", render: (r) => r.bias_pct === null ? <span className="subtle">–</span> : <span style={{ color: Math.abs(r.bias_pct) > 15 ? "var(--critical-fg)" : undefined }}>{r.bias_pct > 0 ? "+" : ""}{r.bias_pct} %</span> },
    { key: "net", label: "Adhérence nette", get: (r) => r.net_pct, num: true, export: "percent", title: "1 − |Σ réel − Σ plan| / Σ plan : le volume", render: (r) => pct(r.net_pct) },
    { key: "gross", label: "Adhérence brute", get: (r) => r.gross_pct, num: true, export: "percent", title: "1 − Σ par période |réel − plan| / Σ plan : le volume et le calage dans le temps", render: (r) => pct(r.gross_pct) },
    { key: "profile", label: "Profil réel (Lun → Ven)", get: (r) => r.weekday_share.map((x) => Math.round(x)).join(" "), filter: "none", sortable: false, render: (r) => <span className="weekday-mini" title={r.weekday_share.map((x, i) => `${WEEKDAY_SHORT[i]} ${Math.round(x)} %`).join(" · ")}>{r.weekday_share.map((x, i) => <i key={i} style={{ height: `${Math.max(1, (x / Math.max(1, ...r.weekday_share)) * 18)}px` }} />)}</span> },
    { key: "days", label: "Jours conso", get: (r) => r.demand_days, num: true, filter: "select" },
    { key: "suggest", label: "Suggestion", get: (r) => r.suggested_demand_days, num: true, title: "Premier jour où 75 % de la consommation réelle de la semaine est atteinte (jeudi / vendredi → 5)", render: (r) => r.suggested_demand_days === null ? <span className="subtle">–</span> : r.suggested_demand_days === r.demand_days ? <Badge tone="ok">{r.suggested_demand_days}</Badge> : <span className="row" style={{ gap: 4, justifyContent: "flex-end" }}><Badge tone="warning"><Lightbulb size={11} /> {r.suggested_demand_days}</Badge><Button size="xs" title={`Passer les jours de consommation de ${r.demand_days} à ${r.suggested_demand_days}`} onClick={() => applySuggestion.mutate(r)}>Appliquer</Button></span> },
    { key: "status", label: "Statut", get: (r) => r.status, filter: "select", render: (r) => r.status === "ok" ? <span className="subtle">–</span> : <Badge tone={r.status === "sans donnée" ? "neutral" : "warning"}>{r.status}</Badge> },
  ], [applySuggestion]);
  const t = d?.totals;
  return (
    <div className="stack">
      <Card flush>
        <WeekRangeBar weeks={chips} range={range} onChange={(weekRange) => patch({ weekRange })} />
        <div className="row wrap" style={{ padding: "8px 12px" }}>
          <label className="row small subtle">Période brute <select className="select sm" value={f.bucket} onChange={(e) => patch({ bucket: e.target.value as "week" | "day" })} aria-label="Granularité de l'adhérence brute"><option value="week">semaine</option><option value="day">jour</option></select></label>
          <label className="row small subtle">Plan de référence <select className="select sm" value={f.plan_mode} onChange={(e) => patch({ plan_mode: e.target.value as "monday" | "latest" })} aria-label="Plan de référence" title="Lundi : la version du PDP connue au lundi de chaque semaine (celle qu'a vue le CBN) ; actuel : le plan en vigueur aujourd'hui"><option value="monday">PDP du lundi</option><option value="latest">PDP actuel</option></select></label>
          <SearchSelect value={f.program} onChange={(program) => patch({ program })} options={programOptions} placeholder="Tous les programmes" ariaLabel="Programme" loading={lists.isLoading} />
          <span className="subtle small" style={{ marginLeft: "auto" }}>{d ? `${d.weeks} semaine(s), du ${fmtDate(d.from)} au ${fmtDate(d.to)}${d.excluded_plan_source.length ? ` · ${d.excluded_plan_source.length} article(s) en source PDP exclus` : ""}` : q.isFetching ? "calcul…" : ""}</span>
        </div>
        {d && <PlanVersionsStrip versions={d.plan_versions} mode={d.plan_mode} />}
      </Card>
      <div className="grid kpis">
        <Kpi label="Adhérence nette" value={t ? (t.net_pct === null ? "–" : `${t.net_pct} %`) : <Skeleton w={60} h={28} />} meta="1 − |Σ réel − Σ plan| / Σ plan, tous articles" tone={t && t.net_pct !== null ? (t.net_pct >= 85 ? "ok" : t.net_pct >= 60 ? "warning" : "critical") : undefined} />
        <Kpi label="Adhérence brute" value={t ? (t.gross_pct === null ? "–" : `${t.gross_pct} %`) : <Skeleton w={60} h={28} />} meta={`écarts par ${f.bucket === "week" ? "semaine" : "jour"}, en valeur absolue`} tone={t && t.gross_pct !== null ? (t.gross_pct >= 85 ? "ok" : t.gross_pct >= 60 ? "warning" : "critical") : undefined} />
        <Kpi label="Biais global" value={t ? (t.bias_pct === null ? "–" : `${t.bias_pct > 0 ? "+" : ""}${t.bias_pct} %`) : <Skeleton w={60} h={28} />} meta="réel − plan, en % du plan ; positif = surconsommation" tone={t && t.bias_pct !== null && Math.abs(t.bias_pct) > 15 ? "warning" : undefined} />
        <Kpi label="Plan sans conso" value={t ? fmtInt(t.plan_without_consumption) : <Skeleton w={40} h={28} />} meta="articles planifiés sans aucune consommation déclarée" tone={t && t.plan_without_consumption > 0 ? "warning" : "ok"} />
        <Kpi label="Suggestions" value={t ? fmtInt(t.suggestion_changes) : <Skeleton w={40} h={28} />} meta="articles dont le profil réel suggère d'autres jours de consommation" tone={t && t.suggestion_changes > 0 ? "info" : "ok"} />
      </div>
      <Card flush title="Adhérence par article" hint="plan = PDP éclaté par la nomenclature et profilé selon les jours de consommation ; réel = consommation déclarée (dérivée : Σ coef × sources)">
        {q.isError ? <ErrorBox error={q.error} /> : !d ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : <div className="scroll-x"><DataTable rows={d.rows} columns={cols} rowKey={(r) => r.article_id} compact maxHeight="calc(100vh - 520px)" storageKey="data.adherence" exportTitle="Adhérence PDP réel" emptyTitle="Aucun article dans le périmètre" /></div>}
      </Card>
    </div>
  );
}

/** Which PDP version stood for the plan of each week of the window, and a notice when a week had no
 * version imported before its Monday (the first later version holding it is taken) or no plan at all. */
function PlanVersionsStrip({ versions, mode }: { versions: PlanVersionWeek[]; mode: "monday" | "latest" }) {
  const later = versions.filter((v) => v.origin === "later");
  const none = versions.filter((v) => v.origin === "none");
  const names = (vs: PlanVersionWeek[]) => Array.from(new Set(vs.map((v) => v.version_name ?? "?"))).join(", ");
  return (
    <div className="stack" style={{ gap: 0 }}>
      {(later.length > 0 || none.length > 0) && (
        <div className={`banner ${none.length ? "warning" : "info"}`} style={{ margin: "0 12px 6px" }}>
          <AlertTriangle size={14} />
          <span>
            {later.length > 0 && <>{later.length} semaine{later.length > 1 ? "s" : ""} ({later.map((v) => v.week.replace(/^\d{4}-W/, "S")).join(", ")}) sans version du PDP importée avant le lundi : la première version importée ensuite qui les contient ({names(later)}) sert de plan.{" "}</>}
            {none.length > 0 && <>{none.length} semaine{none.length > 1 ? "s" : ""} ({none.map((v) => v.week.replace(/^\d{4}-W/, "S")).join(", ")}) sans aucun plan : aucune version importée ne contient ces semaines{mode === "monday" ? " (importez un PDP couvrant ces semaines, ou passez en « PDP actuel »)" : ""}.</>}
          </span>
        </div>
      )}
      <div className="plan-versions" role="list" aria-label="Version du PDP prise pour chaque semaine">
        {versions.map((v) => (
          <span key={v.week} role="listitem" className={`pv ${v.origin}`} title={`${v.week} · ${PLAN_ORIGIN_LABELS[v.origin]}${v.version_name ? ` : ${v.version_name}` : ""}${v.imported_at ? ` (importée le ${fmtDateTime(v.imported_at)})` : ""}`}>
            <b>{v.week.replace(/^\d{4}-W/, "S")}</b>
            <small>{v.version_name ?? PLAN_ORIGIN_LABELS[v.origin]}</small>
          </span>
        ))}
        <span className="pv-legend"><span><i style={{ borderColor: "var(--ok-fg)" }} />connue le lundi</span><span><i style={{ borderColor: "var(--warning-fg)", background: "var(--warning-bg)" }} />importée après coup</span><span><i style={{ borderColor: "var(--info-fg)" }} />plan ERP</span><span><i style={{ borderStyle: "dashed" }} />aucun plan</span></span>
      </div>
    </div>
  );
}
