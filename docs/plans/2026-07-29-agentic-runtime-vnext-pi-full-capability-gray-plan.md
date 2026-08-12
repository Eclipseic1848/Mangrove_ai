# Agentic Runtime vNext：Pi 全能力生产灰度计划

> 日期：2026-07-29
>
> 状态：PG-01 至 PG-04 已形成管理员灰度候选链；PG-05 独立验证纵切面已实现并取得
> 真实 PDF、Word、Excel、官方会话恢复和一项提示注入任务已有阶段性证据；
> PG-05 整体和正式交付资格未完成
>
> 当前适用分支：`v0.0.7`；历史专项分支：`feature/agentic-runtime-vnext`
>
> 上位决策：[ADR-0017](../adr/0017-agentic-runtime-vnext.md)

## 1. 决策结论

阶段 1 赛马中，Pi、OpenCode、Deep Agents 分别通过 16/18、16/18、12/18。这个结果
证明 Pi 和 OpenCode 已具备有效的动态工具循环能力，但没有证明任何候选可以不加
Mangrove 生产控制就直接替换 Legacy。

用户明确把“真实任务能够完成并形成可用交付”置于首要位置，并接受在必要时分级放开
权限。因此不再执行 ADR-0017 原先的 LangChain/LangGraph fallback，也不先从零编写
Agent Loop。当前选择：

- 首选完整 `pi-coding-agent`，通过 JSONL RPC 嵌入 Mangrove；
- 在任务级 Docker Desktop 容器内开放 `read/write/edit/bash`、Python、Node、Git、
  npm、PyPI、apt、公共网络、Skills、扩展和成熟开源工具；
- OpenCode 保留为后备 Adapter，不在首期同时建设两套生产 Runtime；
- Mangrove 继续掌握目标、用户所有权、任务状态、权限提升、候选验证和正式交付；
- Legacy 保持默认，Pi 先作为管理员可见的显式灰度入口。

“完整能力”表示 Pi 可以在授权的执行环境中查看资料、编写临时步骤、安装依赖、运行代码、
观察结果和修正方案，不表示每个普通任务默认继承整个 Windows 宿主机的所有权限。

## 2. 生产目标

首期不再以冻结夹具或 Adapter 分数作为最终产物，而是打通真实工作台纵切面：

```text
真实用户目标和附件
→ 创建不可变 GoalContract
→ 建立任务级工作区和 Pi 容器
→ Pi 观察来源并动态执行
→ 生成 CandidateArtifact
→ Mangrove Verifier 检查
→ 正式 Delivery 或带原因的未验证候选
→ 页面预览、下载、取消、恢复和追溯
```

首批必须覆盖：

1. PDF 附件 2“服务费用标准及明细”只输出一张 CSV；
2. Word 商务条款抽取为 TXT，禁止全文照搬；
3. Excel 单/多工作表精确定位并输出；
4. 至少一个未增加专属 Prompt、Skill 或分支的未知文件任务。

## 3. Runtime Seam

业务层只依赖一个深 `PiRuntime` Module，Interface 保持为：

```text
start / resume / steer / cancel / inspect
```

生产使用 `PiRpcAdapter`，测试使用受控假进程 Adapter。JSONL RPC、容器生命周期、工作区、
事件翻译、超时、取消和结果发现隐藏在 Module 内，不散落到路由、工作台或领域工具中。

Pi 负责：

- 动态观察、计划、工具选择和重规划；
- 容器内文件操作、脚本编写和命令执行；
- 按任务需要安装、调用成熟开源工具；
- 生成候选文件和简洁行动事件。

Mangrove 负责：

- GoalContract、用户/任务/revision 所有权；
- Runtime 灰度选择、权限档位和显式提升；
- 容器创建、终止、预算和事件持久化；
- 原始附件只读、任务目录隔离和敏感信息注入；
- CandidateArtifact 登记、Verifier、Delivery、Manifest 和下载；
- 取消、恢复、幂等和失败说明。

## 4. 权限档位

### 4.1 标准增强模式

管理员灰度任务默认使用：

- Pi 整个进程运行在任务级容器；
- 当前任务输入以只读方式提供；
- 独立工作目录和候选输出目录可写；
- 容器内允许 Shell、Python、Node、Git、npm、pip 和必要的包安装；
- 允许访问公共 GitHub、npm、PyPI、apt 和当前配置的本地 Qwen/LAN 解析服务；
- 默认不挂载宿主 `.env`、应用数据库、Cookie、其他任务、其他用户目录和 Docker Socket。

