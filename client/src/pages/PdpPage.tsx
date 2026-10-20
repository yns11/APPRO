import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { CheckCircle2, Download, FileUp, History, Power, PowerOff, RotateCcw, Save, Trash2 } from "lucide-react";
import { api } from "@/lib/api";
import { usePdpSheet, usePdpVersions, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { Badge, Button, Card, Empty, ErrorBox, Field, SkeletonBlock, useToast } from "@/components/ui";
import { SearchSelect } from "@/components/SearchSelect";
import { WeekRangeBar, type WeekChip, type WeekRange } from "@/components/WeekRangeBar";
import { SheetGrid, type Pos, type SheetCell, type SheetChange } from "@/components/SheetGrid";
import { fmtDate, fmtDateTime, fmtInt, fmtQty } from "@/lib/format";
import type { ImportReport, PdpSheetProgram, PdpVersionOut } from "@/lib/types";

const WEEK_CHOICES = [26, 52, 78, 104];
const ORIGIN_LABELS: Record<string, string> = {
  active: "version active", monday: "version connue au lundi de la semaine", later: "version importée après coup (aucune ne la contenait au lundi)", none: "aucune version ne contient cette semaine",
};

/**
 * Production plan (PDP): the effective weekly plan of every programme as one sheet (programmes in
 * rows, ISO weeks in columns).  Managers and administrators type the weeks to come directly (or
 * paste a block from Excel) and save: the changes become a new active version.  Import of a
 * workbook and the list of versions live here too.
 */
export default function PdpPage() {
  const toast = useToast();
  const { rights, perimeter } = usePerimeter();
  const canEdit = rights.canImportPdp;
  const isAdmin = rights.isAdmin;
  const [program, setProgram] = useState("");
  const [onlyNoBom, setOnlyNoBom] = useState(false);
  const [onlyNoPlan, setOnlyNoPlan] = useState(false);
  const [range, setRange] = useState<WeekRange>(() => { try { return JSON.parse(localStorage.getItem("appro.pdp.weekRange") ?? "null"); } catch { return null; } });
  useEffect(() => { try { localStorage.setItem("appro.pdp.weekRange", JSON.stringify(range)); } catch { /* private mode */ } }, [range]);
  const [weeks, setWeeks] = useState(() => { try { return Number(localStorage.getItem("appro.pdp.weeks")) || 26; } catch { return 26; } });
  useEffect(() => { try { localStorage.setItem("appro.pdp.weeks", String(weeks)); } catch { /* private mode */ } }, [weeks]);
  const q = usePdpSheet(weeks, perimeter.planner);     // the global planner filter keeps the programmes reaching his articles
  const d = q.data;
  const versions = usePdpVersions();
  const [edits, setEdits] = useState<Map<string, number>>(new Map());   // "program|week_start" → qty
  const [name, setName] = useState("");
  const [report, setReport] = useState<ImportReport | null>(null);
  const [pdpName, setPdpName] = useState("");
  const [activate, setActivate] = useState(true);
  const fileInput = useRef<HTMLInputElement>(null);
  useEffect(() => { setEdits(new Map()); }, [d?.active_version?.id]);

  const save = useWrite((cells: { program_id: string; week_start: string; qty: number }[]) => api.put<PdpVersionOut>("/api/pdp/sheet", { name, cells }),
    (v) => { setEdits(new Map()); setName(""); toast.push(`PDP enregistré : version « ${v.name} » active`, "success"); });
  const importPdp = useWrite(async (file: File) => {
    const fd = new FormData(); fd.append("file", file); fd.append("name", pdpName || file.name); fd.append("activate", String(activate));
    return api.upload<ImportReport>("/api/pdp/import", fd);
  }, (r) => { setReport(r); toast.push(`PDP importé : ${r.created} lignes`, "success"); });
  const act = useWrite(({ id, on }: { id: string; on: boolean }) => api.post(`/api/pdp/versions/${id}/${on ? "activate" : "deactivate"}`), () => toast.push("Version PDP mise à jour", "success"));
  const del = useWrite((id: string) => api.del(`/api/pdp/versions/${id}`), () => toast.push("Version supprimée"));

  const reorder = useWrite((ids: string[]) => api.put<string[]>("/api/pdp/order", { program_ids: ids }), () => toast.push("Ordre des programmes enregistré", "success"));
  const saveScrap = useWrite((x: { program_id: string; rate_pct: number }) => api.put("/api/pdp/scrap", x), () => toast.push("Taux de rebut enregistré : il s'applique à partir de la semaine en cours", "success"));
  const allPrograms = useMemo(() => d?.programs ?? [], [d]);
  const allWeeks = useMemo(() => d?.weeks ?? [], [d]);
  const programOptions = useMemo(() => allPrograms.map((p) => ({ id: p.program_id, label: p.name || p.program_id, hint: p.program_id })), [allPrograms]);
  const weekChips = useMemo<WeekChip[]>(() => allWeeks.map((w) => ({ key: w.week, label: w.week.replace(/^\d{4}-W/, "S"), start: fmtDate(w.week_start), current: w.week === d?.current_week, past: w.past })), [allWeeks, d?.current_week]);
  const pastCount = useMemo(() => allWeeks.filter((w) => w.past).length, [allWeeks]);
  /** the stored week range applies only when it overlaps the loaded weeks ; without a stored range the
   * past weeks (point zero → last week) stay hidden : the sheet opens on the current week */
  const effRange = useMemo(() => {
    const keys = allWeeks.map((w) => w.week);
    if (!keys.length) return null;
    if (!range) return pastCount && d?.current_week ? { from: d.current_week, to: keys[keys.length - 1] } : null;
    const a = keys.indexOf(range.from), b = keys.indexOf(range.to);
    if (a < 0 && b < 0) return null;
    return { from: a < 0 ? keys[0] : range.from, to: b < 0 ? keys[keys.length - 1] : range.to };
  }, [range, allWeeks, pastCount, d?.current_week]);
  const showingPast = !!effRange && allWeeks.findIndex((w) => w.week === effRange.from) < pastCount;
  const colIdx = useMemo(() => {
    const keys = allWeeks.map((w) => w.week);
    const lo = effRange ? keys.indexOf(effRange.from) : 0, hi = effRange ? keys.indexOf(effRange.to) : keys.length - 1;
    return allWeeks.map((_w, i) => i).filter((i) => i >= lo && i <= hi);
  }, [allWeeks, effRange]);
  const programs = useMemo<PdpSheetProgram[]>(() => allPrograms.filter((p) => (!program || p.program_id === program) && (!onlyNoBom || !p.has_bom) && (!onlyNoPlan || p.source === "none" || p.values.every((v) => v === 0))), [allPrograms, program, onlyNoBom, onlyNoPlan]);
  const cols = useMemo(() => colIdx.map((i) => allWeeks[i]), [colIdx, allWeeks]);
  const valueAt = (p: PdpSheetProgram, c: number) => p.values[colIdx[c]];
  const moveRow = (from: number, to: number) => {
    // the rows displayed may be a filtered subset : the move is applied inside the full list
    const ids = allPrograms.map((p) => p.program_id);
    const a = ids.indexOf(programs[from].program_id), b = ids.indexOf(programs[to].program_id);
    if (a < 0 || b < 0 || a === b) return;
    const [moved] = ids.splice(a, 1); ids.splice(b, 0, moved);
    reorder.mutate(ids);
  };
  const key = (r: number, c: number) => `${programs[r].program_id}|${cols[c].week_start}`;
  const cells = useMemo<SheetCell[][]>(() => programs.map((p) => cols.map((w, c) => {
    const k = `${p.program_id}|${w.week_start}`;
    const pending = edits.get(k);
    const base = valueAt(p, c);
    const value = pending ?? base;
    const current = w.week === d?.current_week;
    const origin = w.past ? ` · semaine passée : ${ORIGIN_LABELS[w.origin ?? "none"]}${w.origin_version ? ` (« ${w.origin_version} »)` : ""}${w.editable ? " · modifiable (administrateur)" : " · lecture seule"}` : !w.editable ? " · lecture seule" : "";
    const title = `${p.name} · ${w.week.replace("-W", " S")} (${fmtDate(w.week_start)})${pending !== undefined ? ` · modifié : ${fmtQty(base)} → ${fmtQty(pending)} (non enregistré)` : ""}${current ? " · semaine en cours" : ""}${origin}`;
    return { value, text: value === 0 ? "·" : fmtQty(value), over: pending !== undefined, ro: !w.editable, title, cls: current ? "current-week" : w.past ? `past-week${w.editable ? " editable-past" : ""}` : "" };
  })), [programs, cols, edits, d?.current_week]);   // eslint-disable-line react-hooks/exhaustive-deps

  const apply = (changes: SheetChange[]) => {
    if (!canEdit) return;
    setEdits((m) => {
      const next = new Map(m);
      changes.forEach((x) => {
        const v = x.value === "" ? 0 : x.value;
        if (v < 0) return;
        const k = key(x.r, x.c);
        if (v === valueAt(programs[x.r], x.c)) next.delete(k); else next.set(k, v);
      });
      return next;
    });
  };
  const clear = (ps: Pos[]) => apply(ps.map((p) => ({ ...p, value: 0 })));
  const submit = () => {
    const cells = Array.from(edits.entries()).map(([k, qty]) => { const [program_id, week_start] = k.split("|"); return { program_id, week_start, qty }; });
    if (cells.length) save.mutate(cells);
  };

  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Plan de production (PDP)</h1><p>Quantités hebdomadaires par programme, telles que le moteur les éclate en besoins de composants. {canEdit ? "Saisir directement les semaines à venir (ou coller un bloc Excel), puis enregistrer : la saisie devient une nouvelle version active." : "Lecture seule : la saisie du PDP est réservée aux managers et administrateurs."}</p></div>
        <div className="actions">
          <select className="select sm" value={weeks} onChange={(e) => setWeeks(Number(e.target.value))} aria-label="Nombre de semaines">{WEEK_CHOICES.map((w) => <option key={w} value={w}>{w} semaines</option>)}</select>
          <a className="btn sm primary" href={api.downloadUrl("/api/pdp/export.xlsx", { weeks })} title="Le PDP effectif complet (depuis le point zéro) au format d'import : à corriger puis réimporter tel quel"><Download />Exporter (Excel)</a>
          <a className="btn sm" href={api.downloadUrl("/api/pdp/template.xlsx", { weeks })} title="Classeur vide (semaine en cours et suivantes) à remplir puis importer"><Download />Modèle vide</a>
          <Link className="btn sm" to="/programmes">Impact programmes</Link>
        </div>
      </div>

      <Card flush tight title={d ? <>PDP effectif · semaine en cours {d.current_week.replace("-W", " S")}{d.active_version ? <> · version active <b>{d.active_version.name}</b></> : " · aucune version active"}</> : "PDP effectif"}
        hint={canEdit ? `Cliquer une cellule et saisir ; Entrée / Tab / flèches ; Ctrl+C / Ctrl+V (bloc Excel) ; tirer le carré pour recopier ; Suppr = 0 ; glisser la poignée ⋮⋮ pour réordonner les lignes. La semaine en cours est surlignée ; les semaines passées (depuis ${d?.first_week ? d.first_week.replace("-W", " S") : "le point zéro"}) sont grisées${isAdmin ? " et modifiables par un administrateur" : " et en lecture seule"}.` : undefined}
        actions={canEdit && <div className="row">
          <input className="input sm" style={{ width: 220 }} placeholder="Nom de la version (facultatif)" value={name} onChange={(e) => setName(e.target.value)} aria-label="Nom de la version" />
          <Button size="sm" disabled={!edits.size} onClick={() => setEdits(new Map())}><RotateCcw />Annuler</Button>
          <Button size="sm" variant="primary" disabled={!edits.size || save.isPending} onClick={submit}><Save />Enregistrer{edits.size ? ` (${edits.size})` : ""}</Button>
        </div>}>
        <div className="row wrap pdp-filters">
          <SearchSelect value={program} onChange={setProgram} options={programOptions} placeholder="Tous les programmes" ariaLabel="Programme" width={260} />
          <button type="button" className={`btn sm ${onlyNoBom ? "primary" : ""}`} onClick={() => setOnlyNoBom((v) => !v)} title="N'afficher que les programmes sans nomenclature">Sans nomenclature{allPrograms.length ? ` (${allPrograms.filter((p) => !p.has_bom).length})` : ""}</button>
          <button type="button" className={`btn sm ${onlyNoPlan ? "primary" : ""}`} onClick={() => setOnlyNoPlan((v) => !v)} title="N'afficher que les programmes sans aucune quantité planifiée">Sans plan{allPrograms.length ? ` (${allPrograms.filter((p) => p.source === "none" || p.values.every((v) => v === 0)).length})` : ""}</button>
          {pastCount > 0 && <button type="button" className={`btn sm ${showingPast ? "primary" : ""}`} title={showingPast ? "Revenir à la semaine en cours et aux suivantes" : `Afficher les ${pastCount} semaine(s) passée(s) depuis le point zéro (${d?.first_week?.replace("-W", " S") ?? ""})`}
            onClick={() => setRange(showingPast ? { from: d!.current_week, to: allWeeks[allWeeks.length - 1].week } : { from: allWeeks[0].week, to: allWeeks[allWeeks.length - 1].week })}><History />{showingPast ? "Masquer le passé" : `Depuis le point zéro (${pastCount})`}</button>}
          <span className="subtle small" style={{ marginLeft: "auto" }}>{programs.length} / {allPrograms.length} programme(s){perimeter.planner ? ` · approvisionneur ${perimeter.planner}` : ""}</span>
        </div>
        {weekChips.length > 1 && <WeekRangeBar weeks={weekChips} range={effRange} onChange={setRange} />}
        {q.isError ? <ErrorBox error={q.error} retry={() => q.refetch()} /> : q.isLoading || !d ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div>
          : programs.length === 0 ? <Empty title="Aucun programme" hint={allPrograms.length ? "Aucun programme ne correspond aux filtres." : "Déclarer des programmes et leurs nomenclatures dans le Référentiel, ou importer un PDP."} /> : (
            <SheetGrid ariaLabel="Plan de production" className="pdp-sheet" canEdit={canEdit} cells={cells} onCommit={apply} onClear={clear} fillAxis="both" colWidth={78}
              onReorder={canEdit ? moveRow : undefined}
              corner="Programme" colClass={(c) => (cols[c].week === d?.current_week ? "current-week" : cols[c].past ? "past-week" : "")}
              sideHeader={<span title="Taux de rebut du programme : besoin de la semaine = PDP × (1 + taux / 100), avant l'éclatement par la nomenclature. Un changement s'applique à la semaine en cours et aux suivantes ; les semaines passées gardent leur taux.">Taux rebut (%)</span>}
              rowSides={programs.map((p) => <ScrapInput key={p.program_id} value={p.scrap_pct} canEdit={canEdit} onSave={(v) => saveScrap.mutate({ program_id: p.program_id, rate_pct: v })} />)}
              colHeaders={cols.map((w) => <span title={w.past ? `Semaine passée : ${ORIGIN_LABELS[w.origin ?? "none"]}${w.origin_version ? ` (« ${w.origin_version} »)` : ""}` : undefined}>{w.week.replace("-W", " S")}<span className="sub">{fmtDate(w.week_start, "dd/MM")}{w.past && w.origin && w.origin !== "active" && w.origin !== "none" ? " · hist." : ""}</span></span>)}
              rowHeaders={programs.map((p) => <><b>{p.name}</b> <span className="subtle mono small">{p.program_id}</span>{p.source === "none" && <Badge tone="outline" title="Aucune quantité planifiée sur les semaines affichées">aucun plan</Badge>}{!p.active && <Badge tone="outline">inactif</Badge>}{!p.has_bom && <Badge tone="warning" title="Aucune nomenclature : ce plan n'éclate en aucun besoin de composant (Référentiel › Nomenclatures)">sans nomenclature</Badge>}</>)} />
          )}
        {save.error && <div className="error-box" style={{ margin: 12 }}>{(save.error as Error).message}</div>}
        <p className="small subtle" style={{ padding: "8px 12px" }}>Semaine en cours et suivantes : la version active. Semaines passées : la version active si elle les contient, sinon la version connue au lundi de la semaine, sinon la première importée ensuite (« hist. » dans l'en-tête, détail au survol). Une saisie crée une nouvelle version active à partir de la précédente ; une semaine passée modifiée par un administrateur y est enregistrée entière. Désactiver la version revient à l'état précédent.</p>
      </Card>

      <div className="grid cols-2">
        <Card title="Importer un PDP (classeur)" hint="xlsx · 1re colonne : programme (nom ou id) · en-têtes : 2026-W11, S11-26, 2028W24 ou dates">
          <div className="form-grid">
            <Field label="Nom de la version"><input className="input" value={pdpName} onChange={(e) => setPdpName(e.target.value)} placeholder="ex. PDP S38 – v2" /></Field>
            <Field label="Activation"><label className="checkbox" style={{ height: 34 }}><input type="checkbox" checked={activate} onChange={(e) => setActivate(e.target.checked)} />activer immédiatement</label></Field>
          </div>
          <input ref={fileInput} type="file" accept=".xlsx" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) importPdp.mutate(f); e.target.value = ""; }} />
          {!canEdit && <div className="note" style={{ marginTop: 12 }}>L'import du PDP est réservé aux managers et administrateurs (Référentiel › Approvisionneurs).</div>}
          {canEdit && !isAdmin && <p className="small subtle" style={{ marginTop: 8 }}>Les semaines passées d'un classeur sont ignorées (réservées à un administrateur).</p>}
          {canEdit && <div className="dropzone" style={{ marginTop: 12 }} onClick={() => fileInput.current?.click()} onDragOver={(e) => { e.preventDefault(); e.currentTarget.classList.add("over"); }} onDragLeave={(e) => e.currentTarget.classList.remove("over")} onDrop={(e) => { e.preventDefault(); e.currentTarget.classList.remove("over"); const f = e.dataTransfer.files?.[0]; if (f) importPdp.mutate(f); }}>
            <FileUp /><div>{importPdp.isPending ? "Import en cours…" : "Déposer le classeur PDP ici ou cliquer"}</div>
          </div>}
          {importPdp.error && <div className="error-box" style={{ marginTop: 12 }}>{(importPdp.error as Error).message}</div>}
          {report && <div className="row" style={{ marginTop: 12 }}><CheckCircle2 color="var(--ok)" /><b>{fmtInt(report.created)}</b> ligne(s) · <b>{fmtInt(report.ignored)}</b> avertissement(s){report.version && <> · version <b>{report.version.name}</b> {report.version.active && <Badge tone="ok">active</Badge>}</>}<Button size="sm" variant="ghost" onClick={() => setReport(null)}>Fermer</Button></div>}
          {report && report.notes.length > 0 && <ul className="small subtle" style={{ marginTop: 8 }}>{report.notes.slice(0, 20).map((n, i) => <li key={i}>{n}</li>)}{report.notes.length > 20 && <li>… {report.notes.length - 20} autres</li>}</ul>}
        </Card>

        <Card flush title="Versions (imports et saisies)">
          {versions.isError ? <ErrorBox error={versions.error} /> : versions.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : !versions.data?.length ? <Empty title="Aucune version" hint="Importer un classeur ou saisir la feuille : la première version devient le PDP effectif." /> : (
            <table className="tbl">
              <thead><tr><th>Version</th><th>Origine</th><th className="num">Lignes</th><th className="num">Prog.</th><th>Semaines</th><th>Par</th><th>Statut</th><th></th></tr></thead>
              <tbody>{versions.data.map((v) => (
                <tr key={v.id}>
                  <td><b>{v.name}</b>{v.note && <span className="sub">{v.note}</span>}</td><td className="subtle">{v.source_file}</td>
                  <td className="num">{fmtInt(v.line_count)}</td><td className="num">{v.programs}</td>
                  <td>{fmtDate(v.first_week)} → {fmtDate(v.last_week)}</td>
                  <td className="subtle small">{v.imported_by}<br />{fmtDateTime(v.imported_at)}</td>
                  <td>{v.active ? <Badge tone="ok">active</Badge> : <Badge tone="neutral">inactive</Badge>}</td>
                  <td className="row">
                    {canEdit && <Button size="sm" onClick={() => act.mutate({ id: v.id, on: !v.active })}>{v.active ? <><PowerOff />Désactiver</> : <><Power />Activer</>}</Button>}
                    {canEdit && <Button size="sm" variant="ghost" onClick={() => { if (window.confirm(`Supprimer la version « ${v.name} » ?`)) del.mutate(v.id); }}><Trash2 /></Button>}
                  </td>
                </tr>
              ))}</tbody>
            </table>
          )}
        </Card>
      </div>
    </div>
  );
}

/** The scrap rate of one programme : typed in place, saved on Enter / blur when it changes. */
function ScrapInput({ value, canEdit, onSave }: { value: number; canEdit: boolean; onSave: (v: number) => void }) {
  const [text, setText] = useState(String(value));
  useEffect(() => { setText(String(value)); }, [value]);
  const commit = () => {
    const n = Number(text.replace(",", "."));
    if (!Number.isFinite(n) || n < 0) { setText(String(value)); return; }
    if (n !== value) onSave(n);
  };
  return <input className={`scrap-input ${value ? "nonzero" : ""}`} value={text} disabled={!canEdit} aria-label="Taux de rebut (%)" inputMode="decimal"
    title={canEdit ? "Taux de rebut (%) : Entrée ou quitter la cellule pour enregistrer ; s'applique à partir de la semaine en cours" : `Taux de rebut ${value} %`}
    onChange={(e) => setText(e.target.value)} onBlur={commit} onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); (e.target as HTMLInputElement).blur(); } else if (e.key === "Escape") setText(String(value)); }} />;
}
