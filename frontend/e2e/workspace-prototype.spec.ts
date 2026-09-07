import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { readFile } from "node:fs/promises";

async function begin(page: Page, scenario = 0) {
  await page.goto("/ux01-prototype.html");
  await expect(page.getByText("交互原型 · 虚构数据，不连接真实服务", { exact: true })).toBeVisible();
  await page.getByLabel("演示场景").selectOption(String(scenario));
}

async function start(page: Page) {
  await page.locator(".ux-example").click();
  await page.getByRole("button", { name: "发送要求", exact: true }).click();
  await page.locator(".ux-pending button").first().click();
}

for (const width of [1440, 390]) {
  for (const dark of [false, true]) {
    for (const scenario of [0, 1, 2, 3, 4, 5]) {
      test(`${width}/${dark ? "暗" : "亮"}/旅程${scenario}：原型全程可达且不请求业务服务`, async ({ page }, testInfo) => {
        await page.setViewportSize({ width, height: 900 });
        const forbidden: string[] = [];
        const errors: string[] = [];
        page.on("pageerror", error => errors.push(error.message));
        await page.route("**/*", route => {
          const url = new URL(route.request().url());
          if (!['127.0.0.1', 'localhost'].includes(url.hostname) || url.pathname.startsWith('/api/')) {
            forbidden.push(url.pathname);
            return route.abort();
          }
          return route.continue();
        });
        await begin(page, scenario);
        if (dark) await page.getByRole("button", { name: "切换深色主题", exact: true }).click();
        if (scenario === 0) await page.screenshot({ path: testInfo.outputPath(`start-${width}-${dark}.png`), fullPage: true });
        await start(page);
        if (scenario === 4) {
          await page.getByRole("button", { name: "模拟登录失效", exact: true }).click();
          await expect(page.getByRole("heading", { name: "登录示意已失效" })).toBeVisible();
          await page.getByRole("button", { name: "刷新登录示意", exact: true }).click();
          await page.getByRole("button", { name: "模拟登录成功并恢复", exact: true }).click();
        }
        if (scenario === 5) {
          const dialog = page.getByRole("dialog");
          await expect(dialog.getByText("季度复盘", { exact: true })).toBeVisible();
          await dialog.getByRole("button", { name: "取消删除", exact: true }).click();
          await expect(dialog).toHaveCount(0);
          await page.locator(".ux-pending button").first().click();
          if (dark) await dialog.getByRole("radio", { name: "也删除共享资料：先暂停依赖任务" }).check();
          await dialog.getByRole("button", { name: "确认模拟删除", exact: true }).click();
          await expect(page.getByRole("heading", { name: "演示清理完成" })).toBeVisible();
          await expect(page.locator(".ux-pending")).toContainText(dark ? "共享来源标记已删除" : "共享原始资料保留");
        } else {
          await page.getByRole("button", { name: "推进到演示结果", exact: true }).click();
          if (scenario === 3) await expect(page.locator(".ux-result-summary")).toContainText("缺口");
          await page.locator(".ux-delivery").click();
          await expect(page.getByRole("region", { name: "结果画布" })).toBeVisible();
          await page.getByRole("button", { name: "专注当前画布", exact: true }).click();
          await expect(page.getByRole("button", { name: "退出结果专注", exact: true })).toBeVisible();
          const downloadEvent = page.waitForEvent("download");
          await page.getByRole("button", { name: "下载 JSON 演示", exact: true }).click();
          const download = await downloadEvent;
          const downloaded = await readFile((await download.path())!, "utf8");
          expect(downloaded).toContain("虚构");
          expect(downloaded).toContain("版本 1");
          if (scenario === 3) expect(downloaded).toContain("南岸店");
          await page.getByRole("button", { name: "定位来源原始行 2—3", exact: true }).click();
          await expect(page.getByRole("region", { name: "资料画布" })).toContainText("原始数据");
          await page.getByRole("button", { name: "返回对话", exact: true }).click();
          await expect(page.getByRole("button", { name: "发送要求", exact: true })).toBeInViewport();
        }
        expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
        if (scenario === 0) {
          const result = await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"]).analyze();
          expect(result.violations.map(item => ({ id: item.id, nodes: item.nodes.map(node => ({ target: node.target, reason: node.failureSummary })) }))).toEqual([]);
          await page.screenshot({ path: testInfo.outputPath(`result-${width}-${dark}.png`), fullPage: true });
        }
        expect(forbidden).toEqual([]);
        expect(errors).toEqual([]);
      });
    }
  }
}

