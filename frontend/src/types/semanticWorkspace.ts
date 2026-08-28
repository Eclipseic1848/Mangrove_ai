import type { UploadItem } from "@/types/dataPrep";

export type WorkspaceTaskStatus =
  | "queued"
  | "running"
  | "needs_input"
  | "cancelling"
  | "cancelled"
  | "failed"
  | "candidate_ready"
  | "completed";

export interface WorkspaceQuestionOption {
  value: string;
  label: string;
  description?: string;
}

export interface WorkspaceQuestion {
  kind: "external" | "plan" | "binding" | "harness";
  question_id: string;
  prompt: string;
  reason?: string;
  affected_scope?: string;
  options: WorkspaceQuestionOption[];
  allow_free_text: boolean;
  external_service?: string;
  outbound_data?: string[];
  purpose?: string;
  risk?: string;
}

export interface WorkspaceEvent {
  event_id: string;
  sequence: number;
  stage: string;
  event_type: string;
  summary: string;
  details: Record<string, unknown>;
  created_at: string;
  source?: "harness";
}

export type ProgressStage =
  | "understand"
  | "inspect_sources"
  | "prepare_capabilities"
  | "execute"
  | "verify"
  | "deliver";

export interface StructuredProgressEvent extends WorkspaceEvent {
  revision: number;
  run_id: string | null;
  progress: { current: number; total: number | null; unit: string } | null;
  refs: Record<string, unknown>;
  action: Record<string, unknown> | null;
  audience: "user" | "admin" | "all";
}

export interface TaskProgressView {
  active_stage: ProgressStage | null;
  stages: Array<{
    stage: ProgressStage;
    status: "pending" | "active" | "waiting" | "completed" | "failed";
    summary: string;
  }>;
  events: StructuredProgressEvent[];
}

export interface SteeringResult {
  result_id: string;
  task_id: string;
  turn_id: string;
  delta_id: string;
  action:
    | "answer_only"
    | "normalized_no_material_change"
    | "revision_proposal"
    | "new_task_proposal"
    | "permission_request";
  acknowledgement: string;
  answer: string | null;
  proposal_id: string | null;
  run_id: string | null;
  revision: number;
}

export interface WorkspaceRevision {
  task_id: string;
  revision: number;
  objective_text: string;
  output_formats: string[];
  plan_id: string | null;
  logical_revision: number | null;
  binding_revision: number | null;
  run_id: string | null;
  status: WorkspaceTaskStatus;
  summary: string;
  change_summary: string;
  created_at: string;
  updated_at: string;
}

export interface WorkspaceFailure {
  error_code: string;
  stage: string;
  cause_summary: string;
  attempt_count: number;
  elapsed_ms: number;
  source_read: boolean;
  intermediate_created: boolean;
  delivery_published: boolean;
  next_actions: string[];
  diagnostic_ref: string;
}

export interface DeliveryQA {
  openable: boolean;
  checks: string[];
  warnings: string[];
  row_count?: number | null;
  page_count?: number | null;
  slide_count?: number | null;
  sheet_count?: number | null;
}

export interface DeliveryOutput {
  output_id: string;
  format: string;
  filename: string;
  media_type: string;
  sha256: string;
  size_bytes: number;
  qa: DeliveryQA;
  download_url: string;
}

export interface DeliveryManifest {
  delivery_id: string;
  run_id: string;
  plan_id: string;
  status: string;
  outputs: DeliveryOutput[];
  requested_formats: string[];
  created_at: string;
}

export interface RuntimeCandidate {
  artifact_id: string;
  filename: string;
  format: string;
  sha256: string;
  size_bytes: number;
  openable: boolean;
  qa_checks: string[];
  download_url: string;
  download_allowed?: boolean;
}

export interface RuntimeVerification {
  status: "passed" | "failed" | "inconclusive";
  summary: string;
  evidence_count: number;
  formal_delivery_eligible: boolean;
  checks: Array<{
    code: string;
    passed: boolean;
    summary: string;
  }>;
}

