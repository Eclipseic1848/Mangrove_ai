"""工作台八类示例的真实 Pi 初稿评测；合成来源与答案隔离，不发布结果。"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import fnmatch
import hashlib
import json
import mimetypes
from pathlib import Path
import socket
import sqlite3
import statistics
import time
import uuid

from scripts.workspace_example_cases import KINDS, generate_cases, grade_outputs, write_sources


def save(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def summarize(directory: Path, cases: list[dict]) -> dict:
    results = []
    for case in cases:
        path = directory / "results" / (case["id"] + ".json")
        result = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"status": "not_run"}
        results.append({"id": case["id"], "kind": case["kind"], "variant": case["variant"],
                        "status": result["status"], "passed": result.get("passed", False),
                        "structured_passed": result.get("structured_passed", False),
                        "elapsed_seconds": result.get("elapsed_seconds"), "checks": result.get("checks"),
                        "error_type": result.get("error_type"), "runtime_status": result.get("runtime_status")})
    counts = dict(Counter(result["status"] for result in results))
    by_kind = {}
    for kind in dict.fromkeys(case["kind"] for case in cases):
        selected = [result for result in results if result["kind"] == kind]
        by_kind[kind] = {"planned": len(selected), **dict(Counter(result["status"] for result in selected))}
    report = {"scope": "真实模型与 Pi 容器生成初稿；独立答案核验，不是正式交付或全链路验收",
              "planned": len(cases), "counts": counts, "structured_passed": sum(result["structured_passed"] for result in results),
              "by_kind": by_kind, "results": results}
    durations = sorted(result["elapsed_seconds"] for result in results if result["elapsed_seconds"] is not None)
    report["timing"] = {"completed": len(durations), "sum_seconds": round(sum(durations), 3),
                        "median_seconds": statistics.median(durations) if durations else None,
                        "p95_seconds": durations[max(0, (95 * len(durations) + 99) // 100 - 1)] if durations else None}
    save(directory / "report.json", report)
    lines = ["# 工作台示例泛化执行结果", "", report["scope"], "",
             "| 示例类型 | 计划 | 通过 | 失败 | 未运行/未知 |", "|---|---:|---:|---:|---:|"]
    for kind, count in by_kind.items():
        passed, failed = count.get("passed", 0), count.get("failed", 0)
        lines.append(f"| {kind} | {count['planned']} | {passed} | {failed} | {count['planned'] - passed - failed} |")
    lines += ["", "采集类使用冻结快照，报告语义仍需复核；定时与邮件只生成确认初稿，其执行链另测。", "",
              "失败与未知保留原始结果，不通过重复运行抹掉失败。"]
    (directory / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


def finalize(directory: Path, runs: list[Path], cases: list[dict] | None = None) -> dict:
    """按完整用例匹配唯一执行，不按通过与否挑选重试结果。"""
    cases = generate_cases() if cases is None else cases
    directory = directory.resolve()
    if any(directory == source.resolve() for source in runs):
        raise ValueError("汇总目录不能覆盖原始执行证据")
    frozen = [(source, {case["id"]: case for case in json.loads((source / "cases.json").read_text(encoding="utf-8"))}) for source in runs]
    directory.mkdir(parents=True, exist_ok=True)
    save(directory / "cases.json", cases)
    for case in cases:
        matched = [(source, source / "results" / (case["id"] + ".json")) for source, corpus in frozen
                   if corpus.get(case["id"]) == case and (source / "results" / (case["id"] + ".json")).is_file()]
        if len(matched) > 1:
            raise ValueError("同一冻结用例有多次执行，须明确评测批次：" + case["id"])
        record = {"id": case["id"], "status": "not_run", "passed": False}
        if matched:
            source, path = matched[0]
            record = json.loads(path.read_text(encoding="utf-8"))
            record["evidence_run"] = str(source.resolve())
            record["passed"] = False
            unknown = record["status"] == "inflight_unknown" or record["status"].startswith("unknown_")
            if not unknown:
                record["status"] = "failed"
            record["structured_passed"] = False
            if record.get("workspace_root"):
                record["workspace_root"] = str((source / record["workspace_root"]).resolve())
            # 未知执行即使留下文件，也不能推定已成功完成。
            if not unknown and record.get("workspace_root") and record.get("runtime_status") in {"needs_input", "candidate_ready"} and "output_sha256" in record:
                output = source / record["workspace_root"] / "output"
                current_hashes = {item.name: hashlib.sha256(item.read_bytes()).hexdigest() for item in output.iterdir()
                                  if item.is_file() and item.name != "candidate-manifest.json"}
                if current_hashes != record["output_sha256"]:
                    raise ValueError("产物与执行时哈希不匹配：" + case["id"])
                record.update(grade_outputs(case, output))
                if case["kind"] in KINDS[:4]:
                    review_path = source / "semantic-reviews" / (case["id"] + ".json")
                    reports = list(output.glob("*.md"))
                    if review_path.is_file() and len(reports) == 1:
                        review = json.loads(review_path.read_text(encoding="utf-8"))
                        valid = (review.get("id") == case["id"] and review.get("report_sha256") == hashlib.sha256(reports[0].read_bytes()).hexdigest()
                                 and all(type(review.get(key)) is bool for key in ("grounded", "covers_selected", "bounded")))
                        if not valid:
                            raise ValueError("语义复核与当前产物不匹配：" + case["id"])
                        record["semantic_review"] = review
                        record["semantic_report_review"] = "completed"
                        record["passed"] = record["structured_passed"] and all(review[key] for key in ("grounded", "covers_selected", "bounded"))
                ready = any(event["type"] == "draft.ready" for event in record.get("events", []))
                record["passed"] = record["passed"] and ready
                record["status"] = ("passed" if record["passed"] else "needs_review"
                    if ready and record["structured_passed"] and record.get("semantic_report_review") == "manual_review_required" else "failed")
        record["case_sha256"] = hashlib.sha256(json.dumps(case, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
        save(directory / "results" / (case["id"] + ".json"), record)
    return summarize(directory, cases)


async def run(args) -> dict:
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    cases = generate_cases()
    corpus = directory / "cases.json"
    if corpus.exists() and json.loads(corpus.read_text(encoding="utf-8")) != cases:
        raise ValueError("用例已变化，请使用新的输出目录，不能覆盖已有评测口径")
    save(corpus, cases)
    selected = [case for case in cases if not args.case or any(fnmatch.fnmatchcase(case["id"], pattern) for pattern in args.case)]
    if not selected:
        raise ValueError("没有匹配的用例")
    for case in selected:
        source_dir = directory / "sources" / case["id"]
        if not source_dir.exists():
            write_sources(case, source_dir)
    summarize(directory, cases)
    if not args.live:
        return summarize(directory, cases)
    if args.account_file is None:
        raise ValueError("真实执行需要 --account-file；凭据仅在本进程读取")

    import httpx
    import uvicorn
    from fastapi import FastAPI
    from src.account_execution import execution_context
    from src.agentic_runtime.models import PiRuntimeRequest, RuntimeTaskConfig, RuntimeVersion, SourceInput
    from src.agentic_runtime.pi_runtime import PiRuntime
    from src.agentic_runtime.repository import AgenticRuntimeRepository
    from src.api import auth
    from src.api.routes.document_tools import get_document_tool_broker, router as document_router
    from src.api.routes.model_connections import get_connection_broker
    from src.api.routes.model_relay import router as model_router
    from src.api.store import WebUIStore
    from src.config.settings import settings
    from src.model_connections.broker import get_default_broker

    account = json.loads(args.account_file.read_text(encoding="utf-8-sig"))
    broker = get_default_broker()
    if getattr(args, "local_auth", False):
        # 主入口未运行时只用于隔离 Runtime 评测；不冒充 HTTP 登录或工作台验收。
        user = auth.get_store().get_user_by_name(account["username"])
        if user is None or not auth.verify_password(account["password"], user["password_hash"]):
            raise PermissionError("评测账号无效")
        owner = user["user_id"]
        auth.get_store().capture_account_execution(owner)
        preference = broker.get_usage_preference(owner, allow_local=False)
        if not preference or not preference["available"]:
            raise ValueError("评测账号没有可用模型连接")
    else:
        with httpx.Client(base_url=args.base_url, trust_env=False, timeout=20,
                          headers={"Origin": args.base_url, "X-Mangrove-CSRF": "1"}) as client:
            response = client.post("/api/auth/login", json={key: account[key] for key in ("username", "password")})
            response.raise_for_status()
            owner = response.json()["user_id"]
            response = client.get("/api/model-connections/preferences/default")
            response.raise_for_status()
            preference = response.json()["preference"]
    connection = broker.freeze_connection(owner, preference["connection_id"])
    database = directory / "isolated.db"
    if not database.exists():
        # 只读备份保留账号授权；评测任务、初稿与事件写入本地隔离副本。
        with sqlite3.connect(Path(settings.webui_db_path).resolve().as_uri() + "?mode=ro", uri=True) as source, sqlite3.connect(database) as target:
            source.backup(target)
    original_store = auth.get_store()
    auth._store = WebUIStore(str(database))
    repository = AgenticRuntimeRepository(database)
    app = FastAPI()
    app.include_router(model_router)
    app.include_router(document_router)
    app.dependency_overrides[get_connection_broker] = lambda: broker
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    runtime = PiRuntime(execution_root=directory / "execution", timeout_seconds=args.timeout,
                        connection_broker=broker, state_store=repository, configure_as_default_document_broker=False,
                        relay_base_url=f"http://127.0.0.1:{port}/internal/model-relay",
                        document_relay_base_url=f"http://127.0.0.1:{port}/internal/document-tools",
                        draft_review_required=lambda request: True)
    app.dependency_overrides[get_document_tool_broker] = lambda: runtime._document_tool_broker
    server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False))
    server_task = asyncio.create_task(server.serve(sockets=[sock]))
    semaphore = asyncio.Semaphore(args.concurrency)

    async def execute(case):
        async with semaphore:
            path = directory / "results" / (case["id"] + ".json")
            if path.exists():
                previous = json.loads(path.read_text(encoding="utf-8"))
                if previous["status"] == "inflight_unknown":
                    raise RuntimeError("存在结果未知的执行，须核对容器和状态后另建评测目录")
                return
            task_id = "workspace_eval_" + uuid.uuid4().hex[:16]
            run_id = "pi_run_" + uuid.uuid4().hex[:16]
            source_dir = directory / "sources" / case["id"]
            sources = tuple(SourceInput(upload_id=f"synthetic-{index}", original_name=source["name"],
                            host_path=source_dir / source["name"], sha256=hashlib.sha256((source_dir / source["name"]).read_bytes()).hexdigest(),
                            media_type=mimetypes.guess_type(source["name"])[0] or "application/octet-stream")
                            for index, source in enumerate(case["sources"]))
            request = PiRuntimeRequest(user_id=owner, task_id=task_id, revision=1, objective_text=case["prompt"],
                        requested_output_formats=tuple(case["formats"]), sources=sources, external_api_confirmed=True,
                        model_connection_id=connection.connection_id, model_connection_version=connection.connection_version,
                        model_connection_model=preference["model_id"])
            authorization = auth.get_store().capture_account_execution(owner)
            with execution_context(authorization):
                auth.get_store().create_semantic_workspace_task(owner, task_id=task_id, title=case["example"],
                    objective_text=request.objective_text, upload_ids=[], output_formats=case["formats"],
                    provider="bound", model=preference["model_id"], external_api_confirmed=True)
            repository.register(RuntimeTaskConfig(user_id=owner, task_id=task_id, revision=1, runtime_version=RuntimeVersion.PI))
            repository.update(owner, task_id, 1, request=request.model_dump(mode="json", exclude={"api_key"}))
            record = {"id": case["id"], "status": "inflight_unknown", "task_id": task_id, "run_id": run_id,
                      "started_at": datetime.now(timezone.utc).isoformat(), "model": preference["model_id"],
                      "runtime_image": runtime.image, "source_sha256": {source.original_name: source.sha256 for source in sources},
                      "events": [], "formal_delivery": False, "evidence_scope": case["evidence_scope"]}
            save(path, record)
            started = time.monotonic()

            async def event_sink(event):
                record["events"].append({"type": event.event_type, "elapsed_seconds": round(time.monotonic() - started, 3)})

            try:
                with execution_context(authorization):
                    result = await runtime.start(request, on_event=event_sink, run_id=run_id)
                record["runtime_status"] = result.status.value
                record["workspace_root"] = str(result.workspace_root.relative_to(directory))
                record["failure"] = result.failure
                record.update(grade_outputs(case, result.workspace_root / "output"))
                ready = result.status.value in {"needs_input", "candidate_ready"} and any(event["type"] == "draft.ready" for event in record["events"])
                record["passed"] = record["passed"] and ready
                record["status"] = ("passed" if record["passed"] else "needs_review"
                    if ready and record.get("structured_passed") and record.get("semantic_report_review") == "manual_review_required" else "failed")
            except Exception as error:
                record.update(status="failed", passed=False, error_type=type(error).__name__, error_code=getattr(error, "error_code", None))
            finally:
                record["elapsed_seconds"] = round(time.monotonic() - started, 3)
                save(path, record)
                summarize(directory, cases)
                print(json.dumps({key: record.get(key) for key in ("id", "status", "runtime_status", "elapsed_seconds", "error_type")}, ensure_ascii=False), flush=True)

    try:
        while not server.started:
            if server_task.done():
                await server_task
                raise RuntimeError("隔离中继未启动")
            await asyncio.sleep(.02)
        await asyncio.gather(*(execute(case) for case in selected))
    finally:
        server.should_exit = True
        await server_task
        auth._store = original_store
    return summarize(directory, cases)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--case", action="append", help="只执行匹配的用例，例如 receipt/*")
    parser.add_argument("--live", action="store_true", help="实际调用当前账号默认模型并启动 Pi 容器")
    parser.add_argument("--account-file", type=Path)
    parser.add_argument("--local-auth", action="store_true", help="用本机账号凭据验证后执行隔离 Runtime，不验证 HTTP 入口")
    parser.add_argument("--base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--concurrency", type=int, choices=(1, 2, 4), default=2)
    parser.add_argument("--merge-run", action="append", type=Path, help="仅重新判分并汇总已有批次，不发起模型调用")
    args = parser.parse_args()
    if args.merge_run and args.live:
        parser.error("--merge-run 不能与 --live 同时使用")
    report = finalize(args.output, args.merge_run) if args.merge_run else asyncio.run(run(args))
    print(json.dumps({"planned": report["planned"], "counts": report["counts"]}, ensure_ascii=False))
    if report["counts"].get("failed"):
        return 1
    if (args.live or args.merge_run) and any(status != "passed" and count for status, count in report["counts"].items()):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
