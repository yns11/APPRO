import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link, NavLink, useParams } from "react-router-dom";
import { ArrowLeft, ArrowRight, Pencil, RotateCcw, Save, X } from "lucide-react";
import { api } from "@/lib/api";
import { useHelpPage, useHelpPages } from "@/lib/queries";
import { usePerimeter } from "@/state/PerimeterContext";
import { Badge, Button, Empty, ErrorBox, SkeletonBlock, useToast } from "@/components/ui";
import { fmtDateTime } from "@/lib/format";
import type { HelpPage as HelpPageOut, HelpPageSummary } from "@/lib/types";
import "@/styles/help.css";

const PART_TITLES: Record<string, string> = { A: "Tableau d'approvisionnement", B: "Tableau de bord" };

/**
 * The in-app pense-bête : one page per section (left navigation grouped by part), the text served by the
 * server (the version an administrator saved, else the one shipped with the application).  Administrators
 * get an edit mode : the page body becomes editable in place, « Enregistrer » stores the HTML (sanitised by
 * the server), « Version d'origine » drops the saved text.  Every user can read the pages ; readers never
 * see an edit button nor the history.
 */
export default function HelpPage() {
  const { slug } = useParams();
  const { rights } = usePerimeter();
  const pages = useHelpPages();
  const list = pages.data ?? [];
  const current = slug ?? list[0]?.slug;
  const groups = useMemo(() => {
    const out: { part: string; title: string; pages: HelpPageSummary[] }[] = [];
    list.forEach((p) => { const g = out[out.length - 1]; if (g && g.part === p.part) g.pages.push(p); else out.push({ part: p.part, title: p.part ? `Partie ${p.part} · ${p.part_title || PART_TITLES[p.part] || ""}` : "", pages: [p] }); });
    return out;
  }, [list]);
  const idx = list.findIndex((p) => p.slug === current);
  const prev = idx > 0 ? list[idx - 1] : undefined, next = idx >= 0 && idx < list.length - 1 ? list[idx + 1] : undefined;
  return (
    <div className="page">
      <div className="page-header">
        <div className="title"><h1>Pense-bête de l'approvisionneur</h1><p>Ce que l'application décide, affiche et colore : une page par section.{rights.isAdmin ? " Les administrateurs peuvent corriger le texte sur place ; tous les lecteurs voient la dernière version enregistrée." : ""}</p></div>
      </div>
      {pages.isError ? <ErrorBox error={pages.error} retry={() => pages.refetch()} /> : pages.isLoading ? <SkeletonBlock rows={8} /> : !list.length ? <Empty title="Aucune page d'aide" /> : (
        <div className="help-layout">
          <nav className="help-nav" aria-label="Sections du pense-bête">
            {groups.map((g, gi) => (
              <div key={g.part || `g${gi}`}>
                {g.title && <div className="part">{g.title}</div>}
                {g.pages.map((p) => <NavLink key={p.slug} to={`/aide/${p.slug}`} className={({ isActive }) => (isActive || (!slug && p.slug === current) ? "active" : "")}>{p.title}{rights.isAdmin && p.edited && <span className="edited" title="Texte modifié par un administrateur">· modifié</span>}</NavLink>)}
              </div>
            ))}
          </nav>
          {current && <HelpPageBody slug={current} canEdit={rights.isAdmin} prev={prev} next={next} />}
        </div>
      )}
    </div>
  );
}

