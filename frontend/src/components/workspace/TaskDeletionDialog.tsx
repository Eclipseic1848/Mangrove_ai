import { useEffect, useRef, useState } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import { nanoid } from "nanoid/non-secure";
import { ApiError } from "@/lib/api";
import { findTaskDeletionOperation, getTaskDeletionOperation, getTaskDeletionPlan, resumeTaskDeletion, submitTaskDeletion, WorkspaceDeletionError } from "@/lib/semanticWorkspaceApi";
import type { DeletionPolicy, TaskDeletionOperation, TaskDeletionPlan } from "@/types/semanticWorkspace";
import { SourceReferenceFacts } from "./ReusableSourcePicker";

type Target = { task_id: string; title: string };
type Pending = Target & { key: string; payload: { plan_token: string; shared_policy: DeletionPolicy }; operation_id?: string };
const button = "rounded-lg border px-3 py-2 text-sm hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";
const labels = { planned: "清理操作已登记", stopping: "正在停止依赖任务", cleaning: "正在清理资料", needs_confirmation: "关联已变化，需要重新确认", incomplete: "删除未完成", completed: "删除完成" };

export function TaskDeletionDialog({ ownerId, target, currentTaskId, onClose, onCompleted }: { ownerId: string; target: Target | null; currentTaskId: string | null; onClose: () => void; onCompleted: (operation: TaskDeletionOperation) => void }) {
  const storageKey = `mangrove_task_deletion_${ownerId}`;
  const stored = useRef<string | null>(null);
  const [pending, setPending] = useState<Pending | null>(() => {
    try { stored.current = localStorage.getItem(storageKey); const value = JSON.parse(stored.current || "null"); return value?.task_id && value?.key && value?.payload?.plan_token ? value : null; } catch { return null; }
  });
  const [uncertain, setUncertain] = useState(Boolean(pending));
  const [open, setOpen] = useState(Boolean(pending));
  const [policy, setPolicy] = useState<DeletionPolicy>("keep_shared");
  const [plan, setPlan] = useState<TaskDeletionPlan | null>(null);
  const [operation, setOperation] = useState<TaskDeletionOperation | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [stale, setStale] = useState(false);
  const generation = useRef(0);
  const currentPending = useRef(pending);
  currentPending.current = pending;
  const cancelButton = useRef<HTMLButtonElement>(null);
  const trigger = useRef<HTMLElement | null>(null);
  const task = pending ?? target;
  const active = () => ++generation.current;
  const save = (next: Pending | null) => {
    // 通知可能迟到；写入和完成前再次比对原身份，不能覆盖另一页面的新操作。
    if (localStorage.getItem(storageKey) !== stored.current) {
      active(); setBusy(false); setStale(true); setPlan(null); setOperation(null);
      throw new Error("清理操作已在其他页面更新，请刷新查询");
    }
    // 不保存正文；无法保存不可逆操作身份时，禁止发出新的清理请求。
    if (next) localStorage.setItem(storageKey, JSON.stringify(next)); else localStorage.removeItem(storageKey);
    stored.current = next ? JSON.stringify(next) : null;
    currentPending.current = next; setPending(next);
  };
  const accept = (next: TaskDeletionOperation, expected: Pending) => {
    if (next.task_id !== expected.task_id || (expected.operation_id && next.operation_id !== expected.operation_id)) throw new Error("清理操作身份不符，请保留原请求核对");
    if (next.state === "completed") { save(null); setOperation(next); setUncertain(false); setPlan(null); onCompleted(next); }
    else {
      save({ ...expected, operation_id: next.operation_id });
      setOperation(next); setUncertain(false);
      if (next.state === "needs_confirmation") { setPolicy("keep_shared"); setPlan(null); }
    }
  };
  async function loadPlan(nextPolicy: DeletionPolicy = "keep_shared") {
    if (!task || stale || uncertain) return;
    const request = active(); setBusy(true); setPlan(null); setError(""); setPolicy(nextPolicy);
    try {
      const next = await getTaskDeletionPlan(task.task_id, nextPolicy);
      if (request !== generation.current) return;
      if (next.task_id !== task.task_id || next.shared_policy !== nextPolicy) throw new Error("清理清单身份已变化，请重新核对");
      setPlan(next);
    } catch (reason) { if (request === generation.current) setError(reason instanceof Error ? reason.message : "清单读取失败，尚未清理资料"); }
    finally { if (request === generation.current) setBusy(false); }
  }
  async function inspect(saved: Pending = currentPending.current!) {
    if (!saved || stale) return;
    const request = active(); setBusy(true); setUncertain(true); setError("");
    try {
      const next = saved.operation_id ? await getTaskDeletionOperation(saved.operation_id) : await findTaskDeletionOperation(saved.task_id, saved.key);
      if (request === generation.current) accept(next, saved);
    } catch (reason) {
      if (request === generation.current) setError(reason instanceof ApiError && reason.status === 404 ? "尚未查到原操作，清理结果仍未知；请稍后查询，不会重新提交。" : "清理结果尚未确认，请查询原操作；不会重复删除。");
    } finally { if (request === generation.current) setBusy(false); }
  }
  useEffect(() => {
    if (!target) return;
    trigger.current = document.activeElement as HTMLElement; setOpen(true); setError("");
    if (currentPending.current) void inspect();
    else { setOperation(null); void loadPlan(); }
  }, [target?.task_id]);
  useEffect(() => {
    if (currentPending.current && !target) void inspect();
    const changed = (event: StorageEvent) => { if (event.key === storageKey) { active(); setBusy(false); setStale(true); setPlan(null); setOperation(null); } };
    window.addEventListener("storage", changed);
    return () => { active(); window.removeEventListener("storage", changed); };
  }, []);
  const close = () => {
    active(); setBusy(false); setOpen(false); setPlan(null); onClose();
    requestAnimationFrame(() => { if (trigger.current?.isConnected) trigger.current.focus(); else document.querySelector<HTMLButtonElement>('button[aria-label="任务列表开关"]')?.focus(); });
  };
  useEffect(() => { if (target && currentTaskId !== target.task_id && operation?.state !== "completed") close(); }, [currentTaskId]);
  async function commit(resume: boolean) {
    if (busy || stale || uncertain || !task) return;
    const reConfirm = operation?.state === "needs_confirmation";
    if ((!resume || reConfirm) && (!plan?.can_execute || plan.shared_policy !== policy)) return;
    const request = active(); setBusy(true); setError("");
    let saved = currentPending.current;
    try {
      if (!resume) {
        if (saved) throw new Error("原清理结果尚未确认，请查询原操作");
        saved = { task_id: task.task_id, title: task.title, key: nanoid(), payload: { plan_token: plan!.plan_token, shared_policy: policy } };
        save(saved);
      }
      // 继续同一操作前也核对身份，不能借旧窗口发出新副作用。
      if (resume) save(saved);
      const next = resume ? await resumeTaskDeletion(saved!.operation_id!, reConfirm ? { plan_token: plan!.plan_token, shared_policy: policy } : {}) : await submitTaskDeletion(saved!.task_id, saved!.payload, saved!.key);
      if (request === generation.current) accept(next, saved!);
    } catch (reason) {
      if (request !== generation.current) return;
      // 只有服务端证明尚未开始，才可以废弃确认；任何未知响应都保留原操作。
      if (!resume && reason instanceof WorkspaceDeletionError && reason.rejected) { save(null); setPolicy("keep_shared"); setPlan(null); setUncertain(false); }
      else setUncertain(true);
      setError(reason instanceof Error ? reason.message : "清理结果尚未确认，请查询原操作");
    } finally { if (request === generation.current) setBusy(false); }
  }
  const needsPlan = !uncertain && (!pending || operation?.state === "needs_confirmation");
  return <>
    {!open && pending && <button type="button" className={`${button} m-2`} onClick={() => { setOpen(true); void inspect(); }}>查看未完成的资料清理</button>}
    <AlertDialog.Root open={open} onOpenChange={value => { if (!value) close(); }}><AlertDialog.Portal>
      <AlertDialog.Overlay className="fixed inset-0 z-50 bg-black/40" />
      <AlertDialog.Content className="fixed left-1/2 top-1/2 z-50 flex max-h-[90dvh] w-[calc(100%_-_1.5rem)] max-w-2xl -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border bg-background shadow-xl" onKeyDown={event => { if (event.key === "Enter" && event.nativeEvent.isComposing) event.preventDefault(); }} onOpenAutoFocus={event => { event.preventDefault(); cancelButton.current?.focus(); }} onCloseAutoFocus={event => event.preventDefault()}>
        <div className="shrink-0 border-b p-4"><AlertDialog.Title className="font-semibold">清理任务和资料</AlertDialog.Title><AlertDialog.Description className="mt-2 text-sm text-muted-foreground">{task?.title || "原任务"}：仅核对清单不会删除。清理正文后无法恢复，其他任务及其独立结果仍保留。</AlertDialog.Description></div>
        <div className="min-h-0 space-y-4 overflow-auto p-4">
          {stale && <p role="alert">清理操作已在其他页面更新，请刷新查询；旧确认不能继续使用。</p>}
          {busy && <p role="status">正在核对清单或清理状态…</p>}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          {operation && <section aria-label="清理操作状态" className="space-y-2"><p role="status" className="font-medium">{labels[operation.state]}</p><p className="text-sm">已清理 {operation.completed_source_keys.length} 份 · 保留共享 {operation.retained_source_keys.length} 份</p>{operation.message && <p className="text-sm">{operation.message}</p>}<p className="text-xs text-muted-foreground">停止依赖任务后清理；不承诺原会话原地续跑。保留任务可能无法按原来源完整重跑或复验。</p></section>}
          {needsPlan && operation?.state !== "completed" && <fieldset disabled={busy || stale} className="space-y-2"><legend className="text-sm font-medium">共享资料如何处理</legend><label className="flex items-start gap-2 text-sm"><input type="radio" name="shared-deletion-policy" checked={policy === "keep_shared"} onChange={() => void loadPlan("keep_shared")} className="mt-1" />保留其他任务使用的资料（默认）</label><label className="flex items-start gap-2 text-sm"><input type="radio" name="shared-deletion-policy" checked={policy === "delete_shared"} onChange={() => void loadPlan("delete_shared")} className="mt-1" />同时删除共享资料，先停止依赖任务</label></fieldset>}
          {plan && <section aria-label="资料清理清单" className="space-y-3"><p className="text-sm">本次核对 {plan.objects.length} 份资料，已核对以下关联记录。</p>{plan.objects.map(item => <article key={item.source_key} className="space-y-1 rounded-lg border p-3 text-sm"><p className="break-all font-medium">{item.label}</p><p>{item.kind === "delivery_output" ? "处理结果 · 非原件" : item.kind === "web_artifact" ? "网页原文" : "原始文件"} · {item.disposition === "keep_shared" ? "保留共享资料" : "本次将清理"}</p>{item.kind === "web_artifact" && <p className="break-all text-xs text-muted-foreground">来源组：{item.snapshot_id} · 原件：{item.artifact_id}</p>}<p className="text-xs text-muted-foreground">{item.time_kind === "generated" ? "生成时间" : "取得时间"}：{item.acquired_at && item.time_kind !== "unknown" ? new Date(item.acquired_at).toLocaleString("zh-CN") : "时间未记录"}</p>{item.references.length > 0 && <details><summary className="cursor-pointer py-1">查看 {item.references.length} 条关联记录</summary><div className="space-y-2 break-words text-xs text-muted-foreground">{item.references.map((reference, index) => <p key={index}><SourceReferenceFacts item={reference} /></p>)}</div></details>}</article>)}{plan.affected_tasks.length > 0 && <div className="space-y-1 text-sm"><p>受影响任务（记录和独立结果保留）</p>{plan.affected_tasks.map((item, index) => <p key={index} className="break-all">{item.task_id}{item.revision ? ` · V${item.revision}` : ""}{!item.task_exists ? " · 原任务记录已清理" : item.in_recycle_bin ? " · 回收站中" : ""}</p>)}</div>}{plan.blockers.map((blocker, index) => <p role="alert" key={index} className="text-sm text-destructive">{blocker.message}</p>)}</section>}
          {needsPlan && !plan && !busy && operation?.state !== "completed" && <button type="button" className={button} disabled={stale} onClick={() => void loadPlan()}>重新核对清单</button>}
          {pending && <button type="button" className={button} disabled={busy || stale} onClick={() => void inspect()}>查询原操作状态</button>}
        </div>
        <div className="flex shrink-0 flex-wrap justify-end gap-2 border-t p-4"><button ref={cancelButton} type="button" className={button} onClick={close}>{pending ? "关闭查看" : operation?.state === "completed" ? "完成" : "取消"}</button>
          {!uncertain && operation?.state !== "completed" && (!pending || operation?.state === "needs_confirmation") && <button type="button" className={`${button} bg-destructive text-destructive-foreground hover:bg-destructive/90`} disabled={busy || stale || !plan?.can_execute} onClick={() => void commit(Boolean(pending))}>{pending ? "按新清单确认继续" : policy === "delete_shared" ? "确认清理任务和共享资料" : "确认清理并保留共享资料"}</button>}
          {!uncertain && pending && operation && ["planned", "incomplete"].includes(operation.state) && <button type="button" className={button} disabled={busy || stale} onClick={() => void commit(true)}>继续原清理操作</button>}
        </div>
      </AlertDialog.Content>
    </AlertDialog.Portal></AlertDialog.Root>
  </>;
}
