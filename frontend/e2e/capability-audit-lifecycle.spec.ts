import { expect, test, type Page, type Route } from "@playwright/test";

const actor = { user_id: "admin-a", username: "admin-a", display_name: "模拟管理员", role: "admin" };
const pack = { pack_id: "audit-pack", version: "1.0.0", scope: "personal", maturity: "draft", lifecycle: "active", eligibility: "eligible", source: "governance_event", owner_id: "owner-a", digest: `sha256:${"a".repeat(64)}`, can_validate: false, promotion_gaps: ["validation_incomplete"] };
const outcome = { status: "succeeded", content: "能力审计正文哨兵", truncated: false, failure_reason: null, event: { event_id: "cap-audit-1", result: "succeeded" } };

test("任务默认模型只列已配置项，刷新保留修改，保存使用工作台偏好", async ({ page }) => {
  let preference: object | null = null;
  let fail = false;
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: { ...actor, role: "super_admin" } });
    if (path === "/api/models") return route.fulfill({ json: { options: [
      { provider: "local", model: "Qwen3-30B-A3B", label: "本地模型" },
      { provider: "local", model: "Qwen3.8-27B-FP8", label: "本地模型" },
      { provider: "qwen", model: "未配置的预设", label: "未配置的预设" },
    ], default: null } });
    if (path === "/api/model-connections") return fail ? route.fulfill({ status: 503, json: {} }) : route.fulfill({ json: { items: [] } });
    if (path === "/api/model-connections/preferences/default") {
      if (route.request().method() === "PUT") { preference = route.request().postDataJSON(); return route.fulfill({ json: { ...preference, available: true } }); }
      return route.fulfill({ json: { preference } });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto("/settings?section=personal");
  const select = page.getByRole("combobox", { name: "默认任务模型", exact: true });
  await expect(select).toBeVisible();
  await expect(select.locator("option")).toHaveText(["自动选择可用模型", "Qwen3.8-27B-FP8", "Qwen3-30B-A3B"]);
  await expect(page.getByText("旧对话流程默认模型", { exact: true })).toHaveCount(0);
  const save = page.getByRole("button", { name: "保存默认模型", exact: true });
  await expect(save).toBeDisabled();
  await select.selectOption({ label: "Qwen3.8-27B-FP8" });
  await page.getByRole("button", { name: "刷新模型列表", exact: true }).click();
  await expect(select.locator("option:checked")).toHaveText("Qwen3.8-27B-FP8");
  await save.click();
  await expect(page.getByText("已保存，仅对新任务生效。", { exact: true })).toBeVisible();
  expect(preference).toEqual({ connection_id: "__local__", model_id: "Qwen3.8-27B-FP8" });
  fail = true;
  await page.getByRole("button", { name: "刷新模型列表", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("模型列表加载失败");
});

test("窄屏设置过滤云模型、失效默认需重选，退出可取消", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let writes = 0;
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET") writes++;
    if (path === "/api/auth/me") return route.fulfill({ json: { ...actor, role: "user" } });
    if (path === "/api/models") return route.fulfill({ json: { options: [{ provider: "local", model: "管理员本地模型" }] } });
    if (path === "/api/model-connections") return route.fulfill({ json: { items: [{
      connection_id: "cloud", owner_scope: "platform_shared", status: "verified", display_name: "共享模型", model: "ready",
      models: [{ model_id: "ready", display_name: "可用云模型", status: "available", enabled: true },
        { model_id: "disabled", display_name: "已停用", status: "available", enabled: false },
        { model_id: "unverified", display_name: "未验证", status: "pending", enabled: true }],
    }] } });
    if (path === "/api/model-connections/preferences/default") return route.fulfill({ json: { preference: { connection_id: "cloud", model_id: "disabled", available: false } } });
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto("/settings");
  const select = page.getByLabel("默认任务模型", { exact: true });
  await expect(select.locator("option")).toHaveText(["自动选择可用模型", "原默认模型已不可用，请重新选择", "共享模型 · 可用云模型"]);
  await expect(page.getByRole("button", { name: "保存默认模型" })).toBeDisabled();
  await expect(page.getByLabel("当前密码", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "修改密码", exact: true }).click();
  await page.getByLabel("显示密码", { exact: true }).check();
  await expect(page.getByLabel("新密码", { exact: true })).toHaveAttribute("type", "text");
  page.once("dialog", dialog => dialog.dismiss());
  await page.getByRole("button", { name: "退出所有设备", exact: true }).click();
  expect(writes).toBe(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.getByRole("heading", { name: "任务默认模型", exact: true }).scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("settings-mobile.png"), fullPage: true });
});

