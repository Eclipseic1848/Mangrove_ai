# #125 自然语言起点与运行时交接契约

状态：隔离契约；生产接线由后续工单完成。对应 #125，基线 126ec1eab249bad9a543ea4ffe84eac2324992ab。

## 目标与交付边界

用户只输入需求时即可获得交互身份，不要求先上传文件或输入网址。本票交付可执行的身份/状态契约、现有固定 CoreMind 制品兼容证据及接线约束；不添加生产 API、数据库或第二个智能体执行循环。

允许输入：Owner、非空需求、本人所选模型连接ID/版本/精确模型；正式授权和封存已完成的来源身份；已开始调用的回执和查询事实。
输出：TaskSession 快照、唯一冻结交接事实、同会话用量汇总。回归入口为 tests/test_prerun_contract.py。

## 身份与权威

TaskSession 是交互身份，先于正式 TaskRevision/Run。现有 WorkSession 仍表示一个 Run 的工作窗口，不能拿它冒充无来源的前置身份。
前置阶段不调用要求非空来源的 WorkspaceTaskCreateIn/PiRuntimeRequest，不构造假 Revision。未来一次交互可关联多个修订；本票隔离样例只覆盖一次正式交接，后续修订由生产聚合根建立映射。

ModelSelection 固定 connection_id、version、model。每次已开始调用带同一模型身份及稳定 call_id；禁止静默换连接、换模型或重复开始同一调用。切换模型应形成明确的新执行选择，不能修改历史绑定；具体页面由 #159/#127 接线。

## 状态与恢复

| 操作 | 前置条件 | 结果 |
|---|---|---|
| 提交/澄清 | 非空需求、Owner/模型身份 | submitted/clarifying，无 Run |
| 准备来源 | submitted/clarifying | preparing，无冻结 Revision |
| 冻结交接 | preparing、同 Owner 活跃、已授权非空且不重复来源、有效任务/修订/Run | frozen，不可二次冻结 |
| 暂停/恢复 | 同 Owner、明确可恢复状态 | 保留原阶段和同一身份 |
| 结果未知 | 属于已开始调用 | unknown；禁止新调用和普通恢复 |
| 查询解除未知 | 同 call_id 的明确 completed/failed 事实 | 返回原阶段；绝不自动重发 |
| 取消 | 同 Owner | cancelled；禁止新增执行，已开始调用的迟到用量仍可记账 |
| 重启恢复 | 完整版本化快照及预期 Owner/模型/Session | 校验后恢复同状态；未知/取消不自动复活 |

原型接收的 account_active 和 authorized_snapshot_ids 是授权结果事实，不是授权实现。生产接线必须在原有聚合事务中重新验证账号、来源所有权/授权、来源版本及执行许可，再原子封存来源和建立 RuntimeBinding。不得以传入布尔值替代生产鉴权。
JSON 校验保证结构、身份和状态一致，不证明磁盘可信；生产持久化必须沿用受 Owner 隔离的存储及并发控制，不能接受客户端回传快照作为权威。

## 用量

前置和执行调用以 purpose 区分，同 Session 汇总。稳定 call_id 去重，同一调用冲突回执拒绝；取消后迟到回执仍归原 Session。known_tokens 仅为已知下限，unknown_calls 和 pending_calls 分列，未知不当零。
本票只验证总 Token 的最小账本，输入/输出/cache/价格等明细复用现有 Provider 归一化与 #128 投影，不能据原型计算实际账单。

## CoreMind 薄适配

复用 ADR0035 的唯一 AgentKernel 接缝及 CoreMindAgentKernelAdapter。精确制品身份由 coremind_runtime.py 常量和 prepare_manifest 核实：0.7.1，源码75b706a20ca4cdddef71cbcc0dd90b8b424ddd99，Protocol 2.0，指纹sha256:94c8e093979be73a13ecc1090167454567d0602a70b065ceffeed4cb1eca4ce3。
wheel、实际 SDK 树、worker 和 manifest 摘要须同时匹配；不能只核版本号。运行时额外绑定 execution-contract 和 capability digest，恢复不得绕过。

现有固定 Worker 验证入口为 tests/test_coremind_worker_contract.py；真实 Adapter 工具/检查点纵切面为 tests/test_coremind_agent_kernel_adapter.py::test_locked_worker_executes_real_tools_and_host_verification。使用已安装的固定 SDK、Node、临时目录和回环虚构模型响应，无真实账户、无相邻 CoreMind checkout。
固定 Worker 缺失原生用量时会产生 synthetic zero；现有 Mangrove 事件归一化必须保留 unknown，不能按零计费。

