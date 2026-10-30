import { expect, test, type Page } from "@playwright/test";

const files = ["data.xlsx", "evidence.zip", "data.json"].map(name => ({ name, url: `/api/downloads/collection-preview/${name}` }));
const preview = {
  kind: "json", text: '{"query":"合成权益资料","candidates":[]}', truncated: false,
  benefit_table: { total_rows: 1, truncated: false, rows: [{
    benefit_name: "合成机场接送", bank: "示例银行", benefit_category: "出行",
    customer_segment: null, quota: "每年一次", threshold: "合成资产门槛", point_system: "示例积分",
    point_cost: "800积分", point_earn_rule: null, app_path: "服务 → 出行",
    activity_period: "活动原文范围", registration_period: "报名原文范围",
    redemption_period: "兑换原文范围", usage_period: "使用原文范围",
    valid_from: null, valid_to: null, note_id: "synthetic-note",
    note_url: "https://example.com/source-note", source_title: "合成权益原帖",
  }] }, outcome: { status: "partial", selected_count: 4, target_complete: false },
};

async function setup(page: Page) {
  // 全部接口均为合成数据，未声明的请求不会访问真实服务。
  await page.route("**/api/**", route => route.fulfill({ status: 404, json: {} }));
  await page.addInitScript(() => {
    localStorage.setItem("mangrove_token", "synthetic-test");
    localStorage.setItem('onboarding_all_["u1","admin"]', "skipped");
  });
  await page.route("**/api/auth/me", route => route.fulfill({ json: { user_id: "u1", username: "tester", role: "admin" } }));
  await page.route(/\/api\/operations\/(visits|heartbeat)$/, route => route.fulfill({ json: { ok: true } }));
  await page.route("**/api/semantic-workspace/tasks?*", route => route.fulfill({ json: [] }));
  await page.route("**/api/chat/history", route => route.fulfill({ json: [] }));
  await page.route("**/api/chat/feedback**", route => route.fulfill({ json: { feedback: {} } }));
  await page.route("**/api/models", route => route.fulfill({ json: { options: [{ provider: "local", model: "Synthetic", label: "合成模型" }], default: { provider: "local", model: "Synthetic", label: "合成模型" }, pi_runtime_enabled: true } }));
  await page.route("**/api/model-connections", route => route.fulfill({ json: { items: [] } }));
  await page.route("**/api/model-connections/preferences/default", route => route.fulfill({ json: { preference: null } }));
  await page.route("**/api/semantic-workspace/storage", route => route.fulfill({ json: { task_count: 0, recycle_bin_count: 0, total_bytes: 0 } }));
  await page.route("**/api/semantic-workspace/guidance", route => route.fulfill({ json: { steps: [], examples: [], supported_formats: ["json", "xlsx"] } }));
  await page.route("**/api/chat/running/preview-conv", route => route.fulfill({ json: { running: false, progress: [] } }));
  await page.route("**/api/conversations/preview-conv/messages", route => route.fulfill({ json: [{ id: 1, role: "assistant", content: "合成部分采集结果", task_id: "collection-preview", meta: { files } }] }));
  await page.route("**/api/downloads/collection-preview/*", route => route.fulfill({ contentType: "application/octet-stream", body: "synthetic-only", headers: { "Content-Disposition": 'attachment; filename="data.json"' } }));
}

