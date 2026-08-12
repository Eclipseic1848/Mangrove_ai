-- AC-07 #35：供应链证据只追加并绑定精确 digest，不改写验证运行或治理投影。
CREATE TABLE IF NOT EXISTS capability_supply_chain_evidence (
    evidence_id TEXT NOT NULL PRIMARY KEY,
    owner_key TEXT NOT NULL,
    scope TEXT NOT NULL,
    pack_id TEXT NOT NULL,
    version TEXT NOT NULL,
    digest TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_capability_supply_chain_target
ON capability_supply_chain_evidence(owner_key, pack_id, version, digest, occurred_at);
