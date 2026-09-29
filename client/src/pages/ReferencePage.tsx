import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { CalendarDays, Download, Plus, Trash2, Upload } from "lucide-react";
import { WeeklyParamsDrawer } from "@/components/WeeklyParamsDrawer";
import { DataTable, type Column } from "@/components/DataTable";
import { usePdpErp, usePrograms, useRefRows, useRefTables, useWrite } from "@/lib/queries";
import { api } from "@/lib/api";
import { Badge, Button, Card, Drawer, Empty, ErrorBox, Field, SkeletonBlock, Tabs, useToast } from "@/components/ui";
import { fmtDate, fmtDateTime, fmtQty } from "@/lib/format";
import type { ImportReport, RefColumn, RefRow, RefTableInfo, RefValue } from "@/lib/types";

const fmtCell = (c: RefColumn, v: RefValue) => v === null || v === undefined || v === "" ? "" : c.type === "bool" ? (v ? "oui" : "non") : c.type === "date" ? fmtDate(String(v)) : c.type === "float" ? fmtQty(Number(v)) : String(v);

/** Reference data managed in the application: one CRUD table per reference table, Excel template / import / export. */
export default function ReferencePage() {
  const tables = useRefTables();
  const [tab, setTab] = useState<string>("ref_articles");
  const [editing, setEditing] = useState<RefRow | null>(null);
  const [weeklyOf, setWeeklyOf] = useState<string | null>(null);
  const [report, setReport] = useState<ImportReport | null>(null);
  const [program, setProgram] = useState("");
  const toast = useToast();
  const table = tables.data?.find((t) => t.name === tab);
  const rows = useRefRows(tab);
  const fileInput = useRef<HTMLInputElement>(null);
  const [mode, setMode] = useState<"replace" | "merge">("merge");
  const del = useWrite(({ name, key }: { name: string; key: Record<string, RefValue> }) => api.post(`/api/reference/${name}/delete`, { key }), () => toast.push("Ligne supprimée"));
  const importFile = useWrite(async (file: File) => { const fd = new FormData(); fd.append("file", file); fd.append("mode", mode); return api.upload<ImportReport>(`/api/reference/${tab}/import`, fd); },
    (r) => { setReport(r as ImportReport); toast.push(`${(r as ImportReport).created} ligne(s) importée(s)`, "success"); });

  const cols = useMemo<Column<RefRow>[]>(() => {
    if (!table) return [];
    const out: Column<RefRow>[] = table.columns.map((c) => ({
      key: c.name, label: c.label, num: c.type === "float" || c.type === "int",
      filter: c.type === "bool" || (c.type === "str" && ["unit", "planner", "family", "country", "supplier_id", "program_id", "active"].includes(c.name) && !c.key) ? "select" : undefined,
      get: (r) => (c.type === "bool" ? (r[c.name] ? "oui" : "non") : (r[c.name] as string | number | null)),
      render: (r) => c.key && c.name === "article_id" ? <Link to={`/articles/${encodeURIComponent(String(r[c.name]))}`} onClick={(e) => e.stopPropagation()}><b>{String(r[c.name])}</b></Link>
        : c.type === "bool" ? <Badge tone={r[c.name] ? "ok" : "neutral"}>{r[c.name] ? "oui" : "non"}</Badge> : <>{fmtCell(c, r[c.name])}</>,
    }));
    if (table.name === "ref_articles") out.push({ key: "weeks", label: "Semaines", get: () => "", filter: "none", sortable: false, render: (r) => <Button size="sm" variant="ghost" title="Personnaliser la politique de stock semaine par semaine" onClick={(e) => { e.stopPropagation(); setWeeklyOf(String(r.article_id)); }}><CalendarDays /></Button> });
    out.push({ key: "who", label: "Modifié", get: (r) => `${r.updated_by ?? ""} ${r.updated_at ?? ""}`, render: (r) => <span className="subtle small">{r.updated_by}{r.updated_at ? <><br />{fmtDateTime(r.updated_at)}</> : null}</span> });
    out.push({ key: "del", label: "", get: () => "", filter: "none", sortable: false, render: (r) => <Button size="sm" variant="ghost" title="Supprimer" onClick={(e) => { e.stopPropagation(); if (window.confirm("Supprimer cette ligne ?")) del.mutate({ name: table.name, key: Object.fromEntries(table.key.map((k) => [k, r[k]])) }); }}><Trash2 /></Button> });
    return out;
  }, [table, del]);

  const programs = usePrograms();
  const pdp = usePdpErp(program || undefined);

  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Référentiel</h1><p>Articles, fournisseurs, règles article ↔ fournisseur, programmes, nomenclatures et stock de référence sont gérés ici : ligne par ligne, ou par fichier Excel (modèle à télécharger, puis importer).</p></div>
      </div>
      {tables.isError ? <ErrorBox error={tables.error} /> : tables.isLoading || !tables.data ? <SkeletonBlock /> : (
        <>
          <Tabs value={tab} onChange={(t) => { setTab(t); setReport(null); }} tabs={[...tables.data.map((t) => ({ id: t.name, label: t.label, count: t.rows })), { id: "pdp_erp", label: "PDP ERP" }]} />
          {table && tab !== "pdp_erp" && (
            <Card flush title={table.label} hint={table.description}
              actions={<>
                <select className="select sm" value={mode} onChange={(e) => setMode(e.target.value as "replace" | "merge")} title="Mode d'import" aria-label="Mode d'import">
                  <option value="merge">Import : fusionner (mise à jour par clé)</option><option value="replace">Import : remplacer la table</option>
                </select>
                <input ref={fileInput} type="file" accept=".xlsx" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f && (mode === "merge" || window.confirm(`Remplacer toute la table « ${table.label} » par le fichier ?`))) importFile.mutate(f); e.target.value = ""; }} />
                <Button size="sm" onClick={() => fileInput.current?.click()} disabled={importFile.isPending}><Upload />{importFile.isPending ? "Import…" : "Importer un fichier"}</Button>
                <a className="btn sm" href={api.downloadUrl(`/api/reference/${table.name}/template.xlsx`)} title="Modèle Excel minimaliste (en-têtes, exemple, notice)"><Download />Modèle</a>
                <a className="btn sm" href={api.downloadUrl(`/api/reference/${table.name}/template.xlsx`, { filled: true })} title="Exporter le contenu actuel"><Download />Exporter</a>
                <Button size="sm" variant="primary" onClick={() => setEditing({})}><Plus />Ajouter</Button>
              </>}>
              {importFile.error && <div className="error-box" style={{ margin: 12 }}>{(importFile.error as Error).message}</div>}
              {report && <div className="note" style={{ margin: 12 }}>{report.created} ligne(s) importée(s){report.notes.length ? ` · ${report.notes.slice(0, 5).join(" ; ")}${report.notes.length > 5 ? " …" : ""}` : ""}</div>}
              {rows.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : (
                <DataTable rows={rows.data ?? []} columns={cols} rowKey={(r) => table.key.map((k) => String(r[k])).join("|")} compact onRowClick={(r) => setEditing(r)}
                  emptyTitle={`Aucune ligne dans « ${table.label} »`} emptyHint="Télécharger le modèle, le remplir, l'importer ; ou ajouter une ligne." />
              )}
            </Card>
          )}
          {tab === "pdp_erp" && (
            <div className="grid cols-2">
              <Card flush title="Programmes" hint="cliquer pour voir le PDP lu de l'ERP (si une table est configurée)">
                {programs.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : (
                  <table className="tbl compact"><thead><tr><th>Programme</th><th className="num">Composants</th></tr></thead>
                    <tbody>{(programs.data ?? []).map((p) => <tr key={p.program_id} className={`clickable ${program === p.program_id ? "selected" : ""}`} onClick={() => setProgram(p.program_id)}><td><b>{p.name}</b><span className="sub mono">{p.program_id}</span></td><td className="num">{p.components}</td></tr>)}</tbody></table>
                )}
              </Card>
              <Card flush title={program ? `PDP ERP – ${program}` : "PDP ERP"} hint="le PDP importé par fichier (page Imports / exports) remplace cette table pour ses programmes">
                {!program ? <Empty title="Sélectionnez un programme" /> : pdp.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : !pdp.data?.length ? <Empty title="Aucun PDP ERP pour ce programme" hint="Le PDP vient du fichier importé." /> : (
                  <div style={{ maxHeight: 520, overflow: "auto" }}>
                    <table className="tbl compact"><thead><tr><th>Lundi</th><th className="num">Quantité</th><th>Version</th></tr></thead>
                      <tbody>{pdp.data.map((l, i) => <tr key={i}><td>{fmtDate(l.week_start)}</td><td className="num">{fmtQty(l.qty)}</td><td className="subtle">{l.version}</td></tr>)}</tbody></table>
                  </div>
                )}
              </Card>
            </div>
          )}
        </>
      )}
      {table && <RowEditor table={table} row={editing} onClose={() => setEditing(null)} />}
      <WeeklyParamsDrawer articleId={weeklyOf} onClose={() => setWeeklyOf(null)} />
    </div>
  );
}

