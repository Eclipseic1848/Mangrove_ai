import { createServer, type ServerResponse } from "node:http";
import { expect, test, type Page } from "@playwright/test";

const convId = "terminal-conversation";
const taskId = "terminal-collection";
const runId = "chat_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
const prompt = "合成终态收口任务";
const finalReply = "已保留2/5篇合成资料，剩余证据未齐，尚非正式交付。";
const interimCounts = { found: 20, excluded: 12, review_required: 2, unfinished: 6, selected: 0, target_count: 5 };
const finalCounts = { ...interimCounts, review_required: 6, unfinished: 0, selected: 2 };
const finalFiles = ["data.json", "data.xlsx", "evidence.zip"].map(name => ({ name, url: `/api/downloads/${taskId}/${name}` }));
const interimProgress = { node: "collect", label: "采集数据", status: "started", sequence: 29,
  summary: "正在抽取合成证据，完整入选0/5篇", candidate_counts: interimCounts,
  files: [{ name: "data-in-progress.json", url: `/api/downloads/${taskId}/data-in-progress.json?revision=29` }] };
const finalProgress = { ...interimProgress, status: "completed", sequence: 30,
  summary: "本次采集已结束，已保留2/5篇合成资料", candidate_counts: finalCounts, files: finalFiles };

async function setup(page: Page) {
  // 所有业务接口均使用合成响应；实际流也只访问本测试创建的回环服务器。
  await page.route("**/api/**", route => route.fulfill({ status: 404, json: {} }));
  await page.addInitScript(() => {
    localStorage.setItem("mangrove_token", "synthetic-test");
    localStorage.setItem('onboarding_all_["u1","admin"]', "skipped");
  });
  await page.route("**/api/auth/me", route => route.fulfill({ json: { user_id: "u1", username: "tester", role: "admin" } }));
  await page.route(/\/api\/operations\/(visits|heartbeat)$/, route => route.fulfill({ json: { ok: true } }));
  await page.route("**/api/semantic-workspace/tasks?*", route => route.fulfill({ json: [] }));
  await page.route("**/api/chat/feedback**", route => route.fulfill({ json: { feedback: {} } }));
  await page.route("**/api/models", route => route.fulfill({ json: { options: [{ provider: "local", model: "Synthetic", label: "合成模型" }], default: { provider: "local", model: "Synthetic", label: "合成模型" }, pi_runtime_enabled: true } }));
  await page.route("**/api/model-connections", route => route.fulfill({ json: { items: [] } }));
  await page.route("**/api/model-connections/preferences/default", route => route.fulfill({ json: { preference: null } }));
  await page.route("**/api/semantic-workspace/storage", route => route.fulfill({ json: { task_count: 0, recycle_bin_count: 0, total_bytes: 0 } }));
  await page.route("**/api/semantic-workspace/guidance", route => route.fulfill({ json: { steps: [], examples: [], supported_formats: ["json", "xlsx"] } }));
}

const currentCases = {
  open: "SSE未终结但当前轮后台已结束时收口并切换最终2/5资料",
  interrupted: "断流后一次502只读恢复同轮终态并保留新要求",
  otherRun: "当前对话拒绝旧run助手及高序号进度直到本轮终态出现",
  exhausted: "连续三次读取失败停止自动查询并允许手动只读恢复",
};

