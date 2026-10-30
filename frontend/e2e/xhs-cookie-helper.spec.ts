import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";

type Role = "user" | "admin" | "super_admin";
const DOWNLOAD = "/api/config/cookie-helper/download";
const CHANNELS = [
  { key: "mc_cookie_xhs", name: "小红书", loginName: "小红书", url: "https://www.xiaohongshu.com", logo: "xiaohongshu.png" },
  { key: "mc_cookie_wb", name: "微博", loginName: "微博", url: "https://m.weibo.cn", logo: "weibo.png" },
  { key: "mc_cookie_dy", name: "抖音", loginName: "抖音", url: "https://www.douyin.com", logo: "douyin.png" },
  { key: "mc_cookie_bili", name: "B站", loginName: "B站", url: "https://www.bilibili.com", logo: "bilibili.png" },
  { key: "mc_cookie_zhihu", name: "知乎", loginName: "知乎", url: "https://www.zhihu.com", logo: "zhihu.png" },
  { key: "mc_cookie_ks", name: "快手", loginName: "快手", url: "https://www.kuaishou.com", logo: "kuaishou.png" },
  { key: "mc_cookie_tieba", name: "贴吧", loginName: "贴吧", url: "https://tieba.baidu.com", logo: "tieba.png" },
  { key: "jd_cookie", name: "京东", loginName: "京东", url: "https://www.jd.com", logo: "jd.png" },
  { key: "tb_cookie", name: "淘宝/天猫", loginName: "淘宝", url: "https://www.taobao.com", logo: "taobao.png" },
  { key: "pdd_cookie", name: "拼多多", loginName: "拼多多", url: "https://mobile.yangkeduo.com", logo: "pdd.png" },
];

async function mockSettings(page: Page, role: Role, theme = "light", channels = CHANNELS.slice(0, 1)) {
  const counts = { downloads: 0, writes: 0, recovery: 0 };
  await page.addInitScript(({ role, theme }) => {
    localStorage.setItem("mangrove_theme", theme);
    localStorage.setItem(`onboarding_all_${JSON.stringify(["helper-owner", role])}`, "skipped");
  }, { role, theme });
  const cookies = channels.map(channel => ({ key: channel.key, label: `${channel.name} Cookie`, secret: true, group: "cookies", set: true, source: "override", value: "····demo" }));
  await page.route("**/api/**", route => {
    const request = route.request();
    const path = new URL(request.url()).pathname;
    if (path.startsWith("/api/config") && request.method() !== "GET") counts.writes++;
    if (/recovery|recover|resume/.test(path)) counts.recovery++;
    if (path === "/api/auth/me") return route.fulfill({ json: { user_id: "helper-owner", username: "synthetic", display_name: "合成用户", role } });
    if (path === "/api/operations/visits" || path === "/api/operations/heartbeat") return route.fulfill({ json: { ok: true } });
    if (path === "/api/config/self") return route.fulfill({ json: { items: cookies } });
    if (path === "/api/config") return route.fulfill({ json: { groups: [{ key: "cookies", label: "共享账号", items: cookies }] } });
    if (path === DOWNLOAD) {
      counts.downloads++;
      return route.fulfill({ contentType: "application/zip", body: Buffer.from([80, 75, 3, 4]) });
    }
    return route.fulfill({ status: 404, json: { detail: "隔离 API" } });
  });
  return counts;
}

