import { test, expect } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
let templates: any[], memories: any[];
const template = { template_id: "summary", version: 1, title: "证据摘要", purpose: "general", source: "owner_created", goal_contract_draft: "整理证据", method_draft: "保留原始单位", delivery_spec_draft: {}, summary_sha256: "sha256:" + "a".repeat(64) };
test.beforeEach(async ({ page }) => {
  templates = [{ ...template }]; memories = [{ memory_id: 1, summary: "不补造缺失值", purpose: "general", source: "user_entered", summary_sha256: "sha256:" + "b".repeat(64) }];
  await page.route("**/api/**", async route => {
    const url = route.request().url(), body = route.request().postDataJSON();
    let result: any = {};
    if (url.includes("context-options")) result = { templates, memories };
    else if (url.endsWith("context-preview")) result = { template: body.selection.template ? templates[0] : null, memories: body.selection.memories.length ? memories : [], proposed_changes: { goal_contract: templates[0]?.goal_contract_draft, method: templates[0]?.method_draft }, preview_sha256: "sha256:" + "c".repeat(64) };
    else if (url.endsWith("context-templates")) { result = { ...body.draft, summary_sha256: "sha256:" + "d".repeat(64) }; templates = [...templates.filter(item => item.template_id !== result.template_id), result]; }
    else if (url.includes("context-templates/") && route.request().method() === "DELETE") { templates = []; result = { retired: true }; }
    else if (url.endsWith("source-acquisitions/attempt-a")) result = { attempt_id: "attempt-a", snapshot: { snapshot_id: "snapshot-a", attempt_id: "attempt-a", created_at: "2026-09-09T00:00:00Z", valid_page_count: 1, failed_page_count: 0, allowed_scope: { kind: "exact_url", normalized_url: "https://example.invalid/source" }, coverage: { status: "scope_complete" }, failures: [], artifacts: [{ artifact_id: "artifact-a", content_sha256: "b".repeat(64), title: "网页证据", final_url: "https://example.invalid/source", text_preview: "工程合成证据" }] } };
    else if (url.includes("uploads/")) result = { upload_id: "upload-a", original_name: "原始附件.csv", media_type: "text/csv", size_bytes: 24, sha256: "a".repeat(64) };
    else if (url.endsWith("/memory/self")) { result = { ok: true }; memories.push({ memory_id: 2, summary: body.text, purpose: "general", source: "user_entered", summary_sha256: "sha256:" + "e".repeat(64) }); }
    else if (url.includes("/memory/self/")) { memories = memories.filter(item => item.memory_id !== Number(url.split("/").pop())); }
    await route.fulfill({ json: result });
  });
});
for (const mode of ["file", "web", "mixed"]) test(`${mode}显式应用相同模板记忆与目标输出`, async ({ page }) => {
  await page.goto(`/e2e/fixtures/task-context/index.html?mode=${mode}`);
  const select = page.getByLabel("任务模板（可选）"); await expect(select).toBeVisible();
  await select.selectOption(JSON.stringify(["summary", 1])); await page.getByText("个人记忆（可选）", { exact: true }).click(); await page.getByLabel("不补造缺失值", { exact: true }).check();
  const submit = page.getByRole("button", { name: mode === "file" ? "开始执行" : "启动任务", exact: true }); await expect(submit).toBeDisabled();
  await page.getByRole("button", { name: "检查上下文草案", exact: true }).click(); await expect(page.getByText("已检查，可以启动")).toBeVisible();
  await submit.click(); await expect.poll(() => page.evaluate(() => Boolean((window as any).submitted))).toBe(true);
  const payload = await page.evaluate(() => (window as any).submitted);
  expect(payload.prompt).toBe("按证据汇总费用，不猜测缺失值"); expect(payload.formats).toEqual(["json"]); expect(payload.taskContext.context_selection).toEqual({ template: { template_id: "summary", version: 1 }, memories: [{ memory_id: 1 }] });
  expect(payload.uploads.length).toBe(mode === "web" ? 0 : 1); expect(payload.sourceSnapshotIds).toEqual(mode === "file" ? [] : ["snapshot-a"]); if (mode === "file") expect(payload.sourceGoal).toBeUndefined(); else expect(payload.sourceGoal.quantity_requirement).toBe("当前已成功读取页面中有证据的内容");
});
test("原位创建编辑取消返回保留输入附件，版本变化不自动应用", async ({ page }) => {
  await page.goto("/e2e/fixtures/task-context/index.html?mode=mixed"); await page.getByLabel("任务模板（可选）").selectOption(JSON.stringify(["summary", 1]));
  await page.getByRole("button", { name: "管理任务模板与记忆" }).click(); await page.getByRole("button", { name: "编辑 证据摘要", exact: true }).click();
  await page.getByRole("textbox", { name: "方法建议", exact: true }).fill("新方法"); await page.getByRole("button", { name: "保存新版本", exact: true }).click();
  await expect(page.getByRole("dialog").getByText("证据摘要 · V2", { exact: true })).toBeVisible(); await page.getByRole("button", { name: "返回原任务", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "任务要求" })).toHaveValue("按证据汇总费用，不猜测缺失值"); await expect(page.getByText("原始附件.csv", { exact: true })).toBeVisible(); await expect(page.getByText("网页证据", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("alert")).toContainText("不会自动换成新版");
  await page.getByLabel("任务模板（可选）").selectOption(JSON.stringify(["summary", 2]));
  await page.getByRole("button", { name: "管理任务模板与记忆" }).click(); await page.getByLabel("模板名称").fill("取消的新模板"); await page.getByRole("button", { name: "取消编辑", exact: true }).click(); await expect(page.getByLabel("模板名称")).toHaveValue(""); await page.keyboard.press("Escape");
  await expect(page.getByRole("button", { name: "管理任务模板与记忆" })).toBeFocused();
});
test("创建模板与记忆，明确失败保留编辑草稿", async ({ page }) => {
  await page.goto("/e2e/fixtures/task-context/index.html?mode=file"); await page.getByRole("button", { name: "管理任务模板与记忆" }).click();
  await page.getByLabel("模板名称").fill("新摘要模板"); await page.getByRole("textbox", { name: "目标建议", exact: true }).fill("核对本次资料");
  await page.route("**/context-templates", route => route.fulfill({ status: 422, json: { detail: "建议与当前用户选择冲突，请修改" } }));
  await page.getByRole("button", { name: "保存新版本", exact: true }).click(); await expect(page.getByRole("alert")).toContainText("当前用户选择冲突"); await expect(page.getByLabel("模板名称")).toHaveValue("新摘要模板");
  await page.unroute("**/context-templates"); await page.getByRole("button", { name: "保存新版本", exact: true }).click(); await expect(page.getByRole("dialog").getByText("新摘要模板 · V1", { exact: true })).toBeVisible();
  await page.getByRole("textbox", { name: "新增个人偏好", exact: true }).fill("金额不四舍五入"); await page.getByRole("button", { name: "保存个人记忆", exact: true }).click(); await expect(page.getByRole("dialog").getByText("金额不四舍五入", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "删除记忆 2", exact: true }).click(); await expect(page.getByText("金额不四舍五入", { exact: true })).toHaveCount(0);
});
test("旧预览迟到不能确认新要求，失败可取消选择且保留附件", async ({ page }) => {
  await page.goto("/e2e/fixtures/task-context/index.html?mode=file"); await page.getByLabel("任务模板（可选）").selectOption(JSON.stringify(["summary", 1]));
  let release!: () => void;
  const held = new Promise<void>(resolve => { release = resolve; });
  let entered = false;
  await page.route("**/context-preview", async route => { entered = true; await held; await route.fulfill({ json: { template, memories: [], proposed_changes: {}, preview_sha256: "sha256:" + "c".repeat(64) } }); });
  await page.getByRole("button", { name: "检查上下文草案", exact: true }).click(); await expect.poll(() => entered).toBe(true);
  await page.getByRole("textbox", { name: "任务要求" }).fill("只按新要求处理"); release();
  await expect(page.getByRole("button", { name: "检查上下文草案", exact: true })).toBeEnabled(); await expect(page.getByRole("button", { name: "开始执行", exact: true })).toBeDisabled();
  await page.unroute("**/context-preview"); await page.route("**/context-preview", route => route.fulfill({ status: 404, json: { detail: "模板已停用" } }));
  await page.getByRole("button", { name: "检查上下文草案", exact: true }).click(); await expect(page.getByRole("alert")).toContainText("模板已停用");
  await page.getByLabel("任务模板（可选）").selectOption(""); await expect(page.getByRole("button", { name: "开始执行", exact: true })).toBeEnabled(); await expect(page.getByText("原始附件.csv", { exact: true })).toBeVisible();
});
test("模板保存结果未知只允许原版本原内容重放", async ({ page }) => {
  await page.goto("/e2e/fixtures/task-context/index.html?mode=file"); await page.getByRole("button", { name: "管理任务模板与记忆" }).click();
  await page.getByLabel("模板名称").fill("未知保存模板"); await page.getByRole("textbox", { name: "目标建议", exact: true }).fill("核对证据");
  const posts: any[] = [];
  await page.route("**/context-templates", async route => { posts.push(route.request().postDataJSON()); await route.fulfill({ status: 503, json: { detail: "保存结果未知" } }); });
  await page.getByRole("button", { name: "保存新版本", exact: true }).click(); await expect(page.getByLabel("模板名称")).toBeDisabled();
  await page.getByRole("button", { name: "确认原版本保存", exact: true }).click(); await expect.poll(() => posts.length).toBe(2); expect(posts[0]).toEqual(posts[1]);
  await page.getByRole("button", { name: "返回原任务", exact: true }).click(); await expect(page.getByText("原始附件.csv", { exact: true })).toBeVisible();
});
for (const width of [390, 1440]) test(`${width}键盘和明暗焦点域`, async ({ page }) => {
  await page.setViewportSize({ width, height: 850 }); await page.goto("/e2e/fixtures/task-context/index.html?mode=mixed"); await page.getByRole("button", { name: "管理任务模板与记忆" }).click();
  for (const dark of [false, true]) {
    await page.evaluate(value => document.documentElement.classList.toggle("dark", value), dark);
    for (let i = 0; i < 16; i++) { await page.keyboard.press("Tab"); expect(await page.evaluate(() => Boolean(document.activeElement?.closest('[role="dialog"]')))).toBe(true); }
    const box = await page.getByRole("dialog").boundingBox(); expect(box!.x).toBeGreaterThanOrEqual(0); expect(box!.x + box!.width).toBeLessThanOrEqual(width);
    expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
    await page.screenshot({ path: `test-results/task-context-${width}-${dark ? "dark" : "light"}.png` });
  }
  await page.keyboard.press("Escape"); await expect(page.getByRole("textbox", { name: "任务要求" })).toHaveValue("按证据汇总费用，不猜测缺失值");
});

