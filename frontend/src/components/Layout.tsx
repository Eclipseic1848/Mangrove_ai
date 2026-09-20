import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import * as Tooltip from "@radix-ui/react-tooltip";
import { LayoutDashboard, CalendarClock, Moon, Sun, LogOut, Library, Brain, Settings, Users, BarChart3, Database, Menu, X, PanelLeftClose, PanelLeftOpen, ChevronUp, ShieldCheck } from "lucide-react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth, isAdminish, roleLabel } from "@/lib/auth";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { OperationsActivity } from "@/components/OperationsActivity";

const NAV = [
  { to: "/", label: "概览", icon: LayoutDashboard, end: true },
  { to: "/data-prep", label: "任务工作台", icon: Database, end: false },
  { to: "/tasks", label: "自动化任务", icon: CalendarClock, end: false },
  { to: "/templates", label: "模板库", icon: Library, end: false },
  { to: "/memory", label: "记忆", icon: Brain, end: false },
];

const SETTINGS = { to: "/settings", label: "设置", icon: Settings, end: false };

// 仅管理员可见
const NAV_ADMIN = [
  { to: "/feedback", label: "反馈管理", icon: BarChart3, end: false },
  { to: "/admin", label: "用户管理", icon: Users, end: false },
];

function SidebarAccount({ collapsed }: { collapsed: boolean }) {
  const { user, logout } = useAuth();
  const location = useLocation();
  const details = useRef<HTMLDetailsElement>(null);
  const pending = useRef(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    if (details.current) details.current.open = false;
  }, [location.key, collapsed]);
  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      if (details.current && !details.current.contains(event.target as Node)) details.current.open = false;
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, []);

  return <details ref={details} data-account-options className="relative min-w-0 flex-1"
    onBlur={event => { if (event.relatedTarget && !event.currentTarget.contains(event.relatedTarget)) event.currentTarget.open = false; }}
    onKeyDown={event => {
      if (event.key === "Escape" && event.currentTarget.open) {
        event.preventDefault(); event.stopPropagation();
        event.currentTarget.open = false;
        event.currentTarget.querySelector("summary")?.focus();
      }
    }}>
    <summary aria-label="账号选项" className={cn("nav-action flex min-h-14 list-none items-center gap-2.5 rounded-lg px-2 py-2 [&::-webkit-details-marker]:hidden", collapsed && "md:justify-center md:px-0")}>
      <span aria-hidden="true" className="nav-avatar flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-sm font-medium">{(user?.display_name || "U").slice(0, 1).toUpperCase()}</span>
      <span className={cn("min-w-0 flex-1", collapsed && "md:sr-only")}>
        <span className="block truncate text-sm font-medium">{user?.display_name}</span>
        <span className="nav-secondary block text-xs">{roleLabel(user?.role)}</span>
      </span>
      <ChevronUp aria-hidden="true" className={cn("h-4 w-4 shrink-0", collapsed && "md:hidden")} />
    </summary>
    <div role="group" aria-label="账号操作" className="absolute bottom-full left-0 z-50 mb-2 max-h-[min(24rem,60dvh)] w-64 max-w-[calc(100vw-2rem)] overflow-y-auto rounded-xl border border-border bg-card p-2 text-foreground shadow-lg">
      <div className="border-b px-2 py-2">
        <p className="break-words text-sm font-medium">{user?.display_name}</p>
        <p className="break-all text-xs text-muted-foreground">@{user?.username} · {roleLabel(user?.role)}</p>
      </div>
      <Link to="/settings?section=personal" className="my-1 flex min-h-10 items-center gap-2 rounded-lg px-2 text-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><Settings aria-hidden="true" className="h-4 w-4" />账号设置</Link>
      <div className="border-t pt-1">
        <button type="button" title="退出登录" disabled={loggingOut} aria-busy={loggingOut}
          className="flex min-h-10 w-full cursor-pointer items-center gap-2 rounded-lg px-2 text-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-wait disabled:opacity-60"
          onClick={async () => {
            // 等服务端确认再清理身份；失败不离开页面，也不重复提交。
            if (pending.current) return;
            pending.current = true; setLoggingOut(true); setError("");
            try { await logout(); }
            catch (cause) { setError(cause instanceof Error ? cause.message : "退出失败，请重试"); }
            finally { pending.current = false; setLoggingOut(false); }
          }}>
          <LogOut aria-hidden="true" className="h-4 w-4" />{loggingOut ? "正在退出…" : "退出登录"}
        </button>
        <p className="px-2 py-1 text-xs leading-5 text-muted-foreground">仅退出当前设备，后台任务继续运行。</p>
        {error && <p role="alert" className="px-2 py-1 text-xs leading-5 text-destructive">{error}</p>}
      </div>
    </div>
  </details>;
}

