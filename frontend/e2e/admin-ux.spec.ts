import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

async function mockAdmin(page: Page, count = 21, role = "super_admin") {
  const state = { fail: false, registrationFail: false, creates: 0, mutations: 0,
    rows: Array.from({ length: count }, (_, i) => ({ user_id: `u${i}`, username: `user_${i}`, display_name: `测试用户 ${i}`, role: i === 1 ? "admin" : "user", pending: i === 2 ? 1 : 0, disabled: 0, created_at: "2026-09-20T08:00:00+08:00", execution_hold: null })) };
  await page.route("**/api/**", async route => {
    const request = route.request(), url = new URL(request.url());
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: { user_id: "operator", username: "operator", role } });
    if (url.pathname === "/api/admin/registration") return route.fulfill({ status: state.registrationFail ? 503 : 200, json: state.registrationFail ? { detail: "读取失败" } : { enabled: true } });
    if (url.pathname === "/api/admin/users" && request.method() === "GET") {
      const q = url.searchParams.get("q") || "", roleFilter = url.searchParams.get("role"), status = url.searchParams.get("status");
      const filtered = state.rows.filter(u => (!q || `${u.username} ${u.display_name}`.includes(q)) && (!roleFilter || roleFilter === u.role) && (!status || (status === "pending" ? u.pending : status === "disabled" ? u.disabled : !u.disabled && !u.pending)));
      const size = Number(url.searchParams.get("page_size")), start = (Number(url.searchParams.get("page")) - 1) * size;
      return route.fulfill({ json: { users: filtered.slice(start, start + size), total: filtered.length, pending_total: state.rows.filter(u => u.pending).length } });
    }
    if (url.pathname === "/api/admin/users" && request.method() === "POST") {
      state.creates++;
      await new Promise(resolve => setTimeout(resolve, 300));
      return route.fulfill({ status: 409, json: { detail: "用户名已存在" } });
    }
    if (url.pathname.startsWith("/api/admin/users/")) {
      state.mutations++;
      if (state.fail) return route.fulfill({ status: 503, json: { detail: "模拟服务失败" } });
      const id = url.pathname.split("/").at(-1);
      if (request.method() === "DELETE") state.rows = state.rows.filter(u => u.user_id !== id);
      else Object.assign(state.rows.find(u => u.user_id === id)!, request.postDataJSON());
      return route.fulfill({ json: { ok: true, user: state.rows.find(u => u.user_id === id) } });
    }
    return route.fulfill({ status: 404, json: { detail: "隔离模拟接口" } });
  });
  return state;
}

