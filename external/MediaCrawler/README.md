# MediaCrawler 接入说明

Mangrove 的 `mediacrawler` 采集器通过子进程调用 MediaCrawler 完成社媒（抖音/小红书/微博/B站/快手）数据采集。

> ⚠️ 许可证：MediaCrawler 为**非商业学习用途**。商业化前需替换或采购授权（MediaCrawlerPro / 商业数据源）。

## 安装

优先在 Mangrove 根目录运行固定基线与补丁的准备脚本：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts/setup_external_dependencies.ps1 -Component MediaCrawler
```

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
