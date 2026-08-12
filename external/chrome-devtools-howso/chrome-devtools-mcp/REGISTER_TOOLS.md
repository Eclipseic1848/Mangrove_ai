# 工具注册说明

## 工具注册流程

所有抖音相关的 MCP 工具已经自动注册，无需手动操作。以下是注册流程说明：

### 1. 工具定义 (`src/tools/script.ts`)

所有工具通过 `export const` 导出：

```typescript
export const fetchDouyinVideoLinks = defineTool({
  name: 'fetch_douyin_video_links',
  // ...
});
```

### 2. 工具收集 (`src/tools/tools.ts`)

通过 `Object.values(scriptTools)` 自动收集所有导出的工具：

```typescript
import * as scriptTools from './script.js';

const tools = [
  ...Object.values(scriptTools),  // 自动包含所有导出的工具
  // ... 其他工具模块
];
```

### 3. 工具注册 (`src/main.ts`)

在 `main.ts` 中导入并注册所有工具：

```typescript
import {tools} from './tools/tools.js';

// 注册所有工具
for (const tool of tools) {
  registerTool(tool);
}
```

## 验证工具注册

### 方法 1: 使用本地编译版本（推荐）

```bash
# 1. 编译项目
cd chrome-devtools-mcp
npm run build

# 2. 使用本地版本测试
cd ../scripts
USE_LOCAL_MCP=true python test_mcp_tools.py
```

### 方法 2: 直接调用工具验证

即使 `list_tools()` 无法列出工具，也可以直接调用工具来验证：

```python
# 直接调用工具
result = await session.call_tool("fetch_douyin_video_links", {"url": "https://www.douyin.com"})
```

如果工具调用成功，说明工具已正确注册。

## 常见问题

### Q: 工具未出现在列表中

**原因**: 可能使用了 npx 下载的版本，而不是本地编译的版本。

**解决**:
1. 确保已编译: `cd chrome-devtools-mcp && npm run build`
2. 使用本地版本测试: `USE_LOCAL_MCP=true python scripts/test_mcp_tools.py`

### Q: 如何确认工具已注册？

**方法**:
1. 检查编译后的文件: `grep -r "fetch_douyin_video_links" chrome-devtools-mcp/build/src/`
2. 直接调用工具测试
3. 查看 MCP 服务器日志

### Q: 工具注册失败怎么办？

**检查清单**:
- [ ] 工具是否在 `script.ts` 中正确导出？
- [ ] 是否运行了 `npm run build`？
- [ ] 是否使用了本地编译的版本？
- [ ] 工具名称是否正确（蛇形命名）？

## 使用本地版本

在 Python 脚本中使用本地编译的版本：

```python
from pathlib import Path
from mcp import StdioServerParameters

# 使用本地版本
mcp_dir = Path(__file__).parent.parent / "chrome-devtools-mcp"
index_js = mcp_dir / "build" / "src" / "index.js"

server_params = StdioServerParameters(
    command="node",
    args=[str(index_js), "--browser-url=http://127.0.0.1:9222"]
)
```

或者设置环境变量：

```bash
export USE_LOCAL_MCP=true
python scripts/test_mcp_tools.py
```