export type VerificationAttemptStatus =
  | "requested"
  | "running"
  | "passed"
  | "failed"
  | "inconclusive"
  | "outcome_unknown"
  | "cancelled";

export interface VerificationAttemptSummary {
  attempt_id: string | null;
  status: VerificationAttemptStatus | null;
  reason: string | null;
  ruleset_identity_status: string | null;
}

export interface ReverificationOffer {
  eligible: boolean;
  reason: string | null;
  blockers: string[];
  ruleset_changed: boolean | null;
  ruleset_change_summary: string;
  requires_provider: boolean;
  connection_id: string | null;
  model_id: string | null;
  candidate_count?: number;
  candidate_formats?: string[];
  candidate_set_hash?: string;
  target_ruleset_hash?: string;
  egress_categories: string[];
  egress_summary: string;
  historical_authority_recovery?: HistoricalAuthorityRecoveryOffer | null;
}

export interface HistoricalAuthorityRecoveryOffer {
  expected_evidence_hash: string;
  purpose: "semantic_inconclusive_reverification";
  owner_id: string;
  task_id: string;
  revision: number;
  run_id: string;
  candidate_set_hash: string;
  explanation: string;
}

export interface HistoricalAuthorityRecoveryConfirmation {
  expected_evidence_hash: string;
  acknowledge_no_historical_assignment: true;
  acknowledge_reverification_only: true;
}

export interface LegacyRebaselineConfirmation {
  expected_candidate_set_hash: string;
  expected_target_ruleset_hash: string;
  legacy_ruleset_unknown_acknowledged: true;
  authorization_text_version: "legacy-rebaseline-v1";
}

export interface AgenticRuntimeInfo {
  runtime_version: "legacy" | "pi";
  permission_profile: "standard" | "extended" | "host_dev";
  status: string | null;
  run_id?: string | null;
  session_file?: string | null;
  summary?: string | null;
  candidates: RuntimeCandidate[];
  verification?: RuntimeVerification | null;
  failure?: Record<string, unknown> | null;
  events?: Array<Record<string, unknown>>;
  coverage?: {
    contract: {
      interpretation: string;
      result_cardinality: "first" | "count" | "all";
      completeness: "strict" | "best_effort";
      stop_semantics: string;
    };
    progress: {
      authorized: number;
      observed: number;
      candidates?: number;
      authoritatively_read: number;
      low_quality: number;
      unknown: number;
      evidence: number;
      cache_hits: number;
    };
    ledger: Record<string, unknown>;
  } | null;
  latest_verification_attempt?: VerificationAttemptSummary | null;
  reverification_offer?: ReverificationOffer | null;
  reverification_unavailable_reason?: string | null;
  awaiting_publication?: boolean;
}

export interface WorkspaceTask {
  task_id: string;
  title: string;
  objective_text: string;
  upload_ids: string[];
  output_formats: string[];
  provider: string;
  model: string | null;
  model_connection_id?: string | null;
  external_api_confirmed: boolean;
  status: WorkspaceTaskStatus;
  active_revision: number;
  current_status?: WorkspaceTaskStatus;
  current_revision?: number;
  viewing_revision?: number;
  plan_id: string | null;
  logical_revision: number | null;
  binding_revision: number | null;
  run_id: string | null;
  summary: string;
  error: string | null;
  failure: WorkspaceFailure | null;
  question: WorkspaceQuestion | null;
  cancel_requested: boolean;
  deleted_at: string | null;
  purge_after: string | null;
  created_at: string;
  updated_at: string;
  revisions?: WorkspaceRevision[];
  events?: WorkspaceEvent[];
  uploads?: UploadItem[];
  plan?: {
    revision: number;
    summary: string;
    plan: Record<string, unknown>;
    diagnostics: Array<Record<string, unknown>>;
  } | null;
  run?: Record<string, unknown> | null;
  attempts?: Array<Record<string, unknown>>;
  harness_events?: WorkspaceEvent[];
  delivery?: DeliveryManifest | null;
  runtime_version?: "legacy" | "pi";
  permission_profile?: "standard" | "extended" | "host_dev";
  agentic_runtime?: AgenticRuntimeInfo;
  progress?: TaskProgressView;
  web_source?: {
    source_snapshot_id: string;
    goal_contract: {
      objective: string;
      must_include: string[];
      explicit_exclusions: string[];
      quantity_requirement: string;
      completeness_requirement: string;
    };
    delivery_spec: { formats: string[] };
    runtime_binding: {
      adapter_id: string;
      adapter_version: string;
      runtime_artifact: string;
      protocol_version: string;
      event_schema_version: string;
      capability_digest: string;
      external_run_id: string;
      model_connection_id: string | null;
      model_connection_version: string | null;
      model: string;
    };
    created_at: string;
    snapshot: SourceSnapshot;
  } | null;
}

