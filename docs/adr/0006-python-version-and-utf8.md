# ADR-0006：目标 Python 3.13，统一 UTF-8 基线

- 状态：已采纳
- 日期：2026-07-19
- 决策来源：plan.md 第 4.3/14(Phase 0) 节；产品决策 13B

## 背景

plan.md 写的基线是 Python 3.13。实际环境探测发现：
- 项目使用 Python 3.13；Windows 推荐通过 `py -3.13` 解析解释器，也可用 `MANGROVE_PYTHON` 指定绝对路径。
- PATH 默认 `python`（`C:\Python314`，3.14.6）是裸环境，未装项目依赖，**非项目运行时**。
- Windows 控制台默认 cp1252/cp936，测试打印中文（如 `scripts/test_clean.py` 汇总行）抛 `UnicodeEncodeError`，功能通过却被判失败（plan 4.3 已记录）。

## 决策

- **目标 Python 版本为 3.13**（13B）。所有开发、测试、部署以实际解析到的 Python 3.13 解释器为准。
- 3.14 仅作为 PATH 默认值存在，不用于本项目；不主动降级系统 PATH，避免影响用户其他用途。
- **UTF-8 基线**（plan 4.3）：
  - 根 `conftest.py` 在 pytest 启动时把 stdout/stderr 重配为 UTF-8，并设 `PYTHONUTF8=1` 传给子进程。
  - 脚本式测试（`scripts/test_*.py`）在顶部 `sys.stdout.reconfigure(encoding="utf-8")`，使 `python scripts/test_xxx.py` 无需额外参数即可独立运行（`test_clean.py` 已作为参考实现）。
  - 文件读写一律显式 `encoding="utf-8"`，不依赖系统默认编码。
- 契约模型（Pydantic v2）在 3.13 与 3.14 上均验证可导入，不锁定具体小版本。

## 后果

- 正面：测试不再因编码误报；3.13 依赖生态与 plan 基线对齐。
- 负面：需注意 3.14 上跑脚本时依赖缺失；部署文档需明确指定 3.13 解释器路径。
- 待办：pyarrow 在 3.13 上漏装（requirements.txt 已声明 22.0.0），Phase 1 Parquet 输出前需补装；导出器已做优雅降级。

## 相关

- [[adr-0005-output-formats]]
