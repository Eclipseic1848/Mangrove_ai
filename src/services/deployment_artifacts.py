# -*- coding: utf-8 -*-
"""候选源码与前端产物的离线清单及旧资源保留。"""
from __future__ import annotations

import hashlib
import json
import os
import argparse
from pathlib import Path, PurePosixPath
import re
from typing import Any, Iterable


class DeploymentArtifactError(ValueError):
    """候选内容或清单不满足激活前的完整性边界。"""


def _relative(value: str) -> str:
    if (
        not isinstance(value, str) or not value or "\\" in value or ":" in value
        or any(ord(char) < 32 for char in value)
        or PurePosixPath(value).is_absolute()
        or any(part in {"", ".", ".."} for part in value.split("/"))
    ):
        raise DeploymentArtifactError("清单路径必须是规范的根内相对路径")
    return value


def _root(root: Path) -> Path:
    resolved = Path(root).resolve(strict=True)
    if not resolved.is_dir():
        raise DeploymentArtifactError("清单根必须是目录")
    return resolved


def _record(root: Path, relative: str) -> dict[str, Any]:
    path = root / _relative(relative)
    for parent in path.parents:
        if parent == root:
            break
        if parent.is_symlink() or parent.is_junction():
            raise DeploymentArtifactError(f"不支持目录链接：{relative}")
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise DeploymentArtifactError(f"文件越界或不是普通文件：{relative}")
    digest = hashlib.sha256()
    size = 0
    with resolved.open("rb") as stream:
        while block := stream.read(64 * 1024):
            digest.update(block)
            size += len(block)
    result: dict[str, Any] = {"type": "file", "size_bytes": size, "sha256": digest.hexdigest()}
    if path.is_symlink():
        # 链接与普通文件不能共用身份；清单不泄漏宿主绝对路径。
        result.update({
            "type": "symlink", "target": resolved.relative_to(root).as_posix(),
            "link_sha256": hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest(),
        })
    return result


def _files(root: Path, relative: str = "") -> dict[str, dict[str, Any]]:
    directory = root / relative
    if not directory.exists() and not directory.is_symlink() and not directory.is_junction():
        return {}
    if directory.is_symlink() or directory.is_junction():
        raise DeploymentArtifactError(f"不支持目录链接：{relative}")
    result: dict[str, dict[str, Any]] = {}
    for entry in sorted(directory.iterdir()):
        name = entry.relative_to(root).as_posix()
        _relative(name)
        if entry.is_symlink() or entry.is_junction():
            result[name] = _record(root, name)
        elif entry.is_dir():
            result.update(_files(root, name))
        else:
            result[name] = _record(root, name)
    return result


def _digest(files: dict[str, Any]) -> str:
    encoded = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _manifest(kind: str, files: dict[str, Any], **metadata: Any) -> dict[str, Any]:
    return {"schema_version": 1, "kind": kind, "files": files, "content_sha256": _digest(files), **metadata}


def _manifest_files(manifest: dict[str, Any]) -> dict[str, Any]:
    if (
        not isinstance(manifest, dict) or manifest.get("schema_version") != 1
        or manifest.get("kind") not in ("source", "frontend-native", "frontend-served")
        or not isinstance(manifest.get("files"), dict)
    ):
        raise DeploymentArtifactError("清单格式不受支持")
    files = manifest["files"]
    for relative, record in files.items():
        _relative(relative)
        if not isinstance(record, dict):
            raise DeploymentArtifactError("文件记录格式不受支持")
    if manifest.get("content_sha256") != _digest(files):
        raise DeploymentArtifactError("清单内容摘要不匹配")
    return files


def frontend_manifest(dist: Path) -> dict[str, Any]:
    """在保留旧资源之前记录原生构建，不把服务并集冒充新构建。"""
    return _manifest("frontend-native", _files(_root(dist)))


