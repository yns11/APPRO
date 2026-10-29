import { useEffect, useMemo, useState } from "react";
import { Pencil, Plus, Save, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { useRefRows, useRefTables, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { ArticleLink, Badge, Button, Card, Skeleton, useToast } from "@/components/ui";
import { RefRowEditor } from "@/components/RefRowEditor";
import { DOCKS, DockPicker, splitDocks } from "@/components/DockPicker";
import { WeekdayPicker, splitWeekdays } from "@/components/WeekdayPicker";
import { fmtQty, fmtWeekdays } from "@/lib/format";
import type { RefRow, RefValue, SupplierArticleRow } from "@/lib/types";

const FIELDS: { key: string; label: string; help: string; width?: number }[] = [
  { key: "name", label: "Nom", help: "raison sociale", width: 320 },
  { key: "country", label: "Pays", help: "code pays", width: 80 },
  { key: "contact", label: "Contact", help: "contact commercial ou logistique (texte libre)", width: 320 },
  { key: "transport_contact", label: "Contact transport", help: "adresses e-mail des avis de transport, séparées par ; ou , ; elles donnent accès à l'espace fournisseur", width: 420 },
  { key: "lead_time_days", label: "Délai (j ouvrés)", help: "délai commande → livraison, inclusif (lundi + 5 = lundi suivant) ; valeur par défaut des liens", width: 80 },
];

/**
 * Fiche fournisseur › Paramétrage : the supplier's reference row edited in place (name, country, contacts, docks,
 * delivery weekdays, strict days, lead time, active) and the article ↔ supplier rules of the supplier (add, edit, delete).
 * Open to every declared planner, like the Référentiel.
 */
export function SupplierParamsTab({ supplierId, articles }: { supplierId: string; articles: SupplierArticleRow[] }) {
  const toast = useToast();
  const { rights } = usePerimeter();
  const tables = useRefTables();
  const supTable = tables.data?.find((t) => t.name === "ref_suppliers");
  const linkTable = tables.data?.find((t) => t.name === "ref_article_suppliers");
  const rows = useRefRows("ref_suppliers");
  const row = useMemo(() => (rows.data ?? []).find((r) => String(r.supplier_id) === supplierId), [rows.data, supplierId]);
  const canEdit = rights.canEditTable("ref_suppliers");
  const [draft, setDraft] = useState<Record<string, RefValue>>({});
  useEffect(() => { if (row) setDraft(Object.fromEntries((supTable?.columns ?? []).map((c) => [c.name, row[c.name] ?? (c.type === "bool" ? false : "")]))); }, [row, supTable]);
  const dirty = useMemo(() => !!row && Object.keys(draft).some((k) => String(draft[k] ?? "") !== String(row[k] ?? "")), [draft, row]);
  const save = useWrite((values: Record<string, RefValue>) => api.put("/api/reference/ref_suppliers/rows", { values }), () => toast.push("Fiche fournisseur enregistrée", "success"));
  const set = (k: string, v: RefValue) => setDraft((d) => ({ ...d, [k]: v }));
  const [editLink, setEditLink] = useState<RefRow | null>(null);
  const delLink = useWrite((aid: string) => api.post("/api/reference/ref_article_suppliers/delete", { key: { article_id: aid, supplier_id: supplierId } }), () => toast.push("Lien supprimé"));
  const linkRow = (a: SupplierArticleRow): RefRow => ({ article_id: a.article_id, supplier_id: supplierId, moq: a.moq, pack_qty: a.pack_qty, lead_time_override: a.lead_time_override, quota_pct: a.quota_pct, priority: a.priority, max_qty_per_delivery: a.max_qty_per_delivery, max_qty_per_week: a.max_qty_per_week, active: a.active });

  if (!row || !supTable) return <Card><Skeleton h={120} /></Card>;
  return (
    <div className="stack">
      {!canEdit && <div className="note">Lecture seule : les données du fournisseur sont modifiables par les approvisionneurs déclarés, les managers et les administrateurs.</div>}
      <Card title="Fiche fournisseur" hint="données du Référentiel ; Enregistrer applique l'ensemble des champs"
        actions={<Button size="sm" variant="primary" disabled={!canEdit || !dirty || save.isPending} onClick={() => save.mutate({ ...row, ...draft, delivery_weekdays: String(draft.delivery_weekdays || "1,2,3,4,5"), docks: String(draft.docks || "B15") })}><Save />Enregistrer</Button>}>
        <div className="param-grid identity">
          <div className="param"><span className="label">Code</span><span className="value"><b className="mono">{supplierId}</b></span></div>
          {FIELDS.map((f) => (
            <div key={f.key} className={`param ${canEdit ? "editable" : ""}`} title={f.help}>
              <span className="label">{f.label}</span>
              <span className="value"><input className="input sm" type={f.key === "lead_time_days" ? "number" : "text"} min={f.key === "lead_time_days" ? 1 : undefined} value={String(draft[f.key] ?? "")} disabled={!canEdit} style={{ width: f.width }} aria-label={f.label}
                onChange={(e) => set(f.key, f.key === "lead_time_days" ? Number(e.target.value) : e.target.value)} /></span>
              <span className="small subtle">{f.help}</span>
            </div>
          ))}
          <div className={`param ${canEdit ? "editable" : ""}`} title="Jours de livraison standard : la saisie en colonne semaine se pose sur le premier de ces jours">
            <span className="label">Jours de livraison</span>
            <span className="value row"><WeekdayPicker value={splitWeekdays(draft.delivery_weekdays)} disabled={!canEdit} onChange={(v) => set("delivery_weekdays", v.join(","))} /><span className="small subtle">{fmtWeekdays(String(draft.delivery_weekdays ?? ""))}</span></span>
            <span className="small subtle">cases Lun → Ven</span>
          </div>
          <div className={`param ${canEdit ? "editable" : ""}`} title="Jours stricts : jamais de proposition hors jour standard (◇)">
            <span className="label">Jours stricts</span>
            <span className="value"><select className="select sm" value={draft.strict_days ? "oui" : "non"} disabled={!canEdit} aria-label="Jours stricts" onChange={(e) => set("strict_days", e.target.value === "oui")}><option value="non">non</option><option value="oui">oui</option></select></span>
            <span className="small subtle">oui : livraisons uniquement les jours standard</span>
          </div>
          <div className={`param ${canEdit ? "editable" : ""}`} title="Quais présélectionnés à la création d'un transport">
            <span className="label">Quais par défaut</span>
            <span className="value"><DockPicker all={DOCKS} value={splitDocks(draft.docks)} disabled={!canEdit} onChange={(v) => set("docks", v.join(";"))} label="Quais par défaut" /></span>
            <span className="small subtle">Planning de livraisons</span>
          </div>
          <div className={`param ${canEdit ? "editable" : ""}`}>
            <span className="label">Actif</span>
            <span className="value"><select className="select sm" value={draft.active === false ? "non" : "oui"} disabled={!canEdit} aria-label="Actif" onChange={(e) => set("active", e.target.value === "oui")}><option value="oui">oui</option><option value="non">non</option></select></span>
          </div>
        </div>
      </Card>

      <Card flush title="Articles & règles d'approvisionnement" hint="MOQ, UM, délai spécifique (vide = délai du fournisseur), quota, priorité, plafonds ; une ligne par article livré par ce fournisseur"
        actions={canEdit && linkTable ? <Button size="sm" variant="primary" onClick={() => setEditLink({})}><Plus />Ajouter un article</Button> : undefined}>
        <table className="tbl compact">
          <thead><tr><th>Article</th><th>Approvisionneur</th><th className="num">MOQ</th><th className="num">UM</th><th className="num">Délai (j ouvrés)</th><th className="num">Quota</th><th className="num">Priorité</th><th className="num">Max / livraison</th><th className="num">Max / semaine</th><th>Actif</th><th /></tr></thead>
          <tbody>
            {articles.length === 0 && <tr><td colSpan={11} className="subtle">Aucun article lié à ce fournisseur.</td></tr>}
            {articles.map((a) => (
              <tr key={a.article_id} className={a.active ? "" : "muted"}>
                <td><ArticleLink id={a.article_id} designation={a.designation} /></td>
                <td className="small subtle">{a.planner}</td>
                <td className="num">{fmtQty(a.moq, a.unit)}</td>
                <td className="num">{fmtQty(a.pack_qty, a.unit)}</td>
                <td className="num">{a.lead_time_override === null ? <span className="inherited" title="Délai du fournisseur (hérité)">{a.lead_time_days}</span> : <b title="Délai spécifique au lien">{a.lead_time_days}</b>}</td>
                <td className="num">{a.quota_pct} %</td>
                <td className="num">{a.priority}</td>
                <td className="num">{a.max_qty_per_delivery === null ? <span className="subtle">–</span> : fmtQty(a.max_qty_per_delivery, a.unit)}</td>
                <td className="num">{a.max_qty_per_week === null ? <span className="subtle">–</span> : fmtQty(a.max_qty_per_week, a.unit)}</td>
                <td><Badge tone={a.active ? "ok" : "neutral"}>{a.active ? "oui" : "non"}</Badge></td>
                <td>{canEdit && linkTable && <><Button size="xs" variant="ghost" title="Modifier la règle" onClick={() => setEditLink(linkRow(a))}><Pencil /></Button><Button size="xs" variant="ghost" title="Supprimer le lien" onClick={() => { if (window.confirm(`Supprimer le lien ${a.article_id} ↔ ${supplierId} ?`)) delLink.mutate(a.article_id); }}><Trash2 /></Button></>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
      {linkTable && <RefRowEditor table={linkTable} row={editLink} onClose={() => setEditLink(null)} fixed={{ supplier_id: supplierId }} />}
    </div>
  );
}
