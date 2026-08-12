# Batch 8A Promptfoo PoC

这是一个隔离的六用例契约 PoC，用于验证 Promptfoo 能否以本地 Python Provider
接入 Mangrove 的评测流程。它不调用外部模型、不上传数据，也不替代 pytest、
Playwright 和真实文件重开验证。

运行：

```powershell
cd evals/promptfoo-batch8a
npm.cmd ci
$env:PROMPTFOO_DISABLE_SHARING = "true"
npm.cmd run eval
```

门禁要求：六个用例全部通过；命令必须带 `--no-cache --no-share`。生成的
`results.json` 仅为本地运行证据，不提交到仓库。
