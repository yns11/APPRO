import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Search } from "lucide-react";
import { usePerimeterLists, useSearch } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { Badge, Card, ErrorBox, Segmented, SkeletonBlock } from "@/components/ui";
import { DataTable, type Column } from "@/components/DataTable";
import { SearchSelect } from "@/components/SearchSelect";
import { fmtDate, fmtInt, fmtQty, ORDER_TYPE_LABELS } from "@/lib/format";
import type { SearchKind, SearchRow } from "@/lib/types";

const KINDS: { id: SearchKind; label: string }[] = [
  { id: "receipts", label: "Réceptions" }, { id: "orders", label: "Commandes" }, { id: "desadv", label: "DESADV" }, { id: "pending", label: "BL en attente" },
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
export default function SearchPage() {
  const { perimeter } = usePerimeter();
  const [kind, setKind] = useState<SearchKind>("receipts");
  const [text, setText] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [supplier, setSupplier] = useState("");
  const q = useDebounced(text.trim(), 350);
  const lists = usePerimeterLists(perimeter.planner);
  const supplierOptions = useMemo(() => (lists.data?.suppliers ?? []).map((s) => ({ id: s.id, label: s.name || s.id, hint: s.id })), [lists.data]);
  const res = useSearch({ kind, q: q || undefined, date_from: from || undefined, date_to: to || undefined, planner: perimeter.planner ?? undefined, supplier_id: supplier || undefined });

  const cols = useMemo<Column<SearchRow>[]>(() => {
    const base: Column<SearchRow>[] = [
      { key: "article", label: "Article", get: (r) => `${r.article_id} ${r.designation}`, render: (r) => <><Link to={`/articles/${encodeURIComponent(r.article_id)}`}><b>{r.article_id}</b></Link><span className="sub">{r.designation}</span></> },
      { key: "planner", label: "Approvisionneur", get: (r) => r.planner, filter: "select" },
      { key: "supplier", label: "Fournisseur", get: (r) => `${r.supplier_id ?? ""} ${r.supplier_name}`, render: (r) => <>{r.supplier_id}<span className="sub">{r.supplier_name}</span></> },
      { key: "purch", label: "Commande", get: (r) => r.purch_id ?? "" },
    ];
    const date = (label: string): Column<SearchRow> => ({ key: "date", label, get: (r) => r.date ?? "", render: (r) => fmtDate(r.date) });
    const qty = (label: string, get: (r: SearchRow) => number | undefined): Column<SearchRow> => ({ key: label, label, get: (r) => get(r) ?? null, num: true, render: (r) => fmtQty(get(r) ?? 0, r.unit) });
    switch (kind) {
      case "receipts": return [...base,
        { key: "bl", label: "BL", get: (r) => r.packing_slip ?? "", render: (r) => r.packing_slip || <span className="subtle">ACR non validé</span> },
        date("Reçu le"), qty("Quantité", (r) => r.qty),
        { key: "status", label: "Statut", get: (r) => r.status ?? "", filter: "select", render: (r) => <Badge tone={(r.status ?? "").toLowerCase().startsWith("enregistr") ? "warning" : "ok"}>{r.status || "Reçu"}</Badge> }];
      case "orders": return [...base,
        { key: "type", label: "Type", get: (r) => ORDER_TYPE_LABELS[r.order_type ?? ""] ?? r.order_type ?? "", filter: "select" },
        date("Livraison prévue"), qty("Commandé", (r) => r.qty_ordered), qty("Restant", (r) => r.qty_open),
        { key: "seen", label: "Apparue le", get: (r) => r.first_seen ?? "", render: (r) => r.first_seen ? fmtDate(r.first_seen) : <span className="subtle">–</span> }];
      case "desadv": return [...base,
        { key: "bl", label: "BL", get: (r) => r.packing_slip ?? "", render: (r) => r.packing_slip || <span className="subtle">ACR non validé</span> },
        date("Émis le"), qty("Annoncé", (r) => r.qty),
        { key: "state", label: "État", get: (r) => `${r.state ?? ""}${r.final_processing ? ` / ${r.final_processing}` : ""}`, filter: "select" },
        { key: "received", label: "Reçu ?", get: (r) => r.received ? "reçu" : "en transit", filter: "select", render: (r) => <Badge tone={r.received ? "ok" : "info"}>{r.received ? "reçu" : "en transit"}</Badge> },
        { key: "journal", label: "Journal", get: (r) => r.journal ? "oui" : "non", filter: "select", render: (r) => r.journal ? "oui" : <Badge tone="warning">absent</Badge> }];
      case "pending": return [...base,
        { key: "bl", label: "BL (DESADV)", get: (r) => r.packing_slip ?? "" },
        date("Enregistré le"), qty("Quantité", (r) => r.qty),
        { key: "days", label: "Jours en attente", get: (r) => r.days_pending ?? 0, num: true, render: (r) => <Badge tone={(r.days_pending ?? 0) >= 14 ? "critical" : "warning"}>{fmtInt(r.days_pending)} j</Badge> }];
    }
  }, [kind]);
  const rowKey = (r: SearchRow) => r.receipt_id ?? r.order_id ?? r.desadv_id ?? r.pending_id ?? `${r.article_id}-${r.date}`;

  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Recherche</h1><p>Réceptions, commandes, DESADV et BL en attente du périmètre. Texte libre sur tous les champs ; « ; » sépare des alternatives (<code>123;456</code>). Les filtres de colonnes acceptent aussi « ; ».</p></div>
        <div className="actions"><Segmented value={kind} onChange={setKind} options={KINDS} /></div>
      </div>
      <Card tight>
        <div className="row wrap">
          <div className="search" style={{ minWidth: 320, flex: 1 }}><Search /><input className="input" placeholder="Article, désignation, fournisseur, commande, BL… (« ; » = ou)" value={text} onChange={(e) => setText(e.target.value)} aria-label="Recherche" /></div>
          <label className="row small subtle">du <input type="date" className="input sm" value={from} onChange={(e) => setFrom(e.target.value)} aria-label="Date de début" /></label>
          <label className="row small subtle">au <input type="date" className="input sm" value={to} onChange={(e) => setTo(e.target.value)} aria-label="Date de fin" /></label>
          <SearchSelect value={supplier} onChange={setSupplier} options={supplierOptions} placeholder="Tous les fournisseurs" ariaLabel="Fournisseur" loading={lists.isLoading} />
          <span className="subtle small" style={{ marginLeft: "auto" }}>{res.data ? `${fmtInt(res.data.total)} ligne(s)${res.data.truncated ? ` · ${res.data.rows.length} affichées, affiner la recherche` : ""}` : res.isFetching ? "recherche…" : ""}</span>
        </div>
      </Card>
      <Card flush>
        {res.isError ? <ErrorBox error={res.error} retry={() => res.refetch()} /> : res.isLoading || !res.data ? <div style={{ padding: 20 }}><SkeletonBlock rows={8} /></div>
          : <div className="scroll-x"><DataTable rows={res.data.rows} columns={cols} rowKey={rowKey} emptyTitle="Aucune ligne" emptyHint="Élargir la période ou le texte recherché." maxHeight="calc(100vh - 260px)" /></div>}
      </Card>
    </div>
  );
}
