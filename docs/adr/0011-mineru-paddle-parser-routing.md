# ADR-0011：MinerU 主解析、Paddle 表格增强与失败回退

- 状态：已采纳
- 日期：2026-07-23
- 适用版本：v0.0.4

## 背景

PaddleOCR-VL 完整 `/layout-parsing` Pipeline 已在
`http://192.168.1.21:18081` 就绪。Phase 4A 因此使用同一套固定黄金集，
比较 MinerU 3.4.4 pipeline、MinerU hybrid medium/high 和 PaddleOCR-VL
1.6，而不是仅以接口连通作为选型依据。

原始可复现结果见
`docs/plans/phase4a-parser-ab-results.json`，执行器为
`scripts/compare_document_parsers.py`。

## 同集结果

完整门禁包含 17 份扫描/混合 PDF、85 页：

| 指标 | MinerU pipeline | Paddle `/layout-parsing` |
|---|---:|---:|
| 成功文档 | 17/17 | 17/17 |
| 页覆盖 | 85/85 | 85/85 |
| 目标字段精确值 | 51/51 | 47/51 |
| 期望行召回 | 668/680 | 648/680 |
| 跨页表格行召回 | 260/272 | 272/272 |
| bbox 覆盖 | 449/449 | 425/425 |
| 平均归一化 CER | 0.05493 | 0.15493 |
| 总耗时 | 125.416 秒 | 359.675 秒 |
| 加权得分 | 0.987554 | 0.960267 |

Paddle 耗时约为 MinerU 的 2.87 倍，但表格行召回达到 100%。MinerU 在字段、
正文和字符错误率上更优。

MinerU hybrid medium/high 在全部试点请求中均返回 HTTP 409：

```text
Device string must not be empty
```

这是 MinerU 服务端设备配置问题；v0.0.4 不声明 hybrid 可用。

## 后续状态更新（2026-07-26）

该历史结论对本地 `hybrid-engine` 仍成立，但 MinerU 3.4.4 现可通过
`hybrid-http-client` 调用 `http://192.168.1.21:18080/v1` 的 Paddle VLM。
同一 3 份/15 页试点结果为：

| 指标 | Hyper medium | Hyper high | Paddle `/layout-parsing` |
|---|---:|---:|---:|
| 接口成功 | 3/3 | 3/3，但内容为空 | 3/3 |
| 页覆盖 | 15/15 | 0/15 | 15/15 |
| 字段召回 | 100% | 0% | 88.9% |
| 行召回 | 60% | 0% | 95.8% |
| 表格行召回 | 0% | 0% | 100% |
| 综合分 | 0.740017 | 0 | 0.950895 |

Hyper high 的任务 `29ac7b7d-d659-4614-9c4a-a2039496db5a` 在服务端结果中
返回 5 个空页数组，因此不是 Mangrove 客户端漏解析。该状态更新不反向改变 v0.0.4
的发布内容，也不改变下述生产路由。

## 决策

1. 数字 PDF 和 DOCX 继续优先使用确定性解析器。
2. 扫描/混合 PDF 以 MinerU `pipeline` 为首选。
3. MinerU 请求失败或缺页时，回退 Paddle 18081。
4. MinerU 已识别出表格的页面，额外调用 Paddle 做表格块增强；普通页面不双跑。
5. Paddle 返回页尺寸时把 bbox 归一到 `normalized_1000`；图片没有页尺寸时保留
   `image_pixels`，由 OpenSeadragon 使用真实图片尺寸叠框。
6. 保留各提供方原始响应与端点/后端/版本缓存身份，禁止把后备结果冒充首选结果。

## 待办

- 修复本地 `hybrid-engine` 的 device 配置和 `hybrid-http-client + high` 空结果；
  Mangrove 客户端增加“完成但无内容/缺页”的显式失败分类。
- 为 Hyper 增加独立、受控的 `effort/server_url/image_analysis` 配置和缓存身份后，
  先重跑 3 份试点，再扩到 17 份/85 页全量；达标前不得改变主链。
- 扩充真实低清、旋转、多语言和复杂无框表格样例；本 ADR 不把合成黄金集扩大表述为
  所有真实文档质量。
