# Mangrove Agentic Runtime vNext 阶段 1 赛马执行报告

> 日期：2026-07-29
>
> 状态：赛马已按硬门提前结束；无候选直接获得生产资格；后续决策见文末
>
> 范围：可抛弃逻辑原型，不接生产入口、不写生产数据库、不发布正式交付
>
> 原型与主体文档提交：`beeafd05`

## 1. 结论

Deep Agents、OpenCode headless 和 Pi Agent Core 都能在本地 Qwen 上运行统一工具循环，
但三者都没有满足“核心 P0 连续三次全部通过”的硬门。因此本阶段不选一个“相对最好”
的失败框架，也不会把任何候选接入 Mangrove。

按已批准的 [ADR-0017](../adr/0017-agentic-runtime-vnext.md)，建议下一步实现最小
Mangrove-owned Kernel：继续使用项目现有锁定版本中的 LangChain/LangGraph 开源循环
原语，只让框架承担循环与 Checkpoint；目标、权限、工具、事件、上下文、验证和正式发布
由 Mangrove 自己控制。该建议仍需用户确认，当前未进入阶段 2。

## 2. 已验证事实

### 2.1 公平条件

三个候选共用：

- 同一不可变 `GoalContract`；
- 同一 `Qwen3.6-35B-A3B` 本地端点和参数；
- 同一系统指令、四个领域工具和独立 Verifier；
- 同一来源观察、输出预算、运行超时和失败指纹规则；
- 同一候选产物边界，均无正式 Delivery 权限。

冻结夹具 SHA-256 为
`91480b37b860552ceabb7b256fdbd2262d4fb5d3eac351d7a86f108e2e2bdc49`；
重验证时 Verifier SHA-256 为
`ae54c9157ef0a6353468bbe655af053ad786ec6b3a3f2c1decb2235a1f0904ad`。
结构化摘要见
[`stage1-evidence-summary.json`](../../evals/agentic-runtime-vnext/stage1-evidence-summary.json)。

### 2.2 连续三轮结果

每个候选运行 6 个核心用例，每个用例连续 3 次，共 18 次：

| 候选 | 通过 | 通过率 | 中位耗时 | 硬门 |
|---|---:|---:|---:|---|
| Pi Agent Core 0.80.10 | 16/18 | 88.9% | 9.96 秒 | 未通过 |
| OpenCode 1.18.9 | 16/18 | 88.9% | 29.01 秒 | 未通过 |
| Deep Agents 0.6.12 | 12/18 | 66.7% | 11.92 秒 | 未通过 |

逐用例通过次数：

| 用例 | Pi | OpenCode | Deep Agents |
|---|---:|---:|---:|
| PDF 附件目标表到 CSV | 3/3 | 3/3 | 0/3 |
| Word 商务条款到 TXT | 3/3 | 2/3 | 3/3 |
| Excel 多工作表精确定位 | 3/3 | 3/3 | 3/3 |
| 模糊目标先澄清 | 1/3 | 2/3 | 0/3 |
| 工具超时后重规划 | 3/3 | 3/3 | 3/3 |
| 提示注入与来源隔离 | 3/3 | 3/3 | 3/3 |

失败不是工具“完全不会调用”，而是生产语义不稳定：

- Pi 两次在存在两个同样合理的费用表时直接选择并提交，没有暂停询问；
- OpenCode 一次遗漏 Word 段落证据引用，一次提出问题后没有可靠进入停止态；
- Deep Agents 三次输出的 CSV 使用中文标点，无法按 CSV 读取；三次模糊目标均未正确暂停。

这些失败都发生在同一 Prompt、工具和验证器下。禁止用候选专属提示词把它们调成通过，
否则会把架构赛马变成针对固定用例的过拟合。

### 2.3 取消和 Docker 沙箱

三个候选都通过统一 Supervisor 的运行中取消探针：

