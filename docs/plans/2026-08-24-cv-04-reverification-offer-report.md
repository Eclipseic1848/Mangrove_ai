# CV-04 同 Run 重验资格 Offer 工程验证报告

> 状态：ENGINEERING_VERIFIED
>
> 日期：2026-08-24
>
> 工单：GitHub #64
>
> 固定审查点：`51d327d54aa298ab734f30f106f5405bb12619de`
>
> 用户确认：已确认 CV-04 三类测试接缝；尚未接受本工程产物

## 1. 完成范围

- `CandidateVerificationService.inspect_reverification` 以只读查询返回冻结
  `ReverificationOffer`；客户端不能自行声明 `ruleset_changed`。
- `failed + proven ruleset_changed` 与既有 `semantic_inconclusive` 可进入 Offer；同规则失败、
  `legacy_unversioned`、活动 Attempt、`outcome_unknown`、P0 和已有 Delivery 均失败关闭。
- 重新核对 Candidate 文件大小/SHA、CandidateSet 投影、Manifest 字节与内部文件/格式/来源引用、
  来源文件、GoalContract 和 DeliverySpec；漂移只返回稳定产品 blocker，不泄露内部路径。
- 组合根 Authority 使用 SQLite `mode=ro` 复核 TaskRevision、来源绑定和完整
  RuntimeAssignment；Provider 同样以只读 SQL 复算 Owner 可用连接版本和精确模型状态，不初始化
  Broker、Vault 或 Repository，不签发 Grant。
- 新旧 `formal_delivery_runs` / `semantic_delivery_runs` 任一路径存在正式 Delivery 都阻断重验。
- Task detail 增加 latest Attempt、Offer、规则变化说明、Provider/本地外发摘要和
  `awaiting_publication`；跨 Owner 固定返回 404 且零 Candidate 内容泄露。
- `ReverificationBlocker` 使用领域枚举；普通投影不返回 Secret、Base URL、Prompt、宿主路径或
  内部规则文件列表。

## 2. 变更边界

主要实现和回归文件：

- `src/candidate_verification/models.py`
- `src/candidate_verification/service.py`
- `src/candidate_verification/repository.py`
- `src/candidate_verification/ruleset.py`
- `src/candidate_verification/__init__.py`
- `src/api/semantic_workspace_runtime.py`
- `src/api/routes/semantic_workspace.py`
- `tests/test_candidate_reverification_offer.py`
- `tests/test_pi_runtime_workspace_api.py`

没有增加 Python/npm 依赖，没有实现 CV-05 的完整重验写动作，没有修改前端、Publisher、真实数据
库或 G1 评测文件。

## 3. 验证证据

使用仓库既有项目 Python 解释器并显式启用 UTF-8；系统 Python 3.14 未安装 pytest，因此未用于
工程门，也没有安装任何依赖。

| 验证集合 | 结果 |
|---|---:|
| CandidateVerification 与 Pi 工作台六文件相邻回归 | 87 passed |
| 冷态 Provider Authority：默认 Broker 未初始化、数据库逻辑指纹零变化 | passed |
| Python 语法编译 | exit 0 |
| `git diff --check` | exit 0，仅既有 Windows 换行提示 |
| 当前 VerifierRuleset 目标身份现场解析 | exit 0 |

第三轮双轴复审结论：Standards 与 Spec 均无剩余 P1/P2 阻断问题。非阻断封装债务有两处：
只读 Provider 版本算法与 Model Connections 既有算法重复；Runtime request/candidates 重开与语义
重试路径同形。为避免扩大 CV-04 范围，本工单不顺手重构，后续触碰对应 Seam 时再收口。

测试环境仍报告既有 `requests` 依赖版本、`pynvml` 及 TestClient/httpx 弃用告警；本工单未新增
或升级依赖。

## 4. 未执行与人工门

- 未迁移真实 `data/webui.db`，未调用真实 Provider，未重验真实 Candidate，未发布 Delivery。
- 未创建分支、提交、推送、PR、标签或 Release；GitHub #64 未评论、改标签或关闭。
- 工程验证不等于用户验收、生产资格或 Git 授权。
- 用户接受 CV-04 后，下一阶段只能先展示 CV-05 的 TDD 接缝并等待确认；不得从本报告推断已
  授权实施 CV-05、生产迁移、外发或发布。
