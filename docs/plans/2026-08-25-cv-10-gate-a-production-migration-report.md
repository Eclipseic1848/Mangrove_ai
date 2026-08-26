# CV-10 Gate A 生产迁移报告

> 状态：`GATE_A_COMPLETED`
>
> 执行日期：2026-08-25
>
> 范围：生产 CandidateVerification `0001/0002` 迁移与服务恢复；不含 Provider 重验和
> Delivery 发布

## 1. 授权与执行身份

- 用户明确回复“同意 Gate A”。
- 分支：`main`。
- `HEAD` 与 `origin/main`：
  `7efaf2fd78a8f1df0b86929b19e27cf0a7b5ca03`。
- 远端：`Eclipseic1848/Mangrove_ai`。
- 迁移前无活动普通用户任务、Semantic Harness、CandidateVerification Attempt 或模型 Grant。
- 目标 CandidateSet 仍为
  `2539e5676ba7ae5963d2dc43acc92cb1672a87477f8f07e283bc0e4dfa98a087`。

## 2. 停服与静止性

- 迁移前 8088 仍由旧进程提供服务；使用项目路径验证的停止脚本关闭后端监督进程树。
- 停服后 8088 无监听、无项目后端监督进程；生产库连续 6 秒的大小、修改时间和 SHA-256
  完全一致，且没有 WAL/SHM 文件。
- 已验证的脚本缺口：`scripts/stop_dev_processes.ps1` 即使传入 8088 端口限定，仍会枚举并清理
  所有带项目标记的进程，因而连带停止 5173。该问题没有影响数据；迁移完成后 5173 已恢复。
  本工单不顺带修改停止脚本。

## 3. 恢复点

- 唯一恢复点：`data/backups/webui-before-cv10-20260825-010051.db`。
- SHA-256：
  `09838edfad1826b876821e7857993aa8b858cf18f98335d1815bd535ce6342d1`。
- 恢复点 `integrity_check=ok`，外键违规为 0。
- `0001` 和 `0002` 两条生产迁移记录均冻结上述同一个恢复点哈希。
- 没有覆盖或改写既有备份。

## 4. 正式迁移结果

- 迁移前原业务数据：71 张表、10,313 行。
- 迁移后原 71 张表逐表行数不变。
- 迁移前后原表逻辑指纹均为：
  `aa2f6ddc0856db4a765ac4443a3bfd835942aa3da045c513848ca63cbcdfe602`。
- `0001_candidate_verification_attempts` 与
  `0002_delivery_publication_idempotency` 均已登记。
- 35 条 legacy VerificationReport 已导入为不可变 Attempt：
  - 27 `passed`；
  - 4 `failed`；
  - 4 `inconclusive`。
- 目标旧失败已导入为：
  - Attempt：
    `legacy_384796d3b628c58d89108c8a4eab586b752bd845b70f2ccd33a2f34e1f260086`；
  - `reason_code=initial`；
  - `ruleset_identity_status=legacy_unversioned`；
  - `status=failed`；
  - report SHA-256：
    `f5b3a57765eb03dd7764e460775ba7f89f8dcd3c55d7bbc18f40a6244557ad47`。
- CandidateVerification 的表、索引和失败关闭 Trigger 完整；CV-07 发布幂等索引被显式接管，
  非空发布幂等记录仍为 0。
- 对正式生产库重放迁移返回同一恢复点，没有创建第二备份或重复导入。
- 迁移后 `integrity_check=ok`，外键违规为 0。

## 5. 服务恢复

- 8088 以当前工作树和项目 Python 3.13 冷启动；新后端进程启动于迁移之后。
- 首次健康探测发生在完整依赖加载完成前，尚无 8088 监听；监督会话随后明确记录
  `Application startup complete`。第二次正式健康检查通过：API、同源前端和局域网监听均就绪。
- 5173 已重新启动，HTTP 200。
- 重启后再次核对：
  - 生产库完整性 `ok`、外键违规 0；
  - 35 条 Attempt 与两条迁移记录仍完整；
  - 目标保持 `candidate_ready`，CandidateSet、连接版本和模型未漂移；
  - 活动 Attempt/Grant 为 0；
  - 目标 Delivery 和发布意图均为 0；
  - rollout 仍为 `vnext_default`、`p0_blocked=false`，活动 GateSnapshot 未漂移。

## 6. 浏览器边界

- 使用 `agent-browser 0.34.0` 只读打开 8088，页面标题、登录页和前端资源加载正常。
- 隔离自动化会话没有复用用户 Chrome 中的 `liyi111` 登录状态，因此没有伪造登录或读取凭证，
  也没有把匿名页面探测冒充 Owner 工作台验收；会话已全部关闭。

## 7. 结论与下一门

Gate A 已达到 `GATE_A_COMPLETED`：生产恢复点、显式迁移、数据零改写、重放与服务恢复均有
现场证据。没有调用 Provider、创建真实重验 Attempt、产生本轮模型费用或发布 Delivery。

Gate A 后的生产装配只读 Offer 发现目标唯一 blocker 为 `legacy_unversioned`，与 ADR-0033 的
失败关闭决定一致；因此当前不能直接进入 Gate B。诊断与待确认业务选择见
`docs/plans/2026-08-25-cv-10-legacy-unversioned-diagnosis.md`。
