"""学习文件效果的持久回执：已应用不重放，内容漂移不覆盖。"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from src.database_migrations import DatabaseTarget, inspect_database
from src.memory._io import atomic_write
from src.memory._library_scope import entry_path, require_owner
from src.timezone import now

# 冻结上下文最多三条教训，各占一个稳定回执，不复用失败或生成回执。
LESSON_USE_KINDS = ("lesson_use_1", "lesson_use_2", "lesson_use_3")


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else None


class LearningReceipts:
    def __init__(self, database: str | Path, directory: Path):
        self.database = Path(database)
        self.directory = directory
        inspect_database(DatabaseTarget(profile="webui", path=self.database)).require_current()

    @staticmethod
    def _key(owner_id, task_id, revision, run_id, kind):
        require_owner(owner_id)
        if not task_id or not run_id or isinstance(revision, bool) or not isinstance(revision, int) or revision < 1:
            raise ValueError("学习必须绑定确切任务、版本和执行")
        if kind not in {"template_use", "template_new", "lesson_new", "lesson_failure", *LESSON_USE_KINDS}:
            raise ValueError("学习效果类型无效")
        return owner_id, task_id, revision, run_id, kind

    def get(self, *, owner_id, task_id, revision, run_id, kind):
        key = self._key(owner_id, task_id, revision, run_id, kind)
        with closing(sqlite3.connect(self.database)) as connection:
            connection.row_factory = sqlite3.Row
            row = connection.execute(
                "SELECT * FROM library_learning_receipts WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? AND kind=?",
                key,
            ).fetchone()
        return dict(row) if row else None

    def enqueue(self, *, owner_id, task_id, revision, run_id, kind):
        key = self._key(owner_id, task_id, revision, run_id, kind)
        timestamp = now().isoformat()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "INSERT OR IGNORE INTO library_learning_receipts VALUES (?,?,?,?,?,'',NULL,'','','queued',?,?)",
                (*key, timestamp, timestamp),
            )

    def skip(self, *, owner_id, task_id, revision, run_id, kind, generated=False):
        key = self._key(owner_id, task_id, revision, run_id, kind)
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute(
                "UPDATE library_learning_receipts SET state='skipped',updated_at=? "
                "WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? AND kind=? AND state=?",
                (now().isoformat(), *key, "generating" if generated else "queued"),
            )

    def claim_generation(self, *, owner_id, task_id, revision, run_id, kind):
        key = self._key(owner_id, task_id, revision, run_id, kind)
        if kind not in {"template_new", "lesson_new"}:
            raise ValueError("只有新经验需要调用模型生成")
        with closing(sqlite3.connect(self.database)) as connection, connection:
            return connection.execute(
                "UPDATE library_learning_receipts SET state='generating',updated_at=? "
                "WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? AND kind=? AND state='queued'",
                (now().isoformat(), *key),
            ).rowcount == 1

    def prepare(self, *, owner_id, task_id, revision, run_id, kind, slug, before, after, generated=False):
        key = self._key(owner_id, task_id, revision, run_id, kind)
        entry_path(self.directory, slug)
        if not isinstance(after, str) or not after or len(after.encode("utf-8")) > 65536:
            raise ValueError("学习内容为空或超过上限")
        expected = (slug, _sha(before), _sha(after), after)
        timestamp = now().isoformat()
        with closing(sqlite3.connect(self.database)) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT slug,before_sha256,after_sha256,after_content,state FROM library_learning_receipts "
                "WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? AND kind=?", key,
            ).fetchone()
            if row is not None:
                if row[4] == ("generating" if generated else "queued"):
                    connection.execute(
                        "UPDATE library_learning_receipts SET slug=?,before_sha256=?,after_sha256=?,after_content=?,"
                        "state='pending',updated_at=? WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? AND kind=?",
                        (*expected, timestamp, *key),
                    )
                elif tuple(row[:4]) != expected:
                    raise ValueError("同一学习回执不能绑定不同效果")
                return
            connection.execute(
                "INSERT INTO library_learning_receipts VALUES (?,?,?,?,?,?,?,?,?,'pending',?,?)",
                (*key, *expected, timestamp, timestamp),
            )

    def apply(self, *, owner_id, task_id, revision, run_id, kind, expected_verification_hash=None):
        key = self._key(owner_id, task_id, revision, run_id, kind)
        # 与现有库写入共用进程内锁；SQL 写锁串行化学习回执。
        # ponytail: 既有 Markdown 库限单服务写入，多实例部署需统一文件写锁。
        from src.memory.templates import _templates_lock
        from src.memory.lessons import _lessons_lock
        lock = _templates_lock if kind.startswith("template_") else _lessons_lock
        with lock, closing(sqlite3.connect(self.database)) as connection, connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM library_learning_receipts WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? AND kind=?", key,
            ).fetchone()
            if row is None:
                raise KeyError("学习回执不存在或无权读取")
            if row["state"] != "pending":
                return row["state"]
            if expected_verification_hash is not None:
                from src.delivery_publishing.models import canonical_hash
                runtime = connection.execute(
                    "SELECT verification_json FROM agentic_runtime_runs "
                    "WHERE user_id=? AND task_id=? AND revision=? AND run_id=?", key[:4],
                ).fetchone()
                # 与文件效果同一写事务核对，重验不能在核对后抢先替换报告。
                if (not runtime or not runtime[0]
                        or canonical_hash(json.loads(runtime[0])) != expected_verification_hash):
                    connection.execute(
                        "UPDATE library_learning_receipts SET state='conflict',updated_at=? "
                        "WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? AND kind=?",
                        (now().isoformat(), *key),
                    )
                    return "conflict"
            path = entry_path(self.directory, row["slug"])
            current = path.read_text(encoding="utf-8") if path.exists() else None
            digest = _sha(current)
            if digest == row["after_sha256"]:
                state = "applied"
            elif digest == row["before_sha256"]:
                from src.api.execution import execution_checkpoint
                execution_checkpoint()
                attempt = path.parent / f".learning-write-{_sha(str(key))}"
                if current is None and (attempt.exists() or attempt.is_symlink()):
                    # 写入中断后无法区分未落盘和已被删除，禁止恢复时重建原件。
                    state = "conflict"
                else:
                    if current is None:
                        atomic_write(attempt, row["after_sha256"])
                    atomic_write(path, row["after_content"])
                    state = "applied"
            else:
                state = "conflict"
            connection.execute(
                "UPDATE library_learning_receipts SET state=?,updated_at=? "
                "WHERE owner_id=? AND task_id=? AND revision=? AND run_id=? AND kind=?",
                (state, now().isoformat(), *key),
            )
            return state