for (const surface of ["历史任务", "即时对话"]) test(`采集产物同页预览、独立时间与下载保留：${surface}`, async ({ page }, testInfo) => {
  await setup(page);
  let jsonCalls = 0;
  const sheetCalls: number[] = [];
  const pageErrors: string[] = [];
  page.on("pageerror", error => pageErrors.push(error.message));
  await page.route("**/api/downloads/collection-preview?*", route => {
    const params = new URL(route.request().url()).searchParams;
    expect(route.request().method()).toBe("GET");
    if (params.get("preview") === "data.json") { jsonCalls++; return route.fulfill({ json: preview }); }
    expect(params.get("preview")).toBe("data.xlsx");
    const sheet = Number(params.get("sheet")); sheetCalls.push(sheet);
    return route.fulfill({ json: { sheets: ["权益", "覆盖说明"], total_rows: 2, total_columns: 1,
      cells: [[{ text: sheet ? "未达到全部数量" : "合成机场接送", style: {} }]], widths: [180], heights: [32], merges: [], note: "合成工作表窗口" } });
  });
  if (surface === "历史任务") await page.goto("/data-prep?conversation=preview-conv");
  else {
    await page.route("**/api/semantic-workspace/draft/turns", route => route.fulfill({ contentType: "text/event-stream", body: `event: result\ndata: ${JSON.stringify({ reply: "合成部分采集结果", conv_id: "preview-conv", message_id: 1, files })}\n\nevent: done\ndata: {}\n\n` }));
    await page.goto("/data-prep");
    const input = page.getByRole("textbox", { name: "任务要求", exact: true });
    await input.fill("采集合成权益资料"); await input.press("Enter");
  }
  await expect(page.getByRole("button", { name: "预览已采集资料", exact: true })).toHaveCount(0);
  const section = page.getByRole("region", { name: "已采集资料预览", exact: true });
  for (const file of files) await expect(section.getByRole("button", { name: `下载 ${file.name}`, exact: true })).toBeVisible();
  await expect(section.getByRole("button", { name: "权益表", exact: true })).toHaveAttribute("aria-pressed", "true");
  await expect(section.getByRole("table")).toContainText("合成机场接送");
  const desktopPanel = await section.boundingBox();
  const desktopMessage = await page.getByRole("article", { name: "智能体回复", exact: true }).boundingBox();
  expect(desktopPanel!.x).toBeGreaterThanOrEqual(desktopMessage!.x + desktopMessage!.width);
  const downloads = page.getByRole("region", { name: "已采集资料下载", exact: true }).first();
  for (const file of files) await expect(downloads.getByRole("button", { name: `下载 ${file.name}`, exact: true })).toBeVisible();
  await expect(section.getByRole("table")).toContainText("兑换积分：800积分");
  for (const [label, value] of [["活动期", "活动原文范围"], ["报名期", "报名原文范围"], ["领取/兑换期", "兑换原文范围"], ["使用期", "使用原文范围"]]) await expect(section.getByRole("table")).toContainText(`${label}：${value}`);
  await expect(section.getByRole("table")).toContainText("适用客群：未提供");
  await expect(section).toContainText("尚非正式交付");
  const link = section.getByRole("link", { name: "合成权益原帖", exact: true });
  await expect(link).toHaveAttribute("href", "https://example.com/source-note");
  await expect(link).toHaveAttribute("target", "_blank"); await expect(link).toHaveAttribute("rel", "noopener noreferrer");
  await page.screenshot({ path: testInfo.outputPath("collection-preview-desktop.png") });
  const resize = await page.getByRole("separator", { name: "调整文件预览宽度", exact: true }).boundingBox();
  await page.mouse.move(resize!.x + resize!.width / 2, resize!.y + 80);
  await page.mouse.down(); await page.mouse.move(resize!.x - 70, resize!.y + 80); await page.mouse.up();
  await expect.poll(async () => (await section.boundingBox())!.width).toBeGreaterThan(desktopPanel!.width + 30);
  await section.getByRole("button", { name: "展开预览", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "任务要求", exact: true })).toBeHidden();
  await section.getByRole("button", { name: "恢复分栏", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "任务要求", exact: true })).toBeVisible();
  await section.getByRole("button", { name: "JSON 原始数据", exact: true }).click();
  await expect(section.getByLabel("JSON 资料内容", { exact: true })).toContainText('"query":"合成权益资料"');
  await section.getByRole("button", { name: "Excel 工作表", exact: true }).click();
  await expect(section.getByLabel("工作表内容", { exact: true })).toContainText("合成机场接送");
  await section.getByRole("combobox", { name: "工作表", exact: true }).selectOption("1");
  await expect(section.getByLabel("工作表内容", { exact: true })).toContainText("未达到全部数量");
  expect(sheetCalls).toEqual([0, 1]);
  await page.setViewportSize({ width: 390, height: 844 });
  await section.getByRole("button", { name: "权益表", exact: true }).click();
  await section.scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const table = section.getByLabel("权益表内容", { exact: true });
  await table.focus(); await page.keyboard.press("ArrowRight");
  await expect.poll(() => table.evaluate(element => element.scrollLeft)).toBeGreaterThan(0);
  await page.screenshot({ path: testInfo.outputPath("collection-preview-mobile.png") });
  const beforeClose = jsonCalls;
  await section.getByRole("button", { name: "关闭资料预览", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "任务要求", exact: true })).toBeFocused();
  await page.getByRole("button", { name: "查看资料", exact: true }).click();
  await expect(section.getByRole("button", { name: "权益表", exact: true })).toHaveAttribute("aria-pressed", "true");
  expect(jsonCalls).toBe(beforeClose);
  const download = page.waitForEvent("download");
  await section.getByRole("button", { name: "下载 data.json", exact: true }).click();
  await download;
  expect(pageErrors).toEqual([]);
});

