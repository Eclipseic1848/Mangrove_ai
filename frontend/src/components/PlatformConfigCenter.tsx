import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { api, ApiError } from "@/lib/api";
import { ConfigGuideModal, GuideStepsInline } from "@/components/ConfigGuideModal";
import { ADMIN_GUIDE_SECTIONS, getGuideForKey } from "@/lib/configGuides";

type Item = { key: string; label: string; secret: boolean; value: string; source: string;
  default_value?: string; type?: string; choices?: string[]; minimum?: number; maximum?: number;
  health?: { status: string; message: string; checked_at: string } | null };
type Group = { key: string; label: string; items: Item[] };
type Service = { key: string; label: string; items: Item[]; help: string; target?: string; check?: string; readonly?: boolean };
type CheckResult = { status: string; log?: string; time?: string };
const SERVICE_HELP: Record<string, string> = {
  tavily: "搜索网页，为任务提供外部资料。", searxng: "连接自建搜索服务，聚合网页搜索结果。",
  anysearch: "通过 API 搜索网页与公开内容。", firecrawl: "提取网页正文，供任务读取与分析。", rsshub: "将网站更新转为可订阅的数据源。",
};
const HELP: Record<string, string> = {
  search: "按需配置搜索和网页采集服务，不必全部开启。",
  cookies: "全平台共享。个人任务优先使用用户自己的采集账号。",
  cookie_health: "定期访问平台，检查共享账号是否失效。",
  proxy: "选择代理商，只填写对应信息。保存不等于代理可用。",
  mc_cdp: "使用服务器上的 Chrome / Edge 进行采集。",
  semantic: "检索相似资料并优化排序。检查可能调用云端并计费。",
  mysql: "设置采集数据的存储位置。切换不会迁移已有数据。",
  checkpoint: "保存执行进度，支持具备检查点的任务恢复。",
  library_dedup: "定期合并相似知识，并删除超过设定天数的停滞草稿。",
  data_prep: "仅用于旧采集流程，不影响新工作台。自动清理暂未开放。",
  email: "用户明确指定收件人后，发送报告正文或附件。",
  slack: "向已配置频道发送结果；文件发送需要 Bot Token。",
};
const CHECKS: Record<string, [string, string]> = {
  email: ["smtp", "连接 SMTP 并登录，不发送邮件。"],
  slack: ["slack", "使用 Bot Token 验证身份，不发送消息；不代表文件权限和频道权限已验证。"],
  tavily: ["search", "发送一次测试搜索，可能消耗服务额度。"],
  anysearch: ["anysearch", "发送一次测试搜索，可能消耗服务额度。"],
  searxng: ["searxng_base_url", "只检查地址可达，不保证搜索结果可用。"],
  firecrawl: ["firecrawl_base_url", "只检查地址可达，不验证 API Key 或实际抓取能力。"],
  rsshub: ["rsshub_base_url", "只检查地址可达，不保证每条订阅路由可用。"],
  semantic: ["semantic", "调用向量及已配置的重排服务，可能回退云端并产生费用；只发送测试文本。"],
  mysql: ["mysql", "连接已配置的 MySQL 数据库，不迁移数据。"],
  mc_cdp: ["mc_cdp", "仅检查服务器上的浏览器是否存在，不验证登录状态。"],
  checkpoint: ["checkpoint", "检查本地检查点存储；不代表已有任务一定可以恢复。"],
};
const ORDER = ["search", "cookies", "cookie_health", "proxy", "mc_cdp", "semantic", "mysql", "checkpoint", "library_dedup", "data_prep", "email", "slack"];
const CATEGORIES = [
  { label: "搜索采集", groups: ["search"] },
  { label: "共享账号", groups: ["cookies", "cookie_health"] },
  { label: "网络与浏览器", groups: ["proxy", "mc_cdp"] },
  { label: "存储与知识", groups: ["semantic", "mysql", "checkpoint", "library_dedup"] },
  { label: "通知", groups: ["email", "slack"] },
  { label: "其他", groups: [] as string[] },
];
const MODEL_GROUPS = ["llm_default", "document_extraction", "llm_deepseek", "llm_qwen", "llm_local"];
const LABELS: Record<string, string> = { cookies: "平台共享采集账号", cookie_health: "采集账号巡检", mc_cdp: "采集浏览器", semantic: "知识检索（语义召回）", mysql: "数据存储", library_dedup: "知识维护", data_prep: "历史流程兼容" };
const FIELD_LABELS: Record<string, string> = {
  mc_ip_proxy_provider: "代理商", mc_enable_cdp_mode: "使用服务器浏览器", db_backend: "存储方式",
  embedding_base_url: "向量服务地址", embedding_api_key: "向量服务 API Key", embedding_model: "向量模型名称",
  rerank_base_url: "重排服务地址", rerank_api_key: "重排服务 API Key", rerank_model: "重排模型名称",
};

