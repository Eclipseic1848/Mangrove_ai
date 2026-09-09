import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { api, ApiError } from "@/lib/api";
import { getTaskContextOptions, type TaskTemplateOption, type OwnerMemoryOption } from "@/lib/semanticWorkspaceApi";

const field = "mt-1 w-full rounded-lg border bg-background px-3 py-2 text-sm focus-visible:ring-2 focus-visible:ring-ring";
const button = "rounded-lg border px-3 py-2 text-sm hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50";
type Draft = Omit<TaskTemplateOption, "summary_sha256">;
const blank = (): Draft => ({ template_id: crypto.randomUUID(), version: 1, title: "", purpose: "general", source: "owner_created", goal_contract_draft: "", method_draft: "", delivery_spec_draft: {} });

// 原位管理不卸载任务输入；此组件复用版本表与个人记忆API，不执行任务。
export function TaskContextLibrary({ open, onClose, onChanged, taskMode = false }: { open: boolean; onClose: () => void; onChanged: () => void; taskMode?: boolean }) {
  const [options, setOptions] = useState<{ templates: TaskTemplateOption[]; memories: OwnerMemoryOption[] }>({ templates: [], memories: [] });
  const [draft, setDraft] = useState<Draft>(blank);
  const baseline = useRef(JSON.stringify(draft));
  const replaceDraft = (next: Draft) => { baseline.current = JSON.stringify(next); setDraft(next); };
  const [confirmClose, setConfirmClose] = useState(false);
  const continueEditing = useRef<HTMLButtonElement>(null);
  const [memory, setMemory] = useState("");
  const [busy, setBusy] = useState(false);
  const inFlight = useRef(false);
  const generation = useRef(0);
  const [error, setError] = useState("");
  const [unknown, setUnknown] = useState<{ draft: Draft; expected_version: number } | null>(null);
  const close = useRef<HTMLButtonElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  const refresh = async () => {
    const epoch = ++generation.current;
    try { const result = await getTaskContextOptions("web_research"); if (epoch === generation.current) setOptions(result); }
    catch (reason) { if (epoch === generation.current) setError(reason instanceof Error ? reason.message : "选项读取失败"); }
  };
  useEffect(() => { if (open) void refresh(); return () => { generation.current++; }; }, [open]);
  const run = async (action: () => Promise<unknown>) => {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError("");
    try { await action(); onChanged(); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "操作结果未知，请核对后继续"); }
    finally { inFlight.current = false; setBusy(false); }
  };
  const save = () => run(async () => {
    const payload = unknown ?? { draft, expected_version: draft.version - 1 };
    setUnknown(payload);
    try { await api.post("/api/semantic-workspace/context-templates", payload); setUnknown(null); replaceDraft(blank()); }
    catch (reason) { if (reason instanceof ApiError && [404, 409, 422].includes(reason.status)) setUnknown(null); throw reason; }
  });
  const requestClose = () => {
    // 在途尚未返回；未知结果允许显式放弃本地编辑，但不能谎称撤销服务端保存。
    if (inFlight.current) { setError("保存尚未确认，请先核对原操作再离开。"); return; }
    if (unknown || JSON.stringify(draft) !== baseline.current || memory.trim()) setConfirmClose(true);
    else onClose();
  };
  return <Dialog.Root open={open} onOpenChange={value => { if (!value) requestClose(); }}><Dialog.Portal><Dialog.Overlay className="fixed inset-0 z-50 bg-black/40" />
    <Dialog.Content aria-describedby="task-context-description" className="fixed left-1/2 top-1/2 z-50 max-h-[90dvh] w-[calc(100%_-_1.5rem)] max-w-2xl -translate-x-1/2 -translate-y-1/2 overflow-auto rounded-xl border bg-background p-4 shadow-xl" onOpenAutoFocus={event => { event.preventDefault(); returnFocus.current = document.activeElement as HTMLElement; close.current?.focus(); }} onCloseAutoFocus={event => { event.preventDefault(); if (returnFocus.current?.isConnected) returnFocus.current.focus(); }} onKeyDown={event => { if (event.key === "Enter" && event.nativeEvent.isComposing) event.preventDefault(); }}>
      <div className="flex items-center justify-between gap-3"><Dialog.Title className="font-semibold">任务模板与个人记忆</Dialog.Title><button ref={close} type="button" className={button} onClick={requestClose}>{taskMode ? "返回原任务" : "关闭管理"}</button></div>
      <Dialog.Description id="task-context-description" className="mt-2 text-sm text-muted-foreground">只提供起点与方法；当前任务要求优先。{taskMode ? "原输入与附件保留。" : "此处管理不会启动任务。"}编辑会生成新版本，旧任务不变。</Dialog.Description>
      {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
      {unknown && <p role="status" className="mt-3 text-sm">上次保存尚未确认；只能确认原版本保存，不能更换内容重试。</p>}
      <section className="mt-4 space-y-3" aria-label="我的任务模板"><h3 className="font-medium">我的任务模板</h3>
        {options.templates.map(item => <div key={item.template_id} className="rounded-lg border p-3 text-sm"><p>{item.title} · V{item.version}</p><p className="mt-1 whitespace-pre-wrap">{item.goal_contract_draft}</p><p className="mt-1 whitespace-pre-wrap text-muted-foreground">{item.method_draft}</p><div className="mt-2 flex flex-wrap gap-2">
          <button type="button" className={button} disabled={busy || Boolean(unknown)} onClick={() => { const { summary_sha256: _, ...copy } = item; replaceDraft({ ...copy, version: item.version + 1 }); }}>编辑 {item.title}</button>
          <button type="button" className={button} disabled={busy || Boolean(unknown)} onClick={() => void run(() => api.del(`/api/semantic-workspace/context-templates/${encodeURIComponent(item.template_id)}?version=${item.version}`))}>停用 {item.title}</button>
        </div></div>)}
        <fieldset disabled={busy || Boolean(unknown)} className="space-y-2"><legend className="text-sm">{draft.version === 1 ? "创建任务模板" : `编辑为 V${draft.version}`}</legend>
          <label className="block text-sm">模板名称<input className={field} value={draft.title} maxLength={160} onChange={event => setDraft(current => ({ ...current, title: event.target.value }))} /></label>
          <label className="block text-sm">目标建议<textarea className={field} value={draft.goal_contract_draft} maxLength={4000} onChange={event => setDraft(current => ({ ...current, goal_contract_draft: event.target.value }))} /></label>
          <label className="block text-sm">方法建议<textarea className={field} value={draft.method_draft} maxLength={4000} onChange={event => setDraft(current => ({ ...current, method_draft: event.target.value }))} /></label>
          <label className="block text-sm">建议输出<select className={field} value={JSON.stringify(draft.delivery_spec_draft.formats ?? [])} onChange={event => setDraft(current => ({ ...current, delivery_spec_draft: { formats: JSON.parse(event.target.value) } }))}><option value="[]">遵循当前任务选择</option>{["json", "csv", "markdown", "docx", "pdf", "xlsx", "jsonl", "parquet", "txt", "pptx", "html"].map(format => <option key={format} value={JSON.stringify([format])}>{format}</option>)}{Array.isArray(draft.delivery_spec_draft.formats) && draft.delivery_spec_draft.formats.length > 1 && <option value={JSON.stringify(draft.delivery_spec_draft.formats)}>原多格式建议：{draft.delivery_spec_draft.formats.join("、")}</option>}</select></label>
        </fieldset>
        <div className="flex flex-wrap gap-2"><button type="button" className={button} disabled={busy || !draft.title.trim() || !draft.goal_contract_draft.trim()} onClick={() => void save()}>{unknown ? "确认原版本保存" : "保存新版本"}</button><button type="button" className={button} disabled={busy || Boolean(unknown)} onClick={() => { replaceDraft(blank()); setError(""); }}>取消编辑</button></div>
      </section>
      <section className="mt-5 space-y-3 border-t pt-4" aria-label="本人记忆"><h3 className="font-medium">本人记忆</h3><p className="text-xs text-muted-foreground">只在任务中明确选择后应用；管理不会自动应用全部记忆。</p>
        {options.memories.map(item => <div key={item.memory_id} className="flex items-start gap-3 text-sm"><p className="min-w-0 flex-1 break-words">{item.summary}</p><button type="button" className={button} disabled={busy} onClick={() => void run(() => api.del(`/api/memory/self/${item.memory_id}`))}>删除记忆 {item.memory_id}</button></div>)}
        <label className="block text-sm">新增个人偏好<textarea className={field} value={memory} maxLength={4000} disabled={busy} onChange={event => setMemory(event.target.value)} /></label><button type="button" className={button} disabled={busy || !memory.trim()} onClick={() => void run(async () => { await api.post("/api/memory/self", { text: memory.trim() }); setMemory(""); })}>保存个人记忆</button>
      </section>
    </Dialog.Content></Dialog.Portal>
    <Dialog.Root open={confirmClose} onOpenChange={setConfirmClose}><Dialog.Portal>
      <Dialog.Overlay className="fixed inset-0 z-[60] bg-black/40" />
      <Dialog.Content role="alertdialog" className="fixed left-1/2 top-1/2 z-[60] w-[calc(100%_-_1.5rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 space-y-4 rounded-xl border bg-background p-4 shadow-xl" onOpenAutoFocus={event => { event.preventDefault(); continueEditing.current?.focus(); }}>
        <Dialog.Title className="font-semibold">离开未保存编辑</Dialog.Title>
        <Dialog.Description className="text-sm text-muted-foreground">{unknown ? "服务端可能已经保存。放弃仅清除本地待确认编辑，不会撤销服务端保存；可继续确认原版本，或返回后重新查看目录。" : "模板或个人记忆有未保存内容。继续编辑，或明确放弃这些内容后返回；任务资料不受影响。"}</Dialog.Description>
        <div className="flex flex-wrap gap-2"><button ref={continueEditing} type="button" className={button} onClick={() => setConfirmClose(false)}>继续编辑</button><button type="button" className={button} onClick={() => { replaceDraft(blank()); setMemory(""); setUnknown(null); setError(""); setConfirmClose(false); onClose(); }}>{unknown ? "放弃本地待确认编辑并返回" : "放弃未保存内容并返回"}</button></div>
      </Dialog.Content>
    </Dialog.Portal></Dialog.Root>
    </Dialog.Root>;
}
