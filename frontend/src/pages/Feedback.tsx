import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { PageGuide } from "@/components/onboarding/PageGuide";
import { ThumbsUp, ThumbsDown, Download, RefreshCw, Filter, MessageSquare, MessagesSquare, BarChart3, Trash2, Clock } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Pagination } from "@/components/ui/pagination";
import { api, ApiError, authenticatedFetch, getSessionState, readAuthenticatedBlob, readAuthenticatedJson } from "@/lib/api";
import { isAdminish, useAuth } from "@/lib/auth";
import { beijingTime } from "@/lib/beijingTime";
import { MemoryConfirm as ConfirmDialog } from "@/components/memory/MemoryControls";
import * as Dialog from "@radix-ui/react-dialog";
import { Markdown } from "@/components/Markdown";
const FeedbackTaskContext = lazy(() => import('@/components/FeedbackTaskContext').then(module => ({ default: module.FeedbackTaskContext })));

// 与记忆页采用同一导航保护时序：先于 Router 拦截，且仅反馈页编辑期间接管。
let feedbackPop: ((event: PopStateEvent) => void) | null = null;
const dispatchFeedbackPop = (event: PopStateEvent) => feedbackPop?.(event);
window.addEventListener("popstate", dispatchFeedbackPop, true);
if (import.meta.hot) import.meta.hot.dispose(() => window.removeEventListener("popstate", dispatchFeedbackPop, true));

interface Overview {
  total_tasks?: number;
  total_up: number;
  total_down: number;
  total_pending: number;
  down_rate: number | null;
  reason_counts: Record<string, number>;
  daily: { date: string; up: number; down: number }[];
}

interface FeedbackItem {
  id: number;
  rating: "up" | "down";
  user_id: string;
  display_name: string | null;
  username: string | null;
  reasons: string[];
  created_at: string;
  status: "pending" | "resolved" | "ignored" | "fixed" | "no_change" | "deferred";
  has_comment: boolean;
  has_admin_note: boolean;
  content_available: boolean;
}

interface AuditContent {
  event_id: string;
  content: { question: string | null; answer: string | null; comment: string | null; admin_note: string | null; original_task?: string | null; task_title?: string | null; result_preview?: string | null };
  context?: { task_id?: string | null; revision?: number | null; result_id?: string | null; conv_id?: string | null; message_id?: number | null };
  truncated: boolean;
  content_bytes: number;
}

const REASON_OPTIONS = ["理解错误", "上下文错误", "回答不清晰", "代码错误", "回答不专业", "格式错误", "其他"];
const STATUS_META: Record<string, { label: string; variant: "warning" | "success" | "outline" }> = {
  pending: { label: "待处理", variant: "warning" },
  resolved: { label: "已处理", variant: "success" },
  ignored: { label: "已忽略", variant: "outline" },
  fixed: { label: "已修复", variant: "success" },
  no_change: { label: "无需修改", variant: "outline" },
  deferred: { label: "暂不处理", variant: "warning" },
};

/** 用户显示：@姓名/用户名（userID），姓名与用户名相同时只显示一个。 */
function userLabel(it: FeedbackItem): string {
  const name = it.display_name || it.username || it.user_id;
  const uname = it.username || it.user_id;
  return name !== uname ? `@${name}/${uname}（${it.user_id}）` : `@${uname}（${it.user_id}）`;
}

export function Feedback() {
  const { user } = useAuth();
  if (!user || !isAdminish(user.role)) return null;
  // 同一 Owner 降权也必须卸载临时正文，不能只依赖 API 的 Owner 代数。
  return <FeedbackPage key={`${user.user_id}:${user.role}`} actorId={user.user_id} actorRole={user.role} />;
}