test("预览失败保留下载，通用 JSON 不虚构权益表且危险地址不生成链接", async ({ page }) => {
  await setup(page);
  let failed = true;
  const rawJson = '{ "opaque_id": 9007199254740993, "query": "合成权益资料" }';
  await page.route("**/api/downloads/collection-preview?*", route => route.fulfill(failed ? { status: 409, json: { detail: "资料暂时无法预览" } } : { json: { ...preview, text: rawJson, benefit_table: null, truncated: false } }));
  await page.goto("/data-prep?conversation=preview-conv");
  const section = page.getByRole("region", { name: "已采集资料预览", exact: true });
  await expect(section.getByRole("alert")).toContainText("资料暂时无法预览");
  for (const file of files) await expect(section.getByRole("button", { name: `下载 ${file.name}`, exact: true })).toBeVisible();
  failed = false;
  await section.getByRole("button", { name: "重新读取预览", exact: true }).click();
  await expect(section.getByLabel("JSON 资料内容", { exact: true })).toHaveText(rawJson);
  await expect(section.getByRole("button", { name: "权益表", exact: true })).toHaveCount(0);
  await expect(section).toContainText("已显示本文件 JSON 内容。");
  await page.route("**/api/downloads/collection-preview?*", route => route.fulfill({ json: { ...preview, truncated: true, benefit_table: { ...preview.benefit_table, rows: [{ ...preview.benefit_table.rows[0], note_url: "javascript:alert(1)" }] } } }));
  await page.reload();
  await expect(section.getByRole("table")).toBeVisible();
  await expect(section.getByRole("link")).toHaveCount(0);
  await section.getByRole("button", { name: "JSON 原始数据", exact: true }).click();
  await expect(section).toContainText("仅展示前 16000 字以内，非全文");
});

