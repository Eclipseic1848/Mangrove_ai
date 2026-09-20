"""模型配置测试凭据与不可变替代版本，不改旧任务和旧密钥。"""
from alembic import op

revision = "webui_0019"
down_revision = "webui_0018"
branch_labels = None
depends_on = None
operation_summary = ("新增模型配置测试记录与不可变替代版本",)


def upgrade():
    # 兼容已有受管结构的显式补登；最终完整 Schema 契约仍会拒绝形状漂移。
    op.execute("CREATE TABLE IF NOT EXISTS model_configuration_edits (actor_id TEXT NOT NULL, operation_id TEXT NOT NULL, connection_id TEXT NOT NULL, expected_version TEXT NOT NULL, request_hash TEXT NOT NULL, config_json TEXT NOT NULL, ciphertext TEXT, state TEXT NOT NULL, results_json TEXT NOT NULL DEFAULT '[]', replacement_id TEXT, created_at TEXT NOT NULL, PRIMARY KEY(actor_id, operation_id))")
    op.execute("CREATE TABLE IF NOT EXISTS model_configuration_versions (connection_id TEXT PRIMARY KEY REFERENCES model_connections(connection_id), previous_id TEXT NOT NULL UNIQUE REFERENCES model_connections(connection_id), thinking TEXT NOT NULL DEFAULT 'default', created_at TEXT NOT NULL)")


def downgrade():
    raise RuntimeError("配置版本及验证记录不得丢失，请从显式备份恢复")
