import { test, expect, type Page, type Route } from "@playwright/test";

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

for (const type of ["name", "password"]) test(`R1 ${type}保存失败保留原弹层草稿，成功才清除`, async ({ page }) => {
  const control = await fixture(page); await page.goto("/admin");
  await page.getByTitle(type === "name" ? "修改昵称" : "重置密码").click();
  const dialog = page.getByRole("dialog");
  const field = page.getByPlaceholder(type === "name" ? "新昵称（1~32 字符）" : "新密码（≥6位）");
  await field.fill(type === "name" ? "保留新昵称" : "synthetic-password");
  await dialog.getByRole("button", { name: "确定", exact: true }).click();
  await expect(dialog).toBeVisible(); await expect(field).toHaveValue(type === "name" ? "保留新昵称" : "synthetic-password");
  await expect(dialog.getByRole("alert")).toBeVisible();
  control.mutationFail = false; await dialog.getByRole("button", { name: "确定", exact: true }).click(); await expect(dialog).toHaveCount(0);
});

test("R2 管理员昵称与密码输入法确认不提交", async ({ page }) => {
  const control = await fixture(page); await page.goto("/admin");
  for (const kind of ["name", "password"]) {
    await page.getByTitle(kind === "name" ? "修改昵称" : "重置密码").click();
    const field = page.getByPlaceholder(kind === "name" ? "新昵称（1~32 字符）" : "新密码（≥6位）"); await field.fill("synthetic-text");
    await field.evaluate(input => { input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", isComposing: true, bubbles: true })); input.dispatchEvent(new KeyboardEvent("keydown", { key: "Enter", keyCode: 229, bubbles: true })); });
    await page.waitForTimeout(100); expect(control.writes).toEqual([]);
    await page.getByRole("dialog").getByRole("button", { name: "取消", exact: true }).click();
  }
});

test("R3 管理搜索筛选与创建字段都有常驻标签", async ({ page }) => {
  await fixture(page); await page.goto("/admin");
  await expect(page.getByLabel("搜索用户", { exact: true })).toBeVisible();
  await expect(page.getByRole("combobox", { name: "筛选角色", exact: true })).toBeVisible(); await expect(page.getByRole("combobox", { name: "筛选状态", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "新建用户" }).click();
  for (const label of ["用户名", "昵称（可选）", "初始密码"]) await expect(page.getByRole("dialog").getByLabel(label, { exact: true })).toBeVisible();
  await expect(page.getByLabel("初始密码", { exact: true })).toHaveAttribute("type", "password");
  await page.getByRole("dialog").getByRole("button", { name: "取消", exact: true }).click();
});

test("R4 设置标签页支持方向键首尾键并关联面板", async ({ page }) => {
  await fixture(page); await page.goto("/settings");
  const personal = page.getByRole("tab", { name: "我的设置" }); await personal.focus(); await page.keyboard.press("ArrowRight");
  const models = page.getByRole("tab", { name: "模型与连接" }); await expect(models).toBeFocused(); await expect(models).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("tabpanel")).toHaveAttribute("aria-labelledby", await models.getAttribute("id") as string);
  await page.keyboard.press("End"); await expect(page.getByRole("tab", { name: "运行与诊断" })).toBeFocused();
  await page.keyboard.press("Home"); await expect(personal).toBeFocused(); await expect(personal).toHaveAttribute("tabindex", "0");
});

test("R5 设置读取失败与空配置可区分并可明确刷新", async ({ page }) => {
  const control = await fixture(page); control.settingsFail = true; await page.goto("/settings");
  await expect(page.getByRole("alert")).toContainText("设置加载未完成");
  control.settingsFail = false; await page.getByRole("button", { name: "重新加载设置", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveCount(0); await expect(page.getByLabel("文档抽取默认模型")).toHaveValue("local::synthetic-model");
});

test("R6 域名诊断读取失败不冒称没有短路域名", async ({ page }) => {
  const control = await fixture(page); control.domainFail = true; await page.goto("/settings?section=diagnostics");
  await expect(page.getByRole("alert")).toContainText("无法读取域名状态"); await expect(page.getByText("当前没有被短路的域名")).toHaveCount(0);
  control.domainFail = false; await page.getByRole("button", { name: "重新读取域名状态", exact: true }).click();
  await expect(page.getByText("当前没有被短路的域名")).toBeVisible();
});

test("R7 无权与不存在路由分别解释且不读取管理正文", async ({ page }) => {
  const control = await fixture(page, "user");
  for (const path of ["/admin", "/feedback"]) { await page.goto(path); await expect(page.getByRole("heading", { name: "无权访问此页面", exact: true })).toBeVisible(); await expect(page).toHaveURL(new RegExp(`${path}$`)); }
  expect(control.adminReads).toEqual([]);
  await page.goto("/missing-navigation-page"); await expect(page.getByRole("heading", { name: "页面不存在", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "返回任务工作台", exact: true }).click(); await expect(page).toHaveURL(/\/data-prep$/);
});

test("R8 设置刷新旧响应不能覆盖新默认模型", async ({ page }) => {
  await fixture(page); await page.goto("/settings"); await expect(page.getByLabel("文档抽取默认模型")).toHaveValue("local::synthetic-model");
  let held: Route | undefined; let requests = 0;
  await page.route("**/api/models", route => { requests++; if (requests === 1) { held = route; return; } return route.fulfill({ json: modelData("new-model") }); });
  await page.getByRole("button", { name: "刷新", exact: true }).click(); await expect.poll(() => Boolean(held)).toBe(true);
  await page.getByRole("button", { name: "刷新", exact: true }).click(); await expect(page.getByLabel("文档抽取默认模型")).toHaveValue("local::new-model");
  await held!.fulfill({ json: modelData("old-model") }); await page.waitForTimeout(100); await expect(page.getByLabel("文档抽取默认模型")).toHaveValue("local::new-model");
});

test("R9 管理搜索组词期间不请求，确认后再搜索", async ({ page }) => {
  const control = await fixture(page); await page.goto("/admin"); await expect(page.getByTitle("修改昵称")).toBeVisible();
  const field = page.getByPlaceholder("搜索用户名/昵称…"); await field.dispatchEvent("compositionstart"); await field.fill("合成搜索");
  await page.waitForTimeout(400); expect(control.adminReads.some(path => new URL(path, "http://localhost").searchParams.get("q") === "合成搜索")).toBe(false);
  await field.dispatchEvent("compositionend"); await expect.poll(() => control.adminReads.some(path => new URL(path, "http://localhost").searchParams.get("q") === "合成搜索")).toBe(true);
});
