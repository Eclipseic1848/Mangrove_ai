# -*- coding: utf-8 -*-
"""锁定 SDK 的显式离线契约检查；默认回归不启动外部 Worker。"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch


def _worker_environment(directory):
    node = shutil.which("node")
    if node is None:
        raise RuntimeError("隔离 Worker 验证需要已安装的 Node")
    # SDK 继承进程环境；只在启动接缝传入运行必需项，禁止真实密钥、代理和 Node 注入参数。
    environment = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ}
    environment.update(
        PATH=str(Path(node).parent), HOME=directory, USERPROFILE=directory,
        TEMP=directory, TMP=directory, APPDATA=directory, LOCALAPPDATA=directory,
        MANGROVE_TEST_PROVIDER_KEY="fixture-not-secret",
    )
    return environment


@unittest.skipUnless(os.environ.get("MANGROVE_COREMIND_TEST") == "1", "需显式启用隔离 Worker 验证")
class CoreMindWorkerContractTests(unittest.TestCase):
    def test_v2_handshake_unknown_run_and_idempotent_close(self):
        import coremind

        self.assertEqual(coremind.__version__, "0.7.1")
        with tempfile.TemporaryDirectory(prefix="mangrove-coremind-") as directory:
            environment = _worker_environment(directory)
            client = coremind.CoreMindClient(
                {
                    "schemaVersion": 2, "name": "mangrove-contract",
                    "provider": {
                        "baseUrl": "http://127.0.0.1:9/v1", "model": "fixture-model",
                        "apiKeyEnv": "MANGROVE_TEST_PROVIDER_KEY",
                    },
                    "agents": {"main": {"systemPrompt": "仅用于离线契约检查。", "tools": []}},
                },
                config_dir=directory, cwd=directory, protocol_version="2.0", request_timeout=10,
            )
            try:
                with patch.dict(os.environ, environment, clear=True):
                    client.start()
                self.assertIsInstance(client.pid, int)
                with self.assertRaises(coremind.ProtocolError) as caught:
                    client.query("never-started-run")
                self.assertEqual(caught.exception.coremind_code, "unknown_run")
                self.assertEqual(client.received_events, [])
            finally:
                client.close()
            client.close()
            with self.assertRaises(coremind.CoreMindError):
                client.start()

    def test_local_model_run_preserves_model_usage_and_event_cursor(self):
        self._run_local_model(include_usage=True)

    def test_missing_native_usage_becomes_synthetic_zero_in_locked_worker(self):
        self._run_local_model(include_usage=False)

    def _run_local_model(self, *, include_usage):
        import coremind

        requests = []

        class ModelHandler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length", "0"))
                if self.path != "/v1/chat/completions" or not 0 < length <= 65536:
                    self.send_error(400)
                    return
                requests.append(json.loads(self.rfile.read(length)))
                chunks = [
                    {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "离线测试完成。"}, "finish_reason": None}]},
                    {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                ]
                if include_usage:
                    chunks[-1]["usage"] = {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
                body = "".join(
                    "data: " + json.dumps({"id": "fixture-response", "object": "chat.completion.chunk", "created": 1, "model": "fixture-model", **chunk}) + "\n\n"
                    for chunk in chunks
                ).encode("utf-8") + b"data: [DONE]\n\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), ModelHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix="mangrove-coremind-") as directory:
                environment = _worker_environment(directory)
                client = coremind.CoreMindClient(
                    {
                        "schemaVersion": 2, "name": "mangrove-contract",
                        "provider": {
                            "baseUrl": f"http://127.0.0.1:{server.server_port}/v1", "model": "fixture-model",
                            "apiKeyEnv": "MANGROVE_TEST_PROVIDER_KEY",
                        },
                        "agents": {"main": {"systemPrompt": "仅用于离线契约检查。", "tools": []}},
                        "runtime": {"maxTurns": 2, "maxRetries": 0, "runTimeoutMs": 10000},
                    },
                    config_dir=directory, cwd=directory, protocol_version="2.0", request_timeout=10,
                )
                try:
                    with patch.dict(os.environ, environment, clear=True):
                        client.start()
                    handle = client.run("仅返回合成测试结果。", run_id="fixture-run-a")
                    self.assertEqual(handle["runId"], "fixture-run-a")
                    deadline = time.monotonic() + 10
                    while True:
                        projection = None
                        try:
                            projection = client.query("fixture-run-a")
                        except coremind.ProtocolError as error:
                            # RunHandle 可能早于首条持久事实返回；只重查，不重发有副作用的 run。
                            if error.coremind_code != "unknown_run":
                                raise
                        if projection and projection["projection"]["status"] == "finished":
                            break
                        if time.monotonic() >= deadline:
                            self.fail("合成 Run 未在限时内收敛")
                        time.sleep(0.02)
                    self.assertEqual(projection["projection"]["outcome"]["status"], "succeeded")
                    page = client.events("fixture-run-a", after_sequence=0, limit=1000)
                    usage = [event for event in page["events"] if event["eventType"] == "turn_end"]
                    self.assertEqual(len(usage), 1)
                    # 锁定 Worker 的兼容事实，不是计费权威；Mangrove 投影另测缺失用量保持未知。
                    self.assertEqual(usage[0]["payload"]["inputTokens"], 12 if include_usage else 0)
                    self.assertEqual(usage[0]["payload"]["outputTokens"], 3 if include_usage else 0)
                    self.assertEqual(usage[0]["payload"]["tokens"], 15 if include_usage else 0)
                    self.assertEqual([request["model"] for request in requests], ["fixture-model"])
                    self.assertEqual([event["sequence"] for event in page["events"]], list(range(1, page["nextCursor"] + 1)))
                    self.assertEqual(client.events("fixture-run-a", after_sequence=page["nextCursor"])["events"], [])
                finally:
                    client.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
