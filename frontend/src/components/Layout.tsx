import { Link, Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getHealth } from "../lib/apiClient";
import { useAuth } from "../hooks/useAuth";

function HealthBadge() {
  const { data, isError } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    staleTime: 30_000,
  });
  const label = isError ? "연결 안 됨" : data?.status ?? "확인 중";
  const color = isError || data?.status === "degraded" ? "crimson" : data?.status === "ok" ? "seagreen" : "gray";
  return <span style={{ color, fontSize: 12 }}>● {label}</span>;
}

export function Layout() {
  const { user, logout } = useAuth();

  return (
    <div style={{ fontFamily: "system-ui" }}>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          gap: 16,
          padding: "12px 24px",
          borderBottom: "1px solid #ddd",
        }}
      >
        <strong>SNS 키워드 모니터</strong>
        <nav style={{ display: "flex", gap: 12 }}>
          <Link to="/">대시보드</Link>
          <Link to="/admin/sources">소스</Link>
          <Link to="/admin/keywords">키워드</Link>
          <Link to="/admin/templates">템플릿</Link>
          <Link to="/admin/sns-accounts">SNS 계정</Link>
          <Link to="/audit-log">감사 로그</Link>
        </nav>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 12 }}>
          <HealthBadge />
          {user && (
            <>
              <span>
                {user.email} ({user.role})
              </span>
              <button onClick={() => logout()}>로그아웃</button>
            </>
          )}
        </div>
      </header>
      <main style={{ padding: 24 }}>
        <Outlet />
      </main>
    </div>
  );
}
