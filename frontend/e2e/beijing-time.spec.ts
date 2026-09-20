import { expect, test } from '@playwright/test';
import { beijingTime } from '../src/lib/beijingTime';

test('时间戳保持北京时间转换但不追加时区文案', () => {
  expect(beijingTime('2026-09-18T00:30:00Z')).toBe('2026/09/18 08:30:00');
  expect(beijingTime('2026-09-18T08:30:00+08:00')).toBe('2026/09/18 08:30:00');
  expect(beijingTime('2026-09-18T08:30:00', true)).toBe('2026-09-18 08:30:00');
  expect(beijingTime('2026-09-18T08:30:00')).toBe('2026-09-18 08:30:00（历史时区未记录）');
  expect(beijingTime(null)).toBe('—');
});
