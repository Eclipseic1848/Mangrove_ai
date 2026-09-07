import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  // 隔离 harness 与 Vite 热更新依赖统一导入同一模块，避免测试自己复制内存会话。
  await page.route("**/src/lib/api.ts?*", (route) => route.fulfill({ contentType: "application/javascript", body: 'export * from "/src/lib/api.ts";' }));
  await page.route("**/session-recovery-harness", (route) => route.fulfill({ contentType: "text/html", body: "<!doctype html><title>隔离续期测试</title>" }));
  await page.goto("/session-recovery-harness");
});

test("刷新成功响应无法解析时锁存未知，仅成功重新登录解除", async ({ page }) => {
  const result = await page.evaluate(async () => {
    const mod = await import("/src/lib/api.ts");
    const user = { user_id: "synthetic-owner", username: "owner", display_name: "当前", role: "user" };
    window.fetch = async () => Response.json(user);
    await mod.bootstrapSession();
    let rotations = 0;
    let loggedIn = false;
    let renewed = false;
    window.fetch = async (path) => {
      if (String(path) === "/api/auth/login") { loggedIn = true; return Response.json(user); }
      if (String(path) === "/api/auth/refresh") {
        rotations += 1;
        if (!loggedIn) return new Response("{", { headers: { "Content-Type": "application/json" } });
        renewed = true;
        return Response.json(user);
      }
      return renewed ? Response.json(user) : new Response("{}", { status: 401, headers: { "X-Mangrove-Auth": "access-expired" } });
    };
    const errors: string[] = [];
    for (let attempt = 0; attempt < 2; attempt += 1) {
      await mod.authenticatedFetch("/api/business").catch((error) => errors.push(error.message));
    }
    const beforeLogin = rotations;
    await mod.sessionCommand("/api/auth/login", { username: "owner", password: "synthetic" });
    const response = await mod.authenticatedFetch("/api/business");
    return { beforeLogin, rotations, errors, status: response.status };
  });
  expect(result.beforeLogin).toBe(1);
  expect(result.errors).toHaveLength(2);
  expect(result.errors.every((message) => message.includes("续期结果未知"))).toBe(true);
  expect(result.rotations).toBe(2);
  expect(result.status).toBe(200);
});

test("Workspace流过期复核续期后只续接GET且保留事件游标", async ({ page }) => {
  const result = await page.evaluate(async () => {
    const api = await import("/src/lib/api.ts");
    const workspace = await import("/src/lib/semanticWorkspaceApi.ts");
    const user = { user_id: "synthetic-owner", username: "owner", display_name: "当前", role: "user" };
    window.fetch = async () => Response.json(user);
    await api.bootstrapSession();
    let renewed = false;
    let rotations = 0;
    let subscriptions = 0;
    let resolveDone!: () => void;
    let rejectDone!: (error: Error) => void;
    const done = new Promise<void>((resolve, reject) => { resolveDone = resolve; rejectDone = reject; });
    const requests: Array<{ path: string; method: string; cursor: string | null }> = [];
    window.fetch = async (path, init) => {
      requests.push({ path: String(path), method: init?.method || "GET", cursor: new Headers(init?.headers).get("Last-Event-ID") });
      if (String(path) === "/api/auth/refresh") { rotations += 1; renewed = true; return Response.json(user); }
      if (String(path).endsWith("/stream")) {
        subscriptions += 1;
        const body = subscriptions === 1
          ? 'id: 7\nevent: progress\ndata: {}\n\nevent: auth-expired\ndata: {}\n\n'
          : 'event: done\ndata: {"status":"completed"}\n\n';
        return new Response(body, { headers: { "Content-Type": "text/event-stream" } });
      }
      return renewed ? Response.json(user) : new Response("{}", { status: 401, headers: { "X-Mangrove-Auth": "access-expired" } });
    };
    const stop = workspace.streamWorkspaceTask("synthetic-task", { onDone: resolveDone, onError: rejectDone });
    await done;
    stop();
    return { subscriptions, rotations, requests, state: api.getSessionState() };
  });
  expect(result.subscriptions).toBe(2);
  expect(result.rotations).toBe(1);
  expect(result.requests.filter((request) => request.path.endsWith("/stream")).map((request) => [request.method, request.cursor])).toEqual([["GET", null], ["GET", "7"]]);
  expect(result.requests.filter((request) => request.method !== "GET").map((request) => request.path)).toEqual(["/api/auth/refresh"]);
  expect(result.state.user?.user_id).toBe("synthetic-owner");
});


test("旧Owner响应头已成功但正文迟到时不得交给调用者", async ({ page }) => {
  const result = await page.evaluate(async () => {
    const mod = await import("/src/lib/api.ts");
    const user = { user_id: "synthetic-owner", username: "owner", display_name: "当前", role: "user" };
    window.fetch = async () => Response.json(user);
    await mod.bootstrapSession();
    let body!: ReadableStreamDefaultController<Uint8Array>;
    let started!: () => void;
    const reading = new Promise<void>((resolve) => { started = resolve; });
    window.fetch = async (path) => String(path) === "/api/old-business"
      ? new Response(new ReadableStream<Uint8Array>({ start(controller) { body = controller; started(); } }), { headers: { "Content-Type": "application/json" } })
      : Response.json({ ...user, user_id: "synthetic-other-owner" });
    const old = mod.api.get("/api/old-business").then(() => "leaked", () => "rejected");
    await reading;
    await mod.bootstrapSession();
    body.enqueue(new TextEncoder().encode('{"private":"synthetic-old-owner"}'));
    body.close();
    return { result: await old, state: mod.getSessionState() };
  });
  expect(result.result).toBe("rejected");
  expect(result.state.user?.user_id).toBe("synthetic-other-owner");
});