export interface VerificationAttemptReceipt {
  attempt_id: string;
  task_id: string;
  revision: number;
  run_id: string;
  previous_attempt_id: string;
  status: VerificationAttemptStatus;
  task: WorkspaceTask;
}

export interface TablePreview {
  kind: "table";
  columns: string[];
  rows: Array<Record<string, unknown>>;
  total: number;
  offset: number;
  limit: number;
}

export interface DocumentPreviewItem {
  type: "passage" | "difference" | "finding" | "derived";
  id: string;
  label: string;
  content: string;
  status?: string;
  change_type?: string;
  evidence_refs: Array<Record<string, unknown>>;
}

export interface WorkspaceDocumentPreview {
  kind: "document";
  action: string;
  items: DocumentPreviewItem[];
  total: number;
  offset: number;
  limit: number;
  warnings: string[];
}

export type WorkspacePreview = TablePreview | WorkspaceDocumentPreview;

export interface WorkspaceGuidance {
  schema_version: string;
  onboarding: Array<{ title: string; description: string }>;
  examples: Array<{
    id: string;
    category: string;
    title: string;
    description: string;
    required_inputs: string;
    prompt: string;
    output_formats: string[];
  }>;
}

export interface WorkspaceStorage {
  task_count: number;
  recycle_bin_count: number;
  upload_bytes: number;
  delivery_bytes: number;
  total_bytes: number;
  retention: string;
  calculated_at: string;
}

export type SourceAcquisitionStatus =
  | "acquiring"
  | "succeeded"
  | "failed"
  | "canceled";

export interface SourceArtifact {
  artifact_id: string;
  request_url: string;
  final_url: string;
  read_at: string;
  content_sha256: string;
  media_type: string;
  size_bytes: number;
  title: string;
  text_preview: string;
}

export interface SourceSnapshot {
  snapshot_id: string;
  attempt_id: string;
  allowed_scope: {
    kind: "current_page" | "same_site";
    normalized_url: string;
    site: string;
    page_limit: number;
    completeness: {
      mode: "exploratory" | "hard_min_pages" | "hard_scope_complete";
      required_valid_pages: number | null;
    };
  };
  valid_page_count: number;
  failed_page_count: number;
  created_at: string;
  coverage: {
    status: "scope_complete" | "coverage_unknown" | "hard_insufficient";
    limit_reached: boolean;
    attempted_page_count: number;
    required_valid_pages: number | null;
  };
  artifacts: SourceArtifact[];
  failures: Array<{
    failure_id: string;
    request_url: string;
    final_url: string | null;
    error_code: string;
    error_message: string;
    failed_at: string;
  }>;
}

export interface SourceAcquisitionAttempt {
  attempt_id: string;
  idempotency_key: string;
  request_url: string;
  normalized_url: string;
  allowed_scope: {
    kind: "current_page" | "same_site";
    normalized_url: string;
    site: string;
    page_limit: number;
    completeness: {
      mode: "exploratory" | "hard_min_pages" | "hard_scope_complete";
      required_valid_pages: number | null;
    };
  };
  purpose: string;
  status: SourceAcquisitionStatus;
  started_at: string;
  finished_at: string | null;
  snapshot_id: string | null;
  error_code: string | null;
  error_message: string | null;
  snapshot: SourceSnapshot | null;
}
