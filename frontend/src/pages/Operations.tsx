import { useEffect, useRef, useState } from "react";
import { PageGuide } from "@/components/onboarding/PageGuide";
import { Link, useSearchParams } from "react-router-dom";
import * as Dialog from "@radix-ui/react-dialog";
import { AlertCircle, ArrowUpRight, Bookmark, ChevronLeft, ChevronRight, Download, Info, RefreshCw, Search, ShieldCheck, SlidersHorizontal, X } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Pagination } from "@/components/ui/pagination";
import { api, ApiError, authenticatedFetch, getSessionState, readAuthenticatedBlob } from "@/lib/api";
import { isAdminish, useAuth } from "@/lib/auth";
import { Empty, EventsTable, Ranking, ResultStatus, Stat, Trend, UserTable, formatTime, resultLabels, sourceLabels } from "./OperationsViews";
import "./Operations.css";
import { OperationsTokens } from './OperationsTokens';

const TABS = { overview: "运营总览", login: "登录与活跃", visit: "访问与使用", audit: "操作审计", users: "用户洞察", tokens: "Token用量" };
type Tab = keyof typeof TABS;
type Filters = { start: string; end: string; kind: string; actor_id: string; result: string; module: string; source: string; action: string; search: string; page: number; page_size: number; granularity: "day" | "hour" };
export type Entry = { event_id: string; occurred_at: string; actor_id: string | null; actor_name: string; kind: string; module: string; action: string; object_ref: string; result: string; ip_mask: string; device: string; source: string; status_code?: number; duration_ms?: number; context?: Record<string, unknown>; changes: { field: string; before: unknown; after: unknown }[] };
type Comparison = { available: boolean; reason?: string; pv_change?: number | null; uv_change?: number | null };
export type Summary = { comparison?: { previous: Comparison; year: Comparison }; activity_distribution?: { high: number; active: number; unseen: number; threshold: number }; pv: number; uv: number; pv_per_user: number; login_success: number; login_failure: number; action_failure: number; actions: number; active_users: Record<string, number>; trend: { bucket: string; pv: number; uv: number; logins: number; failures: number }[]; modules: { module: string; pv: number; uv: number; actions: number }[]; sources: { source: string; pv: number }[]; users: { actor_id: string; actor_name: string; pv: number; actions: number; logins: number; last_active: number }[]; sessions: { peak_users?: number; online_users: number; count: number; average_seconds: number }; coverage: { started_at: string; retention_days: number } };
type Options = { users: { user_id: string; name: string }[]; modules: string[]; actions?: string[]; policy: { retention_days: number; version: number } };
type View = { view_id: string; name: string; filters: Filters; tab: Tab };
const control = "dark:[color-scheme:dark] h-10 min-w-0 rounded-md border bg-background px-3 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
const dateAt = (offset = 0) => new Date(Date.now() + 8 * 3600000 - offset * 86400000).toISOString().slice(0, 10);
const safeError = (error: unknown) => error instanceof ApiError && error.status === 403 ? "权限已变化，无法查看这些数据。请返回概览。" : error instanceof ApiError && error.status === 409 ? "数据已变化或达到保存上限，请刷新后重试。" : error instanceof ApiError && error.status === 413 ? "记录超过 10,000 条，请缩小筛选范围后导出。" : "加载或保存未完成，请检查网络后重试。";

export function Operations() {
  const { user } = useAuth();
  useEffect(() => { if (!isAdminish(user?.role)) document.title = "无权访问 — Mangrove"; }, [user?.role]);
  if (!user || !isAdminish(user.role)) return <div className="p-8"><h1 className="text-xl font-semibold">无权访问</h1><p className="my-4">此页面仅管理员和超级管理员可访问</p><Link className="text-primary underline" to="/">返回概览</Link></div>;
  return <OperationsPage key={`${user.user_id}:${user.role}`} actorId={user.user_id} actorRole={user.role} />;
}

