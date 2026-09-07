import { expect, test, type Page, type Route } from "@playwright/test";

const actor = { user_id: "admin-a", username: "admin-a", display_name: "模拟管理员", role: "admin" };
const item = (id = 1) => ({ id, user_id: `owner-${id}`, username: `owner-${id}`, display_name: null, rating: "down", reasons: ["其他"], status: "pending", created_at: "2026-09-07", has_comment: true, has_admin_note: true, content_available: true });
const result = (id = 1) => ({ event_id: `event-${id}`, content: { question: `正文问题-${id}`, answer: `正文回答-${id}`, comment: "正文描述", admin_note: "旧处理备注" }, truncated: false, content_bytes: 80 });
async function setup(page: Page, handler: (route: Route) => Promise<void>, identity = actor) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: identity });
    if (path === "/api/feedback/overview") return route.fulfill({ json: { total_sessions: 2, total_up: 0, total_down: 2, total_pending: 2, reason_counts: {}, daily: [] } });
    if (path.startsWith("/api/feedback")) return handler(route);
    return route.fulfill({ status: 404, json: { detail: "隔离API" } });
  });
  await page.goto("/feedback");
}
const open = async (page: Page, index = 0) => page.getByRole("button", { name: "审计查看业务内容", exact: true }).nth(index).click();

test("元数据列表原因先行，单次审计成功后才显示正文与事件号，关闭清理", async ({ page }) => {
  let request: Route | undefined;
  let posts = 0;
  await setup(page, async (route) => {
    if (route.request().method() === "POST") { posts++; request = route; return; }
    return route.fulfill({ json: { items: [item()], total: 1 } });
  });
  await expect(page.getByText("正文问题-1")).toHaveCount(0);
  await open(page);
  const submit = page.getByRole("button", { name: "提交审计并查看" });
  await expect(submit).toBeDisabled();
  await page.getByLabel("查看原因").fill("核对反馈处理情况");
  await submit.evaluate((button: HTMLButtonElement) => { button.click(); button.click(); });
  await expect.poll(() => posts).toBe(1);
  expect(request!.request().postDataJSON()).toMatchObject({ reason: "核对反馈处理情况", idempotency_key: expect.any(String) });
  await request!.fulfill({ json: result() });
  await expect(page.getByText("正文问题-1", { exact: true })).toBeVisible();
  await expect(page.getByText(/审计事件：event-1/)).toBeVisible();
  await page.keyboard.press("Escape");
  await open(page);
  await expect(page.getByLabel("查看原因")).toHaveValue("");
  await expect(page.getByText("正文问题-1")).toHaveCount(0);
  expect(posts).toBe(1);
});

for (const status of [403, 404, 409, 429, 503, 0]) {
  test(`审计${status || "网络未知"}拒绝不渲染异常正文且不自动重试`, async ({ page }) => {
    let posts = 0;
    await setup(page, async (route) => {
      if (route.request().method() === "POST") {
        posts++;
        return status ? route.fulfill({ status, json: { detail: "泄漏正文哨兵", ...result() } }) : route.abort("failed");
      }
      return route.fulfill({ json: { items: [item()], total: 1 } });
    });
    await open(page);
    await page.getByLabel("查看原因").fill("检查异常反馈内容");
    await page.getByRole("button", { name: "提交审计并查看" }).click();
    await expect(page.getByRole("alert")).toContainText("未显示正文");
    await expect(page.getByText("泄漏正文哨兵")).toHaveCount(0);
    await expect(page.getByText("正文问题-1")).toHaveCount(0);
    await expect(page.getByText(/审计事件：/)).toHaveCount(0);
    await expect(page.getByRole("button", { name: "提交审计并查看" })).toBeDisabled();
    expect(posts).toBe(1);
  });
}

for (const eventId of ["failed-feedback-event", ""]) {
  test(`反馈404${eventId ? "有" : "无"}审计事件号时准确说明留痕状态`, async ({ page }) => {
    await setup(page, async (route) => {
      if (route.request().method() === "POST") return route.fulfill({ status: 404, headers: eventId ? { "X-Audit-Event-ID": eventId } : {}, json: { detail: "错误正文哨兵" } });
      return route.fulfill({ json: { items: [item()], total: 1 } });
    });
    await open(page);
    await page.getByLabel("查看原因").fill("核对已失效反馈的正文");
    await page.getByRole("button", { name: "提交审计并查看" }).click();
    if (eventId) {
      await expect(page.getByRole("alert")).toContainText("正文不可用，访问失败已记录");
      await expect(page.getByText("审计事件：failed-feedback-event", { exact: true })).toBeVisible();
    } else {
      await expect(page.getByRole("alert")).toContainText("未取得可核实的审计结果");
      await expect(page.getByText(/审计事件：/)).toHaveCount(0);
    }
    await expect(page.getByText("错误正文哨兵")).toHaveCount(0);
    await page.keyboard.press("Escape");
    await open(page);
    await expect(page.getByText(/审计事件：/)).toHaveCount(0);
    await expect(page.getByRole("alert")).toHaveCount(0);
  });
}

