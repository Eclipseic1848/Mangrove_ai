import { useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  type ColumnDef,
  type SortingState,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Download,
  FileArchive,
  FileCheck2,
  Loader2,
  RotateCcw,
  Search,
  ShieldCheck,
} from "lucide-react";
import { toast } from "sonner";
import { downloadFile } from "@/lib/api";
import {
  downloadWorkspaceBundle,
  getWorkspacePreview,
} from "@/lib/semanticWorkspaceApi";
import { cn } from "@/lib/utils";
import { productText } from "@/lib/productText";
import type {
  DocumentPreviewItem,
  WorkspaceTask,
} from "@/types/semanticWorkspace";

const PAGE_SIZE = 100;

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function QualityBadge({ warning }: { warning: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
        warning
          ? "bg-amber-500/10 text-amber-700 dark:text-amber-300"
          : "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
      )}
    >
      {warning ? (
        <AlertTriangle className="h-3.5 w-3.5" />
      ) : (
        <ShieldCheck className="h-3.5 w-3.5" />
      )}
      {warning ? "有警告" : "可交付"}
    </span>
  );
}

export function CandidatePreview({
  task,
  onRetryVerification,
}: {
  task: WorkspaceTask;
  onRetryVerification?: () => Promise<void>;
}) {
  const [retrying, setRetrying] = useState(false);
  const candidates = task.agentic_runtime?.candidates || [];
  if (!candidates.length) return null;
  const verification = task.agentic_runtime?.verification;
  const verificationPassed = verification?.status === "passed";
  const verificationFailed = verification?.status === "failed";
  const canRetry = (
    verification?.status === "inconclusive" && Boolean(onRetryVerification)
  );
  const title = verificationPassed
    ? "Mangrove 候选已通过独立验证"
    : verificationFailed
      ? "Mangrove 候选验证未通过"
      : "Mangrove 未验证候选";

  return (
    <section className="mx-auto w-full max-w-6xl px-4 pb-8">
      <div className="rounded-2xl border border-amber-500/25 bg-amber-500/[0.04] shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-amber-500/20 p-5">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-amber-600 dark:text-amber-300" />
              <h2 className="font-semibold">{title}</h2>
              <span className="rounded-full bg-amber-500/10 px-2 py-1 text-[11px] font-medium text-amber-700 dark:text-amber-300">
                不是正式交付
              </span>
            </div>
            <p className="mt-2 max-w-3xl text-xs leading-5 text-muted-foreground">
              {productText(verification?.summary ??
                "文件已通过非空、格式和重新打开检查，但尚未通过独立语义验证。")}
              {" "}系统不会把候选计为正式成功。
            </p>
            {verification?.checks?.length ? (
              <ul className="mt-3 space-y-1 text-xs text-muted-foreground">
                {verification.checks.map((check) => (
                  <li key={check.code}>
                    {check.passed ? "已通过" : "未通过"}：{productText(check.summary)}
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
          {canRetry ? (
            <button
              type="button"
              disabled={retrying}
              onClick={async () => {
                if (!onRetryVerification) return;
                setRetrying(true);
                try {
                  await onRetryVerification();
                  toast.success("候选重新验证已完成");
                } catch (error) {
                  toast.error(
                    error instanceof Error
                      ? error.message
                      : "重新验证候选失败",
                  );
                } finally {
                  setRetrying(false);
                }
              }}
              className="inline-flex shrink-0 items-center gap-2 rounded-xl border border-amber-500/30 bg-background px-3.5 py-2 text-sm font-medium text-amber-800 hover:bg-amber-500/10 disabled:cursor-wait disabled:opacity-60 dark:text-amber-200"
            >
              {retrying ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RotateCcw className="h-4 w-4" />
              )}
              {retrying ? "正在重新验证" : "重新验证候选"}
            </button>
          ) : null}
        </div>
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,260px),1fr))] gap-3 p-5">
          {candidates.map((candidate) => (
            <div
              key={candidate.artifact_id}
              className="flex items-center gap-3 rounded-xl border bg-background p-3"
            >
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-amber-500/10 text-xs font-bold uppercase text-amber-700 dark:text-amber-300">
                {candidate.format === "markdown" ? "MD" : candidate.format}
              </div>
              <div className="min-w-0 flex-1">
                <button
                  type="button"
                  disabled={candidate.download_allowed === false}
                  onClick={() =>
                    void downloadFile(
                      candidate.download_url,
                      candidate.filename,
                    ).catch((error) =>
                      toast.error(
                        error instanceof Error ? error.message : "下载失败",
                      ),
                    )
                  }
                  className="block max-w-full truncate text-left text-sm font-medium hover:underline disabled:cursor-not-allowed disabled:no-underline"
                >
                  {candidate.filename}
                </button>
                <p className="mt-0.5 text-[11px] text-muted-foreground">
                  {candidate.download_allowed === false
                    ? "未通过安全检查 · 禁止下载"
                    : "未验证候选 · 可下载人工检查"}
                  {" · "}{formatBytes(candidate.size_bytes)}
                </p>
              </div>
              <button
                type="button"
                aria-label={`下载候选 ${candidate.filename}`}
                disabled={candidate.download_allowed === false}
                title={
                  candidate.download_allowed === false
                    ? "候选缺少可信来源或身份校验，已禁止下载"
                    : undefined
                }
                onClick={() =>
                  void downloadFile(
                    candidate.download_url,
                    candidate.filename,
                  ).catch((error) =>
                    toast.error(
                      error instanceof Error ? error.message : "下载失败",
                    ),
                  )
                }
                className="shrink-0 rounded-lg border p-2 text-muted-foreground hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Download className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function VirtualTable({
  columns,
  rows,
  sorting,
  onSortingChange,
  onViewSource,
}: {
  columns: string[];
  rows: Array<Record<string, unknown>>;
  sorting: SortingState;
  onSortingChange: (state: SortingState) => void;
  onViewSource: (evidence: Record<string, unknown>) => void;
}) {
  const parentRef = useRef<HTMLDivElement>(null);
  const definitions = useMemo<ColumnDef<Record<string, unknown>>[]>(
    () => [
      ...columns.map<ColumnDef<Record<string, unknown>>>((column) => ({
        accessorKey: column,
        header: column,
        cell: (info) => {
          const value = info.getValue();
          return (
            <span title={String(value ?? "")}>
              {value === null || value === undefined ? "—" : String(value)}
            </span>
          );
        },
      })),
      {
        id: "__source",
        header: "来源",
        enableSorting: false,
        cell: ({ row }) => {
          const lineage = row.original.__lineage as
            | Array<Record<string, unknown>>
            | undefined;
          if (!lineage?.length) return <span className="text-muted-foreground">—</span>;
          return (
            <button
              type="button"
              onClick={() => onViewSource(lineage[0])}
              className="whitespace-nowrap text-xs font-medium text-primary hover:underline"
            >
              查看来源
            </button>
          );
        },
      },
    ],
    [columns, onViewSource],
  );
  const table = useReactTable({
    data: rows,
    columns: definitions,
    state: { sorting },
    manualSorting: true,
    onSortingChange: (updater) =>
      onSortingChange(
        typeof updater === "function" ? updater(sorting) : updater,
      ),
    getCoreRowModel: getCoreRowModel(),
  });
  const tableRows = table.getRowModel().rows;
  const virtualizer = useVirtualizer({
    count: tableRows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 42,
    overscan: 8,
  });

  return (
    <div className="overflow-hidden rounded-xl border">
      <div
        className="grid border-b bg-muted/50 text-xs font-medium"
        style={{
          gridTemplateColumns: `repeat(${columns.length}, minmax(150px, 1fr)) 90px`,
          minWidth: `${columns.length * 150 + 90}px`,
        }}
      >
        {table.getFlatHeaders().map((header) => (
          <button
            key={header.id}
            type="button"
            disabled={!header.column.getCanSort()}
            onClick={header.column.getToggleSortingHandler()}
            className="flex h-10 items-center gap-1 border-r px-3 text-left last:border-r-0 hover:bg-muted disabled:cursor-default"
          >
            {flexRender(header.column.columnDef.header, header.getContext())}
            {header.column.getIsSorted() === "asc" && <ArrowUp className="h-3 w-3" />}
            {header.column.getIsSorted() === "desc" && <ArrowDown className="h-3 w-3" />}
          </button>
        ))}
      </div>
      <div ref={parentRef} className="max-h-[460px] overflow-auto">
        <div
          className="relative"
          style={{
            height: `${virtualizer.getTotalSize()}px`,
            minWidth: `${columns.length * 150 + 90}px`,
          }}
        >
          {virtualizer.getVirtualItems().map((virtualRow) => {
            const row = tableRows[virtualRow.index];
            return (
              <div
                key={row.id}
                className="absolute left-0 top-0 grid w-full border-b bg-background text-xs hover:bg-muted/30"
                style={{
                  transform: `translateY(${virtualRow.start}px)`,
                  gridTemplateColumns: `repeat(${columns.length}, minmax(150px, 1fr)) 90px`,
                }}
              >
                {row.getVisibleCells().map((cell) => (
                  <div
                    key={cell.id}
                    className="flex h-[42px] min-w-0 items-center truncate border-r px-3 last:border-r-0"
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function DocumentResult({
  items,
  onViewSource,
}: {
  items: DocumentPreviewItem[];
  onViewSource: (evidence: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-3">
      {items.map((item) => (
        <article key={item.id} className="rounded-xl border bg-background p-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                {item.type}
              </span>
              <h3 className="mt-1 text-sm font-semibold">{item.label}</h3>
            </div>
            {item.evidence_refs.length > 0 && (
              <button
                type="button"
                onClick={() => onViewSource(item.evidence_refs[0])}
                className="shrink-0 text-xs font-medium text-primary hover:underline"
              >
                查看来源
              </button>
            )}
          </div>
          <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-muted-foreground">
            {item.content}
          </p>
        </article>
      ))}
    </div>
  );
}

export function ResultPreview({
  task,
  onViewSource,
}: {
  task: WorkspaceTask;
  onViewSource: (evidence: Record<string, unknown>) => void;
}) {
  const [page, setPage] = useState(0);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [sorting, setSorting] = useState<SortingState>([]);
  const [includeSources, setIncludeSources] = useState(false);
  const [bundleBusy, setBundleBusy] = useState(false);
  const preview = useQuery({
    queryKey: [
      "semantic-workspace-preview",
      task.task_id,
      page,
      search,
      sorting,
    ],
    queryFn: () =>
      getWorkspacePreview(task.task_id, {
        offset: page * PAGE_SIZE,
        limit: PAGE_SIZE,
        search,
        sortBy: sorting[0]?.id,
        sortDirection: sorting[0]?.desc ? "desc" : "asc",
        revision: task.viewing_revision,
      }),
    enabled: task.status === "completed",
    placeholderData: (previous) => previous,
  });
  const delivery = task.delivery;
  const hasWarnings = Boolean(
    delivery?.outputs.some((output) => output.qa.warnings?.length),
  );

  if (!delivery) return null;
  const total = preview.data?.total ?? 0;
  const start = total ? page * PAGE_SIZE + 1 : 0;
  const end = Math.min((page + 1) * PAGE_SIZE, total);

  return (
    <section className="mx-auto w-full max-w-6xl px-4 pb-8">
      <div className="rounded-2xl border bg-card shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4 border-b p-5">
          <div className="min-w-0 flex-[1_1_320px]">
            <div className="flex flex-wrap items-center gap-2">
              <FileCheck2 className="h-5 w-5 text-primary" />
              <h2 className="font-semibold">结果与正式交付</h2>
              <QualityBadge warning={hasWarnings} />
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              已通过格式重开、文件大小和 SHA-256 校验。预览仅显示当前页，下载包含全部结果。
            </p>
          </div>
          <div className="flex flex-[1_1_260px] flex-wrap items-center justify-end gap-3">
            <label className="flex items-center gap-2 whitespace-nowrap text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={includeSources}
                onChange={(event) => setIncludeSources(event.target.checked)}
                className="h-4 w-4 rounded border"
              />
              ZIP 包含原始文件
            </label>
            <button
              type="button"
              disabled={bundleBusy}
              onClick={async () => {
                setBundleBusy(true);
                try {
                  await downloadWorkspaceBundle(
                    task.task_id,
                    `${task.title}.zip`,
                    includeSources,
                    task.viewing_revision,
                  );
                } catch (error) {
                  toast.error(
                    error instanceof Error ? error.message : "打包下载失败",
                  );
                } finally {
                  setBundleBusy(false);
                }
              }}
              className="inline-flex items-center gap-2 whitespace-nowrap rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground disabled:opacity-50"
            >
              {bundleBusy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <FileArchive className="h-3.5 w-3.5" />
              )}
              下载全部
            </button>
          </div>
        </div>

        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,260px),1fr))] gap-3 border-b p-5">
          {delivery.outputs.map((output) => (
            <div
              key={output.output_id}
              className="flex items-center gap-3 rounded-xl border bg-background p-3"
            >
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-xs font-bold uppercase text-primary">
                {output.format === "markdown" ? "MD" : output.format}
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{output.filename}</p>
                <p className="mt-0.5 flex items-center gap-1 text-[11px] text-muted-foreground">
                  <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                  可打开 · {formatBytes(output.size_bytes)}
                </p>
              </div>
              <button
                type="button"
                aria-label={`下载 ${output.filename}`}
                onClick={() =>
                  void downloadFile(output.download_url, output.filename).catch(
                    (error) =>
                      toast.error(
                        error instanceof Error ? error.message : "下载失败",
                      ),
                  )
                }
                className="shrink-0 rounded-lg border p-2 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <Download className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>

        <div className="p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div className="flex min-w-[280px] flex-1 items-center gap-2">
              <div className="relative max-w-md flex-1">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  value={searchInput}
                  onChange={(event) => setSearchInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      setPage(0);
                      setSearch(searchInput.trim());
                    }
                  }}
                  placeholder="在全部结果中搜索"
                  className="h-9 w-full rounded-lg border bg-background pl-9 pr-3 text-sm outline-none focus:border-primary"
                />
              </div>
              <button
                type="button"
                onClick={() => {
                  setPage(0);
                  setSearch(searchInput.trim());
                }}
                className="h-9 rounded-lg border px-3 text-xs font-medium hover:bg-muted"
              >
                搜索
              </button>
            </div>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span>
                预览 {start}–{end}，共 {total} 条
              </span>
              <button
                type="button"
                disabled={page === 0}
                onClick={() => setPage((value) => Math.max(0, value - 1))}
                className="rounded border p-1.5 hover:bg-muted disabled:opacity-35"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                disabled={end >= total}
                onClick={() => setPage((value) => value + 1)}
                className="rounded border p-1.5 hover:bg-muted disabled:opacity-35"
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>

          {preview.isLoading && !preview.data ? (
            <div className="flex h-52 items-center justify-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
              正在读取结果预览
            </div>
          ) : preview.isError ? (
            <div className="rounded-xl border border-destructive/20 bg-destructive/[0.04] p-4 text-sm text-destructive">
              预览失败：{preview.error.message}
            </div>
          ) : preview.data?.kind === "table" ? (
            <VirtualTable
              columns={preview.data.columns}
              rows={preview.data.rows}
              sorting={sorting}
              onSortingChange={(state) => {
                setPage(0);
                setSorting(state.slice(0, 1));
              }}
              onViewSource={onViewSource}
            />
          ) : preview.data?.kind === "document" ? (
            <DocumentResult
              items={preview.data.items}
              onViewSource={onViewSource}
            />
          ) : null}
        </div>
      </div>
    </section>
  );
}
