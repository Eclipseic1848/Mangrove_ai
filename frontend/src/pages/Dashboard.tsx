import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Boxes, CalendarClock, Library, MessagesSquare, Cpu, Mail, Slack, Sparkles, Save, ArrowRight, CircleDot, Search,
  Shield, Globe, ChevronRight, Brain,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { api } from "@/lib/api";
import { useAuth, isAdminish } from "@/lib/auth";
import { cn } from "@/lib/utils";

interface Overview {
  collectors: { name: string; tier: number; available: boolean }[];
  providers: { available: string[]; catalog: Record<string, string[]> };
  scheduler: { enabled: boolean; active_count: number };
  templates: { total: number; active: number; draft: number; retired: number };
  conversations: number;
  connectors: { email: boolean; slack: boolean; embedding: boolean; checkpoint: boolean };
  connectors_enabled: { email: boolean; slack: boolean; embedding: boolean; checkpoint: boolean };
  search_health?: {
    backends: string[];
    backends_detail?: { name: string; label: string; available: boolean; type: string }[];
    has_html_fallback: boolean; status: "good" | "degraded";
  };
  collector_metrics?: Record<string, { calls: number; success_rate: number; avg_ms: number }>;
  cookie_status?: Record<string, boolean>;
  extraction_chain?: { name: string; label: string; available: boolean }[];
  network?: { rate_limiting: string; ua_rotation: boolean; smart_routing: boolean; retry: string };
  anti_scrape?: { camoufox: boolean; shortcut_domains: number };
  memory_hit_stats?: {
    hit_type: string;
    count: number; // 总召回尝试次数（含未命中，E3 起语义变更，不等于命中数）
    hit_count: number; // 真正命中次数（E3 新增）
    semantic_count: number; // degrade_path=semantic 的尝试次数（含语义召回后被筛空的未命中）
    keyword_count: number; // degrade_path=keyword 的尝试次数
    avg_threshold: number; // 仅命中记录的平均置信度
  }[];
}

const COOKIE_CN: Record<string, string> = {
  xiaohongshu: "小红书", weibo: "微博", douyin: "抖音", bilibili: "B站", zhihu: "知乎",
  kuaishou: "快手", tieba: "贴吧", jd: "京东", taobao: "淘宝", pdd: "拼多多",
};

const COLLECTOR_CN: Record<string, string> = {
  mediacrawler: "社媒采集", rsshub: "RSSHub轻量", youtube: "视频采集", ecommerce: "电商评论", rss: "RSS订阅", v2ex: "社区论坛", article: "文章提取", site_crawler: "整站爬取", firecrawl: "全网发现",
  search: "站定向检索", crawl4ai: "通用引擎", scrapling: "反爬自愈",
  simple_http: "轻量抓取", browser: "浏览器兜底",
};

/** 拆分"名称（细节）"格式的标签为主/次两段；无括号则细节为空。 */
function splitLabel(label: string): [string, string | null] {
  const m = label.match(/^(.+?)[（(](.+)[）)]\s*$/);
  return m ? [m[1].trim(), m[2].trim()] : [label, null];
}

/** 状态胶囊：名称用正常字号深色，细节（括号内容）降一级为更小更淡的辅助文字，避免同级字号堆在一起造成视觉疲劳。 */
function StatusChip({ label, available }: { label: string; available: boolean }) {
  const [primary, secondary] = splitLabel(label);
  return (
    <div className="flex items-center gap-1.5 text-sm">
      <CircleDot className={cn("h-3 w-3 shrink-0", available ? "text-emerald-500" : "text-muted-foreground/40")} />
      <span className={cn(!available && "text-muted-foreground")}>{primary}</span>
      {secondary && <span className="text-xs text-muted-foreground/70">{secondary}</span>}
    </div>
  );
}

/** 模型供应商展示：各家官方 logo；本地模型为纯色标，深色模式下反色，其余保留原色不处理。 */
const PROVIDER_META: Record<string, { label: string; logo?: string; imgClass?: string; initial?: string; badgeClass?: string }> = {
  deepseek: { label: "DeepSeek", logo: "/logos/deepseek.png" },
  qwen: { label: "通义千问", logo: "/logos/qwen.png" },
  local: { label: "本地模型", logo: "/howso-logo-mark.png", imgClass: "dark:brightness-0 dark:invert" },
};

/** 平台登录态展示：官方 logo，未接入登录态时降为灰阶+半透明，与状态点呼应。 */
const COOKIE_LOGO: Record<string, string> = {
  xiaohongshu: "/logos/xiaohongshu.png", weibo: "/logos/weibo.png", douyin: "/logos/douyin.png",
  bilibili: "/logos/bilibili.png", zhihu: "/logos/zhihu.png", kuaishou: "/logos/kuaishou.png",
  tieba: "/logos/tieba.png", jd: "/logos/jd.png", taobao: "/logos/taobao.png", pdd: "/logos/pdd.png",
};

