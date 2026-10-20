import { expect, test, type Page } from '@playwright/test';

async function mockPerformance(page: Page) {
  const state = {
    owner: 'performance-a', role: 'admin', failed: false, preferences: '合成全局偏好', digest: 'a'.repeat(64),
    reads: [] as Array<{ path: string; query: string; owner: string }>,
    writes: [] as Array<{ path: string; body: any }>,
    rows: Array.from({ length: 23 }, (_, index) => ({ id: index + 1, text: `合成个人记忆 ${index + 1}` })),
    errors: [] as string[],
  };
  page.on('pageerror', error => state.errors.push(error.message));
  await page.addInitScript(() => {
    for (const owner of ['performance-a', 'performance-b']) localStorage.setItem(`onboarding_all_${JSON.stringify([owner, 'admin'])}`, 'skipped');
  });
  await page.route('**/api/**', async route => {
    const request = route.request(), url = new URL(request.url()), path = url.pathname;
    if (request.method() === 'GET') state.reads.push({ path, query: url.search, owner: state.owner });
    if (path === '/api/auth/me') return route.fulfill({ json: { user_id: state.owner, username: state.owner, role: state.role } });
    if (path === '/api/auth/login') {
      state.owner = 'performance-b';
      state.rows = [{ id: 99, text: '另一个账号的合成记忆' }];
      return route.fulfill({ json: { user_id: state.owner, username: state.owner, role: state.role } });
    }
    if (path === '/api/auth/logout') return route.fulfill({ json: { ok: true } });
    if (path === '/api/memory' && request.method() === 'GET') {
      await new Promise(resolve => setTimeout(resolve, 150));
      if (state.failed) return route.fulfill({ status: 503, json: { detail: '合成读取失败' } });
      const filtered = state.rows.filter(row => row.text.includes(url.searchParams.get('q') || ''));
      const size = Number(url.searchParams.get('page_size') || 10);
      const current = Math.min(Number(url.searchParams.get('page') || 1), Math.max(1, Math.ceil(filtered.length / size)));
      return route.fulfill({ json: { personal: filtered.slice((current - 1) * size, current * size), page: current, total: filtered.length, preferences: state.preferences, preferences_digest: state.digest } });
    }
    if (path.startsWith('/api/memory') && request.method() !== 'GET') {
      const body = request.postDataJSON();
      state.writes.push({ path, body });
      if (path === '/api/memory/self') {
        const item = { id: 100, text: body.text }; state.rows.unshift(item);
        return route.fulfill({ json: { ok: true, item } });
      }
      state.preferences = body.text; state.digest = 'b'.repeat(64);
      return route.fulfill({ json: { ok: true } });
    }
    if (path === '/api/models') return route.fulfill({ json: { options: [] } });
    if (path === '/api/chat/history') return route.fulfill({ json: [] });
    if (path === '/api/model-connections' || path === '/api/model-connections/presets') return route.fulfill({ json: { items: [] } });
    if (path === '/api/model-connections/preferences/default') return route.fulfill({ json: { preference: null } });
    if (path === '/api/templates') return route.fulfill({ json: { templates: [] } });
    if (path === '/api/lessons') return route.fulfill({ json: { lessons: [] } });
    if (path === '/api/library-dedup-log') return route.fulfill({ json: { log: [] } });
    if (path === '/api/overview') return route.fulfill({ json: { collectors: [], connectors: {}, connectors_enabled: {}, scheduler: { enabled: false, active_count: 0 } } });
    if (path === '/api/semantic-workspace/storage') return route.fulfill({ json: { task_count: 0, recycle_bin_count: 0, total_bytes: 0, upload_bytes: 0, delivery_bytes: 0 } });
    if (path === '/api/semantic-workspace/capabilities') return route.fulfill({ json: { enabled: true, items: [] } });
    if (path === '/api/semantic-workspace/context-options') return route.fulfill({ json: { templates: [], memories: [] } });
    if (path === '/api/semantic-workspace/guidance') return route.fulfill({ json: { schema_version: '1', onboarding: [], examples: [] } });
    if (path === '/api/settings/onboarding/model-connections') return route.fulfill({ json: { completed: true } });
    return route.fulfill({ json: [] });
  });
  return state;
}

