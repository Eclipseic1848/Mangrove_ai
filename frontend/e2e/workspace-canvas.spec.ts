import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import path from "node:path";

const pageErrors = new WeakMap<Page, string[]>();
test.beforeEach(async ({ page }) => { const errors: string[] = []; pageErrors.set(page, errors); page.on("pageerror", error => errors.push(error.message)); });
test.afterEach(async ({ page }) => { expect(pageErrors.get(page)).toEqual([]); });

const sourceHash = "a".repeat(64);
const resultHash = "b".repeat(64);
const outputHash = "c".repeat(64);
const itemRef = `item_${"d".repeat(64)}`;

function canvasTask(revision = 1) {
  return {
    task_id: "canvas-task", title: "画布往返任务", objective_text: "核对付款记录",
    upload_ids: ["canvas-source"], output_formats: ["xlsx"], provider: "local", model: "fixture-model",
    runtime_version: "legacy", external_api_confirmed: false, status: "completed",
    active_revision: 2, current_revision: 2, viewing_revision: revision,
    plan_id: null, logical_revision: null, binding_revision: null, run_id: `run-${revision}`,
    summary: "已完成", error: null, question: null, cancel_requested: false,
    deleted_at: null, purge_after: null, created_at: "2026-09-08T00:00:00Z", updated_at: "2026-09-08T00:01:00Z",
    revisions: [1, 2].map(value => ({ revision: value, status: "completed", summary: `版本${value}` })),
    events: [], attempts: [], harness_events: [], run: null, plan: null, messages: [],
    uploads: [{ upload_id: "canvas-source", original_name: "付款记录.docx", media_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", size_bytes: 512, sha256: sourceHash }],
    delivery: {
      delivery_id: `delivery-${revision}`, run_id: `run-${revision}`, plan_id: `plan-${revision}`,
      status: "published", requested_formats: ["xlsx"], created_at: "2026-09-08T00:01:00Z",
      outputs: [{ output_id: `output-${revision}`, format: "xlsx", filename: `付款V${revision}.xlsx`,
        media_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", sha256: outputHash, size_bytes: 128,
        qa: { openable: true, checks: ["sha256"], warnings: [] }, download_url: `/api/semantic-delivery/outputs/output-${revision}` }],
    },
  };
}

async function mockCanvas(page: Page) {
  const queries: URL[] = [];
  let messages: Array<Record<string, unknown>> = [];
  await page.addInitScript(() => { localStorage.setItem("mangrove_token", "e2e-token"); localStorage.setItem("mangrove_theme", "light"); });
  await page.route("**/api/**", route => route.fulfill({ status: 404, json: { detail: "合成接口未登记" } }));
  await page.route("**/api/auth/me", route => route.fulfill({ json: { user_id: "canvas-owner", username: "canvas", display_name: "画布用户", role: "admin" } }));
  await page.route("**/api/settings/onboarding/model-connections", route => route.fulfill({ json: { completed: true } }));
  await page.route("**/api/model-connections", route => route.fulfill({ json: { items: [] } }));
  await page.route("**/api/model-connections/presets", route => route.fulfill({ json: { presets: [] } }));
  await page.route("**/api/models", route => route.fulfill({ json: { options: [], default: { provider: "local", model: "fixture-model" } } }));
  await page.route("**/api/semantic-workspace/tasks?*", route => route.fulfill({ json: [canvasTask(2)] }));
  await page.route("**/api/semantic-workspace/guidance", route => route.fulfill({ json: { onboarding: [], examples: [] } }));
  await page.route("**/api/semantic-workspace/storage", route => route.fulfill({ json: { task_count: 1, recycle_bin_count: 0, total_bytes: 640 } }));
  await page.route(/\/api\/semantic-workspace\/tasks\/canvas-task(?:\?.*)?$/, route => route.fulfill({ json: { ...canvasTask(Number(new URL(route.request().url()).searchParams.get("revision") || 2)), messages } }));
  await page.route("**/api/semantic-workspace/tasks/canvas-task/turns", route => route.fulfill({ json: { turns: [], results: [], proposals: [] } }));
  await page.route("**/api/semantic-workspace/tasks/canvas-task/preview?*", route => {
    const query = new URL(route.request().url()); queries.push(query);
    const revision = Number(query.searchParams.get("revision") || 2);
    const offset = Number(query.searchParams.get("offset") || 0);
    return route.fulfill({ json: {
      kind: "table", task_id: "canvas-task", revision, run_id: `run-${revision}`, delivery_id: `delivery-${revision}`, output_id: `output-${revision}`,
      representation: { kind: "derived_result", sha256: resultHash, media_type: "application/vnd.apache.parquet", associated_output_id: `output-${revision}`, lineage_available: true },
      columns: ["结果"], rows: [{ 结果: `V${revision}第${offset + 1}条结果`, __lineage: [{ artifact_id: "canvas-source", element_id: "paragraph-40", page: 1, source_sha256: sourceHash }] }],
      item_refs: [itemRef], total: 201, offset, limit: 100,
    } });
  });
  await page.route("**/api/data-sources/uploads/canvas-source/document-preview", route => route.fulfill({ json: {
    upload_id: "canvas-source", original_name: "付款记录.docx", status: "ready", rejects: [],
    elements: Array.from({ length: 60 }, (_, index) => ({ element_id: `paragraph-${index + 1}`, artifact_id: "canvas-source", page: 1, element_type: "paragraph", text: `来源段落${index + 1}`, reading_order: index, extractor: "python_docx", extractor_version: "fixture", metadata: {} })),
  } }));
  await page.route("**/api/semantic-workspace/tasks/canvas-task/sources/canvas-source/preview?*", route => {
    const query = new URL(route.request().url());
    return route.fulfill({ json: {
      task_id: "canvas-task", revision: Number(query.searchParams.get("revision")), artifact_id: "canvas-source", upload_id: "canvas-source", sha256: sourceHash,
      original_name: "付款记录.docx", media_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", content_url: "/api/data-sources/uploads/canvas-source/content",
      representation: { kind: "source", parser_or_inspector_version: "fixture" }, kind: "document", offset: 0, limit: 100, total: 60, is_complete: true,
      location_status: query.searchParams.has("element_id") ? "located" : "not_requested",
      elements: Array.from({ length: 60 }, (_, index) => ({ element_id: `paragraph-${index + 1}`, artifact_id: "canvas-source", page: 1, element_type: "paragraph", text: `来源段落${index + 1}`, extractor: "python_docx", extractor_version: "fixture" })),
    } });
  });
  return { queries, setMessages: (value: Array<Record<string, unknown>>) => { messages = value; } };
}

test("同身份画布往返和关闭保留筛选分页，跨修订仍重置", async ({ page }) => {
  const { queries } = await mockCanvas(page);
  await page.goto("/data-prep?task=canvas-task");
  await expect(page.getByText("V2第1条结果", { exact: true })).toBeVisible();
  await page.getByPlaceholder("在全部结果中搜索").fill("付款");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await page.getByRole("button", { name: "结果", exact: true }).click();
  await page.locator("button:has(svg.lucide-chevron-right)").click();
  await expect(page.getByText("V2第101条结果", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "查看来源", exact: true }).click();
  await expect(page.getByText("来源段落40", { exact: true })).toBeVisible();
  const sourceContent = page.getByLabel("来源内容", { exact: true });
  await sourceContent.evaluate(element => { element.scrollTop = 300; element.dispatchEvent(new Event("scroll")); });
  await page.getByRole("button", { name: "查看结果", exact: true }).click();
  await expect(page.getByPlaceholder("在全部结果中搜索")).toHaveValue("付款");
  await expect(page.getByText("V2第101条结果", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "原文件预览", exact: true }).click();
  await expect.poll(() => sourceContent.evaluate(element => element.scrollTop)).toBe(300);
  await page.getByRole("button", { name: "查看结果", exact: true }).click();
  expect(queries.at(-1)?.searchParams.get("sort_by")).toBe("结果");
  await page.getByRole("button", { name: "关闭结果预览", exact: true }).click();
  await page.getByRole("button", { name: "查看结果", exact: true }).click();
  await expect(page.getByPlaceholder("在全部结果中搜索")).toHaveValue("付款");
  await expect(page.getByText("V2第101条结果", { exact: true })).toBeVisible();
  await page.getByLabel("结果版本").selectOption("1");
  await page.getByRole("button", { name: "查看结果", exact: true }).click();
  await expect(page.getByPlaceholder("在全部结果中搜索")).toHaveValue("");
  await expect(page.getByText("V1第1条结果", { exact: true })).toBeVisible();
  expect(queries.at(-1)?.searchParams.get("sort_by")).toBeNull();
});

test("来源窗口沿物理行和工作表定位，版本不符不声称命中", async ({ page }) => {
  await mockCanvas(page);
  const queries: URL[] = [];
  let mismatch = false;
  await page.route("**/api/semantic-workspace/tasks/canvas-task/preview?*", route => route.fulfill({ json: {
    kind: "table", columns: ["结果"], rows: [{ 结果: "重复值付款", __lineage: [
      { artifact_id: "canvas-source", source_sha256: sourceHash, table_ref: "sheet-second", row_number: 132 },
      { artifact_id: "canvas-source", source_sha256: sourceHash, element_id: "missing", page: 1, extractor_version: "old" },
    ] }], total: 1, offset: 0, limit: 100,
  } }));
  await page.route("**/api/semantic-workspace/tasks/canvas-task/sources/canvas-source/preview?*", route => {
    const query = new URL(route.request().url()); queries.push(query);
    return route.fulfill({ json: {
      task_id: "canvas-task", revision: 2, artifact_id: "canvas-source", upload_id: "canvas-source", sha256: sourceHash,
      original_name: "付款记录.xlsx", media_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", content_url: "/api/data-sources/uploads/canvas-source/content",
      representation: { kind: "source", parser_or_inspector_version: "fixture" }, kind: "table",
      tables: [{ table_ref: "sheet-first", table_index: 0, name: "首表", header_row: 3 }, { table_ref: "sheet-second", table_index: 1, name: "明细", header_row: 5 }],
      selected_table_ref: query.searchParams.get("table_ref") || "sheet-second", columns: ["付款", ...Array.from({ length: 8 }, (_, index) => `附列${index}`)],
      rows: Array.from({ length: 100 }, (_, index) => ({ row_number: index + 43, values: { 付款: "重复值" } })).filter(row => !query.searchParams.get("search") || row.row_number !== 132),
      offset: 100, limit: 100, total: 202, is_complete: true,
      location_status: mismatch ? "version_mismatch" : query.searchParams.has("row_number") ? "located" : "not_requested",
    } });
  });
  await page.goto("/data-prep?task=canvas-task");
  await page.getByLabel("选择结果来源").selectOption("0");
  await expect(page.getByLabel("来源工作表")).toHaveValue("sheet-second");
  await expect(page.getByText(/已定位来源/)).toBeVisible();
  await expect(page.locator('[data-source-row="132"]')).toHaveClass(/bg-amber/);
  await expect(page.locator('[data-source-row="132"]')).toBeInViewport();
  await expect(page.locator('[data-source-row="131"]')).not.toHaveClass(/bg-amber/);
  const scroll = page.getByLabel("来源内容", { exact: true });
  await scroll.evaluate(element => { element.scrollTop = 700; element.scrollLeft = 200; element.dispatchEvent(new Event("scroll")); });
  await expect.poll(() => scroll.evaluate(element => [element.scrollTop, element.scrollLeft])).toEqual([700, 200]);
  await page.getByRole("button", { name: "关闭原文件预览", exact: true }).click();
  await page.getByRole("button", { name: "原文件预览", exact: true }).click();
  await expect.poll(() => scroll.evaluate(element => [element.scrollTop, element.scrollLeft])).toEqual([700, 200]);
  expect(queries.some(query => query.searchParams.get("row_number") === "132" && query.searchParams.get("table_ref") === "sheet-second")).toBe(true);
  await page.getByLabel("搜索完整来源表").fill("重复");
  await page.getByRole("button", { name: "搜索来源", exact: true }).click();
  await expect.poll(() => queries.at(-1)?.searchParams.get("search")).toBe("重复");
  await expect(page.locator('[data-source-row="132"]')).toHaveCount(0);
  await expect(page.getByText(/已定位来源/)).toHaveCount(0);
  await expect(page.getByText(/当前仅浏览来源/)).toBeVisible();
  await page.getByLabel("来源工作表").selectOption("sheet-first");
  await expect.poll(() => queries.at(-1)?.searchParams.get("table_ref")).toBe("sheet-first");
  await expect(page.getByText(/已定位来源/)).toHaveCount(0);
  await page.getByRole("button", { name: "查看结果", exact: true }).click();
  mismatch = true;
  await page.getByLabel("选择结果来源").selectOption("1");
  await expect(page.getByText(/解析版本已变化，无法定位/)).toBeVisible();
  await expect(page.getByText(/已定位来源/)).toHaveCount(0);
});

test("输出身份错位与来源拒绝不展示其它内容，失败后能重试", async ({ page }) => {
  await mockCanvas(page);
  let failSource = true;
  await page.route("**/api/semantic-workspace/tasks/canvas-task/sources/canvas-source/preview?*", route => route.fulfill({ status: failSource ? 413 : 409, json: { detail: failSource ? "来源超出预览限额" : "来源冻结版本不匹配" } }));
  await page.goto("/data-prep?task=canvas-task");
  await page.getByRole("button", { name: "查看来源", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "来源超出预览限额" })).toBeVisible();
  await expect(page.getByText(/已定位来源/)).toHaveCount(0);
  failSource = false;
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await expect(page.getByRole("alert").filter({ hasText: "来源冻结版本不匹配" })).toBeVisible();
  await page.getByRole("button", { name: "查看结果", exact: true }).click();
  await page.route("**/api/semantic-workspace/tasks/canvas-task/preview?*", route => route.fulfill({ json: { kind: "document", task_id: "other-task", revision: 2, output_id: "output-other", items: [{ id: "private", label: "不能显示", content: "另一个输出的正文", evidence_refs: [] }], total: 1, offset: 0, limit: 100 } }));
  await page.reload();
  await expect(page.getByText(/结果身份已变化/)).toBeVisible();
  await expect(page.getByText("另一个输出的正文", { exact: true })).toHaveCount(0);
});

test("真实结果选择保留输入，仅发送身份并恢复持久回答来源", async ({ page }) => {
  const { setMessages } = await mockCanvas(page);
  const requests: Array<Record<string, unknown>> = [];
  const context = { revision: 2, output_id: "output-2", representation_sha256: resultHash, item_ref: itemRef };
  const publicContext = { ...context, label: "已核付款结果", source_refs: [
    { artifact_id: "canvas-source", source_sha256: sourceHash, element_id: "paragraph-40", page: 1, extractor_version: "fixture" },
    { artifact_id: "canvas-source", source_sha256: sourceHash, element_id: "paragraph-50", page: 1, extractor_version: "fixture" },
  ] };
  await page.route("**/api/semantic-workspace/tasks/canvas-task/turns", route => {
    if (route.request().method() === "GET") return route.fulfill({ json: { turns: [], results: [], proposals: [] } });
    requests.push(route.request().postDataJSON());
    setMessages([{ message_id: "selected-answer", version: 1, task_id: "canvas-task", revision: 2, run_id: "run-2", turn_id: "selected-turn", role: "assistant", kind: "answer", content: "这是基于所选结果的完整回答", status: "completed", created_at: "2026-09-08T00:02:00Z", result_context: publicContext }]);
    return route.fulfill({ json: { result_id: "selected-answer", task_id: "canvas-task", turn_id: "selected-turn", delta_id: "selected-delta", action: "answer_only", acknowledgement: "已回答", answer: "这是基于所选结果的完整回答", proposal_id: null, run_id: "run-2", revision: 2, result_context: publicContext } });
  });
  await page.goto("/data-prep?task=canvas-task");
  await page.getByLabel("继续对话").fill("解释这笔付款");
  await page.getByRole("button", { name: "围绕此结果追问", exact: true }).click();
  await expect(page.getByLabel("继续对话")).toHaveValue("解释这笔付款");
  await expect(page.getByLabel("本次追问引用的结果", { exact: true })).toContainText("V2第1条结果");
  expect(requests).toEqual([]);
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("这是基于所选结果的完整回答", { exact: true })).toBeVisible();
  expect(requests).toEqual([{ text: "解释这笔付款", result_context: context }]);
  await expect(page.getByLabel("本次追问引用的结果", { exact: true })).toHaveCount(0);
  await page.reload();
  await expect(page.getByText("这是基于所选结果的完整回答", { exact: true })).toHaveCount(1);
  await page.getByLabel("本次追问引用的结果和来源").getByRole("button", { name: "来源 2" }).click();
  await expect(page.getByText("来源段落50", { exact: true })).toBeVisible();
  await expect(page.getByText(/已定位来源/)).toBeVisible();
  await page.getByLabel("结果版本").selectOption("1");
  await page.getByRole("button", { name: "查看结果", exact: true }).click();
  await expect(page.getByRole("button", { name: "围绕此结果追问", exact: true })).toBeDisabled();
  await expect(page.getByText("历史结果不能用于新追问，请返回最新版本。", { exact: true })).toBeVisible();
});

