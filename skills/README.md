# skills/ —— 技能资产

> loops Engineering 的"技能"层：把跨任务复用的"做法/经验"沉淀为声明式 Markdown，
> 按任务自动匹配注入对应节点的提示词，让循环复利、不每次从零解释。

## 范式：必须带 YAML frontmatter

```yaml
---
title: <技能标题>
inject: analyze | planner    # 必填：注入哪个节点
trigger:
  analysis_type: voc         # 可选，匹配 TaskSpec.analysis_type
  data_type: [comment, post] # 可选，列表内 OR，匹配 TaskSpec.data_type
  intent_keywords: [对比, 比较]  # 可选，列表内任一词命中 intent+keywords 即算命中
  time_range_required: true  # 可选，要求 TaskSpec.time_range 非空
  always: true                # 与其余键互斥独占：出现即恒命中（目前只有 planner 侧技能这么用）
---
<正文：做法描述，会被追加到对应节点的 system prompt>
```

各 trigger 键之间是 AND 关系。**`inject: planner` 目前只支持 `trigger.always: true`**——
planner 运行时 TaskSpec 还没生成，只有松散的 `understanding` 字典，不支持关键词式精确匹配。

由 `src/memory/loader.py` 的 `load_skills()` 加载、`skill_for_analysis()`/`skills_for_planner()`
按 frontmatter 声明的条件匹配注入。无 frontmatter（`README.md` 除外，按文件名排除）/
YAML 解析失败 / 解析成功但正文为空的文件会被跳过并打 `logger.info` 留痕。

## 技能准入门槛（新增技能前自检）

新增技能前确认同时满足：
1. 有明确的注入节点（`analyze` 或 `planner`）；
2. 有明确的触发条件（能写进 `trigger`）；
3. 内容是"怎么做好这类事"的经验，不是报告结构、也不是失败教训——报告结构属于模板的事
   （固定领域模板见 `src/conductor/prompts.py`，自学习模板见 `data/templates/`），失败教训
   （某类任务历史上容易采集失败、应如何应对）属于 `data/lessons/`（`src/memory/lessons.py`，
   由 Checker 判定失败后自动蒸馏沉淀，无需手写）。

**本目录只放真正会被注入的技能**，不放纯参考文档——那些请放 `docs/`
（如 MediaCrawler 采集策略/合规说明见 `docs/scrape-social-media.md`）。

## 当前技能清单

- [voc-analysis.md](voc-analysis.md)：VOC 槽点分析（注入 analyze，`analysis_type=voc` 时触发）。
- [comparison-analysis.md](comparison-analysis.md)：对比分析（注入 analyze，命中"对比/比较"等关键词时触发）。
- [trend-analysis.md](trend-analysis.md)：趋势分析（注入 analyze，需 `time_range` 非空且命中"趋势/变化"等关键词）。
- [platform-selection.md](platform-selection.md)：平台选型经验（注入 planner，恒触发）。
