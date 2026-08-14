# AC-07-06 管理员审核与业务内容审计查看 领域/接口设计

> 日期：2026-08-14
>
> 对应工单：GitHub Issue #11（`Eclipseic1848/Mangrove_ai`，`[AC07-06]`）
>
> 状态：`design_confirmed`（用户于 2026-08-14 确认）
>
> 前置：`2026-08-14-agentic-capability-ac07-06-requirements-review.md`（需求复核，已确认，
> 含 Q1–Q5 推荐答案）
>
> 边界：本文只记录 #11 的设计决策，不授权实现、迁移、提交或发布；这些动作按后续阶段
> 分别取得授权。

## Problem Statement

管理员需要跨 Owner 审核能力治理证据，但现有治理 Interface（`list_visible_projections`、
`/api/capability-governance/packs`）只给"三轴状态 + 缺口"，没有任务管理元数据聚合，也没有
"有原因、可审计的正文读取"命令。Issue #11 要求在既有管理员设置内补全：默认脱敏的管理
审核视图 + 独立审计查看命令 + 不可变审计记录。

## Solution

两个新命令 + 一组只读端点，全部挂在既有 `CapabilityGovernance` 主 Seam 与既有治理路由域：

```text
管理审核列表（只读，默认脱敏）
  CapabilityGovernance.list_admin_review(actor)
    = pack 三轴投影 + promotion_gaps + 最新验证运行摘要 + 供应链摘要
      + 关联任务管理元数据（身份/Owner/状态/时间/输入输出类型数量/资源）

审计查看命令（写，必须有原因）
  CapabilityGovernance.audit_view_business_content(
      actor, target, task_id, revision, subject, reason, idempotency_key)
    → 实时读取正文 + 计算 hash + 追加 audit_viewed 事件（幂等、不可变）
    → 不落正文副本、不批量、列表永不返回正文
```

审计查看事件复用 `capability_governance_events` 表（Q3），投影层过滤该事件类型，
三轴状态不受审计查看影响。

## Implementation Decisions

### D1 领域模型：事件类型扩展 `audit_viewed`

- `CapabilityGovernanceEvent.event_type` 扩展为
  `"registered" | "promoted_to_verified" | "audit_viewed"`。
- 新增字段（`audit_viewed` 专用，其余事件类型必须为 None，validator 分支校验）：
  - `reason: str | None`（非空原因，1–1000 字符；为 `audit_viewed` 必填）
  - `subject_type: Literal["task_prompt", "task_sources", "task_output"] | None`
  - `subject_sha256: str | None`（本次返回正文内容的 hash，失败时可为 None）
  - `result: Literal["succeeded", "failed"] | None`（读取成功与否；失败也要留痕，
    保证"尝试过审计查看"不可抵赖）
- `audit_viewed` 分支校验：三轴保持默认 `draft/active/eligible` 原值，不携带
  `source_validation_run_id`/`source_supply_chain_evidence_id`；`target` 绑定发起查看的
  能力目标（事件表索引列可复用）。
- 旧行兼容：新字段全部 `default=None`，历史 payload 反序列化不受影响；零 DDL。

### D2 审计查看命令（服务层）

新增 `CapabilityGovernance.audit_view_business_content(...) -> AuditViewOutcome`：

- 权限门：`actor.is_admin`，否则 403（服务层门，HTTP 门之外的第二道）。
- 流程：校验 reason 非空 → 读正文（经任务解析器）→ 对**返回内容**计算
  `sha256` → 写 `audit_viewed` 事件（专用入口，幂等键由调用方提供）→ 返回
  `AuditViewOutcome(status, content, event)`。
- 读取失败：**仍写审计记录**（`result=failed`），把原因与异常类型摘要留在 reason 之外，
  不返回正文；调用方看到 failed 记录而非静默无痕。
- 幂等：同一 `Idempotency-Key` 重试返回同一条审计记录（服务端生成的事件 ID 为准），
  不产生第二条正文读取副作用；正文读取在写入前完成，重复请求会重复读取文件但不
  落第二条记录——可接受（审计不可变，重试不覆盖）。
- 正文大小上限：单对象读取上限 2 MiB，超出只返回截断前段并在结果中标注
  `truncated=true`；hash 按实际返回内容计算（拒绝把 GB 级制品读进内存）。
- `AuditViewOutcome`：`status: "succeeded" | "failed"`、`content`（succeeded 时）、
  `truncated`、`event`（审计记录）。

### D3 Repository：审计事件专用入口（InMemory + SQLite 双实现）

