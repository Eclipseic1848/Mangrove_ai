# MediaCrawler 接入说明

Mangrove 的 `mediacrawler` 采集器通过子进程调用 MediaCrawler 完成社媒（抖音/小红书/微博/B站/快手）数据采集。

> ⚠️ 许可证：MediaCrawler 为**非商业学习用途**。商业化前需替换或采购授权（MediaCrawlerPro / 商业数据源）。

## 安装

优先在 Mangrove 根目录运行固定基线与补丁的准备脚本：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts/setup_external_dependencies.ps1 -Component MediaCrawler
```

准备脚本会校验固定基线与受管 Cookie 同步补丁；只接受摘要匹配的原件或完整已应用状态，未知改动会停止。同步范围为小红书当前隔离子进程内的浏览器 Cookie、HTTP 请求及签名输入；不会自动覆盖平台保存的共享 Cookie，也不会跨任务持久化会话。

随后按上游文档安装独立依赖：

```powershell
Set-Location external/MediaCrawler/repo
# 按其官方文档安装依赖（推荐独立 venv，避免与本项目依赖冲突）
pip install -r requirements.txt
playwright install
```

多数平台需先按 MediaCrawler 文档完成**登录 / cookie 配置**（编辑其 `config/base_config.py`）。

## 接入 Mangrove

在项目根 `.env` 填写 MediaCrawler 仓库路径（含 main.py 的目录）：

```
MEDIACRAWLER_PATH=external/MediaCrawler/repo
# 若用独立 venv，指定其解释器；留空则用当前解释器
MEDIACRAWLER_PYTHON=
```

配好并重启应用后，`mediacrawler` 采集器即可用，且对社媒平台任务（如"抖音 小米SU7 槽点"）
享有最高路由优先级。未配置时自动跳过，不影响其他引擎。

## 调用约定

采集器以如下等价命令运行 MediaCrawler，并读取其 `data/` 下最新 JSON 结果：

```bash
python main.py --platform <dy|xhs|wb|bili|ks> --type search --keywords "<关键词>" --save_data_option json
```

若 MediaCrawler 版本的 CLI 参数有差异，请同步调整
`src/collectors/social_media_collector.py` 中的命令与结果解析逻辑。

## 小红书来源拒绝补丁与已有目录升级

公开安装脚本按会话补丁清单核对两个文件的原始字节，只接受完整前件或当前后件。
本轮新增明确来源拒绝的单次停止与脱敏诊断；HTTP 461/471 等不会通过重试绕过验证。

已安装旧版会话补丁的目录不属于新清单的前件，安装器会拒绝直接覆盖。请在新的隔离
目录按固定上游版本和 Mangrove 补丁重新准备源码，核对清单并验证后受控切换；不要
强制应用、清理或回退旧目录。保留原源码、解释器、配置和备份，生产切换沿用排空流程。
凭据值不写入依赖源码；未知采集结果不因更新补丁而获得重发资格。
