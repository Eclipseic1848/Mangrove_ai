# 对话反馈与 token 可观测性设计（2026-07-14）

## 背景

对话工作区每轮 AI 回复后，用户需要：看到本轮 token 消耗、能复制回复、能点赞/点踩反馈（点踩带原因+描述）。
反馈数据供管理员在「反馈管理」页查看处理，驱动对话质量优化与评估（bad case 归因、构造评测集、跟踪改进）。

## 功能

### 1. token 消耗捕获与展示

**捕获（后端）**：
- `src/llm/provider.py` 新增 `_usage_ctx`（contextvars.ContextVar）+ `_collect_usage(resp)` 辅助函数。
  `achat`/`chat` 每次调用后，若当前上下文设了 usage sink，累加 `resp.usage_metadata` 的 `input_tokens`/`output_tokens`/`total_tokens`/`calls`。未 set 时零开销跳过。
- `src/api/routes/chat.py` 的 pipeline 进入时 `_usage_ctx.set({"prompt_tokens":0,...})`，跑完 `astream_conductor` 后读汇总：
  - `result["token_usage"]` 随 SSE result 事件返回前端
  - 同步写进 `messages.meta_json`（重载会话后仍能展示）
- `compress_history` 的 token 也计入（contextvar 在它之前 set）。

**展示（前端）**：每条 assistant 消息操作栏展示 `🪙 输入↑ / 输出↓ · 共 N`，hover title 显示调用次数与明细。`calls=0` 时不显示（避免 0 误导）。

**可行性验证**：本地 Qwen3.6-35B-A3B（ChatOpenAI 封装）的 `resp.usage_metadata` 实测有值 `{input_tokens, output_tokens, total_tokens}`，字段名与代码一致。

### 2. 点赞/点踩

- **点赞**：直接提交 `rating=up`。
- **点踩**：弹框选原因（多选：理解错误/上下文错误/回答不清晰/代码错误/回答不专业/格式错误/其他）+ 自由描述。
- **取消**：再点已选中的赞/踩，调 `DELETE` 取消（点踩已 down 时再点直接取消，不弹框）。
- **查看**：已点踩的消息在操作栏下方显示「已反馈：原因1、原因2 · 描述」摘要。
- 反馈存 `message_feedback` 表，`UNIQUE(message_id, user_id)` 天然覆盖更新（点赞后能改点踩）。

### 3. 复制

`navigator.clipboard` 在 http（非 secure context，内网 IP:8088）下不可用。`Chat.tsx` 的 `copyText` 优先试 clipboard API，不可用时回退 `document.execCommand('copy')`（创建隐藏 textarea）。

### 4. 反馈管理页（管理员）

侧栏「反馈管理」入口（`NAV_ADMIN`，仅管理员可见）。

**统计区（5 卡）**：
| 卡片 | 值 | 说明 |
|---|---|---|
| 总会话数 | `COUNT(conversations)` | 全局，点踩率分母 |
| 点赞数 | `rating=up` | |
| 点踩数 | `rating=down` | |
| 点踩率 | `点踩数 / 总会话数 * 100%` | 分母是会话数，不是反馈数 |
| 待处理 | `status=pending` | |

**原因分布**：点踩原因条形图（7 类原因各多少）。

**明细列表**（分页 + 筛选）：
- 每行：赞/踩图标、时间、用户（`@姓名/用户名（userID）`，join users 表）、模型标签、状态徽标、原因标签、描述
- 操作：已处理（弹框填备注）/ 忽略 / 删除 / 详情（展开看用户问题 + AI 回复 + 模型）
- 筛选：状态 / 赞踩 / 原因 / 时间范围 / 用户名

**处理状态**：`pending`（待处理）/ `resolved`（已处理）/ `ignored`（已忽略），管理员可填 `admin_note` 记录处理方式。

**导出**：CSV 导出全部点踩明细（时间/用户/模型/原因/描述/问题/AI回复），带 BOM 供 Excel 直开，用于构造 bad case 评测集/离线分析。

## 数据模型

