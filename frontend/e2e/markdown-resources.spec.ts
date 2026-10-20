import { expect, test, type Page } from "@playwright/test";

const imageUrl = "https://markdown-resources.invalid/image.png";
const sourceUrl = "https://markdown-resources.invalid/source";
const localImagePath = "/markdown-fixture-image.png";
const content = [
  "# 合成 Markdown 安全报告",
  "",
  "- 可读列表项",
  "",
  "| 来源 | 状态 |",
  "| --- | --- |",
  "| 合成资料 | 保留正文 |",
  "",
  "```text",
  "只读代码示例",
  "```",
  "",
  `[普通来源](${sourceUrl})`,
  "[脚本协议](javascript:alert%281%29)",
  "[数据协议](data:text/html;base64,PGgxPmZpeHR1cmU8L2gxPg==)",
  "",
  `![外部来源图](${imageUrl})`,
  `![站内来源图](${localImagePath})`,
  "![](https://markdown-resources.invalid/no-alt.png)",
  "![危险脚本图](javascript:alert%281%29)",
  "![危险数据图](data:image/png;base64,aW52YWxpZA==)",
].join("\n");

const surfaces = ["最近运行报告", "计划历史报告", "模板预览", "经验预览", "旧 Chat 历史", "工作台独立回答", "工作台追问回答"] as const;
type Surface = typeof surfaces[number];

