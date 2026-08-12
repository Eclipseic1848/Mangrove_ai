# 步骤 2 和 3 的代码说明

## 步骤 2: 工具自动收集

**文件**: `src/tools/tools.ts`

**完整代码**：

```typescript
/**
 * @license
 * Copyright 2025 Google LLC
 * SPDX-License-Identifier: Apache-2.0
 */

import * as consoleTools from './console.js';
import * as emulationTools from './emulation.js';
import * as extensionTools from './extensions.js';
import * as inputTools from './input.js';
import * as networkTools from './network.js';
import * as pagesTools from './pages.js';
import * as performanceTools from './performance.js';
import * as screenshotTools from './screenshot.js';
import * as scriptTools from './script.js';  // ← 包含你的抖音工具
import * as snapshotTools from './snapshot.js';
import type {ToolDefinition} from './ToolDefinition.js';

// 收集所有工具模块中导出的工具
const tools = [
  ...Object.values(consoleTools),
  ...Object.values(emulationTools),
  ...Object.values(extensionTools),
  ...Object.values(inputTools),
  ...Object.values(networkTools),
  ...Object.values(pagesTools),
  ...Object.values(performanceTools),
  ...Object.values(screenshotTools),
  ...Object.values(scriptTools),  // ← 自动收集 script.ts 中所有 export const 的工具
  ...Object.values(snapshotTools),
] as ToolDefinition[];

// 按名称排序
tools.sort((a, b) => {
  return a.name.localeCompare(b.name);
});

// 导出工具数组
export {tools};
```

**关键点**：
- `import * as scriptTools from './script.js'` - 导入 `script.ts` 的所有导出
- `...Object.values(scriptTools)` - 将 `script.ts` 中所有 `export const` 的工具展开到数组
- **无需修改**：只要在 `script.ts` 中用 `export const` 导出工具，就会自动被收集

---

## 步骤 3: 工具自动注册

**文件**: `src/main.ts`

**关键代码片段**：

### 3.1 导入工具数组

```typescript
import {tools} from './tools/tools.js';  // ← 导入步骤 2 收集的所有工具
```

### 3.2 定义注册函数

```typescript
const toolMutex = new Mutex();  // 互斥锁，确保工具串行执行

function registerTool(tool: ToolDefinition): void {
  // 检查工具类别是否启用（某些工具可能需要特定标志）
  if (
    tool.annotations.category === ToolCategory.EMULATION &&
    args.categoryEmulation === false
  ) {
    return;  // 跳过未启用的工具
  }
  // ... 其他类别检查（PERFORMANCE, NETWORK, EXTENSIONS 等）...

  // 注册到 MCP 服务器
  server.registerTool(
    tool.name,  // MCP 工具名称（例如 "fetch_douyin_video_links"）
    {
      description: tool.description,  // 工具描述
      inputSchema: tool.schema,        // 参数 schema（zod）
      annotations: tool.annotations,    // 工具元数据
    },
    async (params): Promise<CallToolResult> => {
      // 工具调用处理器
      const guard = await toolMutex.acquire();  // 获取互斥锁
      const startTime = Date.now();
      let success = false;
      try {
        // 记录请求日志
        logger(`${tool.name} request: ${JSON.stringify(params, null, '  ')}`);
        
        // 获取浏览器上下文
        const context = await getContext();
        await context.detectOpenDevToolsWindows();
        
        // 创建响应对象
        const response = new McpResponse();
        
        // 调用工具的实际 handler
        await tool.handler(
          {
            params,  // 客户端传入的参数
          },
          response,  // 响应对象（用于追加输出）
          context,   // 浏览器上下文
        );
        
        // 处理响应并返回
        const {content, structuredContent} = await response.handle(
          tool.name,
          context,
        );
        const result: CallToolResult & {
          structuredContent?: Record<string, unknown>;
        } = {
          content,
        };
        success = true;
        if (args.experimentalStructuredContent) {
          result.structuredContent = structuredContent;
        }
        return result;
      } catch (err) {
        // 错误处理
        logger(`${tool.name} error:`, err, err?.stack);
        let errorText = err && 'message' in err ? err.message : String(err);
        if ('cause' in err && err.cause) {
          errorText += `\nCause: ${err.cause.message}`;
        }
        return {
          content: [
            {
              type: 'text',
              text: errorText,
            },
          ],
          isError: true,
        };
      } finally {
        // 记录使用统计
        void clearcutLogger?.logToolInvocation({
          toolName: tool.name,
          success,
          latencyMs: Date.now() - startTime,
        });
        guard.dispose();  // 释放互斥锁
      }
    },
  );
}
```

### 3.3 注册所有工具

```typescript
// 遍历所有工具并注册
for (const tool of tools) {
  registerTool(tool);  // ← 自动注册每个工具
}
```

**关键点**：
- `import {tools} from './tools/tools.js'` - 导入步骤 2 收集的所有工具
- `for (const tool of tools)` - 遍历所有工具
- `registerTool(tool)` - 将每个工具注册到 MCP 服务器
- **无需修改**：所有在 `tools` 数组中的工具都会自动注册

---

## 总结

### ✅ 当前状态

**步骤 2** (`src/tools/tools.ts`)：
- ✅ 已导入 `scriptTools`
- ✅ 已自动收集所有导出的工具
- ✅ **无需修改**

**步骤 3** (`src/main.ts`)：
- ✅ 已导入 `tools`
- ✅ 已实现 `registerTool` 函数
- ✅ 已自动注册所有工具
- ✅ **无需修改**

### 📝 添加新工具时

**只需在步骤 1 定义工具**：
1. 在 `src/tools/script.ts` 中添加：
   ```typescript
   export const myNewTool = defineTool({
     name: 'my_new_tool',
     description: `...`,
     schema: { ... },
     handler: async (request, response, context) => { ... },
   });
   ```
2. 运行 `npm run build` 编译
3. **工具会自动被收集和注册，无需修改步骤 2 和 3**

### 🔍 验证

```bash
# 编译
cd chrome-devtools-mcp && npm run build

# 检查工具是否存在
grep -r "fetch_douyin_video_links" build/src/

# 或使用 Python 脚本列出工具
python3 scripts/list_mcp_tools.py
```
