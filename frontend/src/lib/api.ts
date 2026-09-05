/**
 * 网关 API 客户端：统一带 JWT、错误处理，以及 SSE 聊天流解析。
 */
import { productText } from "@/lib/productText";

const TOKEN_KEY = "mangrove_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(t: string) {
  localStorage.setItem(TOKEN_KEY, t);
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

function authHeaders(extra: Record<string, string> = {}): Record<string, string> {
  const t = getToken();
  return { "Content-Type": "application/json", ...(t ? { Authorization: `Bearer ${t}` } : {}), ...extra };
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(productText(message));
    this.status = status;
  }
}

async function handle(res: Response) {
  if (!res.ok) {
    let detail = `${res.status}`;
    if (res.headers.get("content-type")?.includes("application/json")) {
      try {
        const j = await res.json();
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
    return await res.json();
  } catch {
    throw new ApiError(502, "服务返回的数据格式无效，请稍后重新加载。");
  }
}

export const api = {
  get: (path: string, init?: RequestInit) =>
    fetch(path, { ...init, headers: authHeaders() }).then(handle),
  post: (
    path: string,
    body?: unknown,
    headers: Record<string, string> = {},
  ) =>
    fetch(path, {
      method: "POST",
      headers: authHeaders(headers),
      body: body ? JSON.stringify(body) : undefined,
    }).then(handle),
  patch: (path: string, body?: unknown) =>
    fetch(path, { method: "PATCH", headers: authHeaders(), body: body ? JSON.stringify(body) : undefined }).then(handle),
  put: (path: string, body?: unknown) =>
    fetch(path, { method: "PUT", headers: authHeaders(), body: body ? JSON.stringify(body) : undefined }).then(handle),
  del: (path: string) => fetch(path, { method: "DELETE", headers: authHeaders() }).then(handle),
};

/** 下载产出文件（带鉴权），触发浏览器保存。 */
export async function downloadFile(url: string, filename: string) {
  const res = await fetch(url, { headers: authHeaders() });
  if (!res.ok) throw new ApiError(res.status, "下载失败");
  const blob = await res.blob();
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
 * 发起聊天并解析 SSE 流（POST + fetch 流式读取，可带 Authorization）。
 * 返回一个可调用的取消函数。
 */
export function streamChat(
  body: { conv_id?: string | null; content: string; provider?: string; model?: string; mode?: string },
  events: ChatEvents,
): () => void {
  const controller = new AbortController();
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
    const dispatch = (event: string, data: string) => {
      if (finished) return;
      let parsed: any = {};
      try {
        parsed = data ? JSON.parse(data) : {};
      } catch {
        parsed = { raw: data };
      }
      if (event === "meta") events.onMeta?.(parsed);
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
      const res = await fetch("/api/chat/stream", {
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
          if (dataLines.length || event !== "message") dispatch(event, dataLines.join("\n"));
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
