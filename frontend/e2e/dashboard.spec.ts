import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const presets = ["deepseek", "qwen", "openai", "anthropic", "gemini", "kimi", "zhipu", "xai"].map(preset_id => ({ preset_id, display_name: preset_id }));

test("公开构建缺少可选品牌资源时不请求不存在的图片", async ({ page }) => {
  await mockOverview(page);
  const missing: string[] = [];
  page.on("response", response => {
    if (response.url().includes("/overview-brands/") && response.status() >= 400) missing.push(response.url());
  });
  await page.goto("/");
  await expect(page.getByRole("region", { name: "平台登录态" })).toBeVisible();
  await expect(page.getByText("DeepSeek", { exact: true })).toBeVisible();
  const images = page.locator('img[src^="/overview-brands/"]');
  for (const img of await images.all()) await expect.poll(() => img.evaluate(node => (node as HTMLImageElement).complete && (node as HTMLImageElement).naturalWidth > 0)).toBeTruthy();
  expect(missing).toEqual([]);
});

test("概览分页默认十条，支持四种条数及筛选复位", async ({ page }, testInfo) => {
  await mockOverview(page);
  const items = Array.from({ length: 123 }, (_, index) => ({ id: `paged-${index + 1}`, kind: "task", title: `分页任务 ${index + 1}`, status: "running", updated_at: "2026-09-16T10:00:00+08:00" }));
  await page.route("**/api/overview/schedules", route => route.fulfill({ json: Array.from({ length: 23 }, (_, index) => ({ task_id: `schedule-${index + 1}`, name: `分页计划 ${index + 1}`, status: "active", trigger_type: "interval", interval_seconds: 86400, run_count: 0 })) }));
  await page.route("**/api/overview/activity?*", route => {
    const params = new URL(route.request().url()).searchParams;
    const offset = Number(params.get("offset"));
    const limit = Number(params.get("limit"));
    return route.fulfill({ json: { stats: { active: 123, attention: 0, completed: 0 }, total: 123, items: items.slice(offset, offset + limit) } });
  });
  await page.goto("/");
  const list = page.getByRole("region", { name: "任务与定时计划" });
  const size = list.getByRole("combobox", { name: "每页条数" });
  const rows = list.getByRole("link", { name: /^分页任务/ });
  await expect(size).toHaveValue("10");
  await expect(size.locator("option")).toHaveText(["10", "20", "50", "100"]);
  await expect(rows).toHaveCount(10);
  await list.getByRole("navigation", { name: "概览列表分页" }).screenshot({ path: testInfo.outputPath("overview-pagination-desktop.png") });
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/overview/activity?*", async route => { await gate; await route.fallback(); }, { times: 1 });
  await list.getByRole("button", { name: "下一页" }).click();
  await expect(list.getByText("正在加载任务列表…")).toBeVisible();
  await expect(list.getByText("2 / 13 页", { exact: true })).toBeVisible();
  await expect(list.getByRole("button", { name: "下一页" })).toBeFocused();
  release();
  await expect(rows.first()).toHaveText("分页任务 11");
  for (const value of [20, 50, 100]) {
    await size.selectOption(String(value));
    await expect(rows).toHaveCount(value);
    await expect(rows.first()).toHaveText("分页任务 1");
    await list.getByRole("button", { name: "下一页" }).click();
    await expect(rows.first()).toHaveText(`分页任务 ${value + 1}`);
  }
  await expect(rows).toHaveCount(23);
  await expect(list.getByRole("button", { name: "下一页" })).toBeDisabled();
  await list.getByRole("combobox", { name: "任务状态" }).selectOption("active");
  await expect(rows).toHaveCount(100);
  await expect(rows.first()).toHaveText("分页任务 1");
  await list.getByRole("button", { name: "定时计划", exact: true }).click();
  await expect(size).toHaveValue("100");
  await expect(list.getByText("1 / 1 页", { exact: true })).toBeVisible();
  await size.selectOption("10");
  const plans = list.getByRole("link", { name: /^分页计划/ });
  await expect(plans).toHaveCount(10);
  await list.getByRole("button", { name: "下一页" }).click();
  await expect(plans.first()).toHaveText("分页计划 11");
  await list.getByRole("button", { name: "下一页" }).click();
  await expect(plans).toHaveCount(3);
  await expect(plans.first()).toHaveText("分页计划 21");
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(size).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await list.screenshot({ path: testInfo.outputPath("overview-pagination-mobile.png") });
});
async function mockOverview(page: Page, role = "user") {
  // 本组验证概览业务交互；首次教程由 onboarding.spec.ts 独立覆盖，避免遮罩抢占点击。
  await page.addInitScript(currentRole => localStorage.setItem(`onboarding_all_${JSON.stringify(["owner", currentRole])}`, "skipped"), role);
  await page.route("**/api/**", route => {
    const url = new URL(route.request().url());
    if (route.request().method() !== "GET") return route.fulfill({ status: 405, json: { detail: "审计禁止写操作" } });
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: { user_id: "owner", username: "owner", display_name: "合成用户", role } });
    if (url.pathname === "/api/model-connections/presets") return route.fulfill({ json: { items: presets } });
    if (url.pathname === "/api/model-connections") return route.fulfill({ json: { items: [
      { connection_id: "cloud", owner_scope: "platform_shared", preset_id: "deepseek", display_name: "DeepSeek", status: "verified", has_key: true, locality: "public_external", model: "synthetic", models: [] },
      ...["Qwen3.8", "Qwen3.6", "Qwen3.5", "Qwen3"].map(model => ({ connection_id: model, owner_scope: "platform_shared", display_name: model, status: "verified", locality: "managed_private", model, models: [{ model_id: model, display_name: model, enabled: true, status: "available" }] })),
      { connection_id: "retired-import", owner_scope: "platform_shared", display_name: "历史导入", status: "pending_validation", locality: "managed_private", model: "Qwen3.8-27B-FP8", models: [{ model_id: "Qwen3.8-27B-FP8", display_name: "Qwen3.8-27B-FP8", enabled: false, status: "model_access_denied", current_catalog: false }] },
    ] } });
    if (url.pathname === "/api/overview/activity") {
      const filter = url.searchParams.get("filter");
      return route.fulfill({ json: { stats: { active: 2, attention: 1, completed: 8 }, total: 1,
        items: [{ id: "task-1", kind: "task", title: "合成报销单整理", status: filter === "attention" ? "needs_input" : "running", updated_at: "2026-09-16T10:00:00+08:00" }], updated_at: "2026-09-16T10:00:00+08:00" } });
    }
    if (["/api/tasks", "/api/overview/schedules"].includes(url.pathname)) return route.fulfill({ json: [
      { task_id: "plan-1", name: "每日报告", status: "active", user_input: "合成计划", trigger_type: "interval", interval_seconds: 86400, run_count: 2, next_run_at: "2099-09-17T09:00:00+08:00", last_success: 1, last_run_at: "2026-09-16T09:00:00+08:00" },
      { task_id: "plan-2", name: "暂停的周报", status: "paused", user_input: "合成计划", trigger_type: "cron", cron_expr: "0 9 * * 1", run_count: 0 },
    ] });
    if (url.pathname === "/api/overview/services") return route.fulfill({ json: {
      cookies: ["xiaohongshu", "weibo", "douyin", "bilibili", "zhihu", "kuaishou", "tieba", "jd", "taobao", "pdd"].map((platform, index) => ({ platform, status: index < 3 ? "valid" : "unknown", checked_at: index < 3 ? "2026-09-16T10:00:00+08:00" : null })),
      services: ["搜索采集", "邮件", "Slack", "知识检索"].map((label, index) => ({ key: ["search", "email", "slack", "embedding"][index], label, configured: index < 2, enabled: true })), scheduler_enabled: true,
    } });
    return route.fulfill({ json: [] });
  });
}

