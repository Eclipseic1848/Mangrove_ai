import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  Check,
  Clock3,
  ExternalLink,
  Globe2,
  Loader2,
  ShieldCheck,
  X,
} from "lucide-react";
import { nanoid } from "nanoid/non-secure";
import { toast } from "sonner";
import {
  cancelSourceAcquisition,
  createWorkspaceTask,
  createSourceAcquisition,
  getSourceAcquisition,
} from "@/lib/semanticWorkspaceApi";
import type {
  SourceAcquisitionAttempt,
  WorkspaceTask,
} from "@/types/semanticWorkspace";

type ModelConnection = {
  connection_id: string;
  display_name: string;
  default_model?: string | null;
  models?: Array<{
    model_id: string;
    display_name: string;
    status: string;
    enabled: boolean;
  }>;
};

type WebSourceIntakeProps = {
  ownerId: string;
  allowLocalRuntime: boolean;
  localModels: Array<{ model: string; label: string }>;
  defaultLocalModel: string | null;
  modelConnections: ModelConnection[];
  defaultConnectionId: string | null;
  defaultConnectionModel: string | null;
  onTaskCreated: (task: WorkspaceTask) => Promise<void> | void;
};

const ERROR_LABELS: Record<string, string> = {
  timeout: "网页读取超时",
  dns_error: "域名无法解析",
  network_error: "网络连接失败",
  non_html: "网址返回的不是 HTML 页面",
  content_too_large: "页面超过读取大小上限",
  site_refused: "站点拒绝读取",
  parse_failed: "页面正文无法解析",
  scope_denied: "跳转超出已授权范围",
};

type StoredSourceAcquisition = {
  attempt_id: string | null;
  idempotency_key: string;
  url: string;
  purpose: string;
  scope_kind: "current_page" | "same_site";
  page_limit: number;
  completeness_mode: "exploratory" | "hard_min_pages" | "hard_scope_complete";
  required_valid_pages: number | null;
};

type WorkspaceTaskPayload = Parameters<typeof createWorkspaceTask>[0];

type StoredTaskAttempt = {
  fingerprint: string;
  idempotency_key: string;
  payload: WorkspaceTaskPayload;
};

function readStoredAcquisition(value: string): StoredSourceAcquisition | null {
  try {
    const parsed = JSON.parse(value) as Partial<StoredSourceAcquisition>;
    if (
      typeof parsed.idempotency_key === "string"
      && typeof parsed.url === "string"
      && typeof parsed.purpose === "string"
    ) {
      return {
        attempt_id: typeof parsed.attempt_id === "string" ? parsed.attempt_id : null,
        idempotency_key: parsed.idempotency_key,
        url: parsed.url,
        purpose: parsed.purpose,
        scope_kind: parsed.scope_kind === "same_site" ? "same_site" : "current_page",
        page_limit: typeof parsed.page_limit === "number" ? parsed.page_limit : 1,
        completeness_mode: parsed.completeness_mode === "hard_min_pages"
          || parsed.completeness_mode === "hard_scope_complete"
          ? parsed.completeness_mode
          : "exploratory",
        required_valid_pages: typeof parsed.required_valid_pages === "number"
          ? parsed.required_valid_pages
          : null,
      };
    }
  } catch {
    // 兼容上一版只保存 Attempt ID 的本地记录。
    return {
      attempt_id: value,
      idempotency_key: "",
      url: "",
      purpose: "",
      scope_kind: "current_page",
      page_limit: 1,
      completeness_mode: "exploratory",
      required_valid_pages: null,
    };
  }
  return null;
}

function storeAcquisition(
  storageKey: string,
  attempt: SourceAcquisitionAttempt,
) {
  writeStoredValue(storageKey, {
    attempt_id: attempt.attempt_id,
    idempotency_key: attempt.idempotency_key,
    url: attempt.normalized_url,
    purpose: attempt.purpose,
    scope_kind: attempt.allowed_scope.kind,
    page_limit: attempt.allowed_scope.page_limit ?? 1,
    completeness_mode: attempt.allowed_scope.completeness?.mode ?? "exploratory",
    required_valid_pages: attempt.allowed_scope.completeness?.required_valid_pages ?? null,
  } satisfies StoredSourceAcquisition);
}

function readStoredValue(storageKey: string) {
  try {
    return localStorage.getItem(storageKey);
  } catch {
    return null;
  }
}

