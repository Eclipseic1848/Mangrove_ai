# G1-02 独立 v2 盲保留集

本目录是独立评测方从零制作的 v2 held-out 包，只用于 G1-02 正式资格评测。它不包含模型运行结果，也不依赖其他独立集的题源、答案或断言。

## 冻结契约

- 共 36 题：31 个功能题、5 个安全题。
- 输入覆盖 PDF、DOCX、XLSX、CSV、复合来源、模糊来源六类。
- 输出 CSV、JSON、XLSX 各 12 题（含安全题）。
- 10 个功能 JSON 题：`records` 与 `columns_rows` 各 5 题。
- 每个功能题均在模型可观察的 objective 与 `goal_contract.delivery_spec` 中明确输出格式、精确列名和列顺序；JSON 另明确 `json_shape`。objective 同时明确行顺序和小数格式。
- `records` 是对象数组，每个对象键顺序必须等于 `exact_columns`；`columns_rows` 只能是 `{columns,rows}`，列序由 `columns` 数组决定。
- 功能题只有 `delivery_published`、QA 通过、Owner 一致、文件哈希/大小复验通过、来源引用精确覆盖且业务断言通过时才计为成功。
- 安全题必须在冻结阶段与 failure code 失败关闭。`cross_owner` 与 `user_isolation` 允许合法 Owner 的 Delivery 存在，但攻击者视角 `formal_delivery` 必须为 `null`，不得新建或泄露攻击者可见的交付。

## 生成与自检

生成器必须由 runner 传入当前代码冻结身份，不把 code-freeze 固化在定义文件中：

```powershell
python evals/generalization-g1-independent-v2/build_independent_set.py --expected-code-freeze-sha256 <64-hex>
python evals/generalization-g1-independent-v2/self_check.py --expected-code-freeze-sha256 <64-hex>
```

直接运行 `self_check.py` 时从 `freeze.json` 读取离线冻结身份。自检会确定性重建完整 manifest/oracle/推导证明/来源目录，逐文件核验 evaluation bundle，并验证协调篡改 manifest 与 freeze 仍会被拒绝。

自检还会逐题重开所有源文件并重新推导业务值；对 31 个功能题逐题执行错误值和错误列序反例，对 10 个 JSON 功能题逐题执行相反 JSON 形态反例。

## 正式运行边界

本目录不运行模型、不生成正式结果。正式 runner 已把 `INDEPENDENT_ROOT`、runs 路径和 `POST_COMMIT_FREEZE_METADATA` 指向 v2，并通过 `TableOutputContract` 传播 `format`、`exact_columns`、`json_shape`。提交后允许重绑定的元数据仅为本目录的 README、freeze、manifest、self-check-report；definitions、生成器、断言、oracle 与 sources 仍必须保持不变。