- `CapabilityGovernanceRepository` 新增：
  - `save_audit_view_event(event)`：只接受 `audit_viewed`，事务内按幂等键查重
    （同 `save_promotion_event` 模式，`BEGIN IMMEDIATE` + 既有 `idempotency_key`
    唯一语义，部分唯一索引不需要新结构）。
  - `list_audit_view_events(target=None)`：审计记录列表（按 occurred_at 排序），
    供"审核历史渐进展开"消费；无 target 时全量。
- 通用 `save_event` 保持"只接受 registered"不变；`save_promotion_event` 保持"只接受
  promoted_to_verified"不变——三个入口各守一个事件类型，防串类型。

### D4 投影过滤：审计事件不参与三轴

- `_projection_for_pack` 现有 `events[-1]` 语义改为
  `events[-1]` where `event_type != "audit_viewed"`：审计查看不是治理状态事实，
  不得污染成熟度/生命周期/运行资格投影。
- 晋级判定与缺口评估（`evaluate_promotion`）只读验证运行与供应链证据，不受影响。
- 风险提示：任何未来消费 `list_events` 全量的调用方都必须自行区分事件类型；
  在本工单内仅有投影一处消费，设计文档即期记录。

### D5 任务管理元数据与正文读取：扩展任务解析器协议

`ValidationTaskResolver`（协议）新增两个方法，`SqliteValidationTaskResolver` 实现：

- `read_task_metadata(actor, task_id, revision) -> CapabilityTaskMetadata`：
  只读 `semantic_workspace_tasks/revisions` 的行投影（user_id、status、时间、
  `upload_ids_json` 计数与扩展名类型、`output_formats_json`、正式输出计数）。
  - 输入类型：`upload_ids` 经 `UploadStore.resolve` 取文件扩展名去重计数；
    类型与数量**只取元数据**，不读文件内容。
  - 输出计数：`formal_delivery_outputs` / `semantic_delivery_outputs` 按 run_id 计数
    （沿用 task_replay 的两表优先语义）。
  - 权限：本方法不做 Owner 门（管理员聚合与 Owner 自看共用），由服务层调用方保证；
    SQL 按传入 user_id（= 验证运行 owner_id）取行。
- `read_business_content(actor, task_id, revision, subject_type) -> BusinessContent`：
  实时读取正文（Q4，不落副本）：
  - `task_prompt`：`semantic_workspace_revisions.objective_text`（冻结版本）。
  - `task_sources`：`upload_ids` 经 `UploadStore.resolve` 读文件内容（普通文件、
    非 symlink、路径必须位于 owner objects 目录——沿用 resolve 的既有安全校验）。
  - `task_output`：正式输出 `formal_delivery_outputs`（优先）/ `semantic_delivery_outputs`
    的 `file_path` 读文件内容（路径必须位于 execution root——沿用既有校验）。
  - 返回 `BusinessContent(subject_type, content, content_sha256, size_bytes,
    truncated)`；任一正文缺失/损坏返回 failed 结果（含类型化原因），不抛未包装异常。
- InMemory 测试替身：测试中提供 stub 实现（元数据与正文来自测试夹具），断言只落在
  服务层可观察结果。

### D6 HTTP 管理员审核路由组

在 `src/api/routes/capability_governance.py` 新增
`admin_router = APIRouter(prefix="/api/capability-governance/admin")`，全部
`Depends(require_admin)`，注册进 `main.py`：

| 端点 | 语义 | 输出 |
| --- | --- | --- |
| `GET /review` | 跨 Owner 管理审核聚合列表 | `items: [AdminReviewItem]`（三轴+缺口+验证摘要+供应链摘要+任务元数据）；**不含正文类字段** |
| `GET /review/{pack_id}/{version}?digest=` | 单能力审核详情（渐进披露） | 同 item + 审计历史（该 target 的 audit 记录摘要，不含正文） |
| `POST /audit-view` | 审计查看命令 | body：`task_id, revision, subject_type, reason` + `Idempotency-Key`；返回正文与审计记录 |
| `GET /audit-log` | 审计记录列表（全量，按时间倒序） | 记录摘要（actor/时间/任务/对象/用途/结果/hash），不含正文 |

- `AdminReviewItem` 模型字段（脱敏白名单）：pack 三轴、`promotion_gaps`、验证运行摘要
  （run_id、状态、步骤通过数）、供应链摘要（status/blockers/计数）、
  任务元数据（task_id、revision、owner_id、任务状态、时间、输入/输出类型与数量）。
  注：需求复核 Q2 曾以"验证运行耗时"代表"资源"，但 `CapabilityValidationRun` 无
  started/finished 记录、历史运行无法回算，本工单以验证摘要（步骤通过数）代表资源，
  耗时字段留待运行模型补充时间记录后再加（如实偏差，不做假数据）。
