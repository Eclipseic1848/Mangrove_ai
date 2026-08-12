# AC-07-03 Trivy、Syft 供应链证据闭环执行报告

> 日期：2026-08-07
>
> 对应工单：GitHub Issue #35
>
> 状态：`engineering_verified_pending_code_review_production_migration_and_user_acceptance`
>
> 边界：本报告只证明 #35 工程纵切面和隔离真实扫描；未迁移生产数据库，未把任何能力
> 晋级为 `verified`，未生成平台签名，也未进入 #36～#44。

## 1. 本轮交付

- 固定 Trivy `0.70.0` 与 Syft `1.50.0`，锁定官方 Release URL、归档 SHA-256 和本机
  可执行文件 SHA-256；禁止 `latest`、移动标签和动态安装脚本；
- Trivy 对精确 digest 对应的最终能力挂载目录直接执行 vulnerability、misconfiguration、
  secret 三类扫描；实际治理扫描强制使用既有缓存离线运行，不能用 Syft SBOM 代替最终对象；
- Syft 对同一主体生成完整 `syft-json` 与 CycloneDX JSON 1.6，原始受控证据留在忽略的
  证据目录，治理库只保存哈希、版本、漏洞库元数据和计数摘要；
- 新增不可变 `CapabilitySupplyChainEvidence` 与 SQLite 纯新增迁移，按 Owner、scope、Pack、
  version 和 digest 查询最新证据；扫描器错误、主体 digest 变化、工具哈希变化、CycloneDX
  版本错误或漏洞库元数据缺失均失败关闭；
- Secret、Critical、存在修复版本的 High 和超过 7 天的 Trivy DB 会把证据标记为 blocked；
  该判断只供后续新晋级、发布和解除隔离使用，不改变既有合格灰度任务；
- Owner 可读取自己的脱敏证据，管理员/超管可读取跨 Owner 证据；设置页只展示工具版本、
  DB 版本和风险计数，不展示原始路径、Token、业务内容或工具 stderr。

## 2. 上游来源验证

- Trivy Windows ZIP SHA-256：
  `eea5442eab86f9e26cd718d7618d43899e72a83767619e8bee47911bddbfb825`；
- Trivy Release Sigstore bundle 使用固定身份正则
  `^https://github.com/aquasecurity/trivy/` 与 GitHub Actions OIDC issuer 验证通过；
- Syft Windows ZIP SHA-256：
  `815ee6973ec5dff6a671d7f41b0e78835a8c45b91d5a39f4743ea1cee833d3be`；
- Syft `checksums.txt` 的 `.pem + .sig` 使用工作流身份
  `https://github.com/anchore/syft/.github/workflows/release.yaml@refs/heads/main`
  验证通过；
- 验证辅助工具使用规格已冻结的 Cosign `3.0.6`，本轮只用于验证上游发布物，没有生成项目
  密钥或实现平台能力签名；Cosign TUF 自举和本地 OCI 签名路径仍属于 #36。

## 3. 两项真实能力包证据

| 能力包 | 精确 digest | 结果 | Secret | Critical | 可修复 High | CycloneDX |
| --- | --- | --- | ---: | ---: | ---: | --- |
| `gray-python-table@1.0.0` | `sha256:2a430a…3acd902` | passed | 0 | 0 | 0 | 1.6 |
| `gray-everything-mcp@2026.7.4` | `sha256:dce5be…d4cd8a8` | passed | 0 | 0 | 0 | 1.6 |

两项证据均记录 Trivy DB schema version `2`、内容 `UpdatedAt`、`NextUpdate`、
`DownloadedAt`、扫描配置 hash、扫描结果 hash、Syft JSON hash 和 CycloneDX hash。临时 SQLite
迁移后写入并由新 Repository 实例重新打开，恢复结果与原证据完全一致。

## 4. 验证证据

- 供应链模型、CLI Adapter、失败关闭、SQLite 重开：`4 passed`；
- 能力治理、认证 API 与供应链聚焦回归：`27 passed`；
- 前端 TypeScript 与 Vite 生产构建：通过；
- 设置页角色与治理完整文件：`11 passed`，供应链摘要专项：`1 passed`；
- 两份真实冻结能力包 Trivy/Syft 扫描及双格式 SBOM：均通过；
- 全仓按 130 个既有测试文件分四组执行：`1244 passed, 4 skipped, 1 failed`。唯一失败为既有
  `tests/test_db_security.py::TestValidateDbHost::test_host_allowlist_match`，原因是当前网络无法
  解析测试占位域名 `allowed.example.com`；独立重跑仍相同。该失败不涉及本轮文件，未通过
  放宽 DNS 安全校验或修改无关测试掩盖；
- `compileall` 与 `git diff --check` 通过，后者仅有既有 Windows LF/CRLF 提示。

## 5. 已验证事实、推断和待确认项

### 已验证事实

- 两份能力包的精确 digest 标记、工具可执行文件 hash、Trivy DB 时效和两种 SBOM 均可在
  当前 Windows 环境复验；
- 无供应链证据请求时不会启动 Trivy/Syft；新增只读投影不存在时返回空，不阻断既有治理页；
- 真实扫描失败或证据不可判定时不会形成 passed 证据。

### 基于代码的推断

- #37 使用本服务作为晋级硬门后，DB 过期或扫描器不可用会阻止新晋级，同时既有灰度运行不会
  被本票静默改变；#37 尚未实现，因此不能把该行为表述为已上线的晋级策略。

### 尚未验证或需要人工控制

1. 尚未进入正式 code-review 阶段；
2. `0003_supply_chain_evidence.sql` 尚未迁移生产 `data/webui.db`，生产设置页因此不会出现真实摘要；
3. 未在 8088 执行用户验收；
4. 未提交、推送或关闭 Issue #35；
5. Cosign 本地密钥、OCI 签名与验证属于 #36，能力晋级属于 #37，均未开始。

## 6. 下一人工控制点

先进入 #35 `code-review`。审查通过后，由用户明确确认是否执行带备份的生产纯新增迁移；迁移
和 8088 用户验收通过后，才能提交、推送并关闭 #35。之后 #37 仍同时受 #34 真实能力验证闭环
和 #35 供应链证据闭环约束。
