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
  createSourceAcquisition,
  getSourceAcquisition,
} from "@/lib/semanticWorkspaceApi";
import type { SourceAcquisitionAttempt } from "@/types/semanticWorkspace";

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
      };
    }
  } catch {
    // 兼容上一版只保存 Attempt ID 的本地记录。
    return {
      attempt_id: value,
      idempotency_key: "",
      url: "",
      purpose: "",
    };
  }
  return null;
}

function storeAcquisition(
  storageKey: string,
  attempt: SourceAcquisitionAttempt,
) {
  localStorage.setItem(storageKey, JSON.stringify({
    attempt_id: attempt.attempt_id,
    idempotency_key: attempt.idempotency_key,
    url: attempt.normalized_url,
    purpose: attempt.purpose,
  } satisfies StoredSourceAcquisition));
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

export function WebSourceIntake({ ownerId }: { ownerId: string }) {
  const storageKey = `mangrove_web_source_attempt_${ownerId}`;
  const [url, setUrl] = useState("");
  const [purpose, setPurpose] = useState("读取公开网页内容，供当前数据任务分析");
  const [attempt, setAttempt] = useState<SourceAcquisitionAttempt | null>(null);
  const [loading, setLoading] = useState(false);
  const keyRef = useRef<{ fingerprint: string; key: string } | null>(null);
  const normalized = useMemo(() => normalizedUrl(url), [url]);

  useEffect(() => {
    const raw = localStorage.getItem(storageKey);
    if (!raw) return;
    const stored = readStoredAcquisition(raw);
    if (!stored) {
      localStorage.removeItem(storageKey);
      return;
    }
    let active = true;
    if (stored.url) setUrl(stored.url);
    if (stored.purpose) setPurpose(stored.purpose);
    if (stored.idempotency_key) {
      keyRef.current = {
        fingerprint: JSON.stringify({ url: stored.url, purpose: stored.purpose }),
        key: stored.idempotency_key,
      };
    }
    setLoading(!stored.attempt_id);
    const restored = stored.attempt_id
      ? getSourceAcquisition(stored.attempt_id)
      : createSourceAcquisition({
          url: stored.url,
          purpose: stored.purpose,
          allowed_scope: "current_page",
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
    if (!attempt || attempt.status !== "acquiring" || attempt.attempt_id === "pending") {
      return;
    }
    let active = true;
    const timer = window.setInterval(() => {
      void createSourceAcquisition({
        url: attempt.normalized_url,
        purpose: attempt.purpose,
        allowed_scope: "current_page",
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
    const fingerprint = JSON.stringify({ url: normalized, purpose: purpose.trim() });
    if (keyRef.current?.fingerprint !== fingerprint) {
      keyRef.current = { fingerprint, key: nanoid() };
    }
    localStorage.setItem(storageKey, JSON.stringify({
      attempt_id: null,
      idempotency_key: keyRef.current.key,
      url: normalized,
      purpose: purpose.trim(),
    } satisfies StoredSourceAcquisition));
    setLoading(true);
    setAttempt({
      attempt_id: "pending",
      idempotency_key: keyRef.current.key,
      request_url: url,
      normalized_url: normalized,
      allowed_scope: { kind: "current_page", normalized_url: normalized },
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
        allowed_scope: "current_page",
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
    localStorage.removeItem(storageKey);
    keyRef.current = null;
    setAttempt(null);
    setUrl("");
  };

  const artifact = attempt?.snapshot?.artifacts[0];
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

          <div className="mt-4 grid gap-3 border-y py-3 text-xs sm:grid-cols-2">
            <div className="flex gap-2.5">
              <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
              <div>
                <p className="font-medium">允许范围：仅当前页面</p>
                <p className="mt-1 leading-5 text-muted-foreground">
                  不跟随页面链接；跨域跳转会直接失败。
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
      ) : attempt.status === "succeeded" && artifact ? (
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
