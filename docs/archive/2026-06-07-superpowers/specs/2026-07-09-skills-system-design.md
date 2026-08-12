# Mangrove 技能体系重构设计（skills 声明式化 + 技能清单重建）

日期：2026-07-09
状态：已与用户确认清单与方向，待实施

## 背景与问题

用户审查 skills/ 时发现现有两个"技能"名不副实：

1. `scrape-social-media.md` **从未生效**——全仓唯一真正读取技能正文的调用是
   `loader.py` 里硬编码的 `get_skill("voc-analysis")`；`select_skills()` 看似会"选中"
   scrape-social-media，但该函数除单元测试外没有任何调用方（死代码），容易造成
   "这个技能在生效"的错觉。其内容（MediaCrawler 合规限制、平台支持现状）受众是人不是 LLM。
2. `voc-analysis.md` 是唯一真实生效的技能（analyze 节点 VOC 任务时追加进 system prompt），
   但选择逻辑硬编码在 `skill_for_analysis()` 里，新增技能必须改代码。

用户要求：重新梳理 Mangrove 究竟需要哪些技能，改造为声明式（frontmatter 驱动），
以后新增技能只需新建 md 文件、不改代码。

## 技能的定位（与既有知识层的边界）

| 层 | 是什么 | 存放 |
|---|---|---|
| 内置领域模板 | 固定领域的**完整报告结构**（VOC/招投标/新闻/商品） | `src/conductor/prompts.py` |
| 自学习模板 | Agent 沉淀的**完整报告结构**，frontmatter+质量门 | `data/templates/` |
| 记忆（全局+个人） | 用户/系统**偏好**，与任务类型无关 | `memory/user-preferences.md` + `webui.db.user_memory` |
| **技能** | 跨任务复用的**"做法/经验"**，追加在模板之上 | `skills/` |

**技能准入门槛**（以后新增技能必须同时满足）：
1. 有明确的注入节点（analyze / planner）；
2. 有明确的触发条件（可写进 frontmatter）；
3. 内容是"怎么做好这类事"的经验，不是报告结构（那是模板的事）。

## 确认的技能清单

### 处置现有

- **`voc-analysis` 保留**：内容不变，补 frontmatter（`inject: analyze`，
  `trigger.analysis_type: voc`），行为与现状完全一致。
- **`scrape-social-media` 移出 skills/ → `docs/scrape-social-media.md`**：
  纯文档归档（`git mv`），内容不改。skills/ 里从此每个文件都真实生效。

### 新增三个技能

**1. `comparison-analysis.md` 对比分析**（注入 analyze）
- 触发：intent/keywords 命中 对比/比较/vs/哪个好/谁更 等词
- 价值：高频场景（"对比小米SU7和极氪007的口碑"）现在掉通用摘要兜底，产出无对比结构
- 正文要点：对比维度怎么选（先归纳双方共有的评价维度再逐维对比）；对比表格组织
  （维度 × 对象，差异列）；样本量不均衡时必须显式披露、按占比而非绝对数比较；
  差异点提炼与选择建议；单边数据缺失时诚实标注"数据不足"不硬凑。

**2. `trend-analysis.md` 趋势分析**（注入 analyze）
- 触发：`time_range` 非空 **且** intent/keywords 命中 趋势/变化/走势/演变/舆情变化 等词
- 价值：time_range 全链路（`_recency.py`）已就绪，但分析端没有"按时间组织"的做法
- 正文要点：按时间分桶（按数据跨度选天/周/月粒度）；每桶内主题归纳后跨桶对比增减；
  拐点/突变标注并给出对应时间点的代表原文；数据稀疏时段如实标注"该时段样本不足"，
  不得用插值/推测填补；结尾给趋势小结（上升/下降/平稳/波动）。

**3. `platform-selection.md` 平台选型经验**（注入 planner，**新注入点**）
- 触发：恒注入（`trigger.always: true`；表很短，平台已指定时 LLM 自然少用）
- 价值：planner 现在只有平台名清单（`known_platforms()`），没有"什么主题适合去哪采"
  的经验；用户不指定平台时规划质量靠模型自由发挥
