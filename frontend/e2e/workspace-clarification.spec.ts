import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import type { WorkspaceQuestion, WorkspaceTask } from "../src/types/semanticWorkspace";

const errors = new WeakMap<Page, string[]>();
test.beforeEach(async ({ page }) => { const current: string[] = []; errors.set(page, current); page.on("pageerror", error => current.push(error.message)); });
test.afterEach(async ({ page }) => { expect(errors.get(page)).toEqual([]); });

async function mockClarification(page: Page, theme = "light") {
  const question: WorkspaceQuestion = {
    kind: "plan", question_id: "clarification-business-question",
    round_id: `clarification_${"1".repeat(32)}`, revision: 1, purpose: "business",
    continuation: "resume", origin_turn_id: null, outbound_purpose: null,
    prompt: "你要查看本月全部付款，还是只看尚未付款的记录？",
    reason: "付款范围会改变结果包含的记录",
    affected_scope: "筛选条件和结果行数",
    options: [
      { value: "all", label: "本月全部付款" },
      { value: "unpaid", label: "只看尚未付款" },
    ],
    allow_free_text: true,
  };
  const task: WorkspaceTask = {
    task_id: "clarification-task", title: "本月付款核对", objective_text: "帮我看看本月付款",
    upload_ids: [], uploads: [], output_formats: ["xlsx"], provider: "local", model: "fixture-model",
    runtime_version: "legacy", external_api_confirmed: false, status: "needs_input",
    active_revision: 1, current_revision: 1, viewing_revision: 1,
    plan_id: null, logical_revision: null, binding_revision: null, run_id: null,
    summary: "需要补充付款范围", error: null, failure: null, question, cancel_requested: false,
    deleted_at: null, purge_after: null,
    created_at: "2026-09-08T00:00:00Z", updated_at: "2026-09-08T00:00:00Z",
    revisions: [], clarification_history: [],
    events: [], attempts: [], harness_events: [], run: null, plan: null, messages: [], delivery: null,
    understanding: { revision: 1, status: "needs_clarification", summary: "核对本月付款；付款范围仍待你确认。", findings: [], question: null },
  };
  await page.addInitScript(selectedTheme => {
    localStorage.setItem("mangrove_token", "e2e-token");
    localStorage.setItem("mangrove_theme", selectedTheme);
  }, theme);
  // 全部接口使用合成响应，界面缺口不能由后端或真实模型调用代替。
  await page.route("**/api/**", route => route.fulfill({ status: 404, json: { detail: "合成接口未登记" } }));
  await page.route("**/api/auth/me", route => route.fulfill({ json: {
    user_id: "clarification-owner", username: "clarification", display_name: "澄清用户", role: "admin",
  } }));
  await page.route("**/api/settings/onboarding/model-connections", route => route.fulfill({ json: { completed: true } }));
  await page.route("**/api/model-connections", route => route.fulfill({ json: { items: [] } }));
  await page.route("**/api/model-connections/presets", route => route.fulfill({ json: { presets: [] } }));
  await page.route("**/api/models", route => route.fulfill({ json: {
    options: [{ provider: "local", model: "fixture-model", label: "合成模型" }],
    default: { provider: "local", model: "fixture-model" },
  } }));
  await page.route("**/api/semantic-workspace/guidance", route => route.fulfill({ json: { onboarding: [], examples: [] } }));
  await page.route("**/api/semantic-workspace/storage", route => route.fulfill({ json: {
    task_count: 1, recycle_bin_count: 0, upload_bytes: 0, delivery_bytes: 0, total_bytes: 0,
  } }));
  await page.route("**/api/semantic-workspace/tasks?*", route => route.fulfill({ json: [task] }));
  await page.route(/\/api\/semantic-workspace\/tasks\/clarification-task(?:\?.*)?$/, route => route.fulfill({ json: task }));
  const turns: Array<{ turn_id: string; revision: number; text: string }> = [];
  const requests: Array<{ payload: Record<string, unknown>; key: string | undefined }> = [];
  await page.route("**/api/semantic-workspace/tasks/clarification-task/turns", route => route.fulfill({ json: { turns, results: [], proposals: [] } }));
  await page.route("**/api/semantic-workspace/tasks/clarification-task/answer", route => {
    const payload = route.request().postDataJSON();
    requests.push({ payload, key: route.request().headers()["idempotency-key"] });
    const pending = task.question ?? task.understanding?.question;
    if (!pending || payload.question_round_id !== pending.round_id || payload.expected_revision !== 1) return route.fulfill({ status: 409, json: { detail: "问题轮次已失效" } });
    const turnId = `answer-${requests.length}`;
    turns.push({ turn_id: turnId, revision: 1, text: payload.answer });
    task.clarification_history!.push({ round_id: pending.round_id!, revision: 1, question: { ...pending }, answer: payload.answer, turn_id: turnId, asked_at: task.created_at, answered_at: task.updated_at });
    task.question = null;
    task.status = "running";
    task.understanding = { revision: 1, status: "ready", summary: "只看本月尚未付款，不包含已经取消的记录。", findings: [], question: null };
    return route.fulfill({ json: { ...task, answer_receipt: { round_id: pending.round_id, revision: 1, turn_id: turnId, status: "accepted" } } });
  });
  await page.route("**/api/semantic-workspace/tasks/clarification-task/stream*", route => route.fulfill({
    contentType: "text/event-stream", body: "",
  }));

  return { task, question, requests, turns };
}

