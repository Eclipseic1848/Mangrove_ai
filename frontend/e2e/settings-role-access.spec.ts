import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

test("平台配置可编辑验证再保存，窄屏不溢出", async ({ page }, testInfo) => {
  await mockSettings(page, "admin");
  await page.route("**/api/model-connections", route => route.fulfill({ json: { items: [UX_CONNECTION] } }));
  const configuration = { display_name: "DeepSeek", base_url: "https://models.example/v1", model: "deepseek-v4-flash", models: ["deepseek-v4-flash"], api_format: "openai_chat_completions", locality: "public_external", thinking: "default", version: "synthetic-version", has_key: true, superseded: false };
  await page.route("**/api/model-connections/ux-shared/configuration", route => route.fulfill({ json: configuration }));
  let tested = 0, applied = 0;
  await page.route("**/api/model-connections/ux-shared/configuration/test", route => {
    tested++;
    expect(route.request().postDataJSON().api_key).toBeNull();
    return route.fulfill({ json: { state: "verified", results: [] } });
  });
  await page.route("**/api/model-connections/ux-shared/configuration/apply", route => { applied++; return route.fulfill({ json: { state: "applied", connection_id: "new-id" } }); });
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/settings?section=models&scope=platform");
  await page.getByRole("button", { name: "管理 DeepSeek · 合成模型", exact: true }).click();
  await page.getByRole("button", { name: "编辑配置", exact: true }).click();
  const editor = page.getByRole("region", { name: "编辑模型配置" });
  await expect(editor.getByLabel("API 地址", { exact: true })).toHaveValue(configuration.base_url);
  await editor.getByLabel("名称", { exact: true }).fill("团队云端模型");
  await editor.getByLabel("模型 ID（多个用逗号分隔）", { exact: true }).fill("new-model");
  await expect(editor.getByRole("combobox", { name: /连接首选模型/ })).toHaveValue("new-model");
  await editor.getByLabel("API 地址", { exact: true }).fill(configuration.base_url + "/");
  await expect(editor.getByRole("button", { name: "验证配置", exact: true })).toBeEnabled();
  await expect(editor.getByRole("button", { name: "保存配置", exact: true })).toBeDisabled();
  await page.setViewportSize({ width: 390, height: 844 });
  await editor.scrollIntoViewIfNeeded();
  await expect(editor).toBeVisible();
  expect((await new AxeBuilder({ page }).include('[aria-label="编辑模型配置"]').analyze()).violations).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("configuration-mobile.png"), fullPage: true });
  await editor.getByRole("button", { name: "验证配置", exact: true }).click();
  await expect(editor.getByText(/验证通过/)).toBeVisible();
  await editor.getByRole("button", { name: "保存配置", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(tested).toBe(1); expect(applied).toBe(1); expect(errors).toEqual([]);
});

test("配置拒绝可修正，刷新后的验证核对不会锁死或重复调用", async ({ page }) => {
  await mockSettings(page, "admin");
  const configuration = { display_name: "DeepSeek", base_url: "https://models.example/v1", model: "deepseek-v4-flash", models: ["deepseek-v4-flash"], api_format: "openai_chat_completions", locality: "public_external", thinking: "default", version: "v1", has_key: true, superseded: false };
  await page.route("**/api/model-connections", route => route.fulfill({ json: { items: [UX_CONNECTION] } }));
  await page.route("**/api/model-connections/ux-shared/configuration", route => route.fulfill({ json: configuration }));
  let tests = 0;
  await page.route("**/api/model-connections/ux-shared/configuration/test", route => { tests++; return route.fulfill({ status: 422, json: { detail: "请填写名称" } }); });
  await page.goto("/settings?section=models&scope=platform");
  const open = async () => { await page.getByRole("button", { name: "管理 DeepSeek · 合成模型", exact: true }).click(); await page.getByRole("button", { name: "编辑配置", exact: true }).click(); };
  await open();
  const editor = page.getByRole("region", { name: "编辑模型配置" });
  await editor.getByLabel("名称", { exact: true }).fill("");
  await editor.getByRole("button", { name: "验证配置", exact: true }).click();
  await expect(editor.getByLabel("名称", { exact: true })).toBeEnabled();
  await expect(editor.getByRole("button", { name: "关闭编辑" })).toBeEnabled();
  await page.evaluate(() => sessionStorage.setItem("model-configuration-operation:admin-a:ux-shared", "pending-test"));
  await page.route("**/api/model-connections/ux-shared/configuration/operations/pending-test", route => route.fulfill({ json: { state: "testing", configuration } }));
  await page.reload(); await open();
  await expect(editor.getByRole("button", { name: "核对验证结果" })).toBeEnabled();
  await editor.getByRole("button", { name: "核对验证结果" }).click();
  await expect(editor.getByRole("button", { name: "关闭编辑" })).toBeEnabled();
  await editor.getByRole("button", { name: "关闭编辑" }).click();
  expect(tests).toBe(1);
});

test("模型配置从平台模型开始，添加个人模型可达且列表失败可恢复", async ({ page }) => {
  await mockSettings(page, "user");
  await page.goto("/settings?section=models");
  await expect(page.getByRole("tab", { name: "平台可用连接" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("button", { name: "添加自己的模型", exact: true }).click();
  await expect(page.getByLabel("模型服务商", { exact: false })).toBeVisible();
  await page.getByRole("tab", { name: "平台可用连接" }).click();
  await page.route("**/api/model-connections", route => route.fulfill({ status: 503, json: { detail: "合成网络故障" } }));
  await page.reload();
  await expect(page.getByRole("alert").filter({ hasText: "合成网络故障" })).toBeVisible();
  await expect(page.getByRole("button", { name: "重新加载", exact: true })).toBeVisible();
  await expect(page.getByText("还没有平台连接", { exact: true })).toHaveCount(0);
});

test("旧对话入口隐藏，概览统一进入工作台，旧地址保留", async ({ page }, testInfo) => {
  await mockSettings(page, "user");
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "概览", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "旧版对话", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "发起采集对话" })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("mangrove-hidden-chat.png") });
  await page.getByRole("button", { name: /我的会话/ }).click();
  await expect(page).toHaveURL(/\/data-prep$/);
  await page.goto("/");
  await page.getByRole("button", { name: "创建数据任务" }).click();
  await expect(page).toHaveURL(/\/data-prep$/);
  await page.route("**/api/conversations", route => route.fulfill({ json: [] }));
  await page.goto("/chat");
  await expect(page).toHaveURL(/\/chat$/);
  await expect(page.locator("textarea")).toBeVisible();
  expect(errors).toEqual([]);
});

test("退出所有设备使用警示按钮且取消不退出", async ({ page }, testInfo) => {
  await mockSettings(page, "user");
  await page.goto("/settings");
  const button = page.getByRole("button", { name: "退出所有设备", exact: true });
  await expect(button).toBeVisible();
  await expect(button).toHaveClass(/border-red-300/);
  await button.focus();
  await expect(button).toBeFocused();
  page.once("dialog", dialog => dialog.dismiss());
  await button.press("Enter");
  await expect(page).toHaveURL(/\/settings$/);
  await expect(button).toBeEnabled();
  await button.screenshot({ path: testInfo.outputPath("mangrove-logout-button.png") });
});

const PRESETS = [
  {
    preset_id: "deepseek",
    version: "2026-07-30.1",
    display_name: "DeepSeek",
    description: "适合中文、推理和通用 Agent 任务",
    recommended_model: "deepseek-v4-flash",
    models: ["deepseek-v4-flash", "deepseek-v4-pro"],
    model_catalog: [
      {
        model_id: "deepseek-v4-flash",
        display_name: "DeepSeek V4 Flash",
        role: "balanced",
      },
      {
        model_id: "deepseek-v4-pro",
        display_name: "DeepSeek V4 Pro",
        role: "quality",
      },
    ],
    help_url: "https://api-docs.deepseek.com/",
  },
  {
    preset_id: "openai",
    version: "2026-07-30.1",
    display_name: "OpenAI",
    description: "原生 Responses API",
    recommended_model: "gpt-5.6-terra",
    models: ["gpt-5.6-terra"],
    model_catalog: [{
      model_id: "gpt-5.6-terra",
      display_name: "GPT-5.6 Terra",
      role: "balanced",
    }],
    help_url: "https://developers.openai.com/api/docs/models",
  },
];

const UX_CONNECTION = {
  connection_id: "ux-shared", owner_scope: "platform_shared", preset_id: "deepseek",
  display_name: "合成共享模型", model: "deepseek-v4-flash", locality: "cloud", status: "verified", available_model_count: 1,
  models: [{ model_id: "deepseek-v4-flash", display_name: "合成模型", status: "available", enabled: true, is_default: true }],
};

