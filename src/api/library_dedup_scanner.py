"""
模板库/教训库定时巡检：默认关闭的独立轮询协程。

风格参照 src/api/cookie_health_scanner.py（异步轮询、开关关闭时空转、可被 stop() 打断），
但业务完全独立、不共用类——这里巡检的是知识库自身的语义去重与停滞清理，与 Cookie 健康
无关。每轮巡检对模板库、教训库各自：① 按 data_type 语义去重，发现重复对自动融合合并
（保留使用统计更高的一方，另一方删除）；② 清理长期停滞（创建后超过阈值天数仍是 draft）
的条目。结果写入 webui.db 的 library_dedup_scan_log 表。
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime
from typing import Optional

from src.config.settings import settings

logger = logging.getLogger(__name__)

_IDLE_CHECK_SECONDS = 300.0   # 巡检关闭/未到点时，多久再检查一次
_BETWEEN_ITEM_SECONDS = 5.0   # 同一轮巡检内，相邻两次合并之间的等待


class LibraryDedupScanner:
    """异步轮询协程：开关关闭时空转，开启且到点时对模板库/教训库各跑一轮去重+停滞清理。"""

    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._stop = asyncio.Event()
        self._last_scan_at: float = 0.0

    def start(self) -> None:
        """启动后台轮询（幂等）。需在已有事件循环内调用。"""
        if self._task and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            await self._task

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    async def _dedup_pass_templates(self) -> tuple[int, int, list]:
        """模板库去重扫描：返回 (scanned, merged, details)。"""
        from src.memory import templates as tpl

        entries = tpl.load_templates()
        scanned = len(entries)
        merged_slugs: set = set()
        merged = 0
        details: list = []
        for entry in entries:
            if merged >= settings.library_dedup_scan_max_merges_per_run:
                break
            if entry["slug"] in merged_slugs or entry["status"] == "retired":
                continue
            # embedding/rerank 客户端为同步 HTTP，放入工作线程，避免阻塞 API 事件循环。
            result = await asyncio.to_thread(tpl.find_patrol_duplicate, entry)
            if not result:
                continue
            dup, score = result
            if dup["slug"] in merged_slugs:
                continue
            survivor, loser = (entry, dup) if entry["uses"] >= dup["uses"] else (dup, entry)
            fused = await tpl.merge_template_pair(survivor, loser)
            if not fused:
                continue
            if not tpl.apply_patrol_merge(survivor["slug"], fused):
                continue
            tpl.delete_template(loser["slug"])
            merged_slugs.add(survivor["slug"])
            merged_slugs.add(loser["slug"])
            merged += 1
            details.append({
                "action": "merge_template",
                "survivor_slug": survivor["slug"],
                "survivor_title": survivor["title"],
                "loser_slug": loser["slug"],
                "loser_title": loser["title"],
                "data_type": survivor.get("data_type", ""),
                "score": round(score, 4),
                "threshold": settings.template_dedup_rerank_threshold,
            })
            await self._sleep(_BETWEEN_ITEM_SECONDS)
        return scanned, merged, details

    async def _dedup_pass_lessons(self) -> tuple[int, int, list]:
        """教训库去重扫描：返回 (scanned, merged, details)。"""
        from src.memory import lessons as lsn

        entries = lsn.load_lessons()
        scanned = len(entries)
        merged_slugs: set = set()
        merged = 0
        details: list = []
        for entry in entries:
            if merged >= settings.library_dedup_scan_max_merges_per_run:
                break
            if entry["slug"] in merged_slugs:
                continue
            # embedding/rerank 客户端为同步 HTTP，放入工作线程，避免阻塞 API 事件循环。
            result = await asyncio.to_thread(lsn.find_patrol_duplicate_lesson, entry)
            if not result:
                continue
            dup, score = result
            if dup["slug"] in merged_slugs:
                continue
            survivor, loser = (entry, dup) if entry["occurrences"] >= dup["occurrences"] else (dup, entry)
            fused = await lsn.merge_lesson_pair(survivor, loser)
            if not fused:
                continue
            if not lsn.apply_patrol_merge_lesson(survivor["slug"], fused):
                continue
            lsn.delete_lesson(loser["slug"])
            merged_slugs.add(survivor["slug"])
            merged_slugs.add(loser["slug"])
            merged += 1
            details.append({
                "action": "merge_lesson",
                "survivor_slug": survivor["slug"],
                "survivor_title": survivor["title"],
                "loser_slug": loser["slug"],
                "loser_title": loser["title"],
                "data_type": survivor.get("data_type", ""),
                "score": round(score, 4),
                "threshold": settings.template_dedup_rerank_threshold,
            })
            await self._sleep(_BETWEEN_ITEM_SECONDS)
        return scanned, merged, details

    def _stale_pass_templates(self) -> tuple[int, list]:
        """模板库停滞草稿清理：返回 (deleted, details)。"""
        from src.memory import templates as tpl

        now = datetime.now()
        deleted = 0
        details: list = []
        for entry in tpl.load_templates():
            if entry["status"] != "draft" or not entry.get("created_at"):
                continue
            try:
                created = datetime.fromisoformat(entry["created_at"])
            except ValueError:
                continue
            stale_days = (now - created).days
            if stale_days > settings.library_stale_draft_days:
                if tpl.delete_template(entry["slug"]):
                    deleted += 1
                    details.append({
                        "action": "stale_delete_template",
                        "slug": entry["slug"],
                        "title": entry["title"],
                        "data_type": entry.get("data_type", ""),
                        "stale_days": stale_days,
                        "threshold_days": settings.library_stale_draft_days,
                    })
        return deleted, details

    def _stale_pass_lessons(self) -> tuple[int, list]:
        """教训库停滞草稿清理：返回 (deleted, details)。"""
        from src.memory import lessons as lsn

        now = datetime.now()
        deleted = 0
        details: list = []
        for entry in lsn.load_lessons():
            if entry["status"] != "draft" or not entry.get("created_at"):
                continue
            try:
                created = datetime.fromisoformat(entry["created_at"])
            except ValueError:
                continue
            stale_days = (now - created).days
            if stale_days > settings.library_stale_draft_days:
                if lsn.delete_lesson(entry["slug"]):
                    deleted += 1
                    details.append({
                        "action": "stale_delete_lesson",
                        "slug": entry["slug"],
                        "title": entry["title"],
                        "data_type": entry.get("data_type", ""),
                        "stale_days": stale_days,
                        "threshold_days": settings.library_stale_draft_days,
                    })
        return deleted, details

    async def _run_one_scan(self) -> None:
        from src.api.auth import get_store

        logger.info("模板库/教训库定时巡检：开始一轮扫描")
        templates_scanned, templates_merged, tpl_details = await self._dedup_pass_templates()
        lessons_scanned, lessons_merged, lsn_details = await self._dedup_pass_lessons()
        stale_deleted_t, stale_details_t = self._stale_pass_templates()
        stale_deleted_l, stale_details_l = self._stale_pass_lessons()
        stale_deleted = stale_deleted_t + stale_deleted_l
        all_details = tpl_details + lsn_details + stale_details_t + stale_details_l
        try:
            get_store().library_dedup_scan_log_add(
                templates_scanned=templates_scanned,
                templates_merged=templates_merged,
                lessons_scanned=lessons_scanned,
                lessons_merged=lessons_merged,
                stale_drafts_deleted=stale_deleted,
                details=json.dumps(all_details, ensure_ascii=False),
            )
        except Exception:
            logger.warning("巡检日志落库失败（不影响本轮巡检结果）", exc_info=True)
        logger.info(
            "模板库/教训库定时巡检：本轮完成（模板 %d扫/%d合，教训 %d扫/%d合，停滞清理 %d条）",
            templates_scanned, templates_merged, lessons_scanned, lessons_merged, stale_deleted,
        )

    async def _loop(self) -> None:
        while not self._stop.is_set():
            if not settings.library_dedup_scan_enabled:
                await self._sleep(_IDLE_CHECK_SECONDS)
                continue
            interval_seconds = max(1, settings.library_dedup_scan_interval_hours) * 3600.0
            if time.time() - self._last_scan_at < interval_seconds:
                await self._sleep(min(_IDLE_CHECK_SECONDS, interval_seconds))
                continue
            try:
                await self._run_one_scan()
            except Exception as e:  # noqa: BLE001 扫描本身意外出错也不能让循环死掉
                logger.exception("模板库/教训库定时巡检：本轮扫描异常：%s", e)
            self._last_scan_at = time.time()


_scanner: Optional[LibraryDedupScanner] = None


def start_library_dedup_scanner() -> None:
    """幂等启动。即使巡检开关关闭也会启动循环本身（循环内部自己空转直到开关打开），
    这样管理员切换开关不需要重启进程。需在已有事件循环内调用（FastAPI 启动钩子）。"""
    global _scanner
    if _scanner is not None:
        return
    _scanner = LibraryDedupScanner()
    _scanner.start()
