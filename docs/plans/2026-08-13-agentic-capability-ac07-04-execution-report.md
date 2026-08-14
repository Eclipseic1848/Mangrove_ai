# AC-07-04 Cosign 本地 OCI 签名路径 PoC 执行报告

> 工单：`Eclipseic1848/Mangrove_ai#9`
>
> 日期：2026-08-13
>
> 状态：工程 PoC、真实双包验证、最终双轴复审与用户验收通过；未进行能力晋级或平台发布

## 1. 范围

本 PoC 只验证以下窄路径：

```text
冻结 OCI Layout
  → 127.0.0.1 临时 Zot Registry
  → Cosign 按主体 digest 生成标准 OCI image signature
  → ORAS 递归复制主体和 Referrers
  → 独立 OCI Layout
  → 重新上传后由 Cosign 使用公钥验证
```

本次没有增加 API、数据库、前端、能力晋级、平台发布或受众变更。
`sign-blob` 没有被当作 OCI image signature 的替代实现。

## 2. 已验证事实

### 2.1 锁定工具链

| 工具 | 冻结版本 | 来源验证 | 运行内容锁 |
| --- | --- | --- | --- |
| Cosign | 3.0.6 | GitHub Release 资产 digest | `sha256:9b85a88ebff2d9dd30ff4984a6f61f2cedc232dd87d81fa7f2ff3c0ed96c241c` |
| ORAS | 1.3.2 | 官方 checksums 与 ZIP 的 OpenPGP 分离签名，指纹 `2DA461D13B0C27845EDFA77FE462A3894CBAAA47` | `oras.exe`：`sha256:1fd2a8672c9a6e5aade53380dd405781271e802529edef6e8d9509d508b8482b` |
| Zot | 2.1.20 | 官方 GHCR、固定 manifest digest、自报 release tag 与 commit | `sha256:95a837a0afacf5b7edc0c92493f04beee6891989b8d2fd50a00cf65a1e6d4fd5` |

Zot 容器自报提交为 `3b5796d834e8661ea661a5fcc47add8d4405aebf`，与
GitHub `v2.1.20` tag commit 一致。工具锁由
`config/supply-chain-tools.lock.json` 失败关闭校验版本、来源状态和本机内容哈希。

### 2.2 密钥与口令边界

- PoC 私钥由 Cosign 3.0.6 生成，格式为加密 Sigstore 私钥；
- 私钥和公钥位于项目目录之外，没有写入 Git、数据库、任务目录、日志或容器参数；
- 私钥口令只在验收进程内存和子进程环境变量中存在，没有进入 argv 或持久化证据；
- 可持久化证据只含主体 digest、签名 digest、公钥 SHA-256 和 Referrer digest。

### 2.3 真实双包结果

| 主体 | 冻结主体 digest | 签名 Referrer digest | 结果 |
| --- | --- | --- | --- |
| `gray-python-table:1.0.0` | `sha256:2a430aa8e714d318cdc1ba6ddc6363b1ae0e49212c2a207970153dda03acd902` | `sha256:6fc856de65186fff1cfc842ae3d0774e733aedb83041e11ecad2b3f592fb2195` | 通过 |
| `gray-everything-mcp:2026.7.4` | `sha256:dce5be51c949cfe03b10ace23efe92ce192329d58ee9bf45ef2c0a89ed4cd8a8` | `sha256:d8b4210a0d02ea3a16caa78a878e2cfdec68d1cf8a8fdcbfaf6a5c8d0f37ba6e` | 通过 |

两个签名使用同一公钥身份：
`sha256:1b5cb9f335678137049b89484a7c3a6a73e8f48e862e04d286885921d36f7109`。
Cosign 3.0.6 生成的签名 Referrer artifact type 为
`application/vnd.dev.sigstore.bundle.v0.3+json`。

### 2.4 失败关闭与恢复

真实验收验证了：

- 首次签名在写入 `passed` 证据前立即重开独立输出 Layout，密码学重验通过；
- 相同事务重跑不会重新签名，证据和 Layout 重验一致；
- 错误公钥被拒绝；
- 修改主体 manifest blob 后被拒绝；
- 预启动取消不创建 Registry；
- 完整事务在独立 Layout 重验的真实 ORAS 递归复制期间收到取消信号时，会终止子进程并清理
  只读 blob、Registry、存储和半成品 Layout；
- 独立子进程创建 Registry 后由 `os._exit` 异常退出，新 Runtime 能按唯一事务标签删除遗留
  Registry 和存储；
- 验收结束后，临时容器、事务存储和工作目录均无残留；Runtime 未创建命名网络。

## 3. 实现产物

- `src/capability_governance/oci_signing.py`：锁定工具链、签名事务和 CLI Runtime；
- `src/capability_governance/tool_lock.py`：供应链扫描与签名共用的版本、来源和内容锁校验；
- `tests/test_capability_signing.py`：事务、工具锁、回环绑定、口令隔离、独立 Layout 重验和清理测试；
- `scripts/verify_capability_signing_ac07.py`：真实双包验收入口；
- `config/supply-chain-tools.lock.json`：Cosign、ORAS 与 Zot 冻结记录。

## 4. 验证证据

执行：

```powershell
python -m pytest tests/test_capability_signing.py -q
python scripts/verify_capability_signing_ac07.py
```

结果：签名单元测试 `15 passed`；八个 Capability 回归文件 `133 passed`；真实双包验收返回
`status=passed`，预启动取消、真实完整事务重验期间取消、崩溃恢复、错误公钥、主体篡改、幂等和
零残留全部通过。

## 5. 基于代码的推断

该 Runtime 已证明 Windows 本机可以用标准 OCI Referrers 完成离线私钥签名事务，并为后续
个人版本晋级和平台快照提供可复用的窄签名边界。它尚未接入治理状态机，因此不能据此推断任何
能力已经晋级为 `verified` 或发布为 `admin_gray`。

## 6. 用户验收

用户于 2026-08-13 明确授权完成 #9 验收。最终真实验收再次执行双包签名、独立 Layout 重开、
错误公钥、主体篡改、预启动取消、ORAS 重验期间取消、进程崩溃恢复、幂等和零残留检查，结果
全部通过。该验收只确认本地标准 OCI image signature PoC 可采用，不授权能力晋级、平台发布、
普通用户开放或沿用临时 PoC 私钥。

## 7. 尚未验证的后续建议

1. 后续新仓库 #10 只消费已冻结的签名证据契约，不在晋级服务中复制 CLI 编排；
2. 正式平台签名密钥的保管、轮换、吊销和恢复属于独立安全决策，不沿用本 PoC 临时密钥方案。
