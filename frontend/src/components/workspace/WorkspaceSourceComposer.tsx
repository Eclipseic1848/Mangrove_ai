import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ComponentProps } from "react";
import { TaskContextLibrary } from "./TaskContextLibrary";
import { TaskComposer, type WebIntakeDraft } from "./TaskComposer";
import { WebSourceIntake } from "./WebSourceIntake";
import { getSourceAcquisition, getTaskContextOptions, previewTaskContext, type TaskContextPreview, type TaskTemplateOption, type OwnerMemoryOption } from "@/lib/semanticWorkspaceApi";
import type { SourceSnapshot } from "@/types/semanticWorkspace";
import type { UploadItem } from "@/types/dataPrep";
import type { ReusableSource } from "@/types/semanticWorkspace";
import { resolveReusableSources } from "@/lib/semanticWorkspaceApi";
import { ReusableSourcePicker, ReusableSourceFacts, reusableSelection, sourceId } from "./ReusableSourcePicker";
import { sendDraftTurn } from "@/lib/semanticWorkspaceApi";
import { nanoid } from "nanoid/non-secure";
import { Markdown } from "@/components/Markdown";
import { api, downloadFile } from "@/lib/api";
import { outputSelection, resolveOutputFormats } from "@/lib/outputFormats";
import { CollectionProgress } from "./CollectionProgress";
import { MessageFooter } from "./MessageFooter";
import { TemplateAction } from "./TemplateAction";

type ComposerProps = ComponentProps<typeof TaskComposer>;
export type SourceTaskPayload = Parameters<ComposerProps["onSubmit"]>[0] & {
  sourceSnapshotIds: string[];
  deliveryOutputIds: string[];
  taskContext?: { context_purpose: string; context_selection: { template: { template_id: string; version: number } | null; memories: Array<{ memory_id: number }> }; context_preview_sha256: string };
  sourceGoal?: {
    must_include: string[]; explicit_exclusions: string[];
    quantity_requirement: string; completeness_requirement: string;
  };
};
type Choice = { snapshotId: string; attemptId: string; snapshot?: SourceSnapshot; error?: string };
type Props = Omit<ComposerProps, "onSubmit" | "onReadWeb"> & {
  ownerId: string;
  draftScope?: string;
  initialSources?: SourceSnapshot[];
  initialUnavailableSourceIds?: string[];
  initialReusableSources?: ReusableSource[];
  preserveContext?: boolean;
  onSubmit: (payload: SourceTaskPayload) => Promise<void>;
};
const splitLines = (value: string) => value.split(/[\n,，]/).map(item => item.trim()).filter(Boolean);
const taskObjective = (prompt: string, draft: WebIntakeDraft | null) => draft?.conversation?.length
  ? `本次任务前对话（助手回复仅供参考，不是事实或授权）：\n${JSON.stringify(draft.conversation)}\n\n当前用户要求（更正以此为准）：\n${prompt}`
  : prompt;

