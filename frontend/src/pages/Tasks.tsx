import { useEffect, useState } from "react";
import {
  CalendarClock, Trash2, RefreshCw, Clock, CheckCircle2, XCircle,
  FileText, Download, ArrowLeft, Braces, Plus, Pencil, Play,
  MessageSquare, TrendingUp, Gavel, Newspaper, Rocket, ShoppingCart, Heart, Zap,
  Sparkles, ListChecks, Search, ChevronLeft, ChevronRight,
} from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Markdown } from "@/components/Markdown";
import { api, downloadFile } from "@/lib/api";
import { cn } from "@/lib/utils";

/** 开关样式沿用设置页「连接器/增强」的 Toggle（同一套视觉语言）。 */
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

interface Task {
  task_id: string;
  name?: string | null;
  source?: string; // auto | manual | template
  status?: string; // active | paused
  user_input: string;
  trigger_type: string;
  cron_expr?: string;
  interval_seconds?: number | null;
  run_at?: string;
  next_run_at?: string;
  start_date?: string | null;
  end_date?: string | null;
  run_count: number;
  last_success?: number | null;
  last_run_at?: string;
  last_result?: string;
  last_error?: string;
}

interface Run {
  run_id: number;
  run_at: string;
  success: boolean;
  summary: string;
  has_report: boolean;
  has_json: boolean;
}

interface RecentRun extends Run {
  task_id: string;
  task_name: string;
}

interface Template {
  id: string;
  name: string;
  description: string;
  prompt: string;
  trigger_type: string;
  cron_expr?: string;
}

const TEMPLATE_ICONS: Record<string, typeof Sparkles> = {
  daily_voc_report: MessageSquare,
  weekly_sentiment_report: TrendingUp,
  bidding_monitor: Gavel,
  industry_news_digest: Newspaper,
  new_product_buzz: Rocket,
  ecommerce_review_monitor: ShoppingCart,
  xiaohongshu_weekly: Heart,
  one_time_collection: Zap,
};

const SOURCE_LABEL: Record<string, string> = { auto: "自动识别", manual: "手动创建", template: "模板创建" };
const WEEKDAY_NAMES = ["日", "一", "二", "三", "四", "五", "六"];

/** 触发方式的人话描述：cron/interval 拼时间点，once 给具体时刻。 */
function describeTrigger(t: Pick<Task, "trigger_type" | "cron_expr" | "interval_seconds" | "run_at">): string {
  if (t.trigger_type === "once") {
    return `单次 ${(t.run_at || "").replace("T", " ").slice(0, 16)}`;
  }
  if (t.trigger_type === "interval") {
    const s = t.interval_seconds || 0;
    if (s > 0 && s % 3600 === 0) return `每 ${s / 3600} 小时`;
    return `每 ${Math.max(1, Math.round(s / 60))} 分钟`;
  }
  const expr = (t.cron_expr || "").trim();
  const parts = expr.split(/\s+/);
  if (parts.length === 5) {
    const [mm, hh, dom, mon, dow] = parts;
    const time = `${hh.padStart(2, "0")}:${mm.padStart(2, "0")}`;
    if (dom === "*" && mon === "*" && dow === "*") return `每天 ${time}`;
    if (dom === "*" && mon === "*" && dow !== "*") {
      const days = dow.split(",").map((d) => WEEKDAY_NAMES[Number(d) % 7] ?? d).join("、");
      return `每周${days} ${time}`;
    }
    if (dom !== "*" && mon === "*" && dow === "*") return `每月 ${dom} 号 ${time}`;
  }
  return expr || "—";
}

/** 上次执行的人话摘要：不暴露服务器文件路径（用户拿到路径也没用） */
function lastSummary(t: Task): string {
  if (!t.last_run_at) return "";
  const raw = (t.last_success ? t.last_result : t.last_error) || "";
  const note = raw.match(/^\[[^\]]+\]/)?.[0] ?? "";
  const body = raw.replace(/^\[[^\]]+\]\s*/, "");
  if (!t.last_success) return `${note ? note + " " : ""}${body || "执行失败"}`;
  if (/report=/.test(body)) return `${note ? note + " " : ""}报告已生成，点击「报告」查看或下载`;
  return `${note ? note + " " : ""}${body}`;
}

function runSummary(r: Run): string {
  const note = r.summary.match(/^\[[^\]]+\]/)?.[0] ?? "";
  const body = r.summary.replace(/^\[[^\]]+\]\s*/, "");
  if (/report=/.test(body)) return note ? `${note} 报告已生成` : "报告已生成";
  return r.summary;
}

// ---------- 创建/编辑表单 ----------

type FreqMode = "cron" | "interval" | "once";
type CronMode = "daily" | "weekly" | "monthly" | "advanced";