async function openHelper(page: Page, scope: "personal" | "platform") {
  await page.goto(scope === "personal" ? "/settings?section=credentials" : "/settings?section=platform&service=cookies");
  await page.getByRole("button", { name: "小红书登录助手", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText(scope === "personal" ? "我的小红书登录助手" : "平台小红书登录助手");
  return dialog;
}

for (const role of ["user", "admin", "super_admin"] as const) test(`个人登录助手只打开本人配置且不自动提交：${role}`, async ({ page }) => {
  const counts = await mockSettings(page, role);
  const dialog = await openHelper(page, "personal");
  await expect(dialog).toContainText("个人凭据 · 仅自己可用");
  await expect(dialog.locator('input[type="password"], input[type="text"]')).toHaveCount(0);
  await expect(dialog.getByRole("link", { name: "打开小红书官方页面" })).toHaveAttribute("href", "https://www.xiaohongshu.com");
  await dialog.getByRole("button", { name: "下一步", exact: true }).click();
  await expect(dialog).toContainText("本页不会连接或读取你的 Chrome");
  await expect(dialog).not.toContainText("本机浏览器已连接");
  await dialog.getByRole("button", { name: "下一步", exact: true }).click();
  const configure = dialog.getByRole("button", { name: "进入我的配置", exact: true });
  await expect(configure).toBeDisabled();
  await dialog.getByRole("checkbox").check();
  await configure.click();
  await expect(page.getByRole("dialog")).toContainText("修改 · 小红书 Cookie");
  await expect(page.getByPlaceholder("粘贴从浏览器导出的 Cookie")).toHaveValue("");
  await expect(page.getByLabel("小红书 Cookie", { exact: true })).toHaveAttribute("type", "password");
  expect(counts).toEqual({ downloads: 0, writes: 0, recovery: 0 });
});

for (const role of ["user", "admin"] as const) test(`平台助手新入口不向${role}开放且不改变原设置权限`, async ({ page }) => {
  const counts = await mockSettings(page, role);
  await page.goto("/settings?section=platform&service=cookies");
  if (role === "admin") {
    await expect(page.getByRole("button", { name: "配置 小红书", exact: true })).toBeVisible();
  } else {
    await expect(page.getByRole("heading", { name: "设置", exact: true })).toBeVisible();
    await expect(page.getByRole("button", { name: "平台配置", exact: true })).toHaveCount(0);
  }
  await expect(page.getByRole("button", { name: "小红书登录助手", exact: true })).toHaveCount(0);
  expect(counts).toEqual({ downloads: 0, writes: 0, recovery: 0 });
});

test("超级管理员的平台助手只进入共享配置，不保存或验证", async ({ page }) => {
  const counts = await mockSettings(page, "super_admin");
  const dialog = await openHelper(page, "platform");
  await expect(dialog).toContainText("平台共享 · 超级管理员维护");
  await dialog.getByRole("button", { name: "下一步", exact: true }).click();
  await dialog.getByRole("button", { name: "下一步", exact: true }).click();
  await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: "进入平台配置", exact: true }).click();
  await expect(page.getByRole("dialog").getByLabel("小红书 Cookie", { exact: true })).toHaveValue("");
  expect(counts).toEqual({ downloads: 0, writes: 0, recovery: 0 });
});

for (const scope of ["personal", "platform"] as const) test(`${scope}编辑草稿在取消助手和返回配置后保持`, async ({ page }) => {
  const counts = await mockSettings(page, "super_admin");
  await page.goto(scope === "personal" ? "/settings?section=credentials" : "/settings?section=platform&service=cookies");
  await page.getByRole("button", { name: scope === "personal" ? "修改" : "配置 小红书", exact: true }).click();
  const input = scope === "personal" ? page.getByPlaceholder("粘贴从浏览器导出的 Cookie") : page.getByLabel("小红书 Cookie", { exact: true });
  await input.fill("synthetic-xhs-unsaved-draft");
  await expect(input).toHaveAttribute("type", "password");
  if (scope === "personal") {
    await page.getByRole("button", { name: "显示小红书 Cookie", exact: true }).click();
    await expect(input).toHaveAttribute("type", "text");
    await page.getByRole("button", { name: "隐藏小红书 Cookie", exact: true }).click();
  }
  await page.getByRole("button", { name: "打开小红书登录助手", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "取消", exact: true }).click();
  await expect(input).toHaveValue("synthetic-xhs-unsaved-draft");
  await expect(input).toHaveAttribute("type", "password");
  await expect(page.locator("body")).not.toContainText("synthetic-xhs-unsaved-draft");
  await page.getByRole("button", { name: "打开小红书登录助手", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "下一步", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "下一步", exact: true }).click();
  await page.getByRole("dialog").getByRole("checkbox").check();
  await page.getByRole("dialog").getByRole("button", { name: scope === "personal" ? "进入我的配置" : "进入平台配置", exact: true }).click();
  await expect(input).toHaveValue("synthetic-xhs-unsaved-draft");
  expect(counts).toEqual({ downloads: 0, writes: 0, recovery: 0 });
});