def _source_path(relative: str) -> str:
    relative = _relative(relative)
    if relative in {".env.example", "docker/rsshub/.env.example", "data/lessons/README.md", "data/templates/README.md", "memory/README.md"}:
        return relative
    parts = PurePosixPath(relative).parts
    if (
        any(part in {".git", ".venv", "venv", "node_modules", "__pycache__"} for part in parts)
        or parts[0] in {"data", "memory", "downloads", "logs", "backups", ".artifacts"}
        or relative.startswith("frontend/dist/")
        or any(part == ".env" or part.startswith(".env.") for part in parts)
        or PurePosixPath(relative).suffix.lower() in {".key", ".pem", ".p12", ".pfx", ".db", ".sqlite", ".sqlite3"}
    ):
        # 调用者给出的 tracked 集不能静默缩水，否则摘要会掩盖秘密或业务数据误入候选。
        raise DeploymentArtifactError(f"源码清单拒绝受管数据或本机配置：{relative}")
    return relative


def source_manifest(root: Path, tracked_paths: Iterable[str], *, commit_sha: str) -> dict[str, Any]:
    """只记录调用者明确提供的跟踪集；提交身份与 dirty 状态由部署入口另行核验。"""
    if not re.fullmatch(r"[0-9a-f]{40}", commit_sha):
        raise DeploymentArtifactError("源码清单需要完整 Git 提交 SHA")
    directory = _root(root)
    files: dict[str, Any] = {}
    for value in tracked_paths:
        relative = _source_path(value)
        if relative in files:
            raise DeploymentArtifactError(f"源码路径重复：{relative}")
        record = _record(directory, relative)
        if record["type"] == "symlink":
            _source_path(record["target"])
        files[relative] = record
    if not files:
        raise DeploymentArtifactError("源码跟踪路径集不能为空")
    return _manifest("source", files, commit_sha=commit_sha)


def verify_manifest(root: Path, manifest: dict[str, Any]) -> None:
    """源码核验精确输入集，前端核验整个目录，均校验类型与实际字节。"""
    directory = _root(root)
    files = _manifest_files(manifest)
    actual = (
        {relative: _record(directory, relative) for relative in files}
        if manifest["kind"] == "source" else _files(directory)
    )
    if actual != files:
        raise DeploymentArtifactError("实际文件与清单不一致")


def retain_frontend_assets(
    candidate_dist: Path,
    previous_dist: Path,
    *,
    native_manifest: dict[str, Any],
    previous_commit: str,
    previous_source_digest: str,
    previous_dirty: bool,
) -> dict[str, Any]:
    """先核验全部冲突，再保留旧 assets；旧首页永不进入新构建。"""
    candidate, previous = _root(candidate_dist), _root(previous_dist)
    if candidate == previous:
        raise DeploymentArtifactError("新旧构建必须分开")
    if (
        not re.fullmatch(r"[0-9a-f]{40}", previous_commit)
        or not re.fullmatch(r"[0-9a-f]{64}", previous_source_digest)
        or not isinstance(previous_dirty, bool)
    ):
        raise DeploymentArtifactError("旧版本来源必须包含提交、实际源码摘要及 dirty 状态")
    native = _manifest_files(native_manifest)
    if native_manifest["kind"] != "frontend-native":
        raise DeploymentArtifactError("必须提供保留前的原生构建清单")
    current = _files(candidate)
    old = _files(previous, "assets")
    for relative, record in native.items():
        if current.get(relative) != record:
            raise DeploymentArtifactError(f"原生构建已变化：{relative}")
    for relative, record in old.items():
        destination = candidate / relative
        if destination.is_dir() or any(parent.exists() and not parent.is_dir() for parent in destination.parents if parent != candidate and parent.is_relative_to(candidate)):
            raise DeploymentArtifactError(f"旧资源与新构建目录冲突：{relative}")
        present = current.get(relative)
        # 内容相同才可复用；类型不同仍由各自清单明确记录。
        if present and (present["sha256"], present["size_bytes"]) != (record["sha256"], record["size_bytes"]):
            raise DeploymentArtifactError(f"旧资源与新构建同名异内容：{relative}")
    if set(current) - set(native) - set(old):
        raise DeploymentArtifactError("候选包含原生构建与旧资源之外的文件")
    retained = sorted(set(old) - set(native))
    for relative in retained:
        if relative in current:
            continue
        destination = candidate / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        # 独占创建，拒绝覆盖并发写入；失败候选由激活前清单核验拦截。
        created = False
        try:
            with destination.open("xb") as target:
                created = True
                with (previous / relative).open("rb") as source:
                    while block := source.read(64 * 1024):
                        target.write(block)
            copied = _record(candidate, relative)
            if (copied["sha256"], copied["size_bytes"]) != (old[relative]["sha256"], old[relative]["size_bytes"]):
                raise DeploymentArtifactError(f"复制期间旧资源发生变化：{relative}")
        except BaseException:
            if created:
                destination.unlink(missing_ok=True)
            raise
    return _manifest(
        "frontend-served", _files(candidate), native_content_sha256=native_manifest["content_sha256"],
        retained_assets=retained, retained_from_previous={name: old[name] for name in retained},
        provenance={"previous_commit": previous_commit, "previous_source_digest": previous_source_digest, "previous_dirty": previous_dirty},
    )