test("平台模型仅分本地与云端，三角色去重并移除过期型号和导入名称", async ({ page }, testInfo) => {
  const localNames = ["Qwen3.8-27B", "Qwen3.6-35B-A3B", "Qwen3.5-35B-A3B", "Qwen3-30B-A3B"];
  const makeModel = (model: string, current = true) => ({ model_id: model, display_name: model, enabled: true, status: "available", is_default: true, current_catalog: current });
  const local = localNames.map((model, index) => ({ ...UX_CONNECTION, connection_id: `local-${index}`, preset_id: null, display_name: `导入的本地模型 · ${model}`, locality: "managed_private", model, models: [makeModel(model)] }));
  const cloud = [
    { ...UX_CONNECTION, connection_id: "cloud-qwen", preset_id: "qwen", display_name: "导入的平台 阿里百炼", models: [makeModel("qwen3.8-max"), makeModel("qwen3.7-max", false)] },
    { ...UX_CONNECTION, connection_id: "cloud-deepseek", display_name: "导入的平台 DeepSeek", models: [makeModel("deepseek-flash"), makeModel("deepseek-v4-flash", false)] },
  ];
  for (const role of ["user", "admin", "super_admin"] as const) {
    await page.unrouteAll({ behavior: "wait" });
    await mockSettings(page, role);
    await page.route("**/api/model-connections", route => route.fulfill({ json: { items: [...local, { ...local[1], connection_id: "duplicate-local" }, ...cloud, { ...cloud[1], connection_id: "duplicate-cloud" }] } }));
    await page.goto("/settings?section=models");
    const catalog = page.getByRole("region", { name: "平台模型清单" });
    await expect(catalog).toBeVisible();
    await expect(catalog.getByRole("heading", { level: 3 })).toHaveText(["本地模型", "云端模型"]);
    for (const model of localNames) await expect(catalog.getByText(model, { exact: true })).toHaveCount(1);
    await expect(catalog.getByRole("heading", { level: 4 })).toHaveCount(2);
    await expect(catalog.getByRole("heading", { name: "DeepSeek", exact: true })).toBeVisible();
    await expect(catalog.getByRole("heading", { name: "阿里百炼", exact: true })).toBeVisible();
    await expect(catalog.getByText(/导入|qwen3.7|deepseek-v4-flash/)).toHaveCount(0);
    await expect(catalog.getByText("deepseek-flash", { exact: true })).toHaveCount(1);
    expect((await new AxeBuilder({ page }).include('[aria-label="平台模型清单"]').analyze()).violations).toEqual([]);
    await catalog.screenshot({ path: testInfo.outputPath(`${role}-catalog.png`) });
  }
});

test("平台清单去重不丢失管理员异常型号与重复连接维护", async ({ page }) => {
  await mockSettings(page, "admin");
  await page.route("**/api/model-connections", route => route.fulfill({ json: { items: [
    { ...UX_CONNECTION, models: [...UX_CONNECTION.models, { ...UX_CONNECTION.models[0], model_id: "failed", display_name: "失败型号", status: "network_unreachable", enabled: false }, { ...UX_CONNECTION.models[0], model_id: "disabled", display_name: "停用型号", status: "disabled", enabled: false }] },
    { ...UX_CONNECTION, connection_id: "duplicate" },
  ] } }));
  await page.goto("/settings?section=models");
  await expect(page.getByRole("region", { name: "平台模型清单" }).getByText("合成模型", { exact: true })).toHaveCount(1);
  await expect(page.getByText("连接维护（高级）", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "管理 DeepSeek · 合成模型", exact: true }).click();
  await expect(page.getByRole("button", { name: "重试 失败型号", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "启用 停用型号", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "停用连接", exact: true })).toHaveCount(1);
  await page.getByLabel("使用的连接记录").selectOption("duplicate");
  await expect(page.getByRole("button", { name: "停用连接", exact: true })).toHaveCount(1);
});

test("自定义云供应商名称简化不会合并不同供应商的同名模型", async ({ page }) => {
  await mockSettings(page, "user");
  await page.route("**/api/model-connections", route => route.fulfill({ json: { items: ["甲供应商", "乙供应商"].map((name, index) => ({
    ...UX_CONNECTION, connection_id: `custom-${index}`, preset_id: null, display_name: `导入的平台 ${name}`,
  })) } }));
  await page.goto("/settings?section=models");
  const catalog = page.getByRole("region", { name: "平台模型清单" });
  await expect(catalog.getByText("合成模型", { exact: true })).toHaveCount(2);
  await expect(catalog.getByRole("heading", { name: "甲供应商", exact: true })).toBeVisible();
  await expect(catalog.getByRole("heading", { name: "乙供应商", exact: true })).toBeVisible();
  await expect(catalog.getByText(/导入/)).toHaveCount(0);
});

