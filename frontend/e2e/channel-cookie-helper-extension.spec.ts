import { expect, test, chromium, type BrowserContext, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { existsSync, mkdtempSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const extension = resolve(dirname(fileURLToPath(import.meta.url)), "../../tools/channel-cookie-helper");
const requests: { transport: string; target: string }[] = [];
let context: BrowserContext;
let origin: string;
let profile: string;
const proxy = createServer((request, response) => {
  requests.push({ transport: "proxy-http", target: new URL(request.url ?? "/", "http://invalid").origin });
  response.writeHead(502).end();
});
proxy.on("clientError", (_error, socket) => socket.destroy());
proxy.on("connect", (request, socket) => {
  socket.on("error", () => {});
  requests.push({ transport: "proxy-connect", target: request.url ?? "unknown" });
  socket.end("HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\n\r\n");
});

type Fixture = {
  permissionCalls: string[][];
  cookieCalls: string[];
  permissionMode: "real" | "deny" | "claim";
};
const channels = [
  { id: "xhs", name: "小红书", host: "xiaohongshu.com", url: "https://www.xiaohongshu.com/", fields: ["a1", "web_session"], origins: ["xiaohongshu.com", "www.xiaohongshu.com"] },
  { id: "wb", name: "微博", host: "weibo.cn", url: "https://m.weibo.cn/api/config", fields: ["sample"], origins: ["weibo.cn", "m.weibo.cn"] },
  { id: "dy", name: "抖音", host: "douyin.com", url: "https://www.douyin.com/aweme/v1/web/user/profile/self/", fields: ["sample"], origins: ["douyin.com", "www.douyin.com"] },
  { id: "bili", name: "B站", host: "bilibili.com", url: "https://api.bilibili.com/x/web-interface/nav", fields: ["sample"], origins: ["bilibili.com", "api.bilibili.com"] },
  { id: "zhihu", name: "知乎", host: "zhihu.com", url: "https://www.zhihu.com/api/v4/me", fields: ["d_c0"], origins: ["zhihu.com", "www.zhihu.com"] },
  { id: "ks", name: "快手", host: "kuaishou.com", url: "https://www.kuaishou.com/rest/v/profile/get", fields: ["sample"], origins: ["kuaishou.com", "www.kuaishou.com"] },
  { id: "tieba", name: "贴吧", host: "baidu.com", url: "https://tieba.baidu.com/dc/common/tbs", fields: ["sample"], origins: ["baidu.com", "tieba.baidu.com"] },
  { id: "jd", name: "京东", host: "jd.com", url: "https://me-api.jd.com/user_new/info/GetJDUserInfoUnion", fields: ["sample"], origins: ["jd.com", "me-api.jd.com"] },
  { id: "tb", name: "淘宝/天猫", host: "taobao.com", url: "https://h5api.m.taobao.com/h5/mtop.user.getusersimple/1.0/", fields: ["_m_h5_tk"], origins: ["taobao.com", "m.taobao.com", "h5api.m.taobao.com"] },
  { id: "pdd", name: "拼多多", host: "yangkeduo.com", url: "https://mobile.yangkeduo.com/user_setting.html", fields: ["sample"], origins: ["yangkeduo.com", "mobile.yangkeduo.com"] },
];

async function openPopup() {
  const page = await context.newPage();
  await page.addInitScript(() => {
    const target = globalThis as unknown as {
      helperFixture: Fixture;
      chrome: {
        permissions: { request: (details: { origins: string[] }) => Promise<boolean> };
        cookies: { getAll: (details: { url: string }) => Promise<unknown[]> };
      };
    };
    if (!target.chrome?.cookies) return;
    target.helperFixture = { permissionCalls: [], cookieCalls: [], permissionMode: "real" };
    const permission = target.chrome.permissions.request.bind(target.chrome.permissions);
    const cookies = target.chrome.cookies.getAll.bind(target.chrome.cookies);
    target.chrome.permissions.request = details => {
      target.helperFixture.permissionCalls.push([...details.origins]);
      if (target.helperFixture.permissionMode === "deny") return Promise.resolve(false);
      if (target.helperFixture.permissionMode === "claim") return Promise.resolve(true);
      return permission(details);
    };
    target.chrome.cookies.getAll = details => {
      target.helperFixture.cookieCalls.push(details.url);
      return cookies(details);
    };
  });
  await page.goto(`${origin}/popup.html`);
  await expect(page.getByRole("heading", { name: "Mangrove 登录助手", exact: true })).toBeVisible();
  return page;
}

async function readSelected(page: Page) {
  await page.getByRole("button", { name: "我已完成官方登录" }).click();
  await expect(page.locator("#read")).toBeDisabled();
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "授权并读取", exact: true }).click();
}

