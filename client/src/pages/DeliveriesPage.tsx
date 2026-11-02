import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, FileDown, Mail, Plus, Trash2, Truck, X } from "lucide-react";
import { api } from "@/lib/api";
import { useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { Badge, Button, Card, Drawer, Empty, ErrorBox, SkeletonBlock, SupplierLink, Tabs, useToast } from "@/components/ui";
import { WEEKDAY_SHORT, fmtDate, fmtQty } from "@/lib/format";
import { DockPicker } from "@/components/DockPicker";
import { DataTable, type Column } from "@/components/DataTable";
import { WeekRangeBar, type WeekChip, type WeekRange } from "@/components/WeekRangeBar";
import type { PlanningOrder, PlanningResponse, PlanningSupplier, TransportOut, TransportPackOut, TransportSlotIn } from "@/lib/types";

const DEFAULT_TIME = "08:00";
const WINDOW_WEEKS = 10;          // one past week, the current one and eight to come
type Scope = "main" | "maf";
const TABS: { id: Scope; label: string }[] = [{ id: "main", label: "Planning de livraisons" }, { id: "maf", label: "Planning MAF" }];

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
function isoWeekLabel(monday: string): string {
  const d = new Date(`${monday}T12:00:00`);
  const thursday = new Date(d); thursday.setDate(d.getDate() + 3);
  const jan4 = new Date(thursday.getFullYear(), 0, 4);
  const week = 1 + Math.round(((thursday.getTime() - jan4.getTime()) / 86400000 - 3 + ((jan4.getDay() + 6) % 7)) / 7);
  return `S${String(week).padStart(2, "0")}`;
}
/** Outlook opens the pre-filled message ; the planner reads it and sends it (``;`` between recipients, as Outlook expects). */
export function mailtoOf(mail: TransportPackOut["mail"]): string {
  return `mailto:${mail.to.join(";")}?subject=${encodeURIComponent(mail.subject)}&body=${encodeURIComponent(mail.body)}`;
}
const csvUrl = (ids: string[]) => `/api/transports/atr.csv?${ids.map((id) => `transport_id=${encodeURIComponent(id)}`).join("&")}`;

/**
 * « Planning de livraisons » : suppliers in rows (those with a firm order in the shown weeks), working days in
 * columns, the firm ERP orders of each day in the cells.  Two tabs : the suppliers who deliver themselves or
 * through a carrier (the planner's perimeter), and the « Planning MAF » (suppliers whose parts wait at the MAF
 * Hambach, every planner, one shuttle may carry orders of several suppliers).  The weeks are chosen with the
 * same week bar as the supply table.  The transports of the shown weeks are listed with filters, a
 * multi-selection and batch actions (CSV, e-mail, cancellation).
 */
export default function DeliveriesPage() {
  const { perimeter, config } = usePerimeter();
  const [scope, setScope] = useState<Scope>("main");
  const start = config ? addDays(mondayOf(config.as_of), -7) : null;
  const q = useQuery({ queryKey: ["transports-planning", scope, scope === "main" ? perimeter.planner : null, start], enabled: !!start,
    queryFn: () => api.get<PlanningResponse>("/api/transports/planning", { planner: scope === "main" ? perimeter.planner : undefined, start, weeks: WINDOW_WEEKS, scope }) });
  const d = q.data;
  const weeks = useMemo<WeekChip[]>(() => {
    if (!d) return [];
    const current = mondayOf(d.as_of);
    const keys = Array.from(new Set(d.days.map(mondayOf)));
    return keys.map((k) => ({ key: k, label: isoWeekLabel(k), start: fmtDate(k, "dd/MM"), current: k === current, past: k < current }));
  }, [d]);
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
  const toast = useToast();
  const byTransport = useMemo(() => new Map((d?.transports ?? []).map((t) => [t.transport_id, t])), [d]);
  const cancelMany = useWrite((ids: string[]) => api.post<TransportOut[]>("/api/transports/cancel", { transport_ids: ids }), (rows) => { setSelected(new Set()); toast.push(`${rows.length} transport(s) annulé(s) : prévenez les destinataires et retirez-les de l'outil de transport`, "info"); });
  const mailOf = async (ids: string[]) => {
    try {
      const pack = ids.length === 1 ? await api.get<TransportPackOut>(`/api/transports/${encodeURIComponent(ids[0])}/pack`) : await api.post<TransportPackOut>("/api/transports/pack", { transport_ids: ids });
      if (!pack.mail.to.length) toast.push("E-mail sans destinataire : contact transport du fournisseur (ou du transporteur) à renseigner", "info");
      window.location.href = mailtoOf(pack.mail);
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
  const shownSet = new Set(shown);
  const transports = (d?.transports ?? []).filter((t) => shownSet.has(t.date) || (t.date >= shown[0] && t.date <= shown[shown.length - 1]));
  const transportCols = useMemo<Column<TransportOut>[]>(() => [
    { key: "id", label: "Transport", get: (t) => t.transport_id, render: (t) => <b className="mono">{t.transport_id}</b> },
    { key: "supplier", label: scope === "maf" ? "Navette" : "Fournisseur", get: (t) => `${t.supplier_id} ${t.supplier_name}`, render: (t) => t.mode === "maf" ? <Badge tone="brand">{t.supplier_name}</Badge> : <SupplierLink id={t.supplier_id} name={t.supplier_name} inline /> },
    { key: "date", label: "Date", get: (t) => t.date, render: (t) => fmtDate(t.date) },
    { key: "time", label: "Heure", get: (t) => t.time },
    { key: "docks", label: "Quais", get: (t) => t.docks.join(" / "), filter: "select" },
    { key: "carrier", label: "Transporteur", get: (t) => t.carrier, filter: "select", render: (t) => t.carrier || <span className="subtle">–</span> },
    { key: "orders", label: "Commandes", get: (t) => t.orders.map((o) => `${o.ref || o.order_id} ${o.article_id} ${o.supplier_id}`).join(" ; "), render: (t) => <span className="small">{t.orders.map((o) => `${o.ref || o.order_id} · ${o.article_id}${t.mode === "maf" ? ` · ${o.supplier_id}` : ""}`).join(" ; ")}</span> },
    { key: "by", label: "Créé par", get: (t) => t.created_by, filter: "select", render: (t) => <span className="small subtle">{t.created_by}</span> },
    { key: "act", label: "", get: () => "", filter: "none", sortable: false, export: false, render: (t) => (
      <span className="row" style={{ justifyContent: "flex-end", gap: 4 }}>
        <a className="btn xs" href={api.downloadUrl(`/api/transports/${encodeURIComponent(t.transport_id)}/atr.csv`)} download title="Fichier CSV de ce transport pour l'outil de transport"><Download />CSV</a>
        <Button size="xs" title="Ouvrir l'e-mail dans Outlook" onClick={() => mailOf([t.transport_id])}><Mail />E-mail</Button>
        <Button size="xs" variant="ghost" title="Annuler ce transport" onClick={() => { if (window.confirm(`Annuler le transport ${t.transport_id} ?`)) cancelMany.mutate([t.transport_id]); }}><Trash2 /></Button>
      </span>) },
  ], [scope]);   // eslint-disable-line react-hooks/exhaustive-deps
  const picks = [...selected].filter((id) => byTransport.has(id));

  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  return (
    <div className="page">
      <div className="page-header">
        <div className="title">
          <h1>Planning de livraisons</h1>
          <p>{scope === "main" ? "Les ordres fermes ERP de chaque fournisseur, jour par jour. Cochez les commandes d'un jour puis Créer le transport : un ou plusieurs camions (date, heure, quais), numérotés EM. Un fournisseur en mode transporteur : son transporteur dans le fichier ATR, l'e-mail à ses contacts." : "Fournisseurs dont les pièces sont au magasin avancé de Hambach, tous approvisionneurs confondus. Cochez des commandes d'un même jour, de plusieurs fournisseurs s'il le faut, puis Créer la navette."}</p>
        </div>
        <div className="actions">
          <a className="btn primary sm" href={api.downloadUrl("/api/transports/atr.csv")} download title="Fichier CSV pour l'outil de transport : tous les transports prévus à partir d'aujourd'hui, tous fournisseurs et tous approvisionneurs"><FileDown />CSV ATR des transports à venir</a>
        </div>
      </div>
      <Tabs<Scope> tabs={TABS} value={scope} onChange={(s) => { setScope(s); setPicked(null); setSelected(new Set()); }} />
      <Card flush>
        {d && weeks.length > 0 && <WeekRangeBar weeks={weeks} range={range ?? { from: mondayOf(d.as_of), to: addDays(mondayOf(d.as_of), 7) }} onChange={(r) => setRange(r ?? null)} />}
        {!d ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div> : d.suppliers.length === 0 || shown.length === 0 ? <Empty title="Aucun ordre ferme sur la période" hint={scope === "maf" ? "Aucun fournisseur en mode MAF Hambach n'a d'ordre ferme sur ces semaines." : "Changez de semaines ou de périmètre."} /> : (
          <div className="pivot planning" style={{ maxHeight: "calc(100vh - 330px)" }}>
            <table>
              <thead>
                <tr>
                  <th className="sticky-col">Fournisseur</th>
                  {shown.map((day) => {
                    const dt = new Date(`${day}T00:00:00`);
                    const today = day === d.as_of, past = day < d.as_of;
                    return <th key={day} className={today ? "today" : past ? "past" : ""} title={fmtDate(day)}>{WEEKDAY_SHORT[(dt.getDay() + 6) % 7]} {fmtDate(day, "dd/MM")}</th>;
                  })}
                </tr>
              </thead>
              <tbody>
                {d.suppliers.filter((s) => s.orders.some((o) => shownSet.has(o.expected_date))).map((s) => (
                  <tr key={s.supplier_id}>
                    <td className="sticky-col sup">
                      <SupplierLink id={s.supplier_id} name={s.name} />
                      <div className="small" style={{ marginTop: 4 }}>
                        {s.carrier && <Badge tone="brand" title={`Mode transport : ${s.carrier}`}>via {s.carrier}</Badge>}{s.carrier ? " " : ""}
                        {s.emails.length ? <span className="subtle" title={s.emails.join("\n")}><Mail size={11} style={{ verticalAlign: -1 }} /> {s.emails.length} destinataire{s.emails.length > 1 ? "s" : ""}</span> : <Badge tone="warning" title={s.carrier ? "Renseignez les contacts du transporteur dans le Référentiel › Transporteurs" : "Renseignez la colonne « Contact transport » du fournisseur dans le Référentiel"}>sans contact</Badge>}
                        <span className="subtle"> · quais {s.docks.join(", ")}</span>
                      </div>
                    </td>
                    {shown.map((day) => {
                      const orders = s.orders.filter((o) => o.expected_date === day);
                      const here = picked && picked.date === day && (scope === "maf" || picked.supplier_id === s.supplier_id) ? picked.ids : [];
                      const ems = [...new Set(orders.flatMap((o) => o.transports))];
                      return (
                        <td key={day} className={`cell ${day === d.as_of ? "today" : ""} ${day < d.as_of ? "past" : ""}`}>
                          {orders.map((o) => (
                            <label key={o.order_id} className={`ord ${o.ignored ? "muted" : ""}`} title={`${o.ref || o.order_id}\n${o.article_id} ${o.designation}\n${fmtQty(o.qty_open, o.unit)} ${o.unit} restant / ${fmtQty(o.qty_ordered, o.unit)} commandé${o.ignored ? "\nignorée dans le tableau d'approvisionnement" : ""}${o.transports.length ? `\ntransport(s) : ${o.transports.join(", ")}` : ""}`}>
                              <input type="checkbox" checked={here.includes(o.order_id)} onChange={() => toggle(s, o)} aria-label={`${o.ref || o.order_id} ${o.article_id}`} />
                              <span className="grow"><b>{o.article_id}</b> <span>{fmtQty(o.qty_open, o.unit)} {o.unit}</span>{o.transports.length > 0 && <span className="tr-mark" title={o.transports.join(", ")}><Truck size={10} /> {o.transports.length}</span>}</span>
                            </label>
                          ))}
                          {ems.length > 0 && <div className="tr-list">{ems.map((id) => { const t = byTransport.get(id); return <span key={id} className="badge brand" title={t ? `${id} · ${fmtDate(t.date)} ${t.time} · quai ${t.docks.join(" / ")} · ${t.orders.length} commande(s) · ${t.created_by}` : id}><Truck size={10} /> {id}{t ? ` ${t.time} ${t.docks.join("/")}` : ""}</span>; })}</div>}
                          {here.length > 0 && scope === "main" && <button className="btn primary sm" style={{ marginTop: 6, width: "100%" }} onClick={openDialog}><Truck />Créer le transport ({here.length})</button>}
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
          <DataTable rows={transports} columns={transportCols} rowKey={(t) => t.transport_id} compact maxHeight="50vh" storageKey={`transports.${scope}`} exportTitle="Transports" emptyTitle="Aucun transport sur ces semaines"
            selection={{ selected, onChange: setSelected }}
            toolbar={picks.length > 0 ? (
              <span className="row" style={{ gap: 6 }}>
                <b className="small">{picks.length} sélectionné(s)</b>
                <a className="btn xs" href={api.downloadUrl(csvUrl(picks))} download title="Un seul fichier CSV pour les transports sélectionnés"><Download />CSV de la sélection</a>
                <Button size="xs" title="Un e-mail pour les transports sélectionnés (même fournisseur, transporteur ou navette)" onClick={() => mailOf(picks)}><Mail />E-mail de la sélection</Button>
                <Button size="xs" variant="ghost" title="Annuler les transports sélectionnés" onClick={() => { if (window.confirm(`Annuler ${picks.length} transport(s) : ${picks.join(", ")} ?\nPrévenez les destinataires et retirez-les de l'outil de transport.`)) cancelMany.mutate(picks); }}><Trash2 />Annuler la sélection</Button>
              </span>) : undefined} />
        </Card>
      )}

      {dialog && d && <TransportDialog scope={scope} suppliers={dialog.suppliers} date={dialog.date} orders={dialog.orders} docks={d.docks} onClose={() => setDialog(null)} onCreated={() => { setDialog(null); setPicked(null); }} />}
    </div>
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
