import * as Dialog from "@radix-ui/react-dialog";
import { useEffect, useRef, useState } from "react";
import { LayoutDashboard, MessagesSquare, CalendarClock, Moon, Sun, LogOut, Library, Brain, Settings, Users, BarChart3, Database, Menu, X } from "lucide-react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { toast } from "sonner";
import { useAuth, isAdminish } from "@/lib/auth";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

const NAV = [
  { to: "/", label: "概览", icon: LayoutDashboard, end: true },
  { to: "/chat", label: "旧版对话", icon: MessagesSquare, end: false },
  { to: "/data-prep", label: "任务工作台", icon: Database, end: false },
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

// OwnerQueryScope换账号会重挂载Layout；只在本页内存保留身份边界，不能因此认领旧地址。
let navigationOwner: { ownerId: string; blockedLocation: string | null } | null = null;

export function Layout() {
  const { user, logout } = useAuth();
  const { theme, toggle } = useTheme();
  const location = useLocation();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const taskWorkspace = location.pathname === "/data-prep" && new URLSearchParams(location.search).get("legacy") !== "1";
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 767px)").matches);
  const drawer = taskWorkspace || narrow;
  const menuButton = useRef<HTMLButtonElement>(null);
  const main = useRef<HTMLElement>(null);
  const workspaceReturn = useRef<{ ownerId: string; to: string; hasTask: boolean } | null>(null);
  // 只保留当前Owner的真实路由身份，不复制正文、任意查询参数或持久化秘密。
  if (user && navigationOwner?.ownerId !== user.user_id) {
    workspaceReturn.current = null;
    // 换账号时旧地址仍在屏幕上；禁止后续重渲染把它重新归给新Owner。
    navigationOwner = { ownerId: user.user_id, blockedLocation: navigationOwner ? location.key : null };
  }
  if (taskWorkspace && user && location.key !== navigationOwner?.blockedLocation) {
    const current = new URLSearchParams(location.search);
    const target = new URLSearchParams();
    for (const key of ["task", "revision"]) if (current.has(key)) target.set(key, current.get(key)!);
    workspaceReturn.current = { ownerId: user.user_id, to: `/data-prep${target.size ? `?${target}` : ""}`, hasTask: Boolean(target.get("task")) };
  }
  const returnTarget = ["/settings", "/admin"].includes(location.pathname) ? workspaceReturn.current : null;
  const pageLabel = [...NAV, ...NAV_ADMIN].find(item => item.to === location.pathname)?.label || "Mangrove";
  const NavigationContainer = drawer ? "div" : "aside";

  useEffect(() => {
    setMobileNavOpen(false);
  }, [location.pathname, location.search, drawer]);

  useEffect(() => {
    const media = window.matchMedia("(max-width: 767px)");
    const update = () => setNarrow(media.matches);
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);

  const navigation = (
      <NavigationContainer className={cn(
        "flex w-60 max-w-[calc(100vw-2rem)] shrink-0 flex-col border-r border-border bg-sidebar text-sidebar-foreground",
        drawer && "fixed inset-y-0 left-0 z-50",
      )}>
        {drawer && <Dialog.Title className="sr-only">全局导航</Dialog.Title>}
        <div className="flex items-center gap-2.5 px-5 py-5">
          <img src="/logo.svg" alt="howso@Mangrove" className="h-8 w-8" />
          <div className="leading-tight">
            <div className="text-[15px] font-semibold text-foreground">howso@Mangrove</div>
            <div className="text-[11px] text-muted-foreground">数据治理智能体</div>
          </div>
          {drawer && (
            <button
              type="button"
              aria-label="关闭导航"
              onClick={() => setMobileNavOpen(false)}
              className="ml-auto rounded-lg p-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        <nav className="min-h-0 flex-1 space-y-1 overflow-y-auto px-3 py-2">
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
              disabled={loggingOut}
              onClick={async () => {
                setLoggingOut(true);
                try {
                  await logout();
                } catch (error) {
                  toast.error(error instanceof Error ? error.message : "退出失败，请重试");
                } finally {
                  setLoggingOut(false);
                }
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
      </NavigationContainer>
  );

  return (
    <div className="flex h-screen w-screen overflow-hidden bg-background">
      {/* 左侧导航 */}
      {drawer ? (
        <Dialog.Root open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
          <Dialog.Portal>
            <Dialog.Overlay className="fixed inset-0 z-40 bg-foreground/20" />
            <Dialog.Content asChild aria-label="全局导航" aria-describedby={undefined} onCloseAutoFocus={event => {
              event.preventDefault();
              (menuButton.current || main.current)?.focus();
            }}>{navigation}</Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      ) : navigation}

      {/* 主内容 */}
      <main ref={main} tabIndex={-1} className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {(drawer || returnTarget) && (
          <div className="flex min-h-11 shrink-0 flex-wrap items-center justify-between gap-2 border-b bg-background px-3 py-1">
            {drawer && <button
              ref={menuButton}
              type="button"
              aria-label={mobileNavOpen ? "关闭导航" : "打开导航"}
              aria-expanded={mobileNavOpen}
              onClick={() => setMobileNavOpen((open) => !open)}
              className="rounded-lg p-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              {mobileNavOpen ? <X className="h-4 w-4" /> : <Menu className="h-4 w-4" />}
            </button>}
            <span className="text-xs font-medium text-muted-foreground">Mangrove · {pageLabel}</span>
            {returnTarget && <Link to={returnTarget.to} className="rounded-md px-2 py-1 text-sm text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">{returnTarget.hasTask ? "返回原任务" : "返回工作台"}</Link>}
          </div>
        )}
        <Outlet />
      </main>
    </div>
  );
}
