import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {CookieReadError, ERROR_TEXT, prepareCookieHeader} from "../../tools/xhs-cookie-helper/cookies.mjs";

const now = 1_800_000_000;
const row = (name, value, extra = {}) => ({name, value, domain: ".xiaohongshu.com", path: "/", hostOnly: false, session: true, ...extra});
const valid = () => [row("web_session", "synthetic-session"), row("a1", "synthetic-signature")];
const rejects = (rows, code) => assert.throws(() => prepareCookieHeader(rows, now), (error) => error instanceof CookieReadError && error.code === code && error.message === code);

test("输出与现有导入格式一致，不改写合成凭据", () => {
  assert.deepEqual(prepareCookieHeader(valid(), now), {header: "web_session=synthetic-session; a1=synthetic-signature", count: 2});
});
test("允许官网host-only与未到期会话，同值重复仅导出一份", () => {
  const rows = valid();
  rows.push(row("a1", "synthetic-signature", {domain: "www.xiaohongshu.com", hostOnly: true}));
  rows.push(row("timestamp", "123", {session: false, expirationDate: now + 10}));
  assert.equal(prepareCookieHeader(rows, now).count, 3);
});
test("保留可随HTTPS请求发送的非Secure同域字段", () => {
  const rows = valid();
  rows[1].secure = false;
  assert.equal(prepareCookieHeader(rows, now).count, 2);
});
for (const [name, rows, code] of [
  ["空数据", [], "empty"], ["非法结构", {}, "unreadable"], ["非法字段结构", [null], "unreadable"],
  ["伪装域", [...valid(), row("other", "secret", {domain: "evilxiaohongshu.com"})], "domain"],
  ["其他子域", [...valid(), row("other", "secret", {domain: "edith.xiaohongshu.com"})], "domain"],
  ["其他路径", [...valid(), row("other", "secret", {path: "/private"})], "domain"],
  ["不适用的host-only", [...valid(), row("other", "secret", {hostOnly: true})], "domain"],
  ["已过期", [...valid(), row("other", "secret", {session: false, expirationDate: now})], "expired"],
  ["有效期未知", [...valid(), row("other", "secret", {session: false})], "expired"],
  ["非法有效期", [...valid(), row("other", "secret", {expirationDate: NaN})], "expired"],
  ["分区字段", [...valid(), row("other", "secret", {partitionKey: {topLevelSite: "https://example.com"}})], "partitioned"],
  ["同名冲突", [...valid(), row("a1", "different-secret")], "conflict"],
  ["缺会话", [row("a1", "synthetic")], "incomplete"],
  ["缺签名", [row("web_session", "synthetic")], "incomplete"],
  ["空必需值", [row("a1", ""), row("web_session", "synthetic")], "incomplete"],
]) test(`拒绝${name}且错误不含字段原值`, () => rejects(rows, code));
for (const value of ["a=b", "a;b", "a\nb", "a\rb", "a b", "中文", "a,b", 'a"b', "a\\b", "\x00"]) {
  test(`拒绝不可表达字符${JSON.stringify(value)}`, () => rejects([...valid(), row("other", value)], "format"));
}
test("拒绝注入字段名，消息表只有固定文案", () => {
  rejects([...valid(), row("evil;name", "value")], "format");
  assert.equal(Object.keys(ERROR_TEXT).length, 8);
  assert.ok(Object.values(ERROR_TEXT).every((text) => !text.includes("synthetic")));
});
test("清单权限最小、无后台或网页桥、禁止网络连接", () => {
  const manifest = JSON.parse(readFileSync(new URL("../../tools/xhs-cookie-helper/manifest.json", import.meta.url), "utf8"));
  assert.deepEqual(manifest.permissions, ["cookies"]);
  assert.deepEqual(manifest.host_permissions, ["*://xiaohongshu.com/*", "*://www.xiaohongshu.com/*"]);
  assert.equal(manifest.incognito, "not_allowed");
  assert.match(manifest.content_security_policy.extension_pages, /connect-src 'none'/);
  for (const key of ["background", "content_scripts", "externally_connectable", "web_accessible_resources", "optional_permissions"]) assert.equal(manifest[key], undefined);
});
