"""显式链接目标的确定性识别。"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlsplit

_VIDEO_EXT_RE = re.compile(r"\.(?:mp4|webm|mkv|mov|m4v|avi)(?:$|[?#])", re.I)
_ID_PATTERNS = {
    "抖音": re.compile(r"/video/(\d+)", re.I),
    "B站": re.compile(r"/(?:video/)?(?:BV[\w]+|av\d+)", re.I),
    "YouTube": re.compile(r"(?:v=|youtu\.be/|shorts/)([\w-]{6,})", re.I),
    "快手": re.compile(r"/short-video/([\w-]+)", re.I),
    "小红书": re.compile(r"/(?:explore|discovery/item)/([\w-]+)", re.I),
    "微博": re.compile(r"/(?:detail|tv/show)/([\w-]+)", re.I),
}

def _host(url: str) -> str:
    host = (urlsplit(url if "://" in url else f"//{url}").netloc or "").split("@")[-1].split(":")[0].lower()
    return host[4:] if host.startswith("www.") else host

def platform_from_url(url: str) -> str:
    host = _host(url)
    if host.endswith("douyin.com") or host.endswith("iesdouyin.com"):
        return "抖音"
    if host.endswith("bilibili.com") or host == "b23.tv":
        return "B站"
    if host.endswith("youtube.com") or host == "youtu.be":
        return "YouTube"
    if host.endswith("kuaishou.com") or host.endswith("chenzhongtech.com"):
        return "快手"
    if host.endswith("xiaohongshu.com") or host == "xhslink.com":
        return "小红书"
    if host.endswith("weibo.com") or host.endswith("weibo.cn"):
        return "微博"
    return "direct" if _VIDEO_EXT_RE.search(url or "") else ""

def content_id_from_url(url: str, platform: Optional[str] = None) -> str:
    pattern = _ID_PATTERNS.get(platform or platform_from_url(url))
    match = pattern.search(url or "") if pattern else None
    return match.group(1) if match else ""

def is_video_target(url: str, platform: Optional[str] = None) -> bool:
    return (platform or platform_from_url(url)) in {"抖音", "B站", "YouTube", "快手", "小红书", "微博", "direct"}

def build_target_manifest(urls: Iterable[str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for raw in urls or []:
        url = str(raw or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        platform = platform_from_url(url)
        if not platform:
            continue
        content_id = content_id_from_url(url, platform)
        out.append({"requested_url": url, "canonical_url": url if content_id else "", "platform": platform,
                    "content_id": content_id, "media_kind": "video" if is_video_target(url, platform) else "unknown",
                    "collection_mode": "direct", "identity_verified": False})
    return out

def is_direct_video_manifest(manifest: Iterable[Dict[str, Any]]) -> bool:
    return any((m or {}).get("collection_mode") == "direct" and (m or {}).get("media_kind") == "video" for m in (manifest or []))

def target_matches(item: Dict[str, Any], target: Dict[str, Any]) -> bool:
    """核验采集结果是否仍指向用户给出的同一个视频目标。"""
    metadata = (item or {}).get("metadata") or {}
    expected = str((target or {}).get("content_id") or "")
    actual = str((item or {}).get("content_id") or metadata.get("content_id") or "")
    canonical_url = str(
        (item or {}).get("canonical_url") or metadata.get("canonical_url") or (item or {}).get("url") or ""
    )
    if expected:
        return bool(actual and actual.lower() == expected.lower() and canonical_url)
    # 短链接没有可在本地解析的内容 ID 时，只接受详情采集器已明确标记的结果。
    return bool(metadata.get("identity_verified") and canonical_url)