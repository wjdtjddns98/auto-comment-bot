import { Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { ProtectedRoute } from "./routes/ProtectedRoute";
import { AdminRoute } from "./routes/AdminRoute";
import LoginPage from "./pages/LoginPage";
import DashboardPage from "./pages/DashboardPage";
import MatchDetailPage from "./pages/MatchDetailPage";
import AdminSourcesPage from "./pages/AdminSourcesPage";
import AdminKeywordsPage from "./pages/AdminKeywordsPage";
import AdminTemplatesPage from "./pages/AdminTemplatesPage";
import AdminSnsAccountsPage from "./pages/AdminSnsAccountsPage";
import AdminUsersPage from "./pages/AdminUsersPage";
import AuditLogPage from "./pages/AuditLogPage";
import DebugHealthPage from "./pages/DebugHealthPage";
import ThreadsOAuthCallbackPage from "./pages/ThreadsOAuthCallbackPage";
import { THREADS_OAUTH_CALLBACK_PATH } from "./lib/threadsOAuth";

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
        <Route
          path="/admin/sources"
          element={
            <AdminRoute>
              <AdminSourcesPage />
            </AdminRoute>
          }
        />
        <Route
          path="/admin/keywords"
          element={
            <AdminRoute>
              <AdminKeywordsPage />
            </AdminRoute>
          }
        />
        <Route
          path="/admin/templates"
          element={
            <AdminRoute>
              <AdminTemplatesPage />
            </AdminRoute>
          }
        />
        <Route
          path="/admin/users"
          element={
            <AdminRoute>
              <AdminUsersPage />
            </AdminRoute>
          }
        />
        <Route path="/sns-accounts" element={<AdminSnsAccountsPage />} />
        <Route path={THREADS_OAUTH_CALLBACK_PATH} element={<ThreadsOAuthCallbackPage />} />
        <Route path="/audit-log" element={<AuditLogPage />} />
        <Route path="/debug/health" element={<DebugHealthPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