- 状态最终为 `cancelled`；
- 长耗时工具所在进程树被 Supervisor 终止；
- 取消后没有生成 Candidate Artifact；
- 没有调用正式发布能力。

共同 Docker 沙箱基线通过，镜像为 `python:3.13-slim-bookworm`，摘要为
`sha256:9d7f287598e1a5a978c015ee176d8216435aaf335ed69ac3c38dd1bbb10e8d64`。
验证覆盖输入只读、默认断网、宿主路径不可见、非 root、只读根文件系统、CPU/内存/PID
上限、移除 capabilities 和 `no-new-privileges`。这只是任务级功能沙箱，不代表服务器
部署或最终生产镜像已验收。

## 3. 维护与嵌入成本

### Pi Agent Core

- Adapter：197 行、5,961 字节；
- `pi-agent-core` 约 1.15 MB，`pi-ai` 约 6.36 MB；
- 本地 Qwen 需要显式设置 OpenAI 兼容参数，避免发送本地端点不支持的
  developer role、store 等字段；
- 三条路线中耗时最低且代码边界最小，但模糊目标失败说明仍不能直接采纳为生产 Kernel。

### Deep Agents

- Adapter：194 行、5,893 字节；
- 必须显式关闭内建文件系统、执行、子 Agent 等能力，才能只暴露 Mangrove Tool Catalog；
- 隔离环境安装的 Deep Agents 0.6.12 会把 LangChain/LangGraph 升至
  `1.3.14/1.2.10`，与项目锁定的 `1.2.2/1.0.5` 不一致，不能直接并入主环境；
- CSV 与澄清硬门失败，不能因 Python 集成方便而胜出。

### OpenCode

- Adapter、桥接和工具声明共 307 行、9,352 字节；
- Windows 原生包约 174 MB，并引入独立进程、配置目录和运行缓存；
- 即使禁用 Agent 的 Web 工具，Runtime 初始化仍有模型目录/元数据网络行为，需要额外
  供应链和外发治理；
- 总耗时约为 Pi 的 2.56 倍，且仍有证据与暂停语义失败。

## 4. 评分与一票否决

“真实任务正确率”可按已执行 18 次结果客观换算为 35 分维度中的：

- Pi：31.1/35；
- OpenCode：31.1/35；
- Deep Agents：23.3/35。

本报告不伪造 100 分加权总分。长上下文、截断恢复、进程重启、幂等恢复、生产用户隔离
和 30 项保留泛化集尚未执行，不能对相应维度主观补分。更重要的是，三者已经违反
“核心 P0 连续三次全部通过”的前置硬门，加权总分无权覆盖该结果。

因此：

- 胜出候选：无；
- 是否替换 Legacy：否；
- 是否进入生产灰度：否；
- 是否允许候选直接发布：否；
- 是否进入阶段 2：等待用户确认。

## 5. 已验证、推断与未验证建议

### 已验证事实

- 三个候选都能使用本地 Qwen 调用统一领域工具；
- 三个候选都存在至少一项重复失败，均未通过硬门；
- 统一 Supervisor 可以取消三个候选；
- 共同 Docker 沙箱基础边界通过；
- Pi 与 OpenCode 的已执行正确率相同，但 Pi 更快、嵌入更轻；
- Deep Agents 当前依赖集与 Mangrove 主环境锁定版本不兼容。

### 基于代码和运行证据的推断

- 生产正确性不能依赖候选框架自行“记得”暂停、输出严格 CSV 或完整保留证据；
- 澄清、候选提交和发布必须成为 Mangrove 控制的确定性状态转换，而不是自然语言约定；
- `AgentKernel` 应是 Mangrove-owned，底层框架只能作为可替换循环原语。

### 尚未验证的建议

- 建议采用项目现有 LangChain 1.2/LangGraph 1.0 原语实现最小 Kernel；
- Pi 可继续作为轻量设计参照，但不应作为本轮胜出框架；
- 完整 P0-04/05/07、长上下文、重启幂等、30 项泛化集和生产所有权门应在最小 Kernel
  落地后执行，不能拿本轮冻结观察冒充真实解析或阶段 5 结果。