test("统一管理移除退役导入型号，保留异常自建与不同本地接入记录", async ({ page }, testInfo) => {
  await mockSettings(page, "super_admin");
  const local = (id: string, name: string, locality: string, role: string, status = "available") => ({ ...UX_CONNECTION,
    connection_id: id, preset_id: null, display_name: `导入的本地模型 · ${name}`, model: name, locality,
    models: [{ ...UX_CONNECTION.models[0], model_id: name, display_name: name, catalog_role: role, status, enabled: status === "available" }],
  });
  let names = ["Qwen3.6-35B-A3B"];
  await page.route("**/api/config/models*", route => route.fulfill({ json: { models: { local: names } } }));
  await page.route("**/api/model-connections", route => route.fulfill({ json: { items: [
    local("main-local", "Qwen3.6-35B-A3B", "managed_private", "legacy_imported"),
    local("other-local", "Qwen3.6-35B-A3B", "local", "legacy_imported"),
    local("old-local", "Qwen3.8-27B-FP8", "managed_private", "legacy_imported", "model_access_denied"),
    local("custom-local", "自建待修复模型", "local", "custom", "network_unreachable"),
  ] } }));
  await page.goto("/settings?section=models");
  const catalog = page.getByRole("region", { name: "平台模型清单" });
  await expect(catalog.getByText("Qwen3.8-27B-FP8", { exact: true })).toHaveCount(0);
  await expect(catalog.getByText("Qwen3.6-35B-A3B", { exact: true })).toHaveCount(1);
  await expect(catalog.getByText("自建待修复模型", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "管理 Qwen3.6-35B-A3B", exact: true }).click();
  await page.getByLabel("使用的连接记录").selectOption("other-local");
  await expect(page.getByText("连接编号 other-lo", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByRole("dialog").screenshot({ path: testInfo.outputPath("management-mobile.png") });
  await page.getByRole("button", { name: "完成管理", exact: true }).click();
  names = [];
  await page.reload();
  await expect(catalog.getByText("Qwen3.6-35B-A3B", { exact: true })).toHaveCount(0);
  await expect(catalog.getByText("自建待修复模型", { exact: true })).toBeVisible();
  await page.goto("/settings?section=platform");
  await page.getByRole("link", { name: "管理平台模型", exact: true }).click();
  await expect(page).toHaveURL(/section=models&scope=platform/);
});

test("模型默认选择共用设置入口，窄屏添加前可见计费提示并支持键盘切换", async ({ page }) => {
  await mockSettings(page, "user");
  await page.route("**/api/model-connections", route => route.fulfill({ json: { items: [UX_CONNECTION] } }));
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/settings?section=models");
  await expect(page.getByLabel("默认任务模型", { exact: true })).toBeVisible();
  const platformTab = page.getByRole("tab", { name: "平台可用连接" });
  await platformTab.focus();
  await page.keyboard.press("ArrowLeft");
  await expect(page.getByRole("tab", { name: "我的连接" })).toBeFocused();
  await expect(page.getByRole("tab", { name: "我的连接" })).toHaveAttribute("aria-selected", "true");
  await page.getByRole("button", { name: "添加自己的模型", exact: true }).click();
  await page.getByLabel("API Key", { exact: false }).fill("synthetic-key");
  const notice = page.getByText(/仅发送简短测试，可能按服务商标准计费/);
  const submit = page.getByRole("button", { name: "测试并保存所选模型", exact: true });
  await expect(notice).toBeVisible();
  expect((await notice.boundingBox())!.y).toBeLessThan((await submit.boundingBox())!.y);
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
});

test("平台停用需要确认，停用后不能作为默认模型，删除防止重复提交", async ({ page }) => {
  await mockSettings(page, "admin");
  let disabled = false;
  let deletes = 0;
  await page.route("**/api/model-connections", route => route.fulfill({ json: { items: [{ ...UX_CONNECTION, status: disabled ? "disabled" : "verified" }] } }));
  await page.route("**/api/model-connections/ux-shared", async route => {
    if (route.request().method() === "DELETE") {
      deletes++;
      await new Promise(resolve => setTimeout(resolve, 400));
    } else disabled = true;
    await route.fulfill({ json: {} });
  });
  await page.goto("/settings?section=models");
  await page.getByRole("button", { name: "管理 DeepSeek · 合成模型", exact: true }).click();
  await page.getByRole("button", { name: "停用连接", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "停用平台连接" })).toBeVisible();
  expect(disabled).toBe(false);
  await page.getByRole("button", { name: "确认停用", exact: true }).click();
  await expect(page.getByText("连接已停用", { exact: true })).toBeVisible();
  await expect(page.getByRole("dialog", { name: "管理平台模型" }).getByRole("button", { name: /为新任务默认/ })).toHaveCount(0);
  await expect(page.locator("#model-connection-list").getByText("可用", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "删除", exact: true }).click();
  await page.getByRole("button", { name: "确认删除", exact: true }).dblclick();
  await expect(page.getByRole("dialog", { name: "删除模型连接" })).toHaveCount(0);
  expect(deletes).toBe(1);
});

test("平台保存未知可核对列表但不自动重试，更换本地地址清除旧发现结果", async ({ page }) => {
  await mockSettings(page, "super_admin");
  let saves = 0;
  await page.route("**/api/model-connections/managed/presets/*", route => {
    saves++; return route.fulfill({ status: 503, json: { detail: "结果未知" } });
  });
  await page.route("**/api/model-connections/managed/discover", route => route.fulfill({ json: { models: ["model-a"], detected_api_formats: [], manual_models_required: false } }));
  await page.goto("/settings?section=models");
  await page.getByRole("button", { name: "添加平台连接", exact: true }).click();
  await page.getByLabel("API Key", { exact: false }).fill("synthetic-key");
  await page.getByRole("button", { name: "测试并共享", exact: true }).click();
  await page.getByRole("button", { name: "本地或自定义服务", exact: true }).click();
  await page.getByLabel("模型服务地址", { exact: false }).fill("http://localhost:11434/v1");
  await expect(page.getByRole("button", { name: "探测模型与四种协议（会产生测试用量）" })).toBeDisabled();
  await page.getByRole("button", { name: "检查保存结果", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByText(/列表已刷新，但不能据此确认服务商未计费/)).toBeVisible();
  expect(saves).toBe(1);
  page.once("dialog", dialog => dialog.accept());
  await page.getByRole("button", { name: "我已核对，允许重新测试" }).click();
  await page.getByRole("button", { name: "添加平台连接", exact: true }).click();
  await page.getByRole("button", { name: "本地或自定义服务", exact: true }).click();
  await page.getByLabel("模型服务类型", { exact: true }).selectOption("ollama");
  await page.getByRole("button", { name: "读取可用模型", exact: true }).click();
  await expect(page.getByLabel("本地模型 ID", { exact: false })).toHaveValue("model-a");
  await page.getByLabel("模型服务地址", { exact: false }).fill("http://localhost:1234/v1");
  await expect(page.getByLabel("本地模型 ID", { exact: false })).toHaveValue("");
  await page.getByLabel("本地模型 ID", { exact: false }).fill("manual-model");
  await page.getByLabel("API Key", { exact: false }).fill("synthetic-key-2");
  await expect(page.getByLabel("本地模型 ID", { exact: false })).toHaveValue("manual-model");
});

test("三角色模型页面有真实列表且高级首选不替换任务默认", async ({ page }, testInfo) => {
  for (const role of ["user", "admin", "super_admin"] as const) {
    await page.unrouteAll({ behavior: "wait" });
    await mockSettings(page, role);
    await page.setViewportSize({ width: 1440, height: 1000 });
    let preferred = "deepseek-v4-flash";
    let personalWrites = 0;
    await page.route("**/api/model-connections", route => route.fulfill({ json: { items: [{ ...UX_CONNECTION, model: preferred, default_model: preferred, available_model_count: 2, models: [
      ...UX_CONNECTION.models,
      { ...UX_CONNECTION.models[0], model_id: "deepseek-v4-pro", display_name: "合成增强模型", is_default: false },
    ] }] } }));
    await page.route("**/api/model-connections/ux-shared/default-model", route => {
      preferred = route.request().postDataJSON().model;
      return route.fulfill({ json: {} });
    });
    await page.route("**/api/model-connections/preferences/default", route => {
      if (route.request().method() !== "GET") personalWrites++;
      return route.fulfill({ json: { preference: null } });
    });
    const errors: string[] = [];
    const recordError = (error: Error) => errors.push(error.message);
    page.on("pageerror", recordError);
    await page.goto("/settings?section=models");
    await expect(page.getByRole("region", { name: "平台模型清单" }).getByText("合成模型", { exact: true })).toBeVisible();
    await expect(page.locator("vite-error-overlay")).toHaveCount(0);
    if (role === "user") await expect(page.getByRole("button", { name: "删除", exact: true })).toHaveCount(0);
    else {
      await page.getByRole("button", { name: "管理 DeepSeek · 合成模型", exact: true }).click();
      await page.getByText("连接高级设置 · DeepSeek", { exact: true }).click();
      await expect(page.getByLabel("连接首选模型", { exact: true })).toHaveValue("deepseek-v4-flash");
      await page.getByLabel("连接首选模型", { exact: true }).selectOption("deepseek-v4-pro");
      await expect(page.getByLabel("连接首选模型", { exact: true })).toHaveValue("deepseek-v4-pro");
      await expect(page.getByLabel("默认任务模型", { exact: true })).toHaveValue("");
      expect(personalWrites).toBe(0);
      expect((await new AxeBuilder({ page }).include('[role="dialog"]').analyze()).violations).toEqual([]);
      await page.getByRole("button", { name: "完成管理", exact: true }).click();
    }
    expect((await new AxeBuilder({ page }).include("#model-scope-content").analyze()).violations).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`${role}-desktop.png`), fullPage: true });
    await page.getByRole("button", { name: "添加自己的模型", exact: true }).click();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.getByRole("heading", { name: "连接一个模型服务" }).scrollIntoViewIfNeeded();
    expect((await new AxeBuilder({ page }).include("#model-scope-content").analyze()).violations).toEqual([]);
    await page.screenshot({ path: testInfo.outputPath(`${role}-mobile.png`), fullPage: true });
    expect(errors).toEqual([]);
    page.off("pageerror", recordError);
  }
});

