import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFile } from "node:fs/promises";

async function begin(page: Page) {
  await page.goto("/ux01-prototype.html");
  await expect(page.getByText("交互原型 · 虚构数据", { exact: true })).toBeVisible();
}
async function scenario(page: Page, value: number) {
  await page.locator(".ux-demo-tools > summary").click();
  await page.getByLabel("演示场景").selectOption(String(value));
  await page.locator(".ux-demo-tools > summary").click();
}
async function send(page: Page, text?: string) {
  if (text) await page.locator("#ux-request").fill(text);
  await page.getByRole("button", { name: "发送要求", exact: true }).click();
}
async function attach(page: Page) {
  await page.getByRole("button", { name: "添加文件", exact: true }).click();
  await page.getByRole("button", { name: "添加 门店销售示例.csv", exact: true }).click();
}
for (const width of [1440, 390]) for (const dark of [false, true]) {
  test(`${width}/${dark ? "暗" : "亮"}：对话搜索、自动文件预览、继续追问`, async ({ page }, testInfo) => {
    await page.setViewportSize({ width, height: 900 });
    await page.emulateMedia({ reducedMotion: "reduce" });
    const forbidden: string[] = [], errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    await page.route("**/*", route => {
      const url = new URL(route.request().url());
      if (!["127.0.0.1", "localhost"].includes(url.hostname) || url.pathname.startsWith("/api/")) {
        forbidden.push(url.href); return route.abort();
      }
      return route.continue();
    });
    await begin(page);
    if (dark) await page.getByRole("button", { name: "切换深色主题", exact: true }).click();
    const preview = page.getByRole("region", { name: "文件预览" });
    await expect(preview).toHaveCount(0);
    await expect(page.getByLabel("演示场景")).not.toBeVisible();
    await expect(page.getByLabel("选择模型")).toBeVisible();
    await page.screenshot({ path: testInfo.outputPath("start.png"), fullPage: true });
    await send(page, "帮我搜索社区零售的新变化");
    await expect(page.locator(".ux-search-results")).toBeVisible();
    await expect(preview).toHaveCount(0);
    await expect(page.locator(".ux-search-results").getByRole("table")).toHaveCount(0);
    await page.screenshot({ path: testInfo.outputPath("search.png"), fullPage: true });
    const before = await page.locator(".ux-conversation").boundingBox();
    await attach(page);
    await expect(preview).toBeVisible();
    await expect(preview).toContainText("门店销售示例.csv");
    if (width === 390) expect((await preview.boundingBox())!.width).toBeGreaterThanOrEqual(width - 4);
    if (width === 1440) {
      const after = await page.locator(".ux-conversation").boundingBox();
      expect(after!.width).toBeLessThan(before!.width - 200);
      const composer = await page.locator(".ux-composer").boundingBox();
      expect(composer!.x + composer!.width).toBeLessThanOrEqual(after!.x + after!.width + 1);
    } else await page.getByRole("button", { name: "查看对话", exact: true }).click();
    await send(page, "分析这份文件，比较两家门店");
    await expect(page.locator('[role="tablist"] [role="tab"]')).toHaveCount(2);
    if (width === 390) await page.getByRole("button", { name: /^查看文件/ }).click();
    // 后台生成结果不抢走正在核对的原始文件。
    await expect(page.getByRole("tab", { selected: true })).toContainText("门店销售示例.csv");
    await page.screenshot({ path: testInfo.outputPath("preview.png"), fullPage: true });
    const audit = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
    expect(audit.violations.map(v => ({ id: v.id, nodes: v.nodes.map(n => n.target) }))).toEqual([]);
    await page.getByRole("button", { name: "关闭文件预览", exact: true }).click();
    await expect(preview).toHaveCount(0);
    await expect(page.getByRole("button", { name: "发送要求", exact: true })).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
    expect(forbidden).toEqual([]); expect(errors).toEqual([]);
  });
}
test("预览宽度支持键盘及拖动，关闭后恢复对话宽度", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await begin(page);
  const initial = await page.locator(".ux-conversation").boundingBox();
  await attach(page);
  const divider = page.getByRole("separator", { name: "调整文件预览宽度" });
  const preview = page.getByRole("region", { name: "文件预览" });
  const before = await preview.boundingBox();
  await divider.focus(); await divider.press("ArrowLeft");
  const keyboard = await preview.boundingBox();
  expect(Math.abs(keyboard!.width - before!.width)).toBeGreaterThan(5);
  const box = await divider.boundingBox();
  await page.mouse.move(box!.x + box!.width / 2, box!.y + 100); await page.mouse.down();
  await page.mouse.move(box!.x - 70, box!.y + 100, { steps: 5 }); await page.mouse.up();
  const dragged = await preview.boundingBox();
  expect(Math.abs(dragged!.width - keyboard!.width)).toBeGreaterThan(20);
  await page.getByRole("button", { name: "关闭文件预览", exact: true }).click();
  expect((await page.locator(".ux-conversation").boundingBox())!.width).toBeCloseTo(initial!.width, 0);
});
test("模型选择与空输入中文IME", async ({ page }) => {
  await begin(page);
  const input = page.locator("#ux-request");
  await send(page); await expect(input).toHaveAttribute("aria-invalid", "true"); await expect(input).toBeFocused();
  const model = page.getByLabel("选择模型");
  const options = await model.locator("option").evaluateAll(nodes => nodes.map(n => (n as HTMLOptionElement).value));
  expect(options.length).toBeGreaterThan(1);
  await model.selectOption(options[1]); await input.fill("搜索社区零售");
  await input.dispatchEvent("keydown", { key: "Enter", code: "Enter", isComposing: true, bubbles: true });
  await expect(page.locator(".ux-message")).toHaveCount(0);
  await input.press("Shift+Enter"); await expect(input).toHaveValue(/\n$/); await input.press("Enter");
  await expect(page.locator(".ux-search-results")).toBeVisible();
  const first = page.getByRole("article", { name: "Mangrove 回复", exact: true }).first();
  await expect(first.locator(".ux-assistant")).toContainText(options[1]);
  await model.selectOption(options[0]);
  await send(page, "这些变化为什么值得注意？");
  await expect(page.getByRole("article", { name: "Mangrove 回复", exact: true })).toHaveCount(2);
  const second = page.getByRole("article", { name: "Mangrove 回复", exact: true }).last();
  await expect(first.locator(".ux-assistant")).toContainText(options[1]);
  await expect(second.locator(".ux-assistant")).toContainText(options[0]);
  await expect(second).not.toContainText("12.3%");
  await expect(second.locator(".ux-search-results")).toBeVisible();
});
test("短视口长输入可发送，导航弹层键盘焦点恢复", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 568 }); await begin(page);
  const menu = page.getByRole("button", { name: "打开任务导航", exact: true });
  await menu.focus(); await menu.press("Enter");
  await expect(page.getByRole("dialog", { name: "任务导航", exact: true })).toBeVisible();
  await page.keyboard.press("Escape"); await expect(menu).toBeFocused();
  await page.locator("#ux-request").fill("请核对来源并保留缺口。".repeat(100));
  for (const viewport of [{ width: 390, height: 568 }, { width: 720, height: 450 }]) {
    // 720×450是1440×900的200%等效CSS回流，不等于真实设备软键盘验证。
    await page.setViewportSize(viewport);
    await page.getByRole("button", { name: "发送要求", exact: true }).scrollIntoViewIfNeeded();
    await expect(page.getByRole("button", { name: "发送要求", exact: true })).toBeInViewport();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  }
});