test("关闭在途A再打开B，A迟到不覆盖B正文或原因", async ({ page }) => {
  let first: Route | undefined;
  await setup(page, async (route) => {
    if (route.request().method() === "POST") {
      if (route.request().url().includes("/1/")) { first = route; return; }
      return route.fulfill({ json: result(2) });
    }
    return route.fulfill({ json: { items: [item(), item(2)], total: 2 } });
  });
  await open(page);
  await page.getByLabel("查看原因").fill("检查第一条反馈");
  await page.getByRole("button", { name: "提交审计并查看" }).click();
  await expect.poll(() => !!first).toBe(true);
  await page.keyboard.press("Escape");
  await open(page, 1);
  await expect(page.getByLabel("查看原因")).toHaveValue("");
  await page.getByLabel("查看原因").fill("检查第二条反馈");
  await page.getByRole("button", { name: "提交审计并查看" }).click();
  await expect(page.getByText("正文问题-2", { exact: true })).toBeVisible();
  await first!.fulfill({ json: result() });
  await expect(page.getByText("正文问题-1")).toHaveCount(0);
  await expect(page.getByText(/审计事件：event-2/)).toBeVisible();
});

test("只改状态保留旧备注，编辑与显式清除备注先经单条审计", async ({ page }) => {
  const patches: unknown[] = [];
  await setup(page, async (route) => {
    if (route.request().method() === "PATCH") { patches.push(route.request().postDataJSON()); return route.fulfill({ json: { ok: true } }); }
    if (route.request().method() === "POST") return route.fulfill({ json: result() });
    return route.fulfill({ json: { items: [item()], total: 1 } });
  });
  await page.getByTitle("标记已处理", { exact: true }).click();
  await expect.poll(() => patches.length).toBe(1);
  await page.getByTitle("标记忽略", { exact: true }).click();
  await expect.poll(() => patches.length).toBe(2);
  expect(patches).toEqual([{ status: "resolved" }, { status: "ignored" }]);
  await open(page);
  await expect(page.getByLabel(/处理备注/)).toHaveCount(0);
  await page.getByLabel("查看原因").fill("核对并清除处理备注");
  await page.getByRole("button", { name: "提交审计并查看" }).click();
  await expect(page.getByLabel(/处理备注/)).toHaveValue("旧处理备注");
  await page.getByLabel(/处理备注/).fill("");
  await page.getByRole("button", { name: "保存备注" }).click();
  await expect.poll(() => patches.length).toBe(3);
  expect(patches[2]).toEqual({ admin_note: null });
});

test("CSV沿用当前筛选，实际下载只包含元数据", async ({ page }) => {
  let exported = "";
  await setup(page, async (route) => {
    if (new URL(route.request().url()).pathname.endsWith("/export")) {
      exported = route.request().url();
      return route.fulfill({ contentType: "text/csv", body: "id,rating,status\n1,down,pending\n" });
    }
    return route.fulfill({ json: { items: [item()], total: 1 } });
  });
  await page.getByRole("combobox").nth(0).selectOption("pending");
  await page.getByRole("combobox").nth(1).selectOption("down");
  await page.getByRole("combobox").nth(2).selectOption("其他");
  await page.getByPlaceholder("用户名").fill("owner-1");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "导出元数据 CSV" }).click();
  const file = await download;
  const stream = await file.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream!) chunks.push(chunk);
  expect(Buffer.concat(chunks).toString("utf8")).toBe("id,rating,status\n1,down,pending\n");
  expect(Object.fromEntries(new URL(exported).searchParams)).toEqual({ status: "pending", rating: "down", reason: "其他", user_id: "owner-1" });
});

