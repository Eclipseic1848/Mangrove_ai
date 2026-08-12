# Firecrawl 自托管

为 Mangrove 的 `firecrawl` 采集器提供"全网发现（/search）+ 整站抓取（/scrape）"能力。

> 许可证：Firecrawl 为 AGPL-3.0。内部自用通常可接受；若未来对外分发/SaaS 化需评估 copyleft 义务。

## 当前部署

先在 Mangrove 根目录准备固定上游基线和本地补丁：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts/setup_external_dependencies.ps1 -Component Firecrawl
```

脚本会生成 `external/firecrawl/`，并应用以下本地化改动：

1. **改用 ghcr 预构建镜像**（免本地编译）：`docker-compose.yaml` 中 api / playwright-service /
   nuq-postgres 三处的 `build:` 注释掉、`image: ghcr.io/firecrawl/...` 打开。
2. **`external/firecrawl/.env`** 最小配置：`PORT=3002`、`USE_DB_AUTHENTICATION=false`、
   `SEARXNG_ENDPOINT=http://host.docker.internal:8080`（/search 复用已运行的 SearXNG）、
   以及 **`ALLOW_LOCAL_WEBHOOKS=true`**（见下方"网络坑"）。

启动（只起必要服务，自动带起 redis/rabbitmq/playwright，跳过实验性的 foundationdb）：

```bash
cd external/firecrawl
docker compose pull api playwright-service nuq-postgres redis rabbitmq
docker compose up -d api nuq-postgres
```

默认 API 监听 `http://localhost:3002`。

## 接入 Mangrove

项目根 `.env` 已配（自托管无需 Key）：

```
FIRECRAWL_BASE_URL=http://localhost:3002
FIRECRAWL_API_KEY=
```

配好后 `firecrawl` 采集器即可用（`is_available()` 通过，tier 20），关键词类任务优先走它做全网发现。
未配置时该采集器自动跳过，不影响其他引擎。

## 网络坑（Clash / 系统代理 fake-ip 环境）

本机若开了 Clash 等 fake-ip 代理，会有两处被坑，均已处理：

- **容器内 SSRF 防护误杀**：fake-ip 把公网域名解析到 `198.18.0.x` 保留段，Firecrawl 的 playwright-service
  判定为"私有 IP"直接拒绝（scrape 返回 statusCode 403、正文为空）。解法：`.env` 设 `ALLOW_LOCAL_WEBHOOKS=true`
  跳过该检查（仅本机内部用安全；对外暴露需重评）。
- **宿主访问 localhost:3002 被代理拦截 502**：httpx(`trust_env=True`) 会读 Windows 系统代理拦截 localhost。
  `firecrawl-py` 不支持注入 httpx 客户端，故 `src/collectors/firecrawl_collector.py` 在建客户端前自动向
  `no_proxy` 追加 `localhost,127.0.0.1`，无需手动设环境变量。

## 验证

```bash
curl -s -X POST http://localhost:3002/v1/scrape -H "Content-Type: application/json" \
  -d '{"url":"https://example.org","formats":["markdown"]}'   # 期望 success:true、statusCode:200、有 markdown
```

或在 Mangrove 里给一个"全网关键词发现"任务，观察报告里的"采集引擎"是否为 firecrawl。
