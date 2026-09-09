import { fetchEventSource } from "@microsoft/fetch-event-source";
import { api, downloadFile, authenticatedFetch, getAuthGeneration, revalidateStreamSession, ApiError } from "@/lib/api";
import type {
  WorkspaceEvent,
  WorkspaceMessage,
  WorkspaceGuidance,
  WorkspacePreview,
  WorkspaceStorage,
  WorkspaceTask,
  WorkspaceRevision,
  SteeringResult,
  DeliveryManifest,
  VerificationAttemptReceipt,
  HistoricalAuthorityRecoveryConfirmation,
  LegacyRebaselineConfirmation,
  SourceAcquisitionAttempt,
  ResultSelection,
  PublicResultContext,
  WorkspaceSourcePreview,
} from "@/types/semanticWorkspace";

const BASE = "/api/semantic-workspace";

/** 只接收公开引用身份；未知内部字段不能随消息进入画布。 */
export function readPublicResultContext(value: unknown): PublicResultContext | null {
  if (!value || typeof value !== "object") return null;
  const data = value as Record<string, unknown>;
  if (!Number.isSafeInteger(data.revision) || Number(data.revision) < 1
    || typeof data.output_id !== "string" || !data.output_id
    || typeof data.representation_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(data.representation_sha256)
    || typeof data.item_ref !== "string" || !/^item_[0-9a-f]{64}$/.test(data.item_ref)
    || typeof data.label !== "string" || !Array.isArray(data.source_refs)) return null;
  const refs: PublicResultContext["source_refs"] = [];
  for (const value of data.source_refs) {
    if (!value || typeof value !== "object") return null;
    const ref = value as Record<string, unknown>;
    if (typeof ref.artifact_id !== "string" || !ref.artifact_id || typeof ref.source_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(ref.source_sha256)) return null;
    const source: PublicResultContext["source_refs"][number] = { artifact_id: ref.artifact_id, source_sha256: ref.source_sha256 };
    for (const key of ["snapshot_id", "table_ref", "element_id", "extractor", "extractor_version", "read_at"] as const) {
      if (typeof ref[key] === "string") source[key] = ref[key];
    }
    for (const key of ["row_number", "page"] as const) {
      if (Number.isSafeInteger(ref[key]) && Number(ref[key]) > 0) source[key] = Number(ref[key]);
    }
    const box = ref.bbox as Record<string, unknown> | undefined;
    if (box && [box.x0, box.y0, box.x1, box.y1].every(value => typeof value === "number" && Number.isFinite(value))
      && Number(box.x1) > Number(box.x0) && Number(box.y1) > Number(box.y0)
      && ["pdf_points", "image_pixels", "normalized_1000"].includes(String(box.coordinate_space))) {
      source.bbox = { x0: Number(box.x0), y0: Number(box.y0), x1: Number(box.x1), y1: Number(box.y1), coordinate_space: box.coordinate_space as NonNullable<typeof source.bbox>["coordinate_space"] };
    }
    const location = ref.location as Record<string, unknown> | undefined;
    if (location?.kind === "docx_paragraph" && Number.isSafeInteger(location.paragraph) && Number(location.paragraph) >= 1) source.location = { kind: "docx_paragraph", paragraph: Number(location.paragraph) };
    if (location?.kind === "docx_table_row" && Number.isSafeInteger(location.table) && Number(location.table) >= 1 && Number.isSafeInteger(location.row) && Number(location.row) >= 0) source.location = { kind: "docx_table_row", table: Number(location.table), row: Number(location.row) };
    if (location?.kind === "text_line" && Number.isSafeInteger(location.line) && Number(location.line) >= 1) source.location = { kind: "text_line", line: Number(location.line) };
    refs.push(source);
  }
  return { revision: Number(data.revision), output_id: data.output_id, representation_sha256: data.representation_sha256, item_ref: data.item_ref, label: data.label, source_refs: refs };
}

export type TaskTemplateOption = {
  template_id: string;
  version: number;
  title: string;
  source: string;
  purpose: string;
  goal_contract_draft: string;
  delivery_spec_draft: Record<string, unknown>;
  method_draft: string;
  summary_sha256: string;
};

export type OwnerMemoryOption = {
  memory_id: number;
  purpose: string;
  source: string;
  summary: string;
  summary_sha256: string;
};

export type TaskContextSelection = {
  template?: { template_id: string; version: number } | null;
  memories: Array<{ memory_id: number }>;
};

