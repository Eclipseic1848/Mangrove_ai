import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { CHANNELS, getChannel } from "../../tools/channel-cookie-helper/channels.mjs";
import { CookieReadError, prepareCookieHeader } from "../../tools/channel-cookie-helper/cookies.mjs";

const now = 1_800_000_000;
const row = (name, value, extra = {}) => ({ name, value, domain: ".weibo.cn", path: "/", hostOnly: false, session: true, ...extra });

test("10渠道精确业务目标保留各自导出合同", () => {
  assert.equal(CHANNELS.length, 10);
  assert.equal(getChannel("wb").cookieUrl, "https://m.weibo.cn/api/config");
  assert.deepEqual(prepareCookieHeader("wb", [row("sample", "synthetic")], now), { header: "sample=synthetic", count: 1 });
});

function valid(channel) {
  return (channel.required.length ? channel.required : ["sample"]).map(name => row(name, "synthetic-value", { domain: `.${channel.domains[0]}` }));
}
function rejects(id, rows, code) {
  assert.throws(() => prepareCookieHeader(id, rows, now), error => error instanceof CookieReadError && error.code === code && error.message === code);
}

for (const channel of CHANNELS) {
  test(`${channel.name}仅读取已冻结业务目标，不推断未知必需字段`, () => {
    const result = prepareCookieHeader(channel.id, valid(channel), now);
    assert.equal(result.count, valid(channel).length);
    assert.ok(result.header.includes("synthetic-value"));
    assert.ok(Object.isFrozen(channel) && Object.isFrozen(channel.domains));
  });
  test(`${channel.name}拒绝其他业务域和分区、过期、冲突值`, () => {
    rejects(channel.id, [...valid(channel), row("another", "synthetic-private", { domain: ".example.invalid" })], "domain");
    rejects(channel.id, valid(channel).map(item => ({ ...item, partitionKey: { topLevelSite: "https://example.invalid" } })), "partitioned");
    rejects(channel.id, valid(channel).map(item => ({ ...item, session: false, expirationDate: now })), "expired");
    const rows = valid(channel);
    rejects(channel.id, [...rows, { ...rows[0], value: "synthetic-conflict" }], "conflict");
  });
  test(`${channel.name}仅按当前采集器合同处理值内等号`, () => {
    const rows = valid(channel).map(item => ({ ...item, value: "synthetic=value==" }));
    if (channel.rejectEquals) rejects(channel.id, rows, "format");
    else assert.ok(prepareCookieHeader(channel.id, rows, now).header.includes("synthetic=value=="));
  });
}

test("业务路径按Cookie前缀边界匹配，支持适用非根路径", () => {
  assert.equal(prepareCookieHeader("wb", [row("sample", "synthetic", { path: "/api" })], now).count, 1);
  assert.equal(prepareCookieHeader("wb", [row("sample", "synthetic", { path: "/api/" })], now).count, 1);
  assert.equal(prepareCookieHeader("wb", [row("sample", "synthetic", { path: "/api/config" })], now).count, 1);
  for (const path of ["/ap", "/api/config/private", "/other", "api", ""]) rejects("wb", [row("sample", "synthetic", { path })], "domain");
});
test("host-only须精确适用业务host，同值字段去重但不跨域混合", () => {
  const first = row("sample", "synthetic");
  assert.equal(prepareCookieHeader("wb", [first, { ...first, domain: "m.weibo.cn", hostOnly: true }], now).count, 1);
  rejects("wb", [row("sample", "synthetic", { domain: "weibo.cn", hostOnly: true })], "domain");
  rejects("wb", [row("sample", "synthetic", { domain: ".m.weibo.cn", hostOnly: true })], "domain");
  rejects("wb", [row("sample", "synthetic", { domain: ".weibo.com" })], "domain");
  rejects("tb", [...valid(getChannel("tb")), row("sample", "synthetic", { domain: ".tmall.com" })], "domain");
});
test("不臆加其他渠道字段，已知会话和签名不足则提示范围不足", () => {
  rejects("xhs", [row("a1", "synthetic", { domain: ".xiaohongshu.com" })], "incomplete");
  rejects("zhihu", [row("z_c0", "synthetic", { domain: ".zhihu.com" })], "incomplete");
  rejects("tb", [row("sample", "synthetic", { domain: ".taobao.com" })], "incomplete");
  rejects("tb", [row("_m_h5_tk", "_timestamp", { domain: ".taobao.com" })], "incomplete");
  for (const id of ["wb", "dy", "bili", "ks", "tieba", "jd", "pdd"]) assert.equal(prepareCookieHeader(id, valid(getChannel(id)), now).count, 1);
});
test("保留适用的非Secure字段和有效持久Cookie", () => {
  assert.equal(prepareCookieHeader("wb", [row("sample", "synthetic", { secure: false, session: false, expirationDate: now + 1 })], now).count, 1);
  rejects("wb", [row("sample", "synthetic", { session: false })], "expired");
  rejects("wb", [row("sample", "synthetic", { expirationDate: Infinity })], "expired");
});
for (const value of ["a;b", "a\nb", "a\rb", "a b", "中文", "a,b", 'a"b', "a\\b", "\x00"]) test(`各渠道都拒绝不可表达字符${JSON.stringify(value)}`, () => {
  for (const channel of CHANNELS) rejects(channel.id, [...valid(channel), row("bad", value, { domain: `.${channel.domains[0]}` })], "format");
});
test("非法结构和字段名脱敏失败", () => {
  rejects("unknown", [], "unreadable");
  rejects("wb", {}, "unreadable");
  rejects("wb", [], "empty");
  rejects("wb", [null], "unreadable");
  rejects("wb", [row("injected;name", "synthetic")], "format");
});
test("统一包只声明Cookie权限和逐渠道固定可选host，没有后台或网络能力", () => {
  const manifest = JSON.parse(readFileSync(new URL("../../tools/channel-cookie-helper/manifest.json", import.meta.url), "utf8"));
  assert.deepEqual(manifest.permissions, ["cookies"]);
  assert.deepEqual(manifest.optional_host_permissions, [...new Set(CHANNELS.flatMap(channel => channel.origins))]);
  assert.ok(manifest.optional_host_permissions.every(origin => /^\*:\/\/[a-z0-9.-]+\/\*$/.test(origin)));
  assert.equal(manifest.incognito, "not_allowed");
  assert.match(manifest.content_security_policy.extension_pages, /connect-src 'none'/);
  for (const key of ["host_permissions", "background", "content_scripts", "externally_connectable", "web_accessible_resources", "optional_permissions"]) assert.equal(manifest[key], undefined);
});