公共依赖下载不等于允许上传业务正文。若工具要把附件、正文、截图、音频或表格内容发送到
外部模型或解析服务，仍须先说明目标服务、数据、用途和风险并取得用户确认。

### 4.2 扩展访问模式

管理员可以针对单个任务显式授予：

- 额外挂载的指定目录；
- 指定局域网端点；
- 私有 GitHub/npm 源所需的任务级凭证；
- 更高资源、时长或网络范围。

授权必须记录任务、用户、范围和有效期，不得自动继承到其他任务。

### 4.3 宿主机开发模式

容器无法完成的开发诊断可以由管理员逐任务开启宿主机模式。该模式必须显示非生产标识，
记录命令与结果，并禁止普通用户使用。不得因为存在该模式而把宿主机全权限设为生产默认。

## 5. 候选与正式交付

Pi 可以自由生成候选产物，但不能把“文件已写出”直接等同于正式成功：

- Verifier 通过：当前灰度仍只显示“已验证候选”；完成后续生产门并接入
  Delivery Publisher 才能原子发布正式下载；
- Verifier 未通过但文件可打开：向用户展示“未验证候选”、失败原因和下载/重试/修改目标
  操作，不再只显示无法操作的弹窗；
- 文件损坏、越权、来源不明或包含明确禁止内容：不提供下载，保留可操作失败说明；
- 取消任务：不得创建新的正式 Delivery。

## 6. 实施工作包

### PG-01 文档与契约

- 更新 handoff、AGENTS、CONTEXT、总计划、ADR、Charter、任务拆分和工作流说明；
- 保留阶段 1 原始分数和“无候选直接获生产资格”的历史事实；
- 明确 Pi 是生产资格实现方向，而不是已通过生产门；
- 明确保留 Phase 4C、Phase 5A、Phase 5B 和后置服务器工作。

### PG-02 Pi Runtime 基础

- [x] 定义 Runtime 请求、权限档位、事件、候选结果和失败模型；
- [x] 实现生产 Pi JSONL RPC Adapter 与测试假 Adapter Seam；
- [x] 实现每任务容器、JSONL RPC、超时、取消和容器子进程终止；
- [x] 使用中文注释说明权限、状态转换和失败关闭的原因；
- [ ] （部分完成）Pi 官方 JSONL `resume`、进程重启恢复和 HTTP 创建幂等已实现；
  `steer / inspect / get_entries` 与多位置强杀恢复矩阵未完成。

### PG-03 Docker 全能力环境

- [x] 固定 Pi 0.80.10、Node、Python、Git、Bash、curl 的任务镜像；
- [x] 输入只读、工作区可写、候选目录独立；
- [x] 标准增强模式开放依赖下载和 LAN 模型访问；
- [x] 保存镜像身份、运行参数、RPC 轨迹、stderr 和产物引用；
- [x] 仅使用 Docker Desktop 做任务级功能环境，不宣称服务器就绪；
- [x] 按真实 PDF/Word/Excel A/B 固定 ripgrep、PDF/Office 等首批转换依赖；
- [ ] （部分完成）Egress PolicyGate/sidecar 已接入 `PiRuntime.start/resume/cancel`，
  业务执行主链通过真实 Docker + 本地 Qwen + Verifier；独立依赖获取状态机未完成。

### PG-04 工作台灰度接入

- [x] 保持现有 `/api/semantic-workspace/tasks`、详情、SSE、取消、版本和下载兼容；
- [x] 管理员显式选择 Pi 增强模式，Legacy 默认不变；
- [x] SSE 显示精简行动摘要，不展示隐藏思维链；
- [x] 上传后仍立即预览原文件；
- [x] 候选结果与正式交付视觉区分；
- [ ] 补齐失败后的重试、修改目标、提升权限等全部真实操作。

### PG-05 真实纵切面与准入

- [ ] （部分完成）用户原始 PDF、真实 Word、真实 Excel 已形成候选与独立验证证据；
  未知任务未完成；
