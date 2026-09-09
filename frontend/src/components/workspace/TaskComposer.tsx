import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ClipboardEvent,
  type ReactNode,
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
import { getUploadMetadata, uploadFileWithProgress } from "@/lib/dataPrepApi";
import { resolveWorkspaceCapabilities, type CapabilityNeed, type CapabilityResolution, type GrayCapability } from "@/lib/semanticWorkspaceApi";
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
  "png", "jpg", "jpeg", "webp",
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
  file?: File;
  name: string;
  size: number;
  progress: number;
  status: "uploading" | "ready" | "failed" | "reselect" | "restoring";
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

function inputKind(files: Array<{ name: string }>): "table" | "document" | "mixed" | "empty" {
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
  onReselect,
}: {
  item: UploadDraft;
  onRemove: () => void;
  onRetry: () => void;
  onReselect: (file: File) => void;
}) {
  const {
    attributes,
    listeners,
    setNodeRef,
    transform,
    transition,
    isDragging,
  } = useSortable({ id: item.id });
  const table = TABLE_EXTENSIONS.has(extension(item.name));
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
        aria-label={`拖动排序 ${item.name}`}
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
          <span className="truncate text-sm font-medium">{item.name}</span>
          <span className="shrink-0 text-[11px] text-muted-foreground">
            {formatBytes(item.size)}
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
        ) : item.status === "reselect" ? <p className="mt-1 text-xs text-muted-foreground">上传未确认，请重新选择文件；不会自动重传。</p>
        : item.status === "restoring" ? <p className="mt-1 text-xs text-muted-foreground">正在核验已上传文件…</p>
        : item.status === "failed" ? (
          <p className="mt-1 truncate text-xs text-destructive">
            {item.error || "上传失败"}
          </p>
        ) : (
          <p className="mt-1 flex items-center gap-1 text-xs text-emerald-700 dark:text-emerald-400">
            <Check className="h-3 w-3" />
            已上传，等待执行
          </p>
        )}
      </div>
      {item.status === "reselect" && <label className="rounded p-1.5 text-xs text-primary">
        重新选择<input type="file" aria-label={`重新选择 ${item.name}`} className="block max-w-32 text-xs" onChange={event => { const file = event.target.files?.[0]; if (file) onReselect(file); }} />
      </label>}
      {item.status === "failed" && (
        <button
          type="button"
          onClick={onRetry}
          aria-label={`重试上传 ${item.name}`}
          className="rounded p-1.5 text-muted-foreground hover:bg-muted hover:text-foreground"
        >
          <RefreshCw className="h-4 w-4" />
        </button>
      )}
      <button
        type="button"
        onClick={onRemove}
        aria-label={`移除 ${item.name}`}
        className="rounded p-1.5 text-muted-foreground hover:bg-destructive/10 hover:text-destructive"
      >
        <Trash2 className="h-4 w-4" />
      </button>
    </div>
  );
}

export type WebIntakeDraft = {
  prompt: string;
  connectionId: string | null;
  connectionModel: string | null;
  localModel: string | null;
  formats?: string[];
};

