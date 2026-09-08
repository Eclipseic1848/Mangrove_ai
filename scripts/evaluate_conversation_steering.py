# -*- coding: utf-8 -*-
"""用真实当前模型重复评测冻结的对话语义语料。"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest.mock import patch
import uuid

from src.conversation_steering import (
    RawUserTurn,
    SemanticDiffGate,
    SteeringRequest,
    build_context_rewriter,
)


async def evaluate_case(case: dict, semaphore: asyncio.Semaphore) -> dict:
    async with semaphore:
        request = SteeringRequest(
            owner_id="evaluation-owner",
            task_id=f"evaluation-{case['id']}",
            revision=2,
            run_id="evaluation-run",
            text=case["text"],
            current_status="running",
            status_summary="正在检查来源，已检查 2 个文件",
            current_goal=case.get(
                "current_goal",
                "处理附件一，提取王总的全部报销记录并输出 JSON",
            ),
            selection_reason="扫描页需要 OCR；数字页直接读取文本层",
            event_summaries=("已理解任务", "正在检查来源"),
            provider="local",
        )
        turn = RawUserTurn(
            turn_id=f"turn-{uuid.uuid4().hex[:12]}",
            owner_id=request.owner_id,
            task_id=request.task_id,
            revision=request.revision,
            text=request.text,
        )
        delta = await build_context_rewriter(request).rewrite(turn, request)
        actual_action = SemanticDiffGate.classify(delta).value
        actual_material = list(SemanticDiffGate.material_changes(delta))
        checks = {
            "intent": delta.intent.value == case["intent"],
            "action": actual_action == case["action"],
        }
        if case["intent"] in {"normalization", "task_refinement", "permission_request"}:
            checks["material_changes"] = (
                actual_material == case["material_changes"]
            )
        if case["action"] == "answer_only":
            checks["direct_answer"] = bool(delta.direct_answer)
        if case["intent"] == "status_question":
            checks["grounded_answer"] = bool(
                delta.direct_answer
                and (
                    "检查来源" in delta.direct_answer
                    or "2 个文件" in delta.direct_answer
                    or "2个文件" in delta.direct_answer
                )
            )
        if case.get("grounding_markers"):
            checks["grounded_answer"] = bool(
                delta.direct_answer
                and any(
                    marker in delta.direct_answer
                    for marker in case["grounding_markers"]
                )
            )
        return {
            "id": case["id"],
            "passed": all(checks.values()),
            "checks": checks,
            "expected": case,
            "actual": {
                "intent": delta.intent.value,
                "action": actual_action,
                "material_changes": actual_material,
                "normalized_text": delta.normalized_text,
                "direct_answer": delta.direct_answer,
                "open_questions": list(delta.open_questions),
            },
        }


async def run(args: argparse.Namespace) -> int:
    if args.mode == "progressive":
        return await run_progressive(args)
    cases = json.loads(args.fixture.read_text(encoding="utf-8"))
    rounds: list[dict] = []
    for round_number in range(1, args.rounds + 1):
        semaphore = asyncio.Semaphore(args.concurrency)
        results = await asyncio.gather(
            *(evaluate_case(case, semaphore) for case in cases)
        )
        passed = sum(item["passed"] for item in results)
        rounds.append(
            {
                "round": round_number,
                "passed": passed,
                "total": len(results),
                "results": results,
            }
        )
        print(f"round {round_number}: {passed}/{len(results)}", flush=True)
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fixture": str(args.fixture),
        "rounds": rounds,
        "all_passed": all(item["passed"] == item["total"] for item in rounds),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return 0 if report["all_passed"] else 1


def check_progressive_request(request, *, endpoint, model, sent, max_calls):
    """在真实 HTTP 发送前核精确目的地和预算，重复尝试不获得新额度。"""
    from src.model_connections.catalog import model_max_output_tokens
    if str(request.url) != endpoint or request.method != "POST":
        raise ValueError("评测请求偏离已冻结端点")
    if sent >= max_calls or len(request.content) > 65536:
        raise ValueError("评测请求超出冻结预算")
    body = json.loads(request.content)
    if body.get("model") != model or body.get("max_tokens") != model_max_output_tokens(model):
        raise ValueError("评测请求模型或输出预算不一致")


def progressive_rewriter(args):
    """仅注入本次连接配置，不读取全局供应商配置，推理仍用原实现。"""
    from src.conversation_steering import rewriter as implementation
    from src.llm.provider import ResolvedModelConnection

    provider_name = getattr(args, "provider", "local")
    api_key = os.environ.get("MANGROVE_EVAL_API_KEY")
    if provider_name != "local" and not api_key:
        raise ValueError("云端评测必须显式注入本轮获准的API Key")
    connection = ResolvedModelConnection(provider=provider_name, requested_model=args.model,
        model=args.model, base_url=args.base_url.rstrip("/"),
        api_key=api_key or "local-evaluation",
        trust_env=False, timeout=getattr(args, "timeout_seconds", 90), extra_body=None)
    provider = SimpleNamespace(resolve_model=lambda *unused, **kwargs: connection)
    with patch.object(implementation, "get_provider", return_value=provider):
        return implementation.InstructorContextRewriter(provider=provider_name, model=args.model)


async def run_progressive(args: argparse.Namespace) -> int:
    """合成资料经真实检查器及产品转写器；语义仍须逐项审阅实际输出。"""
    from src.model_connections.catalog import model_max_output_tokens
    timeout_seconds = getattr(args, "timeout_seconds", 90)
    if type(timeout_seconds) is not int or not 10 <= timeout_seconds <= 600:
        raise ValueError("评测单次超时必须为10到600秒的有限整数")
    fixture_bytes = args.fixture.read_bytes()
    fixture = json.loads(fixture_bytes)
    cases = fixture["cases"]
    if (fixture.get("synthetic_only") is not True or len(cases) != 18
            or sum(len(case["turns"]) for case in cases) != 24
            or args.rounds != 1 or args.concurrency != 1):
        raise ValueError("本票固定评测须为18例24轮，单轮且串行")
    if not args.base_url or not args.model:
        raise ValueError("必须明确模型和端点，不能采用全局默认")
    provider_name = getattr(args, "provider", "local")
    url = urlsplit(args.base_url)
    local = url.hostname == "localhost"
    if not local:
        try:
            local = ipaddress.ip_address(url.hostname or "").is_private
        except ValueError:
            local = False
    if provider_name == "deepseek":
        # 云端只允许本轮明确选定的官方端点，不能把凭据发送到任意兼容地址。
        if args.base_url.rstrip("/") != "https://api.deepseek.com":
            raise ValueError("DeepSeek评测仅允许明确的官方HTTPS端点")
    elif (provider_name != "local" or not local or url.scheme not in {"http", "https"}
            or url.username or url.password or url.query or url.fragment or url.path.rstrip("/") != "/v1"):
        raise ValueError("须提供明确本地OpenAI兼容/v1端点，不接受凭据或查询串")
    endpoint = args.base_url.rstrip("/") + "/chat/completions"
    report = {
        "mode": "progressive", "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "provider": provider_name, "model": args.model, "endpoint": endpoint, "max_calls": 24,
        "max_output_tokens": model_max_output_tokens(args.model), "max_input_bytes": 65536,
        "output_limit_policy": "verified_model_max_or_deployment_default",
        "timeout_seconds": timeout_seconds, "sdk_retries": 0, "requests_sent": 0,
        "semantic_review_required": True, "all_passed": False, "results": [],
        "status": "prepared" if not args.execute else "running",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    # 每轮使用新证据文件，不能覆盖失败记录后挑选最好结果。
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2)
    if not args.execute:
        print("Prepared: 18 cases / 24 turns; no model requests")
        return 0

    import httpx
    from src.conversation_steering import rewriter as implementation
    from src.semantic_harness.inspectors.uploads import UploadSourceInspector, public_source_findings
    from src.services.upload_store import UploadStore

    report["source_sha256"] = {
        name: hashlib.sha256((Path(__file__).resolve().parents[1] / name).read_bytes()).hexdigest()
        for name in (
            "scripts/evaluate_conversation_steering.py",
            "src/conversation_steering/rewriter.py", "src/conversation_steering/models.py",
            "src/conversation_steering/service.py", "src/conversation_steering/prompts/rewrite-v1.md",
            "src/model_connections/catalog.py", "src/model_connections/text_protocol.py",
            "src/semantic_harness/inspectors/uploads.py",
        )
    }

    def save():
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    original_checkpoint = implementation.execution_http_checkpoint_async
    original_client = implementation.AsyncOpenAI
    active = {}

    async def checkpoint(message):
        await original_checkpoint(message)
        if isinstance(message, httpx.Request):
            if active.get("sent"):
                raise ValueError("同一评测回合禁止再次发送")
            check_progressive_request(message, endpoint=endpoint, model=args.model,
                                      sent=report["requests_sent"], max_calls=24)
            active["sent"] = True
            active["request_sha256"] = hashlib.sha256(message.content).hexdigest()
            active["started_at"] = datetime.now(timezone.utc).isoformat()
            report["requests_sent"] += 1
            # 发送前落盘；中断后保留结果未知，不允许自动重跑。
            active["outcome"] = "unknown"
            save()
        else:
            active["http_status"] = message.status_code
            active["responded_at"] = datetime.now(timezone.utc).isoformat()
            response_bytes = await message.aread()
            active["response_received"] = True
            active["response_body_received_at"] = datetime.now(timezone.utc).isoformat()
            try:
                payload = json.loads(response_bytes)
                active["usage"] = payload.get("usage")
                active["response_model"] = payload.get("model")
            except (ValueError, TypeError):
                active["usage"] = None

    def client_without_retries(**kwargs):
        kwargs["max_retries"] = 0
        return original_client(**kwargs)

    try:
        with tempfile.TemporaryDirectory(prefix="mangrove-clarification-eval-") as temp:
            uploads = UploadStore(temp, max_bytes=65536)
            owner = "evaluation-owner"
            items = {
                name: uploads.save_bytes(owner, source["filename"], source["content"].encode("utf-8"),
                                         media_type="text/csv" if source["filename"].endswith(".csv") else "text/plain",
                                         verify_magic=True)
                for name, source in fixture["sources"].items()
            }
            inspector = UploadSourceInspector(user_id=owner, upload_store=uploads)
            findings = {name: public_source_findings(inspector.inspect_artifacts((item.upload_id,)))
                        for name, item in items.items()}
            if any(not values or any(value["status"] != "ready" for value in values) for values in findings.values()):
                raise ValueError("合成来源未完成真实检查，禁止模型评测")
            report["source_findings"] = findings
            language = progressive_rewriter(args)
            # 只在隔离评测进程中对齐既有编译超时设置，防止客户端被较小默认值截断。
            with patch.object(implementation.settings, "semantic_compiler_timeout_seconds", timeout_seconds), \
                    patch.object(implementation, "AsyncOpenAI", client_without_retries), \
                    patch.object(implementation, "execution_http_checkpoint_async", checkpoint):
                for case in cases:
                    history, previous = [], None
                    for index, row in enumerate(case["turns"]):
                        active = {"case_id": case["id"], "turn": index + 1, "usage": None,
                                  "expected": row["expected"], "text": row["text"], "outcome": "not_sent"}
                        report["results"].append(active)
                        turn = RawUserTurn(turn_id=f"eval-{case['id']}-{index + 1}", owner_id=owner,
                            task_id=f"evaluation-{case['id']}", revision=1, text=row["text"])
                        request = SteeringRequest(owner_id=owner, task_id=turn.task_id, revision=1,
                            run_id="evaluation-run", text=turn.text, current_status="needs_input",
                            current_goal=case["current_goal"], provider=provider_name, model=args.model,
                            external_api_confirmed=provider_name != "local",
                            source_findings=tuple(value for name in case["sources"] for value in findings[name]),
                            relevant_turns=tuple(history), prior_delta=previous,
                            clarification_question=previous.open_questions[0] if previous and previous.open_questions else None,
                            clarification_round_id=f"evaluation-round-{index}" if previous and previous.open_questions else None)
                        async with asyncio.timeout(timeout_seconds):
                            delta = await language.rewrite(turn, request)
                        action = SemanticDiffGate.classify(delta).value
                        expected = row["expected"]
                        active.update(outcome="returned", actual=delta.model_dump(mode="json"),
                            checks={"action": action == expected["action"],
                                    "question_count": len(delta.open_questions) == int(expected["requires_clarification"]),
                                    "no_new_source": not delta.source_scope_delta,
                                    "no_new_permission": not delta.permission_delta,
                                    "turn_identity": delta.source_turn_ids == tuple(item.turn_id for item in (*history, turn)),
                                    "response_model": active.get("response_model") == args.model},
                            action=action, semantic_review="pending")
                        history.append(turn)
                        previous = delta
                        save()
        report["status"] = "awaiting_semantic_review"
        report["automated_passed"] = all(all(row["checks"].values()) for row in report["results"])
        save()
        print(f"Returned {len(report['results'])}/24; semantic review required")
        return 0 if report["automated_passed"] else 1
    except Exception as exc:
        # 不输出SDK异常正文，避免将凭据、URL参数或模型原始响应带入日志。
        report.update(status="stopped", error_type=type(exc).__name__)
        if active.get("outcome") == "unknown" and active.get("response_received"):
            active["outcome"] = "unusable_response"
        attempted = {(row["case_id"], row["turn"]) for row in report["results"] if row.get("sent")}
        report["not_run"] = [{"case_id": case["id"], "turn": index + 1}
            for case in cases for index in range(len(case["turns"]))
            if (case["id"], index + 1) not in attempted]
        save()
        print(f"Stopped: {type(exc).__name__}; partial evidence saved; no automatic retry")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("legacy", "progressive"), default="legacy")
    parser.add_argument("--base-url")
    parser.add_argument("--provider", choices=("local", "deepseek"), default="local")
    parser.add_argument("--model")
    parser.add_argument("--timeout-seconds", type=int, default=90)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=Path("tests/fixtures/conversation_steering/rewrite_cases.json"),
    )
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".artifacts/conversation-steering-evaluation.json"),
    )
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
