# -*- coding: utf-8 -*-
"""供 Pi 在沙箱内逐步登记候选文件和来源证据的轻量 CLI。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import tempfile


class ManifestToolError(ValueError):
    """候选清单操作违反最小安全或完整性约束。"""


def _safe_candidate_name(value: str) -> str:
    name = Path(value).name
    if not name or name != value or name == "candidate-manifest.json":
        raise ManifestToolError("候选文件名必须是不含路径的普通文件名")
    return name


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=".candidate-manifest-",
        suffix=".tmp",
        delete=False,
    ) as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        temporary = Path(handle.name)
    temporary.replace(path)


def initialize_manifest(
    *,
    output_dir: Path,
    filename: str,
    output_format: str,
    description: str,
) -> None:
    """登记一个已生成的候选；不允许清单指向输出目录外的文件。"""

    safe_name = _safe_candidate_name(filename)
    if not (output_dir / safe_name).is_file():
        raise ManifestToolError(f"候选文件不存在：{safe_name}")
    manifest_path = output_dir / "candidate-manifest.json"
    if manifest_path.is_file():
        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ManifestToolError("现有候选清单不是有效 JSON") from exc
    else:
        payload = {
            "version": 2,
            "artifacts": [],
            "result_items": [],
            "qualified_omissions": [],
            "result_search_complete": False,
        }
    artifact = {
        "filename": safe_name,
        "format": output_format.lower().lstrip("."),
        "description": description,
        "evidence": [],
    }
    artifacts = payload.setdefault("artifacts", [])
    artifacts[:] = [
        item for item in artifacts if item.get("filename") != safe_name
    ]
    artifacts.append(artifact)
    _write_atomic(manifest_path, payload)


def add_evidence(
    *,
    output_dir: Path,
    filename: str,
    source: str,
    locator: str,
    quote: str,
) -> None:
    """给已登记候选增加一条短证据，严格 JSON 由工具统一生成。"""

    safe_name = _safe_candidate_name(filename)
    manifest_path = output_dir / "candidate-manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ManifestToolError("请先使用 init 初始化候选清单") from exc
    artifacts = payload.get("artifacts")
    artifact = next(
        (
            item
            for item in artifacts or []
            if item.get("filename") == safe_name
        ),
        None,
    )
    if artifact is None:
        raise ManifestToolError(f"清单中没有候选：{safe_name}")
    if not source.strip() or not locator.strip() or not quote.strip():
        raise ManifestToolError("source、locator、quote 均不能为空")
    artifact["evidence"].append(
        {
            "source": source,
            "locator": locator,
            "quote": quote,
        }
    )
    _write_atomic(manifest_path, payload)


def remove_evidence(
    *,
    output_dir: Path,
    filename: str,
    locator: str,
) -> None:
    """按定位符删除验证失败的证据，不要求模型重写整份 JSON。"""

    safe_name = _safe_candidate_name(filename)
    manifest_path = output_dir / "candidate-manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ManifestToolError("候选清单不存在或不是有效 JSON") from exc
    artifact = next(
        (
            item
            for item in payload.get("artifacts") or []
            if item.get("filename") == safe_name
        ),
        None,
    )
    if artifact is None:
        raise ManifestToolError(f"清单中没有候选：{safe_name}")
    before = len(artifact.get("evidence") or [])
    artifact["evidence"] = [
        item
        for item in artifact.get("evidence") or []
        if item.get("locator") != locator
    ]
    if len(artifact["evidence"]) == before:
        raise ManifestToolError(f"没有命中定位符：{locator}")
    _write_atomic(manifest_path, payload)


def add_result_item(
    *,
    output_dir: Path,
    result_id: str,
    label: str,
    source: str,
    locator: str,
    quote: str,
) -> None:
    """登记一项业务结果及其 EvidenceRef 原料，避免文件级证据冒充逐项证据。"""

    manifest_path = output_dir / "candidate-manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ManifestToolError("请先使用 init 初始化候选清单") from exc
    values = (result_id, label, source, locator, quote)
    if any(not value.strip() for value in values):
        raise ManifestToolError(
            "result_id、label、source、locator、quote 均不能为空"
        )
    if len(result_id) > 200 or len(label) > 500:
        raise ManifestToolError("结果项身份或名称过长")
    item = {
        "result_id": result_id,
        "label": label,
        "evidence": [
            {
                "source": source,
                "locator": locator,
                "quote": quote,
            }
        ],
    }
    result_items = payload.setdefault("result_items", [])
    result_items[:] = [
        existing
        for existing in result_items
        if existing.get("result_id") != result_id
    ]
    result_items.append(item)
    payload.setdefault("qualified_omissions", [])[:] = [
        existing
        for existing in payload.get("qualified_omissions") or []
        if existing.get("result_id") != result_id
    ]
    _write_atomic(manifest_path, payload)


def add_qualified_omission(
    *,
    output_dir: Path,
    result_id: str,
    label: str,
    source: str,
    locator: str,
    quote: str,
) -> None:
    """登记已确认合格但尚未进入候选的结果，供同 Run 修复门使用。"""

    manifest_path = output_dir / "candidate-manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ManifestToolError("请先使用 init 初始化候选清单") from exc
    values = (result_id, label, source, locator, quote)
    if any(not value.strip() for value in values):
        raise ManifestToolError(
            "result_id、label、source、locator、quote 均不能为空"
        )
    item = {
        "result_id": result_id,
        "label": label,
        "evidence": [
            {"source": source, "locator": locator, "quote": quote}
        ],
    }
    omissions = payload.setdefault("qualified_omissions", [])
    omissions[:] = [
        existing
        for existing in omissions
        if existing.get("result_id") != result_id
    ]
    omissions.append(item)
    payload.setdefault("result_items", [])[:] = [
        existing
        for existing in payload.get("result_items") or []
        if existing.get("result_id") != result_id
    ]
    _write_atomic(manifest_path, payload)


def mark_result_search_complete(*, output_dir: Path) -> None:
    """显式声明本 Run 已检查完获准范围中的业务结果候选。"""

    manifest_path = output_dir / "candidate-manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ManifestToolError("请先使用 init 初始化候选清单") from exc
    payload["result_search_complete"] = True
    _write_atomic(manifest_path, payload)


def _main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        default="/workspace/output",
        type=Path,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init")
    init_parser.add_argument("--filename", required=True)
    init_parser.add_argument("--format", required=True)
    init_parser.add_argument("--description", required=True)

    evidence_parser = subparsers.add_parser("add-evidence")
    evidence_parser.add_argument("--filename", required=True)
    evidence_parser.add_argument("--source", required=True)
    evidence_parser.add_argument("--locator", required=True)
    evidence_parser.add_argument("--quote", required=True)

    remove_parser = subparsers.add_parser("remove-evidence")
    remove_parser.add_argument("--filename", required=True)
    remove_parser.add_argument("--locator", required=True)

    result_parser = subparsers.add_parser("add-result")
    result_parser.add_argument("--result-id", required=True)
    result_parser.add_argument("--label", required=True)
    result_parser.add_argument("--source", required=True)
    result_parser.add_argument("--locator", required=True)
    result_parser.add_argument("--quote", required=True)

    omission_parser = subparsers.add_parser("add-omission")
    omission_parser.add_argument("--result-id", required=True)
    omission_parser.add_argument("--label", required=True)
    omission_parser.add_argument("--source", required=True)
    omission_parser.add_argument("--locator", required=True)
    omission_parser.add_argument("--quote", required=True)

    subparsers.add_parser("complete-results")

    args = parser.parse_args()
    if args.command == "init":
        initialize_manifest(
            output_dir=args.output_dir,
            filename=args.filename,
            output_format=args.format,
            description=args.description,
        )
    elif args.command == "add-evidence":
        add_evidence(
            output_dir=args.output_dir,
            filename=args.filename,
            source=args.source,
            locator=args.locator,
            quote=args.quote,
        )
    elif args.command == "remove-evidence":
        remove_evidence(
            output_dir=args.output_dir,
            filename=args.filename,
            locator=args.locator,
        )
    elif args.command == "add-result":
        add_result_item(
            output_dir=args.output_dir,
            result_id=args.result_id,
            label=args.label,
            source=args.source,
            locator=args.locator,
            quote=args.quote,
        )
    elif args.command == "add-omission":
        add_qualified_omission(
            output_dir=args.output_dir,
            result_id=args.result_id,
            label=args.label,
            source=args.source,
            locator=args.locator,
            quote=args.quote,
        )
    else:
        mark_result_search_complete(output_dir=args.output_dir)


if __name__ == "__main__":
    _main()
