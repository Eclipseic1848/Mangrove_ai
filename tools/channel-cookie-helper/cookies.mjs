import { getChannel } from "./channels.mjs";

export const ERROR_TEXT = Object.freeze({
  unreadable: "无法读取登录态，请核对当前 Chrome 的站点授权，并在对应官网正常登录后重试。",
  denied: "未取得本渠道站点授权，没有读取登录态。你可稍后主动重试。",
  empty: "未找到适用于本渠道业务请求的登录字段，请在对应官网完成登录并刷新后重试。",
  domain: "登录态的域或路径不适用于本渠道业务请求，暂不能安全导出。",
  expired: "发现已过期或有效期不明的字段，请在官网核对并重新读取；这不代表账号整体已过期。",
  partitioned: "当前登录态包含不支持的分区字段，请在普通窗口直接打开对应官网登录。",
  conflict: "发现同名但不同值的字段，暂不能安全导出，请在官网核对账号后重新读取。",
  format: "当前登录态包含该渠道采集器无法安全表达的字符，暂不能导出，请联系管理员。",
  incomplete: "读取范围不足，缺少本渠道已知的会话或签名字段；请在官网登录并刷新后重试，不据此判断账号过期。",
});

export class CookieReadError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

export function prepareCookieHeader(channelId, rows, now = Date.now() / 1000) {
  const channel = getChannel(channelId);
  if (!channel || !Array.isArray(rows) || !Number.isFinite(now)) throw new CookieReadError("unreadable");
  if (!rows.length) throw new CookieReadError("empty");
  const target = new URL(channel.cookieUrl);
  const values = new Map();
  for (const row of rows) {
    if (!row || typeof row !== "object") throw new CookieReadError("unreadable");
    const domain = typeof row.domain === "string" ? row.domain.toLowerCase().replace(/^\./, "") : "";
    const path = row.path;
    const pathMatches = typeof path === "string" && path.startsWith("/") && (path === target.pathname
      || target.pathname.startsWith(path) && (path.endsWith("/") || target.pathname[path.length] === "/"));
    if (!channel.domains.includes(domain) || typeof row.hostOnly !== "boolean" || !pathMatches
        || (row.hostOnly ? domain !== target.hostname || row.domain.startsWith(".")
          : target.hostname !== domain && !target.hostname.endsWith(`.${domain}`))) throw new CookieReadError("domain");
    if (row.partitionKey !== undefined) throw new CookieReadError("partitioned");
    if (typeof row.session !== "boolean"
        || (row.expirationDate !== undefined && (!Number.isFinite(row.expirationDate) || row.expirationDate <= now))
        || (!row.session && row.expirationDate === undefined)) throw new CookieReadError("expired");
    // 只服从当前渠道原解析器；快手和电商允许值内等号，其他六社媒不改写或丢弃这些字段。
    if (typeof row.name !== "string" || !/^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$/.test(row.name)
        || typeof row.value !== "string" || !/^[\x21-\x7e]*$/.test(row.value)
        || /[;"\\,]/.test(row.value) || channel.rejectEquals && row.value.includes("=")) throw new CookieReadError("format");
    if (values.has(row.name) && values.get(row.name) !== row.value) throw new CookieReadError("conflict");
    values.set(row.name, row.value);
  }
  // 没有可靠字段合同的渠道保持未知，不把常见字段名猜成必需条件。
  if (channel.required.some(name => !values.get(name))
      || channel.id === "tb" && !values.get("_m_h5_tk").split("_", 1)[0]) throw new CookieReadError("incomplete");
  return { header: [...values].map(([name, value]) => `${name}=${value}`).join("; "), count: values.size };
}
