import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

for (const width of [1440, 390]) test(`语义召回直达配置且刷新保留定位 ${width}`, async ({ page }) => {
  const counts = await mockPlatform(page);
  await page.setViewportSize({ width, height: 900 });
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/config", route => route.fulfill({ json: { groups: [
    { key: "semantic", label: "语义召回", items: [{ key: "embedding_base_url", label: "向量服务地址", value: "https://vector.example.test/v1", secret: false, source: "default" }] },
    { key: "search", label: "搜索", items: [{ key: "tavily_api_key", label: "API Key", value: "", secret: true, source: "default" }] },
  ] } }));
  await page.goto("/settings?section=diagnostics");
  await expect(page.getByText("知识检索（语义召回）", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "前往平台配置" }).first().click();
  await expect(page).toHaveURL(/section=platform&service=semantic/);
  const target = page.getByRole("region", { name: "知识检索（语义召回）", exact: true });
  await expect(target).toBeFocused();
  await expect(page.getByRole("button", { name: "存储与知识", exact: true })).toHaveAttribute("aria-pressed", "true");
  await target.getByRole("button", { name: "配置 知识检索（语义召回）", exact: true }).click();
  await expect(page.getByRole("dialog").getByLabel("向量服务地址", { exact: true })).toHaveValue("https://vector.example.test/v1");
  await page.keyboard.press("Escape");
  await page.reload();
  await expect(target).toBeFocused();
  await expect(target).toBeInViewport();
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  await page.screenshot({ path: `${process.env.TEMP}/semantic-config-deeplink-${width}.png` });
  await page.getByRole("button", { name: "搜索采集", exact: true }).click();
  await expect(page.getByRole("region", { name: "Tavily", exact: true })).toBeVisible();
  expect(counts.save).toBe(0);
  expect(counts.verify).toBe(0);
  expect(errors).toEqual([]);
});

