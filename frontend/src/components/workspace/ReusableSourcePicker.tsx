import { useEffect, useRef, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ApiError } from "@/lib/api";
import { getSourceAcquisition, getSourceReferences, listReusableSources, previewReusableOutput, resolveReusableSources } from "@/lib/semanticWorkspaceApi";
import type { ReusableSource, ReusableSourcePage, ReusableOutputPreview, SourceReferencePage, SourceSelection, SourceSnapshot } from "@/types/semanticWorkspace";
import { ReusableResultPreview } from "./ResultPreview";
import { SourcePreviewPanel } from "./SourcePreviewPanel";

export const sourceId = (item: ReusableSource) => item.upload_id || item.source_snapshot_id || item.output_id || "";
export function reusableSelection(items: ReusableSource[]): SourceSelection {
  return { upload_ids: items.filter(item => item.kind === "upload").map(sourceId), source_snapshot_ids: items.filter(item => item.kind === "snapshot").map(sourceId), delivery_output_ids: items.filter(item => item.kind === "delivery_output").map(sourceId) };
}
export function ReusableSourceFacts({ item }: { item: ReusableSource }) {
  const time = item.acquired_at && !Number.isNaN(Date.parse(item.acquired_at)) ? new Date(item.acquired_at).toLocaleString() : null;
  return <div className="space-y-1 break-words text-xs text-muted-foreground">
    <p>{item.identity === "derived" ? "正式处理结果 · 非原件" : "原始资料"}</p>
    <p>{item.kind === "upload" ? "曾用于" : "来源"}：{item.origin?.task_id || "来源任务未记录"} · {item.origin?.revision ? `V${item.origin.revision}` : "版本未记录"}</p>
    <p>{item.time_kind === "generated" ? "生成时间" : "取得时间"}：{time && item.time_kind !== "unknown" ? time : "时间未记录"}</p>
    {item.allowed_scope && <p>范围：{item.allowed_scope.query || item.allowed_scope.normalized_url} · 时间：{({ any: "不限", day: "最近一天", week: "最近一周", month: "最近一月", year: "最近一年" })[item.allowed_scope.time_range ?? "any"]} · 域名：{item.allowed_scope.domains?.join("、") || "按原授权范围"}</p>}
    {item.coverage && <p>{item.coverage.status === "hard_insufficient" ? "硬性目标未满足，不能由其他资料抵消" : item.coverage.status === "scope_complete" ? "已覆盖授权范围" : "覆盖仍有未知，不代表完整"}{item.coverage.search_report && ` · 正文 ${item.coverage.search_report.read_count} · 失败 ${item.coverage.search_report.failed_count} · 尚未读到正文 ${item.coverage.search_report.candidates.filter(value => value.status !== "read").length}`}</p>}
    {item.availability !== "available" && <p role="alert">资料不可用，请移除或重新核对；不会从缓存恢复。{item.reason_code ? `（${item.reason_code}）` : ""}</p>}
    {item.limitations?.map((value, index) => <p key={index}>{value}</p>)}
  </div>;
}
const button = "rounded-lg border px-3 py-2 text-xs hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50";

