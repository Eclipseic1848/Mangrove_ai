"""
截图工具模块

提供统一的页面截图捕获能力，供 Observe 节点、Tools 包装器等共享使用。
解耦 tools_wrapper 与 ObserveNode 的截图逻辑，避免 tools 直接实例化 ObserveNode。
"""
import base64
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# 截图存储目录名
SCREENSHOT_DIR_NAME = "screenshots"

# 固定使用 JPEG：体积小、传输快，有利于减少 MCP 超时（PNG 大页面易超时）
SCREENSHOT_EXT = "jpg"
SCREENSHOT_FORMAT = "jpeg"

# VL 模型专用：PNG 格式，与步骤记录截图意义不同，供视觉语言模型读图用
SCREENSHOT_VL_EXT = "png"
SCREENSHOT_VL_FORMAT = "png"

# MCP take_screenshot 最大等待时间（秒），超时则跳过。默认值由 settings 覆盖，此处仅作 fallback
SCREENSHOT_MCP_TIMEOUT = 45


def get_screenshot_dir(state: Dict[str, Any]) -> Path:
    """获取截图文件夹路径（按 session_id 隔离，多机/多连接互不干扰）
    
    Args:
        state: 状态字典，含 session_id 时使用 screenshots/<session_id>/，否则使用 screenshots/
    
    Returns:
        截图文件夹路径对象
    """
    base = Path.cwd() / SCREENSHOT_DIR_NAME
    session_id = state.get("session_id") if state else None
    if session_id:
        screenshot_dir = base / session_id
    else:
        screenshot_dir = base
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    return screenshot_dir


def get_screenshot_path(screenshot_count: int, state: Dict[str, Any]) -> Path:
    """获取截图文件路径（固定为 JPEG 扩展名）"""
    screenshot_dir = get_screenshot_dir(state)
    return screenshot_dir / f"{screenshot_count}_screenshot.{SCREENSHOT_EXT}"


def get_screenshot_relative_path(screenshot_count: int, state: Dict[str, Any]) -> str:
    """获取截图的相对路径字符串，供调用方提前写入 state 或日志"""
    session_id = state.get("session_id") if state else None
    name = f"{screenshot_count}_screenshot.{SCREENSHOT_EXT}"
    if session_id:
        return f"{SCREENSHOT_DIR_NAME}/{session_id}/{name}"
    return f"{SCREENSHOT_DIR_NAME}/{name}"


def get_screenshot_vl_path(screenshot_count: int, state: Dict[str, Any]) -> Path:
    """获取 VL 模型专用 PNG 截图文件路径（与步骤记录用的 JPEG 分开）"""
    screenshot_dir = get_screenshot_dir(state)
    return screenshot_dir / f"{screenshot_count}_screenshot_vl.{SCREENSHOT_VL_EXT}"


def get_screenshot_vl_relative_path(screenshot_count: int, state: Dict[str, Any]) -> str:
    """获取 VL 用 PNG 截图的相对路径"""
    session_id = state.get("session_id") if state else None
    name = f"{screenshot_count}_screenshot_vl.{SCREENSHOT_VL_EXT}"
    if session_id:
        return f"{SCREENSHOT_DIR_NAME}/{session_id}/{name}"
    return f"{SCREENSHOT_DIR_NAME}/{name}"


def _save_base64_to_file(base64_data: str, file_path: Path) -> bool:
    """将 base64 数据保存到文件，返回是否成功"""
    try:
        raw = base64.b64decode(base64_data)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(raw)
        return True
    except Exception as e:
        logger.warning(f"保存截图到文件失败: {e}")
        return False


def _extract_base64_from_mcp_result(result: Dict[str, Any]) -> str:
    """从 MCP 工具调用的 result 中提取 base64 图片数据"""
    content = result.get("content") or []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "image":
                data = item.get("data")
                if data:
                    return data
    return ""


def _get_screenshot_timeout() -> int:
    """从配置读取截图超时（秒），0 表示不超时。"""
    try:
        from src.config import settings
        return getattr(settings, "screenshot_mcp_timeout", SCREENSHOT_MCP_TIMEOUT)
    except Exception:
        return SCREENSHOT_MCP_TIMEOUT


