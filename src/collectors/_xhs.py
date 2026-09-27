"""小红书公开字段投影：保留来源内容，不透传认证或排除字段。"""
from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit


def public_note_url(value: str) -> str:
    """笔记公开定位不需要认证参数，避免 Token 进入模型和交付。"""
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _timestamp(value):
    if value in (None, "", 0, "0"):
        return None
    try:
        seconds = float(value)
        if seconds > 1e12:
            seconds /= 1000
        return datetime.fromtimestamp(seconds, timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def _count(value):
    # 缩写或带加号的展示值不伪装成精确计数，原值另行保留。
    text = str(value).strip()
    return int(text) if text.isascii() and text.isdecimal() else None


def public_note_fields(note: dict, comments: list[dict]) -> dict:
    note_id = str(note.get("note_id") or "")
    images = note.get("image_list") or []
    if isinstance(images, str):
        images = images.split(",")
    image_urls = []
    for image in images:
        url = (image.get("url_default") or image.get("url")) if isinstance(image, dict) else image
        if isinstance(url, str) and url.strip():
            public_url = public_note_url(url.strip())
            if public_url not in image_urls:
                image_urls.append(public_url)
    linked = []
    seen = set()
    for comment in comments:
        comment_id = str(comment.get("comment_id") or "")
        if not comment_id or comment_id in seen or str(comment.get("note_id") or "") != note_id:
            continue
        seen.add(comment_id)
        parent = str(comment.get("parent_comment_id") or "")
        linked.append({
            "comment_id": comment_id,
            "note_id": note_id,
            "parent_comment_id": parent if parent != "0" else "",
            "content": str(comment.get("content") or ""),
            "author_id": comment.get("user_id"),
            "author_name": comment.get("nickname"),
            "publish_time": _timestamp(comment.get("create_time")),
            "like_count": _count(comment.get("like_count")),
        })
    fields = {
        "note_id": note_id,
        "note_type": note.get("type"),
        "publish_time": _timestamp(note.get("time")),
        "publish_time_raw": note.get("time"),
        "last_update_time": _timestamp(note.get("last_update_time")),
        "crawl_time": datetime.now(timezone.utc).isoformat(),
        "author_id": note.get("user_id"),
        "author_name": note.get("nickname"),
        "verify_status": None,
        "verify_type": None,
        "account_type": None,
        "image_urls": image_urls,
        "cover_url": image_urls[0] if image_urls else None,
        "cover_source": "first_image" if image_urls else None,
        "comments": linked,
    }
    for target, source in (("like_count", "liked_count"), ("collect_count", "collected_count"),
                           ("comment_count", "comment_count"), ("share_count", "share_count")):
        fields[target] = _count(note.get(source))
        fields[target + "_raw"] = note.get(source)
    fields["missing_reasons"] = {key: "source_not_provided_or_unverified" for key, value in fields.items() if value is None}
    return fields
