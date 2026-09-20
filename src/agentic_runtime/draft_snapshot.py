"""宿主冻结初稿；不读取可变文件充当用户已接受的结果。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory

from filelock import FileLock

from .candidate_qa import inspect_candidates


def _identity(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def read_draft(root: Path, draft_id: str, *, owner_id: str, task_id: str,
               revision: int, run_id: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", draft_id):
        raise ValueError("初稿身份无效")
    directory = root / "drafts" / draft_id
    if directory.is_symlink() or directory.parent.is_symlink():
        raise ValueError("初稿路径无效")
    metadata = directory / "_draft"
    if metadata.is_symlink() or metadata.stat().st_size > 1024 * 1024:
        raise ValueError("初稿元数据无效")
    payload = json.loads(metadata.read_text(encoding="utf-8"))
    if _identity(payload) != draft_id or any(payload.get(key) != value for key, value in {
        "owner_id": owner_id, "task_id": task_id, "revision": revision, "run_id": run_id,
    }.items()):
        raise ValueError("初稿身份或所属任务不匹配")
    for item in payload["files"]:
        name = item["filename"]
        path = directory / name
        if Path(name).name != name or path.is_symlink() or path.resolve().parent != directory.resolve():
            raise ValueError("初稿文件路径无效")
        if path.stat().st_size != item["size_bytes"] or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError("初稿内容已变化")
    return {"draft_id": draft_id, **payload}


def freeze_draft(root: Path, *, owner_id: str, task_id: str, revision: int,
                 run_id: str, formats: tuple[str, ...]) -> dict:
    output = root / "output"
    # 初稿复用既有重开 QA；数量与格式必须完整，不能暴露尚未写完的文件集。
    files = list(output.iterdir())
    if sum(p.stat().st_size for p in files if p.is_file()) > 128 * 1024 * 1024:
        raise ValueError("初稿超过有界预览大小")
    try:
        candidates = inspect_candidates(output, formats)
    except Exception as exc:
        # 第三方文档解析异常不统一；半成品只能推迟预览，不能中断主执行。
        raise ValueError("初稿文件尚未通过重开检查") from exc
    if sorted(item.format for item in candidates) != sorted(formats):
        raise ValueError("初稿文件集合尚未完整")
    payload = {"owner_id": owner_id, "task_id": task_id, "revision": revision, "run_id": run_id,
               "files": [item.model_dump(mode="json", exclude={"host_path"}) for item in candidates]}
    draft_id = _identity(payload)
    parent = root / "drafts"
    if parent.is_symlink():
        raise ValueError("初稿目录无效")
    parent.mkdir(exist_ok=True)
    with FileLock(str(parent / ".freeze.lock")):
        directory = parent / draft_id
        if not directory.exists():
            with TemporaryDirectory(prefix=".snapshot-", dir=parent) as temp:
                stage = Path(temp)
                for item in candidates:
                    target = stage / item.filename
                    shutil.copyfile(item.host_path, target)
                    if hashlib.sha256(target.read_bytes()).hexdigest() != item.sha256:
                        raise ValueError("初稿生成期间内容发生变化")
                (stage / "_draft").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                stage.rename(directory)
    return read_draft(root, draft_id, owner_id=owner_id, task_id=task_id, revision=revision, run_id=run_id)
