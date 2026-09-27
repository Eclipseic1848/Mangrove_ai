/** 有时区才转换；历史无时区记录保留原值，禁止按浏览器时区猜测。 */
export function beijingTime(value?: string | null, knownBeijing = false): string {
  if (!value) return '—';
  if (!/(Z|[+-]\d{2}:?\d{2})$/i.test(value))
    return `${value.replace('T', ' ')}${knownBeijing ? '' : '（历史时区未记录）'}`;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).format(date);
}