function writeStoredValue(storageKey: string, value: unknown) {
  try {
    localStorage.setItem(storageKey, JSON.stringify(value));
  } catch {
    // 存储不可用时仍允许当前页面完成任务，刷新恢复能力降级为本次会话内存。
  }
}

function removeStoredValue(storageKey: string) {
  try {
    localStorage.removeItem(storageKey);
  } catch {
    // 存储不可用时没有可清理的持久状态。
  }
}

function normalizedUrl(value: string) {
  try {
    const url = new URL(value.trim());
    if (!['http:', 'https:'].includes(url.protocol)) return null;
    url.hash = "";
    return url.toString();
  } catch {
    return null;
  }
}

function formatBytes(bytes: number) {
  if (bytes < 1024) return `${bytes} B`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

export function WebSourceIntake({
  ownerId,
  allowLocalRuntime,
  localModels,
  defaultLocalModel,
  modelConnections,
  defaultConnectionId,
  defaultConnectionModel,
  onTaskCreated,
}: WebSourceIntakeProps) {
  const initialConnectionId = defaultConnectionId
    ?? (!allowLocalRuntime ? modelConnections[0]?.connection_id : "")
    ?? "";
  const initialConnection = modelConnections.find(
    (connection) => connection.connection_id === initialConnectionId,
  );
  const initialConnectionModel = defaultConnectionModel
    ?? initialConnection?.default_model
    ?? initialConnection?.models?.find(
      (model) => model.status === "available" && model.enabled,
    )?.model_id
    ?? "";
  const storageKey = `mangrove_web_source_attempt_${ownerId}`;
  const taskStorageKey = `mangrove_web_task_attempt_${ownerId}`;
  const [url, setUrl] = useState("");
  const [purpose, setPurpose] = useState("读取公开网页内容，供当前数据任务分析");
  const [scopeKind, setScopeKind] = useState<"current_page" | "same_site">("current_page");
  const [pageLimit, setPageLimit] = useState(5);
  const [completenessMode, setCompletenessMode] = useState<
    "exploratory" | "hard_min_pages" | "hard_scope_complete"
  >("exploratory");
  const [requiredValidPages, setRequiredValidPages] = useState(3);
  const [attempt, setAttempt] = useState<SourceAcquisitionAttempt | null>(null);
  const [loading, setLoading] = useState(false);
  const [starting, setStarting] = useState(false);
  const [objective, setObjective] = useState("");
  const [mustInclude, setMustInclude] = useState("");
  const [exclusions, setExclusions] = useState("");
  const [quantity, setQuantity] = useState("当前页面中有证据的全部内容");
  const [completeness, setCompleteness] = useState("仅对当前精确页面负责");
  const [format, setFormat] = useState("markdown");
  const [connectionId, setConnectionId] = useState(initialConnectionId);
  const [connectionModel, setConnectionModel] = useState(initialConnectionModel);
  const [localModel, setLocalModel] = useState(
    defaultLocalModel ?? localModels[0]?.model ?? "",
  );
  const [egressConfirmed, setEgressConfirmed] = useState(false);
  const keyRef = useRef<{ fingerprint: string; key: string } | null>(null);
  const taskKeyRef = useRef<{ fingerprint: string; key: string } | null>(null);
  const taskReplayPromiseRef = useRef<Promise<WorkspaceTask> | null>(null);
  const onTaskCreatedRef = useRef(onTaskCreated);
  const normalized = useMemo(() => normalizedUrl(url), [url]);

  useEffect(() => {
    onTaskCreatedRef.current = onTaskCreated;
  }, [onTaskCreated]);

  useEffect(() => {
    const raw = readStoredValue(storageKey);
    if (!raw) return;
    const stored = readStoredAcquisition(raw);
    if (!stored) {
      removeStoredValue(storageKey);
      return;
    }
    let active = true;
    if (stored.url) setUrl(stored.url);
    if (stored.purpose) setPurpose(stored.purpose);
    setScopeKind(stored.scope_kind);
    setPageLimit(stored.page_limit);
    setCompletenessMode(stored.completeness_mode);
    if (stored.required_valid_pages) setRequiredValidPages(stored.required_valid_pages);
    if (stored.idempotency_key) {
      keyRef.current = {
        fingerprint: JSON.stringify({
          url: stored.url,
          purpose: stored.purpose,
          scopeKind: stored.scope_kind,
          pageLimit: stored.page_limit,
          completenessMode: stored.completeness_mode,
          requiredValidPages: stored.required_valid_pages,
        }),
        key: stored.idempotency_key,
      };
    }
    setLoading(!stored.attempt_id);
    const restored = stored.attempt_id
      ? getSourceAcquisition(stored.attempt_id)
      : createSourceAcquisition({
          url: stored.url,
          purpose: stored.purpose,
          allowed_scope: stored.scope_kind,
          page_limit: stored.page_limit,
          completeness_mode: stored.completeness_mode,
          required_valid_pages: stored.required_valid_pages,
        }, stored.idempotency_key);
    void restored
      .then((saved) => {
        if (!active) return;
        setAttempt(saved);
        setUrl(saved.normalized_url);
        setPurpose(saved.purpose);
        storeAcquisition(storageKey, saved);
      })
      .catch(() => {
        // 网络中断时保留同一幂等键，下一次重连不能重复抓取。
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [storageKey]);

  useEffect(() => {
    const raw = readStoredValue(taskStorageKey);
    if (!raw) return;
    let active = true;
    try {
      const stored = JSON.parse(raw) as StoredTaskAttempt;
      if (
        typeof stored.fingerprint !== "string"
        || typeof stored.idempotency_key !== "string"
        || !stored.payload
      ) {
        removeStoredValue(taskStorageKey);
        return;
      }
      taskKeyRef.current = {
        fingerprint: stored.fingerprint,
        key: stored.idempotency_key,
      };
      setStarting(true);
      taskReplayPromiseRef.current ??= createWorkspaceTask(
        stored.payload,
        stored.idempotency_key,
      );
      void taskReplayPromiseRef.current
        .then(async (created) => {
          if (!active) return;
          removeStoredValue(taskStorageKey);
          taskKeyRef.current = null;
          toast.success("已恢复上次网页任务");
          await onTaskCreatedRef.current(created);
        })
        .catch(() => {
          // 结果仍未知时保留原请求；下次重连继续使用同一幂等键。
        })
        .finally(() => {
          if (active) setStarting(false);
        });
    } catch {
      removeStoredValue(taskStorageKey);
    }
    return () => {
      active = false;
    };
  }, [taskStorageKey]);

  useEffect(() => {
    if (!attempt || attempt.status !== "acquiring" || attempt.attempt_id === "pending") {
      return;
    }
    let active = true;
    const timer = window.setInterval(() => {
      void createSourceAcquisition({
        url: attempt.normalized_url,
        purpose: attempt.purpose,
        allowed_scope: attempt.allowed_scope.kind,
        page_limit: attempt.allowed_scope.page_limit ?? 1,
        completeness_mode: attempt.allowed_scope.completeness?.mode ?? "exploratory",
        required_valid_pages: attempt.allowed_scope.completeness?.required_valid_pages ?? null,
      }, attempt.idempotency_key).then((saved) => {
        if (!active) return;
        setAttempt(saved);
        storeAcquisition(storageKey, saved);
      }).catch(() => {
        // 短暂断网不改变服务端持久状态，保持轮询即可。
      });
    }, 1000);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [attempt, storageKey]);

  const submit = async () => {
    if (!normalized || !purpose.trim() || loading) return;
    const effectivePageLimit = scopeKind === "current_page" ? 1 : pageLimit;
    const effectiveRequired = completenessMode === "hard_min_pages"
      ? requiredValidPages
      : null;
    const fingerprint = JSON.stringify({
      url: normalized,
      purpose: purpose.trim(),
      scopeKind,
      pageLimit: effectivePageLimit,
      completenessMode,
      requiredValidPages: effectiveRequired,
    });
    if (keyRef.current?.fingerprint !== fingerprint) {
      keyRef.current = { fingerprint, key: nanoid() };
    }
    writeStoredValue(storageKey, {
      attempt_id: null,
      idempotency_key: keyRef.current.key,
      url: normalized,
      purpose: purpose.trim(),
      scope_kind: scopeKind,
      page_limit: effectivePageLimit,
      completeness_mode: completenessMode,
      required_valid_pages: effectiveRequired,
    } satisfies StoredSourceAcquisition);
    setLoading(true);
    setAttempt({
      attempt_id: "pending",
      idempotency_key: keyRef.current.key,
      request_url: url,
      normalized_url: normalized,
      allowed_scope: {
        kind: scopeKind,
        normalized_url: normalized,
        site: new URL(normalized).host,
        page_limit: effectivePageLimit,
        completeness: {
          mode: completenessMode,
          required_valid_pages: effectiveRequired,
        },
      },
      purpose: purpose.trim(),
      status: "acquiring",
      started_at: new Date().toISOString(),
      finished_at: null,
      snapshot_id: null,
      error_code: null,
      error_message: null,
      snapshot: null,
    });
    try {
      const saved = await createSourceAcquisition({
        url: normalized,
        purpose: purpose.trim(),
        allowed_scope: scopeKind,
        page_limit: effectivePageLimit,
        completeness_mode: completenessMode,
        required_valid_pages: effectiveRequired,
      }, keyRef.current.key);
      setAttempt(saved);
      storeAcquisition(storageKey, saved);
      if (saved.status === "succeeded") toast.success("网页来源已冻结");
    } catch (error) {
      setAttempt(null);
      toast.error(error instanceof Error ? error.message : "网页来源获取失败");
    } finally {
      setLoading(false);
    }
  };

  const cancel = async () => {
    if (!attempt || attempt.attempt_id === "pending") return;
    try {
      const saved = await cancelSourceAcquisition(attempt.attempt_id);
      setAttempt(saved);
      storeAcquisition(storageKey, saved);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "取消失败");
    }
  };

  const clear = () => {
    removeStoredValue(storageKey);
    keyRef.current = null;
    setAttempt(null);
    setUrl("");
  };

  const artifact = attempt?.snapshot?.artifacts[0];
  const snapshot = attempt?.snapshot;
  const selectedConnection = modelConnections.find(
    (connection) => connection.connection_id === connectionId,
  );
  const availableModels = selectedConnection?.models?.filter(
    (model) => model.status === "available" && model.enabled,
  ) ?? [];
  const splitLines = (value: string) => value
    .split(/[\n,，]/)
    .map((item) => item.trim())
    .filter(Boolean);
  const canStart = Boolean(
    attempt?.snapshot_id
    && attempt.snapshot?.coverage.status !== "hard_insufficient"
    && objective.trim()
    && quantity.trim()
    && completeness.trim()
    && (
      (allowLocalRuntime && !connectionId && localModel)
      || (connectionId && connectionModel && egressConfirmed)
    ),
  );

  const startTask = async () => {
    if (!attempt?.snapshot_id || !canStart || starting) return;
    const payload = {
      objective_text: objective.trim(),
      upload_ids: [],
      source_snapshot_id: attempt.snapshot_id,
      must_include: splitLines(mustInclude),
      explicit_exclusions: splitLines(exclusions),
      quantity_requirement: quantity.trim(),
      completeness_requirement: completeness.trim(),
      output_formats: [format],
      runtime_version: "pi" as const,
      permission_profile: "standard" as const,
      provider: "local",
      model: connectionId ? null : localModel,
      model_connection_id: connectionId || null,
      model_connection_model: connectionId ? connectionModel : null,
      external_api_confirmed: Boolean(connectionId && egressConfirmed),
    };
    const fingerprint = JSON.stringify(payload);
    if (taskKeyRef.current?.fingerprint !== fingerprint) {
      taskKeyRef.current = { fingerprint, key: nanoid() };
    }
    writeStoredValue(taskStorageKey, {
      fingerprint,
      idempotency_key: taskKeyRef.current.key,
      payload,
    } satisfies StoredTaskAttempt);
    setStarting(true);
    try {
      const created = await createWorkspaceTask(payload, taskKeyRef.current.key);
      removeStoredValue(taskStorageKey);
      taskKeyRef.current = null;
      toast.success("网页任务已启动");
      await onTaskCreated(created);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : "网页任务启动失败");
    } finally {
      setStarting(false);
    }
  };
  return (
    <section
      aria-labelledby="web-source-title"
      className="overflow-hidden rounded-2xl border bg-background shadow-[0_18px_60px_-38px_hsl(var(--primary)/0.65)]"
    >
      <div className="border-b px-4 py-3">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h3 id="web-source-title" className="text-sm font-semibold">
              获取一个公开网页
            </h3>
            <p className="mt-1 text-xs text-muted-foreground">
              先确认来源事实；此阶段不会创建分析任务或调用模型。
            </p>
          </div>
          {attempt && attempt.status !== "acquiring" && (
            <button
              type="button"
              onClick={clear}
              className="rounded-lg p-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              aria-label="清除网页来源"
            >
              <X className="h-4 w-4" />
            </button>
          )}
        </div>
      </div>

      {!attempt || attempt.status === "acquiring" ? (
        <div className="p-4">
          <label className="block text-xs font-medium" htmlFor="web-source-url">
            精确网址
          </label>
          <div className="relative mt-2">
            <Globe2 className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" />
            <input
              id="web-source-url"
              type="url"
              inputMode="url"
              autoComplete="url"
              value={url}
              disabled={loading}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://example.com/article"
              className="h-10 w-full rounded-xl border bg-background pl-9 pr-3 text-sm outline-none transition-colors focus:border-primary focus:ring-2 focus:ring-primary/15 disabled:opacity-60"
            />
          </div>
          <div aria-live="polite" className="mt-2 min-h-5 text-[11px]">
            {url && normalized ? (
              <span className="text-muted-foreground">
                实际请求：<span className="break-all font-mono text-foreground">{normalized}</span>
              </span>
            ) : url ? (
              <span className="text-destructive">请输入完整的 HTTP 或 HTTPS 网址</span>
            ) : null}
          </div>

          <label className="mt-3 block text-xs font-medium" htmlFor="web-source-purpose">
            来源用途
          </label>
          <textarea
            id="web-source-purpose"
            rows={2}
            value={purpose}
            disabled={loading}
            onChange={(event) => setPurpose(event.target.value)}
            className="mt-2 w-full resize-none rounded-xl border bg-background px-3 py-2 text-sm leading-6 outline-none transition-colors focus:border-primary focus:ring-2 focus:ring-primary/15 disabled:opacity-60"
          />

          <fieldset className="mt-4">
            <legend className="text-xs font-medium">读取范围</legend>
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                type="button"
                disabled={loading}
                aria-pressed={scopeKind === "current_page"}
                onClick={() => {
                  setScopeKind("current_page");
                  setCompletenessMode("exploratory");
                  setQuantity("当前页面中有证据的全部内容");
                  setCompleteness("仅对当前精确页面负责");
                }}
                className={`rounded-lg border px-3 py-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60 ${scopeKind === "current_page" ? "border-primary bg-primary/10 text-foreground" : "text-muted-foreground hover:bg-muted"}`}
              >
                仅当前页面
              </button>
              <button
                type="button"
                disabled={loading}
                aria-pressed={scopeKind === "same_site"}
                onClick={() => {
                  setScopeKind("same_site");
                  setQuantity("当前已成功读取页面中有证据的内容");
                  setCompleteness("披露失败、截断和未覆盖页面，不承诺完整覆盖");
                }}
                className={`rounded-lg border px-3 py-2 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60 ${scopeKind === "same_site" ? "border-primary bg-primary/10 text-foreground" : "text-muted-foreground hover:bg-muted"}`}
              >
                同站有限扩展
              </button>
            </div>
          </fieldset>

          {scopeKind === "same_site" && (
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <label className="text-xs font-medium">
                最多读取页数
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={pageLimit}
                  disabled={loading}
                  onChange={(event) => {
                    const next = Math.max(1, Math.min(50, Number(event.target.value) || 1));
                    setPageLimit(next);
                    setRequiredValidPages((current) => Math.min(current, next));
                  }}
                  className="mt-2 h-10 w-full rounded-xl border bg-background px-3 text-sm font-normal outline-none focus:border-primary focus:ring-2 focus:ring-primary/15 disabled:cursor-not-allowed disabled:opacity-60"
                />
              </label>
              <label className="text-xs font-medium">
                结果要求
                <select
                  value={completenessMode}
                  disabled={loading}
                  onChange={(event) => setCompletenessMode(event.target.value as "exploratory" | "hard_min_pages" | "hard_scope_complete")}
                  className="mt-2 h-10 w-full rounded-xl border bg-background px-3 text-sm font-normal outline-none focus:border-primary focus:ring-2 focus:ring-primary/15 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  <option value="exploratory">能读多少展示多少</option>
                  <option value="hard_min_pages">不足指定页数不启动任务</option>
                  <option value="hard_scope_complete">授权范围内必须全部读取成功</option>
                </select>
              </label>
              {completenessMode === "hard_min_pages" && (
                <label className="text-xs font-medium sm:col-start-2">
                  至少有效页数
                  <input
                    type="number"
                    min={1}
                    max={pageLimit}
                    value={requiredValidPages}
                    disabled={loading}
                    onChange={(event) => setRequiredValidPages(
                      Math.max(1, Math.min(pageLimit, Number(event.target.value) || 1)),
                    )}
                    className="mt-2 h-10 w-full rounded-xl border bg-background px-3 text-sm font-normal outline-none focus:border-primary focus:ring-2 focus:ring-primary/15 disabled:cursor-not-allowed disabled:opacity-60"
                  />
                </label>
              )}
            </div>
          )}

          <div className="mt-4 grid gap-3 border-y py-3 text-xs sm:grid-cols-2">
            <div className="flex gap-2.5">
              <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
              <div>
                <p className="font-medium">
                  {scopeKind === "current_page"
                    ? "允许范围：仅当前页面"
                    : `允许范围：${normalized ? new URL(normalized).host : "当前站点"} 内最多 ${pageLimit} 页`}
                </p>
                <p className="mt-1 leading-5 text-muted-foreground">
                  {scopeKind === "current_page"
                    ? "不跟随页面链接；跨站跳转会直接失败。"
                    : "只跟随同站链接；站外链接只记录、不访问，也不会静默扩大页数。"}
                </p>
              </div>
            </div>
            <div className="flex gap-2.5">
              <ArrowRight className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
              <div>
                <p className="font-medium">可能外发：标题、正文、网址</p>
                <p className="mt-1 leading-5 text-muted-foreground">
                  仅在后续任务中发给你选择的模型；本步骤不调用模型。
                </p>
              </div>
            </div>
          </div>

          <div className="mt-4 flex items-center justify-between gap-3">
            <p className="text-[11px] leading-5 text-muted-foreground">
              页面内容将以读取时间和 SHA-256 冻结，便于后续核验。
            </p>
            {attempt?.status === "acquiring" && attempt.attempt_id !== "pending" ? (
              <button
                type="button"
                onClick={() => void cancel()}
                className="shrink-0 rounded-lg border px-3 py-2 text-xs font-medium hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
              >
                取消获取
              </button>
            ) : (
              <button
                type="button"
                disabled={!normalized || !purpose.trim() || loading}
                aria-busy={loading}
                onClick={() => void submit()}
                className="inline-flex h-9 shrink-0 items-center gap-2 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground transition-colors hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-45"
              >
                {loading ? (
                  <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" />
                ) : (
                  <Globe2 className="h-4 w-4" />
                )}
                {loading ? "正在获取来源" : "获取网页"}
              </button>
            )}
          </div>
        </div>
      ) : attempt.status === "succeeded" && artifact && snapshot ? (
        <div className="p-4" aria-live="polite">
          <div className="flex items-start gap-3">
            <span className="mt-0.5 rounded-full bg-emerald-500/10 p-1.5 text-emerald-700 dark:text-emerald-300">
              <Check className="h-4 w-4" />
            </span>
            <div className="min-w-0 flex-1">
              <p className="text-sm font-semibold">网页来源已冻结</p>
              <a
                href={artifact.final_url}
                target="_blank"
                rel="noreferrer"
                className="mt-1 inline-flex max-w-full items-center gap-1 break-all text-xs text-primary hover:underline"
              >
                {artifact.final_url}
                <ExternalLink className="h-3 w-3 shrink-0" />
              </a>
            </div>
          </div>
          <div className="mt-4 border-y py-4 text-xs">
            <p className="font-medium">
              已冻结 {snapshot.valid_page_count} 个有效页面
              {snapshot.failed_page_count > 0 ? `，另有 ${snapshot.failed_page_count} 个失败或越界记录` : ""}
            </p>
            <p className={`mt-1 leading-5 ${snapshot.coverage.status === "hard_insufficient" ? "text-destructive" : "text-muted-foreground"}`}>
              {snapshot.coverage.status === "scope_complete"
                ? "本次授权范围已读取完成。"
                : snapshot.coverage.status === "hard_insufficient"
                  ? snapshot.allowed_scope.completeness?.mode === "hard_scope_complete"
                    ? "授权站内范围仍有未读取页面，当前结果仅供查看，不能启动任务。"
                    : `有效页面不足 ${snapshot.coverage.required_valid_pages ?? 0} 个，当前结果仅供查看，不能启动任务。`
                  : "已展示当前取得的全部结果；因为达到页数上限或部分页面失败，不能声称覆盖了整个站点。"}
            </p>
            {snapshot.coverage.status === "hard_insufficient" && (
              <p className="mt-2 leading-5 text-muted-foreground">
                下一步：清除本次来源后降低硬性要求、缩小范围，或稍后按原要求重新获取。
              </p>
            )}
            {(snapshot.artifacts.length > 1 || snapshot.failures.length > 0) && (
              <details className="mt-3 rounded-xl border px-3 py-2">
                <summary className="cursor-pointer font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                  查看页面清单与失败原因
                </summary>
                <ul className="mt-3 space-y-2 text-muted-foreground">
                  {snapshot.artifacts.map((item) => (
                    <li key={item.artifact_id} className="break-all">
                      <span className="text-emerald-700 dark:text-emerald-300">成功</span>
                      {" · "}{item.final_url}{" · "}{new Date(item.read_at).toLocaleString("zh-CN")}
                    </li>
                  ))}
                  {snapshot.failures.map((item) => (
                    <li key={item.failure_id} className="break-all">
                      <span className="text-destructive">未读取</span>
                      {" · "}{item.final_url ?? item.request_url}{" · "}
                      {ERROR_LABELS[item.error_code] ?? item.error_message}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
          <dl className="mt-4 grid gap-x-6 gap-y-3 border-y py-4 text-xs sm:grid-cols-3">
            <div>
              <dt className="text-muted-foreground">读取时间</dt>
              <dd className="mt-1 font-medium">{new Date(artifact.read_at).toLocaleString("zh-CN")}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">内容类型 / 大小</dt>
              <dd className="mt-1 font-medium">{artifact.media_type} · {formatBytes(artifact.size_bytes)}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">内容摘要</dt>
              <dd className="mt-1 truncate font-mono font-medium" title={artifact.content_sha256}>
                {artifact.content_sha256.slice(0, 16)}…
              </dd>
            </div>
          </dl>
          <article className="mt-4" aria-label="网页正文预览">
            <p className="text-xs font-medium">{artifact.title || "网页正文预览"}</p>
            <p className="mt-2 max-h-44 overflow-auto whitespace-pre-wrap text-sm leading-6 text-muted-foreground">
              {artifact.text_preview}
            </p>
          </article>
          <div className="mt-5 border-t pt-5">
            <div className="flex flex-wrap items-end justify-between gap-2">
              <div>
                <p className="text-sm font-semibold">确认任务后启动</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  以下内容只在点击启动后冻结；网页不会再次读取。
                </p>
              </div>
              <span className="text-[11px] text-muted-foreground">AgentKernel · 正式 Delivery</span>
            </div>
            <label className="mt-4 block text-xs font-medium" htmlFor="web-task-objective">
              想得到什么结果
            </label>
            <textarea
              id="web-task-objective"
              rows={3}
              value={objective}
              onChange={(event) => setObjective(event.target.value)}
              placeholder="例如：根据当前页面生成一份产品能力摘要，并标注每条结论的来源证据"
              className="mt-2 w-full resize-none rounded-xl border bg-background px-3 py-2 text-sm leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-primary/15"
            />
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <label className="text-xs font-medium">
                必须包含
                <textarea
                  rows={2}
                  value={mustInclude}
                  onChange={(event) => setMustInclude(event.target.value)}
                  placeholder="每行一项；可留空"
                  className="mt-2 w-full resize-none rounded-xl border bg-background px-3 py-2 text-sm font-normal leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-primary/15"
                />
              </label>
              <label className="text-xs font-medium">
                明确不要
                <textarea
                  rows={2}
                  value={exclusions}
                  onChange={(event) => setExclusions(event.target.value)}
                  placeholder="例如：不要推测页面未公开的信息"
                  className="mt-2 w-full resize-none rounded-xl border bg-background px-3 py-2 text-sm font-normal leading-6 outline-none focus:border-primary focus:ring-2 focus:ring-primary/15"
                />
              </label>
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <label className="text-xs font-medium">
                数量要求
                <input
                  value={quantity}
                  onChange={(event) => setQuantity(event.target.value)}
                  className="mt-2 h-10 w-full rounded-xl border bg-background px-3 text-sm font-normal outline-none focus:border-primary focus:ring-2 focus:ring-primary/15"
                />
              </label>
              <label className="text-xs font-medium">
                完整性边界
                <input
                  value={completeness}
                  onChange={(event) => setCompleteness(event.target.value)}
                  className="mt-2 h-10 w-full rounded-xl border bg-background px-3 text-sm font-normal outline-none focus:border-primary focus:ring-2 focus:ring-primary/15"
                />
              </label>
            </div>
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <label className="text-xs font-medium">
                正式交付格式
                <select
                  value={format}
                  onChange={(event) => setFormat(event.target.value)}
                  className="mt-2 h-10 w-full rounded-xl border bg-background px-3 text-sm font-normal outline-none focus:border-primary focus:ring-2 focus:ring-primary/15"
                >
                  <option value="json">JSON</option>
                  <option value="markdown">Markdown</option>
                  <option value="docx">Word</option>
                  <option value="pdf">PDF</option>
                </select>
              </label>
              <label className="text-xs font-medium">
                模型连接
                <select
                  value={connectionId}
                  onChange={(event) => {
                    const next = event.target.value;
                    const connection = modelConnections.find((item) => item.connection_id === next);
                    setConnectionId(next);
                    setConnectionModel(connection?.default_model ?? connection?.models?.find((item) => item.enabled && item.status === "available")?.model_id ?? "");
                    setEgressConfirmed(false);
                  }}
                  className="mt-2 h-10 w-full rounded-xl border bg-background px-3 text-sm font-normal outline-none focus:border-primary focus:ring-2 focus:ring-primary/15"
                >
                  {allowLocalRuntime && <option value="">本地模型（不外发）</option>}
                  {modelConnections.map((connection) => (
                    <option key={connection.connection_id} value={connection.connection_id}>
                      {connection.display_name}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            {connectionId && (
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                <label className="text-xs font-medium">
                  精确模型
                  <select
                    value={connectionModel}
                    onChange={(event) => setConnectionModel(event.target.value)}
                    className="mt-2 h-10 w-full rounded-xl border bg-background px-3 text-sm font-normal outline-none focus:border-primary focus:ring-2 focus:ring-primary/15"
                  >
                    {availableModels.map((model) => (
                      <option key={model.model_id} value={model.model_id}>{model.display_name}</option>
                    ))}
                  </select>
                </label>
                <label className="flex items-start gap-2 rounded-xl border px-3 py-3 text-xs leading-5">
                  <input
                    type="checkbox"
                    checked={egressConfirmed}
                    onChange={(event) => setEgressConfirmed(event.target.checked)}
                    className="mt-0.5 h-4 w-4 rounded border"
                  />
                  <span>我确认将当前公开页面的标题、正文、网址和任务要求发送给所选模型。</span>
                </label>
              </div>
            )}
            {!connectionId && allowLocalRuntime && (
              <label className="mt-3 block text-xs font-medium">
                精确模型
                <select
                  value={localModel}
                  onChange={(event) => setLocalModel(event.target.value)}
                  className="mt-2 h-10 w-full rounded-xl border bg-background px-3 text-sm font-normal outline-none focus:border-primary focus:ring-2 focus:ring-primary/15 sm:max-w-[calc(50%-0.375rem)]"
                >
                  {localModels.map((model) => (
                    <option key={model.model} value={model.model}>{model.label}</option>
                  ))}
                </select>
              </label>
            )}
            <div className="mt-4 flex items-center justify-between gap-3 border-t pt-4">
              <p className="text-[11px] leading-5 text-muted-foreground">
                智能体结果先作为 Candidate；独立验证通过后才会成为正式 Delivery。
              </p>
              <button
                type="button"
                disabled={!canStart || starting}
                aria-busy={starting}
                onClick={() => void startTask()}
                className="inline-flex h-10 shrink-0 items-center gap-2 rounded-lg bg-primary px-4 text-sm font-medium text-primary-foreground hover:bg-primary/90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-45"
              >
                {starting ? <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" /> : <ArrowRight className="h-4 w-4" />}
                {starting ? "正在启动" : "启动任务"}
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div className="p-4" role="alert" aria-live="assertive">
          <div className="flex items-start gap-3">
            {attempt.status === "canceled" ? (
              <Clock3 className="mt-0.5 h-5 w-5 text-muted-foreground" />
            ) : (
              <AlertCircle className="mt-0.5 h-5 w-5 text-destructive" />
            )}
            <div>
              <p className="text-sm font-semibold">
                {attempt.status === "canceled" ? "来源获取已取消" : "没有形成可用来源"}
              </p>
              <p className="mt-1 text-xs leading-5 text-muted-foreground">
                {attempt.error_code
                  ? ERROR_LABELS[attempt.error_code] || attempt.error_message
                  : "本次没有创建快照、分析任务或模型调用。"}
              </p>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
