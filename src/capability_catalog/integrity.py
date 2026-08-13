# -*- coding: utf-8 -*-
"""物化能力目录的外置完整性记录。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat
import uuid


_DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}")


def _record_path(subject_root: Path) -> Path:
    return subject_root.with_name(f"{subject_root.name}.integrity.json")


def capability_integrity_record_exists(subject_root: str | Path) -> bool:
    """判断物化目录是否已有外置完整性记录。"""

    subject = Path(subject_root).resolve(strict=True)
    return _record_path(subject).is_file()


def capability_content_sha256(subject_root: str | Path) -> str:
    """计算与宿主绝对路径无关的稳定目录摘要。"""

    subject = Path(subject_root).resolve(strict=True)
    if not subject.is_dir():
        raise RuntimeError("能力物化主体不是目录")
    digest = hashlib.sha256()
    digest.update(b"R")
    digest.update(stat.S_IMODE(subject.stat().st_mode).to_bytes(4, "big"))
    paths = sorted(
        subject.rglob("*"),
        key=lambda item: item.relative_to(subject).as_posix(),
    )
    for path in paths:
        if path.is_symlink() or (
            hasattr(path, "is_junction") and path.is_junction()
        ):
            raise RuntimeError("能力物化目录不得包含链接或联接点")
        resolved = path.resolve(strict=True)
        if subject not in resolved.parents:
            raise RuntimeError("能力物化目录路径越界")
        relative = path.relative_to(subject).as_posix().encode("utf-8")
        if path.is_dir():
            mode = stat.S_IMODE(path.stat().st_mode)
            digest.update(b"D")
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(mode.to_bytes(4, "big"))
            continue
        if not path.is_file():
            raise RuntimeError("能力物化目录包含不支持的文件类型")
        before = path.stat()
        digest.update(b"F")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(stat.S_IMODE(before.st_mode).to_bytes(4, "big"))
        digest.update(before.st_size.to_bytes(16, "big"))
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        after = path.stat()
        if (
            before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or stat.S_IMODE(before.st_mode) != stat.S_IMODE(after.st_mode)
        ):
            raise RuntimeError("能力物化目录在完整性校验期间发生变化")
    return digest.hexdigest()


def write_capability_integrity(
    subject_root: str | Path,
    subject_digest: str,
) -> str:
    """在主体目录外原子写入物化时的内容摘要。"""

    if _DIGEST_PATTERN.fullmatch(subject_digest) is None:
        raise ValueError("能力物化主体必须使用冻结 OCI digest")
    subject = Path(subject_root).resolve(strict=True)
    content_sha256 = capability_content_sha256(subject)
    record = _record_path(subject)
    temporary = record.with_name(f"{record.name}.tmp-{uuid.uuid4().hex[:12]}")
    try:
        temporary.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "subject_digest": subject_digest,
                    "content_sha256": content_sha256,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        temporary.replace(record)
    finally:
        temporary.unlink(missing_ok=True)
    return content_sha256


def verify_capability_integrity(
    subject_root: str | Path,
    subject_digest: str,
) -> str:
    """核验外置记录与当前主体内容，任何不可判定状态都失败关闭。"""

    subject = Path(subject_root).resolve(strict=True)
    record_path = _record_path(subject)
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError("能力物化完整性记录不可用") from error
    if (
        not isinstance(record, dict)
        or record.get("schema_version") != 2
        or record.get("subject_digest") != subject_digest
        or not isinstance(record.get("content_sha256"), str)
    ):
        raise RuntimeError("能力物化完整性记录与冻结 digest 不一致")
    actual = capability_content_sha256(subject)
    if actual != record["content_sha256"]:
        raise RuntimeError("能力物化目录完整性校验失败")
    return actual