function normalizedValue(item: Item): string {
  // 后端兼容旧布尔字符串；统一展示与表单回填，但不改写已保存配置。
  if (!item.choices?.includes("True") || !item.choices.includes("False")) return item.value;
  const value = item.value.trim().toLowerCase();
  if (["true", "1", "yes", "on"].includes(value)) return "True";
  if (["false", "0", "no", "off"].includes(value)) return "False";
  return item.value;
}

function services(group: Group): Service[] {
  const sets = group.key === "search"
    ? ["tavily", "searxng", "anysearch", "firecrawl", "rsshub"].map(key => ({ key, label: ({ tavily: "Tavily", searxng: "SearXNG", anysearch: "AnySearch", firecrawl: "Firecrawl", rsshub: "RSSHub" } as Record<string, string>)[key], items: group.items.filter(it => it.key.startsWith(`${key}_`)) }))
    : group.key === "cookies" ? group.items.map(it => ({ key: it.key, label: it.label.replace(" Cookie", ""), items: [it] }))
    : [{ ...group, label: LABELS[group.key] || group.label, items: group.items.filter(it => it.key !== "data_prep_raw_retention_days") }];
  // 未归类字段仍可查看，避免后端新增配置被静默丢弃。
  const remaining = group.items.filter(it => !sets.some(s => s.items.some(field => field.key === it.key)));
  if (remaining.length) sets.push({ key: `${group.key}_remaining`, label: group.key === "data_prep" ? "制品保留（未开放）" : `${group.label} · 其他配置`, items: remaining });
  return sets.filter(s => s.items.length).map(s => {
    const check = s.key === "mysql" && s.items.find(it => it.key === "db_backend")?.value !== "mysql" ? undefined
      : group.key === "cookies" ? [s.key, "使用已保存的共享 Cookie 访问对应平台；社交平台可能启动浏览器并小范围搜索，耗时十几秒至几分钟。结果可能受反爬影响。"] : CHECKS[s.key];
    return { ...s, items: s.items.map(it => ({ ...it, value: normalizedValue(it), label: FIELD_LABELS[it.key] || it.label })), help: SERVICE_HELP[s.key] || HELP[group.key] || "", target: check?.[0], check: check?.[1] };
  });
}
function visibleItems(service: Service, values: Record<string, string>) {
  return service.items.filter(it => {
    if (service.key === "mysql" && values.db_backend === "sqlite") return it.key === "db_backend";
    if (service.key !== "proxy") return true;
    if (it.key === "mc_enable_ip_proxy" || it.key === "mc_ip_proxy_provider") return true;
    return values.mc_ip_proxy_provider === "static" ? it.key === "mc_static_proxy_url"
      : values.mc_ip_proxy_provider === "wandouhttp" ? it.key === "mc_wandou_app_key" : it.key.startsWith("mc_kdl_");
  });
}
const display = (value: string) => ({ True: "开启", False: "关闭", kuaidaili: "快代理", wandouhttp: "豌豆 HTTP", static: "固定代理", sqlite: "SQLite（本机）", mysql: "MySQL" } as Record<string, string>)[value] || value || "未配置";

