# AC-07 供应链工具官方事实刷新

> 日期：2026-08-06
>
> 范围：Trivy、Syft、Cosign、ORAS/OCI 进入 AC-07 的固定版本、能力边界和本机 PoC 条件
>
> 资料边界：只使用项目官方文档、官方仓库、官方 Release 和 OCI 正式规范
>
> 本文状态：研究结论，不代表已下载、安装、运行或通过本机验证

## 1. 结论

截至 2026-08-06，可作为 AC-07 PoC 起点的稳定版本是：

| 工具 | 固定版本 | 官方发布依据 | 下载物最低校验要求 |
|---|---:|---|---|
| Trivy | `v0.70.0` | [官方不可变 Release](https://github.com/aquasecurity/trivy/releases/tag/v0.70.0) | 校验 Release checksum，并使用随 Release 提供的 Sigstore bundle 验证下载物；不得使用 `latest` |
| Syft | `v1.50.0` | [官方不可变 Release](https://github.com/anchore/syft/releases/tag/v1.50.0) | 校验 `checksums.txt`，并验证其 `.pem` + `.sig`；不得只信下载 URL |
| Cosign | `v3.0.6` | [官方 Release](https://github.com/sigstore/cosign/releases/tag/v3.0.6) | 按官方 TUF artifact key 或已可信 Cosign + Sigstore bundle 的自举流程验证二进制 |
| ORAS CLI | `v1.3.2` | [官方 Release](https://github.com/oras-project/oras/releases/tag/v1.3.2) | 用官方 `KEYS` 中的 GPG key 验证 Release 附带的 `.asc`，再校验固定版本 |

这四个版本只是本轮可复现 PoC 的版本基线，不是“永久最新版”。实现不得在运行时查询
`latest` 或静默升级；版本、下载 URL、下载物 SHA-256、上游签名验证结果和容器 digest 都应进入
工具锁定清单。

一个关键修正是：**Cosign 官方资料没有证明 `cosign sign` 能把标准 image signature 直接写进
任意现有的本地 OCI Image Layout。**官方明确记录了 registry image 的签名，以及用
`cosign save` 保存后的本地目录进行 `--local-image` 验证；因此 AC-07 不能把“本地 OCI Layout
直接签名”写成已验证能力。首选 PoC 应使用仅绑定回环地址的临时本地 OCI Registry，把 Layout
通过 ORAS 复制进去，按 digest 签名、验证，再将主体和 referrers 复制回 Layout。若这个闭环在
当前环境不稳定，最小降级是用 `cosign sign-blob` 对规范化的主体 manifest blob 生成独立 bundle，
但这不是标准容器 image signature，必须在规格中明确不同语义。

## 2. 判断口径

- **已验证事实**：由官方文档、官方仓库、官方 Release 或 OCI 规范直接支持。
- **基于项目的推断**：把官方能力映射到 Mangrove、ADR-0029 和当前 Windows + Docker Desktop
  架构后得到的设计结论。
- **尚未验证**：本轮没有下载或执行工具，必须通过当前机器真实 PoC 才能确认。

## 3. 固定版本与发布物校验

### 3.1 已验证事实

#### Trivy `v0.70.0`

- 官方 v0.70 安装页提供 Windows Release ZIP、官方容器和固定版本安装方式；官方容器示例也使用
  `aquasec/trivy:0.70.0`：[Trivy v0.70 安装](https://www.trivy.dev/docs/v0.70/getting-started/installation/)。
- `v0.70.0` GitHub Release 被标记为不可变，附有 `trivy_0.70.0_checksums.txt`、该 checksum 的
  `.sigstore.json`，以及各平台下载物自己的 Sigstore bundle：
  [Trivy v0.70.0 Release](https://github.com/aquasecurity/trivy/releases/tag/v0.70.0)。
- 2026-03-19 的 Trivy 供应链事件曾产生恶意 `v0.69.4` 二进制和镜像，并劫持 Action tag。
  官方事后要求固定版本/完整 SHA，并展示了使用 Release Sigstore bundle 验证二进制的方法；
  后续 Release 已启用不可变发布、SLSA provenance 和既有 Sigstore 签名：
  [官方安全公告](https://github.com/aquasecurity/trivy/security/advisories/GHSA-69fq-xp46-6x23)、
  [官方事件收口说明](https://github.com/aquasecurity/trivy/discussions/10462)。

因此 AC-07 不应通过 `setup-trivy`、`trivy-action`、`latest` tag 或未经验证的安装脚本动态获得
Trivy。应下载精确版本资产，在隔离获取阶段验证 bundle 和 checksum 后，冻结二进制 SHA-256；
若改用容器，同样必须先验证上游签名并冻结实际 image digest。

#### Syft `v1.50.0`

- 官方发布页将 `v1.50.0` 标为最新不可变 Release，并提供 checksum、checksum 的证书和签名，
  以及 Windows/Linux/macOS 构建：
  [Syft v1.50.0 Release](https://github.com/anchore/syft/releases/tag/v1.50.0)。
- Anchore 官方安装文档明确其官方构建覆盖 Windows，也提供官方容器；安装脚本的 `-v` 会在已有
  Cosign 时验证下载物签名：[Syft 安装](https://oss.anchore.com/docs/installation/syft/)。

AC-07 应直接取固定版本 Release 资产，先验证签名后的 checksum 文件，再用 checksum 验证目标
Windows 资产；不能把 Scoop/WinGet 社区分发包当作平台供应链的权威来源。

#### Cosign `v3.0.6`

- `v3.0.6` 是当前官方 Release，且修复了 DSSE predicate 校验安全问题：
  [Cosign v3.0.6 Release](https://github.com/sigstore/cosign/releases/tag/v3.0.6)。
- Cosign 官方安装页给出了“先从 Sigstore TUF 获取 artifact key，再验证 v3 bundle/二进制”的
  自举方法；已拥有可信 Cosign 后，也可用发布 bundle 做 identity-based 验证：
  [Cosign 安装与 Release 验证](https://docs.sigstore.dev/cosign/system_config/installation/)。

因此不能让待验证的 Cosign 二进制验证自身后就直接建立信任。首装需要保存 TUF root/artifact
key 获取证据，或者由项目预先冻结一个已通过外部可信路径验证的 Cosign SHA-256。

#### ORAS CLI `v1.3.2`

- ORAS 官方安装页给出 `v1.3.2` 的 Windows AMD64 ZIP 和官方容器，并要求用 `oras version` 检查
 版本、OS/Arch 和 Git commit：[ORAS 安装](https://oras.land/docs/installation/)。
- 官方 Release 声明所有发布物都由 GPG key `E462A3894CBAAA47` 签名，`.asc` 与官方 `KEYS`
  一并提供：[ORAS v1.3.2 Release](https://github.com/oras-project/oras/releases/tag/v1.3.2)。

### 3.2 基于项目的推断

- Mangrove 应维护自己的供应链工具锁文件，至少包含 `name/version/source_url/sha256/upstream_signature`
  和容器 `image_digest`；Git tag、Release 标题和 Docker tag 都不能代替内容 digest。
- 当前工具更适合在无用户原件、无业务 Secret 的验证/获取阶段执行。扫描和签名工具不应进入
 业务 Sidecar，也不应复用 Mangrove 主 Python 环境。
- Trivy 曾发生真实供应链攻击，因此 AC-07 对“扫描器自身”的验证不能比对普通能力包更弱。

### 3.3 尚未验证

- 四个固定版本 Windows 资产的实际 SHA-256、Authenticode 状态及本机 Defender 行为；本轮未下载。
- 四个官方容器在当前 Docker Desktop 上解析出的实际 multi-arch manifest digest 和 AMD64
  platform digest；PoC 必须固定后者，不能只留 tag。
- Cosign TUF 自举在当前网络和 Windows PowerShell 下的无交互自动化方式。

## 4. Trivy 扫描与漏洞库

### 4.1 已验证事实

- `trivy fs` 支持漏洞、Secret、误配置和 License；默认启用 `vuln,secret`，误配置必须显式加入
  `--scanners misconfig`：[Filesystem target](https://trivy.dev/docs/latest/target/filesystem/)。
- `trivy image` 同样可通过 `--scanners vuln,misconfig,secret` 启用三类检查；误配置不是 image/fs
  的默认扫描器：[Misconfiguration scanning](https://trivy.dev/docs/latest/scanner/misconfiguration/)。
- Secret 扫描覆盖 image、filesystem 和 git repository，可用独立配置增加/约束规则：
  [Trivy Secret scanner](https://www.trivy.dev/docs/latest/guide/scanner/secret/)。
- Trivy 的漏洞 DB、Java DB 和 checks bundle 以 OCI artifact 分发。受限网络环境可以提前填充
  cache，并使用 `--skip-db-update`、`--skip-java-db-update` 和相应 checks 更新开关；Java 依赖
  分析若需严格不联网还要使用 `--offline-scan`。版本检查和遥测分别由
  `--skip-version-check`、`--disable-telemetry` 关闭：
  [Connectivity and network considerations](https://trivy.dev/docs/latest/guide/advanced/air-gap/)。
- 官方 air-gap 流程显示 cache 中的 DB 包含 `metadata.json` 和 `trivy.db`，且离线环境需要调用方
  自己定期更新 DB：[Trivy air-gap 文档](https://trivy.dev/docs/v0.50/guide/advanced/air-gap/)。
- `trivy --version` 会展示 DB 的 `UpdatedAt`、`NextUpdate` 和 `DownloadedAt`；官方项目的诊断记录
  可见该输出：[Trivy 官方 Discussion 示例](https://github.com/aquasecurity/trivy/discussions/8332)。
- Trivy 默认即使发现问题也可能返回退出码 0；调用方必须显式配置退出策略或解析 JSON，不能把
  “进程成功退出”当作“没有漏洞”：
  [Trivy 配置说明](https://github.com/aquasecurity/trivy/blob/main/docs/guide/configuration/others.md)。

### 4.2 基于项目的推断

- AC-07 对最终能力目录使用 `trivy fs --scanners vuln,misconfig,secret --format json`；对最终任务
  镜像使用同样三类 scanner 的 `trivy image`。目录扫描和镜像扫描回答的问题不同，不能互相替代。
- ADR-0029 的“DB 七天有效期”应以 DB 内容的 `UpdatedAt` 计算，而不是 `DownloadedAt`。否则重复
  搬运旧数据库会把过期内容伪装成新鲜数据库。三项时间和 DB schema/version 都应写入
  `ValidationRun`。
- 扫描器错误、JSON 无法解析、缺少 DB metadata、DB `UpdatedAt` 超过七天均属于验证失败关闭；
  不能把 Trivy 默认退出码 0 直接映射为通过。
- 离线验证命令应显式关闭 DB/Java/check 更新、远程 Java 解析、版本检查和遥测，并把 cache 以
  只读方式挂入扫描容器。DB 更新是独立获取阶段，不与业务来源共存。

### 4.3 尚未验证

- `v0.70.0` 对 Python Tool 目录和 Everything MCP 镜像能否稳定输出预期 JSON schema。
- Windows bind mount、Docker Desktop cache 挂载以及同时扫描 filesystem/image 的耗时和锁行为。
- misconfiguration checks bundle 的实际 metadata 字段，以及如何和漏洞 DB 七天策略分别计龄。

## 5. Syft SBOM

### 5.1 已验证事实

- Syft 支持容器镜像、目录/filesystem、archive，以及 OCI archive/OCI layout 等来源：
  [Supported scan targets](https://oss.anchore.com/docs/guides/sbom/scan-targets/)、
  [Syft 官方 README](https://github.com/anchore/syft#readme)。
- 官方格式包括 Syft JSON、CycloneDX JSON/XML、SPDX JSON/tag-value；格式可用
  `format@version` 固定 schema，例如 CycloneDX `1.6`、SPDX `2.3`：
  [Syft output formats](https://oss.anchore.com/docs/guides/sbom/formats/)。
- 目录扫描会包含已安装包和声明依赖，而镜像扫描默认侧重最终镜像里已安装的包；两类来源的
  cataloger 语义不同：[Syft catalogers](https://oss.anchore.com/docs/guides/sbom/catalogers/)。

### 5.2 基于项目的推断

- AC-07 应把 `syft-json` 作为保留完整 Anchore 发现信息的内部证据，同时至少产生一个固定 schema
  的可移植 SBOM；首选 `cyclonedx-json@1.6`。不能使用“默认最新版 schema”，否则重放结果会漂移。
- 对 Python Tool 扫最终冻结目录，对 Everything MCP 扫最终冻结 OCI image/layout；不能只扫
  源码目录，因为声明依赖不等于镜像中真实安装内容。
- SBOM 生成失败、格式版本不符或主体 digest 不匹配时，不允许进入 `verified`。

### 5.3 尚未验证

- 两个真实灰度包在目录扫描和 OCI layout 扫描下的包数量差异、路径稳定性与 SBOM 大小。
- Syft `v1.50.0` 在当前 Windows 长路径、中文路径和 Docker Desktop bind mount 下的行为。

## 6. Cosign 本地密钥、离线验证与 OCI Layout

### 6.1 已验证事实

- `cosign generate-key-pair` 生成受密码保护的本地私钥和公钥；密码可交互输入，也可由
  `COSIGN_PASSWORD` 提供。官方支持 RSA、ECDSA 和 ED25519，自带生成默认是本地加密 key：
  [Self-managed keys](https://docs.sigstore.dev/cosign/key_management/signing_with_self-managed_keys/)。
- 本地 key 可对 registry 中的 image digest 签名，`cosign verify --key cosign.pub` 可验证；签名
  payload 包含主体 digest，验证默认检查该 digest：
  [Signing containers](https://docs.sigstore.dev/cosign/signing/signing_with_containers/)、
  [Verifying signatures](https://docs.sigstore.dev/cosign/verifying/verify/)。
- Cosign 支持对普通 blob 生成 bundle 并使用本地 key 验证：
  [Signing blobs](https://docs.sigstore.dev/cosign/signing/signing_with_blobs/)。
- 官方文档支持对 `cosign save` 保存到磁盘的 image 使用
  `cosign verify --key cosign.pub --offline --local-image <path>`；这证明本地离线验证可行，
  但没有等价文档证明 `cosign sign --local-image <任意 OCI Layout>`：
  [Cosign local/offline verification](https://docs.sigstore.dev/cosign/verifying/verify/)。

### 6.2 基于项目的推断

- 项目外加密私钥应只进入短生命周期发布进程；`COSIGN_PASSWORD` 不能出现在命令行、日志、
  Event、Docker argv 或能力包中。公钥可以作为平台信任根冻结并进入运行镜像。
- AC-07 首选标准流程是：ORAS 将本地 Layout 的主体按 digest 复制到只监听回环地址的临时 OCI
  Registry → Cosign 用本地 key 对 registry digest 签名 → 离线/本地公钥验证 → ORAS 递归复制
  主体和 referrers 回独立平台 Layout。ORAS 官方支持 Layout 与 Registry 双向复制以及 referrers：
  [`oras cp`](https://oras.land/docs/commands/oras_cp/)。
- 本地 Registry 必须是任务级临时基础设施，只监听 `127.0.0.1`，使用独立存储并在验证后清理；
  不能把它升级为项目范围的常驻 Registry，也不能因此开放 LAN 端口。
- 如果 Registry 闭环 PoC 不通过，可以对规范化主体 manifest blob 使用 `sign-blob` 形成外置 bundle；
  这只证明某段 manifest bytes 被平台 key 签名，不应冒充标准 OCI image signature/referrer。

### 6.3 尚未验证

- Cosign `v3.0.6`、ORAS `v1.3.2` 与当前 Layout 的 manifest/artifact type/referrers 互操作。
- `oras cp -r` 把 Cosign v3 signature/referrer 从临时 Registry 带回本地 Layout后，能否在完全离线
  状态通过 `--local-image` 验证。
- Cosign v3 对本地 key + 无 Rekor/TSA 的精确 flag、输出 bundle 和并发签名行为。
- Windows Docker Desktop 上回环 Registry、容器到宿主回环映射及清理失败恢复。

## 7. ORAS/OCI 与最小替代

### 7.1 已验证事实

- OCI Image Layout 用 `oci-layout`、`index.json` 和内容寻址 `blobs` 表示本地 OCI 内容：
  [OCI Image Layout Specification](https://github.com/opencontainers/image-spec/blob/main/image-layout.md)。
- ORAS 可读写本地 OCI Layout，也能将 Layout 复制到 Registry、从 Registry 复制回 Layout，并可
  递归携带 referrer：[ORAS OCI Layout 指南](https://oras.land/docs/how_to_guides/distributing_oci_layouts/)、
  [`oras cp`](https://oras.land/docs/commands/oras_cp/)。
- ORAS 官方 Quick Start 使用 Docker 启动的本地 Zot Registry；这证明“本地 Registry 作为标准
  Distribution seam”是官方支持路径，而不是 Mangrove 自创协议：
  [ORAS Quick Start](https://oras.land/docs/quickstart/)。

### 7.2 基于项目的推断

- 继续以 OCI Layout 作为单机静态能力仓库，但把签名所需的 Registry 仅作为发布事务内部的临时
  适配层，是对现有 AC-06 架构影响最小的方案。
- 任务引用仍只保存最终平台主体 digest；tag 只供显示。签名、SBOM 和扫描证据必须明确引用同一
  subject digest，不能只按文件名或版本号关联。

### 7.3 尚未验证

- ORAS 对同一 Layout 的并发写、Windows 文件锁、崩溃恢复和中文/长路径行为。
- `oras cp -r` 在当前 OCI 1.1 referrers 模式下是否保留 Cosign、SBOM 和扫描报告的完整关系。

## 8. Windows 与 Docker Desktop 可行性

### 8.1 已验证事实

- Trivy、Syft、Cosign、ORAS 都提供 Windows 可运行发行物或官方 Windows 安装说明；Trivy、Syft、
  Cosign 和 ORAS 也均提供容器使用路径：
  [Trivy 安装](https://www.trivy.dev/docs/v0.70/getting-started/installation/)、
  [Syft 安装](https://oss.anchore.com/docs/installation/syft/)、
  [Cosign 安装](https://docs.sigstore.dev/cosign/system_config/installation/)、
  [ORAS 安装](https://oras.land/docs/installation/)。

### 8.2 基于项目的推断

- 因最终能力运行环境是 Linux 容器，首版优先使用按 digest 固定的 Linux 官方容器，可减少 Windows
  路径和四套本地二进制的差异；但 Cosign 私钥注入、回环 Registry 和 Docker socket 权限必须
  采用最小挂载，不能直接挂整个项目或用户目录。
- 如果 Docker 容器无法可靠读写当前 Windows OCI Layout，再降级为经上游签名验证的 Windows
  原生 CLI；不能在实现过程中无授权地切换工具、镜像或来源。

### 8.3 必须完成的 PoC

1. 验证四个固定版本的官方签名、checksum 和实际 SHA-256，形成工具锁定清单。
2. 用 Python Tool 目录和 Everything MCP OCI 包分别生成 Syft JSON + CycloneDX 1.6 SBOM。
3. 对两者运行 Trivy `vuln,misconfig,secret`，保存 JSON、DB `UpdatedAt/NextUpdate/DownloadedAt`，
   并证明过期 DB 和扫描器异常都会失败关闭。
4. 启动仅回环可达的临时 Registry，完成 Layout → Registry → 按 digest 签名 → 公钥验证 →
   主体及 referrers 回 Layout → 断网本地验证。
5. 验证私钥、密码、业务原件、Owner 路径和 Provider Secret 不进入 argv、日志、Event、SBOM、
   扫描报告或平台快照。
6. 模拟进程崩溃、取消和重复执行，证明 Registry、容器、临时网络和密钥挂载能够清理且操作幂等。

在这六项 PoC 完成前，只能把工具选型写成“官方能力与架构方向已确认”，不能把本地签名、离线
验证或 Windows/Docker Desktop 兼容写成工程通过。

## 9. 对 AC-07 规格的直接建议

1. 固定 `Trivy 0.70.0 / Syft 1.50.0 / Cosign 3.0.6 / ORAS 1.3.2`，但实际安装必须由后续明确
   授权的获取任务执行，并把上游验证证据写入工具锁定清单。
2. Trivy 直接扫描最终目录和最终镜像；Syft 负责 SBOM，不用“扫描 Syft SBOM”替代最终对象扫描。
3. 七天门以 Trivy DB `UpdatedAt` 为准；`DownloadedAt` 只作传输审计。
4. 平台 SBOM 至少固定 CycloneDX JSON 1.6，同时保存 Syft JSON 供内部追溯。
5. 把“Cosign 直接签名本地 OCI Layout”改为待 PoC；规格主路径采用临时本地 Registry 的标准签名
   闭环，`sign-blob` 仅作语义明确的后备方案。
6. 所有工具错误、输出无法解析、主体 digest 不一致、DB 过期、签名缺失或验证失败都保持
   `draft/quarantined`，不得降级为告警后继续晋级。
