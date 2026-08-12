import { useEffect, useMemo, useRef, useState, type CSSProperties } from "react";
import {
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Cpu,
  FileText,
  Folder,
  FolderOpen,
  Layers3,
  Loader2,
  MessageSquareText,
  Plus,
  Send,
  Sparkles,
  Trash2,
  UploadCloud,
  X,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useDropzone } from "react-dropzone";
import { Document, Page, pdfjs } from "react-pdf";
import { Group, Panel, Separator } from "react-resizable-panels";
import { nanoid } from "nanoid/non-secure";
import "react-pdf/dist/Page/AnnotationLayer.css";
import "react-pdf/dist/Page/TextLayer.css";

import {
  archiveDocumentUnit,
  createDocumentDraft,
  createDocumentScopeRevision,
  createDocumentUnit,
  executeDocumentExtraction,
  getDocumentPreview,
  getDocumentExtractionResults,
  getDocumentWorkspace,
  getUpload,
  getUploadFile,
  listDocumentUnitRuns,
  listDocumentUnits,
  reviseDocumentDraft,
  submitDocumentReview,
  updateDocumentWorkspace,
  updateDocumentExtractionSpec,
  updateDocumentModelSelection,
  uploadFile,
} from "@/lib/dataPrepApi";
import type {
  DataTask,
  DocumentPreview,
  DocumentExtractionResult,
  DocumentModelSelection,
  DocumentTaskUnit,
  EvidenceRef,
  ExtractionSpec,
  ReviewTask,
  UploadItem,
} from "@/types/dataPrep";
import { api, downloadFile } from "@/lib/api";
import { cn } from "@/lib/utils";
import { ImageEvidenceViewer } from "@/components/ImageEvidenceViewer";

pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

type LocalDocument = {
  id: string;
  file: File;
  url: string;
  upload?: UploadItem;
  status: "uploading" | "ready" | "failed";
  previewStatus?: "parsing" | "ready" | "failed";
  preview?: DocumentPreview;
  previewError?: string;
};

type DraftField = {
  id: string;
  name: string;
  dtype: string;
  required: boolean;
  description: string;
};

const STEPS = ["上传文件", "说明目标", "执行抽取", "人工复核", "完成"];
const STEP_HELP = [
  "上传后立即检查文件并生成可读预览",
  "在右下方说明目标，再核对 AI 生成的字段",
  "系统按已确认字段执行证据约束抽取",
  "只处理冲突或低置信度字段",
  "下载权威数据和查看副本",
];
const ACTIVE_DOCUMENT_TASK_KEY = "mangrove_active_document_task";

type ModelOption = DocumentModelSelection & { label: string };
type ModelsResponse = {
  options: ModelOption[];
  document_default: ModelOption | null;
};

function draftFields(spec: ExtractionSpec): DraftField[] {
  return spec.fields.map((field) => ({
    id: nanoid(),
    name: field.name,
    dtype: field.dtype,
    required: field.required,
    description: field.description ?? "",
  }));
}

function documentTaskSpec(task: DataTask): {
  upload_ids?: string[];
  intent_messages?: string[];
  model_selection?: DocumentModelSelection;
  extraction_spec?: ExtractionSpec;
} {
  const spec = task.spec as {
    upload_ids?: string[];
    intent_messages?: string[];
    model_selection?: DocumentModelSelection;
    extraction_spec?: ExtractionSpec;
  };
  if (!spec.extraction_spec || spec.extraction_spec.result_contract) return spec;
  return {
    ...spec,
    extraction_spec: {
      ...spec.extraction_spec,
      // ResultContract 上线前的任务都是单值字段任务；只在展示层补齐，不改历史数据。
      result_contract: {
        shape: "fields",
        cardinality: "one",
        record_grain: null,
        renderer: "field_cards",
        output_formats: ["jsonl", "xlsx"],
        exhaustive: false,
      },
    },
  };
}

function candidateValue(candidate: unknown): unknown {
  if (candidate && typeof candidate === "object" && "value" in candidate) {
    return (candidate as { value: unknown }).value;
  }
  return candidate;
}

function fileExtension(file: Pick<File, "name">): string {
  return file.name.split(".").pop()?.toLowerCase() ?? "";
}

function isPdf(file: Pick<File, "name" | "type">): boolean {
  return file.type === "application/pdf" || fileExtension(file) === "pdf";
}

function isDocx(file: Pick<File, "name">): boolean {
  return fileExtension(file) === "docx";
}

function isImage(file: Pick<File, "name" | "type">): boolean {
  return file.type.startsWith("image/")
    || ["png", "jpg", "jpeg", "webp"].includes(fileExtension(file));
}

function completedDocumentTask(task: DataTask): boolean {
  return ["COMPLETED", "NEEDS_REVIEW", "FAILED"].includes(task.status);
}

function partitionDocumentUnits(units: DocumentTaskUnit[]) {
  const fileSetUploadIds = new Set(
    units
      .filter((unit) => unit.unit_type === "file_set")
      .flatMap((unit) => unit.upload_ids),
  );
  const groupedSingles = units.filter((unit) => (
    unit.unit_type === "single_file"
    && unit.upload_ids.every((uploadId) => fileSetUploadIds.has(uploadId))
  ));
  const groupedSingleIds = new Set(groupedSingles.map((unit) => unit.unit_id));
  return {
    groupedSingles,
    primaryUnits: units.filter((unit) => !groupedSingleIds.has(unit.unit_id)),
  };
}

function resultShapeLabel(spec: ExtractionSpec): string {
  const labels: Record<ExtractionSpec["result_contract"]["shape"], string> = {
    fields: "单值字段",
    records: "记录集合",
    tables: "完整表格",
    document: "文档结果",
    aggregate: "汇总结果",
  };
  return labels[spec.result_contract.shape];
}

function resultCardinalityLabel(spec: ExtractionSpec): string {
  const labels: Record<ExtractionSpec["result_contract"]["cardinality"], string> = {
    one: "单条",
    many: "多条",
    all: "全量",
  };
  return labels[spec.result_contract.cardinality];
}

