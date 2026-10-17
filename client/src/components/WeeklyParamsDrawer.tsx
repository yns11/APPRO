import { useMemo } from "react";
import { RotateCcw } from "lucide-react";
import { api } from "@/lib/api";
import { useWeeklyParams, useWrite } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { Button, Drawer, Empty, ErrorBox, SkeletonBlock, useToast } from "@/components/ui";
import { SheetGrid, type SheetCell, type SheetChange } from "@/components/SheetGrid";
import { fmtDate } from "@/lib/format";
import type { WeeklyParamsResponse } from "@/lib/types";

const LABELS: Record<string, string> = {
  coverage_target_days: "Couverture cible (j)", alert_red_days: "Seuil rouge (j)", alert_yellow_days: "Seuil orange (j)",
  overstock_days: "Surstock (j)", safety_stock_qty: "Stock sécurité", order_cycle_days: "Cycle cde (j)",
};

type Item = { key2: string; field: string; value: number | "" };

/**
 * Weekly calendar of the stock-policy parameters of one article, edited like a sheet (see
 * ``SheetGrid``) ; an empty value / Delete restores the article value.  Every batch is saved in one
 * transaction.  Weeks run from the reference week to the last week of the loaded PDP.
 */
export function WeeklyParamsDrawer({ articleId, planner, onClose }: { articleId: string | null; planner?: string | null; onClose: () => void }) {
  const toast = useToast();
  const { rights } = usePerimeter();
  const q = useWeeklyParams(articleId);
  const d = q.data;
  const canEdit = !!articleId && (rights.canManageParams || rights.canEditPlanner(planner));
  const save = useWrite((items: Item[]) => api.put("/api/params/overrides/batch", { scope: "article_week", key1: articleId, items }),
    () => toast.push("Valeurs hebdomadaires enregistrées", "success"));
  const reset = useWrite(() => api.del(`/api/params/overrides?scope=article_week&key1=${encodeURIComponent(articleId ?? "")}`),
    () => toast.push("Valeurs de l'article rétablies pour toutes les semaines"));

  const fields = d?.fields ?? [];
  const rows = d?.weeks ?? [];
  const cells = useMemo<SheetCell[][]>(() => rows.map((w) => fields.map((f) => {
    const over = w.overridden.includes(f);
    return { value: w.values[f], over, title: over ? "Valeur propre à cette semaine (Suppr = valeur de l'article)" : "Valeur de l'article – saisir crée une valeur pour cette semaine" };
  })), [rows, fields]);
  const commit = (changes: SheetChange[]) => {
    const items = changes.map((x) => ({ key2: rows[x.r].week, field: fields[x.c], value: x.value }))
      .filter((it) => { const w = rows.find((x) => x.week === it.key2); return !!w && (it.value === "" ? w.overridden.includes(it.field) : w.values[it.field] !== it.value); });
    if (items.length) save.mutate(items);
  };

  const overridden = rows.reduce((n, w) => n + w.overridden.length, 0);
  return (
    <Drawer open={!!articleId} onClose={onClose} wide title={<>Paramètres par semaine · {articleId}</>}
      footer={<>
        <span className="small subtle grow">Cellules préremplies avec les valeurs de l'article (Référentiel) ; une valeur modifiée ne vaut que pour sa semaine (bordure bleue). Saisir, <kbd>Entrée</kbd>, flèches, glisser pour sélectionner, <kbd>Ctrl</kbd>+<kbd>C</kbd> / <kbd>Ctrl</kbd>+<kbd>V</kbd> (bloc Excel), tirer le carré pour recopier vers le bas, <kbd>Suppr</kbd> = valeur de l'article.</span>
        {canEdit && <Button disabled={!overridden || reset.isPending} title="Supprimer toutes les valeurs par semaine de cet article : retour aux valeurs uniques du Référentiel › Articles" onClick={() => { if (window.confirm(`Rétablir les valeurs de l'article pour les ${overridden} cellule(s) personnalisée(s) ?`)) reset.mutate(undefined); }}><RotateCcw />Tout rétablir{overridden ? ` (${overridden})` : ""}</Button>}
        <Button onClick={onClose}>Fermer</Button>
      </>}>
      {!canEdit && articleId && d && <div className="note" style={{ marginBottom: 8 }}>Lecture seule : cet article est hors de votre carnet.</div>}
      {q.isError ? <ErrorBox error={q.error} /> : q.isLoading || !d ? <SkeletonBlock rows={10} /> : rows.length === 0 ? <Empty title="Aucune semaine" /> : (
        <SheetGrid ariaLabel="Paramètres par semaine" canEdit={canEdit} cells={cells} onCommit={commit} corner="Semaine" fillAxis="col"
          colHeaders={fields.map((f) => <>{LABELS[f] ?? f}<span className="sub">article : {d.defaults[f]}</span></>)}
          rowHeaders={rows.map((w) => <><b>{w.week.replace("-W", " S")}</b> <span className="subtle">{fmtDate(w.week_start)}</span></>)} />
      )}
      <WeekCount d={d} />
    </Drawer>
  );
}

function WeekCount({ d }: { d: WeeklyParamsResponse | undefined }) {
  if (!d?.weeks.length) return null;
  const last = d.weeks[d.weeks.length - 1];
  return <p className="small subtle" style={{ marginTop: 8 }}>{d.weeks.length} semaines, de {d.weeks[0].week.replace("-W", " S")} à {last.week.replace("-W", " S")} (dernière semaine du PDP chargé ou fin d'horizon).</p>;
}