def capture_screenshot(
    mcp_client: Any,
    state: Dict[str, Any],
    verbose: bool = False,
    timeout: Optional[int] = None,
) -> Tuple[str, str]:
    """捕获截图并保存到本地文件夹
    
    优先使用「响应 base64 + 本地保存」：不传 file_path，从 MCP 响应中提取 base64，
    由本模块负责写入文件。避免 MCP saveFile 的路径编码、跨进程 cwd 等问题，
    从根本上解决「截图文件不存在」。
    
    带超时保护：MCP take_screenshot 可能长时间阻塞（如页面过大、Chrome 忙），
    超时后跳过本次截图。
    
    Args:
        mcp_client: MCP 客户端实例，需提供 take_screenshot() 和 is_connected()
        state: 状态字典，用于获取 screenshot_count、session_id
        verbose: 是否输出详细日志
        timeout: 最大等待秒数，0 表示不超时
        
    Returns:
        (base64编码的截图字符串, 截图文件相对路径)，失败返回 ("", "")
    """
    if timeout is None:
        timeout = _get_screenshot_timeout()
    if not mcp_client or not (hasattr(mcp_client, "is_connected") and mcp_client.is_connected()):
        if mcp_client:
            logger.warning("MCP 服务器未连接，无法获取截图")
        return "", ""
    
    screenshot_count = state.get("screenshot_count", 0)
    screenshot_path = get_screenshot_path(screenshot_count, state)
    
    def _do_capture() -> Dict[str, Any]:
        # 固定 JPEG：体积小、传输快，有利于减少超时
        return mcp_client.take_screenshot(
            full_page=False, file_path=None, format_type=SCREENSHOT_FORMAT
        )
    
    try:
        _t0 = time.time()
        if timeout and timeout > 0:
            result_box: list = [None]
            exc_box: list = [None]
            def _run():
                try:
                    result_box[0] = _do_capture()
                except Exception as e:
                    exc_box[0] = e
            th = threading.Thread(target=_run, daemon=True)
            th.start()
            th.join(timeout=timeout)
            if th.is_alive():
                logger.warning(
                    f"⏱️ [截图] MCP take_screenshot 超时 ({timeout}s)，跳过本次截图 "
                    "(可能页面过大或 Chrome 忙，不影响后续流程)"
                )
                return "", ""
            if exc_box[0]:
                raise exc_box[0]
            raw_result = result_box[0]
        else:
            raw_result = _do_capture()
        
        _mcp_elapsed = time.time() - _t0
        if _mcp_elapsed > 2.0:
            logger.info(f"⏱️ [截图] MCP take_screenshot 耗时: {_mcp_elapsed:.2f}s")
        
        b64 = _extract_base64_from_mcp_result(raw_result)
        if not b64:
            logger.warning("MCP 响应中未包含截图 base64 数据")
            return "", ""
        
        if _save_base64_to_file(b64, screenshot_path):
            session_id = state.get("session_id")
            if session_id:
                relative_path = f"{SCREENSHOT_DIR_NAME}/{session_id}/{screenshot_path.name}"
            else:
                relative_path = f"{SCREENSHOT_DIR_NAME}/{screenshot_path.name}"
            if verbose:
                logger.info(f"📸 截图已保存: {screenshot_path.name} ({len(b64)} 字符)")
            return b64, relative_path
        
        logger.warning(f"保存截图到文件失败: {screenshot_path}")
        return "", ""
            
    except Exception as e:
        logger.error(f"获取截图失败: {e}", exc_info=True)
        if verbose:
            logger.info(f"⚠️ 获取截图失败: {e}")
        return "", ""


def capture_screenshot_for_vl(
    mcp_client: Any,
    state: Dict[str, Any],
    verbose: bool = False,
    timeout: Optional[int] = None,
) -> Tuple[str, str]:
    """为 VL 模型捕获 PNG 截图（与步骤记录用的 JPEG 意义不同，供视觉语言模型读图用）。
    仅当需要喂给 VL 时调用；格式固定为 PNG。
    Returns:
        (base64 字符串, 相对路径)，失败返回 ("", "")
    """
    if timeout is None:
        timeout = _get_screenshot_timeout()
    if not mcp_client or not (hasattr(mcp_client, "is_connected") and mcp_client.is_connected()):
        return "", ""
    screenshot_count = state.get("screenshot_count", 0)
    screenshot_path = get_screenshot_vl_path(screenshot_count, state)

    def _do_capture() -> Dict[str, Any]:
        return mcp_client.take_screenshot(
            full_page=False, file_path=None, format_type=SCREENSHOT_VL_FORMAT
        )

    try:
        if timeout and timeout > 0:
            result_box: list = [None]
            exc_box: list = [None]
            def _run():
                try:
                    result_box[0] = _do_capture()
                except Exception as e:
                    exc_box[0] = e
            th = threading.Thread(target=_run, daemon=True)
            th.start()
            th.join(timeout=timeout)
            if th.is_alive():
                if verbose:
                    logger.info("⏱️ [VL 截图] 超时，跳过本次 PNG")
                return "", ""
            if exc_box[0]:
                raise exc_box[0]
            raw_result = result_box[0]
        else:
            raw_result = _do_capture()
        b64 = _extract_base64_from_mcp_result(raw_result)
        if not b64:
            return "", ""
        if _save_base64_to_file(b64, screenshot_path):
            session_id = state.get("session_id")
            if session_id:
                relative_path = f"{SCREENSHOT_DIR_NAME}/{session_id}/{screenshot_path.name}"
            else:
                relative_path = f"{SCREENSHOT_DIR_NAME}/{screenshot_path.name}"
            if verbose:
                logger.info(f"📸 [VL] PNG 截图已保存: {screenshot_path.name}")
            return b64, relative_path
        return "", ""
    except Exception as e:
        logger.debug(f"VL 截图失败: {e}")
        return "", ""
