import { useState } from "react";
import { Link } from "react-router-dom";
import { RotateCcw } from "lucide-react";
import { api } from "@/lib/api";
import { useOverrides, useParamEffective, useParamSchema, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { BASE_SIZES, DISPLAY_DEFAULTS, FONTS, GRID_SIZES, fontStack, type FontId, type GridFontId } from "@/lib/display";
import { Badge, Button, Card, ErrorBox, SkeletonBlock, Tabs, useToast } from "@/components/ui";

type Tab = "rules" | "display" | "admin";
const ROLE_LABELS: Record<string, string> = { reader: "lecture seule", appro: "approvisionneur", manager: "manager", admin: "administrateur" };

/** Settings in three tabs: supply rules (engine parameters), display (fonts, sizes), administration. */
export default function SettingsPage() {
  const { rights } = usePerimeter();
  const [tab, setTab] = useState<Tab>("rules");
  return (
    <div className="page">
      <div className="page-header"><div className="title"><h1>Paramètres</h1><p>Règles d'approvisionnement du moteur, personnalisation de l'affichage, administration. Les paramètres par article se modifient dans le Référentiel, et par semaine depuis le bouton « Semaines » de la table des articles.</p></div></div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "rules", label: "Règles d'approvisionnement" }, { id: "display", label: "Affichage" }, { id: "admin", label: "Administration" }]} />
      {tab === "rules" && <RulesTab canEdit={rights.canManageParams} />}
      {tab === "display" && <DisplayTab />}
      {tab === "admin" && <AdminTab />}
    </div>
  );
}

/* ---------------------------------------------------------------- rules */
function RulesTab({ canEdit }: { canEdit: boolean }) {
  const schema = useParamSchema();
  const effective = useParamEffective();
  const overrides = useOverrides({ scope: "global" });
  const toast = useToast();
  const setParam = useWrite((b: { scope: string; key1?: string; key2?: string; field: string; value: unknown }) => api.put("/api/params/overrides", { key1: "", key2: "", ...b }), () => toast.push("Règle enregistrée", "success"));
  const del = useWrite((id: string) => api.del(`/api/params/overrides/${id}`), () => toast.push("Surcharge supprimée – valeur par défaut rétablie"));
  const globalOv = new Map((overrides.data ?? []).filter((o) => o.scope === "global").map((o) => [o.field, o]));
  return (
    <Card flush title="Règles globales du moteur" hint={canEdit ? "valeur effective = défaut ← configuration ← surcharge globale ← paramètre d'appel ; les règles valent pour tous les approvisionneurs" : "lecture seule : les règles globales sont modifiées par les managers et administrateurs"}>
      {schema.isError ? <ErrorBox error={schema.error} /> : schema.isLoading || effective.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock rows={10} /></div> : (
        <table className="tbl compact">
          <thead><tr><th>Règle</th><th>Description</th><th>Valeur effective</th><th></th></tr></thead>
          <tbody>{(schema.data ?? []).map((p) => {
            const cur = effective.data?.[p.field];
            const ov = globalOv.get(p.field);
            const onChange = (v: unknown) => setParam.mutate({ scope: "global", field: p.field, value: v });
            return (
              <tr key={p.field}>
                <td className="mono">{p.field}{ov && <span className="sub"><Badge tone="brand">surchargé</Badge></span>}</td>
                <td className="small" style={{ whiteSpace: "normal", maxWidth: 420 }}>{p.description}<span className="sub">défaut : {String(Array.isArray(p.default) ? p.default.join(",") : p.default ?? "vide")}</span></td>
                <td>
                  {p.options ? <select className="select sm" disabled={!canEdit} value={String(cur ?? "")} onChange={(e) => onChange(e.target.value)}>{p.options.map((o) => <option key={o} value={o}>{o}</option>)}</select>
                    : p.type === "bool" ? <label className="checkbox"><input type="checkbox" disabled={!canEdit} checked={Boolean(cur)} onChange={(e) => onChange(e.target.checked)} />{cur ? "oui" : "non"}</label>
                    : p.type === "list" ? <input className="input sm" disabled={!canEdit} defaultValue={Array.isArray(cur) ? cur.join(",") : String(cur ?? "")} onBlur={(e) => onChange(e.target.value)} style={{ width: 220 }} />
                    : <input className="input sm" type="number" disabled={!canEdit} defaultValue={cur === null || cur === undefined ? "" : String(cur)} placeholder={p.type === "int?" ? "vide = illimité" : ""} onBlur={(e) => { if (e.target.value !== String(cur ?? "")) onChange(e.target.value === "" ? null : Number(e.target.value)); }} style={{ width: 120 }} />}
                </td>
                <td>{ov && canEdit && <Button size="xs" variant="ghost" title="Rétablir la valeur par défaut" onClick={() => del.mutate(ov.id)}><RotateCcw /></Button>}</td>
              </tr>
            );
          })}</tbody>
        </table>
      )}
    </Card>
  );
}

