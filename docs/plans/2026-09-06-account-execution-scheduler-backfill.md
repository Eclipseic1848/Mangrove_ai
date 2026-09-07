# 账号执行授权：历史调度绑定回填

此命令是明确升级维护操作，不由 Repository 或服务启动调用。不代表真实库迁移已执行。

## 前置证据

必须保存 #116 升级前的 WebUI `webui_0011` 与 scheduler `scheduler_0001` 配对快照。制作快照时需已停止调度器、确认实际工作者静默，并保存对应维护证据。仅停止等待或发现内存集合为空不构成静默证明。

快照、清单必须在同一证据目录。外部核验并冻结清单 SHA-256；命令不会从当前缺绑定行生成清单。没有预升级证据时拒绝回填，不以 `created_at` 推断历史资格。

清单结构：

```json
{
  "version": 1,
  "captured_at": "2026-09-05T00:00:00Z",
  "quiescent": true,
  "quiescence_evidence": "维护门证据编号",
  "webui_snapshot": "webui-before-upgrade.db",
  "webui_sha256": "预升级WebUI快照SHA256",
  "scheduler_snapshot": "scheduler-before-upgrade.db",
  "scheduler_sha256": "预升级Scheduler快照SHA256",
  "tasks": [
    {"task_id": "历史计划编号", "owner_user_id": "历史Owner编号", "row_sha256": "冻结整行SHA256"}
  ]
}
```

`row_sha256` 对旧快照 `SELECT * FROM scheduled_tasks WHERE task_id=?` 的整行字典计算。规范编码为 `json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")`，再取 SHA-256。清单只记录摘要和身份编号，不包含任务正文；仍须按本地维护证据权限保护，不提交版本库。

## 只读计划与明确应用

先迁移当前库 Schema；保留原始快照不动。当前两库须进入维护静默门并冻结完整主文件，存在非空 WAL 时命令拒绝；本命令不替操作者停止服务或执行 checkpoint。

```text
python -m src.database_migrations backfill-schedules --webui <当前WebUI库> --scheduler <当前Scheduler库> --manifest <冻结清单> --expected-webui-sha256 <当前WebUI摘要> --expected-scheduler-sha256 <当前Scheduler摘要> --expected-manifest-sha256 <已核验清单摘要> --backup-dir <新备份目录>
```

默认只读，返回 `planned` 和合格数量。人工审查输入与计划后，在完全相同参数末尾增加 `--apply` 才会写库。真实库执行仍需单独授权。

正常未变代、账号可用且清单证明静默的历史 active/paused 计划：绑定为 generation=0、idle，保留计划原状态。已变代或不可用账号：绑定仍为旧 generation=0、paused，原 active 计划暂停。重新启用不会自动继续旧任务；必须通过产品显式恢复。已存在绑定不覆盖；清单外的新版孤儿计划不补绑，继续失败关闭。

## 备份、重放与失败

新备份目录保留 `webui.before.db`、`scheduler.before.db` 与 `receipt.json`，均不覆盖。收据只记录摘要、数量和结果，不含任务正文、Owner 明文或宿主路径。成功后源事实未改变且备份完整时，重放返回 `already_applied`，不重复写库。

锁序为 WebUI→scheduler；先提交计划暂停，再提交绑定，不声称跨库原子提交。若后者失败，前者可能已暂停，但仍无合法绑定，不会自动执行。使用原冻结清单、重新核验当前两库摘要，并指定新的备份目录显式重试；不得覆盖失败收据或改用当前孤儿反推清单。任何正文、Owner 或其它冻结字段变化均拒绝；只容许本操作的旧代暂停差异用于半提交恢复。

验证只使用临时显式迁移库：`tests/test_account_execution_scheduler_backfill.py`。Scheduler 启动和业务 add 均没有回填路径。
