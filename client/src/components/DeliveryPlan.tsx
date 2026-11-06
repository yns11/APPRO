import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CalendarClock, Copy, CopyCheck } from "lucide-react";
import { useDeliveryPlan } from "@/lib/queries";
import { Card, Empty, ErrorBox, Segmented, SkeletonBlock, useToast } from "@/components/ui";
import { fmtQty } from "@/lib/format";
import type { DeliveryRow } from "@/lib/types";

type Locale = "FR" | "EN";
const COLS = ["Quantité livrée", "Unité", "Date de début de livraison", "Heure de début de livraison", "Date de fin de livraison", "Heure de fin de livraison"] as const;
const HOUR = "11:59:00 PM";

/** ``2026-08-31`` → ``31/08/2026`` (FR) or ``8/31/2026`` (EN, as the ERP shows it). */
export function fmtErpDate(iso: string, locale: Locale): string {
  const [y, m, d] = iso.split("-");
  return locale === "FR" ? `${d}/${m}/${y}` : `${Number(m)}/${Number(d)}/${y}`;
}
/** Quantity with the decimal separator of the target (``1440,5`` FR, ``1440.5`` EN), no thousands separator. */
export function fmtErpQty(v: number, locale: Locale): string {
  const s = Number.isInteger(v) ? String(v) : String(Math.round(v * 1000) / 1000);
  return locale === "FR" ? s.replace(".", ",") : s;
}
export function deliveryCells(r: DeliveryRow, unit: string, locale: Locale): string[] {
  return [fmtErpQty(r.qty, locale), unit, fmtErpDate(r.date, locale), HOUR, fmtErpDate(r.end_date, locale), HOUR];
}

type Pos = { r: number; c: number };
const FUTURE_KEY = "appro.delivery.futureOnly";

/**
 * « Planning de livraison » tab: the Plan row as ERP schedule lines, **one line per supplier and ISO
 * week** – everything the Plan row shows (typed cells, firm ERP orders taken over, CBN proposals)
 * added up over the week ; delivery window = Monday → Sunday, 11:59:00 PM.  The calendar button
 * keeps only the weeks starting after the reference day.  Cells are selected by dragging the mouse
 * like in Excel (click a row number or a column header to take the whole line / column) ; Ctrl+C
 * or the button copies the selection as tab-separated text, ready to paste in the ERP or in a
 * sheet.  FR / EN switches the date format (dd/mm/yyyy or m/d/yyyy) and the decimal separator.
 */
