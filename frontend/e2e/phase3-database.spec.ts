import { expect, test } from "@playwright/test";

test("数据库连接管理与表列选择闭环", async ({ page }) => {
  let saved = false;
  await page.addInitScript(() => localStorage.setItem("mangrove_token", "e2e-token"));
  await page.route("**/api/auth/me", (route) => route.fulfill({
    json: { access_token: "e2e-token", user_id: "u1", username: "tester", display_name: "测试员", role: "admin" },
  }));
  await page.route("**/api/data-tasks", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/data-sources/connections", async (route) => {
    if (route.request().method() === "POST") {
      saved = true;
      await route.fulfill({ json: {
        connection_id: "c1", user_id: "u1", name: "订单库", dialect: "sqlite",
        host: "", port: 0, database_name: "", username: "", sqlite_relpath: "orders.db",
        created_at: "2026-07-22T00:00:00Z", updated_at: "2026-07-22T00:00:00Z",
      }});
    } else {
      await route.fulfill({ json: saved ? [{
        connection_id: "c1", user_id: "u1", name: "订单库", dialect: "sqlite",
        host: "", port: 0, database_name: "", username: "", sqlite_relpath: "orders.db",
        created_at: "2026-07-22T00:00:00Z", updated_at: "2026-07-22T00:00:00Z",
      }] : [] });
    }
  });
  await page.route("**/api/data-sources/connections/test", (route) => route.fulfill({
    json: { reachable: true, message: "连接成功", sample: { dialect: "sqlite", table_count: 1 } },
  }));
  await page.route("**/api/data-sources/connections/c1/schema", (route) => route.fulfill({
    json: { dialect: "sqlite", tables: [{ name: "orders", primary_key: ["id"], columns: [
      { name: "id", type: "INTEGER", nullable: false },
      { name: "created_at", type: "DATETIME", nullable: false },
    ] }] },
  }));

  await page.goto("/data-prep?legacy=1");
  await page.getByRole("tab", { name: "结构化数据准备" }).click();
  await page.getByRole("button", { name: "数据库" }).click();
  await page.getByRole("button", { name: "新建连接" }).click();
  await page.getByLabel("连接名称").fill("订单库");
  await page.getByLabel("SQLite 相对路径").fill("orders.db");
  await page.getByRole("button", { name: "测试连接" }).click();
  await page.getByRole("button", { name: "保存连接" }).click();

  await expect(page.getByLabel("数据库连接")).toHaveValue("c1");
  await page.getByLabel("表").selectOption("orders");
  await expect(page.getByRole("checkbox").first()).toBeVisible();
  await expect(page.getByLabel("水位线字段")).toBeVisible();
});
