import { test, expect, type Page, type Route } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const modelData = (model = "synthetic-model") => ({ options: [{ provider: "local", model, label: model }], available: ["local"], default: { provider: "local", model, label: model }, document_default: { provider: "local", model, label: model }, document_default_source: "global" });
async function fixture(page: Page, role = "admin") {
  const control = { settingsFail: false, domainFail: false, mutationFail: true, writes: [] as string[], adminReads: [] as string[] };
  // 合成文档保留开发HMR；仅允许当前隔离origin访问本地开发服务器。
  await page.context().grantPermissions(["local-network-access"], { origin: String(test.info().project.use.baseURL) });
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
    if (url.pathname.startsWith("/api/admin")) control.adminReads.push(url.pathname + url.search);
    if (route.request().method() !== "GET") {
      control.writes.push(url.pathname);
      return route.fulfill(control.mutationFail ? { status: 503, json: { detail: "合成服务暂不可用" } } : { json: { ok: true } });
    }
    if (control.settingsFail && ["/api/models", "/api/overview"].includes(url.pathname)) return route.fulfill({ status: 503, json: { detail: "合成设置读取失败" } });
    if (control.domainFail && url.pathname === "/api/config/domain-health") return route.fulfill({ status: 503, json: { detail: "合成域名读取失败" } });
    const data: Record<string, unknown> = {
      "/api/semantic-workspace/tasks": [],
      "/api/semantic-workspace/context-options": { templates: [], memories: [] },
      "/api/semantic-workspace/guidance": { onboarding: [], examples: [] },
      "/api/auth/me": { user_id: `synthetic-${role}`, username: role, display_name: "合成账号", role },
      "/api/models": modelData(),
      "/api/overview": { collectors: [], scheduler: { enabled: false, active_count: 0 }, connectors: {}, connectors_enabled: {} },
      "/api/admin/users": { users: [{ user_id: "target", username: "target", display_name: "原昵称", role: "user", disabled: false, pending: false }], total: 1, pending_total: 0 },
      "/api/admin/registration": { enabled: false },
      "/api/config/domain-health": { flagged: {} },
      "/api/settings/onboarding/model-connections": { state: "completed" },
      "/api/model-connections": { items: [] }, "/api/model-connections/presets": { items: [] },
      "/api/capability-governance/packs": { items: [] }, "/api/capability-governance/validations": { items: [] },
    };
    return route.fulfill({ json: data[url.pathname] ?? { items: [] } });
  });
  return control;
}

for (const role of ["user", "admin", "super_admin"]) test(`B1 ${role}设置首尾键只选择允许分区`, async ({ page }) => {
  await fixture(page, role); await page.goto("/settings");
  await page.getByRole("tab", { name: "我的设置" }).focus(); await page.keyboard.press("End");
  await expect(page.getByRole("tab", { name: role === "user" ? "采集账号" : "运行与诊断" })).toBeFocused();
  await page.keyboard.press("ArrowRight"); await expect(page.getByRole("tab", { name: "我的设置" })).toBeFocused();
  if (role !== "user") { await page.goto("/admin"); await expect(page.getByRole("heading", { name: "用户管理", exact: true })).toBeVisible(); }
});

for (const status of [200, 503]) test(`B2 旧昵称提交${status}不清除同一账号重开草稿`, async ({ page }) => {
  await fixture(page); await page.goto("/admin"); let held: Route | undefined;
  await page.route("**/api/admin/users/target", route => { held = route; });
  await page.getByTitle("修改昵称").click(); await page.getByLabel("新昵称", { exact: true }).fill("第一稿");
  await page.getByRole("dialog").getByRole("button", { name: "确定", exact: true }).click(); await expect.poll(() => Boolean(held)).toBe(true);
  await page.getByRole("dialog").getByRole("button", { name: "取消", exact: true }).click();
  await page.getByTitle("修改昵称").click(); await page.getByLabel("新昵称", { exact: true }).fill("第二稿");
  await held!.fulfill({ status, json: status === 200 ? { ok: true } : { detail: "合成失败" } });
  await page.waitForTimeout(100); await expect(page.getByLabel("新昵称", { exact: true })).toHaveValue("第二稿");
  await expect(page.getByRole("dialog").getByRole("alert")).toHaveCount(0);
});

test("B3 未登录未知路由保持登录与原返回地址", async ({ page }) => {
  await fixture(page); await page.route("**/api/auth/me", route => route.fulfill({ status: 401, json: { detail: "未登录" } }));
  await page.goto("/missing?from=synthetic"); await expect(page).toHaveURL(/\/login\?returnTo=%2Fmissing%3Ffrom%3Dsynthetic$/);
  await expect(page.getByLabel("用户名", { exact: true })).toBeVisible();
});

for (const width of [390, 720, 1440]) for (const dark of [false, true]) test(`B4 ${width}px ${dark ? "深色" : "浅色"}受影响页可见边界与可访问性`, async ({ page }) => {
  await fixture(page); const errors: string[] = []; page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(value => localStorage.setItem("mangrove_theme", value ? "dark" : "light"), dark);
  await page.setViewportSize({ width, height: width === 720 ? 450 : 850 });
  for (const path of ["/settings", "/admin", "/missing"]) {
    await page.goto(path); await expect(page.locator("main h1")).toBeVisible();
    const overflow = await page.locator("main").evaluate(element => ({ scroll: element.scrollWidth, client: element.clientWidth }));
    expect(overflow.scroll).toBeLessThanOrEqual(overflow.client + 1);
    expect((await new AxeBuilder({ page }).include(path === "/settings" ? '[role="tablist"]' : "main").analyze()).violations).toEqual([]);
    await page.screenshot({ path: test.info().outputPath(`${width}-${dark ? "dark" : "light"}-${path.slice(1)}.png`) });
    if (path === "/admin") {
      await page.getByRole("button", { name: "新建用户", exact: true }).click();
      await page.getByRole("dialog").evaluate(element => Promise.all(element.getAnimations({ subtree: true }).map(animation => animation.finished)));
      expect((await new AxeBuilder({ page }).include('[role="dialog"]').analyze()).violations).toEqual([]);
      await page.keyboard.press("Escape"); await expect(page.getByRole("button", { name: "新建用户", exact: true })).toBeFocused();
    }
  }
  expect(errors).toEqual([]);
});
