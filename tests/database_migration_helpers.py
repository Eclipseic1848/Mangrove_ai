"""测试显式数据库迁移接缝；禁止 Repository 构造器代建 Schema。"""

from pathlib import Path
from uuid import uuid4

from src.database_migrations import (
    DatabaseTarget,
    apply_migrations,
    inspect_database,
)


def migrated_webui_database(path: str | Path) -> Path:
    return migrated_profile_database(path, profile="webui")


def migrated_profile_database(path: str | Path, *, profile: str) -> Path:
    database = Path(path)
    target = DatabaseTarget(profile=profile, path=database)
    if inspect_database(target).state != "current":
        backup = database.with_name(
            f"{database.name}.before-migration-{uuid4().hex}.db"
        )
        apply_migrations(target, backup)
    return database


def migrated_qualification_ledger_database(path: str | Path) -> Path:
    return migrated_profile_database(path, profile="qualification_ledger")
