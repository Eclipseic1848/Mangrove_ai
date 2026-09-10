const assert = require('node:assert/strict');
const { mkdtempSync, writeFileSync, rmSync } = require('node:fs');
const { tmpdir } = require('node:os');
const path = require('node:path');
const { test } = require('node:test');

const moduleRoot = path.resolve(process.argv[2]);
const browserslist = require(path.join(moduleRoot, 'browserslist'));
const version = require(path.join(moduleRoot, 'browserslist/package.json')).version;
console.log(`browserslist ${version}`);

// 只在自建临时目录放合成统计文件，不写项目或用户配置。
for (const key of ['__proto__', 'toString', 'valueOf', 'hasOwnProperty', 'constructor', 'isPrototypeOf']) {
  test(`统计键 ${key} 不得使真实构建查询崩溃或改写原型`, () => {
    const dir = mkdtempSync(path.join(tmpdir(), 'mangrove-browserslist-'));
    const before = Object.getOwnPropertyDescriptors(Object.prototype);
    try {
      writeFileSync(path.join(dir, 'browserslist-stats.json'), JSON.stringify({ [key]: { onekey: 5 }, chrome: { '100': 50 } }), 'utf8');
      browserslist.clearCaches();
      const result = browserslist('defaults', { path: dir });
      assert.ok(result.length > 0);
      assert.deepEqual(Object.getOwnPropertyDescriptors(Object.prototype), before);
    } finally {
      browserslist.clearCaches();
      assert.equal(path.dirname(path.resolve(dir)), path.resolve(tmpdir()));
      assert.ok(path.basename(dir).startsWith('mangrove-browserslist-'));
      rmSync(dir, { recursive: true });
    }
  });
}

test('已有 Babel 与 Autoprefixer 使用同一安全版本，正常查询和前缀生成仍可用', async () => {
  const { createRequire } = require('node:module');
  const autoprefixerRequire = createRequire(path.join(moduleRoot, 'autoprefixer/package.json'));
  const babelRequire = createRequire(path.join(moduleRoot, '@babel/helper-compilation-targets/package.json'));
  assert.equal(autoprefixerRequire('browserslist/package.json').version, version);
  assert.equal(babelRequire('browserslist/package.json').version, version);
  assert.ok(browserslist('last 2 Chrome versions').length >= 2);
  const postcss = require(path.join(moduleRoot, 'postcss'));
  const autoprefixer = require(path.join(moduleRoot, 'autoprefixer'));
  const output = await postcss([autoprefixer({ overrideBrowserslist: ['Safari 8'] })]).process('.box { user-select: none; display: flex; }', { from: undefined });
  assert.match(output.css, /-webkit-user-select/);
});
