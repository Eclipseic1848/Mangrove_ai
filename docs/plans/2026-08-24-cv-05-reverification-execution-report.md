# CV-05 不重跑 Pi 的完整候选重验工程验证报告

> 状态：ENGINEERING_VERIFIED
>
> 日期：2026-08-24
>
> 工单：GitHub #65
>
> 固定审查点：`51d327d54aa298ab734f30f106f5405bb12619de`
>
> 用户验收：尚未执行

## 1. 完成范围

- 新增 `POST /api/semantic-workspace/tasks/{task_id}/candidate-verifications`。请求冻结 expected
  revision、previous Attempt、外发确认和 `Idempotency-Key`，服务端返回 HTTP 202 Attempt 收据。
- 同一幂等键返回同一 Attempt；不同键不能绕过活动 Attempt 约束。Owner、P0、活动 revision、
  Runtime/Candidate/Manifest/来源/契约漂移均失败关闭。
- requested→running 在 SQLite `BEGIN IMMEDIATE` 事务中完成最终 CAS；CAS 失败时按既有状态机安全
  收口 cancelled，不调用 Verifier。
- 重验复用完整 `CandidateVerifier.verify`，覆盖 artifact set/count、table contract、source
  grounding 和 semantic goal；不调用 `PiRuntime.start/resume`，不写 Candidate/Revision，不获取
  能力或依赖，不调用 Publisher。
- passed 后任务继续保持 `candidate_ready` 并投影“等待发布”；旧 Attempt 不改写，新 Attempt
  追加保存完整冻结身份、报告和终态。
- 每个 Attempt 使用项目既有 `filelock` 跨进程租约。滚动启动不能把仍在执行的 Worker 误判为
  崩溃；租约释放后的孤儿 running 才会收口为可审计 inconclusive。
- CandidateVerification 工作台事件使用确定性 event ID，并通过 SQLite `BEGIN IMMEDIATE` 与
  `INSERT OR IGNORE` 原子幂等；重复恢复不会产生重复 requested/started/finished 事件。
- 冻结 request/candidates 缺失或损坏属于请求契约错误，API 返回 422；锁不可用返回 503，
  权威漂移和并发冲突返回 409。

## 2. 变更边界

主要实现与回归文件：

- `src/candidate_verification/service.py`
- `src/candidate_verification/repository.py`
- `src/candidate_verification/__init__.py`
- `src/api/semantic_workspace_runtime.py`
- `src/api/routes/semantic_workspace.py`
- `src/api/store.py`
- `tests/test_candidate_reverification_execution.py`
- `tests/test_pi_runtime_workspace_api.py`

没有增加依赖或数据库迁移，没有修改前端、Publisher、Provider Grant/Usage、真实生产数据库或
G1 评测文件。

## 3. 验证证据

测试使用仓库既有项目 Python 解释器。系统默认 Python 3.14 未安装 pytest，因此未用作
工程门，也没有安装依赖或改走替代测试路线。

| 验证集合 | 结果 |
|---|---:|
| CandidateVerification、真实本地 Verifier、Pi 工作台八文件回归 | 116 passed |
| CSV/JSON/XLSX Candidate + PDF 来源完整验证 | passed |
| Candidate 与整个 Workspace 文件树 SHA-256 前后一致 | passed |
| Pi start 仅初始任务 1 次、resume 0 次；Delivery 为空 | passed |
| Grant/Usage 表、能力/依赖事件零新增 | passed |
| 幂等、并发、P0 翻转、锁超时、revision 漂移、崩溃恢复 | passed |
| 活跃跨进程租约保持 running；释放后恢复 inconclusive | passed |
| requested/started/finished 每 Attempt 各恰好一次 | passed |
| 冻结上下文损坏映射 422 | passed |
| Python 语法编译 | exit 0 |

最终 Standards/Spec 双轴复核均无剩余 P1/P2。审查期间补齐了跨进程活租约后的持续接管、维护
扫描与每小时清理的瞬时锁失败重试、后台异常可观察且不击穿应用关闭、以及跨 Store 并发事件
原子幂等证据。

完整回归报告两个既有警告：`pynvml` 弃用，以及 Starlette TestClient/httpx 接口弃用；本工单未
新增或升级相关依赖。

## 4. 未执行与人工门

- 未迁移真实 `data/webui.db`，未调用真实 Provider，未重验真实 Candidate，未发布 Delivery。
- 未创建分支、提交、推送、PR、标签或 Release；GitHub #65 未评论、改标签或关闭。
- 工程验证不等于用户验收、生产资格或 Release 资格。
- CV-06 可以继续做本地 TDD 和离线接缝验证；真实 Provider 连接、模型、外发输入范围、费用及
  outcome_unknown 后的重复请求选择仍由用户控制。
