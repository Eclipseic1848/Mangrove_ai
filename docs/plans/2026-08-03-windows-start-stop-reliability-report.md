# Windows 一键启停可靠性修复报告

> 日期：2026-08-03
> 状态：工程验证通过
> 范围：`start_all.bat`、`stop_all.bat` 及其项目内辅助脚本

## 问题与根因

已验证事实：

- 冷启动时 `scripts/dev_reload.py` 在 Windows 控制台的 cp1252 编码下输出中文，触发
  `UnicodeEncodeError`，后端因此没有监听 8088。
- 前端原命令没有传入 `--host`，Vite 只监听本机回环地址，局域网地址无法访问。
- 原启动脚本只表示“命令已执行”，没有验证后端或前端是否真的可用。
- 原停止脚本只按监听端口结束进程，可能遗留父命令窗口和监控进程。

## 最小修复

- 启动根目录改为 `%~dp0`，移除与磁盘位置绑定的硬编码路径。
- 后端设置 `PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`，并以 `python -X utf8 -u` 启动。
- 前端使用 `npm.cmd run dev -- --host 0.0.0.0`，允许局域网访问。
- 新增 `scripts/check_dev_services.ps1`：只有后端健康 JSON、前端 HTTP 200 和 LAN 监听
  同时满足时才返回成功。
- 新增 `scripts/stop_dev_processes.ps1`：限定在当前项目根目录内识别并清理后端、前端
  进程树；由停止入口调用时一并关闭旧启动器窗口，避免误伤其他项目或正在执行的启动前清理。
- 两个批处理脚本支持 `--no-pause`，用于无人值守回归；交互双击行为仍保留暂停提示。

## 验证证据

连续执行两轮真实停止与启动，结果一致：

- 停止后 5173/8088 无监听，且没有 `dev_reload.py`、`src.api.main` 或本项目 Vite 进程。
- 后端监听 `0.0.0.0:8088`，`GET http://127.0.0.1:8088/api/health` 返回 HTTP 200，
  响应为 `{"ok":true,"service":"mangrove-webui"}`。
- 前端监听 `0.0.0.0:5173`；`http://127.0.0.1:5173` 与
  `http://192.168.1.100:5173` 均返回 HTTP 200。
- `tests/test_agentic_runtime.py::test_unified_scripts_manage_pi_egress_runtime`：1 passed。
- `tests/test_agentic_runtime.py`：31 passed。

## 使用方式

- 日常启动：双击根目录 `start_all.bat`，看到“全部核心服务已启动并通过健康检查”后访问
  `http://192.168.1.100:5173`。
- 日常停止：双击根目录 `stop_all.bat`；脚本会关闭本项目后端、前端以及 Pi 临时资源。
- 若启动失败，脚本会明确返回失败并保留后端/前端窗口，窗口中的首个错误是诊断入口。

## 2026-08-04 收口复核

双轴审查发现首版辅助脚本会先把 5173/8088 的所有监听 PID 加入终止集合，再识别项目
进程；如果其他项目恰好占用端口，会被误杀。现已改为先沿进程及其受控祖先验证项目根、
`--mangrove-service-root` 或 Mangrove Vite 标记，只有验证通过才加入终止集合。未知监听
只保留并返回端口占用警告。

验证证据：

- 新增真实非项目监听回归：修复前因脚本没有安全测试端口入口而 Red；修复后停止脚本返回
  警告，监听进程保持存活，1 passed；
- Runtime、候选验证、对话转向、文档 Relay、工作台 API、AC-04/05 和模型连接组合门：
  182 passed；
- 前端生产构建通过，完整 Playwright 51 passed；
- 再次真实执行 `stop_all.bat --no-pause` → `start_all.bat --no-pause`，随后后端健康、
  本机前端和 `192.168.1.100:5173` 均返回 HTTP 200。

## 2026-08-04 统一 8088 入口与冷启动纠偏

用户明确要求产品入口继续使用既有 `8088`，不能把 `5173` 开发服务器当成正式入口。启动
流程现先解析稳定 Node/npm 路径并等待 Docker Engine；随后构建 `frontend/dist`，由 FastAPI
在 `8088` 同源托管前端和 API。`5173` 只保留为开发服务。健康门改为同时验证
`/api/health`、`8088/` 的 HTML 和 `0.0.0.0:8088` LAN 监听；`frontend/dist` 不再被生成物
清理脚本删除。双击启动链加入祖先进程保护，避免启动前清理误杀当前 `start_all.bat`；每次
结果写入 `logs/start_all.log`，失败窗口保持可见。

2026-08-04 19:21 的真实启动日志记录 Python、npm、Docker、前端构建及 8088 三项健康门
全部通过。最终全仓后端 `1187 passed/4 skipped`、前端生产构建和完整 Playwright
`51 passed`；当前仍应在每次 Windows 重启后以 `start_all.bat` 的实时健康门作为运行事实，
不能只凭历史日志判断服务在线。

## 2026-08-06 运行中后端退出自恢复

后续真实运行又发现：冷启动健康门通过后，`dev_reload.py` 或 Uvicorn 子进程仍可能在运行中
退出，表现为启动窗口消失或 8088 不定期拒绝连接。现采用两层监督：

- `scripts/run_backend_supervisor.bat` 是外层常驻入口；`dev_reload.py` 整体退出时 2 秒后恢复；
- `scripts/dev_reload.py` 每秒检查后端子进程，异常退出时原地拉起；文件监听异常不终止后端；
- 监督证据追加写入 UTF-8 `logs/dev_reload.log`；
- `stop_dev_processes.ps1` 额外校验父子进程创建时间，避免 PID 复用误杀无关进程；
- 8088 仍是统一产品端口，5173 没有被重新设为用户入口。

2026-08-06 联合回归中，与 Runtime/工作台一起执行的后端相关用例为 `110 passed`，前端
生产构建与完整 Playwright `53 passed`。这证明监督器契约和现有产品流程没有自动化回归；
每次 Windows 重启后的真实可用性仍以 `start_all.bat` 三项健康门和 `/api/health` 为准。
