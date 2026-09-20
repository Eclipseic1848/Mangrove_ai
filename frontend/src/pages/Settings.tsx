import { useEffect, useRef, useState } from "react";
import { PageGuide } from "@/components/onboarding/PageGuide";
import { Link, useSearchParams } from "react-router-dom";
import {
  Boxes, Cpu, Sparkles, Save, Moon, Sun, CircleDot, RefreshCw,
  Play, Loader2, CheckCircle2, XCircle, ShieldAlert, Unlock, UserRound,
  KeyRound, SlidersHorizontal, Activity, ShieldCheck, LogOut,
} from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api } from "@/lib/api";
import { useAuth, isAdminish, roleLabel } from "@/lib/auth";
import { AdminConfigCenter, SelfConfigCenter } from "@/components/ConfigCenter";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { ModelConnectionsPanel } from "@/pages/settings/ModelConnectionsPanel";
import { CapabilityGovernancePanel } from "@/pages/settings/CapabilityGovernancePanel";
import { TaskModelSettings } from "@/pages/settings/TaskModelSettings";

interface Overview {
  collectors: { name: string; tier: number; available: boolean }[];
  scheduler: { enabled: boolean; active_count: number };
  connectors: { email: boolean; slack: boolean; embedding: boolean; checkpoint: boolean };
  connectors_enabled: { email: boolean; slack: boolean; embedding: boolean; checkpoint: boolean };
}

const COLLECTOR_CN: Record<string, string> = {
  mediacrawler: "社媒采集", rsshub: "RSSHub轻量", youtube: "视频采集", ecommerce: "电商评论", rss: "RSS订阅", v2ex: "社区论坛", article: "文章提取", site_crawler: "整站爬取", firecrawl: "全网发现",
  search: "站定向检索", crawl4ai: "通用引擎", scrapling: "反爬自愈",
  simple_http: "轻量抓取", browser: "浏览器兜底",
};

type CheckResult = { ok: boolean; detail: string };
type DomainStat = { samples: number; success_rate: number };
type SettingsSection = "personal" | "models" | "credentials" | "platform" | "governance" | "diagnostics";

const SETTINGS_SECTIONS: Array<{
  key: SettingsSection;
  label: string;
  description: string;
  icon: typeof UserRound;
  managerOnly?: boolean;
}> = [
  { key: "personal", label: "我的设置", description: "外观与个人任务默认项", icon: UserRound },
  { key: "models", label: "模型与连接", description: "个人 Key 与可用共享连接", icon: Cpu },
  { key: "credentials", label: "采集账号", description: "只作用于自己的平台登录凭证", icon: KeyRound },
  { key: "platform", label: "平台配置", description: "共享服务、采集与通知", icon: SlidersHorizontal, managerOnly: true },
  { key: "governance", label: "扩展工具管理", description: "查看工具状态、验证与开放范围", icon: ShieldCheck, managerOnly: true },
  { key: "diagnostics", label: "运行与诊断", description: "连接器、路由和采集引擎状态", icon: Activity, managerOnly: true },
];

/** 管理员：反爬自动增补面板——查看/手动释放被判定"疑似强反爬"的域名（P1-2，见 _domain_health.py）。
 * 30 分钟内无新失败会自动过期解除；确认是误判可在此立即释放，不必等 TTL。 */
