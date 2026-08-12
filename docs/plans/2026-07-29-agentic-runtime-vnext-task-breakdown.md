# Mangrove Agentic Runtime vNext 任务拆分

> 日期：2026-07-29
>
> 状态：阶段 1 赛马已完成；PG-05 独立验证纵切面已实现，PG-05 整体未完成
>
> 当前开发分支：`v0.0.8`；无同名标签、未封板
>
> 总原则：每阶段完成后展示产物、差异、证据和未决问题，等待用户确认

## 1. 依赖图

```text
阶段 0 建档
   ↓
阶段 1 三路线赛马
   ↓ 用户选择 Pi 全能力生产灰度
阶段 1B Pi RPC/Docker 真实纵切面
   ↓
阶段 2 控制面与兼容 Seam
   ↓
阶段 3 领域工具与任务沙箱
   ↓
阶段 4 动态 Loop、Context、Skills 与前端
   ↓
阶段 5 影子运行、严格验收与切换
   ↓ 用户确认数据工作台稳定
后续 Conductor 迁移专项
   ↓ 工程功能完成且服务器就绪
最终服务器部署与实机验收
```

## 2. 工作包

| 编号 | 工作包 | 当前状态 | 主要产物 | 完成证据 | 需用户确认 |
|---|---|---|---|---|---|
| VN-00 | 阶段 0 建档与专项建立 | 已完成并确认 | ADR、调研、Charter、评测、任务图、验收、分支、Issue | 文档门、白名单提交、远端引用 | 已确认进入阶段 1 |
| VN-10 | 三路线统一赛马 | 已完成，无候选直接获生产资格 | 3 个 Adapter、固定语料、评分卡 | 本地 Qwen 连续运行、取消、沙箱、硬门 | 已选择 Pi 灰度 |
| VN-15 | Pi 全能力灰度纵切面 | 恢复、安全、真实取消和业务 Egress 主链已实现，PG-05 进行中 | Pi RPC、任务容器、权限档位、真实候选、独立 Verifier | PDF 3/3；Word 回放；Excel 1/1；官方会话恢复 1/1；未知 TXT 注入 1/1；真实取消 1/1；Egress 组合门和主链回放通过 | 权限提升另行确认 |
| VN-20 | 领域契约与持久记录 | Run/Event/Candidate/Checkpoint/幂等最小集已实现 | Goal/Run/Step/Observation 等 Schema 与迁移 | 23 项相关后端用例；多位置强杀恢复待测 | 数据含义、保留周期 |
| VN-21 | Legacy/vNext 兼容 Seam | 管理员显式灰度和候选下载已实现 | 兼容 HTTP/SSE、管理员灰度、回退开关 | Playwright 39 passed；默认仍为 Legacy | 默认切换仍需确认 |
| VN-30 | Tool Catalog 与领域 Adapter | 最小候选清单工具与来源验证已实现 | 来源/表格/文档/产物工具 | 候选清单、PDF/DOCX/XLSX/CSV 来源复读；完整 Catalog 待补 | 新权限或副作用 |
| VN-31 | PDF 表格工具 A/B | 未开始 | pdfplumber/MinerU/Paddle/Docling/GMFT 对照 | 用户附件与公开夹具评分 | 默认 Adapter |
| VN-32 | 任务级 Docker 沙箱 | 标准增强、恢复、真实取消和业务 Egress 主链已实现 | 固定镜像、策略、执行记录、终止机制 | 只读输入、无 Socket、资源；运行中取消通过；Egress 组合门和主链回放通过；依赖获取状态机待补 | 宿主开发模式 |
| VN-40 | 动态 Agent Loop | Verify→Replan 与官方会话恢复已实现 | Observe/Plan/Act/Verify/Replan | 最多 3 次候选修正；恢复 Run ID 不变；steer/inspect 待补 | 自动动作范围 |
| VN-41 | Context 与 Skill Draft | Pi 官方 Extension 上下文门首片已实现 | 压缩引用、Skill 草稿/审批/回放 | 大工具结果限制进入模型上下文；Skill Draft 未开始 | Skill 启用 |
| VN-42 | 前端事件与确认交互 | 精简事件和候选区分已实现 | 精简时间线、真实操作、候选/正式区分 | PC 深浅主题与 39 项 E2E；失败操作待补 | UX 验收 |
| VN-50 | 离线与影子评测 | 未开始 | 固定/保留集、Legacy/vNext 对照 | P0 3/3、泛化 ≥90%、安全 100% | 管理员灰度 |
| VN-51 | 灰度与默认切换 | 未开始 | 管理员→显式试用→默认的分段开关 | 回退演练、零旧交付污染 | 每次扩大范围 |
| VN-60 | Conductor 迁移专项 | 未开始 | 另行 Charter/ADR/评测 | 需数据工作台先验收 | 是否开工 |

## 3. 阶段 0 详细任务

### VN-00A 记录真实状态

- 记录 PDF→CSV 失败和空操作弹窗；
- 明确结构性根因与“不是补一个 Prompt/Skill 即可根治”；
- 记录批次 8A 已验收；
- 记录批次 8B、服务器和实机验收继续后置；
- 记录不创建新版本或标签。

