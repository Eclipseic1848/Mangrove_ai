# SearXNG 自托管（search 采集器的免费后端）

开源元搜索引擎，聚合 Bing/百度/DuckDuckGo 等，**免费、无额度限制**，对中文站覆盖好。
用作本项目"站定向检索"的免费后端，替代有月额度的 Tavily。

## 启动

```bash
cd docker/searxng
docker compose up -d
```

首启约需拉取镜像。起来后访问 http://localhost:8080 应能看到搜索页。

## 验证 JSON API（本项目用的就是它）

```bash
curl "http://localhost:8080/search?q=site:dongchedi.com+领克+关闭大灯&format=json"
```

应返回含 `results[].url` 的 JSON。若返回 403/不是 JSON，检查 `config/settings.yml` 里
`search.formats` 是否含 `json`、`server.limiter` 是否为 `false`，改完 `docker compose restart`。

HTTP 200 但 `results` 为空仍不算搜索通过。检查响应的 `unresponsive_engines`；默认搜索源
可能在部署网络下超时。本配置额外启用 360 和搜狗，保留原默认源。可用性仍须用实际查询验证，
不要仅检查首页。已有服务修改配置前先备份，确认挂载的是当前编辑的目录，再只重启 SearXNG。

## 接入本项目

在项目根目录 `.env`：

```
SEARCH_PROVIDER=auto
SEARXNG_BASE_URL=http://localhost:8080
```

`auto` 模式先尝试已配置的 AnySearch；开启 `SEARCH_TAVILY_FIRST` 时再尝试 Tavily，
然后由 SearXNG、DDG 等后端补齐。检查 SearXNG 本身时应直接调用上面的 JSON API，
不能把其他后端兜底的结果计入 SearXNG。
Linux 服务器部署同理，把地址换成服务器可达的 SearXNG 地址即可。
