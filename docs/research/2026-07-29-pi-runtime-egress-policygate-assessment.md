# Pi Runtime Egress PolicyGate 开源方案评估

> 日期：2026-07-29
> 范围：Mangrove `pi-coding-agent` 任务级 Docker 的强制网络出口治理
> 资料边界：只使用项目官方文档、官方 GitHub、官方源代码和 Docker 官方文档
>
> 实施状态：业务执行主链已在 `v0.0.7` 接入；独立依赖获取状态机尚未实现

## 1. 结论

单一推荐：采用 **Stripe Smokescreen 作为任务级 Egress Proxy 内核**，Mangrove 只实现很薄的
策略适配、生命周期编排和审计入库，不自行实现 HTTP/HTTPS 代理。

选择它的主要原因是：

1. Smokescreen 本身就是为服务端出站治理设计的 HTTP/HTTPS CONNECT 代理，内建基于主机名的
   `enforce` allowlist，并在 DNS 解析后校验目标 IP；它默认拒绝非全局单播、回环、私网、
   CGNAT、IPv4 嵌入 IPv6 和代理自连接，比通用缓存代理更贴近 Agent 的 SSRF 风险。
2. 它提供结构化的准入决定字段、连接指标、限速和 CONNECT 并发限制，适合作为每个 Agent Run
   的可追溯证据。
3. 现有代码通过扩展 `Decider` 支持附加策略。Mangrove 若需严格限制目标端口，只需做策略适配，
   不需要重写代理、TLS 隧道、DNS 解析或连接跟踪。

但必须同时接受两个明确限制：

- **Smokescreen 官方仓库当前没有官方 Release，也没有官方容器镜像或 Dockerfile。**
  不能把 Docker Hub 上名称相似的第三方镜像冒充官方镜像。应固定官方源码提交，自行进行可复现
  的多阶段构建，再按内部镜像摘要部署。
- **默认 HTTPS CONNECT 只能控制目标主机、端口和解析后的 IP，不能读取 TLS 隧道中的 URL
  path、请求正文或上传文件。** 因此它是“目的地 PolicyGate”，不是内容 DLP。

## 2. 已验证事实

### 2.1 Stripe Smokescreen

