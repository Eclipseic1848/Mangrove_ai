# Phase 4B 语义 Harness 样本边界

本目录同时约束本地真实业务评测和可提交的脱敏 golden fixture。

```text
semantic_harness/
  private/   # 真实业务文件，只在本机，已被 .gitignore 排除
  public/    # 可提交的小型脱敏样例及 expected/manifest
```

## 固定流程

1. 真实文件只放入 `private/`，不得复制到仓库其他目录。
2. 文本类 CSV/TSV/JSON/JSONL/TXT/Markdown 可使用
   `scripts/prepare_semantic_fixture.py` 做明确的逐字替换。
3. 脚本会拒绝从 `private/` 之外读取，也会拒绝写到 `public/` 之外。
4. 脚本确认原始敏感字面量已消失后，生成脱敏文件及 `.fixture.json` 清单。
5. **脚本不能保证发现所有个人信息。** 提交前必须人工检查姓名、手机号、身份证、账号、
   地址、内部 URL、文件名、公式、批注和隐藏列。
6. PDF、DOCX、XLSX、图片等复杂或二进制格式在工具 A/B 完成前不得用此脚本处理；
   应先制作人工合成或可公开的小型 fixture。
7. `public/` 中每个样例必须同时包含期望计划、期望结果、证据和来源说明。

示例：

```powershell
py -3.13 -X utf8 scripts\prepare_semantic_fixture.py `
  --source tests\fixtures\semantic_harness\private\workload.csv `
  --output tests\fixtures\semantic_harness\public\workload-filter.csv `
  --replace "真实姓名=示例人员甲" `
  --replace "真实项目名=示例项目A" `
  --confirm-deidentified
```

`--confirm-deidentified` 只表示操作者已经人工复核，不是自动隐私保证。
