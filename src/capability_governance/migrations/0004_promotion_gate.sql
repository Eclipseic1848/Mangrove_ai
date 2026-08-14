-- AC-07-05：同一 digest 至多一条晋级事件；数据库层保证并发晋级不被最后写入覆盖。
-- event_type 列由迁移入口在幂等检查后补充（SQLite 无 ADD COLUMN IF NOT EXISTS），
-- 因此本文件由迁移入口在补列之后单独执行，不参与基础 DDL 拼接。
CREATE UNIQUE INDEX IF NOT EXISTS idx_capability_governance_single_promotion
ON capability_governance_events(owner_key, pack_id, version, digest)
WHERE event_type='promoted_to_verified';
