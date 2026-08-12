# Phase 4A 文档黄金集

本目录由 `scripts/generate_document_golden.py` 确定性生成，全部为 CC0 合成数据，不包含真实个人、企业、账号或交易信息。

- 12 份合同、7 份招投标、5 份发票，共 24 份 PDF / 120 页。
- 覆盖数字页、扫描页、混合页、轻微旋转、噪声、测试印章和跨页表格。
- `expected.json` 固定字段真值、页码和证据原文。
- 修改生成逻辑后必须重新生成全部文件，并运行 `scripts/evaluate_document_golden.py`。
