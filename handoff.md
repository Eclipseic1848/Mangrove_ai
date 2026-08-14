# Mangrove 零上下文交接

> 最后现场核验：2026-08-13
>
> 当前分支：`main`
>
> 公开远端：`origin` → `https://github.com/Eclipseic1848/Mangrove_ai.git`
>
> 当前阶段：AC-07 能力信任与发布治理；#9 已验收，下一工单为 #10

## 1. 当前结论

当前能力、工单和路线状态只以 `docs/status/current.md` 为准。AC-07 新仓库 #9 已完成标准 OCI
image signature 本地 PoC、真实双包验证、最终双轴复审和用户验收；该结论不代表能力已经晋级、
平台能力已经发布或普通用户权限已经扩大。

下一工单是新仓库 #10“个人能力自动晋级 `verified`”。未经用户确认，不自动开始 #10 实现，
也不执行生产迁移、能力晋级、平台发布、受众开放、版本或标签操作。

## 2. 接手步骤

1. 按顺序读取 `AGENTS.md`、本文件、`docs/status/current.md`、`CONTEXT.md` 和 `docs/agents/`。
2. 现场核对：

   ```powershell
   git status --short --branch --untracked-files=all
   git branch --show-current
   git rev-parse HEAD
   git rev-parse origin/main
   git remote -v
   ```

3. 读取新仓库 #10、AC-07 规格、ADR-0029，以及 #34、#35、#9 的执行报告。
4. 先判断 #10 处于需求澄清、规格、拆票、实现、诊断还是审查阶段；展示产物和未决问题后等待
   用户确认，不自动跨越人工阶段门。
5. 运行环境需要验证时使用 `http://127.0.0.1:8088/api/health`；`5173` 不是产品入口。

## 3. #9 已验证事实

- 固定 Cosign 3.0.6、ORAS 1.3.2 和 Zot 2.1.20，并校验来源、版本与内容锁；
- 两项冻结能力均完成本地 OCI Layout → 127.0.0.1 临时 Registry → 按 digest 签名/验证 →
  递归复制主体与 Referrers 到独立 Layout；
- 加密 Sigstore 私钥位于项目、数据库和任务目录之外，口令未进入 argv 或持久化证据；
- 错误公钥、主体篡改、非法事务路径、未绑定工具来源与版本均失败关闭；
- 预启动取消、ORAS 重验期间取消、回调异常、进程崩溃和重复执行均有回归；
- 八个 Capability 测试文件 `133 passed`，最终真实双包验收 `status=passed`，临时 Registry、容器、
  运行存储和网络零残留；
- Standards 与 Spec 最终复审均为 PASS，用户验收通过。

权威实现证据：

- `docs/plans/2026-08-13-agentic-capability-ac07-04-execution-report.md`
- `src/capability_governance/oci_signing.py`
- `scripts/verify_capability_signing_ac07.py`
- `tests/test_capability_signing.py`

## 4. 下一工单：新仓库 #10

目标是把 ValidationRun 与供应链硬门贯通：只有同一精确 digest 的 Smoke、授权真实任务重放、
失败关闭、权限、来源一致性、Trivy、Syft SBOM、Verifier 和清理证据全部通过，个人能力成熟度才
能确定性晋级为 `verified`。

开始前必须重新确认：

- 晋级命令的业务范围、证据含义和失败缺口展示；
- Owner、管理员与普通用户的读取和写入权限；
- 幂等键、预期状态冲突和不可变失败记录；
- Python Tool 与 MCP 的成功/失败夹具；
- 是否需要生产迁移、真实任务、外部模型或新工具；这些动作必须分别授权。

#10 不包含平台快照发布、`admin_gray`、普通用户开放或正式签名密钥管理；这些属于后续工单。

## 5. 稳定边界

- `8088` 是统一产品入口；`5173` 只用于前端开发。
- `/data-prep` 是主工作台；迁移完成前不得删除历史兼容入口或 Legacy Delivery 读取。
- 只有 `delivery_published` 且完整性/QA 通过的 `output_id` 是正式交付。
- TaskRevision、来源快照、连接版本、能力 digest 和 Owner 隔离必须冻结且失败关闭。
- AC-06 两项历史 `admin_gray_only` 包只是过渡例外，不扩大普通用户权限。
- 普通用户、管理员、超级管理员是产品角色；“高级用户”不是权限角色。
- 管理员读取跨 Owner 业务正文必须填写原因并产生审计记录。
- 无能力任务不能新增治理启动负担；完整 PG-05、远程 MCP、真实外部 Provider 安全端到端、
  30 项泛化集和 8B 仍未完成。

## 6. Git 与发布边界

- 当前公开开发分支为 `main`，远端为 `origin`；每次操作前仍须现场核对。
- 使用明确文件允许列表，禁止 `git add .`、强推、`git reset --hard` 或 `git clean`。
- 本机配置、绝对路径、Secret、运行数据、签名私钥和本地审计不得进入版本控制。
- Commit、Push、分支、PR、Release、标签、Issue 写入和外部发布都需要用户明确授权。
- 完成工程测试不等于用户验收或生产资格。

## 7. 权威文档

- 当前状态：`docs/status/current.md`
- 领域词汇：`CONTEXT.md`
- 工程规则：`AGENTS.md`
- AC-07 规格：`docs/plans/2026-08-06-agentic-capability-ac07-spec.md`
- AC-07 ADR：`docs/adr/0029-capability-validation-lifecycle-and-platform-publication.md`
- Issue/标签约定：`docs/agents/`

历史计划、研究和执行报告是证据，不再维护滚动状态。出现冲突时，以代码/数据库实况和
`docs/status/current.md` 的最新核验为准，并修正文档，不得静默选择方便的版本。
