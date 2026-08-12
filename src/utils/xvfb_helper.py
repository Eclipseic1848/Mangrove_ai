"""
虚拟显示 (Xvfb) 辅助模块

在无物理显示的环境（如服务器）下，使用 Xvfb 提供虚拟显示，
使 Chrome 以有界面模式（非 headless）运行，提高兼容性。
"""
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_xvfb_display = None


def start_xvfb(width: int = 1920, height: int = 1080, depth: int = 24, force: bool = False) -> bool:
    """
    启动 Xvfb 虚拟显示并设置 DISPLAY 环境变量。
    
    Args:
        width: 虚拟显示宽度
        height: 虚拟显示高度
        depth: 色深
        force: 为 True 时即使已有 DISPLAY 也强制启动 Xvfb（浏览器将运行在虚拟显示，不在物理屏幕显示）
        
    Returns:
        是否成功启动
    """
    global _xvfb_display
    
    # 若已有 DISPLAY 且未强制，跳过
    if os.environ.get("DISPLAY") and not force:
        logger.info("DISPLAY 已设置 (%s)，跳过 Xvfb 启动", os.environ["DISPLAY"])
        return True
    
    # 若本进程已启动过 Xvfb 且仍在运行，直接返回
    if _xvfb_display is not None and getattr(_xvfb_display, "is_alive", lambda: False)():
        logger.info("Xvfb 已在运行，复用")
        return True
    
    try:
        from pyvirtualdisplay import Display
        
        _xvfb_display = Display(
            size=(width, height),
            color_depth=depth,
            backend="xvfb",
        )
        _xvfb_display.start()
        # pyvirtualdisplay 会自动设置 DISPLAY 环境变量
        display_val = os.environ.get("DISPLAY", "")
        logger.info("Xvfb 虚拟显示已启动: DISPLAY=%s", display_val)
        return True
    except ImportError:
        logger.warning(
            "未安装 pyvirtualdisplay，无法使用虚拟显示。"
            "请运行: pip install pyvirtualdisplay"
        )
        return False
    except Exception as e:
        logger.warning("启动 Xvfb 失败: %s", e)
        return False


def stop_xvfb() -> None:
    """停止 Xvfb 虚拟显示"""
    global _xvfb_display
    if _xvfb_display is not None:
        try:
            _xvfb_display.stop()
            logger.info("Xvfb 虚拟显示已停止")
        except Exception as e:
            logger.debug("停止 Xvfb 时出错: %s", e)
        finally:
            _xvfb_display = None


def ensure_xvfb_for_headless_env() -> bool:
    """
    在无显示环境下自动启动 Xvfb。
    若 DISPLAY 未设置或为空，则尝试启动 Xvfb。
    
    Returns:
        是否具备可用显示（原有或新启动的 Xvfb）
    """
    if os.environ.get("DISPLAY"):
        return True
    return start_xvfb()