def main() -> int:
    """仅消费显式路径，不探测 Git、不构建、不访问网络。"""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("source", "native", "verify"):
        child = commands.add_parser(command)
        child.add_argument("--root", type=Path, required=True)
        if command == "verify":
            child.add_argument("--manifest", type=Path, required=True)
        else:
            child.add_argument("--output", type=Path, required=True)
        if command == "source":
            child.add_argument("--tracked-paths", type=Path, required=True)
            child.add_argument("--commit", required=True)
    retain = commands.add_parser("retain")
    retain.add_argument("--candidate-dist", type=Path, required=True)
    retain.add_argument("--previous-dist", type=Path, required=True)
    retain.add_argument("--native-manifest", type=Path, required=True)
    retain.add_argument("--previous-commit", required=True)
    retain.add_argument("--previous-source-digest", required=True)
    retain.add_argument("--previous-dirty", choices=("true", "false"), required=True)
    retain.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "verify":
            verify_manifest(args.root, json.loads(args.manifest.read_text(encoding="utf-8")))
            result = {"ok": True}
        else:
            directory = args.candidate_dist if args.command == "retain" else args.root
            # 清单放在候选树外，避免生成结果改变刚记录的前端目录身份。
            if args.output.resolve().is_relative_to(_root(directory)):
                raise DeploymentArtifactError("输出清单必须位于候选根之外")
            if args.output.exists() or args.output.is_symlink():
                raise DeploymentArtifactError("输出清单已存在，禁止覆盖证据")
            if args.command == "source":
                raw = args.tracked_paths.read_bytes().decode("utf-8")
                if not raw.endswith("\0"):
                    raise DeploymentArtifactError("跟踪路径文件必须是 UTF-8 NUL 列表")
                result = source_manifest(args.root, raw[:-1].split("\0"), commit_sha=args.commit)
            elif args.command == "native":
                result = frontend_manifest(args.root)
            else:
                result = retain_frontend_assets(
                    args.candidate_dist, args.previous_dist,
                    native_manifest=json.loads(args.native_manifest.read_text(encoding="utf-8")),
                    previous_commit=args.previous_commit, previous_source_digest=args.previous_source_digest,
                    previous_dirty=args.previous_dirty == "true",
                )
            with args.output.open("x", encoding="utf-8", newline="\n") as output:
                output.write(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
            result = {"ok": True, "content_sha256": result["content_sha256"]}
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({"ok": False, "error_type": type(error).__name__, "detail": "产物核验未通过，禁止激活候选"}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