test("上传 Excel 保留全局焦点策略，不因窗口重新可见而重复读取", async ({ page }) => {
  await setup(page);
  let reads = 0;
  await page.route("**/api/data-sources/uploads", route => route.fulfill({ json: { upload_id: "focus-book", original_name: "focus.xlsx", media_type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", size_bytes: 100, sha256: "a".repeat(64) } }));
  await page.route("**/api/data-sources/uploads/focus-book/workbook-preview?*", route => {
    reads++;
    return route.fulfill({ json: { sheets: ["合成工作表"], total_rows: 1, total_columns: 1, cells: [[{ text: "原上传预览", style: {} }]], widths: [140], heights: [28], merges: [], note: "合成工作簿" } });
  });
  await page.goto("/data-prep");
  await page.locator('input[type="file"]').setInputFiles({ name: "focus.xlsx", mimeType: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buffer: Buffer.from("synthetic") });
  await expect(page.getByLabel("工作表内容", { exact: true })).toContainText("原上传预览");
  const beforeFocus = reads;
  await page.evaluate(async () => {
    window.dispatchEvent(new Event("visibilitychange"));
    // 一次重新可见事件后等待网络调度，断言不会启动新的工作簿读取。
    await new Promise<void>(resolve => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
  });
  expect(reads).toBe(beforeFocus);
});

test("切换会话与新任务清除旧预览，迟到请求不能回填其他任务", async ({ page }) => {
  await setup(page);
  await page.route("**/api/chat/history", route => route.fulfill({ json: [
    { conv_id: "preview-conv", title: "合成有资料任务", status: "needs_input", updated_at: "2026-10-07T12:00:00+08:00" },
    { conv_id: "plain-conv", title: "合成无资料任务", status: "completed", updated_at: "2026-10-07T12:01:00+08:00" },
  ] }));
  await page.route("**/api/chat/running/plain-conv", route => route.fulfill({ json: { running: false, progress: [] } }));
  await page.route("**/api/conversations/plain-conv/messages", route => route.fulfill({ json: [{ id: 2, role: "assistant", content: "这是第二个合成会话，未生成文件" }] }));
  let release: () => void = () => {};
  const waiting = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/downloads/collection-preview?*", async route => {
    await waiting;
    await route.fulfill({ json: preview }).catch(() => {});
  });
  await page.goto("/data-prep?conversation=preview-conv");
  const section = page.getByRole("region", { name: "已采集资料预览", exact: true });
  await expect(section).toContainText("正在读取已采集资料");
  await page.getByRole("button", { name: /合成无资料任务/ }).click();
  await expect(page).toHaveURL(/conversation=plain-conv/);
  await expect(page.getByText("这是第二个合成会话，未生成文件", { exact: true })).toBeVisible();
  release();
  await expect(section).toHaveCount(0);
  await expect(page.getByRole("button", { name: "查看资料", exact: true })).toHaveCount(0);
  await page.getByRole("button", { name: /合成有资料任务/ }).click();
  await expect(section.getByRole("table")).toContainText("合成机场接送");
  await page.getByRole("button", { name: "新建任务", exact: true }).click();
  await expect(page).toHaveURL(/\/data-prep$/);
  await expect(section).toHaveCount(0);
  await expect(page.getByRole("textbox", { name: "任务要求", exact: true })).toBeVisible();
});

test("资料内容填满右栏剩余高度，长字段换行且滚动表头不透字", async ({ page }, testInfo) => {
  await setup(page);
  const rows = Array.from({ length: 40 }, (_, index) => ({ ...preview.benefit_table.rows[0], benefit_name: `合成权益${index + 1}`,
    threshold: index === 0 ? "A".repeat(320) : `合成条件${index + 1}`, source_title: `合成原帖${index + 1}` }));
  const text = JSON.stringify({ synthetic: "合成布局验证", rows: Array.from({ length: 150 }, (_, index) => ({ index, content: "合成原始资料文字" })) }, null, 2);
  await page.route("**/api/downloads/collection-preview?*", route => {
    if (new URL(route.request().url()).searchParams.get("preview") === "data.json") return route.fulfill({ json: { ...preview, text, benefit_table: { rows, total_rows: rows.length, truncated: false } } });
    return route.fulfill({ json: { sheets: ["合成长工作表"], total_rows: 100, total_columns: 1,
      cells: Array.from({ length: 100 }, (_, index) => [{ text: `合成行${index + 1}`, style: {} }]), widths: [180], heights: Array(100).fill(28), merges: [], note: "合成布局工作表" } });
  });
  await page.setViewportSize({ width: 1600, height: 1200 });
  await page.goto("/data-prep?conversation=preview-conv");
  const section = page.getByRole("region", { name: "已采集资料预览", exact: true });
  const table = section.getByLabel("权益表内容", { exact: true });
  await expect(table).toContainText("合成权益40");
  await page.screenshot({ path: testInfo.outputPath("layout-tall-table.png") });
  for (const height of [1200, 640]) {
    await page.setViewportSize({ width: 1600, height });
    const panelBounds = (await section.boundingBox())!;
    const tableBounds = (await table.boundingBox())!;
    expect(panelBounds.y + panelBounds.height - tableBounds.y - tableBounds.height).toBeLessThan(30);
    expect(tableBounds.y + tableBounds.height).toBeLessThanOrEqual(panelBounds.y + panelBounds.height);
    expect(await table.locator("table").evaluate(element => element.getBoundingClientRect().width <= Math.max(element.parentElement!.clientWidth, 640) + 2)).toBe(true);
  }
  await page.setViewportSize({ width: 1600, height: 1200 });
  const header = table.getByRole("columnheader", { name: "条件", exact: true });
  await expect(header).toHaveCSS("position", "sticky");
  expect(await header.evaluate(element => getComputedStyle(element).backgroundColor)).not.toBe("rgba(0, 0, 0, 0)");
  const topBefore = (await header.boundingBox())!.y;
  await table.evaluate(element => { element.scrollTop = 310; });
  await expect.poll(async () => Math.abs((await header.boundingBox())!.y - topBefore)).toBeLessThanOrEqual(1);
  expect(await header.evaluate(element => {
    const rect = element.getBoundingClientRect();
    return element.contains(document.elementFromPoint(rect.x + rect.width / 2, rect.y + rect.height / 2));
  })).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("layout-sticky-header.png") });
  await section.getByRole("button", { name: "JSON 原始数据", exact: true }).click();
  const json = section.getByLabel("JSON 资料内容", { exact: true });
  const panel = (await section.boundingBox())!, jsonBounds = (await json.boundingBox())!;
  expect(panel.y + panel.height - jsonBounds.y - jsonBounds.height).toBeLessThan(60);
  await section.getByRole("button", { name: "Excel 工作表", exact: true }).click();
  const workbook = section.getByRole("region", { name: "Excel 工作表预览", exact: true });
  await expect(workbook.getByLabel("工作表内容", { exact: true })).toContainText("合成行100");
  const workbookBounds = (await workbook.boundingBox())!;
  expect(panel.y + panel.height - workbookBounds.y - workbookBounds.height).toBeLessThan(30);
  await page.screenshot({ path: testInfo.outputPath("layout-tall-workbook.png") });
  await page.setViewportSize({ width: 2320, height: 1272 });
  await section.getByRole("button", { name: "权益表", exact: true }).click();
  const verifyAllColumns = async () => {
    const bounds = (await table.boundingBox())!;
    for (const label of ["权益内容", "条件", "期限", "原文链接"]) {
      const column = table.getByRole("columnheader", { name: label, exact: true });
      await expect(column).toBeVisible();
      const rect = (await column.boundingBox())!;
      expect(rect.x).toBeGreaterThanOrEqual(bounds.x);
      expect(rect.x + rect.width).toBeLessThanOrEqual(bounds.x + bounds.width);
    }
    expect(await table.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  };
  await verifyAllColumns();
  await page.screenshot({ path: testInfo.outputPath("layout-wide-all-columns.png") });
  await section.getByRole("button", { name: "展开预览", exact: true }).click();
  await expect(page.getByRole("textbox", { name: "任务要求", exact: true })).toBeHidden();
  await verifyAllColumns();
  await page.screenshot({ path: testInfo.outputPath("layout-expanded-all-columns.png") });
});

test("数量缺口与部分证据同页展示，不把待核对笔记计为完整入选", async ({ page }, testInfo) => {
  await setup(page);
  const candidate_counts = { found: 20, excluded: 16, review_required: 1, unfinished: 3, selected: 0, target_count: 10 };
  await page.route("**/api/conversations/preview-conv/messages", route => route.fulfill({ json: [{ id: 1, role: "assistant", content: "已保留部分证据，数量尚未满足", task_id: "collection-preview", meta: { files,
    work_progress: [{ node: "collect", label: "采集数据", status: "warning", summary: "已找到20篇，完整入选0/10篇", sequence: 1, candidate_counts }] } }] }));
  await page.route("**/api/downloads/collection-preview?*", route => route.fulfill({ json: { ...preview, candidate_counts,
    candidate_summary: { total_rows: 20, truncated: true, rows: [
      { index: 1, source_title: "合成待核对笔记", note_url: "https://example.com/review", status: "review_required", reason: "一张图片未识别完成" },
      { index: 2, source_title: "合成已排除笔记", note_url: "https://example.com/excluded", status: "excluded", reason: "适用客群与任务要求不符", reason_code: "audience_mismatch", explanation: "原文明确为普通客群权益", evidence: [{ source_id: "content", quote: `适用客群：普通客户。${"合成依据".repeat(45)}<script>不会执行</script>` }] },
      { index: 3, source_title: "合成未完成笔记", note_url: "javascript:alert(1)", status: "unprocessed", reason: "extract_outcome_unknown" },
      { index: 4, source_title: "合成过期规则笔记", note_url: "https://example.com/expired", status: "excluded", reason: "expired_rule", explanation: "原文活动期限已经结束", evidence: [{ source_id: "content", quote: "活动已结束，不再适用。" }] },
    ] }, benefit_table: { ...preview.benefit_table, rows: [{ ...preview.benefit_table.rows[0], source_status: "review_required", partial_evidence: true, review_reason: "一张图片未识别完成" }] } } }));
  const pageErrors: string[] = [];
  page.on("pageerror", error => pageErrors.push(error.message));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/data-prep?conversation=preview-conv");
  const section = page.getByRole("region", { name: "已采集资料预览", exact: true });
  const summary = section.getByLabel("采集数量", { exact: true });
  for (const text of ["已找到", "20", "已排除", "16", "待核对", "1", "未完成", "3", "完整入选", "0/10"]) await expect(summary).toContainText(text);
  await expect(page.getByRole("region", { name: "采集执行记录", exact: true }).getByLabel("采集数量", { exact: true })).toContainText("0/10");
  await expect(section).toContainText("待核对资料仅展示已有证据，不计入完整入选篇数");
  await expect(section.getByRole("table")).toContainText("部分证据 · 待核对");
  await expect(section.getByRole("table")).toContainText("一张图片未识别完成");
  await expect(section.getByRole("button", { name: "预览已采集资料", exact: true })).toHaveCount(0);
  const disclosure = section.locator("summary", { hasText: "候选笔记与筛选说明" });
  await disclosure.focus(); await page.keyboard.press("Enter");
  const candidates = section.getByLabel("候选笔记与筛选说明", { exact: true });
  await expect(candidates).toContainText("原文明确为普通客群权益");
  await expect(candidates).toContainText("适用客群与任务要求不符");
  await expect(candidates).toContainText("原文权益规则已过期");
  await expect(candidates).not.toContainText("audience_mismatch");
  await expect(candidates).not.toContainText("expired_rule");
  await expect(candidates).toContainText("权益提取结果尚未确认");
  await expect(candidates.getByRole("link", { name: "合成待核对笔记", exact: true })).toHaveAttribute("href", "https://example.com/review");
  await expect(candidates.getByRole("link", { name: "合成已排除笔记", exact: true })).toHaveAttribute("rel", "noopener noreferrer");
  await expect(candidates.getByRole("link", { name: "合成未完成笔记", exact: true })).toHaveCount(0);
  await expect(candidates).toContainText("仅展示前 4 篇");
  await candidates.locator("summary", { hasText: "查看判断依据" }).first().click();
  await expect(candidates.locator("blockquote").first()).toContainText("<script>不会执行</script>");
  await expect(candidates.locator("script")).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("collection-status-desktop.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  await section.scrollIntoViewIfNeeded();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(summary).toBeVisible();
  for (const file of files) await expect(section.getByRole("button", { name: `下载 ${file.name}`, exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("collection-status-mobile.png") });
  expect(pageErrors).toEqual([]);
});

for (const surface of ["历史任务", "即时对话"]) test(`采集中资料自动更新，保留展开和关闭选择，结束后恢复完整下载：${surface}`, async ({ page }, testInfo) => {
  await setup(page);
  let revision = 1;
  let running = true;
  let observedRevision = 0;
  const candidate_counts = { found: 20, excluded: 16, review_required: 1, unfinished: 3, selected: 0, target_count: 10 };
  await page.route("**/api/chat/running/preview-conv", route => {
    observedRevision = revision;
    return route.fulfill({ json: { running, progress: [{ node: "collect", label: "采集数据", status: "started", summary: "正在处理已找到的笔记", sequence: revision, candidate_counts,
      files: [{ name: "data-in-progress.json", url: `/api/downloads/collection-preview/data-in-progress.json?revision=${revision}` }] }] } });
  });
  await page.route("**/api/conversations/preview-conv/messages", route => route.fulfill({ json: [
    { id: 1, role: "user", content: "合成采集要求" },
    ...(running ? [] : [{ id: 2, role: "assistant", content: "合成采集结束", task_id: "collection-preview", created_at: "2026-10-12T10:00:00+08:00", meta: { files, token_usage: { input_tokens: 1, output_tokens: 1, total_tokens: 2 } } }]),
  ] }));
  let releaseSecond: () => void = () => {};
  const waiting = new Promise<void>(resolve => { releaseSecond = resolve; });
  let secondRequested = false;
  await page.route("**/api/downloads/collection-preview?*", async route => {
    const params = new URL(route.request().url()).searchParams;
    const requested = Number(params.get("revision"));
    if (requested === 2) { secondRequested = true; await waiting; }
    await route.fulfill({ json: { ...preview, candidate_counts, in_progress: requested > 0,
      benefit_table: { ...preview.benefit_table, rows: [{ ...preview.benefit_table.rows[0], benefit_name: requested ? `合成阶段权益${requested}` : "合成最终权益" }] } } }).catch(() => {});
  });
  if (surface === "即时对话") await page.addInitScript(() => localStorage.setItem("mangrove_workspace_draft_u1_new", JSON.stringify({ draft: { prompt: "合成采集要求", connectionId: null, connectionModel: null, localModel: "Synthetic", conversation: [], collection: { convId: "preview-conv", pending: true, prompt: "合成采集要求", steps: [] } } })));
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(surface === "历史任务" ? "/data-prep?conversation=preview-conv" : "/data-prep");
  const section = page.getByRole("region", { name: "已采集资料预览", exact: true });
  await expect(section.getByRole("heading", { name: "采集中资料", exact: true })).toBeVisible();
  await expect(section.getByRole("table")).toContainText("合成阶段权益1");
  await expect(section).toContainText("数量和证据继续更新");
  await section.getByRole("button", { name: "展开预览", exact: true }).click();
  revision = 2;
  await expect.poll(() => secondRequested).toBe(true);
  await expect(section.getByRole("table")).toContainText("合成阶段权益1");
  await expect(section).toContainText("正在更新已保存资料");
  await expect(section.getByRole("button", { name: "恢复分栏", exact: true })).toBeVisible();
  releaseSecond();
  await expect(section.getByRole("table")).toContainText("合成阶段权益2");
  await expect(page.getByRole("textbox", { name: "任务要求", exact: true })).toBeHidden();
  await section.getByRole("button", { name: "关闭资料预览", exact: true }).click();
  revision = 3;
  await expect.poll(() => observedRevision).toBe(3);
  await expect(page.getByRole("region", { name: "采集执行记录", exact: true }).getByLabel("采集数量", { exact: true })).toContainText("0/10");
  await expect(section).toHaveCount(0);
  await page.getByRole("button", { name: "查看资料", exact: true }).click();
  await expect(section.getByRole("table")).toContainText("合成阶段权益3");
  running = false;
  await expect(section.getByRole("heading", { name: "已采集资料", exact: true })).toBeVisible();
  await expect(section.getByRole("table")).toContainText("合成最终权益");
  for (const file of files) await expect(section.getByRole("button", { name: `下载 ${file.name}`, exact: true })).toBeVisible();
  await expect(section.getByRole("button", { name: "下载 data-in-progress.json", exact: true })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath("collection-live-final.png") });
});

for (const status of [404, 409, 503]) test(`历史采集恢复条件拒绝可见且重查不重发：${status}`, async ({ page }, testInfo) => {
  await setup(page);
  let queries = 0, resumes = 0;
  const detail = status === 409
    ? "原采集请求结果尚未确认，无法安全恢复或自动重发；诊断已保留，请联系管理员核对。"
    : status === 404 ? "不应展示其他账号的任务信息" : "恢复条件查询暂不可用";
  await page.route("**/api/chat/collections/collection-preview/recovery", route => {
    queries++;
    expect(route.request().method()).toBe("GET");
    return route.fulfill(queries === 1 ? { status, json: { detail } } : { json: {
      credential_source: "platform", reason: "challenge_required", next_action: "complete_platform_challenge",
      message: "本任务使用平台统一的小红书采集账号。请联系管理员完成来源验证。",
    } });
  });
  await page.route("**/api/chat/collections/collection-preview/resume", route => {
    resumes++;
    return route.fulfill({ status: 500, json: { detail: "重查资格不应恢复执行" } });
  });
  await page.goto("/data-prep?conversation=preview-conv");
  const problem = page.getByRole("alert", { name: "采集恢复条件", exact: true });
  await expect(problem).toBeVisible();
  await expect(problem).toContainText("已有资料仍保留");
  if (status === 404) {
    await expect(problem).toContainText("任务不存在或无权访问");
    await expect(problem).not.toContainText(detail);
  } else await expect(problem).toContainText(detail);
  await expect(page.getByRole("button", { name: "恢复原采集任务", exact: true })).toHaveCount(0);
  await page.screenshot({ path: testInfo.outputPath(`collection-recovery-query-${status}.png`) });
  const retry = problem.getByRole("button", { name: "重新查询恢复条件", exact: true });
  await retry.focus(); await page.keyboard.press("Enter");
  await expect(problem).toBeHidden();
  await expect(page.getByText("本任务使用平台统一的小红书采集账号。请联系管理员完成来源验证。", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "恢复原采集任务", exact: true })).toBeVisible();
  expect(queries).toBe(2); expect(resumes).toBe(0);
});

test("历史采集已无待恢复步骤时不显示恢复故障", async ({ page }) => {
  await setup(page);
  await page.route("**/api/chat/collections/collection-preview/recovery", route => route.fulfill({
    status: 409, json: { detail: "原任务没有可恢复的未完成步骤" },
  }));
  await page.goto("/data-prep?conversation=preview-conv");
  await expect(page.getByRole("region", { name: "已采集资料预览", exact: true })).toBeVisible();
  await expect(page.getByRole("alert", { name: "采集恢复条件", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "恢复原采集任务", exact: true })).toHaveCount(0);
});

for (const detail of ["没有可恢复的本人任务记录", "该任务不支持证据采集恢复"]) test(`普通分析完成后不显示采集恢复故障：${detail}`, async ({ page }, testInfo) => {
  await setup(page);
  await page.route("**/api/conversations/preview-conv/messages", route => route.fulfill({ json: [{
    id: 1, role: "assistant", content: "普通分析已完成", task_id: "plain-analysis",
    meta: { kind: "output", files: [{ name: "analysis.md", url: "/api/downloads/plain-analysis/analysis.md" }] },
  }] }));
  await page.route("**/api/chat/collections/plain-analysis/recovery", route => route.fulfill({ status: 409, json: { detail } }));
  const checked = page.waitForResponse(response => response.url().endsWith("/api/chat/collections/plain-analysis/recovery"));
  await page.goto("/data-prep?conversation=preview-conv");
  await expect(page.getByRole("article", { name: "智能体回复", exact: true })).toContainText("普通分析已完成");
  await (await checked).finished();
  const input = page.getByRole("textbox", { name: "任务要求", exact: true });
  await input.fill("继续分析这份结果");
  await page.screenshot({ path: testInfo.outputPath("plain-analysis-without-collection-recovery.png") });
  await expect(page.getByRole("alert", { name: "采集恢复条件", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "恢复原采集任务", exact: true })).toHaveCount(0);
  await expect(input).toHaveValue("继续分析这份结果");
});

for (const evidence of ["采集结果", "候选进度"]) test(`真实采集失败缺少恢复进度时保留拒绝说明：${evidence}`, async ({ page }) => {
  await setup(page);
  await page.route("**/api/conversations/preview-conv/messages", route => route.fulfill({ json: [{
    id: 1, role: "assistant", content: "来源获取失败，诊断已保留", task_id: "collection-preview",
    meta: { kind: "output", files, ...(evidence === "采集结果" ? { collection_outcome: { status: "failed" } } : {
      work_progress: [{ node: "collect", label: "采集数据", status: "failed", summary: "首批来源未完成", sequence: 1,
        candidate_counts: { found: 0, excluded: 0, review_required: 0, unfinished: 0, selected: 0, target_count: 5 } }],
    }) },
  }] }));
  await page.route("**/api/chat/collections/collection-preview/recovery", route => route.fulfill({
    status: 409, json: { detail: "没有可恢复的本人任务记录" },
  }));
  await page.goto("/data-prep?conversation=preview-conv");
  await expect(page.getByRole("alert", { name: "采集恢复条件", exact: true })).toContainText("没有可恢复的本人任务记录");
  await expect(page.getByRole("button", { name: "恢复原采集任务", exact: true })).toHaveCount(0);
});

test("历史采集 SSE 中断后使用轮询的新快照和数量，不冻结旧进度", async ({ page }) => {
  await setup(page);
  let running = false, revision = 2;
  const progress = (version: number) => ({ node: "collect", label: "采集数据", status: "started", sequence: version, summary: "正在处理笔记",
    candidate_counts: { found: 20, excluded: 16, review_required: 1, unfinished: 3 - version + 1, selected: version - 1, target_count: 10 },
    files: [{ name: "data-in-progress.json", url: `/api/downloads/collection-preview/data-in-progress.json?revision=${version}` }] });
  await page.route("**/api/chat/running/preview-conv", route => route.fulfill({ json: { running, progress: running ? [progress(revision)] : [] } }));
  await page.route("**/api/chat/collections/collection-preview/recovery", route => route.fulfill({ json: { message: "继续原合成任务" } }));
  await page.route("**/api/chat/collections/collection-preview/resume", route => {
    running = true;
    return route.fulfill({ contentType: "text/event-stream", body: `event: meta\ndata: {"conv_id":"preview-conv"}\n\nevent: progress\ndata: ${JSON.stringify(progress(1))}\n\nevent: error\ndata: {"message":"实时连接中断，继续查询进度","code":"stream_interrupted"}\n\n` });
  });
  await page.route("**/api/downloads/collection-preview?*", route => {
    const version = Number(new URL(route.request().url()).searchParams.get("revision"));
    return route.fulfill({ json: { ...preview, in_progress: version > 0, candidate_counts: progress(version || 1).candidate_counts,
      benefit_table: { ...preview.benefit_table, rows: [{ ...preview.benefit_table.rows[0], benefit_name: `轮询权益${version}` }] } } });
  });
  await page.goto("/data-prep?conversation=preview-conv");
  await page.getByRole("button", { name: "恢复原采集任务", exact: true }).click();
  const section = page.getByRole("region", { name: "已采集资料预览", exact: true });
  await expect(section.getByRole("table")).toContainText("轮询权益2");
  await expect(section.getByLabel("采集数量", { exact: true })).toContainText("1/10");
  revision = 3;
  await expect(section.getByRole("table")).toContainText("轮询权益3");
  await expect(section.getByLabel("采集数量", { exact: true })).toContainText("2/10");
  await expect(page.getByRole("region", { name: "采集执行记录", exact: true }).last().getByLabel("采集数量", { exact: true })).toContainText("2/10");
});

for (const surface of ["历史任务", "即时对话"]) for (const outcome of ["模型调用失败", "用户停止"]) test(`当前部分资料在终态保留，不退回上一任务资料：${surface} · ${outcome}`, async ({ page }) => {
  await setup(page);
  const liveFiles = [{ name: "data-in-progress.json", url: "/api/downloads/collection-preview/data-in-progress.json?revision=7" }];
  const work_progress = [{ node: "collect", label: "采集数据", status: "warning", summary: outcome, sequence: 7, files: liveFiles }];
  const savedMessages = [{ id: 1, role: "assistant", content: "上一任务回复", meta: { files } }, { id: 2, role: "user", content: "当前任务要求" }, { id: 3, role: "assistant", content: `${outcome}，已保存部分证据`, meta: { work_progress, files: [] } }];
  await page.route("**/api/conversations/preview-conv/messages", route => route.fulfill({ json: savedMessages }));
  await page.route("**/api/downloads/collection-preview?*", route => {
    const partial = new URL(route.request().url()).searchParams.get("preview") === "data-in-progress.json";
    return route.fulfill({ json: { ...preview, in_progress: partial, benefit_table: { ...preview.benefit_table, rows: [{ ...preview.benefit_table.rows[0], benefit_name: partial ? "当前任务已保存权益" : "上一任务旧权益" }] } } });
  });
  if (surface === "即时对话") await page.addInitScript(({ work_progress, files, outcome }) => localStorage.setItem("mangrove_workspace_draft_u1_new", JSON.stringify({ draft: { prompt: "", connectionId: null, connectionModel: null, localModel: "Synthetic", sessionId: "preview-conv", collection: { convId: "preview-conv", pending: false, prompt: "当前任务要求", steps: [], progress: work_progress }, conversation: [{ role: "assistant", content: "上一任务回复", files }, { role: "user", content: "当前任务要求" }, { role: "assistant", content: `${outcome}，已保存部分证据`, files: [], work_progress }] } })), { work_progress, files, outcome });
  await page.goto(surface === "历史任务" ? "/data-prep?conversation=preview-conv" : "/data-prep");
  const section = page.getByRole("region", { name: "已采集资料预览", exact: true });
  await expect(section.getByRole("table")).toContainText("当前任务已保存权益");
  await expect(section.getByRole("heading", { name: "已采集资料", exact: true })).toBeVisible();
  await expect(section).not.toContainText("数量和证据继续更新");
  await expect(section).not.toContainText("上一任务旧权益");
  await expect(section.getByRole("button", { name: "下载 data-in-progress.json", exact: true })).toBeVisible();
  await page.reload();
  await expect(section.getByRole("table")).toContainText("当前任务已保存权益");
});