export function DeliveryPlan({ articleId }: { articleId: string }) {
  const q = useDeliveryPlan(articleId);
  const toast = useToast();
  const [locale, setLocale] = useState<Locale>(() => { try { return (localStorage.getItem("appro.delivery.locale") as Locale) || "FR"; } catch { return "FR"; } });
  useEffect(() => { try { localStorage.setItem("appro.delivery.locale", locale); } catch { /* private mode */ } }, [locale]);
  const [futureOnly, setFutureOnly] = useState<boolean>(() => { try { return localStorage.getItem(FUTURE_KEY) === "1"; } catch { return false; } });
  useEffect(() => { try { localStorage.setItem(FUTURE_KEY, futureOnly ? "1" : "0"); } catch { /* private mode */ } }, [futureOnly]);
  const [supplier, setSupplier] = useState("");
  const [anchor, setAnchor] = useState<Pos | null>(null);
  const [focus, setFocus] = useState<Pos | null>(null);
  const dragging = useRef(false);
  const [copied, setCopied] = useState(false);
  const wrap = useRef<HTMLDivElement>(null);

  const asOf = q.data?.as_of ?? "";
  const rows = useMemo(() => (q.data?.rows ?? []).filter((r) => (!supplier || (r.supplier_id ?? "") === supplier) && (!futureOnly || r.date > asOf)), [q.data, supplier, futureOnly, asOf]);
  const unit = q.data?.unit ?? "";
  const cells = useMemo(() => rows.map((r) => deliveryCells(r, unit, locale)), [rows, unit, locale]);
  useEffect(() => { setAnchor(null); setFocus(null); }, [rows.length, futureOnly, supplier]);
  const range = useMemo(() => {
    if (!anchor || !focus) return null;
    return { r0: Math.min(anchor.r, focus.r), r1: Math.max(anchor.r, focus.r), c0: Math.min(anchor.c, focus.c), c1: Math.max(anchor.c, focus.c) };
  }, [anchor, focus]);
  const inRange = (r: number, c: number) => !!range && r >= range.r0 && r <= range.r1 && c >= range.c0 && c <= range.c1;
  const selectionText = useCallback((all = false) => {
    const rg = all || !range ? { r0: 0, r1: cells.length - 1, c0: 0, c1: COLS.length - 1 } : range;
    return cells.slice(rg.r0, rg.r1 + 1).map((row) => row.slice(rg.c0, rg.c1 + 1).join("\t")).join("\n");
  }, [cells, range]);
  const copy = useCallback(async (all = false) => {
    const text = selectionText(all);
    if (!text) return;
    try { await navigator.clipboard.writeText(text); } catch {
      const ta = document.createElement("textarea"); ta.value = text; document.body.appendChild(ta); ta.select(); document.execCommand("copy"); ta.remove();
    }
    const nRows = text.split("\n").length;
    setCopied(true); setTimeout(() => setCopied(false), 1500);
    toast.push(`${nRows} ligne(s) copiée(s) : coller dans l'ERP ou dans Excel`, "success");
  }, [selectionText, toast]);

  useEffect(() => {
    const up = () => { dragging.current = false; };
    window.addEventListener("mouseup", up);
    return () => window.removeEventListener("mouseup", up);
  }, []);

  const start = (p: Pos) => { dragging.current = true; setAnchor(p); setFocus(p); wrap.current?.focus(); };
  const over = (p: Pos) => { if (dragging.current) setFocus(p); };
  const selectAll = () => { setAnchor({ r: 0, c: 0 }); setFocus({ r: cells.length - 1, c: COLS.length - 1 }); wrap.current?.focus(); };
  const selectCol = (c: number) => { setAnchor({ r: 0, c }); setFocus({ r: cells.length - 1, c }); wrap.current?.focus(); };
  const selectRow = (r: number) => { setAnchor({ r, c: 0 }); setFocus({ r, c: COLS.length - 1 }); wrap.current?.focus(); };
  const composition = (r: DeliveryRow) => [r.typed_qty > 0 ? `saisi ${fmtQty(r.typed_qty, unit)}` : "", r.erp_qty > 0 ? `ferme ERP ${fmtQty(r.erp_qty, unit)}` : "", r.cbn_qty > 0 ? `proposition CBN ${fmtQty(r.cbn_qty, unit)}` : ""].filter(Boolean).join(" + ");

  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  if (q.isLoading || !q.data) return <Card><SkeletonBlock /></Card>;
  const several = q.data.suppliers.length > 1;
  const total = q.data.rows.length, hiddenPast = total - q.data.rows.filter((r) => r.date > asOf).length;
  return (
    <Card flush title="Planning de livraison" hint="une ligne par semaine ISO et par fournisseur : tout ce que porte la ligne Appro. (saisies, fermes ERP reprises, propositions CBN) du lundi au dimanche ; sélectionner une plage à la souris puis Ctrl+C, ou « Copier »"
      actions={<>
        {several && <select className="select sm" value={supplier} onChange={(e) => setSupplier(e.target.value)} aria-label="Fournisseur"><option value="">Tous les fournisseurs</option>{q.data.suppliers.map((s) => <option key={s.supplier_id ?? ""} value={s.supplier_id ?? ""}>{s.supplier_id} · {s.name}</option>)}</select>}
        <button className={`btn sm ${futureOnly ? "primary" : ""}`} onClick={() => setFutureOnly((v) => !v)} aria-pressed={futureOnly} aria-label="Semaines à venir seulement"
          title={futureOnly ? `Semaines commençant après aujourd'hui (${hiddenPast} masquée(s)) – cliquer pour tout afficher` : "N'afficher que les semaines commençant après aujourd'hui"}><CalendarClock /></button>
        <Segmented size="sm" value={locale} onChange={setLocale} options={[{ id: "FR", label: "FR" }, { id: "EN", label: "EN" }]} />
        <button className="btn sm" onClick={() => copy(false)} disabled={!range} title="Copier la sélection (Ctrl+C)">{copied ? <CopyCheck /> : <Copy />}Copier la sélection</button>
        <button className="btn sm" onClick={() => copy(true)} disabled={!cells.length}>Tout copier</button>
      </>}>
      {rows.length === 0 ? <Empty title="Aucune livraison planifiée" hint={futureOnly && total ? "Aucune semaine après aujourd'hui : désactiver le filtre pour voir la semaine en cours." : "La ligne Appro. ne contient aucune quantité sur l'horizon."} /> : (
        <div className="dgrid-wrap" ref={wrap} tabIndex={0} onKeyDown={(e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "c") { e.preventDefault(); copy(false); } else if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "a") { e.preventDefault(); selectAll(); } }}
          onCopy={(e) => { const t = selectionText(false); if (t) { e.clipboardData.setData("text/plain", t); e.preventDefault(); } }}>
          <table className="dgrid" aria-label="Planning de livraison">
            <thead>
              <tr>
                <th className="rowno" onClick={selectAll} title="Tout sélectionner">#</th>
                {COLS.map((c, ci) => <th key={c} onClick={() => selectCol(ci)} title="Sélectionner la colonne">{c}</th>)}
                <th>Semaine</th>
                {several && !supplier && <th>Fournisseur</th>}
              </tr>
            </thead>
            <tbody>
              {rows.map((r, ri) => (
                <tr key={`${r.supplier_id}-${r.date}`} className={r.date <= asOf ? "current" : ""}>
                  <td className="rowno" onClick={() => selectRow(ri)} title="Sélectionner la ligne">{ri + 1}</td>
                  {cells[ri].map((v, ci) => (
                    <td key={ci} className={[ci === 0 ? "num" : "", inRange(ri, ci) ? "sel" : "", ci === 0 && r.typed ? "typed" : ""].filter(Boolean).join(" ")}
                      onMouseDown={(e) => { e.preventDefault(); start({ r: ri, c: ci }); }} onMouseEnter={() => over({ r: ri, c: ci })}
                      title={ci === 0 ? composition(r) : undefined}>{v}</td>
                  ))}
                  <td className="subtle">{r.week.replace("-W", " S")}{r.date <= asOf && <span className="small"> · en cours</span>}</td>
                  {several && !supplier && <td className="subtle">{r.supplier_id} · {r.supplier_name}</td>}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