for (const role of ["user", "admin", "super_admin"]) {
  test(`${role} 概览显示真实分类、灰显未配供应商和定时计划`, async ({ page }, testInfo) => {
    await mockOverview(page, role);
    await page.setViewportSize({ width: 1440, height: 1000 });
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "模型与连接", exact: true })).toBeVisible();
    const pageHeader = page.locator("header").filter({ has: page.getByRole("heading", { name: "概览", exact: true }) });
    await expect(pageHeader.locator("h1")).toHaveCSS("font-size", "18px");
    await expect(pageHeader.locator("h1")).toHaveCSS("font-weight", "600");
    await expect(pageHeader.locator("p")).toHaveCSS("font-size", "14px");
    await expect(pageHeader).toHaveCSS("padding-left", "28px");
    await expect(pageHeader).toHaveCSS("padding-top", "16px");
    await expect(page.locator('[data-service-subtitle]').first()).toContainText("本地模型 · 4 个已配置");
    const claude = page.locator('[data-provider="anthropic"]');
    await expect(claude).toContainText("Claude");
    await expect(claude).toContainText("未配置");
    // 公开构建可用通用图标代替本机品牌图片，两种形式都应保持未配置的弱化样式。
    await expect(claude.locator("img, svg").first()).toHaveClass(/grayscale|text-muted-foreground/);
    await expect(page.getByText("已启用 1 · 已暂停 1")).toBeVisible();
    await expect(page.getByRole("link", { name: "运行与诊断", exact: true })).toHaveCount(role === "user" ? 0 : 1);
    const alignment = await page.locator('[data-service-body], [data-service-footer]').evaluateAll(nodes => nodes.map(node => ({ top: node.getBoundingClientRect().top, footer: node.hasAttribute("data-service-footer") })));
    for (const footer of [true, false]) {
      const tops = alignment.filter(item => item.footer === footer).map(item => item.top);
      expect(Math.max(...tops) - Math.min(...tops)).toBeLessThanOrEqual(1);
    }
    await expect.poll(() => page.locator('img').evaluateAll(images => images.every(image => (image as HTMLImageElement).complete && (image as HTMLImageElement).naturalWidth > 0))).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(`${role}-overview-tasks.png`), fullPage: true });
    if (role === "super_admin") {
      await page.setViewportSize({ width: 1543, height: 1019 });
      await page.screenshot({ path: testInfo.outputPath("overview-reference-size.png"), fullPage: true });
      await page.getByRole("region", { name: "平台服务", exact: true }).screenshot({ path: testInfo.outputPath("overview-services-detail.png") });
      await page.setViewportSize({ width: 1440, height: 1000 });
    }
    await page.getByRole("button", { name: /待处理/ }).click();
    await expect(page).toHaveURL(/filter=attention/);
    await expect(page.getByRole("link", { name: /合成报销单整理/ })).toHaveAttribute("href", "/data-prep?task=task-1");
    await page.getByRole("button", { name: "定时计划", exact: true }).click();
    await expect(page.getByRole("link", { name: /每日报告/ })).toHaveAttribute("href", "/tasks?task=plan-1");
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`${role}-overview.png`), fullPage: true });
    expect(errors).toEqual([]);
    await page.getByRole("link", { name: /每日报告/ }).click();
    await expect(page).toHaveURL(/\/tasks\?task=plan-1/);
    await expect(page.getByText("暂停的周报", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("link", { name: "查看全部计划" })).toBeVisible();
    await expect(page).not.toHaveTitle("概览 · Mangrove");
  });
}

