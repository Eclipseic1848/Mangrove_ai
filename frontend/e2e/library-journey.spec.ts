import { expect, test } from "@playwright/test";

test("巡检加载失败明确提示且刷新可恢复", async ({ page }) => {
  let failed = true;
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: { user_id: "synthetic", username: "synthetic", role: "admin" } });
    if (path === "/api/library-dedup-log") return route.fulfill(failed
      ? { status: 503, json: { detail: "不得向用户暴露的内部错误" } }
      : { json: { log: [] } });
    return route.fulfill({ json: {} });
  });
  await page.goto("/templates");
  await expect(page.getByText('这里汇集可复用的任务处理方法。Mangrove 会自动积累，并在同类任务中参考使用，无需手动配置。')).toBeVisible();
  await page.getByRole("button", { name: "教训库", exact: true }).click();
  await expect(page.getByText(/连接故障或核验无结论不算业务教训/)).toBeVisible();
  await page.getByRole("button", { name: "巡检报告", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("巡检报告加载失败，请刷新重试");
  await expect(page.getByText(/暂无巡检记录/)).toHaveCount(0);
  await expect(page.getByText("不得向用户暴露的内部错误")).toHaveCount(0);
  failed = false;
  await page.getByRole("button", { name: "刷新", exact: true }).click();
  await expect(page.getByText(/暂无巡检记录/)).toBeVisible();
  await expect(page.getByRole("alert")).toHaveCount(0);
});

for (const role of ["user", "admin", "super_admin"]) {
  for (const width of [390, 1440]) {
    test(`${role} ${width} 三库入口、详情、分页与巡检权限`, async ({ page }, testInfo) => {
      await page.setViewportSize({ width, height: 900 });
      const errors: string[] = [];
      page.on("pageerror", error => errors.push(error.message));
      let scanRequests = 0;
      const entries = Array.from({ length: 13 }, (_, index) => ({
        slug: `entry-${index}`, title: `个人方法 ${index}`, body: `方法正文 ${index}`,
        data_type: index === 0 ? "article" : "workspace_table", keywords: ["订单"], status: "draft",
        uses: 3, verified_uses: index === 1 ? undefined : 2, quality_avg: index === 0 ? 80 : 0, occurrences: 2, helped_avoid: 0,
        scope: "owner", is_owner: true, can_delete: true, content_digest: "a".repeat(64),
      }));
      await page.route("**/api/**", async route => {
        const path = new URL(route.request().url()).pathname;
        if (path === "/api/auth/me") return route.fulfill({ json: { user_id: "synthetic-owner", username: "synthetic", display_name: "测试用户", role } });
        if (path === "/api/templates") return route.fulfill({ json: { templates: entries } });
        if (path === "/api/lessons") return route.fulfill({ json: { lessons: entries } });
        if (path === "/api/library-dedup-log") {
          scanRequests++;
          return route.fulfill({ json: { log: entries.map((_, id) => ({ id, ran_at: "2026-09-19 08:00:00", templates_scanned: 2, templates_merged: 0, lessons_scanned: 3, lessons_merged: 0, stale_drafts_deleted: 1 })) } });
        }
        return route.fulfill({ json: {} });
      });
      await page.goto("/templates");
      await expect(page.getByRole("heading", { name: "模板库", exact: true })).toBeVisible();
      await expect(page.getByRole("button", { name: "管理任务模板与个人记忆", exact: true })).toHaveClass(/bg-primary/);
      await page.screenshot({ path: testInfo.outputPath("library-entry-emphasis.png") });
      await expect(page.getByRole("region", { name: "模板库筛选与操作" }).getByRole("button", { name: "管理任务模板与个人记忆", exact: true })).toBeVisible();
      await expect(page.getByText("均分 80", { exact: true })).toBeVisible();
      await expect(page.getByText("已核验 — 次", { exact: true })).toBeVisible();
      await page.getByRole("button", { name: "下一页" }).click();
      await expect(page.getByText("个人方法 12", { exact: true })).toBeVisible();
      await expect(page.getByText("已核验 2 次", { exact: true })).toBeVisible();
      await expect(page.getByText(/均分/)).toHaveCount(0);
      await page.getByLabel("搜索标题、关键词或正文").fill("方法正文 0");
      await expect(page.getByText("个人方法 0", { exact: true })).toBeVisible();
      await expect(page.getByText("个人方法 12", { exact: true })).toHaveCount(0);
      await page.getByLabel("任务类型").selectOption("workspace_table");
      await expect(page.getByText("没有匹配的记录，请调整筛选条件。")).toBeVisible();
      await page.getByRole("button", { name: "清除筛选", exact: true }).click();
      await page.getByRole("button", { name: "下一页" }).click();
      await page.getByTitle("查看模板正文").click();
      await expect(page.getByRole("dialog")).toContainText("方法正文 12");
      await page.keyboard.press("Escape");
      await page.getByRole("button", { name: "教训库", exact: true }).click();
      await expect(page.getByRole("heading", { name: "教训库", exact: true })).toBeVisible();
      await expect(page.getByLabel("搜索标题、关键词或正文")).toHaveValue("");
      await page.getByLabel("状态", { exact: true }).selectOption("active");
      await expect(page.getByText("没有匹配的记录，请调整筛选条件。")).toBeVisible();
      await page.getByRole("button", { name: "清除筛选", exact: true }).click();
      await expect(page.getByRole("button", { name: "共享通用副本" })).toHaveCount(0);
      await page.getByRole("button", { name: "下一页" }).click();
      await page.getByTitle("查看教训正文").click();
      await expect(page.getByRole("dialog")).toContainText("方法正文 12");
      await page.keyboard.press("Escape");
      if (role === "user") {
        await expect(page.getByRole("button", { name: "巡检报告", exact: true })).toHaveCount(0);
        expect(scanRequests).toBe(0);
      } else {
        await page.getByRole("button", { name: "巡检报告", exact: true }).click();
        await expect(page.getByText("清理停滞草稿 1 条")).toHaveCount(12);
        await page.getByRole("button", { name: "下一页" }).click();
        await expect(page.getByText("清理停滞草稿 1 条")).toHaveCount(1);
        expect(scanRequests).toBeGreaterThan(0);
      }
      expect(errors).toEqual([]);
      await page.screenshot({ path: testInfo.outputPath("library-tabs.png"), animations: "disabled" });
      await page.evaluate(() => document.documentElement.classList.add("dark"));
      await page.screenshot({ path: testInfo.outputPath("library-tabs-dark.png"), animations: "disabled" });
      const heading = await page.getByRole("heading", { level: 1 }).boundingBox();
      expect(heading?.height).toBeLessThanOrEqual(32);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    });
  }
}
