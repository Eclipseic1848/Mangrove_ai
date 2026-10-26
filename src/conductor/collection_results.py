"""从来源获取事实判定初稿资格，供各领域的采集与导出共用。"""
import hashlib
import json
import re
from datetime import date, datetime


CANDIDATE_REASON_LABELS = {
    "content_type_unknown": "资料类型尚未确认", "content_type_mismatch": "与本次要求的资料类型不符",
    "publication_unknown": "发布时间尚未确认", "publication_outside_scope": "发布时间超出本次范围",
    "unrelated": "与本次采集主题不符", "relevance_unknown": "相关性仍待核对",
    "expired_rule": "原文规则已过期", "audience_mismatch": "与本次要求的客群不符",
    "audience_unknown": "适用客群尚未确认",
    "negative_evidence_unknown": "适用条件的依据不完整，仍待核对",
    "credit_card_only": "仅含本次范围外的信用卡权益",
    "interrupted_operation_outcome_unknown": "上次处理被中断，结果尚未确认",
    "interrupted_discovery_outcome_unknown": "上次检索被中断，结果尚未确认",
    "discovery_outcome_unknown": "候选检索结果尚未确认", "screen_outcome_unknown": "本篇初筛结果尚未确认",
    "extract_outcome_unknown": "本篇权益抽取结果尚未确认", "images_outcome_unknown": "本篇图片识别结果尚未确认",
    "comments_outcome_unknown": "本篇评论读取结果尚未确认", "step_timeout": "本篇处理超时，结果尚待核对",
    "model_output_truncated": "模型返回内容不完整", "screening_failed": "本篇初筛尚未完成",
    "image_incomplete": "图片资料尚未识别完整", "images_incomplete": "图片资料尚未识别完整",
    "comments_incomplete": "评论资料尚未读取完整", "evidence_processing_failed": "本篇证据处理尚未完成",
    "time_budget": "本次处理时间已用完", "ocr_limit": "已达到本次图片处理限额",
    "comment_source_unavailable": "评论来源暂时不可读取", "image_ocr_required": "图片仍待识别",
    "image_read_or_parse_failed": "图片读取或识别尚未完成", "raw_only": "已保留原始资料，尚未识别",
}
SEMANTIC_EXCLUSIONS = {"unrelated": ("relevant", False), "expired_rule": ("tag_stale", True),
                       "audience_mismatch": ("tag_audience_mismatch", True), "credit_card_only": ("credit_card_only", True)}
_DIAGNOSTIC_SECRET = re.compile(
    r"authorization|\bbearer\b|api[_ -]?key|xsec[_ -]?token|cookie|secret|traceback|\bprovider\b|http\s*[45]\d\d|sk-[\w-]{4,}|(?:password|session|token)\s*[:=]", re.I)


def collection_report_requested(state):
    spec = state.get("task_spec")
    scope = (spec.evidence_collection or spec.bank_benefits) if spec else None
    return bool(scope and not scope.raw_only and spec.wants_report())


def collection_report_ready(state):
    """报告只消费完整目标的独立入选笔记，不能用权益条数或部分证据填数量。"""
    if not collection_report_requested(state) or state.get("error"):
        return False
    spec = state["task_spec"]
    scope = spec.evidence_collection or spec.bank_benefits
    snapshot = state.get("evidence_collection") or state.get("bank_collection") or {}
    if snapshot.get("scope") != scope.model_dump(mode="json") or not collection_outcome(snapshot)["target_complete"]:
        return False
    selected = [note for note in snapshot.get("candidates", []) if note.get("status") == "selected"]
    groups = scope.queries if spec.evidence_collection else scope.banks
    identities = [(note.get("metadata") or {}).get("note_id") for note in selected]
    return (len(selected) == scope.target_count * len(groups)
        and all(isinstance(identity, str) and identity.strip() for identity in identities)
        and len(set(identities)) == len(identities)
        and all(sum(note.get("group", note.get("bank")) == group.name for note in selected) == scope.target_count for group in groups))


