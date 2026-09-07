import { expect, test, type Page, type Route } from "@playwright/test";

const actor = { user_id: "admin-fixture", username: "admin-fixture", display_name: "模拟管理员", role: "super_admin" };
const hold = (status: "processing" | "completed" | "failed" = "processing") => ({ operation_id: "hold-a", generation: 1, status, affected_count: 2, pending_count: status === "completed" ? 0 : 1, error_code: status === "failed" ? "resource_cleanup_pending" : null, retryable: status === "failed", updated_at: "2026-09-06T00:00:00Z" });
const account = (name = "owner-a") => ({ user_id: name, username: name, display_name: name, role: "user", disabled: 0, pending: 0, created_at: "2026-01-01", execution_hold: null as ReturnType<typeof hold> | null });
const list = (users: ReturnType<typeof account>[]) => ({ users, total: users.length, pending_total: 0 });
async function setup(page: Page, routeUser: (route: Route) => Promise<void>, role = "super_admin") {
  await page.clock.install();
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: { ...actor, role } });
    if (path === "/api/admin/registration") return route.fulfill({ json: { enabled: false } });
    if (path.startsWith("/api/admin/users")) return routeUser(route);
    return route.fulfill({ status: 404, json: { detail: "未匹配隔离API" } });
  });
  await page.goto("/admin");
  await expect(page.getByText("@owner-a", { exact: true })).toBeVisible();
}
const row = (page: Page, name = "owner-a") => page.getByRole("group", { name: `账号 ${name}`, exact: true });

test("停用接受与后台完成分开，刷新恢复且不凭零计数完成", async ({ page }) => {
  const user = account();
  let patch: Route | undefined;
  let patches = 0;
  await setup(page, async (route) => {
    if (route.request().method() === "PATCH") { patches += 1; patch = route; return; }
    return route.fulfill({ json: list([user, account("owner-b")]) });
  });
  await page.getByTitle("禁用账号", { exact: true }).first().evaluate((button: HTMLButtonElement) => { button.click(); button.click(); });
  await expect.poll(() => !!patch).toBe(true);
  await expect(row(page).getByTitle("禁用账号", { exact: true })).toBeDisabled();
  user.disabled = 1;
  user.execution_hold = { ...hold(), pending_count: 0 };
  await patch!.fulfill({ json: { ok: true, user } });
  await expect(row(page)).toContainText("新操作已拒绝");
  await expect(row(page)).toContainText("后台处理进行中");
  await expect(row(page)).not.toContainText("后台执行处理已完成");
  await expect(row(page, "owner-b")).not.toContainText("后台处理");
  await page.reload();
  await expect(row(page)).toContainText("后台处理进行中");
  user.execution_hold = hold("completed");
  await page.clock.fastForward(5000);
  await expect(row(page)).toContainText("后台执行处理已完成");
  expect(patches).toBe(1);
});

test("持久失败可重试同操作，重新启用不会恢复后台任务", async ({ page }) => {
  const user = { ...account(), disabled: 1, execution_hold: hold("failed") };
  const commands: { path: string; body: any }[] = [];
  await setup(page, async (route) => {
    if (route.request().method() !== "GET") {
      commands.push({ path: new URL(route.request().url()).pathname, body: route.request().postDataJSON() });
      if (route.request().method() === "POST") user.execution_hold = hold();
      else user.disabled = 0;
      return route.fulfill({ json: { ok: true, user } });
    }
    return route.fulfill({ json: list([user]) });
  });
  await expect(row(page)).toContainText("后台处理未完成");
  await row(page).getByRole("button", { name: "重试处理", exact: true }).press("Enter");
  await expect(row(page)).toContainText("后台处理进行中");
  await row(page).getByTitle("启用账号", { exact: true }).click();
  await expect(row(page)).toContainText("历史任务不会自动继续");
  await expect(row(page)).toContainText("后台处理进行中");
  await page.reload();
  await expect(row(page)).toContainText("后台处理进行中");
  expect(commands).toEqual([
    { path: "/api/admin/users/owner-a/execution-hold/retry", body: { operation_id: "hold-a" } },
    { path: "/api/admin/users/owner-a", body: { disabled: false } },
  ]);
});

for (const status of [429, 503]) {
  test(`后台读取${status}停止自动轮询，刷新恢复但不改持久状态`, async ({ page }) => {
    const user = { ...account(), disabled: 1, execution_hold: hold() };
    let reads = 0;
    let failed = false;
    await setup(page, async (route) => {
      reads += 1;
      return failed ? route.fulfill({ status, json: { detail: "synthetic" } }) : route.fulfill({ json: list([user]) });
    });
    failed = true;
    await page.clock.fastForward(5000);
    await expect(page.getByRole("alert")).toContainText("自动更新已停止");
    await expect(row(page)).toContainText("后台处理进行中");
    await expect(row(page)).not.toContainText("后台处理未完成");
    const stoppedReads = reads;
    await page.clock.fastForward(15000);
    expect(reads).toBe(stoppedReads);
    failed = false;
    await page.getByRole("button", { name: "刷新", exact: true }).click();
    await expect(page.getByRole("alert")).toHaveCount(0);
    await expect(row(page)).toContainText("后台处理进行中");
  });
}

