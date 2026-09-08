import AxeBuilder from "@axe-core/playwright";
import { expect, test, type Page } from "@playwright/test";

const answer = {
  message_id: "answer-1", version: 1, task_id: "session-task", revision: 1,
  run_id: "run-1", turn_id: "turn-1", role: "assistant", kind: "answer",
  content: "中文🌿完整回答\n\n**尚未闭合的 Markdown", status: "completed", created_at: "2026-09-08T00:00:01Z",
};

test("工作台真实字节分块、重连重放及迟到修订不重复回答或执行", async ({ page }) => {
  await page.route("**/workspace-stream-harness", route => route.fulfill({ contentType: "text/html", body: "<!doctype html><title>隔离工作台协议</title>" }));
  await page.goto("/workspace-stream-harness");
  const result = await page.evaluate(async (message) => {
    const auth = await import("/src/lib/api.ts");
    const workspace = await import("/src/lib/semanticWorkspaceApi.ts");
    window.fetch = async () => Response.json({ user_id: "synthetic-owner", username: "测试", role: "user" });
    await auth.bootstrapSession();
    const seen: string[] = [];
    const requests: string[] = [];
    let subscriptions = 0;
    let finish!: () => void;
    const done = new Promise<void>(resolve => { finish = resolve; });
    const event = (type: string, payload: unknown) => `event: ${type}\r\ndata: ${JSON.stringify(payload)}\r\n\r\n`;
    window.fetch = async (path, init) => {
      requests.push(`${init?.method || "GET"} ${String(path)}`);
      subscriptions++;
      const body = event("message", message) + (subscriptions === 1 ? "" :
        event("message", { ...message, message_id: "late-revision", revision: 2, content: "错版正文" })
        + event("done", { task_id: message.task_id, revision: 1, run_id: "old-run", status: "completed" })
        + event("message", { ...message, message_id: "answer-2", content: "第二条完整回答" })
        + event("done", { task_id: message.task_id, revision: 1, run_id: "run-1", status: "completed" })
        + event("message", { ...message, message_id: "after-done", content: "收口后正文" }));
      return new Response(new ReadableStream<Uint8Array>({ start(controller) {
        // 每字节拆分，确实切断中文、emoji 和 CRLF，而不是模拟打字。
        for (const byte of new TextEncoder().encode(body)) controller.enqueue(new Uint8Array([byte]));
        controller.close();
      } }), { headers: { "Content-Type": "text/event-stream" } });
    };
    const stop = workspace.streamWorkspaceTask(message.task_id, {
      onMessage: item => seen.push(item.content), onDone: finish,
    }, 1, "run-1");
    await done;
    stop();
    return { seen, requests, subscriptions };
  }, answer);
  expect(result.seen).toEqual([answer.content, "第二条完整回答"]);
  expect(result.subscriptions).toBe(2);
  expect(result.requests).toEqual(Array(2).fill("GET /api/semantic-workspace/tasks/session-task/stream?revision=1"));
});

test("切换 Owner 后旧工作台流即使仍返回也不得发布正文", async ({ page }) => {
  await page.route("**/workspace-stream-harness", route => route.fulfill({ contentType: "text/html", body: "<!doctype html><title>Owner 隔离</title>" }));
  await page.goto("/workspace-stream-harness");
  const seen = await page.evaluate(async (message) => {
    const auth = await import("/src/lib/api.ts");
    const workspace = await import("/src/lib/semanticWorkspaceApi.ts");
    let owner = "owner-a";
    let body!: ReadableStreamDefaultController<Uint8Array>;
    let ready!: () => void;
    const connected = new Promise<void>(resolve => { ready = resolve; });
    window.fetch = async (path) => String(path).includes("/stream")
      ? new Response(new ReadableStream<Uint8Array>({ start(controller) { body = controller; ready(); } }), { headers: { "Content-Type": "text/event-stream" } })
      : Response.json({ user_id: owner, username: owner, role: "user" });
    await auth.bootstrapSession();
    const results: string[] = [];
    const stop = workspace.streamWorkspaceTask(message.task_id, { onMessage: item => results.push(item.content) }, 1, "run-1");
    await connected;
    owner = "owner-b";
    await auth.bootstrapSession();
    body.enqueue(new TextEncoder().encode(`event: message\ndata: ${JSON.stringify(message)}\n\n`));
    body.close();
    // 让流读取微任务完成；不使用时间等待掩盖竞态。
    await new Promise(resolve => requestAnimationFrame(resolve));
    stop();
    return results;
  }, answer);
  expect(seen).toEqual([]);
});

