# 聊天取消按钮 设计文档

**功能**: 在对话过程中添加「取消任务」按钮，允许用户随时中断正在执行的 AI 流水线。

**设计原则**: 利用已有基础设施（`AbortController` + `asyncio.Task` 注册表），最小改动。

---

## 背景

当前 Mangrove 聊天页面在 AI 生成回复时，发送按钮被禁用（`running=true`），用户只能等待模型输出完。虽然前端已有 `AbortController` 机制（`cancelRef`）和 SSE 流式解析，但：

- 前端**没有可见的取消按钮**——用户无法主动触发生成取消
- 前端 `controller.abort()` **只断开 SSE 连接**，后端 `pipeline()` 后台 `asyncio.Task` 继续跑完全程，结果照常落库

用户需求：对话时如果发现发错了/不需要了/想重来，能立即停止。

---

## 架构

### 现状

```
┌─ Frontend ───────────────────────┐     ┌─ Backend ──────────────────────────────────┐
│                                  │     │                                              │
│  cancelRef → controller.abort()  │─────│→ event_gen()  [SSE 流] ←queue← pipeline()  │
│                                  │     │                              ↑              │
│  (按钮不存在)                    │     │                    _RUNNING[key] = Task      │
└──────────────────────────────────┘     └──────────────────────────────────────────────┘
```

- pipeline（流水线）运行在独立 `asyncio.Task` 中，通过 `asyncio.Queue` 向 SSE 生成器传递事件
- `_RUNNING` 字典以 `"user_id:conv_id"` 为键持有所有运行中的 Task 引用
- `chat.py:240` 注释明确：客户端断开只是本生成器被取消，pipeline 不受影响

### 改动后

```
┌─ Frontend ───────────────────────┐     ┌─ Backend ───────────────────────────────────┐
│                                  │     │                                               │
│  ┌──────────────┐               │     │                                               │
│  │ 取消任务 按钮 │               │     │  POST /api/chat/{conv_id}/cancel              │
│  └──────┬───────┘               │─────│→ 查找 _RUNNING → task.cancel()                │
│         ├─ cancelRef()          │     │      ↓                                        │
│         │   (断SSE)             │     │  CancelledError 在 pipeline 的 await 点抛出    │
│         └─ POST /cancel         │     │      ↓                                        │
│                                  │     │  except asyncio.CancelledError:              │
│                                  │     │    落库 "❌ 用户已取消任务"                    │
│                                  │     │    queue ← result(cancelled)                 │
│                                  │     │  finally: done + 清理 _RUNNING               │
└──────────────────────────────────┘     └──────────────────────────────────────────────┘
```

---

## 详细设计

### 1. 后端：新增 cancel endpoint

**文件**: `src/api/routes/chat.py`

在文件末尾 `_RUNNING` 字典附近新增路由：

```
POST /api/chat/{conv_id}/cancel
Authorization: Bearer <JWT>

Response 200: {"ok": true, "message": "已取消"}
Response 404: {"detail": "没有正在执行的任务"}
```

行为：
1. 从 `_RUNNING` 中查找 `"user_id:conv_id"` → `asyncio.Task`
2. 找不到或已 done → 404
3. 调用 `task.cancel()` → 在 pipeline 的下一个 `await` 点注入 `CancelledError`
4. 返回成功

### 2. 后端：pipeline 捕获 CancelledError

**文件**: `src/api/routes/chat.py`，`pipeline()` 函数

当前的 `except` 链：

```python
try:
    ...
except Exception as e:
    store.add_message(..., f"❌ 任务执行失败：{e}")
    queue.put_nowait(...)
finally:
    queue.put_nowait({"event": "done", "data": "{}"})
    _RUNNING.pop(task_key, None)
```

改为：

```python
try:
    ...
except asyncio.CancelledError:
    store.add_message(conv_id, "assistant", "❌ 用户已取消任务")
    queue.put_nowait({"event": "result", "data": json.dumps({
        "conv_id": conv_id, "kind": "cancelled",
        "reply": "❌ 用户已取消任务",
    }, ensure_ascii=False)})
except Exception as e:
    ...
finally:
    queue.put_nowait({"event": "done", "data": "{}"})
    _RUNNING.pop(task_key, None)
```

注意：`asyncio.CancelledError` 在 Python 3.9+ 继承自 `BaseException`，**不是** `Exception`，因此必须在 `except Exception` **之前**捕获，否则会逃逸到 `finally` 而不会触发自定义处理。

效果：
- 用户消息已落库（`chat.py:174` 在 pipeline 启动前已执行），保留在会话中
- 取消后追加一条 "❌ 用户已取消任务" 作为 assistant 回复
- 结果事件放入队列（SSE 可能已断，无害）
- `_RUNNING` 正常清理

### 3. 前端：取消按钮 + 取消逻辑

**文件**: `frontend/src/pages/Chat.tsx`

添加状态和函数：

```typescript
// 取消任务
const cancel = async () => {
  // 1. 断 SSE 流
  cancelRef.current?.();
  // 2. 通知后端取消 pipeline
  if (convId) {
    try { await api.post(`/api/chat/${convId}/cancel`); } catch { /* 后端可能已无任务 */ }
  }
  // 3. 更新本地状态
  setRunning(false);
  setActiveNode(null);
};
```

输入区改动——发送按钮区域 `running` 时显示取消按钮：

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

样式要点：
- 取消按钮用 `destructive` variant（红色），视觉上明确是终止操作
- 取消按钮替代发送按钮位置，不额外增加布局宽度
- 发送按钮在 `running` 时不再仅 disabled，而是被替换

### 4. 取消消息渲染

`onResult` 回调中 `kind === "cancelled"` 的消息会用现有 `MessageBubble` 组件渲染（assistant 角色），内容就是 "❌ 用户已取消任务"，用户看到后可以知道这次执行被手动终止了，可以继续输入下一个任务。

---

## 边界情况

| 场景 | 行为 |
|------|------|
| 快速连点两次取消 | 第二次 `cancelRef.current()` 已为 null → 安全；后端 task 已 cancelled → 404 → catch 忽略 |
| 任务即将完成时取消 | `task.cancel()` 在 await 点抛出，但结果可能已入队列 → 无害，队列中可能有 result 和 cancelled 两个事件，但 SSE 已断 |
| 取消后新建会话 | `newChat()` 已调 `cancelRef.current?.()` → 安全 |
| 切换会话时自动取消 | `loadConv()` 已调 `cancelRef.current?.()` → 安全 |
| 取消时 convId 尚未设置（极快取消） | `convId` 为 null → 只调 cancelRef.current() 断 SSE → pipeline 继续跑完落库 → 无害 |

---

## 涉及文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/api/routes/chat.py` | 修改 +20行 | 新增 cancel endpoint（~8行）+ pipeline 捕获 CancelledError（~8行）+ asyncio 导入（~4行） |
| `frontend/src/pages/Chat.tsx` | 修改 +15行 | 新增 cancel 函数 + 取消按钮替换发送按钮 |

---

## 不做

- **不在 Conductor 节点注入取消检查**：`task.cancel()` 的 `CancelledError` 在任意 `await` 点自动生效，无需逐节点加 `if cancelled: break`
- **不做超时自动取消**：那是独立功能（已有采集器超时降级）
- **不改定时任务取消**：已有 `DELETE /api/tasks/{sched_id}`
- **不改 `astream_conductor()`**：`CancelledError` 在 pipeline 层捕获就足够
- **不改后台任务轮询（bgRunning）**：已取消的任务不会触发 bgRunning 状态
