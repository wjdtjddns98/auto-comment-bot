import { useState } from "react";
import { Link, NavLink, Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { getHealth } from "../lib/apiClient";
import { useAuth } from "../hooks/useAuth";
import { cn } from "../lib/cn";
import { Badge, type BadgeTone } from "./ui/Badge";
import { Button } from "./ui/Button";

function LogoMark() {
  // 레이더 모니터 — 동심원 + 스윕 + 블립(키워드 탐지). 색은 디자인 토큰 brand-600.
  return (
    <svg viewBox="0 0 32 32" fill="none" className="h-7 w-7 shrink-0 text-brand-600" aria-hidden="true">
      <circle cx="16" cy="16" r="13" fill="none" stroke="currentColor" strokeWidth="1.6" opacity="0.3" />
      <circle cx="16" cy="16" r="8" fill="none" stroke="currentColor" strokeWidth="1.6" opacity="0.55" />
      <path d="M16 16 L28 12 A12.5 12.5 0 0 1 27 22 Z" fill="currentColor" opacity="0.13" />
      <line x1="16" y1="16" x2="28" y2="12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <circle cx="16" cy="16" r="2" fill="currentColor" />
      <circle cx="22.5" cy="9.5" r="2.4" fill="currentColor" />
    </svg>
  );
}

function MenuIcon({ open }: { open: boolean }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" className="h-5 w-5" aria-hidden="true">
      {open ? (
        <path strokeLinecap="round" strokeLinejoin="round" d="M6 6l12 12M18 6L6 18" />
      ) : (
        <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
      )}
    </svg>
  );
}

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
  const [menuOpen, setMenuOpen] = useState(false);
  const visibleLinks = navLinks.filter((link) => !link.adminOnly || user?.role === "admin");

  const renderNav = (onNavigate?: () => void, vertical = false) =>
    visibleLinks.map((link) => (
      <NavLink
        key={link.to}
        to={link.to}
        end={link.to === "/"}
        onClick={onNavigate}
        className={({ isActive }) =>
          cn(
            "whitespace-nowrap rounded-md px-2 py-2 hover:bg-gray-100 hover:text-brand-600",
            vertical && "block",
            isActive && "bg-brand-50 font-semibold text-brand-700"
          )
        }
      >
        {link.label}
      </NavLink>
    ));

  return (
    <div className="min-h-screen">
      <header className="border-b border-gray-200 bg-white">
        <div className="flex items-center gap-x-4 px-4 py-3 sm:px-6">
          <Link
            to="/"
            className="flex items-center gap-2 whitespace-nowrap font-bold text-gray-900 hover:text-brand-600"
          >
            <LogoMark />
            SNS 키워드 모니터
          </Link>
          {/* 데스크톱(sm↑) 인라인 내비 */}
          <nav className="hidden flex-wrap gap-x-1 gap-y-1 text-sm text-gray-600 sm:flex">
            {renderNav()}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <HealthBadge />
            {user && (
              <div className="hidden items-center gap-3 sm:flex">
                <span className="whitespace-nowrap text-sm text-gray-600">
                  {user.email} ({user.role})
                </span>
                <Button variant="secondary" size="sm" onClick={() => logout()}>
                  로그아웃
                </Button>
              </div>
            )}
            {/* 모바일(<sm) 햄버거 토글 */}
            <button
              type="button"
              className="rounded-md p-1.5 text-gray-600 hover:bg-gray-100 hover:text-brand-600 sm:hidden"
              onClick={() => setMenuOpen((open) => !open)}
              aria-label="메뉴 열기"
              aria-expanded={menuOpen}
            >
              <MenuIcon open={menuOpen} />
            </button>
          </div>
        </div>

        {/* 모바일 펼침 메뉴 */}
        {menuOpen && (
          <div className="border-t border-gray-100 px-4 py-2 sm:hidden">
            <nav className="flex flex-col gap-1 text-sm text-gray-600">
              {renderNav(() => setMenuOpen(false), true)}
            </nav>
            {user && (
              <div className="mt-2 flex items-center justify-between gap-2 border-t border-gray-100 pt-2">
                <span className="min-w-0 truncate text-sm text-gray-600">
                  {user.email} ({user.role})
                </span>
                <Button variant="secondary" size="sm" onClick={() => logout()}>
                  로그아웃
                </Button>
              </div>
            )}
          </div>
        )}
      </header>
      <main className="px-4 py-6 sm:p-6">
        <Outlet />
      </main>
    </div>
  );
}
