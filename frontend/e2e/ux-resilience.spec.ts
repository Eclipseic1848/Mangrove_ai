import { expect, test, type Page } from "@playwright/test";

async function mockApp(page: Page) {
  await page.addInitScript(() => {
    localStorage.setItem('onboarding_all_["ux-owner","admin"]', "skipped");
  });
  // 合成接口兜底，任何测试都不落到真实账号或业务数据。
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: { user_id: "ux-owner", username: "ux", display_name: "体验回归", role: "admin" } });
    if (path === "/api/models") return route.fulfill({ json: { options: [], default: null } });
    if (path === "/api/model-connections") return route.fulfill({ json: { items: [] } });
    if (path === "/api/model-connections/preferences/default") return route.fulfill({ json: { preference: null } });
    if (path === "/api/overview") return route.fulfill({ json: { collectors: [], connectors: {}, connectors_enabled: {}, scheduler: { enabled: false, active_count: 0 } } });
    if (path === "/api/semantic-workspace/storage") return route.fulfill({ json: { task_count: 0, recycle_bin_count: 0, total_bytes: 0, upload_bytes: 0, delivery_bytes: 0 } });
    if (path === "/api/semantic-workspace/capabilities") return route.fulfill({ json: { enabled: true, items: [] } });
    if (path === "/api/semantic-workspace/context-options") return route.fulfill({ json: { templates: [], memories: [] } });
    if (path === "/api/semantic-workspace/guidance") return route.fulfill({ json: { schema_version: "1", onboarding: [], examples: [] } });
    if (path === "/api/settings/onboarding/model-connections") return route.fulfill({ json: { completed: true } });
    if (route.request().method() !== "GET") return route.fulfill({ status: 405, json: { detail: "合成接口拒绝写入" } });
    return route.fulfill({ json: [] });
  });
}

for (const viewport of [{ width: 667, height: 375 }, { width: 390, height: 500 }]) {
  test(`注册页${viewport.width}x${viewport.height}可滚动到完整提交按钮`, async ({ page }) => {
    await mockApp(page);
    await page.route("**/api/auth/me", route => route.fulfill({ status: 401, json: { detail: "未登录" } }));
    await page.setViewportSize(viewport);
    await page.goto("/login");
    await page.getByRole("button", { name: "注册", exact: true }).click();
    await page.mouse.move(viewport.width / 2, viewport.height / 2);
    await page.mouse.wheel(0, -1000);
    const first = page.getByRole("button", { name: "登录", exact: true });
    await expect.poll(async () => {
      const box = await first.boundingBox();
      return Boolean(box && box.y >= 0 && box.y + box.height <= viewport.height);
    }).toBe(true);
    await page.mouse.wheel(0, 1000);
    const submit = page.getByRole("button", { name: "提交注册", exact: true });
    await expect.poll(async () => {
      const box = await submit.boundingBox();
      return Boolean(box && box.y >= 0 && box.y + box.height <= viewport.height);
    }).toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}

for (const status of [404, 503]) test(`页面资源${status}保留导航，手动重新加载后恢复`, async ({ page }) => {
  await mockApp(page);
  let unavailable = true;
  await page.route(/\/(?:src\/pages\/Tasks\.tsx|assets\/Tasks-[^/]+\.js)(?:\?.*)?$/, route =>
    unavailable ? route.fulfill({ status, body: "合成资源不可用" }) : route.continue());
  await page.goto("/tasks");
  await expect(page.getByRole("heading", { name: "页面加载失败" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "工作空间" })).toBeVisible();
  // 切换页面可离开错误区域，不要求用户刷新整个产品。
  await page.getByRole("link", { name: "记忆", exact: true }).click();
  await expect(page.getByRole("heading", { name: "页面加载失败" })).toBeHidden();
  await page.getByRole("link", { name: "自动化任务", exact: true }).click();
  await expect(page.getByRole("heading", { name: "页面加载失败" })).toBeVisible();
  unavailable = false;
  await page.getByRole("button", { name: "重新加载页面", exact: true }).click();
  await expect(page.getByRole("button", { name: "添加自动化", exact: true }).first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "页面加载失败" })).toBeHidden();
});

const schedule = { task_id: "ux-plan", name: "每日报告", user_input: "汇总合成数据", status: "active", trigger_type: "cron", cron_expr: "0 9 * * *", run_count: 0 };

