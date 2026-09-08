/**
 * 网关 API 客户端：统一使用 Cookie 会话、错误处理，以及 SSE 聊天流解析。
 */
import { productText } from "@/lib/productText";

export interface SessionUser {
  user_id: string;
  username: string;
  display_name: string;
  role: string;
}
export const SESSION_EXPIRED = "登录已失效，请重新登录";
let sessionUser: SessionUser | null = null;
let authGeneration = 0;
let authConfirmation = 0;
let identityRead = 0;
const REFRESH_PENDING_KEY = "mangrove_refresh_pending";
const REFRESH_UNKNOWN = "续期结果未知，请重新登录；不要重复提交当前操作";
let sessionMessage: string | null = null;
const listeners = new Set<() => void>();
const responseGenerations = new WeakMap<Response, number>();
let refreshFlight: Promise<void> | null = null;
export const getAuthGeneration = () => authGeneration;
export const getSessionState = () => ({ user: sessionUser, message: sessionMessage });
export function subscribeSession(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
function publishSession(user: SessionUser | null, message: string | null = null) {
  if (sessionUser?.user_id !== user?.user_id) authGeneration += 1;
  authConfirmation += 1;
  sessionUser = user;
  sessionMessage = message;
  listeners.forEach((listener) => listener());
}
export function notifyAuthExpired(generation = authGeneration, confirmation = authConfirmation) {
  if (generation !== authGeneration || confirmation !== authConfirmation) return;
  // null 到 null 也是较新的失效事实，必须阻断旧 bootstrap 成功响应。
  authGeneration += 1;
  publishSession(null, SESSION_EXPIRED);
}
function assertCurrent(generation: number) {
  if (generation !== authGeneration) throw new ApiError(401, "登录身份已变化，请重新打开当前任务");
}
function cookieFetch(path: RequestInfo | URL, init: RequestInit = {}, owner?: string, transport: typeof fetch = fetch): Promise<Response> {
  const headers = new Headers(init.headers);
  headers.delete("Authorization");
  if (owner) headers.set("X-Mangrove-Owner", owner);
  else headers.delete("X-Mangrove-Owner");
  if (!["GET", "HEAD", "OPTIONS"].includes((init.method || "GET").toUpperCase())) headers.set("X-Mangrove-CSRF", "1");
  return transport(path, { ...init, headers, credentials: "same-origin" });
}
export async function withSessionLock<T>(operation: () => Promise<T>): Promise<T> {
  if (!navigator.locks) throw new ApiError(401, "浏览器无法安全续期，请使用支持 Web Locks 的浏览器并通过 HTTPS 或 localhost 重新登录");
  return navigator.locks.request("mangrove-platform-session", operation);
}
const channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel("mangrove-platform-session") : null;
function broadcastSession() { channel?.postMessage("identity-changed"); }
async function refreshUnderLock(generation: number, owner: string | undefined) {
  assertCurrent(generation);
  // 锁内读取也必须冻结确认版本，旧成功、旧拒绝都不能覆盖较新的身份事实。
  const confirmation = authConfirmation;
  const read = ++identityRead;
  const assertReadCurrent = () => {
    assertCurrent(generation);
    if (confirmation !== authConfirmation || read !== identityRead) {
      throw new ApiError(401, "登录状态已更新，请重新执行当前操作");
    }
  };
  let response = await cookieFetch("/api/auth/me", {}, undefined);
  assertReadCurrent();
  let rotated = false;
  if (response.status === 401 && response.headers.get("X-Mangrove-Auth") === "access-expired") {
    // 只存非秘密待确认标记。崩溃、断网或坏响应后，其他标签也不能再次消费。
    if (localStorage.getItem(REFRESH_PENDING_KEY)) throw new ApiError(503, REFRESH_UNKNOWN);
    localStorage.setItem(REFRESH_PENDING_KEY, "1");
    try {
      response = await cookieFetch("/api/auth/refresh", { method: "POST" }, owner);
    } catch {
      throw new ApiError(503, REFRESH_UNKNOWN);
    }
    rotated = true;
    if (response.status >= 400 && response.status < 500) localStorage.removeItem(REFRESH_PENDING_KEY);
  }
  if (response.status === 401) {
    assertReadCurrent();
    notifyAuthExpired(generation, confirmation);
  }
  let user: SessionUser;
  try {
    user = await handle(response);
    if (!user || typeof user.user_id !== "string" || !user.user_id) throw new Error("会话响应缺少身份");
  } catch (error) {
    if (rotated && localStorage.getItem(REFRESH_PENDING_KEY)) throw new ApiError(503, REFRESH_UNKNOWN);
    throw error;
  }
  if (rotated) localStorage.removeItem(REFRESH_PENDING_KEY);
  assertReadCurrent();
  publishSession(user);
  broadcastSession();
  // 即使 Cookie 已换账号，旧业务请求也不能借用新身份执行。
  if (owner && owner !== user.user_id) throw new ApiError(401, "登录身份已变化，请重新打开当前任务");
}
async function refreshSession(generation: number, owner: string | undefined) {
  if (!refreshFlight) {
    refreshFlight = withSessionLock(() => refreshUnderLock(generation, owner))
      .finally(() => { refreshFlight = null; });
  }
  await refreshFlight;
  if (owner) assertCurrent(generation);
}
export async function authenticatedFetch(path: RequestInfo | URL, init: RequestInit = {}, transport: typeof fetch = fetch): Promise<Response> {
  const generation = authGeneration;
  const owner = sessionUser?.user_id;
  let confirmation = authConfirmation;
  let response = await cookieFetch(path, init, owner, transport);
  assertCurrent(generation);
  if (response.status === 401 && response.headers.get("X-Mangrove-Auth") === "access-expired") {
    await response.body?.cancel();
    if (confirmation === authConfirmation) await refreshSession(generation, owner);
    assertCurrent(generation);
    confirmation = authConfirmation;
    response = await cookieFetch(path, init, owner, transport);
    assertCurrent(generation);
  }
  if (response.status === 401) notifyAuthExpired(generation, confirmation);
  responseGenerations.set(response, generation);
  return response;
}
/** 流中的过期只描述建立连接时的 access，先复核 Cookie，绝不重发聊天 POST。 */
export async function revalidateStreamSession(generation: number): Promise<boolean> {
  if (generation !== authGeneration) return false;
  await refreshSession(generation, sessionUser?.user_id);
  return generation === authGeneration && sessionUser !== null;
}
export async function bootstrapSession() {
  // 旧版本凭证只删除，不读取、不迁移为 Cookie 会话。
  localStorage.removeItem("mangrove_token");
  const generation = authGeneration;
  const confirmation = authConfirmation;
  const read = ++identityRead;
  let response = await cookieFetch("/api/auth/me", {}, undefined);
  if (generation !== authGeneration || confirmation !== authConfirmation || read !== identityRead) return;
  if (response.status === 401 && response.headers.get("X-Mangrove-Auth") === "access-expired") {
    await response.body?.cancel();
    await refreshSession(generation, undefined);
    return;
  }
  if (response.status === 401) { notifyAuthExpired(generation, confirmation); return; }
  const user = await handle(response);
  if (generation === authGeneration && confirmation === authConfirmation && read === identityRead) publishSession(user);
}
channel?.addEventListener("message", (event) => {
  if (event.data !== "identity-changed") return;
  // 通知只触发身份重读；先等本页续期收口，避免通知反过来使锁内读取失效。
  // 续期失败也要重读，其他标签可能已切换 Owner，不能吞掉身份变更通知。
  void (refreshFlight ?? Promise.resolve()).catch(() => {}).then(bootstrapSession).catch(() => {});
});
export async function sessionCommand(path: string, body?: unknown) {
  const generation = ++authGeneration;
  const owner = sessionUser?.user_id;
  return withSessionLock(async () => {
    assertCurrent(generation);
    const init: RequestInit = {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    };
    const publicCommand = path === "/api/auth/login" || path === "/api/auth/register";
    let response = await cookieFetch(path, init, publicCommand ? undefined : owner);
    if (!publicCommand && response.status === 401 && response.headers.get("X-Mangrove-Auth") === "access-expired") {
      // 已持有同一个锁，直接调用锁内续期，不能再次申请锁。
      await refreshUnderLock(generation, owner);
      assertCurrent(generation);
      response = await cookieFetch(path, init, owner);
    }
    const result = await handle(response);
    assertCurrent(generation);
    if (path === "/api/auth/login") {
      localStorage.removeItem(REFRESH_PENDING_KEY);
      publishSession(result);
    }
    else if (path !== "/api/auth/register") publishSession(null);
    broadcastSession();
    return result;
  });
}
function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return { "Content-Type": "application/json", ...extra };
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(productText(message));
    this.status = status;
  }
}