async function mockSettings(
  page: Page,
  role: "user" | "admin" | "super_admin",
  theme: "light" | "dark" = "light",
) {
  // 未声明的请求留在隔离环境，避免旧用例落到真实后端并触发登录失效。
  await page.route("**/api/**", route => route.fulfill({ status: 404, json: { detail: "隔离API" } }));
  await page.addInitScript(() => {
    localStorage.setItem("mangrove_token", "e2e-token");
  });
  await page.addInitScript((selectedTheme) => {
    localStorage.setItem("mangrove_theme", selectedTheme);
  }, theme);
  await page.route("**/api/auth/me", (route) => route.fulfill({
    json: {
      access_token: "e2e-token",
      user_id: `${role}-a`,
      username: role,
      display_name: role === "user"
        ? "普通用户甲"
        : role === "admin"
          ? "管理员甲"
          : "超级管理员甲",
      role,
    },
  }));
  await page.route("**/api/models", (route) => route.fulfill({
    json: {
      options: [{
        provider: "local",
        model: "Qwen3.6-35B-A3B",
        label: "本地模型 · Qwen3.6-35B-A3B",
      }],
      available: ["local"],
      default: {
        provider: "local",
        model: "Qwen3.6-35B-A3B",
        label: "本地模型 · Qwen3.6-35B-A3B",
      },
      document_default: {
        provider: "local",
        model: "Qwen3.6-35B-A3B",
        label: "本地模型 · Qwen3.6-35B-A3B",
      },
      document_default_source: "global",
    },
  }));
  await page.route("**/api/overview", (route) => route.fulfill({
    json: {
      collectors: [],
      conversations: 0,
      templates: { total: 0 },
      providers: { available: [] },
      scheduler: { enabled: false, active_count: 0 },
      connectors: {
        email: false,
        slack: false,
        embedding: false,
        checkpoint: true,
      },
      connectors_enabled: {
        email: false,
        slack: false,
        embedding: false,
        checkpoint: true,
      },
    },
  }));
  await page.route("**/api/config/self", (route) => route.fulfill({
    json: {
      items: [{
        key: "mc_cookie_dy",
        label: "抖音 Cookie",
        secret: true,
        group: "cookies",
        set: false,
        value: "",
      }],
    },
  }));
  await page.route("**/api/model-connections/presets", (route) => route.fulfill({
    json: { items: PRESETS },
  }));
  await page.route("**/api/model-connections", (route) => route.fulfill({
    json: { items: [] },
  }));
  await page.route("**/api/model-connections/preferences/default", route => route.fulfill({ json: { preference: null } }));
  await page.route("**/api/capability-governance/packs", (route) => route.fulfill({
    json: { items: [] },
  }));
  await page.route("**/api/capability-governance/validations", (route) => route.fulfill({
    json: { items: [] },
  }));
  await page.route(
    "**/api/settings/onboarding/model-connections",
    (route) => route.fulfill({ json: { state: "completed" } }),
  );
  await page.route("**/api/config/models*", (route) => route.fulfill({
    json: {
      models: {
        local: ["Qwen3.6-35B-A3B"],
        document: ["local::Qwen3.6-35B-A3B"],
      },
      default_provider: "local",
      available_providers: ["local"],
      ...(role !== "user"
        ? { local_urls: { "Qwen3.6-35B-A3B": "http://192.168.1.20:6012/v1" } }
        : {}),
    },
  }));
  await page.route("**/api/config?*", (route) => route.fulfill({
    json: {
      groups: [
        {
          key: "llm_deepseek",
          label: "模型 · DeepSeek",
          items: [{
            key: "deepseek_api_key",
            label: "DeepSeek API Key",
            value: "•••• 1234",
            source: "override",
            secret: true,
          }],
        },
        {
          key: "search",
          label: "搜索与采集服务",
          items: [{
            key: "tavily_api_key",
            label: "Tavily API Key",
            value: "",
            source: "env",
            secret: true,
          }],
        },
      ],
    },
  }));
}

test("普通用户只看到个人范围并可配置自己的 Provider 连接", async ({ page }) => {
  await mockSettings(page, "user");
  let configured: Record<string, unknown> | null = null;
  await page.route("**/api/model-connections/presets/deepseek", (route) => {
    configured = route.request().postDataJSON();
    return route.fulfill({
      json: {
        connection_id: "conn-user-a",
        owner_scope: "user_personal",
        preset_id: "deepseek",
        display_name: "DeepSeek",
        model: "deepseek-v4-flash",
        status: "verified",
        key_hint: "1234",
      },
    });
  });

  await page.goto("/settings");

  await expect(page.getByRole("button", { name: "我的设置" })).toBeVisible();
  await expect(page.getByRole("button", { name: "模型与连接" })).toBeVisible();
  await expect(page.getByRole("button", { name: "采集账号" })).toBeVisible();
  await expect(page.getByRole("button", { name: "平台配置" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "运行与诊断" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "能力治理" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "我的能力验证" })).toHaveCount(0);

  await page.getByRole("button", { name: "模型与连接" }).click();
  await page.getByRole("button", { name: "添加自己的模型", exact: true }).click();
  await expect(page.getByRole("heading", { name: "连接一个模型服务" })).toBeVisible();
  await expect(page.getByText("自定义兼容接口")).toHaveCount(0);
  await page.getByLabel("模型服务商").selectOption("deepseek");
  await expect(page.getByLabel("选择模型")).toHaveValue("deepseek-v4-flash");
  await expect(page.getByLabel("模型服务地址")).toHaveCount(0);
  await expect(page.getByLabel("API 格式")).toHaveCount(0);
  await page.getByText("连接名称（已自动填写，可选修改）", { exact: true }).click();
  await page.getByLabel("连接名称").fill("我的 DeepSeek");
  await page.getByLabel("API Key").fill("sk-user-secret-1234");
  await page.getByRole("button", { name: "测试并保存所选模型" }).click();

  expect(configured).toEqual({
    display_name: "我的 DeepSeek",
    api_key: "sk-user-secret-1234",
    region: null, workspace_id: "",
    model: "deepseek-v4-flash",
  });
  await expect(page.getByText("连接已验证并保存")).toBeVisible();
});

test("管理员从能力卡片创建验证并渐进查看步骤缺口", async ({ page }) => {
  await mockSettings(page, "admin");
  let validationRun: Record<string, any> | null = null;
  await page.route("**/api/capability-governance/packs", (route) => route.fulfill({
    json: {
      items: [
        {
          pack_id: "gray-python-table",
          version: "1.0.0",
          scope: "platform",
          maturity: "verified",
          lifecycle: "active",
          eligibility: "eligible",
          source: "legacy_compat",
          owner_id: null,
          digest: `sha256:${"a".repeat(64)}`,
          can_validate: true,
        },
        {
          pack_id: "everything-mcp",
          version: "2026.7.4",
          scope: "platform",
          maturity: "verified",
          lifecycle: "deprecated",
          eligibility: "eligible",
          source: "legacy_compat",
          owner_id: null,
          digest: `sha256:${"b".repeat(64)}`,
          can_validate: false,
        },
      ],
    },
  }));
  await page.route("**/api/capability-governance/validations", (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      validationRun = {
        run_id: "capval-e2e",
        owner_id: "admin-a",
        target: {
          pack_id: body.pack_id,
          version: body.version,
          digest: body.digest,
        },
        task_ref: { task_id: body.task_id, revision: body.revision },
        status: "queued",
        evidence: [],
        created_at: "2026-08-07T00:00:00Z",
      };
      return route.fulfill({ status: 202, json: validationRun });
    }
    return route.fulfill({ json: { items: validationRun ? [validationRun] : [] } });
  });
  await page.route("**/api/capability-governance/packs/*/*/supply-chain-evidence?*", (route) => route.fulfill({
    json: {
      evidence: {
        status: "blocked",
        blockers: ["misconfiguration_failure", "trivy_database_stale"],
        secret_count: 0,
        critical_count: 0,
        fixable_high_count: 0,
        misconfiguration_failure_count: 1,
        trivy_version: "0.70.0",
        trivy_database: { version: 2, updated_at: "2026-08-07T00:00:00Z" },
        syft_version: "1.50.0",
        cyclonedx_spec_version: "1.6",
        occurred_at: "2026-08-07T00:00:00Z",
      },
    },
  }));
  await page.route("**/api/capability-governance/packs/*/*/validation-tasks?*", (route) => route.fulfill({
    json: {
      items: [{
        task_id: "workspace-table-1",
        revision: 2,
        title: "季度表格汇总",
        updated_at: "2026-08-07T00:00:00Z",
      }],
    },
  }));

  await page.goto("/settings?section=governance");

  await expect(page.getByRole("button", { name: "能力治理" })).toHaveAttribute(
    "aria-current",
    "page",
  );
  await expect(page.getByRole("heading", { name: "能力治理状态" })).toBeVisible();
  await expect(page.getByText("gray-python-table")).toBeVisible();
  const grayCard = page.locator("article").filter({ hasText: "gray-python-table" }).first();
  await expect(grayCard).toContainText("平台");
  await expect(grayCard).toContainText("已验证");
  await expect(grayCard).toContainText("正常");
  await expect(page.getByText("可运行")).toHaveCount(2);
  await expect(page.getByText("兼容读取").first()).toBeVisible();
  await expect(grayCard).toContainText("供应链证据");
  await expect(grayCard).toContainText("存在硬门");
  await expect(grayCard).toContainText("阻断原因：存在 Critical 或可修复 High 安全误配置、Trivy 漏洞库已过期");
  await expect(grayCard).toContainText("DB 更新 2026-08-07 00:00 UTC");
  await expect(page.getByText("sha256:bbbbbbbbbbbb…bbbbbbbbbbbb").first()).toBeVisible();
  await grayCard.getByRole("button", { name: "发起验证" }).click();
  await expect(page.getByRole("dialog", { name: "发起能力验证" })).toBeVisible();
  await expect(page.getByLabel("真实任务证据")).toContainText("季度表格汇总 · V2");
  await expect(page.getByText("自动定位到当前能力卡片下方的“验证进度与结果”")).toBeVisible();
  await page.getByRole("button", { name: "创建验证运行" }).click();
  const progress = page.getByRole("group", { name: "验证进度与结果：gray-python-table" }).first();
  await expect(progress).toBeVisible();
  await expect(progress).toHaveAttribute("open", "");
  await expect(progress).toContainText("验证进度与结果 · 等待验证 · 任务 workspace-table-1 V2");
  await expect(progress).toContainText("本页每 1.5 秒自动更新");
  await expect(progress.getByText("合成 Smoke")).toBeVisible();
  await expect(progress.getByText("供应链扫描与 SBOM")).toHaveCount(0);
  await expect(progress.getByText("已完成 0/5 个步骤。一次业务成功不会自动改变能力成熟度。")).toBeVisible();
});

