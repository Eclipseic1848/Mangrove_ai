import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

async function mockOperations(page: Page, role = "super_admin", dense = false) {
  const entry = { event_id: "00000000000040008000000000000001", occurred_at: "2026-09-17T02:00:00Z", actor_id: "member", actor_name: "合成用户", kind: "login", module: "登录", action: "密码登录", object_ref: "模拟对象", result: "success", ip_mask: "192.168.1.0/24", device: "Chrome / Windows", source: "direct", changes: [] };
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: { user_id: "operator", username: "operator", display_name: "审计员", role } });
    if (path.endsWith("/options")) return route.fulfill({ json: { users: [{ user_id: "member", name: "合成用户" }], modules: ["登录", "任务工作台", "用户管理"], policy: { retention_days: 180, version: 1 } } });
    if (path.endsWith("/views")) return route.fulfill({ json: { items: [] } });
    if (path.endsWith("/summary")) return route.fulfill({ json: { activity_distribution: { high: 2, active: 6, unseen: 1, threshold: 10 }, pv: 26, uv: 8, pv_per_user: 3.25, login_success: 3, login_failure: 1, action_failure: 1, actions: 2, active_users: { day: 2, week: 2, month: 2 }, trend: dense ? [5, 4, 4, 4, 3, 3, 3].map((pv, i) => ({ bucket: `2026-09-${11 + i}`, pv, uv: Math.min(pv, 3), logins: 3, failures: 1 })) : [{ bucket: "2026-09-17", pv: 8, uv: 2, logins: 3, failures: 1 }], modules: dense ? ["任务工作台", "概览", "自动化任务", "文件与交付", "模板库", "用户管理", "设置", "记忆"].map((module, i) => ({ module, pv: 16 - i, uv: 3, actions: 6 })) : [{ module: "任务工作台", pv: 8, uv: 2, actions: 2 }], sources: [{ source: "direct", pv: 8 }], users: dense ? Array.from({ length: 23 }, (_, i) => ({ actor_id: `member-${i}`, actor_name: `合成用户${i + 1}`, pv: 30 - i, actions: 2, logins: 3, last_active: 1789600800 })) : [{ actor_id: "member", actor_name: "合成用户", pv: 8, actions: 2, logins: 3, last_active: 1789600800 }], sessions: { online_users: 1, count: 2, average_seconds: 60 }, coverage: { started_at: "2026-09-17T00:00:00Z", retention_days: 180, timezone: "Asia/Shanghai" } } });
    if (path.endsWith("/events/query")) {
      const filters = route.request().postDataJSON();
      const total = dense ? 53 : 1, pageSize = filters.page_size || 20;
      const current = Math.min(filters.page || 1, Math.ceil(total / pageSize));
      const items = dense ? Array.from({ length: Math.min(pageSize, total - (current - 1) * pageSize) }, (_, i) => ({ ...entry, event_id: `event-${(current - 1) * pageSize + i + 1}`, actor_name: `合成用户${i + 1}`, kind: filters.kind || "action", module: filters.kind === "login" ? "登录" : "任务工作台", action: filters.kind === "login" ? "密码登录" : `模拟操作${(current - 1) * pageSize + i + 1}` })) : [entry];
      return route.fulfill({ json: { items, total, page: current, page_size: pageSize } });
    }
    if (path.includes("/events/")) return route.fulfill({ json: entry });
    return route.fulfill({ json: { ok: true } });
  });
}

