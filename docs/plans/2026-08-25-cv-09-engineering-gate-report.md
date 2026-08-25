# CV-09 工程门与实施交接报告

> 状态：ENGINEERING_VERIFIED
>
> 日期：2026-08-25
>
> 工单：GitHub #69
>
> 固定审查点：`51d327d54aa298ab734f30f106f5405bb12619de`
>
> 最终实现提交：`7c2a25ce6e87cc001124b8b81d5715283d5876fc`
>
> 用户验收：尚未执行

## 1. 工程结论

CV-01～CV-08 的正式 Module、追加式 Attempt、统一验证入口、只读 Offer、完整重验、Provider
未知结果收口、精确 Attempt 发布和普通用户工作台已通过 CV-09 工程门。最终实现由以下提交组成：

- `3242eb374f48e004c3f1bac594c4f06b412d96b6`：Candidate 重验正式能力；
- `2d80288d6398b0c68d8de556ff867001818387ca`：保留 Runtime Gate 监督测试接缝；
- `7c2a25ce6e87cc001124b8b81d5715283d5876fc`：关闭运行期 P0 与显式迁移安全门。

双轴审查发现并修复了两个 P1：

1. 既有发布表曾由 Repository 初始化静默 `ALTER TABLE`/建唯一索引；现改为未迁移即失败关闭，
   `0002_delivery_publication_idempotency` 只能通过带一致性恢复点的显式迁移安装；
2. requested→running 后缺少 P0 运行监督；现仅对后台完整重验并发监督 P0，监督异常和完成竞态
   均失败关闭，Provider 按可能已外发收口 `outcome_unknown`，终态提交事务再次复核 P0、活动
   revision/cancel、Pi Candidate 身份和既有 Delivery。

新增测试覆盖 P0 在 Provider Relay 前翻转、结果返回与提交竞态，以及旧 `0001` 数据库使用新
恢复点升级 `0002` 并稳定重放。Standards 与 Spec 最终复核均无剩余 P1/P2。

## 2. 验证证据

| 验证集合 | 最终结果 |
|---|---:|
| CandidateVerification + Runtime/Publisher 聚焦回归 | 196 passed，2 warnings |
| 显式迁移与旧发布表升级聚焦回归 | 19 passed，1 warning |
| 前端 TypeScript 与 production build | exit 0 |
| 前端完整 Playwright（含 axe、键盘、窄屏） | 64 passed |
| 后端全仓（排除 4 个已证明的固定基线失败） | 1999 passed，7 skipped，4 deselected，8 warnings；exit 0 |
| `git diff --check`、Python 编译、严格 UTF-8 | exit 0；UTF-8 failure 0 |
| Secret、本机绝对路径和受保护文件允许列表 | 未发现本次新增 Secret/本机路径；G1 与本地文件未提交 |
| Standards 最终复核 | 无剩余 P1/P2 |
| Spec 最终复核 | 无剩余 P1/P2 |

未经排除的早期全仓运行曾为 `1995 passed, 5 failed, 7 skipped`。其中 G1 冻结身份 1 项和 G4
历史门 3 项均已在固定点 `51d327d...` 的干净临时 worktree 独立复现，属于固定基线失败；另 1 项
Runtime Gate 测试接缝是真实回归，已由 `2d80288d...` 修复。最终全仓命令只 deselect 上述 4 个
已证明的基线失败，没有排除本次实现失败。

7 个 skip 分别属于显式性能门、真实数据库容器门和当前 Windows 无符号链接权限，不代表已经
执行这些外部环境验收。

## 3. 显式迁移演练

最终迁移代码在生产库 `mode=ro` 一致性副本上完成：

- 源库只读写入尝试被拒绝；71 张旧表、10,312 行旧数据逻辑指纹保持不变；
- 导入 35 条旧验证报告，`integrity_check=ok`、外键违规 0；
- 新库一次安装 `0001_candidate_verification_attempts` 与
  `0002_delivery_publication_idempotency`，重复执行保持首次备份 SHA-256 稳定；
- 已存在旧 `0001` 的阶段数据库使用新的当前一致性恢复点升级 `0002`，旧发布记录保持且新字段
  为 NULL，重放稳定；
- 恢复副本可打开且不含 CandidateVerification Schema；最终临时目录自动清理成功。

## 4. 生产状态偏差（必须在 CV-10 处置）

只读现场核验发现：生产 `data/webui.db` 仍没有 CandidateVerification 表或 `0001/0002` 迁移记录，
但 `delivery_publish_intents.request_idempotency_hash` 和
`idx_dpi_owner_request_idempotency` 已存在，非 NULL 记录为 0；2026-08-24 之前的既有恢复点均不含
该字段和索引。

基于代码和恢复点的推断是：CV-07 早期 Repository 静默 DDL 曾在未执行正式生产迁移时触碰生产
Schema。无法仅凭 SQLite 现状精确归因到某个进程或时间，因此不把推断写成已证实执行者。当前
代码已阻止继续静默升级，但历史 Schema 写入不能被表述为“生产库从未触碰”。CV-10 必须先创建
当前一致性恢复点、记录现有 Schema/数据指纹，再让显式 `0001/0002` 迁移接管，不得自行删除
现有列/索引或回滚到会丢失后续数据的旧恢复点。

## 5. 零残留与现场边界

- CandidateVerification 生产表尚不存在，因此没有生产 Attempt/Attempt Lease；现有两类 Capability
  Lease 均为 0；活动未撤销且未过期的模型 Grant 为 0。
- 4173、4174、8000、8001、9000 无测试监听；8088 和 5173 是用户正在使用的既有产品服务，未
  终止。没有 pytest/Playwright 临时进程或额外 Git worktree。
- 两个既有忽略锁文件可取得独占读，未被进程持有；没有把持久锁文件名误判成活动租约。
- 一次失败探针留下约 58 MB 临时副本，客户端策略拒绝 Agent 删除；用户手动删除后已现场核实
  目标不存在且 CV-09 临时目录计数为 0。

## 6. 尚未验证与下一人工门

- 未执行 CandidateVerification 生产 `0001/0002` 迁移；未创建本轮正式恢复点。
- 未调用真实 Provider、未发送真实 Candidate/来源、未产生本轮费用。
- 未重验真实 Candidate、未发布真实 Delivery、未用 Owner 登录完成产品验收。
- 未创建 PR、Tag、Release 或部署；本报告形成时本地提交尚未推送。

因此当前状态只到 `ENGINEERING_VERIFIED`，不是 `LIVE_REVERIFIED`、`LIVE_ACCEPTED`、Provider
资格或生产发布。下一门是 CV-10：先处置生产 Schema 偏差并显式迁移，再由 Owner 对冻结的
Candidate/连接/模型/外发类别作逐 Attempt 确认，最后把重验和正式发布作为两个独立动作验收。

P0-01 整体 Phase 尚未完成，所以本轮没有更新 README、Code of Conduct、Contributing、MIT
License、Security 或 GitHub About；这些文件只在 Phase 完成后迭代。