test("窄屏、键盘与失败恢复不伪造健康状态", async ({ page }, testInfo) => {
  await mockOverview(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "概览", exact: true })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await page.getByRole("button", { name: "打开导航" }).click();
  await expect(page.getByRole("dialog", { name: "全局导航" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "打开导航" })).toBeFocused();
  await page.getByRole("button", { name: "打开导航" }).click();
  await page.setViewportSize({ width: 1440, height: 1000 });
  await expect(page.getByRole("dialog", { name: "全局导航" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "新建任务", exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route("**/api/overview/services", route => route.fulfill({ status: 503, json: { detail: "合成故障" } }));
  await page.getByRole("button", { name: "刷新概览" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "服务状态加载失败" }).first()).toBeVisible();
  await expect(page.getByText("已配置 · 未检查").first()).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("overview-mobile-stale.png"), fullPage: true });
});

for (const width of [390, 768]) {
  test(`${width}px 深色空状态与加载失败可恢复`, async ({ page }, testInfo) => {
    await mockOverview(page);
    await page.addInitScript(() => localStorage.setItem("mangrove_theme", "dark"));
    await page.setViewportSize({ width, height: 1000 });
    await page.route("**/api/overview/activity?*", route => route.fulfill({ json: { stats: { active: 0, attention: 0, completed: 0 }, items: [], total: 0 } }));
    await page.route("**/api/overview/schedules", route => route.fulfill({ json: [] }));
    await page.route("**/api/overview/services", route => route.fulfill({ status: 503, json: { detail: "合成故障" } }));
    await page.goto("/?page=999");
    await expect(page).toHaveURL(/page=1/);
    await expect(page.getByText("还没有任务，从右上角新建任务开始。")).toBeVisible();
    await expect(page.getByRole("alert").filter({ hasText: "状态未知" }).first()).toBeVisible();
    await expect(page.getByText("已配置 · 未检查")).toHaveCount(0);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`overview-${width}-dark-empty.png`), fullPage: true });
    await page.getByRole("button", { name: "定时计划", exact: true }).click();
    await expect(page.getByText("还没有定时计划。前往自动化任务添加。")).toBeVisible();
    await page.unroute("**/api/overview/services");
    await page.getByRole("button", { name: "刷新概览" }).click();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(page.getByText("已配置 · 未检查").first()).toBeVisible();
  });
}

test("慢请求有加载态，新增供应商可展开，键盘筛选可用", async ({ page }, testInfo) => {
  await mockOverview(page);
  await page.emulateMedia({ reducedMotion: "reduce" });
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/overview/activity?*", async route => {
    await gate;
    await route.fulfill({ json: { stats: { active: 0, attention: 0, completed: 0 }, items: [], total: 0 } });
  });
  await page.route("**/api/model-connections/presets", route => route.fulfill({ json: { items: [...presets, { preset_id: "new", display_name: "新配置供应商" }] } }));
  await page.goto("/");
  await expect(page.getByText("正在加载任务列表…")).toBeVisible();
  await expect(page.getByRole("button", { name: /^进行中/ })).toContainText("—");
  await page.screenshot({ path: testInfo.outputPath("overview-loading.png"), fullPage: true });
  release();
  await expect(page.getByText("还没有任务，从右上角新建任务开始。")).toBeVisible();
  const more = page.getByRole("button", { name: "更多连接", exact: true });
  await more.focus(); await page.keyboard.press("Enter");
  await expect(page.getByText("新配置供应商", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "收起", exact: true }).click();
  await page.getByRole("combobox", { name: "任务状态" }).focus();
  await page.keyboard.press("ArrowDown"); await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/filter=active/);
});