test("旧筛选迟到不覆盖新列表，刷新重新加载明细", async ({ page }) => {
  let old: Route | undefined;
  let reads = 0;
  await setup(page, async (route) => {
    reads++;
    if (new URL(route.request().url()).searchParams.get("status") === "pending") { old = route; return; }
    return route.fulfill({ json: { items: [item(new URL(route.request().url()).searchParams.get("status") ? 2 : 1)], total: 1 } });
  });
  await expect(page.getByText("@owner-1（owner-1）", { exact: true })).toBeVisible();
  await page.getByRole("combobox").first().selectOption("pending");
  await expect.poll(() => !!old).toBe(true);
  await page.getByRole("combobox").first().selectOption("resolved");
  await expect(page.getByText("@owner-2（owner-2）", { exact: true })).toBeVisible();
  const oldResponse = page.waitForResponse((response) => response.url() === old!.request().url());
  await old!.fulfill({ json: { items: [item()], total: 99 } });
  await oldResponse;
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(page.getByText("@owner-1（owner-1）", { exact: true })).toHaveCount(0);
  const before = reads;
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect.poll(() => reads).toBeGreaterThan(before);
});

for (const change of [{ user_id: "admin-b", role: "admin" }, { user_id: "admin-a", role: "super_admin" }, { user_id: "admin-a", role: "user" }]) {
  test(`身份变化${change.user_id}/${change.role}清原因和正文并拒绝迟到响应`, async ({ page }) => {
    const identity = { ...actor };
    let pending: Route | undefined;
    await setup(page, async (route) => {
      if (route.request().method() === "POST") { pending = route; return; }
      return route.fulfill({ json: { items: [item()], total: 1 } });
    }, identity);
    await open(page);
    await page.getByLabel("查看原因").fill("检查身份变化反馈");
    await page.getByRole("button", { name: "提交审计并查看" }).click();
    await expect.poll(() => !!pending).toBe(true);
    Object.assign(identity, change);
    const updated = page.waitForResponse("**/api/auth/me");
    await page.evaluate(() => { const channel = new BroadcastChannel("mangrove-platform-session"); channel.postMessage("identity-changed"); channel.close(); });
    await updated;
    await expect(page.getByRole("dialog")).toHaveCount(0);
    const late = page.waitForResponse((response) => response.url() === pending!.request().url());
    await pending!.fulfill({ json: result() });
    await late;
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(page.getByText("正文问题-1")).toHaveCount(0);
    if (change.role !== "user") {
      await open(page);
      await expect(page.getByLabel("查看原因")).toHaveValue("");
    } else await expect(page.getByRole("button", { name: "审计查看业务内容" })).toHaveCount(0);
  });
}

test("普通用户无反馈管理正文入口", async ({ page }) => {
  let reads = 0;
  await setup(page, async (route) => { reads++; return route.fulfill({ status: 403, json: {} }); }, { ...actor, role: "user" });
  await expect(page).not.toHaveURL(/\/feedback$/);
  await expect(page.getByRole("button", { name: "审计查看业务内容" })).toHaveCount(0);
  expect(reads).toBe(0);
});

test("超级管理员同样先审计，截断备注只读且正文不写持久缓存", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page, async (route) => {
    if (route.request().method() === "POST") return route.fulfill({ json: { ...result(), truncated: true } });
    return route.fulfill({ json: { items: [item()], total: 1 } });
  }, { ...actor, role: "super_admin" });
  await open(page);
  await expect(page.getByText("正文问题-1")).toHaveCount(0);
  await page.getByLabel("查看原因").fill("核对截断的反馈内容");
  await page.getByRole("button", { name: "提交审计并查看" }).click();
  await expect(page.getByText(/本次不可编辑备注/)).toBeVisible();
  await expect(page.getByLabel(/处理备注/)).not.toBeEditable();
  await expect(page.getByRole("button", { name: "保存备注" })).toBeDisabled();
  await page.screenshot({ path: testInfo.outputPath("feedback-audit-mobile.png"), fullPage: true, animations: "disabled" });
  const bounds = await page.getByRole("dialog").boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.y).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(844);
  const storage = await page.evaluate(() => JSON.stringify({ local: { ...localStorage }, session: { ...sessionStorage } }));
  expect(storage).not.toContain("正文问题");
  expect(storage).not.toContain("核对截断");
  await page.getByRole("button", { name: "关闭", exact: true }).focus();
  await page.keyboard.press("Tab");
  await expect(page.getByLabel(/处理备注/)).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "审计查看业务内容", exact: true })).toBeFocused();
});
