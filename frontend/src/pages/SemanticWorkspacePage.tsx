import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { nanoid } from "nanoid/non-secure";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as Dialog from "@radix-ui/react-dialog";
import { Group, Panel, Separator } from "react-resizable-panels";
import {
  ArrowLeft,
  Copy,
  FileSearch,
  HelpCircle,
  LayoutTemplate,
  Loader2,
  Pencil,
  RotateCcw,
  Search,
  Share2,
  Sparkles,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { api, ApiError } from "@/lib/api";
import { isAdminish, useAuth } from "@/lib/auth";
import { ModelConnectionsPanel } from "@/pages/settings/ModelConnectionsPanel";
import { type WebIntakeDraft } from "@/components/workspace/TaskComposer";
import { WorkspaceSourceComposer, type SourceTaskPayload } from "@/components/workspace/WorkspaceSourceComposer";
import {
  CandidatePreview,
  ResultPreview,
  initialResultView,
  type ResultViewState,
} from "@/components/workspace/ResultPreview";
import { SourcePreviewPanel, initialSourceView, type SourceViewState } from "@/components/workspace/SourcePreviewPanel";
import { TaskDeletionDialog } from "@/components/workspace/TaskDeletionDialog";
import { TaskTimeline } from "@/components/workspace/TaskTimeline";
import { Markdown } from "@/components/Markdown";
import { WorkspaceTaskSidebar } from "@/components/workspace/WorkspaceTaskSidebar";
import {
  answerWorkspaceTask,
  cancelWorkspaceTask,
  createWorkspaceRevision,
  WorkspaceRevisionError,
  createWorkspaceTask,
  WorkspaceTaskError,
  decideCandidateGap,
  decideWorkspaceRevision,
  getWorkspaceGuidance,
  getWorkspaceStorage,
  getWorkspaceTask,
  listGrayCapabilities,
  listWorkspaceTasks,
  recycleWorkspaceTask,
  requestCandidateReverification,
  publishCandidateVerification,
  refreshWorkspaceSource,
  restoreWorkspaceTask,
  resumeAccountWorkspaceTask,
  sendWorkspaceTurn,
  streamWorkspaceTask,
  readPublicResultContext,
} from "@/lib/semanticWorkspaceApi";
import type { CapabilityNeed, GrayCapability } from "@/lib/semanticWorkspaceApi";
import { cn } from "@/lib/utils";
import type {
  UploadItem,
} from "@/types/dataPrep";
import type {
  WorkspaceEvent,
  WorkspaceMessage,
  WorkspaceGuidance,
  SteeringResult,
  WorkspaceTask,
  WorkspaceQuestion,
  ResultSelection,
  PublicResultContext,
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

function AnswerReferences({ context, onViewSource }: { context: PublicResultContext | null; onViewSource: (ref: Record<string, unknown>, revision: number) => void }) {
  if (!context) return <p className="text-xs text-muted-foreground">未附结构化引用</p>;
  return <div className="space-y-2 border-l-2 border-primary/40 pl-3 text-xs" aria-label="本次追问引用的结果和来源">
    <p>本次追问引用的结果/来源：{context.label} · V{context.revision}</p>
    {context.source_refs.length ? <div className="flex flex-wrap gap-2">{context.source_refs.map((ref, index) => <button type="button" key={index} className="rounded-lg border px-3 py-2 hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring" onClick={() => onViewSource({ ...ref }, context.revision)}>来源 {index + 1}{ref.page ? ` · 第${ref.page}页` : ref.row_number ? ` · 第${ref.row_number}行` : ""}{ref.read_at ? ` · ${new Date(ref.read_at).toLocaleString()}` : ""}</button>)}</div> : <p className="text-muted-foreground">该结果未附原始来源。</p>}
  </div>;
}

function copyAnswer(text: string) {
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard.writeText(text).then(() => toast.success("已复制回答")).catch(() => copyAnswerFallback(text));
    return;
  }
  copyAnswerFallback(text);
}

function copyAnswerFallback(text: string) {
  const input = document.createElement("textarea");
  input.value = text;
  input.style.position = "fixed";
  input.style.opacity = "0";
  document.body.appendChild(input);
  input.select();
  try {
    if (!document.execCommand("copy")) throw new Error("copy failed");
    toast.success("已复制回答");
  } catch {
    toast.error("复制失败，请手动选择正文");
  } finally {
    document.body.removeChild(input);
  }
}

function ConversationActions({ content }: { content: string }) {
  const shareAnswer = async () => {
    if (typeof navigator.share !== "function") {
      toast.error("当前浏览器不支持系统分享，请复制后自行发送");
      return;
    }
    try {
      await navigator.share({ title: "Mangrove 回答", text: content });
    } catch (error) {
      if (!(error instanceof DOMException && error.name === "AbortError")) toast.error("系统分享失败，请复制后自行发送");
    }
  };
  return <div className="mt-2 flex flex-wrap gap-1 text-xs text-muted-foreground" role="group" aria-label="回答操作">
    <button type="button" className="inline-flex items-center gap-1 rounded px-2 py-1 hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring" onClick={() => copyAnswer(content)}><Copy className="h-3.5 w-3.5" />复制</button>
    <AlertDialog.Root>
      <AlertDialog.Trigger asChild><button type="button" className="inline-flex items-center gap-1 rounded px-2 py-1 hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring"><Share2 className="h-3.5 w-3.5" />分享</button></AlertDialog.Trigger>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className="fixed inset-0 z-50 bg-slate-950/45" />
        <AlertDialog.Content className="fixed left-1/2 top-1/2 z-50 w-[min(90vw,440px)] -translate-x-1/2 -translate-y-1/2 rounded-2xl border bg-background p-6 shadow-2xl">
          <AlertDialog.Title className="font-semibold">分享这条回答？</AlertDialog.Title>
          <AlertDialog.Description className="mt-2 text-sm leading-6 text-muted-foreground">只把当前回答正文交给系统分享面板；不会创建公开链接，也不会额外附带原始资料、下载凭据或任务访问权限。</AlertDialog.Description>
          <div className="mt-5 flex justify-end gap-2"><AlertDialog.Cancel className="rounded-lg border px-3 py-2 text-sm hover:bg-muted">取消</AlertDialog.Cancel><AlertDialog.Action className="rounded-lg bg-primary px-3 py-2 text-sm font-medium text-primary-foreground" onClick={() => void shareAnswer()}>打开系统分享</AlertDialog.Action></div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  </div>;
}

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
  scopeIdentity,
  onAnswer,
  onRefreshQuestion,
  onSubmit,
  onDecision,
  pendingResults,
  resultContext,
  onClearResultContext,
  onBusyChange,
  draftRequest,
}: {
  task: WorkspaceTask;
  scopeIdentity: string;
  onAnswer: (question: WorkspaceQuestion, answer: string, key: string) => Promise<WorkspaceTask>;
  onRefreshQuestion: () => void;
  pendingResults: SteeringResult[];
  onSubmit: (text: string, idempotencyKey: string, context?: ResultSelection) => Promise<SteeringResult>;
  resultContext: (ResultSelection & { label: string }) | null;
  onClearResultContext: () => void;
  onBusyChange: (busy: boolean) => void;
  draftRequest: { key: string; text: string } | null;
  onDecision: (
    proposalId: string,
    mode: "cancel_now" | "after_safe_point" | "new_task",
    externalApiConfirmed: boolean,
  ) => Promise<void>;
}) {
  const [text, setText] = useState("");
  const [answerDraft, setAnswerDraft] = useState({ identity: "", text: "" });
  const [answerMode, setAnswerMode] = useState<string | null>(null);
  const [questionExpanded, setQuestionExpanded] = useState(true);
  const [answerFeedback, setAnswerFeedback] = useState<{ identity: string; scope: string; text: string; unknown: boolean } | null>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const inFlight = useRef<object | null>(null);
  const answerAttempt = useRef<{ fingerprint: string; key: string } | null>(null);
  const submitAttempt = useRef<{ fingerprint: string; key: string } | null>(null);
  const [confirmedProposal, setConfirmedProposal] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const contextExpired = Boolean(resultContext && (resultContext.revision !== task.current_revision || task.viewing_revision !== task.current_revision));
  const question = task.question ?? task.understanding?.question;
  const businessQuestion = question?.purpose === "business" ? question : null;
  const questionIdentity = JSON.stringify([scopeIdentity, question?.round_id, question?.origin_turn_id]);
  const isAnswer = answerMode !== null;
  const answerTargetCurrent = Boolean(businessQuestion && answerMode === questionIdentity);
  const currentIdentity = JSON.stringify([scopeIdentity, questionIdentity, answerMode]);
  const latestIdentity = useRef<string | null>(currentIdentity);
  latestIdentity.current = currentIdentity;
  const latestQuestionScope = useRef({ scopeIdentity, question });
  latestQuestionScope.current = { scopeIdentity, question };
  const latestContext = useRef(resultContext);
  latestContext.current = resultContext;
  const answerText = answerDraft.text;
  const latestAnswerDraft = useRef(answerDraft);
  latestAnswerDraft.current = answerDraft;
  const questionAvailable = Boolean(businessQuestion?.round_id && businessQuestion.revision === (task.current_revision ?? task.active_revision)
    && (task.viewing_revision ?? task.current_revision ?? task.active_revision) === businessQuestion.revision
    && businessQuestion.continuation && businessQuestion.continuation !== "unavailable");
  const feedback = answerFeedback && (answerFeedback.identity === questionIdentity
    || (answerFeedback.unknown && answerFeedback.scope === scopeIdentity && !question)) ? answerFeedback : null;
  const usesExternalConnection = Boolean(task.model_connection_id);
  useEffect(() => {
    setText("");
    setConfirmedProposal(null);
  }, [task.task_id]);
  useEffect(() => {
    if (!draftRequest) return;
    setAnswerMode(null);
    setText(draftRequest.text);
    requestAnimationFrame(() => inputRef.current?.focus());
  }, [draftRequest]);
  useEffect(() => { latestIdentity.current = currentIdentity; return () => { latestIdentity.current = null; }; }, []);
  useLayoutEffect(() => {
    // 新轮次可继续编辑；旧请求不能清理新一轮的忙碌状态。
    inFlight.current = null;
    setBusy(false);
    setQuestionExpanded(true);
    if (question) setAnswerFeedback(current => current?.identity === questionIdentity ? current : null);
    onBusyChange(false);
  }, [scopeIdentity, questionIdentity]);
  const chooseMode = (answering: boolean) => {
    if (answering) setAnswerDraft(current => ({ identity: questionIdentity, text: current.text }));
    setAnswerMode(answering ? questionIdentity : null);
    inputRef.current?.focus();
  };
  const submitAnswer = async (value: string) => {
    if (!businessQuestion || !questionAvailable || !value.trim() || value.trim().length > 10000 || inFlight.current || feedback?.unknown) return;
    const capturedIdentity = currentIdentity;
    const submitted = value;
    const fingerprint = JSON.stringify([scopeIdentity, businessQuestion.round_id, businessQuestion.revision, businessQuestion.origin_turn_id, submitted.trim()]);
    if (answerAttempt.current?.fingerprint !== fingerprint) answerAttempt.current = { fingerprint, key: nanoid() };
    const pending = {};
    inFlight.current = pending;
    setBusy(true);
    onBusyChange(true);
    try {
      const result = await onAnswer(businessQuestion, submitted.trim(), answerAttempt.current.key);
      const receipt = result.answer_receipt;
      // 已接收轮次可退出待答；同版本没有新问题时仍保留真实未知收据提示。
      const latest = latestQuestionScope.current;
      if (latestIdentity.current !== null && receipt?.status === "unknown" && receipt.round_id === businessQuestion.round_id
        && receipt.revision === businessQuestion.revision && latest.scopeIdentity === scopeIdentity
        && (!latest.question || latest.question.round_id === businessQuestion.round_id)) {
        setAnswerFeedback({ identity: questionIdentity, scope: scopeIdentity, text: "回答结果未知，请刷新任务核对；不会自动重发。", unknown: true });
        return;
      }
      // 仅清除已接收且未再编辑的原稿；新轮次或在途编辑都保留。
      if (latestIdentity.current !== null && receipt?.status === "accepted" && receipt.round_id === businessQuestion.round_id && receipt.revision === businessQuestion.revision
        && latestAnswerDraft.current.identity === questionIdentity && latestAnswerDraft.current.text === submitted) {
        setAnswerDraft(current => current.identity === questionIdentity && current.text === submitted ? { ...current, text: "" } : current);
        setAnswerMode(current => current === questionIdentity ? null : current);
      }
      if (latestIdentity.current !== capturedIdentity) return;
      if (!receipt || receipt.round_id !== businessQuestion.round_id || receipt.revision !== businessQuestion.revision || receipt.status === "unknown") {
        setAnswerFeedback({ identity: questionIdentity, scope: scopeIdentity, text: "回答结果未知，请刷新任务核对；不会自动重发。", unknown: true });
        return;
      }
      setAnswerFeedback({ identity: questionIdentity, scope: scopeIdentity, text: "回答已接收，正在核对后续状态。", unknown: false });
    } catch (error) {
      if (latestIdentity.current === capturedIdentity) setAnswerFeedback({ identity: questionIdentity, scope: scopeIdentity, text: error instanceof Error ? error.message : "回答提交失败，原稿已保留。", unknown: false });
    } finally {
      if (latestIdentity.current !== null && inFlight.current === pending) {
        inFlight.current = null;
        setBusy(false);
        onBusyChange(false);
      }
    }
  };
  const submit = async () => {
    if (isAnswer) {
      if (businessQuestion?.allow_free_text && answerTargetCurrent) await submitAnswer(answerText);
      return;
    }
    if (!text.trim() || inFlight.current) return;
    const capturedIdentity = currentIdentity;
    const capturedContext = resultContext;
    const submitted = text;
    const context = resultContext ? { revision: resultContext.revision, output_id: resultContext.output_id,
      representation_sha256: resultContext.representation_sha256, item_ref: resultContext.item_ref } : undefined;
    if (context && (context.revision !== task.current_revision || task.viewing_revision !== task.current_revision)) return;
    const fingerprint = JSON.stringify([task.task_id, submitted.trim(), context]);
    if (submitAttempt.current?.fingerprint !== fingerprint) submitAttempt.current = { fingerprint, key: nanoid() };
    const pending = {};
    inFlight.current = pending;
    setBusy(true);
    onBusyChange(true);
    try {
      await onSubmit(submitted.trim(), submitAttempt.current.key, context);
      if (latestIdentity.current !== capturedIdentity) return;
      submitAttempt.current = null;
      setText(current => current === submitted ? "" : current);
      if (context && latestContext.current === capturedContext) onClearResultContext();
    } catch {
      // 父级展示请求错误；失败保留原稿，不自动重复发送。
    } finally {
      if (latestIdentity.current !== null && inFlight.current === pending) {
        inFlight.current = null;
        setBusy(false);
        onBusyChange(false);
      }
    }
  };
  const decide = async (proposalId: string, mode: "cancel_now" | "after_safe_point" | "new_task") => {
    if (inFlight.current) return;
    const capturedIdentity = currentIdentity;
    const pending = {};
    inFlight.current = pending;
    setBusy(true);
    try {
      await onDecision(proposalId, mode, confirmedProposal === proposalId);
      if (latestIdentity.current === capturedIdentity) setConfirmedProposal(null);
    } catch {
      // 父级保留服务端错误，用户可核对状态后重新决定。
    } finally {
      if (latestIdentity.current !== null && inFlight.current === pending) {
        inFlight.current = null;
        setBusy(false);
      }
    }
  };
  return (
    <div className="rounded-2xl border bg-background p-3 shadow-[0_14px_45px_-32px_hsl(var(--primary)/0.7)]">
      {businessQuestion && <section aria-label="当前业务问题" className="mb-3 rounded-xl border bg-muted/20 p-3 text-sm">
        <p className="font-medium">{businessQuestion.prompt}</p>
        <button type="button" aria-expanded={questionExpanded} onClick={() => setQuestionExpanded(current => !current)} className="mt-1 rounded px-2 py-1 text-xs text-muted-foreground hover:bg-accent">{questionExpanded ? "收起问题" : "继续回答"}</button>
        <div hidden={!questionExpanded}>
        {businessQuestion.reason && <p className="mt-1 text-xs leading-5 text-muted-foreground">{businessQuestion.reason}</p>}
        {businessQuestion.affected_scope && <p className="text-xs leading-5 text-muted-foreground">影响：{businessQuestion.affected_scope}</p>}
        {businessQuestion.continuation === "confirm_revision" && <p className="mt-2 text-xs leading-5">补充后需确认“按补充要求重新开始”，将在同一任务创建新版本和新的执行。</p>}
        {!questionAvailable && <p role="status" className="mt-2 text-xs">当前问题无法直接提交，请回到最新版本或刷新任务核对；仍可继续对话提出更正。</p>}
        {questionAvailable && <div className="mt-2 flex flex-wrap gap-2">
          {businessQuestion.options.map(option => <button key={option.value} type="button" disabled={busy || feedback?.unknown} onClick={() => void submitAnswer(option.value)} className="max-w-full rounded-lg border bg-background px-3 py-2 text-left text-xs leading-5 hover:bg-accent disabled:opacity-50">
            {option.label}{option.description && <span className="mt-1 block text-muted-foreground">{option.description}</span>}
          </button>)}
        </div>}
        <div className="mt-3 flex flex-wrap gap-2" aria-label="输入用途">
          <button type="button" aria-pressed={!isAnswer} disabled={busy} onClick={() => chooseMode(false)} className="rounded-lg border px-3 py-2 text-xs aria-pressed:bg-accent aria-pressed:text-accent-foreground">继续对话</button>
          {businessQuestion.allow_free_text && <button type="button" aria-pressed={answerTargetCurrent} disabled={busy || !questionAvailable} onClick={() => chooseMode(true)} className="rounded-lg border px-3 py-2 text-xs aria-pressed:bg-accent aria-pressed:text-accent-foreground">回答这项问题</button>}
        </div>
        {feedback && <p id="clarification-feedback" role="status" className="mt-2 text-xs leading-5">{feedback.text}</p>}
        {(!questionAvailable || feedback) && <button type="button" onClick={onRefreshQuestion} className="mt-2 rounded-lg border px-3 py-2 text-xs">刷新问题状态</button>}
        </div>
      </section>}
      {!businessQuestion && feedback && <div className="mb-2 text-xs leading-5">
        <p id="clarification-feedback" role="status">{feedback.text}</p>
        <button type="button" onClick={onRefreshQuestion} className="mt-2 rounded-lg border px-3 py-2">刷新问题状态</button>
      </div>}
      {isAnswer && !answerTargetCurrent && <div role="status" className="mb-2 text-xs leading-5">
        问题已更新或结束，补充原稿已保留；请明确选择回答当前问题或继续对话。
        {!businessQuestion && <button type="button" onClick={() => chooseMode(false)} className="ml-2 rounded border px-2 py-1">继续对话</button>}
      </div>}
      {resultContext && <div className="mb-2 flex items-start gap-2 rounded-lg border bg-accent p-2 text-xs text-accent-foreground" aria-label="本次追问引用的结果">
        <span className="min-w-0 flex-1 break-words">本次追问引用的结果：{resultContext.label} · V{resultContext.revision}</span>
        <button type="button" disabled={busy} className="shrink-0 rounded px-2 py-1 hover:bg-background focus-visible:ring-2 focus-visible:ring-ring" onClick={onClearResultContext}>移除引用</button>
      </div>}
      {resultContext && isAnswer && <p className="mb-2 text-xs text-muted-foreground">引用与普通对话草稿已保留；本次只回答当前问题。</p>}
      {contextExpired && !isAnswer && <p role="alert" className="mb-2 text-xs text-destructive">所选结果已过期，请回到最新版本重新选择，或移除引用后继续。</p>}
      <textarea
        ref={inputRef}
        aria-label={isAnswer ? "回答这项问题" : "继续对话"}
        aria-describedby={isAnswer && feedback ? "clarification-feedback" : undefined}
        value={isAnswer ? answerText : text}
        onChange={(event) => isAnswer ? setAnswerDraft({ identity: questionIdentity, text: event.target.value }) : setText(event.target.value)}
        maxLength={isAnswer ? 10000 : undefined}
        onKeyDown={(event) => {
          if (!event.nativeEvent.isComposing && event.nativeEvent.keyCode !== 229 && event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            void submit();
          }
        }}
        rows={2}
        placeholder={isAnswer ? "补充这项问题需要的信息；不会替代外发或修改确认" : "可询问进度和原因，也可提出修改；系统会先说明是否影响当前任务"}
        className="w-full min-h-16 max-h-40 [field-sizing:content] resize-none overflow-y-auto bg-transparent px-1 text-sm leading-6 outline-none placeholder:text-muted-foreground/70"
      />
      <div className="mt-2 flex items-center gap-3 border-t pt-2">
        <span className="text-[11px] text-muted-foreground">
          Enter 发送 · Shift + Enter 换行
        </span>
        <button
          type="button"
          disabled={busy || (isAnswer ? !answerText.trim() || !answerTargetCurrent || !questionAvailable || feedback?.unknown : !text.trim() || contextExpired)}
          onClick={() => void submit()}
          className="ml-auto rounded-lg bg-primary px-3 py-2 text-xs font-medium text-primary-foreground disabled:opacity-45"
        >
          {busy ? "正在理解" : isAnswer ? "提交回答" : "发送"}
        </button>
      </div>
      {pendingResults.map(result => (
        <div
          key={result.result_id}
          aria-live="polite"
          className="mt-3 rounded-xl border bg-muted/25 px-3 py-2 text-xs leading-5"
        >
          <p className="font-medium">待确认修改</p>
          {task.clarification_history?.some(entry => entry.turn_id === result.turn_id && entry.question.continuation === "confirm_revision") && <p className="mt-1">按补充要求重新开始：确认后将在本任务创建新版本和新的执行，不恢复原执行。</p>}
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
  const [recoveringCreate, setRecoveringCreate] = useState(false);
  const createRecovery = useRef<{ key: string; promise: ReturnType<typeof createWorkspaceTask> } | null>(null);
  useEffect(() => {
    if (!user?.user_id) return;
    const storageKey = `mangrove_web_task_attempt_${user.user_id}`;
    let stored: { fingerprint: string; idempotency_key: string; payload: Parameters<typeof createWorkspaceTask>[0] } | null = null;
    try { stored = JSON.parse(localStorage.getItem(storageKey) || "null"); } catch { return; }
    if (!stored?.payload || !stored.idempotency_key) return;
    let current = true;
    const draftKey = `mangrove_workspace_draft_${user.user_id}_new`;
    const savedDraft = localStorage.getItem(draftKey), savedFiles = localStorage.getItem(`${draftKey}_files`);
    createAttemptRef.current = { fingerprint: stored.fingerprint, key: stored.idempotency_key };
    setRecoveringCreate(true);
    // 恢复同一完整请求和幂等键，未知结果不能变成第二个任务。
    if (createRecovery.current?.key !== stored.idempotency_key) createRecovery.current = { key: stored.idempotency_key, promise: createWorkspaceTask(stored.payload, stored.idempotency_key) };
    void createRecovery.current.promise.then(created => {
      if (!current) return;
      localStorage.removeItem(storageKey);
      if (localStorage.getItem(draftKey) === savedDraft && localStorage.getItem(`${draftKey}_files`) === savedFiles) { localStorage.removeItem(draftKey); localStorage.removeItem(`${draftKey}_files`); }
      createAttemptRef.current = null;
      setSelectedTaskId(created.task_id);
      void queryClient.invalidateQueries({ queryKey: ["semantic-workspace-tasks"] });
      toast.success("已恢复上次任务");
    }).catch(error => {
      if (!current) return;
      if (error instanceof WorkspaceTaskError && error.rejected) { localStorage.removeItem(storageKey); createAttemptRef.current = null; }
      toast.error("上次任务尚未恢复，资料仍保留；请核对后重试原请求");
    }).finally(() => { if (current) setRecoveringCreate(false); });
    return () => { current = false; };
  }, [user?.user_id]);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [navigationOpen, setNavigationOpen] = useState(() => window.innerWidth >= 1024);
  const [narrow, setNarrow] = useState(() => window.matchMedia("(max-width: 767px)").matches);
  useEffect(() => {
    const media = window.matchMedia("(max-width: 767px)");
    const update = () => { setNarrow(media.matches); if (media.matches) setNavigationOpen(false); };
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, []);
  const [composerDraft, setComposerDraft] = useState<WebIntakeDraft | null>(null);
  const [sourceEditorIdentity, setSourceEditorIdentity] = useState<string | null>(null);
  const sourceEditAttempt = useRef<{ fingerprint: string; key: string } | null>(null);
  const [sourceEditUnknown, setSourceEditUnknown] = useState(false);
  const [sourceEditRecovering, setSourceEditRecovering] = useState(false);
  const [filter, setFilter] = useState<
    "all" | "active" | "needs_input" | "completed"
  >("all");
  const [recycleBin, setRecycleBin] = useState(false);
  const [deletionTarget, setDeletionTarget] = useState<{ task_id: string; title: string } | null>(null);
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
  const [canvasView, setCanvasView] = useState<{
    identity: string; outputId: string | null; results: Record<string, ResultViewState>; sources: Record<string, SourceViewState>;
  }>({ identity: "", outputId: null, results: {}, sources: {} });
  const canvasIdentityRef = useRef("");
  const pendingSource = useRef<{ taskId: string; revision: number; evidence: Record<string, unknown> } | null>(null);
  const [resultDraft, setResultDraft] = useState<{ identity: string; context: ResultSelection & { label: string } } | null>(null);
  const [followupBusy, setFollowupBusy] = useState(false);
  const [resendDraft, setResendDraft] = useState<{ scope: string; key: string; text: string } | null>(null);
  const rerunFlight = useRef(false);

  const [liveFeed, setLiveFeed] = useState<{ identity: string; events: WorkspaceEvent[] }>({ identity: "", events: [] });
  const setLiveEvents = (events: WorkspaceEvent[]) => setLiveFeed({ identity: "", events });
  const [messageFeed, setMessageFeed] = useState<{ identity: string; messages: WorkspaceMessage[] }>({ identity: "", messages: [] });
  const [streamFeedback, setStreamFeedback] = useState<{ identity: string; text: string } | null>(null);
  const [streamAttempt, setStreamAttempt] = useState(0);
  const conversationScroller = useRef<HTMLDivElement>(null);
  const followLatest = useRef(true);
  const [awayFromLatest, setAwayFromLatest] = useState(false);
  const scrollPositions = useRef(new Map<string, { top: number; follow: boolean }>());
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
    queryKey: ["semantic-workspace-task", selectedTaskId, selectedRevision, user?.user_id],
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
  const viewingRevision = task?.viewing_revision ?? task?.current_revision ?? task?.active_revision;
  const runId = task?.agentic_runtime?.run_id ?? task?.work_session?.run_id ?? task?.run_id ?? (typeof task?.run?.run_id === "string" ? task.run.run_id : null);
  const subscriptionIdentity = JSON.stringify([user?.user_id, selectedTaskId, selectedRevision, viewingRevision, runId]);
  const selectedSubscription = useRef(subscriptionIdentity);
  // 同步阻断上一身份的迟到回调和首帧缓存，不能等 effect 清理。
  selectedSubscription.current = subscriptionIdentity;
  const liveEvents = liveFeed.identity === subscriptionIdentity ? liveFeed.events : [];
  const messagesById = new Map<string, WorkspaceMessage>();
  for (const message of [...(task?.messages ?? []), ...(messageFeed.identity === subscriptionIdentity ? messageFeed.messages : [])]) {
    if (message.task_id === selectedTaskId && message.revision === viewingRevision
      && (message.run_id === null || message.run_id === runId) && !messagesById.has(message.message_id)) messagesById.set(message.message_id, message);
  }
  const messages = [...messagesById.values()].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.message_id.localeCompare(b.message_id));
  const messageSignature = JSON.stringify([messages.map(message => message.message_id), conversation.data?.results?.map(result => result.result_id)]);
  const readingIdentity = JSON.stringify([user?.user_id, selectedTaskId, viewingRevision]);
  const answerScope = useRef(readingIdentity);
  answerScope.current = readingIdentity;
  const answerRound = useRef<string | undefined>(undefined);
  answerRound.current = (task?.question ?? task?.understanding?.question)?.round_id;
  const answerQuestion = async (question: WorkspaceQuestion, answer: string, key: string) => {
    if (!task || !question.round_id || !question.revision || question.revision !== (task.current_revision ?? task.active_revision)
      || viewingRevision !== question.revision) throw new Error("问题版本已失效，请刷新任务核对；原稿已保留。");
    const capturedScope = readingIdentity;
    try {
      const result = await answerWorkspaceTask(task.task_id, { answer, expected_revision: question.revision, question_round_id: question.round_id }, key);
      // 仅重新读取原任务的权威投影，不把旧回答结果直接写进当前会话。
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["semantic-workspace-task", task.task_id] }),
        queryClient.invalidateQueries({ queryKey: ["workspace-turns", user?.user_id, task.task_id] }),
        queryClient.invalidateQueries({ queryKey: ["semantic-workspace-tasks"] }),
      ]);
      return result;
    } catch (error) {
      if (answerScope.current === capturedScope && answerRound.current === question.round_id) toast.error(error instanceof Error ? error.message : "提交回答失败");
      throw error;
    }
  };
  const retryCurrentTask = async (externalApiConfirmed: boolean) => {
    if (!task || rerunFlight.current) return;
    if (task.source_integrity?.can_rerun === false) {
      toast.error("来源已删除，不能按原来源重跑；请先核对本次资料。");
      return;
    }
    const externalConnection = Boolean(task.agentic_runtime?.model_connection_id ?? task.model_connection_id);
    if (externalConnection && !externalApiConfirmed) return;
    const storageKey = `mangrove_task_retry_${user?.user_id ?? "unknown"}_${task.task_id}_${task.active_revision}`;
    let idempotencyKey: string;
    try {
      const stored = localStorage.getItem(storageKey);
      idempotencyKey = stored && /^[A-Za-z0-9_-]{1,128}$/.test(stored) ? stored : nanoid();
      localStorage.setItem(storageKey, idempotencyKey);
    } catch {
      toast.error("浏览器无法安全保存重试标识，本次未重新执行。");
      return;
    }
    const capturedScope = readingIdentity;
    rerunFlight.current = true;
    try {
      await createWorkspaceRevision(task.task_id, "保持原要求，重新执行", task.active_revision, undefined, externalApiConfirmed, undefined, idempotencyKey);
      try { localStorage.removeItem(storageKey); } catch { /* 同一幂等键残留只会重放已完成请求。 */ }
      if (answerScope.current === capturedScope) setSelectedRevision(null);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["semantic-workspace-task", task.task_id] }),
        queryClient.invalidateQueries({ queryKey: ["semantic-workspace-tasks"] }),
      ]);
      if (answerScope.current === capturedScope) toast.success("已创建新版本，正在重新执行");
    } catch (error) {
      if (error instanceof WorkspaceRevisionError && error.rejected) {
        try { localStorage.removeItem(storageKey); } catch { /* 服务端已明确拒绝，残留键不会造成重复执行。 */ }
      }
      if (answerScope.current === capturedScope) toast.error(error instanceof WorkspaceRevisionError && error.rejected ? error.message : "重新执行结果未知，请刷新任务核对；不会自动重发。");
    } finally {
      rerunFlight.current = false;
    }
  };
  useLayoutEffect(() => {
    const scroller = conversationScroller.current;
    if (!scroller) return;
    const position = scrollPositions.current.get(readingIdentity);
    followLatest.current = position?.follow ?? true;
    scroller.scrollTop = followLatest.current ? scroller.scrollHeight : position?.top ?? 0;
    setAwayFromLatest(!followLatest.current);
    return () => { scrollPositions.current.set(readingIdentity, { top: scroller.scrollTop, follow: followLatest.current }); };
  }, [readingIdentity, settingsOpen, fullInspector, inspectorOpen]);
  useLayoutEffect(() => {
    if (followLatest.current && conversationScroller.current) conversationScroller.current.scrollTop = conversationScroller.current.scrollHeight;
  }, [messageSignature]);
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
    user?.user_id,
    task?.task_id,
    task?.viewing_revision,
    task?.delivery?.delivery_id,
  ]);
  canvasIdentityRef.current = resultIdentity;
  // 在渲染阶段切换身份，缓存命中与旧组件卸载也不能写回前一修订的阅读状态。
  if (canvasView.identity !== resultIdentity) {
    setCanvasView({ identity: resultIdentity, outputId: null, results: {}, sources: {} });
    setResultDraft(null);
    setFollowupBusy(false);
  }
  const selectedOutputId = canvasView.outputId ?? task?.delivery?.outputs[0]?.output_id ?? null;
  const resultView = canvasView.results[selectedOutputId ?? ""] ?? initialResultView;
  const updateCanvasResult = (patch: Partial<ResultViewState>) => {
    if (canvasIdentityRef.current !== resultIdentity) return;
    setCanvasView(current => current.identity === resultIdentity
      ? { ...current, results: { ...current.results, [selectedOutputId ?? ""]: { ...(current.results[selectedOutputId ?? ""] ?? initialResultView), ...patch } } } : current);
  };
  // 缓存命中时也必须在本次渲染排除旧版本选择，不能等 effect 再消除错位。
  const sourceSelection = taskSourceSelection?.resultIdentity === resultIdentity
    ? taskSourceSelection : null;
  const taskWebSources = task?.web_sources ?? (task?.web_source ? [task.web_source] : []);
  const taskUploadId = sourceSelection?.uploadId ?? task?.upload_ids[0] ?? taskWebSources.find(source => source.snapshot)?.snapshot?.artifacts[0]?.artifact_id ?? task?.delivery_output_ids?.[0] ?? null;

  useEffect(() => {
    const pending = pendingSource.current;
    if (!pending || !task) return;
    if (pending.taskId !== task.task_id) { pendingSource.current = null; return; }
    if (pending.revision !== task.viewing_revision) return;
    pendingSource.current = null;
    setTaskSourceSelection({ resultIdentity, uploadId: String(pending.evidence.artifact_id), evidence: pending.evidence });
    setInspectorKind("source"); setInspectorOpen(true);
  }, [task?.task_id, task?.viewing_revision, resultIdentity]);

  useEffect(() => {
    if (!task?.delivery || task.status !== "completed" || previewedResults.current.has(resultIdentity)) return;
    previewedResults.current.add(resultIdentity);
    if (inspectorOpen || document.activeElement?.tagName === "TEXTAREA") return;
    setInspectorKind("result");
    setInspectorOpen(true);
  }, [resultIdentity, task?.status, task?.delivery, inspectorOpen]);

  useEffect(() => {
    if (!selectedTaskId || !task) return;
    if (viewingRevision !== (task.current_revision ?? task.active_revision)) return;
    if (!["queued", "running", "cancelling"].includes(task.status)) return;
    const identity = subscriptionIdentity;
    const current = (event?: { revision?: number; run_id?: string | null; task_id?: string }) => selectedSubscription.current === identity
      && (!event || (event.revision === viewingRevision && (!event.task_id || event.task_id === selectedTaskId)
        && (event.run_id === null || event.run_id === runId)));
    const refresh = () => {
      void queryClient.invalidateQueries({ queryKey: ["semantic-workspace-task", selectedTaskId] });
      void queryClient.invalidateQueries({ queryKey: ["semantic-workspace-tasks"] });
    };
    return streamWorkspaceTask(selectedTaskId, {
      onProgress: (event) => {
        if (!current(event)) return;
        setLiveFeed(previous => {
          const events = previous.identity === identity ? previous.events : [];
          return { identity, events: events.some(item => item.event_id === event.event_id) ? events : [...events, event] };
        });
      },
      onMessage: (message) => {
        if (!current(message)) return;
        setStreamFeedback(null);
        setMessageFeed(previous => {
          const saved = previous.identity === identity ? previous.messages : [];
          return { identity, messages: saved.some(item => item.message_id === message.message_id) ? saved : [...saved, message] };
        });
        void queryClient.invalidateQueries({ queryKey: ["workspace-turns", user?.user_id, selectedTaskId] });
      },
      onStatus: (event) => {
        if (!current(event)) return;
        setStreamFeedback(previous => previous?.identity === identity && previous.text ? { identity, text: "连接已恢复" } : previous);
        refresh();
      },
      onDone: (event) => {
        if (!current(event)) return;
        refresh();
        void queryClient.invalidateQueries({ queryKey: ["workspace-turns", user?.user_id, selectedTaskId] });
        void queryClient.invalidateQueries({ queryKey: ["semantic-workspace-storage"] });
      },
      onError: (error) => {
        if (!current()) return;
        setStreamFeedback({ identity, text: error instanceof ApiError && [401, 403, 404, 409, 429].includes(error.status)
          ? "更新连接不可用，请重新读取任务。" : "连接中断，正在恢复已保存的记录。" });
      },
    }, viewingRevision, runId);
  }, [queryClient, selectedTaskId, task?.status, subscriptionIdentity, viewingRevision, runId, user?.user_id, streamAttempt]);



  const useExample = (example: WorkspaceGuidance["examples"][number]) => {

    setSelectedTaskId(null);
    setDraftUploads([]);
    setSelectedUploadId(null);
    setInspectorOpen(false);
    setComposerDraft(null);
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

  const submitNew = async (payload: SourceTaskPayload) => {
    const storageKey = `mangrove_web_task_attempt_${user?.user_id}`;
    const owner = user?.user_id;
    const draftKey = `mangrove_workspace_draft_${owner}_new`;
    const savedDraft = localStorage.getItem(draftKey), savedFiles = localStorage.getItem(`${draftKey}_files`);
    try {
      const requestPayload = {
        objective_text: payload.prompt,
        upload_ids: [...new Set(payload.uploads.map((upload) => upload.upload_id))],
        source_snapshot_ids: payload.sourceSnapshotIds,
        delivery_output_ids: payload.deliveryOutputIds,
        ...payload.sourceGoal,
        ...payload.taskContext,
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
        ...(payload.capabilityNeed ? { capability_need: payload.capabilityNeed } : {}),
      };
      const fingerprint = JSON.stringify(requestPayload);
      if (createAttemptRef.current && createAttemptRef.current.fingerprint !== fingerprint) throw new Error("上次创建结果未知，请刷新恢复原请求后再更改资料");
      if (createAttemptRef.current?.fingerprint !== fingerprint) {
        createAttemptRef.current = {
          fingerprint,
          key: nanoid(),
        };
      }
      localStorage.setItem(storageKey, JSON.stringify({ fingerprint, idempotency_key: createAttemptRef.current.key, payload: requestPayload }));
      const created = await createWorkspaceTask(
        requestPayload,
        createAttemptRef.current.key,
      );
      if (accountResumeOwner.current !== owner) return;
      if (localStorage.getItem(draftKey) === savedDraft && localStorage.getItem(`${draftKey}_files`) === savedFiles) { localStorage.removeItem(draftKey); localStorage.removeItem(`${draftKey}_files`); }
      localStorage.removeItem(storageKey);
      createAttemptRef.current = null;
      setSelectedTaskId(created.task_id);

      setRecycleBin(false);
      setLiveEvents([]);
      await queryClient.invalidateQueries({
        queryKey: ["semantic-workspace-tasks"],
      });
    } catch (error) {
      if (error instanceof WorkspaceTaskError && error.rejected) { localStorage.removeItem(storageKey); createAttemptRef.current = null; }
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

  const viewSource = (evidence: Record<string, unknown>, revision = task?.viewing_revision) => {
    const artifactId = String(evidence.artifact_id || "");
    if (!artifactId || !task || !revision) { toast.error("引用缺少可核验来源身份"); return; }
    const requested = { ...evidence, _requestId: nanoid() };
    if (revision !== task.viewing_revision) {
      pendingSource.current = { taskId: task.task_id, revision, evidence: requested };
      setSelectedRevision(revision); return;
    }
    setTaskSourceSelection({
      resultIdentity,
      uploadId: artifactId,
      evidence: requested,
    });
    setInspectorKind("source");
    setInspectorOpen(true);
  };

  return (
    <div className="flex h-full min-h-0 flex-col">
      {user && <TaskDeletionDialog key={user.user_id} ownerId={user.user_id} target={deletionTarget} currentTaskId={selectedTaskId} onClose={() => setDeletionTarget(null)} onCompleted={operation => {
        if (selectedTaskId === operation.task_id) setSelectedTaskId(null);
        void Promise.all(["semantic-workspace-task", "semantic-workspace-tasks", "semantic-workspace-storage", "workspace-task-source", "workspace-task-derived-source", "workspace-source-file"].map(key => queryClient.invalidateQueries({ queryKey: [key] })));
      }} />}
      <header className="flex min-h-14 shrink-0 flex-wrap items-center justify-between gap-2 border-b bg-background px-3 py-2">
        <div>
          <h1 className="text-sm font-semibold">任务工作台</h1>
          <p className="text-[11px] text-muted-foreground">
            表格与文档的理解、执行、验证和正式交付
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button type="button" aria-label="任务列表开关" aria-expanded={navigationOpen} onClick={() => setNavigationOpen(value => !value)} className="rounded-lg border px-3 py-2 text-xs hover:bg-muted">任务列表</button>
          {(newTask ? draftUploads.length > 0 : Boolean(task?.uploads?.length || taskWebSources.some(source => source.snapshot?.artifacts.length) || task?.delivery_output_ids?.length)) ? (
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
              requestAnimationFrame(() => document.querySelector<HTMLElement>(task ? '[aria-label="继续对话"]' : 'textarea[aria-label="任务要求"]')?.focus());
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
                    <div>
                    <WorkspaceSourceComposer
                    ownerId={user?.user_id ?? "current"}
                    unified
                    onConfigureModels={() => setSettingsOpen(true)}
                    draft={composerDraft}
                    onDraftChange={setComposerDraft}
                    active={!settingsOpen && !recoveringCreate}
                    key={`${user?.user_id}:${exampleSeed?.key || "new-task"}`}
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
                          移入回收站不会清理资料。可恢复任务记录；永久清理需要另行确认。
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
                          <button type="button" className="rounded-lg border border-destructive/30 px-3 py-2 text-xs font-medium text-destructive" onClick={() => setDeletionTarget({ task_id: task.task_id, title: task.title })}>永久删除</button>
                        </div>
                      </div>
                    </div>
                  ) : (
                    <>
                      <div ref={conversationScroller} data-testid="workspace-conversation-scroll" className="min-h-0 flex-1 overflow-y-auto" onScroll={event => {
                        const scroller = event.currentTarget;
                        followLatest.current = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
                        setAwayFromLatest(!followLatest.current);
                      }}>
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
                          key={`${user?.user_id}:${task.task_id}:${task.active_revision}`}
                          task={task}
                          externalConnection={Boolean(task.agentic_runtime?.model_connection_id ?? task.model_connection_id)}
                          connectionLabel={modelConnections.data?.items.find(connection => connection.connection_id === (task.agentic_runtime?.model_connection_id ?? task.model_connection_id))?.display_name}
                          clarificationTurnIds={conversation.data?.turns.map(turn => turn.turn_id)}
                          liveEvents={liveEvents}
                          onAnswer={answerQuestion}
                          onRefreshQuestion={() => { void detail.refetch(); }}
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
                          onRetry={async (unchanged = false, externalApiConfirmed = false) => {
                            if (task.source_integrity?.can_rerun === false) { toast.error("来源已删除，不能按原来源重跑；请先核对本次资料。"); return; }
                            if (!unchanged) {
                              document
                                .getElementById("workspace-revision-composer")
                                ?.scrollIntoView({ behavior: "smooth" });
                              return;
                            }
                            await retryCurrentTask(externalApiConfirmed);
                          }}
                          onRefreshSource={async (externalApiConfirmed, targetSourceSnapshotId) => {
                            const expectedRevision = task.current_revision
                              ?? task.active_revision;
                            const fingerprint = `${task.task_id}:${expectedRevision}${taskWebSources.length > 1 ? `:${targetSourceSnapshotId}` : ""}`;
                            const storageKey = `mangrove_source_refresh_${user?.user_id ?? "unknown"}_${task.task_id}`;
                            const priorRefresh = localStorage.getItem(storageKey);
                            if (priorRefresh) {
                              const prior = JSON.parse(priorRefresh) as { fingerprint?: string };
                              if (prior.fingerprint?.startsWith(`${task.task_id}:${expectedRevision}:`) && prior.fingerprint !== fingerprint) throw new Error("另一网页组的刷新结果仍未知，请先选择原组恢复同一请求");
                            }
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
                                targetSourceSnapshotId,
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
                        <section className="mx-auto mb-5 max-w-4xl space-y-4 px-6" aria-label="对话记录">
                          {conversation.isLoading && <p role="status" className="text-sm text-muted-foreground">正在恢复对话…</p>}
                          {conversation.isError && <button type="button" className="rounded-lg border px-3 py-2 text-sm" onClick={() => void conversation.refetch()}>对话读取失败，重试</button>}
                          {conversation.data?.turns?.filter(turn => turn.revision <= (task.viewing_revision ?? task.current_revision ?? task.active_revision)).map(turn => {
                            const response = conversation.data?.results?.find(item => item.turn_id === turn.turn_id && item.revision <= viewingRevision!);
                            const message = messages.find(message => message.turn_id === turn.turn_id);
                            const answer = message?.content ?? response?.answer;
                            const context = readPublicResultContext(message?.result_context ?? response?.result_context);
                            const clarification = task.clarification_history?.find(entry => entry.turn_id === turn.turn_id);
                            return <article key={turn.turn_id} className="space-y-2 border-b pb-4 text-sm leading-7">
                              {clarification && <p className="text-muted-foreground">{clarification.question.prompt}</p>}
                              <div className="flex items-start gap-2"><p className="min-w-0 flex-1 whitespace-pre-wrap font-medium">{turn.text}</p><button type="button" disabled={followupBusy} className="inline-flex shrink-0 items-center gap-1 rounded px-2 py-1 text-xs text-muted-foreground hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50" onClick={() => { setResultDraft(null); setResendDraft({ scope: readingIdentity, key: nanoid(), text: turn.text }); }}><Pencil className="h-3.5 w-3.5" />编辑为新消息</button></div>
                              {response && <p className="text-muted-foreground">{response.acknowledgement}</p>}
                              {answer && <div aria-label="Mangrove 回答"><Markdown safeResources>{answer}</Markdown></div>}
                              {answer && <AnswerReferences context={context && context.revision === (message?.revision ?? response?.revision) ? context : null} onViewSource={viewSource} />}
                              {answer && <ConversationActions content={answer} />}
                            </article>;
                          })}
                          {messages.filter(message => !conversation.data?.turns?.some(turn => turn.turn_id === message.turn_id)).map(message => (
                            <article key={message.message_id} aria-label="Mangrove 回答" className="text-sm leading-7"><Markdown safeResources>{message.content}</Markdown><AnswerReferences context={message.result_context?.revision === message.revision ? readPublicResultContext(message.result_context) : null} onViewSource={viewSource} /><ConversationActions content={message.content} /></article>
                          ))}
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
                              if (task.source_integrity?.can_reverify === false) throw new Error("来源已删除，不能完整复验；历史 QA 与独立结果仍保留。");
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
                        <div id="workspace-revision-composer" className="mx-auto mb-6 max-w-4xl px-6">
                          {viewingRevision === (task.current_revision ?? task.active_revision) && ["completed", "failed", "cancelled", "candidate_ready"].includes(task.status) && <>
                            <button type="button" aria-expanded={sourceEditorIdentity === resultIdentity} onClick={() => setSourceEditorIdentity(current => current === resultIdentity ? null : resultIdentity)} className="rounded-lg border px-3 py-2 text-sm hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">编辑本次资料</button>
                            {(() => {
                              const prefix = `mangrove_source_revision_${user?.user_id}_${task.task_id}_`;
                              const pendingKey = Object.keys(localStorage).find(key => key.startsWith(prefix));
                              if (!pendingKey && !sourceEditUnknown) return null;
                              if (!pendingKey) return null;
                              return <div role="status" className="mt-3 rounded-lg border p-3 text-xs"><p>上次资料修订结果未知，原请求已保留。恢复只确认该请求，当前草稿不会作为第二次修订发送。</p><button type="button" disabled={sourceEditRecovering} className="mt-2 rounded-lg border px-3 py-2 hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" onClick={async () => {
                                const attempt = JSON.parse(localStorage.getItem(pendingKey) || "null") as { key: string; prompt: string; formats: string[]; externalApiConfirmed: boolean; selection: { upload_ids: string[]; source_snapshot_ids: string[]; delivery_output_ids?: string[] } } | null;
                                if (!attempt) return;
                                setSourceEditRecovering(true);
                                try {
                                  const revision = await createWorkspaceRevision(task.task_id, attempt.prompt, Number(pendingKey.slice(prefix.length)), attempt.formats, attempt.externalApiConfirmed, attempt.selection, attempt.key);
                                  localStorage.removeItem(pendingKey); setSourceEditUnknown(false);
                                  toast.success(`已确认原请求创建的版本 V${revision.revision}`);
                                  await queryClient.invalidateQueries({ queryKey: ["semantic-workspace-task", task.task_id] });
                                } catch (error) { if (error instanceof WorkspaceRevisionError && error.rejected) { localStorage.removeItem(pendingKey); setSourceEditUnknown(false); } toast.error(error instanceof Error ? error.message : "原请求尚未确认，继续保留资料"); }
                                finally { setSourceEditRecovering(false); }
                              }}>{sourceEditRecovering ? "正在恢复原请求" : "恢复上次资料修订"}</button></div>;
                            })()}
                            {sourceEditorIdentity === resultIdentity && <div className="mt-3">
                              <p className="mb-3 text-xs text-muted-foreground">确认后创建新版本。使用原任务模型与上下文；上方旧版本来源保持冻结。</p>
                              <WorkspaceSourceComposer key={`${resultIdentity}:sources`} ownerId={user?.user_id ?? "current"} draftScope={`${task.task_id}_${viewingRevision}`} compact unified preserveContext modelLocked
                                initialPrompt="保持原要求，使用当前选择的全部资料" initialFormats={task.output_formats}
                                initialUploads={task.uploads ?? []} initialSources={taskWebSources.flatMap(source => source.snapshot ? [source.snapshot] : [])} initialUnavailableSourceIds={taskWebSources.filter(source => !source.snapshot).map(source => source.source_snapshot_id)} initialReusableSources={task.reusable_sources ?? []}
                                modelOptions={models.data?.options} defaultModel={{ provider: task.provider, model: task.model || "", label: "任务冻结模型" }}
                                allowPiRuntime={Boolean(models.data?.pi_runtime_enabled)} allowLocalPiRuntime={canUseLocalPiRuntime}
                                modelConnections={verifiedModelConnections.filter(connection => connection.connection_id === task.model_connection_id)}
                                defaultConnectionId={task.model_connection_id} defaultConnectionModel={task.agentic_runtime?.model_connection_model ?? task.web_source?.runtime_binding.model ?? task.model}
                                onSubmit={async payload => {
                                  try {
                                  const selection = { upload_ids: [...new Set(payload.uploads.map(upload => upload.upload_id))], source_snapshot_ids: payload.sourceSnapshotIds, delivery_output_ids: payload.deliveryOutputIds };
                                  const identity = resultIdentity;
                                  const fingerprint = JSON.stringify([task.task_id, task.current_revision ?? task.active_revision, payload.prompt, payload.formats, selection]);
                                  const pendingKey = `mangrove_source_revision_${user?.user_id}_${task.task_id}_${task.current_revision ?? task.active_revision}`;
                                  const stored = JSON.parse(localStorage.getItem(pendingKey) || "null") as { fingerprint: string; key: string; prompt: string; formats: string[]; externalApiConfirmed: boolean; selection: typeof selection } | null;
                                  if (stored && stored.fingerprint !== fingerprint) throw new Error("上次资料修订结果未知，请恢复原资料和要求后重试同一请求");
                                  const attempt = stored ?? { fingerprint, key: nanoid(), prompt: payload.prompt, formats: payload.formats, externalApiConfirmed: payload.externalApiConfirmed, selection };
                                  sourceEditAttempt.current = attempt;
                                  localStorage.setItem(pendingKey, JSON.stringify(attempt));
                                  setSourceEditUnknown(false);
                                  try {
                                    await createWorkspaceRevision(task.task_id, attempt.prompt, task.current_revision ?? task.active_revision, attempt.formats, attempt.externalApiConfirmed, attempt.selection, attempt.key);
                                    localStorage.removeItem(pendingKey);
                                  } catch (error) {
                                    if (error instanceof WorkspaceRevisionError && error.rejected) localStorage.removeItem(pendingKey);
                                    else setSourceEditUnknown(true);
                                    throw error;
                                  }
                                  if (canvasIdentityRef.current !== identity) return;
                                  sourceEditAttempt.current = null;
                                  setSourceEditorIdentity(null); setSelectedRevision(null);
                                  await Promise.all([queryClient.invalidateQueries({ queryKey: ["semantic-workspace-task", task.task_id] }), queryClient.invalidateQueries({ queryKey: ["semantic-workspace-tasks"] })]);
                                  toast.success("已按完整资料集合创建新版本");
                                  } catch (error) { toast.error(error instanceof Error ? error.message : "资料修订未确认，已保留当前草稿"); throw error; }
                                }} />
                            </div>}
                          </>}
                        </div>
                      </div>
                      <div className="shrink-0 border-t bg-background/95 px-6 py-3 backdrop-blur">
                        <div className="mx-auto max-w-4xl">
                          <p role="status" aria-label="对话更新" aria-live="polite" aria-atomic="true" className="text-xs text-muted-foreground">
                            {streamFeedback?.identity === subscriptionIdentity ? streamFeedback.text : messages.length ? `已收到 ${messages.length} 条完整回答` : ""}
                          </p>
                          {streamFeedback?.identity === subscriptionIdentity && streamFeedback.text !== "连接已恢复" && <button type="button" className="my-2 rounded-lg border px-3 py-2 text-xs" onClick={() => {
                            void detail.refetch();
                            setStreamAttempt(attempt => attempt + 1);
                          }}>重新读取任务</button>}
                          {awayFromLatest && <button type="button" className="mb-2 rounded-lg border px-3 py-2 text-xs focus-visible:ring-2 focus-visible:ring-ring" onClick={() => {
                            followLatest.current = true;
                            setAwayFromLatest(false);
                            conversationScroller.current?.scrollTo({ top: conversationScroller.current.scrollHeight, behavior: "instant" });
                          }}>回到最新</button>}
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
                            <span>本任务模型：{task.agentic_runtime?.model_connection_model || task.web_source?.runtime_binding.model || task.model || "任务冻结配置"}</span>
                            <button type="button" className="rounded-lg border px-3 py-2 hover:bg-muted" onClick={() => setSettingsOpen(true)}>模型设置</button>
                            <span>设置用于新任务，当前版本保持原模型。</span>
                          </div>
                          <FollowupComposer
                            key={`${user?.user_id}:${task.task_id}`}
                            task={task}
                            scopeIdentity={readingIdentity}
                            draftRequest={resendDraft?.scope === readingIdentity ? resendDraft : null}
                            onAnswer={answerQuestion}
                            onRefreshQuestion={() => { void detail.refetch(); }}
                            resultContext={resultDraft?.identity === resultIdentity ? resultDraft.context : null}
                            onClearResultContext={() => setResultDraft(current => current?.identity === resultIdentity ? null : current)}
                            onBusyChange={busy => { if (canvasIdentityRef.current === resultIdentity) setFollowupBusy(busy); }}
                            pendingResults={(conversation.data?.results ?? []).filter(result =>
                              conversation.data?.proposals?.some(proposal =>
                                proposal.proposal_id === result.proposal_id
                                && proposal.status === "pending"
                                && proposal.base_revision === (task.current_revision ?? task.active_revision),
                              ),
                            )}
                            onSubmit={async (text, idempotencyKey, context) => {
                              const capturedScope = readingIdentity;
                              try {
                                const result = await sendWorkspaceTurn(
                                  task.task_id,
                                  text,
                                  idempotencyKey,
                                  context,
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
                                if (answerScope.current === capturedScope) toast.error(
                                  error instanceof Error
                                    ? error.message
                                    : "追问处理失败",
                                );
                                throw error;
                              }
                            }}
                            onDecision={async (proposalId, mode, externalConfirmed) => {
                              const capturedScope = readingIdentity;
                              try {
                                await decideWorkspaceRevision(
                                  task.task_id,
                                  proposalId,
                                  mode,
                                  externalConfirmed,
                                );
                                if (answerScope.current !== capturedScope) return;
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
                                if (answerScope.current === capturedScope) toast.error(
                                  error instanceof Error
                                    ? error.message
                                    : "确认修改失败",
                                );
                                throw error;
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
                      <div className="h-full overflow-auto p-3" ref={element => { if (element) element.scrollTop = resultView.bodyTop ?? 0; }}
                        onScroll={event => updateCanvasResult({ bodyTop: event.currentTarget.scrollTop })}>
                        <button type="button" className="mb-3 rounded-lg border px-3 py-2 text-xs hover:bg-muted" onClick={closeInspector}>关闭结果预览</button>
                        {!narrow && <button type="button" className="mb-3 ml-2 rounded-lg border px-3 py-2 text-xs hover:bg-muted" aria-expanded={inspectorExpanded} onClick={() => setInspectorExpanded(value => !value)}>{inspectorExpanded ? "恢复分栏" : "展开预览"}</button>}
                        <ResultPreview key={`${resultIdentity}:${selectedOutputId}`} task={task} onViewSource={viewSource} viewState={resultView} onViewStateChange={updateCanvasResult}
                          outputId={selectedOutputId} onSelectOutput={outputId => setCanvasView(current => ({ ...current, outputId }))}
                          selectionDisabled={followupBusy}
                          onSelectResult={(selection, label) => {
                            if (followupBusy || selection.revision !== task.current_revision || canvasIdentityRef.current !== resultIdentity) return;
                            setResultDraft({ identity: resultIdentity, context: { ...selection, label } });
                            if (fullInspector) setInspectorOpen(false);
                            requestAnimationFrame(() => document.querySelector<HTMLTextAreaElement>('textarea[aria-label="继续对话"]')?.focus());
                          }} />
                      </div>
                    ) : <SourcePreviewPanel
                      key={`${resultIdentity}:${taskUploadId}`}
                      task={task}
                      uploads={task.uploads || []}
                      selectedUploadId={taskUploadId}
                      evidence={sourceSelection?.evidence ?? null}
                      viewState={canvasView.sources[taskUploadId ?? ""] ?? initialSourceView}
                      onViewStateChange={patch => {
                        if (canvasIdentityRef.current !== resultIdentity) return;
                        const id = taskUploadId ?? "";
                        setCanvasView(current => current.identity === resultIdentity ? { ...current, sources: {
                          ...current.sources, [id]: { ...(current.sources[id] ?? initialSourceView), ...patch },
                        } } : current);
                      }}
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