export function DocumentWorkspacePage() {
  const queryClient = useQueryClient();
  const [documents, setDocuments] = useState<LocalDocument[]>([]);
  const [activeUnitId, setActiveUnitId] = useState("");
  const [expandedUnitIds, setExpandedUnitIds] = useState<string[]>([]);
  const [showGroupedSingles, setShowGroupedSingles] = useState(false);
  const [selectedId, setSelectedId] = useState("");
  const [checkedIds, setCheckedIds] = useState<string[]>([]);
  const [runHistory, setRunHistory] = useState<DataTask[]>([]);
  const [showSetComposer, setShowSetComposer] = useState(false);
  const [setName, setSetName] = useState("");
  const [setBusinessType, setSetBusinessType] = useState("");
  const [taskStatus, setTaskStatus] = useState("");
  const [pageNumber, setPageNumber] = useState(1);
  const [pageCount, setPageCount] = useState(0);
  const [fitMode, setFitMode] = useState<"page" | "width" | "custom">("page");
  const [zoom, setZoom] = useState(1);
  const [pageSize, setPageSize] = useState({ width: 595, height: 842 });
  const [previewSize, setPreviewSize] = useState({ width: 760, height: 900 });
  const [intent, setIntent] = useState("");
  const [messages, setMessages] = useState<string[]>([]);
  const [fields, setFields] = useState<DraftField[]>([]);
  const [confirmed, setConfirmed] = useState(false);
  const [taskId, setTaskId] = useState("");
  const [draftSpec, setDraftSpec] = useState<ExtractionSpec | null>(null);
  const [intentBusy, setIntentBusy] = useState(false);
  const [intentError, setIntentError] = useState("");
  const [uploadError, setUploadError] = useState("");
  const [restoring, setRestoring] = useState(true);
  const [result, setResult] = useState<DocumentExtractionResult | null>(null);
  const [reviewValues, setReviewValues] = useState<Record<string, string>>({});
  const [selectedEvidence, setSelectedEvidence] = useState<EvidenceRef | null>(null);
  const [modelOptions, setModelOptions] = useState<ModelOption[]>([]);
  const [modelSelection, setModelSelection] = useState<DocumentModelSelection | null>(null);
  const objectUrls = useRef(new Set<string>());
  const previewRef = useRef<HTMLDivElement | null>(null);
  const restoreRun = useRef(0);
  const workspaceRestored = useRef(false);
  const taskViewRun = useRef(0);

  const unitsQuery = useQuery({
    queryKey: ["document-units"],
    queryFn: ({ signal }) => listDocumentUnits(signal),
  });
  const activeUnit = unitsQuery.data?.find((unit) => unit.unit_id === activeUnitId) ?? null;
  const runsQuery = useQuery({
    queryKey: ["document-unit-runs", activeUnitId],
    queryFn: ({ signal }) => listDocumentUnitRuns(activeUnitId, signal),
    enabled: Boolean(activeUnitId),
  });

  const selected = documents.find((item) => item.id === selectedId) ?? documents[0];
  const checkedDocuments = documents.filter((item) => checkedIds.includes(item.id));
  const selectedIsPdf = Boolean(selected && isPdf(selected.file));
  const pendingReviewCount = result?.review_tasks.filter(
    (item) => item.status === "pending",
  ).length ?? 0;
  const hasAuthoritativeData = Boolean(
    result
    && (
      (result.records?.length ?? 0) > 0
      || result.tables?.some((table) => table.rows.length > 0)
      || result.documents?.some((document) => document.content.trim().length > 0)
      || result.aggregates?.some((aggregate) => (
        aggregate.fields.some((field) => field.status === "found" && field.value != null)
      ))
      || result.fields.some((field) => (
        field.status === "found" && field.value != null
      ))
    ),
  );
  const currentStep = result?.status === "COMPLETED"
    ? 4
    : result?.status === "NEEDS_REVIEW"
      ? 3
      : confirmed
        ? 2
        : documents.some((item) => item.status === "ready") ? 1 : 0;

  function clearTaskView() {
    taskViewRun.current += 1;
    localStorage.removeItem(ACTIVE_DOCUMENT_TASK_KEY);
    setTaskId("");
    setTaskStatus("");
    setDraftSpec(null);
    setMessages([]);
    setFields([]);
    setConfirmed(false);
    setResult(null);
    setReviewValues({});
    setRunHistory([]);
  }

  async function loadTaskView(task: DataTask) {
    const run = ++taskViewRun.current;
    const spec = documentTaskSpec(task);
    let extracted: DocumentExtractionResult | null = null;
    if (completedDocumentTask(task)) {
      extracted = await getDocumentExtractionResults(task.task_id);
    }
    if (run !== taskViewRun.current) return;
    setTaskId(task.task_id);
    setTaskStatus(task.status);
    setDraftSpec(spec.extraction_spec ?? null);
    if (spec.model_selection) setModelSelection(spec.model_selection);
    setFields(spec.extraction_spec ? draftFields(spec.extraction_spec) : []);
    setMessages(spec.intent_messages ?? []);
    setConfirmed(task.status !== "SPEC_DRAFT");
    setResult(extracted);
    setReviewValues({});
    localStorage.setItem(ACTIVE_DOCUMENT_TASK_KEY, task.task_id);
    if (task.status === "FAILED") {
      setIntentError(task.error || "该版本抽取失败");
    }
  }

  function documentForUpload(uploadId: string | null | undefined) {
    return documents.find((item) => item.upload?.upload_id === uploadId);
  }

  function allWorkspaceUploadIds(units: DocumentTaskUnit[] = unitsQuery.data ?? []) {
    return Array.from(new Set(units.flatMap((unit) => unit.upload_ids)));
  }

  async function restoreUpload(uploadId: string): Promise<LocalDocument> {
    const upload = await getUpload(uploadId);
    const file = await getUploadFile(upload);
    const url = URL.createObjectURL(file);
    let preview: DocumentPreview | undefined;
    let previewError: string | undefined;
    if (isDocx(file)) {
      try {
        preview = await getDocumentPreview(uploadId);
      } catch (error) {
        previewError = error instanceof Error ? error.message : "DOCX 预览解析失败";
      }
    }
    return {
      id: `restored-${uploadId}`,
      file,
      url,
      upload,
      status: "ready",
      previewStatus: isDocx(file) ? (preview ? "ready" : "failed") : undefined,
      preview,
      previewError,
    };
  }

  useEffect(() => () => {
    objectUrls.current.forEach((url) => URL.revokeObjectURL(url));
    objectUrls.current.clear();
  }, []);

  useEffect(() => {
    const target = previewRef.current;
    if (!target) return;
    const update = () => {
      setPreviewSize({
        width: target.clientWidth,
        height: target.clientHeight,
      });
    };
    update();
    const observer = new ResizeObserver(update);
    observer.observe(target);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    api.get("/api/models")
      .then((response: ModelsResponse) => {
        setModelOptions(response.options ?? []);
        setModelSelection((current) => current ?? response.document_default ?? null);
      })
      .catch(() => setIntentError("模型目录加载失败，请检查设置页配置。"));
  }, []);

  useEffect(() => {
    if (!unitsQuery.isSuccess || workspaceRestored.current) return;
    workspaceRestored.current = true;
    const run = ++restoreRun.current;
    async function restoreTask() {
      try {
        const workspace = await getDocumentWorkspace();
        const units = unitsQuery.data ?? [];
        const uploadIds = allWorkspaceUploadIds(units);
        const restoredDocuments = await Promise.all(uploadIds.map(restoreUpload));
        if (run !== restoreRun.current) {
          restoredDocuments.forEach((item) => URL.revokeObjectURL(item.url));
          return;
        }
        restoredDocuments.forEach((item) => objectUrls.current.add(item.url));
        setDocuments(restoredDocuments);
        setCheckedIds(
          restoredDocuments
            .filter((item) => workspace.checked_upload_ids.includes(
              item.upload?.upload_id ?? "",
            ))
            .map((item) => item.id),
        );
        const initialUnit = units.find(
          (unit) => unit.unit_id === workspace.active_unit_id,
        ) ?? units[0] ?? null;
        const selectedUploadId = initialUnit?.upload_ids.includes(
          workspace.selected_upload_id ?? "",
        )
          ? workspace.selected_upload_id
          : initialUnit?.upload_ids[0];
        const selectedDocument = restoredDocuments.find(
          (item) => item.upload?.upload_id === selectedUploadId,
        ) ?? restoredDocuments[0];
        setActiveUnitId(initialUnit?.unit_id ?? "");
        setSelectedId(selectedDocument?.id ?? "");
        if (initialUnit?.unit_type === "file_set") {
          setExpandedUnitIds([initialUnit.unit_id]);
        }
      } catch (error) {
        if (run === restoreRun.current) {
          setIntentError(error instanceof Error ? error.message : "历史任务恢复失败");
        }
      } finally {
        if (run === restoreRun.current) setRestoring(false);
      }
    }
    restoreTask();
  }, [unitsQuery.isSuccess, unitsQuery.data]);

  useEffect(() => {
    if (!activeUnitId || !runsQuery.data) return;
    setRunHistory(runsQuery.data);
    const latest = runsQuery.data.find(completedDocumentTask) ?? runsQuery.data[0];
    if (latest) {
      loadTaskView(latest).catch((error) => {
        setIntentError(error instanceof Error ? error.message : "任务结果读取失败");
      });
    } else {
      clearTaskView();
    }
  }, [activeUnitId, runsQuery.dataUpdatedAt]);

  async function addFiles(files: File[]) {
    if (files.length === 0) return;
    setUploadError("");
    clearTaskView();
    const additions = files.map((file) => {
      const url = URL.createObjectURL(file);
      objectUrls.current.add(url);
      return {
        id: `${file.name}-${file.lastModified}-${nanoid()}`,
        file,
        url,
        status: "uploading" as const,
      };
    });
    setDocuments((current) => [...current, ...additions]);
    if (additions[0]) setSelectedId(additions[0].id);
    const completedUploads = await Promise.all(additions.map(async (item) => {
      try {
        const upload = await uploadFile(item.file);
        setDocuments((current) => current.map((doc) => (
          doc.id === item.id
            ? {
                ...doc,
                upload,
                status: "ready",
                previewStatus: isDocx(doc.file) ? "parsing" : undefined,
              }
            : doc
        )));
        if (isDocx(item.file)) {
          try {
            const preview = await getDocumentPreview(upload.upload_id);
            setDocuments((current) => current.map((doc) => (
              doc.id === item.id
                ? { ...doc, preview, previewStatus: "ready", previewError: undefined }
                : doc
            )));
          } catch (error) {
            const detail = error instanceof Error ? error.message : "DOCX 预览解析失败";
            setDocuments((current) => current.map((doc) => (
              doc.id === item.id
                ? { ...doc, previewStatus: "failed", previewError: detail }
                : doc
            )));
          }
        }
        return { id: item.id, upload };
      } catch (error) {
        const detail = error instanceof Error ? error.message : "上传失败";
        setUploadError(`${item.file.name}：${detail}`);
        setDocuments((current) => current.map((doc) => (
          doc.id === item.id ? { ...doc, status: "failed" } : doc
        )));
        return null;
      }
    }));
    const succeeded = completedUploads.filter(
      (item): item is { id: string; upload: UploadItem } => item !== null,
    );
    if (succeeded.length === 0) return;
    setCheckedIds(succeeded.map((item) => item.id));
    try {
      const createdUnits = await Promise.all(succeeded.map((item) => (
        createDocumentUnit({
          unit_type: "single_file",
          name: item.upload.original_name,
          upload_ids: [item.upload.upload_id],
        })
      )));
      await queryClient.invalidateQueries({ queryKey: ["document-units"] });
      const firstUnit = createdUnits[0];
      setActiveUnitId(firstUnit.unit_id);
      setSelectedId(succeeded[0].id);
      clearTaskView();
      await updateDocumentWorkspace({
        upload_ids: Array.from(new Set([
          ...allWorkspaceUploadIds(),
          ...succeeded.map((item) => item.upload.upload_id),
        ])),
        checked_upload_ids: succeeded.map((item) => item.upload.upload_id),
        active_task_id: null,
        active_unit_id: firstUnit.unit_id,
        selected_upload_id: succeeded[0]?.upload.upload_id
          ?? null,
      });
    } catch (error) {
      setUploadError(
        error instanceof Error ? error.message : "文件工作区保存失败",
      );
    }
  }

  const dropzone = useDropzone({
    onDropAccepted: addFiles,
    onDropRejected: (rejections) => {
      const names = rejections.map((item) => item.file.name).join("、");
      setUploadError(
        `${names || "所选文件"}不受支持。请选择 PDF、DOCX、ZIP、PNG、JPG、JPEG 或 WEBP 文件。`,
      );
    },
    multiple: true,
    accept: {
      "application/pdf": [".pdf"],
      "application/vnd.openxmlformats-officedocument.wordprocessingml.document": [".docx"],
      "application/zip": [".zip"],
      "image/*": [".png", ".jpg", ".jpeg", ".webp"],
    },
  });

  async function selectUnit(unit: DocumentTaskUnit, uploadId?: string) {
    const target = documentForUpload(uploadId ?? unit.upload_ids[0]);
    clearTaskView();
    setActiveUnitId(unit.unit_id);
    if (unit.unit_type === "file_set") {
      setExpandedUnitIds((current) => (
        current.includes(unit.unit_id) ? current : [...current, unit.unit_id]
      ));
    }
    if (target) setSelectedId(target.id);
    setPageNumber(1);
    setSelectedEvidence(null);
    setIntentError("");
    try {
      await updateDocumentWorkspace({
        upload_ids: allWorkspaceUploadIds(),
        checked_upload_ids: checkedDocuments.flatMap((item) => (
          item.upload ? [item.upload.upload_id] : []
        )),
        active_task_id: unit.latest_task?.task_id ?? null,
        active_unit_id: unit.unit_id,
        selected_upload_id: target?.upload?.upload_id ?? unit.upload_ids[0] ?? null,
      });
    } catch (error) {
      setIntentError(error instanceof Error ? error.message : "任务历史读取失败");
    }
  }

  async function selectDocument(id: string) {
    const target = documents.find((item) => item.id === id);
    setSelectedId(id);
    setPageNumber(1);
    setSelectedEvidence(null);
    if (!target?.upload) return;
    try {
      await updateDocumentWorkspace({
        upload_ids: allWorkspaceUploadIds(),
        checked_upload_ids: checkedDocuments.flatMap((item) => (
          item.upload ? [item.upload.upload_id] : []
        )),
        active_task_id: taskId || null,
        active_unit_id: activeUnitId || null,
        selected_upload_id: target.upload.upload_id,
      });
    } catch (error) {
      setIntentError(error instanceof Error ? error.message : "预览位置保存失败");
    }
  }

  async function toggleDocumentScope(id: string, checked: boolean) {
    const nextCheckedIds = checked
      ? [...checkedIds, id].filter((item, index, all) => all.indexOf(item) === index)
      : checkedIds.filter((item) => item !== id);
    setCheckedIds(nextCheckedIds);
    try {
      await updateDocumentWorkspace({
        upload_ids: allWorkspaceUploadIds(),
        checked_upload_ids: documents
          .filter((item) => nextCheckedIds.includes(item.id))
          .flatMap((item) => item.upload ? [item.upload.upload_id] : []),
        active_task_id: taskId || null,
        active_unit_id: activeUnitId || null,
        selected_upload_id: selected?.upload?.upload_id ?? null,
      });
    } catch (error) {
      setCheckedIds(checkedIds);
      setIntentError(
        error instanceof Error ? error.message : "文件选择保存失败",
      );
    }
  }

  async function createFileSetFromSelection() {
    const uploadIds = checkedDocuments.flatMap((item) => (
      item.upload ? [item.upload.upload_id] : []
    ));
    if (uploadIds.length < 2) {
      setIntentError("请至少选择两个文件后再创建文件集。");
      return;
    }
    const name = setName.trim();
    if (!name) {
      setIntentError("请为文件集填写一个便于识别的名称。");
      return;
    }
    setIntentBusy(true);
    setIntentError("");
    try {
      const unit = await createDocumentUnit({
        unit_type: "file_set",
        name,
        business_type: setBusinessType.trim() || undefined,
        upload_ids: uploadIds,
      });
      await queryClient.invalidateQueries({ queryKey: ["document-units"] });
      setExpandedUnitIds((current) => [...current, unit.unit_id]);
      setShowSetComposer(false);
      setSetName("");
      setSetBusinessType("");
      setCheckedIds([]);
      await selectUnit(unit);
    } catch (error) {
      setIntentError(
        error instanceof Error ? error.message : "文件集创建失败",
      );
    } finally {
      setIntentBusy(false);
    }
  }

  async function reviseActiveFileSet() {
    if (!activeUnit || activeUnit.unit_type !== "file_set" || !taskId) return;
    const uploadIds = checkedDocuments.flatMap((item) => (
      item.upload ? [item.upload.upload_id] : []
    ));
    if (uploadIds.length === 0) {
      setIntentError("请至少勾选一个文件作为新版本范围。");
      return;
    }
    const confirmedRevision = window.confirm(
      `确定把文件集“${activeUnit.name}”调整为 ${uploadIds.length} 份文件吗？\n\n`
      + "系统会创建下一不可变版本；旧版本和旧产物继续保留。",
    );
    if (!confirmedRevision) return;
    setIntentBusy(true);
    setIntentError("");
    try {
      const revised = await createDocumentScopeRevision(taskId, uploadIds);
      setTaskId(revised.task_id);
      setTaskStatus(revised.status);
      setDraftSpec(revised.extraction_spec);
      setConfirmed(true);
      setResult(null);
      localStorage.setItem(ACTIVE_DOCUMENT_TASK_KEY, revised.task_id);
      await queryClient.invalidateQueries({ queryKey: ["document-units"] });
      await queryClient.invalidateQueries({
        queryKey: ["document-unit-runs", activeUnit.unit_id],
      });
    } catch (error) {
      setIntentError(error instanceof Error ? error.message : "文件集版本创建失败");
    } finally {
      setIntentBusy(false);
    }
  }

  async function removeUnitFromWorkspace(unit: DocumentTaskUnit) {
    const unitLabel = unit.unit_type === "file_set" ? "文件集" : "独立文件";
    const confirmedRemove = window.confirm(
      `确定将${unitLabel}“${unit.name}”从工作区移除吗？\n\n`
      + "原文件、抽取结果和历史版本都会保留；刷新后不会重新出现。",
    );
    if (!confirmedRemove) return;
    setIntentBusy(true);
    setIntentError("");
    try {
      await archiveDocumentUnit(unit.unit_id);
      const nextUnits = (unitsQuery.data ?? []).filter(
        (item) => item.unit_id !== unit.unit_id,
      );
      queryClient.setQueryData(["document-units"], nextUnits);
      const retainedUploadIds = new Set(
        nextUnits.flatMap((item) => item.upload_ids),
      );
      setDocuments((current) => current.filter((document) => {
        const uploadId = document.upload?.upload_id;
        const retained = !uploadId || retainedUploadIds.has(uploadId);
        if (!retained) {
          URL.revokeObjectURL(document.url);
          objectUrls.current.delete(document.url);
        }
        return retained;
      }));
      const { primaryUnits } = partitionDocumentUnits(nextUnits);
      const nextUnit = primaryUnits[0] ?? nextUnits[0] ?? null;
      if (activeUnitId === unit.unit_id) {
        clearTaskView();
        setActiveUnitId(nextUnit?.unit_id ?? "");
        const nextDocument = documentForUpload(nextUnit?.upload_ids[0]);
        setSelectedId(nextDocument?.id ?? "");
        await updateDocumentWorkspace({
          upload_ids: allWorkspaceUploadIds(nextUnits),
          checked_upload_ids: checkedDocuments.flatMap((item) => (
            item.upload && nextUnits.some(
              (candidate) => candidate.upload_ids.includes(item.upload!.upload_id),
            )
              ? [item.upload.upload_id]
              : []
          )),
          active_task_id: nextUnit?.latest_task?.task_id ?? null,
          active_unit_id: nextUnit?.unit_id ?? null,
          selected_upload_id: nextDocument?.upload?.upload_id ?? null,
        });
      }
      await queryClient.invalidateQueries({ queryKey: ["document-units"] });
    } catch (error) {
      setIntentError(error instanceof Error ? error.message : "从工作区移除失败");
    } finally {
      setIntentBusy(false);
    }
  }

  async function submitIntent() {
    const text = intent.trim();
    if (!text) return;
    const uploadIds = activeUnit?.upload_ids ?? [];
    if (!activeUnit) {
      setIntentError("请先在左侧选择一个独立文件或文件集。");
      return;
    }
    if (uploadIds.length === 0) {
      setIntentError("当前任务单元没有可处理的文件。");
      return;
    }
    if (!modelSelection) {
      setIntentError("当前没有可用的文档抽取模型，请先到设置页配置。");
      return;
    }
    setIntentBusy(true);
    setIntentError("");
    try {
      const currentScope = draftSpec?.discovery.artifact_ids ?? [];
      const sameScope = currentScope.length === uploadIds.length
        && currentScope.every((uploadId) => uploadIds.includes(uploadId));
      const canReviseDraft = taskId
        && taskStatus === "SPEC_DRAFT"
        && !result
        && sameScope;
      const draft = canReviseDraft
        ? await reviseDocumentDraft(taskId, text, modelSelection)
        : await createDocumentDraft(activeUnit.unit_id, text, modelSelection);
      setTaskId(draft.task_id);
      setTaskStatus(draft.status);
      localStorage.setItem(ACTIVE_DOCUMENT_TASK_KEY, draft.task_id);
      setDraftSpec(draft.extraction_spec);
      setModelSelection(draft.model_selection);
      setFields(draftFields(draft.extraction_spec));
      setMessages((current) => (
        canReviseDraft ? [...current, text] : [text]
      ));
      setIntent("");
      setConfirmed(false);
      setResult(null);
      await updateDocumentWorkspace({
        upload_ids: allWorkspaceUploadIds(),
        checked_upload_ids: checkedDocuments.flatMap((item) => (
          item.upload ? [item.upload.upload_id] : []
        )),
        active_task_id: draft.task_id,
        active_unit_id: activeUnit.unit_id,
        selected_upload_id: selected?.upload?.upload_id ?? uploadIds[0] ?? null,
      });
      await queryClient.invalidateQueries({ queryKey: ["document-units"] });
    } catch (error) {
      setIntentError(error instanceof Error ? error.message : "抽取方案生成失败");
    } finally {
      setIntentBusy(false);
    }
  }

  function updateField(id: string, patch: Partial<DraftField>) {
    setFields((current) => current.map((field) => (
      field.id === id ? { ...field, ...patch } : field
    )));
    setConfirmed(false);
  }

  async function confirmSpec() {
    if (!taskId || !draftSpec || !modelSelection) {
      setIntentError("请先发送需求，让 AI 生成抽取方案。");
      return;
    }
    setIntentBusy(true);
    setIntentError("");
    try {
      await updateDocumentModelSelection(taskId, modelSelection);
      const updated: ExtractionSpec = {
        ...draftSpec,
        fields: fields.map((field) => ({
          name: field.name,
          dtype: field.dtype,
          required: field.required,
          description: field.description,
          require_evidence: true,
          min_confidence: 0.9,
        })),
      };
      const saved = await updateDocumentExtractionSpec(taskId, updated);
      setDraftSpec(saved.extraction_spec);
      setConfirmed(true);
      setTaskStatus(saved.status);
      const extracted = await executeDocumentExtraction(taskId);
      setResult(extracted);
      setTaskStatus(extracted.status);
      await updateDocumentWorkspace({
        upload_ids: allWorkspaceUploadIds(),
        checked_upload_ids: checkedDocuments.flatMap((item) => (
          item.upload ? [item.upload.upload_id] : []
        )),
        active_task_id: taskId,
        active_unit_id: activeUnitId || null,
        selected_upload_id: selected?.upload?.upload_id ?? null,
      });
      await queryClient.invalidateQueries({ queryKey: ["document-units"] });
    } catch (error) {
      setIntentError(error instanceof Error ? error.message : "方案保存失败");
    } finally {
      setIntentBusy(false);
    }
  }

  async function resolveReview(
    review: ReviewTask,
    decision: "accept_candidate" | "replace" | "mark_not_found",
  ) {
    if (!taskId) return;
    setIntentBusy(true);
    setIntentError("");
    try {
      const updated = await submitDocumentReview(taskId, review.task_id, {
        decision,
        candidate_index: decision === "accept_candidate" ? 0 : undefined,
        value: decision === "replace" ? reviewValues[review.task_id] : undefined,
      });
      setResult(updated);
      setTaskStatus(updated.status);
    } catch (error) {
      setIntentError(error instanceof Error ? error.message : "复核结果保存失败");
    } finally {
      setIntentBusy(false);
    }
  }

  async function downloadArtifact(path: string, filename: string) {
    if (!taskId) return;
    setIntentError("");
    try {
      await downloadFile(`/api/downloads/${taskId}/${path}`, filename);
    } catch (error) {
      setIntentError(error instanceof Error ? error.message : "产物下载失败");
    }
  }

  function focusEvidence(evidence: EvidenceRef) {
    const artifact = result?.artifacts?.find(
      (item) => item.artifact_id === evidence.artifact_id,
    );
    const targetDocument = artifact
      ? documents.find((item) => (
        item.upload?.upload_id === artifact.upload_id
        || item.file.name === artifact.original_name
      ))
      : undefined;
    if (targetDocument) setSelectedId(targetDocument.id);
    setSelectedEvidence(evidence);
    setPageNumber(evidence.page);
  }

  useEffect(() => {
    if (!selectedEvidence || !selected?.preview) return;
    const target = document.querySelector(
      `[data-element-id="${CSS.escape(selectedEvidence.element_id)}"]`,
    );
    target?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [selectedEvidence, selected]);

  const scopeLabel = useMemo(
    () => activeUnit
      ? activeUnit.unit_type === "file_set"
        ? `${activeUnit.name} · ${activeUnit.upload_ids.length} 份文件`
        : activeUnit.name
      : documents.length > 0 ? "尚未选择任务" : "尚未上传",
    [activeUnit, documents.length],
  );
  const selectedArtifactIds = result?.artifacts
    ?.filter((artifact) => artifact.upload_id === selected?.upload?.upload_id)
    .map((artifact) => artifact.artifact_id) ?? [];
  const restrictToSelectedArtifact = activeUnit?.unit_type !== "file_set";
  const visibleRecords = result?.records?.filter((record) => (
    !restrictToSelectedArtifact
    || selectedArtifactIds.length === 0
    || record.source_artifact_ids.length === 0
    || record.source_artifact_ids.some((artifactId) => (
      selectedArtifactIds.includes(artifactId)
    ))
  )) ?? [];
  const visibleTables = result?.tables?.filter((table) => (
    !restrictToSelectedArtifact
    || selectedArtifactIds.length === 0
    || selectedArtifactIds.includes(table.artifact_id)
  )) ?? [];
  const visibleDocuments = result?.documents?.filter((document) => (
    !restrictToSelectedArtifact
    || selectedArtifactIds.length === 0
    || document.source_artifact_ids.some((artifactId) => (
      selectedArtifactIds.includes(artifactId)
    ))
  )) ?? [];
  const visibleAggregates = result?.aggregates?.filter((aggregate) => (
    !restrictToSelectedArtifact
    || selectedArtifactIds.length === 0
    || aggregate.source_artifact_ids.length === 0
    || aggregate.source_artifact_ids.some((artifactId) => (
      selectedArtifactIds.includes(artifactId)
    ))
  )) ?? [];
  const recordColumns = Array.from(new Set(
    visibleRecords.flatMap((record) => Object.keys(record.values)),
  ));
  const visibleFields = result?.fields.filter((field) => (
    !restrictToSelectedArtifact
    || selectedArtifactIds.length === 0
    || field.evidence_refs.length === 0
    || field.evidence_refs.some(
      (evidence) => selectedArtifactIds.includes(evidence.artifact_id),
    )
  )) ?? [];
  const selectedModelRef = modelSelection
    ? `${modelSelection.provider}::${modelSelection.model}`
    : "";
  const selectedModelLabel = modelOptions.find(
    (item) => item.provider === modelSelection?.provider && item.model === modelSelection?.model,
  )?.label;
  const availableWidth = Math.max(320, previewSize.width - 32);
  const availableHeight = Math.max(320, previewSize.height - 32);
  const pageAspect = pageSize.height / pageSize.width;
  const fitPageWidth = Math.min(availableWidth, availableHeight / pageAspect);
  const renderedPageWidth = fitMode === "page"
    ? fitPageWidth
    : fitMode === "width"
      ? availableWidth
      : pageSize.width * zoom;
  const renderedPageHeight = renderedPageWidth * pageAspect;
  const evidenceBoxStyle: CSSProperties | null = (() => {
    const bbox = selectedEvidence?.bbox;
    if (!bbox) return null;
    if (bbox.coordinate_space === "normalized_1000") {
      return {
        left: bbox.x0 / 1000 * renderedPageWidth,
        top: bbox.y0 / 1000 * renderedPageHeight,
        width: (bbox.x1 - bbox.x0) / 1000 * renderedPageWidth,
        height: (bbox.y1 - bbox.y0) / 1000 * renderedPageHeight,
      };
    }
    if (bbox.coordinate_space === "pdf_points") {
      return {
        left: bbox.x0 / pageSize.width * renderedPageWidth,
        top: bbox.y0 / pageSize.height * renderedPageHeight,
        width: (bbox.x1 - bbox.x0) / pageSize.width * renderedPageWidth,
        height: (bbox.y1 - bbox.y0) / pageSize.height * renderedPageHeight,
      };
    }
    return null;
  })();
  const uploadsPending = documents.some((item) => item.status === "uploading");
  const canExecuteDraft = Boolean(
    draftSpec
    && (
      fields.length > 0
      || draftSpec.result_contract.shape === "tables"
      || draftSpec.result_contract.shape === "document"
    ),
  );
  const units = unitsQuery.data ?? [];
  const { groupedSingles, primaryUnits } = partitionDocumentUnits(units);
  const displayedUnits = showGroupedSingles
    ? units
    : units.filter((unit) => (
        primaryUnits.some((primary) => primary.unit_id === unit.unit_id)
        || unit.unit_id === activeUnitId
      ));
  const assignedUploadIds = new Set(units.flatMap((unit) => unit.upload_ids));
  const pendingDocuments = documents.filter((item) => (
    !item.upload
    || (
      item.status !== "ready"
      && !assignedUploadIds.has(item.upload.upload_id)
    )
  ));
  const primaryHint = result
    ? result.status === "COMPLETED"
      ? "当前版本已完成；可下载结果，或输入新要求创建下一版本。"
      : result.status === "FAILED"
        ? "本次没有产出有效数据，请查看错误提示并调整文件或任务要求后重试。"
        : `还需处理 ${pendingReviewCount} 项复核，全部完成后任务才会结束。`
    : documents.length === 0
      ? "第 1 步：请先上传 PDF、DOCX 或图片。"
      : uploadsPending
        ? "文件上传中，绿色勾选后才能生成方案。"
        : messages.length === 0
          ? "第 2 步：在右下方输入你真正想提取的数据。"
          : "请核对字段名称和说明，确认后才会执行抽取。";

  return (
    <div className="flex h-full min-h-0 flex-col bg-muted/20" data-testid="document-workspace">
      <div className="flex h-11 shrink-0 items-center gap-2 border-b bg-background px-4">
        {STEPS.map((step, index) => (
          <div key={step} className="flex items-center gap-2">
            <div className={cn(
              "flex h-6 w-6 items-center justify-center rounded-full border text-xs",
              index < currentStep && "border-primary bg-primary text-primary-foreground",
              index === currentStep && "border-primary text-primary",
              index > currentStep && "text-muted-foreground",
            )}>
              {index < currentStep ? <Check className="h-3.5 w-3.5" /> : index + 1}
            </div>
            <span className={cn(
              "hidden text-xs xl:inline",
              index === currentStep ? "font-medium text-foreground" : "text-muted-foreground",
            )}>{step}</span>
            {index < STEPS.length - 1 && <div className="h-px w-5 bg-border xl:w-8" />}
          </div>
        ))}
        <span className="hidden truncate text-[11px] text-muted-foreground 2xl:block">
          当前：{STEP_HELP[currentStep]}
        </span>
        <div className="ml-auto flex min-w-0 items-center gap-2">
          <Cpu className="hidden h-3.5 w-3.5 text-muted-foreground xl:block" />
          <select
            aria-label="文档抽取模型"
            value={selectedModelRef}
            disabled={intentBusy}
            onChange={(event) => {
              const selectedOption = modelOptions.find(
                (item) => `${item.provider}::${item.model}` === event.target.value,
              );
              if (selectedOption) {
                if (
                  selectedOption.provider !== "local"
                  && !window.confirm(
                    "切换到云端 OpenAPI 模型后，解析出的文档文本会发送给该服务商。"
                    + "原始图片不会随此文本抽取请求发送。确定继续吗？",
                  )
                ) {
                  return;
                }
                setModelSelection({
                  provider: selectedOption.provider,
                  model: selectedOption.model,
                });
                setConfirmed(false);
              }
            }}
            className="h-7 max-w-64 rounded border bg-background px-2 text-xs"
          >
            {modelOptions.map((item) => (
              <option key={`${item.provider}::${item.model}`} value={`${item.provider}::${item.model}`}>
                {item.label}
              </option>
            ))}
          </select>
        </div>
        <span className="rounded bg-muted px-2 py-1 text-xs text-muted-foreground">
          范围：{scopeLabel}
        </span>
      </div>

      <Group orientation="horizontal" className="min-h-0 flex-1" defaultLayout={{ files: 18, viewer: 50, intent: 32 }}>
        <Panel id="files" minSize="220px" maxSize="360px" className="bg-background">
          <div className="flex h-full min-h-0 flex-col">
            <div className="border-b p-3">
              <div
                {...dropzone.getRootProps()}
                className={cn(
                  "cursor-pointer rounded-lg border border-dashed p-4 text-center transition-colors",
                  dropzone.isDragActive ? "border-primary bg-primary/5" : "hover:border-primary/60",
                )}
              >
                <input {...dropzone.getInputProps()} />
                <UploadCloud className="mx-auto mb-2 h-6 w-6 text-primary" />
                <p className="text-sm font-medium">拖入合同、订单或图片</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  支持 PDF、DOCX、ZIP、PNG/JPG/WEBP，可多文件统一处理
                </p>
              </div>
              {uploadError && (
                <p
                  role="alert"
                  data-testid="upload-error"
                  className="mt-2 rounded-md border border-destructive/30 bg-destructive/5 px-2 py-1.5 text-xs text-destructive"
                >
                  {uploadError}
                </p>
              )}
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-2">
              {restoring || unitsQuery.isLoading ? (
                <div className="flex items-center justify-center gap-2 px-3 py-8 text-xs text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  正在恢复上次任务
                </div>
              ) : units.length === 0 && pendingDocuments.length === 0 ? (
                <div className="px-3 py-8 text-center text-xs text-muted-foreground">
                  上传后将在这里显示文件和解析状态
                </div>
              ) : (
                <>
                  {displayedUnits.map((unit) => {
                    const isSet = unit.unit_type === "file_set";
                    const expanded = expandedUnitIds.includes(unit.unit_id);
                    const singleDocument = documentForUpload(unit.upload_ids[0]);
                    return (
                      <div key={unit.unit_id} className="mb-1">
                        <div
                          role="button"
                          tabIndex={0}
                          aria-label={`${isSet ? "打开文件集" : "打开独立任务"} ${unit.name}`}
                          data-testid={`document-unit-${unit.unit_id}`}
                          onClick={() => selectUnit(unit)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              selectUnit(unit);
                            }
                          }}
                          className={cn(
                            "flex w-full items-center gap-2 rounded-md px-2 py-2 text-left",
                            activeUnitId === unit.unit_id
                              ? "bg-primary/10 text-primary"
                              : "hover:bg-muted",
                          )}
                        >
                          {!isSet && singleDocument ? (
                            <input
                              type="checkbox"
                              aria-label={`选择 ${singleDocument.file.name} 用于组合文件集`}
                              checked={checkedIds.includes(singleDocument.id)}
                              disabled={singleDocument.status !== "ready" || intentBusy}
                              onClick={(event) => event.stopPropagation()}
                              onChange={(event) => toggleDocumentScope(
                                singleDocument.id,
                                event.target.checked,
                              )}
                              className="h-3.5 w-3.5 shrink-0 accent-primary"
                            />
                          ) : (
                            <button
                              type="button"
                              aria-label={expanded ? `收起文件集 ${unit.name}` : `展开文件集 ${unit.name}`}
                              onClick={(event) => {
                                event.stopPropagation();
                                setExpandedUnitIds((current) => (
                                  expanded
                                    ? current.filter((id) => id !== unit.unit_id)
                                    : [...current, unit.unit_id]
                                ));
                              }}
                              className="rounded p-0.5 hover:bg-muted"
                            >
                              {expanded
                                ? <ChevronUp className="h-3.5 w-3.5" />
                                : <ChevronDown className="h-3.5 w-3.5" />}
                            </button>
                          )}
                          {isSet
                            ? expanded
                              ? <FolderOpen className="h-4 w-4 shrink-0" />
                              : <Folder className="h-4 w-4 shrink-0" />
                            : <FileText className="h-4 w-4 shrink-0" />}
                          <span className="min-w-0 flex-1 truncate text-xs">{unit.name}</span>
                          {isSet && (
                            <span className="text-[10px] text-muted-foreground">
                              {unit.upload_ids.length}份
                            </span>
                          )}
                          {unit.latest_task && (
                            <Check className="h-3.5 w-3.5 text-emerald-600" />
                          )}
                          <button
                            type="button"
                            aria-label={`从工作区移除 ${unit.name}`}
                            title="从工作区移除（保留原文件和历史结果）"
                            disabled={intentBusy}
                            onClick={(event) => {
                              event.stopPropagation();
                              removeUnitFromWorkspace(unit);
                            }}
                            className="rounded p-1 text-muted-foreground hover:bg-destructive/10 hover:text-destructive disabled:opacity-40"
                          >
                            <Trash2 className="h-3.5 w-3.5" />
                          </button>
                        </div>
                        {isSet && expanded && (
                          <div className="ml-6 border-l pl-2">
                            {unit.members.map((member) => {
                              const item = documentForUpload(member.upload_id);
                              if (!item) return null;
                              return (
                                <button
                                  type="button"
                                  key={member.upload_id}
                                  onClick={() => (
                                    activeUnitId === unit.unit_id
                                      ? selectDocument(item.id)
                                      : selectUnit(unit, member.upload_id)
                                  )}
                                  className={cn(
                                    "flex w-full items-center gap-2 rounded px-2 py-1.5 text-left text-xs",
                                    selected?.id === item.id && activeUnitId === unit.unit_id
                                      ? "bg-muted font-medium"
                                      : "hover:bg-muted/70",
                                  )}
                                >
                                  <FileText className="h-3.5 w-3.5 shrink-0" />
                                  <span className="truncate">{member.original_name}</span>
                                </button>
                              );
                            })}
                          </div>
                        )}
                      </div>
                    );
                  })}
                  {pendingDocuments.map((item) => (
                    <div key={item.id} className="mb-1 flex items-center gap-2 rounded-md px-2 py-2 text-xs">
                      <FileText className="h-4 w-4" />
                      <span className="min-w-0 flex-1 truncate">{item.file.name}</span>
                      {item.status === "uploading" && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                      {item.status === "failed" && <span className="text-destructive">上传失败</span>}
                    </div>
                  ))}
                </>
              )}
            </div>
            <div className="border-t p-2">
              {groupedSingles.length > 0 && (
                <button
                  type="button"
                  onClick={() => setShowGroupedSingles((current) => !current)}
                  className="mb-2 w-full rounded px-2 py-1.5 text-left text-[11px] text-muted-foreground hover:bg-muted"
                >
                  {showGroupedSingles ? "隐藏" : "查看"}已归组的独立任务
                  （{groupedSingles.length}）
                </button>
              )}
              {checkedDocuments.length >= 2 && !showSetComposer && (
                <button
                  type="button"
                  onClick={() => {
                    setSetName(`文件集 ${new Date().toLocaleDateString("zh-CN")}`);
                    setShowSetComposer(true);
                  }}
                  className="flex w-full items-center justify-center gap-1.5 rounded-md border border-primary/40 px-2 py-2 text-xs text-primary hover:bg-primary/5"
                >
                  <Layers3 className="h-3.5 w-3.5" />
                  将已选 {checkedDocuments.length} 个文件组合为文件集
                </button>
              )}
              {activeUnit?.unit_type === "file_set"
                && taskId
                && checkedDocuments.length > 0
                && (
                  <button
                    type="button"
                    onClick={reviseActiveFileSet}
                    disabled={intentBusy}
                    className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-md border px-2 py-2 text-xs hover:border-primary"
                  >
                    用已选文件创建下一版本
                  </button>
                )}
              {showSetComposer && (
                <div className="space-y-2 rounded-md border bg-muted/30 p-2">
                  <input
                    aria-label="文件集名称"
                    value={setName}
                    onChange={(event) => setSetName(event.target.value)}
                    placeholder="文件集名称"
                    className="h-8 w-full rounded border bg-background px-2 text-xs"
                  />
                  <input
                    aria-label="业务类型"
                    value={setBusinessType}
                    onChange={(event) => setSetBusinessType(event.target.value)}
                    placeholder="业务类型（可选，如：客户订单）"
                    className="h-8 w-full rounded border bg-background px-2 text-xs"
                  />
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={createFileSetFromSelection}
                      disabled={intentBusy}
                      className="flex-1 rounded bg-primary px-2 py-1.5 text-xs text-primary-foreground disabled:opacity-50"
                    >
                      创建文件集
                    </button>
                    <button
                      type="button"
                      onClick={() => setShowSetComposer(false)}
                      className="rounded border px-2 py-1.5 text-xs"
                    >
                      取消
                    </button>
                  </div>
                </div>
              )}
              {checkedDocuments.length === 1 && (
                <p className="px-1 text-[11px] text-muted-foreground">
                  再选择一个文件，即可组合为文件集；勾选不会改变当前任务。
                </p>
              )}
            </div>
          </div>
        </Panel>

        <Separator className="w-1 border-x bg-border/40 transition-colors hover:bg-primary/30" />

        <Panel id="viewer" minSize="420px" className="bg-muted/40">
          <div className="flex h-full min-h-0 flex-col">
            <div className="flex h-11 shrink-0 items-center justify-center gap-2 border-b bg-background px-2">
              {selectedIsPdf ? (
                <>
                  <button type="button" aria-label="上一页" disabled={pageNumber <= 1} onClick={() => setPageNumber((n) => n - 1)} className="rounded p-1.5 hover:bg-muted disabled:opacity-30">
                    <ChevronLeft className="h-4 w-4" />
                  </button>
                  <label className="flex items-center gap-1 text-xs">
                    <span className="sr-only">页码</span>
                    <input
                      aria-label="页码"
                      type="number"
                      min={1}
                      max={pageCount || 1}
                      value={pageNumber}
                      onChange={(event) => {
                        const requested = Number(event.target.value);
                        if (Number.isFinite(requested)) {
                          setPageNumber(Math.max(1, Math.min(pageCount || 1, requested)));
                        }
                      }}
                      className="h-7 w-12 rounded border bg-background px-1 text-center"
                    />
                    <span>/ {pageCount || "—"}</span>
                  </label>
                  <button type="button" aria-label="下一页" disabled={!pageCount || pageNumber >= pageCount} onClick={() => setPageNumber((n) => n + 1)} className="rounded p-1.5 hover:bg-muted disabled:opacity-30">
                    <ChevronRight className="h-4 w-4" />
                  </button>
                  <div className="mx-2 h-5 w-px bg-border" />
                  <button
                    type="button"
                    aria-label="缩小"
                    onClick={() => {
                      setZoom(Math.max(0.5, renderedPageWidth / pageSize.width - 0.1));
                      setFitMode("custom");
                    }}
                    className="rounded p-1.5 hover:bg-muted"
                  >
                    <ZoomOut className="h-4 w-4" />
                  </button>
                  <button
                    type="button"
                    aria-pressed={fitMode === "page"}
                    onClick={() => setFitMode("page")}
                    className={cn(
                      "rounded px-2 py-1 text-xs hover:bg-muted",
                      fitMode === "page" && "bg-muted font-medium",
                    )}
                  >
                    整页
                  </button>
                  <button
                    type="button"
                    aria-pressed={fitMode === "width"}
                    onClick={() => setFitMode("width")}
                    className={cn(
                      "rounded px-2 py-1 text-xs hover:bg-muted",
                      fitMode === "width" && "bg-muted font-medium",
                    )}
                  >
                    适宽
                  </button>
                  <span className="w-10 text-center text-[11px] text-muted-foreground">
                    {Math.round(renderedPageWidth / pageSize.width * 100)}%
                  </span>
                  <button
                    type="button"
                    aria-label="放大"
                    onClick={() => {
                      setZoom(Math.min(2, renderedPageWidth / pageSize.width + 0.1));
                      setFitMode("custom");
                    }}
                    className="rounded p-1.5 hover:bg-muted"
                  >
                    <ZoomIn className="h-4 w-4" />
                  </button>
                </>
              ) : (
                <span className="text-xs text-muted-foreground">
                  {selected?.preview
                    ? `结构化预览 · ${selected.preview.elements.length} 个内容块`
                    : selected?.previewStatus === "parsing"
                      ? "正在解析 DOCX 预览…"
                      : "文件预览"}
                </span>
              )}
            </div>
            <div
              ref={previewRef}
              className="min-h-0 flex-1 overflow-auto p-4"
              data-testid="document-preview"
            >
              {!selected ? (
                <div className="flex h-full items-center justify-center text-sm text-muted-foreground">上传文件后在这里查看完整原页</div>
              ) : selectedIsPdf ? (
                <Document
                  file={selected.url}
                  onLoadSuccess={({ numPages }) => {
                    setPageCount(numPages);
                    setPageNumber((current) => Math.max(1, Math.min(numPages, current)));
                  }}
                  loading={<div className="flex h-full items-center justify-center"><Loader2 className="h-6 w-6 animate-spin" /></div>}
                  className="flex justify-center"
                >
                  <div
                    className="relative overflow-hidden rounded-sm bg-white shadow"
                    style={{ width: renderedPageWidth, height: renderedPageHeight }}
                  >
                    <Page
                      pageNumber={pageNumber}
                      width={renderedPageWidth}
                      onLoadSuccess={(page) => {
                        const viewport = page.getViewport({ scale: 1 });
                        setPageSize({ width: viewport.width, height: viewport.height });
                      }}
                    />
                    {selectedEvidence?.page === pageNumber && evidenceBoxStyle && (
                      <div
                        aria-label="证据高亮"
                        className="pointer-events-none absolute border-2 border-amber-500 bg-amber-300/25"
                        style={evidenceBoxStyle}
                      />
                    )}
                    {selectedEvidence?.page === pageNumber
                      && selectedEvidence.bbox?.coordinate_space === "image_pixels"
                      && (
                        <div className="absolute inset-x-3 top-3 rounded bg-amber-50/95 px-3 py-2 text-xs text-amber-800 shadow">
                          该证据缺少原始图像尺寸，不能安全绘制坐标框。
                        </div>
                      )}
                  </div>
                </Document>
              ) : isImage(selected.file) ? (
                <ImageEvidenceViewer
                  url={selected.url}
                  name={selected.file.name}
                  evidence={selectedEvidence}
                />
              ) : selected.previewStatus === "parsing" ? (
                <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-5 w-5 animate-spin" />
                  正在解析 DOCX，完成后将显示段落和表格
                </div>
              ) : selected.preview ? (
                <article
                  aria-label={`${selected.file.name}结构化预览`}
                  className="mx-auto min-h-full max-w-4xl rounded-sm bg-white px-12 py-10 shadow"
                >
                  <header className="mb-8 border-b pb-4">
                    <h2 className="text-lg font-semibold text-slate-900">{selected.file.name}</h2>
                    <p className="mt-1 text-xs text-slate-500">
                      由 python-docx 解析；点击抽取结果可定位到对应内容块
                    </p>
                  </header>
                  <div className="space-y-3">
                    {selected.preview.elements.map((element) => {
                      const active = selectedEvidence?.element_id === element.element_id;
                      const location = element.metadata.location;
                      return (
                        <section
                          key={element.element_id}
                          data-element-id={element.element_id}
                          className={cn(
                            "scroll-m-20 rounded border border-transparent px-3 py-2 text-sm leading-7 text-slate-800 transition-colors",
                            element.element_type === "table" && "bg-slate-50 font-mono text-xs",
                            active && "border-amber-500 bg-amber-100 ring-2 ring-amber-300/60",
                          )}
                        >
                          <span className="mr-2 select-none text-[10px] text-slate-400">
                            {location?.kind === "docx_table_row"
                              ? `表${location.table} 行${location.row}`
                              : `段落${location?.paragraph ?? "—"}`}
                          </span>
                          {element.text || "（空内容）"}
                        </section>
                      );
                    })}
                  </div>
                </article>
              ) : selected.previewStatus === "failed" ? (
                <div className="flex h-full items-center justify-center px-8 text-center text-sm text-destructive">
                  DOCX 预览解析失败：{selected.previewError || "未知错误"}
                </div>
              ) : (
                <div className="flex h-full items-center justify-center text-sm text-muted-foreground">当前格式暂不支持结构化预览</div>
              )}
            </div>
          </div>
        </Panel>

        <Separator className="w-1 border-x bg-border/40 transition-colors hover:bg-primary/30" />

        <Panel id="intent" minSize="320px" maxSize="520px" className="bg-background">
          <div className="flex h-full min-h-0 flex-col">
            <div className="flex items-center gap-2 border-b px-4 py-3">
              <MessageSquareText className="h-4 w-4 text-primary" />
              <div>
                <h2 className="text-sm font-medium">告诉我你想得到什么</h2>
                <p className="text-xs text-muted-foreground">AI 先生成方案，确认后才执行</p>
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              <div className="rounded-lg bg-muted p-3 text-sm">
                我可以从这些文件中提取订单明细、付款条款、交付条件等。请描述你的目标。
              </div>
              {messages.map((message, index) => (
                <div key={`${message}-${index}`} className="ml-8 mt-3 rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">
                  {message}
                </div>
              ))}
              {intentBusy && (
                <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  {selectedModelLabel || "AI"} 正在处理文档任务…
                </div>
              )}
              {intentError && (
                <div className="mt-3 rounded-md border border-red-300 bg-red-50 p-2 text-xs text-red-700">
                  {intentError}
                </div>
              )}
              {runHistory.length > 0 && (
                <label className="mt-4 block text-xs text-muted-foreground">
                  历史版本
                  <select
                    aria-label="历史版本"
                    value={taskId}
                    disabled={intentBusy}
                    onChange={async (event) => {
                      const version = runHistory.find(
                        (item) => item.task_id === event.target.value,
                      );
                      if (!version) return;
                      setIntentBusy(true);
                      setIntentError("");
                      try {
                        await loadTaskView(version);
                        await updateDocumentWorkspace({
                          upload_ids: allWorkspaceUploadIds(),
                          checked_upload_ids: checkedDocuments.flatMap((item) => (
                            item.upload ? [item.upload.upload_id] : []
                          )),
                          active_task_id: version.task_id,
                          active_unit_id: activeUnitId || null,
                          selected_upload_id: selected?.upload?.upload_id ?? null,
                        });
                      } catch (error) {
                        setIntentError(
                          error instanceof Error ? error.message : "历史版本读取失败",
                        );
                      } finally {
                        setIntentBusy(false);
                      }
                    }}
                    className="mt-1 h-8 w-full rounded border bg-background px-2 text-xs text-foreground"
                  >
                    {runHistory.map((task) => {
                      const spec = documentTaskSpec(task);
                      const revision = Number(
                        (task.spec as { revision?: number }).revision ?? 1,
                      );
                      return (
                        <option key={task.task_id} value={task.task_id}>
                          V{revision} · {task.status} · {
                            spec.intent_messages?.[
                              Math.max(0, (spec.intent_messages?.length ?? 1) - 1)
                            ] ?? "文档抽取"
                          }
                        </option>
                      );
                    })}
                  </select>
                </label>
              )}
              {draftSpec ? (
                <>
                  <div className="mt-5 flex items-center justify-between">
                    <div className="flex items-center gap-2 text-sm font-medium">
                      <Sparkles className="h-4 w-4 text-primary" />
                      AI 生成的抽取方案
                    </div>
                    <span className="text-xs text-muted-foreground">{fields.length} 个字段</span>
                  </div>
                  <div className="mt-2 rounded-lg border border-primary/20 bg-primary/5 p-3 text-xs">
                    <div className="font-medium text-foreground">
                      结果形态：{resultShapeLabel(draftSpec)} · {resultCardinalityLabel(draftSpec)}
                    </div>
                    <p className="mt-1 text-muted-foreground">
                      每行代表：{draftSpec.result_contract.record_grain || "一个抽取结果"}
                    </p>
                    <p className="mt-1 text-muted-foreground">
                      {draftSpec.result_contract.exhaustive
                        ? "系统会扫描全部内容，完成后统一提交低置信度或冲突项复核。"
                        : "系统按目标字段提取，并为结果保留原文证据。"}
                    </p>
                  </div>
                  <div className="mt-2 space-y-2">
                    {fields.map((field) => (
                      <div key={field.id} className="rounded-lg border p-3">
                        <div className="flex items-center gap-2">
                          <input
                            value={field.name}
                            onChange={(event) => updateField(field.id, { name: event.target.value })}
                            aria-label="字段名称"
                            className="min-w-0 flex-1 bg-transparent text-sm font-medium outline-none"
                          />
                          <button type="button" aria-label={`删除字段 ${field.name}`} onClick={() => setFields((current) => current.filter((item) => item.id !== field.id))} className="rounded p-1 hover:bg-muted">
                            <X className="h-3.5 w-3.5" />
                          </button>
                        </div>
                        <input
                          value={field.description}
                          onChange={(event) => updateField(field.id, { description: event.target.value })}
                          aria-label={`${field.name}字段说明`}
                          className="mt-1 w-full bg-transparent text-xs text-muted-foreground outline-none"
                        />
                      </div>
                    ))}
                  </div>
                  <button
                    type="button"
                    onClick={() => setFields((current) => [...current, {
                      id: nanoid(), name: "新字段", dtype: "string", required: false, description: "请填写字段含义",
                    }])}
                    className="mt-2 flex w-full items-center justify-center gap-1 rounded-md border border-dashed py-2 text-xs text-muted-foreground hover:border-primary hover:text-primary"
                  >
                    <Plus className="h-3.5 w-3.5" /> 添加字段
                  </button>
                </>
              ) : (
                <div className="mt-5 rounded-lg border border-dashed p-4 text-xs leading-5 text-muted-foreground">
                  上传完成后，请在右下方描述你要的数据，例如“只提取付款节点、比例和收款账户”。AI 会据此生成字段，不会预设你一定要抽取什么。
                </div>
              )}
              {result && (
                <div className="mt-5">
                  <div className="flex items-center justify-between">
                    <h3 className="text-sm font-medium">抽取结果</h3>
                    <span className={cn(
                      "rounded px-2 py-0.5 text-xs",
                      result.status === "COMPLETED"
                        ? "bg-emerald-100 text-emerald-700"
                        : result.status === "FAILED"
                          ? "bg-red-100 text-red-700"
                          : "bg-amber-100 text-amber-700",
                    )}>
                      {result.status === "COMPLETED"
                        ? "已完成"
                        : result.status === "FAILED"
                          ? "未产出有效数据"
                          : `${pendingReviewCount} 项待复核`}
                    </span>
                  </div>
                  {!hasAuthoritativeData && (
                    <div className="mt-2 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs leading-5 text-red-700">
                      当前版本没有可交付的数据。系统已阻止下载空的权威结果和 XLSX，
                      请先查看质量报告，修复解析问题后重新执行。
                    </div>
                  )}
                  {result.coverage && (
                    <div className="mt-2 rounded border bg-muted/40 px-3 py-2 text-[11px] text-muted-foreground">
                      已扫描 {result.coverage.elements_processed ?? 0}/
                      {result.coverage.elements_in_scope ?? result.coverage.elements_total ?? 0} 个内容块
                      {typeof result.coverage.records_extracted === "number"
                        ? `，抽取 ${result.coverage.records_extracted} 条记录`
                        : ""}
                      {typeof result.coverage.table_rows === "number"
                        ? `，保留 ${result.coverage.table_rows} 行表格`
                        : ""}
                      {typeof result.coverage.document_chars === "number"
                        ? `，生成 ${result.coverage.document_chars} 字连续正文`
                        : ""}
                    </div>
                  )}
                  {result.table_recipe && (
                    <div className="mt-2 rounded border border-blue-200 bg-blue-50 px-3 py-2 text-[11px] leading-5 text-blue-800">
                      系统已自动执行可回放的表格归一 Recipe；原始表未覆盖，
                      可在产物中用 extracted_tables_raw.json 和 table_recipe_audit.json 审计恢复。
                    </div>
                  )}
                  {visibleRecords.length > 0 && (
                    <div className="mt-2 overflow-x-auto rounded-lg border">
                      <table className="min-w-full border-collapse text-xs">
                        <thead className="bg-muted/70">
                          <tr>
                            <th className="whitespace-nowrap border-b px-2 py-2 text-left">序号</th>
                            {recordColumns.map((column) => (
                              <th key={column} className="whitespace-nowrap border-b px-2 py-2 text-left">
                                {column}
                              </th>
                            ))}
                            <th className="whitespace-nowrap border-b px-2 py-2 text-left">状态</th>
                          </tr>
                        </thead>
                        <tbody>
                          {visibleRecords.map((record, index) => (
                            <tr
                              key={record.record_id}
                              className="cursor-pointer border-b last:border-b-0 hover:bg-primary/5"
                              onClick={() => {
                                const evidence = record.fields
                                  .flatMap((field) => field.evidence_refs)[0];
                                if (evidence) focusEvidence(evidence);
                              }}
                            >
                              <td className="whitespace-nowrap px-2 py-2 text-muted-foreground">{index + 1}</td>
                              {recordColumns.map((column) => (
                                <td key={column} className="max-w-56 whitespace-pre-wrap px-2 py-2 align-top">
                                  {record.values[column] == null
                                    ? "—"
                                    : String(record.values[column])}
                                </td>
                              ))}
                              <td className="whitespace-nowrap px-2 py-2">{record.status}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                  {visibleTables.length > 0 && (
                    <div className="mt-3 space-y-3">
                      {visibleTables.map((table) => (
                        <section key={table.table_id} className="overflow-hidden rounded-lg border">
                          <div className="bg-muted/70 px-3 py-2 text-xs font-medium">
                            {table.name} · {table.rows.length} 行
                          </div>
                          <div className="overflow-x-auto">
                            <table className="min-w-full border-collapse text-xs">
                              <thead>
                                <tr>
                                  {table.columns.map((column) => (
                                    <th key={column} className="whitespace-nowrap border-b px-2 py-2 text-left">
                                      {column}
                                    </th>
                                  ))}
                                </tr>
                              </thead>
                              <tbody>
                                {table.rows.map((row, rowIndex) => (
                                  <tr key={`${table.table_id}-${rowIndex}`} className="border-b last:border-b-0">
                                    {table.columns.map((column) => (
                                      <td key={column} className="max-w-56 whitespace-pre-wrap px-2 py-2 align-top">
                                        {row[column] == null ? "—" : String(row[column])}
                                      </td>
                                    ))}
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        </section>
                      ))}
                    </div>
                  )}
                  {visibleDocuments.length > 0 && (
                    <div className="mt-3 space-y-3">
                      {visibleDocuments.map((document) => (
                        <article key={document.document_id} className="rounded-lg border p-3">
                          <div className="flex items-center justify-between gap-2">
                            <h4 className="text-xs font-medium">{document.title}</h4>
                            <button
                              type="button"
                              className="text-[11px] text-primary hover:underline"
                              onClick={() => {
                                const evidence = document.evidence_refs[0];
                                if (evidence) focusEvidence(evidence);
                              }}
                            >
                              {document.evidence_refs.length} 个证据片段
                            </button>
                          </div>
                          <div className="mt-2 max-h-96 overflow-y-auto whitespace-pre-wrap text-xs leading-6">
                            {document.content}
                          </div>
                        </article>
                      ))}
                    </div>
                  )}
                  {visibleAggregates.length > 0 && (
                    <div className="mt-3 grid gap-2">
                      {visibleAggregates.flatMap((aggregate) => (
                        aggregate.fields.map((field) => (
                          <button
                            key={`${aggregate.aggregate_id}-${field.name}`}
                            type="button"
                            className="rounded-lg border p-3 text-left hover:border-primary"
                            onClick={() => {
                              const evidence = field.evidence_refs[0];
                              if (evidence) focusEvidence(evidence);
                            }}
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-xs font-medium">{field.name}</span>
                              <span className="text-[11px] text-muted-foreground">{field.status}</span>
                            </div>
                            <div className="mt-1 text-lg font-semibold">
                              {field.value == null ? "—" : String(field.value)}
                            </div>
                          </button>
                        ))
                      ))}
                    </div>
                  )}
                  {visibleRecords.length === 0
                    && visibleTables.length === 0
                    && visibleDocuments.length === 0
                    && visibleAggregates.length === 0 && (
                    <div className="mt-2 space-y-2">
                      {visibleFields.map((field) => (
                        <button
                          key={field.name}
                          type="button"
                          onClick={() => {
                            const evidence = field.evidence_refs[0];
                            if (evidence) focusEvidence(evidence);
                          }}
                          className="w-full rounded-lg border p-3 text-left hover:border-primary"
                        >
                          <div className="flex items-center justify-between gap-2">
                            <span className="text-xs font-medium">{field.name}</span>
                            <span className="text-[11px] text-muted-foreground">{field.status}</span>
                          </div>
                          <div className="mt-1 truncate text-sm">
                            {field.value == null ? "—" : String(field.value)}
                          </div>
                          {field.evidence_refs[0]?.quote && (
                            <div className="mt-1 line-clamp-2 text-[11px] text-muted-foreground">
                              证据：{field.evidence_refs[0].quote}
                            </div>
                          )}
                        </button>
                      ))}
                    </div>
                  )}
                  <div className="mt-3 grid grid-cols-2 gap-2">
                    <button
                      type="button"
                      disabled={!hasAuthoritativeData}
                      title={!hasAuthoritativeData ? "当前没有可下载的有效数据" : undefined}
                      onClick={() => downloadArtifact(
                        result.records?.length
                          ? "extraction/extracted_records.jsonl"
                          : result.tables?.length
                            ? "extraction/extracted_tables.json"
                            : result.documents?.length
                              ? "extraction/extracted_documents.json"
                              : result.aggregates?.length
                                ? "extraction/extracted_aggregates.json"
                                : "extraction/extracted_fields.jsonl",
                        result.records?.length
                          ? "extracted_records.jsonl"
                          : result.tables?.length
                            ? "extracted_tables.json"
                            : result.documents?.length
                              ? "extracted_documents.json"
                              : result.aggregates?.length
                                ? "extracted_aggregates.json"
                                : "extracted_fields.jsonl",
                      )}
                      className="rounded border px-2 py-1.5 text-[11px] hover:border-primary disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      下载权威结果 JSON/JSONL
                    </button>
                    <button
                      type="button"
                      disabled={!hasAuthoritativeData}
                      title={!hasAuthoritativeData ? "当前没有可下载的有效数据" : undefined}
                      onClick={() => downloadArtifact(
                        "extraction/evidence.jsonl",
                        "evidence.jsonl",
                      )}
                      className="rounded border px-2 py-1.5 text-[11px] hover:border-primary disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      下载证据 JSONL
                    </button>
                    <button
                      type="button"
                      onClick={() => downloadArtifact(
                        "quality_report.json",
                        "quality_report.json",
                      )}
                      className="rounded border px-2 py-1.5 text-[11px] hover:border-primary"
                    >
                      下载质量报告
                    </button>
                    <button
                      type="button"
                      disabled={!hasAuthoritativeData}
                      title={!hasAuthoritativeData ? "当前没有可下载的有效数据" : undefined}
                      onClick={() => downloadArtifact(
                        "extraction/document_extraction.xlsx",
                        "document_extraction.xlsx",
                      )}
                      className="rounded border px-2 py-1.5 text-[11px] hover:border-primary disabled:cursor-not-allowed disabled:opacity-40"
                    >
                      下载 XLSX 查看副本
                    </button>
                    <button
                      type="button"
                      onClick={() => downloadArtifact("manifest.json", "manifest.json")}
                      className="rounded border px-2 py-1.5 text-[11px] hover:border-primary"
                    >
                      下载 Manifest
                    </button>
                  </div>
                  {result.review_tasks.length > 0 && (
                    <div className="mt-4 space-y-3" data-testid="review-panel">
                      <h3 className="text-sm font-medium">
                        人工复核
                        {pendingReviewCount > 0 && (
                          <span className="ml-2 text-xs font-normal text-amber-700">
                            {pendingReviewCount} 项待处理
                          </span>
                        )}
                      </h3>
                      {result.review_tasks.map((review) => {
                        const extracted = review.record_id
                          ? result.records
                            ?.find((record) => record.record_id === review.record_id)
                            ?.fields.find((field) => field.name === review.field_name)
                          : result.fields.find(
                            (field) => field.name === review.field_name,
                          );
                        const suggested = candidateValue(review.candidates[0])
                          ?? extracted?.value
                          ?? "";
                        const reviewValue = reviewValues[review.task_id]
                          ?? String(suggested);
                        return (
                          <div key={review.task_id} className="rounded-lg border border-amber-200 bg-amber-50/60 p-3">
                            <div className="flex items-center justify-between gap-2">
                              <span className="text-xs font-medium">{review.field_name}</span>
                              <span className="text-[11px] text-amber-700">
                                {review.status === "pending" ? "待复核" : "已处理"}
                              </span>
                            </div>
                            <p className="mt-1 text-[11px] text-muted-foreground">
                              {review.reasons.join("；")}
                            </p>
                            {review.status === "pending" ? (
                              <>
                                <input
                                  aria-label={`${review.field_name}人工修订值`}
                                  value={reviewValue}
                                  onChange={(event) => setReviewValues((current) => ({
                                    ...current,
                                    [review.task_id]: event.target.value,
                                  }))}
                                  className="mt-2 w-full rounded border bg-background px-2 py-1.5 text-xs outline-none focus:border-primary"
                                />
                                <div className="mt-2 grid grid-cols-3 gap-1">
                                  <button
                                    type="button"
                                    disabled={review.candidates.length === 0 || intentBusy}
                                    onClick={() => resolveReview(review, "accept_candidate")}
                                    className="rounded border bg-background px-2 py-1.5 text-[11px] hover:border-primary disabled:opacity-40"
                                  >
                                    接受候选
                                  </button>
                                  <button
                                    type="button"
                                    disabled={!reviewValue.trim() || intentBusy}
                                    onClick={() => resolveReview(review, "replace")}
                                    className="rounded border bg-background px-2 py-1.5 text-[11px] hover:border-primary disabled:opacity-40"
                                  >
                                    使用修订值
                                  </button>
                                  <button
                                    type="button"
                                    disabled={intentBusy}
                                    onClick={() => resolveReview(review, "mark_not_found")}
                                    className="rounded border bg-background px-2 py-1.5 text-[11px] hover:border-primary disabled:opacity-40"
                                  >
                                    标记未找到
                                  </button>
                                </div>
                              </>
                            ) : (
                              <p className="mt-2 text-xs text-emerald-700">
                                裁决已保存并写入审计记录
                              </p>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
            </div>
            <div className="shrink-0 space-y-3 border-t p-3">
              <div className="flex gap-2">
                <textarea
                  value={intent}
                  onChange={(event) => setIntent(event.target.value)}
                  disabled={restoring || intentBusy}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      submitIntent();
                    }
                  }}
                  placeholder="例如：只提取付款节点、比例和收款账户"
                  rows={2}
                  className="min-h-16 min-w-0 flex-1 resize-none rounded-md border bg-background px-3 py-2 text-sm outline-none focus:border-primary"
                />
                <button type="button" aria-label="发送需求" onClick={submitIntent} disabled={!intent.trim() || restoring || intentBusy || uploadsPending || !activeUnit} className="self-end rounded-md bg-primary p-2 text-primary-foreground disabled:opacity-40">
                  <Send className="h-4 w-4" />
                </button>
              </div>
              <button
                type="button"
                disabled={intentBusy || Boolean(result) || !activeUnit || messages.length === 0 || !canExecuteDraft}
                onClick={confirmSpec}
                className="w-full rounded-md bg-primary py-2 text-sm font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-40"
              >
                {intentBusy
                  ? "处理中…"
                  : result
                    ? result.status === "COMPLETED"
                      ? "抽取已完成"
                      : result.status === "FAILED"
                        ? "本次抽取未产出有效数据"
                        : `请完成 ${pendingReviewCount} 项人工复核`
                    : confirmed ? "方案已确认" : "确认并开始抽取"}
              </button>
              <p className="text-center text-[11px] text-muted-foreground">
                {primaryHint}
              </p>
            </div>
          </div>
        </Panel>
      </Group>
    </div>
  );
}
