<p align="center">
  <strong>Mangrove（红树林）</strong><br>
  <sub>统一数据任务平台</sub>
</p>

<p align="center">
  <a href="./README.md">README</a> ·
  <a href="./CONTRIBUTING.md">参与贡献</a> ·
  <a href="./CODE_OF_CONDUCT.md">行为准则</a> ·
  <a href="./SECURITY.md">安全策略</a> ·
  <a href="./THIRD_PARTY_NOTICES.md">第三方许可</a> ·
  <a href="./LICENSE">MIT License</a>
</p>

---

# 安全策略

## 支持范围

当前发行基线为 `v1.0.0`。安全修复优先作用于默认分支 `main`，需要更新发行版时发布新的补丁版本；
不修改已经发布的标签或替换同名制品。旧开发快照和本机实验版本不保证单独维护。
版本发布不代表已完成部署环境的生产安全验收，部署者仍需完成本环境的验证与运维交接。

## v1.0.0 的依赖处理范围

发布前将主服务 AnyIO 升至 4.14.2、pypdf 升至 6.16.1、采集分组 Protego 升至 0.6.2。
对应公开公告为 [AnyIO TLS 域名校验](https://github.com/agronholm/anyio/security/advisories/GHSA-82r6-8w77-94w6)、
[pypdf 资源消耗](https://github.com/py-pdf/pypdf/security/advisories/GHSA-23w6-3w8w-8484) 和
[Protego robots 规则匹配](https://github.com/advisories/GHSA-wjmf-p669-5m5p)。
随包 Pi 镜像也使用 pypdf 6.16.1；新构建标签 `0.87.1-v1.0.0` 不覆盖原机的旧冻结镜像。

GitHub 仍有前端构建依赖、离线 promptfoo 评测依赖及 React Router 告警。本版不把评测依赖安装进
主服务；前端采用客户端渲染，登录返回地址已有站内校验，但不据此关闭全部路由告警。
后续升级须验证具体调用路径，版本发布不表示仓库或所有镜像已经取得“零漏洞”结论。

## 私密报告漏洞

请使用 GitHub 仓库的 **Security → Report a vulnerability** 私密报告，不要在公开 Issue、
Discussion、PR 或日志中披露漏洞细节、利用代码、用户数据或凭据。

报告尽量包含：

- 受影响版本、提交和组件；
- 可重复的最小步骤与前置条件；
- 实际影响和可利用边界；
- 已知缓解措施；
- 必要时提供已脱敏的日志或示例。

维护者会尽快确认收到报告；修复时间取决于影响、复现难度和安全发布协调。在修复公开前，
请给维护者合理的协调披露时间。

## 特别敏感的边界

以下问题应优先私密报告：Owner/权限隔离绕过、模型或采集凭据泄露、SSRF/内网访问、任务级
容器逃逸、Docker Socket 暴露、验证或发布门绕过、Candidate 冒充正式 Delivery、跨任务制品
读取、审计记录篡改以及任意代码执行。

若你误提交了真实凭据，应立即撤销和轮换；仅从 Git 历史删除不足以使其失效。

## 安全使用说明

- 本项目默认面向本地学习与研究环境，并非已完成生产安全认证的托管服务。
- 不要把 `.env`、Cookie、数据库、用户上传、运行日志、个人偏好或浏览器登录态提交到仓库。
- 公网部署前必须重新评估鉴权、TLS、网络出口、数据保留、备份与恢复策略。
- MediaCrawler 仅限其许可证允许的非商业学习用途；采集行为必须遵守目标站点规则和适用法律。