export type TaskContextPreview = {
  purpose: string;
  template: TaskTemplateOption | null;
  memories: OwnerMemoryOption[];
  proposed_changes: {
    goal_contract: string | null;
    delivery_spec: Record<string, unknown>;
    method: string | null;
  };
  preview_sha256: string;
};

export function getTaskContextOptions(purpose = "web_research"): Promise<{
  templates: TaskTemplateOption[];
  memories: OwnerMemoryOption[];
}> {
  return api.get(`${BASE}/context-options?purpose=${encodeURIComponent(purpose)}`);
}

export function previewTaskContext(payload: {
  purpose: string;
  objective_text: string;
  output_formats: string[];
  selection: TaskContextSelection;
}): Promise<TaskContextPreview> {
  return api.post(`${BASE}/context-preview`, payload);
}

export type CapabilityNeed = {
  purpose: string;
  operations: string[];
  input_formats: string[];
  output_formats: string[];
};

export type CapabilityResolution = {
  matches: Array<{
    ref: { pack_id: string; version: string; digest: string };
    compatibility: Omit<CapabilityNeed, "purpose">;
    authorization: "freeze_gate_passed";
    health: "not_checked";
    license: string;
    source_provenance: string[];
  }>;
  gaps: Array<{ code: string; remediation: string }>;
};

export function resolveWorkspaceCapabilities(need: CapabilityNeed): Promise<CapabilityResolution> {
  return api.post(`${BASE}/capabilities/resolve`, { need, allow_discovery: false });
}

export type GrayCapability = {
  pack_id: string;
  version: string;
  digest: string;
  name: string;
  kind: "tool" | "mcp_local" | "skill" | "dependency_bundle" | "capability_pack";
  purpose: string;
  scope: "platform" | "personal";
  reuse_need?: CapabilityNeed | null;
};

export function listGrayCapabilities(): Promise<{
  enabled: boolean;
  items: GrayCapability[];
}> {
  return api.get(`${BASE}/capabilities`);
}

export function getWorkspaceGuidance(): Promise<WorkspaceGuidance> {
  return api.get(`${BASE}/guidance`);
}

export function listWorkspaceTasks(
  deleted = false,
): Promise<WorkspaceTask[]> {
  return api.get(`${BASE}/tasks?deleted=${deleted ? "true" : "false"}`);
}

export function getWorkspaceTask(
  taskId: string,
  revision?: number | null,
): Promise<WorkspaceTask> {
  const query = revision ? `?revision=${revision}` : "";
  return api.get(`${BASE}/tasks/${taskId}${query}`);
}

export function createWorkspaceTask(payload: {
  objective_text: string;
  upload_ids: string[];
  source_snapshot_id?: string;
  must_include?: string[];
  explicit_exclusions?: string[];
  quantity_requirement?: string;
  completeness_requirement?: string;
  output_formats: string[];
  provider?: string;
  model?: string | null;
  runtime_version?: "legacy" | "pi";
  permission_profile?: "standard";
  model_connection_id?: string | null;
  model_connection_model?: string | null;
  external_api_confirmed?: boolean;
  capability_need?: CapabilityNeed;
  capability_pack_refs?: Array<{
    pack_id: string;
    version: string;
    digest: string;
  }>;
  context_purpose?: string;
  context_selection?: TaskContextSelection;
  context_preview_sha256?: string;
}, idempotencyKey?: string): Promise<WorkspaceTask> {
  return api.post(`${BASE}/tasks`, {
    provider: "local",
    ...payload,
  }, idempotencyKey ? { "Idempotency-Key": idempotencyKey } : {});
}

export function answerWorkspaceTask(
  taskId: string,
  payload: { answer: string; expected_revision: number; question_round_id: string },
  idempotencyKey: string,
): Promise<WorkspaceTask> {
  return api.post(`${BASE}/tasks/${taskId}/answer`, payload, { "Idempotency-Key": idempotencyKey });
}

export function resumeAccountWorkspaceTask(taskId: string, expectedGeneration: number, expectedActiveRevision: number, externalApiConfirmed: boolean): Promise<{ strategy: "waiting" | "unstarted" | "new_revision"; revision: WorkspaceRevision | null }> {
  return api.post(`${BASE}/tasks/${taskId}/account-resume`, {
    expected_generation: expectedGeneration,
    expected_active_revision: expectedActiveRevision,
    external_api_confirmed: externalApiConfirmed,
  });
}

export function cancelWorkspaceTask(taskId: string): Promise<WorkspaceTask> {
  return api.post(`${BASE}/tasks/${taskId}/cancel`);
}

