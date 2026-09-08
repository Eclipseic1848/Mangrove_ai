import {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import { useQuery } from "@tanstack/react-query";
import { Document, Page, pdfjs } from "react-pdf";
import {
  ChevronLeft,
  ChevronRight,
  FileText,
  Loader2,
  LocateFixed,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";
import {
  getDocumentPreview,
  getUploadFile,
  previewTask,
} from "@/lib/dataPrepApi";
import type { UploadItem } from "@/types/dataPrep";
import type { WorkspaceTask } from "@/types/semanticWorkspace";
import { downloadWorkspaceSourceBundle, getWorkspaceSourcePreview } from "@/lib/semanticWorkspaceApi";
import { downloadFile } from "@/lib/api";
import { toast } from "sonner";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "webp"]);

const TABLE_EXTENSIONS = new Set([
  "csv",
  "tsv",
  "xlsx",
  "parquet",
  "json",
  "jsonl",
]);

function extension(name: string) {
  return name.split(".").pop()?.toLowerCase() ?? "";
}

export type SourceViewState = {
  page: number; zoom: number; scrollTop: number; scrollLeft: number;
  offset?: number; tableRef?: string; searchInput?: string; search?: string; sortBy?: string; sortDirection?: "asc" | "desc";
  evidenceKey?: string; locationStatus?: string;
  parserVersion?: string | null;
};
export const initialSourceView: SourceViewState = { page: 1, zoom: 0.95, scrollTop: 0, scrollLeft: 0 };

