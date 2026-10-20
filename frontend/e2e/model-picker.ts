import type { Locator } from '@playwright/test';

/** 按原连接身份操作真实列表，不绕过选择器及确认交互。 */
export async function chooseModel(picker: Locator, selection: string | { label: string }) {
  if (await picker.getAttribute('aria-expanded') !== 'true') await picker.click();
  const menu = picker.page().getByRole('listbox');
  const option = typeof selection === 'string'
    ? menu.locator(`[role="option"][data-value=${JSON.stringify(selection)}]`)
    : menu.getByRole('option').filter({ has: picker.page().getByText(selection.label, { exact: true }) });
  await option.click();
}
