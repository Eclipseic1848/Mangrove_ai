import { useEffect, useMemo, useState } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as Dialog from "@radix-ui/react-dialog";
import * as Collapsible from "@radix-ui/react-collapsible";
import {
  AlertCircle,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleStop,
  Clock3,
  FileCheck2,
  Globe2,
  Loader2,
  RotateCcw,
  Settings2,
  Square,
  Trash2,
  X,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { productText } from "@/lib/productText";
import type {
  StructuredProgressEvent,
  WorkspaceEvent,
  WorkspaceQuestion,
  WorkspaceTask,
} from "@/types/semanticWorkspace";
import { workspaceStatusLabel } from "./WorkspaceTaskSidebar";

const STAGE_LABELS: Record<string, string> = {
  queued: "等待执行",
  interpret: "理解要求",
  inspect: "读取来源",
  bind: "绑定字段",
  plan: "生成执行计划",
  execute: "处理数据",
  verify: "验证结果",
  repair: "自动修复",
  deliver: "正式交付",
  needs_input: "等待确认",
  cancelled: "任务取消",
  failed: "任务失败",
  goal_interpretation: "目标理解",
  source_probe: "来源识别",
  source_discovery: "候选发现",
  evidence_read: "证据精读",
  agent_execute: "Agent 处理",
  verify_coverage: "覆盖验证",
  understand: "理解要求",
  inspect_sources: "检查来源",
  prepare_capabilities: "准备能力",
};

const LEGACY_STAGE_ORDER = [
  "queued",
  "interpret",
  "inspect",
  "bind",
  "plan",
  "execute",
  "verify",
  "deliver",
] as const;

const AGENTIC_STAGE_ORDER = [
  "queued",
  "goal_interpretation",
  "source_probe",
  "source_discovery",
  "evidence_read",
  "agent_execute",
  "verify_coverage",
  "deliver",
] as const;

type MilestoneStatus =
  | "pending"
  | "active"
  | "waiting"
  | "completed"
  | "failed";

type TimelineMilestone = {
  stage: string;
  status: MilestoneStatus;
  summary: string;
  created_at: string | null;
};

type CapabilityReference = {
  name: string;
  kind: string;
  version: string;
  purpose: string;
};

const CAPABILITY_KIND_LABELS: Record<string, string> = {
  tool: "Tool",
  mcp_local: "MCP",
  mcp_remote: "MCP",
  skill: "Skill",
  dependency_bundle: "依赖包",
  capability_pack: "能力包",
};

function capabilityReferences(
  event: StructuredProgressEvent,
): CapabilityReference[] {
  const value = event.refs?.capabilities;
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is CapabilityReference => (
    typeof item === "object"
    && item !== null
    && typeof item.name === "string"
    && typeof item.kind === "string"
    && typeof item.version === "string"
    && typeof item.purpose === "string"
  ));
}

function normalizeStage(stage: string) {
  return stage === "needs_user" ? "needs_input" : stage;
}

function eventCompleted(event: WorkspaceEvent) {
  return (
    event.event_type.includes("completed")
    || [
      "delivery_published",
      "verification_passed",
      "repair_decided",
      "resumed",
    ].includes(event.event_type)
  );
}

function eventFailed(event: WorkspaceEvent) {
  return event.event_type.includes("failed");
}

function buildMilestones(
  events: WorkspaceEvent[],
  taskStatus: WorkspaceTask["status"],
): TimelineMilestone[] {
  const byStage = new Map<string, WorkspaceEvent[]>();
  events.forEach((event) => {
    const stage = normalizeStage(event.stage);
    byStage.set(stage, [...(byStage.get(stage) || []), event]);
  });

  const usesAgenticStages = events.some((event) => [
    "goal_interpretation",
    "source_probe",
    "source_discovery",
    "evidence_read",
    "agent_execute",
    "verify_coverage",
  ].includes(normalizeStage(event.stage)));
  const stageOrder: string[] = usesAgenticStages
    ? [...AGENTIC_STAGE_ORDER]
    : [...LEGACY_STAGE_ORDER];
  if (byStage.has("repair")) stageOrder.splice(stageOrder.length - 1, 0, "repair");
  if (byStage.has("needs_input") || taskStatus === "needs_input") {
    stageOrder.splice(stageOrder.length - 1, 0, "needs_input");
  }
  const observedIndexes = stageOrder.flatMap((stage, index) =>
    stage !== "needs_input" && byStage.has(stage) ? [index] : [],
  );
  const furthestObserved = observedIndexes.length
    ? Math.max(...observedIndexes)
    : -1;
  const unfinishedStage = (() => {
    for (let index = events.length - 1; index >= 0; index -= 1) {
      const event = events[index];
      if (!event.event_type.includes("started")) continue;
      const stage = normalizeStage(event.stage);
      const completedAfter = events.slice(index + 1).some(
        (candidate) =>
          normalizeStage(candidate.stage) === stage
          && eventCompleted(candidate),
      );
      if (!completedAfter) return stage;
    }
    return null;
  })();
  const unfinishedStageIndex = unfinishedStage
    ? stageOrder.indexOf(unfinishedStage)
    : -1;
  const currentUnfinishedStage =
    unfinishedStageIndex >= furthestObserved ? unfinishedStage : null;
  const activeStage = ["queued", "running"].includes(taskStatus)
    ? currentUnfinishedStage || (taskStatus === "queued" ? "queued" : null)
    : null;
  const taskFailure = [...events].reverse().find(eventFailed) || null;
  const failureStage =
    taskStatus === "failed" && taskFailure
      ? currentUnfinishedStage
      || (furthestObserved >= 0 ? stageOrder[furthestObserved] : null)
      || [...events]
        .reverse()
        .map((event) => normalizeStage(event.stage))
        .find((stage) => stageOrder.includes(stage))
        || "queued"
      : null;

  return stageOrder.map((stage, index) => {
    const stageEvents = byStage.get(stage) || [];
    const latest = stageEvents[stageEvents.length - 1] || null;
    let lastStartedIndex = -1;
    let completedIndex = -1;
    stageEvents.forEach((event, eventIndex) => {
      if (event.event_type.includes("started")) {
        lastStartedIndex = eventIndex;
      }
      if (eventCompleted(event)) completedIndex = eventIndex;
    });
    const explicitCompletion =
      completedIndex > lastStartedIndex
        ? stageEvents[completedIndex]
        : undefined;
    const failure =
      [...stageEvents].reverse().find(eventFailed)
      || (stage === failureStage ? taskFailure : null);
    const inferredCompletion =
      index < furthestObserved
      || (taskStatus === "completed" && index <= stageOrder.indexOf("deliver"));

    if (failure) {
      return {
        stage,
        status: "failed",
        summary: failure.summary,
        created_at: failure.created_at,
      };
    }
    if (stage === activeStage) {
      return {
        stage,
        status: "active",
        summary: latest?.summary || `正在${STAGE_LABELS[stage] || stage}`,
        created_at: latest?.created_at || null,
      };
    }
    if (explicitCompletion || inferredCompletion) {
      return {
        stage,
        status: "completed",
        summary:
          explicitCompletion?.summary
          || `${STAGE_LABELS[stage] || stage}已完成`,
        created_at: explicitCompletion?.created_at || latest?.created_at || null,
      };
    }
    if (stage === "needs_input" && taskStatus === "needs_input") {
      return {
        stage,
        status: "waiting",
        summary: latest?.summary || "等待你确认后继续执行",
        created_at: latest?.created_at || null,
      };
    }
    return {
      stage,
      status: "pending",
      summary: "尚未开始",
      created_at: null,
    };
  });
}

