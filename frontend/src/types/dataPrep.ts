/**
 * 数据准备前端类型（Phase 2 Task 11）。
 * 与后端 src/api/routes/data_tasks.py 的响应契约对齐。
 */

export interface UploadItem {
  upload_id: string;
  original_name: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
}

export interface DocumentPreviewElement {
  element_id: string;
  artifact_id: string;
  page: number;
  element_type: "document" | "page" | "section" | "paragraph" | "table" | "cell" | "image";
  text: string | null;
  reading_order: number | null;
  extractor: string;
  extractor_version: string;
  metadata: {
    location?: {
      kind?: string;
      paragraph?: number;
      table?: number;
      row?: number;
    };
    [key: string]: unknown;
  };
}

export interface DocumentPreview {
  upload_id: string;
  original_name: string;
  status: "ready" | "empty";
  elements: DocumentPreviewElement[];
  rejects: Array<Record<string, unknown>>;
}

export interface DataTaskPreview {
  probe: {
    reachable: boolean;
    size_bytes: number;
    media_type: string;
    original_name: string;
  };
  sample: Array<Record<string, unknown>>;
  schema: {
    fields: Array<{ name: string; dtype: string }>;
    inferred: boolean;
    record_count: number;
  };
  parser_warnings: string[];
  recipe: Record<string, unknown>;
  estimated_records: number;
  estimated_bytes: number;
  high_impact_rules: string[];
}

export interface DataTask {
  task_id: string;
  user_id: string;
  status: string; // RUNNING | SUCCEEDED | SUCCEEDED_WITH_WARNINGS | FAILED
  record_counts: Record<string, number>;
  quality: Record<string, unknown> | null;
  manifest_path: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
  spec: Record<string, unknown>;
}

export interface ManifestOutput {
  format: string;
  path: string;
  sha256: string;
  records: number;
}

export interface DatasetManifest {
  task_id: string;
  spec_version: string;
  outputs: ManifestOutput[];
  record_counts: Record<string, number>;
  schema_ref: string | null;
  quality_ref: string | null;
  lineage_ref: string | null;
}

export interface ExtractionFieldSpec {
  name: string;
  dtype: string;
  required: boolean;
  description: string | null;
  require_evidence: boolean;
  min_confidence: number;
}

export interface ExtractionSpec {
  spec_version: string;
  goal: {
    objective: string;
    document_types: string[];
    success_criteria: string[];
  };
  discovery: {
    artifact_ids: string[];
    pages: Record<string, number[]>;
    section_patterns: string[];
  };
  fields: ExtractionFieldSpec[];
  result_contract: {
    shape: "fields" | "records" | "tables" | "document" | "aggregate";
    cardinality: "one" | "many" | "all";
    record_grain: string | null;
    renderer: string;
    output_formats: string[];
    exhaustive: boolean;
    merge_tables?: boolean;
  };
  conflict_policy: "review" | "keep_all" | "reject";
}

export interface DocumentModelSelection {
  provider: string;
  model: string;
}

export interface DocumentDraft {
  task_id: string;
  unit_id?: string;
  revision?: number;
  status: "SPEC_DRAFT" | "READY";
  model_selection: DocumentModelSelection;
  extraction_spec: ExtractionSpec;
}

export interface EvidenceRef {
  artifact_id: string;
  element_id: string;
  page: number;
  bbox: {
    x0: number;
    y0: number;
    x1: number;
    y1: number;
    coordinate_space: string;
  } | null;
  quote: string | null;
  confidence: number;
  extractor: string;
  extractor_version: string;
  location: {
    kind?: string;
    paragraph?: number;
    table?: number;
    row?: number;
    [key: string]: unknown;
  };
}

export interface ExtractedField {
  name: string;
  value: unknown;
  status: "found" | "not_found" | "conflict" | "low_confidence";
  evidence_refs: EvidenceRef[];
  candidates: unknown[];
  review_reason: string | null;
}

export interface ReviewTask {
  task_id: string;
  artifact_id: string;
  page: number;
  field_name: string;
  record_id?: string | null;
  reasons: string[];
  candidates: unknown[];
  status: string;
  resolution: ReviewDecision | null;
}

export interface ReviewDecision {
  review_task_id?: string;
  field_name?: string;
  decision: "accept_candidate" | "replace" | "mark_not_found";
  candidate_index: number | null;
  value: unknown;
  note: string | null;
  user_id: string;
  decided_at: string;
}

export interface ExtractedRecord {
  record_id: string;
  fields: ExtractedField[];
  values: Record<string, unknown>;
  status: "found" | "not_found" | "conflict" | "low_confidence";
  source_artifact_ids: string[];
  review_required: boolean;
}

export interface ExtractedTable {
  table_id: string;
  name: string;
  artifact_id: string;
  page: number;
  columns: string[];
  rows: Array<Record<string, unknown>>;
  evidence_element_ids: string[];
}

export interface ExtractedDocument {
  document_id: string;
  title: string;
  content: string;
  source_artifact_ids: string[];
  evidence_refs: EvidenceRef[];
}

export interface ExtractedAggregate {
  aggregate_id: string;
  fields: ExtractedField[];
  values: Record<string, unknown>;
  status: "found" | "not_found" | "conflict" | "low_confidence";
  source_artifact_ids: string[];
  review_required: boolean;
}

export interface DocumentExtractionResult {
  task_id: string;
  status: "COMPLETED" | "NEEDS_REVIEW" | "FAILED";
  artifacts?: Array<{
    artifact_id: string;
    upload_id: string;
    original_name: string;
  }>;
  fields: ExtractedField[];
  records?: ExtractedRecord[];
  tables?: ExtractedTable[];
  documents?: ExtractedDocument[];
  aggregates?: ExtractedAggregate[];
  coverage?: Record<string, number>;
  review_tasks: ReviewTask[];
  review_decisions?: ReviewDecision[];
  table_recipe?: {
    recipe_id: string;
    version: string;
    mode: string;
    rules: Array<Record<string, unknown>>;
  } | null;
}

export interface DocumentWorkspace {
  upload_ids: string[];
  checked_upload_ids: string[];
  active_task_id: string | null;
  active_unit_id: string | null;
  selected_upload_id: string | null;
  updated_at: string | null;
}

export interface DocumentTaskUnit {
  unit_id: string;
  unit_type: "single_file" | "file_set";
  name: string;
  business_type: string | null;
  upload_ids: string[];
  members: UploadItem[];
  latest_task: DataTask | null;
  run_count: number;
  archived_at?: string | null;
  created_at: string;
  updated_at: string;
}