interface FormState {
  name: string;
  prompt: string;
  freqMode: FreqMode;
  cronMode: CronMode;
  weekdays: number[]; // 1-6=周一~周六, 0=周日
  monthDay: number;
  time: string; // HH:MM
  advancedCron: string;
  intervalValue: number;
  intervalUnit: "minutes" | "hours";
  runAt: string; // datetime-local
  startDate: string;
  endDate: string;
}

const EMPTY_FORM: FormState = {
  name: "", prompt: "", freqMode: "cron", cronMode: "daily",
  weekdays: [1], monthDay: 1, time: "09:00", advancedCron: "",
  intervalValue: 2, intervalUnit: "hours", runAt: "", startDate: "", endDate: "",
};

function buildCronExpr(f: FormState): string {
  if (f.cronMode === "advanced") return f.advancedCron.trim();
  const [hh, mm] = f.time.split(":");
  if (f.cronMode === "daily") return `${Number(mm)} ${Number(hh)} * * *`;
  if (f.cronMode === "weekly") {
    const days = f.weekdays.length ? [...f.weekdays].sort().join(",") : "1";
    return `${Number(mm)} ${Number(hh)} * * ${days}`;
  }
  return `${Number(mm)} ${Number(hh)} ${f.monthDay} * *`; // monthly
}

/** 反解已有 cron 表达式为结构化表单值；反解不了的复杂表达式落到“高级”原样展示。 */
function parseCronToForm(expr: string): Partial<FormState> {
  const parts = (expr || "").trim().split(/\s+/);
  if (parts.length !== 5) return { cronMode: "advanced", advancedCron: expr };
  const [mm, hh, dom, mon, dow] = parts;
  const time = `${hh.padStart(2, "0")}:${mm.padStart(2, "0")}`;
  if (dom === "*" && mon === "*" && dow === "*") return { cronMode: "daily", time };
  if (dom === "*" && mon === "*" && dow !== "*") {
    const weekdays = dow.split(",").map(Number).filter((n) => !Number.isNaN(n));
    return { cronMode: "weekly", time, weekdays };
  }
  if (dom !== "*" && mon === "*" && dow === "*") {
    return { cronMode: "monthly", time, monthDay: Number(dom) || 1 };
  }
  return { cronMode: "advanced", advancedCron: expr };
}

function formToTrigger(f: FormState): { type: FreqMode; cron_expr?: string; interval_seconds?: number; run_at?: string } {
  if (f.freqMode === "interval") {
    const seconds = f.intervalUnit === "hours" ? f.intervalValue * 3600 : f.intervalValue * 60;
    return { type: "interval", interval_seconds: seconds };
  }
  if (f.freqMode === "once") return { type: "once", run_at: f.runAt };
  return { type: "cron", cron_expr: buildCronExpr(f) };
}

function taskToForm(t: Task): FormState {
  const base = { ...EMPTY_FORM, name: t.name || "", prompt: t.user_input, startDate: t.start_date || "", endDate: t.end_date || "" };
  if (t.trigger_type === "interval") {
    const s = t.interval_seconds || 3600;
    const hours = s % 3600 === 0;
    return { ...base, freqMode: "interval", intervalUnit: hours ? "hours" : "minutes", intervalValue: hours ? s / 3600 : Math.round(s / 60) };
  }
  if (t.trigger_type === "once") {
    return { ...base, freqMode: "once", runAt: (t.run_at || "").slice(0, 16) };
  }
  return { ...base, freqMode: "cron", ...parseCronToForm(t.cron_expr || "") };
}

