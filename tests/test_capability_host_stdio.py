"""用真实 SDK 和 stdio 子进程验证日志反压，不调用外部服务。"""
from __future__ import annotations

from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

import pytest


ROOT = Path(__file__).resolve().parents[1]
MARKER = "MANGROVE_SYNTHETIC_PRIVATE_STDERR"
TOKEN = "synthetic-capability-host-token"


@pytest.fixture(params=["modern", "compat"])
def sdk_pack(tmp_path, request):
    node = shutil.which("node")
    installed = os.environ.get("MANGROVE_MCP_TEST_PACK")
    if not node or not installed:
        pytest.skip("需 Node 和 MANGROVE_MCP_TEST_PACK 指向既有 MCP 能力包；不自动安装依赖")
    modules = (
        ["@modelcontextprotocol/client", "@modelcontextprotocol/client/stdio"]
        if request.param == "modern" else
        ["@modelcontextprotocol/sdk/client/index.js", "@modelcontextprotocol/sdk/client/stdio.js"]
    )
    resolved = subprocess.run(
        [node, "-e", "const r=require('node:module').createRequire(require('node:path').resolve(process.argv[1],'package.json')); console.log(JSON.stringify(JSON.parse(process.argv[2]).map(x=>r.resolve(x))));", installed, json.dumps(modules)],
        capture_output=True, text=True, encoding="utf-8", timeout=10, check=True,
    )
    targets = json.loads(resolved.stdout)
    pack = tmp_path / "pack"
    package_name = "@modelcontextprotocol/client" if request.param == "modern" else "@modelcontextprotocol/sdk"
    package = pack / "node_modules" / package_name
    package.mkdir(parents=True)
    exports = (
        {".": "./client.cjs", "./stdio": "./stdio.cjs"}
        if request.param == "modern" else
        {"./client/index.js": "./client.cjs", "./client/stdio.js": "./stdio.cjs"}
    )
    (package / "package.json").write_text(json.dumps({"name": package_name, "exports": exports}), encoding="utf-8")
    for name, target in zip(["client", "stdio"], targets):
        (package / f"{name}.cjs").write_text(f"module.exports = require({json.dumps(target)});\n", encoding="utf-8")
    (pack / "package.json").write_text("{}\n", encoding="utf-8")
    return node, pack


@contextmanager
def running_host(tmp_path, sdk_pack):
    node, pack = sdk_pack
    config = tmp_path / "capability-host.json"
    config.write_text(json.dumps({"capabilities": [{"root": str(pack), "manifest": {
        "name": "fixture", "kind": "mcp_local", "entrypoint": {
            "program": "node", "arguments": [str(ROOT / "tests/fixtures/mcp_stderr_server.mjs"), str(tmp_path / "calls.txt")],
            "timeout_seconds": 2,
        },
    }}]}), encoding="utf-8")
    source = (ROOT / "src/capability_host/capability_host_server.mjs").read_text(encoding="utf-8")
    # 只迁移固定容器路径和监听地址，协议及生命周期使用原宿主代码。
    old_config = '"/opt/mangrove-host/capability-host.json"'
    old_listen = 'server.listen(8765, "0.0.0.0");'
    assert source.count(old_config) == source.count(old_listen) == 1
    source = source.replace(old_config, json.dumps(str(config))).replace(
        old_listen,
        'server.listen(0, "127.0.0.1", () => console.log(JSON.stringify({ port: server.address().port })));',
    )
    host_script = tmp_path / "host.mjs"
    host_script.write_text(source, encoding="utf-8")
    output, errors = tmp_path / "host.stdout", tmp_path / "host.stderr"
    with output.open("wb") as stdout, errors.open("wb") as stderr:
        process = subprocess.Popen([node, str(host_script)], cwd=tmp_path, env={**os.environ, "MANGROVE_CAPABILITY_TOKEN": TOKEN}, stdout=stdout, stderr=stderr)
        try:
            deadline = time.monotonic() + 15
            while not output.stat().st_size and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert process.poll() is None, errors.read_text(encoding="utf-8")[-1000:]
            assert output.stat().st_size, "宿主握手未在期限内完成"
            port = json.loads(output.read_text(encoding="utf-8").splitlines()[0])["port"]
            yield port
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=8)
            assert MARKER not in output.read_text(encoding="utf-8") + errors.read_text(encoding="utf-8")


def invoke(port, action="echo"):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
    try:
        connection.request("POST", "/invoke", json.dumps({"capability": "fixture", "tool": "fixture", "arguments": {"action": action}}), {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
        response = connection.getresponse()
        body = response.read().decode("utf-8")
        assert MARKER not in body
        return response.status, json.loads(body)
    finally:
        connection.close()


def listed_tools(port):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    try:
        connection.request("GET", "/health", headers={"Authorization": f"Bearer {TOKEN}"})
        response = connection.getresponse()
        assert response.status == 200
        return json.loads(response.read())["capabilities"][0]["tools"]
    finally:
        connection.close()


def test_mcp_high_stderr_does_not_block_handshake_or_tool(tmp_path, sdk_pack):
    with running_host(tmp_path, sdk_pack) as port:
        status, body = invoke(port)
        assert status == 200
        assert body["stderr"] == ""
        assert json.loads(body["stdout"]) == {"content": [{"type": "text", "text": "fixture-ok"}]}


def test_mcp_high_stderr_timeout_closes_session(tmp_path, sdk_pack):
    with running_host(tmp_path, sdk_pack) as port:
        started = time.monotonic()
        status, body = invoke(port, "hang")
        elapsed = time.monotonic() - started
        assert (tmp_path / "calls.txt").read_text(encoding="utf-8") == "hang\n"
        assert 2 <= elapsed < 8
        assert status == 400 and body.get("error") and "stdout" not in body
        assert listed_tools(port) == []
        status, body = invoke(port)
        assert status == 400 and body.get("error") and "stdout" not in body


def test_mcp_high_stderr_process_exit_fails_closed(tmp_path, sdk_pack):
    with running_host(tmp_path, sdk_pack) as port:
        status, body = invoke(port, "crash")
        assert status == 400 and body.get("error") and "stdout" not in body
        status, body = invoke(port)
        assert status == 400 and body.get("error") and "stdout" not in body


def test_mcp_high_stderr_request_cancel_closes_session(tmp_path, sdk_pack):
    with running_host(tmp_path, sdk_pack) as port:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=8)
        try:
            connection.request("POST", "/invoke", json.dumps({"capability": "fixture", "tool": "fixture", "arguments": {"action": "hang"}}), {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
            # 夹具确认日志已写完且调用已进入等待，再断开真实 HTTP 连接。
            calls = tmp_path / "calls.txt"
            deadline = time.monotonic() + 1.5
            while not calls.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert calls.read_text(encoding="utf-8") == "hang\n"
            connection.sock.shutdown(socket.SHUT_RDWR)
        finally:
            connection.close()
        deadline = time.monotonic() + 1
        while time.monotonic() < deadline:
            if listed_tools(port) == []:
                break
            time.sleep(0.01)
        else:
            pytest.fail("取消后 MCP 会话未及时撤销")
        status, body = invoke(port)
        assert status == 400 and body.get("error") and "stdout" not in body
