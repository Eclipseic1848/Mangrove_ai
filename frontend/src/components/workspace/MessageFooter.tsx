import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, ThumbsDown, ThumbsUp } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { copyText, fmtTime, formatTokenUsage, type TokenUsage } from "@/lib/messageActions";

export function MessageFooter({ convId, messageId, content, createdAt, usage, assistant = true }: {
  convId?: string; messageId?: number; content: string; createdAt?: string; usage?: TokenUsage | null; assistant?: boolean;
}) {
  const { user } = useAuth();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const queryKey = ["message-feedback", user?.user_id, convId];
  const feedback = useQuery({ queryKey, enabled: Boolean(assistant && convId && messageId),
    queryFn: () => api.get(`/api/chat/feedback?conv_id=${encodeURIComponent(convId!)}`), staleTime: 30000 });
  const rating = feedback.data?.feedback?.[messageId ?? 0]?.rating;
  const vote = async (next: "up" | "down") => {
    if (!convId || !messageId || busy) return;
    setBusy(true); setError("");
    try {
      if (rating === next) await api.del(`/api/chat/feedback/${messageId}`);
      else await api.post("/api/chat/feedback", { conv_id: convId, message_id: messageId, rating: next });
      await client.invalidateQueries({ queryKey });
    } catch (reason) { setError(reason instanceof Error ? reason.message : "反馈保存失败"); }
    finally { setBusy(false); }
  };
  const button = "rounded p-2 text-muted-foreground hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40 aria-pressed:text-primary";
  return <footer className="mt-2 space-y-1 text-xs text-muted-foreground">
    {assistant && <div className="flex flex-wrap items-center gap-1">
      <button type="button" aria-label="复制回复" title="复制回复" className={button} onClick={() => void copyText(content)}><Copy className="h-4 w-4" /></button>
      <button type="button" aria-label="点赞" title={messageId ? "点赞，再次点击取消" : "尚未关联到已保存的消息"} aria-pressed={rating === "up"} disabled={busy || !messageId || !convId || feedback.isPending || feedback.isError} className={button} onClick={() => void vote("up")}><ThumbsUp className="h-4 w-4" /></button>
      <button type="button" aria-label="点踩" title={messageId ? "点踩，再次点击取消" : "尚未关联到已保存的消息"} aria-pressed={rating === "down"} disabled={busy || !messageId || !convId || feedback.isPending || feedback.isError} className={button} onClick={() => void vote("down")}><ThumbsDown className="h-4 w-4" /></button>
      <span className="ml-auto" title={usage ? `${usage.scope === "execution_only" ? "从原始用量账本恢复，不含旧版会话外的需求识别调用；" : ""}本次回复 ${usage.calls} 次模型调用；${usage.incomplete ? "存在未返回的统计，非完整总量" : "供应商返回的 Token 统计"}` : "该回复尚无可唯一关联的用量，不能推算消耗"}>
        {formatTokenUsage(usage)}
      </span>
    </div>}
    {createdAt && <time dateTime={createdAt} title={createdAt}>{fmtTime(createdAt)}</time>}
    {assistant && messageId && feedback.isError && <p role="alert">反馈读取失败。<button type="button" className="underline" onClick={() => void feedback.refetch()}>重试</button></p>}
    {error && <p role="alert" className="text-destructive">{error}</p>}
  </footer>;
}
