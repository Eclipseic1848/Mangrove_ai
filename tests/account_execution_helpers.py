"""测试夹具显式创建虚构账号；不代建执行绑定或改变产品授权。"""
from contextlib import closing
import sqlite3

from src.account_execution import capture_authorization


def seed_execution_owner(database, owner_id='owner-a'):
    with closing(sqlite3.connect(database)) as connection:
        connection.execute("INSERT OR IGNORE INTO users(user_id,username,password_hash,created_at,role,disabled,pending) VALUES (?,?,'synthetic-hash','2026-01-01','user',0,0)", (owner_id, owner_id))
        connection.commit()
        return capture_authorization(connection, owner_id)