function FeedbackPage({ actorId, actorRole }: { actorId: string; actorRole: string }) {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [overviewError, setOverviewError] = useState(false);
  const [items, setItems] = useState<FeedbackItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [revision, setRevision] = useState(0);
  // 筛选
  const [fRating, setFRating] = useState("");
  const [fReason, setFReason] = useState("");
  const [fFrom, setFFrom] = useState("");
  const [fTo, setFTo] = useState("");
  const [fUser, setFUser] = useState("");
  const [userQuery, setUserQuery] = useState("");
  const [composing, setComposing] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [fStatus, setFStatus] = useState("");
  // 操作弹框
  const [auditTarget, setAuditTarget] = useState<FeedbackItem | null>(null);
  const [auditReason, setAuditReason] = useState("");
  const [auditContent, setAuditContent] = useState<AuditContent | null>(null);
  const [auditError, setAuditError] = useState("");
  const [auditFailureEvent, setAuditFailureEvent] = useState("");
  const [auditBusy, setAuditBusy] = useState(false);
  const [auditAttempted, setAuditAttempted] = useState(false);
  const [resolveNote, setResolveNote] = useState("");
  const [resolution, setResolution] = useState<"" | "fixed" | "no_change" | "deferred">("");
  const [discardOpen, setDiscardOpen] = useState(false);
  const [saving, setSaving] = useState(false);
  const [restoringHistory, setRestoringHistory] = useState(false);
  const historyDestination = useRef<number | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<FeedbackItem | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const deleteFlight = useRef(false);
  const auditGeneration = useRef(0);
  const auditFlight = useRef(false);
  const reasonInput = useRef<HTMLTextAreaElement>(null);
  const detailTrigger = useRef<HTMLElement | null>(null);
  const mounted = useRef(true);
  const current = () => mounted.current && getSessionState().user?.user_id === actorId && getSessionState().user?.role === actorRole;
  const noteDirty = !!auditContent && (resolveNote !== (auditContent.content.admin_note || "") || !!resolution);
  const conclusionRequired = auditTarget?.rating === 'down' && ['fixed', 'no_change', 'deferred'].includes(auditTarget.status);
  useEffect(() => {
    if (!noteDirty && !saving) return;
    const index = window.history.state?.idx;
    let restoring = false;
    const pop = (event: PopStateEvent) => {
      const next = event.state?.idx;
      if (!Number.isInteger(index) || !Number.isInteger(next)) return;
      // 先恢复原历史位置，避免 Router 卸载尚未保存的私人备注；不将正文写入缓存。
      event.stopImmediatePropagation();
      if (next === index) { restoring = false; setRestoringHistory(false); return; }
      if (!restoring && !saving) { historyDestination.current = next - index; setDiscardOpen(true); }
      restoring = true; setRestoringHistory(true);
      window.history.go(index - next);
    };
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    feedbackPop = pop;
    window.addEventListener("beforeunload", protect);
    return () => { if (feedbackPop === pop) feedbackPop = null; window.removeEventListener("beforeunload", protect); };
  }, [noteDirty, saving]);
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; auditGeneration.current++; };
  }, []);

  const refresh = () => setRevision((value) => value + 1);
  const invalidDates = !!(fFrom && fTo && fFrom > fTo);
  useEffect(() => {
    if (composing) return;
    const timer = setTimeout(() => { setUserQuery(fUser.trim()); setPage(1); }, 300);
    return () => clearTimeout(timer);
  }, [fUser, composing]);
  const filters = new URLSearchParams();
  if (fRating) filters.set("rating", fRating);
  if (fReason) filters.set("reason", fReason);
  if (fFrom) filters.set("date_from", fFrom);
  if (fTo) filters.set("date_to", fTo);
  if (userQuery) filters.set("q", userQuery);
  if (fStatus) filters.set("status", fStatus);
  const filterQuery = filters.toString();

  useEffect(() => {
    let active = true;
    setOverviewError(false);
    api.get('/api/feedback/overview').then((value: Overview) => {
      if (active && current()) setOverview(value);
    }).catch(() => { if (active && current()) { setOverview(null); setOverviewError(true); } });
    return () => { active = false; };
  }, [revision]);

  useEffect(() => {
    if (invalidDates) { setLoading(false); return; }
    setLoading(true);
    setLoadError(false);
    let active = true;
    api.get(`/api/feedback/list?limit=${pageSize}&offset=${(page - 1) * pageSize}&${filterQuery}`, { signal: AbortSignal.timeout(15000) })
      .then((list: any) => {
        if (!active || !current()) return;
        setItems(list.items || []);
        setTotal(list.total || 0);
        const lastPage = Math.max(1, Math.ceil((list.total || 0) / pageSize));
        if (page > lastPage) setPage(lastPage);
      })
      .catch(() => { if (active && current()) setLoadError(true); })
      .finally(() => { if (active && current()) setLoading(false); });
    // 筛选与刷新只接受最新请求，旧结果不能覆盖当前列表。
    return () => { active = false; };
  }, [page, pageSize, filterQuery, revision, invalidDates]);

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const pageClamped = Math.min(page, totalPages);

  const doExport = async () => {
    if (exporting || invalidDates) return;
    setExporting(true);
    try {
      const res = await authenticatedFetch(`/api/feedback/export?${filterQuery}`);
      if (!res.ok) {
        const failure = await readAuthenticatedJson(res);
        throw new Error(failure.detail || "导出失败，请重试");
      }
      const blob = await readAuthenticatedBlob(res);
      if (!current()) return;
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "feedback.csv";
      a.click();
      URL.revokeObjectURL(a.href);
      toast.success("已导出当前筛选元数据");
    } catch (error) {
      if (current()) toast.error(error instanceof Error ? error.message : "导出失败，请重试");
    } finally { if (current()) setExporting(false); }
  };

  const closeAudit = () => {
    auditGeneration.current++;
    auditFlight.current = false;
    setAuditTarget(null);
    setAuditContent(null);
    setAuditReason("");
    setResolution("");
    setResolveNote("");
    setAuditError("");
    setAuditFailureEvent("");
    setAuditBusy(false);
    setAuditAttempted(false);
    setDiscardOpen(false);
  };

  const requestCloseAudit = () => {
    if (saving) return;
    if (noteDirty) setDiscardOpen(true);
    else closeAudit();
  };

  const openAudit = (it: FeedbackItem) => { detailTrigger.current = document.activeElement as HTMLElement; closeAudit(); setAuditTarget(it); };

  const submitAudit = async () => {
    const reason = auditReason.trim();
    if (!auditTarget || auditFlight.current || auditAttempted || reason.length < 5 || reason.length > 1000) return;
    const generation = auditGeneration.current;
    // 提交按钮禁用后仍把焦点留在弹窗内，Escape 和 Tab 才能继续工作。
    reasonInput.current?.focus();
    auditFlight.current = true;
    setAuditBusy(true);
    setAuditAttempted(true);
    try {
      const response = await authenticatedFetch(`/api/feedback/${auditTarget.id}/audit-content`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason, idempotency_key: crypto.randomUUID() }),
      });
      if (!current() || generation !== auditGeneration.current) return;
      // 失败事件只有服务端提交后才返回；没有事件号不能声称已留痕。
      const failedEvent = response.headers.get("X-Audit-Event-ID");
      if (response.status === 404 && failedEvent) {
        setAuditFailureEvent(failedEvent);
        setAuditError("正文不可用，访问失败已记录");
        return;
      }
      if (!response.ok) throw new ApiError(response.status, "审计查看失败");
      const result: AuditContent = await readAuthenticatedJson(response);
      if (!current() || generation !== auditGeneration.current) return;
      if (!result.event_id || !result.content) throw new Error("审计响应不完整");
      setAuditContent(result);
      setResolveNote(result.content.admin_note || "");
      setResolution("");
    } catch (error) {
      if (!current() || generation !== auditGeneration.current) return;
      setAuditError(error instanceof ApiError && error.status === 429
        ? "请求过于频繁，未显示正文；请稍后重新打开。"
        : "未取得可核实的审计结果，未显示正文；请关闭后核对，不会自动重试。");
    } finally {
      if (current() && generation === auditGeneration.current) { auditFlight.current = false; setAuditBusy(false); }
    }
  };

  const saveNote = async (status?: "fixed" | "no_change" | "deferred") => {
    if (!auditTarget || !auditContent || auditContent.truncated || auditFlight.current || resolveNote.length > 5000) return;
    if ((status || conclusionRequired) && !resolveNote.trim()) { toast.error("请填写处理结论"); return; }
    const generation = auditGeneration.current;
    auditFlight.current = true;
    setAuditBusy(true);
    setSaving(true);
    try {
      await api.patch(`/api/feedback/${auditTarget.id}`, { admin_note: resolveNote.trim() || null, ...(status ? { status } : {}) });
      if (!current() || generation !== auditGeneration.current) return;
      // 保存后重新核对内容，后续处理使用最新备注的审计证据。
      const saved: AuditContent = await api.post(`/api/feedback/${auditTarget.id}/audit-content`, { reason: auditReason, idempotency_key: crypto.randomUUID() });
      if (current() && generation === auditGeneration.current) {
        setAuditContent(saved);
        setResolveNote(saved.content.admin_note || "");
        if (status) setResolution("");
        if (status) setAuditTarget({ ...auditTarget, status });
        refresh(); toast.success(status ? `反馈${STATUS_META[status].label}` : "备注已保存");
      }
    } catch {
      if (current() && generation === auditGeneration.current) setAuditError("备注保存结果未确认，请关闭后重新审计核对。");
    } finally {
      if (current() && generation === auditGeneration.current) { auditFlight.current = false; setAuditBusy(false); setSaving(false); }
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget || deleteFlight.current) return;
    const it = deleteTarget;
    deleteFlight.current = true; setDeleting(true); setDeleteError("");
    try {
      await api.del(`/api/feedback/${it.id}`);
      if (!current()) return;
      setDeleteTarget(null);
      refresh();
      toast.success("已删除");
    } catch {
      if (current()) setDeleteError("删除结果未确认，可取消后刷新核对；不会自动重试。");
    } finally { deleteFlight.current = false; if (current()) setDeleting(false); }
  };

  const maxReason = overview ? Math.max(1, ...Object.values(overview.reason_counts)) : 1;

  return (
    <>
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-3 border-b border-border px-7 py-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">反馈管理</h1>
          <p className="text-sm text-muted-foreground">查看用户评价，核对原始任务与结果。</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <PageGuide page="feedback" ready={!loading} />
          <Button variant="outline" size="sm" onClick={refresh} className="gap-1.5">
            <RefreshCw className="h-4 w-4" /> 刷新
          </Button>
          <Button variant="outline" size="sm" onClick={doExport} disabled={exporting || invalidDates || userQuery !== fUser.trim()} className="gap-1.5">
            <Download className="h-4 w-4" /> 导出元数据 CSV
          </Button>
        </div>
      </header>

      <div data-guide="feedback-list" className="min-h-0 flex-1 overflow-y-auto px-4 py-4 sm:px-7 space-y-4">
        {/* 统计卡 */}
        {overviewError && <p role="alert" className="text-sm text-destructive">反馈统计加载失败，请刷新重试。</p>}
        {overview && <p className="text-xs text-muted-foreground">全平台累计 · 下方筛选仅作用于明细与导出</p>}
        {overview && (
          <div className="grid gap-3 grid-cols-2 sm:grid-cols-3 lg:grid-cols-5">
            <Card>
              <CardContent className="flex items-center gap-3 p-3">
                <MessagesSquare className="h-5 w-5 text-primary" />
                <div>
                  <div className="text-2xl font-semibold tabular-nums">{overview.total_tasks?.toLocaleString() ?? "—"}</div>
                  <div className="text-xs text-muted-foreground">平台总任务数</div>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex items-center gap-3 p-3">
                <ThumbsUp className="h-5 w-5 text-primary" />
                <div>
                  <div className="text-2xl font-semibold tabular-nums">{overview.total_up}</div>
                  <div className="text-xs text-muted-foreground">点赞数</div>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex items-center gap-3 p-3">
                <ThumbsDown className="h-5 w-5 text-destructive" />
                <div>
                  <div className="text-2xl font-semibold tabular-nums">{overview.total_down}</div>
                  <div className="text-xs text-muted-foreground">点踩数</div>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex items-center gap-3 p-3">
                <BarChart3 className="h-5 w-5 text-muted-foreground" />
                <div>
                  <div className="text-2xl font-semibold tabular-nums">
                    {overview.total_tasks ? `${(overview.total_down / overview.total_tasks * 100).toFixed(1)}%` : "—"}
                  </div>
                  <div className="text-xs text-muted-foreground">点踩率</div>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex items-center gap-3 p-3">
                <Clock className="h-5 w-5 text-muted-foreground" />
                <div>
                  <div className="text-2xl font-semibold tabular-nums">{overview.total_pending}</div>
                  <div className="text-xs text-muted-foreground">待处理</div>
                </div>
              </CardContent>
            </Card>
          </div>
        )}

        {/* 原因分布 */}
        {overview && overview.total_down > 0 && (
          <details className="rounded-xl border px-4 py-3">
              <summary className="cursor-pointer text-sm font-medium focus-visible:ring-2 focus-visible:ring-ring">点踩原因分布</summary>
              <div className="mt-3 space-y-2">
                {REASON_OPTIONS.map((r) => {
                  const cnt = overview.reason_counts[r] || 0;
                  return (
                    <div key={r} className="flex items-center gap-3 text-sm">
                      <div className="w-24 shrink-0 text-muted-foreground">{r}</div>
                      <div className="h-5 flex-1 overflow-hidden rounded bg-muted">
                        <div className="h-full bg-red-500/70" style={{ width: `${(cnt / maxReason) * 100}%` }} />
                      </div>
                      <div className="w-8 shrink-0 text-right tabular-nums">{cnt}</div>
                    </div>
                  );
                })}
              </div>
          </details>
        )}

        {/* 筛选条 */}
        <Card>
          <CardContent className="flex flex-wrap items-center gap-3 p-4 text-sm">
            <Filter className="h-4 w-4 text-muted-foreground" />
            <select aria-label="处理状态" value={fStatus} onChange={(e) => { setFStatus(e.target.value); setPage(1); }} className="h-8 rounded border border-input bg-transparent px-2 text-sm">
              <option value="">全部状态</option>
              <option value="pending">待处理</option>
              <option value="resolved">已处理</option>
              <option value="ignored">已忽略</option>
              <option value="fixed">已修复</option>
              <option value="no_change">无需修改</option>
              <option value="deferred">暂不处理</option>
            </select>
            <select aria-label="评价类型" value={fRating} onChange={(e) => { setFRating(e.target.value); setPage(1); }} className="h-8 rounded border border-input bg-transparent px-2 text-sm">
              <option value="">全部反馈</option>
              <option value="up">仅点赞</option>
              <option value="down">仅点踩</option>
            </select>
            <select aria-label="反馈原因" value={fReason} onChange={(e) => { setFReason(e.target.value); setPage(1); }} className="h-8 rounded border border-input bg-transparent px-2 text-sm">
              <option value="">全部原因</option>
              {REASON_OPTIONS.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <input aria-label="开始日期" type="date" value={fFrom} onChange={(e) => { setFFrom(e.target.value); setPage(1); }} className="h-8 rounded border border-input bg-transparent px-2 text-sm" />
            <span className="text-muted-foreground">至</span>
            <input aria-label="结束日期" aria-invalid={invalidDates} aria-describedby={invalidDates ? "feedback-date-error" : undefined} type="date" value={fTo} onChange={(e) => { setFTo(e.target.value); setPage(1); }} className="h-8 rounded border border-input bg-transparent px-2 text-sm" />
            <input aria-label="用户名、昵称或用户ID" title="支持用户名、昵称或完整用户ID" placeholder="搜索用户名 / 昵称 / ID" maxLength={200} value={fUser} onChange={(e) => setFUser(e.target.value)} onCompositionStart={() => setComposing(true)} onCompositionEnd={() => setComposing(false)} className="h-8 w-48 rounded border border-input bg-transparent px-2 text-sm" />
            {(fRating || fReason || fFrom || fTo || fUser || fStatus) && (
              <Button variant="ghost" size="sm" onClick={() => { setFRating(""); setFReason(""); setFFrom(""); setFTo(""); setFUser(""); setUserQuery(""); setFStatus(""); setPage(1); }}>
                清除
              </Button>
            )}
          </CardContent>
        </Card>

        {/* 明细列表 */}
        {invalidDates && <p id="feedback-date-error" role="alert" className="text-sm text-destructive">结束日期不能早于开始日期。下方保留上次查询结果。</p>}
        {loadError && <div role="alert" className="rounded-lg border border-destructive/30 p-4 text-sm">反馈加载失败，请刷新重试。{items.length > 0 && "下方保留上次查询结果。"}<Button variant="outline" size="sm" className="ml-3" onClick={refresh}>重试</Button></div>}
        {loading && <p role="status" className="text-sm text-muted-foreground">正在更新反馈…</p>}
        {loading && !items.length ? (
          <p className="text-sm text-muted-foreground">加载中…</p>
        ) : !items.length && !loadError ? (
          <div className="mx-auto max-w-md py-16 text-center">
            <MessageSquare className="mx-auto mb-3 h-10 w-10 text-muted-foreground/40" />
            <p className="text-sm text-muted-foreground">暂无反馈数据。</p>
          </div>
        ) : (
          <div className="space-y-2">
            {items.map((it) => {
              const sm = STATUS_META[it.status] || STATUS_META.pending;
              return (
                <Card key={it.id}>
                  <CardContent className="p-4">
                    <div className="flex flex-wrap items-center gap-3 text-sm">
                      {it.rating === "up" ? (
                        <ThumbsUp className="h-4 w-4 shrink-0 text-green-500" />
                      ) : (
                        <ThumbsDown className="h-4 w-4 shrink-0 text-red-500" />
                      )}
                      <span className="text-muted-foreground">{beijingTime(it.created_at)}</span>
                      <span className="break-all text-muted-foreground" title={userLabel(it)}>{userLabel(it)}</span>
                      {it.rating === 'down' && <Badge variant={sm.variant} className={`text-xs ${sm.variant === "success" ? "text-emerald-700 dark:text-emerald-300" : ""}`}>{sm.label}</Badge>}
                      {it.reasons.map((r) => (
                        <Badge key={r} variant="warning" className="text-xs">{r}</Badge>
                      ))}
                      <div className="ml-auto flex flex-wrap gap-1">
                        <Button variant="ghost" size="sm" onClick={() => { setDeleteError(""); setDeleteTarget(it); }} className="h-7 gap-1 text-xs text-destructive" title="删除">
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => openAudit(it)} disabled={!it.content_available} className="h-8 text-xs text-primary">
                          查看反馈详情
                        </Button>
                      </div>
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {it.rating === 'up' ? '用户点赞' : '用户点踩'} · {it.has_comment ? "已填写补充说明，查看详情可读" : "未填写补充说明"}{it.rating === 'down' && ` · ${it.has_admin_note ? '有处理备注' : '尚无处理备注'}`}
                      {!it.content_available && " · 原始内容已删除或关联失效，无法审计查看"}
                    </p>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}

        <Pagination page={pageClamped} totalPages={totalPages} total={total} pageSize={pageSize} pageSizeOptions={[10, 20, 50, 100]} disabled={loading || invalidDates || loadError} onChange={setPage} onPageSizeChange={size => { setPageSize(size); setPage(1); }} />
      </div>

      {/* 正文仅保存在当前弹窗，关闭或换身份立即失效。 */}
      <Dialog.Root open={!!auditTarget} onOpenChange={open => { if (!open) requestCloseAudit(); }}>
      <Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
      <Dialog.Content aria-describedby="feedback-detail-description" onCloseAutoFocus={event => { event.preventDefault(); detailTrigger.current?.focus(); }} className="fixed inset-y-0 right-0 z-50 flex w-full max-w-5xl flex-col border-l bg-card shadow-xl">
        <div className="shrink-0 border-b px-5 py-4"><Dialog.Title className="text-lg font-semibold">反馈详情</Dialog.Title><Dialog.Description id="feedback-detail-description" className="mt-1 text-sm text-muted-foreground">核对用户需求、实际结果与反馈，再记录处理结论。</Dialog.Description></div>
        <div className="min-h-0 flex-1 overflow-y-auto p-5">
        <p className="text-sm break-words">反馈 #{auditTarget?.id} · {auditTarget?.rating === 'up' ? '点赞' : '点踩'} · {auditTarget && userLabel(auditTarget)}</p>
        {auditTarget?.rating === 'down' && <p className="text-sm">{STATUS_META[auditTarget.status].label}</p>}
        <p className="mt-2 text-sm text-muted-foreground">这里可查看原始任务、对应回答和用户补充说明。内容涉及用户资料，请说明查看原因，系统会记录本次访问。</p>
        {!!auditTarget?.reasons.length && <p className="mt-2 text-sm">反馈原因：{auditTarget.reasons.join('、')}</p>}
        {!auditContent && (
          <form onSubmit={(event) => { event.preventDefault(); void submitAudit(); }}>
            <label htmlFor="feedback-audit-reason" className="mt-3 block text-sm">查看原因</label>
            <textarea ref={reasonInput} id="feedback-audit-reason" value={auditReason} onChange={(event) => setAuditReason(event.target.value)} readOnly={auditAttempted} maxLength={1000} className="mt-1 h-24 w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm" />
            <p className="text-xs text-muted-foreground">去除首尾空白后 5–1000 字。只查看当前单条反馈。</p>
            <Button type="submit" size="sm" className="mt-3" disabled={auditAttempted || auditReason.trim().length < 5 || auditReason.trim().length > 1000}>提交审计并查看</Button>
          </form>
        )}
        {auditBusy && <p role="status" className="mt-3 text-sm">正在提交…</p>}
        {auditError && <p role="alert" className="mt-3 text-sm text-destructive">{auditError}</p>}
        {auditFailureEvent && <p role="status" className="mt-3 text-sm">审计事件：{auditFailureEvent}</p>}
        {auditContent && (
          <div className="mt-3 space-y-3 text-sm">
            <details className="text-xs text-muted-foreground"><summary className="cursor-pointer">访问记录与任务编号</summary><p role="status">审计事件：{auditContent.event_id}</p>
            {auditContent.context && <p className="break-all">{auditContent.context.task_id ? `任务 ${auditContent.context.task_id} · 版本 V${auditContent.context.revision}` : `会话 ${auditContent.context.conv_id} · 消息 ${auditContent.context.message_id}`}</p>}</details>
            {auditContent.truncated && <p>正文已截断，仅显示有界内容；为避免覆盖完整备注，本次不可编辑备注。</p>}
            <div className="space-y-4">
              {([['comment', '用户补充说明'], ['task_title', '任务名称'], ['original_task', '原始任务要求'], ['question', '本次提问'], ['answer', '对应回答 / 结果摘要']] as const).map(([key, label]) => (
                <section key={key} className="min-w-0 break-words"><h4 className="mb-1 font-medium">{label}</h4>{key === 'answer' ? <Markdown safeResources>{auditContent.content[key] || '未记录'}</Markdown> : <p className="whitespace-pre-wrap text-muted-foreground">{auditContent.content[key] || (key === 'comment' ? '用户未填写补充说明' : '未记录')}</p>}</section>
              ))}
              {auditContent.content.result_preview && <section><h4 className="font-medium">正式结果预览</h4><p className="whitespace-pre-wrap break-words text-muted-foreground">{auditContent.content.result_preview}</p></section>}
            </div>
            <Suspense fallback={<p role="status">正在加载原任务查看器…</p>}><FeedbackTaskContext key={auditContent.event_id} feedbackId={auditTarget!.id} eventId={auditContent.event_id} /></Suspense>
            <label htmlFor="feedback-admin-note" className="block">处理备注</label>
            <textarea id="feedback-admin-note" maxLength={5000} value={resolveNote} onChange={(event) => setResolveNote(event.target.value)} readOnly={auditBusy || !!auditError || auditContent.truncated} aria-describedby="feedback-note-help" className="h-24 w-full resize-none rounded-md border border-input bg-transparent px-3 py-2" />
            <p id="feedback-note-help" className="text-xs text-muted-foreground">{conclusionRequired ? '已提交的处理结果必须保留结论，可修改但不能清空。' : '清空后保存将删除旧备注。'}</p>
            {auditTarget?.rating === 'down' && <><label htmlFor="feedback-resolution" className="block">处理结果</label>
            <select id="feedback-resolution" value={resolution} onChange={event => setResolution(event.target.value as typeof resolution)} disabled={auditBusy || !!auditError || auditContent.truncated} className="h-9 w-full rounded-md border border-input bg-background px-3 text-sm">
              <option value="">请选择处理结果</option>
              <option value="fixed">已修复</option><option value="no_change">无需修改</option><option value="deferred">暂不处理</option>
            </select>
            <p className="text-xs text-muted-foreground">请在备注中记录修复与验证结果，或说明无需修改、暂不处理的原因。仅保存备注不会改变状态。</p></>}
            {resolveNote.length > 5000 && <p className="text-destructive">历史备注超过5000字，请精简后保存；未保存前原文保持不变。</p>}
          </div>
        )}
        </div>
        <div className="shrink-0 border-t bg-card p-4 flex flex-wrap justify-end gap-2">
            {auditContent && <div className="mr-auto flex flex-wrap gap-2">
              <Button size="sm" variant="outline" onClick={() => void saveNote()} disabled={auditBusy || !!auditError || auditContent.truncated || resolveNote.length > 5000 || (conclusionRequired && !resolveNote.trim()) || resolveNote === (auditContent.content.admin_note || "")}>保存备注</Button>
              {auditTarget?.rating === 'down' && <Button size="sm" onClick={() => resolution && void saveNote(resolution)} disabled={auditBusy || !!auditError || auditContent.truncated || resolveNote.length > 5000 || !resolveNote.trim() || !resolution}>提交处理结论</Button>}
            </div>}
          <Button variant="outline" size="sm" disabled={saving} onClick={requestCloseAudit}>关闭</Button>
        </div>
      </Dialog.Content></Dialog.Portal></Dialog.Root>
      <ConfirmDialog open={discardOpen} title="放弃未保存的备注？" description="你填写的处理备注尚未保存，关闭后将丢失。" action="放弃修改" danger busy={restoringHistory} onCancel={() => { historyDestination.current = null; setDiscardOpen(false); }} onConfirm={() => {
        const delta = historyDestination.current; historyDestination.current = null;
        closeAudit();
        if (delta !== null) setTimeout(() => window.history.go(delta), 0);
      }} />

      {/* 删除确认 */}
      <ConfirmDialog open={!!deleteTarget} title="删除反馈" description={`确定删除反馈 #${deleteTarget?.id}？仅删除此条评价，不删除用户任务。此操作不可撤销。`} action="删除" danger busy={deleting} error={deleteError} onCancel={() => setDeleteTarget(null)} onConfirm={() => void confirmDelete()} />
    </>
  );
}