test("页眉字号间距与记忆模块一致，正文滚动时页眉固定", async ({ page }, testInfo) => {
  await mockOperations(page, "super_admin", true);
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 700 });
    await page.goto("/memory");
    await expect(page.getByRole("heading", { name: "记忆", exact: true })).toBeVisible();
    const measure = () => page.locator("main header").evaluate(el => {
      const title = getComputedStyle(el.querySelector("h1")!), subtitle = getComputedStyle(el.querySelector("p")!), style = getComputedStyle(el);
      return { height: el.getBoundingClientRect().height, padding: style.padding, font: title.fontFamily, size: title.fontSize, weight: title.fontWeight, spacing: title.letterSpacing, line: title.lineHeight, subtitle: subtitle.fontSize, subtitleLine: subtitle.lineHeight };
    });
    const expected = await measure();
    await page.goto("/operations?tab=overview");
    await expect(page.getByRole("heading", { name: "运营审计", exact: true })).toBeVisible();
    await expect.poll(measure).toEqual(expected);
    const header = page.locator(".ops-page > header");
    const before = await header.boundingBox();
    await page.locator(".ops-workspace").evaluate(el => { el.scrollTop = el.scrollHeight; });
    expect(await page.locator(".ops-workspace").evaluate(el => el.scrollTop)).toBeGreaterThan(0);
    expect(await header.boundingBox()).toEqual(before);
    expect(await page.evaluate(() => scrollY === 0 && document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.evaluate(() => document.documentElement.classList.add("dark"));
    await page.screenshot({ path: testInfo.outputPath(`header-frozen-${width}.png`) });
  }
  expect(errors).toEqual([]);
});

test("日志分页可选条数、翻页与筛选复位，详情保留右侧抽屉", async ({ page }) => {
  await mockOperations(page);
  const requests: { page: number; page_size: number }[] = [];
  await page.route("**/api/operations/events/query", route => {
    const filters = route.request().postDataJSON();
    requests.push(filters);
    const total = filters.result ? 2 : 53;
    const current = Math.min(filters.page, Math.ceil(total / filters.page_size));
    const items = Array.from({ length: Math.min(filters.page_size, total - (current - 1) * filters.page_size) }, (_, i) => ({
      event_id: `event-${(current - 1) * filters.page_size + i + 1}`, occurred_at: "2026-09-17T02:00:00Z", actor_id: "member", actor_name: `用户${(current - 1) * filters.page_size + i + 1}`, kind: "action", module: "任务工作台", action: "创建任务", result: "success", ip_mask: "192.168.1.0/24", device: "Chrome", changes: [],
    }));
    return route.fulfill({ json: { items, total, page: current, page_size: filters.page_size } });
  });
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/operations?tab=audit");
  const pager = page.getByRole("navigation", { name: "日志分页", exact: true });
  await expect(pager.getByLabel("每页条数")).toHaveValue("10");
  await expect(pager.getByLabel("每页条数").locator("option")).toHaveText(["10", "20", "50", "100"]);
  await expect(page.getByRole("table").locator("tbody tr")).toHaveCount(10);
  await pager.getByRole("button", { name: "下一页" }).click();
  await expect(page.getByRole("button", { name: "用户11", exact: true })).toBeVisible();
  await expect(page).toHaveURL(/page=2/);
  await pager.getByLabel("每页条数").selectOption("100");
  await expect(page.getByRole("table").locator("tbody tr")).toHaveCount(53);
  await expect.poll(() => requests.at(-1)?.page).toBe(1);
  await page.getByLabel("结果", { exact: true }).selectOption("failure");
  await expect(page.getByRole("table").locator("tbody tr")).toHaveCount(2);
  await expect(pager.getByRole("button", { name: "上一页" })).toBeDisabled();
  await expect.poll(() => requests.at(-1)?.page).toBe(1);
  await page.getByRole("button", { name: "查看详情", exact: true }).first().click();
  const drawer = await page.getByRole("dialog").boundingBox();
  expect(drawer!.x + drawer!.width).toBeCloseTo(1440, 0);
  expect(drawer!.y).toBe(0);
});