test("业务澄清留在对话主列并复用唯一输入", async ({ page }) => {
    const { question } = await mockClarification(page);
    await page.goto("/data-prep?task=clarification-task");
    await expect(page.getByText(question.prompt, { exact: true })).toBeVisible();
    // 先证明已有问题真实到达，再检查业务澄清不占用第二个弹窗输入。
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(page.getByRole("button", { name: "只看尚未付款", exact: true })).toBeVisible();
    const input = page.locator("textarea:visible");
    await expect(input).toHaveCount(1);
    await input.fill("只看尚未付款，不包含已经取消的记录");
    await expect(input).toHaveValue("只看尚未付款，不包含已经取消的记录");
});

test("明确切换输入用途，回答身份和真实历史可刷新恢复", async ({ page }) => {
  const { question, requests } = await mockClarification(page);
  await page.goto("/data-prep?task=clarification-task");
  await page.getByRole("textbox", { name: "继续对话", exact: true }).fill("原来的普通进度追问");
  await page.getByRole("button", { name: "回答这项问题", exact: true }).click();
  const input = page.getByRole("textbox", { name: "回答这项问题", exact: true });
  await expect(input).toBeFocused();
  await input.fill("只看尚未付款，不包含已经取消的记录");
  await page.getByRole("button", { name: "继续对话", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "继续对话", exact: true })).toHaveValue("原来的普通进度追问");
  await page.getByRole("button", { name: "回答这项问题", exact: true }).click();
  await expect(input).toHaveValue("只看尚未付款，不包含已经取消的记录");
  await input.press("Enter");
  await expect(page.getByLabel("当前业务问题")).toHaveCount(0);
  expect(requests).toHaveLength(1);
  expect(requests[0].payload).toEqual({ answer: "只看尚未付款，不包含已经取消的记录", expected_revision: 1, question_round_id: question.round_id });
  expect(requests[0].key).toBeTruthy();
  await expect(page.getByRole("textbox", { name: "继续对话", exact: true })).toHaveValue("原来的普通进度追问");
  await expect(page.getByText("只看尚未付款，不包含已经取消的记录", { exact: true })).toHaveCount(1);
  await page.reload();
  await expect(page.getByText(question.prompt, { exact: true })).toBeVisible();
  await expect(page.getByText("只看尚未付款，不包含已经取消的记录", { exact: true })).toHaveCount(1);
  await expect(page.getByLabel("当前理解")).toContainText("不包含已经取消的记录");
});

