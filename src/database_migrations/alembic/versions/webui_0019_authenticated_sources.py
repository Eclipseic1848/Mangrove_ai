"""候选显式认证来源Schema；不得由Repository运行。"""
from alembic import op
revision='webui_0019'
down_revision='webui_0018'
branch_labels=None
depends_on=None
operation_summary=('新增Owner认证来源密文版本和精确绑定认证待办',)
SQL="CREATE TABLE authenticated_source_connections(owner TEXT NOT NULL REFERENCES users(user_id),website TEXT NOT NULL,account TEXT NOT NULL,active_version INTEGER NOT NULL CHECK(active_version>=0),ciphertext TEXT,usable INTEGER NOT NULL CHECK(usable IN (0,1)),PRIMARY KEY(owner,website,account),CHECK((active_version=0 AND ciphertext IS NULL AND usable=0) OR (active_version>0 AND ciphertext IS NOT NULL)));\nCREATE TABLE source_reauthentication_requests(request_id TEXT NOT NULL PRIMARY KEY,idempotency_key TEXT NOT NULL,request_hash TEXT NOT NULL,owner TEXT NOT NULL REFERENCES users(user_id),website TEXT NOT NULL,account TEXT,expected_version INTEGER NOT NULL CHECK(expected_version>=0),binding_json TEXT NOT NULL CHECK(json_valid(binding_json)),driver_version TEXT NOT NULL,authorization_digest TEXT NOT NULL,state TEXT NOT NULL CHECK(state IN ('pending','verified','claimed','cancelled','expired','stale','account_conflict')),expires REAL NOT NULL CHECK(expires>=0),UNIQUE(owner,idempotency_key),FOREIGN KEY(owner,website,account) REFERENCES authenticated_source_connections(owner,website,account));\n"

SQL += "CREATE TABLE authenticated_source_commands (\n owner TEXT NOT NULL REFERENCES users(user_id),\n request_id TEXT NOT NULL REFERENCES source_reauthentication_requests(request_id),\n action TEXT NOT NULL CHECK(action IN ('start','verify','resume','refresh')),\n idempotency_key TEXT NOT NULL,\n fingerprint TEXT NOT NULL,\n state TEXT NOT NULL CHECK(state IN ('claimed','completed')),\n receipt_json TEXT CHECK(receipt_json IS NULL OR json_valid(receipt_json)),\n PRIMARY KEY(owner,action,idempotency_key)\n);\n"

def upgrade():
    conn=op.get_bind()
    columns=conn.exec_driver_sql('PRAGMA table_info(source_acquisition_attempts)').fetchall()
    selected=[row for row in columns if row[1]=='current_auth_request_id']
    if not selected:
        conn.exec_driver_sql('ALTER TABLE source_acquisition_attempts ADD COLUMN current_auth_request_id TEXT')
    elif str(selected[0][2]).upper()!='TEXT' or selected[0][3] or selected[0][4] is not None:
        raise RuntimeError('current_auth_request_id 已有Schema不兼容')
    for statement in SQL.strip().split(';'):
        if not statement.strip(): continue
        name=statement.split('(')[0].split()[-1]
        existing=conn.exec_driver_sql("SELECT sql FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone()
        if existing:
            # 对完整建表合同校验，不能只核列而遗漏CHECK/FK/唯一约束。
            if ' '.join(existing[0].split())!=' '.join(statement.strip().split()):
                raise RuntimeError(name+' 已有Schema不兼容')
        else:
            conn.exec_driver_sql(statement)

def downgrade():
    raise RuntimeError('认证版本与领取事实必须保留，请经显式备份恢复')
