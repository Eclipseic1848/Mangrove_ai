"""操作结果与账号认证分开；评论缺口不能被帖子成功掩盖。"""
import asyncio
import hashlib
import json
from pathlib import Path
import runpy
import subprocess
import sys

import pytest

from src.collectors.base import CollectedItem, CollectResult
from src.collectors.social_media_collector import SocialMediaCollector, _diagnose_mc_failure, _operation_evidence, _safe_mc_failure_hint
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

@pytest.mark.parametrize("platform", ["小红书", "抖音", "微博", "B站", "快手", "知乎", "贴吧"])
@pytest.mark.parametrize("payload,exception_type,http_status", [
    (("HTTP Response: GET https://example.test/api?signature=fake-signature \"HTTP/1.1 403 Forbidden\"\n"
      "Traceback (most recent call last):\n  File \"/private/fake-host.py\", line 9\n"
      "RuntimeError: Cookie=fake-cookie Token=fake-token 作者正文=fake-body\n").encode("utf-8"), "RuntimeError", 403),
    (b"httpx.HTTPStatusError: Server error '503 Service Unavailable' for url "
     b"'https://example.test/api?signature=fake-signature' Cookie=fake-cookie Token=fake-token\n", "HTTPStatusError", 503),
    (b"OpaqueFailure: Cookie=fake-cookie Token=fake-token /private/fake-host.py "
     b"https://example.test/api?signature=fake-signature code=-100 status_code=403\n\xff", None, None),
    (b"RuntimeError: fake-cookie\nTypeError: fake-token\n"
     b"HTTP Response: GET https://example.test/a \"HTTP/1.1 403 Forbidden\"\n"
     b"HTTP Response: GET https://example.test/b \"HTTP/1.1 429 Too Many Requests\"\n", None, None),
])
def test_nonzero_process_retains_only_safe_diagnostic_hint(crawler, caplog, platform, payload, exception_type, http_status):
    # 本地合成子进程只打印假错误，不访问任何站点。
    (crawler / "main.py").write_text("import sys\nsys.stderr.buffer.write(" + repr(payload) + ")\nraise SystemExit(1)\n", encoding="utf-8")
    caplog.set_level("WARNING", logger="src.collectors.social_media_collector")
    with user_overrides_context({"mc_cookie_xhs": "synthetic"}):
        result = asyncio.run(SocialMediaCollector().collect(TaskSpec(intent="读取", platforms=[platform], keywords=["规则"])))
    assert not result.success and not result.authentication
    failure = result.coverage["failure"]
    assert set(failure) == {"reason", "next_action", "diagnostic_hint"}
    assert failure["reason"] == "collection_failed" and failure["next_action"] == "inspect_collection_service"
    assert failure["diagnostic_hint"] == {"exit_code": 1, "exception_type": exception_type,
        "http_status": http_status, "platform_code": None, "stdout_sha256": hashlib.sha256(payload).hexdigest(),
        "isolated_exception_type": None, "isolated_cause_type": None}
    assert result.coverage["operations"]["search"] == {"status": "unknown", "reason": "collection_failed",
        "next_action": "inspect_collection_service"}
    warnings = [record.getMessage() for record in caplog.records
        if record.name == "src.collectors.social_media_collector" and "MediaCrawler 退出码" in record.getMessage()]
    assert len(warnings) == 1
    exposed = json.dumps(result.coverage, ensure_ascii=False) + result.message + warnings[0]
    for private in ("fake-cookie", "fake-token", "fake-host.py", "fake-signature", "fake-body", "作者正文", "https://example.test", "/private/"):
        assert private not in exposed
    assert "403" not in result.message and "RuntimeError" not in result.message


