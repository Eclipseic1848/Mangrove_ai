import { useEffect, useRef, useState, type ReactNode } from "react";
import { PageGuide } from "@/components/onboarding/PageGuide";
import { beijingTime } from "@/lib/beijingTime";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { SiOpenai } from "@icons-pack/react-simple-icons";
import { Activity, ArrowRight, BookOpen, CalendarClock, CheckCircle2, CircleDot, Cpu, Globe, Mail, Plus, RefreshCw, Search, Server, Slack, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Pagination } from "@/components/ui/pagination";
import { api } from "@/lib/api";
import { useAuth, isAdminish } from "@/lib/auth";
import { cn } from "@/lib/utils";
import { taskModelChoices, type TaskModelConnection } from "@/lib/taskModelChoices";
import { describeTrigger, type ScheduledTask } from "@/lib/scheduleSummary";

type Filter = "all" | "active" | "attention" | "completed";
type ActivitySummary = {
  stats: Record<Exclude<Filter, "all">, number>; total: number; updated_at: string;
  items: { id: string; kind: "task" | "conversation"; title: string; status: string; updated_at: string }[];
};
type ServiceSummary = {
  cookies: { platform: string; status: string; checked_at: string | null }[];
  services: { key: string; label: string; configured: boolean; enabled: boolean }[];
  scheduler_enabled: boolean;
};
type Connection = TaskModelConnection & { has_key?: boolean };
type Preset = { preset_id: string; display_name: string };
const PROVIDERS: Record<string, { label: string; file?: string }> = {
  deepseek: { label: "DeepSeek", file: "deepseek.png" }, qwen: { label: "通义千问", file: "qwen.png" },
  openai: { label: "OpenAI" }, anthropic: { label: "Claude", file: "claude-color.png" },
  gemini: { label: "Gemini", file: "gemini-color.png" }, kimi: { label: "Kimi", file: "kimi-color.png" },
  zhipu: { label: "智谱", file: "zhipu-color.png" }, xai: { label: "Grok", file: "grok.png" },
};
const PLATFORMS: Record<string, string> = { xiaohongshu: "小红书", weibo: "微博", douyin: "抖音", bilibili: "B站", zhihu: "知乎", kuaishou: "快手", tieba: "贴吧", jd: "京东", taobao: "淘宝", pdd: "拼多多" };
// 品牌图片是可选本机资源，公开构建缺少图片时保留通用图标与服务名称。
const BRAND_FILES = new Set(Object.keys(import.meta.glob("/public/overview-brands/*.png")).map(path => path.split("/").pop()));
const STATUS: Record<string, string> = { running: "执行中", queued: "排队中", pausing: "正在暂停", paused: "已暂停", needs_input: "需要确认", candidate_ready: "待验证", cancelling: "正在停止", cancelled: "已停止", failed: "失败", completed: "已完成" };
const FILTERS: Record<Filter, string> = { all: "全部任务", active: "进行中", attention: "待处理", completed: "近7天完成" };
const PAGE_SIZES = [10, 20, 50, 100];
const LINK = "inline-flex min-h-9 items-center gap-1 rounded text-sm text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const PANEL = "min-w-0 rounded-xl border border-border bg-card";

function readOverview(path: string, signal: AbortSignal) {
  return api.get(path, { signal: AbortSignal.any([signal, AbortSignal.timeout(15000)]) });
}

function dateLabel(value?: string | null) {
  if (!value) return "尚无记录";
  return beijingTime(value);
}

function LoadState({ loading, error, stale, retry, label }: { loading: boolean; error: boolean; stale: boolean; retry: () => void; label: string }) {
  if (error) return <div role="alert" className="flex flex-wrap items-center gap-2 rounded-lg bg-amber-50 p-3 text-sm text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">{label}加载失败{stale ? "，以下为上次数据" : "，状态未知"}<Button variant="outline" size="sm" onClick={retry}>重试</Button></div>;
  if (loading) return <p role="status" className="py-4 text-sm text-muted-foreground">正在加载{label}…</p>;
  return null;
}

