"""精确匿名网页的获取、冻结与 Owner 隔离。

本模块只负责来源事实，不创建 TaskRevision、Run 或 Delivery。路由、Runtime 和
旧 collector 都不能绕过这里的范围、幂等与持久化边界。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit
import uuid

from bs4 import BeautifulSoup
import httpx

from src.connectors.http_security import HttpSecurityGuard, SsrfError
from src.database_migrations import DatabaseTarget, inspect_database
from src.model_connections.pinned_transport import PinnedAsyncHTTPTransport


_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_HTML_MEDIA_TYPES = frozenset({"text/html", "application/xhtml+xml"})
_MAX_DISCOVERED_LINKS_PER_PAGE = 500
_MAX_SCOPE_FAILURE_SAMPLES = 100


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_public_url(value: str) -> str:
    """把用户输入冻结为不含凭证和片段的精确 HTTP(S) URL。"""

    raw = value.strip()
    try:
        parts = urlsplit(raw)
        port = parts.port
    except ValueError as exc:
        raise ValueError("网址端口无效") from exc
    scheme = parts.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ValueError("网址必须使用 http/https")
    if parts.username or parts.password:
        raise ValueError("网址不能包含用户名密码")
    host = parts.hostname
    if not host:
        raise ValueError("网址缺少域名")
    host = host.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = (scheme == "http" and port in {None, 80}) or (
        scheme == "https" and port in {None, 443}
    )
    netloc = host if default_port else f"{host}:{port}"
    path = parts.path or "/"
    return urlunsplit((scheme, netloc, path, parts.query, ""))


@dataclass(frozen=True)
class SourceAcquisitionRequest:
    """冻结匿名网页的访问范围和来源完整性要求。"""

    url: str
    purpose: str
    scope_kind: str = "current_page"
    page_limit: int = 1
    completeness_mode: str = "exploratory"
    required_valid_pages: int | None = None
    request_context: str = ""

    def normalized(self) -> "SourceAcquisitionRequest":
        purpose = self.purpose.strip()
        if not purpose:
            raise ValueError("来源用途不能为空")
        if len(purpose) > 500:
            raise ValueError("来源用途不能超过 500 个字符")
        if self.scope_kind not in {"current_page", "same_site"}:
            raise ValueError("来源范围必须是当前页或同站有限扩展")
        page_limit = 1 if self.scope_kind == "current_page" else self.page_limit
        if page_limit < 1 or page_limit > 50:
            raise ValueError("同站页面上限必须为 1 至 50")
        if self.completeness_mode not in {
            "exploratory",
            "hard_min_pages",
            "hard_scope_complete",
        }:
            raise ValueError("来源完整性要求无效")
        required = self.required_valid_pages
        if self.completeness_mode == "hard_min_pages":
            if required is None or required < 1 or required > page_limit:
                raise ValueError("硬性有效页数必须在 1 至页面上限之间")
        elif required is not None:
            raise ValueError("探索性上限不能同时声明硬性有效页数")
        request_context = self.request_context.strip()
        if len(request_context) > 500:
            raise ValueError("来源请求上下文不能超过 500 个字符")
        return SourceAcquisitionRequest(
            url=normalize_public_url(self.url),
            purpose=purpose,
            scope_kind=self.scope_kind,
            page_limit=page_limit,
            completeness_mode=self.completeness_mode,
            required_valid_pages=required,
            request_context=request_context,
        )

    def request_hash(self) -> str:
        normalized = self.normalized()
        payload = {
            "allowed_scope": {
                "kind": normalized.scope_kind,
                "normalized_url": normalized.url,
                "site": urlsplit(normalized.url).netloc,
                "page_limit": normalized.page_limit,
            },
            "completeness": {
                "mode": normalized.completeness_mode,
                "required_valid_pages": normalized.required_valid_pages,
            },
            "purpose": normalized.purpose,
        }
        # 仅刷新等复合操作写入内部上下文；普通来源请求保持既有哈希兼容。
        if normalized.request_context:
            payload["request_context"] = normalized.request_context
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def legacy_exact_page_hash(self) -> str | None:
        """返回 #90 精确页请求的旧哈希，仅用于升级后重放兼容。"""

        normalized = self.normalized()
        if (
            normalized.scope_kind != "current_page"
            or normalized.page_limit != 1
            or normalized.completeness_mode != "exploratory"
            or normalized.required_valid_pages is not None
            or normalized.request_context
        ):
            return None
        encoded = json.dumps(
            {
                "allowed_scope": {
                    "kind": "current_page",
                    "normalized_url": normalized.url,
                },
                "purpose": normalized.purpose,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class AcquisitionConflictError(RuntimeError):
    """同一 Owner/幂等键绑定了不同请求。"""


@dataclass(frozen=True)
class _FetchedPage:
    request_url: str
    final_url: str
    read_at: str
    media_type: str
    content: bytes
    content_sha256: str
    title: str
    text_preview: str
    discovered_links: tuple[str, ...] = ()
    truncated_discovery_count: int = 0


@dataclass(frozen=True)
class _PageFailure:
    request_url: str
    final_url: str | None
    error_code: str
    error_message: str
    failed_at: str


@dataclass(frozen=True)
class _FetchBatch:
    pages: tuple[_FetchedPage, ...]
    failures: tuple[_PageFailure, ...]
    limit_reached: bool
    attempted_page_count: int
    failed_request_count: int
    scope_denied_count: int
    truncated_discovery_count: int


class _FetchFailure(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        final_url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.final_url = final_url


class SourceAcquisitionRepository:
    """只访问显式迁移后的来源表；构造器绝不代建 Schema。"""

    def __init__(self, database: str | Path) -> None:
        self.database = Path(database)
        inspect_database(
            DatabaseTarget(profile="webui", path=self.database)
        ).require_current()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    @staticmethod
    def _scope(request: SourceAcquisitionRequest) -> dict[str, Any]:
        return {
            "kind": request.scope_kind,
            "normalized_url": request.url,
            "site": urlsplit(request.url).netloc,
            "page_limit": request.page_limit,
            "completeness": {
                "mode": request.completeness_mode,
                "required_valid_pages": request.required_valid_pages,
            },
        }

    @staticmethod
    def _row(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        result["allowed_scope"] = json.loads(result.pop("allowed_scope_json"))
        result.pop("request_hash", None)
        return result

    def claim_attempt(
        self,
        *,
        owner_id: str,
        idempotency_key: str,
        request: SourceAcquisitionRequest,
    ) -> tuple[dict[str, Any], bool]:
        key = idempotency_key.strip()
        if not key or len(key) > 200:
            raise ValueError("Idempotency-Key 长度必须为 1 至 200 个字符")
        normalized = request.normalized()
        request_hash = normalized.request_hash()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM source_acquisition_attempts "
                "WHERE owner_id=? AND idempotency_key=?",
                (owner_id, key),
            ).fetchone()
            if existing is not None:
                compatible_hashes = {request_hash}
                legacy_hash = normalized.legacy_exact_page_hash()
                if legacy_hash:
                    compatible_hashes.add(legacy_hash)
                if str(existing["request_hash"]) not in compatible_hashes:
                    raise AcquisitionConflictError(
                        "该 Idempotency-Key 已绑定另一份来源请求"
                    )
                connection.commit()
                return self._row(existing) or {}, False
            attempt_id = f"source_attempt_{uuid.uuid4().hex}"
            started_at = _now()
            connection.execute(
                "INSERT INTO source_acquisition_attempts "
                "(attempt_id, owner_id, idempotency_key, request_hash, "
                "request_url, normalized_url, allowed_scope_json, purpose, "
                "status, started_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, "
                "'acquiring', ?)",
                (
                    attempt_id,
                    owner_id,
                    key,
                    request_hash,
                    request.url.strip(),
                    normalized.url,
                    json.dumps(self._scope(normalized), ensure_ascii=False),
                    normalized.purpose,
                    started_at,
                ),
            )
            connection.commit()
            saved = self.get_attempt(owner_id, attempt_id)
            if saved is None:  # pragma: no cover - 同事务插入后不可达
                raise RuntimeError("来源获取记录未能持久化")
            return saved, True
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def get_attempt(
        self,
        owner_id: str,
        attempt_id: str,
        *,
        include_snapshot: bool = True,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_acquisition_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (owner_id, attempt_id),
            ).fetchone()
        attempt = self._row(row)
        if attempt is None:
            return None
        attempt["snapshot"] = (
            self.get_snapshot(owner_id, str(attempt["snapshot_id"]))
            if include_snapshot and attempt.get("snapshot_id")
            else None
        )
        return attempt

    def get_by_idempotency_key(
        self,
        owner_id: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT attempt_id FROM source_acquisition_attempts "
                "WHERE owner_id=? AND idempotency_key=?",
                (owner_id, idempotency_key),
            ).fetchone()
        return (
            self.get_attempt(owner_id, str(row["attempt_id"]))
            if row is not None
            else None
        )

    def complete_success(
        self,
        owner_id: str,
        attempt_id: str,
        page: _FetchedPage,
    ) -> dict[str, Any]:
        return self.complete_batch(
            owner_id,
            attempt_id,
            pages=(page,),
            failures=(),
            limit_reached=False,
            attempted_page_count=1,
            failed_request_count=0,
            scope_denied_count=0,
            truncated_discovery_count=0,
        )

    def complete_batch(
        self,
        owner_id: str,
        attempt_id: str,
        *,
        pages: tuple[_FetchedPage, ...],
        failures: tuple[_PageFailure, ...],
        limit_reached: bool,
        attempted_page_count: int,
        failed_request_count: int,
        scope_denied_count: int,
        truncated_discovery_count: int,
    ) -> dict[str, Any]:
        if not pages:
            raise ValueError("零有效页不能形成 SourceSnapshot")
        snapshot_id = f"source_snapshot_{uuid.uuid4().hex}"
        finished_at = _now()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, allowed_scope_json FROM source_acquisition_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (owner_id, attempt_id),
            ).fetchone()
            if row is None:
                raise RuntimeError("来源获取记录不存在")
            if row["status"] != "acquiring":
                connection.commit()
                saved = self.get_attempt(owner_id, attempt_id)
                if saved is None:
                    raise RuntimeError("来源获取记录不存在")
                return saved
            connection.execute(
                "INSERT INTO source_snapshots "
                "(snapshot_id, owner_id, attempt_id, allowed_scope_json, "
                "valid_page_count, failed_page_count, coverage_json, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    snapshot_id,
                    owner_id,
                    attempt_id,
                    row["allowed_scope_json"],
                    len(pages),
                    failed_request_count + scope_denied_count,
                    json.dumps(
                        {
                            "limit_reached": limit_reached,
                            "attempted_page_count": attempted_page_count,
                            "failed_request_count": failed_request_count,
                            "scope_denied_count": scope_denied_count,
                            "failure_sample_count": len(failures),
                            "truncated_discovery_count": truncated_discovery_count,
                        },
                        ensure_ascii=False,
                    ),
                    finished_at,
                ),
            )
            for page in pages:
                connection.execute(
                    "INSERT INTO source_artifacts "
                    "(artifact_id, owner_id, snapshot_id, request_url, final_url, "
                    "read_at, content_sha256, media_type, size_bytes, title, "
                    "text_preview, content_blob) VALUES (?, ?, ?, ?, ?, ?, ?, ?, "
                    "?, ?, ?, ?)",
                    (
                        f"source_artifact_{uuid.uuid4().hex}",
                        owner_id,
                        snapshot_id,
                        page.request_url,
                        page.final_url,
                        page.read_at,
                        page.content_sha256,
                        page.media_type,
                        len(page.content),
                        page.title,
                        page.text_preview,
                        page.content,
                    ),
                )
            for failure in failures:
                connection.execute(
                    "INSERT INTO source_page_failures "
                    "(failure_id, owner_id, attempt_id, snapshot_id, request_url, "
                    "final_url, error_code, error_message, failed_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"source_failure_{uuid.uuid4().hex}",
                        owner_id,
                        attempt_id,
                        snapshot_id,
                        failure.request_url,
                        failure.final_url,
                        failure.error_code,
                        failure.error_message[:500],
                        failure.failed_at,
                    ),
                )
            connection.execute(
                "UPDATE source_acquisition_attempts SET status='succeeded', "
                "finished_at=?, snapshot_id=?, error_code=NULL, "
                "error_message=NULL WHERE owner_id=? AND attempt_id=?",
                (finished_at, snapshot_id, owner_id, attempt_id),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()
        saved = self.get_attempt(owner_id, attempt_id)
        if saved is None:  # pragma: no cover
            raise RuntimeError("来源获取结果未能持久化")
        return saved

    def complete_failure(
        self,
        owner_id: str,
        attempt_id: str,
        *,
        error_code: str,
        error_message: str,
    ) -> dict[str, Any]:
        with self._connect() as connection:
            connection.execute(
                "UPDATE source_acquisition_attempts SET status='failed', "
                "finished_at=?, error_code=?, error_message=? "
                "WHERE owner_id=? AND attempt_id=? AND status='acquiring'",
                (_now(), error_code, error_message[:500], owner_id, attempt_id),
            )
        saved = self.get_attempt(owner_id, attempt_id)
        if saved is None:
            raise RuntimeError("来源获取记录不存在")
        return saved

    def cancel_attempt(
        self,
        owner_id: str,
        attempt_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM source_acquisition_attempts "
                "WHERE owner_id=? AND attempt_id=?",
                (owner_id, attempt_id),
            ).fetchone()
            if exists is None:
                return None
            connection.execute(
                "UPDATE source_acquisition_attempts SET status='canceled', "
                "finished_at=? WHERE owner_id=? AND attempt_id=? "
                "AND status='acquiring'",
                (_now(), owner_id, attempt_id),
            )
        return self.get_attempt(owner_id, attempt_id)

    def get_snapshot(
        self,
        owner_id: str,
        snapshot_id: str,
    ) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM source_snapshots WHERE owner_id=? AND snapshot_id=?",
                (owner_id, snapshot_id),
            ).fetchone()
            if row is None:
                return None
            artifacts = connection.execute(
                "SELECT artifact_id, request_url, final_url, read_at, "
                "content_sha256, media_type, size_bytes, title, text_preview "
                "FROM source_artifacts WHERE owner_id=? AND snapshot_id=? "
                "ORDER BY read_at, artifact_id",
                (owner_id, snapshot_id),
            ).fetchall()
            failures = connection.execute(
                "SELECT failure_id, request_url, final_url, error_code, "
                "error_message, failed_at FROM source_page_failures "
                "WHERE owner_id=? AND snapshot_id=? ORDER BY failed_at, failure_id",
                (owner_id, snapshot_id),
            ).fetchall()
        result = dict(row)
        result["allowed_scope"] = json.loads(result.pop("allowed_scope_json"))
        coverage = json.loads(result.pop("coverage_json") or "{}")
        required = result["allowed_scope"].get("completeness", {}).get(
            "required_valid_pages"
        )
        mode = result["allowed_scope"].get("completeness", {}).get(
            "mode", "exploratory"
        )
        failed_request_count = coverage.get("failed_request_count")
        if failed_request_count is None:
            # 兼容未保存分类计数的旧快照；新快照不能把未访问的站外链接算作站内失败。
            failed_request_count = max(
                0,
                int(result["failed_page_count"])
                - int(coverage.get("scope_denied_count") or 0),
            )
        has_coverage_gap = bool(
            failed_request_count
            or coverage.get("limit_reached")
            or coverage.get("truncated_discovery_count")
        )
        if mode == "hard_min_pages" and result["valid_page_count"] < int(required or 0):
            completeness_status = "hard_insufficient"
        elif mode == "hard_scope_complete" and has_coverage_gap:
            completeness_status = "hard_insufficient"
        elif has_coverage_gap:
            completeness_status = "coverage_unknown"
        else:
            completeness_status = "scope_complete"
        result["coverage"] = {
            **coverage,
            "status": completeness_status,
            "required_valid_pages": required,
        }
        result["artifacts"] = [dict(item) for item in artifacts]
        result["failures"] = [dict(item) for item in failures]
        return result

    def get_artifact(
        self,
        owner_id: str,
        artifact_id: str,
        *,
        include_content: bool = False,
    ) -> dict[str, Any] | None:
        columns = "*" if include_content else (
            "artifact_id, owner_id, snapshot_id, request_url, final_url, read_at, "
            "content_sha256, media_type, size_bytes, title, text_preview"
        )
        with self._connect() as connection:
            row = connection.execute(
                f"SELECT {columns} FROM source_artifacts "
                "WHERE owner_id=? AND artifact_id=?",
                (owner_id, artifact_id),
            ).fetchone()
        return dict(row) if row is not None else None

    def count_snapshots(self, owner_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM source_snapshots WHERE owner_id=?",
                (owner_id,),
            ).fetchone()
        return int(row[0])

    def fail_if_stale(
        self,
        owner_id: str,
        attempt_id: str,
        *,
        stale_after_seconds: float,
    ) -> bool:
        """把超过执行租期的孤儿 Attempt 原子收口为失败。"""

        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(seconds=stale_after_seconds)
        ).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE source_acquisition_attempts SET status='failed', "
                "finished_at=?, error_code='network_error', "
                "error_message='上次网页获取已中断，未形成来源快照' "
                "WHERE owner_id=? AND attempt_id=? AND status='acquiring' "
                "AND started_at<=?",
                (_now(), owner_id, attempt_id, cutoff),
            )
        return cursor.rowcount == 1

    def reclaim_if_stale(
        self,
        owner_id: str,
        attempt_id: str,
        *,
        stale_after_seconds: float,
    ) -> bool:
        """仅在用户显式恢复时原子接管超过租期的结果未知 Attempt。"""

        cutoff = (
            datetime.now(timezone.utc)
            - timedelta(seconds=stale_after_seconds)
        ).isoformat()
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE source_acquisition_attempts SET started_at=?, "
                "finished_at=NULL, error_code=NULL, error_message=NULL "
                "WHERE owner_id=? AND attempt_id=? AND status='acquiring' "
                "AND started_at<=?",
                (_now(), owner_id, attempt_id, cutoff),
            )
        return cursor.rowcount == 1