export function ReusableSourcePicker({ selectedKeys, onAdd, onClose }: { selectedKeys: string[]; onAdd: (items: ReusableSource[]) => Promise<void>; onClose: (added: boolean) => void }) {
  const [catalog, setCatalog] = useState<ReusableSourcePage | null>(null);
  const [selected, setSelected] = useState<ReusableSource[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [view, setView] = useState<ReusableSource | null>(null);
  const [output, setOutput] = useState<ReusableOutputPreview | null>(null);
  const [snapshot, setSnapshot] = useState<SourceSnapshot | null>(null);
  const [references, setReferences] = useState<SourceReferencePage | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const request = useRef(0);
  const alive = useRef(true);
  const previewButtons = useRef(new Map<string, HTMLButtonElement>());
  useEffect(() => { alive.current = true; void load(); return () => { alive.current = false; request.current++; }; }, []);
  const close = (added = false) => { alive.current = false; request.current++; onClose(added); };
  async function load(more = false) {
    const generation = ++request.current;
    setBusy(true); setError("");
    try {
      const next = await listReusableSources(more ? catalog?.next_cursor : null, more ? catalog?.snapshot_token : undefined);
      if (!alive.current || request.current !== generation) return;
      setCatalog(previous => ({ ...next, items: more && previous ? [...new Map([...previous.items, ...next.items].map(item => [item.source_key, item])).values()] : next.items }));
    } catch (reason) { if (alive.current && request.current === generation) { if (reason instanceof ApiError && reason.status === 409) setCatalog(null); setError(reason instanceof Error ? reason.message : "历史资料读取失败，请重试"); } }
    finally { if (alive.current && request.current === generation) setBusy(false); }
  }
  const back = () => {
    // 添加已进入父草稿核验时不能切换请求代数；取消整个面板仍可中止本次选择。
    if (busy) return;
    const key = view?.source_key;
    request.current++; setView(null); setOutput(null); setSnapshot(null); setReferences(null); setLoadingPreview(false); setError("");
    requestAnimationFrame(() => key && previewButtons.current.get(key)?.focus());
  };
  async function show(item: ReusableSource, offset = 0) {
    const generation = ++request.current;
    setView(item); setOutput(null); setSnapshot(null); setReferences(null); setLoadingPreview(true); setError("");
    try {
      const response = await resolveReusableSources(reusableSelection([item]));
      const checked = response.items.find(value => value.source_key === item.source_key);
      if (!checked || checked.availability !== "available" || checked.sha256 !== item.sha256) throw new Error("所选资料不可用或身份已改变，请重新选择");
      if (!alive.current || request.current !== generation) return;
      setView(checked);
      if (checked.kind === "delivery_output") {
        const next = await previewReusableOutput(sourceId(checked), offset);
        if (next.source_key !== checked.source_key || next.identity !== "derived" || next.output_id !== checked.output_id || next.sha256 !== checked.sha256
          || next.delivery_id !== checked.origin.delivery_id || next.run_id !== checked.origin.run_id || next.representation?.kind !== "output"
          || next.representation.associated_output_id !== checked.output_id || next.representation.sha256 !== checked.sha256) throw new Error("正式输出预览身份已变化，请重新选择");
        if (alive.current && request.current === generation) setOutput(next);
      } else if (checked.kind === "snapshot") {
        if (!checked.attempt_id) throw new Error("网页缺少可恢复身份");
        const next = await getSourceAcquisition(checked.attempt_id);
        if (next.snapshot?.snapshot_id !== checked.source_snapshot_id) throw new Error("网页快照身份已变化");
        if (alive.current && request.current === generation) setSnapshot(next.snapshot);
      }
    } catch (reason) { if (alive.current && request.current === generation) setError(reason instanceof Error ? reason.message : "资料预览失败"); }
    finally { if (alive.current && request.current === generation) setLoadingPreview(false); }
  }
  async function readReferences(more = false) {
    if (busy || !view) return;
    const generation = ++request.current;
    setLoadingPreview(true); setError("");
    try {
      const next = await getSourceReferences(view.kind, sourceId(view), more ? references?.next_cursor : null, more ? references?.snapshot_token : undefined);
      if (alive.current && request.current === generation) setReferences(previous => ({ ...next, items: more && previous ? [...previous.items, ...next.items] : next.items }));
    } catch (reason) { if (alive.current && request.current === generation) { setReferences(null); setError(reason instanceof Error ? reason.message : "引用清单读取失败"); } }
    finally { if (alive.current && request.current === generation) setLoadingPreview(false); }
  }
  async function add() {
    if (busy || loadingPreview || !selected.length) return;
    const generation = ++request.current;
    setBusy(true); setError("");
    try {
      const next = await resolveReusableSources(reusableSelection(selected));
      if (next.items.length !== selected.length || selected.some(item => !next.items.some(value => value.source_key === item.source_key && value.sha256 === item.sha256 && value.availability === "available"))) throw new Error("所选资料已不可用或身份改变；请取消该项后重试，其他选择仍保留");
      if (!alive.current || request.current !== generation) return;
      await onAdd(next.items);
      if (alive.current && request.current === generation) close(true);
    } catch (reason) { if (alive.current && request.current === generation) setError(reason instanceof Error ? reason.message : "添加未完成，当前资料仍保留"); }
    finally { if (alive.current && request.current === generation) setBusy(false); }
  }
  return <Dialog.Root open onOpenChange={open => { if (!open) close(); }}><Dialog.Portal>
    <Dialog.Overlay className="fixed inset-0 z-50 bg-black/40" />
    <Dialog.Content className="fixed left-1/2 top-1/2 z-50 flex max-h-[90dvh] w-[calc(100%_-_1.5rem)] max-w-4xl -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-xl border bg-background shadow-xl" onCloseAutoFocus={event => event.preventDefault()}
      onEscapeKeyDown={event => { if (view) { event.preventDefault(); back(); } }}>
      <div className="shrink-0 border-b p-4"><Dialog.Title className="font-semibold">从历史资料添加</Dialog.Title><Dialog.Description className="mt-1 text-xs text-muted-foreground">使用已保存内容，不会重新上传或联网读取。</Dialog.Description></div>
      <div className="min-h-0 overflow-auto p-4">
        {view ? <section aria-label="历史资料预览" className="min-w-0 space-y-3">
          <button type="button" className={button} disabled={busy} onClick={back}>返回资料列表</button><h3 className="break-all font-medium">{view.label}</h3><ReusableSourceFacts item={view} />
          {loadingPreview && <p role="status">正在读取资料…</p>}
          {!error && !loadingPreview && output && <><ReusableResultPreview key={`${view.source_key}:${output.offset}`} preview={output} /><p className="text-xs text-muted-foreground">仅显示当前窗口 · 共 {output.total} 条</p><div className="flex gap-2"><button type="button" className={button} disabled={busy || output.offset === 0} onClick={() => void show(view, Math.max(0, output.offset - output.limit))}>上一页</button><button type="button" className={button} disabled={busy || output.offset + output.limit >= output.total} onClick={() => void show(view, output.offset + output.limit)}>下一页</button></div></>}
          {!error && !loadingPreview && view.kind === "upload" && <div className="h-[50dvh] min-h-48"><SourcePreviewPanel uploads={[{ upload_id: sourceId(view), original_name: view.label, media_type: view.media_type || "", size_bytes: view.size_bytes ?? 0, sha256: view.sha256 || "" }]} selectedUploadId={sourceId(view)} evidence={null} onSelectUpload={() => undefined} onClose={back} /></div>}
          {!error && snapshot && <><p className="text-xs text-muted-foreground">网页摘要预览，可能截断；完整内容沿任务资料包保留。</p>{snapshot.artifacts.map(item => <article key={item.artifact_id} className="border-t py-3"><p className="break-all">{item.title || item.final_url}</p><p className="break-all text-xs text-muted-foreground">{item.final_url}</p><p className="mt-2 whitespace-pre-wrap text-sm">{item.text_preview}</p></article>)}</>}
          <button type="button" className={button} disabled={busy || loadingPreview} onClick={() => void readReferences()}>查看出处与引用</button>
          {references && <section aria-label="资料引用清单" className="space-y-2 text-xs"><p>{references.page_complete && references.total === 0 && references.unknown_uses === 0 ? "0 个引用" : `已显示 ${references.items.length} / ${references.total} 条引用 · 未知使用 ${references.unknown_uses}`}</p>{references.items.map((item, index) => <p key={`${item.task_id}-${item.revision}-${item.use_id}-${index}`}>{item.task_id || "独立读取"} · {item.revision ? `V${item.revision}` : "版本未知"} · {({ revision: "保留版本", runtime: "任务运行", export: "导出", preview: "预览", delivery: "正式交付记录" })[item.reference_kind]} · {({ retained: "已保留", active: "使用中", unknown: "结果未知", published: "已发布" })[item.state]}{item.delivery_id ? ` · 交付：${item.delivery_id}` : ""}{item.task_exists === false ? " · 原任务记录已清理" : item.in_recycle_bin ? " · 回收站中" : ""}</p>)}{references.next_cursor && <button type="button" className={button} disabled={busy || loadingPreview} onClick={() => void readReferences(true)}>加载更多引用</button>}</section>}
        </section> : <div className="space-y-3">
          {busy && <p role="status">正在核对历史资料…</p>}
          {catalog?.items.map(item => <div key={item.source_key} className="flex items-start gap-3 border-b pb-3">
            <input type="checkbox" aria-label={`选择 ${item.label}`} className="mt-1 h-4 w-4 shrink-0 accent-primary" checked={selectedKeys.includes(item.source_key) || selected.some(value => value.source_key === item.source_key)} disabled={busy || selectedKeys.includes(item.source_key) || item.availability !== "available"} onChange={event => setSelected(current => event.target.checked ? [...current, item] : current.filter(value => value.source_key !== item.source_key))} />
            <div className="min-w-0 flex-1"><p className="break-all font-medium">{item.label}{selectedKeys.includes(item.source_key) ? " · 已添加" : ""}</p><ReusableSourceFacts item={item} /></div>
            <button type="button" ref={node => { if (node) previewButtons.current.set(item.source_key, node); else previewButtons.current.delete(item.source_key); }} aria-label={`预览 ${item.label}`} className={button} disabled={busy || item.availability !== "available"} onClick={() => void show(item)}>预览</button>
          </div>)}
          {catalog && !catalog.total && <p>暂无已保存的历史资料；可以先添加文件或公开网页。</p>}
          {catalog?.next_cursor && <button type="button" disabled={busy} className={button} onClick={() => void load(true)}>加载更多</button>}
          {!catalog && !busy && <button type="button" className={button} onClick={() => void load()}>重新读取历史资料</button>}
        </div>}
        {error && <p role="alert" className="mt-3 break-words text-sm text-destructive">{error}</p>}
      </div>
      <div className="flex shrink-0 justify-end gap-2 border-t p-4"><button type="button" className={button} onClick={() => close()}>取消添加</button><button type="button" className={`${button} bg-primary text-primary-foreground hover:bg-primary/90`} disabled={busy || loadingPreview || !selected.length} onClick={() => void add()}>添加 {selected.length} 份资料</button></div>
    </Dialog.Content>
  </Dialog.Portal></Dialog.Root>;
}
