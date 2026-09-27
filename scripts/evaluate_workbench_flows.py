"""通过实际工作台 HTTP 入口执行八个示例；创建的测试计划立即暂停，邮件止于缺地址澄清。"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import time
import uuid

import httpx

from scripts.evaluate_workspace_examples import save
from scripts.workspace_example_cases import KINDS, generate_cases, workspace_examples, write_sources


async def run(args):
    directory = args.output.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    account = json.loads(args.account_file.read_text(encoding="utf-8-sig"))
    async with httpx.AsyncClient(base_url=args.base_url, trust_env=False, timeout=240,
            headers={"Origin": args.base_url, "X-Mangrove-CSRF": "1"}) as client:
        response = await client.post("/api/auth/login", json={key: account[key] for key in ("username", "password")})
        response.raise_for_status()
        response = await client.get("/api/model-connections/preferences/default")
        response.raise_for_status()
        preference = response.json()["preference"]
        for kind, example in zip(KINDS, workspace_examples(), strict=True):
            if args.case and kind not in args.case:
                continue
            path = directory / (kind + ".json")
            if path.exists():
                continue
            record = {"kind": kind, "prompt": example["prompt"], "status": "inflight_unknown", "model": preference["model_id"]}
            started = time.monotonic()
            save(path, record)
            try:
                if kind in ("receipt", "merge_orders"):
                    record["entrypoint"] = "/api/semantic-workspace/tasks"
                    case = next(case for case in generate_cases() if case["id"] == kind + "/normal-1")
                    files = write_sources(case, directory / "sources" / kind)
                    upload_ids = []
                    for source in files:
                        with source.open("rb") as stream:
                            response = await client.post("/api/data-sources/uploads", files={"file": (source.name, stream)})
                        response.raise_for_status()
                        upload_ids.append(response.json()["upload_id"])
                    record["idempotency_key"] = uuid.uuid4().hex
                    save(path, record)
                    response = await client.post("/api/semantic-workspace/tasks", headers={"Idempotency-Key": record["idempotency_key"]}, json={
                        "objective_text": example["prompt"], "upload_ids": upload_ids,
                        "output_formats": ["json", "csv"] if kind == "receipt" else ["xlsx"],
                        "provider": "bound", "runtime_version": "pi", "permission_profile": "standard",
                        "model_connection_id": preference["connection_id"], "model_connection_model": preference["model_id"],
                        "external_api_confirmed": True})
                    record["http_status"] = response.status_code
                    response.raise_for_status()
                    detail = response.json()
                    detail = detail.get("task", detail)
                    record["task_id"] = detail["task_id"]
                    save(path, record)
                    while time.monotonic() - started < 240:
                        response = await client.get("/api/semantic-workspace/tasks/" + record["task_id"])
                        response.raise_for_status()
                        detail = response.json()
                        record["task"] = detail
                        if detail.get("status") in {"needs_input", "failed", "cancelled", "completed", "candidate_ready", "paused"}:
                            break
                        await asyncio.sleep(2)
                    draft = await client.get(f"/api/semantic-workspace/tasks/{record['task_id']}/draft")
                    record["draft_http_status"] = draft.status_code
                    record["draft"] = draft.json()
                    if detail.get("status") in {"needs_input", "failed", "cancelled", "completed", "candidate_ready", "paused"}:
                        record["status"] = "observed"
                    else:
                        record["status"] = "unknown_timeout"
                        response = await client.post(f"/api/semantic-workspace/tasks/{record['task_id']}/cancel")
                        record["cancel_http_status"] = response.status_code
                        response = await client.get("/api/semantic-workspace/tasks/" + record["task_id"])
                        record["after_cancel"] = response.json()
                else:
                    prompt = example["prompt"]
                    if kind in KINDS[:4]:
                        prompt += " 只处理文字，最多查看5条候选，总耗时上限120秒；来源不足请如实说明，不生成虚构内容。"
                    record["executed_prompt"] = prompt
                    record["entrypoint"] = "/api/semantic-workspace/draft/turns"
                    record["request_id"] = uuid.uuid4().hex
                    save(path, record)

                    async def consume():
                        async with client.stream("POST", record["entrypoint"], json={"request_id": record["request_id"], "text": prompt, "history": [],
                                "model": preference["model_id"], "model_connection_id": preference["connection_id"], "external_api_confirmed": True}) as response:
                            record["http_status"] = response.status_code
                            response.raise_for_status()
                            if "text/event-stream" not in response.headers.get("content-type", ""):
                                record["result"] = json.loads(await response.aread())
                                record["conv_id"] = record["result"].get("conv_id")
                                return
                            event = ""
                            async for line in response.aiter_lines():
                                if line.startswith("event: "):
                                    event = line[7:]
                                elif line.startswith("data: "):
                                    value = json.loads(line[6:])
                                    if event == "meta":
                                        record.update(conv_id=value["conv_id"], task_id=value.get("task_id"))
                                    elif event in ("error", "result"):
                                        record[event] = value
                                    save(path, record)

                    try:
                        await asyncio.wait_for(consume(), timeout=200)
                        record["status"] = "observed" if "result" in record else "failed"
                        schedule_id = (record.get("result") or {}).get("scheduled_task_id")
                        if schedule_id:
                            response = await client.patch("/api/tasks/" + schedule_id, json={"status": "paused"})
                            record["schedule_cleanup"] = {"scheduled_task_id": schedule_id, "http_status": response.status_code}
                            response.raise_for_status()
                    except TimeoutError:
                        record["status"] = "unknown_timeout"
                        if record.get("conv_id"):
                            response = await client.post(f"/api/chat/{record['conv_id']}/cancel")
                            record["cancel_http_status"] = response.status_code
            except Exception as error:
                # 取消或查询失败不能把尚未确认的执行终态改成确定失败。
                if not record["status"].startswith("unknown_"):
                    record["status"] = "unknown_transport" if isinstance(error, httpx.RequestError) else "failed"
                record["error_type"] = type(error).__name__
                if isinstance(error, httpx.HTTPStatusError):
                    record["http_status"] = error.response.status_code
                    record["detail"] = error.response.json()
            finally:
                record["elapsed_seconds"] = round(time.monotonic() - started, 3)
                save(path, record)
                print(json.dumps({key: record.get(key) for key in ("kind", "status", "http_status", "elapsed_seconds", "error_type")}, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--account-file", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:8088")
    parser.add_argument("--case", action="append", choices=KINDS)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
