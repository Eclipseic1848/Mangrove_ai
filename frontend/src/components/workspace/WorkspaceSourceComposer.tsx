import { useEffect, useLayoutEffect, useMemo, useRef, useState, type ComponentProps } from "react";
import { TaskComposer, type WebIntakeDraft } from "./TaskComposer";
import { ConnectorSourceIntake } from "./ConnectorSourceIntake";
import { WebSourceIntake } from "./WebSourceIntake";
import { getSourceAcquisition, getTaskContextOptions, previewTaskContext, type TaskContextPreview, type TaskTemplateOption, type OwnerMemoryOption } from "@/lib/semanticWorkspaceApi";
import { ConnectorScopeFacts } from "./ConnectorScopeFacts";
import type { SourceSnapshot } from "@/types/semanticWorkspace";
import type { UploadItem } from "@/types/dataPrep";
import type { ReusableSource } from "@/types/semanticWorkspace";
import { resolveReusableSources } from "@/lib/semanticWorkspaceApi";
import { ReusableSourcePicker, ReusableSourceFacts, reusableSelection, sourceId } from "./ReusableSourcePicker";

type ComposerProps = ComponentProps<typeof TaskComposer>;
export type SourceTaskPayload = Parameters<ComposerProps["onSubmit"]>[0] & {
  sourceSnapshotIds: string[];
  deliveryOutputIds: string[];
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
  initialUnavailableSourceIds?: string[];
  initialReusableSources?: ReusableSource[];
  preserveContext?: boolean;
  onSubmit: (payload: SourceTaskPayload) => Promise<void>;
};
const splitLines = (value: string) => value.split(/[\n,，]/).map(item => item.trim()).filter(Boolean);

