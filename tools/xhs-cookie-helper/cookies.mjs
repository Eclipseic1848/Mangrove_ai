export const COOKIE_URL = "https://www.xiaohongshu.com/";

export const ERROR_TEXT = Object.freeze({
  unreadable: "无法读取登录态，请确认已允许助手访问小红书，并在当前 Chrome 中正常登录后重试。",
  empty: "未找到小红书登录态，请在当前 Chrome 中打开官网并完成登录。",
  domain: "登录态的域或路径不匹配，请回到小红书官网刷新后重新读取。",
  expired: "发现已过期或有效期不明的登录字段，请在官网重新登录后再读取。",
  partitioned: "当前登录态包含不支持的分区字段，请在普通窗口直接打开小红书官网登录。",
  conflict: "发现同名但不同值的登录字段，暂不能安全导出，请在官网确认账号后重新登录。",
  format: "当前登录态包含采集器不支持的字符，暂不能安全导出，请联系管理员。",
  incomplete: "登录态不完整，缺少会话或签名字段，请在官网完成登录并刷新后重试。",
});

export class CookieReadError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

export function prepareCookieHeader(rows, now = Date.now() / 1000) {
  if (!Array.isArray(rows) || !Number.isFinite(now)) throw new CookieReadError("unreadable");
  if (!rows.length) throw new CookieReadError("empty");
  const values = new Map();
  for (const row of rows) {
    if (!row || typeof row !== "object") throw new CookieReadError("unreadable");
    const domain = typeof row.domain === "string" ? row.domain.toLowerCase() : "";
    if (![".xiaohongshu.com", "xiaohongshu.com", "www.xiaohongshu.com"].includes(domain)
        || row.path !== "/" || typeof row.hostOnly !== "boolean"
        || (row.hostOnly && domain !== "www.xiaohongshu.com")) throw new CookieReadError("domain");
    if (row.partitionKey !== undefined) throw new CookieReadError("partitioned");
    if (typeof row.session !== "boolean"
        || (row.expirationDate !== undefined && (!Number.isFinite(row.expirationDate) || row.expirationDate <= now))
        || (!row.session && row.expirationDate === undefined)) throw new CookieReadError("expired");
    // 现有采集器按分号和等号切分；不能改写凭据或悄悄丢掉无法表达的字段。
    if (typeof row.name !== "string" || !/^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/.test(row.name)
        || typeof row.value !== "string" || !/^[\x21-\x7e]*$/.test(row.value)
        || /[=;"\\,]/.test(row.value)) throw new CookieReadError("format");
    if (values.has(row.name) && values.get(row.name) !== row.value) throw new CookieReadError("conflict");
    values.set(row.name, row.value);
  }
  if (!values.get("web_session") || !values.get("a1")) throw new CookieReadError("incomplete");
  return {header: [...values].map(([name, value]) => `${name}=${value}`).join("; "), count: values.size};
}