test("旧修订仍明确沿用冻结上下文，不开放隐式更换模板", async ({ page }) => {
  await page.goto("/e2e/fixtures/task-context/index.html?mode=mixed&revision=2");
  await expect(page.getByText("本次保留原任务目标与上下文，确认后按当前完整资料创建新版本；旧版本不变。")).toBeVisible();
  await expect(page.getByRole("button", { name: "管理任务模板与记忆" })).toHaveCount(0);
  await expect(page.getByText("原始附件.csv", { exact: true })).toBeVisible();
  await expect(page.getByText("网页证据", { exact: true }).first()).toBeVisible();
});

test("已选记忆失效后可明确移除，刷新不锁死原任务资料", async ({ page }) => {
  await page.goto("/e2e/fixtures/task-context/index.html?mode=mixed");
  await page.getByText("个人记忆（可选）", { exact: true }).click(); await page.getByLabel("不补造缺失值", { exact: true }).check();
  await page.getByRole("button", { name: "管理任务模板与记忆" }).click();
  await page.getByRole("button", { name: "删除记忆 1", exact: true }).click();
  await expect(page.getByRole("dialog").getByText("不补造缺失值", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "返回原任务", exact: true }).click();
  await page.reload();
  await expect(page.getByRole("alert")).toContainText("不可用");
  await page.getByRole("button", { name: "移除已不可用的记忆 1", exact: true }).click();
  await page.getByRole("button", { name: "检查上下文草案", exact: true }).click();
  await page.getByRole("button", { name: "启动任务", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).submitted?.taskContext.context_selection.memories)).toEqual([]);
  const payload = await page.evaluate(() => (window as any).submitted);
  expect(payload.uploads.map((item: any) => item.upload_id)).toEqual(["upload-a"]);
  expect(payload.sourceSnapshotIds).toEqual(["snapshot-a"]); expect(payload.prompt).toBe("按证据汇总费用，不猜测缺失值");
});