test("模型仅分本地与云端，云端按供应商组织且保存原连接身份", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  let saved: unknown;
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: actor });
    if (path === "/api/models") return route.fulfill({ json: { options: [{ provider: "local", model: "Qwen3.8-27B-FP8" }] } });
    if (path === "/api/model-connections") return route.fulfill({ json: { items: [
      { connection_id: "local", locality: "local", preset_id: null, display_name: "导入的本地模型", owner_scope: "platform_shared", status: "verified", model: "Qwen3.6-35B-A3B", models: [{ model_id: "Qwen3.6-35B-A3B", display_name: "Qwen3.6-35B-A3B", enabled: true, status: "available" }] },
      { connection_id: "cloud", locality: "public_external", preset_id: "qwen", display_name: "导入的阿里", owner_scope: "platform_shared", status: "verified", model: "qwen3.8-max", models: [{ model_id: "qwen3.8-max", display_name: "Qwen 3.8 Max", enabled: true, status: "available", current_catalog: true }, { model_id: "qwen3.7-max", display_name: "Qwen 3.7 Max", enabled: true, status: "available", current_catalog: false }] },
    ] } });
    if (path === "/api/model-connections/preferences/default") {
      if (route.request().method() === "PUT") { saved = route.request().postDataJSON(); return route.fulfill({ json: saved }); }
      return route.fulfill({ json: { preference: null } });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto("/settings");
  const select = page.getByLabel("默认任务模型", { exact: true });
  await expect(select.locator("optgroup")).toHaveCount(2);
  await expect(select.locator("optgroup").nth(0)).toHaveAttribute("label", "本地模型");
  await expect(select.locator("optgroup").nth(1)).toHaveAttribute("label", "云端模型");
  await expect(select.locator('optgroup[label="云端模型"] option')).toHaveText(["阿里百炼 · Qwen 3.8 Max"]);
  await select.selectOption({ label: "阿里百炼 · Qwen 3.8 Max" });
  await page.getByRole("button", { name: "保存默认模型", exact: true }).click();
  await expect.poll(() => saved).toEqual({ connection_id: "cloud", model_id: "qwen3.8-max" });
  await expect(page.getByLabel("当前密码", { exact: true })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("settings-compact-desktop.png"), fullPage: true });
  await page.getByRole("button", { name: "修改密码", exact: true }).click();
  await page.getByLabel("当前密码", { exact: true }).fill("synthetic-only");
  await page.getByRole("button", { name: "取消修改", exact: true }).click();
  await page.getByRole("button", { name: "修改密码", exact: true }).click();
  await expect(page.getByLabel("当前密码", { exact: true })).toHaveValue("");
  expect(errors).toEqual([]);
});

test("平台本地模型去重且保留已有默认连接", async ({ page }) => {
  let preference = { connection_id: "imported", model_id: "Qwen3.6-35B-A3B", available: true };
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: actor });
    if (path === "/api/models") return route.fulfill({ json: { options: [{ provider: "local", model: "Qwen3.6-35B-A3B" }, { provider: "local", model: "Qwen3.8-27B-FP8" }] } });
    if (path === "/api/model-connections") return route.fulfill({ json: { items: [{
      connection_id: "imported", owner_scope: "platform_shared", locality: "managed_private", display_name: "导入的本地模型", status: "verified", model: "Qwen3.6-35B-A3B",
      models: [{ model_id: "Qwen3.6-35B-A3B", display_name: "Qwen3.6-35B-A3B", enabled: true, status: "available" }],
    }] } });
    if (path === "/api/model-connections/preferences/default") {
      if (route.request().method() === "PUT") { preference = { ...route.request().postDataJSON(), available: true }; return route.fulfill({ json: preference }); }
      return route.fulfill({ json: { preference } });
    }
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto("/settings");
  const picker = page.getByLabel("默认任务模型", { exact: true });
  await expect(picker.locator("optgroup option")).toHaveText(["Qwen3.8-27B-FP8", "Qwen3.6-35B-A3B"]);
  await expect(picker).toHaveValue(JSON.stringify(["imported", "Qwen3.6-35B-A3B"]));
  await picker.selectOption({ label: "Qwen3.8-27B-FP8" });
  await page.getByRole("button", { name: "保存默认模型", exact: true }).click();
  await expect(page.getByText("已保存，仅对新任务生效。", { exact: true })).toBeVisible();
  await picker.selectOption({ label: "Qwen3.6-35B-A3B" });
  const unsaved = await picker.inputValue();
  await page.getByRole("button", { name: "刷新模型列表", exact: true }).click();
  await expect(picker).toHaveValue(unsaved);
  await expect(page.getByRole("button", { name: "保存默认模型", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "取消修改", exact: true }).click();
  await expect(picker).toHaveValue(JSON.stringify(["__local__", "Qwen3.8-27B-FP8"]));
});

