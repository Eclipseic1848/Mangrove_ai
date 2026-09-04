"""在开发服务启动前一次性检查全部数据库迁移状态。"""

from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config.settings import settings  # noqa: E402
from src.database_migrations import DatabaseTarget, inspect_database  # noqa: E402


def _database_path(value: str) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else PROJECT_ROOT / path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Mangrove 开发服务数据库迁移预检")
    parser.add_argument("--webui-database", default=settings.webui_db_path)
    parser.add_argument("--scheduler-database", default=settings.scheduler_db_path)
    return parser


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    targets = (
        DatabaseTarget("webui", _database_path(args.webui_database)),
        DatabaseTarget("scheduler", _database_path(args.scheduler_database)),
    )
    outdated = [status for target in targets if (status := inspect_database(target)).state != "current"]
    if not outdated:
        print("[数据库预检通过] WebUI 与 Scheduler Schema 均为当前版本。")
        return 0

    print("[数据库迁移预检失败] 以下数据库必须先完成显式迁移：")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for status in outdated:
        database = status.target.path.resolve()
        backup = database.parent / "backups" / f"{database.stem}-before-startup-{timestamp}.db"
        source_guard = (
            f" --expected-source-sha256 {_file_sha256(database)}"
            if database.is_file()
            else ""
        )
        print(
            f"- {status.target.profile}: {status.state} -> {status.target_revision}\n"
            "  python -m src.database_migrations apply "
            f'--profile {status.target.profile} --database "{database}" --backup "{backup}"'
            f"{source_guard}"
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