export function retryCandidateVerification(
  taskId: string,
): Promise<WorkspaceTask> {
  return api.post(`${BASE}/tasks/${taskId}/candidate-verification/retry`);
}

export function createSourceAcquisition(
  payload: {
    url?: string | null;
    query?: string;
    time_range?: "any" | "day" | "week" | "month" | "year";
    domains?: string[];
    purpose: string;
    allowed_scope: "current_page" | "same_site" | "public_search";
    page_limit?: number;
    completeness_mode?: "exploratory" | "hard_min_pages" | "hard_scope_complete";
    required_valid_pages?: number | null;
  },
  idempotencyKey: string,
): Promise<SourceAcquisitionAttempt> {
  return api.post(
    `${BASE}/source-acquisitions`,
    payload,
    { "Idempotency-Key": idempotencyKey },
  );
}

export function getSourceAcquisition(
  attemptId: string,
): Promise<SourceAcquisitionAttempt> {
  return api.get(`${BASE}/source-acquisitions/${attemptId}`);
}

export function cancelSourceAcquisition(
  attemptId: string,
): Promise<SourceAcquisitionAttempt> {
  return api.post(`${BASE}/source-acquisitions/${attemptId}/cancel`);
}

export function refreshWorkspaceSource(
  taskId: string,
  expectedActiveRevision: number,
  externalApiConfirmed: boolean,
  idempotencyKey: string,
  resumeUnknown: boolean,
): Promise<{
  status: "acquiring" | "revision_created";
  attempt: SourceAcquisitionAttempt;
  revision: WorkspaceRevision | null;
}> {
  return api.post(
    `${BASE}/tasks/${taskId}/source-refresh`,
    {
      expected_active_revision: expectedActiveRevision,
      external_api_confirmed: externalApiConfirmed,
      resume_unknown: resumeUnknown,
    },
    { "Idempotency-Key": idempotencyKey },
  );
}

export function decideCandidateGap(
  taskId: string,
  payload: {
    action: "accept_gap" | "reject_gap" | "supplement_source" | "refresh_source";
    expected_revision: number;
    expected_candidate_set_hash: string;
    external_api_confirmed: boolean;
  },
  idempotencyKey: string,
): Promise<{
  action: string;
  status: string;
  source_revision: number;
  target_revision: number | null;
  next_action?: string;
}> {
  return api.post(
    `${BASE}/tasks/${taskId}/candidate-gap-actions`,
    payload,
    { "Idempotency-Key": idempotencyKey },
  );
}

export function requestCandidateReverification(
  taskId: string,
  payload: {
    expected_revision: number;
    expected_previous_attempt_id: string;
    external_api_confirmed: boolean;
    accept_duplicate_provider_cost: boolean;
    historical_authority_recovery?: HistoricalAuthorityRecoveryConfirmation;
    expected_candidate_set_hash?: LegacyRebaselineConfirmation["expected_candidate_set_hash"];
    expected_target_ruleset_hash?: LegacyRebaselineConfirmation["expected_target_ruleset_hash"];
    legacy_ruleset_unknown_acknowledged?: true;
    authorization_text_version?: "legacy-rebaseline-v1";
  },
  idempotencyKey: string,
): Promise<VerificationAttemptReceipt> {
  return api.post(
    `${BASE}/tasks/${taskId}/candidate-verifications`,
    payload,
    { "Idempotency-Key": idempotencyKey },
  );
}

export function publishCandidateVerification(
  taskId: string,
  attemptId: string,
  expectedRevision: number,
  idempotencyKey: string,
): Promise<DeliveryManifest> {
  return api.post(
    `${BASE}/tasks/${taskId}/candidate-verifications/${attemptId}/publish`,
    { expected_revision: expectedRevision },
    { "Idempotency-Key": idempotencyKey },
  );
}

export function createWorkspaceRevision(
  taskId: string,
  instruction: string,
  expectedActiveRevision: number,
  outputFormats?: string[],
  externalApiConfirmed = false,
): Promise<WorkspaceRevision> {
  return api.post(`${BASE}/tasks/${taskId}/revisions`, {
    instruction,
    output_formats: outputFormats,
    external_api_confirmed: externalApiConfirmed,
    expected_active_revision: expectedActiveRevision,
  });
}

export function sendWorkspaceTurn(
  taskId: string,
  text: string,
  idempotencyKey: string,
  resultContext?: ResultSelection | null,
): Promise<SteeringResult> {
  return api.post(
    `${BASE}/tasks/${taskId}/turns`,
    { text, ...(resultContext ? { result_context: resultContext } : {}) },
    { "Idempotency-Key": idempotencyKey },
  );
}