test("五步成功记录与独立供应链证据分别展示", async ({ page }) => {
  await mockSettings(page, "admin");
  const digest = `sha256:${"c".repeat(64)}`;
  await page.route("**/api/capability-governance/packs", (route) => route.fulfill({
    json: {
      items: [{
        pack_id: "legacy-five-step",
        version: "1.0.0",
        scope: "platform",
        maturity: "verified",
        lifecycle: "active",
        eligibility: "eligible",
        source: "legacy_compat",
        owner_id: null,
        digest,
        can_validate: true,
      }],
    },
  }));
  await page.route("**/api/capability-governance/validations", (route) => route.fulfill({
    json: {
      items: [{
        run_id: "legacy-five-step-run",
        owner_id: "admin-a",
        target: { pack_id: "legacy-five-step", version: "1.0.0", digest },
        task_ref: { task_id: "legacy-task", revision: 1 },
        status: "succeeded",
        evidence: [
          "synthetic_smoke",
          "owner_task_replay",
          "fail_closed",
          "verifier",
          "cleanup",
        ].map((step) => ({
          step,
          status: "passed",
          summary: "历史验证步骤已通过",
          occurred_at: "2026-08-06T00:00:00Z",
        })),
        created_at: "2026-08-06T00:00:00Z",
      }],
    },
  }));
  await page.route("**/api/capability-governance/packs/*/*/supply-chain-evidence?*", (route) => route.fulfill({
    json: { evidence: null },
  }));

  await page.goto("/settings?section=governance");

  const progress = page.getByRole("group", { name: "验证进度与结果：legacy-five-step" });
  await progress.locator("summary").click();
  await expect(progress).toContainText(
    "五项验证步骤已通过。全部证据通过后，该能力会自动晋级为已验证。",
  );
  await expect(progress).not.toContainText("供应链扫描与 SBOM");
  await expect(progress).toContainText("已完成 5/5 个步骤");
  const card = page.locator("article").filter({ hasText: "legacy-five-step" });
  await expect(card).toContainText("供应链证据");
  await expect(card).toContainText("尚未扫描");
});

test("草稿能力卡片展示脱敏缺口并提示自动晋级", async ({ page }) => {
  await mockSettings(page, "admin");
  const digest = `sha256:${"d".repeat(64)}`;
  await page.route("**/api/capability-governance/packs", (route) => route.fulfill({
    json: {
      items: [{
        pack_id: "pending-draft",
        version: "1.0.0",
        scope: "personal",
        maturity: "draft",
        lifecycle: "active",
        eligibility: "eligible",
        source: "governance_event",
        owner_id: "admin-a",
        digest,
        can_validate: true,
        promotion_gaps: ["validation_incomplete", "supply_chain_evidence_missing"],
      }],
    },
  }));
  await page.route("**/api/capability-governance/validations", (route) => route.fulfill({
    json: { items: [] },
  }));
  await page.route("**/api/capability-governance/packs/*/*/supply-chain-evidence?*", (route) => route.fulfill({
    json: { evidence: null },
  }));

  await page.goto("/settings?section=governance");

  const card = page.locator("article").filter({ hasText: "pending-draft" });
  await expect(card).toContainText("草稿");
  await expect(card).toContainText("距已验证还缺");
  await expect(card).toContainText("尚无全部通过的验证运行");
  await expect(card).toContainText("尚未形成供应链扫描证据");
});

test("普通用户设置不加载低频能力验证和供应链证据", async ({ page }) => {
  await mockSettings(page, "user");
  const personalDigest = `sha256:${"a".repeat(64)}`;
  const evidenceRequests: string[] = [];
  await page.route("**/api/capability-governance/packs", (route) => route.fulfill({
    json: {
      items: [
        {
          pack_id: "gray-python-table",
          version: "1.0.0",
          scope: "platform",
          maturity: "verified",
          lifecycle: "active",
          eligibility: "eligible",
          source: "legacy_compat",
          owner_id: null,
          digest: null,
          can_validate: false,
        },
        {
          pack_id: "owner-python-table",
          version: "1.0.0",
          scope: "personal",
          maturity: "draft",
          lifecycle: "active",
          eligibility: "eligible",
          source: "governance_event",
          owner_id: "user-a",
          digest: personalDigest,
          can_validate: true,
        },
      ],
    },
  }));
  await page.route(
    "**/api/capability-governance/packs/*/*/supply-chain-evidence?*",
    (route) => {
      evidenceRequests.push(route.request().url());
      return route.fulfill({ json: { evidence: null } });
    },
  );

  await page.goto("/settings");

  await expect(page.getByRole("heading", { name: "我的能力验证" })).toHaveCount(0);
  await expect(page.getByText("owner-python-table")).toHaveCount(0);
  await expect(page.getByText("gray-python-table")).toHaveCount(0);
  expect(evidenceRequests).toEqual([]);
});

test("普通用户可创建并区分同一 Provider 的多套命名连接", async ({ page }) => {
  await mockSettings(page, "user");
  const connections: Record<string, unknown>[] = [];
  const createdBodies: Record<string, unknown>[] = [];
  await page.route("**/api/model-connections", (route) => route.fulfill({
    json: { items: connections },
  }));
  await page.route("**/api/model-connections/presets/deepseek", (route) => {
    const body = route.request().postDataJSON() as Record<string, unknown>;
    createdBodies.push(body);
    const connection = {
      connection_id: `conn-${createdBodies.length}`,
      owner_scope: "user_personal",
      preset_id: "deepseek",
      display_name: body.display_name,
      model: body.model,
      status: "verified",
      key_hint: createdBodies.length === 1 ? "1111" : "2222",
    };
    connections.push(connection);
    return route.fulfill({ status: 201, json: connection });
  });

  await page.goto("/settings?section=models");
  await page.getByRole("button", { name: "添加自己的模型", exact: true }).click();
  await page.getByText("连接名称（已自动填写，可选修改）", { exact: true }).click();
  await page.getByLabel("连接名称").fill("DeepSeek 日常");
  await page.getByLabel("API Key").fill("sk-personal-primary-1111");
  await page.getByRole("button", { name: "测试并保存所选模型" }).click();
  await expect(page.locator("#model-connection-list").getByText("DeepSeek 日常", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "添加个人连接" }).click();
  await page.getByText("连接名称（已自动填写，可选修改）", { exact: true }).click();
  await page.getByLabel("连接名称").fill("DeepSeek 备用");
  await page.getByLabel("API Key").fill("sk-personal-backup-2222");
  await page.getByRole("button", { name: "测试并保存所选模型" }).click();

  await expect(page.locator("#model-connection-list").getByText("DeepSeek 日常", { exact: true })).toBeVisible();
  await expect(page.locator("#model-connection-list").getByText("DeepSeek 备用", { exact: true })).toBeVisible();
  await expect(page.getByText("Key •••• 1111")).toBeVisible();
  await expect(page.getByText("Key •••• 2222")).toBeVisible();
  expect(createdBodies).toEqual([
    {
      display_name: "DeepSeek 日常",
      api_key: "sk-personal-primary-1111",
    region: null, workspace_id: "",
      model: "deepseek-v4-flash",
    },
    {
      display_name: "DeepSeek 备用",
      api_key: "sk-personal-backup-2222",
    region: null, workspace_id: "",
      model: "deepseek-v4-flash",
    },
  ]);
});

