import { useEffect, useState } from "react";
import { LayoutDashboard, MessagesSquare, CalendarClock, Moon, Sun, LogOut, Library, Brain, Settings, Users, BarChart3, Database, Menu, X } from "lucide-react";
import { NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
import { useAuth, isAdminish } from "@/lib/auth";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/", label: "概览", icon: LayoutDashboard, end: true },
  { to: "/chat", label: "对话工作区", icon: MessagesSquare, end: false },
  { to: "/data-prep", label: "数据工作台", icon: Database, end: false },
  { to: "/tasks", label: "自动化任务", icon: CalendarClock, end: false },
  { to: "/templates", label: "模板库", icon: Library, end: false },
  { to: "/memory", label: "记忆", icon: Brain, end: false },
  { to: "/settings", label: "设置", icon: Settings, end: false },
];

// 仅管理员可见
const NAV_ADMIN = [
  { to: "/feedback", label: "反馈管理", icon: BarChart3, end: false },
  { to: "/admin", label: "用户管理", icon: Users, end: false },
];

export function Layout() {
  const { user, logout } = useAuth();
  const { theme, toggle } = useTheme();
  const navigate = useNavigate();
  const location = useLocation();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const compactDataPrep = location.pathname === "/data-prep";

  useEffect(() => {
    setMobileNavOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!mobileNavOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileNavOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [mobileNavOpen]);

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background">
      {compactDataPrep && mobileNavOpen && (
        <button
          type="button"
          aria-label="关闭导航背景"
          onClick={() => setMobileNavOpen(false)}
          className="fixed inset-0 z-40 bg-foreground/20 md:hidden"
        />
      )}
      {/* 左侧导航 */}
      <aside className={cn(
        "flex w-60 shrink-0 flex-col border-r border-border bg-sidebar text-sidebar-foreground",
        compactDataPrep && !mobileNavOpen && "max-md:hidden",
        compactDataPrep && mobileNavOpen && "max-md:fixed max-md:inset-y-0 max-md:left-0 max-md:z-50",
      )}>
        <div className="flex items-center gap-2.5 px-5 py-5">
          <img src="/logo.svg" alt="howso@Mangrove" className="h-8 w-8" />
          <div className="leading-tight">
            <div className="text-[15px] font-semibold text-foreground">howso@Mangrove</div>
            <div className="text-[11px] text-muted-foreground">数据治理智能体</div>
          </div>
          {compactDataPrep && mobileNavOpen && (
            <button
              type="button"
              aria-label="关闭导航"
              onClick={() => setMobileNavOpen(false)}
              className="ml-auto rounded-lg p-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring md:hidden"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        <nav className="flex-1 space-y-1 px-3 py-2">
          {NAV.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              onClick={() => setMobileNavOpen(false)}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                  isActive
                    ? "bg-primary/12 text-teal-700 dark:text-teal-300"
                    : "text-sidebar-foreground hover:bg-accent hover:text-accent-foreground",
                )
              }
            >
              <item.icon className="h-[18px] w-[18px]" />
              {item.label}
            </NavLink>
          ))}

          {isAdminish(user?.role) &&
            NAV_ADMIN.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                onClick={() => setMobileNavOpen(false)}
                className={({ isActive }) =>
                  cn(
                    "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors",
                    isActive
                      ? "bg-primary/12 text-teal-700 dark:text-teal-300"
                      : "text-sidebar-foreground hover:bg-accent hover:text-accent-foreground",
                  )
                }
              >
                <item.icon className="h-[18px] w-[18px]" />
                {item.label}
              </NavLink>
            ))}
        </nav>

        {/* 底部：主题 + 用户 */}
        <div className="border-t border-border p-3">
          <button
            onClick={toggle}
            className="mb-1 flex w-full items-center gap-3 rounded-md px-3 py-2 text-sm text-sidebar-foreground transition-colors hover:bg-accent hover:text-accent-foreground"
          >
            {theme === "dark" ? <Sun className="h-[18px] w-[18px]" /> : <Moon className="h-[18px] w-[18px]" />}
            {theme === "dark" ? "浅色主题" : "深色主题"}
          </button>
          <div className="flex items-center gap-2.5 rounded-md px-3 py-2">
            <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/15 text-sm font-medium text-primary">
              {(user?.display_name || "U").slice(0, 1).toUpperCase()}
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-sm font-medium text-foreground">{user?.display_name}</div>
              <div className="truncate text-[11px] text-muted-foreground">@{user?.username}</div>
            </div>
            <button
              onClick={() => {
                logout();
                navigate("/login");
              }}
              className="rounded-md p-1.5 text-muted-foreground transition-colors hover:bg-accent hover:text-foreground"
              title="退出登录"
            >
              <LogOut className="h-4 w-4" />
            </button>
          </div>
          {/* 出品方归属（LOGO 按主题变色：浅色藏青 / 深色白） */}
          <div className="mt-1 flex items-center justify-center gap-1.5 px-3 text-[10px] text-muted-foreground">
            <span>出品</span>
            <img src="/howso-logo-mark.png" alt="华苏科技" className="h-3.5 w-auto dark:brightness-0 dark:invert" />
            <span>南京华苏科技</span>
          </div>
        </div>
      </aside>

      {/* 主内容 */}
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {compactDataPrep && (
          <div className="hidden h-11 shrink-0 items-center justify-between border-b bg-background px-3 max-md:flex">
            <button
              type="button"
              aria-label={mobileNavOpen ? "关闭导航" : "打开导航"}
              aria-expanded={mobileNavOpen}
              onClick={() => setMobileNavOpen((open) => !open)}
              className="rounded-lg p-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {mobileNavOpen ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
            </button>
            <span className="text-xs font-medium text-muted-foreground">Mangrove 数据工作台</span>
          </div>
        )}
        <Outlet />
      </main>
    </div>
  );
}
