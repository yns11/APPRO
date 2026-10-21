import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertTriangle, CheckCircle2, Clock, Database, RefreshCw, RotateCcw } from "lucide-react";
import { api } from "@/lib/api";
import { useFixedRules, useOrphans, useOverrides, useParamEffective, useParamSchema, usePlantCalendar, useSupplierNames, useSyncStatus, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { usePageState } from "@/lib/pageState";
import { BASE_SIZES, DISPLAY_DEFAULTS, FONTS, GRID_SIZES, fontStack, type FontId, type GridFontId } from "@/lib/display";
import { Badge, Button, Card, ErrorBox, SkeletonBlock, SupplierLink, Tabs, useToast } from "@/components/ui";
import { DataTable, type Column } from "@/components/DataTable";
import { WEEKDAY_LONG, WEEKDAY_SHORT, fmtDate, fmtDateTime, fmtInt, fmtMinutes, fmtQty } from "@/lib/format";
import type { OrphanRow, SyncTable } from "@/lib/types";

type Tab = "rules" | "display" | "data" | "users";
const ROLE_LABELS: Record<string, string> = { reader: "lecture seule", appro: "approvisionneur", manager: "manager", admin: "administrateur" };

/** Settings : common rules (engine), display (per user), data (synchronisation, orphans, calendar, adherence), users. */
export default function SettingsPage() {
  const { rights } = usePerimeter();
  const [params, setParams] = useSearchParams();
  const tab = (["rules", "display", "data", "users"].includes(params.get("tab") ?? "") ? params.get("tab") : "rules") as Tab;
  const setTab = (t: Tab) => setParams(t === "rules" ? {} : { tab: t }, { replace: true });
  return (
    <div className="page">
      <div className="page-header"><div className="title"><h1>Paramètres</h1><p>Règles communes du moteur, affichage personnel, qualité des données et utilisateurs. Le paramétrage d'un article (politique de stock, jours de consommation, source de consommation, fournisseurs) se fait dans sa fiche, onglet Paramétrage ; les tables se gèrent dans le Référentiel.</p></div></div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "rules", label: "Règles communes" }, { id: "display", label: "Affichage" }, { id: "data", label: "Données" }, { id: "users", label: "Utilisateurs" }]} />
      {tab === "rules" && <RulesTab canEdit={rights.canManageParams} />}
      {tab === "display" && <DisplayTab />}
      {tab === "data" && <DataTab />}
      {tab === "users" && <UsersTab />}
    </div>
  );
}

/* ---------------------------------------------------------------- rules */
function RulesTab({ canEdit }: { canEdit: boolean }) {
  const schema = useParamSchema();
  const fixed = useFixedRules();
  const effective = useParamEffective();
  const overrides = useOverrides({ scope: "global" });
  const toast = useToast();
  const setParam = useWrite((b: { scope: string; key1?: string; key2?: string; field: string; value: unknown }) => api.put("/api/params/overrides", { key1: "", key2: "", ...b }), () => toast.push("Règle enregistrée", "success"));
  const del = useWrite((id: string) => api.del(`/api/params/overrides/${id}`), () => toast.push("Surcharge supprimée – valeur par défaut rétablie"));
  const globalOv = new Map((overrides.data ?? []).filter((o) => o.scope === "global").map((o) => [o.field, o]));
  const EDITABLE: Record<string, { label: string; unit: string; max: number; aria: string; explain: (v: number) => string }> = {
    target_tolerance_pct: { label: "Tolérance sous la cible", unit: "%", max: 100, aria: "Tolérance sous la cible (%)", explain: (v) => `Avec ${v} % : le CBN propose un retour à la cible dès que le stock projeté passe sous ${100 - v} % de la cible, et la quantité vise 100 % de la cible. Le plancher (!) reste strict.` },
    lead_margin_days: { label: "Marge de délai", unit: "j ouvrés", max: 30, aria: "Marge de délai (jours ouvrés)", explain: (v) => (v === 0 ? "0 = strict : la date limite de commande est livraison − délai ; toute proposition dont la livraison tombe dans le délai est urgente." : `Avec ${v} j : la date limite de commande est livraison − (délai − ${v}) jours ouvrés ; une proposition est tenable (pleine cible) tant que sa date limite n'est pas passée, sinon elle devient urgente (!) : plancher au jour du besoin ou livraison hors jour standard (◇) au premier jour ouvré tenable.`) },
  };
  const editable = (schema.data ?? []).filter((p) => p.field in EDITABLE);
  return (
    <div className="grid cols-2">
      <Card title="Règles réglables" hint={canEdit ? "les deux règles communes réglables ; valent pour tous les articles et tous les approvisionneurs" : "lecture seule : modifiables par les managers et administrateurs"}>
        {schema.isError ? <ErrorBox error={schema.error} /> : schema.isLoading || effective.isLoading ? <SkeletonBlock rows={3} /> : editable.map((p) => {
          const cur = effective.data?.[p.field];
          const ov = globalOv.get(p.field);
          const meta = EDITABLE[p.field];
          const val = Number(cur ?? p.default ?? 0);
          return (
            <div key={p.field} className="param editable">
              <span className="label">{meta.label}{ov && <Badge tone="brand">surchargée</Badge>}</span>
              <span className="value"><input className="input sm" type="number" min={0} max={meta.max} step={1} disabled={!canEdit} defaultValue={String(cur ?? "")} style={{ width: 80 }} aria-label={meta.aria}
                onBlur={(e) => { if (e.target.value !== String(cur ?? "") && e.target.value !== "") setParam.mutate({ scope: "global", field: p.field, value: Number(e.target.value) }); }} /><small>{meta.unit}</small>
                {ov && canEdit && <Button size="xs" variant="ghost" title={`Rétablir la valeur par défaut (${String(p.default)} ${meta.unit})`} onClick={() => del.mutate(ov.id)}><RotateCcw /></Button>}</span>
              <span className="small subtle">{p.description}. Défaut : {String(p.default)} {meta.unit}. {meta.explain(val)}</span>
            </div>
          );
        })}
      </Card>
      <Card title="Règles figées dans l'application" hint="communes à tous les articles ; détaillées dans le guide du moteur (docs/guide_moteur.md)">
        {fixed.isLoading ? <SkeletonBlock rows={6} /> : (
          <div className="rules-list">
            {(fixed.data ?? []).map((r) => <div key={r.name} className="rule"><div className="name">{r.name}</div><div className="value small">{r.value}</div></div>)}
            <div className="rule"><div className="name">Délai fournisseur</div><div className="value small">jours ouvrés, inclusif (lundi + 5 = lundi suivant) ; valeur du fournisseur, exception possible par lien</div></div>
            <div className="rule"><div className="name">Plafonds</div><div className="value small">par livraison et par semaine, sur le lien article ↔ fournisseur ; l'excédent glisse à la livraison / semaine suivante ou à l'autre fournisseur (quotas) ; manque inévitable affiché et alerté</div></div>
            <div className="rule"><div className="name">Refus d'une proposition</div><div className="value small">bloque le fournisseur ce jour-là seulement ; un mono-source glisse au jour de livraison ouvert suivant ; un refus daté avant aujourd'hui a expiré et n'est plus affiché</div></div>
            <div className="rule"><div className="name">Jours stricts</div><div className="value small">fournisseur à jours stricts : jamais de livraison hors jour standard (◇) ; le besoin non tenable devient un plancher (!) au jour standard</div></div>
          </div>
        )}
      </Card>
    </div>
  );
}

/* ---------------------------------------------------------------- display */
const SAMPLE_COLS = ["2026 S41", "2026 S42", "2026 S43", "2026 S44"];
const SAMPLE_ROWS: [string, number[]][] = [["Besoin", [1800, 2100, 2400, 2400]], ["Quantité à retenir", [2916, 1800, 2088, 2124]], ["Stock projeté", [1818, 2106, 0, 510]]];

function DisplayTab() {
  const { perimeter, set } = usePerimeter();
  const d = perimeter.display;
  const upd = (patch: Partial<typeof d>) => set({ display: { ...d, ...patch } });
  const preview = (font: FontId | GridFontId, size: number) => (
    <table className="font-preview" style={{ fontFamily: fontStack(font, d.font), fontSize: size }}>
      <thead><tr><th>Variable</th>{SAMPLE_COLS.map((c) => <th key={c}>{c}</th>)}</tr></thead>
      <tbody>{SAMPLE_ROWS.map(([l, vs]) => <tr key={l}><td>{l}</td>{vs.map((v, i) => <td key={i} className={v === 0 ? "neg" : ""}>{v.toLocaleString("fr-FR")}</td>)}</tr>)}</tbody>
    </table>
  );
  return (
    <div className="grid cols-2">
      <Card title="Tableaux d'approvisionnement" hint="fenêtre affichée (l'horizon de calcul est fixe : 52 semaines) · enregistré dans ce navigateur">
        <div className="param-grid">
          <div className="param editable"><span className="label">Semaines passées</span><span className="value"><select className="select sm" value={perimeter.historyWeeks} onChange={(e) => set({ historyWeeks: Number(e.target.value) })} aria-label="Semaines passées affichées">{[0, 1, 2, 3, 4, 6, 8, 12].map((n) => <option key={n} value={n}>{n}</option>)}</select><small>avant la semaine en cours</small></span></div>
          <div className="param editable"><span className="label">Semaines détaillées</span><span className="value"><select className="select sm" value={perimeter.focusWeeks} onChange={(e) => set({ focusWeeks: Number(e.target.value) })} aria-label="Semaines détaillées jour par jour">{[0, 1, 2, 3, 4, 6, 8].map((n) => <option key={n} value={n}>{n}</option>)}</select><small>jour par jour après la semaine en cours (mode « Par défaut »)</small></span></div>
          <div className="param editable"><span className="label">Thème</span><span className="value"><select className="select sm" value={perimeter.theme} onChange={(e) => set({ theme: e.target.value as "light" | "dark" | "system" })} aria-label="Thème"><option value="system">système</option><option value="light">clair</option><option value="dark">sombre</option></select></span></div>
        </div>
        <div className="row wrap" style={{ marginTop: 14 }}>
          <label className="field"><span>Police du tableau</span>
            <select className="select sm" value={d.gridFont} onChange={(e) => upd({ gridFont: e.target.value as GridFontId })}>
              <option value="same">Comme l'application</option>
              {FONTS.filter((f) => f.id !== "system").map((f) => <option key={f.id} value={f.id}>{f.label}</option>)}
            </select></label>
          <label className="field"><span>Taille des cellules</span>
            <select className="select sm" value={d.gridSize} onChange={(e) => upd({ gridSize: Number(e.target.value) })}>{GRID_SIZES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}</select></label>
        </div>
        <h4 style={{ margin: "14px 0 6px" }}>Aperçu</h4>
        {preview(d.gridFont, d.gridSize)}
      </Card>
      <Card title="Police de l'application" hint="texte des pages, tableaux et formulaires · enregistré dans ce navigateur">
        <div className="font-options">
          {FONTS.map((f) => (
            <label key={f.id} className={`font-option ${d.font === f.id ? "selected" : ""}`}>
              <input type="radio" name="font" checked={d.font === f.id} onChange={() => upd({ font: f.id })} />
              <div><b style={{ fontFamily: f.stack }}>{f.label}</b><div className="small subtle">{f.note}</div><div className="sample" style={{ fontFamily: f.stack }}>Stock projeté 2 412 &lt; cible 2 400 · 0123456789 · P-00001423 CIRCLIP A25</div></div>
            </label>
          ))}
        </div>
        <div className="row wrap" style={{ marginTop: 12 }}>
          <label className="field"><span>Taille de base</span>
            <select className="select sm" value={d.baseSize} onChange={(e) => upd({ baseSize: Number(e.target.value) })}>{BASE_SIZES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}</select></label>
          <Button size="sm" onClick={() => { upd(DISPLAY_DEFAULTS); set({ historyWeeks: 2, focusWeeks: 2 }); }}><RotateCcw />Valeurs d'origine</Button>
        </div>
      </Card>
    </div>
  );
}

/* ---------------------------------------------------------------- data */
type DataSub = "sync" | "orphans" | "calendar";
function DataTab() {
  const [sub, setSub] = usePageState("parametres.data", { sub: "sync" as DataSub });
  const sync = useSyncStatus(60_000);
  const orphans = useOrphans();
  return (
    <div className="stack">
      <SyncBanner />
      <Tabs value={sub.sub} onChange={(s) => setSub({ sub: s })} tabs={[
        { id: "sync", label: "Synchronisation ERP", count: sync.data?.tables.length },
        { id: "orphans", label: "Articles hors référentiel", count: orphans.data?.length },
        { id: "calendar", label: "Calendrier usine" },
      ]} />
      {sub.sub === "sync" && <SyncCard />}
      {sub.sub === "orphans" && <OrphansCard />}
      {sub.sub === "calendar" && <CalendarCard />}
    </div>
  );
}

function SyncBanner() {
  const sync = useSyncStatus(60_000);
  const s = sync.data;
  if (!s) return null;
  if (!s.mirror) return <div className="banner neutral"><Database size={16} /><span className="small">Source de données : <b>{s.data_source}</b>. Le journal de synchronisation ne concerne que le miroir Lakebase (job Databricks horaire).</span></div>;
  if (s.status === "late") { const worst = Math.max(...s.tables.filter((t) => t.status === "late").map((t) => t.age_minutes ?? 0)); return <div className="banner"><AlertTriangle size={16} /><span className="small grow"><b>Synchronisation en retard</b> : la dernière copie de l'ERP date de {fmtMinutes(worst)} (seuil {fmtMinutes(s.late_after_minutes)}). Les commandes, réceptions et consommations affichées peuvent être anciennes ; vérifier le job <code>appro_sync</code> dans Databricks.</span></div>; }
  if (s.status === "unknown") return <div className="banner neutral"><Clock size={16} /><span className="small">Aucune exécution du job de synchronisation enregistrée.</span></div>;
  return <div className="banner ok"><CheckCircle2 size={16} /><span className="small">Synchronisation à jour : dernière copie le {fmtDateTime(s.last_sync_at ?? "")}.</span></div>;
}

function SyncCard() {
  const sync = useSyncStatus(60_000);
  const cols = useMemo<Column<SyncTable>[]>(() => [
    { key: "status", label: "", get: (t) => t.status, filter: "none", sortable: false, export: false, render: (t) => <span className={`status-dot ${t.status}`} title={t.status === "ok" ? "à jour" : t.status === "late" ? "en retard" : "inconnu"} /> },
    { key: "table", label: "Table miroir", get: (t) => t.table, render: (t) => <span className="mono">{t.table}</span> },
    { key: "synced", label: "Dernière copie", get: (t) => t.synced_at ?? "", render: (t) => t.synced_at ? fmtDateTime(t.synced_at) : <span className="subtle">jamais</span> },
    { key: "age", label: "Âge", get: (t) => t.age_minutes, num: true, render: (t) => t.age_minutes === null ? <span className="subtle">–</span> : <Badge tone={t.status === "late" ? "critical" : "ok"}>{fmtMinutes(t.age_minutes)}</Badge> },
    { key: "rows", label: "Lignes", get: (t) => t.rows, num: true, render: (t) => t.rows === null ? <span className="subtle">–</span> : fmtInt(t.rows) },
    { key: "source", label: "Source Unity Catalog", get: (t) => t.source, render: (t) => <span className="mono small">{t.source}</span> },
    { key: "run", label: "Exécution", get: (t) => t.run_id ?? "", render: (t) => <span className="mono small subtle">{t.run_id}</span> },
  ], []);
  return (
    <Card flush title="Journal de synchronisation" hint="une ligne par table miroir ; le job copie l'ERP toutes les heures, une table sans copie depuis plus de deux heures est en retard" actions={<Button size="sm" onClick={() => sync.refetch()}><RefreshCw />Actualiser</Button>}>
      {sync.isError ? <ErrorBox error={sync.error} /> : sync.isLoading || !sync.data ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : <DataTable rows={sync.data.tables} columns={cols} rowKey={(t) => t.table} compact exportTitle="Synchronisation ERP" footer={null} />}
    </Card>
  );
}

function OrphansCard() {
  const q = useOrphans();
  const names = useSupplierNames();
  const cols = useMemo<Column<OrphanRow>[]>(() => [
    { key: "article", label: "Article (ERP)", get: (o) => o.article_id, render: (o) => <b className="mono">{o.article_id}</b> },
    { key: "orders", label: "Commandes", get: (o) => o.orders, num: true, sum: true },
    { key: "receipts", label: "Réceptions", get: (o) => o.receipts, num: true, sum: true },
    { key: "desadv", label: "DESADV", get: (o) => o.desadv, num: true, sum: true },
    { key: "conso", label: "Consommation", get: (o) => o.consumption, num: true, render: (o) => fmtQty(o.consumption) },
    { key: "last", label: "Dernière date", get: (o) => o.last_date ?? "", render: (o) => fmtDate(o.last_date) },
    { key: "suppliers", label: "Fournisseurs", get: (o) => o.suppliers.map((s) => `${s} ${names.get(s) ?? ""}`).join(" "), render: (o) => <>{o.suppliers.map((s, i) => <span key={s}>{i > 0 && ", "}<SupplierLink id={s} name={names.get(s)} inline /></span>)}</> },
  ], [names]);
  return (
    <Card flush title="Articles présents dans l'ERP et absents du référentiel" hint="commandes, réceptions, DESADV ou consommations portant une référence inconnue de la table des articles : ignorés par les calculs tant qu'ils ne sont pas créés" actions={<Link className="btn sm" to="/referentiel?table=ref_articles">Ouvrir les articles</Link>}>
      {q.isError ? <ErrorBox error={q.error} /> : q.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : <DataTable rows={q.data ?? []} columns={cols} rowKey={(o) => o.article_id} compact emptyTitle="Aucun article orphelin" emptyHint="Toutes les références des flux ERP existent dans le référentiel." maxHeight="calc(100vh - 420px)" storageKey="data.orphans" exportTitle="Articles hors référentiel" />}
    </Card>
  );
}

function CalendarCard() {
  const q = usePlantCalendar();
  const c = q.data;
  return (
    <div className="grid cols-2">
      <Card title="Jours ouvrés" hint="les couvertures, seuils et délais comptent en jours ouvrés de ce calendrier">
        {!c ? <SkeletonBlock rows={3} /> : (
          <>
            <div className="day-picker" aria-label="Jours ouvrés">{[1, 2, 3, 4, 5, 6, 7].map((n) => <button key={n} type="button" disabled className={c.working_weekdays.includes(n) ? "on" : ""} title={WEEKDAY_LONG[n - 1]}>{WEEKDAY_SHORT[n - 1]}</button>)}</div>
            <p className="small subtle" style={{ marginTop: 10 }}>Défini par la configuration de l'application ({c.source}) ; les fermetures s'ajoutent en dates ISO séparées par des virgules. Modifiable par l'administrateur Databricks (variables d'environnement de l'app).</p>
          </>
        )}
      </Card>
      <Card title="Fermetures d'usine" hint="jours fériés et arrêts : aucun besoin, aucune livraison, non comptés dans les couvertures">
        {!c ? <SkeletonBlock rows={3} /> : c.holidays.length === 0 ? <p className="small subtle">Aucune fermeture déclarée.</p> : (
          <div className="calendar-strip">{c.holidays.map((h) => <span key={h} className="closure" title={h}>{fmtDate(h, "EEE dd/MM/yyyy")}</span>)}</div>
        )}
      </Card>
    </div>
  );
}

/* ---------------------------------------------------------------- users */
function UsersTab() {
  const { config, rights } = usePerimeter();
  const toast = useToast();
  const a = config?.access;
  const resetRules = useWrite(() => api.del("/api/params/overrides?scope=global"), () => toast.push("Toutes les règles communes sont revenues à leur valeur par défaut"));
  return (
    <div className="grid cols-3">
      <Card title="Votre accès" tight>
        <dl className="stack small">
          <div><h4>Utilisateur</h4><div>{config?.user}</div></div>
          <div><h4>Rôle</h4><div>{a ? ROLE_LABELS[a.role] : "…"}{a?.bootstrap ? " – table des approvisionneurs vide : tout le monde est administrateur" : ""}</div></div>
          {a?.planner_id && <div><h4>Approvisionneur</h4><div>{a.planner_id} · {a.name}</div></div>}
          <div><h4>Carnet en écriture</h4><div>{a?.is_admin ? "tous les articles" : a?.portfolio.length ? a.portfolio.join(", ") : "aucun"}{a?.delegated_from.length ? ` (délégations de ${a.delegated_from.join(", ")})` : ""}</div></div>
        </dl>
      </Card>
      <Card title="Approvisionneurs, rôles et délégations" tight>
        <p className="small">Les approvisionneurs (ID, prénom, e-mail Databricks, rôle, actif) et les délégations (délégant, destinataire, dates) sont des tables du Référentiel.</p>
        <ul className="small" style={{ margin: "8px 0 12px 18px", padding: 0 }}>
          <li><b>appro</b> : écrit sur son carnet (articles dont il est l'approvisionneur) et sur les carnets qui lui sont délégués.</li>
          <li><b>manager</b> : en plus, règles communes, paramètres hebdomadaires de tout article, import des PDP.</li>
          <li><b>admin</b> : tous les droits, dont la table des approvisionneurs et le pense-bête intégré (bouton ? de l'en-tête, texte modifiable).</li>
          <li>Utilisateur non déclaré ou inactif : lecture seule.</li>
        </ul>
        <div className="row wrap"><Link className="btn sm" to="/referentiel?table=ref_planners">Approvisionneurs</Link><Link className="btn sm" to="/referentiel?table=ref_delegations">Délégations</Link></div>
      </Card>
      <Card title="Environnement" tight>
        <dl className="stack small">
          <div><h4>Source ERP</h4><div>{String(config?.data_source?.name ?? "…")}</div><div className="subtle mono" style={{ wordBreak: "break-all" }}>{String(config?.data_source?.folder ?? (config?.data_source?.catalog ? `${config.data_source.catalog}.${config.data_source.schema}` : ""))}</div></div>
          <div><h4>Aujourd'hui (date du calcul)</h4><div>{config?.as_of}</div></div>
          <div><h4>Initialisation du stock (point zéro)</h4><div>{config?.init_date}</div></div>
          <div><h4>Horizon</h4><div>52 semaines</div></div>
          <div><h4>Version</h4><div>{config?.version}</div></div>
        </dl>
        {rights.isAdmin && <div style={{ marginTop: 12 }}><Button size="sm" onClick={() => { if (window.confirm("Rétablir les valeurs par défaut des règles communes (tolérance 15 %, marge de délai 1 j) ?")) resetRules.mutate(undefined); }}><RotateCcw />Rétablir les règles par défaut</Button></div>}
      </Card>
    </div>
  );
}

