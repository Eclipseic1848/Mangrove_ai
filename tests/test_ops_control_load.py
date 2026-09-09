"""五十个真实登录会话的持久创建和停止；仅临时库和阻塞 Runtime。"""
import asyncio
from contextlib import closing
import json
import math
import os
from pathlib import Path
import sqlite3
import time

import httpx

from src.api import auth
from src.model_connections import ConnectionBroker, broker as broker_module
from src.model_connections.storage import ModelConnectionRepository
from src.model_connections.vault import FernetCredentialVault
from src.account_execution import execution_context
from src.api.routes import auth_routes
from src.config.settings import settings
from src.runtime_routing import RolloutMode
from src.services.upload_store import UploadStore
from tests.test_pi_runtime_workspace_api import BlockingPiRuntime, _client


def test_fifty_owner_durable_control(tmp_path, monkeypatch, record_property):
    # 持续负载明确为5次/秒；0表示50请求突发压力，不把两者的结果混用。
    arrival_rate = float(os.environ.get("MANGROVE_LOAD_ARRIVAL_RATE", "5"))
    if arrival_rate < 0 or not math.isfinite(arrival_rate):
        raise ValueError("负载到达率必须为有限非负数")
    runtime = BlockingPiRuntime()
    harness = _client(tmp_path, monkeypatch, role="user", pi_runtime=runtime, routing_mode=RolloutMode.VNEXT_DEFAULT)
    harness.app.dependency_overrides.clear()
    harness.app.include_router(auth_routes.router)
    monkeypatch.setattr(settings, "jwt_secret", "synthetic-control-only-" + "x" * 48)
    with closing(sqlite3.connect(settings.webui_db_path)) as connection:
        assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    store = auth.get_store()
    password = "synthetic-control-password"
    password_hash = auth.hash_password(password)
    uploads = UploadStore(settings.data_prep_upload_root, max_bytes=1024 * 1024)
    broker = ConnectionBroker(repository=ModelConnectionRepository(settings.webui_db_path), vault=FernetCredentialVault.generate(), transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={"choices": [{"message": {"role": "assistant", "content": "OK"}}]})), resolver=lambda _host: ["8.8.8.8"])
    monkeypatch.setattr(broker_module, "_default_broker", broker)
    actors = []
    connections = []
    for index in range(50):
        user = store.create_user(f"control-{index}", password_hash, pending=False)
        upload = uploads.save_bytes(user["user_id"], "合成表.csv", b"item,value\na,1\n", media_type="text/csv")
        actors.append((user, upload))
        with execution_context(store.capture_account_execution(user["user_id"])):
            connections.append(asyncio.run(broker.configure_personal(owner_user_id=user["user_id"], preset_id="deepseek", api_key="synthetic-control-secret-1234"))["connection_id"])

    async def scenario():
        clients = []
        samples = []
        try:
            for index, _ in enumerate(actors):
                client = httpx.AsyncClient(transport=httpx.ASGITransport(app=harness.app), base_url="https://testserver", headers={"Origin": "https://testserver", "X-Mangrove-CSRF": "1"})
                clients.append(client)
                response = await client.post("/api/auth/login", json={"username": f"control-{index}", "password": password})
                assert response.status_code == 200, response.text

            submit_epoch = time.perf_counter()
            async def submit(index):
                user, upload = actors[index]
                scheduled = submit_epoch + index / arrival_rate if arrival_rate else None
                if scheduled is not None:
                    await asyncio.sleep(max(0, scheduled - time.perf_counter()))
                started = scheduled if scheduled is not None else time.perf_counter()
                response = await clients[index].post("/api/semantic-workspace/tasks", headers={"Idempotency-Key": f"control-{index}"}, json={"objective_text": "读取表格并保留列，以JSON输出", "upload_ids": [upload.upload_id], "output_formats": ["json"], "runtime_version": "pi", "provider": "local", "model_connection_id": connections[index], "external_api_confirmed": True})
                elapsed = time.perf_counter() - started
                assert response.status_code == 202, response.text
                identity = response.json()["task_id"]
                events = await asyncio.to_thread(store.list_semantic_workspace_events, user["user_id"], identity)
                assert any(event["event_type"] == "task_created" for event in events)
                sample = {"owner_id": user["user_id"], "task_id": identity, "revision": 1, "response_seconds": elapsed, "durable_observed_seconds": time.perf_counter() - started}
                samples.append(sample)
                return sample

            await asyncio.gather(*(submit(index) for index in range(50)))
            # 跨Owner拒绝单独测量，不挤入成功停止的延迟分布。
            other = next(index for index, (user, _) in enumerate(actors) if user["user_id"] != samples[0]["owner_id"])
            denied = await clients[other].post(f"/api/semantic-workspace/tasks/{samples[0]['task_id']}/cancel")
            assert denied.status_code == 404, denied.text
        finally:
            stop_epoch = time.perf_counter()
            async def stop(order, sample):
                index = next(i for i, (user, _) in enumerate(actors) if user["user_id"] == sample["owner_id"])
                scheduled = stop_epoch + order / arrival_rate if arrival_rate else None
                if scheduled is not None:
                    await asyncio.sleep(max(0, scheduled - time.perf_counter()))
                started = scheduled if scheduled is not None else time.perf_counter()
                response = await clients[index].post(f"/api/semantic-workspace/tasks/{sample['task_id']}/cancel")
                sample["cancel_seconds"] = time.perf_counter() - started
                sample["cancel_http"] = response.status_code
                row = await asyncio.to_thread(store.get_semantic_workspace_task, sample["owner_id"], sample["task_id"])
                sample["final_status"] = row["status"]
            await asyncio.gather(*(stop(order, sample) for order, sample in enumerate(samples)))
            await asyncio.gather(*(client.aclose() for client in clients))
            report = {"scope": "50 real authenticated ASGI sessions, actual Manager and SQLite WAL/FULL, synthetic blocked Runtime", "arrival_rate_per_second": arrival_rate or "50 simultaneous requests", "timing_includes_scheduled_arrival_delay": bool(arrival_rate), "external_calls": 0, "production_access": False, "samples": samples}
            for field in ("response_seconds", "durable_observed_seconds", "cancel_seconds"):
                values = sorted(item[field] for item in samples if field in item)
                report[field] = {"count": len(values), "p95": values[math.ceil(len(values) * .95) - 1], "max": values[-1]} if values else None
            record_property("load_report", json.dumps(report, ensure_ascii=False))
        assert len(samples) == 50
        assert all(item["cancel_http"] == 200 and item["final_status"] == "cancelled" for item in samples), report
        assert report["durable_observed_seconds"]["p95"] <= 2, report
        assert report["cancel_seconds"]["p95"] <= 2, report

    with harness:
        harness.portal.call(scenario)