function assertResponseCurrent(response: Response) {
  const generation = responseGenerations.get(response);
  if (generation !== undefined) assertCurrent(generation);
}
export async function readAuthenticatedJson(response: Response) {
  const value = await response.json();
  assertResponseCurrent(response);
  return value;
}
export async function readAuthenticatedBlob(response: Response) {
  const value = await response.blob();
  assertResponseCurrent(response);
  return value;
}

async function handle(res: Response) {
  if (!res.ok) {
    let detail = `${res.status}`;
    if (res.headers.get("content-type")?.includes("application/json")) {
      try {
        const j = await readAuthenticatedJson(res);
        detail = j.detail || JSON.stringify(j);
      } catch {
        /* 保留状态码，避免用 JSON 解析异常覆盖真正的 HTTP 错误。 */
      }
    }
    throw new ApiError(res.status, detail);
  }
  if (!res.headers.get("content-type")?.includes("application/json")) {
    throw new ApiError(
      502,
      "服务返回了网页而不是 API 数据，请确认后端已更新并重新加载。",
    );
  }
  try {
    return await readAuthenticatedJson(res);
  } catch (error) {
    if (error instanceof ApiError) throw error;
    throw new ApiError(502, "服务返回的数据格式无效，请稍后重新加载。");
  }
}

