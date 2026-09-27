import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const base = { version: "1.0.0", scope: "platform", maturity: "verified", lifecycle: "active", eligibility: "eligible", source: "governance_event", owner_id: null, digest: `sha256:${"a".repeat(64)}`, can_validate: false, promotion_gaps: [] };
const packs = [
  { ...base, pack_id: "管理员试用工具", audience: "admin_gray" },
  { ...base, pack_id: "已开放工具", audience: "users" },
  { ...base, pack_id: "个人待验证工具", scope: "personal", owner_id: "owner-a", maturity: "draft", can_validate: true, promotion_gaps: ["validation_incomplete"] },
  { ...base, pack_id: "隔离工具", eligibility: "quarantined", audience: "users" },
  { ...base, pack_id: "撤销工具", lifecycle: "revoked", audience: "users" },
  { ...base, pack_id: "旧任务工具", lifecycle: "deprecated", audience: "users" },
  { ...base, pack_id: "未知范围工具" },
];

async function mockTools(page: Page, read: () => { status?: number; items?: unknown[]; detail?: string }) {
  let writes = 0;
  await page.route("**/api/**", route => {
    if (route.request().method() !== "GET") writes++;
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: { user_id: "owner-a", username: "demo", display_name: "隔离管理员", role: "admin" } });
    if (path === "/api/overview") return route.fulfill({ json: { collectors: [], scheduler: { enabled: false, active_count: 0 }, connectors: {}, connectors_enabled: {} } });
    if (path === "/api/capability-governance/packs") {
      const response = read();
      return route.fulfill({ status: response.status || 200, json: response });
    }
    if (path.endsWith("/supply-chain-evidence")) return route.fulfill({ json: { evidence: null } });
    return route.fulfill({ json: { items: [], options: [] } });
  });
  return () => writes;
}

for (const width of [1440, 390]) test(`扩展工具先显示范围与状态，技术详情按需展开 ${width}`, async ({ page }) => {
  const writes = await mockTools(page, () => ({ items: packs }));
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  page.on("console", message => { if (message.type() === "error") errors.push(message.text()); });
  await page.setViewportSize({ width, height: 900 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/settings?section=governance");
  await expect(page).toHaveURL(/settings\?section=governance/);
  expect(await page.title()).not.toBe("");
  await expect(page.getByRole("heading", { name: "扩展工具管理", exact: true })).toBeVisible();
  await expect(page.getByText("执行任务无需先来这里操作。", { exact: false })).toBeVisible();
  const trial = page.getByRole("article", { name: "管理员试用工具 1.0.0", exact: true });
  await expect(trial).toContainText("仅管理员试用");
  await expect(trial).toContainText("用途说明暂未提供");
  await expect(trial.locator("code")).not.toBeVisible();
  await expect(page.getByRole("article", { name: "已开放工具 1.0.0", exact: true })).toContainText("已向普通用户开放");
  const personal = page.getByRole("article", { name: "个人待验证工具 1.0.0", exact: true });
  await expect(personal).toContainText("仅所属用户");
  await expect(personal).toContainText("待验证，暂不可用于常规任务");
  await expect(personal.getByRole("button", { name: "发起验证", exact: true })).toBeVisible();
  await expect(page.getByText("已隔离，禁止使用", { exact: true })).toBeVisible();
  await expect(page.getByText("已撤销，禁止使用", { exact: true })).toBeVisible();
  await expect(page.getByText("已弃用，仅供历史任务", { exact: true })).toBeVisible();
  await expect(page.getByRole("article", { name: "未知范围工具 1.0.0", exact: true })).toContainText("使用范围待确认");
  const disclosure = trial.locator("summary").filter({ hasText: "技术详情与安全检查" });
  await disclosure.focus();
  await page.keyboard.press("Enter");
  await expect(trial.locator("code")).toBeVisible();
  await expect(trial.locator("code")).toHaveText(base.digest);
  await page.keyboard.press("Enter");
  await expect(trial.locator("code")).not.toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  expect((await new AxeBuilder({ page }).include("main").analyze()).violations).toEqual([]);
  await page.getByRole("heading", { name: "扩展工具管理", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: `${process.env.TEMP}/extension-tools-${width}.png` });
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  expect(errors).toEqual([]);
  expect(writes()).toBe(0);
});

test("扩展工具加载失败可重试，空态不要求用户配置", async ({ page }) => {
  let failed = true;
  const writes = await mockTools(page, () => failed ? { status: 503, detail: "工具列表暂不可用" } : { items: [] });
  await page.goto("/settings?section=governance");
  await expect(page.getByRole("alert")).toContainText("工具列表暂不可用");
  failed = false;
  await page.getByRole("button", { name: "重新加载", exact: true }).click();
  await expect(page.getByText("暂无扩展工具。无需配置此页，可直接前往任务工作台。")).toBeVisible();
  expect(writes()).toBe(0);
});