function savedCookieCheck(item: Item): CheckResult {
  if (!item.value.trim()) return { status: "请先配置 Cookie" };
  if (!item.health) return { status: "尚未检查" };
  return { status: `上次检查：${item.health.status === "valid" ? "有效" : item.health.status === "invalid" ? "失效" : "未知"}`, log: item.health.message, time: item.health.checked_at.replace("T", " ") };
}

function CheckSummary({ label, result, onView }: { label: string; result: CheckResult; onView: () => void }) {
  return <div role="status" className="min-w-0 space-y-1">
    <div className="flex min-w-0 items-center gap-2"><span className="shrink-0 font-medium text-foreground">{result.status}</span>{result.time && <span className="truncate text-xs" title={result.time}>{result.time}</span>}</div>
    {result.log ? <button type="button" aria-label={`查看 ${label} 检查日志`} onClick={onView} className="flex w-full min-w-0 cursor-pointer items-center gap-2 rounded text-left hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"><span className="min-w-0 flex-1 truncate">日志：{result.log}</span><span className="shrink-0 text-primary">查看</span></button> : <p className="text-xs">暂无检查日志</p>}
  </div>;
}

export function AdminConfigCenter() {
  const [searchParams] = useSearchParams();
  const targetService = searchParams.get("service") || "";
  const targetCategory = CATEGORIES.find(c => c.groups.includes(targetService))?.label;
  const targetRow = useRef<HTMLElement>(null);
  const focusedService = useRef("");
  const [groups, setGroups] = useState<Group[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState(targetCategory || "搜索采集");
  const [edit, setEdit] = useState<Service | null>(null);
  const [view, setView] = useState<Service | null>(null);
  const [guideService, setGuideService] = useState<Service | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const [invalidKey, setInvalidKey] = useState("");
  const [revealed, setRevealed] = useState<string[]>([]);
  const searchInput = useRef<HTMLInputElement>(null);
  const [unknown, setUnknown] = useState(false);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const loadSequence = useRef(0);
  const [confirm, setConfirm] = useState<{ service: Service; item?: Item; batch?: Service[] } | null>(null);
  const [batchProgress, setBatchProgress] = useState("");
  const [batchActive, setBatchActive] = useState(false);
  const stopBatch = useRef(false);
  const mounted = useRef(true);
  const [results, setResults] = useState<Record<string, CheckResult>>({});
  const [logView, setLogView] = useState<{ label: string; result: CheckResult } | null>(null);
  const [notice, setNotice] = useState("");
  const [guide, setGuide] = useState(false);
  async function load() {
    const sequence = ++loadSequence.current;
    setLoading(true); setLoadError("");
    try {
      const data = await api.get("/api/config");
      if (sequence === loadSequence.current) setGroups(data.groups || []);
      return true;
    } catch {
      if (sequence === loadSequence.current) setLoadError("配置加载失败。当前内容可能不是最新，请重新加载后再操作。");
      return false;
    } finally { if (sequence === loadSequence.current) setLoading(false); }
  }
  useEffect(() => { mounted.current = true; void load(); return () => { mounted.current = false; stopBatch.current = true; loadSequence.current++; }; }, []);
  useEffect(() => {
    if (targetCategory) { setCategory(targetCategory); setQuery(""); }
    focusedService.current = "";
  }, [targetService, targetCategory]);
  useEffect(() => {
    // 等配置加载完成再定位；后台刷新和手动切换分类不重复抢焦点。
    if (!loading && targetRow.current && focusedService.current !== targetService) {
      targetRow.current.scrollIntoView({ block: "center" });
      targetRow.current.focus({ preventScroll: true });
      focusedService.current = targetService;
    }
  }, [loading, category, targetService, groups]);
  function openEdit(service: Service) {
    setEdit(service); setValues(Object.fromEntries(service.items.map(it => [it.key, it.secret ? "" : it.value])));
    setError(""); setUnknown(false); setInvalidKey(""); setRevealed([]);
  }
  async function save() {
    if (!edit || lock.current || unknown) return;
    const changed: Record<string, string> = {};
    for (const it of visibleItems(edit, values)) {
      const raw = values[it.key] || "";
      const value = it.secret ? raw : raw.trim();
      if (it.secret && !value.trim() || !it.secret && value === it.value) continue;
      if (!value || it.type === "number" && (!/^\d+$/.test(value) || it.minimum != null && Number(value) < it.minimum || it.maximum != null && Number(value) > it.maximum)) {
        setError(`${it.label} ${!value ? "不能为空；恢复默认请使用恢复操作" : "须填写允许范围内的整数"}`);
        setInvalidKey(it.key);
        document.getElementById(`config-${it.key}`)?.focus(); return;
      }
      changed[it.key] = value;
    }
    if (!Object.keys(changed).length) { setError("没有需要保存的修改。密钥留空表示保留原值。"); return; }
    lock.current = true; setBusy(true); setError("");
    try {
      await api.put("/api/config/batch", { values: changed });
      setNotice(`${edit.label} 已保存。`);
      setResults(old => ({ ...old, [edit.key]: { status: "配置已修改，尚未重新检查" } }));
      setEdit(null); await load();
      window.dispatchEvent(new CustomEvent("mangrove:config-changed"));
    } catch (e) {
      const uncertain = !(e instanceof ApiError) || e.status >= 500;
      setUnknown(uncertain);
      setError(uncertain ? "保存结果未知，可能已生效。请关闭后重新加载并核对，不要重复提交。" : (e as Error).message);
    } finally { lock.current = false; setBusy(false); }
  }
  async function act() {
    if (!confirm || lock.current) return;
    if (confirm.batch) {
      const batch = confirm.batch;
      lock.current = true; setBusy(true); setBatchActive(true); stopBatch.current = false; setConfirm(null);
      let completed = 0;
      // 逐项请求，避免同时启动多个采集浏览器；离开页面或结果未知时不再发起下一项。
      for (const service of batch) {
        if (stopBatch.current || !mounted.current) break;
        setBatchProgress(`正在检查 ${service.label} · ${completed}/${batch.length}`);
        setResults(old => ({ ...old, [service.key]: { status: "正在检查 Cookie 状态…" } }));
        try {
          const result = await api.post("/api/config/verify", { target: service.target });
          if (!mounted.current) break;
          setResults(old => ({ ...old, [service.key]: { status: result.ok ? "本次检查通过" : "本次检查未通过", log: result.detail, time: new Date().toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" }) } }));
        } catch {
          if (!mounted.current) break;
          setResults(old => ({ ...old, [service.key]: { status: "检查结果未知", log: "请重新加载核对；未自动重试。" } }));
          stopBatch.current = true;
        }
        completed++;
      }
      if (mounted.current) {
        setBatchProgress(`${stopBatch.current ? "批量检查已停止" : "批量检查完成"} · ${completed}/${batch.length}`);
        await load(); setBusy(false); setBatchActive(false);
      }
      lock.current = false;
      return;
    }
    lock.current = true; setBusy(true); setError("");
    const { service, item } = confirm;
    try {
      if (item) {
        await api.del(`/api/config/${item.key}`);
        setNotice(`${item.label} 已恢复服务器默认。`);
        setResults(old => ({ ...old, [service.key]: { status: "配置已恢复，尚未重新检查" } }));
        setEdit(null);
        window.dispatchEvent(new CustomEvent("mangrove:config-changed"));
      } else {
        const result = await api.post("/api/config/verify", { target: service.target });
        setResults(old => ({ ...old, [service.key]: { status: result.ok ? "本次检查通过" : "本次检查未通过", log: result.detail, time: new Date().toLocaleString("zh-CN", { hour12: false, timeZone: "Asia/Shanghai" }) } }));
      }
      setConfirm(null); await load();
    } catch (e) {
      setUnknown(true);
      setError("操作未取得确认，结果可能未知。关闭后重新加载核对，不自动重复操作。");
    } finally { lock.current = false; setBusy(false); }
  }
  const disabled = busy || loading || !!loadError;
  const available = groups.filter(g => !MODEL_GROUPS.includes(g.key) && g.key !== "data_prep");
  const cookies = groups.filter(g => g.key === "cookies").flatMap(services);
  const configuredCookies = cookies.filter(s => s.items[0]?.value.trim());
  const cookieScan = groups.filter(g => g.key === "cookie_health").flatMap(services)[0];
  const categoryFor = (key: string) => CATEGORIES.find(c => c.groups.includes(key))?.label || "其他";
  const shown = [...ORDER.flatMap(key => available.filter(g => g.key === key)), ...available.filter(g => !ORDER.includes(g.key))]
    .filter(g => query.trim() || categoryFor(g.key) === category)
    .map(g => ({ ...g, services: services(g).filter(s => !query.trim() || `${g.label} ${s.label} ${s.help} ${s.items.map(it => `${it.label} ${it.key}`).join(" ")}`.toLowerCase().includes(query.trim().toLowerCase())) })).filter(g => g.services.length);
  const listed = shown.filter(g => g.key !== "cookie_health");
  const showCookieManagement = (category === "共享账号" || shown.some(g => g.key === "cookies" || g.key === "cookie_health")) && (cookies.length > 0 || !!cookieScan);
  return <Card>
    <CardHeader>
      <div className="flex flex-wrap items-center justify-between gap-3"><CardTitle>平台配置</CardTitle><Button variant="outline" size="sm" onClick={() => setGuide(true)}>使用指南</Button></div>
      <p className="text-sm text-muted-foreground">全平台共享，保存后影响后续调用。</p>
      <Link className="text-sm text-primary underline underline-offset-4" to="/settings?section=models&scope=platform">管理平台模型</Link>
    </CardHeader>
    <CardContent className="space-y-3">
      <div className="flex flex-wrap items-end gap-2"><label className="min-w-0 flex-1 text-sm">查找服务<Input ref={searchInput} value={query} onChange={e => setQuery(e.target.value)} placeholder="如：代理、存储" /></label>{query && <Button variant="outline" onClick={() => { setQuery(""); searchInput.current?.focus(); }}>清除搜索</Button>}<Button variant="outline" disabled={loading || busy} onClick={() => void load()}>重新加载</Button></div>
      <nav aria-label="配置分类" className="flex flex-wrap gap-2">{CATEGORIES.filter(c => c.label !== "其他" || available.some(g => categoryFor(g.key) === "其他")).map(c => <Button key={c.label} variant={category === c.label && !query.trim() ? "default" : "outline"} aria-pressed={category === c.label && !query.trim()} onClick={() => { setCategory(c.label); setQuery(""); }}>{c.label}</Button>)}</nav>
      <p className="text-sm text-muted-foreground">{query.trim() ? "全部分类的搜索结果" : category} · {listed.reduce((total, g) => total + g.services.length, 0)} 项服务</p>
      {showCookieManagement && <section aria-label="Cookie 检查管理" className="rounded-lg border border-primary/25 border-l-4 border-l-primary bg-primary/5 p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-semibold text-primary">Cookie 检查管理</h2><p className="text-xs text-muted-foreground mt-1">{configuredCookies.length} 个已配置，{cookies.length - configuredCookies.length} 个未配置。手动检查覆盖全部已配置账号。</p></div>
        <Button disabled={disabled || !configuredCookies.length} onClick={() => { setConfirm({ service: { key: "cookie_batch", label: "全部共享 Cookie", items: [], help: "", check: `将检查 ${configuredCookies.length} 个已配置的共享 Cookie，未配置的跳过。会真实访问对应平台，可能启动浏览器并进行少量搜索，耗时数分钟；按顺序执行，不自动重试。` }, batch: configuredCookies }); setError(""); setUnknown(false); }}>一键检查 Cookie 状态</Button>
        </div>
        {cookieScan && <div className="flex flex-wrap items-center justify-between gap-3 border-t border-primary/20 pt-3"><div className="text-sm"><span className="font-medium">定时巡检</span><span className="ml-2">{cookieScan.items.find(it => it.key === "cookie_health_scan_enabled")?.value === "True" ? "开启" : "关闭"}</span><span className="ml-3 text-xs text-muted-foreground">间隔 {cookieScan.items.find(it => it.key === "cookie_health_scan_interval_hours")?.value || "未设置"} 小时</span></div><div className="flex gap-2"><Button variant="outline" size="sm" disabled={disabled} aria-label={`配置 ${cookieScan.label}`} onClick={() => openEdit(cookieScan)}>编辑巡检</Button><Button variant="ghost" size="sm" onClick={() => setGuideService(cookieScan)}>巡检说明</Button></div></div>}
      </section>}
      {batchProgress && <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border p-3"><p role="status" className="text-sm">{batchProgress}</p>{batchActive && <Button size="sm" variant="outline" onClick={() => { stopBatch.current = true; setBatchProgress("正在停止后续检查；当前请求仍会完成。"); }}>停止后续检查</Button>}</div>}
      {loading && <p role="status">正在加载平台配置…</p>}
      {loadError && <p role="alert" className="text-sm text-destructive">{loadError}</p>}
      {notice && <p role="status" className="rounded-md border p-3 text-sm">{notice}</p>}
      {!loading && !loadError && !shown.length && <p>没有匹配的服务。{query ? "请更换关键词或清除搜索。" : "暂无可维护配置。"}</p>}
      {listed.length > 0 && <div className="divide-y rounded-lg border" data-testid="config-service-grid">{listed.flatMap(group => group.services.map(service => <section key={service.key} aria-label={service.label} ref={service.key === targetService ? targetRow : undefined} tabIndex={service.key === targetService ? -1 : undefined} className={`grid min-w-0 items-center gap-x-4 gap-y-2 px-3 py-3 ${service.key === targetService ? "bg-primary/5 outline-none ring-inset focus:ring-2 focus:ring-primary" : ""} ${group.key === "cookies" ? "md:grid-cols-[minmax(0,0.55fr)_minmax(0,1.45fr)_16rem]" : "md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_16rem]"}`}>
          <div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="text-sm font-semibold">{service.label}</h3><span className="rounded bg-muted px-1.5 py-0.5 text-xs">{service.readonly ? "只读" : service.items.some(it => it.value.trim()) ? "已有配置" : "未配置"}</span></div><p className="mt-1 text-xs text-muted-foreground">{group.key === "cookies" ? "平台共享登录凭据" : service.help}</p></div>
          <div className="min-w-0 text-xs text-muted-foreground" data-testid="config-service-summary">
            {group.key === "cookies" || results[service.key] ? <CheckSummary label={service.label} result={results[service.key] || savedCookieCheck(service.items[0])} onView={() => setLogView({ label: service.label, result: results[service.key] || savedCookieCheck(service.items[0]) })} />
              : <dl className="space-y-1">{service.items.slice(0, 2).map(it => <div key={it.key} className="flex min-w-0 gap-1"><dt className="shrink-0">{it.label}：</dt><dd className="truncate" title={it.secret ? undefined : display(it.value)}>{it.secret ? it.value ? "凭据已保存" : "未配置" : display(it.value)}</dd></div>)}</dl>}
          </div>
          <div className="grid grid-cols-3 items-center gap-2 md:w-64">
            {service.readonly ? <Button variant="outline" size="sm" onClick={() => setView(service)}>查看详情</Button> : <Button variant="outline" size="sm" disabled={disabled} aria-label={`配置 ${service.label}`} onClick={() => openEdit(service)}>{service.items.some(it => it.value.trim()) ? "编辑配置" : "添加配置"}</Button>}
            {service.target && <Button variant="secondary" size="sm" disabled={disabled || group.key === "cookies" && !service.items[0]?.value.trim()} aria-label={`检查 ${service.label}`} onClick={() => { setConfirm({ service }); setError(""); setUnknown(false); }}>{group.key === "cookies" ? "检查状态" : "检查连接"}</Button>}
            <Button variant="ghost" size="sm" className="col-start-3" onClick={() => setGuideService(service)}>配置说明</Button>
          </div>
        </section>))}</div>}
    </CardContent>
    <Modal open={!!logView} onClose={() => setLogView(null)} title={`${logView?.label || ""} 检查日志`}><p className="font-medium">{logView?.result.status}</p><p className="mt-1 text-xs text-muted-foreground">{logView?.result.time}</p><p className="mt-3 whitespace-pre-wrap break-words text-sm">{logView?.result.log}</p><Button className="mt-4" variant="outline" onClick={() => setLogView(null)}>关闭</Button></Modal>
    <Modal open={!!view} onClose={() => setView(null)} title={view?.label || "配置详情"}>
      <p className="mb-4 text-sm text-muted-foreground">{view?.help}</p><dl className="space-y-3">{view?.items.map(it => <div key={it.key}><dt className="text-sm text-muted-foreground">{it.label}</dt><dd className="break-all text-sm">{display(it.value)} · {it.source === "override" ? "平台设置" : "服务器默认"}</dd></div>)}</dl><Button className="mt-4" variant="outline" onClick={() => setView(null)}>关闭</Button>
    </Modal>
    <Modal open={!!edit && !confirm} onClose={() => { if (!lock.current) setEdit(null); }} title={`配置 ${edit?.label || ""}`} wide>
      {edit && <form noValidate onSubmit={e => { e.preventDefault(); void save(); }} onKeyDown={e => { if (e.key === "Enter" && e.nativeEvent.isComposing) e.preventDefault(); }} className="space-y-4">
        <p className="text-sm text-muted-foreground">{edit.help}</p><p className="text-sm">密钥留空保留。其他字段直接修改。</p>
        <fieldset disabled={busy || unknown} className="space-y-4">{visibleItems(edit, values).map(it => <div key={it.key} className="space-y-1">
          <label htmlFor={`config-${it.key}`} className="text-sm font-medium">{it.key === "data_prep_mode_enabled" ? "旧采集流程默认模式" : it.label}</label>
          {/* 少量固定选项复用浏览器原生选择器，保留系统键盘交互。 */}
          {it.choices ? <select id={`config-${it.key}`} className="h-10 w-full rounded-md border bg-background px-3 text-sm" value={values[it.key]} onChange={e => { setValues(v => ({ ...v, [it.key]: e.target.value })); setError(""); setInvalidKey(""); }}><option value="" disabled>请选择</option>{it.choices.map(choice => <option key={choice} value={choice}>{display(choice)}</option>)}</select>
            : <Input id={`config-${it.key}`} type={it.secret && !revealed.includes(it.key) ? "password" : it.type === "number" ? "number" : "text"} autoComplete={it.secret ? "new-password" : "off"} min={it.minimum} max={it.maximum} step={it.type === "number" ? 1 : undefined} aria-invalid={invalidKey === it.key} aria-describedby={`help-${it.key}${invalidKey === it.key ? " config-error" : ""}`} value={values[it.key] || ""} placeholder={it.secret ? "留空保留已存凭据" : "请输入配置值"} onChange={e => { setValues(v => ({ ...v, [it.key]: e.target.value })); setError(""); setInvalidKey(""); }} />}
          {it.secret && <Button type="button" size="sm" variant="ghost" aria-label={`${revealed.includes(it.key) ? "隐藏" : "显示"} ${it.label}`} onClick={() => setRevealed(keys => keys.includes(it.key) ? keys.filter(key => key !== it.key) : [...keys, it.key])}>{revealed.includes(it.key) ? "隐藏输入" : "显示输入"}</Button>}
          <div id={`help-${it.key}`} className="flex flex-wrap items-center gap-2 text-sm text-muted-foreground"><span>{it.source === "override" ? "平台设置" : "服务器默认"}{it.secret && ` · ${it.value ? "已存凭据" : "未配置"}`}{it.type === "number" && ` · 最小 ${it.minimum}${it.maximum ? `，最大 ${it.maximum}` : ""}`}</span>{it.source === "override" && <Button type="button" size="sm" variant="outline" onClick={() => { setConfirm({ service: edit, item: it }); setError(""); setUnknown(false); }}>恢复 {it.label} 默认</Button>}</div>
          {getGuideForKey(it.key) && <details><summary className="cursor-pointer text-sm text-primary">如何填写</summary><GuideStepsInline configKey={it.key} /></details>}
        </div>)}</fieldset>
        {error && <p id="config-error" role="alert" className="text-sm text-destructive">{error}</p>}
        <div className="flex justify-end gap-2"><Button type="button" variant="outline" disabled={busy} onClick={() => setEdit(null)}>{unknown ? "关闭并核对" : "取消"}</Button><Button type="submit" disabled={busy || unknown}>{busy ? "保存中…" : "保存配置"}</Button></div>
      </form>}
    </Modal>
    <ConfigGuideModal open={!!guideService} onClose={() => setGuideService(null)} title={`${guideService?.label || "服务"} 配置说明`} sections={guideService ? [{ key: guideService.key, title: guideService.help, entries: ADMIN_GUIDE_SECTIONS.find(section => section.key === guideService.key)?.entries ?? guideService.items.map(it => getGuideForKey(it.key) || { key: it.key, title: it.label, steps: [{ text: guideService.help || "按服务维护者提供的参数填写。" }] }) }] : []} />
    <Modal open={!!confirm} onClose={() => { if (!lock.current) { setConfirm(null); setError(""); } }} title={confirm?.item ? "恢复服务器默认" : `检查 ${confirm?.service.label || ""}`}>
      <p className="text-sm break-words">{confirm?.item ? `${confirm.item.label} 将移除平台设置，恢复为：${confirm.item.default_value === undefined ? "服务器启动时的默认值（当前未提供预览）" : display(confirm.item.default_value)}。可能影响后续调用；未保存的编辑将丢弃。` : confirm?.service.check}</p>
      {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
      <div className="mt-4 flex justify-end gap-2"><Button variant="outline" disabled={busy} onClick={() => { setConfirm(null); setError(""); if (unknown) setEdit(null); }}>{unknown ? "关闭并核对" : "取消"}</Button><Button disabled={busy || unknown} onClick={() => void act()}>{busy ? "处理中…" : confirm?.item ? "确认恢复默认" : "开始检查"}</Button></div>
    </Modal>
    <Modal open={guide} onClose={() => setGuide(false)} title="平台配置使用指南">
      <ol className="list-decimal pl-5 space-y-3 text-sm"><li>按用途找到服务，点击“配置”，同一服务的字段一次填写。</li><li>地址与数字显示当前值；密钥留空保留。保存后会影响相关服务的后续读取，但不会迁移旧数据。</li><li>需要时点击“检查已保存配置”，先阅读检查范围与外部访问说明。只读参数没有伪造的验证按钮。</li><li>“平台设置”表示管理员覆盖值，不表示健康。恢复默认前会展示回落值；秘密只展示掩码。</li><li>模型统一在“模型与连接”维护；个人登录凭据在“采集账号”维护。</li></ol>
      <Button className="mt-4" onClick={() => setGuide(false)}>关闭指南</Button>
    </Modal>
  </Card>;
}