@pytest.mark.parametrize("setup,factory,isolated_type,legacy_type,reason", [
    ("", "RuntimeError", "RuntimeError", "RuntimeError", "collection_failed"),
    ("", "ModuleNotFoundError", "ModuleNotFoundError", "ModuleNotFoundError", "collector_dependency_missing"),
    ("from playwright._impl._errors import Error\n", "Error", "PlaywrightError", None, "collection_failed"),
    ("from playwright._impl._errors import TimeoutError\n", "TimeoutError", "PlaywrightTimeoutError", "TimeoutError", "collection_failed"),
    ("from playwright._impl._errors import TargetClosedError\n", "TargetClosedError", "PlaywrightTargetClosedError", None, "collection_failed"),
    ("class RuntimeError(Exception): pass\n", "RuntimeError", "UnknownError", "RuntimeError", "collection_failed"),
])
def test_isolated_exception_marker_is_closed_and_keeps_failure_behavior(crawler, caplog, setup, factory, isolated_type, legacy_type, reason):
    secret = "Cookie=typed-cookie Token=typed-token https://example.test/?signature=typed-signature /private/typed-host.py"
    (crawler / "main.py").write_text(setup + f"raise {factory}({secret!r})\n", encoding="utf-8")
    caplog.set_level("WARNING", logger="src.collectors.social_media_collector")
    with user_overrides_context({"mc_cookie_xhs": "synthetic"}):
        result = asyncio.run(SocialMediaCollector().collect(TaskSpec(intent="读取", platforms=["小红书"], keywords=["规则"])))
    assert not result.success and not result.authentication
    failure = result.coverage["failure"]
    hint = failure["diagnostic_hint"]
    assert hint["isolated_exception_type"] == isolated_type
    assert hint["exit_code"] == 1 and hint["exception_type"] == legacy_type
    assert hint["http_status"] is None and hint["platform_code"] is None
    assert failure["reason"] == reason
    assert result.coverage["operations"]["search"]["status"] == "unknown"
    exposed = json.dumps(result.coverage) + result.message + "\n".join(record.getMessage() for record in caplog.records)
    for value in ("typed-cookie", "typed-token", "typed-signature", "typed-host.py", "https://example.test", "/private/"):
        assert value not in exposed


@pytest.mark.parametrize("payload,isolated_type", [
    (b"MANGROVE_MC_EXCEPTION_TYPE:RuntimeError\n", "RuntimeError"),
    (b"MANGROVE_MC_EXCEPTION_TYPE:ModuleNotFoundError\n", "ModuleNotFoundError"),
    (b"MANGROVE_MC_EXCEPTION_TYPE:PlaywrightError\r\n", "PlaywrightError"),
    (b"MANGROVE_MC_EXCEPTION_TYPE:UnknownError\n", "UnknownError"),
    (b"opaque-output\n", None),
    (b"MANGROVE_MC_EXCEPTION_TYPE:RuntimeError\nMANGROVE_MC_EXCEPTION_TYPE:RuntimeError\n", None),
    (b"MANGROVE_MC_EXCEPTION_TYPE:RuntimeError\nMANGROVE_MC_EXCEPTION_TYPE:PlaywrightError\n", None),
    (b"MANGROVE_MC_EXCEPTION_TYPE:RuntimeError Cookie=marker-cookie\n", None),
    (b"MANGROVE_MC_EXCEPTION_TYPE:NotAllowedError\nMANGROVE_MC_EXCEPTION_TYPE:RuntimeError\n", None),
    (b"{\"message\":\"MANGROVE_MC_EXCEPTION_TYPE:RuntimeError\"}\n", None),
    (b"MANGROVE_MC_EXCEPTION_TYPE:RuntimeError\\n", None),
])
def test_isolated_marker_validation_never_changes_existing_diagnostics(crawler, caplog, payload, isolated_type):
    (crawler / "main.py").write_text("import sys\nsys.stderr.buffer.write(" + repr(payload) + ")\nraise SystemExit(1)\n", encoding="utf-8")
    caplog.set_level("WARNING", logger="src.collectors.social_media_collector")
    with user_overrides_context({"mc_cookie_xhs": "synthetic"}):
        result = asyncio.run(SocialMediaCollector().collect(TaskSpec(intent="读取", platforms=["小红书"], keywords=["规则"])))
    assert not result.success and not result.authentication
    assert result.coverage["failure"] == {"reason": "collection_failed", "next_action": "inspect_collection_service",
        "diagnostic_hint": {"exit_code": 1, "exception_type": None, "http_status": None, "platform_code": None,
            "stdout_sha256": hashlib.sha256(payload).hexdigest(), "isolated_exception_type": isolated_type,
            "isolated_cause_type": None}}
    assert result.coverage["operations"]["search"] == {"status": "unknown", "reason": "collection_failed",
        "next_action": "inspect_collection_service"}
    exposed = json.dumps(result.coverage) + result.message + "\n".join(record.getMessage() for record in caplog.records)
    assert "marker-cookie" not in exposed and "NotAllowedError" not in exposed


