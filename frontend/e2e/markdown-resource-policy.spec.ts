import { test, expect } from "@playwright/test";
test.beforeEach(async ({ page }) => {
  // 所有请求只去隔离Vite；外站查看由测试context直接响应，不真实外发。
  await page.route("**/*", route => new URL(route.request().url()).origin === new URL(String(test.info().project.use.baseURL)).origin ? route.continue() : route.abort());
});

test("C7 原safeResources仍全部链接，新增模式识别协议相对与HTTP外站", async ({ page }) => {
  await page.goto("/e2e/fixtures/markdown-resource-policy/index.html");
  const original = page.getByRole("region", { name: "原安全资源模式" });
  await expect(original.getByRole("link", { name: "查看图片：同源示例" })).toHaveAttribute("href", "/api/data-sources/uploads/image/content");
  await expect(original.getByRole("img")).toHaveCount(0);
  await expect(original.getByRole("link", { name: "原普通链接" })).toHaveAttribute("target", "_blank");
  const external = page.getByRole("region", { name: "仅外站资源模式" });
  await expect(external.getByRole("link", { name: "查看图片：协议相对外站图" })).toHaveAttribute("href", "//image.example.invalid/relative.png");
  await expect(external.getByRole("link", { name: "查看图片：HTTP外站图" })).toHaveAttribute("href", "http://image.example.invalid/http.png");
});

test("C6 真实Chat外站图改主动查看，同源图文字与普通链接保留", async ({ page, context }) => {
  let externalRequests = 0;
  await context.route("https://image.example.invalid/**", route => { externalRequests++; return route.fulfill({ contentType: "text/html; charset=utf-8", body: "<h1>合成主动查看</h1>" }); });
  await page.route("**/api/**", route => {
    const path = new URL(route.request().url()).pathname;
    if (path === "/api/conversations") return route.fulfill({ json: [{ conv_id: "synthetic-chat", title: "安全策略对话", updated_at: "2026-09-09T00:00:00Z" }] });
    if (path.endsWith("/messages")) return route.fulfill({ json: [{ id: 1, role: "assistant", content: "原有正文与 **加粗**\n\n![外站图](https://image.example.invalid/photo.png)\n\n![相对同源图](/api/data-sources/uploads/image/content)\n\n![绝对同源图](http://127.0.0.1:4187/api/data-sources/uploads/image/content)\n\n[普通链接](/local-link)".replace("http://127.0.0.1:4187", new URL(route.request().url()).origin) }] });
    if (path.includes("/uploads/")) return route.fulfill({ contentType: "image/png", body: Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+cRa8AAAAASUVORK5CYII=", "base64") });
    return route.fulfill({ json: path === "/api/models" ? { options: [], available: [], default: null } : {} });
  });
  await page.goto("/e2e/fixtures/markdown-resource-policy/index.html?mode=chat"); await page.getByText("安全策略对话", { exact: true }).click();
  const link = page.getByRole("link", { name: "查看图片：外站图", exact: true });
  await expect(link).toBeVisible(); await expect(link).toHaveAttribute("href", "https://image.example.invalid/photo.png");
  await expect(link).toHaveAttribute("target", "_blank"); await expect(link).toHaveAttribute("rel", "noopener noreferrer");
  for (const name of ["相对同源图", "绝对同源图"]) await expect.poll(() => page.getByRole("img", { name, exact: true }).evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(1);
  await expect(page.locator("strong", { hasText: "加粗" })).toBeVisible(); await expect(page.getByRole("link", { name: "普通链接", exact: true })).toHaveAttribute("href", "/local-link");
  expect(externalRequests).toBe(0);
  const popupPromise = page.waitForEvent("popup"); await link.click(); const popup = await popupPromise;
  await expect(popup.getByRole("heading", { name: "合成主动查看" })).toBeVisible(); expect(externalRequests).toBe(1); await popup.close();
});