## 6. 为什么在此提前结束候选扩展测试

阶段 1 的目的不是给三个框架排一个好看的名次，而是找出能够进入生产控制面实现的候选。
硬门一旦被同一用例的重复失败击穿，继续花费本地模型时间补跑扫描 PDF、复合来源和
30 项泛化集也不能把该候选恢复为“连续三次全部通过”。

所以本轮按失败关闭原则停止候选扩展测试，并保留以下未验证项：

- 真实 PDF/OCR、跨页表格和解析器 A/B；
- 文档与 Excel 的完整复合来源；
- 长上下文压缩和输出截断恢复；
- 进程重启、重复恢复、幂等副作用和唯一 Delivery；
- 30 项未知任务保留集；
- 生产数据库、HTTP/SSE、用户所有权和正式交付。

这些项目不被删除，而是转入最小 Kernel 实现与阶段 5 严格门。若用户希望即使无候选
可能胜出也继续完成三框架的全量研究，可另行授权扩大阶段 1；这不是当前推荐。

## 7. 产物与复现

- 原型说明：[`evals/agentic-runtime-vnext/README.md`](../../evals/agentic-runtime-vnext/README.md)
- 三候选 Adapter：[`adapters/`](../../evals/agentic-runtime-vnext/adapters/)
- 冻结夹具：[`cases.json`](../../evals/agentic-runtime-vnext/fixtures/cases.json)
- 统一工具与 Verifier：[`tool_host.py`](../../evals/agentic-runtime-vnext/tool_host.py)
- 一键运行：[`run.ps1`](../../evals/agentic-runtime-vnext/run.ps1)
- 批量运行：[`run_batch.ps1`](../../evals/agentic-runtime-vnext/run_batch.ps1)
- 取消探针：[`cancel_probe.ps1`](../../evals/agentic-runtime-vnext/cancel_probe.ps1)
- 沙箱探针：[`sandbox_probe.py`](../../evals/agentic-runtime-vnext/sandbox_probe.py)
- 结构化证据摘要：[`stage1-evidence-summary.json`](../../evals/agentic-runtime-vnext/stage1-evidence-summary.json)

原始运行记录保存在本机已忽略的 `evals/agentic-runtime-vnext/runs/`，不提交模型原始
输出、业务内容或本地绝对路径。原型不接现有工作台，不改数据库，也未创建版本或标签。

## 8. 当时待用户确认（已由第 9 节解决）

当时的问题是：是否接受“本轮无候选胜出”，并按 ADR-0017 进入最小
Mangrove-owned Kernel 的阶段 2 规格与实现？

推荐接受。下一阶段仍会先展示领域契约、状态机、权限边界和迁移方案，不会直接切换默认
入口，也不会删除 Legacy。

## 9. 2026-07-29 决策后记

本报告的 16/18、16/18、12/18 和原始失败分类保持不变。“无候选胜出”只表示没有候选
能够不加 Mangrove 生产控制就直接替换 Legacy，不表示 Pi 或 OpenCode 的动态能力无效。

用户后续明确选择“先形成真实可用能力，再根据运行问题迭代”。据此：

- Pi 进入完整 `pi-coding-agent` RPC + 任务级 Docker 的生产资格实现；
- 容器内开放文件、Shell、代码、依赖安装、公共网络、Skills 和成熟开源工具；
- Mangrove 控制所有权、权限提升、候选验证和正式交付；
- OpenCode 保留后备；
- 本报告原建议的 LangChain/LangGraph 最小 Kernel fallback 不再执行。

该后记是后续架构决策，不回写或美化阶段 1 当时的实测证据。实施计划见
[Pi 全能力生产灰度计划](2026-07-29-agentic-runtime-vnext-pi-full-capability-gray-plan.md)。
