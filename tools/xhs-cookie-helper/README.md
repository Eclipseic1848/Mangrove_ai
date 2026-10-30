# Mangrove 小红书登录助手

此工具仅在本机 Chrome 读取你明确同意提供的小红书登录态，然后由你复制、手动导入 Mangrove。它不接收密码，不自动上传，不恢复旧任务，也不保证登录态或采集永久有效。

## 安装与使用

1. 将下载的 ZIP 解压至本机固定目录。
2. 在 Chrome 地址栏输入 `chrome://extensions`，打开“开发者模式”，点击“加载已解压的扩展程序”，选择包含 `manifest.json` 的目录。
3. 在当前 Chrome 个人资料的普通窗口打开小红书官网，确认账号并正常登录、完成官网验证。密码只在官网输入。
4. 点击工具栏中的助手图标；按“官方登录 → 本机读取 → 手动导入”步骤操作，勾选明确同意后点击读取。
5. 点击“复制登录态”，返回 Mangrove 对应的小红书配置，手动粘贴、保存，再按现有流程验证。个人入口只维护本人；平台共享入口仅供超级管理员维护。
6. 粘贴后自行清理系统剪贴板。关闭助手窗口会清除助手内本次读取内容；下次使用需重新确认和读取。

Chrome 个人资料之间互相隔离。请在登录小红书的同一资料中安装和使用助手；不支持无痕窗口、多账号合并或分区 Cookie。若读取失败，按提示在官网确认账号；不要把密码或 Cookie 发给聊天、模型或他人。

## 数据与权限

- 仅申请 `cookies` 与小红书根域、`www` 两个精确站点的权限，不包含通配子域。HTTP/HTTPS 匹配用于读取域 Cookie 及非 Secure 字段；实际读取 URL 和官方登录链接固定为 `https://www.xiaohongshu.com/`。
- 没有后台程序、网页注入、消息桥、持久化存储、遥测或上传接口；CSP 禁止连接请求。
- 原始登录态只保存在打开的助手窗口内存中，不显示在页面或日志；只有用户明确点击复制时才写入剪贴板。
- Cookie 的存在和复制成功均不证明账号身份或搜索权限，真实验证沿用 Mangrove 的既有流程。
- 现有采集器不能表达的字段、过期/分区数据和同名冲突均拒绝导出，不改写或悄悄丢弃。

## 开发验证

在仓库根目录运行：`node --test scripts/tests/xhs_cookie_helper.test.mjs`。

此测试只使用合成 Cookie；不读取常用 Chrome，不访问真实平台。

官方依据：[Cookie API](https://developer.chrome.com/docs/extensions/reference/api/cookies)、[声明权限](https://developer.chrome.com/docs/extensions/develop/concepts/declare-permissions)、[扩展 CSP](https://developer.chrome.com/docs/extensions/reference/manifest/content-security-policy)。
