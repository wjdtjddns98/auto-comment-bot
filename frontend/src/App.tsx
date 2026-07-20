import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { ProtectedRoute } from "./routes/ProtectedRoute";
import LoginPage from "./pages/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import MatchDetailPage from "./pages/MatchDetailPage";
import AdminSourcesPage from "./pages/AdminSourcesPage";
import AdminKeywordsPage from "./pages/AdminKeywordsPage";
import AdminTemplatesPage from "./pages/AdminTemplatesPage";
import AdminSnsAccountsPage from "./pages/AdminSnsAccountsPage";
import AuditLogPage from "./pages/AuditLogPage";
import DebugHealthPage from "./pages/DebugHealthPage";

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <ProtectedRoute>
            <Layout />
          </ProtectedRoute>
        }
      >
        <Route path="/" element={<DashboardPage />} />
        <Route path="/matches/:id" element={<MatchDetailPage />} />
        <Route path="/admin/sources" element={<AdminSourcesPage />} />
        <Route path="/admin/keywords" element={<AdminKeywordsPage />} />
        <Route path="/admin/templates" element={<AdminTemplatesPage />} />
        <Route path="/admin/sns-accounts" element={<AdminSnsAccountsPage />} />
        <Route path="/audit-log" element={<AuditLogPage />} />
        <Route path="/debug/health" element={<DebugHealthPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
