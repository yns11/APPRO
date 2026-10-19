import { useEffect, useMemo, useState } from "react";
import { FilePlus2, Search } from "lucide-react";
import { usePerimeterLists, useSearch, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { usePageState } from "@/lib/pageState";
import { ArticleLink, Badge, Button, Card, ErrorBox, Segmented, SkeletonBlock, SupplierLink, useToast } from "@/components/ui";
import { api } from "@/lib/api";
import { DataTable, type Column } from "@/components/DataTable";
import { SearchSelect } from "@/components/SearchSelect";
import { fmtDate, fmtInt, fmtQty, ORDER_KIND_LABELS, ORDER_TYPE_LABELS } from "@/lib/format";
import type { SearchKind, SearchRow } from "@/lib/types";

const KINDS: { id: SearchKind; label: string }[] = [
  { id: "receipts", label: "Réceptions" }, { id: "orders", label: "Commandes" }, { id: "desadv", label: "DESADV" },
];

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => { const t = setTimeout(() => setV(value), ms); return () => clearTimeout(t); }, [value, ms]);
  return v;
}

/**
 * Search page, in the spirit of a Power BI search report : one kind of line at a time (receipts
 * first), free text where « ; » separates alternatives, a date range, a supplier ; the result is a
 * filterable table (column filters accept « ; » too).
 */
/** « Flux actif » : an active article ↔ supplier link exists for the line ; « inactif » : none (former supplier, or a link to create). */
type LinkFilter = "" | "active" | "inactive";
/** Receipts : « CO » = open order, « PO déclarée » = closed order declared in the Référentiel (counted), « autres » = receipts that do not count. */
type OrderFilter = "" | "co" | "po" | "other";

