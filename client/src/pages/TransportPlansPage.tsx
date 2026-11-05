import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowLeft, Mail } from "lucide-react";
import { api } from "@/lib/api";
import { usePerimeter } from "@/state/PerimeterContext";
import { Badge, Button, Card, Empty, ErrorBox, SkeletonBlock } from "@/components/ui";
import { usePlanSender } from "@/components/transports/PlanSender";
import { weekShort } from "@/lib/transports";
import { WEEKDAY_LONG, fmtDate } from "@/lib/format";
import type { PlanOut, PlansOut } from "@/lib/types";

const STATE: Record<PlanOut["state"], { label: string; tone: "brand" | "ok" | "warning" }> = { a_envoyer: { label: "à envoyer", tone: "brand" }, envoyee: { label: "envoyée", tone: "ok" }, a_renvoyer: { label: "✱ à renvoyer", tone: "warning" } };

/** « Envois » : the weekly plans by recipient, the current week (re-sendings only) and the next (to send). */
export default function TransportPlansPage() {
  const { perimeter, config } = usePerimeter();
  const q = useQuery({ queryKey: ["transports-plans", perimeter.planner], enabled: !!config, queryFn: () => api.get<PlansOut>("/api/transports/plans", { planner: perimeter.planner ?? undefined }) });
  const sender = usePlanSender();
  const d = q.data;
  if (config && !config.access?.can_transports) return <div className="page"><Empty title="Planning transport : accès réservé" /></div>;
  if (q.isError) return <ErrorBox error={q.error} retry={() => q.refetch()} />;
  const first = d ? d.plans.find((p) => p.state === "a_renvoyer") ?? d.plans.find((p) => p.week === d.next_week && p.state === "a_envoyer") : undefined;
  const dayName = d ? WEEKDAY_LONG[(d.plan_weekday + 6) % 7] : "jeudi";
  const group = (week: string, note: string) => {
    const rows = (d?.plans ?? []).filter((p) => p.week === week);
    return (
      <>
        <tr className="tp-grp"><td colSpan={6}>{weekShort(week)} <span>· {note}</span></td></tr>
        {rows.length === 0 && <tr><td colSpan={6} className="subtle small">aucun camion</td></tr>}
        {rows.map((p) => (
          <tr key={`${p.week}/${p.recipient}`} className={p.state === "a_renvoyer" ? "warn" : ""}>
            <td><b>{p.name}</b><div className="small subtle">{p.emails.length ? (p.recipient.startsWith("carrier:") ? p.supplier_ids.join(" · ") : p.emails.join(" · ")) : <span style={{ color: "var(--critical-fg)" }}>aucune adresse e-mail</span>}</div></td>
            <td className="num">{p.trucks}</td>
            <td><Badge tone={STATE[p.state].tone}>{STATE[p.state].label}</Badge></td>
            <td>{p.sent_no ? `${p.sent_at ? fmtDate(p.sent_at) : ""} · n° ${p.sent_no}` : <span className="subtle">—</span>}</td>
            <td className="small subtle">{p.reasons.length ? p.reasons.join(" · ") : "—"}</td>
            <td style={{ textAlign: "right" }}>
              {p.state === "a_renvoyer" ? <Button size="sm" className="warn" onClick={() => sender.send(p)}><Mail />Renvoyer</Button>
                : p.state === "a_envoyer" ? <Button size="sm" variant="primary" onClick={() => sender.send(p)}><Mail />Envoyer</Button>
                : <Button size="sm" variant="ghost" onClick={() => sender.reopen(p)} title="Rouvrir le plan dans Outlook sans noter d'envoi">Rouvrir</Button>}
            </td>
          </tr>
        ))}
      </>
    );
  };
  return (
    <div className="page tp-page">
      <div className="tp-head">
        <div>
          <Link to="/livraisons" className="btn ghost sm"><ArrowLeft />Planning transport</Link>
          <h1 style={{ margin: "6px 0 0" }}>Plans de semaine</h1>
        </div>
        {first && <Button variant="primary" onClick={() => sender.send(first)}><Mail />{first.state === "a_renvoyer" ? "Renvoyer" : "Envoyer"} · {first.name}</Button>}
      </div>
      <Card flush>
        {!d ? <div style={{ padding: 20 }}><SkeletonBlock rows={6} /></div> : (
          <table className="tp-plans" aria-label="Envois par destinataire et semaine">
            <thead><tr><th style={{ width: "30%" }}>Destinataire</th><th className="num">Camions</th><th>État</th><th>Dernier envoi</th><th>Changé depuis</th><th /></tr></thead>
            <tbody>
              {group(d.current_week, "en cours · rectificatifs seulement")}
              {group(d.next_week, `semaine suivante · à envoyer ${dayName}`)}
            </tbody>
          </table>
        )}
      </Card>
      {sender.drawer}
    </div>
  );
}
