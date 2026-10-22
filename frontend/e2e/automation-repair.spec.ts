import { expect, test, type Page } from '@playwright/test';
import { chooseModel } from './model-picker';
test.use({ timezoneId: 'America/Los_Angeles' });

test('模型显式选择与历史时区阻断', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  let submitted: Record<string, unknown> | null = null;
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/me') return route.fulfill({ json: { user_id: 'synthetic', username: '测试', role: 'admin' } });
    if (path === '/api/tasks') return route.fulfill({ json: [{ task_id: 'old', name: '历史合成计划', user_input: '合成任务', status: 'active', trigger_type: 'cron', cron_expr: '0 9 * * *', run_count: 1, next_run_at: '2026-09-17T09:00:00', time_zone: null, execution_state: 'blocked', can_recreate: true, blocked_reason: '历史时区未记录，请核对执行时间后重新创建', provider: 'local', model: 'synthetic' }] });
    if (path === '/api/tasks/templates') return route.fulfill({ json: [] });
    if (path === '/api/tasks/scheduler/status') return route.fulfill({ json: { enabled: true, running: true } });
    if (path === '/api/models') return route.fulfill({ json: { options: [{ provider: 'local', model: 'synthetic' }] } });
    if (path === '/api/model-connections') return route.fulfill({ json: { items: [] } });
    if (path === '/api/model-connections/preferences/default') return route.fulfill({ json: { preference: null } });
    if (path === '/api/tasks/manual') { submitted = route.request().postDataJSON(); return route.fulfill({ json: { ok: true, task_id: 'new' } }); }
    return route.fulfill({ json: {} });
  });
  await page.goto('/tasks');
  await expect(page).toHaveURL(/\/tasks$/);
  await expect(page.getByRole('button', { name: '立即执行', exact: true })).toBeDisabled();
  await expect(page.getByText('历史时区未记录，请核对执行时间后重新创建')).toBeVisible();
  await page.getByRole('button', { name: '重新创建计划' }).click();
  await expect(page.getByLabel('执行模型')).toBeVisible();
  await expect(page.getByLabel('执行模型')).toHaveAttribute('value', JSON.stringify(['__local__', 'synthetic']));
  await chooseModel(page.getByLabel('执行模型'), JSON.stringify(['__local__', 'synthetic']));
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

