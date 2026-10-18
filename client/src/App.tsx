import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "@/components/layout/AppShell";
import CockpitPage from "@/pages/CockpitPage";
import ArticlesPage from "@/pages/ArticlesPage";
import ArticlePage from "@/pages/ArticlePage";
import SuppliersPage from "@/pages/SuppliersPage";
import SupplierPage from "@/pages/SupplierPage";
import SearchPage from "@/pages/SearchPage";
import SupplyTablePage from "@/pages/SupplyTablePage";
import ProgramsPage from "@/pages/ProgramsPage";
import PdpPage from "@/pages/PdpPage";
import EntriesPage from "@/pages/EntriesPage";
import ImportsPage from "@/pages/ImportsPage";
import ReferencePage from "@/pages/ReferencePage";
import SettingsPage from "@/pages/SettingsPage";

/** The search page can be hidden again by turning this off (no menu entry, no route). */
const SEARCH_ENABLED = true;

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<CockpitPage />} />
        <Route path="/articles" element={<ArticlesPage />} />
        <Route path="/articles/:articleId" element={<ArticlePage />} />
        <Route path="/fournisseurs" element={<SuppliersPage />} />
        <Route path="/fournisseurs/:supplierId" element={<SupplierPage />} />
        <Route path="/tableau" element={<SupplyTablePage />} />
        <Route path="/propositions" element={<Navigate to="/?tab=flux" replace />} />
        {SEARCH_ENABLED && <Route path="/recherche" element={<SearchPage />} />}
        <Route path="/programmes" element={<ProgramsPage />} />
        <Route path="/pdp" element={<PdpPage />} />
        <Route path="/saisies" element={<EntriesPage />} />
        <Route path="/imports" element={<ImportsPage />} />
        <Route path="/referentiel" element={<ReferencePage />} />
        <Route path="/parametres" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