- 越权语义：非管理员 401/403（HTTP 门）；管理员但 pack 不存在 → 404；
  跨 Owner 是默认合法（本工单目的），不做 Owner 过滤。
- 正文只经 `audit-view` 返回；`review` 与 `audit-log` 的响应模型没有正文字段
  （Pydantic 模型冻结字段即白名单，天然防漏）。

### D7 前端管理员审核视图（CapabilityGovernancePanel 增强）

- 保持 `ownerOnly` 普通用户视图零改动；管理员视图在既有卡片流基础上增加：
  1. **分组**：按成熟度分"待验证（draft）／已晋级（verified）／已弃用·撤销
     （deprecated/revoked）"三组，组标题带计数（Q5；平台候选/管理员灰度分组留给 #12，
     UI 不做空态占位）。
  2. **渐进披露**：卡片默认摘要不变；新增"任务管理元数据"可折叠块（身份、Owner、
     状态、时间、输入/输出类型数量、资源/验证摘要）；再下一层才是"审计查看"动作。
  3. **审计查看弹窗**：选择对象（Prompt／来源正文／输出正文）＋ 必填原因
     （非空校验，最小 5 字符），提交后展示返回正文（截断提示）与审计记录引用；
     失败时展示失败记录。
  4. **审计历史**：面板内"审计记录"可折叠块，列出该能力的审计记录（actor、时间、
     对象、原因、结果）；不做全量审计中心（非目标）。
- 无障碍（AC6）：分组标题用 `h3`+文本计数（不只颜色）、弹窗 `role=dialog` 与焦点
  管理、状态文本替代色块、深浅主题沿用现有 token、1366 宽度不横向滚动
  （e2e 用 1366 视口断言无横向滚动）、reduced-motion 沿用仓库既有基线
  （全局 CSS 无 `prefers-reduced-motion` 支持是既有状态，#11 不引入新动画，
  亦不扩大范围补全局样式）。
- 文案风格延续 #10：中文、无 Emoji、明确"审计查看会产生不可变记录"提示。

### D8 迁移：零 DDL

- 事件表 `capability_governance_events` 的 `event_type` 列与 `payload_json` 全量落库
  已经支持 `audit_viewed`（0004 起）；新模型字段全部在 payload 内，旧行默认 None
  兼容。**本工单不新增迁移文件**。
- 生产执行前仍走既有"一致性备份 + 幂等重放"流程（复用 `migrate_capability_governance`
  无变化），但不需要 0005。

### D9 明确不改的

- `catalog.py`、`catalog_actor.py`、`auth.py` 零改动（require_admin 复用）。
- 五步验证执行器、供应链采集、晋级门（#10）零改动。
- Owner 视图（`/packs`、`/validations` 等现有端点）零改动；新增端点全是 `/admin/` 前缀。
- 普通用户可访问端点列表不变，`CapabilityGovernancePanel` ownerOnly 分支不改。

## Testing Decisions

- **服务层（InMemory 双实现）**：
  - 权限矩阵：普通 actor 调 `list_admin_review` / `audit_view` 抛 PermissionError；
    管理员/超管可读跨 Owner 元数据。
  - 审计查看：成功路径（正文 + hash + 记录）；空原因拒绝；失败路径仍写 failed 记录；
    幂等重试返回同一记录；正文超大截断；投影不受 audit 事件影响（写 audit 后三轴不变）。
  - 元数据聚合：输入/输出类型数量计数正确；任务缺失时元数据为"不可用"占位而非 500。
- **HTTP Interface（认证调用）**：普通用户 403、管理员成功；`review`/`audit-log`
  响应断言不含 objective_text/正文/path/Secret 字段；`audit-view` 必须带原因与幂等键；
  不存在的 pack/task → 404/422。
- **浏览器流程（Playwright）**：管理员看到分组与计数；普通用户无治理入口；渐进披露
  展开；审计查看填原因→成功展示；空原因被拦截；键盘可达与屏幕阅读器名称；
  1366 宽度无横向滚动。不测试私有查询函数。
- **回归**：Capability 九文件全量 + 前端 build + 既有 Playwright 全量（#10 的 14 项
  与既有 e2e）零回退。

## Out of Scope

- 平台快照、签名、admin_gray（#12）；平台快照的受限审计读取。
- 普通用户受众开放；CapabilityMountResolver 运行门（#13）；弃用/回滚/撤销/风险接受
  （#14）；两条真实纵切面（#15/#16）；AC-06 兼容切换（#17）。
- 通用跨用户任务管理中心、自动化方案库、审核队列（AC-09）。
- Secret、宿主路径、原始工具日志的审计查看（需求复核非目标）。
- 正文批量导出、正文快照副本、审计记录删除/编辑。
