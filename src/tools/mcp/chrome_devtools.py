"""
Chrome DevTools MCP 适配器

提供 Python 接口来调用 Chrome DevTools MCP 服务器的功能。

MCP (Model Context Protocol) 工作原理：
1. 通过 subprocess 启动 MCP 服务器 (npx chrome-devtools-mcp 或本地编译版本)
2. 使用 stdin/stdout 进行 JSON-RPC 通信
3. 遵循 MCP 协议进行初始化握手和工具调用

工具返回格式（统一便于模型判断步骤是否成功）：
- 所有工具均返回 JSON 字符串，根级必含 success (bool)、message (str)。
- 成功时可选 data（如 content、file_path、file_name 等）；失败时可选 error（详细原因）。

MCP 规范结果约定（从根本上避免「工具成功但 file_path/file_name 为 null」）：
- 凡 MCP 端会落盘并需向 Python 返回文件路径的工具，应在响应中输出一行：MCP_TOOL_RESULT: + 单行 JSON。
- JSON 建议字段：success (bool), file_path (str|null), file_name (str|null), message (可选)。
- Python 端优先用 parse_mcp_tool_result() 解析该行，再回退到正则/非结构化解析。参见 MCP_TOOL_RESULT_PREFIX 与 parse_mcp_tool_result()。

功能分类（浏览器工具约 37 个，含 MCP 直连方法若干）：
1. 输入自动化 (8 tools): click, drag, fill, fill_form, handle_dialog, hover, press_key, upload_file
2. 导航自动化 (6 tools): close_page, list_pages, navigate_page, new_page, select_page, wait_for
3. 模拟 (2 tools): emulate, resize_page
4. 性能 (3 tools): performance_analyze_insight, performance_start_trace, performance_stop_trace
5. 网络 (2 tools): get_network_request, list_network_requests
6. 调试 (5 tools): evaluate_script, get_console_message, list_console_messages, take_screenshot, take_snapshot
7. 抖音专用 (1 tool): fetch_and_download_douyin_video（fetch_douyin_video_links、download_douyin_video 已暂线屏蔽）
8. 内容提取-汽车之家 (2 tools): extract_autohome_post, extract_autohome_chejiahao_info
9. 内容提取-懂车帝 (2 tools): extract_dcd_by_url, extract_dcd_video
10. VOC 用户声音处理 (2 tools): browser_filter_voc, browser_analyze_voc
11. MongoDB 入库 (2 tools): browser_voc_store_from_json_file, browser_voc_mongo_ping
12. Qwen 视频文字提取 (1 tool): browser_analyze_video

落盘路径约定：懂车帝/汽车之家/抖音等抓取与下载仅写入 <项目根>/downloads/下对应子目录；VOC/视频分析结果仅写入 <项目根>/analysis/（与 downloads 子目录结构对齐），不在工具参数中暴露自定义保存路径。

主要组件：
- ChromeDevToolsMCP: MCP 客户端类，提供底层工具调用接口
- ChromeDevToolsConfig: 配置类，支持浏览器连接、视口、代理等配置
- create_browser_tools: 创建 LangChain 工具集，用于 Agent 集成
"""

import json
import logging
import subprocess
import threading
import time
import os
import sys
import re
import requests
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List
from enum import Enum
from pathlib import Path

from src.config.settings import CHROME_DEVTOOLS_MCP_INDEX_JS, PROJECT_ROOT
from src.services.voc_processor import filter_voc_file, analyze_voc_file
from src.services.qwen_video_processor import analyze_video_file
from src.agent.utils.screenshot_utils import _extract_base64_from_mcp_result
from src.agent.utils.vision_click_utils import get_click_position_from_screenshot

logger = logging.getLogger(__name__)

# ============================================================================
# 默认配置常量
# ============================================================================

# 统一的下载目录（相对于项目根目录，避免因进程 cwd 不同导致路径不一致）
DEFAULT_DOWNLOAD_DIR = PROJECT_ROOT / "downloads"
# 分析结果目录（VOC、视频文字提取等），与 downloads 子目录结构对齐
DEFAULT_ANALYSIS_DIR = PROJECT_ROOT / "analysis"

# MCP 规范结果约定：MCP 端在响应中输出一行 "MCP_TOOL_RESULT:" + 单行 JSON，
# 便于 Python 端可靠解析 file_path / file_name 等，避免依赖正则或非结构化文本。
# JSON 建议字段：success (bool), file_path (str|null), file_name (str|null), message (str, 可选)
MCP_TOOL_RESULT_PREFIX = "MCP_TOOL_RESULT:"

# MCP 服务器启动等待配置（替代固定 sleep）
MCP_STARTUP_MIN_WAIT = 0.5   # 最短等待时间（秒），让进程至少完成 spawn
MCP_STARTUP_MAX_TIMEOUT = 15  # 初始化最大超时（秒），超时则放弃


def parse_mcp_tool_result(full_text: str) -> Optional[Dict[str, Any]]:
    """
    从 MCP 响应文本中解析规范结果行（MCP_TOOL_RESULT: + 单行 JSON）。
    用于「文件类」工具（如下载视频）可靠获取 file_path / file_name，避免依赖正则或非结构化文本。

    Returns:
        解析成功时返回包含 success, file_path, file_name 等字段的字典；否则返回 None。
    """
    if not full_text or not full_text.strip():
        return None
    prefix = MCP_TOOL_RESULT_PREFIX
    for line in full_text.splitlines():
        line = line.strip()
        if line.startswith(prefix):
            try:
                json_str = line[len(prefix):].strip()
                obj = json.loads(json_str)
                if isinstance(obj, dict):
                    return obj
            except (json.JSONDecodeError, TypeError):
                pass
            return None
    return None


def _parse_mcp_saved_file_from_text(full_text: str) -> Optional[str]:
    """从 MCP 文本响应中解析 extract_autohome_chejiahao_info / extract_dcd_video 等返回的 savedFile 路径。"""
    if not full_text:
        return None
    m = re.search(r'"savedFile"\s*:\s*"([^"]+)"', full_text)
    if m:
        return m.group(1).strip().replace("\\", "/")
    return None


# ============================================================================
# 配置类
# ============================================================================

class BrowserChannel(Enum):
    """Chrome 渠道"""
    STABLE = "stable"
    CANARY = "canary"
    BETA = "beta"
    DEV = "dev"


@dataclass
class ChromeDevToolsConfig:
    """Chrome DevTools MCP 配置"""
    # 连接配置
    browser_url: Optional[str] = None  # 例如 http://127.0.0.1:9222
    ws_endpoint: Optional[str] = None  # WebSocket 端点
    ws_headers: Optional[Dict[str, str]] = None  # WebSocket 自定义头
    auto_connect: bool = False  # 自动连接到运行中的 Chrome
    
    # 浏览器配置
    headless: bool = False  # 无头模式
    executable_path: Optional[str] = None  # Chrome 可执行文件路径
    user_data_dir: Optional[str] = None  # 用户数据目录
    channel: BrowserChannel = BrowserChannel.STABLE  # Chrome 渠道
    isolated: bool = True  # 使用临时用户数据目录（推荐 True 避免冲突）
    
    # 视口配置
    viewport: Optional[str] = None  # 例如 "1280x720"
    
    # 代理配置
    proxy_server: Optional[str] = None
    accept_insecure_certs: bool = False
    
    # 功能开关
    category_emulation: bool = True
    category_performance: bool = True
    category_network: bool = True
    experimental_vision: bool = True  # 启用 click_at 等视觉相关工具（browser_click_by_vision 依赖）
    
    # 日志配置
    log_file: Optional[str] = None
    
    # 超时配置
    startup_timeout: int = 30  # 服务器启动超时（秒）
    request_timeout: int = 60  # 请求超时（秒）
    
    # Chrome 启动参数（仅在本进程启动 Chrome 时生效，连接 browser_url 时忽略）
    # 服务器/无头/Docker 下若出现 Protocol error (Target.setDiscoverTargets): Target closed，请传入：
    # chrome_arg=["--no-sandbox", "--disable-setuid-sandbox", "--disable-dev-shm-usage", "--disable-gpu"]
    chrome_arg: Optional[List[str]] = None
    # 当 MCP 自动启动 Chrome（未指定 browser_url）时，是否自动添加服务器常用参数（避免 Target closed）
    # 容器/服务器环境下无论 headless 与否均需这些参数，否则 Chrome 易崩溃
    server_safe_chrome_args: bool = True
    
    def to_args(self) -> List[str]:
        """转换为命令行参数"""
        args = []
        
        if self.browser_url:
            args.append(f"--browser-url={self.browser_url}")
        if self.ws_endpoint:
            args.append(f"--ws-endpoint={self.ws_endpoint}")
        if self.ws_headers:
            args.append(f"--ws-headers={json.dumps(self.ws_headers)}")
        if self.auto_connect:
            args.append("--auto-connect")
        if self.headless:
            args.append("--headless")
        if self.executable_path:
            args.append(f"--executable-path={self.executable_path}")
        if self.user_data_dir:
            args.append(f"--user-data-dir={self.user_data_dir}")
        if self.channel != BrowserChannel.STABLE:
            args.append(f"--channel={self.channel.value}")
        if self.isolated:
            args.append("--isolated")
        if self.viewport:
            args.append(f"--viewport={self.viewport}")
        if self.proxy_server:
            args.append(f"--proxy-server={self.proxy_server}")
        if self.accept_insecure_certs:
            args.append("--accept-insecure-certs")
        if not self.category_emulation:
            args.append("--no-category-emulation")
        if not self.category_performance:
            args.append("--no-category-performance")
        if not self.category_network:
            args.append("--no-category-network")
        if self.experimental_vision:
            args.append("--experimental-vision")
        if self.log_file:
            args.append(f"--log-file={self.log_file}")
        
        # Chrome 启动参数：显式传入的 + 可选的服务端安全默认值
        # MCP 自动启动 Chrome 时（无 browser_url），容器/服务器环境需 --no-sandbox 等，否则易 Target closed
        chrome_args: List[str] = list(self.chrome_arg) if self.chrome_arg else []
        if (
            self.server_safe_chrome_args
            and not self.browser_url
            and not self.ws_endpoint
        ):
            for safe in (
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ):
                if safe not in chrome_args:
                    chrome_args.append(safe)
        for arg in chrome_args:
            args.append(f"--chrome-arg={arg}")
        
        return args


# ============================================================================
# MCP 客户端
# ============================================================================