class AnonymousWebFetcher:
    """读取精确页或用户授权的同站有限页面批次。"""

    def __init__(
        self,
        *,
        security_guard: HttpSecurityGuard | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        max_bytes: int = 5 * 1024 * 1024,
        timeout_seconds: float = 20.0,
        max_redirects: int = 5,
    ) -> None:
        self._guard = security_guard or HttpSecurityGuard()
        self._transport = transport
        self._max_bytes = max_bytes
        self._timeout = timeout_seconds
        self._max_redirects = max_redirects

    def _validate(self, url: str):
        try:
            return self._guard.validate(url)
        except SsrfError as exc:
            code = "dns_error" if "DNS" in str(exc) else "scope_denied"
            raise _FetchFailure(code, str(exc)) from exc

    async def fetch(self, request_url: str) -> _FetchedPage:
        try:
            return await asyncio.wait_for(
                self._fetch_within_scope(request_url),
                timeout=self._timeout,
            )
        except TimeoutError as exc:
            raise _FetchFailure(
                "timeout",
                "网页读取超时",
                final_url=normalize_public_url(request_url),
            ) from exc

    async def fetch_batch(
        self,
        request_url: str,
        *,
        page_limit: int,
    ) -> _FetchBatch:
        root = normalize_public_url(request_url)
        root_parts = urlsplit(root)
        allowed_origin = (root_parts.scheme, root_parts.netloc)
        queue = [root]
        queued = {root}
        queue_capacity = max(20, min(200, page_limit * 4))
        visited: set[str] = set()
        pages: list[_FetchedPage] = []
        failures: list[_PageFailure] = []
        failed_request_count = 0
        scope_denied_count = 0
        truncated_discovery_count = 0
        while queue and len(visited) < page_limit:
            url = queue.pop(0)
            visited.add(url)
            try:
                page = await self.fetch(url)
            except _FetchFailure as exc:
                failed_request_count += 1
                failures.append(_PageFailure(
                    request_url=url,
                    final_url=exc.final_url,
                    error_code=exc.code,
                    error_message=str(exc),
                    failed_at=_now(),
                ))
                continue
            pages.append(page)
            truncated_discovery_count += page.truncated_discovery_count
            for link in page.discovered_links:
                link_parts = urlsplit(link)
                if (link_parts.scheme, link_parts.netloc) != allowed_origin:
                    scope_denied_count += 1
                    if len(failures) < _MAX_SCOPE_FAILURE_SAMPLES:
                        failures.append(_PageFailure(
                            request_url=link,
                            final_url=None,
                            error_code="scope_denied",
                            error_message="发现链接超出已授权站点，未访问",
                            failed_at=_now(),
                        ))
                    continue
                if link not in queued:
                    if len(queue) >= queue_capacity:
                        truncated_discovery_count += 1
                    else:
                        queued.add(link)
                        queue.append(link)
        return _FetchBatch(
            pages=tuple(pages),
            failures=tuple(failures),
            limit_reached=bool(queue),
            attempted_page_count=len(visited),
            failed_request_count=failed_request_count,
            scope_denied_count=scope_denied_count,
            truncated_discovery_count=truncated_discovery_count,
        )

    def batch_deadline_seconds(self, page_limit: int) -> float:
        """冻结批次总时限；恢复门槛必须晚于这个上界。"""

        return self._timeout * max(1, page_limit) + 5.0

    async def _fetch_within_scope(self, request_url: str) -> _FetchedPage:
        current_url = normalize_public_url(request_url)
        original_parts = urlsplit(current_url)
        original_origin = (original_parts.scheme, original_parts.netloc)
        kwargs: dict[str, Any] = {
            "follow_redirects": False,
            "timeout": self._timeout,
            "trust_env": False,
            "headers": {
                "accept": "text/html,application/xhtml+xml",
                "user-agent": "MangroveSourceReader/1.0",
            },
        }
        try:
            for redirect_count in range(self._max_redirects + 1):
                target = self._validate(current_url)
                transport = PinnedAsyncHTTPTransport(
                    target=target,
                    transport=self._transport,
                )
                async with httpx.AsyncClient(
                    **kwargs,
                    transport=transport,
                ) as client:
                    # Transport 连接已校验 IP，并保留逻辑 URL 的 Host 与 TLS SNI。
                    async with client.stream("GET", current_url) as response:
                        if response.status_code in _REDIRECT_STATUSES:
                            location = response.headers.get("location")
                            if not location:
                                raise _FetchFailure(
                                    "site_refused",
                                    "站点返回了无目标的跳转",
                                    final_url=current_url,
                                )
                            if redirect_count >= self._max_redirects:
                                raise _FetchFailure(
                                    "site_refused",
                                    "站点跳转次数超过上限",
                                    final_url=current_url,
                                )
                            redirected = normalize_public_url(
                                urljoin(current_url, location)
                            )
                            # 当前授权只覆盖输入页面及其同域规范化跳转。
                            redirect_parts = urlsplit(redirected)
                            if (
                                redirect_parts.scheme,
                                redirect_parts.netloc,
                            ) != original_origin:
                                raise _FetchFailure(
                                    "scope_denied",
                                    "跳转目标超出已授权站点",
                                    final_url=redirected,
                                )
                            current_url = redirected
                            continue
                        if response.status_code >= 400:
                            raise _FetchFailure(
                                "site_refused",
                                f"站点拒绝读取（HTTP {response.status_code}）",
                                final_url=current_url,
                            )
                        media_type = response.headers.get(
                            "content-type", ""
                        ).split(";", 1)[0].strip().lower()
                        if media_type not in _HTML_MEDIA_TYPES:
                            raise _FetchFailure(
                                "non_html",
                                f"页面不是 HTML（{media_type or '未知类型'}）",
                                final_url=current_url,
                            )
                        content_length = response.headers.get("content-length")
                        if content_length:
                            try:
                                if int(content_length) > self._max_bytes:
                                    raise _FetchFailure(
                                        "content_too_large",
                                        "页面大小超过读取上限",
                                        final_url=current_url,
                                    )
                            except ValueError:
                                pass
                        content = bytearray()
                        async for chunk in response.aiter_bytes():
                            content.extend(chunk)
                            if len(content) > self._max_bytes:
                                raise _FetchFailure(
                                    "content_too_large",
                                    "页面大小超过读取上限",
                                    final_url=current_url,
                                )
                        raw = bytes(content)
                        try:
                            soup = BeautifulSoup(raw, "html.parser")
                            for element in soup(["script", "style", "noscript"]):
                                element.decompose()
                            text = " ".join(soup.get_text(" ", strip=True).split())
                        except Exception as exc:  # BeautifulSoup 插件错误必须归类
                            raise _FetchFailure(
                                "parse_failed",
                                "HTML 解析失败",
                                final_url=current_url,
                            ) from exc
                        if not text:
                            raise _FetchFailure(
                                "parse_failed",
                                "页面没有可读取的正文",
                                final_url=current_url,
                            )
                        title = (
                            " ".join(soup.title.get_text(" ", strip=True).split())
                            if soup.title
                            else ""
                        )
                        links: list[str] = []
                        seen_links: set[str] = set()
                        truncated_discovery_count = 0
                        for anchor in soup.find_all("a", href=True):
                            if len(links) >= _MAX_DISCOVERED_LINKS_PER_PAGE:
                                truncated_discovery_count += 1
                                continue
                            try:
                                link = normalize_public_url(
                                    urljoin(current_url, str(anchor["href"]))
                                )
                            except ValueError:
                                continue
                            if link not in seen_links:
                                seen_links.add(link)
                                links.append(link)
                        return _FetchedPage(
                            request_url=normalize_public_url(request_url),
                            final_url=current_url,
                            read_at=_now(),
                            media_type=media_type,
                            content=raw,
                            content_sha256=hashlib.sha256(raw).hexdigest(),
                            title=title[:300],
                            text_preview=text[:4000],
                            discovered_links=tuple(links),
                            truncated_discovery_count=truncated_discovery_count,
                        )
            raise _FetchFailure(
                "site_refused",
                "站点跳转次数超过上限",
                final_url=current_url,
            )
        except _FetchFailure:
            raise
        except httpx.TimeoutException as exc:
            raise _FetchFailure(
                "timeout", "网页读取超时", final_url=current_url
            ) from exc
        except httpx.TransportError as exc:
            raise _FetchFailure(
                "network_error",
                "网页网络连接失败",
                final_url=current_url,
            ) from exc