@pytest.mark.parametrize("statement,returncode", [("raise SystemExit(0)", 0), ("raise SystemExit(7)", 7),
    ("raise KeyboardInterrupt()", 3221225786 if sys.platform == "win32" else -2),
    ("import asyncio\nraise asyncio.CancelledError()", 1)])
def test_isolated_boundary_preserves_base_exception_exit(crawler, statement, returncode):
    (crawler / "main.py").write_text(statement + "\n", encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts/mediacrawler_isolated.py"
    completed = subprocess.run([sys.executable, str(script), "default"], cwd=crawler, input=b'"synthetic"',
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    assert completed.returncode == returncode
    assert b"MANGROVE_MC_EXCEPTION_TYPE:" not in completed.stderr
    assert b"MANGROVE_MC_CAUSE_TYPE:" not in completed.stderr


@pytest.mark.parametrize("failed_operation", ["write", "flush"])
def test_isolated_hint_io_failure_preserves_original_exception(crawler, failed_operation):
    (crawler / "main.py").write_text("raise ValueError('original-exception')\n", encoding="utf-8")
    script = Path(__file__).resolve().parents[1] / "scripts/mediacrawler_isolated.py"
    source = f"""
import runpy, sys
original_stderr = sys.stderr
class BrokenStream:
    def write(self, value):
        if {failed_operation!r} == 'write':
            raise OSError('synthetic-diagnostic-write-failure')
        return len(value)
    def flush(self):
        if {failed_operation!r} == 'flush':
            raise OSError('synthetic-diagnostic-flush-failure')
sys.stderr = BrokenStream()
sys.argv = [{str(script)!r}, 'default']
try:
    try:
        runpy.run_path({str(script)!r}, run_name='__main__')
    except ValueError as error:
        assert type(error) is ValueError and error.args == ('original-exception',)
    else:
        raise AssertionError('原异常必须继续传播')
finally:
    sys.stderr = original_stderr
print('ORIGINAL_EXCEPTION_PRESERVED')
"""
    completed = subprocess.run([sys.executable, "-c", source], cwd=crawler, input=b'"synthetic"',
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    assert completed.returncode == 0
    assert completed.stdout.strip() == b"ORIGINAL_EXCEPTION_PRESERVED"


def _write_cause_crawler(crawler, statement):
    package = crawler / "media_platform" / "xhs"
    package.mkdir(parents=True, exist_ok=True)
    (package.parent / "__init__.py").write_text("", encoding="utf-8")
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "exception.py").write_text(
        "from httpx import RequestError\nclass DataFetchError(RequestError): pass\n"
        "class IPBlockError(RequestError): pass\nclass NoteNotFoundError(RequestError): pass\n", encoding="utf-8")
    source = """
from tenacity import Future, RetryError
from httpx import ReadTimeout
from playwright._impl._errors import Error as PlaywrightError
from media_platform.xhs.exception import DataFetchError, IPBlockError, NoteNotFoundError
class FakeDataFetchError(Exception): pass
FakeDataFetchError.__name__ = 'DataFetchError'
def forbidden(*args, **kwargs):
    raise AssertionError('不得再次读取Future或执行重试')
def wrapped(cause):
    future = Future(1)
    future.set_exception(cause)
    future.exception = forbidden
    future.result = forbidden
    error = RetryError(future)
    error.reraise = forbidden
    error.__cause__ = cause
    return error
secret = 'Cookie=cause-cookie Token=cause-token https://example.test/?signature=cause-signature'
""" + statement + "\n"
    (crawler / "main.py").write_text(source, encoding="utf-8")


@pytest.mark.parametrize("factory,cause_type", [
    ("DataFetchError", "xhs_data_fetch"), ("IPBlockError", "xhs_ip_block"),
    ("NoteNotFoundError", "xhs_note_not_found"), ("ReadTimeout", "httpx_read_timeout"),
    ("RuntimeError", "builtin_runtime_error"), ("PlaywrightError", "playwright_error"),
    ("FakeDataFetchError", None),
])
def test_isolated_retry_cause_subprocess_is_safe_and_keeps_business_failure(crawler, factory, cause_type):
    _write_cause_crawler(crawler, f"raise wrapped(wrapped({factory}(secret)))")
    script = Path(__file__).resolve().parents[1] / "scripts/mediacrawler_isolated.py"
    completed = subprocess.run([sys.executable, str(script), "default"], cwd=crawler, input=b'"synthetic"',
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
    assert completed.returncode == 1
    hint = _safe_mc_failure_hint(completed.stderr, 1)
    assert hint["isolated_exception_type"] == "UnknownError"
    assert hint["isolated_cause_type"] == cause_type
    assert hint["http_status"] is None and hint["platform_code"] is None
    assert hint["stdout_sha256"] == hashlib.sha256(completed.stderr).hexdigest()
    markers = [line for line in completed.stderr.splitlines() if line.startswith(b"MANGROVE_MC_CAUSE_TYPE:")]
    assert markers == ([b"MANGROVE_MC_CAUSE_TYPE:" + cause_type.encode()] if cause_type else [])
    text = completed.stderr.decode("utf-8", "replace")
    original = "\n".join(line for line in text.splitlines() if not line.startswith("MANGROVE_MC_CAUSE_TYPE:"))
    assert _diagnose_mc_failure("xhs", text) == _diagnose_mc_failure("xhs", original)
    assert _operation_evidence(TaskSpec(intent="合成", keywords=["规则"]),
        CollectResult(False, "synthetic", coverage={"failure": _diagnose_mc_failure("xhs", text)}))["search"]["status"] == "unknown"
    for private in ("cause-cookie", "cause-token", "cause-signature", "https://example.test"):
        assert private not in json.dumps(hint) and all(private.encode() not in line for line in markers)


@pytest.mark.parametrize("case,cause_type", [
    ("depth4", "builtin_runtime_error"), ("depth5", None), ("cycle", None), ("root_cycle", None),
    ("conflict", None), ("unknown", None), ("context_only", None), ("missing", None),
])
def test_isolated_cause_chain_has_four_node_limit_and_never_guesses(case, cause_type):
    script = Path(__file__).resolve().parents[1] / "scripts/mediacrawler_isolated.py"
    namespace = runpy.run_path(str(script))
    root = Exception()
    if case.startswith("depth"):
        current = root
        for _ in range(int(case[-1])):
            current.__cause__ = RuntimeError()
            current = current.__cause__
    elif case == "cycle":
        root.__cause__ = RuntimeError()
        root.__cause__.__cause__ = root.__cause__
    elif case == "root_cycle":
        root.__cause__ = root
    elif case == "conflict":
        root.__cause__ = RuntimeError()
        root.__cause__.__cause__ = ValueError()
    elif case == "unknown":
        root.__cause__ = Exception()
        root.__cause__.__cause__ = RuntimeError()
    elif case == "context_only":
        root.__context__ = RuntimeError()
    assert namespace["_isolated_cause_type"](root) == cause_type


@pytest.mark.parametrize("payload,cause_type", [
    (b"MANGROVE_MC_CAUSE_TYPE:xhs_data_fetch\n", "xhs_data_fetch"),
    (b"MANGROVE_MC_CAUSE_TYPE:xhs_ip_block\n", "xhs_ip_block"),
    (b"MANGROVE_MC_CAUSE_TYPE:xhs_note_not_found\n", "xhs_note_not_found"),
    (b"MANGROVE_MC_CAUSE_TYPE:httpx_read_timeout\r\n", "httpx_read_timeout"),
    (b"opaque-output\n", None),
    (b"MANGROVE_MC_CAUSE_TYPE:xhs_data_fetch\nMANGROVE_MC_CAUSE_TYPE:xhs_data_fetch\n", None),
    (b"MANGROVE_MC_CAUSE_TYPE:xhs_data_fetch\nMANGROVE_MC_CAUSE_TYPE:httpx_read_timeout\n", None),
    (b"MANGROVE_MC_CAUSE_TYPE:xhs_data_fetch Cookie=cause-cookie\n", None),
    (b"MANGROVE_MC_CAUSE_TYPE:not_allowed\n", None),
    (b'{"message":"MANGROVE_MC_CAUSE_TYPE:xhs_data_fetch"}\n', None),
    (b"MANGROVE_MC_CAUSE_TYPE:xhs_data_fetch\\n", None),
])
def test_isolated_cause_marker_keeps_original_fields_actions_and_unknown(crawler, caplog, payload, cause_type):
    (crawler / "main.py").write_text("import sys\nsys.stderr.buffer.write(" + repr(payload) + ")\nraise SystemExit(1)\n", encoding="utf-8")
    caplog.set_level("WARNING", logger="src.collectors.social_media_collector")
    with user_overrides_context({"mc_cookie_xhs": "synthetic"}):
        result = asyncio.run(SocialMediaCollector().collect(TaskSpec(intent="合成", platforms=["小红书"], keywords=["规则"])))
    assert not result.success and not result.authentication
    assert result.coverage["failure"] == {"reason": "collection_failed", "next_action": "inspect_collection_service",
        "diagnostic_hint": {"exit_code": 1, "exception_type": None, "http_status": None, "platform_code": None,
            "stdout_sha256": hashlib.sha256(payload).hexdigest(), "isolated_exception_type": None,
            "isolated_cause_type": cause_type}}
    assert result.coverage["operations"]["search"] == {"status": "unknown", "reason": "collection_failed",
        "next_action": "inspect_collection_service"}
    assert "cause-cookie" not in json.dumps(result.coverage) + result.message + "\n".join(r.getMessage() for r in caplog.records)


def test_isolated_standard_parse_causes_remain_closed_and_do_not_change_classification():
    script = Path(__file__).resolve().parents[1] / "scripts/mediacrawler_isolated.py"
    reader = runpy.run_path(str(script))["_isolated_cause_type"]
    cases = [(AttributeError("synthetic"), "builtin_attribute_error"), (NameError("synthetic"), "builtin_name_error"),
        (IndexError("synthetic"), "builtin_index_error"), (AssertionError("synthetic"), "builtin_assertion_error"),
        (json.JSONDecodeError("synthetic", "{}", 0), "json_decode_error")]
    for cause, cause_type in cases:
        error = Exception()
        error.__cause__ = cause
        assert reader(error) == cause_type
        payload = ("MANGROVE_MC_CAUSE_TYPE:" + cause_type + "\n").encode("utf-8")
        assert _safe_mc_failure_hint(payload, 1) == {"exit_code": 1, "exception_type": None, "http_status": None,
            "platform_code": None, "stdout_sha256": hashlib.sha256(payload).hexdigest(),
            "isolated_exception_type": None, "isolated_cause_type": cause_type}
        assert _diagnose_mc_failure("xhs", payload.decode("utf-8")) == _diagnose_mc_failure("xhs", "")
