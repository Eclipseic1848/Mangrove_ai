"""现役服务器兼容接缝；浏览器、模型与容器均使用合成替身。"""
import sys
from types import SimpleNamespace

import pytest

from src.conductor.task_spec import TaskSpec


@pytest.fixture
def browser_boundary(monkeypatch):
    from src.collectors import browser_collector as module

    contexts, stopped = [], []
    responses = [{"success": True, "final_result": "合成页面正文"}] * 2

    class Agent:
        async def run_async(self, task, context=None):
            contexts.append(context)
            return responses[len(contexts) - 1]

        def stop(self):
            stopped.append(True)

    async def acquire(_url):
        pass

    monkeypatch.setattr(module, "BrowserAgent", lambda **kwargs: Agent())
    monkeypatch.setattr(module.BrowserCollector, "is_available", lambda _self: True)
    monkeypatch.setattr(module, "get_rate_limiter", lambda: SimpleNamespace(acquire=acquire))
    return module.BrowserCollector(), contexts, stopped, responses


@pytest.mark.asyncio
async def test_browser_collector_isolates_context_for_each_url(browser_boundary):
    from src.agent.types.state_types import BrowserAgentContext

    collector, contexts, stopped, _responses = browser_boundary
    urls = ["https://first.invalid/page", "https://second.invalid/page"]
    result = await collector.collect(TaskSpec(intent="读取页面", urls=urls, max_items=2))
    assert result.success and [item.url for item in result.items] == urls
    assert len(contexts) == 2 and all(isinstance(context, BrowserAgentContext) for context in contexts)
    assert contexts[0] is not contexts[1]
    assert contexts[0].session_id != contexts[1].session_id
    assert stopped == [True]


@pytest.mark.asyncio
async def test_browser_collector_discards_explicit_failure_text(browser_boundary):
    collector, _contexts, stopped, responses = browser_boundary
    responses[0] = {"success": False, "final_result": "失败诊断不得冒充正文"}
    responses[1] = {"success": True, "final_result": "真正取得的合成正文"}
    urls = ["https://failed.invalid/page", "https://usable.invalid/page"]
    result = await collector.collect(TaskSpec(intent="读取页面", urls=urls, max_items=2))
    assert result.success
    assert [(item.url, item.content) for item in result.items] == [(urls[1], "真正取得的合成正文")]
    assert stopped == [True]