function OperationsPage({ actorId, actorRole }: { actorId: string; actorRole: string }) {
  const [params, setParams] = useSearchParams();
  const tab: Tab = params.get("tab") && params.get("tab")! in TABS ? params.get("tab") as Tab : "overview";
  const start = params.get("start") || dateAt(6), end = params.get("end") || dateAt();
  const page = Math.max(1, Math.min(10000, Math.floor(Number(params.get("page")) || 1)));
  const pageSize = [10, 20, 50, 100].includes(Number(params.get("page_size"))) ? Number(params.get("page_size")) : 10;
  const [customDates, setCustomDates] = useState(false);
  const [fields, setFields] = useState({ actor_id: "", result: "", module: "", source: "", action: "", search: "" });
  const [search, setSearch] = useState("");
  const [composing, setComposing] = useState(false);
  const [metrics, setMetrics] = useState(["login", "uv", "pv", "failure"]);
  const [autoRefresh, setAutoRefresh] = useState(false);
  useEffect(() => {
    if (!autoRefresh || tab === 'tokens') return;
    const timer = setInterval(() => { if (document.visibilityState === "visible" && !flight.current) setRevision(value => value + 1); }, 30000);
    return () => clearInterval(timer);
  }, [autoRefresh, tab]);
  const [granularity, setGranularity] = useState<"day" | "hour">("day");
  const [auditKind, setAuditKind] = useState("action");
  const [revision, setRevision] = useState(0);
  const [detailRevision, setDetailRevision] = useState(0);
  const [options, setOptions] = useState<Options | null>(null);
  const [views, setViews] = useState<View[]>([]);
  const [viewsPage, setViewsPage] = useState(1), [viewsSize, setViewsSize] = useState(10);
  const [data, setData] = useState<{ summary: Summary; items: Entry[]; total: number; page: number; queryKey: string } | null>(null);
  const [loading, setLoading] = useState(true), [error, setError] = useState("");
  const [dialog, setDialog] = useState<"detail" | "timeline" | "metrics" | "export" | "view" | "policy" | "help" | "views" | null>(null);
  const [timelineUser, setTimelineUser] = useState({ id: "", name: "" });
  const [timelinePage, setTimelinePage] = useState(1), [timelineSize, setTimelineSize] = useState(10);
  const [timeline, setTimeline] = useState<{ items: Entry[]; total: number; page: number } | null>(null);
  const [timelineLoading, setTimelineLoading] = useState(false);
  const [timelineReturn, setTimelineReturn] = useState(false);
  const [selected, setSelected] = useState("");
  const [detail, setDetail] = useState<Entry | null>(null);
  const [dialogError, setDialogError] = useState(""), [busy, setBusy] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [viewName, setViewName] = useState(""), [exportFormat, setExportFormat] = useState("csv");
  const [retention, setRetention] = useState(180);
  const mounted = useRef(true), flight = useRef(false), returnFocus = useRef<HTMLElement | null>(null);
  const searchInput = useRef<HTMLInputElement>(null);
  const current = () => mounted.current && getSessionState().user?.user_id === actorId && getSessionState().user?.role === actorRole;
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { document.title = `${TABS[tab]} — Mangrove`; }, [tab]);
  useEffect(() => {
    const nav = document.querySelector<HTMLElement>('.ops-tabs');
    if (!nav) return;
    const reveal = () => {
      const active = nav.querySelector<HTMLElement>('[aria-current="page"]');
      if (!active) return;
      const box = active.getBoundingClientRect(), viewport = nav.getBoundingClientRect();
      if (box.right > viewport.right) nav.scrollLeft += box.right - viewport.right;
      else if (box.left < viewport.left) nav.scrollLeft -= viewport.left - box.left;
    };
    const observer = new ResizeObserver(reveal); observer.observe(nav); reveal();
    return () => observer.disconnect();
  }, [tab]);
  const navigate = (values: Record<string, string>) => { const next = new URLSearchParams(params); for (const [key, value] of Object.entries(values)) next.set(key, value); setParams(next); };
  const change = (key: keyof typeof fields, value: string) => { setFields(previous => ({ ...previous, [key]: value })); if (page !== 1) navigate({ page: "1" }); };
  useEffect(() => {
    if (composing || search === fields.search) return;
    const timer = setTimeout(() => change("search", search), search ? 300 : 0);
    return () => clearTimeout(timer);
  }, [search, composing]);
  const preset = start === end && end === dateAt() ? "1" : start === end && end === dateAt(1) ? "yesterday" : end === dateAt() && start === dateAt(6) ? "7" : end === dateAt() && start === dateAt(29) ? "30" : "custom";
  const invalid = !/^\d{4}-\d{2}-\d{2}$/.test(start) || !/^\d{4}-\d{2}-\d{2}$/.test(end) || !Number.isFinite(Date.parse(start)) || !Number.isFinite(Date.parse(end)) || start > end || (Date.parse(end) - Date.parse(start)) / 86400000 > 365;
  const filters: Filters = { start, end, ...fields, kind: tab === "login" ? "login" : tab === "visit" ? "visit" : tab === "audit" ? auditKind : "", page, page_size: pageSize, granularity };
  const queryKey = JSON.stringify({ ...filters, tab });
  // 后端会将越界页钳到最后一页，URL 同步，避免下一次翻页仍使用旧页码。
  useEffect(() => {
    if (data && data.queryKey === queryKey && !loading && tab !== "users" && data.page !== page) {
      const next = new URLSearchParams(params); next.set("page", String(data.page)); setParams(next, { replace: true });
    }
  }, [data, loading, tab, page]);
  useEffect(() => {
    if (tab === 'tokens') { setData(null); setError(''); setLoading(false); return; }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      controller.abort();
      // 超时后旧筛选结果不可继续操作；切换页面的主动取消不显示错误。
      if (current()) { setData(null); setLoading(false); setError("请求超时，请重新加载。"); }
    }, 20000);
    if (invalid) { setLoading(false); setData(null); return () => { clearTimeout(timer); controller.abort(); }; }
    setLoading(true); setError("");
    Promise.all([api.post("/api/operations/summary", filters, {}, controller.signal), api.post("/api/operations/events/query", tab === "overview" ? { ...filters, kind: "action" } : filters, {}, controller.signal), api.get("/api/operations/options", { signal: controller.signal }), api.get("/api/operations/views", { signal: controller.signal })])
      .then(([summary, logs, opts, saved]) => { if (current() && !controller.signal.aborted) { setData({ summary, ...logs, queryKey }); setOptions(opts); setViews(saved.items); } })
      .catch(cause => { if (current() && !controller.signal.aborted) { setData(null); setError(safeError(cause)); } })
      .finally(() => { clearTimeout(timer); if (current() && !controller.signal.aborted) setLoading(false); });
    return () => { clearTimeout(timer); controller.abort(); };
  }, [queryKey, revision]);
  useEffect(() => {
    if (dialog !== "detail" || !selected) return;
    const controller = new AbortController();
    const timer = setTimeout(() => { controller.abort(); if (current()) setDialogError("详情读取超时，请重试。"); }, 20000);
    setDetail(null); setDialogError("");
    api.get(`/api/operations/events/${selected}`, { signal: controller.signal }).then(value => { if (current() && !controller.signal.aborted) setDetail(value); }).catch(cause => { if (current() && !controller.signal.aborted) setDialogError(safeError(cause)); }).finally(() => clearTimeout(timer));
    return () => { clearTimeout(timer); controller.abort(); };
  }, [dialog, selected, detailRevision]);
  const timelineKey = JSON.stringify({ start, end, timelineUser, timelinePage, timelineSize });
  useEffect(() => {
    if (dialog !== "timeline" || !timelineUser.id || invalid) return;
    const controller = new AbortController();
    let active = true;
    setTimelineLoading(true); setDialogError("");
    const timer = setTimeout(() => { controller.abort(); if (active && current()) { setTimelineLoading(false); setTimeline(null); setDialogError("时间线读取超时，请重试。"); } }, 20000);
    // 时间线按用户与时间读取全部事件，不继承列表中的结果/模块筛选。
    api.post("/api/operations/events/query", { start, end, actor_id: timelineUser.id, kind: "", page: timelinePage, page_size: timelineSize }, {}, controller.signal)
      .then(value => { if (active && current() && !controller.signal.aborted) { setTimeline(value); if (value.page !== timelinePage) setTimelinePage(value.page); } })
      .catch(cause => { if (active && current() && !controller.signal.aborted) { setTimeline(null); setDialogError(safeError(cause)); } })
      .finally(() => { clearTimeout(timer); if (active && current() && !controller.signal.aborted) setTimelineLoading(false); });
    return () => { active = false; clearTimeout(timer); controller.abort(); };
  }, [dialog, timelineKey, detailRevision]);
  const open = (name: NonNullable<typeof dialog>) => { returnFocus.current = document.activeElement as HTMLElement; setDialogError(""); setUncertain(false); if (name === "views") setViewsPage(1); setDialog(name); };
  const mutate = async (operation: (signal: AbortSignal) => Promise<void>) => {
    if (flight.current || uncertain) return;
    flight.current = true; setBusy(true); setDialogError("");
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);
    try { await operation(controller.signal); controller.signal.throwIfAborted(); if (current()) { setDialog(null); setRevision(value => value + 1); toast.success("操作已完成"); } }
    catch (cause) { if (current()) { setUncertain(controller.signal.aborted); setDialogError(controller.signal.aborted ? "请求超时，结果尚未确认。请关闭并刷新核对，勿重复提交。" : safeError(cause)); } }
    finally { clearTimeout(timer); flight.current = false; if (current()) setBusy(false); }
  };
  const reset = () => { setFields({ actor_id: "", result: "", module: "", source: "", action: "", search: "" }); setSearch(""); navigate({ page: "1" }); };
  const drill = (target: Tab, values: Partial<typeof fields> = {}, kind = "action") => { setFields(previous => ({ ...previous, ...values })); if (target === "audit") setAuditKind(kind); navigate({ tab: target, page: "1" }); };
  const showUser = (id: string, name: string) => {
    if (!id) return;
    setTimelineUser({ id, name }); setTimelinePage(1); setTimeline(null); setTimelineReturn(false);
    if (dialog) { setDialogError(""); setDialog("timeline"); } else open("timeline");
  };
  const showDetail = (id: string) => {
    setSelected(id); setDetail(null); setTimelineReturn(dialog === "timeline");
    if (dialog) { setDialogError(""); setDialog("detail"); } else open("detail");
  };
  const summary = data?.summary;
  const comparison = (period: "previous" | "year") => {
    const value = summary?.comparison?.[period];
    return !value?.available ? "数据范围不足" : `PV ${value.pv_change == null ? "无基数" : `${value.pv_change > 0 ? "+" : ""}${value.pv_change}%`} · UV ${value.uv_change == null ? "无基数" : `${value.uv_change > 0 ? "+" : ""}${value.uv_change}%`}`;
  };

  const filterBar = <div className="ops-filters">
    <label>用户<select aria-label="用户" className={control} value={fields.actor_id} onChange={event => change("actor_id", event.target.value)}><option value="">全部可见用户</option>{options?.users.map(user => <option key={user.user_id} value={user.user_id}>{user.name}</option>)}</select></label>
    {tab !== "visit" && tab !== "users" && <label>结果<select aria-label="结果" className={control} value={fields.result} onChange={event => change("result", event.target.value)}><option value="">全部结果</option>{Object.entries(resultLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>}
    {tab !== "login" && <label>模块<select aria-label="模块" className={control} value={fields.module} onChange={event => change("module", event.target.value)}><option value="">全部模块</option>{options?.modules.map(module => <option key={module}>{module}</option>)}</select></label>}
    {tab === "audit" && <><label>日志类型<select aria-label="日志类型" className={control} value={auditKind} onChange={event => { setAuditKind(event.target.value); navigate({ page: "1" }); }}><option value="action">业务操作</option><option value="access">审计查看与管理</option><option value="">全部事件</option></select></label><label>操作类型<select aria-label="操作类型" className={control} value={fields.action} onChange={event => change("action", event.target.value)}><option value="">全部操作</option>{options?.actions?.map(action => <option key={action}>{action}</option>)}</select></label></>}
    {tab === "visit" && <label>来源<select aria-label="来源" className={control} value={fields.source} onChange={event => change("source", event.target.value)}><option value="">全部来源</option>{Object.entries(sourceLabels).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>}
    <label className="ops-search"><span><Search size={16} /><input aria-label="搜索用户或事件编号" placeholder="搜索用户或事件编号" ref={searchInput} className={control} value={search} maxLength={80} onChange={event => setSearch(event.target.value)} onCompositionStart={() => setComposing(true)} onCompositionEnd={() => setComposing(false)} />{search && <Button variant="ghost" size="icon" aria-label="清除搜索" onClick={() => { setSearch(""); change("search", ""); searchInput.current?.focus(); }}><X /></Button>}</span></label>
    <Button variant="link" className="ops-text" onClick={reset}>清除筛选</Button><Button variant="outline" size="sm" disabled={!data || loading} onClick={() => open("view")}><Bookmark />保存视图</Button>
  </div>;
  const trend = <section className="ops-panel"><div className="ops-panel-heading"><h2>{tab === "overview" ? "使用趋势" : "访问趋势"}</h2><label className="ops-muted">粒度 <select className={control + " !h-8 !text-xs"} value={granularity} onChange={event => setGranularity(event.target.value as "day" | "hour")}><option value="day">按天</option><option value="hour">按小时</option></select></label></div><Trend key={start + end + granularity} rows={summary?.trend || []} onDay={bucket => { navigate({ tab: "visit", start: bucket.slice(0, 10), end: bucket.slice(0, 10), page: "1" }); }} /></section>;
  const table = <section className="ops-panel ops-flush">
    <div className="ops-panel-heading"><h2>{tab === "overview" ? "最近关键操作" : tab === "login" ? "登录记录" : tab === "visit" ? "访问明细" : "操作记录"}</h2>{tab === "overview" ? <Button variant="link" className="ops-text" onClick={() => drill("audit")}>查看全部日志<ArrowUpRight size={14} /></Button> : <span className="ops-muted">时间倒序 · 北京时间</span>}</div>
    <EventsTable key={data?.queryKey} items={data?.items || []} kind={tab} compact={tab === "overview"} onUser={showUser} onDetail={showDetail} />
    {<nav className="ops-pager" aria-label="日志分页"><Pagination pageSizeOptions={[10, 20, 50, 100]} total={data?.total || 0} page={data?.page || page} totalPages={Math.max(1, Math.ceil((data?.total || 0) / pageSize))} pageSize={pageSize} disabled={loading} onPageSizeChange={size => navigate({ page_size: String(size), page: "1" })} onChange={value => navigate({ page: String(value) })} /></nav>}
  </section>;
  const activeFilters = Object.entries(fields).filter(([, value]) => value);
  return <div data-operations-page className="ops-page">
    <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border px-7 py-4"><div><h1 className="text-lg font-semibold tracking-tight">运营审计</h1><p className="text-sm text-muted-foreground">看清使用情况，追溯关键操作。</p></div><PageGuide page={`operations.${tab}`} /></header>
    <div className="ops-workspace">
      <div className="ops-toolbar">
        <div className="ops-periods" role="group" aria-label="快捷时间范围">{[["1", "今天"], ["yesterday", "昨天"], ["7", "近7天"], ["30", "近30天"], ["custom", "自定义"]].map(([key, label]) => <Button key={key} size="sm" variant={(customDates || preset === "custom" ? "custom" : preset) === key ? "default" : "ghost"} aria-pressed={(customDates || preset === "custom" ? "custom" : preset) === key} onClick={() => { setCustomDates(key === "custom"); if (key !== "custom") navigate({ start: dateAt(key === "yesterday" ? 1 : Number(key) - 1), end: key === "yesterday" ? dateAt(1) : dateAt(), page: "1" }); else document.getElementById("ops-start")?.focus(); }}>{label}</Button>)}</div>
        <div className="ops-dates"><input id="ops-start" aria-label="开始日期" className={control} type="date" value={start} aria-invalid={invalid} aria-describedby={invalid ? "operations-date-error" : undefined} onChange={event => navigate({ start: event.target.value, page: "1" })} /><span>至</span><input aria-label="结束日期" className={control} type="date" value={end} aria-invalid={invalid} aria-describedby={invalid ? "operations-date-error" : undefined} onChange={event => navigate({ end: event.target.value, page: "1" })} /><span>UTC+8</span></div>
        <div className="ops-toolbar-end"><Button variant="outline" disabled={loading} onClick={() => setRevision(value => value + 1)}><RefreshCw />刷新</Button>{tab !== 'tokens' && <Button disabled={!data || loading} onClick={() => open("export")}><Download />导出记录</Button>}</div>
      </div>
      {invalid && <p id="operations-date-error" role="alert" className="mt-3 text-sm text-destructive">请选择有效日期，开始时间不能晚于结束时间，最长 366 天。</p>}
      <div className="ops-tabline"><nav aria-label="运营子页面" className="ops-tabs">{Object.entries(TABS).map(([key, label]) => { const next = new URLSearchParams(params); next.set("tab", key); next.set("page", "1"); return <Link key={key} to={`?${next}`} aria-current={tab === key ? "page" : undefined}>{label}</Link>; })}</nav><span className="ops-scope"><ShieldCheck size={14} />{actorRole === "super_admin" ? "全平台" : "普通用户及本人"}</span></div>
      {tab !== 'tokens' && <>
      {views.length > 0 && <div className="mb-3 flex flex-wrap items-center gap-3"><label className="ops-muted">常用视图 <select aria-label="常用视图" className={control + " !h-8 !text-xs"} value="" onChange={event => { const view = views.find(item => item.view_id === event.target.value); if (!view) return; setFields({ actor_id: view.filters.actor_id, result: view.filters.result, module: view.filters.module, source: view.filters.source, action: view.filters.action, search: view.filters.search }); setAuditKind(view.filters.kind); setSearch(view.filters.search); setGranularity(view.filters.granularity); navigate({ tab: view.tab, start: view.filters.start, end: view.filters.end, page: "1", page_size: String(view.filters.page_size) }); }}><option value="">选择已保存视图</option>{views.map(view => <option key={view.view_id} value={view.view_id}>{view.name}</option>)}</select></label></div>}
      {!!activeFilters.length && <div className="mb-3 flex flex-wrap items-center gap-2" aria-label="已选筛选条件">{activeFilters.map(([key, value]) => <Button key={key} size="sm" variant="secondary" aria-label={`移除筛选 ${key}`} onClick={() => { change(key as keyof typeof fields, ""); if (key === "search") setSearch(""); }}>{({ actor_id: "用户", result: "结果", module: "模块", source: "来源", action: "操作", search: "搜索" } as Record<string, string>)[key]}：{key === "actor_id" ? options?.users.find(user => user.user_id === value)?.name || "已选用户" : key === "result" ? resultLabels[value] : key === "source" ? sourceLabels[value] : value}<X size={12} /></Button>)}<Button variant="link" className="ops-text" onClick={reset}>清除全部</Button></div>}
      {error && <div role="alert" className="my-4 rounded-lg border border-destructive/30 p-4 text-sm">{error}<Button variant="link" onClick={() => setRevision(value => value + 1)}>重新加载</Button></div>}
      {loading && !summary && <div role="status" className="flex min-h-80 items-center justify-center text-sm text-muted-foreground">正在加载运营数据…</div>}
      </>}
      {tab === 'tokens' && <OperationsTokens start={start} end={end} invalid={invalid} revision={revision} actorId={actorId} actorRole={actorRole} />}
      {tab !== 'tokens' && summary && <fieldset aria-busy={loading} onClickCapture={event => { if (loading) { event.preventDefault(); event.stopPropagation(); } }} className="relative min-w-0">
        {loading && <div role="status" className="absolute inset-0 z-10 flex items-start justify-center bg-background/70 pt-6 text-sm">正在更新数据…</div>}
        {tab === "overview" && <>
          <div className="ops-section-heading"><span className="ops-muted">使用概况</span><Button variant="link" className="ops-text" onClick={() => open("metrics")}><SlidersHorizontal size={14} />自定义指标</Button></div>
          <div className="ops-stats">
            {metrics.includes("login") && <Stat label="登录次数" value={summary.login_success + summary.login_failure} note="包含成功与失败" onClick={() => drill("login", { result: "" })} />}
            {metrics.includes("uv") && <Stat label="活跃用户" value={summary.activity_distribution ? summary.activity_distribution.high + summary.activity_distribution.active : "—"} note="成功登录或访问的现有用户" onClick={() => drill("users")} />}
            {metrics.includes("pv") && <Stat label="页面访问 PV" value={summary.pv} note="有效页面进入次数" onClick={() => drill("visit")} />}
            {metrics.includes("failure") && <Stat label="失败操作" value={summary.action_failure} danger note="点击查看失败记录" onClick={() => drill("audit", { result: "failure" })} />}
          </div>
          <div className="ops-grid">{trend}<section className="ops-panel ops-attention"><h2>需要关注</h2>
            <button type="button" onClick={() => drill("login", { result: "failure" })}><AlertCircle size={20} className="text-destructive" /><span><strong>失败登录</strong><small>核对来源与账号状态</small></span><b>{summary.login_failure}</b><ChevronRight size={16} /></button>
            <button type="button" onClick={() => drill("audit", { module: "用户管理" })}><ShieldCheck size={20} /><span><strong>账号与权限操作</strong><small>查看用户管理操作记录</small></span><b>{summary.modules.find(item => item.module === "用户管理")?.actions || 0}</b><ChevronRight size={16} /></button>
            <div className="ops-health"><Info size={18} /><span><strong>数据采集范围</strong><small>{formatTime(summary.coverage.started_at)} 起 · 保留 {summary.coverage.retention_days} 天<br />历史未采集数据不回填</small></span></div>
          </section></div>{table}
        </>}
        {tab === "login" && <><div className="ops-stats"><Stat label="成功 / 失败登录" value={`${summary.login_success} / ${summary.login_failure}`} /><Stat label="DAU / WAU / MAU" value={`${summary.active_users.day} / ${summary.active_users.week} / ${summary.active_users.month}`} note="截至结束日的 1 / 7 / 30 天活跃用户" /><Stat label="当前在线（估算）" value={summary.sessions.online_users} note={`所选时段峰值 ${summary.sessions.peak_users ?? 0} 人`} /><Stat label="平均活跃时长" value={`${Math.round(summary.sessions.average_seconds / 60)} 分钟`} note={`${summary.sessions.count} 个前台活动时段`} /></div>{filterBar}{table}</>}
        {tab === "visit" && <><div className="ops-stats"><Stat label="页面访问 PV" value={summary.pv} /><Stat label="访问用户 UV" value={summary.uv} note="所选周期内去重" /><Stat label="人均 PV" value={summary.pv_per_user} /><Stat label="较上一周期" value={summary.comparison?.previous?.available && summary.comparison.previous.pv_change != null ? `${summary.comparison.previous.pv_change}%` : "—"} note="PV · 相同已过时长，无基数不比较" /></div><div className="ops-grid">{trend}<Ranking key={queryKey} items={summary.modules} onSelect={module => drill("visit", { module })} /></div><div className="mb-4 flex flex-wrap gap-3 text-xs text-muted-foreground"><span>较上一等长时段：{comparison("previous")}</span><span>较去年同期（365天前）：{comparison("year")}</span></div><div className="ops-sources" role="group" aria-label="访问来源统计"><span className="ops-muted">访问来源</span>{summary.sources.map(source => <Button key={source.source} variant="outline" size="sm" aria-pressed={fields.source === source.source} onClick={() => change("source", source.source)}>{sourceLabels[source.source] || "来源未知"} · {source.pv} 次</Button>)}</div>{filterBar}{table}</>}
        {tab === "audit" && <><div className="ops-section-heading"><span className="ops-muted">追溯关键操作，核对结果与变更内容。</span><span className="ops-scope"><ShieldCheck size={14} />只读审计</span></div>{filterBar}{table}</>}
        {tab === "users" && <><div className="ops-grid"><Ranking key={queryKey} items={summary.modules} onSelect={module => drill("visit", { module })} /><section className="ops-panel"><h2>用户活跃分布</h2><p className="ops-muted mt-2">按所选范围内的页面访问与成功登录次数</p>{summary.activity_distribution ? [["高活跃 · 10次及以上", summary.activity_distribution.high], ["有使用 · 1至9次", summary.activity_distribution.active], ["未见活动", summary.activity_distribution.unseen]].map(([label, count]) => <div className="ops-distribution" key={label}><span>{label}</span><b>{count} <small className="ops-muted">人</small></b></div>) : <Empty text="暂无活跃分布数据" />}<p className="ops-muted mt-4">未见活动不等于历史沉默；留存与回流暂不推断。</p></section></div>{filterBar}<UserTable key={queryKey} users={summary.users} onUser={showUser} /></>}
      </fieldset>}
      {tab !== 'tokens' && <footer className="ops-footer"><span>{summary ? `采集起点：${formatTime(summary.coverage.started_at)} · 保留 ${summary.coverage.retention_days} 天` : "仅展示权限范围内的已采集记录"}</span><div><label className="flex items-center gap-2"><input type="checkbox" checked={autoRefresh} onChange={event => setAutoRefresh(event.target.checked)} />30秒刷新</label><Button variant="link" className="ops-text" disabled={!views.length} onClick={() => open("views")}>管理视图</Button><Button variant="link" className="ops-text" onClick={() => open("help")}><Info size={14} />统计口径</Button>{actorRole === "super_admin" && <Button variant="link" className="ops-text" disabled={!options} onClick={() => { setRetention(options!.policy.retention_days); open("policy"); }}>保留策略</Button>}</div></footer>}
    </div>
    <Dialog.Root open={dialog !== null} onOpenChange={value => { if (!value && !flight.current) setDialog(null); }}><Dialog.Portal><Dialog.Overlay className="fixed inset-0 z-50 bg-black/40" /><Dialog.Content onCloseAutoFocus={event => { event.preventDefault(); returnFocus.current?.focus(); }} className={`ops-overlay ${dialog === "detail" || dialog === "timeline" ? "ops-drawer" : "ops-dialog"}`}><div className="flex items-center justify-between gap-3"><Dialog.Title className="text-lg font-semibold">{dialog === "detail" ? "事件详情" : dialog === "timeline" ? "用户行为时间线" : dialog === "metrics" ? "自定义指标" : dialog === "export" ? "导出记录" : dialog === "view" ? "保存常用视图" : dialog === "policy" ? "日志保留策略" : dialog === "views" ? "管理常用视图" : "统计口径"}</Dialog.Title><Dialog.Close asChild><Button variant="ghost" size="icon" disabled={busy} aria-label="关闭详情"><X /></Button></Dialog.Close></div><Dialog.Description className="mt-2 text-sm text-muted-foreground">仅处理你的权限范围内数据，不展示业务正文或密钥。</Dialog.Description>
      <div className={dialog === "detail" || dialog === "timeline" ? "ops-drawer-body" : ""}>
      {dialogError && <div role="alert" className="my-3 text-sm text-destructive">{dialogError}{(dialog === "detail" || dialog === "timeline") && <Button variant="link" onClick={() => setDetailRevision(value => value + 1)}>{dialog === "detail" ? "重试读取详情" : "重试读取时间线"}</Button>}</div>}
      {dialog === "detail" && timelineReturn && <Button variant="link" className="ops-text" onClick={() => { setDialogError(""); setDialog("timeline"); }}><ChevronLeft size={14} />返回用户时间线</Button>}
      {dialog === "detail" && (detail ? <div className="mt-5 space-y-4"><div className="flex items-center gap-3"><span className="flex h-10 w-10 items-center justify-center rounded-full bg-accent text-primary">{detail.actor_name.slice(0, 1)}</span><div><strong className="block text-sm">{detail.actor_name}</strong><small className="text-muted-foreground">{formatTime(detail.occurred_at)}</small></div></div><ResultStatus result={detail.result} /><dl className="grid grid-cols-[auto_1fr] gap-x-5 gap-y-3 text-sm">{[["事件编号", detail.event_id], ["用户", detail.actor_name], ["时间", formatTime(detail.occurred_at)], ["模块 / 操作", `${detail.module} / ${detail.action}`], ["结果", resultLabels[detail.result]], ["对象标识", detail.object_ref], ["IP 网段", detail.ip_mask || "未采集"], ["设备", detail.device], ["地理位置", "未解析，不调用外部定位服务"], ["来源", sourceLabels[detail.source]], ["响应耗时", detail.duration_ms == null ? "未采集" : `${detail.duration_ms} ms`]].map(([name, value]) => <div key={name} className="contents"><dt className="text-muted-foreground">{name}</dt><dd className="break-all">{value}</dd></div>)}</dl>{detail.context && Object.keys(detail.context).length > 0 && <section className="rounded-lg bg-muted/40 p-3"><h3 className="text-sm font-semibold">审计范围</h3><dl className="mt-2 grid grid-cols-[auto_1fr] gap-2 text-xs">{Object.entries(detail.context).map(([key, value]) => <div key={key} className="contents"><dt>{{ start: "开始日期", end: "结束日期", kind: "事件类型", module: "模块", result: "结果", count: "返回条数", query_digest: "筛选摘要", event_id: "目标事件" }[key] || key}</dt><dd className="break-all">{value == null || value === "" ? "未限定 / 未完成" : String(value)}</dd></div>)}</dl></section>}<h3 className="font-semibold">变更内容</h3>{detail.changes.length ? <table className="ops-diff"><caption className="sr-only">变更前后对照</caption><thead><tr><th scope="col">字段</th><th scope="col">变更前</th><th scope="col">变更后</th></tr></thead><tbody>{detail.changes.map((change, index) => <tr key={index}><td>{change.field}</td><td>{String(change.before ?? "未记录")}</td><td>{String(change.after ?? "已修改")}</td></tr>)}</tbody></table> : <p className="text-sm text-muted-foreground">此事件未记录字段差异；不代表没有发生变更。</p>}<Button variant="outline" disabled={!detail.actor_id} onClick={() => { if (detail.actor_id) showUser(detail.actor_id, detail.actor_name); }}>查看用户时间线</Button></div> : !dialogError && <p role="status" className="py-12 text-center">正在读取详情…</p>)}
      {dialog === "metrics" && <div className="mt-5 space-y-4">{[["login", "登录次数"], ["uv", "活跃用户"], ["pv", "页面访问"], ["failure", "失败操作"]].map(([key, name]) => <label key={key} className="flex items-center gap-3 text-sm"><input type="checkbox" checked={metrics.includes(key)} disabled={metrics.length === 1 && metrics.includes(key)} onChange={event => setMetrics(values => event.target.checked ? [...values, key] : values.filter(value => value !== key))} />{name}</label>)}<p className="ops-muted">至少保留一项，仅影响本次页面展示。</p></div>}
      {dialog === "timeline" && <><div className="mb-3"><strong>{timelineUser.name}</strong><p className="ops-muted mt-1">{start} 至 {end} · 北京时间 · 全部事件</p></div>{timelineLoading && !timeline && <p role="status" className="py-12 text-center text-sm">正在读取时间线…</p>}{timeline && <fieldset aria-busy={timelineLoading} onClickCapture={event => { if (timelineLoading) { event.preventDefault(); event.stopPropagation(); } }} className="relative min-w-0">{timelineLoading && <p role="status" className="absolute inset-0 z-10 bg-background/70 p-4 text-center text-sm">正在更新时间线…</p>}<ol key={`${timeline.page}:${timelineSize}`} className="ops-timeline">{timeline.items.map(item => <li key={item.event_id}><ResultStatus result={item.result} /><time>{formatTime(item.occurred_at)}</time><p>{item.module} · {item.action}</p><Button variant="link" className="ops-text" onClick={() => showDetail(item.event_id)}>查看详情<ChevronRight size={14} /></Button></li>)}</ol>{!timeline.items.length && <Empty />}<nav aria-label="时间线分页"><Pagination pageSizeOptions={[10, 20, 50, 100]} disabled={timelineLoading} page={timeline.page} totalPages={Math.max(1, Math.ceil(timeline.total / timelineSize))} total={timeline.total} pageSize={timelineSize} onChange={setTimelinePage} onPageSizeChange={size => { setTimelineSize(size); setTimelinePage(1); }} /></nav></fieldset>}</>}
      {dialog === "export" && <div className="mt-5 space-y-4"><p className="text-sm">导出当前筛选内的记录（最多 10,000 条），包括脱敏 IP 和设备分类。导出后请妥善保管。</p><label className="flex items-center gap-3 text-sm">文件格式<select className={control} value={exportFormat} onChange={event => setExportFormat(event.target.value)}><option value="csv">CSV</option><option value="xlsx">Excel（XLSX）</option></select></label><Button disabled={busy || uncertain} aria-busy={busy} onClick={() => mutate(async signal => { const response = await authenticatedFetch("/api/operations/export", { method: "POST", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ filters, format: exportFormat }) }); if (!response.ok) throw new ApiError(response.status, "导出未完成"); const blob = await readAuthenticatedBlob(response); if (!current() || signal.aborted) return; const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = `Mangrove-运营日志-${start}-${end}.${exportFormat}`; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); })}>{busy ? "正在导出…" : "确认导出"}</Button></div>}
      {dialog === "view" && <form noValidate className="mt-5 space-y-4" onSubmit={event => { event.preventDefault(); if (viewName.trim()) mutate(async signal => { await api.post("/api/operations/views", { name: viewName.trim(), filters, tab }, {}, signal); }); }}><label className="grid gap-2 text-sm">视图名称<input className={control} value={viewName} maxLength={30} onChange={event => setViewName(event.target.value)} /></label><p className="text-xs text-muted-foreground">保存当前页面、日期和筛选条件，仅你自己可见。</p><Button disabled={busy || uncertain || !viewName.trim()} aria-busy={busy} type="submit">{busy ? "正在保存…" : "保存视图"}</Button></form>}
      {dialog === "views" && <div className="mt-5 space-y-3"><p className="text-xs text-muted-foreground">删除仅移除此筛选快捷方式，不影响日志。需要时可重新保存。</p>{views.slice((viewsPage - 1) * viewsSize, viewsPage * viewsSize).map(view => <div key={view.view_id} className="flex items-center justify-between gap-3 rounded-lg border p-3"><span className="break-all text-sm">{view.name}</span><Button variant="outline" disabled={busy || uncertain} aria-label={`删除视图 ${view.name}`} onClick={() => mutate(async signal => { const response = await authenticatedFetch(`/api/operations/views/${view.view_id}`, { method: "DELETE", signal }); if (!response.ok) throw new ApiError(response.status, "删除视图失败"); })}>删除</Button></div>)}<nav aria-label="常用视图分页"><Pagination pageSize={viewsSize} pageSizeOptions={[10, 20, 50, 100]} onPageSizeChange={size => { setViewsSize(size); setViewsPage(1); }} page={viewsPage} totalPages={Math.max(1, Math.ceil(views.length / viewsSize))} total={views.length} onChange={setViewsPage} disabled={busy} /></nav></div>}
      {dialog === "policy" && <div className="mt-5 space-y-4"><label className="flex items-center gap-3 text-sm">保留期限<select className={control} value={retention} onChange={event => setRetention(Number(event.target.value))}><option value={90}>90 天</option><option value={180}>180 天</option></select></label><p className="text-sm">缩短后，超期运营日志立即不再可查，并将分批永久清理。仅影响本模块，不删除历史任务及既有安全审计。</p><div className="flex gap-3"><Button variant="outline" disabled={busy} onClick={() => setDialog(null)}>取消</Button><Button variant="destructive" disabled={busy || uncertain || retention === options?.policy.retention_days} aria-busy={busy} onClick={() => mutate(async signal => { const response = await authenticatedFetch("/api/operations/policy", { method: "PUT", signal, headers: { "Content-Type": "application/json" }, body: JSON.stringify({ retention_days: retention, version: options?.policy.version, confirmed: true }) }); if (!response.ok) throw new ApiError(response.status, "更新策略失败"); })}>{busy ? "正在保存…" : "确认更新策略"}</Button></div></div>}
      {dialog === "help" && <div className="mt-5 space-y-3 text-sm leading-6"><p>PV：登录后的页面访问次数。UV：所选范围内访问用户去重数。刷新页面会产生一次访问，站内路由跳转也计一次。</p><p>DAU / WAU / MAU：截至所选结束日，1 / 7 / 30 天内成功登录或访问页面的去重用户。采集起点之前没有历史数据。</p><p>在线状态：90 秒内有前台心跳。活跃时长为估算，同一登录会话多标签不重复计时，跨日分段；不是实际工作时长。</p><p>日志“成功”表示接口请求成功，不代表异步任务已交付。“结果未知”表示未取得确定回执。</p><p>IP 仅显示网段，地理位置未解析。未记录对话、文件正文、密钥或原始请求响应。</p></div>}
      </div>
    </Dialog.Content></Dialog.Portal></Dialog.Root>
  </div>;
}
