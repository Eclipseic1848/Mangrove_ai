import { useEffect, useState } from "react";
import { BookOpen, ChevronDown, ChevronRight, Loader2, Pencil, Play, RotateCcw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { api } from "@/lib/api";
import { formatRelativeTime } from "@/lib/utils";
import { ConfigGuideModal, GuideStepsInline } from "@/components/ConfigGuideModal";
import { ADMIN_GUIDE_SECTIONS, CONFIG_GUIDE_SECTIONS, COOKIE_KEY_ORDER } from "@/lib/configGuides";

interface CookieHealth {
  status: "valid" | "invalid" | "unknown";
  message: string;
  checked_at: string;
  checked_by: "manual" | "scheduled";
}
interface CfgItem {
  key: string; label: string; secret: boolean; user_editable?: boolean;
  source?: "override" | "env"; value: string; set?: boolean; group?: string;
  type?: "select"; choices?: string[]; choicesFrom?: string;  // 下拉选择型
  health?: CookieHealth | null;  // 仅 cookies 分组的项有值；未验证过为 null
}
interface CfgGroup { key: string; label: string; items: CfgItem[] }

/** 从 /api/config/models 获取的各供应商可选模型列表。 */
interface ModelData {
  models: Record<string, string[]>;
  default_provider: string;
  available_providers: string[];
  local_urls?: Record<string, string>;  // 本地模型名→base_url，切换时联动更新地址
}

/** 每个配置键对应的连通验证目标（无则不显示验证按钮）。 */
const VERIFY_TARGET: Record<string, string> = {
  llm_default_provider: "llm_deepseek",
  deepseek_api_key: "llm_deepseek", deepseek_base_url: "llm_deepseek", deepseek_model: "llm_deepseek",
  qwen_api_key: "llm_qwen", qwen_base_url: "llm_qwen", qwen_model: "llm_qwen",
  llm_base_url: "llm_local", llm_model_name: "llm_local", local_extra_models: "llm_local",
  local_enable_thinking: "llm_local",
  anysearch_api_key: "anysearch",
  tavily_api_key: "search", searxng_base_url: "searxng_base_url",
  firecrawl_base_url: "firecrawl_base_url", firecrawl_api_key: "firecrawl_base_url",
  rsshub_base_url: "rsshub_base_url",
  smtp_enabled: "email", smtp_host: "email", smtp_port: "email", smtp_user: "email", smtp_password: "email",
  smtp_from: "email", smtp_use_ssl: "email",
  slack_enabled: "slack", slack_webhook_url: "slack",
  embedding_enabled: "semantic", embedding_base_url: "semantic", embedding_api_key: "semantic",
  embedding_model: "semantic", rerank_base_url: "semantic", rerank_api_key: "semantic", rerank_model: "semantic",
  db_backend: "mysql", mysql_host: "mysql", mysql_port: "mysql", mysql_user: "mysql",
  mysql_password: "mysql", mysql_database: "mysql",
  // 平台 Cookie 验证：目标即键名本身，_verify_target 按 mc_cookie_ 前缀路由到真实探测
  mc_cookie_dy: "mc_cookie_dy", mc_cookie_xhs: "mc_cookie_xhs", mc_cookie_wb: "mc_cookie_wb",
  mc_cookie_bili: "mc_cookie_bili", mc_cookie_zhihu: "mc_cookie_zhihu",
  mc_cookie_ks: "mc_cookie_ks", mc_cookie_tieba: "mc_cookie_tieba",
  // 京东/淘宝/拼多多电商 Cookie：目标即键名本身，_verify_target 路由到真实登录态探测
  // （访问登录后才能看的页面，看是否被重定向回登录页；拼多多反爬激进，判定为 best-effort）
  jd_cookie: "jd_cookie", tb_cookie: "tb_cookie", pdd_cookie: "pdd_cookie",
  // 代理池：8 个字段共用同一个"已保存"轻量校验目标（_verify_target 的 proxy 分支）
  mc_enable_ip_proxy: "proxy", mc_ip_proxy_provider: "proxy", mc_static_proxy_url: "proxy",
  mc_kdl_secret_id: "proxy", mc_kdl_signature: "proxy", mc_kdl_user_name: "proxy",
  mc_kdl_user_pwd: "proxy", mc_wandou_app_key: "proxy",
  checkpoint_enabled: "checkpoint",
  cookie_health_scan_enabled: "cookie_health", cookie_health_scan_interval_hours: "cookie_health",
  // CDP 反检测模式：验证目标探测本机是否装了 Chrome/Edge（_verify_target 的 mc_cdp 分支）
  mc_enable_cdp_mode: "mc_cdp",
  // 知识库巡检：4项共用同一个验证目标（_verify_target 的 library_dedup 分支，回读当前启用状态与参数）
  library_dedup_scan_enabled: "library_dedup", library_dedup_scan_interval_hours: "library_dedup",
  library_stale_draft_days: "library_dedup", library_dedup_scan_max_merges_per_run: "library_dedup",
};

/** 验证会有真实副作用/耗时较长的目标：点击前需先弹确认框，不能秒触发。 */
function isSlowVerifyTarget(target: string): boolean {
  return target === "slack" || target.startsWith("mc_cookie_");
}

function slowVerifyConfirmText(target: string): string {
  if (target === "slack") {
    return "会向 Webhook 绑定的频道发送一条测试消息，频道成员可见。";
  }
  return "会真实启动一次 MediaCrawler 浏览器自动化登录并小范围搜索，用来确认该平台 Cookie 是否还有效——耗时可能从十几秒到几分钟不等。";
}

/** best-effort：反爬较激进、结果仅供参考的 Cookie（拼多多）。 */
const BEST_EFFORT_COOKIE_KEYS = new Set(["pdd_cookie"]);

/** 旧模型配置仍被 Conductor 等旧流程读取；只降级展示，不迁移或删除。 */
const LEGACY_MODEL_GROUPS = ["llm_default", "document_extraction", "llm_deepseek", "llm_qwen", "llm_local"];

/** 管理员平台配置按用途分区；旧模型配置单独放进折叠兼容区。 */
const GROUP_CATEGORIES: { label: string; groups: string[] }[] = [
  { label: "采集与反爬", groups: ["search", "cookies", "cookie_health", "proxy", "mc_cdp"] },
  { label: "通知集成", groups: ["email", "slack"] },
  { label: "高级 / 基础设施", groups: ["semantic", "mysql", "checkpoint"] },
  { label: "知识库巡检", groups: ["library_dedup"] },
];

function CookieHealthBadge({ health }: { health?: CookieHealth | null }) {
  if (!health) {
    return <Badge variant="outline">从未验证</Badge>;
  }
  const variant = health.status === "valid" ? "success" : health.status === "invalid" ? "danger" : "outline";
  const text = health.status === "valid" ? "有效" : health.status === "invalid" ? "失效" : "未知";
  return (
    <Badge variant={variant} title={health.message}>
      {text} · {formatRelativeTime(health.checked_at)}
    </Badge>
  );
}

function useVerify() {
  const [verifying, setVerifying] = useState<string | null>(null);
  const run = async (target: string) => {
    setVerifying(target);
    try {
      const r = await api.post("/api/config/verify", { target });
      r.ok ? toast.success(r.detail) : toast.error(r.detail);
    } catch (e: any) {
      toast.error(e.message || "验证失败");
    } finally {
      setVerifying(null);
    }
  };
  return { verifying, run };
}

/** 供应商→模型键映射：默认供应商联动对应模型一并保存。 */
const PROVIDER_MODEL_KEY: Record<string, string> = {
  deepseek: "deepseek_model",
  qwen: "qwen_model",
  local: "llm_model_name",
};

/** 管理员/超管：全局配置中心（改完即时生效，.env 为兜底）。 */
export function AdminConfigCenter() {
  const [groups, setGroups] = useState<CfgGroup[]>([]);
  const [models, setModels] = useState<ModelData | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [edit, setEdit] = useState<CfgItem | null>(null);
  const [val, setVal] = useState("");            // 主值（非级联：文本/下拉值；级联：选中的供应商）
  const [cascadeModel, setCascadeModel] = useState("");  // 级联二级：选中供应商下的模型
  const [saving, setSaving] = useState(false);
  const { verifying, run } = useVerify();
  const [confirmTarget, setConfirmTarget] = useState<string | null>(null);  // 待确认的慢速/副作用验证目标
  const [guideOpen, setGuideOpen] = useState(false);

  /** 验证有真实副作用或耗时较长（Slack 发消息、Cookie 真实登录探测）时先弹确认，其余直接跑。 */
  const runOrConfirm = (target: string) => {
    if (isSlowVerifyTarget(target)) setConfirmTarget(target);
    else run(target);
  };

  const load = async () => {
    const ts = Date.now();
    try {
      const [d, m] = await Promise.all([
        api.get(`/api/config?_=${ts}`),
        api.get(`/api/config/models?_=${ts}`),
      ]);
      setGroups((d as any).groups || []);
      setModels(m as ModelData);
    } catch {
      /* 静默：网络抖动时不刷旧数据即可 */
    }
  };
  useEffect(() => { load(); }, []);

  /** 根据配置项的 choices/choicesFrom 解析下拉选项列表。 */
  const getChoices = (item: CfgItem): string[] => {
    if (item.choices) return item.choices;
    if (item.choicesFrom && models?.models) {
      const provider = item.choicesFrom;
      return models.models[provider] || [];
    }
    return [];
  };

  /** 当前级联供应商对应的可选模型列表。 */
  const cascadeChoices = (): string[] => {
    if (!val || !models?.models) return [];
    return models.models[val] || [];
  };

  /** 打开编辑弹窗：普通项填 val；级联项（llm_default_provider）额外填 cascadeModel。 */
  const openEdit = (item: CfgItem) => {
    setEdit(item);
    if (item.key === "llm_default_provider") {
      const prov = item.value || "deepseek";
      setVal(prov);
      // 从 groups 中找出该供应商当前默认模型
      const modelItem = groups.flatMap((g) => g.items).find((i) => i.key === PROVIDER_MODEL_KEY[prov]);
      setCascadeModel(modelItem?.value || "");
    } else {
      setVal(item.type === "select" ? item.value : "");
      setCascadeModel("");
    }
  };

  const save = async () => {
    if (!edit) return;
    setSaving(true);
    try {
      if (edit.key === "llm_default_provider") {
        // 级联保存：先存模型（低风险），再切供应商；任一失败则报错并保留弹窗以便重试
        const modelKey = PROVIDER_MODEL_KEY[val];
        if (modelKey && cascadeModel) {
          await api.put(`/api/config/${modelKey}`, { value: cascadeModel });
          // 本地模型有独立端点时，同步更新 llm_base_url（不同模型部署在不同端口）
          if (val === "local" && models?.local_urls?.[cascadeModel]) {
            await api.put(`/api/config/llm_base_url`, { value: models.local_urls[cascadeModel] });
          }
        }
        await api.put(`/api/config/${edit.key}`, { value: val });
        toast.success(`默认模型已切换至 ${val} / ${cascadeModel || "（未选模型，仅切供应商）"}`);
      } else {
        await api.put(`/api/config/${edit.key}`, { value: val });
        toast.success(`${edit.label} 已保存并即时生效`);
      }
      setEdit(null);
      await load();
      window.dispatchEvent(new CustomEvent("mangrove:config-changed"));
    } catch (e: any) {
      toast.error(e.message || "保存失败");
    } finally {
      setSaving(false);
    }
  };

  const reset = async (item: CfgItem) => {
    try {
      await api.del(`/api/config/${item.key}`);
      toast.success(`${item.label} 已恢复 .env 默认`);
      await load();
      window.dispatchEvent(new CustomEvent("mangrove:config-changed"));
    } catch (e: any) {
      toast.error(e.message || "重置失败");
    }
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">平台配置（全局，改完即时生效）</CardTitle>
          <Button variant="outline" size="sm" className="h-7 gap-1.5" onClick={() => setGuideOpen(true)}>
            <BookOpen className="h-3.5 w-3.5" /> 使用指南
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          密钥/Cookie/端点等运行时可改；「已覆盖」为前端保存的值，「.env 兜底」为服务器默认。重置即恢复默认，不影响 Agent 运行。
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        {(() => {
          const byKey = new Map(groups.map((g) => [g.key, g]));
          const categorized = new Set([
            ...LEGACY_MODEL_GROUPS,
            ...GROUP_CATEGORIES.flatMap((c) => c.groups),
          ]);
          const leftover = groups.filter((g) => !categorized.has(g.key));
          const renderGroup = (g: CfgGroup) => {
            const opened = open.has(g.key);
            return (
              <div key={g.key} className="rounded-md border border-border/60">
                <button
                  className="flex w-full items-center gap-2 px-3 py-2.5 text-sm font-medium"
                  onClick={() => setOpen((s) => { const n = new Set(s); opened ? n.delete(g.key) : n.add(g.key); return n; })}
                >
                  {opened ? <ChevronDown className="h-4 w-4" /> : <ChevronRight className="h-4 w-4" />}
                  {g.label}
                  <span className="ml-auto text-[11px] font-normal text-muted-foreground">
                    {g.items.filter((i) => i.source === "override").length > 0 &&
                      `${g.items.filter((i) => i.source === "override").length} 项已覆盖`}
                  </span>
                </button>
                {opened && (
                  <div className="space-y-1 border-t border-border/60 px-3 py-2">
                    {g.items.map((it) => {
                      const target = VERIFY_TARGET[it.key];
                      return (
                        <div key={it.key} className="flex flex-wrap items-center gap-x-2 gap-y-1 py-1 text-sm">
                          <span className="min-w-[180px]">{it.label}</span>
                          <code className="max-w-[260px] truncate rounded bg-muted px-1.5 py-0.5 text-xs">
                            {it.value || "（未配置）"}
                          </code>
                          <Badge variant={it.source === "override" ? "success" : "outline"}>
                            {it.source === "override" ? "已覆盖" : ".env 兜底"}
                          </Badge>
                          {g.key === "cookies" && (
                            <>
                              <CookieHealthBadge health={it.health} />
                              {BEST_EFFORT_COOKIE_KEYS.has(it.key) && (
                                <span
                                  className="rounded border border-border/60 px-1 text-[10px] text-muted-foreground"
                                  title="反爬较激进，结果仅供参考"
                                >
                                  best-effort
                                </span>
                              )}
                            </>
                          )}
                          <span className="ml-auto flex items-center gap-1">
                            <Button variant="ghost" size="sm" className="h-7 gap-1 px-2"
                              onClick={() => openEdit(it)}>
                              <Pencil className="h-3.5 w-3.5" /> 修改
                            </Button>
                            {it.source === "override" && (
                              <Button variant="ghost" size="sm" className="h-7 gap-1 px-2" onClick={() => reset(it)}>
                                <RotateCcw className="h-3.5 w-3.5" /> 重置
                              </Button>
                            )}
                            <Button
                              variant="ghost" size="sm" className="h-7 gap-1 px-2"
                              disabled={!it.value || verifying === target}
                              title={!it.value ? "先配置才能验证" : undefined}
                              onClick={() => runOrConfirm(target)}
                            >
                              {verifying === target
                                ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
                                : <Play className="h-3.5 w-3.5" />} 验证
                            </Button>
                          </span>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            );
          };
          return (
            <>
              {LEGACY_MODEL_GROUPS.some((key) => byKey.has(key)) && (
                <details className="rounded-xl border border-amber-500/30 bg-amber-500/[0.03]">
                  <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
                    旧流程兼容
                    <span className="ml-2 text-xs font-normal text-muted-foreground">
                      Conductor、本地模型和旧文档流程仍在读取；暂不迁移或删除
                    </span>
                  </summary>
                  <div className="space-y-1.5 border-t border-amber-500/20 p-3">
                    {LEGACY_MODEL_GROUPS
                      .map((key) => byKey.get(key))
                      .filter((group): group is CfgGroup => !!group)
                      .map(renderGroup)}
                  </div>
                </details>
              )}
              {GROUP_CATEGORIES.map((cat) => {
                const catGroups = cat.groups.map((k) => byKey.get(k)).filter((g): g is CfgGroup => !!g);
                if (!catGroups.length) return null;
                return (
                  <div key={cat.label}>
                    <div className="mb-1.5 text-xs font-medium text-muted-foreground">{cat.label}</div>
                    <div className="space-y-1.5">{catGroups.map(renderGroup)}</div>
                  </div>
                );
              })}
              {leftover.length > 0 && (
                <div>
                  <div className="mb-1.5 text-xs font-medium text-muted-foreground">其他</div>
                  <div className="space-y-1.5">{leftover.map(renderGroup)}</div>
                </div>
              )}
            </>
          );
        })()}
        {!groups.length && <p className="py-3 text-center text-sm text-muted-foreground">加载中…</p>}
      </CardContent>

      <Modal open={!!edit} onClose={() => setEdit(null)} title={`修改 · ${edit?.label ?? ""}`}>
        {edit?.key === "llm_default_provider" ? (
          /* ──── 默认模型：二级联动（供应商 → 模型） ──── */
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">
              当前默认：{edit.value || "（未配置）"}（{edit.source === "override" ? "前端覆盖" : ".env 兜底"}）
            </p>
            <div className="flex items-center gap-3">
              <label className="w-16 shrink-0 text-sm text-muted-foreground">供应商</label>
              <select
                className="flex h-10 flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                value={val}
                onChange={(e) => { setVal(e.target.value); setCascadeModel(""); }}
              >
                {(["deepseek", "qwen", "local"] as string[]).map((p) => (
                  <option key={p} value={p}>{p === "deepseek" ? "DeepSeek" : p === "qwen" ? "阿里百炼 Qwen" : "本地模型"}</option>
                ))}
              </select>
            </div>
            <div className="flex items-center gap-3">
              <label className="w-16 shrink-0 text-sm text-muted-foreground">模型</label>
              <select
                className="flex h-10 flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                value={cascadeModel}
                onChange={(e) => setCascadeModel(e.target.value)}
              >
                <option value="" disabled>请选择模型…</option>
                {cascadeChoices().map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            </div>
            <p className="text-xs text-muted-foreground">
              保存后将同时更新「默认供应商」和对应「{val === "deepseek" ? "DeepSeek" : val === "qwen" ? "百炼" : "本地"}默认模型」两项。
            </p>
          </div>
        ) : (
          /* ──── 普通项 / 单一下拉项 ──── */
          <>
            <p className="mb-2 text-xs text-muted-foreground">
              当前值：{edit?.value || "（未配置）"}（{edit?.source === "override" ? "前端覆盖" : ".env 兜底"}）
              {edit?.secret && "；密钥仅显示尾 4 位，输入新值将完整替换"}
            </p>
            {edit && <GuideStepsInline configKey={edit.key} />}
            {edit?.type === "select" ? (
              <select
                className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                value={val}
                onChange={(e) => setVal(e.target.value)}
              >
                <option value="" disabled>请选择…</option>
                {getChoices(edit).map((m) => (
                  <option key={m} value={m}>{m}</option>
                ))}
              </select>
            ) : (
              <Input placeholder="输入新值" value={val} onChange={(e) => setVal(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && save()} />
            )}
          </>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setEdit(null)}>取消</Button>
          <Button size="sm"
            disabled={edit?.key === "llm_default_provider" ? (!val.trim() || !cascadeModel || saving) : (!val.trim() || saving)}
            onClick={save}>
            {saving ? "保存中…" : "保存并生效"}
          </Button>
        </div>
      </Modal>

      {/* 慢速/副作用验证确认（Slack 发消息、Cookie 真实登录探测） */}
      <Modal open={!!confirmTarget} onClose={() => setConfirmTarget(null)} title="确认验证">
        <p className="text-sm text-muted-foreground">
          {confirmTarget && slowVerifyConfirmText(confirmTarget)} 确定继续？
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setConfirmTarget(null)}>取消</Button>
          <Button size="sm" onClick={() => { const t = confirmTarget; setConfirmTarget(null); if (t) run(t); }}>
            开始验证
          </Button>
        </div>
      </Modal>

      <ConfigGuideModal open={guideOpen} onClose={() => setGuideOpen(false)} sections={ADMIN_GUIDE_SECTIONS} />
    </Card>
  );
}

/** 所有角色：自己的采集账号，只作用于本人任务。 */
export function SelfConfigCenter() {
  const [items, setItems] = useState<CfgItem[]>([]);
  const [edit, setEdit] = useState<CfgItem | null>(null);
  const [val, setVal] = useState("");
  const [guideOpen, setGuideOpen] = useState(false);
  const { verifying, run } = useVerify();
  const [confirmTarget, setConfirmTarget] = useState<string | null>(null);

  const runOrConfirm = (target: string) => {
    if (isSlowVerifyTarget(target)) setConfirmTarget(target);
    else run(target);
  };

  const load = () => api.get("/api/config/self").then((d) => setItems(d.items || [])).catch(() => {});
  useEffect(() => { load(); }, []);

  const save = async () => {
    if (!edit) return;
    try {
      await api.put(`/api/config/self/${edit.key}`, { value: val });
      toast.success(`${edit.label} 已保存，之后你的任务将优先使用它`);
      setEdit(null);
      load();
    } catch (e: any) {
      toast.error(e.message || "保存失败");
    }
  };

  const del = async (it: CfgItem) => {
    try {
      await api.del(`/api/config/self/${it.key}`);
      toast.success(`${it.label} 已清除，回落系统默认配置`);
      load();
    } catch (e: any) {
      toast.error(e.message || "清除失败");
    }
  };

  /** 按 key 分类固定顺序取出对应项；用户尚未加载完成或该 key 不在返回列表里时自然跳过。 */
  const byKey = new Map(items.map((it) => [it.key, it]));
  const pick = (order: string[]) =>
    order.map((k) => byKey.get(k)).filter((it): it is CfgItem => !!it);
  const cookieItems = pick(COOKIE_KEY_ORDER);

  const renderItem = (it: CfgItem) => {
    const target = VERIFY_TARGET[it.key] || it.key;
    return (
      <div key={it.key} className="flex flex-wrap items-center gap-x-2 gap-y-1 py-1 text-sm">
        <span className="min-w-[150px]">{it.label}</span>
        <code className="max-w-[220px] truncate rounded bg-muted px-1.5 py-0.5 text-xs">
          {it.set ? it.value : "（未配置，用系统默认）"}
        </code>
        <span className="ml-auto flex items-center gap-1">
          <Button variant="ghost" size="sm" className="h-7 gap-1 px-2"
            onClick={() => { setEdit(it); setVal(""); }}>
            <Pencil className="h-3.5 w-3.5" /> {it.set ? "修改" : "配置"}
          </Button>
          {it.set && (
            <Button variant="ghost" size="sm" className="h-7 gap-1 px-2" onClick={() => del(it)}>
              <Trash2 className="h-3.5 w-3.5" /> 清除
            </Button>
          )}
          <Button
            variant="ghost" size="sm" className="h-7 gap-1 px-2"
            disabled={!it.set || verifying === target}
            title={!it.set ? "先配置才能验证" : undefined}
            onClick={() => runOrConfirm(target)}
          >
            {verifying === target
              ? <Loader2 className="h-3.5 w-3.5 animate-spin" />
              : <Play className="h-3.5 w-3.5" />} 验证
          </Button>
        </span>
      </div>
    );
  };

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between gap-2">
          <CardTitle className="text-base">我的采集账号</CardTitle>
          <Button variant="outline" size="sm" className="h-7 gap-1.5" onClick={() => setGuideOpen(true)}>
            <BookOpen className="h-3.5 w-3.5" /> 使用指南
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          平台 Cookie 只用于你发起的采集任务，不会覆盖其他用户或平台全局配置。
        </p>
      </CardHeader>
      <CardContent className="space-y-4">
        <div>
          <div className="mb-1 text-xs text-muted-foreground">平台 / 网站 Cookie</div>
          <div className="space-y-1">{cookieItems.map(renderItem)}</div>
        </div>
      </CardContent>

      <Modal open={!!edit} onClose={() => setEdit(null)} title={`${edit?.set ? "修改" : "配置"} · ${edit?.label ?? ""}`}>
        {edit && <GuideStepsInline configKey={edit.key} />}
        <Input placeholder={edit?.key.includes("cookie") ? "粘贴从浏览器导出的 Cookie" : "输入 API Key"}
          value={val} onChange={(e) => setVal(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && save()} />
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setEdit(null)}>取消</Button>
          <Button size="sm" disabled={!val.trim()} onClick={save}>保存</Button>
        </div>
      </Modal>

      {/* 慢速/副作用验证确认（Cookie 真实登录探测等） */}
      <Modal open={!!confirmTarget} onClose={() => setConfirmTarget(null)} title="确认验证">
        <p className="text-sm text-muted-foreground">
          {confirmTarget && slowVerifyConfirmText(confirmTarget)} 确定继续？
        </p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setConfirmTarget(null)}>取消</Button>
          <Button size="sm" onClick={() => { const t = confirmTarget; setConfirmTarget(null); if (t) run(t); }}>
            开始验证
          </Button>
        </div>
      </Modal>

      <ConfigGuideModal
        open={guideOpen}
        onClose={() => setGuideOpen(false)}
        sections={CONFIG_GUIDE_SECTIONS.filter((section) => section.key === "cookie")}
      />
    </Card>
  );
}
