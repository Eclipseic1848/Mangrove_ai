import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

async function fixture(page: Page) {
  const state = { role: "user", id: "guide-owner", writes: 0, errors: [] as string[] };
  page.on("pageerror", error => state.errors.push(error.message));
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: { user_id: state.id, username: state.id, display_name: "合成用户", role: state.role } });
    if (path === "/api/operations/tokens/query") return route.fulfill({ json: { items: [], total: 0, page: 1, summary: { users: 0, requests: 0, total_tokens: 0, cost: null, priced_requests: 0, unknown_requests: 0 }, pricing: { version: "test", notice: "参考价格", models: [] } } });
    if (path.startsWith("/api/operations/")) return route.fulfill({ status: 503, json: { detail: "合成空态" } });
    if (route.request().method() !== "GET") { state.writes++; return route.fulfill({ json: {} }); }
    if (path === "/api/memory") return route.fulfill({ json: { personal: [], total: 0, page: 1, preferences: "报告注明来源。", preferences_digest: "a".repeat(64) } });
    if (path === "/api/feedback/overview") return route.fulfill({ json: { total_tasks: 0, total_up: 0, total_down: 0, total_pending: 0, reason_counts: {} } });
    if (path === "/api/tasks/runs/recent") return route.fulfill({ json: { items: [], total: 0 } });
    if (path === "/api/overview") return route.fulfill({ json: { collectors: [], connectors: {}, connectors_enabled: {}, scheduler: { enabled: false, active_count: 0 } } });
    if (path === "/api/models") return route.fulfill({ json: { options: [], available: [], default: null, document_default: null } });
    if (path === "/api/semantic-workspace/guidance") return route.fulfill({ json: { onboarding: [], examples: [] } });
    if (path === "/api/semantic-workspace/capabilities") return route.fulfill({ json: { enabled: true, items: [] } });
    if (path === "/api/semantic-workspace/context-options") return route.fulfill({ json: { templates: [], memories: [] } });
    if (path === "/api/semantic-workspace/storage") return route.fulfill({ json: { task_count: 0, total_bytes: 0 } });
    if (path === "/api/model-connections/presets") return route.fulfill({ json: { presets: [], items: [] } });
    if (path === "/api/model-connections") return route.fulfill({ json: { items: [] } });
    if (path === "/api/overview/activity") return route.fulfill({ json: { items: [], total: 0, stats: { active: 0, attention: 0, completed: 0 } } });
    if (path === "/api/overview/services") return route.fulfill({ status: 503, json: { detail: "合成不可用状态" } });
    return route.fulfill({ json: [] });
  });
  return state;
}

test("首次进入引导，完成后刷新不打扰且可完整重播", async ({ page }) => {
  const state = await fixture(page);
  await page.goto("/memory");
  const guide = page.getByRole("dialog", { name: "新手教程" });
  await expect(guide).toBeVisible();
  await expect(guide).toContainText("记住你的常用偏好");
  for (let i = 0; i < 10 && await guide.isVisible(); i++) {
    const finish = guide.getByRole("button", { name: "完成", exact: true });
    if (await finish.isVisible()) await finish.click();
    else await guide.getByRole("button", { name: "下一步", exact: true }).click();
  }
  await expect(guide).toBeHidden();
  await page.reload();
  await expect(page.getByRole("button", { name: "新手教程", exact: true })).toBeVisible();
  await page.waitForTimeout(900);
  await expect(guide).toBeHidden();
  await page.getByRole("button", { name: "新手教程", exact: true }).click();
  await expect(guide).toBeVisible();
  await expect(guide).toContainText("1 /");
  expect(state.errors).toEqual([]);
});