export default function SearchPage() {
  const { perimeter, rights } = usePerimeter();
  const toast = useToast();
  const [st, patch] = usePageState("recherche", { kind: "receipts" as SearchKind, text: "", from: "", to: "", supplier: "", link: "" as LinkFilter, order: "" as OrderFilter });
  const { kind, text, from, to, supplier, link, order } = st;
  const canDeclare = rights.canEditTable("ref_closed_orders");
  const declare = useWrite((r: SearchRow) => api.put("/api/reference/ref_closed_orders/rows", { values: { purch_id: r.purch_id, supplier_id: r.supplier_id ?? "", article_id: r.article_id, note: "déclarée depuis la recherche", active: true } }),
    () => toast.push("PO déclarée : ses réceptions comptent désormais dans le stock", "success"));
  const setKind = (kind: SearchKind) => patch({ kind }); const setText = (text: string) => patch({ text }); const setFrom = (from: string) => patch({ from }); const setTo = (to: string) => patch({ to }); const setSupplier = (supplier: string) => patch({ supplier });
  const q = useDebounced(text.trim(), 350);
  const lists = usePerimeterLists(perimeter.planner);
  const supplierOptions = useMemo(() => (lists.data?.suppliers ?? []).map((s) => ({ id: s.id, label: s.name || s.id, hint: s.id })), [lists.data]);
  const res = useSearch({ kind, q: q || undefined, date_from: from || undefined, date_to: to || undefined, planner: perimeter.planner ?? undefined, supplier_id: supplier || undefined, link: link || undefined, order: kind === "receipts" && order ? order : undefined });

  const cols = useMemo<Column<SearchRow>[]>(() => {
    const base: Column<SearchRow>[] = [
      { key: "article", label: "Article", get: (r) => `${r.article_id} ${r.designation}`, exportGet: (r) => r.article_id, render: (r) => <ArticleLink id={r.article_id} designation={r.designation} /> },
      { key: "planner", label: "Approvisionneur", get: (r) => r.planner, filter: "select" },
      { key: "supplier", label: "Fournisseur", get: (r) => `${r.supplier_id ?? ""} ${r.supplier_name}`, title: "Code ou nom ; cliquer pour ouvrir la fiche fournisseur", render: (r) => <SupplierLink id={r.supplier_id} name={r.supplier_name} /> },
      { key: "linked", label: "Flux", get: (r) => (r.linked ? "actif" : "inactif"), filter: "select", title: "Actif : un lien article ↔ fournisseur actif existe dans le Référentiel ; inactif : ancien fournisseur ou lien à créer", render: (r) => <Badge tone={r.linked ? "ok" : "neutral"}>{r.linked ? "actif" : "inactif"}</Badge> },
      { key: "purch", label: "Commande", get: (r) => r.purch_id ?? "" },
    ];
    const date = (label: string): Column<SearchRow> => ({ key: "date", label, get: (r) => r.date ?? "", render: (r) => fmtDate(r.date) });
    const qty = (label: string, get: (r: SearchRow) => number | undefined): Column<SearchRow> => ({ key: label, label, get: (r) => get(r) ?? null, num: true, sum: true, unit: (r) => r.unit, render: (r) => fmtQty(get(r) ?? 0, r.unit) });
    switch (kind) {
      case "receipts": return [...base,
        { key: "order", label: "Type", get: (r) => (r.order_kind === "co" ? "CO" : r.order_kind === "po" ? (r.counted ? "PO déclarée" : "PO non déclarée") : "autre"), filter: "select", title: "CO : commande ouverte, comptée ; PO déclarée : commande fermée déclarée dans le Référentiel, comptée ; sinon la réception ne compte pas dans le stock",
          render: (r) => <span className="row" style={{ gap: 6 }}><Badge tone={r.counted ? "ok" : "neutral"}>{r.order_kind === "co" ? "CO" : r.order_kind === "po" ? (r.counted ? "PO déclarée" : "PO non déclarée") : ORDER_KIND_LABELS[r.order_kind ?? "other"]}</Badge>{canDeclare && r.order_kind === "po" && !r.counted && <Button size="xs" title={`Déclarer la commande fermée ${r.purch_id} : ses réceptions compteront dans la ligne Reçu et le stock`} onClick={(e) => { e.stopPropagation(); declare.mutate(r); }}><FilePlus2 />Déclarer la PO</Button>}</span> },
        { key: "bl", label: "BL", get: (r) => r.packing_slip ?? "", render: (r) => r.packing_slip || <span className="subtle">sans BL</span> },
        date("Reçu le"), qty("Quantité", (r) => r.qty),
        { key: "status", label: "Statut", get: (r) => r.status ?? "", filter: "select", render: (r) => <Badge tone={(r.status ?? "").toLowerCase().startsWith("comptabilis") ? "ok" : (r.status ?? "").toLowerCase().includes("transit") ? "info" : "warning"}>{r.status || "Reçu"}</Badge> }];
      case "orders": return [...base,
        { key: "type", label: "Type", get: (r) => ORDER_TYPE_LABELS[r.order_type ?? ""] ?? r.order_type ?? "", filter: "select" },
        date("Livraison prévue"), qty("Commandé", (r) => r.qty_ordered), qty("Restant", (r) => r.qty_open),
        { key: "seen", label: "Apparue le", get: (r) => r.first_seen ?? "", render: (r) => r.first_seen ? fmtDate(r.first_seen) : <span className="subtle">–</span> }];
      case "desadv": return [...base,
        { key: "bl", label: "BL", get: (r) => r.packing_slip ?? "", render: (r) => r.packing_slip || <span className="subtle">sans BL</span> },
        date("Émis le"), qty("Annoncé", (r) => r.qty),
        { key: "state", label: "État", get: (r) => `${r.state ?? ""}${r.final_processing ? ` / ${r.final_processing}` : ""}`, filter: "select" },
        { key: "received", label: "Reçu ?", get: (r) => r.received ? "reçu" : "en transit", filter: "select", render: (r) => <Badge tone={r.received ? "ok" : "info"}>{r.received ? "reçu" : "en transit"}</Badge> },
        { key: "journal", label: "Journal", get: (r) => r.journal ? "oui" : "non", filter: "select", render: (r) => r.journal ? "oui" : <Badge tone="warning">absent</Badge> }];
    }
  }, [kind, canDeclare, declare]);
  const rowKey = (r: SearchRow) => r.receipt_id ?? r.order_id ?? r.desadv_id ?? `${r.article_id}-${r.date}`;

  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Recherche</h1><p>Réceptions, commandes et DESADV du périmètre. Texte libre sur tous les champs ; « ; » sépare des alternatives (<code>123;456</code>). Les filtres de colonnes acceptent aussi « ; ».</p></div>
        <div className="actions"><Segmented value={kind} onChange={setKind} options={KINDS} /></div>
      </div>
      <Card tight>
        <div className="row wrap">
          <div className="search" style={{ minWidth: 320, flex: 1 }}><Search /><input className="input" placeholder="Article, désignation, fournisseur, commande, BL… (« ; » = ou)" value={text} onChange={(e) => setText(e.target.value)} aria-label="Recherche" /></div>
          <label className="row small subtle">du <input type="date" className="input sm" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="Date de début" /></label>
          <label className="row small subtle">au <input type="date" className="input sm" value={to} onChange={(e) => setTo(e.target.value)} aria-label="Date de fin" /></label>
          <SearchSelect value={supplier} onChange={setSupplier} options={supplierOptions} placeholder="Tous les fournisseurs" ariaLabel="Fournisseur" loading={lists.isLoading} />
          <Segmented<LinkFilter> size="sm" value={link} onChange={(v) => patch({ link: v })} options={[{ id: "", label: "Tous les flux" }, { id: "active", label: "Flux actif" }, { id: "inactive", label: "Flux inactif" }]} />
          {kind === "receipts" && <Segmented<OrderFilter> size="sm" value={order} onChange={(v) => patch({ order: v })} options={[{ id: "", label: "Toutes commandes" }, { id: "co", label: "CO" }, { id: "po", label: "PO déclarée" }, { id: "other", label: "Autres" }]} />}
          <span className="subtle small" style={{ marginLeft: "auto" }}>{res.data ? `${fmtInt(res.data.total)} ligne(s)${res.data.truncated ? ` · ${res.data.rows.length} affichées, affiner la recherche` : ""}` : res.isFetching ? "recherche…" : ""}</span>
        </div>
      </Card>
      <Card flush>
        {res.isError ? <ErrorBox error={res.error} retry={() => res.refetch()} /> : res.isLoading || !res.data ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div>
          : <div className="scroll-x"><DataTable rows={res.data.rows} columns={cols} rowKey={rowKey} emptyTitle="Aucune ligne" emptyHint="Élargir la période ou le texte recherché." maxHeight="calc(100vh - 260px)" storageKey={`recherche.${kind}`} exportTitle={`Recherche - ${KINDS.find((x) => x.id === kind)?.label ?? kind}`} /></div>}
      </Card>
    </div>
  );
}
