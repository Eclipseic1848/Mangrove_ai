"""从来源获取事实判定初稿资格，供各领域的采集与导出共用。"""
import hashlib


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
            "ready_for_review": count > 0 and status in {"complete", "partial"},
            "target_complete": complete, "coverage_partial": partial,
            "message": {"failed": "来源获取失败，诊断快照已保留；未创建业务初稿。",
                        "empty": "本次已检索范围内未找到符合条件的资料；未创建业务初稿。",
                        "incomplete": "本次范围或数量要求尚未满足，已保留进度与缺口；未创建业务初稿。",
                        "partial": "已保留部分符合范围的资料，覆盖缺口见执行说明。",
                        "complete": "已达到本次数量目标；内容仍待核对，不代表穷尽来源。"}[status]
                       + "".join(action_messages[action] for action in actions)}
