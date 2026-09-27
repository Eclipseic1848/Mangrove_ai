"""宿主冻结初稿；不读取可变文件充当用户已接受的结果。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory

from filelock import FileLock

from .candidate_qa import inspect_candidates, verify_frozen_copies


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
                 run_id: str, formats: tuple[str, ...], expected_sha256_by_format=None) -> dict:
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
    verify_frozen_copies(candidates, expected_sha256_by_format)
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


def draft_table_issues(root: Path, draft: dict, contracts) -> list[str]:
    """对已校验身份的冻结初稿提示结构差异；提示不取代用户的接受决定。"""
    from .candidate_verifier import _read_table_columns
    from src.delivery_publishing.models import TableOutputContract

    from .output_requirements import read_output_requirements, output_requirement_issues
    requirements = read_output_requirements(root, **{key: draft[key] for key in ("owner_id", "task_id", "revision", "run_id")})
    issues = output_requirement_issues(root / "drafts" / draft["draft_id"], requirements)
    for value in contracts:
        contract = TableOutputContract.model_validate(value)
        files = [item for item in draft["files"] if item["format"] == contract.format]
        try:
            if len(files) != 1:
                raise ValueError("文件数量不匹配")
            path = root / "drafts" / draft["draft_id"] / files[0]["filename"]
            if _read_table_columns(path, contract) == contract.exact_columns:
                continue
        except Exception:
            # 不把解析异常和宿主路径展示给用户；无法核对也不能声称符合要求。
            pass
        columns = "、".join(contract.exact_columns)[:500]
        issues.append(f"{contract.format.upper()} 初稿未通过列名、顺序或表格结构检查；要求列为：{columns}。可查看初稿后决定接受或继续核对。")
    return issues


def copy_source_draft(root, request, source_names, run_id):
    """确定性的原样输出复用重开、哈希和初稿冻结门，不签发模型权限。"""
    expected = request.expected_sha256_by_format
    if set(expected) != set(request.requested_output_formats):
        raise ValueError("原样输出必须覆盖完整格式合同")
    for fmt, digest in expected.items():
        matches = [root / "input" / name for source, name in zip(request.sources, source_names, strict=True)
                   if source.sha256 == digest and Path(source.original_name).suffix.lower() == "." + fmt]
        if not matches or any(path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != digest for path in matches):
            raise ValueError("原样输出来源与冻结身份不一致")
        shutil.copyfile(matches[0], root / "output" / ("data." + fmt))
    draft = freeze_draft(root, owner_id=request.user_id, task_id=request.task_id, revision=request.revision,
                         run_id=run_id, formats=request.requested_output_formats, expected_sha256_by_format=expected)
    marker = {"draft_id": draft["draft_id"], "owner_id": request.user_id, "task_id": request.task_id,
              "revision": request.revision, "run_id": run_id, "expected_sha256_by_format": expected}
    # 宿主恢复资格不能挂载给模型，否则模型能重建已消费的检查点。
    private = root / "host-state"
    if private.is_symlink():
        raise ValueError("宿主状态目录无效")
    private.mkdir(exist_ok=True)
    temporary = private / "host-copy-pending.tmp"
    temporary.write_text(json.dumps(marker, ensure_ascii=False), encoding="utf-8")
    temporary.replace(private / "host-copy-pending.json")
    return draft


def host_copy_pending(root, *, owner_id, task_id, revision, run_id):
    """只有尚未启动过模型的宿主复制步骤可以没有 Pi 会话；丢失会话仍拒绝。"""
    private = root / "host-state"
    if private.is_symlink():
        raise ValueError("宿主状态目录无效")
    if (private / "host-copy-consumed.json").exists():
        return False
    path = private / "host-copy-pending.json"
    if not path.exists():
        return False
    if path.is_symlink() or path.stat().st_size > 65536:
        raise ValueError("宿主复制检查点无效")
    marker = json.loads(path.read_text(encoding="utf-8"))
    identity = {"owner_id": owner_id, "task_id": task_id, "revision": revision, "run_id": run_id}
    if any(marker.get(key) != value for key, value in identity.items()):
        raise ValueError("宿主复制检查点与当前任务不一致")
    draft = read_draft(root, marker["draft_id"], **identity)
    if {item["format"]: item["sha256"] for item in draft["files"]} != marker.get("expected_sha256_by_format"):
        raise ValueError("宿主复制检查点与冻结初稿不一致")
    return True
