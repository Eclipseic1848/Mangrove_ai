"""单独固定两条旧 Chat 隔离，不扩充首次切换清单或改写原事实。"""
import json

from alembic import op

revision = "webui_0026"
down_revision = "webui_0025"
branch_labels = None
depends_on = None
operation_summary = ("初始化两条旧 Chat 的独立补充隔离权威及原绑定保护；保留首次隔离原件",)

_KEY = "deployment.supplemental_chat_quarantine"
_EMPTY = json.dumps({"version": 1, "targets": [], "records": [], "approval_reference": None,
                     "actor_id": None, "accepted_risk": None}, sort_keys=True, separators=(",", ":"))


def _protected(reference):
    return f"""EXISTS (SELECT 1 FROM app_settings s,json_each(s.value,'$.records') q
        WHERE s.key='{_KEY}' AND json_extract(q.value,'$.profile')='webui'
          AND json_extract(q.value,'$.table')='account_execution_bindings'
          AND json_extract(q.value,'$.key.owner_user_id')={reference}.owner_user_id
          AND json_extract(q.value,'$.key.resource_kind')={reference}.resource_kind
          AND json_extract(q.value,'$.key.resource_id')={reference}.resource_id)"""


def upgrade():
    connection = op.get_bind()
    if connection.exec_driver_sql("SELECT 1 FROM app_settings WHERE key=?", (_KEY,)).fetchone():
        raise RuntimeError("补充 Chat 隔离设置已经存在，须核对原件")
    connection.exec_driver_sql("INSERT INTO app_settings(key,value) VALUES (?,?)", (_KEY, _EMPTY))
    op.execute(f"""CREATE TRIGGER supplemental_chat_quarantine_no_delete BEFORE DELETE ON app_settings
        WHEN OLD.key='{_KEY}' BEGIN SELECT RAISE(ABORT,'persistent supplemental chat quarantine'); END""")
    # REPLACE 默认不触发旧行 DELETE；在 INSERT 前拒绝覆盖隔离权威。
    op.execute(f"""CREATE TRIGGER supplemental_chat_quarantine_no_replace BEFORE INSERT ON app_settings
        WHEN NEW.key='{_KEY}' AND EXISTS (SELECT 1 FROM app_settings WHERE key='{_KEY}')
        BEGIN SELECT RAISE(ABORT,'persistent supplemental chat quarantine'); END""")
    empty_literal = _EMPTY.replace("'", "''")
    connection.exec_driver_sql(f"""CREATE TRIGGER supplemental_chat_quarantine_no_reset BEFORE UPDATE ON app_settings
        WHEN (OLD.key='{_KEY}' AND (NEW.key IS NOT OLD.key OR OLD.value IS NOT '{empty_literal}'))
          OR (NEW.key='{_KEY}' AND OLD.key IS NOT '{_KEY}')
        BEGIN SELECT RAISE(ABORT,'persistent supplemental chat quarantine'); END""")
    for operation, references in (("INSERT", ("NEW",)), ("UPDATE", ("OLD", "NEW")), ("DELETE", ("OLD",))):
        # 原绑定主键是 Owner/类型/资源；NEW 与 OLD 同时核对，禁止换 Owner 或 REPLACE 重建旧身份。
        op.execute(f"""CREATE TRIGGER supplemental_chat_binding_no_{operation.lower()}
            BEFORE {operation} ON account_execution_bindings
            WHEN {" OR ".join(_protected(reference) for reference in references)}
            BEGIN SELECT RAISE(ABORT,'quarantined historical chat'); END""")


def downgrade():
    raise RuntimeError("补充 Chat 隔离不可移除；回退须使用识别两份隔离的兼容代码")
