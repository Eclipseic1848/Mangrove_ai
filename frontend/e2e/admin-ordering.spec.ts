import { expect, test, type Page, type Route } from "@playwright/test";

const actor = { user_id: "admin-fixture", username: "admin-fixture", display_name: "模拟管理员", role: "super_admin", access_token: "fixture-token" };
const result = (name: string, total = 61, pending = 0) => ({
  users: [{ user_id: name, username: name, display_name: name, role: "user", disabled: 0, pending: 0, created_at: "2026-01-01" }],
  total, pending_total: pending,
});

async function mockAdmin(page: Page, list: (route: Route, query: URLSearchParams) => Promise<void>, mutation?: (route: Route) => Promise<void>) {
  await page.addInitScript(() => localStorage.setItem("mangrove_token", "fixture-token"));
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: actor });
    if (url.pathname === "/api/admin/registration") return route.fulfill({ json: { enabled: false } });
    if (url.pathname === "/api/admin/users" && route.request().method() === "GET") return list(route, url.searchParams);
    if (url.pathname.startsWith("/api/admin/users") && mutation) return mutation(route);
    return route.fulfill({ status: 404, json: { detail: "未匹配的模拟 API" } });
  });
  await page.goto("/admin");
  await expect(page.getByText("@initial", { exact: true })).toBeVisible();
}

async function deliver(page: Page, route: Route, json: unknown, status = 200) {
  const response = page.waitForResponse((r) => r.url() === route.request().url() && r.request().method() === route.request().method());
  await route.fulfill({ status, json });
  await (await response).finished();
  // 屏障控制网络次序后等待浏览器提交渲染，不用固定网络延时猜测竞态。
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
}

test("搜索迟到成功不能覆盖新列表与统计", async ({ page }) => {
  let old: Route | undefined;
  await mockAdmin(page, async (route, query) => {
    if (query.get("q") === "old") { old = route; return; }
    await route.fulfill({ json: result(query.get("q") === "new" ? "new-result" : "initial", 61, 2) });
  });
  await page.getByPlaceholder("搜索用户名/昵称…").fill("old");
  await expect.poll(() => !!old).toBeTruthy();
  await page.getByPlaceholder("搜索用户名/昵称…").fill("new");
  await expect(page.getByText("@new-result", { exact: true })).toBeVisible();
  await deliver(page, old!, result("old-result", 999, 99));
  await expect(page.getByText("@new-result", { exact: true })).toBeVisible();
  await expect(page.getByText("用户（共 61）", { exact: false })).toBeVisible();
  await expect(page.getByText("99 待审批", { exact: false })).toHaveCount(0);
});

test("连续刷新旧 finally 不得提前结束当前 loading", async ({ page }) => {
  const held: Route[] = [];
  let block = false;
  await mockAdmin(page, async (route) => {
    if (block) { held.push(route); return; }
    await route.fulfill({ json: result("initial") });
  });
  block = true;
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect.poll(() => held.length).toBe(1);
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect.poll(() => held.length).toBe(2);
  await deliver(page, held[0], { detail: "旧请求失败" }, 500);
  await expect(page.getByText("加载中…", { exact: true })).toBeVisible();
  await expect(page.getByText("@initial", { exact: true })).toHaveCount(0);
  await deliver(page, held[1], result("latest"));
  await expect(page.getByText("@latest", { exact: true })).toBeVisible();
});

test("翻页请求迟到不能覆盖筛选回到第一页", async ({ page }) => {
  let second: Route | undefined;
  const queries: string[] = [];
  await mockAdmin(page, async (route, query) => {
    queries.push(query.toString());
    if (query.get("page") === "2" && !query.get("role")) { second = route; return; }
    await route.fulfill({ json: result(query.get("role") ? "filtered" : "initial") });
  });
  await page.getByRole("button", { name: "下一页" }).click();
  await expect.poll(() => !!second).toBeTruthy();
  await page.getByRole("combobox").nth(0).selectOption("user");
  await expect(page.getByText("@filtered", { exact: true })).toBeVisible();
  expect(queries.filter((q) => q.includes("role=user")).every((q) => new URLSearchParams(q).get("page") === "1")).toBeTruthy();
  await deliver(page, second!, result("obsolete-page"));
  await expect(page.getByText("@filtered", { exact: true })).toBeVisible();
  await expect(page.getByText("第 1 / 4 页", { exact: true })).toBeVisible();
});

