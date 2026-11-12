import { useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useWrite } from "@/lib/queries";
import { Button, Drawer, useToast } from "@/components/ui";
import { fmtDate, fmtQty } from "@/lib/format";
import type { CalibrateOut } from "@/lib/types";

/** One article offered to « Caler sur l'ERP » : its last ERP snapshot against the projected stock of the same day. */
export interface CalibrateRow { article_id: string; designation: string; unit: string; erp_qty: number; erp_date: string; projected: number; gap: number; locked?: boolean; }

/**
 * « Caler sur l'ERP » : for each selected article, one signed adjustment dated the last ERP snapshot and equal to
 * the gap ERP − projected (added to an adjustment already on that day) : the projected stock meets the ERP stock,
 * the CBN and the alerts recompute.  One undoable action, journalised ; the point zero and the ERP are untouched.
 */
export function CalibrateDialog({ rows, onClose, title }: { rows: CalibrateRow[]; onClose: () => void; title?: string }) {
  const toast = useToast();
  const [picked, setPicked] = useState<Set<string>>(() => new Set(rows.filter((r) => !r.locked && Math.abs(r.gap) > 1e-9).map((r) => r.article_id)));
  const [note, setNote] = useState("");
  const chosen = useMemo(() => rows.filter((r) => picked.has(r.article_id)), [rows, picked]);
  const run = useWrite((ids: string[]) => api.post<CalibrateOut>("/api/entries/adjustments/calibrate", { article_ids: ids, note: note.trim() || undefined }),
    (out) => {
      const n = out.created.length;
      toast.push(n ? `${n} ajustement(s) de calage créé(s)${out.skipped.length ? ` · ${out.skipped.length} article(s) ignoré(s) : ${out.skipped.map((s) => `${s.article_id} (${s.reason})`).join(", ")}` : ""}` : `Aucun calage : ${out.skipped.map((s) => `${s.article_id} (${s.reason})`).join(", ")}`, n ? "success" : "info");
      onClose();
    });
  const toggle = (id: string) => setPicked((p) => { const n = new Set(p); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const one = rows.length === 1 ? rows[0] : null;
  const date = rows[0]?.erp_date ?? "";
  return (
    <Drawer open onClose={onClose} title={title ?? (one ? `Caler ${one.article_id} sur l'ERP` : `Caler ${rows.length} article(s) sur l'ERP`)}
      footer={<><Button onClick={onClose}>Annuler</Button><Button variant="primary" disabled={!chosen.length || run.isPending} onClick={() => run.mutate(chosen.map((r) => r.article_id))}>{run.isPending ? "En cours…" : one ? `Créer l'ajustement ${fmtSigned(one.gap, one.unit)}` : `Créer ${chosen.length} ajustement(s)`}</Button></>}>
      <table className="tbl compact">
        <thead><tr>{!one && <th />}<th>Article</th><th className="num">Stock ERP du {fmtDate(date)}</th><th className="num">Projeté</th><th className="num">Ajustement</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.article_id} className={r.locked ? "muted" : ""}>
              {!one && <td><input type="checkbox" checked={picked.has(r.article_id)} disabled={r.locked || Math.abs(r.gap) < 1e-9} onChange={() => toggle(r.article_id)} aria-label={r.article_id} /></td>}
              <td><b>{r.article_id}</b><span className="sub">{r.designation}{r.locked ? " · hors de votre carnet" : Math.abs(r.gap) < 1e-9 ? " · déjà aligné" : ""}</span></td>
              <td className="num">{fmtQty(r.erp_qty, r.unit)}</td>
              <td className="num">{fmtQty(r.projected, r.unit)}</td>
              <td className="num" style={{ color: r.gap < 0 ? "var(--warning-fg)" : "var(--info-fg)", fontWeight: 600 }}>{fmtSigned(r.gap, r.unit)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <label className="field" style={{ marginTop: 12 }}><span>Commentaire</span>
        <input className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder={`Calage sur le stock ERP du ${fmtDate(date)} (écart …)`} maxLength={200} />
      </label>
      <p className="small subtle" style={{ marginTop: 10 }}>Un ajustement signé par article, daté du <b>{fmtDate(date)}</b> (le jour du snapshot) : le stock projeté sera égal au stock ERP ce jour-là, le CBN et les alertes se recalculent. Il apparaît dans la ligne <b>Ajustement</b>, l'onglet <b>Ajustements</b> et <b>Saisies &amp; journal</b> ; <b>annulable</b> (↶, Ctrl+Z), une seule action pour la liste. Le point zéro et l'ERP ne sont pas modifiés.</p>
      {run.error && <div className="error-box">{(run.error as Error).message}</div>}
    </Drawer>
  );
}

export function fmtSigned(v: number, unit?: string): string {
  return `${v > 0 ? "+" : v < 0 ? "−" : ""}${fmtQty(Math.abs(v), unit)}`;
}

/** Signed percentage, French style : « −12,7 % », « +8 % », « – » when unknown. */
export function fmtSignedPct(p: number | null | undefined): string {
  if (p === null || p === undefined || !Number.isFinite(p)) return "–";
  return `${p > 0 ? "+" : p < 0 ? "−" : ""}${Math.abs(p).toLocaleString("fr-FR", { maximumFractionDigits: 1 })} %`;
}
