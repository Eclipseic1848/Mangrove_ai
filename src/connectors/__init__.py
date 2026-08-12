"""连接器层（plan.md 第 7 节）。

设计原则：Connector 只负责获取，不承担业务清洗。
- base.py：SourceConnector 统一契约（probe/discover/read/checkpoint/capabilities/close）
- web_adapter.py：适配现有 src/collectors，输出 RawArtifact/RecordBatch
- file_connector.py：Phase 2 Task 3 已实现（上传文件 -> RawArtifact）
- http_security.py / pagination.py：Phase 2 Task 8 已实现（SSRF 防护 + 四种分页状态机）
- http_api_connector.py：Phase 2 Task 9 已实现（httpx + SSRF + 分页，只读 HTTP API）
- database_connector.py / media_connector.py：Phase 3-4 实现
"""
from __future__ import annotations