test('自动化模型按来源区分同名连接，保存所选身份', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem('onboarding_all_["synthetic","user"]', 'skipped'));
  let submitted: Record<string, unknown> | null = null;
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/me') return route.fulfill({ json: { user_id: 'synthetic', username: '测试', role: 'user' } });
    if (path === '/api/tasks' || path === '/api/tasks/templates') return route.fulfill({ json: [] });
    if (path === '/api/tasks/scheduler/status') return route.fulfill({ json: { enabled: true, running: true } });
    if (path === '/api/models') return route.fulfill({ json: { options: [{ provider: 'local', model: '管理员本地模型' }] } });
    if (path === '/api/model-connections') return route.fulfill({ json: { items: [
      { connection_id: 'shared', owner_scope: 'platform_shared', preset_id: 'deepseek', locality: 'public_external', display_name: '平台连接', status: 'verified', model: 'same-model',
        models: [{ model_id: 'same-model', display_name: '同名型号', status: 'available', enabled: true }, { model_id: 'disabled', display_name: '已停用型号', status: 'available', enabled: false }] },
      { connection_id: 'personal', owner_scope: 'user_personal', preset_id: 'deepseek', locality: 'public_external', display_name: '个人连接', status: 'verified', model: 'same-model',
        models: [{ model_id: 'same-model', display_name: '同名型号', status: 'available', enabled: true }] },
    ] } });
    if (path === '/api/model-connections/preferences/default') return route.fulfill({ json: { preference: null } });
    if (path === '/api/tasks/manual') { submitted = route.request().postDataJSON(); return route.fulfill({ json: { ok: true, task_id: 'new' } }); }
    return route.fulfill({ json: {} });
  });
  await page.goto('/tasks');
  await page.getByRole('button', { name: '添加自动化', exact: true }).first().click();
  await page.getByRole('button', { name: /空白创建/ }).click();
  const picker = page.getByLabel('执行模型', { exact: true });
  await picker.click();
  const menu = page.getByRole('listbox');
  await expect(menu).toBeVisible();
  await expect(menu.getByRole('group', { name: '平台提供 · 云端模型' })).toBeVisible();
  await expect(menu.getByRole('group', { name: '我的连接 · 云端模型' })).toBeVisible();
  await expect(menu.locator('[data-model-label]')).toHaveText(['DeepSeek · 同名型号', 'DeepSeek · 同名型号']);
  const tags = menu.getByTestId('model-option-source');
  await expect(tags).toHaveText(['平台', '我的']);
  expect(await tags.nth(0).evaluate(node => getComputedStyle(node).backgroundColor)).not.toBe(await tags.nth(1).evaluate(node => getComputedStyle(node).backgroundColor));
  await chooseModel(picker, JSON.stringify(['shared', 'same-model']));
  await expect(page.getByTestId('model-picker-source')).toHaveText('平台');
  const consent = page.getByRole('checkbox', { name: '允许本计划将任务内容及所需资料交给所选云端模型处理。' });
  await consent.check();
  await picker.press('ArrowDown');
  await picker.press('End');
  await picker.press('Enter');
  await expect(picker).toHaveAttribute('value', JSON.stringify(['personal', 'same-model']));
  await expect(page.getByTestId('model-picker-source')).toHaveText('我的');
  await picker.click();
  await picker.press('Escape');
  await expect(menu).not.toBeVisible();
  await expect(page.getByRole('dialog', { name: '添加自动化任务' })).toBeVisible();
  await expect(consent).not.toBeChecked();
  await page.getByLabel('名称', { exact: true }).fill('合成来源验证');
  await page.getByLabel('提示词', { exact: true }).fill('合成任务');
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect(page.getByText('请选择可用模型，并确认云端模型的数据处理范围', { exact: true })).toBeVisible();
  expect(submitted).toBeNull();
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(picker).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await picker.click();
  await expect(tags).toHaveText(['平台', '我的']);
  await page.screenshot({ path: testInfo.outputPath('automation-model-source-mobile.png') });
  await picker.press('Tab');
  await expect(menu).not.toBeVisible();
  await expect(consent).toBeFocused();
  await consent.check();
  await page.getByRole('button', { name: '保存', exact: true }).click();
  await expect.poll(() => submitted).toMatchObject({ model_connection_id: 'personal', model: 'same-model', external_api_confirmed: true });
  expect(errors).toEqual([]);
});

