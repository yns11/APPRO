import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { CalendarDays, Download, Plus, Trash2, Upload, Wand2 } from "lucide-react";
import { WeeklyParamsDrawer } from "@/components/WeeklyParamsDrawer";
import { RefRowEditor } from "@/components/RefRowEditor";
import { DataTable, type Column } from "@/components/DataTable";
import { useRefRows, useRefTables, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { useRemembered } from "@/lib/pageState";
import { api } from "@/lib/api";
import { Badge, Button, Card, Drawer, ErrorBox, Field, SkeletonBlock, SupplierLink, Tabs, useToast } from "@/components/ui";
import { CONSUMPTION_SOURCE_SHORT, SOURCING_LABELS, fmtDate, fmtDateTime, fmtDemandDays, fmtQty, fmtWeekdays } from "@/lib/format";
import type { ImportReport, RefColumn, RefRow, RefTableInfo, RefValue } from "@/lib/types";

/** Quick filters of the Référentiel tables, on the query columns the server computes (several buttons combine with AND). */
const QUICK_FILTERS: Record<string, { id: string; label: string; title: string; test: (r: RefRow) => boolean }[]> = {
  ref_articles: [
    { id: "no_supplier", label: "Sans fournisseur", title: "Articles sans règle article ↔ fournisseur active", test: (r) => Number(r.supplier_count ?? 0) === 0 },
    { id: "multi", label: "Multi-sourcing", title: "Articles ayant au moins deux fournisseurs actifs", test: (r) => Number(r.supplier_count ?? 0) >= 2 },
    { id: "no_program", label: "Sans programme", title: "Articles qu'aucune nomenclature n'utilise", test: (r) => Number(r.program_count ?? 0) === 0 },
    { id: "source", label: "Conso dérivée / PDP", title: "Articles dont la consommation ne vient pas de l'ERP", test: (r) => String(r.consumption_source ?? "erp") !== "erp" },
    { id: "days", label: "Jours conso ≠ 5", title: "Articles dont le besoin hebdomadaire est réparti sur moins de cinq jours", test: (r) => Number(r.demand_days ?? 5) !== 5 },
  ],
  ref_suppliers: [
    { id: "no_article", label: "Sans article", title: "Fournisseurs sans règle article ↔ fournisseur active", test: (r) => Number(r.article_count ?? 0) === 0 },
    { id: "lead", label: "Délai ≠ 5", title: "Fournisseurs dont le délai n'est pas la valeur initiale de 5 jours ouvrés", test: (r) => Number(r.lead_time_days ?? 5) !== 5 },
  ],
  ref_article_suppliers: [
    { id: "caps", label: "Plafonnés", title: "Liens avec un plafond par livraison ou par semaine", test: (r) => r.max_qty_per_delivery != null && r.max_qty_per_delivery !== "" || r.max_qty_per_week != null && r.max_qty_per_week !== "" },
    { id: "override", label: "Délai spécifique", title: "Liens avec un délai différent de celui du fournisseur", test: (r) => r.lead_time_override != null && r.lead_time_override !== "" },
  ],
  ref_closed_orders: [
    { id: "no_receipt", label: "Sans réception", title: "PO déclarées qu'aucune réception ERP ne porte (numéro à vérifier)", test: (r) => Number(r.receipt_count ?? 0) === 0 },
  ],
  ref_programs: [
    { id: "no_bom", label: "Sans nomenclature", title: "Programmes sans composant dans les nomenclatures", test: (r) => Number(r.article_count ?? 0) === 0 },
    { id: "no_plan", label: "Sans plan", title: "Programmes sans quantité planifiée à partir de la semaine en cours (PDP ERP ou version active)", test: (r) => !r.has_plan },
  ],
};
/** Columns of the table itself that the Référentiel does not show (replaced by query columns). */
const HIDDEN: Record<string, string[]> = { ref_articles: ["family"] };
/** Columns that can be set in bulk on the filtered rows. */
const BULK: Record<string, string[]> = { ref_articles: ["coverage_target_days", "alert_red_days", "alert_yellow_days", "overstock_days", "demand_days", "sourcing_policy", "consumption_source", "planner", "active"], ref_suppliers: ["lead_time_days", "delivery_weekdays", "active"], ref_article_suppliers: ["lead_time_override", "max_qty_per_delivery", "max_qty_per_week", "quota_pct", "priority", "active"] };

const fmtCell = (c: RefColumn, v: RefValue) => v === null || v === undefined || v === "" ? "" : c.type === "bool" ? (v ? "oui" : "non") : c.type === "date" ? fmtDate(String(v)) : c.type === "float" ? fmtQty(Number(v)) : String(v);

/** Reference data managed in the application: one CRUD table per reference table, Excel template / import / export, bulk edit. */
export default function ReferencePage() {
  const tables = useRefTables();
  const { rights, perimeter } = usePerimeter();
  const [params] = useSearchParams();
  const [tab, setTab] = useRemembered<string>("referentiel.tab", "ref_articles");
  useEffect(() => { const t = params.get("table"); if (t) setTab(t); }, [params, setTab]);
  const [editing, setEditing] = useState<RefRow | null>(null);
  const [weeklyOf, setWeeklyOf] = useState<{ id: string; planner: string | null } | null>(null);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [bulk, setBulk] = useState(false);
  const toast = useToast();
  const table = tables.data?.find((t) => t.name === tab);
  const rows = useRefRows(tab, perimeter.planner);   // the global planner filter applies here too
  const [flags, setFlags] = useRemembered<string[]>(`referentiel.quick.${tab}`, []);
  const quick = QUICK_FILTERS[tab] ?? [];
  const flagSet = useMemo(() => new Set(flags), [flags]);
  const shown = useMemo(() => { const on = quick.filter((f) => flagSet.has(f.id)); return on.length ? (rows.data ?? []).filter((r) => on.every((f) => f.test(r))) : rows.data ?? []; }, [rows.data, quick, flagSet]);
  const fileInput = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<"replace" | "merge">("merge");
  const del = useWrite(({ name, key }: { name: string; key: Record<string, RefValue> }) => api.post(`/api/reference/${name}/delete`, { key }), () => toast.push("Ligne supprimée"));
  const importFile = useWrite(async (file: File) => { const fd = new FormData(); fd.append("file", file); fd.append("mode", mode); return api.upload<ImportReport>(`/api/reference/${tab}/import`, fd); },
    (r) => { setReport(r as ImportReport); toast.push(`${(r as ImportReport).created} ligne(s) importée(s)`, "success"); });

  const cols = useMemo<Column<RefRow>[]>(() => {
    if (!table) return [];
    const hidden = HIDDEN[table.name] ?? [];
    const out: Column<RefRow>[] = table.columns.filter((c) => !hidden.includes(c.name)).map((c) => ({
      key: c.name, label: c.label, num: c.type === "float" || c.type === "int", title: c.description || undefined,
      filter: c.type === "bool" || (c.type === "str" && ["unit", "planner", "family", "country", "program_id", "active", "role", "sourcing_policy", "consumption_source", "delivery_weekdays"].includes(c.name) && !c.key) || c.name === "demand_days" ? "select" : undefined,
      get: (r) => (c.type === "bool" ? (r[c.name] ? "oui" : "non") : c.name === "consumption_source" ? CONSUMPTION_SOURCE_SHORT[String(r[c.name] ?? "erp")] ?? String(r[c.name]) : c.name === "sourcing_policy" ? SOURCING_LABELS[String(r[c.name] ?? "quota")] ?? String(r[c.name]) : c.name === "delivery_weekdays" ? fmtWeekdays(String(r[c.name] ?? "")) : (r[c.name] as string | number | null)),
      exportGet: (r) => (r[c.name] as string | number | boolean | null),
      render: (r) => c.name === "article_id" ? <Link to={`/articles/${encodeURIComponent(String(r[c.name]))}`} onClick={(e) => e.stopPropagation()} title="Ouvrir la fiche article"><b>{String(r[c.name])}</b></Link>
        : c.name === "supplier_id" ? <SupplierLink id={String(r[c.name])} />
        : c.name === "name" && table.name === "ref_suppliers" ? <SupplierLink id={String(r.supplier_id)} name={String(r[c.name] ?? "")} inline className="" />
        : c.name === "source_article_id" ? <Link to={`/articles/${encodeURIComponent(String(r[c.name]))}`} onClick={(e) => e.stopPropagation()}>{String(r[c.name])}</Link>
        : c.type === "bool" ? <Badge tone={r[c.name] ? "ok" : "neutral"}>{r[c.name] ? "oui" : "non"}</Badge>
        : c.name === "consumption_source" ? <Badge tone={String(r[c.name] ?? "erp") === "erp" ? "neutral" : "info"}>{CONSUMPTION_SOURCE_SHORT[String(r[c.name] ?? "erp")]}</Badge>
        : c.name === "demand_days" ? <span title={fmtDemandDays(Number(r[c.name] ?? 5))}>{Number(r[c.name] ?? 5) === 5 ? <span className="subtle">5</span> : <b>{String(r[c.name])}</b>}</span>
        : c.name === "lead_time_override" ? (r[c.name] == null || r[c.name] === "" ? <span className="inherited" title="Délai du fournisseur">hérité</span> : <b>{String(r[c.name])}</b>)
        : <>{fmtCell(c, r[c.name])}</>,
    }));
    const after = (name: string, col: Column<RefRow>) => out.splice(out.findIndex((c) => c.key === name) + 1, 0, col);
    const list = (text: RefValue, width: number) => <span className="truncate" style={{ display: "inline-block", maxWidth: width, verticalAlign: "bottom" }} title={String(text ?? "")}>{String(text ?? "")}</span>;
    if (table.name === "ref_articles") after("unit", { key: "programs", label: "Programmes", title: "Programmes dont la nomenclature utilise l'article (colonne calculée)", get: (r) => (r.programs as string) ?? "", render: (r) => list(r.programs, 260) });
    if (table.name === "ref_articles") after("programs", { key: "supplier_count", label: "Fourn.", title: "Nombre de fournisseurs actifs (colonne calculée)", num: true, get: (r) => Number(r.supplier_count ?? 0) });
    if (table.name === "ref_suppliers") after("name", { key: "articles", label: "Articles", title: "Articles reliés au fournisseur par une règle active (colonne calculée)", get: (r) => (r.articles as string) ?? "", render: (r) => <>{Number(r.article_count ?? 0) ? <b style={{ marginRight: 6 }}>{Number(r.article_count)} ·</b> : null}{list(r.articles, 300)}</> });
    if (table.name === "ref_programs") after("name", { key: "article_count", label: "Articles", title: "Nombre de composants dans la nomenclature (colonne calculée)", num: true, get: (r) => Number(r.article_count ?? 0) });
    if (table.name === "ref_closed_orders") {
      after("purch_id", { key: "receipts", label: "Réceptions ERP", title: "Réceptions de l'ERP portant ce numéro de PO, journaux en transit exclus (colonne calculée)", num: true, get: (r) => Number(r.receipt_count ?? 0), render: (r) => Number(r.receipt_count ?? 0) ? <span className="cell-2l"><b>{Number(r.receipt_count)} · {fmtQty(Number(r.receipt_qty ?? 0))}</b><span className="sub">dernière le {fmtDate(String(r.last_receipt ?? ""))}</span></span> : <Badge tone="warning">aucune</Badge> });
      after("receipts", { key: "receipt_articles", label: "Articles reçus", title: "Articles et fournisseurs des réceptions portant ce numéro (colonne calculée)", get: (r) => `${r.receipt_articles ?? ""} ${r.receipt_suppliers ?? ""}`, render: (r) => <span className="cell-2l"><span>{list(r.receipt_articles, 220)}</span><span className="sub">{String(r.receipt_suppliers ?? "")}</span></span> });
    }
    if (table.name === "ref_articles") out.push({ key: "weeks", label: "", get: () => "", filter: "none", sortable: false, export: false, render: (r) => <Button size="sm" variant="ghost" title="Politique de stock semaine par semaine" onClick={(e) => { e.stopPropagation(); setWeeklyOf({ id: String(r.article_id), planner: r.planner == null ? null : String(r.planner) }); }}><CalendarDays /></Button> });
    if (table.name !== "ref_articles") out.push({ key: "who", label: "Modifié", get: (r) => `${r.updated_by ?? ""} ${r.updated_at ?? ""}`, render: (r) => <span className="subtle small">{r.updated_by}{r.updated_at ? <><br />{fmtDateTime(r.updated_at)}</> : null}</span> });
    if (rights.canEditTable(table.name)) out.push({ key: "del", label: "", get: () => "", filter: "none", sortable: false, export: false, render: (r) => <Button size="sm" variant="ghost" title="Supprimer" onClick={(e) => { e.stopPropagation(); if (window.confirm("Supprimer cette ligne ?")) del.mutate({ name: table.name, key: Object.fromEntries(table.key.map((k) => [k, r[k]])) }); }}><Trash2 /></Button> });
    return out;
  }, [table, del, rights]);

  const toggleFlag = (id: string) => setFlags(flags.includes(id) ? flags.filter((x) => x !== id) : [...flags, id]);

  return (
    <div className="page">
      <div className="page-header sticky">
        <div className="title"><h1>Référentiel</h1><p>Articles, fournisseurs, règles article ↔ fournisseur, termes de consommation, commandes fermées (PO), programmes, nomenclatures, stock de référence, approvisionneurs et délégations : ligne par ligne, par fichier Excel (modèle à télécharger, puis importer) ou en lot sur les lignes filtrées. Le paramétrage d'un article se fait aussi depuis sa fiche (onglet Paramétrage).{rights.access && !rights.canWrite ? " Lecture seule : vous n'êtes pas déclaré comme approvisionneur." : ""}</p></div>
        {tables.data && <Tabs value={tab} onChange={(t) => { setTab(t); setReport(null); }} tabs={[...tables.data.map((t) => ({ id: t.name, label: t.label, count: t.rows }))]} />}
      </div>
      {tables.isError ? <ErrorBox error={tables.error} /> : tables.isLoading || !tables.data ? <SkeletonBlock /> : (
        <>
          {table && (
            <Card flush title={table.label} hint={table.description}
              actions={<>
                {rights.canImportTable(table.name) && <>
                  <select className="select sm" value={mode} onChange={(e) => setMode(e.target.value as "replace" | "merge")} title="Mode d'import" aria-label="Mode d'import">
                    <option value="merge">Import : fusionner (mise à jour par clé)</option><option value="replace">Import : remplacer la table</option>
                  </select>
                  <input ref={fileInput} type="file" accept=".xlsx" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f && (mode === "merge" || window.confirm(`Remplacer toute la table « ${table.label} » par le fichier ?`))) importFile.mutate(f); e.target.value = ""; }} />
                  <Button size="sm" onClick={() => fileInput.current?.click()} disabled={importFile.isPending}><Upload />{importFile.isPending ? "Import…" : "Importer un fichier"}</Button>
                </>}
                <a className="btn sm" href={api.downloadUrl(`/api/reference/${table.name}/template.xlsx`)} title="Modèle Excel minimaliste (en-têtes, exemple, notice)"><Download />Modèle</a>
                <a className="btn sm" href={api.downloadUrl(`/api/reference/${table.name}/template.xlsx`, { filled: true })} title="Exporter la table complète au format du modèle (réimportable)"><Download />Exporter (modèle)</a>
                {rights.canEditTable(table.name) && BULK[table.name] && <Button size="sm" onClick={() => setBulk(true)} title="Donner la même valeur à une colonne pour toutes les lignes filtrées"><Wand2 />Modifier en lot</Button>}
                {rights.canEditTable(table.name) && <Button size="sm" variant="primary" onClick={() => setEditing({})}><Plus />Ajouter</Button>}
              </>}>
              {importFile.error && <div className="error-box" style={{ margin: 12 }}>{(importFile.error as Error).message}</div>}
              {report && <div className="note" style={{ margin: 12 }}>{report.created} ligne(s) importée(s){report.notes.length ? ` · ${report.notes.slice(0, 5).join(" ; ")}${report.notes.length > 5 ? " …" : ""}` : ""}</div>}
              {quick.length > 0 && (
                <div className="row wrap quick-filters ref-toolbar">
                  {quick.map((f) => <button key={f.id} type="button" className={`btn sm ${flagSet.has(f.id) ? "primary" : ""}`} title={f.title} onClick={() => toggleFlag(f.id)}>{f.label}{rows.data ? ` (${rows.data.filter(f.test).length})` : ""}</button>)}
                  <span className="subtle small" style={{ marginLeft: "auto" }}>{shown.length} / {rows.data?.length ?? 0} ligne(s)</span>
                </div>
              )}
              {rows.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : (
                <DataTable rows={shown} columns={cols} rowKey={(r) => table.key.map((k) => String(r[k])).join("|")} compact maxHeight="calc(100vh - 330px)" storageKey={`referentiel.${table.name}`} exportTitle={table.label}
                  onRowClick={rights.canEditTable(table.name) ? (r) => setEditing(r) : undefined}
                  emptyTitle={flags.length && rows.data?.length ? "Aucune ligne ne correspond aux filtres" : `Aucune ligne dans « ${table.label} »`} emptyHint={flags.length && rows.data?.length ? undefined : table.name === "ref_planners" ? "Tant que cette table est vide, tout utilisateur est administrateur : commencez par vous déclarer avec le rôle admin." : "Télécharger le modèle, le remplir, l'importer ; ou ajouter une ligne."} />
              )}
            </Card>
          )}
        </>
      )}
      {table && <RefRowEditor table={table} row={editing} onClose={() => setEditing(null)} />}
      {table && bulk && <BulkEditor table={table} rows={shown} onClose={() => setBulk(false)} />}
      <WeeklyParamsDrawer articleId={weeklyOf?.id ?? null} planner={weeklyOf?.planner} onClose={() => setWeeklyOf(null)} />
    </div>
  );
}