test("部分成功连接展示逐模型结果并可只重试失败模型", async ({ page }) => {
  await mockSettings(page, "user");
  let connection: Record<string, any> | null = null;
  const modelResult = (
    modelId: string,
    displayName: string,
    status: string,
    isDefault: boolean,
  ) => ({
    model_id: modelId,
    display_name: displayName,
    catalog_role: modelId.endsWith("flash") ? "balanced" : "quality",
    catalog_version: "2026-07-30.2",
    status,
    enabled: status === "available",
    is_default: isDefault,
    verified_at: "2026-07-30T12:00:00",
    error_code: status === "available" ? null : status,
    usage_status: "unknown",
  });
  await page.route("**/api/model-connections", (route) => route.fulfill({
    json: { items: connection ? [connection] : [] },
  }));
  await page.route("**/api/model-connections/presets/deepseek", (route) => {
    connection = {
      connection_id: "conn-multi-model",
      owner_scope: "user_personal",
      preset_id: "deepseek",
      display_name: "DeepSeek 主连接",
      model: "deepseek-v4-flash",
      default_model: "deepseek-v4-flash",
      available_model_count: 1,
      status: "verified",
      key_hint: "1234",
      models: [
        modelResult(
          "deepseek-v4-flash",
          "DeepSeek V4 Flash",
          "available",
          true,
        ),
        modelResult(
          "deepseek-v4-pro",
          "DeepSeek V4 Pro",
          "model_access_denied",
          false,
        ),
      ],
    };
    return route.fulfill({ status: 201, json: connection });
  });
  await page.route(
    "**/api/model-connections/conn-multi-model/models/retry",
    (route) => {
      connection!.available_model_count = 2;
      connection!.models = [
        modelResult(
          "deepseek-v4-flash",
          "DeepSeek V4 Flash",
          "available",
          true,
        ),
        modelResult(
          "deepseek-v4-pro",
          "DeepSeek V4 Pro",
          "available",
          false,
        ),
      ];
      return route.fulfill({ json: connection });
    },
  );
  await page.route(
    "**/api/model-connections/preferences/default",
    (route) => route.fulfill({ json: { preference: null } }),
  );

  await page.goto("/settings?section=models");
  await page.getByRole("button", { name: "添加自己的模型", exact: true }).click();
  await page.getByText("连接名称（已自动填写，可选修改）", { exact: true }).click();
  await page.getByLabel("连接名称").fill("DeepSeek 主连接");
  await page.getByLabel("API Key").fill("sk-personal-multi-model-1234");
  await page.getByRole("button", { name: "测试并保存所选模型" }).click();

  await expect(page.getByText(/^1 \/ 2 个模型可用 · 连接首选 DeepSeek V4 Flash · Key/)).toBeVisible();
  await expect(page.locator("#model-connection-list span").filter({ hasText: /^DeepSeek V4 Flash$/ })).toBeVisible();
  await expect(page.getByText("无模型权限")).toBeVisible();
  await page.getByRole("button", { name: "重试 DeepSeek V4 Pro" }).click();
  await expect(page.getByText("2 / 2 个模型可用")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "设 DeepSeek 主连接 的 DeepSeek V4 Pro 为新任务默认" }),
  ).toBeVisible();
});

test("管理员同时拥有个人设置和平台治理入口", async ({ page }) => {
  await mockSettings(page, "admin");
  let presetConnection: Record<string, unknown> | null = null;
  await page.route("**/api/model-connections/managed/presets/deepseek", (route) => {
    presetConnection = route.request().postDataJSON();
    return route.fulfill({
      json: {
        connection_id: "managed-deepseek",
        owner_scope: "platform_shared",
        preset_id: "deepseek",
        display_name: "生产 DeepSeek",
        model: "deepseek-v4-pro",
        status: "verified",
      },
    });
  });
  await page.goto("/settings");

  await expect(page.getByRole("button", { name: "我的设置", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "模型与连接", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "采集账号", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "平台配置", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "运行与诊断", exact: true })).toBeVisible();

  await page.getByRole("button", { name: "模型与连接", exact: true }).click();
  await expect(page.getByRole("tab", { name: "个人连接" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "平台连接" })).toBeVisible();
  await page.getByRole("tab", { name: "平台连接" }).click();
  await expect(page.getByRole("button", { name: "添加平台连接" })).toBeVisible();
  await page.getByRole("button", { name: "添加平台连接" }).click();
  await expect(page.getByRole("button", { name: "云端服务商" }))
    .toHaveAttribute("aria-pressed", "true");
  await page.locator('[role="dialog"]').evaluate(async (dialog) => {
    await Promise.all(
      dialog.getAnimations({ subtree: true }).map((animation) => animation.finished),
    );
  });
  const platformDialogAccessibility = await new AxeBuilder({ page })
    .include('[role="dialog"]')
    .analyze();
  expect(
    platformDialogAccessibility.violations,
    "平台连接表单存在可访问性违规",
  ).toEqual([]);
  await page.getByLabel("连接名称").fill("生产 DeepSeek");
  await page.getByLabel("模型服务商").selectOption("deepseek");
  await page.getByLabel("选择模型").selectOption("deepseek-v4-pro");
  await page.getByLabel("API Key").fill("sk-platform-secret-2468");
  await page.getByRole("button", { name: "测试并共享" }).click();
  expect(presetConnection).toEqual({
    display_name: "生产 DeepSeek",
    model: "deepseek-v4-pro",
    api_key: "sk-platform-secret-2468",
    region: null, workspace_id: "",
  });

  await page.getByRole("button", { name: "添加平台连接" }).click();
  await page.getByRole("button", { name: "本地或自定义服务" }).click();
  await expect(page.getByLabel("模型服务地址")).toBeVisible();
  await expect(page.getByLabel("API 格式")).toBeVisible();
  await expect(
    page.getByText("公网连接必须填写；无鉴权 LAN/本地服务可以留空。"),
  ).toBeVisible();
  await page.getByRole("button", { name: "取消" }).click();

  await page.getByRole("button", { name: "平台配置", exact: true }).click();
  const legacy = page.locator("summary").filter({ hasText: "高级：旧流程模型参数" });
  await expect(legacy).toHaveCount(0);
  await expect(page.getByText("DeepSeek API Key")).not.toBeVisible();
  await expect(page.getByRole("link", { name: "管理平台模型" })).toHaveAttribute("href", "/settings?section=models&scope=platform");

  await page.getByRole("button", { name: "采集账号", exact: true }).click();
  await expect(page.getByText("我的采集账号")).toBeVisible();
});