### message_feedback 表（新建）
```sql
CREATE TABLE IF NOT EXISTS message_feedback (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  INTEGER NOT NULL,
    conv_id     TEXT NOT NULL,            -- 冗余，便于按会话查
    user_id     TEXT NOT NULL,
    rating      TEXT NOT NULL,            -- 'up' | 'down'
    reasons     TEXT,                     -- JSON 数组（点踩原因）
    comment     TEXT,                     -- 自由描述
    created_at  TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending/resolved/ignored
    admin_note  TEXT,                             -- 管理员处理备注
    UNIQUE(message_id, user_id)           -- 一人一消息一反馈（覆盖更新）
);
```

### messages.meta_json 扩展
- `token_usage`: `{prompt_tokens, completion_tokens, total_tokens, calls}`
- `model`: `"provider/model"`（本轮实际用的模型，供反馈管理页展示）

### messages 表
- `add_message` 返回新消息 id（供前端反馈定位）
- `list_messages` SELECT 补 `id`

## 接口

### 用户视角（/api/chat/feedback，提交/取消自己的反馈）
| 接口 | 作用 |
|---|---|
| `POST /api/chat/feedback` | 提交/更新反馈（FeedbackIn: message_id/conv_id/rating/reasons/comment） |
| `GET /api/chat/feedback?conv_id=` | 查当前用户在某会话内的反馈（重载时恢复高亮） |
| `DELETE /api/chat/feedback/{message_id}` | 用户取消自己的反馈 |

### 管理员视角（/api/feedback，全局只读 + 处理）
| 接口 | 作用 |
|---|---|
| `GET /api/feedback/overview` | 全局统计（赞/踩/会话数/点踩率/待处理/原因分布/趋势） |
| `GET /api/feedback/list` | 明细分页（join messages+users，带原始问答+模型，支持状态/赞踩/原因/时间/用户筛选） |
| `GET /api/feedback/export` | CSV 导出全部点踩明细 |
| `PATCH /api/feedback/{id}` | 管理员改 status + admin_note |
| `DELETE /api/feedback/{id}` | 管理员删除（按 feedback id，区别于用户取消） |

管理员接口均 `require_admin`；overview 的 `total_sessions` 为全局会话数（`COUNT(conversations)`），点踩率分母用它。

## 踩过的坑

1. **SQLite 裸字符串真值陷阱**：`feedback_overview` 的 SQL 原写 `WHERE rating='down' AND reasons`，SQLite 对字符串列做布尔判断时**尝试转数字**，`'["其他"]'` 开头 `[` 转成 0 -> 判假，导致所有 reasons 为 JSON 数组字符串的点踩记录全被过滤，`reason_counts` 恒空。**修**：`AND reasons IS NOT NULL AND reasons != ''`（显式判非空）。以后 SQLite 里裸字符串列做 `WHERE col` 都不可靠。

2. **navigator.clipboard 在 http 不可用**：内网 IP:8088 是 http（非 secure context），`navigator.clipboard` 为 undefined 或被拒。**修**：`copyText` 回退 `execCommand('copy')`。

3. **runtime_config 覆盖 .env**：管理员在前端配置中心保存的 `llm_model_name` 存进 `runtime_config` 表，后端 lifespan 的 `apply_global_overrides` 会用它覆盖 .env。改 .env 的模型名不生效时，先查 `runtime_config` 表（`UPDATE runtime_config SET value='...' WHERE key='llm_model_name'`）。详见 [[mangrove-runtime-config-registry-invariant]]。

## 概览页调整

- 记忆系统命中统计板块（`MemoryHitCard`）仅管理员可见（`isAdminish` 判断），普通用户不显示。
- 反馈统计不放概览页，集中在反馈管理页（避免概览页信息过载）。

## 验证

- 后端单测：`scripts/test_library_dedup_scanner.py`（巡检 details，非本功能）。
- token 捕获：`resp.usage_metadata` 实测有值（Qwen3.6@6012，input/output/total 齐全）。
- 反馈链路：提交/取消/查看/处理/导出 均手工跑通。
- 点踩原因分布：修复 SQLite 真值后 `reason_counts={'其他':1}` 正确计数。