test("页面渲染异常保留导航且恢复后可手动重新加载", async ({ page }) => {
  await mockApp(page);
  let malformed = true;
  // 错误形状使现有列表渲染抛错，用真实组件验证共享边界，不注入替代页面。
  await page.route("**/api/tasks", route => route.fulfill({ json: malformed ? { unexpected: true } : [] }));
  await page.goto("/tasks");
  await expect(page.getByRole("heading", { name: "页面加载失败" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "工作空间" })).toBeVisible();
  malformed = false;
  await page.getByRole("button", { name: "重新加载页面", exact: true }).click();
  await expect(page.getByRole("button", { name: "添加自动化", exact: true }).first()).toBeVisible();
});

test("离开运营审计后标题随路由更新，后退也保持一致", async ({ page }) => {
  await mockApp(page);
  await page.goto("/operations");
  await expect(page).toHaveTitle("运营总览 — Mangrove");
  await page.getByRole("link", { name: "设置", exact: true }).click();
  await expect(page).toHaveTitle("设置 · Mangrove");
  await page.getByRole("link", { name: "记忆", exact: true }).click();
  await expect(page).toHaveTitle("记忆 · Mangrove");
  await page.goBack();
  await expect(page).toHaveTitle("设置 · Mangrove");
});

test("手机工作台首屏可输入，展开示例仍能追加已有需求", async ({ page }) => {
  await mockApp(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/data-prep");
  const prompt = page.getByRole("textbox", { name: "任务要求", exact: true });
  await expect(prompt).toBeVisible();
  const box = await prompt.boundingBox();
  expect(box && box.y >= 0 && box.y + box.height <= 844).toBe(true);
  await prompt.fill("保留我的要求");
  await page.getByRole("button", { name: "查看任务示例", exact: true }).click();
  await page.getByRole("button", { name: "文件解析与提取", exact: true }).click();
  await page.getByRole("button", { name: "追加到需求", exact: true }).click();
  await expect(prompt).toHaveValue(/保留我的要求\n\n.*报销单/);
  await expect(prompt).toBeFocused();
});

test("运营页退出后不把运营标题留在登录页", async ({ page }) => {
  await mockApp(page);
  await page.route("**/api/auth/logout", route => route.fulfill({ json: { ok: true } }));
  await page.goto("/operations");
  await expect(page).toHaveTitle("运营总览 — Mangrove");
  await page.getByLabel("账号选项", { exact: true }).click();
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page).toHaveURL(/\/login\?returnTo=%2Foperations$/);
  await expect(page).toHaveTitle("Mangrove");
});

test("后台刷新未结束时历史加载按钮明确显示忙状态", async ({ page }) => {
  await mockApp(page);
  const items = Array.from({ length: 101 }, (_, i) => ({ task_id: `busy-${i}`, title: `刷新记录 ${i}`, status: "completed", updated_at: "2026-09-26T00:00:00Z" }));
  let reads = 0;
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/semantic-workspace/tasks?*", async route => {
    if (++reads > 1) await held;
    await route.fulfill({ json: items });
  });
  await page.goto("/data-prep");
  const sidebar = page.getByRole("complementary", { name: "任务列表", exact: true });
  await expect(sidebar.getByRole("button", { name: "加载更早任务" })).toBeEnabled();
  await expect.poll(() => reads).toBe(2);
  await expect(sidebar.getByRole("button", { name: "正在加载…", exact: true })).toBeDisabled();
  release();
  await expect(sidebar.getByRole("button", { name: "加载更早任务" })).toBeEnabled();
});

test("工作台加载第101条历史，筛选覆盖未加载页，回收站独立", async ({ page }) => {
  await mockApp(page);
  const tasks = Array.from({ length: 101 }, (_, i) => ({ task_id: `task-${101 - i}`, title: `历史任务 ${101 - i}`, status: i === 100 ? "needs_input" : "completed", updated_at: "2026-09-26T00:00:00Z" }));
  await page.route("**/api/semantic-workspace/tasks?*", route => {
    const query = new URL(route.request().url()).searchParams;
    let items = query.get("deleted") === "true" ? [{ task_id: "recycled", title: "回收站合成记录", status: "completed", updated_at: "2026-09-26T00:00:00Z" }] : tasks;
    if (query.get("filter") === "needs_input") items = items.filter(item => item.status === "needs_input");
    const offset = Number(query.get("offset") || 0), limit = Number(query.get("limit") || 100);
    return route.fulfill({ json: items.slice(offset, offset + limit) });
  });
  await page.goto("/data-prep");
  const sidebar = page.getByRole("complementary", { name: "任务列表", exact: true });
  await expect(sidebar.getByText("历史任务 1", { exact: true })).toBeHidden();
  await sidebar.getByRole("button", { name: "加载更早任务", exact: true }).click();
  await expect(sidebar.getByText("历史任务 1", { exact: true })).toBeVisible();
  await expect(sidebar.getByRole("button", { name: "加载更早任务" })).toBeHidden();
  await sidebar.getByRole("button", { name: "待确认", exact: true }).click();
  await expect(sidebar.getByText("历史任务 1", { exact: true })).toBeVisible();
  await expect(sidebar.getByText("历史任务 101", { exact: true })).toBeHidden();
  await sidebar.getByRole("button", { name: "回收站", exact: false }).click();
  await expect(sidebar.getByText("回收站合成记录", { exact: true })).toBeVisible();
  await expect(sidebar.getByText("历史任务 1", { exact: true })).toBeHidden();
});