function DomainHealthPanel() {
  const [flagged, setFlagged] = useState<Record<string, DomainStat>>({});
  const [loading, setLoading] = useState(true);
  const [releasing, setReleasing] = useState<string | null>(null);

  const load = () => {
    setLoading(true);
    api.get("/api/config/domain-health")
      .then((d) => setFlagged(d.flagged || {}))
      .catch(() => {})
      .finally(() => setLoading(false));
  };
  useEffect(load, []);

  const release = async (domain: string) => {
    setReleasing(domain);
    try {
      await api.del(`/api/config/domain-health/${encodeURIComponent(domain)}`);
      toast.success(`${domain} 已释放，下次任务重新按 tier 正常路由`);
      load();
    } catch (e: any) {
      toast.error(e.message || "释放失败");
    } finally {
      setReleasing(null);
    }
  };

  const domains = Object.entries(flagged);

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <ShieldAlert className="h-4 w-4 text-primary" /> 反爬自动增补
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          非隐身引擎（article/site_crawler/firecrawl/crawl4ai/simple_http）近期持续失败的域名会被临时短路，优先走
          Camoufox；30 分钟无新失败自动解除，确认是误判可在此立即释放。
        </p>
      </CardHeader>
      <CardContent className="space-y-2">
        {loading ? (
          <p className="py-3 text-center text-sm text-muted-foreground">加载中…</p>
        ) : domains.length === 0 ? (
          <p className="py-3 text-center text-sm text-muted-foreground">当前没有被短路的域名</p>
        ) : (
          domains.map(([domain, stat]) => (
            <div
              key={domain}
              className="flex items-center justify-between gap-2 rounded-md border border-border/60 px-3 py-2 text-sm"
            >
              <div className="min-w-0">
                <div className="truncate font-medium">{domain}</div>
                <div className="text-xs text-muted-foreground">
                  近 {stat.samples} 次成功率 {(stat.success_rate * 100).toFixed(0)}%
                </div>
              </div>
              <Button
                variant="outline" size="sm" disabled={releasing === domain}
                onClick={() => release(domain)} className="h-7 shrink-0 gap-1.5"
              >
                {releasing === domain ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Unlock className="h-3.5 w-3.5" />}
                释放
              </Button>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}


export function Settings() {
  return <SettingsContent />;
}

function AccountSecurity() {
  const { user, changePassword, logoutAll } = useAuth();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [showPasswords, setShowPasswords] = useState(false);
  const [editingPassword, setEditingPassword] = useState(false);
  return (
    <Card>
      <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3 space-y-0 p-4">
        <div className="space-y-1">
        <CardTitle className="text-base">登录与密码</CardTitle>
        <p className="text-xs text-muted-foreground">管理登录安全；退出登录不会停止后台任务。</p>
        </div>
        <Button variant="outline" size="sm" disabled={busy} aria-expanded={editingPassword} aria-controls="password-editor" onClick={() => {
          setEditingPassword(!editingPassword); setCurrentPassword(""); setNewPassword(""); setShowPasswords(false); setError("");
        }}>{editingPassword ? "取消修改" : "修改密码"}</Button>
      </CardHeader>
      <CardContent className="space-y-3 px-4 pb-4 pt-0">
        {editingPassword && <form id="password-editor" className="max-w-2xl space-y-3 rounded-lg border bg-muted/20 p-3" onSubmit={async (event) => {
          event.preventDefault();
          if (busy) return;
          setBusy(true);
          setError("");
          try {
            await changePassword(currentPassword, newPassword);
          } catch (failure) {
            setError(failure instanceof Error ? failure.message : "修改密码失败，请重试");
          } finally {
            setBusy(false);
          }
        }}>
          <input type="hidden" name="username" autoComplete="username" value={user?.username || ""} />
          <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <label htmlFor="current-password" className="text-sm">当前密码</label>
            <Input id="current-password" name="current_password" type={showPasswords ? "text" : "password"} autoComplete="current-password"
              value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} required disabled={busy} />
          </div>
          <div className="space-y-1.5">
            <label htmlFor="new-password" className="text-sm">新密码</label>
            <Input id="new-password" name="new_password" type={showPasswords ? "text" : "password"} autoComplete="new-password" minLength={6} aria-describedby="password-help"
              value={newPassword} onChange={(event) => setNewPassword(event.target.value)} required disabled={busy} />
          </div>
          </div>
          <p id="password-help" className="text-xs text-muted-foreground">新密码至少 6 个字符。</p>
          <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={showPasswords} disabled={busy} onChange={event => setShowPasswords(event.target.checked)} />显示密码</label>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <Button type="submit" disabled={busy || !currentPassword || newPassword.length < 6}>修改密码并退出所有设备</Button>
        </form>}
        {!editingPassword && error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <Button variant="outline" className="min-h-11 border-red-300 bg-red-50 text-red-700 hover:border-red-400 hover:bg-red-100 hover:text-red-800 focus-visible:ring-red-500 dark:border-red-800 dark:bg-red-950/30 dark:text-red-300 dark:hover:bg-red-950/60 dark:hover:text-red-200" disabled={busy} onClick={async () => {
          if (busy || !window.confirm("退出所有设备（包括当前设备）？后台任务会继续运行，你需要重新登录。")) return;
          setBusy(true);
          setError("");
          try {
            await logoutAll();
          } catch (failure) {
            setError(failure instanceof Error ? failure.message : "退出所有设备失败，请重试");
          } finally {
            setBusy(false);
          }
        }}><LogOut aria-hidden="true" />退出所有设备</Button>
      </CardContent>
    </Card>
  );
}

function SettingsContent() {
  const { theme, toggle } = useTheme();
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const manager = isAdminish(user?.role);
  const requestedSection = searchParams.get("section") as SettingsSection | null;
  const allowedSections = SETTINGS_SECTIONS.filter((item) => !item.managerOnly || manager);
  const section = allowedSections.some((item) => item.key === requestedSection)
    ? requestedSection!
    : "personal";
  const currentSection = SETTINGS_SECTIONS.find((item) => item.key === section)!;
  const navigation = useRef<HTMLElement>(null);
  useEffect(() => {
    navigation.current?.querySelector('[aria-current="page"]')?.scrollIntoView({ block: "nearest", inline: "nearest" });
  }, [section]);
  const [ov, setOv] = useState<Overview | null>(null);
  const [testing, setTesting] = useState<string | null>(null); // 正在自检的 target
  const [results, setResults] = useState<Record<string, CheckResult>>({});

  const selectSection = (next: SettingsSection) => {
    const params = new URLSearchParams(searchParams);
    params.set("section", next);
    setSearchParams(params, { replace: true });
  };

  const load = () => {
    api.get("/api/overview").then(setOv).catch(() => {});
  };
  useEffect(load, []);

  const runTest = async (target: string) => {
    setTesting(target);
    try {
      const r: CheckResult = await api.post("/api/settings/selfcheck", { target });
      setResults((m) => ({ ...m, [target]: r }));
      r.ok ? toast.success(r.detail) : toast.error(r.detail);
    } catch (e: any) {
      const detail = e.message || "自检失败";
      setResults((m) => ({ ...m, [target]: { ok: false, detail } }));
      toast.error(detail);
    } finally {
      setTesting(null);
    }
  };

  // target 为 null 表示不可主动自检，仅展示配置状态
  const connectors: {
    key: string; label: string; icon: typeof Sparkles; on?: boolean; hint: string;
    target: string | null;
  }[] = [
    { key: "embedding", label: "知识检索（语义召回）", icon: Sparkles, on: ov?.connectors.embedding, hint: "请求一次 embedding 端点", target: "embedding" },
    { key: "checkpoint", label: "断点续跑 (checkpoint)", icon: Save, on: ov?.connectors.checkpoint, hint: "本地存储，检查存储目录可写", target: "checkpoint" },
  ];

  return (
    <>
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-7 py-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-lg font-semibold tracking-tight">设置</h1>
            <Badge
              variant="outline"
              className={manager ? "border-amber-600/40 text-amber-800 dark:text-amber-200" : undefined}
            >
              {roleLabel(user?.role)}
            </Badge>
          </div>
          <p className="text-sm text-muted-foreground">{currentSection.description}</p>
        </div>
        <div className="ml-auto flex flex-wrap items-center gap-2">
          <PageGuide page={`settings.${section}`} />
          {section === "diagnostics" ? (
            <Button variant="outline" size="sm" onClick={load} className="gap-1.5">
              <RefreshCw className="h-4 w-4" /> 刷新
            </Button>
          ) : null}
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-4 py-5 sm:px-7 sm:py-6">
        <div className="mx-auto max-w-6xl">
          <nav
            ref={navigation}
            aria-label="设置分区"
            className="mb-6 flex gap-2 overflow-x-auto pb-2"
          >
            {allowedSections.map((item) => (
              <button
                key={item.key}
                type="button"
                aria-current={section === item.key ? "page" : undefined}
                onClick={() => selectSection(item.key)}
                className={cn(
                  "flex shrink-0 items-center gap-2 rounded-lg border px-3 py-3 text-left transition focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  section === item.key
                    ? "border-primary bg-primary/5 shadow-sm ring-2 ring-primary/10"
                    : "border-border/70 hover:bg-muted/40",
                )}
              >
                <item.icon className={cn("h-4 w-4 shrink-0", section === item.key ? "text-primary" : "text-muted-foreground")} />
                <span className="min-w-0">
                  <span className="block text-sm font-medium">{item.label}</span>
                </span>
              </button>
            ))}
          </nav>

          <div data-guide="settings-content" className="space-y-5">
            {section === "personal" && (
              <>
                <TaskModelSettings key={user?.user_id} />
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">外观</CardTitle>
                  </CardHeader>
                  <CardContent className="flex items-center justify-between">
                    <div>
                      <div className="text-sm font-medium">主题</div>
                      <div className="text-xs text-muted-foreground">
                        当前：{theme === "dark" ? "深色" : "浅色"}
                      </div>
                    </div>
                    <Button variant="outline" size="sm" onClick={toggle} className="gap-1.5">
                      {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
                      切换为{theme === "dark" ? "浅色" : "深色"}
                    </Button>
                  </CardContent>
                </Card>

                <AccountSecurity />

              </>
            )}

            {section === "models" && <ModelConnectionsPanel isManager={manager} initialScope={searchParams.get("scope") === "personal" ? "personal" : "platform"} />}
            {section === "credentials" && <SelfConfigCenter />}
            {section === "platform" && manager && <AdminConfigCenter />}
            {section === "governance" && manager && <CapabilityGovernancePanel />}

            {section === "diagnostics" && manager && (
              <>
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">平台连接器 / 增强</CardTitle>
                    <p className="text-xs text-muted-foreground">邮件和 Slack 在“平台配置 → 通知”维护，仅按用户明确要求发送。</p>
                  </CardHeader>
                  <CardContent className="space-y-2">
                    {connectors.map((c) => {
                      const res = results[c.key];
                      const busy = testing === c.key;
                      const enabled = ov?.connectors_enabled[c.key as keyof Overview["connectors_enabled"]] ?? false;
                      return (
                        <div
                          key={c.key}
                          className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5 rounded-md border border-border/60 px-3 py-2.5"
                        >
                          <c.icon className="h-4 w-4 text-muted-foreground" />
                          <div className="min-w-0 flex-1">
                            <div className="truncate text-sm">{c.label}</div>
                            <div className="truncate text-[11px] text-muted-foreground">{c.hint}</div>
                          </div>
                          <span className="text-sm">{enabled ? "已启用" : "已停用"}</span>
                          <Link className="inline-flex h-8 items-center rounded-md border px-3 text-xs hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" to={`/settings?section=platform${c.key === "embedding" ? "&service=semantic" : ""}`}>前往平台配置</Link>
                          <Badge variant={c.on ? "success" : "outline"}>{c.on ? "已配置" : "未配"}</Badge>
                          {c.target && (
                            <Button
                              variant="outline"
                              size="sm"
                              disabled={busy}
                              onClick={() => runTest(c.target!)}
                              className="h-7 gap-1.5"
                            >
                              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Play className="h-3.5 w-3.5" />}
                              测试
                            </Button>
                          )}
                          {res && (
                            <div
                              className={cn(
                                "flex w-full items-center gap-1.5 text-xs",
                                res.ok ? "text-emerald-500" : "text-destructive",
                              )}
                            >
                              {res.ok ? <CheckCircle2 className="h-3.5 w-3.5" /> : <XCircle className="h-3.5 w-3.5" />}
                              {res.detail}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </CardContent>
                </Card>

                <DomainHealthPanel />

                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base">
                      <Boxes className="h-4 w-4 text-primary" /> 采集引擎（分层路由）
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
                    {(ov?.collectors ?? []).map((c) => (
                      <div
                        key={c.name}
                        className="flex items-center justify-between rounded-md border border-border/60 px-3 py-2"
                      >
                        <div className="min-w-0">
                          <div className="truncate text-sm font-medium">{COLLECTOR_CN[c.name] || c.name}</div>
                          <div className="truncate text-[11px] text-muted-foreground">{c.name} · t{c.tier}</div>
                        </div>
                        <CircleDot
                          className={cn(
                            "h-3.5 w-3.5 shrink-0",
                            c.available ? "text-emerald-500" : "text-muted-foreground/40",
                          )}
                        />
                      </div>
                    ))}
                  </CardContent>
                </Card>
              </>
            )}
          </div>
        </div>
      </div>

    </>
  );
}
