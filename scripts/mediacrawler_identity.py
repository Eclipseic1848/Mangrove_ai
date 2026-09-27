"""只在独立凭证验证进程读取当前账号接口；不使用公开搜索证明身份。"""
import hashlib
import importlib
import json
from pathlib import Path


_CRAWLERS = {
    "xhs": ("xhs", "XiaoHongShuCrawler", "xhs_client"),
    "bili": ("bilibili", "BilibiliCrawler", "bili_client"),
    "wb": ("weibo", "WeiboCrawler", "wb_client"),
}


async def probe_account(platform, client):
    unknown = {"status": "unknown", "reason": "identity_unverified"}
    account = None
    account_kind = "unavailable"
    try:
        if platform == "xhs":
            try:
                current = await client.get("/api/sns/web/v2/user/me", params={})
            except Exception:
                # 新接口不可用不抹掉旧接口的有效证据；取消不会进入此分支。
                current = None
            if isinstance(current, dict):
                if current.get("guest") is True:
                    return {"status": "invalid", "reason": "login_required"}
                uid = current.get("user_id")
                if current.get("guest") is False and isinstance(uid, str) and uid.strip():
                    account, account_kind = uid, "account_id"
            if account is None:
                response = await client.query_self()
                data = response.get("data", {}) if isinstance(response, dict) else {}
                info = data.get("basic_info", {})
                if response is None or response.get("success") is not True or data.get("result", {}).get("success") is not True or not isinstance(info.get("red_id"), str) or not info["red_id"].strip():
                    return unknown
                account, account_kind = info["red_id"], "handle"
        elif platform == "bili":
            data = await client.get("/x/web-interface/nav")
            if data.get("isLogin") is False:
                return {"status": "invalid", "reason": "login_required"}
            if data.get("isLogin") is not True:
                return unknown
            account, account_kind = data.get("mid"), "account_id"
        elif platform == "wb":
            data = await client.request(method="GET", url=f"{client._host}/api/config", headers=client.headers)
            if data.get("login") is False:
                return {"status": "invalid", "reason": "login_required"}
            if data.get("login") is not True:
                return unknown
            account, account_kind = data.get("uid"), "account_id"
        else:
            # 知乎启动会先搜索；未隔离该前置步骤前不把它接入身份探针。
            return {"status": "unknown", "reason": "identity_probe_unsupported"}
    except Exception:
        # 网络、挑战及上游结构变化不等于凭证过期，禁止保存原始响应或异常正文。
        return {"status": "unknown", "reason": "identity_probe_failed"}
    result = {"status": "valid", "reason": "authenticated", "account_ref_kind": account_kind if account else "unavailable"}
    if isinstance(account, (str, int)) and not isinstance(account, bool) and str(account):
        result["account_ref"] = hashlib.sha256(f"{platform}\0{account}".encode("utf-8")).hexdigest()
    # handle可更改；此结果只证明本次认证，不能据此自动批准同账号续跑。
    return result


def install_probe(platform: str, output: Path) -> bool:
    definition = _CRAWLERS.get(platform)
    if definition is None:
        output.write_text(json.dumps({"status": "unknown", "reason": "identity_probe_unsupported"}), encoding="utf-8")
        return False
    module, class_name, client_attribute = definition
    crawler = getattr(importlib.import_module(f"media_platform.{module}.core"), class_name)

    async def search(self):
        result = await probe_account(platform, getattr(self, client_attribute))
        output.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    crawler.search = search
    return True
