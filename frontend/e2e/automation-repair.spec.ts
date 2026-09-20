import { expect, test } from '@playwright/test';
test.use({ timezoneId: 'America/Los_Angeles' });

test('模型显式选择与历史时区阻断', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  let submitted: Record<string, unknown> | null = null;
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/me') return route.fulfill({ json: { user_id: 'synthetic', username: '测试', role: 'admin' } });
    if (path === '/api/tasks') return route.fulfill({ json: [{ task_id: 'old', name: '历史合成计划', user_input: '合成任务', status: 'active', trigger_type: 'cron', cron_expr: '0 9 * * *', run_count: 1, next_run_at: '2026-09-17T09:00:00', time_zone: null, execution_state: 'blocked', can_recreate: true, blocked_reason: '历史时区未记录，请核对北京时间后重新创建', provider: 'local', model: 'synthetic' }] });
    if (path === '/api/tasks/templates') return route.fulfill({ json: [] });
    if (path === '/api/models') return route.fulfill({ json: { options: [{ provider: 'local', model: 'synthetic' }] } });
    if (path === '/api/model-connections') return route.fulfill({ json: { items: [] } });
    if (path === '/api/model-connections/preferences/default') return route.fulfill({ json: { preference: null } });
    if (path === '/api/tasks/manual') { submitted = route.request().postDataJSON(); return route.fulfill({ json: { ok: true, task_id: 'new' } }); }
    return route.fulfill({ json: {} });
  });
  await page.goto('/tasks');
  await expect(page).toHaveURL(/\/tasks$/);
  await expect(page.getByRole('button', { name: '立即执行', exact: true })).toBeDisabled();
  await expect(page.getByText('历史时区未记录，请核对北京时间后重新创建')).toBeVisible();
  await page.getByRole('button', { name: '按北京时间重新创建' }).click();
  await expect(page.getByLabel('执行模型')).toBeVisible();
  await expect(page.getByLabel('执行模型')).toHaveValue(JSON.stringify(['__local__', 'synthetic']));
  await page.getByLabel('执行模型').selectOption(JSON.stringify(['__local__', 'synthetic']));
  await page.screenshot({ path: testInfo.outputPath('automation-model-desktop.png') });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByLabel('执行模型')).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('automation-model-mobile.png') });
  expect(submitted).toBeNull();
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect.poll(() => submitted?.model).toBe('synthetic');
  expect(submitted?.model_connection_id).toBe('__local__');
  expect(errors).toEqual([]);
});
