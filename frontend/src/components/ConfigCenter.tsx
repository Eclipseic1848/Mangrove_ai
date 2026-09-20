import { useEffect, useState } from "react";
import { BookOpen, Loader2, Pencil, Play, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { api } from "@/lib/api";
import { ConfigGuideModal, GuideStepsInline } from "@/components/ConfigGuideModal";
import { CONFIG_GUIDE_SECTIONS, COOKIE_KEY_ORDER } from "@/lib/configGuides";

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
  return target.startsWith("mc_cookie_");
}

function slowVerifyConfirmText(): string {
  return "会真实启动一次 MediaCrawler 浏览器自动化登录并小范围搜索，用来确认该平台 Cookie 是否还有效——耗时可能从十几秒到几分钟不等。";
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

export { AdminConfigCenter } from "./PlatformConfigCenter";

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
          {confirmTarget && slowVerifyConfirmText()} 确定继续？
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