def collection_report_dataset(state):
    """分析原文与成功识别的图片，同时保留已核验的字段引用，不改写冻结快照。"""
    if not collection_report_ready(state):
        raise ValueError("完整独立笔记尚未达到报告要求")
    from src.data_prep.artifact_store import ArtifactStore
    snapshot = state.get("evidence_collection") or state["bank_collection"]
    store = ArtifactStore()
    result = []
    for note in snapshot["candidates"]:
        if note.get("status") != "selected":
            continue
        for image in note.get("images") or []:
            verified_image_bytes(store, state["task_id"], image)
        content = "\n\n".join(f"【{source_id}】\n{text}" for source_id, text in _readable_source_context(note).items())
        records = partial_evidence_records(note)
        if records:
            content += "\n\n已核验原文引用的结构化明细：\n" + json.dumps(records, ensure_ascii=False)
        result.append({"title": note.get("title") or "", "url": note.get("url") or "", "content": content,
            "metadata": {"note_id": note["metadata"]["note_id"], "group": note.get("group", note.get("bank"))}})
    return result


def analysis_context_capacity(state):
    """只采用冻结的已核云模型容量；自定义、本地及未绑定调用保持原限额。"""
    from src.llm.provider import _bound_chat_identity
    from src.memory._library_scope import execution_owner
    identity = _bound_chat_identity.get()
    model = state.get("model")
    owner = execution_owner()
    if (state.get("provider") != "bound" or not isinstance(identity, tuple)
            or len(identity) != 7 or not all(identity[:3]) or identity[0] != owner or identity[3] != model):
        return 0
    if "execution_binding" in state:
        # 调度会话名与授权任务名不同；必须核对完整冻结身份，不能退回名称匹配放行。
        expected = hashlib.sha256(json.dumps([owner, identity], ensure_ascii=False).encode("utf-8")).hexdigest()
        if state["execution_binding"] != expected:
            return 0
    elif identity[4] != (state.get("session_id") or state.get("task_id")):
        return 0
    from src.model_connections.broker import get_default_broker
    preset = get_default_broker().verified_model_preset(identity[0], identity[1], identity[2], model)
    # 字符数和 UTF-8 字节数都不等于实际令牌数；由供应商校验真实物理窗口并拒绝超限。
    return preset.context_window if preset and preset.context_window and preset.max_output_tokens else 0


def collection_candidate_counts(snapshot):
    """按冻结候选事实统计，不把待核对资料加入完整入选数量。"""
    candidates = [item for item in snapshot.get("candidates", []) if isinstance(item, dict)]
    selected = sum(item.get("status") == "selected" for item in candidates)
    excluded = sum(item.get("status") == "excluded" for item in candidates)
    review = sum(item.get("status") == "review_required" for item in candidates)
    groups = snapshot.get("groups", snapshot.get("banks", []))
    groups = groups if isinstance(groups, list) else []
    scope = snapshot.get("scope") or {}
    target = scope.get("target_count") if isinstance(scope, dict) else None
    return {"found": len(candidates), "selected": selected, "excluded": excluded,
            "review_required": review, "unfinished": len(candidates) - selected - excluded - review,
            "target_count": target * max(len(groups), 1) if type(target) is int and target > 0 else None}


def _readable_source_context(note):
    from .evidence_collection import source_context
    metadata = note.get("metadata") or {}
    metadata = metadata if isinstance(metadata, dict) else {}
    comments = metadata.get("comments")
    comments = comments if isinstance(comments, list) else []
    images = note.get("images")
    images = images if isinstance(images, list) else []
    return source_context({
        "title": note.get("title") if isinstance(note.get("title"), str) else "",
        "content": note.get("content") if isinstance(note.get("content"), str) else "",
        "metadata": {**metadata, "comments": [item for item in comments
            if isinstance(item, dict) and item.get("comment_id") is not None and isinstance(item.get("content"), str)]},
        "images": [image for image in images if isinstance(image, dict)
            and image.get("status") == "recognized" and image.get("image_index") is not None
            and isinstance(image.get("text"), str)],
    })