for (const width of [1440, 390]) test(`通知配置说明不重复且字段帮助独立 ${width}`, async ({ page }) => {
  await mockPlatform(page);
  await page.setViewportSize({ width, height: 900 });
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const item = (key: string) => ({ key, label: key, value: "", secret: key === "smtp_password", source: "default" });
  await page.route("**/api/config", route => route.fulfill({ json: { groups: [
    { key: "email", label: "邮件 SMTP", items: ["smtp_enabled", "smtp_host", "smtp_port", "smtp_user", "smtp_password", "smtp_from", "smtp_use_ssl"].map(item) },
    { key: "slack", label: "Slack", items: ["slack_enabled", "slack_webhook_url", "slack_bot_token", "slack_channel_id"].map(item) },
  ] } }));
  await page.goto("/settings?section=platform");
  await page.getByRole("button", { name: "通知", exact: true }).click();
  for (const name of ["邮件 SMTP", "Slack"]) {
    const trigger = page.getByRole("region", { name, exact: true }).getByRole("button", { name: "配置说明" });
    await trigger.click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toContainText(`${name} 配置说明`);
    await expect(dialog.locator("ol")).toHaveCount(1);
    await expect(dialog).toContainText(name === "Slack" ? "files:write" : "465");
    await expect(dialog).not.toContainText(name === "Slack" ? "465" : "files:write");
    await page.evaluate(() => Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {}))));
    expect((await new AxeBuilder({ page }).include('[role="dialog"]').analyze()).violations).toEqual([]);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await dialog.screenshot({ path: `${process.env.TEMP}/notification-guide-${name === "Slack" ? "slack" : "smtp"}-${width}.png` });
    await page.keyboard.press("Escape");
    await expect(trigger).toBeFocused();
  }
  await page.getByRole("button", { name: "配置 邮件 SMTP", exact: true }).click();
  await page.getByLabel("smtp_host", { exact: true }).fill("smtp.draft.invalid");
  const passwordField = page.getByLabel("smtp_password", { exact: true }).locator("..");
  await passwordField.getByText("如何填写", { exact: true }).click();
  await expect(passwordField.locator("li")).toHaveCount(1);
  await expect(passwordField).toContainText("留空保留");
  await expect(passwordField).not.toContainText("465");
  await expect(page.getByLabel("smtp_host", { exact: true })).toHaveValue("smtp.draft.invalid");
  await expect(page.locator("vite-error-overlay")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("通知可编辑保存，历史兼容参数隐藏而不删除", async ({ page }) => {
  await mockPlatform(page);
  const values: unknown[] = [];
  const item = (key: string, label: string, value: string, secret = false) => ({ key, label, value, secret, source: "override" });
  await page.route("**/api/config", route => route.fulfill({ json: { groups: [
    { key: "email", label: "邮件 SMTP", items: [item("smtp_host", "SMTP 服务器", "smtp.example.invalid"), item("smtp_password", "SMTP 密码/授权码", "····demo", true)] },
    { key: "slack", label: "Slack", items: [item("slack_bot_token", "Bot Token（发送文件必填）", "····demo", true), item("slack_channel_id", "目标频道 ID", "C123")] },
    { key: "data_prep", label: "历史流程", items: [item("data_prep_raw_retention_days", "保留天数", "7")] },
  ] } }));
  await page.route("**/api/config/batch", async route => { values.push(route.request().postDataJSON()); await route.fulfill({ json: { ok: true } }); });
  await page.goto("/settings?section=platform");
  await page.getByRole("button", { name: "通知", exact: true }).click();
  await expect(page.getByRole("button", { name: "历史兼容", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "配置 邮件 SMTP", exact: true }).click();
  await page.getByLabel("SMTP 服务器", { exact: true }).fill("smtp.changed.invalid");
  await page.getByRole("button", { name: "保存配置", exact: true }).click();
  await expect.poll(() => values.length).toBe(1);
  expect(values[0]).toEqual({ values: { smtp_host: "smtp.changed.invalid" } });
  await page.getByRole("region", { name: "Slack", exact: true }).getByRole("button", { name: "配置说明" }).click();
  await expect(page.getByRole("dialog")).toContainText("files:write");
  await page.keyboard.press("Escape");
  await page.screenshot({ path: `${process.env.TEMP}/platform-notifications.png` });
});

test("检查管理与账号分区，状态日志两行且长日志可查看", async ({ page }) => {
  await mockPlatform(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  const message = "合成检查日志：" + "用于验证长日志不会撑高列表。".repeat(15);
  await page.route("**/api/config", route => route.fulfill({ json: { groups: [
    { key: "cookies", label: "共享账号", items: [{ key: "mc_cookie_xhs", label: "小红书 Cookie", value: "····demo", secret: true, source: "override", health: { status: "valid", message, checked_at: "2026-09-15T20:17:08" } }] },
    { key: "cookie_health", label: "Cookie 健康巡检", items: [
      { key: "cookie_health_scan_enabled", label: "启用定时巡检", value: "true", secret: false, source: "override", choices: ["True", "False"] },
      { key: "cookie_health_scan_interval_hours", label: "巡检间隔（小时）", value: "96", secret: false, source: "override", type: "number", minimum: 1 },
    ] },
  ] } }));
  await page.goto("/settings?section=platform");
  await page.getByRole("button", { name: "共享账号", exact: true }).click();
  const management = page.getByRole("region", { name: "Cookie 检查管理", exact: true });
  await expect(management).toContainText("定时巡检");
  await expect(management).toContainText("96");
  await expect(management).toContainText("开启");
  await expect(page.getByTestId("config-service-grid")).not.toContainText("采集账号巡检");
  const account = page.getByRole("region", { name: "小红书", exact: true });
  await expect(account).toContainText("上次检查：有效");
  const rowBounds = (await account.boundingBox())!;
  const summaryBounds = (await account.getByTestId("config-service-summary").boundingBox())!;
  expect(summaryBounds.x - rowBounds.x).toBeLessThan(rowBounds.width * 0.3);
  expect((await account.boundingBox())!.height).toBeLessThanOrEqual(100);
  await account.getByRole("button", { name: "查看 小红书 检查日志", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText(message);
  await page.evaluate(() => Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {}))));
  expect((await new AxeBuilder({ page }).include('[role="dialog"]').analyze()).violations).toEqual([]);
  await page.keyboard.press("Escape");
  await management.screenshot({ path: `${process.env.TEMP}/platform-cookie-management.png` });
  await page.getByTestId("config-service-grid").screenshot({ path: `${process.env.TEMP}/platform-cookie-status-lines.png` });
  await management.getByRole("button", { name: "配置 采集账号巡检", exact: true }).click();
  await expect(page.getByLabel("巡检间隔（小时）", { exact: true })).toHaveValue("96");
  await expect(page.getByLabel("启用定时巡检", { exact: true })).toHaveValue("True");
  await page.keyboard.press("Escape");
  await page.getByLabel("查找服务").fill("巡检");
  await expect(management).toBeVisible();
  await expect(page.getByTestId("config-service-grid")).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 900 });
  await page.getByRole("button", { name: "清除搜索", exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await management.screenshot({ path: `${process.env.TEMP}/platform-cookie-management-mobile.png` });
});

test("不同按钮数量的服务摘要与操作列对齐", async ({ page }) => {
  await mockPlatform(page);
  const item = (key: string, label: string, value: string) => ({ key, label, value, secret: false, source: "override" });
  await page.route("**/api/config", route => route.fulfill({ json: { groups: [
    { key: "proxy", label: "代理池", items: [item("mc_enable_ip_proxy", "启用代理池", "False"), item("mc_ip_proxy_provider", "代理商", "static")] },
    { key: "mc_cdp", label: "采集浏览器", items: [item("mc_enable_cdp_mode", "使用服务器浏览器", "True")] },
    { key: "semantic", label: "知识检索", items: [item("embedding_enabled", "启用语义召回", "True")] },
    { key: "mysql", label: "数据存储", items: [item("db_backend", "存储方式", "sqlite")] },
    { key: "checkpoint", label: "断点续跑", items: [item("checkpoint_enabled", "启用断点续跑", "True")] },
    { key: "library_dedup", label: "知识维护", items: [item("library_dedup_scan_enabled", "启用知识库巡检", "True")] },
  ] } }));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/settings?section=platform");
  for (const category of ["网络与浏览器", "存储与知识"]) {
    await page.getByRole("button", { name: category, exact: true }).click();
    const summaries = await page.getByTestId("config-service-summary").evaluateAll(elements => elements.map(element => element.getBoundingClientRect().x));
    expect(new Set(summaries).size).toBe(1);
    const actions = await page.getByRole("button", { name: "配置说明", exact: true }).evaluateAll(elements => elements.map(element => element.getBoundingClientRect().x));
    expect(new Set(actions).size).toBe(1);
    await page.getByTestId("config-service-grid").screenshot({ path: `${process.env.TEMP}/platform-aligned-${category}.png` });
  }
});

for (const width of [1440, 390]) test(`紧凑服务行恢复配置说明，字段帮助不丢失输入 ${width}`, async ({ page }) => {
  await mockPlatform(page);
  await page.setViewportSize({ width, height: 900 });
  await page.goto("/settings?section=platform");
  const service = page.getByRole("region", { name: "Firecrawl", exact: true });
  expect((await service.boundingBox())!.height).toBeLessThanOrEqual(width === 1440 ? 120 : 200);
  await service.getByRole("button", { name: "配置说明", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("Firecrawl 地址");
  await expect(page.getByRole("dialog")).toContainText("API Key");
  await expect(page.getByRole("dialog").getByRole("link", { name: "Firecrawl 官网" })).toHaveAttribute("href", "https://www.firecrawl.dev");
  await page.evaluate(() => Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {}))));
  expect((await new AxeBuilder({ page }).include('[role="dialog"]').analyze()).violations).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("dialog").screenshot({ path: `${process.env.TEMP}/platform-config-guide-${width}.png` });
  await page.keyboard.press("Escape");
  await expect(service.getByRole("button", { name: "配置说明", exact: true })).toBeFocused();
  await service.getByRole("button", { name: "配置 Firecrawl", exact: true }).click();
  await page.getByLabel("Firecrawl 地址", { exact: true }).fill("https://draft.example.test");
  await page.getByRole("dialog").getByText("如何填写", { exact: true }).first().click();
  await expect(page.getByRole("dialog")).toContainText("服务器");
  await expect(page.getByLabel("Firecrawl 地址", { exact: true })).toHaveValue("https://draft.example.test");
});

test("一键检查共享 Cookie：确认后逐项反馈，跳过空配置且不重复发送", async ({ page }) => {
  await mockPlatform(page);
  const calls: string[] = [];
  await page.route("**/api/config", route => route.fulfill({ json: { groups: [{ key: "cookies", label: "平台 Cookie", items: [
    { key: "mc_cookie_xhs", label: "小红书 Cookie", value: "····demo", secret: true, source: "override" },
    { key: "mc_cookie_dy", label: "抖音 Cookie", value: "····demo", secret: true, source: "override" },
    { key: "jd_cookie", label: "京东 Cookie", value: "", secret: true, source: "env" },
  ] }] } }));
  await page.route("**/api/config/verify", async route => {
    const target = route.request().postDataJSON().target;
    calls.push(target);
    await new Promise(resolve => setTimeout(resolve, 500));
    return route.fulfill({ json: { ok: target === "mc_cookie_xhs", detail: target === "mc_cookie_xhs" ? "登录有效" : "Cookie 已失效" } });
  });
  await page.goto("/settings?section=platform");
  await page.getByRole("button", { name: "共享账号", exact: true }).click();
  await page.getByRole("button", { name: "一键检查 Cookie 状态", exact: true }).click();
  expect(calls).toEqual([]);
  await expect(page.getByRole("dialog")).toContainText("2 个已配置");
  await page.getByRole("button", { name: "开始检查", exact: true }).click();
  await expect(page.getByRole("button", { name: "一键检查 Cookie 状态", exact: true })).toBeDisabled();
  await expect(page.getByRole("region", { name: "小红书", exact: true })).toContainText("登录有效");
  await expect(page.getByRole("region", { name: "抖音", exact: true })).toContainText("Cookie 已失效");
  await expect(page.getByRole("status").filter({ hasText: "批量检查完成" })).toContainText("2/2");
  expect(calls).toEqual(["mc_cookie_xhs", "mc_cookie_dy"]);
  await expect(page.getByRole("button", { name: "检查 京东", exact: true })).toBeDisabled();
  await page.getByTestId("config-service-grid").screenshot({ path: `${process.env.TEMP}/platform-cookie-batch-results.png` });
});

for (const outcome of ["stop", "unknown", "leave"]) test(`批量 Cookie 检查停止边界 ${outcome}`, async ({ page }) => {
  await mockPlatform(page);
  const calls: string[] = [];
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/config", route => route.fulfill({ json: { groups: [{ key: "cookies", label: "平台 Cookie", items: ["mc_cookie_xhs", "mc_cookie_dy"].map(key => ({ key, label: key, value: "····demo", secret: true, source: "override" })) }] } }));
  await page.route("**/api/config/verify", async route => {
    calls.push(route.request().postDataJSON().target);
    await pending;
    await route.fulfill(outcome === "unknown" ? { status: 503, json: { detail: "隔离错误" } } : { json: { ok: true, detail: "登录有效" } });
  });
  await page.goto("/settings?section=platform");
  await page.getByRole("button", { name: "共享账号", exact: true }).click();
  await page.getByRole("button", { name: "一键检查 Cookie 状态", exact: true }).click();
  await page.getByRole("button", { name: "开始检查", exact: true }).click();
  await expect.poll(() => calls.length).toBe(1);
  if (outcome === "stop") await page.getByRole("button", { name: "停止后续检查" }).click();
  if (outcome === "leave") await page.getByRole("navigation", { name: "设置分区" }).getByRole("button", { name: "我的设置" }).click();
  release();
  if (outcome !== "leave") await expect(page.getByRole("status").filter({ hasText: "批量检查已停止" })).toContainText("1/2");
  else { await page.waitForResponse(response => response.url().endsWith("/api/config/verify")); await page.waitForTimeout(100); }
  expect(calls).toEqual(["mc_cookie_xhs"]);
});

async function mockPlatform(page: Page, role = "admin") {
  const counts = { delete: 0, verify: 0, models: 0, save: 0 };
  const item = (key: string, label: string, value: string, extra = {}) => ({ key, label, value, secret: false, source: "override", default_value: "False", ...extra });
  const groups = [
    { key: "search", label: "搜索与采集服务", items: [item("firecrawl_base_url", "Firecrawl 地址", "https://old.example.test", { default_value: "https://default.example.test" }), item("firecrawl_api_key", "Firecrawl API Key", "····1234", { secret: true })] },
    { key: "proxy", label: "代理池", items: [item("mc_enable_ip_proxy", "启用代理池", "False", { choices: ["True", "False"] }), item("mc_ip_proxy_provider", "代理商", "static", { choices: ["static", "kuaidaili", "wandouhttp"] }), item("mc_static_proxy_url", "静态代理 URL", "····1234", { secret: true }), item("mc_kdl_user_name", "快代理用户名", "demo")] },
    { key: "data_prep", label: "数据准备模式", items: [item("data_prep_mode_enabled", "启用数据准备模式", "False", { choices: ["True", "False"] }), item("data_prep_raw_retention_days", "原始制品保留天数", "30")] },
  ];
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: { user_id: "audit", username: "audit", display_name: "合成账号", role } });
    if (path === "/api/overview") return route.fulfill({ json: { collectors: [], connectors: {}, connectors_enabled: {}, scheduler: { enabled: false, active_count: 0 } } });
    if (path === "/api/config") return route.fulfill({ json: { groups } });
    if (path === "/api/config/models") counts.models++;
    if (path === "/api/config/verify") { counts.verify++; return route.fulfill({ json: { ok: true, detail: "地址可达" } }); }
    if (path === "/api/config/batch") { counts.save++; return route.fulfill({ json: { ok: true } }); }
    if (route.request().method() === "DELETE") { counts.delete++; return route.fulfill({ json: { ok: true } }); }
    return route.fulfill({ status: 404, json: { detail: "隔离接口" } });
  });
  return counts;
}

