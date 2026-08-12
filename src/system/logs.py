"""
日志配置模块

设计要点（多任务并发友好）：

- 进程级 console 输出由 `setup_global_logging()` 在服务启动时一次性配置；不再随任务来回清空 root handler。
- 任务级文件日志通过 `attach_task_log_file()` 临时挂载，`detach_task_log_file()` 移除；
  借助 `contextvars` 让每个任务的 FileHandler 仅写入自己上下文产生的日志，并发任务互不串扰。
- `setup_logging()` 保留为旧接口的兼容封装：仅在第一次调用时初始化 console，不再清空 root handler，
  避免并发任务下「先到任务的日志被后来任务整体抢走」的问题。
"""
import asyncio
import contextvars
import logging
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from src.config import settings
from src.config.settings import LOG_TIMESTAMP_FORMAT
from src.agent.utils.terminal_colors import ColoredFormatter, ToolIOFilter


class FileFormatter(logging.Formatter):
    """文件日志 Formatter：换行 + 消息左对齐（与终端 ColoredFormatter 一致）；当 record 含 full_msg 时使用完整内容"""

    def format(self, record: logging.LogRecord) -> str:
        full_msg = getattr(record, "full_msg", None)
        if full_msg is not None:
            old_msg, old_args = record.msg, record.args
            record.msg = full_msg
            record.args = ()
            try:
                return super().format(record)
            finally:
                record.msg = old_msg
                record.args = old_args
        full = super().format(record)
        msg = record.message
        # 前缀单独一行，消息换行左对齐
        prefix = full[: len(full) - len(msg)] if msg else full
        return prefix.rstrip() + "\n" + msg if msg else full


def get_task_log_paths(logs_base: Optional[Path] = None) -> Tuple[Path, Path, str]:
    """生成单个任务下统一的日志目录与主日志文件路径。
    
    确保同一任务下：目录名、主日志文件、节点输出（工具结果等）均使用同一时间戳。
    
    结构示例：
        logs/
        ├── 2026-03-06_090332/                    ← 任务目录（工具结果等）
        ├── 2026-03-06_090424/
        ├── browser_agent_20260306_090332.log    ← 主日志（logs 根下）
        └── browser_agent_20260306_090424.log
    
    Args:
        logs_base: 日志根目录，默认 Path("logs")
        
    Returns:
        (task_log_dir, log_file_path, log_dir_name)
        - task_log_dir: 任务目录，如 logs/2026-03-06_090332
        - log_file_path: 主日志文件路径，如 logs/browser_agent_20260306_090332.log
        - log_dir_name: 任务目录名，如 2026-03-06_090332，可传入 BrowserAgentContext.log_dir_name
    """
    base = logs_base or Path("logs")
    base.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    log_dir_name = now.strftime(LOG_TIMESTAMP_FORMAT)  # 2026-03-06_090332
    log_file_suffix = now.strftime("%Y%m%d_%H%M%S")    # 20260306_090332
    task_log_dir = base / log_dir_name
    # 不在此处创建 task_log_dir，由首次写入（工具结果等）时按需创建，避免空目录
    log_file_path = base / f"browser_agent_{log_file_suffix}.log"
    return task_log_dir, log_file_path, log_dir_name


# ============================================================================
# 任务级日志隔离：contextvar + 任务专属 FileHandler + filter
# ============================================================================

# 当前任务的「log id」（一般使用 log_dir_name）。在不同 asyncio.Task 间天然隔离；
# asyncio.to_thread / LangGraph 调度 sync 节点时也会复制 contextvars 上下文，子线程能正确读取。
_current_task_log_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "browser_agent_current_task_log_id", default=None
)

_global_logging_lock = threading.Lock()
_global_logging_setup_done = False


def get_current_task_log_id() -> Optional[str]:
    return _current_task_log_id.get()


def set_current_task_log_id(task_log_id: Optional[str]) -> contextvars.Token:
    """设置当前任务的 log id；调用方需在结束时用 reset_current_task_log_id 还原。"""
    return _current_task_log_id.set(task_log_id)


def reset_current_task_log_id(token: contextvars.Token) -> None:
    try:
        _current_task_log_id.reset(token)
    except (LookupError, ValueError):
        pass


class _TaskLogFilter(logging.Filter):
    """任务级 FileHandler 的严格隔离 Filter：

    仅当当前 contextvar 等于此 handler 绑定的 `task_log_id` 时放行。
    这要求 LangGraph 等线程池调度的 sync 节点也能继承 contextvars——
    通过 `install_context_propagating_default_executor()` 安装的默认 executor 提供该能力。
    """

    def __init__(self, task_log_id: str) -> None:
        super().__init__()
        self.task_log_id = task_log_id

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: D401 - logging API
        return _current_task_log_id.get() == self.task_log_id


class ContextPropagatingExecutor(ThreadPoolExecutor):
    """提交任务时自动复制当前 contextvars 到子线程的执行器。

    asyncio 默认 ThreadPoolExecutor 不会复制 contextvars，
    导致 `loop.run_in_executor(None, fn)` 的子线程读不到调用方的 ContextVar；
    LangGraph 把 sync 节点丢到默认 executor 跑时，会让任务级日志路由失效。
    本 executor 在 `submit` 时通过 `contextvars.copy_context()` 包一层 `ctx.run(fn, ...)`，
    让子线程内的代码（含 logging filter）能正确读取调用方的 ContextVar。
    """

    def submit(self, fn, /, *args, **kwargs):  # type: ignore[override]
        ctx = contextvars.copy_context()
        return super().submit(ctx.run, fn, *args, **kwargs)


