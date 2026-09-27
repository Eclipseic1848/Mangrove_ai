"""分阶段读取的短期来源凭证：复用既有 Vault，绑定 Owner 和任务。"""
import json
from urllib.parse import parse_qs, urlencode, urlsplit

from src.config.secret_refs import load_vault
from src.config.settings import settings
from src.memory._library_scope import execution_owner


def seal_access(task_id, note_id, url):
    owner = execution_owner()
    if not owner or not task_id:
        return None
    return load_vault(settings.webui_db_path).encrypt(json.dumps(
        {"owner": owner, "task": task_id, "note": note_id, "url": url}, ensure_ascii=False))


def open_access(handle, task_id, note_id):
    if not handle or not execution_owner():
        raise ValueError("来源后续读取凭证不可用")
    data = json.loads(load_vault(settings.webui_db_path).decrypt(handle))
    if (data.get("owner"), data.get("task"), data.get("note")) != (execution_owner(), task_id, note_id):
        raise PermissionError("来源凭证与当前 Owner、任务或笔记不一致")
    parts = urlsplit(data["url"])
    if parts.scheme != "https" or parts.hostname not in {"www.xiaohongshu.com", "xiaohongshu.com"} or parts.username or parts.password or parts.port not in (None, 443):
        raise ValueError("来源凭证地址无效")
    return data["url"]


def xhs_access_url(record):
    import re
    note_id = str(record.get("note_id") or "")
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", note_id):
        return None
    params = parse_qs(urlsplit(str(record.get("note_url") or "")).query)
    return f"https://www.xiaohongshu.com/explore/{note_id}?" + urlencode({
        "xsec_token": record.get("xsec_token") or (params.get("xsec_token") or [""])[0],
        "xsec_source": record.get("xsec_source") or (params.get("xsec_source") or ["pc_search"])[0]})