test("平台连接对话框支持键盘进入、Esc 关闭和焦点归还", async ({ page }) => {
  await mockSettings(page, "admin");
  await page.goto("/settings?section=models");
  await page.getByRole("tab", { name: "平台连接" }).click();
  const trigger = page.getByRole("button", { name: "添加平台连接" });
  await trigger.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("button", { name: "云端服务商" }))
    .toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("导入连接网络失败后仍可再次验证且无需重填 Key", async ({ page }) => {
  await mockSettings(page, "user");
  let preference: Record<string, unknown> | null = null;
  let connection: Record<string, any> | null = null;
  const retryableModel = {
    model_id: "deepseek-v4-flash",
    display_name: "DeepSeek V4 Flash",
    catalog_role: "balanced",
    catalog_version: "2026-07-30.2",
    status: "network_unreachable",
    enabled: false,
    is_default: false,
    usage_status: "unknown",
  };
  await page.route("**/api/model-connections", (route) =>
    route.fulfill({ json: { items: connection ? [connection] : [] } }));
  await page.route("**/api/model-connections/imports/legacy", (route) => {
    connection = {
      connection_id: "imported-1",
      owner_scope: "user_personal",
      preset_id: "deepseek",
      display_name: "导入的 DeepSeek",
      model: "deepseek-v4-flash",
      status: "pending_validation",
      key_hint: "7788",
      available_model_count: 0,
      models: [retryableModel],
    };
    return route.fulfill({ json: { items: [connection] } });
  });
  await page.route("**/api/model-connections/imported-1/models/retry", (route) => {
    connection!.status = "verified";
    connection!.default_model = "deepseek-v4-flash";
    connection!.available_model_count = 1;
    connection!.models = [{ ...retryableModel, status: "available", enabled: true, is_default: true }];
    return route.fulfill({ json: connection });
  });
  await page.route("**/api/model-connections/preferences/default", (route) => {
    if (route.request().method() === "PUT") {
      preference = {
        ...route.request().postDataJSON(),
        available: true,
      };
      return route.fulfill({ json: preference });
    }
    return route.fulfill({ json: { preference } });
  });

  await page.goto("/settings?section=models");
  await page.getByText("帮助与高级操作", { exact: true }).click();
  await page.getByRole("button", { name: "导入现有配置" }).click();
  await page.getByRole("tab", { name: "我的连接" }).click();
  await expect(page.getByText("验证并启用（Key 无需重填）")).toBeVisible();
  await page.getByRole("button", { name: "验证并启用（Key 无需重填）" }).click();
  await page.getByRole("button", { name: /导入的 DeepSeek.*新任务默认/ }).click();
  expect(preference).toMatchObject({
    connection_id: "imported-1",
    model_id: "deepseek-v4-flash",
  });
  await expect(page.getByLabel("默认任务模型", { exact: true })).toHaveValue(JSON.stringify(["imported-1", "deepseek-v4-flash"]));
  await expect(page.locator('[data-model-tour="default-connection"]')).toContainText(
    "DeepSeek V4 Flash",
  );
  await expect(page.locator('[data-model-tour="default-connection"]')).toContainText("可用");
});

test("超级管理员可发现四协议并手工覆盖最多八个模型", async ({ page }) => {
  await mockSettings(page, "super_admin");
  let published: Record<string, unknown> | null = null;
  await page.route("**/api/model-connections/managed/discover", (route) =>
    route.fulfill({
      json: {
        models: ["model-a", "model-b"],
        models_discovered: true,
        detected_api_formats: [
          "anthropic_messages",
          "openai_chat_completions",
          "openai_responses",
          "gemini_generate_content",
        ],
        recommended_api_format: "openai_responses",
        manual_models_required: false,
      },
    }));
  await page.route("**/api/model-connections/managed", (route) => {
    published = route.request().postDataJSON();
    return route.fulfill({ json: { connection_id: "custom-1", status: "verified" } });
  });

  await page.goto("/settings?section=models");
  await page.getByRole("tab", { name: "平台连接" }).click();
  await page.getByRole("button", { name: "添加平台连接" }).click();
  await page.getByRole("button", { name: "本地或自定义服务" }).click();
  await expect(page.getByLabel("API 格式").locator("option")).toHaveCount(4);
  await page.getByLabel("连接名称").fill("多协议网关");
  await page.getByLabel("模型服务地址").fill("https://gateway.example/v1");
  await page.getByLabel("API Key").fill("gateway-secret-1234");
  await page.getByRole("button", { name: "探测模型与四种协议（会产生测试用量）" }).click();
  await expect(page.getByText(/已检测：/)).toContainText("openai_responses");
  await expect(page.getByLabel("API 格式")).toHaveValue("openai_responses");
  await page.getByRole("dialog").getByLabel("默认模型").fill("model-b");
  await page.getByRole("button", { name: "测试并共享" }).click();
  expect(published).toMatchObject({
    display_name: "多协议网关",
    api_format: "openai_responses",
    model: "model-b",
    models: ["model-a", "model-b"],
  });
});

test("新手引导可跳过并从设置页重新播放", async ({ page }) => {
  await mockSettings(page, "user");
  const savedStates: string[] = [];
  await page.route("**/api/settings/onboarding/model-connections", (route) => {
    if (route.request().method() === "PUT") {
      const state = route.request().postDataJSON().state as string;
      savedStates.push(state);
      return route.fulfill({ json: { state } });
    }
    return route.fulfill({ json: { state: "not_started" } });
  });

  await page.goto("/settings?section=models");
  await expect(page.getByText("先确认新任务默认模型")).toHaveCount(0);
  await page.getByText("帮助与高级操作", { exact: true }).click();
  await page.getByRole("button", { name: "播放新手引导" }).click();
  await expect(page.getByText("先确认新任务默认模型")).toBeVisible();
  await page.getByRole("button", { name: "跳过" }).click();
  await expect.poll(() => savedStates).toContain("skipped");

  await page.getByRole("button", { name: "播放新手引导" }).click();
  await expect(page.getByText("先确认新任务默认模型")).toBeVisible();
});

test("模型连接接口误返回网页时显示可恢复错误而不是 JSON 解析异常", async ({ page }) => {
  await mockSettings(page, "user");
  await page.route("**/api/model-connections/presets", (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: "<!doctype html><title>Mangrove</title>",
    }));

  await page.goto("/settings?section=models");

  await expect(page.getByRole("alert").filter({ hasText: "模型连接加载失败" })).toBeVisible();
  await expect(
    page.getByRole("main").getByText(/服务返回了网页而不是 API 数据/),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "重新加载" })).toBeVisible();
  await expect(page.getByText(/Unexpected token/)).toHaveCount(0);
});

test("三角色明暗主题设置主视图没有自动化可访问性违规", async ({ page }) => {
  for (const role of ["user", "admin", "super_admin"] as const) {
    for (const theme of ["light", "dark"] as const) {
      await page.unrouteAll({ behavior: "wait" });
      await mockSettings(page, role, theme);
      await page.goto("/settings?section=models");
      if (theme === "dark") {
        await expect(page.locator("html")).toHaveClass(/dark/);
      } else {
        await expect(page.locator("html")).not.toHaveClass(/dark/);
      }
      const result = await new AxeBuilder({ page }).analyze();
      expect(
        result.violations,
        `${role}/${theme} 设置页存在可访问性违规`,
      ).toEqual([]);
    }
  }
});