async function mockResources(page: Page, surface: Surface) {
  const state = { external: [] as string[], local: [] as string[], unexpected: [] as string[], errors: [] as string[] };
  page.on("pageerror", error => state.errors.push(error.message));
  // 只保存教程偏好；身份仍由当前页面正常的会话读取入口提供合成响应。
  await page.addInitScript(() => localStorage.setItem('onboarding_all_["markdown-owner","admin"]', "skipped"));
  // 精确合成域由本机替身响应；既能观察旧 img 请求，也不依赖实际外联成功。
  await page.context().route("https://markdown-resources.invalid/**", route => {
    state.external.push(route.request().url());
    return route.fulfill(route.request().isNavigationRequest()
      ? { contentType: "text/html; charset=utf-8", body: '<!doctype html><html lang="zh-CN"><title>合成来源</title><link rel="icon" href="data:,"><h1>用户主动打开的合成来源</h1></html>' }
      : { contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jQ1sAAAAASUVORK5CYII=", "base64") });
  });
  await page.route(`**${localImagePath}`, route => {
    state.local.push(new URL(route.request().url()).pathname);
    return route.fulfill({ contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jQ1sAAAAASUVORK5CYII=", "base64") });
  });

  const task = {
    task_id: "markdown-task", title: "合成 Markdown 任务", objective_text: "读取已保存的合成报告",
    upload_ids: [], output_formats: ["md"], provider: "local", model: "Synthetic", runtime_version: "legacy",
    external_api_confirmed: false, status: "completed", active_revision: 1, current_revision: 1, viewing_revision: 1,
    plan_id: null, logical_revision: null, binding_revision: null, run_id: null,
    summary: "合成报告已保存", error: null, question: null, cancel_requested: false,
    deleted_at: null, purge_after: null, created_at: "2026-10-20T00:00:00Z", updated_at: "2026-10-20T00:01:00Z",
  };
  const message = {
    message_id: "markdown-answer", task_id: task.task_id, revision: 1, run_id: null,
    turn_id: surface === "工作台追问回答" ? "markdown-turn" : null,
    role: "assistant", kind: "answer", content, status: "completed", created_at: task.updated_at,
  };
  const plan = {
    task_id: "markdown-plan", name: "合成 Markdown 计划", user_input: "合成只读需求", status: "active",
    trigger_type: "interval", interval_seconds: 86400, run_count: 1, next_run_at: "2099-10-21T09:00:00",
    time_zone: "Asia/Shanghai", execution_state: "idle", provider: "local", model: "Synthetic", source: "manual",
  };
  const run = {
    task_id: plan.task_id, task_name: plan.name, run_id: 1, run_at: task.updated_at,
    success: true, state: "succeeded", summary: "合成报告回执", has_report: true, has_json: false,
  };
  const entry = {
    slug: "markdown-entry", title: "合成 Markdown 方法", body: content, data_type: "article", keywords: [],
    status: "active", uses: 1, quality_avg: 80, occurrences: 1, helped_avoid: 1,
    scope: "owner", is_owner: true, can_delete: true, content_digest: "a".repeat(64),
  };
  await page.route("**/api/**", route => {
    const request = route.request(), path = new URL(request.url()).pathname;
    const json = (value: unknown) => route.fulfill({ json: value });
    if (path === "/api/auth/me") return json({ user_id: "markdown-owner", username: "合成账号", display_name: "合成账号", role: "admin" });
    if (path === "/api/operations/visits" || path === "/api/operations/heartbeat") return json({ ok: true });
    if (path === "/api/models") return json({ options: [{ provider: "local", model: "Synthetic", label: "合成模型" }], default: { provider: "local", model: "Synthetic" }, pi_runtime_enabled: true });
    if (path === "/api/model-connections") return json({ items: [] });
    if (path === "/api/model-connections/preferences/default") return json({ preference: null });
    if (path === "/api/model-connections/presets") return json({ presets: [] });
    if (path === "/api/settings/onboarding/model-connections") return json({ completed: true });
    if (path === "/api/tasks") return json([plan]);
    if (path === "/api/tasks/templates") return json([]);
    if (path === "/api/tasks/scheduler/status") return json({ enabled: true, running: true });
    if (path === "/api/tasks/runs/recent") return json({ items: [run], total: 1 });
    if (path === `/api/tasks/${plan.task_id}/runs/1/report`) return json({ content, run_at: run.run_at, success: true });
    if (path === "/api/templates") return json({ templates: [entry] });
    if (path === "/api/lessons") return json({ lessons: [entry] });
    if (path === "/api/conversations") return json([{ conv_id: "markdown-conversation", title: "合成 Markdown 历史", updated_at: task.updated_at }]);
    if (path === "/api/conversations/markdown-conversation/messages") return json([{ id: 1, role: "assistant", content, created_at: task.updated_at, meta: {} }]);
    if (path === "/api/chat/running/markdown-conversation") return json({ running: false, progress: [] });
    if (path === "/api/chat/feedback") return json({ feedback: {} });
    if (path === "/api/chat/history") return json([]);
    if (path === "/api/semantic-workspace/tasks") return json([task]);
    if (path === `/api/semantic-workspace/tasks/${task.task_id}`) return json({
      ...task, revisions: [{ ...task, revision: 1, change_summary: "" }], events: [], uploads: [],
      plan: null, run: null, attempts: [], harness_events: [], delivery: null, messages: [message],
    });
    if (path === `/api/semantic-workspace/tasks/${task.task_id}/turns`) return json({
      turns: surface === "工作台追问回答" ? [{ turn_id: "markdown-turn", revision: 1, text: "核对报告的来源", created_at: task.created_at }] : [],
      results: [], proposals: [],
    });
    if (path === `/api/semantic-workspace/tasks/${task.task_id}/draft`) return json({ draft: null });
    if (path === `/api/semantic-workspace/tasks/${task.task_id}/feedback`) return json({ feedback: {} });
    if (path === "/api/semantic-workspace/capabilities") return json({ enabled: false, items: [] });
    if (path === "/api/semantic-workspace/storage") return json({ task_count: 1, recycle_bin_count: 0, upload_bytes: 0, delivery_bytes: 0, total_bytes: 0, retention: "合成保留策略", calculated_at: task.updated_at });
    if (path === "/api/semantic-workspace/guidance") return json({ steps: [], examples: [], supported_formats: ["md"] });
    // 未声明接口始终失败关闭，不落入任何真实后端。
    state.unexpected.push(`${request.method()} ${path}`);
    return route.fulfill({ status: 404, json: {} });
  });
  return state;
}

async function openSurface(page: Page, surface: Surface) {
  if (surface === "最近运行报告" || surface === "计划历史报告") {
    await page.goto("/tasks");
    if (surface === "最近运行报告") await page.getByRole("button", { name: "运行记录", exact: true }).click();
    else await page.getByRole("button", { name: "历史", exact: true }).click();
    await page.getByRole("button", { name: "查看", exact: true }).click();
    return page.getByRole("dialog").locator(".prose-mangrove");
  }
  if (surface === "模板预览" || surface === "经验预览") {
    await page.goto("/templates");
    if (surface === "经验预览") await page.getByRole("button", { name: "教训库", exact: true }).click();
    await page.getByTitle(surface === "模板预览" ? "查看模板正文" : "查看教训正文", { exact: true }).click();
    return page.getByRole("dialog").locator(".prose-mangrove");
  }
  if (surface === "旧 Chat 历史") {
    await page.goto("/chat");
    await page.getByRole("button", { name: "合成 Markdown 历史", exact: true }).click();
  } else {
    await page.goto("/data-prep?task=markdown-task");
    await expect(page.getByRole("heading", { name: "合成 Markdown 任务", exact: true })).toBeVisible();
  }
  return page.locator(".prose-mangrove");
}

for (const surface of surfaces) test(`Markdown 资源默认安全且正文可读：${surface}`, async ({ page }, testInfo) => {
  await page.setViewportSize(surface === "经验预览" || surface === "工作台追问回答"
    ? { width: 390, height: 844 } : { width: 1440, height: 1000 });
  const state = await mockResources(page, surface);
  const markdown = await openSurface(page, surface);
  await expect(page).toHaveTitle(/Mangrove/i);
  await expect(markdown.getByRole("heading", { name: "合成 Markdown 安全报告", exact: true })).toBeVisible();
  await expect(markdown.getByRole("listitem")).toHaveText("可读列表项");
  await expect(markdown.getByRole("cell", { name: "保留正文", exact: true })).toBeVisible();
  await expect(markdown.locator("pre code")).toHaveText("只读代码示例\n");
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("markdown-before-click.png"), animations: "disabled" });
  await testInfo.attach("automatic-resource-observation", {
    body: JSON.stringify({ images: await markdown.locator("img").evaluateAll(images => images.map(image => ({ src: image.getAttribute("src"), alt: image.getAttribute("alt") }))), external: state.external, local: state.local }, null, 2),
    contentType: "application/json",
  });
  expect(await markdown.locator("img").count()).toBe(0);
  expect(state.external).toEqual([]);
  expect(state.local).toEqual([]);

  const image = markdown.getByRole("link", { name: "查看图片：外部来源图", exact: true });
  await expect(image).toHaveAttribute("href", imageUrl);
  await expect(markdown.getByRole("link", { name: "查看图片：站内来源图", exact: true })).toHaveAttribute("href", localImagePath);
  await expect(markdown.getByRole("link", { name: "查看图片：图片", exact: true })).toHaveAttribute("href", "https://markdown-resources.invalid/no-alt.png");
  await expect(markdown.getByText("危险脚本图", { exact: true })).toBeVisible();
  await expect(markdown.getByText("危险数据图", { exact: true })).toBeVisible();
  for (const label of ["脚本协议", "数据协议"]) await expect(markdown.getByText(label, { exact: true })).toHaveAttribute("href", "");
  await expect(markdown.locator('a[href^="javascript:"], a[href^="data:"]')).toHaveCount(0);
  for (const link of await markdown.getByRole("link").all()) {
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(link).toHaveAttribute("rel", "noopener noreferrer");
  }
  await expect(markdown.getByRole("link", { name: "普通来源", exact: true })).toHaveAttribute("href", sourceUrl);
  const opened = page.waitForEvent("popup");
  await image.focus();
  await page.keyboard.press("Enter");
  const popup = await opened;
  await expect(popup).toHaveURL(imageUrl);
  await expect(popup.getByRole("heading", { name: "用户主动打开的合成来源", exact: true })).toBeVisible();
  expect(await popup.evaluate(() => ({ opener: window.opener, referrer: document.referrer }))).toEqual({ opener: null, referrer: "" });
  await popup.close();
  expect(state.external).toEqual([imageUrl]);
  expect(state.local).toEqual([]);
  expect(state.unexpected).toEqual([]);
  expect(state.errors).toEqual([]);
});