export function Dashboard() {
  const [data, setData] = useState<Overview | null>(null);
  const navigate = useNavigate();
  const { user } = useAuth();
  const isAdmin = isAdminish(user?.role);

  useEffect(() => {
    api.get("/api/overview").then(setData).catch(() => {});
  }, []);

  // to=跳转路由；anchor=页内锚点（采集器无独立页，滚动到下方"采集引擎"区块）
  const stats = [
    { label: "我的会话", value: data?.conversations ?? "—", icon: MessagesSquare, to: "/chat" },
    { label: "进行中定时任务", value: data?.scheduler.active_count ?? "—", icon: CalendarClock, to: "/tasks" },
    { label: "可用采集器", value: data ? data.collectors.filter((c) => c.available).length : "—", icon: Boxes, anchor: "collectors-section" },
    { label: "分析模板", value: data?.templates.total ?? "—", icon: Library, to: "/templates" },
  ] as { label: string; value: any; icon: any; to?: string; anchor?: string }[];

  const onStatClick = (s: { to?: string; anchor?: string }) => {
    if (s.to) navigate(s.to);
    else if (s.anchor) document.getElementById(s.anchor)?.scrollIntoView({ behavior: "smooth" });
  };

  // configured：是否已填凭证；enabled：管理员开关是否打开。两者都满足才算"真的在生效"，
  // 否则"已配置但被停用"会被误显示成"已启用"（这正是本卡片标签文案本身承诺的含义）。
  const connectors = [
    { key: "email", label: "邮件", icon: Mail, configured: data?.connectors.email, enabled: data?.connectors_enabled.email },
    { key: "slack", label: "Slack", icon: Slack, configured: data?.connectors.slack, enabled: data?.connectors_enabled.slack },
    { key: "embedding", label: "语义召回", icon: Sparkles, configured: data?.connectors.embedding, enabled: data?.connectors_enabled.embedding },
    { key: "checkpoint", label: "断点续跑", icon: Save, configured: data?.connectors.checkpoint, enabled: data?.connectors_enabled.checkpoint },
  ];

  return (
    <>
      <header className="flex items-center justify-between gap-6 border-b border-border px-7 py-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">概览</h1>
          <p className="text-sm text-muted-foreground">智能体能力与运行状态一览</p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Button variant="outline" onClick={() => navigate("/chat")}>
            发起采集对话
          </Button>
          <Button onClick={() => navigate("/data-prep")} className="gap-1.5">
            创建数据任务 <ArrowRight className="h-4 w-4" />
          </Button>
        </div>
      </header>

      <div className="flex-1 space-y-6 overflow-y-auto px-7 py-6">
        {/* 统计卡 */}
        <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
          {stats.map((s) => (
            <Card
              key={s.label}
              className="animate-fade-in cursor-pointer transition-colors hover:border-primary/50 hover:bg-primary/[0.03]"
              role="button"
              tabIndex={0}
              onClick={() => onStatClick(s)}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && onStatClick(s)}
            >
              <CardContent className="flex items-center gap-4 p-5">
                <div className="flex h-11 w-11 items-center justify-center rounded-lg bg-primary/12 text-primary">
                  <s.icon className="h-5 w-5" />
                </div>
                <div>
                  <div className="text-2xl font-semibold tabular-nums">{s.value}</div>
                  <div className="text-xs text-muted-foreground">{s.label}</div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>

        {/* 采集引擎（全宽） */}
        <Card id="collectors-section">
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <Boxes className="h-4 w-4 text-primary" /> 采集引擎（分层路由 · {(data?.collectors ?? []).length}个引擎 · tier 升序优先）
            </CardTitle>
          </CardHeader>
          <CardContent className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6">
            {(data?.collectors ?? []).map((c) => (
              <div
                key={c.name}
                className="flex items-center justify-between rounded-md border border-border/60 px-3 py-2"
              >
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">{COLLECTOR_CN[c.name] || c.name}</div>
                  <div className="truncate text-[11px] text-muted-foreground">{c.name} · t{c.tier}</div>
                </div>
                <CircleDot
                  className={cn("h-3.5 w-3.5 shrink-0", c.available ? "text-emerald-500" : "text-muted-foreground/40")}
                />
              </div>
            ))}
            {/* 采集器指标（有调用数据时展示） */}
            {data?.collector_metrics && Object.keys(data.collector_metrics).length > 0 && (
              <div className="col-span-full mt-2 border-t border-border/40 pt-2">
                <div className="text-[11px] text-muted-foreground mb-1">运行指标（当前进程累计）</div>
                <div className="grid grid-cols-3 sm:grid-cols-5 gap-1">
                  {Object.entries(data.collector_metrics).map(([name, m]) => (
                    <div key={name} className="flex items-center justify-between text-[11px] px-1">
                      <span className="text-muted-foreground truncate">{COLLECTOR_CN[name] || name}</span>
                      <span className={cn("tabular-nums", m.success_rate >= 0.5 ? "text-emerald-500" : "text-amber-500")}>
                        {Math.round(m.success_rate * 100)}% · {Math.round(m.avg_ms)}ms
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </CardContent>
        </Card>

        {/* 搜索后端 + 正文提取链 · 反爬加固 + 网络层：合并为等高两列，横向排布压缩高度 */}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {/* 搜索后端 + 正文提取链 */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Search className="h-4 w-4 text-primary" /> 搜索后端 · 正文提取
                {data?.search_health && (
                  <Badge variant={data.search_health.status === "good" ? "success" : "warning"}>
                    {data.search_health.status === "good" ? "健康" : "降级"}
                  </Badge>
                )}
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div>
                <div className="text-xs text-muted-foreground mb-1.5">搜索后端（{(data?.search_health?.backends_detail ?? []).length}个）</div>
                <div className="flex flex-wrap gap-x-5 gap-y-2">
                  {(data?.search_health?.backends_detail ?? []).length > 0 ? (
                    data!.search_health!.backends_detail!.map((b) => (
                      <StatusChip key={b.name} label={b.label} available={b.available} />
                    ))
                  ) : (
                    (data?.search_health?.backends ?? []).map((b: string) => (
                      <StatusChip
                        key={b}
                        available
                        label={b === "anysearch" ? "AnySearch" : b === "searxng" ? "SearXNG" : b === "tavily" ? "Tavily" : b === "ddg" ? "DuckDuckGo" : b}
                      />
                    ))
                  )}
                </div>
              </div>
              <div className="border-t border-border/40 pt-3">
                <div className="text-xs text-muted-foreground mb-1.5">正文提取链（3级级联，命中即停）</div>
                <div className="flex flex-wrap items-center gap-x-2 gap-y-2">
                  {(data?.extraction_chain ?? [
                    { name: "trafilatura", label: "Trafilatura", available: false },
                    { name: "readability", label: "readability-lxml", available: false },
                    { name: "html_to_text", label: "html_to_text", available: true },
                  ]).map((ex, i) => (
                    <span key={ex.name} className="flex items-center gap-2">
                      {i > 0 && <ChevronRight className="h-3.5 w-3.5 text-muted-foreground/40" />}
                      <StatusChip label={ex.label} available={ex.available} />
                    </span>
                  ))}
                </div>
              </div>
            </CardContent>
          </Card>

          {/* 反爬加固 + 网络层 */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Shield className="h-4 w-4 text-primary" /> 反爬加固 · 网络层
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div>
                <div className="text-xs text-muted-foreground mb-1.5">反爬加固</div>
                <div className="flex flex-wrap gap-x-5 gap-y-2">
                  <StatusChip label="Camoufox 隐身" available={!!data?.anti_scrape?.camoufox} />
                  <StatusChip label={`短路（${data?.anti_scrape?.shortcut_domains ?? 10}域名）`} available />
                  <StatusChip label="UA 轮换" available={!!data?.network?.ua_rotation} />
                </div>
              </div>
              <div className="border-t border-border/40 pt-3">
                <div className="text-xs text-muted-foreground mb-1.5">网络层</div>
                <div className="flex flex-wrap gap-x-5 gap-y-2">
                  <StatusChip label={`限速（${data?.network?.rate_limiting ?? "1s~3s"}）`} available />
                  <StatusChip label={`退避重试（${data?.network?.retry ?? "≤2次"}）`} available />
                  <StatusChip label="智能分流" available={!!data?.network?.smart_routing} />
                </div>
                <div className="text-xs text-muted-foreground pt-2">
                  国内直连 · 境外走代理 · LAN 绕过系统代理
                </div>
              </div>
            </CardContent>
          </Card>
        </div>

        {/* 模型 + 连接器 + 登录态 */}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Cpu className="h-4 w-4 text-primary" /> 模型供应商
              </CardTitle>
            </CardHeader>
            <CardContent className="flex flex-wrap gap-2">
              {(data?.providers.available ?? []).length ? (
                data!.providers.available.map((p) => {
                  const meta = PROVIDER_META[p];
                  return (
                    <span key={p} className="inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/30 px-2.5 py-1 text-xs font-medium">
                      {meta?.logo ? (
                        <img src={meta.logo} alt={meta.label} className={cn("h-4 w-4 shrink-0 rounded object-contain", meta.imgClass)} />
                      ) : meta?.initial ? (
                        <span className={cn("flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[9px] font-bold", meta.badgeClass)}>
                          {meta.initial}
                        </span>
                      ) : null}
                      {meta?.label ?? p}
                    </span>
                  );
                })
              ) : (
                <span className="text-sm text-muted-foreground">仅本地模型可用</span>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Globe className="h-4 w-4 text-primary" /> 平台登录态
              </CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-3 gap-1.5">
              {(data?.cookie_status && Object.entries(data.cookie_status).length > 0
                ? Object.entries(data.cookie_status).map(([k, v]) => (
                    <div key={k} className="flex items-center gap-1.5 text-sm">
                      <CircleDot className={cn("h-3 w-3 shrink-0", v ? "text-emerald-500" : "text-muted-foreground/40")} />
                      {COOKIE_LOGO[k] && (
                        <img
                          src={COOKIE_LOGO[k]}
                          alt=""
                          className={cn("h-4 w-4 shrink-0 rounded object-contain", !v && "opacity-40 grayscale")}
                        />
                      )}
                      <span className={cn(v ? "text-foreground" : "text-muted-foreground")}>{COOKIE_CN[k] || k}</span>
                    </div>
                  ))
                : <span className="text-sm text-muted-foreground">加载中…</span>)}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">连接器 / 增强</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-2">
              {connectors.map((c) => {
                const variant = c.enabled ? "success" : c.configured ? "warning" : "outline";
                const text = c.enabled ? "已启用" : c.configured ? "已停用" : "未配";
                return (
                  <div key={c.key} className="flex items-center gap-2 rounded-md border border-border/60 px-2.5 py-2">
                    <c.icon className="h-4 w-4 text-muted-foreground" />
                    <span className="flex-1 text-sm">{c.label}</span>
                    <Badge variant={variant}>{text}</Badge>
                  </div>
                );
              })}
            </CardContent>
          </Card>
        </div>

        {/* 记忆系统命中统计（方案 D 评估闭环） */}
        {isAdmin && <MemoryHitCard stats={data?.memory_hit_stats ?? []} />}
      </div>
    </>
  );
}

/** 记忆系统命中统计卡片：展示教训/模板命中次数、语义召回率、降级率，量化记忆有没有在起作用。 */
function MemoryHitCard({ stats }: { stats: NonNullable<Overview["memory_hit_stats"]> }) {
  const lesson = stats.find((s) => s.hit_type === "lesson");
  const total = lesson?.count ?? 0; // 总召回尝试次数（命中+未命中）
  const hitCount = lesson?.hit_count ?? 0;
  const semantic = lesson?.semantic_count ?? 0;
  const keyword = lesson?.keyword_count ?? 0;
  const avgThreshold = lesson?.avg_threshold ?? 0;
  const hitRate = total > 0 ? Math.round((hitCount / total) * 100) : 0;
  const semanticRate = total > 0 ? Math.round((semantic / total) * 100) : 0;
  const keywordRate = total > 0 ? Math.round((keyword / total) * 100) : 0;

  const health = (() => {
    if (total === 0) return { label: "暂无记录", variant: "outline" as const };
    if (hitRate >= 50) return { label: "良好", variant: "success" as const };
    if (hitRate >= 20) return { label: "一般", variant: "warning" as const };
    return { label: "建议检查召回配置", variant: "danger" as const };
  })();

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-base">
          <Brain className="h-4 w-4 text-primary" /> 记忆系统 · 命中统计
        </CardTitle>
      </CardHeader>
      <CardContent>
        {total === 0 ? (
          <p className="text-sm text-muted-foreground">
            暂无记录。教训库在任务被判定采集失败时自动沉淀，累计 2 次失败且至少 1 次帮到后续任务后转正开始注入。
          </p>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-4 text-sm">
                <span>
                  尝试 <span className="font-semibold text-foreground">{total}</span> 次，命中{" "}
                  <span className="font-semibold text-foreground">{hitCount}</span> 次
                </span>
                <span className="text-muted-foreground">命中平均置信度 {avgThreshold.toFixed(2)}</span>
              </div>
              <Badge variant={health.variant}>{health.label}</Badge>
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">命中率</span>
                <span className="text-foreground">{hitCount} / {total} ({hitRate}%)</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full bg-emerald-500" style={{ width: `${hitRate}%` }} />
              </div>
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">语义召回路径</span>
                <span className="text-foreground">{semantic} 次 ({semanticRate}%)</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full bg-sky-500" style={{ width: `${semanticRate}%` }} />
              </div>
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center justify-between text-xs">
                <span className="text-muted-foreground">关键词兜底路径</span>
                <span className="text-foreground">{keyword} 次 ({keywordRate}%)</span>
              </div>
              <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
                <div className="h-full rounded-full bg-amber-500" style={{ width: `${keywordRate}%` }} />
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