test("未保存模板与记忆离开前确认，保留或明确放弃后返回任务", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 850 });
  await page.goto("/e2e/fixtures/task-context/index.html?mode=file"); await page.getByRole("button", { name: "管理任务模板与记忆" }).click();
  await page.getByLabel("模板名称").fill("未保存草稿"); await page.getByRole("textbox", { name: "新增个人偏好", exact: true }).fill("未保存记忆");
  await page.getByRole("button", { name: "返回原任务", exact: true }).click();
  await expect(page.getByRole("alertdialog", { name: "离开未保存编辑" })).toBeVisible();
  await expect(page.getByRole("button", { name: "继续编辑", exact: true })).toBeFocused();
  expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
  await page.getByRole("button", { name: "继续编辑", exact: true }).click();
  await expect(page.getByLabel("模板名称")).toHaveValue("未保存草稿"); await expect(page.getByRole("textbox", { name: "新增个人偏好", exact: true })).toHaveValue("未保存记忆");
  await page.keyboard.press("Escape"); await expect(page.getByRole("alertdialog", { name: "离开未保存编辑" })).toBeVisible();
  await page.getByRole("button", { name: "放弃未保存内容并返回", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "管理任务模板与记忆" }).click(); await expect(page.getByLabel("模板名称")).toHaveValue(""); await expect(page.getByRole("textbox", { name: "新增个人偏好", exact: true })).toHaveValue("");
  await page.keyboard.press("Escape"); await page.getByRole("button", { name: "开始执行", exact: true }).click();
  expect((await page.evaluate(() => (window as any).submitted)).uploads[0].upload_id).toBe("upload-a");
});

