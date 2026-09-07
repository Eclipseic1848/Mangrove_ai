import { fetchEventSource } from "@microsoft/fetch-event-source";
import { api, downloadFile, authenticatedFetch, getAuthGeneration, revalidateStreamSession, ApiError } from "@/lib/api";
import type {
  WorkspaceEvent,
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
} from "@/types/semanticWorkspace";

const BASE = "/api/semantic-workspace";

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

export type GrayCapability = {
  pack_id: string;
  version: string;
  digest: string;
  name: string;
  kind: "tool" | "mcp_local" | "skill" | "dependency_bundle" | "capability_pack";
  purpose: string;
  scope: "platform" | "personal";
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
  answer: string,
): Promise<WorkspaceTask> {
  return api.post(`${BASE}/tasks/${taskId}/answer`, { answer });
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
    url: string;
    purpose: string;
    allowed_scope: "current_page" | "same_site";
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
): Promise<SteeringResult> {
  return api.post(
    `${BASE}/tasks/${taskId}/turns`,
    { text },
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
  return api.get(`${BASE}/tasks/${taskId}/preview?${query}`);
}

export function getWorkspaceStorage(): Promise<WorkspaceStorage> {
  return api.get(`${BASE}/storage`);
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
    onStatus?: (event: {
      task_id: string;
      status: WorkspaceTask["status"];
      question: WorkspaceTask["question"];
      error: string | null;
      failure: WorkspaceTask["failure"];
    }) => void;
    onDone?: (event: { status: WorkspaceTask["status"] }) => void;
    onError?: (error: Error) => void;
  },
): () => void {
  const generation = getAuthGeneration();
  let stopped = false;
  let controller: AbortController;
  let lastEventId = "";
  const connect = () => {
    if (stopped || generation !== getAuthGeneration()) return;
    const connection = new AbortController();
    controller = connection;
    void fetchEventSource(`${BASE}/tasks/${taskId}/stream`, {
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
        const payload = JSON.parse(message.data);
        if (message.event === "auth-expired") {
          connection.abort();
          // 只复核会话并重新订阅 GET；不能把连接过期转换为任务取消或重复执行。
          void revalidateStreamSession(generation).then((current) => {
            if (current && !stopped) connect();
          }).catch((error) => {
            if (!stopped) handlers.onError?.(error as Error);
          });
        }
        else if (message.event === "progress") handlers.onProgress?.(payload);
        else if (message.event === "status") handlers.onStatus?.(payload);
        else if (message.event === "done") {
          stopped = true;
          connection.abort();
          handlers.onDone?.(payload);
        }
      },
      onerror(error) {
        if (connection.signal.aborted || stopped) throw error;
        if (error instanceof ApiError && (error.status === 401 || error.status === 429)) {
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
