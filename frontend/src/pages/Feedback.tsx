import { useEffect, useRef, useState } from "react";
import { ThumbsUp, ThumbsDown, Download, RefreshCw, Filter, MessageSquare, MessagesSquare, BarChart3, Check, Trash2, Ban, Clock } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { Pagination } from "@/components/ui/pagination";
import { api, ApiError, authenticatedFetch, getSessionState, readAuthenticatedBlob, readAuthenticatedJson } from "@/lib/api";
import { isAdminish, useAuth } from "@/lib/auth";

interface Overview {
  total_sessions: number;
  total_up: number;
  total_down: number;
  total_pending: number;
  down_rate: number;
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
  status: "pending" | "resolved" | "ignored";
  has_comment: boolean;
  has_admin_note: boolean;
  content_available: boolean;
}

interface AuditContent {
  event_id: string;
  content: { question: string | null; answer: string | null; comment: string | null; admin_note: string | null };
  truncated: boolean;
  content_bytes: number;
}

const REASON_OPTIONS = ["理解错误", "上下文错误", "回答不清晰", "代码错误", "回答不专业", "格式错误", "其他"];
const STATUS_META: Record<string, { label: string; variant: "warning" | "success" | "outline" }> = {
  pending: { label: "待处理", variant: "warning" },
  resolved: { label: "已处理", variant: "success" },
  ignored: { label: "已忽略", variant: "outline" },
};
const PAGE_SIZE = 10;

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
  const [items, setItems] = useState<FeedbackItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  // 筛选
  const [fRating, setFRating] = useState("");
  const [fReason, setFReason] = useState("");
  const [fFrom, setFFrom] = useState("");
  const [fTo, setFTo] = useState("");
  const [fUser, setFUser] = useState("");
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
  const [deleteTarget, setDeleteTarget] = useState<FeedbackItem | null>(null);
  const auditGeneration = useRef(0);
  const auditFlight = useRef(false);
  const reasonInput = useRef<HTMLTextAreaElement>(null);
  const mounted = useRef(true);
  const current = () => mounted.current && getSessionState().user?.user_id === actorId && getSessionState().user?.role === actorRole;
  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; auditGeneration.current++; };
  }, []);

  const refresh = () => setRevision((value) => value + 1);
  const filters = new URLSearchParams();
  if (fRating) filters.set("rating", fRating);
  if (fReason) filters.set("reason", fReason);
  if (fFrom) filters.set("date_from", fFrom);
  if (fTo) filters.set("date_to", fTo);
  if (fUser) filters.set("user_id", fUser);
  if (fStatus) filters.set("status", fStatus);
  const filterQuery = filters.toString();

  useEffect(() => {
    setLoading(true);
    let active = true;
    Promise.all([
      api.get("/api/feedback/overview"),
      api.get(`/api/feedback/list?limit=${PAGE_SIZE}&offset=${(page - 1) * PAGE_SIZE}&${filterQuery}`),
    ])
      .then(([ov, list]: any) => {
        if (!active || !current()) return;
        setOverview(ov);
        setItems(list.items || []);
        setTotal(list.total || 0);
      })
      .catch(() => { if (active && current()) { setItems([]); setTotal(0); toast.error("反馈加载失败，请刷新重试"); } })
      .finally(() => { if (active && current()) setLoading(false); });
    // 筛选与刷新只接受最新请求，旧结果不能覆盖当前列表。
    return () => { active = false; };
  }, [page, filterQuery, revision]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pageClamped = Math.min(page, totalPages);

  const doExport = async () => {
    try {
      const res = await authenticatedFetch(`/api/feedback/export?${filterQuery}`);
      if (!res.ok) throw new Error("导出失败");
      const blob = await readAuthenticatedBlob(res);
      if (!current()) return;
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "feedback.csv";
      a.click();
      URL.revokeObjectURL(a.href);
      toast.success("已导出当前筛选元数据");
    } catch {
      if (current()) toast.error("导出失败，请重试");
    }
  };

  const setStatus = async (it: FeedbackItem, status: "ignored" | "resolved") => {
    try {
      // 仅改状态时省略备注，避免把未读取的旧备注清空。
      await api.patch(`/api/feedback/${it.id}`, { status });
      if (current()) refresh();
    } catch {
      if (current()) toast.error("状态更新失败，请刷新核对");
    }
  };

  const closeAudit = () => {
    auditGeneration.current++;
    auditFlight.current = false;
    setAuditTarget(null);
    setAuditContent(null);
    setAuditReason("");
    setResolveNote("");
    setAuditError("");
    setAuditFailureEvent("");
    setAuditBusy(false);
    setAuditAttempted(false);
  };

  const openAudit = (it: FeedbackItem) => { closeAudit(); setAuditTarget(it); };

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
    } catch (error) {
      if (!current() || generation !== auditGeneration.current) return;
      setAuditError(error instanceof ApiError && error.status === 429
        ? "请求过于频繁，未显示正文；请稍后重新打开。"
        : "未取得可核实的审计结果，未显示正文；请关闭后核对，不会自动重试。");
    } finally {
      if (current() && generation === auditGeneration.current) { auditFlight.current = false; setAuditBusy(false); }
    }
  };

  const saveNote = async () => {
    if (!auditTarget || !auditContent || auditContent.truncated || auditFlight.current) return;
    const generation = auditGeneration.current;
    auditFlight.current = true;
    setAuditBusy(true);
    try {
      await api.patch(`/api/feedback/${auditTarget.id}`, { admin_note: resolveNote.trim() || null });
      if (current() && generation === auditGeneration.current) { closeAudit(); refresh(); toast.success("备注已保存"); }
    } catch {
      if (current() && generation === auditGeneration.current) setAuditError("备注保存结果未确认，请关闭后重新审计核对。");
    } finally {
      if (current() && generation === auditGeneration.current) { auditFlight.current = false; setAuditBusy(false); }
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    const it = deleteTarget;
    setDeleteTarget(null);
    try {
      await api.del(`/api/feedback/${it.id}`);
      if (!current()) return;
      refresh();
      toast.success("已删除");
    } catch {
      if (current()) toast.error("删除结果未确认，请刷新核对");
    }
  };

  const maxReason = overview ? Math.max(1, ...Object.values(overview.reason_counts)) : 1;

  return (
    <>
      <header className="flex items-center justify-between border-b border-border px-7 py-4">
        <div>
          <h1 className="text-lg font-semibold tracking-tight">反馈管理</h1>
          <p className="text-sm text-muted-foreground">用户点赞/点踩统计与明细，驱动对话质量优化</p>
        </div>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={refresh} className="gap-1.5">
            <RefreshCw className="h-4 w-4" /> 刷新
          </Button>
          <Button variant="outline" size="sm" onClick={doExport} className="gap-1.5">
            <Download className="h-4 w-4" /> 导出元数据 CSV
          </Button>
        </div>
      </header>

      <div className="flex-1 overflow-y-auto px-7 py-6 space-y-6">
        {/* 统计卡 */}
        {overview && (
          <div className="grid gap-3 grid-cols-2 sm:grid-cols-3 lg:grid-cols-5">
            <Card>
              <CardContent className="flex items-center gap-3 p-5">
                <MessagesSquare className="h-8 w-8 text-primary" />
                <div>
                  <div className="text-2xl font-semibold tabular-nums">{overview.total_sessions}</div>
                  <div className="text-xs text-muted-foreground">总会话数</div>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex items-center gap-3 p-5">
                <ThumbsUp className="h-8 w-8 text-green-500" />
                <div>
                  <div className="text-2xl font-semibold tabular-nums">{overview.total_up}</div>
                  <div className="text-xs text-muted-foreground">点赞数</div>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex items-center gap-3 p-5">
                <ThumbsDown className="h-8 w-8 text-red-500" />
                <div>
                  <div className="text-2xl font-semibold tabular-nums">{overview.total_down}</div>
                  <div className="text-xs text-muted-foreground">点踩数</div>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex items-center gap-3 p-5">
                <BarChart3 className="h-8 w-8 text-muted-foreground" />
                <div>
                  <div className="text-2xl font-semibold tabular-nums">
                    {overview.total_sessions ? ((overview.total_down / overview.total_sessions) * 100).toFixed(1) : "0.0"}%
                  </div>
                  <div className="text-xs text-muted-foreground">点踩率</div>
                </div>
              </CardContent>
            </Card>
            <Card>
              <CardContent className="flex items-center gap-3 p-5">
                <Clock className="h-8 w-8 text-amber-500" />
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
          <Card>
            <CardContent className="p-5">
              <div className="mb-3 text-sm font-medium">点踩原因分布</div>
              <div className="space-y-2">
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
            </CardContent>
          </Card>
        )}

        {/* 筛选条 */}
        <Card>
          <CardContent className="flex flex-wrap items-center gap-3 p-4 text-sm">
            <Filter className="h-4 w-4 text-muted-foreground" />
            <select value={fStatus} onChange={(e) => { setFStatus(e.target.value); setPage(1); }} className="h-8 rounded border border-input bg-transparent px-2 text-sm">
              <option value="">全部状态</option>
              <option value="pending">待处理</option>
              <option value="resolved">已处理</option>
              <option value="ignored">已忽略</option>
            </select>
            <select value={fRating} onChange={(e) => { setFRating(e.target.value); setPage(1); }} className="h-8 rounded border border-input bg-transparent px-2 text-sm">
              <option value="">全部反馈</option>
              <option value="up">仅点赞</option>
              <option value="down">仅点踩</option>
            </select>
            <select value={fReason} onChange={(e) => { setFReason(e.target.value); setPage(1); }} className="h-8 rounded border border-input bg-transparent px-2 text-sm">
              <option value="">全部原因</option>
              {REASON_OPTIONS.map((r) => <option key={r} value={r}>{r}</option>)}
            </select>
            <input type="date" value={fFrom} onChange={(e) => { setFFrom(e.target.value); setPage(1); }} className="h-8 rounded border border-input bg-transparent px-2 text-sm" />
            <span className="text-muted-foreground">至</span>
            <input type="date" value={fTo} onChange={(e) => { setFTo(e.target.value); setPage(1); }} className="h-8 rounded border border-input bg-transparent px-2 text-sm" />
            <input placeholder="用户名" value={fUser} onChange={(e) => { setFUser(e.target.value); setPage(1); }} className="h-8 w-32 rounded border border-input bg-transparent px-2 text-sm" />
            {(fRating || fReason || fFrom || fTo || fUser || fStatus) && (
              <Button variant="ghost" size="sm" onClick={() => { setFRating(""); setFReason(""); setFFrom(""); setFTo(""); setFUser(""); setFStatus(""); setPage(1); }}>
                清除
              </Button>
            )}
          </CardContent>
        </Card>

        {/* 明细列表 */}
        {loading ? (
          <p className="text-sm text-muted-foreground">加载中…</p>
        ) : !items.length ? (
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
                      <span className="text-muted-foreground">{it.created_at}</span>
                      <span className="text-muted-foreground" title={userLabel(it)}>{userLabel(it)}</span>
                      <Badge variant={sm.variant} className="text-[11px]">{sm.label}</Badge>
                      {it.reasons.map((r) => (
                        <Badge key={r} variant="warning" className="text-[11px]">{r}</Badge>
                      ))}
                      <div className="ml-auto flex gap-1">
                        <Button variant="ghost" size="sm" onClick={() => setStatus(it, "resolved")} className="h-7 gap-1 text-xs" title="标记已处理">
                          <Check className="h-3.5 w-3.5" /> 已处理
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => setStatus(it, "ignored")} className="h-7 gap-1 text-xs" title="标记忽略">
                          <Ban className="h-3.5 w-3.5" /> 忽略
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => setDeleteTarget(it)} className="h-7 gap-1 text-xs text-destructive" title="删除">
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => openAudit(it)} disabled={!it.content_available} className="h-7 text-xs">
                          审计查看业务内容
                        </Button>
                      </div>
                    </div>
                    <p className="mt-2 text-xs text-muted-foreground">
                      {it.has_comment ? "有用户描述" : "无用户描述"} · {it.has_admin_note ? "有处理备注" : "无处理备注"}
                      {!it.content_available && " · 业务内容不可用"}
                    </p>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}

        <Pagination page={pageClamped} totalPages={totalPages} total={total} onChange={setPage} />
      </div>

      {/* 正文仅保存在当前弹窗，关闭或换身份立即失效。 */}
      <Modal open={!!auditTarget} onClose={closeAudit} title="审计查看业务内容" wide>
        <p className="text-sm text-muted-foreground">反馈 #{auditTarget?.id}。查看需填写原因并留下审计记录。</p>
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
            <p role="status">审计事件：{auditContent.event_id}</p>
            {auditContent.truncated && <p>正文已截断，仅显示有界内容；为避免覆盖完整备注，本次不可编辑备注。</p>}
            <div className="max-h-72 space-y-3 overflow-y-auto">
              {([['question', '用户问题'], ['answer', 'AI 回复'], ['comment', '用户描述']] as const).map(([key, label]) => (
                <div key={key}><p className="font-medium">{label}</p><p className="whitespace-pre-wrap break-words text-muted-foreground">{auditContent.content[key] || "（无）"}</p></div>
              ))}
            </div>
            <label htmlFor="feedback-admin-note" className="block">处理备注（清空后保存将删除旧备注）</label>
            <textarea autoFocus id="feedback-admin-note" value={resolveNote} onChange={(event) => setResolveNote(event.target.value)} readOnly={auditBusy || !!auditError || auditContent.truncated} className="h-24 w-full rounded-md border border-input bg-transparent px-3 py-2" />
            <Button size="sm" onClick={saveNote} disabled={auditBusy || !!auditError || auditContent.truncated || resolveNote === (auditContent.content.admin_note || "")}>保存备注</Button>
          </div>
        )}
        <div className="mt-4 flex justify-end"><Button variant="outline" size="sm" onClick={closeAudit}>关闭</Button></div>
      </Modal>

      {/* 删除确认 */}
      <Modal open={!!deleteTarget} onClose={() => setDeleteTarget(null)} title="删除反馈">
        <p className="text-sm text-muted-foreground">确定删除这条反馈？此操作不可撤销。</p>
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setDeleteTarget(null)}>取消</Button>
          <Button variant="destructive" size="sm" onClick={confirmDelete}>删除</Button>
        </div>
      </Modal>
    </>
  );
}
