# Agentic Runtime vNext 覆盖感知文档检索执行报告

> 日期：2026-07-31
> 范围：DR-00–DR-07 工程实现与工程验收
> 状态：`engineering_verified_pending_user_acceptance`
> 未执行：提交、推送、版本、标签、默认入口切换、外部 Provider/OCR 外发

## 1. 结论

DR-00–DR-07 的工程工作已完成。Pi 文档任务不再在启动前生成整份高质量 OCR sidecar，
而是由 Pi 先观察来源、冻结覆盖契约，再自主调用候选发现和权威精读能力；独立完成门依据
覆盖账本、候选处置、逐结果证据、对象边界和必需字段决定是否允许完成。

真实 109 页扫描 PDF 的四类新 revision 回放均符合设计：指定页和首个完整对象各只精读
1 页；全量散落查询完成 109/109 可信发现，只精读 9 个候选页；注入第 40 页发现失败时
拒绝完成。原任务、原候选和历史缓存没有被改写或删除。

本报告只能证明工程门通过。用户尚未在真实工作台亲自操作本轮 UI，因此 DR-07 的最终
产品状态是“工程验证通过，等待用户实际操作验收”，不能写成整个 Phase 4 完成或生产封板。

## 2. 已验证事实

### 2.1 架构与边界

- `CoverageContract` 冻结来源、内容单元、结果基数、完整性、顺序、对象边界、必需字段和
  停止语义；没有按用户问法写关键词路线。
- `CoverageLedger` 持久化已观察、发现候选、权威读取、低质量、未知、证据绑定、候选排除、
  缓存命中和完成门结论。
- 严格 `all` 任务中，每个发现候选必须形成结果，或在权威读取后用 EvidenceRef 明确排除；
  不能用“已扫描全部页面”掩盖漏掉候选。
- 数字 PDF 发现复用文本层；扫描/混合 PDF 使用已安装的 RapidOCR 3.9.2 建立低分辨率发现
  索引。发现结果只做召回，正式结果仍通过现有 MinerU/Paddle 权威读取。
- 缓存按 Owner 摘要、原件 SHA-256 和解析版本隔离；同 Owner 的既有高质量不可变页缓存可
  直接复用。取消信号会贯穿 Relay 和同步解析线程，取消后不再写缓存或账本。
- 文档工具 Grant 在宿主 Relay 重新核对 Token、Owner 绑定、Task、Revision、Run、Purpose
  和 TTL；内部 Relay 是业务执行阶段唯一新增的精确出口，没有开放公共网络。
- 工作台展示冻结解释、严格/尽力完整性、已发现/总数、已精读、证据、未覆盖和缓存命中，
  并把来源识别、候选发现、证据精读、Agent 处理、覆盖验证映射为可恢复业务阶段。

### 2.2 真实 Pi 与语义自主性

- 固定 `pi-coding-agent 0.80.10` Docker 生产 Extension 探针：`PASS`。
- 本地 `Qwen3.6-35B-A3B` 冻结 16 条覆盖语义语料连续三轮均为 `16/16`。
- 真实 Pi 对“首个 / 全部 / 实质歧义”三类任务连续三轮共 `9/9`；歧义任务只调用单问题
  澄清，明确任务完成冻结、发现/读取和独立验证；结束后残留容器为零。
- 测试过程中发现并修复一个真实台账错误：顺序读取新页面时，历史可信页曾被误降级为
  低质量。新增回归证明多次可信读取后历史状态保持可信。

### 2.3 真实 109 页文件 A/B

原件 SHA-256：
`bb598a503f85acb5d391366eb83eba31a94ce98e3789b76fcf98d1ee8d50b955`。

旧试验路径的真实墙钟基线为 `1,071,223 ms`（约 17 分 51 秒），且之后因 sidecar 复制哈希
不一致失败。新路径同一原件的缓存回放结果如下：

| 场景 | 墙钟时间 | 发现覆盖 | 高质量精读 | 缓存命中 | 完成门 |
|---|---:|---:|---:|---:|---|
| 指定第 20 页 | 1.211 秒 | 授权范围 1 页 | 1 | 1 | 通过 |
| 第一个完整对象 | 0.958 秒 | 稳定顺序至首个对象 | 1 | 1 | 通过 |
| 全部“都江堰”散落项 | 8.092 秒 | 109/109 | 9 | 发现 109、证据 9 | 通过 |
| 注入第 40 页失败 | — | 108/109 | 0 | — | `replan_required` |