const baseTask = {
  task_id: "session-task", title: "会话合成任务", objective_text: "核对已授权资料", upload_ids: [],
  output_formats: ["xlsx"], provider: "local", model: "frozen-model", model_connection_id: "connection-1",
  status: "running", active_revision: 1, current_revision: 1, viewing_revision: 1,
  external_api_confirmed: true, question: null, error: null, summary: "处理中",
  cancel_requested: false, deleted_at: null, created_at: "2026-09-08T00:00:00Z", updated_at: "2026-09-08T00:00:01Z",
  revisions: [{ revision: 1 }], events: [], uploads: [], attempts: [], harness_events: [],
  delivery: null, plan: null, run: { run_id: "run-1" }, messages: [] as typeof answer[],
};
const session = {
  task_id: "session-task", revision: 1, run_id: "run-1", status: "running",
  started_at: "2026-09-08T00:00:00Z", ended_at: null, work_duration_ms: 4000, waiting_duration_ms: 1000,
  action_count: 2, tool_call_count: 1, handled_retry_count: 1,
  usage: { input_tokens: null, output_tokens: 20, cache_tokens: null, total_tokens: 42, call_count: 2, unknown_call_count: 1 },
  provider_usage: [{ owner_user_id: "owner-a", task_id: "session-task", revision: 1, run_id: "run-1", connection_id: "connection-1", model: "frozen-model", purpose: "context_rewrite", status: "completed", input_tokens: null, output_tokens: 20, cache_tokens: null, total_tokens: 42, request_count: 1, created_at: "2026-09-08T00:00:01Z" }],
  entries: [],
};

async function mockPage(page: Page, readTask: () => unknown) {
  await page.route("**/api/**", route => route.fulfill({ json: {} }));
  await page.route("**/api/auth/me", route => route.fulfill({ json: { user_id: "owner-a", username: "测试", role: "user" } }));
  await page.route("**/api/models", route => route.fulfill({ json: { options: [], default: null } }));
  await page.route("**/api/model-connections", route => route.fulfill({ json: { items: [{ connection_id: "connection-1", display_name: "已冻结连接", status: "verified", models: [] }] } }));
  await page.route("**/api/semantic-workspace/tasks?*", route => route.fulfill({ json: [baseTask] }));
  await page.route("**/api/semantic-workspace/tasks/session-task", route => route.fulfill({ json: readTask() }));
  await page.route("**/api/semantic-workspace/tasks/session-task/turns", route => route.fulfill({ json: { turns: [], results: [], proposals: [] } }));
  await page.route("**/api/semantic-workspace/tasks/session-task/stream*", route => route.fulfill({ status: 403, json: {} }));
  await page.route("**/api/semantic-workspace/storage", route => route.fulfill({ json: { total_bytes: 0, task_count: 1, recycle_bin_count: 0 } }));
  await page.route("**/api/semantic-workspace/guidance", route => route.fulfill({ json: { onboarding: [], examples: [] } }));
  await page.goto("/data-prep?task=session-task");
  await expect(page.getByRole("heading", { name: "会话合成任务" })).toBeVisible();
}

