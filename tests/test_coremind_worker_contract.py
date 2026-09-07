# -*- coding: utf-8 -*-
"""锁定 SDK 的显式离线契约检查；默认回归不启动外部 Worker。"""
from __future__ import annotations

import hashlib
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

    @unittest.skipUnless(os.environ.get("MANGROVE_COREMIND_HOST_VERIFICATION_TEST") == "1", "需固定宿主验收开发制品")
    def test_host_rejects_then_accepts_repair_in_same_run(self):
        self._run_local_model(include_usage=True, host_gate=True)

    @unittest.skipUnless(os.environ.get("MANGROVE_COREMIND_HOST_VERIFICATION_TEST") == "1", "需固定宿主验收开发制品")
    def test_host_timeout_resumes_same_run_then_applies_cancel_control(self):
        self._run_local_model(include_usage=True, host_gate=True, control_probe=True)

    def _run_local_model(self, *, include_usage, host_gate=False, control_probe=False):
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
                if len(requests) > (2 if host_gate else 1):
                    self.send_error(500, "unexpected model request")
                    return
                candidate = ("初稿" if len(requests) == 1 else "修正稿") if host_gate else "离线测试完成。"
                chunks = [
                    {"choices": [{"index": 0, "delta": {"role": "assistant", "content": candidate}, "finish_reason": None}]},
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
                        **({"loop": {
                            "execute": {"agent": "main", "input": "{{prompt}}"},
                            "verify": {"mode": "host", "timeoutMs": 1000 if control_probe else 5000},
                            "repair": {"agent": "main", "input": "按宿主反馈修正：{{verification.text}}"},
                            "maxIterations": 2, "maxRepairs": 1,
                        }} if host_gate else {}),
                    },
                    config_dir=directory, cwd=directory, protocol_version="2.0", request_timeout=10,
                )
                try:
                    with patch.dict(os.environ, environment, clear=True):
                        client.start()
                    handle = client.run("仅返回合成测试结果。", run_id="fixture-run-a")
                    self.assertEqual(handle["runId"], "fixture-run-a")
                    if control_probe:
                        self._probe_controls(client, handle, requests)
                        return
                    if host_gate:
                        self.assertIn("verification", handle["availableControls"])
                        for index, candidate in enumerate(("初稿", "修正稿")):
                            deadline = time.monotonic() + 10
                            while len(client.received_verification_requests) <= index:
                                if time.monotonic() >= deadline:
                                    self.fail("未收到持久宿主验收请求")
                                time.sleep(0.02)
                            verification = client.received_verification_requests[index]
                            self.assertEqual(verification["runId"], handle["runId"])
                            self.assertEqual(verification["candidate"], candidate)
                            self.assertEqual(verification["candidateSha256"], hashlib.sha256(candidate.encode("utf-8")).hexdigest())
                            self.assertEqual(verification["iteration"], index + 1)
                            self.assertNotEqual(client.query(handle["runId"])["projection"]["status"], "finished")
                            reply = {
                                "run_id": handle["runId"], "request_id": verification["requestId"],
                                "candidate_sha256": verification["candidateSha256"],
                                "decision": "reject" if index == 0 else "accept",
                                "feedback": "补充独立证据" if index == 0 else "",
                                "control_id": f"fixture-verification-{index}",
                            }
                            # 摘要不符不能放行；固定控制身份让未知结果可以幂等查询/重试。
                            invalid = client.submit_verification(**{**reply, "candidate_sha256": "0" * 64, "control_id": f"invalid-{index}"})
                            self.assertEqual(invalid["status"], "rejected")
                            self.assertEqual(client.submit_verification(**reply)["status"], "applied")
                            if index == 0:
                                self.assertEqual(client.submit_verification(**reply)["status"], "duplicate")
                                self.assertEqual(client.submit_verification(**{**reply, "decision": "accept"})["status"], "conflict")
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
                    self.assertEqual(len(usage), 2 if host_gate else 1)
                    # 锁定 Worker 的兼容事实，不是计费权威；Mangrove 投影另测缺失用量保持未知。
                    self.assertEqual(usage[0]["payload"]["inputTokens"], 12 if include_usage else 0)
                    self.assertEqual(usage[0]["payload"]["outputTokens"], 3 if include_usage else 0)
                    self.assertEqual(usage[0]["payload"]["tokens"], 15 if include_usage else 0)
                    self.assertEqual([request["model"] for request in requests], ["fixture-model"] * (2 if host_gate else 1))
                    if host_gate:
                        self.assertIn("补充独立证据", json.dumps(requests[1], ensure_ascii=False))
                    self.assertEqual([event["sequence"] for event in page["events"]], list(range(1, page["nextCursor"] + 1)))
                    self.assertEqual(client.events("fixture-run-a", after_sequence=page["nextCursor"])["events"], [])
                finally:
                    client.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    def _probe_controls(self, client, handle, requests):
        import coremind

        run_id = handle["runId"]

        def wait_for(predicate, description):
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                result = predicate()
                if result:
                    return result
                time.sleep(0.02)
            self.fail(description)

        wait_for(lambda: client.received_verification_requests, "未到达宿主验收边界")
        original = client.received_verification_requests[0]
        self.assertNotIn("pause", handle["availableControls"])
        # 固定协议没有任意暂停命令，必须保留明确拒绝而非伪装成支持。
        with self.assertRaises(coremind.ProtocolError) as caught:
            client.control({"schemaVersion": 1, "controlId": "fixture-pause",
                            "runId": run_id, "type": "pause"})
        self.assertEqual(caught.exception.coremind_code, "protocol_validation_failed")
        wait_for(lambda: client.query(run_id)["projection"]["status"] == "paused",
                 "验收结果未知未形成暂停")
        resumed = client.resume_run(run_id)
        self.assertEqual(resumed["runId"], run_id)
        wait_for(lambda: len(client.received_verification_requests) == 2, "同 Run 验收未恢复")
        self.assertEqual(client.received_verification_requests[1], original)
        self.assertEqual(len(requests), 1, "恢复不得重复已经完成的模型调用")
        command = {"schemaVersion": 1, "controlId": "fixture-cancel",
                   "runId": run_id, "type": "cancel"}
        receipt = client.control(command)
        self.assertEqual(receipt["runId"], run_id)
        self.assertEqual(receipt["controlId"], command["controlId"])
        self.assertEqual(receipt["status"], "applied")
        wait_for(lambda: client.query(run_id)["projection"]["status"] == "finished",
                 "取消未形成终态")
        self.assertEqual(client.query(run_id)["projection"]["outcome"]["status"], "aborted")
        events = client.events(run_id, after_sequence=0, limit=1000)["events"]
        self.assertTrue(all(event["runId"] == run_id for event in events))
        controls = [event for event in events if event["eventType"] == "fact.control" and
                    event["payload"].get("controlId") == command["controlId"]]
        self.assertEqual([event["payload"]["state"] for event in controls], ["accepted", "applied"])
        self.assertEqual(controls[-1]["sequence"], receipt["appliedSequence"])
        self.assertIn("fact.pause", [event["eventType"] for event in events])
        self.assertIn("fact.resume", [event["eventType"] for event in events])


if __name__ == "__main__":
    unittest.main()
