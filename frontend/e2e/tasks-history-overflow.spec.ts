import { expect, test } from '@playwright/test';

test('执行历史长错误换行且不撑宽弹窗', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const summary = '任务描述需澄清：请明确指定平台和关键词。'.repeat(12) + '\nhttps://example.invalid/' + 'long-path'.repeat(60);
  let historyRequests = 0;
  let releaseRefresh: (() => void) | undefined;
  await page.route('**/api/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/me') return route.fulfill({ json: { user_id: 'test', username: 'test', role: 'admin' } });
    if (path === '/api/tasks') return route.fulfill({ json: [{ task_id: 'test', name: '合成历史任务', user_input: '模拟需求', status: 'paused', trigger_type: 'cron', cron_expr: '0 9 * * *', run_count: 1 }] });
    if (path === '/api/tasks/templates') return route.fulfill({ json: [] });
    if (path === '/api/tasks/runs/recent') {
      historyRequests++;
      if (historyRequests === 2) await new Promise<void>(resolve => { releaseRefresh = resolve; });
      return route.fulfill({ json: { total: 1, items: [{ task_id: 'test', run_id: 1, run_at: '2026-09-18T09:00:00+08:00', success: false, summary, has_report: true, has_json: true }] } });
    }
    return route.fulfill({ json: {} });
  });
  await page.goto('/tasks');
  await page.getByRole('button', { name: '历史', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText(summary, { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 390, height: 900 });
  const scroll = dialog.locator('div.overflow-y-auto').first();
  await scroll.evaluate(el => { el.scrollTop = 180; });
  const position = await scroll.evaluate(el => el.scrollTop);
  expect(position).toBeGreaterThan(0);
  await expect.poll(() => historyRequests, { timeout: 12000 }).toBeGreaterThanOrEqual(2);
  await expect(dialog.getByText('加载中…', { exact: true })).toHaveCount(0);
  expect(await scroll.evaluate(el => el.scrollTop)).toBe(position);
  const refreshed = page.waitForResponse(response => response.url().includes('/api/tasks/runs/recent'));
  releaseRefresh!();
  await refreshed;
  await expect.poll(() => scroll.evaluate(el => el.scrollTop)).toBe(position);
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 900 });
    await expect.poll(() => dialog.evaluate(el => el.scrollWidth <= el.clientWidth + 1 && Array.from(el.querySelectorAll('div,p')).every(node => node.scrollWidth <= node.clientWidth + 1))).toBe(true);
    await expect(dialog.getByRole('button', { name: '查看', exact: true })).toBeVisible();
    await dialog.getByRole('navigation', { name: '执行历史分页' }).scrollIntoViewIfNeeded();
    await expect(dialog.getByLabel('每页条数')).toHaveValue('10');
    await page.screenshot({ path: testInfo.outputPath(`history-${width}.png`) });
  }
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(errors).toEqual([]);
});
