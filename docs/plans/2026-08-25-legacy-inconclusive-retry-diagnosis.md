# 历史 inconclusive Candidate 重试阻断诊断

> 状态：`DIAGNOSIS_ACCEPTED_SPEC_DRAFTED`
>
> 日期：2026-08-25
>
> 范围：只读诊断；未修改任务、Attempt、Provider、文件或代码

## 1. 用户症状

数据工作台任务“请获取这份文档里所有的技术指标要求，然后输出一个CSV表格”显示候选文件、
数量和 88 条来源证据均通过，但停在“候选待验证”，点击“重新验证候选”后仍没有形成正式结果。

## 2. 精确对象

- Owner：`liyi / u_9505fd620899`，不是 `liyi111`；
- Task：`workspace_c115f33be1004f51 / revision 1`；
- Run：`pi_run_42daee348b9a45bc`；
- Runtime：`candidate_ready`；
- CandidateSet：1 个 CSV；
- 最新 Attempt：`inconclusive + initial + legacy_unversioned`。

## 3. 已验证事实

1. VerificationReport 不是 `failed`，而是 `inconclusive`。
2. `artifact_set`、`artifact_count`、`source_grounding` 均通过，重新确认了 88 条原件证据；唯一
   未通过的是 `semantic_goal`，原因是语义验证服务未形成可靠结论。
3. 2026-08-16 发生了三组 `candidate_verify` Grant；Provider Usage 均有记录。最后一组两次调用
   的输出均精确达到当时的 2000 token 上限，但没有形成可解析的 SemanticDecision。
4. 2026-08-18 的提交 `ccf6cfb2` 把语义 Judge 上限从 2000 提高到 4000；当前源码注释明确记录
   2000 会使长 reason 偶发截断并导致 INCONCLUSIVE。该修复晚于本任务。
5. 2026-08-25 已追加一条 `candidate_verification_retry_started` 事件，但没有新 Attempt 或新
   Provider Usage。
6. 当前重试路径会先用旧 `request_json` 重建 `PiRuntimeRequest`；该旧请求没有后来强制要求的
   `external_api_confirmed`。只读反馈命令连续两次稳定返回：

   ```text
   VERDICT=RED
   Value error, 外部连接模式必须冻结外发确认
   ```

   因而当前点击重试在 Provider 调用前失败。

## 4. 基于代码与时间线的推断

原始 Candidate 很可能不是因为内容错误而停住，而是旧版 2000 token 上限截断了模型的结构化
语义结论。证据是最后两次验证输出都精确打满 2000，后续代码又针对同一失效模式把上限提升为
4000。旧日志只保留了面向用户的稳定错误，没有保留原始模型响应，因此不能把“截断”提升为
100% 已证明的历史事实。

## 5. 当前产品缺口

这是“历史 `inconclusive` 语义重试兼容”问题，不是 `failed + legacy_unversioned` 再基线：

- 原报告已证明文件、数量和来源门通过，只需重新执行语义门；
- 但旧冻结请求缺少当前外发确认契约，不能直接复用旧授权；
- 现有旧重试入口先追加“开始”事件，再重建请求，失败后留下误导性孤立事件；
- 该入口在通过后仍调用旧的自动发布路径，与当前“验证和发布分离”的稳定边界不一致。

## 6. 推荐处理

另立最小“历史语义重试恢复”纵切片，不扩大 legacy 再基线 V1：

1. 只覆盖 `inconclusive` 且 artifact/source 必要门已经通过的精确 CandidateSet；
2. 通过只读 Offer 展示连接、模型、外发范围和费用，由 Owner 对本次 Provider 外发重新确认；
3. 从冻结 TaskRevision、RuntimeAssignment 和现有 Candidate/Manifest 读取可证明字段；缺失的业务
   语义继续失败关闭，不回填旧 request；
4. 先完成全部契约/CAS 校验，再追加 started 事件和 versioned
   `semantic_inconclusive` Attempt；
5. 只重跑语义门，不重跑 Pi、不修改 Candidate、不创建 revision；
6. 通过后停在等待发布，沿用精确 Attempt 的独立发布动作，不走旧自动发布路径；
7. Provider 未知结果、P0、Owner、幂等和重复费用确认沿用当前安全边界。

## 7. 必须由用户确认

- “历史 inconclusive 语义重试恢复”已由用户确认作为独立纵切片加入当前工作，不扩展
  `legacy_rebaseline` 的业务含义；
- 是否允许 Owner 在重新展示连接、模型、外发范围和费用后，对该旧 Candidate 发起一次新的
  Provider 语义验证；
- 真实调用与费用仍必须在工程实现和验证完成后另行授权，本诊断不授权外发。

配套规格：
[历史 inconclusive Candidate 语义重试恢复规格](2026-08-25-historical-inconclusive-semantic-retry-spec.md)。