全量查询候选页为 `2, 4, 7, 13, 36, 37, 60, 61, 99`，与人工高质量真值 `9/9` 一致；
非候选页高质量 OCR 为零。冷启动 RapidOCR 发现索引曾完成 109 页，按缓存文件时间跨度测得
约 `483.276 秒`，精确召回同为 `9/9`；该冷启动数字来自缓存 mtime，因为原测量进程在缓存
完整写入后、汇总输出前被人工终止，不能冒充脚本直接墙钟输出。

## 3. 验证证据

```text
E:\python3.13\python.exe -m pytest tests/test_document_tool_relay.py
  tests/test_document_retrieval.py tests/test_agentic_runtime.py
  tests/test_pi_runtime_workspace_api.py -q --basetemp=.pytest-tmp\coverage-final
=> 57 passed, 2 warnings

E:\python3.13\python.exe scripts\probe_pi_document_tools.py
=> PRODUCTION_DOCUMENT_TOOL_PROBE=PASS

E:\python3.13\python.exe scripts\evaluate_coverage_semantics.py  （连续 3 次）
=> 16/16、16/16、16/16

E:\python3.13\python.exe scripts\probe_pi_coverage_semantics.py
=> 9/9，residual_containers=""

E:\python3.13\python.exe scripts\validate_real_109_coverage.py ...
=> 四场景符合第 2.3 节，失败注入正确关闭

E:\python3.13\python.exe -m pytest tests -q --basetemp=.pytest-tmp\full-final
=> 672 passed, 4 skipped, 4 warnings

cd frontend && npm run test:e2e
=> 51 passed

cd frontend && npm run build
=> TypeScript 检查与 Vite 生产构建通过

git diff --check HEAD
=> 通过
```

4 个跳过项是需要显式参数的真实数据库或大规模性能门，不是本轮失败。构建仅保留既有
大 chunk 警告；测试警告来自第三方依赖弃用或字段遮蔽。

## 4. 基于代码的推断

- 对首次出现且没有任何缓存的超长扫描文件，低成本发现仍需遍历全部获准页面；其成本不会
  凭 Pi 推理消失，但已与高质量权威 OCR 解耦，并可在后续任务按 Owner/原件版本复用。
- 当前真实文件的“全部”路径大幅快于旧前置路径，主要来自 109 页发现缓存和 9 页证据缓存
  命中；不能把缓存回放的 8.092 秒宣传成所有冷启动文件的固定性能。
- 候选显式处置和逐结果证据绑定降低静默漏项概率，但最终准确率仍受发现索引质量和业务对象
  边界理解影响，因此真实泛化集和用户操作验收仍有价值。

## 5. 尚未验证与保留边界

- 未执行用户本人在工作台的最终点击验收；下次应以指定页、首个、全部和故障重试各操作
  一次，核对解释、进度、结果和恢复体验。
- 未调用外部 OCR 或外部模型，未改变任何 BYOK/平台连接外发边界。
- 未切换 Legacy/vNext 默认入口，未创建正式 Delivery，未声明完整 PG-05 或整个 Phase 4
  完成。
- 未执行提交、推送、GitHub Issue、版本或标签操作。

## 6. 回滚与接管

新能力只在 Pi PDF 文档 Grant 存在时启用；撤销 Grant 或关闭 vNext 文档工具后应失败关闭，
Legacy 默认入口保持不变。不得恢复“Pi 启动前整份高质量 OCR”作为静默兜底，也不得删除
历史用户缓存或任务。详细契约见规格、ADR-0025 和 DR-00–DR-07 任务拆分。

## 7. 2026-08-02 收口复核

进入下一阶段前使用当前未提交工作树重新执行：

```text
聚焦后端：57 passed, 2 warnings
全仓后端：672 passed, 4 skipped, 4 warnings
完整 Playwright：51 passed
前端生产构建：通过，保留既有大 chunk 警告
git diff --check HEAD：通过
```

没有发现本专项的自动化回归，产品状态仍保持
`engineering_verified_pending_user_acceptance`。本次复核没有执行用户代表任务、正式
Delivery、外部 Provider/OCR、提交、推送或默认切换，因此不得把重新跑绿的自动化升级为
用户验收或整个 Phase 4 完成。当前剩余问题和推荐顺序见
[Phase 4 当前问题与优化审计](2026-08-02-phase4-current-issues-audit.md)。
