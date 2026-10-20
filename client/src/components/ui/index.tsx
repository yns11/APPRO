import { cloneElement, createContext, isValidElement, useCallback, useContext, useEffect, useId, useMemo, useRef, useState, type ReactElement, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { AlertTriangle, ChevronDown, ChevronRight, Inbox, X } from "lucide-react";
import type { Severity } from "@/lib/types";
import { SEVERITY_LABELS } from "@/lib/format";

/* ---------------------------------------------------------------- Links to the records */
/** Supplier code (and name) as a link to the supplier page ; ``name`` goes under the code. */
export function SupplierLink({ id, name, inline, className }: { id: string | null | undefined; name?: string; inline?: boolean; className?: string }) {
  if (!id) return <span className="subtle">{name || "–"}</span>;
  const to = `/fournisseurs/${encodeURIComponent(id)}`;
  if (inline) return <Link to={to} className={className} onClick={(e) => e.stopPropagation()} title="Ouvrir la fiche fournisseur">{id}{name ? ` · ${name}` : ""}</Link>;
  return <><Link to={to} className={className} onClick={(e) => e.stopPropagation()} title="Ouvrir la fiche fournisseur"><b>{id}</b></Link>{name && <span className="sub"><Link to={to} className="subtle" onClick={(e) => e.stopPropagation()}>{name}</Link></span>}</>;
}
/** Article code (and designation) as a link to the article page. */
export function ArticleLink({ id, designation, className }: { id: string; designation?: string; className?: string }) {
  return <><Link to={`/articles/${encodeURIComponent(id)}`} className={className} onClick={(e) => e.stopPropagation()}><b>{id}</b></Link>{designation && <span className="sub" title={designation}>{designation}</span>}</>;
}

/* ---------------------------------------------------------------- Button */
export function Button({ variant = "default", size, icon, className = "", children, ...rest }:
  React.ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "default" | "primary" | "danger" | "ghost"; size?: "sm" | "xs"; icon?: boolean }) {
  const cls = ["btn", variant !== "default" ? variant : "", size ?? "", icon ? "icon" : "", className].filter(Boolean).join(" ");
  return <button className={cls} {...rest}>{children}</button>;
}

/* ---------------------------------------------------------------- Card */
export function Card({ title, hint, actions, children, className = "", tight, flush, style, collapsible, storageKey }:
  { title?: ReactNode; hint?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string; tight?: boolean; flush?: boolean; style?: React.CSSProperties;
    /** a chevron folds the body ; the state is remembered per ``storageKey`` */
    collapsible?: boolean; storageKey?: string }) {
  const [open, setOpen] = useState(() => { if (!collapsible || !storageKey) return true; try { return localStorage.getItem(`appro.card.${storageKey}`) !== "closed"; } catch { return true; } });
  const toggle = () => { const next = !open; setOpen(next); if (storageKey) { try { localStorage.setItem(`appro.card.${storageKey}`, next ? "open" : "closed"); } catch { /* private mode */ } } };
  return (
    <section className={["card", tight ? "tight" : "", flush ? "flush" : "", collapsible && !open ? "collapsed" : "", className].filter(Boolean).join(" ")} style={style}>
      {(title || actions || collapsible) && (
        <div className="card-header" style={flush ? { padding: "var(--sp-4) var(--sp-5) 0" } : undefined}>
          <div className={collapsible ? "card-title-toggle" : undefined} onClick={collapsible ? toggle : undefined} role={collapsible ? "button" : undefined} aria-expanded={collapsible ? open : undefined}>
            {collapsible && (open ? <ChevronDown className="card-chevron" /> : <ChevronRight className="card-chevron" />)}
            {typeof title === "string" ? <h3>{title}</h3> : title}{hint && open && <div className="hint">{hint}</div>}
          </div>
          {actions && open && <div className="actions">{actions}</div>}
        </div>
      )}
      {(!collapsible || open) && children}
    </section>
  );
}