for (const intent of [
  { action: 'pointerenter', label: '设置', module: '/src/pages/Settings.tsx' },
  { action: 'focus', label: '自动化任务', module: '/src/pages/Tasks.tsx' },
  { action: 'pointerdown', label: '模板库', module: '/src/pages/Templates.tsx' },
]) {
  test(`导航${intent.action}只预载对应页面代码，不读取业务API`, async ({ page }) => {
    const state = await mockPerformance(page);
    await page.goto('/memory');
    await expect(page.getByText('合成个人记忆 1', { exact: true })).toBeVisible();
    const reads = state.reads.length;
    const loaded = page.waitForResponse(response => new URL(response.url()).pathname === intent.module, { timeout: 3000 });
    const link = page.getByRole('navigation').getByRole('link', { name: intent.label, exact: true });
    if (intent.action === 'focus') await link.focus();
    else if (intent.action === 'pointerenter') await link.hover();
    else await link.dispatchEvent(intent.action, { pointerType: 'touch' });
    await (await loaded).finished();
    await expect(page).toHaveURL(/\/memory$/);
    await expect(page.getByRole('heading', { name: '记忆', exact: true })).toBeVisible();
    expect(state.reads.length).toBe(reads);
    expect(state.errors).toEqual([]);
  });
}

test('默认工作台导航意图并行预载两层代码，进入前不读取业务API，旧入口保留', async ({ page }) => {
  const state = await mockPerformance(page);
  await page.goto('/memory');
  await expect(page.getByText('合成个人记忆 1', { exact: true })).toBeVisible();
  const reads = state.reads.length;
  const responses = ['/src/pages/DataPrepPage.tsx', '/src/pages/SemanticWorkspacePage.tsx'].map(path => page.waitForResponse(response => new URL(response.url()).pathname === path, { timeout: 3000 }));
  const link = page.getByRole('navigation').getByRole('link', { name: '任务工作台', exact: true });
  await link.focus();
  await Promise.all(responses.map(async response => (await response).finished()));
  await expect(page).toHaveURL(/\/memory$/);
  expect(state.reads.length).toBe(reads);
  await link.click();
  await expect(page.getByRole('heading', { name: '任务工作台', exact: true })).toBeVisible();
  await expect(page.getByRole('textbox', { name: '任务要求', exact: true })).toBeVisible();
  await page.goto('/data-prep?legacy=1');
  await expect(page.getByRole('heading', { name: '数据准备', exact: true })).toBeVisible();
  await expect(page.getByRole('tab', { name: '文档智能抽取', exact: true })).toBeVisible();
  expect(state.errors).toEqual([]);
});

test('记忆短缓存过期后后台重读，失败保留旧正文并禁用失效编辑', async ({ page }) => {
  await page.clock.install();
  const state = await mockPerformance(page);
  await page.goto('/memory');
  await expect(page.getByText('合成个人记忆 1', { exact: true })).toBeVisible();
  await page.getByRole('link', { name: '模板库', exact: true }).click();
  await expect(page.getByRole('heading', { name: '模板库', exact: true })).toBeVisible();
  await page.clock.fastForward(16000);
  state.failed = true;
  await page.getByRole('link', { name: '记忆', exact: true }).click();
  await expect(page.getByText('合成个人记忆 1', { exact: true })).toBeVisible();
  await expect(page.getByText('正在更新列表…', { exact: true })).toBeVisible();
  await expect(page.getByText('更新失败，当前显示上次读取的记忆。', { exact: true })).toBeVisible();
  await expect(page.getByRole('button', { name: '编辑记忆：合成个人记忆 1', exact: true })).toBeDisabled();
  expect(state.reads.filter(read => read.path === '/api/memory')).toHaveLength(2);
  expect(state.errors).toEqual([]);
});

