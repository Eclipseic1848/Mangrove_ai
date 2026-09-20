import { expect, test, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

async function setup(page: Page, rating = 'down') {
  let note = '旧处理备注';
  let status = 'pending';
  const queries: string[] = [];
  const patches: unknown[] = [];
  await page.route('**/api/**', async route => {
    const req = route.request(), url = new URL(req.url());
    if (url.pathname === '/api/auth/me') return route.fulfill({ json: { user_id: 'ux-admin', username: 'ux-admin', display_name: '测试管理员', role: 'admin' } });
    if (url.pathname.endsWith('/overview')) return route.fulfill({ json: { total_tasks: 100, total_up: 30, total_down: 5, total_pending: 4, down_rate: .05, reason_counts: { '其他': 5 }, daily: [] } });
    if (url.pathname.endsWith('/list')) {
      queries.push(req.url());
      const total = url.searchParams.get('q') === '无匹配' ? 0 : 35;
      const offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit') || 10);
      return route.fulfill({ json: { total, items: Array.from({ length: Math.min(limit, total - offset) }, (_, i) => ({ id: offset + i + 1, user_id: 'test-user', username: 'test-user', display_name: '测试用户', rating, reasons: rating === 'down' ? ['其他'] : [], status, created_at: '2026-09-20T02:00:00Z', content_available: true, has_comment: true, has_admin_note: true })) } });
    }
    if (url.pathname.endsWith('/audit-content')) return route.fulfill({ json: { event_id: 'ux-audit', content: { task_title: '核对订单', original_task: '请逐行核对订单金额', question: '请逐行核对订单金额', answer: '## 核对结果\n\n|订单|金额|\n|---|---|\n|A001|100|', comment: '缺少第二份表的明细', admin_note: note }, truncated: false, content_bytes: 200 } });
    if (req.method() === 'PATCH') { const body = req.postDataJSON(); patches.push(body); if ('admin_note' in body) note = body.admin_note || ''; if (body.status) status = body.status; return route.fulfill({ json: { ok: true } }); }
    return route.fulfill({ status: 404, json: { detail: '隔离接口' } });
  });
  await page.goto('/feedback');
  await expect(page.getByRole('button', { name: '查看反馈详情', exact: true }).first()).toBeVisible();
  return { queries, patches };
}

async function openDetail(page: Page) {
  await page.getByRole('button', { name: '查看反馈详情', exact: true }).first().click();
  await page.getByLabel('查看原因', { exact: true }).fill('核对用户反馈的任务内容');
  await page.getByRole('button', { name: '提交审计并查看', exact: true }).click();
  await expect(page.getByLabel(/^处理备注/)).toHaveValue('旧处理备注');
}

test('点赞可查看原始反馈但无需提交处理结论', async ({ page }) => {
  await setup(page, 'up');
  await expect(page.getByText('尚无处理备注', { exact: false })).toHaveCount(0);
  await openDetail(page);
  await expect(page.getByRole('button', { name: '提交处理结论', exact: true })).toHaveCount(0);
  await expect(page.getByLabel('处理结果', { exact: true })).toHaveCount(0);
  await expect(page.getByText('请逐行核对订单金额', { exact: true }).first()).toBeVisible();
});

test('列表不能直接处理，详情需结论和处理结果', async ({ page }) => {
  const { patches } = await setup(page);
  await expect(page.getByRole('button', { name: '已处理', exact: true })).toHaveCount(0);
  await expect(page.getByText('待处理', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('button', { name: '标记已处理', exact: true })).toHaveCount(0);
  await expect(page.getByRole('button', { name: '忽略', exact: true })).toHaveCount(0);
  await openDetail(page);
  await expect(page.getByRole('button', { name: '提交处理结论', exact: true })).toBeDisabled();
  await page.getByLabel('处理结果', { exact: true }).selectOption('no_change');
  await page.getByLabel('处理备注', { exact: true }).fill(' ');
  await expect(page.getByRole('button', { name: '提交处理结论', exact: true })).toBeDisabled();
  await page.getByLabel('处理备注', { exact: true }).fill('已核对原文，结果符合用户要求');
  await page.getByRole('button', { name: '提交处理结论', exact: true }).click();
  await expect.poll(() => patches).toEqual([{ status: 'no_change', admin_note: '已核对原文，结果符合用户要求' }]);
  await expect(page.getByRole('dialog').locator('p').filter({ hasText: /^无需修改$/ })).toBeVisible();
});

test('分页支持四档条数，搜索防抖回首页，无效日期保留列表且不请求', async ({ page }) => {
  const state = await setup(page);
  const size = page.getByRole('combobox', { name: '每页条数' });
  await expect(size).toHaveValue('10');
  for (const value of ['20', '50', '100', '10']) {
    await size.selectOption(value);
    await expect.poll(() => new URL(state.queries.at(-1)!).searchParams.get('limit')).toBe(value);
  }
  await page.getByRole('button', { name: '下一页' }).click();
  await expect.poll(() => new URL(state.queries.at(-1)!).searchParams.get('offset')).toBe('10');
  const before = state.queries.length;
  await page.getByLabel('用户名、昵称或用户ID').pressSequentially('tester', { delay: 20 });
  await expect.poll(() => state.queries.length).toBe(before + 1);
  expect(new URL(state.queries.at(-1)!).searchParams.get('offset')).toBe('0');
  await page.getByLabel('开始日期').fill('2026-09-20');
  await expect.poll(() => new URL(state.queries.at(-1)!).searchParams.get('date_from')).toBe('2026-09-20');
  const valid = state.queries.length;
  await page.getByLabel('结束日期').fill('2026-09-19');
  await expect(page.getByRole('alert')).toContainText('结束日期不能早于开始日期');
  expect(state.queries.length).toBe(valid);
  await expect(page.getByRole('button', { name: '查看反馈详情', exact: true })).toHaveCount(10);
});

test('未保存备注关闭前确认，取消保留，保存后留在详情', async ({ page }) => {
  const state = await setup(page);
  await openDetail(page);
  await page.getByLabel(/^处理备注/).fill('新的核查结论');
  await page.keyboard.press('Escape');
  await expect(page.getByRole('alertdialog')).toBeVisible();
  await page.getByRole('alertdialog').getByRole('button', { name: '取消', exact: true }).click();
  await expect(page.getByLabel('处理备注', { exact: true })).toHaveValue('新的核查结论');
  await page.getByRole('button', { name: '保存备注', exact: true }).click();
  await expect(page.getByRole('dialog', { name: '反馈详情', exact: true })).toBeVisible();
  expect(state.patches).toEqual([{ admin_note: '新的核查结论' }]);
});

test('详情内保存备注并完成处理是一次提交，成功后可继续阅读', async ({ page }) => {
  const state = await setup(page);
  await openDetail(page);
  await page.getByLabel('处理备注', { exact: true }).fill('已核对并定位问题');
  await page.getByLabel('处理结果', { exact: true }).selectOption('fixed');
  await page.getByRole('button', { name: '提交处理结论', exact: true }).click();
  await expect(page.getByRole('dialog').locator('p').filter({ hasText: /^已修复$/ })).toBeVisible();
  expect(state.patches).toEqual([{ admin_note: '已核对并定位问题', status: 'fixed' }]);
  await expect(page.getByRole('button', { name: '提交处理结论', exact: true })).toBeDisabled();
  await page.getByLabel('处理备注', { exact: true }).fill('');
  await expect(page.getByRole('button', { name: '保存备注', exact: true })).toBeDisabled();
});

test('任务总数与点踩口径明确，首屏可处理反馈，详情可直接阅读表格', async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await setup(page);
  await expect(page.getByText('平台总任务数', { exact: true })).toBeVisible();
  await expect(page.getByText('点踩总量 ÷ 总任务数', { exact: true })).toHaveCount(0);
  await expect(page.getByText('反馈总数', { exact: true })).toHaveCount(0);
  await expect(page.getByText('5.0%', { exact: true })).toBeVisible();
  expect((await page.getByRole('button', { name: '查看反馈详情', exact: true }).first().boundingBox())!.y).toBeLessThan(500);
  await page.screenshot({ path: testInfo.outputPath('feedback-list.png') });
  await openDetail(page);
  await expect(page.getByRole('table')).toContainText('A001');
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 844 });
    const dialog = page.getByRole('dialog', { name: '反馈详情' });
    expect(await dialog.evaluate(el => el.scrollWidth <= el.clientWidth)).toBe(true);
    const close = page.getByRole('button', { name: '关闭', exact: true });
    expect((await close.boundingBox())!.y).toBeLessThan(844);
    await page.screenshot({ path: testInfo.outputPath(`feedback-detail-${width}.png`) });
  }
});

