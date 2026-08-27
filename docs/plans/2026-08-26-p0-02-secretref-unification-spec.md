# P0-02 配置中心 SecretRef 统一规格

> 状态：`ENGINEERING_REVIEWED_PENDING_PRODUCTION_COPY`
>
> GitHub：#57
>
> 依赖：#56 的中央迁移体系与生产副本门已通过

## 1. 目标与边界

配置中心与模型连接使用同一个 `FernetCredentialVault` 主密钥边界，但使用独立业务密文表。
`runtime_config.value` 对 25 个 `secret=True` 键只能保存不透明
`secretref:runtime-config:<uuid>`；原值仅存在于 Vault 密文、受信进程内存和调用所需的短暂
上下文中。

本工单不轮换或销毁生产 Secret/Vault Key，不迁移生产原库，不调用 Provider，不改变
`.env` 兜底语义，也不把模型连接的删除、Grant 或轮换生命周期套到配置 Secret 上。

## 2. 已盘点 Secret

| 领域 | 键 | 读取路径 |
|---|---|---|
| 模型 | `deepseek_api_key`、`qwen_api_key` | Provider profile、用户任务覆盖、旧连接显式导入 |
| 搜索/采集 | `tavily_api_key`、`anysearch_api_key`、`firecrawl_api_key` | Search/AnySearch/Firecrawl Collector |
| Cookie | `mc_cookie_*` 七项、`jd_cookie`、`tb_cookie`、`pdd_cookie` | MediaCrawler、电商采集、Cookie 健康检查 |
| 通知 | `smtp_password`、`slack_webhook_url` | EmailSender、SlackSender、配置连通验证 |
| 语义 | `embedding_api_key`、`rerank_api_key` | Embedding/Rerank 调用 |
| 代理 | `mc_static_proxy_url`、`mc_kdl_secret_id`、`mc_kdl_signature`、`mc_kdl_user_pwd`、`mc_wandou_app_key` | Collector 网络与 MediaCrawler 子进程环境 |
| 数据库 | `mysql_password` | MySQL Writer 与配置连通验证 |

写入者只有管理员全局配置 API、当前用户自助配置 API、启动时显式迁移；读取者仍通过
`WebUIStore.config_all(scope)` 的受信内部接口取得解密值。前端、审计和诊断只能得到掩码或
脱敏文本。

## 3. 数据模型与权限

- `runtime_config(scope,key,value,...)`：业务元数据；Secret 的 `value` 只能是 opaque ref。
- `runtime_config_secrets(secret_id,owner_scope,config_key,ciphertext,created_at)`：独立密文表。
- SecretRef 同时绑定 `owner_scope + config_key`；不存在、坏格式、跨 Owner、跨键复用和坏密文
  对外统一为无法解析，不提供枚举旁路。
- `global` 只能由管理员/超级管理员 API 修改；个人项只能由当前登录用户修改自己的 scope。
- 模型连接仍使用 `model_connection_secrets`；两表只共享 key，不共享删除或轮换行语义。

## 4. 显式迁移

`webui_0004` 在 #56 的单一 Alembic 事务中：

1. 建立并核验完整 `runtime_config_secrets` Schema；
2. 启用 SQLite `secure_delete`，降低自由页保留旧值风险；
3. 逐条加密旧明文并以 Owner/键绑定的 SecretRef 原子替换；
4. 已是 SecretRef 的值必须可由同一 Vault 解密且身份一致；
5. 拒绝孤儿密文、未知 ref、坏 key/密文、并发改写或部分 Schema；
6. 任一步失败整批回滚，恢复点由 #56 中央迁移体系保留。

受控扫描命令：

```powershell
python -m scripts.scan_runtime_config_secrets `
  --database <迁移后副本> `
  --artifact <迁移后副本> `
  --artifact <新备份> `
  --artifact <显式 WAL 或 journal>
```

输出只包含制品文件名和命中数量；不输出绝对路径、Secret、密文或 SecretRef。

## 5. 完成门

- 25 项 Registry 与迁移冻结集合完全一致；
- 新增、更新、删除、跨 Owner、未知 ref、坏 Vault 均有公共接缝回归；
- `webui_0003 → 0004`、长值安全删除、中断回滚、迁移副本/新备份扫描通过；
- Provider 旧配置导入、SMTP/Slack、MySQL/数据库连接和用户任务覆盖回归通过；
- 当前生产库只在单独授权下做副本演练；生产原库迁移、旧备份处置、Key/Secret 轮换或销毁
  均为独立人工门。