test("修改失败保留弹窗和输入，重试成功才关闭", async ({ page }) => {
  const state = await mockAdmin(page); state.fail = true;
  await page.goto("/admin");
  await page.getByRole("group", { name: "账号 user_0", exact: true }).getByTitle("修改昵称").click();
  await page.getByPlaceholder("新昵称（1~32 字符）").fill("新昵称");
  await page.getByRole("dialog").getByRole("button", { name: "确定", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("dialog").getByRole("alert")).toBeVisible();
  await expect(page.getByPlaceholder("新昵称（1~32 字符）")).toHaveValue("新昵称");
  state.fail = false;
  await page.getByRole("dialog").getByRole("button", { name: "确定", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("group", { name: "账号 user_0", exact: true })).toContainText("新昵称");
});

test("分页四档默认10，筛选复位，删除末页自动回退", async ({ page }) => {
  await mockAdmin(page);
  await page.goto("/admin");
  await expect(page.getByRole("group", { name: /^账号 / })).toHaveCount(10);
  const size = page.getByRole("combobox", { name: "每页条数" });
  await expect(size).toHaveValue("10");
  await expect(size.locator("option")).toHaveText(["10", "20", "50", "100"]);
  for (const n of ["20", "50", "100", "10"]) {
    await size.selectOption(n);
    await expect(page.getByRole("group", { name: /^账号 / })).toHaveCount(n === "10" ? 10 : n === "20" ? 20 : 21);
  }
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await page.getByRole("combobox", { name: "角色筛选" }).selectOption("admin");
  await expect(page.getByRole("group", { name: /^账号 / })).toHaveCount(1);
  await expect(page.getByText("1 / 1 页", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "清除筛选" }).click();
  await expect(page.getByRole("group", { name: /^账号 / })).toHaveCount(10);
  await page.getByRole("button", { name: "3", exact: true }).click();
  const last = page.getByRole("group", { name: "账号 user_20", exact: true });
  await last.getByRole("button", { name: "更多操作" }).click();
  await last.getByRole("button", { name: "删除用户", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "确认删除" }).click();
  await expect(page.getByText("2 / 2 页", { exact: true })).toBeVisible();
  await expect(page.getByRole("group", { name: /^账号 / })).toHaveCount(10);
  await expect(page.getByRole("button", { name: "上一页", exact: true })).toBeEnabled();
});

test("创建请求防重复，失败保留字段和密码显隐", async ({ page }) => {
  const state = await mockAdmin(page);
  await page.goto("/admin");
  await page.getByRole("button", { name: "新建用户", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("用户名（必填，至少2位）").fill("testuser");
  await dialog.getByLabel("显示密码").check();
  await dialog.getByLabel("初始密码（必填，至少6位）").fill("synthetic-password");
  await expect(dialog.getByLabel("初始密码（必填，至少6位）")).toHaveAttribute("type", "text");
  await dialog.getByRole("button", { name: "创建", exact: true }).dblclick();
  await expect(dialog.getByRole("alert")).toHaveText("用户名已存在");
  expect(state.creates).toBe(1);
  await expect(dialog.getByLabel("用户名（必填，至少2位）")).toHaveValue("testuser");
  page.once("dialog", d => d.accept());
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "新建用户", exact: true }).click();
  await expect(dialog.getByLabel("用户名（必填，至少2位）")).toHaveValue("");
  await expect(dialog.getByLabel("初始密码（必填，至少6位）")).toHaveValue("");
  await dialog.getByRole("button", { name: "管理员", exact: true }).click();
  page.once("dialog", d => d.accept());
  await dialog.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "新建用户", exact: true }).click();
  await expect(dialog.getByRole("button", { name: "普通用户", exact: true })).toHaveAttribute("aria-pressed", "true");
});

