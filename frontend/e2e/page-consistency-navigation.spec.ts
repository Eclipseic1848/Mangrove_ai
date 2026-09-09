import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

async function mockPages(page: Page, role = "admin") {
  await page.addInitScript(() => localStorage.setItem("mangrove_token", "synthetic-navigation-token"));
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.origin !== new URL(String(test.info().project.use.baseURL)).origin) return route.abort();
    if (route.request().isNavigationRequest() && route.request().resourceType() === "document") {
      // 保留真实地址和路由，HTML由Vite处理，绝不落到真实API。
      const response = await route.fetch({ url: new URL("/e2e/fixtures/page-consistency/index.html", url).toString() });
      return route.fulfill({ response });
    }
    if (url.pathname.startsWith("/api/semantic-workspace/tasks/")) return route.fulfill({ status: 404, json: { detail: "合成任务不存在" } });
    if (!url.pathname.startsWith("/api/")) return route.continue();
    const data: Record<string, unknown> = {
      "/api/semantic-workspace/tasks": [],
      "/api/semantic-workspace/guidance": { onboarding: [], examples: [] },
      "/api/auth/me": { user_id: `synthetic-${role}`, username: role, display_name: "合成导航账号", role },
      "/api/auth/login": { user_id: "synthetic-other", username: "other", display_name: "另一个合成账号", role },
      "/api/models": { options: [], available: [], default: null, document_default: null, document_default_source: "global" },
      "/api/overview": { collectors: [], scheduler: { enabled: false, active_count: 0 }, connectors: {}, connectors_enabled: {} },
      "/api/admin/users": { users: [], total: 0, pending_total: 0 },
      "/api/admin/registration": { enabled: false },
      "/api/memory": { personal: [], preferences: "" },
      "/api/templates": { templates: [] }, "/api/lessons": { lessons: [] }, "/api/library-dedup-log": { log: [] },
      "/api/tasks": [], "/api/tasks/templates": [],
      "/api/feedback/overview": { total: 0, positive: 0, negative: 0, reason_counts: {}, status_counts: {} },
      "/api/feedback/list": { items: [], total: 0 },
      "/api/settings/onboarding/model-connections": { state: "completed" },
      "/api/model-connections": { items: [] }, "/api/model-connections/presets": { items: [] },
      "/api/capability-governance/packs": { items: [] }, "/api/capability-governance/validations": { items: [] },
    };
    return route.fulfill({ json: data[url.pathname] ?? { items: [] } });
  });
}

test("N1 管理页390px可打开同一全局导航", async ({ page }) => {
  await mockPages(page); await page.setViewportSize({ width: 390, height: 800 }); await page.goto("/admin");
  await expect(page.getByRole("heading", { name: "用户管理", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "打开导航", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "全局导航" })).toBeVisible();
});

test("N2 设置窄屏导航约束焦点且Escape返回触发按钮", async ({ page }) => {
  await mockPages(page); await page.setViewportSize({ width: 390, height: 800 }); await page.goto("/settings");
  const trigger = page.getByRole("button", { name: "打开导航", exact: true }); await trigger.click();
  const drawer = page.getByRole("dialog", { name: "全局导航" }); await expect(drawer).toBeVisible();
  for (let i = 0; i < 18; i++) { await page.keyboard.press("Tab"); expect(await page.evaluate(() => Boolean(document.activeElement?.closest('[role="dialog"]')))).toBe(true); }
  await page.keyboard.press("Escape"); await expect(drawer).toHaveCount(0); await expect(trigger).toBeFocused();
});

