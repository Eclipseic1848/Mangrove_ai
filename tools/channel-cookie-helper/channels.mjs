const definitions = [
  { id: "xhs", name: "小红书", image: "xiaohongshu.png", loginUrl: "https://www.xiaohongshu.com/", cookieUrl: "https://www.xiaohongshu.com/", domains: ["xiaohongshu.com", "www.xiaohongshu.com"], required: ["a1", "web_session"], rejectEquals: true, note: "会话与签名字段齐全也不代表搜索、详情或评论已放行。" },
  { id: "wb", name: "微博", image: "weibo.png", loginUrl: "https://m.weibo.cn/", cookieUrl: "https://m.weibo.cn/api/config", domains: ["weibo.cn", "m.weibo.cn"], required: [], rejectEquals: true, note: "采集使用微博移动业务域；仅在 weibo.com 登录不代表本业务域已有可用登录态。" },
  { id: "dy", name: "抖音", image: "douyin.png", loginUrl: "https://www.douyin.com/", cookieUrl: "https://www.douyin.com/aweme/v1/web/user/profile/self/", domains: ["douyin.com", "www.douyin.com"], required: [], rejectEquals: true, note: "业务请求还依赖浏览器环境与签名；读取 Cookie 不代表搜索已可用。" },
  { id: "bili", name: "B站", image: "bilibili.png", loginUrl: "https://www.bilibili.com/", cookieUrl: "https://api.bilibili.com/x/web-interface/nav", domains: ["bilibili.com", "api.bilibili.com"], required: [], rejectEquals: true, note: "读取适用于账号接口的登录态；WBI 签名和业务访问仍需原流程验证。" },
  { id: "zhihu", name: "知乎", image: "zhihu.png", loginUrl: "https://www.zhihu.com/", cookieUrl: "https://www.zhihu.com/api/v4/me", domains: ["zhihu.com", "www.zhihu.com"], required: ["d_c0"], rejectEquals: true, note: "当前身份请求签名需要 d_c0；字段存在不能证明账号有效或业务放行。" },
  { id: "ks", name: "快手", image: "kuaishou.png", loginUrl: "https://www.kuaishou.com/new-reco", cookieUrl: "https://www.kuaishou.com/rest/v/profile/get", domains: ["kuaishou.com", "www.kuaishou.com"], required: [], rejectEquals: false, note: "账号验证使用官网产生的同域请求；此处仅读取，不访问账号接口或推荐内容。" },
  { id: "tieba", name: "贴吧", image: "tieba.png", loginUrl: "https://tieba.baidu.com/", cookieUrl: "https://tieba.baidu.com/dc/common/tbs", domains: ["baidu.com", "tieba.baidu.com"], required: [], rejectEquals: true, note: "当前验证接口没有稳定账号 ID，不用此读取结果证明同账号恢复旧任务。" },
  { id: "jd", name: "京东", image: "jd.png", loginUrl: "https://home.m.jd.com/", cookieUrl: "https://me-api.jd.com/user_new/info/GetJDUserInfoUnion", domains: ["jd.com", "me-api.jd.com"], required: [], rejectEquals: false, note: "读取适用于京东身份接口的字段；账号及商品、评论权限仍未验证。" },
  { id: "tb", name: "淘宝/天猫", image: "taobao.png", loginUrl: "https://www.taobao.com/", cookieUrl: "https://h5api.m.taobao.com/h5/mtop.user.getusersimple/1.0/", domains: ["taobao.com", "m.taobao.com", "h5api.m.taobao.com"], required: ["_m_h5_tk"], rejectEquals: false, note: "本次读取淘宝身份业务域；不混合天猫域 Cookie，淘宝身份也不代表天猫访问已验证。" },
  { id: "pdd", name: "拼多多", image: "pdd.png", loginUrl: "https://mobile.yangkeduo.com/", cookieUrl: "https://mobile.yangkeduo.com/user_setting.html", domains: ["yangkeduo.com", "mobile.yangkeduo.com"], required: [], rejectEquals: false, note: "现有页面探测不能可靠证明账号身份，读取后仍保持尚未确认。" },
];

export const CHANNELS = Object.freeze(definitions.map(channel => Object.freeze({
  ...channel,
  domains: Object.freeze(channel.domains),
  required: Object.freeze(channel.required),
  origins: Object.freeze(channel.domains.map(domain => `*://${domain}/*`)),
})));

export function getChannel(id) {
  return CHANNELS.find(channel => channel.id === id) || null;
}