test("相同question_id的新轮次不被旧回答响应清稿", async ({ page }) => {
  const { task, question } = await mockClarification(page);
  task.status = "running";
  task.question = null;
  task.understanding!.question = { ...question, continuation: "steering", origin_turn_id: "original-turn" };
  let release!: () => void;
  const responseGate = new Promise<void>(resolve => { release = resolve; });
  let started = false;
  await page.route("**/api/semantic-workspace/tasks/clarification-task/answer", async route => {
    started = true;
    await responseGate;
    await route.fulfill({ json: { ...task, answer_receipt: { round_id: question.round_id, revision: 1, turn_id: "old-answer", status: "accepted" } } });
  });
  await page.goto("/data-prep?task=clarification-task");
  await page.getByRole("button", { name: "回答这项问题", exact: true }).click();
  await page.getByRole("textbox", { name: "回答这项问题", exact: true }).fill("第一轮回答");
  await page.getByRole("button", { name: "提交回答", exact: true }).click();
  await expect.poll(() => started).toBe(true);
  await page.getByRole("textbox", { name: "回答这项问题", exact: true }).fill("发送中继续编辑的补充原稿");
  task.understanding!.question = { ...question, continuation: "steering", origin_turn_id: "original-turn", round_id: `clarification_${"2".repeat(32)}`, prompt: "付款时间按到账日期还是申请日期？" };
  try {
  // 运行中 steering 问题通过产品既有轮询到达，原 Run 不因提问暂停。
  await expect(page.getByText(task.understanding!.question.prompt, { exact: true })).toBeVisible();
  await expect(page.getByRole("textbox", { name: "回答这项问题", exact: true })).toHaveValue("发送中继续编辑的补充原稿");
  await expect(page.getByRole("button", { name: "提交回答", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "回答这项问题", exact: true }).click();
  await page.getByRole("textbox", { name: "回答这项问题", exact: true }).fill("第二轮新稿，按到账日期");
  const oldResponse = page.waitForResponse(response => response.url().endsWith("/answer"));
  release();
  await oldResponse;
  await expect(page.getByRole("textbox", { name: "回答这项问题", exact: true })).toHaveValue("第二轮新稿，按到账日期");
  await expect(page.getByRole("button", { name: "提交回答", exact: true })).toBeEnabled();
  } finally { release(); }
});

test("409保留原稿与幂等键，unknown不自动重发", async ({ page }) => {
  const { task, question } = await mockClarification(page);
  task.question = null;
  task.status = "running";
  task.understanding!.question = { ...question, continuation: "steering", origin_turn_id: "original-turn" };
  const keys: string[] = [];
  await page.route("**/api/semantic-workspace/tasks/clarification-task/answer", route => {
    keys.push(route.request().headers()["idempotency-key"]);
    if (keys.length === 1) return route.fulfill({ status: 409, json: { detail: "问题轮次已失效，请核对" } });
    // 与实际 HTTP 快照一致：已接收的轮次退出待答，未知结果仍保留原话。
    const pending = task.understanding!.question!;
    task.clarification_history = [{ round_id: pending.round_id!, revision: 1, question: pending, answer: route.request().postDataJSON().answer,
      turn_id: "unknown-answer", asked_at: task.created_at, answered_at: task.updated_at }];
    task.understanding = { ...task.understanding!, status: "unavailable", question: null };
    return route.fulfill({ json: { ...task, answer_receipt: { round_id: question.round_id, revision: 1, turn_id: "unknown-answer", status: "unknown" } } });
  });
  await page.goto("/data-prep?task=clarification-task");
  await page.getByRole("button", { name: "回答这项问题", exact: true }).click();
  const input = page.getByRole("textbox", { name: "回答这项问题", exact: true });
  await input.fill("原稿不能丢");
  await input.press("Enter");
  await expect(page.getByLabel("当前业务问题")).toContainText("问题轮次已失效");
  await expect(input).toHaveValue("原稿不能丢");
  await input.press("Enter");
  await expect(page.getByText("回答结果未知，请刷新任务核对；不会自动重发。", { exact: true })).toBeVisible();
  expect(keys).toHaveLength(2);
  expect(keys[0]).toBe(keys[1]);
  await expect(input).toHaveValue("原稿不能丢");
  await page.getByRole("button", { name: "刷新问题状态", exact: true }).click();
  await expect(input).toHaveValue("原稿不能丢");
  await input.press("Enter");
  await expect(page.getByRole("button", { name: "提交回答", exact: true })).toBeDisabled();
  expect(keys).toHaveLength(2);
  task.understanding!.question = { ...question, round_id: `clarification_${"9".repeat(32)}`, continuation: "steering", origin_turn_id: "new-turn" };
  await expect(page.getByLabel("当前业务问题")).toBeVisible();
  await expect(page.getByText("回答结果未知，请刷新任务核对；不会自动重发。", { exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "回答这项问题", exact: true }).click();
  await expect(input).toHaveValue("原稿不能丢");
  await expect(page.getByRole("button", { name: "提交回答", exact: true })).toBeEnabled();
});

test("外发许可保持专门确认和原用途，普通回答不代替授权", async ({ page }) => {
  const { task, question, requests } = await mockClarification(page);
  task.question = { ...question, kind: "external", purpose: "authorization", continuation: "unavailable", prompt: "允许将当前任务范围发送到所选模型吗？",
    external_service: "合成模型连接", outbound_data: ["已选择的付款资料"], outbound_purpose: "按已确认目标核对付款", risk: "资料将离开本机",
    allow_free_text: false, options: [{ value: "confirm", label: "确认外发" }, { value: "cancel", label: "取消本次任务" }] };
  await page.goto("/data-prep?task=clarification-task");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("按已确认目标核对付款");
  await expect(dialog).not.toContainText("authorization");
  await expect(dialog.getByRole("button", { name: "确认外发", exact: true })).toBeEnabled();
  await expect(dialog.getByRole("button", { name: "取消本次任务", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "稍后回答" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByLabel("当前业务问题")).toHaveCount(0);
  await page.getByRole("textbox", { name: "继续对话", exact: true }).fill("可以，先解释当前进度");
  expect(requests).toHaveLength(0);
  await page.getByRole("button", { name: /继续回答/ }).click();
  await page.getByRole("button", { name: "确认外发", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(requests[0].payload).toEqual({ answer: "confirm", expected_revision: 1, question_round_id: question.round_id });
});

test("Runtime问题优先于steering，普通暂停不造业务问题", async ({ page }) => {
  const { task, question } = await mockClarification(page);
  task.understanding!.question = { ...question, round_id: `clarification_${"4".repeat(32)}`, origin_turn_id: "original-turn", continuation: "steering", prompt: "统计金额使用含税还是未税？" };
  await page.goto("/data-prep?task=clarification-task");
  await expect(page.getByLabel("当前业务问题")).toContainText(question.prompt);
  await expect(page.getByLabel("当前业务问题")).not.toContainText("统计金额使用含税还是未税");
  task.question = { ...question, purpose: "control", continuation: "unavailable", prompt: "执行已暂停，等待控制操作。", options: [], allow_free_text: false };
  task.status = "paused";
  task.understanding = null;
  await page.reload();
  await expect(page.getByText(/执行已暂停，等待控制操作/)).toBeVisible();
  await expect(page.getByLabel("当前业务问题")).toHaveCount(0);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "继续对话", exact: true })).toBeVisible();
});

test("冻结Pi业务补充明确说明新版本，未确认不创建新执行", async ({ page }) => {
  const { task, question } = await mockClarification(page);
  task.question = { ...question, continuation: "confirm_revision" };
  const decisions: string[] = [];
  await page.route("**/api/semantic-workspace/tasks/clarification-task/revision-proposals/**", route => { decisions.push(route.request().url()); return route.fulfill({ status: 409 }); });
  await page.goto("/data-prep?task=clarification-task");
  await expect(page.getByLabel("当前业务问题")).toContainText("按补充要求重新开始");
  await expect(page.getByLabel("当前业务问题")).toContainText("新版本和新的执行");
  await expect(page.getByLabel("当前业务问题")).not.toContainText("恢复原");
  expect(decisions).toEqual([]);
});

test("旧版本决策回包不清除新版本的明确外发选择", async ({ page }) => {
  const { task } = await mockClarification(page);
  task.question = null;
  task.status = "running";
  task.model_connection_id = "synthetic-connection";
  task.understanding = null;
  let lastConversationRevision = 0;
  await page.route("**/api/semantic-workspace/tasks/clarification-task/turns", route => {
    const revision = task.current_revision!;
    lastConversationRevision = revision;
    return route.fulfill({ json: { turns: [], proposals: [{ proposal_id: `proposal-${revision}`, base_revision: revision, status: "pending" }],
      results: [{ result_id: `result-${revision}`, task_id: task.task_id, revision, turn_id: `turn-${revision}`, delta_id: `delta-${revision}`,
        action: "revision_proposal", proposal_id: `proposal-${revision}`, acknowledgement: `第${revision}版待确认修改`, answer: null, run_id: null }] } });
  });
  let release!: () => void;
  let started = false;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const decisionPath = "/revision-proposals/proposal-1/decision";
  await page.route(`**${decisionPath}`, async route => {
    started = true;
    await gate;
    await route.fulfill({ json: { decision: { decision_id: "old-decision", status: "applied" }, revision: { revision: 2 } } });
  });
  try {
    await page.goto("/data-prep?task=clarification-task");
    const consent = page.getByRole("checkbox", { name: /我确认新版本或独立任务/ });
    await consent.check();
    await page.getByRole("button", { name: "立即停止并切换", exact: true }).click();
    await expect.poll(() => started).toBe(true);
    // 真实详情轮询先带来新版本，旧决策响应仍在途。
    task.current_revision = 2; task.active_revision = 2; task.viewing_revision = 2;
    await expect.poll(() => lastConversationRevision).toBe(2);
    await expect(consent).not.toBeChecked();
    await consent.check();
    const response = page.waitForResponse(item => item.url().endsWith(decisionPath));
    release();
    await (await response).finished();
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(consent).toBeChecked();
    await expect(page.getByRole("button", { name: "立即停止并切换", exact: true })).toBeEnabled();
  } finally { release(); }
});

test("历史和缺轮次问题不可提交，options-only不提供自由回答", async ({ page }) => {
  const { task, question, requests } = await mockClarification(page);
  task.current_revision = 2;
  task.active_revision = 2;
  await page.goto("/data-prep?task=clarification-task");
  await expect(page.getByRole("button", { name: "回答这项问题", exact: true })).toBeDisabled();
  expect(requests).toEqual([]);
  task.current_revision = 1; task.active_revision = 1;
  task.question = { ...question, round_id: undefined };
  await page.reload();
  await expect(page.getByRole("button", { name: "回答这项问题", exact: true })).toBeDisabled();
  task.question = { ...question, allow_free_text: false };
  await page.reload();
  await expect(page.getByRole("button", { name: "回答这项问题", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: "只看尚未付款", exact: true }).click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0].payload.answer).toBe("unpaid");
});

test("结果引用和普通草稿不随业务回答转交或丢失", async ({ page }) => {
  const { task, question, requests } = await mockClarification(page);
  task.status = "completed";
  task.question = null;
  task.understanding!.question = { ...question, continuation: "steering", origin_turn_id: "original-result-turn" };
  const hash = "a".repeat(64);
  const itemRef = `item_${"b".repeat(64)}`;
  task.delivery = { delivery_id: "clarification-delivery", run_id: "completed-run", plan_id: "completed-plan", status: "published", requested_formats: ["xlsx"], created_at: task.created_at,
    outputs: [{ output_id: "clarification-output", format: "xlsx", filename: "付款结果.xlsx", media_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", sha256: hash, size_bytes: 128, qa: { openable: true, checks: ["sha256"], warnings: [] }, download_url: "/api/semantic-delivery/outputs/clarification-output" }] };
  await page.route("**/api/semantic-workspace/tasks/clarification-task/preview?*", route => route.fulfill({ json: {
    kind: "table", task_id: task.task_id, revision: 1, delivery_id: task.delivery!.delivery_id, output_id: "clarification-output",
    representation: { kind: "derived_result", sha256: hash, associated_output_id: "clarification-output", media_type: "application/vnd.apache.parquet", lineage_available: true },
    columns: ["付款"], rows: [{ 付款: "本次选中的正式结果" }], item_refs: [itemRef], offset: 0, limit: 100, total: 1,
  } }));
  await page.goto("/data-prep?task=clarification-task");
  await page.getByRole("textbox", { name: "继续对话", exact: true }).fill("围绕这条正式结果的普通原稿");
  await page.getByRole("button", { name: "围绕此结果追问", exact: true }).click();
  await expect(page.getByLabel("本次追问引用的结果", { exact: true })).toContainText("本次选中的正式结果");
  await page.getByRole("button", { name: "回答这项问题", exact: true }).click();
  await page.getByRole("textbox", { name: "回答这项问题", exact: true }).fill("本次只补充筛选范围");
  await page.getByRole("button", { name: "提交回答", exact: true }).click();
  await expect.poll(() => requests.length).toBe(1);
  expect(requests[0].payload).not.toHaveProperty("result_context");
  await expect(page.getByRole("textbox", { name: "继续对话", exact: true })).toHaveValue("围绕这条正式结果的普通原稿");
  await expect(page.getByLabel("本次追问引用的结果", { exact: true })).toContainText("本次选中的正式结果");
});

test("切任务后旧回答不清新任务草稿或忙碌状态", async ({ page }) => {
  const { task } = await mockClarification(page);
  const second = { ...task, task_id: "second-task", title: "第二个核对任务", question: null, status: "completed", understanding: null };
  await page.route("**/api/semantic-workspace/tasks?*", route => route.fulfill({ json: [task, second] }));
  await page.route(/\/api\/semantic-workspace\/tasks\/second-task(?:\?.*)?$/, route => route.fulfill({ json: second }));
  let releaseOld!: () => void;
  let releaseNew!: () => void;
  const oldGate = new Promise<void>(resolve => { releaseOld = resolve; });
  const newGate = new Promise<void>(resolve => { releaseNew = resolve; });
  let oldStarted = false;
  let newStarted = false;
  await page.route("**/api/semantic-workspace/tasks/clarification-task/answer", async route => {
    oldStarted = true; await oldGate;
    await route.fulfill({ json: { ...task, answer_receipt: { round_id: task.question!.round_id, revision: 1, turn_id: "old-answer", status: "accepted" } } });
  });
  await page.route("**/api/semantic-workspace/tasks/second-task/turns", async route => {
    if (route.request().method() === "GET") return route.fulfill({ json: { turns: [], results: [], proposals: [] } });
    newStarted = true; await newGate;
    await route.fulfill({ json: { result_id: "second-result", task_id: second.task_id, turn_id: "second-turn", delta_id: "second-delta", action: "answer_only", acknowledgement: "已回答", answer: "第二个任务的进度", proposal_id: null, run_id: null, revision: 1 } });
  });
  try {
    await page.goto("/data-prep?task=clarification-task");
    await page.getByRole("button", { name: "回答这项问题", exact: true }).click();
    await page.getByRole("textbox", { name: "回答这项问题", exact: true }).fill("旧任务回答");
    await page.getByRole("button", { name: "提交回答", exact: true }).click();
    await expect.poll(() => oldStarted).toBe(true);
    await page.getByRole("button", { name: /第二个核对任务/ }).click();
    const input = page.getByRole("textbox", { name: "继续对话", exact: true });
    await input.fill("第二个任务的原稿");
    await input.press("Enter");
    await expect.poll(() => newStarted).toBe(true);
    const oldResponse = page.waitForResponse(response => response.url().endsWith("/clarification-task/answer"));
    releaseOld(); await oldResponse;
    await expect(input).toHaveValue("第二个任务的原稿");
    await expect(page.getByRole("button", { name: "正在理解", exact: true })).toBeDisabled();
    releaseNew();
    await expect(page.getByRole("button", { name: "发送", exact: true })).toBeVisible();
    await expect(input).toHaveValue("");
  } finally { releaseOld(); releaseNew(); }
});

for (const [width, height] of [[390, 620], [1366, 900]]) {
for (const theme of ["light", "dark"]) {
  test(`${theme}${width}长问题键盘与来源发现可访问`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    const { task, question, requests } = await mockClarification(page, theme);
    question.options = Array.from({ length: 6 }, (_, index) => ({ value: `scope-${index}`, label: `第${index + 1}项：核对已授权付款资料中的到账日期与申请日期，按确认后的业务范围统计` }));
    task.understanding!.findings = [{ artifact_id: "allowed-source", source_sha256: "a".repeat(64), inspection_id: "inspection-current", inspection_sha256: "b".repeat(64), inspector_version: "fixture-v1", status: "ready", summary: "已读取表格结构：到账日期、申请日期、付款状态；这里只显示有界样例。", table_ref: "table-current" }];
    await page.goto("/data-prep?task=clarification-task");
    await expect(page.getByLabel("来源观察")).toContainText("有界样例");
    await page.getByRole("button", { name: "回答这项问题", exact: true }).click();
    const input = page.getByRole("textbox", { name: "回答这项问题", exact: true });
    await input.fill("按到账日期");
    await input.dispatchEvent("keydown", { key: "Enter", keyCode: 229, isComposing: true });
    expect(requests).toEqual([]);
    await input.press("Shift+Enter");
    await input.press("End");
    await input.type("不要取消记录");
    await expect(input).toHaveValue("按到账日期\n不要取消记录");
    await page.getByRole("button", { name: "提交回答", exact: true }).scrollIntoViewIfNeeded();
    await expect(page.getByRole("button", { name: "提交回答", exact: true })).toBeInViewport();
    const report = await new AxeBuilder({ page }).analyze();
    expect(report.violations).toEqual([]);
    await page.screenshot({ path: `../.artifacts/issue-130/${theme}-${width}.png`, fullPage: true });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}
}
