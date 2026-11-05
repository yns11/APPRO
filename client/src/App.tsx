import { Suspense, lazy } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "@/components/layout/AppShell";
import { SkeletonBlock } from "@/components/ui";
import { usePerimeter } from "@/state/PerimeterContext";

// every page is loaded **on demand** : the first screen only downloads the shell and the page it shows (F33)
const CockpitPage = lazy(() => import("@/pages/CockpitPage"));
const ArticlesPage = lazy(() => import("@/pages/ArticlesPage"));
const ArticlePage = lazy(() => import("@/pages/ArticlePage"));
const SuppliersPage = lazy(() => import("@/pages/SuppliersPage"));
const DeliveriesPage = lazy(() => import("@/pages/DeliveriesPage"));
const SupplierPage = lazy(() => import("@/pages/SupplierPage"));
const SearchPage = lazy(() => import("@/pages/SearchPage"));
const SupplyTablePage = lazy(() => import("@/pages/SupplyTablePage"));
const ProgramsPage = lazy(() => import("@/pages/ProgramsPage"));
const PdpPage = lazy(() => import("@/pages/PdpPage"));
const EntriesPage = lazy(() => import("@/pages/EntriesPage"));
const ImportsPage = lazy(() => import("@/pages/ImportsPage"));
const ReferencePage = lazy(() => import("@/pages/ReferencePage"));
const SettingsPage = lazy(() => import("@/pages/SettingsPage"));
const HelpPage = lazy(() => import("@/pages/HelpPage"));
const SupplierPortalPage = lazy(() => import("@/pages/SupplierPortalPage"));
const PerformancePage = lazy(() => import("@/pages/PerformancePage"));
const OtdSheetPage = lazy(() => import("@/pages/OtdSheetPage"));
const TransportPlansPage = lazy(() => import("@/pages/TransportPlansPage"));

/** The search page can be hidden again by turning this off (no menu entry, no route). */
const SEARCH_ENABLED = true;

export default function App() {
  const { config } = usePerimeter();
  // a supplier's transport contact only sees the « espace fournisseur » (the API refuses everything else anyway)
  const fallback = <div className="page"><SkeletonBlock rows={6} /></div>;
  if (config?.access?.role === "supplier") {
    return (
      <Suspense fallback={fallback}>
      <Routes>
        <Route element={<AppShell />}>
          <Route path="/portail" element={<SupplierPortalPage />} />
          <Route path="/aide" element={<HelpPage />} />
          <Route path="/aide/:slug" element={<HelpPage />} />
          <Route path="*" element={<Navigate to="/portail" replace />} />
        </Route>
      </Routes>
      </Suspense>
    );
  }
  return (
    <Suspense fallback={fallback}>
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<CockpitPage />} />
        <Route path="/portail" element={<SupplierPortalPage />} />
        <Route path="/articles" element={<ArticlesPage />} />
        <Route path="/articles/:articleId" element={<ArticlePage />} />
        <Route path="/fournisseurs" element={<SuppliersPage />} />
        <Route path="/fournisseurs/:supplierId" element={<SupplierPage />} />
        <Route path="/tableau" element={<SupplyTablePage />} />
        {(!config || config.access?.can_transports) && <Route path="/livraisons" element={<DeliveriesPage />} />}
        {(!config || config.access?.can_transports) && <Route path="/livraisons/envois" element={<TransportPlansPage />} />}
        <Route path="/propositions" element={<Navigate to="/?tab=flux" replace />} />
        {SEARCH_ENABLED && <Route path="/recherche" element={<SearchPage />} />}
        <Route path="/programmes" element={<ProgramsPage />} />
        <Route path="/pdp" element={<PdpPage />} />
        <Route path="/saisies" element={<EntriesPage />} />
        <Route path="/imports" element={<ImportsPage />} />
        <Route path="/referentiel" element={<ReferencePage />} />
        <Route path="/performance" element={<PerformancePage />} />
        <Route path="/performance/otd/:supplierId/:month" element={<OtdSheetPage />} />
        <Route path="/parametres" element={<SettingsPage />} />
        <Route path="/aide" element={<HelpPage />} />
        <Route path="/aide/:slug" element={<HelpPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
    </Suspense>
  );
}