test("平台配置跳转唯一模型管理入口，旧模型编辑不再展示", async ({ page }, testInfo) => {
  let writes = 0;
  let failConnections = false;
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (route.request().method() !== "GET") writes++;
    if (path === "/api/auth/me") return route.fulfill({ json: actor });
    if (path === "/api/config") return route.fulfill({ json: { groups: [{ key: "llm_local", label: "本地模型配置", items: [] }] } });
    if (path === "/api/config/models") return route.fulfill({ json: { models: { local: ["Qwen3.6-35B-A3B"] }, default_provider: "local", available_providers: ["local"] } });
    if (path === "/api/model-connections/presets") return route.fulfill({ json: { items: [] } });
    if (path === "/api/model-connections/preferences/default") return route.fulfill({ json: { preference: null } });
    if (path === "/api/settings/onboarding/model-connections") return route.fulfill({ json: { state: "completed" } });
    if (path === "/api/model-connections" && failConnections) return route.fulfill({ status: 503, json: {} });
    if (path === "/api/model-connections") return route.fulfill({ json: { items: [
      { connection_id: "local", owner_scope: "platform_shared", locality: "managed_private", status: "verified", display_name: "导入模型", models: [{ model_id: "Qwen3.6-35B-A3B", display_name: "Qwen3.6-35B-A3B", enabled: true, status: "available" }] },
      { connection_id: "cloud", owner_scope: "platform_shared", preset_id: "qwen", status: "verified", display_name: "阿里", models: [{ model_id: "qwen3.8-max", display_name: "Qwen 3.8 Max", enabled: true, status: "available" }] },
      { connection_id: "mine", owner_scope: "user_personal", status: "verified", display_name: "个人秘密模型", models: [{ model_id: "private-model", display_name: "个人秘密模型", enabled: true, status: "available" }] },
    ] } });
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto("/settings?section=platform");
  const inventory = page.getByRole("main");
  await expect(page.getByText("个人秘密模型", { exact: true })).toHaveCount(0);
  await expect(inventory.getByRole("link", { name: "管理平台模型" })).toHaveAttribute("href", "/settings?section=models&scope=platform");
  await page.screenshot({ path: testInfo.outputPath("platform-models-desktop.png"), fullPage: true });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("platform-models-mobile.png"), fullPage: true });
  await expect(page.locator("summary").filter({ hasText: "高级：旧流程模型参数" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "本地模型配置" })).toHaveCount(0);
  failConnections = false;
  await inventory.getByRole("link", { name: "管理平台模型" }).click();
  await expect(page.getByRole("tab", { name: "平台连接", exact: true })).toHaveAttribute("aria-selected", "true");
  expect(writes).toBe(0); expect(errors).toEqual([]);
});

test("同名云模型可区分个人与平台连接", async ({ page }) => {
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: actor });
    if (path === "/api/models") return route.fulfill({ json: { options: [] } });
    if (path === "/api/model-connections") return route.fulfill({ json: { items: ["user_personal", "platform_shared"].map(scope => ({
      connection_id: scope, owner_scope: scope, preset_id: "deepseek", display_name: "DeepSeek", status: "verified", model: "deepseek-flash",
      models: [{ model_id: "deepseek-flash", display_name: "DeepSeek V4.1 Flash", enabled: true, status: "available" }],
    })) } });
    if (path === "/api/model-connections/preferences/default") return route.fulfill({ json: { preference: null } });
    return route.fulfill({ status: 404, json: {} });
  });
  await page.goto("/settings");
  const options = page.getByLabel("默认任务模型", { exact: true }).locator("optgroup option");
  await expect(options).toHaveText(["DeepSeek · DeepSeek V4.1 Flash · 我的", "DeepSeek · DeepSeek V4.1 Flash · 平台"]);
});

for (const role of ["user", "admin", "super_admin"]) test(`工具验证仅保留在管理入口：${role}`, async ({ page }) => {
  let governanceReads = 0;
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: { ...actor, role } });
    if (path === "/api/models") return route.fulfill({ json: { options: [], available: [], default: null, document_default: null } });
    if (path.startsWith("/api/capability-governance/")) {
      governanceReads++;
      return route.fulfill({ json: { items: path.endsWith("/packs") ? [{ ...pack, can_validate: true }] : [] } });
    }
    return route.fulfill({ status: 404, json: { detail: "隔离API" } });
  });
  await page.goto("/settings?section=personal");
  await expect(page.getByText("外观", { exact: true })).toBeVisible();
  await expect(page.getByRole("heading", { name: "我的能力验证", exact: true })).toHaveCount(0);
  expect(governanceReads).toBe(0);
  await page.goto("/settings?section=governance");
  if (role === "user") {
    await expect(page.getByRole("heading", { name: "扩展工具管理", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "扩展工具管理", exact: true })).toHaveCount(0);
    expect(governanceReads).toBe(0);
  } else {
    await expect(page.getByRole("heading", { name: "扩展工具管理", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "发起验证", exact: true })).toBeVisible();
  }
});
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
