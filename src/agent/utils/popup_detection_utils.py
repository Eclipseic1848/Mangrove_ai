"""
弹窗/广告检测工具

供 Observe 节点使用，通过截图调用视觉大模型判断是否存在遮挡式弹窗/模态框，
将结果写入 state.popup_hint，供 Execute 节点构建 observation_result 时使用。

弹窗检测采用视觉模型识别，与视觉点击共用 resolve_vision_model_api_settings（含 vision_click_use_gui_owl）。
"""
import base64
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

POPUP_HINT = (
    "⚠️ 检测到页面可能存在弹窗/广告遮罩。\n"
    "- 【必须】先关闭弹窗再执行其他操作。关闭弹窗时**必须使用** browser_click_by_vision，勿用 browser_click(uid)。\n"
    "- 调用示例：browser_click_by_vision(instruction=\"点击弹窗右上角关闭按钮或X\") 或 instruction=\"点击广告遮罩的关闭按钮\"。\n"
)

POPUP_VISION_PROMPT = (
    "判断截图中是否存在遮挡式弹窗、模态框或广告遮罩。"
    "特征：半透明遮罩层、居中/悬浮弹框、关闭按钮(X)、登录/注册弹窗等。"
    "不包含：页面内嵌的普通广告位、导航栏、下拉选择框。"
    "只回答一个字：是 或 否。"
)


def _get_vision_config() -> Dict[str, Any]:
    """与视觉点击共用端点配置（vision_click_use_gui_owl 控制 GUI-Owl 或主 LLM）。"""
    try:
        from src.config.settings import resolve_vision_model_api_settings, settings

        api = resolve_vision_model_api_settings()
        return {
            "enabled": getattr(settings, "popup_vision_detect_enabled", True),
            "base_url": api["base_url"],
            "model": api["model"],
            "api_key": api.get("api_key") or "",
            "timeout": api["timeout"],
            "max_tokens": api["max_tokens"],
        }
    except Exception as e:
        logger.warning(f"[popup_vision] 读取配置失败: {e}")
        return {
            "enabled": True,
            "base_url": "http://192.168.1.21:4243/v1",
            "model": "/home/howso/model/GUI-Owl",
            "api_key": "",
            "timeout": 60,
            "max_tokens": 64,
        }


def _load_base64_from_path(relative_path: str) -> Optional[str]:
    """从相对路径（如 screenshots/xxx/1_screenshot.jpg）加载图片并编码为 base64。"""
    if not relative_path or not isinstance(relative_path, str):
        return None
    try:
        p = Path.cwd() / relative_path.strip()
        if not p.exists() or not p.is_file():
            logger.debug("[popup_vision] 截图文件不存在: %s", p)
            return None
        b64 = base64.b64encode(p.read_bytes()).decode("utf-8")
        return b64
    except Exception as e:
        logger.warning("[popup_vision] 读取截图失败 %s: %s", relative_path, e)
        return None


def _detect_popup_by_vision(screenshot_base64: str) -> bool:
    """调用视觉模型判断截图中是否存在弹窗。返回 True 表示存在弹窗。"""
    cfg = _get_vision_config()
    if not cfg.get("enabled"):
        logger.debug("[popup_vision] 已禁用")
        return False
    base_url = cfg.get("base_url")
    if not base_url:
        logger.warning("[popup_vision] base_url 未配置")
        return False

    url = f"{base_url}/chat/completions"
    messages: List[Dict[str, Any]] = [
        {"role": "user", "content": [
            {"type": "text", "text": POPUP_VISION_PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_base64}"}},
        ]},
    ]

    payload = {
        "model": cfg.get("model"),
        "messages": messages,
        "max_tokens": cfg.get("max_tokens", 64),
    }

    headers = {"Content-Type": "application/json"}
    api_key = (cfg.get("api_key") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        logger.info("[popup_vision] 请求视觉模型判断弹窗 url=%s", url)
        # 视觉模型（GUI-Owl）多在局域网，须绕过系统代理，否则 Clash 等会拦成 502/超时
        from src.services.llm_provider import _is_lan
        proxies = {"http": None, "https": None} if _is_lan(url) else None
        resp = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=cfg.get("timeout", 60),
            proxies=proxies,
        )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            return False
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            content = "".join(
                c.get("text", "") if isinstance(c, dict) else str(c)
                for c in content
            )
        else:
            content = str(content or "").strip()

        has_popup = "是" in content
        logger.info("[popup_vision] 模型返回: %r -> 弹窗=%s", content[:50], has_popup)
        return has_popup
    except requests.exceptions.RequestException as e:
        logger.warning("[popup_vision] 请求失败: %s", e)
        return False
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("[popup_vision] 解析响应失败: %s", e)
        return False


def detect_popup_from_screenshot(
    screenshot_path: Optional[str] = None,
    screenshot_base64: Optional[str] = None,
) -> Optional[str]:
    """根据截图（路径或 base64）调用视觉模型判断是否存在弹窗。有则返回提示文案。"""
    b64 = screenshot_base64
    if not b64 and screenshot_path:
        b64 = _load_base64_from_path(screenshot_path)
    if not b64:
        logger.debug("[popup_vision] 无可用截图，跳过")
        return None

    if _detect_popup_by_vision(b64):
        return POPUP_HINT
    return None


def detect_popup_from_console(console_messages: Any) -> Optional[str]:
    """从控制台消息中检测 JavaScript 对话框（alert/confirm/prompt）。"""
    if not console_messages:
        return None
    # msgs = console_messages if isinstance(console_messages, list) else [console_messages]
    # for msg in msgs:
    #     s = (msg if isinstance(msg, str) else str(msg)).lower()
    #     if any(kw in s for kw in ['dialog', 'alert', 'confirm', 'prompt']):
    #         logger.info("[popup_detect] 控制台命中 dialog/alert/confirm/prompt")
    #         return (
    #             "⚠️ 检测到浏览器可能弹出对话框（alert/confirm/prompt）。\n"
    #             "- 【建议】使用 browser_handle_dialog 处理，或关闭对话框后再继续。\n"
    #         )
    return None


def detect_popup(
    page_snapshot: str,
    console_messages: Any,
    *,
    screenshot_path: Optional[str] = None,
    screenshot_base64: Optional[str] = None,
) -> Optional[str]:
    """
    综合检测弹窗。优先使用视觉模型分析截图；若未检测到，再检查控制台 alert/confirm/prompt。
    """
    logger.info("[popup_detect] 开始检测 snapshot_len=%d", len(page_snapshot or ""))
    hint = None
    if screenshot_path or screenshot_base64:
        hint = detect_popup_from_screenshot(
            screenshot_path=screenshot_path,
            screenshot_base64=screenshot_base64,
        )
        if hint:
            logger.info("[popup_detect] ✅ 视觉检测到弹窗，popup_hint 已设置")
    if not hint:
        hint = detect_popup_from_console(console_messages)
    if hint:
        logger.info("[popup_detect] popup_hint 已设置 (前80字): %s", (hint or "")[:80])
    else:
        logger.info("[popup_detect] 未检测到弹窗，popup_hint=None")
    return hint