test("未知停用提交不自动重发，只通过刷新核对", async ({ page }) => {
  const user = account();
  let patches = 0;
  await setup(page, async (route) => {
    if (route.request().method() === "PATCH") {
      patches += 1;
      user.disabled = 1;
      user.execution_hold = hold();
      return route.abort("failed");
    }
    return route.fulfill({ json: list([user]) });
  });
  await page.getByTitle("禁用账号", { exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("提交结果尚未确认");
  await page.clock.fastForward(15000);
  expect(patches).toBe(1);
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(row(page)).toContainText("新操作已拒绝");
  expect(patches).toBe(1);
});

test("迟到停用结果只刷新当前筛选，不把旧Owner插回列表", async ({ page }) => {
  let patch: Route | undefined;
  const filters: string[] = [];
  await setup(page, async (route) => {
    if (route.request().method() === "PATCH") { patch = route; return; }
    const filter = new URL(route.request().url()).searchParams.get("status") || "";
    filters.push(filter);
    return route.fulfill({ json: list([account(filter ? "owner-b" : "owner-a")]) });
  });
  await page.getByTitle("禁用账号", { exact: true }).click();
  await expect.poll(() => !!patch).toBe(true);
  await page.getByRole("combobox").nth(1).selectOption("disabled");
  await expect(page.getByText("@owner-b", { exact: true })).toBeVisible();
  const before = filters.length;
  await patch!.fulfill({ json: { ok: true, user: { ...account(), disabled: 1, execution_hold: hold() } } });
  await expect.poll(() => filters.length).toBeGreaterThan(before);
  expect(filters.slice(before)).toEqual(["disabled"]);
  await expect(page.getByText("@owner-a", { exact: true })).toHaveCount(0);
});

test("轮询请求未结束不并发，后台标签不轮询", async ({ page }) => {
  const user = { ...account(), disabled: 1, execution_hold: hold() };
  let reads = 0;
  let held: Route | undefined;
  let block = false;
  await setup(page, async (route) => {
    reads += 1;
    if (block) { held = route; return; }
    return route.fulfill({ json: list([user]) });
  });
  const initialReads = reads;
  block = true;
  await page.clock.fastForward(5000);
  await expect.poll(() => !!held).toBe(true);
  await page.clock.fastForward(15000);
  expect(reads).toBe(initialReads + 1);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", { configurable: true, get: () => true });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await held!.fulfill({ json: list([user]) });
  await page.clock.fastForward(15000);
  expect(reads).toBe(initialReads + 1);
});

test("权限拒绝不报告停用成功，同级账号不提供治理按钮", async ({ page }) => {
  await setup(page, async (route) => {
    if (route.request().method() === "PATCH") return route.fulfill({ status: 403, json: { detail: "synthetic forbidden" } });
    return route.fulfill({ json: list([account(), { ...account("peer-admin"), role: "admin" }]) });
  }, "admin");
  await expect(row(page, "peer-admin").getByTitle("禁用账号", { exact: true })).toHaveCount(0);
  await row(page).getByTitle("禁用账号", { exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("无权管理该账号");
  await expect(row(page)).not.toContainText("新操作已拒绝");
});

test("持久失败只显示受控原因，不显示原始错误正文", async ({ page }) => {
  const user = { ...account(), disabled: 1, execution_hold: { ...hold("failed"), error_code: "synthetic-secret-/private/path" } };
  await setup(page, async (route) => route.fulfill({ json: list([user]) }));
  await expect(row(page)).toContainText("后台处理未完成");
  await expect(page.locator("body")).not.toContainText("synthetic-secret");
  await expect(page.locator("body")).not.toContainText("/private/path");
});

for (const generation of [1, 2]) {
test(`旧停用回执不能覆盖第${generation}代更新后的处理状态`, async ({ page }) => {
  let user = account();
  let patch: Route | undefined;
  let block = false;
  await setup(page, async (route) => {
    if (route.request().method() === "PATCH") { patch = route; return; }
    if (block) return;
    return route.fulfill({ json: list([user]) });
  });
  await page.getByTitle("禁用账号", { exact: true }).click();
  await expect.poll(() => !!patch).toBe(true);
  user = { ...user, disabled: 1, execution_hold: { ...hold(), operation_id: generation === 2 ? "hold-new" : "hold-a", generation, updated_at: "2026-09-06T00:00:01Z" } };
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(row(page)).toContainText("后台处理进行中");
  block = true;
  await patch!.fulfill({ json: { ok: true, user: { ...user, execution_hold: hold("completed") } } });
  await expect(row(page)).toContainText("后台处理进行中");
  await expect(row(page)).not.toContainText("后台执行处理已完成");
});

}
