# MCP 工具注册流程详解

本文档详细说明新工具如何自动导入和注册到 MCP 服务器。

## 完整流程

### 步骤 1: 定义工具（在 `src/tools/*.ts`）

在 `src/tools/script.ts` 中定义工具：

```typescript
export const fetchDouyinVideoLinks = defineTool({
  name: 'fetch_douyin_video_links',
  description: `...`,
  annotations: {
    category: ToolCategory.DEBUGGING,
    readOnlyHint: false,
  },
  schema: { ... },
  handler: async (request, response, context) => { ... },
});
```

**关键点**：使用 `export const` 导出，工具会被自动收集。

---

## 步骤 2: 工具自动收集（`src/tools/tools.ts`）

**当前代码**（无需修改）：

```typescript:chrome-devtools-mcp/src/tools/tools.ts
import * as consoleTools from './console.js';
import * as emulationTools from './emulation.js';
import * as extensionTools from './extensions.js';
import * as inputTools from './input.js';
import * as networkTools from './network.js';
import * as pagesTools from './pages.js';
import * as performanceTools from './performance.js';
import * as screenshotTools from './screenshot.js';
import * as scriptTools from './script.js';  // ← 你的抖音工具在这里
import * as snapshotTools from './snapshot.js';
import type {ToolDefinition} from './ToolDefinition.js';

const tools = [
  ...Object.values(consoleTools),
  ...Object.values(emulationTools),
  ...Object.values(extensionTools),
  ...Object.values(inputTools),
  ...Object.values(networkTools),
  ...Object.values(pagesTools),
  ...Object.values(performanceTools),
  ...Object.values(screenshotTools),
  ...Object.values(scriptTools),  // ← 自动收集 script.ts 中所有导出的工具
  ...Object.values(snapshotTools),
] as ToolDefinition[];

tools.sort((a, b) => {
  return a.name.localeCompare(b.name);
});

export {tools};
```

**说明**：
- `import * as scriptTools from './script.js'` 导入 `script.ts` 中所有导出
- `...Object.values(scriptTools)` 将 `script.ts` 中所有 `export const` 的工具加入数组
- **无需手动添加**：只要你在 `script.ts` 中用 `export const` 导出工具，就会自动被收集

**如果将来需要添加新的工具文件**（例如 `douyin.ts`）：

```typescript
// 1. 在 tools.ts 顶部添加导入
import * as douyinTools from './douyin.js';

// 2. 在 tools 数组中添加
const tools = [
  ...
  ...Object.values(douyinTools),  // ← 添加这一行
  ...
] as ToolDefinition[];
```

---

## 步骤 3: 工具自动注册（`src/main.ts`）

**当前代码**（无需修改）：

```typescript:chrome-devtools-mcp/src/main.ts
// 1. 导入所有工具
import {tools} from './tools/tools.js';

// 2. 定义注册函数
function registerTool(tool: ToolDefinition): void {
  // 检查工具类别是否启用（某些工具可能需要特定标志）
  if (
    tool.annotations.category === ToolCategory.EMULATION &&
    args.categoryEmulation === false
  ) {
    return;  // 跳过未启用的工具
  }
  // ... 其他类别检查 ...

  // 3. 注册到 MCP 服务器
  server.registerTool(
    tool.name,  // MCP 工具名称（例如 "fetch_douyin_video_links"）
    {
      description: tool.description,  // 工具描述
      inputSchema: tool.schema,        // 参数 schema（zod）
      annotations: tool.annotations,    // 工具元数据
    },
    async (params): Promise<CallToolResult> => {
      // 4. 工具调用处理器
      const guard = await toolMutex.acquire();  // 互斥锁，确保工具串行执行
      const startTime = Date.now();
      let success = false;
      try {
        logger(`${tool.name} request: ${JSON.stringify(params, null, '  ')}`);
        const context = await getContext();  // 获取浏览器上下文
        await context.detectOpenDevToolsWindows();
        const response = new McpResponse();
        
        // 5. 调用工具的实际 handler
        await tool.handler(
          {
            params,  // 客户端传入的参数
          },
          response,  // 响应对象（用于追加输出）
          context,   // 浏览器上下文
        );
        
        // 6. 处理响应并返回
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
        // 7. 错误处理
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
        // 8. 记录使用统计
        void clearcutLogger?.logToolInvocation({
          toolName: tool.name,
          success,
          latencyMs: Date.now() - startTime,
        });
        guard.dispose();
      }
    },
  );
}

// 9. 遍历所有工具并注册
for (const tool of tools) {
  registerTool(tool);
}
```

**说明**：
- `import {tools} from './tools/tools.js'` 导入步骤 2 收集的所有工具
- `for (const tool of tools)` 循环注册每个工具
- `registerTool(tool)` 将工具注册到 MCP 服务器
- **无需手动修改**：所有在 `tools` 数组中的工具都会自动注册

---

## 总结

### 当前状态（已配置好，无需修改）

✅ **步骤 2** (`src/tools/tools.ts`)：
- 已导入 `scriptTools`
- 已自动收集所有导出的工具

✅ **步骤 3** (`src/main.ts`)：
- 已导入 `tools`
- 已实现 `registerTool` 函数
- 已自动注册所有工具

### 添加新工具时

**只需在步骤 1 定义工具**：
1. 在 `src/tools/script.ts` 中添加 `export const myNewTool = defineTool({...})`
2. 运行 `npm run build` 编译
3. 工具会自动被收集和注册，无需修改步骤 2 和 3

**如果需要创建新的工具文件**（例如 `douyin.ts`）：
1. 创建 `src/tools/douyin.ts` 并导出工具
2. 在 `src/tools/tools.ts` 中添加：
   ```typescript
   import * as douyinTools from './douyin.js';
   // ...
   ...Object.values(douyinTools),
   ```
3. 运行 `npm run build` 编译

---

## 验证工具是否注册成功

### 方法 1: 检查编译后的代码

```bash
cd chrome-devtools-mcp
npm run build
grep -r "fetch_douyin_video_links" build/src/
```

### 方法 2: 使用 Python 脚本列出工具

```bash
python3 scripts/list_mcp_tools.py
```

### 方法 3: 直接调用工具测试

```python
result = await session.call_tool("fetch_douyin_video_links", {
    "url": "https://www.douyin.com"
})
```

如果调用成功，说明工具已正确注册。
