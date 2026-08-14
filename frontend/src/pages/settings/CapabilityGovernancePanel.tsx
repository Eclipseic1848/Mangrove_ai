import { useEffect, useRef, useState } from "react";
import { Boxes, ChevronDown, Eye, Loader2, Play, ShieldCheck, X } from "lucide-react";
import { api } from "@/lib/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Modal } from "@/components/ui/modal";

type GovernanceItem = {
  pack_id: string;
  version: string;
  scope: "personal" | "platform";
  maturity: "draft" | "verified";
  lifecycle: "active" | "deprecated" | "revoked";
  eligibility: "eligible" | "quarantined";
  source: "governance_event" | "legacy_compat";
  owner_id: string | null;
  digest: string | null;
  can_validate: boolean;
  promotion_gaps: PromotionGap[];
};

type PromotionGap =
  | "validation_incomplete"
  | "evidence_reference_mismatch"
  | "supply_chain_evidence_missing"
  | "secret_detected"
  | "critical_vulnerability"
  | "fixable_high_vulnerability"
  | "misconfiguration_failure"
  | "trivy_database_stale";

type ResolvedGovernanceItem = GovernanceItem & { digest: string };

type ValidationTaskOption = {
  task_id: string;
  revision: number;
  title: string;
  updated_at: string;
};

type ValidationEvidence = {
  step: "synthetic_smoke" | "owner_task_replay" | "fail_closed" | "verifier" | "cleanup";
  status: "passed" | "failed" | "cancelled";
  summary: string;
  occurred_at: string;
};

type ValidationRun = {
  run_id: string;
  owner_id: string;
  target: { pack_id: string; version: string; digest: string };
  task_ref: { task_id: string; revision: number };
  status: "queued" | "running" | "succeeded" | "failed" | "cancelling" | "cancelled";
  evidence: ValidationEvidence[];
  created_at: string;
};

type SupplyChainBlocker =
  | "secret_detected"
  | "critical_vulnerability"
  | "fixable_high_vulnerability"
  | "misconfiguration_failure"
  | "trivy_database_stale";

type SupplyChainEvidence = {
  status: "passed" | "blocked";
  blockers: SupplyChainBlocker[];
  secret_count: number;
  critical_count: number;
  fixable_high_count: number;
  misconfiguration_failure_count: number;
  trivy_version: "0.70.0";
  trivy_database: { version: number; updated_at: string };
  syft_version: "1.50.0";
  cyclonedx_spec_version: "1.6";
  occurred_at: string;
};

const MATURITY_LABEL = { draft: "草稿", verified: "已验证" } as const;
const LIFECYCLE_LABEL = {
  active: "正常",
  deprecated: "已弃用",
  revoked: "已撤销",
} as const;
const ELIGIBILITY_LABEL = {
  eligible: "可运行",
  quarantined: "已隔离",
} as const;
const RUN_LABEL = {
  queued: "等待验证",
  running: "验证中",
  succeeded: "验证步骤通过",
  failed: "验证未通过",
  cancelling: "正在取消",
  cancelled: "已取消",
} as const;
const STEP_LABEL = {
  synthetic_smoke: "合成 Smoke",
  owner_task_replay: "授权真实任务重放",
  fail_closed: "失败关闭与权限",
  verifier: "独立 Verifier",
  cleanup: "资源清理",
} as const;
const BLOCKER_LABEL: Record<SupplyChainBlocker, string> = {
  secret_detected: "检测到 Secret",
  critical_vulnerability: "存在 Critical 漏洞",
  fixable_high_vulnerability: "存在可修复 High 漏洞",
  misconfiguration_failure: "存在 Critical 或可修复 High 安全误配置",
  trivy_database_stale: "Trivy 漏洞库已过期",
};
const GAP_LABEL: Record<PromotionGap, string> = {
  validation_incomplete: "尚无全部通过的验证运行",
  evidence_reference_mismatch: "验证证据引用不一致",
  supply_chain_evidence_missing: "尚未形成供应链扫描证据",
  ...BLOCKER_LABEL,
};