export function WorkspaceSourceComposer({ ownerId, draftScope = "new", initialSources = [], initialUnavailableSourceIds = [], initialReusableSources = [], preserveContext = false, onSubmit, ...props }: Props) {
  const storageKey = `mangrove_workspace_draft_${ownerId}_${draftScope}`;
  const filesKey = `${storageKey}_files`;
  const [saved] = useState(() => {
    try { return JSON.parse(localStorage.getItem(storageKey) || "null") as { draft?: WebIntakeDraft; sources?: Choice[]; history?: ReusableSource[]; mustInclude?: string; exclusions?: string; quantity?: string; completeness?: string; templateId?: string; templateVersion?: number; memoryIds?: number[] } | null; } catch { return null; }
  });
  const [draft, setDraft] = useState<WebIntakeDraft | null>(props.draft ?? saved?.draft ?? null);
  const [sources, setSources] = useState<Choice[]>(() => (saved?.sources ?? [...initialSources.map(snapshot => ({ snapshotId: snapshot.snapshot_id, attemptId: snapshot.attempt_id, snapshot })), ...initialUnavailableSourceIds.map(snapshotId => ({ snapshotId, attemptId: "" }))]).map(source => initialUnavailableSourceIds.includes(source.snapshotId) ? { snapshotId: source.snapshotId, attemptId: "", error: "来源已删除，请明确移除此组后换新资料；不会重新采集。" } : source));
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [history, setHistory] = useState<ReusableSource[]>(() => saved?.history ?? initialReusableSources.filter(item => item.kind === "delivery_output"));
  const [restoringHistory, setRestoringHistory] = useState(true);
  const [pickerOpen, setPickerOpen] = useState(false);
  const pickerTrigger = useRef<HTMLElement | null>(null);
  const [webOpen, setWebOpen] = useState(() => { try { return Boolean(localStorage.getItem(`mangrove_web_source_attempt_${ownerId}${draftScope === "new" ? "" : `_${draftScope}`}`)); } catch { return false; } });
  const [webPrompt, setWebPrompt] = useState("");
  const [acquiring, setAcquiring] = useState(false);
  const [stale, setStale] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [mustInclude, setMustInclude] = useState(saved?.mustInclude ?? "");
  const [exclusions, setExclusions] = useState(saved?.exclusions ?? "");
  const [quantity, setQuantity] = useState(saved?.quantity ?? "当前已成功读取页面中有证据的内容");
  const [completeness, setCompleteness] = useState(saved?.completeness ?? "逐来源披露失败、范围和未覆盖内容，不承诺全网完整");
  const [options, setOptions] = useState<{ templates: TaskTemplateOption[]; memories: OwnerMemoryOption[] }>({ templates: [], memories: [] });
  const [templateId, setTemplateId] = useState(saved?.templateId ?? "");
  const [templateVersion, setTemplateVersion] = useState(saved?.templateVersion ?? 0);
  const [libraryOpen, setLibraryOpen] = useState(false);
  const [optionsRevision, setOptionsRevision] = useState(0);
  const [memoryIds, setMemoryIds] = useState<number[]>(saved?.memoryIds ?? []);
  const [preview, setPreview] = useState<{ identity: string; value: TaskContextPreview } | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [error, setError] = useState("");
  const [chatting, setChatting] = useState<string | null>(null);
  const [collectionCheck, setCollectionCheck] = useState(0);
  const chatRequest = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const container = useRef<HTMLDivElement>(null);
  const closeWeb = () => { setWebOpen(false); requestAnimationFrame(() => container.current?.querySelector<HTMLTextAreaElement>('textarea[aria-label="任务要求"]')?.focus()); };
  const parentDraft = useRef(props.draft);
  useLayoutEffect(() => {
    if (props.draft && props.draft !== parentDraft.current) { setDraft(props.draft); parentDraft.current = props.draft; }
  }, [props.draft]);
  useEffect(() => {
    if (!props.draft && saved?.draft) props.onDraftChange?.(saved.draft);
    return () => { generation.current += 1; chatRequest.current?.abort(); };
  }, []);
  useEffect(() => {
    let current = true;
    if (!history.length) { setRestoringHistory(false); return; }
    // 恢复只读真实引用，缓存里的标签和正文不能授予继续读取权限。
    void resolveReusableSources(reusableSelection(history)).then(response => {
      if (!current) return;
      setHistory(previous => previous.map(item => {
        const resolved = response.items.find(value => value.source_key === item.source_key);
        return resolved && resolved.sha256 === item.sha256 ? resolved : { ...item, availability: "unavailable", reason_code: "identity_changed" };
      }));
    }).catch(() => { if (current) setHistory(previous => previous.map(item => ({ ...item, availability: "unknown" }))); })
      .finally(() => { if (current) setRestoringHistory(false); });
    return () => { current = false; };
  }, []);
  useEffect(() => { if (stale || props.active === false) { generation.current++; setPickerOpen(false); chatRequest.current?.abort(); } }, [stale, props.active]);
  useEffect(() => {
    let current = true;
    for (const source of sources.filter(item => !item.snapshot && !item.error)) {
      void getSourceAcquisition(source.attemptId).then(attempt => {
        if (!current) return;
        if (attempt.snapshot?.snapshot_id !== source.snapshotId) throw new Error("网页来源不可用或身份已改变");
        setSources(previous => previous.map(item => item.snapshotId === source.snapshotId ? { ...item, snapshot: attempt.snapshot! } : item));
      }).catch(reason => { if (current) setSources(previous => previous.map(item => item.snapshotId === source.snapshotId ? { ...item, error: reason instanceof Error ? reason.message : "来源恢复失败" } : item)); });
    }
    return () => { current = false; };
  }, []);
  useEffect(() => {
    if (stale) return;
    try { localStorage.setItem(storageKey, JSON.stringify({ draft, mustInclude, exclusions, quantity, completeness, templateId, templateVersion, memoryIds,
      history: history.map(({ source_key, kind, identity, label, sha256, upload_id, output_id, acquired_at, time_kind, media_type, size_bytes, origin }) => ({ source_key, kind, identity, label, sha256, upload_id, output_id, acquired_at, time_kind, media_type, size_bytes, origin, availability: "unknown", limitations: [] })),
      sources: sources.map(({ snapshotId, attemptId }) => ({ snapshotId, attemptId })) })); } catch { /* 不存原文，存储不可用时仅当前会话保留。 */ }
  }, [draft, sources, history, storageKey, stale, mustInclude, exclusions, quantity, completeness, templateId, templateVersion, memoryIds]);
  useEffect(() => {
    const changed = (event: StorageEvent) => { if (event.key === storageKey || event.key === filesKey) { generation.current += 1; setStale(true); } };
    window.addEventListener("storage", changed);
    return () => window.removeEventListener("storage", changed);
  }, [storageKey, filesKey]);
  const hasWeb = sources.length > 0;
  useEffect(() => {
    if (preserveContext || (props.unified && !hasWeb && !templateId && !memoryIds.length && !libraryOpen)) return;
    let current = true;
    void getTaskContextOptions("web_research").then(value => { if (current) setOptions(value); }).catch(reason => { if (current) setError(reason instanceof Error ? reason.message : "上下文选项加载失败"); });
    return () => { current = false; };
  }, [preserveContext, optionsRevision, props.unified, hasWeb, templateId, memoryIds.length, libraryOpen]);
  const template = options.templates.find(item => item.template_id === templateId && item.version === templateVersion);
  const contextSelected = Boolean(templateId || memoryIds.length);
  const selectionMissing = Boolean(templateId && !template) || memoryIds.some(id => !options.memories.some(item => item.memory_id === id));
  const selection = useMemo(() => ({ template: template ? { template_id: template.template_id, version: template.version } : null, memories: memoryIds.map(memory_id => ({ memory_id })) }), [template, memoryIds]);
  const sourceIdentity = JSON.stringify([sources.map(item => [item.snapshotId, item.snapshot?.artifacts.map(artifact => [artifact.artifact_id, artifact.content_sha256])]), uploads.map(item => [item.upload_id, item.sha256]), history.map(item => [item.source_key, item.sha256, item.availability])]);
  const outputFormats = props.unified
    ? resolveOutputFormats(draft?.prompt ?? "", outputSelection(draft?.formats, draft?.formatSelection) === "manual" ? draft?.formats ?? [] : null, draft?.conversation).formats
    : draft?.formats?.length ? draft.formats : ["markdown"];
  const reviewIdentity = JSON.stringify([sourceIdentity, taskObjective(draft?.prompt ?? "", draft), outputFormats, mustInclude, exclusions, quantity, completeness, selection]);
  const currentReview = useRef(reviewIdentity);
  currentReview.current = reviewIdentity;
  const ready = !restoringHistory && history.every(item => item.availability === "available") && sources.every(item => item.snapshot && item.snapshot.coverage.status !== "hard_insufficient" && !item.error);
  const contextReady = preserveContext || (!hasWeb && !contextSelected) || (!selectionMissing && Boolean(preview?.identity === reviewIdentity && (!hasWeb || (quantity.trim() && completeness.trim()))));
  const review = async () => {
    if (!draft?.prompt.trim() || reviewing || selectionMissing) return;
    const identity = reviewIdentity;
    const request = generation.current;
    setReviewing(true); setError("");
    try {
      const value = await previewTaskContext({ purpose: "web_research", objective_text: taskObjective(draft.prompt.trim(), draft), output_formats: outputFormats, selection });
      if (request === generation.current && identity === currentReview.current) setPreview({ identity, value });
    } catch (reason) { if (request === generation.current) setError(reason instanceof Error ? reason.message : "上下文检查失败"); }
    finally { setReviewing(false); }
  };
  const updateDraft = (next: WebIntakeDraft) => { parentDraft.current = next; setDraft(next); props.onDraftChange?.(next); };
  const savedSession = draft?.sessionId ?? draft?.collection?.convId;
  const needsSavedMetadata = Boolean(draft?.conversation?.some(message => message.role === "assistant" && (!message.id || !message.created_at || !message.token_usage)));
  const [metadataRetry, setMetadataRetry] = useState(0);
  const [metadataError, setMetadataError] = useState("");
  useEffect(() => {
    if (!savedSession || !needsSavedMetadata || draft?.collection?.pending || chatting || stale || props.active === false) return;
    let active = true;
    setMetadataError("");
    void api.get(`/api/conversations/${encodeURIComponent(savedSession)}/messages`).then(messages => {
      if (!active) return;
      const latest = parentDraft.current ?? draft;
      if (!latest) return;
      // 只补回同会话且正文唯一匹配的消息，不能按位置猜编号或覆盖正在输入的需求。
      const normalized = (text: string) => text.replace(/\r\n/g, "\n").replace(/\n\n---\n\n/g, "\n\n").trim();
      const conversation = latest.conversation?.map(message => {
        if (message.id && message.created_at && message.token_usage) return message;
        const matches = messages.filter((saved: { id: number; role: string; content: string }) => saved.role === message.role && (message.id ? saved.id === message.id : normalized(saved.content) === normalized(message.content)));
        if (matches.length !== 1) return message;
        const saved = matches[0];
        return { ...message, id: saved.id, created_at: saved.created_at, token_usage: saved.meta?.token_usage, work_progress: saved.meta?.work_progress };
      });
      updateDraft({ ...latest, sessionId: savedSession, conversation });
      if (conversation?.some(message => message.role === "assistant" && !message.id)) setMetadataError("旧草稿无法唯一关联到已保存的消息，请从左侧打开对应历史会话。");
    }).catch(() => { if (active) setMetadataError("已保存消息暂时无法读取，反馈信息尚未恢复。"); });
    return () => { active = false; };
  }, [savedSession, needsSavedMetadata, draft?.collection?.pending, chatting, stale, props.active, metadataRetry]);
  useEffect(() => {
    if (!draft?.collection?.pending || chatting || stale || props.active === false) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const restore = async () => {
      try {
        const status = await api.get(`/api/chat/running/${draft.collection!.convId}`);
        if (!active) return;
        if (status.running) {
          if (status.progress?.length) updateDraft({ ...(parentDraft.current ?? draft), collection: { ...draft.collection!, progress: status.progress } });
          timer = setTimeout(restore, 2000); return;
        }
        const messages = await api.get(`/api/conversations/${draft.collection!.convId}/messages`);
        if (!active) return;
        const last = messages.at(-1);
        if (last?.role !== "assistant") throw new Error("执行结果尚未形成，请保留当前任务，勿重复发送。");
        const latest = parentDraft.current ?? draft;
        updateDraft({ ...latest, prompt: latest.prompt === draft.collection!.prompt ? "" : latest.prompt,
          chatAttempt: undefined, collection: { ...draft.collection!, pending: false, progress: last.meta?.work_progress ?? draft.collection?.progress },
          conversation: [...(draft.conversation ?? []), { role: "user", content: draft.collection!.prompt },
            { role: "assistant", content: last.content, files: last.meta?.files, id: last.id, created_at: last.created_at, token_usage: last.meta?.token_usage, work_progress: last.meta?.work_progress }] });
        setError("");
      } catch (reason) { if (active) setError(reason instanceof Error ? reason.message : "执行状态暂时无法读取"); }
    };
    void restore();
    return () => { active = false; clearTimeout(timer); };
  }, [draft?.collection?.convId, draft?.collection?.pending, chatting, stale, props.active, collectionCheck]);
  const chat = async (next: WebIntakeDraft, externalApiConfirmed: boolean) => {
    const model = next.connectionId ? next.connectionModel : next.localModel;
    if (!model) { setError("请选择可用模型"); throw new Error("请选择可用模型"); }
    const sessionId = next.sessionId ?? next.collection?.convId;
    const payload = { text: next.prompt.trim(), conv_id: sessionId, history: sessionId ? [] : (next.conversation ?? []).map(({ role, content }) => ({ role, content })), model, model_connection_id: next.connectionId, external_api_confirmed: externalApiConfirmed };
    const fingerprint = JSON.stringify(payload);
    const attempt = next.chatAttempt?.fingerprint === fingerprint ? next.chatAttempt : { requestId: nanoid(), fingerprint };
    const controller = new AbortController();
    const requestGeneration = generation.current;
    chatRequest.current = controller;
    updateDraft({ ...next, sessionId, collection: undefined, chatAttempt: attempt });
    setChatting(payload.text); setError("");
    let collection: WebIntakeDraft["collection"];
    try {
      const result = await sendDraftTurn({ ...payload, request_id: attempt.requestId }, controller.signal, {
        onMeta: value => {
          if (controller.signal.aborted || requestGeneration !== generation.current) return;
          collection = { convId: value.conv_id, pending: true, prompt: payload.text, steps: [] };
          updateDraft({ ...next, chatAttempt: attempt, collection });
        },
        onNode: value => {
          if (!collection || controller.signal.aborted || requestGeneration !== generation.current) return;
          collection = { ...collection, steps: [...collection.steps, value.label] };
          updateDraft({ ...next, chatAttempt: attempt, collection });
        },
        onProgress: value => {
          if (!collection || controller.signal.aborted || requestGeneration !== generation.current) return;
          collection = { ...collection, progress: [...(collection.progress ?? []), value] };
          updateDraft({ ...next, chatAttempt: attempt, collection });
        },
      });
      if (controller.signal.aborted || requestGeneration !== generation.current) throw new Error("草稿已切换");
      updateDraft({ ...next, prompt: "", sessionId: result.conv_id ?? collection?.convId ?? sessionId, chatAttempt: undefined, collection: collection ? { ...collection, pending: false } : undefined,
        conversation: [...(next.conversation ?? []), { role: "user", content: payload.text, created_at: result.user_created_at }, { role: "assistant", content: result.reply, id: result.message_id, created_at: result.created_at, token_usage: result.token_usage, work_progress: collection?.progress, ...(result.files?.length ? { files: result.files } : {}) }], formats: result.output_formats.length ? result.output_formats : next.formats });
      requestAnimationFrame(() => container.current?.querySelector<HTMLTextAreaElement>('textarea[aria-label="任务要求"]')?.focus());
    } catch (reason) {
      if (requestGeneration === generation.current && !controller.signal.aborted) setError(reason instanceof Error ? reason.message : "回复未成功，需求已保留");
      throw reason;
    } finally {
      if (chatRequest.current === controller) { chatRequest.current = null; setChatting(null); }
    }
  };
  const fields = "mt-1 w-full rounded-lg border bg-background px-3 py-2 text-sm font-normal focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
  return <div ref={container} role="group" className={props.unified ? "flex flex-1 flex-col gap-3" : "space-y-3"} aria-label="当前任务资料">
    {stale && <p role="alert" className="rounded-lg border p-3 text-sm">当前草稿已在其他页面更新，请刷新页面恢复最新选择；本页不会覆盖它。</p>}
    {(draft?.conversation?.length || chatting || draft?.chatAttempt || draft?.collection) ? <section aria-label="当前对话" className="flex-1 space-y-5 pb-6 text-sm">
      <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground"><span>本次对话 · {draft?.sessionId || draft?.collection?.convId ? "已保存到当前账号" : "保存在当前浏览器草稿"}</span><button type="button" disabled={Boolean(chatting) || submitting || stale || props.active === false} className="rounded-lg px-2 py-1 hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring" onClick={() => {
        if (draft && !draft.collection?.pending && window.confirm("清空当前对话草稿？已保存的历史、文件和输入框内容会保留。")) { updateDraft({ ...draft, sessionId: undefined, conversation: [], chatAttempt: undefined, collection: undefined }); setError(""); }
      }}>清空对话</button></div>
      {draft?.conversation?.map((message, index) => <article key={index} aria-label={message.role === "user" ? "我的消息" : "智能体回复"} className={message.role === "user" ? "ml-8 rounded-2xl bg-muted px-4 py-3" : "px-1 py-2"}>
        <Markdown safeResources>{message.content}</Markdown>
        {message.role === "assistant" && message.content.includes("沉淀为模板") && <TemplateAction convId={savedSession} messageId={message.id} />}
        {message.work_progress?.length ? <CollectionProgress pending={false} progress={message.work_progress} /> : null}
        {message.files?.filter(file => /^\/api\/downloads\/[^?#]+$/.test(file.url)).map(file => <button key={file.url} type="button" className="mt-2 mr-2 rounded-lg border px-3 py-2 text-xs hover:bg-muted" onClick={() => void downloadFile(file.url, file.name).catch(reason => setError(reason instanceof Error ? reason.message : "下载失败"))}>下载 {file.name}</button>)}
        <MessageFooter convId={draft.sessionId ?? draft.collection?.convId} messageId={message.id} content={message.content} createdAt={message.created_at} usage={message.token_usage} assistant={message.role === "assistant"} />
      </article>)}
      {(chatting || draft?.collection?.pending) && <p className="ml-8 whitespace-pre-wrap rounded-2xl bg-muted px-4 py-3">{chatting || draft?.collection?.prompt}</p>}
      {(chatting || draft?.collection?.pending || (draft?.collection && !draft.conversation?.some(message => message.work_progress?.length))) && <CollectionProgress pending={Boolean(chatting || draft?.collection?.pending)} preparing={!draft?.collection} progress={draft?.collection?.progress} steps={draft?.collection?.steps} />}
      {chatting && !draft?.collection && <button type="button" className="rounded-lg border px-3 py-2 text-xs focus-visible:ring-2 focus-visible:ring-ring" onClick={() => { chatRequest.current?.abort(); setError("已停止等待回复，需求已保留；外部模型可能仍在处理，不会自动重发。"); }}>停止等待</button>}
      {draft?.collection?.pending && <button type="button" className="rounded-lg border px-3 py-2 text-xs" onClick={() => void api.post(`/api/chat/${draft.collection!.convId}/cancel`).then(() => chatRequest.current?.abort()).catch(reason => setError(reason instanceof Error ? reason.message : "停止执行未成功"))}>停止执行</button>}
      {draft?.collection?.pending && !chatting && <button type="button" className="ml-2 rounded-lg border px-3 py-2 text-xs" onClick={() => setCollectionCheck(value => value + 1)}>重新读取执行状态</button>}
    </section> : null}
    {error && <p role="alert" className="rounded-lg border border-destructive/30 px-3 py-2 text-sm text-destructive">{error}</p>}
    {metadataError && <p role="alert" className="text-sm text-amber-700">{metadataError}<button type="button" className="ml-2 underline" onClick={() => setMetadataRetry(value => value + 1)}>重新读取</button></p>}
    <TaskComposer {...props} draft={draft} onDraftChange={updateDraft} active={props.active !== false && !stale} uploadStorageKey={filesKey}
      onChat={props.unified ? props.onChat ?? chat : undefined}
      webSourceCount={sources.length} additionalSourceCount={history.length} additionalInputFormats={history.map(item => { const format = item.label.split(".").pop()?.toLowerCase() || ""; return format === "md" ? "markdown" : format; })}
      sourceBusy={props.sourceBusy || acquiring || submitting || restoringHistory} submitBlocked={props.submitBlocked || stale || Boolean(draft?.collection?.pending) || !ready || !contextReady || webOpen || pickerOpen || libraryOpen}
      onPickSources={() => { pickerTrigger.current = document.activeElement as HTMLElement; setPickerOpen(true); }}
      sourceIdentity={sourceIdentity}
      onUploadsChange={value => { setUploads(value); props.onUploadsChange?.(value); }}
      onReadWeb={value => { updateDraft(value); setWebPrompt(value.prompt); setWebOpen(true); }}
      onSubmit={async payload => {
        if (stale || !ready || !contextReady || webOpen || pickerOpen || libraryOpen) return;
        const requestGeneration = generation.current;
        const storedDraft = localStorage.getItem(storageKey);
        const storedFiles = localStorage.getItem(filesKey);
        setSubmitting(true);
        try {
          // 原话完整传入既有执行器；助手回复仅供解析指代，不提升为用户授权或来源事实。
          if (draft?.conversation?.length) {
            const prompt = taskObjective(payload.prompt, draft);
            if (prompt.length > 20000) throw new Error("本次对话过长，请概括需求后清空对话再发送；不会静默丢弃早先要求。");
            payload = { ...payload, prompt };
          }
          if (history.length) {
            const resolved = await resolveReusableSources(reusableSelection(history));
            if (requestGeneration !== generation.current) return;
            if (history.some(item => !resolved.items.some(value => value.source_key === item.source_key && value.sha256 === item.sha256 && value.availability === "available"))) {
              setHistory(current => current.map(item => resolved.items.find(value => value.source_key === item.source_key && value.sha256 === item.sha256) ?? { ...item, availability: "unavailable" }));
              throw new Error("历史资料已不可用，请核对当前选择");
            }
          }
          const allUploads = [...new Map([...payload.uploads, ...history.filter(item => item.kind === "upload").map(item => ({ upload_id: sourceId(item), original_name: item.label, media_type: item.media_type || "", size_bytes: item.size_bytes ?? 0, sha256: item.sha256 || "" }))].map(item => [item.upload_id, item])).values()];
          await onSubmit({ ...payload, uploads: allUploads, deliveryOutputIds: history.filter(item => item.kind === "delivery_output").map(sourceId), sourceSnapshotIds: sources.map(item => item.snapshotId), ...(!preserveContext && preview && (hasWeb || contextSelected) ? { taskContext: { context_purpose: "web_research", context_selection: selection, context_preview_sha256: preview.value.preview_sha256 } } : {}), ...(hasWeb && !preserveContext && preview ? { sourceGoal: {
            must_include: splitLines(mustInclude), explicit_exclusions: splitLines(exclusions), quantity_requirement: quantity.trim(), completeness_requirement: completeness.trim(),
          } } : {}) });
          try { if (requestGeneration === generation.current && localStorage.getItem(storageKey) === storedDraft && localStorage.getItem(filesKey) === storedFiles) { localStorage.removeItem(storageKey); localStorage.removeItem(filesKey); } } catch { /* 当前草稿已完成。 */ }
        } catch (reason) { setError(reason instanceof Error ? reason.message : "任务未创建，需求已保留"); throw reason; }
        finally { setSubmitting(false); }
      }}>
      {history.length > 0 && <section aria-label="已选历史资料" className="mt-3 space-y-3 border-t pt-3">{history.map(item => <div key={item.source_key} className="flex items-start gap-3 border-b pb-3"><div className="min-w-0 flex-1"><p className="break-all text-sm font-medium">{item.label}</p><ReusableSourceFacts item={item} /></div><button type="button" aria-label={`移除历史资料 ${item.label}`} disabled={submitting} className="shrink-0 rounded border px-2 py-1 text-xs hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring" onClick={() => { generation.current++; setHistory(current => current.filter(value => value.source_key !== item.source_key)); }}>从本次移除</button></div>)}<p className="text-xs text-muted-foreground">只移除本次引用，不删除历史资料。</p></section>}
      {hasWeb && <section className="mt-3 space-y-3 border-t pt-3" aria-label="已选网页资料">
        {sources.map(source => <div key={source.snapshotId} className="rounded-xl border p-3 text-xs">
          <div className="flex items-start justify-between gap-3"><div className="min-w-0 break-words">
            <p className="font-medium">{source.snapshot?.allowed_scope.query || source.snapshot?.artifacts[0]?.title || source.snapshotId}</p>
            <p className="mt-1 text-muted-foreground">{source.snapshot ? `${source.snapshot.valid_page_count} 页正文 · ${source.snapshot.failed_page_count} 页失败 · ${new Date(source.snapshot.created_at).toLocaleString("zh-CN")}` : source.error || "正在恢复网页来源…"}</p>
          </div><button type="button" disabled={submitting} aria-label={`移除网页组 ${source.snapshot?.allowed_scope.query || source.snapshot?.artifacts[0]?.title || source.snapshotId}`} className="shrink-0 rounded border px-2 py-1 hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => { generation.current += 1; setSources(current => current.filter(item => item.snapshotId !== source.snapshotId)); }}>移除此组</button></div>
          {source.snapshot && <p className="mt-2 text-muted-foreground">{source.snapshot.coverage.status === "scope_complete" ? "已覆盖授权范围" : source.snapshot.coverage.status === "hard_insufficient" ? "硬性目标未满足" : "覆盖范围仍有未知，不代表完整"}{source.snapshot.coverage.search_report && ` · 发现 ${source.snapshot.coverage.search_report.discovered_count} 条链接 · 尚未读到正文 ${source.snapshot.coverage.search_report.candidates.filter(item => item.status !== "read").length} 条`}</p>}
          {source.snapshot?.allowed_scope.kind === "public_search" && <p className="mt-1 text-muted-foreground">时间：{({ any: "不限", day: "最近一天", week: "最近一周", month: "最近一月", year: "最近一年" })[source.snapshot.allowed_scope.time_range ?? "any"]} · 域名：{source.snapshot.allowed_scope.domains?.join("、") || "公开网页不限域名"}</p>}
          {source.snapshot?.coverage.status === "hard_insufficient" && <p role="alert" className="mt-2 text-destructive">此组有效页面不足，当前仅供查看；其他资料不能抵消该组硬性缺口。</p>}
          {source.snapshot && <details className="mt-2"><summary className="cursor-pointer">查看已读页面与范围</summary><p className="mt-2 break-all">{source.snapshot.allowed_scope.normalized_url || source.snapshot.allowed_scope.query} · {source.snapshot.allowed_scope.kind === "public_search" ? "公开搜索" : source.snapshot.allowed_scope.kind === "same_site" ? "同站有限范围" : "精确页面"}；摘要可能截断，完整原文在任务资料包。</p>
            {source.snapshot.coverage.search_report?.candidates.filter(item => item.status !== "read").map((item, index) => <p key={`${item.url}-${index}`} className="mt-2 break-all text-muted-foreground">{item.status === "discovered" ? "仅发现链接，尚未读取" : item.status === "scope_denied" ? "超出范围，未读取" : "读取失败"}：{item.title || item.url} · {item.url}{item.message ? ` · ${item.message}` : ""}</p>)}
            {source.snapshot.failures.map(item => <p key={item.failure_id} className="mt-2 break-all text-muted-foreground">未读取：{item.request_url} · {item.error_message}</p>)}
            {source.snapshot.artifacts.map(item => <article key={item.artifact_id} className="mt-2 border-t pt-2"><p className="font-medium">{item.title || item.final_url}</p><p className="break-all text-muted-foreground">{item.final_url}</p><p className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap">{item.text_preview}</p></article>)}</details>}
        </div>)}
        <p className="text-xs text-muted-foreground">确认时使用上方文件与全部已选网页组。逐来源保留缺口，排序不代表冲突优先级。</p>
        {preserveContext && <p className="text-xs text-muted-foreground">本次保留原任务目标与上下文，确认后按当前完整资料创建新版本；旧版本不变。</p>}
      </section>}
      {!preserveContext && (!props.unified || hasWeb || contextSelected) && <section aria-label="任务上下文" className="mt-3 space-y-3 border-t pt-3"><p className="text-xs text-muted-foreground">模板与个人记忆只提供建议；当前任务要求和输出格式优先。</p><button type="button" className="rounded-lg border px-3 py-2 text-xs hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring" onClick={() => setLibraryOpen(true)}>管理任务模板与记忆</button>
        {selectionMissing && <p role="alert" className="text-xs text-destructive">所选模板版本或记忆已不可用，请重新选择或明确取消；不会自动换成新版。</p>}
        {preserveContext ? <p className="text-xs text-muted-foreground">本次保留原任务目标与上下文，确认后按当前完整资料创建新版本；旧版本不变。</p> : <>
        {hasWeb && <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-xs font-medium">必须包含<textarea rows={2} className={fields} value={mustInclude} onChange={event => setMustInclude(event.target.value)} /></label>
          <label className="text-xs font-medium">明确不要<textarea rows={2} className={fields} value={exclusions} onChange={event => setExclusions(event.target.value)} /></label>
          <label className="text-xs font-medium">数量要求<input className={fields} value={quantity} onChange={event => setQuantity(event.target.value)} /></label>
          <label className="text-xs font-medium">完整性要求<input className={fields} value={completeness} onChange={event => setCompleteness(event.target.value)} /></label>
        </div>}
        <label className="block text-xs font-medium">任务模板（可选）<select aria-label="任务模板（可选）" className={fields} value={templateId ? JSON.stringify([templateId, templateVersion]) : ""} onChange={event => { const selected = options.templates.find(item => JSON.stringify([item.template_id, item.version]) === event.target.value); setTemplateId(selected?.template_id ?? ""); setTemplateVersion(selected?.version ?? 0); }}><option value="">不使用模板</option>{templateId && !template && <option disabled value={JSON.stringify([templateId, templateVersion])}>已选版本不可用</option>}{options.templates.map(item => <option key={item.template_id} value={JSON.stringify([item.template_id,item.version])}>{item.title} · {item.source === "legacy_library" ? `内容版 ${item.summary_sha256.slice(7, 15)}` : `V${item.version}`}</option>)}</select></label>
        <div className="space-y-2">{memoryIds.filter(id => !options.memories.some(item => item.memory_id === id)).map(id => <button key={id} type="button" className="rounded-lg border px-3 py-2 text-xs focus-visible:ring-2 focus-visible:ring-ring" onClick={() => setMemoryIds(current => current.filter(value => value !== id))}>移除已不可用的记忆 {id}</button>)}</div><details><summary className="cursor-pointer text-xs font-medium">个人记忆（可选）</summary>{options.memories.map(item => <label key={item.memory_id} className="mt-2 flex gap-2 text-xs"><input type="checkbox" checked={memoryIds.includes(item.memory_id)} onChange={event => setMemoryIds(current => event.target.checked ? [...current, item.memory_id] : current.filter(id => id !== item.memory_id))} />{item.summary}</label>)}</details>
        <button type="button" disabled={reviewing || selectionMissing || !ready || !draft?.prompt.trim()} onClick={() => void review()} className="rounded-lg border px-3 py-2 text-xs hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">{reviewing ? "正在检查草案" : contextReady ? "重新检查草案" : "检查上下文草案"}</button>
        {contextReady && preview && <div role="status" className="text-xs"><p>已检查，可以启动</p><p>当前要求：{draft?.prompt}</p><p>输出：{outputFormats.join("、")}</p><p>建议目标：{preview.value.proposed_changes.goal_contract || "无模板建议"}</p><p>建议方法：{preview.value.proposed_changes.method || "无额外方法"}</p><p>{preview.value.template ? preview.value.template.source === "legacy_library" ? `模板内容版 ${preview.value.template.summary_sha256.slice(7, 15)}` : `模板 V${preview.value.template.version}` : "未选模板"} · 已选 {preview.value.memories.length} 条本人记忆</p></div>}
        </>}
      </section>}
    </TaskComposer>
    {!preserveContext && <TaskContextLibrary key={ownerId} taskMode open={libraryOpen} onClose={() => setLibraryOpen(false)} onChanged={() => { setPreview(null); setOptionsRevision(value => value + 1); }} />}
    {pickerOpen && <ReusableSourcePicker selectedKeys={[...uploads.map(item => `upload:${item.upload_id}`), ...sources.map(item => `snapshot:${item.snapshotId}`), ...history.map(item => item.source_key)]}
      onClose={added => { generation.current++; setPickerOpen(false); requestAnimationFrame(() => { if (added) container.current?.querySelector<HTMLTextAreaElement>('textarea[aria-label="任务要求"]')?.focus(); else pickerTrigger.current?.focus(); }); }}
      onAdd={async items => {
        const requestGeneration = generation.current;
        const snapshots = await Promise.all(items.filter(item => item.kind === "snapshot").map(async item => {
          if (!item.attempt_id) throw new Error("网页快照缺少恢复身份");
          const attempt = await getSourceAcquisition(item.attempt_id);
          if (!attempt.snapshot || attempt.snapshot.snapshot_id !== item.source_snapshot_id) throw new Error("网页快照身份已变化");
          return { snapshotId: attempt.snapshot.snapshot_id, attemptId: item.attempt_id, snapshot: attempt.snapshot };
        }));
        if (requestGeneration !== generation.current) return;
        setSources(current => [...new Map([...current, ...snapshots].map(item => [item.snapshotId, item])).values()]);
        setHistory(current => [...new Map([...current, ...items.filter(item => item.kind !== "snapshot" && !uploads.some(upload => upload.upload_id === item.upload_id))].map(item => [item.source_key, item])).values()]);
        const next = draft ?? { prompt: "", connectionId: null, connectionModel: null, localModel: props.defaultModel?.model ?? null };
        updateDraft({ ...next, formats: next.formats?.length ? next.formats : ["markdown"] });
      }} />}
    {webOpen && <section aria-label="网页资料" className="rounded-xl border p-3">
      <button type="button" disabled={acquiring} onClick={closeWeb} className="mb-3 rounded-lg border px-3 py-2 text-xs hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">返回当前资料</button>
      <WebSourceIntake draft={draft} initialPrompt={webPrompt} ownerId={ownerId} storageScope={draftScope === "new" ? undefined : draftScope}
        allowLocalRuntime={Boolean(props.allowLocalPiRuntime)} localModels={(props.modelOptions ?? []).filter(item => item.provider === "local").map(item => ({ model: item.model, label: item.label }))}
        defaultLocalModel={draft?.localModel ?? null} modelConnections={props.modelConnections ?? []} defaultConnectionId={draft?.connectionId ?? null} defaultConnectionModel={draft?.connectionModel ?? null}
        onTaskCreated={() => undefined} onAcquisitionBusy={setAcquiring} onSourceSelected={(snapshot, attemptId) => {
          setSources(current => current.some(item => item.snapshotId === snapshot.snapshot_id) ? current : [...current, { snapshotId: snapshot.snapshot_id, attemptId, snapshot }]);
          const next = draft ?? { prompt: webPrompt, connectionId: null, connectionModel: null, localModel: props.defaultModel?.model ?? null };
          updateDraft({ ...next, prompt: next.prompt || snapshot.allowed_scope.query || "", formats: next.formats?.length ? next.formats : ["markdown"] });
          closeWeb();
        }} />
    </section>}
  </div>;
}
