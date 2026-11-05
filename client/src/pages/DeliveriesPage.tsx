import { useCallback, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeftRight, FileDown, Printer, Truck } from "lucide-react";
import { api } from "@/lib/api";
import { usePerimeter } from "@/state/PerimeterContext";
import { Button, Card, Empty, ErrorBox, SkeletonBlock } from "@/components/ui";
import { WeekBoard } from "@/components/transports/WeekBoard";
import { DayAgenda } from "@/components/transports/DayAgenda";
import { TruckMenu, type MenuAt } from "@/components/transports/TruckMenu";
import { ShuttleDialog } from "@/components/transports/ShuttleDialog";
import { SpecialTruckDialog } from "@/components/transports/SpecialTruckDialog";
import { KindIcon, PalletIcon } from "@/components/transports/icons";
import { addDays, dayLong, dayShort, isoWeekKey, localIso, mondayOf, weekShort } from "@/lib/transports";
import { fmtDate } from "@/lib/format";
import type { BoardResponse, SpecialKind, TransportOut } from "@/lib/types";

type Dialog = { kind: "maf" } | { kind: SpecialKind; date: string };

/**
 * « Planning transport » : one screen, the docks.  Twelve period buttons (this week and its days, next week and its
 * days : the horizon) ; the week shows how many trucks come per dock and day (yours in blue), a day shows the agenda of
 * the docks.  Every supplier is automatic : the three buttons on the right add the trucks without supplier orders (MAF
 * shuttle, EV return, one-off truck) ; the printer gives the PDF of the two weeks, the arrow the CSV for the transport
 * tool.  A click on a truck opens its menu where the click happened.
 */