export const api = {
  get: (path: string, init?: RequestInit) =>
    authenticatedFetch(path, { ...init, headers: authHeaders() }).then(handle),
  post: (
    path: string,
    body?: unknown,
    headers: Record<string, string> = {},
  ) =>
    authenticatedFetch(path, {
      method: "POST",
      headers: authHeaders(headers),
      body: body ? JSON.stringify(body) : undefined,
    }).then(handle),
  patch: (path: string, body?: unknown) =>
    authenticatedFetch(path, { method: "PATCH", headers: authHeaders(), body: body ? JSON.stringify(body) : undefined }).then(handle),
  put: (path: string, body?: unknown) =>
    authenticatedFetch(path, { method: "PUT", headers: authHeaders(), body: body ? JSON.stringify(body) : undefined }).then(handle),
  del: (path: string) => authenticatedFetch(path, { method: "DELETE", headers: authHeaders() }).then(handle),
};

/** 下载产出文件（带鉴权），触发浏览器保存。 */
export async function downloadFile(url: string, filename: string, signal?: AbortSignal, expectedMediaType?: string) {
  signal?.throwIfAborted();
  const res = await authenticatedFetch(url, { headers: authHeaders(), signal });
  if (!res.ok) throw new ApiError(res.status, "下载失败");
  if (expectedMediaType && res.headers.get("content-type")?.split(";")[0].trim() !== expectedMediaType) {
    // 拒绝的正文不再读取，先释放传输；清理失败也不能覆盖产品错误。
    await res.body?.cancel().catch(() => {});
    throw new ApiError(502, "下载内容格式无效，请稍后重试");
  }
  const blob = await readAuthenticatedBlob(res);
  // 关闭画布或切换版本后，迟到的下载不能在新任务中触发保存。
  signal?.throwIfAborted();
  const a = document.createElement("a");
  const objectUrl = URL.createObjectURL(blob);
  a.href = objectUrl;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // 浏览器需要在异步下载真正接管 Blob 后才能撤销地址，否则会误报“没有权限”。
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
}

