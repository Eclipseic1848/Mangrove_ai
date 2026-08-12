# 模板库/教训库定时巡检设计

## 背景

C 阶段（结构性升级）第一个子项目（教训库前端管理页面）已交付。本 spec 是第二个子项目：**定时巡检**。

模板库（`data/templates/`）与教训库（`data/lessons/`）目前的去重/淘汰机制**只在任务真实触发时才生效**——命中同类失败/兜底任务时才会去查重、才会累加使用统计。这带来两个真实问题：① 若 `embedding_enabled` 曾经关闭或端点一度不可用，那段时间产出的条目会退回关键词 Jaccard 兜底判重，可能漏检本该合并的近似重复项，且事后没有任何机制补救（B2 上线当天就真实堆过 20 条这样的教训草稿，虽然根因已修，但"曾经漏检的存量"这个问题本身与 bug 修复无关，任何时候语义端点不稳定都可能重演）；② `draft` 状态的条目如果长期没有再被同类任务命中，会一直留在库里，没有机制识别"这类任务其实已经不再发生了"。

用户在 brainstorming 过程中把范围从"补做语义去重扫描"扩展为三件套：语义去重扫描 + 长期停滞 draft 清理 + 巡检报告页面。三者共享同一个后台巡检器和同一批配置项，是同一个改动集的自然延伸。

## 目标

- 新增独立的后台定时巡检器（`src/api/library_dedup_scanner.py`），风格参照 `CookieHealthScanner`（异步轮询、开关关闭时空转、可被 `stop()` 打断），但业务完全独立、不共用类。
- 每轮巡检对 `data/templates/`、`data/lessons/` 各自：① 按 `data_type` 分组做两两语义去重比对，发现重复自动合并（LLM 融合正文，保留方使用统计不清零）；② 清理长期停滞的 `draft` 条目。
- 每轮巡检结果落库（`webui.db` 新表），前端模板库页面加第 3 个 Tab 只读展示最近若干轮摘要。

**不做**（本次范围外，已与用户确认）：
- 不做通用巡检框架抽象（新建独立巡检器类，不重构 `CookieHealthScanner` 成通用基类）
- 不做巡检参数的前端配置界面（开关/间隔/停滞天数走 `.env`/运行时配置中心现有的通用机制，不单独做专属 UI）
- 停滞判定不追溯旧数据：无 `created_at` 字段的历史条目跳过停滞判定，不做批量回填

## 设计

### 1. 巡检器骨架

`src/api/library_dedup_scanner.py`，`LibraryDedupScanner` 类，结构对照 `CookieHealthScanner`：

```python
class LibraryDedupScanner:
    def __init__(self) -> None: ...          # self._task/self._stop/self._last_scan_at
    def start(self) -> None: ...              # 幂等启动
    async def stop(self) -> None: ...
    async def _sleep(self, seconds: float) -> None: ...   # 可被 stop() 打断
    async def _run_one_scan(self) -> None: ...            # 一轮巡检：模板库+教训库各扫一遍
    async def _loop(self) -> None: ...        # 热读 settings.library_dedup_scan_enabled，到点跑 _run_one_scan
```

`main.py` 的 `lifespan` 里新增一行 `start_library_dedup_scanner()`（幂等启动函数，与 `start_cookie_health_scanner()` 同款写法），关闭时循环内部自己空转，不需要重启进程切换开关。

新增配置（`src/config/settings.py`）：
- `library_dedup_scan_enabled: bool = False`
- `library_dedup_scan_interval_hours: int = 24`
- `library_stale_draft_days: int = 30`（模板库、教训库公用同一个阈值）
- `library_dedup_scan_max_merges_per_run: int = 5`（每轮每个知识库最多处理的确认合并对数，防止历史积压一次性打出过多 LLM 调用；未处理完的留到下一轮）

### 2. 语义去重扫描与合并

**发现重复**：按 `data_type` 分组，组内每条与"排在它之后的其余条目"做比对（避免同一对被正反各判一次）。模板库复用 `find_duplicate_semantic(data_type, keywords, title)`、教训库复用 `find_similar_lesson(data_type, keywords, intent)`——但两者是为"新内容 vs 库内候选"设计的，直接拿库内已有一条当"新内容"传入会连自己一起搜进候选、产生自我匹配（100% 命中自己）。因此巡检调用时需要在候选结果里**过滤掉当前条目自身的 slug**（`if dup and dup["slug"] != current_slug`），不修改这两个既有函数本身。

**合并**：一旦确认是重复对，**不复用** `curate_template()`/`record_failure()` 的完整判定流程（那是"要不要合并"的判断，巡检已经用确定性的 rerank 阈值确认过了，不需要 LLM 再判一次）。新增两个更简单的"直接融合"辅助函数：
- 模板库：`merge_template_pair(a: dict, b: dict) -> Optional[dict]`（`src/memory/templates.py`），直接调 `achat` 复用 `TEMPLATE_CURATOR_SYSTEM` 的融合语义构造 prompt（"给你两条已确认属于同一类的模板，请融合成一份完整正文"），返回 `{title, keywords, body}` 或 `None`（LLM 失败时巡检跳过这一对，不留残缺文件）。
- 教训库：`merge_lesson_pair(a: dict, b: dict) -> Optional[dict]`（`src/memory/lessons.py`），复用 `LESSON_DISTILL_SYSTEM` 的融合语义，同样返回 `{title, keywords, body}` 或 `None`。

