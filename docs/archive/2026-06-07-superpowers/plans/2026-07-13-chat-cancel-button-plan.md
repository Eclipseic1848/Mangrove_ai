# 聊天取消按钮 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在聊天页面添加「取消任务」按钮，让用户能随时中断正在执行的 AI 流水线。

**Architecture:** 后端新增 `POST /api/chat/{conv_id}/cancel` 端点，调用 `_RUNNING` 注册表中的 `asyncio.Task.cancel()`；`pipeline()` 捕获 `asyncio.CancelledError` 优雅停止；前端在 `running` 状态显示取消按钮，同时调用 `cancelRef.current()`（断 SSE）和取消 API（停后端）。

**Tech Stack:** FastAPI + asyncio + React + TypeScript

## Global Constraints

- 只在 2 个文件中改动：`src/api/routes/chat.py` 和 `frontend/src/pages/Chat.tsx`
- `CancelledError`（Python 3.9+ 继承自 `BaseException`）必须在 `except Exception` 之前捕获
- 取消按钮使用 `destructive` variant（红色），视觉上明确是终止操作
- 取消按钮替代发送按钮位置，不额外增加布局宽度
- `cancelRef.current?.()` 和 `api.post(...)` 独立调用，互不依赖
- 取消后 convId 为 null 时不调后端 API（静默跳过）

---

### Task 1：后端 — 新增 cancel endpoint + CancelledError 处理

**Files:**
- Modify: `src/api/routes/chat.py:225-233`（pipeline 的 except 链）
- Modify: `src/api/routes/chat.py:252-263`（在 _RUNNING 和 running endpoint 之后新增 cancel endpoint）
- Verify: 无需新增 test 文件（手动验证即可）

**Interfaces:**
- Consumes: `_RUNNING: Dict[str, asyncio.Task]`（模块级，已在 `chat.py:252`）
- Produces: `POST /api/chat/{conv_id}/cancel` — 返回 `{"ok": true, "message": "已取消"}` 或 404

- [ ] **Step 1：确认 import 已就绪**

`asyncio` 已在 `chat.py:18` 导入，无需新增。

确认文件头部已有：
```python
import asyncio
import json
from fastapi import APIRouter, Depends, HTTPException
```

- [ ] **Step 2：pipeline() 添加 CancelledError 捕获**

在 pipeline() 的 `try` 块中，在 `except Exception` 之前增加 `except asyncio.CancelledError`：

```python
# 找到第 225 行，改为：
        except asyncio.CancelledError:
            store.add_message(conv_id, "assistant", "❌ 用户已取消任务")
            queue.put_nowait({"event": "result", "data": json.dumps({
                "conv_id": conv_id, "kind": "cancelled",
                "reply": "❌ 用户已取消任务",
            }, ensure_ascii=False)})
        except Exception as e:  # noqa: BLE001 异常也记入会话历史，切页面后回来能看到失败原因
```

注意：`CancelledError` 处理块放在 `Exception` 之前，因为它继承自 `BaseException` 而非 `Exception`，放在后面永远不会被匹配到。

`finally` 块（第 231-233 行）保持不变，它负责放 done 事件 + 清理 `_RUNNING`。

- [ ] **Step 3：新增 cancel endpoint**

在 `_RUNNING` 字典声明（第 252 行）之后、`chat_running` endpoint（第 255 行）之后，新增：

```python
@router.post("/{conv_id}/cancel")
async def cancel_pipeline(conv_id: str, user=Depends(get_current_user)):
    """取消正在执行的聊天流水线。"""
    user_id = user["user_id"]
    task = _RUNNING.get(f"{user_id}:{conv_id}")
    if not task or task.done():
        raise HTTPException(status_code=404, detail="没有正在执行的任务")
    task.cancel()
    return {"ok": True, "message": "已取消"}
```

- [ ] **Step 4：重启后端验证**

