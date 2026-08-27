# P0-02 配置中心 SecretRef 统一实现报告

> 状态：`ENGINEERING_AND_PRODUCTION_COPY_VERIFIED`
>
> GitHub：#57
>
> 日期：2026-08-26

## 1. 已实现

- 冻结 25 个 `runtime_config.REGISTRY secret=True` 键，测试保证 Registry 与迁移集合不漂移。
- 新增独立 `runtime_config_secrets` 密文表；配置 Secret 与模型连接共享
  `webui.db.model-connections.key` 和 `FernetCredentialVault`，不共享 Provider 密文表、删除或
  Grant 生命周期。
- `runtime_config.value` 对 Secret 只保存 `secretref:runtime-config:<uuid>`；解析同时绑定
  `owner_scope + config_key`。
- WebUIStore 新增、更新、删除在单一事务内替换 ref/密文并清理旧密文；旧明文、坏 ref、跨
  Owner/跨键、未知 ref、坏密文、缺失或损坏 key 均失败关闭。
- 缺失 key 时只有数据库内完全不存在任何模型/配置密文才允许创建首个共享 key；已有密文时
  不会静默生成替代 key。
- `webui_0004` 经 #56 中央迁移体系把旧明文原子迁为 SecretRef，启用 `secure_delete`，校验
  部分 Schema、孤儿密文、现有 ref、Vault、并发和中断；不支持隐式降级回明文。
- `webui_0004` 自包含冻结 25 项、SecretRef 格式、keyring-v1 读取和所需 Fernet 语义，不导入
  未纳入 revision 摘要的可变项目运行时代码；内容摘要由中央 manifest 强制校验。
- 更新和删除在任何写入前先验证旧 Ref 的格式、Owner、配置键、Vault 和密文可解性；跨键、
  未知 Ref、坏密文和缺 key 均回滚并保留原状态。
- 配置 API 继续只返回尾四位掩码；连通验证的 HTTPException、普通异常和 Cookie 健康诊断在
  返回/持久化前移除当前全局及个人 Secret；后台总异常日志不携带原始 traceback。
- 新增只读扫描器 `scripts/scan_runtime_config_secrets.py`，按显式制品清单扫描数据库、备份、
  WAL/journal；正式入口为 `python -m scripts.scan_runtime_config_secrets`，输出仅含文件名和
  命中数量。

## 2. TDD 证据

红灯首先证明：缺少 SecretRef 模块；随后证明旧 Schema 没有 `runtime_config_secrets`；API 诊断
会回显合成 Secret。每个红灯均由对应最小纵切面转绿。

生产副本门前的最终聚焦回归：

```text
17 passed, 62 deselected, 4 warnings in 13.30s
```

该最窄回归在终审修复后覆盖 revision 自包含、更新/删除坏状态矩阵、后台持久化与日志脱敏及
revision manifest。最终完整核心回归为：

```text
2165 passed, 7 skipped, 3 deselected, 8 warnings in 762.55s
```

三个 deselect 分别是已在干净 dev 环境单独通过的依赖漂移契约、等待 Git 提交授权的
VerifierRuleset 身份绑定门，以及按规则不得改写的独立 G1 冻结集。

覆盖：

- 17 条 SecretRef/迁移专属测试：数据库无明文、更新/删除无孤儿、Owner/键绑定、未知 ref、
  坏/缺 key、已有模型密文时禁止替代 key、Registry 25 项同步、共享 Vault、旧明文迁移、
  长值 secure_delete、坏 Vault/第二条写入中断整批回滚、只读备份扫描、API/诊断脱敏；
- #56 中央迁移与组件接管完整回归；
- 配置权限、用户模型选择、完整模型连接/Provider 旧配置导入；
- SMTP、Slack、Cookie/采集健康、MySQL/数据库连接、调度器和数据任务读取路径。

附加验证：相关 Python 文件 `py_compile` 通过；相关 `git diff --check` 通过。测试警告均为既有
依赖的 deprecation/版本提示，不是本实现失败。

## 3. 已验证事实与限制

已验证事实：

- 合成旧库可从 `webui_0003` 迁到 `webui_0004`；迁移后数据库和新备份字节不含合成旧明文，
  迁移前备份被扫描器准确检出。
- 同一既有 key 能同时解密模型连接密文和迁移后的配置密文。
- 失败事务保留 `webui_0003` 与全部旧值，不留下已提交的第一条迁移或密文行。
- 经用户授权，生产 `webui.db` 与匹配 Vault key 已复制到仅当前 Windows 用户可访问的临时
  目录；原库连续静止且 8088 无监听，生产原库与 key 均未改写。
- 副本源 SHA-256 为
  `6f83a3fdc73e6d7c2b5fc8578d36f642852d200ab6eeab6c86e52e5aa613ef3a`；中央迁移从 legacy
  依次应用 `webui_0001`～`webui_0004`，最终 `state=current`、无 pending 或 gap。
- 迁移恢复点 SHA-256 为
  `3133e3db82f20699b7554d8916a065260e643e0224190e048f2ce9d6858302bc`；恢复验证为
  `integrity_check=ok`、外键违规 0、Schema 为 legacy，并从该恢复点完整重放到
  `webui_0004`。
- 生产副本实际有 1 条已配置 Secret；迁移后为 1 个 opaque ref 和 1 条密文，孤儿密文 0，
  受信 `WebUIStore` 读取通过且未输出 Secret、密文、Ref 或 Owner。
- 迁移后副本、迁移后新备份和重放副本的明文命中均为 0。迁移前恢复点按预期检出 1 项旧
  明文；它只在受限临时目录内用于恢复/重放。完成证据后已按本次授权删除整个受限目录：
  共 11 个临时数据库、key、恢复点、收据与锁文件（145,882,653 bytes），现场确认目录不再
  存在；该删除不可恢复，生产原库与原 key 仍存在。
- 演练前后均不存在 `webui.db-wal`、`webui.db-journal` 或 `webui.db-shm`，因此没有可扫描的
  SQLite sidecar；未把“不存在”冒充扫描通过。

仍未执行：

- 未迁移生产原库，未调用外部连接，未轮换/销毁任何生产 Secret 或 Vault Key；
- 独立 Standards/Spec 双轴终审均为 0 P1 / 0 P2；生产副本门通过不等于产品/Owner 验收；
- 未提交、推送、写 GitHub 或发布。

## 4. 剩余门禁

1. 生产原库迁移、既有生产备份轮换/销毁、Secret/Key 轮换仍需用户独立确认；
2. 工程门与副本门通过不代表生产迁移、用户验收或版本发布；GitHub 收口按本会话已获授权
   的精确文件白名单执行。