export default function DeliveriesPage() {
  const { config } = usePerimeter();
  const today = config?.as_of ?? localIso(new Date());
  const monday = mondayOf(today);
  const [week, setWeek] = useState<0 | 1>(0);
  const [day, setDay] = useState<string | null>(null);
  const [menu, setMenu] = useState<MenuAt | null>(null);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const q = useQuery({ queryKey: ["transports-board", monday], enabled: !!config, refetchInterval: 120_000,
    queryFn: () => api.get<BoardResponse>("/api/transports/board", { start: monday, weeks: 2 }) });
  const d = q.data;
  const weeks = useMemo(() => [0, 1].map((i) => { const m = addDays(monday, 7 * i); return { monday: m, days: (d?.days ?? []).filter((x) => x >= m && x < addDays(m, 7)) }; }), [d, monday]);
  const shown = weeks[week];
  const dayShown = day && shown.days.includes(day) ? day : null;
  const closeMenu = useCallback(() => setMenu(null), []);
  const openMenu = (t: TransportOut, e: React.MouseEvent<HTMLButtonElement>) => setMenu((m) => (m?.t.transport_id === t.transport_id ? null : { t, x: e.clientX, y: e.clientY }));
  const pickWeek = (w: 0 | 1) => { setWeek(w); setDay(null); setMenu(null); };
  const pickDay = (w: 0 | 1, x: string) => { setWeek(w); setDay((cur) => (cur === x ? null : x)); setMenu(null); };
  const planned = (d?.transports ?? []).filter((t) => t.status === "planned");
  const inView = planned.filter((t) => (dayShown ? t.date === dayShown : shown.days.includes(t.date)));
  const mine = inView.filter((t) => t.mine).length;
  const canWrite = !!config?.access?.can_write;
  const defaultDay = dayShown ?? (shown.days.find((x) => x >= today) ?? d?.maf_day ?? today);

  if (config && !config.access?.can_transports) return <div className="page"><Empty title="Planning transport : accès réservé" hint="Pendant l'essai des camions automatiques, le planning n'est ouvert qu'aux utilisateurs désignés." /></div>;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  return (
    <div className="page tp-page">
      <div className="tp-head">
        <h1>Planning transport</h1>
        <div className="row" style={{ gap: 6, justifyContent: "flex-end" }}>
          <Button size="sm" disabled={!d || !canWrite} title={d?.maf_day ? `Navette MAF : ${d.maf_pending} commande(s) MAF du ${fmtDate(d.maf_day)} encore sans navette` : "Navette MAF Hambach"} onClick={() => setDialog({ kind: "maf" })}><ArrowLeftRight />Navette MAF{d && d.maf_pending > 0 && <span className="tp-badge">{d.maf_pending}</span>}</Button>
          <Button size="sm" disabled={!d || !canWrite} title="Retour EV : le fournisseur vient enlever ses emballages vides (EM…EV)" onClick={() => setDialog({ kind: "ev", date: defaultDay })}><PalletIcon width={14} height={14} />Retour EV</Button>
          <Button size="sm" disabled={!d || !canWrite} title="Camion ponctuel : tout autre camion hors commandes (échantillons, retour de pièces…)" onClick={() => setDialog({ kind: "ponctuel", date: defaultDay })}><Truck />Camion ponctuel</Button>
          <span className="tp-sep" />
          <a className="btn sm icon" href={api.downloadUrl("/api/transports/planning.pdf")} target="_blank" rel="noreferrer" title="PDF imprimable : cette semaine, ses jours, la semaine prochaine, ses jours" aria-label="PDF imprimable du planning"><Printer /></a>
          <a className="btn sm icon" href={api.downloadUrl("/api/transports/atr.csv")} download title="CSV pour l'outil de transport ATR : tous les transports prévus à partir d'aujourd'hui" aria-label="CSV ATR"><FileDown /></a>
        </div>
      </div>
      <div className="tp-periods">
        {weeks.map((w, i) => (
          <div key={w.monday} className="tp-days" role="group" aria-label={i === 0 ? "Cette semaine" : "Semaine prochaine"}>
            <button type="button" className={`btn sm wk ${week === i && !dayShown ? "on" : ""}`} aria-pressed={week === i && !dayShown} onClick={() => pickWeek(i as 0 | 1)}>{i === 0 ? "Cette semaine" : "Semaine prochaine"}</button>
            {Array.from({ length: 5 }, (_, k) => addDays(w.monday, k)).map((x) => {
              const working = !d || w.days.includes(x);
              return <button key={x} type="button" className={`btn sm ${dayShown === x ? "on" : ""} ${x === today ? "today" : ""}`} aria-pressed={dayShown === x} disabled={!working} title={working ? dayLong(x) : "Jour non ouvré"} onClick={() => pickDay(i as 0 | 1, x)}>{dayShort(x)}</button>;
            })}
          </div>
        ))}
      </div>

      {!d ? <Card flush><div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div></Card> : (
        <Card flush className="tp-main">
          <div className="tp-title">
            <b>{dayShown ? dayLong(dayShown) : `${weekShort(isoWeekKey(shown.monday))} · ${fmtDate(shown.monday, "dd/MM")} → ${fmtDate(addDays(shown.monday, 4), "dd/MM")}`}</b>
            <span className="subtle">{dayShown ? `agenda des quais · un bloc = un camion · ${d.capacity} camion${d.capacity > 1 ? "s" : ""} par quai et par créneau, ${d.hard_max} au plus en forçant · le retour d'une navette n'occupe pas de créneau` : "camions par quai et par jour · les vôtres en bleu · un jour pour l'agenda, un camion pour ses actions"}</span>
            <span style={{ marginLeft: "auto", fontWeight: 600 }}>{inView.length} camion{inView.length > 1 ? "s" : ""} {dayShown ? "" : "cette semaine"}{mine ? ` · ${mine} à vous` : ""}</span>
          </div>
          {dayShown
            ? <DayAgenda d={d} day={dayShown} selected={menu?.t.transport_id ?? null} onSelect={openMenu} now={config?.as_of === localIso(new Date()) ? `${today}T${new Date().toTimeString().slice(0, 5)}` : undefined} />
            : <WeekBoard d={d} days={shown.days} selected={menu?.t.transport_id ?? null} onSelect={openMenu} onDay={(x) => pickDay(week, x)} />}
          <div className="tp-legend">
            <span><i className="mine" /> vos camions et les navettes</span><span><i className="check" /> vos camions à vérifier</span><span><i className="other" /> autres approvisionneurs (point rouge : à vérifier chez eux)</span>
            {dayShown && <><span><i className="off" /> annulé</span><span><i className="forced" /> créneau forcé</span></>}
            <span><KindIcon t={{ mode: "maf" }} /> navette MAF</span><span><KindIcon t={{ mode: "ev" }} /> retour EV</span><span><KindIcon t={{ mode: "ponctuel" }} /> camion ponctuel</span>
            <span style={{ marginLeft: "auto" }}>horizon : cette semaine et la suivante</span>
          </div>
        </Card>
      )}

      {d && <TruckMenu menu={menu} d={d} onClose={closeMenu} />}
      {dialog?.kind === "maf" && d && <ShuttleDialog d={d} onClose={() => setDialog(null)} />}
      {(dialog?.kind === "ev" || dialog?.kind === "ponctuel") && d && <SpecialTruckDialog kind={dialog.kind} d={d} date={dialog.date} onClose={() => setDialog(null)} />}
    </div>
  );
}