// ---------- SSE 聊天流 ----------
export interface ChatEvents {
  onMeta?: (d: { conv_id: string }) => void;
  onNode?: (d: { node: string; label: string; view?: any }) => void;
  onResult?: (d: any) => void;
  onError?: (d: { message: string }) => void;
  onDone?: () => void;
}

/**
 * 发起聊天并解析 SSE 流（POST + fetch 流式读取，统一 Cookie 鉴权）。
 * 返回一个可调用的取消函数。
 */
export function streamChat(
  body: { conv_id?: string | null; content: string; provider?: string; model?: string; mode?: string },
  events: ChatEvents,
): () => void {
  const controller = new AbortController();
  const generation = getAuthGeneration();
  let finished = false;
  const finish = (error?: { message: string }) => {
    if (finished) return;
    // 先冻结终态，避免重复 done、取消或迟到事件再次修改调用者状态。
    finished = true;
    try {
      if (error) events.onError?.(error);
    } finally {
      events.onDone?.();
    }
  };

  (async () => {
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined;
    const dispatch = async (event: string, data: string) => {
      if (finished) return;
      if (generation !== getAuthGeneration()) { controller.abort(); finish(); return; }
      let parsed: any = {};
      try {
        parsed = data ? JSON.parse(data) : {};
      } catch {
        parsed = { raw: data };
      }
      if (event === "auth-expired") {
        controller.abort();
        try {
          const current = await revalidateStreamSession(generation);
          finish({ message: current ? "会话有效，本次聊天连接已结束，请查看结果后再继续。" : SESSION_EXPIRED });
        } catch (error) {
          finish({ message: error instanceof Error ? error.message : SESSION_EXPIRED });
        }
      }
      else if (event === "meta") events.onMeta?.(parsed);
      else if (event === "node") events.onNode?.(parsed);
      else if (event === "result") events.onResult?.(parsed);
      else if (event === "error") {
        finish({
          ...parsed,
          message: productText(parsed?.message || "聊天请求失败"),
        });
      }
      else if (event === "done") finish();
    };

    try {
      const res = await authenticatedFetch("/api/chat/stream", {
        method: "POST",
        headers: authHeaders({ Accept: "text/event-stream" }),
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (finished) {
        await res.body?.cancel();
        return;
      }
      if (!res.ok || !res.body) {
        finish({ message: `请求失败（${res.status}）` });
        await res.body?.cancel();
        return;
      }
      reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (!finished) {
        const { done, value } = await reader.read();
        if (finished) break;
        buffer += decoder.decode(value, { stream: !done });
        // 块尾的 CR 可能属于下一块的 CRLF，先保留，避免将一行误切为事件边界。
        const pendingCR = !done && buffer.endsWith("\r");
        const normalized = (pendingCR ? buffer.slice(0, -1) : buffer).replace(/\r\n/g, "\n").replace(/\r/g, "\n");
        const blocks = normalized.split("\n\n");
        buffer = (blocks.pop() ?? "") + (pendingCR ? "\r" : "");
        for (const block of blocks) {
          let event = "message";
          const dataLines: string[] = [];
          for (const line of block.split("\n")) {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
          }
          if (dataLines.length || event !== "message") await dispatch(event, dataLines.join("\n"));
        }
        if (done) break;
      }
    } catch (e: any) {
      if (!controller.signal.aborted) {
        finish({ message: productText(String(e?.message || e)) });
      }
    } finally {
      // 即使尚未取得 reader 就失败，也必须终止该请求的传输。
      controller.abort();
      finish();
      if (reader) {
        try { await reader.cancel(); } catch { /* 已断开的流仍需释放读取锁。 */ }
        reader.releaseLock();
      }
    }
  })();

  return () => { controller.abort(); finish(); };
}
