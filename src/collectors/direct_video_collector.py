"""直接媒体文件采集器：为后续视频证据提取保留目标链接。"""
from __future__ import annotations

from src.conductor.targets import build_target_manifest
from src.conductor.task_spec import TaskSpec

from .base import BaseCollector, CollectedItem, CollectResult
from .registry import register


class DirectVideoCollector(BaseCollector):
    """只处理可直接下载的视频文件，不抢占平台页面采集器。"""

    name = "direct_video"
    tier = -1

    def matches(self, spec: TaskSpec) -> bool:
        return any(target.get("platform") == "direct" for target in build_target_manifest(spec.urls or []))

    async def collect(self, spec: TaskSpec) -> CollectResult:
        targets = [
            target for target in build_target_manifest(spec.urls or [])
            if target.get("platform") == "direct"
        ]
        items = [
            CollectedItem(
                url=target["requested_url"],
                title="直接视频文件",
                content="",
                metadata={
                    "engine": self.name,
                    "collection_mode": "direct",
                    "requested_url": target["requested_url"],
                    "canonical_url": target["canonical_url"] or target["requested_url"],
                    "media_url": target["requested_url"],
                    "identity_verified": True,
                },
            )
            for target in targets
        ]
        return CollectResult(True, self.name, items=items, message=f"已识别 {len(items)} 个直接视频文件")


register(DirectVideoCollector())