for (const mode of Object.keys(currentCases) as Array<keyof typeof currentCases>) test(currentCases[mode], async ({ page }, testInfo) => {
  await setup(page);
  const interrupted = mode === "interrupted";
  let started = false, finished = false, executions = 0, statusReads = 0;
  let remainingReadFailures = 0, failedStatusReads = 0, otherRun = false, otherRunReads = 0, otherMessageReads = 0;
  const nextPrompt = "先保留这段新的要求，尚未发送";
  const otherRunId = "chat_cccccccccccccccccccccccccccccccc";
  const otherProgress = { ...interimProgress, sequence: 999, summary: "异轮进度禁止覆盖当前对话", candidate_counts: { ...interimCounts, selected: 4 }, files: [] };
  let stream: ServerResponse | undefined;
  const emitted: string[] = [], writes: string[] = [], errors: string[] = [];
  const network: Record<string, unknown>[] = [];
  page.on("console", message => {
    if (["error", "warning"].includes(message.type())) network.push({ event: "console", type: message.type(), text: message.text() });
  });
  page.on("requestfailed", request => network.push({ event: "requestfailed", method: request.method(), url: request.url(), error: request.failure()?.errorText }));
  page.on("pageerror", error => errors.push(error.message));
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    if (path === "/stream" || path.endsWith("/draft/turns")) network.push({ event: "request", method: request.method(), url: request.url(), header_names: Object.keys(request.headers()) });
    if (request.method() === "POST" && /\/api\/(semantic-workspace\/draft\/turns|chat\/.*\/(resume|cancel))$/.test(path)) writes.push(path);
  });
  const server = createServer((request, response) => {
    network.push({ event: "fixture", method: request.method, path: request.url, origin: request.headers.origin,
      requested_headers: request.headers["access-control-request-headers"], header_names: Object.keys(request.headers) });
    response.setHeader("Access-Control-Allow-Origin", request.headers.origin || "*");
    response.setHeader("Access-Control-Allow-Credentials", "true");
    response.setHeader("Access-Control-Allow-Headers", request.headers["access-control-request-headers"] || "Content-Type,X-Mangrove-Owner,X-Mangrove-CSRF");
    response.setHeader("Access-Control-Allow-Methods", "POST,OPTIONS");
    response.setHeader("Vary", "Origin, Access-Control-Request-Headers");
    if (request.method === "OPTIONS") { response.writeHead(204).end(); return; }
    request.resume();
    stream = response;
    response.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
    const emit = (event: string, data: unknown) => { emitted.push(event); response.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`); };
    started = true; executions++;
    emit("meta", { conv_id: convId, task_id: taskId, chat_run_id: runId });
    emit("progress", interimProgress);
    // 故意保留真实HTTP流，不发送result/done；终态只能来自获准的只读回查。
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as { port: number };
  // 保留产品原POST与请求头，仅把测试传输接到真实回环流，避免合成307跳转失败。
  await page.route("**/api/semantic-workspace/draft/turns", route => route.continue({ url: `http://127.0.0.1:${address.port}/stream` }));
  await page.route("**/api/chat/history", route => route.fulfill({ json: started ? [{ conv_id: convId, title: prompt, updated_at: "2026-10-30T09:00:00Z", status: finished ? "needs_input" : "running" }] : [] }));
  await page.route(`**/api/chat/running/${convId}`, route => {
    statusReads++;
    if (remainingReadFailures > 0) {
      remainingReadFailures--; failedStatusReads++;
      return route.fulfill({ status: 502, json: { detail: "合成状态读取暂时不可用" } });
    }
    if (otherRun) {
      otherRunReads++;
      return route.fulfill({ json: { running: true, chat_run_id: otherRunId, progress: [otherProgress] } });
    }
    return route.fulfill({ json: { running: !finished, chat_run_id: finished ? null : runId, progress: finished ? [interimProgress, finalProgress] : [interimProgress] } });
  });
  await page.route(`**/api/conversations/${convId}/messages`, route => {
    if (otherRun) otherMessageReads++;
    return route.fulfill({ json: [
      { id: 1, role: "user", content: prompt, task_id: taskId, created_at: "2026-10-30T09:00:00Z" },
      ...(otherRun ? [{ id: 2, role: "assistant", content: "异轮助手不能收口本轮任务", meta: { chat_run_id: otherRunId, files: [], work_progress: [otherProgress] } }] : []),
      ...(finished ? [{ id: 3, role: "assistant", content: finalReply, task_id: taskId, created_at: "2026-10-30T09:01:00Z", meta: { kind: "output", chat_run_id: runId, files: finalFiles, work_progress: [interimProgress, finalProgress], collection_outcome: { status: "partial", selected_count: 2, target_complete: false } } }] : []),
    ] });
  });
  await page.route(`**/api/downloads/${taskId}?*`, route => {
    const interim = new URL(route.request().url()).searchParams.get("preview") === "data-in-progress.json";
    if (mode === "open" && interim) return route.fulfill({ status: 502, json: { detail: "合成临时快照预览502" } });
    return route.fulfill({ json: { kind: "json", text: interim ? '{"fixture":"旧临时0/5"}' : '{"fixture":"最终2/5"}', truncated: false, benefit_table: null, outcome: { status: "partial", target_complete: false }, candidate_counts: interim ? interimCounts : finalCounts, in_progress: interim } });
  });
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/data-prep");
    const input = page.getByRole("textbox", { name: "任务要求", exact: true });
    await input.fill(prompt); await input.press("Enter");
    const progress = page.getByRole("region", { name: "采集执行记录", exact: true });
    const preview = page.getByRole("region", { name: "已采集资料预览", exact: true });
    await expect(progress.getByLabel("采集数量", { exact: true })).toContainText("0/5");
    await expect(preview.getByRole("heading", { name: "采集中资料", exact: true })).toBeVisible();
    if (mode === "open") await expect(preview.getByRole("alert")).toContainText("合成临时快照预览502");
    expect(executions).toBe(1);
    if (interrupted) {
      // 只结束进度传输，后台仍运行；原发送已退出等待后才由用户编辑新要求。
      stream!.end(); remainingReadFailures = 1;
      await expect(page.getByText("本次执行状态尚待确认，已保留上次读取的资料；请勿重复提交。", { exact: true })).toBeVisible();
      await expect(page.getByText(/任务正在后台执行，刷新后会恢复结果/)).toHaveCount(0);
      await expect(input).toBeEditable();
      await input.fill(nextPrompt);
      await expect(input).toHaveValue(nextPrompt);
    }
    if (mode === "otherRun") {
      otherRun = true;
      await expect.poll(() => otherRunReads).toBeGreaterThan(0);
      await expect.poll(() => otherMessageReads).toBeGreaterThan(0);
      await expect(page.getByText("本次执行状态尚待确认，已保留上次读取的资料；请勿重复提交。", { exact: true })).toBeVisible();
      await expect(progress).not.toContainText("异轮进度禁止覆盖当前对话");
      await expect(progress.getByLabel("采集数量", { exact: true })).toContainText("0/5");
      await expect(page.getByRole("article", { name: "智能体回复", exact: true })).toHaveCount(0);
      otherRun = false;
    }
    if (mode === "exhausted") {
      remainingReadFailures = 3;
      await expect.poll(() => failedStatusReads, { timeout: 15_000 }).toBe(3);
      const exhaustedReads = statusReads;
      // 覆盖最大失败退避窗口，确认没有第四次自动读取，而不只检查瞬时计数。
      await page.waitForTimeout(6500);
      expect(statusReads).toBe(exhaustedReads);
      await expect(page.getByText(/任务正在后台执行，刷新后会恢复结果/)).toHaveCount(0);
      await expect(preview.getByLabel("采集数量", { exact: true })).toContainText("0/5");
    }
    finished = true;
    if (mode === "otherRun" || mode === "exhausted") await page.getByRole("button", { name: "重新读取执行状态", exact: true }).click();
    await expect(page.getByRole("article", { name: "智能体回复", exact: true })).toContainText(finalReply, { timeout: 15_000 });
    await expect(page.getByText(/任务正在后台执行，刷新后会恢复结果/)).toHaveCount(0);
    await expect(preview.getByRole("heading", { name: "已采集资料", exact: true })).toBeVisible();
    await expect(preview.getByLabel("采集数量", { exact: true })).toContainText("2/5");
    await expect(preview.getByLabel("JSON 资料内容", { exact: true })).toContainText("最终2/5");
    await expect(preview.getByRole("alert")).toHaveCount(0);
    for (const file of finalFiles) await expect(preview.getByRole("button", { name: `下载 ${file.name}`, exact: true })).toBeVisible();
    await expect(preview.getByRole("button", { name: "下载 data-in-progress.json", exact: true })).toHaveCount(0);
    expect(statusReads).toBeGreaterThan(0);
    expect(failedStatusReads).toBe(interrupted ? 1 : mode === "exhausted" ? 3 : 0);
    if (interrupted) await expect(input).toHaveValue(nextPrompt);
    expect(emitted).toEqual(["meta", "progress"]);
    expect(executions).toBe(1);
    expect(writes).toEqual(["/api/semantic-workspace/draft/turns"]);
    expect(errors).toEqual([]);
    await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  } finally {
    await testInfo.attach("terminal-fixture-network", { body: JSON.stringify({ network, emitted, executions, statusReads, failedStatusReads }, null, 2), contentType: "application/json" });
    await page.screenshot({ path: testInfo.outputPath("terminal-state-without-sse-done.png") });
    stream?.destroy(); server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});

for (const rejectOtherRun of [false, true]) test(rejectOtherRun
  ? "历史续问拒绝旧run助手及进度直到当前run终态出现"
  : "历史续问SSE未终结但同轮终态已保存时停止旧进度并使用最终资料", async ({ page }, testInfo) => {
  await setup(page);
  const historyConv = "history-terminal-conversation";
  const historyTask = "history-terminal-collection";
  const historyRun = "chat_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb";
  const historyPrompt = "继续核对本轮合成权益资料";
  const historyFiles = finalFiles.map(file => ({ ...file, url: file.url.replace(taskId, historyTask) }));
  const historyProgress = { ...interimProgress, files: [{ name: "data-in-progress.json", url: `/api/downloads/${historyTask}/data-in-progress.json?revision=29` }] };
  const historyFinalProgress = { ...finalProgress, files: historyFiles };
  let started = false, finished = false, executions = 0, statusReads = 0;
  let otherRun = false, otherRunReads = 0, otherMessageReads = 0;
  const otherRunId = "chat_cccccccccccccccccccccccccccccccc";
  const otherProgress = { ...historyProgress, sequence: 999, summary: "异轮进度禁止覆盖历史续问", candidate_counts: { ...interimCounts, selected: 4 }, files: [] };
  let stream: ServerResponse | undefined;
  const emitted: string[] = [], writes: string[] = [], errors: string[] = [];
  const network: Record<string, unknown>[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => {
    if (["error", "warning"].includes(message.type())) network.push({ event: "console", type: message.type(), text: message.text() });
  });
  page.on("requestfailed", request => network.push({ event: "requestfailed", method: request.method(), url: request.url(), error: request.failure()?.errorText }));
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    if (request.method() === "POST" && /\/api\/(semantic-workspace\/draft\/turns|chat\/.*\/(resume|cancel))$/.test(path)) writes.push(path);
  });
  const server = createServer((request, response) => {
    network.push({ event: "fixture", method: request.method, path: request.url, origin: request.headers.origin });
    response.setHeader("Access-Control-Allow-Origin", request.headers.origin || "*");
    response.setHeader("Access-Control-Allow-Credentials", "true");
    response.setHeader("Access-Control-Allow-Headers", request.headers["access-control-request-headers"] || "Content-Type,X-Mangrove-Owner,X-Mangrove-CSRF");
    response.setHeader("Access-Control-Allow-Methods", "POST,OPTIONS");
    if (request.method === "OPTIONS") { response.writeHead(204).end(); return; }
    request.resume(); stream = response;
    response.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
    const emit = (event: string, data: unknown) => { emitted.push(event); response.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`); };
    started = true; executions++;
    emit("meta", { conv_id: historyConv, task_id: historyTask, chat_run_id: historyRun });
    emit("progress", historyProgress);
    // 历史入口与即时草稿分别维护sending，不能仅凭消息已入库就留下转圈。
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as { port: number };
  await page.route("**/api/semantic-workspace/draft/turns", route => route.continue({ url: `http://127.0.0.1:${address.port}/stream` }));
  await page.route("**/api/chat/history", route => route.fulfill({ json: [{ conv_id: historyConv, title: "已有的合成权益会话", updated_at: "2026-10-30T09:00:00Z", status: started && !finished ? "running" : "needs_input" }] }));
  await page.route(`**/api/chat/running/${historyConv}`, route => {
    statusReads++;
    if (otherRun) {
      otherRunReads++;
      return route.fulfill({ json: { running: true, chat_run_id: otherRunId, progress: [otherProgress] } });
    }
    return route.fulfill({ json: { running: started && !finished, chat_run_id: started && !finished ? historyRun : null, progress: started ? [historyProgress] : [] } });
  });
  await page.route(`**/api/conversations/${historyConv}/messages`, route => {
    if (otherRun) otherMessageReads++;
    return route.fulfill({ json: [
    { id: 1, role: "user", content: "上一轮合成要求", created_at: "2026-10-30T08:00:00Z" },
    { id: 2, role: "assistant", content: "上一轮合成回复，当前轮应使用自己的结果。", created_at: "2026-10-30T08:01:00Z", meta: { chat_run_id: "chat_00000000000000000000000000000000" } },
    ...(started ? [{ id: 3, role: "user", content: historyPrompt, task_id: historyTask, created_at: "2026-10-30T09:00:00Z" }] : []),
    ...(otherRun ? [{ id: 4, role: "assistant", content: "异轮助手不是当前续问结果", meta: { chat_run_id: otherRunId } }] : []),
    ...(finished ? [{ id: 4, role: "assistant", content: finalReply, task_id: historyTask, created_at: "2026-10-30T09:01:00Z", meta: { kind: "output", chat_run_id: historyRun, files: historyFiles, work_progress: [historyProgress, historyFinalProgress], collection_outcome: { status: "partial", selected_count: 2, target_complete: false } } }] : []),
    ] });
  });
  await page.route(`**/api/chat/collections/${historyTask}/recovery`, route => route.fulfill({ status: 409, json: { detail: "原任务没有可恢复的未完成步骤" } }));
  await page.route(`**/api/downloads/${historyTask}?*`, route => {
    const interim = new URL(route.request().url()).searchParams.get("preview") === "data-in-progress.json";
    return route.fulfill({ json: { kind: "json", text: interim ? '{"fixture":"历史旧临时0/5"}' : '{"fixture":"历史最终2/5"}', truncated: false, benefit_table: null, outcome: { status: "partial", target_complete: false }, candidate_counts: interim ? interimCounts : finalCounts, in_progress: interim } });
  });
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/data-prep?conversation=${historyConv}`);
    const input = page.getByRole("textbox", { name: "任务要求", exact: true });
    await input.fill(historyPrompt); await input.press("Enter");
    const preview = page.getByRole("region", { name: "已采集资料预览", exact: true });
    await expect(preview.getByRole("heading", { name: "采集中资料", exact: true })).toBeVisible();
    await expect(preview.getByLabel("采集数量", { exact: true })).toContainText("0/5");
    expect(executions).toBe(1);
    if (rejectOtherRun) {
      otherRun = true;
      await expect.poll(() => otherRunReads).toBeGreaterThan(0);
      await expect.poll(() => otherMessageReads).toBeGreaterThan(0);
      await expect(page.getByRole("status").filter({ hasText: "执行状态待确认，已保留上次读取的资料。" })).toBeVisible();
      await expect(preview.getByLabel("采集数量", { exact: true })).toContainText("0/5");
      await expect(page.getByRole("region", { name: "采集执行记录", exact: true })).not.toContainText("异轮进度禁止覆盖历史续问");
      await expect(page.getByRole("article", { name: "智能体回复", exact: true }).filter({ hasText: finalReply })).toHaveCount(0);
      otherRun = false;
    }
    finished = true;
    if (rejectOtherRun) await page.getByRole("button", { name: "重新读取执行状态", exact: true }).click();
    await expect(page.getByRole("article", { name: "智能体回复", exact: true }).filter({ hasText: finalReply })).toBeVisible();
    await expect(page.getByText(/任务正在后台执行，刷新后会恢复结果/)).toHaveCount(0, { timeout: 15_000 });
    await expect(preview.getByRole("heading", { name: "已采集资料", exact: true })).toBeVisible();
    await expect(preview.getByLabel("采集数量", { exact: true })).toContainText("2/5");
    await expect(preview.getByLabel("JSON 资料内容", { exact: true })).toContainText("历史最终2/5");
    for (const file of historyFiles) await expect(preview.getByRole("button", { name: `下载 ${file.name}`, exact: true })).toBeVisible();
    await expect(preview.getByRole("button", { name: "下载 data-in-progress.json", exact: true })).toHaveCount(0);
    expect(statusReads).toBeGreaterThan(1);
    expect(emitted).toEqual(["meta", "progress"]);
    expect(executions).toBe(1);
    expect(writes).toEqual(["/api/semantic-workspace/draft/turns"]);
    expect(errors).toEqual([]);
  } finally {
    await testInfo.attach("history-terminal-fixture-network", { body: JSON.stringify({ network, emitted, executions, statusReads }, null, 2), contentType: "application/json" });
    await page.screenshot({ path: testInfo.outputPath("history-terminal-state-without-sse-done.png") });
    stream?.destroy(); server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});

test("旧草稿无runId只接受严格历史前缀之后的当前消息对", async ({ page }, testInfo) => {
  await setup(page);
  const prefix = [
    { id: 1, role: "user", content: "旧轮合成要求", created_at: "2026-10-30T08:00:00Z" },
    { id: 2, role: "assistant", content: "旧轮合成回复", created_at: "2026-10-30T08:01:00Z", token_usage: { input_tokens: 1, output_tokens: 1, total_tokens: 2 } },
  ];
  await page.addInitScript(({ prefix, convId, prompt, interimProgress }) => {
    localStorage.setItem("mangrove_workspace_draft_u1_new", JSON.stringify({ draft: {
      prompt, sessionId: convId, connectionId: null, connectionModel: null, localModel: "Synthetic",
      conversation: prefix, collection: { convId, pending: true, prompt, steps: [], progress: [interimProgress] },
    } }));
  }, { prefix, convId, prompt, interimProgress });
  let correctPrefix = false, messageReads = 0;
  const writes: string[] = [];
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    if (request.method() === "POST" && path.startsWith("/api/") && !path.startsWith("/api/operations/")) writes.push(path);
  });
  await page.route("**/api/chat/history", route => route.fulfill({ json: [{ conv_id: convId, title: prompt, updated_at: "2026-10-30T09:01:00Z", status: "needs_input" }] }));
  await page.route(`**/api/chat/running/${convId}`, route => route.fulfill({ json: { running: false, chat_run_id: null, progress: [] } }));
  await page.route(`**/api/conversations/${convId}/messages`, route => {
    messageReads++;
    return route.fulfill({ json: [
      prefix[0], { ...prefix[1], id: correctPrefix ? 2 : 999 },
      { id: 3, role: "user", content: prompt, task_id: taskId, created_at: "2026-10-30T09:00:00Z" },
      { id: 4, role: "assistant", content: finalReply, task_id: taskId, created_at: "2026-10-30T09:01:00Z", meta: { files: finalFiles, work_progress: [interimProgress, finalProgress] } },
    ] });
  });
  await page.route(`**/api/downloads/${taskId}?*`, route => {
    const interim = new URL(route.request().url()).searchParams.get("preview") === "data-in-progress.json";
    return route.fulfill({ json: { kind: "json", text: interim ? '{"fixture":"旧临时0/5"}' : '{"fixture":"严格前缀匹配后的最终2/5"}', truncated: false, benefit_table: null, outcome: { status: "partial" }, candidate_counts: interim ? interimCounts : finalCounts, in_progress: interim } });
  });
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/data-prep");
  await expect(page.getByText("本次执行状态尚待确认，已保留上次读取的资料；请勿重复提交。", { exact: true })).toBeVisible();
  await expect(page.getByRole("article", { name: "智能体回复", exact: true }).filter({ hasText: finalReply })).toHaveCount(0);
  const preview = page.getByRole("region", { name: "已采集资料预览", exact: true });
  await expect(preview.getByLabel("采集数量", { exact: true })).toContainText("0/5");
  const rejectedReads = messageReads;
  correctPrefix = true;
  await page.getByRole("button", { name: "重新读取执行状态", exact: true }).click();
  await expect(page.getByRole("article", { name: "智能体回复", exact: true }).filter({ hasText: finalReply })).toBeVisible();
  await expect(preview.getByRole("heading", { name: "已采集资料", exact: true })).toBeVisible();
  await expect(preview.getByLabel("采集数量", { exact: true })).toContainText("2/5");
  await expect(preview.getByLabel("JSON 资料内容", { exact: true })).toContainText("严格前缀匹配后的最终2/5");
  expect(messageReads).toBeGreaterThan(rejectedReads);
  expect(writes).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("legacy-strict-prefix-terminal.png") });
});

test("切换会话后旧消息GET晚到不会覆盖新会话或其文件", async ({ page }, testInfo) => {
  await setup(page);
  const nextConv = "next-terminal-conversation", nextTask = "next-terminal-collection";
  const nextReply = "切换后保持的新会话回复";
  const lateReply = "迟到的旧任务终态不得覆盖当前会话";
  const nextFiles = finalFiles.map(file => ({ ...file, url: file.url.replace(taskId, nextTask) }));
  await page.addInitScript(({ convId, runId, prompt, interimProgress }) => {
    localStorage.setItem("mangrove_workspace_draft_u1_new", JSON.stringify({ draft: {
      prompt, sessionId: convId, connectionId: null, connectionModel: null, localModel: "Synthetic", conversation: [],
      collection: { convId, runId, pending: true, prompt, steps: [], progress: [interimProgress] },
    } }));
  }, { convId, runId, prompt, interimProgress });
  let release = () => {};
  const held = new Promise<void>(resolve => { release = resolve; });
  let oldReadStarted = false, oldResponseAttempted = false;
  const writes: string[] = [], lateTransport: string[] = [];
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    if (request.method() === "POST" && path.startsWith("/api/") && !path.startsWith("/api/operations/")) writes.push(path);
  });
  await page.route("**/api/chat/history", route => route.fulfill({ json: [
    { conv_id: convId, title: prompt, updated_at: "2026-10-30T09:00:00Z", status: "needs_input" },
    { conv_id: nextConv, title: "切换目标合成会话", updated_at: "2026-10-30T09:01:00Z", status: "needs_input" },
  ] }));
  await page.route(`**/api/chat/running/${convId}`, route => route.fulfill({ json: { running: false, chat_run_id: null, progress: [] } }));
  await page.route(`**/api/conversations/${convId}/messages`, async route => {
    oldReadStarted = true;
    await held;
    try {
      await route.fulfill({ json: [
        { id: 1, role: "user", content: prompt, task_id: taskId },
        { id: 2, role: "assistant", content: lateReply, task_id: taskId, meta: { chat_run_id: runId, files: finalFiles, work_progress: [finalProgress] } },
      ] });
      lateTransport.push("fulfilled");
    } catch (error) {
      // 切换后的请求允许被正常中止；留证而不把传输取消误记为页面接受旧终态。
      lateTransport.push(String(error));
    } finally { oldResponseAttempted = true; }
  });
  await page.route(`**/api/chat/running/${nextConv}`, route => route.fulfill({ json: { running: false, chat_run_id: null, progress: [] } }));
  await page.route(`**/api/conversations/${nextConv}/messages`, route => route.fulfill({ json: [
    { id: 10, role: "user", content: "目标会话合成要求" },
    { id: 11, role: "assistant", content: nextReply, task_id: nextTask, meta: { kind: "output", chat_run_id: "chat_dddddddddddddddddddddddddddddddd", files: nextFiles } },
  ] }));
  await page.route(`**/api/chat/collections/${nextTask}/recovery`, route => route.fulfill({ status: 409, json: { detail: "原任务没有可恢复的未完成步骤" } }));
  await page.route(`**/api/downloads/${nextTask}?*`, route => route.fulfill({ json: { kind: "json", text: '{"fixture":"仅属于新会话的资料"}', truncated: false, benefit_table: null, outcome: { status: "partial" }, candidate_counts: finalCounts, in_progress: false } }));
  await page.route(`**/api/downloads/${taskId}?*`, route => route.fulfill({ json: { kind: "json", text: '{"fixture":"旧会话临时资料"}', truncated: false, benefit_table: null, outcome: null, candidate_counts: interimCounts, in_progress: true } }));
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto("/data-prep");
    await expect.poll(() => oldReadStarted).toBe(true);
    await page.getByRole("button", { name: /切换目标合成会话/ }).click();
    await expect(page).toHaveURL(new RegExp(`conversation=${nextConv}`));
    await expect(page.getByRole("article", { name: "智能体回复", exact: true })).toContainText(nextReply);
    release();
    await expect.poll(() => oldResponseAttempted).toBe(true);
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
    await expect(page.getByRole("article", { name: "智能体回复", exact: true })).toContainText(nextReply);
    await expect(page.getByText(lateReply, { exact: true })).toHaveCount(0);
    const preview = page.getByRole("region", { name: "已采集资料预览", exact: true });
    await expect(preview.getByLabel("JSON 资料内容", { exact: true })).toContainText("仅属于新会话的资料");
    await expect(preview).not.toContainText("旧会话临时资料");
    expect(writes).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath("late-old-response-after-conversation-switch.png") });
  } finally {
    release();
    await testInfo.attach("late-query-transport", { body: JSON.stringify({ oldReadStarted, oldResponseAttempted, lateTransport, writes }, null, 2), contentType: "application/json" });
  }
});

test("显式恢复同一任务的新run收口后刷新同URL最终预览到3/5", async ({ page }, testInfo) => {
  await setup(page);
  const resumeConv = "resume-terminal-conversation";
  const resumeTask = "resume-terminal-collection";
  const resumedRun = "chat_eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee";
  const resumedReply = "恢复后已保留3/5篇合成资料，仍有缺口，尚非正式交付。";
  const resumeFiles = finalFiles.map(file => ({ ...file, url: file.url.replace(taskId, resumeTask) }));
  const resumeProgress = { ...interimProgress, summary: "正在恢复原任务的合成未完成步骤", candidate_counts: finalCounts, files: resumeFiles };
  const resumedCounts = { ...finalCounts, selected: 3, review_required: finalCounts.review_required - 1 };
  const resumedProgress = { ...finalProgress, summary: "恢复已结束，已保留3/5篇合成资料", candidate_counts: resumedCounts, files: resumeFiles };
  let started = false, finished = false, executions = 0, previewReads = 0;
  let stream: ServerResponse | undefined;
  const emitted: string[] = [], writes: string[] = [], previewUrls: string[] = [];
  const resumeRequests: Array<{ request_id: string; external_api_confirmed: boolean }> = [];
  page.on("request", request => {
    const path = new URL(request.url()).pathname;
    if (request.method() === "POST" && path.startsWith("/api/") && !path.startsWith("/api/operations/")) writes.push(path);
  });
  const server = createServer((request, response) => {
    response.setHeader("Access-Control-Allow-Origin", request.headers.origin || "*");
    response.setHeader("Access-Control-Allow-Credentials", "true");
    response.setHeader("Access-Control-Allow-Headers", request.headers["access-control-request-headers"] || "Content-Type,X-Mangrove-Owner,X-Mangrove-CSRF");
    response.setHeader("Access-Control-Allow-Methods", "POST,OPTIONS");
    if (request.method === "OPTIONS") { response.writeHead(204).end(); return; }
    request.resume(); stream = response;
    response.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-cache" });
    const emit = (event: string, data: unknown) => { emitted.push(event); response.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`); };
    started = true; executions++;
    emit("meta", { conv_id: resumeConv, task_id: resumeTask, chat_run_id: resumedRun });
    emit("progress", resumeProgress);
    // 恢复仍复用原任务产物地址；新run落库不能继续展示旧run已缓存的正文。
  });
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const address = server.address() as { port: number };
  await page.route(`**/api/chat/collections/${resumeTask}/resume`, route => {
    resumeRequests.push(route.request().postDataJSON());
    return route.continue({ url: `http://127.0.0.1:${address.port}/stream` });
  });
  await page.route("**/api/chat/history", route => route.fulfill({ json: [{ conv_id: resumeConv, title: "合成部分任务的显式恢复", updated_at: "2026-10-30T09:00:00Z", status: started && !finished ? "running" : "needs_input" }] }));
  await page.route(`**/api/chat/running/${resumeConv}`, route => route.fulfill({ json: { running: started && !finished, chat_run_id: started && !finished ? resumedRun : null, progress: started ? [resumeProgress] : [] } }));
  await page.route(`**/api/conversations/${resumeConv}/messages`, route => route.fulfill({ json: [
    { id: 1, role: "user", content: "最初的合成权益要求", task_id: resumeTask },
    { id: 2, role: "assistant", content: "上次已保留2/5篇合成资料", task_id: resumeTask, meta: { kind: "output", chat_run_id: runId, files: resumeFiles, work_progress: [{ ...finalProgress, files: resumeFiles }], collection_outcome: { status: "partial", selected_count: 2, target_complete: false } } },
    ...(started ? [{ id: 3, role: "user", content: "恢复原采集任务", task_id: resumeTask }] : []),
    ...(finished ? [{ id: 4, role: "assistant", content: resumedReply, task_id: resumeTask, meta: { kind: "output", chat_run_id: resumedRun, files: resumeFiles, work_progress: [resumeProgress, resumedProgress], collection_outcome: { status: "partial", selected_count: 3, target_complete: false } } }] : []),
  ] }));
  await page.route(`**/api/chat/collections/${resumeTask}/recovery`, route => route.fulfill({ json: {
    message: finished ? "合成任务仍有未完成缺口，再次恢复也需你明确确认。" : "合成任务有可恢复的未完成步骤，仅在你确认后继续。",
  } }));
  await page.route(`**/api/downloads/${resumeTask}?*`, route => {
    previewReads++; previewUrls.push(route.request().url());
    return route.fulfill({ json: { kind: "json", text: finished ? '{"fixture":"同任务恢复后的3/5"}' : '{"fixture":"同任务恢复前的2/5"}', truncated: false, benefit_table: null, outcome: { status: "partial", target_complete: false }, candidate_counts: finished ? resumedCounts : finalCounts, in_progress: false } });
  });
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.goto(`/data-prep?conversation=${resumeConv}`);
    const preview = page.getByRole("region", { name: "已采集资料预览", exact: true });
    await expect(preview.getByLabel("JSON 资料内容", { exact: true })).toContainText("同任务恢复前的2/5");
    await expect(preview.getByLabel("采集数量", { exact: true })).toContainText("2/5");
    const cachedReads = previewReads;
    expect(cachedReads).toBeGreaterThan(0);
    await page.getByRole("button", { name: "恢复原采集任务", exact: true }).click();
    await expect(preview.getByRole("heading", { name: "采集中资料", exact: true })).toBeVisible();
    expect(executions).toBe(1);
    expect(resumeRequests).toHaveLength(1);
    expect(resumeRequests[0].request_id).toMatch(/^[a-f0-9]{32}$/);
    expect(resumeRequests[0].external_api_confirmed).toBe(true);
    finished = true;
    await expect(page.getByRole("article", { name: "智能体回复", exact: true }).filter({ hasText: resumedReply })).toBeVisible();
    await expect(page.getByText(/任务正在后台执行，刷新后会恢复结果/)).toHaveCount(0);
    await expect(preview.getByRole("heading", { name: "已采集资料", exact: true })).toBeVisible();
    await expect(preview.getByLabel("采集数量", { exact: true })).toContainText("3/5");
    await expect(preview.getByLabel("JSON 资料内容", { exact: true })).toContainText("同任务恢复后的3/5");
    expect(previewReads).toBeGreaterThan(cachedReads);
    const input = page.getByRole("textbox", { name: "任务要求", exact: true });
    await input.fill("恢复结束后保留的新要求，不发送");
    await expect(page.getByRole("button", { name: "发送", exact: true })).toBeEnabled();
    expect(writes).toEqual([`/api/chat/collections/${resumeTask}/resume`]);
    expect(emitted).toEqual(["meta", "progress"]);
    expect(executions).toBe(1);
  } finally {
    await testInfo.attach("same-task-resume-preview", { body: JSON.stringify({ executions, emitted, resumeRequests, previewReads, previewUrls }, null, 2), contentType: "application/json" });
    await page.screenshot({ path: testInfo.outputPath("same-task-new-run-final-preview.png") });
    stream?.destroy(); server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
  }
});
