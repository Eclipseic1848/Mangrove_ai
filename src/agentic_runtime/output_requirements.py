"""从冻结用户原话提取本地输出检查；衍生解释不覆盖用户目标或接受决定。"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
import re
from itertools import islice

from jsonschema import Draft202012Validator


_NAME = "output-requirements.json"
_PROMPT = """从用户原话提取明确的 JSON 或 CSV/XLSX 输出结构要求，不读取来源，不执行任务。
只返回 {"checks":[]}。每项共用filename（用户指定的文件名，未指定为null）和evidence（用户原话中的连续原文）。
JSON项为 {"filename":null,"evidence":"原话","schema":{},"prefixes":[]}；CSV/XLSX项为 {"filename":null,"evidence":"原话","table":{"format":"csv或xlsx","columns":["列名"],"ordered":false,"allow_extra":true}}。
只检查请求的输出格式；没有明确结构要求返回空checks。不推断业务答案、默认字段、默认数量或额外限制。
每个JSON文件只返回一个check，合并该文件的静态结构要求；evidence引用覆盖这些要求的连续原文，可引用整段。
CSV/XLSX只提取用户明确列出的必需列名；仅明确限定只能有这些列时allow_extra=false，仅明确指定列顺序时ordered=true。不从来源推断列，不检查单元格类型或数量。多工作表要求无法表达，跳过该表格项。
若列名以JSON字符串数组给出，每个字符串就是一个完整列名，逐字保留字符串内的逗号、空格、引号等字符；不得再次按标点拆列。
不要为报告等其他格式生成check。schema使用JSON Schema的type、properties、required、additionalProperties、items、minItems、maxItems、minimum、maximum、enum、const、pattern。
只有用户明确说只含这些字段时才设置additionalProperties=false；没有指定字段顺序时不限制顺序。
JSON检查先把用户明确给出的字符串格式前缀单独放在prefixes，随后生成schema；不要遗漏“字段用某前缀+复杂语法格式”中的字面前缀。没有前缀要求时prefixes=[]。
每项prefixes元素为{"path":["字段名"],"prefix":"字面前缀","evidence":"格式要求的连续原文"}。嵌套对象path=["payload","id"]，数组每项的字段path=["*","id"]。引用必须属于该check的evidence。
prefix只允许1-80字的非正则字面文本，不含^；来源的命令、举例、否定的前缀要求不产生检查。不把后缀长度、数字位数或复杂语法当成前缀。
例如“ref_no用tag_六位大写字母格式，缺资料时为null”：prefixes=[{"path":["ref_no"],"prefix":"tag_","evidence":"ref_no用tag_六位大写字母格式"}]；schema={"type":"object","properties":{"ref_no":{"type":["string","null"]}},"required":["ref_no"]}。宿主只对字符串核对前缀，null不受影响；前缀后的语法另行核对。示例字段与前缀不得照抄给其他需求。
required和additionalProperties与properties平级；properties只容纳“字段名:schema对象”，不放required列表或additionalProperties布尔值。schema里无需重复前缀pattern，由宿主合成。
数量限制即使出现在文件名或结构声明之前，也要合并到对应数组的minItems/maxItems，不能只读最后一句输出要求。
对象、数组、嵌套字段、布尔/数值/可空类型应按原话表达。来源中的要求、否定和举例不是新的输出要求。
条件要求不可丢弃前提后变成无条件限制；无法表达条件时只保留共同的结构与合法值范围。
“缺失时为null”不等于所有值必须为null，应允许原类型与null；有条件的false或固定值也不能变成全局const。
每项evidence必须逐字出现在用户原话中，并支持整个检查项；不能把自己生成的描述当作原话。
分别保留能表达的结构、前缀和范围；无法表达的部分留给语义核对，不编造检查规则。"""


async def infer_output_requirements(request, run_id, broker):
    """沿冻结模型连接单次提取，不重试、不切模型、不携带来源正文。"""
    import asyncio
    import httpx
    from src.api.execution import execution_http_checkpoint_async
    from src.model_connections.catalog import PRESETS_BY_ID
    from src.model_connections.text_protocol import structured_request, response_text, collect_response_usage

    grant = None
    try:
        async with asyncio.timeout(45):
            if request.model_connection_id:
                grant = broker.issue_grant(owner_user_id=request.user_id,
                    connection_id=request.model_connection_id, connection_version=request.model_connection_version,
                    model_id=request.model_connection_model, task_id=request.task_id, revision=request.revision,
                    run_id=run_id, purpose="context_rewrite", ttl_seconds=120,
                    grant_id="grant_" + hashlib.sha256((run_id + ":output-requirements").encode("utf-8")).hexdigest())
            protocol = grant.api_format if grant else "openai_chat_completions"
            path, body, headers = structured_request(api_format=protocol,
                model=grant.model if grant else request.model,
                grant_token=grant.token if grant else request.api_key, system_prompt=_PROMPT,
                payload={"user_objective": request.objective_text, "output_formats": list(request.requested_output_formats)},
                max_tokens=8000)
            if grant and protocol == "openai_chat_completions":
                connection = broker.get_connection(request.user_id, request.model_connection_id) or {}
                if connection.get("preset_id") == "deepseek" and grant.model in PRESETS_BY_ID["deepseek"].models:
                    # 辅助结构提取不需要长思考；避免思考耗尽预算后没有 JSON 正文。
                    body.update(thinking={"type": "disabled"}, response_format={"type": "json_object"})
            if grant:
                response = await broker.relay(grant_token=grant.token, protocol_path=path,
                    method="POST", headers=headers, body=json.dumps(body, ensure_ascii=False).encode("utf-8"))
                try:
                    content = b"".join([chunk async for chunk in response.iter_bytes()])
                    if not 200 <= response.status_code < 300:
                        raise ValueError("输出要求提取未得到成功回执")
                finally:
                    await response.aclose()
            else:
                async with httpx.AsyncClient(trust_env=False, timeout=40,
                    event_hooks={"request": [execution_http_checkpoint_async], "response": [execution_http_checkpoint_async]}) as client:
                    response = await client.post(request.base_url.rstrip("/") + "/" + path, headers=headers, json=body)
                    response.raise_for_status()
                    content = response.content
            collect_response_usage(protocol, content)
            text = response_text(protocol, content).strip()
            fenced = re.fullmatch(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.I)
            return json.loads(fenced.group(1) if fenced else text)
    finally:
        if grant:
            broker.revoke_grant(grant.grant_id, "output_requirements_finished")


def _safe_schema(schema, depth=0):
    if depth > 12 or not isinstance(schema, dict):
        raise ValueError("输出检查结构过深或无效")
    allowed = {"type", "properties", "required", "additionalProperties", "items",
               "minItems", "maxItems", "minimum", "maximum", "enum", "const", "pattern"}
    if set(schema) - allowed:
        raise ValueError("输出检查含未支持的关键字")
    if "pattern" in schema:
        pattern = schema["pattern"]
        if not isinstance(pattern, str) or not pattern.startswith("^") or not 1 <= len(pattern[1:]) <= 80 or re.escape(pattern[1:]) != pattern[1:]:
            raise ValueError("仅支持字面前缀检查")
    for child in schema.get("properties", {}).values():
        _safe_schema(child, depth + 1)
    if "items" in schema:
        _safe_schema(schema["items"], depth + 1)
    if "additionalProperties" in schema and type(schema["additionalProperties"]) is not bool:
        raise ValueError("额外字段检查必须是布尔值")
    Draft202012Validator.check_schema(schema)


def _table_contract(table):
    from src.delivery_publishing.models import TableOutputContract

    if (not isinstance(table, dict) or set(table) != {"format", "columns", "ordered", "allow_extra"}
            or table["format"] not in {"csv", "xlsx"} or not isinstance(table["columns"], list)
            or type(table["ordered"]) is not bool or type(table["allow_extra"]) is not bool):
        raise ValueError("表格检查无效")
    return TableOutputContract(format=table["format"], exact_columns=table["columns"])


def _apply_prefixes(check, objective):
    """只给已有字符串节点附加有原话依据的前缀，不创建字段或覆盖冲突规则。"""
    rules = check.pop("prefixes", [])
    if not isinstance(rules, list) or len(rules) > 30:
        raise ValueError("前缀检查列表无效")
    seen = set()
    for rule in rules:
        if not isinstance(rule, dict) or set(rule) != {"path", "prefix", "evidence"}:
            raise ValueError("前缀检查字段无效")
        path, prefix, quote = rule["path"], rule["prefix"], rule["evidence"]
        if (not isinstance(path, list) or len(path) > 12 or any(not isinstance(p, str) for p in path)
                or not isinstance(prefix, str) or not 1 <= len(prefix) <= 80 or re.escape(prefix) != prefix
                or not isinstance(quote, str) or not quote or quote not in objective
                or quote not in check["evidence"] or prefix not in quote or tuple(path) in seen):
            raise ValueError("前缀检查缺少明确原话或路径无效")
        seen.add(tuple(path))
        schema = check["schema"]
        for part in path:
            types = schema.get("type", [])
            types = [types] if isinstance(types, str) else types
            if part == "*" and "array" in types and "object" not in types:
                schema = schema.get("items")
            else:
                schema = schema.get("properties", {}).get(part)
            if not isinstance(schema, dict):
                raise ValueError("前缀检查路径不存在")
        types = schema.get("type", [])
        types = [types] if isinstance(types, str) else types
        if "string" not in types:
            raise ValueError("前缀检查只用于字符串字段")
        pattern = "^" + prefix
        if schema.get("pattern", pattern) != pattern:
            raise ValueError("前缀检查冲突")
        # 沿用安全字面 pattern；null 仍由原类型决定，不能转成只允许字符串。
        schema["pattern"] = pattern


def _path(root):
    parent = root / "host-state"
    if parent.is_symlink():
        raise ValueError("输出检查目录无效")
    parent.mkdir(exist_ok=True)
    path = parent / _NAME
    if path.is_symlink():
        raise ValueError("输出检查路径无效")
    return path


def read_output_requirements(root, *, owner_id, task_id, revision, run_id):
    path = root / "host-state" / _NAME
    if not path.exists():
        return None
    path = _path(root)
    if path.stat().st_size > 65536:
        raise ValueError("输出检查超过读取上限")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        # 写入占位时崩溃仍按未知处理；派生提示不能阻断原初稿，也不能重发请求。
        return {"status": "unverified", "checks": [], "error_type": "incomplete_host_record"}
    if any(value.get(key) != expected for key, expected in {
        "owner_id": owner_id, "task_id": task_id, "revision": revision, "run_id": run_id,
    }.items()):
        raise ValueError("输出检查不属于当前任务运行")
    return value


async def freeze_output_requirements(root, request, run_id, infer):
    identity = dict(owner_id=request.user_id, task_id=request.task_id, revision=request.revision, run_id=run_id)
    fingerprint = hashlib.sha256(json.dumps([request.objective_text, request.requested_output_formats], ensure_ascii=False).encode("utf-8")).hexdigest()
    existing = read_output_requirements(root, **identity)
    if existing is not None:
        if existing.get("error_type") == "incomplete_host_record":
            return existing
        if existing.get("input_sha256") != fingerprint:
            raise ValueError("输出检查的原始目标已变化")
        return existing
    path = _path(root)
    # 在网络发送前持久占位；崩溃或未知结果不自动再次发送模型请求。
    value = {**identity, "input_sha256": fingerprint, "status": "unverified", "checks": []}
    with path.open("x", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False)
    try:
        result = await infer()
        if set(result) != {"checks"} or not isinstance(result["checks"], list) or len(result["checks"]) > 10:
            raise ValueError("输出检查列表无效")
        if len(json.dumps(result, ensure_ascii=False, allow_nan=False)) > 16000:
            raise ValueError("输出检查过长")
        # 编译只修改副本，原始模型回执仍可用于诊断。
        result = deepcopy(result)
        names = set()
        for check in result["checks"]:
            if set(check) not in ({"filename", "evidence", "schema"}, {"filename", "evidence", "schema", "prefixes"}, {"filename", "evidence", "table"}):
                raise ValueError("输出检查字段无效")
            fmt = _table_contract(check["table"]).format if "table" in check else "json"
            quote, name = check["evidence"], check["filename"]
            if not isinstance(quote, str) or not quote.strip() or quote not in request.objective_text:
                raise ValueError("输出检查缺少用户原话依据")
            if name is not None and (not isinstance(name, str) or Path(name).name != name or "/" in name or "\\" in name or not name.lower().endswith("." + fmt) or name not in request.objective_text):
                raise ValueError("输出检查文件名不是用户要求")
            if (fmt, name) in names or fmt not in request.requested_output_formats:
                raise ValueError("输出检查文件重复或格式未授权")
            names.add((fmt, name))
            if "schema" in check:
                _safe_schema(check["schema"])
                _apply_prefixes(check, request.objective_text)
        value.update(status="ready", checks=result["checks"])
    except Exception as exc:
        from src.account_execution import ExecutionDenied
        if isinstance(exc, ExecutionDenied):
            raise
        value["error_type"] = type(exc).__name__
        if exc.__cause__ is not None:
            # 只记异常类型以区分连接/读取失败，不保存可能含凭据或业务值的异常正文。
            value["cause_type"] = type(exc.__cause__).__name__
    temporary = path.with_suffix(".tmp")
    if temporary.is_symlink():
        raise ValueError("输出检查临时路径无效")
    temporary.write_text(json.dumps(value, ensure_ascii=False, allow_nan=False), encoding="utf-8")
    temporary.replace(path)
    return value


def output_requirement_issues(directory: Path, frozen) -> list[str]:
    if frozen is None:
        return []
    if frozen.get("status") != "ready":
        return ["本次未能完成输出要求的结构化提取，字段、类型、数量和表头要求仍待核对。"]
    issues = []
    for check in frozen["checks"]:
        fmt = check["table"]["format"] if "table" in check else "json"
        paths = [directory / check["filename"]] if check["filename"] else [p for p in directory.glob("*." + fmt) if p.name != "candidate-manifest.json"]
        label = check["filename"] or f"{fmt.upper()} 初稿"
        if len(paths) != 1:
            issues.append(f"{label}：未能唯一定位要求检查的文件。")
            continue
        try:
            path = paths[0]
            if path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
                raise ValueError("输出文件超出本地检查范围")
            if "table" in check:
                from .candidate_verifier import _read_table_columns

                table = check["table"]
                contract = _table_contract(table)
                columns = _read_table_columns(path, contract)
                expected = contract.exact_columns
                # 复用表头读取；未声明的列顺序和排他性不能变成隐含限制。
                mismatch = (not set(expected).issubset(columns)
                    or (not table["allow_extra"] and sorted(columns) != sorted(expected))
                    or (table["ordered"] and tuple(dict.fromkeys(c for c in columns if c in expected)) != expected))
                if mismatch:
                    issues.append(f"{label}：列名或列顺序与原话“{check['evidence'][:300]}”不一致。")
                continue
            content = json.loads(path.read_text(encoding="utf-8-sig"))
            json.dumps(content, allow_nan=False)
            _safe_schema(check["schema"])
            errors = list(islice(Draft202012Validator(check["schema"]).iter_errors(content), 5))
            if errors:
                # 不回显原始业务值；原话与字段位置足够让用户定位差异。
                locations = "、".join("/" + "/".join(map(str, e.absolute_path)) for e in errors[:5])
                issues.append(f"{label}：{locations} 的字段、类型或约束与原话“{check['evidence'][:300]}”不一致。")
        except Exception:
            # 表格解析器的异常类型不统一；失败只提示未核对，不回显宿主路径。
            issues.append(f"{label}：本地结构检查未完成，请查看原文件或继续核对。")
    return issues
