# Agentic Capability Acquisition 开源方案调研

> 日期：2026-08-02
> 范围：Pi 自主获取工具、MCP、Skill，验证后沉淀为个人/平台 SOP 能力包
> 资料边界：只使用官方文档、官方仓库、正式规范等一手来源
> 本文状态：研究结论，不构成实现授权

## 1. 结论

Mangrove 不需要自行发明包格式、依赖解析器、容器构建缓存、SBOM 或签名协议。推荐的最小
技术组合是：

1. 用 **Agent Skills 开放规范**承载可渐进披露的 `SKILL.md`、脚本、参考资料和资源；
2. 用 **uv + `uv.lock`** 固定 Python 依赖，用 **npm + `package-lock.json` + `npm ci`**
   固定 Node 依赖；
3. 用 **BuildKit** 在不挂载用户来源的获取阶段构建能力环境，并复用包管理器缓存和构建层；
4. 用 **OCI Artifact + ORAS** 保存不可变能力包，业务任务只绑定 OCI digest，不能绑定可移动
   tag；单机第一版先使用 OCI Image Layout，跨主机时再接 OCI Registry；
5. 把 **MCP 官方 Registry** 仅作为候选发现和标准化元数据来源；它不是代码安全背书，也不是
   Mangrove 的直接运行许可；
6. 用 **Trivy** 扫描漏洞、Secret 和容器/文件系统问题，用 **Syft** 为进入 `verified` 或
   `platform_shared` 的能力生成标准 SBOM；
7. 个人草稿依靠 digest、锁文件和验证证据保证可复现；管理员发布平台能力前，再用
   **Cosign** 对 OCI digest 签名，运行前验证签名；
8. 采用 SLSA/in-toto 的 Provenance 数据模型，但第一版不声称达到 SLSA L2/L3，也不建设完整
   in-toto Layout、私有 Fulcio/Rekor 或复杂策略引擎。

这套组合与 Mangrove 已有的任务级 Docker、Smokescreen Egress、Purpose Grant、Owner 隔离
和 Verifier 相容。最重要的设计约束仍然是：

```text
能力获取阶段：可访问批准的依赖来源，不挂载用户原件，不注入业务 Provider Key
业务执行阶段：挂载只读原件，只加载已冻结能力包，不访问公共依赖站点
```

项目当前只有上述阶段策略，尚无独立依赖获取编排，证据见
[PG-05 Egress 纵切面报告](../plans/2026-07-29-agentic-runtime-vnext-pg05-live-cancel-egress-slice-report.md)
和 [Phase 4 当前问题审查](../plans/2026-08-02-phase4-current-issues-audit.md)。

## 2. 判断口径

本文将结论分为三类：

- **已验证事实**：由正式规范或项目官方文档直接支持；
- **基于项目的推断**：依据 Mangrove 已有实现边界作出的架构判断；
- **尚未验证建议**：适合进入规格或 PoC，但还没有在当前 Windows/Docker Desktop 环境实测。

本文没有安装候选工具、构建能力包或调用外部 MCP；版本兼容性、Windows 路径行为、并发写入
和实际性能仍需后续 PoC 验证。

## 3. OCI Artifact 与 ORAS

### 3.1 已验证事实

