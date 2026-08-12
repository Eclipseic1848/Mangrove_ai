import { useEffect, useRef, useState } from "react";
import {
  Plus, Send, Square, PanelRightOpen, PanelRightClose, Download, Database, Mail, Slack, FileText,
  CalendarClock, Trash2, Pencil, AlertTriangle, Award, Sparkles, MessageSquare,
  Copy, ThumbsUp, ThumbsDown,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Modal } from "@/components/ui/modal";
import { Markdown } from "@/components/Markdown";
import { PipelineTracker } from "@/components/PipelineTracker";
import { NodeStream, type NodeEntry } from "@/components/NodeStream";
import { api, downloadFile, streamChat } from "@/lib/api";
import { cn } from "@/lib/utils";

interface FileRef { name: string; url: string; mime: string }
interface Msg {
  role: "user" | "assistant";
  content: string;
  createdAt?: string;
  kind?: string;
  taskId?: string;
  files?: FileRef[];
  actions?: string[];
  schedule?: string;
  grade?: any;
  meta?: { collector?: string; item_count?: number; data_type?: string; record_counts?: Record<string, number>; quality?: any };
  id?: number;
  tokenUsage?: { prompt_tokens: number; completion_tokens: number; total_tokens: number; calls: number };
  feedback?: { rating: "up" | "down"; reasons?: string[]; comment?: string };
}

/** 时间戳格式化（本地化短格式）。 */
function fmtTime(iso?: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleString("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" });
}

/** 复制文本到剪贴板（http 非 secure context 下 navigator.clipboard 不可用，回退 execCommand）。 */
function copyText(text: string) {
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => toast.success("已复制")).catch(() => copyFallback(text));
    return;
  }
  copyFallback(text);
}
function copyFallback(text: string) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  try {
    document.execCommand("copy");
    toast.success("已复制");
  } catch {
    toast.error("复制失败");
  }
  document.body.removeChild(ta);
}
interface Conv { conv_id: string; title: string; updated_at: string }
interface ModelOpt { provider: string; model: string; label: string }

const ACTION_LABELS: Record<string, { label: string; icon: any; path: string }> = {
  db: { label: "确认入库", icon: Database, path: "/api/confirm/db" },
  email: { label: "确认发送邮件", icon: Mail, path: "/api/confirm/email" },
  slack: { label: "确认推送 Slack", icon: Slack, path: "/api/confirm/slack" },
  template: { label: "沉淀为模板", icon: Sparkles, path: "/api/confirm/template" },
};

const GRADE_VARIANT: Record<string, "success" | "default" | "warning" | "danger"> = {
  A: "success", B: "default", C: "warning", D: "danger",
};

// 点踩原因选项
const DISLIKE_REASONS = ["理解错误", "上下文错误", "回答不清晰", "代码错误", "回答不专业", "格式错误", "其他"];