**保留方选择**：模板库取 `uses` 更高者为保留方，教训库取 `occurrences` 更高者为保留方（相同则取先加载到的那条，即 `sorted(glob())` 的顺序，不引入额外随机性）。保留方的 `uses`/`quality_avg`（模板）或 `occurrences`/`status`（教训）**保持原值不变**——延续 B1/B2 已有的"合并不清零历史统计"原则；只更新 `title`/`keywords`/`body`。被合并的另一方文件直接删除（模板库额外清理其 `_vectors.json` 缓存条目，教训库无此步骤）。

**节奏控制**：每个知识库每轮巡检最多处理 `settings.library_dedup_scan_max_merges_per_run` 对（默认5），处理完一对后仿 Cookie 巡检的做法 `await self._sleep(...)` 稍作等待再处理下一对；超出上限的重复对留到下一轮巡检自然继续处理（因为已合并的对不会再被发现为重复，不会重复处理同一对）。

### 3. 长期停滞 draft 清理

- 模板/教训的 frontmatter 新增 `created_at` 字段（ISO 格式时间戳字符串），仅在**新建**时写入（`save_template`/`record_failure` 的新建分支各加一行）；已存在的旧文件不回填，加载时 `created_at` 缺失则该条目**跳过停滞判定**（不因为历史数据没有这个字段就被误删）。
- 巡检时对每条 `status == "draft"` 的条目：若有 `created_at` 且 `(now - created_at).days > settings.library_stale_draft_days`（默认 30），直接删除文件（`delete_template`/`delete_lesson`，无需二次确认——这条草稿本来就是低风险的自动产出，长期没再命中说明这类任务不再发生）。

### 4. 巡检报告

- `webui.db` 新建 `library_dedup_scan_log` 表：`id`（主键自增）、`ran_at`（ISO 时间戳）、`templates_scanned`、`templates_merged`、`lessons_scanned`、`lessons_merged`、`stale_drafts_deleted`（后三项均为整数计数）、`details`（TEXT，该轮每步操作 JSON 明细；2026-07-14 增）。每轮 `_run_one_scan()` 结束后写一行（即便本轮什么都没做也写，计数全 0，可用于确认巡检确实在跑）。
- 新增只读接口 `GET /api/library-dedup-log`（任意登录用户可读，权限与模板库/教训库列表一致），返回最近 200 轮（按 `ran_at` 倒序；2026-07-14 从 20 调至 200，以支撑前端分页查看更多历史）。
- 前端 `Templates.tsx` 加第 3 个 Tab "巡检报告"：拉取该接口，列表展示每轮的时间/扫描条数/合并对数/清理停滞草稿数；有操作的行可点"查看详情"弹 Modal 展示 `details` 明细（合并类显示保留方←被合并方 + 相似度≥阈值；清理类显示被清理条目 + 停滞天数≥阈值）。空状态："暂无巡检记录（巡检开关默认关闭，需在 `.env` 开启 `LIBRARY_DEDUP_SCAN_ENABLED` 后台生效）"。

## 测试计划

- 后端单测（`scripts/test_library_dedup_scanner.py`，新文件；及 `test_template_learning.py`/`test_lesson_learning.py` 各追加合并辅助函数测试）：
  - `merge_template_pair`/`merge_lesson_pair`：mock LLM，验证融合正文正确、失败返回 `None`
  - 去重发现的自我匹配过滤：确认同一条不会被判定为自己的重复项
  - 停滞清理：`created_at` 超过阈值且仍为 draft → 删除；未超阈值/已转正/无 `created_at` → 不删除
  - 巡检整轮：mock 两条模板库语义重复项 → 确认合并为一条、保留方 `uses`/`quality_avg` 不变、被合并方文件消失、`library_dedup_scan_log` 写入一行计数正确
- 前端：`npm run build` 编译通过；手工验证巡检报告 Tab 展示记录列表与空状态

## 验证

1. 单元测试全绿
2. 手工验证：临时调小 `library_dedup_scan_interval_hours` 并开启 `library_dedup_scan_enabled`，往 `data/templates/` 放两条人为构造的语义近似模板，等一轮巡检后确认自动合并、`uses` 未清零；再放一条 `created_at` 手动改到31天前的 draft 模板，确认下一轮巡检被清理；前端"巡检报告" Tab 能看到对应的一行记录

## 后续增强（2026-07-14）

1. **巡检操作明细（details）**：`library_dedup_scan_log` 加 `details` 字段，scanner 在每轮去重合并/停滞清理时收集每步操作明细（合并双方 slug/title、rerank 相似度分数与阈值；清理草稿的 slug/title、停滞天数与阈值），`json.dumps` 后随计数落库。`find_patrol_duplicate`/`find_patrol_duplicate_lesson` 返回值由 `Optional[Dict]` 改为 `Optional[tuple[Dict, float]]`，把 rerank 分数带出来供明细使用。前端巡检报告每行有操作时显示"查看详情"按钮，Modal 展示明细。
2. **三个 Tab 分页**：模板库/教训库/巡检报告统一前端分页（每页 12 条，新建可复用 `Pagination` 组件，页码智能省略）。巡检报告后端 limit 从 20 调至 200 以支撑分页查看更多历史。
