# 模板库/教训库自学习曲线评测设计

## 背景

C 阶段（结构性升级）第三个、也是最后一个子项目：评测基线扩展。前两个子项目（教训库前端管理页面、定时巡检）已交付。

项目已有一套端到端评测基线 `scripts/eval_e2e.py`（P1-4 阶段交付）：6 个"黄金任务"真实联网采集+真调 LLM 跑完整流水线，衡量采集成功率/模板命中率/质量均分三个指标，首次基线（deepseek）采集 6/6、模板命中 6/6、质量均分 84.5。还有一个独立的意图评测集 `scripts/eval_intent.py`（16 条真调 LLM，只测意图理解节点）。

模板库/教训库自学习子系统本身有大量单元测试（`test_template_learning.py`/`test_embeddings.py`/`test_lesson_*`系列/`test_library_dedup_scanner.py`等），全部是 mock LLM + 临时目录的确定性单测，验证的是"单个函数在孤立场景下行为对不对"。但**没有任何指标衡量"自学习机制本身随时间推移是否真的在起作用"**——比如同类任务反复出现时模板/教训是否真的收敛而不是无限膨胀、巡检去重是否真的把重复库存控制住。B2 阶段上线当天就真实暴露过一次这类"曲线异常"（`record_failure` 用原始意图比对已蒸馏教训导致 20 条近乎相同的教训永远各建各的，从未合并），而这类问题任何"单轮孤立场景"的单测都测不出来，只有连续多轮观察库存/命中率变化才能发现。

## 目标

新建 `scripts/test_learning_curve.py`：mock LLM 调用，但直接调用 `templates.py`/`lessons.py`/`library_dedup_scanner.py` 的真实合并/去重/转正逻辑，注入合成的同类任务序列，断言库存收敛/命中率/转正节点等曲线行为符合预期，带硬性断言（`assert` 失败即 `sys.exit(1)`），纳入常规回归跑，能真正拦住"曲线异常"这类无法被单轮单测发现的回归。

**不做**（本次范围外）：
- 不做真实全链路多轮重跑（不联网、不真调 LLM，跟 `eval_e2e.py` 的真实性质不同，是两种独立的评测手段，不合并进同一文件）。
- 不做 JSON 报表落盘（这是回归门槛脚本，不是"跑一次看当时基线数值"的性质，跟 `eval_e2e.py`/`data/eval/` 的用途不同）。
- 不做定时自动跑或前端可视化（跟现有 `test_*.py` 系列一样，手动/CI 触发即可，不需要额外基础设施）。

## 设计

### 1. 整体结构

`scripts/test_learning_curve.py`，沿用项目 `test_*.py` 惯例：无 pytest，纯 `def test_x(): assert ...` + `main()` 收集 PASS/FAIL 打印 + `sys.exit(1 if failed else 0)`。用 `tempfile` 建临时的模板库/教训库目录（`patch.object` 重定向 `templates.TEMPLATES_DIR`/`lessons.LESSONS_DIR` 等已有测试文件的常规做法），mock LLM 调用返回确定性响应（`patch.object` 打 `achat`/`curate_template`/`merge_template_pair`/`distill_lesson`/`merge_lesson_pair` 等函数）。不产出 JSON 报表，纯断言+PASS/FAIL 打印。

### 2. 模板库自学习曲线

- **命中收敛**：构造 8 轮"同一 `data_type`+相似 `keywords`/`title`"的合成任务描述，依次调用 `save_template()`（mock 返回确定性的 reuse/merge 判定，不真调 LLM）。断言：第 1 轮建草稿后，后续 7 轮不新增独立文件——最终该类型下模板总数收敛在 1 条，而不是随轮次线性增长到 8 条。
- **转正曲线**：持续调用直到 `uses` 达到 `settings.template_promote_uses` 且 `quality_avg >= settings.template_promote_quality`，断言 `status` 恰好在该轮从 `draft` 变为 `active`（不早于阈值轮、也不晚于阈值轮次+1轮才生效）。
- **巡检去重收敛**：绕过 `save_template()`，直接用 `tempfile`+手写 frontmatter 落盘写入 2-3 个近似重复的模板文件（模拟"曾经漏检的存量"这种真实历史场景），调用 `LibraryDedupScanner._dedup_pass_templates()`，断言合并后库存条目数符合预期（如 3 条近似重复合并为 1 条）；再对同一目录跑第二遍 `_dedup_pass_templates()`，断言这一轮 `merged` 计数为 0（幂等，不会对已经处理过的条目重复合并）。

### 3. 教训库自学习曲线

- **命中不碎片化**：构造 N 轮"同一失败症状模式"的合成任务描述，依次调用 `record_failure()`（mock `distill_lesson`/`merge_lesson_pair` 返回确定性响应）。断言：最终该症状对应的教训文件数 == 1，且 `occurrences` == N——直接对应 B2 上线当天真实修复过的那个 bug 类型（原始意图 vs 已蒸馏教训语义不同质导致永不合并），防止同一根因回归。
- **转正+消费闭环**：`occurrences` 达到 `settings.lesson_promote_occurrences` 后，断言 `status` 转 `active`；再断言 `lesson_for_analyze()`/`lesson_for_planner()` 能读到这条已转正的教训并返回包含其提醒内容的文本——消费侧和存储侧都要验，不能只测存储侧。
- **巡检去重收敛**：镜像模板库第3点，绕过 `record_failure()` 直接落盘写入近似重复的教训草稿文件，调用 `LibraryDedupScanner._dedup_pass_lessons()`，断言合并收敛到预期条目数，再跑第二遍断言幂等（无新合并）。

### 4. 断言失败即回归拦截

所有场景采用硬性 `assert`，触发时 `main()` 的 PASS/FAIL 收集机制会打印 FAIL 详情并 `sys.exit(1)`，与项目现有 `test_*.py` 系列的回归拦截方式完全一致（可以直接加入"全量回归跑一遍所有 `test_*.py`"的例行检查列表，不需要额外的 CI 配置改动）。

## 测试计划

`scripts/test_learning_curve.py` 本身就是测试脚本，没有"测试的测试"这一层。验收：
- 脚本本身在当前代码状态下全绿（证明现有模板库/教训库/巡检器的收敛行为符合预期，这也是本次任务顺带验证现有实现健康度的副产品）。
- 回归确认不影响既有测试：`test_template_learning.py`、`test_lesson_*` 系列、`test_library_dedup_scanner.py` 保持全绿（本任务不修改这些文件本身的逻辑，只是新增一个观察多轮行为的脚本）。

## 验证

1. `python scripts/test_learning_curve.py` → 全部场景 PASS
2. 既有回归：`test_template_learning.py`/`test_embeddings.py`/`test_library_dedup_scanner.py` 等全部保持通过（确认新脚本没有改动任何既有逻辑，纯新增观测）