def candidate_review_details(note, scope):
    """把受控原因和可重验的依据投影给用户，未知异常不进入产品提示。"""
    raw_reason = note.get("reason")
    code = raw_reason if isinstance(raw_reason, str) and raw_reason in CANDIDATE_REASON_LABELS else "unknown"
    label = CANDIDATE_REASON_LABELS.get(code, "符合本次采集范围" if note.get("status") == "selected"
        and not raw_reason else "尚未处理" if note.get("status") == "unprocessed" and not raw_reason else "尚待核对")
    result = {"reason_code": code, "reason": label, "explanation": None, "evidence": []}
    if code == "unknown":
        return result
    assessment = note.get("assessment")
    assessment = assessment if isinstance(assessment, dict) else {}
    sources = _readable_source_context(note)
    judgment = assessment.get("judgment_evidence")
    judgment = judgment if isinstance(judgment, dict) else {}
    refs = list(judgment.values())
    records = assessment.get("records", assessment.get("benefits"))
    for record in records if isinstance(records, list) else []:
        if isinstance(record, dict) and isinstance(record.get("evidence"), dict):
            refs.extend(record["evidence"].values())

    def verified(ref):
        source_id = ref.get("source_id") if isinstance(ref, dict) else None
        quote = ref.get("quote") if isinstance(ref, dict) else None
        return (isinstance(source_id, str) and len(source_id) <= 96 and isinstance(quote, str)
                and bool(quote) and not _DIAGNOSTIC_SECRET.search(quote) and quote in sources.get(source_id, ""))

    for ref in refs:
        if verified(ref):
            bounded = {"source_id": ref["source_id"], "quote": ref["quote"][:240]}
            if bounded not in result["evidence"]:
                result["evidence"].append(bounded)
            if len(result["evidence"]) == 4:
                break
    if note.get("status") == "excluded" and code in SEMANTIC_EXCLUSIONS:
        field, value = SEMANTIC_EXCLUSIONS[code]
        explanation = assessment.get("rank_reason")
        if assessment.get(field) is value and verified(judgment.get(field)) and isinstance(explanation, str) and not _DIAGNOSTIC_SECRET.search(explanation):
            result["explanation"] = " ".join(explanation.split())[:120] or None
    elif note.get("status") == "excluded" and code == "publication_outside_scope" and isinstance(scope, dict):
        metadata = note.get("metadata") or {}
        published = metadata.get("publish_time") if isinstance(metadata, dict) else None
        try:
            actual = datetime.fromisoformat(published)
            frozen = datetime.fromisoformat(scope["frozen_at"])
            start = date.fromisoformat(scope["publication_from"]) if scope.get("publication_from") else None
            end = date.fromisoformat(scope["publication_to"]) if scope.get("publication_to") else None
            day = actual.astimezone(frozen.tzinfo).date()
            if actual.tzinfo is not None and frozen.tzinfo is not None and ((start and day < start) or (end and day > end)):
                result["explanation"] = f"发布日期：{day}；本次范围：{start or '不限'}至{end or '不限'}"
                result["evidence"] = [{"source_id": "metadata", "quote": published[:240]}]
        except (KeyError, TypeError, ValueError, OverflowError):
            pass
    return result


def partial_evidence_records(note):
    """待核对明细再次验证原文引用，失败的图片文字不能成为字段证据。"""
    assessment = note.get("assessment") or {}
    if not isinstance(assessment, dict) or assessment.get("relevant") is False:
        return []
    records = assessment.get("records", assessment.get("benefits"))
    if not isinstance(records, list):
        return []
    from .evidence_collection import evidenced_records
    sources = _readable_source_context(note)
    result = []
    for record in records:
        if not isinstance(record, dict) or record.get("evidence_status") != "quoted_unreviewed":
            continue
        fields = [key for key in record if key not in {"evidence", "missing_reasons", "evidence_status", "review_required"}]
        result.extend(evidenced_records([record], fields, sources))
    return result


