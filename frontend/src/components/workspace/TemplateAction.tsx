import { useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "@/lib/api";
import { useAuth } from "@/lib/auth";

type Message = { id: number; task_id?: string; meta?: { template_available?: boolean } };

export function TemplateAction({ convId, messageId }: { convId?: string; messageId?: number }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [phase, setPhase] = useState<"idle" | "saving" | "done" | "failed">("idle");
  const [notice, setNotice] = useState("");
  const submitted = useRef(false);
  const queryKey = ["collection-template-actions", user?.user_id, convId];
  const state = useQuery<Message[]>({
    queryKey, enabled: Boolean(convId && messageId), retry: false,
    queryFn: () => api.get(`/api/conversations/${encodeURIComponent(convId!)}/messages`),
    staleTime: 0,
  });
  const message = state.data?.find(item => item.id === messageId);
  const save = async () => {
    if (submitted.current || !message?.task_id || !message.meta?.template_available) return;
    submitted.current = true;
    setPhase("saving");
    try {
      const result = await api.post("/api/confirm/template", { task_id: message.task_id });
      setNotice(result.message || "模板已保存");
      setPhase("done");
    } catch (reason) {
      setNotice(`${reason instanceof Error ? reason.message : "保存结果未知"}。请先查看模板库，不会自动重试。`);
      setPhase("failed");
    } finally {
      void queryClient.invalidateQueries({ queryKey });
    }
  };
  return <section aria-label="报告模板操作" className="mt-3 space-y-2 rounded-lg border p-3 text-xs">
    {phase === "done" || phase === "failed" ? <p role={phase === "failed" ? "alert" : "status"}>{notice}</p>
      : phase === "saving" || message?.meta?.template_available ? <>
        <p>使用本次任务所选模型提炼报告结构，可能产生模型用量；保存到我的模板库。</p>
        <button type="button" onClick={() => void save()} disabled={phase === "saving"}
          className="rounded-lg border border-primary px-3 py-2 font-medium text-primary hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">
          {phase === "saving" ? "正在提炼…" : "沉淀为模板"}
        </button>
      </> : !convId || !messageId ? <p>正在关联已保存的报告；若无法恢复，请从左侧打开原会话。</p>
        : state.isError ? <p role="alert">模板操作状态读取失败。<button type="button" className="ml-2 underline" onClick={() => void state.refetch()}>重新读取</button></p>
          : state.isPending ? <p role="status">正在检查模板操作…</p>
            : <p>模板操作已处理或已过期。请先查看模板库；如未保存，需重新执行任务后提炼。</p>}
    <Link to="/templates" className="block w-fit rounded text-primary underline focus-visible:ring-2 focus-visible:ring-ring">查看我的模板</Link>
  </section>;
}
