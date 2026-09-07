import { expect, test, type Page, type Route } from "@playwright/test";

const actor = { user_id: "admin-a", username: "admin-a", display_name: "模拟管理员", role: "admin" };
const pack = { pack_id: "audit-pack", version: "1.0.0", scope: "personal", maturity: "draft", lifecycle: "active", eligibility: "eligible", source: "governance_event", owner_id: "owner-a", digest: `sha256:${"a".repeat(64)}`, can_validate: false, promotion_gaps: ["validation_incomplete"] };
const outcome = { status: "succeeded", content: "能力审计正文哨兵", truncated: false, failure_reason: null, event: { event_id: "cap-audit-1", result: "succeeded" } };
async function setup(page: Page, post: (route: Route) => Promise<void>, identity = actor) {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: identity });
    if (path === "/api/capability-governance/packs") return route.fulfill({ json: { items: [pack] } });
    if (path === "/api/capability-governance/validations" || path === "/api/capability-governance/admin/platform-candidates") return route.fulfill({ json: { items: [] } });
    if (path.endsWith("/supply-chain-evidence")) return route.fulfill({ json: { evidence: null } });
    if (path === "/api/capability-governance/admin/review") return route.fulfill({ json: { items: [{ ...pack, validation: null, supply_chain: null, audit_history: [], task_metadata: { task_id: "task-a", revision: 2, owner_id: "owner-a", task_status: "completed", created_at: "2026-09-07", updated_at: "2026-09-07", input_count: 0, input_types: [], output_count: 0, output_formats: [] } }] } });
    if (path === "/api/capability-governance/admin/audit-view") return post(route);
    return route.fulfill({ status: 404, json: { detail: "隔离API" } });
  });
  await page.goto("/settings?section=governance");
  await page.getByRole("button", { name: "审计查看业务内容", exact: true }).click();
}

test("能力审计提交中可关闭，重开后迟到正文失效", async ({ page }) => {
  let pending: Route | undefined;
  let posts = 0;
  await setup(page, async (route) => { posts++; pending = route; });
  await page.getByLabel("查看原因").fill("检查能力任务冻结正文");
  await page.getByRole("button", { name: "确认查看并写入审计记录" }).click();
  await expect.poll(() => !!pending).toBe(true);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "审计查看业务内容", exact: true }).click();
  await expect(page.getByLabel("查看原因")).toHaveValue("");
  const late = page.waitForResponse((response) => response.url() === pending!.request().url());
  await pending!.fulfill({ json: outcome });
  await late;
  await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
  await expect(page.getByText("能力审计正文哨兵")).toHaveCount(0);
  expect(posts).toBe(1);
});

test("能力审计双击只写一次，成功后键盘关闭并清正文", async ({ page }, testInfo) => {
  let pending: Route | undefined;
  let posts = 0;
  await page.setViewportSize({ width: 390, height: 844 });
  await setup(page, async (route) => { posts++; pending = route; });
  await page.getByLabel("查看原因").fill("核对能力来源冻结内容");
  await page.getByRole("button", { name: "确认查看并写入审计记录" }).evaluate((button: HTMLButtonElement) => { button.click(); button.click(); });
  await expect.poll(() => posts).toBe(1);
  await expect(page.getByLabel("查看对象")).toBeDisabled();
  await pending!.fulfill({ json: outcome });
  await expect(page.getByText("能力审计正文哨兵", { exact: true })).toBeVisible();
  await expect(page.getByText(/审计记录已写入（cap-audit-1）/)).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("capability-audit-mobile.png"), fullPage: true, animations: "disabled" });
  const bounds = await page.getByRole("dialog").boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.y).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  expect(bounds!.y + bounds!.height).toBeLessThanOrEqual(844);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "审计查看业务内容", exact: true }).click();
  await expect(page.getByLabel("查看原因")).toHaveValue("");
  await expect(page.getByText("能力审计正文哨兵")).toHaveCount(0);
});

for (const status of [409, 429, 503, 0]) {
  test(`能力审计${status || "网络未知"}不显示原始错误且不重发`, async ({ page }) => {
    let posts = 0;
    await setup(page, async (route) => {
      posts++;
      return status ? route.fulfill({ status, json: { detail: "错误正文哨兵" } }) : route.abort("failed");
    });
    await page.getByLabel("查看原因").fill("核对能力失败场景");
    await page.getByRole("button", { name: "确认查看并写入审计记录" }).click();
    await expect(page.getByRole("dialog").getByRole("alert")).toContainText("未显示正文");
    await expect(page.getByText("错误正文哨兵")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "确认查看并写入审计记录" })).toBeDisabled();
    expect(posts).toBe(1);
  });
}

test("能力读取失败有事件号但不显示正文或未知原始异常", async ({ page }) => {
  await setup(page, async (route) => route.fulfill({ json: { ...outcome, status: "failed", failure_reason: "错误正文哨兵", event: { event_id: "failed-event", result: "failed" } } }));
  await page.getByLabel("查看原因").fill("核对已记录的失败查看");
  await page.getByRole("button", { name: "确认查看并写入审计记录" }).click();
  await expect(page.getByRole("dialog").getByRole("alert")).toContainText("失败尝试已写入审计记录（failed-event）");
  await expect(page.getByText("能力审计正文哨兵")).toHaveCount(0);
  await expect(page.getByText("错误正文哨兵")).toHaveCount(0);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

for (const change of [{ user_id: "admin-b", role: "admin" }, { user_id: "admin-a", role: "super_admin" }, { user_id: "admin-a", role: "user" }]) {
  test(`能力审计身份变化${change.user_id}/${change.role}迟到失效`, async ({ page }) => {
    const identity = { ...actor };
    let pending: Route | undefined;
    await setup(page, async (route) => { pending = route; }, identity);
    await page.getByLabel("查看原因").fill("核对能力身份变化");
    await page.getByRole("button", { name: "确认查看并写入审计记录" }).click();
    await expect.poll(() => !!pending).toBe(true);
    Object.assign(identity, change);
    const updated = page.waitForResponse("**/api/auth/me");
    await page.evaluate(() => { const channel = new BroadcastChannel("mangrove-platform-session"); channel.postMessage("identity-changed"); channel.close(); });
    await updated;
    await expect(page.getByRole("dialog")).toHaveCount(0);
    const late = page.waitForResponse((response) => response.url() === pending!.request().url());
    await pending!.fulfill({ json: outcome });
    await late;
    await page.evaluate(() => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(page.getByText("能力审计正文哨兵")).toHaveCount(0);
  });
}