test("历史恰好100条不显示更多，下一页失败保留列表且能重试", async ({ page }) => {
  await mockApp(page);
  const items = Array.from({ length: 101 }, (_, i) => ({ task_id: `page-${i}`, title: `分页记录 ${i}`, status: "completed", updated_at: "2026-09-26T00:00:00Z" }));
  let count = 100, unavailable = true;
  await page.route("**/api/semantic-workspace/tasks?*", route => {
    const query = new URL(route.request().url()).searchParams;
    const offset = Number(query.get("offset") || 0), limit = Number(query.get("limit") || 100);
    if (offset && unavailable) return route.fulfill({ status: 503, json: { detail: "下一页读取失败" } });
    return route.fulfill({ json: items.slice(0, count).slice(offset, offset + limit) });
  });
  await page.goto("/data-prep");
  const sidebar = page.getByRole("complementary", { name: "任务列表", exact: true });
  await expect(sidebar.getByText("分页记录 0", { exact: true })).toBeVisible();
  await expect(sidebar.getByRole("button", { name: "加载更早任务" })).toBeHidden();
  count = 101;
  await page.reload();
  await sidebar.getByRole("button", { name: "加载更早任务", exact: true }).click();
  await expect(sidebar.getByRole("alert")).toBeVisible({ timeout: 15000 });
  await expect(sidebar.getByText("分页记录 0", { exact: true })).toBeVisible();
  unavailable = false;
  await sidebar.getByRole("button", { name: "加载更早任务", exact: true }).click();
  await expect(sidebar.getByText("分页记录 100", { exact: true })).toBeVisible();
  await expect(sidebar.getByRole("alert")).toBeHidden();
});

test("自动化表单可通过标签区分所有频率和起止日期", async ({ page }) => {
  await mockApp(page);
  await page.route("**/api/tasks", route => route.fulfill({ json: [schedule] }));
  await page.goto("/tasks");
  await page.getByRole("button", { name: "编辑", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("名称", { exact: true })).toHaveValue("每日报告");
  await dialog.locator("label").filter({ hasText: /^名称$/ }).click();
  await expect(dialog.getByLabel("名称", { exact: true })).toBeFocused();
  await expect(dialog.getByLabel("提示词", { exact: true })).toHaveValue("汇总合成数据");
  await expect(dialog.getByRole("group", { name: "执行频率", exact: true })).toBeVisible();
  await expect(dialog.getByLabel("执行时间", { exact: true })).toBeVisible();
  await expect(dialog.getByLabel("执行时间", { exact: true })).toHaveAccessibleDescription(/UTC\+8/);
  await dialog.getByLabel("开始日期", { exact: true }).fill("2026-10-01");
  await dialog.getByLabel("结束日期", { exact: true }).fill("2026-10-31");
  await dialog.getByRole("button", { name: "每月", exact: true }).click();
  await expect(dialog.getByLabel("每月执行日期")).toBeVisible();
  await dialog.getByRole("button", { name: "高级", exact: true }).click();
  await expect(dialog.getByLabel("Cron 表达式")).toBeVisible();
  await dialog.getByRole("button", { name: "按间隔", exact: true }).click();
  await expect(dialog.getByLabel("间隔数值")).toBeVisible();
  await expect(dialog.getByLabel("间隔单位")).toBeVisible();
  await dialog.getByRole("button", { name: "单次", exact: true }).click();
  await expect(dialog.getByLabel("单次执行时间")).toBeVisible();
  await expect(dialog.getByLabel("单次执行时间")).toHaveAccessibleDescription(/UTC\+8/);
});

test("键盘可选择空白和模板创建，关闭后返回添加入口", async ({ page }) => {
  await mockApp(page);
  await page.route("**/api/tasks/templates", route => route.fulfill({ json: [{ id: "daily", name: "日报模板", description: "合成示例", prompt: "汇总今天数据", trigger_type: "cron", cron_expr: "0 9 * * *" }] }));
  await page.goto("/tasks");
  const add = page.getByRole("button", { name: "添加自动化", exact: true }).first();
  await add.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: /空白创建/ })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("执行模型")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(add).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("button", { name: /空白创建/ })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: /日报模板/ })).toBeFocused();
  await page.keyboard.press("Space");
  await expect(page.getByPlaceholder("给这个自动化任务起个名字")).toHaveValue("日报模板");
});