async function mockAutomation(page: Page, schedulerStatus: Record<string, unknown> = { enabled: true, running: true }) {
  const task = (id: string, name: string) => ({
    task_id: id, name, user_input: '合成需求', status: 'active', trigger_type: 'cron',
    cron_expr: '30 9 * * 1,3,5', run_count: 23, next_run_at: '2099-10-07T09:30:00',
    time_zone: 'Asia/Shanghai', execution_state: 'idle', provider: 'local', model: 'synthetic',
    model_connection_id: '__local__', source: 'manual',
  });
  const run = (id: number, taskId = 'existing') => ({
    task_id: taskId, task_name: '合成计划', run_id: id, run_at: '2026-10-06T09:30:00+08:00',
    success: id % 2 === 1, state: id % 2 === 1 ? 'succeeded' : 'failed',
    summary: `合成记录${id}`, has_report: true, has_json: true,
  });
  const state = {
    tasks: [task('existing', '合成计划')], runs: Array.from({ length: 23 }, (_, index) => run(index + 1)),
    templates: [{ id: 'audit-template', name: '审计模板', description: '合成模板', prompt: '合成模板需求', trigger_type: 'cron', cron_expr: '30 9 * * 1,3,5' }],
    requests: [] as Array<{ method: string; path: string; query: URLSearchParams; body: any }>,
    schedulerStatus, statusFailure: false, tasksFailure: false, runsFailure: false, holdNextRuns: false,
    releaseRuns: undefined as (() => void) | undefined,
  };
  await page.addInitScript(() => localStorage.setItem('onboarding_all_["synthetic","admin"]', 'skipped'));
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    const method = request.method(), body = request.postData() ? request.postDataJSON() : null;
    state.requests.push({ method, path, query: url.searchParams, body });
    if (path === '/api/auth/me') return route.fulfill({ json: { user_id: 'synthetic', username: '合成账号', role: 'admin' } });
    if (path === '/api/tasks/scheduler/status') return route.fulfill(state.statusFailure
      ? { status: 503, json: { detail: '合成状态读取失败' } } : { json: state.schedulerStatus });
    if (path === '/api/tasks') return route.fulfill(state.tasksFailure
      ? { status: 503, json: { detail: '合成任务列表读取失败' } } : { json: state.tasks });
    if (path === '/api/tasks/templates') return route.fulfill({ json: state.templates });
    if (path === '/api/models') return route.fulfill({ json: { options: [{ provider: 'local', model: 'synthetic' }] } });
    if (path === '/api/model-connections') return route.fulfill({ json: { items: [] } });
    if (path === '/api/model-connections/preferences/default') return route.fulfill({ json: { preference: null } });
    if (path === '/api/tasks/runs/recent') {
      if (state.holdNextRuns) {
        state.holdNextRuns = false;
        await new Promise<void>(resolve => { state.releaseRuns = resolve; });
      }
      if (state.runsFailure) return route.fulfill({ status: 503, json: { detail: '合成记录读取失败' } });
      const filtered = state.runs.filter(item =>
        (!url.searchParams.get('task_id') || item.task_id === url.searchParams.get('task_id')) &&
        (!url.searchParams.has('success') || item.success === (url.searchParams.get('success') === 'true')) &&
        (!url.searchParams.get('q') || item.summary.includes(url.searchParams.get('q')!)));
      const offset = Number(url.searchParams.get('offset') || 0), limit = Number(url.searchParams.get('limit') || 10);
      return route.fulfill({ json: { items: filtered.slice(offset, offset + limit), total: filtered.length } });
    }
    if (/\/runs\/\d+\/report$/.test(path)) return route.fulfill({ json: { content: '# 合成报告\n\n验证结果：20。', run_at: '2026-10-06T09:30:00+08:00', success: true } });
    if (/\/runs\/\d+\/download$/.test(path)) return route.fulfill({
      contentType: url.searchParams.get('kind') === 'json' ? 'application/json' : 'text/markdown',
      body: url.searchParams.get('kind') === 'json' ? '{"synthetic":true}' : '# 合成报告\n验证结果：20。',
    });
    if (path === '/api/tasks/manual' && method === 'POST') {
      const created = { ...task(`created-${state.tasks.length}`, body.name), user_input: body.prompt,
        ...body.trigger, trigger_type: body.trigger.type, run_count: 0, source: body.template_id ? 'template' : 'manual' };
      state.tasks.push(created);
      return route.fulfill({ json: { ok: true, task_id: created.task_id } });
    }
    const item = state.tasks.find(entry => path.startsWith(`/api/tasks/${entry.task_id}`));
    if (item && method === 'PATCH') {
      Object.assign(item, body.status ? { status: body.status, execution_state: body.status === 'paused' ? 'paused' : 'idle' }
        : { name: body.name, user_input: body.prompt, ...body.trigger, trigger_type: body.trigger.type });
      return route.fulfill({ json: { ok: true } });
    }
    if (item && path.endsWith('/run_now')) {
      item.run_count++;
      state.runs.unshift({ ...run(25, item.task_id), task_name: item.name });
      return route.fulfill({ json: { ok: true, started: true, run_id: 25, state: 'running' } });
    }
    if (item && method === 'DELETE') {
      state.tasks = state.tasks.filter(entry => entry.task_id !== item.task_id);
      return route.fulfill({ json: { ok: true } });
    }
    return route.fulfill({ json: {} });
  });
  return state;
}

