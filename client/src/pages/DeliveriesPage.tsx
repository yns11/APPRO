import { useCallback, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { FileDown, Package, RotateCcw, Truck } from "lucide-react";
import { api } from "@/lib/api";
import { usePerimeter } from "@/state/PerimeterContext";
import { useRemembered } from "@/lib/pageState";
import { Button, Card, Empty, ErrorBox, Segmented, SkeletonBlock } from "@/components/ui";
import { WeekView } from "@/components/transports/WeekView";
import { DocksView } from "@/components/transports/DocksView";
import { LoadView } from "@/components/transports/LoadView";
import { TruckMenu, type MenuAt } from "@/components/transports/TruckMenu";
import { TransportDialog } from "@/components/transports/TransportDialog";
import { SpecialTruckDialog } from "@/components/transports/SpecialTruckDialog";
import { addDays, dayLabel, dayLong, isoWeekKey, localIso, mondayOf, weekShort } from "@/lib/transports";
import { fmtDate } from "@/lib/format";
import type { PlanningOrder, PlanningResponse, PlanningSupplier, SpecialKind, TransportOut } from "@/lib/types";

type View = "suppliers" | "docks";
type Scope = "mine" | "all" | "maf";
type Week = "this" | "next";
const VIEWS: { id: View; label: string }[] = [{ id: "suppliers", label: "Fournisseurs" }, { id: "docks", label: "Quais" }];
type Dialog = { kind: "orders"; suppliers: PlanningSupplier[]; date: string; initial: Set<string>; maf: boolean; pickDay?: boolean } | { kind: SpecialKind; date: string };

/**
 * « Planning transport » : two screens (Fournisseurs : suppliers × days, one truck per cell ; Quais : trucks per
 * dock and half-day over the week, or the agenda of one day), one period bar (this week, next week, the five days :
 * the horizon is these two weeks), three buttons to add a truck without orders (MAF shuttle, EV return, one-off
 * truck).  A click on a truck opens its menu where the click happened.
 */
export default function DeliveriesPage() {
  const { perimeter, config } = usePerimeter();
  const [view, setView] = useRemembered<View>("transports.view2", "suppliers");
  const [scope, setScope] = useRemembered<Scope>("transports.scope", "mine");
  const today = config?.as_of ?? localIso(new Date());
  const [week, setWeek] = useState<Week>("this");
  const [day, setDay] = useState<string | null>(null);        // a day of the chosen week (Quais : the agenda of that day)
  const monday = week === "this" ? mondayOf(today) : addDays(mondayOf(today), 7);
  const [menu, setMenu] = useState<MenuAt | null>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const planner = scope === "mine" ? perimeter.planner : null;
  const q = useQuery({ queryKey: ["transports-planning", scope, planner, monday], enabled: !!config,
    queryFn: () => api.get<PlanningResponse>("/api/transports/planning", { planner: planner ?? undefined, start: monday, weeks: 1, scope: scope === "maf" ? "maf" : "main" }) });
  const allQ = useQuery({ queryKey: ["transports-week", monday], enabled: !!config && view === "docks",
    queryFn: () => api.get<TransportOut[]>("/api/transports", { date_from: monday, date_to: addDays(monday, 6) }) });
  // the MAF shuttle from the header button : the MAF orders of the two weeks
  const mafQ = useQuery({ queryKey: ["transports-planning", "maf-all", mondayOf(today)], enabled: !!config && dialog?.kind === "orders" && dialog.maf && !!dialog.pickDay,
    queryFn: () => api.get<PlanningResponse>("/api/transports/planning", { start: mondayOf(today), weeks: 2, scope: "maf" }) });
  const d = q.data;
  const all = useMemo(() => [...(allQ.data ?? []), ...(d?.transports ?? []).filter((t) => !(allQ.data ?? []).some((x) => x.transport_id === t.transport_id))], [allQ.data, d]);
  const closeMenu = useCallback(() => setMenu(null), []);
  const openMenu = (t: TransportOut, e: React.MouseEvent<HTMLButtonElement>) => setMenu((m) => (m?.t.transport_id === t.transport_id ? null : { t, x: e.clientX, y: e.clientY }));
  const weekDays = useMemo(() => Array.from({ length: 5 }, (_, i) => addDays(monday, i)), [monday]);
  const working = d?.days ?? weekDays;
  const dayShown = day && working.includes(day) ? day : null;
  const pickWeek = (w: Week) => { setWeek(w); setDay(null); setMenu(null); };
  const pickDay = (x: string) => { setDay((cur) => (cur === x ? null : x)); setMenu(null); };
  const defaultDay = dayShown ?? (working.find((x) => x >= today) ?? working[0] ?? today);

  if (config && !config.access?.can_transports) return <div className="page"><Empty title="Planning transport : accès réservé" hint="Pendant l'essai des camions automatiques, le planning n'est ouvert qu'aux utilisateurs désignés." /></div>;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  return (
    <div className="page tp-page">
      <div className="tp-head">
        <div className="row" style={{ gap: 10 }}>
          <h1>Planning transport</h1>
          <Segmented<View> size="sm" options={VIEWS} value={view} onChange={(v) => { setView(v); setMenu(null); if (v === "suppliers") setDay(null); }} />
        </div>
        <div className="tp-days" role="group" aria-label="Période">
          <button type="button" className={`btn sm ${week === "this" && !dayShown ? "on" : ""}`} aria-pressed={week === "this" && !dayShown} onClick={() => pickWeek("this")}>Cette semaine</button>
          <button type="button" className={`btn sm ${week === "next" && !dayShown ? "on" : ""}`} aria-pressed={week === "next" && !dayShown} onClick={() => pickWeek("next")}>Semaine prochaine</button>
          <span className="sep" />
          {weekDays.map((x) => {
            const off = view === "suppliers" || !working.includes(x);
            return <button key={x} type="button" className={`btn sm ${dayShown === x ? "on" : ""} ${x === today ? "today" : ""}`} aria-pressed={dayShown === x} disabled={off}
              title={view === "suppliers" ? "Les jours s'ouvrent dans l'écran Quais (agenda de la journée)" : !working.includes(x) ? "Jour non ouvré" : dayLong(x)} onClick={() => pickDay(x)}>{dayLabel(x).slice(0, 6)}</button>;
          })}
        </div>
        <div className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
          <Button size="sm" title="Navette MAF Hambach : commandes de plusieurs fournisseurs MAF dans un même camion" onClick={() => setDialog({ kind: "orders", maf: true, pickDay: true, suppliers: [], date: defaultDay, initial: new Set() })}><Truck />Navette MAF</Button>
          <Button size="sm" title="Retour EV : le fournisseur vient enlever ses emballages vides (EM…EV)" onClick={() => setDialog({ kind: "ev", date: defaultDay })}><RotateCcw />Retour EV</Button>
          <Button size="sm" title="Camion ponctuel : tout autre camion hors commandes (échantillons, retour de pièces…)" onClick={() => setDialog({ kind: "ponctuel", date: defaultDay })}><Package />Camion ponctuel</Button>
        </div>
      </div>

      {!d ? <Card flush><div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div></Card> : (
        <Card flush className="tp-main">
          <div className="tp-title">
            <b>{dayShown ? dayLong(dayShown) : `${weekShort(isoWeekKey(monday))} · ${fmtDate(monday, "dd/MM")} → ${fmtDate(addDays(monday, 4), "dd/MM")}`}</b>
            <span className="subtle">{view === "suppliers" ? "un camion par case · cliquez un camion pour ses actions" : dayShown ? "agenda des quais : un bloc = un camion" : "camions par quai et demi-journée · cliquez une case pour la journée"}</span>
            <span className="row" style={{ marginLeft: "auto", gap: 6 }}>
              <select className="select sm" value={scope} onChange={(e) => { setScope(e.target.value as Scope); setMenu(null); }} aria-label="Périmètre">
                <option value="mine">{perimeter.planner ? `Carnet ${perimeter.planner}` : "Tous les fournisseurs"}</option>
                {perimeter.planner && <option value="all">Tous les fournisseurs</option>}
                <option value="maf">Planning MAF</option>
              </select>
              <a className="btn sm ghost" href={api.downloadUrl("/api/transports/atr.csv")} download title="CSV pour l'outil de transport ATR : tous les transports prévus à partir d'aujourd'hui"><FileDown />CSV ATR</a>
            </span>
          </div>
          {view === "suppliers" && <WeekView d={d} selected={menu?.t.transport_id ?? null} onSelect={openMenu} onCreate={(s, date, orders) => setDialog({ kind: "orders", maf: scope === "maf", suppliers: scope === "maf" ? d.suppliers : [s], date, initial: new Set(orders.map((o: PlanningOrder) => o.order_id)) })} />}
          {view === "docks" && (allQ.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div>
            : dayShown ? <DocksView d={d} all={all} day={dayShown} selected={menu?.t.transport_id ?? null} onSelect={openMenu} now={config?.as_of === localIso(new Date()) ? `${today}T${new Date().toTimeString().slice(0, 5)}` : undefined} />
              : <LoadView d={d} all={all} onPick={(x) => setDay(x)} />)}
          <div className="tp-legend">
            <span><i className="plan" /> prévu</span><span><i className="check" /> à vérifier</span><span><i className="ghost" /> à créer (fournisseur en manuel)</span>
            <span style={{ marginLeft: "auto" }}>{view === "docks" && !dayShown ? `${d.capacity} camion${d.capacity > 1 ? "s" : ""} par quai et par créneau · une case = une demi-journée` : "horizon : cette semaine et la suivante"}</span>
          </div>
        </Card>
      )}

      {d && <TruckMenu menu={menu} d={d} onClose={closeMenu} />}
      {dialog?.kind === "orders" && d && (!dialog.pickDay || mafQ.data) && (
        <TransportDialog maf={dialog.maf} suppliers={dialog.pickDay ? mafQ.data!.suppliers : dialog.suppliers} date={dialog.date} initial={dialog.initial} docks={d.docks} slots={d.slots}
          pickDay={dialog.pickDay} days={dialog.pickDay ? mafQ.data!.days.filter((x) => x >= today) : undefined} onClose={() => setDialog(null)} onCreated={() => setDialog(null)} />)}
      {(dialog?.kind === "ev" || dialog?.kind === "ponctuel") && d && <SpecialTruckDialog kind={dialog.kind} date={dialog.date} docks={d.docks} slots={d.slots} onClose={() => setDialog(null)} />}
    </div>
  );
}
