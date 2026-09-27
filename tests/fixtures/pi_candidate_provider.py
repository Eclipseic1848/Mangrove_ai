"""容器内合成模型与工具中继；仅监听回环地址，不访问外部服务。"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
from pathlib import Path
import subprocess
import sys
import threading
import time

requests = []
session_path = Path("/workspace/session/qualification.jsonl")
print(json.dumps({"fixture_existing_session_bytes": session_path.stat().st_size if session_path.exists() else None}), file=sys.stderr, flush=True)
Path("/workspace/work/model-requests.json").write_text("[]", encoding="utf-8")
calls = [
    ("read", {"path": "/workspace/work/source.txt"}),
    ("inspect_source", {"source_id": "fixture"}),
    ("capability_fixture", {"arguments": []}),
    ("bash", {"command": "cat /root/.pi/agent/models.json"}),
]

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= 200000:
            self.send_error(400)
            return
        body = json.loads(self.rfile.read(length))
        if self.path == "/v1/chat/completions":
            requests.append(body)
            Path("/workspace/work/model-requests.json").write_text(json.dumps(requests, ensure_ascii=False), encoding="utf-8")
            if "fixture-unknown-outcome" in json.dumps(body.get("messages", [])):
                # 请求已被服务端记录，但响应丢失；客户端不能据此安全重发。
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                self.close_connection = True
                return
            if "fixture-cancel-running" in json.dumps(body.get("messages", [])):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                chunk = {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "等待取消"}, "finish_reason": None}]}
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode("utf-8"))
                self.wfile.flush()
                time.sleep(30)
                return
            if len(requests) > 5:
                self.send_error(500)
                return
            if len(requests) <= len(calls):
                name, arguments = calls[len(requests) - 1]
                delta = {"role": "assistant", "tool_calls": [{"index": 0, "id": f"fixture-{len(requests)}", "type": "function",
                    "function": {"name": name, "arguments": json.dumps(arguments)}}]}
                finish = "tool_calls"
            else:
                delta, finish = {"role": "assistant", "content": "fixture-complete"}, "stop"
            chunks = [{"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                      {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                       "usage": {"prompt_tokens": 100, "completion_tokens": 10, "total_tokens": 110}}]
            payload = "".join("data: " + json.dumps(chunk) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
            content_type = "text/event-stream"
        elif self.path == "/inspect_source":
            payload, content_type = json.dumps({"source_id": "fixture", "pages": 1}), "application/json"
        elif self.path == "/invoke":
            payload, content_type = json.dumps({"stdout": "capability-ok"}), "application/json"
        else:
            self.send_error(404)
            return
        encoded = payload.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

server = ThreadingHTTPServer(("127.0.0.1", 19190), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
try:
    raise SystemExit(subprocess.call(["pi", *sys.argv[1:]]))
finally:
    server.shutdown()
    server.server_close()
