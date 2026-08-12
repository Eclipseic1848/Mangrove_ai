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

  (async () => {
    const res = await fetch("/api/chat/stream", {
      method: "POST",
      headers: authHeaders({ Accept: "text/event-stream" }),
      body: JSON.stringify(body),
      signal: controller.signal,
    });
    if (!res.ok || !res.body) {
      events.onError?.({ message: `请求失败（${res.status}）` });
      events.onDone?.();
      return;
    }
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    const dispatch = (event: string, data: string) => {
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
        events.onError?.({
          ...parsed,
          ...(typeof parsed?.message === "string"
            ? { message: productText(parsed.message) }
            : {}),
        });
      }
      else if (event === "done") events.onDone?.();
    };

    try {
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        // sse_starlette 用 \r\n 作行分隔（块以 \r\n\r\n 结尾），归一化为 \n 再按空行切块
        buffer += decoder.decode(value, { stream: true });
        const normalized = buffer.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
        const blocks = normalized.split("\n\n");
        buffer = blocks.pop() ?? "";
        for (const block of blocks) {
          let event = "message";
          const dataLines: string[] = [];
          for (const line of block.split("\n")) {
            if (line.startsWith("event:")) event = line.slice(6).trim();
            else if (line.startsWith("data:")) dataLines.push(line.slice(5).replace(/^ /, ""));
          }
          if (dataLines.length || event !== "message") dispatch(event, dataLines.join("\n"));
        }
      }
    } catch (e: any) {
      if (e?.name !== "AbortError") {
        events.onError?.({ message: productText(String(e?.message || e)) });
      }
    }
    events.onDone?.();
  })();

  return () => controller.abort();
}
