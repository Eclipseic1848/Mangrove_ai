import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ComponentProps } from "react";
import { TaskComposer, type WebIntakeDraft } from "./TaskComposer";
import { WebSourceIntake } from "./WebSourceIntake";
import { getSourceAcquisition, getTaskContextOptions, previewTaskContext, type TaskContextPreview, type TaskTemplateOption, type OwnerMemoryOption } from "@/lib/semanticWorkspaceApi";
import type { SourceSnapshot } from "@/types/semanticWorkspace";
import type { UploadItem } from "@/types/dataPrep";

type ComposerProps = ComponentProps<typeof TaskComposer>;
export type SourceTaskPayload = Parameters<ComposerProps["onSubmit"]>[0] & {
  sourceSnapshotIds: string[];
  sourceGoal?: {
    must_include: string[]; explicit_exclusions: string[];
    quantity_requirement: string; completeness_requirement: string;
    context_purpose: string; context_selection: { template: { template_id: string; version: number } | null; memories: Array<{ memory_id: number }> };
    context_preview_sha256: string;
  };
};
type Choice = { snapshotId: string; attemptId: string; snapshot?: SourceSnapshot; error?: string };
type Props = Omit<ComposerProps, "onSubmit" | "onReadWeb"> & {
  ownerId: string;
  draftScope?: string;
  initialSources?: SourceSnapshot[];
  preserveContext?: boolean;
  onSubmit: (payload: SourceTaskPayload) => Promise<void>;
};
const splitLines = (value: string) => value.split(/[\n,，]/).map(item => item.trim()).filter(Boolean);