test('记忆父子同查询去重、热返回复用，刷新写入强制回读且筛选不串页', async ({ page }) => {
  const state = await mockPerformance(page);
  const memoryReads = () => state.reads.filter(read => read.path === '/api/memory');
  await page.goto('/memory');
  await expect(page.getByText('合成个人记忆 1', { exact: true })).toBeVisible();
  expect(memoryReads()).toHaveLength(1);
  await page.getByRole('link', { name: '模板库', exact: true }).click();
  await expect(page.getByRole('heading', { name: '模板库', exact: true })).toBeVisible();
  await page.getByRole('link', { name: '记忆', exact: true }).click();
  await expect(page.getByText('合成个人记忆 1', { exact: true })).toBeVisible();
  expect(memoryReads()).toHaveLength(1);
  await page.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(page.getByText('合成个人记忆 11', { exact: true })).toBeVisible();
  await expect(page.getByText('合成个人记忆 1', { exact: true })).toHaveCount(0);
  await page.getByLabel('搜索个人记忆', { exact: true }).fill('记忆 21');
  await expect(page.getByText('合成个人记忆 21', { exact: true })).toBeVisible();
  await expect(page.getByText('合成个人记忆 11', { exact: true })).toHaveCount(0);
  await page.getByRole('button', { name: '清除搜索', exact: true }).click();
  await expect(page.getByText('合成个人记忆 1', { exact: true })).toBeVisible();
  state.failed = true;
  await page.getByRole('button', { name: '刷新', exact: true }).click();
  await expect(page.getByText('更新失败，当前显示上次读取的记忆。', { exact: true })).toBeVisible();
  await expect(page.getByText('合成个人记忆 1', { exact: true })).toBeVisible();
  await page.getByRole('tab', { name: '全局记忆', exact: true }).click();
  await expect(page.getByRole('button', { name: '编辑全局记忆', exact: true })).toBeDisabled();
  state.failed = false;
  await page.getByRole('button', { name: '刷新', exact: true }).click();
  await expect(page.getByRole('button', { name: '编辑全局记忆', exact: true })).toBeEnabled();
  await page.getByRole('button', { name: '编辑全局记忆', exact: true }).click();
  await page.getByLabel('全局记忆内容', { exact: true }).fill('修改后的合成全局偏好');
  await page.getByRole('button', { name: '保存全局记忆', exact: true }).click();
  await page.getByRole('button', { name: '确认保存', exact: true }).click();
  await expect(page.getByText('修改后的合成全局偏好', { exact: true })).toBeVisible();
  expect(state.writes.find(write => write.path === '/api/memory')?.body.expected_digest).toBe('a'.repeat(64));
  await page.getByRole('tab', { name: '我的记忆', exact: true }).click();
  await page.getByLabel('添加个人记忆', { exact: true }).fill('新写入的合成记忆');
  await page.getByRole('button', { name: '记住', exact: true }).click();
  await expect(page.getByText('新写入的合成记忆', { exact: true })).toBeVisible();
  expect(memoryReads().some(read => read.query.includes('page=2'))).toBe(true);
  expect(memoryReads().some(read => read.query.includes('q=') && read.query.includes('21'))).toBe(true);
  expect(state.errors).toEqual([]);
});

test('退出后换账号不能复用上一账号记忆缓存', async ({ page }) => {
  const state = await mockPerformance(page);
  await page.goto('/memory');
  await expect(page.getByText('合成个人记忆 1', { exact: true })).toBeVisible();
  await page.locator('summary[aria-label="账号选项"]:visible').click();
  await page.getByRole('button', { name: '退出登录', exact: true }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.getByLabel('用户名', { exact: true }).fill('performance-b');
  await page.getByLabel('密码', { exact: true }).fill('synthetic-password');
  await page.locator('button[type="submit"]').click();
  await expect(page.getByText('另一个账号的合成记忆', { exact: true })).toBeVisible();
  await expect(page.getByText('合成个人记忆 1', { exact: true })).toHaveCount(0);
  expect(state.reads.filter(read => read.path === '/api/memory' && read.owner === 'performance-b')).toHaveLength(1);
  expect(state.errors).toEqual([]);
});

test('设置仅进入诊断时读取overview，离开后返回重新读取', async ({ page }) => {
  const state = await mockPerformance(page);
  const overviews = () => state.reads.filter(read => read.path === '/api/overview');
  await page.goto('/settings');
  await expect(page.getByRole('heading', { name: '设置', exact: true })).toBeVisible();
  await expect(page.getByText('暂无可用模型。请到“模型与连接”配置，或联系管理员。', { exact: true })).toBeVisible();
  expect(overviews()).toHaveLength(0);
  await page.getByRole('button', { name: '运行与诊断', exact: true }).click();
  await expect.poll(() => overviews().length).toBe(1);
  await page.getByRole('button', { name: '我的设置', exact: true }).click();
  await page.getByRole('button', { name: '运行与诊断', exact: true }).click();
  await expect.poll(() => overviews().length).toBe(2);
  expect(state.errors).toEqual([]);
});

test('模板默认分区不读取经验与巡检，选用时再读取', async ({ page }) => {
  const state = await mockPerformance(page);
  await page.goto('/templates');
  await expect(page.getByRole('heading', { name: '模板库', exact: true })).toBeVisible();
  await expect.poll(() => state.reads.filter(read => read.path === '/api/templates').length).toBeGreaterThan(0);
  expect(state.reads.filter(read => ['/api/lessons', '/api/library-dedup-log'].includes(read.path))).toHaveLength(0);
  await page.getByRole('button', { name: '教训库', exact: true }).click();
  await expect.poll(() => state.reads.filter(read => read.path === '/api/lessons').length).toBe(1);
  expect(state.reads.filter(read => read.path === '/api/library-dedup-log')).toHaveLength(0);
  await page.getByRole('button', { name: /巡检报告/ }).click();
  await expect.poll(() => state.reads.filter(read => read.path === '/api/library-dedup-log').length).toBe(1);
  expect(state.errors).toEqual([]);
});