type AuditSubject = "task_prompt" | "task_sources" | "task_output";

type AuditRecord = {
  event_id: string;
  actor_id: string;
  actor_role: string;
  reason: string;
  subject_type: AuditSubject;
  subject_sha256: string | null;
  result: "succeeded" | "failed";
  task_id: string;
  revision: number;
  failure_reason: string | null;
  occurred_at: string;
};

type TaskMetadata = {
  task_id: string;
  revision: number;
  owner_id: string;
  task_status: string;
  created_at: string;
  updated_at: string;
  input_count: number;
  input_types: string[];
  output_count: number;
  output_formats: string[];
};

type AdminReviewItem = {
  pack_id: string;
  version: string;
  scope: "personal" | "platform";
  maturity: "draft" | "verified";
  lifecycle: "active" | "deprecated" | "revoked";
  eligibility: "eligible" | "quarantined";
  source: "governance_event" | "legacy_compat";
  owner_id: string | null;
  digest: string;
  promotion_gaps: PromotionGap[];
  validation: ValidationRun | null;
  supply_chain: SupplyChainEvidence | null;
  task_metadata: TaskMetadata | null;
  audit_history: AuditRecord[];
};

type AuditViewResult = {
  status: "succeeded" | "failed";
  content: string | null;
  truncated: boolean;
  failure_reason: string | null;
  event: AuditRecord;
};

const AUDIT_SUBJECT_LABEL: Record<AuditSubject, string> = {
  task_prompt: "任务 Prompt 正文",
  task_sources: "来源正文",
  task_output: "输出正文",
};

const AUDIT_RESULT_LABEL = { succeeded: "成功", failed: "失败" } as const;

const FAILURE_REASON_LABEL: Record<string, string> = {
  task_not_found: "任务或 revision 不存在",
  source_missing: "来源文件缺失",
  source_invalid: "来源文件无效或路径越界",
  source_unreadable: "来源文件不可读",
  output_missing: "输出文件缺失",
  output_invalid: "输出文件无效或路径越界",
  output_unreadable: "输出文件不可读",
};

const REVIEW_GROUPS = [
  {
    key: "draft",
    label: "待验证",
    match: (item: GovernanceItem) => item.maturity === "draft",
  },
  {
    key: "verified",
    label: "已晋级",
    match: (item: GovernanceItem) =>
      item.maturity === "verified" && item.lifecycle === "active",
  },
  {
    key: "retired",
    label: "已弃用·撤销",
    match: (item: GovernanceItem) =>
      item.lifecycle === "deprecated" || item.lifecycle === "revoked",
  },
] as const;

function shortDigest(digest: string) {
  if (digest.length <= 32) return digest;
  return `${digest.slice(0, 19)}…${digest.slice(-12)}`;
}

function itemKey(item: GovernanceItem) {
  return `${item.scope}:${item.owner_id ?? "platform"}:${item.pack_id}:${item.version}:${item.digest}`;
}

function hasDigest(item: GovernanceItem): item is ResolvedGovernanceItem {
  return typeof item.digest === "string" && item.digest.length > 0;
}

function utcMinute(value: string) {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "不可判定";
  return `${parsed.toISOString().slice(0, 16).replace("T", " ")} UTC`;
}

