import { test, expect, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
async function fixture(page: Page) {
    const control = { pause: false, unknown: false, task: null as any, created: false };
    const writes: any[] = [];
    const scope = { kind: "connector", protocol: "http_api", connection_id: "http-synthetic", connection_version: "b".repeat(64), configuration_version: "c".repeat(64), selection_sha256: "f".repeat(64), selection: { source_type: "http_api", url: "https://records.example.invalid/items" } };
    const snapshot = { snapshot_id: "connector-snapshot", attempt_id: "connector-attempt", source_kind: "connector", allowed_scope: scope, artifact_count: 1, valid_page_count: 1, failed_page_count: 0, created_at: "2026-09-09T00:00:00Z", coverage: { status: "coverage_unknown", limit_reached: false, attempted_page_count: 1, required_valid_pages: null }, failures: [], artifacts: [{ artifact_id: "connector-artifact", title: "记录原件.json", final_url: scope.selection.url, request_url: scope.selection.url, read_at: "2026-09-09T00:00:00Z", content_sha256: "d".repeat(64), media_type: "application/json", size_bytes: 20, text_preview: '[{"name":"合成记录"}]' }] };
    await page.context().grantPermissions(["local-network-access"], { origin: new URL(test.info().project.use.baseURL as string).origin });
    await page.route("**/*", async (route) => {
        const path = new URL(route.request().url()).pathname;
        if (new URL(route.request().url()).origin !== new URL(test.info().project.use.baseURL as string).origin)
            return route.abort();
        if (route.request().isNavigationRequest() && path === "/data-prep")
            return route.fulfill({ response: await route.fetch({ url: new URL("/e2e/fixtures/connector-source/app.html", test.info().project.use.baseURL as string).href }) });
        if (!path.startsWith("/api/"))
            return route.continue();
        if (path === "/api/data-sources/uploads/original-file")
            return route.fulfill({ json: { upload_id: "original-file", original_name: "原附件.csv", media_type: "text/csv", size_bytes: 20, sha256: "a".repeat(64) } });
        const body = route.request().headers()["content-type"]?.includes("application/json") ? route.request().postDataJSON() : null;
        if (route.request().method() !== "GET")
            writes.push({ path, body, key: route.request().headers()["idempotency-key"] });
        const models = { provider: "local", model: "fixture", label: "合成模型" };
        const data: Record<string, unknown> = {
            "/api/auth/me": { user_id: "owner-a", username: "owner-a", role: "admin" },
            "/api/models": { options: [models], default: models, pi_runtime_enabled: true, pi_capability_host_enabled: true },
            "/api/model-connections": { items: [] }, "/api/model-connections/presets": { presets: [] }, "/api/model-connections/preferences/default": { preference: null },
            "/api/settings/onboarding/model-connections": { completed: true },
            "/api/semantic-workspace/guidance": { onboarding: [], examples: [] }, "/api/semantic-workspace/capabilities": { enabled: true, items: [] },
            "/api/semantic-workspace/storage": { task_count: 0, recycle_bin_count: 0, upload_bytes: 0, delivery_bytes: 0, total_bytes: 0 },
            "/api/data-sources/uploads": { upload_id: "original-file", original_name: "原附件.csv", media_type: "text/csv", size_bytes: 20, sha256: "a".repeat(64) },
            "/api/data-tasks/preview": { schema: { fields: [{ name: "姓名", dtype: "string", nullable: false }] }, sample: [{ 姓名: "合成附件" }], estimated_records: 1 },
        };
        if (path in data)
            return route.fulfill({ json: data[path] });
        if (path === "/api/connector-sources/resolve") {
            scope.selection = body;
            return route.fulfill({ json: scope });
        }
        if (path.endsWith("/acquisitions") && control.unknown)
            return route.abort();
        if (path.endsWith("/acquisitions") && control.pause && !body.resume_checkpoint)
            return route.fulfill({ status: 202, json: { attempt_id: "connector-attempt", idempotency_key: route.request().headers()["idempotency-key"], allowed_scope: scope, status: "acquiring", snapshot_id: null, snapshot: null, error_code: "connector_checkpoint_ready" } });
        if (path.endsWith("/acquisitions"))
            return route.fulfill({ status: 202, json: { attempt_id: "connector-attempt", idempotency_key: route.request().headers()["idempotency-key"], allowed_scope: scope, status: "succeeded", snapshot_id: snapshot.snapshot_id, snapshot, error_code: null } });
        if (path === "/api/semantic-workspace/context-options")
            return route.fulfill({ json: { templates: [], memories: [] } });
        if (path.endsWith("context-preview"))
            return route.fulfill({ json: { template: null, memories: [], proposed_changes: {}, preview_sha256: "sha256:" + "e".repeat(64) } });
        if (path === "/api/semantic-workspace/tasks") {
            if (route.request().method() === "GET")
                return route.fulfill({ json: control.created ? [control.task] : [] });
            control.created = true;
            return route.fulfill({ json: control.task ?? { task_id: "created-connector-task" } });
        }
        if (path === "/api/semantic-workspace/tasks/created-connector-task")
            return route.fulfill({ json: control.task });
        if (path.endsWith("/turns"))
            return route.fulfill({ json: { turns: [], results: [], proposals: [] } });
        if (path.endsWith("/stream"))
            return route.fulfill({ contentType: "text/event-stream", body: "" });
        return route.fulfill({ json: {} });
    });
    return { writes, scope, snapshot, control };
}
for (const cloud of [false, true])
    test(`J1 ${cloud ? "云连接" : "本地"} 真实工作台保留附件，连接原件进入冻结任务与正式结果预览`, async ({ page }) => {
        const { writes, scope, snapshot, control } = await fixture(page);
        const errors: string[] = [];
        page.on("pageerror", error => errors.push(error.message));
        control.task = {
            task_id: "created-connector-task", title: "连接与附件核对", objective_text: "核对连接记录和原附件并输出表格", upload_ids: ["original-file"],
            output_formats: ["xlsx"], provider: "local", model: "fixture", runtime_version: "legacy", external_api_confirmed: false,
            status: "completed", active_revision: 1, current_revision: 1, viewing_revision: 1, plan_id: "plan", logical_revision: 1, binding_revision: 1,
            run_id: "run", summary: "合成正式交付已验证", error: null, question: null, cancel_requested: false, deleted_at: null, purge_after: null,
            created_at: "2026-09-09T00:00:00Z", updated_at: "2026-09-09T00:00:01Z", revisions: [], events: [], attempts: [], harness_events: [], plan: null, run: null,
            uploads: [{ upload_id: "original-file", original_name: "原附件.csv", media_type: "text/csv", size_bytes: 20, sha256: "a".repeat(64) }],
            web_sources: [{ source_snapshot_id: snapshot.snapshot_id, snapshot }],
            delivery: { delivery_id: "delivery", run_id: "run", plan_id: "plan", status: "published", requested_formats: ["xlsx"], created_at: "2026-09-09T00:00:01Z",
                outputs: [{ output_id: "formal-output", format: "xlsx", filename: "核对结果.xlsx", media_type: "application/octet-stream", sha256: "e".repeat(64), size_bytes: 64,
                        qa: { openable: true, checks: ["可打开", "字段完整"], warnings: [] }, download_url: "/api/semantic-delivery/outputs/formal-output" }] },
        };
        control.task.revisions = [{ ...control.task, revision: 1, change_summary: "" }];
        if (cloud) {
            await page.route("**/api/model-connections", route => route.fulfill({ json: { items: [{ connection_id: "cloud", owner_scope: "user_personal", display_name: "合成云连接", api_format: "openai_chat_completions", locality: "public", status: "verified", default_model: "fixture", models: [{ model_id: "fixture", display_name: "合成模型", status: "available", enabled: true }] }] } }));
        }
        await page.route("**/sources/*/preview?*", route => {
            const id = new URL(route.request().url()).pathname.split("/").at(-2)!;
            return route.fulfill({ json: { task_id: "created-connector-task", revision: 1, artifact_id: id, sha256: (id === "connector-artifact" ? "d" : "a").repeat(64),
                    original_name: id === "connector-artifact" ? "记录原件.json" : "原附件.csv", kind: "table", source_kind: id === "connector-artifact" ? "connector_artifact" : "upload",
                    representation: { kind: "source", parser_or_inspector_version: "fixture" }, is_complete: true, truncated: false,
                    columns: ["name"], rows: [{ row_number: 1, values: { name: id === "connector-artifact" ? "连接冻结原件行" : "原附件行" } }], total: 1, offset: 0, limit: 100 } });
        });
        await page.route("**/api/semantic-workspace/tasks/created-connector-task/preview?*", route => route.fulfill({ json: { task_id: "created-connector-task", revision: 1, delivery_id: "delivery", output_id: "formal-output", representation: { kind: "output", associated_output_id: "formal-output", sha256: "e".repeat(64) }, kind: "table", columns: ["结果"], rows: [{ 结果: "正式合成结果" }], total: 1, offset: 0, limit: 100 } }));
        await page.goto("/data-prep");
        await page.locator('input[type="file"]').first().setInputFiles({ name: "原附件.csv", mimeType: "text/csv", buffer: Buffer.from("姓名\n合成附件", "utf8") });
        await page.locator("textarea").first().fill(control.task.objective_text);
        await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
        await page.getByLabel("公开数据地址", { exact: true }).fill(scope.selection.url);
        await page.getByRole("button", { name: "核对读取范围", exact: true }).click();
        await page.getByRole("button", { name: "读取并冻结资料", exact: true }).click();
        await page.getByRole("button", { name: "加入当前任务", exact: true }).click();
        if (cloud)
            await page.getByRole("combobox", { name: "模型连接", exact: true }).selectOption("cloud");
        await page.getByRole("button", { name: "检查上下文草案", exact: true }).click();
        if (cloud) {
            await expect(page.getByText(/全部已选连接资料的记录、字段与来源信息/)).toBeVisible();
            await expect(page.getByRole("button", { name: "启动任务", exact: true })).toBeDisabled();
            expect(writes.filter(item => item.path === "/api/semantic-workspace/tasks")).toHaveLength(0);
            await page.getByRole("checkbox", { name: "我确认将上述内容发送到 合成云连接", exact: true }).check();
        }
        await page.getByRole("button", { name: "启动任务", exact: true }).click();
        await expect(page).toHaveURL(/task=created-connector-task/);
        expect(writes.find(item => item.path === "/api/semantic-workspace/tasks").body).toMatchObject({ source_snapshot_ids: [snapshot.snapshot_id], upload_ids: ["original-file"] });
        if (cloud)
            expect(writes.find(item => item.path === "/api/semantic-workspace/tasks").body).toMatchObject({ model_connection_id: "cloud", external_api_confirmed: true });
        await expect(page.getByText("1 个连接原件", { exact: false })).toBeVisible();
        await expect(page.getByRole("button", { name: "获取最新连接资料", exact: true })).toBeVisible();
        await page.getByRole("button", { name: "查看结果", exact: true }).click();
        await expect(page.getByText("正式合成结果", { exact: true })).toBeVisible();
        await page.getByRole("button", { name: "原文件预览", exact: true }).click();
        await page.getByLabel("预览文件", { exact: true }).selectOption("connector-artifact");
        await expect(page.getByText("连接冻结原件行", { exact: true })).toBeVisible();
        control.task.source_integrity = { deleted_source_keys: ["connector_artifact:connector-artifact"], state: "source_deleted", can_rerun: false, can_reverify: false };
        await page.reload();
        await page.getByRole("button", { name: "原文件预览", exact: true }).click();
        await page.getByLabel("预览文件", { exact: true }).selectOption("connector-artifact");
        await expect(page.getByRole("alert").filter({ hasText: "来源已删除，原文不可再读取" })).toBeVisible();
        await expect(page.getByText("连接冻结原件行", { exact: true })).toHaveCount(0);
        expect(errors).toEqual([]);
    });
test("R1 历史数据库资料显示原表字段并预览复用，不重新联网", async ({ page }) => {
    const { scope, snapshot, writes } = await fixture(page);
    Object.assign(scope, { protocol: "database", connection_id: "owned-db", selection: { source_type: "database", connection_id: "owned-db", table: "orders", fields: ["region"], filters: [{ field: "region", op: "eq", value: "华东" }] } });
    const item = { source_key: "snapshot:connector-snapshot", kind: "snapshot", identity: "original", source_snapshot_id: snapshot.snapshot_id, attempt_id: snapshot.attempt_id, label: "历史订单记录", sha256: "a".repeat(64), availability: "available", acquired_at: snapshot.created_at, time_kind: "acquired", origin: { task_id: "origin-task", revision: 1 }, allowed_scope: scope, coverage: snapshot.coverage };
    await page.route("**/api/semantic-workspace/reusable-sources{,?*}", route => route.fulfill({ json: { items: [item], total: 1, next_cursor: null, snapshot_token: "catalog", page_complete: true } }));
    await page.route("**/api/semantic-workspace/reusable-sources/resolve", route => route.fulfill({ json: { items: [item] } }));
    await page.route("**/api/semantic-workspace/source-acquisitions/connector-attempt", route => route.fulfill({ json: { attempt_id: snapshot.attempt_id, snapshot, status: "succeeded" } }));
    await page.goto("/e2e/fixtures/connector-source/index.html");
    await page.getByRole("button", { name: "历史资料", exact: true }).click();
    await expect(page.getByText("数据库：owned-db · 表：orders", { exact: true })).toBeVisible();
    await expect(page.getByText("字段：region", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "预览 历史订单记录", exact: true }).click();
    await expect(page.getByText("连接记录摘要预览，可能截断；完整内容沿任务资料包保留。", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "返回资料列表", exact: true }).click();
    await page.getByRole("checkbox", { name: "选择 历史订单记录", exact: true }).check();
    await page.getByRole("button", { name: "添加 1 份资料", exact: true }).click();
    await expect(page.getByText("连接资料 · 1 个原件", { exact: true })).toBeVisible();
    expect(writes.filter(item => item.path.includes("/connector-sources/"))).toHaveLength(0);
});
test("I3 另一页替换读取身份，旧响应不能覆盖存储或加入原件", async ({ page }) => {
    const { scope, snapshot } = await fixture(page);
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    let entered = false;
    await page.route("**/api/connector-sources/acquisitions", async (route) => { entered = true; await pending; return route.fulfill({ status: 202, json: { attempt_id: snapshot.attempt_id, idempotency_key: route.request().headers()["idempotency-key"], allowed_scope: scope, status: "succeeded", snapshot_id: snapshot.snapshot_id, snapshot } }); });
    await page.goto("/e2e/fixtures/connector-source/index.html");
    await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
    await page.getByLabel("公开数据地址", { exact: true }).fill(scope.selection.url);
    await page.getByRole("button", { name: "核对读取范围", exact: true }).click();
    await page.getByRole("button", { name: "读取并冻结资料", exact: true }).click();
    await expect.poll(() => entered).toBe(true);
    const changed = await page.evaluate(() => { const key = Object.keys(localStorage).find(key => key.startsWith("mangrove_connector_attempt_"))!; const next = JSON.parse(localStorage.getItem(key)!); next.key = "other-page-key"; const raw = JSON.stringify(next); localStorage.setItem(key, raw); return { key, raw }; });
    release();
    await expect(page.getByRole("alert").filter({ hasText: "另一页面" })).toBeVisible();
    await expect(page.getByRole("button", { name: "加入当前任务", exact: true })).toHaveCount(0);
    expect(await page.evaluate(key => localStorage.getItem(key), changed.key)).toBe(changed.raw);
});
test("I4 主任务草稿被另一页更新时关连接面板，保留原读取待确认", async ({ page }) => {
    const { scope, snapshot } = await fixture(page);
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    let entered = false;
    await page.route("**/api/connector-sources/acquisitions", async (route) => { entered = true; await pending; return route.fulfill({ status: 202, json: { attempt_id: snapshot.attempt_id, idempotency_key: route.request().headers()["idempotency-key"], allowed_scope: scope, status: "succeeded", snapshot_id: snapshot.snapshot_id, snapshot } }); });
    await page.goto("/e2e/fixtures/connector-source/index.html");
    await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
    await page.getByLabel("公开数据地址", { exact: true }).fill(scope.selection.url);
    await page.getByRole("button", { name: "核对读取范围", exact: true }).click();
    await page.getByRole("button", { name: "读取并冻结资料", exact: true }).click();
    await expect.poll(() => entered).toBe(true);
    const changed = await page.evaluate(() => { const key = "mangrove_workspace_draft_owner-a_new"; const oldValue = localStorage.getItem(key); const next = JSON.parse(oldValue!); next.draft.prompt = "另一页面的新要求"; const raw = JSON.stringify(next); localStorage.setItem(key, raw); window.dispatchEvent(new StorageEvent("storage", { key, oldValue, newValue: raw, storageArea: localStorage })); return { key, raw }; });
    release();
    await expect(page.getByRole("dialog", { name: "从已有连接读取", exact: true })).toHaveCount(0);
    await expect(page.getByRole("alert").filter({ hasText: "当前草稿已在其他页面更新" })).toBeVisible();
    await expect.poll(() => page.evaluate(() => JSON.parse(localStorage.getItem("mangrove_connector_attempt_owner-a_new") || "null")?.attemptId)).toBe(snapshot.attempt_id);
    expect(await page.evaluate(key => localStorage.getItem(key), changed.key)).toBe(changed.raw);
    await expect(page.getByText("连接资料 · 1 个原件", { exact: true })).toHaveCount(0);
    await page.reload();
    await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
    await expect(page.getByRole("button", { name: "确认原读取结果", exact: true })).toBeVisible();
});
for (const [width, theme] of [[390, "light"], [390, "dark"], [720, "light"], [1440, "dark"]] as const)
    test(`V1 ${width} ${theme} 连接弹窗键盘输入法与可见范围`, async ({ page }, info) => {
        await fixture(page);
        await page.setViewportSize({ width, height: width === 720 ? 450 : 900 });
        await page.goto("/e2e/fixtures/connector-source/index.html");
        await page.evaluate(theme => document.documentElement.classList.toggle("dark", theme === "dark"), theme);
        const opener = page.getByRole("button", { name: "从已有连接读取", exact: true });
        await opener.click();
        const dialog = page.getByRole("dialog", { name: "从已有连接读取", exact: true });
        const url = dialog.getByRole("textbox", { name: "公开数据地址", exact: true });
        await url.fill("https://records.example.invalid/items");
        await url.dispatchEvent("keydown", { key: "Escape", isComposing: true });
        await expect(dialog).toBeVisible();
        await dialog.getByRole("combobox", { name: "资料来源", exact: true }).focus();
        await page.keyboard.press("Shift+Tab");
        await expect(dialog.getByRole("button", { name: "返回原任务", exact: true })).toBeFocused();
        expect(await dialog.evaluate(node => node.scrollWidth <= node.clientWidth + 1)).toBe(true);
        expect((await new AxeBuilder({ page }).include('[role="dialog"]').analyze()).violations).toEqual([]);
        await page.screenshot({ path: info.outputPath(`connector-${width}-${theme}.png`) });
        await page.keyboard.press("Escape");
        await expect(dialog).toHaveCount(0);
        await expect(opener).toBeFocused();
        await opener.click();
        await expect(url).toHaveValue("https://records.example.invalid/items");
    });
test("H1 公开HTTP来源先核范围再读取预览，显式加入完整任务", async ({ page }) => {
    const { writes, scope } = await fixture(page);
    await page.goto("/e2e/fixtures/connector-source/index.html");
    await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
    await page.getByLabel("公开数据地址", { exact: true }).fill(scope.selection.url);
    await page.getByRole("button", { name: "核对读取范围", exact: true }).click();
    await expect(page.getByText("连接版本已锁定", { exact: true })).toBeVisible();
    expect(writes.filter(item => item.path.endsWith("acquisitions"))).toHaveLength(0);
    await page.getByRole("button", { name: "读取并冻结资料", exact: true }).click();
    await expect(page.getByText('[{"name":"合成记录"}]', { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "加入当前任务", exact: true }).click();
    await expect(page.getByText("连接资料 · 1 个原件", { exact: true })).toBeVisible();
    await expect(page.getByLabel("数量要求", { exact: true })).toHaveValue("当前已成功读取资料中有证据的内容");
    await expect(page.getByLabel("完整性要求", { exact: true })).toHaveValue("逐来源披露失败、范围和未覆盖内容，不承诺来源完整");
    await page.getByRole("button", { name: "检查上下文草案", exact: true }).click();
    await page.getByRole("button", { name: "启动任务", exact: true }).click();
    await expect(page.getByText("任务已创建：created-connector-task", { exact: true })).toBeVisible();
    const acquisition = writes.find(item => item.path.endsWith("acquisitions"));
    expect(acquisition.body.expected_connection_version).toBe("b".repeat(64));
    expect(acquisition.key).toBeTruthy();
    expect(writes.find(item => item.path === "/api/semantic-workspace/tasks").body).toMatchObject({ source_snapshot_ids: ["connector-snapshot"], upload_ids: ["original-file"], output_formats: ["json"] });
});
test("H6 页码读取先明确上限，长任务要求不能变成无法恢复的422", async ({ page }) => {
    const { writes, scope } = await fixture(page);
    await page.goto("/e2e/fixtures/connector-source/index.html");
    await page.locator("textarea").first().fill("长".repeat(501));
    await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
    await page.getByLabel("公开数据地址", { exact: true }).fill(scope.selection.url);
    await page.getByLabel("按页码读取", { exact: true }).check();
    await page.getByLabel("最多读取页数", { exact: true }).fill("12");
    await page.getByRole("button", { name: "核对读取范围", exact: true }).click();
    await expect(page.getByRole("button", { name: "读取并冻结资料", exact: true })).toBeDisabled();
    expect(writes.filter(item => item.path.endsWith("acquisitions"))).toHaveLength(0);
    await page.getByRole("textbox", { name: "本次读取用途", exact: true }).fill("核对十二页记录");
    await page.getByRole("button", { name: "读取并冻结资料", exact: true }).click();
    expect(writes.find(item => item.path.endsWith("/resolve")).body.pagination).toEqual({ strategy: "page", options: { page_param: "page", per_page_param: "per_page", per_page: 100, start_page: 1, max_pages: 12 } });
    expect(writes.find(item => item.path.endsWith("acquisitions")).body.purpose).toBe("核对十二页记录");
});
test("H7 首次读取前连接已换版明确重新核对，不能锁死在原请求重放", async ({ page }) => {
    const { scope, writes } = await fixture(page);
    await page.route("**/api/connector-sources/acquisitions", route => route.fulfill({ status: 409, json: { detail: "连接版本变化，请重新确认" } }));
    await page.goto("/e2e/fixtures/connector-source/index.html");
    await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
    await page.getByLabel("公开数据地址", { exact: true }).fill(scope.selection.url);
    await page.getByRole("button", { name: "核对读取范围", exact: true }).click();
    await page.getByRole("button", { name: "读取并冻结资料", exact: true }).click();
    await expect(page.getByRole("button", { name: "核对读取范围", exact: true })).toBeEnabled();
    await expect(page.getByRole("button", { name: "确认原读取结果", exact: true })).toHaveCount(0);
    await expect(page.getByRole("alert")).toContainText("连接版本变化");
    expect(writes.filter(item => item.path.endsWith("/resolve"))).toHaveLength(1);
});
test("H2 分段暂停只显式继续原请求，关闭重开不重复读取", async ({ page }) => {
    const { writes, scope, control } = await fixture(page);
    control.pause = true;
    await page.goto("/e2e/fixtures/connector-source/index.html");
    await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
    await page.getByLabel("公开数据地址", { exact: true }).fill(scope.selection.url);
    await page.getByRole("button", { name: "核对读取范围", exact: true }).click();
    await page.getByRole("button", { name: "读取并冻结资料", exact: true }).click();
    await expect(page.getByText("已暂停在安全断点", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "返回原任务", exact: true }).click();
    await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
    expect(writes.filter(item => item.path.endsWith("acquisitions"))).toHaveLength(1);
    await page.getByRole("button", { name: "继续本次读取", exact: true }).click();
    await expect(page.getByRole("button", { name: "加入当前任务", exact: true })).toBeVisible();
    const reads = writes.filter(item => item.path.endsWith("acquisitions"));
    expect(reads).toHaveLength(2);
    expect(reads[1].key).toBe(reads[0].key);
    expect(reads[1].body).toEqual({ ...reads[0].body, resume_checkpoint: true });
});
test("H3 首次响应丢失后刷新保留原key和选择，明确确认才重放", async ({ page }) => {
    const { writes, scope, control } = await fixture(page);
    control.unknown = true;
    await page.goto("/e2e/fixtures/connector-source/index.html");
    await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
    await page.getByLabel("公开数据地址", { exact: true }).fill(scope.selection.url);
    await page.getByRole("button", { name: "核对读取范围", exact: true }).click();
    await page.getByRole("button", { name: "读取并冻结资料", exact: true }).click();
    await expect(page.getByRole("button", { name: "确认原读取结果", exact: true })).toBeVisible();
    await page.reload();
    await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
    await expect(page.getByLabel("公开数据地址", { exact: true })).toHaveValue(scope.selection.url);
    expect(writes.filter(item => item.path.endsWith("acquisitions"))).toHaveLength(1);
    control.unknown = false;
    await page.getByRole("button", { name: "确认原读取结果", exact: true }).click();
    await expect(page.getByRole("button", { name: "加入当前任务", exact: true })).toBeVisible();
    const reads = writes.filter(item => item.path.endsWith("acquisitions"));
    expect(reads).toHaveLength(2);
    expect(reads[1]).toEqual(reads[0]);
    expect(await page.evaluate(() => Object.values(localStorage).some(value => value.includes('"name":"合成记录"')))).toBe(false);
});
test("H4 停止未知沿真实GET路由确认，不能提前冒称已取消", async ({ page }) => {
    const { writes, scope, control } = await fixture(page);
    control.pause = true;
    let confirmed = false;
    await page.route("**/api/semantic-workspace/source-acquisitions/connector-attempt{,/cancel}", route => route.fulfill({ json: { attempt_id: "connector-attempt", idempotency_key: writes.find(item => item.path.endsWith("acquisitions"))?.key, allowed_scope: scope, status: confirmed ? "canceled" : "cancelling", snapshot: null, error_code: confirmed ? null : "connector_cleanup_unknown" } }));
    await page.goto("/e2e/fixtures/connector-source/index.html");
    await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
    await page.getByLabel("公开数据地址", { exact: true }).fill(scope.selection.url);
    await page.getByRole("button", { name: "核对读取范围", exact: true }).click();
    await page.getByRole("button", { name: "读取并冻结资料", exact: true }).click();
    await page.getByRole("button", { name: "停止本次读取", exact: true }).click();
    await expect(page.getByText("停止尚未确认", { exact: true })).toBeVisible();
    await expect(page.getByText("已停止读取", { exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "加入当前任务", exact: true })).toHaveCount(0);
    confirmed = true;
    await page.getByRole("button", { name: "检查停止状态", exact: true }).click();
    await expect(page.getByText("已停止读取", { exact: true })).toBeVisible();
});
test("H8 停止网络未知不能继续旧断点，只查原状态后才恢复继续", async ({ page }) => {
    const { scope, control, writes } = await fixture(page);
    control.pause = true;
    let queries = 0;
    await page.route("**/api/semantic-workspace/source-acquisitions/connector-attempt/cancel", route => route.fulfill({ status: 503, json: { detail: "停止响应未知" } }));
    await page.route("**/api/semantic-workspace/source-acquisitions/connector-attempt", route => { queries++; return route.fulfill({ json: { attempt_id: "connector-attempt", idempotency_key: writes.find(item => item.path.endsWith("acquisitions"))?.key, allowed_scope: scope, status: "acquiring", snapshot: null, error_code: "connector_checkpoint_ready" } }); });
    await page.goto("/e2e/fixtures/connector-source/index.html");
    await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
    await page.getByLabel("公开数据地址", { exact: true }).fill(scope.selection.url);
    await page.getByRole("button", { name: "核对读取范围", exact: true }).click();
    await page.getByRole("button", { name: "读取并冻结资料", exact: true }).click();
    await page.getByRole("button", { name: "停止本次读取", exact: true }).click();
    await expect(page.getByRole("button", { name: "确认原读取结果", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "继续本次读取", exact: true })).toHaveCount(0);
    await expect(page.getByRole("button", { name: "停止本次读取", exact: true })).toHaveCount(0);
    expect(writes.filter(item => item.path.endsWith("acquisitions"))).toHaveLength(1);
    await page.getByRole("button", { name: "确认原读取结果", exact: true }).click();
    expect(queries).toBe(1);
    await expect(page.getByRole("button", { name: "继续本次读取", exact: true })).toBeEnabled();
    await page.getByRole("button", { name: "继续本次读取", exact: true }).click();
    const reads = writes.filter(item => item.path.endsWith("acquisitions"));
    expect(reads).toHaveLength(2);
    expect(reads[1].body.resume_checkpoint).toBe(true);
    expect(reads[1].key).toBe(reads[0].key);
});
test("H5 继续在途仍可停止，迟到成功不能覆盖已确认停止", async ({ page }) => {
    const { scope, snapshot, control } = await fixture(page);
    control.pause = true;
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    let key = "";
    let resumed = false;
    let cancels = 0;
    await page.route("**/api/connector-sources/acquisitions", async (route) => {
        key = route.request().headers()["idempotency-key"];
        if (!route.request().postDataJSON().resume_checkpoint)
            return route.fallback();
        resumed = true;
        await pending;
        return route.fulfill({ status: 202, json: { attempt_id: "connector-attempt", idempotency_key: key, allowed_scope: scope, status: "succeeded", snapshot_id: snapshot.snapshot_id, snapshot } });
    });
    await page.route("**/api/semantic-workspace/source-acquisitions/connector-attempt/cancel", route => {
        cancels++;
        return route.fulfill({ json: { attempt_id: "connector-attempt", idempotency_key: key, allowed_scope: scope, status: "canceled", snapshot: null } });
    });
    await page.goto("/e2e/fixtures/connector-source/index.html");
    await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
    await page.getByLabel("公开数据地址", { exact: true }).fill(scope.selection.url);
    await page.getByRole("button", { name: "核对读取范围", exact: true }).click();
    await page.getByRole("button", { name: "读取并冻结资料", exact: true }).click();
    await page.getByRole("button", { name: "继续本次读取", exact: true }).click();
    await expect.poll(() => resumed).toBe(true);
    try {
        await expect(page.getByRole("button", { name: "停止本次读取", exact: true })).toBeEnabled();
        await page.getByRole("button", { name: "停止本次读取", exact: true }).click();
        await expect(page.getByText("已停止读取", { exact: true })).toBeVisible();
        expect(cancels).toBe(1);
    }
    finally {
        release();
    }
    await expect(page.getByText("已停止读取", { exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "加入当前任务", exact: true })).toHaveCount(0);
});
test("D1 复用本人数据库连接和表字段筛选，不创建凭据或SQL", async ({ page }) => {
    const { writes } = await fixture(page);
    await page.route("**/api/data-sources/connections", route => route.fulfill({ json: [{ connection_id: "owned-db", name: "已登记订单库", dialect: "sqlite", database_name: "synthetic" }] }));
    await page.route("**/api/data-sources/connections/owned-db/schema", route => route.fulfill({ json: { default_schema: "main", tables: [{ name: "orders", schema: "main", primary_key: ["id"], columns: [{ name: "id", type: "INTEGER" }, { name: "region", type: "TEXT" }] }] } }));
    await page.goto("/e2e/fixtures/connector-source/index.html");
    await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
    await page.getByRole("combobox", { name: "资料来源", exact: true }).selectOption("database");
    await page.getByRole("combobox", { name: "已登记数据库连接", exact: true }).selectOption("owned-db");
    await page.getByRole("combobox", { name: "读取哪张表", exact: true }).selectOption("orders");
    await page.getByLabel("读取字段 region", { exact: true }).check();
    await page.getByRole("combobox", { name: "筛选字段", exact: true }).selectOption("region");
    await page.getByLabel("等于这个值", { exact: true }).fill("华东");
    await page.getByRole("button", { name: "核对读取范围", exact: true }).click();
    const source = writes.find(item => item.path.endsWith("/resolve"))?.body;
    expect(source).toEqual({ source_type: "database", connection_id: "owned-db", table: "orders", fields: ["region"], filters: [{ field: "region", op: "eq", value: "华东" }] });
    expect(JSON.stringify(writes)).not.toMatch(/password|cookie|sql/);
});
test("D2 数据库范围和原件全程正确标示，加入后仍完整提交原附件", async ({ page }) => {
    const { writes, scope, snapshot } = await fixture(page);
    await page.route("**/api/data-sources/connections", route => route.fulfill({ json: [{ connection_id: "owned-db", name: "已登记订单库", dialect: "sqlite" }] }));
    await page.route("**/api/data-sources/connections/owned-db/schema", route => route.fulfill({ json: { default_schema: "main", tables: [{ name: "orders", schema: "main", primary_key: ["id"], columns: [{ name: "id", type: "INTEGER" }, { name: "region", type: "TEXT" }] }] } }));
    await page.route("**/api/connector-sources/resolve", route => {
        Object.assign(scope, { protocol: "database", connection_id: "owned-db", selection: route.request().postDataJSON() });
        snapshot.artifacts[0].final_url = "connector:owned-db";
        return route.fulfill({ json: scope });
    });
    await page.goto("/e2e/fixtures/connector-source/index.html");
    await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
    await page.getByRole("combobox", { name: "资料来源", exact: true }).selectOption("database");
    await page.getByRole("combobox", { name: "已登记数据库连接", exact: true }).selectOption("owned-db");
    await page.getByRole("combobox", { name: "读取哪张表", exact: true }).selectOption("orders");
    await page.getByLabel("读取字段 region", { exact: true }).check();
    await page.getByRole("button", { name: "核对读取范围", exact: true }).click();
    await expect(page.getByText("数据库：owned-db · 表：orders", { exact: true })).toBeVisible();
    await expect(page.getByText("字段：region", { exact: true })).toBeVisible();
    await page.getByRole("button", { name: "读取并冻结资料", exact: true }).click();
    await page.getByRole("button", { name: "加入当前任务", exact: true }).click();
    const sources = page.getByRole("region", { name: "已选连接资料", exact: true });
    await expect(sources).toBeVisible();
    await sources.getByText("查看已读原件与范围", { exact: true }).click();
    await expect(sources.getByText("数据库：owned-db · 表：orders", { exact: true })).toBeVisible();
    await expect(sources).not.toContainText("精确页面");
    await page.getByLabel("数量要求", { exact: true }).fill("保留用户明确的数量目标");
    await page.getByLabel("完整性要求", { exact: true }).fill("保留用户明确的缺口边界");
    await page.getByRole("button", { name: "检查上下文草案", exact: true }).click();
    await page.getByRole("button", { name: "启动任务", exact: true }).click();
    await expect(page.getByText("任务已创建：created-connector-task", { exact: true })).toBeVisible();
    expect(writes.find(item => item.path.endsWith("/acquisitions")).body.source).toEqual({ source_type: "database", connection_id: "owned-db", table: "orders", fields: ["region"] });
    expect(writes.find(item => item.path === "/api/semantic-workspace/tasks").body).toMatchObject({ quantity_requirement: "保留用户明确的数量目标", completeness_requirement: "保留用户明确的缺口边界" });
    expect(writes.find(item => item.path === "/api/semantic-workspace/tasks").body).toMatchObject({ source_snapshot_ids: ["connector-snapshot"], upload_ids: ["original-file"], output_formats: ["json"] });
});
for (const [code, message] of [["authorization_expired", "来源授权已过期"], ["no_results", "本次范围内没有记录"], ["local_parse", "本地无法解析来源内容"], ["source_network", "来源网络或超时失败"]])
    test(`E1 ${code}明确分类且可重新选择范围`, async ({ page }) => {
        const { writes, scope } = await fixture(page);
        await page.route("**/api/connector-sources/acquisitions", route => route.fulfill({ status: 202, json: { attempt_id: "failed-attempt", idempotency_key: route.request().headers()["idempotency-key"], allowed_scope: scope, status: "failed", error_code: code, error_message: "连接来源未形成可用完整结果", snapshot: null } }));
        await page.goto("/e2e/fixtures/connector-source/index.html");
        await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
        await page.getByLabel("公开数据地址", { exact: true }).fill(scope.selection.url);
        await page.getByRole("button", { name: "核对读取范围", exact: true }).click();
        await page.getByRole("button", { name: "读取并冻结资料", exact: true }).click();
        await expect(page.getByRole("alert")).toContainText(message);
        await expect(page.getByRole("button", { name: "加入当前任务", exact: true })).toHaveCount(0);
        await page.getByRole("button", { name: "重新选择范围", exact: true }).click();
        await expect(page.getByLabel("公开数据地址", { exact: true })).toBeEnabled();
        expect(writes.filter(item => item.path.endsWith("/resolve"))).toHaveLength(1);
    });
test("I1 成功快照身份错配不能预览或加入当前任务", async ({ page }) => {
    const { scope, snapshot } = await fixture(page);
    await page.route("**/api/connector-sources/acquisitions", route => route.fulfill({ status: 202, json: { attempt_id: "connector-attempt", idempotency_key: route.request().headers()["idempotency-key"], allowed_scope: scope, status: "succeeded", snapshot_id: snapshot.snapshot_id, snapshot: { ...snapshot, attempt_id: "other-attempt" } } }));
    await page.goto("/e2e/fixtures/connector-source/index.html");
    await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
    await page.getByLabel("公开数据地址", { exact: true }).fill(scope.selection.url);
    await page.getByRole("button", { name: "核对读取范围", exact: true }).click();
    await page.getByRole("button", { name: "读取并冻结资料", exact: true }).click();
    await expect(page.getByRole("alert")).toContainText("身份不一致");
    await expect(page.getByRole("button", { name: "加入当前任务", exact: true })).toHaveCount(0);
    await expect(page.getByText('[{"name":"合成记录"}]', { exact: true })).toHaveCount(0);
});
test("I2 Owner切换后迟到原件不能回填新Owner或持久化正文", async ({ page }) => {
    const { scope, snapshot, writes } = await fixture(page);
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    let reading = false;
    await page.route("**/api/connector-sources/acquisitions", async (route) => { reading = true; await pending; return route.fulfill({ status: 202, json: { attempt_id: "connector-attempt", idempotency_key: route.request().headers()["idempotency-key"], allowed_scope: scope, status: "succeeded", snapshot_id: snapshot.snapshot_id, snapshot } }); });
    await page.goto("/e2e/fixtures/connector-source/index.html");
    await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
    await page.getByLabel("公开数据地址", { exact: true }).fill(scope.selection.url);
    await page.getByRole("button", { name: "核对读取范围", exact: true }).click();
    await page.getByRole("button", { name: "读取并冻结资料", exact: true }).click();
    await expect.poll(() => reading).toBe(true);
    await page.getByRole("button", { name: "返回原任务", exact: true }).click();
    await page.getByRole("button", { name: "切换合成Owner", exact: true }).click();
    release();
    await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
    await expect(page.getByLabel("公开数据地址", { exact: true })).toHaveValue("");
    await expect(page.getByRole("button", { name: "加入当前任务", exact: true })).toHaveCount(0);
    await expect(page.getByText('[{"name":"合成记录"}]', { exact: true })).toHaveCount(0);
    expect(await page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.includes("owner-b") && key.includes("connector_attempt")))).toEqual([]);
    expect(writes.filter(item => item.path.endsWith("acquisitions"))).toHaveLength(0);
});
for (const mode of ["http_api", "database"])
    test(`J2 纯${mode}无附件默认输出并提交原任务`, async ({ page }) => {
        const { scope, control, writes } = await fixture(page);
        control.task = { task_id: "created-connector-task", title: "纯连接任务", objective_text: "核对连接记录", upload_ids: [], output_formats: ["markdown"], provider: "local", model: "fixture", runtime_version: "pi", status: "queued", active_revision: 1, current_revision: 1, viewing_revision: 1, run_id: "original-run", summary: null, error: null, question: null, cancel_requested: false, created_at: "2026-09-09T00:00:00Z", updated_at: "2026-09-09T00:00:00Z", events: [], revisions: [], attempts: [], harness_events: [], plan: null, run: null, delivery: null, uploads: [], web_sources: [] };
        if (mode === "database") {
            Object.assign(scope, { protocol: "database", connection_id: "owned-db" });
            await page.route("**/api/data-sources/connections", route => route.fulfill({ json: [{ connection_id: "owned-db", name: "本人只读连接", dialect: "sqlite" }] }));
            await page.route("**/api/data-sources/connections/owned-db/schema", route => route.fulfill({ json: { default_schema: "main", tables: [{ name: "orders", schema: "main", primary_key: ["id"], columns: [{ name: "id", type: "integer" }] }] } }));
        }
        await page.goto("/data-prep");
        await page.getByRole("textbox", { name: "任务要求" }).fill("核对连接记录");
        await page.getByRole("button", { name: "从已有连接读取", exact: true }).click();
        if (mode === "database") {
            await page.getByRole("combobox", { name: "资料来源", exact: true }).selectOption(mode);
            await page.getByRole("combobox", { name: "已登记数据库连接", exact: true }).selectOption("owned-db");
            await page.getByRole("combobox", { name: "读取哪张表", exact: true }).selectOption("orders");
        }
        else
            await page.getByLabel("公开数据地址", { exact: true }).fill("https://records.example.invalid/items");
        await page.getByRole("button", { name: "核对读取范围", exact: true }).click();
        await page.getByRole("button", { name: "读取并冻结资料", exact: true }).click();
        await page.getByRole("button", { name: "加入当前任务", exact: true }).click();
        await page.getByRole("button", { name: "检查上下文草案", exact: true }).click();
        await page.getByRole("button", { name: /^(启动任务|开始执行)$/ }).click();
        await expect.poll(() => writes.filter(item => item.path === "/api/semantic-workspace/tasks").length).toBe(1);
        expect(writes.find(item => item.path === "/api/semantic-workspace/tasks").body).toMatchObject({ upload_ids: [], source_snapshot_ids: ["connector-snapshot"], output_formats: ["markdown"] });
        await expect(page).toHaveURL(/task=created-connector-task/);
    });
