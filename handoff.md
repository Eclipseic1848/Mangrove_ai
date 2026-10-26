# Mangrove 开发入口

## 接手顺序

1. 阅读 [当前状态](docs/status/current.md)、[领域词汇](CONTEXT.md) 和[工程约定](AGENTS.md)。
2. 核对当前分支、提交、工作树改动及目标 Issue，避免覆盖未提交工作。
3. 阅读 Issue 引用的规格与 ADR，按变更范围运行相关检查。
4. 功能或能力状态变化时更新 `docs/status/current.md`；版本变化写入 `CHANGELOG.md`。

## 产品版本与发布核验

本工作树产品版本为 `1.1.0`：`src/__init__.py`是API版本来源，前端package及lock根版本
由永久合同核对。Pi镜像、kernel、adapter和冻结协议使用各自版本，不能全局替换。

正式发行状态、Tag对应提交以[GitHub Releases](https://github.com/Eclipseic1848/Mangrove_ai/releases)
为准；本轮范围为#239–#255及插入#267。构建后核17张品牌PNG原件，并在发行清单记录同一
Git提交、版本和源码/前端摘要。部署复用自然排空与封口门，先完整备份/独立恢复及异机副本；
新实例取得独立进程证明后再恢复入口。失败停止，保留原未知、隔离、失败和回滚材料。

## 主要代码路径

| 路径 | 职责 |
| --- | --- |
| `src/api/`、`frontend/` | HTTP 接口与前端工作台 |
| `src/conductor/` | 目标理解、来源规划与参数契约 |
| `src/semantic_harness/` | 任务修订、执行、候选与交付流程 |
| `src/agentic_runtime/` | Pi 运行时、会话绑定与恢复 |
| `src/candidate_verification/` | 候选核验与规则身份 |
| `src/capability_host/`、`src/capability_governance/` | 能力装载与治理 |
| `src/database_migrations/` | 显式数据库迁移 |
| `tests/`、`frontend/e2e/` | 后端和浏览器回归测试 |

## 常用文档

- [本地启动与功能概览](README.md)
- [开发、测试和提交](CONTRIBUTING.md)
- [Linux 部署、迁移与恢复](docs/deployment/linux-migration-it-guide.md)
- [工作台示例评测](docs/agents/workspace-example-evaluation.md)
- [任务能力矩阵](docs/agents/task-capability-matrix.md)

具体任务进度以对应 Issue 和当前状态台账为准；历史规格中的计划不代表当前执行指令。