/* ---------------------------------------------------------------- KPI */
export function Kpi({ label, value, unit, meta, tone, onClick, active, icon }:
  { label: string; value: ReactNode; unit?: string; meta?: ReactNode; tone?: "critical" | "warning" | "ok" | "info" | "brand"; onClick?: () => void; active?: boolean; icon?: ReactNode }) {
  return (
    <div className={["kpi", tone ? `tone-${tone}` : "", onClick ? "clickable" : "", active ? "active" : ""].filter(Boolean).join(" ")}
      onClick={onClick} role={onClick ? "button" : undefined} tabIndex={onClick ? 0 : undefined}
      onKeyDown={onClick ? (e) => { if (e.key === "Enter" || e.key === " ") onClick(); } : undefined}>
      <div className="label">{icon}{label}</div>
      <div className={`value ${typeof value === "string" && value.length > 9 ? "long" : ""}`}>{value}{unit && <small>{unit}</small>}</div>
      {meta && <div className="meta">{meta}</div>}
    </div>
  );
}

/* ---------------------------------------------------------------- Badge */
export function Badge({ tone = "neutral", children, title }: { tone?: Severity | "ok" | "neutral" | "brand" | "outline"; children: ReactNode; title?: string }) {
  return <span className={`badge ${tone}`} title={title}>{children}</span>;
}
export function SeverityBadge({ severity }: { severity: Severity | null | undefined }) {
  if (!severity) return <Badge tone="ok">OK</Badge>;
  if (severity === "info") return <Badge tone="neutral" title="Alertes informatives seulement">OK · info</Badge>;
  return <Badge tone={severity}><span className={`dot ${severity}`} />{SEVERITY_LABELS[severity]}</Badge>;
}

/* ---------------------------------------------------------------- States */
export function Skeleton({ h = 16, w = "100%", style }: { h?: number; w?: number | string; style?: React.CSSProperties }) {
  return <div className="skeleton" style={{ height: h, width: w, ...style }} />;
}
export function SkeletonBlock({ rows = 6 }: { rows?: number }) {
  return <div className="stack">{Array.from({ length: rows }).map((_, i) => <Skeleton key={i} h={14} w={`${70 + ((i * 13) % 30)}%`} />)}</div>;
}
export function Empty({ title, hint, action, icon }: { title: string; hint?: ReactNode; action?: ReactNode; icon?: ReactNode }) {
  return <div className="empty">{icon ?? <Inbox />}<h3>{title}</h3>{hint && <p>{hint}</p>}{action}</div>;
}
export function ErrorBox({ error, retry }: { error: unknown; retry?: () => void }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="error-box" role="alert">
      <AlertTriangle size={18} />
      <div className="grow"><b>Erreur de chargement.</b> {msg}</div>
      {retry && <Button size="sm" onClick={retry}>Réessayer</Button>}
    </div>
  );
}

/* ---------------------------------------------------------------- Tabs */
export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: string; count?: number }[]; value: T; onChange: (t: T) => void }) {
  return (
    <div className="tabs" role="tablist">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={t.id === value} className={t.id === value ? "active" : ""} onClick={() => onChange(t.id)}>
          {t.label}{t.count !== undefined && <span className="count">{t.count}</span>}
        </button>
      ))}
    </div>
  );
}

/* ---------------------------------------------------------------- Drawer */
export function Drawer({ open, onClose, title, children, footer, wide }: { open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; footer?: ReactNode; wide?: boolean }) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className={`drawer ${wide ? "wide" : ""}`} role="dialog" aria-modal="true">
        <header><h2 className="grow">{title}</h2><Button variant="ghost" icon aria-label="Fermer" onClick={onClose}><X /></Button></header>
        <div className="body">{children}</div>
        {footer && <footer>{footer}</footer>}
      </div>
    </div>
  );
}

/* ---------------------------------------------------------------- Field */
export function Field({ label, help, children, span2 }: { label: string; help?: ReactNode; children: ReactNode; span2?: boolean }) {
  // associate the label with the (single) control for accessibility / testing
  const generated = useId();
  let control: ReactNode = children;
  let id: string | undefined;
  if (isValidElement(children)) {
    const el = children as ReactElement<{ id?: string }>;
    id = el.props.id ?? generated;
    control = cloneElement(el, { id });
  }
  return <div className={`field ${span2 ? "span-2" : ""}`}><label htmlFor={id}>{label}</label>{control}{help && <div className="help">{help}</div>}</div>;
}