- OCI Descriptor 的 `digest` 是内容标识符，内容消费者应重新计算并校验；规范建议使用
  SHA-256。OCI tag 则只是指向 manifest 的人类可读指针，一个 manifest 可以有零个或多个
  tag。因此 digest 适合作为不可变任务绑定，tag 只适合作为可读别名：
  [OCI Descriptor](https://github.com/opencontainers/image-spec/blob/main/descriptor.md)、
  [OCI Distribution Spec](https://github.com/opencontainers/distribution-spec/blob/main/spec.md)。
- OCI Distribution Spec 不限定内容必须是容器镜像，可分发任意由 manifest 和 blob 组成的
  内容；OCI 1.1 Referrers API 可以把签名、SBOM、扫描报告等对象关联到主体 digest：
  [OCI Distribution Spec](https://github.com/opencontainers/distribution-spec/blob/main/spec.md)。
- ORAS 可以把任意文件、目录和自定义 media type 推送为 OCI Artifact，也可以直接写入本地
  OCI Image Layout；`ORAS_CACHE` 可启用内容寻址的拉取缓存：
  [`oras push`](https://oras.land/docs/commands/oras_push/)、
  [OCI Layout 分发](https://oras.land/docs/how_to_guides/distributing_oci_layouts/)、
  [ORAS 拉取缓存](https://oras.land/docs/1.2/how_to_guides/pushing_and_pulling/)。
- ORAS 支持向 artifact 附加其他 artifact，适合挂接 SBOM、扫描报告和签名：
  [`oras attach`](https://oras.land/docs/commands/oras_attach/)。

### 3.2 基于项目的推断

- 能力包的不可变身份应为 `sha256:...`，`personal/foo:v3` 或 `platform/foo:stable` 只能用于
  界面显示和发现。`TaskRevision`、`Run`、验证证据和历史回放必须冻结 digest。
- OCI 负责内容和引用，不负责 Mangrove 的用户权限。`owner_id`、个人/平台可见性、管理员发布
  决策和删除策略必须继续由 Mangrove 数据库与 API 做授权，不能只靠仓库命名空间。
- 个人 SOP 发布为平台 SOP 时不能只把同一条记录的 `visibility` 改成 `platform`。应脱敏、
  重新验证并形成新的 OCI digest，个人原版本继续独立存在。
- 单机第一版可以用 ORAS OCI Image Layout 避免立即运营 Registry；但 ORAS 官方资料没有承诺
  多进程并发写同一 Layout 的事务语义，首版写入应串行。跨主机、服务器并发或远端备份时再切换
  到标准 OCI Registry。

### 3.3 尚未验证建议

建议定义一个 Mangrove 自有 artifact type：

```text
application/vnd.mangrove.capability-pack.v1
```

一个能力包可以包含：

```text
capability.json                 机器可读总清单
skill/SKILL.md                  Agent Skills 入口
skill/scripts/                  可执行脚本
skill/references/               按需读取的参考资料
sop/sop.json                    结构化步骤、失败条件和验证规则
mcp/server.json                 MCP 元数据快照；仅在存在 MCP 时提供
locks/uv.lock                   Python 精确解析结果
locks/package-lock.json         Node 精确依赖树与 integrity
policy/requirements.json        网络、目录、Secret 槽位和资源预算声明
tests/synthetic/                合成/脱敏测试夹具
evidence/verification.json      验证摘要，不包含业务原文
```

SBOM、扫描结果、Provenance 和签名更适合作为 OCI referrer 挂到能力包 digest，而不是反复复制
进主包。是否所有目标 Registry 都完整支持 OCI 1.1 Referrers，需要在选定 Registry 后做兼容性
PoC。

## 4. Python 与 Node 依赖锁定和缓存

### 4.1 Python：uv

#### 已验证事实

- `uv.lock` 记录精确解析版本，并支持跨平台 marker；`uv sync --locked` 在锁文件过期时失败，
  `uv sync --frozen` 只使用现有锁文件而不更新。新版本发布不会自动改变既有 lock：
  [uv 锁定与同步](https://docs.astral.sh/uv/concepts/projects/sync/)、
  [uv 项目结构](https://docs.astral.sh/uv/concepts/projects/layout/)。
- uv 使用积极缓存；Registry 依赖遵从 HTTP 缓存，Git 依赖按完整 commit hash 缓存。缓存是
  append-only、支持并发读写，并对目标虚拟环境加文件锁；缓存与环境在同一文件系统时可以链接
  而不是复制：
  [uv 缓存](https://docs.astral.sh/uv/concepts/cache/)。
- uv 可以导出 `requirements.txt`、PEP 751 `pylock.toml` 和 CycloneDX SBOM；它也支持
  `exclude-newer` 依赖冷静期。同步时的恶意包检查当前仍是预览能力，不能当成稳定安全门：
  [uv 锁定与同步](https://docs.astral.sh/uv/concepts/projects/sync/)、
  [uv 解析](https://docs.astral.sh/uv/concepts/resolution/)。
- `uvx` 会为工具创建隔离环境并缓存，但该环境被定义为可丢弃缓存；手工修改工具环境不受支持：
  [uv 工具环境](https://docs.astral.sh/uv/concepts/tools/)。

#### 基于项目的推断

- Pi 可以用 `uvx package@version` 做候选试用，但能力包进入 `draft` 前必须转成包含
  `pyproject.toml` 和 `uv.lock` 的冻结项目；不能把“某次 uvx 正好能运行”当成可复现证据。
- 业务执行阶段只允许 `uv sync --frozen` 从已封存 wheel/cache 或已构建环境恢复，不得重新
  解析公网依赖。
- uv 缓存只解决速度，不能作为信任依据。最终信任依据仍是能力包 digest、lock、下载物哈希、
  扫描和验证结果。

### 4.2 Node：npm

#### 已验证事实

- `package-lock.json` 记录精确依赖树；其中 `resolved` 指向来源，`integrity` 保存下载物的
  SRI 哈希。官方将其定位为可复现安装和审查依赖变化的文件：
  [npm `package-lock.json`](https://docs.npmjs.com/cli/v11/configuring-npm/package-lock-json/)。
- `npm ci` 要求已有 lock；当 `package.json` 与 lock 不一致时直接失败，不修改 manifest 或
  lock，并会先清理已有 `node_modules`：
  [npm ci](https://docs.npmjs.com/cli/v11/commands/npm-ci/)。
- npm 生命周期脚本默认可以执行。官方提供 `--ignore-scripts`，较新 npm 也提供
  `allow-scripts`、`strict-allow-scripts` 等控制；这说明安装脚本本身应被视为代码执行，而非
  普通下载：
  [npm ci 配置](https://docs.npmjs.com/cli/v11/commands/npm-ci/)。

#### 基于项目的推断

- Pi 不应在生产能力中使用裸 `npx -y some-server` 或浮动 `latest`。MCP/CLI 候选验证后应生成
  独立 `package.json` 和 `package-lock.json`，再通过 `npm ci` 构建冻结环境。
- 首次解析默认应禁用生命周期脚本；确有必要时，把允许脚本的包名、原因和命令作为能力包权限
  声明，并在隔离构建阶段显式批准。它不能继承宿主机用户的 `.npmrc` 或写权限 Token。
- npm 缓存和 BuildKit cache mount 可显著降低重复安装成本，但业务任务应挂载已经验证的
  只读能力层，而不是直接把可写 npm cache 当作运行环境。

### 4.3 最小锁定规则

建议第一版统一执行：

| 依赖种类 | 发现阶段 | 冻结条件 | 业务执行 |
|---|---|---|---|
| Python Registry | 允许解析 | `uv.lock` + 下载物哈希 | `uv sync --frozen` 或只读环境 |
| Python Git | 允许候选 ref | 完整 commit SHA + lock | 禁止重新解析分支 |
| npm Registry | 允许解析 | `package-lock.json` + SRI | `npm ci` 生成的只读环境 |
| GitHub Release | 允许官方 Release | 精确 URL + SHA-256；有上游签名则验证 | 只读二进制 |
| OCI 镜像 | 允许官方 Registry | image digest | `image@sha256:...` |
| 陌生 URL | 先暂停确认 | 原 URL、最终 URL、哈希、扫描、测试 | 仅已冻结副本 |

这张表是尚未在当前环境验证的 Mangrove 策略建议，不是上述工具的默认行为。

## 5. MCP Registry、Server 配置与安全边界

### 5.1 已验证事实

- MCP 官方 Registry 当前是 **Preview**；它保存 `server.json` 元数据、标准化安装/运行信息和
  namespace 归属。它只托管元数据，实际 npm/PyPI/OCI 包仍由底层 Registry 承载：
  [MCP Registry About](https://modelcontextprotocol.io/registry/about)、
  [发布 Quickstart](https://modelcontextprotocol.io/registry/quickstart)。
- 官方 Registry 的 namespace 验证只能证明发布者控制相应 GitHub 账号或域名。官方明确把
  实际代码扫描委托给底层包仓库和下游聚合器；Registry 本身不提供代码安全结论：
  [Trust and Security](https://modelcontextprotocol.io/registry/about#trust-and-security)。
- 官方 Registry 不支持私有 MCP，官方 Registry 代码也不是为自托管而设计；Host 应消费符合
  Registry OpenAPI 的下游聚合器，而不是把官方 Registry 当作直接应用商店：
  [Registry 生态关系](https://modelcontextprotocol.io/registry/about#the-mcp-registry-ecosystem)。
- Registry 版本元数据发布后不可修改，版本号必须唯一；官方建议 Server 与底层 package 对齐
  精确版本：
  [MCP Registry Versioning](https://modelcontextprotocol.io/registry/versioning)。
- MCP 官方安全文档明确指出，本地 MCP Server 是下载后执行的二进制，可能执行恶意启动命令、
  读取本地数据或造成数据丢失；客户端应显示完整命令、取得同意、限制文件系统/网络并在沙箱中
  运行。对本地 Server，`stdio` 可减少额外监听面：
  [MCP Security Best Practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices#local-mcp-server-compromise)。
- MCP OAuth 禁止 token passthrough，要求 token audience/resource 绑定；官方还列出了 OAuth
  元数据 SSRF、DNS rebinding、内网和云元数据攻击，并建议用 Egress Proxy 与网络策略防护：
  [MCP Authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization)、
  [MCP Security Best Practices](https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices#server-side-request-forgery-ssrf)。
- MCP 官方 reference servers 明确是教学参考，不是生产就绪实现；例如 Fetch reference server
  还明确提示可以访问本地/内网地址：
  [Reference Servers](https://github.com/modelcontextprotocol/servers#readme)、
  [Fetch Server](https://github.com/modelcontextprotocol/servers/blob/main/src/fetch/README.md)。

### 5.2 基于项目的推断

- Mangrove 能力获取服务可以把 MCP 官方 Registry 当作上游 `DiscoveryFeed`，但业务 Runtime
  Host 只消费 Mangrove 已同步、审查的私有目录，不能直接连官方 Registry 执行候选。Pi 找到
  候选后仍需解析底层 package/image、固定版本和 digest、扫描、合成测试并走权限门。
- Registry 的 `server.json` 可能带启动命令和环境变量声明，但 Mangrove 不应把字符串交给
  Shell。应规范化为可校验的 argv、工作目录、transport、Secret 槽位和资源声明。
- 本地 stdio MCP 应作为能力包中的进程在任务容器内启动，不能在 Windows 宿主机或 Mangrove
  主 Python 环境执行。远程 MCP 只保存 endpoint、transport、schema 快照和 Secret 引用；
  用户凭证仍由现有连接/Grant 体系在运行时短期注入。
- 个人 MCP/SOP 只能被 owner 检索和执行；平台 MCP/SOP 必须形成新的已脱敏、已签名 digest。
  官方 Registry 不支持私有内容，因此个人和 LAN MCP 必须保存在 Mangrove 私有目录，而不是
  尝试发布到官方 Registry。
- MCP Server 的 tool description、prompt 和返回内容都是不可信输入，不能直接修改冻结的
  GoalContract、权限或 Revision。对数据外发、目录扩大和新网络目标仍必须进入现有确认门。

### 5.3 尚未验证建议

第一版 MCP 接入优先支持两类：

1. 已固定 npm/Python/OCI 版本的本地 `stdio` MCP；
2. 精确 HTTPS endpoint 的远程 Streamable HTTP MCP。

SSE legacy、自动 OAuth Dynamic Client Registration、任意代理转发和自建私有 MCP Registry
先后置。Mangrove 私有目录只需实现最小查询 API，不应 fork 官方 Registry 做一套市场。

## 6. Agent Skills 与 SOP

### 6.1 已验证事实

- Agent Skills 是开放格式：最小包是含 YAML frontmatter 的 `SKILL.md`，可附带 `scripts/`、
  `references/` 和 `assets/`；官方提供 `skills-ref validate` 做格式验证：
  [Agent Skills Specification](https://agentskills.io/specification)。
- 该规范采用渐进式披露：启动时只加载约百 token 的 name/description，匹配后加载正文，资源
  仅在需要时读取。这直接适合减少 Pi 的上下文窗口占用：
  [Agent Skills Progressive Disclosure](https://agentskills.io/specification#progressive-disclosure)。
- `allowed-tools` 当前仍是实验字段，不同 Agent 的支持可能不同。因此不能把它当成 Mangrove
  的强制权限机制：
  [Agent Skills Frontmatter](https://agentskills.io/specification#frontmatter)。

### 6.2 基于项目的推断

- SOP 最适合用 `SKILL.md` 承载给 Pi 的简洁方法和触发描述，用额外 `sop.json` 承载确定性的
  输入/输出 Schema、能力依赖、验证规则、权限声明和版本关系。不能把所有平台状态塞进自由文本。
- 原子能力与组合 SOP 应分层：原子能力包固定工具/MCP/Skill 及单项验证；SOP 只按 digest 引用
  多个已验证原子能力，并描述优先路径、失败条件和重规划边界。
- `SKILL.md` 中的脚本仍是可执行代码，必须和 npm/Python 工具走同一获取、扫描和隔离门；
  “只是 Markdown Skill”不代表安全。
- Pi 自动总结的成功轨迹只能生成个人 `draft`。合成测试、至少一次 owner 的真实任务和失败关闭
  测试通过后才能成为个人 `verified`；管理员脱敏、重测并签名后才能成为 `platform_shared`。

## 7. 签名、Provenance、SBOM 与扫描

### 7.1 Cosign / Sigstore

#### 已验证事实

- Cosign 可以签名和验证容器、普通 blob，并可把自定义 in-toto attestation 关联到 OCI 对象；
  签名 payload 包含目标 image digest，验证默认检查该 digest：
  [Cosign Signing](https://docs.sigstore.dev/cosign/signing/signing_with_containers/)、
  [Cosign Verification](https://docs.sigstore.dev/cosign/verifying/verify/)。
- Cosign 支持 OIDC keyless、自管理密钥和多种 KMS。签名、SBOM 和 attestation 可使用 OCI 1.1
  Referrers；Sigstore bundle 还支持离线验证：
  [Sigstore Keyless Overview](https://docs.sigstore.dev/cosign/signing/overview/)、
  [Cosign Key Management](https://docs.sigstore.dev/cosign/key_management/overview/)。

#### 基于项目的推断

- 个人 `draft` 无需签名，但必须有不可变 digest。平台发布属于管理员控制的信任跃迁，应以
  Mangrove 平台签名绑定确切 digest；加载平台能力时同时校验 owner/visibility 和签名。
- 第一版不适合要求普通用户完成 OIDC keyless 登录。可先用保存在项目外、仅发布服务可读取的
  自管理密钥；正式服务器阶段再评估 KMS 或 keyless。

### 7.2 SLSA / in-toto / BuildKit Provenance

#### 已验证事实

- SLSA 1.2 把供应链安全划分为递进等级：Build L1 要求存在 Provenance，L2 要求托管构建平台
  生成签名 Provenance，L3 进一步要求加固构建平台。SLSA 也明确不判断代码质量或生产者是否
  故意恶意：
  [SLSA 1.2](https://slsa.dev/spec/v1.2/)、
  [Build Track](https://slsa.dev/spec/v1.2/build-track-basics)、
  [SLSA 边界](https://slsa.dev/spec/v1.2/about)。
- in-toto 使用由项目 owner 签名的 Layout 和各步骤 functionary 签名的 Link 保护完整供应链；
  验证需要 Layout、Link 和 owner 公钥：
  [in-toto Getting Started](https://in-toto.io/docs/getting-started/)。
- BuildKit 可以原生生成 SLSA Provenance v1 和 SBOM attestation；`mode=min` 不包含 build arg
  值，`mode=max` 信息更完整但可能暴露 build arg，因此 Secret 应通过 secret mount 提供：
  [BuildKit Provenance](https://docs.docker.com/build/metadata/attestations/slsa-provenance/)、
  [Build Secrets](https://docs.docker.com/build/building/secrets/)。

#### 基于项目的推断

- 第一版应直接保存 BuildKit `mode=min,version=v1` Provenance 和 Mangrove 自己的来源解析记录，
  但不能因此宣称 SLSA L1/L2。等级还取决于构建平台和验证根，不是 JSON 格式正确就自动获得。
- 完整 in-toto Layout 会要求为“发现、下载、构建、扫描、验证、发布”管理 functionary key 和
  Link，对当前单机学习环境过重。第一版只复用 in-toto Statement/attestation 格式即可。

### 7.3 Syft / Trivy

#### 已验证事实

- Syft 可从容器镜像、文件系统和 archive 生成 SPDX、CycloneDX 等 SBOM，并覆盖 Python、
  JavaScript 和多种系统包生态：
  [Syft 官方仓库](https://github.com/anchore/syft#readme)。
- Trivy 可以对文件系统和容器执行漏洞、Secret、误配置和 License 扫描，也可以生成/读取 SBOM；
  Secret 与漏洞扫描默认启用，误配置扫描需显式开启：
  [Trivy Filesystem](https://trivy.dev/docs/latest/target/filesystem/)、
  [Trivy Container Image](https://trivy.dev/docs/latest/target/container_image/)。
- Trivy 官方提示，读取其他工具生成的 SBOM 可能因缺少 Trivy 自定义属性而降低检测准确性：
  [Trivy SBOM Scanning](https://trivy.dev/docs/latest/guide/target/sbom/)。

#### 基于项目的推断

- 第一版应让 Trivy直接扫描最终目录/镜像，不要只扫描 Syft 输出；Syft SBOM用于资产清单和
  后续复查，两者职责分开。
- Secret 扫描命中必须失败关闭。漏洞门槛不能简单写成“存在任何 CVE 就拒绝”，应在规格中区分
  severity、是否有修复、是否可达和管理员例外，并保留扫描数据库版本。
- 扫描通过只证明当前规则和漏洞库没有发现问题，不证明能力安全；仍需合成测试、权限测试和
  Verifier。

### 7.4 最小供应链证据

建议每个进入 `verified` 的能力至少保留：

```text
主体 OCI digest
原始来源 URI 与最终下载 URI
上游版本、Git commit 或 image digest
Python/Node lock
BuildKit provenance（若经过构建）
Syft SBOM
Trivy JSON 扫描结果与 DB 版本
合成测试、失败关闭测试和 Verifier 摘要
```

`platform_shared` 再增加管理员身份、审核时间、发布依据和 Cosign 签名。该证据集合是尚未验证
的 Mangrove 建议，不代表满足某个 SLSA 等级。

## 8. BuildKit 隔离与缓存

### 8.1 已验证事实

- BuildKit 使用内容寻址构建图，能跳过未使用阶段、并行独立步骤并精确复用缓存；cache mount
  可以持久化 uv/npm 下载缓存，外部 cache 可通过 Registry 在不同 builder 间共享：
  [BuildKit](https://docs.docker.com/build/buildkit/)、
  [优化 Build Cache](https://docs.docker.com/build/cache/optimize/)。
- `docker buildx build --network=none` 可让 Build 中的 `RUN` 无网络；`network.host` 和
  `security.insecure` 属于额外危险 entitlement，不应默认开启：
  [`docker buildx build`](https://docs.docker.com/reference/cli/docker/buildx/build/)。
- Build arg 和环境变量不适合传 Secret，因为可能持久化到最终镜像；BuildKit secret mount
  只在单个构建指令期间暴露：
  [Build Secrets](https://docs.docker.com/build/building/secrets/)。
- Docker Rootless 模式让 daemon 与容器都在非 root 用户命名空间运行，可减轻 daemon/runtime
  漏洞影响；但它有 Linux 前置条件：
  [Docker Rootless](https://docs.docker.com/engine/security/rootless/)。

### 8.2 基于项目的推断

- Mangrove 可以把“联网解析/下载”和“离线组装/验证”拆成两个 BuildKit target：前者不挂载
  用户来源，后者 `--network=none`，只消费冻结下载物和 lock。
- 缓存只能挂载到构建过程，不应进入最终能力层；任务运行时把能力层只读挂载到现有 Pi 容器，
  不必为每个工具启动独立容器。因此后续调用开销主要是 digest 查询和只读挂载，不会重复下载。
- BuildKit 的共享 cache 提升性能但不是发布物；清缓存后能力包仍应能从 OCI digest 恢复。
- 当前目标是 Windows Docker Desktop，本轮不能把 Linux Rootless 的官方能力写成已可用。应在
  后置的 8B/Linux 服务器门中实测。

### 8.3 尚未验证建议

建议 PoC 测量以下四个指标，再决定缓存布局：

1. Python 能力冷构建与 uv 热缓存构建时间；
2. Node MCP 冷构建与 npm cache mount 热构建时间；
3. 本地 OCI Layout 首次拉取与重复挂载时间；
4. 同一任务挂载 1、5、10 个原子能力时的 Pi 启动增量。

不要预先建设跨主机 Registry cache、远程 BuildKit 集群或 Kubernetes builder。

## 9. 个人与平台 SOP 隔离

### 9.1 基于项目的推断

推荐把“内容”和“授权”分离：

```text
OCI/ORAS：不可变能力内容、SBOM、扫描、Provenance、签名
Mangrove DB：owner_id、visibility、状态、别名、默认版本、审核和撤销
Secret Store：用户 Key/OAuth token；能力包只声明 Secret 槽位
Task/Revision：冻结实际选择的 capability digest 和 SOP digest
```

最低授权规则：

| 对象 | 可见/可用范围 | 发布方式 |
|---|---|---|
| 个人 `draft` | 仅 owner | Pi 首次成功后生成 |
| 个人 `verified` | 仅 owner | 合成、真实任务、失败关闭验证通过 |
| 平台候选 | 管理员/超级管理员 | 从个人版本脱敏复制为新 digest |
| `platform_shared` | 获准用户 | 管理员审核、重测、签名后发布 |

- Pi 只能检索当前用户的个人 SOP 和平台 SOP，不能看到其他用户个人目录或统计明细。
- 能力包不能包含真实 Key、用户原文、宿主绝对路径或真实结果值；个人能力只引用 owner 的任务
  ID，平台版本使用合成/脱敏夹具。使用真实样本发布需单独授权。
- 平台撤销应停止新任务选择，但历史 TaskRevision 仍保留原 digest 和证据；是否允许重新执行
  已撤销版本，需要在后续状态机规格中单独决定。

这些是用户已确认方向与现有 Owner 隔离边界的设计推断，尚未落地为数据库和 API。

## 10. 第一版与后置范围

### 10.1 进入最小第一版

| 能力 | 采用方式 | 原因 |
|---|---|---|
| Agent Skills | `SKILL.md` + `skills-ref validate` | 成熟开放格式，直接支持渐进披露 |
| uv | 每个 Python 能力生成 `uv.lock`，冻结同步 | 避免污染 Mangrove 主环境，缓存成熟 |
| npm | `package-lock.json` + `npm ci`，默认禁安装脚本 | 精确树和 integrity 已由 npm 提供 |
| BuildKit | 获取/离线构建 target、cache mount、只读能力层 | 复用现有 Docker，兼顾隔离和性能 |
| ORAS/OCI | 单机 OCI Layout，Task 按 digest 绑定 | 不自创 artifact/CAS 协议 |
| MCP Registry | 获取服务的上游 Feed；业务 Host 只查 Mangrove 目录 | Registry 是 Preview 且不扫描代码 |
| Trivy | 扫最终目录/镜像；Secret 失败关闭 | 同时覆盖漏洞、Secret、误配置 |
| Syft | `verified` 及平台候选生成 SBOM | 覆盖二进制与多包生态 |
| Cosign | 平台发布签名，加载时验证 | 把管理员审核绑定到确切 digest |
| Mangrove DB | 个人/平台 ACL、状态、别名、审计 | OCI 不提供产品权限语义 |

第一版仍然需要 Mangrove 自己实现的只有薄编排层：状态机、Owner 授权、来源策略编译、资源预算、
Verifier 适配、事件流和 UI；包解析、构建、缓存、扫描、SBOM、内容寻址与签名都复用成熟工具。

### 10.2 明确后置

- 自建 MCP Marketplace 或 fork 官方 Registry；
- 自动执行任意陌生 URL、任意启动命令或裸 `npx`；
- SLSA L2/L3 合规宣称、完整 in-toto Layout 和 functionary key 管理；
- 私有 Fulcio、Rekor、TUF 或企业 KMS 基础设施；
- 全自动 CVE 例外、VEX、复杂 Rego/CUE Policy Engine；
- 跨主机 OCI Registry、远程 BuildKit 集群和 Registry build cache；
- Kubernetes admission、Rootless 生产部署和服务器并发门；
- HTTPS MITM/DLP；现有 Egress 只控制目的地，不证明正文没有外发；
- 未经用户确认，把个人真实样本、SOP 或 MCP 发布给平台；
- 静默升级已验证能力、原地覆盖历史版本或自动改变默认平台 SOP。

这些能力不是没有价值，而是在当前单机学习项目和 Phase 4 收口阶段会显著增加运维、密钥、
兼容性和误阻断成本。

## 11. 推荐的最小流程

```text
Pi 发现能力缺口
→ 查询 owner 个人能力 + 平台能力
→ 无匹配时查询批准的 DiscoverySource
→ 选择官方 Registry/仓库候选；陌生 URL 先确认
→ 进入 dependency_acquisition（无用户来源、无业务 Key）
→ 生成精确 lock，下载到共享缓存
→ Trivy 扫描，BuildKit 组装只读能力层
→ ORAS 写入 OCI Layout，得到 capability_digest
→ 加载到无公网依赖访问的合成测试任务
→ Verifier 通过后生成个人 draft
→ owner 真实任务验证和失败关闭测试通过后成为个人 verified
→ 管理员脱敏复制、重测、生成 SBOM、Cosign 签名
→ 生成新的 platform_shared digest
→ 后续 TaskRevision 冻结 SOP digest + capability digest 集合
```

缓存命中只减少下载和构建，不跳过 digest、签名、权限、验证状态和兼容性检查。

## 12. 需要通过 PoC 回答的问题

以下内容不能仅凭文档宣称已解决：

1. ORAS OCI Layout 在当前 Windows 路径、长路径和并发读写下的行为；
2. uv cache、npm cache mount 与 Docker Desktop 文件系统之间的实际性能；
3. BuildKit 导出的非镜像 OCI artifact、SBOM/Provenance referrer 与 ORAS 的互操作；
4. Cosign 对本地 OCI Layout 的签名/离线验证流程，还是需要先推入 Registry；
5. Pi 是否能稳定从任务缺口生成最小依赖声明，而不是下载过多工具；
6. MCP `server.json` 到无 Shell typed argv 的规范化覆盖率；
7. MCP 远程 OAuth、DNS rebinding、redirect chain 和 owner token 隔离；
8. 个人 SOP → 脱敏平台候选是否会误带真实路径、值或 Evidence；
9. 失效/撤销平台能力对运行中任务、恢复任务和历史回放的精确定义；
10. 109 页 PDF、Word、Excel 各一条真实任务在冷缓存和热缓存下的总耗时变化。

## 13. 最终建议

推荐将下一实现包从原来的“独立依赖获取状态机”提升为一个更准确但仍受控的纵切面：

> **Capability Acquisition + Personal SOP 最小闭环**

其验收终点不是“Pi 成功执行了 `pip install`”，而是：

1. Pi 在无用户来源环境发现并冻结一个真实缺失能力；
2. 能力可由 lock、digest、SBOM、扫描和测试复现；
3. 首次任务完成后生成只属于该用户的 SOP draft；
4. 第二次相似任务从 OCI digest 热复用，不重新搜索和下载；
5. 业务容器没有访问公共依赖站点，宿主机和 Mangrove 主环境没有被修改；
6. 用户可查看选择原因、来源、版本、权限和事件流并随时追问；
7. 平台共享只实现管理员审核/签名的最小发布门，不同时建设市场、评级或自动更新。

这样能直接验证用户最关心的“Pi 会自主找工具，并把成功经验变成下次可复用的模式”，同时把
复杂的供应链合规、分布式 Registry 和企业密钥基础设施留到真正需要时再建设。
