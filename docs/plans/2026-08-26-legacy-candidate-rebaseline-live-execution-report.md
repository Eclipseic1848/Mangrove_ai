# Legacy Candidate 再基线真实执行报告

> 状态：`LIVE_ACCEPTED`
>
> 执行日期：2026-08-26
>
> 范围：`liyi111` 唯一一次真实 `legacy_rebaseline`；不含正式 Delivery 发布

## 1. 授权与冻结身份

- 用户明确授权 Gate B，只执行一个真实 Qwen CandidateVerification Attempt；
- Owner：`liyi111 / u_439547686101`；
- Task：`workspace_8363695f133645ac`，revision 1；
- Run：`pi_run_c033ae394ae94cf4`；
- CandidateSet：
  `2539e5676ba7ae5963d2dc43acc92cb1672a87477f8f07e283bc0e4dfa98a087`；
- 前序 Attempt：
  `legacy_384796d3b628c58d89108c8a4eab586b752bd845b70f2ccd33a2f34e1f260086`；
- 目标 Ruleset：
  `891b0a5874681f14839d3b322a62f10602486a799db92a688973f811550fa88d`；
- 模型：`Qwen3.6-35B-A3B`；幂等键：
  `cv10-liyi111-legacy-rebaseline-gate-b-20260826-v1`。

## 2. 真实结果

- 唯一新 Attempt：`verification_4b4f150b993422fc41ff2dc58b93a915`；
- 原因：`legacy_rebaseline`；状态：`passed`；
- 授权证据 SHA-256：
  `9f47c8e1b2f5a2b3d2d418ed3b090388d4f18792e9d4b790823dabe75fba0998`；
- 验证报告 SHA-256：
  `4cc2fd39f0f1673ff4e53f5dd3629a37745c3ddb80223bc69f6f780ea7b101a2`；
- `artifact_set`、`artifact_count`、`source_grounding`、`semantic_goal` 四门全部通过；
- 独立 Verifier 从原件重新确认 6 条证据；CSV 与 JSON 字段一致，按 region 升序，金额计算和
  两位小数满足目标，没有补造记录。

## 3. Provider 与不变量

- Provider 只调用 1 次；Usage 状态 `recorded`：input 421 / output 1,869 / total 2,290 tokens；
- Grant `grant_cv_199a86fad1ff53d55acea43d9d337588` 已以
  `candidate_verify_closed` 撤销；活动 Grant 与活动 Attempt 均为 0；
- CSV 与 JSON 的 SHA-256 仍分别为
  `e4e061e8f234616983afd205b64cb8e4ad1b833779d5901544f60d67cfdb6292`、
  `10e6722eff9a43e4d73728eb51b6659faa6c5e953172ff507fbb7ee6fecc5e65`；
- Pi 没有重跑，TaskRevision、Run 和 CandidateSet 没有变化；
- 目标正式 Delivery 与发布意图仍为 0。`passed` 只表示验证通过，不等于正式交付。

## 4. Gate C 正式发布

- 用户单独授权 Gate C 后，精确 passed Attempt 已发布为正式 Delivery
  `delivery_84956666b2f34ed7`，状态 `succeeded`；
- CSV：`output_184a1dd3ece24095`，48 bytes，2 行，SHA-256
  `e4e061e8f234616983afd205b64cb8e4ad1b833779d5901544f60d67cfdb6292`；
- JSON：`output_48e9010d0bf74d82`，93 bytes，SHA-256
  `10e6722eff9a43e4d73728eb51b6659faa6c5e953172ff507fbb7ee6fecc5e65`；
- 两个输出均通过 `non_empty`、`sha256`、`reopened` QA，无警告；
- Delivery provenance 精确绑定 Attempt
  `verification_4b4f150b993422fc41ff2dc58b93a915` 与报告 SHA-256
  `4cc2fd39f0f1673ff4e53f5dd3629a37745c3ddb80223bc69f6f780ea7b101a2`。

TaskOwner 已在查看发布结果摘要后明确回复“同意”，本任务达到 `LIVE_ACCEPTED`。GitHub Issue
完成证据已写入 #70 与父任务 #54；实现提交 `20d3a2a9` 推送到 `main` 后，两张 Issue 均于
2026-08-26 关闭。
