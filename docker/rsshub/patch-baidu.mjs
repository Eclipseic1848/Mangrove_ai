import {createHash} from 'node:crypto';
import {readFileSync, writeFileSync} from 'node:fs';

const path = '/app/dist/baidu-search-CHVDCYIY.mjs';
const source = readFileSync(path, 'utf8');
// 只修改已核验的发行文件，升级 RSSHub 后须重新核对，避免误改其他版本。
if (createHash('sha256').update(source).digest('hex') !== '658f8753fc9934adcbf547e9b983485cfd13e3f86043ce6c03cc0b57f32fd758') {
    throw Error('RSSHub 百度路由版本不匹配');
}
const before = 'let e=(await n(a)).data,t=new Set,r=o(e);';
const after = `let e=(await n(a)).data;
// 静态响应无结果时才用浏览器；不自动操作验证码，不把验证页当正文。
if (!o(e)('#content_left .c-container h3 a').length) {
    const {page, destroy} = await openBaiduBrowser(a);
    try {
        await page.locator('#content_left .c-container h3 a').first().waitFor({timeout: 10000});
        e = await page.content();
    } finally {
        await destroy();
    }
}
let t=new Set,r=o(e);`;
if (source.split(before).length !== 2) throw Error('RSSHub 百度路由补丁位置不匹配');
writeFileSync(path, 'import {t as openBaiduBrowser} from "./playwright-DQym1rQm.mjs";\n'
    + source.replace(before, after), 'utf8');
