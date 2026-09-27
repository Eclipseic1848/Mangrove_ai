# 平台登录态验证边界

`POST /api/config/verify` 的十个 Cookie 目标保留 `ok`、`detail`，并返回 `verification`：

```json
{
  "scope": "identity",
  "status": "valid",
  "reason": "authenticated",
  "operations": "not_checked"
}
```

`valid` 只说明本次独立身份探测取得登录证据。搜索、详情、评论及其他业务操作没有随身份探针验证，不能据此认为账号拥有所有操作权限。非 Cookie 验证目标不附加这个对象。

## 当前探针能力

| 平台 | 配置键 | 独立身份验证 |
| --- | --- | --- |
| 小红书 | mc_cookie_xhs | 身份接口；不依赖共享 Chrome/CDP 登录态 |
| B站 | mc_cookie_bili | 身份接口 |
| 微博 | mc_cookie_wb | 身份接口 |
| 抖音 | mc_cookie_dy | 未接通，返回 unknown |
| 快手 | mc_cookie_ks | 未接通，返回 unknown |
| 知乎 | mc_cookie_zhihu | 未接通，返回 unknown |
| 贴吧 | mc_cookie_tieba | 未接通，返回 unknown |
| 京东 | jd_cookie | 页面探测；无已登录身份证据时返回 unknown |
| 淘宝 | tb_cookie | 页面探测；无已登录身份证据时返回 unknown |
| 拼多多 | pdd_cookie | 页面探测；无已登录身份证据时返回 unknown |

表中描述代码能力，不代表每个平台均已完成真实账号验收。空结果、公开页面、HTTP 200、网络错误都不能作为已登录证据。

## 故障与权限

- 明确未登录：`invalid / login_required`，由连接持有人正常重新认证。
- 429：`unknown / rate_limited`；403：`unknown / access_denied`。拒绝访问可能来自挑战或权限限制，不能直接声称 Cookie 过期。
- 缺凭证、采集器不可用、未支持身份探针、网络或未知异常分别保留机器原因；未识别原因降为 `probe_failed`，不透传第三方任意字符串。
- 管理员验证共享配置；普通用户验证实际生效的个人覆盖或共享配置。个人凭证失败不自动切换共享身份。
- 共享健康记录绑定凭证版本与探测环境摘要；轮换、环境变化或无绑定旧记录需要重新验证。摘要不是原始凭证，公开响应不包含摘要或账号标识。

重新认证、确认同一账号、采用新凭证恢复任务属于单独的恢复流程。更新 Cookie 不自动授权重放结果未知的调用，也不改变已冻结来源与正式交付。

## 回归

```powershell
python -m pytest tests/test_cookie_verification_response.py tests/test_cookie_health_contract.py tests/test_cookie_health_binding.py tests/test_platform_config_services.py -q
```

合成探针测试验证分类、角色路径、迟到结果拒收和响应边界；不能替代真实平台身份、操作权限或同账号重新认证验收。

## 实际采集操作结果

MediaCrawler普通采集在coverage.operations分别记录search/detail/comments的本次结果，coverage.connection只提供平台代号与personal/platform/unconfigured来源，不暴露凭证或账号。评论完整度需要明确的覆盖事实，帖子成功不能证明评论完整或账号已认证。

既有采集错误诊断附白名单reason/next_action；证据采集的发现、评论批次保留这些字段，collection_outcome仅将白名单下一步投影到用户提示。诊断仍受上游错误信息质量限制，不是所有故障均可精确归因。不会自动换账号、恢复旧进度或重放结果未知的调用。

## 验证后更新凭证

个人采集账号和平台共享账号的编辑框新增“验证并更新”，原保存入口保留。接口`POST /api/config/reauthenticate`接收`key`、`value`和`scope`（personal/platform）；平台作用域仅管理员可写。新值只在独立探针内使用，认证成功后按旧SecretRef版本原子替换；失败、未知、取消不写入，验证期间版本或环境变化返回409。

响应的saved表示本次已保存，不证明旧任务已恢复或新旧凭证为同一账号；原任务凭证绑定及未知调用禁止重放规则保持。网络响应未知时应重新读取配置核对，不能自动重试。未支持身份探针的平台不能使用此入口强行标绿，可继续使用原手动配置功能。
