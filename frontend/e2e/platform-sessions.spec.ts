import { expect, test, type Page } from "@playwright/test";

const owner = { user_id: "session-owner", username: "session-owner", display_name: "会话测试用户", role: "user",
  access_expires_at: 2000000000, session_expires_at: 2000500000 };

async function mockSession(page: Page, initiallyLoggedIn: boolean) {
  let loggedIn = initiallyLoggedIn;
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return loggedIn
      ? route.fulfill({ json: owner })
      : route.fulfill({ status: 401, headers: { "X-Mangrove-Auth": "session-invalid" }, json: { detail: "登录已失效，请重新登录" } });
    if (path === "/api/auth/login") {
      loggedIn = true;
      return route.fulfill({ json: owner });
    }
    if (path === "/api/overview") return route.fulfill({ json: { collectors: [], scheduler: { enabled: false, active_count: 0 },
      connectors: {}, connectors_enabled: {} } });
    if (path === "/api/models") return route.fulfill({ json: { options: [], available: [], default: null, document_default: null } });
    return route.fulfill({ status: 404, json: { detail: "未匹配的虚构 API" } });
  });
}

test("仅有 HttpOnly Cookie 时能恢复设备会话，不依赖脚本凭证", async ({ page, context }) => {
  await context.addCookies([{ name: "mangrove_access", value: "synthetic-http-only-access", domain: "127.0.0.1",
    path: "/api", httpOnly: true, secure: false, sameSite: "Strict" }]);
  await mockSession(page, true);
  await page.goto("/settings?section=personal");
  await expect(page.getByText("会话测试用户", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("mangrove_token"))).toBeNull();
  expect(await page.evaluate(() => document.cookie)).not.toContain("synthetic-http-only-access");
});

test("过期后提示并在重新登录后返回原站内位置", async ({ page }) => {
  await mockSession(page, false);
  await page.goto("/settings?section=personal");
  await expect(page.getByText("登录已失效，请重新登录", { exact: true })).toBeVisible();
  await page.getByLabel("用户名", { exact: true }).fill("session-owner");
  await page.getByLabel("密码", { exact: true }).fill("synthetic-password");
  await page.getByLabel("密码", { exact: true }).press("Enter");
  await expect(page).toHaveURL(/\/settings\?section=personal$/);
  await expect(page.getByText("会话测试用户", { exact: true })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("mangrove_token"))).toBeNull();
});

test("登录和注册提供密码管理器字段语义及键盘提交", async ({ page }) => {
  await mockSession(page, false);
  await page.goto("/login");
  await expect(page.getByLabel("用户名", { exact: true })).toHaveAttribute("name", "username");
  await expect(page.getByLabel("用户名", { exact: true })).toHaveAttribute("autocomplete", "username");
  await expect(page.getByLabel("密码", { exact: true })).toHaveAttribute("autocomplete", "current-password");
  await page.getByRole("button", { name: "注册", exact: true }).click();
  await expect(page.getByLabel("密码", { exact: true })).toHaveAttribute("autocomplete", "new-password");
});

for (const command of ["logout", "logout-all", "password"]) {
  test(`${command} 服务端失败保留身份，确认成功才退出且不取消后台任务`, async ({ page }) => {
    await mockSession(page, true);
    let fail = true;
    const mutations: string[] = [];
    page.on("request", (request) => {
      if (request.method() === "POST") mutations.push(new URL(request.url()).pathname);
    });
    await page.route(`**/api/auth/${command}`, async (route) => {
      expect(route.request().headers()["x-mangrove-csrf"]).toBe("1");
      expect(route.request().headers()["x-mangrove-owner"]).toBe(owner.user_id);
      expect(route.request().headers()["authorization"]).toBeUndefined();
      if (command === "password") expect(route.request().postDataJSON()).toEqual({
        current_password: "old-synthetic-password", new_password: "new-synthetic-password",
      });
      await route.fulfill(fail ? { status: 503, json: { detail: "虚构服务暂不可用" } } : { json: { ok: true } });
    });
    await page.goto("/settings?section=personal");
    if (command === "password") {
      await expect(page.getByLabel("当前密码", { exact: true })).toHaveAttribute("autocomplete", "current-password");
      await expect(page.getByLabel("新密码", { exact: true })).toHaveAttribute("autocomplete", "new-password");
      await page.getByLabel("当前密码", { exact: true }).fill("old-synthetic-password");
      await page.getByLabel("新密码", { exact: true }).fill("new-synthetic-password");
    }
    const submit = async () => {
      if (command === "logout") await page.getByTitle("退出登录").click();
      else if (command === "logout-all") await page.getByRole("button", { name: "退出所有设备", exact: true }).click();
      else await page.getByLabel("新密码", { exact: true }).press("Enter");
    };
    await submit();
    await expect(page.getByText("虚构服务暂不可用", { exact: true })).toBeVisible();
    await expect(page.getByText("会话测试用户", { exact: true })).toBeVisible();
    await expect(page).toHaveURL(/\/settings\?section=personal$/);
    fail = false;
    await submit();
    await expect(page.getByLabel("用户名", { exact: true })).toBeVisible();
    await expect(page).toHaveURL(/\/login\?returnTo=/);
    expect(mutations).toEqual([`/api/auth/${command}`, `/api/auth/${command}`]);
  });
}

for (const returnTo of ["https://external.invalid/", "//external.invalid/", "/\\external.invalid/", "/login"]) {
  test(`重新登录拒绝不安全返回地址 ${returnTo}`, async ({ page }) => {
    await mockSession(page, false);
    await page.goto(`/login?returnTo=${encodeURIComponent(returnTo)}`);
    await page.getByLabel("用户名", { exact: true }).fill("session-owner");
    await page.getByLabel("密码", { exact: true }).fill("synthetic-password");
    await page.getByLabel("密码", { exact: true }).press("Enter");
    await expect(page).toHaveURL(new URL("/", page.url()).href);
  });
}