function HelpPageBody({ slug, canEdit, prev, next }: { slug: string; canEdit: boolean; prev?: HelpPageSummary; next?: HelpPageSummary }) {
  const q = useHelpPage(slug);
  const qc = useQueryClient();
  const toast = useToast();
  const [editing, setEditing] = useState(false);
  const [title, setTitle] = useState("");
  const [busy, setBusy] = useState(false);
  const body = useRef<HTMLDivElement>(null);
  const p = q.data;
  useEffect(() => { setEditing(false); }, [slug]);
  // the body is injected once per loaded version : React never re-renders it while the administrator types
  useEffect(() => { if (body.current && p && !editing) body.current.innerHTML = p.html; }, [p, editing]);
  const start = () => { if (!p) return; setTitle(p.title); setEditing(true); setTimeout(() => body.current?.focus(), 0); };
  const cancel = () => { setEditing(false); if (body.current && p) body.current.innerHTML = p.html; };
  const done = (out: HelpPageOut, msg: string) => {
    qc.setQueryData(["help-page", slug], out);
    qc.invalidateQueries({ queryKey: ["help-pages"] });
    qc.invalidateQueries({ queryKey: ["actions"] });
    setEditing(false);
    toast.push(msg, "success");
  };
  const save = async () => {
    if (!body.current) return;
    setBusy(true);
    try { done(await api.put<HelpPageOut>(`/api/help/pages/${encodeURIComponent(slug)}`, { html: body.current.innerHTML, title: title.trim() || p?.title }), "Page enregistrée : tous les lecteurs voient ce texte"); }
    catch (e) { toast.push(`Enregistrement impossible : ${(e as Error).message}`, "error"); }
    finally { setBusy(false); }
  };
  const reset = async () => {
    if (!window.confirm("Revenir au texte livré avec l'application ? Le texte enregistré est perdu.")) return;
    setBusy(true);
    try { done(await api.del<HelpPageOut>(`/api/help/pages/${encodeURIComponent(slug)}`), "Texte d'origine rétabli"); }
    catch (e) { toast.push(`Rétablissement impossible : ${(e as Error).message}`, "error"); }
    finally { setBusy(false); }
  };
  if (q.isError) return <div className="help-page"><ErrorBox error={q.error} retry={() => q.refetch()} /></div>;
  return (
    <article className="help-page">
      <div className="help-head">
        <h2>
          {p?.part && <span className="eyebrow">Partie {p.part} · {p.part_title || PART_TITLES[p.part] || ""}</span>}
          {editing ? <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} aria-label="Titre de la page" style={{ minWidth: 320, fontSize: "inherit", fontWeight: "inherit" }} /> : (p?.title ?? <span className="subtle">…</span>)}
        </h2>
        {canEdit && p && (
          <div className="help-edit-tools no-print">
            {editing ? <>
              <Button size="sm" variant="primary" disabled={busy} onClick={save}><Save />Enregistrer</Button>
              <Button size="sm" disabled={busy} onClick={cancel}><X />Annuler</Button>
            </> : <>
              <Button size="sm" onClick={start} title="Modifier le texte de cette page sur place (réservé aux administrateurs)"><Pencil />Modifier</Button>
              {p.edited && <Button size="sm" variant="ghost" disabled={busy} onClick={reset} title="Abandonner le texte enregistré et revenir à la version livrée avec l'application"><RotateCcw />Version d'origine</Button>}
            </>}
          </div>
        )}
      </div>
      {editing && <div className="note small" style={{ marginBottom: 12 }}>Mode édition : cliquer dans le texte et corriger directement (gras, listes et tableaux conservés). « Enregistrer » publie la page pour tous les lecteurs ; les scripts et liens actifs sont retirés à l'enregistrement.</div>}
      {q.isLoading || !p ? <SkeletonBlock rows={10} /> : <div ref={body} className="help-doc" contentEditable={editing} suppressContentEditableWarning spellCheck={editing} aria-label={p.title} />}
      <div className="help-foot">
        <span>{prev ? <Link to={`/aide/${prev.slug}`} className="btn sm ghost"><ArrowLeft />{prev.title}</Link> : <span />}</span>
        <span className="subtle small" style={{ alignSelf: "center" }}>{canEdit && p?.edited ? <>Texte modifié par {p.updated_by}{p.updated_at ? ` le ${fmtDateTime(p.updated_at)}` : ""} <Badge tone="brand">enregistré</Badge></> : null}</span>
        <span>{next ? <Link to={`/aide/${next.slug}`} className="btn sm ghost">{next.title}<ArrowRight /></Link> : <span />}</span>
      </div>
    </article>
  );
}