export function decideWorkspaceRevision(
  taskId: string,
  proposalId: string,
  mode: "cancel_now" | "after_safe_point" | "new_task",
  externalApiConfirmed = false,
): Promise<{
  decision: { status: string; decision_id: string };
  revision: WorkspaceRevision | null;
}> {
  return api.post(
    `${BASE}/tasks/${taskId}/revision-proposals/${proposalId}/decision`,
    { mode, external_api_confirmed: externalApiConfirmed },
  );
}

export function recycleWorkspaceTask(taskId: string): Promise<WorkspaceTask> {
  return api.del(`${BASE}/tasks/${taskId}`);
}

export function restoreWorkspaceTask(taskId: string): Promise<WorkspaceTask> {
  return api.post(`${BASE}/tasks/${taskId}/restore`);
}

export function permanentlyDeleteWorkspaceTask(
  taskId: string,
): Promise<{ ok: boolean }> {
  return api.del(`${BASE}/tasks/${taskId}/permanent`);
}

export function getWorkspacePreview(
  taskId: string,
  params: {
    offset: number;
    limit: number;
    search?: string;
    sortBy?: string;
    sortDirection?: "asc" | "desc";
    revision?: number;
    outputId?: string;
  },
): Promise<WorkspacePreview> {
  const query = new URLSearchParams({
    offset: String(params.offset),
    limit: String(params.limit),
    search: params.search ?? "",
    sort_direction: params.sortDirection ?? "asc",
  });
  if (params.sortBy) query.set("sort_by", params.sortBy);
  if (params.revision) query.set("revision", String(params.revision));
  if (params.outputId) query.set("output_id", params.outputId);
  return api.get(`${BASE}/tasks/${taskId}/preview?${query}`);
}

export function getWorkspaceStorage(): Promise<WorkspaceStorage> {
  return api.get(`${BASE}/storage`);
}

export function getWorkspaceSourcePreview(taskId: string, artifactId: string, params: {
  revision: number; offset?: number; limit?: number; table_ref?: string; search?: string;
  sort_by?: string; sort_direction?: "asc" | "desc"; row_number?: number; element_id?: string; extractor_version?: string; page?: number;
}): Promise<WorkspaceSourcePreview> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value !== undefined && value !== "") query.set(key, String(value));
  return api.get(`${BASE}/tasks/${encodeURIComponent(taskId)}/sources/${encodeURIComponent(artifactId)}/preview?${query}`);
}

export async function downloadWorkspaceSourceBundle(
  taskId: string, filename: string, revision: number,
  tableFormat: "none" | "csv" | "xlsx", signal: AbortSignal,
) {
  const query = new URLSearchParams({ revision: String(revision), table_format: tableFormat });
  try {
    await downloadFile(`${BASE}/tasks/${encodeURIComponent(taskId)}/source-bundle?${query}`, filename, signal, "application/zip");
  } catch (error) {
    if (signal.aborted) throw error;
    // 下载错误只展示产品说明，避免把服务端路径或内部细节带入界面。
    const messages: Record<number, string> = {
      401: "登录已失效或身份已变化，请重新登录后打开任务。",
      403: "没有权限下载这份资料，请核对当前登录账号。",
      404: "资料或版本不存在，或无权访问，请刷新任务核对。",
      409: "来源已变化、已删除或没有可用的冻结来源，请刷新任务核对。",
      413: "资料包超出下载限制，请联系管理员。",
      422: "下载参数无效，请重新选择表格格式后重试。",
      502: "下载内容格式无效，请稍后重试。",
      507: "存储空间不足，请稍后重试或联系管理员。",
    };
    throw new Error(error instanceof ApiError && messages[error.status]
      ? messages[error.status] : "资料包下载失败，请检查网络后重试。");
  }
}

export function downloadWorkspaceBundle(
  taskId: string,
  filename: string,
  includeSources: boolean,
  revision?: number,
) {
  const query = new URLSearchParams({
    include_sources: String(includeSources),
  });
  if (revision) query.set("revision", String(revision));
  return downloadFile(
    `${BASE}/tasks/${taskId}/bundle?${query}`,
    filename,
  );
}