test("引用提交冲突保留原稿，移除引用后自由问答不伪造来源", async ({ page }) => {
  await mockCanvas(page);
  const requests: Array<Record<string, unknown>> = [];
  await page.route("**/api/semantic-workspace/tasks/canvas-task/turns", route => {
    if (route.request().method() === "GET") return route.fulfill({ json: { turns: [], results: [], proposals: [] } });
    requests.push(route.request().postDataJSON());
    return route.fulfill({ status: 409, json: { detail: "结果选择已过期，请重新核对" } });
  });
  await page.goto("/data-prep?task=canvas-task");
  await page.getByRole("button", { name: "围绕此结果追问", exact: true }).click();
  await page.getByLabel("继续对话").fill("保持我的原稿");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("结果选择已过期，请重新核对", { exact: true })).toBeVisible();
  await expect(page.getByLabel("继续对话")).toHaveValue("保持我的原稿");
  await expect(page.getByLabel("本次追问引用的结果", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "移除引用", exact: true }).click();
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect.poll(() => requests.length).toBe(2);
  expect(requests[1]).toEqual({ text: "保持我的原稿" });
});

test("长文来源可读第二窗口，关闭返回保留实际滚动", async ({ page }) => {
  await mockCanvas(page);
  await page.route("**/api/semantic-workspace/tasks/canvas-task/sources/canvas-source/preview?*", route => {
    const offset = Number(new URL(route.request().url()).searchParams.get("offset") || 0);
    return route.fulfill({ json: {
      task_id: "canvas-task", revision: 2, artifact_id: "canvas-source", upload_id: "canvas-source", sha256: sourceHash,
      original_name: "付款记录.docx", media_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", content_url: "/api/data-sources/uploads/canvas-source/content",
      representation: { kind: "source", parser_or_inspector_version: "fixture" }, kind: "document", offset, limit: 100, total: 160, is_complete: true, location_status: "not_requested",
      elements: Array.from({ length: Math.min(100, 160 - offset) }, (_, index) => ({ element_id: `long-${offset + index + 1}`, artifact_id: "canvas-source", page: 1, element_type: "paragraph", text: `真实长文段落${offset + index + 1}`, extractor: "python_docx", extractor_version: "fixture" })),
    } });
  });
  await page.goto("/data-prep?task=canvas-task");
  await page.getByRole("button", { name: "原文件预览", exact: true }).click();
  await expect(page.getByText("真实长文段落1", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "下一页来源", exact: true }).click();
  await expect(page.getByText("真实长文段落101", { exact: true })).toBeVisible();
  const scroll = page.getByLabel("来源内容", { exact: true });
  await scroll.evaluate(element => { element.scrollTop = 350; element.dispatchEvent(new Event("scroll")); });
  await page.getByRole("button", { name: "关闭原文件预览", exact: true }).click();
  await page.getByRole("button", { name: "原文件预览", exact: true }).click();
  await expect(page.getByText("真实长文段落101", { exact: true })).toHaveCount(1);
  await expect.poll(() => scroll.evaluate(element => element.scrollTop)).toBe(350);
  await expect(page.getByRole("button", { name: "下一页来源", exact: true })).toBeDisabled();
});

