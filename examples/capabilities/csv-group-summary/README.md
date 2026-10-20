# CSV 分组求和工具

对部门费用、分类数量等 CSV 明细按指定列求和。只使用 Python 标准库，不访问网络、不读取宿主文件、不写入业务来源。适用于管理员任务能力的安装与复用验证。

输入为一个 JSON 字符串参数：

```json
{"csv":"部门,金额\n研发,0.1\n市场,12.50\n研发,0.2\n","group_field":"部门","value_field":"金额"}
```

输出：

```json
{"groups":{"市场":"12.50","研发":"0.3"},"row_count":3,"tool_version":"1.0.0"}
```

数值使用十进制字符串，避免浮点求和误差。允许负数；不接受千位分隔符、指数、非有限数值、缺失列、重复列或行列数不一致。CSV 上限 60,000 字节、10,000 行；数值最多 24 位整数、12 位小数。空表保留零行事实。

从仓库根目录检查：

```bash
python examples/capabilities/csv-group-summary/table_summary.py --health
python -m pytest tests/test_csv_group_summary_capability.py -q
```

`mangrove-capability.json` 定义实际运行入口与参数用法；`reuse-contract.json` 定义 `group_sum`、CSV 输入和 JSON 输出的兼容契约。登记新包时，把后者作为 JSON 字符串写入目录 manifest 的 `reuse_contract`，同时声明 `license=MIT` 和本仓库公共来源。

安装须使用新包身份 `csv-group-summary@1.0.0`，按实际 OCI 制品摘要冻结；不得改写历史工具版本。登记从个人草稿开始，经过既有任务验证、供应链检查和晋级后才能供业务选择。发布到平台还需既有签名和管理员受众门；本目录存在不代表已安装或已发布。

任务上传 CSV、选择 JSON 输出后，可在任务的本地能力区明确确认复用。相同操作、输入和输出的后续任务应命中同一个精确版本；撤销、隔离或不兼容时仍由原治理门拒绝。

许可证沿用仓库 MIT，见 `LICENSE`。