test("保存持续未知仍可明确放弃本地编辑返回，不能假称撤销", async ({ page }) => {
  await page.goto("/e2e/fixtures/task-context/index.html?mode=file"); await page.getByRole("button", { name: "管理任务模板与记忆" }).click();
  await page.getByLabel("模板名称").fill("未知保存模板"); await page.getByRole("textbox", { name: "目标建议", exact: true }).fill("按原始资料核对");
  const posts: any[] = [];
  await page.route("**/context-templates", route => { posts.push(route.request().postDataJSON()); return route.fulfill({ status: 503, json: { detail: "合成网络未知" } }); });
  await page.getByRole("button", { name: "保存新版本", exact: true }).click();
  await expect(page.getByRole("button", { name: "确认原版本保存", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "返回原任务", exact: true }).click();
  await expect(page.getByRole("alertdialog")).toContainText("服务端可能已经保存");
  await page.getByRole("button", { name: "继续编辑", exact: true }).click();
  await expect(page.getByRole("button", { name: "确认原版本保存", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "返回原任务", exact: true }).click();
  await page.getByRole("button", { name: "放弃本地待确认编辑并返回", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0); expect(posts).toHaveLength(1);
  await page.getByRole("textbox", { name: "任务要求" }).fill("继续原资料任务");
  await page.getByRole("button", { name: "开始执行", exact: true }).click();
  expect((await page.evaluate(() => (window as any).submitted)).uploads[0].upload_id).toBe("upload-a");
});
