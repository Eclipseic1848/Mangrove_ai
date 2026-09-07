import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

const ids = ["deepseek", "qwen", "openai", "anthropic", "gemini", "kimi", "zhipu", "xai"];
const presets = ids.map((id) => ({
  preset_id: id, version: "2026-09-07.1", display_name: id, description: "模型服务",
  recommended_model: `${id}-selected`, models: [`${id}-selected`, `${id}-other`, `${id}-third`],
  help_url: "https://example.com/docs", key_url: "https://example.com/keys",
  region_note: "API 密钥与聊天订阅分开，地域必须一致。",
  model_catalog: [{ model_id: `${id}-selected`, display_name: `${id} 所选模型`, role: "balanced", verified_on: "2026-09-07", source_url: "https://example.com/models" }],
  regions: id === "qwen" ? [{ id: "cn-beijing", label: "中国 · 北京", workspace_required: true }] : [],
}));

async function setup(page: Page, role = "user") {
  const errors: string[] = [];
  page.on("pageerror", (error) => { errors.push(error.message); });
  await page.route("**/api/**", (route) => route.fulfill({ json: { items: [], options: [], default: {}, state: "completed", preference: null } }));
  await page.route("**/api/auth/me", (route) => route.fulfill({ json: { user_id: "owner", username: "owner", role, display_name: "测试用户" } }));
  await page.route("**/api/overview", (route) => route.fulfill({ json: {
    collectors: [], scheduler: { enabled: false, active_count: 0 },
    connectors: { email: false, slack: false, embedding: false, checkpoint: true },
    connectors_enabled: { email: false, slack: false, embedding: false, checkpoint: true },
  } }));
  await page.route("**/api/model-connections/presets", (route) => route.fulfill({ json: { items: presets } }));
  await page.goto("/settings?section=models");
  await expect(page.getByRole("heading", { name: "连接一个模型服务" })).toBeVisible();
  return errors;
}

for (const id of ids) {
  test(`${id}：填写、仅测试选定模型、保存后用于新任务`, async ({ page }) => {
    const errors = await setup(page);
    const calls: unknown[] = [];
    await page.route(`**/api/model-connections/presets/${id}`, async (route) => {
      calls.push(route.request().postDataJSON());
      await route.fulfill({ json: { connection_id: "new-connection", display_name: `${id} 连接`, owner_scope: "user_personal", default_model: `${id}-selected`, available_model_count: 1, models: [{ model_id: `${id}-selected`, display_name: "所选模型", status: "available", enabled: true, is_default: true }] } });
    });
    let preference: unknown;
    await page.route("**/api/model-connections/preferences/default", (route) => {
      if (route.request().method() === "PUT") preference = route.request().postDataJSON();
      return route.fulfill({ json: { preference } });
    });
    await page.getByLabel("模型服务商", { exact: false }).selectOption(id);
    await expect(page.getByLabel("连接名称")).not.toHaveValue("");
    if (id === "qwen") await page.getByLabel("业务空间 ID").fill("workspace-test");
    await expect(page.getByRole("link", { name: "获取 Key" })).toHaveAttribute("href", "https://example.com/keys");
    await page.getByLabel("API Key", { exact: false }).fill("fictional-key");
    await expect(page.getByText(/可能按服务商标准计费/)).toBeVisible();
    await page.getByRole("button", { name: "测试并保存所选模型" }).click();
    expect(calls).toHaveLength(1);
    expect(calls[0]).toMatchObject({ model: `${id}-selected`, api_key: "fictional-key" });
    expect(calls[0]).not.toHaveProperty("verify_all", true);
    await page.getByRole("button", { name: "用于新任务", exact: true }).click();
    expect(preference).toEqual({ connection_id: "new-connection", model_id: `${id}-selected` });
    expect(errors).toEqual([]);
  });
}

test("切换服务商清除密钥；未知结果保留输入并阻止立即重复计费", async ({ page }) => {
  await setup(page);
  await page.getByLabel("API Key", { exact: false }).fill("fictional-key");
  await page.getByLabel("模型服务商", { exact: false }).selectOption("kimi");
  await expect(page.getByLabel("API Key", { exact: false })).toHaveValue("");
  await page.getByLabel("API Key", { exact: false }).fill("another-fictional-key");
  let calls = 0;
  await page.route("**/api/model-connections/presets/kimi", (route) => { calls++; return route.fulfill({ status: 400, json: { detail: "请求结果未知" } }); });
  await page.getByRole("button", { name: "测试并保存所选模型" }).click();
  await expect(page.getByRole("alert")).toContainText("先检查连接列表");
  await expect(page.getByRole("button", { name: "测试并保存所选模型" })).toBeDisabled();
  await expect(page.getByLabel("API Key", { exact: false })).toHaveValue("another-fictional-key");
  expect(calls).toBe(1);
});

test("普通用户能找到本地入口并得到明确的管理员接入路径", async ({ page }) => {
  await setup(page);
  await page.getByRole("button", { name: "本地或局域网模型" }).click();
  await expect(page.getByText(/本地地址由管理员登记/)).toBeVisible();
  await expect(page.getByRole("button", { name: "登记本地模型", exact: true })).toHaveCount(0);
});

