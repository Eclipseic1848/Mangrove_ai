# AC07-12（#17）AC-06 兼容切换与 AC-07 综合验收门 — 执行报告

> 状态：阶段 1-4 已真实完成；阶段 5（用户 8088 最终灰度验收）待用户执行
>
> 依据：`Eclipseic1848/Mangrove_ai#17`（AC07-12）；前置 #15/#16 两条真实纵切面
>
> 证据位置：生产库 `data/webui.db`、`scripts/ac07_12_migration_drive.py`、e2e 套件

## 1. AC1-AC7 逐条对照

### AC1：切换前验证两项冻结 Pack 均具有通过的 AC-07 治理投影、供应链证据、平台签名和 admin_gray 受众 ✅

| 冻结 Pack | 投影 | 供应链 | 签名 | 发布 |
| --- | --- | --- | --- | --- |
| gray-python-table@2.0.0 | verified/revoked/eligible + admin_gray | passed | 有 | 有 |
| gray-python-table@3.0.0 | verified/active/eligible + admin_gray | passed | 有 | 有 |
| gray-everything-mcp@2026.7.4 | verified/active/eligible + admin_gray | passed | 有 | 有 |

（2026-08-19 预检现场核验，逐项查库确认）

### AC2：生产库副本执行备份、前向迁移、重复迁移、旧数据零改写和恢复演练 ✅

演练脚本 `scripts/ac07_12_migration_drive.py`（全程副本，不触碰生产库）：

| 步骤 | 结果 |
| --- | --- |
| 副本 | 27.9MB（生产库在线复制） |
| 迁移前指纹基线 | 5 张治理表（11 目录行/44 事件/17 验证/16 供应链/6 平台验证）+ 内容 sha256 |
| 前向迁移 | `migrate_capability_governance`（一致性备份 + 纯新增 DDL） |
| 重复迁移 | 幂等：返回同一恢复点，不覆盖 |
| 旧数据零改写 | 全部治理表行数与内容指纹一致 |
| 恢复演练 | 恢复点备份还原后指纹与迁移前完全一致 |

### AC3：Legacy/AC-06 兼容开关只在新路径满足门禁后切换；失败可恢复 ✅

| 要求 | 证据 |
| --- | --- |
| 门禁满足后切换 | #15/#16 真实发布链完整走通后执行；`_AC06_ADMIN_GRAY_VALIDATION_TARGETS` 白名单 + `is_ac06_admin_gray_validation_target` 退役；`_can_validate_pack`/`task_replay` 的 ac06 分支移除（验证只接受本人个人能力） |
| 切换后核验 | admin 对 python-table@1.0.0、everything-mcp@2026.7.4（平台行）验证 options 为空（被拒）；个人包验证路径保留（投影 verified/active/eligible） |
| 可恢复 | 改动在 `codex/ac07-12-compat-switch` 分支（git 回滚）；白名单代码保留于 git 历史；迁移演练验证了恢复点还原路径 |

### AC4：完整回归覆盖 Catalog、Acquisition、Adapter、Host、Pi Runtime、工作台、设置权限、任务取消/恢复和正式 Delivery ✅

| 子系统 | 结果 |
| --- | --- |
| Catalog/Acquisition/Adapter/Host | ✅（test_capability_catalog/acquisition/adapters/host） |
| Pi Runtime/Agentic | ✅（test_agentic_runtime） |
| 工作台/设置权限 | ✅（test_semantic_workspace_api / settings_permissions） |
| 取消/恢复 | ✅（test_replay_guard / runtime_gate_selection / runtime_gate_supervision） |
| 正式 Delivery | ✅（test_semantic_delivery / document_delivery / vnext_delivery_publisher） |
| 治理全套 | ✅（governance/audit/promotion/platform_publish/commands/rescan/seam/governance_api） |
| **合计** | **419 passed**；1 项环境性失败（`test_stop_helper_preserves_unrelated_port_listener`，Windows 停启脚本边界；git stash 验证与 #17 无关，预先存在） |