for (const [cronExpr, expectedCron, mode, time] of [
  ['0 9 * * 1-5', '0 9 * * 1-5', '高级', ''],
  ['0 9 1,15 * *', '0 9 1,15 * *', '高级', ''],
  ['*/15 9 * * *', '*/15 9 * * *', '高级', ''],
  ['0 9-17 * * *', '0 9-17 * * *', '高级', ''],
  ['0 9 * 1 *', '0 9 * 1 *', '高级', ''],
  ['0 9 1 * 1', '0 9 1 * 1', '高级', ''],
  ['0 9 * * 7', '0 9 * * 7', '高级', ''],
  ['0 9 * * 1,', '0 9 * * 1,', '高级', ''],
  ['0 0 * * *', '0 0 * * *', '每天', '00:00'],
  ['0 9 * * 0,1,3', '0 9 * * 0,1,3', '每周', '09:00'],
  ['0 9 31 * *', '0 9 31 * *', '每月', '09:00'],
  ['05 09 * * *', '5 9 * * *', '每天', '09:05'],
]) {
  const title = mode === '高级' ? '已有复杂 cron 仅改名称保存并重开仍保留原表达式' : '已有简单 cron 仅改名称保存并重开保持原计划';
  test(`${title}：${cronExpr}`, async ({ page }, testInfo) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    const state = await mockAutomation(page);
    state.tasks[0].cron_expr = cronExpr;
    await page.goto('/tasks');
    await page.getByRole('button', { name: '编辑', exact: true }).click();
    let dialog = page.getByRole('dialog', { name: '编辑自动化任务' });
    await expect(dialog.getByLabel('执行模型', { exact: true })).toHaveAttribute('value', JSON.stringify(['__local__', 'synthetic']));
    await chooseModel(dialog.getByLabel('执行模型', { exact: true }), JSON.stringify(['__local__', 'synthetic']));
    await dialog.getByLabel('名称', { exact: true }).fill('仅修改名称的合成计划');
    await dialog.getByRole('button', { name: '保存', exact: true }).click();
    await expect(dialog).not.toBeVisible();
    const updates = () => state.requests.filter(request => request.method === 'PATCH' && request.path === '/api/tasks/existing');
    await expect.poll(() => updates().length).toBe(1);
    expect(updates()[0].body).toMatchObject({ name: '仅修改名称的合成计划', trigger: { type: 'cron' } });
    expect(updates()[0].body.trigger.cron_expr).toBe(expectedCron);
    await expect(page.getByText('仅修改名称的合成计划', { exact: true })).toBeVisible();
    await page.getByRole('button', { name: '编辑', exact: true }).click();
    dialog = page.getByRole('dialog', { name: '编辑自动化任务' });
    await expect(dialog.getByRole('button', { name: mode, exact: true })).toHaveClass(/bg-secondary/);
    if (mode === '高级') {
      await expect(dialog.getByLabel('Cron 表达式', { exact: true })).toHaveValue(expectedCron);
    } else {
      await expect(dialog.getByLabel('Cron 表达式', { exact: true })).toHaveCount(0);
      await expect(dialog.getByLabel('执行时间', { exact: true })).toHaveValue(time);
      if (mode === '每周') await expect(dialog.getByRole('button', { name: '日', exact: true })).toHaveClass(/bg-primary/);
      if (mode === '每月') await expect(dialog.getByLabel('每月执行日期', { exact: true })).toHaveValue('31');
    }
    if (cronExpr === '0 9 * * 1-5') await dialog.screenshot({ path: testInfo.outputPath('cron-advanced-preserved-desktop.png') });
    if (mode === '每月') {
      await page.setViewportSize({ width: 390, height: 844 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await dialog.screenshot({ path: testInfo.outputPath('cron-monthly-31-mobile.png') });
    }
    expect(errors).toEqual([]);
  });
}

test('复杂 cron 模板创建提交原表达式并可按高级模式重开', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const state = await mockAutomation(page);
  const cronExpr = '0 9 1,15 * *';
  state.templates[0].cron_expr = cronExpr;
  await page.goto('/tasks');
  await page.getByRole('button', { name: '添加自动化', exact: true }).first().click();
  await page.getByRole('button', { name: /审计模板/ }).click();
  let dialog = page.getByRole('dialog', { name: '添加自动化任务' });
  await expect(dialog.getByLabel('提示词', { exact: true })).toHaveValue('合成模板需求');
  await dialog.getByLabel('名称', { exact: true }).fill('复杂模板计划');
  await chooseModel(dialog.getByLabel('执行模型', { exact: true }), JSON.stringify(['__local__', 'synthetic']));
  await dialog.getByRole('button', { name: '保存', exact: true }).click();
  await expect(dialog).not.toBeVisible();
  const creations = () => state.requests.filter(request => request.method === 'POST' && request.path === '/api/tasks/manual');
  await expect.poll(() => creations().length).toBe(1);
  expect(creations()[0].body).toMatchObject({ name: '复杂模板计划', template_id: 'audit-template', trigger: { type: 'cron' } });
  expect(creations()[0].body.trigger.cron_expr).toBe(cronExpr);
  const card = page.locator('.animate-fade-in').filter({ has: page.getByText('复杂模板计划', { exact: true }) });
  await card.getByRole('button', { name: '编辑', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '编辑自动化任务' });
  await expect(dialog.getByRole('button', { name: '高级', exact: true })).toHaveClass(/bg-secondary/);
  await expect(dialog.getByLabel('Cron 表达式', { exact: true })).toHaveValue(cronExpr);
  expect(errors).toEqual([]);
});