test("运营审计所有数据列表统一条数选项", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await mockOperations(page, "super_admin", true);
  await page.route("**/api/operations/views", route => route.fulfill({ json: { items: Array.from({ length: 12 }, (_, i) => ({ view_id: `view-${i}`, name: `合成视图${i + 1}` })) } }));
  const check = async (name: string) => {
    const pager = page.getByRole("navigation", { name, exact: true });
    await expect(pager.getByLabel("每页条数")).toHaveValue("10");
    await expect(pager.getByLabel("每页条数").locator("option")).toHaveText(["10", "20", "50", "100"]);
    await pager.getByLabel("每页条数").selectOption("100");
    await expect(pager.getByLabel("每页条数")).toHaveValue("100");
  };
  for (const tab of ["overview", "login", "visit", "audit"]) {
    await page.goto(`/operations?tab=${tab}`);
    await check("日志分页");
    await expect(page.getByRole("table").locator("tbody tr")).toHaveCount(53);
  }
  await page.goto("/operations?tab=users");
  await expect(page.getByRole("group", { name: "功能热度矩阵" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "功能排行分页" })).toHaveCount(0);
  await check("用户明细分页");
  await expect(page.getByRole("table").locator("tbody tr")).toHaveCount(23);
  await page.getByRole("button", { name: "合成用户1", exact: true }).click();
  await check("时间线分页");
  await expect(page.getByRole("dialog").locator("ol > li")).toHaveCount(53);
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "管理视图", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("button", { name: /^删除视图/ })).toHaveCount(10);
  await check("常用视图分页");
  await expect(page.getByRole("dialog").getByRole("button", { name: /^删除视图/ })).toHaveCount(12);
  await page.keyboard.press("Escape");
  await page.goto("/operations?tab=audit");
  await page.setViewportSize({ width: 390, height: 844 });
  const pager = page.getByRole("navigation", { name: "日志分页", exact: true });
  await pager.scrollIntoViewIfNeeded();
  await expect(pager.getByLabel("每页条数")).toHaveValue("10");
  await page.screenshot({ path: testInfo.outputPath("pagination-mobile.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});

test("功能热度矩阵支持键盘提示、下钻及深浅窄屏", async ({ page }, testInfo) => {
  await mockOperations(page, "super_admin", true);
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/operations?tab=visit");
  const matrix = page.getByRole("group", { name: "功能热度矩阵" });
  await expect(matrix.getByRole("button")).toHaveCount(8);
  const boxes = await matrix.getByRole("button").evaluateAll(items => items.map(item => { const box = item.getBoundingClientRect(); return { height: box.height, top: box.top }; }));
  expect(new Set(boxes.map(box => box.height)).size).toBe(1);
  expect(boxes.slice(0, 3).every(box => box.top === boxes[0].top)).toBe(true);
  const tile = matrix.getByRole("button", { name: /任务工作台.*16 次/ });
  await tile.focus();
  await expect(page.getByRole("tooltip")).toContainText("访问用户：3 人");
  await page.keyboard.press("Escape");
  await page.screenshot({ path: testInfo.outputPath("heatmap-light.png") });
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("模块", { exact: true })).toHaveValue("任务工作台");
  await page.getByRole("button", { name: "深色主题", exact: true }).click();
  await expect(matrix).toBeVisible();
  expect((await new AxeBuilder({ page }).include('[data-operations-page]').analyze()).violations).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("heatmap-dark.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await matrix.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("heatmap-mobile.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect(errors).toEqual([]);
});

test("热度矩阵保留零值与各档数字，空数据不伪造热度", async ({ page }) => {
  await mockOperations(page);
  let empty = false;
  await page.route("**/api/operations/summary", async route => {
    await route.fulfill({ json: { pv: 201, uv: 1, pv_per_user: 201, login_success: 0, login_failure: 0, action_failure: 0, actions: 0, active_users: {}, trend: [], sources: [], users: [], sessions: { online_users: 0, count: 0, average_seconds: 0 }, coverage: { started_at: "2026-09-17T00:00:00Z", retention_days: 180 }, modules: empty ? [] : [0, 1, 40, 60, 100].map((pv, i) => ({ module: `合成功能${i}`, pv, uv: pv ? 1 : 0, actions: 0 })) } });
  });
  await page.goto("/operations?tab=visit");
  const matrix = page.getByRole("group", { name: "功能热度矩阵" });
  await expect(matrix.getByRole("button")).toHaveCount(5);
  await expect(matrix.getByRole("button", { name: /合成功能0/ })).toContainText("0");
  await matrix.getByRole("button", { name: /合成功能2/ }).hover();
  await expect(page.getByRole("tooltip")).toContainText("访问用户：1 人");
  for (const dark of [false, true]) {
    await page.locator("html").evaluate((el, value) => el.classList.toggle("dark", value), dark);
    expect((await new AxeBuilder({ page }).include('.ops-heatmap').include('.ops-heatmap-legend').analyze()).violations).toEqual([]);
  }
  empty = true;
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.getByText("暂无已采集的功能访问数据")).toBeVisible();
  await expect(matrix).toHaveCount(0);
});

test("越界页回到有效末页，访问来源与排行仍能下钻", async ({ page }) => {
  await mockOperations(page, "super_admin", true);
  await page.goto("/operations?tab=audit&page=999&page_size=20");
  await expect(page).toHaveURL(/page=3/);
  await expect(page.getByRole("table").locator("tbody tr")).toHaveCount(13);
  await page.getByRole("link", { name: "访问与使用", exact: true }).click();
  await page.getByRole("group", { name: "访问来源统计" }).getByRole("button", { name: /直接访问/ }).click();
  await expect(page.getByLabel("来源", { exact: true })).toHaveValue("direct");
  await page.getByRole("button", { name: /用户管理.*次/ }).click();
  await expect(page.getByLabel("模块", { exact: true })).toHaveValue("用户管理");
});

test("五个页面、详情焦点及管理员入口", async ({ page }) => {
  await mockOperations(page);
  await page.goto("/operations");
  await expect(page.getByRole("heading", { name: "运营审计", exact: true })).toBeVisible();
  await expect(page).toHaveTitle(/运营总览/);
  for (const name of ["登录与活跃", "访问与使用", "操作审计", "用户洞察", "运营总览"]) {
    await page.getByRole("link", { name, exact: true }).click();
    await expect(page).toHaveTitle(new RegExp(name));
  }
  await page.getByRole("link", { name: "操作审计", exact: true }).click();
  const detail = page.getByRole("button", { name: "查看详情", exact: true }).first();
  await detail.click();
  await expect(page.getByRole("dialog")).toContainText("192.168.1.0/24");
  await page.keyboard.press("Escape");
  await expect(detail).toBeFocused();
  expect((await new AxeBuilder({ page }).include('[data-operations-page]').analyze()).violations).toEqual([]);
});

test("普通用户不可访问运营页面", async ({ page }) => {
  await mockOperations(page, "user");
  await page.goto("/operations");
  await expect(page.getByText("此页面仅管理员和超级管理员可访问")).toBeVisible();
  await expect(page.getByRole("navigation", { name: "管理", exact: true }).getByRole("link", { name: "运营审计" })).toHaveCount(0);
});

test("日期快捷标签与下钻一致，用户名打开全部事件时间线", async ({ page }) => {
  await mockOperations(page);
  await page.goto("/operations?start=2026-01-01&end=2026-01-02");
  await expect(page.getByRole("group", { name: "快捷时间范围" }).getByRole("button", { name: "自定义" })).toHaveAttribute("aria-pressed", "true");
  await page.getByRole("button", { name: "合成用户", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "用户行为时间线" })).toContainText("全部事件");
});

test("详情失败可重试", async ({ page }) => {
  await mockOperations(page);
  await page.route("**/api/operations/events/*", route => {
    if (route.request().url().endsWith("/query")) return route.fallback();
    return route.fulfill({ status: 503, json: { detail: "模拟故障" } });
  });
  await page.goto("/operations");
  await page.getByRole("button", { name: "查看详情", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("button", { name: "重试读取详情" })).toBeVisible();
});

test("图表和失败指标下钻保留用户范围并固定业务事件", async ({ page }) => {
  await mockOperations(page);
  await page.goto("/operations?tab=audit");
  await page.getByLabel("日志类型").selectOption("access");
  await page.getByLabel("用户", { exact: true }).selectOption("member");
  await page.getByRole("link", { name: "运营总览", exact: true }).click();
  await page.getByRole("button", { name: /失败操作.*点击查看失败记录/ }).click();
  await expect(page.getByLabel("用户", { exact: true })).toHaveValue("member");
  await expect(page.getByLabel("日志类型")).toHaveValue("action");
  await page.getByRole("link", { name: "运营总览", exact: true }).click();
  await page.getByRole("button", { name: /2026-09-17，PV/ }).click();
  await expect(page.getByLabel("用户", { exact: true })).toHaveValue("member");
});

test("常用视图保留全部事件并支持删除", async ({ page }) => {
  await mockOperations(page);
  const view = { view_id: "00000000000040008000000000000008", name: "完整时间线", tab: "audit", filters: { start: "2026-09-17", end: "2026-09-17", kind: "", actor_id: "member", result: "", module: "", source: "", action: "", search: "", page: 1, page_size: 20, granularity: "day" } };
  let deleted = false;
  await page.route("**/api/operations/views**", route => {
    if (route.request().method() === "DELETE") { deleted = true; return route.fulfill({ json: { ok: true } }); }
    return route.fulfill({ json: { items: deleted ? [] : [view] } });
  });
  await page.goto("/operations");
  await page.getByLabel("常用视图", { exact: true }).selectOption(view.view_id);
  await expect(page.getByLabel("日志类型")).toHaveValue("");
  await page.getByRole("button", { name: "管理视图", exact: true }).click();
  await page.getByRole("button", { name: "删除视图 完整时间线" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "管理视图", exact: true })).toBeDisabled();
  expect(deleted).toBe(true);
});

test("未识别账号不生成无效的用户筛选", async ({ page }) => {
  await mockOperations(page);
  const entry = { event_id: "00000000000040008000000000000001", occurred_at: "2026-09-17T02:00:00Z", actor_id: null, actor_name: "未识别账号", kind: "login", module: "登录", action: "密码登录", result: "failure", device: "其他浏览器", changes: [] };
  await page.route("**/api/operations/events/*", route => route.fulfill({ json: route.request().url().endsWith("/query") ? { items: [entry], total: 1, page: 1 } : entry }));
  await page.goto("/operations");
  await expect(page.getByRole("button", { name: "未识别账号", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "查看详情", exact: true }).click();
  await expect(page.getByRole("button", { name: "查看用户时间线" })).toBeDisabled();
});

test("导出显式确认且失败留在弹窗，不重复点击提交", async ({ page }) => {
  await mockOperations(page);
  let requests = 0;
  await page.route("**/api/operations/export", async route => {
    requests++;
    await route.fulfill({ status: 413, json: { detail: "合成超限" } });
  });
  await page.goto("/operations");
  await page.getByRole("button", { name: "导出记录", exact: true }).click();
  expect(requests).toBe(0);
  await page.getByRole("button", { name: "确认导出", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText("缩小筛选范围");
  expect(requests).toBe(1);
});

test("桌面与窄屏深色视觉、错误恢复及权限策略", async ({ page }, testInfo) => {
  await mockOperations(page, "admin");
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 1000 });
  await page.goto("/operations");
  await expect(page.getByText("普通用户及本人", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "保留策略", exact: true })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "需要关注" })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("operations-desktop.png"), animations: "disabled" });
  await page.getByRole("button", { name: "深色主题", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath("operations-mobile-dark.png"), animations: "disabled" });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  expect((await new AxeBuilder({ page }).include('[data-operations-page]').analyze()).violations).toEqual([]);
  await page.route("**/api/operations/summary", route => route.fulfill({ status: 503, json: { detail: "合成故障" } }));
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("未完成");
  await expect(page.getByRole("button", { name: "重新加载", exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test("访问只记录白名单路径，后台页面不发送心跳", async ({ page }) => {
  await mockOperations(page);
  const visits: { page: string; event_id: string }[] = [];
  await page.route("**/api/operations/visits", route => { visits.push(route.request().postDataJSON()); return route.fulfill({ json: { ok: true } }); });
  await page.goto("/operations?tab=login&private=must-not-collect");
  await expect.poll(() => visits.length).toBe(1);
  expect(visits[0].page).toBe("/operations");
  expect(JSON.stringify(visits)).not.toContain("must-not-collect");
  await page.getByRole("link", { name: "访问与使用", exact: true }).click();
  await expect(page).toHaveTitle(/访问与使用/);
  expect(visits.length).toBe(1);
});

test("原型层次、多记录用户与时间线分页、宽窄主题截图", async ({ page }, testInfo) => {
  await mockOperations(page, "super_admin", true);
  const timelineRequests: Record<string, unknown>[] = [];
  page.on("request", request => { if (request.url().endsWith("/events/query")) timelineRequests.push(request.postDataJSON()); });
  await page.setViewportSize({ width: 1440, height: 1035 });
  await page.goto("/operations?start=2026-09-11&end=2026-09-17");
  await expect(page.getByRole("heading", { name: "最近关键操作" })).toBeVisible();
  await expect(page.getByRole("table").locator("tbody tr")).toHaveCount(10);
  await expect(page.getByLabel("用户", { exact: true })).toHaveCount(0);
  const trend = await page.getByRole("heading", { name: "使用趋势" }).boundingBox();
  expect(trend!.y).toBeLessThan(440);
  await page.screenshot({ path: testInfo.outputPath("overview-restored.png") });
  for (const [name, image] of [["登录与活跃", "login"], ["访问与使用", "visits"], ["操作审计", "audit"], ["用户洞察", "users"]]) {
    await page.getByRole("link", { name, exact: true }).click();
    await expect(page.locator('fieldset[aria-busy="false"]')).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath(`${image}-restored.png`) });
  }
  const users = page.getByRole("table", { name: "用户使用明细", exact: true });
  await expect(users.locator("tbody tr")).toHaveCount(10);
  await page.getByRole("navigation", { name: "用户明细分页" }).getByRole("button", { name: "下一页" }).click();
  await expect(users.getByRole("button", { name: "合成用户11", exact: true })).toBeVisible();
  await users.getByRole("button", { name: "合成用户11", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "用户行为时间线" });
  await expect(drawer.locator("ol > li")).toHaveCount(10);
  const timelinePager = drawer.getByRole("navigation", { name: "时间线分页" });
  await drawer.locator("ol").evaluate(el => { el.scrollTop = el.scrollHeight; });
  const nextTimeline = timelinePager.getByRole("button", { name: "下一页" });
  await nextTimeline.focus();
  await page.keyboard.press("Enter");
  await expect(drawer.getByText("任务工作台 · 模拟操作11", { exact: true })).toBeVisible();
  await expect(nextTimeline).toBeFocused();
  expect(await drawer.locator("ol").evaluate(el => el.scrollTop)).toBe(0);
  expect(timelineRequests.at(-1)).toMatchObject({ page: 2, page_size: 10, actor_id: "member-10", kind: "" });
  await page.screenshot({ path: testInfo.outputPath("timeline-restored.png") });
  await drawer.getByRole("button", { name: "查看详情", exact: true }).first().click();
  await expect(page.getByRole("dialog", { name: "事件详情" })).toBeVisible();
  await page.getByRole("button", { name: "返回用户时间线" }).click();
  await expect(drawer.getByText("任务工作台 · 模拟操作11", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(users.getByRole("button", { name: "合成用户11", exact: true })).toBeFocused();
  await page.getByRole("link", { name: "运营总览", exact: true }).click();
  await page.getByRole("button", { name: "深色主题", exact: true }).click();
  await expect(page.locator('fieldset[aria-busy="false"]')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("overview-dark-restored.png"), animations: "disabled" });
  expect((await new AxeBuilder({ page }).include('[data-operations-page]').analyze()).violations).toEqual([]);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({ path: testInfo.outputPath("mobile-restored.png") });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect((await new AxeBuilder({ page }).include('[data-operations-page]').analyze()).violations).toEqual([]);
});

test("滚到底部不拖出全页空白，长列表在表格内部滚动", async ({ page }, testInfo) => {
  await mockOperations(page, "super_admin", true);
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/operations?tab=audit&page_size=50");
  await expect(page.getByRole("table").locator("tbody tr")).toHaveCount(50);
  const metrics = await page.evaluate(() => {
    const root = document.scrollingElement!;
    const pane = document.querySelector<HTMLElement>(".ops-workspace")!;
    pane.scrollTop = pane.scrollHeight;
    window.scrollTo(0, 100000);
    return { documentHeight: root.scrollHeight, viewport: innerHeight, outerScroll: scrollY,
      overflowing: Array.from(document.querySelectorAll("body *")).filter(el => el.getBoundingClientRect().bottom > innerHeight + 1).map(el => ({ tag: el.tagName, className: el.className, bottom: el.getBoundingClientRect().bottom })).slice(-12) };
  });
  await testInfo.attach("scroll-geometry", { body: JSON.stringify(metrics, null, 2), contentType: "application/json" });
  expect(metrics.documentHeight).toBeLessThanOrEqual(metrics.viewport + 1);
  expect(metrics.outerScroll).toBe(0);
  const table = page.getByRole("region", { name: "日志表格，可滚动查看" });
  await table.evaluate(el => { el.scrollTop = el.scrollHeight; });
  await table.hover();
  await page.mouse.wheel(0, 4000);
  await expect.poll(() => page.evaluate(() => scrollY)).toBe(0);
  const navigation = await page.getByRole("complementary", { name: "全局导航" }).boundingBox();
  expect(navigation!.y).toBe(0);
  expect(navigation!.height).toBe(900);
  await expect(page.getByRole("navigation", { name: "日志分页", exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("bottom-no-blank.png") });
  const pager = page.getByRole("navigation", { name: "日志分页", exact: true });
  await pager.getByRole("button", { name: "下一页" }).click();
  await expect(page.getByRole("table").locator("tbody tr")).toHaveCount(3);
  await expect(pager.getByRole("button", { name: "下一页" })).toBeDisabled();
  expect(await table.evaluate(el => el.scrollTop)).toBe(0);
  await pager.getByRole("button", { name: "上一页" }).click();
  await expect(page.getByRole("table").locator("tbody tr")).toHaveCount(50);
  expect(await table.evaluate(el => el.scrollTop)).toBe(0);
  await page.getByRole("button", { name: "查看详情", exact: true }).first().click();
  await page.keyboard.press("Escape");
  await page.setViewportSize({ width: 390, height: 620 });
  await page.locator(".ops-workspace").evaluate(el => { el.scrollTop = el.scrollHeight; });
  await page.mouse.wheel(0, 4000);
  expect(await page.evaluate(() => document.scrollingElement!.scrollHeight <= innerHeight + 1 && scrollY === 0)).toBe(true);
});

test("总览无关键操作仍可导出，刷新超时不保留旧筛选数据", async ({ page }) => {
  await mockOperations(page);
  await page.route("**/api/operations/events/query", route => route.fulfill({ json: { items: [], total: 0, page: 1 } }));
  await page.goto("/operations");
  await expect(page.getByRole("button", { name: "导出记录", exact: true })).toBeEnabled();
  await page.clock.install();
  await page.route("**/api/operations/summary", () => {});
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await page.clock.fastForward(21000);
  await expect(page.getByRole("alert")).toContainText("请求超时");
  await expect(page.getByRole("heading", { name: "最近关键操作" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "导出记录", exact: true })).toBeDisabled();
});