export function streamWorkspaceTask(
  taskId: string,
  handlers: {
    onProgress?: (event: WorkspaceEvent) => void;
    onMessage?: (event: WorkspaceMessage) => void;
    onStatus?: (event: {
      task_id: string;
      revision?: number;
      run_id?: string | null;
      status: WorkspaceTask["status"];
      question: WorkspaceTask["question"];
      error: string | null;
      failure: WorkspaceTask["failure"];
    }) => void;
    onDone?: (event: { task_id?: string; revision?: number; run_id?: string | null; status: WorkspaceTask["status"] }) => void;
    onError?: (error: Error) => void;
  },
  revision?: number,
  runId?: string | null,
): () => void {
  const generation = getAuthGeneration();
  let stopped = false;
  let controller: AbortController;
  let lastEventId = "";
  const seenMessages = new Map<string, string>();
  const connect = () => {
    if (stopped || generation !== getAuthGeneration()) return;
    const connection = new AbortController();
    controller = connection;
    void fetchEventSource(`${BASE}/tasks/${encodeURIComponent(taskId)}/stream${revision === undefined ? "" : `?revision=${revision}`}`, {
      method: "GET",
      headers: lastEventId ? { "Last-Event-ID": lastEventId } : {},
      fetch: (path, init) => {
        if (stopped || generation !== getAuthGeneration()) {
          connection.abort();
          return Promise.reject(new Error("登录身份已变化，请重新打开当前任务"));
        }
        return authenticatedFetch(path, init);
      },
      signal: connection.signal,
      openWhenHidden: true,
      async onopen(response) {
        if (!response.ok) throw new ApiError(response.status, `事件流连接失败（${response.status}）`);
      },
      onmessage(message) {
        if (connection.signal.aborted || stopped) return;
        if (generation !== getAuthGeneration()) { connection.abort(); return; }
        if (message.id) lastEventId = message.id;
        if (!message.data) return;
        let payload;
        try { payload = JSON.parse(message.data); }
        catch { handlers.onError?.(new Error("收到无法读取的更新，正在恢复任务记录")); return; }
        if (!payload || typeof payload !== "object") return;
        if (["progress", "message", "status", "done"].includes(message.event) && revision !== undefined) {
          if (payload.revision !== revision || (payload.task_id && payload.task_id !== taskId)
            || (runId !== undefined && payload.run_id !== runId && !(["message", "progress"].includes(message.event) && payload.run_id === null))) return;
        }
        if (message.event === "auth-expired") {
          connection.abort();
          // 只复核会话并重新订阅 GET；不能把连接过期转换为任务取消或重复执行。
          void revalidateStreamSession(generation).then((current) => {
            if (current && !stopped) connect();
          }).catch((error) => {
            if (!stopped) handlers.onError?.(error as Error);
          });
        }
        else if (message.event === "message") {
          // 只接受公开且完整的持久回答；不能把未知载荷当正文渲染。
          if (typeof payload.message_id !== "string" || !payload.message_id
            || payload.version !== 1 || payload.task_id !== taskId
            || !Number.isSafeInteger(payload.revision) || payload.revision < 1
            || (revision !== undefined && payload.revision !== revision)
            || !(payload.run_id === null || typeof payload.run_id === "string")
            || typeof payload.turn_id !== "string" || typeof payload.created_at !== "string"
            || payload.role !== "assistant" || payload.kind !== "answer"
            || payload.status !== "completed" || typeof payload.content !== "string") return;
          const previous = seenMessages.get(payload.message_id);
          if (previous !== undefined) {
            if (previous !== payload.content) handlers.onError?.(new Error("回答记录版本冲突，请重新读取任务"));
            return;
          }
          seenMessages.set(payload.message_id, payload.content);
          handlers.onMessage?.({ ...payload, result_context: readPublicResultContext(payload.result_context) });
        }
        else if (message.event === "progress") handlers.onProgress?.(payload);
        else if (message.event === "status") handlers.onStatus?.(payload);
        else if (message.event === "done") {
          stopped = true;
          connection.abort();
          handlers.onDone?.(payload);
        }
      },
      onclose() {
        // 没有 done 的断流只恢复只读订阅，不能误称任务完成。
        if (!stopped && !connection.signal.aborted) throw new Error("更新连接中断");
      },
      onerror(error) {
        if (connection.signal.aborted || stopped) throw error;
        if (error instanceof ApiError && [401, 403, 404, 409, 429].includes(error.status)) {
          connection.abort();
          handlers.onError?.(error);
          throw error;
        }
        handlers.onError?.(error as Error);
        // 只读订阅按协议重连，任务事实仍由持久化 sequence 决定。
      },
    }).catch((error) => {
      if (!connection.signal.aborted && !stopped) handlers.onError?.(error as Error);
    });
  };
  connect();
  return () => { stopped = true; controller?.abort(); };
}
