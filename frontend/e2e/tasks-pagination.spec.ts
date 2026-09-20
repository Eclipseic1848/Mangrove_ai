import { expect, test } from "@playwright/test";

test("自动化列表、运行记录和历史统一分页", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  let tasks = Array.from({ length: 21 }, (_, i) => ({ task_id: `t${i}`, name: `合成任务${i + 1}`, user_input: "模拟需求", status: "active", trigger_type: "cron", cron_expr: "0 9 * * *", run_count: 23 }));
  const queries: URLSearchParams[] = [];
  await page.route("**/api/**", route => {
    const url = new URL(route.request().url());
    if (url.pathname === "/api/auth/me") return route.fulfill({ json: { user_id: "test", username: "test", role: "admin" } });
    if (url.pathname === "/api/tasks" ) return route.fulfill({ json: tasks });
    if (url.pathname === "/api/tasks/templates") return route.fulfill({ json: [] });
    if (url.pathname === "/api/tasks/runs/recent") {
      queries.push(url.searchParams);
      const total = url.searchParams.get("q") ? 1 : 23;
      const limit = Number(url.searchParams.get("limit")), offset = Number(url.searchParams.get("offset"));
      return route.fulfill({ json: { total, items: Array.from({ length: Math.max(0, Math.min(limit, total - offset)) }, (_, i) => ({ task_id: "t0", task_name: "合成任务1", run_id: offset + i, run_at: "2026-09-17T09:00:00", success: true, summary: `模拟记录${offset + i + 1}`, has_report: false, has_json: false })) } });
    }
    if (route.request().method() === "DELETE") { tasks = tasks.filter(task => url.pathname !== `/api/tasks/${task.task_id}`); return route.fulfill({ json: { ok: true } }); }
    return route.fulfill({ json: {} });
  });
  await page.goto("/tasks");
  await expect(page.locator("header")).not.toContainText("调度状态待确认");
  await expect(page.locator("header")).not.toContainText("北京时间（UTC+8）");
  const pager = page.getByRole("navigation", { name: "定时任务分页" });
  await expect(pager.getByLabel("每页条数")).toHaveValue("10");
  await expect(pager.getByLabel("每页条数").locator("option")).toHaveText(["10", "20", "50", "100"]);
  await expect(page).toHaveURL(/\/tasks$/);
  await page.screenshot({ path: testInfo.outputPath("tasks-pagination-desktop.png") });
  await expect(page.getByRole("button", { name: "编辑", exact: true })).toHaveCount(10);
  for (const size of [20, 50]) {
    await pager.getByLabel("每页条数").selectOption(String(size));
    await expect(page.getByRole("button", { name: "编辑", exact: true })).toHaveCount(Math.min(size, 21));
  }
  await pager.getByLabel("每页条数").selectOption("10");
  await pager.getByRole("button", { name: "3", exact: true }).click();
  await page.getByTitle("删除任务", { exact: true }).click();
  await expect(pager).toContainText("2 / 2 页");
  await page.getByPlaceholder("搜索任务名称或提示词").fill("合成任务1");
  await expect(pager).toContainText("1 / 2 页");
  await pager.getByLabel("每页条数").selectOption("100");
  await expect(pager).toContainText("共 11 条");
  await page.getByRole("button", { name: "历史", exact: true }).first().click();
  const history = page.getByRole("navigation", { name: "执行历史分页" });
  await expect(history.getByLabel("每页条数")).toHaveValue("10");
  await history.getByRole("button", { name: "下一页" }).click();
  await expect(page.getByText("模拟记录11", { exact: true })).toBeVisible();
  await history.getByLabel("每页条数").selectOption("100");
  await expect(history).toContainText("1 / 1 页");
  await expect.poll(() => queries.at(-1)?.get("task_id")).toBe("t0");
  await page.getByRole("button", { name: "关闭", exact: true }).click();
  await page.getByRole("button", { name: "运行记录", exact: true }).click();
  const runs = page.getByRole("navigation", { name: "运行记录分页" });
  await expect(runs.getByLabel("每页条数")).toHaveValue("10");
  await runs.getByRole("button", { name: "下一页" }).click();
  await expect(page.getByText("模拟记录11", { exact: true })).toBeVisible();
  await runs.getByLabel("每页条数").selectOption("100");
  await expect.poll(() => queries.at(-1)?.get("offset")).toBe("0");
  await expect(runs).toContainText("共 23 条");
  await page.getByPlaceholder("搜索执行摘要关键词").fill("筛选");
  await expect(runs).toContainText("共 1 条");
  await expect(runs.getByRole("button", { name: "下一页" })).toBeDisabled();
  await page.setViewportSize({ width: 390, height: 844 });
  await runs.scrollIntoViewIfNeeded();
  const bounds = await runs.boundingBox();
  expect(bounds!.x).toBeGreaterThanOrEqual(0);
  expect(bounds!.x + bounds!.width).toBeLessThanOrEqual(390);
  await page.screenshot({ path: testInfo.outputPath("tasks-pagination-mobile.png") });
  expect(errors).toEqual([]);
});
