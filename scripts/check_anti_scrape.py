"""以受控网页检查反爬增补；隔离进程内状态，不改业务域名记录或账号。"""
from __future__ import annotations

import argparse
import asyncio
import base64
import importlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="另读取一个真实公开网页，不调用模型")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    import src.agent
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.api.auth import get_current_user, get_store
    from src.api.routes.config_routes import router
    from src.collectors import _domain_health as health
    from src.collectors import scrapling_collector as scrapling
    from src.collectors.article_collector import ArticleCollector
    from src.collectors.registry import _effective_tier
    from src.config.runtime_config import apply_global_overrides
    from src.conductor.task_spec import TaskSpec

    apply_global_overrides(get_store())
    marker = "Mangrove anti scrape dynamic content verified"
    encoded = base64.b64encode(marker.encode("utf-8")).decode("ascii")

    class Page(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/blocked":
                body, status = b"<html><body>Access Denied</body></html>", 403
            else:
                body = ('<html><head><title>Just a moment</title></head><body><main id="content">Checking your browser</main>'
                        '<script>document.title="Verified";document.getElementById("content").textContent=atob("' + encoded + '");</script></body></html>').encode("utf-8")
                status = 200
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Page)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:" + str(server.server_port)
    checks = {}
    try:
        spec = TaskSpec(intent="反爬受控验证", urls=[base + "/dynamic"], max_items=1)
        result = asyncio.run(scrapling.ScraplingCollector().collect(spec))
        checks["verification_page_upgrades_to_dynamic_body"] = bool(result.success and any(marker in item.content for item in result.items))
        checks["stealth_browser_used"] = any(item.metadata.get("via") == "stealth" for item in result.items)
        # 使用真实前置引擎与采集节点，防止验证页提前截断升级链路。
        collect = importlib.import_module("src.conductor.nodes.collect")
        registry = {"article": ArticleCollector(), "scrapling": scrapling.ScraplingCollector()}
        with patch.object(collect, "get_registry", return_value=registry), patch.object(collect, "emit_progress"):
            routed = asyncio.run(collect.collect_node({
                "task_spec": TaskSpec(intent="完整升级链检查", urls=[base + "/routed"], max_items=1),
                "collector_candidates": ["article", "scrapling"],
            }))
        attempts = routed["collector_attempts"]
        checks["preceding_verification_page_is_failure"] = bool(
            attempts and not attempts[0]["success"] and "验证页" in attempts[0]["message"])
        checks["collection_route_reaches_stealth_browser"] = bool(
            routed["collector_used"] == "scrapling" and any(
                marker in item["content"] and item["metadata"].get("via") == "stealth"
                for item in routed["raw_dataset"]))
        blocked = asyncio.run(scrapling._fetch_stealth(base + "/blocked"))
        checks["http_error_not_reported_as_content"] = blocked is None
        # 已关闭端口制造临时网络故障，随后仍须能读取正常动态页面。
        unused = ThreadingHTTPServer(("127.0.0.1", 0), Page)
        closed_url = "http://127.0.0.1:" + str(unused.server_port)
        unused.server_close()
        failed = asyncio.run(scrapling._fetch_stealth(closed_url))
        recovered = asyncio.run(scrapling._fetch_stealth(base + "/dynamic"))
        checks["temporary_network_failure_recovers"] = bool(failed is None and recovered and marker in recovered.content and not scrapling._stealth_broken)

        # 状态演练只在此独立进程发生，不能注入真实服务中的业务域名。
        clock = [1000.0]
        with patch.object(health, "_recent", {}), patch.object(health.time, "time", side_effect=lambda: clock[0]):
            urls = ["https://verification.example/a", "https://verification.example/b", "https://verification.example/c"]
            health.record("article", urls, False)
            checks["same_domain_counted_once"] = not health.is_learned_anti_scrape(urls[0])
            health.record("firecrawl", urls, False)
            health.record("crawl4ai", urls, False)
            checks["three_failures_trigger_adjustment"] = health.is_learned_anti_scrape(urls[0])
            from types import SimpleNamespace
            checks["anti_scrape_engine_promoted"] = _effective_tier(SimpleNamespace(name="scrapling", tier=50), True) == 16

            app = FastAPI()
            app.include_router(router)
            app.dependency_overrides[get_current_user] = lambda: {"role": "user"}
            with TestClient(app) as client:
                checks["ordinary_user_cannot_release"] = client.delete("/api/config/domain-health/verification.example").status_code == 403
                app.dependency_overrides[get_current_user] = lambda: {"role": "admin"}
                checks["admin_can_view_adjustment"] = "verification.example" in client.get("/api/config/domain-health").json()["flagged"]
                checks["admin_can_release"] = client.delete("/api/config/domain-health/verification.example").status_code == 200 and not health.list_flagged()
            for _ in range(3):
                health.record("article", urls, False)
            clock[0] += 1801
            checks["thirty_minute_expiry"] = not health.list_flagged()

        if args.live:
            live = asyncio.run(scrapling._fetch_stealth("https://docs.python.org/3/tutorial/"))
            checks["live_public_page"] = bool(live and len(live.content) > 400 and "Python" in live.content)
        report = {"scope": "isolated_runtime_and_routes", "checks": checks, "passed": all(checks.values())}
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False), flush=True)
        if not report["passed"]:
            raise SystemExit(1)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
