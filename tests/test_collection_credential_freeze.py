"""跨页采集使用同一凭证，恢复不能悄悄改用新账号。"""
import asyncio
import json

import pytest

from src.collectors.base import CollectedItem, CollectResult
from src.config import user_ctx
from src.conductor import evidence_collection as flow
from tests.test_evidence_collection_flow import spec_for
from tests.test_social_collection_isolation import crawler


def test_collection_child_uses_frozen_environment(crawler, monkeypatch):
    from src.config.cookie_probe_binding import capture_probe_environment, probe_environment_context
    from src.conductor.task_spec import TaskSpec

    monkeypatch.setenv("SYNTHETIC_SOURCE_KEYWORD", "environment-A")
    environment = capture_probe_environment()
    monkeypatch.setenv("SYNTHETIC_SOURCE_KEYWORD", "environment-B")
    monkeypatch.setattr(flow.settings, "mediacrawler_path", "missing-environment-B")
    with probe_environment_context(environment), user_ctx.user_overrides_context({"mc_cookie_xhs": "synthetic"}):
        result = asyncio.run(flow.SocialMediaCollector().collect(TaskSpec(
            intent="合成测试", keywords=["environment-A"], platforms=["小红书"])))
    assert result.success, result.message
    assert result.items[0].metadata["source_keyword"] == "environment-A"


@pytest.mark.parametrize("key", ["mc_cookie_xhs", "mc_cookie_dy", "mc_cookie_wb", "mc_cookie_bili",
                                 "mc_cookie_zhihu", "mc_cookie_ks", "mc_cookie_tieba", "jd_cookie", "tb_cookie", "pdd_cookie"])
def test_empty_freeze_is_isolated_and_restored_after_cancellation(monkeypatch, key):
    monkeypatch.setattr(flow.settings, key, "")

    async def run():
        async def frozen():
            with pytest.raises(asyncio.CancelledError):
                with user_ctx.frozen_effective_values([key]):
                    monkeypatch.setattr(flow.settings, key, "new-shared")
                    await asyncio.sleep(0)
                    assert user_ctx.effective(key) == ""
                    raise asyncio.CancelledError()
            assert user_ctx.effective(key) == "new-shared"

        async def other():
            await asyncio.sleep(0)
            assert user_ctx.effective(key) == "new-shared"

        await asyncio.gather(frozen(), other())

    asyncio.run(run())


@pytest.mark.parametrize("personal", [False, True])
@pytest.mark.parametrize("platforms", [["小红书"], [], ["微博"]])
@pytest.mark.parametrize("rotation", ["credential", "environment"])
def test_pages_freeze_credential_and_resume_rejects_rotation(tmp_path, monkeypatch, personal, platforms, rotation):
    monkeypatch.setattr(flow.settings, "data_prep_artifact_root", str(tmp_path / "artifacts"))
    monkeypatch.setattr(flow.settings, "semantic_execution_root", str(tmp_path / "execution"))
    monkeypatch.setattr(flow.settings, "mc_cookie_xhs", "shared-old")
    monkeypatch.setattr(flow, "execution_owner", lambda: "owner-a")
    seen = []
    monkeypatch.setenv("MANGROVE_SYNTHETIC_NODE", "environment-A")

    async def collect(self, request):
        seen.append(user_ctx.effective("mc_cookie_xhs"))
        if rotation == "credential":
            monkeypatch.setattr(flow.settings, "mc_cookie_xhs", "shared-new")
        else:
            monkeypatch.setenv("MANGROVE_SYNTHETIC_NODE", "environment-B")
        return CollectResult(True, "synthetic", items=[CollectedItem(content="规则", metadata={
            "note_id": str(len(seen)), "note_type": "normal"})],
            coverage={"pages": [{"has_more": len(seen) < 2}]})

    async def assess(*args):
        return {"relevant": False, "records": []}

    async def images(*args):
        raise AssertionError("无图片不应调用")

    monkeypatch.setattr(flow.SocialMediaCollector, "collect", collect)
    spec = spec_for("服务规则", target_count=1, initial_candidates=1, candidate_limit=2)
    spec = spec.model_copy(update={"platforms": platforms})
    state = {"task_id": "frozen", "task_spec": spec}

    def run():
        return asyncio.run(flow.collect_evidence(state, scope=spec.evidence_collection,
            queries=spec.evidence_collection.queries, assess=assess, exclude=flow.exclude_topic, read_images=images))

    with user_ctx.user_overrides_context({"mc_cookie_xhs": "personal-old"} if personal else {}):
        run()
        assert seen == ["personal-old" if personal else "shared-old"] * 2
    with user_ctx.user_overrides_context({"mc_cookie_xhs": "personal-new" if rotation == "credential" else "personal-old"} if personal else {}):
        with pytest.raises(ValueError, match="凭证"):
            run()
    assert len(seen) == 2
    for path in (tmp_path / "execution").rglob("progress.json"):
        serialized = path.read_text(encoding="utf-8")
        assert all(value not in serialized for value in ("personal-old", "personal-new", "shared-old", "shared-new"))
        json.loads(serialized)
