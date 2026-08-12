# 可选外部依赖

本目录只提交 Mangrove 的集成说明和补丁，不提交第三方项目的完整工作副本。这样可以避免把
浏览器登录态、上游构建缓存和数百 MiB 依赖误发布，也不会用根目录 MIT 许可证覆盖第三方许可。

## 一键准备

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts/setup_external_dependencies.ps1
```

脚本会失败关闭地执行以下操作：

1. 将 MediaCrawler 固定到 `c9a111be73586bdf6fc44536f088e4db6ed86d64`，再应用
   `external/patches/mediacrawler-mangrove.patch`；
2. 将 Firecrawl 固定到 `8d679cbcb68ad8456f26166d69fb17d03c7068fe`，再应用
   `external/patches/firecrawl-mangrove.patch`；
3. 若目标目录已存在但不是脚本预期状态，脚本停止并要求人工处理，不覆盖本机改动。

MediaCrawler 依赖与登录配置仍按 `external/MediaCrawler/README.md` 处理；Firecrawl 的 Docker
说明见 `docker/firecrawl/README.md`。补丁源代码随对应第三方组件遵循其许可证，详见
`THIRD_PARTY_NOTICES.md`。
