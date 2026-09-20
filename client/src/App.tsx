import { Navigate, Route, Routes } from "react-router-dom";
import AppShell from "@/components/layout/AppShell";
import CockpitPage from "@/pages/CockpitPage";
import ArticlesPage from "@/pages/ArticlesPage";
import ArticlePage from "@/pages/ArticlePage";
import ProposalsPage from "@/pages/ProposalsPage";
import ScenariosPage from "@/pages/ScenariosPage";
import EntriesPage from "@/pages/EntriesPage";
import ImportsPage from "@/pages/ImportsPage";
import ReferencePage from "@/pages/ReferencePage";
import SettingsPage from "@/pages/SettingsPage";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<CockpitPage />} />
        <Route path="/articles" element={<ArticlesPage />} />
        <Route path="/articles/:articleId" element={<ArticlePage />} />
        <Route path="/propositions" element={<ProposalsPage />} />
        <Route path="/simulation" element={<ScenariosPage />} />
        <Route path="/saisies" element={<EntriesPage />} />
        <Route path="/imports" element={<ImportsPage />} />
        <Route path="/referentiel" element={<ReferencePage />} />
        <Route path="/parametres" element={<SettingsPage />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