test("多格式输出独立保留筛选且下载真实文件，空结果可清除", async ({ page }) => {
  const { queries } = await mockCanvas(page);
  await page.route(/\/api\/semantic-workspace\/tasks\/canvas-task(?:\?.*)?$/, route => {
    const task = canvasTask(2);
    task.delivery.outputs.push({ ...task.delivery.outputs[0], output_id: "second-output", filename: "付款复核.csv", format: "csv", media_type: "text/csv", download_url: "/api/semantic-delivery/outputs/second-output" });
    return route.fulfill({ json: task });
  });
  await page.route("**/api/semantic-workspace/tasks/canvas-task/preview?*", async route => {
    const query = new URL(route.request().url()); queries.push(query);
    const outputId = query.searchParams.get("output_id");
    const empty = query.searchParams.get("search") === "不存在";
    return route.fulfill({ json: { kind: "table", task_id: "canvas-task", revision: 2, delivery_id: "delivery-2", output_id: outputId,
      representation: { kind: "output", sha256: resultHash, associated_output_id: outputId, media_type: "text/csv", lineage_available: false },
      columns: ["内容"], rows: empty ? [] : [{ 内容: outputId === "second-output" ? "第二格式真实内容" : "第一格式真实内容" }], item_refs: empty ? [] : [null], total: empty ? 0 : 1, offset: 0, limit: 100 } });
  });
  await page.route("**/api/semantic-delivery/outputs/second-output", route => route.fulfill({ contentType: "text/csv", body: "内容\n第二格式真实内容" }));
  await page.goto("/data-prep?task=canvas-task");
  await page.getByLabel("在全部结果中搜索").fill("保留第一格式筛选");
  await page.getByRole("button", { name: "搜索", exact: true }).click();
  await page.getByLabel("预览输出").selectOption("second-output");
  await expect(page.getByText("第二格式真实内容", { exact: true })).toBeVisible();
  await expect(page.getByLabel("在全部结果中搜索")).toHaveValue("");
  await expect(page.getByRole("button", { name: "围绕此结果追问", exact: true })).toBeDisabled();
  const downloaded = page.waitForEvent("download");
  await page.getByRole("button", { name: "下载 付款复核.csv", exact: true }).click();
  expect((await downloaded).suggestedFilename()).toBe("付款复核.csv");
  await page.getByLabel("在全部结果中搜索").fill("不存在");
  await page.getByLabel("在全部结果中搜索").press("Enter");
  await expect(page.getByText("没有匹配的结果，请调整筛选。", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "清除结果搜索", exact: true }).click();
  await expect(page.getByText("第二格式真实内容", { exact: true })).toBeVisible();
  await page.getByLabel("预览输出").selectOption("output-2");
  await expect(page.getByLabel("在全部结果中搜索")).toHaveValue("保留第一格式筛选");
  await expect(page.getByText("第一格式真实内容", { exact: true })).toBeVisible();
});