test("键盘、遮罩、焦点恢复和移动端深色无障碍", async ({ page }, testInfo) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/memory");
  await page.evaluate(() => document.documentElement.classList.add("dark"));
  const guide = page.getByRole("dialog", { name: "新手教程" });
  await expect(guide).toBeVisible();
  await page.keyboard.press("ArrowRight");
  await expect(guide).toContainText("添加一条偏好");
  await expect(guide).toContainText("2 /");
  await page.keyboard.press("ArrowLeft");
  await expect(guide).toContainText("1 /");
  await page.mouse.click(8, 400);
  await expect(guide).toBeVisible();
  for (let i = 0; i < 8; i++) {
    await page.keyboard.press("Tab");
    expect(await guide.evaluate(element => element.contains(document.activeElement))).toBe(true);
  }
  const box = (await guide.boundingBox())!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390);
  expect(box.y + box.height).toBeLessThanOrEqual(844);
  expect((await new AxeBuilder({ page }).include('.page-guide-tooltip').analyze()).violations).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("guide-mobile-dark.png") });
  await page.keyboard.press("Escape");
  await expect(guide).toBeHidden();
  await expect(page.getByRole("button", { name: "新手教程", exact: true })).toBeFocused();
  await page.reload(); await page.waitForTimeout(900); await expect(guide).toBeHidden();
  expect(state.writes).toBe(0);
  expect(state.errors).toEqual([]);
});

test("页面独立、跳过全部不妨碍重播，用户和角色进度互不干扰", async ({ page }) => {
  const state = await fixture(page);
  await page.goto("/memory");
  const guide = page.getByRole("dialog", { name: "新手教程" });
  await expect(guide).toBeVisible();
  await guide.getByRole("button", { name: "跳过本页", exact: true }).click();
  await page.getByRole("tab", { name: "全局记忆", exact: true }).click();
  await expect(guide).toContainText("大家共同参考的偏好");
  await expect(guide).toContainText("1 / 2");
  await guide.getByRole("button", { name: "跳过全部引导", exact: true }).click();
  await page.getByRole("link", { name: "模板库", exact: true }).click();
  await page.waitForTimeout(900); await expect(guide).toBeHidden();
  await page.getByRole("button", { name: "新手教程", exact: true }).click();
  await expect(guide).toBeVisible();
  await page.keyboard.press("Escape");
  state.role = "admin";
  await page.goto("/memory"); await expect(guide).toBeVisible();
  await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "全局记忆", exact: true }).click();
  await expect(guide).toContainText("1 / 3");
  await guide.getByRole("button", { name: "下一步", exact: true }).click();
  await expect(guide).toContainText("谨慎添加全局偏好");
  state.id = "second-owner";
  await page.goto("/memory"); await expect(guide).toContainText("记住你的常用偏好");
  expect(state.errors).toEqual([]);
});

test("存储拒绝可继续引导并明确提示，单页会话不反复弹出", async ({ page }) => {
  await fixture(page);
  await page.addInitScript(() => {
    const original = Storage.prototype.setItem;
    Storage.prototype.setItem = function(key, value) {
      if (key.startsWith("onboarding_")) throw new DOMException("denied", "SecurityError");
      return original.call(this, key, value);
    };
  });
  await page.goto("/memory");
  const guide = page.getByRole("dialog", { name: "新手教程" });
  await expect(guide).toContainText("浏览器未允许保存进度");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("status").filter({ hasText: "进度仅在当前页面会话保留" })).toBeVisible();
  await page.getByRole("tab", { name: "全局记忆", exact: true }).click();
  await expect(guide).toBeVisible(); await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "我的记忆", exact: true }).click();
  await page.waitForTimeout(900); await expect(guide).toBeHidden();
});

test("普通用户不获得管理页面或管理员步骤引导", async ({ page }) => {
  await fixture(page);
  await page.goto("/operations");
  await expect(page.getByRole("heading", { name: "无权访问" })).toBeVisible();
  await expect(page.getByRole("button", { name: "新手教程", exact: true })).toHaveCount(0);
  await page.goto("/templates");
  const guide = page.getByRole("dialog", { name: "新手教程" });
  await expect(guide).toBeVisible(); await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "巡检报告", exact: true })).toHaveCount(0);
  await page.goto("/settings?section=platform");
  await expect(guide).toContainText("管理自己的使用习惯");
  await expect(page.getByRole("button", { name: "平台配置", exact: true })).toHaveCount(0);
});