test('原任务预览进入可见区域，失败重试保留文本分页位置', async ({ page }) => {
  await setup(page);
  let fail = true;
  const offsets: number[] = [];
  await page.route('**/api/feedback/*/task-context', route => {
    const body = route.request().postDataJSON();
    if (body.action === 'message') {
      offsets.push(body.offset || 0);
      if (body.offset === 16000 && fail) { fail = false; return route.fulfill({ status: 503, json: { detail: '模拟读取失败' } }); }
      return route.fulfill({ json: { text: body.offset ? '第二段原文' : '第一段原文 <字段> **字面内容**', offset: body.offset || 0, total: 20000 } });
    }
    return route.fulfill({ json: { total: 10, files: [], messages: Array.from({ length: 10 }, (_, i) => ({ id: String(i), role: 'user', created_at: '2026-09-20T10:00:00+08:00' })) } });
  });
  await openDetail(page);
  await page.getByRole('button', { name: '查看原任务', exact: true }).click();
  await page.getByRole('button', { name: '查看内容', exact: true }).last().click();
  await expect(page.getByRole('heading', { name: '用户原始内容', exact: true })).toBeFocused();
  const original = page.getByText('第一段原文 <字段> **字面内容**', { exact: true });
  await expect(original).toBeVisible();
  expect((await original.boundingBox())!.y).toBeLessThan(700);
  await page.getByRole('button', { name: '下一段' }).click();
  await page.getByRole('button', { name: '重试', exact: true }).click();
  await expect(page.getByText('第二段原文', { exact: true })).toBeVisible();
  expect(offsets).toEqual([0, 16000, 16000]);
});

