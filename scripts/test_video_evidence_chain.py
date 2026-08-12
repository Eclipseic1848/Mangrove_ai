"""显式视频证据链的离线回归测试。"""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.collectors.social_media_collector import _build_detail_cmd
from src.conductor.graph import _route_after_collect, _route_after_video_enrich
from src.conductor.nodes.checker import checker_node
from src.conductor.nodes.video_enrich import video_enrich_node
from src.conductor.targets import build_target_manifest, target_matches
from src.conductor.task_spec import TaskSpec


class VideoEvidenceChainTests(unittest.TestCase):
    def test_douyin_short_link_is_video_target(self):
        target = build_target_manifest(["https://v.douyin.com/2fbnPZn1iSg/"])[0]
        self.assertEqual(target["platform"], "抖音")
        self.assertEqual(target["media_kind"], "video")

    def test_direct_mp4_is_video_target(self):
        target = build_target_manifest(["https://example.com/demo.mp4"])[0]
        self.assertEqual(target["platform"], "direct")
        self.assertEqual(target["collection_mode"], "direct")

    def test_detail_command_does_not_include_search_keywords(self):
        command = _build_detail_cmd("python", "dy", ["https://v.douyin.com/abc/"], False, "", 1)
        self.assertIn("detail", command)
        self.assertIn("--specified_id", command)
        self.assertNotIn("--keywords", command)

    def test_identity_match_rejects_other_video(self):
        target = build_target_manifest(["https://www.douyin.com/video/123456"])[0]
        matched = {"url": "https://www.douyin.com/video/123456", "metadata": {"content_id": "123456"}}
        other = {"url": "https://www.douyin.com/video/999999", "metadata": {"content_id": "999999"}}
        self.assertTrue(target_matches(matched, target))
        self.assertFalse(target_matches(other, target))

    def test_manifest_marks_video_for_evidence_chain(self):
        target = build_target_manifest(["https://example.com/demo.mp4"])[0]
        self.assertEqual(target["media_kind"], "video")
        self.assertEqual(target["collection_mode"], "direct")

    def test_video_route_runs_without_raw_data(self):
        state = {"target_manifest": build_target_manifest(["https://example.com/demo.mp4"]), "raw_dataset": []}
        self.assertEqual(_route_after_collect(state), "video_enrich")
        state["evidence_ready"] = False
        self.assertEqual(_route_after_video_enrich(state), "checker")

    def test_missing_target_becomes_evidence_block(self):
        spec = TaskSpec(intent="总结视频", urls=["https://example.com/demo.mp4"])
        state = {"task_spec": spec, "target_manifest": build_target_manifest(spec.urls), "raw_dataset": []}
        result = asyncio.run(video_enrich_node(state))
        self.assertFalse(result["evidence_ready"])
        self.assertEqual(result["analysis_source"], "video_evidence_blocked")

    def test_video_enrich_keeps_collection_failure_note(self):
        spec = TaskSpec(intent="总结视频", urls=["https://example.com/demo.mp4"])
        state = {
            "task_spec": spec,
            "target_manifest": build_target_manifest(spec.urls),
            "raw_dataset": [],
            "collector_notes": ["⚠️ 抖音登录失败"],
        }
        result = asyncio.run(video_enrich_node(state))
        self.assertIn("⚠️ 抖音登录失败", result["collector_notes"])

    def test_checker_blocks_without_evidence(self):
        spec = TaskSpec(intent="总结视频", urls=["https://example.com/demo.mp4"])
        state = {
            "task_spec": spec,
            "target_manifest": build_target_manifest(spec.urls),
            "evidence_ready": False,
            "analysis": "不应作为视频内容结论的文本",
        }
        result = asyncio.run(checker_node(state))
        self.assertEqual(result["quality"]["score"], 0)
        self.assertFalse(result["quality"]["passed"])




if __name__ == "__main__":
    unittest.main()