- 正文要点：主题→平台适配表，**严格只收录 `platforms.py::KNOWN_PLATFORMS` 里
  真正有专用采集器的 10 个平台**（抖音/小红书/微博/B站/快手/知乎/贴吧/京东/YouTube/V2EX，
  已核实 `src/collectors/platforms.py:17-19`）：美妆/穿搭/种草/生活方式→小红书；
  数码评测/游戏/长视频深度解说→B站；专业问答/深度讨论/行业分析→知乎；垂直兴趣社区讨论
  （如球队吧/游戏吧）→贴吧；热点事件/舆情→微博；短视频/下沉市场话题→抖音/快手；
  商品评论/购物体验→京东；海外内容→YouTube；技术/开发者社区话题→V2EX。
  **明确边界**：懂车帝/汽车之家一类站点不在这 10 个平台内，不建议 LLM 把它们塞进
  `platforms` 字段——那是 `_PLATFORM_DOMAINS`/`site_domains` 管的另一套机制（站点限定检索），
  `PLANNER_SYSTEM` 本身已经在处理，技能正文需要显式说明"表外话题不归本表，
  按你已知的域名信息处理"，避免技能内容和 `PLANNER_SYSTEM` 的既有规则打架。
  无匹配主题回退全网搜索的原则；一个主题多平台适配时优先选列表靠前者
  （顺序按采集稳定性人工排定，因为 planner 不知道用户 Cookie 配置状态）。

### 评估过但不做（防清单虚胖）

- 领域审查清单注入 checker：`CHECKER_SYSTEM` 已内置防杜撰规则，够用；
- 追问技巧注入 intent：`INTENT_SYSTEM` 已有 few-shot；
- 定时任务周报环比：需要历史运行数据基础设施，量级不同，另立项。

## Frontmatter 范式与匹配语义

```yaml
---
title: 对比分析做法
inject: analyze            # analyze | planner，必填
trigger:                   # 各条件之间 AND；条件值为列表时列表内 OR
  analysis_type: voc       # 可选：匹配 spec.analysis_type
  data_type: [comment, post]   # 可选：匹配 spec.data_type
  intent_keywords: [对比, 比较]  # 可选：任一词出现在 intent/keywords 即命中
  time_range_required: true    # 可选：要求 spec.time_range 非空
  always: true             # 与其余键互斥、独占使用：出现即恒命中，忽略同级其它键
---
<正文：做法描述，追加到对应节点 system prompt>
```

**`inject: analyze` 与 `inject: planner` 的触发字段不同源**，需分开说明：
- `analyze` 技能的 trigger 匹配对象是 analyze 节点已有的 `TaskSpec`（`analysis_type`/
  `data_type`/`intent_keywords`/`time_range_required` 均对此评估），字段与 `data/templates/`
  的匹配口径保持一致。
- `planner` 技能此时 `TaskSpec` 尚不存在（planner 的产出物正是 TaskSpec），可用信息只有
  `understanding`（松散字典：intent/what/where/output），字段结构不固定，不适合做关键词式
  精确匹配。**本轮 planner 侧 trigger 只支持 `always: true` 一种**（`platform-selection`
  正是这种用法）；如未来需要"按主题选择性注入 planner 技能"，需另设计对 `understanding`
  自由文本的匹配方式，本轮不做、也不预留占位字段。

**frontmatter 解析不重复造轮子**：`data/templates/` 的 `templates.py` 已有一套 frontmatter
提取正则 + YAML 解析（`_FRONTMATTER_RE`）。抽出一个共享小函数（如
`src/memory/_frontmatter.py:parse_frontmatter(raw: str) -> tuple[dict, str] | None`），
`templates.py` 与 `loader.py` 都改用它，不各自维护一份正则。

- **与 `templates.py` 现有告警口径保持一致**（已核实 `templates.py:57-58` 无 frontmatter 静默
  `continue`、`61-63` YAML 解析失败才 `logger.warning`、`64-66` 有 frontmatter 但正文为空同样
  静默跳过——三种情况 spec 均按现状对齐）：无 frontmatter 的 md（如 `README.md`）静默跳过，
  不告警（文档本就不该有 frontmatter，这是正常情况非异常）；**有** frontmatter 但 YAML
  解析失败，或解析成功但正文为空，才需要留痕迹。
  **`load_skills()` 仍按文件名排除 `readme`**（和现状一样，不改）——`README.md` 是每次
  加载都会遇到的、预期内的"无 frontmatter"文件，如果不排除、指望"无 frontmatter 自动跳过"
  兜底，会导致每次调用（`load_skills()` 在 analyze/planner 节点每次请求都会调）都对同一个
  正常文件打一遍 info 日志，几乎每个请求都在刷屏同一条无意义日志。按文件名排除是更合适的
  处理方式，下面的"info 留痕"只针对 README 之外、真正意外的跳过情形。
  这是行为变更：技能文件从"禁止 frontmatter"改为"必须 frontmatter"，与 `data/templates/`
  对齐成同一套心智模型。