test("N3 从原任务经设置到管理后按精确任务修订返回", async ({ page }) => {
  await mockPages(page); await page.setViewportSize({ width: 1440, height: 900 }); await page.goto("/data-prep?task=synthetic-task&revision=3&unrelated=excluded");
  await page.getByRole("button", { name: "打开导航", exact: true }).click(); await page.getByRole("link", { name: "设置", exact: true }).click();
  await expect(page.getByRole("heading", { name: "设置", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "用户管理", exact: true }).click();
  await page.getByRole("link", { name: "返回原任务", exact: true }).click();
  await expect(page).toHaveURL(/\/data-prep\?task=synthetic-task&revision=3$/);
});

for (const role of ["user", "admin", "super_admin"]) test(`N4 ${role}菜单保留角色边界与现有模型分区`, async ({ page }) => {
  await mockPages(page, role); await page.setViewportSize({ width: 390, height: 800 }); await page.goto("/settings");
  await expect(page.getByRole("tab", { name: "模型与连接" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "平台配置" })).toHaveCount(role === "user" ? 0 : 1);
  await page.getByRole("button", { name: "打开导航", exact: true }).click();
  const drawer = page.getByRole("dialog", { name: "全局导航" });
  await expect(drawer.getByRole("link", { name: "用户管理", exact: true })).toHaveCount(role === "user" ? 0 : 1);
  await expect(drawer.getByRole("link", { name: "反馈管理", exact: true })).toHaveCount(role === "user" ? 0 : 1);
  await page.keyboard.press("Escape"); await page.getByRole("tab", { name: "模型与连接" }).click();
  await expect(page.getByRole("heading", { name: "连接一个模型服务" })).toBeVisible();
});

test("N5 六个真实兄弟页面切换后抽屉关闭，无框架异常", async ({ page }) => {
  await mockPages(page); const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  await page.setViewportSize({ width: 390, height: 800 }); await page.goto("/settings");
  for (const [label, path] of [["用户管理", "/admin"], ["模板库", "/templates"], ["记忆", "/memory"], ["自动化任务", "/tasks"], ["反馈管理", "/feedback"], ["设置", "/settings"]]) {
    await page.getByRole("button", { name: "打开导航", exact: true }).click();
    await page.getByRole("dialog", { name: "全局导航" }).getByRole("link", { name: label, exact: true }).click();
    await expect(page).toHaveURL(new RegExp(`${path}$`)); await expect(page.getByRole("dialog", { name: "全局导航" })).toHaveCount(0);
    await expect(page.locator("main h1")).toBeVisible(); await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  }
  expect(errors).toEqual([]);
});

test("N6 换Owner清除原任务返回；直达设置不猜任务", async ({ page }) => {
  await mockPages(page); await page.goto("/settings"); await expect(page.getByRole("heading", { name: "设置", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "返回原任务", exact: true })).toHaveCount(0);
  await page.getByRole("link", { name: "任务工作台", exact: true }).click();
  await page.goto("/data-prep?task=owner-a-task&revision=2");
  await page.getByRole("button", { name: "打开导航", exact: true }).click(); await page.getByRole("link", { name: "设置", exact: true }).click();
  await expect(page.getByRole("link", { name: "返回原任务", exact: true })).toBeVisible();
  await page.evaluate(() => (window as any).navigationFixture.changeOwner());
  await expect(page.getByRole("link", { name: "返回原任务", exact: true })).toHaveCount(0);
  expect(await page.evaluate(() => [localStorage, sessionStorage].some(storage => Object.values(storage).some(value => value.includes("owner-a-task"))))).toBe(false);
});

test("N7 改变视口关闭旧抽屉，浏览器前后退恢复路线", async ({ page }) => {
  await mockPages(page); await page.setViewportSize({ width: 390, height: 800 }); await page.goto("/settings");
  await page.getByRole("button", { name: "打开导航", exact: true }).click(); await page.getByRole("link", { name: "用户管理", exact: true }).click();
  await page.goBack(); await expect(page).toHaveURL(/\/settings$/); await page.goForward(); await expect(page).toHaveURL(/\/admin$/);
  await page.getByRole("button", { name: "打开导航", exact: true }).click(); await page.setViewportSize({ width: 1440, height: 900 });
  await expect(page.getByRole("dialog", { name: "全局导航" })).toHaveCount(0); await expect(page.getByRole("link", { name: "设置", exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 800 }); await expect(page.getByRole("dialog", { name: "全局导航" })).toHaveCount(0);
});

for (const width of [390, 720, 1440]) for (const dark of [false, true]) test(`N8 ${width}px ${dark ? "深色" : "浅色"}导航边界与axe`, async ({ page }) => {
  await mockPages(page); const consoleErrors: string[] = []; page.on("console", message => { if (message.type() === "error") consoleErrors.push(message.text()); });
  await page.addInitScript(value => localStorage.setItem("mangrove_theme", value ? "dark" : "light"), dark);
  await page.setViewportSize({ width, height: width === 720 ? 450 : 800 }); await page.goto("/settings");
  await expect(page).toHaveTitle("Mangrove 页面一致性回归"); await expect(page.locator("main h1")).toBeVisible();
  if (width < 768) {
    await page.getByRole("button", { name: "打开导航", exact: true }).click();
    const drawer = page.getByRole("dialog", { name: "全局导航" }); const box = await drawer.boundingBox();
    expect(box!.x).toBeGreaterThanOrEqual(0); expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    expect((await new AxeBuilder({ page }).include('[role="dialog"]').analyze()).violations).toEqual([]);
    await page.screenshot({ path: test.info().outputPath(`${width}-${dark ? "dark" : "light"}-drawer.png`) });
    await drawer.getByRole("button", { name: "关闭导航", exact: true }).click();
  } else {
    await expect(page.getByRole("link", { name: "设置", exact: true })).toBeVisible();
    expect((await new AxeBuilder({ page }).include("aside").analyze()).violations).toEqual([]);
  }
  await expect(page.locator("vite-error-overlay")).toHaveCount(0); expect(consoleErrors).toEqual([]);
  await page.screenshot({ path: test.info().outputPath(`${width}-${dark ? "dark" : "light"}.png`) });
});
