# RSSHub 部署与验证

此镜像固定 RSSHub 版本，并补齐 Patchright 1.61.1 所需的 Chromium 和系统库。
百度路由保留原静态读取；无搜索结果时再用浏览器读取，仍无结果则失败，不自动操作验证码。
补丁校验原文件摘要，升级基础镜像时必须重新适配，不能直接替换摘要绕过核验。
只检查 `/healthz` 无法证明关键词采集可用；必须核验实际路由返回了带内容的 RSS 条目。

## 全新部署

```bash
cd docker/rsshub
cp .env.example .env
chmod 600 .env
docker compose build
docker compose up -d
curl --fail http://127.0.0.1:1200/healthz
```

Mangrove 的 `RSSHUB_BASE_URL` 配为 `http://127.0.0.1:1200`。端口只监听服务器本机。
首次构建需要访问 npm、Debian 软件源和浏览器官方下载源；构建失败不能直接切换现有容器。
Debian 默认源下载较慢时，可使用其镜像站，例如
`docker compose build --build-arg DEBIAN_MIRROR=http://mirrors.ustc.edu.cn`。
镜像站仅用于构建，保留 Debian 包签名校验，安装后恢复镜像内原软件源配置。

已有实例升级前，先保存容器配置、镜像 ID、环境变量和挂载信息。凭据备份须限制访问。
使用独立端口验证新实例，确认通过后再切换；保留停止的旧容器用于回退。
不要直接删除旧容器，也不要用示例 `.env` 覆盖原配置。

## 路由前提

| 关键词来源 | 路由 | 前提与失败处理 |
|---|---|---|
| B站 | `/bilibili/vsearch/Python` | 需要浏览器；上游仍可能要求有效登录或限制服务器访问 |
| 微博 | `/weibo/keyword/Python` | 需要浏览器获取访客凭据；失败时须在本地 `.env` 配置 `WEIBO_COOKIES` |
| 百度 | `/baidu/search/Python` | 依赖公开搜索页；安全验证页面不是搜索结果，不能算采集通过 |
| 什么值得买 | `/smzdm/keyword/Python` | 需要 `.env` 中的 `SMZDM_COOKIE`；未配置时保留路由，状态为待配置 |

Cookie 由管理员在服务器文件中配置，不通过聊天、工单或 Git 传递。
修改 `.env` 后需重建 RSSHub 容器才能生效：`docker compose up -d --force-recreate`。
此命令适用于 Compose 管理的实例。原 `docker run` 实例需按原参数重建，保留旧容器用于回退，
不能直接用 Compose 覆盖同名容器。

## 运行验证

浏览器启动检查：

```bash
docker exec -w /app mangrove-rsshub node --input-type=module -e 'import {chromium} from "patchright"; const browser = await chromium.launch({headless:true,args:["--no-sandbox"]}); const page = await browser.newPage(); await page.setContent("<h1>RSSHUB_BROWSER_OK</h1>"); if (await page.locator("h1").textContent() !== "RSSHUB_BROWSER_OK") throw Error("浏览器读取失败"); await browser.close(); console.log("RSSHUB_BROWSER_OK");'
```

路由检查（可替换为表中的其他路径）：

```bash
curl --fail --max-time 90 http://127.0.0.1:1200/bilibili/vsearch/Python -o /tmp/rsshub-check.xml && \
python3 -c 'import xml.etree.ElementTree as E; items=E.parse("/tmp/rsshub-check.xml").findall("./channel/item"); assert items and all(x.findtext("link") and x.findtext("description") for x in items); print("RSS 条目:", len(items))'
```

浏览器启动、服务存活和路由采集是三项不同检查。空 feed、错误页、验证码页均不计为成功。

百度分支离线回归（正常页面、浏览器承接、仍是验证页时失败并释放资源）：

```bash
docker exec -i -w /app mangrove-rsshub node --experimental-test-module-mocks --input-type=module < test-baidu.mjs
```
