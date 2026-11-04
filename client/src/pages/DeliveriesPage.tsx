import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, Download, Eye, FileDown, LayoutGrid, Mail, MoveRight, Pin, Plus, Scissors, Send, StickyNote, Trash2, Truck, X } from "lucide-react";
import { api } from "@/lib/api";
import { useInvalidateAll, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { Badge, Button, Card, Drawer, Empty, ErrorBox, SkeletonBlock, SupplierLink, Tabs, useToast } from "@/components/ui";
import { WEEKDAY_LONG, WEEKDAY_SHORT, fmtDate, fmtDateTime, fmtQty } from "@/lib/format";
import { DockPicker } from "@/components/DockPicker";
import { DataTable, type Column } from "@/components/DataTable";
import { WeekRangeBar, type WeekChip, type WeekRange } from "@/components/WeekRangeBar";
import type { DockDayOut, PlanMailOut, PlanMailPart, PlanOut, PlanningOrder, PlanningResponse, PlanningSupplier, PlansOut, TransportOut, TransportPackOut, TransportSlotIn } from "@/lib/types";

const DEFAULT_TIME = "08:00";
const WINDOW_WEEKS = 10;          // one past week, the current one and eight to come
type Scope = "main" | "maf";
const TABS: { id: Scope; label: string }[] = [{ id: "main", label: "Planning de livraisons" }, { id: "maf", label: "Planning MAF" }];
const STATE_LABEL: Record<PlanOut["state"], string> = { a_envoyer: "à envoyer", envoyee: "envoyée", a_renvoyer: "à renvoyer" };

/** ``YYYY-MM-DD`` of a local date (never ``toISOString`` : it converts to UTC and shifts the day in Europe/Paris). */
export function localIso(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}
/** Monday of the ISO week of ``iso`` (civil date arithmetic, same result in every time zone). */
export function mondayOf(iso: string): string {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() - ((d.getDay() + 6) % 7));
  return localIso(d);
}
export function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T12:00:00`);
  d.setDate(d.getDate() + n);
  return localIso(d);
}
function isoWeek(monday: string): { year: number; week: number } {
  const d = new Date(`${monday}T12:00:00`);
  const thursday = new Date(d); thursday.setDate(d.getDate() + 3);
  const jan4 = new Date(thursday.getFullYear(), 0, 4);
  const week = 1 + Math.round(((thursday.getTime() - jan4.getTime()) / 86400000 - 3 + ((jan4.getDay() + 6) % 7)) / 7);
  return { year: thursday.getFullYear(), week };
}
function isoWeekLabel(monday: string): string { return `S${String(isoWeek(monday).week).padStart(2, "0")}`; }
/** ``2026-W46`` of the ISO week of a Monday (the key of the server's weekly plans). */
function isoWeekKey(monday: string): string { const w = isoWeek(monday); return `${w.year}-W${String(w.week).padStart(2, "0")}`; }
/** Outlook opens the pre-filled message ; the planner reads it and sends it (``;`` between recipients, as Outlook expects). */
export function mailtoOf(mail: { to: string[]; subject: string; body: string }): string {
  return `mailto:${mail.to.join(";")}?subject=${encodeURIComponent(mail.subject)}&body=${encodeURIComponent(mail.body)}`;
}
const csvUrl = (ids: string[]) => `/api/transports/atr.csv?${ids.map((id) => `transport_id=${encodeURIComponent(id)}`).join("&")}`;
const dayLabel = (iso: string) => `${WEEKDAY_SHORT[(new Date(`${iso}T12:00:00`).getDay() + 6) % 7]} ${fmtDate(iso, "dd/MM")}`;

/**
 * « Planning de livraisons » : suppliers in rows (those with a firm order in the shown weeks), working days in
 * columns, the firm ERP orders of each day in the cells.  A supplier in **Camions automatiques** gets his trucks from
 * the application (one per day, slot on a dock) : the cell shows them, red when « à vérifier », ✱ when changed since
 * the week was sent ; a click opens the truck menu (split, move, note, cancel, seen, CSV, e-mail).  The other
 * suppliers keep the manual creation (tick the orders, « Créer le transport »).  The band above the weeks drives the
 * **weekly plan** : one complete e-mail per recipient and week, sent the configured weekday for the next week, re-sent
 * when a sent week changes.  Two tabs : the main planning (the planner's perimeter) and the « Planning MAF ».
 */
export default function DeliveriesPage() {
  const { perimeter, config } = usePerimeter();
  const [scope, setScope] = useState<Scope>("main");
  const start = config ? addDays(mondayOf(config.as_of), -7) : null;
  const planner = scope === "main" ? perimeter.planner : null;
  const q = useQuery({ queryKey: ["transports-planning", scope, planner, start], enabled: !!start,
    queryFn: () => api.get<PlanningResponse>("/api/transports/planning", { planner: planner ?? undefined, start, weeks: WINDOW_WEEKS, scope }) });
  const plansQ = useQuery({ queryKey: ["transports-plans", perimeter.planner], enabled: scope === "main",
    queryFn: () => api.get<PlansOut>("/api/transports/plans", { planner: perimeter.planner ?? undefined }) });
  const d = q.data;
  const plans = plansQ.data;
  const weeks = useMemo<WeekChip[]>(() => {
    if (!d) return [];
    const current = mondayOf(d.as_of);
    const keys = Array.from(new Set(d.days.map(mondayOf)));
    return keys.map((k) => {
      const wk = isoWeekKey(k);
      const mine = (plans?.plans ?? []).filter((p) => p.week === wk);
      const tone = !mine.length ? undefined : mine.some((p) => p.state === "a_renvoyer") ? "resend" : mine.every((p) => p.state === "envoyee") ? "sent" : "pending";
      const hint = !mine.length ? undefined : tone === "sent" ? `plan envoyé à ${mine.length} destinataire(s)` : tone === "resend" ? `${mine.filter((p) => p.state === "a_renvoyer").length} rectificatif(s) à renvoyer` : `plan à envoyer (${mine.filter((p) => p.state === "a_envoyer").length} destinataire(s))`;
      return { key: k, label: isoWeekLabel(k), start: fmtDate(k, "dd/MM"), current: k === current, past: k < current, tone, hint };
    });
  }, [d, plans]);
  const [range, setRange] = useState<WeekRange>(null);
  const shown = useMemo(() => {
    if (!d) return [] as string[];
    const current = mondayOf(d.as_of);
    const r = range ?? { from: current, to: addDays(current, 7) };
    return d.days.filter((day) => { const m = mondayOf(day); return m >= r.from && m <= r.to; });
  }, [d, range]);
  const [picked, setPicked] = useState<{ date: string; supplier_id: string | null; ids: string[] } | null>(null);
  const [dialog, setDialog] = useState<{ suppliers: PlanningSupplier[]; date: string; orders: PlanningOrder[] } | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [menu, setMenu] = useState<{ t: TransportOut; x: number; y: number } | null>(null);
  const [action, setAction] = useState<{ kind: "move" | "split" | "note"; t: TransportOut } | null>(null);
  const [docksDay, setDocksDay] = useState<string | null>(null);
  const [mailParts, setMailParts] = useState<{ plan: PlanOut; parts: PlanMailPart[] } | null>(null);
  const toast = useToast();
  const invalidate = useInvalidateAll();
  const byTransport = useMemo(() => new Map((d?.transports ?? []).map((t) => [t.transport_id, t])), [d]);
  const cancelMany = useWrite((ids: string[]) => api.post<TransportOut[]>("/api/transports/cancel", { transport_ids: ids }), (rows) => { setSelected(new Set()); toast.push(`${rows.length} transport(s) annulé(s) : la semaine passe « à renvoyer » si elle était envoyée ; retirez-les de l'outil de transport`, "info"); });
  const seen = useWrite((id: string) => api.post<TransportOut>(`/api/transports/${encodeURIComponent(id)}/seen`), () => toast.push("Camion vérifié", "success"));
  const mailOf = async (ids: string[]) => {
    try {
      const pack = ids.length === 1 ? await api.get<TransportPackOut>(`/api/transports/${encodeURIComponent(ids[0])}/pack`) : await api.post<TransportPackOut>("/api/transports/pack", { transport_ids: ids });
      if (!pack.mail.to.length) toast.push("E-mail sans destinataire : contact transport du fournisseur (ou du transporteur) à renseigner", "info");
      window.location.href = mailtoOf(pack.mail);
    } catch (e) { toast.push((e as Error).message, "error"); }
  };
  /** One click : the complete plan of the week opens in Outlook and the week is marked sent (several messages when too long). */
  const sendPlan = async (p: PlanOut) => {
    try {
      const out = await api.get<PlanMailOut>(`/api/transports/plans/${encodeURIComponent(p.week)}/${encodeURIComponent(p.recipient)}/mail`);
      if (out.split) { setMailParts({ plan: p, parts: out.parts }); return; }
      if (!out.mail.to.length) toast.push(`${p.name} : aucun destinataire, complétez le contact transport (ou les contacts du transporteur) dans le Référentiel`, "info");
      window.location.href = mailtoOf(out.mail);
      const sent = await api.post<PlanOut>(`/api/transports/plans/${encodeURIComponent(p.week)}/${encodeURIComponent(p.recipient)}/sent`);
      invalidate();
      toast.push(`Plan ${sent.label} · ${sent.name} : brouillon ouvert dans Outlook, envoi n° ${sent.sent_no} noté`, "success");
    } catch (e) { toast.push((e as Error).message, "error"); }
  };
  const markSent = async (p: PlanOut) => {
    try {
      const sent = await api.post<PlanOut>(`/api/transports/plans/${encodeURIComponent(p.week)}/${encodeURIComponent(p.recipient)}/sent`);
      invalidate(); setMailParts(null);
      toast.push(`Plan ${sent.label} · ${sent.name} : envoi n° ${sent.sent_no} noté`, "success");
    } catch (e) { toast.push((e as Error).message, "error"); }
  };
  const toggle = (s: PlanningSupplier, o: PlanningOrder) => setPicked((p) => {
    const same = p && p.date === o.expected_date && (scope === "maf" || p.supplier_id === s.supplier_id);
    const ids = same ? [...p.ids] : [];
    const i = ids.indexOf(o.order_id);
    if (i >= 0) ids.splice(i, 1); else ids.push(o.order_id);
    return ids.length ? { date: o.expected_date, supplier_id: scope === "maf" ? null : s.supplier_id, ids } : null;
  });
  const openDialog = () => {
    if (!d || !picked) return;
    const ids = new Set(picked.ids);
    const sups = d.suppliers.filter((s) => s.orders.some((o) => o.expected_date === picked.date && ids.has(o.order_id)));
    setDialog({ suppliers: sups, date: picked.date, orders: sups.flatMap((s) => s.orders.filter((o) => o.expected_date === picked.date && ids.has(o.order_id))) });
  };
  useEffect(() => {
    if (!menu) return;
    const close = (e: MouseEvent) => { if (!(e.target as HTMLElement).closest(".truck-menu")) setMenu(null); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setMenu(null); };
    window.addEventListener("mousedown", close); window.addEventListener("keydown", esc);
    return () => { window.removeEventListener("mousedown", close); window.removeEventListener("keydown", esc); };
  }, [menu]);
  const shownSet = new Set(shown);
  const transports = (d?.transports ?? []).filter((t) => shownSet.has(t.date) || (t.date >= shown[0] && t.date <= shown[shown.length - 1]));
  const transportCols = useMemo<Column<TransportOut>[]>(() => [
    { key: "id", label: "Transport", get: (t) => t.transport_id, render: (t) => <b className={`mono ${t.status === "cancelled" ? "muted" : ""}`}>{t.transport_id}{t.changed_since_sent ? <span className="star" title={t.change_note}> ✱</span> : null}</b> },
    { key: "supplier", label: scope === "maf" ? "Navette" : "Fournisseur", get: (t) => `${t.supplier_id} ${t.supplier_name}`, render: (t) => t.mode === "maf" ? <Badge tone="brand">{t.supplier_name}</Badge> : <SupplierLink id={t.supplier_id} name={t.supplier_name} inline /> },
    { key: "date", label: "Date", get: (t) => t.date, render: (t) => fmtDate(t.date) },
    { key: "time", label: "Heure", get: (t) => t.time },
    { key: "docks", label: "Quais", get: (t) => t.docks.join(" / "), filter: "select" },
    { key: "origin", label: "Origine", get: (t) => (t.origin === "auto" ? "automatique" : "manuel"), filter: "select", render: (t) => <Badge tone={t.origin === "auto" ? "brand" : "neutral"}>{t.origin === "auto" ? "auto" : "manuel"}</Badge> },
    { key: "state", label: "État", get: (t) => [t.status === "cancelled" ? "annulé" : "", t.check_reason ? `à vérifier : ${t.check_reason}` : "", t.changed_since_sent ? `✱ ${t.change_note}` : "", t.pinned ? "épinglé" : ""].filter(Boolean).join(" · "), render: (t) => (
      <span className="row" style={{ gap: 4, flexWrap: "wrap" }}>
        {t.status === "cancelled" && <Badge tone="neutral">annulé</Badge>}
        {t.check_reason && <Badge tone="critical" title={t.check_reason}>à vérifier</Badge>}
        {t.changed_since_sent && <Badge tone="warning" title={t.change_note}>✱ à renvoyer</Badge>}
        {t.pinned && <span className="subtle small" title="Touché à la main : l'ordonnanceur ne le déplace plus"><Pin size={11} style={{ verticalAlign: -1 }} /></span>}
      </span>) },
    { key: "carrier", label: "Transporteur", get: (t) => t.carrier, filter: "select", render: (t) => t.carrier || <span className="subtle">–</span> },
    { key: "orders", label: "Commandes", get: (t) => t.orders.map((o) => `${o.ref || o.order_id} ${o.article_id} ${o.supplier_id}`).join(" ; "), render: (t) => <span className="small">{t.orders.map((o) => `${o.ref || o.order_id} · ${o.article_id}${t.mode === "maf" ? ` · ${o.supplier_id}` : ""}`).join(" ; ")}</span> },
    { key: "by", label: "Créé par", get: (t) => t.created_by, filter: "select", render: (t) => <span className="small subtle">{t.created_by}</span> },
    { key: "act", label: "", get: () => "", filter: "none", sortable: false, export: false, render: (t) => (
      <span className="row" style={{ justifyContent: "flex-end", gap: 4 }}>
        <a className="btn xs" href={api.downloadUrl(`/api/transports/${encodeURIComponent(t.transport_id)}/atr.csv`)} download title="Fichier CSV de ce transport pour l'outil de transport"><Download />CSV</a>
        <Button size="xs" title="Ouvrir l'e-mail de ce seul transport dans Outlook (le plan de semaine est dans la bande du haut)" onClick={() => mailOf([t.transport_id])}><Mail />E-mail</Button>
        {t.status !== "cancelled" && <Button size="xs" variant="ghost" title="Annuler ce transport" onClick={() => { if (window.confirm(`Annuler le transport ${t.transport_id} ?`)) cancelMany.mutate([t.transport_id]); }}><Trash2 /></Button>}
      </span>) },
  ], [scope]);   // eslint-disable-line react-hooks/exhaustive-deps
  const picks = [...selected].filter((id) => byTransport.has(id));
  const trucksOf = (sid: string, day: string) => (d?.transports ?? []).filter((t) => t.supplier_id === sid && t.date === day);

  if (config && !config.access?.can_transports) return <div className="page"><Empty title="Planning de livraisons : accès réservé" hint="Pendant l'essai des camions automatiques, le planning n'est ouvert qu'aux utilisateurs désignés (APPRO_TRANSPORT_USERS)." /></div>;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  return (
    <div className="page">
      <div className="page-header">
        <div className="title">
          <h1>Planning de livraisons</h1>
          <p>{scope === "main" ? "Les ordres fermes ERP de chaque fournisseur, jour par jour. Fournisseur en camions automatiques : l'application crée un camion par jour et le pose sur un créneau de quai ; cliquez un camion pour le scinder, le déplacer ou l'annuler. Sinon, cochez les commandes d'un jour puis Créer le transport. La bande du haut envoie le plan de la semaine, un e-mail complet par destinataire." : "Fournisseurs dont les pièces sont au magasin avancé de Hambach, tous approvisionneurs confondus. Cochez des commandes d'un même jour, de plusieurs fournisseurs s'il le faut, puis Créer la navette."}</p>
        </div>
        <div className="actions">
          <a className="btn primary sm" href={api.downloadUrl("/api/transports/atr.csv")} download title="Fichier CSV pour l'outil de transport : tous les transports prévus à partir d'aujourd'hui, tous fournisseurs et tous approvisionneurs"><FileDown />CSV ATR des transports à venir</a>
        </div>
      </div>
      <Tabs<Scope> tabs={TABS} value={scope} onChange={(s) => { setScope(s); setPicked(null); setSelected(new Set()); }} />
      <Card flush>
        {scope === "main" && plans && <PlanBand plans={plans} onSend={sendPlan} />}
        {d && weeks.length > 0 && <WeekRangeBar weeks={weeks} range={range ?? { from: mondayOf(d.as_of), to: addDays(mondayOf(d.as_of), 7) }} onChange={(r) => setRange(r ?? null)} />}
        {!d ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div> : d.suppliers.length === 0 || shown.length === 0 ? <Empty title="Aucun ordre ferme sur la période" hint={scope === "maf" ? "Aucun fournisseur en mode MAF Hambach n'a d'ordre ferme sur ces semaines." : "Changez de semaines ou de périmètre."} /> : (
          <div className="pivot planning" style={{ maxHeight: "calc(100vh - 330px)" }}>
            <table>
              <thead>
                <tr>
                  <th className="sticky-col">Fournisseur</th>
                  {shown.map((day) => {
                    const today = day === d.as_of, past = day < d.as_of;
                    return <th key={day} className={today ? "today" : past ? "past" : ""} title={fmtDate(day)}>{dayLabel(day)}
                      <button type="button" className="btn ghost xs icon day-btn" title="Quais × créneaux de la journée" aria-label={`Quais et créneaux du ${fmtDate(day)}`} onClick={() => setDocksDay(day)}><LayoutGrid size={12} /></button></th>;
                  })}
                </tr>
              </thead>
              <tbody>
                {d.suppliers.filter((s) => s.orders.some((o) => shownSet.has(o.expected_date))).map((s) => (
                  <tr key={s.supplier_id}>
                    <td className="sticky-col sup">
                      <SupplierLink id={s.supplier_id} name={s.name} />
                      <div className="small" style={{ marginTop: 4 }}>
                        {s.auto && <Badge tone="brand" title="Camions automatiques : un camion par jour créé, complété et annulé par l'application ; cliquez un camion pour agir">auto</Badge>}{s.auto ? " " : ""}
                        {s.carrier && <Badge tone="brand" title={`Mode transport : ${s.carrier}`}>via {s.carrier}</Badge>}{s.carrier ? " " : ""}
                        {s.emails.length ? <span className="subtle" title={s.emails.join("\n")}><Mail size={11} style={{ verticalAlign: -1 }} /> {s.emails.length} destinataire{s.emails.length > 1 ? "s" : ""}</span> : <Badge tone="warning" title={s.carrier ? "Renseignez les contacts du transporteur dans le Référentiel › Transporteurs" : "Renseignez la colonne « Contact transport » du fournisseur dans le Référentiel"}>sans contact</Badge>}
                        <span className="subtle"> · quais {s.docks.join(", ")}</span>
                      </div>
                    </td>
                    {shown.map((day) => {
                      const orders = s.orders.filter((o) => o.expected_date === day);
                      const here = picked && picked.date === day && (scope === "maf" || picked.supplier_id === s.supplier_id) ? picked.ids : [];
                      const trucks = scope === "maf" ? [...new Set(orders.flatMap((o) => o.transports))].map((id) => byTransport.get(id)).filter((t): t is TransportOut => !!t) : trucksOf(s.supplier_id, day);
                      return (
                        <td key={day} className={`cell ${day === d.as_of ? "today" : ""} ${day < d.as_of ? "past" : ""}`}>
                          {orders.map((o) => s.auto ? (
                            <div key={o.order_id} className={`ord-ro ${o.ignored ? "muted" : ""}`} title={`${o.ref || o.order_id}\n${o.article_id} ${o.designation}\n${fmtQty(o.qty_open, o.unit)} ${o.unit} restant / ${fmtQty(o.qty_ordered, o.unit)} commandé${o.ignored ? "\nignorée dans le tableau d'approvisionnement : aucun camion" : ""}${o.transports.length ? `\ncamion : ${o.transports.join(", ")}` : "\nsans camion"}`}>
                              <span><b>{o.article_id}</b> {fmtQty(o.qty_open, o.unit)} {o.unit}</span>{o.transports.length > 0 ? <span className="tr-mark"><Truck size={10} /></span> : !o.ignored && day >= d.as_of ? <Badge tone="warning" title="Aucun camion ne porte cette commande (jour supprimé après une annulation, ou en attente de synchronisation)">sans camion</Badge> : null}
                            </div>
                          ) : (
                            <label key={o.order_id} className={`ord ${o.ignored ? "muted" : ""}`} title={`${o.ref || o.order_id}\n${o.article_id} ${o.designation}\n${fmtQty(o.qty_open, o.unit)} ${o.unit} restant / ${fmtQty(o.qty_ordered, o.unit)} commandé${o.ignored ? "\nignorée dans le tableau d'approvisionnement" : ""}${o.transports.length ? `\ntransport(s) : ${o.transports.join(", ")}` : ""}`}>
                              <input type="checkbox" checked={here.includes(o.order_id)} onChange={() => toggle(s, o)} aria-label={`${o.ref || o.order_id} ${o.article_id}`} />
                              <span className="grow"><b>{o.article_id}</b> <span>{fmtQty(o.qty_open, o.unit)} {o.unit}</span>{o.transports.length > 0 && <span className="tr-mark" title={o.transports.join(", ")}><Truck size={10} /> {o.transports.length}</span>}</span>
                            </label>
                          ))}
                          {trucks.map((t) => <TruckChip key={t.transport_id} t={t} onMenu={(e) => setMenu({ t, x: e.clientX, y: e.clientY })} />)}
                          {here.length > 0 && scope === "main" && !s.auto && <button className="btn primary sm" style={{ marginTop: 6, width: "100%" }} onClick={openDialog}><Truck />Créer le transport ({here.length})</button>}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {scope === "maf" && picked && picked.ids.length > 0 && (
          <div className="row" style={{ padding: "8px 12px", borderTop: "1px solid var(--border)", gap: 10 }}>
            <span className="small">{picked.ids.length} commande(s) du {fmtDate(picked.date)} cochée(s)</span>
            <Button size="sm" variant="primary" onClick={openDialog}><Truck />Créer la navette ({picked.ids.length})</Button>
            <Button size="sm" variant="ghost" onClick={() => setPicked(null)}><X />Vider</Button>
          </div>
        )}
      </Card>

      {d && (
        <Card flush title={`Transports des semaines affichées · ${transports.length}`}>
          <DataTable rows={transports} columns={transportCols} rowKey={(t) => t.transport_id} rowClass={(t) => (t.status === "cancelled" ? "muted" : "")} compact maxHeight="50vh" storageKey={`transports.${scope}`} exportTitle="Transports" emptyTitle="Aucun transport sur ces semaines"
            selection={{ selected, onChange: setSelected }}
            toolbar={picks.length > 0 ? (
              <span className="row" style={{ gap: 6 }}>
                <b className="small">{picks.length} sélectionné(s)</b>
                <a className="btn xs" href={api.downloadUrl(csvUrl(picks))} download title="Un seul fichier CSV pour les transports sélectionnés"><Download />CSV de la sélection</a>
                <Button size="xs" title="Un e-mail pour les transports sélectionnés (même fournisseur, transporteur ou navette)" onClick={() => mailOf(picks)}><Mail />E-mail de la sélection</Button>
                <Button size="xs" variant="ghost" title="Annuler les transports sélectionnés" onClick={() => { if (window.confirm(`Annuler ${picks.length} transport(s) : ${picks.join(", ")} ?\nLa semaine passe « à renvoyer » si elle était envoyée ; retirez-les de l'outil de transport.`)) cancelMany.mutate(picks); }}><Trash2 />Annuler la sélection</Button>
              </span>) : undefined} />
        </Card>
      )}

      {menu && (
        <div className="truck-menu" style={{ left: Math.min(menu.x, window.innerWidth - 260), top: Math.min(menu.y, window.innerHeight - 320) }} role="menu" aria-label={`Actions sur ${menu.t.transport_id}`}>
          <div className="hd"><b className="mono">{menu.t.transport_id}</b> · {dayLabel(menu.t.date)} {menu.t.time} · quai {menu.t.docks.join(" / ")}<br />{menu.t.orders.length} commande(s) : {menu.t.orders.map((o) => `${o.ref || o.order_id} ${o.article_id} ${fmtQty(o.qty)}`).join(" ; ")}{menu.t.check_reason ? <><br /><span style={{ color: "var(--critical-fg)" }}>à vérifier : {menu.t.check_reason}</span></> : null}{menu.t.changed_since_sent ? <><br />✱ {menu.t.change_note}</> : null}{menu.t.note ? <><br />Note : {menu.t.note}</> : null}</div>
          {menu.t.status !== "cancelled" && <>
            {menu.t.check_reason && <button type="button" onClick={() => { seen.mutate(menu.t.transport_id); setMenu(null); }}><Eye />Vu, rien à changer</button>}
            <button type="button" onClick={() => { setAction({ kind: "move", t: menu.t }); setMenu(null); }}><MoveRight />Déplacer (jour, heure, quais)</button>
            {menu.t.orders.length > 1 && <button type="button" onClick={() => { setAction({ kind: "split", t: menu.t }); setMenu(null); }}><Scissors />Scinder en deux camions</button>}
            <button type="button" onClick={() => { setAction({ kind: "note", t: menu.t }); setMenu(null); }}><StickyNote />Note pour le fournisseur</button>
          </>}
          <button type="button" onClick={() => { window.location.href = api.downloadUrl(`/api/transports/${encodeURIComponent(menu.t.transport_id)}/atr.csv`); setMenu(null); }}><Download />CSV de ce camion</button>
          <button type="button" onClick={() => { mailOf([menu.t.transport_id]); setMenu(null); }}><Mail />E-mail de ce seul camion</button>
          {menu.t.status !== "cancelled" && <button type="button" className="danger" onClick={() => { const t = menu.t; setMenu(null); if (window.confirm(`Annuler le camion ${t.transport_id} ?${t.origin === "auto" ? "\nIl ne renaîtra que si une nouvelle commande apparaît ce jour-là." : ""}`)) cancelMany.mutate([t.transport_id]); }}><Trash2 />Annuler le camion</button>}
        </div>
      )}

      {dialog && d && <TransportDialog scope={scope} suppliers={dialog.suppliers} date={dialog.date} orders={dialog.orders} docks={d.docks} onClose={() => setDialog(null)} onCreated={() => { setDialog(null); setPicked(null); }} />}
      {action && d && action.kind === "move" && <MoveDrawer t={action.t} docks={d.docks} slots={d.slots} onClose={() => setAction(null)} />}
      {action && d && action.kind === "split" && <SplitDrawer t={action.t} docks={d.docks} slots={d.slots} onClose={() => setAction(null)} />}
      {action && action.kind === "note" && <NoteDrawer t={action.t} onClose={() => setAction(null)} />}
      {docksDay && <DocksDrawer date={docksDay} onClose={() => setDocksDay(null)} />}
      {mailParts && <MailPartsDrawer plan={mailParts.plan} parts={mailParts.parts} onClose={() => setMailParts(null)} onSent={() => markSent(mailParts.plan)} />}
    </div>
  );
}

/** One truck in a cell : identifier, ✱ when changed since the week was sent, time and docks, red when « à vérifier ». */
function TruckChip({ t, onMenu }: { t: TransportOut; onMenu: (e: React.MouseEvent) => void }) {
  const cls = ["truck", t.check_reason ? "check" : "", t.status === "cancelled" ? "cancelled" : ""].filter(Boolean).join(" ");
  const title = `${t.transport_id} · ${fmtDate(t.date)} ${t.time} · quai ${t.docks.join(" / ")} · ${t.orders.length} commande(s) · ${t.origin === "auto" ? "créé par l'application" : t.created_by}${t.pinned ? " · touché à la main" : ""}${t.check_reason ? `\nà vérifier : ${t.check_reason}` : ""}${t.changed_since_sent ? `\n✱ ${t.change_note} (semaine à renvoyer)` : ""}${t.status === "cancelled" ? "\nannulé : barré jusqu'au renvoi du plan" : ""}\nCliquer : actions`;
  return (
    <button type="button" className={cls} title={title} onClick={onMenu} aria-label={`Camion ${t.transport_id}`}>
      <span className="id"><Truck size={10} /> {t.transport_id}{t.changed_since_sent && <span className="star">✱</span>}{t.pinned && <Pin size={9} className="pin" />}</span>
      <span>{t.status === "cancelled" ? "annulé" : <>{t.time} · {t.docks.join("/")} · {t.orders.length} cmd</>}</span>
      {t.check_reason && t.status !== "cancelled" && <span className="why">à vérifier · {t.check_reason}</span>}
    </button>
  );
}

/** The two-line band : the plan of the next week to send (one click per recipient), and the weeks to re-send with why. */
function PlanBand({ plans, onSend }: { plans: PlansOut; onSend: (p: PlanOut) => void }) {
  const next = plans.plans.filter((p) => p.week === plans.next_week);
  const toSend = next.filter((p) => p.state === "a_envoyer");
  const resend = plans.plans.filter((p) => p.state === "a_renvoyer");
  const currentUnsent = plans.plans.filter((p) => p.week === plans.current_week && p.state === "a_envoyer");
  const checks = plans.plans.reduce((n, p) => n + p.checks, 0);
  const dayName = WEEKDAY_LONG[(plans.plan_weekday + 6) % 7] ?? "jeudi";
  const label = (wk: string) => `S${wk.split("-W")[1]}`;
  const pill = (p: PlanOut) => (
    <button key={`${p.week}/${p.recipient}`} type="button" className={`plan-pill ${p.state} ${p.emails.length ? "" : "nomail"}`} onClick={() => onSend(p)}
      title={`${p.name} · ${STATE_LABEL[p.state]} · ${p.trucks} camion(s)${p.emails.length ? ` · ${p.emails.join(", ")}` : " · aucun destinataire (Référentiel)"}${p.sent_no ? ` · envoi n° ${p.sent_no} le ${p.sent_at ? fmtDateTime(p.sent_at) : ""} par ${p.sent_by}` : ""}${p.reasons.length ? `\n${p.reasons.join("\n")}` : ""}\nCliquer : ${p.state === "envoyee" ? "renvoyer le plan complet" : "ouvrir le plan complet dans Outlook et noter l'envoi"}`}>
      {p.state === "envoyee" ? <Check size={11} /> : p.state === "a_renvoyer" ? "✱" : <Send size={11} />}{p.name}{p.state === "envoyee" ? "" : ` · ${p.trucks}`}
    </button>
  );
  return (
    <div className="plan-band" role="region" aria-label="Plan de semaine">
      <div className="line">
        <span className="head"><Send size={13} style={{ verticalAlign: -2 }} /> Plan de la semaine {label(plans.next_week)}{toSend.length ? <> · à envoyer {dayName} : {toSend.length} destinataire{toSend.length > 1 ? "s" : ""}, {toSend.reduce((n, p) => n + p.trucks, 0)} camion{toSend.reduce((n, p) => n + p.trucks, 0) > 1 ? "s" : ""}</> : next.length ? <> · envoyé à {next.length} destinataire{next.length > 1 ? "s" : ""}</> : <> · aucun camion pour l'instant</>}</span>
        <span className="pills">{next.map(pill)}</span>
        {toSend.length > 0 && <Button size="xs" variant="primary" onClick={() => onSend(toSend[0])} title={`Ouvrir dans Outlook le plan complet de ${toSend[0].name}, puis le suivant`}><Mail />Envoyer le suivant</Button>}
        {checks > 0 && <Badge tone="critical" title="Camions marqués « à vérifier » dans les deux semaines : cliquez-les dans le planning">{checks} à vérifier</Badge>}
      </div>
      {(resend.length > 0 || currentUnsent.length > 0) && (
        <div className="line resend">
          <span className="head">✱ Rectificatif{resend.length > 1 ? "s" : ""}{resend.length ? ` · ${resend.length}` : ""}</span>
          {resend.map((p) => <span key={`${p.week}/${p.recipient}`} className="row" style={{ gap: 6 }}>{pill(p)}<span className="reason">{label(p.week)} · {p.reasons.slice(0, 2).join(" ; ")}{p.reasons.length > 2 ? ` ; +${p.reasons.length - 2}` : ""}</span><Button size="xs" onClick={() => onSend(p)} title="Renvoyer le plan complet de la semaine, lignes changées marquées ✱"><Mail />Renvoyer</Button></span>)}
          {currentUnsent.length > 0 && <span className="row" style={{ gap: 6 }}><span className="reason">semaine en cours {label(plans.current_week)} jamais envoyée :</span>{currentUnsent.map(pill)}</span>}
        </div>
      )}
    </div>
  );
}

/** The plan is too long for one ``mailto:`` : one message per supplier, then the planner marks the week sent. */
function MailPartsDrawer({ plan, parts, onClose, onSent }: { plan: PlanOut; parts: PlanMailPart[]; onClose: () => void; onSent: () => void }) {
  const [opened, setOpened] = useState<Set<number>>(new Set());
  return (
    <Drawer open onClose={onClose} title={<>Plan {plan.label} · {plan.name} · {parts.length} e-mails</>}
      footer={<><Button onClick={onClose}>Fermer</Button><Button variant="primary" disabled={opened.size < parts.length} onClick={onSent} title={opened.size < parts.length ? "Ouvrez chaque e-mail d'abord" : "Noter l'envoi"}><Check />Marquer la semaine envoyée</Button></>}>
      <p className="small subtle">Le plan complet dépasse la taille qu'un lien Outlook accepte : il part en un e-mail par fournisseur, tous au même destinataire. Ouvrez-les un à un, envoyez-les dans Outlook, puis marquez la semaine envoyée.</p>
      <table className="tbl compact">
        <thead><tr><th>#</th><th>Objet</th><th className="num">Caractères</th><th /></tr></thead>
        <tbody>{parts.map((p, i) => <tr key={i}><td>{i + 1}</td><td className="small">{p.subject}</td><td className="num small">{p.chars}</td><td><Button size="xs" variant={opened.has(i) ? "default" : "primary"} onClick={() => { window.location.href = mailtoOf(p); setOpened((s) => new Set([...s, i])); }}><Mail />{opened.has(i) ? "Rouvrir" : "Ouvrir dans Outlook"}</Button></td></tr>)}</tbody>
      </table>
    </Drawer>
  );
}

/** Day, hour and docks of a truck : pinned afterwards, its week turns « à renvoyer » when it was sent. */
function MoveDrawer({ t, docks, slots, onClose }: { t: TransportOut; docks: string[]; slots: string[]; onClose: () => void }) {
  const toast = useToast();
  const [date, setDate] = useState(t.date);
  const [time, setTime] = useState(t.time || DEFAULT_TIME);
  const [picked, setPicked] = useState<string[]>(t.docks);
  const run = useWrite((body: { date?: string; time?: string; docks?: string[] }) => api.post<TransportOut>(`/api/transports/${encodeURIComponent(t.transport_id)}/move`, body),
    (out) => { toast.push(`${out.transport_id} déplacé au ${fmtDate(out.date)} ${out.time}, quai ${out.docks.join(" / ")}${out.changed_since_sent ? " · semaine à renvoyer" : ""}`, "success"); onClose(); });
  const valid = !!date && /^([01]\d|2[0-3]):[0-5]\d$/.test(time) && picked.length > 0;
  return (
    <Drawer open onClose={onClose} title={<>Déplacer {t.transport_id} <span className="muted">· {t.supplier_id} {t.supplier_name}</span></>}
      footer={<><Button onClick={onClose}>Annuler</Button><Button variant="primary" disabled={!valid || run.isPending} onClick={() => run.mutate({ date, time, docks: picked })}>{run.isPending ? "En cours…" : "Déplacer"}</Button></>}>
      <div className="grid cols-2">
        <label className="field"><span>Jour</span><input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} /></label>
        <label className="field"><span>Heure</span>
          <span className="row" style={{ gap: 6 }}>
            <select className="select" value={slots.includes(time) ? time : ""} onChange={(e) => { if (e.target.value) setTime(e.target.value); }} aria-label="Créneau"><option value="">hors créneau…</option>{slots.map((s) => <option key={s} value={s}>{s}</option>)}</select>
            <input className="input" type="time" value={time} step={300} onChange={(e) => setTime(e.target.value)} aria-label="Heure" style={{ width: 110 }} />
          </span></label>
      </div>
      <div className="field" style={{ marginTop: 10 }}><span>Quais</span><DockPicker all={docks} value={picked} onChange={setPicked} label="Quais" /></div>
      <p className="small subtle" style={{ marginTop: 10 }}>Le camion est ensuite <b>épinglé</b> : l'ordonnanceur ne le déplace plus et sa marque « à vérifier » s'efface. Si la semaine a déjà été envoyée, elle passe « à renvoyer » ({t.orders.length} commande(s) à bord).</p>
      {run.error && <div className="error-box">{(run.error as Error).message}</div>}
    </Drawer>
  );
}

/** Some orders leave for a second truck of the same day (new EM) ; both trucks are pinned. */
function SplitDrawer({ t, docks, slots, onClose }: { t: TransportOut; docks: string[]; slots: string[]; onClose: () => void }) {
  const toast = useToast();
  const [ids, setIds] = useState<string[]>([]);
  const [time, setTime] = useState("");
  const [picked, setPicked] = useState<string[]>(t.docks);
  const run = useWrite((body: { order_ids: string[]; time?: string; docks?: string[] }) => api.post<TransportOut[]>(`/api/transports/${encodeURIComponent(t.transport_id)}/split`, body),
    (out) => { toast.push(`${out[1].transport_id} créé à ${out[1].time} avec ${out[1].orders.length} commande(s) ; ${out[0].transport_id} en garde ${out[0].orders.length}`, "success"); onClose(); });
  const valid = ids.length > 0 && ids.length < t.orders.length && picked.length > 0 && (time === "" || /^([01]\d|2[0-3]):[0-5]\d$/.test(time));
  return (
    <Drawer open onClose={onClose} title={<>Scinder {t.transport_id} <span className="muted">· {dayLabel(t.date)} {t.time}</span></>}
      footer={<><Button onClick={onClose}>Annuler</Button><Button variant="primary" disabled={!valid || run.isPending} onClick={() => run.mutate({ order_ids: ids, time: time || undefined, docks: picked })}>{run.isPending ? "En cours…" : `Créer le second camion (${ids.length})`}</Button></>}>
      <h4 style={{ margin: "0 0 6px" }}>Commandes qui partent dans le second camion</h4>
      <table className="tbl compact">
        <thead><tr><th /><th>Commande</th><th>Article</th><th className="num">Quantité</th></tr></thead>
        <tbody>{t.orders.map((o) => <tr key={o.order_id}><td><input type="checkbox" checked={ids.includes(o.order_id)} onChange={() => setIds((s) => (s.includes(o.order_id) ? s.filter((x) => x !== o.order_id) : [...s, o.order_id]))} aria-label={o.ref || o.order_id} /></td><td className="mono small">{o.ref || o.order_id}</td><td><b>{o.article_id}</b></td><td className="num">{fmtQty(o.qty)}</td></tr>)}</tbody>
      </table>
      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <label className="field"><span>Créneau du second camion</span><select className="select" value={time} onChange={(e) => setTime(e.target.value)} aria-label="Créneau du second camion"><option value="">le plus proche libre (automatique)</option>{slots.map((s) => <option key={s} value={s}>{s}</option>)}</select></label>
        <div className="field"><span>Quais</span><DockPicker all={docks} value={picked} onChange={setPicked} label="Quais du second camion" /></div>
      </div>
      <p className="small subtle" style={{ marginTop: 10 }}>Au moins une commande reste dans {t.transport_id}. Les deux camions sont épinglés ; la semaine passe « à renvoyer » si elle était envoyée.</p>
      {run.error && <div className="error-box">{(run.error as Error).message}</div>}
    </Drawer>
  );
}

function NoteDrawer({ t, onClose }: { t: TransportOut; onClose: () => void }) {
  const toast = useToast();
  const [note, setNote] = useState(t.note);
  const run = useWrite((body: { note: string }) => api.post<TransportOut>(`/api/transports/${encodeURIComponent(t.transport_id)}/note`, body), () => { toast.push("Note enregistrée", "success"); onClose(); });
  return (
    <Drawer open onClose={onClose} title={<>Note · {t.transport_id}</>} footer={<><Button onClick={onClose}>Annuler</Button><Button variant="primary" disabled={run.isPending} onClick={() => run.mutate({ note })}>Enregistrer</Button></>}>
      <label className="field"><span>Note pour le fournisseur (imprimée dans le plan de semaine, ne déclenche pas de renvoi)</span><input className="input" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="Ex. : prévenir le quai 30 min avant l'arrivée" /></label>
    </Drawer>
  );
}

/** Docks × slots of one day : who comes when, the closed docks, the trucks to check. */
function DocksDrawer({ date, onClose }: { date: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["transports-docks", date], queryFn: () => api.get<DockDayOut>("/api/transports/docks", { date }) });
  const d = q.data;
  return (
    <Drawer open onClose={onClose} wide title={<>Quais × créneaux · {dayLabel(date)}</>} footer={<Button onClick={onClose}>Fermer</Button>}>
      {q.isError ? <ErrorBox error={q.error} /> : !d ? <SkeletonBlock rows={5} /> : (
        <>
          <p className="small subtle" style={{ marginTop: 0 }}>{d.capacity} camion{d.capacity > 1 ? "s" : ""} au plus par quai et par créneau. Un créneau orange est plein ; un quai gris est fermé ce jour-là ; en rouge, les camions « à vérifier ».</p>
          <div className="scroll-x">
            <table className="dock-grid">
              <thead><tr><th>Quai</th>{d.slots.map((s) => <th key={s} className="slot">{s}</th>)}</tr></thead>
              <tbody>
                {d.docks.map((row) => (
                  <tr key={row.dock}>
                    <th>{row.dock}{row.closed && <span className="small subtle"> · fermé</span>}</th>
                    {d.slots.map((s) => { const xs = row.slots[s] ?? []; return (
                      <td key={s} className={row.closed ? "closed" : xs.length >= d.capacity ? "full" : ""} title={xs.map((x) => `${x.transport_id} · ${x.supplier_id} ${x.supplier_name}${x.check_reason ? ` · à vérifier : ${x.check_reason}` : ""}`).join("\n")}>
                        {xs.map((x) => <span key={x.transport_id} className={`t ${x.check_reason ? "check" : ""} ${x.origin === "auto" ? "" : "manual"}`}>{x.transport_id}<br /><span style={{ fontFamily: "inherit" }}>{x.supplier_name.slice(0, 12) || x.supplier_id}</span></span>)}
                      </td>); })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {d.off_grid.length > 0 && <p className="small" style={{ marginTop: 8 }}>Hors grille : {d.off_grid.map((x) => `${x.transport_id} (${x.supplier_id})`).join(", ")}</p>}
        </>
      )}
    </Drawer>
  );
}

/** One or several trucks for the ticked orders of one day : one supplier (main planning, default docks preselected) or the MAF shuttle (several suppliers). */
function TransportDialog({ scope, suppliers, date, orders, docks, onClose, onCreated }: { scope: Scope; suppliers: PlanningSupplier[]; date: string; orders: PlanningOrder[]; docks: string[]; onClose: () => void; onCreated: () => void }) {
  const toast = useToast();
  const maf = scope === "maf";
  const first = suppliers[0];
  const preset = (first?.docks ?? []).filter((x) => docks.includes(x));
  const [slots, setSlots] = useState<TransportSlotIn[]>([{ date, time: DEFAULT_TIME, docks: preset.length ? preset : [docks[0]] }]);
  const [carrier, setCarrier] = useState(maf ? "" : first?.carrier ?? "");
  const [note, setNote] = useState("");
  const emails = Array.from(new Set(suppliers.flatMap((s) => s.emails)));
  const supplierOf = (o: PlanningOrder) => suppliers.find((s) => s.orders.includes(o));
  const run = useWrite((body: { supplier_id?: string; maf?: boolean; order_ids: string[]; slots: TransportSlotIn[]; carrier: string; note: string }) => api.post<TransportPackOut>("/api/transports", body),
    (pack) => {
      const ids = pack.transports.map((t) => t.transport_id).join(", ");
      toast.push(`Transport ${ids} créé · fichier CSV et e-mail disponibles dans la liste des transports${pack.mail.to.length ? "" : " · e-mail SANS destinataire (contacts à renseigner)"}`, pack.mail.to.length ? "success" : "info");
      onCreated();
    });
  const update = (i: number, patch: Partial<TransportSlotIn>) => setSlots((s) => s.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const valid = slots.every((s) => s.date && /^([01]\d|2[0-3]):[0-5]\d$/.test(s.time) && s.docks.length > 0) && orders.length > 0;
  const title = maf ? <>Navette Hambach · {fmtDate(date)} · {suppliers.length} fournisseur{suppliers.length > 1 ? "s" : ""}</> : <>Transport · {first?.supplier_id} <span className="muted">{first?.name}</span> · {fmtDate(date)}</>;
  return (
    <Drawer open onClose={onClose} wide title={title}
      footer={<><Button onClick={onClose}>Annuler</Button><Button variant="primary" disabled={!valid || run.isPending} onClick={() => run.mutate(maf ? { maf: true, order_ids: orders.map((o) => o.order_id), slots, carrier, note } : { supplier_id: first.supplier_id, order_ids: orders.map((o) => o.order_id), slots, carrier, note })}>{run.isPending ? "En cours…" : `Créer ${slots.length} ${maf ? "navette" : "transport"}${slots.length > 1 ? "s" : ""}`}</Button></>}>
      <h4 style={{ margin: "0 0 6px" }}>Commandes {maf ? "de la navette" : "du transport"}</h4>
      <table className="tbl compact">
        <thead><tr>{maf && <th>Fournisseur</th>}<th>Commande</th><th>Article</th><th className="num">Quantité</th></tr></thead>
        <tbody>{orders.map((o) => <tr key={o.order_id}>{maf && <td className="small">{supplierOf(o)?.supplier_id} <span className="subtle">{supplierOf(o)?.name}</span></td>}<td className="mono small">{o.ref || o.order_id}</td><td><b>{o.article_id}</b> <span className="subtle">{o.designation}</span></td><td className="num">{fmtQty(o.qty_open, o.unit)} {o.unit}</td></tr>)}</tbody>
      </table>
      <h4 style={{ margin: "14px 0 6px" }}>Camion{slots.length > 1 ? "s" : ""}</h4>
      <table className="tbl compact slots">
        <thead><tr><th>Transport</th><th>Date</th><th>Heure</th><th>Quais <span className="subtle small">(un ou plusieurs)</span></th><th /></tr></thead>
        <tbody>
          {slots.map((s, i) => (
            <tr key={i}>
              <td className="subtle small">EM… {i + 1}</td>
              <td><input className="input sm" type="date" value={s.date} onChange={(e) => update(i, { date: e.target.value })} aria-label={`Date du transport ${i + 1}`} /></td>
              <td><input className="input sm" type="time" value={s.time} step={300} onChange={(e) => update(i, { time: e.target.value })} aria-label={`Heure du transport ${i + 1}`} /></td>
              <td><DockPicker all={docks} value={s.docks} onChange={(v) => update(i, { docks: v })} label={`Quais du transport ${i + 1}`} /></td>
              <td>{slots.length > 1 && <Button size="sm" variant="ghost" icon title="Retirer ce transport" onClick={() => setSlots((x) => x.filter((_, j) => j !== i))}><X /></Button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <Button size="sm" style={{ marginTop: 8 }} onClick={() => setSlots((x) => [...x, { ...x[x.length - 1], docks: [...x[x.length - 1].docks] }])}><Plus />Ajouter un transport pour ces commandes</Button>
      <div className="grid cols-2" style={{ marginTop: 14 }}>
        <label className="field"><span>Transporteur{first?.carrier && !maf ? " (mode transport du fournisseur)" : " (facultatif)"}</span><input className="input" value={carrier} onChange={(e) => setCarrier(e.target.value)} maxLength={120} placeholder="colonne TRANSPORTEUR du fichier ATR" /></label>
        <label className="field"><span>Commentaire (facultatif)</span><input className="input" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="Ex. : prévenir le quai 30 min avant l'arrivée" /></label>
      </div>
      <p className="small subtle" style={{ marginTop: 10 }}>Un identifiant EM par camion. {maf ? "Le fichier ATR porte NAVETTE HAMBACH comme fournisseur." : ""} E-mail à : {emails.length ? <b>{emails.join(", ")}</b> : <b style={{ color: "var(--warning-fg)" }}>aucun destinataire (contacts à renseigner dans le Référentiel)</b>}.</p>
      {run.error && <div className="error-box">{(run.error as Error).message}</div>}
    </Drawer>
  );
}