test("安装包仅显式点击下载，使用鉴权API且不携带Cookie正文", async ({ page }) => {
  const counts = await mockSettings(page, "user");
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**${DOWNLOAD}`, async route => {
    counts.downloads++;
    await pending;
    await route.fulfill({ contentType: "application/zip", body: Buffer.from([80, 75, 3, 4]) });
  });
  const dialog = await openHelper(page, "personal");
  expect(counts.downloads).toBe(0);
  const request = page.waitForRequest(request => new URL(request.url()).pathname === DOWNLOAD);
  const download = page.waitForEvent("download");
  await dialog.getByRole("button", { name: "下载本机助手", exact: true }).evaluate(element => {
    (element as HTMLButtonElement).click(); (element as HTMLButtonElement).click();
  });
  await expect.poll(() => counts.downloads).toBe(1);
  await expect(dialog.getByRole("button", { name: "下载中…", exact: true })).toBeDisabled();
  release();
  expect((await download).suggestedFilename()).toBe("mangrove-channel-cookie-helper.zip");
  const sent = await request;
  expect(sent.method()).toBe("GET");
  expect(sent.headers()["x-mangrove-owner"]).toBe("helper-owner");
  expect(sent.headers().authorization).toBeUndefined();
  expect(sent.postData()).toBeNull();
  await expect(dialog.getByRole("status")).toContainText("已发起安装包下载");
  expect(counts).toEqual({ downloads: 1, writes: 0, recovery: 0 });
});

test("下载失败原位说明且不自动重试或提交", async ({ page }) => {
  const counts = await mockSettings(page, "user");
  let failures = 0;
  await page.route(`**${DOWNLOAD}`, route => { failures++; return route.fulfill({ status: 503, json: { detail: "synthetic-failure" } }); });
  const dialog = await openHelper(page, "personal");
  await dialog.getByRole("button", { name: "下载本机助手", exact: true }).click();
  await expect(dialog.getByRole("alert")).toContainText("安装包下载失败");
  await expect(dialog.getByRole("button", { name: "下载本机助手", exact: true })).toBeEnabled();
  expect(failures).toBe(1);
  expect(counts.writes).toBe(0); expect(counts.recovery).toBe(0);
});

for (const [width, theme] of [[1440, "light"], [390, "dark"]] as const) test(`助手三步键盘、主题与窄屏可访问性 ${width}`, async ({ page }, testInfo) => {
  const counts = await mockSettings(page, "user", theme);
  await page.setViewportSize({ width, height: 844 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  const dialog = await openHelper(page, "personal");
  const close = dialog.getByRole("button", { name: "关闭小红书登录助手", exact: true });
  await expect(close).toBeFocused();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.getByRole("button", { name: "下一步", exact: true })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(close).toBeFocused();
  for (const step of [0, 1, 2]) {
    if (step > 0) await expect.poll(() => dialog.evaluate(element => element.scrollTop)).toBe(0);
    await expect(dialog).toContainText("基础身份：未验证");
    await expect(dialog).toContainText("搜索访问：未验证");
    await expect(dialog.locator('img[src="/overview-brands/xiaohongshu.png"]')).toBeVisible();
    await page.evaluate(() => Promise.all(document.getAnimations().map(animation => animation.finished.catch(() => {}))));
    expect((await new AxeBuilder({ page }).include('[role="dialog"]').analyze()).violations).toEqual([]);
    expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    await dialog.screenshot({ path: testInfo.outputPath(`helper-${width}-${theme}-step-${step + 1}.png`) });
    if (step < 2) await dialog.getByRole("button", { name: "下一步", exact: true }).click();
  }
  await expect(dialog.getByRole("region", { name: "确认导入", exact: true })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "小红书登录助手", exact: true })).toBeFocused();
  expect(counts).toEqual({ downloads: 0, writes: 0, recovery: 0 });
});


for (const channel of CHANNELS) for (const scope of ["personal", "platform"] as const) test(`${scope}十渠道入口和原编辑目标匹配：${channel.name}`, async ({ page }, testInfo) => {
  const narrow = channel.key === "tb_cookie" && scope === "platform";
  const counts = await mockSettings(page, "super_admin", narrow ? "dark" : "light", CHANNELS);
  if (narrow) await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(scope === "personal" ? "/settings?section=credentials" : "/settings?section=platform&service=cookies");
  await page.getByRole("button", { name: `${channel.name}登录助手`, exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText(`${scope === "personal" ? "我的" : "平台"}${channel.name}登录助手`);
  await expect(dialog.getByRole("link", { name: `打开${channel.loginName}官方页面`, exact: true })).toHaveAttribute("href", channel.url);
  const logo = dialog.locator(`img[src="/overview-brands/${channel.logo}"]`);
  await expect(logo).toBeVisible();
  await expect.poll(() => logo.evaluate(image => (image as HTMLImageElement).naturalWidth)).toBeGreaterThan(0);
  if (narrow) {
    expect(await dialog.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
    await dialog.screenshot({ path: testInfo.outputPath("taobao-tmall-platform-dark.png") });
  }
  if (channel.key === "mc_cookie_wb" && scope === "personal") await dialog.screenshot({ path: testInfo.outputPath("weibo-personal-light.png") });
  await dialog.getByRole("button", { name: "下一步", exact: true }).click();
  await expect(dialog).toContainText(`选择“${channel.name}”`);
  await expect(dialog).toContainText("网页不会自动同步扩展中的渠道");
  await dialog.getByRole("button", { name: "下一步", exact: true }).click();
  await expect(dialog.getByRole("button", { name: scope === "personal" ? "进入我的配置" : "进入平台配置", exact: true })).toBeDisabled();
  await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: scope === "personal" ? "进入我的配置" : "进入平台配置", exact: true }).click();
  const input = page.getByRole("dialog").getByLabel(`${channel.name} Cookie`, { exact: true });
  await expect(input).toHaveValue("");
  await expect(input).toHaveAttribute("type", "password");
  expect(counts).toEqual({ downloads: 0, writes: 0, recovery: 0 });
});

test("个人切换渠道重新开始并清除确认，不复用上个渠道结果", async ({ page }) => {
  const counts = await mockSettings(page, "user", "light", CHANNELS);
  await page.goto("/settings?section=credentials");
  await page.getByRole("button", { name: "微博登录助手", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "下一步", exact: true }).click();
  await dialog.getByRole("button", { name: "下一步", exact: true }).click();
  await dialog.getByRole("checkbox").check();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: "京东登录助手", exact: true }).click();
  await expect(dialog).toContainText("我的京东登录助手");
  await expect(dialog.getByRole("link", { name: "打开京东官方页面", exact: true })).toBeVisible();
  await expect(dialog.getByRole("checkbox")).toHaveCount(0);
  await dialog.getByRole("button", { name: "下一步", exact: true }).click();
  await dialog.getByRole("button", { name: "下一步", exact: true }).click();
  await expect(dialog.getByRole("checkbox")).not.toBeChecked();
  await expect(dialog.getByRole("button", { name: "进入我的配置", exact: true })).toBeDisabled();
  expect(counts).toEqual({ downloads: 0, writes: 0, recovery: 0 });
});

for (const role of ["user", "admin"] as const) test(`全部平台助手对${role}保持隐藏`, async ({ page }) => {
  await mockSettings(page, role, "light", CHANNELS);
  await page.goto("/settings?section=platform&service=cookies");
  if (role === "admin") await expect(page.getByRole("button", { name: "配置 微博", exact: true })).toBeVisible();
  else await expect(page.getByRole("button", { name: "平台配置", exact: true })).toHaveCount(0);
  for (const channel of CHANNELS) await expect(page.getByRole("button", { name: `${channel.name}登录助手`, exact: true })).toHaveCount(0);
});

for (const scope of ["personal", "platform"] as const) test(`${scope}非小红书渠道往返助手保留草稿并遮罩`, async ({ page }) => {
  const channel = CHANNELS[1];
  const counts = await mockSettings(page, "super_admin", "light", [channel]);
  await page.goto(scope === "personal" ? "/settings?section=credentials" : "/settings?section=platform&service=cookies");
  await page.getByRole("button", { name: scope === "personal" ? "修改" : "配置 微博", exact: true }).click();
  const input = page.getByRole("dialog").getByLabel("微博 Cookie", { exact: true });
  await input.fill("synthetic-weibo-private-draft");
  await expect(input).toHaveAttribute("type", "password");
  await page.getByRole("button", { name: "打开微博登录助手", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "取消", exact: true }).click();
  await expect(input).toHaveValue("synthetic-weibo-private-draft");
  await expect(input).toHaveAttribute("type", "password");
  await expect(page.locator("body")).not.toContainText("synthetic-weibo-private-draft");
  expect(counts).toEqual({ downloads: 0, writes: 0, recovery: 0 });
});

test("取消未完成下载后换渠道不显示旧下载回执或自动重试", async ({ page }) => {
  const counts = await mockSettings(page, "user", "light", CHANNELS);
  let release!: () => void;
  const pending = new Promise<void>(resolve => { release = resolve; });
  await page.route(`**${DOWNLOAD}`, async route => {
    counts.downloads++;
    await pending;
    await route.fulfill({ contentType: "application/zip", body: Buffer.from([80, 75, 3, 4]) }).catch(() => {});
  });
  await page.goto("/settings?section=credentials");
  await page.getByRole("button", { name: "微博登录助手", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "下载本机助手", exact: true }).click();
  await expect.poll(() => counts.downloads).toBe(1);
  await page.getByRole("dialog").getByRole("button", { name: "取消", exact: true }).click();
  await page.getByRole("button", { name: "京东登录助手", exact: true }).click();
  release();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("我的京东登录助手");
  await expect(dialog.getByRole("button", { name: "下载本机助手", exact: true })).toBeEnabled();
  await expect(dialog.getByRole("status")).toHaveCount(0);
  await expect(dialog.getByRole("alert")).toHaveCount(0);
  expect(counts).toEqual({ downloads: 1, writes: 0, recovery: 0 });
});