export function WorkspaceSourceComposer({ ownerId, draftScope = "new", initialSources = [], preserveContext = false, onSubmit, ...props }: Props) {
  const storageKey = `mangrove_workspace_draft_${ownerId}_${draftScope}`;
  const filesKey = `${storageKey}_files`;
  const [saved] = useState(() => {
    try { return JSON.parse(localStorage.getItem(storageKey) || "null") as { draft?: WebIntakeDraft; sources?: Choice[]; mustInclude?: string; exclusions?: string; quantity?: string; completeness?: string; templateId?: string; memoryIds?: number[] } | null; } catch { return null; }
  });
  const [draft, setDraft] = useState<WebIntakeDraft | null>(props.draft ?? saved?.draft ?? null);
  const [sources, setSources] = useState<Choice[]>(() => saved?.sources ?? initialSources.map(snapshot => ({ snapshotId: snapshot.snapshot_id, attemptId: snapshot.attempt_id, snapshot })));
  const [uploads, setUploads] = useState<UploadItem[]>([]);
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
  const [memoryIds, setMemoryIds] = useState<number[]>(saved?.memoryIds ?? []);
  const [preview, setPreview] = useState<{ identity: string; value: TaskContextPreview } | null>(null);
  const [reviewing, setReviewing] = useState(false);
  const [error, setError] = useState("");
  const generation = useRef(0);
  const container = useRef<HTMLDivElement>(null);
  const closeWeb = () => { setWebOpen(false); requestAnimationFrame(() => container.current?.querySelector<HTMLTextAreaElement>('textarea[aria-label="任务要求"]')?.focus()); };
  const parentDraft = useRef(props.draft);
  useLayoutEffect(() => {
    if (props.draft && props.draft !== parentDraft.current) { setDraft(props.draft); parentDraft.current = props.draft; }
  }, [props.draft]);
  useEffect(() => () => { generation.current += 1; }, []);
  useEffect(() => {
    let current = true;
    for (const source of sources.filter(item => !item.snapshot)) {
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
    try { localStorage.setItem(storageKey, JSON.stringify({ draft, mustInclude, exclusions, quantity, completeness, templateId, memoryIds, sources: sources.map(({ snapshotId, attemptId }) => ({ snapshotId, attemptId })) })); } catch { /* 不存原文，存储不可用时仅当前会话保留。 */ }
  }, [draft, sources, storageKey, stale, mustInclude, exclusions, quantity, completeness, templateId, memoryIds]);
  useEffect(() => {
    const changed = (event: StorageEvent) => { if (event.key === storageKey || event.key === filesKey) { generation.current += 1; setStale(true); } };
    window.addEventListener("storage", changed);
    return () => window.removeEventListener("storage", changed);
  }, [storageKey, filesKey]);
  const hasWeb = sources.length > 0;
  useEffect(() => {
    if (!hasWeb || preserveContext) return;
    let current = true;
    void getTaskContextOptions("web_research").then(value => { if (current) setOptions(value); }).catch(reason => { if (current) setError(reason instanceof Error ? reason.message : "上下文选项加载失败"); });
    return () => { current = false; };
  }, [hasWeb]);
  const template = options.templates.find(item => item.template_id === templateId);
  const selection = useMemo(() => ({ template: template ? { template_id: template.template_id, version: template.version } : null, memories: memoryIds.map(memory_id => ({ memory_id })) }), [template, memoryIds]);
  const sourceIdentity = JSON.stringify([sources.map(item => [item.snapshotId, item.snapshot?.artifacts.map(artifact => [artifact.artifact_id, artifact.content_sha256])]), uploads.map(item => [item.upload_id, item.sha256])]);
  const reviewIdentity = JSON.stringify([sourceIdentity, draft?.prompt, draft?.formats, mustInclude, exclusions, quantity, completeness, selection]);
  const currentReview = useRef(reviewIdentity);
  currentReview.current = reviewIdentity;
  const ready = sources.every(item => item.snapshot && item.snapshot.coverage.status !== "hard_insufficient" && !item.error);
  const contextReady = preserveContext || !hasWeb || Boolean(preview?.identity === reviewIdentity && quantity.trim() && completeness.trim());
  const review = async () => {
    if (!draft?.prompt.trim() || reviewing) return;
    const identity = reviewIdentity;
    const request = generation.current;
    setReviewing(true); setError("");
    try {
      const value = await previewTaskContext({ purpose: "web_research", objective_text: draft.prompt.trim(), output_formats: draft.formats?.length ? draft.formats : ["markdown"], selection });
      if (request === generation.current && identity === currentReview.current) setPreview({ identity, value });
    } catch (reason) { if (request === generation.current) setError(reason instanceof Error ? reason.message : "上下文检查失败"); }
    finally { if (request === generation.current) setReviewing(false); }
  };
  const updateDraft = (next: WebIntakeDraft) => { parentDraft.current = next; setDraft(next); props.onDraftChange?.(next); };
  const fields = "mt-1 w-full rounded-lg border bg-background px-3 py-2 text-sm font-normal focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";
  return <div ref={container} className="space-y-3" aria-label="当前任务资料">
    {stale && <p role="alert" className="rounded-lg border p-3 text-sm">当前草稿已在其他页面更新，请刷新页面恢复最新选择；本页不会覆盖它。</p>}
    <TaskComposer {...props} draft={draft} onDraftChange={updateDraft} active={props.active !== false && !stale} uploadStorageKey={filesKey}
      webSourceCount={sources.length} sourceBusy={acquiring || submitting} submitBlocked={stale || !ready || !contextReady || webOpen}
      sourceIdentity={sourceIdentity}
      onUploadsChange={value => { setUploads(value); props.onUploadsChange?.(value); }}
      onReadWeb={value => { updateDraft(value); setWebPrompt(value.prompt); setWebOpen(true); }}
      onSubmit={async payload => {
        if (stale || !ready || !contextReady || webOpen) return;
        const requestGeneration = generation.current;
        const storedDraft = localStorage.getItem(storageKey);
        const storedFiles = localStorage.getItem(filesKey);
        setSubmitting(true);
        try {
          await onSubmit({ ...payload, sourceSnapshotIds: sources.map(item => item.snapshotId), ...(hasWeb && !preserveContext && preview ? { sourceGoal: {
            must_include: splitLines(mustInclude), explicit_exclusions: splitLines(exclusions), quantity_requirement: quantity.trim(), completeness_requirement: completeness.trim(),
            context_purpose: "web_research", context_selection: selection, context_preview_sha256: preview.value.preview_sha256,
          } } : {}) });
          try { if (requestGeneration === generation.current && localStorage.getItem(storageKey) === storedDraft && localStorage.getItem(filesKey) === storedFiles) { localStorage.removeItem(storageKey); localStorage.removeItem(filesKey); } } catch { /* 当前草稿已完成。 */ }
        } finally { setSubmitting(false); }
      }}>
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
        {preserveContext ? <p className="text-xs text-muted-foreground">本次保留原任务目标与上下文，确认后按当前完整资料创建新版本；旧版本不变。</p> : <>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-xs font-medium">必须包含<textarea rows={2} className={fields} value={mustInclude} onChange={event => setMustInclude(event.target.value)} /></label>
          <label className="text-xs font-medium">明确不要<textarea rows={2} className={fields} value={exclusions} onChange={event => setExclusions(event.target.value)} /></label>
          <label className="text-xs font-medium">数量要求<input className={fields} value={quantity} onChange={event => setQuantity(event.target.value)} /></label>
          <label className="text-xs font-medium">完整性要求<input className={fields} value={completeness} onChange={event => setCompleteness(event.target.value)} /></label>
        </div>
        <label className="block text-xs font-medium">任务模板（可选）<select aria-label="任务模板（可选）" className={fields} value={templateId} onChange={event => setTemplateId(event.target.value)}><option value="">不使用模板</option>{options.templates.map(item => <option key={item.template_id} value={item.template_id}>{item.title}</option>)}</select></label>
        <details><summary className="cursor-pointer text-xs font-medium">个人记忆（可选）</summary>{options.memories.map(item => <label key={item.memory_id} className="mt-2 flex gap-2 text-xs"><input type="checkbox" checked={memoryIds.includes(item.memory_id)} onChange={event => setMemoryIds(current => event.target.checked ? [...current, item.memory_id] : current.filter(id => id !== item.memory_id))} />{item.summary}</label>)}</details>
        <button type="button" disabled={reviewing || !ready || !draft?.prompt.trim()} onClick={() => void review()} className="rounded-lg border px-3 py-2 text-xs hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">{reviewing ? "正在检查草案" : contextReady ? "重新检查草案" : "检查上下文草案"}</button>
        {contextReady && preview && <div role="status" className="text-xs"><p>已检查，可以启动</p><p>{preview.value.proposed_changes.goal_contract}</p></div>}
        {error && <p role="alert" className="text-xs text-destructive">{error}</p>}
        </>}
      </section>}
    </TaskComposer>
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
