import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { Activity, BookOpen, ClipboardList, FileSpreadsheet, FlaskConical, LayoutDashboard, Menu, Moon, PenLine, Settings, ShoppingCart, Sun, Monitor, RefreshCw } from "lucide-react";
import { usePerimeter } from "@/state/PerimeterContext";
import { useInvalidateAll, useScenarios } from "@/lib/queries";
import { Button } from "@/components/ui";
import { fmtDate } from "@/lib/format";
import { api } from "@/lib/api";
import { useToast } from "@/components/ui";

const NAV = [
  { to: "/", label: "Cockpit du jour", icon: LayoutDashboard, end: true },
  { to: "/articles", label: "Fiches articles", icon: Activity },
  { to: "/propositions", label: "Calcul CBN & commandes simulées", icon: ShoppingCart },
  { to: "/simulation", label: "Scénarios & simulation", icon: FlaskConical },
  { to: "/saisies", label: "Saisies & journal", icon: PenLine },
  { to: "/imports", label: "Imports / exports", icon: FileSpreadsheet },
  { to: "/referentiel", label: "Référentiel", icon: BookOpen },
  { to: "/parametres", label: "Paramètres & règles", icon: Settings },
];

export default function AppShell() {
  const { perimeter, set, config } = usePerimeter();
  const { data: scenarios } = useScenarios();
  const [open, setOpen] = useState(false);
  const invalidate = useInvalidateAll();
  const toast = useToast();
  const themeIcon = perimeter.theme === "dark" ? Moon : perimeter.theme === "light" ? Sun : Monitor;
  const ThemeIcon = themeIcon;
  const cycleTheme = () => set({ theme: perimeter.theme === "system" ? "light" : perimeter.theme === "light" ? "dark" : "system" });
  const refresh = async () => {
    try { await api.post("/api/reference/refresh"); invalidate(); toast.push("Données ERP rechargées", "success"); }
    catch (e) { toast.push(`Rechargement impossible : ${(e as Error).message}`, "error"); }
  };

  return (
    <div className="shell">
      <aside className={`sidebar ${open ? "open" : ""}`}>
        <div className="brand"><span className="logo"><ClipboardList size={16} /></span>APPRO</div>
        <nav onClick={() => setOpen(false)}>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end} className={({ isActive }) => (isActive ? "active" : "")}>
              <n.icon />{n.label}
            </NavLink>
          ))}
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
            <label className="field" style={{ minWidth: 170 }}>
              <span className="sr-only">Scénario</span>
              <select className="select sm" value={perimeter.scenarioId ?? ""} onChange={(e) => set({ scenarioId: e.target.value || null })}>
                <option value="">Scénario : base (réel)</option>
                {(scenarios ?? []).map((s) => <option key={s.id} value={s.id}>Scénario : {s.name}</option>)}
              </select>
            </label>
            <label className="field" style={{ minWidth: 120 }}>
              <span className="sr-only">Horizon</span>
              <select className="select sm" value={perimeter.horizonDays} onChange={(e) => set({ horizonDays: Number(e.target.value) })}>
                {[30, 60, 90, 120, 180, 270, 365].map((h) => <option key={h} value={h}>Horizon {h} j</option>)}
              </select>
            </label>
            {perimeter.scenarioId && <span className="badge brand">Mode scénario</span>}
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
