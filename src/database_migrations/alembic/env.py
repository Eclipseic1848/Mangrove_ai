"""Alembic 运行环境；连接由显式迁移深模块注入。"""

from alembic import context


connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("数据库迁移必须通过 Mangrove 显式入口执行")

context.configure(connection=connection, target_metadata=None)
with context.begin_transaction():
    context.run_migrations()
