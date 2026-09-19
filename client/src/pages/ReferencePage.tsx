import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Search } from "lucide-react";
import { useArticles, useBom, useLinks, useOverrides, usePlan, usePrograms, useSuppliers, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { api } from "@/lib/api";
import { Badge, Button, Card, Empty, ErrorBox, SkeletonBlock, Tabs, useToast } from "@/components/ui";
import { fmtQty } from "@/lib/format";

type Tab = "articles" | "suppliers" | "links" | "programs" | "bom";
const WD = ["", "Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"];

/** Reference data browser with inline parameter overrides (article thresholds, supplier link rules). */
export default function ReferencePage() {
  const { perimeter } = usePerimeter();
  const [tab, setTab] = useState<Tab>("articles");
  const [search, setSearch] = useState("");
  const [program, setProgram] = useState<string>("");
  const toast = useToast();
  const articles = useArticles(perimeter.planner);
  const suppliers = useSuppliers();
  const links = useLinks();
  const programs = usePrograms();
  const bom = useBom();
  const plan = usePlan(program || undefined);
  const overrides = useOverrides();
  const setParam = useWrite((b: { scope: string; key1: string; key2?: string; field: string; value: string | number }) => api.put("/api/params/overrides", b), () => toast.push("Paramètre enregistré", "success"));
  const s = search.trim().toLowerCase();
  const ovKeys = useMemo(() => new Set((overrides.data ?? []).map((o) => `${o.scope}|${o.key1}|${o.key2}|${o.field}`)), [overrides.data]);
  const isOv = (scope: string, k1: string, field: string, k2 = "") => ovKeys.has(`${scope}|${k1}|${k2}|${field}`);

  const Editable = ({ scope, key1, key2, field, value, unit }: { scope: "article" | "link"; key1: string; key2?: string; field: string; value: number; unit?: string }) => (
    <input className={`input sm num ${isOv(scope, key1, field, key2) ? "" : ""}`} style={{ width: 84, textAlign: "right", borderColor: isOv(scope, key1, field, key2) ? "var(--brand)" : undefined }} type="number" step="any" defaultValue={value} title={isOv(scope, key1, field, key2) ? "Valeur surchargée dans l'application" : "Valeur ERP – modifier crée une surcharge"}
      onBlur={(e) => { const v = Number(e.target.value); if (!Number.isNaN(v) && v !== value) setParam.mutate({ scope, key1, key2, field, value: v }); }} onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} aria-label={`${field} ${unit ?? ""}`} />
  );

  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Référentiel</h1><p>Données de base issues de l'ERP (Unity Catalog). Les champs modifiables créent une surcharge applicative (bordure bleue), traçée dans le journal et réversible dans Paramètres & règles.</p></div>
        <div className="actions"><div className="search"><Search /><input className="input" placeholder="Filtrer…" value={search} onChange={(e) => setSearch(e.target.value)} style={{ width: 260 }} /></div></div>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "articles", label: "Articles", count: articles.data?.length }, { id: "links", label: "Article ↔ fournisseur", count: links.data?.length }, { id: "suppliers", label: "Fournisseurs", count: suppliers.data?.length }, { id: "programs", label: "Programmes & PDP", count: programs.data?.length }, { id: "bom", label: "Nomenclatures", count: bom.data?.length }]} />

      {tab === "articles" && <Card flush>{articles.isError ? <ErrorBox error={articles.error} /> : articles.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : (
        <table className="tbl compact">
          <thead><tr><th>Article</th><th>Unité</th><th>Appro</th><th className="num">Couverture cible (j)</th><th className="num">Seuil rouge (j)</th><th className="num">Seuil orange (j)</th><th className="num">Surstock (j)</th><th className="num">Stock sécurité</th><th className="num">Cycle cde (j)</th><th>Actif</th></tr></thead>
          <tbody>{(articles.data ?? []).filter((a) => !s || `${a.article_id} ${a.designation}`.toLowerCase().includes(s)).map((a) => (
            <tr key={a.article_id}>
              <td><Link to={`/articles/${encodeURIComponent(a.article_id)}`}><b>{a.article_id}</b></Link><span className="sub">{a.designation}</span></td>
              <td className="subtle">{a.unit}</td><td>{a.planner}</td>
              <td className="num"><Editable scope="article" key1={a.article_id} field="coverage_target_days" value={a.coverage_target_days} /></td>
              <td className="num"><Editable scope="article" key1={a.article_id} field="alert_red_days" value={a.alert_red_days} /></td>
              <td className="num"><Editable scope="article" key1={a.article_id} field="alert_yellow_days" value={a.alert_yellow_days} /></td>
              <td className="num"><Editable scope="article" key1={a.article_id} field="overstock_days" value={a.overstock_days} /></td>
              <td className="num"><Editable scope="article" key1={a.article_id} field="safety_stock_qty" value={a.safety_stock_qty} unit={a.unit} /></td>
              <td className="num"><Editable scope="article" key1={a.article_id} field="order_cycle_days" value={a.order_cycle_days} /></td>
              <td>{a.active ? <Badge tone="ok">oui</Badge> : <Badge tone="neutral">non</Badge>}</td>
            </tr>
          ))}</tbody>
        </table>
      )}</Card>}

      {tab === "links" && <Card flush>{links.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : (
        <table className="tbl compact">
          <thead><tr><th>Article</th><th>Fournisseur</th><th className="num">MOQ</th><th className="num">PLA (conditionnement)</th><th className="num">Délai (j ouvrés)</th><th className="num">Quota %</th><th className="num">Priorité</th><th>Actif</th></tr></thead>
          <tbody>{(links.data ?? []).filter((l) => !s || `${l.article_id} ${l.supplier_id} ${l.supplier_name}`.toLowerCase().includes(s)).map((l) => (
            <tr key={`${l.article_id}-${l.supplier_id}`}>
              <td><Link to={`/articles/${encodeURIComponent(l.article_id)}`}><b>{l.article_id}</b></Link></td>
              <td>{l.supplier_id}<span className="sub">{l.supplier_name}</span></td>
              <td className="num"><Editable scope="link" key1={l.article_id} key2={l.supplier_id} field="moq" value={l.moq} /></td>
              <td className="num"><Editable scope="link" key1={l.article_id} key2={l.supplier_id} field="pack_qty" value={l.pack_qty} /></td>
              <td className="num"><Editable scope="link" key1={l.article_id} key2={l.supplier_id} field="lead_time_days" value={l.lead_time_days} /></td>
              <td className="num"><Editable scope="link" key1={l.article_id} key2={l.supplier_id} field="quota_pct" value={l.quota_pct} /></td>
              <td className="num"><Editable scope="link" key1={l.article_id} key2={l.supplier_id} field="priority" value={l.priority} /></td>
              <td>{l.active ? <Badge tone="ok">oui</Badge> : <Badge tone="neutral">non</Badge>}</td>
            </tr>
          ))}</tbody>
        </table>
      )}</Card>}

      {tab === "suppliers" && <Card flush>{suppliers.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : (
        <table className="tbl compact">
          <thead><tr><th>COFOR</th><th>Nom</th><th>Pays</th><th>Jours de livraison</th><th>Calendrier</th><th>Contact</th><th>Actif</th></tr></thead>
          <tbody>{(suppliers.data ?? []).filter((x) => !s || `${x.supplier_id} ${x.name}`.toLowerCase().includes(s)).map((x) => (
            <tr key={x.supplier_id}><td><b>{x.supplier_id}</b></td><td>{x.name}</td><td>{x.country}</td><td>{x.delivery_weekdays.map((d) => WD[d]).join(" ")}</td><td className="subtle">DEFAULT</td><td className="subtle">{x.contact || "–"}</td><td>{x.active ? <Badge tone="ok">oui</Badge> : <Badge tone="neutral">non</Badge>}</td></tr>
          ))}</tbody>
        </table>
      )}</Card>}

      {tab === "programs" && (
        <div className="grid cols-2">
          <Card flush title="Programmes de production" hint="cliquer pour voir le PDP ERP">
            {programs.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : (
              <table className="tbl compact">
                <thead><tr><th>Programme</th><th>Famille</th><th className="num">Composants</th></tr></thead>
                <tbody>{(programs.data ?? []).filter((p) => !s || `${p.program_id} ${p.name}`.toLowerCase().includes(s)).map((p) => (
                  <tr key={p.program_id} className={`clickable ${program === p.program_id ? "selected" : ""}`} onClick={() => setProgram(p.program_id)}><td><b>{p.name}</b><span className="sub mono">{p.program_id}</span></td><td>{p.family}</td><td className="num">{p.components}</td></tr>
                ))}</tbody>
              </table>
            )}
          </Card>
          <Card flush title={program ? `PDP ERP – ${program}` : "PDP ERP"} hint="quantités hebdomadaires (version ERP)">
            {!program ? <Empty title="Sélectionnez un programme" /> : plan.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : !plan.data?.length ? <Empty title="Aucun PDP pour ce programme" /> : (
              <div style={{ maxHeight: 520, overflow: "auto" }}>
                <table className="tbl compact"><thead><tr><th>Semaine</th><th>Lundi</th><th className="num">Quantité</th><th>Version</th></tr></thead>
                  <tbody>{plan.data.map((l, i) => <tr key={i}><td>{l.iso_week}</td><td className="subtle">{l.week_start}</td><td className="num">{fmtQty(l.qty)}</td><td className="subtle">{l.version}</td></tr>)}</tbody></table>
              </div>
            )}
          </Card>
        </div>
      )}

      {tab === "bom" && <Card flush>{bom.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : (
        <table className="tbl compact">
          <thead><tr><th>Programme</th><th>Composant</th><th className="num">Qté / unité</th><th>Unité</th><th className="num">Rebut %</th></tr></thead>
          <tbody>{(bom.data ?? []).filter((b) => !s || `${b.program_id} ${b.program_name} ${b.article_id}`.toLowerCase().includes(s)).map((b, i) => (
            <tr key={i}><td>{b.program_name}<span className="sub mono">{b.program_id}</span></td><td><Link to={`/articles/${encodeURIComponent(b.article_id)}`}>{b.article_id}</Link></td><td className="num">{b.qty_per}</td><td>{b.unit}</td><td className="num">{b.scrap_pct}</td></tr>
          ))}</tbody>
        </table>
      )}</Card>}
      <p className="small subtle">Astuce : <Button size="xs" variant="ghost" onClick={() => overrides.refetch()}>rafraîchir</Button> les surcharges après modification dans un autre onglet.</p>
    </div>
  );
}