test("PDF真实页与缩放关闭后恢复，越界不称已定位", async ({ page }) => {
  await mockCanvas(page);
  await page.route(/\/api\/semantic-workspace\/tasks\/canvas-task(?:\?.*)?$/, route => {
    const task = canvasTask(2);
    task.uploads[0] = { ...task.uploads[0], original_name: "真实五页.pdf", media_type: "application/pdf" };
    return route.fulfill({ json: task });
  });
  await page.route("**/api/semantic-workspace/tasks/canvas-task/sources/canvas-source/preview?*", route => route.fulfill({ json: {
    task_id: "canvas-task", revision: 2, artifact_id: "canvas-source", upload_id: "canvas-source", sha256: sourceHash, original_name: "真实五页.pdf", media_type: "application/pdf",
    content_url: "/api/data-sources/uploads/canvas-source/content", representation: { kind: "source", parser_or_inspector_version: null }, kind: "document", elements: [], total: 0, offset: 0, limit: 100, is_complete: true, page_count: 5, location_status: "not_requested",
  } }));
  await page.route("**/api/data-sources/uploads/canvas-source/content", route => route.fulfill({ contentType: "application/pdf", path: path.resolve(process.cwd(), "../tests/fixtures/document_golden/contract_01_digital.pdf") }));
  await page.goto("/data-prep?task=canvas-task");
  await page.getByRole("button", { name: "原文件预览", exact: true }).click();
  await expect(page.getByText("1/5", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "下一页", exact: true }).click();
  await page.getByRole("button", { name: "放大", exact: true }).click();
  const pdf = page.locator('.react-pdf__Page[data-page-number="2"]');
  await expect(pdf.locator("canvas")).toBeVisible();
  const width = await pdf.evaluate(element => element.getBoundingClientRect().width);
  const scroll = page.getByLabel("来源内容", { exact: true });
  await scroll.evaluate(element => { element.scrollTop = 180; element.dispatchEvent(new Event("scroll")); });
  await page.getByRole("button", { name: "关闭原文件预览", exact: true }).click();
  await page.getByRole("button", { name: "原文件预览", exact: true }).click();
  await expect(page.getByText("2/5", { exact: true })).toBeVisible();
  await expect(pdf.locator("canvas")).toBeVisible();
  expect(await pdf.evaluate(element => element.getBoundingClientRect().width)).toBe(width);
  await expect.poll(() => scroll.evaluate(element => element.scrollTop)).toBe(180);
  await page.getByRole("button", { name: "查看结果", exact: true }).click();
  await page.route("**/api/semantic-workspace/tasks/canvas-task/preview?*", route => route.fulfill({ json: { kind: "table", columns: ["结果"], rows: [{ 结果: "不存在第99页", __lineage: [{ artifact_id: "canvas-source", source_sha256: sourceHash, page: 99 }] }], total: 1, offset: 0, limit: 100 } }));
  await page.reload();
  await page.getByRole("button", { name: "查看来源", exact: true }).click();
  await expect(page.getByText(/未找到对应页/)).toBeVisible();
  await expect(page.getByText(/已定位来源/)).toHaveCount(0);
});

