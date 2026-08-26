# CV-10 `legacy_unversioned` 资格阻断诊断

> 状态：`DIAGNOSIS_COMPLETE_DECISION_REQUIRED`
>
> 日期：2026-08-25
>
> 范围：只读资格诊断；未调用 Provider、未创建新 Attempt、未发布 Delivery、未修改资格代码

## 1. 精确症状

CV-10 Gate A 正式迁移成功后，生产装配的只读 Offer 对目标
`liyi111 / workspace_8363695f133645ac / revision 1` 返回：

```text
eligible=false
reason=null
blockers=[legacy_unversioned]
previous_status=failed
ruleset_changed=null
candidate_count=2
candidate_formats=[csv,json]
requires_provider=true
```

因此当前产品门不会创建 Gate B 的真实重验 Attempt，也没有发生 Provider 外发。

## 2. 反馈环与复现

### 生产只读反馈环

使用生产装配的 `SemanticWorkspaceManager.inspect_candidate_reverification` 读取精确 Owner/Task/
revision，稳定返回上述结果。该调用不写库、不创建 Grant、不调用 Provider。

### 回归反馈环

命令：

```powershell
E:\python3.13\python.exe -m pytest `
  tests/test_candidate_reverification_offer.py::test_legacy_unversioned_attempt_is_not_automatically_eligible -q
```

连续两次结果均为通过：`1 passed`（26.21s、7.96s）。这证明当前行为受专门回归测试保护，
不是迁移偶发错误。

## 3. 已验证事实

1. 目标旧报告已按原字节导入为唯一 legacy Attempt，CandidateSet、报告哈希、连接版本和模型
   均匹配；迁移没有丢失或错绑身份。
2. 当前 Ruleset 可解析并有完整版本身份；Offer 唯一 blocker 是 `legacy_unversioned`，不是 P0、
   Candidate/来源漂移、Delivery、活动 Attempt、Grant、连接或 Ruleset 不可用。
3. ADR-0033 和已批准规格明确规定：RuntimeAssignment/GateSnapshot 只能证明分配时规则候选，
   不能证明旧实际执行进程；缺少不可变 Run 级执行凭据时必须保持 `legacy_unversioned`。
4. ADR 明确禁止用当前工作树、服务重启说明、管理员手填或口头声明补猜，并明确写明“若未来
   需要恢复，必须另立规格、证据模型和用户授权”。
5. 已批准规格记录：现有缺少 Run 级身份的确定性 failed Candidate 不能凭本任务自动重验，
   且该失败关闭后果已由用户于 2026-08-24 确认。
6. 同一任务拆分又要求 CV-10 对一条受影响生产 Candidate 执行真实重验；当前选定目标正是上述
   `legacy_unversioned failed`，所以任务的真实验收目标与已批准资格边界不可同时满足。

## 4. 排名后的原因判断

1. **已确认：规格/验收目标可达性矛盾。** 安全实现、迁移、测试与 ADR 相互一致；CV-10
   选择的生产目标不满足该 ADR 的可重验前提。
2. **已排除：迁移导入错误。** 目标身份、状态、哈希和连接均正确，35 条分布与迁移前一致。
3. **已排除：当前 Ruleset 或运行环境不可证明。** 当前 Ruleset 四项身份解析成功；Offer 没有
   `ruleset_unavailable`。
4. **已排除：其他生产门阻断。** P0、文件/来源漂移、Delivery、活动 Attempt/Grant 都不是
   blocker。

## 5. 必须由用户确认的业务选择

### A. 新增正式的 legacy 再基线验证能力（推荐）

另立规格和 ADR 替代关系，引入新的业务原因（建议命名 `legacy_rebaseline`）：

- 明确承认旧实际 Ruleset **未知**，不伪装成 `ruleset_changed`；
- Owner 对“旧规则不可比较”、精确 CandidateSet、当前 Ruleset、Provider 外发和潜在费用单独
  确认；
- 对同一 Run/CandidateSet 执行一次当前完整验证，创建新的 versioned Attempt，并引用旧
  `legacy_unversioned` Attempt；
- 保留旧失败，不重跑 Pi、不生成 Candidate、不创建 revision、不自动发布；
- `outcome_unknown` 与 Gate C 继续按现有失败关闭和独立授权执行；
- 这是正式可复用能力，不使用一次性脚本、人工改库或管理员后门。

代价：改变已批准的资格语义，需新增规格、ADR、TDD、双轴审查和垂直工单，CV-10 不能直接
把它当作既有 Gate B。

### B. 保持 ADR-0033，不重验当前目标

维持现有安全边界，CV-10 改用一个已有 versioned Attempt 且符合规则变化/语义不确定条件的
Candidate。当前生产库没有这样的现成受影响目标，因此只能等待未来自然产生，或另行授权新
任务/新 Candidate；后者不能证明当前旧 Candidate 的恢复能力。

### C. 设计外部执行身份证据导入

只有用户能提供不可变、可校验且与旧 Run/commit/source hash 一致的执行时凭据时才可能成立。
当前现场没有这种权威证据；普通日志、GateSnapshot、进程时间和口头说明均被 ADR 明确排除，
因此当前不推荐。

## 6. 当前结论

Gate A 保持完成且无需回滚。CV-10 当前不能进入 Gate B；下一阶段必须先由用户选择是否修改
`legacy_unversioned` 的业务恢复语义。未确认前不得放宽 blocker、直接调用内部 Verifier、人工
插入 versioned Attempt 或以新 revision/新 Candidate 替代原目标。
