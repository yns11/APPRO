import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import { useConsumptionSources, useRefRows, useWrite } from "@/lib/queries";
import { Button, Drawer, Field, useToast } from "@/components/ui";
import { CONSUMPTION_SOURCE_LABELS, SOURCING_LABELS, WEEKDAY_SHORT } from "@/lib/format";
import type { RefColumn, RefRow, RefTableInfo } from "@/lib/types";

/** Choice lists of the reference columns that are not free text. */
const CHOICES: Record<string, Record<string, { value: string; label: string }[]>> = {
  ref_planners: { role: [{ value: "appro", label: "appro – écrit sur son carnet" }, { value: "manager", label: "manager – + paramètres pour tous, import PDP" }, { value: "admin", label: "admin – tous les droits" }] },
  ref_articles: {
    sourcing_policy: Object.entries(SOURCING_LABELS).map(([value, label]) => ({ value, label: `${label}${value === "quota" ? " – chaque besoin réparti selon les quotas des liens" : " – le fournisseur prioritaire d'abord"}` })),
    consumption_source: Object.entries(CONSUMPTION_SOURCE_LABELS).map(([value, label]) => ({ value, label })),
    demand_days: [1, 2, 3, 4, 5].map((n) => ({ value: String(n), label: n === 5 ? "5 – toute la semaine" : `${n} – ${WEEKDAY_SHORT.slice(0, n).join(", ")}` })),
  },
};

/**
 * Create / edit one row of a reference table (typed inputs from the column definitions).  Some
 * columns are choice lists ; the source article of a consumption term is picked among the eligible
 * articles ; ``fixed`` values are set by the caller (e.g. the article of the page) and not editable.
 */
export function RefRowEditor({ table, row, onClose, fixed, onSaved }: { table: RefTableInfo; row: RefRow | null; onClose: () => void; fixed?: Record<string, string>; onSaved?: () => void }) {
  const toast = useToast();
  const [values, setValues] = useState<Record<string, string>>({});
  const isNew = !!row && table.key.every((k) => row[k] === undefined);
  useEffect(() => {
    if (!row) return;
    const v: Record<string, string> = {};
    table.columns.forEach((c) => { const x = row[c.name]; v[c.name] = x === undefined || x === null ? (c.type === "bool" ? "oui" : c.name === "demand_days" ? "5" : c.name === "sourcing_policy" ? "quota" : c.name === "consumption_source" ? "erp" : "") : c.type === "bool" ? (x ? "oui" : "non") : String(x); });
    setValues({ ...v, ...(fixed ?? {}) });
  }, [row, table, fixed]);
  const save = useWrite((vals: Record<string, string>) => api.put(`/api/reference/${table.name}/rows`, { values: vals }), () => { toast.push("Ligne enregistrée", "success"); onSaved?.(); onClose(); });
  const termArticle = table.name === "ref_consumption_terms" ? (values.article_id || fixed?.article_id || "") : "";
  const sources = useConsumptionSources(termArticle || undefined, !!termArticle);
  const suppliers = useRefRows(table.name === "ref_article_suppliers" || table.name === "ref_articles" ? "ref_suppliers" : null);
  const supplierLead = useMemo(() => { const s = (suppliers.data ?? []).find((r) => String(r.supplier_id) === values.supplier_id); return s ? Number(s.lead_time_days ?? 5) : null; }, [suppliers.data, values.supplier_id]);
  const choices = CHOICES[table.name] ?? {};
  const control = (c: RefColumn) => {
    const set = (v: string) => setValues({ ...values, [c.name]: v });
    const disabled = (c.key && !isNew) || !!fixed?.[c.name];
    if (c.type === "bool") return <select className="select" value={values[c.name] ?? "oui"} onChange={(e) => set(e.target.value)} disabled={disabled}><option value="oui">oui</option><option value="non">non</option></select>;
    if (choices[c.name]) return <select className="select" value={values[c.name] || choices[c.name][0].value} onChange={(e) => set(e.target.value)} disabled={disabled}>{choices[c.name].map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select>;
    if (table.name === "ref_consumption_terms" && c.name === "source_article_id") {
      return (
        <select className="select" value={values[c.name] ?? ""} onChange={(e) => set(e.target.value)} disabled={disabled || !termArticle}>
          <option value="">{termArticle ? (sources.isLoading ? "chargement…" : sources.data?.length ? "choisir un article source…" : "aucun article éligible") : "renseigner d'abord l'article"}</option>
          {(sources.data ?? []).map((s) => <option key={s.article_id} value={s.article_id}>{s.article_id} · {s.designation} ({s.shared_programs.join(", ")})</option>)}
          {values[c.name] && !(sources.data ?? []).some((s) => s.article_id === values[c.name]) && <option value={values[c.name]}>{values[c.name]} (non éligible)</option>}
        </select>
      );
    }
    const placeholder = c.name === "lead_time_override" ? (supplierLead !== null ? `vide = délai du fournisseur (${supplierLead} j)` : "vide = délai du fournisseur") : c.name.startsWith("max_qty") ? "vide = aucun plafond" : undefined;
    return <input className="input" type={c.type === "date" ? "date" : c.type === "float" || c.type === "int" ? "number" : "text"} step={c.type === "float" ? "any" : undefined} value={values[c.name] ?? ""} disabled={disabled} placeholder={placeholder} onChange={(e) => set(e.target.value)} />;
  };
  return (
    <Drawer open={!!row} onClose={onClose} title={`${isNew ? "Ajouter" : "Modifier"} · ${table.label}`}
      footer={<><Button onClick={onClose}>Annuler</Button><Button variant="primary" disabled={save.isPending} onClick={() => save.mutate(values)}>Enregistrer</Button></>}>
      <div className="form-grid">
        {table.columns.map((c) => (
          <Field key={c.name} label={`${c.label}${c.required ? " *" : ""}`} help={c.description}>{control(c)}</Field>
        ))}
      </div>
      {save.error && <div className="error-box">{(save.error as Error).message}</div>}
      <p className="small subtle">* obligatoire · les colonnes clé ({table.key.join(", ")}) identifient la ligne et ne se modifient pas.</p>
    </Drawer>
  );
}