for (const theme of ["light", "dark"] as const) {
  for (const viewport of [{ width: 1366, height: 900 }, { width: 390, height: 620 }]) {
    test(`画布明暗窄屏键盘可达且来源文本不执行 ${theme} ${viewport.width}`, async ({ page }, testInfo) => {
      await page.setViewportSize(viewport);
      await mockCanvas(page);
      await page.addInitScript(value => localStorage.setItem("mangrove_theme", value), theme);
      const external: string[] = [];
      await page.route("https://canvas.invalid/**", route => { external.push(route.request().url()); return route.abort(); });
      await page.route("**/api/semantic-workspace/tasks/canvas-task/sources/canvas-source/preview?*", route => route.fulfill({ json: {
        task_id: "canvas-task", revision: 2, artifact_id: "canvas-source", upload_id: null, sha256: sourceHash, original_name: "已保存网页", media_type: "text/html", content_url: null,
        representation: { kind: "source", parser_or_inspector_version: null }, kind: "web", text_preview: '<img src="https://canvas.invalid/pixel" onerror="alert(1)"><script>alert(1)</script>已保存摘要', read_at: "2026-09-08T00:00:00Z", snapshot_id: "frozen-web", is_complete: false, truncated: true, location_status: "not_requested",
      } }));
      await page.goto("/data-prep?task=canvas-task");
      await expect(page.getByLabel("预览输出")).toBeVisible();
      await page.getByLabel("预览输出").focus();
      await expect(page.getByLabel("预览输出")).toBeFocused();
      await page.keyboard.press("ArrowDown");
      await page.keyboard.press("Enter");
      await page.screenshot({ path: testInfo.outputPath(`result-${theme}-${viewport.width}.png`), fullPage: true });
      expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
      await page.getByRole("button", { name: "查看来源", exact: true }).click();
      await expect(page.getByLabel("网页摘要预览")).toContainText("<script>alert(1)</script>");
      await expect(page.getByText("仅展示已保存摘要，内容可能截断，完整性未确认。", { exact: true })).toBeVisible();
      await expect(page.locator("iframe, object")).toHaveCount(0);
      await page.getByLabel("来源内容", { exact: true }).focus();
      await page.keyboard.press("PageDown");
      await expect(page.getByLabel("来源内容", { exact: true })).toBeFocused();
      expect(external).toEqual([]);
      await page.screenshot({ path: testInfo.outputPath(`source-${theme}-${viewport.width}.png`), fullPage: true });
      expect((await new AxeBuilder({ page }).analyze()).violations).toEqual([]);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    });
  }
}

