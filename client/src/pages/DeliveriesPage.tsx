import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Download, FileDown, Mail, Plus, Trash2, Truck, X } from "lucide-react";
import { api } from "@/lib/api";
import { useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { Badge, Button, Card, Drawer, Empty, ErrorBox, SkeletonBlock, SupplierLink, useToast } from "@/components/ui";
import { WEEKDAY_SHORT, fmtDate, fmtQty } from "@/lib/format";
import { DockPicker } from "@/components/DockPicker";
import type { PlanningOrder, PlanningResponse, PlanningSupplier, TransportOut, TransportPackOut, TransportSlotIn } from "@/lib/types";

const DEFAULT_TIME = "08:00";

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
/** Outlook opens the pre-filled message ; the planner reads it and sends it (``;`` between recipients, as Outlook expects). */
export function mailtoOf(mail: TransportPackOut["mail"]): string {
  return `mailto:${mail.to.join(";")}?subject=${encodeURIComponent(mail.subject)}&body=${encodeURIComponent(mail.body)}`;
}

/**
 * « Planning de livraisons » : suppliers in rows (those with a firm order in the window), working days in columns,
 * the firm ERP orders of each day in the cells (article and remaining quantity).  Ticking orders of one cell and
 * clicking « Créer le transport » opens the dialog : one or several trucks (date, time, docks) for those orders ;
 * validating creates the transports (EM…).  The CSV for the transport tool and the e-mail to the supplier are
 * downloaded / opened from the list of transports, per transport ; the global CSV (every planned transport from
 * today on) is one click in the header.
 */
export default function DeliveriesPage() {
  const { perimeter, config } = usePerimeter();
  const [start, setStart] = useState<string | null>(null);
  const [weeks, setWeeks] = useState(2);
  const first = start ?? (config ? mondayOf(config.as_of) : null);
  const q = useQuery({ queryKey: ["transports-planning", perimeter.planner, first, weeks], enabled: !!first,
    queryFn: () => api.get<PlanningResponse>("/api/transports/planning", { planner: perimeter.planner, start: first, weeks }) });
  const [picked, setPicked] = useState<{ supplier_id: string; date: string; ids: string[] } | null>(null);
  const [dialog, setDialog] = useState<{ supplier: PlanningSupplier; date: string; orders: PlanningOrder[] } | null>(null);
  const toast = useToast();
  const d = q.data;
  const byTransport = useMemo(() => new Map((d?.transports ?? []).map((t) => [t.transport_id, t])), [d]);
  const cancel = useWrite((id: string) => api.del<TransportOut>(`/api/transports/${encodeURIComponent(id)}`), (t) => toast.push(`Transport ${t.transport_id} annulé : prévenez le fournisseur et retirez-le de l'outil de transport`, "info"));
  const mailOf = async (id: string) => {
    try { window.location.href = mailtoOf((await api.get<TransportPackOut>(`/api/transports/${encodeURIComponent(id)}/pack`)).mail); }
    catch (e) { toast.push((e as Error).message, "error"); }
  };
  const toggle = (s: PlanningSupplier, o: PlanningOrder) => setPicked((p) => {
    const same = p && p.supplier_id === s.supplier_id && p.date === o.expected_date;
    const ids = same ? [...p.ids] : [];
    const i = ids.indexOf(o.order_id);
    if (i >= 0) ids.splice(i, 1); else ids.push(o.order_id);
    return ids.length ? { supplier_id: s.supplier_id, date: o.expected_date, ids } : null;
  });
  const openDialog = (s: PlanningSupplier, date: string) => {
    const ids = new Set(picked?.ids ?? []);
    setDialog({ supplier: s, date, orders: s.orders.filter((o) => o.expected_date === date && ids.has(o.order_id)) });
  };

  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  return (
    <div className="page">
      <div className="page-header">
        <div className="title">
          <h1>Planning de livraisons</h1>
          <p>Les ordres fermes ERP de chaque fournisseur, jour par jour. Cochez les commandes d'un jour puis <b>Créer le transport</b> : un ou plusieurs camions (date, heure, quais) ; l'application numérote les transports (EM…). Le fichier pour l'outil de transport et l'e-mail au fournisseur se prennent dans la liste des transports, ou en un seul fichier pour tous les transports à venir.</p>
        </div>
        <div className="actions">
          <a className="btn primary sm" href={api.downloadUrl("/api/transports/atr.csv")} download title="Fichier CSV pour l'outil de transport : tous les transports prévus à partir d'aujourd'hui, tous fournisseurs et tous approvisionneurs (une ligne par transport et par quai)"><FileDown />CSV ATR des transports à venir</a>
          <Button size="sm" onClick={() => first && setStart(addDays(first, -7 * weeks))} title="Période précédente"><ChevronLeft />Précédente</Button>
          <Button size="sm" onClick={() => setStart(null)} title="Revenir à la semaine en cours">Cette semaine</Button>
          <Button size="sm" onClick={() => first && setStart(addDays(first, 7 * weeks))} title="Période suivante">Suivante<ChevronRight /></Button>
          <select className="select sm" value={weeks} onChange={(e) => setWeeks(Number(e.target.value))} aria-label="Nombre de semaines" style={{ width: "auto" }}>
            {[1, 2, 3, 4].map((n) => <option key={n} value={n}>{n} semaine{n > 1 ? "s" : ""}</option>)}
          </select>
        </div>
      </div>

      <Card flush>
        {!d ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div> : d.suppliers.length === 0 ? <Empty title="Aucun ordre ferme sur la période" hint="Changez de période ou de périmètre." /> : (
          <div className="pivot planning" style={{ maxHeight: "calc(100vh - 330px)" }}>
            <table>
              <thead>
                <tr>
                  <th className="sticky-col">Fournisseur</th>
                  {d.days.map((day) => {
                    const dt = new Date(`${day}T00:00:00`);
                    const today = day === d.as_of, past = day < d.as_of;
                    return <th key={day} className={today ? "today" : past ? "past" : ""} title={fmtDate(day)}>{WEEKDAY_SHORT[(dt.getDay() + 6) % 7]} {fmtDate(day, "dd/MM")}</th>;
                  })}
                </tr>
              </thead>
              <tbody>
                {d.suppliers.map((s) => (
                  <tr key={s.supplier_id}>
                    <td className="sticky-col sup">
                      <SupplierLink id={s.supplier_id} name={s.name} />
                      <div className="small" style={{ marginTop: 4 }}>{s.emails.length ? <span className="subtle" title={s.emails.join("\n")}><Mail size={11} style={{ verticalAlign: -1 }} /> {s.emails.length} destinataire{s.emails.length > 1 ? "s" : ""}</span> : <Badge tone="warning" title="Renseignez la colonne « Contact transport » du fournisseur dans le Référentiel : l'e-mail n'aura pas de destinataire">sans contact transport</Badge>}<span className="subtle"> · quais {s.docks.join(", ")}</span></div>
                    </td>
                    {d.days.map((day) => {
                      const orders = s.orders.filter((o) => o.expected_date === day);
                      const here = picked && picked.supplier_id === s.supplier_id && picked.date === day ? picked.ids : [];
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
                          {here.length > 0 && <button className="btn primary sm" style={{ marginTop: 6, width: "100%" }} onClick={() => openDialog(s, day)}><Truck />Créer le transport ({here.length})</button>}
                        </td>
                      );
                    })}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {d && d.transports.length > 0 && (
        <Card title={`Transports de la période · ${d.transports.length}`} hint="fichier CSV et e-mail de chaque transport, annulation (le fournisseur et l'outil de transport sont à prévenir par vous)" tight>
          <table className="tbl compact">
            <thead><tr><th>Transport</th><th>Fournisseur</th><th>Date</th><th>Heure</th><th>Quais</th><th>Transporteur</th><th>Commandes</th><th>Créé par</th><th /></tr></thead>
            <tbody>
              {d.transports.map((t) => (
                <tr key={t.transport_id}>
                  <td><b className="mono">{t.transport_id}</b></td>
                  <td><SupplierLink id={t.supplier_id} name={t.supplier_name} inline /></td>
                  <td>{fmtDate(t.date)}</td><td>{t.time}</td><td>{t.docks.join(" / ")}</td><td className="small">{t.carrier || <span className="subtle">–</span>}</td>
                  <td className="small">{t.orders.map((o) => `${o.ref || o.order_id} · ${o.article_id}`).join(" ; ")}</td>
                  <td className="small subtle">{t.created_by}</td>
                  <td className="row" style={{ justifyContent: "flex-end" }}>
                    <a className="btn sm" href={api.downloadUrl(`/api/transports/${encodeURIComponent(t.transport_id)}/atr.csv`)} download title="Fichier CSV de ce transport pour l'outil de transport"><Download />CSV</a>
                    <Button size="sm" title="Ouvrir l'e-mail au fournisseur dans Outlook" onClick={() => mailOf(t.transport_id)}><Mail />E-mail</Button>
                    <Button size="sm" variant="ghost" title="Annuler ce transport" onClick={() => { if (window.confirm(`Annuler le transport ${t.transport_id} ?\nPrévenez le fournisseur et retirez-le de l'outil de transport.`)) cancel.mutate(t.transport_id); }}><Trash2 /></Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}

      {dialog && d && <TransportDialog supplier={dialog.supplier} date={dialog.date} orders={dialog.orders} docks={d.docks} onClose={() => setDialog(null)} onCreated={() => { setDialog(null); setPicked(null); }} />}
    </div>
  );
}

/** One or several trucks for the ticked orders of one supplier and one day ; the supplier's default docks are preselected. */
function TransportDialog({ supplier, date, orders, docks, onClose, onCreated }: { supplier: PlanningSupplier; date: string; orders: PlanningOrder[]; docks: string[]; onClose: () => void; onCreated: () => void }) {
  const toast = useToast();
  const preset = supplier.docks.filter((d) => docks.includes(d));
  const [slots, setSlots] = useState<TransportSlotIn[]>([{ date, time: DEFAULT_TIME, docks: preset.length ? preset : [docks[0]] }]);
  const [carrier, setCarrier] = useState("");
  const [note, setNote] = useState("");
  const run = useWrite((body: { supplier_id: string; order_ids: string[]; slots: TransportSlotIn[]; carrier: string; note: string }) => api.post<TransportPackOut>("/api/transports", body),
    (pack) => {
      const ids = pack.transports.map((t) => t.transport_id).join(", ");
      toast.push(`Transport ${ids} créé · fichier CSV et e-mail disponibles dans la liste des transports${pack.mail.to.length ? "" : " · e-mail SANS destinataire (contact transport du fournisseur à renseigner)"}`, pack.mail.to.length ? "success" : "info");
      onCreated();
    });
  const update = (i: number, patch: Partial<TransportSlotIn>) => setSlots((s) => s.map((x, j) => (j === i ? { ...x, ...patch } : x)));
  const valid = slots.every((s) => s.date && /^([01]\d|2[0-3]):[0-5]\d$/.test(s.time) && s.docks.length > 0) && orders.length > 0;
  return (
    <Drawer open onClose={onClose} wide title={<>Transport · {supplier.supplier_id} <span className="muted">{supplier.name}</span> · {fmtDate(date)}</>}
      footer={<><Button onClick={onClose}>Annuler</Button><Button variant="primary" disabled={!valid || run.isPending} onClick={() => run.mutate({ supplier_id: supplier.supplier_id, order_ids: orders.map((o) => o.order_id), slots, carrier, note })}>{run.isPending ? "En cours…" : `Créer ${slots.length} transport${slots.length > 1 ? "s" : ""}`}</Button></>}>
      <h4 style={{ margin: "0 0 6px" }}>Commandes du transport</h4>
      <table className="tbl compact">
        <thead><tr><th>Commande</th><th>Article</th><th className="num">Quantité</th></tr></thead>
        <tbody>{orders.map((o) => <tr key={o.order_id}><td className="mono small">{o.ref || o.order_id}</td><td><b>{o.article_id}</b> <span className="subtle">{o.designation}</span></td><td className="num">{fmtQty(o.qty_open, o.unit)} {o.unit}</td></tr>)}</tbody>
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
        <label className="field"><span>Transporteur (facultatif)</span><input className="input" value={carrier} onChange={(e) => setCarrier(e.target.value)} maxLength={120} placeholder="colonne TRANSPORTEUR du fichier ATR" /></label>
        <label className="field"><span>Commentaire pour le fournisseur (facultatif)</span><input className="input" value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="Ex. : prévenir le quai 30 min avant l'arrivée" /></label>
      </div>
      <p className="small subtle" style={{ marginTop: 10 }}>À la validation : un identifiant <b>EM</b> + 6 chiffres par camion. Le fichier CSV pour l'outil de transport (en-tête ATR, une ligne par transport et par quai, flux R) et l'e-mail au fournisseur ({supplier.emails.length ? <b>{supplier.emails.join(", ")}</b> : <b style={{ color: "var(--warning-fg)" }}>aucun destinataire : colonne « Contact transport » du fournisseur vide</b>}) se prennent ensuite dans la liste des transports, ou en un seul fichier avec le bouton <b>CSV ATR des transports à venir</b>.</p>
      {run.error && <div className="error-box">{(run.error as Error).message}</div>}
    </Drawer>
  );
}
