# CV-06 Provider 重验安全闭环工程验证报告

> 状态：ENGINEERING_VERIFIED
>
> 日期：2026-08-24
>
> 工单：GitHub #66
>
> 固定审查点：`51d327d54aa298ab734f30f106f5405bb12619de`
>
> 用户验收：尚未执行

## 1. 已验证完成

- Provider 重验沿用冻结的 connection/version/model；每个 VerificationAttempt 单独记录本次
  Owner 外发确认和稳定 Provider Attempt ID，客户端不能替换连接身份。
- VerificationAttempt 在 Grant 签发和请求发送前持久化；落库失败时 Grant 签发数和发送数均为
  0。同一 Attempt 的并发 Worker 只有一个能完成 requested→running CAS，Provider 调用数为 1。
- Provider Attempt ID 直接作为有界 Grant ID，Usage 可按 Owner/task/revision/run/grant 精确读取；
  Provider 未返回费用时投影为 `unknown`/`null`，不伪造为 0。
- Provider 发送后的超时、响应丢失、运行中取消、Worker/进程中断和 Provider 结论持久化不确定均
  失败关闭为 `outcome_unknown`，相同幂等请求不自动重发。
- 恢复未知结果必须由 Owner 再次确认外发与重复费用风险，并创建引用旧 Attempt 的新 Attempt；
  旧 Attempt 保持不可变，幂等重放不能改变费用确认语义。
- requested Provider Attempt 可由维护 Worker 安全接管；running Provider Attempt 只收口未知结果，
  不会伪装成普通 inconclusive/cancelled。
- 成功、失败、取消、未知和异常收口均撤销 Grant；撤销失败时不伪造已安全终结。
- 冻结连接不存在、停用、版本或模型漂移返回冲突；实际查到跨 Owner 个人连接返回权限拒绝；
  权威数据库故障返回服务不可用。响应不包含连接正文、Secret 或内部宿主路径。

## 2. 主要变更

- `src/candidate_verification/models.py`
- `src/candidate_verification/repository.py`
- `src/candidate_verification/service.py`
- `src/model_connections/__init__.py`
- `src/model_connections/broker.py`
- `src/model_connections/storage.py`
- `src/agentic_runtime/candidate_verifier.py`
- `src/api/semantic_workspace_runtime.py`
- `src/api/routes/semantic_workspace.py`
- `tests/test_candidate_reverification_provider.py`

没有新增依赖或数据库迁移，没有修改前端、Publisher、生产数据库或 G1 评测文件。

## 3. 验证证据

| 验证集合 | 结果 |
|---|---:|
| CV-06 Provider 专用测试 | 11 passed |
| CandidateVerification、Workspace Provider 与 Model Connections 聚焦回归 | 97 passed |
| Python 语法编译 | exit 0 |
| `git diff --check HEAD` | exit 0 |
| Standards 最终复核 | 无剩余 P1/P2 |
| Spec 最终复核 | 无剩余 P1/P2 |

审查过程曾发现并修复：running Provider 被误收口为 inconclusive/cancelled、恢复扫描排除
Provider、费用确认未纳入幂等一致性、Provider 权限/漂移/数据库故障状态码折叠，以及持久化失败
缺少零外发直接证据。

完整 G4 测试集合现场曾得到 155 passed / 4 failed；其中本次异常文案兼容回归已修复。其余 3 项
是当前已提交 HEAD 相对旧 Provider 证据提交确实改过受保护的
`src/agentic_runtime/candidate_verifier.py`，G4 兼容门按设计拒绝复用旧证据；未放宽该安全门，也未
把旧 Provider 资格冒充为当前候选资格。

## 4. 尚未验证与人工门

- 未连接或调用真实 Provider，未产生真实费用，未验证真实 Provider 的账单/Usage 对账。
- 未迁移真实 `data/webui.db`，未重验真实 Candidate，未发布 Delivery。
- 未创建分支、提交、推送、PR、标签或 Release；GitHub #66 未评论、改标签或关闭。
- 工程验证不等于用户验收、生产资格、Provider 认证或发布资格。真实外发和生产验证仍属于
  CV-10 人工门。

下一工程依赖门是 CV-07：建立精确 VerificationAttempt 绑定的显式正式发布动作；它不得自动
发布 CV-06 的 passed Attempt。
