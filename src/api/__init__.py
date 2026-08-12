"""
api —— Web UI 网关（FastAPI）。

把现有后端纯函数（conductor / scheduler / memory / llm / 连接器）包装成 REST + SSE 接口，
供独立 React 前端调用。核心 Agent 逻辑零改动；本包只做"传输 + 鉴权 + 会话持久化"。

- store.py          用户与会话历史的 SQLite 持久化
- auth.py           密码哈希(pbkdf2) + JWT + 当前用户依赖
- session_store.py  HITL 待确认动作的服务端暂存（按 user+task 隔离）
- schemas.py        请求/响应的 pydantic 模型
- routes/           各业务路由（auth/chat/confirm/tasks/models/downloads/...）
- main.py           FastAPI app 装配 + CORS + 前端静态托管
"""