export function Chat() {
  const [convs, setConvs] = useState<Conv[]>([]);
  const [convId, setConvId] = useState<string | null>(null);
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [running, setRunning] = useState(false);
  const [bgRunning, setBgRunning] = useState(false); // 会话有任务在后台执行（断开 SSE 后仍继续）
  const [doneNodes, setDoneNodes] = useState<Set<string>>(new Set());
  const [nodeEntries, setNodeEntries] = useState<NodeEntry[]>([]);
  const [activeNode, setActiveNode] = useState<string | null>(null);
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [models, setModels] = useState<ModelOpt[]>([]);
  const [sel, setSel] = useState<string>("");
  const [dialog, setDialog] = useState<{ type: "delete" | "rename"; conv: Conv } | null>(null);
  const [renameVal, setRenameVal] = useState("");
  const cancelRef = useRef<(() => void) | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const [dislikeTarget, setDislikeTarget] = useState<number | null>(null);
  const [dislikeReasons, setDislikeReasons] = useState<string[]>([]);
  const [dislikeComment, setDislikeComment] = useState("");
  // 数据准备 / 旧分析 模式切换（6B：默认 data_prep，可回退 legacy_analysis）
  const [mode, setMode] = useState<"data_prep" | "legacy_analysis">("data_prep");

  // 初始化：会话列表 + 模型目录
  useEffect(() => {
    api.get("/api/conversations").then(setConvs).catch(() => {});
  }, []);

  // 模型列表：初载 + 配置变更后自动刷新（无需手动刷新页面）
  const refreshModels = () => {
    api.get("/api/models").then((d) => {
      setModels(d.options || []);
      if (d.default) setSel(`${d.default.provider}::${d.default.model}`);
    }).catch(() => {});
  };
  useEffect(() => {
    refreshModels();
    const onCfg = () => refreshModels();
    window.addEventListener("mangrove:config-changed", onCfg);
    return () => window.removeEventListener("mangrove:config-changed", onCfg);
  }, []);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, running]);

  const fetchMessages = async (id: string) => {
    const [msgs, fbRes] = await Promise.all([
      api.get(`/api/conversations/${id}/messages`),
      api.get(`/api/chat/feedback?conv_id=${encodeURIComponent(id)}`).catch(() => ({ feedback: {} })),
    ]);
    const fbMap = fbRes.feedback || {};
    setMessages(
      msgs.map((m: any) => ({
        id: m.id,
        role: m.role,
        content: m.content,
        createdAt: m.created_at,
        taskId: m.task_id || undefined,
        kind: m.meta?.kind,
        files: m.meta?.files, // 重载后仍可下载（后端按磁盘解析）
        grade: m.meta?.grade,
        meta: m.meta
          ? { collector: m.meta.collector, item_count: m.meta.item_count, data_type: m.meta.data_type }
          : undefined,
        tokenUsage: m.meta?.token_usage,
        feedback: fbMap[m.id],
        // 注意：HITL 动作(actions/schedule)为一次性、不持久化，重载后不再出现
      })),
    );
  };

  const loadConv = async (id: string) => {
    setConvId(id);
    cancelRef.current?.();
    setRunning(false);
    setBgRunning(false);
    try {
      await fetchMessages(id);
      // 该会话若有任务仍在后台执行（切页面/刷新后任务不中断），亮提示并轮询等结果
      api.get(`/api/chat/running/${id}`).then((r) => setBgRunning(!!r.running)).catch(() => {});
    } catch {
      toast.error("加载会话失败");
    }
  };

  // 后台任务轮询：执行完自动刷新会话消息（结果由后端落库，无需保持 SSE 连接）
  useEffect(() => {
    if (!bgRunning || !convId) return;
    const timer = setInterval(async () => {
      try {
        const r = await api.get(`/api/chat/running/${convId}`);
        if (!r.running) {
          setBgRunning(false);
          await fetchMessages(convId);
          toast.success("后台任务已完成，结果已更新");
        }
      } catch { /* 瞬时失败忽略，下轮再查 */ }
    }, 4000);
    return () => clearInterval(timer);
  }, [bgRunning, convId]);

  const newChat = () => {
    cancelRef.current?.();
    setRunning(false);
    setBgRunning(false);
    setConvId(null);
    setMessages([]);
    setDoneNodes(new Set());
  };

  const doDelete = async () => {
    if (!dialog) return;
    const id = dialog.conv.conv_id;
    try {
      await api.del(`/api/conversations/${id}`);
      setConvs((cs) => cs.filter((c) => c.conv_id !== id));
      if (convId === id) newChat(); // 删除的是当前会话则清空视图
      toast.success("会话已删除");
    } catch (err: any) {
      toast.error(err.message || "删除失败");
    }
    setDialog(null);
  };

  const doRename = async () => {
    if (!dialog) return;
    const id = dialog.conv.conv_id;
    const title = renameVal.trim() || "新会话";
    try {
      await api.patch(`/api/conversations/${id}`, { title });
      setConvs((cs) => cs.map((c) => (c.conv_id === id ? { ...c, title } : c)));
      toast.success("已重命名");
    } catch (err: any) {
      toast.error(err.message || "重命名失败");
    }
    setDialog(null);
  };

  const send = () => {
    const content = input.trim();
    if (!content || running) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content, createdAt: new Date().toISOString() }]);
    setDoneNodes(new Set());
    setNodeEntries([]);
    setActiveNode(null);
    setRunning(true);
    setDrawerOpen(true);

    const [provider, model] = sel.split("::");
    cancelRef.current = streamChat(
      { conv_id: convId, content, provider, model, mode },
      {
        onMeta: (d) => {
          if (!convId) {
            setConvId(d.conv_id);
            api.get("/api/conversations").then(setConvs).catch(() => {});
          }
        },
        onNode: (d) => {
          setDoneNodes((prev) => new Set(prev).add(d.node));
          setNodeEntries((prev) =>
            prev.some((e) => e.node === d.node)
              ? prev.map((e) => (e.node === d.node ? { ...e, view: d.view, label: d.label } : e))
              : [...prev, { node: d.node, label: d.label, view: d.view }],
          );
          setActiveNode(d.node);
        },
        onResult: (r) => {
          setMessages((m) => [
            ...m,
            {
              role: "assistant",
              content: r.analysis ? `${r.reply}\n\n---\n\n${r.analysis}` : r.reply,
              createdAt: new Date().toISOString(),
              kind: r.kind,
              taskId: r.task_id,
              files: r.files,
              actions: r.actions,
              schedule: r.schedule,
              grade: r.grade,
              meta: { collector: r.collector, item_count: r.item_count, data_type: r.data_type, record_counts: r.record_counts, quality: r.quality },
              id: r.message_id,
              tokenUsage: r.token_usage || undefined,
            },
          ]);
        },
        onError: (e) => {
          toast.error(e.message);
          setMessages((m) => [
            ...m,
            { role: "assistant", content: `❌ ${e.message}`, kind: "error", createdAt: new Date().toISOString() },
          ]);
        },
        onDone: () => { setRunning(false); setActiveNode(null); },
      },
    );
  };

  /** 取消当前正在执行的任务。 */
  const cancel = async () => {
    cancelRef.current?.();                    // 断 SSE 流（停止接收事件）
    if (convId) {                             // 通知后端取消 pipeline
      try { await api.post(`/api/chat/${convId}/cancel`); } catch { /* 静默 */ }
    }
    setRunning(false);
    setActiveNode(null);
  };

  /** 点赞/点踩：已选中同一反馈则取消，否则提交。 */
  const submitFeedback = async (msgIdx: number, rating: "up" | "down") => {
    const msg = messages[msgIdx];
    if (!msg?.id || !convId) return;
    if (msg.feedback?.rating === rating) {
      // 已是同一反馈 -> 取消
      try {
        await api.del(`/api/chat/feedback/${msg.id}`);
        setMessages((m) => m.map((mm, i) => (i === msgIdx ? { ...mm, feedback: undefined } : mm)));
      } catch (e: any) {
        toast.error(e.message || "取消失败");
      }
      return;
    }
    try {
      await api.post("/api/chat/feedback", { message_id: msg.id, conv_id: convId, rating });
      setMessages((m) => m.map((mm, i) => (i === msgIdx ? { ...mm, feedback: { rating } } : mm)));
    } catch (e: any) {
      toast.error(e.message || "反馈失败");
    }
  };

  /** 点踩：提交带原因与描述的负面反馈。 */
  const submitDislike = async () => {
    if (dislikeTarget === null) return;
    const idx = dislikeTarget;
    const msg = messages[idx];
    if (!msg?.id || !convId) return;
    try {
      const reasons = dislikeReasons.length ? dislikeReasons : undefined;
      const comment = dislikeComment.trim() || undefined;
      await api.post("/api/chat/feedback", {
        message_id: msg.id, conv_id: convId, rating: "down", reasons, comment,
      });
      setMessages((m) =>
        m.map((mm, i) => (i === idx ? { ...mm, feedback: { rating: "down" as const, reasons, comment } } : mm)),
      );
      setDislikeTarget(null);
      setDislikeReasons([]);
      setDislikeComment("");
      toast.success("已提交反馈");
    } catch (e: any) {
      toast.error(e.message || "提交失败");
    }
  };

  // HITL / 定时任务确认
  const runAction = async (msgIdx: number, action: string, taskId?: string) => {
    if (!taskId) return;
    try {
      let res: any;
      if (action === "schedule") res = await api.post("/api/tasks", { task_id: taskId });
      else res = await api.post(ACTION_LABELS[action].path, { task_id: taskId });
      toast.success(res.message || (action === "schedule" ? `已创建定时任务（下次 ${res.next_run_at}）` : "已完成"));
      // 移除已消费的动作按钮
      setMessages((m) =>
        m.map((msg, i) =>
          i === msgIdx
            ? { ...msg, actions: msg.actions?.filter((a) => a !== action), schedule: action === "schedule" ? undefined : msg.schedule }
            : msg,
        ),
      );
    } catch (e: any) {
      toast.error(e.message || "操作失败");
    }
  };

  const lastRun = messages.length && messages[messages.length - 1].role === "assistant"
    ? messages[messages.length - 1] : null;

  return (
    <div className="flex h-full min-h-0">
      {/* 会话列表 */}
      <div className="flex w-56 shrink-0 flex-col border-r border-border">
        <div className="p-3">
          <Button onClick={newChat} variant="outline" className="w-full gap-1.5">
            <Plus className="h-4 w-4" /> 新建会话
          </Button>
        </div>
        <div className="flex-1 space-y-0.5 overflow-y-auto px-2 pb-2">
          {convs.map((c) => (
            <div
              key={c.conv_id}
              className={cn(
                "group flex items-center gap-1 rounded-md pr-1 transition-colors",
                convId === c.conv_id ? "bg-accent text-accent-foreground" : "text-muted-foreground hover:bg-accent/60",
              )}
            >
              <button
                onClick={() => loadConv(c.conv_id)}
                className="flex min-w-0 flex-1 items-center gap-2 px-2.5 py-2 text-left text-sm"
              >
                <MessageSquare className="h-3.5 w-3.5 shrink-0" />
                <span className="truncate">{c.title}</span>
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setRenameVal(c.title);
                  setDialog({ type: "rename", conv: c });
                }}
                className="shrink-0 rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-accent hover:text-foreground focus:opacity-100 group-hover:opacity-100"
                title="重命名"
              >
                <Pencil className="h-3.5 w-3.5" />
              </button>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setDialog({ type: "delete", conv: c });
                }}
                className="shrink-0 rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-destructive/15 hover:text-destructive focus:opacity-100 group-hover:opacity-100"
                title="删除会话"
              >
                <Trash2 className="h-3.5 w-3.5" />
              </button>
            </div>
          ))}
          {!convs.length && <p className="px-2 py-4 text-center text-xs text-muted-foreground">还没有会话</p>}
        </div>
      </div>

      {/* 对话区 */}
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between gap-3 border-b border-border px-5 py-3">
          <select
            value={sel}
            onChange={(e) => setSel(e.target.value)}
            className="h-8 max-w-[260px] truncate rounded-md border border-input bg-transparent px-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            {models.map((m) => (
              <option key={`${m.provider}::${m.model}`} value={`${m.provider}::${m.model}`}>
                {m.label}
              </option>
            ))}
          </select>
          <Button variant="ghost" size="icon" onClick={() => setDrawerOpen((o) => !o)} title="运行详情">
            {drawerOpen ? <PanelRightClose className="h-4 w-4" /> : <PanelRightOpen className="h-4 w-4" />}
          </Button>
        </header>

        <div ref={scrollRef} className="flex-1 overflow-y-auto px-5 py-6">
          <div className="mx-auto max-w-3xl space-y-5">
            {!messages.length && <Welcome onPick={(t) => setInput(t)} />}
            {messages.map((m, i) => (
              <MessageBubble
                key={i}
                msg={m}
                onAction={(a) => runAction(i, a, m.taskId)}
                onLike={() => submitFeedback(i, "up")}
                onDislike={() => {
                  if (m.feedback?.rating === "down") {
                    submitFeedback(i, "down");
                  } else {
                    setDislikeTarget(i);
                    setDislikeReasons(m.feedback?.reasons || []);
                    setDislikeComment(m.feedback?.comment || "");
                  }
                }}
              />
            ))}
            {(nodeEntries.length > 0 || running) && (
              <div className="rounded-2xl border border-border bg-card/40 p-3">
                <div className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
                  运行过程
                </div>
                <NodeStream entries={nodeEntries} activeNode={activeNode} running={running} doneNodes={doneNodes} />
              </div>
            )}
            {running && (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <span className="h-2 w-2 animate-pulse-dot rounded-full bg-primary" />
                正在处理…
              </div>
            )}
            {bgRunning && !running && (
              <div className="flex items-center gap-2 rounded-lg border border-border bg-card/60 px-4 py-3 text-sm text-muted-foreground">
                <span className="h-2 w-2 animate-pulse-dot rounded-full bg-primary" />
                该会话有任务正在后台执行，完成后结果会自动显示——可自由切换页面，任务不会中断。
              </div>
            )}
          </div>
        </div>

        {/* 输入框 */}
        <div className="border-t border-border px-5 py-4">
          <div className="mx-auto max-w-3xl">
            <div className="mb-2 flex items-center gap-1 text-xs">
              <span className="mr-1 text-muted-foreground">模式</span>
              <button
                onClick={() => setMode("data_prep")}
                className={cn("rounded-md px-2.5 py-1 font-medium transition-colors", mode === "data_prep" ? "bg-primary text-primary-foreground" : "bg-secondary/60 text-muted-foreground hover:bg-secondary")}
                title="采集->解析->清洗->质检->输出干净数据（默认）"
              >
                数据准备
              </button>
              <button
                onClick={() => setMode("legacy_analysis")}
                className={cn("rounded-md px-2.5 py-1 font-medium transition-colors", mode === "legacy_analysis" ? "bg-primary text-primary-foreground" : "bg-secondary/60 text-muted-foreground hover:bg-secondary")}
                title="旧链路：采集->分析->报告（兼容回退）"
              >
                分析报告（旧）
              </button>
            </div>
            <div className="flex items-end gap-2">
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  send();
                }
              }}
              rows={1}
              placeholder="描述你的采集/分析任务，例如：采集5条小米SU7的用户评价，分析用户口碑，生成 Markdown 报告"
              className="max-h-40 min-h-[44px] flex-1 resize-none rounded-lg border border-input bg-transparent px-3.5 py-2.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            />
            {running ? (
              <Button onClick={cancel} variant="destructive" size="icon" className="h-11 w-11 shrink-0" title="取消任务">
                <Square className="h-4 w-4 fill-current" />
              </Button>
            ) : (
              <Button onClick={send} disabled={!input.trim()} size="icon" className="h-11 w-11 shrink-0">
                <Send className="h-4 w-4" />
              </Button>
            )}
            </div>
          </div>
        </div>
      </div>

      {/* 运行抽屉 */}
      {drawerOpen && (
        <div className="flex w-80 shrink-0 flex-col overflow-y-auto border-l border-border bg-sidebar/40">
          <div className="border-b border-border px-4 py-3 text-sm font-semibold">运行详情</div>
          <div className="space-y-5 p-4">
            <div>
              <div className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">流水线</div>
              <PipelineTracker done={doneNodes} running={running} />
            </div>

            {lastRun?.meta && (lastRun.meta.collector || lastRun.meta.item_count != null) && (
              <div>
                <div className="mb-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">本次运行</div>
                <div className="space-y-1.5 text-sm">
                  {lastRun.meta.collector && (
                    <Row label="采集引擎"><Badge variant="secondary">{lastRun.meta.collector}</Badge></Row>
                  )}
                  {lastRun.meta.item_count != null && <Row label="数据条数">{lastRun.meta.item_count}</Row>}
                  {lastRun.meta.data_type && <Row label="数据类型">{lastRun.meta.data_type}</Row>}
                  {lastRun.grade?.grade && (
                    <Row label="质量分级">
                      <Badge variant={GRADE_VARIANT[lastRun.grade.grade] || "default"}>
                        <Award className="h-3 w-3" /> {lastRun.grade.grade}
                        {lastRun.grade.score != null ? ` · ${lastRun.grade.score}` : ""}
                      </Badge>
                    </Row>
                  )}
                </div>
                {Array.isArray(lastRun.grade?.anomalies) && lastRun.grade.anomalies.length > 0 && (
                  <div className="mt-3 rounded-md border border-amber-500/30 bg-amber-500/10 p-2.5">
                    <div className="mb-1 flex items-center gap-1.5 text-xs font-medium text-amber-500">
                      <AlertTriangle className="h-3.5 w-3.5" /> 异常检测
                    </div>
                    <ul className="space-y-0.5 text-xs text-muted-foreground">
                      {lastRun.grade.anomalies.map((a: string, i: number) => <li key={i}>· {a}</li>)}
                    </ul>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* 删除 / 重命名 弹窗 */}
      <Modal open={!!dialog} onClose={() => setDialog(null)} title={dialog?.type === "delete" ? "删除会话" : "重命名会话"}>
        {dialog?.type === "delete" ? (
          <>
            <p className="text-sm text-muted-foreground">
              确定删除会话「{dialog.conv.title}」？该会话的全部消息将一并删除，<span className="text-foreground">不可恢复</span>。
            </p>
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setDialog(null)}>取消</Button>
              <Button variant="destructive" size="sm" onClick={doDelete}>删除</Button>
            </div>
          </>
        ) : (
          <>
            <Input
              value={renameVal}
              onChange={(e) => setRenameVal(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && doRename()}
              placeholder="会话名称"
              autoFocus
            />
            <div className="mt-5 flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setDialog(null)}>取消</Button>
              <Button size="sm" onClick={doRename}>保存</Button>
            </div>
          </>
        )}
      </Modal>

      {/* 点踩反馈弹窗 */}
      <Modal open={dislikeTarget !== null} onClose={() => setDislikeTarget(null)} title="反馈：回答不满意">
        <div className="space-y-3 text-sm">
          <p className="text-muted-foreground">请选择原因（可多选）：</p>
          <div className="grid grid-cols-2 gap-2">
            {DISLIKE_REASONS.map((r) => (
              <label key={r} className="flex cursor-pointer items-center gap-2">
                <input
                  type="checkbox"
                  checked={dislikeReasons.includes(r)}
                  onChange={(e) =>
                    setDislikeReasons((prev) => (e.target.checked ? [...prev, r] : prev.filter((x) => x !== r)))
                  }
                />
                <span>{r}</span>
              </label>
            ))}
          </div>
          <textarea
            placeholder="补充说明（可选）"
            value={dislikeComment}
            onChange={(e) => setDislikeComment(e.target.value)}
            className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
            rows={3}
          />
          <div className="flex justify-end gap-2">
            <Button variant="outline" size="sm" onClick={() => setDislikeTarget(null)}>
              取消
            </Button>
            <Button size="sm" onClick={submitDislike}>
              提交
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium tabular-nums">{children}</span>
    </div>
  );
}

function MessageBubble({ msg, onAction, onLike, onDislike }: { msg: Msg; onAction: (a: string) => void; onLike: () => void; onDislike: () => void }) {
  if (msg.role === "user") {
    return (
      <div className="flex flex-col items-end gap-1">
        <div className="max-w-[85%] whitespace-pre-wrap rounded-2xl rounded-br-sm bg-primary px-4 py-2.5 text-sm text-primary-foreground">
          {msg.content}
        </div>
        {msg.createdAt && <span className="px-1 text-[11px] text-muted-foreground">{fmtTime(msg.createdAt)}</span>}
      </div>
    );
  }
  return (
    <div className="animate-fade-in space-y-3">
      <div className="rounded-2xl rounded-tl-sm border border-border bg-card px-4 py-3">
        <Markdown>{msg.content}</Markdown>
      </div>

      {/* 产出文件 */}
      {msg.files && msg.files.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {msg.files.map((f) => (
            <button
              key={f.name}
              onClick={() => downloadFile(f.url, f.name).catch(() => toast.error("下载失败"))}
              className="inline-flex items-center gap-1.5 rounded-md border border-border bg-secondary/60 px-3 py-1.5 text-xs font-medium transition-colors hover:bg-secondary"
            >
              <FileText className="h-3.5 w-3.5" /> {f.name}
              <Download className="h-3 w-3 text-muted-foreground" />
            </button>
          ))}
        </div>
      )}

      {/* HITL / 定时任务确认按钮 */}
      {(msg.actions?.length || msg.kind === "schedule") && (
        <div className="flex flex-wrap gap-2">
          {msg.kind === "schedule" && msg.schedule && (
            <Button size="sm" onClick={() => onAction("schedule")} className="gap-1.5">
              <CalendarClock className="h-3.5 w-3.5" /> 确认创建定时任务
            </Button>
          )}
          {msg.actions?.map((a) => {
            const cfg = ACTION_LABELS[a];
            if (!cfg) return null;
            return (
              <Button key={a} size="sm" variant={a === "template" ? "outline" : "default"} onClick={() => onAction(a)} className="gap-1.5">
                <cfg.icon className="h-3.5 w-3.5" /> {cfg.label}
              </Button>
            );
          })}
        </div>
      )}

      {/* 操作栏：复制 / 赞 / 踩 / token 用量 */}
      {msg.kind !== "error" && msg.kind !== "cancelled" && (
        <div className="flex items-center gap-1 px-1">
          <Button
            variant="ghost"
            size="icon"
            className="h-6 w-6 text-muted-foreground hover:text-foreground"
            onClick={() => copyText(msg.content)}
            title="复制"
          >
            <Copy className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className={cn("h-6 w-6", msg.feedback?.rating === "up" ? "text-green-500" : "text-muted-foreground hover:text-foreground")}
            onClick={onLike}
            title="点赞"
          >
            <ThumbsUp className="h-3.5 w-3.5" />
          </Button>
          <Button
            variant="ghost"
            size="icon"
            className={cn("h-6 w-6", msg.feedback?.rating === "down" ? "text-red-500" : "text-muted-foreground hover:text-foreground")}
            onClick={onDislike}
            title="点踩"
          >
            <ThumbsDown className="h-3.5 w-3.5" />
          </Button>
          {msg.tokenUsage && msg.tokenUsage.calls > 0 && (
            <span
              className="ml-auto text-[11px] text-muted-foreground"
              title={`输入 ${msg.tokenUsage.prompt_tokens} / 输出 ${msg.tokenUsage.completion_tokens} / 共 ${msg.tokenUsage.total_tokens}（${msg.tokenUsage.calls} 次调用）`}
            >
              🪙 {msg.tokenUsage.prompt_tokens}↑ / {msg.tokenUsage.completion_tokens}↓ · 共 {msg.tokenUsage.total_tokens}
            </span>
          )}
        </div>
      )}
      {msg.feedback?.rating === "down" && (
        <div className="px-1 text-[11px] text-muted-foreground">
          已反馈：{msg.feedback.reasons?.length ? msg.feedback.reasons.join("、") : "不满意"}
          {msg.feedback.comment ? ` · ${msg.feedback.comment}` : ""}
        </div>
      )}
      {msg.createdAt && <span className="block px-1 text-[11px] text-muted-foreground">{fmtTime(msg.createdAt)}</span>}
    </div>
  );
}

// 示例任务：每条对应一类核心能力，措辞均经全链路实测跑通（见 scripts/eval_e2e.py 黄金任务集）
const SAMPLES = [
  { tag: "口碑分析", text: "采集5条小米SU7的用户评价，分析用户口碑和主要槽点，生成 Markdown 报告" },
  { tag: "定向网页", text: "抓取这个网页的正文并总结今日财经要闻：https://finance.sina.com.cn/" },
  { tag: "站内检索", text: "去懂车帝搜集3条问界M9的资讯并总结要点" },
  { tag: "定时任务", text: "每周一三五 9:30 搜集3条医疗设备招标公告，整理成标讯报告" },
  { tag: "邮件推送", text: "采集3条关于新能源汽车销量的最新新闻，生成汇总报告并发邮件到 test@example.com" },
];

function Welcome({ onPick }: { onPick: (t: string) => void }) {
  return (
    <div className="mx-auto max-w-2xl py-10 text-center">
      <img src="/logo.svg" alt="" className="mx-auto mb-4 h-12 w-12" />
      <h2 className="text-xl font-semibold">你好，我是 howso@Mangrove</h2>
      <p className="mt-1.5 text-sm text-muted-foreground">
        用自然语言描述采集/分析任务，我会自动选择采集引擎、清洗、分析并按需产出。
      </p>
      <div className="mt-6 grid gap-2 text-left">
        {SAMPLES.map((s) => (
          <button
            key={s.text}
            onClick={() => onPick(s.text)}
            className="flex items-center gap-3 rounded-lg border border-border bg-card px-4 py-3 text-sm transition-colors hover:border-primary/50 hover:bg-accent/50"
          >
            <span className="shrink-0 rounded-md bg-primary/10 px-2 py-0.5 text-xs font-medium text-primary">{s.tag}</span>
            <span className="min-w-0 flex-1 truncate">{s.text}</span>
          </button>
        ))}
      </div>
    </div>
  );
}
