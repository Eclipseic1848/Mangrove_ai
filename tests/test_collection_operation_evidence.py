"""操作结果与账号认证分开；评论缺口不能被帖子成功掩盖。"""
import asyncio
import json

import pytest

from src.collectors.base import CollectedItem, CollectResult
from src.collectors.social_media_collector import SocialMediaCollector, _diagnose_mc_failure, _operation_evidence
from src.conductor.task_spec import TaskSpec
from src.config.user_ctx import user_overrides_context
from tests.test_social_collection_isolation import crawler


@pytest.mark.parametrize("platform", ["xhs", "dy", "wb", "bili", "zhihu", "ks", "tieba"])
@pytest.mark.parametrize("error,reason,action", [
    ("验证码", "challenge_required", "complete_platform_challenge"),
    ("IPBlock", "rate_limited", "wait_for_rate_limit"),
    ("登录已过期", "login_required", "reauthenticate_selected_account"),
    ("private-cookie=secret", "collection_failed", "inspect_collection_service"),
])
def test_shared_failure_actions_do_not_leak_credentials(platform, error, reason, action):
    result = _diagnose_mc_failure(platform, error)
    assert (result["reason"], result["next_action"]) == (reason, action)
    assert "secret" not in json.dumps(result)


@pytest.mark.parametrize("coverage,status", [({}, "unknown"), ({"truncated": True}, "partial"), ({"truncated": False}, "succeeded")])
def test_detail_success_does_not_claim_comment_completion(coverage, status):
    spec = TaskSpec(intent="读取", urls=["https://www.xiaohongshu.com/explore/123"], platforms=["小红书"], include_comments=True)
    result = CollectResult(True, "synthetic", items=[CollectedItem(metadata={"comment_coverage": coverage})])
    evidence = _operation_evidence(spec, result)
    assert evidence["detail"]["status"] == "succeeded"
    assert evidence["comments"]["status"] == status
    assert "search" not in evidence
    assert not result.authentication


def test_real_synthetic_process_failure_reaches_operation_result(crawler):
    (crawler / "main.py").write_text('raise RuntimeError("登录已过期 private-cookie=secret")', encoding="utf-8")
    with user_overrides_context({"mc_cookie_xhs": "synthetic"}):
        result = asyncio.run(SocialMediaCollector().collect(TaskSpec(intent="读取", platforms=["小红书"], keywords=["规则"])))
    assert not result.success
    assert result.coverage["operations"]["search"]["next_action"] == "reauthenticate_selected_account"
    assert result.coverage["connection"] == {"platform": "xhs", "source": "personal"}
    assert "secret" not in json.dumps(result.coverage)
    assert not result.authentication


def test_collection_outcome_keeps_source_action_without_promoting_delivery():
    from src.conductor.collection_results import collection_outcome
    result = collection_outcome({"groups": [{"stop_reason": "collection_failed", "batches": [{"coverage": {
        "operations": {"search": {"next_action": "reauthenticate_selected_account"},
                       "comments": {"next_action": "private-secret"}},
    }}]}]})
    assert result["source_actions"] == ["reauthenticate_selected_account"]
    assert "不会自动续跑" in result["message"]
    assert "private-secret" not in json.dumps(result)
    assert result["status"] == "failed" and not result["ready_for_review"]
