"""
视觉点击模块：根据截图通过大模型识别点击坐标，再调用 click_at 执行点击。
主要用途：点击页面广告（广告位通常无稳定 uid、难以用 browser_snapshot 定位）。

适配 Mobile-Agent-v3.5 方案：
- 模型被告知屏幕分辨率为 1000x1000（归一化坐标）
- 支持 <tool_call> 输出格式，兼容 {"x","y"} 兜底
- 可选对截图做 smart_resize 再发送，减少 token
- 坐标换算：归一化 [0,1000] -> 原始视口像素

配置来自 src.config.settings。
"""
import ast
import base64
import json
import logging
import math
import re
from io import BytesIO
from typing import Any, Dict, Optional, Tuple

import requests
from PIL import Image

logger = logging.getLogger(__name__)

# 系统提示：与 Mobile-Agent-v3.5 一致，约定 1000x1000 归一化坐标，输出 tool_call 格式
VISION_CLICK_SYSTEM_PROMPT = (
    "# Tools\n\n"
    "You may call one function to locate the click position.\n\n"
    "You are provided with a function signature within <tools></tools> XML tags:\n"
    '<tools>\n'
    '{"type": "function", "function": {"name": "vision_click", '
    '"description": "Locate the pixel coordinate to click according to the user instruction. '
    'The screen resolution is 1000x1000. Output the normalized coordinate (x, y) in range [0, 1000].", '
    '"parameters": {"properties": {"action": {"description": "Must be \'click\'", "type": "string"}, '
    '"coordinate": {"description": "(x, y): normalized coordinates in [0, 1000] for both x and y. '
    'x from left edge, y from top edge.", "type": "array"}}, "required": ["action", "coordinate"]}}}\n'
    '</tools>\n\n'
    "# Response format\n\n"
    "Return a json object within <tool_call></tool_call> XML tags:\n"
    "<tool_call>\n"
    '{"name": "vision_click", "arguments": {"action": "click", "coordinate": [x, y]}}\n'
    "</tool_call>\n\n"
    "Rules:\n"
    "- Output exactly one <tool_call> block with coordinate [x, y] in range [0, 1000].\n"
    "- Do not output anything else. If the target cannot be found, use coordinate [0, 0]."
)

# 兜底用 prompt（模型未严格遵循 tool_call 时）
DEFAULT_CLICK_PROMPT = (
    "根据当前截图，找出用户要求点击的位置。\n"
    "屏幕分辨率为 1000x1000，请输出归一化坐标 [x, y]，x 和 y 均为 0-1000 的整数。\n"
    "只返回一个 JSON：{\"coordinate\": [x, y]} 或 {\"x\": x, \"y\": y}。\n"
    "若无法确定，返回 {\"coordinate\": [0, 0]} 或 {\"x\": -1, \"y\": -1}。"
)


def smart_resize(
    height: int,
    width: int,
    factor: int = 28,
    min_pixels: int = 3136,
    max_pixels: int = 10035200,
) -> Tuple[int, int]:
    """
    Rescale dimensions (Mobile-Agent / Qwen-VL style):
    - Both divisible by factor
    - Total pixels within [min_pixels, max_pixels]
    - Aspect ratio preserved
    Returns (new_height, new_width).
    """
    if height < 2 or width < 2:
        raise ValueError(f"height and width must be >= 2, got {height}x{width}")

    def _round(n: float) -> int:
        return round(n / factor) * factor

    def _floor(n: float) -> int:
        return max(factor, math.floor(n / factor) * factor)

    def _ceil(n: float) -> int:
        return max(factor, math.ceil(n / factor) * factor)

    h_bar = _round(height)
    w_bar = _round(width)

    if h_bar * w_bar > max_pixels:
        beta = math.sqrt((height * width) / max_pixels)
        h_bar = _floor(height / beta)
        w_bar = _floor(width / beta)
    elif h_bar * w_bar < min_pixels:
        beta = math.sqrt(min_pixels / (height * width))
        h_bar = _ceil(height * beta)
        w_bar = _ceil(width * beta)

    return h_bar, w_bar


def _image_from_base64(b64: str, image_format: str = "jpeg") -> Image.Image:
    """从 base64 解码为 PIL Image。"""
    raw = base64.b64decode(b64)
    return Image.open(BytesIO(raw)).convert("RGB")


