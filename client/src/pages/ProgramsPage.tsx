import { Navigate } from "react-router-dom";

/** The programme impact lives in the Plan de production page (tab « Impact programmes ») ; old links follow. */
export default function ProgramsPage() {
  return <Navigate to="/pdp?tab=impact" replace />;
}