### AC5：浏览器验收覆盖管理员治理、普通用户不可见、审计查看、渐进披露、键盘、深浅主题和 reduced-motion ✅

Playwright e2e `settings-role-access.spec.ts` **16 项全部通过**（39.8s）：

- 普通用户只看到个人范围（不可见）✅
- 管理员能力卡片验证 + 渐进步骤缺口 ✅
- 草稿能力卡片脱敏缺口 + 自动晋级提示 ✅
- 普通用户只为自己的能力读取供应链证据 ✅
- 管理员同时拥有个人设置和平台治理入口 ✅
- 平台连接对话框键盘进入、Esc 关闭、焦点归还 ✅
- 三角色明暗主题设置主视图无自动化可访问性违规（axe，含 reduced-motion 规则）✅
- 管理员审核视图分组渐进披露并完成一次审计查看 ✅
- **管理员提交平台候选并发布到管理员灰度**（真实发布路径）✅

### AC6：无能力任务、历史冻结任务和现有正常任务的功能、结果与资源清理不退化 ✅

- 无能力任务：`test_document_capability_is_explicitly_registered` 等（能力显式性测试通过）
- 历史冻结任务：`test_replay_guard`（重放守卫）、`test_runtime_gate_supervision`（冻结装载监督）通过
- 现有正常任务：Pi Runtime/工作台/Delivery 全套通过（见 AC4）
- 资源清理：Host/清理测试通过（`test_capability_host` 8 项含强制删除失败保留证据）

### AC7：最终报告明确工程验证、用户验收、未开放普通用户、未完成 AC-08/AC-09/8B 等边界；由用户在 8088 执行最终灰度验收 ✅（报告完成，用户验收待执行）

边界声明：

- **未开放普通用户**：平台能力受众固定 admin_gray；普通用户目录可见但装载被拒（#15/#16 真实拒绝矩阵）
- **未完成**：AC-08（普通用户开放决策）、AC-09（远程 MCP/Registry 自动发现）、8B Linux/Compose/并发/故障与目标服务器验证、完整 PG-05、P0 GateSnapshot 与默认入口切换
- **用户验收**：由用户在 8088 执行最终灰度验收（见下）

## 2. 阶段执行摘要

| 阶段 | 内容 | 结果 |
| --- | --- | --- |
| 1 | 迁移演练（AC2） | ✅ 副本/前向/重放/零改写/恢复 全通过 |
| 2 | 兼容开关切换（AC3） | ✅ 白名单退役 + 测试更新 + 切换核验 |
| 3 | 完整回归（AC4/AC6） | ✅ 419 passed（1 环境性失败，与 #17 无关） |
| 4 | 浏览器验收（AC5） | ✅ 16 项全部通过 |
| 5 | 最终报告（AC7） | ✅ 本报告；用户 8088 最终灰度验收待执行 |

## 3. 代码改动

- `src/capability_governance/models.py`：AC-06 白名单 + `is_ac06_admin_gray_validation_target` 退役
- `src/capability_governance/service.py`：`_can_validate_pack` 移除 ac06 分支（只验证本人个人能力）
- `src/capability_governance/task_replay.py`：`resolve`/`list_options` 移除 ac06 白名单路径
- `tests/test_capability_governance.py`：`test_ac06_admin_gray_platform_pack_validation_bridge_retired`（退役锚点）
- `scripts/ac07_12_migration_drive.py`：迁移演练脚本

## 4. 用户 8088 最终灰度验收（AC7 最后一步）

在浏览器打开 `http://localhost:8088`（或前端入口），按以下清单核验：

1. 管理员登录 → 能力治理入口可见 → python-table@3.0.0 / everything-mcp@2026.7.4 平台投影 verified/active/eligible
2. 管理员对平台能力发起真实任务 → 装载通过（admin_gray 受众）
3. 普通用户（liyi111）登录 → 能力不可用（装载被拒）
4. 审计查看：管理员可查看验证证据绑定内容
5. 无能力任务 / 历史冻结任务：功能与结果正常
