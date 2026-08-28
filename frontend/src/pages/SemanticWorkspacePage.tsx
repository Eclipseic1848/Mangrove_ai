import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { nanoid } from "nanoid/non-secure";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as Dialog from "@radix-ui/react-dialog";
import { Group, Panel, Separator } from "react-resizable-panels";
import {
  BookOpen,
  ArrowLeft,
  ChevronDown,
  FileSearch,
  HelpCircle,
  LayoutTemplate,
  Loader2,
  RotateCcw,
  Search,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { isAdminish, useAuth } from "@/lib/auth";
import { TaskComposer } from "@/components/workspace/TaskComposer";
import {
  CandidatePreview,
  ResultPreview,
} from "@/components/workspace/ResultPreview";
import { SourcePreviewPanel } from "@/components/workspace/SourcePreviewPanel";
import { TaskTimeline } from "@/components/workspace/TaskTimeline";
import { WorkspaceTaskSidebar } from "@/components/workspace/WorkspaceTaskSidebar";
import { WebSourceIntake } from "@/components/workspace/WebSourceIntake";
import {
  answerWorkspaceTask,
  cancelWorkspaceTask,
  createWorkspaceRevision,
  createWorkspaceTask,
  decideWorkspaceRevision,
  getWorkspaceGuidance,
  getWorkspaceStorage,
  getWorkspaceTask,
  listGrayCapabilities,
  listWorkspaceTasks,
  permanentlyDeleteWorkspaceTask,
  recycleWorkspaceTask,
  requestCandidateReverification,
  publishCandidateVerification,
  refreshWorkspaceSource,
  restoreWorkspaceTask,
  sendWorkspaceTurn,
  streamWorkspaceTask,
} from "@/lib/semanticWorkspaceApi";
import type { GrayCapability } from "@/lib/semanticWorkspaceApi";
import { cn } from "@/lib/utils";
import type {
  UploadItem,
} from "@/types/dataPrep";
import type {
  WorkspaceEvent,
  WorkspaceGuidance,
  SteeringResult,
  WorkspaceTask,
} from "@/types/semanticWorkspace";

const ONBOARDING_KEY = "mangrove_workspace_onboarding_complete";
type ModelOption = { provider: string; model: string; label: string };
type ModelsResponse = {
  options: ModelOption[];
  default: ModelOption | null;
  pi_runtime_enabled: boolean;
  pi_capability_host_enabled: boolean;
};
type ModelConnection = {
  connection_id: string;
  owner_scope: "user_personal" | "platform_shared";
  preset_id?: string | null;
  display_name: string;
  model: string;
  api_format: string;
  locality: string;
  status: string;
  default_model?: string | null;
  models?: Array<{
    model_id: string;
    display_name: string;
    status: string;
    enabled: boolean;
  }>;
};
type ModelConnectionsResponse = { items: ModelConnection[] };

function taskRecoveryError(error: unknown) {
  if (!(error instanceof ApiError)) {
    return "任务详情暂时无法读取，请稍后重新加载。";
  }
  if (error.status === 401) return "登录状态已失效，请重新登录后再打开任务。";
  if (error.status === 403) return "当前账号没有读取这条任务的权限。";
  if (error.status === 404) return "这条任务已不存在或已被移出当前列表。";
  if (error.status >= 500) return "历史任务详情暂时无法读取，请稍后重新加载。";
  return error.message;
}

function FollowupComposer({
  task,
  onSubmit,
  onDecision,
}: {
  task: WorkspaceTask;
  onSubmit: (text: string, idempotencyKey: string) => Promise<SteeringResult>;
  onDecision: (
    proposalId: string,
    mode: "cancel_now" | "after_safe_point" | "new_task",
    externalApiConfirmed: boolean,
  ) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [result, setResult] = useState<SteeringResult | null>(null);
  const [externalConfirmed, setExternalConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const usesExternalConnection = Boolean(task.model_connection_id);
  useEffect(() => {
    setText("");
    setResult(null);
    setExternalConfirmed(false);
  }, [task.task_id]);
  const submit = async () => {
    if (!text.trim() || busy) return;
    setBusy(true);
    try {
      const next = await onSubmit(text.trim(), nanoid());
      setResult(next);
      setText("");
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="rounded-2xl border bg-background p-3 shadow-[0_14px_45px_-32px_hsl(var(--primary)/0.7)]">
      <textarea
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
            event.preventDefault();
            void submit();
          }
        }}
        rows={2}
        placeholder="可询问进度和原因，也可提出修改；系统会先说明是否影响当前任务"
        className="w-full resize-none bg-transparent px-1 text-sm leading-6 outline-none placeholder:text-muted-foreground/70"
      />
      <div className="mt-2 flex items-center gap-3 border-t pt-2">
        <span className="text-[11px] text-muted-foreground">
          Ctrl + Enter 发送
        </span>
        <button
          type="button"
          disabled={!text.trim() || busy}
          onClick={() => void submit()}
          className="ml-auto rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground disabled:opacity-45"
        >
          {busy ? "正在理解" : "发送"}
        </button>
      </div>
      {result && (
        <div
          aria-live="polite"
          className="mt-3 rounded-xl border bg-muted/25 px-3 py-2 text-xs leading-5"
        >
          <p className="font-medium">{result.acknowledgement}</p>
          {result.answer && (
            <p className="mt-1 text-muted-foreground">{result.answer}</p>
          )}
          {result.action === "revision_proposal" && result.proposal_id && (
            <div className="mt-3">
              {usesExternalConnection && (
                <label className="mb-3 flex items-start gap-2 rounded-lg border bg-background p-2.5 text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={externalConfirmed}
                    onChange={(event) => setExternalConfirmed(event.target.checked)}
                    className="mt-0.5"
                  />
                  <span>我确认新版本或独立任务仍会把当前任务范围内的数据发送到已选外部模型连接。</span>
                </label>
              )}
              <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={usesExternalConnection && !externalConfirmed}
                onClick={() => void onDecision(result.proposal_id!, "after_safe_point", externalConfirmed)}
                className="rounded-lg bg-primary px-3 py-2 font-medium text-primary-foreground"
              >
                当前步骤结束后切换
              </button>
              <button
                type="button"
                disabled={usesExternalConnection && !externalConfirmed}
                onClick={() => void onDecision(result.proposal_id!, "cancel_now", externalConfirmed)}
                className="rounded-lg border px-3 py-2 font-medium hover:bg-muted"
              >
                立即停止并切换
              </button>
              <button
                type="button"
                disabled={usesExternalConnection && !externalConfirmed}
                onClick={() => void onDecision(result.proposal_id!, "new_task", externalConfirmed)}
                className="rounded-lg border px-3 py-2 font-medium hover:bg-muted"
              >
                作为独立任务
              </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function GuidanceDialog({
  guidance,
  open,
  onOpenChange,
  onUseExample,
}: {
  guidance?: WorkspaceGuidance;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onUseExample: (example: WorkspaceGuidance["examples"][number]) => void;
}) {
  const [query, setQuery] = useState("");
  const examples = (guidance?.examples || []).filter((example) =>
    `${example.title} ${example.description} ${example.category}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-slate-950/45 backdrop-blur-[2px]" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 flex h-[min(82vh,760px)] w-[min(92vw,920px)] -translate-x-1/2 -translate-y-1/2 flex-col overflow-hidden rounded-2xl border bg-background shadow-2xl">
          <div className="flex items-start justify-between border-b p-5">
            <div>
              <Dialog.Title className="font-semibold">
                使用帮助与场景示例
              </Dialog.Title>
              <Dialog.Description className="mt-1 text-sm text-muted-foreground">
                当前示例全部使用已经交付的表格和文档能力。
              </Dialog.Description>
            </div>
            <Dialog.Close className="rounded-lg p-2 text-muted-foreground hover:bg-muted">
              <X className="h-4 w-4" />
            </Dialog.Close>
          </div>
          <div className="grid min-h-0 flex-1 grid-cols-[260px_1fr]">
            <aside className="border-r bg-muted/20 p-5">
              <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                三步开始
              </h3>
              <div className="mt-4 space-y-4">
                {guidance?.onboarding.map((item, index) => (
                  <div key={item.title} className="flex gap-3">
                    <div className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
                      {index + 1}
                    </div>
                    <div>
                      <p className="text-sm font-medium">{item.title}</p>
                      <p className="mt-1 text-xs leading-5 text-muted-foreground">
                        {item.description}
                      </p>
                    </div>
                  </div>
                ))}
              </div>
            </aside>
            <div className="min-h-0 overflow-y-auto p-5">
              <div className="relative mb-4">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="搜索示例"
                  className="h-10 w-full rounded-xl border bg-background pl-9 pr-3 text-sm outline-none focus:border-primary"
                />
              </div>
              <div className="grid gap-3 md:grid-cols-2">
                {examples.map((example) => (
                  <button
                    key={example.id}
                    type="button"
                    onClick={() => {
                      onUseExample(example);
                      onOpenChange(false);
                    }}
                    className="rounded-xl border p-4 text-left transition-colors hover:border-primary/35 hover:bg-primary/[0.03]"
                  >
                    <span className="text-[10px] font-medium uppercase tracking-wide text-primary">
                      {example.category}
                    </span>
                    <h3 className="mt-1 text-sm font-semibold">
                      {example.title}
                    </h3>
                    <p className="mt-2 text-xs leading-5 text-muted-foreground">
                      {example.description}
                    </p>
                    <p className="mt-3 text-[11px] text-muted-foreground">
                      需要：{example.required_inputs}
                    </p>
                  </button>
                ))}
              </div>
            </div>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

export function SemanticWorkspacePage() {
  const queryClient = useQueryClient();
  const createAttemptRef = useRef<{
    fingerprint: string;
    key: string;
  } | null>(null);
  const reverificationAttemptRef = useRef<{
    fingerprint: string;
    key: string;
  } | null>(null);
  const publicationAttemptRef = useRef<{
    fingerprint: string;
    key: string;
  } | null>(null);
  const sourceRefreshAttemptRef = useRef<{
    fingerprint: string;
    key: string;
  } | null>(null);
  const { user } = useAuth();
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [selectedRevision, setSelectedRevision] = useState<number | null>(null);
  const [newTask, setNewTask] = useState(true);
  const [filter, setFilter] = useState<
    "all" | "active" | "needs_input" | "completed"
  >("all");
  const [recycleBin, setRecycleBin] = useState(false);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [selectedUploadId, setSelectedUploadId] = useState<string | null>(null);
  const [selectedEvidence, setSelectedEvidence] =
    useState<Record<string, unknown> | null>(null);
  const [draftUploads, setDraftUploads] = useState<UploadItem[]>([]);
  const [sourceMode, setSourceMode] = useState<"file" | "web">("file");
  const [liveEvents, setLiveEvents] = useState<WorkspaceEvent[]>([]);
  const [helpOpen, setHelpOpen] = useState(false);
  const [exampleSeed, setExampleSeed] = useState<{
    key: string;
    prompt: string;
    formats: string[];
  } | null>(null);
  const [showOnboarding, setShowOnboarding] = useState(
    () => localStorage.getItem(ONBOARDING_KEY) !== "1",
  );

  const tasks = useQuery({
    queryKey: ["semantic-workspace-tasks", recycleBin],
    queryFn: () => listWorkspaceTasks(recycleBin),
    refetchInterval: 3_000,
  });
  const guidance = useQuery({
    queryKey: ["semantic-workspace-guidance"],
    queryFn: getWorkspaceGuidance,
  });
  const models = useQuery<ModelsResponse>({
    queryKey: ["models"],
    queryFn: () => api.get("/api/models"),
  });
  const modelConnections = useQuery<ModelConnectionsResponse>({
    queryKey: ["model-connections"],
    queryFn: () => api.get("/api/model-connections"),
  });
  const modelPreference = useQuery<{
    preference: {
      connection_id: string;
      model_id: string;
      available: boolean;
    } | null;
  }>({
    queryKey: ["model-connection-preference"],
    queryFn: () => api.get("/api/model-connections/preferences/default"),
  });
  const verifiedModelConnections = (modelConnections.data?.items ?? []).filter(
    (connection) => connection.status === "verified",
  );
  const canUseLocalPiRuntime = isAdminish(user?.role);
  const canUsePiRuntime = Boolean(models.data?.pi_runtime_enabled)
    && (canUseLocalPiRuntime || verifiedModelConnections.length > 0);
  const grayCapabilities = useQuery<{ enabled: boolean; items: GrayCapability[] }>({
    queryKey: ["semantic-workspace-gray-capabilities"],
    queryFn: listGrayCapabilities,
    enabled: canUseLocalPiRuntime
      && Boolean(models.data?.pi_capability_host_enabled),
  });
  const storage = useQuery({
    queryKey: ["semantic-workspace-storage"],
    queryFn: getWorkspaceStorage,
    refetchInterval: 15_000,
  });
  const detail = useQuery({
    queryKey: ["semantic-workspace-task", selectedTaskId, selectedRevision],
    queryFn: () => getWorkspaceTask(selectedTaskId!, selectedRevision),
    enabled: Boolean(selectedTaskId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      const verificationStatus = query.state.data?.agentic_runtime
        ?.latest_verification_attempt?.status;
      return (
        status && ["queued", "running", "cancelling"].includes(status)
      ) || ["requested", "running"].includes(verificationStatus ?? "")
        ? 2_000
        : false;
    },
  });
  const task = detail.data;

  useEffect(() => {
    if (!selectedTaskId || !task) return;
    if (!["queued", "running", "cancelling"].includes(task.status)) return;
    return streamWorkspaceTask(selectedTaskId, {
      onProgress: (event) =>
        setLiveEvents((current) =>
          current.some((item) => item.event_id === event.event_id)
            ? current
            : [...current, event],
        ),
      onStatus: () => {
        void queryClient.invalidateQueries({
          queryKey: ["semantic-workspace-task", selectedTaskId],
        });
        void queryClient.invalidateQueries({
          queryKey: ["semantic-workspace-tasks"],
        });
      },
      onDone: () => {
        void queryClient.invalidateQueries({
          queryKey: ["semantic-workspace-task", selectedTaskId],
        });
        void queryClient.invalidateQueries({
          queryKey: ["semantic-workspace-tasks"],
        });
        void queryClient.invalidateQueries({
          queryKey: ["semantic-workspace-storage"],
        });
      },
    });
  }, [queryClient, selectedTaskId, task?.status]);

  useEffect(() => {
    if (task?.status === "completed" && showOnboarding) {
      localStorage.setItem(ONBOARDING_KEY, "1");
      setShowOnboarding(false);
    }
  }, [showOnboarding, task?.status]);

  useEffect(() => {
    if (!selectedTaskId || !task) return;
    setSelectedUploadId((current) => current || task.upload_ids[0] || null);
  }, [selectedTaskId, task?.upload_ids]);

  const useExample = (example: WorkspaceGuidance["examples"][number]) => {
    setNewTask(true);
    setSelectedTaskId(null);
    setSelectedRevision(null);
    setDraftUploads([]);
    setSelectedUploadId(null);
    setInspectorOpen(false);
    setExampleSeed({
      key: `${example.id}:${Date.now()}`,
      prompt: example.prompt,
      formats: example.output_formats,
    });
  };

  const handleDraftUploadsChange = useCallback((uploads: UploadItem[]) => {
    setDraftUploads(uploads);
    if (!uploads.length) {
      setSelectedUploadId(null);
      setInspectorOpen(false);
      return;
    }
    setInspectorOpen(true);
    setSelectedUploadId((current) =>
      current && uploads.some((upload) => upload.upload_id === current)
        ? current
        : uploads[0].upload_id,
    );
  }, []);

  const submitNew = async (payload: {
    prompt: string;
    uploads: Array<{ upload_id: string }>;
    formats: string[];
    provider: string;
    model: string | null;
    runtimeVersion?: "legacy" | "pi";
    permissionProfile: "standard";
    modelConnectionId: string | null;
    modelConnectionModel: string | null;
    externalApiConfirmed: boolean;
    capabilityPackRefs: Array<{
      pack_id: string;
      version: string;
      digest: string;
    }>;
  }) => {
    try {
      const requestPayload = {
        objective_text: payload.prompt,
        upload_ids: payload.uploads.map((upload) => upload.upload_id),
        output_formats: payload.formats,
        provider: payload.provider,
        model: payload.model,
        ...(payload.runtimeVersion
          ? { runtime_version: payload.runtimeVersion }
          : {}),
        permission_profile: payload.permissionProfile,
        model_connection_id: payload.modelConnectionId,
        model_connection_model: payload.modelConnectionModel,
        external_api_confirmed: payload.externalApiConfirmed,
        capability_pack_refs: payload.capabilityPackRefs,
      } as const;
      const fingerprint = JSON.stringify(requestPayload);
      if (createAttemptRef.current?.fingerprint !== fingerprint) {
        createAttemptRef.current = {
          fingerprint,
          key: nanoid(),
        };
      }
      const created = await createWorkspaceTask(
        requestPayload,
        createAttemptRef.current.key,
      );
      createAttemptRef.current = null;
      setSelectedTaskId(created.task_id);
      setSelectedRevision(null);
      setNewTask(false);
      setRecycleBin(false);
      setLiveEvents([]);
      await queryClient.invalidateQueries({
        queryKey: ["semantic-workspace-tasks"],
      });
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "创建任务失败");
      throw error;
    }
  };

  const viewSource = (evidence: Record<string, unknown>) => {
    const artifactId = String(evidence.artifact_id || "");
    if (artifactId && task?.upload_ids.includes(artifactId)) {
      setSelectedUploadId(artifactId);
    }
    setSelectedEvidence(evidence);
    setInspectorOpen(true);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between border-b bg-background px-5">
        <div>
          <h1 className="text-sm font-semibold">数据工作台</h1>
          <p className="text-[11px] text-muted-foreground">
            表格与文档的理解、执行、验证和正式交付
          </p>
        </div>
        <div className="flex items-center gap-2">
          {selectedTaskId && !newTask ? (
            <button
              type="button"
              onClick={() => {
                setSelectedTaskId(null);
                setSelectedRevision(null);
              }}
              className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium hover:bg-muted md:hidden"
            >
              <ArrowLeft className="h-3.5 w-3.5" />
              任务列表
            </button>
          ) : null}
          {(newTask ? draftUploads.length > 0 : Boolean(task?.uploads?.length)) ? (
            <button
              type="button"
              onClick={() => setInspectorOpen((value) => !value)}
              className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium hover:bg-muted"
            >
              <FileSearch className="h-3.5 w-3.5" />
              原文件预览
            </button>
          ) : null}
          <button
            type="button"
            onClick={() => setHelpOpen(true)}
            className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium hover:bg-muted"
          >
            <HelpCircle className="h-3.5 w-3.5" />
            帮助
          </button>
        </div>
      </header>

      <GuidanceDialog
        guidance={guidance.data}
        open={helpOpen}
        onOpenChange={setHelpOpen}
        onUseExample={useExample}
      />

      <div className="flex min-h-0 flex-1">
        <div className={cn(
          "shrink-0",
          selectedTaskId && !newTask && "max-md:hidden",
        )}>
          <WorkspaceTaskSidebar
          tasks={tasks.data || []}
          activeTaskId={selectedTaskId}
          filter={filter}
          recycleBin={recycleBin}
          storage={storage.data}
          onSelect={(taskId) => {
            setSelectedTaskId(taskId);
            setSelectedRevision(null);
            setNewTask(false);
            setLiveEvents([]);
          }}
          onFilter={(nextFilter) => {
            setRecycleBin(false);
            setFilter(nextFilter);
          }}
          onNew={() => {
            setRecycleBin(false);
            setSelectedTaskId(null);
            setSelectedRevision(null);
            setDraftUploads([]);
            setSelectedUploadId(null);
            setInspectorOpen(false);
            setNewTask(true);
          }}
          onToggleRecycleBin={() => {
            setRecycleBin((value) => !value);
            setSelectedTaskId(null);
            setSelectedRevision(null);
            setNewTask(false);
          }}
          />
        </div>

        <div className="min-w-0 flex-1">
          {newTask ? (
            <div className="flex h-full min-h-0">
              <div className="min-w-0 flex-1 overflow-y-auto">
                <div
                  className={cn(
                    "mx-auto max-w-5xl",
                    draftUploads.length
                      ? "px-4 pb-8 pt-6"
                      : "px-4 pb-12 pt-8 sm:px-8 sm:pt-14",
                  )}
                >
                {draftUploads.length === 0 ? (
                  <>
                    <div className="mx-auto max-w-3xl text-center">
                      <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-2xl bg-primary/10 text-primary">
                        <Sparkles className="h-5 w-5" />
                      </div>
                      <h2 className="mt-5 text-2xl font-semibold tracking-tight">
                        想处理什么资料？
                      </h2>
                      <p className="mt-2 text-sm text-muted-foreground">
                        添加文件并说明目标，明确任务会直接执行；只有结果含义不明确时才会询问。
                      </p>
                    </div>

                    {showOnboarding && guidance.data && (
                      <div className="mx-auto mt-8 max-w-3xl rounded-2xl border bg-muted/20 p-4">
                        <div className="flex items-center justify-between">
                          <span className="flex items-center gap-2 text-sm font-medium">
                            <BookOpen className="h-4 w-4 text-primary" />
                            三步完成第一个任务
                          </span>
                          <button
                            type="button"
                            onClick={() => setShowOnboarding(false)}
                            className="rounded p-1 text-muted-foreground hover:bg-muted"
                            aria-label="收起新手引导"
                          >
                            <ChevronDown className="h-4 w-4" />
                          </button>
                        </div>
                        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-3">
                          {guidance.data.onboarding.map((item, index) => (
                            <div key={item.title} className="flex gap-2">
                              <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[10px] font-semibold text-primary">
                                {index + 1}
                              </span>
                              <div>
                                <p className="text-xs font-medium">{item.title}</p>
                                <p className="mt-1 text-[11px] leading-5 text-muted-foreground">
                                  {item.description}
                                </p>
                              </div>
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                ) : (
                  <div className="mx-auto mb-4 max-w-3xl">
                    <h2 className="text-lg font-semibold">核对文件并说明目标</h2>
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">
                      原文件已在右侧显示。确认内容无误后，用一句话说明要筛选、汇总或提取什么。
                    </p>
                  </div>
                )}

                <div
                  className={cn(
                    "mx-auto max-w-3xl",
                    draftUploads.length === 0 && "mt-6",
                  )}
                >
                  <fieldset className="mb-3 flex items-center gap-1 border-b pb-3">
                    <legend className="mr-2 text-xs font-medium text-muted-foreground">
                      资料来源
                    </legend>
                    {(["file", "web"] as const).map((mode) => (
                      <label
                        key={mode}
                        className={cn(
                          "cursor-pointer rounded-lg px-3 py-1.5 text-xs transition-colors focus-within:ring-2 focus-within:ring-ring",
                          sourceMode === mode
                            ? "bg-primary/10 font-medium text-teal-800 dark:text-primary"
                            : "text-muted-foreground hover:bg-muted hover:text-foreground",
                        )}
                      >
                        <input
                          type="radio"
                          name="workspace-source-mode"
                          value={mode}
                          checked={sourceMode === mode}
                          onChange={() => setSourceMode(mode)}
                          className="sr-only"
                        />
                        {mode === "file" ? "文件" : "公开网页"}
                      </label>
                    ))}
                  </fieldset>
                  {sourceMode === "file" ? (
                    <TaskComposer
                    key={exampleSeed?.key || "new-task"}
                    initialPrompt={exampleSeed?.prompt}
                    initialFormats={exampleSeed?.formats}
                    modelOptions={models.data?.options}
                    defaultModel={models.data?.default}
                    allowPiRuntime={canUsePiRuntime}
                    allowLocalPiRuntime={canUseLocalPiRuntime}
                    modelConnections={verifiedModelConnections}
                    defaultConnectionId={
                      modelPreference.data?.preference?.available
                        ? modelPreference.data.preference.connection_id
                        : null
                    }
                    defaultConnectionModel={
                      modelPreference.data?.preference?.available
                        ? modelPreference.data.preference.model_id
                        : null
                    }
                    grayCapabilities={grayCapabilities.data?.items ?? []}
                    onUploadsChange={handleDraftUploadsChange}
                    onSubmit={submitNew}
                    />
                  ) : (
                    <WebSourceIntake
                      ownerId={user?.user_id ?? "current"}
                      allowLocalRuntime={canUseLocalPiRuntime}
                      localModels={(models.data?.options ?? [])
                        .filter((model) => model.provider === "local")
                        .map((model) => ({ model: model.model, label: model.label }))}
                      defaultLocalModel={
                        models.data?.default?.provider === "local"
                          ? models.data.default.model
                          : null
                      }
                      modelConnections={verifiedModelConnections}
                      defaultConnectionId={
                        modelPreference.data?.preference?.available
                          ? modelPreference.data.preference.connection_id
                          : null
                      }
                      defaultConnectionModel={
                        modelPreference.data?.preference?.available
                          ? modelPreference.data.preference.model_id
                          : null
                      }
                      onTaskCreated={async (created) => {
                        setSelectedTaskId(created.task_id);
                        setSelectedRevision(null);
                        setNewTask(false);
                        setRecycleBin(false);
                        setLiveEvents([]);
                        await queryClient.invalidateQueries({
                          queryKey: ["semantic-workspace-tasks"],
                        });
                      }}
                    />
                  )}
                  <p className="mt-2 text-center text-[11px] text-muted-foreground">
                    {sourceMode === "file"
                      ? "支持拖放、点击和粘贴文件；上传完成后会立即显示原件预览。"
                      : "当前只读取一个精确公开页面；来源冻结后可核对要求并启动正式任务。"}
                  </p>
                </div>

                {sourceMode === "file" && draftUploads.length === 0 && guidance.data && (
                  <div className="mt-12">
                    <div className="flex items-end justify-between">
                      <div>
                        <h3 className="text-sm font-semibold">常用场景</h3>
                        <p className="mt-1 text-xs text-muted-foreground">
                          只展示当前版本已经具备执行和交付能力的示例
                        </p>
                      </div>
                      <button
                        type="button"
                        onClick={() => setHelpOpen(true)}
                        className="text-xs font-medium text-teal-700 hover:underline dark:text-teal-300"
                      >
                        更多示例
                      </button>
                    </div>
                    <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-3">
                      {guidance.data.examples.slice(0, 6).map((example) => (
                        <button
                          key={example.id}
                          type="button"
                          onClick={() => useExample(example)}
                          className="group rounded-2xl border bg-background p-4 text-left transition-all hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-md"
                        >
                          <span className="text-[10px] font-medium uppercase tracking-wide text-primary">
                            {example.category}
                          </span>
                          <h4 className="mt-2 text-sm font-semibold">
                            {example.title}
                          </h4>
                          <p className="mt-2 line-clamp-2 text-xs leading-5 text-muted-foreground">
                            {example.description}
                          </p>
                          <div className="mt-4 flex gap-1">
                            {example.output_formats.map((format) => (
                              <span
                                key={format}
                                className="rounded bg-muted px-1.5 py-0.5 text-[9px] font-medium uppercase text-muted-foreground"
                              >
                                {format}
                              </span>
                            ))}
                          </div>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
                </div>
              </div>
              {inspectorOpen && draftUploads.length > 0 && (
                <div className="h-full w-[min(42%,620px)] min-w-[380px] border-l bg-background shadow-[-18px_0_45px_-38px_rgba(15,23,42,0.45)]">
                  <SourcePreviewPanel
                    uploads={draftUploads}
                    selectedUploadId={selectedUploadId}
                    evidence={null}
                    onSelectUpload={(uploadId) => {
                      setSelectedUploadId(uploadId);
                      setSelectedEvidence(null);
                    }}
                    onClose={() => setInspectorOpen(false)}
                  />
                </div>
              )}
            </div>
          ) : selectedTaskId && detail.isLoading ? (
            <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-5 w-5 animate-spin" />
              正在恢复任务
            </div>
          ) : selectedTaskId && detail.isError ? (
            <div className="flex h-full items-center justify-center px-5 py-8">
              <section
                role="alert"
                aria-labelledby="task-recovery-error-title"
                className="w-full max-w-md rounded-2xl border border-destructive/25 bg-destructive/[0.04] p-6 text-center shadow-sm"
              >
                <FileSearch className="mx-auto h-8 w-8 text-destructive" />
                <h2
                  id="task-recovery-error-title"
                  className="mt-3 text-base font-semibold"
                >
                  任务恢复失败
                </h2>
                <p className="mt-2 text-sm leading-6 text-muted-foreground">
                  历史任务仍保留在任务列表中，本次只是详情读取失败。
                </p>
                <p className="mt-2 text-sm leading-6 text-destructive">
                  {taskRecoveryError(detail.error)}
                </p>
                <button
                  type="button"
                  disabled={detail.isFetching}
                  aria-busy={detail.isFetching}
                  onClick={() => void detail.refetch()}
                  className="mt-5 inline-flex h-10 min-w-28 cursor-pointer items-center justify-center gap-2 rounded-xl border bg-background px-4 text-sm font-medium transition-colors hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {detail.isFetching ? (
                    <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
                  ) : (
                    <RotateCcw className="h-4 w-4" />
                  )}
                  重新加载
                </button>
              </section>
            </div>
          ) : task ? (
            <Group
              orientation="horizontal"
              className="h-full min-h-0"
              defaultLayout={{
                content: inspectorOpen ? 68 : 100,
                source: inspectorOpen ? 32 : 0,
              }}
            >
              <Panel id="content" minSize="0px" className="min-w-0 md:min-w-[560px]">
                <div className="flex h-full min-h-0 flex-col">
                  {recycleBin && task.deleted_at ? (
                    <div className="flex h-full items-center justify-center p-8">
                      <div className="w-full max-w-lg rounded-2xl border bg-card p-6 text-center shadow-sm">
                        <Trash2 className="mx-auto h-8 w-8 text-muted-foreground" />
                        <h2 className="mt-4 font-semibold">{task.title}</h2>
                        <p className="mt-2 text-sm text-muted-foreground">
                          可在{" "}
                          {new Date(task.purge_after || "").toLocaleDateString(
                            "zh-CN",
                          )}{" "}
                          前恢复。
                        </p>
                        <div className="mt-5 flex justify-center gap-2">
                          <button
                            type="button"
                            onClick={async () => {
                              try {
                                await restoreWorkspaceTask(task.task_id);
                                setRecycleBin(false);
                                await Promise.all([
                                  queryClient.invalidateQueries({
                                    queryKey: ["semantic-workspace-task", task.task_id],
                                  }),
                                  queryClient.invalidateQueries({
                                    queryKey: ["semantic-workspace-tasks"],
                                  }),
                                  queryClient.invalidateQueries({
                                    queryKey: ["semantic-workspace-storage"],
                                  }),
                                ]);
                                toast.success("任务已恢复");
                              } catch (error) {
                                toast.error(
                                  error instanceof Error
                                    ? error.message
                                    : "恢复任务失败",
                                );
                              }
                            }}
                            className="inline-flex items-center gap-1.5 rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground"
                          >
                            <RotateCcw className="h-3.5 w-3.5" />
                            恢复任务
                          </button>
                          <AlertDialog.Root>
                            <AlertDialog.Trigger asChild>
                              <button
                                type="button"
                                className="rounded-lg border border-destructive/30 px-3 py-2 text-xs font-medium text-destructive"
                              >
                                永久删除
                              </button>
                            </AlertDialog.Trigger>
                            <AlertDialog.Portal>
                              <AlertDialog.Overlay className="fixed inset-0 z-50 bg-slate-950/45" />
                              <AlertDialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(90vw,440px)] -translate-x-1/2 -translate-y-1/2 rounded-2xl border bg-background p-6 shadow-2xl">
                                <AlertDialog.Title className="font-semibold">
                                  永久删除这个任务？
                                </AlertDialog.Title>
                                <AlertDialog.Description className="mt-2 text-sm leading-6 text-muted-foreground">
                                  此操作不可恢复。工作台记录会删除，底层审计制品按生产溯源规则继续保留。
                                </AlertDialog.Description>
                                <div className="mt-5 flex justify-end gap-2">
                                  <AlertDialog.Cancel className="rounded-lg border px-3 py-2 text-sm">
                                    取消
                                  </AlertDialog.Cancel>
                                  <AlertDialog.Action
                                    onClick={async () => {
                                      try {
                                        await permanentlyDeleteWorkspaceTask(
                                          task.task_id,
                                        );
                                        setSelectedTaskId(null);
                                        await Promise.all([
                                          queryClient.invalidateQueries({
                                            queryKey: ["semantic-workspace-tasks"],
                                          }),
                                          queryClient.invalidateQueries({
                                            queryKey: ["semantic-workspace-storage"],
                                          }),
                                        ]);
                                        toast.success("任务已永久删除");
                                      } catch (error) {
                                        toast.error(
                                          error instanceof Error
                                            ? error.message
                                            : "永久删除失败",
                                        );
                                      }
                                    }}
                                    className="rounded-lg bg-destructive px-3 py-2 text-sm text-destructive-foreground"
                                  >
                                    永久删除
                                  </AlertDialog.Action>
                                </div>
                              </AlertDialog.Content>
                            </AlertDialog.Portal>
                          </AlertDialog.Root>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div className="min-h-0 flex-1 overflow-y-auto">
                        <TaskTimeline
                          task={task}
                          liveEvents={liveEvents}
                          onAnswer={async (answer) => {
                            try {
                              await answerWorkspaceTask(task.task_id, answer);
                              await Promise.all([
                                queryClient.invalidateQueries({
                                  queryKey: [
                                    "semantic-workspace-task",
                                    task.task_id,
                                  ],
                                }),
                                queryClient.invalidateQueries({
                                  queryKey: ["semantic-workspace-tasks"],
                                }),
                              ]);
                            } catch (error) {
                              toast.error(
                                error instanceof Error
                                  ? error.message
                                  : "提交确认失败",
                              );
                              throw error;
                            }
                          }}
                          onCancel={async () => {
                            try {
                              await cancelWorkspaceTask(task.task_id);
                              await Promise.all([
                                queryClient.invalidateQueries({
                                  queryKey: [
                                    "semantic-workspace-task",
                                    task.task_id,
                                  ],
                                }),
                                queryClient.invalidateQueries({
                                  queryKey: ["semantic-workspace-tasks"],
                                }),
                              ]);
                            } catch (error) {
                              toast.error(
                                error instanceof Error
                                  ? error.message
                                  : "取消任务失败",
                              );
                            }
                          }}
                          onRecycle={async () => {
                            try {
                              await recycleWorkspaceTask(task.task_id);
                              setSelectedTaskId(null);
                              setSelectedRevision(null);
                              await Promise.all([
                                queryClient.invalidateQueries({
                                  queryKey: ["semantic-workspace-tasks"],
                                }),
                                queryClient.invalidateQueries({
                                  queryKey: ["semantic-workspace-storage"],
                                }),
                              ]);
                              toast.success("任务已移入回收站");
                            } catch (error) {
                              toast.error(
                                error instanceof Error
                                  ? error.message
                                  : "移入回收站失败",
                              );
                            }
                          }}
                          onRetry={async (unchanged = false) => {
                            if (!unchanged) {
                              document
                                .getElementById("workspace-revision-composer")
                                ?.scrollIntoView({ behavior: "smooth" });
                              return;
                            }
                            try {
                              await createWorkspaceRevision(
                                task.task_id,
                                "保持原要求，重新执行",
                                task.active_revision,
                                undefined,
                                true,
                              );
                              await Promise.all([
                                queryClient.invalidateQueries({
                                  queryKey: [
                                    "semantic-workspace-task",
                                    task.task_id,
                                  ],
                                }),
                                queryClient.invalidateQueries({
                                  queryKey: ["semantic-workspace-tasks"],
                                }),
                              ]);
                              toast.success("已创建新版本并重新执行");
                            } catch (error) {
                              toast.error(
                                error instanceof Error
                                  ? error.message
                                  : "重新执行失败",
                              );
                            }
                          }}
                          onRefreshSource={async (externalApiConfirmed) => {
                            const expectedRevision = task.current_revision
                              ?? task.active_revision;
                            const fingerprint = `${task.task_id}:${expectedRevision}`;
                            const storageKey = `mangrove_source_refresh_${user?.user_id ?? "unknown"}_${task.task_id}`;
                            let resumeUnknown = sourceRefreshAttemptRef.current?.fingerprint
                              === fingerprint;
                            if (
                              sourceRefreshAttemptRef.current?.fingerprint
                              !== fingerprint
                            ) {
                              let restoredKey: string | null = null;
                              try {
                                const stored = JSON.parse(
                                  localStorage.getItem(storageKey) ?? "null",
                                ) as { fingerprint?: string; key?: string } | null;
                                if (
                                  stored?.fingerprint === fingerprint
                                  && typeof stored.key === "string"
                                ) {
                                  restoredKey = stored.key;
                                  resumeUnknown = true;
                                }
                              } catch {
                                try {
                                  localStorage.removeItem(storageKey);
                                } catch {
                                  // 存储不可用时仅失去跨刷新恢复，不阻断当前操作。
                                }
                              }
                              sourceRefreshAttemptRef.current = {
                                fingerprint,
                                key: restoredKey ?? `source-refresh-${nanoid()}`,
                              };
                              try {
                                localStorage.setItem(
                                  storageKey,
                                  JSON.stringify(sourceRefreshAttemptRef.current),
                                );
                              } catch {
                                // 当前页面仍通过 ref 保持幂等键。
                              }
                            }
                            try {
                              const result = await refreshWorkspaceSource(
                                task.task_id,
                                expectedRevision,
                                externalApiConfirmed,
                                sourceRefreshAttemptRef.current.key,
                                resumeUnknown,
                              );
                              if (result.status === "acquiring") {
                                toast.info(
                                  "刷新请求结果仍未知；旧版本保持不变。请稍后再次点击并恢复同一请求。",
                                );
                                return;
                              }
                              sourceRefreshAttemptRef.current = null;
                              try {
                                localStorage.removeItem(storageKey);
                              } catch {
                                // 存储不可用时无需额外清理。
                              }
                              setSelectedRevision(null);
                              await Promise.all([
                                queryClient.invalidateQueries({
                                  queryKey: [
                                    "semantic-workspace-task",
                                    task.task_id,
                                  ],
                                }),
                                queryClient.invalidateQueries({
                                  queryKey: ["semantic-workspace-tasks"],
                                }),
                              ]);
                              toast.success("最新网页已冻结，并创建了新版本");
                            } catch (error) {
                              toast.error(
                                error instanceof Error
                                  ? error.message
                                  : "网页来源刷新失败；旧版本保持不变",
                              );
                              throw error;
                            }
                          }}
                          onRevisionChange={(revision) =>
                            setSelectedRevision(
                              revision === task.current_revision
                                ? null
                                : revision,
                            )
                          }
                        />
                        {task.status === "completed" && (
                          <ResultPreview task={task} onViewSource={viewSource} />
                        )}
                        {task.status === "candidate_ready" && (
                          <CandidatePreview
                            task={task}
                            providerConnectionLabel={
                              modelConnections.data?.items.find(
                                (connection) => connection.connection_id
                                  === task.agentic_runtime?.reverification_offer?.connection_id,
                              )?.display_name
                            }
                            onRequestReverification={async (
                              externalApiConfirmed,
                              historicalAuthorityRecovery,
                              legacyRebaseline,
                            ) => {
                              const previousAttemptId = task.agentic_runtime
                                ?.latest_verification_attempt?.attempt_id;
                              if (!previousAttemptId) {
                                throw new Error("缺少当前验证记录，请刷新后重试");
                              }
                              const revision = task.current_revision ?? task.active_revision;
                              const offer = task.agentic_runtime?.reverification_offer;
                              const fingerprint = [
                                task.task_id,
                                revision,
                                previousAttemptId,
                                offer?.reason ?? "none",
                                offer?.candidate_set_hash ?? "none",
                                offer?.target_ruleset_hash ?? "none",
                              ].join(":");
                              if (reverificationAttemptRef.current?.fingerprint !== fingerprint) {
                                reverificationAttemptRef.current = {
                                  fingerprint,
                                  key: `reverify-${nanoid()}`,
                                };
                              }
                              await requestCandidateReverification(
                                task.task_id,
                                {
                                  expected_revision: revision,
                                  expected_previous_attempt_id: previousAttemptId,
                                  external_api_confirmed: externalApiConfirmed,
                                  accept_duplicate_provider_cost: false,
                                  historical_authority_recovery:
                                    historicalAuthorityRecovery,
                                  ...legacyRebaseline,
                                },
                                reverificationAttemptRef.current.key,
                              );
                              await Promise.all([
                                queryClient.invalidateQueries({
                                  queryKey: ["semantic-workspace-task", task.task_id],
                                }),
                                queryClient.invalidateQueries({
                                  queryKey: ["semantic-workspace-tasks"],
                                }),
                              ]);
                            }}
                            onPublishVerification={async (attemptId) => {
                              const revision = task.current_revision ?? task.active_revision;
                              const fingerprint = `${task.task_id}:${revision}:${attemptId}`;
                              if (publicationAttemptRef.current?.fingerprint !== fingerprint) {
                                publicationAttemptRef.current = {
                                  fingerprint,
                                  key: `publish-${nanoid()}`,
                                };
                              }
                              await publishCandidateVerification(
                                task.task_id,
                                attemptId,
                                revision,
                                publicationAttemptRef.current.key,
                              );
                              await Promise.all([
                                queryClient.invalidateQueries({
                                  queryKey: ["semantic-workspace-task", task.task_id],
                                }),
                                queryClient.invalidateQueries({
                                  queryKey: ["semantic-workspace-tasks"],
                                }),
                              ]);
                            }}
                          />
                        )}
                        <div id="workspace-revision-composer" className="h-28" />
                      </div>
                      <div className="shrink-0 border-t bg-background/95 px-6 py-3 backdrop-blur">
                        <div className="mx-auto max-w-4xl">
                          {task.viewing_revision !== task.current_revision && (
                            <button
                              type="button"
                              onClick={() => setSelectedRevision(null)}
                              className="mb-2 w-full rounded-xl border px-4 py-2 text-left text-xs text-muted-foreground hover:bg-muted"
                            >
                              当前显示历史版本 V{task.viewing_revision}；下面的追问会基于最新版本 V{task.current_revision} 处理
                            </button>
                          )}
                          <FollowupComposer
                            task={task}
                            onSubmit={async (text, idempotencyKey) => {
                              try {
                                const result = await sendWorkspaceTurn(
                                  task.task_id,
                                  text,
                                  idempotencyKey,
                                );
                                await queryClient.invalidateQueries({
                                  queryKey: [
                                    "semantic-workspace-task",
                                    task.task_id,
                                  ],
                                });
                                return result;
                              } catch (error) {
                                toast.error(
                                  error instanceof Error
                                    ? error.message
                                    : "追问处理失败",
                                );
                                throw error;
                              }
                            }}
                            onDecision={async (proposalId, mode, externalConfirmed) => {
                              try {
                                await decideWorkspaceRevision(
                                  task.task_id,
                                  proposalId,
                                  mode,
                                  externalConfirmed,
                                );
                                setLiveEvents([]);
                                setSelectedRevision(null);
                                await Promise.all([
                                  queryClient.invalidateQueries({
                                    queryKey: [
                                      "semantic-workspace-task",
                                      task.task_id,
                                    ],
                                  }),
                                  queryClient.invalidateQueries({
                                    queryKey: ["semantic-workspace-tasks"],
                                  }),
                                ]);
                                toast.success(
                                  mode === "after_safe_point"
                                    ? "已确认，将在当前步骤结束后切换"
                                    : mode === "cancel_now"
                                      ? "已切换到新版本"
                                      : "已登记为独立任务",
                                );
                              } catch (error) {
                                toast.error(
                                  error instanceof Error
                                    ? error.message
                                    : "确认修改失败",
                                );
                              }
                            }}
                          />
                        </div>
                      </div>
                    </>
                  )}
                </div>
              </Panel>
              {inspectorOpen && (
                <>
                  <Separator className="w-1 border-x bg-border/50 transition-colors hover:bg-primary/30" />
                  <Panel
                    id="source"
                    minSize="360px"
                    maxSize="720px"
                    className="bg-background"
                  >
                    <SourcePreviewPanel
                      uploads={task.uploads || []}
                      selectedUploadId={selectedUploadId}
                      evidence={selectedEvidence}
                      onSelectUpload={(uploadId) => {
                        setSelectedUploadId(uploadId);
                        setSelectedEvidence(null);
                      }}
                      onClose={() => setInspectorOpen(false)}
                    />
                  </Panel>
                </>
              )}
            </Group>
          ) : (
            <div className="flex h-full flex-col items-center justify-center text-center text-sm text-muted-foreground">
              <LayoutTemplate className="mb-3 h-7 w-7 opacity-40" />
              {recycleBin
                ? "选择一个回收站任务查看详情"
                : "选择任务或新建任务"}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