async function grantFixtureHosts(origins: string[]) {
  const manager = await context.newPage();
  await manager.goto("chrome://extensions/");
  const result = await manager.evaluate(async ({ extensionId, hosts }) => {
    const browser = globalThis as unknown as { chrome: {
      runtime: { lastError?: { message: string } };
      developerPrivate: { addHostPermission: (id: string, host: string, callback: () => void) => void };
    } };
    const outcomes = [];
    for (const host of hosts) outcomes.push(await new Promise<{ host: string; error: string | null }>(done => {
      browser.chrome.developerPrivate.addHostPermission(extensionId, host, () => done({ host, error: browser.chrome.runtime.lastError?.message || null }));
    }));
    return outcomes;
  }, { extensionId: new URL(origin).hostname, hosts: origins });
  await manager.close();
  expect(result).toEqual(origins.map(host => ({ host, error: null })));
}

test.describe("独立 Chromium 多渠道扩展验收", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(90_000);

  test.beforeAll(async ({}, testInfo) => {
    const executablePath = process.env.MANGROVE_EXTENSION_CHROMIUM || chromium.executablePath();
    expect(existsSync(executablePath), "只用已有Chromium和临时资料，不安装或读取日常Chrome").toBe(true);
    profile = mkdtempSync(join(tmpdir(), "mangrove-channel-extension-"));
    await new Promise<void>(done => proxy.listen(0, "127.0.0.1", done));
    const address = proxy.address();
    if (!address || typeof address === "string") throw new Error("ISOLATED_PROXY_UNAVAILABLE");
    context = await chromium.launchPersistentContext(profile, {
      executablePath, channel: "chromium", headless: true,
      proxy: { server: `http://127.0.0.1:${address.port}`, bypass: "<-loopback>" },
      viewport: { width: 400, height: 700 }, serviceWorkers: "block",
      args: [`--disable-extensions-except=${extension}`, `--load-extension=${extension}`,
        "--disable-background-networking", "--disable-component-update", "--disable-sync", "--no-first-run",
        "--disable-quic", "--dns-prefetch-disable"],
    });
    await context.route(/^https?:\/\//, route => {
      requests.push({ transport: "page-http", target: new URL(route.request().url()).origin });
      return route.abort("blockedbyclient");
    });
    await context.routeWebSocket(/.*/, socket => {
      requests.push({ transport: "page-websocket", target: new URL(socket.url()).origin });
      socket.close({ code: 1008, reason: "isolated test" });
    });
    const manager = await context.newPage();
    await manager.goto("chrome://extensions/");
    const extensions = await manager.evaluate(() => new Promise<{ id: string; name: string }[]>(done => {
      const browser = globalThis as unknown as { chrome: { developerPrivate: { getExtensionsInfo: (options: object, callback: typeof done) => void } } };
      browser.chrome.developerPrivate.getExtensionsInfo({ includeDisabled: false, includeTerminated: false }, done);
    }));
    const loaded = extensions.filter(item => item.name === "Mangrove 登录助手");
    expect(loaded).toHaveLength(1);
    origin = `chrome-extension://${loaded[0].id}`;
    await manager.close();
    await testInfo.attach("isolated-browser.json", { body: JSON.stringify({ browser: context.browser()?.version(), playwright: testInfo.config.version,
      executablePath, profile, extensionOrigin: origin, realCookieUsed: false, realSourceRequests: 0 }), contentType: "application/json" });
  });

  test.afterAll(async ({}, testInfo) => {
    if (context) await context.close();
    if (proxy.listening) await new Promise<void>(done => proxy.close(() => done()));
    const attempts = requests.filter(item => item.transport.startsWith("page-"));
    writeFileSync(testInfo.outputPath("network-boundary.json"), JSON.stringify({ blockedBrowserAttempts: requests, extensionPageAttempts: attempts,
      transmittedExternalRequests: 0, isolatedProfilePreserved: profile }, null, 2), { encoding: "utf8" });
    expect(attempts, "扩展页面不发起HTTP或WebSocket请求").toEqual([]);
    expect(requests.filter(item => /xiaohongshu|weibo|douyin|bilibili|zhihu|kuaishou|baidu|jd\.com|taobao|yangkeduo/.test(item.target))).toEqual([]);
  });

  test("临时资料站点授权后真实权限检查及Cookie读取可用", async () => {
    await context.clearCookies();
    await context.addCookies([
      { name: "a1", value: "synthetic-signature-295", domain: ".xiaohongshu.com", path: "/", secure: false },
      { name: "web_session", value: "synthetic-session-295", domain: ".xiaohongshu.com", path: "/", secure: true, httpOnly: true },
    ]);
    // 无头浏览器不能点击浏览器拥有的原生权限对话框；只在此临时资料配置固定合成站点授权。
    await grantFixtureHosts(["*://xiaohongshu.com/*", "*://www.xiaohongshu.com/*"]);
    const page = await openPopup();
    expect(await page.evaluate(() => (globalThis as unknown as { helperFixture: Fixture }).helperFixture.permissionCalls)).toEqual([]);
    await readSelected(page);
    await expect(page.locator("#summary")).toContainText("已读取 2 项登录字段", { timeout: 12_000 });
    const fixture = await page.evaluate(() => (globalThis as unknown as { helperFixture: Fixture }).helperFixture);
    expect(fixture.permissionCalls).toEqual([["*://xiaohongshu.com/*", "*://www.xiaohongshu.com/*"]]);
    expect(fixture.cookieCalls).toEqual(["https://www.xiaohongshu.com/"]);
    expect(await page.content()).not.toContain("synthetic-session-295");
    await page.close();
  });

  test("拒绝或未实际授予权限时不调用Cookie API", async () => {
    const page = await openPopup();
    await page.locator("#channel").selectOption("wb");
    await page.evaluate(() => { (globalThis as unknown as { helperFixture: Fixture }).helperFixture.permissionMode = "deny"; });
    await readSelected(page);
    await expect(page.locator("#read-error")).toContainText("未取得本渠道站点授权");
    expect(await page.evaluate(() => (globalThis as unknown as { helperFixture: Fixture }).helperFixture.cookieCalls)).toEqual([]);
    await page.evaluate(() => { (globalThis as unknown as { helperFixture: Fixture }).helperFixture.permissionMode = "claim"; });
    await page.getByRole("button", { name: "授权并读取", exact: true }).click();
    await expect(page.locator("#read-error")).toContainText("未取得本渠道站点授权");
    expect(await page.evaluate(() => (globalThis as unknown as { helperFixture: Fixture }).helperFixture.cookieCalls)).toEqual([]);
    await expect(page.locator("#copy")).toBeDisabled();
    await page.close();
  });

  for (const channel of channels) test(`${channel.name}真实Cookie API按业务路径读取且只显式复制`, async () => {
    const origins = channel.origins.map(host => `*://${host}/*`);
    await grantFixtureHosts(origins);
    await context.clearCookies();
    const values = channel.fields.map(name => ({ name, value: `synthetic-${channel.id}-${name}-295`, domain: `.${channel.host}`, path: "/", secure: true, httpOnly: true }));
    const scopedPath = new URL(channel.url).pathname;
    if (scopedPath !== "/") values.push({ name: "scoped", value: `synthetic-${channel.id}-scope-295`, domain: `.${channel.host}`, path: scopedPath, secure: true, httpOnly: false });
    if (["ks", "jd", "tb", "pdd"].includes(channel.id)) values.push({ name: "with_equals", value: "synthetic=preserved==", domain: `.${channel.host}`, path: "/", secure: false, httpOnly: false });
    await context.addCookies([...values, { name: "unrelated", value: "synthetic-foreign-295", domain: "example.invalid", path: "/", secure: true, httpOnly: false }]);
    const page = await openPopup();
    await page.locator("#channel").selectOption(channel.id);
    expect(await page.evaluate(() => (globalThis as unknown as { helperFixture: Fixture }).helperFixture.permissionCalls)).toEqual([]);
    await expect(page.locator("#channel-name")).toHaveText(channel.name);
    await readSelected(page);
    await expect(page.locator("#summary")).toContainText(`已读取 ${values.length} 项登录字段`);
    await expect(page.locator("#summary")).toContainText("身份尚未验证");
    const fixture = await page.evaluate(() => (globalThis as unknown as { helperFixture: Fixture }).helperFixture);
    expect(fixture.permissionCalls).toEqual([origins]);
    expect(fixture.cookieCalls).toEqual([channel.url]);
    expect(await page.content()).not.toContain("synthetic-");
    await page.getByRole("button", { name: "复制登录态", exact: true }).click();
    await expect(page.locator("#copy-status")).toContainText(`已复制${channel.name}登录态`);
    await context.grantPermissions(["clipboard-read"]);
    // 仅读回并清理完全等于本例合成值的剪贴板；未知内容不回传、不清空。
    expect(await page.evaluate(async expected => {
      const actual = await navigator.clipboard.readText();
      if (actual.split("; ").sort().join("; ") !== expected.sort().join("; ")) return false;
      await navigator.clipboard.writeText("");
      return true;
    }, values.map(item => `${item.name}=${item.value}`))).toBe(true);
    await page.locator("#channel").selectOption(channel.id === "pdd" ? "xhs" : "pdd");
    await expect(page.locator("#consent")).not.toBeChecked();
    await expect(page.locator("#summary")).toBeEmpty();
    await expect(page.locator("#copy-status")).toBeEmpty();
    await expect(page.locator("#copy")).toBeDisabled();
    await page.close();
  });

  test("权限未决期间切换或取消使旧结果失效，不自动读取新渠道", async () => {
    for (const action of ["change", "back"]) {
      const page = await openPopup();
      await page.evaluate(() => {
        const target = globalThis as unknown as { finishPermission: (granted: boolean) => void; chrome: { permissions: { request: () => Promise<boolean> } } };
        target.chrome.permissions.request = () => new Promise(done => { target.finishPermission = done; });
      });
      await readSelected(page);
      await expect(page.locator("#read")).toBeDisabled();
      if (action === "change") await page.locator("#channel").selectOption("jd");
      else await page.getByRole("button", { name: "上一步", exact: true }).click();
      await page.evaluate(() => (globalThis as unknown as { finishPermission: (granted: boolean) => void }).finishPermission(true));
      await expect(page.locator("#step-1")).toBeVisible();
      await expect(page.locator("#consent")).not.toBeChecked();
      await expect(page.locator("#copy")).toBeDisabled();
      expect(await page.evaluate(() => (globalThis as unknown as { helperFixture: Fixture }).helperFixture.cookieCalls)).toEqual([]);
      await page.close();
    }
  });

  test("Cookie旧响应与重复点击不能串到切换后的渠道", async () => {
    await context.clearCookies();
    await context.addCookies([{ name: "a1", value: "synthetic-pending-295", domain: ".xiaohongshu.com", path: "/" },
      { name: "web_session", value: "synthetic-pending-295", domain: ".xiaohongshu.com", path: "/" }]);
    const page = await openPopup();
    await page.evaluate(() => {
      const target = globalThis as unknown as { finishCookies: () => void; helperFixture: Fixture; chrome: { cookies: { getAll: (details: { url: string }) => Promise<unknown[]> } } };
      const original = target.chrome.cookies.getAll.bind(target.chrome.cookies);
      target.chrome.cookies.getAll = async details => {
        const rows = await original(details);
        return new Promise(done => { target.finishCookies = () => done(rows); });
      };
    });
    await readSelected(page);
    await expect.poll(() => page.evaluate(() => typeof (globalThis as unknown as { finishCookies: unknown }).finishCookies)).toBe("function");
    await page.locator("#read").evaluate(button => button.dispatchEvent(new MouseEvent("click", { bubbles: true })));
    expect(await page.evaluate(() => (globalThis as unknown as { helperFixture: Fixture }).helperFixture.permissionCalls.length)).toBe(1);
    await page.locator("#channel").selectOption("pdd");
    await page.evaluate(() => (globalThis as unknown as { finishCookies: () => void }).finishCookies());
    await expect(page.locator("#channel-name")).toHaveText("拼多多");
    await expect(page.locator("#summary")).toBeEmpty();
    await expect(page.locator("#copy")).toBeDisabled();
    expect(await page.content()).not.toContain("synthetic-pending-295");
    expect(await page.evaluate(() => (globalThis as unknown as { helperFixture: Fixture }).helperFixture.cookieCalls)).toEqual(["https://www.xiaohongshu.com/"]);
    await page.close();
  });

  test("复制失败脱敏、关闭不保留、键盘及明暗窄屏可读", async ({}, testInfo) => {
    await context.clearCookies();
    await context.addCookies([{ name: "sample", value: "synthetic-visible-never-295", domain: ".jd.com", path: "/" }]);
    const page = await openPopup();
    await page.setViewportSize({ width: 320, height: 560 });
    await page.locator("#channel").focus();
    await page.keyboard.press("End");
    await page.keyboard.press("ArrowUp");
    await page.keyboard.press("ArrowUp");
    await expect(page.locator("#channel")).toHaveValue("jd");
    await page.getByRole("button", { name: "我已完成官方登录" }).click();
    await page.keyboard.press("Tab");
    await expect(page.getByRole("checkbox")).toBeFocused();
    await page.keyboard.press("Space");
    await page.keyboard.press("Tab");
    await expect(page.locator("#read")).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page.locator("#summary")).toContainText("已读取 1 项登录字段");
    await page.evaluate(() => { navigator.clipboard.writeText = async () => { throw new Error("synthetic-private-clipboard-error-295"); }; });
    await page.getByRole("button", { name: "复制登录态", exact: true }).click();
    await expect(page.locator("#copy-status")).toContainText("复制失败");
    expect(await page.content()).not.toContain("synthetic-private-");
    expect(await page.content()).not.toContain("synthetic-visible-never-295");
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath("extension-import-narrow-light.png"), fullPage: true });
    expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze()).violations).toEqual([]);
    await page.emulateMedia({ colorScheme: "dark", reducedMotion: "reduce" });
    await page.getByRole("button", { name: "返回重读" }).click();
    await expect(page.locator("#consent")).not.toBeChecked();
    await expect(page.locator("#copy")).toBeDisabled();
    await page.screenshot({ path: testInfo.outputPath("extension-reading-narrow-dark.png"), fullPage: true });
    expect((await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa"]).analyze()).violations).toEqual([]);
    await page.close();
    const reopened = await openPopup();
    await expect(reopened.locator("#channel")).toHaveValue("xhs");
    await expect(reopened.locator("#summary")).toBeEmpty();
    await expect(reopened.locator("#consent")).not.toBeChecked();
    await expect(reopened.locator("#copy")).toBeDisabled();
    await reopened.close();
  });
});