### VN-00B 建立架构决策

- 新增 ADR-0017；
- ADR-0003/0012 只追加部分取代说明，不改写历史；
- ADR-0016 保留为后置的服务器历史设计输入；
- 更新 ADR 索引。

### VN-00C 建立专项资料

- 官方源框架调研；
- Charter；
- 评测语料与赛马规格；
- 本任务拆分；
- 阶段 0 用户验收说明；
- 更新领域词汇表。

### VN-00D 同步权威文档

- `handoff.md`；
- `AGENTS.md`；
- `CONTEXT.md`；
- `plan.md`；
- `mangrove_plan.md`；
- `docs/task-driven-data-workflows.md`；
- 批次 7 延期台账；
- 批次 8A 执行报告后记；
- 8B 历史草案状态与索引。

### VN-00E 发布与任务图

- 只提交文档白名单；
- 推送 `platform/v0.0.6`；
- 从该提交创建并推送 `feature/agentic-runtime-vnext`；
- 建立 GitHub 总 Issue；
- 建立调研赛马、领域契约、工具目录、沙箱、动态循环、上下文与 Skills、前端事件、
  评测切换、Conductor 迁移子 Issue；
- 总 Issue 使用链接清单维护关系，不依赖仓库必须支持原生 Sub-issues。

## 4. 阶段 0 验证门

- 所有新增和修改 Markdown 可按 UTF-8 严格解码；
- 相对链接目标存在；
- `git diff --check` 通过；
- 提交内容没有 `src/`、`frontend/`、测试、数据、运行期文件或秘密；
- 没有创建或移动标签；
- 只推送 `platform`；
- 专项分支与文档提交指向一致；
- GitHub Issue 明确后续阶段未获开工授权；
- 工作区原有无关改动保持不变。

## 5. 人工控制点

下列决策不得由实现 Agent 自动作出：

- 业务范围、字段和结果含义；
- 来源扩大、文件组合和数据保留；
- 用户/管理员权限；
- 外部模型或服务的数据外发；
- 删除、业务写入和不可逆动作；
- Skill 从草稿进入生产；
- AgentKernel 胜出方案；
- 管理员灰度、显式试用和默认入口切换；
- Conductor 迁移开工；
- 版本、标签、外部发布和最终服务器部署。

## 6. GitHub 任务图

- 总任务：[#2 Agentic Runtime 专项整改总任务](https://github.com/Eclipseic1848/Mangrove_platform/issues/2)
- VN-10：[#3 三路线 AgentKernel 统一赛马](https://github.com/Eclipseic1848/Mangrove_platform/issues/3)
- VN-20/21：[#4 GoalContract 与 Agent Run 领域契约](https://github.com/Eclipseic1848/Mangrove_platform/issues/4)
- VN-30/31：[#5 Tool Catalog 与跨模态领域 Adapter](https://github.com/Eclipseic1848/Mangrove_platform/issues/5)
- VN-32：[#6 任务级 Docker 执行沙箱](https://github.com/Eclipseic1848/Mangrove_platform/issues/6)
- VN-40：[#7 动态 Agent Loop、预算与恢复](https://github.com/Eclipseic1848/Mangrove_platform/issues/7)
- VN-41：[#8 ContextManager 与 Skill Draft 治理](https://github.com/Eclipseic1848/Mangrove_platform/issues/8)
- VN-42：[#9 工作台事件、确认与候选结果 UX](https://github.com/Eclipseic1848/Mangrove_platform/issues/9)
- VN-50/51：[#10 固定评测、影子运行与默认切换](https://github.com/Eclipseic1848/Mangrove_platform/issues/10)
- VN-60：[#11 Conductor 迁移专项](https://github.com/Eclipseic1848/Mangrove_platform/issues/11)

Issue 已创建不等于开工授权。#3 已完成可抛弃赛马；用户已授权 Pi 全能力生产灰度，
因此 #4–#10 中仅与该纵切面直接相关的工作可以按
[Pi 灰度计划](2026-07-29-agentic-runtime-vnext-pi-full-capability-gray-plan.md)实施。
#11 Conductor 迁移、默认切换、版本/标签和最终服务器部署仍未获授权。

## 7. 阶段 1 完成状态

阶段 1 的详细证据见
[执行报告](2026-07-29-agentic-runtime-vnext-stage1-execution-report.md)：

- Pi、OpenCode、Deep Agents 分别通过 16/18、16/18、12/18；
- 三者均未做到已执行核心 P0 连续三次全过，因此没有胜出候选；
- 统一取消与 Docker 沙箱基础门通过；
- 未验证的真实解析、复合来源、长上下文、重启幂等、30 项泛化集和生产所有权已显式
  保留，不得标为完成；
- 当前决策是：以完整 `pi-coding-agent` RPC + 任务级 Docker 实施生产灰度，OpenCode
  保留后备，不执行原 LangChain/LangGraph fallback；
- 当前人工控制点是：额外目录/凭证/网络/宿主机权限提升、默认入口切换和外部业务内容
  发送。
