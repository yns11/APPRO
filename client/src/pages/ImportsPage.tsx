import { useRef, useState } from "react";
import { CheckCircle2, Download, FileUp, Power, PowerOff, Trash2, Upload } from "lucide-react";
import { api } from "@/lib/api";
import { useCockpit, usePdpVersions, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { Badge, Button, Card, Empty, ErrorBox, Field, SkeletonBlock, useToast } from "@/components/ui";
import { fmtDate, fmtDateTime, fmtInt } from "@/lib/format";
import type { ImportReport } from "@/lib/types";

/** Imports (PDP, entries re-import) and exports (simulation, alerts, order book). */
export default function ImportsPage() {
  const { perimeter, engineParams } = usePerimeter();
  const toast = useToast();
  const versions = usePdpVersions();
  const cockpit = useCockpit();
  const [report, setReport] = useState<ImportReport | null>(null);
  const [pdpName, setPdpName] = useState("");
  const [activate, setActivate] = useState(true);
  const [granularity, setGranularity] = useState<"day" | "week">(perimeter.granularity);
  const [selection, setSelection] = useState<string[]>([]);
  const pdpInput = useRef<HTMLInputElement>(null);
  const entriesInput = useRef<HTMLInputElement>(null);

  const importPdp = useWrite(async (file: File) => {
    const fd = new FormData(); fd.append("file", file); fd.append("name", pdpName || file.name); fd.append("activate", String(activate));
    return api.upload<ImportReport>("/api/pdp/import", fd);
  }, (r) => { setReport(r as ImportReport); toast.push(`PDP importé : ${(r as ImportReport).created} lignes`, "success"); });
  const importEntries = useWrite(async (file: File) => { const fd = new FormData(); fd.append("file", file); return api.upload<ImportReport>("/api/imports/entries", fd); },
    (r) => { setReport(r as ImportReport); toast.push(`Saisies importées : ${(r as ImportReport).created}`, "success"); });
  const act = useWrite(({ id, on }: { id: string; on: boolean }) => api.post(`/api/pdp/versions/${id}/${on ? "activate" : "deactivate"}`), () => toast.push("Version PDP mise à jour", "success"));
  const del = useWrite((id: string) => api.del(`/api/pdp/versions/${id}`), () => toast.push("Version supprimée"));

  const exportUrl = api.downloadUrl("/api/exports/simulation.xlsx", { planner: engineParams.planner, scenario_id: engineParams.scenario_id, granularity, horizon_days: engineParams.horizon_days, article_ids: selection.length ? selection : undefined });

  return (
    <div className="page">
      <div className="page-header"><div className="title"><h1>Imports / exports</h1><p>Importez un plan de production (format legacy « SOP - PDP » ou format long), exportez la simulation dans un classeur Excel « vivant » (formules) et réimportez ce que vous y avez saisi : onglet SAISIES, décisions A / M de l'onglet PROPOSITIONS, réceptions saisies dans le CARNET_COMMANDES.</p></div></div>

      <div className="grid cols-2">
        <Card title="Importer un PDP hebdomadaire" hint="xlsx · 1re colonne : programme (nom ou id) · en-têtes : S11-26, 2026-W11, 2028W24 ou dates">
          <div className="form-grid">
            <Field label="Nom de la version"><input className="input" value={pdpName} onChange={(e) => setPdpName(e.target.value)} placeholder="ex. PDP S38 – v2" /></Field>
            <Field label="Activation"><label className="checkbox" style={{ height: 34 }}><input type="checkbox" checked={activate} onChange={(e) => setActivate(e.target.checked)} />activer immédiatement</label></Field>
          </div>
          <input ref={pdpInput} type="file" accept=".xlsx" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) importPdp.mutate(f); e.target.value = ""; }} />
          <div className="dropzone" style={{ marginTop: 12 }} onClick={() => pdpInput.current?.click()} onDragOver={(e) => { e.preventDefault(); e.currentTarget.classList.add("over"); }} onDragLeave={(e) => e.currentTarget.classList.remove("over")} onDrop={(e) => { e.preventDefault(); e.currentTarget.classList.remove("over"); const f = e.dataTransfer.files?.[0]; if (f) importPdp.mutate(f); }}>
            <FileUp /><div>{importPdp.isPending ? "Import en cours…" : "Déposer le classeur PDP ici ou cliquer"}</div>
          </div>
          {importPdp.error && <div className="error-box" style={{ marginTop: 12 }}>{(importPdp.error as Error).message}</div>}
          <p className="note" style={{ marginTop: 12 }}>Un PDP importé et actif remplace le plan ERP pour les programmes qu'il contient. Désactivez-le pour revenir au plan ERP. Les versions restent conservées pour comparaison.</p>
        </Card>

        <Card title="Exporter la simulation" hint="Classeur à formules : PARAMETRES, ARTICLES, SIMULATION (stocks ferme / prévisionnel / simulé, manque, cible, couverture recalculés dans Excel), ALERTES, PROPOSITIONS, CARNET_COMMANDES, SAISIES">
          <div className="form-grid">
            <Field label="Granularité"><select className="select" value={granularity} onChange={(e) => setGranularity(e.target.value as "day" | "week")}><option value="day">Jour</option><option value="week">Semaine</option></select></Field>
            <Field label="Périmètre"><input className="input" readOnly value={`${perimeter.planner ?? "tous"} · ${engineParams.horizon_days} j${perimeter.scenarioId ? " · scénario" : ""}`} /></Field>
            <Field label="Articles (vide = tous)" span2>
              <select className="select" multiple size={6} value={selection} onChange={(e) => setSelection(Array.from(e.target.selectedOptions).map((o) => o.value))} style={{ height: "auto" }}>
                {(cockpit.data?.articles ?? []).map((a) => <option key={a.article_id} value={a.article_id}>{a.article_id} · {a.designation}</option>)}
              </select>
            </Field>
          </div>
          <div className="row wrap" style={{ marginTop: 12 }}>
            <a className="btn primary" href={exportUrl}><Download />Simulation (xlsx)</a>
            <a className="btn" href={api.downloadUrl("/api/exports/alerts.xlsx", { planner: engineParams.planner, scenario_id: engineParams.scenario_id })}><Download />Alertes</a>
            <a className="btn" href={api.downloadUrl("/api/exports/orders.xlsx", { planner: engineParams.planner, scenario_id: engineParams.scenario_id })}><Download />Carnet de commandes</a>
          </div>
          <div className="divider" style={{ margin: "16px 0" }} />
          <h4>Réimporter un classeur (SAISIES, décisions PROPOSITIONS, réceptions CARNET)</h4>
          <input ref={entriesInput} type="file" accept=".xlsx" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) importEntries.mutate(f); e.target.value = ""; }} />
          <Button style={{ marginTop: 8 }} onClick={() => entriesInput.current?.click()} disabled={importEntries.isPending}><Upload />{importEntries.isPending ? "Import…" : "Choisir le classeur"}</Button>
          {importEntries.error && <div className="error-box" style={{ marginTop: 12 }}>{(importEntries.error as Error).message}</div>}
        </Card>
      </div>

      {report && (
        <Card title="Rapport d'import" actions={<Button size="sm" onClick={() => setReport(null)}>Fermer</Button>}>
          <div className="row"><CheckCircle2 color="var(--ok)" /><b>{fmtInt(report.created)}</b> ligne(s) créée(s) · <b>{fmtInt(report.ignored)}</b> avertissement(s){report.version && <> · version <b>{report.version.name}</b> {report.version.active && <Badge tone="ok">active</Badge>}</>}</div>
          {report.notes.length > 0 && <ul className="small subtle" style={{ marginTop: 8 }}>{report.notes.slice(0, 30).map((n, i) => <li key={i}>{n}</li>)}{report.notes.length > 30 && <li>… {report.notes.length - 30} autres</li>}</ul>}
        </Card>
      )}

      <Card flush title="Versions de PDP importées">
        {versions.isError ? <ErrorBox error={versions.error} /> : versions.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : !versions.data?.length ? <Empty title="Aucune version importée" hint="Le plan ERP est utilisé." /> : (
          <table className="tbl">
            <thead><tr><th>Version</th><th>Fichier</th><th className="num">Lignes</th><th className="num">Programmes</th><th>Semaines</th><th>Importé</th><th>Statut</th><th></th></tr></thead>
            <tbody>{versions.data.map((v) => (
              <tr key={v.id}>
                <td><b>{v.name}</b>{v.note && <span className="sub">{v.note}</span>}</td><td className="subtle">{v.source_file}</td>
                <td className="num">{fmtInt(v.line_count)}</td><td className="num">{v.programs}</td>
                <td>{fmtDate(v.first_week)} → {fmtDate(v.last_week)}</td>
                <td className="subtle small">{v.imported_by}<br />{fmtDateTime(v.imported_at)}</td>
                <td>{v.active ? <Badge tone="ok">active</Badge> : <Badge tone="neutral">inactive</Badge>}</td>
                <td className="row">
                  <Button size="sm" onClick={() => act.mutate({ id: v.id, on: !v.active })}>{v.active ? <><PowerOff />Désactiver</> : <><Power />Activer</>}</Button>
                  <Button size="sm" variant="ghost" onClick={() => { if (window.confirm(`Supprimer la version « ${v.name} » ?`)) del.mutate(v.id); }}><Trash2 /></Button>
                </td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
