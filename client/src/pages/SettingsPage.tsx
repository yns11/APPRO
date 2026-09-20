import { RotateCcw } from "lucide-react";
import { api } from "@/lib/api";
import { useOverrides, useParamEffective, useParamSchema, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { Badge, Button, Card, Empty, ErrorBox, SkeletonBlock, useToast } from "@/components/ui";
import { fmtDateTime } from "@/lib/format";

/** Engine rules (global parameters) and list of every override (article / link / global). */
export default function SettingsPage() {
  const { config } = usePerimeter();
  const schema = useParamSchema();
  const effective = useParamEffective();
  const overrides = useOverrides();
  const toast = useToast();
  const setParam = useWrite((b: { scope: string; key1?: string; key2?: string; field: string; value: unknown }) => api.put("/api/params/overrides", { key1: "", key2: "", ...b }), () => toast.push("Règle enregistrée", "success"));
  const del = useWrite((id: string) => api.del(`/api/params/overrides/${id}`), () => toast.push("Surcharge supprimée – valeur par défaut rétablie"));
  const globalOv = new Map((overrides.data ?? []).filter((o) => o.scope === "global").map((o) => [o.field, o]));

  return (
    <div className="page">
      <div className="page-header"><div className="title"><h1>Paramètres & règles métier</h1><p>Toutes les règles de calcul sont paramétrables ici (valeurs globales) ou par article / fournisseur dans le Référentiel. Chaque modification est journalisée et s'applique immédiatement à tous les calculs.</p></div></div>

      <div className="grid cols-3">
        <Card title="Environnement" tight>
          <dl className="stack small">
            <div><h4>Source ERP</h4><div>{String(config?.data_source?.name ?? "…")}</div><div className="subtle mono" style={{ wordBreak: "break-all" }}>{String(config?.data_source?.folder ?? (config?.data_source?.catalog ? `${config.data_source.catalog}.${config.data_source.schema}` : ""))}</div></div>
            <div><h4>Date de référence</h4><div>{config?.as_of}</div></div>
            <div><h4>Utilisateur</h4><div>{config?.user}</div></div>
            <div><h4>Version</h4><div>{config?.version}</div></div>
          </dl>
        </Card>
        <Card className="cols-2" flush title="Règles globales du moteur" hint="valeur effective = défaut ← configuration ← surcharge globale ← paramètre d'appel" style={{ gridColumn: "span 2" }}>
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
                      {p.options ? <select className="select sm" value={String(cur ?? "")} onChange={(e) => onChange(e.target.value)}>{p.options.map((o) => <option key={o} value={o}>{o}</option>)}</select>
                        : p.type === "bool" ? <label className="checkbox"><input type="checkbox" checked={Boolean(cur)} onChange={(e) => onChange(e.target.checked)} />{cur ? "oui" : "non"}</label>
                        : p.type === "list" ? <input className="input sm" defaultValue={Array.isArray(cur) ? cur.join(",") : String(cur ?? "")} onBlur={(e) => onChange(e.target.value)} style={{ width: 220 }} />
                        : <input className="input sm" type="number" defaultValue={cur === null || cur === undefined ? "" : String(cur)} placeholder={p.type === "int?" ? "vide = illimité" : ""} onBlur={(e) => { if (e.target.value !== String(cur ?? "")) onChange(e.target.value === "" ? null : Number(e.target.value)); }} style={{ width: 120 }} />}
                    </td>
                    <td>{ov && <Button size="xs" variant="ghost" title="Rétablir la valeur par défaut" onClick={() => del.mutate(ov.id)}><RotateCcw /></Button>}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          )}
        </Card>
      </div>

      <Card flush title="Surcharges article / fournisseur" hint="modifiables dans le Référentiel ; supprimer une surcharge rétablit la valeur ERP">
        {overrides.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : !(overrides.data ?? []).some((o) => o.scope !== "global") ? <Empty title="Aucune surcharge" hint="Les valeurs ERP s'appliquent telles quelles." /> : (
          <table className="tbl compact">
            <thead><tr><th>Portée</th><th>Article</th><th>Fournisseur</th><th>Champ</th><th>Valeur</th><th>Modifié</th><th></th></tr></thead>
            <tbody>{(overrides.data ?? []).filter((o) => o.scope !== "global").map((o) => (
              <tr key={o.id}><td><Badge tone="outline">{o.scope}</Badge></td><td>{o.key1}</td><td>{o.key2 || "–"}</td><td className="mono">{o.field}</td><td><b>{o.value}</b></td><td className="subtle small">{o.updated_by} · {fmtDateTime(o.updated_at)}</td><td><Button size="xs" variant="ghost" onClick={() => del.mutate(o.id)}><RotateCcw /></Button></td></tr>
            ))}</tbody>
          </table>
        )}
      </Card>
    </div>
  );
}
