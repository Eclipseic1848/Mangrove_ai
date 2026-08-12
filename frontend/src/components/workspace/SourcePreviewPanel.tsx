import {
  useEffect,
  useMemo,
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

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

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

export function SourcePreviewPanel({
  uploads,
  selectedUploadId,
  evidence,
  onSelectUpload,
  onClose,
}: {
  uploads: UploadItem[];
  selectedUploadId: string | null;
  evidence: Record<string, unknown> | null;
  onSelectUpload: (uploadId: string) => void;
  onClose: () => void;
}) {
  const selected =
    uploads.find((upload) => upload.upload_id === selectedUploadId)
    ?? uploads[0]
    ?? null;
  const [page, setPage] = useState(1);
  const [pageCount, setPageCount] = useState(0);
  const [zoom, setZoom] = useState(0.95);
  const [pageSize, setPageSize] = useState({ width: 612, height: 792 });
  const contentRef = useRef<HTMLDivElement>(null);
  const file = useQuery({
    queryKey: ["workspace-source-file", selected?.upload_id],
    queryFn: () => getUploadFile(selected!),
    enabled: Boolean(
      selected
      && extension(selected.original_name) === "pdf",
    ),
  });
  const fileUrl = useMemo(
    () => (file.data ? URL.createObjectURL(file.data) : null),
    [file.data],
  );
  useEffect(
    () => () => {
      if (fileUrl) URL.revokeObjectURL(fileUrl);
    },
    [fileUrl],
  );

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
    enabled: Boolean(selected && table),
  });
  const documentPreview = useQuery({
    queryKey: ["workspace-source-document", selected?.upload_id],
    queryFn: () => getDocumentPreview(selected!.upload_id),
    enabled: Boolean(
      selected
      && !table
      && extension(selected.original_name) !== "pdf",
    ),
    retry: false,
  });

  useEffect(() => {
    const evidencePage = Number(evidence?.page || 0);
    if (evidencePage > 0) setPage(evidencePage);
    const elementId = String(evidence?.element_id || "");
    if (elementId) {
      window.setTimeout(() => {
        contentRef.current
          ?.querySelector(`[data-element-id="${CSS.escape(elementId)}"]`)
          ?.scrollIntoView({ block: "center", behavior: "smooth" });
      }, 50);
    }
  }, [evidence]);

  if (!selected) {
    return (
      <div className="flex h-full flex-col items-center justify-center p-6 text-center text-sm text-muted-foreground">
        <FileText className="mb-3 h-7 w-7 opacity-40" />
        当前任务没有可预览文件
      </div>
    );
  }

  const ext = extension(selected.original_name);
  const sourceColumns = tablePreview.data?.schema.fields.map((item) => item.name) || [];
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
  const isEvidenceRow = (
    row: Record<string, unknown>,
    index: number,
  ) => {
    if (evidenceValues.length > 0) {
      return evidenceValues.every(
        ([key, value]) => String(row[key] ?? "") === String(value ?? ""),
      );
    }
    const sourceOffset = ["csv", "tsv", "xlsx"].includes(ext) ? 2 : 1;
    return highlightedRow === index + sourceOffset;
  };

  return (
    <div className="flex h-full min-h-0 flex-col bg-muted/20">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b bg-background px-3">
        <select
          value={selected.upload_id}
          onChange={(event) => onSelectUpload(event.target.value)}
          className="min-w-0 flex-1 truncate rounded-lg border bg-background px-2 py-1.5 text-xs"
        >
          {uploads.map((upload) => (
            <option key={upload.upload_id} value={upload.upload_id}>
              {upload.original_name}
            </option>
          ))}
        </select>
        {ext === "pdf" && (
          <>
            <button
              type="button"
              aria-label="上一页"
              disabled={page <= 1}
              onClick={() => setPage((value) => Math.max(1, value - 1))}
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
              onClick={() => setPage((value) => value + 1)}
              className="rounded p-1.5 hover:bg-muted disabled:opacity-30"
            >
              <ChevronRight className="h-4 w-4" />
            </button>
            <button
              type="button"
              aria-label="缩小"
              onClick={() => setZoom((value) => Math.max(0.5, value - 0.1))}
              className="rounded p-1.5 hover:bg-muted"
            >
              <ZoomOut className="h-4 w-4" />
            </button>
            <button
              type="button"
              aria-label="放大"
              onClick={() => setZoom((value) => Math.min(1.8, value + 0.1))}
              className="rounded p-1.5 hover:bg-muted"
            >
              <ZoomIn className="h-4 w-4" />
            </button>
          </>
        )}
        <button
          type="button"
          onClick={onClose}
          aria-label="关闭原文件预览"
          className="rounded p-1.5 text-muted-foreground hover:bg-muted"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      {evidence && (
        <div className="shrink-0 border-b bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-200">
          <div className="flex items-start gap-2">
            <LocateFixed className="mt-0.5 h-3.5 w-3.5 shrink-0" />
            <span>
              已定位来源
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

      <div ref={contentRef} className="min-h-0 flex-1 overflow-auto p-4">
        {file.isLoading || tablePreview.isLoading || documentPreview.isLoading ? (
          <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-5 w-5 animate-spin" />
            正在读取原文件
          </div>
        ) : ext === "pdf" && fileUrl ? (
          <Document
            file={fileUrl}
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
                scale={zoom}
                onLoadSuccess={(loadedPage) => {
                  const viewport = loadedPage.getViewport({ scale: 1 });
                  setPageSize({
                    width: viewport.width,
                    height: viewport.height,
                  });
                }}
              />
              {Number(evidence?.page || page) === page && evidenceBoxStyle && (
                <div
                  aria-label="证据高亮"
                  className="pointer-events-none absolute border-2 border-amber-500 bg-amber-300/25"
                  style={evidenceBoxStyle}
                />
              )}
            </div>
          </Document>
        ) : table && tablePreview.data ? (
          <div className="overflow-hidden rounded-xl border bg-background">
            <div
              className="grid border-b bg-muted/50 text-[11px] font-medium"
              style={{
                gridTemplateColumns: `repeat(${Math.max(sourceColumns.length, 1)}, minmax(140px, 1fr))`,
                minWidth: `${Math.max(sourceColumns.length, 1) * 140}px`,
              }}
            >
              {sourceColumns.map((column) => (
                <div key={column} className="border-r px-3 py-2 last:border-r-0">
                  {column}
                </div>
              ))}
            </div>
            <div className="max-h-[calc(100vh-190px)] overflow-auto">
              {tablePreview.data.sample.map((row, index) => (
                <div
                  key={index}
                  className={`grid border-b text-[11px] ${
                    isEvidenceRow(row, index)
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
        ) : documentPreview.data ? (
          <article
            aria-label={`${selected.original_name}结构化预览`}
            className="mx-auto min-h-full max-w-3xl rounded bg-white px-8 py-7 text-slate-900 shadow"
          >
            <h2 className="border-b pb-3 text-base font-semibold">
              {selected.original_name}
            </h2>
            <div className="mt-5 space-y-2">
              {documentPreview.data.elements.map((element) => {
                const active = evidence?.element_id === element.element_id;
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
