import { useCallback, useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { nanoid } from "nanoid/non-secure";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as Dialog from "@radix-ui/react-dialog";
import { Group, Panel, Separator } from "react-resizable-panels";
import {
  ArrowLeft,
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
import { ModelConnectionsPanel } from "@/pages/settings/ModelConnectionsPanel";
import { TaskComposer, type WebIntakeDraft } from "@/components/workspace/TaskComposer";
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
  decideCandidateGap,
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
  resumeAccountWorkspaceTask,
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
  pendingResults,
}: {
  task: WorkspaceTask;
  pendingResults: SteeringResult[];
  onSubmit: (text: string, idempotencyKey: string) => Promise<SteeringResult>;
  onDecision: (
    proposalId: string,
    mode: "cancel_now" | "after_safe_point" | "new_task",
    externalApiConfirmed: boolean,
  ) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const inFlight = useRef(false);
  const [confirmedProposal, setConfirmedProposal] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const usesExternalConnection = Boolean(task.model_connection_id);
  useEffect(() => {
    setText("");
    setConfirmedProposal(null);
  }, [task.task_id]);
  const submit = async () => {
    if (!text.trim() || inFlight.current) return;
    const submitted = text;
    inFlight.current = true;
    setBusy(true);
    try {
      await onSubmit(submitted.trim(), nanoid());
      setText(current => current === submitted ? "" : current);
    } catch {
      // 父级展示请求错误；失败保留原稿，不自动重复发送。
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };
  const decide = async (proposalId: string, mode: "cancel_now" | "after_safe_point" | "new_task") => {
    if (inFlight.current) return;
    inFlight.current = true;
    setBusy(true);
    try {
      await onDecision(proposalId, mode, confirmedProposal === proposalId);
      setConfirmedProposal(null);
    } catch {
      // 父级保留服务端错误，用户可核对状态后重新决定。
    } finally {
      inFlight.current = false;
      setBusy(false);
    }
  };
  return (
    <div className="rounded-2xl border bg-background p-3 shadow-[0_14px_45px_-32px_hsl(var(--primary)/0.7)]">
      <textarea
        aria-label="继续对话"
        value={text}
        onChange={(event) => setText(event.target.value)}
        onKeyDown={(event) => {
          if (!event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229 && event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            void submit();
          }
        }}
        rows={2}
        placeholder="可询问进度和原因，也可提出修改；系统会先说明是否影响当前任务"
        className="w-full min-h-16 max-h-40 [field-sizing:content] resize-none overflow-y-auto bg-transparent px-1 text-sm leading-6 outline-none placeholder:text-muted-foreground/70"
      />
      <div className="mt-2 flex items-center gap-3 border-t pt-2">
        <span className="text-[11px] text-muted-foreground">
          Enter 发送 · Shift + Enter 换行
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
      {pendingResults.map(result => (
        <div
          key={result.result_id}
          aria-live="polite"
          className="mt-3 rounded-xl border bg-muted/25 px-3 py-2 text-xs leading-5"
        >
          <p className="font-medium">待确认修改</p>
          {result.action === "revision_proposal" && result.proposal_id && (
            <div className="mt-3">
              {usesExternalConnection && (
                <label className="mb-3 flex items-start gap-2 rounded-lg border bg-background p-2.5 text-muted-foreground">
                  <input
                    type="checkbox"
                    checked={confirmedProposal === result.proposal_id}
                    onChange={(event) => setConfirmedProposal(event.target.checked ? result.proposal_id : null)}
                    className="mt-0.5"
                  />
                  <span>我确认新版本或独立任务仍会把当前任务范围内的数据发送到已选外部模型连接。</span>
                </label>
              )}
              <div className="flex flex-wrap gap-2">
              <button
                type="button"
                disabled={busy || (usesExternalConnection && confirmedProposal !== result.proposal_id)}
                onClick={() => void decide(result.proposal_id!, "after_safe_point")}
                className="rounded-lg bg-primary px-3 py-2 font-medium text-primary-foreground"
              >
                当前步骤结束后切换
              </button>
              <button
                type="button"
                disabled={busy || (usesExternalConnection && confirmedProposal !== result.proposal_id)}
                onClick={() => void decide(result.proposal_id!, "cancel_now")}
                className="rounded-lg border px-3 py-2 font-medium hover:bg-muted"
              >
                立即停止并切换
              </button>
              <button
                type="button"
                disabled={busy || (usesExternalConnection && confirmedProposal !== result.proposal_id)}
                onClick={() => void decide(result.proposal_id!, "new_task")}
                className="rounded-lg border px-3 py-2 font-medium hover:bg-muted"
              >
                作为独立任务
              </button>
              </div>
            </div>
          )}
        </div>
      ))}
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
          <div className="grid min-h-0 flex-1 overflow-y-auto md:grid-cols-[260px_1fr]">
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
  const gapActionAttemptRef = useRef<{
    fingerprint: string;
    key: string;
  } | null>(null);
  const accountResumeFlight = useRef<string | null>(null);
  const [accountResumeBusy, setAccountResumeBusy] = useState<string | null>(null);
  const [accountResumeError, setAccountResumeError] = useState<{ key: string; message: string; unknown: boolean } | null>(null);
  const [accountResumeConfirmed, setAccountResumeConfirmed] = useState<string | null>(null);
  const { user } = useAuth();
  const accountResumeOwner = useRef(user?.user_id);
  accountResumeOwner.current = user?.user_id;
  const [searchParams, setSearchParams] = useSearchParams();
  const selectionParams = useRef(searchParams);
  selectionParams.current = searchParams;
  // 选择保留在站内地址，重新登录后仍读取同一任务与修订；正文仍经 Owner 鉴权获取。
  const selectedTaskId = searchParams.get("task") || null;
  const revision = Number(searchParams.get("revision"));
  const selectedRevision = Number.isSafeInteger(revision) && revision > 0 ? revision : null;
  const setSelectedTaskId = (taskId: string | null) => {
    const next = new URLSearchParams(selectionParams.current);
    if (taskId) next.set("task", taskId);
    else next.delete("task");
    next.delete("revision");
    selectionParams.current = next;
    setSearchParams(next);
  };
  const setSelectedRevision = (value: number | null) => {
    const current = selectionParams.current;
    // 异步操作属于发起时的任务/修订，迟到回调不能覆盖用户的新选择。
    if (current.get("task") !== selectedTaskId || current.get("revision") !== searchParams.get("revision")) return;
    const next = new URLSearchParams(current);
    if (value === null) next.delete("revision");
    else next.set("revision", String(value));
    selectionParams.current = next;
    setSearchParams(next);
  };
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [navigationOpen, setNavigationOpen] = useState(() => window.innerWidth >= 1024);
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 767px)").matches);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 767px)");
    const update = () => { setNarrow(media.matches); if (media.matches) setNavigationOpen(false); };
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const [webPrompt, setWebPrompt] = useState<WebIntakeDraft | null>(null);
  const [composerDraft, setComposerDraft] = useState<WebIntakeDraft | null>(null);
  const [webOpen, setWebOpen] = useState(false);
  const [filter, setFilter] = useState<
    "all" | "active" | "needs_input" | "completed"
  >("all");
  const [recycleBin, setRecycleBin] = useState(false);
  const newTask = !selectedTaskId && !recycleBin;
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [inspectorExpanded, setInspectorExpanded] = useState(false);
  const fullInspector = narrow || inspectorExpanded;
  const [inspectorKind, setInspectorKind] = useState<"source" | "result">("source");
  const previewedDraft = useRef(false);
  const previewedResults = useRef(new Set<string>());
  const [selectedUploadId, setSelectedUploadId] = useState<string | null>(null);
  const [taskSourceSelection, setTaskSourceSelection] = useState<{
    resultIdentity: string;
    uploadId: string | null;
    evidence: Record<string, unknown> | null;
  } | null>(null);
  const [draftUploads, setDraftUploads] = useState<UploadItem[]>([]);

  const [liveEvents, setLiveEvents] = useState<WorkspaceEvent[]>([]);
  const [helpOpen, setHelpOpen] = useState(false);
  const [exampleSeed, setExampleSeed] = useState<{
    key: string;
    prompt: string;
    formats: string[];
  } | null>(null);


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
        status && ["queued", "running", "cancelling", "pausing"].includes(status)
      ) || ["requested", "running"].includes(verificationStatus ?? "")
        ? 2_000
        : false;
    },
  });
  const conversation = useQuery<{
    turns: Array<{ turn_id: string; text: string; revision: number }>;
    results: SteeringResult[];
    proposals: Array<{ proposal_id: string; base_revision: number; status: string }>;
  }>({
    queryKey: ["workspace-turns", user?.user_id, selectedTaskId, detail.data?.current_revision],
    queryFn: () => api.get(`/api/semantic-workspace/tasks/${encodeURIComponent(selectedTaskId!)}/turns`),
    enabled: Boolean(selectedTaskId && detail.data),
  });
  const task = detail.data;
  const accountResumeKey = JSON.stringify([user?.user_id, task?.task_id, task?.current_revision ?? task?.active_revision, task?.account_resume?.generation]);
  const accountResumeFeedback = accountResumeError?.key === accountResumeKey ? accountResumeError : null;
  const accountResumeStrategy = task?.viewing_revision === task?.current_revision ? task?.account_resume?.strategy : undefined;
  const accountResumeExternal = accountResumeStrategy === "new_revision" && Boolean(task?.model_connection_id);
  const resumeAccountTask = async () => {
    if (!task?.account_resume || accountResumeFlight.current || accountResumeFeedback?.unknown) return;
    const key = accountResumeKey;
    const owner = user?.user_id;
    const selection = selectionParams.current.toString();
    const isCurrent = () => accountResumeOwner.current === owner && selectionParams.current.toString() === selection;
    accountResumeFlight.current = key;
    setAccountResumeBusy(key);
    setAccountResumeError(null);
    try {
      await resumeAccountWorkspaceTask(task.task_id, task.account_resume.generation, task.current_revision ?? task.active_revision, accountResumeConfirmed === key);
      if (!isCurrent()) return;
      setSelectedRevision(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["semantic-workspace-task", task.task_id] }),
        queryClient.invalidateQueries({ queryKey: ["semantic-workspace-tasks"] }),
      ]);
    } catch (error) {
      if (!isCurrent()) return;
      const unknown = !(error instanceof ApiError) || error.status >= 500;
      setAccountResumeError({ key, unknown, message: unknown
        ? "恢复结果未确认，请刷新任务状态；不会自动重发。"
        : "任务当前无法恢复，请刷新后确认暂停状态。" });
    } finally {
      if (accountResumeFlight.current === key) {
        accountResumeFlight.current = null;
        setAccountResumeBusy(null);
      }
    }
  };
  const resultIdentity = JSON.stringify([
    task?.task_id,
    task?.viewing_revision,
    task?.delivery?.delivery_id,
  ]);
  // 缓存命中时也必须在本次渲染排除旧版本选择，不能等 effect 再消除错位。
  const sourceSelection = taskSourceSelection?.resultIdentity === resultIdentity
    ? taskSourceSelection : null;
  const taskUploadId = sourceSelection?.uploadId ?? task?.upload_ids[0] ?? null;

  useEffect(() => {
    if (!task?.delivery || task.status !== "completed" || previewedResults.current.has(resultIdentity)) return;
    previewedResults.current.add(resultIdentity);
    if (inspectorOpen || document.activeElement?.tagName === "TEXTAREA") return;
    setInspectorKind("result");
    setInspectorOpen(true);
  }, [resultIdentity, task?.status, task?.delivery, inspectorOpen]);

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



  const useExample = (example: WorkspaceGuidance["examples"][number]) => {

    setSelectedTaskId(null);
    setDraftUploads([]);
    setSelectedUploadId(null);
    setInspectorOpen(false);
    setComposerDraft(null);
    setWebPrompt(null);
    setWebOpen(false);
    setExampleSeed({
      key: `${example.id}:${Date.now()}`,
      prompt: example.prompt,
      formats: example.output_formats,
    });
  };

  const handleDraftUploadsChange = useCallback((uploads: UploadItem[]) => {
    setDraftUploads(uploads);
    if (!uploads.length) {
      previewedDraft.current = false;
      setSelectedUploadId(null);
      setInspectorOpen(false);
      return;
    }
    if (!previewedDraft.current) {
      previewedDraft.current = true;
      // 上传返回不能切走用户正在输入的内容；预览入口仍常驻。
      if (document.activeElement?.tagName !== "TEXTAREA") setInspectorOpen(true);
      setInspectorKind("source");
    }
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

  const taskNavigation = (
          <WorkspaceTaskSidebar
          tasks={tasks.data || []}
          activeTaskId={selectedTaskId}
          filter={filter}
          recycleBin={recycleBin}
          storage={storage.data}
          onSelect={(taskId) => {
            if (narrow) setNavigationOpen(false);
            setSelectedTaskId(taskId);

            setLiveEvents([]);
          }}
          onFilter={(nextFilter) => {
            setRecycleBin(false);
            setFilter(nextFilter);
          }}
          onNew={() => {
            setComposerDraft(null);
            setWebPrompt(null);
            setWebOpen(false);
            setExampleSeed(null);
            if (narrow) setNavigationOpen(false);
            setRecycleBin(false);
            setSelectedTaskId(null);
            setDraftUploads([]);
            setSelectedUploadId(null);
            setInspectorOpen(false);

          }}
          onToggleRecycleBin={() => {
            setRecycleBin((value) => !value);
            setSelectedTaskId(null);

          }}
          />
  );

  const closeInspector = () => {
    setInspectorOpen(false);
    setInspectorExpanded(false);
    requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('textarea[aria-label="任务要求"], textarea[aria-label="继续对话"]')?.focus());
  };

  const viewSource = (evidence: Record<string, unknown>) => {
    const artifactId = String(evidence.artifact_id || "");
    setTaskSourceSelection({
      resultIdentity,
      uploadId: artifactId && task?.upload_ids.includes(artifactId) ? artifactId : taskUploadId,
      evidence,
    });
    setInspectorKind("source");
    setInspectorOpen(true);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex min-h-14 shrink-0 flex-wrap items-center justify-between gap-2 border-b bg-background px-3 py-2">
        <div>
          <h1 className="text-sm font-semibold">任务工作台</h1>
          <p className="text-[11px] text-muted-foreground">
            表格与文档的理解、执行、验证和正式交付
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" aria-label="任务列表开关" aria-expanded={navigationOpen} onClick={() => setNavigationOpen(value => !value)} className="rounded-lg border px-3 py-2 text-xs hover:bg-muted">任务列表</button>
          {(newTask ? draftUploads.length > 0 : Boolean(task?.uploads?.length)) ? (
            <button
              type="button"
              onClick={() => { setInspectorKind("source"); setInspectorOpen(value => inspectorKind !== "source" || !value); }}
              className="inline-flex items-center gap-1.5 rounded-lg border px-3 py-2 text-xs font-medium hover:bg-muted"
            >
              <FileSearch className="h-3.5 w-3.5" />
              原文件预览
            </button>
          ) : null}
          {task?.delivery && !newTask && <button type="button" className="rounded-lg border px-3 py-2 text-xs hover:bg-muted" onClick={() => { setInspectorKind("result"); setInspectorOpen(true); }}>查看结果</button>}
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

      {settingsOpen && (
        <section className="min-h-0 flex-1 overflow-y-auto p-4" aria-label="模型设置">
          <div className="mx-auto max-w-4xl">
            <h2 className="text-lg font-semibold">模型设置</h2>
            <button type="button" autoFocus className="my-3 rounded-lg border px-3 py-2 text-sm hover:bg-muted" onClick={() => {
              setSettingsOpen(false);
              void queryClient.invalidateQueries({ queryKey: ["model-connections"] });
              void queryClient.invalidateQueries({ queryKey: ["model-connection-preference"] });
              requestAnimationFrame(() => document.querySelector<HTMLElement>(task ? '[aria-label="继续对话"]' : webOpen ? '#web-task-objective, #web-source-url' : '[data-testid="workspace-model-picker"] button')?.focus());
            }}>返回当前任务</button>
            <ModelConnectionsPanel isManager={isAdminish(user?.role)} />
          </div>
        </section>
      )}
      <div className={cn("relative flex min-h-0 flex-1", settingsOpen && "hidden")}>

        {narrow ? (
          <Dialog.Root open={navigationOpen} onOpenChange={setNavigationOpen}>
            <Dialog.Portal>
              <Dialog.Overlay className="fixed inset-0 z-40 bg-foreground/20" />
              <Dialog.Content aria-describedby={undefined} className="fixed inset-y-0 left-0 z-50 max-w-[90vw] bg-background" onCloseAutoFocus={event => {
                event.preventDefault();
                document.querySelector<HTMLButtonElement>('[aria-label="任务列表开关"]')?.focus();
              }}>
                <Dialog.Title className="sr-only">任务列表</Dialog.Title>
                {taskNavigation}
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
        ) : navigationOpen && taskNavigation}

        <div className="min-w-0 flex-1">
          {newTask ? (
            <Group orientation="horizontal" className="h-full min-h-0" defaultLayout={{ draft: 58, preview: 42 }}>
              <Panel id="draft" minSize="0px" className={cn("min-w-0", fullInspector && inspectorOpen && draftUploads.length > 0 && "hidden")}>
              <div className="h-full overflow-y-auto">
                <div
                  className={cn(
                    "mx-auto max-w-5xl",
                    draftUploads.length
                      ? "px-3 pb-4 pt-2"
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
                        描述想得到的结果，添加文件或提供具体公开网址。
                      </p>
                    </div>

                  </>
                ) : (
                  <div className="mx-auto mb-2 hidden max-w-3xl md:block">
                    <h2 className="text-lg font-semibold">核对文件并说明目标</h2>
                    <p className="mt-1 text-xs leading-5 text-muted-foreground">
                      文件已添加，可打开预览核对内容，再说明要筛选、汇总或提取什么。
                    </p>
                  </div>
                )}

                <div
                  className={cn(
                    "mx-auto max-w-3xl",
                    draftUploads.length === 0 && "mt-6",
                  )}
                >
                    <div className={webOpen ? "hidden" : undefined}>
                    <TaskComposer
                    unified
                    onConfigureModels={() => setSettingsOpen(true)}
                    draft={composerDraft}
                    onDraftChange={setComposerDraft}
                    active={!webOpen && !settingsOpen}
                    onReadWeb={draft => { setComposerDraft(draft); setWebPrompt(draft); setWebOpen(true); }}
                    key={exampleSeed?.key || "new-task"}
                    initialPrompt={exampleSeed?.prompt}
                    initialFormats={exampleSeed?.formats}
                    modelOptions={models.data?.options}
                    defaultModel={models.data?.default}
                    allowPiRuntime={Boolean(models.data?.pi_runtime_enabled)}
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
                    </div>
                  {webPrompt !== null && (
                    <section className={cn("mt-3 rounded-xl border p-3", !webOpen && "hidden")} aria-label="网页资料">
                      <button type="button" className="mb-3 rounded-lg border px-3 py-2 text-xs hover:bg-muted" onClick={() => { setWebOpen(false); requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('[aria-label="任务要求"]')?.focus()); }}>返回文件输入</button>
                      <button type="button" className="mb-3 ml-2 rounded-lg border px-3 py-2 text-xs hover:bg-muted" onClick={() => setSettingsOpen(true)}>模型设置</button>
                    <WebSourceIntake
                      draft={composerDraft}
                      onDraftChange={setComposerDraft}
                      active={webOpen && !settingsOpen}
                      initialPrompt={webPrompt.prompt}
                      ownerId={user?.user_id ?? "current"}
                      allowLocalRuntime={canUseLocalPiRuntime}
                      localModels={(models.data?.options ?? [])
                        .filter((model) => model.provider === "local")
                        .map((model) => ({ model: model.model, label: model.label }))}
                      defaultLocalModel={webPrompt.localModel}
                      modelConnections={verifiedModelConnections}
                      defaultConnectionId={webPrompt.connectionId}
                      defaultConnectionModel={webPrompt.connectionModel}
                      onTaskCreated={async (created) => {
                        setWebPrompt(null);
                        setComposerDraft(null);
                        setWebOpen(false);
                        setSelectedTaskId(created.task_id);

                        setRecycleBin(false);
                        setLiveEvents([]);
                        await queryClient.invalidateQueries({
                          queryKey: ["semantic-workspace-tasks"],
                        });
                      }}
                    />
                    </section>
                  )}
                </div>

                </div>
              </div>
              </Panel>
              {inspectorOpen && draftUploads.length > 0 && (
                <>
                {!fullInspector && <Separator aria-label="调整文件预览宽度" className="w-1 bg-border focus-visible:bg-primary" />}
                <Panel id="preview" minSize={fullInspector ? "100%" : "280px"} maxSize={fullInspector ? "100%" : "70%"} className="h-full border-l bg-background">
                  <SourcePreviewPanel
                    key={selectedUploadId}
                    uploads={draftUploads}
                    selectedUploadId={selectedUploadId}
                    evidence={null}
                    onSelectUpload={(uploadId) => {
                      setSelectedUploadId(uploadId);
                      setTaskSourceSelection(null);
                    }}
                    onClose={closeInspector}
                    expanded={inspectorExpanded}
                    onToggleExpand={narrow ? undefined : () => setInspectorExpanded(value => !value)}
                  />
                </Panel>
                </>
              )}
            </Group>
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
              <Panel id="content" minSize="0px" className={cn("min-w-0", fullInspector && inspectorOpen && "hidden")}>
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
                        {["pausing", "paused"].includes(task.current_status ?? task.status) && (
                          <section className="mx-auto max-w-4xl border-b px-6 py-4 text-sm" aria-label="账号暂停恢复">
                            <p className="font-medium">{(task.current_status ?? task.status) === "pausing" ? "任务正在暂停，等待执行停止确认。" : "任务已因账号状态变化暂停，重新启用账号不会自动继续。"}</p>
                            {accountResumeStrategy && (
                              <>
                                <p className="mt-2 text-muted-foreground">{accountResumeStrategy === "waiting"
                                  ? "原问题与执行记录已保留；恢复后仍需你确认，不会自动回答。"
                                  : accountResumeStrategy === "unstarted"
                                    ? "此任务尚未开始；恢复后按原版本重新排队。"
                                    : "原执行已停止；恢复将按原要求创建新版本，保留旧版本记录。"}</p>
                                {accountResumeExternal && (
                                  <label className="mt-3 flex items-start gap-2">
                                    <input type="checkbox" checked={accountResumeConfirmed === accountResumeKey} onChange={(event) => setAccountResumeConfirmed(event.target.checked ? accountResumeKey : null)} />
                                    确认本次新版本继续使用已选外部模型连接，并外发本任务必要数据
                                  </label>
                                )}
                                <button type="button" className="mt-3 rounded-md border px-3 py-2 disabled:opacity-50" disabled={accountResumeBusy !== null || Boolean(accountResumeFeedback?.unknown) || (accountResumeExternal && accountResumeConfirmed !== accountResumeKey)} onClick={() => void resumeAccountTask()}>
                                  {accountResumeStrategy === "waiting" ? "恢复原问题" : accountResumeStrategy === "unstarted" ? "恢复原任务" : "创建新版本恢复"}
                                </button>
                              </>
                            )}
                            {accountResumeFeedback && <p role="alert" className="mt-2 text-destructive">{accountResumeFeedback.message}</p>}
                            {accountResumeFeedback && <button type="button" className="ml-3 mt-3 underline" onClick={() => void detail.refetch()}>刷新任务状态</button>}
                          </section>
                        )}
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
                          onGapAction={async (action) => {
                            const revision = task.current_revision ?? task.active_revision;
                            const candidateHash = task.agentic_runtime
                              ?.reverification_offer?.candidate_set_hash;
                            if (!candidateHash) {
                              throw new Error("缺少冻结 Candidate 身份，请刷新任务后重试");
                            }
                            const fingerprint = `${task.task_id}:${revision}:${candidateHash}:${action}`;
                            if (gapActionAttemptRef.current?.fingerprint !== fingerprint) {
                              gapActionAttemptRef.current = {
                                fingerprint,
                                key: `candidate-gap-${nanoid()}`,
                              };
                            }
                            try {
                              const result = await decideCandidateGap(
                                task.task_id,
                                {
                                  action,
                                  expected_revision: revision,
                                  expected_candidate_set_hash: candidateHash,
                                  external_api_confirmed: Boolean(task.model_connection_id),
                                },
                                gapActionAttemptRef.current.key,
                              );
                              gapActionAttemptRef.current = null;
                              if (action === "accept_gap") {
                                setSelectedRevision(null);
                                toast.success(`已创建结果版本 V${result.target_revision}`);
                              } else if (action === "reject_gap") {
                                toast.info("已保留原目标，当前部分结果不会正式发布");
                              } else if (action === "supplement_source") {
                                toast.info("已记录补充来源，请从新任务区选择新增来源");
                              } else {
                                toast.info("已记录刷新来源，请使用上方“获取最新网页”继续");
                              }
                              await Promise.all([
                                queryClient.invalidateQueries({
                                  queryKey: ["semantic-workspace-task", task.task_id],
                                }),
                                queryClient.invalidateQueries({
                                  queryKey: ["semantic-workspace-tasks"],
                                }),
                              ]);
                            } catch (error) {
                              toast.error(
                                error instanceof Error ? error.message : "缺口决定提交失败",
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
                        <section className="mb-5 space-y-4" aria-label="对话记录">
                          {conversation.isLoading && <p role="status" className="text-sm text-muted-foreground">正在恢复对话…</p>}
                          {conversation.isError && <button type="button" className="rounded-lg border px-3 py-2 text-sm" onClick={() => void conversation.refetch()}>对话读取失败，重试</button>}
                          {conversation.data?.turns?.filter(turn => turn.revision <= (task.viewing_revision ?? task.current_revision ?? task.active_revision)).map(turn => {
                            const response = conversation.data?.results?.find(item => item.turn_id === turn.turn_id);
                            return <article key={turn.turn_id} className="space-y-2 border-b pb-4 text-sm leading-7">
                              <p className="whitespace-pre-wrap font-medium">{turn.text}</p>
                              {response && <><p>{response.acknowledgement}</p>{response.answer && <p className="whitespace-pre-wrap">{response.answer}</p>}</>}
                            </article>;
                          })}
                        </section>
                        {task.status === "completed" && (
                          <p className="text-sm text-muted-foreground">正式结果已生成，可在文件侧栏核对并下载。</p>
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
                          <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                            <span>本任务模型：{task.model || "任务冻结配置"}</span>
                            <button type="button" className="rounded-lg border px-3 py-2 hover:bg-muted" onClick={() => setSettingsOpen(true)}>模型设置</button>
                            <span>设置用于新任务，当前版本保持原模型。</span>
                          </div>
                          <FollowupComposer
                            key={task.task_id}
                            task={task}
                            pendingResults={(conversation.data?.results ?? []).filter(result =>
                              conversation.data?.proposals?.some(proposal =>
                                proposal.proposal_id === result.proposal_id
                                && proposal.status === "pending"
                                && proposal.base_revision === (task.current_revision ?? task.active_revision),
                              ),
                            )}
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
                                await queryClient.invalidateQueries({ queryKey: ["workspace-turns", user?.user_id, task.task_id] });
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
                                await queryClient.invalidateQueries({ queryKey: ["workspace-turns", user?.user_id, task.task_id] });
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
                  {!fullInspector && <Separator className="w-1 border-x bg-border/50 transition-colors hover:bg-primary/30" />}
                  <Panel
                    id="source"
                    minSize={fullInspector ? "100%" : "280px"}
                    maxSize={fullInspector ? "100%" : "65%"}
                    className="bg-background"
                  >
                    {inspectorKind === "result" ? (
                      <div className="h-full overflow-auto p-3">
                        <button type="button" className="mb-3 rounded-lg border px-3 py-2 text-xs hover:bg-muted" onClick={closeInspector}>关闭结果预览</button>
                        {!narrow && <button type="button" className="mb-3 ml-2 rounded-lg border px-3 py-2 text-xs hover:bg-muted" aria-expanded={inspectorExpanded} onClick={() => setInspectorExpanded(value => !value)}>{inspectorExpanded ? "恢复分栏" : "展开预览"}</button>}
                        <ResultPreview key={resultIdentity} task={task} onViewSource={viewSource} />
                      </div>
                    ) : <SourcePreviewPanel
                      key={`${resultIdentity}:${taskUploadId}`}
                      uploads={task.uploads || []}
                      selectedUploadId={taskUploadId}
                      evidence={sourceSelection?.evidence ?? null}
                      onSelectUpload={(uploadId) => {
                        setTaskSourceSelection({ resultIdentity, uploadId, evidence: null });
                      }}
                      onClose={closeInspector}
                    expanded={inspectorExpanded}
                    onToggleExpand={narrow ? undefined : () => setInspectorExpanded(value => !value)}
                    />}
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