export function Layout() {
  const { user } = useAuth();
  const { theme, toggle } = useTheme();
  const location = useLocation();
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const groups = [
    { label: "工作空间", items: NAV },
    { label: "管理", items: [...(isAdminish(user?.role) ? [{ to: "/operations", label: "运营审计", icon: ShieldCheck, end: false }] : []), ...(isAdminish(user?.role) ? NAV_ADMIN : []), SETTINGS] },
  ];

  useEffect(() => {
    setMobileNavOpen(false);
  }, [location.key]);

  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 768px)");
    const closeOnDesktop = () => { if (desktop.matches) setMobileNavOpen(false); };
    desktop.addEventListener("change", closeOnDesktop);
    return () => desktop.removeEventListener("change", closeOnDesktop);
  }, []);

  const navigation = (
      <aside aria-label="全局导航" className={cn(
        "app-navigation flex min-h-0 w-60 shrink-0 flex-col border-r",
        sidebarCollapsed && "md:w-16",
      )}>
        <div className="flex h-20 shrink-0 items-center gap-2 px-3">
          <img src="/logo.svg" alt="howso@Mangrove" className={cn("h-8 w-8 shrink-0", sidebarCollapsed && "md:hidden")} />
          <div className={cn("min-w-0 flex-1 leading-tight", sidebarCollapsed && "md:hidden")}>
            <div className="truncate text-[13px] font-semibold">howso@Mangrove</div>
            <div className="nav-secondary text-[11px]">数据治理智能体</div>
          </div>
          <button
            type="button"
            aria-label={sidebarCollapsed ? "展开侧边栏" : "收起侧边栏"}
            title={sidebarCollapsed ? "展开侧边栏" : "收起侧边栏"}
            aria-expanded={!sidebarCollapsed}
            onClick={() => setSidebarCollapsed(collapsed => !collapsed)}
            className="nav-action mx-auto hidden h-9 w-9 shrink-0 items-center justify-center rounded-md md:flex"
          >
            {sidebarCollapsed ? <PanelLeftOpen className="h-4 w-4" /> : <PanelLeftClose className="h-4 w-4" />}
          </button>
          {mobileNavOpen && (
            <button
              type="button"
              aria-label="关闭导航"
              onClick={() => setMobileNavOpen(false)}
              className="nav-action ml-auto rounded-lg p-2"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>

        <div className="min-h-0 flex-1 space-y-6 overflow-y-auto px-3 py-2">
          <Tooltip.Provider delayDuration={200}>
            {groups.map(group => <nav key={group.label} aria-label={group.label} className="space-y-1">
              <p className={cn("nav-secondary mb-2 px-3 text-xs", sidebarCollapsed && "md:sr-only")}>{group.label}</p>
              {group.items.map(item => <Tooltip.Root key={item.to}>
                <Tooltip.Trigger asChild>
                  <NavLink to={item.to} end={item.end} title={sidebarCollapsed ? item.label : undefined}
                    onClick={() => setMobileNavOpen(false)}
                    className={cn("nav-action nav-link relative flex min-h-11 items-center gap-3 rounded-lg px-3 text-sm font-medium", sidebarCollapsed && "md:justify-center md:px-0")}>
                    <item.icon aria-hidden="true" className="h-[18px] w-[18px] shrink-0" />
                    <span className={cn(sidebarCollapsed && "md:sr-only")}>{item.label}</span>
                  </NavLink>
                </Tooltip.Trigger>
                {sidebarCollapsed && <Tooltip.Portal><Tooltip.Content side="right" sideOffset={8} className="z-50 hidden rounded-md border bg-card px-3 py-2 text-sm text-foreground shadow-md md:block">{item.label}</Tooltip.Content></Tooltip.Portal>}
              </Tooltip.Root>)}
            </nav>)}
          </Tooltip.Provider>
        </div>

        {/* 底部：主题 + 用户 */}
        <div className="nav-footer shrink-0 border-t p-3">
          <div className={cn("flex items-center gap-1", sidebarCollapsed && "md:flex-col")}>
          <SidebarAccount collapsed={sidebarCollapsed} />
          <Tooltip.Provider delayDuration={200}><Tooltip.Root>
            <Tooltip.Trigger asChild>
              <button type="button" onClick={toggle} aria-label={theme === "dark" ? "浅色主题" : "深色主题"}
                className="nav-action flex h-9 w-9 shrink-0 items-center justify-center rounded-lg border border-[var(--nav-border)]">
                {theme === "dark" ? <Sun aria-hidden="true" className="h-4 w-4" /> : <Moon aria-hidden="true" className="h-4 w-4" />}
              </button>
            </Tooltip.Trigger>
            <Tooltip.Portal><Tooltip.Content side="top" sideOffset={8} className="z-[60] rounded-md border bg-card px-3 py-2 text-xs text-foreground shadow-md">
              {theme === "dark" ? "切换为浅色" : "切换为深色"}
            </Tooltip.Content></Tooltip.Portal>
          </Tooltip.Root></Tooltip.Provider>
          </div>
          {/* 导航在两种主题中均为深色，品牌标识始终使用浅色版本。 */}
          <div className={cn("nav-secondary mt-2 flex items-center justify-center gap-1.5 px-3 text-[11px]", sidebarCollapsed && "md:hidden")}>
            <img src="/howso-logo-mark.png" alt="华苏科技" className="h-3.5 w-auto brightness-0 invert" />
            <span>南京华苏科技</span>
          </div>
        </div>
      </aside>
  );

  return (
    <div className="fixed inset-0 flex h-dvh w-full overflow-hidden bg-background">
      {/* 左侧导航 */}
      <div className="hidden md:flex">{navigation}</div>
      <Dialog.Root open={mobileNavOpen} onOpenChange={setMobileNavOpen}>
        <Dialog.Portal>
          <Dialog.Overlay className="fixed inset-0 z-40 bg-black/30 md:hidden" />
          <Dialog.Content aria-describedby={undefined} className="fixed inset-y-0 left-0 z-50 flex w-60 md:hidden" onEscapeKeyDown={event => {
            // 先关闭内部账号浮层；第二次 Escape 才关闭导航抽屉。
            const account = event.target instanceof Element ? event.target.closest<HTMLDetailsElement>("[data-account-options][open]") : null;
            if (account) { event.preventDefault(); account.open = false; account.querySelector("summary")?.focus(); }
          }} onCloseAutoFocus={event => {
            event.preventDefault();
            document.querySelector<HTMLButtonElement>('[aria-label="打开导航"]')?.focus();
          }}>
            <Dialog.Title className="sr-only">全局导航</Dialog.Title>
            {navigation}
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>

      {/* 主内容 */}
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
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
            <span className="text-xs font-medium text-muted-foreground">Mangrove {[...NAV, SETTINGS, ...NAV_ADMIN].find(item => item.end ? location.pathname === item.to : location.pathname.startsWith(item.to))?.label || "工作台"}</span>
          </div>
        <OperationsActivity />
        <Outlet />
      </main>
    </div>
  );
}
