import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type ClipboardEvent,
} from "react";
import { useDropzone } from "react-dropzone";
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  closestCenter,
  useSensor,
  useSensors,
  type DragEndEvent,
} from "@dnd-kit/core";
import {
  SortableContext,
  arrayMove,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  AlertCircle,
  Check,
  ChevronDown,
  FileSpreadsheet,
  FileText,
  GripVertical,
  Loader2,
  Paperclip,
  RefreshCw,
  Send,
  Settings2,
  Trash2,
  UploadCloud,
} from "lucide-react";
import { nanoid } from "nanoid/non-secure";
import { uploadFileWithProgress } from "@/lib/dataPrepApi";
import type { GrayCapability } from "@/lib/semanticWorkspaceApi";
import { cn } from "@/lib/utils";
import type { UploadItem } from "@/types/dataPrep";

const TABLE_EXTENSIONS = new Set([
  "csv",
  "tsv",
  "xlsx",
  "parquet",
  "json",
  "jsonl",
]);
const DOCUMENT_EXTENSIONS = new Set([
  "pdf",
  "docx",
  "pptx",
  "html",
  "htm",
  "md",
  "markdown",
  "txt",
  "xml",
]);
const FORMAT_OPTIONS = [
  "xlsx",
  "csv",
  "json",
  "jsonl",
  "parquet",
  "docx",
  "pdf",
  "html",
  "markdown",
  "txt",
  "pptx",
];
const TABLE_COMMON_FORMATS = ["xlsx", "csv", "json"];
const DOCUMENT_COMMON_FORMATS = ["docx", "pdf"];
const FORMAT_LABELS: Record<string, string> = {
  xlsx: "Excel",
  csv: "CSV",
  json: "JSON",
  jsonl: "JSONL",
  parquet: "Parquet",
  docx: "Word",
  pdf: "PDF",
  html: "HTML",
  markdown: "Markdown",
  txt: "TXT",
  pptx: "PPT",
};

type UploadDraft = {
  id: string;
  file: File;
  progress: number;
  status: "uploading" | "ready" | "failed";
  upload?: UploadItem;
  error?: string;
};

type ModelConnection = {
  connection_id: string;
  owner_scope: "user_personal" | "platform_shared";
  preset_id?: string | null;
  display_name: string;
  model: string;
  api_format: string;
  locality: string;
  status: string;
  default_model?: string | null;
  models?: Array<{
    model_id: string;
    display_name: string;
    status: string;
    enabled: boolean;
  }>;
};

function extension(name: string) {
  return name.split(".").pop()?.toLowerCase() ?? "";
}

function inputKind(files: File[]): "table" | "document" | "mixed" | "empty" {
  const kinds = new Set(
    files.map((file) => {
      const ext = extension(file.name);
      if (TABLE_EXTENSIONS.has(ext)) return "table";
      if (DOCUMENT_EXTENSIONS.has(ext)) return "document";
      return "unsupported";
    }),
  );
  if (!files.length) return "empty";
  if (kinds.size === 1 && kinds.has("table")) return "table";
  if (kinds.size === 1 && kinds.has("document")) return "document";
  return "mixed";
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function SortableFileCard({
  item,
  onRemove,
  onRetry,
}: {
  item: UploadDraft;
  onRemove: () => void;
  onRetry: () => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: item.id });
  const table = TABLE_EXTENSIONS.has(extension(item.file.name));
  return (
    <div
      ref={setNodeRef}
      style={{ transform: CSS.Transform.toString(transform), transition }}
      className={cn(
        "group flex items-center gap-3 rounded-xl border bg-background px-3 py-2.5 shadow-sm",
        isDragging && "z-20 border-primary/50 shadow-lg",
      )}
    >
      <button
        type="button"
        aria-label={`拖动排序 ${item.file.name}`}
        className="cursor-grab rounded p-1 text-muted-foreground opacity-50 hover:bg-muted hover:opacity-100"
        {...attributes}
        {...listeners}
      >
        <GripVertical className="h-4 w-4" />
      </button>
      <div className="rounded-lg bg-primary/10 p-2 text-primary">
        {table ? (
          <FileSpreadsheet className="h-4 w-4" />
        ) : (
          <FileText className="h-4 w-4" />
        )}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium">{item.file.name}</span>
          <span className="shrink-0 text-[11px] text-muted-foreground">
            {formatBytes(item.file.size)}
          </span>
        </div>
        {item.status === "uploading" ? (
          <div className="mt-1.5 flex items-center gap-2">
            <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
              <div
                className="h-full rounded-full bg-primary transition-[width]"
                style={{ width: `${item.progress}%` }}
              />
            </div>
            <span className="w-8 text-right text-[11px] text-muted-foreground">
              {item.progress}%
            </span>
          </div>
        ) : item.status === "failed" ? (
          <p className="mt-1 truncate text-xs text-destructive">
            {item.error || "上传失败"}
          </p>
        ) : (
          <p className="mt-1 flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
            <Check className="h-3 w-3" />
            已上传，等待执行
          </p>
        )}
      </div>
      {item.status === "failed" && (
        <button
          type="button"
          onClick={onRetry}
          aria-label={`重试上传 ${item.file.name}`}
          className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <RefreshCw className="h-4 w-4" />
        </button>
      )}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`移除 ${item.file.name}`}
        className="rounded p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
      >
        <Trash2 className="h-4 w-4" />
      </button>
    </div>
  );
}

