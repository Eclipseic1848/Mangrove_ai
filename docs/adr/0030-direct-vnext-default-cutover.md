# ADR-0030：合格后直接切换全用户 vNext 默认

- 状态：`accepted`，用户于 2026-08-23 明确确认
- 日期：2026-08-23
- 部分取代：[ADR-0019](0019-vnext-delivery-and-default-cutover-state-machine.md) 第 5 项

平台不再建立普通用户 `explicit_opt_in` 资格系统。生产保持 `admin_gray`，只有当前
GateSnapshot 累计硬门合格且默认切换获得独立授权后，才允许直接进入 `vnext_default`；该
模式让所有普通用户的新任务默认使用 vNext，仍允许显式选择 Legacy。历史数据库中的
`explicit_opt_in` 只为兼容读取而保留，新任务一律失败关闭到 Legacy，并且只能经合格门和
独立授权恢复 `admin_gray`，不能继续扩大受众。

这一决定避免为短暂中间阶段引入独立用户资格、授权、撤销和审计系统，同时保留
GateSnapshot、P0 自动回退、历史 RuntimeAssignment 不改写及默认切换单独授权等安全边界。
