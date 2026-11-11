"""从来源获取事实判定初稿资格，供各领域的采集与导出共用。"""
import hashlib
import json
import re
from datetime import date, datetime


IMAGE_FAILURE_LABELS = {
    "image_download_failed": "图片下载失败，已保留取得的资料并停止后续处理",
    "image_decode_failed": "图片格式无法读取，已保留取得的资料并停止后续处理",
    "image_ocr_unavailable": "图片识别服务尚未配置，已保留图片并停止后续处理",
    "image_ocr_service_failed": "图片识别服务未完成本次处理，已保留图片并停止后续处理",
    "image_ocr_no_text": "图片已取得，但识别服务未返回可用文字，已停止后续处理",
}
CANDIDATE_REASON_LABELS = {
    **IMAGE_FAILURE_LABELS,
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
    "operation_timeout": "来源或处理服务响应超时，已保留资料并停止后续处理",
    "time_budget": "本次处理时间已用完", "ocr_limit": "已达到本次图片处理限额",
    "comment_source_unavailable": "评论来源暂时不可读取", "image_ocr_required": "图片仍待识别",
    "image_read_or_parse_failed": "图片读取或识别失败，已保留取得的资料并停止后续处理", "raw_only": "已保留原始资料，尚未识别",
    "target_reached": "已达到本次采集目标", "candidate_limit": "已达到本次候选检索限额",
    "source_exhausted": "本次来源检索已结束", "no_new_candidates": "本批未返回新的候选资料",
    "collection_failed": "来源获取尚未完成",
    "source_access_stopped": "已保留取得的资料，来源读取中断，尚未完成核对",
}
SEMANTIC_EXCLUSIONS = {"unrelated": ("relevant", False), "expired_rule": ("tag_stale", True),
                       "audience_mismatch": ("tag_audience_mismatch", True), "credit_card_only": ("credit_card_only", True)}
_DIAGNOSTIC_SECRET = re.compile(
    r"authorization|\bbearer\b|api[_ -]?key|xsec[_ -]?token|cookie|secret|traceback|\bprovider\b|http\s*[45]\d\d|sk-[\w-]{4,}|(?:password|session|token)\s*[:=]", re.I)
_STARTUP_STEP_LABELS = {
    "init_script": "初始化采集浏览器",
    "new_page": "创建采集页面",
    "homepage_navigation": "打开小红书首页",
    "create_client": "初始化小红书采集客户端",
    "driver_shutdown": "关闭采集浏览器",
}


def collection_running_explanation(diagnostic):
    """进行中仅展示固定步骤，不把第三方异常或地址带入页面。"""
    if diagnostic.get("search_started") is not False:
        return None
    steps = diagnostic.get("startup_steps")
    if isinstance(steps, dict):
        for step, label in reversed(_STARTUP_STEP_LABELS.items()):
            entry = steps.get(step)
            if step != "driver_shutdown" and isinstance(entry, dict) and entry.get("status") == "started":
                return f"正在{label}，搜索尚未开始"
    return {"browser_starting": "正在启动采集浏览器，搜索尚未开始",
            "login_check": "正在确认采集账号登录状态，搜索尚未开始"}.get(diagnostic.get("runtime_phase"))


def _startup_stop_explanation(diagnostic):
    steps = diagnostic.get("startup_steps")
    if not isinstance(steps, dict):
        return None
    unfinished = None
    for step, label in _STARTUP_STEP_LABELS.items():
        entry = steps.get(step)
        if not isinstance(entry, dict):
            continue
        if step == "driver_shutdown" and (unfinished or diagnostic.get("runtime_phase") not in (
                "browser_starting", "browser_ready")):
            # 收尾不能把先前的取消或登录问题改写成浏览器准备失败。
            continue
        status = entry.get("status")
        # 顺序固定，先发生的启动故障优先于后续清理失败。
        if status in ("timeout", "failed"):
            outcome = "超时" if status == "timeout" else "失败"
            return f"{label}{outcome}，搜索尚未开始；请联系管理员检查采集服务"
        if status in ("started", "cancelled"):
            unfinished = label
    if unfinished and diagnostic.get("termination") in ("cancelled", "timeout"):
        return f"{unfinished}未完成，搜索尚未开始；请联系管理员检查采集服务"
    return None


def collection_stop_explanation(group, diagnostic=None, credential_source=None):
    """客户提示只采用固定原因或受控采集失败说明，机器状态保持原值。"""
    reason = group.get("stop_reason")
    if group.get("done") is False and group.get("candidate_attempted") == 0:
        return "本组尚未开始采集"
    label = CANDIDATE_REASON_LABELS.get(reason, "本组处理已停止，结果尚待核对")
    if reason == "image_read_or_parse_failed":
        return IMAGE_FAILURE_LABELS.get(group.get("image_failure_reason"), label)
    failure = {}
    if reason == "collection_failed":
        failure = next((batch.get("coverage", {}).get("failure") or {} for batch in reversed(group.get("batches", []))
                        if batch.get("coverage", {}).get("failure")), {})
        message = group.get("message")
        # 未知异常或第三方日志不能借组级失败进入客户回复。
        if (failure.get("reason") in {"login_interaction_required", "login_required", "challenge_required", "rate_limited", "access_denied"}
                and isinstance(message, str) and 0 < len(message) <= 500 and not _DIAGNOSTIC_SECRET.search(message)):
            return message.strip()
    if (not isinstance(diagnostic, dict) or diagnostic.get("phase") != "discovery"
            or diagnostic.get("group_name") != group.get("name")):
        return label
    timed_or_unknown = (reason in {"discovery_outcome_unknown", "time_budget", "operation_timeout"}
                        or (reason == "collection_failed" and failure.get("reason") == "collector_timeout"))
    if ((timed_or_unknown or (reason == "collection_failed" and failure.get("reason") == "collection_failed"))
            and failure.get("rejection_confirmed") is not True and diagnostic.get("search_started") is False):
        if explanation := _startup_stop_explanation(diagnostic):
            return explanation
    if timed_or_unknown:
        if diagnostic.get("search_started") is True:
            return "搜索已发出，结果尚未确认；本次不会自动重试，请先核对采集服务状态"
        if diagnostic.get("search_started") is False:
            phase = diagnostic.get("runtime_phase")
            if diagnostic.get("browser_started") is True and (diagnostic.get("login_detected") is False
                    or phase in {"login_check", "login_cookie_injection", "login_interaction_required"}):
                action = {"platform": "请由超级管理员维护平台采集账号并重新验证",
                          "personal": "请在我的采集账号维护本人的登录信息并重新验证"}.get(
                              credential_source, "请先验证本次实际使用的采集账号")
                # 未确认登录不等于凭证失效，也不授予未知请求自动恢复资格。
                return "浏览器已启动，但未确认登录成功，搜索尚未开始；" + action
            if diagnostic.get("browser_started") is False and phase == "browser_starting":
                return "采集浏览器尚未完成启动，请联系管理员检查采集服务"
    return label


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
    if (any(group.get("stop_reason") in {"image_read_or_parse_failed", "images_outcome_unknown", "operation_timeout"} for group in groups)
            or any(note.get("reason") == "images_outcome_unknown" for note in snapshot.get("candidates", []))):
        # 图片停止门不因先前已有入选资料而放行后续模型或初稿创建。
        status = "incomplete"
    elif not count:
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
