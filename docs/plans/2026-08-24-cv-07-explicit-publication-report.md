# CV-07 精确 Attempt 显式正式发布工程验证报告

> 状态：ENGINEERING_VERIFIED
>
> 日期：2026-08-24
>
> 工单：GitHub #67
>
> 固定审查点：`51d327d54aa298ab734f30f106f5405bb12619de`
>
> 用户验收：尚未执行

## 1. 已验证完成

- 新增
  `POST /api/semantic-workspace/tasks/{task_id}/candidate-verifications/{attempt_id}/publish`；
  Owner 必须提交 expected revision 和 `Idempotency-Key`，passed 重验本身仍不会自动发布。
- PiCandidateAdapter 和 PublishCommand 显式冻结 Attempt ID、报告哈希、CandidateSet 哈希及
  HTTP 幂等键摘要；原始幂等键不进入数据库、Manifest 或用户响应。
- HTTP 幂等键在 Owner 内唯一绑定一个冻结发布请求；同键并发返回同一 Delivery，换 Attempt
  或换冻结输入冲突。同 publication 由跨线程/进程文件锁串行执行。
- 新发布和所有提交点前重试均重新检查 Owner、当前 revision、latest Attempt、Ruleset、来源、
  Candidate/Manifest/契约、P0、取消和既有 Delivery；`begin_commit` 在同一 SQLite 写事务内
  再次执行 revision/P0/精确 Attempt CAS。
- QA、取消、P0、revision 或候选漂移发生在提交点前时正式 Delivery 数保持 0；新 Revision
  不能越过 `committing`，旧发布也不能覆盖后来创建的新 Revision 状态。
- `committing` 是发布提交点。rename 前崩溃只从冻结 staging/Manifest 恢复，rename 后崩溃只
  接受与 Intent 冻结 Manifest 完全一致且位于 final 根内的非符号链接输出；不重读 Candidate
  或业务门，不覆盖未知 final。
- 旧自动发布和 legacy Delivery 继续沿用原 publication lineage；新增字段为空时不改变旧命令
  的冻结哈希或发布键。

## 2. 主要变更

- `src/delivery_publishing/models.py`
- `src/delivery_publishing/pi_adapter.py`
- `src/delivery_publishing/repository.py`
- `src/delivery_publishing/service.py`
- `src/candidate_verification/service.py`
- `src/api/routes/semantic_workspace.py`
- `src/api/semantic_workspace_runtime.py`
- `src/api/store.py`
- `tests/test_candidate_reverification_publish.py`
- `tests/test_candidate_reverification_offer.py`
- `tests/test_vnext_delivery_publisher.py`
- `tests/test_pi_runtime_workspace_api.py`

`delivery_publish_intents` 追加可空 `request_idempotency_hash` 和 Owner 内部分唯一索引；旧行保持
NULL 且零改写。没有新增第三方依赖，没有修改前端、生产数据库、真实 Candidate 或 G1 文件。

## 3. 验证证据

| 验证集合 | 结果 |
|---|---:|
| CV-07 发布专用测试 | 8 passed |
| CandidateVerification、Publisher、Workspace API 聚焦回归 | 131 passed，1 deselected |
| Python 语法编译 | exit 0 |
| `git diff --check`（CV-07 文件允许列表） | exit 0 |
| Standards 最终复核 | 无剩余 P1/P2 |
| Spec 最终复核 | 无剩余 P1/P2 |

唯一 deselected 的
`test_current_ruleset_resolver_is_deterministic_and_ignores_unrelated_worktree` 若单独运行会按设计失败：
CV-05/CV-06 对受保护 Verifier 符号存在尚未提交的真实语义变化，当前 Ruleset Resolver 拒绝把
旧 HEAD 冒充为新规则身份。本阶段没有放宽或改写该安全门；形成提交后需以新 HEAD 重跑。

双轴审查过程中发现并修复了 HTTP 幂等键跨 Attempt 复用、同 publication 并发 staging 竞争、
Ruleset/latest 的提交点前重试绕过、active revision TOCTOU、SQLite 故障误报、错误详情路径
泄露，以及 committing 在 rename 前后两个崩溃窗口的冻结恢复完整性问题。

## 4. 尚未验证与人工门

- 未把追加列应用到真实 `data/webui.db`，未执行生产备份或迁移演练。
- 未重验真实 Candidate，未通过产品入口发布真实 Delivery，未执行普通用户浏览器验收。
- 未调用真实 Provider、未产生费用，也未改变 P0 Rollout 状态。
- 未创建提交、推送、PR、标签或 Release；GitHub #67 未评论、改标签或关闭。
- 工程验证不等于用户验收、生产资格或发布资格。

下一工程依赖门是 CV-08：在普通用户工作台呈现只读 Offer、重验确认、Attempt 时间线和第二个
显式发布动作；前端设计与实现必须使用已安装的 frontend-design 技能，且不能绕过本阶段冻结
的 API/权限/提交语义。
