import { useEffect, useRef, useState } from "react";
import { Boxes, ChevronDown, Loader2, Play, ShieldCheck, X } from "lucide-react";
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
  digest: string;
  can_validate: boolean;
};

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

type SupplyChainEvidence = {
  status: "passed" | "blocked";
  blockers: string[];
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

function shortDigest(digest: string) {
  if (digest.length <= 32) return digest;
  return `${digest.slice(0, 19)}…${digest.slice(-12)}`;
}

function itemKey(item: GovernanceItem) {
  return `${item.scope}:${item.owner_id ?? "platform"}:${item.pack_id}:${item.version}:${item.digest}`;
}

export function CapabilityGovernancePanel({ ownerOnly = false }: { ownerOnly?: boolean }) {
  const [items, setItems] = useState<GovernanceItem[]>([]);
  const [runs, setRuns] = useState<ValidationRun[]>([]);
  const [supplyChainEvidence, setSupplyChainEvidence] = useState<Record<string, SupplyChainEvidence | null>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [target, setTarget] = useState<GovernanceItem | null>(null);
  const [taskOptions, setTaskOptions] = useState<ValidationTaskOption[]>([]);
  const [selectedTask, setSelectedTask] = useState("");
  const [modalLoading, setModalLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [focusedRunId, setFocusedRunId] = useState("");
  const [expandedRunId, setExpandedRunId] = useState("");
  const runElementRef = useRef<HTMLDetailsElement | null>(null);

  const reload = () => Promise.all([
    api.get("/api/capability-governance/packs"),
    api.get("/api/capability-governance/validations"),
  ]).then(async ([packData, runData]) => {
    const nextItems: GovernanceItem[] = packData.items ?? [];
    setItems(nextItems);
    setRuns(runData.items ?? []);
    const evidencePairs = await Promise.all(nextItems.map(async (item) => {
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

  async function openValidation(item: GovernanceItem) {
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
          visibleItems.map((item) => {
            const supplyEvidence = supplyChainEvidence[itemKey(item)];
            const matchingRuns = runs.filter((candidate) => (
              candidate.target.pack_id === item.pack_id
              && candidate.target.version === item.version
              && candidate.target.digest === item.digest
              && (item.scope === "platform" || candidate.owner_id === item.owner_id)
            ));
            const run = matchingRuns.find((candidate) => candidate.run_id === focusedRunId) ?? matchingRuns[0];
            const completedSteps = new Set(run?.evidence.map((evidence) => evidence.step) ?? []);
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
                <code>{shortDigest(item.digest)}</code>
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
                  <p className="mt-1.5 text-muted-foreground">
                    Trivy {supplyEvidence.trivy_version} · DB v{supplyEvidence.trivy_database.version} · Syft {supplyEvidence.syft_version} · CycloneDX {supplyEvidence.cyclonedx_spec_version}；
                    Secret {supplyEvidence.secret_count}，Critical {supplyEvidence.critical_count}，可修复 High {supplyEvidence.fixable_high_count}，配置失败 {supplyEvidence.misconfiguration_failure_count}
                  </p>
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
                          ? "五项验证步骤已通过。该结果只形成验证证据，不会自动晋级或发布能力。"
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
                      已完成 {completedSteps.size}/5 个步骤。一次业务成功不会自动改变能力成熟度。
                    </p>
                  </div>
                </details>
              )}
            </article>
          );})
        )}
      </CardContent>
      <Modal open={target !== null} onClose={() => !submitting && setTarget(null)} title="发起能力验证">
        <div className="space-y-4">
          <p className="text-sm text-muted-foreground">
            选择一个曾冻结此精确能力版本并已正式交付的任务。平台会复核来源、输入、输出和授权，并使用该 TaskRevision 原已确认的模型连接重新执行，可能产生 Token 消耗；不会覆盖原任务或发布新结果。
          </p>
          <p className="rounded-md border border-primary/20 bg-primary/5 px-3 py-2 text-xs text-foreground">
            确认后弹窗会关闭，并自动定位到当前能力卡片下方的“验证进度与结果”；执行状态会在本页自动刷新。
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
    </Card>
  );
}
