# 任务泛化评测与扩展

从仓库根目录执行，无网络、无模型费用、无真实任务写入：

```powershell
python -m scripts.evaluate_task_generalization --output logs/task-generalization
python -m pytest tests/test_task_generalization.py -q
```

报告目录包含 `cases.json`、`report.json`、`report.md`。逐例保存合同、输入、独立期望、理由与耗时；报告保存待测源码哈希。预期由明确的契约规则制定，禁止用待测模型或校验器反推期望。任何失败或异常使命令返回非零。

当前500例覆盖10个 TaskFamily 各40例、共享采集40例、组合60例。各家族的40例复用公共合同变体，尚不能代替每类专属语义及执行能力的覆盖。正常文本、边界、缺失/多余字段、类型错误、后置条件、权限与对抗性文本分开标注。恶意文字通过文本校验只说明它作为数据被保存，不证明提示注入防护有效；真实安全执行必须另测。

## 证据分层

- 本套测试：SemanticTaskPlan／TaskSpec 参数与权限合同。accepted、rejected、needs_input 都有明确期望；正确拒绝可算合同通过。
- 执行器验证：使用隔离来源、真实执行器与独立预期产物，检查内容、来源、错误和正式交付门。
- 模型与业务验收：冻结模型、来源及输入，标注多轮约束、错误重采集和无证据回答；模型输出不能充当自己的正确答案。
- 历史任务：按历史实际入口与版本单独建清单。缺来源、缺模型、版本不兼容或缺预期必须标为阻塞／未验证，不能用本套合同通过率替代。

## 新增任务类型

先定位现有入口。当前工作台、采集 TaskSpec 和历史 semantic-harness 的 TaskFamily 不是同一个注册入口，不能只加一个枚举便声称全平台支持新类型。

1. 复用已有输入、来源冻结、权限、输出和验证合同；只有语义确实不同才新增类型或字段。
2. 传统 semantic-harness 使用现有 CapabilityRegistry 注册固定 manifest、executor 和 healthcheck，同时核对绑定、物理计划、验证器及入口支持范围；用户输入不得动态导入实现。当前仅两种固定能力，不能把10个枚举都算成已有执行器。
3. 统一工作台扩展遵守 CONTEXT.md 的能力选择和宿主边界，不新增按业务关键词分支的执行框架。
4. 为新增类型提供独立期望的正常、边界、缺失、类型、约束、组合及对抗变体，每类至少40例；不要仅替换领域名称凑数。
5. 增加最短真实执行器纵切面及拒绝路径；通过模型/执行/产物验收后，才在当前状态台账声明可用。

本生成器不承担账号有效性、真实平台采集、运行时升级或历史任务完整重放的验收。

## 历史合同兼容性

```powershell
python -m scripts.audit_historical_task_contracts --database data/webui.db --output logs/history-contracts.json
```

此命令只读数据库快照，分别验证数据准备、文档抽取和历史语义计划合同；工作台任务列为待单独运行时验证。缺表、缺合同与当前合同不兼容分开记录，不输出输入正文或凭证。发现不兼容时返回非零；业务成功率始终为空，不能代替历史任务重放。输出路径禁止覆盖数据库及其日志。

## 历史清洗节点隔离重放

```powershell
python -m scripts.replay_historical_cleaning --database data/webui.db --artifacts downloads --output logs/history-cleaning-new.json
python -m pytest tests/test_historical_cleaning_replay.py tests/test_historical_task_contract_audit.py -q
```

报告必须使用新文件名，不得位于历史制品目录；排他新建防止硬链接覆盖旧产物。工具从现存解析快照执行真实清洗节点，工作目录为临时目录，禁止网络；逐例比较历史JSONL记录，记录快照哈希、耗时与阻塞原因。已有产物只作回归基线，没有独立人工正确性标注。此重放不包含采集、解析、模型、文档运行时和正式交付，不能据此计算全平台业务成功率；存在阻塞或差异时返回非零。

文档历史任务现可用同一重放命令验证抽取快照到交付节点：校验快照哈希，比较权威JSON/JSONL、证据、血缘、Schema、质量总评及输出格式/数量。缺快照不补空。XLSX另比较工作表名称/顺序、单元格值/类型及公式文本，不比较样式或计算公式。原始文件、OCR/模型、质量明细及coverage/raw_tables/table_recipe_audit重建不在该阶段内；不回写历史数据或发布交付。

`missing_snapshot_kinds` 一次列出文档 Manifest 未登记的全部必需快照种类，`missing_snapshot_kind` 保留首项供旧报告兼容。它不证明对应文件曾生成或后来丢失；已登记文件的缺失、损坏仍由读取与哈希检查报告。未形成历史产物的草稿或失败任务仍计入原清单并保持未验证，不能当成通过，也不能仅凭缺文件判断当前代码退化。

## 真实多轮讨论评测

```powershell
python -m scripts.evaluate_multiturn_live --account <本机测试账号文件> --database data/webui.db --output logs/live-discussion-new.json
python -m scripts.summarize_multiturn_live logs/live-discussion-new.json --output logs/live-discussion-summary-new.json
python -m pytest tests/test_live_multiturn_generator.py tests/test_rewrite_diagnostics.py -q
```

这是会调用已配置外部模型的在线测试，使用正常测试账号与已获授权的模型外发；账号文件含username/password，只能留在本机，不能入库或加入报告。当前固定要求账号默认模型为deepseek-flash。测试只发送合成资料讨论，不确认任务或发布结果。

40组由10个领域的4种对话结构组成，每组4轮，覆盖更正后撤销显示条件、未知值补充后撤回、来源内嵌指令与用户更正、跨话题返回。它们验证讨论语义，不能替代长历史、结果引用、执行复用和恢复测试。

任一请求结果未知或任务计数变化便停止后续发送，不自动重试；已并发发送的请求仍记录结果。明确检查报告后，可用`--start-case N`继续从未发送的组，不能用它重发未知回合。合并报告发现重复场景会拒绝，避免重复计数。原始报告保留逐轮回复与值匹配状态；汇总另列reply_format_counts及complete_plain_json_matching_cases，拒绝NaN和Infinity，不把代码围栏算作纯JSON。汇总另外比较唯一JSON代码块的结构值，额外说明仍需人工审读，不能用结构值匹配掩盖相矛盾的文字。报告均要求新文件名。

汇总按报告计划场景数和逐例计划轮数判定完整性，支持短对话和长历史分别汇总。不同计划分母不得混合。旧报告仅对生成器已知场景回退轮数；未知计划不能仅凭已收到的回答判定完整。

新文档交付会将非None的coverage输入保存为extraction_coverage快照并登记哈希；重放校验后读取。旧历史缺该快照时不从质量报告反推，XLSX的Manifest页差异仍计为未通过。