test('浏览器返回前保护未保存备注，取消保留，确认后离开', async ({ page }) => {
  await setup(page);
  await page.getByRole('link', { name: '概览', exact: true }).click();
  await page.getByRole('link', { name: '反馈管理', exact: true }).click();
  await openDetail(page);
  await page.getByLabel('处理备注', { exact: true }).fill('不能丢失的核对记录');
  await page.goBack();
  await expect(page.getByRole('alertdialog')).toBeVisible();
  await page.getByRole('alertdialog').getByRole('button', { name: '取消', exact: true }).click();
  await expect(page.getByLabel('处理备注', { exact: true })).toHaveValue('不能丢失的核对记录');
  await page.goBack();
  await page.getByRole('button', { name: '放弃修改', exact: true }).click();
  await expect(page).not.toHaveURL(/\/feedback$/);
});

test('深色列表与详情无严重可访问性问题，窄屏保留全部操作', async ({ page }, testInfo) => {
  await setup(page);
  await page.getByRole('button', { name: '深色主题', exact: true }).click();
  for (const detail of [false, true]) {
    if (detail) await openDetail(page);
    const result = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa']).analyze();
    expect(result.violations).toEqual([]);
    await page.setViewportSize({ width: 390, height: 700 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.screenshot({ path: testInfo.outputPath(detail ? 'detail-dark-mobile.png' : 'list-dark-mobile.png') });
  }
});

test('删除失败保留确认和错误，不自动重试', async ({ page }) => {
  await setup(page);
  let deletes = 0;
  await page.route('**/api/feedback/1', route => { deletes++; return route.fulfill({ status: 503, json: {} }); });
  await page.getByTitle('删除', { exact: true }).first().click();
  await page.getByRole('alertdialog').getByRole('button', { name: '删除', exact: true }).click();
  await expect(page.getByRole('alertdialog').getByRole('alert')).toContainText('删除结果未确认');
  expect(deletes).toBe(1);
  await page.getByRole('alertdialog').getByRole('button', { name: '取消' }).click();
  await expect(page.getByRole('button', { name: '查看反馈详情', exact: true })).toHaveCount(10);
});