def _resize_and_encode_to_base64(
    img: Image.Image,
    target_h: int,
    target_w: int,
    format: str = "PNG",
) -> str:
    """将图片缩放到目标尺寸并编码为 base64。"""
    resized = img.resize((target_w, target_h), Image.Resampling.LANCZOS)
    buf = BytesIO()
    resized.save(buf, format=format)
    return base64.b64encode(buf.getvalue()).decode("utf-8")


def _parse_xy_from_content(content: str, orig_width: int, orig_height: int) -> Optional[Tuple[int, int]]:
    """
    从模型返回的 content 中解析归一化坐标，并换算为原始视口像素。

    支持：
    1. <tool_call>{"name": "vision_click", "arguments": {"action":"click", "coordinate": [x,y]}}</tool_call>
    2. {"coordinate": [x, y]}
    3. {"x": n, "y": n} 或 {"x": [a,b]} 等兜底格式

    若解析到坐标在 0-1000 范围，视为归一化，换算为像素；
    若 > 1000，视为已是像素（向后兼容）。
    """
    if not content or not content.strip():
        return None
    text = content.strip()

    # 1) 尝试提取 <tool_call> 块
    tool_match = re.search(r"<tool_call>(.*?)</tool_call>", text, re.DOTALL | re.IGNORECASE)
    if tool_match:
        blk = tool_match.group(1).strip()
        try:
            obj = ast.literal_eval(blk)
            args = obj.get("arguments") or obj
            coord = args.get("coordinate")
            if isinstance(coord, (list, tuple)) and len(coord) >= 2:
                nx, ny = float(coord[0]), float(coord[1])
                return _norm_to_pixel(nx, ny, orig_width, orig_height)
        except (ValueError, SyntaxError, TypeError):
            pass

    # 2) 去掉 ```json ... ```
    m = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if m:
        text = m.group(1).strip()

    # 3) 尝试 JSON 解析
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None

    if isinstance(data, dict):
        coord = data.get("coordinate")
        if isinstance(coord, (list, tuple)) and len(coord) >= 2:
            nx, ny = float(coord[0]), float(coord[1])
            return _norm_to_pixel(nx, ny, orig_width, orig_height)
        x = data.get("x")
        y = data.get("y")
        if x is not None and y is not None:
            return _norm_to_pixel(float(x), float(y), orig_width, orig_height)
        if isinstance(x, (list, tuple)) and len(x) >= 2:
            return _norm_to_pixel(float(x[0]), float(x[1]), orig_width, orig_height)

    # 4) 兜底：正则提取数字
    arr = re.findall(r"-?\d+", text)
    if len(arr) >= 2:
        nx, ny = float(arr[0]), float(arr[1])
        return _norm_to_pixel(nx, ny, orig_width, orig_height)

    return None


def _norm_to_pixel(nx: float, ny: float, orig_width: int, orig_height: int) -> Optional[Tuple[int, int]]:
    """将归一化坐标 [0,1000] 换算为像素；若已是像素(>1000)则直接 clamp。"""
    if nx < 0 or ny < 0:
        return None
    # 若任一 > 1000，视为已是像素
    if nx > 1000 or ny > 1000:
        px = int(max(0, min(nx, orig_width - 1)))
        py = int(max(0, min(ny, orig_height - 1)))
        return (px, py)
    # 归一化 -> 像素
    px = int(nx / 1000.0 * orig_width)
    py = int(ny / 1000.0 * orig_height)
    px = max(0, min(px, orig_width - 1))
    py = max(0, min(py, orig_height - 1))
    return (px, py)


def _get_config() -> Dict[str, Any]:
    """从 settings 读取视觉点击配置（端点由 vision_click_use_gui_owl 决定）。"""
    try:
        from src.config.settings import resolve_vision_model_api_settings, settings

        api = resolve_vision_model_api_settings()
        return {
            "enabled": getattr(settings, "vision_click_enabled", True),
            "base_url": api["base_url"],
            "model": api["model"],
            "api_key": api.get("api_key") or "",
            "timeout": api["timeout"],
            "max_tokens": api["max_tokens"],
            "resize_before_send": getattr(settings, "vision_click_resize_before_send", True),
        }
    except Exception as e:
        logger.warning(f"读取视觉点击配置失败: {e}，使用默认值")
        return {
            "enabled": True,
            "base_url": "http://192.168.1.21:4243/v1",
            "model": "/home/howso/model/GUI-Owl",
            "api_key": "",
            "timeout": 60,
            "max_tokens": 512,
            "resize_before_send": True,
        }