export function TaskComposer({
  compact = false,
  initialPrompt = "",
  initialFormats = [],
  onSubmit,
  onBusyChange,
  onUploadsChange,
  modelOptions = [],
  defaultModel = null,
  allowPiRuntime = false,
  allowLocalPiRuntime = false,
  modelConnections = [],
  defaultConnectionId = null,
  defaultConnectionModel = null,
  grayCapabilities = [],
}: {
  compact?: boolean;
  initialPrompt?: string;
  initialFormats?: string[];
  onSubmit: (payload: {
    prompt: string;
    uploads: UploadItem[];
    formats: string[];
    provider: string;
    model: string | null;
    runtimeVersion?: "legacy" | "pi";
    permissionProfile: "standard";
    modelConnectionId: string | null;
    modelConnectionModel: string | null;
    externalApiConfirmed: boolean;
    capabilityPackRefs: Array<{
      pack_id: string;
      version: string;
      digest: string;
    }>;
  }) => Promise<void>;
  onBusyChange?: (busy: boolean) => void;
  onUploadsChange?: (uploads: UploadItem[]) => void;
  modelOptions?: Array<{ provider: string; model: string; label: string }>;
  defaultModel?: { provider: string; model: string; label: string } | null;
  allowPiRuntime?: boolean;
  allowLocalPiRuntime?: boolean;
  modelConnections?: ModelConnection[];
  defaultConnectionId?: string | null;
  defaultConnectionModel?: string | null;
  grayCapabilities?: GrayCapability[];
}) {
  const [prompt, setPrompt] = useState(initialPrompt);
  const [formats, setFormats] = useState<string[]>(initialFormats);
  const [items, setItems] = useState<UploadDraft[]>([]);
  const [submitting, setSubmitting] = useState(false);
  const [selectedModel, setSelectedModel] = useState("");
  const [runtimeSelection, setRuntimeSelection] =
    useState<"platform_default" | "legacy" | "pi">("platform_default");
  const usesPiConfiguration =
    allowPiRuntime && runtimeSelection !== "legacy";
  const [selectedConnectionId, setSelectedConnectionId] = useState("");
  const [selectedConnectionModelId, setSelectedConnectionModelId] = useState("");
  const [externalApiConfirmed, setExternalApiConfirmed] = useState(false);
  const [selectedCapabilityIds, setSelectedCapabilityIds] = useState<string[]>([]);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [fileNotice, setFileNotice] = useState("");
  useEffect(() => {
    if (!allowPiRuntime && runtimeSelection === "pi") {
      setRuntimeSelection("platform_default");
    }
  }, [allowPiRuntime, runtimeSelection]);
  useEffect(() => {
    if (runtimeSelection !== "legacy") return;
    setSelectedConnectionId("");
    setSelectedConnectionModelId("");
    setSelectedCapabilityIds([]);
    setExternalApiConfirmed(false);
    if (defaultModel) {
      setSelectedModel(`${defaultModel.provider}::${defaultModel.model}`);
    }
  }, [defaultModel, runtimeSelection]);
  const readyUploads = useMemo(
    () =>
      items.flatMap((item) =>
        item.status === "ready" && item.upload ? [item.upload] : [],
      ),
    [items],
  );
  useEffect(() => {
    onUploadsChange?.(readyUploads);
  }, [onUploadsChange, readyUploads]);
  useEffect(() => {
    if (!selectedModel && defaultModel) {
      setSelectedModel(`${defaultModel.provider}::${defaultModel.model}`);
    }
  }, [defaultModel, selectedModel]);
  useEffect(() => {
    if (!usesPiConfiguration) return;
    if (!selectedConnectionId) {
      setSelectedConnectionId(
        defaultConnectionId
          ? defaultConnectionId
          : allowLocalPiRuntime
          ? "__local__"
          : (modelConnections[0]?.connection_id ?? ""),
      );
    }
  }, [
    allowLocalPiRuntime,
    defaultConnectionId,
    modelConnections,
    usesPiConfiguration,
    selectedConnectionId,
  ]);
  const selectedConnection = usesPiConfiguration
    ? modelConnections.find(
        (connection) => connection.connection_id === selectedConnectionId,
      )
    : undefined;
  useEffect(() => {
    if (!usesPiConfiguration) return;
    if (selectedConnection) {
      const available = (selectedConnection.models || []).filter(
        (item) => item.status === "available" && item.enabled,
      );
      const nextModel = (
        selectedConnection.connection_id === defaultConnectionId
          ? defaultConnectionModel
          : selectedConnection.default_model
      ) || available[0]?.model_id || selectedConnection.model;
      setSelectedConnectionModelId(nextModel);
      setSelectedModel(
        `${selectedConnection.preset_id || "external"}::${nextModel}`,
      );
      return;
    }
    if (selectedConnectionId === "__local__") {
      const localModel = modelOptions.find(
        (option) => option.provider === "local",
      );
      if (localModel) {
        setSelectedModel(`${localModel.provider}::${localModel.model}`);
      }
    }
  }, [
    modelOptions,
    usesPiConfiguration,
    selectedConnection,
    selectedConnectionId,
    defaultConnectionId,
    defaultConnectionModel,
  ]);
  useEffect(() => {
    setExternalApiConfirmed(false);
  }, [usesPiConfiguration, selectedConnectionId]);
  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 6 } }),
    useSensor(KeyboardSensor, {
      coordinateGetter: sortableKeyboardCoordinates,
    }),
  );

  const uploadDraft = useCallback(
    async (draft: UploadDraft) => {
      try {
        const upload = await uploadFileWithProgress(draft.file, (progress) => {
          setItems((current) =>
            current.map((item) =>
              item.id === draft.id ? { ...item, progress } : item,
            ),
          );
        });
        setItems((current) =>
          current.map((item) =>
            item.id === draft.id
              ? { ...item, upload, progress: 100, status: "ready" }
              : item,
          ),
        );
      } catch (error) {
        setItems((current) =>
          current.map((item) =>
            item.id === draft.id
              ? {
                  ...item,
                  status: "failed",
                  error: error instanceof Error ? error.message : String(error),
                }
              : item,
          ),
        );
      }
    },
    [],
  );

  const addFiles = useCallback(
    (files: File[]) => {
      const supported = files.filter((file) => {
        const ext = extension(file.name);
        return TABLE_EXTENSIONS.has(ext) || DOCUMENT_EXTENSIONS.has(ext);
      });
      const existing = new Set(
        items.map((item) => `${item.file.name}:${item.file.size}`),
      );
      const drafts = supported
        .filter((file) => !existing.has(`${file.name}:${file.size}`))
        .map<UploadDraft>((file) => ({
          id: nanoid(),
          file,
          progress: 0,
          status: "uploading",
        }));
      if (supported.length < files.length) {
        setFileNotice(
          "部分文件格式不受支持。可上传表格、PDF、Word、PPT、HTML、Markdown、TXT 和 XML。",
        );
      } else if (!drafts.length && supported.length > 0) {
        setFileNotice("同名且大小相同的文件已经添加，本次没有重复上传。");
      } else {
        setFileNotice("");
      }
      if (!drafts.length) return;
      setItems((current) => [...current, ...drafts]);
      if (!formats.length) {
        const kind = inputKind(drafts.map((item) => item.file));
        setFormats(kind === "document" ? ["docx", "pdf"] : ["xlsx"]);
      }
      drafts.forEach((draft) => void uploadDraft(draft));
    },
    [formats.length, items, uploadDraft],
  );

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    noClick: true,
    noKeyboard: true,
    onDrop: addFiles,
    onDropRejected: () => {
      setFileNotice(
        "文件格式不受支持。可上传表格、PDF、Word、PPT、HTML、Markdown、TXT 和 XML。",
      );
    },
    accept: {
      "text/*": [
        ".csv",
        ".tsv",
        ".json",
        ".jsonl",
        ".txt",
        ".md",
        ".markdown",
        ".html",
        ".htm",
        ".xml",
      ],
      "application/pdf": [".pdf"],
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
      "application/vnd.openxmlformats-officedocument.presentationml.presentation": [".pptx"],
      "application/vnd.apache.parquet": [".parquet"],
    },
  });

  const kind = useMemo(
    () => inputKind(items.map((item) => item.file)),
    [items],
  );
  const busy = items.some((item) => item.status === "uploading") || submitting;
  const hasFailed = items.some((item) => item.status === "failed");
  const ready = items.filter((item) => item.status === "ready" && item.upload);
  const commonFormats =
    kind === "document"
      ? DOCUMENT_COMMON_FORMATS
      : kind === "table"
        ? TABLE_COMMON_FORMATS
        : FORMAT_OPTIONS.filter((format) => formats.includes(format));
  const advancedFormats = FORMAT_OPTIONS.filter(
    (format) => !commonFormats.includes(format),
  );
  const advancedSelectedCount = advancedFormats.filter((format) =>
    formats.includes(format)
  ).length + selectedCapabilityIds.length;
  const externalConfirmationRequired =
    usesPiConfiguration && Boolean(selectedConnection);
  const piSelectionInvalid =
    (runtimeSelection === "pi"
      && !selectedConnection
      && selectedConnectionId !== "__local__")
    || (
      externalConfirmationRequired && !externalApiConfirmed
    );

  const handlePaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(event.clipboardData.files);
    if (files.length) {
      event.preventDefault();
      addFiles(files);
    }
  };

  const handleDragEnd = (event: DragEndEvent) => {
    if (!event.over || event.active.id === event.over.id) return;
    setItems((current) => {
      const from = current.findIndex((item) => item.id === event.active.id);
      const to = current.findIndex((item) => item.id === event.over?.id);
      return arrayMove(current, from, to);
    });
  };

  const submit = async () => {
    if (
      !prompt.trim()
      || !ready.length
      || !formats.length
      || busy
      || (kind === "mixed" && runtimeSelection === "legacy")
      || piSelectionInvalid
    ) return;
    setSubmitting(true);
    onBusyChange?.(true);
    try {
      const [provider = "local", ...modelParts] = selectedModel.split("::");
      await onSubmit({
        prompt: prompt.trim(),
        uploads: ready.map((item) => item.upload!),
        formats,
        provider,
        model: modelParts.join("::") || null,
        runtimeVersion: runtimeSelection === "platform_default"
          ? undefined
          : runtimeSelection,
        permissionProfile: "standard",
        modelConnectionId: usesPiConfiguration
          ? selectedConnection?.connection_id ?? null
          : null,
        modelConnectionModel: usesPiConfiguration && selectedConnection
          ? selectedConnectionModelId
          : null,
        externalApiConfirmed:
          usesPiConfiguration
          && Boolean(selectedConnection)
          && externalApiConfirmed,
        capabilityPackRefs: usesPiConfiguration
          ? grayCapabilities
              .filter((item) => selectedCapabilityIds.includes(
                `${item.pack_id}@${item.version}@${item.digest}`,
              ))
              .map((item) => ({
                pack_id: item.pack_id,
                version: item.version,
                digest: item.digest,
              }))
          : [],
      });
      setPrompt("");
      setItems([]);
      setFormats([]);
      setSelectedCapabilityIds([]);
    } catch {
      return;
    } finally {
      setSubmitting(false);
      onBusyChange?.(false);
    }
  };

  return (
    <div
      {...getRootProps()}
      className={cn(
        "relative rounded-2xl border bg-background shadow-[0_18px_60px_-38px_hsl(var(--primary)/0.65)] transition-colors",
        isDragActive && "border-primary bg-primary/[0.03]",
        compact ? "p-3" : "p-4",
      )}
    >
      <input {...getInputProps()} />
      {isDragActive && (
        <div className="absolute inset-2 z-30 flex items-center justify-center rounded-xl border-2 border-dashed border-primary bg-background/95">
          <div className="text-center">
            <UploadCloud className="mx-auto h-7 w-7 text-primary" />
            <p className="mt-2 text-sm font-medium">松开后开始上传</p>
          </div>
        </div>
      )}
      {items.length > 0 && (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={handleDragEnd}
        >
          <SortableContext
            items={items.map((item) => item.id)}
            strategy={verticalListSortingStrategy}
          >
            <div className="mb-3 grid max-h-52 gap-2 overflow-auto pr-1">
              {items.map((item) => (
                <SortableFileCard
                  key={item.id}
                  item={item}
                  onRemove={() =>
                    setItems((current) =>
                      current.filter((candidate) => candidate.id !== item.id),
                    )
                  }
                  onRetry={() => {
                    setItems((current) =>
                      current.map((candidate) =>
                        candidate.id === item.id
                          ? {
                              ...candidate,
                              status: "uploading",
                              progress: 0,
                              error: undefined,
                            }
                          : candidate,
                      ),
                    );
                    void uploadDraft({ ...item, status: "uploading", progress: 0 });
                  }}
                />
              ))}
            </div>
          </SortableContext>
        </DndContext>
      )}
      <textarea
        value={prompt}
        onChange={(event) => setPrompt(event.target.value)}
        onPaste={handlePaste}
        rows={compact ? 2 : 4}
        placeholder={
          compact
            ? "继续提出修改，例如：增加按地区汇总，并同时输出 JSON"
            : "描述你想得到的结果，例如：合并这些订单表，按订单号去重，输出 XLSX 和 JSON"
        }
        className="w-full resize-none bg-transparent px-1 text-[15px] leading-7 outline-none placeholder:text-muted-foreground/70"
      />
      {kind === "mixed" && runtimeSelection === "legacy" && (
        <div className="mt-2 flex items-start gap-2 rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          当前执行器需将表格和文档分成两个任务，请移除其中一类文件。
        </div>
      )}
      {kind === "mixed" && runtimeSelection === "pi" && (
        <div className="mt-2 flex items-start gap-2 rounded-lg bg-primary/10 px-3 py-2 text-xs text-primary">
          <Check className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          Mangrove 增强模式可以在同一任务中观察并组合表格与文档来源。
        </div>
      )}
      {fileNotice && (
        <div className="mt-2 flex items-start gap-2 rounded-lg bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
          <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
          {fileNotice}
        </div>
      )}
      <div className="mt-3 flex flex-wrap items-center gap-2 border-t pt-3">
        <button
          type="button"
          onClick={open}
          className="inline-flex items-center gap-1.5 rounded-lg border px-2.5 py-1.5 text-xs font-medium hover:bg-muted"
        >
          <Paperclip className="h-3.5 w-3.5" />
          添加文件
        </button>
        <span className="mr-1 text-xs text-muted-foreground">
          {kind === "empty" && !formats.length ? "上传后自动推荐输出" : "输出格式"}
        </span>
        {commonFormats.map((format) => {
          const selected = formats.includes(format);
          return (
            <button
              key={format}
              type="button"
              aria-pressed={selected}
              onClick={() =>
                setFormats((current) =>
                  selected
                    ? current.filter((item) => item !== format)
                    : [...current, format],
                )
              }
              className={cn(
                "rounded-full border px-2.5 py-1 text-[11px] font-medium uppercase transition-colors",
                selected
                  ? "border-primary/30 bg-primary/10 text-primary"
                  : "text-muted-foreground hover:bg-muted",
              )}
            >
              {FORMAT_LABELS[format]}
            </button>
          );
        })}
        {(ready.length > 0 || formats.length > 0) && (
          <button
            type="button"
            aria-expanded={advancedOpen}
            onClick={() => setAdvancedOpen((value) => !value)}
            className="inline-flex items-center gap-1 rounded-full px-2 py-1 text-[11px] font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
          >
            更多{advancedSelectedCount > 0 ? ` · 已选 ${advancedSelectedCount}` : ""}
            <ChevronDown
              className={cn(
                "h-3 w-3 transition-transform",
                advancedOpen && "rotate-180",
              )}
            />
          </button>
        )}
        <button
          type="button"
          onClick={() => void submit()}
          disabled={
            !prompt.trim()
            || !ready.length
            || !formats.length
            || busy
            || hasFailed
            || (kind === "mixed" && runtimeSelection === "legacy")
            || piSelectionInvalid
          }
          className="ml-auto inline-flex h-9 items-center gap-2 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-45"
        >
          {submitting ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Send className="h-4 w-4" />
          )}
          {compact ? "创建新版本" : "开始执行"}
        </button>
      </div>
      {advancedOpen && (
        <div className="mt-3 rounded-xl border bg-muted/20 p-3">
          {!compact && (
            <div className="mb-3 border-b pb-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="text-[11px] font-medium">执行引擎</p>
                  <p className="mt-0.5 text-[11px] text-muted-foreground">
                    vNext 增强模式允许在任务级容器中动态使用工具。
                  </p>
                </div>
                <fieldset className="flex flex-wrap rounded-lg border bg-background p-1">
                  <legend className="sr-only">执行引擎</legend>
                  {([
                    ["platform_default", "平台默认（推荐）"],
                    ["pi", "增强模式（Pi）"],
                    ["legacy", "兼容模式（Legacy）"],
                  ] as const).map(([value, label]) => (
                    <label
                      key={value}
                      className={cn(
                        "inline-flex cursor-pointer items-center gap-1.5 rounded-md px-2.5 py-1 text-[11px] transition-colors focus-within:ring-2 focus-within:ring-primary focus-within:ring-offset-1",
                        value === "pi" && !allowPiRuntime
                          && "cursor-not-allowed opacity-45",
                        runtimeSelection === value
                          ? value === "pi"
                            ? "bg-primary/10 font-medium text-primary"
                            : "bg-muted font-medium text-foreground"
                          : "text-muted-foreground hover:bg-muted/60",
                      )}
                    >
                      <input
                        type="radio"
                        name="runtime-selection"
                        value={value}
                        checked={runtimeSelection === value}
                        disabled={value === "pi" && !allowPiRuntime}
                        onChange={() => setRuntimeSelection(value)}
                        className="h-3 w-3 accent-primary"
                      />
                      {label}
                    </label>
                  ))}
                </fieldset>
              </div>
              {runtimeSelection !== "legacy" && (
                <>
                  <p className="mt-2 rounded-lg bg-amber-500/10 px-2.5 py-2 text-[11px] leading-5 text-amber-700 dark:text-amber-300">
                    输入文件只读；工作区可写；容器内可使用 Shell、Python、Node、Git、npm 和 pip。
                    当前结果先作为未验证候选，不会冒充正式交付。
                  </p>
                  <label className="mt-3 grid grid-cols-[72px_minmax(0,1fr)] items-center gap-2 text-[11px] text-muted-foreground">
                    <span className="font-medium">模型连接</span>
                    <select
                      aria-label="模型连接"
                      value={selectedConnectionId}
                      onChange={(event) =>
                        setSelectedConnectionId(event.target.value)}
                      className="h-8 min-w-0 rounded-lg border bg-background px-2 text-[11px]"
                    >
                      {allowLocalPiRuntime && (
                        <option value="__local__">平台本地模型（不外发）</option>
                      )}
                      {modelConnections.map((connection) => (
                        <option
                          key={connection.connection_id}
                          value={connection.connection_id}
                        >
                          {connection.display_name} · {connection.model}
                          {connection.owner_scope === "platform_shared"
                            ? "（平台共享）"
                            : "（我的）"}
                        </option>
                      ))}
                    </select>
                  </label>
                  {selectedConnection && (
                    <div
                      data-testid="external-model-disclosure"
                      className="mt-3 rounded-xl border border-primary/25 bg-primary/[0.04] p-3 text-[11px] leading-5"
                    >
                      <p className="font-medium text-foreground">
                        外发确认：{selectedConnection.display_name} · {selectedConnectionModelId}
                      </p>
                      {(selectedConnection.models?.length ?? 0) > 1 && (
                        <label className="mt-2 grid gap-1">
                          <span className="text-muted-foreground">本任务模型</span>
                          <select
                            aria-label="本任务模型"
                            value={selectedConnectionModelId}
                            onChange={(event) => {
                              setSelectedConnectionModelId(event.target.value);
                              setSelectedModel(
                                `${selectedConnection.preset_id || "external"}::${event.target.value}`,
                              );
                              setExternalApiConfirmed(false);
                            }}
                            className="h-8 rounded-md border bg-background px-2"
                          >
                            {selectedConnection.models
                              ?.filter((item) => item.status === "available" && item.enabled)
                              .map((item) => (
                                <option key={item.model_id} value={item.model_id}>
                                  {item.display_name}
                                </option>
                              ))}
                          </select>
                        </label>
                      )}
                      <p className="mt-1 text-muted-foreground">
                        将发送当前任务中的
                        {kind === "table"
                          ? "表格内容"
                          : kind === "document"
                            ? "文档内容"
                            : "上传文件内容"}
                        与任务说明；仅用于当前任务版本，不授权其他任务复用。
                      </p>
                      <label className="mt-2 flex items-start gap-2 text-foreground">
                        <input
                          type="checkbox"
                          checked={externalApiConfirmed}
                          onChange={(event) =>
                            setExternalApiConfirmed(event.target.checked)}
                          className="mt-1"
                        />
                        <span>
                          我确认将上述内容发送到 {selectedConnection.display_name}
                        </span>
                      </label>
                    </div>
                  )}
                  {grayCapabilities.length > 0 && (
                    <fieldset className="mt-3 rounded-xl border bg-background p-3">
                      <legend className="px-1 text-[11px] font-medium">
                        本地任务能力（管理员灰度）
                      </legend>
                      <p className="mb-2 text-[11px] leading-5 text-muted-foreground">
                        选择会随当前任务版本冻结；创建后不可替换，不选则保持原有执行路径。
                      </p>
                      <div className="grid gap-2">
                        {grayCapabilities.map((capability) => {
                          const identity = `${capability.pack_id}@${capability.version}@${capability.digest}`;
                          const selected = selectedCapabilityIds.includes(identity);
                          return (
                            <label
                              key={identity}
                              className="flex items-start gap-2 rounded-lg border px-2.5 py-2 text-[11px]"
                            >
                              <input
                                type="checkbox"
                                checked={selected}
                                onChange={(event) => setSelectedCapabilityIds((current) => (
                                  event.target.checked
                                    ? [...current, identity]
                                    : current.filter((item) => item !== identity)
                                ))}
                                className="mt-1"
                              />
                              <span className="min-w-0">
                                <span className="font-medium text-foreground">
                                  {capability.name}
                                </span>
                                <span className="ml-1 text-muted-foreground">
                                  {capability.kind === "mcp_local" ? "MCP" : "Tool"}
                                  {` · v${capability.version}`}
                                </span>
                                <span className="mt-0.5 block text-muted-foreground">
                                  {capability.purpose}
                                </span>
                              </span>
                            </label>
                          );
                        })}
                      </div>
                    </fieldset>
                  )}
                </>
              )}
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2">
            <span className="flex items-center gap-1 text-[11px] font-medium text-muted-foreground">
              <Settings2 className="h-3.5 w-3.5" />
              更多输出
            </span>
            {advancedFormats.map((format) => {
              const selected = formats.includes(format);
              return (
                <button
                  key={format}
                  type="button"
                  aria-pressed={selected}
                  onClick={() =>
                    setFormats((current) =>
                      selected
                        ? current.filter((item) => item !== format)
                        : [...current, format],
                    )
                  }
                  className={cn(
                    "rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors",
                    selected
                      ? "border-primary/30 bg-primary/10 text-primary"
                      : "bg-background text-muted-foreground hover:bg-muted",
                  )}
                >
                  {FORMAT_LABELS[format]}
                </button>
              );
            })}
          </div>
          {modelOptions.length > 0 && !selectedConnection && (
            <label className="mt-3 grid grid-cols-[64px_minmax(0,1fr)] items-center gap-x-2 gap-y-1 border-t pt-3 text-[11px] text-muted-foreground">
              <span className="font-medium">执行模型</span>
              <select
                aria-label="执行模型"
                value={selectedModel}
                onChange={(event) => setSelectedModel(event.target.value)}
                className="h-8 min-w-0 rounded-lg border bg-background px-2 text-[11px]"
              >
                {modelOptions
                  .filter(
                    (option) =>
                      runtimeSelection === "legacy"
                      || (
                        selectedConnectionId === "__local__"
                        && option.provider === "local"
                      ),
                  )
                  .map((option) => (
                  <option
                    key={`${option.provider}::${option.model}`}
                    value={`${option.provider}::${option.model}`}
                  >
                    {option.label}
                  </option>
                ))}
              </select>
              <span className="col-start-2">默认模型通常无需调整</span>
            </label>
          )}
        </div>
      )}
    </div>
  );
}
