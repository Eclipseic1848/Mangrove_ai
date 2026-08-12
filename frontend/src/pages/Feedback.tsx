import { useEffect, useState } from "react";
import { ThumbsUp, ThumbsDown, Download, RefreshCw, Filter, MessageSquare, MessagesSquare, BarChart3, Check, Trash2, Ban, Clock } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Modal } from "@/components/ui/modal";
import { Pagination } from "@/components/ui/pagination";
import { api, getToken } from "@/lib/api";

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
  model: string | null;
  reasons: string[];
  comment: string | null;
  created_at: string;
  question: string | null;
  answer: string | null;
  status: "pending" | "resolved" | "ignored";
  admin_note: string | null;
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
  const [overview, setOverview] = useState<Overview | null>(null);
  const [items, setItems] = useState<FeedbackItem[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<number | null>(null);
  // 筛选
  const [fRating, setFRating] = useState("");
  const [fReason, setFReason] = useState("");
  const [fFrom, setFFrom] = useState("");
  const [fTo, setFTo] = useState("");
  const [fUser, setFUser] = useState("");
  const [fStatus, setFStatus] = useState("");
  // 操作弹框
  const [resolveTarget, setResolveTarget] = useState<FeedbackItem | null>(null);
  const [resolveNote, setResolveNote] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<FeedbackItem | null>(null);

  const reloadOverview = () => api.get("/api/feedback/overview").then(setOverview).catch(() => {});

  useEffect(() => {
    setLoading(true);
    const params = new URLSearchParams();
    if (fRating) params.set("rating", fRating);
    if (fReason) params.set("reason", fReason);
    if (fFrom) params.set("date_from", fFrom);
    if (fTo) params.set("date_to", fTo);
    if (fUser) params.set("user_id", fUser);
    if (fStatus) params.set("status", fStatus);
    Promise.all([
      api.get("/api/feedback/overview"),
      api.get(`/api/feedback/list?limit=${PAGE_SIZE}&offset=${(page - 1) * PAGE_SIZE}&${params.toString()}`),
    ])
      .then(([ov, list]: any) => {
        setOverview(ov);
        setItems(list.items || []);
        setTotal(list.total || 0);
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [page, fRating, fReason, fFrom, fTo, fUser, fStatus]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  const pageClamped = Math.min(page, totalPages);

  const doExport = async () => {
    try {
      const res = await fetch("/api/feedback/export", { headers: { Authorization: `Bearer ${getToken()}` } });
      if (!res.ok) throw new Error("导出失败");
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = "feedback.csv";
      a.click();
      URL.revokeObjectURL(a.href);
      toast.success("已导出点踩明细");
    } catch (e: any) {
      toast.error(e.message || "导出失败");
    }
  };

  const setIgnored = async (it: FeedbackItem) => {
    try {
      await api.patch(`/api/feedback/${it.id}`, { status: "ignored", admin_note: it.admin_note });
      setItems((m) => m.map((x) => (x.id === it.id ? { ...x, status: "ignored" as const } : x)));
      reloadOverview();
    } catch (e: any) {
      toast.error(e.message || "更新失败");
    }
  };

  const openResolve = (it: FeedbackItem) => {
    setResolveTarget(it);
    setResolveNote(it.admin_note || "");
  };

  const confirmResolve = async () => {
    if (!resolveTarget) return;
    const it = resolveTarget;
    setResolveTarget(null);
    try {
      await api.patch(`/api/feedback/${it.id}`, { status: "resolved", admin_note: resolveNote || null });
      setItems((m) => m.map((x) => (x.id === it.id ? { ...x, status: "resolved" as const, admin_note: resolveNote || null } : x)));
      reloadOverview();
      toast.success("已标记为已处理");
    } catch (e: any) {
      toast.error(e.message || "更新失败");
    }
  };

  const confirmDelete = async () => {
    if (!deleteTarget) return;
    const it = deleteTarget;
    setDeleteTarget(null);
    try {
      await api.del(`/api/feedback/${it.id}`);
      setItems((m) => m.filter((x) => x.id !== it.id));
      setTotal((t) => Math.max(0, t - 1));
      reloadOverview();
      toast.success("已删除");
    } catch (e: any) {
      toast.error(e.message || "删除失败");
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
          <Button variant="outline" size="sm" onClick={() => { reloadOverview(); setPage(page); }} className="gap-1.5">
            <RefreshCw className="h-4 w-4" /> 刷新
          </Button>
          <Button variant="outline" size="sm" onClick={doExport} className="gap-1.5">
            <Download className="h-4 w-4" /> 导出点踩 CSV
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
                      {it.model && (
                        <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground">{it.model}</span>
                      )}
                      <Badge variant={sm.variant} className="text-[11px]">{sm.label}</Badge>
                      {it.reasons.map((r) => (
                        <Badge key={r} variant="warning" className="text-[11px]">{r}</Badge>
                      ))}
                      <div className="ml-auto flex gap-1">
                        <Button variant="ghost" size="sm" onClick={() => openResolve(it)} className="h-7 gap-1 text-xs" title="标记已处理">
                          <Check className="h-3.5 w-3.5" /> 已处理
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => setIgnored(it)} className="h-7 gap-1 text-xs" title="标记忽略">
                          <Ban className="h-3.5 w-3.5" /> 忽略
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => setDeleteTarget(it)} className="h-7 gap-1 text-xs text-destructive" title="删除">
                          <Trash2 className="h-3.5 w-3.5" />
                        </Button>
                        <Button variant="ghost" size="sm" onClick={() => setExpanded(expanded === it.id ? null : it.id)} className="h-7 text-xs">
                          {expanded === it.id ? "收起" : "详情"}
                        </Button>
                      </div>
                    </div>
                    {it.comment && (
                      <div className="mt-2 rounded bg-muted/50 px-3 py-2 text-xs text-muted-foreground">
                        用户描述：{it.comment}
                      </div>
                    )}
                    {it.admin_note && (
                      <div className="mt-2 rounded bg-amber-500/10 px-3 py-2 text-xs text-muted-foreground">
                        处理备注：{it.admin_note}
                      </div>
                    )}
                    {expanded === it.id && (
                      <div className="mt-3 space-y-2 border-t border-border/60 pt-3 text-xs">
                        {it.model && (
                          <div className="text-muted-foreground">模型：<span className="text-foreground">{it.model}</span></div>
                        )}
                        <div>
                          <div className="mb-1 font-medium text-foreground">用户问题</div>
                          <div className="whitespace-pre-wrap text-muted-foreground">{it.question || "（无）"}</div>
                        </div>
                        <div>
                          <div className="mb-1 font-medium text-foreground">AI 回复</div>
                          <div className="max-h-60 overflow-y-auto whitespace-pre-wrap text-muted-foreground">{it.answer || "（无）"}</div>
                        </div>
                      </div>
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}

        <Pagination page={pageClamped} totalPages={totalPages} total={total} onChange={setPage} />
      </div>

      {/* 标记已处理弹框 */}
      <Modal open={!!resolveTarget} onClose={() => setResolveTarget(null)} title="标记为已处理">
        <p className="text-sm text-muted-foreground">记录处理方式（可选），便于后续回溯这条反馈催生了什么改进。</p>
        <textarea
          value={resolveNote}
          onChange={(e) => setResolveNote(e.target.value)}
          placeholder="如：已修 prompt / 已加教训 / 已优化采集器"
          className="mt-3 h-24 w-full resize-none rounded-md border border-input bg-transparent px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        />
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="outline" size="sm" onClick={() => setResolveTarget(null)}>取消</Button>
          <Button size="sm" onClick={confirmResolve}>确认</Button>
        </div>
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