/* ---------------------------------------------------------------- Toast */
/** ``action`` : a button in the toast (« Annuler ») ; ``key`` : a toast replacing the previous one of the same key ;
 *  ``ms`` : how long it stays (a progress bar shows the countdown when there is an action). */
export interface ToastOpts { action?: { label: string; onClick: () => void }; key?: string; ms?: number }
interface Toast { id: number; text: string; tone: "info" | "error" | "success"; opts?: ToastOpts }
const ToastCtx = createContext<{ push: (text: string, tone?: Toast["tone"], opts?: ToastOpts) => void; dismiss: (key: string) => void } | null>(null);
export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const seq = useRef(0);
  const dismiss = useCallback((key: string) => setItems((l) => l.filter((t) => t.opts?.key !== key)), []);
  const lastAction = useRef(0);
  const push = useCallback((text: string, tone: Toast["tone"] = "info", opts?: ToastOpts) => {
    // a write that produced an undoable action already shows its own toast (label + « Annuler ») : the plain
    // confirmation the screen pushes right after would say the same thing twice
    if (opts?.action) lastAction.current = Date.now();
    else if (tone !== "error" && Date.now() - lastAction.current < 1500) return;
    const id = ++seq.current;
    setItems((l) => [...(opts?.key ? l.filter((t) => t.opts?.key !== opts.key) : l), { id, text, tone, opts }]);
    setTimeout(() => setItems((l) => l.filter((t) => t.id !== id)), opts?.ms ?? (tone === "error" ? 7000 : opts?.action ? 8000 : 3500));
  }, []);
  const value = useMemo(() => ({ push, dismiss }), [push, dismiss]);
  return (
    <ToastCtx.Provider value={value}>
      {children}
      <div className="toasts" aria-live="polite">{items.map((t) => (
        <div key={t.id} className={`toast ${t.tone} ${t.opts?.action ? "with-action" : ""}`}>
          <span className="toast-text">{t.text}</span>
          {t.opts?.action && <button type="button" className="toast-action" onClick={() => { t.opts!.action!.onClick(); setItems((l) => l.filter((x) => x.id !== t.id)); }}>{t.opts.action.label}</button>}
          {t.opts?.action && <span className="toast-progress" style={{ animationDuration: `${t.opts.ms ?? 8000}ms` }} />}
        </div>
      ))}</div>
    </ToastCtx.Provider>
  );
}
export function useToast() {
  const ctx = useContext(ToastCtx);
  if (!ctx) throw new Error("useToast outside provider");
  return ctx;
}

/* ---------------------------------------------------------------- Sparkline */
export function Sparkline({ values, width = 90, height = 26, zeroLine = true }: { values: number[]; width?: number; height?: number; zeroLine?: boolean }) {
  if (!values.length) return null;
  const min = Math.min(0, ...values), max = Math.max(0, ...values);
  const span = max - min || 1;
  const x = (i: number) => (i / Math.max(1, values.length - 1)) * (width - 2) + 1;
  const y = (v: number) => height - 1 - ((v - min) / span) * (height - 2);
  const d = values.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  const neg = values.some((v) => v < 0);
  return (
    <svg className="sparkline" width={width} height={height} aria-hidden>
      {zeroLine && min < 0 && <line x1={0} x2={width} y1={y(0)} y2={y(0)} stroke="var(--border-strong)" strokeDasharray="2 2" />}
      <path d={d} fill="none" stroke={neg ? "var(--critical)" : "var(--s-stock-sim)"} strokeWidth={1.5} />
    </svg>
  );
}

/* ---------------------------------------------------------------- Segmented */
export function Segmented<T extends string>({ options, value, onChange, size }: { options: { id: T; label: ReactNode }[]; value: T; onChange: (v: T) => void; size?: "sm" }) {
  return (
    <div className="btn-group" role="group">
      {options.map((o) => <button key={o.id} className={`btn ${size ?? ""} ${o.id === value ? "on" : ""}`} onClick={() => onChange(o.id)}>{o.label}</button>)}
    </div>
  );
}

/* ---------------------------------------------------------------- Confirm (simple) */
export function useConfirm() {
  return useCallback((msg: string) => window.confirm(msg), []);
}