export function CapabilityGovernancePanel({ ownerOnly = false }: { ownerOnly?: boolean }) {
  const [items, setItems] = useState<GovernanceItem[]>([]);
  const [runs, setRuns] = useState<ValidationRun[]>([]);
  const [supplyChainEvidence, setSupplyChainEvidence] = useState<Record<string, SupplyChainEvidence | null>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [target, setTarget] = useState<ResolvedGovernanceItem | null>(null);
  const [taskOptions, setTaskOptions] = useState<ValidationTaskOption[]>([]);
  const [selectedTask, setSelectedTask] = useState("");
  const [modalLoading, setModalLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [focusedRunId, setFocusedRunId] = useState("");
  const [expandedRunId, setExpandedRunId] = useState("");
  const [reviewItems, setReviewItems] = useState<Record<string, AdminReviewItem>>({});
  const [auditTarget, setAuditTarget] = useState<AdminReviewItem | null>(null);
  const [auditSubject, setAuditSubject] = useState<AuditSubject>("task_prompt");
  const [auditReason, setAuditReason] = useState("");
  const [auditSubmitting, setAuditSubmitting] = useState(false);
  const [auditOutcome, setAuditOutcome] = useState<AuditViewResult | null>(null);
  // 弹窗生命周期内固定幂等键：网络重试不会落第二条审计记录。
  const [auditIdempotencyKey, setAuditIdempotencyKey] = useState("");
  const runElementRef = useRef<HTMLDetailsElement | null>(null);

  const reload = () => Promise.all([
    api.get("/api/capability-governance/packs"),
    api.get("/api/capability-governance/validations"),
    ownerOnly
      ? Promise.resolve(null)
      // 审核聚合是新增只读投影；后端未升级时不阻断既有治理页面。
      : api.get("/api/capability-governance/admin/review").catch(() => null),
  ]).then(async ([packData, runData, reviewData]) => {
    const nextItems: GovernanceItem[] = packData.items ?? [];
    setItems(nextItems);
    setRuns(runData.items ?? []);
    if (reviewData) {
      const nextReview: Record<string, AdminReviewItem> = {};
      for (const item of reviewData.items ?? []) {
        nextReview[reviewKey(item)] = item;
      }
      setReviewItems(nextReview);
    }
    const evidenceItems = nextItems
      .filter(hasDigest)
      .filter((item) => !ownerOnly || item.can_validate);
    const evidencePairs = await Promise.all(evidenceItems.map(async (item) => {
      try {
        const data = await api.get(
          `/api/capability-governance/packs/${encodeURIComponent(item.pack_id)}/${encodeURIComponent(item.version)}/supply-chain-evidence?digest=${encodeURIComponent(item.digest)}`,
        );
        return [itemKey(item), data.evidence ?? null] as const;
      } catch {
        // 供应链证据是新增只读投影；旧库未迁移时不阻断既有治理页面。
        return [itemKey(item), null] as const;
      }
    }));
    setSupplyChainEvidence(Object.fromEntries(evidencePairs));
  });

  useEffect(() => {
    reload()
      .catch((reason: Error) => setError(reason.message || "能力治理状态加载失败"))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!runs.some((run) => ["queued", "running", "cancelling"].includes(run.status))) return;
    const timer = window.setInterval(() => {
      reload().catch((reason: Error) => setError(reason.message || "验证进度刷新失败"));
    }, 1500);
    return () => window.clearInterval(timer);
  }, [runs]);

  useEffect(() => {
    if (!focusedRunId || target !== null || !runElementRef.current) return;
    runElementRef.current.scrollIntoView({ behavior: "smooth", block: "center" });
    runElementRef.current.focus({ preventScroll: true });
  }, [focusedRunId, runs, target]);

  const visibleItems = ownerOnly ? items.filter((item) => item.can_validate) : items;

  function reviewKey(item: { pack_id: string; version: string; scope: string; owner_id: string | null; digest: string | null }) {
    return `${item.scope}:${item.owner_id ?? "platform"}:${item.pack_id}:${item.version}:${item.digest}`;
  }

  function openAuditView(item: AdminReviewItem) {
    if (!item.task_metadata) {
      setError("该能力没有可审计查看的关联任务");
      return;
    }
    setAuditTarget(item);
    setAuditSubject("task_prompt");
    setAuditReason("");
    setAuditOutcome(null);
    setAuditIdempotencyKey(crypto.randomUUID());
    setError("");
  }

  async function submitAuditView() {
    if (!auditTarget?.task_metadata) return;
    setAuditSubmitting(true);
    setError("");
    try {
      const data = await api.post(
        "/api/capability-governance/admin/audit-view",
        {
          pack_id: auditTarget.pack_id,
          version: auditTarget.version,
          digest: auditTarget.digest,
          task_id: auditTarget.task_metadata.task_id,
          revision: auditTarget.task_metadata.revision,
          subject_type: auditSubject,
          reason: auditReason.trim(),
        },
        { "Idempotency-Key": auditIdempotencyKey },
      );
      setAuditOutcome(data);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "审计查看失败");
    } finally {
      setAuditSubmitting(false);
    }
  }

  async function openValidation(item: GovernanceItem) {
    if (!hasDigest(item)) {
      setError("能力 digest 已脱敏，不能发起验证");
      return;
    }
    setTarget(item);
    setTaskOptions([]);
    setSelectedTask("");
    setModalLoading(true);
    setError("");
    try {
      const data = await api.get(
        `/api/capability-governance/packs/${encodeURIComponent(item.pack_id)}/${encodeURIComponent(item.version)}/validation-tasks?digest=${encodeURIComponent(item.digest)}`,
      );
      const options = data.items ?? [];
      setTaskOptions(options);
      if (options[0]) setSelectedTask(`${options[0].task_id}::${options[0].revision}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "可验证任务加载失败");
      setTarget(null);
    } finally {
      setModalLoading(false);
    }
  }

  async function submitValidation() {
    if (!target || !selectedTask) return;
    const [taskId, revision] = selectedTask.split("::");
    setSubmitting(true);
    try {
      const createdRun = await api.post(
        "/api/capability-governance/validations",
        {
          pack_id: target.pack_id,
          version: target.version,
          digest: target.digest,
          task_id: taskId,
          revision: Number(revision),
        },
        { "Idempotency-Key": crypto.randomUUID() },
      );
      setTarget(null);
      setFocusedRunId(createdRun.run_id);
      setExpandedRunId(createdRun.run_id);
      await reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "验证运行创建失败");
    } finally {
      setSubmitting(false);
    }
  }

  async function cancelValidation(runId: string) {
    try {
      await api.post(`/api/capability-governance/validations/${encodeURIComponent(runId)}/cancel`);
      await reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "取消验证失败");
    }
  }

  // 管理员视图按成熟度与生命周期分组；空组不占位（#12 引入平台候选/管理员灰度分组）。
  const groups = ownerOnly
    ? [{ key: "owner", label: "", items: visibleItems }]
    : REVIEW_GROUPS
        .map((group) => ({
          key: group.key,
          label: group.label,
          items: visibleItems.filter(group.match),
        }))
        .filter((group) => group.items.length > 0);

  return (
    <Card>
      <CardHeader className="border-b border-border/60">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-4 w-4 text-primary" aria-hidden="true" />
          <h2 className="text-base font-semibold">{ownerOnly ? "我的能力验证" : "能力治理状态"}</h2>
        </div>
        <p className="text-xs text-muted-foreground">
          {ownerOnly
            ? "主动验证自己拥有的精确能力版本；一次业务成功不会自动改变成熟度。"
            : "按精确版本展示成熟度、生命周期和运行资格。管理员查看不会改变能力或用户权限。"}
        </p>
      </CardHeader>
      <CardContent className="space-y-3 pt-4">
        {loading ? (
          <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            正在读取治理状态
          </div>
        ) : error ? (
          <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
            {error}
          </div>
        ) : visibleItems.length === 0 ? (
          <div className="rounded-md border border-dashed p-6 text-center text-sm text-muted-foreground">
            当前没有可治理的能力包
          </div>
        ) : (
          groups.map((group) => (
          <section key={group.key} className="space-y-3">
            {group.label !== "" && (
              <h3 className="flex items-center gap-2 text-sm font-medium">
                {group.label}（{group.items.length}）
              </h3>
            )}
          {group.items.map((item) => {
            const supplyEvidence = supplyChainEvidence[itemKey(item)];
            const reviewItem = ownerOnly ? undefined : reviewItems[itemKey(item)];
            const matchingRuns = runs.filter((candidate) => (
              candidate.target.pack_id === item.pack_id
              && candidate.target.version === item.version
              && candidate.target.digest === item.digest
              && (item.scope === "platform" || candidate.owner_id === item.owner_id)
            ));
            const run = matchingRuns.find((candidate) => candidate.run_id === focusedRunId) ?? matchingRuns[0];
            const completedSteps = new Set(run?.evidence.map((evidence) => evidence.step) ?? []);
            const validationSteps = Object.keys(STEP_LABEL) as Array<keyof typeof STEP_LABEL>;
            const totalSteps = validationSteps.length;
            const hasAllValidationSteps = validationSteps.every((step) => completedSteps.has(step));
            return (
            <article
              key={itemKey(item)}
              className="rounded-lg border border-border/70 p-4"
            >
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <Boxes className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
                    <h3 className="truncate text-sm font-semibold">{item.pack_id}</h3>
                    <Badge variant="outline">{item.version}</Badge>
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">
                    {item.scope === "platform" ? "平台" : ownerOnly ? "个人能力" : `个人 · ${item.owner_id}`}
                  </p>
                </div>
                <div className="flex flex-wrap gap-1.5" aria-label="三轴治理状态">
                  <Badge variant={item.maturity === "verified" ? "success" : "outline"}>
                    {MATURITY_LABEL[item.maturity]}
                  </Badge>
                  <Badge variant={item.lifecycle === "active" ? "success" : "warning"}>
                    {LIFECYCLE_LABEL[item.lifecycle]}
                  </Badge>
                  <Badge variant={item.eligibility === "eligible" ? "success" : "danger"}>
                    {ELIGIBILITY_LABEL[item.eligibility]}
                  </Badge>
                </div>
              </div>
              <div className="mt-3 flex flex-wrap items-center justify-between gap-2 border-t border-border/60 pt-3 text-xs text-muted-foreground">
                <code>{item.digest ? shortDigest(item.digest) : "digest 已脱敏"}</code>
                <div className="flex items-center gap-2">
                  <span>{item.source === "legacy_compat" ? "兼容读取" : "治理事件"}</span>
                  {item.can_validate && (
                    <Button variant="outline" size="sm" onClick={() => openValidation(item)}>
                      <Play className="h-3.5 w-3.5" aria-hidden="true" />
                      发起验证
                    </Button>
                  )}
                </div>
              </div>
              {(item.promotion_gaps ?? []).length > 0 && (
                <div className="mt-3 rounded-md border border-border/60 bg-muted/20 px-3 py-2 text-xs">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="font-medium text-foreground">距已验证还缺</span>
                  </div>
                  <ul className="mt-1.5 list-inside list-disc space-y-0.5 text-muted-foreground">
                    {(item.promotion_gaps ?? []).map((gap) => (
                      <li key={gap}>{GAP_LABEL[gap] ?? gap}</li>
                    ))}
                  </ul>
                </div>
              )}
              <div className="mt-3 rounded-md border border-border/60 bg-muted/20 px-3 py-2 text-xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="font-medium text-foreground">供应链证据</span>
                  {supplyEvidence ? (
                    <Badge variant={supplyEvidence.status === "passed" ? "success" : "danger"}>
                      {supplyEvidence.status === "passed" ? "扫描通过" : "存在硬门"}
                    </Badge>
                  ) : (
                    <Badge variant="outline">尚未扫描</Badge>
                  )}
                </div>
                {supplyEvidence && (
                  <div className="mt-1.5 space-y-1 text-muted-foreground">
                    <p>
                      Trivy {supplyEvidence.trivy_version} · DB v{supplyEvidence.trivy_database.version} · DB 更新 {utcMinute(supplyEvidence.trivy_database.updated_at)} · Syft {supplyEvidence.syft_version} · CycloneDX {supplyEvidence.cyclonedx_spec_version}；
                      Secret {supplyEvidence.secret_count}，Critical {supplyEvidence.critical_count}，可修复 High {supplyEvidence.fixable_high_count}，配置失败 {supplyEvidence.misconfiguration_failure_count}
                    </p>
                    {supplyEvidence.blockers.length > 0 && (
                      <p className="text-destructive">
                        阻断原因：{supplyEvidence.blockers.map((blocker) => BLOCKER_LABEL[blocker]).join("、")}
                      </p>
                    )}
                  </div>
                )}
              </div>
              {run && (
                <details
                  key={run.run_id}
                  ref={run.run_id === focusedRunId ? runElementRef : undefined}
                  tabIndex={-1}
                  open={run.run_id === expandedRunId}
                  onToggle={(event) => {
                    if (event.currentTarget.open) setExpandedRunId(run.run_id);
                    else if (expandedRunId === run.run_id) setExpandedRunId("");
                  }}
                  className="mt-3 scroll-m-6 rounded-md border border-border/60 bg-muted/20 p-3 focus:outline-none focus:ring-2 focus:ring-primary/40"
                  aria-label={`验证进度与结果：${item.pack_id}`}
                >
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-2 text-sm font-medium">
                    <span>验证进度与结果 · {RUN_LABEL[run.status]} · 任务 {run.task_ref.task_id} V{run.task_ref.revision}</span>
                    <ChevronDown className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                  </summary>
                  <div className="mt-3 space-y-2">
                    <div role="status" aria-live="polite" className="rounded-md border border-primary/20 bg-primary/5 px-3 py-2 text-xs text-foreground">
                      {["queued", "running", "cancelling"].includes(run.status)
                        ? "验证正在后台执行，本页每 1.5 秒自动更新；你可以留在本页查看，也可以稍后返回能力治理。"
                        : run.status === "succeeded"
                          ? hasAllValidationSteps
                            ? "五项验证步骤已通过。全部证据通过后，该能力会自动晋级为已验证。"
                            : `该验证记录仅包含 ${completedSteps.size}/${totalSteps} 项，不能视为五项均通过。`
                          : run.status === "failed"
                            ? "验证未通过。请查看下方标记为“未通过”的步骤及其说明。"
                            : "验证已取消；下方仍保留已产生的步骤证据和资源清理结果。"}
                    </div>
                    {Object.entries(STEP_LABEL).map(([step, label]) => {
                      const evidence = run.evidence.find((item) => item.step === step);
                      return (
                        <div key={step} className="flex items-start justify-between gap-3 rounded border border-border/50 px-3 py-2 text-xs">
                          <div>
                            <div className="font-medium text-foreground">{label}</div>
                            <div className="mt-0.5 text-muted-foreground">{evidence?.summary ?? "尚未产生证据"}</div>
                          </div>
                          <Badge variant={evidence?.status === "passed" ? "success" : evidence?.status === "failed" ? "danger" : "outline"}>
                            {evidence ? (evidence.status === "passed" ? "通过" : evidence.status === "failed" ? "未通过" : "已取消") : "待执行"}
                          </Badge>
                        </div>
                      );
                    })}
                    {run.status !== "succeeded" && run.status !== "failed" && run.status !== "cancelled" && (
                      <div className="flex justify-end">
                        <Button variant="outline" size="sm" onClick={() => cancelValidation(run.run_id)}>
                          <X className="h-3.5 w-3.5" aria-hidden="true" />
                          取消验证
                        </Button>
                      </div>
                    )}
                    <p className="text-xs text-muted-foreground">
                      已完成 {completedSteps.size}/{totalSteps} 个步骤。一次业务成功不会自动改变能力成熟度。
                    </p>
                  </div>
                </details>
              )}
              {!ownerOnly && reviewItem?.task_metadata && (
                <details className="mt-3 rounded-md border border-border/60 bg-muted/20 p-3">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-2 text-sm font-medium">
                    <span>任务管理元数据</span>
                    <ChevronDown className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                  </summary>
                  <div className="mt-3 space-y-1 text-xs text-muted-foreground">
                    <p>
                      任务 {reviewItem.task_metadata.task_id} · V{reviewItem.task_metadata.revision} · 状态 {reviewItem.task_metadata.task_status} · 更新 {utcMinute(reviewItem.task_metadata.updated_at)}
                    </p>
                    <p>输入 {reviewItem.task_metadata.input_count} 个（{reviewItem.task_metadata.input_types.join("、") || "无"}）</p>
                    <p>输出 {reviewItem.task_metadata.output_count} 个（{reviewItem.task_metadata.output_formats.join("、") || "无"}）</p>
                  </div>
                </details>
              )}
              {!ownerOnly && reviewItem && reviewItem.audit_history.length > 0 && (
                <details className="mt-3 rounded-md border border-border/60 bg-muted/20 p-3">
                  <summary className="flex cursor-pointer list-none items-center justify-between gap-2 text-sm font-medium">
                    <span>审计记录（{reviewItem.audit_history.length}）</span>
                    <ChevronDown className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                  </summary>
                  <ul className="mt-3 list-inside list-disc space-y-0.5 text-xs text-muted-foreground">
                    {reviewItem.audit_history.map((record) => (
                      <li key={record.event_id}>
                        {record.actor_id} · {utcMinute(record.occurred_at)} · 任务 {record.task_id} V{record.revision} · {AUDIT_SUBJECT_LABEL[record.subject_type]} · {AUDIT_RESULT_LABEL[record.result]}{record.result === "failed" && record.failure_reason ? `（${FAILURE_REASON_LABEL[record.failure_reason] ?? record.failure_reason}）` : ""} · 原因：{record.reason}
                      </li>
                    ))}
                  </ul>
                </details>
              )}
              {!ownerOnly && reviewItem?.task_metadata && (
                <div className="mt-3 flex justify-end">
                  <Button variant="outline" size="sm" onClick={() => openAuditView(reviewItem)}>
                    <Eye className="h-3.5 w-3.5" aria-hidden="true" />
                    审计查看业务内容
                  </Button>
                </div>
              )}
            </article>
          );})}
          </section>
          ))
        )}
      </CardContent>
      <Modal open={target !== null} onClose={() => !submitting && setTarget(null)} title="发起能力验证">
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            选择一个曾冻结此精确能力版本并已正式交付的任务。平台会复核来源、输入、输出和授权，并使用该 TaskRevision 原已确认的模型连接重新执行，可能产生 Token 消耗；不会覆盖原任务或发布新结果。
          </p>
          <p className="rounded-md border border-primary/20 bg-primary/5 px-3 py-2 text-xs text-foreground">
            确认后弹窗会关闭，并自动定位到当前能力卡片下方的“验证进度与结果”；执行状态会在本页自动刷新。验证与供应链证据全部通过后，该能力会自动晋级为已验证。
          </p>
          {modalLoading ? (
            <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              正在查找可重放任务
            </div>
          ) : taskOptions.length === 0 ? (
            <div className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
              暂无符合条件的历史任务。请先创建一个真正需要该能力的任务，确保任务成功调用该能力并形成正式交付；仅选择但未调用的任务不能作为验证证据。
            </div>
          ) : (
            <label className="block space-y-1.5 text-sm">
              <span className="font-medium">真实任务证据</span>
              <select
                className="h-10 w-full rounded-md border border-input bg-background px-3"
                value={selectedTask}
                onChange={(event) => setSelectedTask(event.target.value)}
              >
                {taskOptions.map((option) => (
                  <option key={`${option.task_id}:${option.revision}`} value={`${option.task_id}::${option.revision}`}>
                    {option.title} · V{option.revision}
                  </option>
                ))}
              </select>
            </label>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setTarget(null)} disabled={submitting}>取消</Button>
            <Button onClick={submitValidation} disabled={!selectedTask || submitting || modalLoading}>
              {submitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
              确认并创建验证运行
            </Button>
          </div>
        </div>
      </Modal>
      <Modal open={auditTarget !== null} onClose={() => !auditSubmitting && setAuditTarget(null)} title="审计查看业务内容">
        <div className="space-y-4">
          {auditOutcome === null ? (
            <>
              <p className="rounded-md border border-primary/20 bg-primary/5 px-3 py-2 text-xs text-foreground">
                此操作会读取任务业务正文并写入不可变审计记录（记录 actor、时间、任务、对象、用途和结果）；不提供批量导出。
              </p>
              <label className="block space-y-1.5 text-sm">
                <span className="font-medium">查看对象</span>
                <select
                  className="h-10 w-full rounded-md border border-input bg-background px-3"
                  value={auditSubject}
                  onChange={(event) => setAuditSubject(event.target.value as AuditSubject)}
                >
                  {(Object.keys(AUDIT_SUBJECT_LABEL) as AuditSubject[]).map((key) => (
                    <option key={key} value={key}>{AUDIT_SUBJECT_LABEL[key]}</option>
                  ))}
                </select>
              </label>
              <label className="block space-y-1.5 text-sm">
                <span className="font-medium">查看原因</span>
                <textarea
                  className="min-h-20 w-full rounded-md border border-input bg-background px-3 py-2"
                  value={auditReason}
                  onChange={(event) => setAuditReason(event.target.value)}
                  placeholder="说明排障或审核用途，至少 5 个字符"
                />
              </label>
              <div className="flex justify-end gap-2">
                <Button variant="outline" onClick={() => setAuditTarget(null)} disabled={auditSubmitting}>取消</Button>
                <Button onClick={submitAuditView} disabled={auditReason.trim().length < 5 || auditSubmitting}>
                  {auditSubmitting && <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />}
                  确认查看并写入审计记录
                </Button>
              </div>
            </>
          ) : auditOutcome.status === "succeeded" ? (
            <>
              <div role="status" aria-live="polite" className="rounded-md border border-primary/20 bg-primary/5 px-3 py-2 text-xs text-foreground">
                审计记录已写入（{auditOutcome.event.event_id}）。
              </div>
              {auditOutcome.truncated && (
                <p className="text-xs text-muted-foreground">
                  内容超过单对象读取上限，以下只展示截断前段；hash 按截断后内容计算。
                </p>
              )}
              <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-md border border-border/60 bg-muted/20 px-3 py-2 text-xs">{auditOutcome.content}</pre>
              <div className="flex justify-end gap-2">
                <Button variant="outline" onClick={() => setAuditTarget(null)}>关闭</Button>
              </div>
            </>
          ) : (
            <>
              <div role="alert" className="rounded-md border border-destructive/40 bg-destructive/5 p-3 text-sm text-destructive">
                读取失败：{auditOutcome.failure_reason ? (FAILURE_REASON_LABEL[auditOutcome.failure_reason] ?? auditOutcome.failure_reason) : "未知原因"}；失败尝试已写入审计记录（{auditOutcome.event.event_id}）。
              </div>
              <div className="flex justify-end gap-2">
                <Button variant="outline" onClick={() => setAuditTarget(null)}>关闭</Button>
              </div>
            </>
          )}
        </div>
      </Modal>
    </Card>
  );
}
