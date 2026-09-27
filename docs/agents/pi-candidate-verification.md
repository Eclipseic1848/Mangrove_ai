# Pi 独立候选验证

候选使用独立镜像标签，不能覆盖当前镜像或让旧 Run 改用新版本。下载及新增依赖安装须先取得授权；候选验证不自动授权生产切换。

## 离线检查

镜像须已包含 Python 与精确版本 Pi。运行时只挂载临时合成资料，禁用容器网络、缓存预热、遥测和 Provider 自动重试。

```powershell
python scripts/verify_pi_candidate_rpc.py --image <候选镜像> --output <报告.json>
python scripts/verify_pi_candidate_rpc.py --image <候选镜像> --scripted-model --output <工具报告.json>
python scripts/verify_pi_candidate_rpc.py --image <候选镜像> --scripted-model --cancel-running --output <取消报告.json>
python scripts/verify_pi_candidate_rpc.py --image <候选镜像> --scripted-model --unknown-outcome --output <响应丢失报告.json>
python -m pytest tests/test_pi_candidate_rpc.py -q
```

工具场景验证三类现有扩展、工具结果与调用身份、完整输出、凭证文件读取拦截及完成后冷读取。取消场景在收到指定文本片段后发出 abort。响应丢失场景在服务端记录请求后断开连接，要求客户端停止且不重发。冷读取均不得调用模型。

报告记录实际镜像 ID、扩展哈希、RPC 响应、逐项断言及容器清理结果。任一检查失败返回非零退出码。会话与报告含合成资料，仍不应直接发布整份运行日志。

## 真实模型与切换边界

通过既有普通测试账号、冻结账户授权和模型连接版本验证所选模型；必须保留现有中继、短期 Grant、来源哈希与任务网络隔离。独立状态库不能替代生产账户授权，不得关闭授权检查以让探针通过。

初稿停在 `NEEDS_INPUT` 只证明可生成待确认内容，不能记为正式交付。候选测试中的缓存预热和重试设置应在切换前进入正式配置并补回归；不得只验证关闭后的行为，却按新版本默认设置上线。

模型流取消不证明工具副作用已经停止；完成/取消后的冷读取不证明未知工具可以安全重放。工具恢复、完整正式验证、生产部署资格仍需各自证据。

回退保持旧镜像与原 Run 绑定。候选会话不能交给旧版本继续写；不删除旧镜像、运行目录或数据库。