```bash
# 终止旧进程
taskkill /t /f /fi "WINDOWTITLE eq mangrove*" 2>nul

# 重新启动
cd d:\03.工作资料\05.华苏科技\06.实验代码\demo_agent_mcp_v1-develop
python -m src.api.app
```

在新终端中测试：
```bash
# 获取 token
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"admin123"}' | python -c "import sys,json;print(json.load(sys.stdin).get('access_token',''))")

# 先发起一个聊天（后台跑着）
curl -s -X POST http://localhost:8000/api/chat/stream \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: text/event-stream" \
  -H "Content-Type: application/json" \
  -d '{"content":"采集3条新闻"}' &

# 拿到 conv_id 后立即取消（需要 conv_id，从 SSE 的 meta 事件中提取）
# 测试取消接口（conv_id 替换为实际的）
curl -s -X POST "http://localhost:8000/api/chat/CONV_ID/cancel" \
  -H "Authorization: Bearer $TOKEN"

# 期望：{"ok": true, "message": "已取消"}
```

- [ ] **Step 5：Commit**

```bash
git add src/api/routes/chat.py
git commit -m "feat: 新增聊天取消 endpoint + pipeline CancelledError 处理"
```

---

### Task 2：前端 — 添加取消按钮

**Files:**
- Modify: `frontend/src/pages/Chat.tsx:179-236`（send 函数附近）
- Verify: `npm run build` 通过

**Interfaces:**
- Consumes: `cancelRef`（第 67 行已有）、`convId`（第 54 行已有）、`setRunning`/`setActiveNode`（第 57/61 行已有）
- Produces: `cancel()` 函数 + 条件渲染的取消按钮

- [ ] **Step 1：添加 cancel 函数**

在 `send` 函数定义（第 179 行）之后、`runAction`（第 239 行）之前，插入：

```typescript
  /** 取消当前正在执行的任务。 */
  const cancel = async () => {
    cancelRef.current?.();                    // 断 SSE 流（停止接收事件）
    if (convId) {                             // 通知后端取消 pipeline
      try { await api.post(`/api/chat/${convId}/cancel`); } catch { /* 静默 */ }
    }
    setRunning(false);
    setActiveNode(null);
  };
```

- [ ] **Step 2：替换发送按钮为条件渲染**

找到第 378 行：
```tsx
<Button onClick={send} disabled={!input.trim() || running} size="icon" className="h-11 w-11 shrink-0">
  <Send className="h-4 w-4" />
</Button>
```

改为：
```tsx
{running ? (
  <Button onClick={cancel} variant="destructive" className="h-11 shrink-0 px-5">
    取消任务
  </Button>
) : (
  <Button onClick={send} disabled={!input.trim()} size="icon" className="h-11 w-11 shrink-0">
    <Send className="h-4 w-4" />
  </Button>
)}
```

- [ ] **Step 3：前端编译**

```bash
cd d:\03.工作资料\05.华苏科技\06.实验代码\demo_agent_mcp_v1-develop\frontend
npm run build
```

预期：编译通过，无 TypeScript 错误。

- [ ] **Step 4：Commit**

```bash
git add frontend/src/pages/Chat.tsx
git commit -m "feat: 聊天页添加取消任务按钮"
```

---

### 手动验证步骤

后端重启（已在 Task 1 Step 4 中重启过，如需重新启动）：

```bash
cd d:\03.工作资料\05.华苏科技\06.实验代码\demo_agent_mcp_v1-develop
python -m src.api.app
```

在浏览器中验证：

1. 打开 Mangrove 聊天页 → 输入任务 → 点击发送
2. 确认：发送按钮被红色「取消任务」按钮替代
3. 点击「取消任务」→ 确认 SSE 流停止、回到可输入状态
4. 确认：会话历史中出现「❌ 用户已取消任务」消息
5. 测试边界场景：
   - 任务快速完成后点取消 → 无影响
   - 切换会话 → 原会话的取消状态正确
   - 新建会话 → 恢复正常
