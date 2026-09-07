"""模板和教训共用的 Owner、确切副本确认及路径边界。"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

import yaml

from ._frontmatter import FrontmatterError, parse_frontmatter
from ._io import atomic_write


def execution_owner() -> str | None:
    from src.account_execution import current_authorization
    auth = current_authorization(required=False)
    return auth.owner_user_id if auth else None


def require_owner(owner_id: str | None) -> str:
    if not isinstance(owner_id, str) or not owner_id.strip():
        raise PermissionError("缺少库 Owner")
    auth_owner = execution_owner()
    if auth_owner is not None and auth_owner != owner_id:
        raise PermissionError("库 Owner 与执行身份不一致")
    return owner_id


def content_fields(entry: dict) -> dict:
    return {
        "title": str(entry.get("title") or "").strip(),
        "keywords": [str(k).strip() for k in (entry.get("keywords") or []) if str(k).strip()],
        "body": str(entry.get("body") or "").strip(),
        "data_type": str(entry.get("data_type") or "").strip().lower(),
    }


def content_digest(entry: dict) -> str:
    return hashlib.sha256(json.dumps(content_fields(entry), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def valid_entry(entry: dict) -> bool:
    owner = entry.get("owner_id")
    if not isinstance(owner, str) or not owner.strip():
        return False
    if entry.get("scope") == "owner":
        return True
    return bool(
        entry.get("scope") == "platform" and entry.get("confirmed") is True
        and entry.get("confirmed_by") == owner and entry.get("confirmed_at")
        and entry.get("source_slug") and entry.get("source_digest")
        and entry.get("shared_content_digest") == content_digest(entry)
    )


def visible(entry: dict, owner_id: str | None, scope: str = "visible") -> bool:
    if not owner_id or not valid_entry(entry):
        return False
    if scope not in {"visible", "owner", "platform"}:
        raise ValueError("库范围无效")
    private = entry["scope"] == "owner" and entry["owner_id"] == owner_id
    platform = entry["scope"] == "platform"
    return private if scope == "owner" else platform if scope == "platform" else private or platform


def entry_path(directory: Path, slug: str) -> Path:
    if not isinstance(slug, str) or not re.fullmatch(r"[\w-]+", slug) or len(slug) > 160:
        raise ValueError("库条目编号无效")
    if re.fullmatch(r"(?i:con|prn|aux|nul|com[1-9]|lpt[1-9])", slug):
        raise ValueError("库条目编号不可使用设备名")
    path = directory / f"{slug}.md"
    if path.is_symlink() or path.resolve().parent != directory.resolve():
        raise ValueError("库条目路径越界")
    return path


def read_entry(directory: Path, slug: str) -> dict | None:
    path = entry_path(directory, slug)
    try:
        parsed = parse_frontmatter(path.read_text(encoding="utf-8"))
    except (OSError, FrontmatterError):
        return None
    if parsed is None:
        return None
    meta, body = parsed
    entry = {**meta, "body": body, "slug": slug}
    return entry if valid_entry(entry) else None


def may_mutate(entry: dict | None, owner_id: str | None, *, stats=False, is_admin=False) -> bool:
    if not owner_id or not entry or not valid_entry(entry):
        return False
    require_owner(owner_id)
    if entry["scope"] == "platform":
        return stats or is_admin or entry["owner_id"] == owner_id
    return entry["owner_id"] == owner_id


def share_copy(directory: Path, slug: str, *, owner_id: str, title: str, keywords: list[str], body: str,
               data_type: str, expected_source_digest: str, expected_content_digest: str, confirmed: bool,
               stats: dict, initial_status: str = "draft", source_quality: dict | None = None) -> dict:
    require_owner(owner_id)
    if confirmed is not True:
        raise ValueError("须明确确认通用副本")
    source = read_entry(directory, slug)
    if not source or source["scope"] != "owner" or source["owner_id"] != owner_id:
        raise PermissionError("只能分享本人原件")
    if content_digest(source) != expected_source_digest:
        raise ValueError("来源内容已变化，请重新预览")
    fields = content_fields(dict(title=title, keywords=keywords, body=body, data_type=data_type))
    if not fields["title"] or not fields["body"] or content_digest(fields) != expected_content_digest:
        raise ValueError("确认内容不一致")
    now = datetime.now().isoformat()
    identity = json.dumps([owner_id, slug, expected_source_digest, expected_content_digest], ensure_ascii=False)
    shared_slug = "shared-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
    existing = read_entry(directory, shared_slug)
    if existing and existing.get("shared_content_digest") == expected_content_digest:
        return {**existing, "content_digest": content_digest(existing)}
    if entry_path(directory, shared_slug).exists():
        raise ValueError("既有共享文件无法核对，禁止覆盖")
    meta = {**fields, **stats, "scope": "platform", "owner_id": owner_id, "status": initial_status, "source_quality": source_quality,
            "created_at": now, "confirmed": True, "confirmed_by": owner_id, "confirmed_at": now,
            "source_slug": slug, "source_digest": expected_source_digest,
            "shared_content_digest": expected_content_digest}
    shared_body = meta.pop("body")
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    atomic_write(entry_path(directory, shared_slug), f"---\n{front}\n---\n{shared_body}\n")
    return {**meta, "body": shared_body, "slug": shared_slug, "content_digest": expected_content_digest}

def same_private_snapshot(directory: Path, entry: dict, owner_id: str | None) -> bool:
    if not visible(entry, owner_id, "owner"):
        return False
    current = read_entry(directory, entry["slug"])
    return bool(current and visible(current, owner_id, "owner") and content_digest(current) == content_digest(entry))


def apply_private_merge(directory: Path, slug: str, merged: dict, *, owner_id: str | None,
                        expected_source_digest: str | None, loser_slug: str | None = None,
                        expected_loser_digest: str | None = None) -> bool:
    """调用方持库锁：两份快照仍一致才融合并删除同 Owner 重复原件。"""
    current = read_entry(directory, slug)
    if not current or not visible(current, owner_id, "owner") or content_digest(current) != expected_source_digest:
        return False
    require_owner(owner_id)
    if loser_slug:
        loser = read_entry(directory, loser_slug)
        if loser_slug == slug or not loser or not visible(loser, owner_id, "owner") or content_digest(loser) != expected_loser_digest:
            return False
    fields = content_fields({**current, **{k: merged.get(k) or current[k] for k in ("title", "keywords", "body")}})
    meta = {**current, **fields}
    body = meta.pop("body")
    meta.pop("slug", None)
    meta.pop("content_digest", None)
    front = yaml.safe_dump(meta, allow_unicode=True, sort_keys=False).strip()
    atomic_write(entry_path(directory, slug), f"---\n{front}\n---\n{body}\n")
    if loser_slug:
        entry_path(directory, loser_slug).unlink()
    return True