export function WorkspaceSourceComposer({ ownerId, draftScope = "new", initialSources = [], initialUnavailableSourceIds = [], initialReusableSources = [], preserveContext = false, onSubmit, ...props }: Props) {
  const storageKey = `mangrove_workspace_draft_${ownerId}_${draftScope}`;
  const filesKey = `${storageKey}_files`;
  const [saved] = useState(() => {
    try { return JSON.parse(localStorage.getItem(storageKey) || "null") as { draft?: WebIntakeDraft; sources?: Choice[]; history?: ReusableSource[]; mustInclude?: string; exclusions?: string; quantity?: string | null; completeness?: string | null; templateId?: string; memoryIds?: number[] } | null; } catch { return null; }
  });
  const [draft, setDraft] = useState<WebIntakeDraft | null>(props.draft ?? saved?.draft ?? null);
  const [sources, setSources] = useState<Choice[]>(() => (saved?.sources ?? [...initialSources.map(snapshot => ({ snapshotId: snapshot.snapshot_id, attemptId: snapshot.attempt_id, snapshot })), ...initialUnavailableSourceIds.map(snapshotId => ({ snapshotId, attemptId: "" }))]).map(source => initialUnavailableSourceIds.includes(source.snapshotId) ? { snapshotId: source.snapshotId, attemptId: "", error: "来源已删除，请明确移除此组后换新资料；不会重新采集。" } : source));
  const [uploads, setUploads] = useState<UploadItem[]>([]);
  const [history, setHistory] = useState<ReusableSource[]>(() => saved?.history ?? initialReusableSources.filter(item => item.kind === "delivery_output"));
  const [restoringHistory, setRestoringHistory] = useState(true);
  const [pickerOpen, setPickerOpen] = useState(false);
  const pickerTrigger = useRef<HTMLElement | null>(null);
  const [webOpen, setWebOpen] = useState(() => { try { return Boolean(localStorage.getItem(`mangrove_web_source_attempt_${ownerId}${draftScope === "new" ? "" : `_${draftScope}`}`)); } catch { return false; } });
  const [connectorOpen, setConnectorOpen] = useState(false);
  const [webPrompt, setWebPrompt] = useState("");
  const [acquiring, setAcquiring] = useState(false);
  const [stale, setStale] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [mustInclude, setMustInclude] = useState(saved?.mustInclude ?? "");
  const [exclusions, setExclusions] = useState(saved?.exclusions ?? "");
  const [quantityDraft, setQuantity] = useState<string | null>(saved?.quantity ?? null);
  const [completenessDraft, setCompleteness] = useState<string | null>(saved?.completeness ?? null);
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
  useEffect(() => { if (stale || props.active === false) { generation.current++; setPickerOpen(false); setConnectorOpen(false); } }, [stale, props.active]);
  useEffect(() => {
    let current = true;
    for (const source of sources.filter(item => !item.snapshot && !item.error)) {
      void getSourceAcquisition(source.attemptId).then(attempt => {
        if (!current) return;
        if (attempt.snapshot?.snapshot_id !== source.snapshotId) throw new Error("来源不可用或身份已改变");
        setSources(previous => previous.map(item => item.snapshotId === source.snapshotId ? { ...item, snapshot: attempt.snapshot! } : item));
      }).catch(reason => { if (current) setSources(previous => previous.map(item => item.snapshotId === source.snapshotId ? { ...item, error: reason instanceof Error ? reason.message : "来源恢复失败" } : item)); });
    }
    return () => { current = false; };
  }, []);
  useEffect(() => {
    if (stale) return;
    try { localStorage.setItem(storageKey, JSON.stringify({ draft, mustInclude, exclusions, quantity: quantityDraft, completeness: completenessDraft, templateId, memoryIds,
      history: history.map(({ source_key, kind, identity, label, sha256, upload_id, output_id, acquired_at, time_kind, media_type, size_bytes, origin }) => ({ source_key, kind, identity, label, sha256, upload_id, output_id, acquired_at, time_kind, media_type, size_bytes, origin, availability: "unknown", limitations: [] })),
      sources: sources.map(({ snapshotId, attemptId }) => ({ snapshotId, attemptId })) })); } catch { /* 不存原文，存储不可用时仅当前会话保留。 */ }
  }, [draft, sources, history, storageKey, stale, mustInclude, exclusions, quantityDraft, completenessDraft, templateId, memoryIds]);
  useEffect(() => {
    const changed = (event: StorageEvent) => { if (event.key === storageKey || event.key === filesKey) { generation.current += 1; setStale(true); } };
    window.addEventListener("storage", changed);
    return () => window.removeEventListener("storage", changed);
  }, [storageKey, filesKey]);
  const hasWeb = sources.length > 0;
  const connectorCount = sources.filter(item => item.snapshot?.source_kind === "connector").length;
  const quantity = quantityDraft ?? (connectorCount ? "当前已成功读取资料中有证据的内容" : "当前已成功读取页面中有证据的内容");
  const completeness = completenessDraft ?? (connectorCount ? "逐来源披露失败、范围和未覆盖内容，不承诺来源完整" : "逐来源披露失败、范围和未覆盖内容，不承诺全网完整");
  useEffect(() => {
    if (!hasWeb || preserveContext) return;
    let current = true;
    void getTaskContextOptions("web_research").then(value => { if (current) setOptions(value); }).catch(reason => { if (current) setError(reason instanceof Error ? reason.message : "上下文选项加载失败"); });
    return () => { current = false; };
  }, [hasWeb]);
  const template = options.templates.find(item => item.template_id === templateId);
  const selection = useMemo(() => ({ template: template ? { template_id: template.template_id, version: template.version } : null, memories: memoryIds.map(memory_id => ({ memory_id })) }), [template, memoryIds]);
  const sourceIdentity = JSON.stringify([sources.map(item => [item.snapshotId, item.snapshot?.artifacts.map(artifact => [artifact.artifact_id, artifact.content_sha256])]), uploads.map(item => [item.upload_id, item.sha256]), history.map(item => [item.source_key, item.sha256, item.availability])]);
  const reviewIdentity = JSON.stringify([sourceIdentity, draft?.prompt, draft?.formats, mustInclude, exclusions, quantity, completeness, selection]);
  const currentReview = useRef(reviewIdentity);
  currentReview.current = reviewIdentity;
  const ready = !restoringHistory && history.every(item => item.availability === "available") && sources.every(item => item.snapshot && item.snapshot.coverage.status !== "hard_insufficient" && !item.error);
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
    <button type="button" disabled={submitting || stale || props.active === false} className="rounded-lg border px-3 py-2 text-sm hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring" onClick={() => setConnectorOpen(true)}>从已有连接读取</button>
    <ConnectorSourceIntake key={`${ownerId}:${draftScope}`} ownerId={ownerId} storageScope={draftScope} open={connectorOpen && !stale && props.active !== false} onOpenChange={setConnectorOpen} purpose={draft?.prompt || ""} onSelected={snapshot => { if (stale || props.active === false) return false; generation.current++; setSources(current => current.some(item => item.snapshotId === snapshot.snapshot_id) ? current : [...current, { snapshotId: snapshot.snapshot_id, attemptId: snapshot.attempt_id, snapshot }]); const next = draft ?? { prompt: "", connectionId: null, connectionModel: null, localModel: props.defaultModel?.model ?? null }; updateDraft({ ...next, formats: next.formats?.length ? next.formats : ["markdown"] }); return true; }} />
    <TaskComposer {...props} draft={draft} onDraftChange={updateDraft} active={props.active !== false && !stale} uploadStorageKey={filesKey}
      webSourceCount={sources.length - connectorCount} connectorSourceCount={connectorCount} additionalSourceCount={history.length} additionalInputFormats={history.map(item => { const format = item.label.split(".").pop()?.toLowerCase() || ""; return format === "md" ? "markdown" : format; })}
      sourceBusy={acquiring || submitting || restoringHistory} submitBlocked={stale || !ready || !contextReady || webOpen || connectorOpen || pickerOpen}
      onPickSources={() => { pickerTrigger.current = document.activeElement as HTMLElement; setPickerOpen(true); }}
      sourceIdentity={sourceIdentity}
      onUploadsChange={value => { setUploads(value); props.onUploadsChange?.(value); }}
      onReadWeb={value => { updateDraft(value); setWebPrompt(value.prompt); setWebOpen(true); }}
      onSubmit={async payload => {
        if (stale || !ready || !contextReady || webOpen || connectorOpen || pickerOpen) return;
        const requestGeneration = generation.current;
        const storedDraft = localStorage.getItem(storageKey);
        const storedFiles = localStorage.getItem(filesKey);
        setSubmitting(true);
        try {
          if (history.length) {
            const resolved = await resolveReusableSources(reusableSelection(history));
            if (requestGeneration !== generation.current) return;
            if (history.some(item => !resolved.items.some(value => value.source_key === item.source_key && value.sha256 === item.sha256 && value.availability === "available"))) {
              setHistory(current => current.map(item => resolved.items.find(value => value.source_key === item.source_key && value.sha256 === item.sha256) ?? { ...item, availability: "unavailable" }));
              throw new Error("历史资料已不可用，请核对当前选择");
            }
          }
          const allUploads = [...new Map([...payload.uploads, ...history.filter(item => item.kind === "upload").map(item => ({ upload_id: sourceId(item), original_name: item.label, media_type: item.media_type || "", size_bytes: item.size_bytes ?? 0, sha256: item.sha256 || "" }))].map(item => [item.upload_id, item])).values()];
          await onSubmit({ ...payload, uploads: allUploads, deliveryOutputIds: history.filter(item => item.kind === "delivery_output").map(sourceId), sourceSnapshotIds: sources.map(item => item.snapshotId), ...(hasWeb && !preserveContext && preview ? { sourceGoal: {
            must_include: splitLines(mustInclude), explicit_exclusions: splitLines(exclusions), quantity_requirement: quantity.trim(), completeness_requirement: completeness.trim(),
            context_purpose: "web_research", context_selection: selection, context_preview_sha256: preview.value.preview_sha256,
          } } : {}) });
          try { if (requestGeneration === generation.current && localStorage.getItem(storageKey) === storedDraft && localStorage.getItem(filesKey) === storedFiles) { localStorage.removeItem(storageKey); localStorage.removeItem(filesKey); } } catch { /* 当前草稿已完成。 */ }
        } finally { setSubmitting(false); }
      }}>
      {history.length > 0 && <section aria-label="已选历史资料" className="mt-3 space-y-3 border-t pt-3">{history.map(item => <div key={item.source_key} className="flex items-start gap-3 border-b pb-3"><div className="min-w-0 flex-1"><p className="break-all text-sm font-medium">{item.label}</p><ReusableSourceFacts item={item} /></div><button type="button" aria-label={`移除历史资料 ${item.label}`} disabled={submitting} className="shrink-0 rounded border px-2 py-1 text-xs hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring" onClick={() => { generation.current++; setHistory(current => current.filter(value => value.source_key !== item.source_key)); }}>从本次移除</button></div>)}<p className="text-xs text-muted-foreground">只移除本次引用，不删除历史资料。</p></section>}
      {hasWeb && <section className="mt-3 space-y-3 border-t pt-3" aria-label={connectorCount ? connectorCount === sources.length ? "已选连接资料" : "已选网页与连接资料" : "已选网页资料"}>
        {sources.map(source => <div key={source.snapshotId} className="rounded-xl border p-3 text-xs">
          <div className="flex items-start justify-between gap-3"><div className="min-w-0 break-words">
            <p className="font-medium">{source.snapshot?.allowed_scope.query || source.snapshot?.artifacts[0]?.title || source.snapshotId}</p>
            <p className="mt-1 text-muted-foreground">{source.snapshot ? source.snapshot.source_kind === "connector" ? `连接资料 · ${source.snapshot.artifacts.length} 个原件` : `${source.snapshot.valid_page_count} 页正文 · ${source.snapshot.failed_page_count} 页失败 · ${new Date(source.snapshot.created_at).toLocaleString("zh-CN")}` : source.error || "正在恢复来源…"}</p>
          </div><button type="button" disabled={submitting} aria-label={`移除${source.snapshot?.source_kind === "connector" ? "连接资料" : "网页组"} ${source.snapshot?.allowed_scope.query || source.snapshot?.artifacts[0]?.title || source.snapshotId}`} className="shrink-0 rounded border px-2 py-1 hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={() => { generation.current += 1; setSources(current => current.filter(item => item.snapshotId !== source.snapshotId)); }}>移除此组</button></div>
          {source.snapshot && <p className="mt-2 text-muted-foreground">{source.snapshot.coverage.status === "scope_complete" ? "已覆盖授权范围" : source.snapshot.coverage.status === "hard_insufficient" ? "硬性目标未满足" : "覆盖范围仍有未知，不代表完整"}{source.snapshot.coverage.search_report && ` · 发现 ${source.snapshot.coverage.search_report.discovered_count} 条链接 · 尚未读到正文 ${source.snapshot.coverage.search_report.candidates.filter(item => item.status !== "read").length} 条`}</p>}
          {source.snapshot?.allowed_scope.kind === "public_search" && <p className="mt-1 text-muted-foreground">时间：{({ any: "不限", day: "最近一天", week: "最近一周", month: "最近一月", year: "最近一年" })[source.snapshot.allowed_scope.time_range ?? "any"]} · 域名：{source.snapshot.allowed_scope.domains?.join("、") || "公开网页不限域名"}</p>}
          {source.snapshot?.coverage.status === "hard_insufficient" && <p role="alert" className="mt-2 text-destructive">此组有效页面不足，当前仅供查看；其他资料不能抵消该组硬性缺口。</p>}
          {source.snapshot && <details className="mt-2"><summary className="cursor-pointer">{source.snapshot.allowed_scope.kind === "connector" ? "查看已读原件与范围" : "查看已读页面与范围"}</summary>{source.snapshot.allowed_scope.kind === "connector" ? <ConnectorScopeFacts scope={source.snapshot.allowed_scope} /> : <p className="mt-2 break-all">{source.snapshot.allowed_scope.normalized_url || source.snapshot.allowed_scope.query} · {source.snapshot.allowed_scope.kind === "public_search" ? "公开搜索" : source.snapshot.allowed_scope.kind === "same_site" ? "同站有限范围" : "精确页面"}；摘要可能截断，完整原文在任务资料包。</p>}
            {source.snapshot.coverage.search_report?.candidates.filter(item => item.status !== "read").map((item, index) => <p key={`${item.url}-${index}`} className="mt-2 break-all text-muted-foreground">{item.status === "discovered" ? "仅发现链接，尚未读取" : item.status === "scope_denied" ? "超出范围，未读取" : "读取失败"}：{item.title || item.url} · {item.url}{item.message ? ` · ${item.message}` : ""}</p>)}
            {source.snapshot.failures.map(item => <p key={item.failure_id} className="mt-2 break-all text-muted-foreground">未读取：{item.request_url} · {item.error_message}</p>)}
            {source.snapshot.artifacts.map(item => <article key={item.artifact_id} className="mt-2 border-t pt-2"><p className="font-medium">{item.title || item.final_url}</p><p className="break-all text-muted-foreground">{item.final_url}</p><p className="mt-1 max-h-32 overflow-auto whitespace-pre-wrap">{item.text_preview}</p></article>)}</details>}
        </div>)}
        <p className="text-xs text-muted-foreground">确认时使用上方文件与全部已选资料组。逐来源保留缺口，排序不代表冲突优先级。</p>
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
    {pickerOpen && <ReusableSourcePicker selectedKeys={[...uploads.map(item => `upload:${item.upload_id}`), ...sources.map(item => `snapshot:${item.snapshotId}`), ...history.map(item => item.source_key)]}
      onClose={added => { generation.current++; setPickerOpen(false); requestAnimationFrame(() => { if (added) container.current?.querySelector<HTMLTextAreaElement>('textarea[aria-label="任务要求"]')?.focus(); else pickerTrigger.current?.focus(); }); }}
      onAdd={async items => {
        const requestGeneration = generation.current;
        const snapshots = await Promise.all(items.filter(item => item.kind === "snapshot").map(async item => {
          if (!item.attempt_id) throw new Error("来源快照缺少恢复身份");
          const attempt = await getSourceAcquisition(item.attempt_id);
          if (!attempt.snapshot || attempt.snapshot.snapshot_id !== item.source_snapshot_id) throw new Error("来源快照身份已变化");
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
