import { expect, test } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

test('中国区价格表显示人民币原价、来源和折算说明', async ({ page }) => {
  const pricing = JSON.parse(readFileSync(resolve(process.env.TOKEN_PRICE_TEST_FILE || '../src/operations_prices.json'), 'utf8'));
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/me') return route.fulfill({ json: { user_id: 'operator', username: 'operator', role: 'super_admin' } });
    if (path === '/api/operations/tokens/query') return route.fulfill({ json: { items: [], total: 0, page: 1, page_size: 10, summary: { users: 0, requests: 0, local_requests: 0, total_tokens: 0, unknown_requests: 0, priced_requests: 0, cost: '0.000000' }, pricing, coverage_note: '' } });
    return route.fulfill({ json: {} });
  });
  await page.goto('/operations?tab=tokens');
  await page.getByText('参考价格与统计说明', { exact: true }).click();
  const priceRow = page.getByRole('row').filter({ has: page.getByRole('link', { name: 'deepseek-flash', exact: true }) });
  await expect(priceRow).toContainText('2.00 CNY');
  await expect(priceRow).toContainText('8.00 CNY');
  await expect(page.getByRole('link', { name: 'qwen3.8-max', exact: true })).toHaveAttribute('href', 'https://help.aliyun.com/zh/model-studio/qwen3-8-max');
  await expect(page.getByRole('link', { name: 'glm-5.3', exact: true })).toBeVisible();
  await expect(page.getByText(/人民币按官网中国区原价估算，不由美元报价换算/)).toBeVisible();
});

test('本地模型只计Token并展示无需计价', async ({ page }) => {
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/me') return route.fulfill({ json: { user_id: 'operator', username: 'operator', role: 'super_admin' } });
    if (path === '/api/operations/tokens/query') {
      const detail = { model: 'local-model', api_format: 'openai_chat', requests: 1, local_requests: 1, input_tokens: 100, output_tokens: 50, total_tokens: 150, unknown_requests: 0, priced_requests: 0, cost: null, last_used: '2026-09-19T12:00:00+08:00' };
      return route.fulfill({ json: { items: [{ ...detail, user_id: 'local-user', username: 'local-user', name: '本地用户', models: [detail] }], total: 1, page: 1, page_size: 10, summary: { ...detail, users: 1 }, pricing: { version: 'test', usd_to_cny: '7', exchange_note: '测试汇率', notice: '参考价格，仅供估算', models: [], unpriced_models: ['qwen3.8-flash'] }, coverage_note: '' } });
    }
    return route.fulfill({ json: {} });
  });
  await page.goto('/operations?tab=tokens');
  const users = page.getByRole('table', { name: '用户Token统计' });
  await expect(users).toContainText('无需计价');
  await expect(users).toContainText('150');
  await page.getByRole('button', { name: '查看本地用户的模型明细' }).click();
  await expect(page.getByRole('table', { name: '模型消耗明细' })).toContainText('无需计价');
  await page.getByText('参考价格与统计说明', { exact: true }).click();
  await expect(page.getByText('qwen3.8-flash', { exact: false })).toBeVisible();
});

