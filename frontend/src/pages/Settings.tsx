import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Boxes, Cpu, Mail, Slack, Sparkles, Save, Moon, Sun, CircleDot, RefreshCw,
  Play, Loader2, CheckCircle2, XCircle, ShieldAlert, Unlock, UserRound,
  KeyRound, SlidersHorizontal, Activity, ShieldCheck,
} from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { api } from "@/lib/api";
import { useAuth, isAdminish, roleLabel } from "@/lib/auth";
import { AdminConfigCenter, SelfConfigCenter } from "@/components/ConfigCenter";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";
import { ModelConnectionsPanel } from "@/pages/settings/ModelConnectionsPanel";
import { CapabilityGovernancePanel } from "@/pages/settings/CapabilityGovernancePanel";

interface Overview {
  collectors: { name: string; tier: number; available: boolean }[];
  scheduler: { enabled: boolean; active_count: number };
  connectors: { email: boolean; slack: boolean; embedding: boolean; checkpoint: boolean };
  connectors_enabled: { email: boolean; slack: boolean; embedding: boolean; checkpoint: boolean };
}
interface ModelOption {
  provider: string;
  model: string;
  label: string;
}
interface Models {
  options: ModelOption[];
  available: string[];
  default: ModelOption | null;
  document_default: ModelOption | null;
  document_default_source: "user" | "global";
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
  { key: "platform", label: "平台配置", description: "全局运行配置与旧流程兼容", icon: SlidersHorizontal, managerOnly: true },
  { key: "governance", label: "能力治理", description: "能力版本的三轴治理状态", icon: ShieldCheck, managerOnly: true },
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

/**
 * 极简开关：只在这一处用，不引入新的 UI 依赖。非管理员点了也会因后端 403 无效，直接禁用更直白。
 * 上一版关闭态用 bg-muted 配 bg-background 的圆点，两者在浅色主题下亮度都接近 95%，
 * 轨道和圆点几乎融成一片、看不出是开关——这版改成轨道加边框 + 圆点用纯白/纯色，拉开对比度。
 */
function Toggle({ checked, disabled, title, onChange }: {
  checked: boolean; disabled?: boolean; title?: string; onChange: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      title={title}
      onClick={onChange}
      className={cn(
        "inline-flex h-5 w-9 shrink-0 items-center rounded-full border transition-colors",
        checked ? "border-primary bg-primary" : "border-border bg-muted-foreground/25",
        disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer",
      )}
    >
      <span
        className={cn(
          "h-4 w-4 rounded-full bg-white shadow-sm ring-1 ring-black/5 transition-transform",
          checked ? "translate-x-4" : "translate-x-0.5",
        )}
      />
    </button>
  );
}

export function Settings() {
  return <SettingsContent />;
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
  const [ov, setOv] = useState<Overview | null>(null);
  const [models, setModels] = useState<Models | null>(null);
  const [documentModelRef, setDocumentModelRef] = useState("");
  const [savingDocumentModel, setSavingDocumentModel] = useState(false);
  const [testing, setTesting] = useState<string | null>(null); // 正在自检的 target
  const [results, setResults] = useState<Record<string, CheckResult>>({});
  const [slackConfirm, setSlackConfirm] = useState(false); // Slack 自检会发消息，先确认
  const [togglingKey, setTogglingKey] = useState<string | null>(null); // 正在切换开关的 connector.key

  const selectSection = (next: SettingsSection) => {
    const params = new URLSearchParams(searchParams);
    params.set("section", next);
    setSearchParams(params, { replace: true });
  };

  const load = () => {
    api.get("/api/overview").then(setOv).catch(() => {});
    api.get("/api/models").then((data: Models) => {
      setModels(data);
      setDocumentModelRef(
        data.document_default
          ? `${data.document_default.provider}::${data.document_default.model}`
          : "",
      );
    }).catch(() => {});
  };
  useEffect(load, []);

  /** 切换"是否启用该服务"（不影响已保存的凭证）。仅管理员/超管可调用，其余角色 Toggle 已禁用。 */
  const toggleConnector = async (registryKey: string, connectorKey: string, next: boolean) => {
    setTogglingKey(connectorKey);
    try {
      await api.put(`/api/config/${registryKey}`, { value: next ? "True" : "False" });
      toast.success(next ? "已启用" : "已停用");
      load();
    } catch (e: any) {
      toast.error(e.message || "切换失败");
    } finally {
      setTogglingKey(null);
    }
  };

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

  const saveDocumentModel = async () => {
    if (!documentModelRef) return;
    setSavingDocumentModel(true);
    try {
      await api.put("/api/config/self/document_extraction_model", {
        value: documentModelRef,
      });
      toast.success("文档抽取默认模型已保存到当前用户");
      window.dispatchEvent(new CustomEvent("mangrove:config-changed"));
      load();
    } catch (e: any) {
      toast.error(e.message || "保存失败");
    } finally {
      setSavingDocumentModel(false);
    }
  };

  const resetDocumentModel = async () => {
    setSavingDocumentModel(true);
    try {
      await api.del("/api/config/self/document_extraction_model");
      toast.success("已恢复管理员或 .env 中的文档抽取默认模型");
      window.dispatchEvent(new CustomEvent("mangrove:config-changed"));
      load();
    } catch (e: any) {
      toast.error(e.message || "恢复失败");
    } finally {
      setSavingDocumentModel(false);
    }
  };

  // target 为 null 表示不可主动自检，仅展示配置状态（目前 4 项都有对应自检目标）
  // enabledKey：对应配置中心的开关字段（管理员可用它临时启停服务，不影响已保存的凭证）
  const connectors: {
    key: string; label: string; icon: typeof Mail; on?: boolean; hint: string;
    target: string | null; sideEffect?: boolean; enabledKey: string;
  }[] = [
    { key: "email", label: "邮件 (SMTP)", icon: Mail, on: ov?.connectors.email, hint: "连接并登录，不发送邮件", target: "email", enabledKey: "smtp_enabled" },
    { key: "slack", label: "Slack", icon: Slack, on: ov?.connectors.slack, hint: "向频道发送一条测试消息", target: "slack", sideEffect: true, enabledKey: "slack_enabled" },
    { key: "embedding", label: "语义召回 (embedding)", icon: Sparkles, on: ov?.connectors.embedding, hint: "请求一次 embedding 端点", target: "embedding", enabledKey: "embedding_enabled" },
    { key: "checkpoint", label: "断点续跑 (checkpoint)", icon: Save, on: ov?.connectors.checkpoint, hint: "本地存储，检查存储目录可写", target: "checkpoint", enabledKey: "checkpoint_enabled" },
  ];

  return (
    <>
      <header className="flex items-center justify-between border-b border-border px-7 py-4">
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
        {section === "personal" || section === "diagnostics" ? (
          <Button variant="outline" size="sm" onClick={load} className="gap-1.5">
            <RefreshCw className="h-4 w-4" /> 刷新
          </Button>
        ) : null}
      </header>

      <div className="flex-1 overflow-y-auto px-7 py-6">
        <div className="mx-auto max-w-6xl">
          <nav
            role="tablist"
            aria-label="设置分区"
            className="mb-6 grid gap-2 sm:grid-cols-2 xl:grid-cols-6"
          >
            {allowedSections.map((item) => (
              <button
                key={item.key}
                type="button"
                role="tab"
                aria-selected={section === item.key}
                onClick={() => selectSection(item.key)}
                className={cn(
                  "flex items-center gap-3 rounded-xl border px-3 py-3 text-left transition",
                  section === item.key
                    ? "border-primary bg-primary/5 shadow-sm ring-2 ring-primary/10"
                    : "border-border/70 hover:bg-muted/40",
                )}
              >
                <item.icon className={cn("h-4 w-4 shrink-0", section === item.key ? "text-primary" : "text-muted-foreground")} />
                <span className="min-w-0">
                  <span className="block text-sm font-medium">{item.label}</span>
                  <span className={cn(
                    "block truncate text-xs",
                    section === item.key ? "text-foreground/75" : "text-muted-foreground",
                  )}>
                    {item.description}
                  </span>
                </span>
              </button>
            ))}
          </nav>

          <div className="space-y-5">
            {section === "personal" && (
              <>
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

                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base">
                      <Cpu className="h-4 w-4 text-primary" /> 个人任务默认项
                    </CardTitle>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="flex items-center justify-between text-sm">
                      <span className="text-muted-foreground">旧对话流程默认模型</span>
                      <span className="font-medium">{models?.default?.label || "—"}</span>
                    </div>
                    <div className="space-y-2 rounded-md border border-border/60 p-3">
                      <div>
                        <div className="text-sm font-medium">文档抽取默认模型</div>
                        <div className="text-xs text-muted-foreground">
                          当前用户可覆盖平台默认；文档工作区仍可按单个任务临时切换。
                        </div>
                      </div>
                      <div className="flex flex-wrap gap-2">
                        <select
                          aria-label="文档抽取默认模型"
                          value={documentModelRef}
                          onChange={(event) => setDocumentModelRef(event.target.value)}
                          className="h-9 min-w-0 flex-1 rounded-md border bg-background px-3 text-sm"
                        >
                          {(models?.options ?? []).map((option) => (
                            <option
                              key={`${option.provider}::${option.model}`}
                              value={`${option.provider}::${option.model}`}
                            >
                              {option.label}
                            </option>
                          ))}
                        </select>
                        <Button
                          size="sm"
                          disabled={!documentModelRef || savingDocumentModel}
                          onClick={saveDocumentModel}
                          className="bg-teal-700 text-white hover:bg-teal-800"
                        >
                          {savingDocumentModel ? <Loader2 className="h-4 w-4 animate-spin" /> : "保存"}
                        </Button>
                        {models?.document_default_source === "user" && (
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={savingDocumentModel}
                            onClick={resetDocumentModel}
                          >
                            恢复平台默认
                          </Button>
                        )}
                      </div>
                      <p className="text-xs text-amber-700 dark:text-amber-300">
                        选择云模型时，解析后的文档文本会发送到对应 Provider；任务执行前仍会展示外发摘要。
                      </p>
                    </div>
                  </CardContent>
                </Card>

                <CapabilityGovernancePanel ownerOnly />
              </>
            )}

            {section === "models" && <ModelConnectionsPanel isManager={manager} />}
            {section === "credentials" && <SelfConfigCenter />}
            {section === "platform" && manager && <AdminConfigCenter />}
            {section === "governance" && manager && <CapabilityGovernancePanel />}

            {section === "diagnostics" && manager && (
              <>
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">平台连接器 / 增强</CardTitle>
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
                          <Toggle
                            checked={enabled}
                            disabled={togglingKey === c.key || !ov}
                            title={enabled ? "点击停用" : "点击启用"}
                            onChange={() => toggleConnector(c.enabledKey, c.key, !enabled)}
                          />
                          <Badge variant={c.on ? "success" : "outline"}>{c.on ? "已配置" : "未配"}</Badge>
                          {c.target && (
                            <Button
                              variant="outline"
                              size="sm"
                              disabled={busy}
                              onClick={() => (c.sideEffect ? setSlackConfirm(true) : runTest(c.target!))}
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

      {/* Slack 自检确认（会向频道发送测试消息） */}
      <Modal open={slackConfirm} onClose={() => setSlackConfirm(false)} title="测试 Slack 连接">
        <p className="text-sm text-muted-foreground">
          自检会向 Webhook 绑定的频道<strong className="text-foreground">发送一条测试消息</strong>，频道成员可见。确定继续？
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setSlackConfirm(false)}>
            取消
          </Button>
          <Button
            size="sm"
            onClick={() => {
              setSlackConfirm(false);
              runTest("slack");
            }}
          >
            发送测试消息
          </Button>
        </div>
      </Modal>
    </>
  );
}