test("保存中重复点击不增加测试请求，取消本地配置不发请求", async ({ page }) => {
  await setup(page, "admin");
  let calls = 0;
  let finish: () => void = () => {};
  const waiting = new Promise<void>((resolve) => { finish = resolve; });
  await page.route("**/api/model-connections/presets/deepseek", async (route) => {
    calls++;
    await waiting;
    await route.fulfill({ status: 400, json: { detail: "密钥无效" } });
  });
  await page.getByLabel("API Key").fill("fictional-key");
  const button = page.getByRole("button", { name: "测试并保存所选模型" });
  await button.evaluate((element: HTMLButtonElement) => { element.click(); element.click(); });
  await expect(page.getByLabel("API Key")).toBeDisabled();
  expect(calls).toBe(1);
  finish();
  await expect(page.getByRole("alert")).toContainText("密钥无效");
  await page.getByRole("button", { name: "本地或局域网模型" }).click();
  await page.getByRole("button", { name: "登记本地模型", exact: true }).click();
  let localCalls = 0;
  await page.route("**/api/model-connections/managed", (route) => { localCalls++; return route.fulfill({ json: {} }); });
  await page.getByRole("dialog").getByRole("button", { name: "取消", exact: true }).click();
  expect(localCalls).toBe(0);
});

test("已有连接的待验证型号可单独验证并改为新任务默认", async ({ page }) => {
  await setup(page);
  const pending = { model_id: "second", display_name: "第二模型", status: "pending_validation", enabled: false, is_default: false };
  const connection = { connection_id: "existing", display_name: "已有连接", owner_scope: "user_personal", status: "verified", model: "first", default_model: "first", available_model_count: 1, models: [pending] };
  await page.route("**/api/model-connections", (route) => route.fulfill({ json: { items: [connection] } }));
  let calls = 0;
  await page.route("**/api/model-connections/existing/models/retry", (route) => {
    calls++;
    expect(route.request().postDataJSON()).toEqual({ model_ids: ["second"] });
    pending.status = "available"; pending.enabled = true;
    return route.fulfill({ json: connection });
  });
  await page.reload();
  await page.getByRole("button", { name: "验证 第二模型", exact: true }).click();
  await expect(page.getByRole("button", { name: "设 已有连接 的 第二模型 为新任务默认", exact: true })).toBeVisible();
  expect(calls).toBe(1);
});

test("平台百炼必须填写地域业务空间，切换服务商清除密钥", async ({ page }) => {
  await setup(page, "admin");
  await page.getByRole("tab", { name: "平台连接", exact: true }).click();
  await page.getByRole("button", { name: "添加平台连接" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("模型服务商").selectOption("qwen");
  await dialog.getByLabel("连接名称").fill("百炼团队");
  await dialog.getByLabel("API Key").fill("fictional-key");
  await expect(dialog.getByRole("button", { name: "验证并发布", exact: true })).toBeDisabled();
  await dialog.getByLabel("业务空间 ID").fill("workspace-test");
  await expect(dialog.getByRole("button", { name: "验证并发布", exact: true })).toBeEnabled();
  let body: unknown;
  await page.route("**/api/model-connections/managed/presets/qwen", (route) => {
    body = route.request().postDataJSON();
    return route.fulfill({ json: { connection_id: "team", models: [] } });
  });
  await dialog.getByRole("button", { name: "验证并发布", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(body).toMatchObject({ region: "cn-beijing", workspace_id: "workspace-test", model: "qwen-selected" });
});

for (const theme of ["light", "dark"]) {
  for (const width of [390, 1440]) {
    test(`模型设置视觉与键盘：${theme} ${width}`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 900 });
      await page.addInitScript((value) => localStorage.setItem("mangrove_theme", value), theme);
      const errors = await setup(page);
      await page.getByLabel("模型服务商").focus();
      await page.keyboard.press("Tab");
      await expect(page.locator(":focus")).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze()).violations).toEqual([]);
      await page.screenshot({ path: testInfo.outputPath(`models-${theme}-${width}.png`), fullPage: true });
      expect(errors).toEqual([]);
    });
  }
}

test("窄屏管理员读取本地模型列表不推理，选择后只测试该模型", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.addInitScript(() => localStorage.setItem("mangrove_theme", "dark"));
  const errors = await setup(page, "admin");
  let discovery: any;
  let saved: any;
  await page.route("**/api/model-connections/managed/discover", (route) => {
    discovery = route.request().postDataJSON();
    return route.fulfill({ json: { models: ["local-a", "local-b"], detected_api_formats: [], manual_models_required: false } });
  });
  await page.route("**/api/model-connections/managed", (route) => {
    saved = route.request().postDataJSON();
    return route.fulfill({ json: { connection_id: "local", display_name: "本地", model: "local-b", models: [] } });
  });
  await page.getByRole("button", { name: "本地或局域网模型" }).click();
  await page.getByRole("button", { name: "登记本地模型", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByLabel("模型服务地址")).toHaveValue("http://localhost:11434/v1");
  await dialog.getByRole("button", { name: "读取可用模型" }).click();
  expect(discovery.probe_protocols).toBe(false);
  await dialog.getByLabel("本地模型 ID").fill("local-b");
  await dialog.screenshot({ path: testInfo.outputPath("local-dark-mobile.png") });
  await dialog.getByRole("button", { name: "验证并发布", exact: true }).click();
  expect(saved.models).toEqual(["local-b"]);
  expect(saved.api_key).toBe("");
  await expect(dialog).toHaveCount(0);
  expect(errors).toEqual([]);
});