- 官方 README 将 Smokescreen 定义为 HTTP CONNECT 代理，支持按客户端角色配置主机名
  allowlist，并会在域名解析后拒绝非公开可路由地址：
  [Smokescreen README](https://github.com/stripe/smokescreen#readme)。
- ACL 支持 `open`、`report`、`enforce` 三种策略；`enforce` 只允许列出的域名，支持
  `*.example.com` 形式的前缀通配：
  [官方示例 ACL](https://github.com/stripe/smokescreen/blob/master/pkg/smokescreen/acl/v1/testdata/sample_config.yaml)。
- 官方源代码明确处理非全局单播、回环、私网、CGNAT、NAT64、6to4、Teredo 和自连接，并将
  实际解析后的地址用于连接：
  [smokescreen.go](https://github.com/stripe/smokescreen/blob/master/pkg/smokescreen/smokescreen.go)。
- 审计字段包括请求主机、解析后远端地址、代理类型、角色、项目、准入原因、是否允许、连接耗时
  和 DNS 耗时；官方代码还定义了 `CANONICAL-PROXY-DECISION` 事件：
  [结构化日志字段](https://github.com/stripe/smokescreen/blob/master/pkg/smokescreen/smokescreen.go#L42-L65)。
- CLI 支持 IP/CIDR allow/deny、ACL 文件、Prometheus、限速、请求并发和 CONNECT 隧道并发：
  [CLI 选项](https://github.com/stripe/smokescreen#cli)。
- 当前官方 GitHub 的 Releases 列表为空：
  [GitHub Releases API](https://api.github.com/repos/stripe/smokescreen/releases)；仓库递归文件树
  中也没有 Dockerfile：
  [GitHub Tree API](https://api.github.com/repos/stripe/smokescreen/git/trees/master?recursive=1)。
- 本次评估快照的官方源码提交为
  [`da4840c9d8730fe74775573adb0b947ffe14732d`](https://github.com/stripe/smokescreen/commit/da4840c9d8730fe74775573adb0b947ffe14732d)。
  该提交还把原始 CONNECT 请求交给自定义 `Decider`，可用于实现 Mangrove 的端口和任务策略，
  但内建 ACL 本身仍主要按主机名判断。
- Smokescreen 已提供可选 MITM 域名配置和详细 HTTP 日志：
  [MITM ACL 示例](https://github.com/stripe/smokescreen/blob/master/pkg/smokescreen/acl/v1/testdata/mitm_config.yaml)。
  这不等于默认能够查看 HTTPS 正文，也不应在首期作为通用 DLP 使用。

### 2.2 Squid

- Squid 的 `dstdomain`、`dst`、`port`、`method` ACL 可以组合成域名、解析后 IP、目标端口和
  CONNECT 方法的默认拒绝策略：
  [Squid ACL 官方参考](https://www.squid-cache.org/Doc/config/acl/)。
- `http_access` 官方参考建议显式拒绝非安全端口、CONNECT 非 TLS 端口、localhost、
  link-local，并以 `deny all` 结束：
  [http_access](https://www.squid-cache.org/Doc/config/http_access/)。
- Squid 可以逐请求写访问日志并自定义日志格式：
  [access_log](https://www.squid-cache.org/Doc/config/access_log/)。
- Canonical 提供维护中的 `ubuntu/squid` Verified Publisher 镜像，并支持挂载
  `/etc/squid/squid.conf`：
  [Canonical ubuntu/squid](https://hub.docker.com/r/ubuntu/squid)。

Squid 的优势是镜像成熟、部署简单、端口 ACL 完整。缺点是它首先是通用缓存代理，私网/SSRF
边界需要 Mangrove 正确列全并长期维护；Smokescreen 则把“解析后只允许公开地址”作为核心安全
语义。对于允许 Agent 自主安装工具的场景，失败关闭的 SSRF 默认值比缓存能力更重要。

### 2.3 Envoy

- Envoy Dynamic Forward Proxy 能动态解析目标并提供 DNS 缓存、熔断、RBAC 和访问日志：
  [Dynamic Forward Proxy](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/dynamic_forward_proxy_filter.html)。
- 官方文档同时明确警告：不可信客户端可能访问代理自身的 localhost、link-local、云元数据或
  私网；必须另外使用网络防火墙、默认拒绝 RBAC 和容器/内核网络约束。
- Envoy 有持续维护的官方镜像和稳定发布：
  [Envoy Releases](https://github.com/envoyproxy/envoy/releases)。

Envoy 能力最广，但要得到 Smokescreen 已内建的语义，需要组合 Dynamic Forward Proxy、RBAC、
解析地址过滤、访问日志和网络规则。对当前单机 Docker Desktop 灰度而言，配置面和升级验证成本
显著更高，暂不作为首选。

## 3. 能力比较

| 能力 | Smokescreen | Squid | Envoy |
|---|---|---|---|
| 目标域名 allow/deny | 内建角色化 allowlist，适配度高 | `dstdomain`，成熟 | RBAC/路由可实现 |
| 目标端口 allow/deny | 内建粒度不足，宜用自定义 `Decider` 补齐 | `port` + CONNECT ACL，直接支持 | RBAC/匹配器可实现 |
| HTTPS CONNECT | 原生支持 | 原生支持 | 可配置 |
| 默认读取 HTTPS 正文 | 否 | 否 | 否 |
| 解析后私网/SSRF 阻断 | 内建且失败关闭 | 需要完整配置 `dst` 规则 | 官方要求额外 RBAC/防火墙 |
| 审计 | 结构化决定、指标、连接跟踪 | 成熟访问日志 | 强大的结构化日志和遥测 |
| 官方容器镜像 | **没有** | Canonical Verified Publisher | 有 |
| 当前集成复杂度 | 中 | 低 | 高 |
| 与 Agent 风险的匹配度 | 高 | 中 | 中 |

## 4. HTTPS 正文与内容外发边界

这是本方案最容易被误解的地方。

普通 HTTPS 代理流程是：

```text
Pi → CONNECT api.example.com:443 → Egress Proxy
   → Proxy 只决定是否建立 TCP 隧道
   → TLS 在 Pi 与 api.example.com 之间完成
```

代理可以可靠记录和控制：

- 请求建立隧道的目标主机和端口；
- DNS 解析得到的 IP；
- allow/deny 原因、连接时间、字节数和任务身份。

代理默认不能可靠读取：

- HTTPS URL 的 path 和 query；
- Header、请求正文、上传文件；
- 发送的数据是不是用户业务资料。

因此，即使只允许 `github.com`、`registry.npmjs.org` 或 `pypi.org`，恶意代码仍可能把少量资料
编码进允许域名的 URL、Header 或正文。**域名 allowlist 不能证明“业务内容没有外发”。**

Smokescreen 的可选 MITM 会要求向 Pi 容器植入代理 CA，可能破坏证书固定、Git/npm/pip 安装
及部分二进制工具；即使解密成功，也仍需另行开发内容分类、脱敏和误报处理。首期不应把 MITM
当成通用防泄漏方案。

推荐把策略分为两层：

1. **依赖获取阶段**：只允许到经过批准的 GitHub/npm/PyPI/apt 域名，不挂载用户来源文件，
   不注入任何 GitHub/npm/PyPI 写权限 Token。下载物落到任务缓存并记录来源、版本和哈希。
2. **业务执行阶段**：挂载只读来源文件，默认关闭公共外发，只允许本地模型和任务内部服务。
   如果目标确实要求访问外部业务站点，必须形成单独 `PolicyDecision`，明确目标域名、目的、
   数据范围和有效期后再临时开放。

这样既保留 Pi 使用成熟开源依赖的能力，也避免在同一时间把“用户原件”和“任意公共网络”同时
交给高权限 Agent。

## 5. 推荐集成草图

```text
                    外部 bridge（可访问互联网）
                              │
                       Smokescreen sidecar
                              │
                   任务级 internal bridge
                     ┌────────┴────────┐
                     │                 │
                Pi Runtime        本地模型 Relay
             只连接 internal       只转发固定 Qwen
                     │
        只读 input / 可写 work、output、session
```

执行约束：

1. 为每个 Run 创建独立 `--internal` 用户网络；Pi 只加入该网络。
2. Smokescreen sidecar 同时加入任务 internal 网络和普通外部 bridge；不发布宿主机端口。
3. Pi 的 `HTTP_PROXY`、`HTTPS_PROXY` 指向 sidecar；所有目标域名由 Smokescreen 解析，
   不能让 Pi 通过直连绕过代理。
4. 本地 Qwen 使用一个只允许固定上游的反向代理 Relay，不把私网整体加入 Smokescreen
   allow range。Relay 可复用成熟的 Nginx/Envoy，不自行实现转发器。
5. 限制 Pi 的直接 DNS 外发。仅设置代理环境变量不足以形成强制门禁，因为不遵守代理变量的
   程序仍可能直连或利用 DNS；验收必须包含直连 IP、外部 DNS、IPv6、私网、云元数据和
   `host.docker.internal` 绕过测试。
6. 为每个 Run 生成独立 ACL 和身份；`default.action` 固定为 `enforce`，禁止 `open` 和
   `report` 进入生产灰度。
7. Mangrove `PolicyGate` 只负责把已批准策略编译为 Smokescreen ACL/Decider 输入、启动和
   清理 sidecar、归档审计；正式交付仍由独立 Verifier 和 Delivery Publisher 控制。
8. 取消、超时或恢复失败时，先停止 Pi，再停止 sidecar/Relay，最后删除任务网络；网络和容器
   名称继续绑定 `user_id + task_id + revision + run_id`。

Docker 官方文档确认：容器可以同时连接多个网络，`--internal` 网络用于外部隔离；Docker
Compose 也支持 `internal: true`：
[Docker networking](https://docs.docker.com/engine/network/)、
[Compose networks](https://docs.docker.com/reference/compose-file/networks/)。

Windows Docker Desktop 运行 Linux 容器时，容器位于 Docker 管理的 Linux VM 网络中，网络由
Docker Desktop 后端转发；上述 bridge/internal 方案仍可使用：
[Docker Desktop networking](https://docs.docker.com/desktop/features/networking/)。
这只是本机功能可行性，不替代最终 Linux 服务器和并发容量验收。

## 6. 固定版本与供应链建议

Smokescreen 没有可直接固定的官方镜像标签，建议：

1. 首次 PoC 固定官方源码提交
   `da4840c9d8730fe74775573adb0b947ffe14732d`，不要使用 `master` 浮动构建。
2. 使用固定 Go 基础镜像和最小运行时镜像进行可复现构建；记录源码提交、Go 版本、模块校验、
   SBOM 和镜像 SHA-256。
3. 产物推入 Mangrove 自己的本地/私有镜像命名空间，例如
   `mangrove/smokescreen:<commit-short>`，生产配置最终固定镜像 digest，不只固定 tag。
4. 每次升级都重新执行 ACL、CONNECT、DNS rebinding、私网、IPv6、取消清理、审计完整性和
   依赖下载回归；不得自动跟随上游 HEAD。
5. 不使用来源不明的第三方 Smokescreen 镜像。若团队不能承担内部构建和安全更新责任，则应
   回退到 Canonical `ubuntu/squid:6.6-24.04_*@sha256:...`，但必须补齐并验证全部私网/SSRF
   ACL，不能只配置域名 allowlist。

## 7. 必须通过的验收

- 允许域名的 HTTP/HTTPS 依赖下载成功，未允许域名明确失败并产生审计事件。
- 直接访问公网 IP、RFC1918、loopback、link-local、CGNAT、IPv6 嵌入地址、云元数据地址失败。
- 不遵守 `HTTP_PROXY` 的程序不能直连；DNS 查询不能成为旁路。
- CONNECT 非批准端口失败；ACL 缺失、格式错误或 sidecar 未就绪时 Pi 失败关闭。
- Pi 只能访问当前 Run 的代理和工作区，不能访问其他任务/用户的 sidecar。
- 取消、超时和进程重启不会留下 Pi、代理、Relay 或任务网络。
- 审计至少保留 `user_id`、task/revision/run、请求主机、端口、解析 IP、准入结果、原因、
  时间、策略版本和幂等键；不记录原始业务正文。
- 依赖获取阶段看不到用户来源；业务执行阶段默认没有公共外发。
- 用户批准一次外发只对指定 Run、域名和有效期生效，不升级为全局白名单。

## 8. 尚未验证和实施风险

- 2026-07-29 后记：已在当前 Docker Desktop 从固定官方源码提交构建 Smokescreen，
  并完成允许 npm、拒绝未批准域名、拒绝云元数据、阻断直连旁路、仅放行固定本地模型、
  结构化日志和清理的组合验证。镜像身份和证据见
  [PG-05 真实取消与 Egress 纵切面报告](../plans/2026-07-29-agentic-runtime-vnext-pg05-live-cancel-egress-slice-report.md)。
  随后的主链接入已把业务执行阶段接入 `PiRuntime.start/resume/cancel`，并通过真实
  Pi + 本地 Qwen + 候选 Verifier 回放；独立依赖获取状态机仍未实施。
- Smokescreen 没有官方发布节奏和官方镜像，Mangrove 必须承担构建、漏洞跟踪和升级回归。
- 目标端口策略需要通过 Smokescreen 自定义 `Decider` 或等价的强制层补齐；只生成 ACL 文件
  不足以宣称端口门禁完成。
- 常见依赖管理器会访问 CDN、对象存储、镜像源和重定向域名，实际 allowlist 必须从真实下载
  审计中建立，不能预先声称已经完整。
- `npm install` 生命周期脚本和 Python 构建后端会执行第三方代码；Egress Proxy 只能限制网络
  目的地，不能证明依赖本身安全。
- MITM、内容 DLP、最终服务器部署、并发容量和 Linux/GPU 验收不在本工作包内。
