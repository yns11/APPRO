import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { useLinks, usePrograms, useWrite, useOrders } from "@/lib/queries";
import { Button, Drawer, Field, useToast } from "@/components/ui";

export type EntryKind = "order" | "receipt" | "adjustment" | "production";
export interface EntryDraft { kind: EntryKind; article_id?: string; supplier_id?: string | null; date?: string; qty?: number; program_id?: string; order_id?: string | null; note?: string; order_type?: "FIRM"; }

const TITLES: Record<EntryKind, string> = { order: "Nouvelle commande ferme", receipt: "Nouvelle réception", adjustment: "Ajustement de stock", production: "Production réelle" };

/** One drawer for the four planner entries (order, receipt, adjustment, actual production). */
export function EntryDrawer({ draft, onClose, articles }: { draft: EntryDraft | null; onClose: () => void; articles: { article_id: string; designation: string; unit: string }[] }) {
  const toast = useToast();
  const [form, setForm] = useState<EntryDraft>({ kind: "order" });
  useEffect(() => { if (draft) setForm({ note: "", order_type: "FIRM", ...draft }); }, [draft]);
  const { data: links } = useLinks(form.article_id);
  const { data: programs } = usePrograms();
  const { data: openOrders } = useOrders(form.kind === "receipt" && form.article_id ? { article_id: form.article_id, status: "OPEN" } : undefined);
  const write = useWrite(async (f: EntryDraft) => {
    switch (f.kind) {
      case "order": return api.post("/api/entries/orders", { article_id: f.article_id, supplier_id: f.supplier_id || null, expected_date: f.date, qty: f.qty, order_type: "FIRM", note: f.note ?? "" });
      case "receipt": return api.post("/api/entries/receipts", { article_id: f.article_id, supplier_id: f.supplier_id || null, order_id: f.order_id || null, receipt_date: f.date, qty: f.qty, note: f.note ?? "" });
      case "adjustment": return api.post("/api/entries/adjustments", { article_id: f.article_id, date: f.date, qty: f.qty, comment: f.note ?? "" });
      case "production": return api.post("/api/entries/production", { program_id: f.program_id, date: f.date, qty: f.qty });
    }
  }, () => { toast.push("Saisie enregistrée", "success"); onClose(); });

  if (!draft) return null;
  const set = (patch: Partial<EntryDraft>) => setForm((f) => ({ ...f, ...patch }));
  const unit = articles.find((a) => a.article_id === form.article_id)?.unit ?? "";
  const valid = !!form.date && form.qty !== undefined && !Number.isNaN(form.qty) && (form.kind === "production" ? !!form.program_id : !!form.article_id) && (form.kind === "adjustment" ? form.qty !== 0 : (form.qty ?? 0) > 0 || form.kind === "production");

  return (
    <Drawer open onClose={onClose} title={TITLES[form.kind]}
      footer={<><Button onClick={onClose}>Annuler</Button><Button variant="primary" disabled={!valid || write.isPending} onClick={() => write.mutate(form)}>{write.isPending ? "Enregistrement…" : "Enregistrer"}</Button></>}>
      <div className="form-grid">
        <Field label="Type" span2>
          <select className="select" value={form.kind} onChange={(e) => set({ kind: e.target.value as EntryKind })}>
            <option value="order">Commande ferme (passée au fournisseur)</option>
            <option value="receipt">Réception</option>
            <option value="adjustment">Ajustement de stock (±)</option>
            <option value="production">Production réelle d'un programme</option>
          </select>
        </Field>
        {form.kind === "production" ? (
          <Field label="Programme" span2>
            <select className="select" value={form.program_id ?? ""} onChange={(e) => set({ program_id: e.target.value })}>
              <option value="">— choisir —</option>
              {(programs ?? []).filter((p) => p.components > 0).map((p) => <option key={p.program_id} value={p.program_id}>{p.name} ({p.program_id})</option>)}
            </select>
          </Field>
        ) : (
          <Field label="Article" span2>
            <select className="select" value={form.article_id ?? ""} onChange={(e) => set({ article_id: e.target.value, supplier_id: null })}>
              <option value="">— choisir —</option>
              {articles.map((a) => <option key={a.article_id} value={a.article_id}>{a.article_id} · {a.designation}</option>)}
            </select>
          </Field>
        )}
        {(form.kind === "order" || form.kind === "receipt") && (
          <Field label="Fournisseur">
            <select className="select" value={form.supplier_id ?? ""} onChange={(e) => set({ supplier_id: e.target.value || null })}>
              <option value="">— non précisé —</option>
              {(links ?? []).map((l) => <option key={l.supplier_id} value={l.supplier_id}>{l.supplier_id} · {l.supplier_name} (délai {l.lead_time_days} j)</option>)}
            </select>
          </Field>
        )}
        {form.kind === "order" && (
          <Field label="Nature" help="Commande réelle, comptée dans le stock ferme. Pour simuler, saisir dans la ligne Commandes simulées du tableau.">
            <div className="input" style={{ display: "flex", alignItems: "center" }}>Ferme (passée au fournisseur)</div>
          </Field>
        )}
        {form.kind === "receipt" && (
          <Field label="Commande soldée (optionnel)" help="Une réception rattachée à une commande la solde">
            <select className="select" value={form.order_id ?? ""} onChange={(e) => set({ order_id: e.target.value || null })}>
              <option value="">— aucune —</option>
              {(openOrders ?? []).map((o) => <option key={o.id} value={o.id}>{o.id} · {o.expected_date} · {o.qty}</option>)}
            </select>
          </Field>
        )}
        <Field label={form.kind === "order" ? "Date de livraison attendue" : "Date"}>
          <input className="input" type="date" value={form.date ?? ""} onChange={(e) => set({ date: e.target.value })} />
        </Field>
        <Field label={`Quantité${unit ? ` (${unit})` : ""}`} help={form.kind === "adjustment" ? "Négatif pour une sortie / casse" : undefined}>
          <input className="input" type="number" step="any" value={form.qty ?? ""} onChange={(e) => set({ qty: e.target.value === "" ? undefined : Number(e.target.value) })} />
        </Field>
        {form.kind !== "production" && (
          <Field label="Commentaire" span2><input className="input" value={form.note ?? ""} onChange={(e) => set({ note: e.target.value })} placeholder="Motif, référence, remarque…" /></Field>
        )}
      </div>
      {write.error && <div className="error-box">{(write.error as Error).message}</div>}
    </Drawer>
  );
}