前置无来源澄清、搜索规划尚无生产执行请求；该缺口在 Mangrove 的 Session 与 API/Broker 接线，不意味着需要重建或更换 CoreMind。现有生产 Adapter 只接受冻结请求，本票不放宽它。若固定制品控制方法在实测中失败，须记录精确方法/制品和缺口，不能用模拟成功替代或升级上游掩盖。

### 已核实的控制缺口

固定制品实际拒绝 control(type=pause)，返回 protocol_validation_failed；availableControls 也不声明 pause。
新增 test_host_timeout_resumes_same_run_then_applies_cancel_control 实测宿主验收超时形成 paused，resume_run 恢复同 Run 和同验收请求，模型仍只调用一次；cancel 返回 applied，最终 finished/aborted，控制事件 accepted→applied 及 appliedSequence 对应一致。
这证明特定等待状态的暂停恢复与取消，不证明用户随时暂停。#127/#130 不得把取消重启显示成暂停恢复；若需该入口，先在上游补精确 Protocol 控制/持久回执/能力声明，再锁定新制品并复验。当前界面可提供已验证的停止及明确等待状态恢复，不静默降级。

## 验收与后续

- 契约验证：无来源提交到准备、冻结拒绝、Owner/模型隔离、暂停取消、同调用未知查询、迟到用量去重和完整快照恢复。
- 固定制品验证：prepare_manifest 身份门、模型不切换、工具、事件游标、宿主候选验证、检查点/副作用回执；控制实测与模拟测试分别记录。
- #126 固化外部只读边界；#159 完成模型配置；#127/#130 接前置自然语言产品链；#128 统一用量/恢复展示。真实服务、用户体验和生产资格不得由本票隔离通过推导。

## 2026-09-07 本机隔离验证证据

| 验证 | 结果 |
|---|---|
| 前置契约最终回归 | 10 passed，17 subtests passed |
| 前置/AgentKernel/CoreMind事件/Adapter/真实Worker组合 | 105 passed，17 subtests passed，45.35秒，零跳过 |
| minimum-ci选定的快速组合 | 65 passed，17 subtests passed，1 deselected（既有collectors导入重门），15.93秒 |
| UTF-8 | 1320个已跟踪文件通过；新增中文文件额外检查 |

运行时组合显式启用 MANGROVE_COREMIND_ADAPTER_TEST、MANGROVE_COREMIND_TEST、MANGROVE_COREMIND_HOST_VERIFICATION_TEST。
已有完整依赖解释器通过单进程 sys.path 追加已有固定 SDK 安装位置；没有安装或升级依赖。执行 pytest -q tests/test_prerun_contract.py tests/test_agent_kernel.py tests/test_coremind_events.py tests/test_coremind_agent_kernel_adapter.py tests/test_coremind_worker_contract.py --randomly-seed=0。
对应本机 JUnit 为 runtime-final.xml（122 testcase含17子场景）及fast.xml（82 testcase含17子场景），二者零failure/error/skip；旧runtime.xml为修复前证据，不用于最终验收。

真实 Adapter prepare_manifest 已通过：wheel 3fa5301c444da2e3bdaca51bd4800b1bdbcb6dc68e3abef4b39197bde3625e74，SDK树812258edd429587ba01a31101c64fc74ed110b5d91d1d0330044eae9039a2488，worker ba4590a68841e520dcd3a91e206ca9e346d10fd9a23b3ed4c560f59707cfa71e；manifest与Protocol指纹也通过同一身份门。
真实工具纵切面运行三个模型请求且始终chosen-model，产生候选与checkpoint_created/effect_receipt committed；宿主验证通过只到Candidate，不称Delivery。所有模型请求均为回环合成响应。

双轴审查首轮发现嵌套快照缺字段丢失用量及未知状态关联已确定查询结果；新增回归先复现，再修复递归字段完整性和状态一致性。恢复快照不会用默认值补丢失的记账事实，也不能覆盖已经确定的查询结果。
主会话与三代理实际turn_context分别核实Astra medium和Astra low。PR同提交CI、讨论解决及合并结果由关联GitHub PR提供，不把本机通过写成远端通过。
