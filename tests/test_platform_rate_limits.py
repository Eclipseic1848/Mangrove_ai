# -*- coding: utf-8 -*-
"""共享网页登录和请求限流；所有用户、来源与 SQLite 库均为测试数据。"""
from contextlib import contextmanager
import hashlib
import multiprocessing
import sqlite3

from fastapi import Depends, FastAPI
from fastapi.responses import StreamingResponse
from fastapi.testclient import TestClient
import pytest

from src.api import auth
from src.api.routes import auth_routes
from src.api.store import WebUIStore
from src.api import platform_rate_limits as limits
from src.config.settings import settings
from tests.database_migration_helpers import migrated_webui_database


@pytest.mark.parametrize("username", ["rate-fixture", "unknown-fixture"])
def test_sixth_failed_login_is_rate_limited(tmp_path, monkeypatch, username):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "webui.db")))
    store.create_user("rate-fixture", auth.hash_password("synthetic-correct-password"))
    monkeypatch.setattr(auth, "_store", store)
    app = FastAPI()
    app.include_router(auth_routes.router)
    with TestClient(app, base_url="https://testserver", client=("127.0.0.1", 50115)) as client:
        responses = [client.post("/api/auth/login",
                                 json={"username": username, "password": "synthetic-wrong-password"},
                                 headers={"Origin": "https://testserver", "X-Mangrove-CSRF": "1"})
                     for _ in range(6)]
    assert [response.status_code for response in responses[:5]] == [401] * 5
    assert responses[5].status_code == 429
    assert 1 <= int(responses[5].headers["Retry-After"]) <= 900


@contextmanager
def transaction(path):
    conn = sqlite3.connect(str(path), timeout=10)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


@pytest.fixture
def shared_stores(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "jwt_secret", "synthetic-platform-rate-key-" + "x" * 32)
    path = str(migrated_webui_database(tmp_path / "rates.db"))
    return WebUIStore(path), WebUIStore(path)


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def login_attempt(path, *, now, account="account-a", source="source-a", success=False):
    with transaction(path) as conn:
        kwargs = {"account_key": digest(account), "source_key": digest(source), "now": now}
        retry = limits.login_retry_after(conn, **kwargs)
        if not retry:
            limits.record_login_result(conn, success=success, **kwargs)
        return retry


def consume(path, now, control=False, owner="synthetic-owner"):
    with transaction(path) as conn:
        return limits.consume_request_limits(conn, owner_user_id=owner, control=control, now=now)


