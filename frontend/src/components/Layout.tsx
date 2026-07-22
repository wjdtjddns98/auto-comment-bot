import { Link, NavLink, Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getHealth } from "../lib/apiClient";
import { useAuth } from "../hooks/useAuth";
import { cn } from "../lib/cn";
import { Badge, type BadgeTone } from "./ui/Badge";
import { Button } from "./ui/Button";

function HealthBadge() {
  const { data, isError } = useQuery({
    queryKey: ["health"],
    queryFn: getHealth,
    staleTime: 30_000,
  });
  const label = isError ? "연결 안 됨" : data?.status ?? "확인 중";
  const tone: BadgeTone =
    isError || data?.status === "degraded" ? "danger" : data?.status === "ok" ? "success" : "neutral";
  return <Badge tone={tone}>● {label}</Badge>;
}

const navLinks = [
  { to: "/", label: "대시보드" },
  { to: "/admin/sources", label: "소스", adminOnly: true },
  { to: "/admin/keywords", label: "키워드", adminOnly: true },
  { to: "/admin/templates", label: "템플릿", adminOnly: true },
  { to: "/admin/users", label: "사용자", adminOnly: true },
  { to: "/sns-accounts", label: "SNS 계정" },
  { to: "/audit-log", label: "감사 로그" },
];

export function Layout() {
  const { user, logout } = useAuth();
  const visibleLinks = navLinks.filter((link) => !link.adminOnly || user?.role === "admin");

  return (
    <div className="min-h-screen">
      <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b border-gray-200 bg-white px-6 py-3">
        <Link to="/" className="whitespace-nowrap font-bold text-gray-900 hover:text-brand-600">
          SNS 키워드 모니터
        </Link>
        <nav className="flex flex-wrap gap-x-1 gap-y-1 text-sm text-gray-600">
          {visibleLinks.map((link) => (
            <NavLink
              key={link.to}
              to={link.to}
              end={link.to === "/"}
              className={({ isActive }) =>
                cn(
                  "whitespace-nowrap rounded-md px-2 py-2 hover:bg-gray-100 hover:text-brand-600",
                  isActive && "bg-brand-50 font-semibold text-brand-700"
                )
              }
            >
              {link.label}
            </NavLink>
          ))}
        </nav>
        <div className="ml-auto flex flex-wrap items-center gap-3">
          <HealthBadge />
          {user && (
            <>
              <span className="whitespace-nowrap text-sm text-gray-600">
                {user.email} ({user.role})
              </span>
              <Button variant="secondary" size="sm" onClick={() => logout()}>
                로그아웃
              </Button>
            </>
          )}
        </div>
      </header>
      <main className="p-6">
        <Outlet />
      </main>
    </div>
  );
}