test("慢速用量请求完成后才开始，完整覆盖模型明细步骤", async ({ page }) => {
  const state = await fixture(page); state.role = "admin";
  let release!: () => void;
  const waiting = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/operations/tokens/query", async route => {
    await waiting;
    await route.fulfill({ json: { items: [], total: 0, page: 1, summary: { users: 0, requests: 0, total_tokens: 0, cost: null, priced_requests: 0, unknown_requests: 0 }, pricing: { version: "test", notice: "参考价格", models: [] } } });
  });
  await page.goto("/operations?tab=tokens");
  const guide = page.getByRole("dialog", { name: "新手教程" });
  await page.waitForTimeout(1500);
  await expect(guide).toBeHidden();
  release();
  await expect(guide).toBeVisible(); await expect(guide).toContainText("1 / 5");
  for (let i = 0; i < 4; i++) {
    await guide.getByRole("button", { name: "下一步", exact: true }).click();
    await expect(guide).toContainText(`${i + 2} / 5`);
  }
  await expect(guide).toContainText("展开模型明细与导出");
  await guide.getByRole("button", { name: "完成", exact: true }).click();
  expect(state.errors).toEqual([]);
});

test("首次用量加载失败不误记完成，恢复后完整自动引导", async ({ page }) => {
  const state = await fixture(page); state.role = "admin";
  const fail = (route: import("@playwright/test").Route) => route.fulfill({ status: 503, json: { detail: "合成加载失败" } });
  await page.route("**/api/operations/tokens/query", fail);
  await page.goto("/operations?tab=tokens");
  await expect(page.getByRole("alert")).toContainText("统计暂不可用");
  await page.waitForTimeout(900);
  const guide = page.getByRole("dialog", { name: "新手教程" });
  await expect(guide).toBeHidden();
  await page.getByRole("button", { name: "新手教程", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("页面内容尚未就绪");
  expect(await page.evaluate(() => localStorage.getItem('onboarding_v1_["guide-owner","admin","operations.tokens"]'))).toBeNull();
  await page.unroute("**/api/operations/tokens/query", fail);
  await page.reload();
  await expect(guide).toContainText("1 / 5");
  expect(state.errors).toEqual([]);
});

test("输入时不抢焦点，停止输入后继续首次引导", async ({ page }) => {
  await fixture(page);
  await page.goto("/memory");
  const input = page.getByRole("textbox", { name: "添加个人记忆", exact: true });
  await input.focus();
  await page.waitForTimeout(900);
  const guide = page.getByRole("dialog", { name: "新手教程" });
  await expect(guide).toBeHidden();
  await input.blur();
  await expect(guide).toBeVisible();
});

test("引导遇任务列表弹窗延后，不产生两个焦点陷阱", async ({ page }) => {
  const state = await fixture(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/data-prep");
  await page.locator("#task-list-toggle").click();
  await expect(page.getByRole("dialog", { name: "任务列表", exact: true })).toBeVisible();
  await page.waitForTimeout(900);
  const guide = page.getByRole("dialog", { name: "新手教程" });
  await expect(guide).toBeHidden();
  await page.keyboard.press("Escape");
  await expect(guide).toBeVisible();
  // 模拟后台状态打开既有弹窗；教程应让位而不是覆盖它。
  await page.locator("#task-list-toggle").evaluate((element: HTMLButtonElement) => element.click());
  await expect(page.getByRole("dialog", { name: "任务列表", exact: true })).toBeVisible();
  await expect(guide).toBeHidden();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "新手教程", exact: true }).click();
  await expect(guide).toBeVisible();
  expect(state.errors).toEqual([]);
});

test("工作台移除旧帮助，教程与任务示例仍可使用", async ({ page }) => {
  const state = await fixture(page);
  await page.goto("/data-prep");
  const guide = page.getByRole("dialog", { name: "新手教程" });
  await expect(guide).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "帮助", exact: true })).toHaveCount(0);
  const examples = page.getByRole("group", { name: "任务方向", exact: true });
  await expect(examples.getByRole("button")).toHaveCount(4);
  await examples.getByRole("button", { name: "信息采集与分析", exact: true }).click();
  await page.getByRole("button", { name: "站内检索", exact: true }).click();
  await page.getByRole("button", { name: "填入需求", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "任务要求", exact: true })).toHaveValue(/懂车帝/);
  await page.getByRole("button", { name: "新手教程", exact: true }).click();
  await expect(guide).toBeVisible();
  expect(state.writes).toBe(0);
  expect(state.errors).toEqual([]);
});