test("停止与未知恢复撤销迟到的自动完成", async ({ page }) => {
  await begin(page); await page.clock.install();
  await send(page, "搜索社区零售");
  await page.getByRole("button", { name: "停止演示", exact: true }).click();
  await page.clock.runFor(1200);
  await expect(page.getByRole("heading", { name: "停止请求已发出（演示）" })).toBeVisible();
  await expect(page.locator(".ux-search-results")).toHaveCount(0);
  await page.getByRole("button", { name: "模拟确认已停止", exact: true }).click();
  await page.getByRole("button", { name: "恢复演示执行", exact: true }).click();
  await page.locator(".ux-demo-tools > summary").click();
  await page.getByRole("button", { name: "模拟结果未知", exact: true }).click();
  await page.clock.runFor(1200);
  await expect(page.getByRole("button", { name: "发送要求", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "查询状态后恢复结果", exact: true }).click();
  await expect(page.locator(".ux-search-results")).toHaveCount(1);
});

test("登录取消后恢复及重新发送都不能绕过登录", async ({ page }) => {
  await begin(page); await scenario(page, 4); await send(page);
  await page.getByRole("button", { name: "确认本次只读范围", exact: true }).click();
  await page.getByRole("button", { name: "取消本次读取", exact: true }).click();
  await send(page, "继续读取");
  await expect(page.getByRole("heading", { name: "请完成来源登录", exact: true })).toBeVisible();
  await page.locator(".ux-demo-tools > summary").click();
  await page.getByRole("button", { name: "模拟登录失效", exact: true }).click();
  await page.getByRole("button", { name: "刷新登录示意", exact: true }).click();
  await page.getByRole("button", { name: "模拟登录成功并恢复", exact: true }).click();
  await expect(page.getByRole("tab", { name: /v1/ })).toBeVisible();
});

for (const shared of [false, true]) test(`关联删除保留独立任务，共享资料选择${shared}`, async ({ page }) => {
  await begin(page); await scenario(page, 5); await send(page);
  await page.getByRole("button", { name: "查看关联与删除范围", exact: true }).click();
  await page.getByRole("button", { name: "取消删除", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await page.getByRole("button", { name: "查看关联与删除范围", exact: true }).click();
  if (shared) await page.getByRole("radio", { name: "也删除共享资料：先暂停依赖任务" }).check();
  await page.getByRole("button", { name: "确认模拟删除", exact: true }).click();
  await expect(page.locator(".ux-pending")).toContainText(shared ? "共享来源标记已删除" : "共享原始资料保留");
  await expect(page.locator(".ux-pending")).toContainText("季度复盘");
  await expect(page.getByRole("button", { name: "发送要求", exact: true })).toBeDisabled();
});

test("部分资料重试失败仍保留结果，三格式下载包含缺口", async ({ page }) => {
  await begin(page); await scenario(page, 3); await send(page);
  await page.getByLabel("选择模型").selectOption("澄明 · 深入");
  await page.getByRole("button", { name: "先处理可用资料，保留缺口", exact: true }).click();
  await expect(page.getByRole("tab", { name: /v1/ })).toBeVisible();
  await expect(page.getByRole("article", { name: "Mangrove 回复", exact: true }).last().locator(".ux-assistant")).toContainText("澄明 · 深入");
  await page.getByRole("button", { name: "重试缺失来源（演示）", exact: true }).click();
  await page.getByRole("button", { name: "模拟重试仍失败", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "重试仍失败" })).toContainText("南岸店");
  await page.getByRole("tab", { name: /v1/ }).click();
  for (const format of ["MD", "CSV", "JSON"]) {
    const waiting = page.waitForEvent("download");
    await page.getByRole("button", { name: `下载 ${format} 演示`, exact: true }).click();
    const download = await waiting;
    expect(await readFile((await download.path())!, "utf8")).toContain("南岸店");
  }
});

test("混合资料分别核对文件与网页，修订不篡改版本一", async ({ page }) => {
  await begin(page); await scenario(page, 2); await send(page);
  await page.getByRole("tab", { name: /v1/ }).click();
  await page.getByRole("button", { name: "核对网页原始段落", exact: true }).click();
  await expect(page.getByRole("article", { name: "网页原始段落" })).toContainText("社区零售");
  await expect(page.getByRole("article", { name: "网页原始段落" })).not.toContainText("128,000");
  await page.getByRole("tab", { name: /v1/ }).click();
  await page.getByRole("button", { name: "继续修订此结果", exact: true }).click();
  await expect(page.locator("#ux-request")).toBeFocused();
  await send(page);
  await expect(page.getByRole("tab", { name: /v2/ })).toBeVisible();
  await expect(page.getByRole("tab", { selected: true })).toContainText("v1");
  for (const version of ["2", "1"]) {
    await page.getByLabel("结果版本").selectOption(version);
    for (const format of ["MD", "CSV", "JSON"]) {
      const waiting = page.waitForEvent("download");
      await page.getByRole("button", { name: `下载 ${format} 演示`, exact: true }).click();
      const download = await waiting;
      const body = await readFile((await download.path())!, "utf8");
      expect(body).toContain(`版本 ${version}`);
      expect(body.includes("新增核对事项")).toBe(version === "2");
    }
  }
});




test("展开预览后关闭或切窄屏，对话不会消失", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 }); await begin(page); await attach(page);
  await page.getByRole("button", { name: "展开文件预览", exact: true }).click();
  await expect(page.locator(".ux-conversation")).not.toBeVisible();
  await page.getByRole("button", { name: "打开文件预览", exact: true }).click();
  await expect(page.locator("#ux-request")).toBeVisible();
  await page.getByRole("button", { name: "打开文件预览", exact: true }).click();
  await page.getByRole("button", { name: "展开文件预览", exact: true }).click();
  await page.setViewportSize({ width: 390, height: 900 });
  await page.getByRole("button", { name: "查看对话", exact: true }).click();
  await expect(page.locator("#ux-request")).toBeVisible();
});

