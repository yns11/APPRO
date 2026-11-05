import { useState, type ReactNode } from "react";
import { Check, Mail } from "lucide-react";
import { api } from "@/lib/api";
import { useInvalidateAll } from "@/lib/queries";
import { Button, Drawer, useToast } from "@/components/ui";
import { mailtoOf } from "@/lib/transports";
import type { PlanMailOut, PlanMailPart, PlanOut } from "@/lib/types";

/**
 * Sending a weekly plan : one click opens the complete plan in Outlook and marks the week sent ; when the plan is too
 * long for one ``mailto:``, a drawer lists one message per supplier and the planner marks the week sent afterwards.
 * ``reopen`` opens the e-mail again without marking anything.
 */
export function usePlanSender(): { send: (p: PlanOut) => Promise<void>; reopen: (p: PlanOut) => Promise<void>; drawer: ReactNode } {
  const toast = useToast();
  const invalidate = useInvalidateAll();
  const [parts, setParts] = useState<{ plan: PlanOut; parts: PlanMailPart[] } | null>(null);
  const mailUrl = (p: PlanOut) => `/api/transports/plans/${encodeURIComponent(p.week)}/${encodeURIComponent(p.recipient)}`;
  const markSent = async (p: PlanOut) => {
    const sent = await api.post<PlanOut>(`${mailUrl(p)}/sent`);
    invalidate(); setParts(null);
    toast.push(`Plan ${sent.label} · ${sent.name} : envoi n° ${sent.sent_no} noté`, "success");
  };
  const send = async (p: PlanOut) => {
    try {
      const out = await api.get<PlanMailOut>(`${mailUrl(p)}/mail`);
      if (out.split) { setParts({ plan: p, parts: out.parts }); return; }
      if (!out.mail.to.length) toast.push(`${p.name} : aucun destinataire, complétez le contact transport dans le Référentiel`, "info");
      window.location.href = mailtoOf(out.mail);
      await markSent(p);
    } catch (e) { toast.push((e as Error).message, "error"); }
  };
  const reopen = async (p: PlanOut) => {
    try {
      const out = await api.get<PlanMailOut>(`${mailUrl(p)}/mail`);
      window.location.href = mailtoOf(out.split ? out.parts[0] : out.mail);
    } catch (e) { toast.push((e as Error).message, "error"); }
  };
  const drawer = parts ? <MailPartsDrawer plan={parts.plan} parts={parts.parts} onClose={() => setParts(null)} onSent={() => markSent(parts.plan).catch((e) => toast.push((e as Error).message, "error"))} /> : null;
  return { send, reopen, drawer };
}

function MailPartsDrawer({ plan, parts, onClose, onSent }: { plan: PlanOut; parts: PlanMailPart[]; onClose: () => void; onSent: () => void }) {
  const [opened, setOpened] = useState<Set<number>>(new Set());
  return (
    <Drawer open onClose={onClose} title={<>Plan {plan.label} · {plan.name} · {parts.length} e-mails</>}
      footer={<><Button onClick={onClose}>Fermer</Button><Button variant="primary" disabled={opened.size < parts.length} onClick={onSent} title={opened.size < parts.length ? "Ouvrez chaque e-mail d'abord" : "Noter l'envoi"}><Check />Marquer la semaine envoyée</Button></>}>
      <p className="small subtle">Le plan complet dépasse la taille qu'un lien Outlook accepte : il part en un e-mail par fournisseur, tous au même destinataire. Ouvrez-les un à un, envoyez-les, puis marquez la semaine envoyée.</p>
      <table className="tbl compact">
        <thead><tr><th>#</th><th>Objet</th><th /></tr></thead>
        <tbody>{parts.map((p, i) => <tr key={i}><td>{i + 1}</td><td className="small">{p.subject}</td><td><Button size="xs" variant={opened.has(i) ? "default" : "primary"} onClick={() => { window.location.href = mailtoOf(p); setOpened((s) => new Set([...s, i])); }}><Mail />{opened.has(i) ? "Rouvrir" : "Ouvrir dans Outlook"}</Button></td></tr>)}</tbody>
      </table>
    </Drawer>
  );
}
