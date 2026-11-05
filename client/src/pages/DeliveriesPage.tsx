import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { FileDown, Mail } from "lucide-react";
import { api } from "@/lib/api";
import { usePerimeter } from "@/state/PerimeterContext";
import { useRemembered } from "@/lib/pageState";
import { Button, Card, Empty, ErrorBox, Segmented, SkeletonBlock } from "@/components/ui";
import { WeekView } from "@/components/transports/WeekView";
import { DocksView } from "@/components/transports/DocksView";
import { LoadView } from "@/components/transports/LoadView";
import { TruckPanel } from "@/components/transports/TruckPanel";
import { TransportDialog } from "@/components/transports/TransportDialog";
import { usePlanSender } from "@/components/transports/PlanSender";
import { addDays, dayLong, isoWeekKey, localIso, mondayOf, sentKeysOf, weekShort } from "@/lib/transports";
import { WEEKDAY_LONG, fmtDate } from "@/lib/format";
import type { PlanOut, PlanningOrder, PlanningResponse, PlanningSupplier, PlansOut, TransportOut } from "@/lib/types";

type View = "week" | "docks" | "load";
type Scope = "mine" | "all" | "maf";
const VIEWS: { id: View; label: string }[] = [{ id: "week", label: "Semaine" }, { id: "docks", label: "Quais" }, { id: "load", label: "Charge" }];

/**
 * « Planning transport » : one header (view, period, perimeter), one line for the weekly plan, three views sharing
 * the same colours.  Semaine : suppliers × days, one truck per cell, the detail of the selected truck at the right.
 * Quais : the agenda of one day, docks × slots.  Charge : trucks per dock and half-day over the week.
 */
export default function DeliveriesPage() {
  const { perimeter, config } = usePerimeter();
  const [view, setView] = useRemembered<View>("transports.view", "week");
  const [scope, setScope] = useRemembered<Scope>("transports.scope", "mine");
  const today = config?.as_of ?? localIso(new Date());
  // the period follows the application's « today » once the config is known ; a navigation then takes over
  const [mondayPicked, setMonday] = useState<string | null>(null);
  const [dayPicked, setDay] = useState<string | null>(null);
  const monday = mondayPicked ?? mondayOf(today);
  const day = dayPicked ?? today;
  const [selected, setSelected] = useState<string | null>(null);
  const [dialog, setDialog] = useState<{ suppliers: PlanningSupplier[]; date: string; initial: Set<string> } | null>(null);
  const planner = scope === "mine" ? perimeter.planner : null;
  const q = useQuery({ queryKey: ["transports-planning", scope, planner, monday], enabled: !!config,
    queryFn: () => api.get<PlanningResponse>("/api/transports/planning", { planner: planner ?? undefined, start: monday, weeks: 1, scope: scope === "maf" ? "maf" : "main" }) });
  const plansQ = useQuery({ queryKey: ["transports-plans", perimeter.planner], enabled: !!config, queryFn: () => api.get<PlansOut>("/api/transports/plans", { planner: perimeter.planner ?? undefined }) });
  const allQ = useQuery({ queryKey: ["transports-week", monday], enabled: !!config && view !== "week",
    queryFn: () => api.get<TransportOut[]>("/api/transports", { date_from: monday, date_to: addDays(monday, 6) }) });
  const sender = usePlanSender();
  const d = q.data;
  const plans = plansQ.data;
  const sent = useMemo(() => sentKeysOf(plans), [plans]);
  const all = useMemo(() => [...(allQ.data ?? []), ...(d?.transports ?? []).filter((t) => !(allQ.data ?? []).some((x) => x.transport_id === t.transport_id))], [allQ.data, d]);
  const selectedTruck = useMemo(() => all.find((t) => t.transport_id === selected) ?? d?.transports.find((t) => t.transport_id === selected) ?? null, [all, d, selected]);
  const goWeek = (n: number) => { const m = addDays(monday, 7 * n); setMonday(m); setDay(n > 0 ? m : addDays(m, 4)); setSelected(null); };
  const goDay = (n: number) => { let x = addDays(day, n); while (new Date(`${x}T12:00:00`).getDay() % 6 === 0) x = addDays(x, n); setDay(x); if (mondayOf(x) !== monday) setMonday(mondayOf(x)); setSelected(null); };
  const goToday = () => { setMonday(null); setDay(null); };
  const days = d?.days ?? [];
  const dayShown = days.includes(day) ? day : days[0] ?? day;

  if (config && !config.access?.can_transports) return <div className="page"><Empty title="Planning transport : accès réservé" hint="Pendant l'essai des camions automatiques, le planning n'est ouvert qu'aux utilisateurs désignés." /></div>;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  return (
    <div className="page tp-page">
      <div className="tp-head">
        <div className="row" style={{ gap: 14 }}>
          <h1 style={{ margin: 0 }}>Planning transport</h1>
          <Segmented<View> size="sm" options={VIEWS} value={view} onChange={(v) => { setView(v); setSelected(null); }} />
        </div>
        <div className="row" style={{ gap: 6 }}>
          {view === "docks" ? <>
            <Button size="sm" aria-label="Jour précédent" onClick={() => goDay(-1)}>‹</Button>
            <span className="tp-period">{dayLong(dayShown)}</span>
            <Button size="sm" aria-label="Jour suivant" onClick={() => goDay(1)}>›</Button>
          </> : <>
            <Button size="sm" aria-label="Semaine précédente" onClick={() => goWeek(-1)}>‹</Button>
            <span className="tp-period">{weekShort(isoWeekKey(monday))} · {fmtDate(monday, "dd/MM")} → {fmtDate(addDays(monday, 4), "dd/MM")}</span>
            <Button size="sm" aria-label="Semaine suivante" onClick={() => goWeek(1)}>›</Button>
          </>}
          <Button size="sm" variant="ghost" onClick={goToday}>Aujourd'hui</Button>
          <select className="select sm" value={scope} onChange={(e) => { setScope(e.target.value as Scope); setSelected(null); }} aria-label="Périmètre">
            <option value="mine">{perimeter.planner ? `Carnet ${perimeter.planner}` : "Tous les fournisseurs"}</option>
            {perimeter.planner && <option value="all">Tous les fournisseurs</option>}
            <option value="maf">Planning MAF</option>
          </select>
          <a className="btn sm" href={api.downloadUrl("/api/transports/atr.csv")} download title="CSV pour l'outil de transport : tous les transports prévus à partir d'aujourd'hui"><FileDown />CSV ATR</a>
        </div>
      </div>

      {plans && <PlanLine plans={plans} onSend={sender.send} />}

      {!d ? <Card flush><div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div></Card> : (
        <div className="tp-body">
          <Card flush className="tp-main">
            {view === "week" && <WeekView d={d} sent={sent} selected={selected} onSelect={(id) => setSelected((s) => (s === id ? null : id))} onCreate={(s, date, orders) => setDialog({ suppliers: scope === "maf" ? d.suppliers : [s], date, initial: new Set(orders.map((o: PlanningOrder) => o.order_id)) })} />}
            {view === "docks" && (allQ.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div> : <DocksView d={d} all={all} day={dayShown} sent={sent} selected={selected} onSelect={(id) => setSelected((s) => (s === id ? null : id))} now={config?.as_of === localIso(new Date()) ? `${today}T${new Date().toTimeString().slice(0, 5)}` : undefined} />)}
            {view === "load" && (allQ.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock rows={5} /></div> : <LoadView d={d} all={all} onPick={(x) => { setDay(x); setView("docks"); }} />)}
            <div className="tp-legend">
              <span><i className="plan" /> à envoyer</span><span><i className="sent" /> envoyé</span><span><i className="resend" /> ✱ changé</span><span><i className="check" /> à vérifier</span><span><i className="off" /> annulé</span>
              {view === "load" && <span style={{ marginLeft: "auto" }}>{d.capacity} camion{d.capacity > 1 ? "s" : ""} par quai et par créneau · une case = une demi-journée</span>}
            </div>
          </Card>
          {selectedTruck && <TruckPanel t={selectedTruck} d={d} plans={plans} sent={sent} onClose={() => setSelected(null)} />}
        </div>
      )}

      {dialog && d && <TransportDialog maf={scope === "maf"} suppliers={dialog.suppliers} date={dialog.date} initial={dialog.initial} docks={d.docks} slots={d.slots} onClose={() => setDialog(null)} onCreated={() => setDialog(null)} />}
      {sender.drawer}
    </div>
  );
}

