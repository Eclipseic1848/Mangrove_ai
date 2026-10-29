"""只用合成采集器验证专属平台限制，不访问外部来源或模型。"""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from src.collectors.base import CollectedItem, CollectResult
from src.conductor.nodes import collect as node
from src.conductor.task_spec import TaskSpec


class XhsCollectRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def collect(self, platforms, *, first_success, first_error=None, first_coverage=None):
        first = CollectResult(
            first_success,
            "mediacrawler",
            items=[CollectedItem(
                url="https://www.xiaohongshu.com/explore/synthetic",
                title="合成笔记",
                content="这是完整的合成采集正文，不需要浏览器、来源请求或模型。",
            )] if first_success else [],
            message="合成来源结果" if first_success else "需要正常完成平台验证",
            coverage=first_coverage if first_coverage is not None else (
                {} if first_success else {"failure": {"reason": "challenge_required"}}),
        )
        primary = SimpleNamespace(collect=AsyncMock(return_value=first, side_effect=first_error))
        fallback = SimpleNamespace(collect=AsyncMock(return_value=CollectResult(
            True,
            "search",
            items=[CollectedItem(
                url="https://weibo.com/synthetic",
                title="其他来源的合成内容",
                content="这是另一个用户指定来源的合成正文，不访问真实平台。",
            )],
        )))
        spec = TaskSpec(intent="按原来源要求采集反馈", platforms=platforms,
                        keywords=["合成"], max_items=5)
        with patch.object(node, "get_registry", return_value={"mediacrawler": primary, "search": fallback}), \
                patch.object(node, "emit_progress") as progress, patch.object(node, "metrics_record"), \
                patch.object(node, "domain_health_record"):
            result = await node.collect_node({
                "task_spec": spec,
                "collector_candidates": ["mediacrawler", "search"],
            })
        self.assertEqual(spec.platforms, platforms)
        self.assertEqual(primary.collect.await_count, 1)
        self.warning_messages = [call.args[2] for call in progress.call_args_list if call.args[1] == "warning"]
        return result, fallback

    async def test_mixed_platform_topup_does_not_depend_on_platform_order(self):
        for platforms in (["小红书", "微博"], ["微博", "小红书"], ["XHS", "微博"]):
            with self.subTest(platforms=platforms):
                result, fallback = await self.collect(platforms, first_success=True)
                fallback.collect.assert_awaited_once()
                self.assertEqual(len(result["raw_dataset"]), 2)
                self.assertEqual(result["collector_used"], "mediacrawler+search")

    async def test_xhs_aliases_never_fallback_after_platform_rejection(self):
        for platform in ("小红书", "xhs", "XIAOHONGSHU", "小红书App"):
            with self.subTest(platform=platform):
                result, fallback = await self.collect([platform], first_success=False)
                fallback.collect.assert_not_awaited()
                self.assertEqual(result["raw_dataset"], [])
                self.assertEqual(len(result["collector_attempts"]), 1)
                self.assertEqual(result["collector_attempts"][0]["coverage"]["failure"]["reason"],
                                 "challenge_required")

    async def test_xhs_only_success_keeps_dedicated_collector(self):
        result, fallback = await self.collect(["小红书", "XHS"], first_success=True)
        fallback.collect.assert_not_awaited()
        self.assertEqual(len(result["raw_dataset"]), 1)
        self.assertEqual(result["collector_used"], "mediacrawler")

    async def test_other_platform_failure_keeps_existing_fallback(self):
        for platform in ("微博", "weibo"):
            with self.subTest(platform=platform):
                result, fallback = await self.collect([platform], first_success=False)
                fallback.collect.assert_awaited_once()
                self.assertEqual(len(result["raw_dataset"]), 1)
                self.assertEqual(result["collector_used"], "search")

    async def test_xhs_exception_progress_reports_stop_without_fallback_promise(self):
        for error in (TimeoutError(), RuntimeError("合成异常")):
            with self.subTest(error=type(error).__name__):
                result, fallback = await self.collect(["xhs"], first_success=False, first_error=error)
                fallback.collect.assert_not_awaited()
                self.assertEqual(result["raw_dataset"], [])
                self.assertTrue(any("已停止" in message for message in self.warning_messages))
                self.assertTrue(all("将检查其他可用来源" not in message for message in self.warning_messages))

    async def test_mixed_platform_exception_progress_keeps_existing_fallback_promise(self):
        for error in (TimeoutError(), RuntimeError("合成异常")):
            with self.subTest(error=type(error).__name__):
                _, fallback = await self.collect(["xhs", "微博"], first_success=False, first_error=error)
                fallback.collect.assert_awaited_once()
                self.assertTrue(any("将检查其他可用来源" in message for message in self.warning_messages))

    async def test_mixed_confirmed_source_rejection_stops_in_either_platform_order(self):
        for reason in ("challenge_required", "login_required", "rate_limited", "access_denied"):
            for platforms in (["小红书", "微博"], ["微博", "小红书"]):
                with self.subTest(reason=reason, platforms=platforms):
                    coverage = {"failure": {"reason": reason, "rejection_confirmed": True},
                                "connection": {"platform": "xhs", "source": "platform"}}
                    result, fallback = await self.collect(platforms, first_success=False, first_coverage=coverage)
                    fallback.collect.assert_not_awaited()
                    self.assertEqual(result["raw_dataset"], [])
                    self.assertEqual(result["collector_attempts"][0]["coverage"], coverage)
                    self.assertTrue(any("已停止" in message for message in self.warning_messages))
                    self.assertTrue(all("将检查其他可用来源" not in message for message in self.warning_messages))

    async def test_mixed_unconfirmed_failure_keeps_existing_fallback(self):
        for confirmed in (False, 1, None):
            for platforms in (["小红书", "微博"], ["微博", "小红书"]):
                with self.subTest(confirmed=confirmed, platforms=platforms):
                    failure = {"reason": "challenge_required"}
                    if confirmed is not None:
                        failure["rejection_confirmed"] = confirmed
                    result, fallback = await self.collect(platforms, first_success=False, first_coverage={
                        "failure": failure, "connection": {"platform": "xhs"}})
                    fallback.collect.assert_awaited_once()
                    self.assertEqual(len(result["raw_dataset"]), 1)

    async def test_formal_rejection_marker_stops_without_connection_metadata(self):
        coverage = {"failure": {"reason": "access_denied", "rejection_confirmed": True}}
        result, fallback = await self.collect(["微博", "小红书"], first_success=False, first_coverage=coverage)
        fallback.collect.assert_not_awaited()
        self.assertEqual(result["collector_attempts"][0]["coverage"], coverage)

    async def test_confirmed_flag_with_ordinary_failure_does_not_change_fallback(self):
        result, fallback = await self.collect(["小红书", "微博"], first_success=False, first_coverage={
            "failure": {"reason": "collection_failed", "rejection_confirmed": True}})
        fallback.collect.assert_awaited_once()
        self.assertEqual(len(result["raw_dataset"]), 1)

    async def test_mixed_rejection_preserves_previously_collected_items_and_original_scope(self):
        spec = TaskSpec(intent="按冻结来源采集反馈", platforms=["微博", "小红书"],
                        keywords=["合成"], max_items=5)
        before = spec.model_dump(mode="json")
        item = CollectedItem(url="https://weibo.com/synthetic-first", title="已有资料",
                             content="这是拒绝发生前已经完成读取的合成资料，必须保留。")
        first = SimpleNamespace(collect=AsyncMock(return_value=CollectResult(True, "first", items=[item])))
        coverage = {"failure": {"reason": "challenge_required", "rejection_confirmed": True},
                    "connection": {"platform": "xhs"}}
        primary = SimpleNamespace(collect=AsyncMock(return_value=CollectResult(
            False, "mediacrawler", message="需要正常完成平台验证", coverage=coverage)))
        fallback = SimpleNamespace(collect=AsyncMock(return_value=CollectResult(False, "search")))
        with patch.object(node, "get_registry", return_value={"first": first, "mediacrawler": primary, "search": fallback}), \
                patch.object(node, "emit_progress"), patch.object(node, "metrics_record"), \
                patch.object(node, "domain_health_record"):
            result = await node.collect_node({"task_spec": spec,
                "collector_candidates": ["first", "mediacrawler", "search"]})
        first.collect.assert_awaited_once()
        primary.collect.assert_awaited_once()
        fallback.collect.assert_not_awaited()
        self.assertEqual(result["raw_dataset"], [item.to_dict()])
        self.assertEqual(result["collector_used"], "first")
        self.assertEqual(result["collector_attempts"][-1]["coverage"], coverage)
        self.assertEqual(spec.model_dump(mode="json"), before)


if __name__ == "__main__":
    unittest.main()