- **⚠️ 与模板侧的一处不对称，需要单独处理**：`data/templates/*.md`（除 README）全部由
  `save_template()` 机器生成，frontmatter 必然存在——"忘写 frontmatter"这种失误在模板侧不可能
  发生，静默跳过零风险。但 `skills/*.md` 是工程师手写，"本想生效却忘加 frontmatter"是真实的
  失误路径；现状 `load_skills()` 是全量加载不存在这个问题，改造后如果照搬模板侧"静默跳过"就是
  **新引入的失误吞没面**——技能没生效但没有任何日志线索可查。因此技能侧比模板侧多一条：
  无 frontmatter 跳过、YAML 解析失败跳过、**解析成功但正文为空**跳过，这三种情况在 `load_skills()`
  里统一打 `logger.info`（不用 `warning`——毕竟 `README.md` 落入第一种情况是正常现象，
  不是错误，但仍要留可查线索区分"这个文件我确实跳过了、跳过原因是什么"）。
- `skills/README.md` 相应重写（今天早些时候刚写的"禁止 frontmatter"要反转）。

## 代码改造点

| 文件 | 改动 |
|---|---|
| `src/memory/_frontmatter.py`（新建） | `parse_frontmatter(raw: str) -> tuple[dict, str] \| None`，从 `templates.py` 的 `_FRONTMATTER_RE`+YAML 解析逻辑抽出的公共小函数 |
| `src/memory/templates.py` | **两处**调用点都改用 `_frontmatter.parse_frontmatter`：`load_templates()`（第 56 行）与 `record_template_use()`（第 348 行，回写使用统计时重新读取 frontmatter），去掉自己的 `_FRONTMATTER_RE`/解析代码 |
| `src/memory/loader.py` | `load_skills()` 改用 `_frontmatter.parse_frontmatter` 解析每个技能文件；`skill_for_analysis(spec)` 重写为按 frontmatter 匹配 `inject: analyze` 的技能并拼接正文（**函数名保留**，`analyze.py:209` 调用点不动）；新增 `skills_for_planner(understanding: dict) -> str`（只处理 `trigger.always`）；删除死代码 `select_skills()` |
| `src/memory/__init__.py` | 导出增删同步 |
| `src/conductor/nodes/planner.py` | `_plan()` 里 `system = PLANNER_SYSTEM.format(...) + skills_for_planner(understanding)`（planner.py:51） |
| `skills/*.md` | voc-analysis 补 frontmatter；新建 comparison-analysis / trend-analysis / platform-selection；scrape-social-media `git mv` 到 docs/ |
| `skills/README.md` | 重写范式说明（必须 frontmatter + 准入门槛三条） |
| `scripts/test_memory.py` | select_skills 相关断言删除，改写为：frontmatter 解析、voc 触发回归、对比/趋势触发、time_range_required 约束、planner 恒注入、无 frontmatter 跳过（含空正文场景）+ 三种跳过情形均有 `logger.info` 留痕（用 `caplog`/`unittest.mock.patch` 断言日志被调用，而非只断言跳过结果） |
| `AGENTS.md` / `README_AGENT.md` | 同步"技能体系"章节 |

## 触发词表（初版，实施时写进 frontmatter）

- comparison：对比、比较、vs、VS、哪个好、谁更、差异、优劣
- trend：趋势、变化、走势、演变、变动、舆情变化、热度变化

匹配口径与模板关键词匹配一致：小写化后子串命中 intent + keywords 拼接串。

## 测试与验证

1. `python scripts/test_memory.py`：新断言全过（见上表）；
2. `python scripts/test_template_learning.py` / `test_embeddings.py`：模板路径回归无破坏；
3. 端到端冒烟（重启后端后）：
   - "对比小米SU7和极氪007的口碑" → analyze 的 system 含对比技能正文（看日志/trace）；
   - "最近30天小米SU7舆情变化趋势" → 含趋势技能正文；
   - "看看大家对某数码新品的评测和讨论"（不指定平台）→ planner prompt 含平台选型表，
     规划出的 platforms 合理（B站/知乎一类深度评测/讨论向平台，而非塞进未支持的站点名）；
   - VOC 任务回归：voc-analysis 注入行为与改造前一致。

## 不做（本轮明确范围外）

- scrape-social-media 内容改写（原样归档）；
- checker/intent 节点的技能注入点；
- 技能的前端管理页（skills 仍是工程师手工维护的文件，无 UI）。