/** Set one column to the same value on every filtered row (one write per row, in sequence). */
function BulkEditor({ table, rows, onClose }: { table: RefTableInfo; rows: RefRow[]; onClose: () => void }) {
  const toast = useToast();
  const fields = (BULK[table.name] ?? []).map((f) => table.columns.find((c) => c.name === f)).filter((c): c is RefColumn => !!c);
  const [field, setField] = useState(fields[0]?.name ?? "");
  const [value, setValue] = useState("");
  const col = fields.find((c) => c.name === field);
  const apply = useWrite(async () => {
    let n = 0;
    for (const r of rows) {
      const values: Record<string, unknown> = {};
      table.columns.forEach((c) => { values[c.name] = r[c.name] === undefined ? null : r[c.name]; });
      values[field] = col?.type === "bool" ? value === "oui" : value === "" ? null : value;
      await api.put(`/api/reference/${table.name}/rows`, { values });
      n += 1;
    }
    return n;
  }, (n) => { toast.push(`${n} ligne(s) modifiée(s)`, "success"); onClose(); });
  return (
    <Drawer open onClose={onClose} title={`Modifier en lot · ${table.label}`}
      footer={<><Button onClick={onClose}>Annuler</Button><Button variant="primary" disabled={!field || apply.isPending || !rows.length} onClick={() => { if (window.confirm(`Donner la valeur « ${value || "vide"} » à « ${col?.label} » sur ${rows.length} ligne(s) ?`)) apply.mutate(undefined); }}>{apply.isPending ? "En cours…" : `Appliquer à ${rows.length} ligne(s)`}</Button></>}>
      <p className="small">Les <b>{rows.length}</b> lignes actuellement filtrées (filtres rapides du Référentiel ; les filtres de colonnes du tableau ne s'appliquent pas ici) recevront la même valeur.</p>
      <div className="form-grid" style={{ marginTop: 12 }}>
        <Field label="Colonne"><select className="select" value={field} onChange={(e) => { setField(e.target.value); setValue(""); }}>{fields.map((c) => <option key={c.name} value={c.name}>{c.label}</option>)}</select></Field>
        <Field label="Valeur" help={col?.description}>
          {col?.type === "bool" ? <select className="select" value={value || "oui"} onChange={(e) => setValue(e.target.value)}><option value="oui">oui</option><option value="non">non</option></select>
            : col?.name === "consumption_source" ? <select className="select" value={value} onChange={(e) => setValue(e.target.value)}><option value="">choisir…</option><option value="erp">Consommation ERP</option><option value="plan">PDP éclaté</option><option value="derived">Dérivée</option></select>
            : col?.name === "sourcing_policy" ? <select className="select" value={value} onChange={(e) => setValue(e.target.value)}><option value="">choisir…</option><option value="quota">Quotas</option><option value="priority">Priorité</option></select>
            : col?.name === "demand_days" ? <select className="select" value={value} onChange={(e) => setValue(e.target.value)}><option value="">choisir…</option>{[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n} – {fmtDemandDays(n)}</option>)}</select>
            : <input className="input" type={col?.type === "float" || col?.type === "int" ? "number" : "text"} step="any" value={value} onChange={(e) => setValue(e.target.value)} placeholder={col?.name.startsWith("max_qty") || col?.name === "lead_time_override" ? "vide = aucun / hérité" : ""} />}
        </Field>
      </div>
      {apply.error && <div className="error-box">{(apply.error as Error).message}</div>}
    </Drawer>
  );
}
