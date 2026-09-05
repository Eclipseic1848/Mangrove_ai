import { useMemo, useRef, useState } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import { useQuery } from "@tanstack/react-query";
import {
  type ColumnDef,
  type SortingState,
  flexRender,
  getCoreRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  Download,
  FileArchive,
  FileCheck2,
  Loader2,
  RotateCcw,
  Search,
  ShieldCheck,
} from "lucide-react";
import { toast } from "sonner";
import { ApiError, downloadFile } from "@/lib/api";
import {
  downloadWorkspaceBundle,
  getWorkspacePreview,
} from "@/lib/semanticWorkspaceApi";
import { cn } from "@/lib/utils";
import { productText } from "@/lib/productText";
import type {
  DocumentPreviewItem,
  HistoricalAuthorityRecoveryConfirmation,
  LegacyRebaselineConfirmation,
  WorkspaceTask,
} from "@/types/semanticWorkspace";

const PAGE_SIZE = 100;

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 ** 2).toFixed(1)} MB`;
}

function QualityBadge({ warning }: { warning: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full px-2.5 py-1 text-xs font-medium",
        warning
          ? "bg-amber-500/10 text-amber-700 dark:text-amber-300"
          : "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300",
      )}
    >
      {warning ? (
        <AlertTriangle className="h-3.5 w-3.5" />
      ) : (
        <ShieldCheck className="h-3.5 w-3.5" />
      )}
      {warning ? "有警告" : "可交付"}
    </span>
  );
}

function candidateActionError(error: unknown) {
  const status = error instanceof ApiError ? error.status : null;
  if (status === 403) {
    return "当前账号没有操作权限。请使用任务所有者账号重新打开任务。";
  }
  if (status === 409) {
    return "任务、候选或验证状态已经变化。请刷新任务并核对最新状态后再操作。";
  }
  if (status === 422) {
    return "确认信息不完整或已经失效。请重新核对恢复证据和外发内容后再次确认。";
  }
  if (status === 503) {
    return "服务暂时不可用，本次操作尚未确认完成。请稍后重试。";
  }
  return error instanceof Error
    ? `操作未完成：${error.message}。请检查网络后重试。`
    : "操作未完成。请检查网络后重试。";
}

const REVERIFICATION_BLOCKER_TEXT: Record<string, string> = {
  task_revision_drift: "任务版本已经变化，请打开最新版本后再检查候选。",
  runtime_assignment_drift: "任务的执行配置已经变化，请创建新版本重新执行。",
  source_binding_drift: "来源绑定已经变化，请创建新版本重新执行。",
  provider_binding_forbidden: "当前模型连接不属于任务所有者，不能用于重验。",
  provider_binding_unavailable: "冻结的模型连接当前不可用，请先恢复连接。",
  authority_unavailable: "重验资格暂时无法核对，请稍后刷新。",
  p0_blocked: "生产安全门当前阻断了新的候选重验。",
  delivery_exists: "该候选已经形成正式交付，无需重复重验。",
  candidate_drift: "候选文件与冻结记录不一致，不能继续重验。",
  source_drift: "来源文件与冻结记录不一致，不能继续重验。",
  active_attempt: "已有一次候选重验正在执行，请先等待当前结果。",
  verification_attempt_active: "已有一次候选重验正在执行，请先等待当前结果。",
  outcome_unknown: "上一次外部模型请求结果不确定，请先核对状态和用量。",
  legacy_unversioned: "旧验证记录缺少可比较的规则身份，不能自动重验。",
  manifest_drift: "候选清单已经变化，不能继续重验。",
  goal_contract_drift: "任务目标已经变化，请创建新版本重新执行。",
  delivery_spec_drift: "输出要求已经变化，请创建新版本重新执行。",
  ruleset_unavailable: "当前验证规则暂时无法核对，请稍后刷新。",
  ruleset_unchanged: "验证规则没有变化，重复重验不会形成新的有效结论。",
  already_passed: "当前候选已经通过验证。",
  previous_cancelled: "上一次验证已取消，请先核对任务状态。",
  semantic_retry_unavailable: "文件、数量、契约或来源证据门未全部通过，不能只重跑语义验证。",
};

function reverificationBlockerText(blocker: string) {
  return REVERIFICATION_BLOCKER_TEXT[blocker]
    ?? "当前候选暂时不具备重新验证条件，请刷新任务后再检查。";
}

export function CandidatePreview({
  task,
  onRequestReverification,
  onPublishVerification,
  providerConnectionLabel,
}: {
  task: WorkspaceTask;
  onRequestReverification?: (
    externalApiConfirmed: boolean,
    historicalAuthorityRecovery?: HistoricalAuthorityRecoveryConfirmation,
    legacyRebaseline?: LegacyRebaselineConfirmation,
  ) => Promise<void>;
  onPublishVerification?: (attemptId: string) => Promise<void>;
  providerConnectionLabel?: string;
}) {
  const [retrying, setRetrying] = useState(false);
  const [publishing, setPublishing] = useState(false);
  const [egressConfirmed, setEgressConfirmed] = useState(false);
  const [historicalGapConfirmed, setHistoricalGapConfirmed] = useState(false);
  const [reverificationOnlyConfirmed, setReverificationOnlyConfirmed] = useState(false);
  const [legacyRulesUnknownConfirmed, setLegacyRulesUnknownConfirmed] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [reverificationDialogOpen, setReverificationDialogOpen] = useState(false);
  const [publishDialogOpen, setPublishDialogOpen] = useState(false);
  const cancelRef = useRef<HTMLButtonElement>(null);
  const publishCancelRef = useRef<HTMLButtonElement>(null);
  const candidates = task.agentic_runtime?.candidates || [];
  if (!candidates.length) return null;
  const verification = task.agentic_runtime?.verification;
  const verificationPassed = verification?.status === "passed";
  const verificationFailed = verification?.status === "failed";
  const offer = task.agentic_runtime?.reverification_offer;
  const historicalRecovery = offer?.historical_authority_recovery;
  const historicalRecoveryRequired = Boolean(
    historicalRecovery
      && offer?.blockers.length === 1
      && offer.blockers[0] === "historical_authority_recovery_required",
  );
  const reverificationUnavailableReason =
    task.agentic_runtime?.reverification_unavailable_reason;
  const attempt = task.agentic_runtime?.latest_verification_attempt;
  const attemptActive = attempt?.status === "requested" || attempt?.status === "running";
  const awaitingPublication = Boolean(
    task.agentic_runtime?.awaiting_publication
      && attempt?.status === "passed"
      && !task.agentic_runtime?.candidate_coverage?.is_partial,
  );
  const semanticRetry = offer?.reason === "semantic_inconclusive"
    || historicalRecovery?.purpose === "semantic_inconclusive_reverification";
  const legacyRebaseline = offer?.reason === "legacy_rebaseline";
  const canReverify = Boolean(
    (offer?.eligible || historicalRecoveryRequired)
      && attempt?.attempt_id
      && !attemptActive
      && attempt?.status !== "outcome_unknown"
      && !awaitingPublication
      && onRequestReverification,
  );
  const title = attemptActive
    ? semanticRetry
      ? "正在重跑候选语义验证"
      : "正在使用最新规则重新验证"
    : attempt?.status === "outcome_unknown"
      ? "重验结果尚无法确认"
      : awaitingPublication
        ? "候选重验已通过，等待正式发布"
          : verificationPassed
          ? "Mangrove 候选已通过独立验证"
          : verificationFailed
            ? "Mangrove 候选验证未通过"
            : "Mangrove 未验证候选";

  return (
    <section className="mx-auto w-full max-w-6xl px-4 pb-8">
      <div className="rounded-2xl border border-amber-500/25 bg-amber-500/[0.04] shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-3 border-b border-amber-500/20 p-5">
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-amber-600 dark:text-amber-300" />
              <h2 className="font-semibold">{title}</h2>
              <span className="rounded-full bg-amber-500/10 px-2 py-1 text-[11px] font-medium text-amber-700 dark:text-amber-300">
                不是正式交付
              </span>
            </div>
            <p className="mt-2 max-w-3xl text-xs leading-5 text-muted-foreground">
              {productText(verification?.summary ??
                "文件已通过非空、格式和重新打开检查，但尚未通过独立语义验证。")}
              {" "}系统不会把候选计为正式成功。
            </p>
            {verification?.checks?.length ? (
              <ul className="mt-3 space-y-1 text-xs text-muted-foreground">
                {verification.checks.map((check) => (
                  <li key={check.code}>
                    {check.passed ? "已通过" : "未通过"}：{productText(check.summary)}
                  </li>
                ))}
              </ul>
            ) : null}
            {reverificationUnavailableReason ? (
              <p
                role="status"
                className="mt-3 rounded-xl border border-amber-500/25 bg-background/70 px-3 py-2 text-xs leading-5 text-amber-800 dark:text-amber-200"
              >
                {reverificationUnavailableReason}
              </p>
            ) : null}
          </div>
          {awaitingPublication && attempt?.attempt_id && onPublishVerification ? (
            <AlertDialog.Root
              open={publishDialogOpen}
              onOpenChange={(open) => {
                if (!publishing) setPublishDialogOpen(open);
                if (open) setActionError(null);
              }}
            >
              <AlertDialog.Trigger asChild>
                <button
                  type="button"
                  onClick={() => setPublishDialogOpen(true)}
                  className="inline-flex h-10 shrink-0 items-center gap-2 rounded-xl bg-primary px-4 text-sm font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
                >
                  <FileCheck2 className="h-4 w-4" />
                  发布正式结果
                </button>
              </AlertDialog.Trigger>
              <AlertDialog.Portal>
                <AlertDialog.Overlay className="fixed inset-0 z-50 bg-slate-950/50" />
                <AlertDialog.Content
                  onOpenAutoFocus={(event) => {
                    event.preventDefault();
                    publishCancelRef.current?.focus();
                  }}
                  className="fixed left-1/2 top-1/2 z-50 w-[min(92vw,500px)] -translate-x-1/2 -translate-y-1/2 rounded-2xl border bg-background p-5 shadow-2xl sm:p-6"
                >
                  <AlertDialog.Title className="text-lg font-semibold">
                    发布正式结果？
                  </AlertDialog.Title>
                  <AlertDialog.Description className="mt-3 text-sm leading-6 text-muted-foreground">
                    将把验证尝试 {attempt.attempt_id} 对应的 {candidates.length} 个候选文件发布为正式交付。
                    发布会再次执行完整性与 QA 检查；不会重新运行任务或模型。
                  </AlertDialog.Description>
                  {actionError ? (
                    <div role="alert" className="mt-4 rounded-xl border border-destructive/25 bg-destructive/[0.05] p-3 text-sm text-destructive">
                      {actionError}
                    </div>
                  ) : null}
                  <div className="mt-5 flex justify-end gap-2">
                    <AlertDialog.Cancel asChild>
                      <button
                        ref={publishCancelRef}
                        type="button"
                        disabled={publishing}
                        onClick={() => setPublishDialogOpen(false)}
                        className="h-10 rounded-lg border px-4 text-sm font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
                      >
                        取消
                      </button>
                    </AlertDialog.Cancel>
                    <button
                      type="button"
                      disabled={publishing}
                      aria-busy={publishing}
                      onClick={async () => {
                        if (publishing) return;
                        setPublishing(true);
                        setActionError(null);
                        try {
                          await onPublishVerification(attempt.attempt_id!);
                          setPublishDialogOpen(false);
                          toast.success("正式结果已发布");
                        } catch (error) {
                          setActionError(candidateActionError(error));
                        } finally {
                          setPublishing(false);
                        }
                      }}
                      className="inline-flex h-10 min-w-32 items-center justify-center gap-2 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {publishing && <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />}
                      <span>{publishing ? "正在发布" : "确认发布"}</span>
                    </button>
                  </div>
                </AlertDialog.Content>
              </AlertDialog.Portal>
            </AlertDialog.Root>
          ) : canReverify ? (
            <AlertDialog.Root
              open={reverificationDialogOpen}
              onOpenChange={(open) => {
                if (!retrying) setReverificationDialogOpen(open);
                if (!open) {
                  setEgressConfirmed(false);
                  setHistoricalGapConfirmed(false);
                  setReverificationOnlyConfirmed(false);
                  setLegacyRulesUnknownConfirmed(false);
                }
                if (open) setActionError(null);
              }}
            >
              <AlertDialog.Trigger asChild>
                <button
                  type="button"
                  onClick={() => setReverificationDialogOpen(true)}
                  className="inline-flex h-10 shrink-0 items-center gap-2 rounded-xl border border-amber-500/30 bg-background px-3.5 text-sm font-medium text-amber-800 transition-colors hover:bg-amber-500/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 dark:text-amber-200"
                >
                  <RotateCcw className="h-4 w-4" />
                  {historicalRecoveryRequired
                    ? "恢复并重新验证候选"
                    : legacyRebaseline
                      ? "建立当前验证基线"
                    : semanticRetry
                      ? "只重跑语义验证"
                      : "使用最新规则重新验证"}
                </button>
              </AlertDialog.Trigger>
              <AlertDialog.Portal>
                <AlertDialog.Overlay className="fixed inset-0 z-50 bg-slate-950/50" />
                <AlertDialog.Content
                  onOpenAutoFocus={(event) => {
                    event.preventDefault();
                    cancelRef.current?.focus();
                  }}
                  className="fixed left-1/2 top-1/2 z-50 flex max-h-[min(90dvh,720px)] w-[min(92vw,560px)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border bg-background shadow-2xl"
                >
                  <div className="overflow-y-auto p-5 sm:p-6">
                    <AlertDialog.Title className="text-lg font-semibold">
                      {historicalRecoveryRequired
                        ? "恢复历史重验权威并重新验证？"
                        : legacyRebaseline
                          ? "为旧候选建立当前验证基线？"
                        : semanticRetry
                          ? "只重跑语义验证？"
                          : "重新验证现有候选？"}
                    </AlertDialog.Title>
                    <AlertDialog.Description asChild>
                      <div className="mt-3 space-y-4 text-sm leading-6 text-muted-foreground">
                        <p>
                          {historicalRecoveryRequired
                            ? "这条旧任务早于 RuntimeRouting 上线，系统会记录你现在对精确候选集的窄重验确认。不会补造旧 RuntimeAssignment，也不会重跑 Pi、生成文件或创建任务版本。"
                            : legacyRebaseline
                              ? "旧验证规则身份无法证明。本次只对同一冻结候选使用当前规则建立新的可信验证基线；不会重跑任务、不会修改候选，通过后仍需单独发布。"
                            : semanticRetry
                            ? "只会对冻结候选重新执行语义判断；不会重跑 Pi、生成新文件或创建任务版本，旧验证记录会继续保留。"
                            : "不会重新执行整个任务或生成新文件；旧验证记录会继续保留。"}
                        </p>
                        <div className="rounded-xl border bg-muted/25 p-3">
                          <p className="font-medium text-foreground">
                            {semanticRetry
                              ? `将重新核验 ${candidates.length} 个候选文件的冻结身份`
                              : `将重新检查 ${candidates.length} 个候选文件`}
                          </p>
                          <ul className="mt-1 list-disc pl-5">
                            {candidates.map((candidate) => (
                              <li key={candidate.artifact_id}>
                                <span>{candidate.filename}（{candidate.format.toUpperCase()}）</span>
                                <span className="mt-0.5 block break-all font-mono text-[11px] text-muted-foreground">
                                  Candidate ID：{candidate.artifact_id}
                                </span>
                                <span className="block break-all font-mono text-[11px] text-muted-foreground">
                                  SHA-256：{candidate.sha256}
                                </span>
                              </li>
                            ))}
                          </ul>
                        </div>
                        {historicalRecoveryRequired && historicalRecovery ? (
                          <div className="rounded-xl border border-amber-500/30 bg-amber-500/[0.05] p-3">
                            <p className="font-medium text-foreground">
                              需要恢复历史重验权威
                            </p>
                            <p className="mt-1">{historicalRecovery.explanation}</p>
                            <dl className="mt-3 grid gap-x-4 gap-y-1 text-xs sm:grid-cols-2">
                              <div>
                                <dt className="inline font-medium text-foreground">Owner：</dt>
                                <dd className="inline">{historicalRecovery.owner_id}</dd>
                              </div>
                              <div>
                                <dt className="inline font-medium text-foreground">任务：</dt>
                                <dd className="inline break-all">{historicalRecovery.task_id}</dd>
                              </div>
                              <div>
                                <dt className="inline font-medium text-foreground">版本：</dt>
                                <dd className="inline">V{historicalRecovery.revision}</dd>
                              </div>
                              <div>
                                <dt className="inline font-medium text-foreground">Run：</dt>
                                <dd className="inline break-all">{historicalRecovery.run_id}</dd>
                              </div>
                              <div className="sm:col-span-2">
                                <dt className="inline font-medium text-foreground">范围：</dt>
                                <dd className="inline">
                                  {offer?.candidate_count ?? candidates.length} 个候选 · {
                                    (offer?.candidate_formats?.length
                                      ? offer.candidate_formats
                                      : [...new Set(candidates.map((candidate) => candidate.format))]
                                    ).map((format) => format.toUpperCase()).join("、")
                                  }
                                </dd>
                              </div>
                            </dl>
                            <p className="mt-2 break-all font-mono text-[11px]">
                              Evidence SHA-256：{historicalRecovery.expected_evidence_hash}
                            </p>
                            <div className="mt-3 space-y-2">
                              <label className="flex cursor-pointer items-start gap-2 text-foreground">
                                <input
                                  type="checkbox"
                                  checked={historicalGapConfirmed}
                                  onChange={(event) => setHistoricalGapConfirmed(event.target.checked)}
                                  className="mt-1 h-4 w-4 rounded border-border accent-primary"
                                />
                                <span>我确认旧任务没有可证明的 RuntimeAssignment，系统不会补造这段历史</span>
                              </label>
                              <label className="flex cursor-pointer items-start gap-2 text-foreground">
                                <input
                                  type="checkbox"
                                  checked={reverificationOnlyConfirmed}
                                  onChange={(event) => setReverificationOnlyConfirmed(event.target.checked)}
                                  className="mt-1 h-4 w-4 rounded border-border accent-primary"
                                />
                                <span>我确认本授权只用于当前候选重验，不重跑 Pi、不创建版本、不发布</span>
                              </label>
                            </div>
                          </div>
                        ) : null}
                        {legacyRebaseline ? (
                          <div className="rounded-xl border border-amber-500/30 bg-amber-500/[0.05] p-3">
                            <p className="font-medium text-foreground">旧规则身份未知</p>
                            <p className="mt-1">
                              系统会保留旧的失败记录，并把本次授权绑定到当前候选集和当前验证规则。
                            </p>
                            <label className="mt-3 flex cursor-pointer items-start gap-2 text-foreground">
                              <input
                                type="checkbox"
                                checked={legacyRulesUnknownConfirmed}
                                onChange={(event) => setLegacyRulesUnknownConfirmed(event.target.checked)}
                                className="mt-1 h-4 w-4 rounded border-border accent-primary"
                              />
                              <span>我理解旧验证规则身份无法证明，本次将使用当前规则建立新的可信基线</span>
                            </label>
                          </div>
                        ) : null}
                        <div>
                          <p className="font-medium text-foreground">
                            {legacyRebaseline ? "当前验证基线" : semanticRetry ? "验证范围" : "规则变化"}
                          </p>
                          <p>
                            {semanticRetry
                              ? `复用已通过的确定性检查，不会重新读取 ${verification?.evidence_count ?? 0} 条来源证据，也不会执行完整 Verifier。`
                              : offer?.ruleset_change_summary}
                          </p>
                          {legacyRebaseline && offer?.target_ruleset_hash ? (
                            <p className="mt-1 break-all font-mono text-[11px]">
                              Target Ruleset SHA-256：{offer.target_ruleset_hash}
                            </p>
                          ) : null}
                        </div>
                        {offer?.requires_provider ? (
                          <div className="rounded-xl border border-amber-500/25 bg-amber-500/[0.05] p-3">
                            <p className="font-medium text-foreground">
                              本次会调用外部模型
                            </p>
                            <p>
                              连接：{providerConnectionLabel ?? "任务冻结的模型连接"}
                              {offer.model_id ? ` · 模型：${offer.model_id}` : ""}
                            </p>
                            <p>{offer.egress_summary}</p>
                            <p>外发类别：{offer.egress_categories.join("、")}</p>
                            <p>费用由 Provider 决定，当前可能未知或产生重复计费。</p>
                            <label className="mt-3 flex cursor-pointer items-start gap-2 text-foreground">
                              <input
                                type="checkbox"
                                checked={egressConfirmed}
                                onChange={(event) => setEgressConfirmed(event.target.checked)}
                                className="mt-1 h-4 w-4 rounded border-border accent-primary"
                              />
                              <span>我确认本次会向外部模型发送上述内容，并可能产生费用</span>
                            </label>
                          </div>
                        ) : (
                          <div className="rounded-xl border border-emerald-500/25 bg-emerald-500/[0.05] p-3 text-emerald-700 dark:text-emerald-300">
                            本次不外发，只在本地重新验证。
                          </div>
                        )}
                        <p>
                          验证通过后仍需由你单独点击“发布正式结果”；关闭此窗口不会中断后台验证。
                          如果 Provider 请求结果未知，平台会停止自动重发，请先核对 Provider 状态和用量。
                        </p>
                        {actionError ? (
                          <div role="alert" className="rounded-xl border border-destructive/25 bg-destructive/[0.05] p-3 text-destructive">
                            {actionError}
                          </div>
                        ) : null}
                      </div>
                    </AlertDialog.Description>
                  </div>
                  <div className="flex shrink-0 justify-end gap-2 border-t bg-background p-4 sm:px-6">
                    <AlertDialog.Cancel asChild>
                      <button
                        ref={cancelRef}
                        type="button"
                        onClick={() => setReverificationDialogOpen(false)}
                        className="h-10 rounded-lg border px-4 text-sm font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        取消
                      </button>
                    </AlertDialog.Cancel>
                      <button
                        type="button"
                        disabled={
                          retrying
                          || Boolean(offer?.requires_provider && !egressConfirmed)
                          || Boolean(
                            historicalRecoveryRequired
                            && (!historicalGapConfirmed || !reverificationOnlyConfirmed),
                          )
                          || Boolean(legacyRebaseline && !legacyRulesUnknownConfirmed)
                        }
                        aria-busy={retrying}
                        onClick={async () => {
                          if (!onRequestReverification || retrying) return;
                          setRetrying(true);
                          setActionError(null);
                          try {
                            await onRequestReverification(
                              Boolean(offer?.requires_provider && egressConfirmed),
                              historicalRecoveryRequired && historicalRecovery
                                ? {
                                    expected_evidence_hash:
                                      historicalRecovery.expected_evidence_hash,
                                    acknowledge_no_historical_assignment: true,
                                    acknowledge_reverification_only: true,
                                }
                                : undefined,
                              legacyRebaseline
                                && offer?.candidate_set_hash
                                && offer.target_ruleset_hash
                                ? {
                                    expected_candidate_set_hash: offer.candidate_set_hash,
                                    expected_target_ruleset_hash: offer.target_ruleset_hash,
                                    legacy_ruleset_unknown_acknowledged: true,
                                    authorization_text_version: "legacy-rebaseline-v1",
                                  }
                                : undefined,
                            );
                            setReverificationDialogOpen(false);
                            toast.success(
                              historicalRecoveryRequired
                                ? "已恢复历史重验权威并创建语义验证"
                                : legacyRebaseline
                                  ? "已创建当前规则验证基线"
                                : semanticRetry
                                  ? "已创建语义验证"
                                  : "已创建候选重验",
                            );
                          } catch (error) {
                            setActionError(candidateActionError(error));
                          } finally {
                            setRetrying(false);
                          }
                        }}
                        className="inline-flex h-10 min-w-32 items-center justify-center gap-2 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground disabled:cursor-not-allowed disabled:opacity-50"
                      >
                        {retrying && <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />}
                        <span>
                          {retrying
                            ? "正在提交"
                            : historicalRecoveryRequired
                              ? "恢复并开始语义验证"
                              : legacyRebaseline
                                ? "开始建立验证基线"
                              : semanticRetry
                              ? "开始语义验证"
                              : "开始重新验证"}
                        </span>
                      </button>
                  </div>
                </AlertDialog.Content>
              </AlertDialog.Portal>
            </AlertDialog.Root>
          ) : null}
        </div>
        {attemptActive ? (
          <div role="status" aria-live="polite" className="flex min-h-14 items-center gap-3 border-b border-amber-500/20 px-5 py-3 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 shrink-0 animate-spin motion-reduce:animate-none" />
            <span>
              {attempt?.status === "requested"
                ? "重验请求已受理，正在等待执行。刷新或关闭页面不会丢失进度。"
                : "正在重新检查候选文件与验证规则。进度以任务状态和时间线为准。"}
            </span>
          </div>
        ) : null}
        {attempt?.status === "outcome_unknown" ? (
          <div role="alert" className="border-b border-amber-500/25 bg-amber-500/[0.06] px-5 py-4 text-sm leading-6 text-amber-800 dark:text-amber-200">
            平台无法确认外部模型请求的最终结果，已停止自动重试。请先核对 Provider 状态和用量记录；不要直接重复发送。
          </div>
        ) : null}
        {awaitingPublication ? (
          <div role="status" aria-live="polite" className="border-b border-emerald-500/20 bg-emerald-500/[0.05] px-5 py-4 text-sm text-emerald-700 dark:text-emerald-300">
            最新验证尝试已通过，但还不是正式交付。请检查候选文件后，再单独发布正式结果。
          </div>
        ) : null}
        {offer && !offer.eligible && !historicalRecoveryRequired && !attemptActive && attempt?.status !== "outcome_unknown" && !awaitingPublication ? (
          <div className="border-b border-amber-500/20 px-5 py-4 text-sm leading-6 text-muted-foreground">
            <p className="font-medium text-foreground">当前不能重新验证</p>
            <ul className="mt-1 list-disc pl-5">
              {offer.blockers.map((blocker) => <li key={blocker}>{reverificationBlockerText(blocker)}</li>)}
            </ul>
          </div>
        ) : null}
        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,260px),1fr))] gap-3 p-5">
          {candidates.map((candidate) => (
            <div
              key={candidate.artifact_id}
              className="flex items-center gap-3 rounded-xl border bg-background p-3"
            >
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-amber-500/10 text-xs font-bold uppercase text-amber-700 dark:text-amber-300">
                {candidate.format === "markdown" ? "MD" : candidate.format}
              </div>
              <div className="min-w-0 flex-1">
                <button
                  type="button"
                  disabled={candidate.download_allowed === false}
                  onClick={() =>
                    void downloadFile(
                      candidate.download_url,
                      candidate.filename,
                    ).catch((error) =>
                      toast.error(
                        error instanceof Error ? error.message : "下载失败",
                      ),
                    )
                  }
                  className="block max-w-full truncate text-left text-sm font-medium hover:underline disabled:cursor-not-allowed disabled:no-underline"
                >
                  {candidate.filename}
                </button>
                <p className="mt-0.5 text-[11px] text-muted-foreground">
                  {candidate.download_allowed === false
                    ? "未通过安全检查 · 禁止下载"
                    : "未验证候选 · 可下载人工检查"}
                  {" · "}{formatBytes(candidate.size_bytes)}
                </p>
              </div>
              <button
                type="button"
                aria-label={`下载候选 ${candidate.filename}`}
                disabled={candidate.download_allowed === false}
                title={
                  candidate.download_allowed === false
                    ? "候选缺少可信来源或身份校验，已禁止下载"
                    : undefined
                }
                onClick={() =>
                  void downloadFile(
                    candidate.download_url,
                    candidate.filename,
                  ).catch((error) =>
                    toast.error(
                      error instanceof Error ? error.message : "下载失败",
                    ),
                  )
                }
                className="shrink-0 rounded-lg border p-2 text-muted-foreground hover:bg-muted hover:text-foreground disabled:cursor-not-allowed disabled:opacity-40"
              >
                <Download className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}

function VirtualTable({
  columns,
  rows,
  sorting,
  onSortingChange,
  onViewSource,
}: {
  columns: string[];
  rows: Array<Record<string, unknown>>;
  sorting: SortingState;
  onSortingChange: (state: SortingState) => void;
  onViewSource: (evidence: Record<string, unknown>) => void;
}) {
  const parentRef = useRef<HTMLDivElement>(null);
  const definitions = useMemo<ColumnDef<Record<string, unknown>>[]>(
    () => [
      ...columns.map<ColumnDef<Record<string, unknown>>>((column) => ({
        accessorKey: column,
        header: column,
        cell: (info) => {
          const value = info.getValue();
          return (
            <span title={String(value ?? "")}>
              {value === null || value === undefined ? "—" : String(value)}
            </span>
          );
        },
      })),
      {
        id: "__source",
        header: "来源",
        enableSorting: false,
        cell: ({ row }) => {
          const lineage = row.original.__lineage as
            | Array<Record<string, unknown>>
            | undefined;
          if (!lineage?.length) return <span className="text-muted-foreground">—</span>;
          return (
            <button
              type="button"
              onClick={() => onViewSource(lineage[0])}
              className="whitespace-nowrap text-xs font-medium text-primary hover:underline"
            >
              查看来源
            </button>
          );
        },
      },
    ],
    [columns, onViewSource],
  );
  const table = useReactTable({
    data: rows,
    columns: definitions,
    state: { sorting },
    manualSorting: true,
    onSortingChange: (updater) =>
      onSortingChange(
        typeof updater === "function" ? updater(sorting) : updater,
      ),
    getCoreRowModel: getCoreRowModel(),
  });
  const tableRows = table.getRowModel().rows;
  const virtualizer = useVirtualizer({
    count: tableRows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 42,
    overscan: 8,
  });

  return (
    <div className="overflow-hidden rounded-xl border">
      <div
        className="grid border-b bg-muted/50 text-xs font-medium"
        style={{
          gridTemplateColumns: `repeat(${columns.length}, minmax(150px, 1fr)) 90px`,
          minWidth: `${columns.length * 150 + 90}px`,
        }}
      >
        {table.getFlatHeaders().map((header) => (
          <button
            key={header.id}
            type="button"
            disabled={!header.column.getCanSort()}
            onClick={header.column.getToggleSortingHandler()}
            className="flex h-10 items-center gap-1 border-r px-3 text-left last:border-r-0 hover:bg-muted disabled:cursor-default"
          >
            {flexRender(header.column.columnDef.header, header.getContext())}
            {header.column.getIsSorted() === "asc" && <ArrowUp className="h-3 w-3" />}
            {header.column.getIsSorted() === "desc" && <ArrowDown className="h-3 w-3" />}
          </button>
        ))}
      </div>
      <div ref={parentRef} className="max-h-[460px] overflow-auto">
        <div
          className="relative"
          style={{
            height: `${virtualizer.getTotalSize()}px`,
            minWidth: `${columns.length * 150 + 90}px`,
          }}
        >
          {virtualizer.getVirtualItems().map((virtualRow) => {
            const row = tableRows[virtualRow.index];
            return (
              <div
                key={row.id}
                className="absolute left-0 top-0 grid w-full border-b bg-background text-xs hover:bg-muted/30"
                style={{
                  transform: `translateY(${virtualRow.start}px)`,
                  gridTemplateColumns: `repeat(${columns.length}, minmax(150px, 1fr)) 90px`,
                }}
              >
                {row.getVisibleCells().map((cell) => (
                  <div
                    key={cell.id}
                    className="flex h-[42px] min-w-0 items-center truncate border-r px-3 last:border-r-0"
                  >
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}

function DocumentResult({
  items,
  onViewSource,
}: {
  items: DocumentPreviewItem[];
  onViewSource: (evidence: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-3">
      {items.map((item) => (
        <article key={item.id} className="rounded-xl border bg-background p-4">
          <div className="flex items-start justify-between gap-4">
            <div>
              <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
                {item.type}
              </span>
              <h3 className="mt-1 text-sm font-semibold">{item.label}</h3>
            </div>
            {item.evidence_refs.length > 0 && (
              <button
                type="button"
                onClick={() => onViewSource(item.evidence_refs[0])}
                className="shrink-0 text-xs font-medium text-primary hover:underline"
              >
                查看来源
              </button>
            )}
          </div>
          <p className="mt-3 whitespace-pre-wrap text-sm leading-7 text-muted-foreground">
            {item.content}
          </p>
        </article>
      ))}
    </div>
  );
}

export function ResultPreview({
  task,
  onViewSource,
}: {
  task: WorkspaceTask;
  onViewSource: (evidence: Record<string, unknown>) => void;
}) {
  const [page, setPage] = useState(0);
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [sorting, setSorting] = useState<SortingState>([]);
  const [includeSources, setIncludeSources] = useState(false);
  const [bundleBusy, setBundleBusy] = useState(false);
  const preview = useQuery({
    queryKey: [
      "semantic-workspace-preview",
      task.task_id,
      task.viewing_revision,
      task.delivery?.delivery_id,
      page,
      search,
      sorting,
    ],
    queryFn: () =>
      getWorkspacePreview(task.task_id, {
        offset: page * PAGE_SIZE,
        limit: PAGE_SIZE,
        search,
        sortBy: sorting[0]?.id,
        sortDirection: sorting[0]?.desc ? "desc" : "asc",
        revision: task.viewing_revision,
      }),
    enabled: task.status === "completed",
  });
  const delivery = task.delivery;
  const hasWarnings = Boolean(
    delivery?.outputs.some((output) => output.qa.warnings?.length),
  );

  if (!delivery) return null;
  const total = preview.data?.total ?? 0;
  const start = total ? page * PAGE_SIZE + 1 : 0;
  const end = Math.min((page + 1) * PAGE_SIZE, total);

  return (
    <section className="mx-auto w-full max-w-6xl px-4 pb-8">
      <div className="rounded-2xl border bg-card shadow-sm">
        <div className="flex flex-wrap items-start justify-between gap-4 border-b p-5">
          <div className="min-w-0 flex-[1_1_320px]">
            <div className="flex flex-wrap items-center gap-2">
              <FileCheck2 className="h-5 w-5 text-primary" />
              <h2 className="font-semibold">结果与正式交付</h2>
              <QualityBadge warning={hasWarnings} />
            </div>
            <p className="mt-2 text-xs text-muted-foreground">
              已通过格式重开、文件大小和 SHA-256 校验。预览仅显示当前页，下载包含全部结果。
            </p>
          </div>
          <div className="flex flex-[1_1_260px] flex-wrap items-center justify-end gap-3">
            <label className="flex items-center gap-2 whitespace-nowrap text-xs text-muted-foreground">
              <input
                type="checkbox"
                checked={includeSources}
                onChange={(event) => setIncludeSources(event.target.checked)}
                className="h-4 w-4 rounded border"
              />
              ZIP 包含原始文件
            </label>
            <button
              type="button"
              disabled={bundleBusy}
              onClick={async () => {
                setBundleBusy(true);
                try {
                  await downloadWorkspaceBundle(
                    task.task_id,
                    `${task.title}.zip`,
                    includeSources,
                    task.viewing_revision,
                  );
                } catch (error) {
                  toast.error(
                    error instanceof Error ? error.message : "打包下载失败",
                  );
                } finally {
                  setBundleBusy(false);
                }
              }}
              className="inline-flex items-center gap-2 whitespace-nowrap rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground disabled:opacity-50"
            >
              {bundleBusy ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <FileArchive className="h-3.5 w-3.5" />
              )}
              下载全部
            </button>
          </div>
        </div>

        <div className="grid grid-cols-[repeat(auto-fit,minmax(min(100%,260px),1fr))] gap-3 border-b p-5">
          {delivery.outputs.map((output) => (
            <div
              key={output.output_id}
              className="flex items-center gap-3 rounded-xl border bg-background p-3"
            >
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-xs font-bold uppercase text-primary">
                {output.format === "markdown" ? "MD" : output.format}
              </div>
              <div className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{output.filename}</p>
                <p className="mt-0.5 flex items-center gap-1 text-[11px] text-muted-foreground">
                  <CheckCircle2 className="h-3 w-3 text-emerald-500" />
                  可打开 · {formatBytes(output.size_bytes)}
                </p>
              </div>
              <button
                type="button"
                aria-label={`下载 ${output.filename}`}
                onClick={() =>
                  void downloadFile(output.download_url, output.filename).catch(
                    (error) =>
                      toast.error(
                        error instanceof Error ? error.message : "下载失败",
                      ),
                  )
                }
                className="shrink-0 rounded-lg border p-2 text-muted-foreground hover:bg-muted hover:text-foreground"
              >
                <Download className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>

        <div className="p-5">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
            <div className="flex min-w-[280px] flex-1 items-center gap-2">
              <div className="relative max-w-md flex-1">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  value={searchInput}
                  onChange={(event) => setSearchInput(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") {
                      setPage(0);
                      setSearch(searchInput.trim());
                    }
                  }}
                  placeholder="在全部结果中搜索"
                  className="h-9 w-full rounded-lg border bg-background pl-9 pr-3 text-sm outline-none focus:border-primary"
                />
              </div>
              <button
                type="button"
                onClick={() => {
                  setPage(0);
                  setSearch(searchInput.trim());
                }}
                className="h-9 rounded-lg border px-3 text-xs font-medium hover:bg-muted"
              >
                搜索
              </button>
            </div>
            <div className="flex items-center gap-2 text-xs text-muted-foreground">
              <span>
                预览 {start}–{end}，共 {total} 条
              </span>
              <button
                type="button"
                disabled={page === 0}
                onClick={() => setPage((value) => Math.max(0, value - 1))}
                className="rounded border p-1.5 hover:bg-muted disabled:opacity-35"
              >
                <ChevronLeft className="h-3.5 w-3.5" />
              </button>
              <button
                type="button"
                disabled={end >= total}
                onClick={() => setPage((value) => value + 1)}
                className="rounded border p-1.5 hover:bg-muted disabled:opacity-35"
              >
                <ChevronRight className="h-3.5 w-3.5" />
              </button>
            </div>
          </div>

          {preview.isLoading && !preview.data ? (
            <div className="flex h-52 items-center justify-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
              正在读取结果预览
            </div>
          ) : preview.isError ? (
            <div className="rounded-xl border border-destructive/20 bg-destructive/[0.04] p-4 text-sm text-destructive">
              预览失败：{preview.error.message}
            </div>
          ) : preview.data?.kind === "table" ? (
            <VirtualTable
              columns={preview.data.columns}
              rows={preview.data.rows}
              sorting={sorting}
              onSortingChange={(state) => {
                setPage(0);
                setSorting(state.slice(0, 1));
              }}
              onViewSource={onViewSource}
            />
          ) : preview.data?.kind === "document" ? (
            <DocumentResult
              items={preview.data.items}
              onViewSource={onViewSource}
            />
          ) : null}
        </div>
      </div>
    </section>
  );
}
