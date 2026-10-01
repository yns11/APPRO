import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { Activity, BookOpen, ClipboardList, Factory, FileSpreadsheet, LayoutDashboard, Menu, Moon, PanelLeftClose, PanelLeftOpen, PenLine, Settings, ShoppingCart, Sun, Monitor, RefreshCw, Table } from "lucide-react";
import { usePerimeter } from "@/state/PerimeterContext";
import { useInvalidateAll } from "@/lib/queries";
import { Button } from "@/components/ui";
import { fmtDate } from "@/lib/format";
import { api } from "@/lib/api";
import { useToast } from "@/components/ui";

const NAV = [
  { to: "/", label: "Cockpit du jour", icon: LayoutDashboard, end: true },
  { to: "/tableau", label: "Tableau d'approvisionnement", icon: Table },
  { to: "/articles", label: "Fiches articles", icon: Activity },
  { to: "/propositions", label: "Propositions CBN", icon: ShoppingCart },
  { to: "/saisies", label: "Saisies & journal", icon: PenLine },
];
const MORE = [
  { to: "/referentiel", label: "Référentiel", icon: BookOpen },
  { to: "/programmes", label: "Impact programmes", icon: Factory },
  { to: "/imports", label: "Imports / exports", icon: FileSpreadsheet },
  { to: "/parametres", label: "Paramètres & règles", icon: Settings },
];

export default function AppShell() {
  const { perimeter, set, config } = usePerimeter();
  const [open, setOpen] = useState(false);
  const invalidate = useInvalidateAll();
  const toast = useToast();
  const ThemeIcon = perimeter.theme === "dark" ? Moon : perimeter.theme === "light" ? Sun : Monitor;
  const cycleTheme = () => set({ theme: perimeter.theme === "system" ? "light" : perimeter.theme === "light" ? "dark" : "system" });
  const refresh = async () => {
    try { await api.post("/api/reference/refresh"); invalidate(); toast.push("Données ERP rechargées", "success"); }
    catch (e) { toast.push(`Rechargement impossible : ${(e as Error).message}`, "error"); }
  };
  const mini = perimeter.sidebarCollapsed;
  const link = (n: { to: string; label: string; icon: typeof Table; end?: boolean }) => (
    <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "active" : "")} title={mini ? n.label : undefined} aria-label={n.label}><n.icon /><span className="lbl">{n.label}</span></NavLink>
  );

  return (
    <div className={`shell ${mini ? "collapsed" : ""}`}>
      <aside className={`sidebar ${open ? "open" : ""} ${mini ? "mini" : ""}`}>
        <div className="brand"><span className="logo"><ClipboardList size={16} /></span><span className="txt">APPRO</span></div>
        <Button variant="ghost" icon size="sm" className="collapse no-print" title={mini ? "Déployer le panneau de navigation" : "Réduire le panneau de navigation (icônes seules)"} aria-label={mini ? "Déployer la navigation" : "Réduire la navigation"} onClick={() => set({ sidebarCollapsed: !mini })}>{mini ? <PanelLeftOpen /> : <PanelLeftClose />}</Button>
        <nav onClick={() => setOpen(false)}>
          {NAV.map(link)}
          <div className="nav-group small subtle">Plus</div>
          {MORE.map(link)}
        </nav>
        <div className="foot">
          <div>{config?.user ?? "…"}</div>
          <div>Source : {String(config?.data_source?.name ?? "…")}</div>
          <div>v{config?.version ?? ""}</div>
        </div>
      </aside>
      <div className="main">
        <header className="topbar">
          <Button variant="ghost" icon className="no-print" style={{ display: "none" }} id="menu-btn" onClick={() => setOpen((o) => !o)} aria-label="Menu"><Menu /></Button>
          <div className="ctx">
            <label className="field" style={{ minWidth: 150 }}>
              <span className="sr-only">Périmètre</span>
              <select className="select sm" value={perimeter.planner ?? ""} onChange={(e) => set({ planner: e.target.value || null })}>
                <option value="">Tous les approvisionneurs</option>
                {(config?.planners ?? []).map((p) => <option key={p} value={p}>{p}</option>)}
              </select>
            </label>
            <label className="field" style={{ minWidth: 120 }}>
              <span className="sr-only">Horizon</span>
              <select className="select sm" value={perimeter.horizonDays} onChange={(e) => set({ horizonDays: Number(e.target.value) })}>
                {[30, 60, 90, 120, 180, 270, 365].map((h) => <option key={h} value={h}>Horizon {h} j</option>)}
              </select>
            </label>
          </div>
          <div className="right row">
            <span className="freshness">Référence : <b>{config ? fmtDate(config.as_of) : "…"}</b></span>
            <Button variant="ghost" icon title="Recharger les données ERP" onClick={refresh}><RefreshCw /></Button>
            <Button variant="ghost" icon title={`Thème : ${perimeter.theme}`} onClick={cycleTheme}><ThemeIcon /></Button>
          </div>
        </header>
        <main className="content"><Outlet /></main>
      </div>
      <style>{`@media (max-width: 900px) { #menu-btn { display: inline-flex !important; } }`}</style>
    </div>
  );
}