class SourceAcquisitionService:
    """先持久化 Attempt，再执行一次可幂等获取。"""

    def __init__(
        self,
        repository: SourceAcquisitionRepository,
        fetcher: AnonymousWebFetcher,
        *,
        stale_after_seconds: float = 60.0,
    ) -> None:
        self.repository = repository
        self.fetcher = fetcher
        self.stale_after_seconds = stale_after_seconds

    def _batch_deadline_seconds(self, page_limit: int) -> float:
        deadline = getattr(self.fetcher, "batch_deadline_seconds", None)
        if callable(deadline):
            return float(deadline(page_limit))
        # 测试替身和兼容 Fetcher 没有批次接口时，沿用正式 Fetcher 的默认上界。
        return 20.0 * max(1, page_limit) + 5.0

    async def acquire(
        self,
        *,
        owner_id: str,
        idempotency_key: str,
        request: SourceAcquisitionRequest,
        resume_unknown: bool = False,
    ) -> dict[str, Any]:
        normalized = request.normalized()
        attempt, created = self.repository.claim_attempt(
            owner_id=owner_id,
            idempotency_key=idempotency_key,
            request=request,
        )
        if not created:
            if attempt.get("status") == "acquiring":
                stale_after_seconds = max(
                    self.stale_after_seconds,
                    self._batch_deadline_seconds(normalized.page_limit) + 30.0,
                )
                if resume_unknown:
                    # 普通重放只观察原请求；只有用户显式恢复才能在租期后重新执行。
                    created = self.repository.reclaim_if_stale(
                        owner_id,
                        str(attempt["attempt_id"]),
                        stale_after_seconds=stale_after_seconds,
                    )
                else:
                    self.repository.fail_if_stale(
                        owner_id,
                        str(attempt["attempt_id"]),
                        stale_after_seconds=stale_after_seconds,
                    )
            if not created:
                return self.repository.get_attempt(
                    owner_id, str(attempt["attempt_id"])
                ) or attempt
        try:
            if normalized.scope_kind == "same_site":
                batch = await asyncio.wait_for(
                    self.fetcher.fetch_batch(
                        str(attempt["normalized_url"]),
                        page_limit=normalized.page_limit,
                    ),
                    timeout=self._batch_deadline_seconds(normalized.page_limit),
                )
                if not batch.pages:
                    first = batch.failures[0] if batch.failures else None
                    return self.repository.complete_failure(
                        owner_id,
                        str(attempt["attempt_id"]),
                        error_code=(first.error_code if first else "network_error"),
                        error_message=(
                            first.error_message
                            if first
                            else "同站范围没有形成有效页面"
                        ),
                    )
                return self.repository.complete_batch(
                    owner_id,
                    str(attempt["attempt_id"]),
                    pages=batch.pages,
                    failures=batch.failures,
                    limit_reached=batch.limit_reached,
                    attempted_page_count=batch.attempted_page_count,
                    failed_request_count=batch.failed_request_count,
                    scope_denied_count=batch.scope_denied_count,
                    truncated_discovery_count=(
                        batch.truncated_discovery_count
                    ),
                )
            page = await self.fetcher.fetch(str(attempt["normalized_url"]))
        except asyncio.CancelledError:
            self.repository.complete_failure(
                owner_id,
                str(attempt["attempt_id"]),
                error_code="network_error",
                error_message="网页获取被中断，未形成来源快照",
            )
            raise
        except _FetchFailure as exc:
            return self.repository.complete_failure(
                owner_id,
                str(attempt["attempt_id"]),
                error_code=exc.code,
                error_message=str(exc),
            )
        except TimeoutError:
            return self.repository.complete_failure(
                owner_id,
                str(attempt["attempt_id"]),
                error_code="timeout",
                error_message="同站批次超过总读取时限，未形成来源快照",
            )
        except Exception:
            self.repository.complete_failure(
                owner_id,
                str(attempt["attempt_id"]),
                error_code="network_error",
                error_message="网页获取意外中断，未形成来源快照",
            )
            raise
        return self.repository.complete_success(
            owner_id,
            str(attempt["attempt_id"]),
            page,
        )
