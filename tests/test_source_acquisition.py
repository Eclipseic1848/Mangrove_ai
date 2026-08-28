# -*- coding: utf-8 -*-
"""P1-01/02：精确匿名网页来源获取契约。"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading

import httpx
import pytest

from src.connectors.http_security import HttpSecurityGuard
from src.source_acquisition import (
    AcquisitionConflictError,
    AnonymousWebFetcher,
    SourceAcquisitionRepository,
    SourceAcquisitionRequest,
    SourceAcquisitionService,
    normalize_public_url,
)
from tests.database_migration_helpers import migrated_webui_database


PUBLIC_RESOLVER = lambda _host: ["93.184.216.34"]


def _service(
    database: Path,
    handler,
    *,
    max_bytes: int = 1024,
) -> SourceAcquisitionService:
    repository = SourceAcquisitionRepository(database)
    fetcher = AnonymousWebFetcher(
        security_guard=HttpSecurityGuard(resolver=PUBLIC_RESOLVER),
        transport=httpx.MockTransport(handler),
        max_bytes=max_bytes,
        timeout_seconds=0.2,
    )
    return SourceAcquisitionService(repository, fetcher)


def test_normalize_public_url_freezes_exact_current_page() -> None:
    assert normalize_public_url(" HTTPS://Example.COM:443/path?q=1#part ") == (
        "https://example.com/path?q=1"
    )
    assert normalize_public_url("http://example.com") == "http://example.com/"

    with pytest.raises(ValueError, match="http/https"):
        normalize_public_url("file:///etc/passwd")
    with pytest.raises(ValueError, match="用户名密码"):
        normalize_public_url("https://user:secret@example.com/")


@pytest.mark.asyncio
async def test_one_exact_html_page_creates_one_snapshot_and_artifact(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "source-success.db")
    visited: list[tuple[str, str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        visited.append((
            str(request.url),
            request.headers["host"],
            request.extensions.get("sni_hostname"),
        ))
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=(
                "<html><head><title>示例页</title></head>"
                "<body><main>正文<a href='/next'>不要跟随</a></main></body></html>"
            ).encode("utf-8"),
            request=request,
        )

    service = _service(database, handler)
    result = await service.acquire(
        owner_id="owner-a",
        idempotency_key="source-one",
        request=SourceAcquisitionRequest(
            url="https://Example.com/article#intro",
            purpose="读取公开产品说明，供当前任务分析",
        ),
    )

    assert visited == [(
        "https://93.184.216.34/article",
        "example.com",
        "example.com",
    )]
    assert result["status"] == "succeeded"
    assert result["allowed_scope"] == {
        "kind": "current_page",
        "normalized_url": "https://example.com/article",
        "site": "example.com",
        "page_limit": 1,
        "completeness": {
            "mode": "exploratory",
            "required_valid_pages": None,
        },
    }
    assert result["snapshot"]["valid_page_count"] == 1
    artifact = result["snapshot"]["artifacts"][0]
    assert artifact["request_url"] == "https://example.com/article"
    assert artifact["final_url"] == "https://example.com/article"
    assert artifact["media_type"] == "text/html"
    assert len(artifact["content_sha256"]) == 64
    assert artifact["title"] == "示例页"
    assert "正文" in artifact["text_preview"]

    with sqlite3.connect(database) as connection:
        for statement in (
            "UPDATE source_snapshots SET valid_page_count=0",
            "DELETE FROM source_snapshots",
            "UPDATE source_artifacts SET title='篡改'",
            "DELETE FROM source_artifacts",
        ):
            with pytest.raises(sqlite3.IntegrityError, match="不可"):
                connection.execute(statement)


@pytest.mark.asyncio
async def test_same_domain_redirect_allowed_but_cross_domain_redirect_fails_closed(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "source-redirect.db")

    def same_domain(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(
                302,
                headers={"location": "https://example.com/new"},
                request=request,
            )
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body>新地址正文</body></html>",
            request=request,
        )

    success = await _service(database, same_domain).acquire(
        owner_id="owner-a",
        idempotency_key="redirect-same",
        request=SourceAcquisitionRequest(
            url="https://example.com/old",
            purpose="读取公开页面",
        ),
    )
    assert success["status"] == "succeeded"
    assert success["snapshot"]["artifacts"][0]["final_url"] == (
        "https://example.com/new"
    )

    def cross_domain(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            302,
            headers={"location": "https://other.example/new"},
            request=request,
        )

    failure = await _service(database, cross_domain).acquire(
        owner_id="owner-a",
        idempotency_key="redirect-cross",
        request=SourceAcquisitionRequest(
            url="https://example.com/old",
            purpose="读取公开页面",
        ),
    )
    assert failure["status"] == "failed"
    assert failure["error_code"] == "scope_denied"
    assert failure["snapshot"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("key", "handler", "max_bytes", "error_code"),
    [
        (
            "non-html",
            lambda request: httpx.Response(
                200,
                headers={"content-type": "application/pdf"},
                content=b"%PDF",
                request=request,
            ),
            1024,
            "non_html",
        ),
        (
            "too-large",
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html", "content-length": "4096"},
                content=b"<html><body>large</body></html>",
                request=request,
            ),
            64,
            "content_too_large",
        ),
        (
            "refused",
            lambda request: httpx.Response(403, request=request),
            1024,
            "site_refused",
        ),
        (
            "parse",
            lambda request: httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=b"<html><body>   </body></html>",
                request=request,
            ),
            1024,
            "parse_failed",
        ),
    ],
)
async def test_failures_are_distinct_and_create_no_snapshot(
    tmp_path: Path,
    key: str,
    handler,
    max_bytes: int,
    error_code: str,
) -> None:
    database = migrated_webui_database(tmp_path / f"source-{key}.db")
    result = await _service(database, handler, max_bytes=max_bytes).acquire(
        owner_id="owner-a",
        idempotency_key=key,
        request=SourceAcquisitionRequest(
            url="https://example.com/page",
            purpose="读取公开页面",
        ),
    )

    assert result["status"] == "failed"
    assert result["error_code"] == error_code
    assert result["snapshot"] is None
    assert SourceAcquisitionRepository(database).count_snapshots("owner-a") == 0


@pytest.mark.asyncio
async def test_timeout_and_network_failures_are_distinct(tmp_path: Path) -> None:
    database = migrated_webui_database(tmp_path / "source-transport.db")

    def timeout_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    timeout = await _service(database, timeout_handler).acquire(
        owner_id="owner-a",
        idempotency_key="timeout",
        request=SourceAcquisitionRequest(
            url="https://example.com/slow",
            purpose="读取公开页面",
        ),
    )
    assert timeout["error_code"] == "timeout"

    def network_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("offline", request=request)

    network = await _service(database, network_handler).acquire(
        owner_id="owner-a",
        idempotency_key="network",
        request=SourceAcquisitionRequest(
            url="https://example.com/offline",
            purpose="读取公开页面",
        ),
    )
    assert network["error_code"] == "network_error"


@pytest.mark.asyncio
async def test_dns_failure_is_distinct_and_never_reaches_transport(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "source-dns.db")
    transport_called = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal transport_called
        transport_called = True
        return httpx.Response(200, request=request)

    service = SourceAcquisitionService(
        SourceAcquisitionRepository(database),
        AnonymousWebFetcher(
            security_guard=HttpSecurityGuard(resolver=lambda _host: []),
            transport=httpx.MockTransport(handler),
        ),
    )
    result = await service.acquire(
        owner_id="owner-a",
        idempotency_key="dns",
        request=SourceAcquisitionRequest(
            url="https://missing.example/page",
            purpose="读取公开页面",
        ),
    )

    assert result["error_code"] == "dns_error"
    assert result["snapshot"] is None
    assert transport_called is False


@pytest.mark.asyncio
async def test_cancelled_and_stale_attempts_become_terminal_failures(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "source-interrupted.db")
    repository = SourceAcquisitionRepository(database)
    request = SourceAcquisitionRequest(
        url="https://example.com/page",
        purpose="读取公开页面",
    )

    class CancelingFetcher:
        async def fetch(self, _request_url: str):
            raise asyncio.CancelledError

    canceled_service = SourceAcquisitionService(repository, CancelingFetcher())
    with pytest.raises(asyncio.CancelledError):
        await canceled_service.acquire(
            owner_id="owner-a",
            idempotency_key="canceled",
            request=request,
        )
    canceled = repository.get_by_idempotency_key("owner-a", "canceled")
    assert canceled is not None
    assert canceled["status"] == "failed"
    assert canceled["error_code"] == "network_error"

    stale, created = repository.claim_attempt(
        owner_id="owner-a",
        idempotency_key="stale",
        request=request,
    )
    assert created is True
    stale_service = SourceAcquisitionService(
        repository,
        CancelingFetcher(),
        stale_after_seconds=0,
    )
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE source_acquisition_attempts SET started_at=? "
            "WHERE attempt_id=?",
            (
                (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
                stale["attempt_id"],
            ),
        )
    restored = await stale_service.acquire(
        owner_id="owner-a",
        idempotency_key="stale",
        request=request,
    )
    assert restored["attempt_id"] == stale["attempt_id"]
    assert restored["status"] == "failed"
    assert restored["error_code"] == "network_error"


@pytest.mark.asyncio
async def test_explicit_resume_reclaims_stale_acquiring_attempt(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "source-resume-stale.db")
    repository = SourceAcquisitionRepository(database)
    request = SourceAcquisitionRequest(
        url="https://example.com/page",
        purpose="恢复结果未知的网页获取",
    )
    stale, created = repository.claim_attempt(
        owner_id="owner-a",
        idempotency_key="resume-stale",
        request=request,
    )
    assert created is True
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE source_acquisition_attempts SET started_at=? "
            "WHERE attempt_id=?",
            (
                (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
                stale["attempt_id"],
            ),
        )

    calls = 0

    def handler(http_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body>恢复后的页面</body></html>",
            request=http_request,
        )

    resumed = await _service(database, handler).acquire(
        owner_id="owner-a",
        idempotency_key="resume-stale",
        request=request,
        resume_unknown=True,
    )

    assert resumed["attempt_id"] == stale["attempt_id"]
    assert resumed["status"] == "succeeded"
    assert resumed["snapshot"] is not None
    assert calls == 1


@pytest.mark.asyncio
async def test_explicit_resume_accepts_pre_scope_upgrade_exact_page_hash(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "source-legacy-hash.db")
    repository = SourceAcquisitionRepository(database)
    request = SourceAcquisitionRequest(
        url="https://example.com/page",
        purpose="恢复升级前的精确页获取",
    )
    attempt, created = repository.claim_attempt(
        owner_id="owner-a",
        idempotency_key="legacy-hash",
        request=request,
    )
    assert created is True
    legacy_hash = hashlib.sha256(json.dumps(
        {
            "allowed_scope": {
                "kind": "current_page",
                "normalized_url": "https://example.com/page",
            },
            "purpose": "恢复升级前的精确页获取",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")).hexdigest()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE source_acquisition_attempts SET request_hash=?, started_at=? "
            "WHERE attempt_id=?",
            (
                legacy_hash,
                (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat(),
                attempt["attempt_id"],
            ),
        )

    def handler(http_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body>兼容恢复</body></html>",
            request=http_request,
        )

    resumed = await _service(database, handler).acquire(
        owner_id="owner-a",
        idempotency_key="legacy-hash",
        request=request,
        resume_unknown=True,
    )

    assert resumed["attempt_id"] == attempt["attempt_id"]
    assert resumed["status"] == "succeeded"


@pytest.mark.asyncio
async def test_idempotency_reuses_result_and_conflicts_on_different_request(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "source-idempotency.db")
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body>唯一抓取</body></html>",
            request=request,
        )

    service = _service(database, handler)
    request = SourceAcquisitionRequest(
        url="https://example.com/page",
        purpose="读取公开页面",
    )
    first, second = await asyncio.gather(
        service.acquire(
            owner_id="owner-a",
            idempotency_key="same-key",
            request=request,
        ),
        service.acquire(
            owner_id="owner-a",
            idempotency_key="same-key",
            request=request,
        ),
    )

    assert calls == 1
    assert first["attempt_id"] == second["attempt_id"]
    assert {first["status"], second["status"]} <= {"acquiring", "succeeded"}
    assert service.repository.get_attempt("owner-a", first["attempt_id"])

    with pytest.raises(AcquisitionConflictError):
        await service.acquire(
            owner_id="owner-a",
            idempotency_key="same-key",
            request=SourceAcquisitionRequest(
                url="https://example.com/other",
                purpose="读取公开页面",
            ),
        )


def test_repository_claim_is_thread_safe_and_owner_isolated(tmp_path: Path) -> None:
    database = migrated_webui_database(tmp_path / "source-owner.db")
    repository = SourceAcquisitionRepository(database)
    request = SourceAcquisitionRequest(
        url="https://example.com/page",
        purpose="读取公开页面",
    )
    barrier = threading.Barrier(2)
    claims: list[tuple[str, bool]] = []

    def claim() -> None:
        barrier.wait()
        attempt, created = repository.claim_attempt(
            owner_id="owner-a",
            idempotency_key="thread-key",
            request=request,
        )
        claims.append((attempt["attempt_id"], created))

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert len({item[0] for item in claims}) == 1
    assert sorted(item[1] for item in claims) == [False, True]
    attempt_id = claims[0][0]
    assert repository.get_attempt("owner-b", attempt_id) is None
    assert repository.cancel_attempt("owner-b", attempt_id) is None
    canceled = repository.cancel_attempt("owner-a", attempt_id)
    assert canceled is not None
    assert canceled["status"] == "canceled"


@pytest.mark.asyncio
async def test_same_site_batch_freezes_pages_failures_and_scope_boundary(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "source-same-site.db")
    visited: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        visited.append(path)
        pages = {
            "/": (
                "首页<a href='/a'>A</a><a href='/bad'>坏页</a>"
                "<a href='https://other.example/private'>站外</a>"
            ),
            "/a": "A页<a href='/'>循环</a><a href='/c'>C</a>",
            "/c": "C页",
        }
        if path == "/bad":
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=f"<html><body>{pages[path]}</body></html>",
            request=request,
        )

    service = _service(database, handler)
    result = await service.acquire(
        owner_id="owner-a",
        idempotency_key="same-site-batch",
        request=SourceAcquisitionRequest(
            url="https://example.com/",
            purpose="读取同站公开说明",
            scope_kind="same_site",
            page_limit=4,
        ),
    )

    snapshot = result["snapshot"]
    assert visited == ["/", "/a", "/bad", "/c"]
    assert snapshot["valid_page_count"] == 3
    assert snapshot["failed_page_count"] == 2
    assert snapshot["coverage"]["status"] == "coverage_unknown"
    assert {item["error_code"] for item in snapshot["failures"]} == {
        "scope_denied",
        "site_refused",
    }
    assert any(
        item["request_url"] == "https://other.example/private"
        for item in snapshot["failures"]
    )
    assert service.repository.count_snapshots("owner-a") == 1


@pytest.mark.asyncio
async def test_hard_completeness_distinguishes_insufficient_from_zero_valid(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "source-hard-min.db")

    def partial(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<html><body>首页<a href='/missing'>下一页</a></body></html>",
                request=request,
            )
        return httpx.Response(404, request=request)

    service = _service(database, partial)
    insufficient = await service.acquire(
        owner_id="owner-a",
        idempotency_key="hard-insufficient",
        request=SourceAcquisitionRequest(
            url="https://example.com/",
            purpose="至少需要两页",
            scope_kind="same_site",
            page_limit=2,
            completeness_mode="hard_min_pages",
            required_valid_pages=2,
        ),
    )
    assert insufficient["status"] == "succeeded"
    assert insufficient["snapshot"]["valid_page_count"] == 1
    assert insufficient["snapshot"]["coverage"]["status"] == "hard_insufficient"

    scope_complete = await service.acquire(
        owner_id="owner-a",
        idempotency_key="hard-scope-complete",
        request=SourceAcquisitionRequest(
            url="https://example.com/",
            purpose="授权范围必须全部成功",
            scope_kind="same_site",
            page_limit=2,
            completeness_mode="hard_scope_complete",
        ),
    )
    assert scope_complete["snapshot"]["coverage"]["status"] == "hard_insufficient"

    def all_failed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, request=request)

    zero_service = _service(database, all_failed)
    zero = await zero_service.acquire(
        owner_id="owner-a",
        idempotency_key="zero-valid",
        request=SourceAcquisitionRequest(
            url="https://example.com/none",
            purpose="验证零有效页",
            scope_kind="same_site",
            page_limit=2,
        ),
    )
    assert zero["status"] == "failed"
    assert zero["snapshot"] is None
    assert zero["error_code"] == "site_refused"


@pytest.mark.asyncio
async def test_cross_site_redirect_records_final_url_without_fetching_target(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "source-cross-redirect.db")
    visited: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        visited.append(request.headers["host"] + request.url.path)
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<html><body>首页<a href='/jump'>跳转</a></body></html>",
                request=request,
            )
        return httpx.Response(
            302,
            headers={"location": "https://other.example/final"},
            request=request,
        )

    service = _service(database, handler)
    result = await service.acquire(
        owner_id="owner-a",
        idempotency_key="cross-redirect",
        request=SourceAcquisitionRequest(
            url="https://example.com/",
            purpose="验证跳转边界",
            scope_kind="same_site",
            page_limit=2,
        ),
    )

    assert visited == ["example.com/", "example.com/jump"]
    failure = result["snapshot"]["failures"][0]
    assert failure["error_code"] == "scope_denied"
    assert failure["final_url"] == "https://other.example/final"


@pytest.mark.asyncio
async def test_requested_failure_records_same_origin_redirect_final_url(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "source-failure-final-url.db")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"content-type": "text/html"},
                text="<html><body>首页<a href='/old'>旧页</a></body></html>",
                request=request,
            )
        if request.url.path == "/old":
            return httpx.Response(
                302,
                headers={"location": "/missing"},
                request=request,
            )
        return httpx.Response(404, request=request)

    result = await _service(database, handler).acquire(
        owner_id="owner-a",
        idempotency_key="failure-final-url",
        request=SourceAcquisitionRequest(
            url="https://example.com/",
            purpose="记录失败最终地址",
            scope_kind="same_site",
            page_limit=2,
        ),
    )

    failure = result["snapshot"]["failures"][0]
    assert failure["request_url"] == "https://example.com/old"
    assert failure["final_url"] == "https://example.com/missing"


@pytest.mark.asyncio
async def test_high_cardinality_links_are_bounded_and_not_counted_as_requests(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "source-link-bound.db")
    links = "".join(
        f"<a href='https://other.example/{index}'>x</a>"
        for index in range(1000)
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=f"<html><body>正文{links}</body></html>",
            request=request,
        )

    service = _service(database, handler, max_bytes=200_000)
    result = await service.acquire(
        owner_id="owner-a",
        idempotency_key="bounded-links",
        request=SourceAcquisitionRequest(
            url="https://example.com/",
            purpose="验证高基数链接边界",
            scope_kind="same_site",
            page_limit=1,
        ),
    )

    snapshot = result["snapshot"]
    assert snapshot["coverage"]["attempted_page_count"] == 1
    assert snapshot["coverage"]["scope_denied_count"] == 500
    assert snapshot["coverage"]["failure_sample_count"] == 100
    assert snapshot["coverage"]["truncated_discovery_count"] == 500
    assert len(snapshot["failures"]) == 100
    assert snapshot["coverage"]["status"] == "coverage_unknown"


@pytest.mark.asyncio
async def test_outside_links_do_not_fail_hard_authorized_scope_completeness(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "source-outside-links.db")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=(
                "<html><body>授权站点正文"
                "<a href='https://outside.example/page'>站外参考</a>"
                "</body></html>"
            ),
            request=request,
        )

    result = await _service(database, handler).acquire(
        owner_id="owner-a",
        idempotency_key="outside-links",
        request=SourceAcquisitionRequest(
            url="https://example.com/",
            purpose="完整读取授权站内范围",
            scope_kind="same_site",
            page_limit=2,
            completeness_mode="hard_scope_complete",
        ),
    )

    snapshot = result["snapshot"]
    assert snapshot["failed_page_count"] == 1
    assert snapshot["coverage"]["scope_denied_count"] == 1
    assert snapshot["coverage"]["failed_request_count"] == 0
    assert snapshot["coverage"]["status"] == "scope_complete"


@pytest.mark.asyncio
async def test_replay_does_not_expire_batch_within_enforced_deadline(
    tmp_path: Path,
) -> None:
    database = migrated_webui_database(tmp_path / "source-batch-lease.db")
    service = _service(
        database,
        lambda request: httpx.Response(500, request=request),
    )
    service.stale_after_seconds = 0
    request = SourceAcquisitionRequest(
        url="https://example.com/",
        purpose="验证批次恢复门槛",
        scope_kind="same_site",
        page_limit=2,
    )
    attempt, created = service.repository.claim_attempt(
        owner_id="owner-a",
        idempotency_key="batch-lease",
        request=request,
    )
    assert created
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE source_acquisition_attempts SET started_at=? "
            "WHERE attempt_id=?",
            (
                (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat(),
                attempt["attempt_id"],
            ),
        )

    replay = await service.acquire(
        owner_id="owner-a",
        idempotency_key="batch-lease",
        request=request,
    )

    assert replay["status"] == "acquiring"
