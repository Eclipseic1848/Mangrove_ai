# CV-02 VerificationAttempt 与显式迁移工程验证报告

> 状态：ENGINEERING_VERIFIED（待用户确认）
>
> 日期：2026-08-24
>
> 固定点：`51d327d54aa298ab734f30f106f5405bb12619de`

## 1. 范围

本报告只覆盖 CV-02：追加式 `VerificationAttempt` 领域记录、SQLite Repository、带一致性
恢复点的显式迁移、legacy 报告失败关闭导入和生产库只读副本演练。未接入既有验证入口，
未迁移 `data/webui.db`，未调用 Provider，未执行真实重验或正式发布。

## 2. 已验证事实

- 新增测试：`27 passed`，exit code 0；覆盖空库/重复/并发迁移、损坏源与错误备份、备份后
  DDL 前恢复、legacy 三种确定状态、空报告、不可变终态、前向 CAS、Owner/前序链、幂等、
  状态字段和报告状态一致性。
- 相邻回归：CandidateVerifier、Agentic Runtime、Runtime Routing、Semantic Workspace API、
  vNext Delivery Publisher 共 `131 passed`，exit code 0。
- UTF-8 Python AST 检查 5 个文件通过；CV-02 文件 `git diff --check` 通过。
- 当前 `data/webui.db` 仅以 SQLite `mode=ro` 打开并 online backup 到系统临时目录；DDL 只在
  副本执行。副本迁移前后 71 张既有表、10,223 行逻辑指纹一致。
- 副本导入 35 条历史 Attempt：27 `passed`、4 `failed`、4 `inconclusive`；35 条均通过领域
  模型校验，且全部为 `legacy_unversioned`。
- 副本迁移前后 `integrity_check=ok`、外键违规 0；同路径重放未覆盖首次恢复点；从恢复点
  启动的副本不含 CandidateVerification Schema，既有表指纹一致且完整性为 `ok`。
- 首轮双轴审查发现 Standards 1 项硬问题和 1 项重复代码判断项、Spec 2 项有效 P1；已修复
  关键门禁中文 why 注释、测试重复、跨 Owner 前序链以及 Attempt/报告终态矛盾。另两项分别
  依据 CV-02 分期边界和本报告的生产副本证据完成裁决；Standards、Spec 复核均无发现。
- 上述演练退出码为 0，临时生产副本、恢复副本和本轮 `.pyc` 生成物均已清理。

## 3. 基于代码与 ADR 的推断

- ADR-0033 已确认：旧 GateSnapshot/commit 只能证明门和源码版本，不能证明具体旧报告由该
  Verifier 实际执行；当前历史也没有不可变 Run execution receipt。因此 35 条 legacy 报告
  不能补猜旧 Ruleset，必须失败关闭为 `legacy_unversioned`。
- 新的 versioned Attempt 会冻结 Manifest、Ruleset、源码与 execution identity；把既有验证入口
  统一接入该 Module 属于 CV-03，不在 CV-02 预埋双写逻辑。

## 4. 尚未验证或未授权

- 未在真实 `data/webui.db` 上执行迁移；生产迁移仍属于 CV-10 人工门。
- 未把 CandidateVerification 接入初验/语义重试，未验证真实业务主链；属于 CV-03。
- 未调用外部 Provider，未重验真实 Candidate，未创建 Delivery，未做用户验收。
- 未创建 GitHub Issue、分支、提交、推送、PR、标签、Release 或部署。
- Python 3.13 环境仍输出既有 `requests` 依赖版本告警；测试本身 exit code 为 0，本工单未改依赖。

## 5. 下一门禁

CV-02 只有在用户确认本报告后才算阶段完成。不得由本报告自动进入 CV-03；CV-03 将另行声明
接入文件允许列表、事务边界和 TDD 接缝。
