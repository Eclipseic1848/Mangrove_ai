import { fetchEventSource } from "@microsoft/fetch-event-source";
import { api, downloadFile, getToken } from "@/lib/api";
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
    allowed_scope: "current_page";
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
  const controller = new AbortController();
  const token = getToken();
  void fetchEventSource(`${BASE}/tasks/${taskId}/stream`, {
    method: "GET",
    headers: token ? { Authorization: `Bearer ${token}` } : {},
    signal: controller.signal,
    openWhenHidden: true,
    async onopen(response) {
      if (!response.ok) {
        throw new Error(`事件流连接失败（${response.status}）`);
      }
    },
    onmessage(message) {
      if (!message.data) return;
      const payload = JSON.parse(message.data);
      if (message.event === "progress") handlers.onProgress?.(payload);
      else if (message.event === "status") handlers.onStatus?.(payload);
      else if (message.event === "done") handlers.onDone?.(payload);
    },
    onerror(error) {
      if (!controller.signal.aborted) handlers.onError?.(error as Error);
      // 不抛出即可让 fetch-event-source 按协议重连；任务事实仍以服务端持久化
      // sequence 为准，刷新或网络抖动不会制造第二个执行状态。
    },
  }).catch((error) => {
    if (!controller.signal.aborted) handlers.onError?.(error as Error);
  });
  return () => controller.abort();
}
