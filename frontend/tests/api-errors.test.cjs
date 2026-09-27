// 通过真实 API 客户端观察错误；传输边界使用内存响应，不访问网络。
const { test } = require('node:test');
const assert = require('node:assert/strict');
const vm = require('node:vm');
const { join } = require('node:path');
const { buildSync } = require('esbuild');

const root = join(__dirname, '..');
const code = buildSync({
  entryPoints: [join(root, 'src/lib/api.ts')],
  alias: { '@': join(root, 'src') },
  bundle: true, format: 'cjs', platform: 'node', write: false,
}).outputFiles[0].text;

for (const [name, body, expected] of [
  ['字符串', { detail: '请缩短搜索内容' }, '请缩短搜索内容'],
  ['校验数组', { detail: [{ msg: '搜索内容不能超过200字', input: 'private-input' }] }, '搜索内容不能超过200字'],
  ['错误对象', { detail: { message: '配置已变化，请刷新', input: 'private-input' } }, '配置已变化，请刷新'],
  ['无说明对象', { detail: { input: 'private-input' } }, null],
  ['空响应', null, null],
  ['损坏JSON', '{', null],
]) {
  test(`${name}错误保留HTTP状态且不回显输入`, async () => {
    const module = { exports: {} };
    vm.runInNewContext(code, {
      module, exports: module.exports, Headers, Response, BroadcastChannel: undefined,
      fetch: async () => new Response(typeof body === 'string' ? body : JSON.stringify(body), {
        status: 422, headers: { 'Content-Type': 'application/json' },
      }),
    });
    await assert.rejects(module.exports.api.get('/api/validation'), error => {
      assert.equal(error.constructor.name, 'ApiError');
      assert.equal(error.status, 422);
      assert.doesNotMatch(error.message, /private-input|replace is not/);
      if (expected) assert.equal(error.message, expected);
      else assert.match(error.message, /请求.*422/);
      return true;
    });
  });
}
