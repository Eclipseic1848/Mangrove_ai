/**
 * 数据准备 API 客户端（Phase 2 Task 11）。
 * 复用 lib/api 的鉴权与错误处理；上传走 multipart（不用 JSON Content-Type）。
 */
import { api, ApiError, getToken } from "./api";
import type {
  DataTaskPreview,
  DataTask,
  DatasetManifest,
  DocumentPreview,
  DocumentDraft,
  DocumentExtractionResult,
  DocumentModelSelection,
  DocumentTaskUnit,
  ExtractionSpec,
  DocumentWorkspace,
  UploadItem,
} from "@/types/dataPrep";

export type DataTaskSource =
  | { source_type: "upload_file"; upload_id: string }
  | {
      source_type: "http_api";
      url: string;
      pagination?: {
        strategy: "page";
        options: { page_param: string; per_page_param: string; per_page: number; max_pages: number };
      };
    }
  | {
      source_type: "database";
      connection_id: string;
      table?: string;
      fields?: string[];
      filters?: { field: string; op: string; value: unknown }[];
      time_range?: { field: string; start: string; end: string };
      incremental?: { strategy: string; cursor_field: string; last_value?: string };
    };

/** 上传文件（multipart），返回 upload_id。 */
export async function uploadFile(file: File): Promise<UploadItem> {
  const body = new FormData();
  body.append("file", file);
  const t = getToken();
  const res = await fetch("/api/data-sources/uploads", {
    method: "POST",
    headers: t ? { Authorization: `Bearer ${t}` } : {},
    body,
  });
  if (!res.ok) {
    let detail = `上传失败（${res.status}）`;
    try {
      detail = (await res.json()).detail || detail;
    } catch {
      /* ignore */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json();
}

/** 使用浏览器原生上传进度事件，不引入第二套上传框架。 */
export function uploadFileWithProgress(
  file: File,
  onProgress: (percent: number) => void,
): Promise<UploadItem> {
  return new Promise((resolve, reject) => {
    const body = new FormData();
    body.append("file", file);
    const request = new XMLHttpRequest();
    request.open("POST", "/api/data-sources/uploads");
    const token = getToken();
    if (token) request.setRequestHeader("Authorization", `Bearer ${token}`);
    request.upload.addEventListener("progress", (event) => {
      if (!event.lengthComputable) return;
      onProgress(Math.round((event.loaded / event.total) * 100));
    });
    request.addEventListener("load", () => {
      if (request.status < 200 || request.status >= 300) {
        try {
          const payload = JSON.parse(request.responseText);
          reject(new Error(payload.detail || "上传失败"));
        } catch {
          reject(new Error(`上传失败（${request.status}）`));
        }
        return;
      }
      onProgress(100);
      resolve(JSON.parse(request.responseText) as UploadItem);
    });
    request.addEventListener("error", () => reject(new Error("上传连接中断")));
    request.send(body);
  });
}

/** 读取上传元数据。 */
export function getUpload(uploadId: string): Promise<UploadItem> {
  return api.get(`/api/data-sources/uploads/${uploadId}`);
}

/** 获取 DOCX 的结构化预览，元素标识与后续抽取证据保持一致。 */
export function getDocumentPreview(uploadId: string): Promise<DocumentPreview> {
  return api.get(`/api/data-sources/uploads/${uploadId}/document-preview`);
}

/** 下载用户自己的上传原件，供页面刷新后恢复本地预览。 */
export async function getUploadFile(item: UploadItem): Promise<File> {
  const t = getToken();
  const res = await fetch(`/api/data-sources/uploads/${item.upload_id}/content`, {
    headers: t ? { Authorization: `Bearer ${t}` } : {},
  });
  if (!res.ok) {
    throw new ApiError(res.status, "上传原件读取失败");
  }
  const blob = await res.blob();
  return new File([blob], item.original_name, {
    type: item.media_type || blob.type,
  });
}

/** 测试连接可达性（不拉全量）。 */
export function testConnection(sourceType: string, payload: Record<string, unknown>) {
  return api.post("/api/data-sources/connections/test", {
    source_type: sourceType,
    ...payload,
  });
}

/** 数据库连接 CRUD（Phase 3）。 */
export type DbConnection = {
  connection_id: string;
  user_id: string;
  name: string;
  dialect: string;
  host: string;
  port: number;
  database_name: string;
  username: string;
  sqlite_relpath: string;
  created_at: string;
  updated_at: string;
};

export function listDbConnections(): Promise<DbConnection[]> {
  return api.get("/api/data-sources/connections");
}

export function createDbConnection(payload: {
  name: string;
  dialect: string;
  host?: string;
  port?: number;
  database_name?: string;
  username?: string;
  password?: string;
  sqlite_relpath?: string;
}): Promise<DbConnection> {
  return api.post("/api/data-sources/connections", payload);
}

export function deleteDbConnection(connectionId: string): Promise<{ ok: boolean }> {
  return api.del(`/api/data-sources/connections/${connectionId}`);
}

export function testDbConnection(payload: {
  connection_id?: string;
  dialect?: string;
  host?: string;
  port?: number;
  database_name?: string;
  username?: string;
  password?: string;
  sqlite_relpath?: string;
}) {
  return api.post("/api/data-sources/connections/test", payload);
}

export function getDbSchema(connectionId: string, schema?: string) {
  const params = schema ? `?schema=${encodeURIComponent(schema)}` : "";
  return api.get(`/api/data-sources/connections/${connectionId}/schema${params}`);
}

/** 预览小样本 + Schema 推断 + Recipe 影响。 */
export function previewTask(source: DataTaskSource, sampleRecords = 20): Promise<DataTaskPreview> {
  return api.post("/api/data-tasks/preview", {
    source,
    sample_records: sampleRecords,
  });
}

/** 创建并执行数据准备任务（同步返回最终状态）。 */
export function createTask(
  source: DataTaskSource,
  opts?: { intent?: string; outputs?: string[]; max_records?: number },
): Promise<DataTask> {
  return api.post("/api/data-tasks", { source, ...opts });
}

/** 查询任务列表（最新优先）。 */
export function listTasks(): Promise<DataTask[]> {
  return api.get("/api/data-tasks");
}

/** 查询任务状态。 */
export function getTask(taskId: string): Promise<DataTask> {
  return api.get(`/api/data-tasks/${taskId}`);
}

/** 获取 Manifest。 */
export function getManifest(taskId: string): Promise<DatasetManifest> {
  return api.get(`/api/data-tasks/${taskId}/manifest`);
}

/** 复跑任务。 */
export function rerunTask(taskId: string): Promise<DataTask> {
  return api.post(`/api/data-tasks/${taskId}/rerun`);
}

/** 根据用户目标生成可编辑的文档抽取方案，不执行抽取。 */
export function createDocumentDraft(
  unitId: string,
  intent: string,
  selection: DocumentModelSelection,
): Promise<DocumentDraft> {
  return api.post("/api/data-tasks/document-drafts", {
    unit_id: unitId,
    intent,
    ...selection,
  });
}

/** 在同一文档任务内继续对话并修订方案。 */
export function reviseDocumentDraft(
  taskId: string,
  intent: string,
  selection: DocumentModelSelection,
): Promise<DocumentDraft> {
  return api.post(`/api/data-tasks/${taskId}/intent-messages`, {
    intent,
    ...selection,
  });
}

/** 切换当前文档任务使用的模型，供本地超时后显式改用云端重试。 */
export function updateDocumentModelSelection(
  taskId: string,
  selection: DocumentModelSelection,
): Promise<{ task_id: string; status: string; model_selection: DocumentModelSelection }> {
  return api.put(`/api/data-tasks/${taskId}/model-selection`, selection);
}

/** 保存用户确认或修改后的 ExtractionSpec。 */
export function updateDocumentExtractionSpec(
  taskId: string,
  extractionSpec: ExtractionSpec,
): Promise<DocumentDraft> {
  return api.put(`/api/data-tasks/${taskId}/extraction-spec`, extractionSpec);
}

/** 确认方案后执行文档解析和证据约束抽取。 */
export function executeDocumentExtraction(
  taskId: string,
): Promise<DocumentExtractionResult> {
  return api.post(`/api/data-tasks/${taskId}/extract`);
}

export function getDocumentExtractionResults(
  taskId: string,
): Promise<DocumentExtractionResult> {
  return api.get(`/api/data-tasks/${taskId}/extraction-results`);
}

/** 当前文档工作区范围，独立于不可变历史任务。 */
export function getDocumentWorkspace(): Promise<DocumentWorkspace> {
  return api.get("/api/data-tasks/document-workspace");
}

export function updateDocumentWorkspace(
  workspace: Pick<
    DocumentWorkspace,
    "upload_ids" | "checked_upload_ids" | "active_task_id" | "active_unit_id" | "selected_upload_id"
  >,
): Promise<DocumentWorkspace> {
  return api.put("/api/data-tasks/document-workspace", workspace);
}

/** 创建一个明确的独立文件任务或文件集。 */
export function createDocumentUnit(payload: {
  unit_type: "single_file" | "file_set";
  name: string;
  business_type?: string;
  upload_ids: string[];
}): Promise<DocumentTaskUnit> {
  return api.post("/api/data-tasks/document-units", payload);
}

/** 获取当前用户的任务单元；独立文件与文件集互不串历史。 */
export function listDocumentUnits(signal?: AbortSignal): Promise<DocumentTaskUnit[]> {
  return api.get("/api/data-tasks/document-units", { signal });
}

/** 获取指定任务单元自己的不可变版本历史。 */
export function listDocumentUnitRuns(
  unitId: string,
  signal?: AbortSignal,
): Promise<DataTask[]> {
  return api.get(`/api/data-tasks/document-units/${unitId}/runs`, { signal });
}

/** 仅从工作区移除任务单元；后端保留原文件和历史结果。 */
export function archiveDocumentUnit(unitId: string): Promise<{
  ok: boolean;
  unit_id: string;
  retained_uploads: boolean;
  retained_history: boolean;
}> {
  return api.del(`/api/data-tasks/document-units/${unitId}`);
}

/** 某个上传文件参与过的执行版本，最新优先。 */
export function listDocumentRunsByUpload(uploadId: string): Promise<DataTask[]> {
  return api.get(`/api/data-tasks/document-runs/by-upload/${uploadId}`);
}

/** 文件范围变化时从已确认方案创建新版本。 */
export function createDocumentScopeRevision(
  taskId: string,
  uploadIds: string[],
): Promise<DocumentDraft & {
  parent_task_id: string;
  revision: number;
  workspace: DocumentWorkspace;
}> {
  return api.post(`/api/data-tasks/${taskId}/scope-revisions`, {
    upload_ids: uploadIds,
  });
}

export function submitDocumentReview(
  taskId: string,
  reviewTaskId: string,
  payload: {
    decision: "accept_candidate" | "replace" | "mark_not_found";
    candidate_index?: number;
    value?: unknown;
    note?: string;
  },
): Promise<DocumentExtractionResult> {
  return api.post(
    `/api/data-tasks/${taskId}/review-decisions/${reviewTaskId}`,
    payload,
  );
}