class ChromeDevToolsMCP:
    """
    Chrome DevTools MCP 客户端
    
    使用 stdio 与 MCP 服务器通信。
    
    使用方式：
    ```python
    mcp = ChromeDevToolsMCP()
    mcp.start()  # 启动服务器
    result = mcp.call_tool("navigate_page", {"url": "https://example.com"})
    mcp.stop()  # 停止服务器
    ```
    
    或者使用上下文管理器：
    ```python
    with ChromeDevToolsMCP() as mcp:
        result = mcp.navigate("https://example.com")
    ```
    """
    
    def __init__(
        self,
        config: Optional[ChromeDevToolsConfig] = None,
        verbose: bool = True,
    ):
        """
        初始化 Chrome DevTools MCP 客户端
        
        Args:
            config: MCP 配置
            verbose: 是否输出详细日志
        """
        self.config = config or ChromeDevToolsConfig()
        self.verbose = verbose
        self._process: Optional[subprocess.Popen] = None
        self._connected = False
        self._request_id = 0
        self._lock = threading.Lock()
        self._stderr_thread: Optional[threading.Thread] = None
        self._initialized = False
        
        logger.info("ChromeDevToolsMCP 客户端初始化完成，browser_url: %s", self.config.browser_url)
    
    def __enter__(self):
        """上下文管理器入口"""
        self.start()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.stop()
        return False
    
    def _get_command(self) -> List[str]:
        """获取 MCP 服务器启动命令"""
        # 优先使用本地编译版本
        # 使用全局配置的路径
        index_js = CHROME_DEVTOOLS_MCP_INDEX_JS
        
        if index_js.exists():
            # 使用本地编译版本
            cmd = ["node", str(index_js)]
            logger.info(f"使用本地编译版本: {index_js}")
        else:
            # 回退到 npm 包版本
            if sys.platform == "win32":
                cmd = ["npx.cmd", "-y", "chrome-devtools-mcp@latest"]
            else:
                cmd = ["npx", "-y", "chrome-devtools-mcp@latest"]
            logger.warning(f"本地编译版本不存在 ({index_js})，使用 npm 包版本")
        
        # 添加配置参数
        cmd.extend(self.config.to_args())
        return cmd
    
    def _read_stderr(self):
        """读取 stderr 输出（日志）"""
        if not self._process or not self._process.stderr:
            return
        
        try:
            for line in self._process.stderr:
                if self.verbose:
                    logger.debug(f"[MCP stderr] {line.strip()}")
        except Exception as e:
            logger.debug(f"stderr 读取结束: {e}")
    
    def _next_request_id(self) -> int:
        """获取下一个请求 ID"""
        with self._lock:
            self._request_id += 1
            return self._request_id
    
    def _send_request(self, method: str, params: Optional[Dict[str, Any]] = None, skip_connected_check: bool = False) -> Dict[str, Any]:
        """
        发送 JSON-RPC 请求
        
        Args:
            method: 方法名
            params: 参数
            skip_connected_check: 是否跳过连接状态检查（用于初始化阶段）
            
        Returns:
            响应结果
        """
        if not self._process:
            raise RuntimeError("MCP 服务器进程未启动")
        if not skip_connected_check and not self._connected:
            raise RuntimeError("MCP 服务器未连接")
        
        request_id = self._next_request_id()
        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params:
            request["params"] = params
        
        request_str = json.dumps(request) + "\n"
        
        try:
            with self._lock:
                if self.verbose:
                    logger.info(f"🔧 [MCP] 发送请求 - method={method} | request={request_str[:200]}...")
                
                self._process.stdin.write(request_str)
                # logger.info(f"🔧 [MCP] 发送请求 - request={request_str}")
                self._process.stdin.flush()
                
                # 读取响应
                response_line = self._process.stdout.readline()
                # logger.info(f"🔧 [MCP] 响应 - response_line={response_line}")
                
                if not response_line:
                    raise RuntimeError("MCP 服务器无响应")
                
                response = json.loads(response_line)
                # logger.info(f"🔧 [MCP] 响应 - response={response}")
                
                if self.verbose:
                    logger.info(f"🔧 [MCP] 响应 - response={str(response)[:500]}...")
                
                return response
                
        except json.JSONDecodeError as e:
            logger.error(f"JSON 解析错误: {e}")
            raise RuntimeError(f"MCP 响应解析失败: {e}")
        except Exception as e:
            logger.error(f"通信错误: {e}")
            raise RuntimeError(f"MCP 通信失败: {e}")
    
    def _send_notification(self, method: str, params: Optional[Dict[str, Any]] = None, skip_connected_check: bool = False) -> None:
        """
        发送 JSON-RPC 通知（无 id，不等待响应）
        
        MCP 协议中 notifications/initialized 等为通知类型，不应期待服务端响应。
        若用 _send_request 发送会错误地等待响应，可能阻塞或与标准 MCP 服务器不兼容。
        
        Args:
            method: 方法名
            params: 参数
            skip_connected_check: 是否跳过连接状态检查（用于初始化阶段）
        """
        if not self._process:
            raise RuntimeError("MCP 服务器进程未启动")
        if not skip_connected_check and not self._connected:
            raise RuntimeError("MCP 服务器未连接")
        
        notification = {"jsonrpc": "2.0", "method": method}
        if params:
            notification["params"] = params
        
        request_str = json.dumps(notification) + "\n"
        
        try:
            with self._lock:
                if self.verbose:
                    logger.debug(f"[MCP] 发送通知: {method}")
                self._process.stdin.write(request_str)
                self._process.stdin.flush()
        except Exception as e:
            logger.error(f"通知发送错误: {e}")
            raise RuntimeError(f"MCP 通知发送失败: {e}")
    
    def start(self) -> bool:
        """
        启动 MCP 服务器
        
        Returns:
            是否成功启动
        """
        if self._connected:
            logger.warning("MCP 服务器已经在运行")
            return True
        
        cmd = self._get_command()
        
        if self.verbose:
            logger.info(f"启动 MCP 服务器: {' '.join(cmd)}")
        
        try:
            # 设置环境变量
            env = os.environ.copy()
            
            # 启动进程
            self._process = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",      # MCP 输出为 UTF-8，须显式指定，否则 Windows 默认 GBK 解码中文/emoji 会崩
                errors="replace",      # 个别非法字节不致命：替换而非抛错，保证通信不中断
                bufsize=1,  # 行缓冲
                env=env,
            )
            
            # 启动 stderr 读取线程
            self._stderr_thread = threading.Thread(target=self._read_stderr, daemon=True)
            self._stderr_thread.start()
            
            # 轮询等待服务器就绪：先短暂等待进程 spawn，再在超时内尝试初始化
            time.sleep(MCP_STARTUP_MIN_WAIT)
            
            init_result: List[Any] = [None]  # [True/False] 或异常
            
            def _do_init():
                try:
                    init_result[0] = self._initialize()
                except Exception as e:
                    init_result[0] = e
            
            init_thread = threading.Thread(target=_do_init, daemon=True)
            init_thread.start()
            init_thread.join(timeout=MCP_STARTUP_MAX_TIMEOUT)
            
            if init_thread.is_alive():
                logger.error(f"MCP 服务器初始化超时（{MCP_STARTUP_MAX_TIMEOUT}s），可能负载过高或启动缓慢")
                self.stop()
                return False
            
            if init_result[0] is True:
                self._connected = True
                logger.info("MCP 服务器已启动并初始化")
                return True
            else:
                err = init_result[0]
                if isinstance(err, Exception):
                    logger.error(f"MCP 服务器初始化错误: {err}")
                else:
                    logger.error("MCP 服务器初始化失败")
                self.stop()
                return False
                
        except FileNotFoundError:
            logger.error("找不到 npx 命令，请确保已安装 Node.js")
            return False
        except Exception as e:
            logger.error(f"启动 MCP 服务器失败: {e}")
            self.stop()
            return False
    
    def _initialize(self) -> bool:
        """
        执行 MCP 初始化握手
        
        Returns:
            是否初始化成功
        """
        try:
            # 发送 initialize 请求（跳过连接检查，因为这是初始化阶段）
            response = self._send_request("initialize", {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {},
                },
                "clientInfo": {
                    "name": "python-mcp-client",
                    "version": "1.0.0",
                }
            }, skip_connected_check=True)
            
            if "result" in response:
                logger.info(f"MCP 初始化成功: {response.get('result', {}).get('serverInfo', {})}")
                
                # 发送 initialized 通知（MCP 协议规定为通知，无 id，不期待响应）
                self._send_notification("notifications/initialized", {}, skip_connected_check=True)
                
                self._initialized = True
                return True
            else:
                logger.error(f"MCP 初始化失败: {response}")
                return False
                
        except Exception as e:
            logger.error(f"MCP 初始化错误: {e}")
            return False
    
    def stop(self):
        """停止 MCP 服务器"""
        if self._process:
            try:
                self._process.terminate()
                self._process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._process.kill()
            except Exception as e:
                logger.warning(f"停止服务器时出错: {e}")
            finally:
                self._process = None
        
        self._connected = False
        self._initialized = False
        logger.info("MCP 服务器已停止")
    
    def is_connected(self) -> bool:
        """检查是否已连接"""
        return self._connected and self._initialized
    
    def list_tools(self) -> List[Dict[str, Any]]:
        """
        获取可用工具列表
        
        Returns:
            工具列表
        """
        response = self._send_request("tools/list")
        logger.info(f"--------可用工具列表------------{response}")
        return response.get("result", {}).get("tools", [])
    
    def call_tool(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        调用 MCP 工具
        
        Args:
            tool_name: 工具名称
            arguments: 工具参数
            
        Returns:
            工具执行结果
        """
        # 执行工具入口：便于排查执行过程中是否有其他操作
        args_preview = arguments or {}
        args_str = json.dumps(args_preview, ensure_ascii=False)
        if len(args_str) > 200:
            args_str = args_str[:200] + "..."
        # logger.info(f"🔧 [MCP] 执行工具入口 - tool={tool_name} | arguments={args_str}")
        if not self.is_connected():
            raise RuntimeError("MCP 服务器未连接，请先调用 start() 方法")
        
        response = self._send_request("tools/call", {
            "name": tool_name,
            "arguments": arguments or {},
        })

        # logger.info(f"🔧 [MCP] 执行工具入口 - tool={tool_name} | arguments={args_str} | response={response}")
        
        if "error" in response:
            error = response["error"]
            error_code = error.get("code", "unknown")
            error_message = error.get("message", str(error))
            
            # 如果是工具未找到错误，提供更详细的诊断信息
            if error_code == -32602 or "not found" in error_message.lower():
                # 尝试获取可用工具列表
                try:
                    available_tools = self.list_tools()
                    tool_names = [t.get("name", "") for t in available_tools]
                    similar_tools = [name for name in tool_names if tool_name.lower() in name.lower() or name.lower() in tool_name.lower()]
                    
                    error_msg = f"MCP 工具 '{tool_name}' 未找到 (错误代码: {error_code})\n"
                    error_msg += f"错误信息: {error_message}\n"
                    
                    if tool_names:
                        error_msg += f"\n可用工具列表 (共 {len(tool_names)} 个):\n"
                        error_msg += f"  {', '.join(sorted(tool_names))}\n"
                    
                    if similar_tools:
                        error_msg += f"\n相似的工具名称: {', '.join(similar_tools)}\n"
                    else:
                        error_msg += f"\n提示: 请检查工具名称是否正确，或确认 MCP 服务器已正确编译并包含此工具。\n"
                        error_msg += f"      如果是抖音相关工具，请确保已编译 chrome-devtools-mcp 项目。\n"
                    
                    raise RuntimeError(error_msg)
                except Exception as e:
                    # 如果获取工具列表失败，返回原始错误
                    logger.warning(f"无法获取工具列表进行诊断: {e}")
                    raise RuntimeError(f"MCP 工具 '{tool_name}' 调用失败 (错误代码: {error_code}): {error_message}")
            else:
                raise RuntimeError(f"MCP 工具 '{tool_name}' 调用失败 (错误代码: {error_code}): {error_message}")
        
        return response.get("result", {})
    
    # ==================== 快捷方法 ====================
    
    def navigate(self, url: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        """导航到指定 URL"""
        params = {"url": url, "type": "url"}
        if timeout:
            params["timeout"] = timeout
        return self.call_tool("navigate_page", params)
    
    def new_page(self, url: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        """创建新页面"""
        params = {"url": url}
        if timeout:
            params["timeout"] = timeout
        return self.call_tool("new_page", params)
    
    def list_pages(self) -> Dict[str, Any]:
        """列出所有页面"""
        return self.call_tool("list_pages")
    
    def select_page(self, page_id: int, bring_to_front: bool = False) -> Dict[str, Any]:
        """选择页面"""
        return self.call_tool("select_page", {
            "pageId": page_id,
            "bringToFront": bring_to_front,
        })
    
    def close_page(self, page_id: int) -> Dict[str, Any]:
        """关闭页面"""
        return self.call_tool("close_page", {"pageId": page_id})
    
    def wait_for(self, text: str, timeout: Optional[int] = None) -> Dict[str, Any]:
        """等待文本出现"""
        params = {"text": text}
        if timeout:
            params["timeout"] = timeout
        return self.call_tool("wait_for", params)
    
    def click(self, uid: str, dbl_click: bool = False) -> Dict[str, Any]:
        """点击元素"""
        return self.call_tool("click", {
            "uid": uid,
            "dblClick": dbl_click,
        })

    def click_at(self, x: int, y: int, dbl_click: bool = False) -> Dict[str, Any]:
            """在指定坐标点击"""
            return self.call_tool("click_at", {
                "x": x,
                "y": y,
                "dblClick": dbl_click,
            })  
    
    def fill(self, uid: str, value: str) -> Dict[str, Any]:
        """填充表单元素"""
        return self.call_tool("fill", {
            "uid": uid,
            "value": value,
        })
    
    def hover(self, uid: str) -> Dict[str, Any]:
        """悬停在元素上"""
        return self.call_tool("hover", {"uid": uid})
    
    def press_key(self, key: str) -> Dict[str, Any]:
        """按键"""
        return self.call_tool("press_key", {"key": key})
    
    def drag(self, from_uid: str, to_uid: str) -> Dict[str, Any]:
        """拖拽元素"""
        return self.call_tool("drag", {
            "from_uid": from_uid,
            "to_uid": to_uid,
        })
    
    def fill_form(self, elements: List[Dict[str, str]]) -> Dict[str, Any]:
        """
        批量填写表单元素
        
        Args:
            elements: 元素列表，每个元素包含 uid 和 value
                     例如: [{"uid": "element_123", "value": "text1"}, {"uid": "element_456", "value": "text2"}]
        """
        return self.call_tool("fill_form", {"elements": elements})
    
    def handle_dialog(self, action: str, prompt_text: Optional[str] = None) -> Dict[str, Any]:
        """
        处理浏览器对话框
        
        Args:
            action: 操作类型，"accept" 或 "dismiss"
            prompt_text: 可选的提示文本（用于 prompt 对话框）
        """
        params = {"action": action}
        if prompt_text is not None:
            params["promptText"] = prompt_text
        return self.call_tool("handle_dialog", params)
    
    def upload_file(self, uid: str, file_path: str) -> Dict[str, Any]:
        """
        上传文件
        
        Args:
            uid: 文件输入元素的 uid（从页面快照中获取）
            file_path: 要上传的本地文件路径
        """
        return self.call_tool("upload_file", {
            "uid": uid,
            "filePath": file_path,
        })
    
    def take_screenshot(
        self,
        full_page: Optional[bool] = None,
        format_type: Optional[str] = None,
        quality: Optional[int] = None,
        uid: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        截图
        
        Args:
            full_page: 是否截取整个页面（与 uid 互斥）
            format_type: 图片格式 ("png", "jpeg", "webp")，默认 "png"
            quality: 压缩质量 (0-100)，仅用于 JPEG 和 WebP
            uid: 元素 uid（与 full_page 互斥）
            file_path: 保存路径（可选）
        """
        params = {}
        if full_page is not None:
            params["fullPage"] = full_page
        if format_type is not None:
            params["format"] = format_type
        if quality is not None:
            params["quality"] = quality
        if uid is not None:
            params["uid"] = uid
        if file_path is not None:
            params["filePath"] = file_path
        return self.call_tool("take_screenshot", params)
    
    def take_snapshot(
        self,
        verbose: Optional[bool] = None,
        file_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        获取页面快照
        
        Args:
            verbose: 是否包含完整的 a11y 树信息，默认 False
            file_path: 保存路径（可选）
        """
        params = {}
        if verbose is not None:
            params["verbose"] = verbose
        if file_path is not None:
            params["filePath"] = file_path
        return self.call_tool("take_snapshot", params)
    
    def evaluate_script(self, function: str, args: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
        """
        执行 JavaScript 函数
        
        Args:
            function: JavaScript 函数声明（字符串格式）
                     例如: "() => { return document.title; }"
                     或: "(el) => { return el.innerText; }"
            args: 可选的参数列表，每个参数包含元素的 uid
                  例如: [{"uid": "element_123"}]
        
        Returns:
            执行结果
        """
        params = {"function": function}
        if args:
            params["args"] = args
        return self.call_tool("evaluate_script", params)
    
    def list_console_messages(self) -> Dict[str, Any]:
        """列出控制台消息"""
        return self.call_tool("list_console_messages")
    
    def get_console_message(self, message_id: int) -> Dict[str, Any]:
        """获取控制台消息详情"""
        return self.call_tool("get_console_message", {"msgid": message_id})
    
    def list_network_requests(self) -> Dict[str, Any]:
        """列出网络请求"""
        return self.call_tool("list_network_requests")
    
    def get_network_request(self, request_id: Optional[int] = None) -> Dict[str, Any]:
        """
        获取网络请求详情
        
        Args:
            request_id: 网络请求的 reqid（可选，如果省略则返回 DevTools Network 面板中当前选中的请求）
        """
        params = {}
        if request_id is not None:
            params["reqid"] = request_id
        return self.call_tool("get_network_request", params)
    
    def performance_start_trace(self, reload: bool, auto_stop: bool) -> Dict[str, Any]:
        """开始性能跟踪"""
        return self.call_tool("performance_start_trace", {
            "reload": reload,
            "autoStop": auto_stop,
        })
    
    def performance_stop_trace(self) -> Dict[str, Any]:
        """停止性能跟踪"""
        return self.call_tool("performance_stop_trace")
    
    def performance_analyze_insight(self, insight_name: str, insight_set_id: str) -> Dict[str, Any]:
        """分析性能洞察"""
        return self.call_tool("performance_analyze_insight", {
            "insightName": insight_name,
            "insightSetId": insight_set_id,
        })
    
    def emulate(
        self,
        network_conditions: Optional[str] = None,
        cpu_throttling_rate: Optional[float] = None,
        geolocation: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Any]:
        """
        模拟设备/网络
        
        Args:
            network_conditions: 网络条件 ("No emulation", "Offline", "Slow 3G", "Fast 3G", "Slow 4G", "Fast 4G")
            cpu_throttling_rate: CPU 降速因子 (1-20)，1 表示禁用
            geolocation: 地理位置 {"latitude": float, "longitude": float}，或 None 清除
        """
        params = {}
        if network_conditions is not None:
            params["networkConditions"] = network_conditions
        if cpu_throttling_rate is not None:
            params["cpuThrottlingRate"] = cpu_throttling_rate
        if geolocation is not None:
            params["geolocation"] = geolocation
        return self.call_tool("emulate", params)
    
    def resize_page(self, width: int, height: int) -> Dict[str, Any]:
        """调整页面大小"""
        return self.call_tool("resize_page", {
            "width": width,
            "height": height,
        })
    
    def fetch_douyin_video_links(
        self,
        url: str,
        initial_wait_ms: Optional[int] = None,
        play_wait_ms: Optional[int] = None,
        network_limit: Optional[int] = None,
        include_all_videos: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """
        获取抖音视频链接
        
        Args:
            url: 要打开的抖音页面 URL（必需）
            initial_wait_ms: 导航/注入后的初始等待时间（毫秒），默认 8000ms
            play_wait_ms: 自动播放后的等待时间（毫秒），默认 5000ms
            network_limit: 网络请求回退时检查的最近请求数量，默认 20
            include_all_videos: 是否包含所有视频元素（不仅限于正在播放的），默认 false
        
        Returns:
            包含视频链接列表和执行步骤信息的结果
        """
        params = {"url": url}
        if initial_wait_ms is not None:
            params["initialWaitMs"] = initial_wait_ms
        if play_wait_ms is not None:
            params["playWaitMs"] = play_wait_ms
        if network_limit is not None:
            params["networkLimit"] = network_limit
        if include_all_videos is not None:
            params["includeAllVideos"] = include_all_videos
        return self.call_tool("fetch_douyin_video_links", params)
    
    def download_douyin_video(
        self,
        url: str,
        file_path: Optional[str] = None,
        referer: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        下载抖音视频文件
        
        Args:
            url: 视频直链 URL（必需，例如：https://v26-web.douyinvod.com/...）
            file_path: 保存文件的路径（可选，如果省略则自动从 URL 提取视频 ID）
            referer: Referer 请求头（可选，默认 https://www.douyin.com）
        
        Returns:
            下载结果，包含文件名、文件大小等信息
        """
        params = {"url": url}
        if file_path is not None:
            params["filePath"] = file_path
        if referer is not None:
            params["referer"] = referer
        return self.call_tool("download_douyin_video", params)
    
    def fetch_and_download_douyin_video(
        self,
        url: str,
        initial_wait_ms: Optional[int] = None,
        play_wait_ms: Optional[int] = None,
        network_limit: Optional[int] = None,
        include_all_videos: Optional[bool] = None,
        file_path: Optional[str] = None,
        referer: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        获取抖音视频链接并下载视频到本地（fetch_douyin_video_links + download_douyin_video 合并）。
        
        Args:
            url: 要打开的抖音视频详情页 URL（必需）
            initial_wait_ms: 导航/注入后的初始等待时间（毫秒），默认 8000ms
            play_wait_ms: 自动播放后的等待时间（毫秒），默认 5000ms
            network_limit: 网络请求回退时检查的最近请求数量，默认 20
            include_all_videos: 是否包含所有视频元素（不仅限于正在播放的），默认 false
            file_path: 保存文件的路径（可选，省略则自动生成文件名）
            referer: Referer 请求头（可选，默认 https://www.douyin.com）
        
        Returns:
            包含导航、链接提取、下载步骤及最终文件路径的结果
        """
        params = {"url": url}
        if initial_wait_ms is not None:
            params["initialWaitMs"] = initial_wait_ms
        if play_wait_ms is not None:
            params["playWaitMs"] = play_wait_ms
        if network_limit is not None:
            params["networkLimit"] = network_limit
        if include_all_videos is not None:
            params["includeAllVideos"] = include_all_videos
        if file_path is not None:
            params["filePath"] = file_path
        if referer is not None:
            params["referer"] = referer
        return self.call_tool("fetch_and_download_douyin_video", params)
    
    def extract_autohome_post(
        self,
        url: str,
        output_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        提取汽车之家论坛帖子数据并保存为 JSON 文件
        
        Args:
            url: 汽车之家论坛帖子 URL（必需）
            output_dir: 保存 JSON 文件的目录路径（可选，如果省略则保存到当前工作目录）
        
        Returns:
            提取结果，包含保存的文件路径和提取的数据
        """
        params = {"url": url}
        if output_dir is not None:
            params["outputDir"] = output_dir
        return self.call_tool("extract_autohome_post", params)
    
    def extract_dcd_by_url(
        self,
        url: str,
        output_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        提取懂车帝社区帖子数据并保存为 JSON 文件
        
        Args:
            url: 懂车帝社区帖子 URL（必需）
            output_dir: 保存 JSON 文件的目录路径（可选，如果省略则保存到当前工作目录）
        
        Returns:
            提取结果，包含保存的文件路径和提取的数据
        """
        params = {"url": url}
        if output_dir is not None:
            params["outputDir"] = output_dir
        return self.call_tool("extract_dcd_by_url", params)

    def extract_autohome_chejiahao_info(
        self,
        url: str,
        output_dir: Optional[str] = None,
        initial_wait_ms: Optional[int] = None,
        scroll_loops: Optional[int] = None,
        network_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """提取汽车之家车家号 info 页数据并保存 JSON（MCP: extract_autohome_chejiahao_info）。"""
        params: Dict[str, Any] = {"url": url}
        if output_dir is not None:
            params["outputDir"] = output_dir
        if initial_wait_ms is not None:
            params["initialWaitMs"] = initial_wait_ms
        if scroll_loops is not None:
            params["scrollLoops"] = scroll_loops
        if network_limit is not None:
            params["networkLimit"] = network_limit
        return self.call_tool("extract_autohome_chejiahao_info", params)

    def extract_dcd_video(
        self,
        url: str,
        output_dir: Optional[str] = None,
        initial_wait_ms: Optional[int] = None,
        scroll_loops: Optional[int] = None,
        network_limit: Optional[int] = None,
    ) -> Dict[str, Any]:
        """提取懂车帝视频页数据并保存 JSON（MCP: extract_dcd_video）。"""
        params: Dict[str, Any] = {"url": url}
        if output_dir is not None:
            params["outputDir"] = output_dir
        if initial_wait_ms is not None:
            params["initialWaitMs"] = initial_wait_ms
        if scroll_loops is not None:
            params["scrollLoops"] = scroll_loops
        if network_limit is not None:
            params["networkLimit"] = network_limit
        return self.call_tool("extract_dcd_video", params)

    def voc_store_from_json_file(
        self,
        file_path: str,
        platform: Optional[str] = None,
        save_raw_json: bool = False,
        store_raw_in_mongo: bool = True,
    ) -> Dict[str, Any]:
        """将本地爬取 JSON 入库 MongoDB（MCP: voc_store_from_json_file）。"""
        params: Dict[str, Any] = {
            "filePath": file_path,
            "saveRawJson": save_raw_json,
            "storeRawInMongo": store_raw_in_mongo,
        }
        if platform is not None:
            params["platform"] = platform
        return self.call_tool("voc_store_from_json_file", params)

    def voc_store_crawl_result(
        self,
        payload_json: str,
        platform: Optional[str] = None,
        save_raw_json: bool = True,
        store_raw_in_mongo: bool = True,
        parent_platform: Optional[str] = None,
        parent_content_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """将爬取结果 JSON 字符串入库 MongoDB（MCP: voc_store_crawl_result）。"""
        params: Dict[str, Any] = {
            "payloadJson": payload_json,
            "saveRawJson": save_raw_json,
            "storeRawInMongo": store_raw_in_mongo,
        }
        if platform is not None:
            params["platform"] = platform
        if parent_platform is not None:
            params["parentPlatform"] = parent_platform
        if parent_content_id is not None:
            params["parentContentId"] = parent_content_id
        return self.call_tool("voc_store_crawl_result", params)

    def voc_mongo_ping(self) -> Dict[str, Any]:
        """检测 MongoDB 连接（MCP: voc_mongo_ping）。"""
        return self.call_tool("voc_mongo_ping", {})


# ============================================================================
# LangChain 工具集成
# ============================================================================

def _validate_url(url: str) -> str:
    """
    验证和修正 URL 格式
    
    Args:
        url: 输入的 URL
        
    Returns:
        修正后的 URL
    """
    if not url:
        return "https://www.google.com"
    
    url = url.strip()
    
    # 处理无效的 URL
    invalid_urls = ["about:blank", "about:", "blank", ""]
    if url.lower() in invalid_urls:
        return "https://www.google.com"
    
    # 确保有协议前缀
    if not url.startswith(("http://", "https://")):
        # 如果是域名格式，添加 https://
        if "." in url and not url.startswith("/"):
            url = "https://" + url
        else:
            # 否则视为搜索词
            return f"https://www.google.com/search?q={url}"
    
    return url


def _format_result(result: Dict[str, Any], operation: str) -> Optional[str]:
    """
    格式化工具执行结果
    
    Args:
        result: MCP 返回的结果
        operation: 操作名称
        
    Returns:
        格式化的结果字符串
    """
    if not result:
        return f"{operation} 完成"
    
    # 提取文本内容
    content = result.get("content", [])
    if isinstance(content, list):
        texts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                texts.append(item.get("text", ""))
        if texts:
            # 保留换行，便于解析 MCP 多行 JSON（如 voc_store_* 入库结果）
            return "\n".join(texts)
    
    # 返回完整的 JSON 结果，不限制长度
    return json.dumps(result, ensure_ascii=False)


def _resolve_json_input_path(input_file: str) -> Optional[str]:
    """解析 JSON 输入路径：绝对路径、相对项目根、downloads/ 子路径。"""
    raw = (input_file or "").strip()
    if not raw:
        return None
    expanded = os.path.expanduser(raw)
    candidate = os.path.abspath(expanded)
    if os.path.isfile(candidate):
        return candidate
    path_obj = Path(expanded)
    if not path_obj.is_absolute():
        under_root = PROJECT_ROOT / expanded
        if os.path.isfile(under_root):
            return str(under_root)
        if expanded.replace("\\", "/").startswith("downloads/"):
            after = expanded.replace("\\", "/").split("downloads/", 1)[-1]
            under_downloads = DEFAULT_DOWNLOAD_DIR / after
            if os.path.isfile(under_downloads):
                return str(under_downloads)
    return candidate if os.path.isfile(candidate) else None


def _parse_json_object_from_mcp_text(text: str) -> Optional[Dict[str, Any]]:
    """从 MCP 文本响应中解析 JSON 对象（入库结果等）。"""
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    # voc_store_* / voc_mongo_ping：首行为 "# tool response"，其后为 pretty-printed JSON
    brace = stripped.find("{")
    if brace != -1:
        try:
            obj = json.loads(stripped[brace:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
    if "```json" in stripped:
        start = stripped.find("```json") + 7
        end = stripped.find("```", start)
        if end != -1:
            try:
                obj = json.loads(stripped[start:end].strip())
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                pass
    for line in stripped.splitlines():
        line = line.strip()
        if line.startswith("{"):
            try:
                obj = json.loads(line)
                if isinstance(obj, dict):
                    return obj
            except json.JSONDecodeError:
                continue
    return None


def _parse_voc_ingest_mcp_result(
    result: Dict[str, Any],
    operation: str,
) -> Dict[str, Any]:
    """解析 voc_store_* / voc_mongo_ping 的 MCP 返回为结构化字典。"""
    text = _format_result(result, operation) or ""
    parsed = _parse_json_object_from_mcp_text(text)
    if parsed is not None:
        return parsed
    if isinstance(result, dict) and result.get("isError"):
        return {"ok": False, "error": text or str(result)}
    return {"ok": False, "raw": text}


def _content_indicates_failure(content: Optional[str]) -> bool:
    """根据 MCP 返回的 content 文本判断是否实际为失败（如超时、Locator 错误）。
    用于在 MCP 未抛异常但返回错误信息时，将根级 success 置为 false。"""
    if not content or not isinstance(content, str):
        return False
    failure_indicators = (
        "Timed out",
        "Locator.click",
        "Locator.waitHandle",
        "No such element",
    )
    return any(ind in content for ind in failure_indicators)


def _tool_response_success(message: str, data: Optional[Dict[str, Any]] = None) -> str:
    """统一成功返回：便于模型判断该步骤执行成功。
    根级必含 success=true、message；可选 data（如 content、file_path、file_name 等）。"""
    payload: Dict[str, Any] = {"success": True, "message": message}
    if data is not None:
        payload["data"] = data
    return json.dumps(payload, ensure_ascii=False)


def _tool_response_error(message: str, error: Optional[str] = None) -> str:
    """统一失败返回：便于模型判断该步骤执行失败。
    根级必含 success=false、message；可选 error（详细原因）。"""
    payload: Dict[str, Any] = {"success": False, "message": message}
    if error is not None:
        payload["error"] = error
    return json.dumps(payload, ensure_ascii=False)


def create_browser_tools(mcp_client: ChromeDevToolsMCP) -> List:
    """
    创建浏览器自动化工具集
    
    这些工具可以被 LangChain Agent 使用来控制浏览器。
    
    注意：mcp_client 必须已经调用过 start() 方法！
    
    Args:
        mcp_client: 已启动的 MCP 客户端实例
        
    Returns:
        LangChain 工具列表
    """
    from langchain_core.tools import tool
    
    @tool
    def browser_navigate(url: str) -> Optional[str]:
        """
        导航浏览器到指定 URL。URL 必须是完整的 http:// 或 https:// 格式。
        
        Args:
            url: 目标 URL（可选，仅当 type=url 时使用）。如果省略 type 参数，默认为 "url" 类型。
            
        Returns:
            导航结果字符串，包含成功信息和页面列表
        """
        try:
            validated_url = _validate_url(url)
            result = mcp_client.navigate(validated_url)
            content = _format_result(result, f"导航到 {validated_url}")
            return _tool_response_success(f"导航到 {validated_url} 完成", data={"content": content})
        except Exception as e:
            return _tool_response_error("导航失败", str(e))
    
    @tool
    def browser_new_page(url: str) -> Optional[str]:
        """
        在浏览器中打开新页面并导航到指定 URL。URL 必须是完整的 http:// 或 https:// 格式。
        
        Args:
            url: 要在新页面中加载的 URL（必需）。
            
        Returns:
            页面创建结果字符串，包含成功信息和页面列表
        """
        try:
            validated_url = _validate_url(url)
            result = mcp_client.new_page(validated_url)
            content = _format_result(result, f"新页面已打开: {validated_url}")
            return _tool_response_success(f"新页面已打开: {validated_url}", data={"content": content})
        except Exception as e:
            return _tool_response_error("创建页面失败", str(e))
    
    @tool
    def browser_list_pages() -> Optional[str]:
        """
        获取浏览器中所有打开的页面列表。页面列表包含每个页面的 pageId 和 URL。
        
        Parameters: 无
        
        Returns:
            页面列表字符串，包含每个页面的 pageId 和 URL
        """
        try:
            result = mcp_client.list_pages()
            content = _format_result(result, "页面列表")
            return _tool_response_success("已获取页面列表", data={"content": content})
        except Exception as e:
            return _tool_response_error("获取页面列表失败", str(e))
    
    @tool
    def browser_select_page(pageId: int) -> Optional[str]:
        """
        选择并切换到指定的页面。
        
        Args:
            pageId: 要选择的页面的 ID（必需）。调用 browser_list_pages 获取可用页面列表。
            
        Returns:
            选择结果字符串，包含成功信息和页面列表
        """
        try:
            result = mcp_client.select_page(pageId, bring_to_front=True)
            content = _format_result(result, f"已切换到页面 {pageId}")
            return _tool_response_success(f"已切换到页面 {pageId}", data={"content": content})
        except Exception as e:
            return _tool_response_error("选择页面失败", str(e))
    
    @tool
    def browser_close_page(pageId: int) -> Optional[str]:
        """
        关闭指定的页面。
        
        Args:
            pageId: 要关闭的页面的 ID（必需）。调用 browser_list_pages 列出页面。
            
        Returns:
            关闭结果字符串，包含成功信息和页面列表
        """
        try:
            result = mcp_client.close_page(pageId)
            content = _format_result(result, f"已关闭页面 {pageId}")
            return _tool_response_success(f"已关闭页面 {pageId}", data={"content": content})
        except Exception as e:
            return _tool_response_error("关闭页面失败", str(e))
    
    @tool
    def browser_screenshot(
        full_page: bool = False,
        format_type: Optional[str] = None,
        quality: Optional[int] = None,
        uid: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> Optional[str]:
        """
        对页面或元素截图。
        
        Args:
            format_type: 保存截图的格式类型（枚举："png", "jpeg", "webp"，可选）。默认为 "png"。
            quality: JPEG 和 WebP 格式的压缩质量（0-100，数字，可选）。值越高表示质量越好但文件越大。PNG 格式忽略此参数。
            uid: 页面快照中元素的 uid（字符串，可选）。如果省略，则截取页面截图。
            full_page: 如果设置为 true，截取完整页面而不是当前可见的视口（布尔值，可选）。与 uid 不兼容。
            file_path: 保存截图的绝对路径或相对于当前工作目录的路径（字符串，可选）。如果省略，截图将附加到响应中而不是保存到文件。
                      - 如果路径包含目录，目录会自动创建
                      - 示例：`"screenshots/page.png"` 或 `"/tmp/screenshot.png"` 或 `"screenshot.png"`
            
        Returns:
            截图成功消息字符串
        """
        try:
            # 处理文件路径：确保目录存在
            processed_file_path = None
            if file_path:
                file_path = file_path.strip()
                if file_path:
                    file_path_obj = Path(file_path)
                    if not file_path_obj.is_absolute():
                        file_path_obj = Path.cwd() / file_path_obj
                    file_path_obj.parent.mkdir(parents=True, exist_ok=True)
                    processed_file_path = str(file_path_obj.absolute())
            
            result = mcp_client.take_screenshot(
                full_page=full_page if full_page else None,
                format_type=format_type,
                quality=quality,
                uid=uid,
                file_path=processed_file_path,
            )
            return _tool_response_success("截图成功", data={"file_path": processed_file_path} if processed_file_path else None)
        except Exception as e:
            return _tool_response_error("截图失败", str(e))
    
    @tool
    def browser_click(uid: str, dbl_click: bool = False) -> Optional[str]:
        """
        点击页面上的元素。
        
        Args:
            uid: 页面快照中元素的 uid（必需）。必须先调用 browser_snapshot 获取页面快照，然后从快照中提取元素的 uid。
            dbl_click: 是否双击，默认为 False（单击）。设置为 True 进行双击操作。
            
        Returns:
            点击结果字符串，包含成功信息和页面快照
        """
        try:
            if not uid or uid.strip() == "":
                return _tool_response_error("需要提供有效的元素 uid，请先调用 browser_snapshot 获取")
            result = mcp_client.click(uid.strip(), dbl_click=dbl_click)
            action = "双击" if dbl_click else "点击"
            content = _format_result(result, f"{action}元素 {uid}")
            if _content_indicates_failure(content):
                return _tool_response_error(f"{action}元素失败", error=content)
            return _tool_response_success(f"{action}元素完成", data={"content": content})
        except Exception as e:
            return _tool_response_error("点击失败", str(e))


    @tool
    def browser_click_at(x: int, y: int, dbl_click: bool = False) -> Optional[str]:
        """
        在页面的指定坐标处点击。
        通常用于无法获取元素 UID 的情况（如 Canvas、地图等）。
        
        Args:
            x: X 坐标（像素）
            y: Y 坐标（像素）
            dbl_click: 是否双击，默认为 False。
        """
        try:
            result = mcp_client.click_at(x, y, dbl_click=dbl_click)
            action = "双击" if dbl_click else "点击"
            content = _format_result(result, f"在坐标 ({x}, {y}) 处{action}")
            if _content_indicates_failure(content):
                return _tool_response_error(f"在坐标 ({x}, {y}) 处{action}失败", error=content)
            return _tool_response_success(f"在坐标 ({x}, {y}) 处{action}完成", data={"content": content})
        except Exception as e:
            return _tool_response_error("坐标点击失败", str(e))

    @tool
    def browser_click_by_vision(instruction: str) -> Optional[str]:
        """
        根据截图由大模型识别要点击的位置，再在该坐标执行点击。
        主要用途：点击页面广告（广告位通常无稳定 uid）；也可用于图片、Canvas、地图、弹窗关闭等。
        
        Args:
            instruction: 对要点击位置的描述，如「点击页面广告」「点击登录按钮」「点击弹窗右上角关闭」。
            
        Returns:
            成功时返回点击结果；失败时返回错误信息（如截图失败、模型未识别到位置等）。
        """
        instr = (instruction or "").strip()
        logger.info("[browser_click_by_vision] 调用 instruction=%r", instr)
        try:
            if not instr:
                logger.warning("[browser_click_by_vision] 缺少 instruction")
                return _tool_response_error("请提供要点击位置的描述，如：点击登录按钮")
            # 1) 截图（不落盘，仅取 base64）
            logger.info("[browser_click_by_vision] 步骤1: 获取页面截图...")
            result = mcp_client.take_screenshot(
                full_page=False, file_path=None, format_type="jpeg"
            )
            b64 = _extract_base64_from_mcp_result(result)
            if not b64:
                logger.warning("[browser_click_by_vision] 截图失败，无法提取 base64")
                return _tool_response_error("获取页面截图失败，无法进行视觉识别")
            logger.info("[browser_click_by_vision] 步骤1: 截图成功，base64 长度=%d", len(b64))
            # 2) 视觉模型识别坐标
            logger.info("[browser_click_by_vision] 步骤2: 视觉模型识别坐标 instruction=%r...", instr)
            xy = get_click_position_from_screenshot(
                screenshot_base64=b64,
                instruction=instr,
                image_format="jpeg",
            )
            if xy is None:
                # 预期内结果：模型无法在截图上给出有效坐标（非 MCP/代码异常）
                logger.warning(
                    "[browser_click_by_vision] 视觉模型未能识别出点击位置（get_click_position_from_screenshot 返回 None）"
                )
                return _tool_response_error(
                    "视觉模型未能识别出点击位置，请检查描述或稍后重试",
                    error="VISION_CLICK_NO_TARGET",
                )
            x, y = xy
            logger.info("[browser_click_by_vision] 步骤2: 识别到坐标 x=%d, y=%d", x, y)
            # 3) 执行 click_at
            logger.info("[browser_click_by_vision] 步骤3: 在 (%d, %d) 执行 click_at...", x, y)
            click_result = mcp_client.click_at(x, y, dbl_click=False)
            content = _format_result(click_result, f"在坐标 ({x}, {y}) 处点击")
            if _content_indicates_failure(content):
                logger.warning("[browser_click_by_vision] 点击执行失败 content=%s", content[:200])
                return _tool_response_error("视觉点击执行失败", error=content)
            logger.info("[browser_click_by_vision] 完成 instruction=%r -> (%d, %d)", instr, x, y)
            return _tool_response_success(
                f"已根据描述「{instr}」在坐标 ({x}, {y}) 处完成点击",
                data={"content": content, "x": x, "y": y},
            )
        except Exception as e:
            logger.exception("[browser_click_by_vision] 异常 instruction=%r: %s", instr, e)
            return _tool_response_error("视觉点击失败", str(e))

    
    @tool
    def browser_fill(uid: str, value: str) -> Optional[str]:
        """
        在输入框、文本区域中输入文本，或从 <select> 元素中选择选项。
        
        Args:
            uid: 页面快照中元素的 uid（必需）。必须先调用 browser_snapshot 获取页面快照，然后从快照中提取元素的 uid。
            value: 要填入的值（必需）。对于输入框和文本区域，这是要输入的文本；对于 <select> 元素，这是要选择的选项文本。
            
        Returns:
            填充结果字符串，包含成功信息和页面快照
        """
        try:
            if not uid or uid.strip() == "":
                return _tool_response_error("需要提供有效的元素 uid，请先调用 browser_snapshot 获取")
            if not value or value.strip() == "":
                return _tool_response_error("需要提供有效的 value 值")
            result = mcp_client.fill(uid.strip(), value)
            content = _format_result(result, f"已填入文本: {value}")
            if _content_indicates_failure(content):
                return _tool_response_error("填充失败", error=content)
            return _tool_response_success(f"已填入文本", data={"content": content})
        except Exception as e:
            return _tool_response_error("填充失败", str(e))
    
    @tool
    def browser_evaluate(function: str) -> Optional[str]:
        """
        在当前选中的页面内评估 JavaScript 函数。返回 JSON 格式的响应，因此返回值必须是 JSON 可序列化的。
        
        Args:
            function: 要在当前选中页面中执行的 JavaScript 函数声明（字符串，必需）。
                     无参数示例: `() => { return document.title }` 或 `async () => { return await fetch("example.com") }`
                     带参数示例: `(el) => { return el.innerText; }`
                     
        **重要说明**：
        - 参数名必须是 'function'（不是 'script'）
        - 可以传入函数声明（推荐）或普通表达式（会自动包装）
        
        **普通表达式自动包装**：
        - 属性访问: "document.body.innerText" → 自动包装为 "() => { return document.body.innerText; }"
        - 方法调用: "document.querySelector('button')" → 自动包装为 "() => { return document.querySelector('button'); }"
        - 多行代码: "const x = 1; return x;" → 自动包装为 "() => { const x = 1; return x; }"
        
        **提取页面文本内容的最佳实践**：
        - ✅ 推荐: "document.body.innerText" 或 "document.body.textContent"（直接获取整个页面文本，无重复）
        - ❌ 不推荐: "Array.from(document.querySelectorAll('body *')).map(el => el.textContent).join(' ')"（会重复提取父元素和子元素的文本）
        - 原因: 父元素的 textContent 包含子元素的文本，遍历所有元素会导致重复
            
        Returns:
            执行结果字符串（JSON 格式）
        """
        try:
            # 验证函数格式（基本检查）
            function = function.strip()
            if not function:
                return _tool_response_error("函数不能为空")
            
            # 如果用户传入的是普通代码（不是函数声明），尝试包装成函数
            if not (function.startswith("(") or function.startswith("function") or function.startswith("async")):
                # 尝试自动包装：如果是单行表达式（不包含分号和花括号），包装成箭头函数
                if ";" not in function and "{" not in function:
                    # 单行表达式，包装成返回语句
                    function = f"() => {{ return {function}; }}"
                else:
                    # 多行代码，包装成函数体（不添加 return，因为可能已经有 return）
                    function = f"() => {{ {function} }}"
            
            result = mcp_client.evaluate_script(function)
            content = _format_result(result, "JavaScript 执行结果")
            return _tool_response_success("JavaScript 执行完成", data={"content": content})
        except Exception as e:
            return _tool_response_error("JavaScript 执行失败", str(e))
    
    @tool
    def browser_snapshot(verbose: bool = False, file_path: Optional[str] = None) -> Optional[str]:
        """
        基于 a11y 树获取当前选中页面的文本快照。快照列出页面元素及其唯一标识符（uid）。始终使用最新的快照。优先使用快照而不是截图。快照会指示 DevTools Elements 面板中选中的元素（如果有）。
        
        Args:
            verbose: 是否包含完整 a11y 树中所有可用信息（布尔值，可选）。默认为 False。
            file_path: 保存快照的绝对路径或相对于当前工作目录的路径（字符串，可选）。如果省略，快照将附加到响应中而不是保存到文件。
                      - 如果路径包含目录，目录会自动创建
                      - 示例：`"snapshots/page.txt"` 或 `"/tmp/snapshot.txt"` 或 `"snapshot.txt"`
        
        Returns:
            页面快照字符串，包含元素列表和 uid
        """
        try:
            # 处理文件路径：确保目录存在
            processed_file_path = None
            if file_path:
                file_path = file_path.strip()
                if file_path:
                    file_path_obj = Path(file_path)
                    if not file_path_obj.is_absolute():
                        file_path_obj = Path.cwd() / file_path_obj
                    file_path_obj.parent.mkdir(parents=True, exist_ok=True)
                    processed_file_path = str(file_path_obj.absolute())
            
            result = mcp_client.take_snapshot(
                verbose=verbose,
                file_path=processed_file_path
            )
            content = _format_result(result, "页面快照")
            return _tool_response_success("已获取页面快照", data={"content": content, "file_path": processed_file_path} if processed_file_path else {"content": content})
        except Exception as e:
            return _tool_response_error("获取快照失败", str(e))
    
    @tool
    def browser_wait_for(text: str, timeout: int = 10000) -> Optional[str]:
        """
        等待指定文本出现在选中的页面上。
        
        Args:
            text: 要出现在页面上的文本（必需）。
            timeout: 最大等待时间（毫秒，可选）。如果设置为 0，将使用默认超时时间。默认值为 10000（10秒）。
            
        Returns:
            等待结果字符串，包含成功信息和页面快照
        """
        try:
            result = mcp_client.wait_for(text, timeout)
            content = _format_result(result, f"等待文本 '{text}'")
            return _tool_response_success(f"等待文本出现完成", data={"content": content})
        except Exception as e:
            return _tool_response_error("等待超时", str(e))
    
    @tool
    def browser_console_messages() -> Optional[str]:
        """
        列出当前选中页面自上次导航以来的所有控制台消息。
        
        Parameters: 无（可选参数可通过底层 MCP 客户端调用）
        
        Returns:
            控制台消息列表字符串
        """
        try:
            result = mcp_client.list_console_messages()
            content = _format_result(result, "控制台消息")
            return _tool_response_success("已获取控制台消息列表", data={"content": content})
        except Exception as e:
            return _tool_response_error("获取控制台消息失败", str(e))
    
    @tool
    def browser_network_requests() -> Optional[str]:
        """
        列出当前选中页面自上次导航以来的所有请求。
        
        Parameters: 无（可选参数可通过底层 MCP 客户端调用）
        
        Returns:
            网络请求列表字符串
        """
        try:
            result = mcp_client.list_network_requests()
            content = _format_result(result, "网络请求")
            return _tool_response_success("已获取网络请求列表", data={"content": content})
        except Exception as e:
            return _tool_response_error("获取网络请求失败", str(e))
    
    @tool
    def browser_press_key(key: str) -> Optional[str]:
        """
        按下键盘按键或组合键。当其他输入方法（如 fill()）无法使用时使用此工具（例如键盘快捷键、导航键或特殊按键组合）。
        
        Args:
            key: 按键或组合键字符串
                - 单个按键：如 "Enter", "Tab", "Escape", "Backspace", "Delete", "Space", "A", "KeyA", "F1" 等
                - 组合键：使用 `+` 连接修饰键和主键，如 "Control+A", "Control+Shift+R", "Shift++"
                - 修饰键：Control, Shift, Alt, Meta（可组合使用）
                - 格式规则：组合键必须用 `+` 连接，不能有空格；最后一个键是主键，前面的是修饰键
                
        Returns:
            按键执行结果，包含成功信息和页面快照
        """
        try:
            result = mcp_client.press_key(key)
            content = _format_result(result, f"按下 {key} 键")
            return _tool_response_success(f"已按下 {key} 键", data={"content": content})
        except Exception as e:
            return _tool_response_error("按键失败", str(e))
    
    @tool
    def browser_hover(uid: str) -> Optional[str]:
        """
        将鼠标悬停在页面上提供的元素上。
        
        Args:
            uid: 页面快照中元素的 uid（必需）。必须先调用 browser_snapshot 获取页面快照，然后从快照中提取元素的 uid。
            
        Returns:
            悬停结果字符串，包含成功信息和页面快照
        """
        try:
            if not uid or uid.strip() == "":
                return _tool_response_error("需要提供有效的元素 uid，请先调用 browser_snapshot 获取")
            result = mcp_client.hover(uid.strip())
            content = _format_result(result, f"悬停在元素 {uid}")
            return _tool_response_success("悬停完成", data={"content": content})
        except Exception as e:
            return _tool_response_error("悬停失败", str(e))
    
    @tool
    def browser_drag(from_uid: str, to_uid: str) -> Optional[str]:
        """
        将一个元素拖拽到另一个元素上。
        
        Args:
            from_uid: 要拖拽的元素的 uid（必需）。必须先调用 browser_snapshot 获取页面快照，然后从快照中提取元素的 uid。
            to_uid: 要放置到的目标元素的 uid（必需）。必须先调用 browser_snapshot 获取页面快照，然后从快照中提取元素的 uid。
            
        Returns:
            拖拽结果字符串，包含成功信息和页面快照
        """
        try:
            if not from_uid or from_uid.strip() == "":
                return _tool_response_error("需要提供有效的源元素 uid，请先调用 browser_snapshot 获取")
            if not to_uid or to_uid.strip() == "":
                return _tool_response_error("需要提供有效的目标元素 uid，请先调用 browser_snapshot 获取")
            result = mcp_client.drag(from_uid.strip(), to_uid.strip())
            content = _format_result(result, f"从元素 {from_uid} 拖拽到 {to_uid}")
            return _tool_response_success("拖拽完成", data={"content": content})
        except Exception as e:
            return _tool_response_error("拖拽失败", str(e))
    
    @tool
    def browser_fill_form(elements: str) -> Optional[str]:
        """
        一次性填写多个表单元素。
        
        Args:
            elements: 要填写的元素数组（JSON 字符串格式，必需）。每个元素必须包含：
                     - uid: 页面快照中元素的 uid（字符串）
                     - value: 要填入的值（字符串）
                     示例: '[{"uid": "element_123", "value": "text1"}, {"uid": "element_456", "value": "text2"}]'
                     必须先调用 browser_snapshot 获取页面快照，然后从快照中提取元素的 uid。
                      
        Returns:
            批量填写结果字符串，包含成功信息和页面快照
        """
        try:
            if not elements or elements.strip() == "":
                return _tool_response_error("需要提供有效的元素列表（JSON 格式）")
            try:
                elements_list = json.loads(elements)
                if not isinstance(elements_list, list):
                    return _tool_response_error("elements 必须是数组格式")
                if len(elements_list) == 0:
                    return _tool_response_error("elements 数组不能为空")
                for i, elem in enumerate(elements_list):
                    if not isinstance(elem, dict):
                        return _tool_response_error(f"第 {i+1} 个元素必须是对象格式")
                    if "uid" not in elem or "value" not in elem:
                        return _tool_response_error(f"第 {i+1} 个元素必须包含 uid 和 value 字段")
                    if not elem.get("uid") or not elem.get("value"):
                        return _tool_response_error(f"第 {i+1} 个元素的 uid 和 value 不能为空")
                result = mcp_client.fill_form(elements_list)
                content = _format_result(result, f"已批量填写 {len(elements_list)} 个表单元素")
                return _tool_response_success(f"已批量填写 {len(elements_list)} 个表单元素", data={"content": content})
            except json.JSONDecodeError as e:
                return _tool_response_error("JSON 解析失败，请确保 elements 是有效的 JSON 数组格式", str(e))
        except Exception as e:
            return _tool_response_error("批量填写表单失败", str(e))
    
    @tool
    def browser_handle_dialog(action: str, prompt_text: Optional[str] = None) -> Optional[str]:
        """
        如果浏览器对话框已打开，使用此命令来处理它。
        
        Args:
            action: 操作类型（必需），枚举值：
                   - "accept": 接受/确认对话框
                   - "dismiss": 取消/拒绝对话框
            prompt_text: 可选的提示文本（字符串）。仅用于 prompt 对话框，需要输入文本时使用。
            
        Returns:
            对话框处理结果字符串
        """
        try:
            if action not in ["accept", "dismiss"]:
                return _tool_response_error("action 必须是 'accept' 或 'dismiss'")
            result = mcp_client.handle_dialog(action, prompt_text)
            action_text = "接受" if action == "accept" else "取消"
            content = _format_result(result, f"已{action_text}对话框")
            return _tool_response_success(f"已{action_text}对话框", data={"content": content})
        except Exception as e:
            return _tool_response_error("处理对话框失败", str(e))
    
    @tool
    def browser_upload_file(uid: str, file_path: str) -> Optional[str]:
        """
        通过提供的元素上传文件。
        
        Args:
            uid: 文件输入元素的 uid，或会打开文件选择器的元素的 uid（必需）。必须先调用 browser_snapshot 获取页面快照，然后从快照中提取元素的 uid。
            file_path: 要上传的文件的本地路径（必需）。必须是绝对路径或相对于当前工作目录的路径。
            
        Returns:
            文件上传结果字符串，包含成功信息和页面快照
        """
        try:
            if not uid or uid.strip() == "":
                return _tool_response_error("需要提供有效的元素 uid，请先调用 browser_snapshot 获取文件输入元素的 uid")
            if not file_path or file_path.strip() == "":
                return _tool_response_error("需要提供有效的文件路径")
            file_path_obj = Path(file_path.strip())
            if not file_path_obj.exists():
                return _tool_response_error("文件不存在", file_path)
            if not file_path_obj.is_file():
                return _tool_response_error("路径不是文件", file_path)
            result = mcp_client.upload_file(uid.strip(), str(file_path_obj.absolute()))
            content = _format_result(result, f"已上传文件: {file_path}")
            return _tool_response_success("文件上传完成", data={"content": content, "file_path": str(file_path_obj.absolute())})
        except Exception as e:
            return _tool_response_error("上传文件失败", str(e))
    
    @tool
    def browser_get_console_message(message_id: int) -> Optional[str]:
        """
        通过 ID 获取控制台消息。可以通过调用 browser_console_messages 获取所有消息。
        
        Args:
            message_id: 页面控制台消息列表中控制台消息的 msgid（数字，必需）。
            
        Returns:
            控制台消息详情字符串
        """
        try:
            result = mcp_client.get_console_message(message_id)
            content = _format_result(result, f"控制台消息 {message_id} 详情")
            return _tool_response_success(f"已获取控制台消息 {message_id} 详情", data={"content": content})
        except Exception as e:
            return _tool_response_error("获取控制台消息失败", str(e))
    
    @tool
    def browser_get_network_request(request_id: Optional[int] = None) -> Optional[str]:
        """
        通过可选的 reqid 获取网络请求，如果省略则返回 DevTools Network 面板中当前选中的请求。
        
        Args:
            request_id: 网络请求的 reqid（数字，可选）。如果省略，返回 DevTools Network 面板中当前选中的请求。
            
        Returns:
            网络请求详情字符串
        """
        try:
            result = mcp_client.get_network_request(request_id)
            content = _format_result(result, "网络请求详情")
            return _tool_response_success("已获取网络请求详情", data={"content": content})
        except Exception as e:
            return _tool_response_error("获取网络请求失败", str(e))
    
    @tool
    def browser_performance_start_trace(reload: bool = False, auto_stop: bool = True) -> Optional[str]:
        """
        在选中的页面上开始性能跟踪记录。可用于查找性能问题和洞察，以改善页面性能。还会报告页面的 Core Web Vital (CWV) 分数。
        
        Args:
            reload: 确定一旦跟踪开始，是否应自动重新加载当前选中的页面（布尔值，必需）。如果 reload 或 autoStop 设置为 true，请在使用此工具开始跟踪之前，使用 browser_navigate 工具将页面导航到正确的 URL。
            auto_stop: 确定跟踪记录是否应自动停止（布尔值，必需）。
            
        Returns:
            跟踪启动结果字符串
        """
        try:
            result = mcp_client.performance_start_trace(reload, auto_stop)
            content = _format_result(result, "性能跟踪已启动")
            return _tool_response_success("性能跟踪已启动", data={"content": content})
        except Exception as e:
            return _tool_response_error("启动性能跟踪失败", str(e))
    
    @tool
    def browser_performance_stop_trace() -> Optional[str]:
        """
        停止选中页面上活动的性能跟踪记录。
        
        Parameters: 无（可选参数可通过底层 MCP 客户端调用）
        
        Returns:
            跟踪停止结果字符串
        """
        try:
            result = mcp_client.performance_stop_trace()
            content = _format_result(result, "性能跟踪已停止")
            return _tool_response_success("性能跟踪已停止", data={"content": content})
        except Exception as e:
            return _tool_response_error("停止性能跟踪失败", str(e))
    
    @tool
    def browser_performance_analyze_insight(insight_name: str, insight_set_id: str) -> Optional[str]:
        """
        提供跟踪记录结果中突出显示的特定性能洞察集的特定性能洞察的详细信息。
        
        Args:
            insight_name: 要获取更多信息的洞察名称（字符串，必需）。例如："DocumentLatency" 或 "LCPBreakdown"。
            insight_set_id: 特定洞察集的 id（字符串，必需）。仅使用 "Available insight sets" 列表中给出的 id。
            
        Returns:
            性能分析结果字符串
        """
        try:
            result = mcp_client.performance_analyze_insight(insight_name, insight_set_id)
            content = _format_result(result, f"性能洞察分析: {insight_name}")
            return _tool_response_success(f"性能洞察分析完成: {insight_name}", data={"content": content})
        except Exception as e:
            return _tool_response_error("性能分析失败", str(e))
    
    @tool
    def browser_emulate(
        network_conditions: Optional[str] = None,
        cpu_throttling_rate: Optional[float] = None,
        latitude: Optional[float] = None,
        longitude: Optional[float] = None,
    ) -> Optional[str]:
        """
        模拟选中页面的各种功能。
        
        Args:
            network_conditions: 限制网络（枚举，可选）。设置为 "No emulation" 以禁用。如果省略，条件保持不变。
                               可选值: "No emulation", "Offline", "Slow 3G", "Fast 3G", "Slow 4G", "Fast 4G"
            cpu_throttling_rate: 表示 CPU 降速因子（数字，1-20，可选）。将速率设置为 1 以禁用限制。如果省略，限制保持不变。
            latitude: 地理位置纬度（-90 到 90，可选）。与 longitude 一起使用设置地理位置。
            longitude: 地理位置经度（-180 到 180，可选）。与 latitude 一起使用设置地理位置。
            
        Returns:
            模拟设置结果字符串
        """
        try:
            geolocation = None
            if latitude is not None and longitude is not None:
                geolocation = {"latitude": latitude, "longitude": longitude}
            result = mcp_client.emulate(
                network_conditions=network_conditions,
                cpu_throttling_rate=cpu_throttling_rate,
                geolocation=geolocation,
            )
            content = _format_result(result, "设备/网络模拟已设置")
            return _tool_response_success("设备/网络模拟已设置", data={"content": content})
        except Exception as e:
            return _tool_response_error("模拟设置失败", str(e))
    
    @tool
    def browser_resize_page(width: int, height: int) -> Optional[str]:
        """
        调整选中页面的窗口大小，使页面具有指定的尺寸。
        
        Args:
            width: 页面宽度（数字，必需）。
            height: 页面高度（数字，必需）。
            
        Returns:
            调整结果字符串，包含成功信息和页面列表
        """
        try:
            result = mcp_client.resize_page(width, height)
            content = _format_result(result, f"页面大小已调整为 {width}x{height}")
            return _tool_response_success(f"页面大小已调整为 {width}x{height}", data={"content": content})
        except Exception as e:
            return _tool_response_error("调整页面大小失败", str(e))
    
    @tool
    def browser_fetch_douyin_video_links(
        url: str,
        initial_wait_ms: Optional[int] = None,
        play_wait_ms: Optional[int] = None,
        network_limit: Optional[int] = None,
        include_all_videos: Optional[bool] = None,
    ) -> Optional[str]:
        """
        针对抖音视频下载任务，根据当前页面中的URL，获取抖音视频真实的视频链接。
        
        Args:
            url: 要导航到的抖音视频 URL（字符串，必需）。**必须使用进入具体视频后的详情页链接，而不是列表页、话题页或创作者主页的链接**，即用户在浏览器中点击进入某个视频后地址栏中的页面链接。
            initial_wait_ms: 导航/注入后允许页面加载的初始等待时间（整数，可选）。默认 8000ms。
            play_wait_ms: 自动播放后允许请求/URL 出现的等待时间（整数，可选）。默认 5000ms。
            network_limit: 回退时检查的最近网络钩子请求数量（整数，可选）。默认 20。
            include_all_videos: 如果为 true，包含所有 <video> 元素的 URL（不仅限于当前正在播放的）（布尔值，可选）。默认为 false（仅当前正在播放的）。
            
        Returns:
            包含视频链接列表和执行步骤信息的 JSON 结果字符串
        """
        try:
            result = mcp_client.fetch_douyin_video_links(
                url=url,
                initial_wait_ms=initial_wait_ms,
                play_wait_ms=play_wait_ms,
                network_limit=network_limit,
                include_all_videos=include_all_videos,
            )
            content = _format_result(result, "获取抖音视频链接")
            return _tool_response_success("已获取抖音视频链接", data={"content": content})
        except RuntimeError as e:
            error_msg = str(e)
            if "not found" in error_msg.lower() or "未找到" in error_msg:
                return _tool_response_error(
                    "获取抖音视频链接失败。请确认 MCP 服务器已启动、chrome-devtools-mcp 已编译且工具已注册。",
                    error_msg,
                )
            return _tool_response_error("获取抖音视频链接失败", error_msg)
        except Exception as e:
            return _tool_response_error("获取抖音视频链接失败", str(e))
    
    @tool
    def browser_download_douyin_video(
        url: str,
        referer: Optional[str] = None,
    ) -> Optional[str]:
        """
        针对抖音视频下载任务，根据browser_fetch_douyin_video_links工具提供的视频URL，下载抖音视频文件。
        
        Args:
            url: browser_fetch_douyin_video_links工具的返回结果中获取的视频URL（字符串，必需）。例如：https://v26-web.douyinvod.com/...
            referer: Referer 请求头值（字符串，可选）。**默认值**：https://www.douyin.com。
            
        Returns:
            下载结果字符串，包含文件名、文件大小等信息。视频固定保存到 <项目根>/downloads/抖音/。
        """
        try:
            download_dir = DEFAULT_DOWNLOAD_DIR / "抖音"
            download_dir.mkdir(parents=True, exist_ok=True)
            processed_file_path = None
            try:
                from urllib.parse import urlparse, parse_qs
                parsed_url = urlparse(url)
                query_params = parse_qs(parsed_url.query)
                vid = query_params.get("__vid", [None])[0]
                if vid:
                    default_filename = f"douyin_video_{vid}.mp4"
                else:
                    import time
                    default_filename = f"douyin_video_{int(time.time() * 1000)}.mp4"
                processed_file_path_obj = download_dir / default_filename
                processed_file_path = str(processed_file_path_obj.absolute())
            except Exception:
                pass
            if not processed_file_path:
                import time
                processed_file_path = str(
                    (download_dir / f"douyin_video_{int(time.time() * 1000)}.mp4").absolute()
                )

            result = mcp_client.download_douyin_video(
                url=url,
                file_path=processed_file_path,
                referer=referer,
            )

            # 从 MCP 返回结果中解析最终保存的文件路径与文件名，返回结构化 JSON，方便前端消费
            try:
                content = result.get("content", [])
                texts = []
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            texts.append(item.get("text", ""))
                full_text = "\n".join(texts)

                file_path_in_text = None

                # 优先从最终 JSON 结果中的 "filename" 字段解析
                m = re.search(r'"filename"\s*:\s*"([^"]+)"', full_text)
                if m:
                    file_path_in_text = m.group(1)
                else:
                    # 回退：从日志行 "Video saved to: xxx" 中解析
                    m2 = re.search(r"Video saved to:\s*([^\s]+)", full_text)
                    if m2:
                        file_path_in_text = m2.group(1)

                file_name = None
                if file_path_in_text:
                    file_name = os.path.basename(file_path_in_text)

                return _tool_response_success(
                    "抖音视频已下载",
                    data={"file_path": file_path_in_text, "file_name": file_name, "tool": "browser_download_douyin_video"},
                )
            except Exception:
                content = _format_result(result, "下载抖音视频")
                return _tool_response_success("下载抖音视频完成", data={"content": content})
        except Exception as e:
            return _tool_response_error("下载抖音视频失败", str(e))
    
    @tool
    def browser_fetch_and_download_douyin_video(
        url: str,
        initial_wait_ms: Optional[int] = None,
        play_wait_ms: Optional[int] = None,
        network_limit: Optional[int] = None,
        include_all_videos: Optional[bool] = None,
        referer: Optional[str] = None,
    ) -> Optional[str]:
        """
        针对抖音视频下载任务，下载视频到本地。
        相当于 browser_fetch_douyin_video_links + browser_download_douyin_video 的合并。
        
        Args:
            url: 要导航到的抖音视频详情页 URL（字符串，必需）。必须使用具体视频详情页链接，而非列表/话题/主页。
            initial_wait_ms: 导航/注入后等待页面加载的时间（毫秒，可选）。**默认值**：8000。
            play_wait_ms: 自动播放后等待请求/URL 出现的时间（毫秒，可选）。**默认值**：5000。
            network_limit: 回退时检查的最近网络请求数量（可选）。**默认值**：20。
            include_all_videos: 是否包含所有 <video> 的 URL，不仅当前播放（可选）。**默认值**：False。
            referer: Referer 请求头（可选）。**默认值**：https://www.douyin.com。
            
        Returns:
            包含 status、tool、file_path、file_name 等的结果 JSON 或错误信息。视频固定保存到 <项目根>/downloads/抖音/。
        """
        try:
            default_file_path = DEFAULT_DOWNLOAD_DIR / "抖音"
            default_file_path.mkdir(parents=True, exist_ok=True)
            processed_file_path = str(default_file_path.absolute())

            result = mcp_client.fetch_and_download_douyin_video(
                url=url,
                initial_wait_ms=initial_wait_ms,
                play_wait_ms=play_wait_ms,
                network_limit=network_limit,
                include_all_videos=include_all_videos,
                file_path=processed_file_path,
                referer=referer,
            )

            content = result.get("content", [])
            texts = []
            if isinstance(content, list):
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        texts.append(item.get("text", ""))
            full_text = "\n".join(texts)

            file_path_in_text = None
            file_name = None
            # 0) 优先解析约定行 MCP_TOOL_RESULT: {"success", "file_path", "file_name"}，从根本上避免正则/非结构化解析
            canonical = parse_mcp_tool_result(full_text)
            if canonical is not None:
                file_path_in_text = canonical.get("file_path") or canonical.get("filename")
                file_name = canonical.get("file_name")
                if isinstance(file_path_in_text, str):
                    file_path_in_text = file_path_in_text.strip()
                else:
                    file_path_in_text = None
                if file_name is not None and not isinstance(file_name, str):
                    file_name = None

            if not file_path_in_text:
                # 1) 正则：filename / file_path / filePath / Video saved to（回退）
                for pattern in (
                    r'"filename"\s*:\s*"([^"]+)"',
                    r'"file_path"\s*:\s*"([^"]+)"',
                    r'"filePath"\s*:\s*"([^"]+)"',
                    r"Video saved to:\s*([^\s\n]+)",
                    r"视频已保存[至到]\s*[：:]\s*([^\s\n]+)",
                    r'"download"\s*:\s*\{[^}]*"filename"\s*:\s*"([^"]+)"',
                ):
                    m = re.search(pattern, full_text)
                    if m:
                        candidate = m.group(1).strip().rstrip('",')
                        if candidate.endswith(".mp4") or "/" in candidate or "\\" in candidate:
                            file_path_in_text = candidate
                            break
                # 2) 从 content 中按段解析 JSON 取 filename / file_path（含 MCP 的 result.download.filename）
            if not file_path_in_text and texts:
                for block in texts:
                    block = (block or "").strip()
                    if not block.startswith("{"):
                        continue
                    try:
                        obj = json.loads(block)
                        file_path_in_text = (
                            obj.get("filename") or obj.get("file_path") or obj.get("filePath")
                        )
                        if not file_path_in_text and isinstance(obj.get("download"), dict):
                            file_path_in_text = (
                                obj["download"].get("filename")
                                or obj["download"].get("file_path")
                                or obj["download"].get("filePath")
                            )
                        if isinstance(file_path_in_text, str) and file_path_in_text.strip():
                            file_path_in_text = file_path_in_text.strip()
                            break
                    except Exception:
                        continue
            # 3) 回退：若传入的是目录且未解析到路径，取该目录下最新 .mp4
            if not file_path_in_text and processed_file_path:
                try:
                    dir_path = Path(processed_file_path)
                    if dir_path.is_dir():
                        mp4s = sorted(
                            dir_path.glob("*.mp4"),
                            key=lambda p: p.stat().st_mtime,
                            reverse=True,
                        )
                        if mp4s:
                            file_path_in_text = str(mp4s[0].absolute())
                except Exception:
                    pass

            if file_name is None and file_path_in_text:
                file_name = os.path.basename(file_path_in_text)

            # 无法解析到 file_path 时视为失败，返回 success=False，便于模型区分并排查 MCP 响应
            if not file_path_in_text:
                has_mcp_line = MCP_TOOL_RESULT_PREFIX in (full_text or "")
                logger.warning(
                    "browser_fetch_and_download_douyin_video: 无法从 MCP 响应解析 file_path。"
                    " 响应中含 MCP_TOOL_RESULT 行=%s，响应前 500 字=%s",
                    has_mcp_line,
                    (full_text or "")[:500],
                )
                return _tool_response_error(
                    "下载完成但无法解析到保存路径（file_path 为空）",
                    error=(
                        "MCP 响应中应包含一行 MCP_TOOL_RESULT:{\"success\":true,\"file_path\":\"...\",\"file_name\":\"...\"}，"
                        "当前未解析到。请检查 MCP 端 fetch_and_download_douyin_video 是否在响应末尾输出了该行；"
                        "若为目录保存，可检查传入的 file_path 对应目录下是否有新生成的 .mp4。"
                    ),
                )
            return _tool_response_success(
                "抖音视频已获取并下载",
                data={"file_path": file_path_in_text, "file_name": file_name, "tool": "browser_fetch_and_download_douyin_video"},
            )
        except Exception as e:
            return _tool_response_error("获取并下载抖音视频失败", str(e))
    
    def _extract_autohome_post_impl(
        url: str,
        tool_name: str,
    ) -> Optional[str]:
        """未装饰的实现函数：供多个工具名复用，避免 @tool 后的 StructuredTool 不可调用问题。"""
        try:
            default_output_dir = DEFAULT_DOWNLOAD_DIR / "汽车之家"
            default_output_dir.mkdir(parents=True, exist_ok=True)
            processed_output_dir = str(default_output_dir.absolute())

            result = mcp_client.extract_autohome_post(
                url=url,
                output_dir=processed_output_dir,
            )

            # 从返回内容中解析保存的文件路径，并返回结构化 JSON
            try:
                content = result.get("content", [])
                texts = []
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            texts.append(item.get("text", ""))
                full_text = "\n".join(texts)

                file_path_in_text = None
                # 查找形如 “JSON file saved: /path/to/file.json” 的日志行
                m = re.search(r"JSON file saved:\s*([^\s]+)", full_text)
                if m:
                    file_path_in_text = m.group(1)

                file_name = None
                if file_path_in_text:
                    file_name = os.path.basename(file_path_in_text)

                return _tool_response_success(
                    "汽车之家帖子数据已提取并保存",
                    data={"file_path": file_path_in_text, "file_name": file_name, "tool": tool_name},
                )
            except Exception:
                content = _format_result(result, "提取汽车之家帖子数据")
                return _tool_response_success("提取汽车之家帖子数据完成", data={"content": content})
        except Exception as e:
            return _tool_response_error("提取汽车之家帖子数据失败", str(e))

    @tool
    def browser_extract_autohome_post_detail(
        url: str,
    ) -> Optional[str]:
        """
        只针对汽车之家网站提取【汽车之家单条帖子/资讯详情页】内容并保存为 JSON，不能用于提取其他网站的帖子详情页内容。
        
        什么时候用：
        - ✅ 当前页面/链接是「具体内容详情页」，能看到正文与评论（例如：club.autohome.com.cn 的帖子页，或资讯详情页）
        - ✅ 你已经从搜索/列表页点击进入某一条内容详情页，URL 已变成具体帖子/文章的详情地址
        
        什么时候不要用：
        - ❌ 在「搜索结果聚合页」直接用（典型 URL：sou.autohome.com.cn/zonghe?...），该页不是详情页，通常会导出空壳 JSON
        - ❌ 在仅有标题列表的聚合页/频道页使用（没有正文与评论的页面）
        
        推荐步骤示例：
        1) 先搜索：在汽车之家站内搜索关键词，进入结果页（sou.autohome.com.cn/zonghe）
        2) 点进详情：browser_click 点击某条“论坛/帖子/资讯”进入详情页（URL 发生变化）
        3) 再抽取：browser_extract_autohome_post_detail(url=当前详情页URL)
        4) 最后总结：结合 JSON 中的 title/content/comments 输出分析或继续做 VOC
        
        Args:
            url: 汽车之家「详情页」URL（字符串，必需）。必须是真实可访问的详情页链接。
        
        Returns:
            JSON 字符串：包含 file_path、file_name，指向保存的详情内容 JSON 文件。文件固定保存到 <项目根>/downloads/汽车之家/。
        """
        return _extract_autohome_post_impl(url=url, tool_name="browser_extract_autohome_post_detail")

    def _extract_autohome_chejiahao_impl(
        url: str,
        tool_name: str,
        initial_wait_ms: Optional[int] = None,
        scroll_loops: Optional[int] = None,
        network_limit: Optional[int] = None,
    ) -> Optional[str]:
        try:
            out_dir = DEFAULT_DOWNLOAD_DIR / "汽车之家"
            out_dir.mkdir(parents=True, exist_ok=True)
            processed_output_dir = str(out_dir.absolute())
            result = mcp_client.extract_autohome_chejiahao_info(
                url=url,
                output_dir=processed_output_dir,
                initial_wait_ms=initial_wait_ms,
                scroll_loops=scroll_loops,
                network_limit=network_limit,
            )
            try:
                content = result.get("content", [])
                texts = []
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            texts.append(item.get("text", ""))
                full_text = "\n".join(texts)
                file_path_in_text = _parse_mcp_saved_file_from_text(full_text)
                file_name = os.path.basename(file_path_in_text) if file_path_in_text else None
                return _tool_response_success(
                    "汽车之家车家号页面数据已提取并保存",
                    data={"file_path": file_path_in_text, "file_name": file_name, "tool": tool_name},
                )
            except Exception:
                content = _format_result(result, "提取汽车之家车家号数据")
                return _tool_response_success("提取汽车之家车家号数据完成", data={"content": content})
        except Exception as e:
            return _tool_response_error("提取汽车之家车家号数据失败", str(e))

    @tool
    def browser_extract_autohome_chejiahao_info(
        url: str,
        initial_wait_ms: Optional[int] = None,
        scroll_loops: Optional[int] = None,
        network_limit: Optional[int] = None,
    ) -> Optional[str]:
        """
        只针对汽车之家网站提取【汽车之家车家号】info 详情页（含视频直链、正文、评论等）并保存为 JSON，不能用于提取其他网站的车家号info详情页内容。
        对应 MCP 工具 extract_autohome_chejiahao_info；文件固定保存到 <项目根>/downloads/汽车之家/。

        Args:
            url: 车家号 info 页 URL，例如 https://chejiahao.autohome.com.cn/info/25061145
            initial_wait_ms: 导航后等待毫秒（可选，默认由 MCP 侧决定，常见 4000）
            scroll_loops: 滚动加载评论轮数（可选）
            network_limit: 网络钩子检查条数（可选）
        """
        return _extract_autohome_chejiahao_impl(
            url=url,
            tool_name="browser_extract_autohome_chejiahao_info",
            initial_wait_ms=initial_wait_ms,
            scroll_loops=scroll_loops,
            network_limit=network_limit,
        )
    
    def _extract_dcd_post_impl(
        url: str,
        tool_name: str,
    ) -> Optional[str]:
        """未装饰的实现函数：供多个工具名复用，避免 @tool 后的 StructuredTool 不可调用问题。"""
        try:
            default_output_dir = DEFAULT_DOWNLOAD_DIR / "懂车帝"
            default_output_dir.mkdir(parents=True, exist_ok=True)
            processed_output_dir = str(default_output_dir.absolute())

            result = mcp_client.extract_dcd_by_url(
                url=url,
                output_dir=processed_output_dir,
            )

            # 从返回内容中解析保存的文件路径，并返回结构化 JSON
            try:
                content = result.get("content", [])
                texts = []
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            texts.append(item.get("text", ""))
                full_text = "\n".join(texts)

                file_path_in_text = None
                # 查找形如 “JSON文件已保存: /path/to/file.json” 的日志行
                m = re.search(r"JSON文件已保存:\s*([^\s]+)", full_text)
                if m:
                    file_path_in_text = m.group(1)

                file_name = None
                if file_path_in_text:
                    file_name = os.path.basename(file_path_in_text)

                return _tool_response_success(
                    "懂车帝帖子数据已提取并保存",
                    data={"file_path": file_path_in_text, "file_name": file_name, "tool": tool_name},
                )
            except Exception:
                content = _format_result(result, "提取懂车帝帖子数据")
                return _tool_response_success("提取懂车帝帖子数据完成", data={"content": content})
        except Exception as e:
            return _tool_response_error("提取懂车帝帖子数据失败", str(e))

    @tool
    def browser_extract_dcd_post_detail(
        url: str,
    ) -> Optional[str]:
        """
        只针对懂车帝网站提取【懂车帝单条帖子详情页】内容并保存为 JSON，不能用于提取其他网站的帖子详情页内容。
        
        什么时候用：
        - ✅ URL 指向单条帖子详情页（通常形如：https://www.dongchedi.com/ugc/article/xxxx 或帖子详情链接）
        - ✅ 你已从列表/搜索/信息流点击进入具体帖子详情页，页面能看到正文与评论
        
        什么时候不要用：
        - ❌ 在列表/聚合页直接用（如频道信息流、搜索结果页、推荐流），这些页面不是单帖详情页
        - ❌ URL 不确定是否为详情页时，不要“猜 URL”，先 browser_click 进入详情页或从快照中取真实详情页链接
        
        推荐步骤示例：
        1) 先搜索/浏览列表：找到目标帖子条目
        2) 点进详情：browser_click 进入帖子详情页（URL 发生变化）
        3) 再抽取：browser_extract_dcd_post_detail(url=当前详情页URL)
        4) 最后总结：结合 JSON 中的 title/content/comments 输出分析或继续做 VOC
        
        Args:
            url: 懂车帝「帖子详情页」URL（字符串，必需）。必须是真实可访问的详情页链接。
        
        Returns:
            JSON 字符串：包含 file_path、file_name，指向保存的详情内容 JSON 文件。文件固定保存到 <项目根>/downloads/懂车帝/。
        """
        return _extract_dcd_post_impl(url=url, tool_name="browser_extract_dcd_post_detail")

    def _extract_dcd_video_impl(
        url: str,
        tool_name: str,
        initial_wait_ms: Optional[int] = None,
        scroll_loops: Optional[int] = None,
        network_limit: Optional[int] = None,
    ) -> Optional[str]:
        try:
            out_dir = DEFAULT_DOWNLOAD_DIR / "懂车帝"
            out_dir.mkdir(parents=True, exist_ok=True)
            processed_output_dir = str(out_dir.absolute())
            result = mcp_client.extract_dcd_video(
                url=url,
                output_dir=processed_output_dir,
                initial_wait_ms=initial_wait_ms,
                scroll_loops=scroll_loops,
                network_limit=network_limit,
            )
            try:
                content = result.get("content", [])
                texts = []
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            texts.append(item.get("text", ""))
                full_text = "\n".join(texts)
                file_path_in_text = _parse_mcp_saved_file_from_text(full_text)
                file_name = os.path.basename(file_path_in_text) if file_path_in_text else None
                return _tool_response_success(
                    "懂车帝视频页数据已提取并保存",
                    data={"file_path": file_path_in_text, "file_name": file_name, "tool": tool_name},
                )
            except Exception:
                content = _format_result(result, "提取懂车帝视频页数据")
                return _tool_response_success("提取懂车帝视频页数据完成", data={"content": content})
        except Exception as e:
            return _tool_response_error("提取懂车帝视频页数据失败", str(e))

    @tool
    def browser_extract_dcd_video(
        url: str,
        initial_wait_ms: Optional[int] = None,
        scroll_loops: Optional[int] = None,
        network_limit: Optional[int] = None,
    ) -> Optional[str]:
        """
        只针对懂车帝网站提取【懂车帝视频详情页】元数据、视频直链、评论等并保存为 JSON，不能用于提取其他网站的视频详情页内容
        对应 MCP 工具 extract_dcd_video；文件固定保存到 <项目根>/downloads/懂车帝/。

        Args:
            url: 懂车帝视频页 URL，例如 https://www.dongchedi.com/video/7567211858810159659
            initial_wait_ms: 导航后等待毫秒（可选）
            scroll_loops: 滚动轮数（可选）
            network_limit: 网络钩子检查条数（可选）
        """
        return _extract_dcd_video_impl(
            url=url,
            tool_name="browser_extract_dcd_video",
            initial_wait_ms=initial_wait_ms,
            scroll_loops=scroll_loops,
            network_limit=network_limit,
        )

    def _resolve_analysis_output_path(input_file: str) -> str:
        """根据 input 路径解析 analysis 输出路径，保持与 downloads 一致的子目录结构"""
        analysis_base = DEFAULT_ANALYSIS_DIR
        input_path = Path(input_file)
        input_str = str(input_path).replace("\\", "/")
        stem, ext = input_path.stem, input_path.suffix
        after_downloads = ""
        if "downloads/" in input_str:
            after_downloads = input_str.split("downloads/")[-1]
        elif "downloads\\" in input_str:
            after_downloads = input_str.split("downloads\\")[-1].replace("\\", "/")
        if after_downloads and "/" in after_downloads:
            subdir = "/".join(after_downloads.split("/")[:-1])
            output_rel = f"{subdir}/{stem}_analyzed{ext}"
        else:
            output_rel = f"{stem}_analyzed{ext}"
        output_path = analysis_base / output_rel
        output_path.parent.mkdir(parents=True, exist_ok=True)
        return str(output_path)

    def _resolve_video_analysis_output_path(video_path: str) -> str:
        """根据视频路径解析 analysis 输出路径，保持与 downloads 一致的子目录结构（同 browser_analyze_voc）。
        例如 downloads/抖音/xxx.mp4 -> analysis/抖音/xxx_analysis.json"""
        analysis_base = DEFAULT_ANALYSIS_DIR
        input_path = Path(video_path)
        input_str = str(input_path).replace("\\", "/")
        stem = input_path.stem
        after_downloads = ""
        if "downloads/" in input_str:
            after_downloads = input_str.split("downloads/")[-1]
        elif "downloads\\" in input_str:
            after_downloads = input_str.split("downloads\\")[-1].replace("\\", "/")
        if after_downloads and "/" in after_downloads:
            subdir = "/".join(after_downloads.split("/")[:-1])
            output_rel = f"{subdir}/{stem}_analysis.json"
        else:
            output_rel = f"抖音/{stem}_analysis.json"  # 默认放 analysis/抖音/ 下
        output_path = analysis_base / output_rel
        output_path.parent.mkdir(parents=True, exist_ok=True)
        return str(output_path)

    def _resolve_video_input_path(video_file: str) -> Optional[str]:
        """解析视频输入路径：先按绝对/当前工作目录解析，若不存在则尝试相对于项目根、downloads/抖音。与 browser_analyze_voc 的 input_file 解析方式一致。"""
        raw = (video_file or "").strip()
        if not raw:
            return None
        expanded = os.path.expanduser(raw)
        # 1) 先按 abspath(expanduser) 解析（与 browser_analyze_voc 一致）
        candidate = os.path.abspath(expanded)
        if os.path.isfile(candidate):
            return candidate
        # 2) 若为相对路径且不存在，尝试相对于项目根
        path_obj = Path(expanded)
        if not path_obj.is_absolute():
            under_root = PROJECT_ROOT / expanded
            if os.path.isfile(under_root):
                return str(under_root)
            # 3) 仅文件名时尝试 downloads/抖音/
            if len(path_obj.parts) <= 1:
                under_douyin = DEFAULT_DOWNLOAD_DIR / "抖音" / path_obj.name
                if os.path.isfile(under_douyin):
                    return str(under_douyin)
            else:
                # 如 "downloads/抖音/xxx.mp4" 已在上方 PROJECT_ROOT / expanded 试过；再试 DEFAULT_DOWNLOAD_DIR / 后半段
                if expanded.replace("\\", "/").startswith("downloads/"):
                    after = expanded.replace("\\", "/").split("downloads/", 1)[-1]
                    under_downloads = DEFAULT_DOWNLOAD_DIR / after
                    if os.path.isfile(under_downloads):
                        return str(under_downloads)
        return candidate  # 返回首次解析结果，由调用方报错「文件不存在」

    @tool
    def browser_voc_store_from_json_file(
        input_file: str,
        platform: Optional[str] = None,
    ) -> str:
        """
        将爬取保存的 JSON 文件解析并写入 MongoDB（content_items、comments、raw_documents）。
        在 browser_extract_autohome_post_detail / browser_extract_dcd_post_detail 等提取完成后调用。

        Args:
            input_file: JSON 文件路径（必需）。通常为 extract 工具返回的 file_path 或绝对路径。
            platform: 平台覆盖（可选），如 autohome、dongchedi；省略则按 JSON 内容自动识别。

        Returns:
            JSON 字符串：success、message、ingest_result（含 ok、platform、content_id、comments_upserted 等）
        """
        try:
            input_path = _resolve_json_input_path(input_file)
            if not input_path:
                return json.dumps({
                    "success": False,
                    "message": "input_file 不能为空。请先使用 extract 工具提取并保存 JSON，再传入 file_path。",
                    "input_file": input_file,
                    "ingest_result": None,
                }, ensure_ascii=False, indent=2)
            if not os.path.isfile(input_path):
                reason = "文件不存在" if not os.path.exists(input_path) else "路径存在但不是文件"
                return json.dumps({
                    "success": False,
                    "message": f"输入文件验证失败：{reason}。路径: {input_path}",
                    "input_file": input_path,
                    "ingest_result": None,
                }, ensure_ascii=False, indent=2)
            mcp_result = mcp_client.voc_store_from_json_file(
                file_path=input_path,
                platform=platform,
                save_raw_json=False,
                store_raw_in_mongo=True,
            )
            ingest = _parse_voc_ingest_mcp_result(mcp_result, "MongoDB 入库")
            ok = bool(ingest.get("ok"))
            return json.dumps({
                "success": ok,
                "message": "MongoDB 入库成功" if ok else f"MongoDB 入库失败: {ingest.get('error', ingest)}",
                "input_file": input_path,
                "ingest_result": ingest,
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({
                "success": False,
                "message": f"MongoDB 入库失败: {e}",
                "input_file": input_file,
                "ingest_result": None,
            }, ensure_ascii=False, indent=2)

    @tool
    def browser_voc_mongo_ping() -> str:
        """
        检测 MongoDB 连接是否正常，并返回数据库名、集合列表、content_items 文档数。
        无需浏览器；可在入库前用于排查 MONGO_URI / MONGO_DB 配置。

        Returns:
            JSON 字符串：success、message、ping_result
        """
        try:
            mcp_result = mcp_client.voc_mongo_ping()
            ping = _parse_voc_ingest_mcp_result(mcp_result, "MongoDB 连接检测")
            ok = bool(ping.get("ok"))
            return json.dumps({
                "success": ok,
                "message": "MongoDB 连接正常" if ok else "MongoDB 连接异常",
                "ping_result": ping,
            }, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({
                "success": False,
                "message": f"MongoDB 连接检测失败: {e}",
                "ping_result": None,
            }, ensure_ascii=False, indent=2)

    @tool
    def browser_filter_voc(input_file: str) -> str:
        """
        对懂车帝或汽车之家的主贴 JSON 进行内容筛选，返回 need_analysis 表示是否值得分析。与 browser_analyze_voc 为独立工具，可单独使用或组合使用。
        
        Args:
            input_file: 输入 JSON 文件路径（必需）。通常是 browser_extract_dcd_post_detail 或 browser_extract_autohome_post_detail 保存的文件。
        
        Returns:
            处理结果 JSON 字符串，包含 success、message、input_file、need_analysis、filter_result
        """
        try:
            input_path = os.path.abspath(os.path.expanduser((input_file or "").strip()))
            if not input_path:
                return json.dumps({
                    "success": False,
                    "message": "input_file 不能为空。请先使用 browser_extract_autohome_post_detail 或 browser_extract_dcd_post_detail 提取帖子详情并保存为 JSON，再传入该文件路径。",
                    "input_file": input_file,
                    "need_analysis": False,
                    "filter_result": None,
                }, ensure_ascii=False, indent=2)
            if not os.path.isfile(input_path):
                reason = "文件不存在" if not os.path.exists(input_path) else "路径存在但不是文件（可能是目录）"
                return json.dumps({
                    "success": False,
                    "message": f"输入文件验证失败：{reason}。路径: {input_path}。请先使用 browser_extract_autohome_post_detail 或 browser_extract_dcd_post_detail 提取帖子详情并保存为 JSON，确保路径正确后再调用本工具。",
                    "input_file": input_path,
                    "need_analysis": False,
                    "filter_result": None,
                }, ensure_ascii=False, indent=2)
            result = filter_voc_file(input_path)
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({
                "success": False,
                "message": f"VOC 筛选失败: {e}",
                "input_file": input_file,
                "need_analysis": False,
                "filter_result": None,
            }, ensure_ascii=False, indent=2)

    @tool
    def browser_analyze_voc(
        input_file: str,
    ) -> str:
        """
        对懂车帝或汽车之家的主贴 JSON 进行 VOC 内容解析与标签提取，输出带 _analyzed 后缀的 JSON。与 browser_filter_voc 为独立工具，可直接调用分析，也可在 filter 筛选通过后调用。
        
        Args:
            input_file: 输入 JSON 文件路径（必需）。来自工具 browser_extract_autohome_post_detail / browser_extract_dcd_post_detail，或 browser_filter_voc 的 output_file 字段。
        
        Returns:
            处理结果 JSON 字符串，包含 success、message、input_file、output_file。输出文件固定写入 <项目根>/analysis/，子目录结构与 downloads 对齐。
        """
        try:
            input_path = os.path.abspath(os.path.expanduser((input_file or "").strip()))
            if not input_path:
                return json.dumps({
                    "success": False,
                    "message": "input_file 不能为空。请先使用 browser_extract_autohome_post_detail 或 browser_extract_dcd_post_detail 提取帖子详情并保存为 JSON，再传入该文件路径。",
                    "input_file": input_file,
                    "output_file": None,
                }, ensure_ascii=False, indent=2)
            if not os.path.isfile(input_path):
                reason = "文件不存在" if not os.path.exists(input_path) else "路径存在但不是文件（可能是目录）"
                return json.dumps({
                    "success": False,
                    "message": f"输入文件验证失败：{reason}。路径: {input_path}。请先使用 browser_extract_autohome_post_detail 或 browser_extract_dcd_post_detail 提取帖子详情并保存为 JSON，确保路径正确后再调用本工具。",
                    "input_file": input_path,
                    "output_file": None,
                }, ensure_ascii=False, indent=2)
            output_file = _resolve_analysis_output_path(input_path)
            result = analyze_voc_file(input_path, output_file)
            if result.get("success") and result.get("output_file"):
                out_path = result["output_file"].replace("\\", "/")
                if "analysis/" in out_path:
                    rel_path = out_path.split("analysis/")[-1]
                else:
                    rel_path = os.path.basename(out_path)
                result["file_path"] = rel_path
                result["file_name"] = os.path.basename(out_path)
                result["source"] = "analysis"
                result["status"] = "success"
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({
                "success": False,
                "message": f"VOC 解析失败: {e}",
                "input_file": input_file,
                "output_file": None,
            }, ensure_ascii=False, indent=2)

    @tool
    def browser_analyze_video(
        video_file: str,
        question: Optional[str] = None,
        slice_seconds: int = 0,
    ) -> str:
        """
        从视频画面中提取文字（字幕、标牌、界面文字等），使用 Qwen3.5 视频模型，输出连贯文章或按问题说明提取。

        Args:
            video_file: 视频文件路径（必需）。可为绝对路径或相对于项目根的路径。通常来自 browser_fetch_and_download_douyin_video 返回的 file_path；也可为本地录屏等路径。省略时请勿调用本工具。
            question: 对提取方式的说明（可选）。例如「只提取屏幕底部字幕」；省略时使用默认提示输出一整篇连贯文章。
            slice_seconds: 按多少秒切片处理（默认 0）。0 表示不切片整体处理；大于 0 时按该秒数切片后分别提取再整合。

        Returns:
            处理结果 JSON 字符串，包含 success、message、summary、usage、output_file（保存到 analysis/抖音/ 或与 downloads 对应的 analysis 子目录）
        """
        try:
            video_path = _resolve_video_input_path(video_file)
            if not video_path:
                return json.dumps({
                    "success": False,
                    "message": "video_file 不能为空。请传入视频文件路径（可为 browser_fetch_and_download_douyin_video 返回的 file_path，或绝对路径/相对于项目根的路径）。",
                    "summary": None,
                    "usage": None,
                }, ensure_ascii=False, indent=2)
            if not os.path.isfile(video_path):
                reason = "文件不存在" if not os.path.exists(video_path) else "路径存在但不是文件（可能是目录）"
                return json.dumps({
                    "success": False,
                    "message": f"视频文件验证失败：{reason}。路径: {video_path}。请先使用 browser_fetch_and_download_douyin_video 下载视频或传入有效的本地视频路径后再调用本工具。",
                    "summary": None,
                    "usage": None,
                }, ensure_ascii=False, indent=2)
            output_file = _resolve_video_analysis_output_path(video_path)
            result = analyze_video_file(video_path, question=question, slice_seconds=slice_seconds, output_file=output_file)
            if result.get("success") and result.get("output_file"):
                out_path = result["output_file"].replace("\\", "/")
                if "analysis/" in out_path:
                    result["file_path"] = out_path.split("analysis/")[-1]
                else:
                    result["file_path"] = os.path.basename(out_path)
                result["file_name"] = os.path.basename(result["output_file"])
                result["source"] = "analysis"
            return json.dumps(result, ensure_ascii=False, indent=2)
        except Exception as e:
            return json.dumps({
                "success": False,
                "message": f"视频文字提取失败: {e}",
                "summary": None,
                "usage": None,
            }, ensure_ascii=False, indent=2)

    tools = [
        browser_navigate, # 导航浏览器到指定 URL。URL 必须是完整的 https:// 格式。  
        browser_new_page, # 在浏览器中打开新页面并导航到指定 URL。URL 必须是完整的 https:// 格式。
        browser_list_pages, # 列出浏览器中所有打开的页面（包含每个页面的 pageId 和 URL）
        browser_select_page, # 选择并切换到指定的页面。在点击链接后如果打开了新页面，需要先调用 browser_list_pages 获取新页面的 pageId，然后调用此工具切换到新页面。
        browser_close_page, # 关闭指定的页面。需要先调用 browser_list_pages 获取要关闭的页面的 pageId。
        browser_screenshot, # 对当前页面截图
        browser_click, # 点击页面上的元素。需要先调用 browser_snapshot 获取元素的 uid。
        browser_click_at, # 在页面的指定坐标处点击。通常用于无法获取元素 UID 的情况（如 Canvas、地图等）。
        browser_click_by_vision, # 根据截图由大模型识别要点击的位置并执行点击（主要点击页面广告；也可用于 Canvas、图片按钮等）。
        browser_fill, # 在输入框中填入文本。需要先调用 browser_snapshot 获取输入框的 uid。
        browser_fill_form, # 批量填写表单元素。可以一次性填写多个表单字段，提高效率。
        browser_hover, # 将鼠标悬停在页面元素上。需要先调用 browser_snapshot 获取元素的 uid。
        browser_drag, # 拖拽元素从源位置到目标位置。需要先调用 browser_snapshot 获取元素的 uid。
        browser_handle_dialog, # 处理浏览器对话框（如 alert、confirm、prompt）
        browser_upload_file, # 上传文件到页面上的文件输入元素。需要先调用 browser_snapshot 获取文件输入元素的 uid。
        browser_evaluate, # 在浏览器中执行 JavaScript 代码（支持函数声明或表达式，表达式会自动包装）
        browser_snapshot, # 获取当前页面的 DOM 快照，包含所有可交互元素及其 uid。
        browser_wait_for, # 等待页面上出现指定文本
        browser_console_messages, # 获取浏览器控制台的消息列表
        browser_get_console_message, # 获取指定控制台消息的详细信息
        browser_network_requests, # 获取页面的网络请求列表
        browser_get_network_request, # 获取指定网络请求的详细信息
        browser_press_key, # 按下键盘按键。常用于提交表单（Enter）或快捷键操作。
        browser_performance_start_trace, # 开始性能跟踪
        browser_performance_stop_trace, # 停止性能跟踪
        browser_performance_analyze_insight, # 分析性能洞察
        browser_emulate, # 模拟设备/网络条件
        browser_resize_page, # 调整浏览器页面大小（视口尺寸）
        browser_fetch_and_download_douyin_video, # 获取抖音视频链接并下载第一个视频到本地（合并 fetch + download，旧两工具已暂线屏蔽）
        # browser_fetch_douyin_video_links, # [暂线屏蔽]
        # browser_download_douyin_video, # [暂线屏蔽]
        browser_extract_autohome_post_detail, # 提取汽车之家「详情页」内容并保存为 JSON（推荐；先点进详情页再用）
        browser_extract_autohome_chejiahao_info, # 提取汽车之家车家号 info 页并保存 JSON（固定 downloads/汽车之家）
        browser_extract_dcd_post_detail, # 提取懂车帝「帖子详情页」内容并保存为 JSON（推荐；先点进详情页再用）
        browser_extract_dcd_video, # 提取懂车帝视频页并保存 JSON（固定 downloads/懂车帝）
        browser_filter_voc, # VOC 内容筛选：判断 JSON 是否值得分析（独立工具）
        browser_analyze_voc, # VOC 内容解析：对 JSON 进行标签提取（独立工具，可直接调用）
        browser_voc_store_from_json_file, # 将 extract 产出的 JSON 入库 MongoDB
        browser_voc_mongo_ping, # 检测 MongoDB 连接（无需浏览器）
        browser_analyze_video, # Qwen 视频文字提取：从视频画面提取字幕/标牌/界面文字
    ]
    
    return tools


# ============================================================================
# Cursor MCP 配置生成
# ============================================================================

def generate_cursor_mcp_config(config: Optional[ChromeDevToolsConfig] = None) -> Dict[str, Any]:
    """
    生成 Cursor IDE 的 MCP 配置
    
    Args:
        config: MCP 配置
        
    Returns:
        Cursor MCP 配置字典
    """
    config = config or ChromeDevToolsConfig()
    
    args = ["-y", "chrome-devtools-mcp@latest"]
    args.extend(config.to_args())
    
    return {
        "mcpServers": {
            "chrome-devtools": {
                "command": "npx",
                "args": args,
            }
        }
    }


def save_cursor_mcp_config(
    path: str = ".cursor/mcp.json",
    config: Optional[ChromeDevToolsConfig] = None,
) -> str:
    """
    保存 Cursor MCP 配置到文件
    
    Args:
        path: 配置文件路径
        config: MCP 配置
        
    Returns:
        配置文件路径
    """
    config_dict = generate_cursor_mcp_config(config)
    
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config_dict, f, indent=2, ensure_ascii=False)
    
    logger.info(f"Cursor MCP 配置已保存到: {config_path}")
    return str(config_path)

