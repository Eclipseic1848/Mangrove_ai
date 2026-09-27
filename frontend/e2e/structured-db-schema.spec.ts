import { expect, test } from "@playwright/test";

for (const lateError of [false, true]) {
  test(`切换数据库连接忽略旧 Schema ${lateError ? "错误" : "结果"}`, async ({ page }) => {
    let release!: () => void;
    const pending = new Promise<void>(resolve => { release = resolve; });
    const table = (name: string) => ({ name, primary_key: [], columns: [{ name: "id", type: "INTEGER" }] });
    await page.route("**/api/**", async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/auth/me") return route.fulfill({ json: { user_id: "synthetic-owner", username: "合成用户", role: "user" } });
      if (path === "/api/data-tasks") return route.fulfill({ json: [] });
      if (path === "/api/data-sources/connections") return route.fulfill({ json: [
        { connection_id: "a", name: "连接 A", dialect: "sqlite" },
        { connection_id: "b", name: "连接 B", dialect: "sqlite" },
      ] });
      if (path === "/api/data-sources/connections/a/schema") {
        await pending;
        return lateError
          ? route.fulfill({ status: 400, json: { detail: "旧连接的错误" } })
          : route.fulfill({ json: { tables: [table("only_in_A")] } });
      }
      if (path === "/api/data-sources/connections/b/schema") return route.fulfill({ json: { tables: [table("only_in_B")] } });
      if (path === "/api/data-tasks/preview") return route.fulfill({ status: 422, json: { detail: "已捕获合成请求" } });
      if (path === "/api/operations/visits") return route.fulfill({ json: { ok: true } });
      return route.fulfill({ status: 404, json: { detail: "未匹配的合成 API" } });
    });
    await page.goto("/data-prep?legacy=1");
    await page.getByRole("tab", { name: "结构化数据准备" }).click();
    await page.getByRole("button", { name: "数据库", exact: true }).click();
    const connection = page.getByLabel("数据库连接", { exact: true });
    const tableSelect = page.getByLabel("表", { exact: true });
    const requested = page.waitForRequest("**/connections/a/schema");
    await connection.selectOption("a");
    await requested;
    await connection.selectOption("b");
    await expect(tableSelect.locator("option[value=only_in_B]")).toHaveCount(1);
    const lateResponse = page.waitForResponse("**/connections/a/schema");
    release();
    await (await lateResponse).finished();
    // 等待响应的状态更新落入浏览器绘制周期，再判断旧结果是否覆盖了当前选择。
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(connection).toHaveValue("b");
    await expect(tableSelect.locator("option[value=only_in_B]")).toHaveCount(1);
    await expect(page.getByText("旧连接的错误", { exact: true })).toHaveCount(0);
    await tableSelect.selectOption("only_in_B");
    await page.getByLabel("水位线字段", { exact: true }).selectOption("id");
    await page.getByLabel("时间范围字段", { exact: true }).selectOption("id");
    await page.getByLabel("开始时间", { exact: true }).fill("2026-09-01T00:00");
    await tableSelect.selectOption("");
    await tableSelect.selectOption("only_in_B");
    await expect(page.getByLabel("水位线字段", { exact: true })).toHaveValue("");
    await expect(page.getByLabel("时间范围字段", { exact: true })).toHaveValue("");
    const preview = page.waitForRequest("**/api/data-tasks/preview");
    await page.getByRole("button", { name: "预览", exact: true }).click();
    expect((await preview).postDataJSON().source).toMatchObject({ connection_id: "b", table: "only_in_B" });
    await connection.selectOption("");
    await expect(tableSelect).toHaveCount(0);
    await expect(page.getByRole("button", { name: "预览", exact: true })).toHaveCount(0);
  });
}
