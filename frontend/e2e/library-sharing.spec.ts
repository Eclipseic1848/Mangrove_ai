import { expect, test, type Route } from "@playwright/test";

test("本人模板预览确认通用副本后共享，保留原件和独立评分", async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const own = { slug: "own-a", title: "本人模板", body: "本人报告结构", data_type: "article", keywords: ["分析"], status: "active", uses: 4, quality_avg: 80, scope: "owner", is_owner: true, can_delete: true, content_digest: "a".repeat(64) };
  let items = [own];
  let shares = 0;
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/auth/me") return route.fulfill({ json: { user_id: "owner-a", username: "a", display_name: "本人", role: "user" } });
    if (path === "/api/templates/own-a/share") {
      const data = route.request().postDataJSON();
      expect(data).toMatchObject({ confirmed: true, expected_source_digest: own.content_digest, title: "通用报告方法", body: "获准共享的通用步骤" });
      shares++;
      const shared = { ...own, ...data, slug: "shared-a", scope: "platform", status: "draft", uses: 0, quality_avg: 0 };
      items = [own, shared];
      return route.fulfill({ json: { entry: shared } });
    }
    if (path === "/api/templates") return route.fulfill({ json: { templates: items } });
    if (path === "/api/lessons") return route.fulfill({ json: { lessons: [] } });
    return route.fulfill({ status: 404, json: { detail: "隔离 API" } });
  });
  await page.goto("/templates");
  await expect(page.getByText("仅本人", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "共享通用副本" }).click();
  const submit = page.getByRole("button", { name: "确认共享" });
  await expect(submit).toBeDisabled();
  await page.getByLabel("副本标题").fill("通用报告方法");
  await page.getByLabel("副本正文").fill("获准共享的通用步骤");
  await page.getByLabel("我已移除个人信息、凭据和业务专属内容，同意共享此副本").check();
  await submit.click();
  await expect(page.getByText("平台共享", { exact: true })).toBeVisible();
  await expect(page.getByText("本人模板", { exact: true })).toBeVisible();
  await expect(page.getByText("通用报告方法", { exact: true })).toBeVisible();
  expect(shares).toBe(1);
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath("library-sharing-desktop.png"), animations: "disabled" });
});

for (const finish of ["close", "identity", "failure"]) {
  test(`共享${finish}后不串入旧内容或自动重试`, async ({ page }, testInfo) => {
    const identity = { user_id: "a", username: "a", display_name: "本人", role: "user" };
    let pending: Route | undefined;
    let posts = 0;
    await page.setViewportSize({ width: 390, height: 844 });
    await page.route("**/api/**", async route => {
      const path = new URL(route.request().url()).pathname;
      if (path === "/api/auth/me") return route.fulfill({ json: identity });
      if (path.endsWith("/share")) { pending = route; posts++; return; }
      if (path === "/api/templates") return route.fulfill({ json: { templates: identity.user_id === "a" ? [{ slug: "own-a", title: "本人私有模板", body: "本人私有正文", data_type: "article", keywords: [], status: "active", uses: 3, quality_avg: 80, scope: "owner", is_owner: true, can_delete: true, content_digest: "a".repeat(64) }] : [] } });
      if (path === "/api/lessons") return route.fulfill({ json: { lessons: [] } });
      return route.fulfill({ status: 404, json: { detail: "隔离API" } });
    });
    await page.goto("/templates");
    await page.getByRole("button", { name: "共享通用副本" }).click();
    const consent = page.getByLabel("我已移除个人信息、凭据和业务专属内容，同意共享此副本");
    await consent.check();
    await page.getByLabel("副本正文").fill("已核对通用方法");
    await expect(consent).not.toBeChecked();
    await consent.check();
    await page.getByRole("button", { name: "确认共享" }).evaluate((button: HTMLButtonElement) => { button.click(); button.click(); });
    await expect.poll(() => posts).toBe(1);
    if (finish === "close") await page.keyboard.press("Escape");
    if (finish === "identity") {
      identity.user_id = "b";
      const refreshed = page.waitForResponse("**/api/auth/me");
      await page.evaluate(() => { const channel = new BroadcastChannel("mangrove-platform-session"); channel.postMessage("identity-changed"); channel.close(); });
      await refreshed;
      await expect(page.getByRole("dialog")).toHaveCount(0);
    }
    const released = page.waitForResponse(response => response.url() === pending!.request().url());
    await pending!.fulfill({ status: finish === "failure" ? 503 : 200, json: finish === "failure" ? { detail: "敏感内部错误不应显示" } : { entry: { body: "旧响应正文" } } });
    await released;
    await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve()))));
    await expect(page.getByText("旧响应正文")).toHaveCount(0);
    await expect(page.getByText("敏感内部错误不应显示")).toHaveCount(0);
    expect(posts).toBe(1);
    if (finish === "failure") await expect(page.getByRole("alert")).toContainText("分享未确认");
    else await expect(page.getByRole("dialog")).toHaveCount(0);
    if (finish === "identity") await expect(page.getByText("本人私有模板", { exact: true })).toHaveCount(0);
    if (finish === "close") {
      await page.getByRole("button", { name: "共享通用副本" }).click();
      await expect(consent).not.toBeChecked();
    }
    if (finish === "failure") await page.screenshot({ path: testInfo.outputPath("library-sharing-mobile.png"), animations: "disabled" });
  });
}