def get_click_position_from_screenshot(
    screenshot_base64: str,
    instruction: str,
    image_format: str = "jpeg",
    orig_width: Optional[int] = None,
    orig_height: Optional[int] = None,
) -> Optional[Tuple[int, int]]:
    """
    根据截图与用户指令，调用视觉模型得到点击坐标 (x, y)（视口像素）。

    Args:
        screenshot_base64: 截图 base64 编码字符串（无 data URL 前缀）。
        instruction: 用户描述，如「点击登录按钮」「点一下提交」。
        image_format: 图片格式，用于 data URL。
        orig_width, orig_height: 原始视口宽高；若为 None，则从 base64 解码获取。

    Returns:
        成功时为 (x, y) 视口像素坐标；失败或无法识别时为 None。
        模型返回 (0,0) 或 (-1,-1) 时也视为无效。
    """
    cfg = _get_config()
    if not cfg.get("enabled"):
        logger.info("[vision_click] 已禁用，跳过 instruction=%r", instruction[:80] if instruction else "")
        return None
    base_url = cfg.get("base_url")
    if not base_url:
        logger.warning("[vision_click] base_url 未配置")
        return None

    logger.info("[vision_click] 开始 instruction=%r, image_format=%s", instruction[:80], image_format)
    img = _image_from_base64(screenshot_base64, image_format)
    w_orig, h_orig = img.size[0], img.size[1]
    if orig_width is not None:
        w_orig = orig_width
    if orig_height is not None:
        h_orig = orig_height
    logger.debug("[vision_click] 图片尺寸 %dx%d", w_orig, h_orig)

    # 可选：resize 后再发送（Mobile-Agent 做法）
    b64_to_send = screenshot_base64
    if cfg.get("resize_before_send") and (w_orig > 512 or h_orig > 512):
        try:
            h_new, w_new = smart_resize(h_orig, w_orig)
            b64_to_send = _resize_and_encode_to_base64(img, h_new, w_new)
        except Exception as e:
            logger.warning("resize 失败，使用原图: %s", e)

    url = f"{base_url}/chat/completions"
    user_prompt = f"{DEFAULT_CLICK_PROMPT}\n\n用户要求：{instruction}"
    messages = [
        {"role": "system", "content": [{"type": "text", "text": VISION_CLICK_SYSTEM_PROMPT}]},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/{image_format};base64,{b64_to_send}"}},
            ],
        },
    ]

    payload = {
        "model": cfg.get("model", "/home/howso/model/GUI-Owl"),
        "messages": messages,
        "max_tokens": cfg.get("max_tokens", 512),
    }

    headers = {"Content-Type": "application/json"}
    api_key = (cfg.get("api_key") or "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        logger.info("[vision_click] 请求视觉模型 url=%s model=%s", url, cfg.get("model"))
        # GUI-Owl 等视觉模型多在局域网（如 192.168.1.21），须绕过系统代理，否则 Clash 等会拦成 502/超时
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
            logger.warning("[vision_click] 视觉模型返回无 choices")
            return None
        msg = choices[0].get("message") or {}
        raw_content = msg.get("content")
        if isinstance(raw_content, list):
            content = "".join(
                c.get("text", "") if isinstance(c, dict) else str(c)
                for c in raw_content
            )
        else:
            content = str(raw_content or "")

        xy = _parse_xy_from_content(content, w_orig, h_orig)
        if xy is None:
            logger.warning("[vision_click] 无法从模型输出解析坐标 content=%s", content[:200])
            return None
        if xy == (0, 0) or xy == (-1, -1):
            logger.info("[vision_click] 模型表示无法确定点击位置，返回 (0,0) 或 (-1,-1)")
            return None
        logger.info("[vision_click] 解析得到坐标 (%d, %d) instruction=%r", xy[0], xy[1], (instruction or "")[:50])
        return xy
    except requests.exceptions.RequestException as e:
        logger.warning("[vision_click] 模型请求失败 instruction=%r: %s", (instruction or "")[:50], e)
        return None
    except (KeyError, TypeError, ValueError) as e:
        logger.warning("[vision_click] 解析响应失败: %s", e)
        return None