def test_login_window_blocks_account_or_source_and_success_clears_only_its_buckets(shared_stores):
    first, second = shared_stores
    for now in (1000, 1010, 1020, 1030, 1040):
        assert login_attempt(first.db_path, now=now) == 0
    assert login_attempt(second.db_path, now=1040, source="other-source") == 900
    assert login_attempt(second.db_path, now=1040, account="other-account") == 900
    assert login_attempt(second.db_path, now=1939.01) == 1
    assert login_attempt(second.db_path, now=1940, success=True) == 0
    with transaction(first.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM platform_rate_events").fetchone()[0] == 0
    for _ in range(4):
        assert login_attempt(first.db_path, now=2000, account="survivor", source="other") == 0
    assert login_attempt(second.db_path, now=2000, success=True) == 0
    assert login_attempt(first.db_path, now=2000, account="survivor", source="other") == 0
    assert login_attempt(second.db_path, now=2000, account="survivor", source="new-source") == 900


def test_login_strict_window_excludes_exact_boundary_and_uses_max_retry(shared_stores):
    first, _ = shared_stores
    for _ in range(4):
        assert login_attempt(first.db_path, now=1000) == 0
    assert login_attempt(first.db_path, now=1900) == 0
    # 两个独立阈值同时命中时，必须返回更晚的来源窗口。
    for _ in range(5):
        assert login_attempt(first.db_path, now=3000, account="a", source="a-source") == 0
        assert login_attempt(first.db_path, now=3010, account="b", source="b-source") == 0
    with transaction(first.db_path) as conn:
        assert limits.login_retry_after(conn, account_key=digest("a"), source_key=digest("b-source"), now=3010.1) == 900


@pytest.mark.parametrize("control,quota", [(False, 120), (True, 10)])
def test_shared_store_strict_request_window_and_owner_isolation(shared_stores, control, quota):
    first, second = shared_stores
    for index in range(quota):
        store = first if index % 2 else second
        assert consume(store.db_path, now=1000 + index / 1000, control=control) == 0
    assert consume(second.db_path, now=1001, control=control) == 59
    assert consume(first.db_path, now=1059.01, control=control) == 1
    assert consume(first.db_path, now=1060, control=control) == 0
    assert consume(first.db_path, now=1060, control=control) == 1
    assert consume(second.db_path, now=1001, control=control, owner="another-owner") == 0


def test_control_consumes_both_buckets_and_stop_only_ordinary(shared_stores):
    first, _ = shared_stores
    for _ in range(10):
        assert consume(first.db_path, 1000, control=True) == 0
    assert consume(first.db_path, 1001, control=True) == 59
    for _ in range(110):
        assert consume(first.db_path, 1010) == 0
    assert consume(first.db_path, 1011) == 49
    assert consume(first.db_path, 1011, control=True) == 49
    with transaction(first.db_path) as conn:
        counts = sorted(row[0] for row in conn.execute("SELECT COUNT(*) FROM platform_rate_events GROUP BY bucket"))
        assert counts == [10, 120]


def test_denied_threshold_audit_is_committed_once_and_retained_180_days(shared_stores):
    first, second = shared_stores
    now = 200 * 86400
    for _ in range(10):
        assert consume(first.db_path, now, control=True) == 0
    for _ in range(10):
        assert consume(second.db_path, now, control=True) == 60
    with transaction(first.db_path) as conn:
        rows = conn.execute("SELECT actor_user_id, action, subject_digest, reason, result FROM platform_security_audit").fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "synthetic-owner"
        assert "synthetic-owner" not in rows[0][2]
        assert len(rows[0][2]) == 64
        limits.append_security_event(conn, action="session_revoked", subject_digest=digest("low-sensitive-id"),
                                     reason="fixture", result="success", now=now + 180 * 86400)
        assert conn.execute("SELECT COUNT(*) FROM platform_security_audit").fetchone()[0] == 2
        limits.append_security_event(conn, action="session_revoked", subject_digest=digest("low-sensitive-id"),
                                     reason="fixture", result="success", now=now + 180 * 86400 + 0.001)
        assert conn.execute("SELECT COUNT(*) FROM platform_security_audit").fetchone()[0] == 2


def test_login_audit_aggregates_without_account_or_source_text(shared_stores):
    first, _ = shared_stores
    for _ in range(5):
        assert login_attempt(first.db_path, now=1000, account="synthetic-name-canary", source="192.0.2.115") == 0
    for _ in range(10):
        assert login_attempt(first.db_path, now=1001, account="synthetic-name-canary", source="192.0.2.115") == 899
    with transaction(first.db_path) as conn:
        rows = conn.execute("SELECT * FROM platform_security_audit").fetchall()
        assert len(rows) == 2
        assert "synthetic-name-canary" not in repr(rows)
        assert "192.0.2.115" not in repr(rows)


def _process_attempts(path, mode, barrier, output):
    settings.jwt_secret = "synthetic-platform-rate-key-" + "x" * 32
    try:
        barrier.wait(timeout=10)
        accepted = 0
        for _ in range(80 if mode == "ordinary" else 8):
            retry = login_attempt(path, now=1000) if mode == "login" else consume(path, 1000, control=mode == "control")
            accepted += retry == 0
        output.put((accepted, None))
    except Exception as error:
        output.put((0, type(error).__name__))


@pytest.mark.parametrize("mode,quota", [("login", 5), ("ordinary", 120), ("control", 10)])
def test_two_processes_share_atomic_quota(shared_stores, mode, quota):
    first, _ = shared_stores
    context = multiprocessing.get_context("spawn")
    barrier = context.Barrier(2)
    output = context.Queue()
    processes = [context.Process(target=_process_attempts, args=(first.db_path, mode, barrier, output)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        results = [output.get(timeout=20) for _ in processes]
        for process in processes:
            process.join(timeout=5)
        assert all(not process.is_alive() and process.exitcode == 0 for process in processes)
        assert [error for _, error in results] == [None, None]
        assert sum(accepted for accepted, _ in results) == quota
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
        output.close()
        output.join_thread()


def test_missing_database_state_fails_closed_without_memory_fallback(tmp_path):
    with transaction(tmp_path / "missing.db") as conn:
        with pytest.raises(sqlite3.OperationalError):
            limits.consume_request_limits(conn, owner_user_id="synthetic-owner", control=False, now=1000)
    with sqlite3.connect(str(tmp_path / "no-transaction.db")) as conn:
        with pytest.raises(ValueError, match="事务"):
            limits.login_retry_after(conn, account_key=digest("a"), source_key=digest("b"), now=1000)


def test_max_retry_after_across_ordinary_and_control_windows(shared_stores):
    first, _ = shared_stores
    for _ in range(110):
        assert consume(first.db_path, 1000) == 0
    for _ in range(10):
        assert consume(first.db_path, 1010, control=True) == 0
    assert consume(first.db_path, 1011) == 49
    assert consume(first.db_path, 1011, control=True) == 59


@pytest.mark.parametrize("role", ["user", "super_admin"])
def test_authenticated_api_limits_admins_and_counts_nested_dependencies_and_sse_once(tmp_path, monkeypatch, role):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "api.db")))
    store.create_user("rate-fixture", auth.hash_password("synthetic-password"), role=role)
    monkeypatch.setattr(auth, "_store", store)
    app = FastAPI()
    app.include_router(auth_routes.router)

    def nested(user=Depends(auth.get_current_user, use_cache=False)):
        return user

    @app.get("/api/quota-probe")
    def probe(user=Depends(auth.get_current_user, use_cache=False), again=Depends(nested)):
        assert user["user_id"] == again["user_id"]
        return {"ok": True}

    @app.get("/api/events-probe")
    def events(user=Depends(auth.get_current_user)):
        return StreamingResponse(iter(["data: one\n\n", "data: two\n\n", "data: three\n\n"]), media_type="text/event-stream")

    with TestClient(app, base_url="https://testserver", client=("127.0.0.1", 50115)) as client:
        response = client.post("/api/auth/login", json={"username": "rate-fixture", "password": "synthetic-password"},
                               headers={"Origin": "https://testserver", "X-Mangrove-CSRF": "1"})
        assert response.status_code == 200, response.text
        streamed = client.get("/api/events-probe")
        assert streamed.status_code == 200 and streamed.text.count("data:") == 3
        for _ in range(119):
            assert client.get("/api/quota-probe").status_code == 200
        response = client.get("/api/quota-probe")
        assert response.status_code == 429
        assert 1 <= int(response.headers["Retry-After"]) <= 60
        # 只保留已饱和的共享事件，验证拒绝分支新建的阈值审计也提交而非随 HTTP 异常回滚。
        with transaction(store.db_path) as conn:
            conn.execute("DELETE FROM platform_rate_blocks")
            conn.execute("DELETE FROM platform_security_audit")
        assert client.get("/api/quota-probe").status_code == 429
    with transaction(store.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM platform_rate_events WHERE bucket='request.api'").fetchone()[0] == 120
        assert conn.execute("SELECT COUNT(*) FROM platform_security_audit WHERE reason='request.api'").fetchone()[0] == 1


def test_control_metadata_counts_ten_requests_but_stop_is_only_ordinary(tmp_path, monkeypatch):
    store = WebUIStore(str(migrated_webui_database(tmp_path / "control-api.db")))
    store.create_user("rate-fixture", auth.hash_password("synthetic-password"))
    monkeypatch.setattr(auth, "_store", store)
    app = FastAPI()
    app.include_router(auth_routes.router)

    @app.post("/api/start-probe", openapi_extra={"x-mangrove-task-control": True})
    def start(user=Depends(auth.get_current_user)):
        return {"ok": True}

    @app.post("/api/stop-probe")
    def stop(user=Depends(auth.get_current_user)):
        return {"ok": True}

    headers = {"Origin": "https://testserver", "X-Mangrove-CSRF": "1"}
    with TestClient(app, base_url="https://testserver", client=("127.0.0.1", 50115), headers=headers) as client:
        assert client.post("/api/auth/login", json={"username": "rate-fixture", "password": "synthetic-password"}).status_code == 200
        for _ in range(10):
            assert client.post("/api/start-probe").status_code == 200
        assert client.post("/api/start-probe").status_code == 429
        assert client.post("/api/stop-probe").status_code == 200
    with transaction(store.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM platform_rate_events WHERE bucket='request.control'").fetchone()[0] == 10
        assert conn.execute("SELECT COUNT(*) FROM platform_rate_events WHERE bucket='request.api'").fetchone()[0] == 11