export function TaskComposer({
  compact = false,
  unified = false,
  onConfigureModels,
  onReadWeb,
  draft,
  onDraftChange,
  active = true,
  initialPrompt = "",
  initialFormats = [],
  onSubmit,
  onBusyChange,
  onUploadsChange,
  uploadStorageKey,
  initialUploads = [],
  webSourceCount = 0,
  sourceBusy = false,
  submitBlocked = false,
  sourceIdentity = "",
  modelLocked = false,
  children,
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
  unified?: boolean;
  onConfigureModels?: () => void;
  onReadWeb?: (draft: WebIntakeDraft) => void;
  draft?: WebIntakeDraft | null;
  onDraftChange?: (draft: WebIntakeDraft) => void;
  active?: boolean;
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
    capabilityNeed?: CapabilityNeed;
    capabilityPackRefs: Array<{
      pack_id: string;
      version: string;
      digest: string;
    }>;
  }) => Promise<void>;
  onBusyChange?: (busy: boolean) => void;
  onUploadsChange?: (uploads: UploadItem[]) => void;
  uploadStorageKey?: string;
  initialUploads?: UploadItem[];
  webSourceCount?: number;
  sourceBusy?: boolean;
  submitBlocked?: boolean;
  sourceIdentity?: string;
  modelLocked?: boolean;
  children?: ReactNode;
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
  const [items, setItems] = useState<UploadDraft[]>(() => {
    try {
      const stored = uploadStorageKey && localStorage.getItem(uploadStorageKey);
      if (stored) return (JSON.parse(stored) as UploadDraft[]).map(item => ({ ...item, file: undefined, status: item.upload ? "restoring" : "reselect" }));
    } catch { /* 当前草稿损坏时不伪造上传。 */ }
    return initialUploads.map(upload => ({ id: upload.upload_id, name: upload.original_name, size: upload.size_bytes, progress: 100, status: "restoring", upload }));
  });
  useEffect(() => {
    let current = true;
    for (const item of items.filter(item => item.status === "restoring" && item.upload)) {
      void getUploadMetadata(item.upload!.upload_id).then(upload => {
        if (!current) return;
        if (upload.upload_id !== item.upload!.upload_id || upload.sha256 !== item.upload!.sha256) throw new Error("已上传文件身份已改变，请重新选择");
        setItems(previous => previous.map(value => value.id === item.id ? { ...value, upload, status: "ready" } : value));
      }).catch(error => { if (current) setItems(previous => previous.map(value => value.id === item.id ? { ...value, status: "failed", error: error instanceof Error ? error.message : "已上传文件暂不可用" } : value)); });
    }
    return () => { current = false; };
  }, []);
  useEffect(() => {
    if (!uploadStorageKey || !active) return;
    try { localStorage.setItem(uploadStorageKey, JSON.stringify(items.map(({ file: _file, ...item }) => item))); } catch { /* 不存正文；当前会话仍保留资料。 */ }
  }, [items, uploadStorageKey, active]);
  const submittingRef = useRef(false);
  const uploadControllers = useRef(new Map<string, AbortController>());
  useEffect(() => () => {
    for (const controller of uploadControllers.current.values()) controller.abort();
    uploadControllers.current.clear();
  }, []);
  const [submitting, setSubmitting] = useState(false);
  const [selectedModel, setSelectedModel] = useState("");
  const [runtimeSelection, setRuntimeSelection] =
    useState<"platform_default" | "legacy" | "pi">("platform_default");
  const usesPiConfiguration =
    allowPiRuntime && runtimeSelection !== "legacy";
  const modelSelectionConnection = useRef("");
  const modelSelectionExplicit = useRef(false);
  const [selectedConnectionId, setSelectedConnectionId] = useState("");
  const [selectedConnectionModelId, setSelectedConnectionModelId] = useState("");
  const [externalApiConfirmed, setExternalApiConfirmed] = useState(false);
  const [selectedCapabilityIds, setSelectedCapabilityIds] = useState<string[]>([]);
  const [reuse, setReuse] = useState<{
    need: CapabilityNeed;
    result: CapabilityResolution | null;
    pending: boolean;
    error: string;
  } | null>(null);
  const reuseRequest = useRef(0);
  const reuseContext = JSON.stringify([prompt, formats, sourceIdentity, runtimeSelection, usesPiConfiguration, active, grayCapabilities,
    items.map(item => [item.id, item.status, item.upload])]);
  useLayoutEffect(() => {
    // 修改输入或离开当前输入流程后，旧匹配和迟到响应都不能继续启用工具。
    reuseRequest.current += 1;
    setReuse(current => current ? { ...current, result: null, pending: false, error: "任务要求、资料或输出已改变，请重新匹配工具。" } : null);
  }, [reuseContext]);
  useEffect(() => () => { reuseRequest.current += 1; }, []);
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [fileNotice, setFileNotice] = useState("");
  useEffect(() => {
    setExternalApiConfirmed(false);
    if (sourceIdentity) setReuse(current => current ? { ...current, result: null, pending: false, error: "来源集合已改变，请重新匹配或取消复用。" } : null);
  }, [sourceIdentity]);
  const promptRef = useRef<HTMLTextAreaElement>(null);
  const receivedDraft = useRef<WebIntakeDraft | null>(null);
  const receivingDraft = Boolean(active && draft && receivedDraft.current !== draft);
  useLayoutEffect(() => {
    if (!receivingDraft || !draft) return;
    receivedDraft.current = draft;
    // 仅模型交接变化属于明确选型；编辑要求不能阻挡尚未返回的默认偏好。
    if (
      draft.connectionId !== (selectedConnectionId && selectedConnectionId !== "__local__" ? selectedConnectionId : null)
      || (draft.connectionId
        ? (draft.connectionModel ?? "") !== selectedConnectionModelId
        : Boolean(draft.localModel) && draft.localModel !== selectedModel.split("::").slice(1).join("::"))
    ) modelSelectionExplicit.current = true;
    // 只交接输入和模型；附件、上传状态及运行路由留在原组件。
    setPrompt(draft.prompt);
    if (draft.formats) setFormats(draft.formats);
    setSelectedConnectionId(draft.connectionId ?? (allowLocalPiRuntime ? "__local__" : ""));
    setSelectedConnectionModelId(draft.connectionModel ?? "");
    modelSelectionConnection.current = draft.connectionId ?? "";
    if (draft.connectionId) {
      const connection = modelConnections.find(item => item.connection_id === draft.connectionId);
      setSelectedModel(`${connection?.preset_id || "external"}::${draft.connectionModel ?? ""}`);
    } else if (draft.localModel) {
      setSelectedModel(`local::${draft.localModel}`);
    }
    setExternalApiConfirmed(false);
  }, [active, draft, receivingDraft, allowLocalPiRuntime, modelConnections]);
  useLayoutEffect(() => {
    const input = promptRef.current;
    if (!active || !input || !input.getClientRects().length) return;
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 160)}px`;
  }, [active, prompt]);
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
    setReuse(null);
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
    if (!usesPiConfiguration || receivingDraft) return;
    if (!selectedConnectionId || (
      !modelSelectionExplicit.current
      && defaultConnectionId
      && selectedConnectionId !== defaultConnectionId
      && (selectedConnectionId === "__local__" || modelConnections.some(item => item.connection_id === selectedConnectionId))
    )) {
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
    receivingDraft,
  ]);
  const selectedConnection = usesPiConfiguration
    ? modelConnections.find(
        (connection) => connection.connection_id === selectedConnectionId,
      )
    : undefined;
  useEffect(() => {
    if (!usesPiConfiguration || receivingDraft) return;
    if (selectedConnection) {
      const available = (selectedConnection.models || []).filter(
        (item) => item.status === "available" && item.enabled,
      );
      if (modelSelectionConnection.current === selectedConnection.connection_id) {
        if (selectedConnectionModelId && !available.some(item => item.model_id === selectedConnectionModelId)) {
          // 已失效的选择必须人工重选，后到的偏好也不能静默替代。
          modelSelectionExplicit.current = true;
          setSelectedConnectionModelId("");
          setExternalApiConfirmed(false);
          setFileNotice("原先选择的模型已不可用，请重新选择；不会自动替换模型。");
        } else if (
          !modelSelectionExplicit.current
          && selectedConnection.connection_id === defaultConnectionId
          && defaultConnectionModel
          && defaultConnectionModel !== selectedConnectionModelId
          && available.some(item => item.model_id === defaultConnectionModel)
        ) {
          // 连接目录和本人偏好并行到达；后到偏好只能修正尚未明确选择的初始值。
          setSelectedConnectionModelId(defaultConnectionModel);
          setSelectedModel(`${selectedConnection.preset_id || "external"}::${defaultConnectionModel}`);
          setExternalApiConfirmed(false);
        }
        return;
      }
      modelSelectionConnection.current = selectedConnection.connection_id;
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
      modelSelectionConnection.current = "";
      if (modelOptions.some(option => option.provider === "local" && `${option.provider}::${option.model}` === selectedModel)) return;
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
    selectedConnectionModelId,
    selectedModel,
    defaultConnectionId,
    defaultConnectionModel,
    receivingDraft,
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
      if (!draft.file) {
        if (draft.upload) {
          try {
            const upload = await getUploadMetadata(draft.upload.upload_id);
            if (upload.sha256 !== draft.upload.sha256) throw new Error("文件身份已改变");
            setItems(current => current.map(item => item.id === draft.id ? { ...item, upload, status: "ready", error: undefined } : item));
          } catch (error) { setItems(current => current.map(item => item.id === draft.id ? { ...item, status: "failed", error: error instanceof Error ? error.message : "恢复失败" } : item)); }
        }
        return;
      }
      const controller = new AbortController();
      uploadControllers.current.get(draft.id)?.abort();
      uploadControllers.current.set(draft.id, controller);
      try {
        const upload = await uploadFileWithProgress(draft.file, (progress) => {
          if (controller.signal.aborted) return;
          setItems((current) =>
            current.map((item) =>
              item.id === draft.id ? { ...item, progress } : item,
            ),
          );
        }, controller.signal);
        if (controller.signal.aborted) return;
        setItems((current) =>
          current.some(item => item.id !== draft.id && item.upload?.upload_id === upload.upload_id && item.upload.sha256 === upload.sha256)
            ? current.filter(item => item.id !== draft.id)
            : current.map(item => item.id === draft.id ? { ...item, upload, progress: 100, status: "ready" } : item),
        );
      } catch (error) {
        if (controller.signal.aborted) return;
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
      } finally {
        if (uploadControllers.current.get(draft.id) === controller) uploadControllers.current.delete(draft.id);
      }
    },
    [],
  );

  const addFiles = (files: File[]) => {
      const supported = files.filter((file) => {
        const ext = extension(file.name);
        return TABLE_EXTENSIONS.has(ext) || DOCUMENT_EXTENSIONS.has(ext);
      });
      const drafts = supported
        .map<UploadDraft>((file) => ({
          id: nanoid(),
          file,
          name: file.name,
          size: file.size,
          progress: 0,
          status: "uploading",
        }));
      if (supported.length < files.length) {
        setFileNotice(
          "部分文件格式不受支持。可上传表格、PDF、Word、PPT、PNG/JPG/WEBP 图片、HTML、Markdown、TXT 和 XML。",
        );

      } else {
        setFileNotice("");
      }
      if (!drafts.length) return;
      setItems((current) => [...current, ...drafts]);
      if (!formats.length) {
        const kind = inputKind(drafts);
        const nextFormats = kind === "document" ? ["docx", "pdf"] : ["xlsx"];
        setFormats(nextFormats);
        updateDraft({ formats: nextFormats });
      }
      drafts.forEach((draft) => void uploadDraft(draft));
    };

  const { getRootProps, getInputProps, isDragActive, open } = useDropzone({
    noClick: true,
    noKeyboard: true,
    onDrop: addFiles,
    onDropRejected: () => {
      setFileNotice(
        "文件格式不受支持。可上传表格、PDF、Word、PPT、PNG/JPG/WEBP 图片、HTML、Markdown、TXT 和 XML。",
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
      "image/png": [".png"],
      "image/jpeg": [".jpg", ".jpeg"],
      "image/webp": [".webp"],
      "application/pdf": [".pdf"],
      "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": [".xlsx"],
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
      "application/vnd.openxmlformats-officedocument.presentationml.presentation": [".pptx"],
      "application/vnd.apache.parquet": [".parquet"],
    },
  });

  const kind = useMemo(
    () => inputKind(items),
    [items],
  );
  const busy = items.some((item) => item.status === "uploading" || item.status === "restoring") || submitting || Boolean(reuse?.pending) || sourceBusy;
  const hasFailed = items.some((item) => item.status === "failed" || item.status === "reselect");
  const reusedMatch = reuse?.result?.matches[0];
  const reuseInvalid = Boolean(reuse && !reusedMatch);
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
    (usesPiConfiguration
      && !selectedConnection
      && selectedConnectionId !== "__local__")
    || (
      externalConfirmationRequired && (!externalApiConfirmed || !selectedConnectionModelId)
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

  const currentDraft: WebIntakeDraft = {
    prompt,
    formats,
    connectionId: selectedConnectionId && selectedConnectionId !== "__local__" ? selectedConnectionId : null,
    connectionModel: selectedConnectionId && selectedConnectionId !== "__local__" ? selectedConnectionModelId : null,
    localModel: selectedConnectionId && selectedConnectionId !== "__local__" ? null : selectedModel.split("::").slice(1).join("::") || null,
  };
  // 仅用户编辑回传，避免切换视图时旧渲染的 effect 覆盖外来草稿。
  const updateDraft = (change: Partial<WebIntakeDraft>) => {
    if (!active) return;
    const nextDraft = { ...currentDraft, ...change };
    // 本组件编辑的回声已在本地应用，不能当作外来选模阻止迟到偏好。
    receivedDraft.current = nextDraft;
    if (change.prompt !== undefined || change.formats !== undefined) setExternalApiConfirmed(false);
    onDraftChange?.(nextDraft);
  };
  const readWeb = () => {
    if (!active || submitting || sourceBusy) return;
    receivedDraft.current = currentDraft;
    onDraftChange?.(currentDraft);
    onReadWeb?.(currentDraft);
  };

  const reuseCapability = async (capability: GrayCapability) => {
    const template = capability.reuse_need;
    if (!template || !usesPiConfiguration || submitting || !active) return;
    const request = ++reuseRequest.current;
    setSelectedCapabilityIds([]);
    const inputFormats = [...new Set(readyUploads.map(upload => {
      const format = extension(upload.original_name);
      return format === "md" ? "markdown" : format;
    }))];
    const need = { ...template, input_formats: inputFormats, output_formats: [...formats] };
    if (!readyUploads.length || readyUploads.length !== items.length || !formats.length
      || !template.operations.length || inputFormats.some(format => !TABLE_EXTENSIONS.has(format) && !DOCUMENT_EXTENSIONS.has(format))) {
      setReuse({ need, result: null, pending: false, error: "请先完成文件上传并选择输出格式；操作或来源格式不明确时，不能匹配同类工具。" });
      return;
    }
    setReuse({ need, result: null, pending: true, error: "" });
    try {
      const result = await resolveWorkspaceCapabilities(need);
      if (request !== reuseRequest.current) return;
      setReuse({ need, result, pending: false, error: result.matches.length ? "" : "没有兼容且获准的工具，请修复下列缺口后重新匹配。" });
    } catch {
      if (request === reuseRequest.current) setReuse({ need, result: null, pending: false, error: "工具匹配未成功，尚未启用任何工具。请核对权限或治理状态后重新匹配。" });
    }
  };

  const submit = async () => {
    if (!active || submittingRef.current) return;
    if (unified && prompt.trim() && !items.length && !webSourceCount && !submitting) {
      readWeb();
      return;
    }
    if (
      !prompt.trim()
      || (!ready.length && !webSourceCount)
      || !formats.length
      || busy
      || hasFailed
      || (kind === "mixed" && runtimeSelection === "legacy")
      || piSelectionInvalid
      || reuseInvalid
      || submitBlocked
    ) return;
    submittingRef.current = true;
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
        ...(usesPiConfiguration && reuse && reusedMatch ? { capabilityNeed: reuse.need } : {}),
        capabilityPackRefs: usesPiConfiguration && reusedMatch ? [reusedMatch.ref] : usesPiConfiguration
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
      setReuse(null);
    } catch {
      // 创建失败后重新过治理门，不能把预览时的获准状态继续当作本次许可。
      setReuse(current => current ? { ...current, result: null, pending: false, error: "任务未创建，工具尚未启用。请重新匹配并核对许可后再提交。" } : null);
      return;
    } finally {
      submittingRef.current = false;
      setSubmitting(false);
      onBusyChange?.(false);
    }
  };

  const modelControls = (<>
                  <label className="mt-3 grid grid-cols-[72px_minmax(0,1fr)] items-center gap-2 text-[11px] text-muted-foreground">
                    <span className="font-medium">模型连接</span>
                    <select
                      aria-label="模型连接"
                      disabled={modelLocked}
                      value={selectedConnectionId}
                      onChange={(event) => {
                        modelSelectionExplicit.current = true;
                        const connectionId = event.target.value;
                        const connection = modelConnections.find(item => item.connection_id === connectionId);
                        const model = connection?.default_model || connection?.models?.find(item => item.status === "available" && item.enabled)?.model_id || connection?.model || null;
                        const localModel = modelOptions.find(item => item.provider === "local")?.model ?? null;
                        setSelectedConnectionId(connectionId);
                        setSelectedConnectionModelId(model ?? "");
                        modelSelectionConnection.current = connection?.connection_id ?? "";
                        setSelectedModel(connection ? `${connection.preset_id || "external"}::${model ?? ""}` : `local::${localModel ?? ""}`);
                        setExternalApiConfirmed(false);
                        updateDraft({ connectionId: connection?.connection_id ?? null, connectionModel: model, localModel: connection ? null : localModel });
                      }}
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
                      {((selectedConnection.models?.length ?? 0) > 1 || !selectedConnectionModelId) && (
                        <label className="mt-2 grid gap-1">
                          <span className="text-muted-foreground">本任务模型</span>
                          <select
                            aria-label="本任务模型"
                      disabled={modelLocked}
                            value={selectedConnectionModelId}
                            onChange={(event) => {
                              modelSelectionExplicit.current = true;
                              setSelectedConnectionModelId(event.target.value);
                              setSelectedModel(
                                `${selectedConnection.preset_id || "external"}::${event.target.value}`,
                              );
                              setExternalApiConfirmed(false);
                              updateDraft({ connectionModel: event.target.value });
                            }}
                            className="h-8 rounded-md border bg-background px-2"
                          >
                            <option value="" disabled>请选择可用模型</option>
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
                        {webSourceCount > 0 ? "、全部已选网页的标题、正文与网址，以及本次确认的上下文" : ""}
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
  </>);
  const localModelControls = (<>
          {modelOptions.length > 0 && !selectedConnection && (
            <label className="mt-3 grid grid-cols-[64px_minmax(0,1fr)] items-center gap-x-2 gap-y-1 border-t pt-3 text-[11px] text-muted-foreground">
              <span className="font-medium">执行模型</span>
              <select
                aria-label="执行模型"
                      disabled={modelLocked}
                value={selectedModel}
                onChange={(event) => {
                  modelSelectionExplicit.current = true;
                  setSelectedModel(event.target.value);
                  updateDraft({ localModel: event.target.value.split("::").slice(1).join("::") || null });
                }}
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
          )}  </>);

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
                  onRemove={() => {
                    uploadControllers.current.get(item.id)?.abort();
                    setItems((current) => current.filter((candidate) => candidate.id !== item.id));
                  }}
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
                  onReselect={file => {
                    const next = { ...item, file, name: file.name, size: file.size, status: "uploading" as const, progress: 0, upload: undefined, error: undefined };
                    setItems(current => current.map(value => value.id === item.id ? next : value));
                    void uploadDraft(next);
                  }}
                />
              ))}
            </div>
          </SortableContext>
        </DndContext>
      )}
      <textarea
        ref={promptRef}
        value={prompt}
        aria-label="任务要求"
        onChange={(event) => {
          setPrompt(event.target.value);
          updateDraft({ prompt: event.target.value });
        }}
        onPaste={handlePaste}
        onKeyDown={event => {
          if (unified && event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229) {
            event.preventDefault();
            void submit();
          }
        }}
        rows={compact || unified ? 2 : 4}
        placeholder={
          compact
            ? "继续提出修改，例如：增加按地区汇总，并同时输出 JSON"
            : "描述你想得到的结果，例如：合并这些订单表，按订单号去重，输出 XLSX 和 JSON"
        }
        className="w-full min-h-16 max-h-40 resize-none overflow-y-auto bg-transparent px-1 text-[15px] leading-7 outline-none placeholder:text-muted-foreground/70"
      />
      {unified && (
        <div className="space-y-2" data-testid="workspace-model-picker">
          {usesPiConfiguration && modelControls}
          {localModelControls}
          <button type="button" onClick={onConfigureModels} className="rounded-lg border px-3 py-2 text-xs hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring">添加模型</button>
        </div>
      )}
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
      {reuse && <div role="status" aria-live="polite" className="mt-3 space-y-2 rounded-lg border p-3 text-xs" data-testid="capability-reuse-result">
        {reuse.pending && <p>正在匹配已获准工具…</p>}
        {reuse.error && <p className="text-destructive">{reuse.error}</p>}
        {reusedMatch && <>
          <p className="break-words font-medium">可复用：{reusedMatch.ref.pack_id} · v{reusedMatch.ref.version}</p>
          <p>匹配操作：{reusedMatch.compatibility.operations.join("、")}；输入：{reusedMatch.compatibility.input_formats.join("、")}；输出：{reusedMatch.compatibility.output_formats.join("、")}</p>
          <p>已通过选择许可；运行时检查健康状态。</p>
          <p>许可证：{reusedMatch.license}</p>
          <p className="break-all">来源：{reusedMatch.source_provenance.join("、")}</p>
        </>}
        {!reusedMatch && reuse.result?.gaps.map((gap, index) => <p key={`${gap.code}:${index}`}>{gap.remediation}</p>)}
        <button type="button" disabled={submitting} onClick={() => { reuseRequest.current += 1; setReuse(null); }} className="rounded-lg border px-3 py-2 hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">取消复用</button>
      </div>}
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
        {unified && <button type="button" onClick={readWeb} disabled={submitting || sourceBusy} className="rounded-lg border px-2.5 py-1.5 text-xs hover:bg-muted disabled:opacity-50" title="添加公开网页，保留已有资料">公开网页</button>}
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
              onClick={() => {
                  const next = selected ? formats.filter(item => item !== format) : [...formats, format];
                  setFormats(next); updateDraft({ formats: next });
                }}
              className={cn(
                "rounded-full border px-2.5 py-1 text-[11px] font-medium uppercase transition-colors",
                selected
                  ? "border-primary/30 bg-primary/10 text-foreground"
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
            || ((!unified || items.length > 0 || webSourceCount > 0) && !ready.length && !webSourceCount)
            || (ready.length > 0 && !formats.length)
            || busy
            || reuseInvalid
            || hasFailed
            || submitBlocked
            || (kind === "mixed" && runtimeSelection === "legacy")
            || ((ready.length > 0 || webSourceCount > 0) && piSelectionInvalid)
          }
          className="ml-auto inline-flex h-9 items-center gap-2 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground shadow-sm hover:bg-primary/90 disabled:cursor-not-allowed disabled:opacity-45"
        >
          {submitting ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Send className="h-4 w-4" />
          )}
          {compact ? "创建新版本" : webSourceCount ? "启动任务" : "开始执行"}
        </button>
      </div>
      {children}
      {advancedOpen && (
        <div className="mt-3 rounded-xl border bg-muted/20 p-3">
          {!compact && (
            <div className="mb-3 border-b pb-3">
              {!unified && <div className="flex flex-wrap items-center justify-between gap-2">
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
              </div>}
              {runtimeSelection !== "legacy" && (
                <>
                  {!unified && <p className="mt-2 rounded-lg bg-amber-500/10 px-2.5 py-2 text-[11px] leading-5 text-amber-700 dark:text-amber-300">
                    输入文件只读；工作区可写；容器内可使用 Shell、Python、Node、Git、npm 和 pip。
                    当前结果先作为未验证候选，不会冒充正式交付。
                  </p>}
                  {!unified && modelControls}
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
                            <div
                              key={identity}
                              className="rounded-lg border px-2.5 py-2 text-[11px]"
                            >
                            <label className="flex items-start gap-2">
                              <input
                                type="checkbox"
                                checked={selected}
                                onChange={(event) => {
                                  reuseRequest.current += 1;
                                  setReuse(null);
                                  setSelectedCapabilityIds((current) => (
                                    event.target.checked
                                      ? [...current, identity]
                                      : current.filter((item) => item !== identity)
                                  ));
                                }}
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
                            {capability.reuse_need ? <div className="mt-2 border-t pt-2">
                              <p className="break-words text-muted-foreground">复用用途：{capability.reuse_need.purpose}</p>
                              <p className="break-words text-muted-foreground">声明操作：{capability.reuse_need.operations.join("、")}</p>
                              <button type="button" disabled={submitting || Boolean(reuse?.pending)} onClick={() => void reuseCapability(capability)}
                                aria-label={`复用同类工具：${capability.name}`}
                                className="mt-2 rounded-lg border px-3 py-2 text-foreground hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50">
                                复用同类工具
                              </button>
                              <p className="mt-1 text-muted-foreground">点击即确认上述用途与操作；按当前文件和输出匹配已获准工具。</p>
                            </div> : <p className="mt-2 text-muted-foreground">缺少完整复用声明，仍可按原方式手动选择。</p>}
                            </div>
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
                  onClick={() => {
                  const next = selected ? formats.filter(item => item !== format) : [...formats, format];
                  setFormats(next); updateDraft({ formats: next });
                }}
                  className={cn(
                    "rounded-full border px-2.5 py-1 text-[11px] font-medium transition-colors",
                    selected
                      ? "border-primary/30 bg-primary/10 text-foreground"
                      : "bg-background text-muted-foreground hover:bg-muted",
                  )}
                >
                  {FORMAT_LABELS[format]}
                </button>
              );
            })}
          </div>
          {!unified && localModelControls}
        </div>
      )}
    </div>
  );
}