test("同页权限降级立即终止旧角色引导，身份改变不串进度", async ({ page }) => {
  const state = await fixture(page); state.role = "super_admin";
  await page.goto("/memory");
  const guide = page.getByRole("dialog", { name: "新手教程" });
  await expect(guide).toBeVisible(); await page.keyboard.press("Escape");
  await page.getByRole("tab", { name: "全局记忆", exact: true }).click();
  await expect(guide).toContainText("1 / 3");
  state.role = "user";
  await page.evaluate(() => { const channel = new BroadcastChannel("mangrove-platform-session"); channel.postMessage("identity-changed"); channel.close(); });
  await expect(page.getByRole("button", { name: "编辑全局记忆", exact: true })).toHaveCount(0);
  await expect(guide).toContainText("1 / 2");
  await page.keyboard.press("Escape");
  state.id = "another-owner";
  await page.evaluate(() => { const channel = new BroadcastChannel("mangrove-platform-session"); channel.postMessage("identity-changed"); channel.close(); });
  await expect(guide).toBeVisible();
  await expect(guide).toContainText("1 / 3");
  expect(state.errors).toEqual([]);
});

test("同一模块各子功能首次独立播放，普通管理员无越级说明", async ({ page }) => {
  const state = await fixture(page); state.role = "admin";
  const guide = page.getByRole("dialog", { name: "新手教程" });
  await page.goto("/templates"); await expect(guide).toBeVisible(); await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "教训库", exact: true }).click();
  await expect(guide).toContainText("让同类问题少发生"); await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "巡检报告", exact: true }).click();
  await expect(guide).toContainText("了解经验库维护情况"); await page.keyboard.press("Escape");
  await page.goto("/tasks"); await expect(guide).toBeVisible(); await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "运行记录", exact: true }).click();
  await expect(guide).toContainText("每次执行都有记录"); await page.keyboard.press("Escape");
  await page.goto("/admin"); await expect(guide).toContainText("1 / 3");
  expect(state.errors).toEqual([]);
});

test("反馈教程每步、滚动与缩放窗口后均完整留在可视区", async ({ page }, testInfo) => {
  const state = await fixture(page); state.role = "admin";
  await page.route("**/api/feedback/list?*", route => route.fulfill({ json: {
    total: 10, items: Array.from({ length: 10 }, (_, index) => ({
      id: index + 1, user_id: "guide-owner", username: "合成用户", rating: "down", reasons: ["其他"], status: "pending",
      created_at: "2026-09-20T10:00:00+08:00", content_available: true, has_comment: true, has_admin_note: false,
    })),
  } }));
  const guide = page.getByRole("dialog", { name: "新手教程" });
  const inside = async () => {
    await expect(async () => {
      const box = (await guide.boundingBox())!, viewport = page.viewportSize()!;
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.y).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
      expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
      await expect(guide.getByRole("button", { name: "关闭引导", exact: true })).toBeInViewport();
      await expect(guide.getByRole("button", { name: "跳过全部引导", exact: true })).toBeInViewport();
    }).toPass({ timeout: 3000 });
  };
  for (const [width, height] of [[1440, 900], [2554, 1200], [390, 844]]) {
    await page.setViewportSize({ width, height });
    await page.goto("/feedback");
    await expect(page.getByRole("heading", { name: "反馈管理", exact: true })).toBeVisible();
    if (width !== 1440) await page.getByRole("button", { name: "新手教程", exact: true }).click();
    for (let index = 0; index < 3; index++) {
      await expect(guide).toContainText(`${index + 1} / 3`);
      await inside();
      await expect(guide).toHaveCSS("opacity", "1");
      await page.screenshot({ path: testInfo.outputPath(`feedback-guide-${width}-step${index + 1}.png`) });
      if (index < 2) await guide.getByRole("button", { name: "下一步", exact: true }).click();
    }
    await page.locator('[data-guide="feedback-list"]').evaluate(element => { element.scrollTop = element.scrollHeight; });
    await inside();
    await page.setViewportSize({ width, height: 600 });
    await inside();
    await page.keyboard.press("Escape");
    await expect(guide).toBeHidden();
  }
  expect(state.writes).toBe(0);
  expect(state.errors).toEqual([]);
});

