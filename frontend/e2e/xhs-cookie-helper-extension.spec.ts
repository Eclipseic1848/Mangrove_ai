import { expect, test, chromium, type BrowserContext, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { existsSync, mkdtempSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const extension = resolve(dirname(fileURLToPath(import.meta.url)), "../../tools/xhs-cookie-helper");
const cookieHeader = "web_session=synthetic-session-293; a1=synthetic-signature-293";
const externalRequests: { transport: string; target: string }[] = [];
let context: BrowserContext;
let origin: string;
let profile: string;
const proxy = createServer((request, response) => {
  externalRequests.push({ transport: "proxy-http", target: new URL(request.url ?? "/", "http://invalid").origin });
  response.writeHead(502).end();
});
proxy.on("clientError", (_error, socket) => socket.destroy());
proxy.on("connect", (request, socket) => {
  socket.on("error", () => {});
  externalRequests.push({ transport: "proxy-connect", target: request.url ?? "unknown" });
  socket.end("HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n");
});

async function openPopup() {
  const page = await context.newPage();
  await page.addInitScript(() => {
    const target = globalThis as unknown as {
      chrome: { cookies: { getAll: (...args: unknown[]) => Promise<unknown> } };
      cookieReadCalls: number;
    };
    if (!target.chrome?.cookies) return;
    const original = target.chrome.cookies.getAll.bind(target.chrome.cookies);
    target.cookieReadCalls = 0;
    target.chrome.cookies.getAll = (...args) => {
      target.cookieReadCalls += 1;
      return original(...args);
    };
  });
  await page.goto(`${origin}/popup.html`);
  await expect(page).toHaveTitle("Mangrove 小红书登录助手");
  return page;
}

async function advanceToReading(page: Page) {
  await page.getByRole("button", { name: "我已完成官方登录" }).click();
  await expect(page.locator("#read")).toBeDisabled();
  expect(await page.evaluate(() => (globalThis as unknown as { cookieReadCalls: number }).cookieReadCalls)).toBe(0);
}

async function seedCookies(conflict = false) {
  await context.clearCookies();
  await context.addCookies([
    { name: "web_session", value: "synthetic-session-293", domain: ".xiaohongshu.com", path: "/", secure: true, httpOnly: true },
    { name: "a1", value: "synthetic-signature-293", domain: ".xiaohongshu.com", path: "/", secure: false },
    { name: "unrelated", value: "synthetic-foreign-domain-293", domain: "example.invalid", path: "/", secure: true },
    ...(conflict ? [{ name: "a1", value: "synthetic-conflict-293", domain: "www.xiaohongshu.com", path: "/", secure: true }] : []),
  ]);
}

async function clearOnlySyntheticClipboard(page: Page) {
  return page.evaluate(async knownValues => {
    const current = await navigator.clipboard.readText();
    if (!knownValues.includes(current)) return false;
    await navigator.clipboard.writeText("");
    return true;
  }, [cookieHeader, cookieHeader.split("; ").reverse().join("; ")]);
}

test.describe("独立 Chromium 小红书扩展验收", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(90_000);

  test.beforeAll(async ({}, testInfo) => {
    const executablePath = process.env.MANGROVE_EXTENSION_CHROMIUM || chromium.executablePath();
    expect(existsSync(executablePath), "需要已有 Chromium；不自动安装或使用日常 Chrome 资料").toBe(true);
    profile = mkdtempSync(join(tmpdir(), "mangrove-xhs-extension-"));
    await new Promise<void>(done => proxy.listen(0, "127.0.0.1", done));
    const address = proxy.address();
    if (!address || typeof address === "string") throw new Error("ISOLATED_PROXY_UNAVAILABLE");
    context = await chromium.launchPersistentContext(profile, {
      executablePath, channel: "chromium", headless: true,
      proxy: { server: `http://127.0.0.1:${address.port}`, bypass: "<-loopback>" },
      viewport: { width: 400, height: 600 },
      serviceWorkers: "block",
      args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`,
        "--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-first-run",
        "--disable-quic", "--dns-prefetch-disable"],
    });
    await context.route(/^https?:\/\//, route => {
      externalRequests.push({ transport: "page-http", target: new URL(route.request().url()).origin });
      return route.abort("blockedbyclient");
    });
    await context.routeWebSocket(/.*/, socket => {
      externalRequests.push({ transport: "page-websocket", target: new URL(socket.url()).origin });
      socket.close({ code: 1008, reason: "isolated test" });
    });
    const manager = await context.newPage();
    await manager.goto("chrome://extensions/");
    const extensions = await manager.evaluate(() => new Promise<{ id: string; name: string }[]>(done => {
      const browser = globalThis as unknown as {
        chrome: { developerPrivate: { getExtensionsInfo: (options: object, callback: typeof done) => void } };
      };
      browser.chrome.developerPrivate.getExtensionsInfo({ includeDisabled: false, includeTerminated: false }, done);
    }));
    const loaded = extensions.filter(item => item.name === "Mangrove 小红书登录助手");
    expect(loaded).toHaveLength(1);
    origin = `chrome-extension://${loaded[0].id}`;
    await manager.close();
    const metadata = { browserPlugin: "Browser plugin not available", browser: context.browser()?.version(),
      playwright: testInfo.config.version, executablePath, userDataDir: profile,
      extensionOrigin: origin, normalChromeProfileUsed: false, realPlatformRequests: 0 };
    await testInfo.attach("isolated-browser.json", { body: JSON.stringify(metadata, null, 2), contentType: "application/json" });
  });

  test.afterAll(async ({}, testInfo) => {
    if (context) await context.close();
    if (proxy.listening) await new Promise<void>(done => proxy.close(() => done()));
    const pageAttempts = externalRequests.filter(item => item.transport.startsWith("page-"));
    const metadata = { blockedAttempts: externalRequests, extensionPageAttempts: pageAttempts,
      transmittedExternalRequests: 0, isolatedProfilePreserved: profile };
    writeFileSync(testInfo.outputPath("network-boundary.json"), JSON.stringify(metadata, null, 2), { encoding: "utf8" });
    await testInfo.attach("network-boundary.json", { body: JSON.stringify(metadata, null, 2), contentType: "application/json" });
    // Chromium 自身的更新尝试也被代理拒绝；必须与扩展页面请求分开留证。
    expect(pageAttempts, "扩展页面不应发起 HTTP 或 WebSocket 请求").toEqual([]);
    expect(externalRequests.filter(item => item.target.includes("xiaohongshu.com"))).toEqual([]);
  });

  test("真实 Cookie API、明确同意、真实复制和关闭后不保留", async ({}, testInfo) => {
    await seedCookies();
    expect((await context.cookies("https://www.xiaohongshu.com/")).map(cookie => cookie.name).sort()).toEqual(["a1", "web_session"]);
    const page = await openPopup();
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.name));
    await advanceToReading(page);
    await page.screenshot({ path: testInfo.outputPath("extension-reading-desktop.png"), fullPage: true });
    await expect(page.getByRole("checkbox")).not.toBeChecked();
    await page.getByRole("checkbox").check();
    await page.getByRole("button", { name: "读取最新登录态" }).click();
    await expect(page.locator("#summary")).toContainText("已读取 2 项登录字段");
    expect(await page.evaluate(() => (globalThis as unknown as { cookieReadCalls: number }).cookieReadCalls)).toBe(1);
    const markup = await page.content();
    expect(markup).not.toContain("synthetic-session-293");
    expect(markup).not.toContain("synthetic-signature-293");
    expect(markup).not.toContain("synthetic-foreign-domain-293");
    await expect(page.locator("#copy-status")).toBeEmpty();
    await page.getByRole("button", { name: "复制登录态", exact: true }).click();
    await expect(page.locator("#copy-status")).toContainText("已复制");
    // 先确认真实写入成功，再授予测试专用读取权限，不掩盖扩展自身的复制资格。
    await context.grantPermissions(["clipboard-read"]);
    expect(await clearOnlySyntheticClipboard(page), "剪贴板已改变或复制结果不符，保留当前内容且不输出原值").toBe(true);
    await page.evaluate(() => {
      const target = globalThis as unknown as { fixtureClipboardWrites: number };
      target.fixtureClipboardWrites = 0;
      navigator.clipboard.readText = async () => "synthetic-unrelated-clipboard-293";
      navigator.clipboard.writeText = async () => { target.fixtureClipboardWrites += 1; };
    });
    expect(await clearOnlySyntheticClipboard(page)).toBe(false);
    expect(await page.evaluate(() => (globalThis as unknown as { fixtureClipboardWrites: number }).fixtureClipboardWrites)).toBe(0);
    await page.evaluate(() => {
      navigator.clipboard.writeText = async () => { throw new Error("synthetic-private-clipboard-error-293"); };
    });
    await page.getByRole("button", { name: "复制登录态", exact: true }).click();
    await expect(page.locator("#copy-status")).toContainText("复制失败");
    expect(await page.content()).not.toContain("synthetic-private-clipboard-error-293");
    await page.screenshot({ path: testInfo.outputPath("extension-import-desktop.png"), fullPage: true });
    expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze()).violations).toEqual([]);
    expect(errors).toEqual([]);
    await page.close();
    const reopened = await openPopup();
    await expect(reopened.locator("#step-1")).toBeVisible();
    await expect(reopened.locator("#consent")).not.toBeChecked();
    await expect(reopened.locator("#summary")).toBeEmpty();
    await expect(reopened.locator("#copy")).toBeDisabled();
    expect(await reopened.evaluate(() => (globalThis as unknown as { cookieReadCalls: number }).cookieReadCalls)).toBe(0);
    await reopened.close();
  });

  test("真实同名冲突、失败脱敏、键盘与窄高布局", async ({}, testInfo) => {
    await seedCookies(true);
    const page = await openPopup();
    await page.setViewportSize({ width: 320, height: 560 });
    await advanceToReading(page);
    await page.keyboard.press("Tab");
    await expect(page.getByRole("checkbox")).toBeFocused();
    await page.keyboard.press("Space");
    await page.keyboard.press("Tab");
    await expect(page.locator("#read")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator("#read-error")).toContainText("同名但不同值");
    expect(await page.content()).not.toContain("synthetic-conflict-293");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath("extension-conflict-narrow.png"), fullPage: true });
    await page.evaluate(() => {
      const browser = globalThis as unknown as { chrome: { cookies: { getAll: () => Promise<unknown> } } };
      browser.chrome.cookies.getAll = async () => { throw new Error("synthetic-private-error-293"); };
    });
    await page.getByRole("button", { name: "读取最新登录态" }).click();
    await expect(page.locator("#read-error")).toContainText("无法读取登录态");
    expect(await page.content()).not.toContain("synthetic-private-error-293");
    expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze()).violations).toEqual([]);
    await page.close();
  });

  test("缺少真实必需字段保持未验证，深色系统下仍可读", async ({}, testInfo) => {
    await context.clearCookies();
    await context.addCookies([{ name: "web_session", value: "synthetic-session-293", domain: ".xiaohongshu.com", path: "/", secure: true, httpOnly: true }]);
    const page = await openPopup();
    await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
    await advanceToReading(page);
    await page.getByRole("checkbox").check();
    await page.getByRole("button", { name: "读取最新登录态" }).click();
    await expect(page.locator("#read-error")).toContainText("登录态不完整");
    await expect(page.locator("#copy")).toBeDisabled();
    await expect(page.locator("footer")).toContainText("基础身份：未验证");
    await expect(page.locator("footer")).toContainText("搜索访问：未验证");
    await page.screenshot({ path: testInfo.outputPath("extension-missing-dark-system.png"), fullPage: true });
    expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze()).violations).toEqual([]);
    await page.close();
  });
});
