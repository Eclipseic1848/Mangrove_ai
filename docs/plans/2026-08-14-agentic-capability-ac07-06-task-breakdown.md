# AC-07-06 管理员审核与业务内容审计查看 任务拆分

> 日期：2026-08-14
>
> 对应工单：GitHub Issue #11（`Eclipseic1848/Mangrove_ai`，`[AC07-06]`）
>
> 状态：`completed`（S1–S8 全部完成；双轴审查发现 2 阻断 + 3 重要已全部修复，
> 修复清单见下文"审查修复附录"）
>
> 前置：`2026-08-14-agentic-capability-ac07-06-requirements-review.md`（已确认）、
> `2026-08-14-agentic-capability-ac07-06-design.md`（已确认）
>
> 边界：本文是 #11 的执行切片计划；每个切片"先红灯、再最小实现、切片回归通过"后才进入
> 下一片，完成全部切片后统一走双轴审查与用户验收。

## 切片总览

| 切片 | 内容 | 验证标准（聚焦回归） | 主要文件 |
| --- | --- | --- | --- |
| S1 | 领域模型扩展 | 模型测试全绿（validator 分支、旧 payload 兼容） | `models.py`、`tests/test_capability_audit.py` |
| S2 | Repository 双实现 | 审计事件幂等/串类型/列表（InMemory + SQLite） | `repository.py`、`sqlite_repository.py`、同测试 |
| S3 | 投影过滤 | audit 事件不改变三轴投影 | `service.py`、同测试 |
| S4 | 任务解析器扩展 | 元数据/正文读取 + 安全校验（真实 SQLite 表） | `task_replay.py`、`tests/test_capability_task_metadata.py` |
| S5 | 服务层命令 | 审核聚合 + 审计查看（权限/原因/幂等/失败留痕/截断） | `service.py`、`models.py`、`tests/test_capability_audit.py` |
| S6 | HTTP 审核路由组 | API 权限矩阵/脱敏/404/422 | `routes/capability_governance.py`、`main.py`、`tests/test_capability_governance_api.py` |
| S7 | 前端审核视图 | 分组/渐进披露/审计弹窗/历史/无障碍（Playwright） | `CapabilityGovernancePanel.tsx`、`frontend/e2e/*` |
| S8 | 收尾回归 | Capability 全量 + 前端 build + 既有 Playwright 零回退 | 无新增 |

## 切片详情

### S1 领域模型

- 目标：`CapabilityGovernanceEvent.event_type` 增加 `"audit_viewed"` 分支与
  `reason/subject_type/subject_sha256/result` 字段；新增 `AuditViewOutcome`、
  `CapabilityTaskMetadata`、`BusinessContent`、`AdminReviewItem` 模型（字段即脱敏白名单）。
- 红灯：validator 对缺 reason 的 audit 事件报错；携带三轴非默认值报错；旧 registered
  payload 反序列化通过。
- 绿灯：最小字段与分支校验实现。

### S2 Repository 双实现

- 目标：`save_audit_view_event`（幂等：同 target+idempotency 键返回同一事件；
  拒绝非 audit 类型）与 `list_audit_view_events(target=None)`（按 occurred_at 排序）。
- 红灯：InMemory 幂等重试返回同一 event_id；SQLite 事务内查重（同 `save_promotion_event`）；
  串类型（registered 进审计入口）报错。
- 绿灯：InMemory + SQLite 两实现。

### S3 投影过滤

- 目标：`_projection_for_pack` 只取 `event_type != "audit_viewed"` 的最新事件。
- 红灯：同一 target 先 registered、再 audit_viewed → 投影仍是 draft/active/eligible，
  且 audit 事件不影响 `evaluate_promotion`（无验证运行 → 缺口不变）。
- 绿灯：投影过滤一行实现。

### S4 任务解析器扩展

- 目标：`ValidationTaskResolver` 协议新增 `read_task_metadata` 与
  `read_business_content`；`SqliteValidationTaskResolver` 实现：
  - 元数据：任务行投影（user_id/status/时间/upload 计数与扩展名类型/
    output_formats/正式输出计数），不读正文；
  - 正文：`task_prompt`（revision.objective_text）、`task_sources`（owner objects
    目录内普通文件，anti-symlink）、`task_output`（execution root 内正式输出），
    2 MiB 截断 + 内容 hash + 缺失/损坏返回类型化 failed。
- 红灯：真实临时 SQLite 库 + 临时 upload/execution 目录：元数据计数正确；
  路径逃逸/符号链接/缺失文件全部 failed；超限截断。
