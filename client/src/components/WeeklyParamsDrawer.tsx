import { useMemo } from "react";
import { RotateCcw } from "lucide-react";
import { api } from "@/lib/api";
import { useOverrides, useWeeklyParams, useWrite } from "@/lib/queries";
import { Button, Drawer, Empty, ErrorBox, SkeletonBlock, useToast } from "@/components/ui";
import { fmtDate } from "@/lib/format";

const LABELS: Record<string, string> = {
  coverage_target_days: "Couverture cible (j)", alert_red_days: "Seuil rouge (j)", alert_yellow_days: "Seuil orange (j)",
  overstock_days: "Surstock (j)", safety_stock_qty: "Stock sécurité", order_cycle_days: "Cycle cde (j)",
};

/** Weekly calendar of the stock-policy parameters of one article (pre-filled with the article values). */
export function WeeklyParamsDrawer({ articleId, onClose }: { articleId: string | null; onClose: () => void }) {
  const toast = useToast();
  const q = useWeeklyParams(articleId);
  const overrides = useOverrides(articleId ? { scope: "article_week", key1: articleId } : undefined);
  const setParam = useWrite((b: { key2: string; field: string; value: number }) => api.put("/api/params/overrides", { scope: "article_week", key1: articleId, ...b }), () => toast.push("Paramètre hebdomadaire enregistré", "success"));
  const del = useWrite((id: string) => api.del(`/api/params/overrides/${id}`), () => toast.push("Valeur de l'article rétablie"));
  const ovId = useMemo(() => new Map((overrides.data ?? []).map((o) => [`${o.key2}|${o.field}`, o.id])), [overrides.data]);
  const d = q.data;
  return (
    <Drawer open={!!articleId} onClose={onClose} wide title={<>Paramètres par semaine · {articleId}</>}
      footer={<><span className="small subtle grow">Les cellules sont préremplies avec les valeurs de l'article (Référentiel) ; une valeur modifiée ne vaut que pour sa semaine (bordure bleue). Le bouton ↺ rétablit la valeur de l'article.</span><Button onClick={onClose}>Fermer</Button></>}>
      {q.isError ? <ErrorBox error={q.error} /> : q.isLoading || !d ? <SkeletonBlock rows={10} /> : d.weeks.length === 0 ? <Empty title="Aucune semaine" /> : (
        <table className="tbl compact">
          <thead><tr><th>Semaine</th>{d.fields.map((f) => <th key={f} className="num">{LABELS[f] ?? f}<span className="sub">article : {d.defaults[f]}</span></th>)}</tr></thead>
          <tbody>{d.weeks.map((w) => (
            <tr key={w.week}>
              <td>{w.week.replace("-W", " S")}<span className="sub">{fmtDate(w.week_start)}</span></td>
              {d.fields.map((f) => {
                const over = w.overridden.includes(f);
                const id = ovId.get(`${w.week}|${f}`);
                return <td key={f} className="num" style={{ whiteSpace: "nowrap" }}>
                  <input className="input sm" type="number" step="any" defaultValue={w.values[f]} key={`${w.week}-${f}-${w.values[f]}`} style={{ width: 84, textAlign: "right", borderColor: over ? "var(--brand)" : undefined }}
                    aria-label={`${LABELS[f] ?? f} ${w.week}`} title={over ? "Valeur propre à cette semaine" : "Valeur de l'article – modifier crée une valeur pour cette semaine"}
                    onBlur={(e) => { const v = Number(e.target.value); if (!Number.isNaN(v) && v !== w.values[f]) setParam.mutate({ key2: w.week, field: f, value: v }); }}
                    onKeyDown={(e) => { if (e.key === "Enter") (e.target as HTMLInputElement).blur(); }} />
                  {over && id && <Button size="xs" variant="ghost" title="Rétablir la valeur de l'article" onClick={() => del.mutate(id)}><RotateCcw /></Button>}
                </td>;
              })}
            </tr>
          ))}</tbody>
        </table>
      )}
    </Drawer>
  );
}