for (const scenario of [
  { name: '关闭', status: { enabled: false, running: false }, message: '自动调度已关闭' },
  { name: '正常轮询', status: { enabled: true, running: true }, message: '自动调度运行中' },
  { name: '轮询未运行', status: { enabled: true, running: false }, message: '自动调度已开启，但轮询未运行' },
  { name: '未知状态', status: {}, message: '自动调度状态无法确认' },
]) {
  test(`自动调度${scenario.name}可见且保留手动执行`, async ({ page }, testInfo) => {
    const errors: string[] = [];
    page.on('pageerror', error => errors.push(error.message));
    await mockAutomation(page, scenario.status);
    await page.goto('/tasks');
    await expect(page).toHaveURL(/\/tasks$/);
    await expect(page.getByRole('heading', { name: '任务中心' })).toBeVisible();
    await expect(page.getByText(scenario.message, { exact: false })).toBeVisible();
    await expect(page.locator('body')).not.toContainText('北京时间');
    await expect(page.locator('body')).not.toContainText('UTC+8');
    await expect(page.getByRole('button', { name: '立即执行', exact: true })).toBeEnabled();
    if (scenario.name !== '正常轮询') await expect(page.getByText('自动调度运行中', { exact: false })).toHaveCount(0);
    if (scenario.name === '关闭') {
      await page.screenshot({ path: testInfo.outputPath('scheduler-disabled-desktop.png') });
      await page.setViewportSize({ width: 390, height: 844 });
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
      await page.screenshot({ path: testInfo.outputPath('scheduler-disabled-mobile.png') });
    }
    expect(errors).toEqual([]);
  });
}

test('调度状态读取失败不能沿用上次正常提示，重试可恢复', async ({ page }) => {
  const state = await mockAutomation(page);
  await page.goto('/tasks');
  await expect(page.getByText('自动调度运行中', { exact: false })).toBeVisible();
  state.statusFailure = true;
  await page.getByRole('button', { name: '刷新', exact: true }).click();
  await expect(page.getByText('自动调度状态读取失败', { exact: false })).toBeVisible();
  await expect(page.getByText('自动调度运行中', { exact: false })).toHaveCount(0);
  state.statusFailure = false;
  await page.getByRole('button', { name: '重试读取调度状态', exact: true }).click();
  await expect(page.getByText('自动调度运行中', { exact: false })).toBeVisible();
});

test('执行历史读取失败不能冒充空记录，重试恢复且后台失败保留报告数据', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const state = await mockAutomation(page);
  const originalRuns = JSON.stringify(state.runs);
  state.runsFailure = true;
  await page.goto('/tasks');
  await page.getByRole('button', { name: '历史', exact: true }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('执行历史读取失败，请重试。', { exact: true })).toBeVisible();
  await expect(dialog.getByText('还没有执行历史。', { exact: true })).toHaveCount(0);
  state.runsFailure = false;
  await dialog.getByRole('button', { name: '重试读取执行历史', exact: true }).click();
  const firstRecord = dialog.getByText('合成记录1', { exact: true });
  await expect(firstRecord).toBeVisible();
  const recordNode = await firstRecord.elementHandle();
  state.runsFailure = true;
  await expect(dialog.getByText('执行历史更新失败，当前显示上次读取的数据。', { exact: true })).toBeVisible({ timeout: 10_000 });
  expect(await recordNode!.evaluate(element => element.isConnected)).toBe(true);
  await expect(firstRecord).toBeVisible();
  await expect(dialog.getByText('还没有执行历史。', { exact: true })).toHaveCount(0);
  state.runsFailure = false;
  await dialog.getByRole('button', { name: '重试读取执行历史', exact: true }).click();
  await expect(dialog.getByText('执行历史更新失败，当前显示上次读取的数据。', { exact: true })).toHaveCount(0);
  await dialog.getByRole('button', { name: '查看', exact: true }).first().click();
  await expect(dialog.getByRole('heading', { name: '合成报告', exact: true })).toBeVisible();
  await expect(dialog.getByRole('heading', { name: /^报告 ·/ })).not.toContainText('+08:00');
  await expect(dialog.getByRole('heading', { name: /^报告 ·/ })).toHaveText('报告 · 2026/10/06 09:30:00');
  await expect(dialog).toContainText('验证结果：20。');
  expect(JSON.stringify(state.runs)).toBe(originalRuns);
  expect(errors).toEqual([]);
});