- 绿灯：实现（复用 resolve 的既有安全校验函数）。

### S5 服务层命令

- 目标：`list_admin_review(actor)`（管理员门；pack 三轴 + 缺口 + 最新验证运行摘要
  + 供应链摘要 + 任务元数据聚合）与 `audit_view_business_content(actor, ...)`
  （权限门、reason 非空 5–1000、读正文 → hash → 写 audit 事件 → 返回
  `AuditViewOutcome`；失败也写 failed 记录）。
- 红灯：普通 actor 两方法均 PermissionError；管理员跨 Owner 可读；空原因 ValueError；
  失败路径返回 failed + 记录；幂等重试同一记录；投影不变。
- 绿灯：服务层实现。

### S6 HTTP 审核路由组

- 目标：`admin_router`（prefix `/api/capability-governance/admin`，全部
  `Depends(require_admin)`）：`GET /review`、`GET /review/{pack}/{version}?digest=`
  （含审计历史）、`POST /audit-view`（body + Idempotency-Key）、`GET /audit-log`；
  注册进 `main.py`。
- 红灯：普通用户 401/403；管理员 200；`review`/`audit-log` 响应 JSON 断言不含
  objective_text/正文/路径/Secret 字段；`audit-view` 缺原因 422、缺幂等键 422、
  不存在的 pack 404、任务与验证证据不一致 422；审计记录出现在 audit-log。
  任务正文读取失败按 D2"失败留痕"返回 200 + failed 记录，不是 404。
- 绿灯：路由实现（复用 `_http_error` 与 `catalog_actor_from_user` 风格）。

### S7 前端审核视图

- 目标：管理员视图分组（待验证/已晋级/已弃用·撤销 + 计数）、卡片"任务管理元数据"
  渐进披露、审计查看弹窗（对象选择 + 原因必填 + 截断提示 + 失败展示）、面板内审计
  历史；ownerOnly 视图零改动；AC6 无障碍（h3 文本计数、dialog 焦点、1366 无横滚）。
- 红灯：Playwright 用例（管理员分组可见、普通用户无入口、空原因被拦截、审计成功流、
  键盘可达）；前端 build 通过。
- 绿灯：面板实现。

### S8 收尾回归

- 目标：Capability 九文件全量、前端 `npm run build`、既有 Playwright 全量（含 #10 的
  14 项）零回退；统计报告。
- 无新增代码；如发现回归按切片归属回修。

## 执行纪律

- 每个切片开始前先写该片的失败测试，再做最小实现；切片聚焦回归通过后才进入下一片。
- 夹具数据（任务行、upload 文件、输出文件、供应链证据、验证运行）来自测试内构造的
  冻结事实，不来自实现自身摘要。
- 所有正文断言只检查"返回了正确内容/hash/截断"，不把真实业务数据带进测试。
- 完成后按批次规则同步 md 文档；双轴审查与发布动作另行授权。

## 审查修复附录（2026-08-14 双轴审查后）

双轴审查（Standards + Spec）结论：2 阻断 + 3 重要 + 多项次要，已全部修复并复核：

| 编号 | 问题 | 修复 |
| --- | --- | --- |
| B1 | 2MiB 截断发生在整读之后，大文件可致 OOM | `_read_limited` 按块读 2MiB+1 字节；sources/output 累计预算截断；绕过 UploadStore 兜底整读 |
| B2 | 审计事件缺 task_id/revision（AC3 任务字段） | 事件加 `task_id`/`revision`（audit_viewed 必填），落库 + 前端审计历史展示 |
| A1 | 失败类型化原因未留痕 | 事件/Outcome 加 `failure_reason`（failed 必填），前端中文映射展示 |
| A2 | 审计查看未绑定验证证据任务 | 收紧：只允许审计查看该能力最新成功验证运行绑定的冻结任务，不一致抛 ValueError |
| A3 | pack 不存在 403 应 404 | 服务层改抛 KeyError |
| 次要 | InMemory 幂等键跨类型串键 | 键纳入 event_type，与 SQLite 一致 |
| 次要 | 前端每次提交随机幂等键 | 弹窗生命周期内固定 |
| 次要 | 元数据 JSON 损坏 500 | 服务层 catch ValueError（JSONDecodeError 子类）留空占位 |
| 次要 | registered/promoted 校验不完整 | 全部 7 个审计字段均拒绝非 None |
| 次要 | docstring/导出/文档对齐 | 路由 docstring、AuditSubjectType 导出、S6 文字对齐 |
