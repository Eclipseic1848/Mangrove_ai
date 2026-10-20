# DeepSeek 独立网页搜索与抓取

`SearchDiscoveryCollector` 支持官方独立 Search / Fetch API。它提供公开网页候选和第三方提取正文；项目筛选、来源核验、数量检查及文件输出仍由现有流程负责。

## 配置

- 默认 `SEARCH_PROVIDER=auto` 保持原有渠道和顺序，不自动增加 DeepSeek 付费调用。
- 管理员可通过运行时配置的“搜索渠道”选择 `deepseek`，或设置 `SEARCH_PROVIDER=deepseek`。
- 复用平台的 `DEEPSEEK_API_KEY`，不使用任务选中的个人模型连接或个人 Key。
- 平台 `DEEPSEEK_BASE_URL` 必须为 `https://api.deepseek.com` 或其 `/v1` 根地址。自定义网关的凭据不发送至官方搜索接口。
- 搜索渠道与分析模型独立；使用此渠道无需切换至 Pro，也不启用模型的服务端 `web_search`。

显式 `deepseek` 模式不混用旧搜索后端。移回 `auto` 即恢复原有渠道；变更只影响下一次采集，当前采集固定渠道及凭据。

## 范围与失败

搜索关键词和站点约束沿用原任务；每次返回最多50条候选，总候选与时间预算继续受原任务约束。搜索摘要、日期和权威性分数仅供发现，不能直接证明业务达标。Search 发现网址后由 Fetch 获取正文。

Fetch 只发送无凭证、无敏感查询参数的公开 HTTP/S 网址，并检查本机解析的全部地址。供应商内部的 DNS、重定向与提取过程无法由本机固定；`source_representation=provider_markdown` 明确表示供应商返回的 Markdown，不代表原站 HTML 或原始 HTTP 响应。保留现有范围、挑战页、重复正文及业务核验，不把登录平台、评论分页或图片 OCR 视为已替代。

请求不自动重试；超时或网络结果未知如实保留。取消与权限撤销继续经过执行安全点。

项目字段核验兼容 Markdown 数字小数点及日期分隔符的单个转义，保存的引用仍保持原文。明确的金额单位冲突继续拒绝；父批次、其他标段及合计不能冒充独立子项目金额。发布时间范围和截止日期检查使用同样受限的日期兼容规则。

## 用量

`CollectResult.coverage.deepseek` 保存脱敏的逐请求状态、耗时、成功且非空的调用次数，以及计费未知标记；不保存 Key，不把候选条数当调用次数，不输出未经核对的账单金额。

费用对比须区分供应商原生账单、按当日公开单价估算和未知费用；模型分析费用另计。公开价格和接口约束以 [DeepSeek 官方指南](https://api-docs.deepseek.com/zh-cn/guides/search/) 为准。

## 回归

```text
python -m pytest tests/test_deepseek_search_fetch.py tests/test_deepseek_collector.py -q
```

真实对比应固定同一任务规格、模型、日期窗、候选预算和核验流程，在独立目录与数据库副本执行，不重写原客户任务或历史失败结果。