_default_executor_installed = False
_default_executor_lock = threading.Lock()


def install_context_propagating_default_executor(max_workers: Optional[int] = None) -> None:
    """把当前事件循环的默认 executor 替换为 `ContextPropagatingExecutor`。

    必须在 event loop 已运行后调用（FastAPI 的 startup 钩子是合适时机）。
    重复调用为 no-op。
    """
    global _default_executor_installed
    with _default_executor_lock:
        if _default_executor_installed:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        workers = max_workers or min(32, (os.cpu_count() or 4) + 4)
        executor = ContextPropagatingExecutor(
            max_workers=workers,
            thread_name_prefix="ctx-propagating",
        )
        loop.set_default_executor(executor)
        _default_executor_installed = True


def setup_global_logging(
    log_level: Optional[str] = None,
    server_log_file: Optional[str] = None,
    log_format: Optional[str] = None,
    force: bool = False,
) -> None:
    """进程级一次性日志配置。

    - 首次调用：创建 console handler（带颜色、ToolIOFilter）；可选挂一个进程主日志文件。
    - 重复调用：默认 no-op，避免清空 root handler 影响并发任务的任务级 FileHandler。
    - 强制刷新：`force=True` 会清掉所有 root handler 后重建（仅在初始化期使用）。
    """
    global _global_logging_setup_done
    with _global_logging_lock:
        if _global_logging_setup_done and not force:
            return

        level = getattr(logging, (log_level or settings.log_level).upper(), logging.INFO)
        fmt = log_format or settings.log_format

        root_logger = logging.getLogger()
        root_logger.setLevel(level)

        if force:
            for h in list(root_logger.handlers):
                root_logger.removeHandler(h)
                try:
                    h.close()
                except Exception:
                    pass
        else:
            # 仅清掉之前由 setup_global_logging 安装过的 handler，保留任务级 handler 不动
            for h in list(root_logger.handlers):
                if getattr(h, "_global_logging_managed", False):
                    root_logger.removeHandler(h)
                    try:
                        h.close()
                    except Exception:
                        pass

        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(ColoredFormatter(fmt))
        console_handler.addFilter(ToolIOFilter())
        setattr(console_handler, "_global_logging_managed", True)
        root_logger.addHandler(console_handler)

        if server_log_file:
            log_path = Path(server_log_file)
            log_path.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(server_log_file, encoding="utf-8")
            file_handler.setLevel(level)
            file_handler.setFormatter(FileFormatter(fmt))
            setattr(file_handler, "_global_logging_managed", True)
            root_logger.addHandler(file_handler)

        # 第三方降噪
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)
        logging.getLogger("openai").setLevel(logging.WARNING)

        _global_logging_setup_done = True


def attach_task_log_file(
    log_file: Path,
    task_log_id: str,
    log_level: Optional[str] = None,
    log_format: Optional[str] = None,
) -> logging.Handler:
    """为当前任务挂一个独立 FileHandler。

    Handler 携带 `_TaskLogFilter`：仅当当前 contextvar 等于 `task_log_id` 时才写入。
    多任务并发时各自的日志只进各自的文件，互不串扰。

    Args:
        log_file: 任务日志文件路径
        task_log_id: 任务唯一 ID（建议使用 `get_task_log_paths` 返回的 `log_dir_name`）
        log_level / log_format: 可选，覆盖默认级别/格式

    Returns:
        FileHandler 实例；任务结束时调用 `detach_task_log_file(handler)` 释放。
    """
    level = getattr(logging, (log_level or settings.log_level).upper(), logging.INFO)
    fmt = log_format or settings.log_format

    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(str(log_path), encoding="utf-8")
    handler.setLevel(level)
    handler.setFormatter(FileFormatter(fmt))
    handler.addFilter(_TaskLogFilter(task_log_id))
    setattr(handler, "_task_log_id", task_log_id)
    logging.getLogger().addHandler(handler)
    return handler


def detach_task_log_file(handler: Optional[logging.Handler]) -> None:
    """移除 `attach_task_log_file` 创建的 handler 并关闭。"""
    if handler is None:
        return
    try:
        logging.getLogger().removeHandler(handler)
    finally:
        try:
            handler.close()
        except Exception:
            pass


def setup_logging(
    log_level: Optional[str] = None,
    log_file: Optional[str] = None,
    log_format: Optional[str] = None,
) -> None:
    """已弃用：保留以兼容老调用方（CLI 脚本等）。

    新代码请使用 `setup_global_logging()` + `attach_task_log_file()` / `detach_task_log_file()`。

    本实现行为：
    - 首次调用：等同 `setup_global_logging(server_log_file=log_file)`，创建 console（+ 可选主日志文件）。
    - 重复调用：no-op，避免清空 root handler 影响并发任务的任务级 FileHandler。
    """
    setup_global_logging(
        log_level=log_level,
        server_log_file=log_file,
        log_format=log_format,
        force=False,
    )


def get_logger(name: str) -> logging.Logger:
    """获取指定名称的日志器。"""
    return logging.getLogger(name)
