"""
YouTube 专用采集器（Tier 8）。

用 yt-dlp（业界标准，免密钥）做关键词搜索或直接解析视频 URL，取标题/简介/作者/
播放量等元数据入库。yt-dlp 是同步阻塞库，经 asyncio.to_thread 丢线程池避免卡住事件循环。

安装（可选，未装则本采集器自动跳过）：pip install yt-dlp

当前只取视频描述作为正文（信息量通常够用），暂不下载/解析字幕（如需逐字稿，
后续可扩展 writesubtitles + VTT 解析）。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List

from src.config.settings import settings
from src.conductor.task_spec import TaskSpec

from .base import BaseCollector, CollectedItem, CollectResult
from .registry import register

logger = logging.getLogger(__name__)


def _is_youtube(spec: TaskSpec) -> bool:
    for p in spec.platforms:
        if p.strip().lower() in ("youtube", "yt"):
            return True
    return any(("youtube.com" in u or "youtu.be" in u) for u in spec.urls)


class YouTubeCollector(BaseCollector):
    """YouTube 视频元数据采集器——yt-dlp 搜索/解析，免登录免密钥。"""

    name = "youtube"
    tier = 8  # mediacrawler(0)/rsshub(5) 之后，YouTube 为海外专用平台，与国内社媒不重叠

    def is_available(self) -> bool:
        try:
            import yt_dlp  # noqa: F401
            return True
        except ImportError:
            return False

    def matches(self, spec: TaskSpec) -> bool:
        return _is_youtube(spec)

    async def collect(self, spec: TaskSpec) -> CollectResult:
        if not self.is_available():
            return CollectResult(False, self.name, message="yt-dlp 未安装（pip install yt-dlp）")
        if not spec.urls and not spec.keywords:
            return CollectResult(False, self.name, message="YouTube 采集需要关键词或视频 URL")

        limit = max(1, min(spec.max_items, settings.collector_max_items))
        targets = spec.urls or [f"ytsearch{limit}:{' '.join(spec.keywords)}"]

        items: List[CollectedItem] = []
        for target in targets:
            try:
                infos = await asyncio.to_thread(self._extract, target, limit)
            except Exception as e:
                logger.warning("YouTube 提取失败 %s: %s", target, e)
                continue
            for info in infos:
                if len(items) >= limit:
                    break
                items.append(self._to_item(info))
            if len(items) >= limit:
                break

        if not items:
            return CollectResult(False, self.name, message="未获取到 YouTube 视频数据")
        return CollectResult(True, self.name, items=items, message=f"采集 {len(items)} 个 YouTube 视频")

    def _extract(self, target: str, limit: int) -> List[Dict[str, Any]]:
        """同步调用 yt-dlp（在线程池中执行，避免阻塞事件循环）。"""
        import yt_dlp
        ydl_opts = {
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "extract_flat": False,  # 拿完整元数据（含描述）
            "playlistend": limit,
        }
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(target, download=False)
        if info is None:
            return []
        if "entries" in info:
            return [e for e in info["entries"] if e]
        return [info]

    def _to_item(self, info: Dict[str, Any]) -> CollectedItem:
        title = info.get("title", "") or ""
        desc = info.get("description", "") or ""
        return CollectedItem(
            url=info.get("webpage_url") or info.get("url", ""),
            title=title,
            content=desc or title,
            metadata={
                "engine": self.name,
                "uploader": info.get("uploader", ""),
                "duration": info.get("duration"),
                "view_count": info.get("view_count"),
                "upload_date": info.get("upload_date", ""),
            },
        )


register(YouTubeCollector())