/** Create / edit one row of a reference table (typed inputs from the column definitions). */
function RowEditor({ table, row, onClose }: { table: RefTableInfo; row: RefRow | null; onClose: () => void }) {
  const toast = useToast();
  const [values, setValues] = useState<Record<string, string>>({});
  const isNew = !!row && table.key.every((k) => row[k] === undefined);
  useEffect(() => {
    if (!row) return;
    const v: Record<string, string> = {};
    table.columns.forEach((c) => { const x = row[c.name]; v[c.name] = x === undefined || x === null ? (c.type === "bool" ? "oui" : "") : c.type === "bool" ? (x ? "oui" : "non") : String(x); });
    setValues(v);
  }, [row, table]);
  const save = useWrite((vals: Record<string, string>) => api.put(`/api/reference/${table.name}/rows`, { values: vals }), () => { toast.push("Ligne enregistrée", "success"); onClose(); });
  return (
    <Drawer open={!!row} onClose={onClose} title={`${isNew ? "Ajouter" : "Modifier"} · ${table.label}`}
      footer={<><Button onClick={onClose}>Annuler</Button><Button variant="primary" disabled={save.isPending} onClick={() => save.mutate(values)}>Enregistrer</Button></>}>
      <div className="form-grid">
        {table.columns.map((c) => (
          <Field key={c.name} label={`${c.label}${c.required ? " *" : ""}`} help={c.description}>
            {c.type === "bool" ? <select className="select" value={values[c.name] ?? "oui"} onChange={(e) => setValues({ ...values, [c.name]: e.target.value })}><option value="oui">oui</option><option value="non">non</option></select>
              : <input className="input" type={c.type === "date" ? "date" : c.type === "float" || c.type === "int" ? "number" : "text"} step={c.type === "float" ? "any" : undefined} value={values[c.name] ?? ""} disabled={c.key && !isNew}
                onChange={(e) => setValues({ ...values, [c.name]: e.target.value })} />}
          </Field>
        ))}
      </div>
      {save.error && <div className="error-box">{(save.error as Error).message}</div>}
      <p className="small subtle">* obligatoire · les colonnes clé ({table.key.join(", ")}) identifient la ligne et ne se modifient pas.</p>
    </Drawer>
  );
}
