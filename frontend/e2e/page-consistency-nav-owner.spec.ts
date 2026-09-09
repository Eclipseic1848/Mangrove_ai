import { test, expect, type Page } from "@playwright/test";

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

test("N9 工作台切Owner后旧location持续禁记，新导航可以登记", async ({ page }) => {
  await mockPages(page); await page.goto("/data-prep?task=owner-a-task&revision=2");
  await expect(page.getByRole("button", { name: "打开导航", exact: true })).toBeVisible();
  await page.evaluate(() => (window as any).navigationFixture.changeOwner());
  await page.getByRole("button", { name: "打开导航", exact: true }).click();
  await page.getByRole("button", { name: "深色主题", exact: true }).click();
  await page.getByRole("link", { name: "设置", exact: true }).click();
  await expect(page.getByRole("link", { name: "返回原任务", exact: true })).toHaveCount(0);
  await page.getByRole("link", { name: "任务工作台", exact: true }).click();
  await page.evaluate(() => { window.history.pushState({ key: "synthetic-new-navigation" }, "", "/data-prep?task=owner-b-task&revision=1"); window.dispatchEvent(new PopStateEvent("popstate")); });
  await page.getByRole("button", { name: "打开导航", exact: true }).click();
  await page.getByRole("link", { name: "设置", exact: true }).click();
  await expect(page.getByRole("link", { name: "返回原任务", exact: true })).toHaveAttribute("href", "/data-prep?task=owner-b-task&revision=1");
});