test("同一来源解析版本后台变化不沿用旧定位与滚动", async ({ page }) => {
  await mockCanvas(page);
  let version = "first-parser";
  await page.route("**/api/semantic-workspace/tasks/canvas-task/sources/canvas-source/preview?*", route => route.fulfill({ json: {
    task_id: "canvas-task", revision: 2, artifact_id: "canvas-source", upload_id: "canvas-source", sha256: sourceHash, original_name: "付款记录.docx", media_type: "application/vnd.openxmlformats-officedocument.wordprocessingml.document", content_url: "/api/data-sources/uploads/canvas-source/content",
    representation: { kind: "source", parser_or_inspector_version: version }, kind: "document", offset: 0, limit: 100, total: 60, is_complete: true, location_status: "located",
    elements: Array.from({ length: 60 }, (_, index) => ({ element_id: `paragraph-${index + 1}`, artifact_id: "canvas-source", page: 1, element_type: "paragraph", text: `${version}来源段落${index + 1}`, extractor: "python_docx", extractor_version: version })),
  } }));
  await page.goto("/data-prep?task=canvas-task");
  await page.getByRole("button", { name: "查看来源", exact: true }).click();
  await expect(page.getByText(/已定位来源/)).toBeVisible();
  const scroll = page.getByLabel("来源内容", { exact: true });
  await scroll.evaluate(element => { element.scrollTop = 500; element.dispatchEvent(new Event("scroll")); });
  version = "second-parser";
  await page.getByRole("button", { name: "关闭原文件预览", exact: true }).click();
  await page.getByRole("button", { name: "原文件预览", exact: true }).click();
  await expect(page.getByText(/解析版本：second-parser/)).toBeVisible();
  await expect(page.getByText(/解析版本已变化，无法定位/)).toBeVisible();
  await expect(page.getByText(/已定位来源/)).toHaveCount(0);
  await expect.poll(() => scroll.evaluate(element => element.scrollTop)).toBe(0);
});