test('自动化模板、手动创建、编辑控制及报告记录完整界面旅程', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const state = await mockAutomation(page, { enabled: false, running: false });
  await page.goto('/tasks');
  await page.getByRole('button', { name: '添加自动化', exact: true }).first().click();
  await page.getByRole('button', { name: /审计模板/ }).click();
  let dialog = page.getByRole('dialog', { name: '添加自动化任务' });
  await expect(dialog.getByLabel('提示词', { exact: true })).toHaveValue('合成模板需求');
  await dialog.getByLabel('名称', { exact: true }).fill('模板计划');
  await chooseModel(dialog.getByLabel('执行模型', { exact: true }), JSON.stringify(['__local__', 'synthetic']));
  await dialog.getByRole('button', { name: '保存', exact: true }).click();
  await expect(page.locator('.animate-fade-in').filter({ has: page.getByText('模板计划', { exact: true }) })).toContainText('模板创建');
  expect(state.requests.find(request => request.path === '/api/tasks/manual')?.body).toMatchObject({ template_id: 'audit-template', trigger: { type: 'cron', cron_expr: '30 9 * * 1,3,5' } });

  await page.getByRole('button', { name: '添加自动化', exact: true }).first().click();
  await page.getByRole('button', { name: /空白创建/ }).click();
  dialog = page.getByRole('dialog', { name: '添加自动化任务' });
  await dialog.getByLabel('名称', { exact: true }).fill('手动计划');
  await dialog.getByLabel('提示词', { exact: true }).fill('合成手动需求');
  await chooseModel(dialog.getByLabel('执行模型', { exact: true }), JSON.stringify(['__local__', 'synthetic']));
  await dialog.getByRole('button', { name: '按间隔', exact: true }).click();
  await dialog.getByLabel('间隔数值').fill('3');
  await dialog.getByLabel('间隔单位').selectOption('minutes');
  await dialog.getByRole('button', { name: '保存', exact: true }).click();
  let card = page.locator('.animate-fade-in').filter({ has: page.getByText('手动计划', { exact: true }) });
  await expect(card).toContainText('手动创建');
  expect(state.requests.filter(request => request.path === '/api/tasks/manual').at(-1)?.body).toMatchObject({ trigger: { type: 'interval', interval_seconds: 180 } });
  expect(state.requests.filter(request => request.path === '/api/tasks/manual').at(-1)?.body.template_id).toBeUndefined();
  await card.getByRole('button', { name: '编辑', exact: true }).click();
  dialog = page.getByRole('dialog', { name: '编辑自动化任务' });
  await dialog.getByLabel('名称', { exact: true }).fill('已编辑计划');
  await dialog.getByRole('button', { name: '单次', exact: true }).click();
  await dialog.getByLabel('单次执行时间').fill('2099-10-07T09:30');
  await dialog.getByRole('button', { name: '保存', exact: true }).click();
  card = page.locator('.animate-fade-in').filter({ has: page.getByText('已编辑计划', { exact: true }) });
  await expect(card).toContainText('单次');
  await card.getByRole('switch').click();
  await expect(card.getByRole('switch')).toHaveAttribute('aria-checked', 'false');
  await page.getByRole('button', { name: '已暂停', exact: true }).click();
  await expect(card).toBeVisible();
  await expect(page.getByText('模板计划', { exact: true })).toHaveCount(0);
  await card.getByRole('switch').click();
  await page.getByRole('button', { name: '全部', exact: true }).click();
  await expect(card.getByRole('switch')).toHaveAttribute('aria-checked', 'true');
  await card.getByRole('button', { name: '立即执行', exact: true }).click();
  await expect(card.getByRole('button', { name: '历史', exact: true })).toBeVisible();
  await card.getByRole('button', { name: '历史', exact: true }).click();
  dialog = page.getByRole('dialog');
  await expect(dialog.getByText('合成记录25', { exact: true })).toBeVisible();
  for (const kind of ['报告 (Markdown)', '采集数据 (JSON)']) {
    const downloading = page.waitForEvent('download');
    await dialog.getByTitle(`下载${kind}`, { exact: true }).click();
    const file = await downloading;
    expect(file.suggestedFilename()).toMatch(kind.startsWith('报告') ? /\.md$/ : /\.json$/);
    expect(file.suggestedFilename()).toBe(kind.startsWith('报告') ? 'report_20261006_093000.md' : 'json_20261006_093000.json');
    expect(await file.failure()).toBeNull();
  }
  await dialog.getByRole('button', { name: '查看', exact: true }).click();
  await expect(dialog.getByRole('heading', { name: '合成报告', exact: true })).toBeVisible();
  await expect(dialog.getByRole('heading', { name: /^报告 ·/ })).not.toContainText('+08:00');
  await expect(dialog.getByRole('heading', { name: /^报告 ·/ })).toHaveText('报告 · 2026/10/06 09:30:00');
  await expect(dialog).toContainText('验证结果：20。');
  await dialog.getByRole('button', { name: '返回历史列表', exact: true }).click();
  await expect(dialog.getByText('合成记录25', { exact: true })).toBeVisible();
  await dialog.getByRole('button', { name: '关闭', exact: true }).click();
  await page.getByPlaceholder('搜索任务名称或提示词').fill('已编辑');
  await expect(page.getByText('模板计划', { exact: true })).toHaveCount(0);
  await card.getByTitle('删除任务', { exact: true }).click();
  await page.getByRole('dialog', { name: '删除自动化计划？' }).getByRole('button', { name: '确认删除', exact: true }).click();
  await expect(card).toHaveCount(0);
  await page.getByRole('button', { name: '运行记录', exact: true }).click();
  const pager = page.getByRole('navigation', { name: '运行记录分页' });
  await pager.getByRole('button', { name: '下一页' }).click();
  await expect(pager).toContainText('2 / 3 页');
  await page.getByRole('button', { name: '失败', exact: true }).click();
  await expect(pager).toContainText('1 / 2 页');
  await page.getByPlaceholder('搜索执行摘要关键词').fill('合成记录2');
  await expect(pager).toContainText('共 3 条');
  await page.getByRole('button', { name: '全部', exact: true }).click();
  await page.getByPlaceholder('搜索执行摘要关键词').fill('合成记录25');
  await expect(pager).toContainText('共 1 条');
  await page.getByRole('button', { name: '查看', exact: true }).click();
  await expect(page.getByRole('dialog').getByRole('heading', { name: '合成报告', exact: true })).toBeVisible();
  await expect(page.getByRole('dialog').getByRole('heading', { name: /^报告 ·/ })).not.toContainText('+08:00');
  await expect(page.getByRole('dialog').getByRole('heading', { name: /^报告 ·/ })).toHaveText('报告 · 2026/10/06 09:30:00');
  await page.getByRole('dialog').getByRole('button', { name: '关闭', exact: true }).click();
  await page.screenshot({ path: testInfo.outputPath('automation-full-flow.png') });
  expect(errors).toEqual([]);
});