function TaskFormModal({
  open, onClose, initial, onSaved,
}: {
  open: boolean;
  onClose: () => void;
  initial: { mode: "create"; form: FormState } | { mode: "edit"; taskId: string; form: FormState } | null;
  onSaved: () => void;
}) {
  const [f, setF] = useState<FormState>(EMPTY_FORM);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    if (initial) setF(initial.form);
  }, [initial]);

  if (!open || !initial) return null;

  const toggleWeekday = (d: number) => {
    setF((s) => ({
      ...s,
      weekdays: s.weekdays.includes(d) ? s.weekdays.filter((x) => x !== d) : [...s.weekdays, d].sort(),
    }));
  };

  const save = async () => {
    if (!f.name.trim() || !f.prompt.trim()) {
      toast.error("请填写名称和提示词");
      return;
    }
    if (f.freqMode === "once" && !f.runAt) {
      toast.error("请选择单次执行时间");
      return;
    }
    if (f.freqMode === "cron" && f.cronMode === "advanced" && !f.advancedCron.trim()) {
      toast.error("请填写 cron 表达式");
      return;
    }
    const trigger = formToTrigger(f);
    setSaving(true);
    try {
      if (initial.mode === "create") {
        await api.post("/api/tasks/manual", {
          name: f.name.trim(), prompt: f.prompt.trim(), trigger,
          start_date: f.startDate || undefined, end_date: f.endDate || undefined,
        });
        toast.success("已创建自动化任务");
      } else {
        await api.patch(`/api/tasks/${initial.taskId}`, {
          name: f.name.trim(), prompt: f.prompt.trim(), trigger,
          start_date: f.startDate || undefined, end_date: f.endDate || undefined,
        });
        toast.success("已保存修改");
      }
      onSaved();
      onClose();
    } catch (e: any) {
      toast.error(e.message || "保存失败");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Modal open={open} onClose={onClose} title={initial.mode === "create" ? "添加自动化任务" : "编辑自动化任务"} wide>
      <div className="grid max-h-[70vh] gap-4 overflow-y-auto pr-1">
        <div>
          <label className="mb-1 block text-sm text-muted-foreground">名称</label>
          <Input value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} placeholder="给这个自动化任务起个名字" />
        </div>
        <div>
          <label className="mb-1 block text-sm text-muted-foreground">提示词</label>
          <textarea
            value={f.prompt}
            onChange={(e) => setF({ ...f, prompt: e.target.value })}
            placeholder="像对话一样描述要采集分析什么，例如：采集汽车之家上小米SU7的最新评论并输出口碑分析"
            rows={3}
            className="flex w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-sm placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </div>

        <div>
          <label className="mb-1 block text-sm text-muted-foreground">执行频率</label>
          <div className="mb-2 flex gap-1.5">
            {([["cron", "周期"], ["interval", "按间隔"], ["once", "单次"]] as [FreqMode, string][]).map(([m, label]) => (
              <Button key={m} type="button" size="sm" variant={f.freqMode === m ? "default" : "outline"}
                onClick={() => setF({ ...f, freqMode: m })}>
                {label}
              </Button>
            ))}
          </div>

          {f.freqMode === "cron" && (
            <div className="rounded-lg border border-border p-3">
              <div className="mb-2 flex gap-1.5">
                {([["daily", "每天"], ["weekly", "每周"], ["monthly", "每月"], ["advanced", "高级"]] as [CronMode, string][]).map(([m, label]) => (
                  <Button key={m} type="button" size="sm" variant={f.cronMode === m ? "secondary" : "ghost"}
                    onClick={() => setF({ ...f, cronMode: m })}>
                    {label}
                  </Button>
                ))}
              </div>
              {f.cronMode === "advanced" ? (
                <Input value={f.advancedCron} onChange={(e) => setF({ ...f, advancedCron: e.target.value })}
                  placeholder="分 时 日 月 周，例如 30 9 * * 1,3,5" className="font-mono text-xs" />
              ) : (
                <div className="flex flex-wrap items-center gap-3">
                  {f.cronMode === "weekly" && (
                    <div className="flex gap-1">
                      {WEEKDAY_NAMES.map((name, idx) => (
                        <button key={idx} type="button" onClick={() => toggleWeekday(idx)}
                          className={`h-7 w-7 rounded-md text-xs transition-colors ${f.weekdays.includes(idx) ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground hover:bg-accent"}`}>
                          {name}
                        </button>
                      ))}
                    </div>
                  )}
                  {f.cronMode === "monthly" && (
                    <div className="flex items-center gap-1.5 text-sm">
                      每月
                      <Input type="number" min={1} max={31} value={f.monthDay}
                        onChange={(e) => setF({ ...f, monthDay: Number(e.target.value) || 1 })}
                        className="w-16" />
                      号
                    </div>
                  )}
                  <Input type="time" value={f.time} onChange={(e) => setF({ ...f, time: e.target.value })} className="w-28" />
                </div>
              )}
            </div>
          )}

          {f.freqMode === "interval" && (
            <div className="flex items-center gap-1.5 rounded-lg border border-border p-3 text-sm">
              每
              <Input type="number" min={1} value={f.intervalValue}
                onChange={(e) => setF({ ...f, intervalValue: Number(e.target.value) || 1 })} className="w-20" />
              <select value={f.intervalUnit} onChange={(e) => setF({ ...f, intervalUnit: e.target.value as "minutes" | "hours" })}
                className="h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <option value="minutes">分钟</option>
                <option value="hours">小时</option>
              </select>
              执行一次
            </div>
          )}

          {f.freqMode === "once" && (
            <div className="rounded-lg border border-border p-3">
              <Input type="datetime-local" value={f.runAt} onChange={(e) => setF({ ...f, runAt: e.target.value })} />
            </div>
          )}
        </div>

        <div>
          <label className="mb-1 block text-sm text-muted-foreground">生效日期区间（可选，留空则始终生效）</label>
          <div className="flex items-center gap-2">
            <Input type="date" value={f.startDate} onChange={(e) => setF({ ...f, startDate: e.target.value })} />
            <span className="text-muted-foreground">至</span>
            <Input type="date" value={f.endDate} onChange={(e) => setF({ ...f, endDate: e.target.value })} />
          </div>
        </div>
      </div>
      <div className="mt-4 flex justify-end gap-2">
        <Button variant="outline" size="sm" onClick={onClose}>取消</Button>
        <Button size="sm" onClick={save} disabled={saving}>{saving ? "保存中…" : "保存"}</Button>
      </div>
    </Modal>
  );
}