test("页面直接消费完整SSE回答，GET恢复不双显，切任务拒绝旧流", async ({ page }) => {
  await page.addInitScript(() => {
    const original = window.fetch;
    window.fetch = async (input, init) => {
      if (!String(input).includes("/session-task/stream")) return original(input, init);
      return new Response(new ReadableStream<Uint8Array>({ start(controller) {
        Object.assign(window, { emitWorkspaceMessage: (message: unknown) => controller.enqueue(new TextEncoder().encode(`event: message\ndata: ${JSON.stringify(message)}\n\n`)) });
      } }), { headers: { "Content-Type": "text/event-stream" } });
    };
  });
  let task = { ...baseTask, run: null, run_id: "run-1" };
  let reads = 0;
  await mockPage(page, () => { reads++; return task; });
  const emit = (message: typeof answer) => page.evaluate(payload => {
    (window as unknown as { emitWorkspaceMessage: (item: unknown) => void }).emitWorkspaceMessage(payload);
  }, message);
  await expect.poll(() => page.evaluate(() => typeof (window as unknown as { emitWorkspaceMessage?: unknown }).emitWorkspaceMessage)).toBe("function");
  await page.getByRole("textbox", { name: "继续对话" }).fill("保留输入焦点");
  await emit(answer);
  await emit(answer);
  await expect(page.getByLabel("Mangrove 回答")).toHaveCount(1);
  await expect(page.getByRole("textbox", { name: "继续对话" })).toBeFocused();
  await expect(page.getByRole("status", { name: "对话更新" })).toHaveText("已收到 1 条完整回答");
  task = { ...task, messages: [answer] };
  const beforeRestore = reads;
  await expect.poll(() => reads).toBeGreaterThan(beforeRestore);
  await expect(page.getByLabel("Mangrove 回答")).toHaveCount(1);
  await page.route("**/api/semantic-workspace/tasks/other-task", route => route.fulfill({ json: { ...baseTask, task_id: "other-task", title: "另一条任务", status: "completed" } }));
  await page.route("**/api/semantic-workspace/tasks/other-task/turns", route => route.fulfill({ json: { turns: [], results: [], proposals: [] } }));
  await page.evaluate(() => {
    history.pushState(null, "", "/data-prep?task=other-task");
    dispatchEvent(new PopStateEvent("popstate"));
  });
  await expect(page.getByRole("heading", { name: "另一条任务" })).toBeVisible();
  await emit({ ...answer, message_id: "late-answer", content: "不能泄入另一任务" });
  await expect(page.getByText("不能泄入另一任务")).toHaveCount(0);
  await expect(page.getByLabel("Mangrove 回答")).toHaveCount(0);
});

test("正文安全渲染、刷新恢复和用户上翻后回到最新", async ({ page }) => {
  const long = { ...answer, content: Array.from({ length: 40 }, (_, index) => `第 ${index} 段真实完整回答`).join("\n\n") + "\n\n![图片](https://synthetic.invalid/tracker.png)\n\n<script>synthetic-secret</script>\n\n**未闭合" };
  let task = { ...baseTask, messages: [long] };
  const remoteRequests: string[] = [];
  page.on("request", request => { if (request.url().includes("synthetic.invalid")) remoteRequests.push(request.url()); });
  await mockPage(page, () => task);
  const scroller = page.getByTestId("workspace-conversation-scroll");
  await expect(page.getByRole("link", { name: "查看图片：图片" })).toBeVisible();
  await expect(page.locator("script", { hasText: "synthetic-secret" })).toHaveCount(0);
  expect(remoteRequests).toEqual([]);
  await scroller.evaluate(element => { element.scrollTop = 300; element.dispatchEvent(new Event("scroll")); });
  await expect(page.getByRole("button", { name: "回到最新" })).toBeVisible();
  task = { ...task, messages: [long, { ...answer, message_id: "answer-2", turn_id: "turn-2", content: "后来到达的完整回答" }] };
  await page.getByRole("button", { name: "重新读取任务" }).click();
  await expect(page.getByText("后来到达的完整回答", { exact: true })).toHaveCount(1);
  expect(await scroller.evaluate(element => element.scrollTop)).toBe(300);
  await page.getByRole("button", { name: "回到最新" }).focus();
  await page.keyboard.press("Enter");
  await expect.poll(() => scroller.evaluate(element => element.scrollHeight - element.clientHeight - element.scrollTop)).toBeLessThan(3);
  await page.reload();
  await expect(page.getByText("后来到达的完整回答", { exact: true })).toHaveCount(1);
  expect(remoteRequests).toEqual([]);
});

