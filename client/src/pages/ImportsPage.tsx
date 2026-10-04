import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Download, Upload } from "lucide-react";
import { api } from "@/lib/api";
import { useCockpit, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { Button, Card, Field, useToast } from "@/components/ui";
import { fmtInt } from "@/lib/format";
import type { ImportReport } from "@/lib/types";

/** Exports (simulation workbook, alerts, plan) and re-import of the simulation workbook ; the PDP has its own page. */
export default function ImportsPage() {
  const { perimeter, engineParams, rights } = usePerimeter();
  const toast = useToast();
  const cockpit = useCockpit();
  const [report, setReport] = useState<ImportReport | null>(null);
  const [granularity, setGranularity] = useState<"day" | "week">(perimeter.granularity === "week" ? "week" : "day");
  const [selection, setSelection] = useState<string[]>([]);
  const simInput = useRef<HTMLInputElement>(null);

  const importSim = useWrite(async (file: File) => { const fd = new FormData(); fd.append("file", file); return api.upload<ImportReport>("/api/imports/simulation", fd); },
    (r) => { setReport(r as ImportReport); toast.push(`Classeur réimporté : ${(r as ImportReport).created} cellule(s)`, "success"); });
  const exportUrl = api.downloadUrl("/api/exports/simulation.xlsx", { planner: engineParams.planner, granularity, horizon_days: engineParams.horizon_days, article_ids: selection.length ? selection : undefined });

  return (
    <div className="page">
      <div className="page-header"><div className="title"><h1>Imports / exports</h1><p>Classeur de simulation à formules (export, puis réimport des lignes Plan et Ajustement). Le PDP se saisit et s'importe depuis la page <Link to="/pdp">Plan de production</Link> ; le référentiel depuis la page Référentiel.</p></div></div>

      <div className="grid cols-2">
        <Card title="Classeur de simulation" hint="Excel à formules : PARAMETRES, ARTICLES, SIMULATION (le tableau, une ligne Plan par fournisseur), ALERTES">
          <div className="form-grid">
            <Field label="Granularité"><select className="select" value={granularity} onChange={(e) => setGranularity(e.target.value as "day" | "week")}><option value="day">Jour (réimportable)</option><option value="week">Semaine (lecture)</option></select></Field>
            <Field label="Périmètre"><input className="input" readOnly value={`${perimeter.planner ?? "tous"} · ${engineParams.horizon_days} j`} /></Field>
            <Field label="Articles (vide = tous)" span2>
              <select className="select" multiple size={6} value={selection} onChange={(e) => setSelection(Array.from(e.target.selectedOptions).map((o) => o.value))} style={{ height: "auto" }}>
                {(cockpit.data?.articles ?? []).map((a) => <option key={a.article_id} value={a.article_id}>{a.article_id} · {a.designation}</option>)}
              </select>
            </Field>
          </div>
          <div className="row wrap" style={{ marginTop: 12 }}>
            <a className="btn primary" href={exportUrl}><Download />Simulation (xlsx)</a>
            <a className="btn" href={api.downloadUrl("/api/exports/alerts.xlsx", { planner: engineParams.planner })}><Download />Alertes</a>
            <a className="btn" href={api.downloadUrl("/api/exports/plan.xlsx", { planner: engineParams.planner })}><Download />Plan (liste)</a>
          </div>
          <div className="divider" style={{ margin: "16px 0" }} />
          <h4>Réimporter un classeur de simulation (jour)</h4>
          <p className="small subtle">Les lignes Plan et Ajustement des articles du classeur remplacent les cellules de l'application : une valeur Plan égale au Ferme vaut « ERP », une valeur différente devient une cellule saisie.</p>
          <input ref={simInput} type="file" accept=".xlsx" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) importSim.mutate(f); e.target.value = ""; }} />
          <Button style={{ marginTop: 8 }} onClick={() => simInput.current?.click()} disabled={importSim.isPending || !rights.canWrite} title={rights.canWrite ? "Les lignes Plan et Ajustement des articles de votre carnet sont reprises" : "Lecture seule"}><Upload />{importSim.isPending ? "Import…" : "Choisir le classeur"}</Button>
          {importSim.error && <div className="error-box" style={{ marginTop: 12 }}>{(importSim.error as Error).message}</div>}
        </Card>
      </div>

      {report && (
        <Card title="Rapport d'import" actions={<Button size="sm" onClick={() => setReport(null)}>Fermer</Button>}>
          <div className="row"><CheckCircle2 color="var(--ok)" /><b>{fmtInt(report.created)}</b> élément(s) · <b>{fmtInt(report.ignored)}</b> avertissement(s)</div>
          {report.notes.length > 0 && <ul className="small subtle" style={{ marginTop: 8 }}>{report.notes.slice(0, 30).map((n, i) => <li key={i}>{n}</li>)}{report.notes.length > 30 && <li>… {report.notes.length - 30} autres</li>}</ul>}
        </Card>
      )}

    </div>
  );
}