test("服务一次编辑、密钥掩码、空值与重复提交保护", async ({ page }) => {
  const writes: unknown[] = [];
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: { user_id: "audit", username: "audit", display_name: "合成管理员", role: "admin" } });
    if (path === "/api/overview") return route.fulfill({ json: { collectors: [], connectors: {}, connectors_enabled: {}, scheduler: { enabled: false, active_count: 0 } } });
    if (path === "/api/config") return route.fulfill({ json: { groups: [{ key: "search", label: "搜索与采集服务", items: [
      { key: "firecrawl_base_url", label: "Firecrawl 地址", secret: false, source: "override", value: "https://old.example.test", default_value: "https://default.example.test" },
      { key: "firecrawl_api_key", label: "Firecrawl API Key", secret: true, source: "override", value: "····1234", default_value: "" },
    ] }] } });
    if (path === "/api/config/batch") {
      writes.push(route.request().postDataJSON());
      await new Promise(resolve => setTimeout(resolve, 250));
      return route.fulfill({ json: { ok: true } });
    }
    return route.fulfill({ status: 404, json: { detail: "隔离接口" } });
  });
  await page.goto("/settings?section=platform");
  await page.getByRole("button", { name: "配置 Firecrawl", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "配置 Firecrawl", exact: true });
  await expect(dialog.getByLabel("Firecrawl 地址", { exact: true })).toHaveValue("https://old.example.test");
  await expect(dialog.getByLabel("Firecrawl API Key", { exact: true })).toHaveAttribute("type", "password");
  await dialog.getByLabel("Firecrawl 地址", { exact: true }).fill("");
  await dialog.getByLabel("Firecrawl 地址", { exact: true }).press("Enter");
  await expect(dialog.getByRole("alert")).toContainText("不能为空");
  expect(writes).toHaveLength(0);
  await dialog.getByLabel("Firecrawl 地址", { exact: true }).fill("https://new.example.test");
  await dialog.getByLabel("Firecrawl 地址", { exact: true }).press("Enter");
  await dialog.getByLabel("Firecrawl 地址", { exact: true }).press("Enter");
  await expect(dialog).not.toBeVisible();
  expect(writes).toEqual([{ values: { firecrawl_base_url: "https://new.example.test" } }]);
});

