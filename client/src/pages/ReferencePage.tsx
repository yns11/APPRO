import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { CalendarDays } from "lucide-react";
import { WeeklyParamsDrawer } from "@/components/WeeklyParamsDrawer";
import { DataTable, type Column } from "@/components/DataTable";
import { useArticles, useBom, useLinks, useOverrides, usePlan, usePrograms, useSuppliers, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { api } from "@/lib/api";
import { Badge, Button, Card, Empty, ErrorBox, SkeletonBlock, Tabs, useToast } from "@/components/ui";
import { fmtQty } from "@/lib/format";
import type { ArticleRef, BomRef, LinkRef, ProgramRef, SupplierRef } from "@/lib/types";

type Tab = "articles" | "suppliers" | "links" | "programs" | "bom";
const WD = ["", "Lun", "Mar", "Mer", "Jeu", "Ven", "Sam", "Dim"];

/** Reference data browser with inline parameter overrides (article thresholds, supplier link rules). */
export default function ReferencePage() {
  const { perimeter } = usePerimeter();
  const [tab, setTab] = useState<Tab>("articles");
  const [program, setProgram] = useState<string>("");
  const [weeklyOf, setWeeklyOf] = useState<string | null>(null);
  const toast = useToast();
  const articles = useArticles(perimeter.planner);
  const suppliers = useSuppliers();
  const links = useLinks();
  const programs = usePrograms();
  const bom = useBom();
  const plan = usePlan(program || undefined);
  const overrides = useOverrides();
  const setParam = useWrite((b: { scope: string; key1: string; key2?: string; field: string; value: string | number }) => api.put("/api/params/overrides", b), () => toast.push("Paramètre enregistré", "success"));
  const ovKeys = useMemo(() => new Set((overrides.data ?? []).map((o) => `${o.scope}|${o.key1}|${o.key2}|${o.field}`)), [overrides.data]);
  const isOv = (scope: string, k1: string, field: string, k2 = "") => ovKeys.has(`${scope}|${k1}|${k2}|${field}`);

  const Editable = ({ scope, key1, key2, field, value }: { scope: "article" | "link"; key1: string; key2?: string; field: string; value: number }) => (
    <input className="input sm num" style={{ width: 84, textAlign: "right", borderColor: isOv(scope, key1, field, key2) ? "var(--brand)" : undefined }} type="number" step="any" defaultValue={value} title={isOv(scope, key1, field, key2) ? "Valeur surchargée dans l'application" : "Valeur ERP – modifier crée une surcharge"}
      onClick={(e) => e.stopPropagation()} onBlur={(e) => { const v = Number(e.target.value); if (!Number.isNaN(v) && v !== value) setParam.mutate({ scope, key1, key2, field, value: v }); }} onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} aria-label={`${field} ${key1}`} />
  );
  const num = (field: string, label: string, scope: "article" | "link" = "article", get: (r: ArticleRef) => number = (r) => (r as unknown as Record<string, number>)[field]): Column<ArticleRef> => ({
    key: field, label, get, num: true, render: (r) => <Editable scope={scope} key1={r.article_id} field={field} value={get(r)} />,
  });
  const articleCols = useMemo<Column<ArticleRef>[]>(() => [
    { key: "article", label: "Article", get: (a) => `${a.article_id} ${a.designation}`, render: (a) => <><Link to={`/articles/${encodeURIComponent(a.article_id)}`}><b>{a.article_id}</b></Link><span className="sub">{a.designation}</span></> },
    { key: "unit", label: "Unité", get: (a) => a.unit, filter: "select" },
    { key: "planner", label: "Appro", get: (a) => a.planner, filter: "select" },
    num("coverage_target_days", "Couverture cible (j)"), num("alert_red_days", "Seuil rouge (j)"), num("alert_yellow_days", "Seuil orange (j)"),
    num("overstock_days", "Surstock (j)"), num("safety_stock_qty", "Stock sécurité"), num("order_cycle_days", "Cycle cde (j)"),
    { key: "active", label: "Actif", get: (a) => a.active ? "oui" : "non", filter: "select", render: (a) => a.active ? <Badge tone="ok">oui</Badge> : <Badge tone="neutral">non</Badge> },
    { key: "weeks", label: "Semaines", get: () => "", filter: "none", sortable: false, render: (a) => <Button size="sm" variant="ghost" title="Personnaliser ces paramètres semaine par semaine" aria-label={`Paramètres hebdomadaires ${a.article_id}`} onClick={() => setWeeklyOf(a.article_id)}><CalendarDays /></Button> },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [ovKeys]);
  const linkCols = useMemo<Column<LinkRef>[]>(() => [
    { key: "article", label: "Article", get: (l) => l.article_id, render: (l) => <Link to={`/articles/${encodeURIComponent(l.article_id)}`}><b>{l.article_id}</b></Link> },
    { key: "supplier", label: "Fournisseur", get: (l) => `${l.supplier_id} ${l.supplier_name}`, render: (l) => <>{l.supplier_id}<span className="sub">{l.supplier_name}</span></> },
    ...(["moq", "pack_qty", "lead_time_days", "quota_pct", "priority"] as const).map((f) => ({
      key: f, label: { moq: "MOQ", pack_qty: "PLA", lead_time_days: "Délai (j ouvrés)", quota_pct: "Quota %", priority: "Priorité" }[f], get: (l: LinkRef) => l[f], num: true,
      render: (l: LinkRef) => <Editable scope="link" key1={l.article_id} key2={l.supplier_id} field={f} value={l[f]} />,
    })),
    { key: "active", label: "Actif", get: (l) => l.active ? "oui" : "non", filter: "select", render: (l) => l.active ? <Badge tone="ok">oui</Badge> : <Badge tone="neutral">non</Badge> },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  ], [ovKeys]);
  const supplierCols = useMemo<Column<SupplierRef>[]>(() => [
    { key: "id", label: "COFOR", get: (x) => x.supplier_id, render: (x) => <b>{x.supplier_id}</b> },
    { key: "name", label: "Nom", get: (x) => x.name },
    { key: "country", label: "Pays", get: (x) => x.country, filter: "select" },
    { key: "days", label: "Jours de livraison", get: (x) => x.delivery_weekdays.map((d) => WD[d]).join(" ") },
    { key: "contact", label: "Contact", get: (x) => x.contact },
    { key: "active", label: "Actif", get: (x) => x.active ? "oui" : "non", filter: "select", render: (x) => x.active ? <Badge tone="ok">oui</Badge> : <Badge tone="neutral">non</Badge> },
  ], []);
  const bomCols = useMemo<Column<BomRef>[]>(() => [
    { key: "program", label: "Programme", get: (b) => `${b.program_name} ${b.program_id}`, render: (b) => <>{b.program_name}<span className="sub mono">{b.program_id}</span></> },
    { key: "article", label: "Composant", get: (b) => b.article_id, render: (b) => <Link to={`/articles/${encodeURIComponent(b.article_id)}`}>{b.article_id}</Link> },
    { key: "qty", label: "Qté / unité", get: (b) => b.qty_per, num: true },
    { key: "unit", label: "Unité", get: (b) => b.unit, filter: "select" },
    { key: "scrap", label: "Rebut %", get: (b) => b.scrap_pct, num: true },
  ], []);
  const programCols = useMemo<Column<ProgramRef>[]>(() => [
    { key: "name", label: "Programme", get: (p) => `${p.name} ${p.program_id}`, render: (p) => <><b>{p.name}</b><span className="sub mono">{p.program_id}</span></> },
    { key: "family", label: "Famille", get: (p) => p.family, filter: "select" },
    { key: "components", label: "Composants", get: (p) => p.components, num: true },
  ], []);

  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Référentiel</h1><p>Données ERP ; les champs modifiables créent une surcharge applicative (bordure bleue), tracée et réversible.</p></div>
      </div>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "articles", label: "Articles", count: articles.data?.length }, { id: "links", label: "Article ↔ fournisseur", count: links.data?.length }, { id: "suppliers", label: "Fournisseurs", count: suppliers.data?.length }, { id: "programs", label: "Programmes & PDP", count: programs.data?.length }, { id: "bom", label: "Nomenclatures", count: bom.data?.length }]} />

      {tab === "articles" && <Card flush>{articles.isError ? <ErrorBox error={articles.error} /> : articles.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : <DataTable rows={articles.data ?? []} columns={articleCols} rowKey={(a) => a.article_id} compact />}</Card>}
      <WeeklyParamsDrawer articleId={weeklyOf} onClose={() => setWeeklyOf(null)} />
      {tab === "links" && <Card flush>{links.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : <DataTable rows={links.data ?? []} columns={linkCols} rowKey={(l) => `${l.article_id}-${l.supplier_id}`} compact />}</Card>}
      {tab === "suppliers" && <Card flush>{suppliers.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : <DataTable rows={suppliers.data ?? []} columns={supplierCols} rowKey={(x) => x.supplier_id} compact />}</Card>}
      {tab === "programs" && (
        <div className="grid cols-2">
          <Card flush title="Programmes de production" hint="cliquer pour voir le PDP ERP">
            {programs.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : <DataTable rows={programs.data ?? []} columns={programCols} rowKey={(p) => p.program_id} compact onRowClick={(p) => setProgram(p.program_id)} rowClass={(p) => (program === p.program_id ? "selected" : "")} />}
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
      {tab === "bom" && <Card flush>{bom.isLoading ? <div style={{ padding: 20 }}><SkeletonBlock /></div> : <DataTable rows={bom.data ?? []} columns={bomCols} rowKey={(b) => `${b.program_id}-${b.article_id}`} compact />}</Card>}
    </div>
  );
}
