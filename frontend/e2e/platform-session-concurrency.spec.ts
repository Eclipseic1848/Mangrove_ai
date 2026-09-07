import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => {
  await page.route("**/session-concurrency-harness", (route) => route.fulfill({ contentType: "text/html", body: "<!doctype html><title>隔离会话测试</title>" }));
  await page.goto("/session-concurrency-harness");
});

test("较新未登录结果不能被旧 bootstrap 成功覆盖", async ({ page }) => {
  const state = await page.evaluate(async () => {
    const mod = await import("/src/lib/api.ts");
    const pending: Array<(response: Response) => void> = [];
    window.fetch = async () => new Promise<Response>((resolve) => pending.push(resolve));
    const older = mod.bootstrapSession();
    const newer = mod.bootstrapSession();
    pending[1](new Response("{}", { status: 401, headers: { "X-Mangrove-Auth": "session-invalid" } }));
    await newer;
    pending[0](Response.json({ user_id: "synthetic-owner", username: "owner", display_name: "旧成功", role: "user" }));
    await older;
    return mod.getSessionState();
  });
  expect(state.user).toBeNull();
});

test("同 Owner 新身份读取不能被旧业务401清除", async ({ page }) => {
  const state = await page.evaluate(async () => {
    const mod = await import("/src/lib/api.ts");
    const user = { user_id: "synthetic-owner", username: "owner", display_name: "当前", role: "user" };
    let rejectOld!: (response: Response) => void;
    window.fetch = async (path) => String(path) === "/api/old-business"
      ? new Promise<Response>((resolve) => { rejectOld = resolve; }) : Response.json(user);
    await mod.bootstrapSession();
    const oldRequest = mod.authenticatedFetch("/api/old-business").catch(() => null);
    await mod.bootstrapSession();
    rejectOld(new Response("{}", { status: 401, headers: { "X-Mangrove-Auth": "session-invalid" } }));
    await oldRequest;
    return mod.getSessionState();
  });
  expect(state.user?.user_id).toBe("synthetic-owner");
});

test("刷新网络结果未知后不再次消费旧刷新凭证", async ({ page }) => {
  const result = await page.evaluate(async () => {
    const mod = await import("/src/lib/api.ts");
    const user = { user_id: "synthetic-owner", username: "owner", display_name: "当前", role: "user" };
    window.fetch = async () => Response.json(user);
    await mod.bootstrapSession();
    let rotations = 0;
    window.fetch = async (path) => {
      if (String(path) === "/api/auth/refresh") { rotations += 1; throw new TypeError("synthetic lost response"); }
      return new Response("{}", { status: 401, headers: { "X-Mangrove-Auth": "access-expired" } });
    };
    await mod.authenticatedFetch("/api/business").catch(() => null);
    await mod.authenticatedFetch("/api/business").catch(() => null);
    return { rotations, state: mod.getSessionState() };
  });
  expect(result.rotations).toBe(1);
});

test("旧聊天流过期不能清除已刷新会话且不重发聊天", async ({ page }) => {
  const result = await page.evaluate(async () => {
    const mod = await import("/src/lib/api.ts");
    const user = { user_id: "synthetic-owner", username: "owner", display_name: "当前", role: "user" };
    window.fetch = async () => Response.json(user);
    await mod.bootstrapSession();
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    let resolveStarted!: () => void;
    const started = new Promise<void>((resolve) => { resolveStarted = resolve; });
    let resolveDone!: () => void;
    const done = new Promise<void>((resolve) => { resolveDone = resolve; });
    let chatPosts = 0;
    let refreshed = false;
    window.fetch = async (path) => {
      if (String(path) === "/api/chat/stream") {
        chatPosts += 1;
        return new Response(new ReadableStream<Uint8Array>({ start(value) { controller = value; resolveStarted(); } }), { headers: { "Content-Type": "text/event-stream" } });
      }
      if (String(path) === "/api/auth/refresh") { refreshed = true; return Response.json(user); }
      return refreshed ? Response.json(user) : new Response("{}", { status: 401, headers: { "X-Mangrove-Auth": "access-expired" } });
    };
    mod.streamChat({ content: "虚构内容" }, { onDone: resolveDone });
    await started;
    await mod.authenticatedFetch("/api/business");
    controller.enqueue(new TextEncoder().encode("event: auth-expired\ndata: {}\n\n"));
    await done;
    return { state: mod.getSessionState(), chatPosts };
  });
  expect(result.chatPosts).toBe(1);
  expect(result.state.user?.user_id).toBe("synthetic-owner");
});