test("记录展开保持、用量逐项 unknown 与失败状态不误报完成", async ({ page }) => {
  let task = { ...baseTask, model: "wrong-global-model", agentic_runtime: {
    runtime_version: "pi", permission_profile: "standard", status: "running", run_id: "run-1", candidates: [],
    model_connection_id: "connection-1", model_connection_model: "frozen-model",
  }, work_session: session };
  await mockPage(page, () => task);
  const record = page.getByRole("button", { name: /工作记录/ });
  await expect(record).toHaveAttribute("aria-expanded", "false");
  await record.click();
  task = { ...task, status: "failed", work_session: { ...session, status: "failed", ended_at: "2026-09-08T00:00:05Z" } as typeof session };
  await page.getByRole("button", { name: "重新读取任务" }).click();
  await expect(record).toHaveAttribute("aria-expanded", "true");
  await page.getByRole("button", { name: "查看本次用量" }).click();
  const dialog = page.getByRole("dialog", { name: "本次执行用量" });
  await expect(dialog).toContainText("已知 42 Tokens · 2 次调用 · 另 1 次未知");
  await expect(dialog).toContainText("未知 / 20");
  await expect(dialog).toContainText("已冻结连接");
  await expect(dialog).toContainText("frozen-model");
  await expect(page.getByText("本任务模型：frozen-model", { exact: true })).toHaveCount(1);
  await expect(dialog).not.toContainText("wrong-global-model");
  await expect(dialog).toContainText("参考费用未提供");
  await expect(dialog).toContainText("失败");
  await expect(dialog).not.toContainText("已完成");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "查看本次用量" })).toBeFocused();
});

for (const kind of ["missing", "unknown", "zero"] as const) {
  test(`Session ${kind} 不混淆未知和零`, async ({ page }) => {
    const usage = kind === "unknown" ? { ...session.usage, total_tokens: 0, unknown_call_count: 2 }
      : { input_tokens: 0, output_tokens: 0, cache_tokens: 0, total_tokens: 0, call_count: 1, unknown_call_count: 0 };
    await mockPage(page, () => ({ ...baseTask, work_session: kind === "missing" ? null : { ...session, usage, started_at: null } }));
    await page.getByRole("button", { name: "查看本次用量" }).click();
    const dialog = page.getByRole("dialog", { name: "本次执行用量" });
    await expect(dialog).toContainText(kind === "missing" ? "尚无执行用量记录" : kind === "unknown" ? "总用量未知 · 2 次调用未报告" : "0 Tokens · 1 次调用");
    if (kind !== "zero") await expect(dialog).not.toContainText("0 Tokens");
    if (kind !== "missing") await expect(dialog).toContainText("时间未记录");
  });
}

for (const theme of ["light", "dark"] as const) {
  for (const viewport of [{ width: 390, height: 568 }, { width: 1440, height: 900 }, { width: 720, height: 450 }]) {
  test(`${viewport.width} ${theme}用量键盘、读屏基础和图片安全`, async ({ page }) => {
    await page.setViewportSize(viewport);
    await page.emulateMedia({ reducedMotion: "reduce", colorScheme: theme });
    await page.addInitScript(value => localStorage.setItem("mangrove_theme", value), theme);
    await mockPage(page, () => ({ ...baseTask, messages: [answer], work_session: session }));
    await page.getByRole("button", { name: "查看本次用量" }).click();
    await expect(page.getByRole("dialog", { name: "本次执行用量" })).toBeVisible();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.screenshot({ path: `../.artifacts/issue-128/session-${theme}-${viewport.width}.png` });
    await page.keyboard.press("Escape");
    await expect(page.getByRole("button", { name: "查看本次用量" })).toBeFocused();
  });
  }
}