/** One line : the plan of the next week, three counts, one button ; the full list is the Envois page. */
function PlanLine({ plans, onSend }: { plans: PlansOut; onSend: (p: PlanOut) => void }) {
  const next = plans.plans.filter((p) => p.week === plans.next_week);
  const toSend = next.filter((p) => p.state === "a_envoyer");
  const resend = plans.plans.filter((p) => p.state === "a_renvoyer");
  const first = resend[0] ?? toSend[0];
  const dayName = WEEKDAY_LONG[(plans.plan_weekday + 6) % 7] ?? "jeudi";
  return (
    <div className="tp-plan" role="region" aria-label="Plan de semaine">
      <b>Plan {weekShort(plans.next_week)}</b>
      <span className="row" style={{ gap: 12 }}>
        <span><i className="dot sent" /> {next.filter((p) => p.state !== "a_envoyer").length} envoyé{next.filter((p) => p.state !== "a_envoyer").length > 1 ? "s" : ""}</span>
        <span><i className="dot plan" /> {toSend.length} à envoyer {dayName}</span>
        <span><i className="dot resend" /> {resend.length} à renvoyer</span>
      </span>
      <span style={{ marginLeft: "auto" }} className="row">
        {first && <Button size="sm" variant="primary" onClick={() => onSend(first)} title={`${first.state === "a_renvoyer" ? "Renvoyer" : "Envoyer"} ${first.name} : le plan complet s'ouvre dans Outlook`}><Mail />{first.state === "a_renvoyer" ? "Renvoyer" : "Envoyer"} · {first.name}</Button>}
        <Link to="/livraisons/envois" className="btn sm ghost">Tous les envois</Link>
      </span>
    </div>
  );
}