test("两标签同时401只执行一次刷新", async ({ page, context }) => {
  const second = await context.newPage();
  await second.route("**/session-concurrency-harness", (route) => route.fulfill({ contentType: "text/html", body: "<!doctype html><title>第二标签</title>" }));
  await second.goto("/session-concurrency-harness");
  let expired = false;
  let rotations = 0;
  let initialRequests = 0;
  let bothStarted!: () => void;
  const started = new Promise<void>((resolve) => { bothStarted = resolve; });
  const user = { user_id: "synthetic-owner", username: "owner", display_name: "当前", role: "user" };
  await context.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/refresh") { rotations += 1; expired = false; }
    else if (path === "/api/business" && expired) {
      initialRequests += 1;
      if (initialRequests === 2) bothStarted();
      await started;
      await route.fulfill({ status: 401, headers: { "X-Mangrove-Auth": "access-expired" }, json: {} });
      return;
    }
    await route.fulfill(expired ? { status: 401, headers: { "X-Mangrove-Auth": "access-expired" }, json: {} } : { json: user });
  });
  for (const tab of [page, second]) {
    await tab.evaluate(async () => { const mod = await import("/src/lib/api.ts"); await mod.bootstrapSession(); });
  }
  expired = true;
  const responses = await Promise.all([page, second].map((tab) => tab.evaluate(async () => {
    const mod = await import("/src/lib/api.ts");
    return (await mod.authenticatedFetch("/api/business")).status;
  })));
  expect(responses).toEqual([200, 200]);
  expect(rotations).toBe(1);
  await second.close();
});

test("锁内发现Owner变化不重发旧POST", async ({ page }) => {
  const result = await page.evaluate(async () => {
    const mod = await import("/src/lib/api.ts");
    const user = { user_id: "synthetic-owner", username: "owner", display_name: "当前", role: "user" };
    window.fetch = async () => Response.json(user);
    await mod.bootstrapSession();
    let posts = 0;
    window.fetch = async (path) => {
      if (String(path) === "/api/business") {
        posts += 1;
        return new Response("{}", { status: 401, headers: { "X-Mangrove-Auth": "access-expired" } });
      }
      return Response.json({ ...user, user_id: "synthetic-other-owner" });
    };
    await mod.authenticatedFetch("/api/business", { method: "POST", body: "{}" }).catch(() => null);
    return { posts, state: mod.getSessionState() };
  });
  expect(result.posts).toBe(1);
  expect(result.state.user?.user_id).toBe("synthetic-other-owner");
});

test("锁内旧身份401不能清除较新bootstrap确认", async ({ page }) => {
  const state = await page.evaluate(async () => {
    const mod = await import("/src/lib/api.ts");
    const user = { user_id: "synthetic-owner", username: "owner", display_name: "当前", role: "user" };
    window.fetch = async () => Response.json(user);
    await mod.bootstrapSession();
    let releaseOld!: (response: Response) => void;
    let lockReadStarted!: () => void;
    const started = new Promise<void>((resolve) => { lockReadStarted = resolve; });
    let reads = 0;
    window.fetch = async (path) => {
      if (String(path) === "/api/business") return new Response("{}", { status: 401, headers: { "X-Mangrove-Auth": "access-expired" } });
      if (++reads === 1) return new Promise<Response>((resolve) => { releaseOld = resolve; lockReadStarted(); });
      return Response.json(user);
    };
    const old = mod.authenticatedFetch("/api/business").catch(() => null);
    await started;
    await mod.bootstrapSession();
    releaseOld(new Response("{}", { status: 401, headers: { "X-Mangrove-Auth": "session-invalid" } }));
    await old;
    return mod.getSessionState();
  });
  expect(state.user?.user_id).toBe("synthetic-owner");
});

test("未知刷新状态可由显式登录恢复", async ({ page }) => {
  const result = await page.evaluate(async () => {
    const mod = await import("/src/lib/api.ts");
    const user = { user_id: "synthetic-owner", username: "owner", display_name: "当前", role: "user" };
    localStorage.setItem("mangrove_refresh_pending", "1");
    window.fetch = async () => Response.json(user);
    await mod.sessionCommand("/api/auth/login", { username: "owner", password: "synthetic-password" });
    const response = await mod.api.get("/api/business");
    return { response, pending: localStorage.getItem("mangrove_refresh_pending") };
  });
  expect(result.pending).toBeNull();
  expect(result.response.user_id).toBe("synthetic-owner");
});