function ServiceCard({ title, icon, subtitle, footer, children }: { title: string; icon: ReactNode; subtitle: ReactNode; footer: ReactNode; children: ReactNode }) {
  return <section aria-label={title} className={cn(PANEL, "flex flex-col p-5")}>
    <h3 className="flex items-center gap-2 text-base font-semibold">{icon}{title}</h3>
    <div className="mb-4 mt-2 min-h-10 text-xs leading-5 text-muted-foreground" data-service-subtitle>{subtitle}</div>
    <div className="min-h-[208px] flex-1" data-service-body>{children}</div>
    <div className="mt-4 flex min-h-11 items-center justify-between gap-2 border-t border-border pt-2 text-xs text-muted-foreground" data-service-footer>{footer}</div>
  </section>;
}

export function Dashboard() {
  const { user } = useAuth();
  const manager = isAdminish(user?.role);
  const [params, setParams] = useSearchParams();
  const rawFilter = params.get("filter") || "all";
  const filter: Filter = Object.prototype.hasOwnProperty.call(FILTERS, rawFilter) ? rawFilter as Filter : "all";
  const schedulesView = params.get("view") === "schedules";
  const rawPage = Number(params.get("page") || 1);
  const page = Number.isSafeInteger(rawPage) && rawPage > 0 && rawPage < 1000000 ? rawPage : 1;
  const rawSize = Number(params.get("page_size"));
  const pageSize = PAGE_SIZES.includes(rawSize) ? rawSize : 10;
  const listRef = useRef<HTMLElement>(null);
  const [expandedProviders, setExpandedProviders] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const owner = [user?.user_id, user?.role];
  const activity = useQuery<ActivitySummary>({ queryKey: ["overview-activity", ...owner, filter, page, pageSize], queryFn: ({ signal }) => readOverview(`/api/overview/activity?filter=${filter}&offset=${(page - 1) * pageSize}&limit=${pageSize}`, signal),
    // 仅保留同账号、同筛选的总数，避免翻页等待时显示“2 / 1 页”或混入其他范围数据。
    placeholderData: (previous, query) => query?.queryKey[1] === user?.user_id && query?.queryKey[2] === user?.role && query?.queryKey[3] === filter && previous ? { ...previous, items: [] } : undefined,
    networkMode: "always", retry: false, refetchInterval: 30000 });
  const services = useQuery<ServiceSummary>({ queryKey: ["overview-services", ...owner], queryFn: ({ signal }) => readOverview("/api/overview/services", signal), networkMode: "always", retry: false });
  const models = useQuery<{ connections: Connection[]; presets: Preset[] }>({ queryKey: ["overview-models", ...owner], queryFn: async ({ signal }) => {
    const [connections, presets] = await Promise.all([readOverview("/api/model-connections", signal), readOverview("/api/model-connections/presets", signal)]);
    return { connections: connections.items, presets: presets.items };
  }, networkMode: "always", retry: false });
  const schedules = useQuery<Omit<ScheduledTask, "user_input">[]>({ queryKey: ["overview-schedules", ...owner], queryFn: ({ signal }) => readOverview("/api/overview/schedules", signal), networkMode: "always", retry: false, refetchInterval: 30000 });
  useEffect(() => { const previous = document.title; document.title = "概览 · Mangrove"; return () => { document.title = previous; }; }, []);
  function selectView(view: "tasks" | "schedules", nextFilter: Filter = "all", nextPage = 1, focus = false) {
    const next = new URLSearchParams(params);
    next.set("view", view); next.set("filter", nextFilter); next.set("page", String(nextPage)); setParams(next);
    if (focus) requestAnimationFrame(() => { listRef.current?.scrollIntoView({ block: "start" }); listRef.current?.focus({ preventScroll: true }); });
  }
  async function refresh() {
    setRefreshing(true);
    try { await Promise.all([activity.refetch(), services.refetch(), models.refetch(), schedules.refetch()]); }
    finally { setRefreshing(false); }
  }
  const connections = models.data?.connections ?? [];
  const choices = taskModelChoices([], connections, false, undefined, undefined, true);
  const localModels = new Set(choices.filter(choice => choice.group === "本地模型").map(choice => choice.model.toLowerCase()));
  const cloud = connections.filter(connection => !["local", "managed_private"].includes(connection.locality || "") && !(connection.models?.length && connection.models.every(model => model.current_catalog === false)));
  const providerList = (models.data?.presets ?? []).map(preset => {
    const matches = cloud.filter(connection => connection.preset_id === preset.preset_id);
    const configured = matches.some(connection => connection.has_key === true || (connection.has_key === undefined && connection.status === "verified"));
    const scope = [matches.some(connection => connection.owner_scope === "platform_shared") && "平台提供", matches.some(connection => connection.owner_scope === "user_personal") && "我的连接"].filter(Boolean).join("、");
    return { ...preset, ...PROVIDERS[preset.preset_id], configured, scope };
  }).sort((a, b) => Number(b.configured) - Number(a.configured));
  const custom = cloud.filter(connection => !models.data?.presets.some(preset => preset.preset_id === connection.preset_id));
  const plans = schedules.data ?? [];
  const activePlans = plans.filter(plan => plan.status === "active");
  const pausedPlans = plans.filter(plan => plan.status === "paused");
  const nextPlan = activePlans.filter(plan => plan.next_run_at).sort((a, b) => new Date(a.next_run_at!).getTime() - new Date(b.next_run_at!).getTime())[0];
  const total = schedulesView ? plans.length : activity.data?.total ?? 0;
  useEffect(() => {
    const ready = schedulesView ? schedules.isSuccess : activity.isSuccess && !activity.isPlaceholderData;
    const lastPage = Math.max(1, Math.ceil(total / pageSize));
    if (ready && page > lastPage) setParams(previous => { const next = new URLSearchParams(previous); next.set("page", String(lastPage)); return next; }, { replace: true });
  }, [schedulesView, schedules.isSuccess, activity.isSuccess, activity.isPlaceholderData, total, page, pageSize, setParams]);
  const stats = [
    { key: "active" as const, icon: Activity, label: "进行中", hint: "当前执行或排队的任务" },
    { key: "attention" as const, icon: TriangleAlert, label: "待处理", hint: "需要确认、暂停或执行失败" },
    { key: "completed" as const, icon: CheckCircle2, label: "近7天完成", hint: "按完成时间统计，仅我的任务" },
  ];
  return <>
    <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border px-7 py-4">
      <div><h1 className="text-lg font-semibold tracking-tight">概览</h1><p className="text-sm text-muted-foreground">任务进展与平台状态，一目了然</p></div>
      <div className="flex flex-wrap items-center gap-2"><PageGuide page="dashboard" /><Button variant="outline" aria-label="刷新概览" disabled={refreshing} onClick={refresh}><RefreshCw className={cn(refreshing && "animate-spin motion-reduce:animate-none")} />{refreshing ? "刷新中" : "刷新"}</Button><Link className={cn(LINK, "bg-primary px-3 py-2 text-primary-foreground hover:no-underline")} to="/data-prep"><Plus className="h-4 w-4" />新建任务</Link></div>
    </header>
    <div className="flex-1 overflow-y-auto p-4 sm:p-6">
      <div className="mx-auto max-w-[1600px] space-y-6">
        <div className="grid grid-cols-2 gap-3 xl:grid-cols-4">
          {stats.map(stat => <button key={stat.key} className={cn(PANEL, "p-4 text-left transition-colors hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:p-5")} onClick={() => selectView("tasks", stat.key, 1, true)}>
            <span className="flex items-center justify-between text-sm text-muted-foreground">{stat.label}<stat.icon className="h-4 w-4 text-primary" /></span>
            <span className="my-2 block text-3xl font-semibold tabular-nums">{activity.data?.stats[stat.key] ?? "—"}</span><span className="block text-xs text-muted-foreground">{activity.isError ? "统计更新失败" : stat.hint}</span>
          </button>)}
          <button className={cn(PANEL, "p-4 text-left transition-colors hover:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring sm:p-5")} onClick={() => selectView("schedules", "all", 1, true)}>
            <span className="flex items-center justify-between text-sm text-muted-foreground">自动化任务<CalendarClock className="h-4 w-4 text-primary" /></span>
            <span className="my-2 block text-3xl font-semibold tabular-nums">{schedules.data ? activePlans.length : "—"}<span className="ml-2 text-xs font-normal text-muted-foreground">已启用</span></span>
            <span className="block text-xs text-muted-foreground">{schedules.isError ? "计划更新失败" : schedules.data ? `已启用 ${activePlans.length} · 已暂停 ${pausedPlans.length}` : "正在读取计划…"}</span>
          </button>
        </div>
        <section aria-labelledby="platform-services-title">
          <div className="mb-3 flex items-center justify-between"><h2 id="platform-services-title" className="font-semibold">平台服务</h2>{manager && <Link className={LINK} to="/settings?section=diagnostics">运行与诊断<ArrowRight className="h-3 w-3" /></Link>}</div>
          <div className="grid gap-4 min-[1280px]:grid-cols-3">
            <ServiceCard title="模型与连接" icon={<Cpu className="h-5 w-5 text-primary" />} subtitle={models.data ? <>本地模型 · {localModels.size} 个已配置<br />云端供应商 · {providerList.filter(p => p.configured).length} 个已配置</> : "当前账号获准使用的模型"} footer={<><span>彩色已配 · 灰色未配</span><Link className={LINK} to="/settings?section=models">管理连接<ArrowRight className="h-3 w-3" /></Link></>}>
              <LoadState label="模型列表" loading={models.isPending} error={models.isError} stale={!!models.data} retry={() => void models.refetch()} />
              {models.data && <div className="grid auto-rows-[52px] grid-cols-2 gap-x-2">
                {(expandedProviders ? providerList : providerList.slice(0, 8)).map(provider => <div key={provider.preset_id} data-provider={provider.preset_id} title={`${provider.label || provider.display_name}：${provider.configured ? `已配置（${provider.scope}）；连接健康请查看验证记录` : "未配置 API Key"}`} className="my-1 flex min-w-0 items-center gap-2 rounded-lg border border-border bg-muted/20 px-2">
                  <span className={cn("flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-muted/60", provider.preset_id === "kimi" && "bg-slate-900")}>
                    {provider.file && BRAND_FILES.has(provider.file) ? <img src={`/overview-brands/${provider.file}`} alt="" className={cn("h-5 w-5 object-contain", provider.preset_id === "xai" && "dark:invert", !provider.configured && "grayscale opacity-45")} /> : provider.preset_id === "openai" ? <SiOpenai aria-hidden className={cn("h-5 w-5", !provider.configured && "text-muted-foreground")} /> : <Cpu className="h-5 w-5 text-muted-foreground" />}
                  </span><span className="min-w-0"><span className={cn("block truncate text-sm", !provider.configured && "text-muted-foreground")}>{provider.label || provider.display_name}</span><span className="block truncate text-[11px] text-muted-foreground">{provider.configured ? provider.scope : "未配置"}</span></span>
                </div>)}
              </div>}
              {(providerList.length > 8 || custom.length > 0) && <Button variant="link" size="sm" aria-expanded={expandedProviders} onClick={() => setExpandedProviders(value => !value)}>{expandedProviders ? "收起" : "更多连接"}</Button>}
              {expandedProviders && custom.map(connection => <p className="mt-2 text-sm" key={connection.connection_id}>{connection.display_name} · {connection.owner_scope === "platform_shared" ? "平台提供" : "我的连接"}</p>)}
            </ServiceCard>
            <ServiceCard title="平台登录态" icon={<Globe className="h-5 w-5 text-primary" />} subtitle={<>共享账号的最近检查结果<br />不代表当前实时在线</>} footer={<><span>有效 · 失效 · 未检查</span>{manager ? <Link className={LINK} to="/settings?section=platform&service=cookies">管理账号<ArrowRight className="h-3 w-3" /></Link> : <Link className={LINK} to="/settings?section=credentials">我的采集账号<ArrowRight className="h-3 w-3" /></Link>}</>}>
              <LoadState label="服务状态" loading={services.isPending} error={services.isError} stale={!!services.data} retry={() => void services.refetch()} />
              {services.data && <div className="grid auto-rows-[52px] grid-cols-3 gap-x-2">{services.data.cookies.map(cookie => {
                const label = cookie.status === "valid" ? "有效" : cookie.status === "invalid" ? "失效" : "未检查";
                return <div key={cookie.platform} tabIndex={0} aria-label={`${PLATFORMS[cookie.platform] || cookie.platform}：${label}，${dateLabel(cookie.checked_at)}`} title={`上次检查：${label} · ${dateLabel(cookie.checked_at)}`} className="flex min-w-0 items-center gap-1.5 rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  <CircleDot aria-hidden className={cn("h-3 w-3 shrink-0", cookie.status === "valid" ? "text-emerald-600 dark:text-emerald-400" : cookie.status === "invalid" ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground")} />
                  {BRAND_FILES.has(`${cookie.platform}.png`) ? <img src={`/overview-brands/${cookie.platform}.png`} alt="" className={cn("h-5 w-5 shrink-0 rounded object-contain", cookie.status !== "valid" && "grayscale opacity-50")} /> : <Globe aria-hidden className="h-5 w-5 shrink-0 text-muted-foreground" />}<span className="min-w-0"><span className="block truncate text-sm">{PLATFORMS[cookie.platform] || cookie.platform}</span><span className="block text-[11px] text-muted-foreground">{label}</span></span>
                </div>;
              })}</div>}
            </ServiceCard>
            <ServiceCard title="服务与增强" icon={<Server className="h-5 w-5 text-primary" />} subtitle={<>展示平台配置状态<br />连接是否可用需单独检查</>} footer={<><span>配置不等于检查通过</span>{manager ? <Link className={LINK} to="/settings?section=platform">管理服务<ArrowRight className="h-3 w-3" /></Link> : <span>由管理员维护</span>}</>}>
              <LoadState label="服务状态" loading={services.isPending} error={services.isError} stale={!!services.data} retry={() => void services.refetch()} />
              {services.data && <div className="grid auto-rows-[52px]">{services.data.services.map(service => {
                const Icon = ({ search: Search, email: Mail, slack: Slack, embedding: BookOpen } as Record<string, typeof Search>)[service.key] || Server;
                return <div key={service.key} className="flex min-w-0 items-center justify-between gap-2 border-b border-border/50 last:border-0"><span className="flex items-center gap-2 text-sm"><Icon className="h-4 w-4 text-primary" />{service.label}</span><span className="text-xs text-muted-foreground">{!service.enabled ? "已停用" : service.configured ? "已配置 · 未检查" : "未配置"}</span></div>;
              })}</div>}
            </ServiceCard>
          </div>
        </section>
        <section ref={listRef} tabIndex={-1} aria-label="任务与定时计划" className={cn(PANEL, "scroll-mt-4 focus:outline-none")}>
          <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3 sm:px-5">
            <div className="flex gap-1" role="group" aria-label="列表类型"><Button variant={!schedulesView ? "secondary" : "ghost"} aria-pressed={!schedulesView} onClick={() => selectView("tasks")}>最近任务</Button><Button variant={schedulesView ? "secondary" : "ghost"} aria-pressed={schedulesView} onClick={() => selectView("schedules")}>定时计划</Button></div>
            <Link className={LINK} to={schedulesView ? "/tasks" : "/data-prep"}>{schedulesView ? "管理全部计划" : "进入任务工作台"}<ArrowRight className="h-4 w-4" /></Link>
          </div>
          <div className="p-4 sm:px-5">
            {schedulesView ? <>
              <p className="mb-3 text-sm text-muted-foreground">{services.isError ? "调度服务状态未知" : services.data?.scheduler_enabled === false ? "平台调度已停用，计划不会自动执行" : services.isPending ? "正在读取调度状态…" : nextPlan ? `最近计划执行：${dateLabel(nextPlan.next_run_at)}` : "暂无待执行计划"}<span className="ml-2 text-xs">时间按本机时区显示</span></p>
              <LoadState label="定时计划" loading={schedules.isPending} error={schedules.isError} stale={!!schedules.data} retry={() => void schedules.refetch()} />
              {schedules.data && plans.slice((page - 1) * pageSize, page * pageSize).map(plan => <div className="grid gap-2 border-b border-border py-3 last:border-0 sm:grid-cols-[minmax(0,1fr)_160px_180px]" key={plan.task_id}>
                <div className="min-w-0"><Link className={cn(LINK, "max-w-full font-medium")} to={`/tasks?task=${encodeURIComponent(plan.task_id)}`}><span className="truncate">{plan.name || "未命名计划"}</span></Link><p className="text-xs text-muted-foreground">{describeTrigger(plan)}</p></div>
                <div className="text-sm"><span>{plan.status === "paused" ? "已暂停" : "已启用"}</span><p className="mt-1 text-xs text-muted-foreground">上次：{!plan.last_run_at ? "尚未执行" : plan.last_success === 1 ? "成功" : plan.last_success === 0 ? "失败" : "结果未知"}</p></div>
                <div className="text-xs text-muted-foreground">下次执行<p className="mt-1 text-sm text-foreground">{plan.status === "paused" ? "已暂停" : services.data?.scheduler_enabled === false ? "调度已停用" : dateLabel(plan.next_run_at)}</p></div>
              </div>)}
              {schedules.isSuccess && !plans.length && <p className="py-8 text-center text-sm text-muted-foreground">还没有定时计划。前往自动化任务添加。</p>}
            </> : <>
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><label className="flex items-center gap-2 text-sm">任务状态<select className="h-9 rounded-md border border-border bg-background px-2" value={filter} onChange={event => selectView("tasks", event.target.value as Filter)}>{Object.entries(FILTERS).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label><span className="text-xs text-muted-foreground">仅我的任务 · 更新于 {dateLabel(activity.data?.updated_at)}</span></div>
              <LoadState label="任务列表" loading={activity.isPending || activity.isPlaceholderData} error={activity.isError} stale={!!activity.data} retry={() => void activity.refetch()} />
              {activity.data?.items.map(task => <div className="grid items-center gap-1 border-b border-border py-3 last:border-0 sm:grid-cols-[minmax(0,1fr)_100px_130px] sm:gap-4" key={`${task.kind}:${task.id}`}>
                <Link className={cn(LINK, "min-w-0 font-medium")} to={`/data-prep?${task.kind === "task" ? "task" : "conversation"}=${encodeURIComponent(task.id)}`}><span className="truncate">{task.title || "未命名任务"}</span><ArrowRight className="h-3 w-3 shrink-0" /></Link>
                <span className={cn("text-sm", task.status === "completed" ? "text-teal-700 dark:text-teal-300" : ["failed", "needs_input", "candidate_ready"].includes(task.status) ? "text-amber-700 dark:text-amber-300" : "text-muted-foreground")}>{STATUS[task.status] || "状态未知"}</span><time className="text-xs text-muted-foreground" dateTime={task.updated_at}>{dateLabel(task.updated_at)}</time>
              </div>)}
              {activity.isSuccess && !activity.isPlaceholderData && !activity.data.items.length && <p className="py-8 text-center text-sm text-muted-foreground">{filter === "all" ? "还没有任务，从右上角新建任务开始。" : "此分类暂无任务。"}</p>}
            </>}
            {(schedulesView ? schedules.data : activity.data) && <nav aria-label="概览列表分页" className="mt-3 border-t border-border">
              <Pagination page={page} totalPages={Math.max(1, Math.ceil(total / pageSize))} total={total}
                pageSize={pageSize} pageSizeOptions={PAGE_SIZES} disabled={schedulesView ? schedules.isFetching : activity.isFetching}
                onChange={value => selectView(schedulesView ? "schedules" : "tasks", filter, value)}
                onPageSizeChange={value => { const next = new URLSearchParams(params); next.set("page_size", String(value)); next.set("page", "1"); setParams(next); }} />
            </nav>}
          </div>
        </section>
      </div>
    </div>
  </>;
}