test("角色与停用必须确认，失败不关闭，取消不请求", async ({ page }) => {
  const state = await mockAdmin(page);
  await page.goto("/admin");
  const row = page.getByRole("group", { name: "账号 user_0", exact: true });
  await row.getByRole("button", { name: "更多操作" }).click();
  await row.getByRole("button", { name: "调整角色" }).click();
  await expect(page.getByRole("dialog")).toContainText("普通用户 → 管理员");
  expect(state.mutations).toBe(0);
  await page.getByRole("dialog").getByRole("button", { name: "取消" }).click();
  expect(state.mutations).toBe(0);
  await row.getByRole("button", { name: "调整角色" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "确认", exact: true }).click();
  await expect(row).toContainText("管理员");
  await row.getByTitle("禁用账号").click();
  await expect(page.getByRole("dialog")).toContainText("后台停止相关执行");
  state.fail = true;
  await page.getByRole("dialog").getByRole("button", { name: "确认", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toBeVisible();
  state.fail = false;
  await page.getByRole("dialog").getByRole("button", { name: "确认", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(row).toContainText("已禁用");
});

test("注册状态读取失败可重试，拒绝明确删除后果", async ({ page }) => {
  const state = await mockAdmin(page); state.registrationFail = true;
  await page.goto("/admin");
  await expect(page.getByText("状态读取失败，请重试")).toBeVisible();
  await expect(page.getByRole("button", { name: "开放注册", exact: true })).toHaveCount(0);
  state.registrationFail = false;
  await page.getByRole("button", { name: "重试读取" }).click();
  await expect(page.getByRole("button", { name: "关闭注册", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "全部待审批 1" }).click();
  await expect(page.getByRole("group", { name: /^账号 / })).toHaveCount(1);
  await page.getByRole("button", { name: "拒绝并删除申请", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("不可撤销");
  state.fail = true;
  await page.getByRole("dialog").getByRole("button", { name: "确认删除" }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toBeVisible();
});

test("密码失败保留、中文输入不误提交、详情时间与草稿保护", async ({ page }) => {
  const state = await mockAdmin(page); state.fail = true;
  await page.goto("/admin");
  const row = page.getByRole("group", { name: "账号 user_0", exact: true });
  await row.getByRole("button", { name: "查看详情" }).click();
  await expect(page.getByRole("dialog")).toContainText("2026/09/20 08:00:00");
  await page.keyboard.press("Escape");
  await expect(row.getByRole("button", { name: "查看详情" })).toBeFocused();
  await row.getByTitle("修改昵称").click();
  await page.getByPlaceholder("新昵称（1~32 字符）").fill("中文名字");
  await page.getByPlaceholder("新昵称（1~32 字符）").dispatchEvent("keydown", { key: "Enter", isComposing: true });
  expect(state.mutations).toBe(0);
  page.once("dialog", dialog => dialog.dismiss());
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toBeVisible();
  page.once("dialog", dialog => dialog.accept());
  await page.keyboard.press("Escape");
  await row.getByRole("button", { name: "更多操作" }).click();
  await row.getByRole("button", { name: "重置密码", exact: true }).click();
  await page.getByPlaceholder("新密码（≥6位）").fill("synthetic-password");
  await page.getByRole("dialog").getByRole("button", { name: "确定", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toBeVisible();
  await expect(page.getByPlaceholder("新密码（≥6位）")).toHaveValue("synthetic-password");
});

test("管理员不能管理同级，空列表保留分页", async ({ page }) => {
  await mockAdmin(page, 3, "admin");
  await page.goto("/admin");
  await expect(page.getByRole("group", { name: "账号 user_1", exact: true })).toContainText("仅可管理低于自己角色的账号");
  await page.getByRole("group", { name: "账号 user_0", exact: true }).getByRole("button", { name: "更多操作" }).click();
  await expect(page.getByRole("button", { name: "调整角色" })).toHaveCount(0);
  await page.getByRole("textbox", { name: "搜索用户名或昵称" }).fill("不存在的账号");
  await expect(page.getByText("未找到匹配的用户")).toBeVisible();
  await expect(page.getByRole("combobox", { name: "每页条数" })).toBeVisible();
  await page.getByRole("button", { name: "清除筛选" }).click();
  await expect(page.getByRole("group", { name: /^账号 / })).toHaveCount(3);
});

test("业务请求超时保留草稿并解除提交锁，不自动重发", async ({ page }) => {
  await mockAdmin(page);
  let requests = 0;
  await page.route("**/api/admin/users/u0", () => { requests++; });
  await page.goto("/admin");
  await page.getByRole("group", { name: "账号 user_0", exact: true }).getByTitle("修改昵称").click();
  await page.getByPlaceholder("新昵称（1~32 字符）").fill("保留草稿");
  await page.getByRole("dialog").getByRole("button", { name: "确定", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText("提交结果尚未确认", { timeout: 35_000 });
  await expect(page.getByPlaceholder("新昵称（1~32 字符）")).toHaveValue("保留草稿");
  await expect(page.getByPlaceholder("新昵称（1~32 字符）")).toBeEnabled();
  expect(requests).toBe(1);
});

test("桌面窄屏深浅主题及可访问性", async ({ page }, testInfo) => {
  await mockAdmin(page);
  const errors: string[] = []; page.on("pageerror", e => errors.push(e.message));
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "用户管理", exact: true })).toBeVisible();
  expect((await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa"]).analyze()).violations).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  expect((await page.getByText("测试用户 0", { exact: true }).boundingBox())!.height).toBeLessThan(30);
  await page.screenshot({ path: testInfo.outputPath("mobile.png") });
  await page.setViewportSize({ width: 1440, height: 960 });
  await page.getByRole("button", { name: "深色主题", exact: true }).click();
  await page.waitForTimeout(400); // 等待颜色过渡结束后测最终对比度。
  expect((await new AxeBuilder({ page }).include("main").withTags(["wcag2a", "wcag2aa"]).analyze()).violations).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("dark.png") });
  await page.getByRole("button", { name: "新建用户", exact: true }).click();
  await page.waitForTimeout(400);
  await page.getByRole("dialog").getByRole("button", { name: "创建", exact: true }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toBeVisible();
  expect((await new AxeBuilder({ page }).include('[role="dialog"]').withTags(["wcag2a", "wcag2aa"]).analyze()).violations).toEqual([]);
  expect(errors).toEqual([]);
});
