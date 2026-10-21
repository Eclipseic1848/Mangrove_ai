# ADR-0050：masked 单元的受保护原身份窗口

状态：accepted

日期：2026-10-20

关联：[#244](https://github.com/Eclipseic1848/Mangrove_ai/issues/244)、[ADR-0049](0049-historical-unknown-run-seals.md)

## 背景

隔离且从未启动的单元实测表明：systemd 有效 masked 后，`User` 与 `WorkingDirectory`
可能都明确返回空字符串。原封存门因此正确阻断；直接接受维护账号填写的旧身份会丢失
停止事实的可信来源。用户已批准只替换这一身份来源，逐 Run 原始停止证据与实时停机门保持。

## 决策

1. 仅两个键均存在且值为空字符串时，允许使用固定 root 工具现场采集的 mask 前身份。
   缺键、半空、非空冲突及仅磁盘 masked 而 manager 仍 loaded 均拒绝。旧非空正确身份路径保留。
2. 采集器必须以系统 Python 的隔离模式运行，原件及祖先由 root 保护，安装原件 SHA-256
   与审核源码一致。root 批准清单只约束 service、真实根、候选根、数据库、审批和精确目标，
   不接受调用方自报的 PID、UID、boot 或配置身份。普通 JSON 不能作为可信回执。
3. capture 从 live loaded 单元、`/proc`、原 unit 及全部 drop-in 采集身份，生成随机 nonce
   和 root 私有回执。capture 只确认身份，不能返回已停止。verify 只读，既不 mask/stop，
   也不迁移、移动配置、封存或恢复接单；不增加 sudoers 授权。
4. 每次核验要求同 boot、有效 masked、PID0、inactive/dead、无待重读配置、root 所有的
   精确 `/dev/null` mask、旧 PID 已消失、原源码根无同账号 Python 旁实例。无法确认即拒绝，
   对自身 PID 不设豁免；维护 CLI 必须在审核候选根且在原应用根之外。
5. 原根、数据库及配置身份绑定回执。数据库只绑定 canonical path、dev、ino、uid、mode，
   合法原地迁移和封存不使窗口失效；替换 inode 拒绝。若配置须移开才能 mask，只接受
   清单明确批准且同文件系统保全原 inode、字节及权限的映射；工具自身不执行移动。
6. 窗口同 boot，UTC 与 monotonic 均最多 600 秒。首次写入在事务前和 INSERT 前两次
   核验，最终以同一 `sealed_at` 再核停止证明和窗口期限，跨期零写入。
7. 主停止证明可增加六字段 `window_ref`：version、nonce、receipt_sha256、manifest_sha256、
   approval_reference、captured_at。首次使用窗口时精确匹配当前受保护引用，原三份逐 Run
   证据、request_id 和原摘要算法保持；不新增迁移，不改旧 proof 或原未知事实。
8. 历史验证自包含，不读取当前 root 文件、不调用 sudo、不因窗口后来过期失效。精确重放
   保留旧 proof/ref/request_sha，只返回原审计；当前新窗口仅满足入口维护门，不续期旧记录。

## 后果与验收边界

工具安装、root 批准清单、真实 capture、现役服务停止/mask、原库迁移与封存均须先批准
具体生产方案。本决策只授权本地实现、合成回归、双轴审查和 CI，不表示已完成生产隔离、
逐 Run 停止证明、服务器更新或三端一致验收。窗口到期不能自动 unmask/start 来刷新。
