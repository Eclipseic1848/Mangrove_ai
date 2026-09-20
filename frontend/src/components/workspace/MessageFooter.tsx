import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, ThumbsDown, ThumbsUp } from "lucide-react";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { Modal } from "@/components/ui/modal";
import { Button } from "@/components/ui/button";
import { copyText, fmtTime, formatTokenUsage, type TokenUsage } from "@/lib/messageActions";

type Props = {
  convId?: string; messageId?: number; content: string; createdAt?: string; usage?: TokenUsage | null; assistant?: boolean;
  workspace?: { taskId: string; revision: number; resultId?: string };
  usageLabel?: string;
  copyContent?: () => Promise<string>;
  copyLabel?: string;
};
type Feedback = { rating: "up" | "down"; reasons: string[]; comment: string };
type FeedbackData = { feedback: Record<string, Feedback | null> };
const reasonsList = ["理解错误", "上下文错误", "回答不清晰", "代码错误", "回答不专业", "格式错误", "其他"];

export function MessageTimestamp({ value }: { value?: string | null }) {
  const text = fmtTime(value ?? undefined);
  return text ? <time className="block mt-1 text-xs text-muted-foreground" dateTime={value!} title={value!}>{text}</time>
    : <span className="block mt-1 text-xs text-muted-foreground">时间未记录</span>;
}

export function MessageFooter(props: Props) {
  const { user } = useAuth();
  // 身份或目标变化必须清空弹窗，在途回执不能影响另一个结果。
  return <Footer key={JSON.stringify([user?.user_id, props.convId, props.messageId, props.workspace])} {...props} />;
}

function Footer({ convId, messageId, content, createdAt, usage, assistant = true, workspace, usageLabel, copyContent, copyLabel = "复制回复" }: Props) {
  const { user } = useAuth();
  const client = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [copying, setCopying] = useState(false);
  const [dislike, setDislike] = useState(false);
  const [reasons, setReasons] = useState<string[]>([]);
  const [comment, setComment] = useState("");
  const mounted = useRef(true);
  const writing = useRef(false);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const path = workspace ? `/api/semantic-workspace/tasks/${encodeURIComponent(workspace.taskId)}/feedback` : "/api/chat/feedback";
  const queryKey = ["message-feedback", user?.user_id, workspace ?? convId];
  const target = workspace ? "current" : String(messageId ?? 0);
  const linked = Boolean(workspace || (convId && messageId));
  const feedback = useQuery<FeedbackData>({ queryKey, enabled: assistant && linked,
    queryFn: ({ signal }) => api.get(workspace ? `${path}?revision=${workspace.revision}&result_id=${encodeURIComponent(workspace.resultId ?? "delivery")}` : `${path}?conv_id=${encodeURIComponent(convId!)}`, { signal }), staleTime: 30000, retry: false });
  const rating = feedback.data?.feedback?.[target]?.rating;
  const vote = async (next: "up" | "down" | null) => {
    if (!linked || writing.current) return;
    writing.current = true; setBusy(true); setError(""); setNotice("");
    const saved = next ? { rating: next, reasons: next === "down" ? reasons : [], comment: next === "down" ? comment.trim() : "" } : null;
    try {
      await client.cancelQueries({ queryKey });
      if (workspace) await api.post(path, { revision: workspace.revision, result_id: workspace.resultId ?? "delivery", rating: next, reasons: saved?.reasons ?? [], comment: saved?.comment || null }, {}, AbortSignal.timeout(15000));
      else if (!next) await api.del(`${path}/${messageId}`);
      else await api.post(path, { conv_id: convId, message_id: messageId, ...saved }, {}, AbortSignal.timeout(15000));
      client.setQueryData<FeedbackData>(queryKey, old => ({ feedback: { ...old?.feedback, [target]: saved } }));
      if (mounted.current) { setDislike(false); setNotice(next ? "反馈已保存" : "已取消反馈"); }
    } catch (reason) {
      if (mounted.current) setError(reason instanceof Error ? reason.message : "反馈保存失败，请重试");
      void client.invalidateQueries({ queryKey });
    } finally {
      writing.current = false;
      if (mounted.current) setBusy(false);
    }
  };
  const disabled = busy || !linked || feedback.isPending || feedback.isError;
  const button = "rounded p-2 text-muted-foreground hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40 aria-pressed:text-primary";
  return <footer className="mt-2 space-y-1 text-xs text-muted-foreground">
    {assistant && <div className="flex flex-wrap items-center gap-1">
      <button type="button" aria-label={copyLabel} title={copyLabel} disabled={copying} className={button} onClick={async () => {
        setCopying(true); setNotice("");
        try {
          const text = copyContent ? await copyContent() : content;
          if (!mounted.current) return;
          const copied = await copyText(text);
          if (mounted.current) setNotice(copied ? "已复制" : "复制失败，请手动选择文本复制");
        } catch (reason) { if (mounted.current) setNotice(reason instanceof Error ? reason.message : "结果读取失败，请重试"); }
        finally { if (mounted.current) setCopying(false); }
      }}><Copy className="h-4 w-4" /></button>
      <button type="button" aria-label="点赞" title={linked ? "点赞，再次点击取消" : "尚未关联到已保存的消息"} aria-pressed={rating === "up"} disabled={disabled} className={button} onClick={() => void vote(rating === "up" ? null : "up")}><ThumbsUp className="h-4 w-4" /></button>
      <button type="button" aria-label="点踩" title={linked ? "点踩，再次点击取消" : "尚未关联到已保存的消息"} aria-pressed={rating === "down"} disabled={disabled} className={button} onClick={() => { if (rating === "down") void vote(null); else { setReasons([]); setComment(""); setError(""); setDislike(true); } }}><ThumbsDown className="h-4 w-4" /></button>
      <span className="ml-auto" title={usage ? `${usage.calls} 次模型调用；${usage.incomplete ? "部分统计未知" : "实际记录用量"}` : "该记录未保存可关联的用量，不能推算消耗"}>{formatTokenUsage(usage)}{usageLabel ? `（${usageLabel}）` : ""}</span>
    </div>}
    <MessageTimestamp value={createdAt} />
    {notice && <p role="status">{notice}</p>}
    {assistant && linked && feedback.isError && <p role="alert">反馈读取失败。<button type="button" className="underline" onClick={() => void feedback.refetch()}>重试</button></p>}
    {error && !dislike && <p role="alert" className="text-destructive">{error}</p>}
    <Modal open={dislike} onClose={() => { if (!busy) setDislike(false); }} title="反馈：回答不满意">
      <div className="space-y-3 text-sm">
        <p>请选择原因（可多选）：</p>
        <div className="grid grid-cols-2 gap-2">{reasonsList.map(reason => <label key={reason} className="flex items-center gap-2"><input type="checkbox" disabled={busy} checked={reasons.includes(reason)} onChange={event => setReasons(values => event.target.checked ? [...values, reason] : values.filter(value => value !== reason))} />{reason}</label>)}</div>
        <textarea aria-label="补充说明（可选）" placeholder="补充说明（可选）" disabled={busy} maxLength={5000} rows={3} value={comment} onChange={event => setComment(event.target.value)} className="w-full rounded-md border border-border bg-background px-3 py-2" />
        {error && <p role="alert" className="text-destructive">{error}</p>}
        <div className="flex justify-end gap-2"><Button variant="outline" size="sm" disabled={busy} onClick={() => setDislike(false)}>取消</Button><Button size="sm" disabled={busy} onClick={() => void vote("down")}>{busy ? "保存中…" : "提交"}</Button></div>
      </div>
    </Modal>
  </footer>;
}