test('运行记录延迟轮询不卸载已有节点，保留筛选分页滚动并能更新和恢复失败', async ({ page }, testInfo) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const state = await mockAutomation(page);
  await page.setViewportSize({ width: 1280, height: 720 });
  await page.goto('/tasks');
  await page.getByRole('button', { name: '运行记录', exact: true }).click();
  const search = page.getByPlaceholder('搜索执行摘要关键词');
  await search.fill('合成记录');
  const taskFilter = page.locator('select').filter({ has: page.getByRole('option', { name: '全部任务', exact: true }) });
  await taskFilter.selectOption('existing');
  const pager = page.getByRole('navigation', { name: '运行记录分页' });
  await pager.getByRole('button', { name: '下一页' }).click();
  await expect(page.getByText('合成记录11', { exact: true })).toBeVisible();
  const row = await page.getByText('合成记录11', { exact: true }).elementHandle();
  const scroll = search.locator('xpath=ancestor::div[contains(@class,"overflow-y-auto")][1]');
  await scroll.evaluate(node => { node.scrollTop = 160; });
  const position = await scroll.evaluate(node => node.scrollTop);
  expect(position).toBeGreaterThan(0);
  state.holdNextRuns = true;
  await expect.poll(() => Boolean(state.releaseRuns), { timeout: 12000 }).toBe(true);
  await expect(page.getByText('加载中…', { exact: true })).toHaveCount(0);
  expect(await row!.evaluate(node => node.isConnected)).toBe(true);
  expect(await scroll.evaluate(node => node.scrollTop)).toBe(position);
  await expect(search).toHaveValue('合成记录');
  await expect(taskFilter).toHaveValue('existing');
  await expect(pager).toContainText('2 / 3 页');
  state.runs.find(item => item.run_id === 11)!.summary = '合成记录11 · 已更新';
  state.runs.push({ ...state.runs[0], run_id: 24, summary: '合成记录24 · 新结果' });
  const response = page.waitForResponse(item => item.url().includes('/api/tasks/runs/recent'));
  state.releaseRuns!(); state.releaseRuns = undefined;
  await response;
  await expect(page.getByText('合成记录11 · 已更新', { exact: true })).toBeVisible();
  expect(await row!.evaluate(node => node.isConnected)).toBe(true);
  expect(await scroll.evaluate(node => node.scrollTop)).toBe(position);
  await expect(pager).toContainText('共 24 条');
  state.runsFailure = true; state.holdNextRuns = true;
  await expect.poll(() => Boolean(state.releaseRuns), { timeout: 12000 }).toBe(true);
  expect(await row!.evaluate(node => node.isConnected)).toBe(true);
  state.releaseRuns!(); state.releaseRuns = undefined;
  await expect(page.getByText('运行记录更新失败，当前显示上次读取的数据。', { exact: true })).toBeVisible();
  expect(await row!.evaluate(node => node.isConnected)).toBe(true);
  await expect(search).toHaveValue('合成记录');
  await expect(pager).toContainText('2 / 3 页');
  state.runsFailure = false;
  await page.getByRole('button', { name: '重试读取运行记录', exact: true }).click();
  await expect(page.getByText('运行记录更新失败，当前显示上次读取的数据。', { exact: true })).toHaveCount(0);
  expect(await row!.evaluate(node => node.isConnected)).toBe(true);
  await pager.getByRole('button', { name: '下一页' }).click();
  await expect(page.getByText('合成记录24 · 新结果', { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath('runs-background-refresh.png') });
  expect(errors).toEqual([]);
});

test('任务列表连续失败时运行记录仍逐轮刷新', async ({ page }) => {
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  const state = await mockAutomation(page);
  await page.goto('/tasks');
  await page.getByRole('button', { name: '运行记录', exact: true }).click();
  await expect(page.getByText('合成记录1', { exact: true })).toBeVisible();
  const row = await page.getByText('合成记录1', { exact: true }).elementHandle();
  state.tasksFailure = true;
  state.runs[0].summary = '合成记录1 · 列表失败期间第一次更新';
  await expect(page.getByText(state.runs[0].summary, { exact: true })).toBeVisible({ timeout: 12000 });
  expect(await row!.evaluate(node => node.isConnected)).toBe(true);
  state.runs[0].summary = '合成记录1 · 列表失败期间第二次更新';
  await expect(page.getByText(state.runs[0].summary, { exact: true })).toBeVisible({ timeout: 12000 });
  expect(await row!.evaluate(node => node.isConnected)).toBe(true);
  await expect(page.getByText('加载中…', { exact: true })).toHaveCount(0);
  await expect(page.getByText('运行记录更新失败', { exact: false })).toHaveCount(0);
  expect(errors).toEqual([]);
});