test("删除计划必须确认，取消不发送请求，失败保留计划并可重试", async ({ page }) => {
  await mockApp(page);
  let deletes = 0, deleted = false;
  await page.route("**/api/tasks", route => route.fulfill({ json: deleted ? [] : [schedule] }));
  await page.route("**/api/tasks/ux-plan", route => {
    deletes++;
    deleted = deletes > 1;
    return deleted ? route.fulfill({ json: { ok: true } }) : route.fulfill({ status: 503, json: { detail: "取消未完成，请重试" } });
  });
  await page.goto("/tasks");
  await page.getByRole("button", { name: "删除任务", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "删除自动化计划？" });
  await expect(dialog).toContainText("每日报告");
  await expect(dialog).toContainText("已有执行记录保留");
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  expect(deletes).toBe(0);
  await page.getByRole("button", { name: "删除任务", exact: true }).click();
  await dialog.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(page.getByText("取消未完成，请重试")).toBeVisible();
  await expect(dialog).toBeVisible();
  expect(deletes).toBe(1);
  await dialog.getByRole("button", { name: "确认删除", exact: true }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByText("每日报告", { exact: true })).toBeHidden();
  expect(deletes).toBe(2);
});

test("自动化列表区分读取失败与空数据，刷新失败保留已有计划", async ({ page }) => {
  await mockApp(page);
  let failed = true;
  await page.route("**/api/tasks", route => failed
    ? route.fulfill({ status: 503, json: { detail: "合成读取失败" } })
    : route.fulfill({ json: [schedule] }));
  await page.goto("/tasks");
  await expect(page.getByRole("alert")).toContainText("任务加载失败");
  await expect(page.getByText("暂无进行中的定时任务。", { exact: false })).toBeHidden();
  failed = false;
  await page.getByRole("button", { name: "重试读取任务" }).click();
  await expect(page.getByText("每日报告", { exact: true })).toBeVisible();
  failed = true;
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("上次读取");
  await expect(page.getByText("每日报告", { exact: true })).toBeVisible();
});

test("工作台分别提示任务和采集历史失败，重试成功才显示真实空状态", async ({ page }) => {
  await mockApp(page);
  let failed = true;
  await page.route(/\/api\/(semantic-workspace\/tasks(?:\?.*)?|chat\/history)$/, route => failed
    ? route.fulfill({ status: 503, json: { detail: "合成读取失败" } })
    : route.fulfill({ json: [] }));
  await page.goto("/data-prep");
  const toggle = page.getByRole("button", { name: "展开任务列表", exact: true });
  if (await toggle.isVisible()) await toggle.click();
  const sidebar = page.getByRole("complementary", { name: "任务列表", exact: true });
  await expect(sidebar.getByRole("alert")).toContainText("任务列表、采集历史读取失败");
  await expect(sidebar.getByText("当前筛选下没有任务")).toBeHidden();
  failed = false;
  await sidebar.getByRole("button", { name: "重试读取列表" }).click();
  await expect(sidebar.getByText("当前筛选下没有任务")).toBeVisible();
  await expect(sidebar.getByRole("alert")).toBeHidden();
});

test("诊断读取失败显示未知并可分别重试", async ({ page }) => {
  await mockApp(page);
  let failed = true;
  await page.route("**/api/overview", route => failed
    ? route.fulfill({ status: 503, json: { detail: "读取失败" } })
    : route.fulfill({ json: { collectors: [], connectors: { embedding: true }, connectors_enabled: { embedding: true } } }));
  await page.route("**/api/config/domain-health", route => failed
    ? route.fulfill({ status: 503, json: { detail: "读取失败" } })
    : route.fulfill({ json: { flagged: {} } }));
  await page.goto("/settings?section=diagnostics");
  await expect(page.getByText("配置状态读取失败，请重试。")).toBeVisible();
  await expect(page.getByText("域名状态读取失败，请重试。")).toBeVisible();
  await expect(page.getByText("已停用", { exact: true })).toBeHidden();
  await expect(page.getByText("未配", { exact: true })).toBeHidden();
  await expect(page.getByText("当前没有被短路的域名")).toBeHidden();
  failed = false;
  await page.getByRole("button", { name: "重试读取配置" }).click();
  await page.getByRole("button", { name: "重试读取域名状态" }).click();
  await expect(page.getByText("已启用", { exact: true })).toBeVisible();
  await expect(page.getByText("当前没有被短路的域名")).toBeVisible();
});