export function SourcePreviewPanel({
  uploads,
  selectedUploadId,
  evidence,
  onSelectUpload,
  onClose,
  expanded = false,
  onToggleExpand,
  viewState,
  onViewStateChange,
  task,
}: {
  uploads: UploadItem[];
  selectedUploadId: string | null;
  evidence: Record<string, unknown> | null;
  onSelectUpload: (uploadId: string) => void;
  onClose: () => void;
  expanded?: boolean;
  onToggleExpand?: () => void;
  viewState?: SourceViewState;
  onViewStateChange?: (patch: Partial<SourceViewState>) => void;
  task?: WorkspaceTask;
}) {
  const selectedUpload =
    uploads.find((upload) => upload.upload_id === selectedUploadId)
    ?? (!selectedUploadId ? uploads[0] : null)
    ?? null;
  const [localView, setLocalView] = useState(initialSourceView);
  const [tableFormat, setTableFormat] = useState<"none" | "csv" | "xlsx">("none");
  const [downloadBusy, setDownloadBusy] = useState(false);
  const [downloadError, setDownloadError] = useState("");
  const [downloadStatus, setDownloadStatus] = useState("");
  const downloadRequest = useRef<AbortController | null>(null);
  useEffect(() => () => { downloadRequest.current?.abort(); }, [task?.task_id, task?.viewing_revision]);
  const downloadSources = async () => {
    if (!task || downloadRequest.current) return;
    const request = new AbortController();
    downloadRequest.current = request;
    setDownloadBusy(true); setDownloadError(""); setDownloadStatus("正在准备下载…");
    try {
      const revision = task.viewing_revision ?? task.current_revision ?? task.active_revision;
      await downloadWorkspaceSourceBundle(task.task_id, `${task.title}-V${revision}-完整资料包.zip`, revision, tableFormat, request.signal);
      if (!request.signal.aborted) setDownloadStatus("已交给浏览器下载；资料缺口请查看包内清单。");
    } catch (error) {
      if (!request.signal.aborted) { setDownloadError(error instanceof Error ? error.message : "资料包下载失败，请重试。"); setDownloadStatus(""); }
    } finally {
      if (downloadRequest.current === request) { downloadRequest.current = null; setDownloadBusy(false); }
    }
  };
  const currentView = viewState ?? localView;
  const { page, zoom } = currentView;
  const updateView = (patch: Partial<SourceViewState>) => {
    if (onViewStateChange) onViewStateChange(patch);
    else setLocalView(current => ({ ...current, ...patch }));
  };
  const setPage = (value: number | ((page: number) => number)) => updateView({ page: typeof value === "function" ? value(page) : value });
  const [pageCount, setPageCount] = useState(0);
  const [pageSize, setPageSize] = useState({ width: 612, height: 792 });
  const [loadedPage, setLoadedPage] = useState(0);
  const [pdfError, setPdfError] = useState<string | null>(null);
  const [imageError, setImageError] = useState<string | null>(null);
  const [imageSize, setImageSize] = useState<{ width: number; height: number } | null>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const evidenceKey = evidence ? JSON.stringify(evidence) : "";
  const locate = Boolean(evidence && currentView.evidenceKey !== evidenceKey);
  const sourceParams = {
    revision: task?.viewing_revision ?? 1, offset: currentView.offset ?? 0, limit: 100,
    table_ref: locate && typeof evidence?.table_ref === "string" ? evidence.table_ref : currentView.tableRef,
    search: locate ? "" : currentView.search, sort_by: locate ? undefined : currentView.sortBy,
    sort_direction: currentView.sortDirection,
    ...(locate ? { row_number: typeof evidence?.row_number === "number" ? evidence.row_number : undefined,
      page: typeof evidence?.page === "number" ? evidence.page : undefined,
      element_id: typeof evidence?.element_id === "string" ? evidence.element_id : undefined,
      extractor_version: typeof evidence?.extractor_version === "string" ? evidence.extractor_version : undefined } : {}),
  };
  const source = useQuery({
    queryKey: ["workspace-task-source", task?.task_id, task?.viewing_revision, selectedUploadId, evidence?.source_sha256, sourceParams],
    // 同一来源定位完成后切换到所在窗口时，保留内容，避免卸载造成滚动归零。
    placeholderData: (previous, query) => query && query.queryKey[1] === task?.task_id
      && query.queryKey[2] === task?.viewing_revision && query.queryKey[3] === selectedUploadId
      && query.queryKey[4] === evidence?.source_sha256 ? previous : undefined,
    queryFn: async () => {
      const data = await getWorkspaceSourcePreview(task!.task_id, selectedUploadId!, sourceParams);
      if (data.task_id !== task!.task_id || data.revision !== task!.viewing_revision || data.artifact_id !== selectedUploadId
        || (typeof evidence?.source_sha256 === "string" && evidence.source_sha256 !== data.sha256)
        || (selectedUpload && data.sha256 !== selectedUpload.sha256)) throw new Error("来源身份或版本不匹配，请重新选择来源");
      if (data.content_url && (!data.upload_id || data.content_url !== `/api/data-sources/uploads/${data.upload_id}/content`)) throw new Error("来源原件地址不可用");
      return data;
    },
    enabled: Boolean(task && selectedUploadId), retry: false,
  });
  const selected = selectedUpload ?? (source.data ? { upload_id: source.data.upload_id ?? source.data.artifact_id,
    original_name: source.data.original_name, media_type: source.data.media_type, sha256: source.data.sha256, size_bytes: 0 } : null);
  const isImage = Boolean(selected && IMAGE_EXTENSIONS.has(extension(selected.original_name)));
  const parserVersion = source.data?.representation.parser_or_inspector_version;
  const parserChanged = Boolean(source.data && !source.isPlaceholderData && currentView.parserVersion != null && currentView.parserVersion !== parserVersion);
  const evidenceLocated = !locate && !parserChanged && currentView.locationStatus === "已定位来源";
  const file = useQuery({
    queryKey: ["workspace-source-file", selected?.upload_id, task?.task_id, task?.viewing_revision, source.data?.sha256],
    queryFn: () => getUploadFile(selected!),
    enabled: Boolean(
      selected
      && (!task || Boolean(source.data?.content_url))
      && (extension(selected.original_name) === "pdf" || isImage),
    ),
  });
  const [fileUrl, setFileUrl] = useState<string | null>(null);
  useEffect(() => {
    if (!file.data) { setFileUrl(null); return; }
    // URL 与 effect 生命周期一致，避免开发模式重挂载复用已撤销的原件地址。
    const url = URL.createObjectURL(file.data);
    setFileUrl(url);
    setImageError(null);
    setImageSize(null);
    return () => URL.revokeObjectURL(url);
  }, [file.data]);

  const table = Boolean(
    selected && TABLE_EXTENSIONS.has(extension(selected.original_name)),
  );
  const tablePreview = useQuery({
    queryKey: ["workspace-source-table", selected?.upload_id],
    queryFn: () =>
      previewTask(
        {
          source_type: "upload_file",
          upload_id: selected!.upload_id,
        },
        30,
      ),
    enabled: Boolean(!task && selected && table),
  });
  const documentPreview = useQuery({
    queryKey: ["workspace-source-document", selected?.upload_id],
    queryFn: () => getDocumentPreview(selected!.upload_id),
    enabled: Boolean(
      selected
      && !task
      && !table
      && !isImage
      && extension(selected.original_name) !== "pdf",
    ),
    retry: false,
  });

  useLayoutEffect(() => {
    // PDF 的真实页在异步解析后才有高度，提前恢复会被浏览器裁成零。
    if (selected && extension(selected.original_name) === "pdf" && loadedPage !== page) return;
    if (isImage && !imageSize) return;
    if (contentRef.current) {
      contentRef.current.scrollTop = currentView.scrollTop;
      contentRef.current.scrollLeft = currentView.scrollLeft;
    }
  }, [file.data, tablePreview.data, documentPreview.data, source.data, loadedPage, imageSize]);

  useLayoutEffect(() => {
    if (!source.data || source.isPlaceholderData || currentView.parserVersion === parserVersion) return;
    if (parserChanged) {
      // 同一原件的新解析表示不继承旧元素位置，首次取得版本只记录身份。
      if (contentRef.current) { contentRef.current.scrollTop = 0; contentRef.current.scrollLeft = 0; }
      updateView({ ...initialSourceView, offset: 0, tableRef: undefined, searchInput: "", search: "", sortBy: undefined,
        parserVersion, evidenceKey, locationStatus: "解析版本已变化，无法定位" });
    } else updateView({ parserVersion });
  }, [source.data, source.isPlaceholderData]);

  useLayoutEffect(() => {
    if (!locate || parserChanged || source.isPlaceholderData || (task && !source.data) || (!task && !documentPreview.data && !file.data && !tablePreview.data)) return;
    const data = source.data;
    // 本切片只验证图片原件，尚未建立识别坐标到原件的可核验映射。
    if (isImage) { updateView({ evidenceKey, locationStatus: "尚无可核验的图片定位，当前仅浏览原件" }); return; }
    const failure = data?.location_status === "version_mismatch" ? "解析版本已变化，无法定位" : data?.location_status === "not_found" ? "未找到对应位置" : null;
    if (failure) { updateView({ evidenceKey, locationStatus: failure }); return; }
    const evidencePage = Number(evidence?.page || 0);
    if (selected && extension(selected.original_name) === "pdf") {
      if (!pageCount) return;
      if (evidencePage < 1 || evidencePage > pageCount) { updateView({ evidenceKey, locationStatus: "未找到对应页" }); return; }
      if (page !== evidencePage) { updateView({ page: evidencePage }); return; }
      if (loadedPage !== page) return;
      updateView({ evidenceKey, locationStatus: "已定位来源" }); return;
    }
    const selector = evidence?.element_id ? `[data-element-id="${CSS.escape(String(evidence.element_id))}"]`
      : evidence?.row_number ? `[data-source-row="${Number(evidence.row_number)}"]` : null;
    const target = selector ? contentRef.current?.querySelector<HTMLElement>(selector) : null;
    if (!task && !documentPreview.data && !tablePreview.data) return;
    if (target && contentRef.current) {
      contentRef.current.scrollTop += target.getBoundingClientRect().top - contentRef.current.getBoundingClientRect().top - contentRef.current.clientHeight / 3;
      updateView({ evidenceKey, locationStatus: "已定位来源", scrollTop: contentRef.current.scrollTop,
        ...(data ? { offset: data.offset ?? 0, tableRef: data.selected_table_ref, search: "", searchInput: "", sortBy: undefined } : {}) });
    } else updateView({ evidenceKey, locationStatus: "缺少可核验位置，当前仅浏览来源" });
  }, [locate, evidenceKey, parserChanged, source.data, source.isPlaceholderData, documentPreview.data, tablePreview.data, pageCount, loadedPage, page]);

  if (!selected && !task) {
    return (
      <div className="flex h-full flex-col items-center justify-center p-6 text-center text-sm text-muted-foreground">
        <FileText className="mb-3 h-7 w-7 opacity-40" />
        当前任务没有可预览文件
      </div>
    );
  }

  const ext = extension(selected?.original_name ?? "");
  const sourceColumns = source.data?.columns ?? tablePreview.data?.schema.fields.map((item) => item.name) ?? [];
  const elements = task ? source.data?.elements : documentPreview.data?.elements;
  const sourceRows = task ? source.data?.rows : tablePreview.data?.sample.map((values, index) => ({ row_number: index + (["csv", "tsv", "xlsx"].includes(ext) ? 2 : 1), values }));
  const readError = source.error ?? file.error ?? tablePreview.error ?? documentPreview.error;
  const highlightedRow = Number(evidence?.row_number || 0);
  const evidenceValues =
    evidence?.values && typeof evidence.values === "object"
      ? Object.entries(evidence.values as Record<string, unknown>)
      : [];
  const bbox =
    evidence?.bbox && typeof evidence.bbox === "object"
      ? evidence.bbox as Record<string, unknown>
      : null;
  const evidenceBoxStyle: CSSProperties | null = (() => {
    if (!bbox) return null;
    const x0 = Number(bbox.x0);
    const y0 = Number(bbox.y0);
    const x1 = Number(bbox.x1);
    const y1 = Number(bbox.y1);
    if (![x0, y0, x1, y1].every(Number.isFinite)) return null;
    const width = pageSize.width * zoom;
    const height = pageSize.height * zoom;
    if (bbox.coordinate_space === "normalized_1000") {
      return {
        left: x0 / 1000 * width,
        top: y0 / 1000 * height,
        width: (x1 - x0) / 1000 * width,
        height: (y1 - y0) / 1000 * height,
      };
    }
    if (bbox.coordinate_space === "pdf_points") {
      return {
        left: x0 / pageSize.width * width,
        top: y0 / pageSize.height * height,
        width: (x1 - x0) / pageSize.width * width,
        height: (y1 - y0) / pageSize.height * height,
      };
    }
    return null;
  })();
  return (
    <div className="flex h-full min-h-0 flex-col bg-muted/20">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b bg-background px-3">
        <select
          aria-label="预览文件"
          value={selectedUploadId ?? selected?.upload_id ?? ""}
          onChange={(event) => onSelectUpload(event.target.value)}
          className="min-w-0 flex-1 truncate rounded-lg border bg-background px-2 py-1.5 text-xs"
        >
          {uploads.map((upload) => (
            <option key={upload.upload_id} value={upload.upload_id}>
              {upload.original_name}
            </option>
          ))}
          {task?.web_source?.snapshot?.artifacts.map(artifact => <option key={artifact.artifact_id} value={artifact.artifact_id}>{artifact.title || artifact.final_url}</option>)}
          {selectedUploadId && !uploads.some(upload => upload.upload_id === selectedUploadId) && !task?.web_source?.snapshot?.artifacts.some(artifact => artifact.artifact_id === selectedUploadId) && <option value={selectedUploadId}>引用来源</option>}
        </select>
        {(ext === "pdf" || isImage) && (
          <>
            {!isImage && <>
            <button
              type="button"
              aria-label="上一页"
              disabled={page <= 1}
              onClick={() => updateView({ page: Math.max(1, page - 1), locationStatus: "当前仅浏览来源" })}
              className="rounded p-1.5 hover:bg-muted disabled:opacity-30"
            >
              <ChevronLeft className="h-4 w-4" />
            </button>
            <span className="whitespace-nowrap text-[11px] text-muted-foreground">
              {page}/{pageCount || "—"}
            </span>
            <button
              type="button"
              aria-label="下一页"
              disabled={!pageCount || page >= pageCount}
              onClick={() => updateView({ page: page + 1, locationStatus: "当前仅浏览来源" })}
              className="rounded p-1.5 hover:bg-muted disabled:opacity-30"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
            </>}
            <button
              type="button"
              aria-label="缩小"
              onClick={() => updateView({ zoom: Math.max(0.5, zoom - 0.1) })}
              className="rounded p-1.5 hover:bg-muted"
            >
              <ZoomOut className="h-4 w-4" />
            </button>
            <button
              type="button"
              aria-label="放大"
              onClick={() => updateView({ zoom: Math.min(1.8, zoom + 0.1) })}
              className="rounded p-1.5 hover:bg-muted"
            >
              <ZoomIn className="h-4 w-4" />
            </button>
          </>
        )}
        {onToggleExpand && <button type="button" className="shrink-0 rounded-lg border px-2 py-1 text-xs hover:bg-muted" aria-expanded={expanded} onClick={onToggleExpand}>{expanded ? "恢复分栏" : "展开预览"}</button>}
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭原文件预览"
          className="rounded p-1.5 text-muted-foreground hover:bg-muted"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {isImage && <p className="shrink-0 border-b px-3 py-2 text-xs text-muted-foreground">此预览仅显示原件，不执行文字识别。</p>}
      {task && <div className="shrink-0 space-y-1 border-b px-3 py-2 text-xs text-muted-foreground">
        <p>版本 V{task.viewing_revision} · {ext === "pdf" ? "PDF 原件" : isImage ? "图片原件" : source.data?.kind === "web" ? "网页摘要预览" : "解析预览"}</p>
        <p>读取时间：{source.data?.read_at ? new Date(source.data.read_at).toLocaleString() : "未提供"} · 解析版本：{source.data?.representation.parser_or_inspector_version || "未提供"}</p>
        {source.data?.content_url && <button type="button" className="rounded border px-2 py-1 hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring" onClick={() => void downloadFile(source.data!.content_url!, source.data!.original_name).catch(error => toast.error(error instanceof Error ? error.message : "原件下载失败"))}>下载原件</button>}
        {source.data?.kind === "web" && <p>仅展示已保存摘要，内容可能截断，完整性未确认。</p>}
      </div>}

      {task && <section aria-label="完整资料包下载" className="shrink-0 space-y-2 border-b px-3 py-2 text-xs">
        <p className="font-medium">当前版本全部已保存来源</p>
        <p className="text-muted-foreground">包含原件、Markdown、JSON/JSONL 和清单；未保存或未生成的内容会注明缺口。下载不会重新采集、识别或生成回答。</p>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex min-w-0 items-center gap-2">附加表格副本
            <select aria-label="附加表格副本" value={tableFormat} disabled={downloadBusy} onChange={event => setTableFormat(event.target.value as typeof tableFormat)} className="min-w-0 rounded border bg-background px-2 py-1.5 focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">
              <option value="none">不附加</option><option value="csv">CSV（适用表格）</option><option value="xlsx">XLSX（适用表格）</option>
            </select>
          </label>
          <button type="button" disabled={downloadBusy || !(task.upload_ids.length || task.web_source?.snapshot?.artifacts.length)} onClick={() => void downloadSources()}
            className="inline-flex min-w-36 items-center justify-center gap-2 rounded border px-3 py-2 hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50">
            {downloadBusy && <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />}{downloadBusy ? "正在准备下载…" : "下载完整资料包"}
          </button>
          {downloadBusy && <button type="button" onClick={() => { downloadRequest.current?.abort(); downloadRequest.current = null; setDownloadBusy(false); setDownloadStatus("已取消下载"); }} className="rounded border px-3 py-2 hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring">取消下载</button>}
        </div>
        {!(task.upload_ids.length || task.web_source?.snapshot?.artifacts.length) && <p>当前版本没有可下载的已保存来源。</p>}
        {downloadError && <p role="alert" className="text-destructive">{downloadError}</p>}
        {downloadStatus && <p role="status" className="text-muted-foreground">{downloadStatus}</p>}
      </section>}

      {source.data?.kind === "table" && <div className="shrink-0 space-y-2 border-b px-3 py-2 text-xs">
        <label className="flex items-center gap-2">工作表<select aria-label="来源工作表" className="min-w-0 flex-1 rounded border bg-background p-2" value={source.data.selected_table_ref ?? ""} onChange={event => updateView({ tableRef: event.target.value, offset: 0, scrollTop: 0, locationStatus: "当前仅浏览来源" })}>
          {source.data.tables?.map(item => <option key={item.table_ref} value={item.table_ref}>{item.name}</option>)}
        </select></label>
        <div className="flex gap-2"><input aria-label="搜索完整来源表" placeholder="搜索完整来源表" className="min-w-0 flex-1 rounded border bg-background p-2" value={currentView.searchInput ?? ""} onChange={event => updateView({ searchInput: event.target.value })}
          onKeyDown={event => { if (event.key === "Enter" && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) updateView({ search: currentView.searchInput?.trim() ?? "", offset: 0, scrollTop: 0, locationStatus: "当前仅浏览来源" }); }} />
          <button type="button" className="rounded border px-2 hover:bg-muted" onClick={() => updateView({ search: currentView.searchInput?.trim() ?? "", offset: 0, scrollTop: 0, locationStatus: "当前仅浏览来源" })}>搜索来源</button>
          {currentView.searchInput && <button type="button" aria-label="清除来源搜索" className="rounded border px-2 hover:bg-muted" onClick={() => updateView({ search: "", searchInput: "", offset: 0, scrollTop: 0, locationStatus: "当前仅浏览来源" })}>清除</button>}
        </div>
      </div>}

      {source.data && (source.data.kind === "table" || (source.data.kind === "document" && ext !== "pdf")) && <div className="shrink-0 space-y-2 border-b px-3 py-2 text-xs">
        <p>仅显示当前窗口 · 共 {source.data.total ?? "未知"} 条{source.data.is_complete ? "" : " · 内容不完整"}</p>
        <div className="flex flex-wrap items-center gap-2"><button type="button" aria-label="上一页来源" disabled={!source.data.offset || source.isFetching} className="rounded border px-2 py-1 disabled:opacity-40" onClick={() => updateView({ offset: Math.max(0, (source.data!.offset ?? 0) - 100), scrollTop: 0, locationStatus: "当前仅浏览来源" })}>上一页</button>
          <span>{source.data.total ? (source.data.offset ?? 0) + 1 : 0}–{(source.data.offset ?? 0) + (source.data.rows?.length ?? source.data.elements?.length ?? 0)}</span>
          <button type="button" aria-label="下一页来源" disabled={source.isFetching || (source.data.offset ?? 0) + (source.data.rows?.length ?? source.data.elements?.length ?? 0) >= (source.data.total ?? 0)} className="rounded border px-2 py-1 disabled:opacity-40" onClick={() => updateView({ offset: (source.data!.offset ?? 0) + 100, scrollTop: 0, locationStatus: "当前仅浏览来源" })}>下一页</button></div>
      </div>}

      {evidence && (
        <div className="shrink-0 border-b bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
          <div className="flex items-start gap-2">
            <LocateFixed className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
              {readError || pdfError || imageError ? "来源不可用，无法定位" : locate ? "正在定位来源" : currentView.locationStatus || "当前仅浏览来源"}
              {evidence.page ? ` · 第 ${String(evidence.page)} 页` : ""}
              {evidence.row_number ? ` · 原文件第 ${String(evidence.row_number)} 行` : ""}
            </span>
          </div>
          {evidenceValues.length > 0 && (
            <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 pl-5">
              {evidenceValues.map(([key, value]) => (
                <div key={key} className="min-w-0">
                  <dt className="truncate opacity-70">{key}</dt>
                  <dd className="truncate font-medium" title={String(value ?? "")}>
                    {String(value ?? "—")}
                  </dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      )}

      <div ref={contentRef} tabIndex={0} aria-label="来源内容" className="min-h-0 flex-1 overflow-auto p-4 focus-visible:ring-2 focus-visible:ring-ring"
        onScroll={event => {
          // 图片重新解码时短暂没有高度，不能把浏览器裁出的零覆盖已保存位置。
          if (isImage && !imageSize) return;
          updateView({ scrollTop: event.currentTarget.scrollTop, scrollLeft: event.currentTarget.scrollLeft });
        }}>
        {readError || pdfError || imageError ? <div role="alert" className="rounded-lg border border-destructive/30 p-4 text-sm text-destructive">来源预览失败：{readError?.message ?? pdfError ?? imageError}<button type="button" className="ml-2 rounded border px-2 py-1" onClick={() => { setPdfError(null); setImageError(null); void (source.isError ? source.refetch() : file.isError || ext === "pdf" || isImage ? file.refetch() : task ? source.refetch() : table ? tablePreview.refetch() : documentPreview.refetch()); }}>重试</button></div>
        : source.isLoading || file.isLoading || tablePreview.isLoading || documentPreview.isLoading || ((isImage || ext === "pdf") && file.data && !fileUrl) ? (
          <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
            正在读取原文件
          </div>
        ) : isImage && fileUrl ? (
          <img key={fileUrl} src={fileUrl} alt={`${selected?.original_name ?? "图片"}原件`} className="block max-w-none"
            style={imageSize ? { width: imageSize.width * zoom, height: imageSize.height * zoom } : undefined}
            onLoad={event => setImageSize({ width: event.currentTarget.naturalWidth, height: event.currentTarget.naturalHeight })}
            onError={() => setImageError("图片无法解码，请检查原件或重新上传。")} />
        ) : ext === "pdf" && fileUrl ? (
          <Document
            file={fileUrl}
            onLoadError={error => setPdfError(error.message)}
            onLoadSuccess={({ numPages }) => {
              setPageCount(numPages);
              setPage((value) => Math.min(Math.max(1, value), numPages));
            }}
            loading={null}
            className="flex justify-center"
          >
            <div
              className="relative overflow-hidden rounded bg-white shadow"
              style={{
                width: pageSize.width * zoom,
                height: pageSize.height * zoom,
              }}
            >
              <Page
                pageNumber={page}
                onLoadError={error => setPdfError(error.message)}
                scale={zoom}
                onLoadSuccess={(loadedPage) => {
                  const viewport = loadedPage.getViewport({ scale: 1 });
                  setPageSize({
                    width: viewport.width,
                    height: viewport.height,
                  });
                  setLoadedPage(page);
                }}
              />
              {evidenceLocated && Number(evidence?.page || page) === page && evidenceBoxStyle && (
                <div
                  aria-label="证据高亮"
                  className="pointer-events-none absolute border-2 border-amber-500 bg-amber-300/25"
                  style={evidenceBoxStyle}
                />
              )}
            </div>
          </Document>
        ) : source.data?.total === 0 ? <p className="p-4 text-sm text-muted-foreground">没有匹配的来源内容，请调整筛选。</p>
        : (table || source.data?.kind === "table") && sourceRows ? (
          <div className="w-max min-w-full rounded-xl border bg-background">
            <div
              className="grid border-b bg-muted/50 text-[11px] font-medium"
              style={{
                gridTemplateColumns: `repeat(${Math.max(sourceColumns.length, 1)}, minmax(140px, 1fr))`,
                minWidth: `${Math.max(sourceColumns.length, 1) * 140}px`,
              }}
            >
              {sourceColumns.map((column) => (
                <div key={column} className="border-r px-3 py-2 last:border-r-0">
                  <button type="button" disabled={!task} className="text-left hover:underline focus-visible:ring-2 focus-visible:ring-ring" onClick={() => updateView({ sortBy: column, sortDirection: currentView.sortBy === column && currentView.sortDirection !== "desc" ? "desc" : "asc", offset: 0, scrollTop: 0, locationStatus: "当前仅浏览来源" })}>{column}{currentView.sortBy === column ? currentView.sortDirection === "desc" ? " ↓" : " ↑" : ""}</button>
                </div>
              ))}
            </div>
            <div>
              {sourceRows.map(({ values: row, row_number }) => (
                <div
                  key={row_number}
                  data-source-row={row_number}
                  className={`grid border-b text-[11px] ${
                    evidenceLocated && highlightedRow === row_number && (!evidence?.table_ref || evidence.table_ref === source.data?.selected_table_ref)
                      ? "bg-amber-100 text-amber-950"
                      : "bg-background"
                  }`}
                  style={{
                    gridTemplateColumns: `repeat(${Math.max(sourceColumns.length, 1)}, minmax(140px, 1fr))`,
                    minWidth: `${Math.max(sourceColumns.length, 1) * 140}px`,
                  }}
                >
                  {sourceColumns.map((column) => (
                    <div
                      key={column}
                      className="truncate border-r px-3 py-2 last:border-r-0"
                      title={String(row[column] ?? "")}
                    >
                      {String(row[column] ?? "—")}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
        ) : source.data?.kind === "web" ? <article aria-label="网页摘要预览" className="whitespace-pre-wrap break-words text-sm leading-7">{source.data.text_preview || "未保存可展示摘要"}</article>
        : elements ? (
          <article
            aria-label={`${selected?.original_name ?? "来源"}结构化预览`}
            className="mx-auto min-h-full max-w-3xl rounded bg-white px-8 py-7 text-slate-900 shadow"
          >
            <h2 className="border-b pb-3 text-base font-semibold">
              {selected?.original_name}
            </h2>
            <div className="mt-5 space-y-2">
              {elements.map((element) => {
                const active = evidenceLocated && evidence?.element_id === element.element_id;
                return (
                  <section
                    key={element.element_id}
                    data-element-id={element.element_id}
                    className={`scroll-m-16 rounded border px-3 py-2 text-sm leading-7 ${
                      active
                        ? "border-amber-500 bg-amber-100 ring-2 ring-amber-300/50"
                        : "border-transparent"
                    }`}
                  >
                    {element.text || "（空内容）"}
                  </section>
                );
              })}
            </div>
          </article>
        ) : (
          <div className="flex h-full items-center justify-center px-8 text-center text-sm text-muted-foreground">
            当前文件无法生成结构化预览，但仍可参与执行和正式交付。
          </div>
        )}
      </div>
    </div>
  );
}