test("管理员审核视图分组渐进披露并完成一次审计查看", async ({ page }) => {
  await mockSettings(page, "admin");
  const draftDigest = `sha256:${"a".repeat(64)}`;
  const verifiedDigest = `sha256:${"b".repeat(64)}`;
  const baseItem = (packId: string, digest: string) => ({
    pack_id: packId,
    version: "1.0.0",
    scope: packId === "pending-draft" ? "personal" : "platform",
    maturity: packId === "pending-draft" ? "draft" : "verified",
    lifecycle: "active",
    eligibility: "eligible",
    source: packId === "pending-draft" ? "governance_event" : "legacy_compat",
    owner_id: packId === "pending-draft" ? "owner-a" : null,
    digest,
    can_validate: false,
  });
  await page.route("**/api/capability-governance/packs", (route) => route.fulfill({
    json: {
      items: [
        {
          ...baseItem("pending-draft", draftDigest),
          promotion_gaps: ["validation_incomplete"],
        },
        baseItem("gray-python-table", verifiedDigest),
      ],
    },
  }));
  await page.route("**/api/capability-governance/validations", (route) => route.fulfill({
    json: { items: [] },
  }));
  await page.route("**/api/capability-governance/packs/*/*/supply-chain-evidence?*", (route) => route.fulfill({
    json: { evidence: null },
  }));
  await page.route("**/api/capability-governance/admin/review", (route) => route.fulfill({
    json: {
      items: [
        {
          ...baseItem("pending-draft", draftDigest),
          promotion_gaps: ["validation_incomplete"],
          validation: null,
          supply_chain: null,
          task_metadata: {
            task_id: "workspace-owner-a",
            revision: 2,
            owner_id: "owner-a",
            task_status: "completed",
            created_at: "2026-08-14T00:00:00Z",
            updated_at: "2026-08-14T01:00:00Z",
            input_count: 1,
            input_types: ["csv"],
            output_count: 1,
            output_formats: ["csv"],
          },
          audit_history: [
            {
              event_id: "capgov_prior_audit",
              actor_id: "admin-b",
              actor_role: "admin",
              reason: "排障：验证输出与任务不符，先行核对",
              subject_type: "task_prompt",
              subject_sha256: "f".repeat(64),
              result: "succeeded",
              task_id: "workspace-owner-a",
              revision: 2,
              failure_reason: null,
              occurred_at: "2026-08-14T01:30:00Z",
            },
          ],
        },
        {
          ...baseItem("gray-python-table", verifiedDigest),
          promotion_gaps: [],
          validation: null,
          supply_chain: null,
          task_metadata: null,
          audit_history: [],
        },
      ],
    },
  }));
  let auditBody: Record<string, unknown> | null = null;
  await page.route("**/api/capability-governance/admin/audit-view", (route) => {
    auditBody = route.request().postDataJSON();
    return route.fulfill({
      json: {
        status: "succeeded",
        content: "审计正文：任务 workspace-owner-a 的 task_sources",
        truncated: false,
        failure_reason: null,
        event: {
          event_id: "capgov_e2e_audit",
          actor_id: "admin-a",
          actor_role: "admin",
          reason: auditBody?.reason,
          subject_type: "task_sources",
          subject_sha256: "e".repeat(64),
          result: "succeeded",
          task_id: "workspace-owner-a",
          revision: 2,
          failure_reason: null,
          occurred_at: "2026-08-14T02:00:00Z",
        },
      },
    });
  });

  // 1366 宽度验收：无横向滚动。
  await page.setViewportSize({ width: 1366, height: 768 });
  await page.goto("/settings?section=governance");

  // 分组与计数：状态不只依赖颜色，用文本标题表达。
  await expect(page.getByRole("heading", { name: "待验证（1）" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "已晋级（1）" })).toBeVisible();

  // 1366 宽度下无横向滚动。
  const scrolls = await page.evaluate(() => ({
    scrollWidth: document.scrollingElement!.scrollWidth,
    clientWidth: document.scrollingElement!.clientWidth,
  }));
  expect(scrolls.scrollWidth).toBeLessThanOrEqual(scrolls.clientWidth);

  // 渐进披露：任务管理元数据与审计历史。
  const draftCard = page.locator("article").filter({ hasText: "pending-draft" });
  const meta = draftCard.locator("summary").filter({ hasText: "任务管理元数据" });
  await meta.click();
  await expect(draftCard).toContainText("workspace-owner-a");
  await expect(draftCard).toContainText("输入 1 个（csv）");
  await expect(draftCard).toContainText("输出 1 个（csv）");
  const auditLog = draftCard.locator("summary").filter({ hasText: "审计记录" });
  await auditLog.click();
  await expect(draftCard).toContainText("任务 workspace-owner-a V2");

  // 审计查看：原因未填写前不可提交。
  await draftCard.getByRole("button", { name: "审计查看业务内容" }).click();
  const dialog = page.getByRole("dialog", { name: "审计查看业务内容" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("会读取任务业务正文并写入不可变审计记录");
  const submit = dialog.getByRole("button", { name: "确认查看并写入审计记录" });
  await expect(submit).toBeDisabled();
  await dialog.getByLabel("查看对象").selectOption("task_sources");
  await dialog.getByLabel("查看原因").fill("排障：核对来源正文内容");
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect(dialog).toContainText("审计正文");
  await expect(dialog).toContainText("审计记录已写入");
  expect(auditBody).toMatchObject({
    pack_id: "pending-draft",
    task_id: "workspace-owner-a",
    revision: 2,
    subject_type: "task_sources",
  });
});

test("管理员提交平台候选并发布到管理员灰度", async ({ page }) => {
  await mockSettings(page, "admin");
  const personalDigest = `sha256:${"a".repeat(64)}`;
  const platformDigest = `sha256:${"b".repeat(64)}`;
  const verifiedItem = {
    pack_id: "verified-personal-tool",
    version: "1.0.0",
    scope: "personal",
    maturity: "verified",
    lifecycle: "active",
    eligibility: "eligible",
    source: "governance_event",
    owner_id: "owner-a",
    digest: personalDigest,
    can_validate: true,
    promotion_gaps: [],
  };
  await page.route("**/api/capability-governance/packs", (route) => route.fulfill({
    json: { items: [verifiedItem] },
  }));
  await page.route("**/api/capability-governance/validations", (route) => route.fulfill({
    json: { items: [] },
  }));
  await page.route("**/api/capability-governance/packs/*/*/supply-chain-evidence?*", (route) => route.fulfill({
    json: { evidence: null },
  }));
  await page.route("**/api/capability-governance/admin/review", (route) => route.fulfill({
    json: { items: [] },
  }));
  const candidates: Record<string, unknown>[] = [];
  await page.route("**/api/capability-governance/admin/platform-candidates", (route) => {
    if (route.request().method() === "POST") {
      const body = route.request().postDataJSON();
      const outcome = {
        status: "created",
        snapshot: {
          pack_id: body.pack_id,
          version: body.version,
          source_digest: personalDigest,
          platform_digest: platformDigest,
          manifest_summary: ["entrypoint"],
        },
        event: {
          event_id: "capgov_candidate_e2e",
          reason: body.reason,
          occurred_at: "2026-08-15T00:00:00Z",
        },
      };
      candidates.push({
        pack_id: body.pack_id,
        version: body.version,
        source_digest: personalDigest,
        platform_digest: platformDigest,
        validation_status: "queued",
        steps_passed: 0,
        steps_total: 6,
        signed: false,
        submitted_at: "2026-08-15T00:00:00Z",
        reason: body.reason,
      });
      return route.fulfill({ json: outcome });
    }
    return route.fulfill({ json: { items: candidates } });
  });
  let publishBody: Record<string, unknown> | null = null;
  await page.route("**/api/capability-governance/admin/platform-publish", (route) => {
    publishBody = route.request().postDataJSON();
    return route.fulfill({
      json: {
        status: "published",
        event: {
          event_id: "capgov_publish_e2e",
          reason: publishBody?.reason,
          audience: "admin_gray",
          platform_validation_run_id: "pfval_e2e",
          signing_signature_digest: `sha256:${"c".repeat(64)}`,
          signing_public_key_sha256: "d".repeat(64),
          occurred_at: "2026-08-15T01:00:00Z",
        },
      },
    });
  });

  await page.goto("/settings?section=governance");

  // 已验证个人能力卡片出现"提交平台候选"按钮。
  const card = page.locator("article").filter({ hasText: "verified-personal-tool" });
  await card.getByRole("button", { name: "提交平台候选" }).click();
  const dialog = page.getByRole("dialog", { name: "提交平台候选" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("删除 Owner、任务引用、业务字段与敏感引用");
  const submit = dialog.getByRole("button", { name: "确认提交平台候选" });
  await expect(submit).toBeDisabled();
  await dialog.getByLabel("提交原因").fill("平台候选：个人验证已完成并通过审核");
  await expect(submit).toBeEnabled();
  await submit.click();

  // 候选分组出现，展示平台 digest 与原因。
  await expect(page.getByRole("heading", { name: "平台候选（1）" })).toBeVisible();
  const candidateCard = page.locator("article").filter({ hasText: "平台 digest" }).first();
  await expect(candidateCard).toContainText("verified-personal-tool");
  await expect(candidateCard).toContainText("平台候选：个人验证已完成并通过审核");
  await candidateCard.getByRole("button", { name: "发布" }).click();
  const publishDialog = page.getByRole("dialog", { name: "发布平台能力" });
  await expect(publishDialog).toBeVisible();
  await expect(publishDialog).toContainText("受众固定为管理员灰度");
  const publishSubmit = publishDialog.getByRole("button", { name: "确认发布" });
  await expect(publishSubmit).toBeDisabled();
  await publishDialog.getByLabel("发布原因").fill("发布：验证与签名全部通过");
  await publishSubmit.click();
  expect(publishBody).toMatchObject({
    pack_id: "verified-personal-tool",
    platform_digest: platformDigest,
  });
});


for (const role of ["admin", "super_admin"] as const) {
  test(`外部只读边界：${role} 历史通知无编辑验证入口，搜索与内部增强保留`, async ({ page }) => {
    await mockSettings(page, role);
    const errors: string[] = [];
    page.on("pageerror", (error) => errors.push(error.message));
    page.on("console", (message) => { if (message.type() === "error") errors.push(message.text()); });
    await page.route("**/api/config/domain-health", (route) => route.fulfill({ json: { flagged: {} } }));
    await page.setViewportSize(role === "admin" ? { width: 1366, height: 768 } : { width: 390, height: 844 });
    const writes: string[] = [];
    page.on("request", (request) => {
      if (request.method() !== "GET") writes.push(request.url());
    });
    await page.route("**/api/config?*", (route) => route.fulfill({ json: { groups: [
      ...["email", "slack"].map((key) => ({ key, label: `历史 ${key}`, items: [{
        key: key === "email" ? "smtp_enabled" : "slack_webhook_url",
        label: `历史 ${key} 值`, value: "已保留", source: "override", secret: true,
      }] })),
      { key: "search", label: "搜索与采集服务", items: [{ key: "tavily_api_key", label: "Tavily API Key", value: "虚构值", source: "override", secret: true }] },
    ] } }));
    await page.goto("/settings?section=platform");
    await expect(page).toHaveURL(/settings\?section=platform/);
    await expect(page).toHaveTitle(/Mangrove/);
    await expect(page.getByRole("main")).not.toBeEmpty();
    await expect(page.locator("vite-error-overlay")).toHaveCount(0);
    for (const key of ["email", "slack"]) {
      const group = page.getByRole("button", { name: `历史 ${key}`, exact: false }).locator("..");
      await group.getByRole("button").click();
      await expect(group).toContainText("外部只读边界");
      await expect(group).toContainText("已保留");
      await expect(group.getByRole("button", { name: /修改|重置|验证|启用|发送/ })).toHaveCount(0);
    }
    await page.screenshot({ path: `../.artifacts/issue-126/ui-${role}.png`, fullPage: true });
    await page.getByRole("button", { name: "搜索与采集服务", exact: false }).click();
    await expect(page.getByRole("button", { name: "修改", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "验证", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "使用指南", exact: true }).click();
    await expect(page.getByRole("dialog")).not.toContainText("随时可以重新开启");
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "运行与诊断" }).click();
    await expect(page.getByText("邮件和 Slack 外发已关闭", { exact: false })).toBeVisible();
    await expect(page.getByText("语义召回 (embedding)", { exact: true })).toBeVisible();
    await expect(page.getByText("断点续跑 (checkpoint)", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "测试", exact: true })).toHaveCount(2);
    expect(writes).toEqual([]);
    expect(errors).toEqual([]);
  });
}