test('Token统计按用户分页、筛选并查看模型明细', async ({ page }, testInfo) => {
  const errors: string[] = [];
  let exported: { format: string; filters: { search: string; currency: string } } | null = null;
  page.on('pageerror', error => errors.push(error.message));
  await page.route('**/api/**', route => {
    const path = new URL(route.request().url()).pathname;
    if (path === '/api/auth/me') return route.fulfill({ json: { user_id: 'operator', username: 'operator', display_name: '运营员', role: 'super_admin' } });
    if (path === '/api/operations/tokens/export') {
      exported = route.request().postDataJSON();
      return route.fulfill({ contentType: 'text/csv', body: '用户,用量\n合成用户,1500' });
    }
    if (path === '/api/operations/tokens/query') {
      const filters = route.request().postDataJSON();
      const total = filters.search ? 1 : 23;
      const size = filters.page_size;
      const current = Math.min(filters.page, Math.ceil(total / size));
      const detail = { model: 'gpt-4o-mini', api_format: 'openai_chat', requests: 2, input_tokens: 1000, output_tokens: 500, total_tokens: 1500, unknown_requests: 0, priced_requests: 2, cost: '0.003150', last_used: '2026-09-19T12:00:00+08:00' };
      const items = Array.from({ length: Math.min(size, total - (current - 1) * size) }, (_, i) => ({ ...detail, user_id: `user-${i}`, username: `user${i}`, name: `合成用户${(current - 1) * size + i + 1}`, models: [detail] }));
      return route.fulfill({ json: { items, total, page: current, page_size: size, currency: filters.currency, summary: { ...detail, users: total }, pricing: { version: '2026-09-19', usd_to_cny: '7.0', exchange_note: '示例汇率，非实时汇率', notice: '参考价格，仅供估算，实际以官方账单为准', models: [] }, coverage_note: '仅统计已保存的可信调用记录' } });
    }
    return route.fulfill({ json: {} });
  });
  await page.goto('/operations?tab=tokens');
  await expect(page.getByRole('heading', { name: '分用户Token消耗' })).toBeVisible();
  const table = page.getByRole('table', { name: '用户Token统计' });
  await expect(table.locator('tbody > tr')).toHaveCount(10);
  for (const size of ['20', '50', '100', '10']) {
    await page.getByRole('combobox', { name: '每页条数' }).selectOption(size);
    await expect(table.locator('tbody > tr')).toHaveCount(Math.min(Number(size), 23));
  }
  await page.getByRole('button', { name: '下一页', exact: true }).click();
  await expect(table).toContainText('合成用户11');
  await page.getByRole('textbox', { name: '搜索用户' }).fill('合成');
  await expect(table.locator('tbody > tr')).toHaveCount(1);
  const initialStart = await page.getByLabel('开始日期', { exact: true }).inputValue();
  await page.getByLabel('开始日期', { exact: true }).fill('9999-12-31');
  await expect(page.getByRole('button', { name: '导出用量', exact: true })).toBeDisabled();
  await page.getByLabel('开始日期', { exact: true }).fill(initialStart);
  await expect(page.getByRole('textbox', { name: '搜索用户' })).toHaveValue('合成');
  await expect(table.locator('tbody > tr')).toHaveCount(1);
  await page.getByRole('button', { name: '查看合成用户1的模型明细' }).click();
  await expect(page.getByRole('table', { name: '模型消耗明细' })).toContainText('gpt-4o-mini');
  await page.getByRole('combobox', { name: '币种' }).selectOption('USD');
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: '导出用量', exact: true }).click();
  expect((await download).suggestedFilename()).toContain('Token');
  expect(exported).toMatchObject({ format: 'csv', filters: { search: '合成', currency: 'USD' } });
  await page.getByRole('button', { name: '近30天', exact: true }).click();
  await expect(page.getByRole('textbox', { name: '搜索用户' })).toHaveValue('合成');
  await expect(page.getByRole('combobox', { name: '币种' })).toHaveValue('USD');
  await expect(table.locator('tbody > tr')).toHaveCount(1);
  await expect(page.locator('vite-error-overlay')).toHaveCount(0);
  expect(errors).toEqual([]);
  await page.screenshot({ path: testInfo.outputPath('token-desktop.png') });
  await page.setViewportSize({ width: 390, height: 750 });
  await expect(page.getByRole('link', { name: 'Token用量', exact: true })).toBeInViewport();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath('token-mobile.png') });
  expect((await new AxeBuilder({ page }).include('[aria-label="Token统计"]').analyze()).violations).toEqual([]);
  await page.route('**/api/operations/tokens/query', route => route.fulfill({ status: 503, json: { detail: '合成错误' } }));
  await page.getByRole('button', { name: '刷新', exact: true }).click();
  await expect(page.getByRole('alert')).toContainText('统计暂不可用');
  await expect(page.getByRole('button', { name: '导出用量', exact: true })).toBeDisabled();
});
