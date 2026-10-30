import { COOKIE_LOGIN_LINKS } from "@/lib/configGuides";

/** 助手只展示已有采集账号；身份和写入权限仍由原配置接口判定。 */
export const COOKIE_HELPER_CHANNELS = {
  mc_cookie_xhs: { name: "小红书", loginName: "小红书", logo: "xiaohongshu.png", loginUrl: COOKIE_LOGIN_LINKS.mc_cookie_xhs.url },
  mc_cookie_wb: { name: "微博", loginName: "微博", logo: "weibo.png", loginUrl: COOKIE_LOGIN_LINKS.mc_cookie_wb.url },
  mc_cookie_dy: { name: "抖音", loginName: "抖音", logo: "douyin.png", loginUrl: COOKIE_LOGIN_LINKS.mc_cookie_dy.url },
  mc_cookie_bili: { name: "B站", loginName: "B站", logo: "bilibili.png", loginUrl: COOKIE_LOGIN_LINKS.mc_cookie_bili.url },
  mc_cookie_zhihu: { name: "知乎", loginName: "知乎", logo: "zhihu.png", loginUrl: COOKIE_LOGIN_LINKS.mc_cookie_zhihu.url },
  mc_cookie_ks: { name: "快手", loginName: "快手", logo: "kuaishou.png", loginUrl: COOKIE_LOGIN_LINKS.mc_cookie_ks.url },
  mc_cookie_tieba: { name: "贴吧", loginName: "贴吧", logo: "tieba.png", loginUrl: COOKIE_LOGIN_LINKS.mc_cookie_tieba.url },
  jd_cookie: { name: "京东", loginName: "京东", logo: "jd.png", loginUrl: COOKIE_LOGIN_LINKS.jd_cookie.url },
  tb_cookie: { name: "淘宝/天猫", loginName: "淘宝", logo: "taobao.png", loginUrl: COOKIE_LOGIN_LINKS.tb_cookie.url },
  pdd_cookie: { name: "拼多多", loginName: "拼多多", logo: "pdd.png", loginUrl: COOKIE_LOGIN_LINKS.pdd_cookie.url },
} as const;

export type CookieHelperChannelKey = keyof typeof COOKIE_HELPER_CHANNELS;

export function isCookieHelperChannelKey(key: string): key is CookieHelperChannelKey {
  return Object.prototype.hasOwnProperty.call(COOKIE_HELPER_CHANNELS, key);
}
