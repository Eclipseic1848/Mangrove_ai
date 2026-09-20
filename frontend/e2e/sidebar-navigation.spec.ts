import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

async function mockNavigation(page: Page, role = "super_admin") {
  // 所有请求留在合成边界，不读取真实身份、配置或任务。
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: {
      user_id: "sidebar-owner", username: "sidebar-test", display_name: "导航测试员", role,
    } });
    if (path === "/api/models") return route.fulfill({ json: { options: [], available: [], default: null, document_default: null } });
    if (path === "/api/overview") return route.fulfill({ json: { collectors: [], connectors: {}, connectors_enabled: {}, scheduler: { enabled: false, active_count: 0 } } });
    if (path === "/api/semantic-workspace/storage") return route.fulfill({ json: { task_count: 0, recycle_bin_count: 0, total_bytes: 0, upload_bytes: 0, delivery_bytes: 0 } });
    if (path === "/api/semantic-workspace/capabilities") return route.fulfill({ json: { enabled: true, items: [] } });
    if (path === "/api/semantic-workspace/context-options") return route.fulfill({ json: { templates: [], memories: [] } });
    if (path === "/api/semantic-workspace/guidance") return route.fulfill({ json: { schema_version: "1", onboarding: [], examples: [] } });
    if (path === "/api/settings/onboarding/model-connections") return route.fulfill({ json: { completed: true } });
    if (path === "/api/model-connections" || path === "/api/model-connections/presets") return route.fulfill({ json: { items: [], presets: [] } });
    if (route.request().method() !== "GET") return route.fulfill({ status: 405, json: { detail: "合成页面禁止真实操作" } });
    return route.fulfill({ json: [] });
  });
}

test("导航分组、选中态及主题切换保留工作台草稿", async ({ page }, testInfo) => {
  await mockNavigation(page);
  await page.setViewportSize({ width: 1440, height: 1000 });
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/data-prep");
  const nav = page.getByRole("complementary", { name: "全局导航", exact: true });
  await expect(nav.getByRole("navigation", { name: "工作空间", exact: true })).toBeVisible();
  await expect(nav.getByRole("navigation", { name: "管理", exact: true }).getByRole("link")).toHaveText(["运营审计", "反馈管理", "用户管理", "设置"]);
  await expect(nav.getByRole("link", { name: "任务工作台", exact: true })).toHaveAttribute("aria-current", "page");
  await expect(nav.getByRole("link", { name: "任务工作台", exact: true })).not.toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  const draft = page.getByRole("textbox", { name: "任务要求", exact: true });
  await draft.fill("切换主题与导航不丢失输入");
  await page.screenshot({ path: testInfo.outputPath("sidebar-light.png"), animations: "disabled" });
  await nav.getByRole("button", { name: "深色主题", exact: true }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);
  await expect(nav.getByRole("button", { name: "浅色主题", exact: true })).toBeVisible();
  const accountBox = (await nav.locator('summary[aria-label="账号选项"]').boundingBox())!;
  const themeBox = (await nav.getByRole("button", { name: "浅色主题", exact: true }).boundingBox())!;
  expect(Math.abs(accountBox.y + accountBox.height / 2 - themeBox.y - themeBox.height / 2)).toBeLessThan(1);
  await nav.locator('summary[aria-label="账号选项"]').focus();
  await page.keyboard.press("Tab");
  await expect(nav.getByRole("button", { name: "浅色主题", exact: true })).toBeFocused();
  await expect(page.getByRole("tooltip")).toHaveText("切换为浅色");
  await expect(draft).toHaveValue("切换主题与导航不丢失输入");
  await page.screenshot({ path: testInfo.outputPath("sidebar-dark.png"), animations: "disabled" });
  expect((await new AxeBuilder({ page }).include('[aria-label="全局导航"]').analyze()).violations).toEqual([]);
  expect(errors).toEqual([]);
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
});

test("账号选项键盘关闭并归还焦点，退出失败可重试且保留输入", async ({ page }, testInfo) => {
  await mockNavigation(page);
  let requests = 0;
  await page.route("**/api/auth/logout", async route => {
    requests++;
    await route.fulfill({ status: 503, json: { detail: "退出服务暂不可用，请重试" } });
  });
  await page.goto("/data-prep");
  const draft = page.getByRole("textbox", { name: "任务要求", exact: true });
  await draft.fill("退出失败后仍然保留这段草稿");
  const account = page.locator('summary[aria-label="账号选项"]:visible');
  await expect(page.getByRole("button", { name: "退出登录", exact: true })).toBeHidden();
  await account.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("link", { name: "账号设置", exact: true })).toHaveAttribute("href", "/settings?section=personal");
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "账号设置", exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(account).toBeFocused();
  await expect(page.getByRole("button", { name: "退出登录", exact: true })).toBeHidden();
  await account.click();
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("退出服务暂不可用");
  await expect(draft).toHaveValue("退出失败后仍然保留这段草稿");
  expect(requests).toBe(1);
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("退出服务暂不可用");
  expect(requests).toBe(2);
  await page.screenshot({ path: testInfo.outputPath("account-failure.png"), animations: "disabled" });
  await draft.click();
  await expect(page.getByRole("button", { name: "退出登录", exact: true })).toBeHidden();
});

for (const role of ["user", "admin", "super_admin"]) {
  test(`角色 ${role} 的收起导航与窄屏账号操作`, async ({ page }, testInfo) => {
    await mockNavigation(page, role);
    await page.goto("/data-prep");
    await expect(page.getByRole("navigation", { name: "管理", exact: true }).getByRole("link")).toHaveText(role === "user" ? ["设置"] : ["运营审计", "反馈管理", "用户管理", "设置"]);
    await page.getByRole("button", { name: "收起侧边栏", exact: true }).click();
    const nav = page.getByRole("complementary", { name: "全局导航", exact: true });
    await expect(nav).toHaveCSS("width", "64px");
    await nav.getByRole("link", { name: "任务工作台", exact: true }).focus();
    await expect(page.getByRole("tooltip")).toHaveText("任务工作台");
    await page.getByRole("button", { name: "展开侧边栏", exact: true }).click();
    await page.setViewportSize({ width: 390, height: 600 });
    const open = page.getByRole("button", { name: "打开导航", exact: true });
    await open.click();
    const drawer = page.getByRole("dialog", { name: "全局导航", exact: true });
    await expect(drawer.getByRole("navigation", { name: "管理", exact: true }).getByRole("link")).toHaveText(role === "user" ? ["设置"] : ["运营审计", "反馈管理", "用户管理", "设置"]);
    const account = drawer.locator('summary[aria-label="账号选项"]');
    await account.click();
    await page.keyboard.press("Escape");
    await expect(drawer).toBeVisible();
    await expect(account).toBeFocused();
    await expect(drawer.getByRole("link", { name: "账号设置", exact: true })).toBeHidden();
    await drawer.getByRole("button", { name: "深色主题", exact: true }).click();
    await page.screenshot({ path: testInfo.outputPath("mobile-dark.png"), animations: "disabled" });
    expect((await new AxeBuilder({ page }).include('[role="dialog"]').analyze()).violations).toEqual([]);
    await page.keyboard.press("Escape");
    await expect(drawer).toBeHidden();
    await expect(open).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    // 同一设置路径内只变查询参数，也必须收起抽屉。
    await page.goto("/settings?section=personal");
    await open.click();
    await drawer.locator('summary[aria-label="账号选项"]').click();
    await drawer.getByRole("link", { name: "账号设置", exact: true }).click();
    await expect(drawer).toBeHidden();
  });
}
