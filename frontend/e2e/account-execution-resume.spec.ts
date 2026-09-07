import { expect, test, type Page } from "@playwright/test";

async function fixture(page: Page, strategy: "waiting" | "unstarted" | "new_revision" = "waiting", external = false) {
  let calls = 0;
  let release: (() => void) | undefined;
  const barrier = new Promise<void>((resolve) => { release = resolve; });
  let task = {
    task_id: "resume-task", title: "虚构暂停任务", objective_text: "虚构目标",
    upload_ids: [], output_formats: [], provider: "local", model: null,
    model_connection_id: external ? "synthetic-external" : null,
    external_api_confirmed: false, status: "paused", active_revision: 1,
    current_revision: 1, viewing_revision: 1, current_status: "paused",
    plan_id: null, logical_revision: null, binding_revision: null, run_id: null,
    summary: "账号停用后暂停", error: null, failure: null,
    question: strategy === "waiting" ? { kind: "harness", question_id: "frozen-q", prompt: "保留原问题", options: [{ value: "continue", label: "继续原确认" }], allow_free_text: false } : null,
    cancel_requested: false, deleted_at: null, purge_after: null,
    created_at: "2026-09-05T00:00:00Z", updated_at: "2026-09-05T00:00:00Z",
    events: [], uploads: [], revisions: [], account_resume: { generation: 0, strategy } as object | null,
  };
  await page.addInitScript(() => localStorage.setItem("mangrove_workspace_onboarding_complete", "1"));
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: { user_id: "owner", username: "fixture", display_name: "测试员", role: "user" } });
    if (url.pathname.endsWith("/account-resume")) {
      calls++;
      expect(route.request().postDataJSON()).toMatchObject({ expected_generation: 0, expected_active_revision: 1 });
      await barrier;
      task = { ...task, status: strategy === "waiting" ? "needs_input" : "queued", current_status: strategy === "waiting" ? "needs_input" : "queued", account_resume: null };
      return route.fulfill({ json: { strategy, revision: null } });
    }
    if (url.pathname === "/api/semantic-workspace/tasks/resume-task") return route.fulfill({ json: task });
    const otherTask = { ...task, task_id: "other-task", title: "另一个任务", status: "completed", current_status: "completed", question: null, account_resume: null };
    if (url.pathname === "/api/semantic-workspace/tasks/other-task") return route.fulfill({ json: otherTask });
    if (url.pathname === "/api/semantic-workspace/tasks") return route.fulfill({ json: [task, otherTask] });
    if (url.pathname === "/api/semantic-workspace/guidance") return route.fulfill({ json: { onboarding: [], examples: [] } });
    if (url.pathname === "/api/semantic-workspace/storage") return route.fulfill({ json: { task_count: 1, recycle_bin_count: 0, total_bytes: 0, upload_bytes: 0, delivery_bytes: 0 } });
    if (url.pathname === "/api/models") return route.fulfill({ json: { options: [], default: null, pi_runtime_enabled: false } });
    if (url.pathname === "/api/model-connections") return route.fulfill({ json: { items: [] } });
    if (url.pathname === "/api/model-connections/preferences/default") return route.fulfill({ json: { preference: null } });
    if (url.pathname.endsWith("/stream")) return route.fulfill({ status: 200, contentType: "text/event-stream", body: "" });
    return route.fulfill({ status: 404, json: { detail: "isolated" } });
  });
  return { calls: () => calls, release: () => release!() };
}

test("账号重新启用不自动继续，显式恢复保留原问题且双击只提交一次", async ({ page }) => {
  const api = await fixture(page);
  await page.goto("/data-prep?task=resume-task");
  const button = page.getByRole("button", { name: "恢复原问题" });
  await expect(button).toBeVisible();
  await expect(page.getByRole("button", { name: "继续原确认" })).toHaveCount(0);
  expect(api.calls()).toBe(0);
  await button.click();
  await expect(button).toBeDisabled();
  expect(api.calls()).toBe(1);
  api.release();
  await expect(page.getByRole("button", { name: "继续原确认" })).toBeVisible();
  expect(api.calls()).toBe(1);
});

test("提交未知后只提示刷新，不自动重发恢复", async ({ page }) => {
  const api = await fixture(page, "unstarted");
  let writes = 0;
  await page.route("**/api/semantic-workspace/tasks/resume-task/account-resume", (route) => { writes++; return route.abort("failed"); });
  await page.goto("/data-prep?task=resume-task");
  await page.getByRole("button", { name: "恢复原任务" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "恢复结果未确认" })).toBeVisible();
  await expect(page.getByRole("button", { name: "刷新任务状态" })).toBeVisible();
  await expect(page.getByRole("button", { name: "恢复原任务" })).toBeDisabled();
  expect(api.calls()).toBe(0);
  await page.getByRole("button", { name: "刷新任务状态" }).click();
  expect(writes).toBe(1);
});

test("硬停任务明确创建新版本，不能称作继续原执行", async ({ page }) => {
  await fixture(page, "new_revision");
  await page.goto("/data-prep?task=resume-task");
  await expect(page.getByText("原执行已停止；恢复将按原要求创建新版本，保留旧版本记录。")).toBeVisible();
  await expect(page.getByRole("button", { name: "创建新版本恢复" })).toBeVisible();
});


test("新版本外部模型必须本次重新确认", async ({ page }) => {
  const api = await fixture(page, "new_revision", true);
  await page.goto("/data-prep?task=resume-task");
  const resume = page.getByRole("button", { name: "创建新版本恢复" });
  await expect(resume).toBeDisabled();
  await page.getByRole("checkbox", { name: /确认本次新版本继续使用已选外部模型连接/ }).check();
  await expect(resume).toBeEnabled();
  const request = page.waitForRequest("**/account-resume");
  await resume.click();
  expect((await request).postDataJSON().external_api_confirmed).toBe(true);
  expect(api.calls()).toBe(1);
  api.release();
});

test("迟到恢复响应不能把新选择拉回旧任务", async ({ page }) => {
  const api = await fixture(page, "unstarted");
  await page.goto("/data-prep?task=resume-task");
  await page.getByRole("button", { name: "恢复原任务" }).click();
  await page.getByRole("button", { name: /另一个任务/ }).click();
  await expect(page).toHaveURL(/task=other-task/);
  const response = page.waitForResponse("**/account-resume");
  api.release();
  await response;
  await expect(page).toHaveURL(/task=other-task/);
  await expect(page.getByRole("heading", { name: "另一个任务" })).toBeVisible();
});