test("恢复默认和外部检查先确认，结果留在服务内", async ({ page }) => {
  const counts = await mockPlatform(page);
  await page.goto("/settings?section=platform");
  await page.getByRole("button", { name: "配置 Firecrawl", exact: true }).click();
  await page.getByRole("button", { name: "恢复 Firecrawl 地址 默认" }).click();
  await expect(page.getByRole("dialog")).toContainText("https://default.example.test");
  expect(counts.delete).toBe(0);
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "恢复 Firecrawl 地址 默认" }).click();
  await page.getByRole("button", { name: "确认恢复默认" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(counts.delete).toBe(1);
  await page.getByRole("button", { name: "检查 Firecrawl", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("不验证 API Key");
  expect(counts.verify).toBe(0);
  await page.getByRole("button", { name: "开始检查" }).click();
  await expect(page.getByRole("status").filter({ hasText: "本次检查通过" })).toContainText("地址可达");
  expect(counts.verify).toBe(1);
  expect(counts.models).toBe(0);
});

test("配置错误可恢复，未知保存不重复提交", async ({ page }) => {
  await mockPlatform(page);
  await page.route("**/api/config", route => route.fulfill({ status: 503, json: { detail: "隔离失败" } }));
  await page.goto("/settings?section=platform");
  await expect(page.getByRole("alert")).toContainText("配置加载失败");
  await page.unroute("**/api/config");
  await page.getByRole("button", { name: "重新加载", exact: true }).click();
  await page.getByRole("button", { name: "配置 Firecrawl", exact: true }).click();
  await page.getByLabel("Firecrawl 地址", { exact: true }).fill("https://new.example.test");
  await page.route("**/api/config/batch", route => route.fulfill({ status: 503, json: { detail: "隔离失败" } }));
  await page.getByRole("button", { name: "保存配置", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("保存结果未知");
  await expect(page.getByRole("button", { name: "保存配置", exact: true })).toBeDisabled();
  await expect(page.getByLabel("Firecrawl 地址", { exact: true })).toHaveValue("https://new.example.test");
});

test("凭据原样保存并可切换显示", async ({ page }) => {
  await mockPlatform(page);
  const writes: unknown[] = [];
  await page.route("**/api/config/batch", route => { writes.push(route.request().postDataJSON()); return route.fulfill({ json: { ok: true } }); });
  await page.goto("/settings?section=platform");
  await page.getByRole("button", { name: "配置 Firecrawl", exact: true }).click();
  await page.getByLabel("Firecrawl API Key", { exact: true }).fill(" secret with spaces ");
  await page.getByRole("button", { name: "显示 Firecrawl API Key", exact: true }).click();
  await expect(page.getByLabel("Firecrawl API Key", { exact: true })).toHaveAttribute("type", "text");
  await page.getByRole("button", { name: "保存配置", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(writes).toEqual([{ values: { firecrawl_api_key: " secret with spaces " } }]);
});

test("按代理商显示字段，历史参数没有假验证入口", async ({ page }) => {
  await mockPlatform(page);
  await page.goto("/settings?section=platform");
  await page.getByRole("button", { name: "网络与浏览器", exact: true }).click();
  await page.getByRole("button", { name: "配置 代理池", exact: true }).click();
  await expect(page.getByLabel("静态代理 URL", { exact: true })).toBeVisible();
  await expect(page.getByLabel("快代理用户名", { exact: true })).toHaveCount(0);
  await page.getByLabel("代理商", { exact: true }).selectOption("kuaidaili");
  await expect(page.getByLabel("快代理用户名", { exact: true })).toBeVisible();
  await expect(page.getByLabel("静态代理 URL", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.getByRole("button", { name: "检查 代理池" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "历史兼容", exact: true })).toHaveCount(0);
  await expect(page.getByRole("region", { name: "制品保留（未开放）" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "配置 历史流程兼容", exact: true })).toHaveCount(0);
  await expect(page.getByLabel("原始制品保留天数")).toHaveCount(0);
});

test("紧凑列表高度、跨分类搜索及未知配置不遗漏", async ({ page }) => {
  await mockPlatform(page);
  await page.setViewportSize({ width: 1440, height: 900 });
  const field = (key: string) => ({ key, label: key, value: "demo", secret: false, source: "override" });
  await page.route("**/api/config", route => route.fulfill({ json: { groups: [
    { key: "search", label: "搜索采集", items: [field("tavily_api_key"), field("firecrawl_base_url"), field("new_search_key")] },
    { key: "future", label: "新增服务", items: [field("future_key")] },
  ] } }));
  await page.goto("/settings?section=platform");
  const a = await page.getByRole("region", { name: "Tavily", exact: true }).boundingBox();
  const b = await page.getByRole("region", { name: "Firecrawl", exact: true }).boundingBox();
  expect(a && b && a.x === b.x && b.y > a.y && a.height <= 120 && b.height <= 120).toBeTruthy();
  await expect(page.getByRole("region", { name: "搜索采集 · 其他配置" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Tavily", exact: true })).toContainText("为任务提供外部资料");
  await expect(page.getByRole("region", { name: "Tavily", exact: true }).locator("summary")).toHaveCount(0);
  await page.evaluate(() => Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {}))));
  await page.getByTestId("config-service-grid").screenshot({ path: `${process.env.TEMP}/platform-config-categories-desktop.png` });
  await page.getByLabel("查找服务").fill("future_key");
  await expect(page.getByRole("region", { name: "新增服务" })).toBeVisible();
  await page.getByRole("button", { name: "其他", exact: true }).click();
  await expect(page.getByLabel("查找服务")).toHaveValue("");
  await page.setViewportSize({ width: 390, height: 900 });
  await page.getByRole("button", { name: "搜索采集", exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.evaluate(() => Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {}))));
  await page.getByTestId("config-service-grid").screenshot({ path: `${process.env.TEMP}/platform-config-categories-mobile.png` });
});

for (const role of ["user", "admin", "super_admin"]) test(`${role} 的平台入口权限`, async ({ page }) => {
  await mockPlatform(page, role);
  await page.goto("/settings?section=platform");
  if (role === "user") await expect(page.getByRole("button", { name: "配置 Firecrawl", exact: true })).toHaveCount(0);
  else await expect(page.getByRole("button", { name: "配置 Firecrawl", exact: true })).toBeVisible();
});

for (const width of [1440, 390]) test(`桌面和窄屏键盘可访问性 ${width}`, async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", e => errors.push(e.message));
  await mockPlatform(page);
  await page.setViewportSize({ width, height: 900 });
  await page.goto("/settings?section=platform");
  await page.getByRole("button", { name: "网络与浏览器", exact: true }).click();
  const trigger = page.getByRole("button", { name: "配置 代理池", exact: true });
  await trigger.click();
  await expect(page.getByLabel("启用代理池", { exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(trigger).toBeFocused();
  await page.getByRole("button", { name: "搜索采集", exact: true }).click();
  await page.getByRole("button", { name: "配置 Firecrawl", exact: true }).click();
  await page.evaluate(() => Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {}))));
  const violations = (await new AxeBuilder({ page }).include('[role="dialog"]').analyze()).violations;
  expect(violations).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: `${process.env.TEMP}/platform-config-editor-${width}.png`, fullPage: true });
  expect(errors).toEqual([]);
});