test("最新筛选失败清除旧资料且刷新可恢复", async ({ page }) => {
  let failed = true;
  await mockAdmin(page, async (route, query) => {
    if (query.get("status") && failed) return route.fulfill({ status: 503, json: { detail: "模拟暂不可用" } });
    await route.fulfill({ json: result(query.get("status") ? "recovered" : "initial") });
  });
  await page.getByRole("combobox").nth(1).selectOption("pending");
  await expect(page.getByRole("alert")).toContainText("加载用户失败");
  await expect(page.getByText("@initial", { exact: true })).toHaveCount(0);
  failed = false;
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.getByText("@recovered", { exact: true })).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

for (const action of ["patch", "delete", "create"] as const) {
  test(`${action} 迟到完成后仅刷新当前筛选`, async ({ page }) => {
    let operation: Route | undefined;
    const queries: string[] = [];
    await mockAdmin(page, async (route, query) => {
      queries.push(query.get("status") || "");
      await route.fulfill({ json: result(query.get("status") ? "current-filter" : "initial") });
    }, async (route) => { operation = route; });
    if (action === "patch") await page.getByTitle("禁用账号", { exact: true }).click();
    if (action === "delete") {
      await page.getByTitle("删除用户", { exact: true }).click();
      await page.getByRole("button", { name: "删除", exact: true }).click();
    }
    if (action === "create") {
      await page.getByRole("button", { name: "新建用户", exact: true }).click();
      await page.getByPlaceholder("用户名（≥2位）").fill("fixture-new");
      await page.getByPlaceholder("密码（≥6位）").fill("synthetic-password");
      await page.getByRole("button", { name: "创建", exact: true }).click();
      await page.getByRole("button", { name: "取消", exact: true }).click();
    }
    await expect.poll(() => !!operation).toBeTruthy();
    await page.getByRole("combobox").nth(1).selectOption("disabled");
    await expect(page.getByText("@current-filter", { exact: true })).toBeVisible();
    const count = queries.length;
    await deliver(page, operation!, { ok: true });
    await expect.poll(() => queries.length).toBeGreaterThan(count);
    expect(queries.slice(count)).toEqual(["disabled"]);
    await expect(page.getByText("@current-filter", { exact: true })).toBeVisible();
  });
}

for (const editor of ["name", "password", "create"] as const) {
  for (const reopen of [false, true]) {
    test(`${editor} 旧提交不丢失${reopen ? "关闭重开的同对象" : "等待中新改"}草稿`, async ({ page }) => {
      let operation: Route | undefined;
      await mockAdmin(page, async (route) => { await route.fulfill({ json: result("initial") }); }, async (route) => { operation = route; });
      const open = async () => {
        if (editor === "create") await page.getByRole("button", { name: "新建用户", exact: true }).click();
        else await page.getByTitle(editor === "name" ? "修改昵称" : "重置密码", { exact: true }).click();
      };
      const field = page.getByPlaceholder(editor === "name" ? "新昵称（1~32 字符）" : editor === "password" ? "新密码（≥6位）" : "用户名（≥2位）");
      await open();
      await field.fill("first-draft");
      if (editor === "create") await page.getByPlaceholder("密码（≥6位）").fill("synthetic-password");
      await page.getByRole("button", { name: editor === "create" ? "创建" : "确定", exact: true }).click();
      await expect.poll(() => !!operation).toBeTruthy();
      if (reopen) {
        await page.getByRole("button", { name: "取消", exact: true }).click();
        await open();
      }
      if (!reopen) await field.fill("later-draft");
      const draft = await field.inputValue();
      await deliver(page, operation!, { ok: true });
      await expect(field).toBeVisible();
      await expect(field).toHaveValue(draft);
    });
  }
}

test("登录注册标签可聚焦并用键盘提交", async ({ page }) => {
  const submitted: { path: string; body: any }[] = [];
  await page.route("**/api/**", (route) => {
    const path = new URL(route.request().url()).pathname;
    if (["/api/auth/login", "/api/auth/register"].includes(path)) {
      submitted.push({ path, body: route.request().postDataJSON() });
      return route.fulfill({ json: path.endsWith("register") ? { pending: true, message: "模拟待审批" } : actor });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto("/login");
  await page.getByRole("button", { name: "注册", exact: true }).click();
  for (const label of ["用户名", "显示名（可选）", "密码"]) {
    await page.locator("label").filter({ hasText: label }).click();
    await expect(page.getByLabel(label, { exact: true })).toBeFocused();
    await expect(page.getByLabel(label, { exact: true })).toHaveCount(1);
  }
  await page.getByLabel("用户名", { exact: true }).fill("fixture-new");
  await page.getByLabel("显示名（可选）", { exact: true }).fill("模拟显示名");
  await page.getByLabel("密码", { exact: true }).fill("synthetic-password");
  await page.getByLabel("密码", { exact: true }).press("Enter");
  await expect.poll(() => submitted.length).toBe(1);
  expect(submitted[0]).toEqual({ path: "/api/auth/register", body: { username: "fixture-new", password: "synthetic-password", display_name: "模拟显示名" } });
  await expect(page.getByLabel("显示名（可选）", { exact: true })).toHaveCount(0);
  await page.locator("label").filter({ hasText: "密码" }).click();
  await expect(page.getByLabel("密码", { exact: true })).toBeFocused();
  await page.getByLabel("密码", { exact: true }).fill("synthetic-password");
  await page.getByLabel("密码", { exact: true }).press("Enter");
  await expect.poll(() => submitted.length).toBe(2);
  expect(submitted[1].path).toBe("/api/auth/login");
  await expect(page).toHaveURL(/\/$/);
});
