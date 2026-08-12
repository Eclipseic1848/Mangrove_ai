# Mangrove Web UI（前端）

> 统一产品入口是 `http://localhost:8088`；`http://localhost:5173` 只用于 Vite 开发。
> 后端 API 同源挂载在 8088。当前项目状态请见 `../docs/status/current.md`。

Mangrove（红树林）数据治理智能体的新版前端：独立 React + Vite + TypeScript + Tailwind 工程，
深/浅双主题，工作台仪表盘 + 对话工作区布局。后端通过 `src/api`（FastAPI 网关）暴露 REST + SSE，
核心 Agent 逻辑（LangGraph 流水线、采集器、记忆、调度等）零改动。

## 技术栈
- Vite + React 18 + TypeScript
- Tailwind CSS（CSS 变量双主题，品牌色：红树林青 teal）
- 轻量自研 UI 基础件（shadcn 令牌约定）+ lucide 图标 + sonner 提示
- react-router-dom 路由，react-markdown 渲染报告

## 目录
```
src/
  lib/        api 客户端(含 SSE) / auth(JWT) / theme(双主题) / utils
  components/ Layout(工作台外壳) / Sidebar / PipelineTracker / Markdown / ui(基础件)
  pages/      Login / Dashboard(概览) / Chat(对话工作区) / Tasks(任务中心)
```

## 开发
```bash
# 1) 先起后端网关（项目根目录）
python -m uvicorn src.api.main:app --host 0.0.0.0 --port 8088

# 2) 起前端开发服务器（本目录）
npm install
npm run dev        # http://localhost:5173，/api 代理到 8088
```

## 生产构建
```bash
npm run build      # 产物在 frontend/dist
# dist 存在时由 FastAPI 同源托管（访问 http://<host>:8088）
```

## 功能（v1）
- 多用户登录/注册（JWT），会话与定时任务按用户隔离
- 对话工作区：SSE 流式、流水线 8 节点实时可视化、产出文件下载、
  HITL 确认（入库/邮件/Slack/沉淀模板）、定时任务确认、质量分级与异常检测展示
- 概览仪表盘：采集器健康、模型供应商、连接器状态、模板与会话统计；统计卡可点击跳转对应面板（采集器卡滚动到本页"采集引擎"区块）
- 任务中心：定时任务列表（cron/once）、下次执行、删除；上次执行的人话摘要（成败/补跑标注，不暴露服务器路径）；「报告」按钮打开**执行历史**——每次执行一行，可在线查看（Markdown 渲染）、下载报告（.md）与采集数据（.json）

## v2（已交付）
  模板库（状态/使用统计/预览/删除）、记忆（偏好查看与追加）、设置（主题 + 模型目录 + 连接器健康主动自检）。

## RBAC 权限（已交付）
三级角色 `super_admin`（超级管理员）> `admin`（管理员）> `user`（普通用户）：
- 首个注册用户自动成为超级管理员；旧库迁移时最早注册的用户被提升为超级管理员。
- **分级管理**：只能操作严格低于自己级别的账号——管理员之间平级，互相不可改/删（杜绝互删互改密码）；超管可管理所有管理员与用户。`super_admin` 不可经界面授予（防提权），仅由系统引导设定。
- **注册待审批**：除首个用户外，自助注册者初始为「待审批」，审批通过前无法登录；管理员在「用户管理」一键通过/拒绝；管理员后台直接建号免审批。
- 管理员/超管专属「用户管理」面板：用户增删、改角色、禁用/启用、重置密码、审批、开关自助注册（按级别显示可操作项）；支持关键词搜索（用户名/昵称）+ 角色/状态筛选 + 分页（2026-07-07，应对上千+用户量级）。
- 全局共享资源（模板库删除、记忆追加）仅管理员可写，普通用户只读；会话/定时任务仍按用户隔离。
- 后端门控见 `src/api/auth.py` 的 `require_admin` 与 `src/api/routes/admin_routes.py`。
