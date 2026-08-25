# CV-03 CandidateVerification Module 工程验证报告

> 状态：USER_ACCEPTED
>
> 日期：2026-08-24
>
> 工单：GitHub #63
>
> 固定审查点：`51d327d54aa298ab734f30f106f5405bb12619de`
>
> 用户确认：2026-08-24 已接受 CV-03 工程产物；不包含 Git、生产迁移、Provider 或发布授权

## 1. 完成范围

- 初始 Candidate 验证和既有 `inconclusive` 语义重试统一经过
  `CandidateVerificationService`；生产路径不再允许直接调用 Verifier 后事后补录 Attempt。
- Module 以依赖注入接收 Repository、Verifier、Ruleset resolver、P0 reader、Broker Adapter
  和事件写入 Adapter。
- `initial` 与 `semantic_inconclusive` 均追加不可变 `VerificationAttempt`；Attempt 终态和
  `verification_json`、`verified_candidate_set_hash` 兼容投影在同一事务提交。
- P0 最终重查、Attempt 创建和 `requested → running` 在同一 `BEGIN IMMEDIATE` 事务内完成；
  Ruleset 解析期间 P0 翻转时零 Attempt。
- Verifier 执行异常收口为带报告的 `inconclusive`，协程取消收口为不伪造报告的 `cancelled`；
  语义重试异常的新投影可继续形成后继 Attempt。
- VerifierRuleset 绑定实际 `CandidateVerifier` 实例、相关源码符号闭包、依赖和当前执行身份；
  重复符号、未覆盖本地契约、相关未提交语义变化和非受控 Verifier 均失败关闭。
- 现有语义重试保持不重跑 Pi，并保留 CV-07 前的既有“通过后发布”兼容行为。
- Runtime 尚未进入 `candidate_ready` 时，公共投影不暴露中间 Candidate 或 Verification。

## 2. 变更边界

主要实现和回归文件：

- `src/candidate_verification/service.py`
- `src/candidate_verification/ruleset.py`
- `src/candidate_verification/repository.py`
- `src/candidate_verification/models.py`
- `src/agentic_runtime/pi_runtime.py`
- `src/api/semantic_workspace_runtime.py`
- `src/api/routes/semantic_workspace.py`
- `tests/test_candidate_verification_service.py`
- `tests/test_candidate_verification_ruleset.py`
- `tests/test_pi_runtime_workspace_api.py`

未修改 `CandidateVerifier` 的验证规则，没有新增 Python/npm 依赖，没有重构无关 Runtime、
Publisher、前端或 G1 评测内容。

## 3. 验证证据

使用本机既有 Python 3.13 工程解释器；系统 Python 3.14 没有 pytest，因此未用于工程门，且未
安装任何依赖。

| 验证集合 | 结果 |
|---|---:|
| CandidateVerification service/ruleset/repository | 24 passed |
| Pi 工作台 API 全文件 | 23 passed |
| CandidateVerifier、Agentic Runtime、显式迁移 | 80 passed |
| Python 语法编译 | exit 0 |
| `git diff --check` | exit 0，仅既有 Windows 换行提示 |

定向测试覆盖 initial/passed/failed/inconclusive、幂等与冲突、Owner/task/revision 串线、双向
事务回滚、P0 翻转、取消、执行异常、语义异常后继、恢复沿用原 Run，以及 HTTP 重试时
`PiRuntime.start_calls == 1`。

第三轮双轴审查结论：Standards 与 Spec 均无 P1/P2 阻断问题。Broker Adapter 在组合根读取
Verifier/Judge 私有绑定字段属于非阻断封装债务；以后调整 Verifier Seam 时应改为公开、不可变的
binding identity，本工单不为此扩大接口范围。

测试环境仍报告既有 `requests` 依赖版本告警、`pynvml` 弃用告警及 TestClient/httpx 弃用告警；
本工单未新增或升级依赖。

## 4. 未执行与人工门

- 未迁移真实 `data/webui.db`，未调用真实 Provider，未重验真实 Candidate，未发布 Delivery。
- 未创建分支、提交、推送、PR、标签或 Release；GitHub #63 未评论、改标签或关闭。
- 工程验证不等于用户验收、生产资格或 Git 授权。
- 用户确认 CV-03 后，下一阶段只能进入 CV-04 的只读 `ReverificationOffer` 规格纵切片；不能从本
  报告推断生产迁移、外发、发布或 Git 权限。