/* ---------------------------------------------------------------- display */
const SAMPLE_COLS = ["2026 S41", "2026 S42", "2026 S43", "2026 S44"];
const SAMPLE_ROWS: [string, number[]][] = [["Besoin", [1800, 2100, 2400, 2400]], ["Plan", [2916, 1800, 2088, 2124]], ["Scenario Plan", [1818, 2106, -1890, 510]]];

function DisplayTab() {
  const { perimeter, set } = usePerimeter();
  const d = perimeter.display;
  const upd = (patch: Partial<typeof d>) => set({ display: { ...d, ...patch } });
  const preview = (font: FontId | GridFontId, size: number) => (
    <table className="font-preview" style={{ fontFamily: fontStack(font, d.font), fontSize: size }}>
      <thead><tr><th>Variable</th>{SAMPLE_COLS.map((c) => <th key={c}>{c}</th>)}</tr></thead>
      <tbody>{SAMPLE_ROWS.map(([l, vs]) => <tr key={l}><td>{l}</td>{vs.map((v, i) => <td key={i} className={v < 0 ? "neg" : ""}>{v.toLocaleString("fr-FR")}</td>)}</tr>)}</tbody>
    </table>
  );
  return (
    <div className="grid cols-2">
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
          <Button size="sm" onClick={() => upd(DISPLAY_DEFAULTS)}><RotateCcw />Valeurs d'origine</Button>
        </div>
      </Card>
      <Card title="Tableau d'approvisionnement" hint="police et taille des cellules de la grille de simulation (tableau, fiche article)">
        <div className="row wrap">
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
        <p className="small subtle" style={{ marginTop: 10 }}>Retenues après essais sur la grille (colonnes de 72 px) : <b>Inter</b> et <b>IBM Plex Sans</b> pour la lisibilité des chiffres, <b>Source Sans 3</b> et <b>Roboto Condensed</b> pour afficher davantage de colonnes sans débordement. 11 px est la taille d'origine ; 12 px reste sans débordement jusqu'à six chiffres.</p>
      </Card>
    </div>
  );
}

/* ---------------------------------------------------------------- administration */
function AdminTab() {
  const { config, rights } = usePerimeter();
  const toast = useToast();
  const a = config?.access;
  const resetRules = useWrite(() => api.del("/api/params/overrides?scope=global"), () => toast.push("Toutes les règles globales sont revenues à leur valeur par défaut"));
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
          <li><b>manager</b> : en plus, règles globales, paramètres hebdomadaires de tout article, import des PDP.</li>
          <li><b>admin</b> : tous les droits, dont la table des approvisionneurs.</li>
          <li>Utilisateur non déclaré ou inactif : lecture seule.</li>
        </ul>
        <div className="row wrap"><Link className="btn sm" to="/referentiel">Ouvrir le Référentiel</Link></div>
      </Card>
      <Card title="Environnement" tight>
        <dl className="stack small">
          <div><h4>Source ERP</h4><div>{String(config?.data_source?.name ?? "…")}</div><div className="subtle mono" style={{ wordBreak: "break-all" }}>{String(config?.data_source?.folder ?? (config?.data_source?.catalog ? `${config.data_source.catalog}.${config.data_source.schema}` : ""))}</div></div>
          <div><h4>Aujourd'hui (date du calcul)</h4><div>{config?.as_of}</div></div>
          <div><h4>Initialisation du stock (point zéro)</h4><div>{config?.init_date}</div></div>
          <div><h4>Version</h4><div>{config?.version}</div></div>
        </dl>
        {rights.isAdmin && <div style={{ marginTop: 12 }}><Button size="sm" onClick={() => { if (window.confirm("Supprimer toutes les surcharges globales des règles du moteur ?")) resetRules.mutate(undefined); }}><RotateCcw />Rétablir toutes les règles par défaut</Button></div>}
      </Card>
    </div>
  );
}
