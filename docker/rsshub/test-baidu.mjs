import assert from 'node:assert/strict';
import {mock, test} from 'node:test';

const resultHtml = '<div id="content_left"><div class="c-container"><h3><a href="https://example.com/python">Python</a></h3><div class="c-row">公开测试正文</div></div></div>';
let staticHtml, opened, closed, timeout;
mock.module('/app/dist/got-BRwyCRBX.mjs', {namedExports: {t: async () => ({data: staticHtml})}});
mock.module('/app/dist/cache-C4L8srDo.mjs', {namedExports: {t: {tryGet: async (_key, callback) => callback()}}});
mock.module('/app/dist/playwright-DQym1rQm.mjs', {namedExports: {t: async () => {
    opened++;
    return {page: {
        locator: () => ({first: () => ({waitFor: async ({timeout: limit}) => {
            assert.equal(limit, 10000);
            if (timeout) throw Error('仍是验证页');
        }})}),
        content: async () => resultHtml,
    }, destroy: async () => { closed++; }};
}}});
const {route} = await import('/app/dist/baidu-search-CHVDCYIY.mjs');
const context = {req: {param: () => 'Python'}};

for (const useBrowser of [false, true]) {
    await test(useBrowser ? '空静态响应使用浏览器取得结果并关闭' : '正常静态结果不启动浏览器', async () => {
        staticHtml = useBrowser ? '<title>百度安全验证</title>' : resultHtml;
        opened = closed = 0;
        timeout = false;
        const feed = await route.handler(context);
        assert.equal(feed.item.length, 1);
        assert.equal(feed.item[0].link, 'https://example.com/python');
        assert.match(feed.item[0].description, /公开测试正文/);
        assert.equal(opened, Number(useBrowser));
        assert.equal(closed, Number(useBrowser));
    });
}
await test('浏览器仍无结果时失败且关闭，不生成成功 feed', async () => {
    staticHtml = '<title>百度安全验证</title>';
    opened = closed = 0;
    timeout = true;
    await assert.rejects(route.handler(context), /仍是验证页/);
    assert.equal(opened, 1);
    assert.equal(closed, 1);
});