test("空输入、中文IME、停止确认与未知结果恢复保持同一演示", async ({ page }) => {
  await begin(page);
  const input = page.locator("#ux-request");
  await page.getByRole("button", { name: "发送要求", exact: true }).click();
  await expect(input).toBeFocused();
  await expect(input).toHaveAttribute("aria-invalid", "true");
  await input.fill("分析示例资料");
  await input.dispatchEvent("keydown", { key: "Enter", code: "Enter", isComposing: true, bubbles: true });
  await expect(page.locator(".ux-message")).toHaveCount(0);
  await input.press("Shift+Enter");
  await expect(input).toHaveValue(/\n$/);
  await input.press("Enter");
  await page.locator(".ux-pending button").first().click();
  await page.getByRole("button", { name: "停止演示", exact: true }).click();
  await expect(page.getByRole("heading", { name: "停止请求已发出（演示）" })).toBeVisible();
  await page.getByRole("button", { name: "模拟确认已停止", exact: true }).click();
  await page.getByRole("button", { name: "恢复演示执行", exact: true }).click();
  await page.getByRole("button", { name: "模拟结果未知", exact: true }).click();
  await expect(page.getByRole("button", { name: "发送要求", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "查询状态后恢复结果", exact: true }).click();
  await expect(page.locator(".ux-delivery")).toContainText("版本 1");
  await page.locator(".ux-delivery").click();
  await page.getByRole("button", { name: "继续修订此结果", exact: true }).click();
  await page.evaluate(() => new Promise<void>(resolve => requestAnimationFrame(() => resolve())));
  await expect(input).toBeFocused();
  await input.press("Enter");
  await page.getByRole("button", { name: "推进到演示结果", exact: true }).click();
  await page.locator(".ux-delivery").click();
  await page.getByLabel("结果版本").selectOption("1");
  await expect(page.getByRole("table")).toContainText("版本 1");
});

test("窄短视口、键盘抽屉与弹层焦点、长输入和缩放布局", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 568 });
  await page.emulateMedia({ reducedMotion: "reduce" });
  await begin(page);
  const menu = page.getByRole("button", { name: "打开任务导航", exact: true });
  await menu.focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("dialog", { name: "任务导航", exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(menu).toBeFocused();
  await menu.click();
  await page.getByRole("dialog").getByRole("button", { name: "模板", exact: true }).click();
  await page.getByRole("button", { name: "应用本人演示模板", exact: true }).click();
  await expect(page.locator("#ux-request")).toHaveValue(/月度复盘/);
  await expect(menu).toBeFocused();
  await page.locator("#ux-request").fill("请核对来源并保留缺口。".repeat(100));
  await page.getByRole("button", { name: "发送要求", exact: true }).scrollIntoViewIfNeeded();
  await expect(page.getByRole("button", { name: "发送要求", exact: true })).toBeInViewport();
  await page.setViewportSize({ width: 720, height: 450 });
  // 1440×900在200%缩放下的等效CSS视口，避免CSS zoom改变布局两次。
  await page.getByRole("button", { name: "发送要求", exact: true }).scrollIntoViewIfNeeded();
  await expect(page.getByRole("button", { name: "发送要求", exact: true })).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
});

test("取消来源登录后恢复仍需登录，不能直接执行", async ({ page }) => {
  await begin(page, 4);
  await start(page);
  await page.getByRole("button", { name: "取消本次读取", exact: true }).click();
  await page.getByRole("button", { name: "恢复演示执行", exact: true }).click();
  await expect(page.getByRole("heading", { name: "请完成来源登录", exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "推进到演示结果", exact: true })).toHaveCount(0);
});

test("原生场景选择器可用键盘打开、选择并返回任务", async ({ page }) => {
  await begin(page);
  const selector = page.getByLabel("演示场景");
  await selector.focus();
  await selector.press("Alt+ArrowDown");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await expect(selector).toHaveValue("1");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("了解社区零售的新变化");
});

test("混合资料分别核对文件与网页原文", async ({ page }) => {
  await begin(page, 2);
  await start(page);
  await page.getByRole("button", { name: "推进到演示结果", exact: true }).click();
  await page.locator(".ux-result-summary .ux-source-link").click();
  await page.getByRole("button", { name: "核对文件原始行", exact: true }).click();
  await expect(page.getByRole("region", { name: "资料画布" })).toContainText("销售");
  await page.getByRole("button", { name: "核对网页原始段落", exact: true }).click();
  await expect(page.getByRole("region", { name: "资料画布" })).toContainText("社区零售");
  await expect(page.getByRole("article", { name: "网页原始段落" })).not.toContainText("128,000");
});

test("部分来源重试失败后保留现有结果和下载缺口", async ({ page }) => {
  await begin(page, 3);
  await start(page);
  await page.getByRole("button", { name: "推进到演示结果", exact: true }).click();
  await page.getByRole("button", { name: "重试缺失来源（演示）", exact: true }).click();
  await page.getByRole("button", { name: "模拟重试仍失败", exact: true }).click();
  await expect(page.getByRole("status").filter({ hasText: "南岸店" })).toContainText("仍");
  await page.locator(".ux-delivery").click();
  for (const format of ["MD", "CSV", "JSON"]) {
    const waiting = page.waitForEvent("download");
    await page.getByRole("button", { name: `下载 ${format} 演示`, exact: true }).click();
    const download = await waiting;
    expect(await readFile((await download.path())!, "utf8")).toContain("南岸店");
  }
});

test("版本二新增内容与三种下载一致，切回版本一保留旧内容", async ({ page }) => {
  await begin(page);
  await start(page);
  await page.getByRole("button", { name: "推进到演示结果", exact: true }).click();
  await page.locator(".ux-delivery").click();
  await page.getByRole("button", { name: "继续修订此结果", exact: true }).click();
  await page.getByRole("button", { name: "发送要求", exact: true }).click();
  await page.getByRole("button", { name: "推进到演示结果", exact: true }).click();
  await page.locator(".ux-delivery").click();
  const canvas = page.getByRole("region", { name: "结果画布" });
  await expect(canvas).toContainText("新增核对事项");
  for (const version of ["2", "1"]) {
    await page.getByLabel("结果版本").selectOption(version);
    if (version === "1") await expect(canvas).not.toContainText("新增核对事项");
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