/** 添加自动化的第一步：挑模板或空白创建。收进弹窗而不是常驻页面，
 * 避免任务多起来时把模板挤到瀑布流底部——模板只在“要创建”这一刻才需要看见。 */
function TemplatePickerModal({
  open, onClose, templates, onPick,
}: {
  open: boolean;
  onClose: () => void;
  templates: Template[];
  onPick: (tpl?: Template) => void;
}) {
  return (
    <Modal open={open} onClose={onClose} title="添加自动化任务" wide>
      <div className="grid max-h-[70vh] grid-cols-1 gap-3 overflow-y-auto pr-1 sm:grid-cols-2 lg:grid-cols-3">
        <Card className="cursor-pointer border-dashed transition-colors hover:border-primary/50 hover:bg-primary/[0.03]"
          onClick={() => onPick(undefined)}>
          <CardContent className="flex items-center gap-3 p-4">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-muted text-muted-foreground">
              <Plus className="h-4 w-4" />
            </div>
            <div>
              <p className="text-sm font-medium">空白创建</p>
              <p className="mt-0.5 text-xs text-muted-foreground">从头填写，不使用模板</p>
            </div>
          </CardContent>
        </Card>
        {templates.map((tpl) => {
          const Icon = TEMPLATE_ICONS[tpl.id] || Sparkles;
          return (
            <Card key={tpl.id} className="cursor-pointer transition-colors hover:border-primary/50 hover:bg-primary/[0.03]"
              onClick={() => onPick(tpl)}>
              <CardContent className="flex items-start gap-3 p-4">
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/12 text-primary">
                  <Icon className="h-4 w-4" />
                </div>
                <div className="min-w-0">
                  <p className="text-sm font-medium">{tpl.name}</p>
                  <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">{tpl.description}</p>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </Modal>
  );
}

// ---------- 主页面 ----------

const TASK_PAGE_SIZE = 10;
const RUNS_PAGE_SIZE = 20;

export function Tasks() {
  const [tab, setTab] = useState<"scheduled" | "runs">("scheduled");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [pickerOpen, setPickerOpen] = useState(false);
  const [runningNow, setRunningNow] = useState<Set<string>>(new Set());

  // 定时任务列表：搜索 + 状态筛选 + 前端分页（任务量级有限，一次拉全量本地过滤足够）
  const [taskQuery, setTaskQuery] = useState("");
  const [taskStatusFilter, setTaskStatusFilter] = useState<"all" | "active" | "paused">("all");
  const [taskPage, setTaskPage] = useState(1);

  // 运行记录：按任务/成败/关键词筛选 + 后端分页（记录会无限增长，不能一次性全拉）
  const [recentRuns, setRecentRuns] = useState<RecentRun[]>([]);
  const [recentRunsLoading, setRecentRunsLoading] = useState(false);
  const [runsTotal, setRunsTotal] = useState(0);
  const [runsTaskFilter, setRunsTaskFilter] = useState("");
  const [runsSuccessFilter, setRunsSuccessFilter] = useState<"all" | "success" | "failed">("all");
  const [runsQuery, setRunsQuery] = useState("");
  const [runsPage, setRunsPage] = useState(1);

  const [runsLoading, setRunsLoading] = useState(false);

  // 报告查看弹窗：既服务「某任务的执行历史」也服务「运行记录 Tab 里直接查看」
  const [historyFor, setHistoryFor] = useState<Task | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [reading, setReading] = useState<{ taskId: string; runAt: string; content: string } | null>(null);

  const [formState, setFormState] = useState<
    { mode: "create"; form: FormState } | { mode: "edit"; taskId: string; form: FormState } | null
  >(null);

  const load = () => {
    setLoading(true);
    api.get("/api/tasks").then(setTasks).catch(() => {}).finally(() => setLoading(false));
  };
  useEffect(load, []);
  useEffect(() => {
    api.get("/api/tasks/templates").then(setTemplates).catch(() => {});
  }, []);

  const filteredTasks = tasks.filter((t) => {
    if (taskStatusFilter === "active" && t.status === "paused") return false;
    if (taskStatusFilter === "paused" && t.status !== "paused") return false;
    if (taskQuery.trim()) {
      const hay = `${t.name || ""} ${t.user_input}`.toLowerCase();
      if (!hay.includes(taskQuery.trim().toLowerCase())) return false;
    }
    return true;
  });
  const taskTotalPages = Math.max(1, Math.ceil(filteredTasks.length / TASK_PAGE_SIZE));
  const pagedTasks = filteredTasks.slice((taskPage - 1) * TASK_PAGE_SIZE, taskPage * TASK_PAGE_SIZE);
  useEffect(() => setTaskPage(1), [taskQuery, taskStatusFilter]);
  useEffect(() => {
    if (taskPage > taskTotalPages) setTaskPage(taskTotalPages);
  }, [taskPage, taskTotalPages]);

  const loadRecentRuns = () => {
    setRecentRunsLoading(true);
    const params = new URLSearchParams();
    if (runsTaskFilter) params.set("task_id", runsTaskFilter);
    if (runsSuccessFilter !== "all") params.set("success", runsSuccessFilter === "success" ? "true" : "false");
    if (runsQuery.trim()) params.set("q", runsQuery.trim());
    params.set("limit", String(RUNS_PAGE_SIZE));
    params.set("offset", String((runsPage - 1) * RUNS_PAGE_SIZE));
    api.get(`/api/tasks/runs/recent?${params.toString()}`)
      .then((res) => {
        setRecentRuns(res.items);
        setRunsTotal(res.total);
      })
      .catch(() => {})
      .finally(() => setRecentRunsLoading(false));
  };
  useEffect(() => setRunsPage(1), [runsTaskFilter, runsSuccessFilter, runsQuery]);
  useEffect(() => {
    if (tab !== "runs") return;
    const timer = setTimeout(loadRecentRuns, runsQuery ? 300 : 0);
    return () => clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, runsPage, runsTaskFilter, runsSuccessFilter, runsQuery]);
  const runsTotalPages = Math.max(1, Math.ceil(runsTotal / RUNS_PAGE_SIZE));

  const cancel = async (id: string) => {
    try {
      await api.del(`/api/tasks/${id}`);
      toast.success("已删除定时任务");
      setTasks((t) => t.filter((x) => x.task_id !== id));
    } catch (e: any) {
      toast.error(e.message || "删除失败");
    }
  };

  const toggleEnabled = async (t: Task) => {
    const next = t.status === "paused" ? "active" : "paused";
    setTasks((ts) => ts.map((x) => (x.task_id === t.task_id ? { ...x, status: next } : x)));
    try {
      await api.patch(`/api/tasks/${t.task_id}`, { status: next });
      toast.success(next === "paused" ? "已暂停" : "已恢复");
      load();
    } catch (e: any) {
      toast.error(e.message || "操作失败");
      setTasks((ts) => ts.map((x) => (x.task_id === t.task_id ? { ...x, status: t.status } : x)));
    }
  };

  const runNow = async (t: Task) => {
    setRunningNow((s) => new Set(s).add(t.task_id));
    try {
      await api.post(`/api/tasks/${t.task_id}/run_now`);
      toast.success("已开始执行，完成后可在「运行记录」中查看");
    } catch (e: any) {
      toast.error(e.status === 409 ? "任务正在执行中，请稍候" : e.message || "执行失败");
    } finally {
      setRunningNow((s) => {
        const n = new Set(s);
        n.delete(t.task_id);
        return n;
      });
    }
  };

  const openCreate = (template?: Template) => {
    const form = template
      ? { ...EMPTY_FORM, name: template.name, prompt: template.prompt,
          freqMode: (template.trigger_type as FreqMode) || "cron",
          ...(template.cron_expr ? parseCronToForm(template.cron_expr) : {}) }
      : EMPTY_FORM;
    setFormState({ mode: "create", form });
  };

  const pickTemplate = (tpl?: Template) => {
    setPickerOpen(false);
    openCreate(tpl);
  };

  const openEdit = (t: Task) => {
    setFormState({ mode: "edit", taskId: t.task_id, form: taskToForm(t) });
  };

  const openHistory = async (t: Task) => {
    setHistoryFor(t);
    setReading(null);
    setRunsLoading(true);
    try {
      setRuns(await api.get(`/api/tasks/${t.task_id}/runs`));
    } catch (e: any) {
      toast.error(e.message || "读取执行历史失败");
      setRuns([]);
    } finally {
      setRunsLoading(false);
    }
  };

  const readReport = async (taskId: string, runId: number, runAt: string) => {
    try {
      const res = await api.get(`/api/tasks/${taskId}/runs/${runId}/report`);
      setReading({ taskId, runAt, content: res.content });
    } catch (e: any) {
      toast.error(e.message || "读取报告失败");
    }
  };

  const download = async (taskId: string, r: Run, kind: "report" | "json") => {
    const ts = r.run_at.replace(/[-:]/g, "").replace("T", "_");
    try {
      await downloadFile(
        `/api/tasks/${taskId}/runs/${r.run_id}/download?kind=${kind}`,
        `${kind}_${ts}${kind === "report" ? ".md" : ".json"}`,
      );
    } catch (e: any) {
      toast.error(e.message || "下载失败");
    }
  };

  const closeHistory = () => {
    setHistoryFor(null);
    setReading(null);
  };

  return (
    <>
      <header className="flex items-center justify-between border-b border-border px-7 py-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">任务中心</h1>
          <p className="text-sm text-muted-foreground">对话中说出定时需求会自动创建，也可以手动添加或从模板快速开始</p>
        </div>
        <div className="flex items-center gap-2">
          <Button variant="outline" size="sm" onClick={tab === "scheduled" ? load : loadRecentRuns} className="gap-1.5">
            <RefreshCw className="h-4 w-4" /> 刷新
          </Button>
          <Button size="sm" onClick={() => setPickerOpen(true)} className="gap-1.5">
            <Plus className="h-4 w-4" /> 添加自动化
          </Button>
        </div>
      </header>

      <div className="flex gap-1.5 border-b border-border px-7 pt-3">
        <Button variant={tab === "scheduled" ? "secondary" : "ghost"} size="sm" onClick={() => setTab("scheduled")} className="gap-1.5">
          <CalendarClock className="h-3.5 w-3.5" /> 定时任务
        </Button>
        <Button variant={tab === "runs" ? "secondary" : "ghost"} size="sm" onClick={() => setTab("runs")} className="gap-1.5">
          <ListChecks className="h-3.5 w-3.5" /> 运行记录
        </Button>
      </div>

      <div className="flex-1 overflow-y-auto px-7 py-6">
        {tab === "scheduled" ? (
          <>
            {loading ? (
              <p className="text-sm text-muted-foreground">加载中…</p>
            ) : !tasks.length ? (
              <div className="mx-auto max-w-md py-12 text-center">
                <CalendarClock className="mx-auto mb-3 h-10 w-10 text-muted-foreground/40" />
                <p className="mb-4 text-sm text-muted-foreground">
                  暂无进行中的定时任务。
                  <br />在对话工作区说「每周一三五 9:30 抓某站标讯」即可自动创建，也可以手动添加或从模板快速开始。
                </p>
                <Button size="sm" onClick={() => setPickerOpen(true)} className="gap-1.5">
                  <Plus className="h-4 w-4" /> 添加自动化
                </Button>
              </div>
            ) : (
              <>
                <div className="mb-3 flex flex-wrap items-center gap-2">
                  <div className="relative">
                    <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                    <Input value={taskQuery} onChange={(e) => setTaskQuery(e.target.value)}
                      placeholder="搜索任务名称或提示词" className="w-56 pl-8" />
                  </div>
                  <div className="flex gap-1">
                    {([["all", "全部"], ["active", "启用中"], ["paused", "已暂停"]] as const).map(([v, label]) => (
                      <Button key={v} size="sm" variant={taskStatusFilter === v ? "secondary" : "ghost"}
                        onClick={() => setTaskStatusFilter(v)}>
                        {label}
                      </Button>
                    ))}
                  </div>
                </div>

                {!filteredTasks.length ? (
                  <p className="py-12 text-center text-sm text-muted-foreground">没有符合条件的任务。</p>
                ) : (
                  <div className="grid gap-3">
                {pagedTasks.map((t) => (
                  <Card key={t.task_id} className="animate-fade-in">
                    <CardContent className="flex items-start justify-between gap-4 p-4">
                      <div className="min-w-0 flex-1">
                        <div className="mb-1.5 flex flex-wrap items-center gap-2">
                          <p className="truncate text-sm font-medium">{t.name || t.user_input}</p>
                          <Badge variant="outline">{SOURCE_LABEL[t.source || "auto"] || "自动识别"}</Badge>
                          {t.status === "paused" && <Badge variant="secondary">已暂停</Badge>}
                          {t.last_run_at && (
                            t.last_success ? (
                              <span className="inline-flex items-center gap-1 text-xs text-emerald-500">
                                <CheckCircle2 className="h-3.5 w-3.5" /> 上次成功
                              </span>
                            ) : (
                              <span className="inline-flex items-center gap-1 text-xs text-destructive">
                                <XCircle className="h-3.5 w-3.5" /> 上次失败
                              </span>
                            )
                          )}
                        </div>
                        <p className="truncate text-xs text-muted-foreground" title={t.user_input}>{t.user_input}</p>
                        <div className="mt-1.5 flex flex-wrap gap-4 text-xs text-muted-foreground">
                          <code className="rounded bg-muted px-1.5 py-0.5 font-mono">{describeTrigger(t)}</code>
                          <span className="inline-flex items-center gap-1">
                            <Clock className="h-3 w-3" /> 下次：{t.next_run_at || "—"}
                          </span>
                          <span>已执行 {t.run_count} 次</span>
                          {(t.start_date || t.end_date) && (
                            <span>生效期 {t.start_date || "…"} ~ {t.end_date || "…"}</span>
                          )}
                        </div>
                        {t.last_run_at && lastSummary(t) && (
                          <p
                            className={`mt-1 truncate text-xs ${t.last_success ? "text-muted-foreground" : "text-destructive/80"}`}
                            title={lastSummary(t)}
                          >
                            上次（{t.last_run_at}）：{lastSummary(t)}
                          </p>
                        )}
                      </div>
                      <div className="flex shrink-0 flex-wrap items-center justify-end gap-1">
                        <Toggle
                          checked={t.status !== "paused"}
                          title={t.status === "paused" ? "已暂停，点击恢复" : "启用中，点击暂停"}
                          onChange={() => toggleEnabled(t)}
                        />
                        <Button variant="outline" size="sm" className="gap-1.5" disabled={runningNow.has(t.task_id)}
                          onClick={() => runNow(t)} title="立即执行一次">
                          <Play className="h-3.5 w-3.5" /> 立即执行
                        </Button>
                        <Button variant="outline" size="sm" className="gap-1.5" onClick={() => openEdit(t)}>
                          <Pencil className="h-3.5 w-3.5" /> 编辑
                        </Button>
                        {t.run_count > 0 && (
                          <Button variant="outline" size="sm" className="gap-1.5" onClick={() => openHistory(t)}>
                            <FileText className="h-3.5 w-3.5" /> 历史
                          </Button>
                        )}
                        <Button variant="ghost" size="icon" onClick={() => cancel(t.task_id)} title="删除任务"
                          className="text-muted-foreground hover:text-destructive">
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </CardContent>
                  </Card>
                ))}
                  </div>
                )}

                {taskTotalPages > 1 && (
                  <div className="mt-4 flex items-center justify-center gap-3 text-sm text-muted-foreground">
                    <Button variant="outline" size="sm" disabled={taskPage <= 1}
                      onClick={() => setTaskPage((p) => p - 1)} className="gap-1">
                      <ChevronLeft className="h-3.5 w-3.5" /> 上一页
                    </Button>
                    第 {taskPage} / {taskTotalPages} 页 · 共 {filteredTasks.length} 条
                    <Button variant="outline" size="sm" disabled={taskPage >= taskTotalPages}
                      onClick={() => setTaskPage((p) => p + 1)} className="gap-1">
                      下一页 <ChevronRight className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                )}
              </>
            )}
          </>
        ) : (
          <>
            <div className="mb-3 flex flex-wrap items-center gap-2">
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input value={runsQuery} onChange={(e) => setRunsQuery(e.target.value)}
                  placeholder="搜索执行摘要关键词" className="w-56 pl-8" />
              </div>
              <select value={runsTaskFilter} onChange={(e) => setRunsTaskFilter(e.target.value)}
                className="h-9 rounded-md border border-input bg-background px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <option value="">全部任务</option>
                {tasks.map((t) => (
                  <option key={t.task_id} value={t.task_id}>{t.name || t.user_input.slice(0, 20)}</option>
                ))}
              </select>
              <div className="flex gap-1">
                {([["all", "全部"], ["success", "成功"], ["failed", "失败"]] as const).map(([v, label]) => (
                  <Button key={v} size="sm" variant={runsSuccessFilter === v ? "secondary" : "ghost"}
                    onClick={() => setRunsSuccessFilter(v)}>
                    {label}
                  </Button>
                ))}
              </div>
            </div>

            <div className="grid gap-2">
            {recentRunsLoading ? (
              <p className="text-sm text-muted-foreground">加载中…</p>
            ) : !recentRuns.length ? (
              <p className="py-12 text-center text-sm text-muted-foreground">没有符合条件的执行记录。</p>
            ) : (
              recentRuns.map((r) => (
                <div key={`${r.task_id}-${r.run_id}`}
                  className="flex items-center justify-between gap-3 rounded-lg border border-border px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2 text-sm">
                      {r.success ? (
                        <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
                      ) : (
                        <XCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />
                      )}
                      <span className="font-mono text-xs text-muted-foreground">{r.run_at}</span>
                      <span className="truncate font-medium">{r.task_name}</span>
                    </div>
                    <p className={`mt-0.5 truncate text-xs ${r.success ? "text-muted-foreground" : "text-destructive/80"}`}
                      title={runSummary(r)}>
                      {runSummary(r)}
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-1">
                    {r.has_report && (
                      <>
                        <Button variant="outline" size="sm" onClick={() => readReport(r.task_id, r.run_id, r.run_at)}>查看</Button>
                        <Button variant="ghost" size="icon" title="下载报告 (Markdown)"
                          onClick={() => download(r.task_id, r, "report")}>
                          <Download className="h-3.5 w-3.5" />
                        </Button>
                      </>
                    )}
                    {r.has_json && (
                      <Button variant="ghost" size="icon" title="下载采集数据 (JSON)"
                        onClick={() => download(r.task_id, r, "json")}>
                        <Braces className="h-3.5 w-3.5" />
                      </Button>
                    )}
                  </div>
                </div>
              ))
            )}
            </div>

            {runsTotalPages > 1 && (
              <div className="mt-4 flex items-center justify-center gap-3 text-sm text-muted-foreground">
                <Button variant="outline" size="sm" disabled={runsPage <= 1}
                  onClick={() => setRunsPage((p) => p - 1)} className="gap-1">
                  <ChevronLeft className="h-3.5 w-3.5" /> 上一页
                </Button>
                第 {runsPage} / {runsTotalPages} 页 · 共 {runsTotal} 条
                <Button variant="outline" size="sm" disabled={runsPage >= runsTotalPages}
                  onClick={() => setRunsPage((p) => p + 1)} className="gap-1">
                  下一页 <ChevronRight className="h-3.5 w-3.5" />
                </Button>
              </div>
            )}
          </>
        )}
      </div>

      <TemplatePickerModal
        open={pickerOpen}
        onClose={() => setPickerOpen(false)}
        templates={templates}
        onPick={pickTemplate}
      />

      <TaskFormModal
        open={!!formState}
        initial={formState}
        onClose={() => setFormState(null)}
        onSaved={() => {
          load();
          if (tab === "runs") loadRecentRuns();
        }}
      />

      {/* 单份报告直读（运行记录 Tab 里点「查看」） */}
      {reading && !historyFor && (
        <Modal open onClose={() => setReading(null)} title={`报告 · ${reading.runAt}`} wide>
          <div className="max-h-[70vh] overflow-y-auto pr-1">
            <Markdown>{reading.content}</Markdown>
          </div>
          <div className="mt-4 flex justify-end">
            <Button variant="outline" size="sm" onClick={() => setReading(null)}>关闭</Button>
          </div>
        </Modal>
      )}

      {/* 某任务的执行历史：列表 ↔ 单份报告阅读 */}
      <Modal
        open={!!historyFor}
        onClose={closeHistory}
        title={reading ? `报告 · ${reading.runAt}` : `执行历史 · ${historyFor?.name || historyFor?.user_input || ""}`}
        wide
      >
        {reading ? (
          <>
            <div className="max-h-[70vh] overflow-y-auto pr-1">
              <Markdown>{reading.content}</Markdown>
            </div>
            <div className="mt-4 flex justify-between">
              <Button variant="outline" size="sm" className="gap-1.5" onClick={() => setReading(null)}>
                <ArrowLeft className="h-3.5 w-3.5" /> 返回历史列表
              </Button>
              <Button variant="outline" size="sm" onClick={closeHistory}>关闭</Button>
            </div>
          </>
        ) : (
          <>
            <div className="max-h-[60vh] overflow-y-auto pr-1">
              {runsLoading ? (
                <p className="py-6 text-center text-sm text-muted-foreground">加载中…</p>
              ) : !runs.length ? (
                <p className="py-6 text-center text-sm text-muted-foreground">还没有执行历史。</p>
              ) : (
                <div className="grid gap-2">
                  {runs.map((r) => (
                    <div key={r.run_id}
                      className="flex items-center justify-between gap-3 rounded-lg border border-border px-3 py-2">
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 text-sm">
                          {r.success ? (
                            <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-500" />
                          ) : (
                            <XCircle className="h-3.5 w-3.5 shrink-0 text-destructive" />
                          )}
                          <span className="font-mono text-xs">{r.run_at}</span>
                        </div>
                        <p className={`mt-0.5 truncate text-xs ${r.success ? "text-muted-foreground" : "text-destructive/80"}`}
                          title={runSummary(r)}>
                          {runSummary(r)}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-1">
                        {r.has_report && (
                          <>
                            <Button variant="outline" size="sm" onClick={() => readReport(historyFor!.task_id, r.run_id, r.run_at)}>查看</Button>
                            <Button variant="ghost" size="icon" title="下载报告 (Markdown)"
                              onClick={() => download(historyFor!.task_id, r, "report")}>
                              <Download className="h-3.5 w-3.5" />
                            </Button>
                          </>
                        )}
                        {r.has_json && (
                          <Button variant="ghost" size="icon" title="下载采集数据 (JSON)"
                            onClick={() => download(historyFor!.task_id, r, "json")}>
                            <Braces className="h-3.5 w-3.5" />
                          </Button>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
            <div className="mt-4 flex justify-end">
              <Button variant="outline" size="sm" onClick={closeHistory}>关闭</Button>
            </div>
          </>
        )}
      </Modal>
    </>
  );
}
