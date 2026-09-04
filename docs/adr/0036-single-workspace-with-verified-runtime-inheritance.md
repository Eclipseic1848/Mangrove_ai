# ADR-0036：单一工作台继承已验证 Runtime 与 Harness 能力

- 状态：`accepted`，用户于 2026-08-30 明确确认
- 日期：2026-08-30
- 决策来源：GitHub Issue #85
- 部分取代：ADR-0018、ADR-0019、ADR-0035 中为未上线历史任务、旧报告、旧 Delivery 和旧入口维持兼容迁移的要求

Mangrove 尚未最终上线，因此不迁移未上线历史记录，也不把 `/chat`、“分析报告（旧）”、
Legacy Runtime 或只为兼容存在的资产作为新平台完成门。新任务只使用统一数据工作台和一个
SemanticTaskLifecycle；真实本地数据库与文件在未取得不可逆清理授权前只停止读取，不自动删除。

统一不表示重写已经验证有效的智能能力。SemanticTaskLifecycle 只协调创建修订、启动、暂停、
恢复、取消、重试、验证和发布，并继续调用独立的 Source、ConversationSteering、AgentKernel、
CandidateVerification 与 DeliveryPublishing Module。CoreMind 是主 Agent Runtime；Pi、
coding-agent、CapabilityHost 继续提供已验证的工具执行能力；DeepSeek Harness 与现有 Harness
保留成功任务策略、测试和独立验证；Codex/Grok 只提供可公开复用的交互模式参考。

所有 Runtime 与 Harness 能力必须通过 Adapter 或已有共享 Module 接入同一主链。用户冻结哪个
模型就使用哪个模型，不允许静默切换。所谓“最新”只指通过契约测试并冻结 commit、协议、依赖
与制品 digest 的最新候选，不自动跟随浮动 `main`，也不建立四套平行任务系统。

该决定减少未上线兼容负担，但要求删除旧入口或废代码前证明统一工作台具备对应用户能力；生产
数据、Secret、备份和其他不可逆清理仍是独立人工门。
