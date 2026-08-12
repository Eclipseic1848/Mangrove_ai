"""
MCP (Model Context Protocol) 适配层

提供与 MCP 服务器（如 Chrome DevTools MCP）的集成能力。

主要组件：
- ChromeDevToolsMCP: Chrome DevTools MCP 客户端（通过 stdio 与 MCP 服务器通信）
- ChromeDevToolsConfig: MCP 配置类
- create_browser_tools: 创建 LangChain 浏览器工具集
- BrowserUseAgent: 浏览器自动化 Agent（browser-use 风格）

使用方式：
1. 直接使用 MCP 客户端:
   ```python
   from src.tools.mcp import ChromeDevToolsMCP
   
   with ChromeDevToolsMCP() as mcp:
       mcp.navigate("https://example.com")
       mcp.take_screenshot()
   ```

2. 使用 Browser Agent:
   ```python
   from src.agent.browser_agent import BrowserUseAgent
   
   agent = BrowserUseAgent(auto_start_mcp=True)
   try:
       result = agent.run("打开 Google 并截图")
   finally:
       agent.stop()
   ```
"""

from .chrome_devtools import (
    ChromeDevToolsMCP,
    ChromeDevToolsConfig,
    create_browser_tools,
    generate_cursor_mcp_config,
    save_cursor_mcp_config,
    BrowserChannel,
)

__all__ = [
    # Chrome DevTools MCP 客户端
    "ChromeDevToolsMCP",
    "ChromeDevToolsConfig",
    "BrowserChannel",
    # 工具创建函数
    "create_browser_tools",
    # 配置生成函数
    "generate_cursor_mcp_config",
    "save_cursor_mcp_config",
]
