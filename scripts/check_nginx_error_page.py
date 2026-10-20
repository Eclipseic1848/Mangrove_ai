"""隔离验证故障页的状态码和 API 错误透传，不中断线上服务。"""
from contextlib import closing
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import shutil
import os
import socket
import subprocess
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


def main() -> None:
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise SystemExit("请用普通部署账号运行自检，不要使用 sudo 或 root 终端。")
    nginx = shutil.which("nginx")
    if not nginx:
        raise SystemExit("需要已安装的 Nginx，请在 Linux 服务器运行。")
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path(__file__).with_name("nginx-http.conf"))
    args = parser.parse_args()
    source = Path(__file__).with_name("service-unavailable.html")
    json_source = Path(__file__).with_name("service-unavailable.json")
    template = args.config.read_text(encoding="utf-8")

    class Upstream(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/api/gateway-failure":
                self.close_connection = True
                return
            if self.path == "/api/gateway-timeout":
                time.sleep(0.3)
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            try:
                self.wfile.write(b'{"detail":"upstream-unavailable"}')
            except BrokenPipeError:
                pass

        def log_message(self, *_args):
            pass

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    threading.Thread(target=upstream.serve_forever, daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="mangrove-nginx-check-") as folder:
            root = Path(folder)
            page = root / "service-unavailable.html"
            shutil.copyfile(source, page)
            api_error = root / "service-unavailable.json"
            if json_source.exists():
                shutil.copyfile(json_source, api_error)
            with closing(socket.socket()) as sock:
                sock.bind(("127.0.0.1", 0))
                port = sock.getsockname()[1]
            config = root / "nginx.conf"
            server_config = re.sub(r"listen\s+(?:127\.0\.0\.1:)?\d+\s*;", f"listen 127.0.0.1:{port};", template, count=1)
            server_config = server_config.replace("/var/www/mangrove-errors/service-unavailable.html", str(page))
            server_config = server_config.replace("/var/www/mangrove-errors/service-unavailable.json", str(api_error))
            server_config = server_config.replace("http://127.0.0.1:8088", f"http://127.0.0.1:{upstream.server_port}")
            server_config = re.sub(r"proxy_read_timeout\s+[^;]+;", "proxy_read_timeout 100ms;", server_config)
            end = server_config.rfind("}")
            server_config = server_config[:end] + "\n".join(f"location = /{code} {{ return {code}; }}" for code in (502, 503, 504)) + server_config[end:]
            config.write_text(
                f'pid "{root}/nginx.pid"; error_log "{root}/error.log"; events {{}}\n'
                f'http {{ access_log off; {server_config} }}',
                encoding="utf-8",
            )
            command = [nginx, "-p", folder, "-c", str(config)]
            subprocess.run(command + ["-t"], check=True, capture_output=True)
            subprocess.run(command, check=True, capture_output=True)
            try:
                def request(path):
                    try:
                        response = urlopen(f"http://127.0.0.1:{port}{path}", timeout=3)
                    except HTTPError as error:
                        response = error
                    with response:
                        return response.status, response.headers, response.read()

                for attempt in range(30):
                    try:
                        request("/502")
                        break
                    except URLError:
                        if attempt == 29:
                            raise
                        time.sleep(0.1)
                for code in (502, 503, 504):
                    status, headers, body = request(f"/{code}")
                    assert status == code, (status, code)
                    assert body == page.read_bytes()
                    assert "系统正在维护升级中" in body.decode("utf-8")
                    assert headers["Cache-Control"] == "no-store"
                    assert headers["Server"] == "nginx"
                status, headers, body = request("/api/health")
                assert status == 503 and headers["Content-Type"] == "application/json"
                assert body == b'{"detail":"upstream-unavailable"}'
                for path, expected in (("gateway-failure", 502), ("gateway-timeout", 504)):
                    status, headers, body = request(f"/api/{path}")
                    assert status == expected and headers["Content-Type"] == "application/json"
                    assert body == api_error.read_bytes()
                    assert headers["Cache-Control"] == "no-store"
                assert request("/operations")[2] == page.read_bytes()
                assert request("/__mangrove_service_unavailable.html")[0] == 404
                assert request("/__mangrove_api_unavailable.json")[0] == 404
                assert request("/internal/model-relay")[0] == 404
                print("通过：页面 502/503/504 维护提示、状态码、禁用缓存、隐藏版本、API JSON 透传及网关失败/超时、内部入口隔离。")
            finally:
                subprocess.run(command + ["-s", "quit"], check=True, capture_output=True)
                for _ in range(50):
                    if not (root / "nginx.pid").exists():
                        break
                    time.sleep(0.1)
    finally:
        upstream.shutdown()
        upstream.server_close()


if __name__ == "__main__":
    main()