- [ ] （部分完成）PDF 核心任务本地 Qwen 已连续 3/3，Word/Excel 连续 3/3 未完成；
- [x] 独立 Verifier 重开候选与 PDF/DOCX/XLSX 原件，校验候选集合、逐字证据和目标语义；
- [x] 验证失败可在同一 Pi RPC 会话有界重规划，候选验证报告已持久化并展示；
- [x] 使用 Pi 官方 Extension Hook 治理大工具输出，并修复超过 64KB 的 RPC JSONL 事件；
- [ ] （部分完成）真实会话恢复、重复请求幂等、跨用户取消/下载、一项真实提示注入
  和运行中真实容器取消已通过；多位置强杀恢复未完成；
- 扩展保留集至少 30 项，正确正式交付率不低于 90%；
- 安全、所有权、禁止项和失败不得冒充成功为 100%。

## 7. 当前明确不做

- 不创建或移动版本标签；
- 不切换默认入口；
- 不删除 Legacy 或迁移旧任务；
- 不同时建设 OpenCode 生产链路；
- 不实施最终服务器部署、Linux/GPU、并发容量或长期运行结论；
- 不提前实施 Phase 4C、Phase 5A 或 Phase 5B；
- 不把当前任务级 Docker 环境写成 8B 或服务器验收通过。

## 8. 后续路线不变

Pi 灰度是 Phase 4B vNext 的整改纵切面，不替代总路线：

1. Pi vNext 通过真实生产门并经用户验收；
2. 完成 vNext 影子运行和默认入口决策；
3. 按原计划进入 Phase 4C 图片/音频/视频；
4. 进入 Phase 5A 认证来源安全发现；
5. 进入 Phase 5B Recipe、增量、队列、配额和工程化；
6. 全部功能工程完成、目标服务器条件明确后，执行最终服务器部署和实机验收。

## 9. 开工授权

用户于 2026-07-29 明确要求：

> 先记录当前状态并更新相关 Markdown；完成后按 Pi 全能力生产灰度方案开工。

因此 PG-01 获得完成授权，PG-02 至 PG-05 获得按顺序实施授权。权限提升、外部业务内容
发送、默认入口切换、版本/标签和最终服务器部署仍需单独确认。

## 10. 首个纵切面证据

2026-07-29 已完成管理员显式 Pi 灰度的候选链，工程证据为后端相关用例 `20 passed`、
前端完整 Playwright `39 passed`、生产构建通过，以及真实 Docker + 本地 Qwen + Pi
CSV 工具循环通过。候选仍停在 `candidate_ready`，不会创建正式 Delivery。

详细差异、镜像身份、验证命令和未完成门禁见
[Pi 灰度首个纵切面执行报告](2026-07-29-agentic-runtime-vnext-pi-gray-slice-execution-report.md)。

## 11. PG-05 独立验证纵切面证据

2026-07-29 已增加候选集合/来源/语义独立验证、同会话 Verify→Replan、Pi 官方
`tool_result` 上下文门和通用候选清单 CLI。用户原始 PDF 附件表格核心回放连续
`3/3`，最终上下文门回归 `1/1`；真实 Word 六类商务条款候选重放验证通过；真实
19 工作表 Excel 完整任务 `1/1` 并与原件动态计算的 17 条目标记录完全一致。

这仍不是 PG-05 完成：Word/Excel 连续 `3/3`、依赖获取状态机、更多未知任务、
30 项泛化集和正式 Delivery 均未完成。独立验证证据见
[PG-05 独立验证纵切面报告](2026-07-29-agentic-runtime-vnext-pg05-verifier-slice-report.md)。
恢复与安全证据见
[PG-05 恢复与安全纵切面报告](2026-07-29-agentic-runtime-vnext-pg05-recovery-security-slice-report.md)。

2026-07-29 又完成运行中真实 Pi 容器取消，以及 Egress Controller 的本机组合门：
批准 npm 依赖出口，拒绝未批准域名、云元数据和直连旁路；业务阶段仅放行固定本地模型，
并验证结构化日志与容器/网络清理。随后业务执行阶段已接入
`PiRuntime.start/resume/cancel`，真实 Pi + 本地 Qwen + CSV + Verifier 主链通过。
依赖获取状态机仍未完成，因此 PG-03 保持部分完成。证据见
[PG-05 真实取消与 Egress 纵切面报告](2026-07-29-agentic-runtime-vnext-pg05-live-cancel-egress-slice-report.md)。