@pytest.mark.asyncio
async def test_crawl4ai_reuses_installed_linux_chrome_configuration(monkeypatch):
    from src.collectors import _common, crawl4ai_collector as module

    configured, closed = [], []

    class Crawler:
        def __init__(self, *, config=None):
            configured.append(config)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            closed.append(True)

        async def arun(self, *, url):
            return SimpleNamespace(success=True, markdown="合成网页正文", metadata={"title": "合成标题"})

    async def acquire(_url):
        pass

    monkeypatch.setitem(sys.modules, "crawl4ai", SimpleNamespace(BrowserConfig=lambda **kwargs: SimpleNamespace(**kwargs)))
    monkeypatch.setattr(_common, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(_common, "shutil", SimpleNamespace(which=lambda _name: "/synthetic/google-chrome"))
    monkeypatch.setattr(module, "_CRAWL4AI_OK", True)
    monkeypatch.setattr(module, "AsyncWebCrawler", Crawler)
    monkeypatch.setattr(module, "get_rate_limiter", lambda: SimpleNamespace(acquire=acquire))
    result = await module.Crawl4AICollector().collect(TaskSpec(intent="读取页面", urls=["https://crawl.invalid/page"]))
    assert result.success and result.items[0].content == "合成网页正文"
    assert len(configured) == 1 and getattr(configured[0], "chrome_channel", None) == "chrome"
    assert closed == [True]


@pytest.fixture
def browser_llm_boundary(monkeypatch):
    from src.services import llm_provider as module

    calls = []

    def client(**kwargs):
        return SimpleNamespace(options=kwargs)

    def model(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace()

    monkeypatch.setattr(module, "httpx", SimpleNamespace(Client=client, AsyncClient=client))
    monkeypatch.setattr(module, "ChatOpenAI", model)
    monkeypatch.setattr(module.settings, "llm_base_url", "http://127.0.0.1:19999/v1")
    monkeypatch.setattr(module.settings, "llm_api_key", "synthetic-unused")
    monkeypatch.setattr(module.settings, "llm_model_name", "Qwen3-Synthetic")
    monkeypatch.setattr(module.settings, "llm_max_tokens", 512)
    monkeypatch.setattr(module.settings, "local_max_tokens", 8192)
    monkeypatch.setattr(module.settings, "local_enable_thinking", None)
    provider = object.__new__(module.LLMProvider)
    provider._llm = None
    return provider, calls, module


def test_browser_llm_uses_local_budget_and_keeps_execution_hooks(browser_llm_boundary):
    provider, calls, module = browser_llm_boundary
    provider._initialize_llm()
    assert len(calls) == 1 and calls[0]["max_tokens"] == 8192
    assert "extra_body" not in calls[0]
    assert calls[0]["http_client"].options["event_hooks"] == {
        "request": [module.execution_http_checkpoint], "response": [module.execution_http_checkpoint],
    }
    assert calls[0]["http_async_client"].options["event_hooks"] == {
        "request": [module.execution_http_checkpoint_async], "response": [module.execution_http_checkpoint_async],
    }
    assert calls[0]["http_client"].options["trust_env"] is False
    assert calls[0]["http_async_client"].options["trust_env"] is False


@pytest.mark.parametrize("snapshot_key", ["evidence_collection", "bank_collection"])
def test_collection_progress_preserves_only_controlled_failure_message(snapshot_key):
    from src.conductor.progress import completed_summary

    snapshot = {"groups": [{"stop_reason": "collection_failed", "batches": [{"coverage": {
        "operations": {"search": {"next_action": "reauthenticate_selected_account"},
                       "comments": {"next_action": "内部诊断不得公开"}},
    }}]}]}
    message = ("来源获取失败，诊断快照已保留；未创建业务初稿。"
               "请在采集账号设置中更新本任务实际使用的账号凭证，再验证身份；更新凭证不会自动续跑。")
    assert completed_summary("collect", {"error": message, snapshot_key: snapshot}) == message
    assert completed_summary("collect", {"error": "内部诊断不得公开", snapshot_key: snapshot}) == "本阶段未完成，请查看任务提示。"
    assert completed_summary("analyze", {"error": message, snapshot_key: snapshot}) == "本阶段未完成，请查看任务提示。"
    assert completed_summary("collect", {"error": message}) == "本阶段未完成，请查看任务提示。"


@pytest.mark.parametrize("model,thinking,expected", [
    ("Qwen3-Synthetic", False, {"chat_template_kwargs": {"enable_thinking": False}}),
    ("qWeN3.8-Synthetic", True, {"chat_template_kwargs": {"enable_thinking": True}}),
    ("Qwen3-Synthetic", None, None),
    ("Other-Synthetic", False, None),
], ids=["disabled", "enabled", "unset", "other-model"])
def test_browser_llm_keeps_qwen3_thinking_configuration(browser_llm_boundary, monkeypatch, model, thinking, expected):
    provider, calls, module = browser_llm_boundary
    monkeypatch.setattr(module.settings, "llm_model_name", model)
    monkeypatch.setattr(module.settings, "local_enable_thinking", thinking)
    provider._initialize_llm()
    assert calls[0].get("extra_body") == expected
    assert calls[0]["max_tokens"] == 8192


@pytest.mark.parametrize("platform,uid,expected", [
    ("posix", 1000, "1000:1000"), ("posix", 0, None), ("nt", 1000, None),
], ids=["linux-service", "root-host", "windows"])
def test_registry_mount_uses_service_identity_without_overriding_root_or_windows(tmp_path, monkeypatch, platform, uid, expected):
    from src.capability_governance.oci_signing import LockedCliOciSigningRuntime, LockedOciSigningToolchain
    from src.utils import docker_user
    from tests.test_capability_signing import RecordingRunner

    monkeypatch.setattr(docker_user, "os", SimpleNamespace(name=platform, geteuid=lambda: uid, getegid=lambda: 1000))
    runner = RecordingRunner()
    runtime = LockedCliOciSigningRuntime(
        toolchain=LockedOciSigningToolchain(cosign_executable=tmp_path / "cosign", oras_executable=tmp_path / "oras",
            registry_image="ghcr.io/project-zot/zot-linux-amd64:v2.1.20@sha256:" + "a" * 64),
        work_root=tmp_path / "runtime", project_root=tmp_path, protected_key_roots=(),
        runner=runner, health_probe=lambda _endpoint: True,
    )
    runtime.recover("synthetic-server-compat")
    lease = runtime.start_registry("synthetic-server-compat")
    try:
        command = next(command for command in runner.commands if command[1] == "run")
        if expected is None:
            assert "--user" not in command
        else:
            assert command[command.index("--user") + 1] == expected
        assert command[command.index("--publish") + 1] == "127.0.0.1::5000"
        assert any(value.endswith(":/etc/zot/config.json:ro") for value in command)
        assert "mangrove.ac07.signing.transaction=synthetic-server-compat" in command
    finally:
        runtime.stop_registry(lease)
    assert any(command[1:3] == ("rm", "--force") for command in runner.commands)
    assert not (tmp_path / "runtime").exists()