test("生成途中打开原始文件不被迟到结果抢走", async ({ page }) => {
  await begin(page); await page.clock.install();
  await send(page, "请生成社区零售观察报告");
  await attach(page);
  await page.clock.runFor(1200);
  await expect(page.getByRole("tab", { name: /v1/ })).toBeVisible();
  await expect(page.getByRole("tab", { selected: true })).toContainText("门店销售示例.csv");
});

test("手机文件标签键盘切换，修订后焦点回输入", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 900 }); await begin(page);
  await scenario(page, 0); await send(page);
  await expect(page.locator('[role="tablist"] [role="tab"]')).toHaveCount(2);
  await page.getByRole("button", { name: /^查看文件/ }).click();
  const file = page.getByRole("tab", { name: "门店销售示例.csv", exact: true });
  await file.focus(); await file.press("ArrowRight");
  await expect(page.getByRole("tab", { selected: true })).toContainText("v1");
  await page.getByRole("button", { name: "继续修订此结果", exact: true }).click();
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
  await expect(page.locator("#ux-request")).toBeFocused();
  await expect(page.locator("#ux-request")).toBeInViewport();
});

for (const width of [1440, 390]) for (const dark of [false, true]) test(`${width}/${dark ? "暗" : "亮"}：混合、部分、登录及删除旅程可达`, async ({ page }) => {
  await page.setViewportSize({ width, height: 900 });
  for (const kind of [2, 3, 4, 5]) {
    await begin(page);
    if (dark) await page.getByRole("button", { name: "切换深色主题", exact: true }).click();
    await scenario(page, kind); await send(page);
    if (kind >= 3) await page.locator(".ux-pending button").first().click();
    if (kind === 4) await page.getByRole("button", { name: "模拟登录成功并恢复", exact: true }).click();
    if (kind === 5) {
      await page.getByRole("button", { name: "取消删除", exact: true }).click();
      await page.getByRole("button", { name: "查看关联与删除范围", exact: true }).click();
      await page.getByRole("button", { name: "确认模拟删除", exact: true }).click();
      await expect(page.getByRole("heading", { name: "演示清理完成", exact: true })).toBeVisible();
    } else {
      await expect(page.locator('[role="tablist"] [role="tab"]').filter({ hasText: "v1" })).toHaveCount(1);
      if (width === 390) await page.getByRole("button", { name: /^查看文件/ }).click();
      await page.getByRole("tab", { name: /v1/ }).click();
      const preview = page.getByRole("region", { name: "文件预览" });
      await expect(preview).toBeVisible();
      if (kind === 3) await expect(preview).toContainText("南岸店");
      if (kind === 2) {
        await page.getByRole("button", { name: "核对网页原始段落", exact: true }).click();
        await expect(page.getByRole("article", { name: "网页原始段落" })).toBeVisible();
      }
      await page.getByRole("button", { name: "关闭文件预览", exact: true }).click();
      await expect(page.getByRole("button", { name: "发送要求", exact: true })).toBeInViewport();
    }
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  }
});