test("设置教程与刷新在右侧相邻，窄屏不溢出", async ({ page }, testInfo) => {
  const state = await fixture(page); state.role = "super_admin";
  await page.goto("/settings?section=diagnostics");
  await expect(page.getByRole("dialog", { name: "新手教程" })).toBeVisible();
  await page.keyboard.press("Escape");
  const header = page.locator("main header"), replay = header.getByRole("button", { name: "新手教程", exact: true }), refresh = header.getByRole("button", { name: "刷新", exact: true });
  for (const [width, height] of [[1440, 900], [2554, 1200], [390, 844]]) {
    await page.setViewportSize({ width, height });
    await expect(async () => {
      const a = (await replay.boundingBox())!, b = (await refresh.boundingBox())!;
      expect(Math.abs(a.y - b.y)).toBeLessThanOrEqual(1);
      expect(b.x - (a.x + a.width)).toBeGreaterThanOrEqual(0);
      expect(b.x - (a.x + a.width)).toBeLessThanOrEqual(12);
      expect(b.x + b.width).toBeLessThanOrEqual(width);
      expect(a.x).toBeGreaterThanOrEqual(0);
    }).toPass({ timeout: 2000 });
    await page.screenshot({ path: testInfo.outputPath(`settings-guide-entry-${width}.png`) });
  }
  expect(state.writes).toBe(0);
  expect(state.errors).toEqual([]);
});

test("已有任务完整引导不发送追问、不停止运行", async ({ page }) => {
  const state = await fixture(page);
  const task = {
    task_id: "guide-task", title: "合成执行任务", objective_text: "整理合成资料", status: "running",
    upload_ids: [], output_formats: ["json"], provider: "local", model: "test", runtime_version: "legacy",
    active_revision: 1, current_revision: 1, viewing_revision: 1, summary: "已理解要求",
    plan_id: null, run_id: null, error: null, question: null, cancel_requested: false,
    created_at: "2026-09-20T10:00:00+08:00", updated_at: "2026-09-20T10:00:00+08:00",
  };
  await page.route("**/api/semantic-workspace/tasks?*", route => route.fulfill({ json: [task] }));
  await page.route("**/api/semantic-workspace/tasks/guide-task/turns", route => route.fulfill({ json: { turns: [], results: [], proposals: [] } }));
  await page.route("**/api/semantic-workspace/tasks/guide-task/stream*", route => route.fulfill({ contentType: "text/event-stream", body: "" }));
  await page.route("**/api/semantic-workspace/tasks/guide-task/draft?*", route => route.fulfill({ json: { draft: null } }));
  await page.route(/\/api\/semantic-workspace\/tasks\/guide-task(?:\?.*)?$/, route => route.fulfill({ json: {
    ...task, revisions: [{ ...task, revision: 1 }], events: [], uploads: [], plan: null, run: null, attempts: [], harness_events: [], delivery: null,
  } }));
  await page.goto("/data-prep?task=guide-task");
  const guide = page.getByRole("dialog", { name: "新手教程" });
  await expect(guide).toContainText("1 / 4");
  for (let i = 0; i < 3; i++) {
    await guide.getByRole("button", { name: "下一步", exact: true }).click();
    await expect(guide).toContainText(`${i + 2} / 4`);
  }
  await guide.getByRole("button", { name: "完成", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "继续对话", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "停止", exact: true })).toBeVisible();
  expect(state.writes).toBe(0);
  expect(state.errors).toEqual([]);
});

for (const path of ["/", "/data-prep", "/tasks", "/templates", "/memory", "/settings?section=personal", "/settings?section=models", "/settings?section=credentials", "/settings?section=platform", "/settings?section=governance", "/settings?section=diagnostics", "/feedback", "/admin", ...["overview", "login", "visit", "audit", "users", "tokens"].map(tab => `/operations?tab=${tab}`)]) {
  test(`管理员页面 ${path} 首次引导及重播入口`, async ({ page }, testInfo) => {
    const state = await fixture(page); state.role = "super_admin";
    await page.goto(path);
    const guide = page.getByRole("dialog", { name: "新手教程" });
    await expect(guide).toBeVisible();
    await expect(page.locator("vite-error-overlay")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "新手教程", exact: true })).toBeVisible();
    if (path === "/data-prep") await page.screenshot({ path: testInfo.outputPath("guide-workspace-desktop.png") });
    await page.keyboard.press("Escape");
    expect(state.errors).toEqual([]);
  });
}