function formatTime(value: string) {
  return new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}

function formatElapsed(milliseconds: number) {
  if (milliseconds < 1000) return `${milliseconds} 毫秒`;
  return `${(milliseconds / 1000).toFixed(1)} 秒`;
}

function QuestionDialog({
  question,
  onAnswer,
}: {
  question: WorkspaceQuestion;
  onAnswer: (answer: string) => Promise<void>;
}) {
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [open, setOpen] = useState(true);

  useEffect(() => {
    setOpen(true);
    setAnswer("");
  }, [question.question_id]);

  const submit = async (value: string) => {
    if (!value.trim() || busy) return;
    setBusy(true);
    try {
      await onAnswer(value.trim());
      setOpen(false);
    } catch {
      setOpen(true);
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      {!open && (
        <Dialog.Trigger asChild>
          <button
            type="button"
            className="mb-5 flex w-full items-center justify-between rounded-xl border border-amber-500/25 bg-amber-500/[0.06] px-4 py-3 text-left text-sm text-amber-800 hover:bg-amber-500/10 dark:text-amber-200"
          >
            <span>任务正在等待你的确认</span>
            <span className="text-xs font-medium">继续回答</span>
          </button>
        </Dialog.Trigger>
      )}
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-slate-950/45 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(92vw,520px)] -translate-x-1/2 -translate-y-1/2 rounded-2xl border bg-background p-6 shadow-2xl outline-none">
          <div className="flex items-start justify-between gap-4">
            <div>
              <Dialog.Title className="text-base font-semibold">
                {question.kind === "external"
                  ? "确认外部数据处理"
                  : "需要确认一项信息"}
              </Dialog.Title>
              <Dialog.Description className="mt-2 text-sm leading-6 text-muted-foreground">
                {productText(question.prompt)}
              </Dialog.Description>
            </div>
            <Dialog.Close
              aria-label="稍后回答"
              className="rounded-lg p-1.5 text-muted-foreground hover:bg-muted"
            >
              <X className="h-4 w-4" />
            </Dialog.Close>
          </div>

          {question.kind === "external" && (
            <dl className="mt-4 grid gap-2 rounded-xl border bg-muted/30 p-4 text-xs">
              <div className="grid grid-cols-[86px_1fr] gap-2">
                <dt className="text-muted-foreground">目标服务</dt>
                <dd>{question.external_service || "外部 OpenAPI"}</dd>
              </div>
              <div className="grid grid-cols-[86px_1fr] gap-2">
                <dt className="text-muted-foreground">外发内容</dt>
                <dd>{question.outbound_data?.join("、") || "必要任务数据"}</dd>
              </div>
              <div className="grid grid-cols-[86px_1fr] gap-2">
                <dt className="text-muted-foreground">目的</dt>
                <dd>{productText(question.purpose)}</dd>
              </div>
              <div className="grid grid-cols-[86px_1fr] gap-2">
                <dt className="text-muted-foreground">风险</dt>
                <dd className="text-amber-700 dark:text-amber-300">
                  {productText(question.risk)}
                </dd>
              </div>
            </dl>
          )}

          {question.reason && question.kind !== "external" && (
            <div className="mt-4 rounded-xl bg-amber-500/10 px-3 py-2 text-xs text-amber-700 dark:text-amber-300">
              {productText(question.reason)}
              {question.affected_scope
                ? `，影响范围：${productText(question.affected_scope)}`
                : ""}
            </div>
          )}

          <div className="mt-5 grid gap-2">
            {question.options?.map((option) => (
              <button
                key={option.value}
                type="button"
                disabled={busy}
                onClick={() => void submit(option.value)}
                className="rounded-xl border px-4 py-3 text-left transition-colors hover:border-primary/40 hover:bg-primary/[0.04] disabled:opacity-50"
              >
                <span className="text-sm font-medium">{option.label}</span>
                {option.description && (
                  <span className="mt-1 block text-xs text-muted-foreground">
                    {option.description}
                  </span>
                )}
              </button>
            ))}
            {question.allow_free_text && (
              <div className="mt-1 flex gap-2">
                <input
                  value={answer}
                  onChange={(event) => setAnswer(event.target.value)}
                  onKeyDown={(event) => {
                    if (event.key === "Enter") void submit(answer);
                  }}
                  placeholder="输入你的补充说明"
                  className="h-10 flex-1 rounded-xl border bg-background px-3 text-sm outline-none focus:border-primary"
                />
                <button
                  type="button"
                  disabled={!answer.trim() || busy}
                  onClick={() => void submit(answer)}
                  className="rounded-xl bg-primary px-4 text-sm font-medium text-primary-foreground disabled:opacity-50"
                >
                  继续
                </button>
              </div>
            )}
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

function TimelineEvent({
  milestone,
  last,
}: {
  milestone: TimelineMilestone;
  last: boolean;
}) {
  const Icon =
    milestone.status === "failed"
      ? AlertCircle
      : milestone.status === "completed"
        ? Check
        : milestone.status === "active"
          ? Loader2
          : Clock3;
  return (
    <div
      data-testid="progress-stage"
      data-state={milestone.status}
      className="relative grid grid-cols-[28px_1fr] gap-3 pb-5"
    >
      {!last && (
        <div className="absolute left-[13px] top-7 h-[calc(100%-12px)] w-px bg-border" />
      )}
      <div
        className={cn(
          "relative z-10 flex h-7 w-7 items-center justify-center rounded-full border bg-background",
          milestone.status === "failed" && "border-destructive/30 text-destructive",
          milestone.status === "completed" && "border-emerald-500/30 text-emerald-600",
          milestone.status === "active" && "border-primary/25 text-primary",
          milestone.status === "waiting" && "border-amber-500/30 text-amber-600",
          milestone.status === "pending" && "border-border text-muted-foreground/50",
        )}
      >
        <Icon
          className={cn(
            "h-3.5 w-3.5",
            milestone.status === "active" && "animate-spin",
          )}
        />
      </div>
      <div className="min-w-0 pt-0.5">
        <div className="flex items-center justify-between gap-3">
          <span className="text-xs font-medium text-muted-foreground">
            {STAGE_LABELS[milestone.stage] || milestone.stage}
          </span>
          {milestone.created_at && (
            <time className="text-[10px] text-muted-foreground">
              {formatTime(milestone.created_at)}
            </time>
          )}
        </div>
        <p
          className={cn(
            "mt-1 text-sm leading-6",
            milestone.status === "pending" && "text-muted-foreground/60",
          )}
        >
          {productText(milestone.summary)}
        </p>
      </div>
    </div>
  );
}

export function TaskTimeline({
  task,
  liveEvents,
  onAnswer,
  onCancel,
  onRecycle,
  onRetry,
  onRefreshSource,
  onGapAction,
  onRevisionChange,
}: {
  task: WorkspaceTask;
  liveEvents: WorkspaceEvent[];
  onAnswer: (answer: string) => Promise<void>;
  onCancel: () => Promise<void>;
  onRecycle: () => Promise<void>;
  onRetry: (unchanged?: boolean) => void | Promise<void>;
  onRefreshSource: (externalApiConfirmed: boolean) => Promise<void>;
  onGapAction: (
    action: "accept_gap" | "reject_gap" | "supplement_source" | "refresh_source",
  ) => Promise<void>;
  onRevisionChange: (revision: number) => void;
}) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [progressOpen, setProgressOpen] = useState(false);
  const [eventsOpen, setEventsOpen] = useState(true);
  const [retryingUnknown, setRetryingUnknown] = useState(false);
  const [refreshingSource, setRefreshingSource] = useState(false);
  const [gapAction, setGapAction] = useState<string | null>(null);
  const events = useMemo(() => {
    const map = new Map<string, WorkspaceEvent>();
    [...(task.events || []), ...(task.harness_events || []), ...liveEvents].forEach(
      (event) => map.set(event.event_id, event),
    );
    return [...map.values()].sort(
      (a, b) =>
        new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
    );
  }, [liveEvents, task.events, task.harness_events]);
  const canCancel = ["queued", "running", "needs_input", "cancelling"].includes(
    task.status,
  );
  const milestones = useMemo(() => {
    if (!task.progress) return buildMilestones(events, task.status);
    return task.progress.stages.map((stage) => {
      const latest = [...task.progress!.events]
        .reverse()
        .find((event) => event.stage === stage.stage);
      return {
        stage: stage.stage,
        status: stage.status,
        summary: stage.summary,
        created_at: latest?.created_at || null,
      };
    });
  }, [events, task.progress, task.status]);
  const completedMilestones = milestones.filter(
    (milestone) => milestone.status === "completed",
  ).length;

  useEffect(() => {
    setProgressOpen(false);
    setEventsOpen(true);
  }, [task.task_id, task.status, task.viewing_revision]);

  const workSession = task.work_session;
  const ownerActionIndex = workSession?.entries.reduce(
    (latest, entry, index) => entry.event_type === "owner_action.requested" ? index : latest,
    -1,
  ) ?? -1;
  const ownerActionResolved = ownerActionIndex >= 0 && workSession?.entries
    .slice(ownerActionIndex + 1)
    .some((entry) => entry.recovery_status === "handled"
      || entry.recovery_status === "resumed"
      || ["resumed", "runtime.resuming", "question_answered", "question.answered", "revision.safe_point_applied"].includes(entry.event_type));
  const pendingOwnerAction = ownerActionIndex >= 0 && !ownerActionResolved
    ? workSession?.entries[ownerActionIndex]
    : undefined;
  const usageLabel = workSession
    ? `${workSession.usage.unknown_call_count > 0 ? "至少 " : ""}${workSession.usage.total_tokens.toLocaleString("zh-CN")} Tokens · ${workSession.usage.call_count} 次调用${workSession.usage.unknown_call_count > 0 ? ` · ${workSession.usage.unknown_call_count} 次未知` : ""}`
    : "0 Tokens · 0 次调用";
  const detailEventCount = workSession?.entries.length
    || task.progress?.events.length
    || 0;

  return (
    <section className="mx-auto w-full max-w-4xl px-6 py-6">
      {task.question && (
        <QuestionDialog question={task.question} onAnswer={onAnswer} />
      )}
      {task.web_source && (
        <div className="mb-4 flex flex-wrap items-start gap-3 border-b pb-4 text-sm">
          <Globe2 className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
          <div className="min-w-0 flex-1">
            <p className="font-medium">来源快照已冻结</p>
            <p className="mt-1 break-all text-xs leading-5 text-muted-foreground">
              {task.web_source.snapshot.artifacts[0]?.final_url}
              {" · "}
              {new Date(task.web_source.snapshot.created_at).toLocaleString("zh-CN")}
              {" · "}
              {task.web_source.snapshot.valid_page_count} 个有效页面
              {" · "}
              {task.web_source.snapshot.allowed_scope.kind === "same_site"
                ? `${task.web_source.snapshot.allowed_scope.site} 内最多 ${task.web_source.snapshot.allowed_scope.page_limit} 页`
                : "仅当前页"}
              {" · "}
              重试不会重新读取网页
            </p>
          </div>
          {task.viewing_revision === task.current_revision && (
            task.model_connection_id ? (
              <AlertDialog.Root>
                <AlertDialog.Trigger asChild>
                  <button
                    type="button"
                    disabled={refreshingSource}
                    className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    {refreshingSource
                      ? <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
                      : <RotateCcw className="h-3.5 w-3.5" />}
                    获取最新网页
                  </button>
                </AlertDialog.Trigger>
                <AlertDialog.Portal>
                  <AlertDialog.Overlay className="fixed inset-0 z-50 bg-slate-950/45" />
                  <AlertDialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(90vw,440px)] -translate-x-1/2 -translate-y-1/2 rounded-2xl border bg-background p-6 shadow-2xl">
                    <AlertDialog.Title className="font-semibold">获取最新网页并创建新版本？</AlertDialog.Title>
                    <AlertDialog.Description className="mt-2 text-sm leading-6 text-muted-foreground">
                      系统会沿用原来的站点和页数边界。只有新快照满足原完整性要求后，才会创建新版本，并把公开网页内容发送给当前模型连接；旧版本和旧 Run 保持不变。
                    </AlertDialog.Description>
                    <div className="mt-5 flex justify-end gap-2">
                      <AlertDialog.Cancel className="rounded-lg border px-3 py-2 text-sm">取消</AlertDialog.Cancel>
                      <AlertDialog.Action
                        onClick={() => {
                          setRefreshingSource(true);
                          void onRefreshSource(true).finally(() => setRefreshingSource(false));
                        }}
                        className="rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground"
                      >
                        确认获取并创建新版本
                      </AlertDialog.Action>
                    </div>
                  </AlertDialog.Content>
                </AlertDialog.Portal>
              </AlertDialog.Root>
            ) : (
              <button
                type="button"
                disabled={refreshingSource}
                onClick={() => {
                  setRefreshingSource(true);
                  void onRefreshSource(false).finally(() => setRefreshingSource(false));
                }}
                className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
              >
                {refreshingSource
                  ? <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" />
                  : <RotateCcw className="h-3.5 w-3.5" />}
                获取最新网页
              </button>
            )
          )}
        </div>
      )}
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span
              className={cn(
                "rounded-full px-2.5 py-1 text-xs font-medium",
                task.status === "completed"
                  ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
                  : task.status === "candidate_ready"
                    ? "bg-amber-500/10 text-amber-700 dark:text-amber-300"
                  : task.status === "failed"
                    ? "bg-destructive/10 text-destructive"
                    : task.status === "needs_input"
                      ? "bg-amber-500/10 text-amber-700 dark:text-amber-300"
                      : "bg-primary/10 text-primary",
              )}
            >
              {workspaceStatusLabel(task.status)}
            </span>
            {task.runtime_version && (
              <span className="rounded-full border bg-muted/40 px-2.5 py-1 text-xs font-medium text-muted-foreground">
                {task.runtime_version === "pi"
                  ? "实际执行：增强模式（Pi）"
                  : "实际执行：兼容模式（Legacy）"}
              </span>
            )}
            <label className="flex items-center gap-1 text-xs text-muted-foreground">
              <span className="sr-only">结果版本</span>
              <select
                value={task.viewing_revision || task.active_revision}
                onChange={(event) =>
                  onRevisionChange(Number(event.target.value))
                }
                className="rounded-md border bg-background px-2 py-1 text-xs text-foreground"
              >
                {(task.revisions || []).map((revision) => (
                  <option key={revision.revision} value={revision.revision}>
                    V{revision.revision}
                    {revision.revision === task.current_revision
                      ? "（最新）"
                      : ""}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <h1 className="mt-3 truncate text-xl font-semibold tracking-tight">
            {task.title}
          </h1>
          <p className="mt-2 max-w-2xl whitespace-pre-wrap text-sm leading-6 text-muted-foreground">
            {task.objective_text}
          </p>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {canCancel && task.status !== "cancelling" && (
            <AlertDialog.Root>
              <AlertDialog.Trigger asChild>
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium hover:bg-muted"
                >
                  <Square className="h-3.5 w-3.5" />
                  取消任务
                </button>
              </AlertDialog.Trigger>
              <AlertDialog.Portal>
                <AlertDialog.Overlay className="fixed inset-0 z-50 bg-slate-950/45" />
                <AlertDialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(90vw,440px)] -translate-x-1/2 -translate-y-1/2 rounded-2xl border bg-background p-6 shadow-2xl">
                  <AlertDialog.Title className="font-semibold">
                    确认取消当前任务？
                  </AlertDialog.Title>
                  <AlertDialog.Description className="mt-2 text-sm leading-6 text-muted-foreground">
                    后续处理将停止，未发布的临时文件会被清理；任务要求和取消记录仍会保留。
                  </AlertDialog.Description>
                  <div className="mt-5 flex justify-end gap-2">
                    <AlertDialog.Cancel className="rounded-lg border px-3 py-2 text-sm hover:bg-muted">
                      继续执行
                    </AlertDialog.Cancel>
                    <AlertDialog.Action
                      onClick={() => void onCancel()}
                      className="rounded-lg bg-destructive px-3 py-2 text-sm font-medium text-destructive-foreground"
                    >
                      确认取消
                    </AlertDialog.Action>
                  </div>
                </AlertDialog.Content>
              </AlertDialog.Portal>
            </AlertDialog.Root>
          )}
          {["completed", "candidate_ready", "failed", "cancelled"].includes(
            task.status,
          ) && (
            <AlertDialog.Root>
              <AlertDialog.Trigger asChild>
                <button
                  type="button"
                  className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium text-muted-foreground hover:bg-muted hover:text-foreground"
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  移入回收站
                </button>
              </AlertDialog.Trigger>
              <AlertDialog.Portal>
                <AlertDialog.Overlay className="fixed inset-0 z-50 bg-slate-950/45" />
                <AlertDialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(90vw,440px)] -translate-x-1/2 -translate-y-1/2 rounded-2xl border bg-background p-6 shadow-2xl">
                  <AlertDialog.Title className="font-semibold">
                    将这个任务移入回收站？
                  </AlertDialog.Title>
                  <AlertDialog.Description className="mt-2 text-sm leading-6 text-muted-foreground">
                    任务会从当前列表移除，并可在回收站保留期内恢复；生产审计和溯源记录不会被清除。
                  </AlertDialog.Description>
                  <div className="mt-5 flex justify-end gap-2">
                    <AlertDialog.Cancel className="rounded-lg border px-3 py-2 text-sm hover:bg-muted">
                      保留任务
                    </AlertDialog.Cancel>
                    <AlertDialog.Action
                      onClick={() => void onRecycle()}
                      className="rounded-lg bg-destructive px-3 py-2 text-sm font-medium text-destructive-foreground"
                    >
                      移入回收站
                    </AlertDialog.Action>
                  </div>
                </AlertDialog.Content>
              </AlertDialog.Portal>
            </AlertDialog.Root>
          )}
        </div>
      </div>

      {task.summary && (
        <div className="mt-6 rounded-2xl border bg-muted/20 p-4">
          <div className="flex items-center gap-2 text-sm font-medium">
            <FileCheck2 className="h-4 w-4 text-primary" />
            系统理解
          </div>
          <div className="mt-3 grid gap-1 text-sm leading-6 text-muted-foreground">
            {task.summary.split("\n").map((line) => (
              <p key={line}>{productText(line)}</p>
            ))}
          </div>
          {task.plan?.plan && (
            <Collapsible.Root open={detailsOpen} onOpenChange={setDetailsOpen}>
              <Collapsible.Trigger className="mt-3 flex items-center gap-1 text-xs font-medium text-primary">
                <Settings2 className="h-3.5 w-3.5" />
                高级计划详情
                <ChevronDown
                  className={cn(
                    "h-3.5 w-3.5 transition-transform",
                    detailsOpen && "rotate-180",
                  )}
                />
              </Collapsible.Trigger>
              <Collapsible.Content>
                <pre className="mt-3 max-h-72 overflow-auto rounded-xl bg-slate-950 p-4 text-[11px] leading-5 text-slate-200">
                  {JSON.stringify(task.plan.plan, null, 2)}
                </pre>
              </Collapsible.Content>
            </Collapsible.Root>
          )}
        </div>
      )}

      {task.agentic_runtime?.coverage && (
        <section
          aria-label="文档覆盖范围"
          className="mt-4 rounded-2xl border border-primary/20 bg-primary/[0.03] p-4"
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <p className="text-sm font-medium">本次查找范围</p>
              <p className="mt-1 text-sm leading-6 text-muted-foreground">
                {productText(task.agentic_runtime.coverage.contract.interpretation)}
              </p>
            </div>
            <span className="rounded-full border bg-background px-2.5 py-1 text-xs text-muted-foreground">
              {task.agentic_runtime.coverage.contract.completeness === "strict"
                ? "严格完整"
                : "尽力完成"}
            </span>
          </div>
            <div className="mt-3 grid grid-cols-2 gap-2 text-xs text-muted-foreground sm:grid-cols-5">
              <span>已发现 {task.agentic_runtime.coverage.progress.observed}/{task.agentic_runtime.coverage.progress.authorized}</span>
              <span>候选 {task.agentic_runtime.coverage.progress.candidates ?? 0} · 已精读 {task.agentic_runtime.coverage.progress.authoritatively_read}</span>
            <span>证据 {task.agentic_runtime.coverage.progress.evidence}</span>
            <span>未覆盖 {task.agentic_runtime.coverage.progress.unknown}</span>
            <span>缓存命中 {task.agentic_runtime.coverage.progress.cache_hits}</span>
          </div>
          <p className="mt-3 text-xs leading-5 text-muted-foreground">
            停止条件：{productText(task.agentic_runtime.coverage.contract.stop_semantics)}
          </p>
        </section>
      )}

      {task.agentic_runtime?.candidate_coverage?.is_partial && (
        <section
          aria-labelledby="partial-candidate-heading"
          data-testid="partial-candidate-panel"
          className="mt-4 border-l-2 border-amber-500 bg-amber-500/[0.04] px-4 py-4"
        >
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs font-medium uppercase tracking-[0.12em] text-amber-700 dark:text-amber-300">
                部分结果 · 尚非正式交付
              </p>
              <h2 id="partial-candidate-heading" className="mt-1 text-base font-semibold">
                {`已找到 ${task.agentic_runtime.candidate_coverage.actual_result_count} 项，目标是 ${task.agentic_runtime.candidate_coverage.target_result_count ?? "全部"} 项`}
              </h2>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-muted-foreground">
                {productText(task.agentic_runtime.candidate_coverage.conclusion?.reason
                  || "现有结果都有来源证据，但当前版本仍有未解决的覆盖缺口。")}
              </p>
            </div>
            <span className="rounded-full border border-amber-500/30 bg-background px-2.5 py-1 text-xs text-amber-700 dark:text-amber-300">
              {task.agentic_runtime.candidate_coverage.conclusion?.kind === "confirmed_omission"
                ? "确认漏提"
                : task.agentic_runtime.candidate_coverage.conclusion?.kind === "confirmed_scope_insufficient"
                  ? "确认本次范围不足"
                  : "覆盖无法判断"}
            </span>
          </div>
          <p className="mt-3 text-xs leading-5 text-muted-foreground">
            这 {task.agentic_runtime.candidate_coverage.actual_result_count} 项仍可在下方检查；
            原版本不会发布 Delivery。接受较少结果会创建新版本，旧要求和旧 Candidate 保持不变。
          </p>
          <ul className="mt-3 grid gap-x-5 gap-y-1 border-y py-3 text-xs sm:grid-cols-2">
            {task.agentic_runtime.candidate_coverage.result_items.map((item) => (
              <li key={item.result_id} className="flex min-w-0 items-center justify-between gap-3 py-1">
                <span className="truncate">{item.label || item.result_id}</span>
                <span className="shrink-0 text-muted-foreground">
                  {item.evidence_refs.length} 条证据
                </span>
              </li>
            ))}
          </ul>
          <div className="mt-4 flex flex-wrap gap-2">
            {task.agentic_runtime.candidate_coverage.actual_result_count > 0 ? <AlertDialog.Root>
              <AlertDialog.Trigger asChild>
                <button
                  type="button"
                  disabled={gapAction !== null}
                  className="inline-flex items-center rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                >
                  接受 {task.agentic_runtime.candidate_coverage.actual_result_count} 项并调整目标
                </button>
              </AlertDialog.Trigger>
              <AlertDialog.Portal>
                <AlertDialog.Overlay className="fixed inset-0 z-50 bg-slate-950/45" />
                <AlertDialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(90vw,440px)] -translate-x-1/2 -translate-y-1/2 rounded-2xl border bg-background p-6 shadow-2xl">
                  <AlertDialog.Title className="font-semibold">
                    接受当前部分结果并创建新版本？
                  </AlertDialog.Title>
                  <AlertDialog.Description className="mt-2 text-sm leading-6 text-muted-foreground">
                    新版本会把目标调整为当前 {task.agentic_runtime.candidate_coverage.actual_result_count} 项并重新验证。
                    原版本、Candidate 和缺口结论不会被修改。
                    {task.model_connection_id
                      ? " 新版本会继续使用当前模型连接并再次发送冻结内容，可能产生 Token 消耗。"
                      : ""}
                  </AlertDialog.Description>
                  <div className="mt-5 flex justify-end gap-2">
                    <AlertDialog.Cancel className="rounded-lg border px-3 py-2 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                      返回检查
                    </AlertDialog.Cancel>
                    <AlertDialog.Action
                      disabled={gapAction !== null}
                      onClick={() => {
                        setGapAction("accept_gap");
                        void onGapAction("accept_gap").finally(() => setGapAction(null));
                      }}
                      className="rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      确认创建新版本
                    </AlertDialog.Action>
                  </div>
                </AlertDialog.Content>
              </AlertDialog.Portal>
            </AlertDialog.Root> : (
              <p role="status" className="rounded-lg border border-amber-500/30 bg-background px-3 py-2 text-xs text-muted-foreground">
                当前没有可接受的有证据结果，请补充或刷新来源。
              </p>
            )}
            {([
              ["reject_gap", "不接受本次缺口"],
              ["supplement_source", "补充来源"],
              ["refresh_source", "刷新原来源"],
            ] as const).map(([action, label]) => (
              <button
                key={action}
                type="button"
                disabled={gapAction !== null}
                onClick={() => {
                  setGapAction(action);
                  void onGapAction(action).finally(() => setGapAction(null));
                }}
                className="rounded-lg border bg-background px-3 py-2 text-xs font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
              >
                {gapAction === action ? "处理中…" : label}
              </button>
            ))}
          </div>
        </section>
      )}

      {task.agentic_runtime?.candidate_coverage &&
        !task.agentic_runtime.candidate_coverage.is_partial &&
        (task.agentic_runtime.candidate_coverage.disclosure.failed_unit_count > 0 ||
          task.agentic_runtime.candidate_coverage.disclosure.unknown_unit_count > 0) && (
          <section className="mt-5 border-l-2 border-slate-300 pl-4 dark:border-slate-700">
            <p className="text-xs font-semibold uppercase tracking-[0.14em] text-muted-foreground">
              本次交付覆盖说明
            </p>
            <p className="mt-1 text-sm leading-6">
              实际形成 {task.agentic_runtime.candidate_coverage.actual_result_count} 项有证据结果；
              {task.agentic_runtime.candidate_coverage.disclosure.failed_unit_count > 0
                ? ` ${task.agentic_runtime.candidate_coverage.disclosure.failed_unit_count} 个获准页面读取失败；`
                : ""}
              {task.agentic_runtime.candidate_coverage.disclosure.unknown_unit_count > 0
                ? " 覆盖范围仍有无法判断的部分。"
                : ""}
            </p>
          </section>
        )}

      {pendingOwnerAction && (
        <div className="mt-5 border-l-2 border-amber-500 bg-amber-500/[0.06] px-4 py-3 text-sm" role="status">
          <p className="font-medium">需要你处理后才能继续</p>
          <p className="mt-1 text-xs leading-5 text-muted-foreground">
            {productText(pendingOwnerAction.summary)}
          </p>
        </div>
      )}

      <Collapsible.Root
        open={progressOpen}
        onOpenChange={setProgressOpen}
        className="mt-7"
      >
        <Collapsible.Trigger asChild>
          <button
            type="button"
            className="mb-4 flex w-full cursor-pointer items-center justify-between rounded-lg px-2 py-2 text-left hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          >
            <h2 className="text-sm font-semibold">工作记录</h2>
            <span className="flex flex-wrap items-center justify-end gap-x-2 gap-y-1 text-xs text-muted-foreground">
              <span>{workSession?.ended_at ? "已完成" : "进行中"}</span>
              {workSession?.started_at && <span>开始 {formatTime(workSession.started_at)}</span>}
              {workSession?.ended_at && <span>完成 {formatTime(workSession.ended_at)}</span>}
              <span>工作 {formatElapsed(workSession?.work_duration_ms || 0)}</span>
              <span>等待 {formatElapsed(workSession?.waiting_duration_ms || 0)}</span>
              <span>{workSession?.action_count || 0} 个行动</span>
              <span>{workSession?.tool_call_count || 0} 次工具</span>
              <span>{usageLabel}</span>
              <span>已处理 {workSession?.handled_retry_count || 0} 次重试</span>
              <ChevronDown
                className={cn(
                  "h-4 w-4 transition-transform motion-reduce:transition-none",
                  progressOpen && "rotate-180",
                )}
              />
            </span>
          </button>
        </Collapsible.Trigger>
        <Collapsible.Content>
          {events.length ? (
            milestones.map((milestone, index) => (
              <TimelineEvent
                key={milestone.stage}
                milestone={milestone}
                last={index === milestones.length - 1}
              />
            ))
          ) : (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <Clock3 className="h-4 w-4" />
              等待第一个执行事件
            </div>
          )}
          {(detailEventCount > 0 || (workSession?.provider_usage?.length || 0) > 0) && (
            <Collapsible.Root
              open={eventsOpen}
              onOpenChange={setEventsOpen}
              className="mt-1 border-t pt-3"
            >
              <Collapsible.Trigger className="flex w-full cursor-pointer items-center justify-between rounded-lg px-2 py-2 text-left text-xs font-medium text-muted-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                <span>行动记录（{detailEventCount}）</span>
                <ChevronDown
                  className={cn(
                    "h-3.5 w-3.5 transition-transform motion-reduce:transition-none",
                    eventsOpen && "rotate-180",
                  )}
                />
              </Collapsible.Trigger>
              <Collapsible.Content>
                {(workSession?.provider_usage?.length || 0) > 0 && (
                  <div className="mb-3 border-b pb-3 text-xs leading-5">
                    <p className="font-medium">模型调用</p>
                    {workSession?.provider_usage?.map((usage, index) => (
                      <p
                        key={`${usage.run_id}:${usage.purpose}:${index}`}
                        className="mt-1 text-muted-foreground"
                      >
                        {usage.model} · {usage.purpose} · {usage.total_tokens === null
                          ? "Token 未知"
                          : `${usage.total_tokens.toLocaleString("zh-CN")} Tokens`}
                        {usage.cache_tokens === null
                          ? " · 缓存用量未知"
                          : ` · 缓存 ${usage.cache_tokens.toLocaleString("zh-CN")}`}
                      </p>
                    ))}
                  </div>
                )}
                {workSession?.entries.length ? (
                  <ol className="mb-3 space-y-2 border-b pb-3">
                    {workSession.entries.map((entry) => (
                      <li key={entry.event_id} className="grid gap-1 text-xs leading-5 sm:grid-cols-[72px_1fr]">
                        <time className="text-muted-foreground">{formatTime(entry.created_at)}</time>
                        <div>
                          <p>{productText(entry.summary)}</p>
                          <p className="text-muted-foreground">
                            {[entry.tool_name, entry.purpose, entry.duration_ms !== null ? formatElapsed(entry.duration_ms) : null, entry.result_summary]
                              .filter(Boolean).join(" · ")}
                          </p>
                          {entry.input_summary && (
                            <p className="text-muted-foreground">输入：{productText(entry.input_summary)}</p>
                          )}
                          {entry.evidence_refs.length > 0 && (
                            <p className="text-muted-foreground">证据：{entry.evidence_refs.join("、")}</p>
                          )}
                          {entry.recovery_status && (
                            <p className="text-muted-foreground">恢复状态：{entry.recovery_status}</p>
                          )}
                        </div>
                      </li>
                    ))}
                  </ol>
                ) : null}
                <ol className="mt-2 space-y-2 pl-2">
                  {task.progress?.events.map((event) => {
                    const capabilities = capabilityReferences(event);
                    const showLegacyEvent = !workSession?.entries.length;
                    if (!showLegacyEvent && capabilities.length === 0) return null;
                    return (
                      <li
                        key={event.event_id}
                        className="grid grid-cols-[58px_1fr] gap-3 text-xs leading-5"
                      >
                        <time className="text-muted-foreground">
                          {formatTime(event.created_at)}
                        </time>
                        <div>
                          {showLegacyEvent && <p>{productText(event.summary)}</p>}
                          {showLegacyEvent && event.progress && (
                            <p className="text-muted-foreground">
                              已处理 {event.progress.current}
                              {event.progress.total !== null
                                ? `/${event.progress.total}`
                                : ""}
                              {` ${event.progress.unit}`}
                            </p>
                          )}
                          {capabilities.length > 0 && (
                            <div className="mt-2 grid gap-2 sm:grid-cols-2">
                              {capabilities.map((capability) => (
                                <div
                                  key={`${capability.kind}:${capability.name}:${capability.version}`}
                                  className="rounded-lg border bg-muted/30 px-3 py-2"
                                >
                                  <div className="flex items-center gap-2">
                                    <Settings2 className="h-3.5 w-3.5 text-primary" />
                                    <span className="font-medium">{capability.name}</span>
                                    <span className="rounded border px-1.5 text-[10px] text-muted-foreground">
                                      {CAPABILITY_KIND_LABELS[capability.kind] || capability.kind}
                                    </span>
                                    <span className="text-[10px] text-muted-foreground">
                                      v{capability.version}
                                    </span>
                                  </div>
                                  <p className="mt-1 text-muted-foreground">
                                    {productText(capability.purpose)}
                                  </p>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      </li>
                    );
                  })}
                </ol>
              </Collapsible.Content>
            </Collapsible.Root>
          )}
        </Collapsible.Content>
      </Collapsible.Root>

      {task.status === "failed" && (
        <div
          data-testid="task-failure-explanation"
          className="mt-3 rounded-2xl border border-destructive/20 bg-destructive/[0.04] p-4"
        >
          <div className="flex items-start gap-3">
            <AlertCircle className="mt-0.5 h-5 w-5 text-destructive" />
            <div className="min-w-0 flex-1">
              <h3 className="text-sm font-semibold">任务未完成</h3>
              <p className="mt-1 text-sm leading-6 text-muted-foreground">
                {productText(task.failure?.cause_summary
                  || task.error
                  || "执行未通过最终验证。")}
              </p>
              {task.failure ? (
                <div className="mt-3 space-y-2 text-xs leading-5 text-muted-foreground">
                  <p>
                    失败阶段：
                    {STAGE_LABELS[task.failure.stage] || task.failure.stage}
                    {" · "}
                    共尝试 {task.failure.attempt_count} 次
                    {" · "}
                    耗时 {formatElapsed(task.failure.elapsed_ms)}
                  </p>
                  <p>
                    {task.failure.source_read
                      ? "已读取原始资料"
                      : "尚未读取原始资料"}
                    {" · "}
                    {task.failure.intermediate_created
                      ? "已生成中间结果"
                      : "未生成中间结果"}
                    {" · "}
                    {task.failure.delivery_published
                      ? "已发布正式交付"
                      : "未发布正式交付"}
                  </p>
                  <div>
                    <span>建议：</span>
                    <ul className="mt-1 list-disc pl-5">
                      {task.failure.next_actions.map((action) => (
                        <li key={action}>{productText(action)}</li>
                      ))}
                    </ul>
                  </div>
                  <p>错误代码：{task.failure.error_code}</p>
                </div>
              ) : (
                <p className="mt-1 text-xs text-muted-foreground">
                  影响：没有发布新的正式交付，已有历史版本不受影响。
                </p>
              )}
              {task.failure?.error_code === "MODEL_OUTCOME_UNKNOWN" ? (
                <AlertDialog.Root>
                  <AlertDialog.Trigger asChild>
                    <button
                      type="button"
                      className="mt-3 inline-flex items-center gap-1.5 rounded-lg border bg-background px-3 py-2 text-xs font-medium hover:bg-muted"
                    >
                      <RotateCcw className="h-3.5 w-3.5" />
                      重新执行
                    </button>
                  </AlertDialog.Trigger>
                  <AlertDialog.Portal>
                    <AlertDialog.Overlay className="fixed inset-0 z-50 bg-slate-950/45" />
                    <AlertDialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(90vw,440px)] -translate-x-1/2 -translate-y-1/2 rounded-2xl border bg-background p-6 shadow-2xl">
                      <AlertDialog.Title className="font-semibold">
                        是否重新执行？
                      </AlertDialog.Title>
                      <AlertDialog.Description className="mt-2 text-sm leading-6 text-muted-foreground">
                        平台无法确认模型是否已经收到上一次请求。重新执行会创建新版本，
                        并可能产生重复调用和费用。
                      </AlertDialog.Description>
                      <div className="mt-5 flex justify-end gap-2">
                        <AlertDialog.Cancel className="rounded-lg border px-3 py-2 text-sm hover:bg-muted">
                          取消
                        </AlertDialog.Cancel>
                        <AlertDialog.Action
                          disabled={retryingUnknown}
                          onClick={() => {
                            setRetryingUnknown(true);
                            void Promise.resolve(onRetry(true)).finally(() => {
                              setRetryingUnknown(false);
                            });
                          }}
                          className="rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-50"
                        >
                          {retryingUnknown ? "正在创建新版本" : "确认重新执行"}
                        </AlertDialog.Action>
                      </div>
                    </AlertDialog.Content>
                  </AlertDialog.Portal>
                </AlertDialog.Root>
              ) : (
                <button
                  type="button"
                  onClick={() => void onRetry(false)}
                  className="mt-3 inline-flex items-center gap-1.5 rounded-lg border bg-background px-3 py-2 text-xs font-medium hover:bg-muted"
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                  修改要求后重试
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {task.status === "cancelled" && (
        <div className="mt-3 flex items-center gap-3 rounded-2xl border bg-muted/25 p-4 text-sm text-muted-foreground">
          <CircleStop className="h-5 w-5" />
          任务已取消，未发布新的正式交付。你可以从原要求创建新版本。
        </div>
      )}

      {task.status === "completed" && (
        <div className="mt-3 flex items-center gap-3 rounded-2xl border border-emerald-500/20 bg-emerald-500/[0.05] p-4 text-sm text-emerald-700 dark:text-emerald-300">
          <CheckCircle2 className="h-5 w-5" />
          最终验证和格式重开检查已完成，可以预览或下载。
        </div>
      )}
      {task.status === "candidate_ready" && (
        <div className="mt-3 flex items-start gap-3 rounded-2xl border border-amber-500/20 bg-amber-500/[0.05] p-4 text-sm text-amber-700 dark:text-amber-300">
          <AlertCircle className="mt-0.5 h-5 w-5 shrink-0" />
          <div>
            <p className="font-medium">Mangrove 已生成候选，但尚未成为正式交付</p>
            <p className="mt-1 text-xs leading-5 text-muted-foreground">
              候选文件可重新打开。请在下方下载检查，或创建新版本修改目标；
              独立语义验证通过前，系统不会显示“已完成”。
            </p>
          </div>
        </div>
      )}
    </section>
  );
}