def verified_image_bytes(store, task_id, image):
    """恢复与导出都核验原件；不把新字节配上旧 OCR 和旧哈希。"""
    location = image.get("artifact_path")
    if not location:
        if image.get("status") in {"recognized", "downloaded"}:
            raise ValueError("图片证据缺少冻结原件")
        return None
    path = store.resolve_path(location)
    if not path.is_relative_to(store.task_dir(task_id).resolve()) or not path.is_file():
        raise ValueError("图片证据不属于当前任务")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != image.get("sha256"):
        raise ValueError("图片证据已变化，拒绝复用")
    return content


def collection_outcome(snapshot):
    scope = snapshot.get("scope") or {}
    groups = snapshot.get("groups", snapshot.get("banks", []))
    count = sum(item.get("status") == "selected" for item in snapshot.get("candidates", []))
    failed = any(group.get("stop_reason") == "collection_failed" for group in groups)
    complete = bool(groups) and all(group.get("stop_reason") == "target_reached" and
        sum(item.get("status") == "selected" and item.get("group", item.get("bank")) == group.get("name", group.get("bank"))
            for item in snapshot.get("candidates", [])) >= scope.get("target_count", 1) for group in groups)
    partial = any(batch.get("coverage", {}).get("partial") for group in groups for batch in group.get("batches", []))
    strict = scope.get("strictness") == "strict"
    action_messages = {
        "reauthenticate_selected_account": "请在采集账号设置中更新本任务实际使用的账号凭证，再验证身份；更新凭证不会自动续跑。",
        "complete_platform_challenge": "请通过来源平台正常登录完成验证，再重新验证采集凭证。",
        "wait_for_rate_limit": "请等待来源平台解除限流后再操作，不要反复更换凭证重试。",
        "inspect_collection_service": "请检查采集服务与网络；当前故障不能证明 Cookie 已过期。",
        "check_platform_access": "来源平台拒绝本次访问，请由采集账号维护者检查平台访问限制；基础登录正常不代表搜索可用。",
        "install_collection_dependencies": "采集环境缺少运行依赖，请联系管理员补齐采集组件。",
        "install_collection_browser": "采集所需浏览器未安装或版本不匹配，请联系管理员安装匹配的浏览器。",
        "configure_collection_headless": "采集浏览器需要桌面环境，请联系管理员将服务器采集配置为无头模式。",
        "check_collection_network": "采集网络连接失败，请检查服务器网络、DNS 和代理配置；不能据此判断 Cookie 过期。",
    }
    actions = sorted({operation.get("next_action") for group in groups for batch in group.get("batches", [])
        for operation in batch.get("coverage", {}).get("operations", {}).values()
        if operation.get("next_action") in action_messages})
    if not count:
        exhausted = bool(groups) and all(group.get("stop_reason") == "source_exhausted" for group in groups)
        unresolved = any(item.get("status") in {"review_required", "unprocessed"} for item in snapshot.get("candidates", []))
        status = "failed" if failed else "empty" if exhausted and not unresolved else "incomplete"
    elif strict and (not complete or partial):
        status = "incomplete"
    else:
        status = "complete" if complete and not partial else "partial"
    return {"status": status, "selected_count": count, "source_actions": actions,
            "candidate_counts": collection_candidate_counts(snapshot),
            "ready_for_review": count > 0 and status in {"complete", "partial"},
            "target_complete": complete, "coverage_partial": partial,
            "message": {"failed": "来源获取失败，诊断快照已保留；未创建业务初稿。",
                        "empty": "本次已检索范围内未找到符合条件的资料；未创建业务初稿。",
                        "incomplete": "本次范围或数量要求尚未满足，已保留进度与缺口；未创建业务初稿。",
                        "partial": "已保留部分符合范围的资料，覆盖缺口见执行说明。",
                        "complete": "已达到本次数量目标；内容仍待核对，不代表穷尽来源。"}[status]
                       + "".join(action_messages[action] for action in actions)}
